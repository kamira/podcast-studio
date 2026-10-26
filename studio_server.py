#!/usr/bin/env -S uv run --script
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "fastapi>=0.115",
#   "uvicorn>=0.30",
#   "python-multipart>=0.0.9",
#   "opencc-python-reimplemented",
#   "imageio-ffmpeg",
#   "pillow<13",
# ]
# ///
"""Podcast 工作室網頁伺服器核心(CHG-20261022-01 T7)。

FastAPI + 原生 JS;只綁 127.0.0.1:7860。本檔提供:

  GET  /                         首頁;`static/index.html` 中的 TOKEN_PLACEHOLDER 換成啟動權杖。
                                 `static/` 尚未建立時(前端屬 T10 之後)送內建的簡易頁面
  GET  /static/{path}            靜態檔;只解析 `static/` 之下的真實路徑
  GET  /api/nodes                {"nodes": [NodeSpec.spec_dict()…], "port_types": [...]}
  POST /api/run                  請求本文就是 executor 的流程圖 JSON(格式見 executor.py 檔頭;
                                 節點 id 必須是字串,Drawflow 的數字 id 由前端轉換)
                                 合法 → 200 {"run_id"};不合法 → 400 {"errors": [{node_id, port, message}]};
                                 已有執行中的 run → 409;本文超過 MAX_BODY → 413
  GET  /api/run/{id}/events      SSE;每則 `id: <序號>` + `data: <executor 事件 JSON>`,
                                 送完 finished 後結束;支援 Last-Event-ID 從中斷處續傳
  POST /api/run/{id}/cancel      設定取消旗標;executor 不再啟動新節點,進行中的節點跑完

上傳、檔案服務、工作流程(T8):

  POST /api/upload?name=<原始檔名>
                                 請求本文就是檔案的原始位元組(不是 multipart,前端以
                                 `fetch(url, {method: "POST", body: file})` 送出);原始檔名只取副檔名。
                                 白名單 `.txt .md .png .jpg .jpeg .webp .wav .mp3 .m4a`(= nodes.FILE_KINDS);
                                 以串流寫入 `outputs/uploads/` 內點開頭的暫存檔並累計大小,超過
                                 MAX_UPLOAD(200 MB)立即中止回 413 並刪除暫存檔;依檔頭位元組嗅探
                                 (不信任用戶端 Content-Type),文字檔必須是 UTF-8、無 NUL、≤ nodes.MAX_TEXT 字;
                                 不符回 400。通過後以 `os.link` 排他建立 `outputs/uploads/<uuid4 hex>.<ext>`
                                 (撞名重取,不覆寫、不跟隨符號連結)。
                                 回 200 {"file_id": "uploads/<uuid>.<ext>", "kind": "text"|"image"|"audio",
                                         "url": "/files/outputs/uploads/<uuid>.<ext>"}
                                 `file_id` 相對於 `outputs/`,可直接當 file-input 節點的 `file` 參數。
  GET  /files/{path}             path 相對於 repo 根目錄,且第一層必須是 `outputs`
                                 (與 SSE 事件裡的 `outputs/…` 相同)。路徑由框架 URL 解碼一次,
                                 再 resolve(),最後以路徑元件判斷是否位於 outputs/ 之內(不用字串前綴,
                                 `outputs_evil/` 之類的兄弟目錄不算)。絕對路徑、任一層以 `.` 開頭
                                 (含 `..`、`.heavy.lock`、上傳暫存檔)、指向 outputs/ 外的符號連結、
                                 不是一般檔案、`outputs/` 本身是符號連結 → 一律 404。
                                 已知媒體型別 inline 送出;文字檔 `text/plain; charset=utf-8`;
                                 其他一律 `application/octet-stream` + `Content-Disposition: attachment`。
  GET  /api/workflows/{name}     讀 `workflows/<name>.json` → {"name", "graph"};不存在 404;內容壞掉 422
  PUT  /api/workflows/{name}     本文 {"graph": 流程圖, "overwrite": true|false(選填,預設 false)};
                                 名稱必須完全符合 `[\\w\\-]{1,64}`(re.ASCII:只限英數、底線、連字號);
                                 graph 必須通過 executor.validate(不合格 400,格式同 /api/run);
                                 本文上限 MAX_BODY(413);已存在且未帶 overwrite:true → 409。
                                 寫入同目錄暫存檔後:不覆寫時以 `os.link` 排他建立,覆寫時 `os.replace`
                                 (原子替換;既有的符號連結只被替換,不寫入其目標)。
  `outputs/`、`outputs/uploads/`、`workflows/` 逐層不得是符號連結、解析後必須等於 repo 內對應目錄;
  不符時上傳與存檔回 500(本文不含路徑)、讀取回 404,且不建立任何東西。

設定 API(T9;模型設定一律只經 `settings.load()` / `save()` / `validate()` / `load_meta()`
/ `voice_options()`,本檔不直接開啟設定資料庫或一次性匯入來源):

  GET  /api/config               `settings.load()` 的結果(roles、review_seats,讀取失敗時另有
                                 `_warnings`)加上 `meta`:{"imported_from", "imported_at"}
                                 (`settings.load_meta()`;只在沒有 `_warnings` 時提供,否則為 {})。
  PUT  /api/config               本文為設定 JSON(可直接送回 GET 的內容;`_warnings`、`meta` 會被忽略),
                                 上限 MAX_CONFIG_BODY(串流累計,超過 413)。流程:
                                 本文不是 JSON 物件 → 422;`settings.validate(cfg, None)` 有錯 → 422
                                 (結構、否決席 glm4、Codex 只限否決席、暫停的 27B、聲線路徑);
                                 向 Ollama `/api/tags` 取已下載清單,連不上 → 503(不儲存,不會在
                                 無法確認時接受未知模型名稱);`settings.validate(cfg, 清單)` 有錯 → 422;
                                 合格才呼叫 `settings.save(cfg)`,失敗 → 500。
                                 成功回 200 {"saved": true, "config": 同 GET}。
  GET  /api/models               {"ollama": {"reachable", "models": [已下載模型名稱], "error"?},
                                  "mflux": {"installed"}, "breezyvoice": {"installed"},
                                  "voices": [可選聲線檔,repo 相對路徑(settings.voice_options())]}
                                 Ollama 連不上時 reachable=false、models=[],仍回 200。
  Ollama 查詢只打 `taiwan_ai.OLLAMA` 的 `/api/tags`(經 `taiwan_ai.local_open()`:只限本機主機、
  不經代理、不跟隨重新導向),逾時 OLLAMA_TIMEOUT 秒、不重試,
  在執行緒池中執行,不阻塞事件迴圈;回應上限 MAX_TAGS_BYTES。
  所有錯誤訊息與 `_warnings` 一律經 `scrub()`,不送本機絕對路徑。

事件內容與 executor.py 檔頭相同,另有兩點:
  - 字串一律經 `scrub()`:repo 根目錄換成 `<repo>`、家目錄換成 `~`,不把本機絕對路徑送到瀏覽器。
  - 節點輸出值若是 repo 內的檔案路徑,改送相對於 repo 的路徑(例 `outputs/cover-….png`);
    檔案下載由 T8 的 `/files/*` 提供。
  - 執行器本身丟例外時補送 {"type": "error", "message": …} 與 {"type": "finished", "status": "error"}。

安全(CHG 安全影響 security-boundary 與 Global Constraints):
  - **所有請求**(含 GET、`/files/*`、SSE)的 Host 必須是 `127.0.0.1:7860` 或 `localhost:7860`。
  - GET / HEAD 以外的請求另外要求 Origin 完整等於 `http://127.0.0.1:7860` 或 `http://localhost:7860`
    (缺少或 `null` 都拒絕,OPTIONS 預檢一律拒絕),且標頭 `X-Studio-Token` 等於啟動權杖
    (`hmac.compare_digest` 比對)。不符一律 403。
  - 權杖每次啟動以 `secrets.token_urlsafe(32)` 產生,只存在記憶體與首頁 HTML,不寫入日誌或檔案。
  - 每個回應都帶 `X-Content-Type-Options: nosniff` 與 CSP;不送任何 CORS 標頭。
  - 同時只允許一個執行中的 run;未預期的例外回 500,本文不含例外內容。
"""
from __future__ import annotations

