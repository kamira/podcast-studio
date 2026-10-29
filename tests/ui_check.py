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
"""UI 互動檢查(CHG-20261022-01 T14;CHG-20261022-04 T9 擴充):以無頭 Chromium 實際操作網頁。

用法:`uv run --script tests/ui_check.py`(需先依 P2 安裝 Chromium:
`uv run --with playwright==1.64.1 playwright install chromium`)。
本檔不叫 test_*.py,pytest 與 `scripts/check.sh` 不會收集它。

兩組檢查,各自用一個暫存目錄與一個伺服器:
- `canvas`(CHG-20261022-01):節點畫布、檔案拖放、設定頁;截圖 canvas.png、dnd.png、settings.png。
  (CHG-20261029-01 T9)設定頁「合音詞庫」:列表、匯出、差異預覽匯入與三類提示訊息;截圖 settings-fusion.png。
- `editor`(CHG-20261022-04 T9):首頁雙卡片、精靈 ①~⑤、完整模式、剪輯互動、免責頁、
  畫布刪除封面步驟後的精靈步驟;截圖 home.png、wizard.png、full.png、editor.png、tone.png、disclaimer.png。
  (CHG-20261029-01 T5)屬性面板的連音、清晰度拉桿與字詞清晰度;截圖 prosody.png。
  (CHG-20261029-01 T6)時間軸語調折線(新增、拖曳、刪除關鍵點、圓弧)與預覽播放後製檔;截圖 pitch.png。後製的 shape worker
  以本機 Python 替身代替(輸入照抄成輸出,只用標準庫)。
  (CHG-20261029-01 修訂 1 T13)清晰度低於 25% 的警告(整句與字詞)、時間軸的自動補點與放大峰谷、
  合音由詞庫套用時帶顯示字;截圖 clarity-warn.png、pitch-auto.png。替身 shape worker 以
  `breezy_worker.expand_points` 算出 pitch_points(其中一點示意吸附,位置與前端試算不同)。
不帶參數兩組都跑(先 canvas 再 editor);`--only editor` 只跑 editor。

editor 的替身:語音合成走真正的 `tts_service.synth`,只把 worker 換成本機 Python 替身腳本
(每句寫一個真的 16 kHz 正弦波 WAV,預覽可實際播放);聲線設定以替身 `taiwan_ai.role_config` 提供;
匯出(render)與注音(annotate)以 `create_app(render=…, annotate=…)` 換成替身。
`project.ROOT`、`taiwan_ai.ROOT` / `OUT`、`heavy_lock.ROOT` / `LOCK_PATH`、`tts_service.BREEZY`
都指到暫存目錄,結束時還原;repo 的 `projects/` 不會被碰。

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

import argparse
import json
import math
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

def run_checks(page, base: str, root: Path, runner: StubRunner, settings, problems: list) -> None:
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

        # CHG-20261029-01 T9:設定 › 合音詞庫(列表、匯出、差異預覽匯入、提示訊息)
        fusion_steps(page, expect, begin, settings, toast, problems)
    except StepFailed as e:
        raise StepFailed(f"步驟「{step['name']}」:{e}") from None
    except AssertionError as e:
        raise StepFailed(f"步驟「{step['name']}」:畫面不符預期({str(e).splitlines()[0] if str(e) else 'AssertionError'})") from None
    except Exception as e:  # Playwright 逾時等
        raise StepFailed(f"步驟「{step['name']}」:{type(e).__name__}:{str(e).splitlines()[0] if str(e) else ''}") from None


FUSION_FORMAT = "podcast-studio-fusions"


def fusion_file(words) -> dict:
    return {"name": "詞庫.json", "mimeType": "application/json",
            "buffer": json.dumps({"format": FUSION_FORMAT, "version": 1, "words": words}, ensure_ascii=False).encode("utf-8")}


def fusion_steps(page, expect, begin, settings, toast, problems: list) -> None:
    """CHG-20261029-01 T9:設定頁「合音詞庫」子頁。跳出訊息(#toast.is-error)只在格式錯誤、超過上限、
    匯入失敗三類出現;一致、不一致、含被拒詞條的預覽都不跳出訊息。"""
    preview = page.locator("#fusion-preview")

    def quiet_toast():
        page.evaluate("() => { const t = document.getElementById('toast'); t.hidden = true; t.classList.remove('is-error'); }")

    def no_popup():
        check(page.evaluate("document.getElementById('toast').hidden"), "這個情況不應出現跳出的提示訊息")

    def allow_one(status):
        """預期的錯誤回應(格式錯誤 422、超過上限 413、版本不符 409)瀏覽器會記一筆 console error;只容許一筆。"""
        hit = [p for p in problems if f"status of {status}" in p]
        check(len(hit) == 1, f"應恰有一筆 {status} 回應:{hit}")
        problems.remove(hit[0])

    def upload(f):
        quiet_toast()
        page.locator("#fusion-file").set_input_files(f)

    begin("設定 › 合音詞庫:列出 6 筆預設詞")
    page.locator('.subnav-link[data-sub="fusion"]').click()
    rows = page.locator("#fusion-rows tr[data-word]")
    expect(rows).to_have_count(len(settings.FUSION_DEFAULTS))

    begin("合音詞庫:匯出 JSON 的內容等於 GET /api/settings/fusions")
    with page.expect_download() as dl:
        page.locator("#fusion-export-json").click()
    exported = json.loads(Path(dl.value.path()).read_text(encoding="utf-8"))
    check(exported["words"] == settings.load_fusions()["words"], "匯出 JSON 應等於詞庫列表")

    begin("合音詞庫:匯入與現有詞庫相同的檔案 → 只顯示一行狀態,沒有確認按鈕")
    upload(fusion_file(exported["words"]))
    expect(preview.locator(".fusion-same")).to_have_text("與現有詞庫一致,無需更動")
    expect(page.locator("#fusion-apply")).to_have_count(0)
    expect(page.locator("#fusion-diff")).to_have_count(0)
    no_popup()

    begin("合音詞庫:新增自訂詞「然後」")
    page.locator("#fusion-new-word").fill("然後")
    page.locator("#fusion-new-zhuyin").fill("ㄖㄢˊ")
    page.locator("#fusion-add").click()
    expect(rows).to_have_count(len(settings.FUSION_DEFAULTS) + 1)

    begin("合音詞庫:匯入不一致的檔案 → 警告、圖例與五欄差異表")
    words = [dict(w, zhuyin="ㄓㄠˋ") if w["word"] == "知道" else dict(w, display="降") if w["word"] == "這樣" else w
             for w in exported["words"] if w["word"] != "我們"]
    words.append({"word": "好像", "zhuyin": "ㄏㄠˇ", "enabled": True, "source": "custom"})
    upload(fusion_file(words))
    table = page.locator("#fusion-diff")
    expect(table).to_be_visible()
    expect(preview.locator(".fusion-warn")).to_be_visible()
    expect(preview.locator(".fusion-legend")).to_be_visible()
    expect(table.locator("thead th")).to_have_text(["保留", "類別", "詞", "合音注音", "啟用"])
    upd = table.locator('tr[data-word="知道"] td')
    expect(upd.nth(3)).to_have_text("ㄗㄠˋ → ㄓㄠˋ")
    expect(upd.nth(3)).to_have_class(re.compile(r"\bdiff-upd\b"))
    check("diff-" not in (upd.nth(4).get_attribute("class") or ""), "「知道」的啟用沒有變,不應有差異標示")
    expect(table.locator('tr[data-word="好像"]')).to_have_class(re.compile(r"\bdiff-add\b"))
    disp = table.locator('tr[data-word="這樣"] td').nth(3)                  # 只差顯示字 → 修改,第二行顯示
    expect(disp).to_contain_text("ㄐㄧㄤˋ")
    expect(disp).to_contain_text("顯示字 醬 → 降")
    expect(disp).to_have_class(re.compile(r"\bdiff-upd\b"))
    dele = table.locator('tr[data-word="然後"]')
    expect(dele).to_have_class(re.compile(r"\bdiff-del\b"))
    deco = dele.locator("td").nth(2).evaluate("e => getComputedStyle(e).textDecorationLine")
    check("line-through" in deco, f"刪除列文字應有刪除線,實際 {deco}")
    dis = table.locator('tr[data-word="我們"] td').nth(4)
    expect(dis).to_have_class(re.compile(r"\bdiff-dis\b"))
    expect(dis).to_have_text("啟用 → 停用")
    expect(dele.locator(".fusion-keep")).to_be_checked()
    expect(table.locator('tr[data-word="我們"] .fusion-keep')).to_be_checked()
    expect(table.locator('tr[data-word="好像"] .fusion-keep')).not_to_be_checked()
    expect(page.locator("#fusion-reject-reasons")).to_have_count(0)
    cells = [t.strip() for t in table.locator("tbody td").all_text_contents()]
    check(all(("→" in t) or t in ("", "新增", "修改", "停用", "刪除", "拒絕", "啟用") or not any(
        k in t for k in ("檔案外", "注音不同", "不合法", "原因")) for t in cells), f"儲存格不應有原因說明:{cells}")
    no_popup()
    page.screenshot(path=str(SHOT_DIR / "settings-fusion.png"))

    begin("合音詞庫:取消勾選「然後」的保留並確認 → 只有該詞被刪,勾保留的「我們」不變")
    dele.locator(".fusion-keep").uncheck()
    page.locator("#fusion-apply").click()
    expect(page.locator("#fusion-diff")).to_have_count(0)
    got = {w["word"]: w for w in settings.load_fusions()["words"]}
    check("然後" not in got and got["我們"]["enabled"] is True and got["知道"]["zhuyin"] == "ㄓㄠˋ"
          and got["好像"]["source"] == "custom" and got["這樣"]["display"] == "降", f"套用結果不符:{sorted(got)}")
    no_popup()

    begin("合音詞庫:含 1 筆非法注音的檔案 → 類別「拒絕」1 列、沒有保留框,拒絕原因區塊 1 行")
    cur = settings.load_fusions()["words"]
    bad = [w for w in cur] + [{"word": "然後", "zhuyin": "ㄓㄧ", "enabled": True}]
    upload(fusion_file(bad))
    rej = page.locator("#fusion-preview tr.kind-reject")
    expect(rej).to_have_count(1)
    expect(rej.locator(".fusion-keep")).to_have_count(0)
    reasons = page.locator("#fusion-reject-reasons .reject-line")
    expect(reasons).to_have_count(1)
    expect(reasons.first).to_have_text("然後:注音不是合法音節(多個音節以半形空白分隔)")
    expect(page.locator("#fusion-apply")).to_have_count(0)            # 只差被拒詞條 → 一致,不給確認
    expect(preview.locator(".fusion-same")).to_have_text("與現有詞庫一致,無需更動")
    no_popup()

    begin("合音詞庫:新增三字詞;注音輸入三個音節時顯示伺服器的錯誤")
    page.locator("#fusion-new-word").fill("沒關係")
    page.locator("#fusion-new-zhuyin").fill("ㄇㄟˊ ㄍㄨㄢ ㄒㄧ")
    quiet_toast()
    page.locator("#fusion-add").click()
    expect(toast).to_contain_text("音節數必須少於字數")
    allow_one(422)
    page.locator("#fusion-new-zhuyin").fill("ㄇㄟˊ ㄍㄨㄢ")
    page.locator("#fusion-add").click()
    expect(page.locator('#fusion-rows tr[data-word="沒關係"]')).to_have_count(1)

    begin("合音詞庫:上限設定超出範圍顯示錯誤且不變;改為 32 KB / 20 筆後超過上限的提示")
    page.locator("#fusion-max-kb").fill("4097")
    page.locator("#fusion-limits-save").click()
    expect(toast).to_contain_text("16–4096")
    allow_one(422)
    page.locator("#fusion-max-kb").fill("256")
    page.locator("#fusion-max-rows").fill("9")
    page.locator("#fusion-limits-save").click()
    expect(toast).to_contain_text("10–10000")
    allow_one(422)
    check(settings.load_import_limits() == {"max_kb": 256, "max_rows": 1000}, "超出範圍時設定值不應改變")
    page.locator("#fusion-max-kb").fill("32")
    page.locator("#fusion-max-rows").fill("20")
    page.locator("#fusion-limits-save").click()
    expect(toast).to_contain_text("32 KB")
    upload({"name": "33.json", "mimeType": "application/json", "buffer": b" " * (33 * 1024)})
    expect(toast).to_have_text("超過允許大小上限(32 KB)")
    allow_one(413)
    many = [{"word": chr(0x4E00 + i) + "好", "zhuyin": "ㄏㄠˇ", "enabled": True} for i in range(21)]
    upload(fusion_file(many))
    expect(toast).to_have_text("超過筆數上限(20 筆)")
    allow_one(413)
    settings.save_import_limits(256, 1000)

    begin("合音詞庫:非 UTF-8 檔 → 格式錯誤提示;300 KB 檔 → 超過上限提示")
    upload({"name": "壞.json", "mimeType": "application/json", "buffer": "{}".encode("utf-16")})
    expect(toast).to_have_class(re.compile(r"\bis-error\b"))
    expect(toast).to_contain_text("格式錯誤")
    allow_one(422)
    upload({"name": "大.json", "mimeType": "application/json", "buffer": b" " * (300 * 1024)})
    expect(toast).to_have_class(re.compile(r"\bis-error\b"))
    expect(toast).to_have_text("超過允許大小上限(256 KB)")
    allow_one(413)

    begin("合音詞庫:預覽後另一處修改詞庫再確認 → 匯入失敗提示")
    upload(fusion_file([w for w in settings.load_fusions()["words"] if w["word"] != "好像"]))
    expect(page.locator("#fusion-apply")).to_be_visible()
    settings.save_fusion("知道", "ㄗㄠˋ", True)
    page.locator("#fusion-apply").click()
    expect(toast).to_have_class(re.compile(r"\bis-error\b"))
    expect(toast).to_contain_text("匯入失敗")
    allow_one(409)
    check("好像" in {w["word"] for w in settings.load_fusions()["words"]}, "版本不符時詞庫不應被改")


# ---------- editor(CHG-20261022-04 T9)----------

PROJECT_NAME = "夜市的味道"
SCRIPT = "大家好，歡迎收聽。今天來聊台灣夜市。這家攤販已經開了四十年，味道真好。"
SCRIPT_EDITED = SCRIPT.replace("四十年", "五十年")
SYNTH_SECONDS = 1.6          # 替身 worker 每句的長度
OWN_WAV_SECONDS = 1.2        # 自錄音檔長度
VOICE_TEXT = "這是聲線逐字稿(UI 檢查替身)。"
EDITOR_SHOTS = ("home.png", "wizard.png", "full.png", "editor.png", "tone.png", "prosody.png", "clarity-warn.png",
                "pitch.png", "pitch-auto.png", "fusion-typing.png", "fusion.png", "fusion-rows.png", "disclaimer.png")
CANVAS_SHOTS = ("canvas.png", "dnd.png", "settings.png", "settings-fusion.png")
STUB_ZHUYIN = {"大": "ㄉㄚˋ", "家": "ㄐㄧㄚ", "好": "ㄏㄠˇ",
               # CHG-20261029-01 T9:合音規則推導需要的字
               "是": "ㄕˋ", "這": "ㄓㄜˋ", "樣": "ㄧㄤˋ", "不": "ㄅㄨˋ", "知": "ㄓ", "道": "ㄉㄠˋ", "就": "ㄐㄧㄡˋ",
               "你": "ㄋㄧˇ", "嗎": "˙ㄇㄚ", "啊": "˙ㄚ", "攤": "ㄊㄢ", "販": "ㄈㄢˋ",
               # DIR-5 修正 M-4:不在詞庫的「那裡」以規則推導
               "那": "ㄋㄚˋ", "裡": "ㄌㄧˇ"}

# 替身 worker:參數格式同 breezy_worker.py synth;每句寫一個真的 16 kHz 正弦波 WAV,
# stdout 逐句印 {"id", "seconds"};呼叫紀錄寫到同目錄的 calls.log(一行一次呼叫)。
# 同目錄有 delay.txt 時,第 2 句起每句先等該秒數(合成中途試聽情境,DIR-5 統整 M-1)。
FAKE_WORKER = """# -*- coding: utf-8 -*-
import json, math, os, struct, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
SECONDS = %r
args = sys.argv[1:]
jobs_path, out = args[args.index("--jobs") + 1], args[args.index("--out") + 1]
with open(jobs_path, encoding="utf-8") as f:
    body = json.load(f)
with open(os.path.join(HERE, "calls.log"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"jobs": body["jobs"]}, ensure_ascii=False) + "\\n")
rate = 16000
n = int(SECONDS * rate)
pcm = b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(n))
delay = 0.0
if os.path.exists(os.path.join(HERE, "delay.txt")):
    with open(os.path.join(HERE, "delay.txt"), encoding="utf-8") as f:
        delay = float(f.read().strip() or 0)
