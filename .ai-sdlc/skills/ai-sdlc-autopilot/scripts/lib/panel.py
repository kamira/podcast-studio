#!/usr/bin/env python3
"""程式碼審查面板:座位、否決權、信心降級(CHG-20260803-09)。

**本模組不發明新機制。** 治理層的 `ai-sdlc/references/review-panel.md` 已經定義了
座位制(一席一領域)、判定行文法、硬規則否決權、風險分級調用與無 spawn 降級;
這裡把那套機制擴到一個新階段——程式碼 diff 審查。座位不同(領域不同),機制相同。

其中最要緊的一條:review-panel 明寫**「分歧是調和或升級,絕不平均」**。
所以信心分數在這裡**只用來降級**(把沒把握的票變成「無法判定」),
永遠不參與任何平均或加權——一個有把握的反對票,不該被兩個沒去看那個角落的贊成票稀釋。

裁決算術一律在這裡(no-LLM)完成,不交給模型彙整:彙整者的盲點會蓋過所有座位,
而且那又是一次「模型轉述模型」的失真。
"""
from __future__ import annotations
import json
import re
from pathlib import Path

from .exec_util import LINE_START, TIMEOUT_NOTE_PREFIX, global_constraints, quote_lines

SEATS_TABLE = Path(__file__).resolve().parent.parent.parent / "assets" / "review_seats.json"

# c5af2cc 的兩個模組層常數:判定解析已不用它們(改用 VERDICT_LINE_RE),但對外契約只增不減,
# 照原樣保留給既有的 import(實作後審查,四席)。
HEAD_RE = re.compile(r"\[task-review\]\s*(T\d+|branch)", re.IGNORECASE)
CONF_RE = re.compile(r"^conf(?:idence)?\s*[:\uff1a]\s*(\d{1,3})\s*$", re.IGNORECASE)
SPEC_VALUES = ("pass", "fail", "cannot-verify")
QUALITY_VALUES = ("pass", "fail")

DEFAULT_THRESHOLD = 80          # 沿用官方 code-review 插件已驗證過的門檻,不憑空發明數字
# 風險分級決定席次:面板是為風險加的深度,不是對所有事情課的稅(review-panel 原話)。
SEATS_BY_RISK = {"low": 1, "medium": 3, "high": 3}

# ── 判定行文法(CHG-20260926-01 T7;NEW-3、NEW-10、NEW-14,代決 R6)────────────────────
#   [task-review] <T<n>|branch> [| <座位> [| <模型>]] | spec: <值> | quality: <值> [| conf: <0-100>] [| <理由>]
# · 行首只准 exec_util.LINE_START(空白、>、*、_、反引號、-,可再加一個列表編號),與 `[blocked]`
#   回報同一套;**不含 `|`**——runner 引用進 brief 的判定行都帶 `| ` 前綴(quote_lines),
#   被回顯或照抄都不是候選行。c5af2cc 在行中任何位置找 `[task-review]`,取第一個。
# · 值必須是單一的允許字:格式說明(`spec: pass|fail|cannot-verify`、`spec: <值>`)本身不是判定行;
#   c5af2cc 逐欄 split 之後拿第一個候選值,抄來的模板因此讀成 pass,蓋過真正的否決。
# · 位置欄不可長得像 `spec:` / `quality:`——否則 `spec: fail | spec: pass` 會把 fail 吞進位置欄。
# · 理由是其餘的全部:以 Specification / Quality 開頭的理由不再讓整行作廢。
# · Markdown 強調(`*`、`_`、反引號)在各欄一律容許:行首與位置欄本來就容許,target 與值欄不容許的話,
#   `[task-review] **T1** |`、`**spec: fail**` 整行不是候選行,同一份回答裡另一行不帶強調的 pass 就成了
#   唯一的判定——強調過的否決被默默吞掉(實作後審查,四席)。容許之後兩行不一致,照 R6 是沒有回答。
_E = r"[ \t*_`]*"
_POS_FIELD = r"\|(?!" + _E + r"(?:spec|quality)[*_`]*[ \t]*[:\uff1a])[^|\n]*"
VERDICT_LINE_RE = re.compile(
    r"^" + LINE_START
    + r"(?P<body>\[task-review\]" + _E + r"(?P<target>T\d+|branch)" + _E
    + r"(?P<pos>(?:" + _POS_FIELD + r")*?)"
    r"\|" + _E + r"spec[*_`]*[ \t]*[:\uff1a]" + _E + r"(?P<spec>pass|fail|cannot-verify)" + _E
    + r"\|" + _E + r"quality[*_`]*[ \t]*[:\uff1a]" + _E + r"(?P<quality>pass|fail)" + _E
    + r"(?!\|" + _E + r"(?:pass|fail|cannot-verify)" + _E + r"(?:\||\r?$))"
    r"(?:\|" + _E + r"conf(?:idence)?[*_`]*[ \t]*[:\uff1a](?P<conf>[^|\n]*))?"
    r"(?:\|(?P<reason>[^\n]*))?[ \t\r]*)$",
    re.IGNORECASE | re.MULTILINE)


