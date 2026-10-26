#!/usr/bin/env -S uv run --script
# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "playwright==1.64.1",
#   "fastapi>=0.115",
#   "uvicorn>=0.30",
#   "python-multipart>=0.0.9",
#   "opencc-python-reimplemented",
#   "imageio-ffmpeg",
#   "pillow<13",
# ]
# ///
"""UI 互動檢查(CHG-20261022-01 T14):以無頭 Chromium 實際操作網頁。

用法:`uv run --script tests/ui_check.py`(需先依 P2 安裝 Chromium:
`uv run --with playwright==1.64.1 playwright install chromium`)。
本檔不叫 test_*.py,pytest 與 `scripts/check.sh` 不會收集它。

做法:
- 伺服器在本行程內以 `studio_server.create_app()` 建立,uvicorn 綁 127.0.0.1 的隨機空閒埠,
  不碰使用者正在 7860 上跑的伺服器。
- 模型一律用替身:執行器換成替身 runner(不呼叫 Ollama / mflux / BreezyVoice / ffmpeg),
  Ollama 已下載清單換成固定替身。
- `outputs/`、`workflows/`、`config/` 全部放在暫存目錄(`settings.ROOT` 與 `create_app(root=…)`
  都指過去),不碰 repo 的 `config/studio.db`、`outputs/`、`workflows/`;只有截圖寫到 repo 的
  `outputs/ui_check/`(已被 .gitignore 忽略)。
- 伺服器只接受 Host 為 127.0.0.1:7860 / localhost:7860 的請求。本檔只在**自己的行程內**把
  `studio_server.ALLOWED_HOSTS` / `ALLOWED_ORIGINS` 換成這個隨機埠的對應值(與 pytest
  monkeypatch 相同性質),結束時還原;伺服器程式與預設值都不變。

每一步都檢查畫面上看得到的東西(狀態列文字、class、元素);任何一步不符即印出
「UI 檢查失敗:步驟「…」」並以非零值結束。全程另外收集瀏覽器 console 錯誤、未捕捉例外與
CSP 違規,有任何一筆也算失敗。
"""
from __future__ import annotations

import json
import re
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

SHOT_DIR = REPO / "outputs" / "ui_check"
VIEWPORT = {"width": 2300, "height": 1250}
TIMEOUT_MS = 10_000

STUB_TAGS = [
    "hf.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M",
    "hf.co/HauhauCS/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF:IQ3_M",
    "hf.co/mradermacher/Llama-Breeze2-8B-Instruct-text-only-GGUF:Q4_K_M",
    "qwen3:8b",
    "glm4:latest",
]
NEW_REASON_MODEL = "qwen3:8b"
EDITED_TEXT = "人工修改過的台灣用語文案(UI 檢查)"


class StepFailed(Exception):
    pass


def check(cond, message: str) -> None:
    if not cond:
        raise StepFailed(message)


# ---------- 替身 ----------

class StubRunner:
    """替身執行器:依節點清單順序送出事件;文字輸出給固定字串,媒體輸出不給(不產生檔案)。

    釘住的節點送 skipped(reason=pinned),與 executor.run 的事件格式相同。
    """

    def __init__(self, specs):
        self.specs = specs
        self.graphs: list[dict] = []

    def __call__(self, graph, emit, cancel=None):
        self.graphs.append(json.loads(json.dumps(graph)))
        order = [n["id"] for n in graph["nodes"]]
        emit({"type": "started", "order": order})
        for n in graph["nodes"]:
            nid = n["id"]
            if n.get("pinned"):
                emit({"type": "node", "node": nid, "status": "skipped", "reason": "pinned",
                      "outputs": n.get("outputs", {})})
                continue
            emit({"type": "node", "node": nid, "status": "running"})
            spec = self.specs[n["kind"]]
            outs = {name: f"替身輸出:{n['kind']}" for name in spec.output_ports(n.get("params", {}))
                    if spec.outputs[name].type == "text"}
            emit({"type": "node", "node": nid, "status": "done", "outputs": outs})
        emit({"type": "finished", "status": "ok"})


