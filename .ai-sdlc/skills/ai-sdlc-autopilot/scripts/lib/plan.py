#!/usr/bin/env python3
"""計畫解析:CHG markdown → task 清單、風險、CHG id。無 LLM、stdlib-only。"""
from __future__ import annotations
import re
from pathlib import Path

TASK_RE = re.compile(r"^- \[(?P<tick>[ xX])\] (?P<tid>T\d+)\. (?P<title>.+?)\s*$")
IFACE_RE = re.compile(r"^\s+-\s*interfaces:\s*\S")
TEST_RE = re.compile(r"^\s+-\s*test:\s*(?P<v>\S.*)$")
GC_RE = re.compile(r"^###\s*Global Constraints", re.MULTILINE)
AOP_RE = re.compile(r"^###\s*Acceptance operation", re.MULTILINE)
# 行為規格(CHG-20260803-02 T5):CHG 的使用者故事是驗收條件的第一順位來源(ai-sdlc v1.19),
# 但那一直只是散文。本節宣告對應的 .feature 路徑,讓故事變成可重跑的斷言。
BSPEC_RE = re.compile(r"^###\s*Behaviour spec", re.MULTILINE)
BSPEC_PATH_RE = re.compile(r"^-\s*feature:\s*(\S+)", re.MULTILINE)
# `- feature:` 路徑是 CHG 裡的**資料**,卻接在操作者的 `--gherkin-cmd` shell 模板後面執行
# (CHG-20260926-01 T4,X3)。任何能讓一份 CHG 落地的人都寫得到它,所以執行前先驗格式,
# 不合格的一個都不執行(代決 S3:驗格式而不是加引號——cmd.exe 仍會展開 %VAR%,
# 引號也擋不住以 `-` 開頭、被 behave 讀成選項的路徑)。規則:
#   相對路徑(不以 `/` 開頭)、以 `/` 分隔、每一段只含 `\w`(含非 ASCII 字母)與
#   `. _ - + @ , =`、不以 `-` 開頭、沒有 `..` 段、以 `.feature` 結尾。
# 反斜線、空白、`$`、反引號、`; | & < >`、引號、`%`、`!` 因此一律不合格。
SAFE_FEATURE_RE = re.compile(
    r"(?!-)(?!(?:[^/]*/)*\.\./)[\w.+@,=-]+(?:/[\w.+@,=-]+)*\.feature\Z")
# plan-check 判「計畫無效」的前瞻起點(代決 S6);verify 不看版本,一律不執行不合格的路徑。
# 語意同其他 `*_SINCE`:`AUTOPILOT_VER_RE.search` 取第一個版本,「v1.66.0 → v1.67.0」讀成 1.66。
FEATURE_PATH_SINCE = (1, 67)
FEATURE_PATH_RULE = ("相對路徑(不以 / 開頭)、以 / 分隔、每一段只含字母數字(非 ASCII 字母可以)"
                     "與 . _ - + @ , =、不以 - 開頭、沒有 .. 段、以 .feature 結尾;"
                     "反斜線、空白、$、反引號、; | & < >、引號、%、! 一律不合格")
# 前瞻適用起點:比照 ai-sdlc「重複性檢查」欄的先例,不追殺既有 CHG
AUTOPILOT_VER_RE = re.compile(r"ai-sdlc-autopilot\s*v(\d+)\.(\d+)", re.IGNORECASE)
BSPEC_SINCE = (1, 5)
DOCS_ONLY_RE = re.compile(r"Acceptance-operation:\s*n/?a|docs-only", re.IGNORECASE)
# 模板宣告(CHG-20260805-11)。原本靠 grep 從內容反推「這是不是 plan-format」——
# 只要用了 `- [ ] T1.` 這種寫法,全套欄位就全部變必填。反推有邊界,而邊界上的
# 每一份 CHG 都是一次擲骰子:CHG-20260707-03 誤送檢、CHG-20260804-12 漏網。
#
# 更實際的代價是它**把人推向全套模板**:文書變更也得寫「本輪零程式改動,故不宣告
# 行為規格」那種樣板話。文書最重要的是變更對、格式對,而樣板話兩樣都不買。
#
# 宣告了就照宣告的算;沒宣告則沿用反推並印警示(不追殺存量)。
TEMPLATE_RE = re.compile(r"^\s*[-*]?\s*Template\s*[:：]\s*(lite|classic|plan)\b",
                         re.MULTILINE | re.IGNORECASE)
