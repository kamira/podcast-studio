# 邏輯結構

> 導入反推(2026-10-22,基準點 `7da165b6048897abb8acac4bba69db9677827471`)。來源等級:〔觀察〕/〔推論〕/〔未知〕,定義見 `docs/ai-guideline.md` 表頭。
> 本文件描述**現況**,不代表現況正確;已知缺陷見 Guideline §9(ADP-n)。

## 分層 / 模組

| 層/模組 | 職責 | 依賴 | 回應需求 | 等級 |
|---------|------|------|----------|------|
| 入口層:網頁 `podcast_studio.py` | Gradio UI、四步流程、輸入檢查、prompt 組裝 | `media`、`taiwan_ai`、gradio | FR-1~7 | 〔觀察〕 |
| 入口層:MCP `local_models_mcp.py` | 把模型包成 6 個 stdio 工具;全域鎖;stdout 導向 stderr | `media`、`taiwan_ai`、mcp SDK、Ollama `/api/tags` `/api/ps` | FR-8~13 | 〔觀察〕 |
| 入口層:CLI `taiwan_ai.main()` | 命令列 `ask/code/image/speak/auto` | `taiwan_ai` 本身 | FR-14 | 〔觀察〕 |
| 媒體層 `media.py` | 生圖、16:9 裁切、疊標題、切句、SRT、ffmpeg 合成 | `taiwan_ai`(`OUT`、`stamp`、`unload_llms`、`MFLUX`、`FLUX`)、Pillow、imageio-ffmpeg | FR-4~6、10、12 | 〔觀察〕 |
| 模型層 `taiwan_ai.py` | 模型名稱設定、`chat()`、`unload_llms()`、`speak()`、`to_tw()`、輸出目錄與時間戳 | Ollama HTTP、BreezyVoice 子程序、mflux 子程序、OpenCC | FR-1~3、8、9、11 | 〔觀察〕 |
| 外部引擎 | Ollama(`localhost:11434`)、`mflux-generate`、`tools/BreezyVoice/.venv`、內附 ffmpeg | — | — | 〔觀察〕皆不在 repo 內 |
| 建置 `setup_breezyvoice.sh` + `patches/` | 重建 BreezyVoice macOS 環境 | GitHub、uv | FR-15 | 〔觀察〕 |

## 架構圖

```mermaid
flowchart TD
  subgraph 入口層
    WEB["podcast_studio.py<br/>Gradio :7860"]
    MCP["local_models_mcp.py<br/>MCP stdio(全域鎖)"]
    CLI["taiwan_ai.py main()<br/>CLI"]
  end
  subgraph 媒體層
    MEDIA["media.py<br/>封面 / 字幕 / 影片"]
  end
  subgraph 模型層
    TAI["taiwan_ai.py<br/>MODELS / chat / speak / unload_llms / to_tw"]
  end
  subgraph 外部引擎["外部引擎(repo 外)"]
    OLLAMA[("Ollama :11434<br/>reason / taiwan / code / heavy")]
    FLUX[["mflux-generate<br/>FLUX.1-schnell"]]
    BREEZY[["BreezyVoice<br/>tools/BreezyVoice/.venv"]]
    FFMPEG[["imageio-ffmpeg"]]
  end
  OUT[/"outputs/"/]

  WEB --> MEDIA
  WEB --> TAI
  MCP --> MEDIA
  MCP --> TAI
  MCP -. list_models .-> OLLAMA
  CLI --> TAI
  MEDIA --> TAI
  MEDIA --> FLUX
  MEDIA --> FFMPEG
  TAI --> OLLAMA
  TAI --> BREEZY
  TAI --> FLUX
  MEDIA --> OUT
  TAI --> OUT
  SETUP["setup_breezyvoice.sh<br/>+ patches/breezyvoice-mac"] -. 建置期 .-> BREEZY
```

〔觀察〕`taiwan_ai.image()`(CLI)與 `media.generate_image()` 各自組一次 mflux 指令——同一功能兩份實作(見設計結構「重複」)。

## 主要流程

### 流程 A:網頁產出一集(FR-1~7)

