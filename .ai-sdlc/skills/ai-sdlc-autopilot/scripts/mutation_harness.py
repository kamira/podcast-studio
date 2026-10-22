#!/usr/bin/env python3
"""變異測試引擎(CHG-20260803-01 T10)。stdlib-only、無 LLM、三平台一致。

**為什麼需要**:單元測試全綠只證明「測試沒失敗」,不證明「測試抓得到錯」。變異測試把
問題倒過來問——我故意在被測程式裡種一個錯,你的測試會不會紅?殺不掉的變異體
(surviving mutant)就是斷言的盲點:那一行改掉,沒有任何測試會知道。

**做法**:以 AST 逐點產生變異體 → 覆寫原檔 → 跑該模組的測試 → 還原。
測試非零 = 殺死;測試仍為零 = 存活。

**變異算子**(刻意只取「靜默翻轉治理語意」那一類,不做語法噪音):
  compare   比較運算子翻轉      ==↔!=  <↔>=  >↔<=  in↔not in  is↔is not
  boolconst 布林常數翻轉        True↔False
  boolop    and↔or
  num       數值常數 ±1        (退出碼、門檻、索引)
  not       移除 not
  return    `return X` → `return None`(整段判斷被短路的極端情形)

**基線護欄**:變異前先把「未變異但同樣經 ast.unparse 重寫」的版本跑一次測試。
若基線就紅,代表 unparse 本身破壞了程式——此時所有「殺死」都是假的,引擎會直接中止。
沒有這道護欄,kill rate 會虛高到 100%,而那正是最危險的假綠。

**上限與中斷(CHG-20260926-01 T11;代決 M3、M4)**:harness 是一串受限行程的協調者,本身
不設上限(變異閘以 `NO_LIMIT` 啟動它);基線測試以 `--process-timeout` 為上限,每個變異體的
測試上限是 `T_m = min(max(60, 10 × 基線秒數), --process-timeout)`。逾時的變異體殺掉整個測試
群組、計為「殺死(逾時)」並另計 `killed_timeout`。收到 SIGTERM / SIGHUP 時**先**把目標檔寫回
原始位元組,再以 SIGKILL 收掉正在跑的測試群組並刪除標記——呼叫端給的寬限只有幾秒,還原不能
排在等測試結束之後。

用法:
  python3 mutation_harness.py --target <module.py> --test "<測試指令>" [--min-kill-rate 90]
                              [--max-mutants N] [--process-timeout 秒] [--json]
退出碼:0 = 達標;1 = kill rate 未達門檻;2 = 參數/基線錯誤、無法解析、或同目錄已有標記;
        3 = 未涵蓋(找不到可變異的點);129 / 143 = 被 SIGHUP / SIGTERM 中斷(目標檔已還原)。
"""
from __future__ import annotations
import argparse
import ast
import copy
import json
import os
import signal
import sys
import time
from pathlib import Path

# 共用啟動器(lib/exec_util):每個測試有自己的行程群組、空的 stdin 與上限。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import exec_util as EU  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

CMP_FLIP = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.GtE, ast.GtE: ast.Lt,
            ast.Gt: ast.LtE, ast.LtE: ast.Gt, ast.In: ast.NotIn, ast.NotIn: ast.In,
            ast.Is: ast.IsNot, ast.IsNot: ast.Is}


class Collector(ast.NodeVisitor):
    """收集所有可變異的點,回 [(算子, 路徑索引, 說明)]。"""

    def __init__(self):
        self.points = []

    def visit(self, node):
        for kind, desc in self._mutations_of(node):
            self.points.append((node, kind, desc))
        super().visit(node)

    @staticmethod
    def _mutations_of(node):
        out = []
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                if type(op) in CMP_FLIP:
                    out.append((f"compare:{i}",
                                f"{type(op).__name__} → {CMP_FLIP[type(op)].__name__}"))
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                out.append(("boolconst", f"{node.value} → {not node.value}"))
            elif isinstance(node.value, int):
                out.append(("num", f"{node.value} → {node.value + 1}"))
        elif isinstance(node, ast.BoolOp):
            out.append(("boolop", f"{type(node.op).__name__} → "
                                  f"{'Or' if isinstance(node.op, ast.And) else 'And'}"))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            out.append(("not", "移除 not"))
        elif isinstance(node, ast.Return) and node.value is not None:
            if not (isinstance(node.value, ast.Constant) and node.value.value is None):
                out.append(("return", "return X → return None"))
        return out


