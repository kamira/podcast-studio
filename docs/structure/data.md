# 資料結構

> 導入反推(2026-10-22,基準點 `7da165b6048897abb8acac4bba69db9677827471`)。來源等級見 `docs/ai-guideline.md` 表頭。
> 2026-10-26 依 CHG-20261022-01 結構文件更新:新增設定資料庫 `config/studio.db`(取代導入時「沒有設定檔」的描述與修訂 2 前規劃的 `config/models.json` schema)、工作流程 JSON、`outputs/uploads/`;輸出檔時間戳改為毫秒 + 隨機碼(以 T1~T13 合併後的 main 為準)。
> 2026-10-26 依 CHG-20261026-01 結構文件更新:`image`、`voice` 角色接上 `media.generate_image()`、`taiwan_ai.speak()`;移除 `FLUX`、`VOICE_WAV`、`VOICE_TEXT` 常數;`workflows/` 只追蹤 `default.json`;`wav_seconds()` 改為自寫 RIFF/WAVE 解析;影片 `-r 10`、WAV 加 `-t`(以 T1~T5 合併後的 main 為準)。

> 2026-10-28 依 CHG-20261022-04 結構文件更新(DIR-5 修正輪,以整合分支 T1~T9 與修正為準):新增專案資料(`projects/`:Project、Segment、Annotation、ImageClip、Asset,`schema_version: 1`);設定資料庫升為 schema 2、新增 `voice_defaults` 表;畫布存檔保留 `steps` 的規則(取代「目前不保留 `steps`」)。

> 2026-10-30 依 CHG-20261029-01 修訂 1 結構文件更新(以整合分支 `chg/CHG-20261029-01-rev1-impl` 的 T10~T14 為準):Segment 的 `fusions[].display`、`post.pitch_points` 兩個選填欄位(`schema_version` 仍為 2);後製鍵附加演算法標記。

> 2026-10-30 依 CHG-20261022-02 結構文件更新(以整合分支 `chg/CHG-20261022-02-impl` 的 T1~T3 為準):新增「repo 外讀寫的檔案」一節(MCP 註冊管理讀取的使用者全域設定與路徑規則)。

〔觀察〕持久化資料有五類:設定資料庫 `config/studio.db`(使用者資料,不入 git)、工作流程 JSON(`workflows/`)、專案資料 `projects/`(使用者資料,不入 git;CHG-20261022-04)、`outputs/` 下的產出檔與上傳檔、程式內常數。流程執行中的文字(文案、標題、prompt)只存在伺服器記憶體中的執行事件(最近 20 次 run)與瀏覽器畫布上,另存工作流程時隨節點參數或釘住的編輯值寫入 JSON。

## 實體 / 資料表

### 設定資料庫 `config/studio.db`(SQLite,schema v2)〔觀察〕`settings.py`

