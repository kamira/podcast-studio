"""模型設定(CHG-20261022-01 T1;T3 修訂 2 起改存 SQLite `config/studio.db`):網頁與 MCP 共用。

介面(簽名與回傳結構與 T1 相同):
  DEFAULTS                       程式內預設值(取自 taiwan_ai.py 現有常數與 DIR-1 席位表)
  DB_PATH                        設定資料庫,相對於 ROOT 的 `config/studio.db`(不入 git)
  load() -> dict                 讀設定。正常讀取時回傳中**沒有** `_warnings` 鍵;以下情況回
                                 DEFAULTS 的副本並在回傳的 dict 加上 `_warnings: list[str]`
                                 (同時寫 stderr):路徑不安全(config/、studio.db、-wal、-shm
                                 任一層是符號連結或不在 repo 內,此時不開資料庫)、資料庫不是
                                 SQLite 或損壞、`schema_version` 不明、讀出的內容不合法。
                                 資料庫不存在時:`config/models.json` 也不存在 → DEFAULTS 的副本
                                 (不建立資料庫);JSON 壞掉或不合法 → DEFAULTS + `_warnings`
                                 (不建立資料庫);JSON 合法 → 建立資料庫並在單一交易內匯入一次,
                                 之後只讀資料庫,程式不刪除 JSON 原檔。
  save(cfg) -> None              先做結構檢查(不含模型是否已下載),不合格丟 ValueError
                                 (args[0] 為錯誤清單);cfg 若帶 `_warnings` 會先移除再檢查。
                                 合格才以 `BEGIN IMMEDIATE` 單一交易整批改寫 roles、review_seats
                                 (cfg 帶 voice_defaults 時一併改寫;省略則不動)(任何例外 → ROLLBACK)。
                                 路徑不安全、資料庫不是 SQLite、版本不明時丟 ValueError,不寫入、
                                 不覆寫、不重建。第 1 版資料庫在同一個交易內先升級再存入。
                                 語速取到 0.05、停頓取到 0.1 後才寫入;超出範圍(語速 0.5–2.0、
                                 停頓 0–5)丟 ValueError。
  validate(cfg, available_models) -> list[str]
                                 回傳逐欄的繁體中文錯誤訊息;空清單代表合格。
                                 available_models 為 Ollama 已下載的模型名稱集合;
                                 傳 None 時只做結構檢查(save 與讀出資料庫時使用)。
  load_meta() -> dict            (T9)唯讀取得 meta 的 imported_from、imported_at;
                                 資料庫不存在或不能用時回空 dict;不建立、不匯入、不寫入。
  voice_options() -> list[str]   (T9)VOICE_DIRS 下可選的 .wav 聲線檔(repo 相對路徑,
                                 不跟隨符號連結;每筆都能通過 validate 的路徑規則)。

load() 與 save() 的回傳 / 輸入結構(CHG-20261022-04 T8 起):{"roles", "review_seats", "voice_defaults"};
voice_defaults = {"speed", "pause_comma", "pause_sentence", "pause_paragraph"}(鍵同 project.DEFAULTS)。

資料庫(schema v2;CHG-20261022-04 T8 由 v1 升級):
  meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)        schema_version、imported_from、imported_at
  roles(role TEXT PRIMARY KEY, config TEXT NOT NULL)      config 為該角色鍵值的 JSON
  review_seats(seat TEXT PRIMARY KEY, model TEXT NOT NULL)
  voice_defaults(key TEXT PRIMARY KEY, value REAL NOT NULL)   (v2 新增)語速與三種停頓預設

升級 v1 → v2(安全影響 prod-migration):load() 唯讀探測到 schema_version = 1 時,取初始化鎖、
在鎖內再唯讀探測一次,仍為 1 才以讀寫模式開啟,於單一 `BEGIN IMMEDIATE` 交易內:交易內再讀一次
版本(另一行程已升到 2 就略過)→ 建 voice_defaults 表 → 寫入預設值 → meta.schema_version 改為 2。
不改 roles、review_seats 任何列。交易內任何例外一律 ROLLBACK,資料庫維持 v1,load() 回傳 v1 的
roles、review_seats 加程式內語音預設與 `_warnings`(WARN_DB_UPGRADE),下次 load() 再試。
版本不是 1 或 2 的資料庫照舊不讀不寫(位元組不變)。升級可重複執行:已是 2 的資料庫不再寫入。
新建與一次性匯入的資料庫直接建為 v2。

回滾(git revert 本筆後,舊版程式讀到 schema_version = 2 會回預設值):先把資料庫備份到 repo 外
(`sqlite3 config/studio.db ".backup '<repo 外路徑>/studio.db.bak'"`),停止網頁與 MCP 伺服器,再於
單一交易內降回 v1:
  sqlite3 config/studio.db "BEGIN IMMEDIATE; DROP TABLE voice_defaults;
    UPDATE meta SET value = '1' WHERE key = 'schema_version'; COMMIT;"
roles、review_seats 不受影響;語音預設回到各專案自己的 defaults 與 project.DEFAULTS。
此步驟由使用者執行或核准後執行。

開啟規則:既有資料庫一律先以唯讀 URI(`mode=ro`)讀 `meta.schema_version`,此階段不下任何
PRAGMA;只有版本已知、或新建(沒有任何表)的資料庫,才以讀寫模式開啟並設定
`PRAGMA busy_timeout=5000`、`PRAGMA journal_mode=WAL`。資料庫檔一律由本模組以排他方式
建立(權限 0600),SQLite 本身不會建立資料庫檔(`mode=rw`)。SQL 一律參數化,表名與欄名
寫死在本檔。

初始化鎖(依 PR #37 審議新增):資料庫不存在或還沒有任何表時,建立檔案、第一次匯入或存入、
失敗時清理,以及 v1 → v2 升級(T8),一律在跨行程鎖 `config/.studio.db.lock`
(`fcntl.flock(LOCK_EX)`,不入 git、不刪除)內進行;v2 的既有資料庫一般讀寫不取這把鎖。

安全影響 (c) 跨模組信任:模型名稱必須在 Ollama 清單內;聲線檔限
`tools/BreezyVoice/data/` 與 `outputs/uploads/` 之下;否決席不得為 glm4(DIR-1 第 6 條)。
安全影響 (e):資料庫讀出的設定一律再經 `validate(cfg, None)`,不合法整份回預設值。
"""
from __future__ import annotations

