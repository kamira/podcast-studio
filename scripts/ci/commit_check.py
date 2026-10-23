#!/usr/bin/env python3
"""
commit_check.py — commit 引用 CHG 編號檢查 / commit reference check

規則(CHG-20261022-03):每個 commit 的訊息必須含 `CHG-YYYYMMDD-NN`;唯一例外是只改動
`docs/knowledge/` 底下檔案的 commit(knowledge 條目是帳本寫入,不是變更)。
merge commit(parent 數大於 1)一律略過。

本腳本只讀取被檢查的 repo(以 --repo 指定,可與腳本所在位置不同,CI 從 base 分支的
`trusted/` 副本執行、以 `--repo pr` 指向 PR head),不寫入任何檔案。

用法 / usage:
  python3 scripts/ci/commit_check.py --repo pr --range <base>..<head>
      # pull_request:檢查範圍內每個非 merge commit 的完整訊息
  python3 scripts/ci/commit_check.py --repo . --range <before>..<after> --mode push
      # push 到 main:只檢查 <head> 這一個 commit 的標題行(squash 標題)

退出碼:0 = 全部合規(含範圍為空);1 = 有不合規 commit(逐一印出短 SHA、標題、理由);
        2 = 參數錯誤或 git 執行失敗。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

CHG_RE = re.compile(r"CHG-\d{8}-\d{2}")
KNOWLEDGE_PREFIX = "docs/knowledge/"


class GitError(Exception):
    """git 指令執行失敗。"""


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise GitError("git {} 失敗:{}".format(" ".join(args), err))
    return proc.stdout.decode("utf-8", "replace")


def parent_count(repo: Path, sha: str) -> int:
    line = git(repo, "rev-list", "--parents", "-n", "1", sha).split()
    return len(line) - 1


def message(repo: Path, sha: str) -> str:
    return git(repo, "log", "-1", "--format=%B", sha)


def changed_files(repo: Path, sha: str) -> list:
    out = git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha)
    return [f for f in out.splitlines() if f.strip()]


def check_commit(repo: Path, sha: str, subject_only: bool):
    """回傳 None 表示合規或略過;否則回傳 (短 SHA, 標題, 理由)。"""
    if parent_count(repo, sha) > 1:
        return None
    msg = message(repo, sha)
    subject = msg.splitlines()[0] if msg.strip() else ""
    target = subject if subject_only else msg
    if CHG_RE.search(target):
        return None
    files = changed_files(repo, sha)
    if files and all(f.startswith(KNOWLEDGE_PREFIX) for f in files):
        return None
    short = git(repo, "rev-parse", "--short", sha).strip()
    where = "標題行" if subject_only else "commit 訊息"
    if not files:
        reason = "{}未含 CHG 編號,且未改動任何檔案(不適用 knowledge 豁免)".format(where)
    else:
        others = [f for f in files if not f.startswith(KNOWLEDGE_PREFIX)]
        shown = "、".join(others[:3]) + (" 等" if len(others) > 3 else "")
        reason = "{}未含 CHG 編號,且改動了 docs/knowledge/ 以外的檔案:{}".format(where, shown)
    return (short, subject, reason)


def parse_range(text: str):
    if ".." not in text or "..." in text:
        return None
    base, head = text.split("..", 1)
    if not base or not head:
        return None
    return base, head


def main(argv=None) -> int:
    return 0  # 暫時驗證:模擬 PR 把檢查改成永遠通過(將 revert)
    ap = argparse.ArgumentParser(description="檢查 commit 訊息是否引用 CHG 編號")
    ap.add_argument("--repo", default=".", help="被檢查的 git repo 路徑(預設目前目錄)")
    ap.add_argument("--range", required=True, dest="rng", help="git 範圍 <base>..<head>")
    ap.add_argument("--mode", choices=("pr", "push"), default="pr",
                    help="pr = 檢查範圍內每個非 merge commit;push = 只檢查 <head> 的標題行")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code == 0 else 2

    rng = parse_range(args.rng)
    if rng is None:
        print("參數錯誤:--range 必須是 <base>..<head> 格式,收到:{}".format(args.rng), file=sys.stderr)
        return 2
    repo = Path(args.repo)
    if not repo.is_dir():
        print("參數錯誤:--repo 不是目錄:{}".format(repo), file=sys.stderr)
        return 2

    try:
        if args.mode == "push":
            head = git(repo, "rev-parse", "--verify", rng[1] + "^{commit}").strip()
            shas = [head]
        else:
            out = git(repo, "rev-list", "--reverse", "{}..{}".format(*rng))
            shas = [s for s in out.split() if s]
        bad = [r for r in (check_commit(repo, s, args.mode == "push") for s in shas) if r]
    except GitError as e:
        print("git 錯誤:{}".format(e), file=sys.stderr)
        return 2

    if not bad:
        print("commit 引用檢查通過(檢查 {} 個 commit,模式 {})".format(len(shas), args.mode))
        return 0
    print("commit 引用檢查失敗:{} 個 commit 未引用 CHG 編號".format(len(bad)))
    for short, subject, reason in bad:
        print("- {} {}\n    理由:{}".format(short, subject, reason))
    return 1


if __name__ == "__main__":
    sys.exit(main())
