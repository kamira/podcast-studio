# 資料結構

> 導入反推(2026-10-22,基準點 `7da165b6048897abb8acac4bba69db9677827471`)。來源等級見 `docs/ai-guideline.md` 表頭。
> 2026-10-26 依 CHG-20261022-01 結構文件更新:新增設定資料庫 `config/studio.db`(取代導入時「沒有設定檔」的描述與修訂 2 前規劃的 `config/models.json` schema)、工作流程 JSON、`outputs/uploads/`;輸出檔時間戳改為毫秒 + 隨機碼(以 T1~T13 合併後的 main 為準)。
> 2026-10-26 依 CHG-20261026-01 結構文件更新:`image`、`voice` 角色接上 `media.generate_image()`、`taiwan_ai.speak()`;移除 `FLUX`、`VOICE_WAV`、`VOICE_TEXT` 常數;`workflows/` 只追蹤 `default.json`;`wav_seconds()` 改為自寫 RIFF/WAVE 解析;影片 `-r 10`、WAV 加 `-t`(以 T1~T5 合併後的 main 為準)。

〔觀察〕持久化資料有四類:設定資料庫 `config/studio.db`(使用者資料,不入 git)、工作流程 JSON(`workflows/`)、`outputs/` 下的產出檔與上傳檔、程式內常數。流程執行中的文字(文案、標題、prompt)只存在伺服器記憶體中的執行事件(最近 20 次 run)與瀏覽器畫布上,另存工作流程時隨節點參數或釘住的編輯值寫入 JSON。

## 實體 / 資料表

### 設定資料庫 `config/studio.db`(SQLite,schema v1)〔觀察〕`settings.py`

| 資料表 | 欄位 | 約束 | 說明 |
|--------|------|------|------|
| `meta` | `key TEXT PRIMARY KEY`、`value TEXT NOT NULL` | — | `schema_version`(目前 `"1"`)、`imported_from`(`config/models.json`,只在一次性匯入時寫)、`imported_at`(UTC 時間) |
| `roles` | `role TEXT PRIMARY KEY`、`config TEXT NOT NULL` | `config` 為該角色鍵值的 JSON | 角色與允許的鍵見下表 |
| `review_seats` | `seat TEXT PRIMARY KEY`、`model TEXT NOT NULL` | 六席:risk、impact、drift、compliance、security、consistency | Ollama 模型名稱或 `codex` |

`roles.config` 的鍵(`settings._ROLE_KEYS`)與預設值(`settings.DEFAULTS`):

| 角色 | 鍵 | 預設值 / 約束 |
|------|----|---------------|
| `reason` | `model`、`think`、`num_ctx` | `hf.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M`、`true`、16384(`num_ctx` 2048–65536) |
| `reason_heavy` | `model`、`think`、`num_ctx` | `hf.co/HauhauCS/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF:IQ3_M`、`true`、16384 |
| `taiwan` | `model`、`think`、`num_ctx` | `hf.co/mradermacher/Llama-Breeze2-8B-Instruct-text-only-GGUF:Q4_K_M`、`null`、16384 |
| `image` | `model`、`steps` | `mflux-community/flux-1-schnell-mflux-q4`、4(`steps` 1–8);〔觀察〕`media.generate_image()` 每次呼叫經 `taiwan_ai.role_config("image")` 讀一次,`model`、`steps` 流入 mflux 的 `--model`、`--steps`(CHG-20261026-01 T2);網頁 `generate-image` 節點、MCP `generate_image` 共用;MCP `list_models` 的 `image` 列回報此 `model` |
| `voice` | `wav`、`text` | `tools/BreezyVoice/data/example.wav` 與官方範例逐字稿;`wav` 限 `tools/BreezyVoice/data/`、`outputs/uploads/` 之下;〔觀察〕`taiwan_ai.speak()` 每次呼叫經 `taiwan_ai.role_config("voice")` 讀一次,`wav`(以 repo 根目錄轉成絕對路徑)、`text` 流入 BreezyVoice 的 `--speaker_prompt_audio_path`、`--speaker_prompt_text_transcription`(CHG-20261026-01 T1);網頁 `speak` 節點、MCP `speak` 共用 |

`review_seats` 預設:risk = Qwen3.5-9B、impact = `glm4:latest`、drift = `qwen3:8b`、compliance = `codex`、security = `codex`、consistency = `glm4:latest`。否決席(risk、security、compliance)不得為 `glm4:*`(DIR-1);`codex` 只限否決席;暫停的 27B 不得用於審議席位(DIR-1 第 10 條)。

