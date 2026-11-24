"""影片合成相關測試(CHG-20261026-01 T4、T5)。

T4:`media.wav_seconds()` 以 `struct` 自行解析 RIFF/WAVE。
T5:`media._run_ffmpeg()` 對 WAV 加 `-t <秒數>`、影格率 `-r 10`,以真的 ffmpeg(imageio-ffmpeg 內建)量軌長。

測試檔一律在暫存目錄以手寫位元組產生(PCM 一份以標準庫 `wave` 產生),不讀 repo 外的真音檔,CI 可跑。
"""
from __future__ import annotations

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
import taiwan_ai as tai

ROOT = Path(__file__).resolve().parents[1]
_HAN = re.compile(r"[一-鿿]")


def _chunk(cid: bytes, body: bytes) -> bytes:
    """組一個 RIFF chunk;奇數長度補一個 0 位元組(補位不算進長度欄位)。"""
    pad = b"\x00" if len(body) % 2 else b""
    return cid + struct.pack("<I", len(body)) + body + pad


def _fmt(fmt_tag, channels, rate, bits, ext_sub=None):
    align = channels * (bits // 8)
    body = struct.pack("<HHIIHH", fmt_tag, channels, rate, rate * align, align, bits)
    if ext_sub is not None:
        # cbSize=22、wValidBitsPerSample、dwChannelMask、SubFormat GUID(前 2 位元組為編碼)
        guid = struct.pack("<H", ext_sub) + bytes.fromhex("000000001000800000aa00389b71")
        body += struct.pack("<HHI", 22, bits, 0) + guid
    return body


def _riff(*chunks: bytes, form: bytes = b"WAVE") -> bytes:
    payload = form + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(payload)) + payload


def _write(tmp_path, name, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def _float_wav(tmp_path, seconds, rate, channels=1, fmt_tag=3, ext_sub=None, before=b"", name="f.wav"):
    frames = round(seconds * rate)
    data = b"\x00" * (frames * channels * 4)
    return _write(tmp_path, name, _riff(before, _chunk(b"fmt ", _fmt(fmt_tag, channels, rate, 32, ext_sub)),
                                        _chunk(b"data", data)))


def _assert_bad(path):
    with pytest.raises(ValueError) as ei:
        media.wav_seconds(path)
    msg = str(ei.value)
    assert _HAN.search(msg), f"錯誤訊息要有繁中:{msg!r}"
    assert str(path) not in msg and path.name not in msg and str(path.parent) not in msg


# ── 支援的編碼 ────────────────────────────────────────────

def test_pcm16_mono_44100_via_stdlib_wave(tmp_path):
    p = tmp_path / "pcm.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x00\x00" * int(2.5 * 44100))
    assert abs(media.wav_seconds(p) - 2.5) <= 0.001


def test_float32_mono_22050_format3(tmp_path):
    p = _float_wav(tmp_path, 3.2, 22050)
    assert abs(media.wav_seconds(p) - 3.2) <= 0.001


def test_extensible_float_stereo_48000(tmp_path):
    p = _float_wav(tmp_path, 1.75, 48000, channels=2, fmt_tag=0xFFFE, ext_sub=3)
    assert abs(media.wav_seconds(p) - 1.75) <= 0.001


def test_list_chunk_with_odd_length_before_fmt(tmp_path):
    lst = _chunk(b"LIST", b"INFOabc")  # 7 位元組,奇數,含補位
    p = _float_wav(tmp_path, 3.2, 22050, before=lst)
    assert abs(media.wav_seconds(p) - 3.2) <= 0.001


def test_accepts_str_path(tmp_path):
    p = _float_wav(tmp_path, 1.0, 22050)
    assert abs(media.wav_seconds(str(p)) - 1.0) <= 0.001


# ── 壞檔與不支援的編碼 ────────────────────────────────────

def test_not_riff(tmp_path):
    _assert_bad(_write(tmp_path, "x.wav", b"ID3\x04" + b"\x00" * 60))