import asyncio
import hmac
import html
import json
import os
import re
import secrets
import sys
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402

import executor  # noqa: E402
import nodes  # noqa: E402
import settings  # noqa: E402
import taiwan_ai  # noqa: E402

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
HOST = "127.0.0.1"
PORT = 7860
ALLOWED_HOSTS = frozenset({f"127.0.0.1:{PORT}", f"localhost:{PORT}"})
ALLOWED_ORIGINS = frozenset({f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"})
SAFE_METHODS = frozenset({"GET", "HEAD"})
TOKEN_HEADER = b"x-studio-token"
TOKEN_PLACEHOLDER = "__STUDIO_TOKEN__"
CSP = "default-src 'self'; media-src 'self' blob:; img-src 'self' blob: data:"
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"content-security-policy", CSP.encode("ascii")),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
]
MAX_BODY = 4 * 1024 * 1024   # /api/run 本文上限(100 個節點 × 20000 字的文字參數仍在範圍內)
KEEP_RUNS = 20               # 保留最近幾次 run 的事件,供 SSE 重連
SSE_POLL_SECONDS = 0.05
SSE_KEEPALIVE_SECONDS = 15.0
MAX_UPLOAD = 200 * 1024 * 1024   # 上傳上限(CHG 代決事項)
MAX_CONFIG_BODY = 256 * 1024     # PUT /api/config 本文上限(逐字稿 20000 字以 \uXXXX 跳脫仍在範圍內)
OLLAMA_TIMEOUT = 3               # 秒;查 /api/tags 不重試
MAX_TAGS_BYTES = 4 * 1024 * 1024  # /api/tags 回應上限
CONFIG_EXTRA_KEYS = ("_warnings", "meta")   # GET 回應附加、PUT 時忽略的鍵
MAX_UPLOAD_NAME = 255
# 副檔名 → 埠型別;與 file-input 節點接受的副檔名一致
UPLOAD_KINDS = {ext: kind for kind, exts in nodes.FILE_KINDS.items() for ext in exts}
WORKFLOW_NAME = re.compile(r"[\w\-]{1,64}", re.ASCII)   # 只允許英數、底線、連字號(以 fullmatch 比對)
M4A_BRANDS = frozenset({b"M4A ", b"M4B ", b"M4P ", b"mp41", b"mp42", b"isom", b"iso2"})
# /files/* 的回應型別:表內的以 inline 送出;不在表內的一律 octet-stream + attachment
SERVE_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "video/mp4",
    ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8",
    ".srt": "text/plain; charset=utf-8",
}