def tiny_png() -> bytes:
    import zlib
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"\x00\xff\xff\xff"
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def tiny_wav() -> bytes:
    pcm = b"\x00\x00" * 800
    fmt = struct.pack("<HHIIHH", 1, 1, 16000, 32000, 2, 16)
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


def make_root(tmp: Path) -> Path:
    root = tmp / "repo"
    for d in ("outputs", "workflows", "config", "tools/BreezyVoice/data"):
        (root / d).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO / "workflows" / "default.json", root / "workflows" / "default.json")
    (root / "tools/BreezyVoice/data/example.wav").write_bytes(tiny_wav())
    return root


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def snapshot(path: Path):
    """回傳檔案的 (存在, 大小, mtime_ns),用來確認 repo 內的檔案沒被碰。"""
    try:
        st = path.stat()
        return (True, st.st_size, st.st_mtime_ns)
    except FileNotFoundError:
        return (False, 0, 0)


# ---------- 瀏覽器端小工具 ----------

INIT_SCRIPT = """
window.__csp = [];
document.addEventListener('securitypolicyviolation', function (e) {
  window.__csp.push(e.violatedDirective + ' ' + e.blockedURI);
});
"""

JS_NODE_IDS = """(label) => Array.from(document.querySelectorAll('#drawflow .drawflow-node'))
  .filter(n => { const t = n.querySelector('.node-title'); return t && t.textContent === label; })
  .map(n => n.id.replace(/^node-/, ''))"""

JS_DROP_FILES = """([selector, offX, offY, files]) => {
  const base = document.querySelector(selector);
  const r = base.getBoundingClientRect();
  const x = r.left + offX, y = r.top + offY;
  const target = document.elementFromPoint(x, y);
  const dt = new DataTransfer();
  files.forEach(f => {
    const bin = atob(f.b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    dt.items.add(new File([bytes], f.name, { type: f.type }));
  });
  const opts = { dataTransfer: dt, clientX: x, clientY: y, bubbles: true, cancelable: true };
  target.dispatchEvent(new DragEvent('dragenter', opts));
  target.dispatchEvent(new DragEvent('dragover', opts));
  target.dispatchEvent(new DragEvent('drop', opts));
  return target.className && target.className.baseVal !== undefined ? 'svg' : String(target.className);
}"""


def file_arg(name: str, data: bytes, mime: str) -> dict:
    import base64
    return {"name": name, "type": mime, "b64": base64.b64encode(data).decode("ascii")}


# ---------- 主流程 ----------