def test_riff_but_not_wave(tmp_path):
    _assert_bad(_write(tmp_path, "x.wav", _riff(_chunk(b"fmt ", _fmt(1, 1, 8000, 16)), form=b"AVI ")))


def test_empty_file(tmp_path):
    _assert_bad(_write(tmp_path, "x.wav", b""))


def test_missing_data(tmp_path):
    _assert_bad(_write(tmp_path, "x.wav", _riff(_chunk(b"fmt ", _fmt(1, 1, 8000, 16)))))


def test_missing_fmt(tmp_path):
    _assert_bad(_write(tmp_path, "x.wav", _riff(_chunk(b"data", b"\x00" * 16))))


def test_format_85_mp3_in_wav(tmp_path):
    _assert_bad(_write(tmp_path, "x.wav", _riff(_chunk(b"fmt ", _fmt(85, 1, 8000, 16)),
                                               _chunk(b"data", b"\x00" * 16))))


def test_extensible_with_unsupported_subformat(tmp_path):
    _assert_bad(_float_wav(tmp_path, 0.1, 8000, fmt_tag=0xFFFE, ext_sub=85))


def test_sample_rate_zero(tmp_path):
    _assert_bad(_write(tmp_path, "x.wav", _riff(_chunk(b"fmt ", _fmt(1, 1, 0, 16)),
                                               _chunk(b"data", b"\x00" * 16))))


def test_channels_zero(tmp_path):
    _assert_bad(_write(tmp_path, "x.wav", _riff(_chunk(b"fmt ", _fmt(1, 0, 8000, 16)),
                                               _chunk(b"data", b"\x00" * 16))))


def test_bits_per_sample_zero(tmp_path):
    _assert_bad(_write(tmp_path, "x.wav", _riff(_chunk(b"fmt ", _fmt(1, 1, 8000, 0)),
                                               _chunk(b"data", b"\x00" * 16))))


def test_data_not_whole_frames(tmp_path):
    # 16 位元雙聲道:每個樣本框 4 位元組,data 6 位元組不是整數個樣本框
    _assert_bad(_write(tmp_path, "x.wav", _riff(_chunk(b"fmt ", _fmt(1, 2, 8000, 16)),
                                               _chunk(b"data", b"\x00" * 6))))


def test_chunk_length_beyond_eof(tmp_path):
    fmt = _chunk(b"fmt ", _fmt(1, 1, 8000, 16))
    bogus = b"data" + struct.pack("<I", 1_000_000) + b"\x00" * 16
    _assert_bad(_write(tmp_path, "x.wav", _riff(fmt) + bogus))


def test_fmt_chunk_too_short(tmp_path):
    _assert_bad(_write(tmp_path, "x.wav", _riff(_chunk(b"fmt ", b"\x01\x00\x01\x00"),
                                               _chunk(b"data", b"\x00" * 16))))


def test_chunk_loop_cap(tmp_path):
    # data 之前塞入超過上限的極小 chunk,必須丟錯而不是一直讀下去
    junk = b"".join(_chunk(b"junk", b"") for _ in range(media.WAV_MAX_CHUNKS + 10))
    _assert_bad(_write(tmp_path, "x.wav", _riff(_chunk(b"fmt ", _fmt(1, 1, 8000, 16)), junk,
                                               _chunk(b"data", b"\x00" * 16))))


def test_media_does_not_import_wave():
    src = (ROOT / "media.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*import wave\b", src, re.M)
    assert "import wave" not in src


# ── 依 PR #56 審議修正:讀檔錯誤與補位 ─────────────────────

def _assert_unreadable(path):
    with pytest.raises(ValueError) as ei:
        media.wav_seconds(path)
    msg = str(ei.value)
    assert _HAN.search(msg), f"錯誤訊息要有繁中:{msg!r}"
    assert path.name not in msg and str(path.parent) not in msg
    assert ei.value.__cause__ is None and ei.value.__suppress_context__