import contextlib
import copy
import fcntl
import json
import math
import os
import sqlite3
import stat
import sys
import time
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "models.json"  # 只作一次性匯入來源(T3 起移出 git)
DB_PATH = Path("config") / "studio.db"  # 相對於 ROOT

SCHEMA_VERSION = "2"           # CHG-20261022-04 T8:新增 voice_defaults 表(1 → 2)
SCHEMA_V1 = "1"
KNOWN_SCHEMA_VERSIONS = frozenset({SCHEMA_V1, SCHEMA_VERSION})
IMPORTED_FROM = "config/models.json"
BUSY_TIMEOUT_MS = 5000

# 聲線檔允許的目錄(相對 repo 根目錄)
VOICE_DIRS = ("tools/BreezyVoice/data", "outputs/uploads")

CODEX = "codex"  # 審議席位由 Codex(codex-cli)擔任的代號,見 DIR-1 第 9、10 條
# 暫停用於審議席位的模型(DIR-1 第 10 條「目前先暫不使用27B模型審議」);
# 使用者明示恢復時移除。只限 review_seats,不影響 roles.reason_heavy(產品功能)。
PAUSED_REVIEW_MODELS = frozenset({
    "hf.co/HauhauCS/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF:IQ3_M",
})
VETO_SEATS = ("risk", "security", "compliance")
SEATS = ("risk", "impact", "drift", "compliance", "security", "consistency")
LLM_ROLES = ("reason", "reason_heavy", "taiwan")

STEPS_RANGE = (1, 8)
NUM_CTX_RANGE = (2048, 65536)
MAX_NAME_LEN = 300
MAX_TEXT_LEN = 20000

WARN_BAD_JSON = "設定檔無法解析,已使用預設值"
WARN_UNREADABLE = "設定檔無法讀取,已使用預設值"
WARN_INVALID = "設定檔內容不合法,已使用預設值"
WARN_UNSAFE_PATH = "設定檔路徑不安全(符號連結或位於 repo 外),已使用預設值"
WARN_DB_UNREADABLE = "設定資料庫無法讀取,已使用預設值"
WARN_DB_VERSION = "設定資料庫版本不明,已使用預設值"
WARN_DB_UPGRADE = "設定資料庫升級失敗(仍為第 1 版),語音預設暫用程式內預設值"

# 語音預設(CHG-20261022-04 T8;鍵與範圍同 project.DEFAULTS、project.normalize_speed / normalize_pause)
VOICE_KEYS = ("speed", "pause_comma", "pause_sentence", "pause_paragraph")
SPEED_RANGE = (0.5, 2.0)
SPEED_STEP = "0.05"
PAUSE_RANGE = (0.0, 5.0)
PAUSE_STEP = "0.1"

# 值取自 taiwan_ai.py 原有常數:MODELS、HEAVY_REASON、生圖模型名稱,以及聲線檔與逐字稿(生圖與聲線
# 的常數於 CHG-20261026-01 T1、T2 移除,預設值只留在這裡;media.generate_image() 與 taiwan_ai.speak()
# 每次呼叫經 taiwan_ai.role_config() 讀取);
# chat() 的 num_ctx 16384;生圖 --steps 4;ask 預設 think=True、潤飾不帶 think(None)。
# review_seats 取自 DIR-1 現行席位表(27B 暫停期間;security、compliance 由 Codex 擔任)。
DEFAULTS: dict = {
    "roles": {
        "reason": {
            "model": "hf.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M",
            "think": True,
            "num_ctx": 16384,
        },
        "reason_heavy": {
            "model": "hf.co/HauhauCS/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF:IQ3_M",
            "think": True,
            "num_ctx": 16384,
        },
        "taiwan": {
            "model": "hf.co/mradermacher/Llama-Breeze2-8B-Instruct-text-only-GGUF:Q4_K_M",
            "think": None,
            "num_ctx": 16384,
        },
        "image": {
            "model": "mflux-community/flux-1-schnell-mflux-q4",
            "steps": 4,
        },
        "voice": {
            "wav": "tools/BreezyVoice/data/example.wav",
            "text": ("在密碼學中，加密是將明文資訊改變為難以讀取的密文內容，使之不可讀的方法。"
                     "只有擁有解密方法的對象，經由解密過程，才能將密文還原為正常可讀的內容。"),
        },
    },
    "review_seats": {
        "risk": "hf.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M",
        "impact": "glm4:latest",
        "drift": "qwen3:8b",
        "compliance": CODEX,
        "security": CODEX,
        "consistency": "glm4:latest",
    },
    # 語速 1.15×(修訂 1 代決);停頓:逗號 0.0、句號 0.4、段落 0.8 秒(代決);同 project.DEFAULTS
    "voice_defaults": {
        "speed": 1.15,
        "pause_comma": 0.0,
        "pause_sentence": 0.4,
        "pause_paragraph": 0.8,
    },
}

