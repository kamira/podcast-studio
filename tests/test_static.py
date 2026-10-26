# -*- coding: utf-8 -*-
"""前端靜態檔的測試(CHG-20261022-01 P1、T10、T11、T12)。

前端互動由 T14 的 UI 檢查驗證;這裡做靜態檢查。T12 的審議席位規則與 422 欄位對應
另在有 JS 執行環境(node 或 macOS jsc)時實跑 app.js 的函式,沒有時略過該兩項。
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import nodes
import settings
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


# ---------- T11:檔案拖放與工作流程存取 ----------

def _app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _js_const(name: str, pattern: str) -> str:
    m = re.search(r"const " + name + r" = " + pattern + ";", _app_js())
    assert m, f"app.js 必須宣告 const {name}"
    return m.group(1)


def _js_upload_kinds() -> dict[str, str]:
    return json.loads(_js_const("UPLOAD_KINDS", r"(\{[^}]*\})"))


def _js_name_pattern() -> re.Pattern:
    """把 app.js 的 WORKFLOW_NAME_RE(JS 正規式,無旗標)轉成 Python 的 fullmatch 樣式。

    只用到字元類別、量詞與 \\uXXXX 跳脫,Python re 與 JS 語意相同;去掉 ^ $ 改用 fullmatch,
    避免 Python 的 `$` 會吃掉結尾換行。
    """
    return re.compile(_js_const("WORKFLOW_NAME_RE", r"/\^(.*)\$/"))


def _js_matches(name: str) -> bool:
    return _js_name_pattern().fullmatch(name) is not None


def _server_accepts(name: str) -> bool:
    return ss._normalize_name(name) is not None


def test_app_js_upload_whitelist_matches_server():
    assert _js_upload_kinds() == ss.UPLOAD_KINDS
    assert _js_upload_kinds() == {ext: k for k, exts in nodes.FILE_KINDS.items() for ext in exts}


def test_app_js_upload_limits_match_server():
    assert int(_js_const("MAX_UPLOAD_MB", r"(\d+)")) * 1024 * 1024 == ss.MAX_UPLOAD
    assert int(_js_const("MAX_UPLOAD_NAME", r"(\d+)")) == ss.MAX_UPLOAD_NAME


def test_app_js_upload_kinds_are_port_types():
    assert set(_js_upload_kinds().values()) <= set(_app_port_types())


def test_app_js_upload_request_shape():
    js = _app_js()
    # 與 T8 契約一致:原始位元組、檔名放查詢參數、帶權杖(不是 multipart)
    assert '"/api/upload?name=" + encodeURIComponent(file.name)' in js
    assert re.search(r'method: "POST", body: file\b', js)
    assert re.search(r'headers: \{ "X-Studio-Token": TOKEN \}', js)
    assert "FormData" not in js


def test_app_js_rejects_before_upload():
    js = _app_js()
    # 拖到畫布:先 checkFile 再 uploadFile;拖到輸入口:型別不符就提示、不上傳
    drop = js[js.index("async onFileDrop("):js.index("addFileNode(up, name, x, y) {")]
    assert drop.index("checkFile(f)") < drop.index("uploadFile(f)")
    port = js[js.index("async dropOnPort("):js.index("canvasPoint(clientX, clientY) {")]
    assert port.index("checkFile(file)") < port.index("c.kind !== port.type") < port.index("uploadFile(file)")
    assert "型別不符" in port
    assert "不支援此檔案類型" in js


@pytest.mark.parametrize("name", [
    "夜市流程", "ポッドキャスト", "ラーメン", "Flow", "default", "my_flow-2",
    "々", "ー", "㐀", "鿿", "一", "ぁ", "ゖ", "ァ", "ヺ",
    "한국어", "café", "ＡＢＣ", "・", "", "a" * 64, "a" * 65, "夜" * 64, "夜" * 65,
    "a b", "a.b", "a/b", "..", "abc\n", "​", "ガ", "ガ", "Ⅻ", "１", "😀", "𠀀",
    "ゝ", "ヽ", "〇", "－", "＿",
])
def test_workflow_name_rule_agrees_with_server(name):
    assert _js_matches(name) == _server_accepts(name), repr(name)


def test_workflow_name_rule_sample_expectations():
    accepted = ["夜市流程", "ポッドキャスト", "ラーメン", "Flow", "a" * 64]
    rejected = ["한국어", "café", "ＡＢＣ", "・", "", "a" * 65]
    assert all(_js_matches(n) and _server_accepts(n) for n in accepted)
    assert not any(_js_matches(n) or _server_accepts(n) for n in rejected)


def test_workflow_name_rule_agrees_on_every_bmp_char():
    pat = _js_name_pattern()
    diff = [hex(cp) for cp in range(0x10000)
            if not 0xD800 <= cp <= 0xDFFF
            and (pat.fullmatch(chr(cp)) is not None) != _server_accepts(chr(cp))]
    assert diff == []


def test_app_js_workflow_name_hint_matches_server():
    assert _js_const("WORKFLOW_NAME_HINT", r'"([^"]*)"') == ss.WORKFLOW_NAME_HINT


@pytest.fixture
def wf_client(tmp_path):
    app = ss.create_app(runner=lambda g, e, c=None: None, token=TOKEN,
                        static_dir=STATIC, root=tmp_path)
    return TestClient(app, base_url="http://127.0.0.1:7860")


def _put(client, name, graph, overwrite=None):
    body = {"graph": graph}
    if overwrite is not None:
        body["overwrite"] = overwrite
    return client.put("/api/workflows/" + name, json=body,
                      headers={"Origin": "http://127.0.0.1:7860", "X-Studio-Token": TOKEN})


SMALL_GRAPH = {"nodes": [{"id": "1", "kind": "text-input", "params": {"text": "台灣夜市文化"},
                          "pos": {"x": 40, "y": 60}}], "edges": []}


def test_case_collision_mark_tells_409_kinds_apart(wf_client):
    mark = _js_const("CASE_COLLISION_MARK", r'"([^"]*)"')
    assert _put(wf_client, "Flow", SMALL_GRAPH).status_code == 200
    same = _put(wf_client, "Flow", SMALL_GRAPH)
    assert same.status_code == 409
    assert mark not in same.json()["error"]          # 同名:前端詢問是否覆寫
    case = _put(wf_client, "flow", SMALL_GRAPH, overwrite=True)
    assert case.status_code == 409
    assert mark in case.json()["error"]               # 只差大小寫:只顯示伺服器訊息


def test_app_js_save_dialog_flow():
    js = _app_js()
    save = js[js.index("async save(name, overwrite) {"):js.index("async load(name) {")]
    assert '"/api/workflows/" + encodeURIComponent(name)' in save
    assert "{ graph: graph, overwrite: overwrite }" in save
    assert "r.status === 409" in save and "CASE_COLLISION_MARK" in save
    assert 'showValidationErrors(r.data.errors, "未存檔")' in save
    assert "this.submit(true)" in js                  # 覆寫只在使用者按「覆寫」後送出
    load = js[js.index("async load(name) {"):]
    assert "r.status === 404" in load and "找不到這個流程" in load
    assert "WORKFLOW_NAME_RE.test(name)" in js


def test_index_has_workflow_dialog():
    html = _index_text()
    for needed in ('<dialog id="wf-dialog"', 'id="wf-form"', 'id="wf-name"', 'id="wf-error"',
                   'id="wf-confirm"', 'id="wf-overwrite"', 'id="wf-cancel"', 'id="wf-submit"',
                   "已有同名流程,要覆寫嗎?", 'id="btn-save"', 'id="btn-load"'):
        assert needed in html, needed
    assert "建置中)" not in html


def test_saved_graph_keeps_positions_round_trip(wf_client):
    assert _put(wf_client, "夜市流程", SMALL_GRAPH).status_code == 200
    r = wf_client.get("/api/workflows/夜市流程")
    assert r.status_code == 200
    assert r.json() == {"name": "夜市流程", "graph": SMALL_GRAPH}
    assert wf_client.get("/api/workflows/沒有這個").status_code == 404


# ---------- T12:設定頁 ----------

def _default_paths(obj=None, prefix="") -> set[str]:
    """settings.DEFAULTS 的葉節點路徑(例 roles.reason.model)。"""
    obj = settings.DEFAULTS if obj is None else obj
    out: set[str] = set()
    for k, v in obj.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out |= _default_paths(v, path)
        else:
            out.add(path)
    return out


def _section(html: str, section_id: str) -> str:
    start = html.index(f'<section id="{section_id}"')
    return html[start:html.index("\n</section>", start)]   # 頂層 section(子區塊有縮排)


def _settings_html() -> str:
    return _section(_index_text(), "page-settings")


def _data_fields(html: str) -> list[str]:
    return re.findall(r'\bdata-field="([^"]+)"', html)


def _js_json_const(name: str):
    m = re.search(r"^  const " + name + r" = (\{.*\});$", _app_js(), re.M)
    assert m, f"app.js 必須以單行 JSON 宣告 const {name}"
    return json.loads(m.group(1))


def test_settings_fields_match_defaults_keys():
    fields = _data_fields(_settings_html())
    assert len(fields) == len(set(fields)), "data-field 不得重複"
    assert set(fields) == _default_paths()
    # 只有設定頁有 data-field(其他頁面不會被當成設定欄位送出)
    assert len(_data_fields(_index_text())) == len(fields)


def test_settings_field_widgets_match_value_types():
    html = _settings_html()
    tags = {f: t for t, f in re.findall(r'<(select|input|textarea)\b[^>]*\bdata-field="([^"]+)"', html)}
    for role in settings.LLM_ROLES:
        assert tags[f"roles.{role}.model"] == "select"
        assert re.search(rf'data-field="roles\.{role}\.model" data-options="ollama"', html)
        assert re.search(rf'data-field="roles\.{role}\.think" data-kind="think"', html)
        assert re.search(rf'data-field="roles\.{role}\.num_ctx" data-kind="int"', html)
    assert re.search(r'data-field="roles\.image\.steps" data-kind="int"', html)
    assert re.search(r'data-field="roles\.voice\.wav" data-options="voice"', html)
    assert tags["roles.voice.text"] == "textarea"
    for seat in settings.SEATS:
        assert re.search(rf'data-field="review_seats\.{seat}" data-options="seat"', html), seat
    # think 三態:true / false / null(同 settings.validate)
    for v in ("true", "false", "null"):
        assert f'<option value="{v}">' in html


def test_js_defaults_equal_settings_defaults():
    assert _js_json_const("SETTINGS_DEFAULTS") == settings.DEFAULTS


def test_js_defaults_security_compliance_are_codex():
    seats = _js_json_const("SETTINGS_DEFAULTS")["review_seats"]
    assert seats["security"] == settings.CODEX and seats["compliance"] == settings.CODEX


def test_js_seat_rules_match_settings():
    rules = _js_json_const("SEAT_RULES")
    assert rules["codex"] == settings.CODEX
    assert rules["veto_seats"] == list(settings.VETO_SEATS)
    assert rules["seats"] == list(settings.SEATS)
    assert set(rules["paused"]) == set(settings.PAUSED_REVIEW_MODELS)
    assert rules["veto_banned_prefix"] == "glm4:"


def test_js_field_limits_match_settings():
    lim = _js_json_const("FIELD_LIMITS")
    assert tuple(lim["num_ctx"]) == settings.NUM_CTX_RANGE
    assert tuple(lim["steps"]) == settings.STEPS_RANGE
    assert lim["text_max"] == settings.MAX_TEXT_LEN
    assert lim["name_max"] == settings.MAX_NAME_LEN


# 審議席位候選模型的樣本矩陣
SEAT_MODELS = sorted({
    "glm4:latest", "glm4:9b", "glm4", "qwen3:8b", "huihui_ai/qwen3-abliterated:8b",
    "hf.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M",
    "Glm4:latest", "my-glm4:latest", settings.CODEX, "Codex",
} | set(settings.PAUSED_REVIEW_MODELS))


def _server_seat_ok(seat: str, model: str) -> bool:
    cfg = copy.deepcopy(settings.DEFAULTS)
    cfg["review_seats"][seat] = model
    avail = set(SEAT_MODELS) | {settings.DEFAULTS["roles"][r]["model"] for r in settings.LLM_ROLES} \
        | {v for v in settings.DEFAULTS["review_seats"].values() if v != settings.CODEX}
    return not [e for e in settings.validate(cfg, avail) if e.startswith(f"review_seats.{seat}:")]


def _py_seat_allows(rules: dict, seat: str, model: str) -> bool:
    """app.js seatAllows 的逐行對照(沒有 JS 執行環境時用來檢查常數組合出的規則)。"""
    veto = seat in rules["veto_seats"]
    if model == rules["codex"]:
        return veto
    if model in rules["paused"]:
        return False
    if veto and model.startswith(rules["veto_banned_prefix"]):
        return False
    return True


def _seat_matrix() -> list[tuple[str, str]]:
    return [(s, m) for s in settings.SEATS for m in SEAT_MODELS]


def test_seat_rule_agrees_with_validate():
    rules = _js_json_const("SEAT_RULES")
    diff = [(s, m) for s, m in _seat_matrix() if _py_seat_allows(rules, s, m) != _server_seat_ok(s, m)]
    assert diff == []


def test_seat_rule_sample_expectations():
    assert not _server_seat_ok("security", "glm4:latest")
    assert _server_seat_ok("impact", "glm4:latest")
    assert _server_seat_ok("security", settings.CODEX)
    assert not _server_seat_ok("drift", settings.CODEX)
    for m in settings.PAUSED_REVIEW_MODELS:
        assert not _server_seat_ok("risk", m) and not _server_seat_ok("drift", m)


def test_seat_rule_lives_in_one_js_function():
    js = _app_js()
    fn = js[js.index("function seatAllows("):js.index("// seatAllows 結束")]
    assert "SEAT_RULES.codex" in fn and "SEAT_RULES.paused" in fn and "SEAT_RULES.veto_banned_prefix" in fn
    # 下拉選項只經 seatAllows 過濾;程式其他地方不另寫 glm4 判斷
    assert js.count("seatAllows(") >= 3
    code = re.sub(r"^  const (SETTINGS_DEFAULTS|SEAT_RULES) = .*$|^\s*//.*$", "", js, flags=re.M)
    assert "glm4" not in code


def _js_runtime() -> list[str] | None:
    node = shutil.which("node")
    if node:
        return [node]
    jsc = shutil.which("jsc") or "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"
    return [jsc] if Path(jsc).is_file() else None


def _run_js(body: str):
    rt = _js_runtime()
    if rt is None:
        pytest.skip("沒有 JS 執行環境(node / jsc)")
    js = _app_js()
    m = re.search(r"^  const SEAT_RULES = .*;$", js, re.M)
    fn = js[js.index("function seatAllows("):js.index("// seatAllows 結束")]
    sp = js[js.index("function splitFieldError("):]
    sp = sp[:sp.index("\n  }\n") + 4]
    script = "\n".join([m.group(0), fn, sp, body,
                        'var __out = (typeof print === "function") ? print : console.log;',
                        "__out(JSON.stringify(result));"])
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "t.js"
        f.write_text(script, encoding="utf-8")
        r = subprocess.run(rt + [str(f)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_js_seat_allows_runs_and_agrees_with_validate():
    cases = _seat_matrix()
    got = _run_js("var result = " + json.dumps(cases, ensure_ascii=True)
                  + ".map(function (c) { return seatAllows(c[0], c[1]); });")
    expected = [_server_seat_ok(s, m) for s, m in cases]
    assert [c for c, g, e in zip(cases, got, expected) if g != e] == []


def _bad_config_errors() -> list[str]:
    """一份多處不合格的設定經 settings.validate 得到的錯誤(PUT /api/config 422 的內容)。"""
    cfg = copy.deepcopy(settings.DEFAULTS)
    cfg["roles"]["reason"]["model"] = "not-downloaded:latest"
    cfg["roles"]["reason"]["num_ctx"] = 99
    cfg["roles"]["taiwan"]["think"] = "maybe"
    cfg["roles"]["image"]["steps"] = 99
    cfg["roles"]["voice"]["wav"] = "/etc/passwd"
    cfg["roles"]["voice"]["text"] = ""
    cfg["review_seats"]["security"] = "glm4:latest"
    cfg["review_seats"]["drift"] = settings.CODEX
    cfg["review_seats"]["risk"] = next(iter(settings.PAUSED_REVIEW_MODELS))
    cfg["roles"]["reason"]["extra"] = 1
    avail = {settings.DEFAULTS["roles"][r]["model"] for r in settings.LLM_ROLES} | {"glm4:latest", "qwen3:8b"}
    return settings.validate(cfg, avail)


def test_422_errors_map_to_settings_fields():
    js = _app_js()
    key_re = re.search(r"if \(!/(\^[^/]+\$)/\.test\(key\)\) return null;", js)
    assert key_re, "splitFieldError 必須以正規式檢查欄位鍵"
    pat = re.compile(key_re.group(1))
    fields = set(_data_fields(_settings_html()))
    errors = _bad_config_errors()
    mapped = {}
    unmatched = []
    for e in errors:
        key = e.split(":", 1)[0]
        if pat.fullmatch(key) and key in fields:
            mapped.setdefault(key, []).append(e)
        else:
            unmatched.append(e)
    for f in ("roles.reason.model", "roles.reason.num_ctx", "roles.taiwan.think", "roles.image.steps",
              "roles.voice.wav", "roles.voice.text", "review_seats.security", "review_seats.drift",
              "review_seats.risk"):
        assert f in mapped, f
    # 未知鍵這類沒有對應欄位的錯誤列在頂端
    assert any("roles.reason.extra" in e for e in unmatched)


def test_js_split_field_error_runs():
    errors = _bad_config_errors() + ["設定:必須是物件", "請求內容不是合法的 JSON"]
    got = _run_js("var result = " + json.dumps(errors, ensure_ascii=True)
                  + ".map(function (e) { var r = splitFieldError(e); return r ? r.field : null; });")
    assert got[-2:] == [None, None]
    for e, f in zip(errors, got):
        if f is not None:
            assert e.startswith(f + ":")
    assert "review_seats.security" in got and "roles.voice.wav" in got


def test_settings_page_header_has_no_local_word():
    html = _settings_html()
    header = html[html.index("<header"):html.index("</header>")]
    assert "本機" not in header
    assert "← 首頁" in header and "設定" in header
    assert 'role="tab"' not in html


def test_home_has_settings_icon_button_and_no_tabs():
    html = _index_text()
    home = _section(html, "page-home")
    assert re.search(r'<button\b[^>]*\baria-label="設定"[^>]*>⚙</button>', home)
    assert not re.search(r'role\s*=\s*["\']tab', html)
    assert not re.search(r'["\']tab["\']', _app_js())


def test_settings_page_has_actions_notices_and_disclaimer_link():
    html = _settings_html()
    for needed in ('id="cfg-reset">還原預設<', 'id="cfg-save"', 'id="cfg-warnings"', 'id="cfg-imported"',
                   'id="cfg-errors"', 'id="cfg-ollama-down"', 'href="#/disclaimer"', ">免責說明與授權<"):
        assert needed in html, needed
    # 子頁:模型、審議席位、語音預設、進階流程
    for sub in ("models", "seats", "voice", "flow"):
        assert f'href="#/settings/{sub}" data-sub="{sub}"' in html
    disc = _section(_index_text(), "page-disclaimer")
    assert "← 首頁" in disc and "尚未提供" in disc
    assert "http" not in disc


def test_app_js_settings_flow():
    js = _app_js()
    assert _js_const("OLLAMA_DOWN_TEXT", r'"([^"]*)"') == "Ollama 未啟動,無法儲存模型設定"
    assert 'api("GET", "/api/config")' in js and 'api("GET", "/api/models")' in js
    assert 'api("PUT", "/api/config", this.collect())' in js
    i = js.index("async save() {")
    save = js[i:js.index("    init() {", i)]
    assert "r.status === 503" in save and "r.status === 422" in save and "showFieldErrors" in save
    # 儲存鍵在 Ollama 連不上時停用
    assert "!this.ollamaUp" in js
    # 「已匯入一次」只看 meta.imported_from;_warnings 以橫幅顯示
    assert "meta.imported_from" in js and "已從 " in js and "匯入一次" in js
    assert "data._warnings" in js
    # 還原預設只填表單(來自 SETTINGS_DEFAULTS),不直接存檔
    reset = js[js.index("    reset() {", js.index("const Settings = {")):i]
    assert "this.fill(SETTINGS_DEFAULTS)" in reset and "api(" not in reset
    assert 'location.hash = "#/settings"' in js
    assert "#\\/disclaimer" in js