for n, job in enumerate(body["jobs"]):
    if n:
        time.sleep(delay)
    head = (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE" + b"fmt " + struct.pack("<I", 16)
            + struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm)))
    with open(os.path.join(out, job["id"] + ".wav"), "wb") as w:
        w.write(head + pcm)
    sys.stdout.write(json.dumps({"id": job["id"], "seconds": SECONDS}) + "\\n")
    sys.stdout.flush()
""" % SYNTH_SECONDS


# 替身 shape worker(CHG-20261029-01):參數格式同 breezy_worker.py shape;輸入照抄成輸出,
# 呼叫紀錄寫到同目錄的 shape.log(一行一次呼叫)。修訂 1 T11 起 stdout 同真 worker 帶 pitch_points
# (沒有語調時為 null)。修訂 1 T13:有語調時以 `breezy_worker.expand_points`(純 Python)展開;
# 替身沒有 F0,吸附以固定規則示意——落在 0.425 的插入點改到 0.405(同 rev1-after mockup),
# 其餘不動,因此畫面若誤用前端試算(不含吸附)會與 post.pitch_points 差一截。
FAKE_SHAPE = """# -*- coding: utf-8 -*-
import json, os, shutil, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, __REPO__)
from breezy_worker import expand_points
def snap(x0, x1, xd):
    return 0.405 if abs(xd - 0.425) < 1e-9 else xd
args = sys.argv[1:]
jobs_path, out = args[args.index("--jobs") + 1], args[args.index("--out") + 1]
with open(jobs_path, encoding="utf-8") as f:
    body = json.load(f)
with open(os.path.join(HERE, "shape.log"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"jobs": body["jobs"]}, ensure_ascii=False) + "\\n")
for job in body["jobs"]:
    shutil.copyfile(os.path.join(out, job["input"]), os.path.join(out, job["id"] + ".wav"))
    pts = expand_points(job["pitch"]["points"], snap)["points"] if job["pitch"] else None
    sys.stdout.write(json.dumps({"id": job["id"], "seconds": 0, "pitch_points": pts}) + "\\n")
    sys.stdout.flush()
