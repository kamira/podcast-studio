#!/usr/bin/env python3
"""測試棘輪與不穩定測試偵測(CHG-20260803-08)。

`test_gates_wired.py` 守的是「閘門還在不在」;本模組守的是下一層——**測試還在不在**,
以及**綠燈可不可重現**。

為什麼現有的閘擋不住「把紅的測試刪掉」:
  · 單元閘只問「測試指令有沒有回 0」——刪掉那支測試,它就回 0 了
  · 變異閘沒有測試就沒有存活變異體,回報的是「未涵蓋」而不是失敗
  · 逐 task review 看得到 diff,但「移除過時測試」與規避在 diff 裡長得一樣
  · 靜態閘只看程式碼形態

以及擋不住「跑一次剛好綠」:整套階梯只跑一次測試,50% 通過率的測試與穩定測試無法區分。

決策層為純函式(輸入決定輸出、不碰 git 與 subprocess),IO 層是薄殼。
"""
from __future__ import annotations
import ast
from pathlib import Path

from .exec_util import run_git, run_shell

# 棘輪只支援 Python:其他語言沒有等價的 AST 算子。
# 與變異閘同一處置——非 .py 一律回報「未涵蓋」,絕不回報「通過」。
RATCHET_SUFFIXES = (".py",)
# 硬性棘輪指標。`files` 刻意不在其中:把三個小測試檔合併成一個時檔案數必然下降,
# 那是重構不是規避。「刪掉一整個測試檔」不必靠 files 抓——檔案沒了,
# 它的 tests 與 asserts 也一起沒了。
RATCHET_METRICS = ("tests", "asserts")
# `stmts` 另以**容忍帶**參與判定,理由是實測踩出來的:
# 本 repo 自己的測試用 `checks.append((名稱, 布林))` 註冊斷言,既沒有 `test_` 開頭的
# 函式也沒有 `assert` 語句——只看 tests/asserts 的話,把整支測試砍光都會放行。
# 一個對自己的程式碼失效的閘,是最難發現的失效。
# 但語句數會因為正當的整理而小幅下降,所以給 10% 的帶寬:
# 吸收「順手把測試寫短一點」,仍抓得住「把一半的斷言刪掉」。
STMT_TOLERANCE = 0.10
DEFAULT_FLAKY_RUNS = 2


def is_test_path(rel: str) -> bool:
    name = Path(rel).name
    return name.startswith("test_") or name.endswith("_test.py")


