"""media.py 輸出檔不覆寫的測試(CHG-20261022-01 T2,依審議修正);生圖讀設定(CHG-20261026-01 T2)。

外部程序(mflux、ffmpeg)一律以 monkeypatch 替身取代,不實際執行;離線不下載的負向驗證用替身 mflux
腳本與替身 huggingface_hub,不連網、不載入真模型。
"""
from __future__ import annotations

import copy
import os
import subprocess
import sys
from pathlib import Path

import pytest

import media
import nodes
import settings
import taiwan_ai as tai


@pytest.fixture
def cfg(monkeypatch):
    """以記憶體中的設定取代 settings.load(),每次呼叫回傳副本;`_calls` 記錄呼叫次數。"""
    data = copy.deepcopy(settings.DEFAULTS)
    calls = []

    def load():
        calls.append(1)
        snap = copy.deepcopy(data)
        snap.pop("_calls", None)
        return snap
    monkeypatch.setattr(settings, "load", load)
    data["_calls"] = calls
    return data


@pytest.fixture
def out(monkeypatch, tmp_path, cfg):
    d = tmp_path / "outputs"
    monkeypatch.setattr(tai, "OUT", d)
    monkeypatch.setattr(tai, "unload_llms", lambda: None)
    return d


def _fake_mflux(cmd, **kw):
    """模擬 mflux 的行為:輸出檔已存在時不覆寫,改存 `<名>_1.png`。"""
    p = Path(cmd[cmd.index("--output") + 1])
    if p.exists():
        p = p.with_name(p.stem + "_1" + p.suffix)
    p.write_bytes(b"PNG")


def test_generate_image寫到佔位路徑且不留暫存目錄(out, monkeypatch):
    monkeypatch.setattr(media.subprocess, "run", _fake_mflux)
    p = media.generate_image("a cat")
    assert p.parent == out and p.name.startswith("image-")
    assert p.read_bytes() == b"PNG"
    assert sorted(x.name for x in out.iterdir()) == [p.name]


def test_generate_image失敗時刪除佔位與暫存目錄(out, monkeypatch):
    def boom(cmd, **kw):
        raise media.subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(media.subprocess, "run", boom)
    with pytest.raises(media.subprocess.CalledProcessError):
        media.generate_image("a cat")
    assert list(out.iterdir()) == []


def test_generate_image不覆寫既有檔(out, monkeypatch):
    out.mkdir()
    old = out / "image-A.png"
    old.write_bytes(b"old")
    stamps = iter(["A", "B"])
    monkeypatch.setattr(tai, "stamp", lambda: next(stamps))
    monkeypatch.setattr(media.subprocess, "run", _fake_mflux)
    assert media.generate_image("x") == out / "image-B.png"
    assert old.read_bytes() == b"old"


def test_make_video影片與字幕共用佔位主檔名(out, monkeypatch, tmp_path):
    out.mkdir()
    (out / "podcast-A.srt").write_text("舊", encoding="utf-8")
    stamps = iter(["A", "B"])
    monkeypatch.setattr(tai, "stamp", lambda: next(stamps))
    monkeypatch.setattr(media, "wav_seconds", lambda p: 10.0)
    seen = []

    def fake_ffmpeg(cmd, **kw):
        seen.append(cmd)
        Path(cmd[-1]).write_bytes(b"MP4")
    monkeypatch.setattr(media.subprocess, "run", fake_ffmpeg)
    p = media.make_video(tmp_path / "c.png", tmp_path / "a.wav", "第一句。第二句。")
    assert p == out / "podcast-B.mp4" and p.read_bytes() == b"MP4"
    assert str(out / "podcast-B.srt") in seen[0]
    assert "第一句" in (out / "podcast-B.srt").read_text(encoding="utf-8")
    assert (out / "podcast-A.srt").read_text(encoding="utf-8") == "舊"
    assert not (out / "podcast-A.mp4").exists()


def test_make_video沒有文稿不留字幕佔位(out, monkeypatch, tmp_path):
    monkeypatch.setattr(media.subprocess, "run", lambda cmd, **kw: Path(cmd[-1]).write_bytes(b"MP4"))
    p = media.make_video(tmp_path / "c.png", tmp_path / "a.wav")
    assert sorted(x.name for x in out.iterdir()) == [p.name]


