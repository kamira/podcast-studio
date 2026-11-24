# -*- coding: utf-8 -*-
"""mcp_registry.py 真 CLI 整合測試(CHG-20261022-02 T1)。

不列入 `./scripts/check.sh` 與 CI:`tests/conftest.py` 只在命令列明確指定 `tests/integration/` 下的路徑時才收集。
只在本機手動執行;需要 `~/.local/bin/claude`、`~/.local/bin/codex`、`~/.local/bin/uv`(缺任一個整檔略過)。

一律把 `CLAUDE_CONFIG_DIR`、`CODEX_HOME` 指到新建的暫存目錄,真 CLI 只寫暫存設定;測試前後比對真實
`~/.codex/config.toml` 的 SHA-256 與真實 `~/.claude.json` 的 `mcpServers` 鍵不變。只做註冊 → 查詢 → 移除 → 查詢
(`status(check=False)`,不做健康檢查,不啟動 MCP 伺服器)。Claude Code 會在暫存 `.claude.json` 自動帶入帳號
相關鍵,測試結束時刪除整個暫存目錄。

  ./scripts/check.sh tests/integration/test_mcp_registry_real.py
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path

import pytest

import mcp_registry as mr

HOME = Path.home()
CLIS = [HOME / ".local/bin/claude", HOME / ".local/bin/codex", HOME / ".local/bin/uv"]

pytestmark = pytest.mark.skipif(not all(p.exists() for p in CLIS), reason="本機沒有 claude / codex / uv CLI")


def _real_snapshot():
    toml = HOME / ".codex" / "config.toml"
    cj = HOME / ".claude.json"
    snap = {"codex": hashlib.sha256(toml.read_bytes()).hexdigest() if toml.is_file() else None, "claude": None}
    if cj.is_file():
        for _ in range(10):
            try:
                snap["claude"] = json.dumps(json.loads(cj.read_text(encoding="utf-8")).get("mcpServers"),
                                            sort_keys=True, ensure_ascii=False)
                break
            except (ValueError, OSError):
                time.sleep(0.2)
    return snap


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    claude_dir = tmp_path / "claude"
    codex_dir = tmp_path / "codex"
    claude_dir.mkdir()
    codex_dir.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_dir))
    monkeypatch.setenv("CODEX_HOME", str(codex_dir))
    p = mr.paths()
    assert p["claude_json"] == claude_dir / ".claude.json"
    assert p["codex_home"] == codex_dir
    before = _real_snapshot()
    yield claude_dir, codex_dir
    shutil.rmtree(tmp_path, ignore_errors=True)
    assert _real_snapshot() == before, "真實設定被改動"


def _claude_servers(claude_dir):
    p = claude_dir / ".claude.json"
    return json.loads(p.read_text(encoding="utf-8")).get("mcpServers", {}) if p.exists() else {}


def test_claude_註冊_查詢_移除_查詢(isolated):
    claude_dir, _ = isolated
    assert mr.status(check=False)["claude"]["state"] == "absent"
    r = mr.apply("claude", "register")
    assert r["ok"] is True, r["output"]
    assert r["status"]["claude"]["state"] == "registered"
    entry = _claude_servers(claude_dir)["local-models"]
    assert entry["command"] == mr.server_argv()[0]
    assert entry["args"] == mr.server_argv()[1:]
    assert list(_claude_servers(claude_dir)) == ["local-models"]
    r = mr.apply("claude", "remove")
    assert r["ok"] is True, r["output"]
    assert mr.status(check=False)["claude"]["state"] == "absent"
    assert "local-models" not in _claude_servers(claude_dir)


def test_codex_註冊_查詢_移除_查詢(isolated):
    _, codex_dir = isolated
    assert mr.status(check=False)["codex"]["state"] == "absent"
    r = mr.apply("codex", "register")
    assert r["ok"] is True, r["output"]
    st = r["status"]["codex"]
    assert st["state"] == "registered"
    assert st["timeout"]["value"] is None
    text = (codex_dir / "config.toml").read_text(encoding="utf-8")
    assert "[mcp_servers.local-models]" in text
    r = mr.apply("codex", "remove")
    assert r["ok"] is True, r["output"]
    assert mr.status(check=False)["codex"]["state"] == "absent"
    assert "local-models" not in (codex_dir / "config.toml").read_text(encoding="utf-8")
