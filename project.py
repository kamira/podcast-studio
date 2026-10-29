# -*- coding: utf-8 -*-
"""專案資料模型(CHG-20261022-04 T2)。

一個專案 = `projects/<名稱>/`:
  project.json   專案檔(本模組的資料模型,`schema_version: 2`;讀到 1 時在記憶體補上 v2 欄位的預設值、
                 下次存檔寫成 2(CHG-20261029-01 D14);其他版本一律拒絕載入,不猜測轉換)
  history/       舊版專案檔 `YYYYmmdd-HHMMSS-<毫秒>-<4 碼隨機>.json`(UTC),只保留最近 HISTORY_KEEP 個
  audio/ assets/ 合成音檔與素材(T3、T6 寫入;專案檔只記 id,不記任何路徑)
  tmp/           worker 輸入檔(T3 用完即刪;`startup_cleanup()` 清空殘留)
  .lock          專案層級鎖檔(`fcntl.flock`;同一專案的存檔一律序列化,跨執行緒也跨行程)
`projects/.trash/<名稱>-<時間戳>-<4 碼隨機>/` 為刪除的專案,滿 TRASH_DAYS 天由 `startup_cleanup()` 清除。

專案檔(JSON 物件,未知鍵一律拒絕):
  schema_version  2(v1 = 沒有 connect、clarity、word_clarity、pitch、post 的舊版,載入時升級)
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
                    (以下 CHG-20261029-01 T1 新增,v2)
                    connect      null(= 0%)或 0~100 整數:連音(字與字之間的黏著;只動句內字間)
                    clarity      null(= 100%)或 0~100 整數:清晰度(咬字含糊程度;100 = 字正腔圓)
                    word_clarity [{"start", "end", "value"}]:字詞清晰度,`[start, end)` 為句內字元索引,
                                 範圍內一律是漢字、互不重疊、每句 ≤ MAX_WORD_CLARITY 段,value 0~100 整數
                    pitch        null(不處理)或 {"points": [{"pos": 0~1(取到 0.01), "semi": −6~+6 半音(取到 0.1)}],
                                 "roundness": null(= 50%)或 0~100 整數};2~16 點,pos 嚴格遞增
                    post         null 或 {"id": 後製檔 id, "key": 64 碼後製鍵, "seconds": 後製後長度(變速前),
                                 "trim": 裁掉的前後低音量秒數}(伺服器產生;PATCH 不接受)
                    fusions      [{"index": 起點字元索引, "length": 2~4, "zhuyin": 1~(length − 1) 個音節(半形空白分隔),
                                  "source": rule|lexicon|manual, "display"?: 空字串或 1~4 個漢字}]
                                 (CHG-20261029-01 T7,D27)範圍內都是漢字、互不重疊、每句 ≤ MAX_FUSIONS 組;
                                 合音範圍內不得有聲調標註;字詞清晰度區段必須完整包含或完全不含合音範圍;
                                 (修訂 1 R1-4)display 為選填的顯示字(由詞庫套用時寫入),缺少視同空字串、
                                 載入時不補;不是空字串時合成以顯示字取代合音範圍
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
                               原文 `[` `]` 轉全形;標註以 `字[:注音]` 插入(注音格式同 breezy_worker,聲調符號);
                               (T7)合音:第一字帶 `[:合音注音]`、刪除第二字(例 `是這[:ㄐㄧㄤˋ]啊`)
                               (修訂 1 R1-4)合音帶顯示字時整段換成顯示字、不加注音(例 `是醬啊`)
  fused_out(segment) -> set    合音範圍內不送模型的原字索引(注音路線為刪去的字;顯示字路線為整個範圍)
  model_units(segment) -> list[int]
                               (修訂 1 R1-5)送模型的每個漢字對應的原文索引(顯示字對應到合音範圍內的字);
                               字界估計與字詞清晰度換算以此為準
  load(name) -> Project / save(project) -> 版本 id 或 None / list_history(name) / trash(name, confirm=True)
  (T6 補)create(name, script, defaults) / list_projects() / load_version(name, 版本 id) / restore(name, 版本 id)
  (T8 補)update(name, change) -> (Project, 版本 id, change 的回傳值)
                               載入、修改、驗證、存檔全程持專案層級鎖(網頁 API 與「送到素材庫」節點共用)
                               ProjectNotFound(不存在,伺服器回 404)、ProjectExists(已存在,回 409)
                               皆為 ProjectError 的子類別
  startup_cleanup(now) -> {"trash_removed", "tmp_cleared", "warnings"}
  (CHG-20261029-01 T1)PROSODY_DEFAULTS / TRIM_PARAMS / POST_VERSION
  (修訂 1 T10)POST_ALGO = {"clarity": 2, "pitch": 2}:後製鍵的演算法標記(R1-11)
  post_params(segment) -> dict 後製參數(null 一律取程式常數):trim、connect、clarity、word_clarity、pitch
  post_key(segment) -> str     SHA-256(JSON 陣列 [audio.id, POST_VERSION, 裁切參數, connect, clarity,
                               word_clarity, pitch] + 演算法標記);演算法標記只在用到時附加(清晰度 < 100%
                               或有字詞清晰度附 clarity、有語調附 pitch,合為一個物件),只有裁切或連音時不附加、
                               鍵與修訂前相同。語速、停頓不在內。自錄音檔(audio 沒有 key)丟 ProjectError
  is_post_fresh(segment)       post 存在且 post.key == post_key(segment)(只比對資料,不看檔案)
  (修訂 1 T11)post 的選填欄位 pitch_points:worker 實際套用的語調內部點(伺服器產生,PATCH 不能寫入)
  check_pitch_points(points, where) -> list
                               2–30 個 [位置 0–1 嚴格遞增, 半音 ±7.2];不合格丟 ProjectError
  is_default_post(segment)     連音 0%、清晰度 100%、沒有字詞清晰度、沒有語調(後製結果 = 裁切結果)
  subdir(name, sub, create=False) -> Path
                               專案的 audio/、assets/、tmp/ 真實路徑(符號連結規則同下;T3 起使用)

符號連結:`projects/`、`.trash/`、專案目錄、`tmp/`、`history/` 本身是符號連結(或解析到別處)時,
存檔、輪替、清除一律拒絕處理該目錄並記警告,不寫入、不刪除連結目標;`.trash/` 與 `tmp/` 內的
符號連結一律不跟隨。錯誤訊息一律為繁中、不含本機絕對路徑。
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import logging
import math
import os
import re
import secrets
import stat
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

import breezy_worker
import names
import safe_fs

log = logging.getLogger("project")

ROOT = Path(__file__).resolve().parent
PROJECTS_NAME = "projects"
TRASH_NAME = ".trash"
PROJECT_FILE = "project.json"
LOCK_FILE = ".lock"
SCHEMA_VERSION = 2
OLD_SCHEMA_VERSIONS = (1,)   # 載入時自動升級的舊版(CHG-20261029-01 D14)

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
# CHG-20261029-01:連音、清晰度、語調(D6、D7、D9、D10、D15)
PROSODY_DEFAULTS = {"connect": 0, "clarity": 100, "roundness": 50}
PITCH_POINTS_MIN, PITCH_POINTS_MAX = 2, 16
SEMI_MIN, SEMI_MAX, SEMI_STEP = Decimal("-6"), Decimal("6"), Decimal("0.1")
POS_STEP = Decimal("0.01")
# 修訂 1(R1-9、R1-10):worker 實際套用的內部點(使用者點 + 自動補點,峰谷 × 1.2)
INNER_POINTS_MAX = 30          # 16 個使用者點 + 14 個自動補點
INNER_SEMI_LIMIT = 7.2         # = 6 × 1.2
MAX_WORD_CLARITY = 32
MAX_FUSIONS = 16
FUSION_MIN, FUSION_MAX = 2, 4     # 合音字數(使用者 2026-10-29:不超過四字)
MAX_FUSION_OFF = 32                # fusion_off 每句上限(D41)
FUSION_DISPLAY_MAX = 4             # 合音顯示字上限(D43、R1-4;同詞庫 display)
FUSION_SOURCES = ("rule", "lexicon", "manual")
POST_VERSION = 1
# 修訂 1(R1-11):後製演算法標記。清晰度改方案 C、語調改 PCHIP 混合後,用到的部分附加於後製鍵,
# 讓有清晰度或語調的既有後製失效重做;只有裁切或連音的片段鍵不變。POST_VERSION 維持 1。
POST_ALGO = {"clarity": 2, "pitch": 2}
# 裁切參數(D2):-40 dBFS 樣本峰值門檻、前後各留 30 ms、10 ms 淡入淡出
TRIM_PARAMS = {"threshold_db": -40, "keep_ms": 30, "fade_ms": 10}
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
SEGMENT_KEYS_V1 = ("id", "text", "break", "pause_after", "speed", "annotations", "audio")
SEGMENT_KEYS = (*SEGMENT_KEYS_V1, "connect", "clarity", "word_clarity", "pitch", "post", "fusions",
                "fusion_off")
POST_KEYS = ("id", "key", "seconds", "trim")
POST_OPTIONAL = ("pitch_points",)  # 修訂 1(R1-10):伺服器依 shape 回傳寫入;PATCH 不能寫入
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


class QuotaExceeded(ProjectError):
    """素材與自錄音檔的磁碟用量超過 MAX_ASSET_BYTES(DIR-5 修正 2 / 3:伺服器據此回 413)。"""


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


# ---------- 連音、清晰度、字詞清晰度、語調(CHG-20261029-01 T1) ----------

def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def check_percent(v, where: str):
    """拉桿百分比:null 或 0~100 的整數(連音、清晰度、圓弧、字詞清晰度值)。"""
    if v is None:
        return None
    if not _is_int(v) or not 0 <= v <= 100:
        raise ProjectError(f"{where}必須是 0 到 100 的整數(百分比)")
    return v


def _check_word_clarity(text: str, items, where: str) -> list[dict]:
    if items is None:
        return []
    if not isinstance(items, list):
        raise ProjectError(f"{where}.word_clarity 必須是陣列")
    if len(items) > MAX_WORD_CLARITY:
        raise ProjectError(f"{where}.word_clarity 每句最多 {MAX_WORD_CLARITY} 段")
    out = []
    for it in items:
        if not isinstance(it, dict) or set(it) != {"start", "end", "value"}:
            raise ProjectError(f"{where}.word_clarity 每項必須恰為 start、end、value 三個欄位")
        a, b = it["start"], it["end"]
        if not _is_int(a) or not _is_int(b) or not 0 <= a < b <= len(text):
            raise ProjectError(f"{where}.word_clarity 的範圍必須在句子之內且起點小於終點")
        if not all(_is_hanzi(ch) for ch in text[a:b]):
            raise ProjectError(f"{where}.word_clarity 只能設定在漢字上(不可含標點或其他字元)")
        out.append({"start": a, "end": b, "value": check_percent(it["value"], f"{where}.word_clarity 的清晰度")})
        if out[-1]["value"] is None:
            raise ProjectError(f"{where}.word_clarity 的清晰度必須是 0 到 100 的整數(百分比)")
    out.sort(key=lambda x: x["start"])
    for x, y in zip(out, out[1:]):
        if y["start"] < x["end"]:
            raise ProjectError(f"{where}.word_clarity 的範圍不可重疊")
    return out


def _check_pitch(p, where: str):
    if p is None:
        return None
    if not isinstance(p, dict) or set(p) != {"points", "roundness"}:
        raise ProjectError(f"{where}.pitch 必須恰為 points、roundness 兩個欄位")
    pts = p["points"]
    if not isinstance(pts, list) or not PITCH_POINTS_MIN <= len(pts) <= PITCH_POINTS_MAX:
        raise ProjectError(f"{where}.pitch 的關鍵點必須是 {PITCH_POINTS_MIN} 到 {PITCH_POINTS_MAX} 個")
    out = []
    for q in pts:
        if not isinstance(q, dict) or set(q) != {"pos", "semi"}:
            raise ProjectError(f"{where}.pitch 每個關鍵點必須恰為 pos、semi 兩個欄位")
        if not _is_number(q["pos"]):
            raise ProjectError(f"{where}.pitch 關鍵點位置必須是 0 到 1 的數字")
        pos = _round(q["pos"], POS_STEP)
        if not Decimal(0) <= pos <= Decimal(1):
            raise ProjectError(f"{where}.pitch 關鍵點位置必須是 0 到 1 的數字")
        if not _is_number(q["semi"]):
            raise ProjectError(f"{where}.pitch 關鍵點音高必須是 {SEMI_MIN} 到 +{SEMI_MAX} 半音的數字")
        semi = _round(q["semi"], SEMI_STEP)
        if not SEMI_MIN <= semi <= SEMI_MAX:
            raise ProjectError(f"{where}.pitch 關鍵點音高必須在 {SEMI_MIN} 到 +{SEMI_MAX} 半音之間")
        out.append({"pos": float(pos), "semi": float(semi) + 0.0})
    if any(b["pos"] <= a["pos"] for a, b in zip(out, out[1:])):
        raise ProjectError(f"{where}.pitch 關鍵點位置必須嚴格遞增")
    return {"points": out, "roundness": check_percent(p["roundness"], f"{where}.pitch 的圓弧")}


def check_pitch_points(points, where: str) -> list:
    """worker 實際套用的語調內部點(修訂 1 R1-10):2–30 個 [位置, 半音],位置 0–1 嚴格遞增、
    半音 ±7.2。不合格丟 ProjectError;回傳新的 list(位置與半音一律為 float)。"""
    if not isinstance(points, list) or not 2 <= len(points) <= INNER_POINTS_MAX:
        raise ProjectError(f"{where} 必須是 2 到 {INNER_POINTS_MAX} 個語調點")
    out = []
    for q in points:
        if not isinstance(q, list) or len(q) != 2 or not all(_is_number(x) for x in q):
            raise ProjectError(f"{where} 每個語調點必須是 [位置, 半音] 兩個數字")
        if not 0 <= q[0] <= 1:
            raise ProjectError(f"{where} 的位置必須在 0 到 1 之間")
        if not -INNER_SEMI_LIMIT <= q[1] <= INNER_SEMI_LIMIT:
            raise ProjectError(f"{where} 的音高必須在 −{INNER_SEMI_LIMIT} 到 +{INNER_SEMI_LIMIT} 半音之間")
        out.append([float(q[0]), float(q[1])])
    if any(b[0] <= a[0] for a, b in zip(out, out[1:])):
        raise ProjectError(f"{where} 的位置必須嚴格遞增")
    return out


def _check_post(post, where: str):
    if post is None:
        return None
    if not isinstance(post, dict) or not set(POST_KEYS) <= set(post) <= {*POST_KEYS, *POST_OPTIONAL}:
        raise ProjectError(f"{where}.post 格式不正確")
    if not isinstance(post["id"], str) or not ID_RE.fullmatch(post["id"]):
        raise ProjectError(f"{where}.post.id 格式不正確")
    if not isinstance(post["key"], str) or not KEY_RE.fullmatch(post["key"]):
        raise ProjectError(f"{where}.post.key 格式不正確")
    for k in ("seconds", "trim"):
        if not _is_number(post[k]) or post[k] < 0:
            raise ProjectError(f"{where}.post.{k} 必須是非負數字")
    out = dict(post)
    if "pitch_points" in post:
        out["pitch_points"] = check_pitch_points(post["pitch_points"], f"{where}.post.pitch_points")
    return out


def _eff(segment: dict, key: str, default_key: str | None = None) -> int:
    v = segment.get(key)
    return PROSODY_DEFAULTS[default_key or key] if v is None else v


def post_params(segment: dict) -> dict:
    """後製參數(null 一律取程式常數;D15)。word_clarity 為 [[start, end, value]],pitch 為 None 或
    {"points": [[pos, semi]], "roundness"}。"""
    pitch = segment.get("pitch")
    if pitch:
        pitch = {"points": [[float(q["pos"]), float(q["semi"])] for q in pitch["points"]],
                 "roundness": PROSODY_DEFAULTS["roundness"] if pitch.get("roundness") is None
                 else pitch["roundness"]}
    else:
        pitch = None
    return {"trim": dict(TRIM_PARAMS), "connect": _eff(segment, "connect"), "clarity": _eff(segment, "clarity"),
            "word_clarity": [{"start": w["start"], "end": w["end"], "value": w["value"]}
                             for w in segment.get("word_clarity") or []],
            "pitch": pitch}


def is_default_post(segment: dict) -> bool:
    """預設參數(連音 0%、清晰度 100%、無字詞清晰度、無語調):後製結果等於裁切結果,不啟動 shape。"""
    p = post_params(segment)
    return p["connect"] == 0 and p["clarity"] == 100 and not p["word_clarity"] and p["pitch"] is None


def post_key(segment: dict) -> str:
    """後製鍵 = SHA-256(JSON 陣列 [audio.id, POST_VERSION, 裁切參數, connect, clarity, word_clarity, pitch]
    + 演算法標記)。

    演算法標記(修訂 1 R1-11)只在用到時附加為陣列最後一項:清晰度 < 100% 或有字詞清晰度時含
    `"clarity": POST_ALGO["clarity"]`、有語調時含 `"pitch": POST_ALGO["pitch"]`(兩者合為一個物件);
    都沒有時不附加,鍵與修訂前相同。語速、停頓不在鍵內(語速另以變速檔快取)。
    只對合成片段(audio 帶 key)有意義,其他丟 ProjectError。"""
    audio = segment.get("audio")
    if not isinstance(audio, dict) or "key" not in audio:
        raise ProjectError("只有合成的片段有後製(自錄音檔不做後製)")
    p = post_params(segment)
    parts = [audio["id"], POST_VERSION, p["trim"], p["connect"], p["clarity"],
             [[w["start"], w["end"], w["value"]] for w in p["word_clarity"]], p["pitch"]]
    algo = {}
    if p["clarity"] < 100 or p["word_clarity"]:
        algo["clarity"] = POST_ALGO["clarity"]
    if p["pitch"] is not None:
        algo["pitch"] = POST_ALGO["pitch"]
    if algo:
        parts.append(algo)
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def is_post_fresh(segment: dict) -> bool:
    """片段的 post 與目前參數相符(只比對資料,不檢查檔案是否存在)。"""
    post = segment.get("post")
    audio = segment.get("audio")
    if not post or not isinstance(audio, dict) or "key" not in audio:
        return False
    return post.get("key") == post_key(segment)


def _check_fusions(text: str, items, annotations, word_clarity, where: str) -> list[dict]:
    """合音(D27):範圍 [index, index + length) 為 2~4 個漢字;zhuyin 為 1~(length − 1) 個音節(半形空白分隔);
    各組不重疊;範圍內沒有聲調標註;字詞清晰度完整包含或完全不含合音範圍。
    (修訂 1 R1-4)選填 display:空字串或 1~4 個漢字;有給才輸出(舊專案檔不補欄位、內容不變)。"""
    if items is None:
        return []
    if not isinstance(items, list):
        raise ProjectError(f"{where}.fusions 必須是陣列")
    if len(items) > MAX_FUSIONS:
        raise ProjectError(f"{where}.fusions 每句最多 {MAX_FUSIONS} 組")
    out = []
    for f in items:
        if not isinstance(f, dict) or set(f) - {"display"} != {"index", "length", "zhuyin", "source"}:
            raise ProjectError(f"{where}.fusions 每項必須恰為 index、length、zhuyin、source 四個欄位"
                               "(另可選填 display)")
        i, n = f["index"], f["length"]
        if not _is_int(n) or not FUSION_MIN <= n <= FUSION_MAX:
            raise ProjectError(f"{where}.fusions 的字數必須是 {FUSION_MIN} 到 {FUSION_MAX}")
        if not _is_int(i) or not 0 <= i or i + n > len(text):
            raise ProjectError(f"{where}.fusions 的位置超出句子範圍")
        if not all(_is_hanzi(c) for c in text[i:i + n]):
            raise ProjectError(f"{where}.fusions 只能合併相鄰的漢字")
        if f["source"] not in FUSION_SOURCES:
            raise ProjectError(f"{where}.fusions 的來源只能是 rule、lexicon、manual")
        z = f["zhuyin"]
        parts = z.split(" ") if isinstance(z, str) else []
        if not 1 <= len(parts) <= n - 1:
            raise ProjectError(f"{where}:合音注音的音節數必須少於字數(1 到 {n - 1} 個,以半形空白分隔)")
        for part in parts:
            try:
                check_zhuyin(part)
            except ProjectError as e:
                raise ProjectError(f"{where}:合音{e.errors[0]}") from None
        item = {"index": i, "length": n, "zhuyin": z, "source": f["source"]}
        if "display" in f:
            d = f["display"]
            if not isinstance(d, str) or len(d) > FUSION_DISPLAY_MAX or not all(_is_hanzi(c) for c in d):
                raise ProjectError(f"{where}:合音的顯示字必須是空字串或 1 到 {FUSION_DISPLAY_MAX} 個漢字")
            item["display"] = d
        out.append(item)
    out.sort(key=lambda x: x["index"])
    if any(b["index"] < a["index"] + a["length"] for a, b in zip(out, out[1:])):
        raise ProjectError(f"{where}.fusions 的範圍不可重疊")
    marked = {a["index"] for a in annotations or []}
    for f in out:
        span = set(range(f["index"], f["index"] + f["length"]))
        if span & marked:
            raise ProjectError(f"{where}:合音範圍內有聲調標註,請先清除該字的念法")
        for w in word_clarity or []:
            inside = [w["start"] <= k < w["end"] for k in span]
            if any(inside) and not all(inside):
                raise ProjectError(f"{where}:字詞清晰度的範圍必須完整包含或完全不含合音的字")
    return out


def _check_fusion_off(text: str, items, where: str) -> list[dict]:
    """停用合音的位置(D41):[{"index", "length": 2~4}],範圍在句內,每句 ≤ MAX_FUSION_OFF 筆。"""
    if items is None:
        return []
    if not isinstance(items, list) or len(items) > MAX_FUSION_OFF:
        raise ProjectError(f"{where}.fusion_off 必須是最多 {MAX_FUSION_OFF} 筆的陣列")
    out = []
    for d in items:
        if not isinstance(d, dict) or set(d) != {"index", "length"}:
            raise ProjectError(f"{where}.fusion_off 每項必須恰為 index、length 兩個欄位")
        i, n = d["index"], d["length"]
        if not _is_int(n) or not FUSION_MIN <= n <= FUSION_MAX or not _is_int(i) or i < 0 or i + n > len(text):
            raise ProjectError(f"{where}.fusion_off 的範圍超出句子")
        if {"index": i, "length": n} not in out:
            out.append({"index": i, "length": n})
    return sorted(out, key=lambda x: (x["index"], x["length"]))


def _changed_range(old: str, new: str) -> tuple[int, int, int]:
    """old → new 的改動範圍(以共同前綴與共同後綴求出):回傳 (舊文字中的起點, 終點, 長度差)。"""
    p = 0
    while p < min(len(old), len(new)) and old[p] == new[p]:
        p += 1
    q = 0
    while q < min(len(old), len(new)) - p and old[len(old) - 1 - q] == new[len(new) - 1 - q]:
        q += 1
    return p, len(old) - q, len(new) - len(old)


def _shift(items, a: int, b: int, delta: int) -> list[dict]:
    out = []
    for d in items or []:
        i, n = d["index"], d["length"]
        if i + n <= a and not (a == b and i + n > a):
            out.append({**d})
        elif i >= b and (a < b or i >= a):
            out.append({**d, "index": i + delta})
    return out


def shift_marks(segment: dict, old_text: str, new_text: str) -> dict:
    """句子文字由 old_text 改成 new_text 時調整合音狀態(D41):改動範圍之前的紀錄不動、之後的依位移調整、
    與改動範圍重疊者清除(回到未決定)。回傳 {"fusions", "fusion_off", "cleared": 被清除的 fusions 筆數}。"""
    a, b, delta = _changed_range(old_text, new_text)
    fus = segment.get("fusions") or []
    kept = _shift(fus, a, b, delta)
    return {"fusions": kept, "fusion_off": _shift(segment.get("fusion_off"), a, b, delta),
            "cleared": len(fus) - len(kept)}


def fused_out(segment) -> set:
    """合音範圍內不送模型的原字索引:注音路線(D26)n 字合成 k 個音節時為後 n − k 字;
    帶顯示字(修訂 1 R1-4)時整個範圍都換成顯示字,範圍內每個原字都不送。"""
    out = set()
    for f in segment.get("fusions") or []:
        k = 0 if f.get("display") else len(f["zhuyin"].split(" "))
        out |= set(range(f["index"] + k, f["index"] + f["length"]))
    return out


def model_units(segment) -> list[int]:
    """(修訂 1 R1-5)送模型的每個漢字依序對應的原文索引:合音範圍外的原字、注音路線留下的前 k 字,
    以及顯示字(第 j 個顯示字對應到合音範圍內第 min(j, length − 1) 個字;顯示字比範圍長時末字重複對應)。
    字詞清晰度必須完整包含或完全不含合音範圍(D27),因此換算後涵蓋顯示字的全部字。"""
    text = segment["text"]
    shown = {f["index"]: f for f in segment.get("fusions") or [] if f.get("display")}
    skip = fused_out(segment)
    out = []
    for i, ch in enumerate(text):
        if i in shown:
            f = shown[i]
            out += [i + min(j, f["length"] - 1) for j in range(len(f["display"]))]
        if _is_hanzi(ch) and i not in skip:
            out.append(i)
    return out


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
    """合成文字:原文跳脫後,在每個標註的字後插入 `[:注音]`(聲調符號格式,worker 再轉數字聲調)。
    合音(D26):前 k 字依序帶 `[:音節]`、刪除其餘字;(修訂 1 R1-4)帶顯示字時整段換成顯示字、不加注音。"""
    if not isinstance(segment, dict):
        raise ProjectError("片段格式不正確")
    text = segment.get("text")
    if not isinstance(text, str) or not text:
        raise ProjectError("片段文字是空的")
    checked = _check_annotations(text, segment.get("annotations"), "片段")
    anns = {a["index"]: a["zhuyin"] for a in checked}
    fus = {}   # 字元索引 → 該字帶的合音音節;合音範圍內其餘字不送模型(D26)
    shown = {}   # 合音起點 → 顯示字(R1-4:取代整個範圍)
    skip = set()
    for f in _check_fusions(text, segment.get("fusions"), checked, segment.get("word_clarity"), "片段"):
        if f.get("display"):
            shown[f["index"]] = f["display"]
            skip |= set(range(f["index"], f["index"] + f["length"]))
            continue
        parts = f["zhuyin"].split(" ")
        for j in range(f["length"]):
            if j < len(parts):
                fus[f["index"] + j] = parts[j]
            else:
                skip.add(f["index"] + j)
    out = []
    for i, ch in enumerate(escape_text(text)):
        if i in shown:
            out.append(escape_text(shown[i]))
        if i in skip:
            continue
        out.append(ch)
        if i in fus:
            out.append(f"[:{fus[i]}]")
        elif i in anns:
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


def _check_version(v) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or (v != SCHEMA_VERSION and v not in OLD_SCHEMA_VERSIONS):
        shown = repr(v) if isinstance(v, (int, float, str, type(None))) else type(v).__name__
        raise ProjectError(f"專案檔版本 {shown[:20]} 不支援,拒絕載入"
                           f"(本版讀 schema_version {SCHEMA_VERSION},舊版 1 會自動升級)")
    return v


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


def _validate_segment(s, where, errors, seen, version: int = SCHEMA_VERSION) -> dict | None:
    if not _check_keys(s, SEGMENT_KEYS if version == SCHEMA_VERSION else SEGMENT_KEYS_V1, ("id", "text"),
                       where, errors):
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
    # CHG-20261029-01 T1(v2 欄位;v1 載入時一律補預設值)
    n_err = len(errors)
    out["connect"] = _try(errors, check_percent, s.get("connect"), f"{where}.connect(連音)")
    out["clarity"] = _try(errors, check_percent, s.get("clarity"), f"{where}.clarity(清晰度)")
    out["word_clarity"] = _try(errors, _check_word_clarity, text, s.get("word_clarity"), where)
    out["pitch"] = _try(errors, _check_pitch, s.get("pitch"), where)
    out["post"] = _try(errors, _check_post, s.get("post"), where)
    if out["annotations"] is not None and out["word_clarity"] is not None:
        out["fusions"] = _try(errors, _check_fusions, text, s.get("fusions"), out["annotations"],
                              out["word_clarity"], where)
    else:
        out["fusions"] = []
    out["fusion_off"] = _try(errors, _check_fusion_off, text, s.get("fusion_off"), where)
    if out["fusions"] and out["fusion_off"]:
        on = {(f["index"], f["length"]) for f in out["fusions"]}
        if any((d["index"], d["length"]) in on for d in out["fusion_off"]):
            errors.append(f"{where}:同一範圍不能同時啟用與停用合音")
            ok = False
    if len(errors) != n_err:
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
    version = _check_version(data.get("schema_version"))
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
    segments = [_validate_segment(s, f"segments[{i}]", errors, seen, version)
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

def _top() -> Path:
    """受信根目錄(repo 根,`ROOT` 解析後的真實路徑);之下各層一律以 dir_fd 逐層 O_NOFOLLOW 開啟。"""
    return Path(ROOT).resolve()


def _child(dirfd: int, part: str, label: str, create: bool) -> int:
    """dirfd 內的子目錄 part(DIR-5 修正 4:以 safe_fs 逐層開啟,檢查與使用是同一個 fd)。

    是符號連結或不是目錄丟 UnsafePath;不存在且 create=False 丟 FileNotFoundError(label)。"""
    try:
        return safe_fs.open_child(dirfd, part, create=create)
    except safe_fs.UnsafePathError:
        st = safe_fs.lstat(dirfd, part)
        if st is not None and stat.S_ISLNK(st.st_mode):
            raise UnsafePath(f"資料目錄「{label}」是符號連結,不接受") from None
        raise UnsafePath(f"資料目錄「{label}」不在預期位置") from None
    except FileNotFoundError:
        raise FileNotFoundError(label) from None


def _root_fd(create: bool) -> int:
    """`projects/` 的 fd(呼叫端關閉);不存在且 create=False 丟 FileNotFoundError。"""
    with safe_fs.closing(safe_fs.open_dir(_top())) as top:
        return _child(top, PROJECTS_NAME, PROJECTS_NAME, create)


@contextlib.contextmanager
def _project_lock(pfd: int):
    try:
        fd = safe_fs.open_or_create(pfd, LOCK_FILE, os.O_RDWR, 0o600)
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


def _case_variant(rfd: int, name: str) -> str | None:
    """找出與 name 只差大小寫(casefold 相等、本身不同)的既有項目;macOS 預設檔案系統不分大小寫。"""
    key = names.fold_name(name)
    with os.scandir(rfd) as it:
        for entry in it:
            if entry.name != name and not entry.name.startswith(".") and names.fold_name(entry.name) == key:
                return entry.name
    return None


def _project_fd(rfd: int, name: str, create: bool) -> int:
    """專案目錄的 fd;已有只差大小寫的其他專案時一律拒絕(不分大小寫的檔案系統上兩者是同一個目錄)。"""
    if _case_variant(rfd, name) is not None:
        if create:
            raise ProjectExists("已有只差大小寫的同名專案,請改用其他名稱")
        raise ProjectNotFound("找不到這個專案(已有只差大小寫的其他專案)")
    try:
        return _child(rfd, name, "專案", create=create)
    except FileNotFoundError:
        raise ProjectNotFound("找不到這個專案") from None


def open_project(name, *, create: bool = False) -> int:
    """`projects/<名稱>/` 的 fd(呼叫端關閉;DIR-5 修正 4)。各層 O_NOFOLLOW,不符丟 UnsafePath;
    專案不存在丟 ProjectNotFound。"""
    name = check_name(name)
    try:
        rfd = _root_fd(create=create)
    except FileNotFoundError:
        raise ProjectNotFound("找不到這個專案") from None
    with safe_fs.closing(rfd):
        return _project_fd(rfd, name, create)


SUBDIRS = ("audio", "assets", "tmp")


def open_subdir(name, sub: str, *, create: bool = False) -> int:
    """專案子目錄 `projects/<名稱>/<sub>` 的 fd(呼叫端關閉;DIR-5 修正 4)。

    自 repo 根目錄以 dir_fd 逐層 O_NOFOLLOW | O_DIRECTORY 開啟;任一層是符號連結或不是目錄丟 UnsafePath,
    create=False 且子目錄不存在丟 FileNotFoundError,專案不存在丟 ProjectError。之後的讀寫刪一律相對此 fd。"""
    if sub not in SUBDIRS:
        raise ValueError(f"不支援的專案子目錄:{sub}")
    with safe_fs.closing(open_project(name, create=create)) as pfd:
        return _child(pfd, sub, sub, create=create)


def subdir(name, sub: str, *, create: bool = False) -> Path:
    """專案子目錄 `projects/<名稱>/<sub>`(audio、assets、tmp)的路徑(CHG-20261022-04 T3 起共用)。

    各層一律不得是符號連結、必須在預期位置,不符丟 UnsafePath;create=False 且子目錄不存在時
    丟 FileNotFoundError,專案不存在時丟 ProjectError。DIR-5 修正 4 起只用於「交給外部程式的路徑」
    (worker、ffmpeg)與顯示;本程式自己的讀寫刪一律改用 `open_subdir()` 的 fd。
    """
    name = check_name(name)
    os.close(open_subdir(name, sub, create=create))
    return _top() / PROJECTS_NAME / name / sub


def unlink_in(name, sub: str, filename: str) -> None:
    """刪除專案子目錄內的單一檔案(相對 dir_fd,不跟隨;不存在不算錯)。供複製失敗時清理。"""
    try:
        dfd = open_subdir(name, sub)
    except (FileNotFoundError, ProjectError):
        return
    with safe_fs.closing(dfd):
        safe_fs.unlink(dfd, filename, missing_ok=True)


def disk_usage(name) -> int:
    """專案 `assets/` 與 `audio/` 內一般檔(不跟隨符號連結)的位元組合計;素材與自錄音檔的共同配額以此計算。

    (CHG-20261022-04 DIR-5 修正 3:由 studio_server 搬來共用,規則不變;修正 4 改以 dir_fd 列舉。)"""
    total = 0
    for sub in ("assets", "audio"):
        try:
            dfd = open_subdir(name, sub)
        except FileNotFoundError:
            continue
        with safe_fs.closing(dfd):
            for _, st in safe_fs.entries(dfd):
                if stat.S_ISREG(st.st_mode):
                    total += st.st_size
    return total


def check_quota(name, incoming: int) -> None:
    """加入 incoming 位元組後,素材與自錄音檔合計超過 MAX_ASSET_BYTES → 丟 QuotaExceeded。

    以磁碟實際用量計算,反覆取代同一句留下的舊自錄檔也算在內;舊檔不刪(history 版本可能仍引用)。
    呼叫端須在專案鎖內(studio_server 的 mutate、nodes 的 update)、複製前呼叫,檢查與複製之間不會有其他寫入。
    studio_server 與「送到素材庫」節點共用此函式。"""
    limit = MAX_ASSET_BYTES
    if disk_usage(name) + incoming > limit:
        raise QuotaExceeded(f"素材與自錄音檔合計超過 {limit / 1024 ** 3:g} GB 上限,無法再加入")


# ---------- 讀寫 ----------
# DIR-5 修正 4:以下讀、寫、搬移、刪除一律相對已開啟的目錄 fd(safe_fs),不再以路徑檢查後再以路徑操作。

def _read_project_file(dirfd: int, filename: str) -> dict:
    try:
        fd = safe_fs.open_file(dirfd, filename)
    except FileNotFoundError:
        raise ProjectNotFound("找不到這個專案") from None
    except safe_fs.UnsafePathError:
        raise ProjectError("專案檔無法讀取(可能是符號連結或不是一般檔案)") from None
    except OSError:
        raise ProjectError("專案檔無法讀取(可能是符號連結)") from None
    with os.fdopen(fd, "rb") as f:
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
    """讀 `projects/<名稱>/project.json`;schema_version 1 在記憶體升級為 2(存檔時才寫回),其他版本拒絕載入。"""
    name = check_name(name)
    with safe_fs.closing(open_project(name)) as pfd:
        data = _read_project_file(pfd, PROJECT_FILE)
    _check_version(data.get("schema_version"))
    if data.get("name") != name:
        raise ProjectError("專案檔的名稱與專案目錄不符,拒絕載入")
    return Project.from_dict(data)


def _write_atomic(pfd: int, payload: bytes) -> None:
    for _ in range(LINK_TRIES):
        tmp = f".project-{secrets.token_hex(8)}.tmp"
        try:
            fd = safe_fs.create_excl(pfd, tmp, 0o644)
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
        safe_fs.replace(pfd, tmp, pfd, PROJECT_FILE)
    except BaseException:
        with contextlib.suppress(OSError):
            safe_fs.unlink(pfd, tmp)
        raise


def _archive_current(pfd: int, hfd: int) -> str | None:
    """把目前的 project.json 以 os.link 排他建立到 history/(檔名唯一,不覆寫);沒有舊檔回 None。"""
    st = safe_fs.lstat(pfd, PROJECT_FILE)
    if st is None:
        return None
    if not stat.S_ISREG(st.st_mode):
        raise UnsafePath("專案檔不是一般檔案(可能是符號連結),拒絕存檔")
    for _ in range(LINK_TRIES):
        vid = f"{_stamp()}-{_rand4()}"
        try:
            safe_fs.link(pfd, PROJECT_FILE, hfd, f"{vid}.json")
            return vid
        except FileExistsError:
            continue
    raise ProjectError("無法建立不重複的版本檔名")


def _history_entries(hfd: int) -> list[tuple]:
    out = []
    for fn, st in safe_fs.entries(hfd):
        m = HISTORY_RE.fullmatch(fn)
        if m and stat.S_ISREG(st.st_mode):
            out.append((m.group(1), st.st_mtime_ns, fn))
    out.sort()
    return out


def _rotate(pfd: int, pname: str, warnings: list) -> list[str]:
    """history/ 只保留最近 HISTORY_KEEP 個;history/ 是符號連結或不是目錄時拒絕處理並記警告。"""
    try:
        hfd = _child(pfd, "history", "history", create=False)
    except FileNotFoundError:
        return []
    except UnsafePath as e:
        _warn(warnings, f"專案「{pname}」的 history 目錄不安全,拒絕輪替:{e.errors[0]}")
        return []
    with safe_fs.closing(hfd):
        entries = _history_entries(hfd)
        removed = []
        for _, _, fn in entries[:max(0, len(entries) - HISTORY_KEEP)]:
            safe_fs.unlink(hfd, fn, missing_ok=True)
            removed.append(fn)
    return removed


def save(project: Project) -> str | None:
    """原子寫入 project.json;寫入前把舊檔移入 history/,history/ 只留最近 10 個。

    同一專案的存檔以專案層級鎖序列化。回傳這次移入 history 的版本 id(第一次存檔為 None)。
    專案目錄、history/ 是符號連結或不是目錄時拒絕存檔(丟 UnsafePath 並記警告),不寫入連結目標。
    """
    data = validate(project.to_dict())
    name = data["name"]
    warnings: list[str] = []
    try:
        with safe_fs.closing(open_project(name, create=True)) as pfd, _project_lock(pfd):
            return _save_locked(pfd, name, data, warnings)
    except UnsafePath as e:
        _warn(warnings, f"專案「{name}」拒絕存檔:{e.errors[0]}")
        raise


def _save_locked(pfd: int, name: str, data: dict, warnings: list) -> str | None:
    """已持有專案鎖時的存檔本體(舊檔移入 history/ → 原子寫入 → 輪替)。"""
    payload = _dump(data)
    with safe_fs.closing(_child(pfd, "history", "history", create=True)) as hfd:
        vid = _archive_current(pfd, hfd)
    _write_atomic(pfd, payload)
    _rotate(pfd, name, warnings)
    return vid


def update(name, change):
    """「載入 → change(project) → 驗證 → 存檔」全程持專案層級鎖(CHG-20261022-04 T8)。

    跨執行緒也跨行程序列化同一專案的修改,避免兩處各自載入後互相覆蓋(例:「送到素材庫」節點與
    網頁的修改同時發生)。change 可原地修改 project 並回傳任意值;change 或驗證丟例外時不存檔。
    回傳 (存檔後的 Project, 版本 id 或 None, change 的回傳值)。專案不存在丟 ProjectNotFound。
    """
    name = check_name(name)
    warnings: list[str] = []
    try:
        with safe_fs.closing(open_project(name)) as pfd, _project_lock(pfd):
            proj = load(name)
            extra = change(proj)
            data = validate(proj.to_dict())
            if data["name"] != name:
                raise ProjectError("不能在修改時改變專案名稱")
            vid = _save_locked(pfd, name, data, warnings)
    except UnsafePath as e:
        _warn(warnings, f"專案「{name}」拒絕存檔:{e.errors[0]}")
        raise
    return Project.from_dict(data), vid, extra


def _history_fd(name) -> int:
    with safe_fs.closing(open_project(name)) as pfd:
        return _child(pfd, "history", "history", create=False)


def list_history(name) -> list[str]:
    """回傳版本 id(舊到新)。"""
    name = check_name(name)
    try:
        hfd = _history_fd(name)
    except (FileNotFoundError, ProjectNotFound):
        return []
    with safe_fs.closing(hfd):
        return [fn[:-len(".json")] for _, _, fn in _history_entries(hfd)]


def load_version(name, vid) -> Project:
    """讀 `history/<版本 id>.json`(T6 還原版本用);版本 id 格式不對或不存在丟 ProjectNotFound。"""
    name = check_name(name)
    if not isinstance(vid, str) or not VERSION_RE.fullmatch(vid):
        raise ProjectNotFound("找不到這個版本")
    try:
        hfd = _history_fd(name)
    except FileNotFoundError:
        raise ProjectNotFound("找不到這個版本") from None
    with safe_fs.closing(hfd):
        try:
            data = _read_project_file(hfd, f"{vid}.json")
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
                              "speed": None, "annotations": [], "audio": None, "connect": None,
                              "clarity": None, "word_clarity": [], "pitch": None, "post": None,
                              "fusions": [], "fusion_off": []}
                             for p in pieces])
    proj = Project.from_dict(proj.to_dict())
    with safe_fs.closing(_root_fd(create=True)) as rfd:
        if _case_variant(rfd, name) is not None or safe_fs.lstat(rfd, name) is not None:
            raise ProjectExists("已有同名的專案,請改用其他名稱")
    save(proj)
    return proj


def list_projects() -> list[dict]:
    """列出 `projects/` 下有專案檔的專案:[{"name", "updated": 專案檔修改時間(epoch 秒)}],依名稱排序。

    點開頭的項目(`.trash/` 等)、名稱不合規則、符號連結、沒有一般檔案 project.json 的目錄一律略過。
    """
    try:
        rfd = _root_fd(create=False)
    except FileNotFoundError:
        return []
    out = []
    with safe_fs.closing(rfd):
        for name, st in safe_fs.entries(rfd):
            if name.startswith(".") or names.normalize_name(name) is None or not stat.S_ISDIR(st.st_mode):
                continue
            try:
                pfd = safe_fs.open_child(rfd, name)
            except OSError:
                continue
            with safe_fs.closing(pfd):
                pst = safe_fs.lstat(pfd, PROJECT_FILE)
            if pst is not None and stat.S_ISREG(pst.st_mode):
                out.append({"name": name, "updated": pst.st_mtime})
    return out


def trash(name, *, confirm, now: float | None = None) -> str:
    """把專案移到 `projects/.trash/<名稱>-<時間戳>-<4 碼隨機>/`;confirm 必須為 True,不覆蓋既有項目。

    搬移以 rename(src_dir_fd=projects/, dst_dir_fd=.trash/)對單一名稱操作,不經任何路徑。"""
    if confirm is not True:
        raise ProjectError("刪除專案需要確認(confirm: true)")
    name = check_name(name)
    try:
        rfd = _root_fd(create=False)
    except FileNotFoundError:
        raise ProjectNotFound("找不到這個專案") from None
    with safe_fs.closing(rfd):
        with safe_fs.closing(_project_fd(rfd, name, create=False)) as pfd, \
                safe_fs.closing(_child(rfd, TRASH_NAME, TRASH_NAME, create=True)) as tfd, _project_lock(pfd):
            for _ in range(LINK_TRIES):
                dest = f"{name}-{_stamp(now)}-{_rand4()}"
                if safe_fs.lstat(tfd, dest) is None:
                    safe_fs.rename(rfd, name, tfd, dest)
                    return dest
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


def _clean_trash(rfd: int, now_ts: float, result: dict) -> None:
    try:
        tfd = _child(rfd, TRASH_NAME, TRASH_NAME, create=False)
    except FileNotFoundError:
        return
    except UnsafePath as e:
        _warn(result["warnings"], f"回收區不安全,拒絕清除:{e.errors[0]}")
        return
    limit = TRASH_DAYS * 86400
    with safe_fs.closing(tfd):
        for name, st in safe_fs.entries(tfd):
            if stat.S_ISLNK(st.st_mode):
                _warn(result["warnings"], f"回收區項目「{name}」是符號連結,不跟隨、不刪除")
                continue
            m = TRASH_RE.fullmatch(name)
            ts = _parse_stamp(m.group(2)) if m else None
            if ts is None:
                _warn(result["warnings"], f"回收區項目「{name}」名稱不含時間戳,略過")
                continue
            if now_ts - ts >= limit:
                safe_fs.rmtree(tfd, name, missing_ok=True)   # 以 fd 遞迴;內部符號連結只刪連結本身
                result["trash_removed"].append(name)


def _clean_project(rfd: int, name: str, result: dict) -> None:
    warnings = result["warnings"]
    try:
        pfd = _child(rfd, name, "專案", create=False)
    except (UnsafePath, FileNotFoundError):
        _warn(warnings, f"專案目錄「{name}」是符號連結或不在預期位置,拒絕處理")
        return
    with safe_fs.closing(pfd):
        for sub in ("history", "audio", "assets"):
            st = safe_fs.lstat(pfd, sub)
            if st is not None and stat.S_ISLNK(st.st_mode):
                _warn(warnings, f"專案「{name}」的 {sub} 目錄是符號連結,拒絕處理")
        try:
            tfd = _child(pfd, "tmp", "tmp", create=False)
        except FileNotFoundError:
            return
        except UnsafePath:
            _warn(warnings, f"專案「{name}」的 tmp 目錄是符號連結或不在預期位置,拒絕清空")
            return
    with safe_fs.closing(tfd):
        for entry, _ in safe_fs.entries(tfd):
            safe_fs.rmtree(tfd, entry, missing_ok=True)
            result["tmp_cleared"] += 1


def startup_cleanup(now) -> dict:
    """伺服器啟動時呼叫:清除 `.trash/` 中滿 30 天的項目、清空各專案 `tmp/` 的殘留。

    now 為注入的現在時間(datetime 或 epoch 秒數;不帶時區的 datetime 視為 UTC);回收項目的年齡
    以其名稱內的時間戳(UTC)判定,不看檔案時間。只刪 `.trash/` 與 `tmp/` 之下的項目,不動
    project.json、audio/、assets/、history/;符號連結一律不跟隨。DIR-5 修正 4 起各層以 dir_fd 開啟、
    刪除以 safe_fs.rmtree(fd 遞迴)進行,檢查與刪除之間把目錄換成符號連結也不會刪到 projects/ 之外。
    """
    now_ts = _now_ts(now)
    result = {"trash_removed": [], "tmp_cleared": 0, "warnings": []}
    try:
        rfd = _root_fd(create=False)
    except FileNotFoundError:
        return result
    except UnsafePath as e:
        _warn(result["warnings"], f"projects 目錄不安全,拒絕清理:{e.errors[0]}")
        return result
    with safe_fs.closing(rfd):
        _clean_trash(rfd, now_ts, result)
        for name, _ in safe_fs.entries(rfd):
            if name.startswith("."):
                continue
            if names.normalize_name(name) is None:
                _warn(result["warnings"], "projects 目錄內有名稱不合規則的項目,略過")
                continue
            _clean_project(rfd, name, result)
    return result
