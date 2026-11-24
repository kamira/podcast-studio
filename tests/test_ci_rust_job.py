# -*- coding: utf-8 -*-
"""CI Rust job、cargo-deny 設定與行為規格的結構測試(CHG-20261123-01 T3)。

L1:`deny.toml`——授權白名單不含 copyleft(GPL、LGPL、AGPL、MPL)、涵蓋三平台目標三元組、
    只允許 crates.io 來源、禁止萬用字元版本、漏洞公告一律阻擋。
L2:workflow 與 main ruleset 的介面——彙總 job 名稱恰為 `rust`、`needs: rust-platform`、
    `if: always()`,其步驟在 `needs.rust-platform.result` 不是 `success` 時 exit 1
    (以本機 bash 對該步驟的 script 段分別代入 success、failure、cancelled、skipped 執行)。
    另檢查 `rust-platform` 矩陣三平台、`fail-fast: false`、shell bash、逾時 30 分鐘、
    只取 PR head、不注入秘密、不加快取、第三方 action 只用既有兩個釘選 SHA、
    `cargo fmt` 以外的 cargo 指令皆帶 `--locked`、cargo-deny 釘完整版號且只在 ubuntu 一格執行。
L3:`features/ci.feature` 新場景「Rust 三平台任一失敗讓 rust 檢查變紅」。

只用標準函式庫(本測試由 `scripts/check.sh` 收集,該載具沒有 PyYAML),以文字方式解析 workflow。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
DENY_TOML = ROOT / "deny.toml"
CI_FEATURE = ROOT / "features" / "ci.feature"

# 既有兩個 action 的釘選 SHA(CHG-20261022-03);本筆不新增任何第三方 action
PINNED_ACTIONS = {
    "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0",
}
PLATFORMS = ["ubuntu-latest", "macos-latest", "windows-latest"]
TARGET_TRIPLES = {
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
    "x86_64-apple-darwin",
    "aarch64-apple-darwin",
    "x86_64-pc-windows-msvc",
    "aarch64-pc-windows-msvc",
}


def ci_text() -> str:
    return CI_YML.read_text(encoding="utf-8")


def job_block(name: str) -> str:
    """回傳 `jobs:` 底下名為 name 的 job 區塊(到下一個同層 job 或檔尾)。"""
    lines = ci_text().splitlines()
    start = None
    for i, line in enumerate(lines):
        if line == f"  {name}:":
            start = i
            break
    assert start is not None, f"ci.yml 找不到 job `{name}`"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^  [A-Za-z0-9_-]+:\s*$", lines[j]) or re.match(r"^\S", lines[j]):
            end = j
            break
    return "\n".join(lines[start:end])


def run_scripts(block: str) -> list[str]:
    """取出區塊內每個 `run: |` 的 script 段(依縮排收集)。"""
    lines = block.splitlines()
    scripts = []
    i = 0
    while i < len(lines):
        m = re.match(r"^(\s*)(?:- )?run: \|\s*$", lines[i])
        if m:
            base = len(m.group(1))
            body = []
            i += 1
            while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > base):
                body.append(lines[i])
                i += 1
            indent = min((len(b) - len(b.lstrip()) for b in body if b.strip()), default=0)
            scripts.append("\n".join(b[indent:] for b in body) + "\n")
            continue
        m = re.match(r"^\s*(?:- )?run: (?!\|)(.+)$", lines[i])
        if m:
            scripts.append(m.group(1) + "\n")
        i += 1
    return scripts


def find_bash() -> str | None:
    """找可用的 bash;Windows 上避開 System32 的 WSL 轉接程式,改用 Git for Windows 的 bash。"""
    if sys.platform != "win32":
        return shutil.which("bash")
    git = shutil.which("git")
    if git:
        for cand in (Path(git).parents[1] / "bin" / "bash.exe", Path(git).parents[1] / "usr" / "bin" / "bash.exe"):
            if cand.is_file():
                return str(cand)
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return found
    return None


# ---------------------------------------------------------------- L3:行為規格


def test_ci_feature_has_rust_scenario():
    text = CI_FEATURE.read_text(encoding="utf-8")
    assert "場景: Rust 三平台任一失敗讓 rust 檢查變紅" in text
    block = text.split("場景: Rust 三平台任一失敗讓 rust 檢查變紅", 1)[1]
    for step in (
        "假設 PR 的 Rust 程式在 ubuntu、macos、windows 其中一個平台的 fmt、clippy、test、release 建置、比對工具示範或 cargo-deny 失敗",
        "當 CI 執行 rust-platform 矩陣與彙總 job rust",
        "那麼 rust 檢查失敗",
        "而且 main ruleset 把 rust 列為必要檢查,PR 不能 merge",
    ):
        assert step in block


def test_ci_feature_keeps_existing_scenarios():
    text = CI_FEATURE.read_text(encoding="utf-8")
    # 既有 15 個場景不得刪減,新增 1 個
    assert text.count("場景:") >= 16
    assert "場景: CI 腳本拒絕以 - 開頭的範圍" in text


# ---------------------------------------------------------------- L2:workflow 結構


def test_workflow_lists_six_jobs_and_keeps_triggers():
    text = ci_text()
    jobs = re.findall(r"^  ([A-Za-z0-9_-]+):\s*$", text.split("\njobs:\n", 1)[1], flags=re.M)
    assert jobs == ["document", "chg", "program", "review", "rust-platform", "rust"]
    # 觸發條件不變:只對 base 為 main 的 PR 與 push 到 main
    assert "on:\n  pull_request:\n    branches: [main]\n  push:\n    branches: [main]\n" in text
    assert "pull_request_target" not in text.replace("禁用 `pull_request_target`", "")


def test_rust_platform_matrix_and_defaults():
    block = job_block("rust-platform")
    assert re.search(r"^    runs-on: \$\{\{ matrix\.os \}\}$", block, flags=re.M)
    assert re.search(r"^      fail-fast: false$", block, flags=re.M)
    m = re.search(r"^        os: \[(.+)\]$", block, flags=re.M)
    assert m, "rust-platform 須以 matrix.os 列出三平台"
    assert [s.strip() for s in m.group(1).split(",")] == PLATFORMS
    assert re.search(r"^    timeout-minutes: 30$", block, flags=re.M)
    assert re.search(r"^    defaults:\n      run:\n        shell: bash$", block, flags=re.M)


def test_rust_platform_checks_out_pr_head_only():
    block = job_block("rust-platform")
    assert "path: pr" in block
    assert "trusted" not in block.replace("不取 `trusted/`", "")
    assert block.count("persist-credentials: false") == block.count("actions/checkout@")
    assert "${{ github.event.pull_request.head.sha || github.sha }}" in block


def test_new_jobs_inject_no_secrets_cache_or_permissions():
    for name in ("rust-platform", "rust"):
        block = job_block(name)
        assert "secrets." not in block
        assert "actions/cache" not in block and "rust-cache" not in block
        assert not re.search(r"^    permissions:", block, flags=re.M), f"{name} 不得另加權限(沿用頂層 contents: read)"


def test_all_uses_are_existing_pinned_actions():
    for line in ci_text().splitlines():
        m = re.match(r"^\s*(?:- )?uses: (\S+)", line)
        if m:
            assert m.group(1) in PINNED_ACTIONS, f"未釘選或新增的 action:{line.strip()}"


def test_rust_platform_steps_cover_all_checks():
    block = job_block("rust-platform")
    body = "\n".join(run_scripts(block))
    for cmd in (
        "rustup show",
        "rustup toolchain install",
        "cargo fmt --all --check",
        "cargo clippy --workspace --all-targets --locked -- -D warnings",
        "cargo test --workspace --locked",
        "cargo build --workspace --release --locked",
        "cargo run --locked --release -p parity -- run --unit demo.parse_count --fixture crates/parity/fixtures/demo/parse_count.json",
        "cargo run --locked --release -p parity -- run --unit demo.parse_count_drift --fixture crates/parity/fixtures/demo/parse_count_drift.json",
        "cargo deny --locked check",
    ):
        assert cmd in body, f"rust-platform 缺少:{cmd}"
    assert "edge_1000" in body
    assert "結論:一致" in body and "結論:不一致" in body
    assert "continue-on-error" not in block
    assert "actions/setup-python@" in block and 'python-version: "3.11"' in block


def test_every_cargo_command_except_fmt_is_locked():
    seen = 0
    for script in run_scripts(ci_text()):
        for line in script.splitlines():
            if line.strip().startswith("#"):
                continue
            for m in re.finditer(r"\bcargo ([a-z][a-z-]*)", line):
                sub = m.group(1)
                if sub == "fmt":
                    continue
                seen += 1
                assert "--locked" in line, f"cargo {sub} 未帶 --locked:{line.strip()}"
    assert seen >= 7


def test_cargo_deny_pinned_and_only_on_ubuntu():
    block = job_block("rust-platform")
    versions = re.findall(r"cargo install --locked cargo-deny@(\S+)", block)
    assert len(versions) == 1
    assert re.fullmatch(r"\d+\.\d+\.\d+", versions[0]), "cargo-deny 須釘完整版號"
    # cargo-deny 的安裝與執行步驟都只在 ubuntu 一格
    steps = re.split(r"^      - ", block, flags=re.M)[1:]
    deny_steps = [s for s in steps if "cargo-deny" in s or "cargo deny" in s]
    assert deny_steps
    for s in deny_steps:
        assert "if: matrix.os == 'ubuntu-latest'" in s


def test_rust_aggregate_job_interface():
    block = job_block("rust")
    assert re.search(r"^    needs: rust-platform$", block, flags=re.M)
    assert re.search(r"^    if: always\(\)$", block, flags=re.M)
    assert re.search(r"^    runs-on: ubuntu-latest$", block, flags=re.M)
    # 名稱字串恰為 rust:不另設 name(check 名稱即 job id)
    assert not re.search(r"^    name:", block, flags=re.M)
    assert "${{ needs.rust-platform.result }}" in block
    assert "actions/checkout@" not in block, "彙總 job 不取任何程式碼"


@pytest.mark.parametrize(
    "result, expected",
    [("success", 0), ("failure", 1), ("cancelled", 1), ("skipped", 1)],
)
def test_rust_aggregate_step_fails_unless_success(result, expected):
    bash = find_bash()
    if bash is None:
        pytest.skip("找不到可用的 bash")
    block = job_block("rust")
    scripts = run_scripts(block)
    assert len(scripts) == 1
    m = re.search(r"^\s+([A-Z_]+): \$\{\{ needs\.rust-platform\.result \}\}$", block, flags=re.M)
    assert m, "彙總步驟須以環境變數取得 needs.rust-platform.result"
    env = dict(os.environ)
    env[m.group(1)] = result
    # 與 GitHub Actions 的 shell: bash 相同旗標
    proc = subprocess.run(
        [bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", scripts[0]],
        env=env,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == expected, proc.stdout + proc.stderr


# ---------------------------------------------------------------- L1:deny.toml


def test_deny_toml_has_no_copyleft():
    text = DENY_TOML.read_text(encoding="utf-8")
    for word in ("GPL", "LGPL", "AGPL", "MPL"):
        assert not re.search(rf"\b{word}\b", text), f"deny.toml 不得出現 {word}"


def test_deny_toml_policy():
    tomllib = pytest.importorskip("tomllib")
    cfg = tomllib.loads(DENY_TOML.read_text(encoding="utf-8"))
    assert set(cfg["graph"]["targets"]) == TARGET_TRIPLES
    allow = cfg["licenses"]["allow"]
    assert allow, "授權白名單不得為空"
    assert set(allow) <= {
        "MIT",
        "Apache-2.0",
        "Apache-2.0 WITH LLVM-exception",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "ISC",
        "Zlib",
        "Unicode-3.0",
        "BSL-1.0",
        "Unlicense",
    }, "白名單只允許代決 10 初稿內的寬鬆授權(補列須記於派發記錄)"
    assert cfg["bans"]["multiple-versions"] == "warn"
    assert cfg["bans"]["wildcards"] == "deny"
    assert cfg["sources"]["unknown-registry"] == "deny"
    assert cfg["sources"]["unknown-git"] == "deny"
    assert cfg["sources"].get("allow-git", []) == []
    assert cfg["sources"].get("allow-registry") == ["https://github.com/rust-lang/crates.io-index"]
    # 漏洞公告一律阻擋:不得有豁免(豁免須經 CHG)
    assert cfg["advisories"].get("ignore", []) == []
