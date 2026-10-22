#!/usr/bin/env python3
"""驗證器完整性錨(CHG-20260803-02 T11)。stdlib-only、無 LLM、三平台一致。

**問題**:所有閘門都由程式執行,而 agent 有寫檔權限。它只要把 `require_test_command`
從前置條件的 tuple 裡拿掉、把 kill rate 門檻改成 0、或讓 `run_tests.sh` 在找不到測試時回綠,
整套驗證就會在**看起來全綠**的狀態下失效。驗證器是唯一不能只靠「它自己說它沒事」的東西。

**做法**:把驗證器清單(`assets/verifier_manifest.json`)裡每個檔案的 SHA-256 錨定在
`assets/verifier_integrity.json`。任何改動都會讓 `--check` 變紅,而回填基線需要
**明文指定 CHG 編號**——修改驗證器因此不再是一次靜默編輯,而是一筆有署名的治理紀錄。

這不是防惡意攻擊(有寫檔權限的人終究能一併改基線),而是**提高成本、消滅「不小心」與
「順手」**:偽造驗證從「改一行」變成「改一行 + 跑回填 + 在 CHG 寫明為什麼 + 過 review」。

用法:
  python3 verifier_integrity.py --repo . --check
  python3 verifier_integrity.py --repo . --update --chg CHG-YYYYMMDD-NN --why "理由"
  python3 verifier_integrity.py --repo . --list

退出碼:0 = 一致 / 回填成功;1 = 偵測到未經宣告的改動;2 = 設定或用法錯誤。
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
MANIFEST = HERE.parent / "assets" / "verifier_manifest.json"
BASELINE = HERE.parent / "assets" / "verifier_integrity.json"
CHG_PAT = "CHG-"


def sha256(p: Path) -> str:
    """以正規化行尾計算雜湊——否則 Windows 的 CRLF 會讓每個檔案在 checkout 後都『被改過』。"""
    data = p.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def load_manifest(repo: Path) -> list[str]:
    m = MANIFEST if MANIFEST.is_file() else (repo / "skills/ai-sdlc-autopilot/assets/verifier_manifest.json")
    return json.loads(m.read_text(encoding="utf-8-sig"))["files"]


def in_scope(prefix: str, files: list[str] | None = None, repo: Path | None = None) -> bool:
    """某一層是否屬於本 repo 的檢查範圍——以**簽過名的清單**判定,不以檔案在不在判定。

    用「檔案存在與否」判定範圍,等於把**刪掉檔案**變成合法的離場方式,
    而「把閘門拔掉」正是 `test_gates_wired.py` 存在的唯一理由。
    清單受 SHA-256 完整性錨定,移除條目需要 `--update --chg` 具名授權,
    所以離場一定留痕(CHG-20260804-10)。

    以 `/` 結尾的前綴才做目錄比對,否則 `plugins-legacy/` 會被 `plugins` 命中。
    """
    listing = files if files is not None else load_manifest(repo or Path("."))
    return any(f.startswith(prefix) for f in listing)


def out_of_scope_note(layer: str, skipped: int) -> str:
    """「不適用」的說明。刻意**不含**任何肯定式的通過措辭(KN-001)。

    印出**條數**而非只說「已略過」:條數是可稽核的量——單層 repo 應該少 12 條,
    不是少 40 條;只說「略過」看不出被略掉多少。
    """
    return (f"⊘ {layer}:不在本 repo 的驗證器清單內,{skipped} 條接線斷言**不適用**"
            f"(不適用是永久結論,與「未涵蓋」及「驗過了」都不同)")


def compute(repo: Path, files: list[str]) -> tuple[dict, list[str]]:
    out, missing = {}, []
    for rel in files:
        p = repo / rel
        if not p.is_file():
            missing.append(rel)
            continue
        out[rel] = sha256(p)
    return out, missing


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--chg", default=None, help="回填基線時必填:哪一筆 CHG 授權了這次驗證器改動")
    ap.add_argument("--why", default="", help="回填時的一句理由(寫進基線檔供追溯)")
    ap.add_argument("--baseline", default=None)
    args = ap.parse_args(argv[1:])

    repo = Path(args.repo).resolve()
    baseline_path = Path(args.baseline) if args.baseline else BASELINE
    try:
        files = load_manifest(repo)
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
        print(f"❌ 讀不到驗證器清單:{e}")
        return 2

    if args.list:
        print(f"驗證器清單({len(files)} 個檔案):")
        for f in files:
            print(f"  {f}")
        return 0

    current, missing = compute(repo, files)

    if args.update:
        if not args.chg or CHG_PAT not in args.chg:
            print("❌ 回填基線必須指定 --chg CHG-YYYYMMDD-NN。")
            print("   修改驗證器是治理事件,不是實作細節:沒有 CHG 就沒有授權。")
            return 2
        if missing:
            print(f"❌ 清單列了但找不到的檔案:{', '.join(missing)}(先修正清單)")
            return 2
        baseline_path.write_text(json.dumps(
            {"version": 1,
             "_doc": "驗證器雜湊基線(生成物)。以 verifier_integrity.py --update 回填,"
                     "必須指定授權的 CHG。手改本檔等同繞過治理。",
             "authorised_by": args.chg, "why": args.why,
             "hashes": current}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"✅ 已回填 {len(current)} 個驗證器檔案的基線(授權:{args.chg})")
        return 0

    # 預設即 --check
    if not baseline_path.is_file():
        print(f"❌ 找不到基線 {baseline_path.name} — 尚未錨定。")
        print("   請跑:verifier_integrity.py --update --chg <CHG 編號> --why <理由>")
        return 1
    try:
        base = json.loads(baseline_path.read_text(encoding="utf-8-sig"))["hashes"]
    except (json.JSONDecodeError, KeyError) as e:
        print(f"❌ 基線檔損毀({e})— 視同不一致,不得放行")
        return 1

    changed = [f for f in current if f in base and current[f] != base[f]]
    added = [f for f in current if f not in base]
    removed = [f for f in base if f not in current]

    if missing:
        print(f"❌ 驗證器檔案消失:{', '.join(missing)}")
        print("   刪掉驗證器與讓它回綠,效果完全相同。")
        return 1
    if changed or added or removed:
        print("❌ 驗證器已被修改,但基線未經授權更新:")
        for f in changed:
            print(f"   [改動] {f}")
        for f in added:
            print(f"   [新增] {f}")
        for f in removed:
            print(f"   [自清單移除] {f}")
        print(f"\n   授權方式:先在 CHG 寫明 `Verifier-change:` 欄與理由(風險分級至少為中),")
        print(f"   再跑 verifier_integrity.py --update --chg <編號> --why <理由>。")
        print(f"   目前基線授權者:{json.loads(baseline_path.read_text(encoding='utf-8-sig')).get('authorised_by')}")
        return 1

    print(f"✅ 驗證器完整性一致({len(current)} 個檔案;基線授權:"
          f"{json.loads(baseline_path.read_text(encoding='utf-8-sig')).get('authorised_by')})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