""".replace("__REPO__", repr(str(REPO)))


def sine_wav(seconds: float, rate: int = 22050, freq: float = 330.0) -> bytes:
    """真正的 RIFF/WAVE(PCM 16-bit 單聲道);自錄音檔替身用。"""
    n = int(seconds * rate)
    pcm = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / rate))) for i in range(n))
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


def color_png(rgb: tuple, size=(640, 360)) -> bytes:
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, rgb).save(buf, format="PNG")
    return buf.getvalue()


class EditorStubs:
    """editor 組的替身與呼叫紀錄。"""

    def __init__(self, tmp: Path):
        self.fake = tmp / "fake"
        self.breezy = tmp / "breezy"
        self.fake.mkdir(parents=True, exist_ok=True)
        self.breezy.mkdir(parents=True, exist_ok=True)
        (self.fake / "worker.py").write_text(FAKE_WORKER, encoding="utf-8")
        (self.fake / "shape.py").write_text(FAKE_SHAPE, encoding="utf-8")
        self.render_calls: list[list[str]] = []
        self.annotate_calls: list[str] = []
        self.out: Path | None = None

    def worker_argv(self, jobs_path, out_dir):
        return [sys.executable, str(self.fake / "worker.py"), "synth", "--jobs", str(jobs_path), "--out", str(out_dir)]

    def shape_argv(self, jobs_path, out_dir):
        return [sys.executable, str(self.fake / "shape.py"), "shape", "--jobs", str(jobs_path), "--out", str(out_dir)]

    def shape_calls(self) -> list[list[dict]]:
        p = self.fake / "shape.log"
        if not p.exists():
            return []
        return [json.loads(x)["jobs"] for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]

    def set_delay(self, seconds: float) -> None:
        (self.fake / "delay.txt").write_text(str(seconds), encoding="utf-8")

    def worker_calls(self) -> list[list[dict]]:
        p = self.fake / "calls.log"
        if not p.exists():
            return []
        return [json.loads(x)["jobs"] for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]

    def render(self, proj):
        self.render_calls.append([s["id"] for s in proj.segments])
        n = len(self.render_calls)
        self.out.mkdir(parents=True, exist_ok=True)
        mp4, srt = self.out / f"podcast-ui-check-{n}.mp4", self.out / f"podcast-ui-check-{n}.srt"
        mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nUI 檢查\n", encoding="utf-8")
        return mp4, srt

    def annotate(self, text):
        self.annotate_calls.append(text)
        return [STUB_ZHUYIN.get(c, "ㄅㄚ") if "一" <= c <= "鿿" else None for c in text]


JS_FIND_TITLE_NODE = """() => {
  // 「文字」節點中,輸出接到「封面 prompt」的那一個(預設流程的 title)
  const nodes = Array.from(document.querySelectorAll('#drawflow .drawflow-node'));
  const idOf = n => n.id.replace(/^node-/, '');
  const label = n => { const t = n.querySelector('.node-title'); return t ? t.textContent : ''; };
  const cp = nodes.filter(n => label(n) === '封面 prompt').map(idOf);
  return nodes.filter(n => label(n) === '文字').map(idOf).filter(id =>
    cp.some(c => document.querySelector('#drawflow svg.connection.node_out_node-' + id + '.node_in_node-' + c)));
}"""


# DIR-6 修正 2:延遲回應期間切換專案(mutate、AI 封面、AI 寫稿)。在頁面內以替身延遲 / 取代
# Studio.api 與 Studio.Flows.runStep,回傳各情境的觀察結果。
JS_STALE_SWITCH = """
async ([a, b]) => {
  const S = window.Studio, Store = S.Store, out = {};
  const realApi = S.api, realRun = S.Flows.runStep;
  const made = await realApi("POST", "/api/projects", { name: b, script: "切換測試的第一句。" });
  out.created = made.ok;
  try {
    // (1) mutate:A 的 PATCH 回應延遲到切換成 B 之後才回來
    await Store.open(a);
    const sid = Store.view.project.segments[0].id;
    let release;
    const gate = new Promise(function (r) { release = r; });
    S.api = async function (method, url, body) {
      if (method === "PATCH") await gate;
      return realApi(method, url, body);
    };
    const pending = Store.patchSegment(sid, { pause_after: 2.0 });
    await Store.open(b);
    release();
    const r = await pending;
    S.api = realApi;
    out.mutate = { stale: r.stale === true, ok: r.ok, name: Store.name,
                   viewName: Store.view && Store.view.project.name };
    // (2) AI 封面:產生期間切換專案,完成時不匯入
    await Store.open(a);
    const imagesA = Store.view.project.images.length;
    S.Flows.runStep = async function () { await Store.open(b); return { value: "outputs/不存在.png" }; };
    await S.CoverPanel.generate();
    out.cover = { status: document.getElementById("cover-status").textContent, name: Store.name,
                  imagesB: Store.view.project.images.length };
    await Store.open(a);
    out.cover.imagesA = Store.view.project.images.length;
    out.cover.imagesABefore = imagesA;
    // (3) AI 寫稿:產生期間切換專案,完成時不填入
    const before = document.getElementById("script-text").value;
    S.Flows.runStep = async function () { await Store.open(b); return { value: "不該填入的文稿" }; };
    await S.ScriptPanel.generate();
    out.script = { status: document.getElementById("script-status").textContent,
                   filled: document.getElementById("script-text").value === "不該填入的文稿",
                   unchanged: document.getElementById("script-text").value === before };
  } finally {
    S.api = realApi;
    S.Flows.runStep = realRun;
  }
  return out;
}
"""


JS_PITCH_SCREEN = """(sel) => {
  // 語調 SVG 上各元素中心相對 SVG 左上角的座標(修訂 1 T13)
  const svg = document.querySelector(sel);
  const r = svg.getBoundingClientRect();
  const c = (e) => { const b = e.getBoundingClientRect(); return [b.left + b.width / 2 - r.left, b.top + b.height / 2 - r.top]; };
  return { w: r.width, h: r.height,
           auto: Array.from(svg.querySelectorAll('.tl-pitch-auto')).map(c),
           amp: Array.from(svg.querySelectorAll('.tl-pitch-amp')).map(c),
           keys: Array.from(svg.querySelectorAll('.tl-pitch-key')).map(c) };
}"""

JS_MARK = """(word) => {
  const m = Array.from(document.querySelectorAll('.fusion-mirror .mk')).filter(e => e.textContent === word);
  return m.map(e => { const r = e.getClientRects()[0] || e.getBoundingClientRect();   // 跨行時取第一行
    return {cls: e.className, x: r.left + r.width / 2, y: r.top + r.height / 2, left: r.left, top: r.top,
            right: r.right, bottom: r.bottom, start: Number(e.dataset.start)}; });
}"""

# 以獨立的鏡像元素量測 textarea 中第 i 個字的位置(與標記層的實作無關),檢查標記是否對齊
JS_CHAR_POS = """(i) => {
  const ta = document.getElementById('script-text'), cs = getComputedStyle(ta);
  const d = document.createElement('div');
  ['fontFamily','fontSize','fontWeight','lineHeight','letterSpacing','paddingTop','paddingRight','paddingBottom',
   'paddingLeft','borderTopWidth','borderRightWidth','borderBottomWidth','borderLeftWidth','borderStyle',
   'boxSizing','whiteSpace','overflowWrap','wordBreak'].forEach(k => d.style[k] = cs[k]);
  d.style.position = 'absolute'; d.style.visibility = 'hidden'; d.style.width = ta.clientWidth + ta.clientLeft * 2 + 'px';
  d.textContent = ta.value.slice(0, i);
  const s = document.createElement('span'); s.textContent = ta.value.charAt(i) || '.'; d.appendChild(s);
  document.body.appendChild(d);
  const r = ta.getBoundingClientRect(), dr = d.getBoundingClientRect(), sr = s.getBoundingClientRect();
  d.remove();
  return {x: r.left + (sr.left - dr.left), y: r.top + (sr.top - dr.top) - ta.scrollTop};
}"""


def fusion_editor_steps(page, expect, begin, shot, proj, calls, wait_until, toast, problems, seg_ids) -> None:
    import settings
    ta = page.locator("#script-text")
    pop = page.locator("#fusion-pop")

    def marks(word):
        return page.evaluate(JS_MARK, word)

    def append_text(text):
        ta.click()
        page.keyboard.press("ControlOrMeta+End")
        page.keyboard.type(text)

    def seg_with(text):
        return next((s for s in proj().segments if s["text"] == text), None)

    begin("文稿框:輸入「我也不知道。」→「不知道」出現黃色波浪底線(不含單獨的「知道」),不自動跳出浮動窗")
    append_text("我也不知道。")
    wait_until(lambda: any("mark-pending" in m["cls"] for m in marks("不知道")), "「不知道」應有黃色波浪底線")
    check(not marks("知道"), "不應另外標示單獨的「知道」")
    expect(pop).to_be_hidden()
    check(page.evaluate("document.activeElement.id") == "script-text", "標示出現後文稿框仍應保有焦點")

    begin("文稿框:點擊「不知道」開浮動窗,選「啟用」→ 綠線、片段 fusions(帶顯示字「不造」)、浮層「不造」蓋住原詞")
    m = marks("不知道")[0]
    page.mouse.click(m["x"], m["y"])
    expect(pop).to_be_visible()
    expect(pop).to_contain_text("不知道 → ㄅㄨˋ ㄗㄠˋ")
    expect(pop.locator("#fusion-pop-on")).to_have_text("啟用")
    expect(pop.locator("#fusion-pop-off")).to_have_text("停用")
    pop.locator("#fusion-pop-on").click()
    wait_until(lambda: seg_with("我也不知道。") is not None and seg_with("我也不知道。")["fusions"] == [
        {"index": 2, "length": 3, "zhuyin": "ㄅㄨˋ ㄗㄠˋ", "source": "lexicon", "display": "不造"}],
        f"啟用後片段應有 3 字合音且帶顯示字「不造」,實際 {(seg_with('我也不知道。') or {}).get('fusions')}")
    wait_until(lambda: any("mark-on" in x["cls"] for x in marks("不知道")), "啟用後應為綠色標記")
    pill = page.locator(".fusion-pill")
    expect(pill).to_have_count(1)
    expect(pill).to_have_text("不造")
    pb, mk = pill.bounding_box(), marks("不知道")[0]
    check(pb["x"] <= mk["left"] + 1 and pb["x"] + pb["width"] >= mk["right"] - 1
          and pb["y"] <= mk["top"] + 1 and pb["y"] + pb["height"] >= mk["bottom"] - 1,
          f"浮層應蓋住「不知道」三字:浮層 {pb},標記 {mk}")
    shot("fusion-typing.png")
    pill.click()
    expect(pop).to_be_visible()
    page.locator("#script-status").click()
    expect(pop).to_be_hidden()

    begin("文稿框啟用「不知道」後重新合成:替身 synth worker 收到顯示字「不造」、不含注音標註(修訂 1 R1-4)")
    n = len(calls())
    page.locator("#script-synth").click()
    wait_until(lambda: len(calls()) == n + 1 and not page.evaluate("window.Studio.Voice.running"), "應重新合成這一句")
    sent = [j["text"] for j in calls()[-1]]
    check(sent == ["我也不造。"], f"送模型的文字應為顯示字且不含 [:,實際 {sent}")
    check(seg_with("我也不知道。")["audio"] is not None, "該句應已合成")

    begin("文稿框:另一處「是這樣」選「停用」→ 紅線、fusion_off 有該位置、fusions 沒有")
    append_text("是這樣啊。")
    wait_until(lambda: any("mark-pending" in x["cls"] for x in marks("是這樣")), "「是這樣」應有黃色波浪底線")
    m = marks("是這樣")[0]
    page.mouse.click(m["x"], m["y"])
    expect(pop).to_be_visible()
    pop.locator("#fusion-pop-off").click()
    wait_until(lambda: seg_with("是這樣啊。") is not None and seg_with("是這樣啊。")["fusion_off"] == [
        {"index": 0, "length": 3}] and seg_with("是這樣啊。")["fusions"] == [], "停用應寫入 fusion_off")
    wait_until(lambda: any("mark-off" in x["cls"] for x in marks("是這樣")), "停用後應為紅色標記")

    begin("重新載入頁面後三種狀態都保留")
    page.reload()
    expect(ta).to_be_visible()
    wait_until(lambda: any("mark-on" in x["cls"] for x in marks("不知道"))
               and any("mark-off" in x["cls"] for x in marks("是這樣")), "重新載入後綠、紅標記應保留")
    expect(page.locator(".fusion-pill")).to_have_text("不造")

    begin("IME 組字期間不出現浮動窗、組字結束後值與游標正確且標記出現;貼上後標記出現")
    ta.click()
    page.keyboard.press("ControlOrMeta+End")
    page.evaluate("() => document.getElementById('script-text').dispatchEvent(new CompositionEvent('compositionstart', {data: ''}))")
    page.evaluate("() => document.getElementById('script-text').dispatchEvent(new CompositionEvent('compositionupdate', {data: 'ㄕㄜˊ'}))")
    page.keyboard.insert_text("你說什麼")
    check(not marks("什麼"), "組字期間不應重算標示")
    expect(pop).to_be_hidden()
    page.evaluate("() => document.getElementById('script-text').dispatchEvent(new CompositionEvent('compositionend', {data: '什麼'}))")
    wait_until(lambda: any("mark-pending" in x["cls"] for x in marks("什麼")), "組字結束後應出現標記")
    v = page.evaluate("() => { const t = document.getElementById('script-text'); return [t.value, t.selectionStart]; }")
    check(v[0].endswith("你說什麼") and v[1] == len(v[0]), f"組字後的值與游標不符:{v}")
    page.keyboard.insert_text("。為什麼呢。")
    wait_until(lambda: any("mark-pending" in x["cls"] for x in marks("為什麼")), "貼上的文字也應出現標記")

    begin("捲動文稿框後標記與文字位置對齊(≤ 2 px)")
    page.evaluate("() => { const t = document.getElementById('script-text'); t.style.height = '90px'; }")
    ta.press("ControlOrMeta+End")
    page.evaluate("() => { const t = document.getElementById('script-text'); t.scrollTop = t.scrollHeight; t.dispatchEvent(new Event('scroll')); }")
    page.wait_for_timeout(100)
    mk = [x for x in marks("為什麼")][0]
    pos = page.evaluate(JS_CHAR_POS, mk["start"])
    check(abs(pos["x"] - mk["left"]) <= 2 and abs(pos["y"] - mk["top"]) <= 2,
          f"捲動後標記應與文字對齊:標記 {mk},字元 {pos}")
    page.evaluate("() => { const t = document.getElementById('script-text'); t.style.height = ''; }")

    begin("在被浮層遮住的字中間刪除一字 → 狀態清除、浮層消失,存檔後該句沒有合音且需重新合成")
    val = ta.input_value()
    i = val.index("我也不知道") + 4      # 「知」之後
    page.evaluate("(i) => { const t = document.getElementById('script-text'); t.focus(); t.setSelectionRange(i, i); }", i)
    page.keyboard.press("Backspace")
    wait_until(lambda: not page.locator(".fusion-pill").count(), "刪字後浮層應消失")
    check(not any("mark-on" in x["cls"] for x in marks("不道")), "刪字後不應再有綠色標記")
    page.locator("#script-save").click()
    wait_until(lambda: seg_with("我也不道。") is not None, "文稿應已存檔")
    s4 = seg_with("我也不道。")
    check(s4["fusions"] == [] and s4["audio"] is None, f"該句應沒有合音且需重新合成:{s4}")
    check(not any(c.get("fusions") for c in proj().segments if "不道" in c["text"]), "不應殘留合音")

    begin("合音模式:準備五句並合成(停用詞庫的「這樣」,以規則推導)")
    settings.save_fusion("這樣", "ㄐㄧㄤˋ", False)
    ta.fill(SCRIPT_EDITED + "是這樣啊，你不知道嗎。就是這家攤販。就是這樣。那裡很好。他也不知道。")
    page.locator("#script-synth").click()
    wait_until(lambda: seg_with("是這樣啊，你不知道嗎。") and seg_with("是這樣啊，你不知道嗎。")["audio"]
               and not page.evaluate("window.Studio.Voice.running"), "三句應已合成", timeout=30)
    s1 = seg_with("是這樣啊，你不知道嗎。")

    def drag_chars(a, b):
        ca = page.locator(f'#props-body .tone-char[data-index="{a}"]').bounding_box()
        cb = page.locator(f'#props-body .tone-char[data-index="{b}"]').bounding_box()
        page.mouse.move(ca["x"] + ca["width"] / 2, ca["y"] + ca["height"] / 2)
        page.mouse.down()
        page.mouse.move(cb["x"] + cb["width"] / 2, cb["y"] + cb["height"] / 2, steps=4)
        page.mouse.up()

    def select_seg(sid):
        page.locator(f'#tl-voice [data-segment="{sid}"]').first.click()
        page.locator('#props-body .tone-mode[data-mode="fusion"]').click()

    fpop = page.locator("#fusion-popup")
    begin("合音模式:選「這樣」顯示規則推導 ㄓ + ㄧㄤˋ → 顎化 → ㄐㄧㄤˋ,套用後重新合成、送模型含 [:ㄐㄧㄤˋ] 不含「樣」")
    select_seg(s1["id"])
    drag_chars(1, 2)
    expect(fpop).to_be_visible()
    expect(fpop.locator(".fusion-derive")).to_contain_text("ㄓ + ㄧㄤˋ")
    expect(fpop.locator(".fusion-derive")).to_contain_text("顎化")
    expect(fpop.locator(".fusion-input")).to_have_value("ㄐㄧㄤˋ")
    # T14:彈出框維持原樣,不含「送模型」「字幕」兩列(兩列在屬性面板「已套用」下方)
    expect(fpop.locator(".fusion-sent")).to_have_count(0)
    expect(fpop.locator(".fusion-subtitle")).to_have_count(0)
    n = len(calls())
    fpop.get_by_role("button", name="套用").click()
    wait_until(lambda: len(calls()) == n + 1 and not page.evaluate("window.Studio.Voice.running"), "套用後應重新合成這一句")
    check(calls()[-1][0]["text"] == "是這[:ㄐㄧㄤˋ]啊，你不知道嗎。", f"送模型的文字不符:{calls()[-1][0]['text']}")
    check(seg_with("是這樣啊，你不知道嗎。")["fusions"] == [{"index": 1, "length": 2, "zhuyin": "ㄐㄧㄤˋ", "source": "rule",
                                                          "display": ""}],
          f"fusions 不符:{seg_with('是這樣啊，你不知道嗎。')['fusions']}")
    sent = calls()[-1][0]["text"]
    check("是這[:ㄐㄧㄤˋ]啊" in sent and "樣" not in sent, f"送模型的文字不符:{sent}")
    cue = page.locator(f'#tl-cues .tl-cue[data-segment="{s1["id"]}"]')
    expect(cue.first).to_contain_text("是這樣啊")

    begin("合音模式:選不在詞庫的「那裡」以規則推導 ㄋㄧˇ,套用後 source 為 rule、送模型含 那[:ㄋㄧˇ] 不含「裡」")
    s5 = seg_with("那裡很好。")
    select_seg(s5["id"])
    drag_chars(0, 1)
    expect(fpop).to_be_visible()
    expect(fpop.locator(".fusion-input")).to_have_value("ㄋㄧˇ")
    n = len(calls())
    fpop.get_by_role("button", name="套用").click()
    wait_until(lambda: len(calls()) == n + 1 and not page.evaluate("window.Studio.Voice.running"), "套用後應重新合成這一句")
    check(seg_with("那裡很好。")["fusions"] == [{"index": 0, "length": 2, "zhuyin": "ㄋㄧˇ", "source": "rule",
                                                "display": ""}],
          f"fusions 不符:{seg_with('那裡很好。')['fusions']}")
    sent = calls()[-1][0]["text"]
    check("那[:ㄋㄧˇ]" in sent and "裡" not in sent, f"送模型的文字不符:{sent}")
    begin("T14 屬性面板合音模式:規則推導的「那裡」套用後「已套用」下方送模型列為 那[:ㄋㄧˇ]很好。(與 worker 相同)、字幕列為原文")
    select_seg(s5["id"])
    psent, psub = page.locator("#props-body .fusion-sent"), page.locator("#props-body .fusion-subtitle")
    expect(psent).to_have_text(sent)
    check(sent == "那[:ㄋㄧˇ]很好。", f"送模型的文字不符:{sent}")
    expect(psub).to_have_text("那裡很好。(原文不變)")
    check(psent.evaluate("e => e.children.length") == 0, "送模型列應只含文字(textContent)")
    check(page.evaluate("""() => { const l = document.querySelector('#props-body .fusion-list');
        const a = l && l.nextElementSibling; const b = a && a.nextElementSibling;
        return !!(a && b && a.querySelector('.fusion-sent') && b.querySelector('.fusion-subtitle')); }"""),
          "兩列應緊接在「已套用」列表下方,依序為送模型、字幕")
    expect(page.locator(f'#tl-cues .tl-cue[data-segment="{s5["id"]}"]').first).to_contain_text("那裡很好")

    begin("合音模式:詞庫有停用的「攤販」時按「存入合音詞庫」先確認覆寫;取消不變、接受才寫入")
    settings.save_fusion("攤販", "ㄈㄢ", False)
    s2 = seg_with("就是這家攤販。")
    select_seg(s2["id"])
    drag_chars(4, 5)
    expect(fpop).to_be_visible()
    finput = fpop.locator(".fusion-input")
    expect(finput).not_to_have_value("")             # 等規則推導預填完成
    z = finput.input_value()
    check(z and z != "ㄈㄢ", f"預填的合音注音應與詞庫中停用的值不同:{z}")
    asked = []    # 對話框事件要靠 Playwright 呼叫才會分派,等待時以 page.wait_for_timeout 推進
    page.once("dialog", lambda d: (asked.append(d.message), d.dismiss()))
    fpop.get_by_role("button", name="存入合音詞庫").click()
    wait_until(lambda: page.wait_for_timeout(50) or len(asked) == 1, "停用中的既有詞也應先跳覆寫確認")
    check("攤販" in asked[0] and "覆寫" in asked[0], f"確認訊息不符:{asked}")
    page.wait_for_timeout(300)

    def lex_tanfan():
        return {w["word"]: w for w in settings.load_fusions()["words"]}["攤販"]

    check(lex_tanfan()["zhuyin"] == "ㄈㄢ" and lex_tanfan()["enabled"] is False,
          f"取消確認後詞庫不應改變:{lex_tanfan()}")
    page.once("dialog", lambda d: (asked.append(d.message), d.accept()))
    fpop.get_by_role("button", name="存入合音詞庫").click()
    wait_until(lambda: page.wait_for_timeout(50) or len(asked) == 2, "再按一次應再跳覆寫確認")
    wait_until(lambda: page.wait_for_timeout(50) or lex_tanfan()["zhuyin"] == z, "接受確認後應寫入新注音")
    check(lex_tanfan()["enabled"] is True, "接受確認後應啟用")
    page.keyboard.press("Escape")
    settings.delete_fusion("攤販")

    begin("合音模式:選三字「不知道」預填詞庫值 ㄅㄨˋ ㄗㄠˋ,套用後帶顯示字「不造」、送模型含「不造」不含 不[:")
    select_seg(s1["id"])
    drag_chars(6, 8)
    expect(fpop.locator(".fusion-input")).to_have_value("ㄅㄨˋ ㄗㄠˋ")
    expect(fpop.locator(".fusion-derive")).to_contain_text("詞庫")
    pb = fpop.bounding_box()
    vw = page.evaluate("document.documentElement.clientWidth")
    check(pb["x"] >= 0 and pb["x"] + pb["width"] <= vw, f"T14:彈出框應在視窗內(不超出右緣):{pb},視窗寬 {vw}")
    n = len(calls())
    fpop.get_by_role("button", name="套用").click()
    wait_until(lambda: len(calls()) == n + 1 and not page.evaluate("window.Studio.Voice.running"), "套用後應重新合成")
    sent = calls()[-1][0]["text"]
    check(sent == "是這[:ㄐㄧㄤˋ]啊，你不造嗎。", f"送模型的文字不符:{sent}")
    check(seg_with("是這樣啊，你不知道嗎。")["fusions"] == [
        {"index": 1, "length": 2, "zhuyin": "ㄐㄧㄤˋ", "source": "rule", "display": ""},
        {"index": 6, "length": 3, "zhuyin": "ㄅㄨˋ ㄗㄠˋ", "source": "lexicon", "display": "不造"}],
        f"fusions 不符:{seg_with('是這樣啊，你不知道嗎。')['fusions']}")
    expect(page.locator(f'#tl-voice [data-segment="{s1["id"]}"] .tl-badge')).to_have_text("⟲ 合音 2")
    select_seg(s1["id"])
    shot("fusion.png")
    begin("T14 屬性面板合音模式:詞庫「不知道」套用後送模型列為顯示字「不造」(含已套用的「這樣」,與 worker 相同)、字幕列為原文")
    expect(psent).to_have_text(sent)
    expect(psub).to_have_text("是這樣啊，你不知道嗎。(原文不變)")
    page.locator("#props-body").screenshot(path=str(SHOT_DIR / "fusion-rows.png"))

    begin("合音模式:「就是」顯示不合音原因、套用停用;選五字時套用停用")
    s2 = seg_with("就是這家攤販。")
    select_seg(s2["id"])
    drag_chars(0, 1)
    expect(fpop.locator(".fusion-derive")).to_contain_text("不合音")
    expect(fpop.get_by_role("button", name="套用")).to_be_disabled()
    page.keyboard.press("Escape")
    select_seg(s2["id"])
    drag_chars(0, 4)
    expect(fpop.locator(".fusion-derive")).to_contain_text("合音最多 4 個漢字")
    expect(fpop.get_by_role("button", name="套用")).to_be_disabled()
    page.keyboard.press("Escape")

    begin("合音模式:選四字「就是這樣」列出三組相鄰兩字選項且預設選「這樣」")
    settings.save_fusion("這樣", "ㄐㄧㄤˋ", True)
    settings.save_fusion("是這樣", "ㄕˋ ㄐㄧㄤˋ", False)
    s3 = seg_with("就是這樣。")
    select_seg(s3["id"])
    drag_chars(0, 3)
    opts = fpop.locator(".fusion-option")
    expect(opts).to_have_count(3)
    expect(fpop.locator(".fusion-option:has(input:checked)")).to_contain_text("這樣:詞庫 ㄐㄧㄤˋ")
    expect(fpop.locator(".fusion-input")).to_have_value("ㄐㄧㄡˋ ㄕˋ ㄐㄧㄤˋ")
    page.keyboard.press("Escape")
    settings.save_fusion("是這樣", "ㄕˋ ㄐㄧㄤˋ", True)

    begin("合音模式:選預設詞「這樣」(詞庫啟用)套用後 source 為 lexicon、帶顯示字「醬」、送模型為「就是醬。」、字幕仍為原文")
    select_seg(s3["id"])
    drag_chars(2, 3)
    expect(fpop).to_be_visible()
    expect(fpop.locator(".fusion-input")).to_have_value("ㄐㄧㄤˋ")
    expect(fpop.locator(".fusion-derive")).to_contain_text("詞庫")
    n = len(calls())
    fpop.get_by_role("button", name="套用").click()
    wait_until(lambda: len(calls()) == n + 1 and not page.evaluate("window.Studio.Voice.running"), "套用後應重新合成這一句")
    check(seg_with("就是這樣。")["fusions"] == [{"index": 2, "length": 2, "zhuyin": "ㄐㄧㄤˋ", "source": "lexicon",
                                              "display": "醬"}],
          f"fusions 不符:{seg_with('就是這樣。')['fusions']}")
    sent = calls()[-1][0]["text"]
    check(sent == "就是醬。", f"送模型的文字不符:{sent}")
    begin("T14 屬性面板合音模式:詞庫「這樣」套用後送模型列為「就是醬。」(與 worker 相同);按 ✕ 移除後兩列即時更新")
    select_seg(s3["id"])
    expect(psent).to_have_text(sent)
    expect(psub).to_have_text("就是這樣。(原文不變)")
    n = len(calls())
    page.locator("#props-body .fusion-item button").first.click()
    expect(psent).to_have_text("就是這樣。")
    expect(psub).to_have_text("就是這樣。(原文不變)")
    wait_until(lambda: seg_with("就是這樣。")["fusions"] == [] and seg_with("就是這樣。")["audio"]
               and not page.evaluate("window.Studio.Voice.running"), "移除後應回到原文的音檔")
    # 原文「就是這樣。」先前已合成過,移除後命中合成快取、不一定再呼叫 worker;有呼叫時文字須與送模型列相同
    check(len(calls()) == n or calls()[-1][0]["text"] == "就是這樣。", f"移除後送模型的文字不符:{calls()[n:]}")
    expect(psent).to_have_text("就是這樣。")
    expect(page.locator(f'#tl-cues .tl-cue[data-segment="{s3["id"]}"]').first).to_contain_text("就是這樣")

    begin("屬性面板句子文字:「不知道」顯示黃色波浪底線,點擊開同一個浮動窗;停用變紅、再點選啟用變綠")
    s6 = seg_with("他也不知道。")
    page.locator(f'#tl-voice [data-segment="{s6["id"]}"]').first.click()
    ptext = page.locator("#props-body .prop-text")
    expect(ptext).to_have_text("他也不知道。")
    pmk = ptext.locator(".mk")
    expect(pmk).to_have_count(1)
    expect(pmk).to_have_text("不知道")
    expect(pmk).to_have_class(re.compile(r"\bmark-pending\b"))
    deco = pmk.evaluate("e => getComputedStyle(e).textDecorationStyle")
    check(deco == "wavy", f"屬性面板的標記應為波浪底線,實際 {deco}")
    expect(pop).to_be_hidden()
    pmk.click()
    expect(pop).to_be_visible()
    expect(pop).to_contain_text("不知道 → ㄅㄨˋ ㄗㄠˋ")
    pop.locator("#fusion-pop-off").click()
    wait_until(lambda: seg_with("他也不知道。")["fusion_off"] == [{"index": 2, "length": 3}]
               and seg_with("他也不知道。")["fusions"] == [], "屬性面板選停用應寫入 fusion_off")
    expect(page.locator("#props-body .prop-text .mk")).to_have_class(re.compile(r"\bmark-off\b"))
    page.locator("#props-body .prop-text .mk").click()
    expect(pop).to_be_visible()
    pop.locator("#fusion-pop-on").click()
    wait_until(lambda: seg_with("他也不知道。")["fusions"] == [
        {"index": 2, "length": 3, "zhuyin": "ㄅㄨˋ ㄗㄠˋ", "source": "lexicon", "display": "不造"}]
        and seg_with("他也不知道。")["fusion_off"] == [], "屬性面板選啟用應寫入 fusions")
    expect(page.locator("#props-body .prop-text .mk")).to_have_class(re.compile(r"\bmark-on\b"))
    expect(page.locator(f'#tl-cues .tl-cue[data-segment="{s6["id"]}"]').first).to_contain_text("他也不知道")


def run_editor_checks(page, base: str, root: Path, stubs: EditorStubs, problems: list) -> None:
    from playwright.sync_api import expect

    import project as pj

    step = {"name": ""}

    def begin(name):
        step["name"] = name
        print(f"・{name}", flush=True)

    def shot(name):
        page.screenshot(path=str(SHOT_DIR / name))

    def proj():
        return pj.load(PROJECT_NAME)

    def calls():
        return stubs.worker_calls()

    def wait_until(fn, message, timeout=TIMEOUT_MS / 1000):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if fn():
                return
            time.sleep(0.05)
        check(fn(), message)

    def drag_x(locator, dx):
        locator.scroll_into_view_if_needed()
        b = locator.bounding_box()
        check(b, "找不到要拖拉的元素")
        x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + dx / 2, y, steps=5)
        page.mouse.move(x + dx, y, steps=5)
        page.mouse.up()

    def drop(selector, off_x, off_y, files):
        page.locator(selector).scroll_into_view_if_needed()
        return page.evaluate(JS_DROP_FILES, [selector, off_x, off_y, files])

    def step_labels():
        return [t.strip() for t in page.locator("#wizard-steps li").all_text_contents()]

    def current_step(text):
        expect(page.locator("#wizard-steps li.is-current")).to_have_text(text)
        expect(page.locator("#wizard-body .wizard-title")).to_have_text(text)

    def no_prev_next(scope):
        btns = page.locator(f"{scope} button:visible, {scope} a:visible")
        texts = [t.strip() for t in btns.all_text_contents()]
        check(not any(t in ("上一步", "下一步") for t in texts), f"{scope} 不應有「上一步」「下一步」:{texts}")

    toast = page.locator("#toast")
    home = page.locator("#page-home")
    wiz = page.locator("#page-wizard")
    full = page.locator("#page-full")

    try:
        # 1. 首頁
        begin("開啟首頁:只有兩張卡片、右上角 ⚙、底部免責連結,沒有分頁列")
        page.goto(base + "/")
        expect(home).to_be_visible()
        cards = home.locator(".home-cards .card")
        expect(cards).to_have_count(2)
        expect(cards.locator(".card-title")).to_have_text(["精靈", "完整模式"])
        expect(home.locator('.page-header [aria-label="設定"]')).to_have_count(1)
        expect(home.locator('.page-header [aria-label="設定"]')).to_be_visible()
        check(home.locator('[aria-label="設定"]').count() == 1, "首頁應恰好一個設定圖示")
        footer = home.locator(".page-footer a")
        expect(footer).to_have_count(1)
        expect(footer).to_have_text("免責說明與授權")
        expect(footer).to_have_attribute("href", "#/disclaimer")
        check(page.locator('[role="tab"], [role="tablist"]').count() == 0, "首頁不應有分頁列(role=tab / tablist)")
        home_text = home.inner_text()
        check("本機" not in home_text and "→" not in home_text, "首頁不應有「本機」或以「→」串起的步驟說明")
        shot("home.png")

        begin("點右上角 ⚙ 開設定頁,再按「← 首頁」回首頁")
        page.locator("#open-settings").click()
        expect(page.locator("#page-settings")).to_be_visible()
        expect(home).to_be_hidden()
        page.locator("#page-settings .back-link").click()
        expect(home).to_be_visible()
        expect(page.locator("#page-settings")).to_be_hidden()
        expect(cards).to_have_count(2)

        # 2. 精靈
        begin("點「精靈」卡片並建立專案「夜市的味道」")
        page.locator("#card-wizard").click()
        expect(wiz).to_be_visible()
        expect(page.locator("#wizard-body #project-picker")).to_be_visible()
        page.locator("#picker-name").fill(PROJECT_NAME)
        page.locator("#picker-create").click()
        expect(page.locator("#wizard-nav")).to_be_visible()
        check(step_labels() == ["① 文稿", "② 語音", "③ 封面", "④ 剪輯", "⑤ 匯出"],
              f"精靈步驟應為預設流程的五步,實際 {step_labels()}")
        current_step("① 文稿")
        nav = page.locator("#page-wizard button:visible").filter(has_text=re.compile(r"^(上一步|下一步)$"))
        expect(nav).to_have_count(2)
        expect(page.locator("#wizard-prev")).to_be_disabled()
        check((root / "projects" / PROJECT_NAME / "project.json").is_file(), "專案應建立在暫存目錄的 projects/")

        begin("① 文稿:填入三句文稿後按「下一步」")
        page.locator("#script-text").fill(SCRIPT)
        page.locator("#wizard-next").click()
        current_step("② 語音")
        expect(page.locator("#voice-list li.voice-row")).to_have_count(3)
        check(proj().script == SCRIPT, "按「下一步」時應把文稿存檔")

        begin("② 語音 → 按「上一步」回 ① 文稿,內容保留,再按「下一步」")
        page.locator("#wizard-prev").click()
        current_step("① 文稿")
        expect(page.locator("#script-text")).to_have_value(SCRIPT)
        page.locator("#wizard-next").click()
        current_step("② 語音")

        begin("② 語音:逐句合成三句(替身 worker 每句間隔 1.2 秒),第一句在整批結束前就能試聽")
        stubs.set_delay(1.2)
        h0 = len(pj.list_history(PROJECT_NAME))
        page.locator("#voice-synth").click()
        play1 = page.locator('#voice-list li.voice-row button[aria-label="試聽第 1 句"]')
        expect(play1).to_be_enabled()
        mid = page.evaluate("""() => {
          const s = window.Studio.Store.view.project.segments;
          const tl = document.querySelector('#tl-voice .tl-seg[data-segment="' + s[0].id + '"]');
          return {status: document.getElementById('voice-status').textContent, running: window.Studio.Voice.running,
                  first: !!s[0].audio, last: !!s[2].audio, tl: tl ? tl.className : null};
        }""")
        check(mid["running"] and "合成中" in mid["status"] and "新合成" not in mid["status"],
              f"第一句可試聽時整批合成應尚未結束,實際 {mid}")
        check(mid["first"] and not mid["last"], f"合成中途專案檢視應只有已完成的句子有音檔,實際 {mid}")
        check(mid["tl"] is not None and "is-pending" not in mid["tl"],
              f"語音軌第一句在合成中途應為已合成的片段(不是 ⏳ 合成中),實際 class={mid['tl']}")
        check(len(pj.list_history(PROJECT_NAME)) == h0, "合成中途不應存檔(history 不應增加)")
        expect(page.locator("#voice-status")).to_contain_text("新合成 3 句、沿用 0 句")
        stubs.set_delay(0)
        check(len(pj.list_history(PROJECT_NAME)) == h0 + 1, "一次合成應只多一個 history 版本")
        check([len(c) for c in calls()] == [3], f"worker 應被呼叫一次、三句,實際 {[len(c) for c in calls()]}")
        rows = page.locator("#voice-list li.voice-row")
        for i in range(3):
            expect(rows.nth(i).locator(".voice-state")).to_contain_text("✓ 已合成")
        segs = proj().segments
        check(len(segs) == 3 and all(s.get("audio") and "key" in s["audio"] for s in segs), "三句都應有合成音檔")
        seg_ids = [s["id"] for s in segs]

        begin("② 語音:把自錄的 WAV 拖到第 2 句上,取代該句且不重新合成")
        drop(f'#voice-list li.voice-row[data-segment="{seg_ids[1]}"]', 40, 10,
             [file_arg("自錄開場.wav", sine_wav(OWN_WAV_SECONDS), "audio/wav")])
        expect(toast).to_have_text("已用「自錄開場.wav」取代這一句")
        expect(rows.nth(1).locator(".voice-state")).to_contain_text("✓ 自錄")
        a2 = proj().segments[1]["audio"]
        check(a2 and "key" not in a2 and abs(a2["seconds"] - OWN_WAV_SECONDS) < 0.01,
              f"第 2 句應改用自錄音檔({OWN_WAV_SECONDS} 秒),實際 {a2}")
        check(len(calls()) == 1, "取代音檔不應呼叫合成 worker")

        begin("② 語音:把第 3 句語速改成 1.30×,只改變速、不觸發合成")
        sel = rows.nth(2).locator("select.speed-select")
        expect(sel).to_have_attribute("aria-label", "第 3 句語速")
        sel.select_option("1.30")
        expect(toast).to_contain_text("語速改為 1.30×")
        expect(rows.nth(2).locator("select.speed-select")).to_have_value("1.30")
        check(proj().segments[2]["speed"] == 1.3, "第 3 句語速應存成 1.3")
        time.sleep(0.5)
        check(len(calls()) == 1, "改語速不應呼叫合成 worker")
        check(proj().segments[2]["audio"] == segs[2]["audio"], "改語速不應改變第 3 句的合成音檔")
        shot("wizard.png")

        begin("③ 封面:把圖片拖進封面面板,加入畫面軌")
        page.locator("#wizard-next").click()
        current_step("③ 封面")
        drop("#panel-cover", 60, 40, [file_arg("封面.png", color_png((200, 60, 40)), "image/png")])
        expect(page.locator("#cover-list .cover-item.is-used")).to_have_count(1)
        check(len(proj().images) == 1, "畫面軌應有 1 張圖")

        begin("④ 剪輯:素材庫、預覽、屬性、時間軸三軌")
        page.locator("#wizard-next").click()
        current_step("④ 剪輯")
        for k in ("library", "preview", "props", "timeline"):
            expect(page.locator(f"#wizard-body #panel-{k}")).to_be_visible()
        expect(page.locator("#tl-voice .tl-seg")).to_have_count(3)
        expect(page.locator("#tl-voice .tl-gap")).to_have_count(2)
        expect(page.locator("#tl-images .tl-img")).to_have_count(1)
        zoom = page.evaluate("window.Studio.Timeline.zoom")

        begin("④ 剪輯:把第 2、3 句之間的空隙拖到 1.5 秒(不重新合成)")
        lay0 = page.evaluate("window.Studio.Store.view.layout.segments")
        gap = page.locator(f'#tl-voice .tl-gap[data-after="{seg_ids[1]}"]')
        expect(gap).to_have_attribute("aria-valuenow", "0.4")
        drag_x(gap, 1.1 * zoom)
        expect(toast).to_have_text("停頓改為 1.5 秒")
        check(proj().segments[1]["pause_after"] == 1.5, "第 2 句之後的停頓應存成 1.5 秒")
        lay1 = page.evaluate("window.Studio.Store.view.layout.segments")
        check(abs((lay1[2]["start"] - lay1[1]["end"]) - 1.5) < 0.01,
              f"時間軸上第 2、3 句間隔應為 1.5 秒,實際 {lay1[2]['start'] - lay1[1]['end']:.3f}")
        check(abs((lay1[2]["start"] - lay0[2]["start"]) - 1.1) < 0.01, "第 3 句應往後移 1.1 秒")
        check(len(calls()) == 1, "改停頓不應呼叫合成 worker")

        begin("④ 剪輯:屬性面板把第 2 句之後的停頓設為 0,空隙仍可點到並拖到 1.5 秒(DIR-5 統整 M-2)")

        def set_pause_in_props(sid, value):
            page.locator(f'#tl-voice .tl-seg[data-segment="{sid}"]').click()
            inp = page.locator('#props-body [data-hook="pause"] input.pause-input')
            inp.fill(value)
            inp.dispatch_event("change")
            wait_until(lambda: proj().segments[1]["pause_after"] == float(value),
                       f"第 2 句之後的停頓應存成 {value} 秒")

        set_pause_in_props(seg_ids[1], "0")
        gap = page.locator(f'#tl-voice .tl-gap[data-after="{seg_ids[1]}"]')
        expect(gap).to_have_attribute("aria-valuenow", "0.0")
        page.locator(f'#tl-voice .tl-seg[data-segment="{seg_ids[2]}"]').click()   # 先選取第 3 句
        expect(page.locator(f'#tl-voice .tl-seg.is-selected[data-segment="{seg_ids[2]}"]')).to_have_count(1)
        gap.scroll_into_view_if_needed()
        hit = page.evaluate("""(after) => {
          const g = document.querySelector('#tl-voice .tl-gap[data-after="' + after + '"]');
          const r = g.getBoundingClientRect();
          const c = document.getElementById('tl-content').getBoundingClientRect();
          const segs = window.Studio.Store.view.layout.segments;
          const prev = segs.find(s => s.id === after);
          const x = c.left + prev.end * window.Studio.Timeline.zoom, y = r.top + r.height / 2;
          const e = document.elementFromPoint(x, y);
          return {width: r.width, x: x, y: y, isGap: e === g, got: e ? e.className : null};
        }""", seg_ids[1])
        check(hit["width"] >= 12, f"停頓 0 秒的空隙點擊範圍應至少 12px,實際 {hit['width']:.1f}")
        check(hit["isGap"], f"選取第 3 句後,交界處最上層應是空隙,實際 {hit['got']}")
        n_calls = len(calls())
        page.mouse.move(hit["x"], hit["y"])
        page.mouse.down()
        page.mouse.move(hit["x"] + 0.75 * zoom, hit["y"], steps=5)
        page.mouse.move(hit["x"] + 1.5 * zoom, hit["y"], steps=5)
        page.mouse.up()
        expect(toast).to_have_text("停頓改為 1.5 秒")
        check(proj().segments[1]["pause_after"] == 1.5, "從 0 秒拖拉後停頓應存成 1.5 秒")
        check(len(calls()) == n_calls, "改停頓不應呼叫合成 worker")

        begin("④ 剪輯:停頓 0 秒的空隙以鍵盤 → 改成 0.1 秒")
        set_pause_in_props(seg_ids[1], "0")
        gap = page.locator(f'#tl-voice .tl-gap[data-after="{seg_ids[1]}"]')
        expect(gap).to_have_attribute("aria-valuenow", "0.0")
        gap.focus()
        page.keyboard.press("ArrowRight")
        wait_until(lambda: proj().segments[1]["pause_after"] == 0.1, "鍵盤 → 後停頓應存成 0.1 秒")
        set_pause_in_props(seg_ids[1], "1.5")                                     # 還原,後續步驟照舊
        check(len(calls()) == n_calls, "改停頓不應呼叫合成 worker")

        begin("④ 剪輯:點第 1 句的「好」,在注音彈出框改成四聲並套用,只重新合成這一句")
        page.locator(f'#tl-voice .tl-seg[data-segment="{seg_ids[0]}"]').click()
        expect(page.locator("#props-body .prop-title")).to_have_text("選取:片段 #1")
        hao = page.locator('#props-body .tone-char[data-index="2"]')
        expect(hao.locator(".tone-glyph")).to_have_text("好")
        hao.click()
        popup = page.locator("#tone-popup")
        expect(popup).to_be_visible()
        expect(popup.locator(".tone-title")).to_have_text("「好」的念法")
        expect(popup.locator(".tone-auto")).to_have_text("自動:ㄏㄠˇ")
        popup.locator('input[type="radio"][value="ㄏㄠˋ"]').check()
        expect(popup.locator(".tone-input")).to_have_value("ㄏㄠˋ")
        shot("tone.png")
        popup.get_by_role("button", name="套用").click()
        expect(popup).to_be_hidden()
        expect(page.locator("#voice-status")).to_contain_text("新合成 1 句、沿用 0 句")
        check([len(c) for c in calls()] == [3, 1], f"改聲調後應只合成 1 句,worker 呼叫 {[len(c) for c in calls()]}")
        check("好[:ㄏㄠˋ]" in calls()[1][0]["text"], f"重新合成的應是第 1 句(含標註),實際 {calls()[1][0]['text']}")
        check("好[:ㄏㄠˋ]" in pj.tts_text(proj().segments[0]), "第 1 句的合成文字應包含「好[:ㄏㄠˋ]」")
        check(stubs.annotate_calls, "應以 /api/annotate 取得自動注音")

        begin("④ 剪輯:把第二張圖拖到畫面軌 3.0 秒處,再把它的起點拖到 4.0 秒")
        off = page.evaluate("""(t) => {
          const c = document.getElementById('tl-content').getBoundingClientRect();
          const l = document.getElementById('tl-images').getBoundingClientRect();
          return [c.left - l.left + t * window.Studio.Timeline.zoom, l.height / 2];
        }""", 3.0)
        drop("#tl-images", off[0], off[1], [file_arg("夜市照片.png", color_png((30, 90, 200)), "image/png")])
        expect(page.locator("#tl-images .tl-img")).to_have_count(2)
        imgs = proj().images
        check(len(imgs) == 2 and abs(imgs[1]["start"] - 3.0) <= 0.1, f"第二張圖應從 3.0 秒開始,實際 {imgs}")
        img2 = imgs[1]["id"]
        drag_x(page.locator(f'#tl-images .tl-img[data-image="{img2}"] .tl-edge-start'), 1.0 * zoom)
        expect(toast).to_have_text("起點改為 4.0 秒")
        check(proj().images[1]["start"] == 4.0, f"第二張圖起點應為 4.0 秒,實際 {proj().images[1]['start']}")

        begin("④ 剪輯:預覽播放 3 秒(音訊實際播放、字幕與畫面切換)")
        page.locator("#preview-start").click()
        page.locator("#preview-play").click()
        expect(page.locator("#preview-play")).to_have_attribute("aria-label", "暫停")
        t0 = time.monotonic()
        wait_until(lambda: page.evaluate("!window.Studio.Preview.audio.paused"), "預覽開始後音訊應在播放")
        a0 = page.evaluate("window.Studio.Preview.audio.currentTime")
        time.sleep(0.6)
        a1 = page.evaluate("window.Studio.Preview.audio.currentTime")
        check(a1 > a0 + 0.2, f"預覽的音訊播放位置應前進,實際 {a0:.2f} → {a1:.2f} 秒")
        expect(page.locator("#preview-cue")).not_to_have_text("")
        expect(page.locator("#preview-img")).to_be_visible()
        time.sleep(max(0.0, 3.0 - (time.monotonic() - t0)))
        pt = page.evaluate("window.Studio.Preview.t")
        check(pt >= 2.5, f"播放 3 秒後預覽時間應 ≥ 2.5 秒,實際 {pt:.2f}")
        check(page.evaluate("window.Studio.Preview.playing"), "播放 3 秒時預覽仍應在播放(總長超過 3 秒)")
        shot("editor.png")
        page.locator("#preview-play").click()
        expect(page.locator("#preview-play")).to_have_attribute("aria-label", "播放")

        begin("⑤ 匯出(替身 render)")
        page.locator("#wizard-next").click()
        current_step("⑤ 匯出")
        expect(page.locator("#wizard-next")).to_be_disabled()
        page.locator("#export-run").click()
        expect(page.locator("#export-status")).to_have_text("匯出完成")
        expect(page.locator("#export-links li")).to_have_count(2)
        check(len(stubs.render_calls) == 1 and stubs.render_calls[0] == seg_ids, "匯出應呼叫一次 render、含三句")

        # 3. 完整模式
        begin("回首頁點「完整模式」卡片,開啟同一個專案")
        page.locator("#page-wizard .back-link").click()
        expect(home).to_be_visible()
        expect(page.locator("#home-recent")).to_be_visible()
        expect(page.locator("#home-recent-name")).to_contain_text(PROJECT_NAME)
        page.locator("#card-full").click()
        expect(full).to_be_visible()
        page.locator("#full-picker-slot .picker-link", has_text=PROJECT_NAME).click()
        expect(page.locator("#full-body")).to_be_visible()
        for k in ("script", "cover", "library", "preview", "props", "timeline"):
            expect(page.locator(f"#full-body #panel-{k}")).to_be_visible()
        expect(page.locator("#script-text")).to_have_value(SCRIPT)
        expect(page.locator("#cover-list .cover-item")).to_have_count(2)
        expect(page.locator("#tl-voice .tl-seg")).to_have_count(3)
        expect(page.locator("#tl-images .tl-img")).to_have_count(2)
        expect(wiz).to_be_hidden()
        no_prev_next("#page-full")

        begin("完整模式:在文稿面板改第 3 句後按「逐句合成全部」,只有該句重新合成")
        page.locator("#script-text").fill(SCRIPT_EDITED)
        expect(page.locator("#script-status")).to_have_text("文稿已修改,尚未存檔")
        page.locator("#script-synth").click()
        expect(page.locator("#script-status")).to_contain_text("新合成 1 句、沿用 0 句")
        c = calls()
        check([len(x) for x in c] == [3, 1, 1], f"改一句後應只合成 1 句,worker 呼叫 {[len(x) for x in c]}")
        check("五十年" in c[2][0]["text"], "重新合成的應是改過的第 3 句")
        segs2 = proj().segments
        check(proj().script == SCRIPT_EDITED and len(segs2) == 3, "文稿應已存檔且仍為三句")
        check(segs2[0]["id"] == seg_ids[0] and segs2[1]["id"] == seg_ids[1], "未改的第 1、2 句應沿用原片段")
        check(segs2[1]["audio"] == a2, "第 2 句應保留自錄音檔")
        expect(page.locator("#tl-voice .tl-seg:not(.is-pending)")).to_have_count(3)
        no_prev_next("#page-full")
        shot("full.png")

        # CHG-20261029-01 T5:韻律拉桿與字詞清晰度
        begin("完整模式:第 1 句連音拉桿拖動時即時顯示百分比,放開後只重做後製(不重新合成)")
        n_calls = len(calls())
        page.locator(f'#tl-voice .tl-seg[data-segment="{seg_ids[0]}"]').click()
        expect(page.locator("#props-body .prop-title")).to_have_text("選取:片段 #1")
        conn = page.locator('#props-body [data-hook="prosody"] input[aria-label="連音(百分比)"]')
        out = conn.locator("xpath=following-sibling::output[1]")
        expect(conn).to_have_attribute("min", "0")
        expect(conn).to_have_attribute("max", "100")
        expect(conn).to_have_attribute("step", "1")
        expect(out).to_have_text("0%")
        conn.evaluate("(e) => { e.value = '35'; e.dispatchEvent(new Event('input', {bubbles: true})); }")
        expect(out).to_have_text("35%")
        check(proj().segments[0]["connect"] is None, "拖動中(還沒放開)不應送出")
        conn.evaluate("(e) => e.dispatchEvent(new Event('change', {bubbles: true}))")
        wait_until(lambda: proj().segments[0]["connect"] == 35, "放開後第 1 句連音應存成 35")
        wait_until(lambda: pj.is_post_fresh(proj().segments[0]), "連音改了之後應完成後製")
        check(len(calls()) == n_calls, "改連音不應呼叫合成 worker")
        check(stubs.shape_calls() and stubs.shape_calls()[-1][0]["connect"] == 35, "應以 shape 重做第 1 句的後製")
        expect(page.locator('#props-body [data-hook="prosody"] input[aria-label="連音(百分比)"]')).to_have_value("35")

        begin("完整模式:按連音的「預設」改回 null")
        page.locator('#props-body [data-hook="prosody"] .pct-row').first.locator(".pct-reset").click()
        wait_until(lambda: proj().segments[0]["connect"] is None, "按「預設」後連音應為 null")
        check(len(calls()) == n_calls, "改回預設不應呼叫合成 worker")

        begin("完整模式:清晰度模式拖曳選取「歡迎」設 40% 並套用")
        page.locator('#props-body .tone-mode[data-mode="clarity"]').click()
        expect(page.locator('#props-body .tone-mode[data-mode="clarity"]')).to_have_attribute("aria-pressed", "true")
        c4 = page.locator('#props-body .tone-char[data-index="4"]')
        c5 = page.locator('#props-body .tone-char[data-index="5"]')
        expect(c4.locator(".tone-glyph")).to_have_text("歡")
        b4, b5 = c4.bounding_box(), c5.bounding_box()
        page.mouse.move(b4["x"] + b4["width"] / 2, b4["y"] + b4["height"] / 2)
        page.mouse.down()
        page.mouse.move(b5["x"] + b5["width"] / 2, b5["y"] + b5["height"] / 2, steps=4)
        page.mouse.up()
        wpop = page.locator("#word-popup")
        expect(wpop).to_be_visible()
        expect(wpop.locator(".tone-title")).to_have_text("「歡迎」的清晰度 — 第 5–6 字")
        wpop.locator("input[type=range]").fill("40")
        expect(wpop.locator("output")).to_have_text("40%")
        wpop.get_by_role("button", name="套用").click()
        wait_until(lambda: proj().segments[0]["word_clarity"] == [{"start": 4, "end": 6, "value": 40}],
                   f"字詞清晰度應存成 [4, 6) 40%,實際 {proj().segments[0]['word_clarity']}")
        wait_until(lambda: pj.is_post_fresh(proj().segments[0]), "字詞清晰度改了之後應完成後製")
        expect(page.locator('#props-body .tone-char[data-index="4"] .clarity-bar.is-own')).to_have_count(1)
        check(len(calls()) == n_calls, "改字詞清晰度不應呼叫合成 worker")
        shot("prosody.png")

        # CHG-20261029-01 修訂 1 T13:清晰度低於 25% 只警告、不阻擋(R1-3)
        warn_text = "⚠ 低於 25% 會過度含糊、聲音偏悶,不建議使用;需要含糊感時建議用 50% 或 25%。"
        clar_sel = '#props-body [data-hook="prosody"] input[aria-label="清晰度(百分比)"]'
        warn = page.locator('#props-body [data-hook="prosody"] .clarity-warn')

        def no_error_toast():
            # 只警告、不阻擋:不跳出錯誤或警告訊息(修改成功的一般提示照舊)
            t = page.evaluate("() => { const t = document.getElementById('toast');"
                              " return [t.hidden, t.classList.contains('toast-error'), t.textContent]; }")
            return t[0] or (not t[1] and "低於 25%" not in t[2])

        def drag_word(a, b):
            ca = page.locator(f'#props-body .tone-char[data-index="{a}"]').bounding_box()
            cb = page.locator(f'#props-body .tone-char[data-index="{b}"]').bounding_box()
            page.mouse.move(ca["x"] + ca["width"] / 2, ca["y"] + ca["height"] / 2)
            page.mouse.down()
            page.mouse.move(cb["x"] + cb["width"] / 2, cb["y"] + cb["height"] / 2, steps=4)
            page.mouse.up()

        begin("屬性面板:整句清晰度拖到 24% 即時出現警告(role=status),拖到 25% 消失")
        page.locator('#props-body .tone-mode[data-mode="tone"]').click()
        clar = page.locator(clar_sel)
        expect(warn).to_be_hidden()
        clar.evaluate("(e) => { e.value = '24'; e.dispatchEvent(new Event('input', {bubbles: true})); }")
        expect(warn).to_be_visible()
        expect(warn).to_contain_text("建議用 50% 或 25%")
        expect(warn).to_have_text(warn_text)
        expect(warn).to_have_attribute("role", "status")
        check(proj().segments[0]["clarity"] is None, "拖動中(還沒放開)不應送出")
        clar.evaluate("(e) => { e.value = '25'; e.dispatchEvent(new Event('input', {bubbles: true})); }")
        expect(warn).to_be_hidden()

        begin("屬性面板:放開在 24% 照常存檔(不阻擋、不跳錯誤訊息),警告仍在;按「預設」後消失")
        clar.evaluate("(e) => { e.value = '24'; e.dispatchEvent(new Event('input', {bubbles: true}));"
                      " e.dispatchEvent(new Event('change', {bubbles: true})); }")
        wait_until(lambda: proj().segments[0]["clarity"] == 24, "放開在 24 後清晰度應存成 24")
        check(no_error_toast(), "清晰度 24% 不應跳出錯誤或警告訊息")
        expect(page.locator(clar_sel)).to_have_value("24")
        expect(warn).to_be_visible()
        wait_until(lambda: pj.is_post_fresh(proj().segments[0]), "清晰度改了之後應完成後製")
        page.locator('#props-body [data-hook="prosody"] .pct-row').nth(1).locator(".pct-reset").click()
        wait_until(lambda: proj().segments[0]["clarity"] is None, "按「預設」後清晰度應為 null")
        expect(warn).to_be_hidden()

        begin("字詞清晰度彈出框:「收聽」設 15% 出現同一則警告且「套用」可按,套用後存入")
        page.locator('#props-body .tone-mode[data-mode="clarity"]').click()
        expect(page.locator('#props-body .tone-char[data-index="6"] .tone-glyph')).to_have_text("收")
        drag_word(6, 7)
        expect(wpop).to_be_visible()
        wwarn = wpop.locator(".clarity-warn")
        expect(wwarn).to_be_hidden()
        wpop.locator("input[type=range]").fill("15")
        expect(wpop.locator("output")).to_have_text("15%")
        expect(wwarn).to_be_visible()
        expect(wwarn).to_have_text(warn_text)
        expect(wwarn).to_have_attribute("role", "status")
        expect(wpop.get_by_role("button", name="套用")).to_be_enabled()
        wpop.locator("input[type=range]").fill("25")
        expect(wwarn).to_be_hidden()
        wpop.locator("input[type=range]").fill("15")
        expect(wwarn).to_be_visible()
        wpop.get_by_role("button", name="套用").click()
        wait_until(lambda: {"start": 6, "end": 8, "value": 15} in proj().segments[0]["word_clarity"],
                   f"字詞清晰度應有 [6, 8) 15%,實際 {proj().segments[0]['word_clarity']}")
        check(no_error_toast(), "字詞清晰度 15% 不應跳出錯誤或警告訊息")
        wait_until(lambda: pj.is_post_fresh(proj().segments[0]), "字詞清晰度改了之後應完成後製")

        begin("重新載入頁面後,清晰度 20% 的片段仍顯示警告")
        page.locator('#props-body .tone-mode[data-mode="tone"]').click()
        page.locator(clar_sel).fill("20")
        wait_until(lambda: proj().segments[0]["clarity"] == 20, "清晰度應存成 20")
        wait_until(lambda: pj.is_post_fresh(proj().segments[0]), "清晰度改了之後應完成後製")
        page.reload()
        expect(page.locator("#full-body")).to_be_visible()
        page.locator(f'#tl-voice .tl-seg[data-segment="{seg_ids[0]}"]').click()
        expect(page.locator("#props-body .prop-title")).to_have_text("選取:片段 #1")
        expect(page.locator(clar_sel)).to_have_value("20")
        expect(warn).to_be_visible()
        expect(warn).to_have_text(warn_text)
        page.locator('#props-body .tone-mode[data-mode="clarity"]').click()
        drag_word(6, 7)
        expect(wpop).to_be_visible()
        expect(wpop.locator(".clarity-warn")).to_be_visible()
        shot("clarity-warn.png")
        page.keyboard.press("Escape")
        page.locator('#props-body .tone-mode[data-mode="tone"]').click()
        page.locator('#props-body [data-hook="prosody"] .pct-row').nth(1).locator(".pct-reset").click()
        wait_until(lambda: proj().segments[0]["clarity"] is None, "按「預設」後清晰度應為 null")
        expect(warn).to_be_hidden()

        begin("完整模式:自錄音檔的第 2 句,韻律拉桿停用")
        page.locator(f'#tl-voice .tl-seg[data-segment="{seg_ids[1]}"]').click()
        expect(page.locator("#props-body .prop-title")).to_have_text("選取:片段 #2")
        for lab in ("連音", "清晰度"):
            expect(page.locator(f'#props-body input[aria-label="{lab}(百分比)"]')).to_be_disabled()
        expect(page.locator('#props-body [data-hook="prosody"]')).to_contain_text("自錄音檔不做後製")
        page.locator('#props-body .tone-mode[data-mode="tone"]').click()

        # CHG-20261029-01 T6:語調折線
        seg0 = f'#tl-voice .tl-seg[data-segment="{seg_ids[0]}"]'
        keys = page.locator(f"{seg0} svg.tl-pitch .tl-pitch-key")

        def pitch_pts():
            p = proj().segments[0]["pitch"]
            return p["points"] if p else None

        def dbl_at(fx, fy):
            box = page.locator(f"{seg0} svg.tl-pitch.is-editable").bounding_box()
            page.mouse.dblclick(box["x"] + box["width"] * fx, box["y"] + box["height"] * fy)

        begin("時間軸:選取第 1 句,點兩下新增關鍵點(菱形),只重做後製")
        n_calls = len(calls())
        page.locator("#tl-zoom-in").click()                     # 放大時間軸,片段夠寬才好點選關鍵點
        page.locator("#tl-zoom-in").click()
        page.locator("#tl-zoom-in").click()
        page.locator(seg0).click()
        expect(page.locator(f"{seg0} svg.tl-pitch.is-editable")).to_have_count(1)
        check(page.locator("#tl-voice").bounding_box()["height"] >= 77, "語音軌高度應為 78 px")
        dbl_at(0.3, 0.25)
        wait_until(lambda: pitch_pts() is not None and len(pitch_pts()) == 3, f"第一次點兩下應建立 3 點,實際 {pitch_pts()}")
        expect(keys).to_have_count(3)
        dbl_at(0.7, 0.75)
        wait_until(lambda: pitch_pts() is not None and len(pitch_pts()) == 4, f"再點兩下應為 4 點,實際 {pitch_pts()}")
        expect(keys).to_have_count(4)
        check(pitch_pts()[1]["semi"] > 0 and pitch_pts()[2]["semi"] < 0, f"點的高度應對應音高,實際 {pitch_pts()}")
        wait_until(lambda: pj.is_post_fresh(proj().segments[0]), "新增關鍵點後應完成後製")
        check(len(calls()) == n_calls, "新增關鍵點不應呼叫合成 worker")

        begin("時間軸:把第 3 個關鍵點往上拖(位置不越過相鄰點),屬性面板顯示選取中與半音")
        before = pitch_pts()
        k = keys.nth(2)
        kb = k.bounding_box()
        x, y = kb["x"] + kb["width"] / 2, kb["y"] + kb["height"] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x - 600, y - 20, steps=6)
        page.mouse.move(x - 600, y - 40, steps=6)
        page.mouse.up()
        wait_until(lambda: pitch_pts()[2]["semi"] > 0, f"往上拖後第 3 點半音應 > 0,實際 {pitch_pts()}")
        got = pitch_pts()
        check(got[1]["pos"] < got[2]["pos"] < got[3]["pos"] and got[2]["pos"] >= before[1]["pos"] + 0.01,
              f"拖曳不應越過相鄰點,實際 {got}")
        expect(page.locator('#props-body [data-hook="pitch-info"]')).to_contain_text("選取中:#3")
        expect(page.locator('#props-body [data-hook="pitch-info"]')).to_contain_text("半音")
        check(len(calls()) == n_calls, "拖曳關鍵點不應呼叫合成 worker")

        begin("屬性面板:圓弧 0% 與 100% 的曲線不同(修訂 1 R1-6:PCHIP 混合,不再用二次貝茲)")
        rnd = page.locator('#props-body input[aria-label="圓弧(百分比)"]')
        rnd.fill("0")
        wait_until(lambda: proj().segments[0]["pitch"]["roundness"] == 0, "圓弧應存成 0")
        expect(page.locator('#props-body input[aria-label="圓弧(百分比)"]')).to_have_value("0")
        d0 = page.locator(f"{seg0} svg.tl-pitch .tl-pitch-line").get_attribute("d")
        page.locator('#props-body input[aria-label="圓弧(百分比)"]').fill("100")
        wait_until(lambda: proj().segments[0]["pitch"]["roundness"] == 100, "圓弧應存成 100")
        expect(page.locator('#props-body input[aria-label="圓弧(百分比)"]')).to_have_value("100")
        d100 = page.locator(f"{seg0} svg.tl-pitch .tl-pitch-line").get_attribute("d")
        check(d0 != d100 and "Q" not in d100, f"圓弧 0% / 100% 的 path 應不同且不含二次貝茲:{d0} / {d100}")
        wait_until(lambda: pj.is_post_fresh(proj().segments[0]), "改圓弧後應完成後製")
        shot("pitch.png")

        begin("預覽:第 1 句播放的 <audio> 來源是後製檔")
        post_id = proj().segments[0]["post"]["id"]
        page.locator("#preview-start").click()
        page.locator("#preview-play").click()
        wait_until(lambda: post_id in (page.evaluate("window.Studio.Preview.audio.src") or ""),
                   "預覽第 1 句時 <audio> 應播放後製檔")
        page.locator("#preview-play").click()
        expect(page.locator("#preview-play")).to_have_attribute("aria-label", "播放")

        begin("時間軸:選取關鍵點後按 Delete 刪除;剩 2 點時再刪即清除語調")
        page.locator(seg0).click()
        for want in (3, 2):
            keys.nth(1).click()
            page.keyboard.press("Delete")
            wait_until(lambda: pitch_pts() is not None and len(pitch_pts()) == want, f"刪除後應剩 {want} 點,實際 {pitch_pts()}")
            expect(keys).to_have_count(want)
        keys.nth(0).click()
        page.keyboard.press("Delete")
        wait_until(lambda: pitch_pts() is None, "剩 2 點時再刪應清除語調(pitch 為 null)")
        expect(keys).to_have_count(0)
        check(len(calls()) == n_calls, "編輯語調不應呼叫合成 worker")

        # CHG-20261029-01 修訂 1 T13:自動補點、放大峰谷與 PCHIP 混合曲線(R1-6~R1-10)
        user5 = [{"pos": 0, "semi": 0}, {"pos": 0.25, "semi": 3}, {"pos": 0.5, "semi": -2},
                 {"pos": 0.75, "semi": 2}, {"pos": 1, "semi": 0}]
        svg_sel = f"{seg0} svg.tl-pitch.is-editable"

        def pitch_screen():
            """畫面上的自動補點、放大峰谷、使用者菱形中心(相對 SVG 左上角)與 SVG 大小。"""
            return page.evaluate(JS_PITCH_SCREEN, svg_sel)

        def to_screen(q, w, h):
            # 同 timeline.js 的 semiToY:縱軸 ±7.2 半音(R1-9),上下各留 6 px
            return [q[0] * w, h / 2 - q[1] / 7.2 * (h / 2 - 6)]

        def near(a, b, tol):
            return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol

        def split_pp(pp):
            user_pos = {p["pos"] for p in user5}
            amp = [q for q in pp if q[0] in (0.25, 0.5, 0.75)]
            auto = [q for q in pp if q[0] not in user_pos]
            return auto, amp

        def check_path_through(d, pts, w, h, label):
            verts = [(float(a), float(b)) for a, b in re.findall(r"(-?[0-9.]+) (-?[0-9.]+)", d)]
            for q in pts:
                x, y = to_screen(q, w, h)
                best = min(math.hypot(vx - x, vy - y) for vx, vy in verts)
                check(best <= 0.5, f"{label}:曲線應經過實際套用的點 {q}(畫面 {x:.1f}, {y:.1f}),最近 {best:.2f} px")

        begin("時間軸:語調 5 點 0、+3、−2、+2、0(圓弧 100%)後製完成後有 3 個自動補點與 3 個放大峰谷,位置與 post.pitch_points 一致")
        page.locator(seg0).click()
        expect(page.locator(svg_sel)).to_have_count(1)
        sid0 = seg_ids[0]
        page.evaluate("([id, pts]) => window.Studio.Edit.patchAndPost(window.Studio.Store.segment(id),"
                      " {pitch: {points: pts, roundness: 100}}, '')", [sid0, user5])
        wait_until(lambda: pitch_pts() == user5 and pj.is_post_fresh(proj().segments[0])
                   and "pitch_points" in proj().segments[0]["post"], "語調 5 點的後製應完成且帶 pitch_points")
        pp = proj().segments[0]["post"]["pitch_points"]
        check(len(pp) == 8 and [0.405, 0.9] in [[round(a, 3), round(b, 3)] for a, b in pp],
              f"替身應回傳 8 個內部點(含吸附到 0.405 的升點),實際 {pp}")
        auto_pp, amp_pp = split_pp(pp)
        expect(page.locator(f"{seg0} .tl-pitch-auto")).to_have_count(3)
        expect(page.locator(f"{seg0} .tl-pitch-amp")).to_have_count(3)
        expect(keys).to_have_count(5)
        expect(page.locator(f"{seg0} .tl-pitch-line.is-estimate")).to_have_count(0)
        scr = pitch_screen()
        w, h = scr["w"], scr["h"]
        for got, want, label in ((scr["auto"], auto_pp, "自動補點"), (scr["amp"], amp_pp, "放大峰谷")):
            for q in want:
                ex = to_screen(q, w, h)
                check(any(near(g, ex, 1) for g in got), f"{label} {q} 應畫在 {ex} 附近(≤ 1 px),畫面 {got}")
        for p_, g in zip(user5, scr["keys"]):
            check(near(g, to_screen([p_["pos"], p_["semi"]], w, h), 1), f"使用者菱形位置不符:{p_} / {g}")
        expect(page.locator('#props-body [data-hook="pitch-info"]')).to_contain_text("關鍵點 5 個")
        expect(page.locator('#props-body [data-hook="pitch-info"]')).to_contain_text("自動補點 3 個(虛線,不可拖曳)")
        expect(page.locator('#props-body [data-hook="pitch-props"]')).to_contain_text(
            "曲線一律經過每個實際套用的點(含放大後的峰谷與自動補點)")
        check_path_through(page.locator(f"{seg0} svg.tl-pitch .tl-pitch-line").get_attribute("d"), pp, w, h, "圓弧 100%")
        for r_ in (0, 50, 100):
            d = page.evaluate("([pp, r, w, h]) => window.Studio.pitchPath(pp, r, w, h)", [pp, r_, w, h])
            check_path_through(d, pp, w, h, f"pitchPath 圓弧 {r_}%")
        page.evaluate("() => { document.getElementById('toast').hidden = true; }")
        shot("pitch-auto.png")
        # 另存時間軸面板的放大圖(與 rev1-after-timeline.png 對照用;不列入 Interaction spec 的產出物)
        page.locator("#panel-timeline").screenshot(path=str(SHOT_DIR / "pitch-auto-timeline.png"), scale="device")

        begin("時間軸:拖曳自動補點不會移動它,使用者點數與 pitch.points 不變")
        n_patch = len(stubs.shape_calls())
        a0 = page.locator(f"{seg0} .tl-pitch-auto").first.bounding_box()
        ax, ay = a0["x"] + a0["width"] / 2, a0["y"] + a0["height"] / 2
        page.mouse.move(ax, ay)
        page.mouse.down()
        page.mouse.move(ax + 30, ay - 20, steps=6)
        page.mouse.up()
        page.wait_for_timeout(400)
        check(pitch_pts() == user5, f"拖曳自動補點後 pitch.points 應不變,實際 {pitch_pts()}")
        check(len(stubs.shape_calls()) == n_patch, "拖曳自動補點不應重做後製")
        expect(keys).to_have_count(5)
        expect(page.locator(f"{seg0} .tl-pitch-auto")).to_have_count(3)
        a1 = page.locator(f"{seg0} .tl-pitch-auto").first.bounding_box()
        check(abs(a1["x"] - a0["x"]) < 0.5 and abs(a1["y"] - a0["y"]) < 0.5, "自動補點不應被拖動")

        begin("屬性面板:圓弧 50%、0% 後製完成後,畫出的曲線仍經過每個實際套用的點")
        for r_ in (50, 0):
            page.locator('#props-body input[aria-label="圓弧(百分比)"]').fill(str(r_))
            wait_until(lambda: proj().segments[0]["pitch"]["roundness"] == r_
                       and pj.is_post_fresh(proj().segments[0]), f"圓弧 {r_}% 的後製應完成")
            pp_r = proj().segments[0]["post"]["pitch_points"]
            expect(page.locator(f"{seg0} .tl-pitch-line.is-estimate")).to_have_count(0)
            scr = pitch_screen()
            check_path_through(page.locator(f"{seg0} svg.tl-pitch .tl-pitch-line").get_attribute("d"),
                               pp_r, scr["w"], scr["h"], f"畫面上的圓弧 {r_}%")

        begin("時間軸:後製有效但沒有 pitch_points(快取命中)時,退回淡色試算(不含吸附)")
        page.locator('#props-body input[aria-label="圓弧(百分比)"]').fill("100")
        wait_until(lambda: proj().segments[0]["pitch"]["roundness"] == 100
                   and pj.is_post_fresh(proj().segments[0]), "圓弧改回 100% 的後製應完成(快取命中)")
        check("pitch_points" not in proj().segments[0]["post"],
              f"快取命中且專案內沒有同鍵的 pitch_points 時不應帶此欄位:{proj().segments[0]['post']}")
        expect(page.locator(f"{seg0} .tl-pitch-line.is-estimate")).to_have_count(1)
        expect(page.locator(f"{seg0} .tl-pitch-auto.is-estimate")).to_have_count(3)
        expect(page.locator(f"{seg0} .tl-pitch-amp.is-estimate")).to_have_count(3)
        scr = pitch_screen()
        est = [[0.175, 1.1], [0.425, 0.5], [0.675, -0.2]]      # R1-7 的例子(未吸附)
        for q in est:
            ex = to_screen(q, scr["w"], scr["h"])
            check(any(near(g, ex, 1) for g in scr["auto"]), f"試算的自動補點 {q} 應畫在 {ex} 附近,畫面 {scr['auto']}")
        expect(page.locator('#props-body [data-hook="pitch-info"]')).to_contain_text("自動補點 3 個")
        page.locator('#props-body [data-hook="pitch-props"] button', has_text="清除語調").click()
        wait_until(lambda: pitch_pts() is None and pj.is_post_fresh(proj().segments[0]), "清除語調後應完成後製")
        expect(page.locator(f"{seg0} .tl-pitch-auto")).to_have_count(0)
        check(len(calls()) == n_calls, "編輯語調不應呼叫合成 worker")

        # CHG-20261029-01 T9:文稿框的合音標示(D40~D43)與聲調區「合音」模式(D23~D28、D37)
        fusion_editor_steps(page, expect, begin, shot, proj, calls, wait_until, toast, problems, seg_ids)

        # 4. 免責說明與授權
        begin("回首頁點底部「免責說明與授權」連結")
        page.locator("#page-full .back-link").click()
        expect(home).to_be_visible()
        home.locator(".page-footer a", has_text="免責說明與授權").click()
        disc = page.locator("#page-disclaimer")
        expect(disc).to_be_visible()
        expect(disc.locator(".section-title")).to_have_text(["AI 生成內容聲明", "聲線", "模型與元件授權"])
        expect(disc).to_contain_text("須取得聲音所有人同意")
        rows_l = disc.locator("#license-table tbody tr")
        check(rows_l.count() >= 3, "授權表應列出模型與元件")
        for i in range(rows_l.count()):
            cells = [t.strip() for t in rows_l.nth(i).locator("td").all_text_contents()]
            check(len(cells) == 4 and all(cells), f"授權表第 {i + 1} 列應有元件、用途、授權、來源(或「未知」):{cells}")
        shot("disclaimer.png")

        # 5. 進階流程刪除封面步驟後,精靈步驟跟著改
        begin("設定 › 進階流程:刪除封面步驟的四個節點")
        disc.locator(".open-settings").click()
        expect(page.locator("#page-settings")).to_be_visible()
        page.locator('.subnav-link[data-sub="flow"]').click()
        expect(page.locator("#statusbar")).to_have_text("已載入預設流程")
        expect(page.locator("#drawflow .drawflow-node")).to_have_count(9)
        title_ids = page.evaluate(JS_FIND_TITLE_NODE)
        check(len(title_ids) == 1, f"應找到 1 個接到「封面 prompt」的文字節點,實際 {title_ids}")
        targets = [title_ids[0]] + [page.evaluate(JS_NODE_IDS, lb)[0] for lb in ("封面 prompt", "生圖", "疊標題")]
        for nid in targets:
            node = page.locator(f"#node-{nid}")
            node.scroll_into_view_if_needed()
            node.locator(".node-title").click()
            expect(node).to_have_class(re.compile(r"\bselected\b"))
            page.keyboard.press("Delete")
            expect(node).to_have_count(0)
        expect(page.locator("#drawflow .drawflow-node")).to_have_count(5)

        begin("「合成影片」的圖片輸入改接拖入的圖片檔(否則流程驗證不過、無法存檔)")
        mv = page.evaluate(JS_NODE_IDS, "合成影片")
        check(len(mv) == 1, "畫布上應有 1 個「合成影片」節點")
        port = page.locator(f"#node-{mv[0]} .inputs .input_1")
        port.scroll_into_view_if_needed()
        pb, cb = port.bounding_box(), page.locator("#drawflow").bounding_box()
        page.evaluate(JS_DROP_FILES, ["#drawflow", pb["x"] - cb["x"] + pb["width"] / 2,
                                      pb["y"] - cb["y"] + pb["height"] / 2,
                                      [file_arg("片頭圖.png", color_png((20, 120, 60)), "image/png")]])
        expect(toast).to_have_text("「合成影片」的「封面」輸入已改用「片頭圖.png」")
        expect(page.locator("#drawflow .drawflow-node")).to_have_count(6)

        begin("以「default」覆寫存檔預設流程")
        page.locator("#btn-save").click()
        expect(page.locator("#wf-dialog")).to_be_visible()
        page.locator("#wf-name").fill("default")
        n_before = len(problems)
        page.locator("#wf-submit").click()
        expect(page.locator("#wf-confirm")).to_be_visible()
        # 第一次 PUT(未帶 overwrite)回 409 是預期的(介面據此詢問是否覆寫);瀏覽器會把它記成一筆
        # console error,只容許這一筆,其餘照常算失敗
        expected = [p for p in problems[n_before:] if "status of 409" in p]
        check(len(expected) <= 1, f"存檔時不應出現多筆 409:{expected}")
        if expected:
            problems.remove(expected[0])
        page.locator("#wf-overwrite").click()
        expect(page.locator("#wf-dialog")).to_be_hidden()
        expect(page.locator("#statusbar")).to_have_text("已存檔:「default」")
        saved = json.loads((root / "workflows" / "default.json").read_text(encoding="utf-8"))
        labels = [s.get("label") for s in saved.get("steps", [])]
        check(labels == ["文稿", "語音", "剪輯", "匯出"], f"存檔後的 steps 應去掉「封面」,實際 {labels}")

        begin("回首頁開精靈:步驟不含「封面」且仍含「剪輯」")
        page.locator("#page-settings .back-link").click()
        expect(home).to_be_visible()
        page.locator("#card-wizard").click()
        expect(wiz).to_be_visible()
        expect(page.locator("#wizard-steps li")).to_have_count(4)
        got = step_labels()
        check(not any("封面" in s for s in got), f"精靈步驟不應含「封面」:{got}")
        check(any("剪輯" in s for s in got), f"精靈步驟應仍含「剪輯」:{got}")
        expect(page.locator("#wizard-error")).to_be_hidden()

        begin("延遲回應期間切換專案:mutate、AI 封面、AI 寫稿的結果不套到新專案(DIR-6 修正 2)")
        got = page.evaluate(JS_STALE_SWITCH, [PROJECT_NAME, "切換測試"])
        check(got.get("created") is True, f"應能建立第二個專案:{got}")
        m = got["mutate"]
        check(m["stale"] and not m["ok"], f"切換後晚到的修改回應應標為 stale、不算成功:{m}")
        check(m["name"] == "切換測試" and m["viewName"] == "切換測試", f"畫面應仍是新專案:{m}")
        cov = got["cover"]
        check(cov["status"] == "封面已產生,但專案已切換,未加入", f"封面狀態文字不符:{cov}")
        check(cov["imagesB"] == 0 and cov["imagesA"] == cov["imagesABefore"], f"封面不應加入任何專案:{cov}")
        sc = got["script"]
        check(sc["status"] == "文稿已產生,但專案已切換,未填入" and not sc["filled"], f"文稿不應填入:{sc}")
        b_disk = json.loads((root / "projects" / "切換測試" / "project.json").read_text(encoding="utf-8"))
        check(b_disk["images"] == [] and b_disk["script"] == "切換測試的第一句。", "新專案的磁碟內容不應被改動")
    except StepFailed as e:
        raise StepFailed(f"步驟「{step['name']}」:{e}") from None
    except AssertionError as e:
        raise StepFailed(f"步驟「{step['name']}」:畫面不符預期({str(e).splitlines()[0] if str(e) else 'AssertionError'})") from None
    except Exception as e:  # Playwright 逾時等
        raise StepFailed(f"步驟「{step['name']}」:{type(e).__name__}:{str(e).splitlines()[0] if str(e) else ''}") from None


# ---------- 執行一組檢查 ----------

def run_suite(browser, suite: str) -> int:
    """在自己的暫存目錄與伺服器上跑一組檢查;通過回 0,失敗印出原因並回 1。"""
    from playwright.sync_api import Error as PwError
    import uvicorn

    import heavy_lock
    import nodes
    import project
    import settings
    import studio_server as ss
    import taiwan_ai
    import tts_service

    print(f"── {suite} ──", flush=True)
    tmp = Path(tempfile.mkdtemp(prefix=f"ui_check-{suite}-")).resolve()
    saved: list[tuple] = []

    def patch(obj, attr, value):
        saved.append((obj, attr, getattr(obj, attr)))
        setattr(obj, attr, value)

    server = None
    thread = None
    problems: list[str] = []
    try:
        root = make_root(tmp)
        patch(settings, "ROOT", root)
        patch(settings, "CONFIG_PATH", root / "config" / "models.json")
        patch(ss, "ROOT", root)
        port = free_port()
        patch(ss, "ALLOWED_HOSTS", frozenset({f"127.0.0.1:{port}"}))
        patch(ss, "ALLOWED_ORIGINS", frozenset({f"http://127.0.0.1:{port}"}))
        runner = StubRunner(nodes.REGISTRY)
        kwargs = {}
        stubs = None
        if suite == "editor":
            stubs = EditorStubs(tmp)
            stubs.out = root / "outputs"
            (root / "voice").mkdir(exist_ok=True)
            (root / "voice" / "voice.wav").write_bytes(sine_wav(0.3, rate=16000))
            patch(project, "ROOT", root)
            patch(taiwan_ai, "ROOT", root)
            patch(taiwan_ai, "OUT", root / "outputs")
            patch(heavy_lock, "ROOT", root)
            patch(heavy_lock, "LOCK_PATH", root / "outputs" / ".heavy.lock")
            patch(tts_service, "BREEZY", stubs.breezy)
            patch(tts_service, "_worker_argv", stubs.worker_argv)
            patch(tts_service, "_shape_argv", stubs.shape_argv)
            patch(taiwan_ai, "role_config", lambda role: {"wav": "voice/voice.wav", "text": VOICE_TEXT})
            kwargs = {"render": stubs.render, "annotate": stubs.annotate}
        app = ss.create_app(runner=runner, root=root, ollama_tags=lambda: list(STUB_TAGS), **kwargs)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                               proxy_headers=False, server_header=False))
        thread = threading.Thread(target=server.run, name=f"ui-check-server-{suite}", daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        while not server.started:
            if time.monotonic() > deadline or not thread.is_alive():
                print("UI 檢查失敗:步驟「啟動伺服器」:伺服器沒有在 15 秒內啟動", file=sys.stderr)
                return 1
            time.sleep(0.05)
        base = f"http://127.0.0.1:{port}"

        page = browser.new_page(viewport=VIEWPORT, locale="zh-TW")
        try:
            page.set_default_timeout(TIMEOUT_MS)
            page.add_init_script(INIT_SCRIPT)
            page.on("console", lambda m: problems.append(f"console {m.type}:{m.text}")
                    if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: problems.append(f"未捕捉例外:{e}"))
            try:
                if suite == "editor":
                    run_editor_checks(page, base, root, stubs, problems)
                else:
                    run_checks(page, base, root, runner, settings, problems)
                csp = page.evaluate("window.__csp || []")
                problems.extend(f"CSP 違規:{c}" for c in csp)
            except StepFailed as e:
                try:
                    page.screenshot(path=str(SHOT_DIR / "failure.png"))
                except PwError:
                    pass
                print(f"UI 檢查失敗({suite}):{e}", file=sys.stderr)
                for p in problems:
                    print(f"  {p}", file=sys.stderr)
                return 1
        finally:
            page.close()
    finally:
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=10)
        for obj, attr, value in reversed(saved):
            setattr(obj, attr, value)
        shutil.rmtree(tmp, ignore_errors=True)

    if problems:
        print(f"UI 檢查失敗({suite}):步驟「瀏覽器 console 與 CSP」:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    return 0


SUITES = {"canvas": CANVAS_SHOTS, "editor": EDITOR_SHOTS}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="UI 互動檢查(無頭 Chromium)")
    ap.add_argument("--only", choices=["editor"],
                    help="只跑指定的一組檢查(editor = CHG-20261022-04 的首頁、精靈、完整模式與剪輯);不帶則全部")
    args = ap.parse_args(argv)
    suites = [args.only] if args.only else ["canvas", "editor"]

    try:
        from playwright.sync_api import Error as PwError, expect, sync_playwright
    except ImportError:
        print("UI 檢查失敗:找不到 playwright;請以 `uv run --script tests/ui_check.py` 執行", file=sys.stderr)
        return 2

    import studio_server as ss

    expect.set_options(timeout=TIMEOUT_MS)
    default_hosts = frozenset({"127.0.0.1:7860", "localhost:7860"})
    if ss.ALLOWED_HOSTS != default_hosts:
        print("UI 檢查失敗:studio_server.ALLOWED_HOSTS 不是預期的預設值,請先更新本檔", file=sys.stderr)
        return 1

    guarded = [REPO / "config" / "studio.db", REPO / "config" / "studio.db-wal", REPO / "config" / "models.json",
               REPO / "workflows", REPO / "workflows" / "default.json", REPO / "outputs" / "uploads",
               REPO / "projects", REPO / "outputs" / ".heavy.lock"]
    before = {p: snapshot(p) for p in guarded}
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    shots = [n for s in suites for n in SUITES[s]]
    for name in [*shots, "failure.png"]:
        (SHOT_DIR / name).unlink(missing_ok=True)

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except PwError as e:
            print("UI 檢查失敗:步驟「啟動 Chromium」:無法啟動瀏覽器;請先執行 "
                  "`uv run --with playwright==1.64.1 playwright install chromium`"
                  f"({str(e).splitlines()[0]})", file=sys.stderr)
            return 1
        try:
            for suite in suites:
                code = run_suite(browser, suite)
                if code:
                    return code
        finally:
            browser.close()

    after = {p: snapshot(p) for p in guarded}
    changed = [str(p.relative_to(REPO)) for p in guarded if before[p] != after[p]]
    if changed:
        print(f"UI 檢查失敗:步驟「確認未碰 repo 檔案」:{'、'.join(changed)} 被改動", file=sys.stderr)
        return 1
    missing = [n for n in shots if not (SHOT_DIR / n).is_file()]
    if missing:
        print(f"UI 檢查失敗:步驟「截圖」:缺少 {'、'.join(missing)}", file=sys.stderr)
        return 1
    print("UI 檢查通過;截圖:")
    for n in shots:
        print(f"  {SHOT_DIR / n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
