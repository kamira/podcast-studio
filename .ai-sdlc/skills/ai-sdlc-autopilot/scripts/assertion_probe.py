#!/usr/bin/env python3
"""斷言反轉探針 —— 找出「不可能變紅」的斷言(CHG-20260805-06)。stdlib-only。

變異產品程式量的是「測試夠不夠強」,而且會被**調校旋鈕**淹沒:把 `DEFAULT_REPEAT`
從 7 改成 8 而測試不紅,那不是測試弱,是那個數字本來就不影響行為。實測 `bench.py`
的 27 個存活變異體裡多數是這種。**噪音高的閘會被關掉**(KN-001),所以不走那條路。

這裡量的是另一件事:**這條斷言有沒有在檢查任何東西**。作法是把它的條件取反,
整份測試檔**必須**因此變紅。三種結果,對應三種狀態:

    斷言名沒出現在輸出裡  → **未執行**(未涵蓋)。這個環境沒走到那條分支,
                            不代表它是裝飾——把它讀成裝飾與讀成通過一樣錯。
    名字出現、但退出碼 0  → **裝飾**。它執行了,而結果被丟掉:排在計分之後、
                            被 except 吞掉、或元組長度寫錯而從未被解包。
    退出碼非 0            → 正常。

這正是 CHG-20260805-04 那三個互相遮蔽的缺陷會被抓到的方式——那次是靠
「斷言總數對不上」發現的,而那不該是發現機制。

用法:
  python3 assertion_probe.py --test-file <路徑> [--max N] [--json]
  python3 assertion_probe.py --all [--max-per-file N] [--json]

退出碼:0 = 沒有裝飾性斷言 | 1 = 有 | 2 = 環境/參數錯誤
"""
from __future__ import annotations
import argparse
import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SCRIPTS = Path(__file__).resolve().parent
# **重播要原樣重建環境**:`run_tests.sh` 開頭就 `cd` 到 repo 根,
# 也就是**在 repo 根目錄**跑每一支測試檔。探針改用 fixture 的目錄當 cwd 的話,
# 吃相對路徑的測試會在乾淨的樹上就紅(實測 `test_performance.py` → KeyError),
# 而那會被判成 baseline-red——**看起來像被測物有缺陷,實際上是我沒接好環境**。
# C2 的 `PY` 那一格付過同一族的學費(CHG-20260814-01 缺陷①)。
REPO_ROOT = SCRIPTS.parents[2]


def assertion_sites(src: str) -> list:
    """回 [(行號, 條件節點的精確位置, 斷言名稱的原始碼片段)]。

    位置用 AST 的 (lineno, col_offset, end_lineno, end_col_offset),**不是子字串**。
    第一版用 `text.replace(cond_src, ...)`,而短條件(例如 `ok`)會先命中同一行前面
    的別處——實測把 `("" if ok else …)` 的 `ok` 換掉,結果改到的是標籤而不是判定,
    於是回報成「裝飾」。字串比對第 N 次分不出意圖(CHG-20260805-06 自己踩到)。
    """
    tree = ast.parse(src)
    out = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "append" and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "checks" and n.args
                and isinstance(n.args[0], ast.Tuple) and len(n.args[0].elts) >= 2):
            cond, name = n.args[0].elts[1], n.args[0].elts[0]
            ns = ast.get_source_segment(src, name)
            pos = (cond.lineno, cond.col_offset,
                   getattr(cond, "end_lineno", None), getattr(cond, "end_col_offset", None))
            if ns and pos[2] is not None:
                out.append((pos, ast.get_source_segment(src, cond) or "", ns))
    return out


def _name_hint(name_src: str) -> str:
    """從斷言名稱取一段夠獨特、且不含格式化語法的字串,用來判斷它有沒有被印出來。"""
    lit = name_src.strip().lstrip("f").strip("\"'")
    for cut in ("{", "\\n"):
        if cut in lit:
            lit = lit.split(cut)[0]
    return lit.strip()[:24]


