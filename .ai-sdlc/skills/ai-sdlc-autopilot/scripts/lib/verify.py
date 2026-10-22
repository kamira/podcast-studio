#!/usr/bin/env python3
"""施工階段針對 **agent 產出程式碼** 的驗證閘(CHG-20260803-02)。

`--test-cmd` 全綠只證明「agent 寫的測試沒抓到 agent 寫的錯」——同一個模型寫程式又寫測試,
兩邊共享同一組盲點(見 ai-sdlc `independent-acceptance`)。變異閘把問句倒過來:
**種一個錯進去,那些測試會不會紅?** 殺不掉的變異體就是這個 task 的驗證缺口。

本模組維持 no-LLM:只跑 git、跑 mutation_harness、算數字、回報。
"""
from __future__ import annotations
import json
import platform
import sys
from pathlib import Path

from .exec_util import (HANDSHAKE_REL, NO_LIMIT, mutation_enabled, run_argv, run_git)
from .exec_util import process_timeout as _global_process_timeout

HARNESS = Path(__file__).resolve().parent.parent / "mutation_harness.py"
# 目前只有 Python 有 AST 變異算子。其他語言不是「通過」,是「未涵蓋」——兩者必須可區分。
MUTATABLE_SUFFIXES = (".py",)


def _is_test_file(p: Path) -> bool:
    """測試檔本身不得作為變異對象。

    變異測試檔會讓 kill rate **虛高**:改壞一條斷言 → 測試紅 → 記為「殺死」,
    但那證明的是「測試檔會影響測試結果」這種同義反覆,不是「程式被保護著」。
    """
    n = p.name
    return (n.startswith("test_") or n.endswith("_test.py")
            or "tests" in p.parts or "test" in p.parts)


def changed_files(repo: Path) -> list[Path]:
    """本 task 尚未提交的變更檔(已追蹤的改動 + 未追蹤的新檔)。

    逐 task commit 的前提下,未提交的 diff 就是這個 task 的產出——變異的歸屬因此正確:
    這個 task 寫的碼,由這個 task 的測試負責。

    runner 自己的 handshake 永遠不算(CHG-20260926-01 T3,代決 S5):它在 task 開始時由
    runner 寫入,算進去的話,什麼都沒改的施工者也會得到「靜態/安全檢查通過(1 個變更檔)」。
    以 resolve() 比對,repo 以相對路徑或經 `..` 傳入時照樣排除。`git add -A` 仍會一併
    commit 它——排除的只是逐 task 的閘,不是 commit。

    檔名以 `-z`(NUL 分隔)讀:逐行讀的話,git 把非 ASCII 或帶 `"`、`\\` 的檔名包成
    `"m\\303\\263dulo.py"`(core.quotePath),那個檔在這裡找不到,整個溜過靜態/安全閘與變異閘
    (實作後審查,四席)。
    """
    handshake = (Path(repo) / HANDSHAKE_REL).resolve()
    out = []
    for args in (["diff", "--name-only", "-z"], ["diff", "--name-only", "--cached", "-z"],
                 ["ls-files", "--others", "--exclude-standard", "-z"]):
        r = run_git(args, repo)
        if r.returncode == 0:
            out += [p for p in (r.stdout or "").split("\0") if p]
    seen, files = set(), []
    for rel in out:
        if rel in seen:
            continue
        seen.add(rel)
        p = Path(repo) / rel
        if p.is_file() and p.resolve() != handshake:
            files.append(p)
    return files