def test_missing_file(tmp_path):
    _assert_unreadable(tmp_path / "不存在.wav")


def test_path_is_directory(tmp_path):
    d = tmp_path / "目錄.wav"
    d.mkdir()
    _assert_unreadable(d)


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root 不受檔案權限限制")
def test_no_read_permission(tmp_path):
    p = _float_wav(tmp_path, 0.5, 8000, name="鎖住.wav")
    p.chmod(0)
    try:
        _assert_unreadable(p)
    finally:
        p.chmod(0o600)


def test_odd_chunk_missing_pad_byte_before_fmt(tmp_path):
    # 奇數長度的 LIST 區塊剛好在檔尾,缺少補位位元組
    p = _write(tmp_path, "x.wav", _riff() + b"LIST" + struct.pack("<I", 3) + b"abc")
    _assert_bad(p)
    with pytest.raises(ValueError, match="超出檔尾"):
        media.wav_seconds(p)


def test_odd_data_chunk_at_eof_without_pad(tmp_path):
    # 8 位元單聲道、data 3 位元組且為最後一個區塊、缺少補位:與其他區塊同樣判為壞檔
    fmt = _chunk(b"fmt ", _fmt(1, 1, 8000, 8))
    p = _write(tmp_path, "x.wav", _riff(fmt) + b"data" + struct.pack("<I", 3) + b"\x80\x80\x80")
    _assert_bad(p)
    with pytest.raises(ValueError, match="超出檔尾"):
        media.wav_seconds(p)


def test_odd_data_chunk_with_pad(tmp_path):
    fmt = _chunk(b"fmt ", _fmt(1, 1, 8000, 8))
    p = _write(tmp_path, "x.wav", _riff(fmt, _chunk(b"data", b"\x80" * 8001)))
    assert abs(media.wav_seconds(p) - 8001 / 8000) <= 0.001


# ── T5:影片長度等於聲音長度(真的 ffmpeg,imageio-ffmpeg 內建) ─────────
#
# 17.3 秒的 float WAV:3 秒音檔重現不出「影片比聲音長」的缺陷(見 CHG-20261026-01 決策表),不得改用短音檔。

LONG_SECONDS = 17.3
SCRIPT3 = "夜市的燈亮了。攤販開始吆喝。我們一起去吃點東西吧。"
_TIME = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")
_SRT_END = re.compile(r"--> (\d+):(\d+):(\d+),(\d+)")


@pytest.fixture
def out(tmp_path, monkeypatch):
    d = tmp_path / "outputs"
    monkeypatch.setattr(tai, "OUT", d)
    return d


@pytest.fixture
def media_in(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    img = src / "cover.png"
    Image.new("RGB", (1360, 768), (40, 90, 160)).save(img)
    wav = _float_wav(src, LONG_SECONDS, 22050, name="speech.wav")
    return img, wav


def _track_seconds(mp4, sel):
    """以 `ffmpeg -i <mp4> -map <sel> -f null -` 解碼,取最後一個 time= 為該軌長度。"""
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(mp4), "-map", sel,
                        "-f", "null", "-"], capture_output=True, text=True, check=True)
    h, m, s = _TIME.findall(r.stderr)[-1]
    return int(h) * 3600 + int(m) * 60 + float(s)


def _probe(mp4):
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(mp4)],
                       capture_output=True, text=True)
    return r.stderr


def test_make_video不帶字幕_影片軌與音軌長度差在0點1秒內(out, media_in):
    img, wav = media_in
    p = media.make_video(img, wav)
    v, a = _track_seconds(p, "0:v"), _track_seconds(p, "0:a")
    assert abs(v - a) <= 0.1, f"影片軌 {v} 秒、音軌 {a} 秒"
    assert abs(a - LONG_SECONDS) <= 0.1