| 資料表 | 欄位 | 約束 | 說明 |
|--------|------|------|------|
| `meta` | `key TEXT PRIMARY KEY`、`value TEXT NOT NULL` | — | `schema_version`(目前 `"2"`;CHG-20261022-04 T8 由 `"1"` 升級)、`imported_from`(`config/models.json`,只在一次性匯入時寫)、`imported_at`(UTC 時間) |
| `voice_defaults` | `key TEXT PRIMARY KEY`、`value REAL NOT NULL` | 鍵只有 `speed`(0.5–2.0,取到 0.05)、`pause_comma`、`pause_sentence`、`pause_paragraph`(0–5 秒,取到 0.1) | 語音預設(設定頁「語音預設」子頁);預設 1.15 / 0.0 / 0.4 / 0.8;新專案的 `defaults` 取自這裡(`studio_server._voice_defaults()`),不合專案規則時回 `project.DEFAULTS`(CHG-20261022-04 T8) |
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
- 〔觀察〕`schema_version` 不是程式所知的版本(1、2)時,不讀不寫、回 `DEFAULTS` 並提示,不猜測轉換。
- 〔觀察〕**schema 1 → 2 升級**(CHG-20261022-04 T8):`load()` 唯讀探測到 v1 才取初始化鎖、鎖內再探測,在 `BEGIN IMMEDIATE` 交易內再讀版本後建 `voice_defaults` 表、寫入預設值並把版本改為 2;不改 `roles`、`review_seats`;失敗即 rollback 仍為 v1,回傳 v1 的設定 + 程式內語音預設 + `_warnings`;兩個行程同時觸發時後到者看到版本已是 2 即略過;v1 上的 `save()` 在同一交易內升級再存入;一次性匯入的新資料庫直接寫 schema 2。`validate()` 的 `voice_defaults` 為選填(省略時 `save()` 不動資料庫內的語音預設),`load()` 一律回傳。回滾步驟寫在 `settings.py` 檔頭與 CHG-20261022-04「風險與回滾」。
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
- 〔觀察〕`steps` 只出現在預設範本 `workflows/default.json`,定義步驟精靈的預設步驟:① 文稿(`topic`、`write-script`、`taiwanize`)② 語音(`speak`)③ 封面(`title`、`cover-prompt`、`generate-image`、`add-title`)④ 剪輯(沒有節點,時間軸屬 CHG-20261022-04)⑤ 匯出(`make-video`)。`executor.validate` 不讀 `steps`;步驟精靈只讀 `GET /api/workflows/default` 的頂層 `steps`(CHG-20261022-04 修訂 3 R3-1)。前端畫布存檔(`buildGraph()` 經 `carrySteps()`)保留載入時的 `steps`:已不在畫布上的節點 id 從各步驟移除,原本有節點、移除後變空的步驟整個刪除,原本就沒有節點的步驟(④ 剪輯)保留(CHG-20261022-04 修訂 3 R3-2;取代先前「目前不保留 `steps`」的觀察)。
- 〔觀察〕檔名即名稱:中英日白名單字元 1~64 字(見 `design.md`「HTTP API」);存檔以 `json.dumps(ensure_ascii=False, indent=2)` 寫入。
- 〔觀察〕`workflows/default.json` 入 git;其他檔案(使用者另存的工作流程與存檔暫存檔 `.wf-*.tmp`)由 `.gitignore` 的 `workflows/*`、`!workflows/default.json` 忽略(CHG-20261026-01 T3),存檔後 `git status --porcelain --untracked-files=all` 仍無輸出,`scripts/deploy_local.sh` 不因此中止。
- 〔觀察〕從畫布覆寫 `default`(`overwrite: true`)會改到入 git 的 `workflows/default.json`:工作樹變髒(`deploy_local.sh` 中止);以 `git checkout -- workflows/default.json` 復原(CHG-20261026-01 代決 7,程式不擋)。CHG-20261022-04 起畫布存檔保留 `steps`(見上),覆寫後精靈步驟跟著畫布變更,不再遺失。

### 專案資料(`projects/<名稱>/project.json`,`schema_version: 1`)〔觀察〕`project.py`(CHG-20261022-04 T2)

JSON 物件,未知鍵一律拒絕;`schema_version` 不是 1 一律拒絕載入,不猜測轉換。上限:文稿 50000 字、片段 2000 個、單句 500 字、`project.json` 5 MB、素材 200 個且合計 2 GB、音訊總長(變速後)60 分鐘。

| 實體 | 欄位 | 約束 / 說明 |
|------|------|-------------|
| Project | `schema_version`、`name`、`script`、`defaults`、`segments`、`images`、`assets` | `name` 與目錄名相同,規則見 `names.py`(中英日白名單 1~64 字,CHG 修訂 4 補記);`defaults` = `{"speed", "pause_comma", "pause_sentence", "pause_paragraph"}`,新專案取自設定資料庫 `voice_defaults` |
| Segment(語音片段) | `id`、`text`、`break`、`pause_after`、`speed`、`annotations`、`audio` | `id` 為伺服器產生的 `^[0-9a-f]{8,32}$`;`text` 為原文(不含標註,1~500 字);`break` ∈ sentence / paragraph / forced / end;`pause_after` 為 null(依 `break` 取 `defaults`)或 0~5 秒(取到 0.1,0 存為 0、不當成 null);`speed` 為 null(用 `defaults.speed`)或 0.5~2.0(取到 0.05);`audio` 為 null 或 `{"id", "seconds"(變速前), "key"?}`——有 `key`(64 碼快取鍵)為合成音檔,沒有 `key` 為自錄取代的音檔(合成不覆蓋) |
| Annotation(聲調標註) | `index`、`zhuyin` | `index` 為句內字元索引,只能標在漢字上;`zhuyin` 為聲調符號格式注音(ㄅ–ㄩ + ˊˇˋ˙,1–5 字);合成時以 `字[:注音]` 插入,原文的 `[`、`]` 轉全形 |
| ImageClip(畫面軌) | `id`、`asset`、`start`、`end` | `asset` 為圖片素材 id(不記路徑);`start` 秒、`end` 為 null(到結尾)或秒,皆取到 0.1 |
| Asset(素材) | `id`、`kind`、`ext`、`bytes`、`label`? | `kind` ∈ image(png、jpg、jpeg、webp)/ audio(wav、mp3、m4a)/ text(txt、md);檔案存於 `assets/<id>.<ext>`;經 `/api/upload` → `POST /api/projects/{p}/assets` 或「送到素材庫」節點進入(後者同樣做檔頭嗅探,DIR-5 修正 M-5) |

