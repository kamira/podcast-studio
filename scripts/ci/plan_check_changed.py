#!/usr/bin/env python3
"""
plan_check_changed.py — PR 改到的 CHG 做 plan-check / plan-check changed CHGs

規則(CHG-20261022-03 T3):
1. 找出 <base>...<head> 範圍內新增或修改的 `docs/changes/CHG-*.md`(刪除的不檢查)。
2. 依各 CHG「## 狀態」節的內容決定處理方式——只讀該節(從標題到下一個 `## ` 標題或檔尾),
   不對全檔比對(避免 KN-1 那種整份檔案子字串誤判):
   - 狀態節含「已驗收」或「accepted」(不分大小寫)→ 驗收完成,略過;
   - 狀態節含「計畫待補」→ 不跑 plan-check,只印警告;
   - 其他 → 執行 `python3 <runner> plan-check --chg <檔案>`,exit 非 0 即不合格(附上 runner 原始輸出)。
3. 範圍內每個 commit 訊息出現的 `CHG-YYYYMMDD-NN`,必須在 <head> 的 `docs/changes/` 有對應檔案。

--repo 是被檢查的 repo(CI 中為 PR head 的 checkout,只當資料);--runner 是 vendored
`autopilot_runner.py` 的位置,預設為本腳本所在 repo 的副本(CI 從 base 分支的 `trusted/` 執行)。
CHG 內容由 --repo 的工作目錄讀取,因此該工作目錄必須就是 <head>;若與 <head> 的內容不一致即視為
參數錯誤。本腳本不寫入任何檔案;子程序一律以 argv list 執行。

用法 / usage:
  python3 scripts/ci/plan_check_changed.py --repo pr --range <base>..<head> [--runner <路徑>]

退出碼:0 = 全部通過(含只有警告、範圍內沒有 CHG 改動);
        1 = 有 CHG 不合格,或 commit 訊息引用了不存在的 CHG 編號;
        2 = 參數錯誤、git 執行失敗或找不到 runner。
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
CHG_FILE_RE = re.compile(r"^docs/changes/CHG-[^/]*\.md$")
STATUS_HEADING_RE = re.compile(r"^##\s+狀態\s*$")
ACCEPTED_RE = re.compile(r"已驗收|accepted", re.IGNORECASE)
PLAN_PENDING = "計畫待補"
DEFAULT_RUNNER = (Path(__file__).resolve().parents[2] / ".ai-sdlc" / "skills"
                  / "ai-sdlc-autopilot" / "scripts" / "autopilot_runner.py")


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


def status_section(text: str) -> str:
    """回傳「## 狀態」節的內容(不含標題行);找不到該節回傳空字串。"""
    lines = text.splitlines()
    out = []
    inside = False
    for line in lines:
        if inside:
            if line.startswith("## "):
                break
            out.append(line)
        elif STATUS_HEADING_RE.match(line):
            inside = True
    return "\n".join(out)


def classify(text: str) -> str:
    """依狀態節判定:'accepted' / 'plan-pending' / 'check'。"""
    section = status_section(text)
    if ACCEPTED_RE.search(section):
        return "accepted"
    if PLAN_PENDING in section:
        return "plan-pending"
    return "check"


def parse_range(text: str):
    if ".." not in text or "..." in text:
        return None
    base, head = text.split("..", 1)
    if not base or not head:
        return None
    return base, head


def changed_chg_files(repo: Path, base: str, head: str) -> list:
    out = git(repo, "diff", "--name-only", "--diff-filter=AM",
              "{}...{}".format(base, head), "--", "docs/changes/")
    return sorted(f for f in out.splitlines() if CHG_FILE_RE.match(f.strip()))