def replace_at(src: str, pos, repl: str) -> str:
    """依 AST 位置換掉該條件。**以位元組計算**——理由見 negate_at。"""
    l1, c1, l2, c2 = pos
    blines = [x.encode("utf-8") for x in src.splitlines(keepends=True)]
    start = sum(len(x) for x in blines[:l1 - 1]) + c1
    end = sum(len(x) for x in blines[:l2 - 1]) + c2
    raw = src.encode("utf-8")
    return (raw[:start] + repl.encode("utf-8") + raw[end:]).decode("utf-8")


def negate_at(src: str, pos) -> str:
    """依 AST 位置把該條件包成 `not (...)`。位置精確,不做子字串比對。

    **以位元組計算**:`ast` 的 `col_offset` 是 UTF-8 位元組偏移,不是字元索引。
    本 repo 的斷言名稱幾乎都是中文,用字元索引切會偏掉好幾格,產出的檔案
    直接 `IndentationError`——而探針會把那個崩潰讀成「這條沒被執行」,
    於是一條好斷言被歸進未涵蓋。紅得不對的第 N 次(KN-003)。
    """
    l1, c1, l2, c2 = pos
    blines = [x.encode("utf-8") for x in src.splitlines(keepends=True)]
    start = sum(len(x) for x in blines[:l1 - 1]) + c1
    end = sum(len(x) for x in blines[:l2 - 1]) + c2
    raw = src.encode("utf-8")
    return (raw[:start] + b"not (" + raw[start:end] + b")" + raw[end:]).decode("utf-8")


# 可達性標記走**退出碼**,不走文字。
#
# 第一版拋 RuntimeError,靠 traceback 裡的字串判。改成退出碼是因為**文字判定依賴
# 子行程的輸出編碼**——而輸出編碼正是本 repo 反覆出事的地方。
#
# (誠實記錄:我原本推論 Windows 那次紅是 traceback 寫 CJK 時壞掉導致標記消失,
#  實測 cp1252 下標記照樣印得出來,**那個推論是錯的**。真正的根因在 fixture:
#  它印中文卻沒釘住輸出編碼,在 cp1252 管線上 UnicodeEncodeError,後面的斷言
#  根本沒被執行到——探針判「未執行」是對的。退出碼仍然保留,因為它與輸出編碼完全無關。)
#
# `SystemExit` 不會被 `except Exception` 攔住,本 repo 既有的 try/except 慣例照樣傳得出來。
PROBE_EXIT_CODE = 97
PROBE_RAISE = f"(_ for _ in ()).throw(SystemExit({PROBE_EXIT_CODE}))"


# 變體檔的前綴(CHG-20260810-06)。**寫進原始碼目錄是刻意的、不能改**:
# 變體要解析得到 `from lib import …` 這種同層 import,而測試檔用
# `Path(__file__).parent` 推路徑——放進 tmpdir 就 import 不到,探針本身會失效。
#
# 真正要修的是**清理機制**。舊版靠 `finally: unlink()`,而行程被 kill、
# 整批探測被中斷、或機器斷電時 `finally` **不會跑**——那一次的殘留就留在
# `scripts/` 裡,然後:`run_tests.sh` 找 `test_*.py` 看不到它、
# 完整性錨定沒列它、`build_suite` 卻照樣把它**出貨給消費者**。
# 實際發生過一次(CHG-20260805-06 留下的 `tmpada5_jpq.py`,而且那份的
# **斷言是被反轉的**——它正是探針的產物)。
#
# 所以改成兩層:①名字可辨識,且**每次開始前先掃掉上一次的殘留**
# (不依賴上一次的 `finally` 有沒有跑到);②`check_probe_leftovers.py`
# 把「版本庫裡有殘留」變成機器擋得下的事(KN-005)。
PROBE_TMP_PREFIX = "_probe_tmp_"


