#!/usr/bin/env python3
"""
autopilot_runner.py — ai-sdlc-autopilot 驅動器(狀態機與裁判;不含 LLM)

施工與審查由外部 headless agent 指令執行(--agent-cmd,模板中 {brief} 代入簡報檔路徑);
runner 只負責:解析計畫、裁決停點、驅動角色單元、打勾與 commit、維護 live handshake。

角色可單獨呼叫(拆指令**非治理繞道**:每個角色走同一 halt policy 與同一帳本,
前置條件缺失一律 halt);`run` 由這些角色組合而成。

用法:
  python3 autopilot_runner.py plan-check --chg <CHG.md>        # = plan
  python3 autopilot_runner.py plan   --chg <CHG.md>
  python3 autopilot_runner.py build  --chg <CHG.md> --repo . [--single] [--agent-cmd T] [--test-cmd C] [--dry-run]
  python3 autopilot_runner.py review --chg <CHG.md> --repo . [--review-cmd R] [--confidence-threshold N]
  python3 autopilot_runner.py verify --chg <CHG.md> --repo . [--verify-cmd V]
  python3 autopilot_runner.py accept --chg <CHG.md> --repo . [--verified]
  python3 autopilot_runner.py run    --chg <CHG.md> --repo . [...]
  (build / review / verify / accept / run 另接 [--process-timeout SECONDS]:每個子行程的上限,
   3600 秒(預設)。子行程的 stdin 是空的、在自己的行程群組裡;上限到期或 runner 被中斷時整組結束)
  python3 autopilot_runner.py status --chg <CHG.md>
  python3 autopilot_runner.py sentinels --repo . [--chg <CHG.md>] [--reentry-count N]

退出碼(本段是退出碼的權威來源;CHG-20260926-01 T14):
  0   = 完成
  1   = runner 自身的非預期錯誤(含讀不到 assets/halt_policy.json)
  2   = 計畫無效(含不合格的 `- feature:` 路徑:verify 對所有 CHG 不執行,plan-check 對宣告 v1.67.0 以上的
        CHG 判無效);argparse 的用法錯誤(例如 review --seat-cmd)也是 2
  3   = 合法停點,原因已印出(cron/CI 據此接線;會通知人的碼)。含:施工者的六種停點(點名本 task
        的 [blocked] 行、逾時、非零退出——126 / 127 另寫成 --agent-cmd 設定錯誤、自行 commit、零變更、
        改了交接檔或 CHG)、審查席或交叉讀沒有回答(含判定互相矛盾、交叉讀缺列)、git commit 失敗、
        --seat-cmd 的席名不存在或指令為空、確認閘 / 永遠停點 / merge 待人
  129 / 130 / 143 = runner 被 SIGHUP / SIGINT / SIGTERM 中斷(先結束派出的行程群組;已被忽略的訊號不接手)
"""
from __future__ import annotations
import argparse
import signal
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import roles  # noqa: E402
from lib.exec_util import (DEFAULT_PROCESS_TIMEOUT, PolicyUnavailable, die,  # noqa: E402
                           set_process_timeout)

# 釘住輸出編碼(CHG-20260803-01 T1):不依賴主控台/locale 的 ambient 編碼。
# 非 UTF-8 主控台(如 Windows cp932)印 CJK/emoji 會 UnicodeEncodeError;
# 釘住後同一份程式在任何平台的輸出行為一致。errors="replace" 確保永不因輸出而崩潰。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# 角色名 → 實作。plan-check 為 plan 的相容別名。
ROLES = {
    "plan": roles.role_plan,
    "plan-check": roles.role_plan,
    "build": roles.role_build,
    "review": roles.role_review,
    "verify": roles.role_verify,
    "accept": roles.role_accept,
    "run": roles.role_run,
    "status": roles.role_status,
}
NEEDS_REPO = ("build", "review", "verify", "accept", "run")


