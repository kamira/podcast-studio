"""executor.py 圖執行器的測試(CHG-20261022-01 T6)。

節點一律以測試內登錄的替身取代(monkeypatch `nodes.REGISTRY`),或使用不會呼叫模型的
真節點(text-input 與只做驗證的 generate-image);不呼叫 Ollama / mflux / BreezyVoice / ffmpeg。
重型鎖把 `heavy_lock.LOCK_PATH` 指到暫存目錄,不碰 repo 內的 outputs/。
"""
from __future__ import annotations

import fcntl
import os
import threading
import time

import pytest

import executor
import heavy_lock as hl
import nodes
from nodes import NodeSpec, Param, Port


# ---------- 替身節點 ----------

class Recorder:
    """記錄替身節點的呼叫順序與收到的輸入。"""

    def __init__(self):
        self.calls: list[str] = []
        self.inputs: dict[str, dict] = {}
        self.lock_held: dict[str, bool] = {}


def _lock_is_held() -> bool:
    """以另一個檔案描述嘗試非阻塞取鎖;取不到代表執行器正持有重型鎖。"""
    fd = os.open(hl.LOCK_PATH, os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


@pytest.fixture
def lock_path(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    p = root / "outputs" / ".heavy.lock"
    monkeypatch.setattr(hl, "ROOT", root)
    monkeypatch.setattr(hl, "LOCK_PATH", p)
    return p


@pytest.fixture
def rec(monkeypatch, lock_path):
    r = Recorder()
    T = Port("text")

    def make(kind, heavy=False, fail=False):
        def fn(inputs, params):
            r.calls.append((params.get("tag") or kind))
            r.inputs[(params.get("tag") or kind)] = dict(inputs)
            if heavy:
                r.lock_held[(params.get("tag") or kind)] = _lock_is_held()
            if fail:
                raise RuntimeError("替身節點失敗")
            joined = "+".join(f"{k}={v}" for k, v in sorted(inputs.items()))
            return {"text": f"{params.get('tag', kind)}({joined})"}
        return fn

    tag = {"tag": Param("str", "標記", default="", max_len=100)}
    specs = [
        NodeSpec("src", "來源", {}, {"text": T}, dict(tag), False, make("src")),
        NodeSpec("pass", "轉送", {"text": T}, {"text": T}, dict(tag), False, make("pass")),
        NodeSpec("heavy-pass", "重型轉送", {"text": T}, {"text": T}, dict(tag), True, make("heavy-pass", heavy=True)),
        NodeSpec("join", "合併", {"a": T, "b": T}, {"text": T}, dict(tag), False, make("join")),
        NodeSpec("boom", "會失敗", {"text": T}, {"text": T}, dict(tag), False, make("boom", fail=True)),
        NodeSpec("stepper", "步數", {}, {"text": T},
                 {**tag, "steps": Param("int", "步數", default=4, minimum=1, maximum=8)}, False, make("stepper")),
    ]
    reg = dict(nodes.REGISTRY)
    reg.update({s.kind: s for s in specs})
    monkeypatch.setattr(nodes, "REGISTRY", reg)
    return r


def N(nid, kind, **params):
    return {"id": nid, "kind": kind, "params": params}


def E(src, sport, dst, dport):
    return {"from": {"node": src, "port": sport}, "to": {"node": dst, "port": dport}}


def G(nodes_, edges=()):
    return {"nodes": list(nodes_), "edges": list(edges)}


def run_collect(graph, cancel=None):
    events: list[dict] = []
    executor.run(graph, events.append, cancel=cancel)
    return events


def node_status(events):
    """每個節點最後一個狀態事件。"""
    out = {}
    for ev in events:
        if ev["type"] == "node":
            out[ev["node"]] = ev
    return out


# ---------- validate:各回一筆錯誤 ----------

def test_合法的圖沒有錯誤(rec):
    g = G([N("a", "src"), N("b", "pass")], [E("a", "text", "b", "text")])
    assert executor.validate(g) == []


def test_有環回一筆錯誤(rec):
    g = G([N("a", "pass"), N("b", "pass")], [E("a", "text", "b", "text"), E("b", "text", "a", "text")])
    errs = executor.validate(g)
    assert len(errs) == 1
    assert errs[0].node_id in {"a", "b"} and "環" in errs[0].message


def test_image接到text輸入回一筆錯誤(rec):
    g = G([N("t", "text-input", text="夜市"), N("img", "generate-image"), N("v", "speak")],
          [E("t", "text", "img", "prompt"), E("img", "image", "v", "text")])
    errs = executor.validate(g)
    assert len(errs) == 1
    e = errs[0]
    assert (e.node_id, e.port) == ("v", "text")
    assert "型別不符" in e.message and "image" in e.message and "text" in e.message


def test_必填輸入未接回一筆錯誤(rec):
    errs = executor.validate(G([N("v", "speak")]))
    assert len(errs) == 1
    assert (errs[0].node_id, errs[0].port) == ("v", "text")


def test_選填輸入未接不算錯(rec):
    g = G([N("t", "text-input", text="標題"), N("c", "cover-prompt")], [E("t", "text", "c", "title")])
    assert executor.validate(g) == []


@pytest.mark.parametrize("node,label", [
    (N("img", "generate-image", width=17), "width=17"),
    (N("s", "stepper", steps=99), "steps=99"),
    (N("t", "text-input", text=123), "字串參數非字串"),
])
def test_參數超出範圍回一筆錯誤(rec, node, label):
    nodes_ = [node]
    edges = []
    if node["kind"] == "generate-image":
        nodes_.insert(0, N("p", "text-input", text="prompt"))
        edges.append(E("p", "text", "img", "prompt"))
    errs = executor.validate(G(nodes_, edges))
    assert len(errs) == 1, (label, errs)
    assert errs[0].node_id == node["id"]
    assert errs[0].port == next(iter(node["params"]))


def test_未知種類回一筆錯誤(rec):
    errs = executor.validate(G([N("x", "rm-rf")]))
    assert len(errs) == 1 and errs[0].node_id == "x" and "未知" in errs[0].message


def test_未知參數回錯誤(rec):
    errs = executor.validate(G([N("t", "text-input", text="a", evil="x")]))
    assert len(errs) == 1 and errs[0].port == "evil"


def test_file_input只開放與檔案種類相符的輸出埠(rec):
    g = G([{"id": "f", "kind": "file-input", "params": {"file": "uploads/a.png", "kind": "image"}}, N("v", "speak")],
          [E("f", "text", "v", "text")])
    errs = executor.validate(g)
    assert len(errs) == 1 and (errs[0].node_id, errs[0].port) == ("f", "text")


def test_同一輸入接兩條線回錯誤(rec):
    g = G([N("a", "src"), N("b", "src"), N("c", "pass")],
          [E("a", "text", "c", "text"), E("b", "text", "c", "text")])
    errs = executor.validate(g)
    assert len(errs) == 1 and (errs[0].node_id, errs[0].port) == ("c", "text")


def test_節點超過上限回錯誤(rec):
    g = G([N(f"n{i}", "src") for i in range(executor.MAX_NODES + 1)])
    errs = executor.validate(g)
    assert len(errs) == 1 and "100" in errs[0].message


@pytest.mark.parametrize("graph", [
    None, [], {"nodes": "x"}, {"nodes": [{"id": 1, "kind": "src"}]},
    {"nodes": [N("a", "src"), N("a", "src")]},
    {"nodes": [N("a", "src")], "edges": [{"from": "a"}]},
    {"nodes": [N("a", "src")], "edges": [E("a", "text", "zz", "text")]},
    {"nodes": [{"id": "a", "kind": "src", "params": []}]},
])
def test_格式不對的圖回錯誤而不是丟例外(rec, graph):
    errs = executor.validate(graph)
    assert errs and all(isinstance(e.message, str) and e.message for e in errs)


def test_pinned只能用在文字輸出且值必須是文字(rec):
    ok = G([{**N("a", "src"), "pinned": True, "outputs": {"text": "改過"}}])
    assert executor.validate(ok) == []
    bad_val = G([{**N("a", "src"), "pinned": True, "outputs": {"text": 1}}])
    assert len(executor.validate(bad_val)) == 1
    img = G([N("t", "text-input", text="p"),
             {**N("img", "generate-image"), "pinned": True, "outputs": {"image": "/etc/passwd"}}],
            [E("t", "text", "img", "prompt")])
    errs = executor.validate(img)
    assert len(errs) == 1 and errs[0].node_id == "img"


def test_錯誤可轉成dict(rec):
    e = executor.validate(G([N("v", "speak")]))[0]
    assert e.as_dict() == {"node_id": "v", "port": "text", "message": e.message}


# ---------- run ----------

def test_非法的圖不執行(rec):
    with pytest.raises(executor.GraphError) as ei:
        run_collect(G([N("v", "speak")]))
    assert ei.value.errors and rec.calls == []


def test_菱形圖依合法拓撲序執行(rec):
    g = G([N("d", "join", tag="d"), N("c", "pass", tag="c"), N("b", "pass", tag="b"), N("a", "src", tag="a")],
          [E("a", "text", "b", "text"), E("a", "text", "c", "text"),
           E("b", "text", "d", "a"), E("c", "text", "d", "b")])
    events = run_collect(g)
    order = rec.calls
    assert sorted(order) == ["a", "b", "c", "d"]
    assert order[0] == "a" and order[-1] == "d"
    assert rec.inputs["d"] == {"a": "b(text=a())", "b": "c(text=a())"}
    assert events[0]["type"] == "started" and events[-1] == {"type": "finished", "status": "ok"}
    st = node_status(events)
    assert all(st[k]["status"] == "done" for k in "abcd")
    assert st["d"]["outputs"] == {"text": "d(a=b(text=a())+b=c(text=a()))"}


def test_每個節點先running再done(rec):
    events = run_collect(G([N("a", "src")]))
    seq = [(e["node"], e["status"]) for e in events if e["type"] == "node"]
    assert seq == [("a", "running"), ("a", "done")]


def test_pinned節點不呼叫run且下游拿到編輯值(rec):
    g = G([N("a", "src", tag="a"), {**N("b", "pass", tag="b"), "pinned": True, "outputs": {"text": "我改的"}},
           N("c", "pass", tag="c")],
          [E("a", "text", "b", "text"), E("b", "text", "c", "text")])
    events = run_collect(g)
    assert "b" not in rec.calls
    assert rec.inputs["c"] == {"text": "我改的"}
    st = node_status(events)
    assert st["b"]["status"] == "skipped" and st["b"]["reason"] == "pinned"
    assert st["b"]["outputs"] == {"text": "我改的"}


def test_中間節點拋例外時下游skipped旁支照跑(rec):
    g = G([N("a", "src", tag="a"), N("x", "boom", tag="x"), N("y", "pass", tag="y"), N("z", "pass", tag="z"),
           N("s", "pass", tag="s")],
          [E("a", "text", "x", "text"), E("x", "text", "y", "text"), E("y", "text", "z", "text"),
           E("a", "text", "s", "text")])
    events = run_collect(g)
    st = node_status(events)
    assert st["x"]["status"] == "error" and "替身節點失敗" in st["x"]["message"]
    assert st["y"]["status"] == "skipped" and st["y"]["reason"] == "upstream"
    assert st["y"]["message"] == "略過:上游失敗"
    assert st["z"]["status"] == "skipped"
    assert st["s"]["status"] == "done"
    assert "y" not in rec.calls and "z" not in rec.calls and "s" in rec.calls
    assert events[-1] == {"type": "finished", "status": "error"}


def test_節點未產生宣告的輸出算失敗(rec, monkeypatch):
    spec = NodeSpec("empty", "空", {}, {"text": Port("text")}, {}, False, lambda i, p: {})
    monkeypatch.setitem(nodes.REGISTRY, "empty", spec)
    st = node_status(run_collect(G([N("e", "empty")])))
    assert st["e"]["status"] == "error"


def test_cancel後不再啟動新節點且進行中的節點跑完(rec):
    cancel = threading.Event()
    g = G([N("a", "src", tag="a"), N("b", "pass", tag="b"), N("c", "pass", tag="c")],
          [E("a", "text", "b", "text"), E("b", "text", "c", "text")])
    events: list[dict] = []

    def emit(ev):
        events.append(ev)
        if ev.get("node") == "a" and ev.get("status") == "running":
            cancel.set()  # a 進行中時取消

    executor.run(g, emit, cancel=cancel)
    assert rec.calls == ["a"]
    st = node_status(events)
    assert st["a"]["status"] == "done"
    assert st["b"]["status"] == "cancelled" and st["c"]["status"] == "cancelled"
    assert events[-1] == {"type": "finished", "status": "cancelled"}


def test_執行前已取消則一個節點都不跑(rec):
    cancel = threading.Event()
    cancel.set()
    events = run_collect(G([N("a", "src")]), cancel=cancel)
    assert rec.calls == []
    assert events[-1]["status"] == "cancelled"


def test_重型節點在heavy_lock內執行輕節點不取鎖(rec, lock_path):
    g = G([N("a", "src", tag="a"), N("h", "heavy-pass", tag="h")], [E("a", "text", "h", "text")])
    run_collect(g)
    assert rec.lock_held == {"h": True}
    assert not _lock_is_held()  # 執行完畢已釋放


def test_重型鎖被占用時推送waiting事件之後照常執行(rec, lock_path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    events: list[dict] = []
    waiting = threading.Event()

    def emit(ev):
        events.append(ev)
        if ev.get("status") == "waiting":
            waiting.set()

    t = threading.Thread(target=executor.run, args=(G([N("a", "src", tag="a"), N("h", "heavy-pass", tag="h")], [E("a", "text", "h", "text")]), emit))
    t.start()
    try:
        assert waiting.wait(5)
        assert rec.calls == ["a"]
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    t.join(10)
    assert not t.is_alive()
    statuses = [e["status"] for e in events if e["type"] == "node"]
    assert statuses.count("waiting") == 1
    assert statuses[-1] == "done" and rec.calls == ["a", "h"]


def test_等待重型鎖時可取消(rec, lock_path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    cancel = threading.Event()
    events: list[dict] = []

    def emit(ev):
        events.append(ev)
        if ev.get("status") == "waiting":
            cancel.set()

    try:
        start = time.monotonic()
        executor.run(G([N("a", "src", tag="a"), N("h", "heavy-pass", tag="h")], [E("a", "text", "h", "text")]), emit, cancel=cancel)
        assert time.monotonic() - start < 5
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert rec.calls == ["a"]
    assert node_status(events)["h"]["status"] == "cancelled"
    assert events[-1]["status"] == "cancelled"
