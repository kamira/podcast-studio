"""commit_check.py 的測試:在暫存目錄建立假 repo,不動全域 git 設定。"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ci" / "commit_check.py"

# 隔離全域 / 系統 git 設定,只用暫存 repo 的 local config
GIT_ENV = dict(os.environ)
GIT_ENV.update({
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "HOME": tempfile.gettempdir(),
})


class FakeRepo:
    def __init__(self, path: Path):
        self.path = path
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "測試者")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args: str) -> str:
        proc = subprocess.run(["git", "-C", str(self.path), *args], env=GIT_ENV,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return proc.stdout.decode("utf-8").strip()

    def commit(self, msg: str, files: dict) -> str:
        for rel, content in files.items():
            p = self.path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            self.git("add", rel)
        self.git("commit", "-q", "-m", msg)
        return self.head()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")


def run(*args: str):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], env=GIT_ENV,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.returncode, proc.stdout.decode("utf-8") + proc.stderr.decode("utf-8")


class CommitCheckTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = FakeRepo(Path(self._tmp.name))
        self.base = self.repo.commit("CHG-20261022-03 初始", {"app.py": "print(1)\n"})

    def tearDown(self):
        self._tmp.cleanup()

    def check(self, *extra: str):
        return run("--repo", str(self.repo.path), "--range",
                   "{}..{}".format(self.base, self.repo.head()), *extra)

    def test_with_chg_passes(self):
        self.repo.commit("CHG-20261022-03 T2: 改程式", {"app.py": "print(2)\n"})
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def test_chg_in_body_passes_pr_mode(self):
        self.repo.commit("改程式\n\n引用 CHG-20261022-03", {"app.py": "print(2)\n"})
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def test_knowledge_only_without_chg_passes(self):
        self.repo.commit("knowledge: 新增 KN-9", {"docs/knowledge/KN-9.md": "規則\n"})
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def test_code_without_chg_fails(self):
        sha = self.repo.commit("隨手修一下", {"app.py": "print(3)\n"})
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn(sha[:7], out)
        self.assertIn("隨手修一下", out)
        self.assertIn("app.py", out)

    def test_knowledge_plus_code_without_chg_fails(self):
        self.repo.commit("混合", {"docs/knowledge/KN-9.md": "x\n", "app.py": "y\n"})
        code, out = self.check()
        self.assertEqual(code, 1, out)

    def test_merge_commit_skipped(self):
        self.repo.git("switch", "-q", "-c", "feature")
        self.repo.commit("CHG-20261022-03 分支改動", {"b.py": "b\n"})
        self.repo.git("switch", "-q", "main")
        self.repo.commit("CHG-20261022-03 主線改動", {"a.py": "a\n"})
        self.repo.git("merge", "-q", "--no-ff", "-m", "Merge branch feature", "feature")
        self.assertEqual(len(self.repo.git("rev-list", "--parents", "-n", "1", "HEAD").split()), 3)
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def test_push_mode_squash_title_with_chg_passes(self):
        self.repo.commit("CHG-20261022-03 T2: commit 引用檢查 (#7)\n\n* 細節", {"app.py": "p\n"})
        code, out = self.check("--mode", "push")
        self.assertEqual(code, 0, out)

    def test_push_mode_requires_chg_in_title(self):
        sha = self.repo.commit("commit 引用檢查 (#7)\n\n* CHG-20261022-03 只在內文", {"app.py": "p\n"})
        code, out = self.check("--mode", "push")
        self.assertEqual(code, 1, out)
        self.assertIn(sha[:7], out)
        self.assertIn("標題行", out)

    def test_push_mode_knowledge_only_passes(self):
        self.repo.commit("knowledge: KN-2 (#8)", {"docs/knowledge/KN-2.md": "k\n"})
        code, out = self.check("--mode", "push")
        self.assertEqual(code, 0, out)

    def test_push_mode_only_checks_head(self):
        self.repo.commit("沒有編號的舊 commit", {"app.py": "old\n"})
        self.repo.commit("CHG-20261022-03 squash 標題", {"app.py": "new\n"})
        code, out = self.check("--mode", "push")
        self.assertEqual(code, 0, out)

    def test_empty_range_passes(self):
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def test_bad_range_argument_exit_2(self):
        code, out = run("--repo", str(self.repo.path), "--range", "沒有點點")
        self.assertEqual(code, 2, out)

    def test_missing_range_argument_exit_2(self):
        code, out = run("--repo", str(self.repo.path))
        self.assertEqual(code, 2, out)

    def test_unknown_ref_exit_2(self):
        code, out = run("--repo", str(self.repo.path), "--range", "nope..HEAD")
        self.assertEqual(code, 2, out)


if __name__ == "__main__":
    unittest.main()