- 〔觀察〕目錄:`project.json`、`history/`(舊版 `YYYYmmdd-HHMMSS-<毫秒>-<4 碼隨機>.json`,UTC,只保留最近 10 個)、`audio/`(合成音檔 `<快取鍵前 32 碼>.wav` 與變速快取 `<id>-s<語速×100>.wav`、自錄音檔 `<新 id>.wav`)、`assets/`、`tmp/`(worker 輸入檔,用完即刪,啟動時清空)、`.lock`(`fcntl.flock` 專案層級鎖);`projects/.trash/<名稱>-<時間戳>-<4 碼>/` 為刪除的專案,滿 30 天由 `startup_cleanup()` 清除。
- 〔觀察〕寫入:每次存檔前把舊檔移入 `history/`,以暫存檔 + `os.replace` 原子寫入,載入到存檔全程持專案鎖(`project.update()`);還原版本也是一次存檔。合成結果在整批結束時一次寫回,一次合成最多新增一個 history 版本;合成中途已完成的句子只在伺服器記憶體中疊到專案檢視上,不存檔(DIR-5 修正 M-1)。
- 〔觀察〕符號連結:`projects/`、`.trash/`、專案目錄、`tmp/`、`history/` 本身是符號連結時一律拒絕處理並記警告;`.trash/`、`tmp/` 內的符號連結不跟隨。
- 〔觀察〕合成快取鍵 = SHA-256(JSON 陣列 [含標註的合成文字, 聲線檔 SHA-256, 聲線逐字稿 SHA-256, 模型名]);文字、標註、聲線檔、逐字稿或模型任一改變即不命中(修訂 3 R3-4)。

### 產出檔(`outputs/`,不入 git)〔觀察〕

| 檔名樣式 | 產生者 | 格式 |
|----------|--------|------|
| `speech-<stamp>.wav` | `taiwan_ai.speak`(`speak` 節點、MCP `speak`) | WAV(BreezyVoice 輸出:IEEE float,format 3,22050 Hz 單聲道 float32) |
| `image-<stamp>.png` | `media.generate_image`(`generate-image` 節點、MCP `generate_image`) | PNG,FLUX 原尺寸 |
| `cover-<stamp>.png` | `media.make_cover` / `add-title` 節點 | PNG 1920×1080 |
| `podcast-<stamp>.mp4` | `media.make_video` | H.264 yuv420p 10 fps + AAC 192k(+ mov_text 字幕軌,language=chi);音檔為 WAV 時影片長度截在 `wav_seconds()` 秒(`-t`),影片軌與音軌長度差 ≤ 0.1 秒;非 WAV 只靠 `-shortest`,片尾可能多出靜止畫面(ADP-7) |
| `podcast-<stamp>.srt` | `media.make_srt` | SRT UTF-8 |
| `uploads/<uuid4 hex>.<ext>` | `POST /api/upload` | 使用者上傳檔;副檔名白名單 `.txt .md .png .jpg .jpeg .webp .wav .mp3 .m4a`,≤ 200 MB,檔頭內容嗅探相符;檔名由伺服器產生,原始檔名只取副檔名 |
| `podcast-<stamp>.mp4` / `.srt`(時間軸匯出) | `media.render_timeline`(`POST /api/projects/{p}/render`,CHG-20261022-04 T5) | 1920×1080 H.264 + AAC + mov_text;`-r 10`、`-vf fps=10`、`-t <layout.duration>`;音軌為 48 kHz 單聲道 PCM 依 layout 組成(各片段解碼以 `-t` 截在需要的長度,DIR-5 修正 M-3);圖片重疊時後開始者優先、沒有圖的時段為黑畫面 |
| `.tmp/` | `/api/annotate` | worker 輸入檔,用完即刪;`/files/` 不提供點開頭目錄 |
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

