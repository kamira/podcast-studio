# Podcast 工作室 — 設計文件

> **現行規格以 `docs/` 為準**(需求見 `docs/ai-guideline.md`,結構見 `docs/structure/`,變更見 `docs/changes/`)。本文件是導入治理前的人寫設計文件,保留作背景參考;CHG-20261022-01 起網頁改為 `studio_server.py`(FastAPI + 原生 JS,節點畫布),Gradio 版 `podcast_studio.py` 與 `taiwan_ai.py` 的 CLI 已移除,§2~§4、§9、§11 中與舊網頁有關的內容已過時。

> 狀態(2026-10-22):規格已確認(§8、§10.5)。**MCP 伺服器已實作並測試**;Podcast 工作室已寫程式但**暫停,尚未端到端測試**。詳見 §11。

## 1. 目標

在本機(Apple M5 Pro / 24 GB)以網頁介面完成一集 podcast 影片:

1. 依需求產生口播文案
2. 使用者可直接編輯文案,或下指令請 AI 修改
3. 依最終文案產生台灣口音語音
4. 產生 podcast 靜態封面(含中文標題)
5. 封面 + 語音合成 MP4 影片

全程離線執行,不送出任何資料到雲端服務。

## 2. 使用流程

| 步驟 | 使用者操作 | 系統動作 | 產出 |
|---|---|---|---|
| 1. 文案 | 輸入主題、風格、長度、補充要求 | 推理模型寫初稿 → Breeze2 潤飾台灣口語 → OpenCC 轉台灣正體 | 可編輯文字框 |
| 1a. 修改 | 手動編輯,或輸入修改指示 | 推理模型依指示改寫全文 | 更新後文案 |
| 2. 語音 | 按「產生語音」 | 清除 Markdown 符號 → BreezyVoice 合成 | `outputs/speech-*.wav` |
| 3. 封面 | 輸入節目標題、畫面要求 | 推理模型寫英文 prompt → 卸載 LLM → FLUX 生圖 → 疊中文標題 | `outputs/cover-*.png` |
| 4. 影片 | 按「合成影片」 | ffmpeg 靜態圖 + 音訊 → H.264/AAC | `outputs/podcast-*.mp4` |

步驟 2 與 3 互不依賴,可任意順序執行。

## 3. 架構

> 下圖為 Gradio 版(已移除);現行架構見 `docs/structure/logical.md`。

```
podcast_studio.py (Gradio 網頁, :7860)
  │
  ├─ import taiwan_ai.py ── 共用:模型設定、chat()、speak()、unload_llms()、to_tw()
  │     │
  │     ├─ Ollama API (localhost:11434)
  │     │     ├─ reason : Qwen3.5-9B Uncensored HauhauCS Aggressive
  │     │     └─ taiwan : Llama-Breeze2-8B
  │     │
  │     └─ BreezyVoice (tools/BreezyVoice/.venv, 子程序)
  │
  ├─ mflux-generate (FLUX.1-schnell 4-bit, 子程序)
  ├─ Pillow + STHeiti Medium 字型 (疊標題)
  └─ imageio-ffmpeg 內附 ffmpeg (合成影片)
```

## 4. 檔案

| 檔案 | 說明 |
|---|---|
| ~~`podcast_studio.py`~~ | 網頁介面與流程(已刪除,CHG-20261022-01;改為 `studio_server.py`,見 `docs/structure/directory.md`) |
| `media.py` | 封面標題、字幕、影片合成(網頁與 MCP 共用) |
| `local_models_mcp.py` | MCP 伺服器 |
| `patches/breezyvoice-mac/`、`setup_breezyvoice.sh` | BreezyVoice Mac 調整與重建腳本 |
| `taiwan_ai.py` | 既有多模型串接 CLI,被 import 共用 |
| `.claude/launch.json` | 讓 Claude 桌面 app 以 `uv run` 啟動網頁 |
| `tools/BreezyVoice/` | 第三方 repo(含本機修改,見 §7),**建議不納入 git**,改以安裝說明重建 |
| `outputs/` | 產出檔案,**建議加入 `.gitignore`** |

## 5. 設計決策

| 決策 | 理由 |
|---|---|
| Gradio 網頁而非 CLI | 文案需要反覆編輯,文字框比終端機好用 |
| 文案用 `think=False` | 寫作不需長推理;開思考會吃掉 context 且慢 3–5 倍 |
| 每分鐘 250 字估算長度 | 台灣華語播報常見語速 |
| 標題用字型疊上,不讓 FLUX 畫 | FLUX 無法正確產生中文字 |
| 生圖前卸載 LLM | FLUX 峰值約 14–16 GB,與 LLM 同時載入會超出 GPU 可用記憶體 |
| 影片 2 fps、`-tune stillimage` | 靜態畫面,降低檔案大小與編碼時間 |
| imageio-ffmpeg 而非系統 ffmpeg | 系統未安裝 Homebrew,免額外安裝 |

