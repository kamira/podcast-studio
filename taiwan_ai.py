# /// script
# requires-python = ">=3.10"
# dependencies = ["opencc-python-reimplemented"]
# ///
"""台灣在地多模型串接函式庫(網頁與 MCP 共用;CHG-20261022-01 T2 起不再提供 CLI)。

角色(模型名稱一律讀 `config/models.json`,見 settings.py):
  reason        邏輯推理(預設 Qwen3.5 9B 去審查)
  reason_heavy  重型推理(預設 Qwen3.8 27B,較準、較慢)
  taiwan        台灣用語(預設 Llama-Breeze2 8B)
  speak         文字轉語音 BreezyVoice(台灣華語)

介面:
  model(role) -> str      每次呼叫重讀設定,回傳該角色的模型名稱;未知角色丟 KeyError
  chat(role, prompt, system="", think=None) -> str
  unload_llms()           卸載 Ollama `/api/ps` 列出的全部模型(ADP-1)
  stamp() -> str          輸出檔名用時間戳:YYYYMMDD-HHMMSS-毫秒-4 碼隨機(ADP-8)
  speak(text) -> Path
"""
import json
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from opencc import OpenCC

import settings

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs"
OLLAMA = "http://localhost:11434"

FLUX = "mflux-community/flux-1-schnell-mflux-q4"
MFLUX = Path.home() / ".local/bin/mflux-generate"
BREEZY = ROOT / "tools/BreezyVoice"
VOICE_WAV = BREEZY / "data/example.wav"
VOICE_TEXT = ("在密碼學中，加密是將明文資訊改變為難以讀取的密文內容，使之不可讀的方法。"
              "只有擁有解密方法的對象，經由解密過程，才能將密文還原為正常可讀的內容。")

TW = "一律使用繁體中文與台灣慣用語(例如:軟體、網路、資訊、影片、品質),不要使用中國大陸用語。"
to_tw = OpenCC("s2twp").convert  # 簡體/大陸用語 → 台灣繁體與用語


def _role(role):
    """回傳設定中該角色的設定(dict);未知角色(例如已移除的 code)丟 KeyError。"""
    roles = settings.load()["roles"]
    if role not in roles or not isinstance(roles[role], dict) or "model" not in roles[role]:
        raise KeyError(f"未知的模型角色:{role}")
    return roles[role]


def model(role):
    """回傳角色的模型名稱;每次呼叫重讀 config/models.json。"""
    return _role(role)["model"]


def chat(role, prompt, system="", think=None):
    # 思考模式會吃掉大量 token,預設 4096 的 context 會讓正式回答變成空白
    conf = _role(role)
    body = {"model": conf["model"], "stream": False, "keep_alive": "10m",
            "options": {"num_ctx": conf.get("num_ctx", 16384)},
            "messages": ([{"role": "system", "content": system}] if system else [])
            + [{"role": "user", "content": prompt}]}
    if think is not None:
        body["think"] = think
    req = urllib.request.Request(f"{OLLAMA}/api/chat", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    # 27B 約 14 tok/s,思考模式可能要跑十幾分鐘
    with urllib.request.urlopen(req, timeout=1800) as r:
        text = json.load(r)["message"]["content"]
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def unload_llms():
    """生圖前釋放 Ollama 佔用的記憶體(FLUX 峰值約 14GB)。

    卸載 `/api/ps` 列出的全部模型,不只設定中的角色——heavy 推理或其他程式載入的模型也一併釋放(ADP-1)。
    Ollama 連不上或單一模型卸載失敗都不丟例外。
    """
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/ps", timeout=10) as r:
            loaded = [m.get("model") or m.get("name") for m in json.load(r).get("models", [])]
    except Exception:
        return
    for m in loaded:
        if not m:
            continue
        body = json.dumps({"model": m, "keep_alive": 0}).encode()
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"{OLLAMA}/api/generate", body, {"Content-Type": "application/json"}), timeout=30)
        except Exception:
            pass


_STAMP_CHARS = "0123456789abcdefghijklmnopqrstuvwxyz"
_stamp_lock = threading.Lock()
_stamp_ms = None
_stamp_used: set[str] = set()


def stamp():
    """輸出檔名用的時間戳:`YYYYMMDD-HHMMSS-毫秒-4 碼隨機`(ADP-8)。

    同一行程同一毫秒內保證不重複;不同行程之間靠 36^4 種隨機碼降低碰撞機率。
    """
    global _stamp_ms
    OUT.mkdir(exist_ok=True)
    t = time.time()
    base = f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(t))}-{int(t * 1000) % 1000:03d}"
    with _stamp_lock:
        if base != _stamp_ms:  # 換了一毫秒,舊的隨機碼不會再撞
            _stamp_ms = base
            _stamp_used.clear()
        while True:
            rnd = "".join(secrets.choice(_STAMP_CHARS) for _ in range(4))
            if rnd not in _stamp_used:
                _stamp_used.add(rnd)
                return f"{base}-{rnd}"


def speak(text):
    # BreezyVoice 讀 Markdown 符號會念錯,先清掉
    text = re.sub(r"[#*`>|_\-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    path = OUT / f"speech-{stamp()}.wav"
    print("▶ 語音合成中(BreezyVoice,CPU 較慢)…", file=sys.stderr)
    subprocess.run([str(BREEZY / ".venv/bin/python"), "single_inference.py",
                    "--content_to_synthesize", text,
                    "--speaker_prompt_audio_path", str(VOICE_WAV),
                    "--speaker_prompt_text_transcription", VOICE_TEXT,
                    "--output_path", str(path)],
                   cwd=BREEZY, check=True, env={"PYTHONUTF8": "1", "PATH": "/usr/bin:/bin"},
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(path)
    return path