```mermaid
sequenceDiagram
  actor U as 使用者
  participant W as podcast_studio
  participant T as taiwan_ai
  participant M as media
  participant O as Ollama
  participant B as BreezyVoice
  participant F as mflux
  participant X as ffmpeg

  U->>W: 主題/風格/長度 → 產生文案
  W->>T: chat("reason", think=False)
  T->>O: /api/chat(Qwen3.5-9B)
  W->>T: chat("taiwan") → to_tw()
  T->>O: /api/chat(Breeze2)
  W-->>U: 文案(可編輯 / 請 AI 修改)
  U->>W: 產生語音
  W->>T: speak(script)
  T->>B: 子程序 single_inference.py
  W-->>U: speech-*.wav
  U->>W: 產生封面(標題)
  W->>T: chat("reason") 寫英文 prompt
  W->>M: make_cover(prompt, title)
  M->>T: unload_llms()
  M->>F: 1360×768
  M-->>W: cover-*.png(1920×1080 + 標題)
  U->>W: 合成影片
  W->>M: make_video(cover, wav, script)
  M->>X: 靜態圖 + 音訊 + SRT
  W-->>U: podcast-*.mp4 + .srt
```

- 〔觀察〕語音與封面兩步沒有相依,可任意順序(FR-7)。
- 〔觀察〕錯誤路徑:輸入空白 → `gr.Error`;子程序失敗 → `CalledProcessError` 由 Gradio 顯示;stdout/stderr 被丟到 DEVNULL,失敗原因看不到。
- 〔觀察〕網頁沒有跨事件鎖,語音與封面可能同時跑(ADP-2)。

### 流程 B:MCP 工具呼叫(FR-8~13)

```mermaid
flowchart LR
  C["MCP client<br/>Claude Code / Codex"] -- stdio JSON-RPC --> S["local_models_mcp"]
  S --> L{"busy():取得 LOCK<br/>stdout → stderr"}
  L --> R["reason / taiwanize → chat()"]
  L --> G["generate_image → unload_llms() → mflux"]
  L --> SP["speak → BreezyVoice"]
  L --> V["make_video → ffmpeg(+ SRT)"]
  S -- 不經鎖 --> LM["list_models → /api/tags, /api/ps"]
  R & G & SP & V --> RET["回傳文字或 outputs/ 絕對路徑"]
```

- 〔觀察〕`list_models` 不取鎖(唯讀 HTTP)。
- 〔觀察〕`make_video` 在取鎖前檢查兩個路徑存在;`generate_image` 在取鎖前檢查 16 倍數。
- 〔觀察〕`reason(heavy=true)` 暫時替換 `MODELS["reason"]`,結束後還原 → heavy 模型不在卸載清單(ADP-1)。

## 相依方向

〔觀察〕單向、無循環:入口層 → 媒體層 → 模型層 → 外部引擎;入口層也直接用模型層。`media` 依賴 `taiwan_ai`,`taiwan_ai` 不依賴 `media`。

〔觀察〕耦合點:`media.py` 直接讀 `taiwan_ai` 的模組層常數(`OUT`、`MFLUX`、`FLUX`)與函式;MCP 的 `reason` 以**改寫模組全域變數** `tai.MODELS` 切換模型——靠全域鎖才安全,網頁行程裡沒有這把鎖(目前網頁不切 heavy,〔推論〕暫無實害)。

## 開發流程(CI/CD)

> 2026-10-23 依 CHG-20261022-03 新增。本節描述開發流程(repo 的檢查與部署),不是產品執行期的模組;產品「不送資料出本機」的要求不因本節改變,開發期送往 Codex 的審議資料屬 DIR-1 第 9 條的使用者明示例外。

### 架構圖

