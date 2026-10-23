"""secret_scan.py 的測試:在暫存目錄建立假 repo,不動全域 git 設定。

所有權杖與私鑰樣本一律在執行時以片段組字串產生,不以原文寫入 repo(KN-1),
也避免本檔被 secret_scan.py 自己掃到。
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci" / "secret_scan.py"

_spec = importlib.util.spec_from_file_location("secret_scan", SCRIPT)
secret_scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(secret_scan)

# 隔離全域 / 系統 git 設定,只用暫存 repo 的 local config
GIT_ENV = dict(os.environ)
GIT_ENV.update({
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "HOME": tempfile.gettempdir(),
})

# ---- 執行時組出的樣本 ----
ALNUM = "aB3dE5gH7jK9mN1pQ2rS4tU6vW8xY0zA"  # 32 字元英數
DASH5 = "-" * 5


def gh_token(n=36):
    return "gh" + "p_" + (ALNUM * 3)[:n]


def pem_header(kind=""):
    return DASH5 + "BEGIN " + (kind + " " if kind else "") + "PRIVATE " + "KEY" + DASH5


def pw_line(key, value, sep=" = "):
    return key + sep + value


SAMPLES = {
    "github-token": {
        "pos": [
            'TOKEN = "' + gh_token() + '"',
            "gh" + "s_" + (ALNUM * 2)[:40],
            "github" + "_pat_" + ("A1b2_" * 20)[:82],
        ],
        "neg": [
            "含 `gh" + "p_` + 36 字元的檔案被抓到",
            "gh" + "p_" + ALNUM[:20],              # 太短
            "x" + gh_token(),                        # 前綴不在字界
        ],
    },
    "openai-key": {
        "pos": [
            "OPENAI = " + "sk" + "-" + ALNUM + "Zz9",
            "sk" + "-proj-" + (ALNUM * 2)[:48],
        ],
        "neg": [
            "openai-key(`sk" + "-` 開頭、`sk" + "-proj-` 開頭的長金鑰)",
            "sk" + "-learn",
            "sk" + "-" + ALNUM[:16],
        ],
    },
    "anthropic-key": {
        "pos": ["KEY=" + "sk" + "-ant-api03-" + (ALNUM * 3)[:80]],
        "neg": ["anthropic-key(`sk" + "-ant-` 開頭)", "sk" + "-ant-" + "short"],
    },
    "aws-access-key": {
        "pos": ["aws_id = " + "AK" + "IA" + "ABCDEFGHIJ234567", "AS" + "IA" + "Z" * 16],
        "neg": ["`AK" + "IA` / `AS" + "IA` + 16 大寫英數", "AK" + "IA" + "abcdefghij234567",
                "AK" + "IA" + "ABCDEFGHIJ2345678"],
    },
    "slack-token": {
        "pos": ["xo" + "xb-" + "1234567890-" + ALNUM[:24]],
        "neg": ["slack-token(`xo" + "x[abprs]-`)", "xo" + "xb-", "xo" + "xz-1234567890-" + ALNUM],
    },
    "private-key": {
        "pos": [pem_header(), pem_header("RSA"), pem_header("EC"), pem_header("OPENSSH"),
                '    KEY = "' + pem_header("RSA") + '\\nMIIE"', "+" + pem_header()],
        "neg": ["PEM 私鑰區塊的開頭行", "`" + pem_header() + "` 被抓到",
                DASH5 + "BEGIN " + "PUBLIC KEY" + DASH5, DASH5 + "BEGIN CERTIFICATE" + DASH5],
    },
    "password-assignment": {
        "pos": [
            pw_line("password", '"hunter22xyz"'),
            pw_line("DB_PASSWD", "s3cr3t!Value", "="),
            pw_line("client_secret", "'q9W8e7R6t5'", ": "),
            pw_line("password", "Tr0ub4dor&3", ": "),
        ],
        "neg": [
            pw_line("password", '""'),
            pw_line("password", '"<你的密碼>"'),
            pw_line("password", '"${DB_PASSWORD}"'),
            pw_line("password", 'os.environ["DB_PASSWORD"]'),
            pw_line("secret", 'os.getenv("SECRET")'),
            pw_line("password", "get_password()"),
            pw_line("password", "str", ": "),
            pw_line("password", "user_supplied_value"),
            "if password == expected_value_here:",
            '"x.py::secret:private-key": "測試用的假樣本"',
            "secrets:治理帳本的完整性",
            "password-assignment(`password` / `passwd` / `secret` 後接 `=` 或 `:`)",
            pw_line("password", '"****"'),
        ],
    },
}


class RuleTest(unittest.TestCase):
    def test_every_rule_has_samples(self):
        self.assertEqual(set(SAMPLES), set(secret_scan.RULES))

    def test_positive_and_negative_samples(self):
        for rule, cases in SAMPLES.items():
            for line in cases["pos"]:
                with self.subTest(rule=rule, kind="正例", line=line[:30]):
                    rules = [r for r, _ in secret_scan.scan_line(line)]
                    self.assertIn(rule, rules)
            for line in cases["neg"]:
                with self.subTest(rule=rule, kind="反例", line=line[:30]):
                    rules = [r for r, _ in secret_scan.scan_line(line)]
                    self.assertNotIn(rule, rules)

    def test_mask_shows_only_six_chars(self):
        self.assertEqual(secret_scan.mask(gh_token()), gh_token()[:6] + "…")


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

    def write(self, rel: str, content):
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
        self.git("add", rel)

    def commit(self, msg: str, files=None, remove=()) -> str:
        for rel, content in (files or {}).items():
            self.write(rel, content)
        for rel in remove:
            self.git("rm", "-q", rel)
        self.git("commit", "-q", "--allow-empty", "-m", msg)
        return self.git("rev-parse", "HEAD")


def run(*args: str):
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], env=GIT_ENV,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.returncode, proc.stdout.decode("utf-8") + proc.stderr.decode("utf-8")


class ScanRepoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = FakeRepo(Path(self._tmp.name))
        self.base = self.repo.commit("初始", {"app.py": "print(1)\n"})

    def tearDown(self):
        self._tmp.cleanup()

    def scan(self, *extra):
        return run("--repo", str(self.repo.path), *extra)

    def test_clean_repo_exit_0(self):
        code, out = self.scan()
        self.assertEqual(code, 0, out)
        self.assertIn("秘密掃描通過", out)

    def test_github_token_file_caught_and_masked(self):
        tok = gh_token()
        self.repo.commit("加設定", {"conf/settings.py": 'TOKEN = "' + tok + '"\n'})
        code, out = self.scan()
        self.assertEqual(code, 1, out)
        self.assertIn("[github-token]", out)
        self.assertIn("conf/settings.py:1", out)
        self.assertIn(tok[:6] + "…", out)
        self.assertNotIn(tok, out)
        self.assertNotIn(tok[:7], out)

    def test_private_key_header_caught(self):
        body = "\n".join([pem_header("RSA"), "MIIEowIBAAKCAQEA", DASH5 + "END RSA PRIVATE KEY" + DASH5, ""])
        self.repo.commit("加金鑰", {"keys/id.pem": body})
        code, out = self.scan()
        self.assertEqual(code, 1, out)
        self.assertIn("[private-key]", out)
        self.assertIn("keys/id.pem:1", out)

    def test_added_then_removed_token_caught_by_range(self):
        tok = gh_token()
        self.repo.commit("誤加權杖", {"leak.txt": "first line\ntoken " + tok + "\n"})
        head = self.repo.commit("刪掉權杖", remove=["leak.txt"])
        code, out = self.scan()
        self.assertEqual(code, 0, "無 --range 時工作樹已乾淨:" + out)
        code, out = self.scan("--range", "{}..{}".format(self.base, head))
        self.assertEqual(code, 1, out)
        self.assertIn("[github-token]", out)
        self.assertIn("leak.txt:2", out)
        self.assertNotIn(tok, out)

    def test_range_ignores_removed_lines(self):
        tok = gh_token()
        mid = self.repo.commit("基準就有", {"old.txt": tok + "\n"})
        head = self.repo.commit("刪掉", remove=["old.txt"])
        code, out = self.scan("--range", "{}..{}".format(mid, head))
        self.assertEqual(code, 0, out)

    def test_allowlist_skips_path_and_rule(self):
        tok = gh_token()
        self.repo.commit("加假樣本", {
            "fixtures/sample.txt": tok + "\n",
            "fixtures/pw.cfg": pw_line("password", '"hunter22xyz"') + "\n",
            "other.txt": tok + "\n",
        })
        allow = self.repo.path / "allow.txt"
        allow.write_text("# 註解\n\nfixtures/*:*  # 測試用假樣本\n"
                         "other.txt:github-token  # 文件範例\n", encoding="utf-8")
        code, out = self.scan("--allowlist", str(allow))
        self.assertEqual(code, 0, out)
        # 規則不同的豁免不應放行
        allow.write_text("other.txt:aws-access-key  # 不相干\nfixtures/*:*  # 假樣本\n", encoding="utf-8")
        code, out = self.scan("--allowlist", str(allow))
        self.assertEqual(code, 1, out)
        self.assertIn("other.txt:1", out)
        self.assertNotIn("fixtures/", out)

    def test_default_allowlist_in_scanned_repo(self):
        self.repo.commit("加假樣本與豁免", {
            "fixtures/sample.txt": gh_token() + "\n",
            "scripts/ci/secret_allowlist.txt": "fixtures/*:github-token  # 測試用假樣本\n",
        })
        code, out = self.scan()
        self.assertEqual(code, 0, out)

    def test_allowlist_applies_to_range(self):
        head = self.repo.commit("加假樣本", {"fixtures/sample.txt": gh_token() + "\n"})
        allow = self.repo.path / "allow.txt"
        allow.write_text("fixtures/*:*  # 假樣本\n", encoding="utf-8")
        code, out = self.scan("--range", "{}..{}".format(self.base, head), "--allowlist", str(allow))
        self.assertEqual(code, 0, out)

    def test_allowlist_without_reason_exit_2(self):
        allow = self.repo.path / "allow.txt"
        for bad in ("fixtures/*:*\n", "fixtures/*:*  #   \n", "fixtures/*  # 缺規則\n",
                    "fixtures/*:no-such-rule  # 理由\n"):
            with self.subTest(bad=bad):
                allow.write_text(bad, encoding="utf-8")
                code, out = self.scan("--allowlist", str(allow))
                self.assertEqual(code, 2, out)
                self.assertIn("豁免清單格式錯誤", out)

    def test_binary_and_large_files_skipped(self):
        tok = gh_token()
        self.repo.commit("加二進位與大檔", {
            "bin.dat": b"\x00\x01" + tok.encode() + b"\x00",
            "big.txt": ("a" * 1023 + "\n") * (5 * 1024 + 1) + tok + "\n",
        })
        code, out = self.scan()
        self.assertEqual(code, 0, out)
        self.assertIn("略過(超過 5 MB):big.txt", out)

    def test_bad_args_exit_2(self):
        self.assertEqual(self.scan("--range", "nodots")[0], 2)
        self.assertEqual(self.scan("--range", "a...b")[0], 2)
        self.assertEqual(self.scan("--range", "nosuch..HEAD")[0], 2)
        self.assertEqual(self.scan("--allowlist", str(self.repo.path / "missing.txt"))[0], 2)
        self.assertEqual(run("--repo", str(self.repo.path / "nope"))[0], 2)

    def test_not_a_git_repo_exit_2(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(run("--repo", d)[0], 2)


class CurrentRepoTest(unittest.TestCase):
    def test_current_repo_clean(self):
        code, out = run("--repo", str(ROOT))
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
