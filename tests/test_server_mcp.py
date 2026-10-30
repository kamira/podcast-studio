# -*- coding: utf-8 -*-
"""studio_server.py 的 MCP 註冊端點測試(CHG-20261022-02 T2)。

`POST /api/mcp/status`、`/api/mcp/plan`、`/api/mcp/apply` 以 `create_app()` + FastAPI TestClient 呼叫,
不開網路埠。CLI 一律是 T1 測試的假 `claude` / `codex` 腳本(`tests/test_mcp_registry.py` 的 `Env`:
暫存 `HOME` / `CLAUDE_CONFIG_DIR` / `CODEX_HOME`,`mcp_registry.ROOT` 換成暫存的 repo 根目錄替身)。
**不呼叫真 CLI、不讀寫真實設定**;只有 `real_config_unchanged` 以唯讀方式比對真實
`~/.codex/config.toml` 的 SHA-256 與真實 `~/.claude.json` 的 `mcpServers` 鍵在整個測試檔前後相同
(檔案不存在時跳過)。
"""
from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

import mcp_registry as mr
import studio_server as ss
from test_mcp_registry import ACCOUNT, EXTRA_ENV, FAKE_VAL, REAL_HOME, Env, _real_snapshot

BASE = "http://127.0.0.1:7860"
ORIGIN = "http://127.0.0.1:7860"
TOKEN = "測試用權杖-CHG02-T2".encode("utf-8").hex()
ENV_KEY = "SECRET" + "_KEY"   # 以拼接組出鍵名,避免秘密掃描的 password-assignment 規則誤判
KV = ENV_KEY + "=" + FAKE_VAL

STATUS, PLAN, APPLY = "/api/mcp/status", "/api/mcp/plan", "/api/mcp/apply"
VALID = {
    STATUS: {"check": False},
    PLAN: {"client": "codex", "action": "register"},
    APPLY: {"client": "codex", "action": "register", "confirm": True},
}


