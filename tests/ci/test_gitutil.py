"""scripts/ci/gitutil.py 的測試(CHG-20261103-01 T2):在暫存目錄建立假 repo,不動全域 git 設定。

L1:`parse_range`(嚴格版)、`run_git` 的 quotepath 旗標與回傳型別、`GitError` 訊息、`git_text`。
L2:三支 CI 腳本取自 `gitutil` 的是同一個 `GitError` 類別物件;secret_scan 的 `git` 帶
quotepath 旗標(非 ASCII 檔名回原字),commit_check / plan_check_changed 的 `git` 不帶。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

CI_DIR = Path(__file__).resolve().parents[2] / "scripts" / "ci"

sys.path.insert(0, str(CI_DIR))
try:
    import commit_check
    import gitutil
    import plan_check_changed
    import secret_scan
finally:
    sys.path.remove(str(CI_DIR))

# 隔離全域 / 系統 git 設定,只用暫存 repo 的 local config
GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "HOME": tempfile.gettempdir(),
}

NON_ASCII = "測試檔.txt"
ESCAPED = '"' + "".join("\\{:03o}".format(b) for b in "測試檔".encode("utf-8")) + '.txt"'


class _RepoCase(unittest.TestCase):
    """建立含一個非 ASCII 檔名追蹤檔的暫存 repo;測試期間 os.environ 套用 GIT_ENV。"""

    def setUp(self):
        patcher = mock.patch.dict(os.environ, GIT_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.name", "測試者")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "commit.gpgsign", "false")
        (self.repo / NON_ASCII).write_text("x\n", encoding="utf-8")
        self._git("add", NON_ASCII)
        self._git("commit", "-q", "-m", "初始")

    def _git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.repo), *args],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


class ParseRangeTest(unittest.TestCase):
    def test_valid_range(self):
        self.assertEqual(gitutil.parse_range("a..b"), ("a", "b"))

    def test_invalid_ranges_return_none(self):
        for text in ("nodots", "a...b", "..b", "a..", "-x..b", "a..-x"):
            with self.subTest(text=text):
                self.assertIsNone(gitutil.parse_range(text))


class RunGitTest(_RepoCase):
    def test_default_quotes_non_ascii_path(self):
        out = gitutil.run_git(self.repo, "ls-files")
        self.assertIsInstance(out, bytes)
        self.assertEqual(out.decode("ascii").strip(), ESCAPED)

    def test_quotepath_false_returns_raw_utf8(self):
        out = gitutil.run_git(self.repo, "ls-files", quotepath=False)
        self.assertIsInstance(out, bytes)
        self.assertEqual(out.strip(), NON_ASCII.encode("utf-8"))

    def test_failure_raises_git_error_without_config_flag(self):
        for quotepath in (True, False):
            with self.subTest(quotepath=quotepath):
                with self.assertRaises(gitutil.GitError) as cm:
                    gitutil.run_git(self.repo, "ls-files", "--bogus", quotepath=quotepath)
                msg = str(cm.exception)
                self.assertTrue(msg.startswith("git ls-files --bogus 失敗:"), msg)
                self.assertNotIn("core.quotepath", msg)

    def test_git_text_returns_decoded_str(self):
        text = gitutil.git_text(self.repo, "ls-files")
        self.assertIsInstance(text, str)
        self.assertEqual(text, gitutil.run_git(self.repo, "ls-files").decode("utf-8", "replace"))


class ScriptsShareGitutilTest(_RepoCase):
    def test_git_error_is_same_class(self):
        self.assertIs(commit_check.GitError, gitutil.GitError)
        self.assertIs(plan_check_changed.GitError, gitutil.GitError)
        self.assertIs(secret_scan.GitError, gitutil.GitError)

    def test_secret_scan_git_uses_quotepath_false(self):
        out = secret_scan.git(self.repo, "ls-files")
        self.assertIsInstance(out, bytes)
        self.assertEqual(out.strip(), NON_ASCII.encode("utf-8"))

    def test_commit_check_and_plan_check_git_keep_quoting(self):
        for mod in (commit_check, plan_check_changed):
            with self.subTest(module=mod.__name__):
                out = mod.git(self.repo, "ls-files")
                self.assertIsInstance(out, str)
                self.assertEqual(out.strip(), ESCAPED)


if __name__ == "__main__":
    unittest.main()