LIGHT_TEMPLATES = ("lite", "classic")
# 設計圖(CHG-20260806-01)。確認閘要使用者確認的四樣東西——動機、影響範圍、
# 代決事項、風險分級——全是散文與表格。而使用者真正要攔截的「模組接錯邊了」
# 「資料流向不對」,恰好是散文最不擅長表達、也最容易讓人看過就點頭的。
# 一份看不懂的確認材料,拿到的不是確認,是默許。
# `##` 與 `###` 都認:設計圖是**確認閘**的產物(與「決策與取捨」同層),
# 不是計畫內部的子節。硬性規定層級只會製造一種除了格式以外什麼都沒防到的紅燈。
DIAGRAM_RE = re.compile(r"^#{2,3}\s*(?:設計圖|Design\s+diagrams?)\s*$",
                        re.MULTILINE | re.IGNORECASE)
FENCE_RE = re.compile(r"^```[^\n]*\n.*?^```", re.MULTILINE | re.DOTALL)
DIAGRAM_SKIP_RE = re.compile(
    r"^\s*[-*]?\s*Diagrams?\s*[:：]\s*(?:skipped|skip|略過|跳過)(?P<why>[^\n]*)$",
    re.MULTILINE | re.IGNORECASE)
DIAGRAM_SINCE = (1, 21)


def declared_template(text: str):
    m = TEMPLATE_RE.search(text)
    return m.group(1).lower() if m else None


def is_light(text: str) -> bool:
    """宣告為 lite/classic → 不套 plan-format 的必填欄位。"""
    return declared_template(text) in LIGHT_TEMPLATES
RISK_RE = re.compile(r"(風險分級|Risk)\s*[::]\s*[^\n]{0,40}?(高|high|中|medium|低|low)", re.IGNORECASE)
CHG_ID_RE = re.compile(r"CHG-\d{8}-\d+")
PERM_RE = re.compile(r"permanent-halt:\s*([\w-]+)")
AUTONOMY_HALT_RE = re.compile(r"^-\s*Autonomy\s*[::].*halt", re.MULTILINE | re.IGNORECASE)
VERDICT_RE = re.compile(
    r"\[task-review\]\s*(?:T\d+|branch)\s*\|\s*spec:\s*(pass|fail|cannot-verify)\s*\|\s*quality:\s*(pass|fail)",
    re.IGNORECASE)