def _norm_target(t) -> str:
    return "branch" if str(t).lower() == "branch" else str(t).upper()


def _field(s) -> str:
    """位置欄的比較用形式:去掉空白、`*`、`_`、反引號與結尾的「席」,小寫。"""
    s = str(s).strip().strip("*_` \t")
    if s.endswith("席"):
        s = s[:-1]
    return s.strip().strip("*_` \t").lower()


def _conf(raw) -> tuple[int | None, str | None]:
    """conf 欄 → (0–100 的整數, None) 或 (None, 讀不懂的原文)。接受 `%`;沒有這一欄 → (None, None)。"""
    if raw is None:
        return None, None
    s = raw.strip().strip("*_`").strip()            # 強調符號不是值的一部分
    num = s[:-1].rstrip() if s.endswith("%") else s
    if num.isascii() and num.isdigit() and len(num) <= 3 and int(num) <= 100:
        return int(num), None
    return None, s


def verdict_lines(line, target=None, seat=None) -> list:
    """回所有合格的候選判定行(dict 清單,欄位同 parse_verdict)。壞輸入回 []。

    · 給了 `target`:點名其他 target 的行跳過(審 T1 時,T9 的判定不是 T1 的)。
    · 給了 `seat`:任何一個位置欄(去掉強調符號與結尾的「席」)等於本席就是本席的行——座位欄與
      模型欄對調也認得;有位置欄但沒有一個是本席的,是別席的行;**沒有位置欄的行不算本席的判定**
      (一席引用一行沒有座位欄的 pass,不能蓋過它自己的 fail)。
    · 沒給 `seat`(低風險單一審查者、整支審查):舊格式照收,位置欄依序是座位、模型。
    """
    if not line or not isinstance(line, str):
        return []
    want = _norm_target(target) if target else None
    me = _field(seat) if seat else None
    out = []
    for m in VERDICT_LINE_RE.finditer(line):
        if want and _norm_target(m.group("target")) != want:
            continue
        raw_pos = [p.strip().strip("*_` \t") for p in (m.group("pos") or "").split("|")[1:]]
        pos = [p for p in raw_pos if p]
        who: str | None
        model: str | None
        if me is not None:
            if me not in [_field(p) for p in pos]:
                continue
            rest = [p for p in pos if _field(p) != me]
            who, model = str(seat), (rest[0] if rest else None)
        else:
            who, model = (pos[0] if pos else None), (pos[1] if len(pos) > 1 else None)
        conf, conf_raw = _conf(m.group("conf"))
        out.append({"target": _norm_target(m.group("target")),
                    "seat": who,
                    "model": model,
                    "spec": m.group("spec").lower(),
                    "quality": m.group("quality").lower(),
                    "confidence": conf,
                    "conf_raw": conf_raw,
                    "line": m.group("body").strip()})
    return out