def test_make_video帶字幕_長度對齊且含一條mov_text字幕軌(out, media_in):
    img, wav = media_in
    p = media.make_video(img, wav, SCRIPT3)
    v, a = _track_seconds(p, "0:v"), _track_seconds(p, "0:a")
    assert abs(v - a) <= 0.1, f"影片軌 {v} 秒、音軌 {a} 秒"
    info = _probe(p)
    assert len(re.findall(r"Stream #\S+.*Subtitle: mov_text", info)) == 1, info
    assert len(re.findall(r"Stream #\S+.*Subtitle:", info)) == 1, info
    srt = p.with_suffix(".srt").read_text(encoding="utf-8")
    h, m, s, ms = _SRT_END.findall(srt)[-1]
    end = int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    assert abs(end - LONG_SECONDS) <= 0.5


def _capture_ffmpeg(monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        Path(cmd[-1]).write_bytes(b"MP4")
    monkeypatch.setattr(media.subprocess, "run", fake_run)
    return seen


def test_wav時argv含t與r10且保留shortest(out, media_in, monkeypatch):
    img, wav = media_in
    seen = _capture_ffmpeg(monkeypatch)
    p = media.make_video(img, wav)
    cmd = seen[0]
    i = cmd.index("-t")
    assert cmd[i + 1] == "17.300"
    assert cmd[-1] == str(p) and i == len(cmd) - 3  # -t <秒數> 緊接在輸出檔名前
    assert cmd[cmd.index("-r") + 1] == "10" and cmd.count("-r") == 1
    assert "-shortest" in cmd and "2" not in cmd


def test_wav帶字幕時argv同樣含t與r10(out, media_in, monkeypatch):
    img, wav = media_in
    seen = _capture_ffmpeg(monkeypatch)
    media.make_video(img, wav, SCRIPT3)
    cmd = seen[0]
    assert cmd[cmd.index("-t") + 1] == "17.300"
    assert cmd[cmd.index("-r") + 1] == "10" and "-shortest" in cmd
    assert cmd[cmd.index("-c:s") + 1] == "mov_text"


def test_非riff的mp3不帶字幕時不加t_仍為r10(out, tmp_path, monkeypatch):
    mp3 = _write(tmp_path, "a.mp3", b"ID3\x04" + b"\x00" * 60)
    seen = _capture_ffmpeg(monkeypatch)
    media.make_video(tmp_path / "c.png", mp3)
    cmd = seen[0]
    assert "-t" not in cmd
    assert cmd[cmd.index("-r") + 1] == "10" and "-shortest" in cmd


def test_以檔頭判斷wav_不看副檔名(out, tmp_path, monkeypatch):
    # 內容是 RIFF/WAVE、副檔名是 .mp3 → 仍加 -t;內容不是 RIFF、副檔名是 .wav → 不加
    disguised = _float_wav(tmp_path, 2.0, 8000, name="其實是wav.mp3")
    fake_wav = _write(tmp_path, "其實不是.wav", b"ID3\x04" + b"\x00" * 60)
    seen = _capture_ffmpeg(monkeypatch)
    media.make_video(tmp_path / "c.png", disguised)
    media.make_video(tmp_path / "c.png", fake_wav)
    assert seen[0][seen[0].index("-t") + 1] == "2.000"
    assert "-t" not in seen[1]


def test_其餘ffmpeg參數與順序不變(out, media_in, monkeypatch):
    img, wav = media_in
    seen = _capture_ffmpeg(monkeypatch)
    p = media.make_video(img, wav)
    vf = (f"scale={media.VIDEO_W}:{media.VIDEO_H}:force_original_aspect_ratio=decrease,"
          f"pad={media.VIDEO_W}:{media.VIDEO_H}:(ow-iw)/2:(oh-ih)/2")
    assert seen[0] == [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loop", "1", "-i", str(img), "-i", str(wav),
                       "-vf", vf, "-c:v", "libx264", "-tune", "stillimage", "-r", "10", "-pix_fmt", "yuv420p",
                       "-c:a", "aac", "-b:a", "192k", "-shortest", "-t", "17.300", str(p)]
