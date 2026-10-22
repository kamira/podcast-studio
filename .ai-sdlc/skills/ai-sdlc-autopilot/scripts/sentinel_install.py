#!/usr/bin/env python3
"""安裝即退出 orchestrator:鋪設哨兵排程再進入,然後退出(休眠=退出,非常駐)。

治理:建立 cron/CI 屬「建立持久設定」——**永遠先 HALT 等人授權**(permanent 停點)。
本檔即使授權,也只**產出可審閱的 re-entry 設定**(crontab 行 + CI workflow 片段),
不逕自寫入系統 crontab 或觸發排程 MCP;真正上線由人放置該設定(留審計面)。

無 --i-authorize-cron  → 印安裝計畫 + HALT(exit 3)。
--dry-run             → 印計畫,不落地(exit 0)。
--i-authorize-cron    → 印計畫 + 產出 re-entry 設定到 stdout(exit 0);人自行放置上線。
"""
import argparse
import sys
from pathlib import Path

# 釘住輸出編碼(CHG-20260803-01 T1):不依賴主控台/locale 的 ambient 編碼。
# 非 UTF-8 主控台(如 Windows cp932)印 CJK/emoji 會 UnicodeEncodeError;
# 釘住後同一份程式在任何平台的輸出行為一致。errors="replace" 確保永不因輸出而崩潰。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

POLL = "skills/ai-sdlc-autopilot/scripts/autopilot_sentinels.py"


def cron_expr(interval_min: int) -> str:
    """週期(分)→合法 cron 五欄。分鐘欄上限 59,故 ≥60 換算為時、≥1440 為每日。"""
    if interval_min < 60:
        return f"*/{max(1, interval_min)} * * * *"
    if interval_min < 1440:
        hours = max(1, interval_min // 60)
        return f"0 */{hours} * * *" if hours < 24 else "0 2 * * *"
    days = interval_min // 1440
    return "0 2 * * *" if days <= 1 else f"0 2 */{min(days, 28)} * *"


def plan_text(repo: str, interval_min: int, chg: str) -> str:
    chg_arg = f" --chg {chg}" if chg else ""
    expr = cron_expr(interval_min)
    cron = f"{expr} cd {repo} && python3 {POLL} poll --repo .{chg_arg} || true"
    ci = (
        "# .github/workflows/autopilot-sentinels.yml (片段)\n"
        "on:\n  schedule:\n"
        f"    - cron: '{expr}'\n"
        "jobs:\n  sentinels:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/checkout@v4\n"
        f"      - run: python3 {POLL} poll --repo .{chg_arg}\n"
        "        # exit 3 = B 層真 halt → job 失敗、通知人;exit 0 = 基線可續/A 層降級\n"
    )
    return (
        "[sentinel-install] 安裝即退出 orchestrator 計畫:\n"
        f"  - 週期:每 {interval_min} 分鐘喚起一次(排程尾遞迴;達 max_reentry 即 base case 停)\n"
        f"  - 動作:{POLL} poll(兩層跳脫:A→exit0 基線 / B→exit3 升級人)\n"
        f"  - 主 agent 裝完即退出(不常駐)\n\n"
        f"--- crontab 行 ---\n{cron}\n\n--- CI workflow ---\n{ci}"
    )


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="哨兵排程安裝器(安裝即退出;建 cron 需授權)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("install")
    p.add_argument("--repo", default=".")
    p.add_argument("--interval-min", type=int, default=30)
    p.add_argument("--chg", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--i-authorize-cron", action="store_true",
                   help="人明確授權建立持久排程設定(permanent 停點;預設 HALT)")
    args = ap.parse_args(argv[1:])
    if args.cmd != "install":
        return 2

    plan = plan_text(str(Path(args.repo)), args.interval_min, args.chg)
    print(plan)
    if args.dry_run:
        print("\n[sentinel-install] --dry-run:僅列計畫,未產出設定 (exit 0)")
        return 0
    if not args.i_authorize_cron:
        print("\n❌ HALT:建立 cron/CI 屬『建立持久設定』,需人明確授權。\n"
              "   確認後以 --i-authorize-cron 產出設定(仍不逕自寫 crontab);或 --dry-run 只看計畫。 (exit 3)")
        return 3
    print("\n✅ 已授權:上方 re-entry 設定可由人放置上線(crontab 或 .github/workflows/)。\n"
          "   本器不逕自寫入系統排程,保留審計面。 (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
