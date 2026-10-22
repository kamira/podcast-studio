#!/usr/bin/env python3
"""behave 步驟的反轉探針 —— 找出「不可能讓場景轉紅」的步驟斷言。stdlib-only。

`assertion_probe` 走 pytest 風格的 `checks.append((名稱, 條件))`,
而 behave 步驟裡的斷言是 `assert <條件>, <訊息>` ——**它照不到**
(CHG-20260812-04 當時實測「探 0 條」,那 49 條宣稱因此一直是 `pending`)。

## 兩段式,而第一段不能省(兩席)

> dry-run 是**靜態配對**(步驟文字 ↔ 步驟實作的 pattern),它證明的是
> 「某場景**含有**這條步驟」,不是「實跑會**執行到**這條斷言」。
> 三種漏法:場景在前面的 Given/When 就先紅、tag 把場景跳掉、
> 斷言在步驟內的分支裡沒走到。三種下 dry-run 都說命中,而反轉後
> 可能因為別的原因紅(誤讀成探到了)或根本沒執行(**誤讀成裝飾**)。(fable)

所以 **dry-run 只當選片器**(挑要跑哪一支 feature),**判定一律錨在實跑的可達性哨兵**:

  第一段 把斷言的條件換成 `_probe_reach()`(求值即 `SystemExit(97)`)
         → 跑那支 feature → **哨兵沒響 = 對照錯或沒走到 = 探針建置失敗**,
           絕不落進「裝飾」。這一段分得出「對照錯」與「真裝飾」,
           而「反轉後未見預期失敗即判失敗」單獨分不出。
  第二段 條件取反 → 那支 feature **必須**轉紅;沒紅才是**裝飾**。

一條步驟被多支 feature 共用時,**以「哨兵實際響過的那支」為準**,不是 dry-run 說的第一支。

用法:
  python3 behave_step_probe.py --steps-file <路徑> [--max N] [--json]
  python3 behave_step_probe.py --all [--max-per-file N] [--json]

## 四態,而第四態是兩席審完加的

`ok` 的原判準是「哨兵響過 + 取反後 `rc != 0`」,而 `rc != 0` 有三種**不是**
「這條斷言把場景擋紅了」的來路:

  · **逾時**(`_run_behave` 回 `-1`)——沒驗成,不是轉紅。
  · **基線本來就紅**——別的場景先掛,取反與否 rc 都非零。
    哨兵只證明「這行執行得到」;「轉紅」要對照綠基線才有意義。(fable)
  · **紅得不可歸因**——改寫把檔案弄壞、紅在別條斷言上,或**訊息裡抽不到字面錨**。
    「逐條保存反轉後的失敗位置,證明紅燈源自該 assert,而非其他錯誤」;
    「非字面訊息無法比對前綴,**不應推定成功**」。(codex)

三者都是「探不了」,而**不是**「探了沒探到」。前者是工具能力不足,後者暗示
對照、控制流或選片錯誤——混用會把工具限制誤診成執行缺口。所以第四態 `out-of-range`
獨立具名(跨行條件也歸這裡)。

退出碼:0 探過且乾淨 | 1 有裝飾 | 2 環境/參數錯誤 | 3 射程外/未證明
(三態沿用 `assertion_probe` 於 CHG-20260813-05 訂下的語意——
 探不到東西不是「沒有裝飾性斷言」。)
"""
from __future__ import annotations
import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# `BEHAVE_STEP_PROBE_REPO` 讓探針指向另一個專案根。存在的理由只有一個:
# **種一個已知答案的校準樣本**——「49 條全 ok、裝飾 0」這個結果本身分不出
# 「真的都會擋」與「判準太寬什麼都判 ok」(KN-001),而分得出來的唯一辦法是
# 拿一條**已知是裝飾**的斷言走完整條真實管線,確認它被叫出來。(兩席)
REPO = Path(os.environ.get("BEHAVE_STEP_PROBE_REPO")
            or Path(__file__).resolve().parents[3]).resolve()
STEPS_DIR = REPO / "features" / "steps"
FEATURES = REPO / "features"

PROBE_EXIT = 97
# 求值就拋 SystemExit(97) 的運算式。與 assertion_probe 同一個手法:
# 它證明的是「這一行**真的被執行到**」,而不是「它出現在某個場景裡」。
REACH = f"(_ for _ in ()).throw(SystemExit({PROBE_EXIT}))"