PLACEHOLDER_INDEX = f"""<!doctype html>
<html lang="zh-Hant-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="studio-token" content="{TOKEN_PLACEHOLDER}">
<title>Podcast 工作室</title>
</head>
<body>
<h1>Podcast 工作室</h1>
<p>伺服器已啟動。網頁介面尚未安裝(<code>static/</code> 不存在)。</p>
</body>
</html>
"""


# ---------- 路徑遮蔽 ----------

def _prefixes() -> list[tuple[str, str]]:
    pairs = []
    for p in {str(ROOT), str(ROOT.resolve())}:
        pairs.append((p, "<repo>"))
    home = Path.home()
    for p in {str(home), str(home.resolve())}:
        if p and p != os.sep:
            pairs.append((p, "~"))
    # 長的先換:repo 通常在家目錄之下,要先換成 <repo>
    return sorted(pairs, key=lambda x: len(x[0]), reverse=True)


def scrub(text: str) -> str:
    """把字串中的 repo 根目錄換成 `<repo>`、家目錄換成 `~`。"""
    for prefix, repl in _prefixes():
        text = text.replace(prefix, repl)
    return text


def _repo_relative(value: str) -> str:
    """repo 內的絕對路徑改成相對路徑(例 `outputs/x.png`);其他字串只做 scrub。"""
    for prefix, repl in _prefixes():
        if repl == "<repo>" and value.startswith(prefix + os.sep):
            rel = value[len(prefix) + 1:]
            if "\n" not in rel:
                return Path(rel).as_posix()
    return scrub(value)


def _scrub_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return scrub(obj)
    if isinstance(obj, dict):
        return {k: _scrub_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_obj(v) for v in obj]
    return obj


def sanitize_event(ev: dict) -> dict:
    out = {}
    for k, v in ev.items():
        if k == "outputs" and isinstance(v, dict):
            out[k] = {p: (_repo_relative(x) if isinstance(x, str) else _scrub_obj(x)) for p, x in v.items()}
        else:
            out[k] = _scrub_obj(v)
    return out


# ---------- 安全中介層(純 ASGI,SSE 串流不受影響) ----------

def _header_values(scope, name: bytes) -> list[bytes]:
    return [v for k, v in scope.get("headers", []) if k.lower() == name]


def _check_request(scope, token: bytes) -> str | None:
    """回傳拒絕原因(只供內部判斷,不送給瀏覽器);合格回 None。"""
    hosts = _header_values(scope, b"host")
    if len(hosts) != 1 or hosts[0].decode("latin-1") not in ALLOWED_HOSTS:
        return "host"
    if scope.get("method", "GET").upper() in SAFE_METHODS:
        return None
    origins = _header_values(scope, b"origin")
    if len(origins) != 1 or origins[0].decode("latin-1") not in ALLOWED_ORIGINS:
        return "origin"
    tokens = _header_values(scope, TOKEN_HEADER)
    if len(tokens) != 1 or not hmac.compare_digest(tokens[0], token):
        return "token"
    return None