def apply_mutation(tree: ast.Module, index: int):
    """在 tree 的副本上套用第 index 個變異點;回 (新 tree, 說明, 行號)。"""
    clone = copy.deepcopy(tree)
    col = Collector()
    col.visit(clone)
    node, kind, desc = col.points[index]
    lineno = getattr(node, "lineno", 0)
    if kind.startswith("compare:"):
        i = int(kind.split(":")[1])
        node.ops[i] = CMP_FLIP[type(node.ops[i])]()
    elif kind == "boolconst":
        node.value = not node.value
    elif kind == "num":
        node.value = node.value + 1
    elif kind == "boolop":
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
    elif kind == "not":
        # 以 operand 取代整個 UnaryOp:改父節點做不到,故就地換成 operand 的內容
        node.__class__ = node.operand.__class__
        node.__dict__ = dict(node.operand.__dict__)
    elif kind == "return":
        node.value = ast.Constant(value=None)
    ast.fix_missing_locations(clone)
    return clone, f"{kind} @L{lineno}: {desc}", lineno


def run_test(cmd: str, cwd: Path, timeout=None) -> bool | None:
    """跑一次測試指令。回 True = 通過(綠);False = 失敗(紅);None = 逾時(整個群組已被殺)。

    經共用啟動器(CHG-20260926-01 T11):測試在自己的行程群組裡、拿不到 stdin。`timeout=None`
    是 `--process-timeout`(基線用);變異體傳自己的 T_m。寬限是 0:逾時或 harness 被中斷時,
    正在跑的是變異體(或 unparse 過的版本),目標檔由 harness 還原,測試不需要清理時間。
    """
    r = EU.run_shell(cmd, cwd, timeout=timeout, kill_grace=0)
    if r.timed_out:
        return None
    return r.returncode == 0


MIN_MUTANT_LIMIT = 60        # 每個變異體至少給 60 秒
MUTANT_LIMIT_FACTOR = 10     # 或基線的 10 倍,取大者;再以 --process-timeout 截住


def mutant_limit(t_base: float, process_timeout: float) -> float:
    """每個變異體的測試上限 T_m = min(max(60, 10 × 基線秒數), --process-timeout)(代決 M3)。"""
    return min(max(MIN_MUTANT_LIMIT, MUTANT_LIMIT_FACTOR * t_base), process_timeout)


def _marker_pid(marker: Path) -> str:
    """標記檔裡的 pid(讀不到就回「未知」)。字串操作,不解析結構。"""
    try:
        text = marker.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return "未知"
    for line in text.splitlines():
        if line.startswith("pid="):
            return line[len("pid="):].strip() or "未知"
    return "未知"


def _refuse_marker(marker: Path) -> int:
    pid = _marker_pid(marker)
    print(f"❌ 偵測到 {marker}(pid {pid}):同一目錄已有變異在進行中,"
          "或上一次變異被強制結束、沒來得及收尾。")
    print("   併行變異會互相覆寫彼此的還原點 → 拒絕執行。")
    print(f"   復原:確認 pid {pid} 已不在執行;把這個目錄裡的檔與版本控制比對,"
          "確認沒有停在變異體的內容;然後刪除這個標記檔再重跑。")
    return 2