# ---- 生圖讀設定(CHG-20261026-01 T2) ----

# 本筆之前 media.generate_image() 的固定值(原文照抄,用來證明預設設定下 argv 逐元素不變)
OLD_MODEL = "mflux-community/flux-1-schnell-mflux-q4"


def _capture_mflux(monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append((cmd, kw))
        _fake_mflux(cmd, **kw)
    monkeypatch.setattr(media.subprocess, "run", fake_run)
    return seen


def _expected_argv(cmd, model, steps, width, height, prompt):
    return [str(tai.MFLUX), "--model", model, "--base-model", "schnell", "--steps", steps,
            "--width", str(width), "--height", str(height), "--prompt", prompt,
            "--output", cmd[-1]]


def test_舊生圖常數已移除():
    assert not hasattr(tai, "FLUX")


def test_generate_image預設設定時argv與本筆之前逐元素相同(out, monkeypatch):
    seen = _capture_mflux(monkeypatch)
    media.generate_image("a cat", 1360, 768)
    ((cmd, kw),) = seen
    assert cmd == _expected_argv(cmd, OLD_MODEL, "4", 1360, 768, "a cat")
    assert Path(cmd[-1]).name == "image.png" and Path(cmd[-1]).parent.parent == out
    assert kw["check"] is True and kw["stdout"] is subprocess.DEVNULL
    assert "shell" not in kw


def test_generate_image每次呼叫重讀設定(out, cfg, monkeypatch):
    seen = _capture_mflux(monkeypatch)
    media.generate_image("first")
    cfg["roles"]["image"]["steps"] = 6
    cfg["roles"]["image"]["model"] = "test/model"
    media.generate_image("second")
    first, second = seen[0][0], seen[1][0]
    assert first == _expected_argv(first, OLD_MODEL, "4", 1024, 1024, "first")
    assert second == _expected_argv(second, "test/model", "6", 1024, 1024, "second")
    assert second[second.index("--base-model") + 1] == "schnell"


def test_generate_image一次呼叫只讀一次設定(out, cfg, monkeypatch):
    _capture_mflux(monkeypatch)
    before = len(cfg["_calls"])
    media.generate_image("x")
    assert len(cfg["_calls"]) - before == 1


def test_generate_image子程序環境為目前環境加離線變數(out, monkeypatch):
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setenv("PODCAST_STUDIO_TEST_MARK", "保留")
    seen = _capture_mflux(monkeypatch)
    media.generate_image("x")
    env = seen[0][1]["env"]
    assert env["HF_HUB_OFFLINE"] == "1"
    assert {k: v for k, v in env.items() if k != "HF_HUB_OFFLINE"} == dict(os.environ)
    assert env["PODCAST_STUDIO_TEST_MARK"] == "保留"
    assert "HF_HUB_OFFLINE" not in os.environ  # 不改動本行程的環境


def test_generate_image已設離線變數為0時仍強制為1(out, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    seen = _capture_mflux(monkeypatch)
    media.generate_image("x")
    assert seen[0][1]["env"]["HF_HUB_OFFLINE"] == "1"
    assert os.environ["HF_HUB_OFFLINE"] == "0"


def test_generate_image節點參數仍只有寬高():
    assert set(nodes.REGISTRY["generate-image"].params) == {"width", "height"}


# ---- 沒有快取的模型離線失敗、不下載(確認閘 security 疑慮;替身 mflux 與 huggingface_hub,不連網) ----

FAKE_HUB = '''
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


class LocalEntryNotFoundError(Exception):
    pass


def snapshot_download(repo_id, **kw):
    """替身:快取存在就回路徑;HF_HUB_OFFLINE=1 且快取不存在 → 丟例外;否則記一筆「下載」。"""
    cache = HERE / "hf-cache" / repo_id.replace("/", "--")
    if cache.is_dir():
        return str(cache)
    if os.environ.get("HF_HUB_OFFLINE") == "1":
        raise LocalEntryNotFoundError("離線模式且快取不存在:" + repo_id)
    with open(HERE / "downloads.log", "a", encoding="utf-8") as f:
        f.write(repo_id + "\\n")
    cache.mkdir(parents=True)
    return str(cache)
'''

FAKE_MFLUX = '''
import argparse
from pathlib import Path

from huggingface_hub import snapshot_download

ap = argparse.ArgumentParser()
for a in ("--model", "--base-model", "--steps", "--width", "--height", "--prompt", "--output"):
    ap.add_argument(a, required=True)
args = ap.parse_args()
snapshot_download(args.model)
Path(args.output).write_bytes(b"PNG-fake")
'''


@pytest.fixture
def fake_mflux(monkeypatch, tmp_path):
    """在暫存目錄建立替身 mflux-generate:轉呼叫目前的直譯器執行同目錄的 mflux_main.py,
    它匯入同目錄的替身 huggingface_hub(遵守 HF_HUB_OFFLINE),依 --model 取「模型」。"""
    d = tmp_path / "mflux"
    (d / "huggingface_hub").mkdir(parents=True)
    (d / "huggingface_hub/__init__.py").write_text(FAKE_HUB, encoding="utf-8")
    (d / "mflux_main.py").write_text(FAKE_MFLUX, encoding="utf-8")
    exe = d / "mflux-generate"
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{d / "mflux_main.py"}" "$@"\n', encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.setattr(tai, "MFLUX", exe)
    return d


def _downloads(d):
    log = d / "downloads.log"
    return log.read_text(encoding="utf-8").split() if log.exists() else []


def test_替身mflux在未離線時會下載_確認替身有效(fake_mflux, tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "HF_HUB_OFFLINE"}
    png = tmp_path / "control.png"
    r = subprocess.run([str(tai.MFLUX), "--model", "nobody/not-cached-model", "--base-model", "schnell",
                        "--steps", "4", "--width", "64", "--height", "64", "--prompt", "x",
                        "--output", str(png)], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert _downloads(fake_mflux) == ["nobody/not-cached-model"]
    assert png.read_bytes() == b"PNG-fake"


def test_設定指向沒有快取的模型時生圖失敗_不下載_不留檔(fake_mflux, out, cfg):
    cfg["roles"]["image"]["model"] = "nobody/not-cached-model"
    with pytest.raises(subprocess.CalledProcessError):
        media.generate_image("a cat", 64, 64)
    assert _downloads(fake_mflux) == []  # 下載次數 0
    assert not (fake_mflux / "hf-cache").exists()
    assert list(out.glob("image-*.png")) == []  # reserve() 的佔位檔已刪除
    assert list(out.glob(".mflux-*")) == []  # 暫存目錄已清除
    assert list(out.iterdir()) == []


def test_快取存在的模型離線生圖成功(fake_mflux, out, cfg):
    cfg["roles"]["image"]["model"] = "local/cached-model"
    (fake_mflux / "hf-cache/local--cached-model").mkdir(parents=True)
    p = media.generate_image("a cat", 64, 64)
    assert p.read_bytes() == b"PNG-fake"
    assert sorted(x.name for x in out.iterdir()) == [p.name]
    assert _downloads(fake_mflux) == []


# ---------- DIR-5 修正 5:生圖暫存目錄建立後被換成符號連結 ----------

def test_generate_image暫存目錄建立後被換成連結_不搬入外部內容(out, tmp_path, monkeypatch):
    outside = tmp_path / "outside-mflux"
    outside.mkdir()
    real = media._mkdtemp_out

    def swapping(prefix):
        name, fd = real(prefix)
        d = out / name
        d.rename(d.with_name(name + "-moved"))
        d.symlink_to(outside, target_is_directory=True)
        return name, fd
    monkeypatch.setattr(media, "_mkdtemp_out", swapping)
    monkeypatch.setattr(media.subprocess, "run", _fake_mflux)   # 外部程式依路徑寫進連結目標(殘餘風險)
    with pytest.raises(OSError):
        media.generate_image("a cat")
    assert not [x for x in out.iterdir() if x.name.startswith("image-")]   # 外部內容沒有被搬進 outputs/
    # 暫存目錄的連結本身被刪(不跟隨),原目錄(以 fd 持有)仍在
    assert [x.name for x in out.iterdir() if x.name.startswith(".mflux-") and not x.name.endswith("-moved")] == []