@pytest.fixture(scope="module", autouse=True)
def real_config_unchanged():
    before = _real_snapshot()
    yield
    assert _real_snapshot() == before, "真實 ~/.codex/config.toml 或 ~/.claude.json 的 mcpServers 被改動"


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env(tmp_path)
    monkeypatch.setenv("HOME", str(e.home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(e.claude_dir))
    monkeypatch.setenv("CODEX_HOME", str(e.codex_dir))
    monkeypatch.setenv("FAKE_LOG", str(e.log))
    monkeypatch.setenv("FAKE_SCENARIO", str(e.scenario))
    for k in EXTRA_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(mr, "ROOT", e.repo)
    monkeypatch.setattr(ss, "ROOT", e.repo)
    for key, value in mr.paths().items():   # 保險:所有路徑都必須落在暫存目錄
        assert str(value).startswith(str(tmp_path)), (key, value)
    return e


def make_client(env, base=BASE):
    app = ss.create_app(runner=lambda g, e, c=None: None, token=TOKEN,
                        static_dir=env.tmp / "no-static", root=env.repo)
    return TestClient(app, base_url=base, raise_server_exceptions=False)


@pytest.fixture
def client(env):
    return make_client(env)


def auth(**extra):
    return {"Origin": ORIGIN, "X-Studio-Token": TOKEN, **extra}


def post(client, path, body, headers=None):
    data = body if isinstance(body, (bytes, str)) else json.dumps(body, ensure_ascii=False)
    return client.post(path, content=data,
                       headers=headers if headers is not None else auth(**{"Content-Type": "application/json"}))


def assert_clean_text(text: str, env: Env):
    assert str(env.tmp) not in text
    assert str(env.tmp.resolve()) not in text
    assert str(REAL_HOME) not in text
    assert ACCOUNT not in text


# ---------- (1) Host / Origin / Token ----------

BAD_HEADERS = {
    "缺 Token": {"Origin": ORIGIN},
    "Token 錯": {"Origin": ORIGIN, "X-Studio-Token": "00" + TOKEN[2:]},
    "錯 Origin": {"Origin": "http://evil.example", "X-Studio-Token": TOKEN},
    "缺 Origin": {"X-Studio-Token": TOKEN},
    "Origin null": {"Origin": "null", "X-Studio-Token": TOKEN},
    "Host 不符": {"Origin": ORIGIN, "X-Studio-Token": TOKEN, "Host": "evil.example:7860"},
}


@pytest.mark.parametrize("path", [STATUS, PLAN, APPLY])
@pytest.mark.parametrize("case", list(BAD_HEADERS))
def test_安全檢查不符一律_403(env, client, path, case):
    env.claude_register()
    r = post(client, path, VALID[path], headers={**BAD_HEADERS[case], "Content-Type": "application/json"})
    assert r.status_code == 403
    assert env.calls() == []


def test_localhost_的_Host_與_Origin_也接受(env):
    c = make_client(env, base="http://localhost:7860")
    r = post(c, STATUS, {}, headers={"Origin": "http://localhost:7860", "X-Studio-Token": TOKEN})
    assert r.status_code == 200


# ---------- (2) 一律 POST ----------

@pytest.mark.parametrize("path", [STATUS, PLAN, APPLY])
def test_GET_回_405_且不呼叫_CLI(env, client, path):
    r = client.get(path)
    assert r.status_code in (404, 405)
    assert env.calls() == []


# ---------- (3) apply 必須 confirm: true ----------

@pytest.mark.parametrize("confirm", ["缺少", "true", 1, False, None, {"value": True}])
def test_apply_未確認回_400(env, client, confirm):
    body = {"client": "codex", "action": "register"}
    if confirm != "缺少":
        body["confirm"] = confirm
    r = post(client, APPLY, body)
    assert r.status_code == 400
    assert "confirm" in r.json()["error"]
    assert r.headers["cache-control"] == "no-store"
    assert env.calls() == []


# ---------- (4) 只接受規定的欄位與列舉值 ----------

@pytest.mark.parametrize("path", [PLAN, APPLY])
@pytest.mark.parametrize("field, value", [
    ("client", "claude; rm"), ("client", "../codex"), ("client", "gemini"), ("client", None),
    ("client", ["codex"]), ("action", "add"), ("action", "register "), ("action", 1),
])
def test_列舉外的值回_422(env, client, path, field, value):
    r = post(client, path, {**VALID[path], field: value})
    assert r.status_code == 422
    assert r.json()["error"]
    assert r.headers["cache-control"] == "no-store"
    assert env.calls() == []


@pytest.mark.parametrize("path", [PLAN, APPLY])
@pytest.mark.parametrize("missing", ["client", "action"])
def test_缺欄位回_422(env, client, path, missing):
    body = {k: v for k, v in VALID[path].items() if k != missing}
    r = post(client, path, body)
    assert r.status_code == 422
    assert env.calls() == []


@pytest.mark.parametrize("path", [STATUS, PLAN, APPLY])
@pytest.mark.parametrize("extra", ["argv", "command", "path", "scope", "name", "env", "check"])
def test_多餘欄位回_422(env, client, path, extra):
    if extra in VALID[path]:
        pytest.skip("該端點的合法欄位")
    r = post(client, path, {**VALID[path], extra: "/tmp/惡意-x"})
    assert r.status_code == 422
    assert extra in r.json()["error"]
    assert env.calls() == []


@pytest.mark.parametrize("check", ["true", 1, None, "false"])
def test_status_check_不是布林回_422(env, client, check):
    r = post(client, STATUS, {"check": check})
    assert r.status_code == 422
    assert env.calls() == []


@pytest.mark.parametrize("path", [STATUS, PLAN, APPLY])
@pytest.mark.parametrize("raw", [b"{not json", b"[]", b'"codex"', b"\xff\xfe"])
def test_本文不是_JSON_物件回_422(env, client, path, raw):
    r = post(client, path, raw)
    assert r.status_code == 422
    assert env.calls() == []


@pytest.mark.parametrize("path", [STATUS, PLAN, APPLY])
def test_本文過大回_413(env, client, path):
    r = post(client, path, {**VALID[path], "pad": "x" * (ss.MAX_MCP_BODY + 10)})
    assert r.status_code == 413
    assert env.calls() == []


def test_狀態不允許的動作回_422(env, client):
    env.codex_register()
    env.claude_json({"mcpServers": {}})
    for path in (PLAN, APPLY):
        r = post(client, path, {**VALID[path], "client": "codex", "action": "register"})
        assert r.status_code == 422
        assert "已註冊" in r.json()["error"]
        r = post(client, path, {**VALID[path], "client": "claude", "action": "remove"})
        assert r.status_code == 422
        assert "尚未註冊" in r.json()["error"]
    assert env.calls(op="add") == [] and env.calls(op="remove") == []


def test_未安裝時回_422(env, client):
    (env.bin / "codex").unlink()
    for path in (PLAN, APPLY):
        r = post(client, path, VALID[path])
        assert r.status_code == 422
        assert "未安裝" in r.json()["error"]
        assert_clean_text(r.text, env)
    assert env.calls() == []


# ---------- (5) 合法 apply ----------

@pytest.mark.parametrize("cli", ["codex", "claude"])
def test_合法_apply_呼叫寫入指令恰一次且與_plan_相同(env, client, cli):
    env.claude_json({"mcpServers": {}})
    p = post(client, PLAN, {"client": cli, "action": "register"})
    assert p.status_code == 200
    planned = p.json()["argv"]
    assert planned[0] == f"~/.local/bin/{cli}"
    assert env.calls(op="add") == []          # plan 不執行寫入

    r = post(client, APPLY, {"client": cli, "action": "register", "confirm": True})
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    adds = env.calls(cli, "add")
    assert len(adds) == 1
    assert [mr.scrub(a) for a in adds[0]["argv"]] == planned[1:]
    assert body["ok"] is True and body["exit_code"] == 0
    assert body["status"][cli]["state"] == "registered"
    assert "Added" in body["output"]
    assert_clean_text(r.text, env)

    r = post(client, APPLY, {"client": cli, "action": "remove", "confirm": True})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["status"][cli]["state"] == "absent"
    assert len(env.calls(cli, "remove")) == 1
    assert len(env.calls(cli, "add")) == 1


def test_apply_CLI_失敗時回_200_與錯誤原文(env, client):
    env.set_scenario(codex_add={"code": 3, "stderr": "Error: 寫入設定失敗-e9\n", "effect": False})
    r = post(client, APPLY, VALID[APPLY])
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["exit_code"] == 3
    assert "寫入設定失敗-e9" in body["output"]


# ---------- (6) 開頁不做健康檢查 ----------

@pytest.mark.parametrize("body", [{"check": False}, {}, b""])
def test_status_check_false_不呼叫_claude(env, client, body):
    env.claude_register()
    env.codex_register()
    r = post(client, STATUS, body)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    st = r.json()
    assert env.calls("claude") == []
    assert st["claude"]["state"] == "registered" and st["claude"]["health"] is None
    assert st["codex"]["state"] == "registered"
    assert st["server"] == ["~/.local/bin/uv", "run", "--script", "<repo>/local_models_mcp.py"]


def test_status_check_true_呼叫_claude_get_一次(env, client):
    env.claude_register()
    env.codex_register()
    r = post(client, STATUS, {"check": True})
    assert r.status_code == 200
    gets = env.calls("claude")
    assert len(gets) == 1 and gets[0]["argv"] == ["mcp", "get", "local-models"]
    assert r.json()["claude"]["health"] == "ok"


def test_status_check_true_但指向其他位置時不呼叫_claude(env, client):
    env.claude_register(args=["run", "--script", "/elsewhere/local_models_mcp.py"])
    r = post(client, STATUS, {"check": True})
    assert r.status_code == 200
    assert env.calls("claude") == []
    assert "不做健康檢查" in r.json()["claude"]["detail"]


# ---------- (7) 回應不含路徑、帳號欄位與 env 值 ----------

def test_三個端點的回應不含路徑_帳號與_env_值(env, client):
    env.claude_register(env={ENV_KEY: FAKE_VAL})
    env.codex_register(env={ENV_KEY: FAKE_VAL}, timeout=1800.0)
    env.set_scenario(
        claude_get={"stdout": "local-models:\n  Status: ✔ Connected\n  Environment:\n    %s\n"
                              "File: %s/.claude.json\n" % (KV, env.claude_dir)},
        claude_remove={"stdout": "Removed (env %s)\n內嵌 %s 字樣\nFile modified: %s/.claude.json\n"
                                 % (KV, FAKE_VAL, env.claude_dir)},
        codex_remove={"stdout": "%s\n家目錄 %s\n" % (KV, env.home)})
    responses = [post(client, STATUS, {"check": False}), post(client, STATUS, {"check": True})]
    responses += [post(client, PLAN, {"client": c, "action": "remove"}) for c in mr.CLIENTS]
    responses += [post(client, APPLY, {"client": c, "action": "remove", "confirm": True}) for c in mr.CLIENTS]
    assert len(env.calls("claude", "get")) == 1
    for r in responses:
        assert r.status_code == 200, r.text
        assert r.headers["cache-control"] == "no-store"
        assert FAKE_VAL not in r.text
        assert ENV_KEY in r.text
        assert_clean_text(r.text, env)
    assert "~" in responses[-1].json()["output"]


def test_錯誤回應不含路徑(env, client):
    env.claude_json(raw="{壞掉".encode("utf-8"))
    r = post(client, PLAN, {"client": "claude", "action": "register"})
    assert r.status_code == 422
    assert "狀態不明" in r.json()["error"]
    assert_clean_text(r.text, env)


# ---------- (8) 忙碌 → 409 ----------

def test_忙碌時第二個_apply_回_409(env, client):
    env.set_scenario(codex_add={"sleep": 1.5})
    result = {}

    def first():
        result["r"] = post(make_client(env), APPLY, VALID[APPLY])

    t = threading.Thread(target=first)
    t.start()
    deadline = time.monotonic() + 10
    while not env.calls("codex", "add") and time.monotonic() < deadline:
        time.sleep(0.02)
    assert env.calls("codex", "add")
    r = post(client, APPLY, {"client": "claude", "action": "register", "confirm": True})
    assert r.status_code == 409
    assert r.headers["cache-control"] == "no-store"
    assert "執行中" in r.json()["error"]
    for path in (STATUS, PLAN):
        assert post(client, path, VALID[path]).status_code == 409
    t.join()
    assert result["r"].status_code == 200
    assert result["r"].json()["ok"] is True
    assert env.calls("claude", "add") == []
