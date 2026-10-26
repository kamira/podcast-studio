# -*- coding: utf-8 -*-
"""前端靜態檔的測試(CHG-20261022-01 P1、T10)。

本機沒有 node,前端互動由 T14 的 UI 檢查驗證;這裡只做靜態檢查。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import nodes
import studio_server as ss

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


# ---------- T10:首頁與「設定 › 進階流程」前端 ----------

TOKEN = "static-test-token-0123456789abcdef"
INDEX = STATIC / "index.html"
APP_JS = STATIC / "app.js"
STYLE = STATIC / "style.css"


@pytest.fixture
def client():
    app = ss.create_app(token=TOKEN, static_dir=STATIC)
    return TestClient(app, base_url="http://127.0.0.1:7860")


def _index_text() -> str:
    return INDEX.read_text(encoding="utf-8")


def _resource_refs(html: str) -> list[str]:
    return re.findall(r"""\b(?:src|href)\s*=\s*["']([^"']*)["']""", html, re.I)


def _app_port_types() -> list[str]:
    js = APP_JS.read_text(encoding="utf-8")
    m = re.search(r"const PORT_TYPES = (\[[^\]]*\]);", js)
    assert m, "app.js 必須宣告 const PORT_TYPES = [...]"
    return json.loads(m.group(1))


def test_static_files_exist():
    for p in (INDEX, APP_JS, STYLE):
        assert p.is_file(), p


def test_index_has_no_external_urls():
    html = _index_text()
    assert "http://" not in html and "https://" not in html
    for ref in _resource_refs(html):
        assert not ref.startswith("//"), ref


def test_index_resources_only_from_static():
    html = _index_text()
    scripts = re.findall(r"<script\b([^>]*)>", html, re.I)
    assert scripts, "index.html 應載入 app.js"
    for attrs in scripts:
        m = re.search(r"""\bsrc\s*=\s*["']([^"']+)["']""", attrs)
        assert m, "不允許行內 <script>(CSP 不允許)"
        assert m.group(1).startswith("/static/"), m.group(1)
    for href in re.findall(r"""<link\b[^>]*\bhref\s*=\s*["']([^"']+)["']""", html, re.I):
        assert href.startswith("/static/"), href
    # 頁內連結(#/…)以外的 src / href 一律在 /static/ 之下
    refs = [r for r in _resource_refs(html) if not r.startswith("#")]
    assert refs and all(r.startswith("/static/") and ".." not in r for r in refs), refs
    for needed in ("/static/vendor/drawflow.min.js", "/static/vendor/drawflow.min.css",
                   "/static/app.js", "/static/style.css"):
        assert needed in refs, needed
        assert (STATIC / needed[len("/static/"):]).is_file(), needed


def test_index_is_csp_clean():
    html = _index_text()
    assert not re.search(r"<style\b", html, re.I), "不允許 <style>(CSP)"
    assert not re.search(r"\sstyle\s*=", html, re.I), "不允許 style 屬性(CSP)"
    assert not re.search(r"\son[a-z]+\s*=", html, re.I), "不允許行內事件處理(CSP)"
    assert "javascript:" not in html.lower()
    for body in re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.I | re.S):
        assert body.strip() == ""


def test_index_has_token_meta_and_settings_entry():
    html = _index_text()
    assert f'<meta name="studio-token" content="{ss.TOKEN_PLACEHOLDER}">' in html
    assert re.search(r'<button[^>]*aria-label="設定"', html)
    assert 'role="tab"' not in html
    assert "← 首頁" in html
    assert 'data-sub="flow"' in html and "進階流程" in html


def test_app_js_port_types_match_nodes_module():
    assert _app_port_types() == sorted(nodes.PORT_TYPES)


def test_app_js_port_types_match_api_nodes(client):
    r = client.get("/api/nodes")
    assert r.status_code == 200
    body = r.json()
    assert _app_port_types() == sorted(body["port_types"])
    used = {p["type"] for n in body["nodes"] for side in ("inputs", "outputs") for p in n[side].values()}
    assert used <= set(_app_port_types())


def test_app_js_palette_covers_registry():
    js = APP_JS.read_text(encoding="utf-8")
    for kind in nodes.REGISTRY:
        assert f'"{kind}"' in js, kind


def test_app_js_is_csp_and_xss_safe():
    js = APP_JS.read_text(encoding="utf-8")
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                "new Function", "setTimeout(\"", "setInterval(\"", "http://", "https://"):
        assert bad not in js, bad
    assert "X-Studio-Token" in js and 'meta[name="studio-token"]' in js
    # 節點內容以 typenode=true 傳 DOM 節點給 Drawflow(cloneNode),不經 innerHTML
    assert re.search(r"addNode\([^;]*NODE_TEMPLATE, true\)", js, re.S)


def test_style_has_no_external_resources():
    css = STYLE.read_text(encoding="utf-8")
    assert "url(" not in css and "@import" not in css


def test_served_index_injects_token_with_csp(client):
    r = client.get("/")
    assert r.status_code == 200
    assert f'content="{TOKEN}"' in r.text
    assert ss.TOKEN_PLACEHOLDER not in r.text
    assert r.headers["content-security-policy"] == ss.CSP
    assert r.headers["x-content-type-options"] == "nosniff"


def test_served_static_assets(client):
    for path in ("/static/app.js", "/static/style.css", "/static/vendor/drawflow.min.js",
                 "/static/vendor/drawflow.min.css"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers["content-security-policy"] == ss.CSP
    assert "javascript" in client.get("/static/app.js").headers["content-type"]
    assert "text/css" in client.get("/static/style.css").headers["content-type"]
