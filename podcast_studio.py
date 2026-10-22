#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["gradio>=5", "opencc-python-reimplemented", "imageio-ffmpeg", "pillow<13"]
# ///
"""Podcast 工作室:文案 → 人工修改 → 語音 → 封面 → 影片(16:9、含字幕),全部在本機執行。

  ./podcast_studio.py      然後開 http://localhost:7860
"""
import sys
from pathlib import Path

import gradio as gr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import media  # noqa: E402
import taiwan_ai as tai  # noqa: E402  共用模型設定、chat()、speak()

STYLES = ["輕鬆聊天", "知識科普", "新聞評論", "故事敘述", "訪談開場"]


def write_script(topic, style, minutes, notes):
    if not topic.strip():
        raise gr.Error("請先輸入主題")
    chars = int(minutes * 250)  # 台灣華語播報約每分鐘 250 字
    prompt = (f"請寫一篇單人主持的 podcast 口播稿。\n主題:{topic}\n風格:{style}\n"
              f"長度:約 {chars} 字\n補充要求:{notes or '無'}\n\n"
              "規則:只輸出要念出來的文字,不要標題、不要 Markdown、不要 [音樂] 之類的舞台指示;"
              "句子短、口語化,適合用耳朵聽;開頭先打招呼並點出主題,結尾簡短收尾。")
    draft = tai.chat("reason", prompt, system=f"你是台灣的 podcast 編劇。{tai.TW}", think=False)
    polished = tai.chat("taiwan", "請把以下口播稿潤飾成自然的台灣口語,保留內容與長度,只輸出改寫結果:\n\n" + draft,
                        system=tai.TW)
    return tai.to_tw(polished)


def revise_script(script, instruction):
    if not script.strip() or not instruction.strip():
        raise gr.Error("需要文案和修改指示")
    out = tai.chat("reason", f"依照指示修改這篇口播稿,只輸出修改後的全文。\n\n指示:{instruction}\n\n原稿:\n{script}",
                   system=f"你是台灣的 podcast 編輯。{tai.TW}", think=False)
    return tai.to_tw(out)


def make_voice(script, progress=gr.Progress()):
    if not script.strip():
        raise gr.Error("文案是空的")
    secs = len(script) / 250 * 60
    progress(0, desc=f"合成中,預估音檔約 {secs:.0f} 秒,CPU 大約要 {secs * 9 / 60:.0f} 分鐘…")
    return str(tai.speak(script))


def make_cover(title, script, extra):
    if not title.strip():
        raise gr.Error("請輸入節目標題(會印在封面上)")
    prompt = tai.chat("reason", f"節目標題:{title}\n內容摘要:{script[:600]}\n額外要求:{extra or '無'}", think=False, system=(
        "Write one English prompt for a wide 16:9 podcast cover background image that fits this episode. "
        "Bold, clean composition, strong central visual, keep the bottom quarter simple for a title. "
        "No text, no letters, no words in the image. Output only the prompt."))
    return str(media.make_cover(prompt, title)), prompt


def make_video(cover, audio, script, burn_subs):
    if not cover or not audio:
        raise gr.Error("需要先產生封面和語音")
    path = media.make_video(cover, audio, script if burn_subs else "")
    srt = path.with_suffix(".srt")
    return str(path), str(srt) if srt.exists() else None


with gr.Blocks(title="Podcast 工作室") as app:
    gr.Markdown("# Podcast 工作室\n文案 → 修改 → 語音 → 封面 → 影片。全部在本機執行。")

    with gr.Tab("1. 文案"):
        with gr.Row():
            topic = gr.Textbox(label="主題", placeholder="例如:台灣夜市文化的演變", scale=3)
            style = gr.Dropdown(STYLES, value=STYLES[0], label="風格")
            minutes = gr.Slider(0.5, 10, value=2, step=0.5, label="長度(分鐘)")
        notes = gr.Textbox(label="補充要求(可留空)", placeholder="例如:加入一個小故事、語氣幽默")
        gen_btn = gr.Button("產生文案", variant="primary")
        script = gr.Textbox(label="文案(可以直接修改)", lines=16, interactive=True)
        with gr.Row():
            instruction = gr.Textbox(label="請 AI 修改", placeholder="例如:開頭更吸引人、刪掉第三段", scale=4)
            rev_btn = gr.Button("套用修改")
        count = gr.Markdown()

    with gr.Tab("2. 語音"):
        gr.Markdown("用第 1 步的文案(含你手動修改的內容)合成台灣口音語音。**BreezyVoice 跑在 CPU,1 分鐘音檔約要 9 分鐘。**")
        voice_btn = gr.Button("產生語音", variant="primary")
        audio = gr.Audio(label="語音", type="filepath")

    with gr.Tab("3. 封面"):
        title = gr.Textbox(label="節目標題(印在封面上)")
        extra = gr.Textbox(label="畫面要求(可留空)", placeholder="例如:插畫風、夜景、暖色調")
        cover_btn = gr.Button("產生封面(16:9)", variant="primary")
        cover = gr.Image(label="封面", type="filepath")
        cover_prompt = gr.Textbox(label="實際使用的英文 prompt", interactive=False)

    with gr.Tab("4. 影片"):
        subs = gr.Checkbox(value=True, label="加入字幕(依文案自動切句,另存 .srt)")
        video_btn = gr.Button("合成影片", variant="primary")
        video = gr.Video(label="影片")
        srt_file = gr.File(label="字幕檔 .srt")

    gen_btn.click(write_script, [topic, style, minutes, notes], script)
    rev_btn.click(revise_script, [script, instruction], script)
    script.change(lambda s: f"目前 {len(s)} 字,約 {len(s) / 250:.1f} 分鐘", script, count)
    topic.change(lambda t: t, topic, title)
    voice_btn.click(make_voice, script, audio)
    cover_btn.click(make_cover, [title, script, extra], [cover, cover_prompt])
    video_btn.click(make_video, [cover, audio, script, subs], [video, srt_file])

if __name__ == "__main__":
    tai.OUT.mkdir(exist_ok=True)
    app.queue().launch(server_name="127.0.0.1", server_port=7860, allowed_paths=[str(tai.OUT)])
