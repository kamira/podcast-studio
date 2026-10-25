"""taiwan_ai.py 的測試(CHG-20261022-01 T2;ADP-1、ADP-8)。

一律以 monkeypatch 替身取代 Ollama(`urllib.request.urlopen`)與設定檔,不連任何網路。
"""
from __future__ import annotations

import ast
import copy
import io
import json
from pathlib import Path

import pytest

import settings
import taiwan_ai as tai

SRC = Path(tai.__file__).resolve()
HEAVY = settings.DEFAULTS["roles"]["reason_heavy"]["model"]
LIGHT = settings.DEFAULTS["roles"]["reason"]["model"]


class FakeOllama:
    """替身 Ollama:記錄每次請求;/api/ps 回 `loaded`,/api/chat 回固定內容。"""

    def __init__(self, loaded=(), reply="回答"):
        self.loaded = list(loaded)
        self.reply = reply
        self.calls: list[tuple[str, dict | None]] = []

    def __call__(self, req, timeout=None):
        if isinstance(req, str):
            url, body = req, None
        else:
            url = req.full_url
            body = json.loads(req.data.decode()) if req.data else None
        self.calls.append((url, body))
        if url.endswith("/api/ps"):
            data = {"models": [{"name": m, "model": m} for m in self.loaded]}
        elif url.endswith("/api/chat"):
            data = {"message": {"content": self.reply}}
        else:
            data = {}
        return io.BytesIO(json.dumps(data).encode())

    def bodies(self, suffix):
        return [b for u, b in self.calls if u.endswith(suffix)]


@pytest.fixture
def ollama(monkeypatch):
    fake = FakeOllama()
    monkeypatch.setattr(tai.urllib.request, "urlopen", fake)
    return fake


@pytest.fixture
def cfg(monkeypatch):
    """以記憶體中的設定取代 settings.load(),每次呼叫回傳副本。"""
    data = copy.deepcopy(settings.DEFAULTS)
    monkeypatch.setattr(settings, "load", lambda: copy.deepcopy(data))
    return data


# ---- 純函式庫:移除 CLI 與 code 角色 ----

def test_沒有CLI函式():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert not names & {"ask", "code", "image", "auto", "main"}


def test_沒有argparse與MODELS常數():
    src = SRC.read_text(encoding="utf-8")
    assert "argparse" not in src
    assert not hasattr(tai, "MODELS")
    assert not hasattr(tai, "HEAVY_REASON")
    assert "__main__" not in src


# ---- model(role) 讀設定 ----

def test_model讀設定(cfg):
    cfg["roles"]["reason"]["model"] = "qwen3:8b"
    assert tai.model("reason") == "qwen3:8b"
    assert tai.model("reason_heavy") == HEAVY


def test_model每次重讀設定(cfg):
    assert tai.model("taiwan") == settings.DEFAULTS["roles"]["taiwan"]["model"]
    cfg["roles"]["taiwan"]["model"] = "glm4:latest"
    assert tai.model("taiwan") == "glm4:latest"


def test_code角色已移除(cfg):
    with pytest.raises(KeyError):
        tai.model("code")


# ---- chat ----

def test_chat使用設定中的模型名(cfg, ollama):
    cfg["roles"]["reason"]["model"] = "qwen3:8b"
    out = tai.chat("reason", "問題", system="系統")
    assert out == "回答"
    (body,) = ollama.bodies("/api/chat")
    assert body["model"] == "qwen3:8b"
    assert body["messages"][0] == {"role": "system", "content": "系統"}
    assert body["messages"][-1] == {"role": "user", "content": "問題"}
    assert "think" not in body


def test_chat_heavy角色與num_ctx(cfg, ollama):
    cfg["roles"]["reason_heavy"]["num_ctx"] = 8192
    tai.chat("reason_heavy", "問題", think=False)
    (body,) = ollama.bodies("/api/chat")
    assert body["model"] == HEAVY
    assert body["options"]["num_ctx"] == 8192
    assert body["think"] is False


def test_chat去除think標籤(cfg, ollama):
    ollama.reply = "<think>想很久</think>  結論 "
    assert tai.chat("taiwan", "x") == "結論"


# ---- unload_llms(ADP-1) ----

def test_unload卸載ps列出的全部模型含heavy(cfg, ollama):
    ollama.loaded = [LIGHT, HEAVY]
    tai.unload_llms()
    bodies = ollama.bodies("/api/generate")
    assert sorted(b["model"] for b in bodies) == sorted([LIGHT, HEAVY])
    assert all(b["keep_alive"] == 0 for b in bodies)


def test_unload不在設定中的模型也卸載(cfg, ollama):
    ollama.loaded = ["qwen3:8b"]
    tai.unload_llms()
    assert [b["model"] for b in ollama.bodies("/api/generate")] == ["qwen3:8b"]


def test_unload沒有載入任何模型(cfg, ollama):
    tai.unload_llms()
    assert ollama.bodies("/api/generate") == []


def test_unload_Ollama連不上不丟例外(monkeypatch):
    def boom(*a, **k):
        raise OSError("連線被拒")
    monkeypatch.setattr(tai.urllib.request, "urlopen", boom)
    tai.unload_llms()


def test_unload單一模型失敗仍卸載其他(monkeypatch):
    fake = FakeOllama(loaded=["a:1", "b:2"])
    seen = []

    def flaky(req, timeout=None):
        if not isinstance(req, str) and req.full_url.endswith("/api/generate"):
            m = json.loads(req.data.decode())["model"]
            seen.append(m)
            if m == "a:1":
                raise OSError("逾時")
        return fake(req, timeout)
    monkeypatch.setattr(tai.urllib.request, "urlopen", flaky)
    tai.unload_llms()
    assert seen == ["a:1", "b:2"]


# ---- stamp(ADP-8) ----

def test_stamp同一毫秒連呼1000次無重複(monkeypatch, tmp_path):
    monkeypatch.setattr(tai, "OUT", tmp_path / "outputs")
    monkeypatch.setattr(tai.time, "time", lambda: 1_790_000_000.123)
    stamps = [tai.stamp() for _ in range(1000)]
    assert len(set(stamps)) == 1000
    assert all(s.startswith(stamps[0][:19]) for s in stamps)
    assert (tmp_path / "outputs").is_dir()


def test_stamp格式(monkeypatch, tmp_path):
    monkeypatch.setattr(tai, "OUT", tmp_path)
    monkeypatch.setattr(tai.time, "time", lambda: 1_790_000_000.042)
    s = tai.stamp()
    date, clock, ms, rnd = s.split("-")
    assert len(date) == 8 and date.isdigit()
    assert len(clock) == 6 and clock.isdigit()
    assert ms == "042"
    assert len(rnd) == 4 and rnd.isalnum() and rnd == rnd.lower()
