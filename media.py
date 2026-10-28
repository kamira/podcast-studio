"""封面、字幕、影片合成。podcast_studio.py 與 local_models_mcp.py 共用。"""
import os
import re
import contextlib
import struct
import subprocess
import tempfile
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import safe_fs
import taiwan_ai as tai

FONT = "/System/Library/Fonts/STHeiti Medium.ttc"
VIDEO_W, VIDEO_H = 1920, 1080
FLUX_W, FLUX_H = 1360, 768  # FLUX 尺寸需為 16 的倍數,比例接近 16:9


def generate_image(prompt, width=1024, height=1024):
    # 生圖模型與步數每次呼叫讀一次設定(roles.image;CHG-20261026-01 T2)
    conf = tai.role_config("image")
    # 沿用目前環境並加 HF_HUB_OFFLINE=1:本機沒有快取的模型直接失敗,不在執行期下載
    env = {**os.environ, "HF_HUB_OFFLINE": "1"}
    tai.unload_llms()
    path = tai.reserve("image", ".png")
    # mflux 遇到已存在的輸出檔不覆寫,而是改存成 `<名>_1.png`;所以讓它寫進新建的私有暫存目錄,
    # 完成後再以 os.replace 換到先佔下的路徑(rename 取代佔位檔本身,不跟隨連結)。
    tmpdir = tempfile.mkdtemp(prefix=".mflux-", dir=tai.OUT)
    try:
        with tai.discard_on_error(path):
            tmp = Path(tmpdir) / "image.png"
            subprocess.run([str(tai.MFLUX), "--model", conf["model"], "--base-model", "schnell",
                            "--steps", str(conf["steps"]),
                            "--width", str(width), "--height", str(height), "--prompt", prompt,
                            "--output", str(tmp)], check=True, stdout=subprocess.DEVNULL, env=env)
            # (DIR-5 修正 4)相對兩個目錄 fd 搬移:私有暫存目錄 → outputs/ 的佔位檔名
            with safe_fs.closing(safe_fs.open_dir(tai.OUT, [Path(tmpdir).name])) as src, \
                    safe_fs.closing(safe_fs.open_dir(tai.OUT)) as out:
                safe_fs.replace(src, tmp.name, out, Path(path).name)
    finally:
        _rmtree_in_out(tmpdir)
    return path


def _rmtree_in_out(path) -> None:
    """刪除 outputs/ 正下方的暫存目錄(DIR-5 修正 4:相對 outputs/ 的目錄 fd 以 safe_fs.rmtree 遞迴,不跟隨連結)。"""
    with contextlib.suppress(OSError, ValueError):
        with safe_fs.closing(safe_fs.open_dir(tai.OUT)) as out:
            safe_fs.rmtree(out, Path(path).name, missing_ok=True)


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


WAV_MAX_CHUNKS = 1024  # 解析 RIFF 時最多看幾個 chunk,防止惡意檔造成長時間迴圈
_WAV_FMT_MAX = 64  # fmt chunk 最多讀進記憶體的位元組數
_WAV_PCM, _WAV_FLOAT, _WAV_EXTENSIBLE = 1, 3, 0xFFFE


def _wav_read_chunks(path):
    """走訪 RIFF chunk,回傳(fmt 內容, data 位元組數);找不到的回 None。OSError 由呼叫端轉換。

    path 也可以是已開啟的 fd(DIR-5 修正 4:呼叫端以 O_NOFOLLOW 開啟後傳入;本函式不關閉它)。"""
    with open(path, "rb", closefd=not isinstance(path, int)) as f:
        size = f.seek(0, os.SEEK_END)
        f.seek(0)
        head = f.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise ValueError("音檔不是 WAV 格式")
        fmt = data_len = None
        pos = 12
        for _ in range(WAV_MAX_CHUNKS):
            if pos + 8 > size:
                break
            f.seek(pos)
            hdr = f.read(8)
            if len(hdr) < 8:
                raise ValueError("WAV 檔案損毀:區塊標頭不完整")
            cid, clen = struct.unpack("<4sI", hdr)
            body = pos + 8
            # 奇數長度的區塊後面有 1 位元組補位(含最後一個 data 區塊),補位也必須在檔案範圍內
            if clen + (clen & 1) > size - body:
                raise ValueError("WAV 檔案損毀:區塊長度超出檔尾")
            if cid == b"fmt ":
                if clen < 16:
                    raise ValueError("WAV 檔案損毀:fmt 區塊太短")
                fmt = f.read(min(clen, _WAV_FMT_MAX))
                if len(fmt) < 16:
                    raise ValueError("WAV 檔案損毀:fmt 區塊太短")
            elif cid == b"data":
                data_len = clen
            if fmt is not None and data_len is not None:
                break
            pos = body + clen + (clen & 1)
        else:
            raise ValueError("WAV 檔案區塊過多,無法解析")
    return fmt, data_len


