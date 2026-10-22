# AGENTS.md — podcast-studio

本 repo 受 AI-SDLC 治理(2026-10-22 以 adopt 方式導入)。任何 agent 進場先做以下事,再動手。

## 進場順序

1. `docs/ai-guideline.md` — 需求、範圍、AI 開發約定(§8)、**導入盤點待補項(§9)**
2. `docs/structure/{logical,design,data,directory}.md` — 現況結構(反推,標有〔觀察〕/〔推論〕/〔未知〕)
3. `docs/knowledge/INDEX.md` — 使用者教過、不該再犯的規則
4. `docs/changes/` 最新的 CHG — 進行中的變更
5. 若 `docs/.onboarding` 存在:導入尚未完成,**不得進入變更流程**,先依其記錄的步驟續做

## 規則

- 帳本落點:`docs/`(宣告於 Guideline 表頭,不要重新推論)。
- **任何修改先開 CHG**(`docs/changes/CHG-YYYYMMDD-NN.md`),再改程式。
- 不補造治理基準點(Governance-baseline)以前的 CHG。
- Guideline §9 的 ADP-n 待補項不得順手修;要修就開 CHG 並引用編號。
- 「已盤點」不等於「已驗收」:驗收結果只看 `docs/acceptance/` 的 ACC。
- 使用者面文字、註解、文件一律繁體中文與台灣用語。
- 不送資料出本機;新增網路呼叫屬高風險變更。

## 執行

```bash
uv run --script podcast_studio.py      # 網頁 http://127.0.0.1:7860
uv run --script local_models_mcp.py    # MCP 伺服器(stdio)
./setup_breezyvoice.sh                 # 重建 tools/BreezyVoice
```

外部依賴(不在 repo 內):Ollama(`localhost:11434`)、`~/.local/bin/mflux-generate`、`tools/BreezyVoice/.venv`。安裝方式見 `PODCAST_STUDIO.md §7`。
