# -*- coding: utf-8 -*-
"""mcp_registry.py 的測試(CHG-20261022-02 T1)。

一律以暫存目錄內的假 `claude` / `codex` 腳本(記錄收到的 argv、cwd 與環境變數到 FAKE_LOG,依
FAKE_SCENARIO 回固定輸出與結束碼)與暫存 `HOME` / `CLAUDE_CONFIG_DIR` / `CODEX_HOME` 執行;
`mcp_registry.ROOT` 換成暫存的 repo 根目錄替身。**不呼叫真 CLI、不讀寫真實設定**;只有
`real_config_unchanged` 以唯讀方式比對真實 `~/.codex/config.toml` 的 SHA-256 與真實
`~/.claude.json` 的 `mcpServers` 鍵在整個測試檔前後相同(檔案不存在時跳過)。
"""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import mcp_registry as mr

REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
ENV_KEY = "SECRET" + "_KEY"   # 以拼接組出鍵名,避免秘密掃描的 password-assignment 規則誤判
FAKE_VAL = "秘密值-7f3a"
LONG_VAL = "秘密值-7f3a-長字串"
ACCOUNT = "假帳號-acct-5b1d@example.com"
OTHER_MARK = "其他專案-9c2e"
EXTRA_ENV = {"UV_OFFLINE": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_TELEMETRY": "1"}
CJK = re.compile(r"[一-鿿]")

FAKE_CLI = r'''#!__PYTHON__
# -*- coding: utf-8 -*-
"""假 claude / codex(依檔名決定扮演哪一個);只讀寫 CLAUDE_CONFIG_DIR / CODEX_HOME 底下的檔案。"""
import json, os, sys, time
from pathlib import Path

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
WATCH = ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "UV_OFFLINE",
         "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "DISABLE_TELEMETRY")
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"tool": tool, "argv": args, "cwd": os.getcwd(),
                        "env": {k: os.environ.get(k) for k in WATCH}}, ensure_ascii=False) + "\n")
sc_path = Path(os.environ["FAKE_SCENARIO"])
sc = json.loads(sc_path.read_text(encoding="utf-8")) if sc_path.exists() else {}
op = args[1] if len(args) > 1 else ""
spec = sc.get(tool + "_" + op, {})
if spec.get("sleep"):
    time.sleep(spec["sleep"])
NAME = "local-models"


def finish(code, stdout="", stderr=""):
    if "code" in spec:
        code = spec["code"]
    stdout = spec.get("stdout", stdout)
    stderr = spec.get("stderr", stderr)
    sys.stdout.write(stdout)
    sys.stderr.write(stderr)
    sys.exit(code)


effect = spec.get("effect", True)
if tool == "claude":
    cfg = Path(os.environ.get("CLAUDE_CONFIG_DIR") or os.environ["HOME"]) / ".claude.json"
    data = json.loads(cfg.read_text(encoding="utf-8")) if cfg.exists() else {}
    servers = data.get("mcpServers") or {}
    if op == "add":
        cmd = args[args.index("--") + 1:]
        if NAME in servers:
            finish(1, stderr="MCP server local-models already exists in user config\n")
        if effect:
            servers[NAME] = {"type": "stdio", "command": cmd[0], "args": cmd[1:], "env": {}}
            data["mcpServers"] = servers
            cfg.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        finish(0, stdout="Added stdio MCP server local-models with command: %s to user config\n"
                          "File modified: %s\n" % (" ".join(cmd), cfg))
    if op == "remove":
        if NAME not in servers:
            finish(1, stderr='No MCP server named "local-models" in user scope\n')
        if effect:
            del servers[NAME]
            cfg.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        finish(0, stdout="Removed MCP server local-models from user config\nFile modified: %s\n" % cfg)
    if op == "get":
        if NAME not in servers:
            finish(1, stderr='No MCP server named "local-models". Run `claude mcp add` to add one.\n')
        e = servers[NAME]
        env_lines = "".join("    %s=%s\n" % kv for kv in (e.get("env") or {}).items())
        finish(0, stdout="local-models:\n  Scope: User config (available in all your projects)\n"
                         "  Status: ✔ Connected\n  Type: stdio\n  Command: %s\n  Args: %s\n"
                         "  Environment:\n%s\nTo remove this server, run: claude mcp remove local-models -s user\n"
                         % (e.get("command"), " ".join(e.get("args") or []), env_lines))
    finish(2, stderr="假 claude:不認得的指令\n")

state_path = Path(os.environ.get("CODEX_HOME") or os.path.join(os.environ["HOME"], ".codex")) / "fake_state.json"
state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
if op == "add":
    cmd = args[args.index("--") + 1:]
    if effect:
        state[NAME] = {"command": cmd[0], "args": cmd[1:], "env": None, "tool_timeout_sec": None}
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    finish(0, stdout="Added global MCP server 'local-models'.\n")
if op == "remove":
    if NAME not in state:
        finish(0, stdout="No MCP server named 'local-models' found.\n")
    if effect:
        del state[NAME]
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    finish(0, stdout="Removed global MCP server 'local-models'.\n")
if op == "get":
    if NAME not in state:
        finish(1, stderr="Error: No MCP server named 'local-models' found.\n")
    e = state[NAME]
    finish(0, stdout=json.dumps({
        "name": NAME, "enabled": True, "disabled_reason": None,
        "transport": {"type": "stdio", "command": e["command"], "args": e["args"], "env": e["env"],
                      "env_vars": [], "cwd": None},
        "enabled_tools": None, "disabled_tools": None, "startup_timeout_sec": None,
        "tool_timeout_sec": e["tool_timeout_sec"]}, ensure_ascii=False) + "\n")
finish(2, stderr="假 codex:不認得的指令\n")
'''


# ---------- 真實設定唯讀比對(test 9) ----------

def _real_snapshot():
    snap = {}
    toml = REAL_HOME / ".codex" / "config.toml"
    snap["codex"] = hashlib.sha256(toml.read_bytes()).hexdigest() if toml.is_file() else None
    cj = REAL_HOME / ".claude.json"
    snap["claude"] = None
    if cj.is_file():
        for _ in range(10):   # Claude Code 執行中會頻繁改寫該檔;讀到寫一半時重試
            try:
                snap["claude"] = json.dumps(json.loads(cj.read_text(encoding="utf-8")).get("mcpServers"),
                                            sort_keys=True, ensure_ascii=False)
                break
            except (ValueError, OSError):
                time.sleep(0.2)
        else:
            snap["claude"] = "無法讀取"
    return snap


@pytest.fixture(scope="module", autouse=True)
def real_config_unchanged():
    before = _real_snapshot()
    yield
    assert _real_snapshot() == before, "真實 ~/.codex/config.toml 或 ~/.claude.json 的 mcpServers 被改動"


# ---------- 暫存環境 ----------

class Env:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.home = tmp / "home"
        self.bin = self.home / ".local" / "bin"
        self.claude_dir = tmp / "claude"
        self.codex_dir = tmp / "codex"
        self.repo = tmp / "repo"
        self.log = tmp / "fake_log.jsonl"
        self.scenario = tmp / "scenario.json"
        for d in (self.bin, self.claude_dir, self.codex_dir, self.repo):
            d.mkdir(parents=True)
        script = FAKE_CLI.replace("__PYTHON__", sys.executable)
        for tool in ("claude", "codex"):
            p = self.bin / tool
            p.write_text(script, encoding="utf-8")
            p.chmod(0o755)
        (self.bin / "uv").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (self.bin / "uv").chmod(0o755)
        self.uv = str(self.bin / "uv")
        self.script = str(self.repo / "local_models_mcp.py")
        self.good_args = ["run", "--script", self.script]

    # 假 CLI 的情境與紀錄
    def set_scenario(self, **kw):
        self.scenario.write_text(json.dumps(kw, ensure_ascii=False), encoding="utf-8")

    def calls(self, tool=None, op=None):
        if not self.log.exists():
            return []
        rows = [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines() if x.strip()]
        return [r for r in rows if (tool is None or r["tool"] == tool)
                and (op is None or (len(r["argv"]) > 1 and r["argv"][1] == op))]

    # 暫存設定
    def claude_json(self, obj=None, raw=None):
        p = self.claude_dir / ".claude.json"
        if raw is not None:
            p.write_bytes(raw)
        else:
            p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    def claude_register(self, command=None, args=None, env=None, extra=None):
        obj = {"oauthAccount": {"emailAddress": ACCOUNT}, "numStartups": 3,
               "mcpServers": {"local-models": {"type": "stdio", "command": command or self.uv,
                                               "args": self.good_args if args is None else args,
                                               "env": env or {}}}}
        obj.update(extra or {})
        self.claude_json(obj)

    def claude_settings(self, env):
        (self.claude_dir / "settings.json").write_text(json.dumps({"env": env}), encoding="utf-8")

    def codex_register(self, command=None, args=None, env=None, timeout=None):
        st = {"local-models": {"command": command or self.uv,
                               "args": self.good_args if args is None else args,
                               "env": env, "tool_timeout_sec": timeout}}
        (self.codex_dir / "fake_state.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")

    def codex_state(self):
        p = self.codex_dir / "fake_state.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


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
    # 保險:所有路徑都必須落在暫存目錄
    for key, value in mr.paths().items():
        assert str(value).startswith(str(tmp_path)), (key, value)
    return e


def dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def assert_scrubbed(obj, e: Env):
    text = dump(obj)
    assert str(e.tmp) not in text
    assert str(REAL_HOME) not in text


def assert_no_secret(obj, secret=FAKE_VAL):
    text = dump(obj)
    assert secret not in text
    assert "SECRET_KEY" in text


# ---------- 介面常數與路徑 ----------

def test_常數與_server_argv(env):
    assert mr.NAME == "local-models"
    assert mr.CLIENTS == ("claude", "codex")
    assert mr.ACTIONS == ("register", "remove")
    assert mr.server_argv() == [env.uv, "run", "--script", env.script]
    p = mr.paths()
    assert set(p) == {"claude_cli", "codex_cli", "uv", "claude_json", "claude_settings", "repo_mcp_json", "codex_home"}
    assert p["claude_json"] == env.claude_dir / ".claude.json"
    assert p["claude_settings"] == env.claude_dir / "settings.json"
    assert p["repo_mcp_json"] == env.repo / ".mcp.json"
    assert p["codex_home"] == env.codex_dir


def test_沒設定目錄變數時用家目錄(env, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    monkeypatch.delenv("CODEX_HOME")
    p = mr.paths()
    assert p["claude_json"] == env.home / ".claude.json"
    assert p["claude_settings"] == env.home / ".claude" / "settings.json"
    assert p["codex_home"] == env.home / ".codex"


# ---------- (1) 開頁不啟動健康檢查;D17 ----------

def test_check_false_不呼叫_claude(env):
    env.claude_register()
    env.codex_register()
    st = mr.status(check=False)
    assert env.calls("claude") == []
    assert st["claude"]["state"] == "registered"
    assert st["claude"]["installed"] is True
    assert st["claude"]["health"] is None
    assert st["codex"]["state"] == "registered"
    assert st["server"] == ["~/.local/bin/uv", "run", "--script", "<repo>/local_models_mcp.py"]
    assert_scrubbed(st, env)


def test_check_true_registered_呼叫_claude_get_一次(env):
    env.claude_register()
    env.codex_register()
    st = mr.status(check=True)
    gets = env.calls("claude")
    assert len(gets) == 1
    assert gets[0]["argv"] == ["mcp", "get", "local-models"]
    assert Path(gets[0]["cwd"]).resolve() == env.repo.resolve()
    assert st["claude"]["health"] == "ok"
    assert_scrubbed(st, env)


@pytest.mark.parametrize("case", ["other", "unknown", "absent", "local_scope", "project_scope", "mcp_json_broken"])
def test_check_true_非本_repo_或被遮蔽時不做健康檢查(env, case):
    if case == "other":
        env.claude_register(args=["run", "--script", "/elsewhere/local_models_mcp.py"])
    elif case == "unknown":
        env.claude_json(raw=b"{not json")
    elif case == "absent":
        env.claude_json({"mcpServers": {}})
    elif case == "local_scope":
        env.claude_register(extra={"projects": {str(env.repo): {"mcpServers": {"local-models": {"command": "x"}}}}})
    elif case == "project_scope":
        env.claude_register()
        (env.repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"local-models": {"command": "x"}}}),
                                            encoding="utf-8")
    else:
        env.claude_register()
        (env.repo / ".mcp.json").write_text("{壞掉", encoding="utf-8")
    st = mr.status(check=True)
    assert env.calls("claude") == []
    assert st["claude"]["health"] is None
    assert "不做健康檢查" in st["claude"]["detail"]
    if case in ("local_scope", "project_scope"):
        assert st["claude"]["shadowed"] is True
        assert "local / project scope" in st["claude"]["detail"]