def wav_seconds(path):
    """以標準庫 struct 解析 RIFF/WAVE 的 fmt 與 data chunk,回傳秒數(CHG-20261026-01 T4)。

    支援 PCM(1)、IEEE float(3)與 WAVE_FORMAT_EXTENSIBLE(0xFFFE,以 SubFormat 前 2 位元組判斷)。
    只讀 chunk 標頭與 fmt 內容,data 不讀進記憶體;壞檔或不支援的編碼丟 ValueError(訊息不含路徑)。
    """
    try:
        fmt, data_len = _wav_read_chunks(path)
    except OSError:
        # 系統錯誤訊息是英文且可能帶路徑;from None 斷開例外鏈,避免路徑經 __cause__ 洩漏
        raise ValueError("音檔無法讀取") from None
    if fmt is None:
        raise ValueError("WAV 檔案損毀:缺少 fmt 區塊")
    if data_len is None:
        raise ValueError("WAV 檔案損毀:缺少 data 區塊")
    tag, channels, rate, _, _, bits = struct.unpack("<HHIIHH", fmt[:16])
    if tag == _WAV_EXTENSIBLE:
        if len(fmt) < 26:
            raise ValueError("WAV 檔案損毀:EXTENSIBLE 格式缺少 SubFormat")
        tag = struct.unpack("<H", fmt[24:26])[0]
    if tag not in (_WAV_PCM, _WAV_FLOAT):
        raise ValueError(f"WAV 編碼不支援(format {tag})")
    if channels == 0 or rate == 0 or bits == 0:
        raise ValueError("WAV 檔案損毀:聲道數、取樣率或每樣本位元數為 0")
    frame = channels * ((bits + 7) // 8)
    if data_len % frame:
        raise ValueError("WAV 檔案損毀:音訊資料不是整數個樣本框")
    return data_len / frame / rate


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


def _is_wav(path):
    """以檔頭前 12 位元組判斷是否為 RIFF/WAVE(不看副檔名);讀不到檔案視為不是 WAV。"""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError:
        return False
    return len(head) == 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE"


def _run_ffmpeg(image_path, audio_path, script, path, srt):
    # `-loop 1` 的靜止圖加 `-shortest` 不會在聲音結束時截止(ACC-20261026-01:17 秒的聲音產出約 50 秒的影片),
    # 所以 WAV 另以 `-t <秒數>` 截止;影格率 10(間隔 0.1 秒)讓截止誤差不超過 0.1 秒(CHG-20261026-01 T5)。
    # 非 WAV(MP3 / M4A)讀不到長度,沿用只有 `-shortest` 的做法(ADP-7)。
    seconds = wav_seconds(audio_path) if _is_wav(audio_path) else None
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loop", "1", "-i", str(image_path), "-i", str(audio_path)]
    if srt is not None:
        make_srt(script, seconds if seconds is not None else wav_seconds(audio_path), srt)
        cmd += ["-i", str(srt), "-map", "0:v", "-map", "1:a", "-map", "2:s",
                "-c:s", "mov_text", "-metadata:s:s:0", "language=chi"]
    cmd += ["-vf", f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=decrease,"
                   f"pad={VIDEO_W}:{VIDEO_H}:(ow-iw)/2:(oh-ih)/2",
            "-c:v", "libx264", "-tune", "stillimage", "-r", "10", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest"]
    if seconds is not None:
        cmd += ["-t", f"{seconds:.3f}"]  # 本機計算的秒數,不是使用者字串
    cmd.append(str(path))
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------- 內容嗅探(CHG-20261022-04 DIR-5 統整 M-5:由 studio_server._sniff_binary 搬來,規則不變) ----------
#
# `/api/upload`(studio_server)與「送到素材庫」節點(nodes)共用;依檔頭位元組判斷內容是否與副檔名相符,
# 不信任副檔名或用戶端 Content-Type。

M4A_BRANDS = frozenset({b"M4A ", b"M4B ", b"M4P ", b"mp41", b"mp42", b"isom", b"iso2"})
SNIFF_BYTES = 16


def sniff_binary(ext: str, head: bytes) -> bool:
    """ext 含開頭的點(例 `.png`);head 為檔頭至少 SNIFF_BYTES 位元組(不足時視為不符)。"""
    if ext == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in (".jpg", ".jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if ext == ".wav":
        return head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    if ext == ".mp3":
        if head.startswith(b"ID3"):
            return True
        if len(head) < 2 or head[0] != 0xFF or head[1] & 0xE0 != 0xE0:
            return False
        version, layer = (head[1] >> 3) & 0x03, (head[1] >> 1) & 0x03
        return version != 0x01 and layer != 0x00   # 保留值不是合法 MPEG 音訊框
    if ext == ".m4a":
        return head[4:8] == b"ftyp" and head[8:12] in M4A_BRANDS
    return False


# ---------- 時間軸匯出(CHG-20261022-04 T5) ----------

RENDER_RATE = 48000      # 匯出音軌:單聲道 16 位元 PCM,48 kHz
RENDER_TIMEOUT = 600     # 渲染逾時下限(秒);實際 = max(600, 音訊秒數 × 2)
DECODE_TIMEOUT = 120     # 單一片段轉成 PCM 的逾時(秒)
RENDER_MAX_MS = 60 * 60 * 1000  # 與 project.MAX_AUDIO_SECONDS 相同:音訊總長上限 60 分鐘
_SILENCE_CHUNK = RENDER_RATE * 10  # 寫入靜音時每次最多寫幾個樣本


class RenderError(ValueError):
    """匯出被拒絕或失敗;訊息為繁中、不含本機絕對路徑,可直接給使用者看。

    segments:因尚未合成而被拒絕時,列出沒有音檔的片段 id(依片段順序)。
    """

    def __init__(self, message, segments=()):
        super().__init__(message)
        self.segments = list(segments)


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def _ms_of(seconds) -> int:
    """秒 → 整數毫秒(四捨五入);負數回 -1 交給呼叫端拒絕。"""
    return int(float(seconds) * 1000 + 0.5) if seconds >= 0 else -1


def _deci_of_ms(ms: int) -> int:
    """毫秒 → 以 0.1 秒為單位的整數(四捨五入,0.5 進位)。"""
    return (ms + 50) // 100


def _project_parts(path: Path) -> list[str] | None:
    """`projects/<名稱>/<sub>/<檔名>` 的路徑元件(相對 repo 根);不是這個形狀回 None。"""
    import project as pj
    try:
        rel = Path(path).relative_to(pj._top() / pj.PROJECTS_NAME)
    except ValueError:
        return None
    return [pj.PROJECTS_NAME, *rel.parts] if len(rel.parts) == 3 else None


def _open_project_file(path: Path) -> int:
    """(DIR-5 修正 4)自 repo 根以 dir_fd 逐層 O_NOFOLLOW 開啟專案內的一般檔;丟 safe_fs 的例外。"""
    import project as pj
    parts = _project_parts(path)
    if parts is None:
        raise safe_fs.UnsafePathError("不在專案目錄內")
    return safe_fs.open_path(pj._top(), parts)


def _contained_file(base: Path, path: Path) -> Path:
    """path 必須是 base(專案子目錄)正下方的一般檔案,任一層都不得是符號連結(以 dir_fd 逐層開啟確認)。"""
    if path.parent != base:
        raise RenderError("✗ 素材檔不是專案內的一般檔案(不接受符號連結)")
    try:
        os.close(_open_project_file(path))
    except FileNotFoundError:
        raise RenderError("✗ 找不到素材檔") from None
    except (OSError, ValueError):
        raise RenderError("✗ 素材檔不是專案內的一般檔案(不接受符號連結)") from None
    return path


def _open_nofollow(path: Path) -> int:
    """開啟專案內的素材或音檔供讀取:自 repo 根逐層 O_NOFOLLOW + fstat 確認一般檔(DIR-5 修正 4);
    之後只讀這個 fd(經 stdin 交給 ffmpeg 或由 PIL 讀取),不以路徑重開。"""
    try:
        return _open_project_file(path)
    except (OSError, ValueError):
        raise RenderError("✗ 素材檔無法讀取(不接受符號連結)") from None


def _check_layout(project, layout) -> tuple[int, list, list]:
    """檢查 layout 與專案一致;回傳(總長毫秒, 片段 [(id, 起, 訖)] 毫秒, cue [(起, 訖, 文字)] 毫秒)。"""
    bad = RenderError("✗ 時間軸資料與專案不一致,請重新整理後再匯出")
    if not isinstance(layout, dict) or not _finite(layout.get("duration")):
        raise bad
    total = _ms_of(layout["duration"])
    if total <= 0:
        raise RenderError("✗ 時間軸總長為 0,沒有可匯出的內容")
    if total > RENDER_MAX_MS:
        raise RenderError("✗ 專案音訊總長超過 60 分鐘")
    segs = layout.get("segments")
    if not isinstance(segs, list) or [s.get("id") if isinstance(s, dict) else None for s in segs] \
            != [s["id"] for s in project.segments]:
        raise bad
    spans, prev = [], 0
    for s in segs:
        if not (_finite(s.get("start")) and _finite(s.get("end"))):
            raise bad
        a, b = _ms_of(s["start"]), _ms_of(s["end"])
        if not (prev <= a <= b <= total):
            raise bad
        spans.append((s["id"], a, b))
        prev = b
    cues = layout.get("cues") or []
    if not isinstance(cues, list):
        raise bad
    out = []
    for c in cues:
        if not (isinstance(c, dict) and _finite(c.get("start")) and _finite(c.get("end"))
                and isinstance(c.get("text"), str)):
            raise bad
        a, b = _ms_of(c["start"]), _ms_of(c["end"])
        text = " ".join(c["text"].split())  # 換行與空白行會破壞 SRT 結構
        if not (0 <= a <= b <= total) or not text:
            raise bad
        out.append((a, b, text))
    return total, spans, out


def _resolve_images(project, layout, total: int) -> list:
    """畫面軌 → [(起, 訖)(0.1 秒為單位), 素材真實路徑];asset id 只在本專案 assets/ 內解析。"""
    import project as pj
    items = layout.get("images") or []
    if not isinstance(items, list):
        raise RenderError("✗ 畫面軌資料不正確")
    if not items:
        return []
    assets = {a.get("id"): a for a in project.assets if isinstance(a, dict)}
    try:
        adir = pj.subdir(project.name, "assets")
    except FileNotFoundError:
        raise RenderError("✗ 專案沒有素材目錄") from None
    except pj.ProjectError as e:
        raise RenderError(f"✗ 素材目錄無法使用:{e}") from None
    total_d = -(-total // 100)
    out = []
    for m in items:
        aid = m.get("file") if isinstance(m, dict) else None
        if not isinstance(aid, str) or not pj.ID_RE.fullmatch(aid):
            raise RenderError("✗ 畫面軌的素材 id 不合法")
        asset = assets.get(aid)
        if not asset or asset.get("kind") != "image" or asset.get("ext") not in pj.ASSET_EXTS["image"]:
            raise RenderError("✗ 畫面軌引用的不是本專案的圖片素材")
        if not (_finite(m.get("start")) and _finite(m.get("end"))):
            raise RenderError("✗ 畫面軌資料不正確")
        a_ms, b_ms = _ms_of(m["start"]), _ms_of(m["end"])
        if a_ms < 0 or b_ms < a_ms:
            raise RenderError("✗ 畫面軌資料不正確")
        start = _deci_of_ms(a_ms)
        end = total_d if b_ms >= total else _deci_of_ms(b_ms)  # 撐到總長的圖一路蓋到最後一格
        path = _contained_file(adir, adir / f"{aid}.{asset['ext']}")
        if start < min(end, total_d):
            out.append((start, min(end, total_d), path))
    return out


def _segment_audio(project) -> list:
    """每個片段的(變速後)音檔真實路徑;變速沿用 `tts_service.apply_speed`(與預覽同一份檔案)。"""
    import project as pj
    import tts_service  # 延後匯入:tts_service 匯入本模組
    try:
        adir = pj.subdir(project.name, "audio")
    except FileNotFoundError:
        raise RenderError("✗ 專案沒有音檔目錄") from None
    except pj.ProjectError as e:
        raise RenderError(f"✗ 音檔目錄無法使用:{e}") from None
    paths = []
    for seg in project.segments:
        try:
            p = Path(tts_service.apply_speed(seg, project))
        except tts_service.SynthError as e:
            raise RenderError(f"{e}(片段 {seg['id']})") from None
        except pj.ProjectError as e:
            raise RenderError(f"✗ 音檔目錄無法使用:{e}") from None
        paths.append(_contained_file(adir, p))
    return paths


def _decode_argv(seconds: float | None) -> list[str]:
    """解碼用的 ffmpeg argv;seconds 有值時在輸出前加 `-t <秒數,小數 3 位>`,只解出需要的長度。"""
    limit = ["-t", f"{seconds:.3f}"] if seconds is not None else []
    return [imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-v", "error", "-i", "pipe:0",
            "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(RENDER_RATE), *limit, "pipe:1"]


def _decode_pcm(path: Path, seconds: float | None = None) -> bytes:
    """以 ffmpeg 把音檔轉成 RENDER_RATE 單聲道 s16le;輸入以 O_NOFOLLOW 開啟後經 stdin 傳入,argv 不含路徑。

    seconds:該片段在 layout 中需要的秒數(DIR-5 統整 M-3);ffmpeg 以 `-t` 截止,不把整個音檔解進記憶體。"""
    fd = _open_nofollow(path)
    try:
        r = subprocess.run(_decode_argv(seconds),
                           stdin=fd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           check=True, timeout=DECODE_TIMEOUT)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        raise RenderError("✗ 語音片段無法解碼") from None
    finally:
        os.close(fd)
    return r.stdout


def _write_silence(w, samples: int) -> None:
    while samples > 0:
        n = min(samples, _SILENCE_CHUNK)
        w.writeframes(b"\x00\x00" * n)
        samples -= n


def _to_sample(ms: int) -> int:
    return (ms * RENDER_RATE + 500) // 1000


class _PcmWriter:
    """寫 16 位元單聲道 PCM WAV;總樣本數事先已知,檔頭一次寫好(以 struct 組 RIFF,不用 wave 模組)。"""

    def __init__(self, f, samples: int):
        self.f = f
        data = samples * 2
        f.write(b"RIFF" + struct.pack("<I", 36 + data) + b"WAVE"
                + b"fmt " + struct.pack("<IHHIIHH", 16, _WAV_PCM, 1, RENDER_RATE, RENDER_RATE * 2, 2, 16)
                + b"data" + struct.pack("<I", data))

    def writeframes(self, pcm: bytes) -> None:
        self.f.write(pcm)


def _build_audio(paths: list, spans: list, total: int, dest: Path) -> None:
    """依 layout 把各片段放到起點(片段之間補靜音),每段截或補到 layout 長度;總長 = layout.duration。"""
    with open(dest, "wb") as f:
        w = _PcmWriter(f, _to_sample(total))
        pos = 0
        for path, (_, a_ms, b_ms) in zip(paths, spans):
            a, b = _to_sample(a_ms), _to_sample(b_ms)
            _write_silence(w, a - pos)
            need = (b - a) * 2
            pcm = _decode_pcm(path, (b_ms - a_ms) / 1000)[:need]
            w.writeframes(pcm)
            _write_silence(w, (need - len(pcm)) // 2)
            pos = b
        _write_silence(w, _to_sample(total) - pos)


def _letterbox(path: Path, dest: Path) -> None:
    """圖片等比縮放進 1920×1080、黑邊置中(與 make_video 的 scale + pad 相同);以 O_NOFOLLOW 開啟。"""
    fd = _open_nofollow(path)
    try:
        with os.fdopen(fd, "rb") as f, Image.open(f) as im:
            img = im.convert("RGB")
    except Exception:
        raise RenderError("✗ 圖片素材無法讀取") from None
    scale = min(VIDEO_W / img.width, VIDEO_H / img.height)
    img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    canvas = Image.new("RGB", (VIDEO_W, VIDEO_H))
    canvas.paste(img, ((VIDEO_W - img.width) // 2, (VIDEO_H - img.height) // 2))
    canvas.save(dest)


def _build_video_list(images: list, total: int, work: Path) -> Path:
    """畫面軌 → concat demuxer 清單(0.1 秒一格;後開始的圖蓋過先前的圖,沒有圖的時段為黑畫面)。"""
    total_d = -(-total // 100)
    slots = [None] * total_d
    for start, end, path in sorted(images, key=lambda x: x[0]):
        slots[start:end] = [path] * (end - start)
    names_of, runs = {}, []
    for p in slots:
        if p not in names_of:
            name = f"v{len(names_of):03d}.png"
            names_of[p] = name
            if p is None:
                Image.new("RGB", (VIDEO_W, VIDEO_H)).save(work / name)
            else:
                _letterbox(p, work / name)
        if runs and runs[-1][0] == names_of[p]:
            runs[-1][1] += 1
        else:
            runs.append([names_of[p], 1])
    lines = ["ffconcat version 1.0"]
    for name, n in runs:
        lines += [f"file {name}", f"duration {n / 10:.1f}"]
    lines.append(f"file {runs[-1][0]}")  # concat demuxer 會忽略最後一筆的 duration,重複最後一張補足長度
    listing = work / "video.ffconcat"
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return listing


def _write_timeline_srt(cues: list, path: Path) -> None:
    rows = [f"{i}\n{_ts(a / 1000)} --> {_ts(b / 1000)}\n{text}\n" for i, (a, b, text) in enumerate(cues, 1)]
    Path(path).write_text("\n".join(rows), encoding="utf-8")


def render_timeline(project, layout):
    """依 `timeline.layout(project)` 匯出 1920×1080 MP4(H.264 + AAC + mov_text)與同主檔名 SRT,回傳 (mp4, srt)。

    - 專案中任何片段沒有音檔 → RenderError(`.segments` 列出片段 id),不輸出任何檔案。
    - 音軌:各片段的(變速後,`tts_service.apply_speed`)音檔轉成 48 kHz 單聲道,放在 layout 的起點、
      截或補到 layout 長度,片段之間補靜音;總長 = layout.duration
      (與 T4 一致:片段長度總和 + 片段之間的停頓總和,最後一段之後的停頓不計)。
    - 畫面:圖片起訖四捨五入到 0.1 秒,後開始的圖蓋過先前的圖,沒有圖的時段為黑畫面;
      輸出影格率 `-r 10`,輸出檔名前加 `-t <layout.duration,小數 3 位>`(修訂 3,R3-5)。
    - 素材與音檔只依 asset id / 片段音檔 id 在本專案 `assets/`、`audio/` 內解析,符號連結一律拒絕;
      ffmpeg 的 argv 只有伺服器產生的路徑(暫存目錄、輸出佔位檔),片段音檔經 stdin 傳入。
    - 輸出以 `taiwan_ai.reserve()` 排他佔位(mp4 + 同主檔名 .srt),失敗時刪除佔位檔與暫存目錄。
    """
    missing = [s["id"] for s in project.segments if not s.get("audio")]
    if missing:
        raise RenderError(f"✗ 還有 {len(missing)} 個片段尚未合成,無法匯出", missing)
    if not project.segments:
        raise RenderError("✗ 沒有可匯出的語音片段")
    total, spans, cues = _check_layout(project, layout)
    images = _resolve_images(project, layout, total)
    audio_paths = _segment_audio(project)

    path = tai.reserve("podcast", ".mp4", also=(".srt",))
    srt = path.with_suffix(".srt")
    with tai.discard_on_error(path, srt):
        work = Path(tempfile.mkdtemp(prefix=".render-", dir=tai.OUT))
        try:
            wav = work / "audio.wav"
            _build_audio(audio_paths, spans, total, wav)
            listing = _build_video_list(images, total, work)
            _write_timeline_srt(cues, srt)
            cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-y",
                   "-f", "concat", "-i", str(listing), "-i", str(wav)]
            if cues:
                cmd += ["-i", str(srt), "-map", "0:v", "-map", "1:a", "-map", "2:s",
                        "-c:s", "mov_text", "-metadata:s:s:0", "language=chi"]
            else:
                cmd += ["-map", "0:v", "-map", "1:a"]
            # concat 的靜止圖每張只有一幀;只靠 `-r 10` 時最後一張不會補幀(實測影片軌只到最後一張的起點),
            # 所以先以 fps 濾鏡展開成每 0.1 秒一幀,再由 `-t` 截止
            cmd += ["-vf", "fps=10",
                    "-c:v", "libx264", "-tune", "stillimage", "-r", "10", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k",
                    "-t", f"{total / 1000:.3f}", str(path)]  # 本機計算的秒數,不是使用者字串
            try:
                subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=max(RENDER_TIMEOUT, total / 1000 * 2))
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
                raise RenderError("✗ 影片渲染失敗") from None
        finally:
            _rmtree_in_out(work)
    return path, srt