def run_checks(page, base: str, root: Path, runner: StubRunner, settings) -> None:
    from playwright.sync_api import expect

    step = {"name": ""}

    def begin(name):
        step["name"] = name
        print(f"・{name}", flush=True)

    def node_ids(label):
        return page.evaluate(JS_NODE_IDS, label)

    def one_node(label):
        ids = node_ids(label)
        check(len(ids) == 1, f"畫布上「{label}」節點應有 1 個,實際 {len(ids)} 個")
        return ids[0]

    def drag_mouse(src, dst):
        src.scroll_into_view_if_needed()
        a = src.bounding_box()
        b = dst.bounding_box()
        check(a and b, "找不到要拖拉的埠")
        page.mouse.move(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
        page.mouse.down()
        page.mouse.move((a["x"] + b["x"]) / 2, (a["y"] + b["y"]) / 2, steps=5)
        page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, steps=5)
        page.mouse.up()

    def connections(out_id, in_id):
        return page.locator(f"#drawflow svg.connection.node_out_node-{out_id}.node_in_node-{in_id}")

    toast = page.locator("#toast")
    status = page.locator("#statusbar")

    try:
        # 1. 首頁 → ⚙ → 設定
        begin("開啟首頁")
        page.goto(base + "/")
        expect(page.locator("#page-home")).to_be_visible()
        check(page.locator('[role="tab"]').count() == 0, "首頁不應有 role=tab 的分頁元素")

        begin("點右上角 ⚙ 進入設定")
        page.get_by_role("button", name="設定").click()
        expect(page.locator("#page-settings")).to_be_visible()
        expect(page.locator("#page-home")).to_be_hidden()
        expect(page.locator("#page-settings .back-link")).to_have_text("← 首頁")
        check(page.locator('[role="tab"]').count() == 0, "設定頁不應有 role=tab 的分頁元素")

        # 2. 設定 › 進階流程,預設範本
        begin("進入「設定 › 進階流程」並自動載入預設範本")
        page.locator('.subnav-link[data-sub="flow"]').click()
        expect(page.locator('.subnav-link[data-sub="flow"]')).to_have_attribute("aria-current", "page")
        expect(status).to_have_text("已載入預設流程")
        expect(page.locator("#drawflow .drawflow-node")).to_have_count(9)
        expect(page.locator("#drawflow svg.connection")).to_have_count(11)

        begin("以「載入」對話框載入預設範本")
        page.locator("#btn-load").click()
        expect(page.locator("#wf-dialog")).to_be_visible()
        page.locator("#wf-name").fill("default")
        page.locator("#wf-submit").click()
        expect(page.locator("#wf-dialog")).to_be_hidden()
        expect(status).to_have_text("已載入:「default」")
        expect(page.locator("#flow-name")).to_have_text("流程:default")
        expect(page.locator("#drawflow .drawflow-node")).to_have_count(9)
        expect(page.locator("#drawflow svg.connection")).to_have_count(11)
        for label in ("寫文案", "台灣用語", "語音", "封面 prompt", "生圖", "疊標題", "合成影片"):
            one_node(label)

        # 3. 從節點清單拖出節點
        begin("從節點清單把「AI 修改」拖到畫布")
        canvas = page.locator("#drawflow")
        box = canvas.bounding_box()
        page.locator('.palette-item[data-kind="revise"]').drag_to(
            canvas, target_position={"x": 340, "y": box["height"] - 260})
        expect(page.locator("#drawflow .drawflow-node")).to_have_count(10)
        revise = one_node("AI 修改")

        # 4. 連線
        begin("把「寫文案」的 text 輸出連到「AI 修改」的 text 輸入")
        ws = one_node("寫文案")
        drag_mouse(page.locator(f"#node-{ws} .outputs .output_1"),
                   page.locator(f"#node-{revise} .inputs .input_1"))
        expect(connections(ws, revise)).to_have_count(1)
        expect(connections(ws, revise)).to_have_class(re.compile(r"\btype-text\b"))

        begin("型別不符的連線被拒絕(生圖 image → 語音 text)")
        gi, sp = one_node("生圖"), one_node("語音")
        drag_mouse(page.locator(f"#node-{gi} .outputs .output_1"),
                   page.locator(f"#node-{sp} .inputs .input_1"))
        expect(toast).to_be_visible()
        expect(toast).to_have_text("型別不符:image 不能接到 text")
        expect(toast).to_have_class(re.compile(r"\btoast-error\b"))
        expect(connections(gi, sp)).to_have_count(0)
        expect(page.locator("#drawflow svg.connection")).to_have_count(12)
        page.screenshot(path=str(SHOT_DIR / "canvas.png"))

        # 5. 檔案拖放
        uploads = root / "outputs" / "uploads"
        begin("把「文稿.txt」拖到畫布空白處,建立 text 檔案節點")
        page.evaluate(JS_DROP_FILES, ["#drawflow", 700, box["height"] - 220,
                                      [file_arg("文稿.txt", "台灣夜市文化的文稿。\n".encode("utf-8"), "text/plain")]])
        expect(status).to_have_text("已加入 1 個檔案節點")
        expect(page.locator("#drawflow .drawflow-node")).to_have_count(11)
        fid = one_node("檔案")
        expect(page.locator(f"#node-{fid} .node-message")).to_have_text("來源檔案:文稿.txt")
        expect(page.locator(f"#node-{fid} .outputs .output")).to_have_count(1)
        expect(page.locator(f"#node-{fid} .outputs .output_1")).to_have_class(re.compile(r"\bport-text\b"))
        saved = sorted(p.name for p in uploads.iterdir())
        check(len(saved) == 1 and saved[0].endswith(".txt") and "文稿" not in saved[0],
              f"上傳檔應存成 outputs/uploads/<伺服器產生的名稱>.txt,實際 {saved}")

        begin("把「封面.png」拖到「語音」的 text 輸入口,提示型別不符且輸入不變")
        tw = one_node("台灣用語")
        before = connections(tw, sp).count()
        port = page.locator(f"#node-{sp} .inputs .input_1")
        pb = port.bounding_box()
        cb = canvas.bounding_box()
        page.evaluate(JS_DROP_FILES, ["#drawflow", pb["x"] - cb["x"] + pb["width"] / 2,
                                      pb["y"] - cb["y"] + pb["height"] / 2,
                                      [file_arg("封面.png", tiny_png(), "image/png")]])
        expect(toast).to_contain_text("型別不符")
        expect(toast).to_contain_text("輸入不變")
        check(before == 1 and connections(tw, sp).count() == 1, "「語音」的輸入應維持接在「台灣用語」")
        check(len(list(uploads.iterdir())) == 1, "型別不符時不應上傳檔案")

        begin("把「錄音.wav」拖到「合成影片」的 audio 輸入口,取代該輸入")
        mv = one_node("合成影片")
        port = page.locator(f"#node-{mv} .inputs .input_2")
        port.scroll_into_view_if_needed()
        pb, cb = port.bounding_box(), canvas.bounding_box()
        page.evaluate(JS_DROP_FILES, ["#drawflow", pb["x"] - cb["x"] + pb["width"] / 2,
                                      pb["y"] - cb["y"] + pb["height"] / 2,
                                      [file_arg("錄音.wav", tiny_wav(), "audio/wav")]])
        expect(toast).to_have_text("「合成影片」的「語音」輸入已改用「錄音.wav」")
        expect(connections(sp, mv)).to_have_count(0)
        audio_ids = [i for i in node_ids("檔案") if i != fid]
        check(len(audio_ids) == 1, "應新增一個 audio 檔案節點")
        expect(connections(audio_ids[0], mv)).to_have_count(1)
        expect(connections(audio_ids[0], mv)).to_have_class(re.compile(r"\btype-audio\b"))

        begin("拖入不支援的「setup.exe」:提示且不上傳")
        page.evaluate(JS_DROP_FILES, ["#drawflow", 1700, box["height"] - 150,
                                      [file_arg("setup.exe", b"MZ\x90\x00", "application/octet-stream")]])
        expect(toast).to_contain_text("不支援此檔案類型")
        check(len(list(uploads.iterdir())) == 2, "不支援的檔案不應上傳")
        expect(page.locator("#drawflow .drawflow-node")).to_have_count(12)
        page.screenshot(path=str(SHOT_DIR / "dnd.png"))

        # 6. 執行、編輯文字、釘住
        begin("填主題與標題後按「執行」(替身模型)")
        ti = node_ids("文字")
        check(len(ti) == 2, f"應有 2 個文字輸入節點(主題、標題),實際 {len(ti)} 個")
        page.locator(f"#node-{ti[0]} .node-params textarea, #node-{ti[0]} .node-params input").first.fill("台灣夜市文化")
        page.locator(f"#node-{ti[1]} .node-params textarea, #node-{ti[1]} .node-params input").first.fill("夜市的味道")
        page.locator("#btn-run").click()
        expect(status).to_have_text("執行完成")
        expect(page.locator(f"#node-{tw} .node-status")).to_have_text("完成")
        ta = page.locator(f"#node-{tw} .node-output textarea")
        expect(ta).to_have_value("替身輸出:taiwanize")
        expect(page.locator(f"#node-{tw}")).not_to_have_class(re.compile(r"\bis-pinned\b"))

        begin("編輯「台灣用語」的輸出文字,節點顯示已釘住")
        ta.fill(EDITED_TEXT)
        expect(page.locator(f"#node-{tw}")).to_have_class(re.compile(r"\bis-pinned\b"))
        expect(page.locator(f"#node-{tw} .pin-bar")).to_be_visible()
        expect(page.locator(f"#node-{tw} .pin-bar")).to_contain_text("📌 已釘住")

        begin("再次執行:釘住的節點不重跑,送出編輯後的文字")
        page.locator("#btn-run").click()
        expect(status).to_have_text("執行完成")
        expect(page.locator(f"#node-{tw} .node-status")).to_have_text("略過")
        expect(page.locator(f"#node-{tw} .node-message")).to_have_text("已釘住,沿用編輯過的文字")
        expect(ta).to_have_value(EDITED_TEXT)
        last = runner.graphs[-1]
        pinned = [n for n in last["nodes"] if n["id"] == tw]
        check(pinned and pinned[0].get("pinned") is True and pinned[0]["outputs"] == {"text": EDITED_TEXT},
              "送出的流程圖中「台灣用語」應為 pinned 且輸出等於編輯後的文字")
        page.screenshot(path=str(SHOT_DIR / "canvas.png"))

        # 7. 設定頁存檔後重新整理仍保留
        begin("在「設定 › 模型」把推理模型改成 qwen3:8b 並儲存")
        page.locator('.subnav-link[data-sub="models"]').click()
        sel = page.locator('select[data-field="roles.reason.model"]')
        expect(sel).to_be_visible()
        expect(page.locator("#cfg-save")).to_be_enabled()
        expect(page.locator("#cfg-warnings")).to_be_hidden()
        expect(page.locator("#cfg-ollama-down")).to_be_hidden()
        check(sel.input_value() != NEW_REASON_MODEL, "推理模型一開始不應已是 qwen3:8b")
        sel.select_option(NEW_REASON_MODEL)
        page.locator("#cfg-save").click()
        expect(toast).to_have_text("設定已儲存")
        expect(page.locator("#cfg-errors")).to_be_hidden()
        check(settings.load()["roles"]["reason"]["model"] == NEW_REASON_MODEL,
              "設定資料庫(暫存目錄)中的推理模型應為 qwen3:8b")
        check((root / "config" / "studio.db").is_file(), "設定資料庫應建立在暫存目錄的 config/studio.db")

        begin("重新整理後設定仍保留")
        page.reload()
        expect(page.locator("#page-settings")).to_be_visible()
        expect(page.locator('.subnav-link[data-sub="models"]')).to_have_attribute("aria-current", "page")
        expect(page.locator("#cfg-save")).to_be_enabled()
        expect(sel).to_have_value(NEW_REASON_MODEL)
        expect(page.locator("#cfg-warnings")).to_be_hidden()
        page.screenshot(path=str(SHOT_DIR / "settings.png"))
    except StepFailed as e:
        raise StepFailed(f"步驟「{step['name']}」:{e}") from None
    except AssertionError as e:
        raise StepFailed(f"步驟「{step['name']}」:畫面不符預期({str(e).splitlines()[0] if str(e) else 'AssertionError'})") from None
    except Exception as e:  # Playwright 逾時等
        raise StepFailed(f"步驟「{step['name']}」:{type(e).__name__}:{str(e).splitlines()[0] if str(e) else ''}") from None