# ---------- (2) Claude 狀態來自 .claude.json ----------

@pytest.mark.parametrize("case, expected", [
    ("registered", "registered"), ("other", "other"), ("no_key", "absent"), ("no_file", "absent"),
    ("broken", "unknown"), ("not_object", "unknown"), ("too_big", "unknown"),
    ("servers_not_object", "unknown"), ("http_type", "other"), ("args_not_list", "unknown"),
])
def test_claude_狀態判斷(env, case, expected):
    if case == "registered":
        env.claude_register()
    elif case == "other":
        env.claude_register(command="/opt/other/uv")
    elif case == "no_key":
        env.claude_json({"oauthAccount": {"emailAddress": ACCOUNT}, "mcpServers": {"another": {"command": "x"}}})
    elif case == "no_file":
        pass
    elif case == "broken":
        env.claude_json(raw=b'{"mcpServers": {')
    elif case == "not_object":
        env.claude_json([1, 2, 3])
    elif case == "too_big":
        env.claude_register()
        with open(env.claude_dir / ".claude.json", "r+b") as f:
            f.truncate(16 * 1024 * 1024 + 1)
    elif case == "servers_not_object":
        env.claude_json({"mcpServers": ["local-models"]})
    elif case == "http_type":
        env.claude_json({"mcpServers": {"local-models": {"type": "http", "url": "http://127.0.0.1:1/"}}})
    else:
        env.claude_register(args="run --script x")
    st = mr.status(check=False)
    assert st["claude"]["state"] == expected
    if expected == "unknown":
        assert st["claude"]["detail"]
    assert_scrubbed(st, env)


