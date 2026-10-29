# -*- coding: utf-8 -*-
"""合音規則(CHG-20261029-01 T7,FR-39;D24、D25)。

相鄰兩字 A、B 合成一個音節(例:這樣 → ㄐㄧㄤˋ「醬」、知道 → 詞庫 ㄗㄠˋ「造」)。純函式、只用標準庫;
注音一律為聲調符號格式(同 `breezy_worker`:一聲不標、輕聲以 ˙ 開頭或結尾)。

介面:
  split_zhuyin(z) -> (聲母, 介音, 韻母, 聲調)   聲調為 ""(一聲)、ˊ、ˇ、ˋ、˙;不合法丟 ValueError
  join_zhuyin(聲母, 介音, 韻母, 聲調) -> str      輕聲寫在前(˙ㄇㄣ,同 G2PW 輸出)
  fuse(a, b) -> (zhuyin | None, reason)        依 D24 規則與 D25 合法化表;不合音時 zhuyin 為 None、reason 為原因
  explain(a, b) -> {"zhuyin", "reason", "raw"}  另附合法化前的音節(彈出框顯示推導)
  suggest(text, zhuyins, lexicon, exclude=()) -> [{"index", "length", "zhuyin", "source": "lexicon"}]
                                               句中出現在詞庫(啟用者)的 2~4 字詞;依「字數多到字數少」
                                               (4 字 → 3 字 → 2 字)、每層由左至右、不重疊(D24、D38);
                                               exclude 為不再提示的字元索引(已套用合音、聲調標註、關閉紀錄)
  pair_options(chars, zhuyins, lexicon) -> [{"offset", "length", "zhuyin", "source", "reason"}]
                                               3~4 字選取不在詞庫時的候選(D37):4 字選取先列其中的 3 字詞庫詞,
                                               再列每一對相鄰兩字(詞庫值優先,其次規則結果,不合法者 zhuyin 為 None)
  combine(zhuyins, option) -> str               選取範圍的合音注音:其餘字維持原讀音,option 範圍換成它的音節
  syllables(z) / is_fusion_zhuyin(z, n)        合音注音以半形空白分隔多個音節;n 字的合音必須是 1~n−1 個音節
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


MIN_CHARS, MAX_CHARS = 2, 4


def syllables(z) -> list[str]:
    return z.split(" ") if isinstance(z, str) and z else []


def is_fusion_zhuyin(z, n: int) -> bool:
    """n 字合音的注音:1~n−1 個以單一半形空白分隔的合法音節(D31)。"""
    parts = syllables(z)
    return 1 <= len(parts) <= n - 1 and all(is_syllable(p) for p in parts)


def suggest(text: str, zhuyins, lexicon: dict, exclude=()) -> list[dict]:
    """句中出現在詞庫(啟用者)的 2~4 字詞:4 字 → 3 字 → 2 字,每層由左至右、不重疊(D24、D38)。

    lexicon 為 {詞: 合音注音};zhuyins(逐字注音,可為 None)中有字沒有注音的範圍略過;exclude 為不提示的字元索引。"""
    chars = list(text or "")
    used = set(exclude or ())
    out = []
    for n in range(MAX_CHARS, MIN_CHARS - 1, -1):
        i = 0
        while i <= len(chars) - n:
            span = range(i, i + n)
            word = "".join(chars[i:i + n])
            z = (lexicon or {}).get(word)
            known = zhuyins is None or all(k < len(zhuyins) and zhuyins[k] for k in span)
            if z and known and all(is_hanzi(chars[k]) for k in span) and not used & set(span):
                out.append({"index": i, "length": n, "zhuyin": z, "source": "lexicon"})
                used |= set(span)
                i += n
                continue
            i += 1
    return sorted(out, key=lambda x: x["index"])


def pair_options(chars, zhuyins, lexicon: dict) -> list[dict]:
    """3~4 字選取的候選(D37):4 字時先列其中的 3 字詞庫詞,再列每一對相鄰兩字。

    每項 {"offset", "length", "zhuyin": 該範圍的合音注音或 None, "source": lexicon | rule | None, "reason"}。"""
    chars = list(chars)
    lex = lexicon or {}
    out = []
    if len(chars) == 4:
        for o in (0, 1):
            z = lex.get("".join(chars[o:o + 3]))
            if z:
                out.append({"offset": o, "length": 3, "zhuyin": z, "source": "lexicon", "reason": "詞庫"})
    for o in range(len(chars) - 1):
        z = lex.get("".join(chars[o:o + 2]))
        if z:
            out.append({"offset": o, "length": 2, "zhuyin": z, "source": "lexicon", "reason": "詞庫"})
            continue
        a = zhuyins[o] if zhuyins and o < len(zhuyins) else None
        b = zhuyins[o + 1] if zhuyins and o + 1 < len(zhuyins) else None
        r = explain(a, b) if a and b else {"zhuyin": None, "reason": "缺少這兩個字的注音"}
        out.append({"offset": o, "length": 2, "zhuyin": r["zhuyin"], "source": "rule" if r["zhuyin"] else None,
                    "reason": r["reason"]})
    return out


def default_option(options: list) -> int | None:
    """D37 預設:3 字詞庫詞 → 兩字詞庫值 → 第一對合法的規則結果;都沒有回 None。"""
    for pick in (lambda o: o["length"] == 3, lambda o: o["source"] == "lexicon", lambda o: o["zhuyin"]):
        for k, o in enumerate(options):
            if o["zhuyin"] and pick(o):
                return k
    return None


def combine(zhuyins, option: dict) -> str | None:
    """選取範圍的合音注音:option 範圍換成它的音節,其餘字維持原讀音;有字沒有注音回 None。"""
    o, m = option["offset"], option["length"]
    if not option.get("zhuyin") or any(not z for k, z in enumerate(zhuyins) if not o <= k < o + m):
        return None
    return " ".join([*zhuyins[:o], *syllables(option["zhuyin"]), *zhuyins[o + m:]])
