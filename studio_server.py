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
                                 (撞名重取,不覆寫、不跟隨符號連結)。DIR-5 修正 4:暫存檔、內容檢查、
                                 硬連結與刪除一律相對 `outputs/uploads/` 的同一個目錄 fd(safe_fs)。
                                 回 200 {"file_id": "uploads/<uuid>.<ext>", "kind": "text"|"image"|"audio",
                                         "url": "/files/outputs/uploads/<uuid>.<ext>"}
                                 `file_id` 相對於 `outputs/`,可直接當 file-input 節點的 `file` 參數。
  GET  /files/{path}             path 相對於 repo 根目錄,且第一層必須是 `outputs`
                                 (與 SSE 事件裡的 `outputs/…` 相同)。路徑由框架 URL 解碼一次,
                                 切成路徑元件後自 repo 根以 dir_fd 逐層 O_NOFOLLOW 開啟(DIR-5 修正 4,
                                 不再 resolve() 後以路徑重開),回應直接讀該 fd(支援單一 Range)。
                                 絕對路徑、任一層以 `.` 開頭(含 `..`、`.heavy.lock`、上傳暫存檔)、
                                 任一層是符號連結(含指向 outputs/ 內者)、不是一般檔案 → 一律 404。
                                 已知媒體型別 inline 送出;文字檔 `text/plain; charset=utf-8`;
                                 其他一律 `application/octet-stream` + `Content-Disposition: attachment`。
  GET  /api/workflows/{name}     讀 `workflows/<name>.json` → {"name", "graph"};不存在 404;內容壞掉 422
  PUT  /api/workflows/{name}     本文 {"graph": 流程圖, "overwrite": true|false(選填,預設 false)};
                                 名稱(GET 同)視為 Unicode 字串逐字檢查,每個字必須是中英日白名單字元
                                 (ASCII 英數、`-`、`_`、CJK 統一漢字與擴充 A、平假名、片假名、「ー」、
                                 「々」),1~64 字,否則 400(使用者 2026-10-26 指示:開放中文、只採中英日);
                                 graph 必須通過 executor.validate(不合格 400,格式同 /api/run);
                                 本文上限 MAX_BODY(413);已存在且未帶 overwrite:true → 409;
                                 已有只差大小寫(casefold 相等)的其他名稱 → 一律 409,不論 overwrite。
                                 寫入同目錄暫存檔後:不覆寫時以 `os.link` 排他建立,覆寫時 `os.replace`
                                 (原子替換;既有的符號連結只被替換,不寫入其目標);列舉、暫存、
                                 連結 / 取代與刪除都相對 `workflows/` 的同一個目錄 fd(DIR-5 修正 4)。
  `outputs/`、`outputs/uploads/`、`workflows/` 自 repo 根以 dir_fd 逐層 O_NOFOLLOW 開啟(safe_fs);
  不符時上傳與存檔回 500(本文不含路徑)、讀取回 404,且不建立任何東西。