def test_claude_json_其他內容不外流(env):
    other_repo = "/Users/someone/" + OTHER_MARK + "-repo"
    env.claude_register(env={"SECRET_KEY": FAKE_VAL}, extra={
        "projects": {other_repo: {"mcpServers": {"local-models": {"command": OTHER_MARK}},
                                  "history": [OTHER_MARK]}},
        "userID": OTHER_MARK + "-user"})
    env.codex_register(timeout=1800.0)
    results = [mr.status(check=False), mr.status(check=True), mr.plan("claude", "remove"), mr.plan("codex", "remove")]
    for r in results:
        text = dump(r)
        assert ACCOUNT not in text
        assert OTHER_MARK not in text
        assert other_repo not in text
        assert "oauthAccount" not in text
        assert_scrubbed(r, env)
    assert results[0]["claude"]["env_keys"] == ["SECRET_KEY"]


def test_claude_逾時設定只讀(env):
    env.claude_register()
    st = mr.status(check=False)
    assert st["claude"]["timeout"]["value"] is None
    env.claude_settings({"MCP_TOOL_TIMEOUT": "1800000"})
    before = (env.claude_dir / "settings.json").read_bytes()
    st = mr.status(check=False)
    assert st["claude"]["timeout"]["value"] == "1800000"
    assert (env.claude_dir / "settings.json").read_bytes() == before