## 6. 已知限制

| 項目 | 限制 | 可能改善 |
|---|---|---|
| 語音速度 | BreezyVoice 跑 CPU,1 分鐘音檔約 9 分鐘 | 測試改用 MPS(Apple GPU);或長文分段平行 |
| 語音聲線 | 只有官方範例女聲 | 錄一段 10–20 秒 wav + 逐字稿即可換聲 |
| 數字念法 | 用自製簡易正規化(pynini 無 macOS ARM wheel) | 遇到念錯可在文案用注音標註,如 `好[:ㄏㄠ3]` |
| 單人主持 | 目前只支援一個聲音 | 雙人對談:文案標記角色 → 兩個聲線分別合成 → 串接 |
| 封面 | FLUX 對成人/特定人物內容訓練資料有限 | 需要特定風格時加 LoRA |

> 註(2026-10-28,CHG-20261022-04):「數字念法」可在網頁的專案編輯器以注音聲調標註逐字修正;時間軸匯出的字幕改依實際片段長度計算(MCP `make_video` 仍依字數估時)。現況與已知缺陷以治理帳本為準:`docs/ai-guideline.md` §6、§9 與 `docs/structure/*.md`。

## 7. 環境依賴(重建用)

```bash
# Ollama 模型
ollama pull hf.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M
ollama pull hf.co/mradermacher/Llama-Breeze2-8B-Instruct-text-only-GGUF:Q4_K_M

# 生圖
uv tool install --python 3.13 mflux --with 'pillow<13'

# BreezyVoice(Mac 調整版)
./setup_breezyvoice.sh
#  - 依 patches/breezyvoice-mac/requirements-mac.txt 安裝(移除 CUDA / Linux 專用套件)
#  - 把 patches/breezyvoice-mac/tn/ 放進 venv,取代 WeTextProcessing
```

## 8. 已確認決策

| # | 項目 | 決定 |
|---|---|---|
| 1 | 主持形式 | 單人主持 |
| 2 | 影片規格 | 16:9,1920×1080(FLUX 生 1360×768 後縮放裁切) |
| 3 | 字幕 | 自動產生 SRT:依標點切句、依字數比例分配時間;MP4 內嵌軟字幕 + 另存 `.srt` |
| 4 | BreezyVoice 修改 | 以 `patches/breezyvoice-mac/` + `setup_breezyvoice.sh` 納入 git,`tools/` 不納入 |

## 9. 啟動方式

```bash
uv run --script studio_server.py
# 開啟 http://127.0.0.1:7860(只綁本機;設定由網頁右上角 ⚙ 開啟,存在 config/studio.db)
```

## 10. MCP 伺服器

把目前所有本機模型包成一個 MCP 伺服器,讓 Claude Code、Codex 等 agent 直接呼叫。

### 10.1 架構

```
Claude Code / Codex / 其他 MCP client
        │  stdio
        ▼
local_models_mcp.py  (Python, 官方 mcp SDK 2.x 的 MCPServer)
        │  import taiwan_ai.py(與 CLI、網頁共用同一套設定)
        ├─ Ollama API ── reason / taiwan / heavy
        ├─ mflux ─────── FLUX.1-schnell
        ├─ BreezyVoice ─ 台灣華語 TTS
        └─ ffmpeg ────── 靜態圖 + 音訊 → MP4
```

### 10.2 工具清單

| 工具 | 參數 | 回傳 | 對應模型 |
|---|---|---|---|
| `reason` | `prompt`, `think=true`, `heavy=false` | 文字 | Qwen3.5-9B Aggressive / `heavy` 時 Qwen3.8-27B |
| `taiwanize` | `text` | 台灣正體口語文字 | Breeze2 + OpenCC |
| `generate_image` | `prompt`, `width=1024`, `height=1024` | 圖片路徑 | FLUX.1-schnell |
| `speak` | `text` | wav 路徑 | BreezyVoice |
| `make_video` | `image_path`, `audio_path`, `script=""` | mp4 路徑(有 script 時附字幕與 .srt) | ffmpeg |
| `list_models` | — | 各角色模型、是否已下載、目前載入記憶體的模型 | — |

### 10.3 註冊方式

> 已註冊(2026-10-22),repo 位置 `/Users/weiss/Documents/github/podcast-studio`。`uv` 用絕對路徑,因為 agent 啟動 MCP 時不一定會載入 `~/.zshrc` 的 PATH。

```bash
# Claude Code(user scope,所有專案可用)
claude mcp add --scope user local-models -- /Users/weiss/.local/bin/uv run --script /Users/weiss/Documents/github/podcast-studio/local_models_mcp.py
```

```json
// Claude Code 長時間工具逾時:~/.claude/settings.json
{ "env": { "MCP_TOOL_TIMEOUT": "1800000" } }
```

