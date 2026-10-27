# -*- coding: utf-8 -*-
"""前端靜態檔的測試(CHG-20261022-01 P1、T10、T11、T12;CHG-20261022-04 T7)。

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
    # CHG-20261022-04 T7 把預留位置換成正式內容(詳見下方 T7 測試)
    assert "← 首頁" in disc and "尚未提供" not in disc
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


# ---------- CHG-20261022-04 T7:首頁雙卡片、精靈、完整模式、剪輯與預覽、免責頁 ----------

MODULES = ("project.js", "timeline.js", "wizard.js")
ALL_JS = ("app.js",) + MODULES


def _js(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _js_strings(name: str) -> list[str]:
    """JS 原始碼中的雙引號字串常值(不含整行註解)。"""
    code = re.sub(r"^\s*//.*$", "", _js(name), flags=re.M)
    return re.findall(r'"((?:[^"\\\n]|\\.)*)"', code)


def _home() -> str:
    return _section(_index_text(), "page-home")


def _wizard_html() -> str:
    return _section(_index_text(), "page-wizard")


def _full_html() -> str:
    return _section(_index_text(), "page-full")


def _parking_html() -> str:
    html = _index_text()
    i = html.index('<div class="parking" id="parking"')
    return html[i:html.index("\n</div>", i)]


def test_t7_modules_loaded_from_static_in_order():
    srcs = re.findall(r'<script src="([^"]+)" defer></script>', _index_text())
    assert srcs == ["/static/vendor/drawflow.min.js", "/static/app.js", "/static/project.js",
                    "/static/timeline.js", "/static/wizard.js"]
    for name in MODULES:
        assert (STATIC / name).is_file(), name


@pytest.mark.parametrize("name", ALL_JS)
def test_t7_js_is_csp_and_xss_safe(name):
    js = _js(name)
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                "new Function", "setTimeout(\"", "setInterval(\"", "http://", "https://",
                "setAttribute(\"style\"", "FormData"):
        assert bad not in js, (name, bad)
    assert not re.search(r'["\']tab["\']', js), name


@pytest.mark.parametrize("name", ALL_JS)
def test_t7_js_only_requests_local_paths(name):
    # 字串常值不含任何外部位址;請求一律是同源的 /api/、/files/(專案 API 以 Store.url() 組出 /api/projects/…)
    for s in _js_strings(name):
        assert "://" not in s and not s.startswith("//"), (name, s)
    code = _js(name)
    pat = r'\b(?:(?:fetch|EventSource)\(\s*|api\(\s*"[A-Z]+",\s*)"([^"]*)"'
    for m in re.finditer(pat, code):
        assert m.group(1).startswith(("/api/", "/files/")), (name, m.group(1))


def test_home_has_exactly_two_cards_with_titles():
    home = _home()
    cards = re.findall(r'<a class="card" id="([^"]+)" href="([^"]+)">(.*?)</a>', home, re.S)
    assert len(re.findall(r'class="card"', home)) == 2
    assert len(cards) == 2
    titles = [re.search(r'<span class="card-title">([^<]*)</span>', body).group(1) for _, _, body in cards]
    assert titles == ["精靈", "完整模式"]
    assert [href for _, href, _ in cards] == ["#/wizard", "#/full"]


def test_cards_route_to_wizard_and_full_pages():
    js = _js("wizard.js")
    assert re.search(r'S\.addRoute\(WIZARD_RE, "wizard"', js)
    assert re.search(r'S\.addRoute\(FULL_RE, "full"', js)
    wiz = re.compile(re.search(r"const WIZARD_RE = /(.*)/;", js).group(1).replace(r"\/", "/"))
    full = re.compile(re.search(r"const FULL_RE = /(.*)/;", js).group(1).replace(r"\/", "/"))
    assert wiz.match("#/wizard") and wiz.match("#/wizard/%E5%A4%9C/3") and not wiz.match("#/full")
    assert full.match("#/full") and full.match("#/full/abc") and not full.match("#/wizard")
    html = _index_text()
    assert '<section id="page-wizard" class="page" data-page="wizard"' in html
    assert '<section id="page-full" class="page" data-page="full"' in html
    # app.js 的路由以 data-page 切換頁面,其他模組登錄的路由在預設(首頁)之前比對
    app = _app_js()
    assert 'document.querySelectorAll("body > section.page")' in app and "extraRoutes" in app


def test_home_has_one_settings_icon_in_header():
    home = _home()
    assert len(re.findall(r'aria-label="設定"', home)) == 1
    header = home[home.index("<header"):home.index("</header>")]
    assert re.search(r'<button\b[^>]*\baria-label="設定"[^>]*>⚙</button>', header)


def test_home_footer_has_one_disclaimer_link_and_settings_too():
    home = _home()
    assert home.count('href="#/disclaimer"') == 1
    footer = home[home.index("<footer"):home.index("</footer>")]
    assert re.search(r'<a [^>]*href="#/disclaimer"[^>]*>免責說明與授權</a>', footer)
    settings_html = _settings_html()
    assert settings_html.count('href="#/disclaimer"') == 1
    assert re.search(r'<a [^>]*href="#/disclaimer"[^>]*>免責說明與授權</a>', settings_html)


def test_no_tabs_anywhere():
    html = _index_text()
    assert not re.search(r'role\s*=\s*["\']tab(list|panel)?["\']', html)
    assert not re.search(r'class="[^"]*\btabs?\b[^"]*"', html)
    assert not re.search(r'class="[^"]*\btab-', html)


def test_full_mode_has_six_blocks_and_no_prev_next():
    full = _full_html()
    blocks = dict(re.findall(r'<section class="panel [^"]*" id="panel-([a-z]+)" data-block="([a-z]+)"', full))
    assert set(blocks) == {"script", "cover", "library", "preview", "props", "timeline"}
    titles = re.findall(r'<h2 class="panel-title">([^<]+)</h2>', full)
    for t in ("文稿", "封面", "素材庫", "預覽", "屬性", "時間軸"):
        assert t in titles, t
    assert "上一步" not in full and "下一步" not in full
    assert "wizard-nav" not in full


def test_wizard_has_only_prev_next_and_no_hardcoded_steps():
    wiz = _wizard_html()
    assert '<button type="button" class="btn" id="wizard-prev">上一步</button>' in wiz
    assert '<button type="button" class="btn btn-primary" id="wizard-next">下一步</button>' in wiz
    for label in ("文稿", "語音", "封面", "剪輯", "匯出"):
        assert label not in wiz, label
    js = _js("wizard.js")
    assert "S.Flows.workflow(true)" in js and "checkSteps(wf.graph.steps)" in js
    assert "this.steps = wf.graph.steps" in js
    assert 'S.api("GET", "/api/workflows/default")' in _js("project.js")
    for name in ALL_JS:
        code = _js(name)
        # 不發清單請求(修訂 3 R3-1:不新增 GET /api/workflows 清單端點)
        assert not re.search(r'"/api/workflows/?"\s*[,)]', code), name
        assert not re.search(r'\[\s*"文稿"\s*,\s*"語音"', code), name


def test_wizard_shows_error_instead_of_fallback_steps():
    js = _js("wizard.js")
    enter = js[js.index("async enter(m) {"):js.index("renderSteps() {")]
    assert "this.showError(" in enter and "RESTORE_HINT" in enter
    assert "git checkout -- workflows/default.json" in js
    assert 'id="wizard-error"' in _wizard_html()


def test_no_local_word_or_arrow_sequence_in_pages():
    for part in (_home(), _wizard_html(), _full_html(), _section(_index_text(), "page-disclaimer"),
                 _parking_html()):
        assert "本機" not in part
        assert "→" not in part
    for name in MODULES:
        for s in _js_strings(name):
            assert "本機" not in s and "→" not in s, (name, s)


def test_build_graph_keeps_steps():
    js = _app_js()
    build = js[js.index("    buildGraph() {"):js.index("    loadGraph(graph) {")]
    assert "carrySteps(this.loadedSteps, present)" in build and "graph.steps = steps" in build
    load = js[js.index("    loadGraph(graph) {"):js.index("    autoLayout(list, edges) {")]
    assert "this.loadedSteps = " in load and "st.origId = n.id" in load


def _run_js_funcs(parts: list[tuple[str, str, str]], body: str):
    """從 static/*.js 取出 (檔名, 起始字串, 結束標記) 之間的純函式,在 JS 執行環境實跑 body(設定 result)。"""
    rt = _js_runtime()
    if rt is None:
        pytest.skip("沒有 JS 執行環境(node / jsc)")
    code = []
    for name, start, end in parts:
        js = _js(name)
        code.append(js[js.index(start):js.index(end)])
    script = "\n".join(code + [body, 'var __out = (typeof print === "function") ? print : console.log;',
                               "__out(JSON.stringify(result));"])
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "t.js"
        f.write_text(script, encoding="utf-8")
        r = subprocess.run(rt + [str(f)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


CARRY = [("app.js", "function carrySteps(", "// carrySteps 結束")]


def _default_steps():
    return json.loads((ROOT / "workflows" / "default.json").read_text(encoding="utf-8"))["steps"]


def test_carry_steps_drops_removed_nodes_and_emptied_steps():
    steps = _default_steps()
    cover = next(s for s in steps if s["id"] == "cover")["nodes"]
    ids = [n for s in steps for n in s["nodes"] if n not in cover]
    present = {old: str(i + 1) for i, old in enumerate(ids)}       # 畫布重新編號後的 id
    got = _run_js_funcs(CARRY, "var result = carrySteps(" + json.dumps(steps) + ", "
                        + json.dumps(present) + ");")
    assert [s["id"] for s in got] == ["script", "voice", "edit", "export"]
    edit = next(s for s in got if s["id"] == "edit")
    assert edit["nodes"] == [] and edit["label"] == "剪輯" and "note" in edit
    script = next(s for s in got if s["id"] == "script")
    assert script["nodes"] == [present[n] for n in ("topic", "write-script", "taiwanize")]


def test_carry_steps_keeps_partial_steps_and_handles_missing():
    steps = _default_steps()
    present = {"topic": "1", "taiwanize": "3", "speak": "4", "title": "5", "make-video": "9"}
    got = _run_js_funcs(CARRY, "var result = [carrySteps(" + json.dumps(steps) + ", " + json.dumps(present)
                        + "), carrySteps(null, {}) === undefined, carrySteps(undefined, {}) === undefined, "
                        "carrySteps([{\"id\": \"x\"}, 5], {})];")
    kept, none1, none2, odd = got
    assert [s["id"] for s in kept] == ["script", "voice", "cover", "edit", "export"]
    assert kept[0]["nodes"] == ["1", "3"] and kept[2]["nodes"] == ["5"]
    assert none1 is True and none2 is True
    assert odd == [{"id": "x"}, 5]          # 格式不認得的步驟原樣保留,交給精靈顯示錯誤


def test_check_steps_accepts_default_and_rejects_bad_shapes():
    cases = [_default_steps(), None, [], {"steps": 1}, [{"id": "a", "nodes": []}],
             [{"id": "a", "label": "甲", "nodes": "x"}], [{"label": "甲", "nodes": []}],
             [{"id": "a", "label": "甲", "nodes": []}, {"id": "a", "label": "乙", "nodes": []}]]
    got = _run_js_funcs([("wizard.js", "function checkSteps(", "// 精靈步驟檢查結束")],
                        "var result = " + json.dumps(cases, ensure_ascii=True) + ".map(function (c) { return checkSteps(c); });")
    assert got[0] == ""
    assert all(isinstance(m, str) and m and re.search(r"[一-鿿]", m) for m in got[1:])


def test_preview_schedule_matches_export_rules():
    images = [{"id": "a", "file": "A", "start": 0, "end": 10}, {"id": "b", "file": "B", "start": 3, "end": 6},
              {"id": "c", "file": "C", "start": 12, "end": 14}]
    segs = [{"id": "s1", "start": 0, "end": 1.5}, {"id": "s2", "start": 1.5, "end": 1.5},
            {"id": "s3", "start": 1.9, "end": 3}]
    cues = [{"segment": "s1", "start": 0, "end": 1.5, "text": "hello"}]
    body = ("var I = " + json.dumps(images) + ", S2 = " + json.dumps(segs) + ", C = " + json.dumps(cues) + ";\n"
            "function f(m) { return m ? (m.file || m.text || m.id) : null; }\n"
            "var result = [2, 4, 6, 7, 11, 13, 14].map(function (t) { return f(activeImage(I, t)); })"
            ".concat([0.5, 1.5, 1.7, 2].map(function (t) { return f(activeSegment(S2, t)); }))"
            ".concat([0.2, 1.6].map(function (t) { return f(activeCue(C, t)); }));")
    got = _run_js_funcs([("timeline.js", "function activeSegment(", "// 預覽排程結束")], body)
    # 後開始的圖蓋過先前的圖;沒有圖的時段為黑畫面(null);長度 0 的片段與停頓處不出聲
    assert got[:7] == ["A", "B", "A", "A", None, "C", None]
    assert got[7:11] == ["s1", None, None, "s3"]
    assert got[11:] == ["hello", None]


def test_preview_uses_layout_and_playback_rate():
    js = _js("timeline.js")
    assert "this.audio.playbackRate = speed" in js
    assert "Store.mediaUrl(seg.audio.id)" in js
    assert "activeImage(lay.images, t)" in js and "activeCue(lay.cues, t)" in js
    assert "activeSegment(this.layout().segments, this.t)" in js


def test_editor_leaves_hooks_for_t8():
    tl = _js("timeline.js")
    for needed in ('"tl-gap"', "gap.dataset.after = prev.id", '"tl-edge tl-edge-" + edge', "h.dataset.edge = edge",
                   '"speed")', '"pause")', 'tone.dataset.hook = "tone"', 'range.dataset.hook = "image-range"',
                   'S.runHook("segment-props"', 'S.runHook("image-props"', 'S.runHook("timeline"'):
        assert needed in tl, needed
    pj_js = _js("project.js")
    assert 'speed.dataset.hook = "speed"' in pj_js and 'S.runHook("voice-row"' in pj_js
    assert "S.hook = function (name, fn)" in pj_js


def test_wizard_steps_accept_dropped_files():
    pj_js = _js("project.js")
    tl = _js("timeline.js")
    # 文稿:.txt/.md;語音:WAV 取代一句(伺服器只收 RIFF/WAVE);封面與畫面軌:圖片;素材庫:白名單檔
    assert 'TEXT_EXTS = [".txt", ".md"]' in pj_js
    assert 'S.uploadExt(file && file.name) !== ".wav"' in pj_js and "{ audio_file: up.file_id }" in pj_js
    assert 'Store.mutate("POST", "/images", body)' in pj_js
    assert 'Store.mutate("POST", "/assets"' in pj_js
    assert "S.replaceAudio(target.dataset.segment, files[0])" in tl
    # 上傳一律經 app.js 的 uploadFile(原始位元組 + ?name=,帶權杖)
    for name in MODULES:
        assert "/api/upload" not in _js(name), name


def _license_rows() -> list[list[str]]:
    disc = _section(_index_text(), "page-disclaimer")
    table = disc[disc.index('<table class="license-table"'):disc.index("</table>")]
    assert re.findall(r"<th[^>]*>([^<]*)</th>", table) == ["元件", "用途", "授權", "來源"]
    rows = re.findall(r"<tr>(.*?)</tr>", table[table.index("<tbody>"):], re.S)
    return [re.findall(r"<td>([^<]*)</td>", r) for r in rows]


def test_disclaimer_page_content_and_license_table():
    disc = _section(_index_text(), "page-disclaimer")
    assert "AI 生成內容聲明" in disc and "同意" in disc and "聲線" in disc
    rows = _license_rows()
    assert len(rows) >= 5
    for cells in rows:
        assert len(cells) == 4 and all(c.strip() for c in cells), cells
        # 每列都有來源欄;授權取不到時授權與來源都標「未知」
        assert cells[3].startswith("未知") == (cells[2] == "未知"), cells
    by_name = {c[0]: c for c in rows}
    drawflow = next(v for k, v in by_name.items() if k.startswith("Drawflow"))
    assert drawflow[2] == "MIT" and drawflow[3] == "static/vendor/LICENSE.drawflow"
    assert (VENDOR / "LICENSE.drawflow").read_text(encoding="utf-8").startswith("MIT License")
    breezy = by_name["BreezyVoice 程式碼"]
    assert breezy[2] == "Apache License 2.0" and breezy[3] == "tools/BreezyVoice/LICENSE"
    lic_file = ROOT / "tools" / "BreezyVoice" / "LICENSE"
    if lic_file.is_file():   # tools/ 不入 git;本機有時才比對
        assert "Apache License" in lic_file.read_text(encoding="utf-8")[:200]


def test_disclaimer_opened_by_links_not_tabs():
    assert _index_text().count('href="#/disclaimer"') == 2          # 首頁底部、設定頁底部
    assert "#\\/disclaimer" in _app_js()


def test_served_t7_modules(client):
    for name in MODULES:
        r = client.get("/static/" + name)
        assert r.status_code == 200, name
        assert "javascript" in r.headers["content-type"]
        assert r.headers["content-security-policy"] == ss.CSP
