"""deploy_local.sh 的測試:在暫存目錄建假 repo 與假伺服器,不佔用 7860、不連網、不動全域 git 設定。"""
from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deploy_local.sh"

# 隔離全域 / 系統 git 設定,只用暫存 repo 的 local config
GIT_ENV = dict(os.environ)
GIT_ENV.update({
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "HOME": tempfile.gettempdir(),
})
for _k in ("DEPLOY_REPO", "DEPLOY_SERVER_CMD", "DEPLOY_PORT", "DEPLOY_SKIP_PULL"):
    GIT_ENV.pop(_k, None)

# 假伺服器:依環境變數綁定位址;先開額外埠(若有)再開主埠,讓主埠 LISTEN 時額外埠已存在
FAKE_SERVER = '''\
import os, socket, time
pid_file = os.environ.get("FAKE_PID_FILE")
if pid_file:
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
socks = []
extra = os.environ.get("FAKE_EXTRA")
if extra:
    host, port = extra.rsplit(":", 1)
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, int(port))); s.listen(); socks.append(s)
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind((os.environ.get("FAKE_BIND", "127.0.0.1"), int(os.environ["DEPLOY_PORT"])))
s.listen(); socks.append(s)
while True:
    time.sleep(1)
'''


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def alive(pid: int) -> bool:
    """行程存在且不是殭屍。"""
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL).stdout.decode().strip()
    except OSError:
        return False
    return bool(out) and not out.startswith("Z")