def parse_mutation_result(stdout: str, stderr: str = "") -> tuple[dict | None, list[str]]:
    """把變異引擎的 JSON 輸出解析成結果。回 `(結果 or None, 錯誤訊息行)`。

    從 `mutation_gate` 抽出來的**純函式**(CHG-20260810-04,待補項 #25)。
    理由與 `ci_gate.parse_checks` 同源:原本這段的輸入來自 subprocess 的 stdout,
    **注入不了、也不該用 fuzz 產物去餵一條會執行指令的路**。抽出來之後
    `mutation_gate` 自己沒有解析呼叫,自動離開 fuzz 清冊——
    **修法是把 gap 消掉,不是登記它**。

    抽出來的當下就看到一個洞:原本只接 `json.JSONDecodeError`,而
    `json.loads("[]")` / `json.loads("{}")` **會成功**,下一行 `res["kill_rate"]`
    才炸(`TypeError` / `KeyError`)——那不在 except 的射程內,會變成未捕捉例外。
    合法 JSON 不等於預期形狀,所以這裡連形狀一起驗(KN-003:崩潰與正確擋下
    在退出碼上分不開)。

    未涵蓋(CHG-20260926-01 T10,代決 M1):找不到變異點的檔,harness 回
    `{"target", "mutants_total": 0, "not_covered": "<理由>"}`,沒有 kill_rate。它是結果,不是錯誤;
    但理由必須是非空字串——讀不出理由的「未涵蓋」與讀不出結果的錯誤分不開。
    """
    try:
        res = json.loads(stdout)
    except (ValueError, TypeError):
        return None, (stdout or stderr or "").strip().splitlines()
    if isinstance(res, dict) and "not_covered" in res:
        why = res["not_covered"]
        if isinstance(why, str) and why.strip():
            return res, []
        return None, [f"引擎回報未涵蓋卻沒有理由:{str(res)[:80]}"]
    if not isinstance(res, dict) or "kill_rate" not in res:
        return None, [f"引擎輸出是合法 JSON 但不是預期形狀:{str(res)[:80]}"]
    return res, []