def cmd_sentinels(args) -> int:
    """drive 層委派:呼叫 autopilot_sentinels.py poll(兩層跳脫 + 排程尾遞迴)。"""
    here = Path(__file__).resolve().parent
    cmd = [sys.executable, str(here / "autopilot_sentinels.py"), "poll", "--repo", args.repo]
    for flag, val in (("--chg", args.chg), ("--policy", args.policy)):
        if val:
            cmd += [flag, val]
    if args.reentry_count:
        cmd += ["--reentry-count", str(args.reentry_count)]
    return subprocess.run(cmd).returncode


def _raise_on_termination() -> None:
    """SIGTERM / SIGHUP / SIGINT → 以 143 / 129 / 130 結束(CHG-20260926-01 T2)。

    以 SystemExit 往上拋,共用啟動器的 `except BaseException` 會先收掉派出去的整個行程群組,
    施工階段也會在 handshake 記下「被中斷、未打勾」。已被設為忽略的訊號**不接手**:
    `nohup` 把 SIGHUP 設成忽略,無條件覆蓋會讓登出殺掉整個執行。
    """
    def _exit(signum, _frame):
        raise SystemExit(128 + signum)

    for name in ("SIGTERM", "SIGHUP", "SIGINT"):
        sig = getattr(signal, name, None)      # Windows 沒有 SIGHUP
        if sig is None or signal.getsignal(sig) == signal.SIG_IGN:
            continue
        signal.signal(sig, _exit)


