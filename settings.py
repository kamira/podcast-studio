"""模型設定(CHG-20261022-01 T1):網頁與 MCP 共用的 `config/models.json` 讀寫與驗證。

介面:
  DEFAULTS                       程式內預設值(取自 taiwan_ai.py 現有常數與 DIR-1 席位表)
  load() -> dict                 讀設定;檔案不存在 → DEFAULTS 的副本;
                                 壞 JSON / 讀取失敗 / 結構不合法 → DEFAULTS 的副本,
                                 並在回傳的 dict 加上 `_warnings: list[str]`(同時寫 stderr)。
                                 正常讀取時回傳中**沒有** `_warnings` 鍵。
  save(cfg) -> None              先做結構檢查(不含模型是否已下載),不合格丟 ValueError
                                 (args[0] 為錯誤清單);合格才以「同目錄暫存檔 + os.replace」
                                 原子寫入。cfg 若帶 `_warnings` 會先移除再檢查。
  validate(cfg, available_models) -> list[str]
                                 回傳逐欄的繁體中文錯誤訊息;空清單代表合格。
                                 available_models 為 Ollama 已下載的模型名稱集合;
                                 傳 None 時只做結構檢查(save 使用)。

安全影響 (c) 跨模組信任:模型名稱必須在 Ollama 清單內;聲線檔限
`tools/BreezyVoice/data/` 與 `outputs/uploads/` 之下;否決席不得為 glm4(DIR-1 第 6 條)。
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "models.json"

# 聲線檔允許的目錄(相對 repo 根目錄)
VOICE_DIRS = ("tools/BreezyVoice/data", "outputs/uploads")

CODEX = "codex"  # 審議席位由 Codex(codex-cli)擔任的代號,見 DIR-1 第 9、10 條
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
            if seat in VETO_SEATS and v.startswith("glm4:"):
                errors.append(f"{where}:否決席不得使用 glm4(DIR-1 第 6 條)")
            if avail is not None and v not in avail:
                errors.append(f"{where}:模型「{v}」尚未下載到 Ollama")
    return errors


def _fallback(warning: str) -> dict:
    print(f"[settings] {warning}({CONFIG_PATH})", file=sys.stderr)
    cfg = copy.deepcopy(DEFAULTS)
    cfg["_warnings"] = [warning]
    return cfg


def load() -> dict:
    """讀取 config/models.json;不存在回預設值,壞掉回預設值並帶 `_warnings`。"""
    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return copy.deepcopy(DEFAULTS)
    except (OSError, UnicodeDecodeError):
        return _fallback(WARN_UNREADABLE)
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError:
        return _fallback(WARN_BAD_JSON)
    if validate(cfg, None):
        return _fallback(WARN_INVALID)
    return cfg


def save(cfg) -> None:
    """結構檢查後原子寫入 config/models.json;中途失敗不留下半個檔。"""
    if isinstance(cfg, dict):
        cfg = {k: v for k, v in cfg.items() if k != "_warnings"}
    errors = validate(cfg, None)
    if errors:
        raise ValueError(errors)
    data = json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=CONFIG_PATH.parent, prefix=".models.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CONFIG_PATH)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
