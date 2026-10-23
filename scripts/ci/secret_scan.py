#!/usr/bin/env python3
"""
secret_scan.py — 全 repo 秘密掃描 / repository secret scan

規則(CHG-20261022-03 T8,安全影響 security-boundary (e)(l)):
  - 無 --range:掃描被掃描 repo 的所有追蹤檔(`git ls-files`);含 NUL 位元組的二進位檔略過,
    單檔超過 5 MB 略過並列出,符號連結略過(不跟隨到 repo 外)。
  - 有 --range:另外掃描 `git log -p --no-merges <base>..<head>` 中每個 commit 新增的行
    (`+` 開頭、排除 `+++`),因此「某 commit 加入、下一個 commit 刪除」的秘密仍會被抓到。

規則一律具名、保守:寧可漏報新格式,也不要大量誤報。命中輸出只顯示前 6 字加「…」,
絕不印出完整秘密。本腳本只讀取被掃描的 repo(以 --repo 指定,可與腳本所在位置不同;
CI 從 base 分支的 `trusted/` 副本執行),不寫入任何檔案。

豁免清單(預設為被掃描 repo 的 `scripts/ci/secret_allowlist.txt`,不存在視為空清單):
  每行 `<路徑 glob>:<規則名或 *>  # 理由`;空行與 `#` 開頭的行為註解。
  理由必填;缺理由、規則名不存在或格式不符,一律視為格式錯誤 exit 2。
  路徑 glob 以 fnmatch 比對 repo 相對路徑(`*` 可跨目錄)。

用法 / usage:
  python3 scripts/ci/secret_scan.py [--repo <路徑>] [--range <base>..<head>] [--allowlist <檔>]

退出碼:0 = 無命中;1 = 有命中;2 = 參數 / git / 豁免清單格式錯誤。
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

MAX_BYTES = 5 * 1024 * 1024
DEFAULT_ALLOWLIST = "scripts/ci/secret_allowlist.txt"
SHOW_CHARS = 6

# PEM 私鑰區塊開頭行:以片段組合,避免本檔原文出現完整開頭行(KN-1)。
_DASH5 = "-" * 5
_PEM_PRIVATE = (_DASH5 + "BEGIN (?:(?:RSA|EC|DSA|OPENSSH|ENCRYPTED|PGP) )?"
                + "PRIVATE KEY" + "(?: BLOCK)?" + _DASH5)
# 開頭行必須位於行首(可有縮排、diff / 引用前綴)或字串字面值開頭(引號、`\n` 之後);
# 包在說明文字行內(例如 markdown 行內程式碼)的不算——那是描述,不含金鑰本體。
_PEM_RE = re.compile(r"(?:^[\s>+]*|[\"']|\\n)(?P<hit>" + _PEM_PRIVATE + ")")

# password-assignment:鍵名含 password / passwd / secret,後接 = 或 :,值為引號字串或裸值。
_PW_KEY = r"(?i)(?<![A-Za-z0-9])[A-Za-z0-9_.-]*(?:password|passwd|secret)[A-Za-z0-9_.-]*"
_PW_RE = re.compile(
    _PW_KEY
    + r"[\"']?\s*(?:=(?!=)\s*|:\s+|:(?=[\"']))"
    + r"(?:\"(?P<dq>[^\"\n]*)\"|'(?P<sq>[^'\n]*)'|(?P<bare>[!-~]+?)(?=[\s,;#\"'`]|$))"
)

RULES = {
    # gh[pousr]_ 後接 36 以上英數;或細粒度權杖 github_pat_ 後接 82 字元
    "github-token": re.compile(
        r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{82})(?![A-Za-z0-9_])"),
    # sk- 後接 32 以上英數(舊格式);sk-proj- / sk-svcacct- / sk-admin- 後接 40 以上
    "openai-key": re.compile(
        r"(?<![A-Za-z0-9_-])sk-(?:(?:proj|svcacct|admin)-[A-Za-z0-9_-]{40,}|[A-Za-z0-9]{32,})(?![A-Za-z0-9_-])"),
    # sk-ant- 後接 40 以上(api03 / admin01 等皆是此形)
    "anthropic-key": re.compile(
        r"(?<![A-Za-z0-9_-])sk-ant-[A-Za-z0-9_-]{40,}(?![A-Za-z0-9_-])"),
    # AKIA / ASIA 後接剛好 16 個大寫英數
    "aws-access-key": re.compile(
        r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9])"),
    # xox[abprs]- 後接至少 10 字元的數字與英數段
    "slack-token": re.compile(
        r"(?<![A-Za-z0-9])xox[abprs]-[0-9]{6,}-[A-Za-z0-9-]{10,}"),
    "private-key": _PEM_RE,
    "password-assignment": _PW_RE,
}

# 佔位值(不算秘密)
_PLACEHOLDER_RES = [
    re.compile(r"^<[^>]*>$"),            # <你的密碼>
    re.compile(r"^\$\{[^}]*\}$"),        # ${PASSWORD}
    re.compile(r"^\$[A-Za-z_][A-Za-z0-9_]*$"),  # $PASSWORD
    re.compile(r"^\{\{.*\}\}$"),         # {{ secret }}
    re.compile(r"^%\([^)]*\)s$"),        # %(password)s
    re.compile(r"^[x*.\-_]+$", re.I),    # xxxx / **** / ...
]
_PLACEHOLDER_WORDS = {
    "none", "null", "nil", "true", "false", "changeme", "password", "passwd",
    "secret", "example", "redacted", "placeholder", "todo", "str", "string",
    "bytes", "int", "optional", "required",
}
_PLACEHOLDER_SUBSTR = ("os.environ", "getenv", "environ[", "environ.get")


def _is_placeholder(value: str) -> bool:
    v = value.strip()
    if not v:
        return True
    if any(s in v for s in _PLACEHOLDER_SUBSTR):
        return True
    if any(r.match(v) for r in _PLACEHOLDER_RES):
        return True
    return v.lower() in _PLACEHOLDER_WORDS


def _password_hits(line: str):
    for m in _PW_RE.finditer(line):
        dq, sq, bare = m.group("dq"), m.group("sq"), m.group("bare")
        if bare is not None:
            # 裸值:只接受像字面值的東西——不是函式呼叫、屬性存取、索引、型別標註或變數名
            if any(c in bare for c in "()[]{}.") or len(bare) < 8:
                continue
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", bare):
                continue  # 像識別字(變數名 / 型別),不算字面值
            value = bare
        else:
            value = dq if dq is not None else sq
            if len(value.strip()) < 6 or " " in value.strip():
                continue  # 太短,或含空白(多半是說明文字)
        if _is_placeholder(value):
            continue
        yield m.group(0)


def scan_line(line: str):
    """回傳 [(規則名, 命中字串)]。"""
    hits = []
    for name, rx in RULES.items():
        if name == "password-assignment":
            hits.extend((name, h) for h in _password_hits(line))
        elif name == "private-key":
            hits.extend((name, m.group("hit")) for m in rx.finditer(line))
        else:
            hits.extend((name, m.group(0)) for m in rx.finditer(line))
    return hits


def mask(text: str) -> str:
    return text[:SHOW_CHARS] + "…"


class GitError(Exception):
    """git 指令執行失敗。"""


class AllowlistError(Exception):
    """豁免清單格式錯誤。"""


def git(repo: Path, *args: str) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.quotepath=false", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise GitError("git {} 失敗:{}".format(" ".join(args), err))
    return proc.stdout


def load_allowlist(path: Path):
    """回傳 [(glob, 規則名或 *)];檔案不存在回傳空清單。"""
    if not path.is_file():
        return []
    entries = []
    for no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        body, sep, reason = line.partition("#")
        if not sep or not reason.strip():
            raise AllowlistError("第 {} 行缺少理由(格式:<路徑 glob>:<規則名或 *>  # 理由)".format(no))
        body = body.strip()
        glob, sep, rule = body.rpartition(":")
        glob, rule = glob.strip(), rule.strip()
        if not sep or not glob or not rule:
            raise AllowlistError("第 {} 行格式不符(格式:<路徑 glob>:<規則名或 *>  # 理由)".format(no))
        if rule != "*" and rule not in RULES:
            raise AllowlistError("第 {} 行的規則名不存在:{}".format(no, rule))
        entries.append((glob, rule))
    return entries


def allowed(entries, path: str, rule: str) -> bool:
    return any(fnmatch.fnmatchcase(path, g) and r in ("*", rule) for g, r in entries)


def scan_tree(repo: Path, entries):
    """掃描追蹤檔;回傳 (命中清單, 略過的大檔清單, 掃描檔數)。"""
    out = git(repo, "ls-files", "-z")
    files = [f for f in out.decode("utf-8", "replace").split("\0") if f]
    findings, big, count = [], [], 0
    for rel in files:
        p = repo / rel
        if p.is_symlink() or not p.is_file():
            continue
        size = p.stat().st_size
        if size > MAX_BYTES:
            big.append((rel, size))
            continue
        data = p.read_bytes()
        if b"\0" in data:
            continue
        count += 1
        text = data.decode("utf-8", "replace")
        for no, line in enumerate(text.splitlines(), 1):
            for rule, hit in scan_line(line):
                if not allowed(entries, rel, rule):
                    findings.append(("工作樹", rel, no, rule, hit))
    return findings, big, count


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_COMMIT_MARK = "\x01SECRETSCAN-COMMIT "


def scan_range(repo: Path, rng: str, entries):
    """掃描範圍內每個非 merge commit 新增的行;回傳 (命中清單, commit 數)。"""
    out = git(repo, "log", "-p", "--no-merges", "--no-color", "--no-ext-diff",
              "--no-textconv", "-U0", "--format=" + _COMMIT_MARK + "%h", rng, "--")
    findings, commits = [], 0
    sha, path, lineno = "?", None, None
    for raw in out.decode("utf-8", "replace").splitlines():
        if raw.startswith(_COMMIT_MARK):
            sha, path, lineno = raw[len(_COMMIT_MARK):].strip(), None, None
            commits += 1
            continue
        if raw.startswith("diff --git "):
            path, lineno = None, None
            continue
        if raw.startswith("+++ "):
            target = raw[4:]
            path = None if target == "/dev/null" else (target[2:] if target.startswith("b/") else target)
            continue
        if raw.startswith("--- "):
            continue
        m = _HUNK_RE.match(raw)
        if m:
            lineno = int(m.group(1))
            continue
        if raw.startswith("+") and path is not None:
            for rule, hit in scan_line(raw[1:]):
                if not allowed(entries, path, rule):
                    findings.append((sha, path, lineno, rule, hit))
            if lineno is not None:
                lineno += 1
    return findings, commits


def parse_range(text: str):
    if ".." not in text or "..." in text:
        return None
    base, head = text.split("..", 1)
    if not base or not head or base.startswith("-") or head.startswith("-"):
        return None
    return base, head


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="掃描 repo 追蹤檔與 commit 新增行中的秘密")
    ap.add_argument("--repo", default=".", help="被掃描的 git repo 路徑(預設目前目錄)")
    ap.add_argument("--range", dest="rng", help="另外掃描 <base>..<head> 內每個 commit 的新增行")
    ap.add_argument("--allowlist", help="豁免清單路徑(預設為被掃描 repo 的 {})".format(DEFAULT_ALLOWLIST))
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code == 0 else 2

    repo = Path(args.repo)
    if not repo.is_dir():
        print("參數錯誤:--repo 不是目錄:{}".format(repo), file=sys.stderr)
        return 2
    if args.rng is not None and parse_range(args.rng) is None:
        print("參數錯誤:--range 必須是 <base>..<head> 格式,收到:{}".format(args.rng), file=sys.stderr)
        return 2

    allow_path = Path(args.allowlist) if args.allowlist else repo / DEFAULT_ALLOWLIST
    if args.allowlist and not allow_path.is_file():
        print("參數錯誤:找不到豁免清單:{}".format(allow_path), file=sys.stderr)
        return 2
    try:
        entries = load_allowlist(allow_path)
    except (AllowlistError, UnicodeDecodeError) as e:
        print("豁免清單格式錯誤({}):{}".format(allow_path, e), file=sys.stderr)
        return 2

    try:
        findings, big, nfiles = scan_tree(repo, entries)
        ncommits = None
        if args.rng is not None:
            rf, ncommits = scan_range(repo, args.rng, entries)
            findings.extend(rf)
    except GitError as e:
        print("git 錯誤:{}".format(e), file=sys.stderr)
        return 2

    for rel, size in big:
        print("略過(超過 5 MB):{}({} 位元組)".format(rel, size))
    scope = "{} 個追蹤檔".format(nfiles)
    if ncommits is not None:
        scope += "、範圍 {} 內 {} 個 commit 的新增行".format(args.rng, ncommits)
    if not findings:
        print("秘密掃描通過(掃描 {};豁免 {} 條)".format(scope, len(entries)))
        return 0
    print("秘密掃描失敗:{} 處命中(掃描 {})".format(len(findings), scope))
    for where, rel, no, rule, hit in findings:
        loc = "{}:{}".format(rel, no if no is not None else "?")
        print("- [{}] {} {} → {}".format(rule, where, loc, mask(hit)))
    print("若確為誤報,請在 {} 加入 `<路徑 glob>:<規則名>  # 理由`;真秘密請立即撤銷並輪替。".format(DEFAULT_ALLOWLIST))
    return 1


if __name__ == "__main__":
    sys.exit(main())