class SecurityMiddleware:
    def __init__(self, app, token: str):
        self.app = app
        self.token = token.encode("ascii")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def send_with_headers(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = [(k, v) for k, v in message.get("headers", [])
                           if k.lower() not in {h for h, _ in SECURITY_HEADERS}
                           and not k.lower().startswith(b"access-control-")]
                message = {**message, "headers": headers + SECURITY_HEADERS}
            await send(message)

        if _check_request(scope, self.token) is not None:
            await _send_json(send_with_headers, 403, {"error": "拒絕存取"})
            return
        try:
            await self.app(scope, receive, send_with_headers)
        except Exception as e:
            print(f"studio_server:未預期的錯誤 {type(e).__name__}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            if not started:
                await _send_json(send_with_headers, 500, {"error": "伺服器內部錯誤"})


async def _send_json(send, status: int, body: dict) -> None:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json; charset=utf-8"),
                            (b"content-length", str(len(data)).encode())]})
    await send({"type": "http.response.body", "body": data})


# ---------- 執行狀態 ----------

class Run:
    def __init__(self, run_id: str):
        self.id = run_id
        self.events: list[dict] = []
        self.done = False
        self.cancel = threading.Event()
        self.lock = threading.Lock()

    def emit(self, ev: dict) -> None:
        clean = sanitize_event(ev) if isinstance(ev, dict) else {"type": "error", "message": "事件格式不對"}
        with self.lock:
            self.events.append(clean)
            if clean.get("type") == "finished":
                self.done = True

    def snapshot(self, start: int) -> tuple[list[dict], bool]:
        with self.lock:
            return self.events[start:], self.done


class Runs:
    def __init__(self):
        self.lock = threading.Lock()
        self.runs: OrderedDict[str, Run] = OrderedDict()
        self.active: str | None = None

    def start(self) -> Run | None:
        """同時只允許一個執行中的 run;已有則回 None。"""
        with self.lock:
            if self.active is not None:
                return None
            run = Run(uuid.uuid4().hex)
            self.runs[run.id] = run
            self.active = run.id
            while len(self.runs) > KEEP_RUNS:
                oldest = next(iter(self.runs))
                if oldest == self.active:
                    break
                self.runs.pop(oldest)
            return run

    def finish(self, run: Run) -> None:
        with self.lock:
            if self.active == run.id:
                self.active = None

    def get(self, run_id: str) -> Run | None:
        with self.lock:
            return self.runs.get(run_id)


def _run_thread(runs: Runs, run: Run, runner, graph) -> None:
    try:
        runner(graph, run.emit, run.cancel)
    except Exception as e:
        msg = str(e).strip() or type(e).__name__
        print(f"studio_server:執行器錯誤 {type(e).__name__}", file=sys.stderr)
        run.emit({"type": "error", "message": msg})
    finally:
        _, done = run.snapshot(0)
        if not done:
            run.emit({"type": "finished", "status": "error"})
        runs.finish(run)


# ---------- 應用程式 ----------

def _error_body(errors) -> dict:
    return {"errors": [_scrub_obj(e.as_dict()) for e in errors]}


def _bad(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"errors": [{"node_id": None, "port": None, "message": message}]}, status_code=status)


async def _read_body(request: Request, limit: int | None = None) -> bytes | None:
    """串流讀取本文;超過 limit(預設 MAX_BODY,呼叫時才讀取)回 None。"""
    if limit is None:
        limit = MAX_BODY
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > limit:
        return None
    buf = bytearray()
    async for chunk in request.stream():
        buf.extend(chunk)
        if len(buf) > limit:
            return None
    return bytes(buf)


def _static_file(static_dir: Path, rel: str) -> Path | None:
    """只回傳 static/ 之下的真實檔案路徑(resolve 後檢查前綴)。"""
    if not rel or "\x00" in rel or rel.startswith(("/", "\\")):
        return None
    base = static_dir.resolve()
    try:
        path = (base / rel).resolve()
    except (OSError, RuntimeError):
        return None
    if not path.is_relative_to(base) or not path.is_file():
        return None
    return path


# ---------- 上傳、檔案服務、工作流程(T8) ----------

class UnsafePath(OSError):
    """repo 內的資料目錄是符號連結或解析到別處。訊息不含路徑,可安全記錄。"""