def _conf_rank(v) -> tuple:
    """信心取最保守的一份:讀不懂的 conf 優先,其次是最小的數字,最後才是沒有 conf 的。"""
    if v["confidence"] is None and v["conf_raw"] is not None:
        return (0, 0)
    if v["confidence"] is not None:
        return (1, v["confidence"])
    return (2, 0)


def parse_verdict(line, target=None, seat=None):
    """在一段輸出裡找判定。回 dict 或 None(壞輸入不崩潰)。

    候選行(verdict_lines)的 (spec, quality) 只有一種 → 採用,信心取最保守的一份;
    **兩種以上 → None**(代決 R6:「最後一行算數」會讓引用的 pass 蓋過否決,「任一 fail 勝」
    會讓引用的舊 fail 白燒一輪回修;不一致就是沒有回答,由 read_answer 說明、runner 停下交人)。
    同一個判定重複寫兩次照樣採用。conf 選填;讀不懂的保留在 `conf_raw`,由 downgrade 降級。
    """
    cands = verdict_lines(line, target, seat)
    if not cands:
        return None
    kinds = {(c["spec"], c["quality"]) for c in cands}
    if len(kinds) > 1:
        return None
    return min(cands, key=_conf_rank)


def downgrade(verdict: dict, threshold: int = DEFAULT_THRESHOLD) -> tuple[dict, str | None]:
    """信心低於門檻 → spec 降級為 `cannot-verify`。回 (判定, 註記)。

    **降級,不是扣分**:沒把握的綠燈不算綠燈,但它也不會被拿去和別票平均。
    沒有信心欄位者不降級(舊格式相容),但註明「未提供信心」供稽核。
    **寫了信心欄、卻讀不出 0–100 的整數**(`conf: high`、`conf: 0.6`、`conf: 150`)視同低於門檻,
    註記附上原值(CHG-20260926-01 T7,NEW-10):讀不出來的把握不算把握。
    """
    v = dict(verdict)
    conf = v.get("confidence")
    who = v.get('seat') or '(未具名席)'
    if conf is None and v.get("conf_raw") is not None:
        v["spec"] = "cannot-verify"
        v["downgraded"] = True
        return v, (f"{who}:信心欄「{v['conf_raw']}」不是 0 到 100 的整數 "
                   f"→ 降級為 cannot-verify(讀不出來的把握不算把握)")
    if conf is None:
        return v, f"{who}:未提供信心分數(舊格式)"
    if conf < threshold:
        v["spec"] = "cannot-verify"
        v["downgraded"] = True
        return v, (f"{who}:信心 {conf} < {threshold} "
                   f"→ 降級為 cannot-verify(沒把握的綠燈不算綠燈)")
    return v, None


def _quoted_lines(verdicts) -> str:
    """裁決訊息引用的判定行:逐行加 `| ` 前綴(quote_lines)。這段訊息會被帶進下一輪的 brief,
    席位回顯它、或照抄它,都不能被讀成一個它沒寫的判定(代決 R6 的配套)。"""
    return quote_lines("\n".join(f"    {v['line']}" for v in verdicts))


