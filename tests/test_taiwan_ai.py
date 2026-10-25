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


# ---- reserve:輸出檔排他建立,不覆寫 ----

@pytest.fixture
def out(monkeypatch, tmp_path):
    d = tmp_path / "outputs"
    monkeypatch.setattr(tai, "OUT", d)
    return d


def _fixed_stamps(monkeypatch, values):
    it = iter(values)
    monkeypatch.setattr(tai, "stamp", lambda: next(it))


def test_reserve建立空佔位檔(out):
    p = tai.reserve("speech", ".wav")
    assert p.parent == out and p.name.startswith("speech-") and p.suffix == ".wav"
    assert p.is_file() and not p.is_symlink() and p.stat().st_size == 0


def test_reserve不覆寫同名檔並回傳另一個名稱(out, monkeypatch):
    out.mkdir()
    old = out / "image-A.png"
    old.write_bytes(b"old")
    _fixed_stamps(monkeypatch, ["A", "B"])
    p = tai.reserve("image", ".png")
    assert p == out / "image-B.png"
    assert old.read_bytes() == b"old"


def test_reserve前兩次撞名第三次成功(out, monkeypatch):
    out.mkdir()
    for s in ("A", "B"):
        (out / f"cover-{s}.png").write_bytes(s.encode())
    calls = []

    def fake():
        calls.append(1)
        return "ABC"[len(calls) - 1]
    monkeypatch.setattr(tai, "stamp", fake)
    assert tai.reserve("cover", ".png") == out / "cover-C.png"
    assert len(calls) == 3
    assert (out / "cover-A.png").read_bytes() == b"A"
    assert (out / "cover-B.png").read_bytes() == b"B"


def test_reserve一律撞名丟例外(out, monkeypatch):
    out.mkdir()
    (out / "speech-X.wav").write_bytes(b"x")
    monkeypatch.setattr(tai, "stamp", lambda: "X")
    with pytest.raises(FileExistsError):
        tai.reserve("speech", ".wav")
    assert (out / "speech-X.wav").read_bytes() == b"x"


def test_reserve不跟隨符號連結(out, monkeypatch, tmp_path):
    out.mkdir()
    target = tmp_path / "target.png"
    target.write_bytes(b"secret")
    dangling = tmp_path / "nowhere.png"
    (out / "image-A.png").symlink_to(target)
    (out / "image-B.png").symlink_to(dangling)
    _fixed_stamps(monkeypatch, ["A", "B", "C"])
    p = tai.reserve("image", ".png")
    assert p == out / "image-C.png" and not p.is_symlink()
    assert target.read_bytes() == b"secret"
    assert not dangling.exists()


def test_reserve_also一起佔位_撞名時清掉本輪佔位(out, monkeypatch):
    out.mkdir()
    (out / "podcast-A.srt").write_text("舊字幕", encoding="utf-8")
    _fixed_stamps(monkeypatch, ["A", "B"])
    p = tai.reserve("podcast", ".mp4", also=(".srt",))
    assert p == out / "podcast-B.mp4"
    assert (out / "podcast-B.srt").is_file()
    assert not (out / "podcast-A.mp4").exists()
    assert (out / "podcast-A.srt").read_text(encoding="utf-8") == "舊字幕"


def test_speak寫入佔位檔_失敗時刪除佔位(out, monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        path = Path(cmd[cmd.index("--output_path") + 1])
        seen.append(path)
        assert path.is_file()  # 已先佔位
        path.write_bytes(b"RIFF")
    monkeypatch.setattr(tai.subprocess, "run", fake_run)
    p = tai.speak("你好")
    assert p == seen[0] and p.read_bytes() == b"RIFF"

    def boom(cmd, **kw):
        seen.append(Path(cmd[cmd.index("--output_path") + 1]))
        raise tai.subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(tai.subprocess, "run", boom)
    with pytest.raises(tai.subprocess.CalledProcessError):
        tai.speak("你好")
    assert not seen[-1].exists()


# ---- 設定不合法時回退預設值 ----

@pytest.fixture
def cfg_file(monkeypatch, tmp_path):
    root = tmp_path.resolve()
    monkeypatch.setattr(settings, "ROOT", root)
    monkeypatch.setattr(settings, "CONFIG_PATH", root / "config" / "models.json")
    (root / "config").mkdir()
    return settings.CONFIG_PATH


def test_壞JSON回退預設模型(cfg_file, ollama, capsys):
    cfg_file.write_text("{壞掉", encoding="utf-8")
    assert tai.model("reason") == LIGHT
    assert settings.WARN_BAD_JSON in capsys.readouterr().err
    tai.chat("reason", "問題")
    assert ollama.bodies("/api/chat")[-1]["model"] == LIGHT


def test_結構不合法回退預設模型(cfg_file, ollama, capsys):
    bad = copy.deepcopy(settings.DEFAULTS)
    bad["roles"]["reason"]["model"] = ""
    cfg_file.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    assert tai.model("reason") == LIGHT
    assert settings.WARN_INVALID in capsys.readouterr().err
    tai.chat("reason", "問題")
    assert ollama.bodies("/api/chat")[-1]["model"] == LIGHT


# ---- speak 子程序離線環境(T4,ADP-12) ----

def test_speak子程序環境含離線變數_其餘不變(out, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        Path(cmd[cmd.index("--output_path") + 1]).write_bytes(b"RIFF")
    monkeypatch.setattr(tai.subprocess, "run", fake_run)
    p = tai.speak("測試")
    env = seen["kw"]["env"]
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["TRANSFORMERS_OFFLINE"] == "1"
    # 修訂前的兩個變數維持原值;ADP-9 的 HOME 不在本 task 處理
    assert env["PYTHONUTF8"] == "1"
    assert env["PATH"] == "/usr/bin:/bin"
    assert set(env) == {"PYTHONUTF8", "PATH", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"}
    # argv 與其他參數不變
    assert seen["cmd"] == [str(tai.BREEZY / ".venv/bin/python"), "single_inference.py",
                           "--content_to_synthesize", "測試",
                           "--speaker_prompt_audio_path", str(tai.VOICE_WAV),
                           "--speaker_prompt_text_transcription", tai.VOICE_TEXT,
                           "--output_path", str(p)]
    assert seen["kw"]["cwd"] == tai.BREEZY
    assert seen["kw"]["check"] is True
    assert seen["kw"]["stdout"] is tai.subprocess.DEVNULL
    assert seen["kw"]["stderr"] is tai.subprocess.DEVNULL
    assert p.read_bytes() == b"RIFF"


def test_speak失敗時提示不自動下載(out, monkeypatch, capsys):
    def boom(cmd, **kw):
        raise tai.subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(tai.subprocess, "run", boom)
    with pytest.raises(tai.subprocess.CalledProcessError):
        tai.speak("測試")
    assert "不連網下載" in capsys.readouterr().err