def _safe_dir(root: Path, *parts: str, create: bool) -> Path:
    """回傳 root/parts 的真實路徑;逐層不得是符號連結、解析後必須等於 repo 內對應目錄。

    create=True 時不存在的層會建立(建立前後都檢查);不符丟 UnsafePath,且不建立任何東西。
    """
    base = Path(root).resolve()
    cur, expected = Path(root), base
    for part in parts:
        cur, expected = cur / part, expected / part
        if cur.is_symlink():
            raise UnsafePath(f"資料目錄「{part}」是符號連結,不接受")
        if not cur.exists():
            if not create:
                raise FileNotFoundError(part)
            try:
                cur.mkdir(mode=0o755)
            except FileExistsError:
                pass
            if cur.is_symlink():
                raise UnsafePath(f"資料目錄「{part}」是符號連結,不接受")
        try:
            resolved = cur.resolve(strict=True)
        except (OSError, RuntimeError):
            raise UnsafePath(f"資料目錄「{part}」無法解析") from None
        if resolved != expected or not resolved.is_dir():
            raise UnsafePath(f"資料目錄「{part}」不在 repo 內")
    return expected


def _valid_name(name: str) -> bool:
    return isinstance(name, str) and WORKFLOW_NAME.fullmatch(name) is not None


def _upload_ext(name: str | None) -> str | None:
    """只取原始檔名的副檔名(小寫);不在白名單回 None。"""
    if not isinstance(name, str) or not name or len(name) > MAX_UPLOAD_NAME or "\x00" in name:
        return None
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    stem, dot, ext = base.rpartition(".")
    if not dot or not stem:
        return None
    ext = "." + ext.lower()
    return ext if ext in UPLOAD_KINDS else None


def _sniff_binary(ext: str, head: bytes) -> bool:
    """依檔頭位元組判斷內容是否與副檔名相符(不信任用戶端 Content-Type)。"""
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


def _check_upload(ext: str, path: Path, size: int) -> str | None:
    """回傳錯誤訊息;內容合格回 None。"""
    if size == 0:
        return "檔案是空的"
    if UPLOAD_KINDS[ext] == "text":
        if size > nodes.MAX_TEXT * 4 + 3:   # UTF-8 每字最多 4 位元組,加 BOM
            return f"文字檔超過 {nodes.MAX_TEXT} 字"
        try:
            text = path.read_bytes().decode("utf-8-sig")
        except UnicodeDecodeError:
            return "文字檔必須是 UTF-8 編碼"
        if "\x00" in text:
            return "文字檔內容不是純文字"
        if len(text) > nodes.MAX_TEXT:
            return f"文字檔超過 {nodes.MAX_TEXT} 字"
        return None
    with open(path, "rb") as f:
        head = f.read(16)
    if not _sniff_binary(ext, head):
        return f"檔案內容與副檔名 {ext} 不符"
    return None


def _open_excl(path: Path, mode: int = 0o600) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    return os.open(path, flags, mode)


def _link_excl(src: Path, make_dst: Callable[[], Path], tries: int = 8) -> Path:
    """以 os.link 排他建立目標(已存在——含符號連結——就換名重試);回傳目標路徑。"""
    for _ in range(tries):
        dst = make_dst()
        try:
            os.link(src, dst)   # link(2) 在目標已存在(含符號連結)時失敗,不會跟隨
            return dst
        except FileExistsError:
            continue
    raise FileExistsError("無法取得不重複的檔名")


def _served_file(root: Path, rel: str) -> Path | None:
    """`/files/{rel}` 對應的真實檔案;不在 outputs/ 之內或不合規則回 None。"""
    if not isinstance(rel, str) or not rel or "\x00" in rel or "\\" in rel or rel.startswith("/"):
        return None
    parts = rel.split("/")
    if parts[0] != "outputs" or len(parts) < 2 or any(not p or p.startswith(".") for p in parts):
        return None
    try:
        out = _safe_dir(root, "outputs", create=False)
        path = (Path(root) / rel).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    # 以路徑元件判斷包含關係(不是字串前綴):outputs_evil/ 不算在 outputs/ 之內
    if not path.is_relative_to(out) or path == out:
        return None
    if any(p.startswith(".") for p in path.relative_to(out).parts):
        return None
    if not path.is_file():
        return None
    return path