def adjudicate(verdicts, threshold: int = DEFAULT_THRESHOLD) -> tuple[bool, str]:
    """面板裁決。回 (是否放行, 人讀訊息)。

    規則沿用 review-panel:
      · **spec fail = 否決**,裁決者不得推翻——規格不符是事實判斷,不是意見
      · quality fail 同樣擋下
      · 全體 cannot-verify → 擋下:「查不出來」不等於「沒問題」(KN-004)
      · 分歧**不平均**:任何一票有把握的反對即成立
    不通過的訊息逐席附上判定行(含理由,#5):施工者下一輪要看得到原因,不是只剩一句「判 fail」。
    """
    if not verdicts:
        return False, "面板無任何判定行——沒有輸出不算通過"
    graded, notes = [], []
    for v in verdicts:
        g, note = downgrade(v, threshold)
        graded.append(g)
        if note:
            notes.append(f"    · {note}")
    prefix = ("信心降級紀錄:\n" + "\n".join(notes) + "\n") if notes else ""

    def who(v):
        return v.get("seat") or v.get("model") or "(未具名席)"

    vetoes = [v for v in graded if v["spec"] == "fail"]
    if vetoes:
        return False, (prefix + "面板裁決:**否決**——"
                       + "、".join(f"{who(v)} 判 spec: fail" for v in vetoes)
                       + "。規格不符是事實判斷,不是意見,裁決者不得以其他席的贊成票推翻。\n"
                       + _quoted_lines(vetoes))
    quality_fail = [v for v in graded if v["quality"] == "fail"]
    if quality_fail:
        return False, (prefix + "面板裁決:擋下——"
                       + "、".join(f"{who(v)} 判 quality: fail" for v in quality_fail) + "\n"
                       + _quoted_lines(quality_fail))
    if all(v["spec"] == "cannot-verify" for v in graded):
        return False, (prefix + "面板裁決:擋下——**全席皆無法判定**。"
                       "「查不出來」不等於「沒問題」:放行等於把未經判斷的變更往下游送。\n"
                       "    請補足證據(縮小 diff、補上下文)或降低門檻後重跑。各席寫下的缺口:\n"
                       + _quoted_lines(graded))
    passed = [v for v in graded if v["spec"] == "pass"]
    return True, (prefix + f"面板裁決:放行({len(passed)}/{len(graded)} 席有把握地通過"
                  + (f",{len(graded) - len(passed)} 席無法判定)" if len(passed) != len(graded)
                     else ")"))


# 交叉讀列:`[cross] <本席>→<對方席> | agree|disagree | 理由`,一個對方席一列(CHG-20260926-01 T8)。
# 行首規則與判定行相同(LINE_START,不含 `|`);值必須是完整的欄位——`agree|disagree`(格式說明)、
# `<agree 或 disagree>`(模板)、`partially agree` 都不是值。c5af2cc 在行中任何位置比對,
# 抄回來的模板會被讀成 agree。
CROSS_RE = re.compile(
    r"^" + LINE_START
    + r"\[cross\][*_`]*[ \t]*(?P<frm>[^\s|→>]+?)[ \t]*(?:→|->)[ \t]*(?P<to>[^\s|]+)[ \t]*"
    r"\|[ \t]*[*_`]*(?P<val>agree|disagree)[*_`]*[ \t]*"
    r"(?:\|(?![ \t]*(?:agree|disagree)[ \t]*(?:\||\r?$))(?P<reason>[^\n]*))?[ \t\r]*$",
    re.IGNORECASE | re.MULTILINE)


def parse_cross(text, seat=None, others=None) -> list:
    """解析交叉讀列。回 [{from, to, agree, reason}];壞輸入回 []。

    給了 `seat`:只算本席寫的列;給了 `others`:只算指向面板裡真實他席的列。
    同一對寫了互相矛盾的 agree / disagree → 那一列算缺(代決 R6,與判定行同一條規則:
    引用造成的矛盾一律停下交人,不猜哪一行才是真的);同值重複照收。
    """
    if not text or not isinstance(text, str):
        return []
    me = _field(seat) if seat else None
    keep = {_field(o) for o in others} if others is not None else None
    rows: dict = {}
    vals: dict = {}
    for m in CROSS_RE.finditer(text):
        frm, to = _field(m.group("frm")), _field(m.group("to"))
        if me is not None and frm != me:
            continue
        if keep is not None and (to not in keep or to == frm):
            continue
        vals.setdefault((frm, to), set()).add(m.group("val").lower())
        rows.setdefault((frm, to), {"from": frm, "to": to,
                                    "agree": m.group("val").lower() == "agree",
                                    "reason": (m.group("reason") or "").strip().strip("*_`").strip()})
    return [r for k, r in rows.items() if len(vals[k]) == 1]


