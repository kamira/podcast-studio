#!/usr/bin/env python3
"""
ledger_migrate.py — 把治理帳本搬到新落點(CHG-20260811-01 T5)

用途:帳本已經在某處,但落點要換——最常見的原因是 `docs/` 後來被 site generator
佔用(帳本會被建進別人的產品文件),或變成生成產物的目錄(下一次重新生成把帳本
刪掉,而且無聲)。判準與規則見 `references/onboarding.md`。

## 三個刻意的設計

1. **預設 dry-run。** 在別人的 repo 裡搬檔是不可逆方向,所以要看得到才動得了手。
   `--apply` 是明示的動作,不是預設。

2. **只搬我方檔案,認「這是什麼」而不是「在哪個目錄底下」。** 來源目錄底下屬於別人的
   東西(MkDocs 的 `index.md`、`api/`、`assets/`…)**一個都不動**。把整個 `docs/`
   搬走會是最快的寫法,也是最容易把別人的網站搬爛的寫法。

3. **不覆寫、可續跑。** 目的地已存在的路徑一律跳過並具名;中斷後重跑只搬剩下的。
   「重跑一次比較快」不是覆寫別人修正的理由。

## 為什麼用 git mv

搬檔案而不搬歷史,等於把帳本的可追溯性丟掉一半——而可追溯正是帳本存在的理由。
非 git 專案降級為複製 + 刪除,並**明講降級**(降級要看得出來,不是靜靜換一種做法)。

用法:
    python3 ledger_migrate.py --repo . --from docs --to sdlc_docs          # dry-run
    python3 ledger_migrate.py --repo . --from docs --to sdlc_docs --apply

退出碼:0 = 完成(或 dry-run 印出計畫);1 = 有問題;2 = 參數/環境錯誤。
"""
from __future__ import annotations
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# 釘住輸出編碼(與 doc_integrity_check / governance_health 同一慣例)。
# 少了這段的代價實測過:平台預設編碼非 UTF-8 時(這台機器是 cp950),
# 一句含 `❌` 的錯誤訊息會讓整支腳本 `UnicodeEncodeError` 崩掉——
# **本來要告訴使用者「來源不存在」,結果給的是 traceback**。
# `errors="replace"` 確保永不因輸出而崩潰。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# 我方檔案的形狀。**清單是資料**——新增一種治理產物是編輯這裡,不是編輯搬移邏輯。
# 每一項都要能回答「這確實是治理帳本的一部分」,否則就會搬到別人的東西。
OURS_DIRS = ("changes", "acceptance", "structure", "knowledge")
OURS_FILES = ("ai-guideline.md",)


def is_ledger(root: Path) -> bool:
    """來源必須真的是一本帳,不能只是「有個叫 docs 的目錄」。

    判準與 `doc_integrity_check.ledger_roots()` 同源:有 `changes/`,而且裡面
    真的有 `CHG-*.md`。少了後半條,一個空的 `changes/` 會讓這支腳本開始搬
    `structure/`、`knowledge/`——而那些目錄名一般得很,可能屬於別人。
    """
    changes = root / "changes"
    return changes.is_dir() and any(changes.glob("CHG-*.md"))


def plan(src: Path, dst: Path) -> tuple[list[tuple[Path, Path]], list[str], list[str]]:
    """回 (要搬的, 跳過的具名理由, 留在原地的別人的東西)。"""
    moves: list[tuple[Path, Path]] = []
    skipped: list[str] = []
    ours = set(OURS_DIRS) | set(OURS_FILES)
    for name in OURS_DIRS + OURS_FILES:
        s = src / name
        if not s.exists():
            continue
        d = dst / name
        if d.exists():
            # 不覆寫。目的地已有同名 → 具名跳過,由人決定要不要合併。
            skipped.append(f"{name}(目的地已存在,未覆寫)")
            continue
        moves.append((s, d))
    # 別人的東西:來源目錄底下不屬於我方清單的一切。列出來是為了讓「沒動它們」
    # 這件事**看得見**——一份沒有列出留下什麼的搬移報告,無法證明它沒多搬。
    theirs = sorted(p.name for p in src.iterdir() if p.name not in ours) if src.is_dir() else []
    return moves, skipped, theirs