# 每個角色允許的鍵
_ROLE_KEYS = {
    "reason": ("model", "think", "num_ctx"),
    "reason_heavy": ("model", "think", "num_ctx"),
    "taiwan": ("model", "think", "num_ctx"),
    "image": ("model", "steps"),
    "voice": ("wav", "text"),
}


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _check_keys(obj, allowed, where, errors) -> bool:
    """檢查 obj 是 dict、沒有未知鍵、沒有缺鍵;回傳是否為 dict。"""
    if not isinstance(obj, dict):
        errors.append(f"{where}:必須是物件")
        return False
    for k in obj:
        if k not in allowed:
            errors.append(f"{where}.{k}:未知的設定鍵")
    for k in allowed:
        if k not in obj:
            errors.append(f"{where}.{k}:缺少此設定")
    return True


def _check_name(v, where, errors) -> bool:
    if not isinstance(v, str) or not v.strip():
        errors.append(f"{where}:必須是非空字串")
        return False
    if len(v) > MAX_NAME_LEN or any(ord(c) < 32 for c in v):
        errors.append(f"{where}:名稱過長或含控制字元")
        return False
    return True


def _voice_path_error(value: str) -> str | None:
    """聲線檔路徑必須位於允許目錄之下;逐層拒絕符號連結。回傳錯誤訊息或 None。"""
    p = Path(value)
    if ".." in p.parts:
        return "路徑不得包含 .."
    root = ROOT.resolve()
    full = p if p.is_absolute() else root / p
    try:
        resolved = full.resolve()
    except (OSError, RuntimeError):
        return "路徑無法解析"
    for d in VOICE_DIRS:
        base = (root / d).resolve()
        try:
            rel = resolved.relative_to(base)
        except ValueError:
            continue
        if not rel.parts:
            return "必須指向允許目錄內的檔案,不能是目錄本身"
        # 逐層檢查:從 repo 根目錄起,允許目錄的每一層與其下每一層都不得是符號連結
        cur = root
        for part in Path(d).parts + rel.parts:
            cur = cur / part
            if cur.is_symlink():
                return f"路徑中的「{cur.relative_to(root)}」是符號連結,不接受"
        # 原始輸入若繞道符號連結(允許目錄之外的連結)才抵達允許目錄,也拒絕
        if Path(os.path.abspath(full)) != resolved:
            return "路徑經符號連結轉向,不接受"
        return None
    return "聲線檔必須位於 " + " 或 ".join(f"{d}/" for d in VOICE_DIRS) + " 之下"