```toml
# Codex:~/.codex/config.toml
[mcp_servers.local-models]
command = "/Users/weiss/.local/bin/uv"
args = ["run", "--script", "/Users/weiss/Documents/github/podcast-studio/local_models_mcp.py"]
tool_timeout_sec = 1800
```

> **網頁操作(CHG-20261022-02,FR-22)**:也可以在網頁設定頁「MCP 註冊」子頁(首頁右上角 ⚙ → 設定 › MCP 註冊,`#/settings/mcp`)查詢、註冊、移除 local-models。網頁一律呼叫上面同樣的官方 CLI(`~/.local/bin/claude mcp add/remove -s user`、`~/.local/bin/codex mcp add/remove`),確認框列出完整指令,按「確認執行」後才執行;開頁不啟動 MCP 伺服器,按「重新檢查」才會由 `claude mcp get` 啟動一次做健康檢查。

> **Codex 的 `tool_timeout_sec` 需手動補**:`codex mcp add` 沒有設定 `tool_timeout_sec` 的參數,從網頁(或 CLI)移除再註冊後這一行會消失,長工具(`speak`、`make_video`、`reason(heavy)`)可能逾時。網頁的移除確認框會提醒;註冊後在 `~/.codex/config.toml` 的 `[mcp_servers.local-models]` 段落加回下面這行,再以 `codex mcp get local-models --json` 確認 `tool_timeout_sec` 為 1800:
>
> ```toml
> [mcp_servers.local-models]
> tool_timeout_sec = 1800
> ```

> **`MCP_TOOL_TIMEOUT` 不由網頁寫入**:Claude Code 的 `MCP_TOOL_TIMEOUT`(`~/.claude/settings.json` 的 `env`)網頁只讀取並提醒(建議值 `1800000`),不會修改 `~/.claude/settings.json`;沒設定時請依上面的 JSON 片段自行加入。

### 10.4 設計重點

| 項目 | 處理方式 |
|---|---|
| 長時間工具 | `speak` 可能跑數分鐘、`reason(heavy)` 可能十幾分鐘;Codex 設 `tool_timeout_sec`,Claude Code 設 `MCP_TOOL_TIMEOUT` |
| 記憶體 | 伺服器內用一把鎖讓工具依序執行;`generate_image` 前自動卸載 LLM |
| 輸出位置 | 圖片 / 音訊 / 影片寫入 `outputs/`,回傳絕對路徑 |
| 安全 | 只走 stdio、只監聽本機;不開對外網路埠 |

### 10.5 已確認決策

| # | 項目 | 決定 |
|---|---|---|
| 5 | Claude Code 註冊範圍 | user scope(所有專案) |
| 6 | Claude 桌面 app 聊天 | 暫不註冊 |
| 7 | `write_code` | 移除,程式需求由 `reason` 兼任 |

## 11. 實作狀態

| 項目 | 狀態 | 驗證方式 / 備註 |
|---|---|---|
| MCP 伺服器 `local_models_mcp.py` | ✅ 完成 | stdio client 實測:列出 6 個工具;`list_models` 正確;`taiwanize`「软件/网络/视频」→「軟體/網路/影片」;`reason` 17×23=391 |
| MCP `generate_image` / `speak` / `make_video` | ⚠️ 未經 MCP 實測 | 底層函式曾在 CLI 測過(生圖 21 秒、語音 7 秒音檔約 1 分鐘);`make_video` 與字幕尚未執行過 |
| MCP 註冊(Claude Code / Codex) | ✅ 完成 | `claude mcp list` 顯示 ✔ Connected;`codex mcp list` 顯示 enabled(Codex 尚未登入,未實際呼叫) |
| Podcast 工作室 `podcast_studio.py` | 已刪除 | CHG-20261022-01 改為 `studio_server.py`;驗收狀態以 `docs/acceptance/` 為準 |
| `media.py`(封面、字幕、影片) | ⏸ 暫停 | 程式已寫,未執行 |
| `patches/breezyvoice-mac/` + `setup_breezyvoice.sh` | ✅ 完成 | 在新 repo 位置實際重跑成功(venv 未隨搬移複製,已重建) |
| `.gitignore` | ✅ | 排除 `outputs/`、`tools/`、`__pycache__/`、`.venv/` |

### 11.1 實作中遇到的問題

| 問題 | 原因 | 處理 |
|---|---|---|
| MCP 啟動即斷線 | mcp 2.x 將 `FastMCP` 改名為 `MCPServer` | 改用 `mcp.server.mcpserver.MCPServer`,依賴改為 `mcp>=2.3` |
| 搬移後 `setup_breezyvoice.sh` 會重複 clone | 以 `.git` 判斷是否已 clone,但 `.git` 未隨搬移複製 | 改以 `single_inference.py` 判斷 |
| stdout 汙染 MCP 協定 | `taiwan_ai.speak()` 等函式用 `print` 輸出路徑 | 工具執行期間 `redirect_stdout` 到 stderr |