def wait_dead(pid: int, timeout: float = 15.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if not alive(pid):
            return True
        time.sleep(0.2)
    return False


class FakeRepo:
    """暫存目錄內的 bare origin + 工作副本。"""

    def __init__(self, root: Path, with_server: bool):
        self.root = root
        self.origin = root / "origin.git"
        self.path = root / "work"
        self.run_git(root, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.run_git(root, "clone", "-q", str(self.origin), str(self.path))
        self.config(self.path)
        # 另列 `outputs`(無斜線),讓「outputs 是符號連結」的情境不被當成未追蹤檔而提早中止
        self.write(".gitignore", "outputs/\noutputs\n")
        self.write("README.md", "假 repo\n")
        if with_server:
            self.write("studio_server.py", FAKE_SERVER)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "CHG-20261022-03 初始")
        self.git("branch", "-M", "main")
        self.git("push", "-q", "-u", "origin", "main")

    @staticmethod
    def run_git(cwd: Path, *args: str) -> str:
        proc = subprocess.run(["git", "-C", str(cwd), *args], env=GIT_ENV,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return proc.stdout.decode("utf-8").strip()

    @classmethod
    def config(cls, path: Path) -> None:
        cls.run_git(path, "config", "user.name", "測試者")
        cls.run_git(path, "config", "user.email", "test@example.invalid")
        cls.run_git(path, "config", "commit.gpgsign", "false")

    def git(self, *args: str) -> str:
        return self.run_git(self.path, *args)

    def write(self, rel: str, content: str) -> None:
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


class DeployLocalTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.port = free_port()
        self.fake_pid_file = self.root / "fake.pid"
        self.extra_pids: list[int] = []

    def tearDown(self):
        leaked = []
        for pid in self.server_pids() + self.extra_pids:
            if alive(pid):
                leaked.append(pid)
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        self._tmp.cleanup()
        self.assertEqual(leaked, [], "測試結束時仍有存活的假伺服器行程")

    def server_pids(self) -> list[int]:
        pids = []
        if self.fake_pid_file.exists():
            txt = self.fake_pid_file.read_text().strip()
            if txt:
                pids.append(int(txt))
        return pids

    def need_lsof(self):
        if shutil.which("lsof") is None:
            reason = "找不到 lsof,無法驗證 LISTEN socket,略過此測試"
            print(reason, file=sys.stderr)
            self.skipTest(reason)

    def fake_lsof(self, mode: str) -> str:
        """建立 lsof 替身目錄,回傳要前置到 PATH 的路徑。

        mode:fail-all = 一律 exit 1;fail-verify = 只有驗證查詢(引數含單獨的 -iTCP)exit 1;
        empty-verify = 驗證查詢回空且 exit 0;其餘查詢轉給真正的 lsof。
        """
        real = shutil.which("lsof")
        bindir = self.root / "fakebin"
        bindir.mkdir(exist_ok=True)
        script = bindir / "lsof"
        script.write_text(f"""#!/bin/sh
mode={mode}
[ "$mode" = fail-all ] && exit 1
verify=0
for a in "$@"; do [ "$a" = "-iTCP" ] && verify=1; done
if [ "$verify" = 1 ]; then
    [ "$mode" = fail-verify ] && exit 1
    [ "$mode" = empty-verify ] && exit 0
fi
exec "{real}" "$@"
""", encoding="utf-8")
        script.chmod(0o755)
        return str(bindir)

    def deploy(self, repo: FakeRepo, skip_pull: bool = True, **extra_env: str):
        env = dict(GIT_ENV)
        env.update({
            "DEPLOY_REPO": str(repo.path),
            "DEPLOY_SERVER_CMD": f"{sys.executable} studio_server.py",
            "DEPLOY_PORT": str(self.port),
            "FAKE_PID_FILE": str(self.fake_pid_file),
        })
        if skip_pull:
            env["DEPLOY_SKIP_PULL"] = "1"
        env.update(extra_env)
        proc = subprocess.run(["sh", str(SCRIPT)], env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=180)
        return proc.returncode, proc.stdout.decode("utf-8") + proc.stderr.decode("utf-8")

    # --- 前置條件 ---

    def test_non_main_branch_exits_1(self):
        repo = FakeRepo(self.root, with_server=False)
        repo.git("switch", "-q", "-c", "feature")
        code, out = self.deploy(repo)
        self.assertEqual(code, 1, out)
        self.assertIn("只能在 main 上部署", out)

    def test_dirty_worktree_exits_1(self):
        repo = FakeRepo(self.root, with_server=False)
        repo.write("untracked.txt", "x\n")
        code, out = self.deploy(repo)
        self.assertEqual(code, 1, out)
        self.assertIn("工作樹不乾淨", out)
        self.assertTrue((repo.path / "untracked.txt").exists(), "腳本不得刪檔")

    def test_ignored_files_do_not_count_as_dirty(self):
        repo = FakeRepo(self.root, with_server=False)
        repo.write("outputs/old.log", "x\n")
        code, out = self.deploy(repo)
        self.assertEqual(code, 0, out)

    def test_clean_main_pulls_ff_and_reports_missing_server(self):
        repo = FakeRepo(self.root, with_server=False)
        # 另一個 clone 推新 commit 到 origin,驗證腳本真的 fast-forward
        other = self.root / "other"
        FakeRepo.run_git(self.root, "clone", "-q", str(repo.origin), str(other))
        FakeRepo.config(other)
        (other / "new.txt").write_text("新\n", encoding="utf-8")
        FakeRepo.run_git(other, "add", "new.txt")
        FakeRepo.run_git(other, "commit", "-q", "-m", "CHG-20261022-03 新 commit")
        FakeRepo.run_git(other, "push", "-q", "origin", "main")
        upstream = FakeRepo.run_git(other, "rev-parse", "HEAD")

        code, out = self.deploy(repo, skip_pull=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(repo.git("rev-parse", "HEAD"), upstream)
        self.assertIn("網頁伺服器尚未建立(CHG-20261022-01)", out)
        self.assertIn("請重新啟動 Claude Code / Codex 以載入新版 local-models", out)

    # --- 綁定驗證 ---

    def test_bind_all_interfaces_is_stopped(self):
        self.need_lsof()
        repo = FakeRepo(self.root, with_server=True)
        code, out = self.deploy(repo, FAKE_BIND="0.0.0.0")
        self.assertEqual(code, 1, out)
        self.assertIn("非本機", out)
        pids = self.server_pids()
        self.assertEqual(len(pids), 1, out)
        self.assertTrue(wait_dead(pids[0]), "綁 0.0.0.0 的假伺服器未被停止")

    def test_extra_socket_on_all_interfaces_is_stopped(self):
        self.need_lsof()
        repo = FakeRepo(self.root, with_server=True)
        extra = free_port()
        code, out = self.deploy(repo, FAKE_EXTRA=f"0.0.0.0:{extra}")
        self.assertEqual(code, 1, out)
        self.assertIn(f":{extra}", out)
        pids = self.server_pids()
        self.assertEqual(len(pids), 1, out)
        self.assertTrue(wait_dead(pids[0]), "另開 0.0.0.0 埠的假伺服器未被停止")

    def test_localhost_server_starts_and_restart_stops_old(self):
        self.need_lsof()
        repo = FakeRepo(self.root, with_server=True)
        code, out = self.deploy(repo)
        self.assertEqual(code, 0, out)
        self.assertIn("請重新啟動 Claude Code / Codex 以載入新版 local-models", out)
        first = self.server_pids()[0]
        self.assertTrue(alive(first))
        self.extra_pids.append(first)

        code, out = self.deploy(repo)
        self.assertEqual(code, 0, out)
        second = self.server_pids()[0]
        self.assertNotEqual(first, second)
        self.assertTrue(wait_dead(first), "重啟時舊伺服器未被停止")
        self.assertTrue(alive(second))
        os.kill(second, signal.SIGTERM)
        self.assertTrue(wait_dead(second))

    # --- 驗證 fail-closed ---

    def assert_stopped_unverified(self, code: int, out: str):
        self.assertEqual(code, 1, out)
        self.assertIn("無法驗證監聽位址", out)
        pids = self.server_pids()
        self.assertEqual(len(pids), 1, out)
        self.assertTrue(wait_dead(pids[0]), "無法驗證時假伺服器未被停止")

    def test_lsof_verify_failure_stops_server(self):
        self.need_lsof()
        repo = FakeRepo(self.root, with_server=True)
        path = self.fake_lsof("fail-verify") + os.pathsep + GIT_ENV["PATH"]
        code, out = self.deploy(repo, PATH=path)
        self.assert_stopped_unverified(code, out)

    def test_lsof_empty_result_stops_server(self):
        self.need_lsof()
        repo = FakeRepo(self.root, with_server=True)
        path = self.fake_lsof("empty-verify") + os.pathsep + GIT_ENV["PATH"]
        code, out = self.deploy(repo, PATH=path)
        self.assert_stopped_unverified(code, out)

    def test_lsof_total_failure_stops_server(self):
        """lsof 全部失敗:埠等待逾時(約 60 秒)後停止伺服器。"""
        self.need_lsof()
        repo = FakeRepo(self.root, with_server=True)
        path = self.fake_lsof("fail-all") + os.pathsep + GIT_ENV["PATH"]
        code, out = self.deploy(repo, PATH=path)
        self.assert_stopped_unverified(code, out)

    # --- 符號連結 ---

    def test_outputs_symlink_outside_repo_is_refused(self):
        repo = FakeRepo(self.root, with_server=True)
        ext = self.root / "ext"
        ext.mkdir()
        (ext / "studio_server.log").write_text("原始\n", encoding="utf-8")
        (repo.path / "outputs").symlink_to(ext, target_is_directory=True)
        code, out = self.deploy(repo)
        self.assertEqual(code, 1, out)
        self.assertIn("符號連結", out)
        self.assertEqual(sorted(p.name for p in ext.iterdir()), ["studio_server.log"])
        self.assertEqual((ext / "studio_server.log").read_text(encoding="utf-8"), "原始\n")
        self.assertEqual(self.server_pids(), [], "不得啟動伺服器")

    def test_log_symlink_outside_repo_is_refused(self):
        repo = FakeRepo(self.root, with_server=True)
        ext_file = self.root / "ext.log"
        ext_file.write_text("原始\n", encoding="utf-8")
        (repo.path / "outputs").mkdir()
        (repo.path / "outputs" / "studio_server.log").symlink_to(ext_file)
        code, out = self.deploy(repo)
        self.assertEqual(code, 1, out)
        self.assertIn("符號連結", out)
        self.assertEqual(ext_file.read_text(encoding="utf-8"), "原始\n")
        self.assertEqual(self.server_pids(), [], "不得啟動伺服器")

    def test_pidfile_pointing_elsewhere_is_left_alone(self):
        self.need_lsof()
        repo = FakeRepo(self.root, with_server=True)
        other = subprocess.Popen(["sleep", "60"])
        self.extra_pids.append(other.pid)
        try:
            (repo.path / "outputs").mkdir()
            (repo.path / "outputs" / ".studio.pid").write_text(f"{other.pid}\n")
            code, out = self.deploy(repo)
            self.assertEqual(code, 1, out)
            self.assertIn("不是本 repo 的 studio_server.py", out)
            self.assertIsNone(other.poll(), "腳本不得停止非本專案行程")
        finally:
            other.kill()
            other.wait()


if __name__ == "__main__":
    unittest.main()
