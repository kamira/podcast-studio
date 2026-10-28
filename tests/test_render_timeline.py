# -*- coding: utf-8 -*-
"""media.render_timeline 的測試(CHG-20261022-04 T5:匯出渲染)。

- 專案目錄(project.ROOT)與輸出目錄(taiwan_ai.OUT)一律指到暫存目錄,不碰 repo 內的 projects/、outputs/。
- 語音片段為測試內產生的 1 秒正弦波 WAV,畫面為兩張純色圖(紅、藍);實跑 imageio-ffmpeg 附帶的 ffmpeg。
- 「音訊總長 = 片段長度總和 + 停頓總和」與 T4 一致:停頓只算片段之間的停頓,最後一段之後的停頓不計入
  (layout.duration 不含片尾停頓)。
"""
from __future__ import annotations

import math
import os
import re
import struct
import subprocess
import wave
from pathlib import Path

import imageio_ffmpeg
import pytest
from PIL import Image

import media
import project as pj
import taiwan_ai as tai
import timeline

NAME = "匯出測試"
RATE = 22050
_HAN = re.compile(r"[一-鿿]")
LONG_TEXT = "今天我們來聊聊台灣夜市文化,從士林到逢甲再到六合,每一個夜市都有自己的味道。"  # > 24 字 → 2 個 cue


def _sine(path: Path, seconds=1.0, freq=440.0):
    n = round(seconds * RATE)
    frames = b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / RATE)))
                      for i in range(n))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames)


def _seg(sid, text, brk="sentence", pause=None, speed=None, audio=True):
    return {"id": sid, "text": text, "break": brk, "pause_after": pause, "speed": speed,
            "annotations": [], "audio": {"id": sid, "seconds": 1.0} if audio else None}


@pytest.fixture
def env(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    out = repo / "outputs"
    monkeypatch.setattr(pj, "ROOT", repo)
    monkeypatch.setattr(tai, "OUT", out)
    adir = pj.subdir(NAME, "audio", create=True)
    sdir = pj.subdir(NAME, "assets", create=True)
    ids = ["a0000001", "a0000002", "a0000003"]
    for i, sid in enumerate(ids):
        _sine(adir / f"{sid}.wav", 1.0, 330.0 + 110 * i)
    red, blue = "b0000001", "b0000002"
    Image.new("RGB", (640, 360), (255, 0, 0)).save(sdir / f"{red}.png")
    Image.new("RGB", (800, 600), (0, 0, 255)).save(sdir / f"{blue}.png")
    data = {
        "schema_version": 1, "name": NAME, "script": "",
        "defaults": {"speed": 1.0, "pause_comma": 0.0, "pause_sentence": 0.4, "pause_paragraph": 0.8},
        "segments": [
            _seg(ids[0], "大家好,歡迎收聽。", pause=0.5),
            _seg(ids[1], LONG_TEXT, speed=1.25, pause=0.3),     # 變速後 0.8 秒
            _seg(ids[2], "我們下次見。", brk="end", pause=0.7),  # 片尾停頓不計入總長
        ],
        "images": [{"id": "c0000001", "asset": red, "start": 0.0, "end": 2.0},
                   {"id": "c0000002", "asset": blue, "start": 2.0, "end": None}],
        "assets": [{"id": red, "kind": "image", "ext": "png", "bytes": (sdir / f"{red}.png").stat().st_size},
                   {"id": blue, "kind": "image", "ext": "png", "bytes": (sdir / f"{blue}.png").stat().st_size}],
    }
    proj = pj.Project.from_dict(data)

    class E:
        pass
    e = E()
    e.tmp, e.repo, e.out, e.adir, e.sdir, e.project = tmp_path, repo, out, adir, sdir, proj
    e.red, e.blue = red, blue
    return e


def _outputs(env):
    return sorted(p.name for p in env.out.glob("podcast-*")) if env.out.exists() else []


def _ffmpeg(*args, timeout=120):
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", *args],
                       capture_output=True, timeout=timeout)
    return r