def _seconds(text: str) -> int:
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"必須是整數秒(得 {text!r})") from None
    if n < 1:
        raise argparse.ArgumentTypeError(f"下限 1 秒(得 {n});沒有「不設上限」的值")
    return n


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="ai-sdlc-autopilot runner(角色單元 + 組合)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("sentinels", help="確定性哨兵輪詢(兩層跳脫)")
    ps.add_argument("--repo", default=".")
    ps.add_argument("--chg", default=None)
    ps.add_argument("--policy", default=None)
    ps.add_argument("--reentry-count", type=int, default=0)

    for name in ROLES:
        p = sub.add_parser(name)
        p.add_argument("--chg", required=True)
        p.add_argument("--policy", default=None, help="autopilot_policy.json 路徑(預設內建矩陣)")
        p.add_argument("--interaction-kinds", default=None,
                       help="互動驗證分類表路徑(預設 assets/interaction_kinds.json);"
                            "可指向自己的表以增補種類")
        if name in NEEDS_REPO:
            p.add_argument("--repo", default=".")
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--confirmed", action="store_true", help="人已於確認閘核可")
            # CHG-20260926-01 T2:無人值守時,一個卡住的子行程會讓整個執行永遠停著
            p.add_argument("--process-timeout", type=_seconds, default=DEFAULT_PROCESS_TIMEOUT,
                           metavar="SECONDS",
                           help="每個子行程(施工者、審查者、測試/驗收指令、各道閘)的時間上限,"
                                f"{DEFAULT_PROCESS_TIMEOUT} 秒(預設),下限 1。這是給卡死行程的上限,"
                                "不是預估;到期時整個行程群組被終止")
        # review 也需要審查指令:整支 branch 的末端審查已不再是 no-op(CHG-20260803-02 T6),
        # 它必須真的把 diff 交給一個模型看過。
        if name in ("build", "run", "review"):
            p.add_argument("--agent-cmd", default=None, help="headless agent 指令模板,{brief}=簡報檔路徑")
        # 審查面板(CHG-20260803-09):席次由風險分級決定,低風險維持單席快速路徑。
        # 只掛在 build / run:整支 review 是單一審查者,c5af2cc 的 review 接受這兩個旗標卻從未讀過
        # (CHG-20260926-01 T9,NEW-8,代決 R2)。
        if name in ("build", "run"):
            p.add_argument("--review-panel", type=int, default=None,
                           help="審查面板席次(可往上加,不得低於風險分級的下限:低 1 / 中 3 / 高 3);"
                                "上限是座位表 assets/review_seats.json 的席數(出廠 3 席),"
                                "超過時照上限開並印註記。整支 review 不開面板")
            p.add_argument("--seat-cmd", action="append", default=None, metavar="SEAT=CMD",
                           help="逐席指定審查指令(可重複),如 --seat-cmd conformance='<指令> {brief}';"
                                "席名取自 assets/review_seats.json ( conformance / defect / idiom ),"
                                "未知的席名或空的指令在任何 agent 執行前 exit 3。"
                                "未指定的席沿用 --review-cmd 並印出同模型警語;低風險單一審查者不用它")
        if name in ("build", "run", "review"):
            p.add_argument("--confidence-threshold", type=int, default=None,
                           help="判定行的信心門檻(預設 80);低於此值的判定降級為 cannot-verify,"
                                "**不會**被拿去平均。逐 task 審查與整支 review 都套用")
            p.add_argument("--review-cmd", default=None,
                           help="審查用的指令模板(可指向與施工不同的模型);未給則回退 --agent-cmd 並註明「同模型」限制")
        if name in ("build", "run"):
            p.add_argument("--test-cmd", default=None, help="每個 task 的單元/build 測試指令")
            p.add_argument("--static-allowlist", default=None,
                           help="靜態/安全檢查的具名豁免清單(預設 ai-sdlc/assets/static_allowlist.json)")
            p.add_argument("--allow-untested", action="store_true",
                           help="明示允許不執行任何測試(agent 寫的程式碼將未經執行即打勾);"
                                "僅適用純文件 task,會印警告並留痕")
            # CHG-20260803-07:變異閘改為**預設開啟**。缺 --test-cmd 會 halt(存在性強制),
            # 而唯一檢驗「測試夠不夠強」的閘卻要人記得加旗標——這個排序反了。
            p.add_argument("--mutation", action="store_true",
                           help="(相容保留,已無作用)變異閘自 v1.10.0 起預設開啟")
            p.add_argument("--no-mutation", action="store_true",
                           help="明示關閉變異閘(測試強度本輪不驗);會印警告並留痕,須記入 ACC 的未涵蓋欄")
            # CHG-20260803-08:測試棘輪與不穩定測試偵測的兩個逃生口。
            p.add_argument("--allow-test-reduction", action="store_true",
                           help="明示允許本次變更淨減少測試/斷言數(正當的測試重構);"
                                "會印警告並留痕,須記入 ACC 的未涵蓋欄")
            p.add_argument("--flaky-runs", type=int, default=None,
                           help="單元測試轉綠後在同一份程式碼上總共跑幾次(預設 2,下限 1);"
                                "設為 1 等同關閉不穩定測試偵測,會留痕")
            p.add_argument("--max-fix-rounds", type=int, default=None,
                           help="單一 task 的回修輪數上限(預設 3,下限 1);"
                                "每一輪都會把上一輪的失敗原文帶進 brief")
            p.add_argument("--escalate-cmd", default=None,
                           help="最後一輪改用的施工指令(通常指向更強的模型);"
                                "未給則沿用 --agent-cmd 並註明「同模型升階只換提示不換盲點」")
            p.add_argument("--min-kill-rate", type=float, default=90.0,
                           help="變異閘門檻(預設 90);低於此值視同該 task 未通過。"
                                "設到 60 以下,須在 CHG 加一行 Escape-hatch: 說明理由,否則 halt"
                                "(不論有沒有 --mutation)")
            p.add_argument("--mutation-max", type=int, default=40,
                           help="每個檔案的變異體取樣上限(0=不設限);丟棄數會如實回報")
            p.add_argument("--test-platforms", default=None,
                           help="宣告本變更的測試應涵蓋哪些平台(逗號分隔,如 linux,macos,windows);"
                                "runner 不代跑,只記錄差集供 ACC 標注未涵蓋")
            p.add_argument("--no-commit", action="store_true")
            p.add_argument("--max-tasks", type=int, default=0)
        if name == "build":
            p.add_argument("--single", action="store_true", help="只做下一個未完成 task")
        if name in ("verify", "run"):
            p.add_argument("--verify-cmd", default=None, help="末端操作測試指令(把變更真的跑一次)")
        # 信任旗標掛在會執行 CHG 內容所宣告之指令的階段(CHG-20260803-05;CHG-20260806-04 起
        # accept 的本機閘也是其中之一)。build 沒有這種指令:它唯一帶 CHG 文字的外部呼叫是
        # git commit 的訊息,以 argv 傳,不經 shell。verify 的 `- feature:` 路徑同樣來自 CHG,
        # 執行前先驗格式(CHG-20260926-01 T3、T4)。
        if name in ("verify", "run", "accept"):
            p.add_argument("--trust-chg-commands", action="store_true",
                           help="明示信任 CHG 檔案內宣告的互動驗證指令並執行之。"
                                "預設不執行——那是內容驅動執行:能讓 CHG 進到 repo 的人"
                                "就能讓 autopilot 跑任意 shell 指令")
            p.add_argument("--interaction-cmd", default=None,
                           help="覆寫 CHG 宣告的互動驗證指令(供 CI 注入);產物檢查仍照 CHG 宣告")
            p.add_argument("--nonfunctional-kinds", default=None,
                           help="非功能性驗證的分類表(效能/負載/併發/資源/可重現/授權/合約/視覺/屬性);"
                                "適用範圍依 .ai-sdlc/profile.json 宣告的專案型態決定")
            p.add_argument("--quality-baseline", default=None,
                           help="委派驗證的基線檔(預設 autopilot/assets/quality_baseline.json);"
                                "基線內的發現放行(每條須具名理由),新增的一律擋,且基線只准往下")
            p.add_argument("--quality-kinds", default=None,
                           help="委派型驗證的分類表(型別/覆蓋率/SAST/相依漏洞);可指向自訂表")
            p.add_argument("--gherkin-cmd", default=None,
                           help="行為規格執行指令(預設 `behave --strict`);"
                                "CHG 的 ### Behaviour spec 宣告的每個 .feature 都會被跑一次")
        if name in ("accept", "run"):
            p.add_argument("--pr", default=None, help="PR 編號(供 CI 閘查詢;未給則由 gh 依分支推斷)")
            p.add_argument("--ci-cmd", default=None,
                           help="自訂 CI 狀態查詢指令,須輸出 [{name,bucket}] 的 JSON;"
                                "非 GitHub CI 用。查不到狀態一律不准合併(fail-closed)")
            p.add_argument("--allow-no-ci", action="store_true",
                           help="明示此專案沒有任何 CI 檢查(逃生口,會留痕);"
                                "「沒有檢查」與「檢查都通過」不是同一件事")
            # 合併前的本機自主檢查閘(CHG-20260806-04)。**只掛在 accept/run**——
            # 它是 merge 路徑上的閘,掛到 build 上等於把它變成別的東西。
            p.add_argument("--local-gate-cmd", default=None,
                           help="合併前在本機跑的自主檢查指令(操作者提供,優先於 CHG 宣告);"
                                "未給時依序找 CHG 的 ### 本機閘、專案的慣例載具"
                                "(.github/ci_local.sh / scripts/check.sh / make check / just check)")
            p.add_argument("--allow-no-local-gate", action="store_true",
                           help="明示略過合併前的本機自主檢查(逃生口,會留痕);"
                                "「沒有跑過」與「跑過而且通過」不是同一件事")
        if name == "accept":
            p.add_argument("--verified", action="store_true",
                           help="聲明操作驗收已通過(證據須入 ACC);缺此且未跑 verify → halt")

    args = ap.parse_args(argv[1:])
    _raise_on_termination()
    if getattr(args, "process_timeout", None) is not None:
        set_process_timeout(args.process_timeout)
    if args.cmd == "sentinels":
        return cmd_sentinels(args)
    try:
        ctx = roles.Ctx(args)
    except ValueError as e:
        return die(str(e), 1)
    except FileNotFoundError as e:
        return die(str(e), 1)
    # build 明確分開呼叫:ROLES 是異質 callable 的 dict,mypy 只能取共同簽章,
    # 於是 fn(ctx, single=True) 被判為多餘參數——而 role_build 確實收 single。
    # 把特例寫成特例,比讓型別檢查器對整張表放水好。
    # 讀不到永遠停點清單(CHG-20260926-01 T5):施工者契約不以較短的清單代替,runner 自身錯誤 → 1
    try:
        if args.cmd == "build":
            return roles.role_build(ctx, single=bool(getattr(args, "single", False)))
        return ROLES[args.cmd](ctx)
    except PolicyUnavailable as e:
        return die(str(e), 1)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