NO_REPLY = "這是一次無人值守的審查:沒有人能回答你的問題——不要提問、不要等人核可,不會有任何回覆。"


def diff_location(test_cmd=None) -> str:
    """逐 task 審查與交叉讀 brief 的一句:diff 在哪、runner 已跑過什麼(T7;驗收第 43 條)。
    新檔是未追蹤的,只看 `git diff` 會漏掉它們。"""
    # 「各道閘」只指本輪啟用的:以逃生口關掉的閘沒有跑(實作後審查),不能說成都通過了
    ran = (f"送審之前 runner 已跑過 `{test_cmd}` 與本輪啟用的各道閘,都通過了"
           "(以逃生口關掉的閘沒有跑,見交接檔的 escape-hatches 行)。" if test_cmd
           else "本輪沒有測試指令,runner 沒有跑測試與各道閘。")
    return ("要審的是工作樹裡尚未提交的變更,包括未追蹤的新檔:"
            "`git status --porcelain --untracked-files=all` 與 `git diff HEAD`;" + ran + "\n")


def _entry(task) -> str:
    if isinstance(task, dict):
        return task.get("entry") or f"{task.get('tid', '?')}. {task.get('title', '')}"
    return str(task)


def _chg_line(chg_path) -> str:
    return f"CHG 檔案:{Path(chg_path).resolve()}\n" if chg_path else ""


def cross_brief(verdicts, seat: str, *, task=None, chg_path=None, test_cmd=None) -> str:
    """第二階段簡報:讓這一席讀其他席的判定並標記同意與否。

    第一階段刻意不給(防定錨:先讀到別人的結論,就會先同意它);
    第二階段才給,而且只求標記分歧,不求達成共識——
    **分歧是調和或升級,絕不平均**。每一席都是全新行程,看不到自己上一輪說過什麼,
    所以本席的判定也附上(代決 R4)——否則「不要改你的判定」是一句找不到對象的話。
    引用的判定行一律加 `| ` 前綴;要席位填的那幾列模板不加(模板本身解析不出值)。
    """
    own = [v for v in verdicts if v.get("seat") == seat]
    others = [v for v in verdicts if v.get("seat") != seat]
    mine = _quoted_lines(own) or "    (無)"
    theirs = _quoted_lines(others) or "    (無其他席判定)"
    rows = "\n".join(f"[cross] {seat}→{v.get('seat')} | <agree 或 disagree> | <一句理由>"
                     for v in others)
    entry = (f"## Task 條目(取自 CHG,原文)\n{quote_lines(_entry(task))}\n\n"
             if task is not None else "")
    return (f"交叉讀階段。你是 **{seat}** 席。唯讀審查,不修改任何檔案。\n"
            f"{NO_REPLY}\n\n"
            f"{entry}{_chg_line(chg_path)}{diff_location(test_cmd)}\n"
            f"拿不準同不同意,就寫 disagree,並在理由寫出拿不準的是什麼。\n\n"
            f"你在第一階段給的判定(不要改它):\n{mine}\n\n"
            f"以下是其他席的判定,請逐一標記你是否同意——只標記,\n"
            f"也不要為了一致而讓步:分歧會帶進回修或交給人,不會被平均掉。\n\n"
            f"{theirs}\n\n"
            f"其他席每一席各寫一列、共 {len(others)} 列;每列獨立成行、行首不要放任何符號或編號,"
            f"把 <…> 換成實際的值:\n"
            f"{rows}\n"
            f"第二欄只能是 agree 或 disagree 其中一個字;部分不同意就寫 disagree。"
            f"少了哪一席的那一列,或同一席寫了互相矛盾的兩列,runner 會把你這一席當成沒有回答。\n")


