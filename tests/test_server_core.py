# -*- coding: utf-8 -*-
"""studio_server.py 伺服器核心與安全檢查的測試(CHG-20261022-01 T7)。

以 FastAPI TestClient 直接呼叫 ASGI app,不開真的網路埠;執行器一律以替身取代
(或只用不呼叫模型的 text-input 節點),不呼叫 Ollama / mflux / BreezyVoice / ffmpeg。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import executor
import studio_server as ss

BASE = "http://127.0.0.1:7860"
ORIGIN = "http://127.0.0.1:7860"
TOKEN = "測試用權杖不可能猜到-abc123".encode("utf-8").hex()

GOOD_GRAPH = {
    "nodes": [
        {"id": "n1", "kind": "text-input", "params": {"text": "台灣夜市文化"}},
        {"id": "n2", "kind": "text-input", "params": {"text": "夜市的味道"}},
    ],
    "edges": [],
}


def fake_runner(graph, emit, cancel=None):
    """替身執行器:依節點清單推送 started → running/done → finished。"""
    ids = [n["id"] for n in graph["nodes"]]
    emit({"type": "started", "order": ids})
    for nid in ids:
        emit({"type": "node", "node": nid, "status": "running"})
        emit({"type": "node", "node": nid, "status": "done", "outputs": {"text": nid}})
    emit({"type": "finished", "status": "ok"})


@pytest.fixture
def make_client(tmp_path):
    def _make(runner=fake_runner, static_dir=None, base=BASE):
        app = ss.create_app(runner=runner, token=TOKEN,
                            static_dir=static_dir or (tmp_path / "no-static"))
        return TestClient(app, base_url=base)
    return _make


@pytest.fixture
def client(make_client):
    return make_client()


def auth(**extra):
    return {"Origin": ORIGIN, "X-Studio-Token": TOKEN, **extra}


def read_sse(client, run_id, headers=None) -> list[dict]:
    events = []
    with client.stream("GET", f"/api/run/{run_id}/events", headers=headers or {}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
    return events


def start_run(client, graph=GOOD_GRAPH) -> str:
    r = client.post("/api/run", json=graph, headers=auth())
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


# ---------- 中介層:改變狀態的請求 ----------

def test_post_without_token_is_403(client):
    r = client.post("/api/run", json=GOOD_GRAPH, headers={"Origin": ORIGIN})
    assert r.status_code == 403


def test_post_with_wrong_token_is_403(client):
    r = client.post("/api/run", json=GOOD_GRAPH, headers=auth(**{"X-Studio-Token": TOKEN + "x"}))
    assert r.status_code == 403


def test_post_with_non_ascii_token_is_403_not_500(client):
    r = client.post("/api/run", json=GOOD_GRAPH,
                    headers={"Origin": ORIGIN, "X-Studio-Token": "éé".encode("latin-1")})
    assert r.status_code == 403


@pytest.mark.parametrize("origin", [
    "http://evil.example", "null", "http://127.0.0.1:7860/", "https://127.0.0.1:7860",
    "http://127.0.0.1:7861", "http://localhost:7860.evil.example",
])
def test_post_with_wrong_origin_is_403(client, origin):
    r = client.post("/api/run", json=GOOD_GRAPH, headers=auth(Origin=origin))
    assert r.status_code == 403


def test_post_without_origin_is_403(client):
    r = client.post("/api/run", json=GOOD_GRAPH, headers={"X-Studio-Token": TOKEN})
    assert r.status_code == 403


@pytest.mark.parametrize("host", ["evil.example", "evil.example:7860", "127.0.0.1:7861",
                                  "127.0.0.1", "localhost"])
def test_post_with_wrong_host_is_403(client, host):
    r = client.post("/api/run", json=GOOD_GRAPH, headers=auth(Host=host))
    assert r.status_code == 403


def test_localhost_origin_and_host_are_accepted(make_client):
    c = make_client(base="http://localhost:7860")
    r = c.post("/api/run", json=GOOD_GRAPH, headers=auth(Origin="http://localhost:7860"))
    assert r.status_code == 200


def test_options_preflight_is_403_without_cors_headers(client):
    r = client.options("/api/run", headers={
        "Origin": ORIGIN, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-studio-token"})
    assert r.status_code == 403
    assert not any(k.lower().startswith("access-control-") for k in r.headers)


def test_cancel_requires_token(client):
    run_id = start_run(client)
    r = client.post(f"/api/run/{run_id}/cancel", headers={"Origin": ORIGIN})
    assert r.status_code == 403


# ---------- 中介層:所有請求檢查 Host ----------

@pytest.mark.parametrize("path", ["/", "/api/nodes", "/api/config", "/files/podcast.mp4",
                                  "/files/uploads/x.png", "/api/run/whatever/events"])
def test_get_with_wrong_host_is_403(client, path):
    r = client.get(path, headers={"Host": "evil.example:7860"})
    assert r.status_code == 403


def test_sse_with_wrong_host_is_403_even_for_real_run(client):
    run_id = start_run(client)
    r = client.get(f"/api/run/{run_id}/events", headers={"Host": "attacker.example"})
    assert r.status_code == 403
    assert "data:" not in r.text


def test_get_with_correct_host_needs_no_token(client):
    assert client.get("/api/nodes").status_code == 200


# ---------- 安全標頭 ----------

@pytest.mark.parametrize("method,path,headers", [
    ("GET", "/", {}),
    ("GET", "/api/nodes", {}),
    ("GET", "/api/nodes", {"Host": "evil.example"}),
    ("POST", "/api/run", {"Origin": ORIGIN}),
])
def test_security_headers_on_every_response(client, method, path, headers):
    r = client.request(method, path, headers=headers)
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == ss.CSP
    assert not any(k.lower().startswith("access-control-") for k in r.headers)


def test_csp_matches_global_constraint():
    assert ss.CSP == "default-src 'self'; media-src 'self' blob:; img-src 'self' blob: data:"


# ---------- 首頁與權杖 ----------

def test_index_placeholder_has_token(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert TOKEN in r.text
    assert 'name="studio-token"' in r.text
    assert "no-store" in r.headers.get("cache-control", "")


def test_index_from_static_dir_gets_token_injected(make_client, tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text(
        f'<!doctype html><meta name="studio-token" content="{ss.TOKEN_PLACEHOLDER}"><p>畫布</p>',
        encoding="utf-8")
    (static / "app.js").write_text("console.log('ok');", encoding="utf-8")
    c = make_client(static_dir=static)
    r = c.get("/")
    assert TOKEN in r.text and ss.TOKEN_PLACEHOLDER not in r.text and "畫布" in r.text
    js = c.get("/static/app.js")
    assert js.status_code == 200 and "console.log" in js.text
    assert TOKEN not in js.text


def test_token_only_in_index(client):
    assert TOKEN not in client.get("/api/nodes").text
    assert TOKEN not in client.post("/api/run", json=GOOD_GRAPH, headers={"Origin": ORIGIN}).text


def test_static_path_traversal_is_404(make_client, tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (tmp_path / "secret.txt").write_text("機密", encoding="utf-8")
    c = make_client(static_dir=static)
    for p in ["/static/%2e%2e/secret.txt", "/static/..%2fsecret.txt", "/static/%2Fetc%2Fpasswd"]:
        r = c.get(p)
        assert r.status_code == 404, p
        assert "機密" not in r.text


def test_default_token_is_random_per_app():
    a, b = ss.create_app(), ss.create_app()
    assert a.state.token != b.state.token
    assert len(a.state.token) >= 40


def test_main_binds_loopback_only(monkeypatch, capsys):
    seen = {}

    class FakeUvicorn:
        @staticmethod
        def run(app, **kw):
            seen.update(kw, app=app)

    monkeypatch.setattr(ss, "uvicorn", FakeUvicorn, raising=False)
    monkeypatch.setattr(ss, "_import_uvicorn", lambda: FakeUvicorn)
    ss.main()
    assert seen["host"] == "127.0.0.1" and seen["port"] == 7860
    assert seen.get("proxy_headers") is False
    out = capsys.readouterr()
    assert seen["app"].state.token not in out.out + out.err


# ---------- GET /api/nodes ----------

def test_api_nodes_lists_registry(client):
    import nodes
    data = client.get("/api/nodes").json()
    kinds = {n["kind"] for n in data["nodes"]}
    assert kinds == set(nodes.REGISTRY)
    assert set(data["port_types"]) == {"text", "image", "audio", "video"}
    one = next(n for n in data["nodes"] if n["kind"] == "write-script")
    assert one == nodes.REGISTRY["write-script"].spec_dict()


# ---------- POST /api/run 與 SSE ----------

def test_valid_run_streams_started_node_finished(client):
    run_id = start_run(client)
    events = read_sse(client, run_id)
    types = [e["type"] for e in events]
    assert types[0] == "started" and types[-1] == "finished"
    assert events[0]["order"] == ["n1", "n2"]
    node_events = [e for e in events if e["type"] == "node"]
    assert [(e["node"], e["status"]) for e in node_events] == [
        ("n1", "running"), ("n1", "done"), ("n2", "running"), ("n2", "done")]
    assert events[-1]["status"] == "ok"


def test_sse_replays_from_last_event_id(client):
    run_id = start_run(client)
    full = read_sse(client, run_id)
    tail = read_sse(client, run_id, headers={"Last-Event-ID": "2"})
    assert tail == full[3:]


def test_real_executor_runs_text_input_graph(make_client):
    c = make_client(runner=executor.run)
    run_id = start_run(c)
    events = read_sse(c, run_id)
    done = {e["node"]: e["outputs"] for e in events if e.get("status") == "done"}
    assert done == {"n1": {"text": "台灣夜市文化"}, "n2": {"text": "夜市的味道"}}
    assert events[-1] == {"type": "finished", "status": "ok"}


def test_invalid_graph_is_400_with_node_and_port(client):
    graph = {
        "nodes": [
            {"id": "a", "kind": "text-input", "params": {"text": "x"}},
            {"id": "b", "kind": "speak", "params": {}},
        ],
        "edges": [],
    }
    r = client.post("/api/run", json=graph, headers=auth())
    assert r.status_code == 400
    errors = r.json()["errors"]
    assert {"node_id": "b", "port": "text"} in [
        {"node_id": e["node_id"], "port": e["port"]} for e in errors]
    assert all(isinstance(e["message"], str) and e["message"] for e in errors)


def test_type_mismatch_is_400_pointing_at_port(client):
    graph = {
        "nodes": [
            {"id": "img", "kind": "file-input", "params": {"kind": "image", "file": "x.png"}},
            {"id": "sp", "kind": "speak", "params": {}},
        ],
        "edges": [{"from": {"node": "img", "port": "image"}, "to": {"node": "sp", "port": "text"}}],
    }
    r = client.post("/api/run", json=graph, headers=auth())
    assert r.status_code == 400
    assert any(e["node_id"] == "sp" and e["port"] == "text" for e in r.json()["errors"])


@pytest.mark.parametrize("body", [
    {"nodes": [{"id": 1, "kind": "text-input", "params": {"text": "x"}}], "edges": []},
    {"nodes": [{"id": "1", "kind": "text-input"}],
     "edges": [{"from": {"node": 1, "port": "text"}, "to": {"node": 2, "port": "text"}}]},
    [1, 2, 3],
    "字串",
])
def test_non_string_ids_or_bad_shape_is_400(client, body):
    r = client.post("/api/run", json=body, headers=auth())
    assert r.status_code == 400
    assert r.json()["errors"]


def test_malformed_json_is_400(client):
    r = client.post("/api/run", content=b"{not json", headers=auth(**{"Content-Type": "application/json"}))
    assert r.status_code == 400


def test_oversized_body_is_413(client, monkeypatch):
    monkeypatch.setattr(ss, "MAX_BODY", 1000)
    r = client.post("/api/run", content=b"{" + b" " * 2000 + b"}", headers=auth())
    assert r.status_code == 413


def test_second_concurrent_run_is_409_and_cancel_stops_it(make_client):
    started = threading.Event()

    def slow(graph, emit, cancel=None):
        emit({"type": "started", "order": ["n1"]})
        started.set()
        assert cancel is not None
        cancel.wait(10)
        emit({"type": "node", "node": "n1", "status": "cancelled", "message": "已取消"})
        emit({"type": "finished", "status": "cancelled"})

    c = make_client(runner=slow)
    run_id = start_run(c)
    assert started.wait(5)
    r = c.post("/api/run", json=GOOD_GRAPH, headers=auth())
    assert r.status_code == 409
    r = c.post(f"/api/run/{run_id}/cancel", headers=auth())
    assert r.status_code == 200
    events = read_sse(c, run_id)
    assert events[-1] == {"type": "finished", "status": "cancelled"}
    # 結束後可以再開新的 run
    assert c.post("/api/run", json=GOOD_GRAPH, headers=auth()).status_code == 200


def test_unknown_run_is_404(client):
    assert client.get("/api/run/nope/events").status_code == 404
    assert client.post("/api/run/nope/cancel", headers=auth()).status_code == 404


# ---------- 本機路徑不外洩 ----------

def _leaks(text: str) -> bool:
    home = str(Path.home())
    return str(ss.ROOT) in text or home in text or str(Path.home().resolve()) in text


def test_node_error_message_paths_are_scrubbed(make_client):
    home = str(Path.home())

    def failing(graph, emit, cancel=None):
        emit({"type": "started", "order": ["n1"]})
        emit({"type": "node", "node": "n1", "status": "error",
              "message": f"找不到 {ss.ROOT}/tools/BreezyVoice/x.wav 與 {home}/.cache/huggingface"})
        emit({"type": "node", "node": "n2", "status": "done",
              "outputs": {"image": f"{ss.ROOT}/outputs/cover-1.png"}})
        emit({"type": "finished", "status": "error"})

    c = make_client(runner=failing)
    run_id = start_run(c)
    events = read_sse(c, run_id)
    raw = json.dumps(events, ensure_ascii=False)
    assert not _leaks(raw)
    err = next(e for e in events if e.get("status") == "error")
    assert "<repo>/tools/BreezyVoice/x.wav" in err["message"]
    assert "~/.cache/huggingface" in err["message"]
    done = next(e for e in events if e.get("status") == "done")
    assert done["outputs"]["image"] == "outputs/cover-1.png"


def test_runner_crash_becomes_error_and_finished_without_paths(make_client):
    def crash(graph, emit, cancel=None):
        emit({"type": "started", "order": []})
        raise OSError(f"無法開啟 {ss.ROOT}/outputs/.heavy.lock")

    c = make_client(runner=crash)
    run_id = start_run(c)
    events = read_sse(c, run_id)
    assert events[-1] == {"type": "finished", "status": "error"}
    assert any(e["type"] == "error" for e in events)
    assert not _leaks(json.dumps(events, ensure_ascii=False))
    # 執行緒結束後可再開新 run
    assert c.post("/api/run", json=GOOD_GRAPH, headers=auth()).status_code == 200


def test_400_messages_are_scrubbed(client, monkeypatch):
    monkeypatch.setattr(executor, "validate", lambda g: [
        executor.Error("n1", "file", f"找不到 {ss.ROOT}/outputs/x.png")])
    r = client.post("/api/run", json=GOOD_GRAPH, headers=auth())
    assert r.status_code == 400
    assert not _leaks(r.text)
    assert "<repo>/outputs/x.png" in r.json()["errors"][0]["message"]


def test_500_has_no_paths_and_keeps_security_headers(client, monkeypatch):
    def boom(g):
        raise RuntimeError(f"內部錯誤 {ss.ROOT}/executor.py")

    monkeypatch.setattr(executor, "validate", boom)
    r = client.post("/api/run", json=GOOD_GRAPH, headers=auth())
    assert r.status_code == 500
    assert not _leaks(r.text)
    assert r.headers["x-content-type-options"] == "nosniff"


def test_scrub_replaces_repo_before_home():
    s = ss.scrub(f"{ss.ROOT}/a 與 {Path.home()}/b")
    assert s == "<repo>/a 與 ~/b"