def _err(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


# ---------- 設定 API(T9) ----------

class OllamaUnavailable(Exception):
    """無法取得 Ollama 已下載模型清單(連不上、逾時或回應格式不對)。"""


OLLAMA_DOWN = "無法連線到本機 Ollama(localhost:11434),無法確認模型是否已下載"


def _good_model_name(v) -> bool:
    return (isinstance(v, str) and v.strip() != "" and len(v) <= settings.MAX_NAME_LEN
            and not any(ord(c) < 32 for c in v))


def fetch_ollama_tags() -> list[str]:
    """查本機 Ollama `/api/tags`,回傳已下載的模型名稱;逾時 OLLAMA_TIMEOUT 秒、不重試。

    連不上、逾時、回應過大或格式不對一律丟 OllamaUnavailable。
    """
    try:
        with taiwan_ai.local_open(f"{taiwan_ai.OLLAMA}/api/tags", timeout=OLLAMA_TIMEOUT) as r:
            data = r.read(MAX_TAGS_BYTES + 1)
    except (OSError, ValueError) as e:   # URLError、逾時、連線被拒、重新導向(HTTPError)皆為 OSError;非本機主機為 ValueError
        raise OllamaUnavailable(type(e).__name__) from None
    if len(data) > MAX_TAGS_BYTES:
        raise OllamaUnavailable("回應過大")
    try:
        models = json.loads(data.decode("utf-8"))["models"]
    except (UnicodeDecodeError, ValueError, RecursionError, KeyError, TypeError):
        raise OllamaUnavailable("回應格式不對") from None
    if not isinstance(models, list):
        raise OllamaUnavailable("回應格式不對")
    return [m["name"] for m in models if isinstance(m, dict) and _good_model_name(m.get("name"))]


def _tags(fetch: Callable[[], list]) -> list[str]:
    """呼叫查詢函式並整理清單(去重、排序、濾掉不合格名稱);任何失敗一律轉成 OllamaUnavailable。"""
    try:
        names = fetch()
    except OllamaUnavailable:
        raise
    except Exception as e:
        print(f"studio_server:查詢 Ollama 模型清單失敗({type(e).__name__})", file=sys.stderr)
        raise OllamaUnavailable(type(e).__name__) from None
    if not isinstance(names, (list, tuple, set, frozenset)):
        raise OllamaUnavailable("回應格式不對")
    return sorted({n for n in names if _good_model_name(n)})


def _config_view() -> dict:
    """GET /api/config 的回應:settings.load() + meta 提示;字串一律 scrub。"""
    cfg = settings.load()
    out = {k: v for k, v in cfg.items() if k != "meta"}
    out["meta"] = {} if cfg.get("_warnings") else settings.load_meta()
    return _scrub_obj(out)


def _installed_tools() -> dict:
    mflux = Path(taiwan_ai.MFLUX)
    return {
        "mflux": {"installed": mflux.is_file() and os.access(mflux, os.X_OK)},
        "breezyvoice": {"installed": (Path(taiwan_ai.BREEZY) / ".venv").is_dir()},
    }


def create_app(runner: Callable | None = None, token: str | None = None,
               static_dir: Path | None = None, root: Path | None = None,
               ollama_tags: Callable[[], list] | None = None) -> FastAPI:
    """建立 app。runner 預設為 executor.run(測試可換替身);token 預設每次隨機產生;
    root 為 repo 根目錄(`outputs/`、`workflows/` 所在處),預設本檔所在目錄,測試可指到暫存目錄;
    ollama_tags 為取得 Ollama 已下載模型清單的函式(預設 fetch_ollama_tags,測試可換替身;
    失敗時應丟 OllamaUnavailable)。設定資料庫的位置由 settings.ROOT 決定。"""
    token = token or secrets.token_urlsafe(32)
    tags_fn = ollama_tags or fetch_ollama_tags
    static_dir = Path(static_dir) if static_dir is not None else STATIC_DIR
    root = Path(root) if root is not None else ROOT
    run_fn = runner or executor.run
    runs = Runs()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token = token

    @app.get("/")
    def index():
        page = static_dir / "index.html"
        text = page.read_text(encoding="utf-8") if page.is_file() and not page.is_symlink() else PLACEHOLDER_INDEX
        text = text.replace(TOKEN_PLACEHOLDER, html.escape(token, quote=True))
        return HTMLResponse(text, headers={"Cache-Control": "no-store"})

    @app.get("/static/{rel:path}")
    def static(rel: str):
        path = _static_file(static_dir, rel)
        if path is None:
            return JSONResponse({"error": "找不到檔案"}, status_code=404)
        return FileResponse(path)

    @app.get("/api/nodes")
    def api_nodes():
        return {"nodes": [s.spec_dict() for s in nodes.REGISTRY.values()],
                "port_types": sorted(nodes.PORT_TYPES)}

    @app.post("/api/run")
    async def api_run(request: Request):
        body = await _read_body(request)
        if body is None:
            return _bad(f"請求內容超過 {MAX_BODY // (1024 * 1024)} MB", 413)
        try:
            graph = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            return _bad("請求內容不是合法的 JSON")
        errors = executor.validate(graph)
        if errors:
            return JSONResponse(_error_body(errors), status_code=400)
        run = runs.start()
        if run is None:
            return JSONResponse({"error": "已有流程在執行中,請等它結束或先停止"}, status_code=409)
        threading.Thread(target=_run_thread, args=(runs, run, run_fn, graph),
                         name=f"run-{run.id}", daemon=True).start()
        return {"run_id": run.id}

    @app.get("/api/run/{run_id}/events")
    def api_events(run_id: str, request: Request):
        run = runs.get(run_id)
        if run is None:
            return JSONResponse({"error": "找不到這次執行"}, status_code=404)
        last = request.headers.get("last-event-id", "")
        start = int(last) + 1 if last.isdigit() else 0

        async def stream():
            idx = start
            last_sent = time.monotonic()
            while True:
                new, done = run.snapshot(idx)
                for ev in new:
                    yield f"id: {idx}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    idx += 1
                    last_sent = time.monotonic()
                if new:
                    continue
                if done:
                    return
                if time.monotonic() - last_sent > SSE_KEEPALIVE_SECONDS:
                    yield ": keepalive\n\n"
                    last_sent = time.monotonic()
                await asyncio.sleep(SSE_POLL_SECONDS)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.post("/api/run/{run_id}/cancel")
    def api_cancel(run_id: str):
        run = runs.get(run_id)
        if run is None:
            return JSONResponse({"error": "找不到這次執行"}, status_code=404)
        run.cancel.set()
        return {"run_id": run.id, "cancelled": True}

    @app.post("/api/upload")
    async def api_upload(request: Request):
        ext = _upload_ext(request.query_params.get("name"))
        if ext is None:
            return _err("不支援此檔案類型", 400)
        limit = MAX_UPLOAD
        length = request.headers.get("content-length")
        if length is not None and length.isdigit() and int(length) > limit:
            return _err(f"檔案超過 {limit // (1024 * 1024)} MB 上限", 413)
        try:
            updir = _safe_dir(root, "outputs", "uploads", create=True)
        except OSError as e:
            print(f"studio_server:上傳目錄不安全({type(e).__name__})", file=sys.stderr)
            return _err("上傳目錄不可用", 500)
        tmp = updir / f".upload-{uuid.uuid4().hex}.part"
        fd = _open_excl(tmp)
        size = 0
        try:
            with os.fdopen(fd, "wb") as f:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > limit:
                        return _err(f"檔案超過 {limit // (1024 * 1024)} MB 上限", 413)
                    f.write(chunk)
            problem = _check_upload(ext, tmp, size)
            if problem:
                return _err(problem, 400)
            final = _link_excl(tmp, lambda: updir / f"{uuid.uuid4().hex}{ext}")
        finally:
            tmp.unlink(missing_ok=True)   # 成功時最終檔是另一個硬連結,暫存名一律移除
        rel = f"uploads/{final.name}"
        return {"file_id": rel, "kind": UPLOAD_KINDS[ext], "url": f"/files/outputs/{rel}"}

    @app.get("/files/{rel:path}")
    def files(rel: str):
        path = _served_file(root, rel)
        if path is None:
            return _err("找不到檔案", 404)
        ctype = SERVE_TYPES.get(path.suffix.lower())
        if ctype is None:
            return FileResponse(path, media_type="application/octet-stream", filename=path.name,
                                content_disposition_type="attachment")
        return FileResponse(path, media_type=ctype, filename=path.name, content_disposition_type="inline")

    def _workflow_path(name: str, create: bool) -> Path:
        return _safe_dir(root, "workflows", create=create) / f"{name}.json"

    @app.get("/api/workflows/{name}")
    def api_workflow_get(name: str):
        if not _valid_name(name):
            return _err("工作流程名稱只能用英數字、底線、連字號,最多 64 字", 400)
        try:
            path = _workflow_path(name, create=False)
        except OSError:
            return _err("找不到這個工作流程", 404)
        if path.is_symlink() or not path.is_file():
            return _err("找不到這個工作流程", 404)
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            with os.fdopen(os.open(path, flags), "rb") as f:
                data = f.read(MAX_BODY + 1)
            if len(data) > MAX_BODY:
                return _err("工作流程檔過大", 422)
            graph = json.loads(data.decode("utf-8"))
        except FileNotFoundError:
            return _err("找不到這個工作流程", 404)
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            return _err("工作流程檔內容損壞,無法讀取", 422)
        return {"name": name, "graph": graph}

    @app.put("/api/workflows/{name}")
    async def api_workflow_put(name: str, request: Request):
        if not _valid_name(name):
            return _err("工作流程名稱只能用英數字、底線、連字號,最多 64 字", 400)
        body = await _read_body(request)
        if body is None:
            return _bad(f"請求內容超過 {MAX_BODY // (1024 * 1024)} MB", 413)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            return _bad("請求內容不是合法的 JSON")
        if not isinstance(payload, dict) or "graph" not in payload:
            return _bad("請求內容必須是 {\"graph\": 流程圖, \"overwrite\": 是/否}")
        overwrite = payload.get("overwrite", False)
        if not isinstance(overwrite, bool):
            return _bad("overwrite 必須是 true 或 false")
        graph = payload["graph"]
        errors = executor.validate(graph)
        if errors:
            return JSONResponse(_error_body(errors), status_code=400)
        try:
            path = _workflow_path(name, create=True)
        except OSError as e:
            print(f"studio_server:工作流程目錄不安全({type(e).__name__})", file=sys.stderr)
            return _err("工作流程目錄不可用", 500)
        if not overwrite and os.path.lexists(path):
            return _err("已有同名的工作流程;確認要覆寫請帶 overwrite:true", 409)
        data = json.dumps(graph, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        tmp = path.parent / f".{name}.{uuid.uuid4().hex}.tmp"
        try:
            with os.fdopen(_open_excl(tmp, 0o644), "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            if overwrite:
                os.replace(tmp, path)
            else:
                try:
                    os.link(tmp, path)   # 排他建立:這段期間別人先存了同名檔就 409
                except FileExistsError:
                    return _err("已有同名的工作流程;確認要覆寫請帶 overwrite:true", 409)
        except OSError as e:
            print(f"studio_server:工作流程寫入失敗({type(e).__name__})", file=sys.stderr)
            return _err("工作流程存檔失敗", 500)
        finally:
            tmp.unlink(missing_ok=True)
        return {"name": name, "saved": True}

    @app.get("/api/config")
    def api_config_get():
        return JSONResponse(_config_view(), headers={"Cache-Control": "no-store"})

    @app.put("/api/config")
    async def api_config_put(request: Request):
        body = await _read_body(request, MAX_CONFIG_BODY)
        if body is None:
            return _err(f"請求內容超過 {MAX_CONFIG_BODY // 1024} KB", 413)
        try:
            cfg = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            return JSONResponse({"errors": ["請求內容不是合法的 JSON"]}, status_code=422)
        if not isinstance(cfg, dict):
            return JSONResponse({"errors": ["設定必須是 JSON 物件"]}, status_code=422)
        cfg = {k: v for k, v in cfg.items() if k not in CONFIG_EXTRA_KEYS}
        errors = settings.validate(cfg, None)
        if errors:
            return JSONResponse({"errors": _scrub_obj(errors)}, status_code=422)
        try:
            available = await run_in_threadpool(_tags, tags_fn)
        except OllamaUnavailable:
            return _err(OLLAMA_DOWN + ";設定未儲存,請確認 Ollama 已啟動後再試", 503)
        errors = settings.validate(cfg, available)
        if errors:
            return JSONResponse({"errors": _scrub_obj(errors)}, status_code=422)
        try:
            await run_in_threadpool(settings.save, cfg)
        except ValueError as e:   # 路徑不安全、資料庫不是 SQLite 或版本不明:不寫入
            msgs = e.args[0] if e.args and isinstance(e.args[0], list) else []
            print("studio_server:設定儲存被拒(ValueError)", file=sys.stderr)
            return JSONResponse({"error": "設定儲存失敗",
                                 "errors": _scrub_obj([str(m) for m in msgs])}, status_code=500)
        except Exception as e:
            print(f"studio_server:設定儲存失敗({type(e).__name__})", file=sys.stderr)
            return _err("設定儲存失敗", 500)
        view = await run_in_threadpool(_config_view)
        return JSONResponse({"saved": True, "config": view}, headers={"Cache-Control": "no-store"})

    @app.get("/api/models")
    def api_models():
        try:
            ollama = {"reachable": True, "models": _tags(tags_fn)}
        except OllamaUnavailable:
            ollama = {"reachable": False, "models": [], "error": OLLAMA_DOWN}
        body = {"ollama": ollama, **_installed_tools(),
                "voices": settings.voice_options(settings.MAX_VOICE_OPTIONS)}
        return JSONResponse(_scrub_obj(body), headers={"Cache-Control": "no-store"})

    app.add_middleware(SecurityMiddleware, token=token)
    return app


def _import_uvicorn():
    import uvicorn
    return uvicorn


def main() -> None:
    uvicorn = _import_uvicorn()
    app = create_app()
    print(f"Podcast 工作室:http://{HOST}:{PORT}", file=sys.stderr)
    uvicorn.run(app, host=HOST, port=PORT, proxy_headers=False, server_header=False,
                log_level="info")


if __name__ == "__main__":
    main()
