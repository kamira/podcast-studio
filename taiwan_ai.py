#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["opencc-python-reimplemented"]
# ///
"""台灣在地多模型串接工具。

角色:
  reason  邏輯推理   Qwen3.5 9B Uncensored HauhauCS Aggressive(去審查)
  taiwan  台灣用語   Llama-Breeze2 8B(聯發科)
  code    程式       Qwen2.5-Coder 7B
  image   生圖       FLUX.1-schnell 4-bit(mflux)
  speak   文字轉語音 BreezyVoice(台灣華語)

用法:
  ./taiwan_ai.py ask   "問題"            推理 → 台灣用語潤飾
  ./taiwan_ai.py ask   "問題" --speak    同上,再念出來
  ./taiwan_ai.py code  "需求"            寫程式,說明用繁中
  ./taiwan_ai.py image "中文描述"        中文描述 → 英文 prompt → 生圖
  ./taiwan_ai.py speak "要念的文字"
  ./taiwan_ai.py auto  "任何需求"        自動判斷交給哪個角色
  加 --heavy                             推理改用 Qwen3.8 27B(較準、較慢)
  加 --no-think                          關閉思考模式(快很多)
"""
import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from opencc import OpenCC

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs"
OLLAMA = "http://localhost:11434"

MODELS = {
    "reason": "hf.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M",
    "taiwan": "hf.co/mradermacher/Llama-Breeze2-8B-Instruct-text-only-GGUF:Q4_K_M",
    "code": "qwen2.5-coder:7b",
}
# --heavy:推理改用 27B(約 13GB),較準但較慢,不能和生圖同時載入
HEAVY_REASON = "hf.co/HauhauCS/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF:IQ3_M"
FLUX = "mflux-community/flux-1-schnell-mflux-q4"
MFLUX = Path.home() / ".local/bin/mflux-generate"
BREEZY = ROOT / "tools/BreezyVoice"
VOICE_WAV = BREEZY / "data/example.wav"
VOICE_TEXT = ("在密碼學中，加密是將明文資訊改變為難以讀取的密文內容，使之不可讀的方法。"
              "只有擁有解密方法的對象，經由解密過程，才能將密文還原為正常可讀的內容。")

TW = "一律使用繁體中文與台灣慣用語(例如:軟體、網路、資訊、影片、品質),不要使用中國大陸用語。"
to_tw = OpenCC("s2twp").convert  # 簡體/大陸用語 → 台灣繁體與用語


def chat(role, prompt, system="", think=None):
    # 思考模式會吃掉大量 token,預設 4096 的 context 會讓正式回答變成空白
    body = {"model": MODELS[role], "stream": False, "keep_alive": "10m", "options": {"num_ctx": 16384},
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
    """生圖前釋放 Ollama 佔用的記憶體(FLUX 峰值約 14GB)。"""
    for m in MODELS.values():
        body = json.dumps({"model": m, "keep_alive": 0}).encode()
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"{OLLAMA}/api/generate", body, {"Content-Type": "application/json"}), timeout=30)
        except Exception:
            pass


def stamp():
    OUT.mkdir(exist_ok=True)
    return time.strftime("%Y%m%d-%H%M%S")


def ask(q, speak_it=False, think=True):
    print(f"▶ 推理中({MODELS['reason'].split('/')[-1]})…", file=sys.stderr)
    draft = chat("reason", q, system=f"你是嚴謹的推理助理,先想清楚再回答。{TW}", think=think)
    if not draft:
        sys.exit("推理模型沒有產生回答(可能是思考過長),請把問題拆小一點再試。")
    print("▶ 台灣用語潤飾(Breeze2)…", file=sys.stderr)
    final = chat("taiwan", f"請把以下內容改寫成自然的台灣繁體中文,保留所有事實、數字與結構,不要增刪內容,只輸出改寫結果:\n\n{draft}",
                 system=TW)
    final = to_tw(final)
    print(final)
    if speak_it:
        speak(final)
    return final


def code(req):
    print("▶ 寫程式中(Qwen2.5-Coder)…", file=sys.stderr)
    out = to_tw(chat("code", req, system=f"你是資深工程師。程式碼註解與說明{TW}"))
    print(out)
    return out


def image(desc):
    print("▶ 轉成英文 prompt(Qwen3)…", file=sys.stderr)
    prompt = chat("reason", desc, think=False, system=(
        "Rewrite the user's image request as one detailed English prompt for an image model "
        "(subject, setting, style, lighting, camera). If it mentions Taiwan, keep Taiwanese details. "
        "Output only the prompt."))
    print(f"  prompt: {prompt}", file=sys.stderr)
    unload_llms()
    path = OUT / f"image-{stamp()}.png"
    print("▶ 生圖中(FLUX.1-schnell)…", file=sys.stderr)
    subprocess.run([str(MFLUX), "--model", FLUX, "--base-model", "schnell", "--steps", "4",
                    "--width", "1024", "--height", "768", "--prompt", prompt,
                    "--output", str(path)], check=True, stdout=subprocess.DEVNULL)
    print(path)
    return path


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


def auto(req):
    route = chat("reason", req, think=False, system=(
        "Classify the request into exactly one word: code (programming), image (draw/generate a picture), "
        "speak (read text aloud), ask (everything else). Output only that word.")).lower()
    route = next((r for r in ("code", "image", "speak") if r in route), "ask")
    print(f"▶ 自動分派 → {route}", file=sys.stderr)
    return {"ask": ask, "code": code, "image": image, "speak": speak}[route](req)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["ask", "code", "image", "speak", "auto"])
    p.add_argument("text")
    p.add_argument("--speak", action="store_true", help="ask 模式:回答完再念出來")
    p.add_argument("--heavy", action="store_true", help="推理改用 Qwen3.8 27B")
    p.add_argument("--no-think", action="store_true", help="ask 模式:關閉思考,快很多但較不嚴謹")
    a = p.parse_args()
    if a.heavy:
        MODELS["reason"] = HEAVY_REASON
    if a.mode == "ask":
        ask(a.text, a.speak, think=not a.no_think)
    else:
        {"code": code, "image": image, "speak": speak, "auto": auto}[a.mode](a.text)


if __name__ == "__main__":
    main()