def read_chg(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def chg_id_of(text: str, fallback: str = "?") -> str:
    m = CHG_ID_RE.search(text)
    return m.group(0) if m else fallback


def risk_of(text: str) -> str:
    m = RISK_RE.search(text)
    if not m:
        return "high"  # 查無=保守
    v = m.group(2).lower()
    return {"高": "high", "中": "medium", "低": "low"}.get(v, v)


# 施工者 brief 附的 task 條目(CHG-20260926-01 T5,#4)。c5af2cc 的 task dict 只帶 `has_iface` /
# `has_test` 兩個旗標,不帶那兩行的文字——施工者拿到的只有標題,而 tdd-loop 叫它從 `test:` 行寫測試。
# 上限只數非空白的子行(task 行本身不算);超過時只附前 80 行,其餘指回 CHG 的行號。
ENTRY_MAX_LINES = 80


def _entry_sub(lines, i) -> list:
    """task 行之後、屬於它的子行 [(行號, 行)];結尾的空白行不算。"""
    sub: list[tuple[int, str]] = []
    for j in range(i + 1, len(lines)):
        ln = str(lines[j])
        if ln.strip() and not ln[:1].isspace():
            break
        sub.append((j, ln))
    while sub and not sub[-1][1].strip():
        sub.pop()
    return sub


def task_entry(lines, i, chg_path=None) -> str:
    """第 i 行的 task 條目:task 行,加上它後面的子行。

    空白行與以空白開頭的行都算子行;遇到第一個非空白、也不縮排的行(下一個 task、標題、
    `**B. …**` 這種粗體標籤)就停,結尾的空白行丟掉。非空白的子行超過 ENTRY_MAX_LINES 時
    只附前 ENTRY_MAX_LINES 行,加上截斷標記、總行數與續讀的 CHG 行號。
    """
    if not isinstance(lines, list) or not 0 <= i < len(lines):
        return ""
    m = TASK_RE.match(str(lines[i]))
    tid = m.group("tid") if m else "task"
    sub = _entry_sub(lines, i)
    total = sum(1 for _, ln in sub if ln.strip())
    kept, count, cut_at = [str(lines[i])], 0, None
    for j, ln in sub:
        if ln.strip():
            count += 1
            if count > ENTRY_MAX_LINES:
                cut_at = j
                break
        kept.append(ln)
    if cut_at is not None:
        while len(kept) > 1 and not kept[-1].strip():
            kept.pop()
        where = str(chg_path) if chg_path else "這份 CHG"
        kept.append(f"…({tid} 的條目共 {total} 行,這裡只附前 {ENTRY_MAX_LINES} 行;"
                    f"其餘請讀 {where} 第 {cut_at + 1} 行起)")
    return "\n".join(kept)


def parse_tasks(text: str, chg_path=None):
    """回傳 (problems, tasks);task = {tid,title,ticked,has_iface,has_test,line_no,entry,entry_lines}

    `entry` 是施工者 brief 附的條目(task_entry);`entry_lines` 是非空白子行的總數,
    plan-check 以它對超過 ENTRY_MAX_LINES 的條目印警告(不擋)。`chg_path` 只用在截斷標記。
    """
    problems: list[str] = []
    tasks: list[dict] = []
    lines = text.splitlines()
    if is_light(text):
        # 宣告為輕量模板:不要求 Global Constraints、interfaces、test、編號連續。
        # 那些欄位服務的是「把 task 派給另一個 agent 去跑」——輕量變更沒有那個需求。
        return problems, tasks
    if not GC_RE.search(text):
        problems.append("缺「### Global Constraints」節——全域約束是 task 簡報自包含的前提")
    for i, line in enumerate(lines):
        m = TASK_RE.match(line)
        if not m:
            continue
        # 前瞻視窗必須在**下一個 task 開始時截斷**(CHG-20260803-01 T9)。
        # 原本固定取 5 行,會越界讀到下一個 task 的子行——缺 `interfaces:` 或 `test:` 的 task
        # 只要後面那個 task 有,就能借到它的行而通過 plan-check。即這道閘可以被無聲地繞過,
        # 而它是整個 autopilot 的第一道閘。
        block = []
        for l in lines[i + 1:i + 6]:
            if TASK_RE.match(l):
                break
            block.append(l)
        sub = [l for l in block if l.startswith((" ", "\t")) and l.strip().startswith("-")]
        entry = task_entry(lines, i, chg_path)
        tasks.append({
            "tid": m.group("tid"), "title": m.group("title"),
            "ticked": m.group("tick").lower() == "x", "line_no": i,
            "has_iface": any(IFACE_RE.match(l) for l in sub),
            "has_test": any(TEST_RE.match(l) for l in sub),
            "entry": entry,
            "entry_lines": sum(1 for _, l in _entry_sub(lines, i) if l.strip()),
        })
    if not tasks:
        problems.append("找不到任何 task(格式:- [ ] T1. <標題>)")
    for t in tasks:
        if not t["has_iface"]:
            problems.append(f"{t['tid']} 缺 interfaces: 行(consumes/produces)")
        if not t["has_test"]:
            problems.append(f"{t['tid']} 缺 test: 行(指令或可斷言條件)")
    nums = [int(str(t["tid"])[1:]) for t in tasks]
    if nums != list(range(1, len(nums) + 1)):
        problems.append(f"task 編號須 T1..T{len(nums)} 連續,實得 {[t['tid'] for t in tasks]}")
    return problems, tasks


def bspec_required(text: str) -> bool:
    """本 CHG 是否需要行為規格:程式類 + Skill ≥ v1.5.0(前瞻適用)。"""
    if is_light(text):
        return False        # 輕量模板不必宣告行為規格(CHG-20260805-11)
    if DOCS_ONLY_RE.search(text):
        return False
    m = AUTOPILOT_VER_RE.search(text)
    if m is None:
        return False
    # 明確早退而不是 `bool(m) and m.group(...)`:後者在執行期正確,但型別上
    # narrow 不掉,於是每個這樣寫的檔案都得欠一筆基線豁免(CHG-20260804-09)。
    return (int(m.group(1)), int(m.group(2))) >= BSPEC_SINCE


def section_body(text: str, head_re: "re.Pattern[str]") -> str:
    """取出某個 markdown 節的內文,到**同層或更淺**的下一個標題為止;查無回空字串。

    截斷點依標題自己的層級算,不是寫死 `###`——否則 `## 設計圖` 會一路吃到檔尾,
    把隔壁節的圍欄當成自己的圖。
    """
    m = head_re.search(text)
    if not m:
        return ""
    head = m.group(0)
    level = len(head) - len(head.lstrip("#"))
    seg = text[m.start():]
    nxt = re.search(rf"^#{{1,{level}}}\s", seg[level:], re.MULTILINE)
    if nxt:
        seg = seg[:nxt.start() + level]
    return seg


def bspec_paths(text: str) -> list[str]:
    return BSPEC_PATH_RE.findall(section_body(text, BSPEC_RE))


def unsafe_feature_paths(text: str) -> list[str]:
    """`### Behaviour spec` 宣告的路徑裡,不合 `SAFE_FEATURE_RE` 的那些(依宣告順序)。

    檢查的正是 verify 會交給 `--gherkin-cmd` 的那一份(`bspec_paths`),
    不多也不少:驗的與跑的是同一組字串,兩邊才不會分岔。
    """
    return [p for p in bspec_paths(text) if SAFE_FEATURE_RE.fullmatch(p) is None]


def diagram_required(text: str) -> bool:
    """本 CHG 是否必須附設計圖:plan 模板 + 風險中/高 + Skill ≥ v1.21(前瞻適用)。

    範圍刻意窄。低風險白名單(文案/註解/樣式/純文件/有測試的內部重構)免圖——
    記帳成本必須隨風險縮放,否則使用者整套繞過流程,這在 CHG-20260805-11 已實證過一次:
    過度模板讓 lite 用不出來,於是連純文件變更都被迫寫樣板話。
    """
    if is_light(text):
        return False        # lite / classic 一律免圖
    if risk_of(text) not in ("medium", "high"):
        return False
    m = AUTOPILOT_VER_RE.search(text)
    if m is None:
        return False        # 未宣告版本 → 行為與本閘上線前完全相同
    return (int(m.group(1)), int(m.group(2))) >= DIAGRAM_SINCE


def diagram_skipped(text: str) -> tuple[bool, str]:
    """回傳 (是否宣告跳過, 理由)。理由去掉破折號與冒號後為空 → 視同沒寫。"""
    m = DIAGRAM_SKIP_RE.search(text)
    if m is None:
        return False, ""
    return True, m.group("why").strip(" \t—–-:：·。,,")


def diagram_problems(text: str) -> list[str]:
    """設計圖節的機器判定。只驗「有沒有給東西看」,不驗圖畫得對不對。

    圖的**語意**由使用者在確認閘判斷——那正是本節存在的理由,不是 lint 的職責。
    驗 Mermaid 語法要外部 parser,而被測程式一律 stdlib-only。
    已知且刻意接受的洞:塞一個空的 ```mermaid 圍欄就能過閘;騙的是使用者自己,不是 CI。
    """
    if not diagram_required(text):
        return []
    skipped, why = diagram_skipped(text)
    if skipped and why:
        return []
    if skipped and not why:
        return ["`Diagrams: skipped` 沒有寫理由——空白簽名不算簽名,與沒宣告等價。"
                "請寫成 `Diagrams: skipped — <使用者為何決定不看圖>`"]
    body = section_body(text, DIAGRAM_RE)
    if not body:
        return ["缺「### 設計圖」節:中/高風險變更須在計畫定案前產出架構圖與本次變更流程圖"
                "供使用者檢視修正(Skill ≥ ai-sdlc-autopilot v1.21.0)——"
                "Mermaid 為主、ASCII 亦可;使用者決定不看時請標"
                "「Diagrams: skipped — <理由>」"]
    if not FENCE_RE.search(body):
        return ["「### 設計圖」節裡沒有任何圍欄區塊——散文不是圖。"
                "請放 ```mermaid(或 ```text 的 ASCII 圖)"]
    return []


def tick_task(chg_path: Path, tid: str) -> None:
    lines = read_chg(chg_path).splitlines(keepends=True)
    for i, line in enumerate(lines):
        m = TASK_RE.match(line.rstrip("\n"))
        if m and m.group("tid") == tid:
            lines[i] = line.replace("- [ ]", "- [x]", 1)
            break
    Path(chg_path).write_text("".join(lines), encoding="utf-8")


# ── 畫面草稿(CHG-20260901-01)─────────────────────────────────────────────
# 設計圖閘問的是「資料往哪流、模組怎麼接」。使用者要確認的還有一樣:**他會看到什麼**
# ——而那在整個 skill 裡原本沒有任何一個地方要求。最接近的互動驗證是**實作完成之後**
# 用 Playwright 點一次並存截圖:那證明按鈕會動,不證明按鈕長對。等到那時候才發現版面
# 不是使用者要的,重畫的是已經寫完的程式。
#
# 機器判不出「這一筆會不會生出畫面」,所以本閘**不猜**:它綁在 CHG 自己已經宣告的
# `### Interaction spec` 之 `kind: gui-*` 上(種類表見 assets/interaction_kinds.json)。
# 宣稱有使用面的人自己說了,那就要先給草稿——零誤報,而 CLI / 函式庫專案完全不被打擾。
# 猜的代價本 repo 記過兩次:誤報會逼人把整道閘關掉。
MOCKUP_RE = re.compile(r"^#{2,3}\s*(?:畫面草稿|Mockups?|Wireframes?)\s*$",
                       re.MULTILINE | re.IGNORECASE)
MOCKUP_SKIP_RE = re.compile(
    r"^\s*[-*]?\s*Mockups?\s*[:：]\s*(?:skipped|skip|略過|跳過)(?P<why>[^\n]*)$",
    re.MULTILINE | re.IGNORECASE)
ISPEC_RE = re.compile(r"^###\s*Interaction spec", re.MULTILINE)
ISPEC_KIND_RE = re.compile(r"^\s*[-*]\s*kind:\s*(\S+)", re.MULTILINE)
# markdown 圖片:線框常常是一張圖而不是一段 ASCII,兩種都算「交出東西了」
IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)\s]+\)")
GUI_KIND_PREFIX = "gui"
MOCKUP_SINCE = (1, 65)


