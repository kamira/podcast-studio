# -*- coding: utf-8 -*-
"""合音規則(CHG-20261029-01 T7,FR-39;D24、D25)。

相鄰兩字 A、B 合成一個音節(例:這樣 → ㄐㄧㄤˋ「醬」、知道 → 詞庫 ㄗㄠˋ「造」)。純函式、只用標準庫;
注音一律為聲調符號格式(同 `breezy_worker`:一聲不標、輕聲以 ˙ 開頭或結尾)。

介面:
  split_zhuyin(z) -> (聲母, 介音, 韻母, 聲調)   聲調為 ""(一聲)、ˊ、ˇ、ˋ、˙;不合法丟 ValueError
  join_zhuyin(聲母, 介音, 韻母, 聲調) -> str      輕聲寫在前(˙ㄇㄣ,同 G2PW 輸出)
  fuse(a, b) -> (zhuyin | None, reason)        依 D24 規則與 D25 合法化表;不合音時 zhuyin 為 None、reason 為原因
  explain(a, b) -> {"zhuyin", "reason", "raw"}  另附合法化前的音節(彈出框顯示推導)
  suggest(text, zhuyins, lexicon) -> [{"index", "zhuyin", "source": "lexicon"}]
                                               句中出現在詞庫(啟用者)的相鄰兩字(由左到右、不重疊);
                                               zhuyins(逐字注音,可為 None)中任一字沒有注音的位置略過
  is_syllable(z) -> bool                        單一合法音節(聲母與介音組合依合法化表檢查;聲調組合不檢查)

規則(D24):聲母取 A(A 零聲母則無);介音取 B,A 零聲母且 B 無介音時取 A;韻母取 B;聲調取 B,B 為輕聲時取 A。
合法化(D25,由上而下):1 B 沒有韻母也沒有介音 → 跳過;2 B 的韻母為 ㄦ → 跳過;6 ㄖ 接 ㄧㄩ → 跳過;
  3~5 ㄓㄗㄍ / ㄔㄘㄎ / ㄕㄙㄏ 接 ㄧㄩ → 顎化為 ㄐ / ㄑ / ㄒ;7 ㄐㄑㄒ 不接 ㄧㄩ → 去顎化為 ㄓㄔㄕ;
  8 ㄅㄆㄇㄈ 接 ㄨ(且有韻母)→ 去掉介音 ㄨ;9 不存在的聲母介音組合 → 跳過;10 零聲母、無介音、無韻母 → 跳過;
  11 結果與 A 或 B 原音相同 → 跳過(合音沒有效果);12 其他照用。捲舌變平舌(知道 → ㄗㄠˋ)只由詞庫指定。
"""
from __future__ import annotations

import re

INITIALS = "ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙ"
MEDIALS = "ㄧㄨㄩ"
FINALS = "ㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦ"
TONES = ("", "ˊ", "ˇ", "ˋ", "˙")
_MARKED = re.compile(rf"(˙?)([{INITIALS}]?)([{MEDIALS}]?)([{FINALS}]?)([ˊˇˋ˙]?)")
HANZI_RANGES = ((0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0xF900, 0xFAFF), (0x20000, 0x323AF))

SKIP_NO_FINAL = "第二字沒有韻母(ㄓㄔㄕㄖㄗㄘㄙ 單獨成音節),不合音"
SKIP_ER = "第二字是兒化韻(ㄦ),不合音"
SKIP_RI = "ㄖ 不接 ㄧ、ㄩ,合不出合法音節"
SKIP_ILLEGAL = "合不出合法的注音音節"
SKIP_EMPTY = "合不出有韻母的音節"
SKIP_SAME = "合音結果與原本其中一字的讀音相同,合音沒有效果"
SKIP_BAD = "注音不是合法的單一音節"


def is_hanzi(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in HANZI_RANGES)


def split_zhuyin(z) -> tuple[str, str, str, str]:
    """聲調符號格式的注音 → (聲母, 介音, 韻母, 聲調);不合法丟 ValueError。"""
    m = _MARKED.fullmatch(z or "") if isinstance(z, str) else None
    if not m or (m.group(1) and m.group(5)) or not (m.group(2) or m.group(3) or m.group(4)):
        raise ValueError(SKIP_BAD)
    tone = "˙" if (m.group(1) or m.group(5) == "˙") else m.group(5)
    return m.group(2), m.group(3), m.group(4), tone


def join_zhuyin(i: str, m: str, f: str, t: str) -> str:
    body = i + m + f
    return "˙" + body if t == "˙" else body + t