# ---------- (3) Codex 狀態來自 codex mcp get --json ----------

def _codex_json(command, args, env=None, timeout=None):
    return json.dumps({"name": "local-models", "transport": {"type": "stdio", "command": command, "args": args,
                                                              "env": env}, "tool_timeout_sec": timeout})


@pytest.mark.parametrize("case, expected", [
    ("registered", "registered"), ("other", "other"), ("absent", "absent"),
    ("not_json", "unknown"), ("other_error", "unknown"), ("exit1_other_text", "unknown"),
])
def test_codex_狀態判斷(env, case, expected):
    if case == "registered":
        env.codex_register()
    elif case == "other":
        env.codex_register(args=["run", "--script", "/elsewhere/local_models_mcp.py"])
    elif case == "absent":
        pass
    elif case == "not_json":
        env.set_scenario(codex_get={"code": 0, "stdout": "local-models  enabled  uv\n"})
    elif case == "other_error":
        env.set_scenario(codex_get={"code": 2, "stderr": "Error: config.toml 壞掉\n"})
    else:
        env.set_scenario(codex_get={"code": 1, "stderr": "Error: permission denied\n"})
    st = mr.status(check=False)
    assert st["codex"]["state"] == expected
    calls = env.calls("codex", "get")
    assert calls and calls[0]["argv"] == ["mcp", "get", "local-models", "--json"]
    if expected == "unknown":
        assert st["codex"]["detail"]
    assert_scrubbed(st, env)


def test_codex_tool_timeout_sec(env):
    env.codex_register(timeout=1800.0)
    t = mr.status(check=False)["codex"]["timeout"]
    assert t["value"] == 1800
    assert "tool_timeout_sec = 1800" in t["snippet"]
    assert "[mcp_servers.local-models]" in t["snippet"]
    env.codex_register(timeout=None)
    t = mr.status(check=False)["codex"]["timeout"]
    assert t["value"] is None
    assert "tool_timeout_sec = 1800" in t["snippet"]


# ---------- (4) plan 的指令組裝 ----------

def test_plan_codex_register(env):
    p = mr.plan("codex", "register")
    assert p["argv"] == ["~/.local/bin/codex", "mcp", "add", "local-models", "--",
                         "~/.local/bin/uv", "run", "--script", "<repo>/local_models_mcp.py"]
    # CODEX_HOME 不在家目錄之下時以 `$CODEX_HOME` 顯示,不送暫存目錄的絕對路徑
    assert p["files"] == ["$CODEX_HOME/config.toml"]
    assert p["current"] is None
    assert any("tool_timeout_sec" in w for w in p["warnings"])
    assert_scrubbed(p, env)