def adjudicate_cross(crosses, expected=None) -> tuple[bool, str]:
    """交叉讀的裁決:有分歧就該輪不通過並點名雙方;缺列不是「無分歧」。

    review-panel:「A disagreement is reconciled or escalated — never averaged」。
    runner 不會替兩席做調和,那需要判斷;它只負責讓分歧無法被靜默吞掉——分歧原文帶進
    下一輪回修,達回修上限時交人。**一列都沒有,或缺 `expected` 裡的任何一個配對 → 不通過**
    (c5af2cc 回「0 條旗標,無分歧」並放行,#10)。
    """
    crosses = list(crosses or [])
    got = {(_field(c["from"]), _field(c["to"])) for c in crosses}
    missing = [f"{a}→{b}" for a, b in (expected or []) if (_field(a), _field(b)) not in got]
    if not crosses or missing:
        return False, ("交叉讀:**缺列**——沒有標記不等於同意,沒有輸出不算通過。"
                       + (f"缺:{'、'.join(missing)}" if missing else "一列都沒有"))
    dis = [c for c in crosses if not c["agree"]]
    if not dis:
        return True, f"交叉讀:{len(crosses)} 條旗標,無分歧"
    rows = "\n".join(f"    · {c['from']} → {c['to']}:{c['reason'] or '(未寫理由)'}"
                     for c in dis)
    return False, ("交叉讀:**席次之間有分歧**——分歧是調和或升級,絕不平均。"
                   "本輪不通過:分歧原文帶進下一輪回修;達回修上限時交人。\n" + rows)


# ── 沒有回答不是判定(CHG-20260926-01 T8;NEW-9、#10,代決 R1、R6)──────────────────────
# c5af2cc 不看席位的退出碼與 stderr:崩潰、只提問、逾時的席被記成 cannot-verify 加 quality pass,
# 握有否決權的 conformance 席崩潰、另兩席判 pass 照樣放行。cannot-verify 是「看過而查不出來」;
# 沒有回答是「沒有看」——不算進任何一邊,runner 停下這個 task 交人,不重派施工者。
TAIL_LIMIT = 600


def _tail(s, n: int = TAIL_LIMIT) -> str:
    s = (s or "").strip()
    body = ("…" + s[-n:]) if len(s) > n else (s or "(無輸出)")
    return quote_lines(body)


def _tails(stdout, stderr) -> str:
    return (f"\n      stdout 尾段:{_tail(stdout)}\n      stderr 尾段:{_tail(stderr)}")


def read_answer(rc, stdout, stderr, target, seat=None) -> tuple[dict | None, str | None]:
    """一席(或單一審查者)的回答 → (判定, None) 或 (None, 為什麼不算回答)。

    · 非零退出(包括 124 逾時、以非零退出提問)一律不是判定,**即使 stdout 有合格的判定行**:
      那個行程沒有正常結束;
    · 退出 0 但沒有合格行(提問、散文、只抄了格式、點名別的 task 或別席),不是判定;
    · 合格行互相矛盾(代決 R6),不是判定。
    原因帶 stdout 與 stderr 的尾段(各 600 字),並說明是哪一種。
    """
    who = f"{seat} 席" if seat else "審查者"
    if rc != 0:
        return None, (f"{who}以退出碼 {rc} 結束(崩潰、逾時(124),或以非零退出提問)——"
                      "非零退出不算回答,即使輸出裡有判定行。" + _tails(stdout, stderr))
    cands = verdict_lines(stdout or "", target, seat)
    if not cands:
        scope = f"點名 {target}" + (f" 與 {seat} 席" if seat else " ")   # 「點名 T5 的」「點名 T5 與 x 席的」
        return None, (f"{who}沒有給出{scope}的合格判定行(提問、散文、只抄了格式,"
                      "或點名了別的 task 或別席)——不算回答。" + _tails(stdout, stderr))
    v = parse_verdict(stdout or "", target, seat)
    if v is None:
        return None, (f"{who}的判定互相矛盾:點名 {target} 的合格判定行不只一種值——"
                      "不猜哪一行才是真的,當成沒有回答(代決 R6)。\n"
                      + _quoted_lines(cands))
    if seat:
        v["seat"] = seat            # runner 知道自己問的是誰;判定行的座位欄只是紀錄
    return v, None