def _illegal(i: str, m: str, f: str) -> bool:
    """D25 第 9 列:不存在的聲母與介音組合。"""
    if i in "ㄅㄆㄇ" and i and m == "ㄩ":
        return True
    if i == "ㄈ" and m in ("ㄧ", "ㄩ"):
        return True
    if i in "ㄉㄊ" and i and m == "ㄩ":
        return True
    if m == "ㄩ" and (not i or i not in "ㄍㄎㄏㄓㄔㄕㄖㄗㄘㄙ") and f not in ("ㄝ", "ㄢ", "ㄣ", "ㄥ", ""):
        return True
    if i and i in "ㄐㄑㄒ" and m not in ("ㄧ", "ㄩ"):
        return True
    if i and i in "ㄍㄎㄏㄓㄔㄕㄖㄗㄘㄙ" and m in ("ㄧ", "ㄩ"):
        return True
    return False


def is_syllable(z) -> bool:
    """單一合法音節:格式合法,且聲母與介音的組合不在合法化表的「跳過」之列(聲調組合不檢查)。"""
    try:
        i, m, f, _ = split_zhuyin(z)
    except ValueError:
        return False
    if not m and not f and i and i not in "ㄓㄔㄕㄖㄗㄘㄙ":
        return False
    return not _illegal(i, m, f)


def explain(a, b) -> dict:
    """fuse 的推導:{"zhuyin": 結果或 None, "reason": 說明, "raw": 合法化前的音節或 None}。"""
    try:
        ia, ma, fa, ta = split_zhuyin(a)
        ib, mb, fb, tb = split_zhuyin(b)
    except ValueError:
        return {"zhuyin": None, "reason": SKIP_BAD, "raw": None}
    if not fb and not mb:
        return {"zhuyin": None, "reason": SKIP_NO_FINAL, "raw": None}
    if fb == "ㄦ":
        return {"zhuyin": None, "reason": SKIP_ER, "raw": None}
    i = ia
    m = mb if (ia or mb) else ma
    f = fb
    t = ta if tb == "˙" else tb
    raw = join_zhuyin(i, m, f, t)
    reason = "照用"
    if i == "ㄖ" and m in ("ㄧ", "ㄩ"):
        return {"zhuyin": None, "reason": SKIP_RI, "raw": raw}
    if i and i in "ㄓㄗㄍ" and m in ("ㄧ", "ㄩ"):
        i, reason = "ㄐ", f"顎化({ia} → ㄐ)"
    elif i and i in "ㄔㄘㄎ" and m in ("ㄧ", "ㄩ"):
        i, reason = "ㄑ", f"顎化({ia} → ㄑ)"
    elif i and i in "ㄕㄙㄏ" and m in ("ㄧ", "ㄩ"):
        i, reason = "ㄒ", f"顎化({ia} → ㄒ)"
    elif i and i in "ㄐㄑㄒ" and m not in ("ㄧ", "ㄩ"):
        new = {"ㄐ": "ㄓ", "ㄑ": "ㄔ", "ㄒ": "ㄕ"}[i]
        i, reason = new, f"去顎化({ia} → {new})"
    elif i and i in "ㄅㄆㄇㄈ" and m == "ㄨ" and f:
        m, reason = "", "唇音去掉介音 ㄨ"
    if _illegal(i, m, f):
        return {"zhuyin": None, "reason": SKIP_ILLEGAL, "raw": raw}
    if not i and not m and not f:
        return {"zhuyin": None, "reason": SKIP_EMPTY, "raw": raw}
    out = join_zhuyin(i, m, f, t)
    if out in (join_zhuyin(ia, ma, fa, ta), join_zhuyin(ib, mb, fb, tb)):
        return {"zhuyin": None, "reason": SKIP_SAME, "raw": raw}
    return {"zhuyin": out, "reason": reason, "raw": raw}


def fuse(a, b) -> tuple[str | None, str]:
    """兩字注音 → (合音注音, 說明);不合音時為 (None, 原因)。"""
    r = explain(a, b)
    return r["zhuyin"], r["reason"]


def suggest(text: str, zhuyins, lexicon: dict) -> list[dict]:
    """句中出現在詞庫的相鄰兩字(由左到右、不重疊);lexicon 為 {兩字詞: 合音注音}(只含啟用者)。"""
    out, i = [], 0
    chars = list(text or "")
    while i < len(chars) - 1:
        word = chars[i] + chars[i + 1]
        z = (lexicon or {}).get(word)
        known = zhuyins is None or (i + 1 < len(zhuyins) and zhuyins[i] and zhuyins[i + 1])
        if z and is_hanzi(chars[i]) and is_hanzi(chars[i + 1]) and known:
            out.append({"index": i, "zhuyin": z, "source": "lexicon"})
            i += 2
            continue
        i += 1
    return out