def test_plan_claude_register_與_remove(env):
    p = mr.plan("claude", "register")
    assert p["argv"] == ["~/.local/bin/claude", "mcp", "add", "-s", "user", "local-models", "--",
                         "~/.local/bin/uv", "run", "--script", "<repo>/local_models_mcp.py"]
    assert p["files"] == ["$CLAUDE_CONFIG_DIR/.claude.json"]
    assert any("MCP_TOOL_TIMEOUT" in w for w in p["warnings"])
    env.claude_register()
    p = mr.plan("claude", "remove")
    assert p["argv"] == ["~/.local/bin/claude", "mcp", "remove", "local-models", "-s", "user"]
    assert p["current"]["command"] == "~/.local/bin/uv"
    assert p["current"]["args"] == ["run", "--script", "<repo>/local_models_mcp.py"]
    assert_scrubbed(p, env)


def test_plan_codex_remove_逾時提醒(env):
    env.codex_register(timeout=1800.0)
    p = mr.plan("codex", "remove")
    assert p["argv"] == ["~/.local/bin/codex", "mcp", "remove", "local-models"]
    text = "\n".join(p["warnings"])
    assert "tool_timeout_sec = 1800" in text
    assert "會一起消失" in text and "不會帶回" in text
    assert "[mcp_servers.local-models]" in text
    assert p["current"]["timeout"]["value"] == 1800
    env.codex_register(timeout=None)
    p = mr.plan("codex", "remove")
    assert not any("會一起消失" in w and "tool_timeout_sec" in w for w in p["warnings"])


def test_plan_不呼叫寫入指令(env):
    mr.plan("codex", "register")
    mr.plan("claude", "register")
    assert env.calls("codex", "add") == [] and env.calls("claude") == []


# ---------- (5) 不合法的 client / action / 狀態 ----------

@pytest.mark.parametrize("client, action", [("gemini", "register"), ("claude; rm", "remove"),
                                            ("codex", "add"), ("../codex", "remove"), ("claude", None)])
def test_列舉外的值丟_ValueError(env, client, action):
    with pytest.raises(ValueError) as ei:
        mr.plan(client, action)
    assert CJK.search(str(ei.value))
    with pytest.raises(ValueError):
        mr.apply(client, action)
    assert env.calls() == []


def test_狀態不允許的動作丟_ValueError(env):
    env.claude_register()
    env.codex_register()
    for client in mr.CLIENTS:
        with pytest.raises(ValueError) as ei:
            mr.plan(client, "register")
        assert CJK.search(str(ei.value))
        with pytest.raises(ValueError):
            mr.apply(client, "register")
    env.claude_json({"mcpServers": {}})
    (env.codex_dir / "fake_state.json").unlink()
    for client in mr.CLIENTS:
        with pytest.raises(ValueError):
            mr.apply(client, "remove")
    assert env.calls(op="add") == [] and env.calls(op="remove") == []


def test_unknown_或未安裝時兩個動作都丟_ValueError(env):
    env.claude_json(raw=b"{")
    env.set_scenario(codex_get={"code": 0, "stdout": "不是 JSON"})
    for client in mr.CLIENTS:
        for action in mr.ACTIONS:
            with pytest.raises(ValueError) as ei:
                mr.plan(client, action)
            assert CJK.search(str(ei.value))
    (env.bin / "claude").unlink()
    (env.bin / "codex").unlink()
    env.claude_json({"mcpServers": {}})
    st = mr.status(check=True)
    assert st["claude"]["installed"] is False and st["codex"]["installed"] is False
    for client in mr.CLIENTS:
        for action in mr.ACTIONS:
            with pytest.raises(ValueError) as ei:
                mr.apply(client, action)
            assert "未安裝" in str(ei.value)


def test_指向其他位置時只能移除(env):
    env.claude_register(command="/opt/other/uv")
    with pytest.raises(ValueError):
        mr.plan("claude", "register")
    assert mr.plan("claude", "remove")["argv"] == ["~/.local/bin/claude", "mcp", "remove", "local-models", "-s", "user"]
    env.codex_register(command="/opt/other/uv")
    with pytest.raises(ValueError):
        mr.plan("codex", "register")
    assert mr.plan("codex", "remove")["current"]["command"] == "/opt/other/uv"


