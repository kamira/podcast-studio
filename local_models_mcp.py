#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=2.3", "opencc-python-reimplemented", "imageio-ffmpeg", "pillow<13"]
# ///
"""本機模型 MCP 伺服器(stdio)。把 taiwan_ai.py 的模型包成 MCP 工具給 Claude Code / Codex 使用。"""
import contextlib
import json
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
import media  # noqa: E402
import settings  # noqa: E402
import taiwan_ai as tai  # noqa: E402
from heavy_lock import heavy_lock  # noqa: E402

mcp = MCPServer("local-models")


def _settings():
    """每次工具呼叫重讀設定(網頁改了設定,MCP 下一次呼叫就生效)。

    設定不合法時 settings.load() 回預設值並把警告寫到 stderr(安全影響 (c));這裡不另外處理。
    """
    return settings.load()


def _waiting():
    print("[local-models] 另一個重型工作執行中(網頁或其他 MCP 呼叫),等待中…", file=sys.stderr)


@contextlib.contextmanager
def busy():
    """重讀設定、取得跨行程重型鎖(ADP-2,與網頁共用 outputs/.heavy.lock),並把 print 導到 stderr。

    stdout 是 MCP 協定通道,taiwan_ai 的 print 會弄壞它。24 GB 統一記憶體同時只撐得住一個重型工作,
    全部重型工具依序執行。
    """
    _settings()
    with heavy_lock(on_wait=_waiting), contextlib.redirect_stdout(sys.stderr):
        yield


@mcp.tool()
def reason(prompt: str, think: bool = True, heavy: bool = False) -> str:
    """用本機去審查推理模型回答(繁體中文、台灣用語)。也可用來寫程式。

    think: 開啟思考模式,較嚴謹但慢(9B 約 1–2 分鐘)。
    heavy: 改用 Qwen3.8 27B,較準但約慢 3 倍;配合 think 可能超過 10 分鐘。
    """
    with busy():
        role = "reason_heavy" if heavy else "reason"
        out = tai.chat(role, prompt, system=f"你是嚴謹的助理。{tai.TW}", think=think)
    if not out:
        raise RuntimeError("模型沒有產生回答(思考過長),請縮小問題或設 think=false")
    return tai.to_tw(out)


@mcp.tool()
def taiwanize(text: str) -> str:
    """把文字改寫成自然的台灣正體中文與台灣用語(Breeze2 + OpenCC),保留原意。"""
    with busy():
        out = tai.chat("taiwan", "請把以下內容改寫成自然的台灣繁體中文,保留所有事實、數字與結構,只輸出改寫結果:\n\n" + text,
                       system=tai.TW)
    return tai.to_tw(out)


@mcp.tool()
def generate_image(prompt: str, width: int = 1024, height: int = 1024) -> str:
    """用 FLUX.1-schnell 在本機生圖(約 20 秒),回傳 PNG 絕對路徑。

    prompt 用英文效果最好;寬高需為 16 的倍數。模型畫不出正確的中文字。
    """
    if width % 16 or height % 16:
        raise ValueError("width / height 需為 16 的倍數")
    with busy():
        return str(media.generate_image(prompt, width, height))


@mcp.tool()
def speak(text: str) -> str:
    """用 BreezyVoice 合成台灣口音華語,回傳 WAV 絕對路徑。

    跑在 CPU,1 分鐘音檔約需 9 分鐘。發音不對可用注音標註,例如「好[:ㄏㄠ3]」。
    """
    with busy():
        return str(tai.speak(text))


@mcp.tool()
def make_video(image_path: str, audio_path: str, script: str = "") -> str:
    """把靜態圖 + 音訊合成 1920×1080 MP4,回傳路徑。

    給 script(口播全文)時會依字數切句產生字幕,內嵌到 MP4 並另存同名 .srt。
    """
    for p in (image_path, audio_path):
        if not Path(p).is_file():
            raise FileNotFoundError(p)
    with busy():
        return str(media.make_video(image_path, audio_path, script))


@mcp.tool()
def list_models() -> str:
    """列出各角色使用的本機模型、是否已下載,以及目前載入記憶體的模型。"""
    with tai.local_open(f"{tai.OLLAMA}/api/tags", timeout=10) as r:
        have = {m["name"] for m in json.load(r)["models"]}
    with tai.local_open(f"{tai.OLLAMA}/api/ps", timeout=10) as r:
        loaded = [m["name"] for m in json.load(r)["models"]]
    roles = _settings()["roles"]  # 同一次呼叫內用同一份設定
    rows = {}
    for role in ("reason", "taiwan", "reason_heavy"):
        m = roles[role]["model"]
        rows[role] = {"model": m, "downloaded": m in have}
    rows["image"] = {"model": roles["image"]["model"],"tool": str(tai.MFLUX), "installed": tai.MFLUX.exists()}
    rows["speech"] = {"model": "BreezyVoice-300M", "installed": (tai.BREEZY / ".venv").exists()}
    return json.dumps({"roles": rows, "loaded_now": loaded, "outputs": str(tai.OUT)}, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    tai.OUT.mkdir(exist_ok=True)
    mcp.run()