def in_git(repo: Path) -> bool:
    try:
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True)
        return r.returncode == 0 and r.stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def move(repo: Path, s: Path, d: Path, use_git: bool) -> str:
    """回實際用的方式,讓降級在報告裡看得見。"""
    d.parent.mkdir(parents=True, exist_ok=True)
    if use_git:
        r = subprocess.run(["git", "-C", str(repo), "mv", str(s), str(d)],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return "git mv"
        # git mv 失敗(未追蹤的檔案是最常見的原因)→ 降級,但**說出來**。
        shutil.move(str(s), str(d))
        return f"降級為 move(git mv 失敗:{r.stderr.strip()[:80]})"
    shutil.move(str(s), str(d))
    return "move(非 git 專案,降級)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把治理帳本搬到新落點(預設 dry-run)")
    ap.add_argument("--repo", default=".", help="repo 根目錄")
    ap.add_argument("--from", dest="src", required=True, help="來源落點(如 docs)")
    ap.add_argument("--to", dest="dst", required=True, help="目的落點(如 sdlc_docs)")
    ap.add_argument("--apply", action="store_true",
                    help="真的搬。不給就是 dry-run——不可逆方向要看得到才動得了手")
    a = ap.parse_args(argv[1:] if argv else None)

    repo = Path(a.repo).resolve()
    src, dst = (repo / a.src).resolve(), (repo / a.dst).resolve()

    # **兩端都必須留在 repo 內。** `repo / a.src` 對絕對路徑會直接採用那個絕對路徑,
    # 對 `../..` 會走出去——而這支腳本在 `--apply` 下會**搬檔案**。落點是使用者打的字,
    # 打錯一個 `../` 與惡意輸入在程式看來一樣,所以守衛不看意圖、只看結果落在哪。
    for label, p in (("--from", src), ("--to", dst)):
        if p != repo and repo not in p.parents:
            print(f"❌ {label} 指到 repo 之外:{p}")
            print(f"   落點必須在 {repo} 底下——這支腳本會搬檔案,不接受走出去的路徑。")
            return 2

    if not src.is_dir():
        print(f"❌ 來源不存在:{src}")
        return 2
    # 守衛要認**兩邊**,不是只認來源。只認來源的版本被規格當場抓到:搬完之後
    # `docs/` 已經沒有 `changes/`,於是「續跑」被自己的守衛擋成 exit 2——
    # 而中斷留下半套(changes/ 搬了、knowledge/ 沒搬)時,那正是最需要能續跑的時刻。
    # 已經搬到目的地的帳本,就是這次遷移的帳本;來源側剩下的是還沒搬完的部分。
    if not is_ledger(src) and not is_ledger(dst):
        print(f"❌ {src} 不是帳本(需要 changes/ 且其中有 CHG-*.md),"
              f"而 {dst} 也不是。")
        print("   這道檢查擋的是「把別人的 docs/ 當成帳本搬走」——"
              "目錄名一般得很,不能只憑名字判。")
        return 2
    if src.resolve() == dst.resolve():
        print("❌ 來源與目的相同,沒有事情可做。")
        return 2

    moves, skipped, theirs = plan(src, dst)

    print(f"# 帳本遷移計畫:{a.src}/ → {a.dst}/")
    print(f"{'(dry-run —— 沒有任何東西被移動)' if not a.apply else '(--apply:實際搬移)'}\n")
    if moves:
        print("搬移:")
        for s, d in moves:
            print(f"  {s.relative_to(repo).as_posix()} → {d.relative_to(repo).as_posix()}")
    else:
        print("搬移:(無——可能已經全部搬完,見下方跳過清單)")
    if skipped:
        print("\n跳過(不覆寫):")
        for x in skipped:
            print(f"  {x}")
    print(f"\n留在原地({a.src}/ 底下不屬於治理帳本的東西,一個都不動):")
    print("  " + (", ".join(theirs) if theirs else "(無)"))

    if not a.apply:
        print(f"\n確認無誤後加 --apply 實際搬移。")
        return 0

    use_git = in_git(repo)
    if not use_git:
        print("\n⚠️ 非 git 專案(或不在工作樹內)——降級為複製+刪除,歷史不會跟著走。")
    print("")
    for s, d in moves:
        how = move(repo, s, d, use_git)
        print(f"  ✅ {s.relative_to(repo).as_posix()} → {d.relative_to(repo).as_posix()}({how})")
    print(f"\n完成。接下來:更新 Guideline 表頭的落點宣告,並為本次遷移開一筆 CHG。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
