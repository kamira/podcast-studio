# /// script
# requires-python = ">=3.10"
# dependencies = ["opencc-python-reimplemented"]
# ///
"""台灣在地多模型串接函式庫(網頁與 MCP 共用;CHG-20261022-01 T2 起不再提供 CLI)。

角色(模型名稱一律經 `settings.load()` 讀設定資料庫 `config/studio.db`,見 settings.py):
  reason        邏輯推理(預設 Qwen3.5 9B 去審查)
  reason_heavy  重型推理(預設 Qwen3.8 27B,較準、較慢)
  taiwan        台灣用語(預設 Llama-Breeze2 8B)
  speak         文字轉語音 BreezyVoice(台灣華語)

介面:
  model(role) -> str      每次呼叫重讀設定,回傳該角色的模型名稱;未知角色丟 KeyError
  chat(role, prompt, system="", think=None) -> str
  unload_llms()           卸載 Ollama `/api/ps` 列出的全部模型(ADP-1)
  stamp() -> str          輸出檔名用時間戳:YYYYMMDD-HHMMSS-毫秒-4 碼隨機(ADP-8)
  reserve(prefix, suffix, also=()) -> Path
                          在 OUT 下排他建立 `<prefix>-<stamp()><suffix>` 空佔位檔,撞名重取;
                          輸出檔一律先佔位再寫入,不覆寫既有檔案
  speak(text) -> Path     BreezyVoice 子程序環境含 HF_HUB_OFFLINE、TRANSFORMERS_OFFLINE(ADP-12),
                          模型快取不存在時失敗、不下載;呼叫端須在 heavy_lock() 內呼叫(ADP-2)
"""
import json
import os
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
    """回傳角色的模型名稱;每次呼叫經 settings.load() 重讀設定資料庫 config/studio.db。"""
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


RESERVE_TRIES = 20


def _create_exclusive(path):
    """排他建立空檔;已存在(含符號連結,即使指向不存在的目標)丟 FileExistsError,不跟隨連結。"""
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o644)
    os.close(fd)


def reserve(prefix, suffix, also=()):
    """在 OUT 下排他建立 `<prefix>-<stamp()><suffix>` 空佔位檔並回傳路徑(不覆寫既有檔案)。

    also:同一主檔名還要一併佔下的其他副檔名(例如影片的 `.srt`),全部佔到才算成功。
    撞名就重取 stamp(),最多 RESERVE_TRIES 次;仍失敗丟 FileExistsError。
    外部程序(BreezyVoice、ffmpeg、PIL)之後寫入的是這個佔位檔。
    """
    for _ in range(RESERVE_TRIES):
        stem = OUT / f"{prefix}-{stamp()}"
        made = []
        try:
            for suf in (suffix, *also):
                path = stem.with_name(stem.name + suf)
                _create_exclusive(path)
                made.append(path)
        except FileExistsError:
            for m in made:  # 只清掉這一輪自己建立的佔位檔
                m.unlink(missing_ok=True)
            continue
        return made[0]
    raise FileExistsError(f"連續 {RESERVE_TRIES} 次撞名,無法在 {OUT} 建立 {prefix}-*{suffix}")


class discard_on_error:
    """包住寫入佔位檔的步驟;發生例外時刪掉佔位檔(不留下空檔),再把例外往外丟。"""

    def __init__(self, *paths):
        self.paths = paths

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            for p in self.paths:
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError:
                    pass
        return False


# BreezyVoice 子程序環境(ADP-12):HF_HUB_OFFLINE、TRANSFORMERS_OFFLINE 讓 snapshot_download 與
# BertTokenizer.from_pretrained 只讀本機快取、不連線 Hugging Face;快取不存在時直接失敗,不自動下載。
BREEZY_ENV = {"PYTHONUTF8": "1", "PATH": "/usr/bin:/bin",
              "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
SPEAK_FAILED_HINT = ("✗ 語音合成失敗。執行期不連網下載模型(HF_HUB_OFFLINE=1);若 BreezyVoice 或"
                     " bert-base-chinese 的 Hugging Face 模型快取不存在,需在建置期(允許連網)先下載一次再試。")


def speak(text):
    # BreezyVoice 讀 Markdown 符號會念錯,先清掉
    text = re.sub(r"[#*`>|_\-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    path = reserve("speech", ".wav")
    print("▶ 語音合成中(BreezyVoice,CPU 較慢)…", file=sys.stderr)
    with discard_on_error(path):
        try:
            subprocess.run([str(BREEZY / ".venv/bin/python"), "single_inference.py",
                            "--content_to_synthesize", text,
                            "--speaker_prompt_audio_path", str(VOICE_WAV),
                            "--speaker_prompt_text_transcription", VOICE_TEXT,
                            "--output_path", str(path)],
                           cwd=BREEZY, check=True, env=BREEZY_ENV,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            print(SPEAK_FAILED_HINT, file=sys.stderr)
            raise
    print(path)
    return path
