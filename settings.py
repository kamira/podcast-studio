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
                                 (任何例外 → ROLLBACK)。路徑不安全、資料庫不是 SQLite、版本不明
                                 時丟 ValueError,不寫入、不覆寫、不重建。
  validate(cfg, available_models) -> list[str]
                                 回傳逐欄的繁體中文錯誤訊息;空清單代表合格。
                                 available_models 為 Ollama 已下載的模型名稱集合;
                                 傳 None 時只做結構檢查(save 與讀出資料庫時使用)。

資料庫(schema v1):
  meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)        schema_version、imported_from、imported_at
  roles(role TEXT PRIMARY KEY, config TEXT NOT NULL)      config 為該角色鍵值的 JSON
  review_seats(seat TEXT PRIMARY KEY, model TEXT NOT NULL)

開啟規則:既有資料庫一律先以唯讀 URI(`mode=ro`)讀 `meta.schema_version`,此階段不下任何
PRAGMA;只有版本已知、或新建(沒有任何表)的資料庫,才以讀寫模式開啟並設定
`PRAGMA busy_timeout=5000`、`PRAGMA journal_mode=WAL`。資料庫檔一律由本模組以排他方式
建立(權限 0600),SQLite 本身不會建立資料庫檔(`mode=rw`)。SQL 一律參數化,表名與欄名
寫死在本檔。

安全影響 (c) 跨模組信任:模型名稱必須在 Ollama 清單內;聲線檔限
`tools/BreezyVoice/data/` 與 `outputs/uploads/` 之下;否決席不得為 glm4(DIR-1 第 6 條)。
安全影響 (e):資料庫讀出的設定一律再經 `validate(cfg, None)`,不合法整份回預設值。
"""
from __future__ import annotations

import copy
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "models.json"  # 只作一次性匯入來源(T3 起移出 git)
DB_PATH = Path("config") / "studio.db"  # 相對於 ROOT

SCHEMA_VERSION = "1"
KNOWN_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})
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

# 值取自 taiwan_ai.py:MODELS、HEAVY_REASON、FLUX、VOICE_WAV、VOICE_TEXT;
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


def validate(cfg, available_models) -> list[str]:
    """驗證設定,回傳逐欄的錯誤清單(繁體中文)。available_models 為 None 時不檢查模型是否已下載。"""
    errors: list[str] = []
    avail = None if available_models is None else set(available_models)
    if not _check_keys(cfg, ("roles", "review_seats"), "設定", errors):
        return errors

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
_KNOWN = "known"  # meta.schema_version 為已知版本

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS roles (role TEXT PRIMARY KEY, config TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS review_seats (seat TEXT PRIMARY KEY, model TEXT NOT NULL)",
)


class _DbError(Exception):
    """資料庫不能使用;args[0] 為對應的警告文字(WARN_DB_UNREADABLE 等)。"""


def _db_file() -> Path:
    return ROOT / DB_PATH


def _sidecars(db: Path) -> tuple[Path, Path]:
    return db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")


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
    """資料庫必須是 repo 內的 config/studio.db;config/、studio.db、-wal、-shm 逐層都不得是符號連結。

    不存在的層不算錯(save 會在檢查後於 repo 內建立 config/)。回傳錯誤訊息或 None。
    """
    root = ROOT.resolve()
    expected = root / "config" / "studio.db"
    db = _db_file()
    layers = (root / "config", expected, *_sidecars(expected), db.parent, db, *_sidecars(db))
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
    """回傳 _NEW 或 _KNOWN;有表但沒有 meta、或 schema_version 不明時丟 _DbError(WARN_DB_VERSION)。"""
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
    return _KNOWN


def _probe(db: Path) -> str:
    """以唯讀 URI 開啟既有資料庫,只讀 schema_version;此階段不下任何 PRAGMA、不寫入。

    回傳 _NEW 或 _KNOWN;非 SQLite、損壞或版本不明時丟 _DbError。
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


def _create_db_file(db: Path) -> bool:
    """以排他方式建立空的資料庫檔(權限 0600,不跟隨符號連結);已存在回 False。"""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(db, flags, 0o600)
    except FileExistsError:
        return False
    try:
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    return True


