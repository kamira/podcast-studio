"""
gitutil.py — CI 腳本共用的 git 執行與範圍解析 / shared git helpers for CI scripts

用途(CHG-20261103-01):`commit_check.py`、`plan_check_changed.py`、`secret_scan.py` 共用的
git 執行函式、錯誤類別與 `--range` 解析,只維護一份。

只用 Python 標準函式庫,不 import repo 內其他模組。CI 以 `python3 trusted/scripts/ci/<x>.py`
直接執行腳本時,`sys.path[0]` 是腳本所在的 `trusted/scripts/ci`,本模組因此從 base 分支的
trusted 副本同目錄載入,PR 內容無法替換它;腳本本身不操作 `sys.path`。

介面 / interface:
  GitError                                      git 指令執行失敗(訊息「git <args> 失敗:<stderr>」)
  run_git(repo, *args, quotepath=True) -> bytes quotepath=False 時加 `-c core.quotepath=false`
  git_text(repo, *args) -> str                  run_git 預設旗標,stdout 以 utf-8 replace 解碼
  parse_range(text) -> (base, head) | None      嚴格版:拒絕 `...`、空端點與 `-` 開頭的端點
"""
from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(Exception):
    """git 指令執行失敗。"""


def run_git(repo: Path, *args: str, quotepath: bool = True) -> bytes:
    cmd = ["git", "-C", str(repo)]
    if not quotepath:
        cmd += ["-c", "core.quotepath=false"]
    proc = subprocess.run(
        [*cmd, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise GitError("git {} 失敗:{}".format(" ".join(args), err))
    return proc.stdout


def git_text(repo: Path, *args: str) -> str:
    return run_git(repo, *args).decode("utf-8", "replace")


def parse_range(text: str):
    if ".." not in text or "..." in text:
        return None
    base, head = text.split("..", 1)
    if not base or not head or base.startswith("-") or head.startswith("-"):
        return None
    return base, head