設定 API(T9;模型設定一律只經 `settings.load()` / `save()` / `validate()` / `load_meta()`
/ `voice_options()`,本檔不直接開啟設定資料庫或一次性匯入來源):

  GET  /api/config               `settings.load()` 的結果(roles、review_seats、voice_defaults,讀取失敗時另有
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

專案 API(CHG-20261022-04 T6;資料模型見 project.py 檔頭,專案目錄由 `project.ROOT` 決定):

  共通:{p} 為專案名稱(`names.py` 中英日白名單,1~64 字;含 `.`、`..`、`%2e%2e`、`/` 一律 400)。
  本文一律 JSON 物件、上限 MAX_PROJECT_BODY(超過 413、不是 JSON 物件 422、不認得的欄位 422)。
  錯誤回應 {"error": 繁中訊息, "errors"?: [訊息…], "segments"?: [片段 id…]},一律經 scrub()、不含本機絕對路徑:
  400 名稱不合規則 / 刪除未確認 / 匯出前仍有未合成片段;404 專案、版本、片段、圖片、素材或上傳檔不存在
  (segment id、image id、asset id 不符 `^[0-9a-f]{8,32}$` 也是 404);409 名稱已存在 / 已有重型工作
  在執行;422 資料不合法(注音、停頓、語速、文字上限、非 WAV 取代音檔…);500 專案目錄不安全。
  「專案檢視」View = {"project": 專案 dict(project.Project.to_dict()),
                      "layout": timeline.layout() 結果(預覽排程用,欄位見 timeline.py 檔頭),
                      "forced_breaks": [文稿字元索引](目前的強制停頓位置;改文稿時帶回 PUT 以保留)}
  修改類端點一律「載入 → 修改 → 存檔」(存檔前舊版移入 history/),回 View + {"version": 這次移入
  history 的版本 id 或 null};同一專案的修改在本行程內序列化。

  GET    /api/projects                 {"projects": [{"name", "updated": 專案檔修改時間 epoch 秒}]}
  POST   /api/projects                 {"name", "script"?} → 201 View;文稿依 project.split_segments 切成片段;
                                       語速與停頓預設取自 `voice_defaults()`(T8:設定資料庫的 voice_defaults)
  GET    /api/projects/{p}             View
  PUT    /api/projects/{p}             存檔:{"script"?, "forced_breaks"?, "defaults"?}(皆可省略;空本文 = 原樣
                                       再存一次)。帶 script 或 forced_breaks 時重新分句:文字相同的片段依序
                                       配對沿用 id、停頓、語速、標註與音檔,其他為新片段(沒有音檔);
                                       forced_breaks 省略時為 [](要保留強制停頓請帶回 View 的 forced_breaks)。
                                       defaults 為部分更新(鍵同 project.DEFAULTS)
  DELETE /api/projects/{p}             本文 {"confirm": true}(缺少或不是 true → 400);移到
                                       `projects/.trash/<名稱>-<時間戳>-<4 碼>/` → {"trashed": true, "name", "trash"};
                                       該專案正在合成或匯出 → 409
  GET    /api/projects/{p}/history     {"versions": [版本 id(舊到新)]}
  POST   /api/projects/{p}/restore     {"version"} → View + {"version", "restored"};還原 = 一次存檔
                                       (目前版本先移入 history,history 因此多一個檔)
  PATCH  /api/projects/{p}/segments/{id}
                                       {"text"?, "pause_after"?, "speed"?, "annotations"?, "split_at"?,
                                        "merge_next"?, "audio_file"?, "audio"?}(至少一個)→ View(+ "new_segment" /
                                       "removed_segment"):
                                       text:不可空、不可含換行,同時改寫文稿中該句的位置;文字改變時標註清空
                                         (同一請求另帶 annotations 除外),**自錄音檔(audio 沒有 key)一併清除**
                                         ——錄音已不符新文字,改由合成產生;合成音檔保留,由快取鍵判定需重合成。
                                       annotations:[{"index", "zhuyin"}](聲調符號格式;非法 → 422);
                                         改標註時自錄音檔同樣清除。
                                       pause_after / speed:null 或數值(0~5 秒、0.5~2.0 倍,超出 422)。
                                       split_at:句內字元索引,在該字之後強制停頓切成兩句(前句 break=forced;
                                         兩句都沒有音檔);merge_next:true 把強制停頓切出的這句與下一句合併。
                                       audio_file:`/api/upload` 回傳的 file_id,以自錄音檔取代這一句(修訂 3,R3-6):
                                         只看前 12 位元組,必須是 RIFF/WAVE,否則 422「取代語音只接受 WAV 音檔」、
                                         片段不變;長度以 media.wav_seconds() 取得(不轉檔);複製成
                                         `audio/<新 id>.wav`,audio = {"id", "seconds"}(沒有 key,合成不覆蓋)。
                                         配額(DIR-5 修正 2):複製前以磁碟實際用量計算——該專案 `assets/` 與
                                         `audio/` 內一般檔的位元組合計 + 新檔大小超過 project.MAX_ASSET_BYTES
                                         → 413,片段不變;舊的自錄檔不刪(history 版本可能仍引用)。
                                       audio:只能為 null(清除音檔,改回由合成產生)。
  POST   /api/projects/{p}/assets      {"file_id", "label"?} → View + {"asset": {"id", "kind", "ext", "bytes",
                                       "label"?}};把上傳檔複製到 `assets/<新 id>.<副檔名>`(素材庫用);
                                       與自錄音檔共用同一配額(磁碟用量超過 → 413)
  POST   /api/projects/{p}/images      {"asset": 圖片素材 id} 或 {"file_id": 上傳的圖片}(擇一),"start"?(預設 0)、
                                       "end"?(null = 到結尾)→ View + {"image": 新圖片 id}
  PATCH  /api/projects/{p}/images/{id} {"start"?, "end"?} → View
  DELETE /api/projects/{p}/images/{id} → View;只移除時間軸項目,不刪素材檔
  GET    /api/projects/{p}/media/{id}  只依 id 解析:專案素材 → `assets/<id>.<ext>`、片段音檔 → `audio/<id>.wav`;
                                       自 repo 根目錄以 dir_fd 逐層 O_NOFOLLOW 開檔、fstat 確認是一般檔,回應直接
                                       讀該 fd(不再以路徑重開,DIR-5 修正 2,RS-15);任一層是符號連結、他專案的 id、
                                       不存在 → 404;支援單一 Range(206 / 416);只送 MEDIA_TYPES 的副檔名,帶 nosniff。送的是合成原檔
                                       (變速前);預覽以 `<audio>.playbackRate = 語速`(保留音高)播放。
  POST   /api/projects/{p}/synth       → {"run_id", "events": "/api/run/<run_id>/events"};事件(SSE,格式同 /api/run):
                                       {"type": "waiting"}、{"type": "segment", "id", "seconds", "cached", "audio"}(每句完成)、
                                       {"type": "error", "message"}、
                                       {"type": "finished", "status": "ok"|"error", "synthesized", "cached", "saved"}。
                                       完成(含中途失敗)時把已合成的句子寫回專案:只更新同 id 且合成文字未變、
                                       期間沒有改成自錄音檔的片段(整批只存檔一次,history 最多多一版)。
                                       合成中途可試聽(DIR-5 統整 M-1):每句完成後,該句音檔在記憶體中疊到
                                       GET /api/projects/{p}、修改類端點回傳的 View(含 layout)與 media 端點上
                                       (同樣只疊同 id、合成文字未變、不是自錄音檔的片段;不存檔),結束寫回後清除。
  POST   /api/projects/{p}/render      匯出前檢查:沒有音檔或快取鍵已過期(文字改過)的片段 → 400
                                       {"error", "segments": [片段 id]};通過 → {"run_id", "events"},事件:
                                       {"type": "waiting"}、{"type": "render", "status": "started"}、
                                       {"type": "error", "message", "segments"}、
                                       {"type": "finished", "status": "ok", "mp4": "outputs/…mp4", "srt": "outputs/…srt"}
                                       (下載走 `/files/<mp4>`)。
  POST   /api/annotate                 {"text": 1~500 字}(超過 422)→ {"text", "zhuyin": [每字注音或 null]};
                                       同步執行,breezy_worker annotate 在 heavy_lock 內、逾時 ANNOTATE_TIMEOUT 秒;
                                       worker 失敗或逾時 → 500 {"error": 繁中訊息}。
  synth、render、annotate 與 /api/run 共用同一個「執行中」旗標(Runs.active),第二個一律 409。
  伺服器啟動(main)時呼叫 `project.startup_cleanup(now)`。`projects/` 不經 `/files/*` 提供。

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
import functools
import hmac
import html
import inspect
import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402

import executor  # noqa: E402
import fusion  # noqa: E402
import heavy_lock  # noqa: E402
import media  # noqa: E402
import names  # noqa: E402
import nodes  # noqa: E402
import project as pj  # noqa: E402
import safe_fs  # noqa: E402
import settings  # noqa: E402
import taiwan_ai  # noqa: E402
import timeline  # noqa: E402
import tts_service  # noqa: E402

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
# 工作流程名稱:中英日白名單,規則與專案名稱共用,單一來源在 `names.py`(CHG-20261022-04 T2 移出;
# 使用者 2026-10-26 指示開放中文、「只採最低限度 中英日即可」)。
MAX_WORKFLOW_NAME = names.MAX_NAME
WORKFLOW_NAME_ASCII = names.NAME_ASCII
WORKFLOW_NAME_RANGES = names.NAME_RANGES
WORKFLOW_NAME_HINT = "工作流程名稱只能用中文、日文假名、英文字母、數字、底線、連字號,長度 1 到 64 字"
M4A_BRANDS = media.M4A_BRANDS
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


def _rel_parts(rel) -> list[str] | None:
    """URL 相對路徑 → 路徑元件;空、`.`、`..`、點開頭以外的不合法元件(含 NUL、反斜線、絕對路徑)回 None。"""
    if not isinstance(rel, str) or not rel or "\x00" in rel or "\\" in rel or rel.startswith("/"):
        return None
    parts = rel.split("/")
    if any(not p or p in (".", "..") for p in parts):
        return None
    return parts


def _open_static(static_dir: Path, rel: str) -> tuple[int, int, str] | None:
    """`/static/{rel}`:自 static/ 以 dir_fd 逐層 O_NOFOLLOW 開啟一般檔,回傳 (fd, 位元組數, 檔名);不合規則回 None。

    (DIR-5 修正 4)不再 resolve() 後以路徑重開;static/ 內的符號連結一律不跟隨。"""
    parts = _rel_parts(rel)
    if parts is None:
        return None
    try:
        fd = safe_fs.open_path(static_dir, parts)
    except (OSError, ValueError):
        return None
    return fd, os.fstat(fd).st_size, parts[-1]


# ---------- 上傳、檔案服務、工作流程(T8) ----------

class UnsafePath(OSError):
    """repo 內的資料目錄是符號連結或解析到別處。訊息不含路徑,可安全記錄。"""


def _open_dir(root: Path, *parts: str, create: bool) -> int:
    """回傳 root/parts 目錄的 fd(呼叫端關閉);自 root 以 dir_fd 逐層 O_NOFOLLOW | O_DIRECTORY 開啟。

    create=True 時不存在的層會建立(建立後同樣以 O_NOFOLLOW 開啟);任一層是符號連結或不是目錄丟 UnsafePath,
    不存在且 create=False 丟 FileNotFoundError。(DIR-5 修正 4:取代先檢查路徑再以路徑操作的 `_safe_dir`。)"""
    try:
        return safe_fs.open_dir(root, parts, create=create)
    except safe_fs.UnsafePathError:
        raise UnsafePath("資料目錄是符號連結或不在 repo 內,不接受") from None


# 工作流程名稱檢查(規則見 `names.py`):合格回傳原名稱(檔名直接用它),不合格回 None;
# 只差大小寫的比對鍵為 `_fold_name()`。
_name_char_ok = names.name_char_ok
_normalize_name = names.normalize_name
_fold_name = names.fold_name


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


# 內容嗅探規則搬到 media.sniff_binary(DIR-5 統整 M-5,與「送到素材庫」節點共用;規則不變)
_sniff_binary = media.sniff_binary


def _check_upload(ext: str, fd: int, size: int) -> str | None:
    """回傳錯誤訊息;內容合格回 None。"""
    if size == 0:
        return "檔案是空的"
    if UPLOAD_KINDS[ext] == "text":
        if size > nodes.MAX_TEXT * 4 + 3:   # UTF-8 每字最多 4 位元組,加 BOM
            return f"文字檔超過 {nodes.MAX_TEXT} 字"
        try:
            text = os.pread(fd, size + 1, 0).decode("utf-8-sig")
        except UnicodeDecodeError:
            return "文字檔必須是 UTF-8 編碼"
        if "\x00" in text:
            return "文字檔內容不是純文字"
        if len(text) > nodes.MAX_TEXT:
            return f"文字檔超過 {nodes.MAX_TEXT} 字"
        return None
    head = os.pread(fd, media.SNIFF_BYTES, 0)
    if not _sniff_binary(ext, head):
        return f"檔案內容與副檔名 {ext} 不符"
    return None


def _open_excl(dirfd: int, name: str, mode: int = 0o600) -> int:
    """在 dirfd 內排他建立 name(O_CREAT | O_EXCL | O_NOFOLLOW,相對 dir_fd)。"""
    return safe_fs.create_excl(dirfd, name, mode)


def _link_excl(dirfd: int, src: str, make_dst: Callable[[], str], tries: int = 8) -> str:
    """在同一目錄 fd 內以 link(2) 排他建立目標(已存在——含符號連結——就換名重試);回傳目標名稱。"""
    for _ in range(tries):
        dst = make_dst()
        try:
            safe_fs.link(dirfd, src, dirfd, dst)
            return dst
        except FileExistsError:
            continue
    raise FileExistsError("無法取得不重複的檔名")


def _open_served(root: Path, rel: str) -> tuple[int, int, str] | None:
    """`/files/{rel}`:只接受 `outputs/` 之下、各層不以點開頭的路徑;自 repo 根以 dir_fd 逐層 O_NOFOLLOW
    開啟一般檔,回傳 (fd, 位元組數, 檔名),不合規則回 None。(DIR-5 修正 4)outputs/ 內的符號連結一律不跟隨。"""
    parts = _rel_parts(rel)
    if parts is None or parts[0] != "outputs" or len(parts) < 2 or any(p.startswith(".") for p in parts):
        return None
    try:
        fd = safe_fs.open_path(root, parts)
    except (OSError, ValueError):
        return None
    return fd, os.fstat(fd).st_size, parts[-1]


def _served_file(root: Path, rel: str) -> Path | None:
    """`/files/{rel}` 對應的檔案路徑(僅供檢查與測試;端點本身用 `_open_served()` 的 fd 回應)。"""
    got = _open_served(root, rel)
    if got is None:
        return None
    os.close(got[0])
    return Path(root).resolve().joinpath(*rel.split("/"))


def _content_disposition(kind: str, filename: str) -> str:
    """與 starlette FileResponse 相同的 Content-Disposition 格式(非 ASCII 檔名用 filename*=utf-8'')。"""
    quoted = quote(filename)
    if quoted != filename:
        return f"{kind}; filename*=utf-8''{quoted}"
    return f'{kind}; filename="{filename}"'


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


# ---------- 專案 API(CHG-20261022-04 T6) ----------

MAX_PROJECT_BODY = 1024 * 1024   # 專案 API 的 JSON 本文上限(文稿 50000 字以 \uXXXX 跳脫仍在範圍內)
MAX_ANNOTATE_CHARS = 500
ANNOTATE_TIMEOUT = 60            # 秒;等待重型工作鎖與 worker 執行各自的上限
COPY_CHUNK = 1 << 20
UPLOAD_ID_RE = re.compile(r"uploads/([0-9a-f]{32})\.([a-z0-9]{1,5})", re.ASCII)
MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
               "wav": "audio/wav", "mp3": "audio/mpeg", "m4a": "audio/mp4",
               "txt": "text/plain; charset=utf-8", "md": "text/plain; charset=utf-8"}
WAV_ONLY = "取代語音只接受 WAV 音檔"
BUSY = "已有語音合成、匯出、注音標註或流程在執行中,請等它結束"
SEGMENT_PATCH_KEYS = ("text", "pause_after", "speed", "annotations", "split_at", "merge_next",
                      "audio_file", "audio", "connect", "clarity", "word_clarity", "pitch", "fusions")
PROSODY_KEYS = ("connect", "clarity", "word_clarity", "pitch")   # CHG-20261029-01:改了只重做後製,不重新合成
NO_POST_EXTERNAL = "自錄音檔不做後製(連音、清晰度、字詞清晰度、語調只適用於合成的語音)"
MAX_POST_IDS = 2000
PROJECT_PUT_KEYS = ("script", "forced_breaks", "defaults")