## repo 外讀寫的檔案(CHG-20261022-02)〔觀察〕`mcp_registry.py`

〔觀察〕本程式讀取的 repo 外檔案只有下列四項,只由 `mcp_registry` 存取;寫入一律只經官方 CLI(`~/.local/bin/claude`、`~/.local/bin/codex` 的 `mcp add` / `mcp remove`),本程式不直接寫任何一個。

| 檔案 | 路徑規則 | 讀寫方式 | 讀取範圍 |
|------|----------|----------|----------|
| `.claude.json`(Claude Code 全域設定,含帳號資訊) | `$CLAUDE_CONFIG_DIR/.claude.json`;未設時 `~/.claude.json` | 唯讀解析;寫入只經 `claude mcp add -s user` / `claude mcp remove -s user` | 頂層 `mcpServers["local-models"]` 取內容(`type`、`command`、`args`、`env`;env 只回鍵名,值只用來遮蔽輸出);`projects[<本 repo 絕對路徑>].mcpServers["local-models"]` 只判斷有無(遮蔽偵測);其餘內容(含其他 repo 的 `projects` 項、帳號欄位)不保留、不回傳、不寫日誌。檔案不存在 → `absent`;壞掉、頂層不是物件或超過 16 MB → `unknown` |
| `<repo>/.mcp.json`(project scope) | 固定為 repo 根目錄 | 唯讀 | 只判斷 `mcpServers["local-models"]` 有無(遮蔽偵測),不回傳內容;無法解析時 `shadowed` 為 null |
| Codex `config.toml` | `$CODEX_HOME/config.toml`;未設時 `~/.codex/config.toml` | 本程式不開啟此檔;讀取經 `codex mcp get local-models --json`,寫入只經 `codex mcp add` / `codex mcp remove` | CLI 回傳的 `transport.command` / `args` / `env`(只回鍵名)與 `tool_timeout_sec` |
| `settings.json`(Claude Code 使用者設定) | `$CLAUDE_CONFIG_DIR/settings.json`;未設時 `~/.claude/settings.json` | 唯讀,不寫入 | 只讀 `env.MCP_TOOL_TIMEOUT`(沒有時提醒建議值 `1800000`) |

- 〔觀察〕路徑每次呼叫依伺服器行程當下的 `CLAUDE_CONFIG_DIR`、`CODEX_HOME` 決定(D10),瀏覽器不能指定;CLI 子程序沿用同一組環境變數,因此解析的檔案與 CLI 寫入的檔案一致。`CODEX_HOME` 須為已存在的目錄。
- 〔觀察〕讀檔上限 16 MB(`.claude.json`、`settings.json`、`.mcp.json`);送到瀏覽器的字串依「去除 env 值 → `scrub()` → 截斷 64 KB」處理,路徑以 `~`、`<repo>` 顯示(不在家目錄或 repo 之下的設定目錄顯示為 `$CLAUDE_CONFIG_DIR`、`$CODEX_HOME`)。
- 〔觀察〕Codex 的 `tool_timeout_sec` 由使用者手動寫在 `config.toml` 的 `[mcp_servers.local-models]`;`codex mcp add` 無法設定,移除再註冊後會消失(D9 只提醒,恢復片段見 `PODCAST_STUDIO.md §10.3`)。

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
| 片段切點 `break` | sentence(句尾標點)、paragraph(換行)、forced(強制停頓)、end(文末);停頓預設依序取 `pause_sentence`、`pause_paragraph`、`pause_comma`、0 | 〔觀察〕`project.py` |
| 合成事件 | waiting、segment(`id`、`seconds`、`cached`、`audio`)、error、finished(`status`、`synthesized`、`cached`、`saved`) | 〔觀察〕`studio_server.py`、`tts_service.py` |

〔觀察〕CLI 模式(ask、code、image、speak、auto)已隨 CLI 移除(CHG-20261022-01 T2)。

## CHG-20261029-01:專案檔 schema 2、後製檔、設定資料庫 schema 3