def mutation_gate(repo: Path, test_cmd: str, min_kill_rate: float,
                  mutation_max: int = 40, process_timeout=None) -> tuple[bool, str, dict]:
    """對本 task 變更的可變異檔跑變異測試。

    回 (通過, 人讀訊息, 明細)。**「未涵蓋」一律回通過=True 但訊息明講**——
    擋住非 Python 專案不是本閘的職責,但讓人以為驗過了才是真正的危害(KN-001)。
    找不到變異點的 .py(例如空的 `__init__.py`)同樣是未涵蓋,逐檔點名,不壓低最低 kill rate
    (CHG-20260926-01 T10,代決 M1)。

    harness 經共用啟動器、以 `timeout=NO_LIMIT` 啟動(T11,代決 M3):它是一串受限行程的協調者,
    整體不設上限;`process_timeout`(None = 全域的 --process-timeout)以 harness 的
    `--process-timeout` 傳進去,基線與每個變異體的測試各自受限。逾時的變異體計為殺死(M4),
    逐檔印出「殺死 K(其中逾時 J)」,扣掉逾時殺死就低於門檻的檔另外點名;總數在
    `detail["killed_timeout"]`。
    """
    files = changed_files(repo)
    targets = [f for f in files if f.suffix in MUTATABLE_SUFFIXES and not _is_test_file(f)]
    # 先建區域變數再放進 detail:直接從異質 dict 取值會被推成 object,
    # 於是 join 與 append 都判不過——而那不是型別註記的問題,是取值方式的問題
    per_file: list[dict] = []
    not_covered: list[str] = []
    skipped = sorted({f.suffix for f in files
                      if f.suffix not in MUTATABLE_SUFFIXES and f.suffix})
    detail = {"changed": len(files), "mutatable": len(targets), "per_file": per_file,
              "skipped_languages": skipped, "not_covered": not_covered, "killed_timeout": 0}

    if not files:
        return True, "變異閘:本 task 無檔案變更 → 未涵蓋(無可變異對象)", detail
    if not targets:
        langs = ", ".join(skipped) or "(無副檔名)"
        return True, (f"變異閘:**未涵蓋** — 本 task 變更的是 {langs},"
                      f"變異引擎目前僅支援 {', '.join(MUTATABLE_SUFFIXES)}。"
                      f"這不是『通過』,請在 ACC 的未涵蓋欄註明"), detail

    limit = _global_process_timeout() if process_timeout is None else process_timeout
    worst: float | None = None
    killed_timeout = 0
    lines = []
    for tgt in targets:
        # 絕對路徑:harness 的工作目錄是 repo,相對路徑會被解讀兩次
        cmd = [sys.executable, str(HARNESS), "--target", str(Path(tgt).resolve()),
               "--test", test_cmd, "--cwd", str(Path(repo).resolve()), "--json",
               "--min-kill-rate", str(min_kill_rate), "--process-timeout", f"{limit:g}"]
        if mutation_max:
            cmd += ["--max-mutants", str(mutation_max)]
        r = run_argv(cmd, repo, timeout=NO_LIMIT)
        res, msg = parse_mutation_result(r.stdout, r.stderr)
        if res is None:
            # 引擎中止(基線失敗或逾時、目標檔無法解析、同目錄殘留標記)——這是**真問題**,不可放行
            per_file.append({"file": str(tgt), "error": msg[:3]})
            return False, f"變異閘:{tgt.name} 無法評估 — {' / '.join(msg[:3])}", detail
        per_file.append(res)
        if "not_covered" in res:
            not_covered.append(tgt.name)
            lines.append(f"  · {tgt.name}: 未涵蓋——{res['not_covered']}")
            continue
        rate, run_n = res["kill_rate"], res.get("mutants_run", 0)
        killed, kt = res.get("killed", 0), res.get("killed_timeout", 0)
        killed_timeout += kt
        worst = rate if worst is None else min(worst, rate)
        tag = "✓" if rate >= min_kill_rate else "✗"
        lines.append(f"  {tag} {tgt.name}: kill rate {rate}% "
                     f"({killed}/{run_n}),殺死 {killed}(其中逾時 {kt})"
                     + (f",取樣丟棄 {res['dropped_by_cap']}" if res.get("dropped_by_cap") else ""))
        for s in res.get("timeouts", []):
            lines.append(f"      [殺死(逾時)] {s}")
        for s in res.get("survivors", []):
            lines.append(f"      [存活] {s}")
        if kt and run_n and rate >= min_kill_rate:
            without = round((killed - kt) / run_n * 100, 1)
            if without < min_kill_rate:
                lines.append(f"      ⚠️ {tgt.name}:扣掉逾時殺死的 {kt} 個,kill rate 是 {without}%,"
                             f"低於門檻 {min_kill_rate}%——這個檔的殺死率依賴逾時,"
                             "請在 ACC 的未涵蓋欄註明")

    detail["killed_timeout"] = killed_timeout
    body = "\n".join(lines)
    uncovered = (f"\n  未涵蓋的檔({', '.join(not_covered)})不是『通過』,請在 ACC 的未涵蓋欄註明"
                 if not_covered else "")
    if worst is None:
        return True, (f"變異閘:**未涵蓋** — {', '.join(not_covered)} 找不到可變異的點。"
                      f"這不是『通過』,請在 ACC 的未涵蓋欄註明\n{body}"), detail
    detail["worst_kill_rate"] = worst
    if worst < min_kill_rate:
        return False, (f"變異閘未達門檻({worst}% < {min_kill_rate}%)——"
                       f"agent 寫的測試殺不掉下列變異體,代表那些行改錯了也不會被發現:\n{body}"
                       f"{uncovered}"), detail
    return True, f"變異閘通過(最低 kill rate {worst}%):\n{body}{uncovered}", detail


# kill rate 的底線(CHG-20260803-02 T13)。低於此值等同關閉變異閘——
# 把門檻調到 0 再宣稱「變異測試通過」是最省事的偽造方式,故需 CHG 明文宣告。
KILL_RATE_FLOOR = 60.0
ESCAPE_DECLARE_RE = "Escape-hatch:"