```mermaid
flowchart LR
  subgraph Mac["這台 Mac(本機)"]
    DISP["主 agent(派發)"]
    LR["scripts/local_review.py<br/>DIR-1 六席 · Ollama<br/>(27B 暫停)"]
    DEP["scripts/deploy_local.sh"]
    APP["網頁伺服器 :7860<br/>(只綁 127.0.0.1)"]
  end
  CODEX[["Codex(雲端,read-only)<br/>risk(高風險)/ security / compliance 席"]]
  subgraph GH["GitHub kamira/podcast-studio(public)"]
    PR["Pull Request"]
    CMT["PR 留言<br/>local-review 判決 / infra-approved"]
    subgraph CI[".github/workflows/ci.yml(代管 ubuntu)"]
      J1["document<br/>doc_integrity_check · secret_scan"]
      J2["chg<br/>commit 引用 + plan-check"]
      J3["program<br/>py_compile · ruff E9,F · unittest"]
      J4["review<br/>驗 local-review 留言"]
    end
    MAIN["main(ruleset:四項 check 必過<br/>只經 PR、只允許 squash)"]
  end
  VEN[".ai-sdlc/(vendored v1.66.0)"]
  DISP -- push 分支 --> PR
  DISP --> LR -- gh pr comment --> CMT
  LR -. 送審資料 .-> CODEX
  PR --> J1 & J2 & J3 & J4
  J1 & J2 --> VEN
  J4 --> CMT
  J1 & J2 & J3 & J4 -- 全綠 + 使用者核准 --> MAIN
  MAIN -- merge 後 --> DEP --> APP
```

- 〔觀察〕四個 job 都先把 PR head 取到 `pr/`、base 取到 `trusted/`,治理檢查一律執行 `trusted/` 的腳本,PR 內容只當資料讀取(`.github/workflows/ci.yml` 檔頭註解)。
- 〔觀察〕main 的 ruleset 要求 `document`、`chg`、`program`、`review` 四項通過,禁止直接 push 與 force push,合併只允許 squash(CHG-20261022-03 派發記錄 T7)。
- 〔觀察〕DIR-1 第 9 條:security、compliance 席由 Codex 擔任;第 10 條暫停 27B,高風險 CHG 的 risk 席改由 Codex 擔任。`scripts/local_review.py` 的預設值仍含 27B,暫停期間須帶 `--codex-seats risk,security,compliance`。
- 〔觀察〕`program` job 在 `scripts/check.sh` 不存在時跳過該步(該檔由 CHG-20261022-01 T1 建立)。

### 一個 PR 的流程

1. **開 PR**:每個步驟開自己的分支(`chg/<CHG 編號>-<task>` 或 `docs/<主題>`),commit message 帶 CHG 編號(只改 `docs/knowledge/` 者除外),push 後開 PR(DIR-2 第 1 條)。
2. **本機審議**:CHG 風險為中 / 高,或 PR 改到程式路徑時,主 agent 從 main 的工作副本執行 `scripts/local_review.py`,六席兩階段審議後以 `gh pr comment` 貼出判決,留言帶 `<!-- local-review CHG-… sha=<head sha> -->` 標記。只處理本 repo 分支且作者為擁有者的 PR。
3. **CI 四項**:`document`(doc_integrity_check、全 repo 與 commit 範圍秘密掃描、vendored 完整性)、`chg`(commit 引用 CHG 編號、PR 改到的 CHG 做 plan-check)、`program`(`py_compile`、`ruff check --select E9,F`、`python3 -m unittest discover -s tests/ci`)、`review`(只採信擁有者留言;標記 sha 須為目前 head;否決席無 fail、每席都有回答;改到治理基礎設施路徑另需 `<!-- infra-approved sha=<head> -->`)。
4. **使用者核准**:CI 全綠後,只改 `docs/`、`features/` 的 PR 可自動 merge;改到任何程式路徑的 PR 不論分級,一律停下等使用者核准(DIR-2 第 3 條)。審議留言只是完整性檢查,授權只有使用者核准(CHG-20261022-03 安全影響 (n))。
5. **squash merge**:main 只接受 squash merge,標題帶 CHG 編號。
6. **本機部署**:有程式變更時,在乾淨的 main 上執行 `scripts/deploy_local.sh`:`git pull --ff-only` → 跑 `scripts/check.sh`(存在時)→ 重啟 7860 網頁伺服器 → 驗證本專案行程的全部 LISTEN socket 只綁 `127.0.0.1` / `[::1]`,否則停止伺服器並 exit 1。MCP 伺服器由 client 啟動,腳本只印出重新啟動提醒。