# **路徑錨在儀器自己,不走 REPO**:`BEHAVE_STEP_PROBE_REPO` 指向別的專案做校準時,
# 判準仍然是這支儀器自己的那一份(fable 席)。
CRITERIA = Path(__file__).resolve().parent.parent / "assets" / "step_probe_criteria.json"


class CriteriaError(Exception):
    """判準檔讀不到或不合格。**具名非零,不是「沒有阻斷步驟」**(KN-001)。"""


def load_criteria(path: Path) -> tuple[tuple[str, ...], str]:
    """讀共用判準,回 `(blocking_hints, steps_glob)`。

    `behave_step_probe` 與 `build_claim_manifest` **共用這一段**(待補項 #55 + #56)。
    兩支原本各自維護一份字面 tuple 與一個字面 glob,內容逐字相同而
    **沒有任何機制維持它**。判準是資料,兩側只負責讀。

    > 兩側載入器不能各自手寫不同驗證規則;至少測試同一組非法 fixture
    > 對兩側結果一致。(codex 席)

    所以驗證也在這裡,不在呼叫端——各自手寫就是把剛消滅的分岔換一個地方長出來。

    讀不到、不是 object、欄位空或型別不對 → 一律 `CriteriaError`。
    **空判準與「沒有宣稱」在退出碼上一樣**,而那正是這份判準要防的東西。
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise CriteriaError(f"讀不到判準檔 {path}({e})"
                            f"——**那不是「沒有阻斷步驟」**") from e
    except ValueError as e:
        raise CriteriaError(f"判準檔 {path} 不是合法 JSON({e})") from e
    if not isinstance(raw, dict):
        raise CriteriaError(f"判準檔 {path} 的頂層不是 object")
    hints = raw.get("blocking_hints")
    if not isinstance(hints, list) or not hints:
        raise CriteriaError(f"判準檔 {path} 的 blocking_hints 缺席或是空的"
                            f"——**空的詞表會讓每一條步驟都判成不阻斷**")
    if not all(isinstance(h, str) and h.strip() for h in hints):
        raise CriteriaError(f"判準檔 {path} 的 blocking_hints 有非字串或空字串的項")
    glob = raw.get("steps_glob")
    if not isinstance(glob, str) or not glob.strip():
        raise CriteriaError(f"判準檔 {path} 的 steps_glob 缺席或是空的"
                            f"——**空的搜尋樣式掃不到任何檔,而那不是「沒有步驟檔」**")
    return tuple(hints), glob


# 只探帶阻斷語意的 `assert`——判準與清冊**共用同一份資料檔**(待補項 #55 + #56)。
# 兩支原本各自維護一份字面 tuple,內容逐字相同而沒有任何機制維持它。
BLOCKING, STEPS_GLOB = load_criteria(CRITERIA)


def _then_texts(fn: ast.FunctionDef) -> list[str]:
    """`@then('…')` 的步驟文字。**只認字面字串**——`ast.Constant.value` 的型別是
    一個大聯集,收窄成 `str` 的副作用是把非字串的裝飾器參數擋在射程外,
    而那本來就不是步驟文字。"""
    out = []
    for d in fn.decorator_list:
        if not (isinstance(d, ast.Call) and getattr(d.func, "id", "") == "then"
                and d.args and isinstance(d.args[0], ast.Constant)):
            continue
        v = d.args[0].value
        if isinstance(v, str):
            out.append(v)
    return out


def step_texts(src: str) -> dict[str, list[str]]:
    """{函式名: [@then 的步驟文字]} —— 用 AST,不用 regex。"""
    out: dict[str, list[str]] = {}
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        texts = _then_texts(fn)
        if texts:
            out[fn.name] = texts
    return out


def claim_targets(src: str) -> dict[str, list[tuple[int, int, int]]]:
    """{步驟文字: [該步驟函式內可探的 assert 條件位置]}。

    **單位必須是「步驟定義」,不是「assert」** —— 清冊抽的是前者
    (`behave::<檔名>::<步驟文字>`),判準是**函式體**含阻斷語意。
    第一版按 assert 抽,於是 `change_scope_steps.py` 一邊算 2 條、一邊算 0 條
    ——**探針的射程與清冊的射程分岔,而分岔沒有東西看著**。
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {}
    out: dict[str, list[tuple[int, int, int]]] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        texts = _then_texts(fn)
        if not texts:
            continue
        body = ast.get_source_segment(src, fn) or ""
        if not any(h in body for h in BLOCKING):
            continue          # 與清冊同一組詞、同一個判準
        sites = []
        for node in ast.walk(fn):
            if not isinstance(node, ast.Assert):
                continue
            t = node.test
            if t.lineno != t.end_lineno:
                # 跨行條件先跳過:改寫它要動多行縮排,而**改壞的探針會被讀成
                # 「未執行」**(assertion_probe 踩過同一個坑)。
                continue
            if t.end_col_offset is None:
                continue  # diffcov-exempt: defensive — `end_col_offset` 對 `ast.parse` 產出的樹永遠是 int,None 只出現在手工組出來的 AST;而**少了這道判斷 mypy 會紅**,且真的發生時會切錯位元組、把檔案改壞,而改壞的探針會被讀成「未執行」 [signoff: codex+fable @ CHG-20260813-06]
            sites.append((t.lineno, t.col_offset, t.end_col_offset))
        for txt in texts:
            out[txt] = sites
    return out


