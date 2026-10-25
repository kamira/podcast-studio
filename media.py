"""封面、字幕、影片合成。podcast_studio.py 與 local_models_mcp.py 共用。"""
import os
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import taiwan_ai as tai

FONT = "/System/Library/Fonts/STHeiti Medium.ttc"
VIDEO_W, VIDEO_H = 1920, 1080
FLUX_W, FLUX_H = 1360, 768  # FLUX 尺寸需為 16 的倍數,比例接近 16:9


def generate_image(prompt, width=1024, height=1024):
    tai.unload_llms()
    path = tai.reserve("image", ".png")
    # mflux 遇到已存在的輸出檔不覆寫,而是改存成 `<名>_1.png`;所以讓它寫進新建的私有暫存目錄,
    # 完成後再以 os.replace 換到先佔下的路徑(rename 取代佔位檔本身,不跟隨連結)。
    tmpdir = tempfile.mkdtemp(prefix=".mflux-", dir=tai.OUT)
    try:
        with tai.discard_on_error(path):
            tmp = Path(tmpdir) / "image.png"
            subprocess.run([str(tai.MFLUX), "--model", tai.FLUX, "--base-model", "schnell", "--steps", "4",
                            "--width", str(width), "--height", str(height), "--prompt", prompt,
                            "--output", str(tmp)], check=True, stdout=subprocess.DEVNULL)
            os.replace(tmp, path)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return path


def to_16x9(path):
    img = Image.open(path).convert("RGB")
    scale = max(VIDEO_W / img.width, VIDEO_H / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    left, top = (img.width - VIDEO_W) // 2, (img.height - VIDEO_H) // 2
    return img.crop((left, top, left + VIDEO_W, top + VIDEO_H))


def add_title(img, title):
    """FLUX 畫不出正確的中文字,標題用字型疊在底部。"""
    w, h = img.size
    top = int(h * 0.72)
    band = img.crop((0, top, w, h)).filter(ImageFilter.GaussianBlur(18))
    img.paste(Image.blend(band, Image.new("RGB", band.size), 0.55), (0, top))
    d = ImageDraw.Draw(img)
    size = 120
    while size > 48:
        font = ImageFont.truetype(FONT, size)
        lines = _wrap(title, font, w - 200, d)
        if len(lines) <= 2:
            break
        size -= 8
    y = top + (h - top - len(lines) * size * 1.25) / 2
    for line in lines:
        d.text(((w - d.textlength(line, font=font)) / 2, y), line, font=font,
               fill="white", stroke_width=3, stroke_fill="black")
        y += size * 1.25
    return img


def _wrap(text, font, max_w, d):
    lines, cur = [], ""
    for ch in text:
        if d.textlength(cur + ch, font=font) > max_w:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    return lines + [cur] if cur else lines


def make_cover(prompt, title):
    raw = generate_image(prompt, FLUX_W, FLUX_H)
    path = tai.reserve("cover", ".png")
    with tai.discard_on_error(path):
        add_title(to_16x9(raw), title).save(path)
    return path


def wav_seconds(path):
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def split_sentences(script, max_chars=24):
    parts = [p.strip() for p in re.split(r"(?<=[。！？!?；;\n])", script) if p.strip()]
    out = []
    for p in parts:
        while len(p) > max_chars:  # 太長再從逗號切
            cut = max((p.rfind(c, 0, max_chars + 1) for c in "，,、"), default=-1)
            cut = cut + 1 if cut > 0 else max_chars
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            out.append(p)
    return out


def make_srt(script, seconds, path):
    """沒有語音對齊模型,依字數比例分配時間;語速平均時誤差約在一兩秒內。"""
    lines = split_sentences(script)
    total = sum(len(s) for s in lines) or 1
    t, rows = 0.0, []
    for i, s in enumerate(lines, 1):
        end = t + seconds * len(s) / total
        rows.append(f"{i}\n{_ts(t)} --> {_ts(end)}\n{s.rstrip('。，,')}\n")
        t = end
    Path(path).write_text("\n".join(rows), encoding="utf-8")
    return path


def _ts(sec):
    ms = round(sec * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def make_video(image_path, audio_path, script=""):
    has_srt = bool(script.strip())
    # 影片與字幕共用主檔名,一起排他佔位;ffmpeg -y 與 make_srt 寫入的是佔下的檔
    path = tai.reserve("podcast", ".mp4", also=(".srt",) if has_srt else ())
    srt = path.with_suffix(".srt")
    with tai.discard_on_error(path, *([srt] if has_srt else [])):
        _run_ffmpeg(image_path, audio_path, script, path, srt if has_srt else None)
    return path


def _run_ffmpeg(image_path, audio_path, script, path, srt):
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loop", "1", "-i", str(image_path), "-i", str(audio_path)]
    if srt is not None:
        make_srt(script, wav_seconds(audio_path), srt)
        cmd += ["-i", str(srt), "-map", "0:v", "-map", "1:a", "-map", "2:s",
                "-c:s", "mov_text", "-metadata:s:s:0", "language=chi"]
    cmd += ["-vf", f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=decrease,"
                   f"pad={VIDEO_W}:{VIDEO_H}:(ow-iw)/2:(oh-ih)/2",
            "-c:v", "libx264", "-tune", "stillimage", "-r", "2", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest", str(path)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
