# -*- coding: utf-8 -*-
"""合成服務:只合成髒片段、內容雜湊快取、語速後製(CHG-20261022-04 T3)。

快取鍵(修訂 3,R3-4):
  key = SHA-256(JSON 陣列 [含標註的合成文字, 聲線檔 SHA-256, 聲線逐字稿 SHA-256, 模型名]) 的 64 碼十六進位。
  以 JSON 陣列編碼而非直接串接,避免「文字結尾 + 雜湊開頭」不同切法得到同一個鍵。
  合成音檔一律存成 `projects/<名稱>/audio/<key 前 32 碼>.wav`(以內容定址;片段的 audio.id 即 key 前 32 碼,
  符合 `^[0-9a-f]{8,32}$`),所以改回原文字、或兩句文字相同時直接命中,不再合成。

聲線(修訂 3,R3-4):每次 `synth()` 經 `taiwan_ai.role_config("voice")` 讀一次(`wav` 以 `taiwan_ai.ROOT`
  轉絕對路徑、`text` 為逐字稿),不另存、不快取設定值;同一次 `synth()` 內的髒片段判斷共用這一份。

介面:
  Voice                         聲線(wav 絕對路徑、逐字稿、兩者的 SHA-256、模型名)
  load_voice() -> Voice         讀一次 `role_config("voice")`
  cache_key(segment, voice) -> str
  dirty_segments(project, voice=None) -> [segment]
                                需要合成的片段:沒有音檔、快取鍵不符(文字、標註、聲線檔、逐字稿或模型改了)、
                                或音檔不在 `audio/`。audio 沒有 key 的片段是外部提供的音檔(T6 自錄取代),不算髒。
  synth(project, emit) -> {"synthesized": [片段 id], "cached": [片段 id]}
                                只處理髒片段:快取命中直接套用;其餘在 `heavy_lock` 內一次啟動 worker
                                (argv list、`env=taiwan_ai.BREEZY_ENV`、`cwd=tools/BreezyVoice`),逐行讀 worker stdout
                                的進度行 `{"id", "seconds"}`,每句完成即把音檔移入 `audio/`、更新片段並 emit,
                                不等整批結束。就地更新 `project.segments[*].audio`,不存檔(存檔由呼叫端決定);
                                中途失敗時已完成的句子保留在快取,重跑會命中。
                                emit 的事件:{"type": "waiting"}(等待其他重型工作)、
                                {"type": "segment", "id", "seconds", "cached", "audio": {"id", "seconds", "key"}}
                                (DIR-5 統整 M-1:帶該句的音檔,伺服器據此讓合成中途即可試聽)。
  apply_speed(segment, project) -> Path
                                以 ffmpeg `atempo`(單一濾鏡,argv list)把合成音檔變速,存成
                                `audio/<audio.id>-s<語速×100>.wav`;同一個「合成快取鍵(audio.id)+ 語速」已存在
                                即重用,不重跑 ffmpeg;語速 1.0 直接回傳原音檔。
  SynthError                    失敗(訊息繁中、不含本機絕對路徑)

atempo 範圍:隨 imageio-ffmpeg 0.6.0 附帶的 ffmpeg 7.1 的 atempo 接受 0.5–100(`ffmpeg -h filter=atempo`,
  2026-10-27 查證);語速範圍 0.5–2.0 在內,依全域約束以單一濾鏡處理、不串接。

worker 錯誤訊息:worker stderr 混有 BreezyVoice 的輸出(含本機路徑),只取 worker 自己的「✗」開頭訊息,
  再遮蔽 repo 根目錄、家目錄與其他絕對路徑;取不到時依結束碼給固定的繁中訊息。
"""
from __future__ import annotations

import collections
import contextlib
import hashlib
import json
import os
import queue
import re
import secrets
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import imageio_ffmpeg

import breezy_worker
import heavy_lock
import media
import project as pj
import safe_fs
import taiwan_ai