def read_cross_answer(rc, stdout, stderr, seat, others) -> tuple[list, str | None]:
    """一席交叉讀的回答 → (列, None) 或 ([], 為什麼不算回答)。每個他席恰好一種值。"""
    if rc != 0:
        return [], (f"{seat} 席的交叉讀以退出碼 {rc} 結束(崩潰、逾時(124),或以非零退出提問)"
                    "——不算回答。" + _tails(stdout, stderr))
    rows = parse_cross(stdout or "", seat=seat, others=others)
    have = {r["to"] for r in rows}
    missing = [o for o in others if _field(o) not in have]
    if missing:
        return [], (f"{seat} 席的交叉讀缺 {'、'.join(f'{seat}→{o}' for o in missing)} 的列"
                    "(或同一對寫了互相矛盾的 agree / disagree;值只能是完整的 agree 或 disagree)"
                    "——不算回答。" + _tails(stdout, stderr))
    return rows, None


def retryable(rc, timed_out, stdout, stderr) -> bool:
    """行程層級失敗 → 以同一份 brief 重試同一席一次(代決 R1-D)。只用字串操作。

    成立條件(三者都要):非零退出或逾時;去掉 runner 自己附加的逾時註記後 stdout 是空白;
    stderr 沒有任何一行看起來像判定行或交叉讀列。stdout 有任何文字(散文否決、提問、寫壞的判定)
    就不重試——不「重抽」一個格式寫壞的否決。stderr 的判斷比 LINE_START 略寬,只會少重試。
    """
    if rc == 0 and not timed_out:
        return False
    for ln in (stdout or "").splitlines():
        if ln.strip() and not ln.strip().startswith(TIMEOUT_NOTE_PREFIX):
            return False
    for ln in (stderr or "").splitlines():
        head = ln.lstrip(" \t>*_`-0123456789.)").lower()
        if head.startswith("[task-review]") or head.startswith("[cross]"):
            return False
    return True


def load_seats(path=None) -> dict:
    p = Path(path) if path else SEATS_TABLE
    return json.loads(p.read_text(encoding="utf-8-sig")).get("seats", {})


def seats_for(risk: str, override=None, seats=None) -> tuple[list, str | None]:
    """依風險分級決定開幾席。回 (座位清單, 註記)。

    覆寫可以往上加,不能往下減到分級下限以下——否則「面板」會變成
    一個可以隨手關掉的裝飾。上限是座位表(`assets/review_seats.json`)的席數:
    要求的席數超過時照上限開,並回一則註記(CHG-20260926-01 T9,#11;c5af2cc 靜默截掉)。
    """
    table = seats if seats is not None else load_seats()
    order = list(table.keys())
    floor = SEATS_BY_RISK.get((risk or "low").lower(), 1)
    n, note, asked = floor, None, f"{risk} 風險的 {floor} 席"
    if override:
        try:
            want = int(override)
        except (TypeError, ValueError):
            want = floor
        if want < floor:
            note = (f"--review-panel {want} 低於 {risk} 風險的下限 {floor} 席,"
                    f"已提升為 {floor}——面板是風險的深度,不是可隨手關掉的裝飾")
        n = max(floor, want)
        if want > floor:
            asked = f"--review-panel {want}"
    if order and n > len(order):
        note = (f"{asked} 超過座位表 review_seats.json 的 {len(order)} 席,"
                f"只開 {len(order)} 席——要更多席,先在座位表加座位")
        n = len(order)
    return order[:n], note