def _track_seconds(mp4: Path, which: str) -> float:
    """以 `ffmpeg -i <mp4> -map 0:<v|a> -f null -` 解碼整軌,取最後一個 time= 為軌長。"""
    r = _ffmpeg("-i", str(mp4), "-map", f"0:{which}", "-f", "null", "-")
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-2000:]
    times = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr.decode("utf-8", "replace"))
    assert times, "ffmpeg 沒有輸出 time="
    h, m, s = times[-1]
    return int(h) * 3600 + int(m) * 60 + float(s)


def _frame_rgb(mp4: Path, at: float, tmp: Path):
    png = tmp / f"frame-{at:.2f}.png"
    r = _ffmpeg("-y", "-ss", f"{at:.3f}", "-i", str(mp4), "-frames:v", "1", str(png))
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-2000:]
    with Image.open(png) as im:
        assert im.size == (media.VIDEO_W, media.VIDEO_H)
        return im.convert("RGB").getpixel((media.VIDEO_W // 2, media.VIDEO_H // 2))


def _close(rgb, target, tol=40):
    return all(abs(a - b) <= tol for a, b in zip(rgb, target))


def _assert_message(err: Exception, env):
    msg = str(err)
    assert _HAN.search(msg), f"錯誤訊息要有繁中:{msg!r}"
    assert str(env.tmp) not in msg and "/private/" not in msg and "/Users/" not in msg, msg


# ── 實跑 ffmpeg ──────────────────────────────────────────

def test_實跑輸出尺寸音訊長度字幕與第二張圖起點(env, monkeypatch):
    calls = []
    real_run = subprocess.run

    def rec_run(cmd, *a, **k):
        calls.append(list(cmd))
        return real_run(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", rec_run)

    lay = timeline.layout(env.project)
    # 片段長度總和 + 片段之間的停頓總和(T4:片尾停頓不計)= 1.0 + 0.5 + 0.8 + 0.3 + 1.0
    seg_sum = sum(s["end"] - s["start"] for s in lay["segments"])
    gaps = sum(b["start"] - a["end"] for a, b in zip(lay["segments"], lay["segments"][1:]))
    expected = seg_sum + gaps
    assert abs(expected - 3.6) < 1e-6 and lay["duration"] == pytest.approx(3.6)
    assert len(lay["cues"]) == 4

    mp4, srt = media.render_timeline(env.project, lay)
    monkeypatch.setattr(subprocess, "run", real_run)

    assert mp4.parent == env.out and srt == mp4.with_suffix(".srt")
    assert mp4.name.startswith("podcast-") and mp4.stat().st_size > 0
    assert _outputs(env) == sorted([mp4.name, srt.name])

    # argv:最後一個 ffmpeg 呼叫(輸出 mp4)含 -r 10 與 -t <總長,小數 3 位>,且 -t 緊接在輸出檔名之前
    final = [c for c in calls if c and c[-1] == str(mp4)]
    assert len(final) == 1
    argv = final[0]
    assert all(isinstance(x, str) for x in argv)
    assert any(argv[i:i + 2] == ["-r", "10"] for i in range(len(argv)))
    assert argv[-3:-1] == ["-t", "3.600"]

    # 尺寸與軌道
    info = _ffmpeg("-i", str(mp4)).stderr.decode("utf-8", "replace")
    assert "1920x1080" in info
    assert re.search(r"Video: h264", info) and re.search(r"Audio: aac", info) and "mov_text" in info

    audio = _track_seconds(mp4, "a")
    video = _track_seconds(mp4, "v")
    print(f"\n[T5 實測] layout.duration={lay['duration']:.3f} 音軌={audio:.3f} 影片軌={video:.3f}")
    assert abs(audio - expected) <= 0.05
    assert abs(video - audio) <= 0.1

    # 字幕:SRT 檔與 mp4 內的字幕軌 cue 數都等於 layout
    text = srt.read_text(encoding="utf-8")
    assert text.count("-->") == len(lay["cues"])
    assert "00:00:00,000 --> 00:00:01,000" in text
    sub = _ffmpeg("-i", str(mp4), "-map", "0:s", "-f", "srt", "-")
    assert sub.returncode == 0
    assert sub.stdout.decode("utf-8").count("-->") == len(lay["cues"])

    # 第二張圖(藍)從 2.0 秒開始;之前是紅
    assert _close(_frame_rgb(mp4, 1.9, env.tmp), (255, 0, 0))
    assert _close(_frame_rgb(mp4, 2.0, env.tmp), (0, 0, 255))
    assert _close(_frame_rgb(mp4, 3.5, env.tmp), (0, 0, 255))


def test_圖片起訖取到0點1秒且沒有圖的時段為黑畫面(env):
    env.project.images = [{"id": "c0000002", "asset": env.blue, "start": 1.0, "end": None}]
    lay = timeline.layout(env.project)
    lay["images"][0]["start"] = 1.04  # 模擬未取整的起點:取到 1.0
    mp4, _ = media.render_timeline(env.project, lay)
    assert _close(_frame_rgb(mp4, 0.5, env.tmp), (0, 0, 0))
    assert _close(_frame_rgb(mp4, 1.0, env.tmp), (0, 0, 255))


# ── 拒絕渲染 ─────────────────────────────────────────────

def _reject(env, lay=None, match=None):
    lay = lay if lay is not None else timeline.layout(env.project)
    with pytest.raises(media.RenderError) as ei:
        media.render_timeline(env.project, lay)
    _assert_message(ei.value, env)
    if match:
        assert match in str(ei.value)
    assert _outputs(env) == [], "拒絕或失敗時不得留下佔位檔"
    return ei.value


def test_asset_id指向專案外路徑時拒絕(env):
    outside = env.tmp / "outside.png"
    Image.new("RGB", (64, 64), (0, 255, 0)).save(outside)
    lay = timeline.layout(env.project)
    for bad in ("../../outside", str(outside), "b0000001/../../x", "ffffffff"):
        lay["images"][0]["file"] = bad
        _reject(env, lay)


def test_素材檔是指向專案外的符號連結時拒絕(env):
    outside = env.tmp / "outside.png"
    Image.new("RGB", (64, 64), (0, 255, 0)).save(outside)
    target = env.sdir / f"{env.red}.png"
    target.unlink()
    os.symlink(outside, target)
    _reject(env)
    assert outside.exists()


def test_assets目錄本身是符號連結時拒絕(env):
    real = env.tmp / "elsewhere"
    env.sdir.rename(real)
    os.symlink(real, env.sdir)
    _reject(env)


def test_音檔是指向專案外的符號連結時拒絕(env):
    outside = env.tmp / "outside.wav"
    _sine(outside)
    target = env.adir / "a0000001.wav"
    target.unlink()
    os.symlink(outside, target)
    _reject(env)


def test_有片段沒有音檔時拒絕並列出片段(env):
    env.project.segments[1]["audio"] = None
    err = _reject(env)
    assert err.segments == ["a0000002"]


def test_layout與專案片段不一致時拒絕(env):
    lay = timeline.layout(env.project)
    lay["segments"] = lay["segments"][:2]
    _reject(env, lay)


def test_ffmpeg失敗時移除佔位檔且訊息不含路徑(env, monkeypatch):
    real_run = subprocess.run

    def failing(cmd, *a, **k):
        if cmd and str(cmd[-1]).endswith(".mp4"):
            raise subprocess.CalledProcessError(1, cmd, stderr=f"error {cmd[-1]}".encode())
        return real_run(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", failing)
    _reject(env)
    leftovers = [p.name for p in env.out.iterdir()] if env.out.exists() else []
    assert leftovers == [], leftovers


# ---------- DIR-5 統整 M-3:解碼只取需要的長度 ----------

def test_解碼以t截止_只取片段需要的長度(tmp_path):
    src = tmp_path / "long.wav"
    _sine(src, seconds=5.0)
    assert media._decode_argv(1.0)[-3:] == ["-t", "1.000", "pipe:1"]
    pcm = media._decode_pcm(src, 1.0)
    assert len(pcm) <= 1 * media.RENDER_RATE * 2 + 2           # ≤ 1 秒 × 48000 × 2 位元組(+1 個樣本)
    assert len(pcm) >= int(0.95 * media.RENDER_RATE * 2)
    assert len(media._decode_pcm(src)) >= int(4.9 * media.RENDER_RATE * 2)   # 不帶秒數時照舊解全檔