ROOT = Path(__file__).resolve().parent
WORKER = ROOT / "breezy_worker.py"
BREEZY = taiwan_ai.BREEZY
MODEL = breezy_worker.DEFAULT_MODEL
SENTENCE_TIMEOUT = 120  # 每句逾時(秒)
MODEL_LOAD_TIMEOUT = 180  # 第一句另給的模型載入額度(秒;CHG 修訂 4,使用者 2026-10-28 核准)
# 等第一個進度行最多 MODEL_LOAD_TIMEOUT + SENTENCE_TIMEOUT 秒,之後每句 SENTENCE_TIMEOUT 秒;
# 整批逾時 = MODEL_LOAD_TIMEOUT + 句數 × SENTENCE_TIMEOUT。
SPEED_TIMEOUT = 120
STDERR_KEEP = 200
MAX_MESSAGE = 300
EXIT_HINTS = {
    2: "✗ 語音合成的輸入不正確",
    3: breezy_worker.LOAD_FAILED_HINT,
    4: "✗ 語音合成失敗",
}
_ABS_PATH = re.compile(r"(?<![\w<>~])/(?:[^\s/'\"(),;:]+/)*[^\s/'\"(),;:]+")


class SynthError(RuntimeError):
    """合成或變速失敗;訊息為繁中、不含本機絕對路徑,可直接給使用者看。"""


@dataclass(frozen=True)
class Voice:
    wav: Path
    text: str
    wav_sha: str
    text_sha: str
    model: str = MODEL