class ApiError(Exception):
    """專案 API 的可預期錯誤;message 為繁中、不含本機路徑,extra 併入回應本文。"""

    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


class _NoChange(Exception):
    """mutate 的 change 沒有任何修改:不存檔(避免 history 多一版)。"""


class AnnotateError(RuntimeError):
    """注音標註失敗;訊息為繁中、不含本機絕對路徑。"""


def _project_error(e: "pj.ProjectError") -> JSONResponse:
    if isinstance(e, pj.UnsafePath):
        print("studio_server:專案目錄不安全(UnsafePath)", file=sys.stderr)
        return _err("專案目錄不可用", 500)
    status = 404 if isinstance(e, pj.ProjectNotFound) else 409 if isinstance(e, pj.ProjectExists) else 422
    errors = _scrub_obj([str(m) for m in e.errors]) or ["專案資料不正確"]
    return JSONResponse({"error": errors[0], "errors": errors}, status_code=status)


def _api_response(e: Exception) -> JSONResponse:
    if isinstance(e, ApiError):
        return JSONResponse({"error": scrub(e.message), **_scrub_obj(e.extra)}, status_code=e.status)
    return _project_error(e)


def _guard(fn):
    """把 ApiError、ProjectError 轉成 JSON 錯誤回應(保留簽章供 FastAPI 解析路徑參數)。"""
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def wrapper(*a, **kw):
            try:
                return await fn(*a, **kw)
            except (ApiError, pj.ProjectError) as e:
                return _api_response(e)
    else:
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            try:
                return fn(*a, **kw)
            except (ApiError, pj.ProjectError) as e:
                return _api_response(e)
    return wrapper


def _pname(p) -> str:
    """路徑或本文中的專案名稱;不合規則(含 `..`、`%2e%2e`、`/`)一律 400。"""
    try:
        return pj.check_name(p)
    except pj.ProjectError:
        raise ApiError(400, pj.NAME_HINT) from None


def _check_item_id(v, what: str) -> str:
    """伺服器產生的 id(`^[0-9a-f]{8,32}$`);其他輸入一律 404。"""
    if not isinstance(v, str) or not pj.ID_RE.fullmatch(v):
        raise ApiError(404, f"找不到這個{what}")
    return v


async def _json(request: Request, limit: int = MAX_PROJECT_BODY, allow_empty: bool = False) -> dict:
    body = await _read_body(request, limit)
    if body is None:
        raise ApiError(413, f"請求內容超過 {limit // 1024} KB")
    if allow_empty and not body.strip():
        return {}
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ApiError(422, "請求內容不是合法的 JSON") from None
    if not isinstance(data, dict):
        raise ApiError(422, "請求內容必須是 JSON 物件")
    return data


def _only_keys(data: dict, allowed) -> None:
    unknown = sorted(set(data) - set(allowed), key=str)
    if unknown:
        raise ApiError(422, f"不認得的欄位:{', '.join(map(str, unknown))[:200]}")


def _spans(script: str, segments: list) -> list:
    """各片段在文稿中的位置(依序往後找);找不到的片段為 None。"""
    out, cur = [], 0
    for s in segments:
        i = script.find(s["text"], cur)
        if i < 0:
            out.append(None)
            continue
        out.append((i, i + len(s["text"])))
        cur = i + len(s["text"])
    return out


def _forced_breaks(proj) -> list[int]:
    """目前片段的強制停頓位置(文稿字元索引,即 `split_segments` 的 forced_breaks)。"""
    return [sp[1] - 1 for s, sp in zip(proj.segments, _spans(proj.script, proj.segments))
            if sp and s.get("break") == "forced"]


def _view(proj) -> dict:
    """GET /api/projects/{p} 與各修改端點共用的回應本體。

    (CHG-20261029-01 T4)post_stale:後製過期的合成片段 id(前端開啟專案或改參數後據此呼叫 POST /post)。"""
    return {"project": proj.to_dict(), "layout": timeline.layout(proj), "forced_breaks": _forced_breaks(proj),
            "post_stale": [s["id"] for s in tts_service.stale_post(proj)]}


def _new_segment(text: str, brk: str) -> dict:
    return {"id": pj.new_id(), "text": text, "break": brk, "pause_after": None, "speed": None,
            "annotations": [], "audio": None, "connect": None, "clarity": None, "word_clarity": [],
            "pitch": None, "post": None, "fusions": []}


def _resplit(proj, script, forced) -> None:
    """文稿改寫後重新分句;文字相同的片段(依序配對)沿用 id、停頓、語速、標註與音檔,只有新句沒有音檔。"""
    pieces = pj.split_segments(script, forced)
    pool: dict[str, list] = {}
    for s in proj.segments:
        pool.setdefault(s["text"], []).append(s)
    segs = []
    for p in pieces:
        olds = pool.get(p["text"])
        if olds:
            s = dict(olds.pop(0))
            s["break"] = p["break"]
        else:
            s = _new_segment(p["text"], p["break"])
        segs.append(s)
    proj.script = script
    proj.segments = segs


def _external(audio) -> bool:
    """自錄取代的音檔(audio 沒有 key)。"""
    return bool(audio) and "key" not in audio


def _open_upload(root: Path, file_id) -> tuple[int, str]:
    """開啟 `/api/upload` 產生的檔案(`uploads/<32 碼>.<副檔名>`),回傳 (fd, 副檔名);以 O_NOFOLLOW 開啟。"""
    m = UPLOAD_ID_RE.fullmatch(file_id) if isinstance(file_id, str) else None
    if m is None or f".{m.group(2)}" not in UPLOAD_KINDS:
        raise ApiError(404, "找不到上傳的檔案")
    try:
        updir = _open_dir(root, "outputs", "uploads", create=False)
    except FileNotFoundError:
        raise ApiError(404, "找不到上傳的檔案") from None
    except OSError:
        print("studio_server:上傳目錄不安全", file=sys.stderr)
        raise ApiError(500, "上傳目錄不可用") from None
    with safe_fs.closing(updir):
        try:
            fd = safe_fs.open_file(updir, f"{m.group(1)}.{m.group(2)}")
        except OSError:
            raise ApiError(404, "找不到上傳的檔案") from None
    return fd, m.group(2)


def _copy_in(fd: int, name: str, sub: str, ext: str) -> tuple[str, str, int]:
    """把已開啟的檔案複製成專案 `<sub>/<新 id>.<ext>`(相對子目錄 fd 排他建立,不覆寫);
    回傳 (id, 檔名, 位元組數)。(DIR-5 修正 4)目錄以 `project.open_subdir()` 逐層 O_NOFOLLOW 開啟。"""
    with safe_fs.closing(pj.open_subdir(name, sub, create=True)) as dfd:
        for _ in range(8):
            new = pj.new_id()
            fn = f"{new}.{ext}"
            try:
                out = _open_excl(dfd, fn, 0o644)
                break
            except FileExistsError:
                continue
        else:  # pragma: no cover - 隨機 id 連續撞名
            raise ApiError(500, "無法建立不重複的檔名")
        size = 0
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            with os.fdopen(out, "wb") as f:
                while True:
                    chunk = os.read(fd, COPY_CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    size += len(chunk)
        except BaseException:
            safe_fs.unlink(dfd, fn, missing_ok=True)
            raise
    return new, fn, size


def _media_file(name: str, aid: str, overlay: Callable | None = None) -> tuple[int, int, str]:
    """`/api/projects/{p}/media/{aid}`:只依 id 在該專案的素材清單或片段音檔中解析,
    開啟該專案 `assets/` 或 `audio/` 內的 `<id>.<ext>`,回傳 (fd, 位元組數, Content-Type);其他一律 404。

    開檔以 `project.open_subdir()`(自 repo 根目錄 dir_fd 逐層 O_NOFOLLOW)取得子目錄 fd,再以
    `safe_fs.open_file()`(O_NOFOLLOW + fstat 確認一般檔)開啟;呼叫端只讀這個 fd,不再以路徑重開
    (DIR-5 修正 2,RS-15;修正 4 改用共用的 safe_fs)。

    overlay(name, project):合成期間把本次已完成、尚未寫回的片段音檔疊上(DIR-5 統整 M-1),
    這些音檔 id 因此也可取用;開檔規則不變。"""
    _check_item_id(aid, "檔案")
    proj = pj.load(name)
    if overlay is not None:
        overlay(name, proj)
    found = next(((("assets", a["ext"])) for a in proj.assets if a["id"] == aid), None)
    if found is None and any((s.get("audio") or {}).get("id") == aid for s in proj.segments):
        found = ("audio", "wav")
    if found is None and any((s.get("post") or {}).get("id") == aid and pj.is_post_fresh(s)
                             for s in proj.segments):
        found = ("audio", "wav")   # CHG-20261029-01 D18:預覽播放後製檔
    if found is None or found[1] not in MEDIA_TYPES:
        raise ApiError(404, "找不到這個檔案")
    sub, ext = found
    try:
        with safe_fs.closing(pj.open_subdir(name, sub)) as dfd:
            fd = safe_fs.open_file(dfd, f"{aid}.{ext}")
    except (OSError, ValueError, pj.ProjectError):
        raise ApiError(404, "找不到這個檔案") from None
    return fd, os.fstat(fd).st_size, MEDIA_TYPES[ext]


RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _fd_response(fd: int, size: int, ctype: str, range_header: str | None, extra: dict | None = None):
    """以已開啟的 fd 串流回應(不以路徑重開);支援單一 Range(多段或格式不合則忽略,回整檔 200)。

    fd 一律由本函式負責關閉:包成檔案物件,串流結束、中斷或回應未送出時都會關閉。
    extra:額外標頭(預設 `Cache-Control: no-store`;/files、/static 以 {} 取代為不帶快取標頭)。"""
    f = os.fdopen(fd, "rb", buffering=0)
    headers = {**({"Cache-Control": "no-store"} if extra is None else extra), "Accept-Ranges": "bytes"}
    start, end, status = 0, size, 200
    m = RANGE_RE.fullmatch(range_header.strip()) if range_header else None
    if m and (m.group(1) or m.group(2)):
        a, b = m.group(1), m.group(2)
        if a:
            start = int(a)
            end = min(int(b) + 1, size) if b else size
            bad = start >= size or (b and int(b) < start)
        else:   # bytes=-n:最後 n 位元組
            n = int(b)
            start, bad = max(size - n, 0), n == 0
        if bad or size == 0:
            f.close()
            return JSONResponse({"error": "要求的範圍不在檔案之內"}, status_code=416,
                                headers={**headers, "Content-Range": f"bytes */{size}"})
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end - 1}/{size}"
    headers["Content-Length"] = str(end - start)

    def body():
        with f:
            pos = start
            while pos < end:
                chunk = os.pread(f.fileno(), min(COPY_CHUNK, end - pos), pos)
                if not chunk:
                    break
                pos += len(chunk)
                yield chunk
    return StreamingResponse(body(), status_code=status, media_type=ctype, headers=headers)


