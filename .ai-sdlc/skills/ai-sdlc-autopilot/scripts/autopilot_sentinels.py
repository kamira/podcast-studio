#!/usr/bin/env python3
"""autopilot 確定性哨兵 poller + 兩層跳脫(drive 層)。

常態需求確認的執行單元:對「需求/結構/變更/驗收」跑既有 ai-sdlc 確定性 check,
依結果分兩層——
  A 無法評估(check 不可用/啟動失敗)→ exit 0,退回基線線性流程(fail-open,log)。
  B 真 halt(check 跑了且旗標問題)→ exit 3,升級給人(不 fall-through)。
「什麼算真 halt」的治理語意錨定 ai-sdlc references/autonomy.md;本檔只做 drive。

排程尾遞迴:由 cron/scheduled-task 週期喚起本檔;--reentry-count 達 max_reentry
即 base case 命中,停止再進入(exit 0)。無 LLM、stdlib-only。

Exit: 0 = 基線可續 / 已停(base case) / A 層降級;3 = B 層真 halt,升級人;2 = 用法錯誤。
"""
import argparse
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

# 釘住輸出編碼(CHG-20260803-01 T1):不依賴主控台/locale 的 ambient 編碼。
# 非 UTF-8 主控台(如 Windows cp932)印 CJK/emoji 會 UnicodeEncodeError;
# 釘住後同一份程式在任何平台的輸出行為一致。errors="replace" 確保永不因輸出而崩潰。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_POLICY = Path(__file__).resolve().parents[1] / "assets" / "sentinel_policy.json"

# 啟動失敗(Tier A,無法評估)vs check 跑了且旗標問題(Tier B,真 halt)的判別。
#
# 退出碼嗅探在 Windows 上**不可靠**(CHG-20260803-01 T3 實測):
#   POSIX sh          127  command not found
#   Windows cmd.exe     1  'x' is not recognized as an internal or external command
#                          ← 與「check 真的跑了、回 1」完全無法區分
#   Windows cmd.exe  9009  App Execution Alias 存在但無法執行(如未安裝的 python3 stub)
# 因為 1 無法區分,單靠退出碼在 Windows 上必然誤判其中一邊。故改為**執行前先解析可執行檔**:
# 指令的第一個 token 解析不到 → 確定是啟動失敗 → Tier A,與平台無關。
# 退出碼僅作為補充訊號(能解析卻仍回 127/9009 的情形,如 alias stub)。
LAUNCH_FAIL = frozenset({127, 9009})


def _first_token(cmd: str):
    """取指令的第一個 token(去掉外層引號);解析不出回 None。"""
    try:
        toks = shlex.split(cmd, posix=False)
    except ValueError:
        return None
    return toks[0].strip('"\'') if toks else None


def launchable(cmd: str) -> bool:
    """第一個 token 是否為可解析的可執行檔。解析不出指令時保守回 True(交給實跑判定)。"""
    tok = _first_token(cmd)
    if not tok:
        return True
    return Path(tok).exists() or shutil.which(tok) is not None


def load_policy(path):
    p = Path(path) if path else DEFAULT_POLICY
    # utf-8-sig(CHG-20260803-01 T3):Windows 工具(記事本、PowerShell 的
    # Set-Content -Encoding UTF8)預設寫入 BOM。以純 utf-8 讀會拋例外 → 政策載入失敗
    # → 整組哨兵靜默降級為 A 層、永遠不再回報任何東西。這正是 KN-001 的失效形狀。
    # utf-8-sig 對無 BOM 的檔案行為與 utf-8 完全相同,故 POSIX 側零變化。
    return json.loads(p.read_text(encoding="utf-8-sig"))


def run_check(cmd: str, repo: Path):
    """回 (tier, detail):tier ∈ {'ok','a','b'}。"""
    if not launchable(cmd):
        return "a", f"啟動失敗:指令不存在({_first_token(cmd)})→ cannot evaluate"
    try:
        r = subprocess.run(cmd, shell=True, cwd=str(repo), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120)
    except FileNotFoundError:
        return "a", "launch failed (not found)"
    except Exception as e:  # noqa: BLE001 — 任何啟動層失敗都當「無法評估」
        return "a", f"cannot evaluate: {e}"
    if r.returncode == 0:
        return "ok", "pass"
    if r.returncode in LAUNCH_FAIL:
        return "a", f"exit {r.returncode} (啟動失敗:指令不存在 → cannot evaluate)"
    tail = (r.stdout or r.stderr or "").strip().splitlines()
    return "b", tail[-1] if tail else f"exit {r.returncode}"


def poll(args) -> int:
    try:
        policy = load_policy(args.policy)
    except Exception as e:  # noqa: BLE001
        print(f"[sentinels] 無法載入政策({e})→ A 層降級,退回基線 (exit 0)")
        return 0

    reentry = policy.get("reentry", {})
    max_re = int(reentry.get("max_reentry", 20))
    if args.reentry_count >= max_re:
        print(f"[sentinels] base case: 達再進入上限 {args.reentry_count}/{max_re} → 停止再進入 (exit 0);升級人檢視")
        return 0

    repo = Path(args.repo).resolve()
    # {python} = 執行期直譯器(CHG-20260803-01 T3)。政策不得寫死 `python3`——
    # Windows 無此指令,cmd 回 9009,整組 check 會全數變成「啟動失敗」。
    # 雙引號在 sh 與 cmd.exe 皆有效,容納含空白的安裝路徑。
    subs = {"chg": args.chg or "", "repo": str(repo), "gate": args.gate, "risk": args.risk,
            "python": f'"{sys.executable}"'}
    tier_b: list = []
    tier_a: list = []
    ok: list = []
    for stage, spec in policy.get("sentinels", {}).items():
        raw = spec.get("cmd", "")
        # 需 CHG 脈絡者(明示 requires_chg,或 cmd 帶 {chg}):無 --chg 時記 A 層略過。
        # 否則會以預設 risk/gate 做恆真查詢,讓排程恆紅、B 層訊號變噪音。
        if not args.chg and (spec.get("requires_chg") or "{chg}" in raw):
            tier_a.append((stage, "略過:無 CHG 脈絡(需 --chg)"))
            continue
        cmd = raw.format(**subs)
        tier, detail = run_check(cmd, repo)
        (ok if tier == "ok" else tier_a if tier == "a" else tier_b).append((stage, detail))

    for stage, d in ok:
        print(f"  [OK] {stage}: {d}")
    for stage, d in tier_a:
        print(f"  [A:無法評估] {stage}: {d} → 不阻擋")
    for stage, d in tier_b:
        print(f"  [B:真 halt] {stage}: {d}")

    if tier_b:
        print(f"❌ B 層:{len(tier_b)} 個 check 旗標真 halt → 升級人 (exit 3);不 fall-through")
        return 3
    if tier_a:
        print(f"⚠️ A 層:{len(tier_a)} 個 check 無法評估 → 退回基線線性流程 (exit 0);已 log")
        return 0
    print("✅ 哨兵全過 → 基線可續 (exit 0)")
    return 0


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="autopilot 確定性哨兵 poller(兩層跳脫 + 排程尾遞迴)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("poll")
    p.add_argument("--repo", default=".")
    p.add_argument("--chg", default=None)
    p.add_argument("--policy", default=None)
    p.add_argument("--reentry-count", type=int, default=0)
    p.add_argument("--gate", default="before_merge_or_release")
    p.add_argument("--risk", default="medium")
    args = ap.parse_args(argv[1:])
    if args.cmd == "poll":
        return poll(args)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
