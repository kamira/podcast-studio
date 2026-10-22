#!/usr/bin/env python3
"""
role_loadout.py — 依角色列出該載入的 references / per-role reference loadout

把「依職責載入子集」從散文變成程式可讀:給定角色(+ 偵測到的情境旗標),讀 assets/role_refs.json
回傳該角色要載入的 references 清單(基本集 + 情境追加),供外部協調器/agent 啟動時據以載入。

用法 / usage:
  python3 role_loadout.py --role verifier
  python3 role_loadout.py --role I1 --multi-repo --cicd        # 接受別名 + 情境旗標
  python3 role_loadout.py --role orchestrator --json           # 輸出 JSON 供程式取用
  python3 role_loadout.py --list                               # 列出所有角色

情境旗標:--multi-repo / --parallel / --autonomous / --cicd(對應 situational)。
退出碼:0 正常;2 角色不存在或設定錯誤。
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

FLAG_TO_KEY = {
    "multi_repo": "multi_repo",
    "multi_branch": "multi_branch",
    "parallel": "parallel_or_handoff",
    "autonomous": "autonomous_run",
    "cicd": "cicd",
}


def load_cfg(path: str | None) -> dict:
    for c in [Path(path) if path else None,
              Path(__file__).resolve().parent.parent / "assets" / "role_refs.json"]:
        if c and c.is_file():
            return json.loads(c.read_text(encoding="utf-8"))
    raise FileNotFoundError("找不到 role_refs.json")


def resolve(cfg: dict, role: str) -> str:
    role = role.strip()
    if role in cfg.get("roles", {}):
        return role
    return cfg.get("aliases", {}).get(role, role)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role")
    ap.add_argument("--multi-repo", action="store_true")
    ap.add_argument("--multi-branch", action="store_true")
    ap.add_argument("--parallel", action="store_true")
    ap.add_argument("--autonomous", action="store_true")
    ap.add_argument("--cicd", action="store_true")
    ap.add_argument("--policy", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv[1:])
    try:
        cfg = load_cfg(args.policy)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"❌ {e}"); return 2

    if args.list or not args.role:
        roles = list(cfg.get("roles", {}))
        print("角色:", ", ".join(roles))
        print("別名:", ", ".join(f"{k}={v}" for k, v in cfg.get("aliases", {}).items()))
        return 0

    role = resolve(cfg, args.role)
    if role not in cfg.get("roles", {}):
        print(f"❌ 未知角色:{args.role}(可用:{', '.join(cfg['roles'])})"); return 2

    refs = []
    for r in list(cfg.get("common", [])) + list(cfg["roles"][role]):
        if r not in refs:
            refs.append(r)
    sit = cfg.get("situational", {})
    active = []
    for flag, on in [("multi_repo", args.multi_repo), ("multi_branch", args.multi_branch),
                     ("parallel", args.parallel), ("autonomous", args.autonomous), ("cicd", args.cicd)]:
        if on:
            for r in sit.get(FLAG_TO_KEY[flag], []):
                if r not in refs:
                    refs.append(r); active.append((flag, r))

    if args.json:
        print(json.dumps({"role": role, "load": refs}, ensure_ascii=False))
    else:
        print(f"角色 {role} 應載入 references:")
        for r in refs:
            print(f"  - references/{r}.md")
        if active:
            print("(其中情境追加:" + ", ".join(f"{f}→{r}" for f, r in active) + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
