# -*- coding: utf-8 -*-
"""本機 Ollama 連線限制的測試(CHG-20261022-01 T9,依 PR #42 審議修正)。

`taiwan_ai.local_open()` 只連 localhost / 127.0.0.1 / ::1、不經代理、不跟隨重新導向;
`taiwan_ai.chat()`、`unload_llms()`、`studio_server.fetch_ollama_tags()`、`local_models_mcp.list_models()`
一律經它。替身伺服器只綁 127.0.0.1 的隨機埠,不連任何外部網路。
"""
from __future__ import annotations

import ast
import copy
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import settings
import studio_server as ss
import taiwan_ai as tai

REPO = Path(tai.__file__).resolve().parent
TOKEN = "測試用權杖-T9-本機連線".encode("utf-8").hex()
AUTH = {"Origin": "http://127.0.0.1:7860", "X-Studio-Token": TOKEN}


class Stub:
    """127.0.0.1 隨機埠的替身 HTTP 伺服器;記錄收到的請求路徑,依 handler 回應。"""

    def __init__(self, respond):
        self.paths: list[str] = []
        stub = self

        class H(BaseHTTPRequestHandler):
            def _do(self):
                stub.paths.append(self.path)
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                status, headers, body = respond(self.path)
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _do

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stubs():
    made = []

    def make(respond):
        s = Stub(respond)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


def ok_json(data):
    return lambda path: (200, {"Content-Type": "application/json"}, json.dumps(data).encode())


TAGS = {"models": [{"name": "qwen3:8b"}]}


@pytest.fixture
def cfg(monkeypatch):
    data = copy.deepcopy(settings.DEFAULTS)
    monkeypatch.setattr(settings, "load", lambda: copy.deepcopy(data))
    return data


# ---------- opener 本身 ----------

