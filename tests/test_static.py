# -*- coding: utf-8 -*-
"""前端靜態檔的測試(CHG-20261022-01 P1、T10)。

本機沒有 node,前端互動由 T14 的 UI 檢查驗證;這裡只做靜態檢查。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
VENDOR = STATIC / "vendor"


# ---------- P1:vendored Drawflow ----------

def _readme_hashes() -> dict[str, str]:
    """從 static/vendor/README.md 的表格讀出 {檔名: SHA-256}。"""
    text = (VENDOR / "README.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\|\s*`([^`]+)`\s*\|[^|\n]*\|\s*`([0-9a-f]{64})`\s*\|\s*$", text, re.M)
    return dict(rows)


def test_vendor_readme_lists_drawflow_files():
    hashes = _readme_hashes()
    assert {"drawflow.min.js", "drawflow.min.css", "LICENSE.drawflow"} <= set(hashes)


def test_vendor_sha256_matches_readme():
    for name, expected in _readme_hashes().items():
        data = (VENDOR / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected, name


def test_vendor_dir_only_has_documented_files():
    documented = set(_readme_hashes()) | {"README.md"}
    actual = {p.name for p in VENDOR.iterdir()}
    assert actual == documented


def test_vendor_readme_records_version_source_and_license():
    text = (VENDOR / "README.md").read_text(encoding="utf-8")
    assert "0.0.60" in text
    assert "https://registry.npmjs.org/drawflow/-/drawflow-0.0.60.tgz" in text
    assert "MIT" in text
    lic = (VENDOR / "LICENSE.drawflow").read_text(encoding="utf-8")
    assert lic.startswith("MIT License")
    assert "Permission is hereby granted, free of charge" in lic


def test_drawflow_does_not_need_unsafe_eval():
    js = (VENDOR / "drawflow.min.js").read_text(encoding="utf-8")
    for bad in ("eval(", "new Function", "Function(", "document.write", "XMLHttpRequest", "fetch("):
        assert bad not in js, bad
    css = (VENDOR / "drawflow.min.css").read_text(encoding="utf-8")
    assert "url(" not in css and "@import" not in css