def _sha256_file(path: Path) -> str:
    """聲線檔的 SHA-256;repo 內的路徑自 repo 根以 dir_fd 逐層 O_NOFOLLOW 開啟後讀同一個 fd(DIR-5 修正 4)。"""
    h = hashlib.sha256()
    root = Path(taiwan_ai.ROOT).resolve()
    try:
        parts = Path(os.path.abspath(path)).relative_to(root).parts
    except ValueError:
        raise OSError("聲線檔不在 repo 內") from None
    with os.fdopen(safe_fs.open_path(root, parts), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_voice() -> Voice:
    """讀一次聲線設定(`role_config("voice")`)並計算聲線檔與逐字稿的 SHA-256。"""
    conf = taiwan_ai.role_config("voice")
    wav, text = conf.get("wav"), conf.get("text")
    if not isinstance(wav, str) or not wav or not isinstance(text, str) or not text.strip():
        raise SynthError("✗ 聲線設定不完整:需要聲線檔與逐字稿(設定 › 語音)")
    path = Path(taiwan_ai.ROOT) / wav
    try:
        wav_sha = _sha256_file(path)
    except OSError:
        raise SynthError("✗ 找不到聲線檔或無法讀取(設定 › 語音)") from None
    return Voice(wav=path.resolve(), text=text, wav_sha=wav_sha,
                 text_sha=hashlib.sha256(text.encode("utf-8")).hexdigest())


def cache_key(segment: dict, voice: Voice) -> str:
    """SHA-256(含標註的合成文字 + 聲線檔 SHA-256 + 聲線逐字稿 SHA-256 + 模型名)。"""
    parts = [pj.tts_text(segment), voice.wav_sha, voice.text_sha, voice.model]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()


def _audio_id(key: str) -> str:
    return key[:32]


def _regular_in(dirfd: int | None, name: str) -> bool:
    """dirfd 內的 name 是一般檔(不跟隨符號連結;DIR-5 修正 4 改以目錄 fd 判斷)。"""
    if dirfd is None:
        return False
    st = safe_fs.lstat(dirfd, name)
    return st is not None and stat.S_ISREG(st.st_mode)


def _audio_fd(project, create: bool) -> int | None:
    """專案 `audio/` 的目錄 fd(自 repo 根逐層 O_NOFOLLOW;呼叫端關閉);不存在或不安全回 None(create 時丟例外)。"""
    try:
        return pj.open_subdir(project.name, "audio", create=create)
    except FileNotFoundError:
        return None
    except pj.ProjectError:
        if create:
            raise
        return None


def _audio_dir(project, create: bool) -> Path | None:
    """專案 `audio/` 的路徑(只供回傳給呼叫端與交給外部程式;本模組的讀寫一律用 `_audio_fd()`)。"""
    fd = _audio_fd(project, create)
    if fd is None:
        return None
    os.close(fd)
    return pj._top() / pj.PROJECTS_NAME / project.name / "audio"


def _seconds_in(dirfd: int, name: str) -> float:
    """以 O_NOFOLLOW 開啟 dirfd 內的 WAV 後讀這個 fd 的長度(不以路徑重開)。"""
    try:
        fd = safe_fs.open_file(dirfd, name)
    except OSError:
        raise SynthError("✗ 合成音檔無法讀取:不是一般檔案") from None
    try:
        return round(media.wav_seconds(fd), 3)
    except ValueError as e:
        raise SynthError(f"✗ 合成音檔無法讀取:{e}") from None
    finally:
        os.close(fd)


def dirty_segments(project, voice: Voice | None = None) -> list[dict]:
    """回傳需要合成的片段(依片段順序);voice 省略時讀一次聲線設定。"""
    voice = voice or load_voice()
    adir = _audio_fd(project, create=False)
    out = []
    try:
        for seg in project.segments:
            audio = seg.get("audio")
            if audio is None:
                out.append(seg)
                continue
            if "key" not in audio:
                continue  # 外部提供的音檔(自錄取代),不由合成服務覆蓋
            key = cache_key(seg, voice)
            if audio["key"] != key or not _regular_in(adir, f"{audio['id']}.wav"):
                out.append(seg)
    finally:
        if adir is not None:
            os.close(adir)
    return out


# ---------- worker ----------

def _mask(text: str) -> str:
    """遮蔽 repo 根目錄、家目錄與其他絕對路徑。"""
    pairs = []
    for p, repl in ((ROOT, "<repo>"), (Path(taiwan_ai.ROOT), "<repo>"), (Path.home(), "~")):
        for s in {str(p), str(p.resolve())}:
            if s and s != os.sep:
                pairs.append((s, repl))
    for s, repl in sorted(pairs, key=lambda x: len(x[0]), reverse=True):
        text = text.replace(s, repl)
    return _ABS_PATH.sub("<路徑>", text)


def _worker_message(code: int | None, stderr_lines) -> str:
    """worker 失敗訊息:只取 worker 自己印的「✗」行並遮蔽路徑;取不到時依結束碼給固定訊息。"""
    mine = [ln.strip() for ln in stderr_lines if ln.strip().startswith("✗")]
    if mine:
        return _mask(mine[-1])[:MAX_MESSAGE]
    return EXIT_HINTS.get(code, "✗ 語音合成失敗")


def _worker_argv(jobs_path: Path, out_dir: Path) -> list[str]:
    return [str(BREEZY / ".venv" / "bin" / "python"), str(WORKER), "synth",
            "--jobs", str(jobs_path), "--out", str(out_dir)]


def _pump(stream, sink):
    try:
        for line in stream:
            sink(line)
    finally:
        sink(None)


def _write_jobs(tmp_fd: int, voice: Voice, jobs: list[dict]) -> int:
    """工作清單寫進 tmp/ 內排他建立的檔案後立即刪除名稱,回傳匿名 fd(DIR-5 修正 4:worker 以 /dev/fd 讀取)。"""
    body = {"prompt_wav": str(voice.wav), "prompt_text": voice.text, "model": voice.model, "jobs": jobs}
    name = f"jobs-{secrets.token_hex(8)}.json"
    fd = safe_fs.create_excl(tmp_fd, name, 0o600, flags=os.O_RDWR)
    try:
        safe_fs.unlink(tmp_fd, name)
        os.write(fd, json.dumps(body, ensure_ascii=False).encode("utf-8"))
        os.lseek(fd, 0, os.SEEK_SET)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _run_worker(voice: Voice, jobs: list[dict], tmp_dir: Path, on_done) -> None:
    """啟動 worker 並逐行處理進度;on_done(job_id, out_fd) 在每句完成時呼叫(out_fd 為本次輸出目錄的 fd)。

    (DIR-5 修正 4)tmp/ 以 `project` 的目錄 fd 開啟:工作清單為匿名 fd(`--jobs /dev/fd/<n>`,pass_fds);
    輸出目錄以 mkdir(dir_fd) 建立後保留 O_NOFOLLOW 開啟的 fd,本程式讀取產物與清理都相對這個 fd。
    worker 是外部行程,只能以路徑 `--out` 寫入(殘餘風險記於 CHG 修訂 4 補記)。"""
    pending = {j["id"] for j in jobs}
    lines: queue.Queue = queue.Queue()
    stderr_tail: collections.deque = collections.deque(maxlen=STDERR_KEEP)
    proc = jobs_fd = out_fd = out_name = None
    tmp_fd = safe_fs.open_dir(tmp_dir.parent, [tmp_dir.name])
    try:
        jobs_fd = _write_jobs(tmp_fd, voice, jobs)
        out_name = f"out-{secrets.token_hex(8)}"
        os.mkdir(out_name, 0o700, dir_fd=tmp_fd)
        out_fd = safe_fs.open_child(tmp_fd, out_name)
        try:
            proc = subprocess.Popen(_worker_argv(Path(f"/dev/fd/{jobs_fd}"), tmp_dir / out_name),
                                    cwd=str(BREEZY), env=taiwan_ai.BREEZY_ENV, pass_fds=(jobs_fd,),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding="utf-8", errors="replace", bufsize=1)
        except OSError:
            raise SynthError("✗ 無法啟動語音合成(請確認 tools/BreezyVoice 已安裝)") from None
        readers = [threading.Thread(target=_pump, args=(proc.stdout, lines.put), daemon=True),
                   threading.Thread(target=_pump, args=(proc.stderr, lambda ln: ln is not None and
                                                        stderr_tail.append(ln)), daemon=True)]
        for t in readers:
            t.start()
        deadline = time.monotonic() + MODEL_LOAD_TIMEOUT + SENTENCE_TIMEOUT * len(jobs)
        first = True   # 第一句含模型載入,另給 MODEL_LOAD_TIMEOUT
        while True:
            limit = SENTENCE_TIMEOUT + (MODEL_LOAD_TIMEOUT if first else 0)
            wait = min(limit, deadline - time.monotonic())
            if wait <= 0:
                raise SynthError("✗ 語音合成逾時,已終止")
            try:
                line = lines.get(timeout=wait)
            except queue.Empty:
                raise SynthError("✗ 語音合成逾時,已終止") from None
            if line is None:
                break
            try:
                row = json.loads(line)
            except ValueError:
                continue
            jid = row.get("id") if isinstance(row, dict) else None
            if jid not in pending:
                continue
            pending.discard(jid)
            first = False
            on_done(jid, out_fd)
        try:
            code = proc.wait(timeout=max(1.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise SynthError("✗ 語音合成逾時,已終止") from None
        for t in readers:
            t.join(timeout=5)
        if code != 0:
            raise SynthError(_worker_message(code, list(stderr_tail)))
        if pending:
            raise SynthError("✗ 語音合成未完成:worker 沒有回報全部句子")
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        if jobs_fd is not None:
            os.close(jobs_fd)
        if out_fd is not None:
            os.close(out_fd)
        if out_name is not None:
            with contextlib.suppress(OSError):
                safe_fs.rmtree(tmp_fd, out_name, missing_ok=True)
        os.close(tmp_fd)


def synth(project, emit) -> dict:
    """只合成髒片段;每句完成即 emit。就地更新片段的 audio,不存檔。"""
    voice = load_voice()  # 每次 synth() 只讀一次聲線設定
    dirty = dirty_segments(project, voice)
    result = {"synthesized": [], "cached": []}
    if not dirty:
        return result
    by_key: dict[str, list[dict]] = {}
    for seg in dirty:
        by_key.setdefault(cache_key(seg, voice), []).append(seg)

    def assign(key: str, seconds: float, cached: bool):
        for seg in by_key[key]:
            seg["audio"] = {"id": _audio_id(key), "seconds": seconds, "key": key}
            result["cached" if cached else "synthesized"].append(seg["id"])
            emit({"type": "segment", "id": seg["id"], "seconds": seconds, "cached": cached,
                  "audio": dict(seg["audio"])})

    adir = _audio_fd(project, create=True)
    try:
        return _synth_into(project, emit, voice, by_key, assign, adir, result)
    finally:
        os.close(adir)


def _synth_into(project, emit, voice, by_key, assign, adir: int, result: dict) -> dict:
    """synth() 的本體:快取判斷、讀長度、產物移入一律相對 `audio/` 的目錄 fd(DIR-5 修正 4)。"""
    todo = []
    for key in by_key:
        name = f"{_audio_id(key)}.wav"
        if _regular_in(adir, name):
            assign(key, _seconds_in(adir, name), cached=True)
        else:
            todo.append(key)
    if not todo:
        return result

    keys = {_audio_id(k): k for k in todo}
    jobs = [{"id": _audio_id(k), "text": pj.tts_text(by_key[k][0])} for k in todo]

    def on_done(jid: str, out_fd: int):
        name = f"{jid}.wav"
        if not _regular_in(out_fd, name):
            raise SynthError("✗ 語音合成失敗:找不到這一句的音檔")
        safe_fs.replace(out_fd, name, adir, name)   # 相對兩個目錄 fd 搬移,不經路徑
        assign(keys[jid], _seconds_in(adir, name), cached=False)

    with heavy_lock.heavy_lock(on_wait=lambda: emit({"type": "waiting"})):
        tmp_dir = pj.subdir(project.name, "tmp", create=True)
        _run_worker(voice, jobs, tmp_dir, on_done)
    return result


# ---------- 語速 ----------

def apply_speed(segment: dict, project) -> Path:
    """回傳變速後的音檔路徑;以「合成快取鍵(audio.id)+ 語速」快取,命中時不跑 ffmpeg。

    (DIR-5 修正 4)來源與輸出都相對 `audio/` 的目錄 fd:來源以 O_NOFOLLOW 開啟後經 stdin(`pipe:0`)
    交給 ffmpeg;輸出先在目錄 fd 內排他建立暫存檔,ffmpeg 寫 `/dev/fd/<n>`(pass_fds),完成後以
    rename(dir_fd) 換成快取檔名。argv 不含任何專案路徑。"""
    audio = segment.get("audio")
    if not audio:
        raise SynthError("✗ 這一句尚未合成")
    speed = pj.effective_speed(project, segment)
    adir = _audio_fd(project, create=False)
    src_name = f"{audio['id']}.wav"
    if not _regular_in(adir, src_name):
        if adir is not None:
            os.close(adir)
        raise SynthError("✗ 找不到這一句的合成音檔")
    base = pj._top() / pj.PROJECTS_NAME / project.name / "audio"
    with safe_fs.closing(adir):
        if speed == 1.0:
            return base / src_name
        dest = f"{audio['id']}-s{round(speed * 100):03d}.wav"
        if _regular_in(adir, dest):
            return base / dest
        part = f".{dest[:-len('.wav')]}-{secrets.token_hex(4)}.part.wav"
        cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-y", "-f", "wav", "-i", "pipe:0",
               "-filter:a", f"atempo={speed:.2f}", "-c:a", "pcm_s16le", "-f", "wav"]
        try:
            src = safe_fs.open_file(adir, src_name)
        except OSError:
            raise SynthError("✗ 找不到這一句的合成音檔") from None
        out = None
        try:
            out = safe_fs.create_excl(adir, part, 0o644, flags=os.O_RDWR)
            subprocess.run([*cmd, f"/dev/fd/{out}"], check=True, stdin=src, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=SPEED_TIMEOUT, pass_fds=(out,))
            safe_fs.replace(adir, part, adir, dest)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            raise SynthError("✗ 語速調整失敗") from None
        finally:
            os.close(src)
            if out is not None:
                os.close(out)
            safe_fs.unlink(adir, part, missing_ok=True)
    return base / dest
