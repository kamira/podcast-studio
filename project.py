# -*- coding: utf-8 -*-
"""專案資料模型(CHG-20261022-04 T2)。

一個專案 = `projects/<名稱>/`:
  project.json   專案檔(本模組的資料模型,`schema_version: 1`;其他版本一律拒絕載入,不猜測轉換)
  history/       舊版專案檔 `YYYYmmdd-HHMMSS-<毫秒>-<4 碼隨機>.json`(UTC),只保留最近 HISTORY_KEEP 個
  audio/ assets/ 合成音檔與素材(T3、T6 寫入;專案檔只記 id,不記任何路徑)
  tmp/           worker 輸入檔(T3 用完即刪;`startup_cleanup()` 清空殘留)
  .lock          專案層級鎖檔(`fcntl.flock`;同一專案的存檔一律序列化,跨執行緒也跨行程)
`projects/.trash/<名稱>-<時間戳>-<4 碼隨機>/` 為刪除的專案,滿 TRASH_DAYS 天由 `startup_cleanup()` 清除。

專案檔(JSON 物件,未知鍵一律拒絕):
  schema_version  1
  name            專案名稱(與目錄名相同;規則見 `names.py`:中英日白名單、1~64 字)
  script          文稿全文(≤ MAX_SCRIPT 字)
  defaults        {"speed": 語速預設, "pause_comma", "pause_sentence", "pause_paragraph": 停頓預設秒數}
  segments        語音片段(≤ MAX_SEGMENTS 個):
                    id           ^[0-9a-f]{8,32}$(伺服器產生)
                    text         單句文字(1~MAX_SEGMENT_TEXT 字;原文,不含標註)
                    break        此片段之後的切點種類:sentence 句尾標點、paragraph 換行、forced 強制停頓、end 文末
                    pause_after  null(依 break 取 defaults)或 0~5 秒(四捨五入到 0.1)
                    speed        null(用 defaults.speed)或 0.5~2.0(四捨五入到 0.05)
                    annotations  [{"index": 句內字元索引, "zhuyin": 聲調符號格式注音}](只能標在漢字上)
                    audio        null 或 {"id": 音檔 id, "seconds": 合成長度(變速前), "key"?: 64 碼快取鍵}
  images          畫面軌(≤ MAX_ASSETS 個):{"id", "asset": 圖片素材 id, "start": 秒, "end": null(到結尾)或秒}
  assets          素材(≤ MAX_ASSETS 個、合計 ≤ MAX_ASSET_BYTES):
                    {"id", "kind": image|audio|text, "ext": 白名單副檔名, "bytes": 位元組數, "label"?: 顯示名稱}

介面:
  Project                      資料類別;`to_dict()`、`Project.from_dict(data)`(經 `validate`)
  validate(data) -> dict       回傳正規化後的專案 dict(語速、停頓、起訖四捨五入,補齊選填欄位);
                               不合法丟 ProjectError(`.errors` 為繁中訊息清單)
  check_name(name) -> str      專案名稱檢查,不合格丟 ProjectError
  normalize_speed / normalize_pause
  effective_speed(project, segment) / effective_pause(project, segment)
  split_segments(text, forced_breaks) -> [{"text", "break", "start", "end"}]
                               依句尾標點(。！？!?；;,後接的引號括號併入同句)與換行切分;
                               forced_breaks 為原文字元索引,在該字之後另外切分
  escape_text(text) / tts_text(segment) -> str
                               原文 `[` `]` 轉全形;標註以 `字[:注音]` 插入(注音格式同 breezy_worker,聲調符號)
  load(name) -> Project / save(project) -> 版本 id 或 None / list_history(name) / trash(name, confirm=True)
  (T6 補)create(name, script, defaults) / list_projects() / load_version(name, 版本 id) / restore(name, 版本 id)
  (T8 補)update(name, change) -> (Project, 版本 id, change 的回傳值)
                               載入、修改、驗證、存檔全程持專案層級鎖(網頁 API 與「送到素材庫」節點共用)
                               ProjectNotFound(不存在,伺服器回 404)、ProjectExists(已存在,回 409)
                               皆為 ProjectError 的子類別
  startup_cleanup(now) -> {"trash_removed", "tmp_cleared", "warnings"}
  subdir(name, sub, create=False) -> Path
                               專案的 audio/、assets/、tmp/ 真實路徑(符號連結規則同下;T3 起使用)

符號連結:`projects/`、`.trash/`、專案目錄、`tmp/`、`history/` 本身是符號連結(或解析到別處)時,
存檔、輪替、清除一律拒絕處理該目錄並記警告,不寫入、不刪除連結目標;`.trash/` 與 `tmp/` 內的
符號連結一律不跟隨。錯誤訊息一律為繁中、不含本機絕對路徑。
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import math
import os
import re
import secrets
import shutil
import stat
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

import breezy_worker
import names

log = logging.getLogger("project")

ROOT = Path(__file__).resolve().parent
PROJECTS_NAME = "projects"
TRASH_NAME = ".trash"
PROJECT_FILE = "project.json"
LOCK_FILE = ".lock"
SCHEMA_VERSION = 1

HISTORY_KEEP = 10
TRASH_DAYS = 30
SPEED_MIN, SPEED_MAX, SPEED_STEP = Decimal("0.5"), Decimal("2.0"), Decimal("0.05")
PAUSE_MIN, PAUSE_MAX, PAUSE_STEP = Decimal("0"), Decimal("5"), Decimal("0.1")
DEFAULTS = {"speed": 1.15, "pause_comma": 0.0, "pause_sentence": 0.4, "pause_paragraph": 0.8}
BREAK_PAUSE = {"sentence": "pause_sentence", "paragraph": "pause_paragraph", "forced": "pause_comma"}
BREAKS = ("sentence", "paragraph", "forced", "end")

MAX_SCRIPT = 50000
MAX_SEGMENT_TEXT = 500
MAX_SEGMENTS = 2000
MAX_PROJECT_BYTES = 5 * 1024 * 1024
MAX_ASSETS = 200
MAX_ASSET_BYTES = 2 * 1024 ** 3
MAX_AUDIO_SECONDS = 60 * 60
MAX_LABEL = 255
ZHUYIN_MAX = 5
ASSET_EXTS = {
    "image": ("png", "jpg", "jpeg", "webp"),
    "audio": ("wav", "mp3", "m4a"),
    "text": ("txt", "md"),
}

ID_RE = re.compile(r"[0-9a-f]{8,32}", re.ASCII)
KEY_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
ZHUYIN_CHARS = frozenset(chr(c) for c in range(0x3105, 0x312A)) | frozenset("ˊˇˋ˙")
SENTENCE_ENDERS = frozenset("。！？!?；;")
CLOSERS = frozenset("」』）)】〕》〉\"'”’")
NEWLINES = frozenset("\r\n")
HANZI_RANGES = ((0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0xF900, 0xFAFF), (0x20000, 0x323AF))
STAMP_RE = r"\d{8}-\d{6}-\d{3}"
HISTORY_RE = re.compile(rf"({STAMP_RE})-[0-9a-f]{{4}}\.json", re.ASCII)
VERSION_RE = re.compile(rf"{STAMP_RE}-[0-9a-f]{{4}}", re.ASCII)
TRASH_RE = re.compile(rf"(.+)-({STAMP_RE})-[0-9a-f]{{4}}")
NAME_HINT = "專案名稱只能用中文、日文假名、英文字母、數字、底線、連字號,長度 1 到 64 字"
LINK_TRIES = 16

TOP_KEYS = ("schema_version", "name", "script", "defaults", "segments", "images", "assets")
SEGMENT_KEYS = ("id", "text", "break", "pause_after", "speed", "annotations", "audio")
IMAGE_KEYS = ("id", "asset", "start", "end")
ASSET_KEYS = ("id", "kind", "ext", "bytes", "label")


class ProjectError(ValueError):
    """專案資料不合法或操作被拒絕;訊息為繁中、不含本機絕對路徑。"""

    def __init__(self, errors):
        if isinstance(errors, str):
            errors = [errors]
        self.errors = list(errors)
        super().__init__(";".join(self.errors))


class UnsafePath(ProjectError):
    """專案資料目錄是符號連結或解析到別處。"""


class ProjectNotFound(ProjectError):
    """專案(或指定的版本)不存在(CHG-20261022-04 T6:伺服器據此回 404)。"""


class ProjectExists(ProjectError):
    """新增專案時已有同名(含只差大小寫)的專案(T6:伺服器據此回 409)。"""


# ---------- 基本檢查 ----------

def check_name(name) -> str:
    """專案名稱檢查(與工作流程名稱同一套中英日白名單,見 `names.py`)。"""
    ok = names.normalize_name(name)
    if ok is None:
        raise ProjectError(NAME_HINT)
    return ok


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _round(v, step: Decimal) -> Decimal:
    try:
        d = Decimal(str(v))
    except InvalidOperation:  # pragma: no cover - _is_number 已擋掉
        raise ProjectError("數值不合法") from None
    return (d / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * step


def normalize_speed(v, where: str = "語速") -> float:
    """語速倍率:四捨五入到 0.05 後必須在 0.5~2.0。"""
    if not _is_number(v):
        raise ProjectError(f"{where}必須是數字")
    r = _round(v, SPEED_STEP)
    if not SPEED_MIN <= r <= SPEED_MAX:
        raise ProjectError(f"{where}必須在 {SPEED_MIN}~{SPEED_MAX} 倍之間")
    return float(r)


def normalize_pause(v, where: str = "停頓") -> float:
    """停頓秒數:四捨五入到 0.1 後必須在 0~5。"""
    if not _is_number(v):
        raise ProjectError(f"{where}必須是數字")
    r = _round(v, PAUSE_STEP)
    if not PAUSE_MIN <= r <= PAUSE_MAX:
        raise ProjectError(f"{where}必須在 {PAUSE_MIN}~{PAUSE_MAX} 秒之間")
    return float(r)


def _is_hanzi(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in HANZI_RANGES)


def check_zhuyin(z) -> str:
    """注音標註:只接受注音符號(ㄅ–ㄩ)與聲調符號(ˊˇˋ˙)、1~5 字,且是 breezy_worker 認得的格式。"""
    if (not isinstance(z, str) or not 1 <= len(z) <= ZHUYIN_MAX
            or any(ch not in ZHUYIN_CHARS for ch in z) or breezy_worker.marked_to_digit(z) is None):
        raise ProjectError("注音標註只接受注音符號與聲調符號(ˊˇˋ˙),長度 1 到 5")
    return z


def _check_annotations(text: str, anns, where: str) -> list[dict]:
    if anns is None:
        return []
    if not isinstance(anns, list):
        raise ProjectError(f"{where}.annotations 必須是陣列")
    out, seen = [], set()
    for a in anns:
        if not isinstance(a, dict) or set(a) != {"index", "zhuyin"}:
            raise ProjectError(f"{where}.annotations 每項必須恰為 index、zhuyin 兩個欄位")
        i = a["index"]
        if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(text):
            raise ProjectError(f"{where}.annotations 的索引超出句子範圍")
        if not _is_hanzi(text[i]):
            raise ProjectError(f"{where}.annotations 只能標在漢字上")
        if i in seen:
            raise ProjectError(f"{where}.annotations 同一個字重複標註")
        seen.add(i)
        try:
            z = check_zhuyin(a["zhuyin"])
        except ProjectError as e:
            raise ProjectError(f"{where}:{e.errors[0]}") from None
        out.append({"index": i, "zhuyin": z})
    return sorted(out, key=lambda a: a["index"])


# ---------- 分句與合成文字 ----------

def _emit_piece(pieces: list, text: str, start: int, end: int, kind: str) -> None:
    raw = text[start:end]
    stripped = raw.strip()
    if not stripped:
        return
    s = start + (len(raw) - len(raw.lstrip()))
    pieces.append({"text": stripped, "break": kind, "start": s, "end": s + len(stripped)})


def split_segments(text, forced_breaks=()) -> list[dict]:
    """把文稿切成語音片段。

    句尾標點(。！？!?；;)為切點,緊接的句尾標點與右引號、右括號併入同一句(break=sentence);
    換行為切點(break=paragraph;句尾標點後接換行時,該句升為 paragraph);forced_breaks 中的
    原文索引在該字之後切開(break=forced,例如逗號處的強制停頓);文末沒有標點的殘句為 end。
    空白片段略過;任一片段超過 MAX_SEGMENT_TEXT 字、片段數超過 MAX_SEGMENTS 丟 ProjectError。
    """
    if not isinstance(text, str):
        raise ProjectError("文稿必須是文字")
    if len(text) > MAX_SCRIPT:
        raise ProjectError(f"文稿超過 {MAX_SCRIPT} 字")
    forced = set()
    for b in forced_breaks or ():
        if not isinstance(b, int) or isinstance(b, bool) or not 0 <= b < len(text):
            raise ProjectError("強制停頓位置超出文稿範圍")
        forced.add(b)
    pieces: list[dict] = []
    start, i, n = 0, 0, len(text)
    while i < n:
        ch = text[i]
        if ch in NEWLINES:
            before = len(pieces)
            _emit_piece(pieces, text, start, i, "paragraph")
            if len(pieces) == before and pieces and not text[start:i].strip():
                pieces[-1]["break"] = "paragraph"
            start = i = i + 1
            continue
        if ch in SENTENCE_ENDERS:
            j = i + 1
            while j < n and (text[j] in SENTENCE_ENDERS or text[j] in CLOSERS):
                j += 1
            _emit_piece(pieces, text, start, j, "sentence")
            start = i = j
            continue
        if i in forced:
            _emit_piece(pieces, text, start, i + 1, "forced")
            start = i + 1
        i += 1
    _emit_piece(pieces, text, start, n, "end")
    for k, p in enumerate(pieces, 1):
        if len(p["text"]) > MAX_SEGMENT_TEXT:
            raise ProjectError(f"第 {k} 句超過 {MAX_SEGMENT_TEXT} 字,請加句尾標點或強制停頓")
    if len(pieces) > MAX_SEGMENTS:
        raise ProjectError(f"片段超過 {MAX_SEGMENTS} 個")
    return pieces


def escape_text(text: str) -> str:
    """跳脫 BreezyVoice 標註語法:原文的 `[`、`]` 換成全形 `［`、`］`(一對一,索引不變)。"""
    return text.replace("[", "［").replace("]", "］")


def tts_text(segment) -> str:
    """合成文字:原文跳脫後,在每個標註的字後插入 `[:注音]`(聲調符號格式,worker 再轉數字聲調)。"""
    if not isinstance(segment, dict):
        raise ProjectError("片段格式不正確")
    text = segment.get("text")
    if not isinstance(text, str) or not text:
        raise ProjectError("片段文字是空的")
    anns = {a["index"]: a["zhuyin"] for a in _check_annotations(text, segment.get("annotations"), "片段")}
    out = []
    for i, ch in enumerate(escape_text(text)):
        out.append(ch)
        if i in anns:
            out.append(f"[:{anns[i]}]")
    return "".join(out)


# ---------- 驗證 ----------

def _check_keys(obj, allowed, required, where, errors) -> bool:
    if not isinstance(obj, dict):
        errors.append(f"{where} 必須是物件")
        return False
    unknown = sorted(set(obj) - set(allowed), key=str)
    if unknown:
        errors.append(f"{where} 有不認得的欄位:{', '.join(map(str, unknown))[:200]}")
        return False
    missing = [k for k in required if k not in obj]
    if missing:
        errors.append(f"{where} 缺少欄位:{', '.join(missing)}")
        return False
    return True


def _check_id(v, where, errors, seen) -> bool:
    if not isinstance(v, str) or not ID_RE.fullmatch(v):
        errors.append(f"{where}.id 格式不正確")
        return False
    if v in seen:
        errors.append(f"{where}.id 重複")
        return False
    seen.add(v)
    return True


def _try(errors, fn, *args):
    try:
        return fn(*args)
    except ProjectError as e:
        errors.extend(e.errors)
        return None


def _check_version(v) -> None:
    if not isinstance(v, int) or isinstance(v, bool) or v != SCHEMA_VERSION:
        shown = repr(v) if isinstance(v, (int, float, str, type(None))) else type(v).__name__
        raise ProjectError(f"專案檔版本 {shown[:20]} 不支援,拒絕載入(本版只讀 schema_version {SCHEMA_VERSION})")


def _validate_defaults(d, errors) -> dict:
    out = dict(DEFAULTS)
    if d is None:
        return out
    if not _check_keys(d, DEFAULTS, (), "defaults", errors):
        return out
    for k, v in d.items():
        fn = normalize_speed if k == "speed" else normalize_pause
        r = _try(errors, fn, v, f"defaults.{k}")
        if r is not None:
            out[k] = r
    return out


def _validate_segment(s, where, errors, seen) -> dict | None:
    if not _check_keys(s, SEGMENT_KEYS, ("id", "text"), where, errors):
        return None
    ok = _check_id(s["id"], where, errors, seen)
    text = s["text"]
    if not isinstance(text, str) or not text.strip():
        errors.append(f"{where}.text 是空的")
        return None
    if len(text) > MAX_SEGMENT_TEXT:
        errors.append(f"{where}.text 超過 {MAX_SEGMENT_TEXT} 字")
        return None
    out = {"id": s["id"], "text": text}
    brk = s.get("break", "end")
    if brk not in BREAKS:
        errors.append(f"{where}.break 只能是 {'、'.join(BREAKS)}")
        ok = False
    out["break"] = brk
    pause = s.get("pause_after")
    out["pause_after"] = None if pause is None else _try(errors, normalize_pause, pause, f"{where}.pause_after")
    speed = s.get("speed")
    out["speed"] = None if speed is None else _try(errors, normalize_speed, speed, f"{where}.speed")
    out["annotations"] = _try(errors, _check_annotations, text, s.get("annotations"), where)
    audio = s.get("audio")
    if audio is not None:
        if not _check_keys(audio, ("id", "seconds", "key"), ("id", "seconds"), f"{where}.audio", errors):
            ok = False
        elif not isinstance(audio["id"], str) or not ID_RE.fullmatch(audio["id"]):
            errors.append(f"{where}.audio.id 格式不正確")
            ok = False
        elif not _is_number(audio["seconds"]) or audio["seconds"] < 0:
            errors.append(f"{where}.audio.seconds 必須是非負數字")
            ok = False
        elif "key" in audio and (not isinstance(audio["key"], str) or not KEY_RE.fullmatch(audio["key"])):
            errors.append(f"{where}.audio.key 格式不正確")
            ok = False
        else:
            audio = dict(audio)
    out["audio"] = audio
    if (pause is not None and out["pause_after"] is None) or (speed is not None and out["speed"] is None) \
            or out["annotations"] is None:
        ok = False
    return out if ok else None


def _validate_asset(a, where, errors, seen) -> dict | None:
    if not _check_keys(a, ASSET_KEYS, ("id", "kind", "ext", "bytes"), where, errors):
        return None
    ok = _check_id(a["id"], where, errors, seen)
    if not isinstance(a["kind"], str) or a["kind"] not in ASSET_EXTS:
        errors.append(f"{where}.kind 只能是 image、audio、text")
        return None
    if a["ext"] not in ASSET_EXTS[a["kind"]]:
        errors.append(f"{where}.ext 不在白名單內")
        ok = False
    if not isinstance(a["bytes"], int) or isinstance(a["bytes"], bool) or a["bytes"] < 0:
        errors.append(f"{where}.bytes 必須是非負整數")
        ok = False
    label = a.get("label")
    if label is not None and (not isinstance(label, str) or len(label) > MAX_LABEL
                              or any(ord(c) < 32 or ord(c) == 127 for c in label)):
        errors.append(f"{where}.label 必須是 {MAX_LABEL} 字以內、不含控制字元的文字")
        ok = False
    return dict(a) if ok else None


def _validate_image(m, where, errors, seen, image_assets) -> dict | None:
    if not _check_keys(m, IMAGE_KEYS, ("id", "asset", "start"), where, errors):
        return None
    ok = _check_id(m["id"], where, errors, seen)
    if not isinstance(m["asset"], str) or m["asset"] not in image_assets:
        errors.append(f"{where}.asset 不是本專案的圖片素材")
        ok = False
    start, end = m["start"], m.get("end")
    if not _is_number(start) or start < 0:
        errors.append(f"{where}.start 必須是非負數字")
        return None
    start = float(_round(start, PAUSE_STEP))
    if end is not None:
        if not _is_number(end):
            errors.append(f"{where}.end 必須是數字或 null")
            return None
        end = float(_round(end, PAUSE_STEP))
        if end <= start:
            errors.append(f"{where}.end 必須大於 start")
            ok = False
    return {"id": m["id"], "asset": m["asset"], "start": start, "end": end} if ok else None


def _list(data, key, errors, limit):
    v = data.get(key, [])
    if not isinstance(v, list):
        errors.append(f"{key} 必須是陣列")
        return []
    if len(v) > limit:
        errors.append(f"{key} 超過 {limit} 個")
        return []
    return v


def _speed_of(defaults: dict, seg: dict) -> float:
    return seg["speed"] if seg.get("speed") is not None else defaults["speed"]


def validate(data) -> dict:
    """驗證並正規化專案 dict;不合法丟 ProjectError(列出全部錯誤)。"""
    errors: list[str] = []
    if not isinstance(data, dict):
        raise ProjectError("專案檔必須是 JSON 物件")
    _check_version(data.get("schema_version"))
    if not _check_keys(data, TOP_KEYS, ("schema_version", "name"), "專案", errors):
        raise ProjectError(errors)
    name = _try(errors, check_name, data["name"])
    script = data.get("script", "")
    if not isinstance(script, str):
        errors.append("script 必須是文字")
        script = ""
    elif len(script) > MAX_SCRIPT:
        errors.append(f"文稿超過 {MAX_SCRIPT} 字")
    defaults = _validate_defaults(data.get("defaults"), errors)

    seen: set = set()
    assets = [_validate_asset(a, f"assets[{i}]", errors, seen)
              for i, a in enumerate(_list(data, "assets", errors, MAX_ASSETS))]
    total = sum(a["bytes"] for a in assets if a)
    if total > MAX_ASSET_BYTES:
        errors.append(f"素材總量超過 {MAX_ASSET_BYTES // 1024 ** 3} GB")
    image_assets = {a["id"] for a in assets if a and a["kind"] == "image"}
    segments = [_validate_segment(s, f"segments[{i}]", errors, seen)
                for i, s in enumerate(_list(data, "segments", errors, MAX_SEGMENTS))]
    images = [_validate_image(m, f"images[{i}]", errors, seen, image_assets)
              for i, m in enumerate(_list(data, "images", errors, MAX_ASSETS))]
    if not errors:
        seconds = sum(s["audio"]["seconds"] / _speed_of(defaults, s) for s in segments if s["audio"])
        if seconds > MAX_AUDIO_SECONDS:
            errors.append(f"專案音訊總長超過 {MAX_AUDIO_SECONDS // 60} 分鐘(以變速後長度計)")
    if errors:
        raise ProjectError(errors)
    out = {"schema_version": SCHEMA_VERSION, "name": name, "script": script, "defaults": defaults,
           "segments": segments, "images": images, "assets": assets}
    if len(_dump(out)) > MAX_PROJECT_BYTES:
        raise ProjectError(f"專案檔超過 {MAX_PROJECT_BYTES // 1024 ** 2} MB")
    return out


def _dump(data: dict) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


# ---------- 資料類別 ----------

@dataclass
class Project:
    name: str
    script: str = ""
    segments: list = field(default_factory=list)
    images: list = field(default_factory=list)
    assets: list = field(default_factory=list)
    defaults: dict = field(default_factory=lambda: dict(DEFAULTS))
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {"schema_version": self.schema_version, "name": self.name, "script": self.script,
                "defaults": dict(self.defaults), "segments": [dict(s) for s in self.segments],
                "images": [dict(m) for m in self.images], "assets": [dict(a) for a in self.assets]}

    @classmethod
    def from_dict(cls, data) -> "Project":
        d = validate(data)
        return cls(name=d["name"], script=d["script"], segments=d["segments"], images=d["images"],
                   assets=d["assets"], defaults=d["defaults"], schema_version=d["schema_version"])


def new_id() -> str:
    """伺服器產生的片段 / 圖片 / 素材 id(16 碼十六進位)。"""
    return secrets.token_hex(8)


def effective_speed(project: Project, segment: dict) -> float:
    """片段實際語速:speed 為 null 時用專案的語速預設(預設 1.15)。"""
    return _speed_of(project.defaults, segment)


def effective_pause(project: Project, segment: dict) -> float:
    """片段之後的停頓:pause_after 為 null 時依 break 取停頓預設(文末為 0)。"""
    if segment.get("pause_after") is not None:
        return segment["pause_after"]
    key = BREAK_PAUSE.get(segment.get("break"))
    return project.defaults[key] if key else 0.0


# ---------- 路徑與鎖 ----------

def _check_dir(cur: Path, expected: Path, label: str) -> None:
    if cur.is_symlink():
        raise UnsafePath(f"資料目錄「{label}」是符號連結,不接受")
    try:
        resolved = cur.resolve(strict=True)
    except (OSError, RuntimeError):
        raise UnsafePath(f"資料目錄「{label}」無法解析") from None
    if resolved != expected or not resolved.is_dir():
        raise UnsafePath(f"資料目錄「{label}」不在預期位置")


def _safe_child(base: Path, part: str, label: str, create: bool) -> Path:
    """base(已檢查過的真實路徑)/part;不得是符號連結、解析後必須等於 base/part。"""
    cur = base / part
    if not cur.is_symlink() and not cur.exists():
        if not create:
            raise FileNotFoundError(label)
        try:
            cur.mkdir(mode=0o755)
        except FileExistsError:
            pass
    _check_dir(cur, base / part, label)
    return base / part


def _projects_root(create: bool) -> Path:
    root = Path(ROOT).resolve()
    return _safe_child(root, PROJECTS_NAME, PROJECTS_NAME, create)


@contextlib.contextmanager
def _project_lock(pdir: Path):
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(pdir / LOCK_FILE, flags, 0o600)
    except OSError:
        raise UnsafePath("無法取得專案鎖(鎖檔可能是符號連結)") from None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _stamp(now: float | None = None) -> str:
    t = time.time() if now is None else now
    dt = datetime.fromtimestamp(t, tz=timezone.utc)
    return f"{dt:%Y%m%d-%H%M%S}-{dt.microsecond // 1000:03d}"


def _rand4() -> str:
    return secrets.token_hex(2)


def _parse_stamp(s: str) -> float | None:
    try:
        dt = datetime.strptime(s[:15], "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return dt.timestamp() + int(s[16:19]) / 1000


def _warn(warnings: list, message: str) -> None:
    log.warning(message)
    warnings.append(message)


def _case_variant(root: Path, name: str) -> str | None:
    """找出與 name 只差大小寫(casefold 相等、本身不同)的既有項目;macOS 預設檔案系統不分大小寫。"""
    key = names.fold_name(name)
    with os.scandir(root) as it:
        for entry in it:
            if entry.name != name and not entry.name.startswith(".") and names.fold_name(entry.name) == key:
                return entry.name
    return None


def _project_dir(root: Path, name: str, create: bool) -> Path:
    """專案目錄;已有只差大小寫的其他專案時一律拒絕(不分大小寫的檔案系統上兩者是同一個目錄)。"""
    if _case_variant(root, name) is not None:
        if create:
            raise ProjectExists("已有只差大小寫的同名專案,請改用其他名稱")
        raise ProjectNotFound("找不到這個專案(已有只差大小寫的其他專案)")
    try:
        return _safe_child(root, name, "專案", create=create)
    except FileNotFoundError:
        raise ProjectNotFound("找不到這個專案") from None


SUBDIRS = ("audio", "assets", "tmp")


def subdir(name, sub: str, *, create: bool = False) -> Path:
    """專案子目錄 `projects/<名稱>/<sub>`(audio、assets、tmp)的真實路徑(CHG-20261022-04 T3 起共用)。

    各層一律不得是符號連結、解析後必須在預期位置,不符丟 UnsafePath;create=False 且子目錄不存在時
    丟 FileNotFoundError,專案不存在時丟 ProjectError。
    """
    if sub not in SUBDIRS:
        raise ValueError(f"不支援的專案子目錄:{sub}")
    name = check_name(name)
    try:
        root = _projects_root(create=create)
    except FileNotFoundError:
        raise ProjectNotFound("找不到這個專案") from None
    return _safe_child(_project_dir(root, name, create=create), sub, sub, create=create)


# ---------- 讀寫 ----------

def _read_project_file(path: Path) -> dict:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise ProjectNotFound("找不到這個專案") from None
    except OSError:
        raise ProjectError("專案檔無法讀取(可能是符號連結)") from None
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise ProjectError("專案檔不是一般檔案")
        raw = f.read(MAX_PROJECT_BYTES + 1)
    if len(raw) > MAX_PROJECT_BYTES:
        raise ProjectError(f"專案檔超過 {MAX_PROJECT_BYTES // 1024 ** 2} MB,拒絕載入")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ProjectError("專案檔內容損壞,無法讀取") from None
    if not isinstance(data, dict):
        raise ProjectError("專案檔必須是 JSON 物件")
    return data


def load(name) -> Project:
    """讀 `projects/<名稱>/project.json`;schema_version 不是 1 一律拒絕載入。"""
    name = check_name(name)
    try:
        root = _projects_root(create=False)
    except FileNotFoundError:
        raise ProjectNotFound("找不到這個專案") from None
    data = _read_project_file(_project_dir(root, name, create=False) / PROJECT_FILE)
    _check_version(data.get("schema_version"))
    if data.get("name") != name:
        raise ProjectError("專案檔的名稱與專案目錄不符,拒絕載入")
    return Project.from_dict(data)


def _write_atomic(pdir: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    for _ in range(LINK_TRIES):
        tmp = pdir / f".project-{secrets.token_hex(8)}.tmp"
        try:
            fd = os.open(tmp, flags, 0o644)
            break
        except FileExistsError:
            continue
    else:  # pragma: no cover - 隨機碼連續撞名
        raise ProjectError("無法建立暫存檔")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, pdir / PROJECT_FILE)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _archive_current(pdir: Path, hdir: Path) -> str | None:
    """把目前的 project.json 以 os.link 排他建立到 history/(檔名唯一,不覆寫);沒有舊檔回 None。"""
    current = pdir / PROJECT_FILE
    try:
        st = current.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        raise UnsafePath("專案檔不是一般檔案(可能是符號連結),拒絕存檔")
    for _ in range(LINK_TRIES):
        vid = f"{_stamp()}-{_rand4()}"
        try:
            os.link(current, hdir / f"{vid}.json")
            return vid
        except FileExistsError:
            continue
    raise ProjectError("無法建立不重複的版本檔名")


def _history_entries(hdir: Path) -> list[tuple]:
    out = []
    with os.scandir(hdir) as it:
        for entry in it:
            m = HISTORY_RE.fullmatch(entry.name)
            if not m:
                continue
            st = entry.stat(follow_symlinks=False)
            if stat.S_ISREG(st.st_mode):
                out.append((m.group(1), st.st_mtime_ns, entry.name))
    out.sort()
    return out


def _rotate(pdir: Path, warnings: list) -> list[str]:
    """history/ 只保留最近 HISTORY_KEEP 個;history/ 是符號連結或解析到別處時拒絕處理並記警告。"""
    try:
        hdir = _safe_child(pdir, "history", "history", create=False)
    except FileNotFoundError:
        return []
    except UnsafePath as e:
        _warn(warnings, f"專案「{pdir.name}」的 history 目錄不安全,拒絕輪替:{e.errors[0]}")
        return []
    entries = _history_entries(hdir)
    removed = []
    for _, _, fn in entries[:max(0, len(entries) - HISTORY_KEEP)]:
        os.unlink(hdir / fn)
        removed.append(fn)
    return removed


def save(project: Project) -> str | None:
    """原子寫入 project.json;寫入前把舊檔移入 history/,history/ 只留最近 10 個。

    同一專案的存檔以專案層級鎖序列化。回傳這次移入 history 的版本 id(第一次存檔為 None)。
    專案目錄、history/ 是符號連結或解析到別處時拒絕存檔(丟 UnsafePath 並記警告),不寫入連結目標。
    """
    data = validate(project.to_dict())
    name = data["name"]
    warnings: list[str] = []
    try:
        root = _projects_root(create=True)
        pdir = _project_dir(root, name, create=True)
        with _project_lock(pdir):
            return _save_locked(pdir, data, warnings)
    except UnsafePath as e:
        _warn(warnings, f"專案「{name}」拒絕存檔:{e.errors[0]}")
        raise


def _save_locked(pdir: Path, data: dict, warnings: list) -> str | None:
    """已持有專案鎖時的存檔本體(舊檔移入 history/ → 原子寫入 → 輪替)。"""
    payload = _dump(data)
    hdir = _safe_child(pdir, "history", "history", create=True)
    vid = _archive_current(pdir, hdir)
    _write_atomic(pdir, payload)
    _rotate(pdir, warnings)
    return vid


def update(name, change):
    """「載入 → change(project) → 驗證 → 存檔」全程持專案層級鎖(CHG-20261022-04 T8)。

    跨執行緒也跨行程序列化同一專案的修改,避免兩處各自載入後互相覆蓋(例:「送到素材庫」節點與
    網頁的修改同時發生)。change 可原地修改 project 並回傳任意值;change 或驗證丟例外時不存檔。
    回傳 (存檔後的 Project, 版本 id 或 None, change 的回傳值)。專案不存在丟 ProjectNotFound。
    """
    name = check_name(name)
    try:
        root = _projects_root(create=False)
    except FileNotFoundError:
        raise ProjectNotFound("找不到這個專案") from None
    pdir = _project_dir(root, name, create=False)
    warnings: list[str] = []
    try:
        with _project_lock(pdir):
            proj = load(name)
            extra = change(proj)
            data = validate(proj.to_dict())
            if data["name"] != name:
                raise ProjectError("不能在修改時改變專案名稱")
            vid = _save_locked(pdir, data, warnings)
    except UnsafePath as e:
        _warn(warnings, f"專案「{name}」拒絕存檔:{e.errors[0]}")
        raise
    return Project.from_dict(data), vid, extra


def list_history(name) -> list[str]:
    """回傳版本 id(舊到新)。"""
    name = check_name(name)
    try:
        pdir = _project_dir(_projects_root(create=False), name, create=False)
        hdir = _safe_child(pdir, "history", "history", create=False)
    except FileNotFoundError:
        return []
    return [fn[:-len(".json")] for _, _, fn in _history_entries(hdir)]


def load_version(name, vid) -> Project:
    """讀 `history/<版本 id>.json`(T6 還原版本用);版本 id 格式不對或不存在丟 ProjectNotFound。"""
    name = check_name(name)
    if not isinstance(vid, str) or not VERSION_RE.fullmatch(vid):
        raise ProjectNotFound("找不到這個版本")
    try:
        pdir = _project_dir(_projects_root(create=False), name, create=False)
        hdir = _safe_child(pdir, "history", "history", create=False)
    except FileNotFoundError:
        raise ProjectNotFound("找不到這個版本") from None
    try:
        data = _read_project_file(hdir / f"{vid}.json")
    except ProjectNotFound:
        raise ProjectNotFound("找不到這個版本") from None
    _check_version(data.get("schema_version"))
    if data.get("name") != name:
        raise ProjectError("版本檔的名稱與專案不符,拒絕還原")
    return Project.from_dict(data)


def restore(name, vid) -> str | None:
    """還原版本 = 一次存檔:先把目前版本移入 history/,再原子寫入該版本的內容;回傳 save() 的版本 id。"""
    return save(load_version(name, vid))


def create(name, script: str = "", defaults: dict | None = None) -> Project:
    """新增專案:文稿依 `split_segments` 切成片段(id 由伺服器產生);已有同名(含只差大小寫)丟 ProjectExists。"""
    name = check_name(name)
    pieces = split_segments(script)
    proj = Project(name=name, script=script, defaults=dict(defaults or DEFAULTS),
                   segments=[{"id": new_id(), "text": p["text"], "break": p["break"], "pause_after": None,
                              "speed": None, "annotations": [], "audio": None} for p in pieces])
    proj = Project.from_dict(proj.to_dict())
    root = _projects_root(create=True)
    if _case_variant(root, name) is not None or os.path.lexists(root / name):
        raise ProjectExists("已有同名的專案,請改用其他名稱")
    save(proj)
    return proj


def list_projects() -> list[dict]:
    """列出 `projects/` 下有專案檔的專案:[{"name", "updated": 專案檔修改時間(epoch 秒)}],依名稱排序。

    點開頭的項目(`.trash/` 等)、名稱不合規則、符號連結、沒有一般檔案 project.json 的目錄一律略過。
    """
    try:
        root = _projects_root(create=False)
    except FileNotFoundError:
        return []
    out = []
    with os.scandir(root) as it:
        entries = sorted(it, key=lambda e: e.name)
    for entry in entries:
        if entry.name.startswith(".") or names.normalize_name(entry.name) is None:
            continue
        if not entry.is_dir(follow_symlinks=False):
            continue
        try:
            st = os.lstat(Path(entry.path) / PROJECT_FILE)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            out.append({"name": entry.name, "updated": st.st_mtime})
    return out


def trash(name, *, confirm, now: float | None = None) -> str:
    """把專案移到 `projects/.trash/<名稱>-<時間戳>-<4 碼隨機>/`;confirm 必須為 True,不覆蓋既有項目。"""
    if confirm is not True:
        raise ProjectError("刪除專案需要確認(confirm: true)")
    name = check_name(name)
    try:
        root = _projects_root(create=False)
    except FileNotFoundError:
        raise ProjectNotFound("找不到這個專案") from None
    pdir = _project_dir(root, name, create=False)
    tdir = _safe_child(root, TRASH_NAME, TRASH_NAME, create=True)
    with _project_lock(pdir):
        for _ in range(LINK_TRIES):
            dest = tdir / f"{name}-{_stamp(now)}-{_rand4()}"
            if not dest.exists() and not dest.is_symlink():
                os.rename(pdir, dest)
                return dest.name
    raise ProjectError("無法建立不重複的回收名稱")  # pragma: no cover


# ---------- 啟動清理 ----------

def _now_ts(now) -> float:
    if isinstance(now, datetime):
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.timestamp()
    if _is_number(now):
        return float(now)
    raise TypeError("now 必須是 datetime 或 epoch 秒數")


def _remove_entry(path: str, is_dir: bool) -> None:
    """刪除一個項目;目錄以 shutil.rmtree(內部符號連結只刪連結本身,不跟隨),其他(含符號連結)unlink。"""
    if is_dir:
        shutil.rmtree(path)
    else:
        os.unlink(path)


def _clean_trash(root: Path, now_ts: float, result: dict) -> None:
    try:
        tdir = _safe_child(root, TRASH_NAME, TRASH_NAME, create=False)
    except FileNotFoundError:
        return
    except UnsafePath as e:
        _warn(result["warnings"], f"回收區不安全,拒絕清除:{e.errors[0]}")
        return
    limit = TRASH_DAYS * 86400
    with os.scandir(tdir) as it:
        entries = list(it)
    for entry in entries:
        if entry.is_symlink():
            _warn(result["warnings"], f"回收區項目「{entry.name}」是符號連結,不跟隨、不刪除")
            continue
        m = TRASH_RE.fullmatch(entry.name)
        ts = _parse_stamp(m.group(2)) if m else None
        if ts is None:
            _warn(result["warnings"], f"回收區項目「{entry.name}」名稱不含時間戳,略過")
            continue
        if now_ts - ts >= limit:
            _remove_entry(entry.path, entry.is_dir(follow_symlinks=False))
            result["trash_removed"].append(entry.name)


def _clean_project(root: Path, name: str, result: dict) -> None:
    warnings = result["warnings"]
    try:
        pdir = _safe_child(root, name, "專案", create=False)
    except (UnsafePath, FileNotFoundError):
        _warn(warnings, f"專案目錄「{name}」是符號連結或不在預期位置,拒絕處理")
        return
    for sub in ("history", "audio", "assets"):
        if (pdir / sub).is_symlink():
            _warn(warnings, f"專案「{name}」的 {sub} 目錄是符號連結,拒絕處理")
    try:
        tmp = _safe_child(pdir, "tmp", "tmp", create=False)
    except FileNotFoundError:
        return
    except UnsafePath:
        _warn(warnings, f"專案「{name}」的 tmp 目錄是符號連結或不在預期位置,拒絕清空")
        return
    with os.scandir(tmp) as it:
        entries = list(it)
    for entry in entries:
        _remove_entry(entry.path, entry.is_dir(follow_symlinks=False))
        result["tmp_cleared"] += 1


def startup_cleanup(now) -> dict:
    """伺服器啟動時呼叫:清除 `.trash/` 中滿 30 天的項目、清空各專案 `tmp/` 的殘留。

    now 為注入的現在時間(datetime 或 epoch 秒數;不帶時區的 datetime 視為 UTC);回收項目的年齡
    以其名稱內的時間戳(UTC)判定,不看檔案時間。只刪 `.trash/` 與 `tmp/` 之下的項目,不動
    project.json、audio/、assets/、history/;符號連結一律不跟隨。
    """
    now_ts = _now_ts(now)
    result = {"trash_removed": [], "tmp_cleared": 0, "warnings": []}
    try:
        root = _projects_root(create=False)
    except FileNotFoundError:
        return result
    except UnsafePath as e:
        _warn(result["warnings"], f"projects 目錄不安全,拒絕清理:{e.errors[0]}")
        return result
    _clean_trash(root, now_ts, result)
    with os.scandir(root) as it:
        entries = sorted(e.name for e in it if not e.name.startswith("."))
    for name in entries:
        if names.normalize_name(name) is None:
            _warn(result["warnings"], "projects 目錄內有名稱不合規則的項目,略過")
            continue
        _clean_project(root, name, result)
    return result
