# -*- coding: utf-8 -*-
"""studio_server.py 設定 API 的測試(CHG-20261022-01 T9)。

一律把 settings.ROOT / CONFIG_PATH 與 `create_app(root=...)` 指到暫存目錄,不碰 repo 內的
`config/`;Ollama `/api/tags` 一律以替身取代(`create_app(ollama_tags=...)`,或替換
`taiwan_ai._LOCAL_OPENER.open`),不連真的網路。以 FastAPI TestClient 呼叫 ASGI app,不開網路埠。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import subprocess
import urllib.error
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import settings
import studio_server as ss
import taiwan_ai

BASE = "http://127.0.0.1:7860"
ORIGIN = "http://127.0.0.1:7860"
TOKEN = "測試用權杖-T9-設定".encode("utf-8").hex()
REPO = Path(ss.__file__).resolve().parent

ALL = sorted({settings.DEFAULTS["roles"][r]["model"] for r in settings.LLM_ROLES}
             | {v for v in settings.DEFAULTS["review_seats"].values() if v != settings.CODEX}
             | {"qwen3:8b"})


def tags_ok(names=ALL):
    return lambda: list(names)


def tags_down():
    raise ss.OllamaUnavailable("連線被拒")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """保險:任何測試意外走到真的 Ollama 請求一律失敗,不連網路。"""
    def boom(*a, **k):
        raise AssertionError("測試不得呼叫真的網路")
    monkeypatch.setattr(taiwan_ai._LOCAL_OPENER, "open", boom)


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = (tmp_path / "repo").resolve()
    r.mkdir()
    monkeypatch.setattr(settings, "ROOT", r)
    monkeypatch.setattr(ss, "ROOT", r)   # 實際部署時 settings.ROOT 與 studio_server.ROOT 相同(scrub 依此遮蔽)
    monkeypatch.setattr(settings, "CONFIG_PATH", r / "config" / "models.json")
    (r / "tools/BreezyVoice/data").mkdir(parents=True)
    (r / "tools/BreezyVoice/data/example.wav").write_bytes(b"RIFF")
    (r / "outputs/uploads").mkdir(parents=True)
    monkeypatch.setattr(taiwan_ai, "MFLUX", r / "no-mflux")
    monkeypatch.setattr(taiwan_ai, "BREEZY", r / "tools/BreezyVoice")
    return r


@pytest.fixture
def make_client(root):
    def _make(tags=tags_ok()):
        app = ss.create_app(runner=lambda g, e, c=None: None, token=TOKEN,
                            static_dir=root / "no-static", root=root, ollama_tags=tags)
        return TestClient(app, base_url=BASE, raise_server_exceptions=False)
    return _make


@pytest.fixture
def client(make_client):
    return make_client()


def auth(**extra):
    return {"Origin": ORIGIN, "X-Studio-Token": TOKEN, **extra}


def cfg(**kw):
    c = copy.deepcopy(settings.DEFAULTS)
    c["roles"]["reason"]["model"] = kw.get("reason", "qwen3:8b")
    return c


def put(client, body, headers=None):
    data = body if isinstance(body, (bytes, str)) else json.dumps(body, ensure_ascii=False)
    return client.put("/api/config", content=data,
                      headers=headers if headers is not None else auth(**{"Content-Type": "application/json"}))


def db(root: Path) -> Path:
    return root / "config" / "studio.db"


def rows(root: Path) -> tuple[list, list, list]:
    con = sqlite3.connect(db(root))
    try:
        return (con.execute("SELECT role, config FROM roles ORDER BY role").fetchall(),
                con.execute("SELECT seat, model FROM review_seats ORDER BY seat").fetchall(),
                con.execute("SELECT key, value FROM meta ORDER BY key").fetchall())
    finally:
        con.close()


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def no_local_paths(text: str, root: Path) -> None:
    for p in {str(root), str(REPO), str(Path.home()), str(Path.home().resolve())}:
        assert p not in text, p


# ---------- GET /api/config ----------

def test_get_config_defaults_without_db(client, root):
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert body["roles"] == settings.DEFAULTS["roles"]
    assert body["review_seats"] == settings.DEFAULTS["review_seats"]
    assert body["meta"] == {}
    assert "_warnings" not in body
    assert not db(root).exists()          # GET 不建立資料庫
    assert r.headers["cache-control"] == "no-store"


def test_get_config_after_import_shows_imported_from(client, root):
    (root / "config").mkdir()
    settings.CONFIG_PATH.write_text(json.dumps(cfg(), ensure_ascii=False), encoding="utf-8")
    body = client.get("/api/config").json()
    assert body["roles"]["reason"]["model"] == "qwen3:8b"
    assert body["meta"]["imported_from"] == "config/models.json"
    assert body["meta"]["imported_at"]
    assert "_warnings" not in body


def test_get_config_warning_when_db_not_sqlite(client, root):
    (root / "config").mkdir()
    db(root).write_bytes(b"this is not sqlite" * 100)
    before = sha(db(root))
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert body["_warnings"] == [settings.WARN_DB_UNREADABLE]
    assert body["roles"] == settings.DEFAULTS["roles"]
    assert body["meta"] == {}
    assert sha(db(root)) == before
    no_local_paths(r.text, root)


def test_get_config_warnings_are_scrubbed(client, root, monkeypatch):
    leak = str(root / "config" / "studio.db")
    monkeypatch.setattr(settings, "load", lambda: {**copy.deepcopy(settings.DEFAULTS),
                                                    "_warnings": [f"無法讀取 {leak}"]})
    r = client.get("/api/config")
    assert r.status_code == 200
    assert r.json()["_warnings"] == ["無法讀取 <repo>/config/studio.db"]
    no_local_paths(r.text, root)


# ---------- PUT /api/config ----------

def test_put_valid_config_saves_and_load_equal(client, root):
    c = cfg()
    r = put(client, c)
    assert r.status_code == 200, r.text
    assert r.json()["saved"] is True
    assert settings.load() == c
    assert not settings.CONFIG_PATH.exists()          # 不建立 config/models.json
    got = client.get("/api/config").json()
    assert got["roles"] == c["roles"] and got["review_seats"] == c["review_seats"]


def test_put_does_not_touch_existing_models_json(client, root):
    (root / "config").mkdir()
    settings.CONFIG_PATH.write_text(json.dumps(settings.DEFAULTS, ensure_ascii=False), encoding="utf-8")
    os.utime(settings.CONFIG_PATH, (1_000_000_000, 1_000_000_000))
    before = (sha(settings.CONFIG_PATH), settings.CONFIG_PATH.stat().st_mtime_ns)
    c = cfg()
    assert put(client, c).status_code == 200
    assert settings.load() == c
    assert (sha(settings.CONFIG_PATH), settings.CONFIG_PATH.stat().st_mtime_ns) == before


def test_put_accepts_get_output_with_warnings_and_meta(client, root):
    """前端把 GET 的回應改一改就送回:`_warnings`、`meta` 不寫入設定。"""
    body = client.get("/api/config").json()
    body["roles"]["reason"]["model"] = "qwen3:8b"
    body["_warnings"] = ["舊警告"]
    body["meta"] = {"imported_from": "x"}
    assert put(client, body).status_code == 200
    assert settings.load() == cfg()


def test_put_unknown_model_is_422_and_db_unchanged(client, root):
    assert put(client, cfg()).status_code == 200
    before = rows(root)
    r = put(client, cfg(reason="not-a-model:1b"))
    assert r.status_code == 422
    errors = r.json()["errors"]
    assert any("roles.reason.model" in e and "not-a-model:1b" in e for e in errors)
    assert rows(root) == before
    assert settings.load() == cfg()


def test_put_unknown_model_without_db_creates_nothing(client, root):
    r = put(client, cfg(reason="not-a-model:1b"))
    assert r.status_code == 422
    assert not db(root).exists()


@pytest.mark.parametrize("seat", list(settings.VETO_SEATS))
def test_put_glm4_on_veto_seat_is_422(client, root, seat):
    assert put(client, cfg()).status_code == 200
    before = rows(root)
    c = cfg()
    c["review_seats"][seat] = "glm4:latest"
    r = put(client, c)
    assert r.status_code == 422
    assert any(f"review_seats.{seat}" in e and "glm4" in e for e in r.json()["errors"])
    assert rows(root) == before


def test_put_codex_on_non_veto_seat_is_422(client, root):
    c = cfg()
    c["review_seats"]["impact"] = settings.CODEX
    r = put(client, c)
    assert r.status_code == 422
    assert not db(root).exists()


def test_put_paused_27b_on_review_seat_is_422(client, root):
    c = cfg()
    c["review_seats"]["risk"] = next(iter(settings.PAUSED_REVIEW_MODELS))
    r = put(client, c)   # 27B 在替身的 Ollama 清單內,仍因暫停而拒絕
    assert r.status_code == 422
    assert any("DIR-1 第 10 條" in e for e in r.json()["errors"])


def test_put_voice_outside_allowed_dirs_is_422_without_path_leak(client, root):
    c = cfg()
    c["roles"]["voice"]["wav"] = str(root / "secret.wav")
    r = put(client, c)
    assert r.status_code == 422
    assert any("roles.voice.wav" in e for e in r.json()["errors"])
    no_local_paths(r.text, root)
    assert not db(root).exists()


@pytest.mark.parametrize("body", [b"{not json", b"[]", "\"字串\"".encode("utf-8"), b"\xff\xfe"])
def test_put_bad_body_is_422(client, root, body):
    r = put(client, body)
    assert r.status_code == 422
    assert r.json()["errors"]
    assert not db(root).exists()


def test_put_body_too_large_is_413(client, root):
    c = cfg()
    c["roles"]["voice"]["text"] = "字" * (ss.MAX_CONFIG_BODY // 2)
    r = put(client, c)
    assert r.status_code == 413
    assert not db(root).exists()


def test_put_body_limit_counts_streamed_bytes_without_content_length(make_client, root):
    """沒有 Content-Length(chunked)時也以串流累計判斷上限。"""
    client = make_client()

    def gen():
        for _ in range(ss.MAX_CONFIG_BODY // 1024 + 2):
            yield b" " * 1024
    r = client.put("/api/config", content=gen(), headers=auth())
    assert r.status_code == 413
    assert not db(root).exists()


# ---------- PUT 經過 T7 中介層 ----------

@pytest.mark.parametrize("headers", [
    {"Origin": ORIGIN},                                        # 缺 Token
    {"Origin": ORIGIN, "X-Studio-Token": "wrong"},             # 錯 Token
    {"X-Studio-Token": TOKEN},                                 # 缺 Origin
    {"Origin": "http://evil.example", "X-Studio-Token": TOKEN},
    {"Origin": "null", "X-Studio-Token": TOKEN},
])
def test_put_without_valid_origin_or_token_is_403_and_unchanged(client, root, headers):
    assert put(client, cfg()).status_code == 200
    before = rows(root)
    r = put(client, cfg(reason=settings.DEFAULTS["roles"]["taiwan"]["model"]), headers=headers)
    assert r.status_code == 403
    assert rows(root) == before
    assert settings.load() == cfg()


def test_put_with_wrong_host_is_403(client, root):
    r = put(client, cfg(), headers=auth(Host="evil.example:7860"))
    assert r.status_code == 403
    assert not db(root).exists()


@pytest.mark.parametrize("path", ["/api/config", "/api/models"])
def test_get_with_wrong_host_is_403(client, path):
    r = client.get(path, headers={"Host": "attacker.example:7860"})
    assert r.status_code == 403
    assert "roles" not in r.text and "voices" not in r.text


# ---------- Ollama 無法連線 ----------

def test_put_when_ollama_unreachable_is_503_and_unchanged(make_client, root):
    good = make_client()
    assert put(good, cfg()).status_code == 200
    before = rows(root)
    down = make_client(tags=tags_down)
    r = put(down, cfg(reason=settings.DEFAULTS["roles"]["taiwan"]["model"]))
    assert r.status_code == 503
    assert "Ollama" in r.json()["error"] and "未儲存" in r.json()["error"]
    assert rows(root) == before
    assert settings.load() == cfg()


def test_put_when_ollama_unreachable_without_db_creates_nothing(make_client, root):
    r = put(make_client(tags=tags_down), cfg())
    assert r.status_code == 503
    assert not db(root).exists()


def test_put_structural_error_is_422_even_when_ollama_unreachable(make_client, root):
    c = cfg()
    c["review_seats"]["security"] = "glm4:latest"
    r = put(make_client(tags=tags_down), c)
    assert r.status_code == 422
    assert not db(root).exists()


def test_put_unknown_model_when_ollama_reachable_but_empty_is_422(make_client, root):
    r = put(make_client(tags=tags_ok([])), cfg())
    assert r.status_code == 422
    assert not db(root).exists()


def test_put_tags_fetcher_unexpected_error_is_503(make_client, root):
    def broken():
        raise RuntimeError(f"壞掉 {root}")
    r = put(make_client(tags=broken), cfg())
    assert r.status_code == 503
    no_local_paths(r.text, root)
    assert not db(root).exists()


# ---------- settings.save 失敗 ----------

def test_put_when_save_raises_is_500_without_local_paths(client, root, monkeypatch):
    def boom(c):
        raise OSError(f"無法寫入 {root / 'config' / 'studio.db'}(家目錄 {Path.home()})")
    monkeypatch.setattr(settings, "save", boom)
    r = put(client, cfg())
    assert r.status_code == 500
    assert r.json()["error"]
    no_local_paths(r.text, root)


def test_put_when_save_raises_value_error_is_500_scrubbed(client, root, monkeypatch):
    def boom(c):
        raise ValueError([f"設定資料庫路徑必須是 {root / 'config' / 'studio.db'}"])
    monkeypatch.setattr(settings, "save", boom)
    r = put(client, cfg())
    assert r.status_code == 500
    no_local_paths(r.text, root)


def test_put_when_db_not_sqlite_is_500_and_file_unchanged(client, root):
    (root / "config").mkdir()
    db(root).write_bytes(b"not sqlite at all" * 50)
    before = sha(db(root))
    r = put(client, cfg())
    assert r.status_code == 500
    assert settings.WARN_DB_UNREADABLE in r.json()["errors"]
    assert sha(db(root)) == before
    assert not (root / "config" / "studio.db-wal").exists()
    no_local_paths(r.text, root)


def test_put_only_goes_through_settings_save(client, root, monkeypatch):
    """PUT 只呼叫 settings.save(),傳入的 dict 不含 `_warnings`、`meta`。"""
    seen = []
    monkeypatch.setattr(settings, "save", lambda c: seen.append(c))
    body = {**cfg(), "_warnings": ["x"], "meta": {"imported_from": "x"}}
    assert put(client, body).status_code == 200
    assert seen == [cfg()]
    assert not db(root).exists()


# ---------- GET /api/models ----------

def test_models_lists_ollama_tools_and_voices(make_client, root):
    (root / "no-mflux").write_text("#!/bin/sh\n")
    (root / "no-mflux").chmod(0o755)
    (root / "tools/BreezyVoice/.venv").mkdir()
    data = root / "tools/BreezyVoice/data"
    (data / "sub").mkdir()
    (data / "sub" / "女聲.wav").write_bytes(b"RIFF")
    (data / ".hidden.wav").write_bytes(b"RIFF")
    (data / "notes.txt").write_text("x")
    (root / "outputs/uploads/abc.wav").write_bytes(b"RIFF")
    (root / "outputs/uploads/.upload-x.part").write_bytes(b"RIFF")
    r = make_client(tags=tags_ok(["qwen3:8b", "glm4:latest"])).get("/api/models")
    assert r.status_code == 200
    body = r.json()
    assert body["ollama"] == {"reachable": True, "models": ["glm4:latest", "qwen3:8b"]}
    assert body["mflux"] == {"installed": True}
    assert body["breezyvoice"] == {"installed": True}
    assert body["voices"] == ["tools/BreezyVoice/data/example.wav",
                              "tools/BreezyVoice/data/sub/女聲.wav",
                              "outputs/uploads/abc.wav"]
    for v in body["voices"]:
        c = cfg()
        c["roles"]["voice"]["wav"] = v
        assert settings.validate(c, None) == []
    no_local_paths(r.text, root)
    assert r.headers["cache-control"] == "no-store"


def test_models_when_tools_missing_and_ollama_down(make_client, root):
    r = make_client(tags=tags_down).get("/api/models")
    assert r.status_code == 200
    body = r.json()
    assert body["ollama"]["reachable"] is False
    assert body["ollama"]["models"] == []
    assert "Ollama" in body["ollama"]["error"]
    assert body["mflux"] == {"installed": False}
    assert body["breezyvoice"] == {"installed": False}
    no_local_paths(r.text, root)


def test_models_voices_skip_symlinks(make_client, root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "偷.wav").write_bytes(b"RIFF")
    data = root / "tools/BreezyVoice/data"
    (data / "link.wav").symlink_to(outside / "偷.wav")
    (data / "linkdir").symlink_to(outside, target_is_directory=True)
    inner = root / "tools/BreezyVoice/data/example.wav"
    (root / "outputs/uploads/inner-link.wav").symlink_to(inner)
    body = make_client().get("/api/models").json()
    assert body["voices"] == ["tools/BreezyVoice/data/example.wav"]


def test_models_voices_skip_symlinked_allowed_dir(make_client, root, tmp_path):
    outside = tmp_path / "outside-uploads"
    outside.mkdir()
    (outside / "x.wav").write_bytes(b"RIFF")
    up = root / "outputs/uploads"
    up.rmdir()
    up.symlink_to(outside, target_is_directory=True)
    body = make_client().get("/api/models").json()
    assert body["voices"] == ["tools/BreezyVoice/data/example.wav"]


def test_models_voice_list_is_capped(make_client, root, monkeypatch):
    for i in range(5):
        (root / f"outputs/uploads/{i}.wav").write_bytes(b"RIFF")
    monkeypatch.setattr(settings, "MAX_VOICE_OPTIONS", 3)
    body = make_client().get("/api/models").json()
    assert len(body["voices"]) == 3


def test_models_filters_bad_tag_names(make_client, root):
    names = ["qwen3:8b", "", "x" * (settings.MAX_NAME_LEN + 1), "壞\n名", 42, None]
    body = make_client(tags=lambda: names).get("/api/models").json()
    assert body["ollama"]["models"] == ["qwen3:8b"]


# ---------- 預設的 Ollama 查詢(以替身取代 opener 的 open) ----------

class FakeResp:
    def __init__(self, data: bytes):
        self.data = data

    def read(self, n=-1):
        return self.data if n < 0 else self.data[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_ollama_tags_uses_local_tags_with_short_timeout(monkeypatch):
    calls = []

    def fake(url, timeout=None):
        calls.append((url, timeout))
        return FakeResp(json.dumps({"models": [{"name": "qwen3:8b"}, {"name": "glm4:latest"},
                                               {"model": "沒有 name"}]}).encode())
    monkeypatch.setattr(taiwan_ai._LOCAL_OPENER, "open", fake)
    assert ss.fetch_ollama_tags() == ["qwen3:8b", "glm4:latest"]
    assert calls == [("http://localhost:11434/api/tags", ss.OLLAMA_TIMEOUT)]
    assert ss.OLLAMA_TIMEOUT <= 3


@pytest.mark.parametrize("exc", [urllib.error.URLError("refused"), TimeoutError("timed out"),
                                 ConnectionRefusedError(61, "refused")])
def test_fetch_ollama_tags_network_errors_raise_unavailable(monkeypatch, exc):
    calls = []

    def fake(url, timeout=None):
        calls.append(url)
        raise exc
    monkeypatch.setattr(taiwan_ai._LOCAL_OPENER, "open", fake)
    with pytest.raises(ss.OllamaUnavailable):
        ss.fetch_ollama_tags()
    assert len(calls) == 1          # 不重試


@pytest.mark.parametrize("data", [b"not json", b"[]", b'{"models": "x"}', b"{}",
                                  b"x" * (ss.MAX_TAGS_BYTES + 10)])
def test_fetch_ollama_tags_bad_response_raises_unavailable(monkeypatch, data):
    monkeypatch.setattr(taiwan_ai._LOCAL_OPENER, "open", lambda url, timeout=None: FakeResp(data))
    with pytest.raises(ss.OllamaUnavailable):
        ss.fetch_ollama_tags()


def test_default_app_uses_fetch_ollama_tags(root, monkeypatch):
    monkeypatch.setattr(taiwan_ai._LOCAL_OPENER, "open",
                        lambda url, timeout=None: FakeResp(json.dumps(
                            {"models": [{"name": n} for n in ALL]}).encode()))
    app = ss.create_app(token=TOKEN, static_dir=root / "no-static", root=root)
    c = TestClient(app, base_url=BASE)
    assert put(c, cfg()).status_code == 200
    assert settings.load() == cfg()


# ---------- settings.load_meta ----------

def test_load_meta_is_read_only(root):
    assert settings.load_meta() == {}
    assert not db(root).exists()
    (root / "config").mkdir()
    db(root).write_bytes(b"garbage" * 100)
    before = sha(db(root))
    assert settings.load_meta() == {}
    assert sha(db(root)) == before
    assert not (root / "config" / "studio.db-wal").exists()


def test_load_meta_after_save_has_no_import(root):
    settings.save(cfg())
    assert settings.load_meta() == {}


# ---------- 靜態檢查 ----------

def test_studio_server_never_touches_db_or_json_directly():
    r = subprocess.run(["grep", "-n", r"sqlite3\|models\.json", str(REPO / "studio_server.py")],
                       capture_output=True, text=True)
    assert r.stdout == ""


# ---------- CHG-20261029-01 T8:合音詞庫端點 ----------

def fusion_rows(root: Path) -> list:
    con = sqlite3.connect(db(root))
    try:
        return con.execute("SELECT word, zhuyin, enabled, source FROM fusion_words ORDER BY word").fetchall()
    finally:
        con.close()


def preview(client, raw: bytes, headers=None):
    return client.post("/api/settings/fusions/import/preview", files={"file": ("詞庫.json", raw, "application/json")},
                       headers=auth() if headers is None else headers)


def apply(client, body, headers=None):
    return client.post("/api/settings/fusions/import/apply", json=body,
                       headers=auth() if headers is None else headers)


def fusion_file(words) -> bytes:
    return json.dumps({"format": "podcast-studio-fusions", "version": 1, "words": words},
                      ensure_ascii=False).encode("utf-8")


def test_合音詞庫列表_新增_刪除預設詞422(client, root):
    r = client.get("/api/settings/fusions")
    assert r.status_code == 200 and len(r.json()["words"]) == 6 and len(r.json()["version"]) == 64
    r = client.put("/api/settings/fusions/然後", json={"zhuyin": "ㄖㄢˊ", "enabled": True}, headers=auth())
    assert r.status_code == 200 and ("然後", "ㄖㄢˊ", 1, "custom") in fusion_rows(root)
    r = client.delete("/api/settings/fusions/知道", headers=auth())
    assert r.status_code == 422 and "預設詞不可刪除" in r.json()["error"]
    assert client.delete("/api/settings/fusions/然後", headers=auth()).status_code == 200
    assert client.delete("/api/settings/fusions/然後", headers=auth()).status_code == 404
    assert client.put("/api/settings/fusions/知道", json={"zhuyin": "ㄓㄧ", "enabled": True},
                      headers=auth()).status_code == 422
    assert client.put("/api/settings/fusions/x", json={"zhuyin": "ㄓ", "enabled": True},
                      headers=auth()).status_code == 404
    client.put("/api/settings/fusions/知道", json={"zhuyin": "ㄓㄠˋ", "enabled": False}, headers=auth())
    r = client.put("/api/settings/fusions/知道", json={"restore": True}, headers=auth())
    assert r.status_code == 200 and ("知道", "ㄗㄠˋ", 1, "default") in fusion_rows(root)


def test_缺Token的寫入與匯入回403(client, root):
    no = {"Origin": ORIGIN}
    assert client.put("/api/settings/fusions/然後", json={"zhuyin": "ㄖㄢˊ", "enabled": True},
                      headers=no).status_code == 403
    assert preview(client, fusion_file([]), headers=no).status_code == 403
    assert apply(client, {"preview_id": "x", "base_version": "y", "apply": "all"}, headers=no).status_code == 403
    assert not db(root).exists()


def test_匯出JSON與CSV_匯出內容等於列表(client):
    r = client.get("/api/settings/fusions/export?format=json")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert json.loads(r.content)["words"] == client.get("/api/settings/fusions").json()["words"]
    r = client.get("/api/settings/fusions/export?format=csv")
    lines = r.content.decode("utf-8").splitlines()
    assert lines[0] == "word,zhuyin,enabled,source" and len(lines) == 7
    assert all(not ln[0] in "=+-@" for ln in lines)
    assert client.get("/api/settings/fusions/export?format=xml").status_code == 422


def test_一致的預覽_apply回409(client, root):
    raw = client.get("/api/settings/fusions/export?format=json").content
    r = preview(client, raw)
    assert r.status_code == 200 and r.json()["status"] == "same" and r.json()["changes"] == []
    r2 = apply(client, {"preview_id": r.json()["preview_id"], "base_version": r.json()["base_version"],
                        "apply": "all"})
    assert r2.status_code == 409


def test_預覽套用_代號單次有效_版本不符409(client, root):
    client.put("/api/settings/fusions/然後", json={"zhuyin": "ㄖㄢˊ", "enabled": True}, headers=auth())
    words = [w for w in json.loads(client.get("/api/settings/fusions/export?format=json").content)["words"]
             if w["word"] not in ("然後", "我們")]
    p = preview(client, fusion_file(words)).json()
    assert {(c["word"], c["kind"]) for c in p["changes"]} == {("然後", "delete"), ("我們", "disable")}
    assert len(p["preview_id"]) == 32
    body = {"preview_id": p["preview_id"], "base_version": p["base_version"], "keep": ["我們"]}
    r = apply(client, body)
    assert r.status_code == 200, r.text
    assert r.json()["applied"] == ["然後"] and r.json()["kept"] == ["我們"]
    assert apply(client, body).status_code == 409                      # 同一代號第二次
    # 預覽後另一個請求改了詞庫 → 409、詞庫不變
    p = preview(client, fusion_file(words)).json()
    client.put("/api/settings/fusions/知道", json={"zhuyin": "ㄓㄠˋ", "enabled": True}, headers=auth())
    before = fusion_rows(root)
    r = apply(client, {"preview_id": p["preview_id"], "base_version": p["base_version"], "apply": "all"})
    assert r.status_code == 409 and "重新預覽" in r.json()["error"]
    assert fusion_rows(root) == before


def test_過期代號回409(client, root, monkeypatch):
    words = [{"word": "好像", "zhuyin": "ㄏㄠˇ", "enabled": True}]
    p = preview(client, fusion_file(words)).json()
    monkeypatch.setattr(ss.time, "monotonic", lambda: 10 ** 9)
    r = apply(client, {"preview_id": p["preview_id"], "base_version": p["base_version"], "apply": "all"})
    assert r.status_code == 409


@pytest.mark.parametrize("raw,status", [
    (b"x" * (300 * 1024), 413),
    ("{}".encode("utf-16"), 422),
    (b"{\"format\": ", 422),
    (fusion_file([{"word": chr(0x4E00 + i) + "好", "zhuyin": "ㄏㄠˇ", "enabled": True} for i in range(1001)]), 413),
])
def test_整檔不合法回413或422且詞庫不變(client, root, raw, status):
    client.put("/api/settings/fusions/然後", json={"zhuyin": "ㄖㄢˊ", "enabled": True}, headers=auth())
    before = fusion_rows(root)
    r = preview(client, raw)
    assert r.status_code == status, r.text
    assert fusion_rows(root) == before


def test_被拒詞條列在rejected含word與reason(client):
    words = [{"word": "怎麼樣", "zhuyin": "ㄗㄤˇ", "enabled": True}, {"word": "好像", "zhuyin": "ㄓㄧ", "enabled": True}]
    r = preview(client, fusion_file(words)).json()
    assert [(x["word"], x["reason"]) for x in r["rejected"]] == [("怎麼樣", "詞須為兩個漢字"),
                                                                ("好像", "注音不是單一合法音節")]
    assert r["rejected"][0]["row"] == 1


def test_合音建議讀設定資料庫的詞庫(client):
    r = client.post("/api/fusion/suggest", json={"text": "我也不知道"}, headers=auth())
    assert r.json()["suggestions"] == [{"index": 3, "zhuyin": "ㄗㄠˋ", "source": "lexicon"}]


def test_匯入上限端點_缺Token403_超出範圍422(client, root):
    r = client.get("/api/settings/fusions/limits")
    assert r.status_code == 200 and (r.json()["max_kb"], r.json()["max_rows"]) == (256, 1000)
    assert client.put("/api/settings/fusions/limits", json={"max_kb": 32, "max_rows": 20},
                      headers={"Origin": ORIGIN}).status_code == 403
    for bad in ({"max_kb": 15, "max_rows": 20}, {"max_kb": 32, "max_rows": 10001}, {"max_kb": "32", "max_rows": 20}):
        assert client.put("/api/settings/fusions/limits", json=bad, headers=auth()).status_code == 422
    assert client.get("/api/settings/fusions/limits").json()["max_kb"] == 256
    r = client.put("/api/settings/fusions/limits", json={"max_kb": 32, "max_rows": 20}, headers=auth())
    assert r.status_code == 200 and r.json()["max_kb"] == 32


def test_改上限後預覽依設定值回413(client, root):
    client.put("/api/settings/fusions/limits", json={"max_kb": 32, "max_rows": 20}, headers=auth())
    r = preview(client, b" " * (33 * 1024))
    assert r.status_code == 413 and r.json()["error"] == "超過允許大小上限(32 KB)"
    r = preview(client, fusion_file([{"word": chr(0x4E00 + i) + "好", "zhuyin": "ㄏㄠˇ", "enabled": True}
                                     for i in range(21)]))
    assert r.status_code == 413 and r.json()["error"] == "超過筆數上限(20 筆)"


def test_請求本體超過上限加16KB時不讀完即回413(client, root):
    client.put("/api/settings/fusions/limits", json={"max_kb": 16, "max_rows": 20}, headers=auth())
    r = client.post("/api/settings/fusions/import/preview", content=b"x" * (33 * 1024),
                    headers=auth(**{"Content-Type": "multipart/form-data; boundary=zz"}))
    assert r.status_code == 413 and r.json()["error"] == "超過允許大小上限(16 KB)"
    # 串流讀取在超過上限的那一塊就停止(不讀完):以替身 request 計算被取走的區塊數
    import asyncio
    pulled = []

    class Req:
        headers = {}

        async def stream(self):
            for _ in range(200):
                pulled.append(1)
                yield b"x" * 1024
    assert asyncio.run(ss._read_body(Req(), 16 * 1024 + ss.FUSION_MULTIPART_OVERHEAD)) is None
    assert len(pulled) == 33
