# 資料結構

> 導入反推(2026-10-22,基準點 `7da165b6048897abb8acac4bba69db9677827471`)。來源等級見 `docs/ai-guideline.md` 表頭。

〔觀察〕本專案**沒有資料庫、沒有持久化的結構化資料、沒有設定檔**。資料只有兩類:程式內常數(設定)與 `outputs/` 下的產出檔。文案、標題、prompt 只存在網頁 session 的記憶體裡,關閉即消失。

## 實體 / 資料表

### 模型設定(程式常數,`taiwan_ai.py:40-52`)〔觀察〕

| 欄位 | 型別 | 約束 | 說明 |
|------|------|------|------|
| `MODELS["reason"]` | str | Ollama 模型名 | `hf.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M`;MCP `heavy` 時暫時替換 |
| `MODELS["taiwan"]` | str | Ollama 模型名 | `hf.co/mradermacher/Llama-Breeze2-8B-Instruct-text-only-GGUF:Q4_K_M` |
| `MODELS["code"]` | str | Ollama 模型名 | `qwen2.5-coder:7b`;只給 CLI 用 |
| `HEAVY_REASON` | str | Ollama 模型名 | `hf.co/HauhauCS/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF:IQ3_M` |
| `FLUX` | str | mflux 模型 | `mflux-community/flux-1-schnell-mflux-q4` |
| `MFLUX` | Path | 必須存在才可生圖 | `~/.local/bin/mflux-generate` |
| `BREEZY` / `VOICE_WAV` / `VOICE_TEXT` | Path / Path / str | 樣本音檔與逐字稿要一致 | 官方範例女聲 |
| `OLLAMA` | str | | `http://localhost:11434` |

### 產出檔(`outputs/`,不入 git)〔觀察〕

| 檔名樣式 | 產生者 | 格式 |
|----------|--------|------|
| `speech-<stamp>.wav` | `taiwan_ai.speak` | WAV(BreezyVoice 輸出) |
| `image-<stamp>.png` | `media.generate_image`、`taiwan_ai.image` | PNG,FLUX 原尺寸 |
| `cover-<stamp>.png` | `media.make_cover` | PNG 1920×1080 |
| `podcast-<stamp>.mp4` | `media.make_video` | H.264 yuv420p 2 fps + AAC 192k(+ mov_text 字幕軌,language=chi) |
| `podcast-<stamp>.srt` | `media.make_srt` | SRT UTF-8 |

`<stamp>` = `YYYYmmdd-HHMMSS`(本機時間,秒級)。

### 導入時已存在的產出檔(不入 git,僅記錄)〔觀察〕
`outputs/test_tts.wav`、`outputs/anime_pinup.png`、`outputs/test_flux.png`——檔名不符上表樣式,〔推論〕為手動測試留下。

## 關聯

〔觀察〕檔案之間沒有記錄關聯;同一集的 cover / speech / podcast 由使用者在網頁中手動串起,時間戳各自獨立。`podcast-<stamp>.mp4` 與 `.srt` 共用同一個 stamp。

## 索引 / 約束

- 〔觀察〕唯一性只靠秒級時間戳,同秒同前綴會覆寫(ADP-8)。
- 〔觀察〕沒有刪除策略,`outputs/` 會無限增長。〔未知〕是否需要清理策略。
- 〔觀察〕FLUX 寬高須為 16 倍數(MCP 有檢查;`media.generate_image` 本身沒檢查)。

## 狀態 / 列舉

| 列舉 | 值 | 等級 |
|------|----|------|
| 文案風格 `STYLES` | 輕鬆聊天、知識科普、新聞評論、故事敘述、訪談開場 | 〔觀察〕`podcast_studio.py:19` |
| 長度 | 0.5–10 分鐘,步進 0.5;字數 = 分鐘 × 250 | 〔觀察〕`podcast_studio.py:25,77` |
| 模型角色 | reason、taiwan、code(+ reason_heavy、image、speech 於 `list_models`) | 〔觀察〕 |
| CLI 模式 | ask、code、image、speak、auto | 〔觀察〕 |

網頁四步沒有狀態機:每一步讀目前元件值,〔觀察〕可任意順序重跑。
