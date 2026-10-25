"""media.py 輸出檔不覆寫的測試(CHG-20261022-01 T2,依審議修正)。

外部程序(mflux、ffmpeg)一律以 monkeypatch 替身取代,不實際執行。
"""
from __future__ import annotations

from pathlib import Path

import pytest

import media
import taiwan_ai as tai


@pytest.fixture
def out(monkeypatch, tmp_path):
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