def _voice_round(key: str, v) -> float | None:
    """語速取到 0.05、停頓取到 0.1(四捨五入);不是有限數字(含 bool)或超出範圍回 None。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    step = Decimal(SPEED_STEP if key == "speed" else PAUSE_STEP)
    lo, hi = SPEED_RANGE if key == "speed" else PAUSE_RANGE
    r = (Decimal(repr(float(v))) / step).to_integral_value(rounding=ROUND_HALF_UP) * step
    if not Decimal(repr(lo)) <= r <= Decimal(repr(hi)):
        return None
    return float(r)


def normalize_voice(d: dict) -> dict:
    """回傳四捨五入後的語音預設(只用於已通過 validate 的值)。"""
    return {k: _voice_round(k, d[k]) for k in VOICE_KEYS}


def _check_voice(d, errors) -> None:
    if not _check_keys(d, VOICE_KEYS, "voice_defaults", errors):
        return
    for k in VOICE_KEYS:
        if k in d and _voice_round(k, d[k]) is None:
            lo, hi = SPEED_RANGE if k == "speed" else PAUSE_RANGE
            unit, step = ("倍", SPEED_STEP) if k == "speed" else ("秒", PAUSE_STEP)
            errors.append(f"voice_defaults.{k}:必須是 {lo:g}–{hi:g} {unit}的數字(取到 {step})")


def validate(cfg, available_models) -> list[str]:
    """驗證設定,回傳逐欄的錯誤清單(繁體中文)。available_models 為 None 時不檢查模型是否已下載。

    頂層必須有 roles、review_seats;voice_defaults(T8)可省略——省略時 save() 不改動資料庫裡的語音預設。
    """
    errors: list[str] = []
    avail = None if available_models is None else set(available_models)
    if not isinstance(cfg, dict):
        errors.append("設定:必須是物件")
        return errors
    for k in cfg:
        if k not in ("roles", "review_seats", "voice_defaults"):
            errors.append(f"設定.{k}:未知的設定鍵")
    for k in ("roles", "review_seats"):
        if k not in cfg:
            errors.append(f"設定.{k}:缺少此設定")
    if "voice_defaults" in cfg:
        _check_voice(cfg["voice_defaults"], errors)

    roles = cfg.get("roles")
    if "roles" in cfg and _check_keys(roles, tuple(_ROLE_KEYS), "roles", errors):
        for role, keys in _ROLE_KEYS.items():
            r = roles.get(role)
            where = f"roles.{role}"
            if role not in roles or not _check_keys(r, keys, where, errors):
                continue
            if "model" in r and _check_name(r["model"], f"{where}.model", errors):
                if role in LLM_ROLES and avail is not None and r["model"] not in avail:
                    errors.append(f"{where}.model:模型「{r['model']}」尚未下載到 Ollama")
            if "think" in r and r["think"] is not None and not isinstance(r["think"], bool):
                errors.append(f"{where}.think:必須是 true、false 或 null")
            if "num_ctx" in r:
                lo, hi = NUM_CTX_RANGE
                if not _is_int(r["num_ctx"]) or not lo <= r["num_ctx"] <= hi:
                    errors.append(f"{where}.num_ctx:必須是 {lo}–{hi} 的整數")
            if "steps" in r:
                lo, hi = STEPS_RANGE
                if not _is_int(r["steps"]) or not lo <= r["steps"] <= hi:
                    errors.append(f"{where}.steps:必須是 {lo}–{hi} 的整數")
            if "wav" in r:
                if not isinstance(r["wav"], str) or not r["wav"].strip():
                    errors.append(f"{where}.wav:必須是非空字串")
                else:
                    msg = _voice_path_error(r["wav"])
                    if msg:
                        errors.append(f"{where}.wav:{msg}")
            if "text" in r:
                if not isinstance(r["text"], str) or not r["text"].strip():
                    errors.append(f"{where}.text:必須是非空字串")
                elif len(r["text"]) > MAX_TEXT_LEN:
                    errors.append(f"{where}.text:不得超過 {MAX_TEXT_LEN} 字")

    seats = cfg.get("review_seats")
    if "review_seats" in cfg and _check_keys(seats, SEATS, "review_seats", errors):
        for seat in SEATS:
            if seat not in seats:
                continue
            v = seats[seat]
            where = f"review_seats.{seat}"
            if not _check_name(v, where, errors):
                continue
            if v == CODEX:
                if seat not in VETO_SEATS:
                    errors.append(f"{where}:只有否決席(risk、security、compliance)可由 Codex 擔任")
                continue
            if v in PAUSED_REVIEW_MODELS:
                errors.append(f"{where}:模型「{v}」暫停用於審議席位(DIR-1 第 10 條)")
            if seat in VETO_SEATS and v.startswith("glm4:"):
                errors.append(f"{where}:否決席不得使用 glm4(DIR-1 第 6 條)")
            if avail is not None and v not in avail:
                errors.append(f"{where}:模型「{v}」尚未下載到 Ollama")
    return errors


# ---- 儲存層(T3 修訂 2:SQLite) ----

_NEW = "new"      # 沒有任何資料表的資料庫(剛由本模組建立,或空檔)
# 版本已知時 _schema_state() 回傳版本字串本身(SCHEMA_V1 或 SCHEMA_VERSION)

_VOICE_TABLE = "CREATE TABLE IF NOT EXISTS voice_defaults (key TEXT PRIMARY KEY, value REAL NOT NULL)"
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS roles (role TEXT PRIMARY KEY, config TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS review_seats (seat TEXT PRIMARY KEY, model TEXT NOT NULL)",
    _VOICE_TABLE,
)


class _DbError(Exception):
    """資料庫不能使用;args[0] 為對應的警告文字(WARN_DB_UNREADABLE 等)。"""


def _db_file() -> Path:
    return ROOT / DB_PATH


def _sidecars(db: Path) -> tuple[Path, Path]:
    return db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")


def _lock_file(db: Path) -> Path:
    """初始化鎖檔 `config/.studio.db.lock`(依 PR #37 審議新增;只用來上鎖,不讀寫內容、不刪除)。"""
    return db.with_name("." + db.name + ".lock")


def _fallback(warning: str) -> dict:
    print("[settings] " + warning + "(" + str(_db_file()) + ")", file=sys.stderr)
    cfg = copy.deepcopy(DEFAULTS)
    cfg["_warnings"] = [warning]
    return cfg


def _config_path_error() -> str | None:
    """一次性匯入來源必須是 repo 內的 config/models.json,且 config/ 與 models.json 逐層都不是符號連結。

    不存在的層不算錯。回傳錯誤訊息或 None。
    """
    root = ROOT.resolve()
    expected = root / "config" / "models.json"
    for layer in (root / "config", expected):
        if layer.is_symlink():
            return f"「{layer.relative_to(root)}」是符號連結,不接受"
    try:
        if CONFIG_PATH.resolve() != expected:
            return f"設定檔路徑必須是 {expected}"
    except (OSError, RuntimeError):
        return "設定檔路徑無法解析"
    return None


def _db_path_error() -> str | None:
    """資料庫必須是 repo 內的 config/studio.db;config/、studio.db、-wal、-shm 與初始化鎖檔
    `.studio.db.lock` 逐層都不得是符號連結。

    不存在的層不算錯(save 會在檢查後於 repo 內建立 config/)。回傳錯誤訊息或 None。
    """
    root = ROOT.resolve()
    expected = root / "config" / "studio.db"
    db = _db_file()
    layers = (root / "config", expected, *_sidecars(expected), _lock_file(expected),
              db.parent, db, *_sidecars(db), _lock_file(db))
    for layer in layers:
        if layer.is_symlink():
            try:
                shown = layer.relative_to(root)
            except ValueError:
                shown = layer
            return f"「{shown}」是符號連結,不接受"
    try:
        if db.resolve() != expected:
            return f"設定資料庫路徑必須是 {expected}"
    except (OSError, RuntimeError):
        return "設定資料庫路徑無法解析"
    return None


def _schema_state(con: sqlite3.Connection) -> str:
    """回傳 _NEW 或版本字串(SCHEMA_V1 / SCHEMA_VERSION);有表但沒有 meta、或 schema_version 不明時
    丟 _DbError(WARN_DB_VERSION)。"""
    if con.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0:
        return _NEW
    has_meta = con.execute(
        "SELECT count(*) FROM sqlite_master WHERE type = ? AND name = ?", ("table", "meta")
    ).fetchone()[0]
    if not has_meta:
        raise _DbError(WARN_DB_VERSION)
    row = con.execute("SELECT value FROM meta WHERE key = ?", ("schema_version",)).fetchone()
    if row is None or row[0] not in KNOWN_SCHEMA_VERSIONS:
        raise _DbError(WARN_DB_VERSION)
    return row[0]


def _probe(db: Path) -> str:
    """以唯讀 URI 開啟既有資料庫,只讀 schema_version;此階段不下任何 PRAGMA、不寫入。

    回傳 _NEW 或版本字串;非 SQLite、損壞或版本不明時丟 _DbError。
    """
    last = None
    for attempt in range(3):
        try:
            con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True,
                                  timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        except sqlite3.Error as e:
            last = e
        else:
            try:
                return _schema_state(con)
            except sqlite3.OperationalError as e:  # 例如另一行程正在切換 WAL,稍後重試
                last = e
            except sqlite3.Error:
                raise _DbError(WARN_DB_UNREADABLE) from None
            finally:
                con.close()
        time.sleep(0.05 * (attempt + 1))
    raise _DbError(WARN_DB_UNREADABLE) from last


def _open_rw(db: Path) -> sqlite3.Connection:
    """只用於版本已知或新建的資料庫:讀寫模式(不會建立新檔)、busy_timeout 5000、WAL。

    兩個行程同時把新資料庫切成 WAL 時,SQLite 可能不經 busy handler 直接回
    「database is locked」;此時關閉連線稍後重試,總等待以 busy_timeout 為上限。
    """
    deadline = time.monotonic() + BUSY_TIMEOUT_MS / 1000
    delay = 0.01
    while True:
        con = sqlite3.connect(db.as_uri() + "?mode=rw", uri=True,
                              timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        try:
            con.execute("PRAGMA busy_timeout = 5000")
            mode = con.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(mode).lower() != "wal":
                raise _DbError(WARN_DB_UNREADABLE)
            return con
        except sqlite3.OperationalError:
            con.close()
            if time.monotonic() + delay > deadline:
                raise
        except BaseException:
            con.close()
            raise
        time.sleep(delay)
        delay = min(delay * 2, 0.2)


def _create_db_file(db: Path) -> tuple[int, int] | None:
    """以排他方式建立空的資料庫檔(權限 0600,不跟隨符號連結)。

    回傳新檔的身分 `(st_dev, st_ino)`,供 `_discard()` 確認要刪的仍是這一個檔;已存在回 None。
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(db, flags, 0o600)
    except FileExistsError:
        return None
    try:
        os.fchmod(fd, 0o600)
        st = os.fstat(fd)
    finally:
        os.close(fd)
    return st.st_dev, st.st_ino


def _same_file(db: Path, ident: tuple[int, int]) -> bool:
    """db 目前是一般檔案(不是符號連結),且身分等於 ident。"""
    try:
        st = os.lstat(db)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and (st.st_dev, st.st_ino) == ident


def _discard(db: Path, ident: tuple[int, int]) -> None:
    """移除本次呼叫建立、但交易失敗的資料庫檔與其 -wal、-shm(下次 load() 再試)。

    一律在 `_init_lock()` 內呼叫(依 PR #37 審議修正):建立資料庫檔、第一次匯入或存入、
    失敗時清理,全部在同一把跨行程初始化鎖內進行,其他行程不可能在清理期間建立新的
    資料庫檔或對這個空檔匯入,刪除 -wal、-shm 因此不會刪到別人的檔。

    另外仍核對:路徑上的檔案是本次建立的那一個(st_dev、st_ino 相同),且在
    `BEGIN IMMEDIATE` 內確認仍沒有任何資料表(沒有 meta、沒有匯入的資料)才刪;
    無法確認時一律保留(依 PR #36 審議修正)。
    """
    if not _same_file(db, ident):
        return
    removed = False
    try:
        con = _open_rw(db)
    except (_DbError, sqlite3.Error):
        return
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            if _same_file(db, ident) and _schema_state(con) == _NEW:
                db.unlink()
                removed = True
        finally:
            if con.in_transaction:
                con.execute("ROLLBACK")
    except (_DbError, sqlite3.Error, OSError):
        pass
    finally:
        con.close()
    if not removed:
        return
    for p in _sidecars(db):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


@contextlib.contextmanager
def _init_lock():
    """跨行程初始化鎖(`fcntl.flock(LOCK_EX)` 鎖 `config/.studio.db.lock`)。

    「資料庫不存在或還沒有任何表」時的建立、第一次匯入或存入、失敗時清理,一律在這把鎖內;
    既有資料庫(版本已知)的一般讀寫不取這把鎖,照舊靠 WAL 與 busy_timeout。
    開鎖檔前做與資料庫相同的路徑檢查(不經符號連結、必須在 repo 內),並以 O_NOFOLLOW 開啟;
    取不到鎖檔時丟 _DbError(只轉換取鎖階段的錯誤,with 區塊內的例外原樣往外丟)。
    """
    msg = _db_path_error()
    if msg:
        raise _DbError(WARN_UNSAFE_PATH)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(_lock_file(_db_file()), flags, 0o600)
    except OSError:
        raise _DbError(WARN_UNSAFE_PATH if _db_path_error() else WARN_DB_UNREADABLE) from None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError:
            raise _DbError(WARN_DB_UNREADABLE) from None
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _role_rows(cfg: dict) -> list[tuple[str, str]]:
    return [(role, json.dumps(conf, ensure_ascii=False, sort_keys=True))
            for role, conf in cfg["roles"].items()]


def _seat_rows(cfg: dict) -> list[tuple[str, str]]:
    return [(seat, model) for seat, model in cfg["review_seats"].items()]


def _voice_rows(voice: dict) -> list[tuple[str, float]]:
    return [(k, v) for k, v in normalize_voice(voice).items()]


def _write_voice(con: sqlite3.Connection, voice: dict) -> None:
    con.execute("DELETE FROM voice_defaults")
    con.executemany("INSERT INTO voice_defaults (key, value) VALUES (?, ?)", _voice_rows(voice))


def _write_config(con: sqlite3.Connection, cfg: dict) -> None:
    """整批改寫 roles、review_seats;cfg 帶 voice_defaults 時一併改寫該表。只在 BEGIN IMMEDIATE 交易內呼叫。"""
    con.execute("DELETE FROM roles")
    con.executemany("INSERT INTO roles (role, config) VALUES (?, ?)", _role_rows(cfg))
    con.execute("DELETE FROM review_seats")
    con.executemany("INSERT INTO review_seats (seat, model) VALUES (?, ?)", _seat_rows(cfg))
    if "voice_defaults" in cfg:
        _write_voice(con, cfg["voice_defaults"])


def _upgrade_v1_v2(con: sqlite3.Connection) -> None:
    """schema 1 → 2:只新增 voice_defaults 表、寫入預設值、把 meta.schema_version 改為 2。

    不動 roles、review_seats 的任何列。只在 BEGIN IMMEDIATE 交易內、確認版本仍為 1 之後呼叫;
    任何例外由 _immediate() rollback(SQLite 的 CREATE TABLE 也在交易內),資料庫維持 v1。
    """
    con.execute(_VOICE_TABLE)
    _write_voice(con, DEFAULTS["voice_defaults"])
    con.execute("UPDATE meta SET value = ? WHERE key = ?", (SCHEMA_VERSION, "schema_version"))


def _upgrade_body(con: sqlite3.Connection) -> None:
    """升級交易本體:在交易內重新讀版本,另一行程已升級(版本 2)就略過,只升一次。"""
    if _schema_state(con) == SCHEMA_V1:
        _upgrade_v1_v2(con)


def _create_schema(con: sqlite3.Connection) -> None:
    for sql in _SCHEMA:
        con.execute(sql)


def _immediate(con: sqlite3.Connection, body) -> None:
    """以 BEGIN IMMEDIATE 執行 body(con);任何例外(含 COMMIT 失敗)一律 ROLLBACK 後再丟出。"""
    con.execute("BEGIN IMMEDIATE")
    try:
        body(con)
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise


def _import_body(cfg: dict):
    def body(con: sqlite3.Connection) -> None:
        if _schema_state(con) != _NEW:
            return  # 另一行程已匯入,略過
        _create_schema(con)
        _write_config(con, cfg)
        if "voice_defaults" not in cfg:   # T8 以前的 models.json 沒有語音預設
            _write_voice(con, DEFAULTS["voice_defaults"])
        now =datetime.now(timezone.utc).isoformat(timespec="seconds")
        con.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", [
            ("schema_version", SCHEMA_VERSION),
            ("imported_from", IMPORTED_FROM),
            ("imported_at", now),
        ])
    return body


def _save_body(cfg: dict):
    def body(con: sqlite3.Connection) -> None:
        state = _schema_state(con)
        if state == _NEW:
            _create_schema(con)
            con.execute("INSERT INTO meta (key, value) VALUES (?, ?)",
                        ("schema_version", SCHEMA_VERSION))
            if "voice_defaults" not in cfg:
                _write_voice(con, DEFAULTS["voice_defaults"])
        elif state == SCHEMA_V1:
            _upgrade_v1_v2(con)   # 升級與存入在同一個交易內
        _write_config(con, cfg)
    return body


def _read(con: sqlite3.Connection) -> dict:
    """在同一個讀取交易內讀出 meta、roles、review_seats、voice_defaults(同一份快照)。

    第 1 版(只會在升級失敗時讀到)沒有 voice_defaults 表,語音預設以程式內預設值補上。
    """
    con.execute("BEGIN")
    try:
        version = _schema_state(con)
        if version not in KNOWN_SCHEMA_VERSIONS:
            raise _DbError(WARN_DB_VERSION)
        roles = {}
        for role, raw in con.execute("SELECT role, config FROM roles"):
            try:
                roles[role] = json.loads(raw)
            except (TypeError, ValueError):
                raise _DbError(WARN_INVALID) from None
        seats = {seat: model for seat, model in con.execute("SELECT seat, model FROM review_seats")}
        if version == SCHEMA_V1:
            voice = copy.deepcopy(DEFAULTS["voice_defaults"])
        else:
            voice = {k: v for k, v in con.execute("SELECT key, value FROM voice_defaults")}
    finally:
        if con.in_transaction:
            con.execute("ROLLBACK")
    return {"roles": roles, "review_seats": seats, "voice_defaults": voice}


def _load_known(db: Path, extra_warning: str | None = None) -> dict:
    """讀版本已知的資料庫;extra_warning(升級失敗)會放進回傳的 `_warnings`(同時寫 stderr)。"""
    try:
        con = _open_rw(db)
        try:
            cfg = _read(con)
        finally:
            con.close()
    except _DbError as e:
        return _fallback(e.args[0])
    except sqlite3.Error:
        return _fallback(WARN_DB_UNREADABLE)
    if validate(cfg, None):
        return _fallback(WARN_INVALID)
    cfg["voice_defaults"] = normalize_voice(cfg["voice_defaults"])
    if extra_warning:
        print("[settings] " + extra_warning + "(" + str(_db_file()) + ")", file=sys.stderr)
        cfg["_warnings"] = [extra_warning]
    return cfg


def _load_v1(db: Path) -> dict:
    """第 1 版資料庫:在初始化鎖內重新唯讀探測,仍為第 1 版才以 BEGIN IMMEDIATE 單一交易升級。

    鎖內重新探測、交易內再讀一次版本,網頁與 MCP 同時第一次 load() 時只升一次。
    取不到鎖或路徑不安全 → 預設值 + `_warnings`(同其他路徑);期間版本變成不明 → 同版本不明;
    升級交易失敗 → rollback(資料庫維持 v1),回傳 v1 的 roles、review_seats + 程式內語音預設 + `_warnings`。
    """
    try:
        with _init_lock():
            if _probe(db) == SCHEMA_V1:
                con = _open_rw(db)
                try:
                    _immediate(con, _upgrade_body)
                finally:
                    con.close()
    except _DbError as e:
        if e.args[0] in (WARN_UNSAFE_PATH, WARN_DB_VERSION):
            return _fallback(e.args[0])
        return _load_known(db, WARN_DB_UPGRADE)
    except Exception:
        return _load_known(db, WARN_DB_UPGRADE)
    return _load_known(db)


def _load_existing(db: Path, state: str) -> dict:
    if state == SCHEMA_V1:
        return _load_v1(db)
    return _load_known(db)


def _import_source() -> tuple[dict | None, str | None]:
    """讀一次性匯入來源 config/models.json;回傳 (合法設定或 None, 警告或 None)。"""
    if _config_path_error():
        return None, WARN_UNSAFE_PATH
    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeDecodeError):
        return None, WARN_UNREADABLE
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError:
        return None, WARN_BAD_JSON
    if validate(cfg, None):
        return None, WARN_INVALID
    return cfg, None


def _create_or_import(db: Path, body, imported_ok: bool):
    """只在 _init_lock() 內呼叫:重新判斷資料庫狀態,需要時建立檔案並以 body 在單一交易內寫入。

    回傳 True 代表已寫入;imported_ok 為 True 且另一行程已完成匯入(版本已知)時回 False,
    不再寫入。非 SQLite、損壞或版本不明丟 _DbError。寫入失敗時清掉本次建立的檔再往外丟。
    """
    created = None
    if db.exists():
        if _probe(db) != _NEW and imported_ok:
            return False
    else:
        created = _create_db_file(db)
        if created is None:  # 鎖內不應發生(建立一律在鎖內),保守視為無法使用
            raise _DbError(WARN_DB_UNREADABLE)
    try:
        con = _open_rw(db)
        try:
            _immediate(con, body)
        finally:
            con.close()
    except BaseException:
        if created is not None:
            _discard(db, created)
        raise
    return True


def load() -> dict:
    """讀設定資料庫;資料庫不存在時視 config/models.json 決定回預設值或匯入一次。"""
    if _db_path_error():
        return _fallback(WARN_UNSAFE_PATH)
    db = _db_file()
    if db.exists():
        try:
            state = _probe(db)
        except _DbError as e:
            return _fallback(e.args[0])
        if state != _NEW:
            return _load_existing(db, state)
    # 資料庫不存在,或存在但沒有任何表(另一行程正在建立或曾中斷)
    src, warning = _import_source()
    if warning:
        return _fallback(warning)
    if src is None:
        return copy.deepcopy(DEFAULTS)  # 不建立資料庫
    try:
        with _init_lock():
            _create_or_import(db, _import_body(src), imported_ok=True)
    except _DbError as e:
        return _fallback(e.args[0])
    except Exception:
        return _fallback(WARN_DB_UNREADABLE)
    try:
        state = _probe(db)
    except _DbError as e:
        return _fallback(e.args[0])
    return _load_existing(db, state)


META_KEYS = ("imported_from", "imported_at")  # load_meta() 對外提供的 meta 鍵(不含 schema_version)


def load_meta() -> dict:
    """唯讀取得設定資料庫 meta 中的匯入資訊(T9 設定 API 的「已匯入一次」提示用)。

    回傳 {"imported_from": …, "imported_at": …} 中有值的鍵;路徑不安全、資料庫不存在、
    不是 SQLite、版本不明或讀取失敗時回空 dict。一律以唯讀 URI 開啟、不下任何 PRAGMA、
    不建立資料庫、不匯入、不寫入。
    """
    if _db_path_error():
        return {}
    db = _db_file()
    if not db.exists():
        return {}
    try:
        if _probe(db) == _NEW:
            return {}
        con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True,
                              timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        try:
            rows = con.execute("SELECT key, value FROM meta WHERE key IN (?, ?)", META_KEYS).fetchall()
        finally:
            con.close()
    except (_DbError, sqlite3.Error):
        return {}
    return {k: v for k, v in rows if k in META_KEYS and isinstance(v, str)
            and 0 < len(v) <= MAX_NAME_LEN}


VOICE_EXTS = (".wav",)  # 可選聲線檔的副檔名(BreezyVoice 的 speaker prompt 範例為 wav)
MAX_VOICE_OPTIONS = 500


def voice_options(limit: int = MAX_VOICE_OPTIONS) -> list[str]:
    """列出允許目錄(VOICE_DIRS)下可選的聲線檔,回傳相對於 repo 根目錄的 POSIX 路徑。

    不跟隨符號連結(符號連結的檔案與目錄一律略過)、略過點開頭的檔案與目錄;
    每一筆都再經與 validate 相同的路徑規則確認,因此列出的值都能通過 validate。
    最多回傳 limit 筆。
    """
    root = ROOT.resolve()
    out: list[str] = []
    for d in VOICE_DIRS:
        base = root / d
        if _voice_dir_unsafe(root, d) or not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            here = Path(dirpath)
            dirnames[:] = sorted(n for n in dirnames
                                 if not n.startswith(".") and not (here / n).is_symlink())
            for fn in sorted(filenames):
                if fn.startswith(".") or Path(fn).suffix.lower() not in VOICE_EXTS:
                    continue
                p = here / fn
                if p.is_symlink() or not p.is_file():
                    continue
                rel = p.relative_to(root).as_posix()
                if _voice_path_error(rel) is None:
                    out.append(rel)
                    if len(out) >= limit:
                        return out
    return out


def _voice_dir_unsafe(root: Path, d: str) -> bool:
    """允許目錄本身(含上層)任一層是符號連結時回 True。"""
    cur = root
    for part in Path(d).parts:
        cur = cur / part
        if cur.is_symlink():
            return True
    return False


def save(cfg) -> None:
    """結構檢查後以單一交易整批改寫設定資料庫;中途失敗 rollback,資料庫維持原狀。"""
    if isinstance(cfg, dict):
        cfg = {k: v for k, v in cfg.items() if k != "_warnings"}
    errors = validate(cfg, None)
    if errors:
        raise ValueError(errors)
    # 建立 config/ 前後都檢查:不經符號連結、不越出 repo
    msg = _db_path_error()
    if msg:
        raise ValueError([msg])
    db = _db_file()
    db.parent.mkdir(exist_ok=True)
    msg = _db_path_error()
    if msg:
        raise ValueError([msg])
    try:
        if db.exists() and _probe(db) == SCHEMA_VERSION:
            # 目前版本的既有資料庫:一般寫入,不取初始化鎖
            con = _open_rw(db)
            try:
                _immediate(con, _save_body(cfg))
            finally:
                con.close()
            return
        # 資料庫不存在、還沒有任何表或仍為第 1 版:建立、升級與存入在初始化鎖內同一個交易進行
        with _init_lock():
            _create_or_import(db, _save_body(cfg), imported_ok=False)
    except _DbError as e:  # 非 SQLite、損壞或版本不明 → 不寫入、不覆寫、不重建
        raise ValueError([e.args[0]]) from None