def test_opener_has_no_proxy_and_no_redirect(monkeypatch):
    # ProxyHandler({}) 沒有任何協定方法,build_opener 不會登錄它,也不會再加預設的 ProxyHandler:
    # opener 內沒有任何 ProxyHandler,即不套用環境變數或系統代理
    handlers = tai._LOCAL_OPENER.handlers
    assert [h for h in handlers if isinstance(h, urllib.request.ProxyHandler)] == []
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    assert any(isinstance(h, urllib.request.ProxyHandler)
               for h in urllib.request.build_opener().handlers)   # 對照:預設 opener 會套用代理
    redirects = [h for h in handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
    assert len(redirects) == 1 and isinstance(redirects[0], tai._NoRedirect)


def test_no_direct_urlopen_in_sources():
    for name in ("taiwan_ai.py", "studio_server.py", "local_models_mcp.py"):
        tree = ast.parse((REPO / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "urlopen":
                pytest.fail(f"{name} 第 {node.lineno} 行直接使用 urlopen")
            if isinstance(node, ast.Name) and node.id == "urlopen":
                pytest.fail(f"{name} 第 {node.lineno} 行直接使用 urlopen")


# ---------- 重新導向不跟隨 ----------

def test_redirect_is_not_followed(stubs, monkeypatch, cfg):
    other = stubs(ok_json(TAGS))
    first = stubs(lambda path: (302, {"Location": f"http://localhost:{other.port}{path}"}, b""))
    monkeypatch.setattr(tai, "OLLAMA", f"http://127.0.0.1:{first.port}")
    with pytest.raises(urllib.error.HTTPError) as e:
        tai.local_open(f"{tai.OLLAMA}/api/tags", timeout=3)
    assert e.value.code == 302
    with pytest.raises(OSError):
        tai.chat("reason", "你好")
    tai.unload_llms()                      # 不丟例外
    with pytest.raises(ss.OllamaUnavailable):
        ss.fetch_ollama_tags()
    assert other.paths == []               # 從未跟到重新導向的目標
    assert "/api/tags" in first.paths and "/api/chat" in first.paths and "/api/ps" in first.paths


@pytest.mark.parametrize("code", [301, 303, 307, 308])
def test_other_redirect_codes_are_not_followed(stubs, monkeypatch, code):
    other = stubs(ok_json(TAGS))
    first = stubs(lambda path: (code, {"Location": f"http://localhost:{other.port}/api/tags"}, b""))
    monkeypatch.setattr(tai, "OLLAMA", f"http://127.0.0.1:{first.port}")
    with pytest.raises(ss.OllamaUnavailable):
        ss.fetch_ollama_tags()
    assert other.paths == []


def test_put_config_is_503_when_ollama_redirects(stubs, monkeypatch, tmp_path):
    root = (tmp_path / "repo").resolve()
    (root / "tools/BreezyVoice/data").mkdir(parents=True)
    monkeypatch.setattr(settings, "ROOT", root)
    monkeypatch.setattr(settings, "CONFIG_PATH", root / "config" / "models.json")
    monkeypatch.setattr(ss, "ROOT", root)
    other = stubs(ok_json(TAGS))
    first = stubs(lambda path: (302, {"Location": f"http://localhost:{other.port}{path}"}, b""))
    monkeypatch.setattr(tai, "OLLAMA", f"http://127.0.0.1:{first.port}")
    app = ss.create_app(token=TOKEN, static_dir=root / "no-static", root=root)
    client = TestClient(app, base_url="http://127.0.0.1:7860")
    r = client.put("/api/config", content=json.dumps(settings.DEFAULTS, ensure_ascii=False), headers=AUTH)
    assert r.status_code == 503
    assert not (root / "config" / "studio.db").exists()
    assert other.paths == []
    models = client.get("/api/models").json()
    assert models["ollama"]["reachable"] is False


# ---------- 不經代理 ----------

@pytest.mark.parametrize("var", ["HTTP_PROXY", "http_proxy", "ALL_PROXY"])
def test_proxy_env_is_ignored(stubs, monkeypatch, cfg, var):
    proxy = stubs(ok_json({"models": [{"name": "從代理來的"}]}))
    ollama = stubs(ok_json(TAGS))
    for v in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv(var, f"http://127.0.0.1:{proxy.port}")
    monkeypatch.setattr(tai, "OLLAMA", f"http://127.0.0.1:{ollama.port}")
    assert ss.fetch_ollama_tags() == ["qwen3:8b"]
    tai.unload_llms()
    assert proxy.paths == []
    assert ollama.paths == ["/api/tags", "/api/ps", "/api/generate"]


# ---------- 非本機主機一律拒絕 ----------

@pytest.mark.parametrize("url", ["http://example.com:11434", "http://10.0.0.5:11434",
                                 "http://localhost.evil.example:11434", "https://localhost:11434",
                                 "http://127.0.0.2:11434", "file:///etc"])
def test_non_local_ollama_is_refused(monkeypatch, cfg, url):
    calls = []
    monkeypatch.setattr(tai._LOCAL_OPENER, "open", lambda *a, **k: calls.append(a) or None)
    monkeypatch.setattr(tai, "OLLAMA", url)
    with pytest.raises(ValueError, match="只允許連線到本機 Ollama"):
        tai.chat("reason", "你好")
    with pytest.raises(ValueError):
        tai.local_open(urllib.request.Request(f"{url}/api/chat", b"{}"), timeout=3)
    tai.unload_llms()                      # 不丟例外、不送出
    with pytest.raises(ss.OllamaUnavailable):
        ss.fetch_ollama_tags()
    assert calls == []


@pytest.mark.parametrize("url", ["http://localhost:11434", "http://127.0.0.1:11434", "http://[::1]:11434"])
def test_local_hosts_are_allowed(monkeypatch, url):
    calls = []
    monkeypatch.setattr(tai._LOCAL_OPENER, "open", lambda req, timeout=None: calls.append((req, timeout)) or "ok")
    assert tai.local_open(f"{url}/api/tags", timeout=3) == "ok"
    assert calls == [(f"{url}/api/tags", 3)]
