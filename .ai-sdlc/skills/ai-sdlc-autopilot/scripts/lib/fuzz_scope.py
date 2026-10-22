#!/usr/bin/env python3
"""誰在 fuzz 的射程內——由**證據**回答,不由記性也不由函式名回答。

## 兩次教訓,兩層設計

**第一次(CHG-20260808-01)**:待補項 #23 記的根因是「fuzz 的目標清單裡沒有它」。
查下去不是漏了一條——引擎的目標介面是 `fn(text)`,而 `_load_vocab(kdir: Path)`
吃的是路徑,**整整一類讀檔的解析器在目標格式裡表達不出來**。於是有了這份掃描。

**第二次(CHG-20260808-03,本次)**:那份掃描找出 69 個候選,而我用**函式名**
批次指派處置、寫四段泛用理由,一次蓋完。lint 只驗「值合不合法」——
**沒有任何一步問過「這個處置與這個函式的實際行為相符嗎」**。
REVIEW 逐條查證,39 條非目標處置裡**至少 7 條是錯的**,其中 6 條落在同一格。

所以本檔現在做兩件事:掃描時**同時擷取證據**,處置由證據**推導**;
清冊上的宣告與推導不一致就擋下,並指出是哪一項證據矛盾。
理由可以寫得漂亮,證據不行。

## 判準(找出候選)

模組層級函式,其函式體內把外部原始資料轉成結構:`json.loads` / `ast.parse` /
`*.read_text` / `re.{search,match,finditer,findall,split,fullmatch}` /
**模組層級 `re.compile(...)` 綁定的名字**上的同一組方法。

最後一條是實測補上的:第一版只認 `re.match`,而本 repo 主流寫法是
`TASK_RE = re.compile(...)` 之後 `TASK_RE.match(line)`——**11 個已經在目標清單裡的
解析器掃不出來**。判準比現實弱時,棘輪會漏掉新的那一個而且不會抗議。

## 推導(決定處置)—— 順序是判定的一部分

    1. 在 TARGETS 內                              → targeted
    2. 只有 read_text,沒有 loads/regex/ast        → reads-only
    3. 讀取路徑全部指向 repo 自己的常數            → repo-asset
    4. 其餘                                       → registered-gap(須帶待補項編號)

**第 2 條排在第 3 條之前是刻意的,但它目前只是一個定義,不是一個被觀察到的差別。**
CHG-20260808-03 的設計說明原本寫「否則 `plan.read_chg` 會被誤推成 gap」——
**那是錯的,而且實測推翻了它**:對調兩條的順序,69 條裡**沒有任何一條改變答案**,
因為兩者在真實資料上互斥(`repo_paths` 要求 `reads` 非空且解析得到 repo 常數,
而那些函式都有解析呼叫)。

保留這個順序的理由因此換成一個**前瞻**的:一個「讀 repo 自己的檔、但只讀不解析」
的函式將來會出現,而那時它該是 `reads-only`(它根本不解析,無從崩潰),
不是 `repo-asset`。`test_api_fuzz` 用**合成的 Evidence** 釘住這個定義——
真實資料上沒有的案例,就用合成的證明它有定義,而不是假裝它被觀察到了。

**判不出來一律往 `registered-gap` 倒**:往多驗那邊,不往少驗那邊(KN-004)。

## 拿掉的兩個類別,以及為什麼

`thin-wrapper`(「真正的解析在別的函式」)**與入選條件直接衝突**——函式會進清冊
就是因為它自己在解析。一個永遠搪塞得過去的類別就會被拿來搪塞:我 7 個錯有 6 個在那格。

`self-written`(「讀本程式自己剛寫的檔」)真正想說的是「只讀不解析」,
而**那個機器判得出來**,不必靠人說。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import NamedTuple

INVENTORY = Path(__file__).resolve().parent.parent.parent / "assets" / "fuzz_inventory.json"

RE_METHODS = frozenset({"search", "match", "finditer", "findall", "split", "fullmatch"})
# `str` **沒有**的那幾個。接收者型別未知(例如迴圈變數)時,只有這幾個方法
# 才足以判定「這是 regex」——`split` 兩邊都有,所以它不在這裡。
STR_FREE_METHODS = RE_METHODS - {"split"}
TARGETED = "targeted"
READS_ONLY = "reads-only"
REPO_ASSET = "repo-asset"
REGISTERED_GAP = "registered-gap"
VALID_DISPOSITIONS = frozenset({TARGETED, READS_ONLY, REPO_ASSET, REGISTERED_GAP})


class Evidence(NamedTuple):
    """一個函式**實際做了什麼**——推導的唯一依據。"""
    parses: tuple[str, ...]       # json.loads / ast.parse / regex(空 = 只讀不解析)
    reads: tuple[str, ...]        # `X.read_text()` 的接收者運算式原文
    repo_paths: bool              # 所有讀取路徑都指向 repo 自己的常數嗎

    @property
    def interprets(self) -> bool:
        return bool(self.parses)


def _repo_path_consts(tree: ast.Module) -> set[str]:
    """模組層級、由 `__file__` 推出來的路徑常數 —— 那些指的是 repo 自己的檔案。"""
    out = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        src = ast.unparse(node.value)
        if "__file__" in src:
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
    return out


def _compiled_names(tree: ast.Module) -> set[str]:
    out = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        val = node.value
        if (isinstance(val, ast.Call) and isinstance(val.func, ast.Attribute)
                and val.func.attr == "compile"
                and isinstance(val.func.value, ast.Name) and val.func.value.id == "re"):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.add(t.id)
    return out


def _defaulted_params(fn: ast.AST) -> set[str]:
    """有預設值的參數。`load_kinds(path=None)` 這種慣例:預設走 repo 常數,
    傳入才走別的地方——生產路徑讀的仍是 repo 自己的檔。"""
    args = getattr(fn, "args", None)
    if args is None:
        return set()
    names = [a.arg for a in args.args]
    n_def = len(args.defaults)
    out = set(names[len(names) - n_def:]) if n_def else set()
    out |= {a.arg for a in args.kwonlyargs}
    return out


def _local_assigns(fn: ast.AST) -> dict[str, ast.AST]:
    """函式內 `name = <expr>` 的對應。

    **沒有這一層,`repo-asset` 會是一個永遠不會發生的類別**——本 repo 的慣例是
    `p = Path(path) if path else KINDS_ASSET` 之後才 `p.read_text(...)`,
    接收者是區域變數 `p`,而不是那個常數本身。第一版判準沒追賦值,
    於是 69 條裡推導出 **0 條 repo-asset**;而一個從不發生的分類,
    與這個分類不存在分不出來(KN-001)。
    """
    out: dict[str, ast.AST] = {}
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = n.value
    return out


def _leaf_names(expr: ast.AST, assigns: dict[str, ast.AST], depth: int = 4) -> set[str]:
    """把運算式裡的名字展開到葉節點(沿區域賦值追,深度有限以防環)。"""
    names = {x.id for x in ast.walk(expr) if isinstance(x, ast.Name)}
    if depth <= 0:
        return names
    out = set()
    for nm in names:
        if nm in assigns:
            out |= _leaf_names(assigns[nm], {k: v for k, v in assigns.items() if k != nm},
                               depth - 1)
        else:
            out.add(nm)
    return out


def evidence_of(fn: ast.AST, compiled: set[str], repo_consts: set[str]) -> Evidence:
    parses: set[str] = set()
    reads: list[str] = []
    defaulted = _defaulted_params(fn)
    required_params = {a.arg for a in getattr(fn, "args").args} - defaulted
    assigns = _local_assigns(fn)
    # 迭代出來的 pattern 也算編譯好的 regex:`for name, pat in SECRET_PATTERNS:`
    # 之後 `pat.search(...)`——CHG-20260808-03 之前的判準只認模組層級的名字,於是
    # `check_secrets` 被推成「只讀不解析」,而它明明在跑 regex。
    #
    # **但那一版把 `split` 也放進來,於是 4 個命中裡 3 個是誤報**(CHG-20260808-04):
    # `kind.split(':', 1)` / `token.split('::')` / `p.split(':', 1)` 全是 `str.split`。
    # 分界線不在「是不是迴圈變數」,在**哪些方法 `str` 也有**:
    # `str` 沒有 search / match / finditer / findall / fullmatch,**只有 `split` 兩邊都有**。
    # 所以 `split` 只在接收者是 `re` 或模組層級編譯名時才算。
    #
    # 代價寫明:編譯好的 pattern 存在迴圈變數裡**且只用 `.split()`** 會漏掉——
    # 罕見,且那種 pattern 通常同時也會被 `search`/`match` 用到。
    # 判準過鬆與過緊一樣壞:誤報會逼人寫豁免,而那就回到蓋章。
    loop_vars: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.For):
            loop_vars |= {x.id for x in ast.walk(n.target) if isinstance(x, ast.Name)}
    repo_paths = True
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute):
            continue
        f = n.func
        if f.attr == "loads" and isinstance(f.value, ast.Name) and f.value.id == "json":
            parses.add("json.loads")
        elif f.attr == "parse" and isinstance(f.value, ast.Name) and f.value.id == "ast":
            parses.add("ast.parse")
        elif f.attr in RE_METHODS and isinstance(f.value, ast.Name):
            known_pattern = f.value.id == "re" or f.value.id in compiled
            # `split` 是 `str` 也有的那一個,所以迴圈變數上的 `.split()` 不算。
            if known_pattern or (f.value.id in loop_vars and f.attr in STR_FREE_METHODS):
                parses.add("regex")
        elif f.attr == "read_text":
            reads.append(ast.unparse(f.value))
            # 接收者展開到葉節點後,若出現**沒有預設值的參數** → 路徑由呼叫端決定,
            # 機器不知道那是誰的檔案 → 保守推成非 repo 路徑(往多驗那邊倒)。
            names = _leaf_names(f.value, assigns)
            if names & required_params or not (names & repo_consts):
                repo_paths = False
    if not reads:
        repo_paths = False        # 沒有讀檔就談不上「讀的是 repo 自己的檔」
    return Evidence(tuple(sorted(parses)), tuple(reads), repo_paths)


def scan(modules) -> dict[str, Evidence]:
    """回 {「模組.函式」: Evidence}。模組名用檔名 stem;`ai_sdlc_mcp` 縮寫為 `mcp`。"""
    found: dict[str, Evidence] = {}
    for path in modules:
        p = Path(path)
        if not p.is_file():
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue          # 讀不到/解析不了由別的閘負責,這裡不吞成「沒有」
        mod = "mcp" if p.name == "ai_sdlc_mcp.py" else p.stem
        compiled = _compiled_names(tree)
        repo_consts = _repo_path_consts(tree)
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            ev = evidence_of(node, compiled, repo_consts)
            if ev.parses or ev.reads:
                found[f"{mod}.{node.name}"] = ev
    return found


def derive(qualname: str, ev: Evidence, targeted: set[str]) -> str:
    """由證據推導處置。**順序即判定**——見檔頭。"""
    if qualname in targeted:
        return TARGETED
    if not ev.interprets:
        return READS_ONLY
    if ev.repo_paths:
        return REPO_ASSET
    return REGISTERED_GAP


def load_inventory(path=None) -> dict:
    p = Path(path) if path else INVENTORY
    try:
        return json.loads(p.read_text(encoding="utf-8-sig")).get("entries", {})
    except (OSError, ValueError):
        # 讀不到清冊**不得放行**:那會讓「刪掉清冊」變成關掉這道閘的方法。
        return {"__unreadable__": {}}


def reconcile(found: dict[str, Evidence], inventory: dict, targeted: set[str]) -> list[str]:
    """回問題清單(空 = 通過)。

    四道判斷,**前三道是 CHG-20260808-01 就有的,一條都沒拿掉**;
    第四道(推導 == 宣告)是本次新增的,也是唯一會去看「處置對不對」的那一道。
    """
    if "__unreadable__" in inventory:
        return ["fuzz 清冊讀不到——讀不到與「沒有新解析器」在退出碼上一樣,故擋下"]
    problems = []
    new = sorted(set(found) - set(inventory))
    if new:
        problems.append(
            "出現**不在清冊內**的解析器,擋下:\n  · " + "\n  · ".join(new)
            + f"\n  請在 assets/fuzz_inventory.json 具名處置"
              f"(可用:{', '.join(sorted(VALID_DISPOSITIONS))})。")
    gone = sorted(set(inventory) - set(found))
    if gone:
        problems.append(
            "清冊列著但掃不到(已改名或刪除),請一併移除:\n  · " + "\n  · ".join(gone))

    for key, item in sorted(inventory.items()):
        declared = (item or {}).get("disposition")
        if declared not in VALID_DISPOSITIONS:
            problems.append(f"{key} 的 disposition「{declared}」不在允許值內")
            continue
        if declared == TARGETED and key not in targeted:
            # 清冊說被涵蓋而目標清單沒有它——**相信會執行的那一份**
            problems.append(f"{key} 清冊標為 targeted,但不在 TARGETS 內(紀錄不一致)")
            continue
        ev = found.get(key)
        if ev is None:
            continue          # 已由上面的 gone 報過
        expected = derive(key, ev, targeted)
        if expected != declared:
            problems.append(
                f"{key}:宣告「{declared}」與證據推導出的「{expected}」不符。\n"
                f"    證據 —— 解析:{', '.join(ev.parses) or '(無)'};"
                f"讀取:{', '.join(ev.reads) or '(無)'};"
                f"讀的是 repo 自己的檔:{ev.repo_paths}\n"
                f"    處置不是寫得漂亮就算數,要與函式實際做的事相符。")
        if declared == REGISTERED_GAP and not str((item or {}).get("registry", "")).strip():
            problems.append(f"{key} 標為 {REGISTERED_GAP} 但沒有帶待補項編號"
                            "——未涵蓋要追得到人,否則與豁免無異(KN-006)")
    for key in sorted(targeted - set(found)):
        problems.append(f"TARGETS 有「{key}」但掃描找不到它——名字對不上或函式已移除")
    return problems