def _check_quota(name: str, incoming: int) -> None:
    """素材與自錄音檔的共同配額:規則在 `project.check_quota()`(DIR-5 修正 3 起與「送到素材庫」節點共用),
    超過 → 413。呼叫端須在專案鎖內(mutate)、複製前呼叫。"""
    try:
        pj.check_quota(name, incoming)
    except pj.QuotaExceeded as e:
        raise ApiError(413, str(e)) from None


def _voice_defaults() -> dict:
    """新專案的語速與停頓預設:設定資料庫的 `voice_defaults`(CHG-20261022-04 T8,設定頁「語音預設」)。

    經 `settings.load()` 讀取(鍵同 `project.DEFAULTS`:speed、pause_comma、pause_sentence、pause_paragraph;
    讀取失敗時 settings 已回程式內預設值)。內容不合專案模組的規則時改用 `project.DEFAULTS`。
    `create_app(voice_defaults=…)` 可換替身。
    """
    voice = settings.load().get("voice_defaults")
    if not isinstance(voice, dict) or set(voice) != set(pj.DEFAULTS):
        return dict(pj.DEFAULTS)
    try:
        return {"speed": pj.normalize_speed(voice["speed"]),
                **{k: pj.normalize_pause(voice[k]) for k in pj.DEFAULTS if k != "speed"}}
    except pj.ProjectError:
        return dict(pj.DEFAULTS)


def annotate_text(text: str, root: Path) -> list:
    """以 `breezy_worker.py annotate` 產生逐字注音(聲調符號格式,非中文字為 None)。

    輸入檔放 `outputs/.tmp/`(不屬任何專案;`/files/` 不提供點開頭目錄),用完即刪;在 `heavy_lock` 內
    執行(等鎖與 worker 各最多 ANNOTATE_TIMEOUT 秒);argv list、`env=taiwan_ai.BREEZY_ENV`、
    `cwd=tools/BreezyVoice`(同 tts_service)。失敗丟 AnnotateError(繁中、不含路徑)。
    """
    try:
        tdir = _open_dir(root, "outputs", ".tmp", create=True)
    except OSError:
        raise AnnotateError("✗ 暫存目錄不可用") from None
    # (DIR-5 修正 4)相對 .tmp/ 的 fd 排他建立後立即刪除名稱,只留匿名 fd;worker 以 `/dev/fd/<n>`
    # (pass_fds 繼承)讀取,全程沒有可被換成符號連結的路徑。
    fd = None
    try:
        with safe_fs.closing(tdir):
            fn = f"annotate-{uuid.uuid4().hex}.json"
            fd = safe_fs.create_excl(tdir, fn, 0o600, flags=os.O_RDWR)
            safe_fs.unlink(tdir, fn)
        os.write(fd, json.dumps({"text": text}, ensure_ascii=False).encode("utf-8"))
        os.lseek(fd, 0, os.SEEK_SET)
        argv = [str(Path(tts_service.BREEZY) / ".venv" / "bin" / "python"), str(tts_service.WORKER),
                "annotate", "--input", f"/dev/fd/{fd}"]
        with heavy_lock.heavy_lock(timeout=ANNOTATE_TIMEOUT):
            r = subprocess.run(argv, cwd=str(tts_service.BREEZY), env=taiwan_ai.BREEZY_ENV,
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, pass_fds=(fd,),
                               encoding="utf-8", errors="replace", timeout=ANNOTATE_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise AnnotateError("✗ 注音標註逾時,已終止") from None
    except TimeoutError:
        raise AnnotateError("✗ 其他重型工作進行中,請稍後再試") from None
    except OSError:
        raise AnnotateError("✗ 無法啟動注音標註(請確認 tools/BreezyVoice 已安裝)") from None
    finally:
        if fd is not None:
            os.close(fd)
    if r.returncode != 0:
        raise AnnotateError(tts_service._worker_message(r.returncode, r.stderr.splitlines()))
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    try:
        out = json.loads(lines[-1]) if lines else None
    except ValueError:
        out = None
    if (not isinstance(out, list) or len(out) != len(text)
            or any(z is not None and not isinstance(z, str) for z in out)):
        raise AnnotateError("✗ 注音標註結果格式不正確")
    return out


def _fusion_lexicon() -> dict:
    """合音詞庫(啟用者):{兩字詞: 合音注音},讀設定資料庫(CHG-20261029-01 T8,settings.fusion_lexicon)。"""
    return settings.fusion_lexicon()


FUSION_PREVIEW_TTL = 600          # 匯入預覽代號 10 分鐘過期(D30)
FUSION_PREVIEW_MAX = 32           # 記憶體內最多保留的預覽數
FUSION_MULTIPART_OVERHEAD = 16 * 1024
FUSION_WORD_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\U00020000-\U000323af]{2}")


def _multipart_file(content_type: str, body: bytes, field: str = "file") -> bytes | None:
    """以標準庫 email 解析 multipart/form-data,回傳欄位 field 的檔案內容(只在記憶體,不落地);沒有回 None。"""
    from email.parser import BytesParser
    from email.policy import HTTP
    if not content_type.lower().startswith("multipart/form-data"):
        return None
    msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body)
    if not msg.is_multipart():
        return None
    for part in msg.iter_parts():
        if part.get_param("name", header="content-disposition") == field:
            data = part.get_payload(decode=True)
            return data if isinstance(data, bytes) else b""
    return None


def _render_default(proj):
    return media.render_timeline(proj, timeline.layout(proj))


def _out_relative(path) -> str:
    """匯出檔的 `/files/` 相對路徑(`outputs/<檔名>`);不在輸出目錄內時只給檔名。"""
    p = Path(path)
    try:
        return "outputs/" + p.resolve().relative_to(Path(taiwan_ai.OUT).resolve()).as_posix()
    except (ValueError, OSError):
        return p.name


def startup_cleanup(now=None) -> dict:
    """伺服器啟動時呼叫 `project.startup_cleanup(now)`(修訂 2):清 `.trash/` 滿 30 天者與各專案 `tmp/` 殘留。
    失敗只記錄、不阻擋啟動;記錄不含本機路徑。"""
    now = now if now is not None else datetime.now(timezone.utc)
    try:
        result = pj.startup_cleanup(now)
    except Exception as e:
        print(f"studio_server:啟動清理失敗({type(e).__name__})", file=sys.stderr)
        return {}
    print(f"studio_server:啟動清理 回收區刪除 {len(result['trash_removed'])} 項、tmp 清除 "
          f"{result['tmp_cleared']} 項、警告 {len(result['warnings'])} 則", file=sys.stderr)
    return result