def interaction_kinds(text: str) -> list[str]:
    """CHG 宣告了哪些互動種類(`### Interaction spec` 的 `kind:` 行,可多筆)。"""
    return [k.strip().lower() for k in ISPEC_KIND_RE.findall(section_body(text, ISPEC_RE))]


def has_gui_surface(text: str) -> bool:
    """這一筆**自己宣稱**有畫面嗎?判準是宣告,不是猜測。"""
    return any(k.startswith(GUI_KIND_PREFIX) for k in interaction_kinds(text))


def mockup_required(text: str) -> bool:
    """是否必須附畫面草稿:plan 模板 + 風險中/高 + 宣告了 gui-* 使用面 + Skill ≥ v1.65。"""
    if is_light(text):
        return False
    if risk_of(text) not in ("medium", "high"):
        return False
    if not has_gui_surface(text):
        return False
    m = AUTOPILOT_VER_RE.search(text)
    if m is None:
        return False        # 未宣告版本 → 行為與本閘上線前完全相同
    return (int(m.group(1)), int(m.group(2))) >= MOCKUP_SINCE


def mockup_skipped(text: str) -> tuple[bool, str]:
    """回傳 (是否宣告跳過, 理由)。語意與 `Diagrams: skipped` 逐字相同——理由空白視同沒寫。"""
    m = MOCKUP_SKIP_RE.search(text)
    if m is None:
        return False, ""
    return True, m.group("why").strip(" \t—–-:：·。,,")


