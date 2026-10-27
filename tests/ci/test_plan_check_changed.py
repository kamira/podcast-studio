"""plan_check_changed.py 的測試:在暫存目錄建立假 repo,不動全域 git 設定。

plan-check 的部分用假 runner(CHG 內容含「BAD-PLAN」時 exit 2,否則 exit 0)取代;
另有一個整合測試直接用 repo 內真的 vendored runner。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci" / "plan_check_changed.py"
REAL_RUNNER = (ROOT / ".ai-sdlc" / "skills" / "ai-sdlc-autopilot" / "scripts"
               / "autopilot_runner.py")

# 隔離全域 / 系統 git 設定,只用暫存 repo 的 local config
GIT_ENV = dict(os.environ)
GIT_ENV.update({
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "HOME": tempfile.gettempdir(),
})

FAKE_RUNNER = '''\
import sys
args = sys.argv[1:]
assert args[0] == "plan-check" and args[1] == "--chg", args
text = open(args[2], encoding="utf-8").read()
if "BAD-PLAN" in text:
    print("假 runner:計畫格式不合格")
    sys.exit(2)
print("假 runner:通過")
sys.exit(0)
'''


def chg(status: str, body: str = "") -> str:
    return "# CHG\n\n## 狀態\n\n{}\n\n## 內容\n\n{}\n".format(status, body)


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

    def commit(self, msg: str, files: dict = None, remove: tuple = ()) -> str:
        for rel, content in (files or {}).items():
            p = self.path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            self.git("add", rel)
        for rel in remove:
            self.git("rm", "-q", rel)
        self.git("commit", "-q", "--allow-empty", "-m", msg)
        return self.head()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")


def run(*args: str):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], env=GIT_ENV,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.returncode, proc.stdout.decode("utf-8") + proc.stderr.decode("utf-8")


class PlanCheckChangedTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        (tmp / "repo").mkdir()
        self.repo = FakeRepo(tmp / "repo")
        self.runner = tmp / "fake_runner.py"
        self.runner.write_text(FAKE_RUNNER, encoding="utf-8")
        self.base = self.repo.commit("CHG-20261022-01 初始", {
            "docs/changes/CHG-20261022-01.md": chg("草稿"),
            "app.py": "print(1)\n",
        })

    def tearDown(self):
        self._tmp.cleanup()

    def check(self, runner: Path = None):
        return run("--repo", str(self.repo.path),
                   "--range", "{}..{}".format(self.base, self.repo.head()),
                   "--runner", str(runner or self.runner))

    def test_reference_to_missing_chg_fails(self):
        self.repo.commit("CHG-20261022-99 改程式", {"app.py": "print(2)\n"})
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("CHG-20261022-99", out)

    def test_reference_to_existing_chg_passes(self):
        self.repo.commit("CHG-20261022-01 改程式", {"app.py": "print(2)\n"})
        code, out = self.check()
        self.assertEqual(code, 0, out)

    def test_bad_plan_fails_with_runner_output(self):
        self.repo.commit("CHG-20261022-02 新增", {
            "docs/changes/CHG-20261022-02.md": chg("草稿", "BAD-PLAN")})
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("CHG-20261022-02.md", out)
        self.assertIn("假 runner:計畫格式不合格", out)

    def test_good_plan_passes(self):
        self.repo.commit("CHG-20261022-02 新增", {
            "docs/changes/CHG-20261022-02.md": chg("草稿")})
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("通過 docs/changes/CHG-20261022-02.md", out)

    def test_accepted_chg_skipped(self):
        for status in ("已驗收(見 ACC-1)", "Accepted"):
            with self.subTest(status=status):
                self.repo.commit("CHG-20261022-01 收尾", {
                    "docs/changes/CHG-20261022-01.md": chg(status, "BAD-PLAN " + status)})
                code, out = self.check()
                self.assertEqual(code, 0, out)
                self.assertIn("略過", out)

    def test_plan_pending_only_warns(self):
        self.repo.commit("CHG-20261022-02 新增", {
            "docs/changes/CHG-20261022-02.md": chg("草稿——計畫待補", "BAD-PLAN")})
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("警告", out)
        self.assertNotIn("假 runner", out)

    def test_deleted_chg_not_checked(self):
        self.repo.commit("CHG-20261022-02 新增", {
            "docs/changes/CHG-20261022-02.md": chg("草稿", "BAD-PLAN")})
        self.base = self.repo.head()
        self.repo.commit("CHG-20261022-01 移除 02", remove=("docs/changes/CHG-20261022-02.md",))
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertNotIn("CHG-20261022-02.md", out)

    def test_no_chg_change_passes(self):
        self.repo.commit("CHG-20261022-01 改程式", {"app.py": "print(3)\n"})
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("沒有新增或修改的 CHG", out)

    def test_missing_runner_exit_2(self):
        self.repo.commit("CHG-20261022-01 改程式", {"app.py": "print(3)\n"})
        code, out = self.check(runner=Path(self._tmp.name) / "no_such_runner.py")
        self.assertEqual(code, 2, out)

    def test_bad_range_exit_2(self):
        code, out = run("--repo", str(self.repo.path), "--range", "abc",
                        "--runner", str(self.runner))
        self.assertEqual(code, 2, out)

    def test_accepted_word_outside_status_section_ignored(self):
        """KN-1 回歸:狀態節以外出現「已驗收」不得讓 CHG 被略過。"""
        self.repo.commit("CHG-20261022-02 新增", {
            "docs/changes/CHG-20261022-02.md":
                "# CHG 已驗收 accepted\n\n## 狀態\n\n草稿\n\n## 規則\n\n"
                "略過狀態為已驗收者;accepted 也一樣。計畫待補\nBAD-PLAN\n"})
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("不合格 docs/changes/CHG-20261022-02.md", out)

    def test_status_section_parsing(self):
        sys.path.insert(0, str(SCRIPT.parent))
        try:
            import plan_check_changed as m
        finally:
            sys.path.pop(0)
        text = "前言 已驗收\n## 狀態\n草稿\n### 子節\n計畫待補\n## 其他\n已驗收\n"
        self.assertEqual(m.status_section(text), "草稿\n### 子節\n計畫待補")
        self.assertEqual(m.classify(text), "plan-pending")
        self.assertEqual(m.classify("## 其他\n已驗收\n"), "check")


@unittest.skipUnless(REAL_RUNNER.is_file(), "repo 內沒有 vendored runner")
class RealRunnerIntegrationTest(unittest.TestCase):
    def test_real_chg01_passes(self):
        src = (ROOT / "docs" / "changes" / "CHG-20261022-01.md").read_text(encoding="utf-8")
        # CHG-01 已收尾(狀態為驗收完成),runner 會略過;把狀態換回草稿,讓 runner 真的檢查計畫格式
        src = src[:src.rindex("\n## 狀態")] + "\n## 狀態\n\n草稿(測試用)\n"
        with tempfile.TemporaryDirectory() as tmp:
            repo = FakeRepo(Path(tmp))
            base = repo.commit("初始", {"README.md": "x\n"})
            repo.commit("CHG-20261022-01 計畫", {"docs/changes/CHG-20261022-01.md": src})
            code, out = run("--repo", tmp, "--range", "{}..{}".format(base, repo.head()))
        self.assertEqual(code, 0, out)
        self.assertIn("通過 docs/changes/CHG-20261022-01.md", out)


if __name__ == "__main__":
    unittest.main()