def create_app(runner: Callable | None = None, token: str | None = None,
               static_dir: Path | None = None, root: Path | None = None,
               ollama_tags: Callable[[], list] | None = None,
               synth: Callable | None = None, render: Callable | None = None,
               annotate: Callable[[str], list] | None = None,
               voice_defaults: Callable[[], dict] | None = None,
               post: Callable | None = None,
               fusion_lexicon: Callable[[], dict] | None = None) -> FastAPI:
    """建立 app。runner 預設為 executor.run(測試可換替身);token 預設每次隨機產生;
    root 為 repo 根目錄(`outputs/`、`workflows/` 所在處),預設本檔所在目錄,測試可指到暫存目錄;
    ollama_tags 為取得 Ollama 已下載模型清單的函式(預設 fetch_ollama_tags,測試可換替身;
    失敗時應丟 OllamaUnavailable)。設定資料庫的位置由 settings.ROOT 決定。
    專案 API(T6)的替身:synth(project, emit)預設 tts_service.synth;render(project) -> (mp4, srt)
    預設 media.render_timeline(project, timeline.layout(project));annotate(text) -> 注音陣列預設
    annotate_text(text, root);voice_defaults() 預設 _voice_defaults(T8:讀設定資料庫);
    post(project, segment_ids, emit) 預設 tts_service.post_process(CHG-20261029-01 T4);
    fusion_lexicon() -> {兩字詞: 合音注音}(只含啟用者)預設 _fusion_lexicon(CHG-20261029-01 T7)。
    專案目錄由 project.ROOT 決定(不是 root)。"""
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
        try:   # (DIR-5 修正 4)以 O_NOFOLLOW 開啟後讀同一個 fd,符號連結或非一般檔一律用預設頁
            with os.fdopen(safe_fs.open_path(static_dir, ["index.html"]), "rb") as f:
                text = f.read().decode("utf-8")
        except (OSError, ValueError):
            text = PLACEHOLDER_INDEX
        text = text.replace(TOKEN_PLACEHOLDER, html.escape(token, quote=True))
        return HTMLResponse(text, headers={"Cache-Control": "no-store"})

    @app.get("/static/{rel:path}")
    def static(rel: str, request: Request):
        got = _open_static(static_dir, rel)
        if got is None:
            return JSONResponse({"error": "找不到檔案"}, status_code=404)
        fd, size, fname = got
        ctype = mimetypes.guess_type(fname)[0] or "text/plain"
        return _fd_response(fd, size, ctype, request.headers.get("range"), extra={})

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
            updir = _open_dir(root, "outputs", "uploads", create=True)
        except OSError as e:
            print(f"studio_server:上傳目錄不安全({type(e).__name__})", file=sys.stderr)
            return _err("上傳目錄不可用", 500)
        # (DIR-5 修正 4)暫存檔、檢查、硬連結與刪除一律相對 uploads/ 的同一個目錄 fd
        tmp = f".upload-{uuid.uuid4().hex}.part"
        try:
            fd = safe_fs.create_excl(updir, tmp, 0o600, flags=os.O_RDWR)   # 讀寫:寫完以同一 fd 檢查內容
            size = 0
            try:
                with os.fdopen(fd, "wb") as f:
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > limit:
                            return _err(f"檔案超過 {limit // (1024 * 1024)} MB 上限", 413)
                        f.write(chunk)
                    f.flush()
                    problem = _check_upload(ext, f.fileno(), size)
                if problem:
                    return _err(problem, 400)
                final = _link_excl(updir, tmp, lambda: f"{uuid.uuid4().hex}{ext}")
            finally:
                safe_fs.unlink(updir, tmp, missing_ok=True)   # 成功時最終檔是另一個硬連結,暫存名一律移除
        finally:
            os.close(updir)
        rel = f"uploads/{final}"
        return {"file_id": rel, "kind": UPLOAD_KINDS[ext], "url": f"/files/outputs/{rel}"}

    @app.get("/files/{rel:path}")
    def files(rel: str, request: Request):
        got = _open_served(root, rel)
        if got is None:
            return _err("找不到檔案", 404)
        fd, size, fname = got
        ctype = SERVE_TYPES.get(Path(fname).suffix.lower())
        disposition = "inline" if ctype is not None else "attachment"
        return _fd_response(fd, size, ctype or "application/octet-stream", request.headers.get("range"),
                            extra={"Content-Disposition": _content_disposition(disposition, fname)})

    def _workflow_dir(create: bool) -> int:
        """`workflows/` 的目錄 fd(呼叫端關閉;DIR-5 修正 4)。"""
        return _open_dir(root, "workflows", create=create)

    def _case_variant(folder: int, name: str) -> str | None:
        """找出 `workflows/` 裡與 name 只差大小寫(casefold 相等、本身不同)的既有名稱。

        macOS 預設檔案系統不分大小寫,`Flow` 與 `flow` 會指到同一檔;Linux 則是兩個檔。
        為了兩邊行為一致,這種名稱一律回 409,請使用者用原名稱覆寫(不論 overwrite)。
        """
        key = _fold_name(name)
        with os.scandir(folder) as it:
            for entry in it:
                fn = entry.name
                if fn.startswith(".") or not fn.endswith(".json"):
                    continue
                stem = fn[:-len(".json")]
                if stem != name and _fold_name(stem) == key:
                    return stem
        return None

    @app.get("/api/workflows/{name}")
    def api_workflow_get(name: str):
        name = _normalize_name(name)
        if name is None:
            return _err(WORKFLOW_NAME_HINT, 400)
        try:
            wdir = _workflow_dir(create=False)
        except OSError:
            return _err("找不到這個工作流程", 404)
        try:
            with safe_fs.closing(wdir):
                fd = safe_fs.open_file(wdir, f"{name}.json")
        except OSError:
            return _err("找不到這個工作流程", 404)
        try:
            with os.fdopen(fd, "rb") as f:
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
        name = _normalize_name(name)
        if name is None:
            return _err(WORKFLOW_NAME_HINT, 400)
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
            wdir = _workflow_dir(create=True)
        except OSError as e:
            print(f"studio_server:工作流程目錄不安全({type(e).__name__})", file=sys.stderr)
            return _err("工作流程目錄不可用", 500)
        with safe_fs.closing(wdir):   # (DIR-5 修正 4)列舉、暫存、取代/連結、刪除都相對同一個目錄 fd
            try:
                variant = _case_variant(wdir, name)
            except OSError as e:
                print(f"studio_server:工作流程目錄無法列出({type(e).__name__})", file=sys.stderr)
                return _err("工作流程目錄不可用", 500)
            if variant is not None:
                return _err(f"已有只差大小寫的工作流程「{variant}」;要覆寫請改用這個名稱", 409)
            fn = f"{name}.json"
            if not overwrite and safe_fs.lstat(wdir, fn) is not None:
                return _err("已有同名的工作流程;確認要覆寫請帶 overwrite:true", 409)
            data = json.dumps(graph, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
            tmp = f".wf-{uuid.uuid4().hex}.tmp"   # 暫存檔名不含工作流程名稱
            try:
                with os.fdopen(_open_excl(wdir, tmp, 0o644), "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                if overwrite:
                    safe_fs.replace(wdir, tmp, wdir, fn)
                else:
                    try:
                        safe_fs.link(wdir, tmp, wdir, fn)   # 排他建立:這段期間別人先存了同名檔就 409
                    except FileExistsError:
                        return _err("已有同名的工作流程;確認要覆寫請帶 overwrite:true", 409)
            except OSError as e:
                print(f"studio_server:工作流程寫入失敗({type(e).__name__})", file=sys.stderr)
                return _err("工作流程存檔失敗", 500)
            finally:
                safe_fs.unlink(wdir, tmp, missing_ok=True)
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

    # ---------- 專案 API(CHG-20261022-04 T6;回應格式見檔頭) ----------

    synth_fn = synth or tts_service.synth
    render_fn = render or _render_default
    annotate_fn = annotate or (lambda text: annotate_text(text, root))
    defaults_fn = voice_defaults or _voice_defaults
    post_fn = post or tts_service.post_process
    lexicon_fn = fusion_lexicon or _fusion_lexicon
    plocks: dict[str, threading.Lock] = {}
    plocks_guard = threading.Lock()
    jobs: dict[str, str] = {}   # run_id → 執行合成 / 匯出中的專案名稱(比對鍵),刪除專案時檢查
    # 合成中途可試聽(DIR-5 統整 M-1):本次合成已完成、尚未寫回專案的片段音檔,只存在記憶體。
    # 專案比對鍵 → {片段 id: (合成當時的含標註文字, audio)};結束寫回(merge_audio)後清除。
    live_audio: dict[str, dict[str, tuple]] = {}   # (CHG-20261029-01)值為 (合成文字, audio, post)
    live_guard = threading.Lock()

    def overlay(name: str, proj):
        """把 live_audio 疊到剛載入的專案上(不存檔):只疊同 id、合成文字未變、不是自錄音檔的片段。"""
        with live_guard:
            done = dict(live_audio.get(names.fold_name(name)) or {})
        for s in proj.segments if done else ():
            got = done.get(s["id"])
            if got is None or _external(s.get("audio")) or pj.tts_text(s) != got[0]:
                continue
            s["audio"] = dict(got[1])
            post = got[2] if len(got) > 2 else None
            if post and pj.is_post_fresh({**s, "post": post}):
                s["post"] = dict(post)
        return proj

    def plock(name: str) -> threading.Lock:
        """同一專案的「讀—改—存」一律序列化(本行程內;跨行程的存檔另有 project.py 的檔案鎖)。"""
        with plocks_guard:
            return plocks.setdefault(names.fold_name(name), threading.Lock())

    def mutate(name: str, change) -> dict:
        """載入 → change(project, created) → 存檔(先移舊版入 history);失敗時刪除這次複製進來的檔案。

        (T8)載入到存檔全程經 `pj.update()` 持專案層級鎖,與「送到素材庫」節點的修改互相序列化。
        """
        created: list[tuple[str, str]] = []   # (子目錄, 檔名);刪除相對子目錄 fd(DIR-5 修正 4)
        with plock(name):
            try:
                proj, version, extra = pj.update(name, lambda proj: change(proj, created) or {})
            except BaseException:
                for sub, fn in created:
                    pj.unlink_in(name, sub, fn)
                raise
        return {**_view(overlay(name, proj)), "version": version, **extra}

    def start_job(name: str | None) -> Run:
        run = runs.start()
        if run is None:
            raise ApiError(409, BUSY)
        if name is not None:
            jobs[run.id] = names.fold_name(name)
        return run

    def end_job(run: Run) -> None:
        jobs.pop(run.id, None)
        _, done = run.snapshot(0)
        if not done:
            run.emit({"type": "finished", "status": "error"})
        runs.finish(run)

    @app.get("/api/projects")
    @_guard
    def api_projects():
        return JSONResponse({"projects": pj.list_projects()}, headers={"Cache-Control": "no-store"})

    @app.post("/api/projects")
    @_guard
    async def api_project_create(request: Request):
        data = await _json(request)
        _only_keys(data, ("name", "script"))
        name = _pname(data.get("name"))
        script = data.get("script", "")
        if not isinstance(script, str):
            raise ApiError(422, "script 必須是文字")

        def work():
            with plock(name):
                return _view(pj.create(name, script, defaults_fn()))
        return JSONResponse(await run_in_threadpool(work), status_code=201)

    @app.get("/api/projects/{p}")
    @_guard
    def api_project_get(p: str):
        name = _pname(p)
        return JSONResponse(_view(overlay(name, pj.load(name))), headers={"Cache-Control": "no-store"})

    @app.put("/api/projects/{p}")
    @_guard
    async def api_project_put(p: str, request: Request):
        name = _pname(p)
        data = await _json(request, allow_empty=True)
        _only_keys(data, PROJECT_PUT_KEYS)

        def change(proj, created):
            if "defaults" in data:
                d = data["defaults"]
                if not isinstance(d, dict):
                    raise ApiError(422, "defaults 必須是物件")
                proj.defaults = {**proj.defaults, **d}
            if "script" in data or "forced_breaks" in data:
                script = data.get("script", proj.script)
                forced = data.get("forced_breaks", [])
                if not isinstance(forced, list):
                    raise ApiError(422, "forced_breaks 必須是陣列")
                _resplit(proj, script, forced)
        return await run_in_threadpool(mutate, name, change)

    @app.delete("/api/projects/{p}")
    @_guard
    async def api_project_delete(p: str, request: Request):
        name = _pname(p)
        data = await _json(request, allow_empty=True)
        if data.get("confirm") is not True:
            raise ApiError(400, "刪除專案需要確認:請帶 {\"confirm\": true}")
        if names.fold_name(name) in jobs.values():
            raise ApiError(409, "這個專案正在合成或匯出,請等它結束再刪除")

        def work():
            with plock(name):
                return pj.trash(name, confirm=True)
        return {"trashed": True, "name": name, "trash": await run_in_threadpool(work)}

    @app.get("/api/projects/{p}/history")
    @_guard
    def api_project_history(p: str):
        return JSONResponse({"versions": pj.list_history(_pname(p))}, headers={"Cache-Control": "no-store"})

    @app.post("/api/projects/{p}/restore")
    @_guard
    async def api_project_restore(p: str, request: Request):
        name = _pname(p)
        data = await _json(request)
        _only_keys(data, ("version",))
        vid = data.get("version")

        def work():
            with plock(name):
                version = pj.restore(name, vid)
                return {**_view(overlay(name, pj.load(name))), "version": version, "restored": vid}
        return await run_in_threadpool(work)

    @app.patch("/api/projects/{p}/segments/{sid}")
    @_guard
    async def api_segment_patch(p: str, sid: str, request: Request):
        name = _pname(p)
        _check_item_id(sid, "片段")
        data = await _json(request)
        _only_keys(data, SEGMENT_PATCH_KEYS)
        if not data:
            raise ApiError(422, "沒有要修改的欄位")

        def change(proj, created):
            idx = next((i for i, s in enumerate(proj.segments) if s["id"] == sid), None)
            if idx is None:
                raise ApiError(404, "找不到這個片段")
            seg = dict(proj.segments[idx])
            out = {}
            if "audio_file" in data:
                fd, _ = _open_upload(root, data["audio_file"])
                try:
                    head = os.pread(fd, 12, 0)
                    if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
                        raise ApiError(422, WAV_ONLY)
                    _check_quota(name, os.fstat(fd).st_size)
                    aid, fn, _size = _copy_in(fd, name, "audio", "wav")
                    created.append(("audio", fn))
                    try:   # 長度讀自同一份上傳內容的 fd(與複製出的檔案位元組相同),不以路徑重開
                        seconds = media.wav_seconds(fd)
                    except ValueError as e:
                        raise ApiError(422, f"{WAV_ONLY}:{e}") from None
                finally:
                    os.close(fd)
                seg["audio"] = {"id": aid, "seconds": seconds}
            if "audio" in data:
                if data["audio"] is not None:
                    raise ApiError(422, "audio 只能設為 null(清除音檔,改回由合成產生)")
                seg["audio"] = None
            if "text" in data:
                text = data["text"]
                if not isinstance(text, str) or not text.strip() or any(c in pj.NEWLINES for c in text):
                    raise ApiError(422, "片段文字不可為空,也不可含換行")
                text = text.strip()
                if text != seg["text"]:
                    spans = _spans(proj.script, proj.segments)
                    if spans[idx] is not None:
                        a, b = spans[idx]
                        proj.script = proj.script[:a] + text + proj.script[b:]
                    seg["text"] = text
                    if "annotations" not in data:
                        seg["annotations"] = []
                    if _external(seg.get("audio")) and "audio_file" not in data:
                        seg["audio"] = None   # 自錄音檔已不符新文字:清除,改由合成產生
            if "annotations" in data:
                if seg.get("annotations") != data["annotations"] and _external(seg.get("audio")) \
                        and "audio_file" not in data:
                    seg["audio"] = None
                seg["annotations"] = data["annotations"]
            for key in ("pause_after", "speed"):
                if key in data:
                    seg[key] = data[key]
            if "text" in data and seg["text"] != proj.segments[idx]["text"]:
                if "word_clarity" not in data:
                    seg["word_clarity"] = []   # 字詞清晰度以字元索引記錄,文字改了即清除(同注音標註)
                if "fusions" not in data:
                    seg["fusions"] = []        # 合音同理(CHG-20261029-01 T7)
            if "fusions" in data:   # 合音改變送模型的文字:該句變髒、走既有 synth(D28)
                if seg.get("fusions") != data["fusions"] and _external(seg.get("audio")) \
                        and "audio_file" not in data:
                    seg["audio"] = None
                seg["fusions"] = data["fusions"]
            for key in PROSODY_KEYS:   # (CHG-20261029-01 T4)範圍驗證在 project.validate()
                if key in data:
                    if _external(seg.get("audio")) and data[key] not in (None, []):
                        raise ApiError(422, NO_POST_EXTERNAL)
                    seg[key] = data[key]
            # 先驗證單一片段(注音、停頓、語速不合法 → 422,不存檔)
            pj.Project.from_dict({**proj.to_dict(), "segments": [seg]})
            new_segments = [*proj.segments[:idx], seg, *proj.segments[idx + 1:]]
            if "split_at" in data:
                i = data["split_at"]
                if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(seg["text"]) - 1:
                    raise ApiError(422, "強制停頓位置必須在句子之內(不可在最後一個字之後)")
                first, second = seg["text"][:i + 1], seg["text"][i + 1:]
                if not first.strip() or not second.strip():
                    raise ApiError(422, "強制停頓會切出空白片段")
                lead = len(first) - len(first.lstrip())
                skip = len(second) - len(second.lstrip())
                anns = seg.get("annotations") or []
                a = {**seg, "text": first.strip(), "break": "forced", "pause_after": None, "audio": None,
                     "word_clarity": [], "pitch": None, "post": None, "fusions": [],
                     "annotations": [{"index": x["index"] - lead, "zhuyin": x["zhuyin"]}
                                     for x in anns if x["index"] <= i]}
                b = {**_new_segment(second.strip(), seg["break"]), "pause_after": seg.get("pause_after"),
                     "speed": seg.get("speed"), "connect": seg.get("connect"), "clarity": seg.get("clarity"),
                     "annotations": [{"index": x["index"] - (i + 1) - skip, "zhuyin": x["zhuyin"]}
                                     for x in anns if x["index"] > i]}
                new_segments[idx:idx + 1] = [a, b]
                out["new_segment"] = b["id"]
            elif data.get("merge_next") is True:
                if seg.get("break") != "forced" or idx + 1 >= len(proj.segments):
                    raise ApiError(422, "只有強制停頓切出的片段可以與下一句合併")
                nxt = proj.segments[idx + 1]
                shift = len(seg["text"])
                merged = {**seg, "text": seg["text"] + nxt["text"], "break": nxt["break"],
                          "pause_after": nxt.get("pause_after"), "audio": None,
                          "word_clarity": [], "pitch": None, "post": None, "fusions": [],
                          "annotations": (seg.get("annotations") or []) + [
                              {"index": x["index"] + shift, "zhuyin": x["zhuyin"]}
                              for x in nxt.get("annotations") or []]}
                new_segments[idx:idx + 2] = [merged]
                out["removed_segment"] = nxt["id"]
            elif "merge_next" in data and data["merge_next"] is not False:
                raise ApiError(422, "merge_next 只能是 true 或 false")
            proj.segments = new_segments
            return out
        return await run_in_threadpool(mutate, name, change)

    def add_asset(name: str, proj, created: list, file_id, label, kinds) -> dict:
        if label is not None and not isinstance(label, str):
            raise ApiError(422, "label 必須是文字")
        fd, ext = _open_upload(root, file_id)
        try:
            kind = UPLOAD_KINDS[f".{ext}"]
            if kind not in kinds:
                raise ApiError(422, "這個檔案類型不能放在這裡")
            _check_quota(name, os.fstat(fd).st_size)
            aid, fn, size = _copy_in(fd, name, "assets", ext)
        finally:
            os.close(fd)
        created.append(("assets", fn))
        asset = {"id": aid, "kind": kind, "ext": ext, "bytes": size}
        if label:
            asset["label"] = label
        proj.assets = [*proj.assets, asset]
        return asset

    @app.post("/api/projects/{p}/assets")
    @_guard
    async def api_asset_add(p: str, request: Request):
        name = _pname(p)
        data = await _json(request)
        _only_keys(data, ("file_id", "label"))

        def change(proj, created):
            return {"asset": add_asset(name, proj, created, data.get("file_id"), data.get("label"),
                                       ("image", "audio", "text"))}
        return await run_in_threadpool(mutate, name, change)

    @app.post("/api/projects/{p}/images")
    @_guard
    async def api_image_add(p: str, request: Request):
        name = _pname(p)
        data = await _json(request)
        _only_keys(data, ("asset", "file_id", "label", "start", "end"))
        if ("asset" in data) == ("file_id" in data):
            raise ApiError(422, "請指定 asset(既有圖片素材 id)或 file_id(上傳的圖片)其中之一")

        def change(proj, created):
            if "file_id" in data:
                aid = add_asset(name, proj, created, data["file_id"], data.get("label"), ("image",))["id"]
            else:
                aid = data["asset"]
                if not any(a["id"] == aid and a["kind"] == "image" for a in proj.assets):
                    raise ApiError(404, "找不到這個圖片素材")
            img = {"id": pj.new_id(), "asset": aid, "start": data.get("start", 0), "end": data.get("end")}
            proj.images = [*proj.images, img]
            return {"image": img["id"]}
        return await run_in_threadpool(mutate, name, change)

    @app.patch("/api/projects/{p}/images/{iid}")
    @_guard
    async def api_image_patch(p: str, iid: str, request: Request):
        name = _pname(p)
        _check_item_id(iid, "圖片")
        data = await _json(request)
        _only_keys(data, ("start", "end"))

        def change(proj, created):
            idx = next((i for i, m in enumerate(proj.images) if m["id"] == iid), None)
            if idx is None:
                raise ApiError(404, "找不到這張圖片")
            proj.images = [*proj.images[:idx], {**proj.images[idx], **data}, *proj.images[idx + 1:]]
        return await run_in_threadpool(mutate, name, change)

    @app.delete("/api/projects/{p}/images/{iid}")
    @_guard
    def api_image_delete(p: str, iid: str):
        name = _pname(p)
        _check_item_id(iid, "圖片")

        def change(proj, created):   # 只移除時間軸項目,不刪素材檔
            if not any(m["id"] == iid for m in proj.images):
                raise ApiError(404, "找不到這張圖片")
            proj.images = [m for m in proj.images if m["id"] != iid]
        return mutate(name, change)

    @app.get("/api/projects/{p}/media/{aid}")
    @_guard
    def api_media(p: str, aid: str, request: Request):
        fd, size, ctype = _media_file(_pname(p), aid, overlay)
        return _fd_response(fd, size, ctype, request.headers.get("range"))

    @app.post("/api/projects/{p}/synth")
    @_guard
    def api_synth(p: str):
        name = _pname(p)
        pj.load(name)   # 不存在 → 404(不佔執行旗標)
        run = start_job(name)
        key = names.fold_name(name)

        def work():
            done_ids: list[str] = []
            snapshot = None

            def emit(ev):
                if isinstance(ev, dict) and ev.get("type") == "segment":
                    sid = ev.get("id")
                    done_ids.append(sid)
                    seg = next((s for s in snapshot.segments if s["id"] == sid), None) if snapshot else None
                    audio = ev.get("audio") if isinstance(ev.get("audio"), dict) else (seg or {}).get("audio")
                    if seg is not None and isinstance(audio, dict) and isinstance(audio.get("id"), str) \
                            and pj.ID_RE.fullmatch(audio["id"]):
                        post = ev.get("post") if isinstance(ev.get("post"), dict) else None
                        with live_guard:   # 該句立即可在專案檢視、時間軸與 media 端點取用(不存檔)
                            live_audio.setdefault(key, {})[sid] = (pj.tts_text(seg), dict(audio), post)
                run.emit(ev)

            status, result = "error", {"synthesized": [], "cached": []}
            try:
                with plock(name):
                    snapshot = pj.load(name)
                result = synth_fn(snapshot, emit) or result
                done = set(done_ids)
                stale = [s["id"] for s in snapshot.segments if s["id"] in done and s.get("audio")
                         and not _external(s.get("audio")) and not pj.is_post_fresh(s)]
                if stale:   # (CHG-20261029-01 D20)非預設參數的片段接著後製(不進 heavy_lock)
                    post_fn(snapshot, stale, run.emit)
                status = "ok"
            except (tts_service.SynthError, pj.ProjectError) as e:
                run.emit({"type": "error", "message": str(e)})
            except Exception as e:
                print(f"studio_server:語音合成錯誤 {type(e).__name__}", file=sys.stderr)
                run.emit({"type": "error", "message": "✗ 語音合成失敗"})
            saved = 0
            try:   # 已完成的句子一律寫回專案(中途失敗也保留);整批只存一次,history 最多多一版
                saved = merge_audio(name, snapshot, done_ids)
            except pj.ProjectError as e:
                status = "error"
                run.emit({"type": "error", "message": f"合成結果無法寫回專案:{e}"})
            finally:
                with live_guard:
                    live_audio.pop(key, None)
            run.emit({"type": "finished", "status": status, "synthesized": result.get("synthesized", []),
                      "cached": result.get("cached", []), "saved": saved})

        threading.Thread(target=_job_thread, args=(work, run, end_job), name=f"synth-{run.id}",
                         daemon=True).start()
        return {"run_id": run.id, "events": f"/api/run/{run.id}/events"}

    def _post_for(cur: dict, done: dict):
        """合成 / 後製結果的 post 只在「目前片段」的後製鍵相符時才寫回(期間改了參數就保持過期)。"""
        post = done.get("post")
        if not post:
            return cur.get("post")
        probe = {**cur, "audio": done["audio"], "post": post}
        return dict(post) if pj.is_post_fresh(probe) else cur.get("post")

    def merge_post(name: str, snapshot, ids) -> int:
        """把後製結果(snapshot 的 post)寫回目前專案:只更新同 id、同音檔、後製鍵仍相符的片段;沒有變化不存檔。"""
        wanted = set(ids)
        by_id = {s["id"]: s for s in snapshot.segments if s["id"] in wanted and s.get("post")}
        if not by_id:
            return 0

        def change(cur, created):
            n = 0
            for s in cur.segments:
                w = by_id.get(s["id"])
                if w is None or _external(s.get("audio")) or s.get("audio") != w.get("audio"):
                    continue
                post = _post_for(s, w)
                if post != s.get("post"):
                    s["post"] = post
                    n += 1
            if not n:
                raise _NoChange()
            return {"n": n}
        try:
            return mutate(name, change)["n"]
        except _NoChange:
            return 0

    def merge_audio(name: str, snapshot, ids: list) -> int:
        """把合成結果寫回目前的專案:只更新同 id 且合成文字未變的片段;合成期間改成自錄音檔的不覆蓋。"""
        if snapshot is None or not ids:
            return 0
        wanted = set(ids)
        by_id = {s["id"]: s for s in snapshot.segments if s["id"] in wanted and s.get("audio")}
        with plock(name):
            cur = pj.load(name)
            n = 0
            for s in cur.segments:
                w = by_id.get(s["id"])
                if w is None or _external(s.get("audio")):
                    continue
                if pj.tts_text(s) != pj.tts_text(w):
                    continue
                post = _post_for(s, w)
                if s.get("audio") == w["audio"] and s.get("post") == post:
                    continue
                s["audio"] = dict(w["audio"])
                s["post"] = post
                n += 1
            if n:
                pj.save(cur)
        return n

    @app.post("/api/projects/{p}/render")
    @_guard
    def api_render(p: str):
        name = _pname(p)
        proj = pj.load(name)
        if not proj.segments:
            raise ApiError(400, "✗ 沒有可匯出的語音片段")
        bad = {s["id"] for s in proj.segments if not s.get("audio")}
        try:
            bad |= {s["id"] for s in tts_service.dirty_segments(proj)}
        except (tts_service.SynthError, KeyError):
            pass   # 聲線設定不可用時無法判斷舊音檔是否過期,只擋沒有音檔的片段
        if bad:
            ids = [s["id"] for s in proj.segments if s["id"] in bad]
            raise ApiError(400, f"✗ 還有 {len(ids)} 個片段尚未合成(或文字改過需要重新合成),無法匯出",
                           segments=ids)
        run = start_job(name)

        def work():
            try:
                stale = [s["id"] for s in tts_service.stale_post(proj)]
                if stale:   # (CHG-20261029-01 D20)匯出前自動補做過期的後製
                    post_fn(proj, stale, run.emit)
                    merge_post(name, proj, stale)
                with heavy_lock.heavy_lock(on_wait=lambda: run.emit({"type": "waiting"})):
                    run.emit({"type": "render", "status": "started"})
                    mp4, srt = render_fn(proj)
                run.emit({"type": "finished", "status": "ok", "mp4": _out_relative(mp4),
                          "srt": _out_relative(srt)})
            except media.RenderError as e:
                run.emit({"type": "error", "message": str(e), "segments": list(e.segments)})
            except tts_service.SynthError as e:
                run.emit({"type": "error", "message": str(e)})
            except Exception as e:
                print(f"studio_server:匯出錯誤 {type(e).__name__}", file=sys.stderr)
                run.emit({"type": "error", "message": "✗ 影片匯出失敗"})

        threading.Thread(target=_job_thread, args=(work, run, end_job), name=f"render-{run.id}",
                         daemon=True).start()
        return {"run_id": run.id, "events": f"/api/run/{run.id}/events"}

    @app.post("/api/projects/{p}/post")
    @_guard
    async def api_post(p: str, request: Request):
        """(CHG-20261029-01 T4)只重做後製(裁切、連音、清晰度、語調),不重新合成;同步完成後回專案檢視。

        body {"segments": [片段 id…]};省略或 {} = 全部後製過期的片段;自錄音檔片段略過。與 synth / render /
        run / annotate 共用「執行中」旗標(忙碌 409),不進 heavy_lock。"""
        name = _pname(p)
        data = await _json(request, allow_empty=True)
        _only_keys(data, ("segments",))
        ids = data.get("segments")
        if ids is not None:
            if not isinstance(ids, list) or len(ids) > MAX_POST_IDS:
                raise ApiError(422, f"segments 必須是最多 {MAX_POST_IDS} 個片段 id 的陣列")
            ids = [_check_item_id(i, "片段") for i in ids]
        proj = await run_in_threadpool(pj.load, name)
        if ids is not None:
            known = {s["id"] for s in proj.segments}
            if any(i not in known for i in ids):
                raise ApiError(404, "找不到這個片段")
            ext = {s["id"] for s in proj.segments if _external(s.get("audio"))}
            ids = [i for i in ids if i not in ext]
        else:
            ids = [s["id"] for s in tts_service.stale_post(proj)]
        run = start_job(name)

        def work():
            try:
                out = post_fn(proj, ids, run.emit) if ids else {"processed": [], "cached": []}
            except tts_service.SynthError as e:
                raise ApiError(500, str(e)) from None
            merge_post(name, proj, ids)
            return {**_view(overlay(name, pj.load(name))), "post": out}
        try:
            result = await run_in_threadpool(work)
            run.emit({"type": "finished", "status": "ok"})
        finally:
            end_job(run)
        return result

    # ---------- 合音詞庫(CHG-20261029-01 T8;D29~D31) ----------

    previews: "OrderedDict[str, dict]" = OrderedDict()   # 預覽代號 → {"result", "expires"}(只在記憶體)
    previews_guard = threading.Lock()

    def fusion_call(fn, *a):
        try:
            return fn(*a)
        except settings.FusionError as e:
            raise ApiError(e.status, str(e)) from None

    def fusion_word(word) -> str:
        if not isinstance(word, str) or not FUSION_WORD_RE.fullmatch(word):
            raise ApiError(404, "詞庫沒有這個詞(詞須為兩個漢字)")
        return word

    def fusions_view() -> dict:
        got = settings.load_fusions()
        return {"words": got["words"], "version": got["version"], **({"_warnings": got["_warnings"]}
                                                                      if got.get("_warnings") else {})}

    @app.get("/api/settings/fusions/limits")
    @_guard
    def api_fusion_limits():
        return JSONResponse({**settings.load_import_limits(), "kb_range": list(settings.FUSION_KB_RANGE),
                             "rows_range": list(settings.FUSION_ROWS_RANGE)}, headers={"Cache-Control": "no-store"})

    @app.put("/api/settings/fusions/limits")
    @_guard
    async def api_fusion_limits_put(request: Request):
        """(D36)匯入大小與筆數上限;超出允許範圍回 422 且不寫入。"""
        data = await _json(request, limit=4096)
        _only_keys(data, ("max_kb", "max_rows"))
        out = await run_in_threadpool(fusion_call, settings.save_import_limits, data.get("max_kb"),
                                      data.get("max_rows"))
        return {**out, "kb_range": list(settings.FUSION_KB_RANGE), "rows_range": list(settings.FUSION_ROWS_RANGE)}

    @app.get("/api/settings/fusions")
    @_guard
    def api_fusions():
        return JSONResponse(fusions_view(), headers={"Cache-Control": "no-store"})

    @app.put("/api/settings/fusions/{word}")
    @_guard
    async def api_fusion_put(word: str, request: Request):
        word = fusion_word(word)
        data = await _json(request, limit=4096)
        if data.get("restore") is True:
            _only_keys(data, ("restore",))
            await run_in_threadpool(fusion_call, settings.restore_fusion, word)
        else:
            _only_keys(data, ("zhuyin", "enabled"))
            await run_in_threadpool(fusion_call, settings.save_fusion, word, data.get("zhuyin"),
                                    data.get("enabled"))
        return fusions_view()

    @app.delete("/api/settings/fusions/{word}")
    @_guard
    async def api_fusion_delete(word: str):
        word = fusion_word(word)
        await run_in_threadpool(fusion_call, settings.delete_fusion, word)
        return fusions_view()

    @app.get("/api/settings/fusions/export")
    @_guard
    def api_fusions_export(format: str = "json"):
        if format not in ("json", "csv"):
            raise ApiError(422, "匯出格式只能是 json 或 csv")
        data = settings.export_fusions(format)
        ctype = "application/json; charset=utf-8" if format == "json" else "text/csv; charset=utf-8"
        return Response(data, media_type=ctype, headers={
            "Cache-Control": "no-store",
            "Content-Disposition": _content_disposition("attachment", f"合音詞庫.{format}")})

    @app.post("/api/settings/fusions/import/preview")
    @_guard
    async def api_fusions_preview(request: Request):
        """差異預覽(D30 (a)):multipart 欄位 file(只在記憶體解析,不落地);回傳預覽結果與 preview_id。"""
        limits = await run_in_threadpool(settings.load_import_limits)
        max_kb = min(limits["max_kb"], settings.FUSION_HARD_MAX_KB)
        body = await _read_body(request, max_kb * 1024 + FUSION_MULTIPART_OVERHEAD)   # 超過即停,不讀完
        if body is None:
            raise ApiError(413, settings.size_limit_message(max_kb))
        raw = _multipart_file(request.headers.get("content-type", ""), body)
        if raw is None:
            raise ApiError(422, "請以 multipart/form-data 的 file 欄位上傳詞庫檔")
        result = await run_in_threadpool(fusion_call, settings.preview_import, raw, limits)
        pid = secrets.token_hex(16)   # 128 位元
        now = time.monotonic()
        with previews_guard:
            for k in [k for k, v in previews.items() if v["expires"] <= now]:
                previews.pop(k, None)
            while len(previews) >= FUSION_PREVIEW_MAX:
                previews.popitem(last=False)
            previews[pid] = {"result": result, "expires": now + FUSION_PREVIEW_TTL}
        return {**result, "preview_id": pid}

    @app.post("/api/settings/fusions/import/apply")
    @_guard
    async def api_fusions_apply(request: Request):
        """套用預覽(D30 (b)):{preview_id, base_version, apply: "all"} 或 {preview_id, base_version, keep: [詞]}。
        代號單次有效、10 分鐘過期;代號失效、詞庫版本不符、預覽為「一致」皆回 409。"""
        data = await _json(request, limit=64 * 1024)
        _only_keys(data, ("preview_id", "base_version", "apply", "keep"))
        pid = data.get("preview_id")
        if ("apply" in data) == ("keep" in data):
            raise ApiError(422, "請指定 apply: \"all\" 或 keep: [詞…] 其中之一")
        if "apply" in data and data["apply"] != "all":
            raise ApiError(422, "apply 只能是 \"all\"")
        keep = data.get("keep", [])
        if not isinstance(keep, list) or any(not isinstance(w, str) for w in keep) or len(keep) > 2000:
            raise ApiError(422, "keep 必須是詞的陣列")
        with previews_guard:
            entry = previews.pop(pid, None) if isinstance(pid, str) else None
        if entry is None or entry["expires"] <= time.monotonic():
            raise ApiError(409, "預覽已失效或已使用,請重新匯入預覽")
        result = entry["result"]
        if data.get("base_version") != result["base_version"]:
            raise ApiError(409, "詞庫在預覽後被修改,請重新預覽")
        if result["status"] == "same":
            raise ApiError(409, "與現有詞庫一致,無需更動")
        out = await run_in_threadpool(fusion_call, settings.apply_import, result["changes"],
                                      result["base_version"], "apply" in data, keep)
        return {**fusions_view(), **out}

    @app.post("/api/fusion/suggest")
    @_guard
    async def api_fusion_suggest(request: Request):
        """(CHG-20261029-01 T7)合音建議與單組推導(純計算,不寫檔、不佔執行旗標)。

        body {"text": 句子, "zhuyin"?: 逐字注音(null = 非中文字), "index"?: 第一字索引}。回傳
        {"suggestions": 詞庫中出現的相鄰兩字, "fusion"?: {"index", "word", "a", "b", "rule", "raw", "reason",
        "lexicon", "zhuyin", "source"}}(zhuyin = 詞庫值優先,否則規則結果;都沒有時為 null)。"""
        data = await _json(request)
        _only_keys(data, ("text", "zhuyin", "index"))
        text = data.get("text")
        if not isinstance(text, str) or not text or len(text) > MAX_ANNOTATE_CHARS:
            raise ApiError(422, f"句子必須是 1 到 {MAX_ANNOTATE_CHARS} 字的文字")
        zh = data.get("zhuyin")
        if zh is not None and (not isinstance(zh, list) or len(zh) != len(text)
                               or any(z is not None and (not isinstance(z, str) or len(z) > 5) for z in zh)):
            raise ApiError(422, "zhuyin 必須是與句子等長的注音陣列(非中文字為 null)")
        lexicon = await run_in_threadpool(lexicon_fn)
        out = {"suggestions": fusion.suggest(text, zh, lexicon)}
        if "index" in data:
            i = data["index"]
            if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(text) - 1:
                raise ApiError(422, "合音需要句內相鄰的兩個字")
            word = text[i:i + 2]
            if not (fusion.is_hanzi(word[0]) and fusion.is_hanzi(word[1])):
                raise ApiError(422, "合音只能合併相鄰的兩個漢字")
            a = zh[i] if zh else None
            b = zh[i + 1] if zh else None
            r = fusion.explain(a, b) if a and b else {"zhuyin": None, "reason": "缺少這兩個字的注音", "raw": None}
            lex = lexicon.get(word)
            out["fusion"] = {"index": i, "word": word, "a": a, "b": b, "rule": r["zhuyin"], "raw": r["raw"],
                             "reason": r["reason"], "lexicon": lex, "zhuyin": lex or r["zhuyin"],
                             "source": "lexicon" if lex else ("rule" if r["zhuyin"] else None)}
        return out

    @app.post("/api/annotate")
    @_guard
    async def api_annotate(request: Request):
        data = await _json(request)
        _only_keys(data, ("text",))
        text = data.get("text")
        if not isinstance(text, str) or not text:
            raise ApiError(422, "要標註的文字不可為空")
        if len(text) > MAX_ANNOTATE_CHARS:
            raise ApiError(422, f"要標註的文字超過 {MAX_ANNOTATE_CHARS} 字")
        run = start_job(None)
        try:
            zhuyin = await run_in_threadpool(annotate_fn, text)
            run.emit({"type": "finished", "status": "ok"})
        except AnnotateError as e:
            raise ApiError(500, str(e)) from None
        finally:
            end_job(run)
        return {"text": text, "zhuyin": zhuyin}

    app.add_middleware(SecurityMiddleware, token=token)
    return app


def _job_thread(work, run: "Run", end) -> None:
    try:
        work()
    except Exception as e:   # pragma: no cover - work() 已自行處理預期錯誤
        print(f"studio_server:背景工作錯誤 {type(e).__name__}", file=sys.stderr)
        run.emit({"type": "error", "message": "✗ 背景工作失敗"})
    finally:
        end(run)


def _import_uvicorn():
    import uvicorn
    return uvicorn


def main() -> None:
    uvicorn = _import_uvicorn()
    startup_cleanup()   # 修訂 2:伺服器啟動時清 `.trash/` 滿 30 天者與各專案 `tmp/` 殘留
    app = create_app()
    print(f"Podcast 工作室:http://{HOST}:{PORT}", file=sys.stderr)
    uvicorn.run(app, host=HOST, port=PORT, proxy_headers=False, server_header=False,
                log_level="info")


if __name__ == "__main__":
    main()