def mockup_problems(text: str) -> list[str]:
    """畫面草稿節的機器判定。只驗「有沒有給東西看」,不驗草稿畫得對不對。

    圖的**語意**由使用者在確認閘判斷——那正是本節存在的理由,不是 lint 的職責
    (與 `diagram_problems` 同一條分工)。
    """
    if not mockup_required(text):
        return []
    skipped, why = mockup_skipped(text)
    if skipped and why:
        return []
    if skipped and not why:
        return ["`Mockup: skipped` 沒有寫理由——空白簽名不算簽名,與沒宣告等價。"
                "請寫成 `Mockup: skipped — <使用者為何決定不看草稿>`"]
    body = section_body(text, MOCKUP_RE)
    if not body:
        return ["缺「## 畫面草稿」節:本記錄宣告了 gui-* 使用面,中/高風險變更須在動碼前"
                "產出畫面草稿(ASCII 線框或圖片皆可)供使用者檢視修正"
                "(Skill ≥ ai-sdlc-autopilot v1.65.0)——"
                "使用者決定不看時請標「Mockup: skipped — <理由>」"]
    if not (FENCE_RE.search(body) or IMAGE_RE.search(body)):
        return ["「## 畫面草稿」節裡既沒有圍欄區塊也沒有圖片連結——散文不是草稿。"
                "請放 ```text 的 ASCII 線框,或 `![說明](路徑)` 的圖片"]
    return []