def _restore_on_signal(target: Path, original: bytes) -> None:
    """SIGTERM / SIGHUP:先同步把目標檔寫回原始位元組,再拋出(CHG-20260926-01 T11)。

    拋出的 SystemExit 經 run_shell 的 `except BaseException` 以 SIGKILL 收掉正在跑的測試群組,
    main 的 finally 再確認一次還原並刪除標記。已被設為忽略的訊號不接手(nohup)。
    handler 一進來就把兩個訊號改成忽略:收尾不能被第二個訊號打斷。
    """
    sigs = [s for s in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGHUP", None))
            if s is not None and signal.getsignal(s) != signal.SIG_IGN]

    def _handler(signum, _frame):
        for s in sigs:
            signal.signal(s, signal.SIG_IGN)
        try:
            target.write_bytes(original)
        except OSError:
            pass
        raise SystemExit(128 + signum)

    for s in sigs:
        signal.signal(s, _handler)


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, help="要變異的模組檔")
    ap.add_argument("--test", required=True, help="判定生死的測試指令")
    ap.add_argument("--cwd", default=".", help="測試指令的工作目錄")
    ap.add_argument("--min-kill-rate", type=float, default=90.0)
    ap.add_argument("--max-mutants", type=int, default=0, help="0 = 不設限")
    ap.add_argument("--process-timeout", type=float, default=EU.DEFAULT_PROCESS_TIMEOUT,
                    metavar="SECONDS",
                    help="基線測試的上限,也是每個變異體上限的天花板"
                         f"(預設 {EU.DEFAULT_PROCESS_TIMEOUT});harness 本身不設上限")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv[1:])
    if not args.process_timeout > 0:
        ap.error("--process-timeout 必須是正數秒;沒有「不設上限」的值")
    EU.set_process_timeout(args.process_timeout)

    target = Path(args.target).resolve()
    cwd = Path(args.cwd).resolve()
    if not target.is_file():
        print(f"找不到 target:{target}")
        return 2

    # 就地覆寫護欄(CHG-20260803-02,實地踩到才補的)。
    # 變異期間 target 的內容**不是原始碼**,而是變異體。若此時有人(或另一個 session)
    # 對 repo 下 `git add -A && git commit`,被永久收進版控的就是那個變異體——
    # 實測發生過一次:`doc_integrity_check.py` 的註解被 ast.unparse 洗掉並進了 commit。
    # 標記檔讓並行的工具與人看得見「現在不要碰這個 repo」,結束時一定移除。
    # 標記檢查排在最前面;讀檔、解析與零點檢查都在建標記**之前**(CHG-20260926-01 T10,NEW-4):
    # c5af2cc 先建標記再檢查,零點、語法錯誤與非 UTF-8 檔都從 try 之前離開,標記永遠留著,
    # 之後同一個目錄的每一次執行都被拒。
    marker = target.parent / ".mutation-in-progress"
    if marker.exists():
        return _refuse_marker(marker)

    try:
        original = target.read_bytes()
        tree = ast.parse(original.decode("utf-8"))
    except (UnicodeDecodeError, SyntaxError, ValueError) as e:
        print(f"{target.name}:無法解析({type(e).__name__}: {e})——不是可變異的 Python 原始檔")
        return 2
    col = Collector()
    col.visit(copy.deepcopy(tree))
    total_points = len(col.points)
    if total_points == 0:
        # 未涵蓋,不是錯誤也不是通過(代決 M1):回 3,--json 下照樣給機器讀得懂的形狀
        why = "找不到可變異的點,算子清單對這個模組無效(例如空的 __init__.py)"
        if args.json:
            print(json.dumps({"target": str(target), "mutants_total": 0, "not_covered": why},
                             ensure_ascii=False, indent=2))
        else:
            print(f"{target.name}:未涵蓋——{why}。這不是通過")
        return 3

    # 還原 handler 先裝、標記在 try/finally 裡建(實作後審查):以前標記建在兩者之前,中間收到的 SIGTERM
    # 以預設處置結束行程,標記留著、之後同一個目錄的每一次執行都被拒。`made` 在 open 成功後立刻設,
    # finally 只刪自己建的標記——搶輸的那一方不能刪掉別人的。
    made = False
    _restore_on_signal(target, original)
    try:
        try:
            with open(marker, "x", encoding="utf-8") as fh:
                made = True
                fh.write(f"target={target.name}\npid={os.getpid()}\n"
                         f"警告:變異進行中,此目錄的檔案內容為變異體,請勿 commit。\n")
        except FileExistsError:  # diffcov-exempt: syserr — `exists()` 之後、`open('x')` 之前被另一個 harness 搶先建標記的競態 [signoff: self(T15 實作者自審,待實作後審查席複核) @ CHG-20260926-01]
            return _refuse_marker(marker)  # diffcov-exempt: syserr — `exists()` 之後、`open('x')` 之前被另一個 harness 搶先建標記的競態 [signoff: self(T15 實作者自審,待實作後審查席複核) @ CHG-20260926-01]
        # --- 基線護欄:先用「unparse 後但未變異」的版本跑一次(上限 = --process-timeout)---
        target.write_text(ast.unparse(tree), encoding="utf-8")
        t0 = time.monotonic()
        base = run_test(args.test, cwd)
        t_base = time.monotonic() - t0
        if base is None:
            print(f"❌ 基線逾時:未變異的版本超過 {args.process_timeout:g} 秒(--process-timeout)"
                  "仍未結束——無法評估,不產出 kill rate。")
            return 2
        if not base:
            print("❌ 基線失敗:未變異的 unparse 版本就跑不過測試。")
            print("   代表 ast.unparse 改變了程式行為(或測試依賴原始碼文字),")
            print("   此時所有『殺死』都是假的 → 中止,不產出 kill rate。")
            return 2
        t_m = mutant_limit(t_base, args.process_timeout)

        # --- 取樣(若設上限)。丟掉的必須明講,不做無聲截斷 ---
        indices = list(range(total_points))
        dropped = 0
        if args.max_mutants and total_points > args.max_mutants:
            step = total_points / args.max_mutants
            indices = sorted({int(i * step) for i in range(args.max_mutants)})
            dropped = total_points - len(indices)

        killed, survived, timeouts = 0, [], []
        for n, idx in enumerate(indices, 1):
            mutant, desc, lineno = apply_mutation(tree, idx)
            try:
                target.write_text(ast.unparse(mutant), encoding="utf-8")
            except (ValueError, AttributeError, TypeError) as e:
                survived.append(f"{desc}(無法產生變異體:{e})")
                continue
            alive = run_test(args.test, cwd, timeout=t_m)
            if alive:
                survived.append(desc)          # 測試仍綠 = 沒殺掉
            else:
                killed += 1                    # 紅,或逾時:跑不完也是測試對這個變異有反應(M4)
                if alive is None:
                    timeouts.append(desc)
            if not args.json:
                mark = "存活" if alive else ("殺死(逾時)" if alive is None else "殺死")
                print(f"  [{n}/{len(indices)}] {mark} {desc}"[:76].ljust(78), end="\r")
    finally:
        target.write_bytes(original)                    # 一定還原,逐位元組
        if made:
            marker.unlink(missing_ok=True)

    run_n = len(indices)
    rate = (killed / run_n * 100) if run_n else 0.0
    result = {"target": str(target), "mutants_total": total_points,
              "mutants_run": run_n, "dropped_by_cap": dropped,
              "killed": killed, "killed_timeout": len(timeouts), "survived": len(survived),
              "kill_rate": round(rate, 1), "min_kill_rate": args.min_kill_rate,
              "mutant_limit": round(t_m, 1), "survivors": survived, "timeouts": timeouts}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(" " * 78, end="\r")
        print(f"\n{target.name}:變異點 {total_points},實跑 {run_n}"
              + (f"(取樣上限丟棄 {dropped} 個)" if dropped else "")
              + f",殺死 {killed}(其中逾時 {len(timeouts)}),存活 {len(survived)}"
              + f" → kill rate {rate:.1f}%(每個變異體上限 {t_m:g} 秒)")
        for s in timeouts:
            print(f"  [殺死(逾時)] {s}")
        for s in survived:
            print(f"  [存活] {s}")
    return 0 if rate >= args.min_kill_rate else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