def test_設定目錄未設時檔案以家目錄顯示(env, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    monkeypatch.delenv("CODEX_HOME")
    assert mr.plan("claude", "register")["files"] == ["~/.claude.json"]
    assert mr.plan("codex", "register")["files"] == ["~/.codex/config.toml"]


# ---------- (6) 子程序參數、逾時、截斷、scrub、去除 env 值 ----------

def test_子程序參數(env, monkeypatch):
    seen = []

    def fake_run(*a, **k):
        seen.append((a, k))
        return subprocess.CompletedProcess(a[0], 1, b"", b"Error: No MCP server named 'local-models' found.\n")

    monkeypatch.setattr(mr.subprocess, "run", fake_run)
    mr.apply("codex", "register")
    assert seen
    for a, k in seen:
        argv = a[0] if a else k["args"]
        assert isinstance(argv, list) and all(isinstance(x, str) for x in argv)
        assert k.get("shell", False) is False
        assert k["stdin"] is subprocess.DEVNULL
        assert k["timeout"] == 30
        assert Path(k["cwd"]).resolve() == env.repo.resolve()
        for name, value in EXTRA_ENV.items():
            assert k["env"][name] == value


def test_逾時回_CLI_執行逾時(env, monkeypatch):
    real_run = subprocess.run

    def fake_run(argv, *a, **k):
        if argv[1:3] == ["mcp", "add"]:
            raise subprocess.TimeoutExpired(argv, k.get("timeout"))
        return real_run(argv, *a, **k)

    monkeypatch.setattr(mr.subprocess, "run", fake_run)
    r = mr.apply("codex", "register")
    assert r["ok"] is False
    assert "CLI 執行逾時" in r["output"]
    assert r["exit_code"] is None


def test_輸出超過_64KB_截斷(env):
    env.set_scenario(codex_add={"stdout": "甲" * 100_000})
    r = mr.apply("codex", "register")
    assert len(r["output"].encode("utf-8")) <= 64 * 1024
    assert "截斷" in r["output"]


def test_回傳字串經_scrub(env):
    env.set_scenario(claude_add={"stdout": "File modified: %s/.claude.json\n家目錄 %s\n" % (env.claude_dir, env.home)})
    r = mr.apply("claude", "register")
    assert "~" in r["output"]
    assert_scrubbed(r, env)


def test_env_值不出現在任何回傳字串(env):
    env.claude_register(env={"SECRET_KEY": FAKE_VAL})
    env.codex_register(env={"SECRET_KEY": FAKE_VAL}, timeout=1800.0)
    env.set_scenario(
        claude_remove={"stdout": "Removed (env SECRET_KEY=%s)\n內嵌 %s 字樣\n" % (FAKE_VAL, FAKE_VAL)},
        codex_remove={"stdout": "SECRET_KEY=%s\n" % FAKE_VAL, "effect": True})
    st = mr.status(check=True)
    assert_no_secret(st)
    for client in mr.CLIENTS:
        assert_no_secret(mr.plan(client, "remove"))
    for client in mr.CLIENTS:
        r = mr.apply(client, "remove")
        assert FAKE_VAL not in dump(r)
        assert "SECRET_KEY" in r["output"]


def test_claude_get_的_Environment_段去除值(env):
    env.claude_register(env={"SECRET_KEY": FAKE_VAL})
    env.set_scenario(claude_get={"stdout": "local-models:\n  Status: ✔ Connected\n  Environment:\n"
                                           "    SECRET_KEY=%s\n    OTHER: 未知值-aa11\n\n結尾\n" % FAKE_VAL})
    st = mr.status(check=True)
    text = dump(st)
    assert FAKE_VAL not in text
    assert "未知值-aa11" not in text


@pytest.mark.parametrize("form", ["line", "known"])
def test_秘密值跨截斷邊界時前綴不外露(env, form):
    # 讓秘密值的前 12 位元組(「秘密值-」與其後兩字)落在截斷點之前、其餘在截斷點之後:
    # 若先截斷再去除,前綴就會外露
    keep = 64 * 1024 - len(mr.TRUNC_MARK.encode("utf-8"))
    head = 12
    assert len(LONG_VAL[:6].encode("utf-8")) == head
    if form == "line":
        out = "x" * (keep - head - len("\nSECRET_KEY=")) + "\nSECRET_KEY=" + LONG_VAL + "\n" + "y" * 200
        assert out.encode("utf-8")[:keep].endswith(("SECRET_KEY=" + LONG_VAL[:6]).encode("utf-8"))
    else:
        env.codex_register(env={"SECRET_KEY": LONG_VAL})
        out = "x" * (keep - head - 1) + " " + LONG_VAL + " 結尾\n" + "y" * 200
        assert out.encode("utf-8")[:keep].endswith((" " + LONG_VAL[:6]).encode("utf-8"))
        env.set_scenario(codex_remove={"stdout": out})
        r = mr.apply("codex", "remove")
        _assert_no_prefix(r)
        return
    env.set_scenario(codex_add={"stdout": out})
    r = mr.apply("codex", "register")
    _assert_no_prefix(r)


def _assert_no_prefix(r):
    assert r["output"].endswith(mr.TRUNC_MARK)   # 確實發生截斷
    text = dump(r)
    for n in range(4, len(LONG_VAL) + 1):
        assert LONG_VAL[:n] not in text, LONG_VAL[:n]


def test_redact_env_規則():
    text = "a\n" + ENV_KEY + "=abc123\n  TOKEN=zz\nEnvironment:\n    K: v1\n    J=v2\n\n內嵌 known-99 值\n"
    out = mr.redact_env(text, ["known-99"])
    for s in ("abc123", "zz", "v1", "v2", "known-99"):
        assert s not in out
    for k in ("SECRET_KEY", "TOKEN", "K", "J"):
        assert k in out
    js = json.dumps({"transport": {"env": {"A": "val-1"}}})
    out = mr.redact_env(js, [])
    assert "val-1" not in out and '"A"' in out


# ---------- (7) 同時操作 ----------

def test_同時_apply_第二個丟_Busy(env):
    env.set_scenario(codex_add={"sleep": 1.5})
    result = {}

    def first():
        result["r"] = mr.apply("codex", "register")

    t = threading.Thread(target=first)
    t.start()
    deadline = time.monotonic() + 10
    while not env.calls("codex", "add") and time.monotonic() < deadline:
        time.sleep(0.02)
    assert env.calls("codex", "add")
    with pytest.raises(mr.Busy):
        mr.apply("claude", "register")
    with pytest.raises(mr.Busy):
        mr.status(check=False)
    t.join()
    assert result["r"]["ok"] is True
    assert len(env.calls("claude", "add")) == 0


# ---------- (8) 環境變數 ----------

@pytest.mark.parametrize("server_env", [None, "0"])
def test_每次子程序都帶三個變數(env, monkeypatch, server_env):
    if server_env is not None:
        for k in EXTRA_ENV:
            monkeypatch.setenv(k, server_env)
    env.claude_register()
    env.codex_register()
    mr.status(check=True)
    mr.apply("codex", "remove")
    mr.apply("codex", "register")
    mr.apply("claude", "remove")
    mr.apply("claude", "register")
    calls = env.calls()
    ops = {(c["tool"], c["argv"][1]) for c in calls}
    assert ops == {("claude", "get"), ("claude", "add"), ("claude", "remove"),
                   ("codex", "get"), ("codex", "add"), ("codex", "remove")}
    for c in calls:
        assert c["env"]["CLAUDE_CONFIG_DIR"] == str(env.claude_dir)
        assert c["env"]["CODEX_HOME"] == str(env.codex_dir)
        for k, v in EXTRA_ENV.items():
            assert c["env"][k] == v, (c["tool"], c["argv"], k)
    if server_env is not None:
        assert os.environ["UV_OFFLINE"] == "0"   # 伺服器行程本身的環境不被改動


def test_健康檢查失敗(env):
    env.claude_register(env={"SECRET_KEY": FAKE_VAL})
    env.codex_register()
    env.set_scenario(claude_get={"code": 1, "stdout": "  Status: ✘ Failed to connect\n  Environment:\n"
                                                      "    SECRET_KEY=%s\n" % FAKE_VAL,
                                 "stderr": "uv: 無法下載依賴(離線)\n"})
    st = mr.status(check=True)
    assert st["claude"]["health"] == "failed"
    assert "無法下載依賴" in st["claude"]["detail"]
    assert FAKE_VAL not in dump(st)
    assert st["claude"]["state"] == "registered"


# ---------- (10) apply 以重新查到的狀態判斷成功 ----------

def test_apply_成功時回新狀態(env):
    r = mr.apply("codex", "register")
    assert r["ok"] is True and r["exit_code"] == 0
    assert r["status"]["codex"]["state"] == "registered"
    assert "Added global MCP server" in r["output"]
    r = mr.apply("claude", "register")
    assert r["ok"] is True
    assert r["status"]["claude"]["state"] == "registered"
    r = mr.apply("codex", "remove")
    assert r["ok"] is True and r["status"]["codex"]["state"] == "absent"
    assert_scrubbed(r, env)


def test_codex_remove_回0但註冊仍在時_ok_為_false(env):
    env.codex_register()
    env.set_scenario(codex_remove={"effect": False})
    r = mr.apply("codex", "remove")
    assert r["exit_code"] == 0
    assert r["ok"] is False
    assert r["status"]["codex"]["state"] == "registered"


def test_add_回0但查不到註冊時_ok_為_false(env):
    env.set_scenario(codex_add={"effect": False}, claude_add={"effect": False})
    for client in mr.CLIENTS:
        r = mr.apply(client, "register")
        assert r["exit_code"] == 0 and r["ok"] is False
        assert r["status"][client]["state"] == "absent"


def test_CLI_非0時_ok_為_false_並附錯誤(env):
    env.set_scenario(claude_add={"code": 1, "stderr": "MCP server local-models already exists in user config\n",
                                 "effect": False})
    r = mr.apply("claude", "register")
    assert r["ok"] is False and r["exit_code"] == 1
    assert "already exists" in r["output"]


# ---------- 一次修正 M-1:CLI 異常輸出時的 env 值遮蔽(統整報告 RS-4) ----------

def _codex_get_out(obj, indent=None):
    return json.dumps(obj, ensure_ascii=False, indent=indent) + "\n"


def test_codex_get_非0結束且_stdout_為含_env_的_JSON(env):
    obj = {"name": "local-models", "transport": {"type": "stdio", "command": env.uv, "args": env.good_args,
                                                 "env": {ENV_KEY: FAKE_VAL}}}
    env.set_scenario(codex_get={"code": 2, "stdout": _codex_get_out(obj), "stderr": "warning: 設定檔有未知欄位\n"})
    st = mr.status(check=False)
    assert st["codex"]["state"] == "unknown"
    assert FAKE_VAL not in dump(st)
    assert "warning" in st["codex"]["detail"]


def test_codex_get_command_非字串且_env_為陣列(env):
    obj = {"name": "local-models", "transport": {"type": "stdio", "command": ["uv"], "args": [],
                                                 "env": [f"{ENV_KEY}={FAKE_VAL}"]}}
    env.set_scenario(codex_get={"code": 0, "stdout": _codex_get_out(obj)})
    st = mr.status(check=False)
    assert st["codex"]["state"] == "unknown"
    text = dump(st)
    assert FAKE_VAL not in text
    assert ENV_KEY in text


@pytest.mark.parametrize("indent", [None, 2])
def test_codex_get_沒有_transport_頂層有_env(env, indent):
    obj = {"name": "local-models", "env": {ENV_KEY: FAKE_VAL}}
    env.set_scenario(codex_get={"code": 0, "stdout": _codex_get_out(obj, indent)})
    st = mr.status(check=False)
    assert st["codex"]["state"] == "unknown"
    assert FAKE_VAL not in dump(st)


def test_codex_remove_非0結束且_stdout_為含_env_的_JSON(env):
    env.codex_register()     # 註冊內容本身沒有 env:秘密值只出現在 CLI 輸出
    obj = {"error": "remove failed", "server": {"transport": {"env": {ENV_KEY: FAKE_VAL}}}}
    env.set_scenario(codex_remove={"code": 1, "stdout": _codex_get_out(obj, 2), "stderr": "Error: 無法寫入設定\n",
                                   "effect": False})
    r = mr.apply("codex", "remove")
    assert r["ok"] is False and r["exit_code"] == 1
    assert FAKE_VAL not in dump(r)
    assert "無法寫入設定" in r["output"]


def test_redact_env_env_為陣列():
    js = json.dumps({"transport": {"env": [f"{ENV_KEY}={FAKE_VAL}", "孤立值-31"]}}, ensure_ascii=False)
    out = mr.redact_env(js, [])
    assert FAKE_VAL not in out and "孤立值-31" not in out
    assert ENV_KEY in out


# ---------- 一次修正:_claude_shadow 型別異常回 None(統整報告 RS-5) ----------

@pytest.mark.parametrize("case", ["projects_not_object", "project_servers_not_object", "mcp_json_servers_not_object"])
def test_遮蔽判斷型別異常時為無法判斷(env, case):
    if case == "projects_not_object":
        env.claude_register(extra={"projects": [str(env.repo)]})
    elif case == "project_servers_not_object":
        env.claude_register(extra={"projects": {str(env.repo): {"mcpServers": ["local-models"]}}})
    else:
        env.claude_register()
        (env.repo / ".mcp.json").write_text(json.dumps({"mcpServers": ["local-models"]}), encoding="utf-8")
    st = mr.status(check=True)
    assert st["claude"]["state"] == "registered"
    assert st["claude"]["shadowed"] is None
    assert env.calls("claude") == []
    assert "不做健康檢查" in st["claude"]["detail"]