# ── 安全影響盤點(CHG-20260901-01)────────────────────────────────────────
# review-panel 的 security 席只載永遠停點清單,判決問題就一句「碰到永遠停點動作嗎?」
# ——那是**紅線攔截**,不是安全機制的整理與確認。「### 影響範圍」表問的是模組與相依,
# 沒有一格問「這一筆碰到哪一類不可逆的東西、範圍到哪」。於是高風險變更可以在
# **從來沒有人具名回答過安全問題**的狀態下走完全程。
#
# 分類**不新造**:唯一真相是 `lib/policy.PERMANENT_HALTS`(見 `security_kinds`)。
SECURITY_RE = re.compile(r"^#{2,3}\s*(?:安全影響|Security\s+impact)\s*$",
                         re.MULTILINE | re.IGNORECASE)
SECURITY_SKIP_RE = re.compile(
    r"^\s*[-*]?\s*Security-impact\s*[:：]\s*(?:skipped|skip|略過|跳過)(?P<why>[^\n]*)$",
    re.MULTILINE | re.IGNORECASE)
SECURITY_SINCE = (1, 65)


def security_kinds() -> tuple[str, ...]:
    """安全盤點要逐類回答的分類——**唯一真相是 `lib/policy.PERMANENT_HALTS`**。

    **呼叫時才 import**:`policy` 在模組載入時就 import 本檔(`AUTONOMY_HALT_RE`),
    頂層互 import 會循環。而在這裡複製一份常數,今天是綠的、半年後才會發現分岔
    ——`autonomy` 明文禁止 drive 層自建平行 taxonomy,複製一份就是自建一份。
    """
    from .policy import PERMANENT_HALTS
    return tuple(PERMANENT_HALTS)


def security_required(text: str) -> bool:
    """是否必須附安全影響盤點:plan 模板 + 風險**高** + Skill ≥ v1.65。

    只課在高風險上——記帳成本必須隨風險縮放,否則使用者整套繞過流程
    (CHG-20260805-11 已實證過一次)。
    """
    if is_light(text):
        return False
    if risk_of(text) != "high":
        return False
    m = AUTOPILOT_VER_RE.search(text)
    if m is None:
        return False        # 未宣告版本 → 行為與本閘上線前完全相同
    return (int(m.group(1)), int(m.group(2))) >= SECURITY_SINCE


def security_skipped(text: str) -> tuple[bool, str]:
    """回傳 (是否宣告跳過, 理由)。理由空白視同沒寫——與 `Diagrams: skipped` 同語意。"""
    m = SECURITY_SKIP_RE.search(text)
    if m is None:
        return False, ""
    return True, m.group("why").strip(" \t—–-:：·。,,")


def security_problems(text: str) -> list[str]:
    """安全影響節的機器判定:四類永遠停點**逐類具名**,漏的那幾類要指名道姓。

    只驗「每一類都被回答了」,不驗答案對不對——寫「不觸及」而其實觸及,騙的是
    確認閘上的使用者,不是 lint。而**沒有被問到**與**答了「不觸及」**是兩件事:
    前者沒有人簽過名,後者有。
    """
    if not security_required(text):
        return []
    skipped, why = security_skipped(text)
    if skipped and why:
        return []
    if skipped and not why:
        return ["`Security-impact: skipped` 沒有寫理由——空白簽名不算簽名,與沒宣告等價。"
                "請寫成 `Security-impact: skipped — <為何本筆不需要安全盤點>`"]
    kinds = security_kinds()
    body = section_body(text, SECURITY_RE)
    if not body:
        return ["缺「## 安全影響」節:高風險變更須逐類具名四類永遠停點"
                f"({', '.join(kinds)})的觸及與否與影響範圍"
                "(Skill ≥ ai-sdlc-autopilot v1.65.0)——"
                "不需要時請標「Security-impact: skipped — <理由>」"]
    low = body.lower()
    missing = [k for k in kinds if k.lower() not in low]
    if missing:
        return [f"「## 安全影響」節沒有具名這幾類永遠停點:{', '.join(missing)}——"
                "沒有被問到與答了「不觸及」是兩件事,後者有人簽過名。"
                "分類表見 lib/policy.PERMANENT_HALTS(不要另立一份)"]
    return []