def _discard(db: Path) -> None:
    """移除本次呼叫建立、但交易失敗的資料庫檔與其 -wal、-shm(下次 load() 再試)。"""
    for p in (db, *_sidecars(db)):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def _role_rows(cfg: dict) -> list[tuple[str, str]]:
    return [(role, json.dumps(conf, ensure_ascii=False, sort_keys=True))
            for role, conf in cfg["roles"].items()]


def _seat_rows(cfg: dict) -> list[tuple[str, str]]:
    return [(seat, model) for seat, model in cfg["review_seats"].items()]


def _write_config(con: sqlite3.Connection, cfg: dict) -> None:
    """整批改寫兩表;只在 BEGIN IMMEDIATE 交易內呼叫。"""
    con.execute("DELETE FROM roles")
    con.executemany("INSERT INTO roles (role, config) VALUES (?, ?)", _role_rows(cfg))
    con.execute("DELETE FROM review_seats")
    con.executemany("INSERT INTO review_seats (seat, model) VALUES (?, ?)", _seat_rows(cfg))


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
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        con.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", [
            ("schema_version", SCHEMA_VERSION),
            ("imported_from", IMPORTED_FROM),
            ("imported_at", now),
        ])
    return body


def _save_body(cfg: dict):
    def body(con: sqlite3.Connection) -> None:
        if _schema_state(con) == _NEW:
            _create_schema(con)
            con.execute("INSERT INTO meta (key, value) VALUES (?, ?)",
                        ("schema_version", SCHEMA_VERSION))
        _write_config(con, cfg)
    return body


def _read(con: sqlite3.Connection) -> dict:
    """在同一個讀取交易內讀出 meta、roles、review_seats(同一份快照)。"""
    con.execute("BEGIN")
    try:
        if _schema_state(con) != _KNOWN:
            raise _DbError(WARN_DB_VERSION)
        roles = {}
        for role, raw in con.execute("SELECT role, config FROM roles"):
            try:
                roles[role] = json.loads(raw)
            except (TypeError, ValueError):
                raise _DbError(WARN_INVALID) from None
        seats = {seat: model for seat, model in con.execute("SELECT seat, model FROM review_seats")}
    finally:
        if con.in_transaction:
            con.execute("ROLLBACK")
    return {"roles": roles, "review_seats": seats}


def _load_known(db: Path) -> dict:
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
    return cfg


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


def load() -> dict:
    """讀設定資料庫;資料庫不存在時視 config/models.json 決定回預設值或匯入一次。"""
    if _db_path_error():
        return _fallback(WARN_UNSAFE_PATH)
    db = _db_file()
    for _ in range(2):
        if db.exists():
            try:
                state = _probe(db)
            except _DbError as e:
                return _fallback(e.args[0])
            if state == _KNOWN:
                return _load_known(db)
        # 資料庫不存在,或存在但沒有任何表(另一行程剛建立、尚未匯入)
        src, warning = _import_source()
        if warning:
            return _fallback(warning)
        if src is None:
            return copy.deepcopy(DEFAULTS)  # 不建立資料庫
        created = False
        if not db.exists():
            created = _create_db_file(db)
            if not created:
                continue  # 另一行程剛建立,重新判斷
        try:
            con = _open_rw(db)
            try:
                _immediate(con, _import_body(src))
            finally:
                con.close()
        except _DbError as e:
            if created:
                _discard(db)
            return _fallback(e.args[0])
        except Exception:
            if created:
                _discard(db)
            return _fallback(WARN_DB_UNREADABLE)
        return _load_known(db)
    return _fallback(WARN_DB_UNREADABLE)


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
    for _ in range(2):
        created = False
        if db.exists():
            try:
                _probe(db)  # 非 SQLite、損壞或版本不明 → 不寫入、不覆寫、不重建
            except _DbError as e:
                raise ValueError([e.args[0]]) from None
        else:
            created = _create_db_file(db)
            if not created:
                continue  # 另一行程剛建立,重新判斷
        try:
            con = _open_rw(db)
            try:
                _immediate(con, _save_body(cfg))
            finally:
                con.close()
        except _DbError as e:
            if created:
                _discard(db)
            raise ValueError([e.args[0]]) from None
        except BaseException:
            if created:
                _discard(db)
            raise
        return
    raise ValueError([WARN_DB_UNREADABLE])