def sweep_leftovers(directory: Path) -> list[Path]:
    """掃掉上一次沒清乾淨的變體。回被刪掉的清單(供呼叫端具名回報)。

    **主動掃,不依賴上一次的 `finally`**——會漏掉的正是「上一次非正常結束」那一次,
    而那一次也是唯一會留下殘留的一次。
    """
    swept = []
    for p in sorted(directory.glob(f"{PROBE_TMP_PREFIX}*.py")):
        try:
            p.unlink()
            swept.append(p)
        except OSError:
            pass          # 刪不掉不致命:check_probe_leftovers 會擋下
    return swept


def _run_variant(test_file: Path, mutated: str, timeout: int):
    sweep_leftovers(test_file.parent)
    with tempfile.NamedTemporaryFile("w", suffix=".py", prefix=PROBE_TMP_PREFIX,
                                     dir=str(test_file.parent),
                                     encoding="utf-8", delete=False) as fh:
        fh.write(mutated)
        tmp = Path(fh.name)
    try:
        r = subprocess.run([sys.executable, str(tmp)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           cwd=str(REPO_ROOT))
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    finally:
        tmp.unlink(missing_ok=True)


def slice_at(src: str, pos) -> str:
    """依 AST 位置取出該條件的**原始碼片段**,以位元組計算。

    與 `replace_at` / `negate_at` 同一套偏移——三者分岔的話,合成出來的變異
    會切在別的地方,而那個失效方向是「這條斷言看起來是裝飾」。
    """
    l1, c1, l2, c2 = pos
    blines = [x.encode("utf-8") for x in src.splitlines(keepends=True)]
    start = sum(len(x) for x in blines[:l1 - 1]) + c1
    end = sum(len(x) for x in blines[:l2 - 1]) + c2
    return src.encode("utf-8")[start:end].decode("utf-8")


PROBE_MARK = "<<probe-reached>>"


def combined_at(src: str, pos) -> str:
    """**一次行程同時回答兩個問題**:這一句被求值了嗎、取反之後整份測試會紅嗎。

    這是待補項 #60 的洞 B。舊版跑兩次子行程:第一次把條件換成拋 `SystemExit(97)`
    的哨兵判可達性,第二次才把條件取反判生死。而**環境敏感的分支在兩次之間會漂移**
    ——第一跑走到(哨兵響)、第二跑沒走到(取反句根本沒被求值,整份照樣綠)
    → 判成 **`decorative`**,也就是**誣指一條好斷言**。

    這台機器是 ephemeral 的,`test_performance.py` 的量測早退分支每天擲一次硬幣;
    實測同一支檔在不同時間拿到過三種不同的判定。

    合成後的運算式:`stderr.write(...)` 回寫入的字元數(非 0 → 真),
    `真 and None` → `None`,`None or (not (原條件))` → **`not (原條件)`**。
    於是它的值就是取反後的值,而副作用證明它**確實被求值過**。
    標記走 stderr 且純 ASCII:輸出編碼正是本 repo 反覆出事的地方(CHG-20260810-01)。
    """
    inner = slice_at(src, pos)
    repl = (f'(__import__("sys").stderr.write("{PROBE_MARK}\\n") and None '
            f'or (not ({inner})))')
    return replace_at(src, pos, repl)


def baseline_green(test_file: Path, timeout: int = 300) -> tuple[bool, str]:
    """**未變異的檔必須先綠。** 這是待補項 #60 的洞 A。

    舊版從不跑未變異的檔,於是基線紅時兩個方向都會說謊:

    · 一條**本來就在失敗**的斷言,取反之後轉綠 → 判 `decorative`
      ——誣指一條好斷言(CHG-20260813-09 實錄的那次就是這樣來的);
    · 更貴的反方向:基線紅、取反後**仍然紅**(紅在別條上)→ 判 **`ok`**,
      也就是**假 verified**。而寬鬆錯放的代價不可見,那正是整份清冊要消滅的東西。

    `behave_step_probe` 有 `baseline_green()`、`gate_canary` 有 `baseline-red` 態,
    唯獨這一支沒有——三支受測物的三態語言刻意同構,而這一格漏了。
    """
    try:
        # **絕對路徑**:cwd 被設成該檔的目錄,相對路徑在那裡開不起來
        # ——而 rc=2 會被讀成「這個檔基線就紅」,也就是**看起來像被測物有問題**。
        r = subprocess.run([sys.executable, str(test_file.resolve())], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, cwd=str(REPO_ROOT))
    except subprocess.TimeoutExpired:
        return False, "未變異的檔逾時——探不了,不是「有裝飾」"
    if r.returncode != 0:
        tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        return False, (f"未變異的檔就 rc={r.returncode}"
                       f"({tail[-1][:90] if tail else ''})——變異的紅證明不了任何事")
    return True, ""


def probe_one(test_file: Path, pos, cond_src: str, name_src: str,
              timeout: int = 300) -> str:
    """一次探測,回 'ok' / 'decorative' / 'not-run'。

    **可達性與取反在同一次行程裡回答**(見 `combined_at`)——分兩次跑的話,
    兩次之間的環境漂移會被讀成「這條斷言是裝飾」。

    呼叫端必須先確認基線綠(見 `baseline_green`);基線紅時本函式的回傳值無意義,
    所以 `probe_file` 在基線非綠時**根本不進來**,整檔回 `baseline-red`。
    """
    src = test_file.read_text(encoding="utf-8")
    try:
        rc, out = _run_variant(test_file, combined_at(src, pos), timeout)
        if "SyntaxError" in out or "IndentationError" in out:
            # 產生的檔案編不過 → 那是**探針壞了**,不是斷言的問題。
            # 讀成「未執行」會讓一條好斷言被歸進未涵蓋,而且沒人看得出來。
            raise RuntimeError(f"探針產生了編不過的檔案({test_file.name}:{pos[0]}):"
                               f"{out.strip().splitlines()[-1][:120]}")
        if PROBE_MARK not in out:
            return "not-run"          # 這個環境沒走到 → **未涵蓋**,不是裝飾
        return "ok" if rc != 0 else "decorative"
    except subprocess.TimeoutExpired:
        return "not-run"


def probe_file(test_file: Path, max_sites: int = 0) -> dict:
    """**基線先綠,才有資格讀變異的紅**(待補項 #60 洞 A)。

    基線一跑,不是每個位置跑一次:未變異的檔在整輪探測期間不會變,
    而一個位置一跑會讓成本乘上位置數(`test_performance.py` 單檔 31 個位置)。
    """
    src = test_file.read_text(encoding="utf-8")
    sites = assertion_sites(src)
    if max_sites:
        sites = sites[:max_sites]
    green, why = baseline_green(test_file)
    if not green:
        # **整檔判 baseline-red,一個位置都不探。**
        # 基線紅時,「取反後轉綠」與「取反後仍紅」兩種觀察都不成立:
        # 前者會誣指一條好斷言是裝飾,後者會給出**假 verified**。
        return {"file": test_file.name, "sites": len(sites), "ok": 0,
                "decorative": [], "not_run": [], "baseline_red": why}
    ok_n = 0
    decorative: list = []
    not_run: list = []
    for pos, cond, name in sites:
        verdict = probe_one(test_file, pos, cond, name)
        if verdict == "ok":
            ok_n += 1
        elif verdict == "decorative":
            decorative.append(f"{test_file.name}:{pos[0]}")
        else:
            not_run.append(f"{test_file.name}:{pos[0]}")
    return {"file": test_file.name, "sites": len(sites), "ok": ok_n,
            "decorative": decorative, "not_run": not_run, "baseline_red": ""}


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-file")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--max", type=int, default=0, help="單檔最多探幾條(0=不設限)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv[1:])
    if not a.test_file and not a.all:
        print("需要 --test-file 或 --all")
        return 2
    files = ([Path(a.test_file)] if a.test_file
             else sorted(SCRIPTS.glob("test_*.py")))
    reports = [probe_file(f, a.max) for f in files if f.is_file()]
    if a.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    else:
        for r in reports:
            if r.get("baseline_red"):
                print(f"── {r['file']}:**基線就紅,整檔沒探**"
                      f"({r['sites']} 個位置)——{r['baseline_red'][:90]}")
                continue
            print(f"── {r['file']}:探 {r['sites']} 條 / 正常 {r['ok']}"
                  f" / **裝飾 {len(r['decorative'])}** / 未執行 {len(r['not_run'])}")
            for d in r["decorative"]:
                print(f"     裝飾(執行了但結果被丟掉):{d}")
            for d in r["not_run"][:3]:
                print(f"     未執行(未涵蓋,不是裝飾):{d}")
    bad = sum(len(r["decorative"]) for r in reports)
    sites = sum(r["sites"] for r in reports)
    ran = sum(r["ok"] for r in reports) + bad          # 真的被反轉並觀察到結果的
    # **探不到東西不是「沒有裝飾性斷言」**(CHG-20260813-05,待補項 #47)。
    # 這支自己就是本 repo 最常用的探針,而它有三個綠燈洞——
    # 三個都印 ✅ 並回 0,與「探過 1041 條、裝飾 0」在退出碼上一模一樣(KN-001):
    #
    #   ① 單檔 0 個探測位置(例如 behave 步驟檔——它走 pytest,不在射程內);
    #   ② `--all` glob 到 0 個 `test_*.py`(搜尋壞了、目錄搬走);
    #   ③ 有位置、而**全部回「未執行」**(含逾時)——**探了但一條都沒真探到**。
    #
    # ③ 與 ① 在可觀測上只差一行摘要,所以三個一起收(fable 指出第三個)。
    red_files = [r["file"] for r in reports if r.get("baseline_red")]
    scope_fail = None
    if not files or not reports:
        scope_fail = ("一個檔案都沒掃到——**搜尋壞了與「沒有測試」在退出碼上一樣**"
                      "(KN-001);這不是「無裝飾性斷言」")
    elif sites == 0:
        scope_fail = (f"探測位置 0 條({', '.join(r['file'] for r in reports)})"
                      f"——**射程外或抽取失敗,不是通過**。"
                      f"behave 步驟檔走的不是 pytest,不在本探針射程內")
    elif red_files:
        scope_fail = (f"{len(red_files)}/{len(reports)} 個檔**基線就紅,整檔沒探**"
                      f"({', '.join(red_files[:3])})——**變異的紅證明不了任何事**;"
                      f"要修的是那些檔為什麼紅,不是去補涵蓋")
    elif ran == 0:
        scope_fail = (f"{sites} 個位置**全部未執行**(含逾時)——"
                      f"**探了但一條都沒真探到**;這與「探過而無裝飾」不是同一件事")
    # --json 只吐 JSON。混一行人看的摘要進去,呼叫端就得先剝掉才 parse 得動——
    # 而那一行的存在完全看不出來,直到有人真的去 parse(CHG-20260805-06 自己踩到)。
    if not a.json:
        if bad:
            print(f"\n❌ {bad} 條斷言執行了卻不可能判失敗——它們印 PASS 而沒在檢查任何東西。")
        elif scope_fail:
            print(f"\n⚠️ **射程外/未證明**:{scope_fail}")
        else:
            print(f"\n✅ 探過 {sites} 條(實際反轉 {ran} 條),無裝飾性斷言。")
    if bad:
        return 1
    # 射程外回 **3**,與「有裝飾」(1)和「探過且乾淨」(0)都分得開
    # ——三態必須分得開,否則呼叫端只能猜(KN-003)。
    return 3 if scope_fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