〔觀察〕以下為 CHG-20261029-01 T1、T3、T7、T8 與修訂 1 T10~T12 的資料變更(程式為準:`project.py`、`tts_service.py`、`settings.py`)。

**專案檔 `schema_version: 2`**(v1 載入時在記憶體補預設值,存檔寫成 v2,舊檔照例移入 `history/`;3 以上拒絕)。Segment 新增:

| 欄位 | 型別 | 說明 |
|------|------|------|
| `connect` | null 或 0~100 整數 | 連音;null = 0% |
| `clarity` | null 或 0~100 整數 | 清晰度(咬字含糊程度);null = 100% |
| `word_clarity` | `[{start, end, value}]` | 字詞清晰度;`[start, end)` 為句內漢字、不重疊、≤ 32 段 |
| `pitch` | null 或 `{points: [{pos, semi}], roundness}` | 語調折線;2~16 點、pos 0~1(0.01)嚴格遞增、semi −6~+6(0.1);roundness null = 50% |
| `post` | null 或 `{id, key, seconds, trim, pitch_points?}` | 伺服器產生的後製結果;`key` = 後製鍵,`seconds` = 後製後長度(變速前),`trim` = 裁掉的秒數;`pitch_points`(修訂 1,選填)= worker 實際套用的語調內部點 `[[位置, 半音], …]`,2–30 點、位置 0–1 嚴格遞增、半音 ±7.2,只在有語調且 worker 回傳合格時寫入(快取命中時沿用同一後製鍵已寫入的值,沒有則不帶);PATCH 不接受 |
| `fusions` | `[{index, length, zhuyin, source, display?}]` | 合音;length 2~4、zhuyin 為 1~length−1 個音節(半形空白分隔)、source ∈ rule / lexicon / manual;範圍內都是漢字、不重疊、≤ 16 組、範圍內不得有聲調標註;`display`(修訂 1,選填)為空字串或 1~4 個漢字,由詞庫套用時寫入詞庫當時的顯示字(規則與手動為空字串),有值時合成以它取代整個範圍送模型、不加注音,缺少視同空字串(舊專案檔不補欄位、送模型的文字不變);加上或改變 `display` 即改變 `tts_text` 與合成快取鍵 |
| `fusion_off` | `[{index, length}]` | 停用的合音位置(≤ 32 筆);與 `fusions` 同一範圍互斥;文字改動時兩者依位移調整、與改動範圍重疊者清除 |

**後製鍵**:SHA-256(JSON 陣列 [audio.id, 後製版本號 `POST_VERSION` 1, 裁切參數, connect, clarity, word_clarity, pitch] + 演算法標記);演算法標記(修訂 1,`project.POST_ALGO = {"clarity": 2, "pitch": 2}`)只在清晰度 < 100% 或有字詞清晰度(`clarity`)、有語調(`pitch`)時附加為陣列最後一項的物件,只有裁切或連音的片段鍵與修訂前相同;語速、停頓不在鍵內。修訂 1 只新增上列兩個選填欄位,`schema_version` 仍為 2,舊專案檔不需轉換。

**`projects/<名稱>/audio/`** 新增:裁切檔與後製檔 `<後製鍵前 32 碼>.wav`(預設參數的後製檔即裁切檔);變速檔改以後製檔為基礎 `<後製 id>-s<語速×100>.wav`。合成檔 `<audio.id>.wav` 不改寫;後製檔與變速檔不自動刪除。

**設定資料庫 schema 3**(v2 在初始化鎖內單一交易升級,失敗 rollback 維持 v2;不改 `roles`、`review_seats`、`voice_defaults`):

| 表 | 欄位 | 說明 |
|----|------|------|
| `fusion_words` | `word` PK、`zhuyin`、`display`(預設 '')、`enabled`、`source`(default / custom) | 合音詞庫;預設詞(`settings.FUSION_DEFAULTS`,11 筆)可編輯 / 停用 / 還原、不可刪 |
| `fusion_settings` | `key` PK、`value` INTEGER | `import_max_kb`(預設 256,16–4096)、`import_max_rows`(預設 1000,10–10000) |

匯出格式:JSON `{"format": "podcast-studio-fusions", "version": 1, "words": [{word, zhuyin, display, enabled, source}]}`;CSV 表頭 `word,zhuyin,display,enabled,source`(UTF-8)。
