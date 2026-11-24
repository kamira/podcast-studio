# -*- coding: utf-8 -*-
"""使用者命名規則(工作流程名稱與專案名稱共用;CHG-20261022-04 T2 由 `studio_server.py` 移出)。

規則來源:PR #44 的工作流程名稱規則(使用者 2026-10-26 指示開放中文,同日追加「只採最低限度
中英日即可」與「採用unicode編碼能解決大部分的問題」)。專案名稱沿用同一套白名單,單一來源在本檔。

  - 名稱視為 Unicode 字串,不做正規化或其他編碼轉換;長度以字數計,1~64。
  - 逐字比對明確的中英日白名單:ASCII 英數、`-`、`_`、CJK 統一漢字(U+4E00–9FFF)與擴充 A
    (U+3400–4DBF)、平假名(U+3041–3096)、片假名(U+30A1–30FA)、「ー」、「々」。
    不用 `\\w` 也不用 Unicode 類別;其餘一律不合格,包含帶附加符號的拉丁字母(`café`、`ß`)、
    韓文、全形英數、羅馬數字、「・」、空白、`.`、`/`、`\\`、控制字元、單獨的組合記號、零寬字元、
    全形斜線、方向覆寫字元等(NFD 形式的濁點組合記號 U+3099 也不在白名單內)。
  - 白名單字元以 UTF-8 計最多 3 位元組,64 字最多 192 位元組,加副檔名或時間戳仍在檔名上限 255 內。
  - 只差大小寫的比對鍵為 `fold_name()`;白名單內只有 ASCII 字母有大小寫之分。
"""
from __future__ import annotations

MAX_NAME = 64
NAME_ASCII = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
NAME_RANGES = (
    (0x4E00, 0x9FFF),   # CJK 統一漢字
    (0x3400, 0x4DBF),   # CJK 統一漢字擴充 A
    (0x3041, 0x3096),   # 平假名
    (0x30A1, 0x30FA),   # 片假名(不含中點「・」U+30FB)
    (0x30FC, 0x30FC),   # 長音符「ー」
    (0x3005, 0x3005),   # 疊字符「々」
)


def name_char_ok(ch: str) -> bool:
    """單一字元是否在中英日白名單內。"""
    if ch in NAME_ASCII:
        return True
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in NAME_RANGES)


def normalize_name(name) -> str | None:
    """名稱檢查;合格回傳原名稱(直接當檔名或目錄名),不合格回 None。"""
    if not isinstance(name, str) or not 1 <= len(name) <= MAX_NAME:
        return None
    if not all(name_char_ok(ch) for ch in name):
        return None
    return name


def fold_name(name: str) -> str:
    """比對「只差大小寫」用的鍵。"""
    return name.casefold()
