#!/usr/bin/env python3
"""
halt_gate.py — 自主執行的停點契約查詢 / autonomy halt-point gate

把「跑到哪該停下等人」從『外部 Python 自己看 Risk 推斷』變成『讀契約』。外部協調器在每個流程
關卡(gate)呼叫本工具,傳入本次變更的 Risk(與可選的具體 action),得到 AUTO(自主續跑)或
HALT(停下等人核准)。契約在 assets/halt_policy.json(可編輯);本檔內建同樣的預設,確保獨立可用。

用法 / usage:
  python3 halt_gate.py --gate before_merge_or_release --risk high
  python3 halt_gate.py --gate before_implement --risk medium --action "data migration"
  python3 halt_gate.py --gate before_implement --risk low --autonomy halt   # CHG 覆寫(加嚴)
  # gate 之一:requirement_confirmed | structure_confirmed | before_implement |
  #            acceptance_failed | before_merge_or_release

輸出:印出 AUTO 或 HALT(可加 --why 看原因)。退出碼:0 = AUTO;10 = HALT;2 = 參數錯誤。
外部用法範例:  python3 halt_gate.py --gate G --risk R || await_human_approval
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

# 釘住輸出編碼(CHG-20260803-01 T1):不依賴主控台/locale 的 ambient 編碼。
# 非 UTF-8 主控台(如 Windows cp932)印 CJK/emoji 會 UnicodeEncodeError;
# 釘住後同一份程式在任何平台的輸出行為一致。errors="replace" 確保永不因輸出而崩潰。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_POLICY = {
    "gates": {
        "requirement_confirmed": {"low": "auto", "medium": "auto", "high": "halt"},
        "structure_confirmed":   {"low": "auto", "medium": "auto", "high": "halt"},
        "before_implement":      {"low": "auto", "medium": "auto", "high": "halt"},
        "acceptance_failed":     {"low": "auto", "medium": "halt", "high": "halt"},
        "before_merge_or_release": {"low": "auto", "medium": "halt", "high": "halt"},
    },
    "always_halt_actions": [
        "deploy", "release", "migration", "irreversible", "delete", "drop table",
        "hard delete", "money", "financial", "secret", "credential", "access control",
        "permission", "publish",
    ],
}


def load_policy(path: str | None) -> dict:
    candidates = []
    if path:
        candidates.append(Path(path))
    candidates.append(Path(__file__).resolve().parent.parent / "assets" / "halt_policy.json")
    for c in candidates:
        if c.is_file():
            try:
                # utf-8-sig(CHG-20260803-01 T6):Windows 工具(記事本、PowerShell
                # Set-Content -Encoding UTF8)預設寫 BOM。以純 utf-8 讀會拋 JSONDecodeError,
                # 而下方 except 會**靜默**回退內建預設——操作者以為自己加嚴過的停點矩陣生效了,
                # 實際上完全沒有。無 BOM 的檔案行為與 utf-8 完全相同,故 POSIX 側零變化。
                p = json.loads(c.read_text(encoding="utf-8-sig"))
                p.setdefault("gates", DEFAULT_POLICY["gates"])
                p.setdefault("always_halt_actions", DEFAULT_POLICY["always_halt_actions"])
                return p
            except json.JSONDecodeError:
                pass
    return DEFAULT_POLICY


def decide(policy: dict, gate: str, risk: str | None,
           action: str | None, autonomy: str | None):
    risk = (risk or "high").lower()
    # always-halt actions(動作字串命中任一關鍵字)
    if action:
        al = action.lower()
        # 支援 policy 內較長的描述句:取其關鍵詞做包含比對
        keys = []
        for a in policy.get("always_halt_actions", []):
            keys += [w.strip() for w in a.replace("/", " ").split() if len(w.strip()) > 2]
        for k in set(keys):
            if k.lower() in al:
                return "HALT", f"always-halt action 命中:'{action}'(關鍵字 {k})"
    base = policy.get("gates", {}).get(gate, {}).get(risk)
    if base is None:
        return "HALT", f"未知 gate/risk({gate}/{risk})→ 保守停點"
    decision = base.upper()
    # CHG 覆寫:只准加嚴(auto→halt),放寬需人預先核准(此處不自動放寬)
    if autonomy:
        a = autonomy.lower()
        if a == "halt":
            return "HALT", "CHG Autonomy 覆寫:halt(加嚴)"
        if a == "auto" and decision == "HALT":
            return "HALT", "CHG 想放寬為 auto,但放寬高風險停點需人預先核准 → 仍 HALT(請人確認)"
    return decision, f"policy[{gate}][{risk}] = {base}"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", required=True)
    ap.add_argument("--risk", default="high")
    ap.add_argument("--action", default=None)
    ap.add_argument("--autonomy", default=None, help="CHG Autonomy 覆寫:auto|halt")
    ap.add_argument("--policy", default=None)
    ap.add_argument("--why", action="store_true")
    args = ap.parse_args(argv[1:])
    decision, why = decide(load_policy(args.policy), args.gate, args.risk, args.action, args.autonomy)
    print(decision + (f"  ({why})" if args.why else ""))
    return 0 if decision == "AUTO" else 10


if __name__ == "__main__":
    sys.exit(main(sys.argv))