def test_metrics(source: str) -> dict | None:
    """一份測試檔的指標。無法解析回 None(回報而非崩潰)。

    一律以 AST 取得:註解裡的 `# assert x` 與字串裡的 `"assert x"` 都不算——
    grep 會把兩者都數進去,而誤報一旦開始,人就學會略過整份輸出(同 CHG-20260803-06)。
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    tests = asserts = stmts = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt):
            stmts += 1
        # `assert x` 之外也數 assertEqual / assertTrue 這類呼叫:
        # 只認 `ast.Assert` 會對 unittest 風格的測試整個失效。
        if isinstance(node, ast.Assert):
            asserts += 1
        elif isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", "") or ""
            if "assert" in name.lower():
                asserts += 1
        # 測試檔裡的**每一個**函式都算,不限 `test_` 開頭:命名慣例因專案而異
        # (本 repo 的測試就叫 `t1_metrics`),綁死慣例等於綁死適用範圍。
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            tests += 1
    return {"tests": tests, "asserts": asserts, "stmts": stmts}


def decide(before: dict, after: dict, allow_reduction: bool = False,
           uncovered=()) -> tuple[bool, str]:
    """棘輪判定。淨減少即不通過。

    判「淨」而不是逐檔:把三個小測試檔合併成一個是正當重構,逐檔判定會擋住它;
    真正要擋的是**總量變少**。
    """
    unc = sorted({u for u in (uncovered or []) if u})
    reduced = [(m, before.get(m, 0), after.get(m, 0))
               for m in RATCHET_METRICS if after.get(m, 0) < before.get(m, 0)]
    # 語句數的容忍帶:抓「斷言被整批刪掉」而不抓「順手把測試寫短一點」
    sb, sa = before.get("stmts", 0), after.get("stmts", 0)
    if sb and sa < sb * (1 - STMT_TOLERANCE):
        reduced.append(("stmts", sb, sa))
    shape = (f"files {before.get('files', 0)}→{after.get('files', 0)}, "
             f"tests {before.get('tests', 0)}→{after.get('tests', 0)}, "
             f"asserts {before.get('asserts', 0)}→{after.get('asserts', 0)}")

    if reduced and not allow_reduction:
        rows = "\n".join(f"    · {m}:{b} → {a}(少了 {b - a})" for m, b, a in reduced)
        return False, ("測試棘輪:**本次變更淨減少了測試**——讓 build 轉綠最省事的方法\n"
                       f"    正是刪掉那個紅的測試,所以這條路預設不通。\n{rows}\n"
                       "    正當的重構請用 --allow-test-reduction 明示(會留痕)。")
    if reduced:
        rows = ", ".join(f"{m} {b}→{a}" for m, b, a in reduced)
        return True, ("⚠️ 測試棘輪:已以 --allow-test-reduction 明示放行測試減少"
                      f"({rows})——須記入 ACC 的未涵蓋欄")
    if unc:
        # 有未涵蓋的語言時**不說「通過」**:沒驗過的事不該被讀成驗過了(KN-001)
        return True, (f"測試棘輪:{', '.join(unc)} 的測試檔**未涵蓋**"
                      f"(棘輪僅支援 {', '.join(RATCHET_SUFFIXES)});"
                      f"已涵蓋的部分無減少({shape})。這不是全面『通過』,"
                      "請在 ACC 的未涵蓋欄註明")
    return True, f"測試棘輪通過({shape})"


def flaky_runs(n) -> tuple[int, str | None]:
    """實際重跑次數。下限 1;為 1 時等同關閉,回留痕訊息。"""
    try:
        runs = int(n) if n is not None else DEFAULT_FLAKY_RUNS
    except (TypeError, ValueError):
        runs = DEFAULT_FLAKY_RUNS
    runs = max(1, runs)
    if runs == 1:
        return 1, ("⚠️ 不穩定測試偵測已關閉(--flaky-runs 1):綠燈只驗過一次,"
                   "須記入 ACC 的未涵蓋欄")
    return runs, None


def flaky_decide(codes) -> tuple[bool, str]:
    """同一份程式碼多次執行的判定。全綠才通過。

    不穩定 → halt 而非警告:不穩定的綠燈與恆真的綠燈是同一個問題(KN-001),
    放行等於把「這條線路的訊號不可信」寫成常態。
    """
    seq = list(codes or [])
    if len(seq) < 2:
        return True, "不穩定測試偵測:未重跑(次數為 1)"
    shown = ", ".join(str(c) for c in seq)
    if all(c == 0 for c in seq):
        return True, f"不穩定測試偵測通過(重跑 {len(seq)} 次,退出碼 {shown})"
    return False, ("不穩定測試偵測:**同一份程式碼重跑結果不一致**——"
                   f"退出碼依序為 {shown}。\n"
                   "    跑一次剛好綠不算通過:不穩定的綠燈與恆真的綠燈是同一個問題。")


def _git_lines(repo: Path, args: str) -> list[str]:
    """`git <args> -z` 列出的路徑(NUL 分隔)。逐行讀的話,非 ASCII 或帶 `"`、`\\` 的檔名被 git 包成
    `"…\\303…"`(core.quotePath),被刪掉的測試檔就溜過棘輪(實作後審查,四席)。"""
    r = run_git([*args.split(), "-z"], repo)
    if r.returncode != 0:
        return []
    return [p for p in (r.stdout or "").split("\0") if p]


def changed_test_files(repo: Path) -> tuple[list[str], list[str]]:
    """(有變動的測試檔, 被刪除的測試檔)。未提交的 diff 就是本 task 的產出。"""
    live, deleted = [], []
    for rel in _git_lines(repo, "diff --name-only --diff-filter=d") + \
            _git_lines(repo, "diff --name-only --cached --diff-filter=d") + \
            _git_lines(repo, "ls-files --others --exclude-standard"):
        if is_test_path(rel) and rel not in live:
            live.append(rel)
    for rel in _git_lines(repo, "diff --name-only --diff-filter=D") + \
            _git_lines(repo, "diff --name-only --cached --diff-filter=D"):
        if is_test_path(rel) and rel not in deleted:
            deleted.append(rel)
    return live, deleted


def before_after(repo: Path) -> tuple[dict, dict, list[str]]:
    """本 task 變更前後的測試指標總量,以及未涵蓋的副檔名。

    被刪掉的整個測試檔算**完整損失**——只看還存在的檔案等於獎勵「整檔刪除」:
    刪一個函式被擋、刪整個檔案反而過,那是最糟的誘因方向。
    """
    repo = Path(repo)
    live, deleted = changed_test_files(repo)
    before = {"files": 0, "tests": 0, "asserts": 0, "stmts": 0}
    after = {"files": 0, "tests": 0, "asserts": 0, "stmts": 0}
    uncovered = set()

    def add(bucket: dict, metrics: dict | None) -> None:
        bucket["files"] += 1
        if metrics:
            for k in ("tests", "asserts", "stmts"):
                bucket[k] += metrics[k]

    for rel in live + deleted:
        if Path(rel).suffix not in RATCHET_SUFFIXES:
            uncovered.add(Path(rel).suffix or "(無副檔名)")
            continue
        # 檔名是施工者自取的:以 argv 交給 git,不經 shell(CHG-20260926-01 T4,X4)。
        # c5af2cc 的 `git show "HEAD:<檔名>"` 經 /bin/sh,雙引號裡的 `$(…)` 照樣展開。
        head = run_git(["show", f"HEAD:{rel}"], repo)
        if head.returncode == 0:
            add(before, test_metrics(head.stdout or ""))
        if rel in deleted:
            continue
        p = repo / rel
        if p.is_file():
            add(after, test_metrics(p.read_text(encoding="utf-8", errors="replace")))
    return before, after, sorted(uncovered)


def ratchet_gate(repo: Path, allow_reduction: bool = False) -> tuple[bool, str, dict]:
    """棘輪閘:純 AST + git,是 build 階段最便宜的一道,故排在最前。"""
    before, after, uncovered = before_after(repo)
    if before["files"] == 0 and after["files"] == 0:
        return True, "測試棘輪:本 task 未動到測試檔", {"before": before, "after": after}
    ok, msg = decide(before, after, allow_reduction, uncovered)
    return ok, msg, {"before": before, "after": after, "uncovered": uncovered}


def flaky_gate(repo: Path, test_cmd: str, runs) -> tuple[bool, str, dict]:
    """不穩定測試偵測:單元測試轉綠後,在**同一份程式碼**上再跑幾次。"""
    n, note = flaky_runs(runs)
    if note:
        return True, note, {"runs": 1}
    codes = [0]                      # 第一次已由單元閘跑過且為綠
    outputs = []
    for _ in range(n - 1):
        r = run_shell(test_cmd, repo)
        codes.append(r.returncode)
        outputs.append((r.stdout or "")[-400:] + (r.stderr or "")[-400:])
    ok, msg = flaky_decide(codes)
    if not ok and outputs:
        msg += f"\n    最後一次重跑的輸出尾段:\n{outputs[-1].strip()[:400]}"
    return ok, msg, {"runs": n, "codes": codes}
