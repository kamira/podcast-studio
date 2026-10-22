#!/usr/bin/env python3
"""
cross_repo_check.py — 跨 repo 指標漂移檢查 / cross-repo pointer drift check

把 cross-repo 指引的「指標版本一致」變成可執行檢查:比對每個消費 repo 在
docs/authority.md 釘住的版本,與權威 repo 目前的契約版本是否一致。落後 = 跨 repo 漂移。

慣例(convention):
- 權威目前契約版本寫在  <authority>/docs/contracts/VERSION   例如一行 "v3"
- 每個消費 repo 的指標寫在  <repo>/docs/authority.md          含一行
      - 釘住版本: v3 ...        或      - Pinned version: v3 ...

用法 / usage:
  # 以 manifest 指定(建議):
  python cross_repo_check.py manifest.json
  # 或用參數:
  python cross_repo_check.py --authority ./authority-repo --repos ./repoA ./repoB

manifest.json:
  { "authority": "authority-repo", "repos": ["repoA", "repoB"] }

退出碼:0 = 全部一致;1 = 偵測到漂移;2 = 設定/檔案錯誤。可直接接 pre-commit 或 CI。
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

# 釘住輸出編碼(CHG-20260803-01 T1):不依賴主控台/locale 的 ambient 編碼。
# 非 UTF-8 主控台(如 Windows cp932)印 CJK/emoji 會 UnicodeEncodeError;
# 釘住後同一份程式在任何平台的輸出行為一致。errors="replace" 確保永不因輸出而崩潰。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

VERSION_TOKEN = re.compile(r"v\d+(?:\.\d+)*", re.IGNORECASE)
PINNED_LINE = re.compile(r"(釘住版本|pinned version)\s*[:：]\s*(.+)", re.IGNORECASE)


def read_authority_version(authority: Path) -> str:
    f = authority / "docs" / "contracts" / "VERSION"
    if not f.is_file():
        raise FileNotFoundError(f"找不到權威版本檔 / authority VERSION not found: {f}")
    m = VERSION_TOKEN.search(f.read_text(encoding="utf-8"))
    if not m:
        raise ValueError(f"權威 VERSION 內找不到版本號(如 v3): {f}")
    return m.group(0).lower()


def read_repo_pinned(repo: Path) -> str | None:
    f = repo / "docs" / "authority.md"
    if not f.is_file():
        return None  # 此 repo 未宣告指標(可能不參與跨 repo 契約)
    for line in f.read_text(encoding="utf-8").splitlines():
        pm = PINNED_LINE.search(line)
        if pm:
            vm = VERSION_TOKEN.search(pm.group(2))
            if vm:
                return vm.group(0).lower()
    return None


def main(argv: list[str]) -> int:
    authority = None
    repos: list[str] = []
    args = argv[1:]
    if args and not args[0].startswith("--"):
        cfg = json.loads(Path(args[0]).read_text(encoding="utf-8"))
        authority = cfg.get("authority")
        repos = cfg.get("repos", [])
        base = Path(args[0]).resolve().parent
    else:
        base = Path.cwd()
        if "--authority" in args:
            authority = args[args.index("--authority") + 1]
        if "--repos" in args:
            repos = args[args.index("--repos") + 1:]
    if not authority or not repos:
        print("用法: cross_repo_check.py manifest.json  或  --authority <path> --repos <p1> <p2>...")
        return 2

    auth_path = (base / authority).resolve()
    try:
        auth_ver = read_authority_version(auth_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"❌ {e}")
        return 2

    print(f"權威契約版本 / authority contract version: {auth_ver}  ({auth_path})\n")
    drift = 0
    missing = 0
    for r in repos:
        rp = (base / r).resolve()
        pinned = read_repo_pinned(rp)
        if pinned is None:
            print(f"  ⚠️  {r}: 無 docs/authority.md 指標(略過 / no pointer, skipped)")
            missing += 1
        elif pinned == auth_ver:
            print(f"  ✅ {r}: {pinned} == {auth_ver}  一致")
        else:
            print(f"  ❌ {r}: 釘住 {pinned} != 權威 {auth_ver}  跨 repo 漂移(該 repo 還在用舊契約)")
            drift += 1

    print()
    if drift:
        print(f"偵測到 {drift} 個 repo 跨 repo 漂移——請更新其指標並依 XCHG 跟進契約變更。")
        return 1
    print("全部 repo 指標與權威契約版本一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