def dry_run_features(step_file: Path, timeout: int = 120) -> list[Path]:
    """**只當選片器**:哪些 feature 的場景用到這支步驟檔的步驟。

    用 behave 自己的 dry-run 反查,不維護人工對照——手抄的對照表不會自己跟上,
    而漏掉的那個不會抗議(待補項 #40 那一族)。
    但它的結果**不判生死**:判生死的是實跑的可達性哨兵。
    """
    texts = [t for ts in step_texts(_read(step_file)).values()
             for t in ts]
    if not texts:
        return []
    hits = []
    for f in sorted(FEATURES.glob("*.feature")):
        body = f.read_text(encoding="utf-8")
        # 步驟文字帶 `{param}` 佔位符時,比對它的字面前綴就夠了——
        # 選片器寧可多選(多跑一支 feature 只是慢),不可少選(少選會誤判)。
        if any(re.split(r"\{", t)[0].strip() in body for t in texts):
            hits.append(f)
    return hits


def _run_behave(feature: Path, timeout: int) -> tuple[int, str]:
    try:
        p = subprocess.run([sys.executable, "-m", "behave", str(feature), "--no-capture"],
                           cwd=str(REPO), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"


def _read(p: Path) -> str:
    """讀原始位元組再解碼——**不讓 Python 幫忙翻譯行尾**。

    `read_text` / `write_text` 會把 CRLF 正規化成 LF,於是探針**在每一個碰過的檔案上
    留下腳印**:`git diff` 看不到(diff 會正規化),而 `git status` 說它改了。
    實測 47 檔掃完之後有 5 個檔停在「已修改」——那正是 `check_probe_leftovers.py`
    存在的理由,而這一次是我自己造的。
    """
    return p.read_bytes().decode("utf-8")


def _write(p: Path, s: str) -> None:
    p.write_bytes(s.encode("utf-8"))


def _span(src: str, site: tuple[int, int, int]) -> tuple[bytes, int, int]:
    """(原始位元組, 起, 迄)。**以位元組計算**——`ast` 的 `col_offset` 是 UTF-8
    位元組偏移,不是字元索引。

    這條教訓**已經寫在 `assertion_probe.negate_at` 的 docstring 裡**
    ——「本 repo 的斷言名稱幾乎都是中文,用字元索引切會偏掉好幾格」——
    而我抄它的設計時照樣寫了字元切法,於是 `build_loop_steps.py` 被改成
    `assert (…SystemExit(97))輸出的回饋不得像是通過"`:訊息的 `, "` 被吃掉,
    檔案編不過 → behave 起不來 → 哨兵不響 → **判成「未執行」**。
    一條好斷言就這樣被歸進未涵蓋(KN-005:靠記性維持的規則會重複違反)。
    """
    ln, c0, c1 = site
    blines = [x.encode("utf-8") for x in src.splitlines(keepends=True)]
    base = sum(len(x) for x in blines[:ln - 1])
    return src.encode("utf-8"), base + c0, base + c1


def _patched(src: str, site: tuple[int, int, int], repl: str) -> str:
    raw, s, e = _span(src, site)
    return (raw[:s] + repl.encode("utf-8") + raw[e:]).decode("utf-8")


def _negated(src: str, site: tuple[int, int, int]) -> str:
    raw, s, e = _span(src, site)
    return (raw[:s] + b"not (" + raw[s:e] + b")" + raw[e:]).decode("utf-8")


ANCHOR_MIN = 3


def assert_anchor(src: str, site: tuple[int, int, int]) -> str | None:
    """該 `assert` 訊息裡**可比對的字面錨**;抽不到回 `None`。

    本 repo 的斷言訊息絕大多數是 f-string(`f"判成 {got},應為 {want}"`),
    而 behave 印出來的是**格式化之後**的字串——所以能比對的只有
    **第一個佔位符之前的字面前綴**。純字面字串則整串都可以當錨。

    抽不到錨(訊息是 `dict[k]`、是 BinOp、根本沒有訊息、或前綴短到不具鑑別力)
    時回 `None`,而呼叫端會把它判成**探不了**——這是 codex 訂下的方向:

    > 僅有 `AssertionError` 類型不足以證明由該目標 assert 造成;
    > 非字面訊息無法比對前綴,**不應推定成功**。

    第一版寫成「抽不到就推定歸因成立」,而**同一段註解裡寫著相反的理由**
    ——「把一條紅在別處的斷言算成 ok 才是說謊」。KN-005 的又一次:
    寫下來的規則擋不住下一行程式碼。
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assert) and node.test.lineno == site[0]
                and node.test.col_offset == site[1]):
            continue
        m = node.msg
        lead = ""
        if isinstance(m, ast.Constant) and isinstance(m.value, str):
            lead = m.value
        elif isinstance(m, ast.JoinedStr):
            for v in m.values:            # 只取第一個佔位符之前的字面段
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    lead += v.value
                else:
                    break
        lead = re.split(r"[{%]", lead)[0].strip()
        return lead if len(lead) >= ANCHOR_MIN else None
    return None


def _other_assert_messages(src: str, site) -> list[str]:
    """**同一支步驟檔裡其他 assert 的字面訊息段**(待補項 #58 的靜態通道)。

    歸因是 `anchor in out` 的**子字串**關係,不是等值——所以錨不必與別條完全同名,
    只要**被別條的訊息包住**就分不出是誰紅的。實測 5 個位置全是同檔撞的
    (例:錨 `放行了:` ⊂ 同檔另一條的 `對帳放行了:`)。

    **只做同檔,不做跨檔**:跨檔同窗撞訊息今天實測為空,為它建跨檔步驟配對的機器
    是擴張。這條邊界是明示的,不是遺漏(兩席)。
    """
    out: list[str] = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        if (node.test.lineno, node.test.col_offset) == (site[0], site[1]):
            continue                      # 自己不算撞自己
        m = node.msg
        if isinstance(m, ast.Constant) and isinstance(m.value, str):
            out.append(m.value)
        elif isinstance(m, ast.JoinedStr):
            lead = ""
            for v in m.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    lead += v.value
                else:
                    lead += "\x00"        # 佔位符:格式化後的值不可預測,當作斷點
            out.append(lead)
    return out


def anchor_discriminating(anchor: str, baseline_out: str, src: str, site) -> str | None:
    """錨在它的歸因窗口內**分得出是誰紅的**嗎?分不出就回具名理由。

    待補項 #58:`ANCHOR_MIN = 3` 是**長度**的啟發式,而真正的判準是**鑑別力**。
    一個夠長卻在窗口裡到處都是的錨,`anchor in out` 對它恆真——
    歸因檢查退化成只剩「有沒有 Assertion Failed」,而那正是本探針要消滅的東西。

    兩個通道,一動一靜:

    · **C2(動態,零近似)**:錨已經出現在**這支 feature 的基線綠輸出**裡。
      behave 把步驟行與場景標題印進輸出,所以錨若與步驟文字重疊就恆真。
      基線綠那一輪本來就跑過,輸出留著即可——**零額外執行**。
      靜態猜輸出會猜錯:實測 `退出碼` 在某 feature 只出現在 `#` 註解裡,
      而**註解不進輸出**,綠跑 grep 到 0 次。所以這一格必須動態量。
    · **C1(靜態)**:錨被**同檔另一條 assert 的字面訊息**包住。
      這一格量不到動態輸出(那條要紅才會印),所以用原始碼近似,**只做同檔**。

    分不出時呼叫端落 `out-of-range`——**不推定成功**,與抽不到錨同一條路。
    """
    if baseline_out and anchor in baseline_out:
        return (f"錨 {anchor!r} 在**基線綠輸出**裡就已經出現"
                f"——`anchor in out` 對它恆真,歸因退化成只剩「有沒有斷言失敗」")
    for msg in _other_assert_messages(src, site):
        if anchor in msg:
            return (f"錨 {anchor!r} 被**同檔另一條斷言的訊息**包住"
                    f"——同窗兩條都紅時分不出是誰")
    return None


def _attributable(out: str, anchor: str | None) -> bool:
    """這次轉紅,歸得到這條斷言頭上嗎?

    兩個條件都要:輸出裡真的有斷言失敗,**而且**該斷言的字面錨出現在輸出裡。
    抽不到錨就回假——不確定一律落 `out-of-range`,不推定成功。
    """
    if "Assertion Failed" not in out and "AssertionError" not in out:
        return False
    return anchor is not None and anchor != "" and anchor in out


def probe_one(step_file: Path, site, features: list[Path], timeout: int = 300,
              green: dict[str, str] | None = None) -> tuple[str, str | None]:
    """回 (判定, 哨兵響過的 feature 名)。判定 ∈ ok / decorative / not-run / out-of-range。"""
    src = _read(step_file)
    orig = src
    reached_in = None
    if green is not None:
        # **基線綠檢查**:本來就紅的 feature,取反與否 rc 都非零。
        features = [f for f in features if f.name in green]
        if not features:
            return "out-of-range", None
    try:
        _write(step_file, _patched(src, site, REACH))
        for f in features:
            rc, out = _run_behave(f, timeout)
            # 哨兵響 = 這一行真的被執行到。**behave 讓 SystemExit 直接傳到行程退出碼**
            # ——第一版只找輸出裡的 `SystemExit` 字樣,而那串字根本不會出現,
            # 於是每一條都被判成「未執行」。**探針有套用,是偵測判準看錯了地方**
            # (KN-008 第 0 問的鏡像:那一問問變異有沒有套用,這裡是讀數取錯來源)。
            # 認退出碼為主、輸出痕跡為輔(未來若 behave 改成攔截 SystemExit 仍抓得到)。
            if rc == PROBE_EXIT or f"SystemExit: {PROBE_EXIT}" in out:
                reached_in = f
                break
        if reached_in is None:
            # **對照錯或沒走到,而不是裝飾。** 這一格若讀成裝飾,
            # 一條好斷言會被指控成裝飾品,而人會去改一條本來就對的斷言。
            return "not-run", None
        _write(step_file, _negated(src, site))
        rc, out = _run_behave(reached_in, timeout)
        if rc == 0:
            return "decorative", reached_in.name
        if rc == -1:
            # 逾時**不是轉紅**。舊版寫 `rc != 0` 就算 ok,於是「沒驗成」被記成「驗過了」。
            return "out-of-range", reached_in.name
        anchor = assert_anchor(src, site)
        if not _attributable(out, anchor):
            # 紅了,而歸因不到這條斷言——改寫把檔案弄壞、或紅在別條上。
            return "out-of-range", reached_in.name
        # **錨要分得出是誰紅的**(待補項 #58):長度只是便宜的前置下限,
        # 真正的判準是鑑別力。分不出時與抽不到錨同一條路——不推定成功。
        if green is not None and anchor is not None:
            why = anchor_discriminating(anchor, green.get(reached_in.name, ""), src, site)
            if why:
                return "out-of-range", reached_in.name
        return "ok", reached_in.name
    finally:
        _write(step_file, orig)


def baseline_green(features: list[Path], timeout: int = 300) -> dict[str, str]:
    """先跑一次**未改寫**的 feature,回 {基線綠的 feature 名: 那一輪的輸出}。

    沒有這一步,「取反後 rc != 0」證明不了任何事:本來就紅的 feature,
    取反與否都非零,而那會被記成 ok(fable 指出的第二個寬鬆點)。

    **輸出要留下來**(待補項 #58):它是錨的鑑別力的**零近似基準**——
    一個在基線綠輸出裡就已經出現的錨,`anchor in out` 對它恆真,
    歸因檢查退化成只剩「有沒有 Assertion Failed」。舊版把這份輸出丟掉,
    於是唯一能免費拿到的反事實基準被扔了。
    """
    out = {}
    for f in features:
        rc, txt = _run_behave(f, timeout)
        if rc == 0:
            out[f.name] = txt
    return out


def probe_file(step_file: Path, max_sites: int = 0) -> dict:
    """單位是**步驟定義**;一個步驟只要有一條 assert 探成 ok,它就算被證明會擋。"""
    src = _read(step_file)
    targets = claim_targets(src)
    if max_sites:
        targets = dict(list(targets.items())[:max_sites])
    feats = dry_run_features(step_file)
    green = baseline_green(feats) if targets else {}
    ok, decorative, not_run, out_of_range = [], [], [], []
    for text, sites in targets.items():
        if not sites:
            # 有阻斷語意卻抽不到單行條件(全是跨行)——**射程外,不是裝飾**,
            # 也**不是**「探了沒探到」:它根本沒被探(兩席同時要求分開)。
            out_of_range.append(text)
            continue
        verdicts = [probe_one(step_file, s, feats, green=green)[0] for s in sites]
        if "ok" in verdicts:
            ok.append(text)
        elif "decorative" in verdicts:
            decorative.append(text)
        elif "not-run" in verdicts:
            not_run.append(text)
        else:
            out_of_range.append(text)
    return {"file": step_file.name, "sites": len(targets), "ok": len(ok),
            "ok_steps": ok, "decorative": decorative, "not_run": not_run,
            "out_of_range": out_of_range, "baseline_green": sorted(green),
            "features_considered": [f.name for f in feats]}


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps-file")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv[1:])
    if not a.steps_file and not a.all:
        print("需要 --steps-file 或 --all")
        return 2
    files = ([Path(a.steps_file)] if a.steps_file
             else sorted(STEPS_DIR.glob(STEPS_GLOB)))
    reports = [probe_file(f, a.max) for f in files if f.is_file()]

    bad = sum(len(r["decorative"]) for r in reports)
    sites = sum(r["sites"] for r in reports)
    oor = sum(len(r["out_of_range"]) for r in reports)
    ran = sum(r["ok"] for r in reports) + bad
    scope_fail = None
    if not files or not reports:
        scope_fail = "一個步驟檔都沒掃到——搜尋壞了與「沒有步驟」在退出碼上一樣(KN-001)"
    elif sites == 0:
        scope_fail = "帶阻斷語意的 assert 共 0 條——射程外或抽取失敗,不是通過"
    elif ran == 0:
        scope_fail = f"{sites} 個位置**全部未執行**——探了但一條都沒真探到"

    if a.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    else:
        for r in reports:
            print(f"── {r['file']}:探 {r['sites']} 條 / 正常 {r['ok']}"
                  f" / **裝飾 {len(r['decorative'])}** / 未執行 {len(r['not_run'])}"
                  f" / 射程外 {len(r['out_of_range'])}")
            for d in r["decorative"]:
                print(f"     裝飾(場景沒因為它轉紅):{d}")
            for d in r["not_run"][:3]:
                print(f"     未執行(哨兵沒響=對照錯或沒走到,**不是裝飾**):{d}")
            for d in r["out_of_range"][:3]:
                print(f"     射程外(探不了,**不是探了沒探到**):{d}")
        if bad:
            print(f"\n❌ {bad} 條步驟斷言反轉後場景照樣綠——它們沒在擋任何東西。")
        elif scope_fail:
            print(f"\n⚠️ **射程外/未證明**:{scope_fail}")
        else:
            print(f"\n✅ 探過 {sites} 條(實際反轉 {ran} 條,射程外 {oor} 條),"
                  f"無裝飾性步驟斷言。")
    if bad:
        return 1
    return 3 if scope_fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))  # diffcov-exempt: defensive — `__main__` 入口,覆蓋率是以匯入方式跑的,永遠走不到;判定邏輯全在 `main()` 內且已逐條受測 [signoff: codex+fable @ CHG-20260813-06]
