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

import executor  # noqa: E402
import nodes  # noqa: E402

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


async def _read_body(request: Request) -> bytes | None:
    """串流讀取本文;超過 MAX_BODY 回 None。"""
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > MAX_BODY:
        return None
    buf = bytearray()
    async for chunk in request.stream():
        buf.extend(chunk)
        if len(buf) > MAX_BODY:
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


def create_app(runner: Callable | None = None, token: str | None = None,
               static_dir: Path | None = None) -> FastAPI:
    """建立 app。runner 預設為 executor.run(測試可換替身);token 預設每次隨機產生。"""
    token = token or secrets.token_urlsafe(32)
    static_dir = Path(static_dir) if static_dir is not None else STATIC_DIR
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