def answer_contract(target, seat=None, threshold: int = DEFAULT_THRESHOLD) -> str:
    """回答契約:每一份審查 brief 的結尾(單一審查者、面板各席;T7,代決 R3)。

    模板本身解析不出值(`<…>` 不是允許值),所以被照抄也不是判定;不給範例數字——
    範例數字會被照抄成信心分數。
    """
    cols = f"{target} | {seat} | <你的模型名>" if seat else f"{target}"
    scope = f"點名 {target}" + (f" 與 {seat} 席" if seat else " ")   # 「點名 T5 的」「點名 T5 與 x 席的」
    return (
        "\n## 回答格式(runner 寫給你的;無人值守)\n\n"
        "這是一次無人值守的審查:沒有人能回答你的問題——不要提問、不要等人核可。"
        "看不到判斷所需的證據時(例如找不到 diff、task 的要求不在你拿到的內容裡),"
        "判 `spec: cannot-verify`,並在理由寫出缺的是哪一項。\n\n"
        "以恰好一行判定結束你的回答。這一行獨立成行、以 [task-review] 開頭,前面不要放任何符號或編號;"
        "欄位照下面的順序,把每個 <…> 換成實際的值:\n"
        f"[task-review] {cols} | spec: <pass、fail 或 cannot-verify> | quality: <pass 或 fail> | "
        "conf: <0 到 100 的整數> | <一句理由,引用 file:line>\n"
        "- spec 只能是 pass、fail、cannot-verify 三者之一;quality 只能是 pass、fail 兩者之一。\n"
        f"- conf 是 0 到 100 的整數,代表你對這個判定有多確定;低於 {threshold} 視同 cannot-verify。\n"
        "- 理由一句話,引用證據(file:line)。\n"
        "- 不要抄上面的格式。這份簡報裡由 runner 引用的判定行都帶 `| ` 前綴;逐項回覆時要引用,"
        "就連同前綴照抄,不要改寫成不帶前綴的判定行。\n"
        f"- runner 讀你回答裡所有{scope}的合格判定行;它們的值不一致,就當成你沒有回答。\n")


def seat_brief(chg_text: str, task, seat: str, seats=None, *, chg_path=None, test_cmd=None,
               extra: str = "", threshold=None) -> str:
    """單一座位的簡報:**只含自己那一列**。

    review-panel:「the panel view belongs to the dispatcher」——
    給了整張表,座位就會開始揣測別人會怎麼投,而那正是獨立性的反面。
    T7:顯示 T5 的 task 條目、CHG 路徑與 diff 的位置;複審附加段(`extra`)經 quote_lines;
    回答契約放在最後。新參數一律 keyword-only。
    """
    table = seats if seats is not None else load_seats()
    spec = table.get(seat, {})
    # 截到下一個標題、行首標記加引用前綴(CHG-20260926-01 T5,代決 B9、B3)
    gc = quote_lines(global_constraints(chg_text))
    tid = task["tid"] if isinstance(task, dict) else str(task)
    return (
        f"你是本次 diff 審查面板的其中一席:**{seat}**。唯讀審查,不修改任何檔案。\n"
        f"你的職責(只有這一條,其他席次的職責不歸你管,也不會告訴你):\n"
        f"  {spec.get('question', '(未定義)')}\n"
        f"你的判定必須引用證據:{spec.get('evidence', 'file:line')}。\n"
        f"沒把握就給低信心分數——**沒把握的綠燈不算綠燈**,低於門檻會被降級為無法判定,\n"
        f"那是正當的結果,不是失敗。不要為了給出結論而提高信心。\n\n"
        f"{_chg_line(chg_path)}{diff_location(test_cmd)}\n"
        f"## Task 條目(取自 CHG,原文)\n{quote_lines(_entry(task))}\n\n"
        f"{gc}\n{quote_lines(extra or '')}"
        + answer_contract(tid, seat, threshold or DEFAULT_THRESHOLD))