- 〔觀察〕檔案:`config/studio.db` 與 SQLite 的 `-wal`、`-shm`(WAL 模式),初始化鎖 `config/.studio.db.lock`;全部列入 `.gitignore`。資料庫檔權限 0600。
- 〔觀察〕**一次性匯入**:資料庫不存在、`config/models.json` 存在、路徑安全且內容通過 `validate(cfg, None)` 時,第一次 `load()` 在 `BEGIN IMMEDIATE` 交易內建表、寫入並記 `schema_version`、`imported_from`、`imported_at`;失敗即 rollback 且不留資料庫檔。之後只讀資料庫,程式不刪除也不再讀 JSON。`config/models.json` 已於 CHG-20261022-01 T3 移出 git(列入 `.gitignore`)。
- 〔觀察〕`schema_version` 不是程式所知的版本時,不讀不寫、回 `DEFAULTS` 並提示,不猜測轉換。
- 〔觀察〕讀出的設定一律再經 `validate(cfg, None)`,不合法整份回 `DEFAULTS`(帶 `_warnings`),資料庫不動。
- 〔觀察〕`image`、`voice` 的讀取端不快取設定:設定頁存檔後,下一次生圖或語音(網頁與 MCP 兩個行程)即使用新值;設定為 `DEFAULTS` 時子程序 argv 與 CHG-20261026-01 之前逐元素相同(`tests/test_media.py`、`tests/test_taiwan_ai.py`)。

### 工作流程 JSON(`workflows/<name>.json`)〔觀察〕`executor.py`、`studio_server.py`

```json
{
  "nodes": [
    {"id": "字串,不重複", "kind": "nodes.REGISTRY 的種類", "params": {"參數": "值"},
     "pinned": false, "outputs": {"埠": "釘住時的編輯值(只限文字)"}, "pos": {"x": 0, "y": 0}}
  ],
  "edges": [
    {"from": {"node": "節點 id", "port": "輸出埠"}, "to": {"node": "節點 id", "port": "輸入埠"}}
  ],
  "steps": [
    {"id": "步驟 id", "label": "步驟名稱", "nodes": ["節點 id"], "note": "選填"}
  ]
}
```

- 〔觀察〕`nodes`、`edges` 由 `executor.validate` 驗證(節點數 ≤ 100、文字參數 ≤ 20000 字);`pinned`、`outputs`、`pos` 選填;`pos` 是畫布座標,只供前端排版。
- 〔觀察〕`steps` 只出現在預設範本 `workflows/default.json`,定義步驟精靈的預設步驟:① 文稿(`topic`、`write-script`、`taiwanize`)② 語音(`speak`)③ 封面(`title`、`cover-prompt`、`generate-image`、`add-title`)④ 剪輯(沒有節點,時間軸屬 CHG-20261022-04)⑤ 匯出(`make-video`)。`executor.validate` 不讀 `steps`;前端畫布存檔目前不保留 `steps`。
- 〔觀察〕檔名即名稱:中英日白名單字元 1~64 字(見 `design.md`「HTTP API」);存檔以 `json.dumps(ensure_ascii=False, indent=2)` 寫入。
- 〔觀察〕`workflows/default.json` 入 git;其他檔案(使用者另存的工作流程與存檔暫存檔 `.wf-*.tmp`)由 `.gitignore` 的 `workflows/*`、`!workflows/default.json` 忽略(CHG-20261026-01 T3),存檔後 `git status --porcelain --untracked-files=all` 仍無輸出,`scripts/deploy_local.sh` 不因此中止。
- 〔觀察〕從畫布覆寫 `default`(`overwrite: true`)會改到入 git 的 `workflows/default.json`:工作樹變髒(`deploy_local.sh` 中止),且畫布存檔不保留頂層 `steps`,步驟精靈的預設步驟遺失;以 `git checkout -- workflows/default.json` 復原(CHG-20261026-01 代決 7,程式不擋)。

### 產出檔(`outputs/`,不入 git)〔觀察〕

| 檔名樣式 | 產生者 | 格式 |
|----------|--------|------|
| `speech-<stamp>.wav` | `taiwan_ai.speak`(`speak` 節點、MCP `speak`) | WAV(BreezyVoice 輸出:IEEE float,format 3,22050 Hz 單聲道 float32) |
| `image-<stamp>.png` | `media.generate_image`(`generate-image` 節點、MCP `generate_image`) | PNG,FLUX 原尺寸 |
| `cover-<stamp>.png` | `media.make_cover` / `add-title` 節點 | PNG 1920×1080 |
| `podcast-<stamp>.mp4` | `media.make_video` | H.264 yuv420p 10 fps + AAC 192k(+ mov_text 字幕軌,language=chi);音檔為 WAV 時影片長度截在 `wav_seconds()` 秒(`-t`),影片軌與音軌長度差 ≤ 0.1 秒;非 WAV 只靠 `-shortest`,片尾可能多出靜止畫面(ADP-7) |
| `podcast-<stamp>.srt` | `media.make_srt` | SRT UTF-8 |
| `uploads/<uuid4 hex>.<ext>` | `POST /api/upload` | 使用者上傳檔;副檔名白名單 `.txt .md .png .jpg .jpeg .webp .wav .mp3 .m4a`,≤ 200 MB,檔頭內容嗅探相符;檔名由伺服器產生,原始檔名只取副檔名 |
| `.heavy.lock` | `heavy_lock` | 跨行程鎖檔,內容不讀不寫、不刪除 |
| `.studio.pid`、`studio_server.log` | `scripts/deploy_local.sh` | 部署用 pidfile 與伺服器日誌 |
| `ui_check/*.png` | UI 互動檢查(CHG-20261022-01 T14) | 截圖 |

