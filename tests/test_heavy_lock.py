"""heavy_lock.py 與 local_models_mcp.py 改用跨行程鎖的測試(CHG-20261022-01 T4,ADP-2)。

每個測試把 heavy_lock.LOCK_PATH 指到暫存目錄,不碰 repo 內的 outputs/;
跨行程測試以子行程執行同一份 heavy_lock.py。MCP 測試以替身 `mcp` 套件載入
local_models_mcp.py(測試環境沒有安裝 mcp),模型呼叫一律以 monkeypatch 替身。
"""
from __future__ import annotations

import copy
import fcntl
import importlib
import io
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
import types
from pathlib import Path

import pytest

import heavy_lock as hl
import settings

REPO = Path(hl.__file__).resolve().parent


@pytest.fixture
def lock_path(tmp_path, monkeypatch):
    p = tmp_path / "outputs" / ".heavy.lock"
    monkeypatch.setattr(hl, "LOCK_PATH", p)
    return p


CHILD_PRELUDE = textwrap.dedent("""
    import json, sys, time
    from pathlib import Path
    sys.path.insert(0, {repo!r})
    import heavy_lock
    heavy_lock.LOCK_PATH = Path({lock!r})
    go = Path({go!r})
    deadline = time.time() + 30
    while not go.exists():
        if time.time() > deadline:
            sys.exit("等不到開始訊號")
        time.sleep(0.001)
""")

CHILD_HOLD_BRIEFLY = CHILD_PRELUDE + textwrap.dedent("""
    with heavy_lock.heavy_lock():
        start = time.time()
        time.sleep(0.3)
        end = time.time()
    print(json.dumps({{"start": start, "end": end}}))
""")

CHILD_HOLD_FOREVER = CHILD_PRELUDE + textwrap.dedent("""
    with heavy_lock.heavy_lock():
        Path({ready!r}).write_text("ok")
        time.sleep({hold})
""")


def _spawn(code: str, cwd: Path) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", code], cwd=cwd, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1"))


def _holder(tmp_path: Path, lock_path: Path, hold: float = 60) -> subprocess.Popen:
    """啟動一個持鎖子行程,等到它確實拿到鎖才回傳。"""
    ready, go = tmp_path / "ready", tmp_path / "go-holder"
    proc = _spawn(CHILD_HOLD_FOREVER.format(repo=str(REPO), lock=str(lock_path), go=str(go),
                                            ready=str(ready), hold=hold), tmp_path)
    go.write_text("go")
    deadline = time.monotonic() + 30
    while not ready.exists():
        if proc.poll() is not None:
            pytest.fail("持鎖子行程提早結束:" + proc.stderr.read())
        if time.monotonic() > deadline:
            proc.kill()
            pytest.fail("持鎖子行程 30 秒內沒有拿到鎖")
        time.sleep(0.01)
    return proc


# ---- 基本行為 ----

def test_鎖檔預設位於outputs():
    assert hl.LOCK_PATH == REPO / "outputs" / ".heavy.lock"


def test_使用LOCK_EX排他鎖(lock_path, monkeypatch):
    seen = []
    real = fcntl.flock

    def spy(fd, op):
        seen.append(op)
        return real(fd, op)
    monkeypatch.setattr(hl.fcntl, "flock", spy)
    with hl.heavy_lock():
        pass
    assert seen[0] & fcntl.LOCK_EX
    assert not seen[0] & fcntl.LOCK_SH
    assert seen[-1] == fcntl.LOCK_UN


def test_建立outputs目錄與鎖檔_離開後可再取得(lock_path):
    with hl.heavy_lock():
        assert lock_path.is_file()
    with hl.heavy_lock(timeout=0.5):
        pass


def test_例外時也釋放鎖(lock_path):
    with pytest.raises(RuntimeError):
        with hl.heavy_lock():
            raise RuntimeError("模擬重型工作失敗")
    with hl.heavy_lock(timeout=0.5):
        pass