def escape_hatches(args, chg_text: str) -> tuple[list[str], str | None]:
    """盤點本輪用了哪些逃生口;回 (使用清單, 阻擋理由或 None)。

    逃生口本身是必要的(純文件 task 不該被逼著寫測試),問題在於**用了沒人知道**。
    每一個都寫進 handshake 與收尾提醒;其中「把門檻調到底線以下」影響最大——
    那等於在保留「跑過變異測試」這個說法的同時把它關掉——故要求 CHG 明文宣告。

    變異閘預設開啟,所以是否檢查底線看的是 `mutation_enabled`,不是 `--mutation`
    (CHG-20260926-01 T10,#8):c5af2cc 只在傳了 `--mutation` 時才列出、才要求宣告行,
    預設路徑上 `--min-kill-rate 10` 既不被列出也不被擋。`--no-mutation` 本身是逃生口(NEW-13);
    變異閘沒跑時,門檻的底線不適用。
    """
    used = []
    blocked = None
    if getattr(args, "allow_untested", False):
        used.append("--allow-untested(本輪未執行任何測試)")
    run_mut, _note = mutation_enabled(args)
    rate = getattr(args, "min_kill_rate", None)
    if not run_mut:
        used.append("--no-mutation(本輪未驗測試強度)")
    elif rate is not None and rate < KILL_RATE_FLOOR:
        used.append(f"--min-kill-rate {rate}(低於底線 {KILL_RATE_FLOOR})")
        if ESCAPE_DECLARE_RE not in chg_text:
            blocked = (f"kill rate 門檻 {rate} 低於底線 {KILL_RATE_FLOOR},"
                       f"等同關閉變異閘卻保留『已跑變異測試』的說法。"
                       f"請在 CHG 加一行 `Escape-hatch:` 說明為何必須調低")
    if getattr(args, "no_commit", False):
        used.append("--no-commit(變更未錨定到 commit)")
    return used, blocked


def platform_coverage(declared: str | None) -> tuple[str, list[str]]:
    """回 (本輪實際平台, 宣告了但本輪未涵蓋的平台)。

    runner 沒有跨平台執行能力,誠實的做法是**記錄差集**而不是假裝跑過。
    真正的跨平台由使用者的 CI 提供;這裡只確保差集不會消失在無人聞問處。
    """
    current = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(
        platform.system(), platform.system().lower())
    if not declared:
        return current, []
    want = [p.strip().lower() for p in declared.split(",") if p.strip()]
    return current, [p for p in want if p != current]


# lib/verify.py → parents: [0]=lib [1]=scripts [2]=ai-sdlc-autopilot
# 治理層與執行層自 CHG-20260804-08 起同在一支 skill,static_check 就在隔壁,
# 不必再從 repo 根目錄繞回來——那個繞法也正是搬家時會斷掉的地方。
STATIC_CHECK = Path(__file__).resolve().parents[1] / "static_check.py"


def static_gate(repo: Path, allowlist: str | None = None) -> tuple[bool, str]:
    """對本 task 變更的檔案跑內建靜態/安全檢查(CHG-20260803-06)。

    與變異閘的差別:變異問「測試夠不夠強」,這裡問「程式本身有沒有已知形態的問題」。
    後者不需要執行程式,也不需要測試存在——所以它對**任何**變更都適用,
    包含那些「還沒寫測試」的 task。
    """
    # 檔名是施工者自取的,所以每一個都寫成 `./<路徑>`(V1,CHG-20260926-01):
    # `--paths` 是 nargs="*",一個名叫 `--paths=x.py` 的檔否則會被 argparse 讀成第二個
    # `--paths`、蓋掉整份清單,檢查器什麼都不掃卻回通過。不用 `--` 分隔(接在 nargs="*"
    # 的選項後面行為不直觀),也不用絕對路徑——`Path("./a.py").as_posix()` 仍是 `a.py`,
    # allowlist 的 `<檔案>::<規則>` 鍵照樣對得上;絕對路徑會讓鍵對不上、豁免全數失效。
    files = ["./" + f.relative_to(repo).as_posix() for f in changed_files(repo)
             if f.suffix in (".py", ".sh", ".yml", ".yaml", ".json", ".md", ".toml")]
    if not files:
        return True, "靜態/安全檢查:本 task 無可檢查的變更檔"
    if not STATIC_CHECK.is_file():
        # 檢查器不見了不能當成通過——那正是它要防的失效形態
        return False, f"找不到靜態檢查器({STATIC_CHECK});缺少檢查器不等於沒有問題"
    cmd = [sys.executable, str(STATIC_CHECK), "--repo", str(repo), "--paths", *files]
    if allowlist:
        cmd += ["--allowlist", allowlist]
    # 走共用啟動器(CHG-20260926-01 T2):一個卡住的檢查器同樣受 --process-timeout 限制
    r = run_argv(cmd, repo)
    body = (r.stdout or "") + (r.stderr or "")
    if r.returncode == 0:
        return True, f"靜態/安全檢查通過({len(files)} 個變更檔)"
    return False, "靜態/安全檢查未通過:\n" + body.strip()[:1500]