`<stamp>` = `YYYYmmdd-HHMMSS-毫秒-4 碼隨機`(本機時間;`taiwan_ai.stamp()`)。輸出檔一律先以 `taiwan_ai.reserve()` 排他建立佔位檔(`O_EXCL`、不跟隨符號連結,撞名重取),不覆寫既有檔案。

### 程式常數〔觀察〕`taiwan_ai.py`

| 常數 | 值 | 說明 |
|------|----|------|
| `OLLAMA` | `http://localhost:11434` | 只經 `local_open()` 連線 |
| `MFLUX` | `~/.local/bin/mflux-generate` | 必須存在才可生圖;生圖模型名稱改讀設定 `roles.image.model` |
| `BREEZY` | `tools/BreezyVoice` | 聲線檔與逐字稿改讀設定 `roles.voice`(`wav`、`text`),兩者要一致 |
| `BREEZY_ENV` | `PYTHONUTF8=1`、`PATH=/usr/bin:/bin`、`HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1` | BreezyVoice 子程序環境(ADP-12) |

〔觀察〕模型名稱不再是程式常數:`reason`、`reason_heavy`、`taiwan` 一律經 `taiwan_ai.model()` 讀設定資料庫;`code` 角色已移除。`FLUX`、`VOICE_WAV`、`VOICE_TEXT` 已於 CHG-20261026-01 T1、T2 移除,預設值只留在 `settings.DEFAULTS`,`image`、`voice` 經 `taiwan_ai.role_config()` 讀取。

### 程式常數〔觀察〕`media.py`

| 常數 | 值 | 說明 |
|------|----|------|
| `WAV_MAX_CHUNKS` | 1024 | `wav_seconds()` 最多走訪的 RIFF chunk 數,超過丟 `ValueError` |
| `_WAV_FMT_MAX` | 64 | `fmt ` chunk 最多讀進記憶體的位元組數 |
| `VIDEO_W` / `VIDEO_H` | 1920 / 1080 | 影片與封面尺寸 |

### 導入時已存在的產出檔(不入 git,僅記錄)〔觀察〕
`outputs/test_tts.wav`、`outputs/anime_pinup.png`、`outputs/test_flux.png`——檔名不符上表樣式,〔推論〕為手動測試留下。

## 關聯

〔觀察〕工作流程 JSON 以節點 id 與連線記錄同一集各產出的來源關係;實際產出檔之間沒有記錄關聯,時間戳各自獨立。`podcast-<stamp>.mp4` 與 `.srt` 共用同一個 stamp(一起佔位)。`file-input` 節點的 `file` 參數是相對 `outputs/` 的路徑(例 `uploads/<uuid>.png`)。

## 索引 / 約束

- 〔觀察〕輸出檔唯一性靠毫秒時間戳 + 4 碼隨機碼與排他建立(ADP-8 的處理,結案以 ACC 為準)。
- 〔觀察〕沒有刪除策略,`outputs/`(含 `uploads/`)會無限增長。〔未知〕是否需要清理策略。
- 〔觀察〕FLUX 寬高須為 16 倍數:MCP 在取鎖前檢查;`generate-image` 節點由 `executor.validate` 檢查(256–2048);`media.generate_image` 本身沒檢查。
- 〔觀察〕設定資料庫的 SQL 一律參數化,表名與欄名寫死在 `settings.py`;資料庫不經 `/files/*` 或任何 HTTP 端點直接提供下載。

## 狀態 / 列舉

| 列舉 | 值 | 等級 |
|------|----|------|
| 文案風格 `STYLES` | 輕鬆聊天、知識科普、新聞評論、故事敘述、訪談開場 | 〔觀察〕`nodes.py` |
| 長度 | 0.5–10 分鐘,步進 0.5;字數 = 分鐘 × 250 | 〔觀察〕`nodes.py`(`write-script` 的 `minutes`、`CHARS_PER_MINUTE`) |
| 模型角色 | reason、reason_heavy、taiwan、image、voice(設定);MCP `list_models` 另回報 image、speech | 〔觀察〕`settings.py`、`local_models_mcp.py` |
| 埠型別 | text、image、audio、video | 〔觀察〕`nodes.PORT_TYPES` |
| 節點執行狀態 | running、waiting、done、error、skipped(pinned / upstream)、cancelled;整次 run:ok、error、cancelled | 〔觀察〕`executor.py` |
| 上傳檔種類 | text(`.txt .md`)、image(`.png .jpg .jpeg .webp`)、audio(`.wav .mp3 .m4a`) | 〔觀察〕`nodes.FILE_KINDS` |

〔觀察〕CLI 模式(ask、code、image、speak、auto)已隨 CLI 移除(CHG-20261022-01 T2)。