def referenced_ids(repo: Path, base: str, head: str) -> dict:
    """回傳 {CHG 編號: [引用它的短 SHA, ...]}。"""
    out = git(repo, "log", "--format=%h%x00%B%x1e", "{}..{}".format(base, head))
    refs = {}
    for record in out.split("\x1e"):
        if "\x00" not in record:
            continue
        short, body = record.split("\x00", 1)
        short = short.strip()
        for cid in sorted(set(CHG_RE.findall(body))):
            refs.setdefault(cid, []).append(short)
    return refs


def existing_ids(repo: Path, head: str) -> set:
    out = git(repo, "ls-tree", "--name-only", head, "docs/changes/")
    ids = set()
    for name in out.splitlines():
        m = re.match(r"^docs/changes/(CHG-\d{8}-\d{2})\.md$", name.strip())
        if m:
            ids.add(m.group(1))
    return ids


def run_plan_check(runner: Path, repo: Path, chg: Path):
    proc = subprocess.run(
        [sys.executable, str(runner), "plan-check", "--chg", str(chg)],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="對範圍內新增 / 修改的 CHG 執行 plan-check")
    ap.add_argument("--repo", default=".", help="被檢查的 git repo 路徑(工作目錄須為 <head>)")
    ap.add_argument("--range", required=True, dest="rng", help="git 範圍 <base>..<head>")
    ap.add_argument("--runner", default=str(DEFAULT_RUNNER),
                    help="vendored autopilot_runner.py 的路徑(預設為本腳本所在 repo 的副本)")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code == 0 else 2

    rng = parse_range(args.rng)
    if rng is None:
        print("參數錯誤:--range 必須是 <base>..<head> 格式,收到:{}".format(args.rng), file=sys.stderr)
        return 2
    base, head = rng
    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print("參數錯誤:--repo 不是目錄:{}".format(repo), file=sys.stderr)
        return 2
    runner = Path(args.runner).resolve()
    if not runner.is_file():
        print("找不到 runner:{}".format(runner), file=sys.stderr)
        return 2

    try:
        head_sha = git(repo, "rev-parse", "--verify", head + "^{commit}").strip()
        git(repo, "rev-parse", "--verify", base + "^{commit}")
        files = changed_chg_files(repo, base, head_sha)
        refs = referenced_ids(repo, base, head_sha)
        existing = existing_ids(repo, head_sha)
        contents = {}
        for rel in files:
            path = repo / rel
            blob = git(repo, "show", "{}:{}".format(head_sha, rel))
            if not path.is_file() or path.read_bytes().decode("utf-8", "replace") != blob:
                print("參數錯誤:--repo 工作目錄的 {} 與 {} 的內容不一致;請先 checkout <head>".format(
                    rel, head), file=sys.stderr)
                return 2
            contents[rel] = blob
    except GitError as e:
        print("git 錯誤:{}".format(e), file=sys.stderr)
        return 2

    failures = 0
    warnings = 0
    missing = {cid: shas for cid, shas in refs.items() if cid not in existing}
    if missing:
        print("commit 訊息引用了 docs/changes/ 不存在的 CHG 編號:")
        for cid in sorted(missing):
            print("- {}(出現在 commit {})".format(cid, "、".join(missing[cid])))
        failures += len(missing)

    if not files:
        print("範圍內沒有新增或修改的 CHG,略過 plan-check")
    for rel in files:
        kind = classify(contents[rel])
        if kind == "accepted":
            print("略過 {}:狀態為驗收完成".format(rel))
            continue
        if kind == "plan-pending":
            print("警告:{} 狀態為「{}」,不執行 plan-check".format(rel, PLAN_PENDING))
            warnings += 1
            continue
        code, out = run_plan_check(runner, repo, repo / rel)
        if code == 0:
            print("通過 {}:plan-check exit 0".format(rel))
        else:
            print("不合格 {}:plan-check exit {}".format(rel, code))
            print(out.rstrip())
            failures += 1

    if failures:
        print("plan-check 檢查失敗:{} 項不合格,{} 項警告".format(failures, warnings))
        return 1
    print("plan-check 檢查通過:{} 個 CHG,{} 項警告".format(len(files), warnings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