def test_鎖檔是符號連結時不跟隨(lock_path, tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("不可動", encoding="utf-8")
    lock_path.parent.mkdir(parents=True)
    lock_path.symlink_to(target)
    with pytest.raises(OSError):
        with hl.heavy_lock():
            pass
    assert target.read_text(encoding="utf-8") == "不可動"


def test_同一行程不同執行緒也互斥(lock_path):
    inside = []
    overlap = []

    def work():
        with hl.heavy_lock():
            inside.append(1)
            if len(inside) > 1:
                overlap.append(1)
            time.sleep(0.05)
            inside.pop()
    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not overlap


# ---- 跨行程 ----

def test_兩個子行程同時取鎖_執行區間不重疊(lock_path, tmp_path):
    go = tmp_path / "go"
    code = CHILD_HOLD_BRIEFLY.format(repo=str(REPO), lock=str(lock_path), go=str(go))
    procs = [_spawn(code, tmp_path) for _ in range(2)]
    time.sleep(0.5)  # 讓兩個子行程都進入等待
    go.write_text("go")
    spans = []
    for p in procs:
        out, err = p.communicate(timeout=60)
        assert p.returncode == 0, err
        spans.append(json.loads(out))
    a, b = sorted(spans, key=lambda s: s["start"])
    assert a["end"] <= b["start"]


def test_持鎖行程被kill後2秒內取得鎖(lock_path, tmp_path):
    proc = _holder(tmp_path, lock_path)
    try:
        with pytest.raises(TimeoutError):
            with hl.heavy_lock(timeout=0.2):
                pass
    finally:
        proc.kill()
    t0 = time.monotonic()
    with hl.heavy_lock(timeout=5):
        elapsed = time.monotonic() - t0
    proc.wait(timeout=10)
    assert elapsed < 2


def test_等待時呼叫on_wait一次_取得後才進入(lock_path, tmp_path):
    proc = _holder(tmp_path, lock_path, hold=0.8)
    calls = []
    with hl.heavy_lock(timeout=30, on_wait=lambda: calls.append(time.monotonic())):
        entered = time.monotonic()
    proc.communicate(timeout=30)
    assert len(calls) == 1
    assert entered >= calls[0]


def test_沒有等待時不呼叫on_wait(lock_path):
    calls = []
    with hl.heavy_lock(on_wait=lambda: calls.append(1)):
        pass
    assert calls == []


def test_逾時丟TimeoutError(lock_path, tmp_path):
    proc = _holder(tmp_path, lock_path)
    try:
        t0 = time.monotonic()
        with pytest.raises(TimeoutError):
            with hl.heavy_lock(timeout=0.3):
                pass
        assert 0.25 <= time.monotonic() - t0 < 5
    finally:
        proc.kill()
        proc.wait(timeout=10)


# ---- local_models_mcp:busy() 改用跨行程鎖、每次工具呼叫重讀設定 ----

class _FakeMCPServer:
    def __init__(self, name):
        self.name = name

    def tool(self):
        return lambda f: f

    def run(self):
        raise AssertionError("測試不應啟動 MCP 伺服器")


@pytest.fixture
def mcp_mod(monkeypatch):
    """以替身 mcp 套件載入 local_models_mcp(測試環境沒有安裝 mcp)。"""
    pkg = types.ModuleType("mcp")
    server = types.ModuleType("mcp.server")
    mcpserver = types.ModuleType("mcp.server.mcpserver")
    mcpserver.MCPServer = _FakeMCPServer
    for name, mod in (("mcp", pkg), ("mcp.server", server), ("mcp.server.mcpserver", mcpserver)):
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.delitem(sys.modules, "local_models_mcp", raising=False)
    mod = importlib.import_module("local_models_mcp")
    yield mod
    sys.modules.pop("local_models_mcp", None)


@pytest.fixture
def cfg(monkeypatch):
    """以記憶體中的設定取代 settings.load();記錄呼叫次數。"""
    state = {"data": copy.deepcopy(settings.DEFAULTS), "calls": 0}

    def fake_load():
        state["calls"] += 1
        return copy.deepcopy(state["data"])
    monkeypatch.setattr(settings, "load", fake_load)
    return state


def test_MCP不再使用行程內鎖(mcp_mod):
    src = Path(mcp_mod.__file__).read_text(encoding="utf-8")
    assert "threading" not in src
    assert not hasattr(mcp_mod, "LOCK")
    assert mcp_mod.heavy_lock is hl.heavy_lock


def test_MCP重型工具等其他行程釋放鎖後才執行(mcp_mod, cfg, lock_path, tmp_path, monkeypatch, capsys):
    called = []
    monkeypatch.setattr(mcp_mod.tai, "chat", lambda role, prompt, **kw: called.append(role) or "結果")
    proc = _holder(tmp_path, lock_path)
    result = []
    t = threading.Thread(target=lambda: result.append(mcp_mod.reason("問題", heavy=True)))
    try:
        t.start()
        time.sleep(0.5)
        assert called == []  # 鎖被另一行程持有,尚未執行
    finally:
        proc.kill()
    t.join(timeout=10)
    proc.wait(timeout=10)
    assert called == ["reason_heavy"]
    assert result == ["結果"]
    assert "等待中" in capsys.readouterr().err


def test_MCP持鎖期間stdout導到stderr(mcp_mod, cfg, lock_path, monkeypatch, capsys):
    def noisy(text):
        print("佔位路徑")
        return Path("/tmp/x.wav")
    monkeypatch.setattr(mcp_mod.tai, "speak", noisy)
    assert mcp_mod.speak("你好") == "/tmp/x.wav"
    cap = capsys.readouterr()
    assert "佔位路徑" not in cap.out
    assert "佔位路徑" in cap.err


def test_MCP每次工具呼叫重讀設定(mcp_mod, cfg, lock_path, monkeypatch):
    monkeypatch.setattr(mcp_mod.tai, "chat", lambda *a, **k: "結果")
    before = cfg["calls"]
    mcp_mod.taiwanize("文字")
    mcp_mod.taiwanize("文字")
    assert cfg["calls"] - before >= 2


def _fake_ollama(have, loaded):
    def urlopen(url, timeout=None):
        if url.endswith("/api/tags"):
            return io.BytesIO(json.dumps({"models": [{"name": m} for m in have]}).encode())
        if url.endswith("/api/ps"):
            return io.BytesIO(json.dumps({"models": [{"name": m} for m in loaded]}).encode())
        raise AssertionError(url)
    return urlopen


def test_list_models每次重讀設定且輸出結構不變(mcp_mod, cfg, monkeypatch):
    monkeypatch.setattr(mcp_mod.urllib.request, "urlopen", _fake_ollama(["qwen3:8b"], ["qwen3:8b"]))
    first = json.loads(mcp_mod.list_models())
    assert list(first) == ["roles", "loaded_now", "outputs"]
    assert list(first["roles"]) == ["reason", "taiwan", "reason_heavy", "image", "speech"]
    assert first["roles"]["reason"] == {
        "model": settings.DEFAULTS["roles"]["reason"]["model"], "downloaded": False}
    assert "code" not in first["roles"]
    cfg["data"]["roles"]["reason"]["model"] = "qwen3:8b"
    second = json.loads(mcp_mod.list_models())
    assert second["roles"]["reason"] == {"model": "qwen3:8b", "downloaded": True}
    assert second["loaded_now"] == ["qwen3:8b"]