def main() -> int:
    try:
        from playwright.sync_api import Error as PwError, expect, sync_playwright
    except ImportError:
        print("UI 檢查失敗:找不到 playwright;請以 `uv run --script tests/ui_check.py` 執行", file=sys.stderr)
        return 2
    import uvicorn

    import nodes
    import settings
    import studio_server as ss

    expect.set_options(timeout=TIMEOUT_MS)
    default_hosts = frozenset({"127.0.0.1:7860", "localhost:7860"})
    if ss.ALLOWED_HOSTS != default_hosts:
        print("UI 檢查失敗:studio_server.ALLOWED_HOSTS 不是預期的預設值,請先更新本檔", file=sys.stderr)
        return 1

    guarded = [REPO / "config" / "studio.db", REPO / "config" / "studio.db-wal", REPO / "config" / "models.json",
               REPO / "workflows", REPO / "workflows" / "default.json", REPO / "outputs" / "uploads"]
    before = {p: snapshot(p) for p in guarded}
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("canvas.png", "dnd.png", "settings.png", "failure.png"):
        (SHOT_DIR / name).unlink(missing_ok=True)

    tmp = Path(tempfile.mkdtemp(prefix="ui_check-")).resolve()
    saved = {"settings.ROOT": settings.ROOT, "settings.CONFIG_PATH": settings.CONFIG_PATH, "ss.ROOT": ss.ROOT,
             "hosts": ss.ALLOWED_HOSTS, "origins": ss.ALLOWED_ORIGINS}
    server = None
    thread = None
    problems: list[str] = []
    try:
        root = make_root(tmp)
        settings.ROOT = root
        settings.CONFIG_PATH = root / "config" / "models.json"
        ss.ROOT = root
        port = free_port()
        ss.ALLOWED_HOSTS = frozenset({f"127.0.0.1:{port}"})
        ss.ALLOWED_ORIGINS = frozenset({f"http://127.0.0.1:{port}"})
        runner = StubRunner(nodes.REGISTRY)
        app = ss.create_app(runner=runner, root=root, ollama_tags=lambda: list(STUB_TAGS))
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                               proxy_headers=False, server_header=False))
        thread = threading.Thread(target=server.run, name="ui-check-server", daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        while not server.started:
            if time.monotonic() > deadline or not thread.is_alive():
                print("UI 檢查失敗:步驟「啟動伺服器」:伺服器沒有在 15 秒內啟動", file=sys.stderr)
                return 1
            time.sleep(0.05)
        base = f"http://127.0.0.1:{port}"

        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=True)
            except PwError as e:
                print("UI 檢查失敗:步驟「啟動 Chromium」:無法啟動瀏覽器;請先執行 "
                      "`uv run --with playwright==1.64.1 playwright install chromium`"
                      f"({str(e).splitlines()[0]})", file=sys.stderr)
                return 1
            try:
                page = browser.new_page(viewport=VIEWPORT, locale="zh-TW")
                page.set_default_timeout(TIMEOUT_MS)
                page.add_init_script(INIT_SCRIPT)
                page.on("console", lambda m: problems.append(f"console {m.type}:{m.text}")
                        if m.type in ("error", "warning") else None)
                page.on("pageerror", lambda e: problems.append(f"未捕捉例外:{e}"))
                try:
                    run_checks(page, base, root, runner, settings)
                    csp = page.evaluate("window.__csp || []")
                    problems.extend(f"CSP 違規:{c}" for c in csp)
                except StepFailed as e:
                    try:
                        page.screenshot(path=str(SHOT_DIR / "failure.png"))
                    except PwError:
                        pass
                    print(f"UI 檢查失敗:{e}", file=sys.stderr)
                    for p in problems:
                        print(f"  {p}", file=sys.stderr)
                    return 1
            finally:
                browser.close()
    finally:
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=10)
        settings.ROOT, ss.ROOT = saved["settings.ROOT"], saved["ss.ROOT"]
        settings.CONFIG_PATH = saved["settings.CONFIG_PATH"]
        ss.ALLOWED_HOSTS, ss.ALLOWED_ORIGINS = saved["hosts"], saved["origins"]
        shutil.rmtree(tmp, ignore_errors=True)

    if problems:
        print("UI 檢查失敗:步驟「瀏覽器 console 與 CSP」:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    after = {p: snapshot(p) for p in guarded}
    changed = [str(p.relative_to(REPO)) for p in guarded if before[p] != after[p]]
    if changed:
        print(f"UI 檢查失敗:步驟「確認未碰 repo 檔案」:{'、'.join(changed)} 被改動", file=sys.stderr)
        return 1
    missing = [n for n in ("canvas.png", "dnd.png", "settings.png") if not (SHOT_DIR / n).is_file()]
    if missing:
        print(f"UI 檢查失敗:步驟「截圖」:缺少 {'、'.join(missing)}", file=sys.stderr)
        return 1
    print("UI 檢查通過;截圖:")
    for n in ("canvas.png", "dnd.png", "settings.png"):
        print(f"  {SHOT_DIR / n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
