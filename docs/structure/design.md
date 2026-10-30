# 設計結構

> 導入反推(2026-10-22,基準點 `7da165b6048897abb8acac4bba69db9677827471`)。來源等級見 `docs/ai-guideline.md` 表頭。現況不等於規範;缺陷見 Guideline §9。
> 2026-10-26 依 CHG-20261022-01 結構文件更新:新增 HTTP API 契約、節點登錄表、設定模組介面、跨行程鎖;CLI 與 Gradio 版移除;設計決策表加入本筆決策(以 T1~T13 合併後的 main 為準)。
> 2026-10-26 依 CHG-20261026-01 結構文件更新:新增 `taiwan_ai.role_config()`;生圖與聲線改讀設定、mflux 子程序加 `HF_HUB_OFFLINE=1`;移除 `FLUX` 常數;`wav_seconds()` 改為自寫 RIFF/WAVE 解析;影片合成 `-r 10`、WAV 加 `-t`;設計決策表加入本筆代決(以 T1~T5 合併後的 main 為準)。
> 2026-10-28 依 CHG-20261028-01 結構文件更新:設計決策表新增「Codex 席以 PR head 唯讀檢視讀完整程式」。
> 2026-10-28 依 CHG-20261028-02 結構文件更新:設計決策表新增「審議席改為意見、單一統整席判定」;「本機審議閘」列改記 gate 驗判定留言。

> 2026-10-28 依 CHG-20261022-04 結構文件更新(DIR-5 修正輪,以整合分支 T1~T9 與修正為準):新增專案 API 契約、`breezy_worker` 介面(jobs 格式、stdout 進度行、G2PW `num_workers = 0`、環境由 `tts_service` 傳入 `taiwan_ai.BREEZY_ENV`)、合成快取鍵、`project` / `timeline` / `tts_service` / `names` 元件、`media.render_timeline` 與 `media.sniff_binary`;設計決策表加入本筆決策(含修訂 2 合成粒度與實測依據);畫布存檔改為保留 `steps`。

> 2026-10-30 依 CHG-20261029-01 修訂 1 結構文件更新(以整合分支 `chg/CHG-20261029-01-rev1-impl` 的 T10~T14 為準):清晰度方案 C 取代 v2 與低通;後製鍵演算法標記 `POST_ALGO`;語調 PCHIP 混合曲線與自動補點(`expand_points`、吸附有聲幀);shape stdout 帶 `pitch_points`;合音送顯示字(`tts_text`、`model_units`);前端清晰度警告、語調試算 / 實際套用的畫法、屬性面板合音模式「送模型」「字幕」兩列。

> 2026-10-30 依 CHG-20261022-02 結構文件更新(以整合分支 `chg/CHG-20261022-02-impl` 的 T1~T3 為準):新增 `mcp_registry` 元件與介面、外部 CLI 契約(`claude mcp …`、`codex mcp …`)、`/api/mcp/*` 三個端點列入 HTTP API 表;設計決策 D8~D12(見檔尾「CHG-20261022-02」節)。

## 關鍵元件

| 元件 | 責任 | 對外介面/契約 | 等級 |
|------|------|----------------|------|
| `taiwan_ai.model(role)` | 每次呼叫經 `settings.load()` 取該角色(`reason`、`reason_heavy`、`taiwan`)的模型名稱 | 回傳 `str`;未知角色丟 `KeyError` | 〔觀察〕 |
| `taiwan_ai.role_config(role)` | 每次呼叫經 `settings.load()` 取 `roles[role]`;不要求角色有 `model` 鍵(`voice` 只有 `wav`、`text`) | 回傳該角色設定的**副本**(`dict`);未知角色丟 `KeyError` | 〔觀察〕CHG-20261026-01 T1 |
| `taiwan_ai.chat(role, prompt, system="", think=None)` | 對 Ollama `/api/chat` 非串流請求;模型與 `num_ctx` 取自設定;`keep_alive=10m`、逾時 1800 秒;移除 `<think>…</think>` | 回傳 `str`(可能是空字串);HTTP 錯誤直接拋出 | 〔觀察〕 |
| `taiwan_ai.local_open(req, timeout)` | 對本機 Ollama 送請求:只限 `localhost` / `127.0.0.1` / `::1`、不經代理、不跟隨重新導向 | `chat`、`unload_llms`、網頁設定 API、MCP `list_models` 一律經此 | 〔觀察〕 |
| `taiwan_ai.unload_llms()` | 查 `/api/ps`,對目前載入的**全部**模型送 `keep_alive: 0`;錯誤全吞 | 無回傳 | 〔觀察〕ADP-1 的處理(CHG-20261022-01 T2) |
| `taiwan_ai.speak(text)` | 每次呼叫以 `role_config("voice")` 取聲線檔(repo 相對路徑轉絕對路徑)與逐字稿 → 清 Markdown 符號 → 子程序 BreezyVoice;子程序環境 `BREEZY_ENV` 含 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`,模型快取不存在時失敗並提示,不下載 | 回傳 `outputs/speech-<stamp>.wav` 的 `Path`;呼叫端須在 `heavy_lock()` 內 | 〔觀察〕ADP-12 的處理(T4);ADP-4、ADP-9 未處理 |
| `taiwan_ai.to_tw` | OpenCC `s2twp` | `str → str` | 〔觀察〕 |
| `taiwan_ai.stamp()` / `reserve(prefix, suffix, also=())` | `stamp()`:`YYYYmmdd-HHMMSS-毫秒-4 碼隨機`;`reserve()`:在 `outputs/` 以 `O_EXCL`、不跟隨符號連結排他建立佔位檔,撞名重取(最多 20 次) | 輸出檔一律先佔位再寫入,不覆寫既有檔 | 〔觀察〕ADP-8 的處理(T2) |
| `media.generate_image(prompt, w, h)` | 每次呼叫以 `role_config("image")` 取 `model`、`steps`(一次呼叫讀一次)→ 卸載 LLM → mflux;子程序環境為目前環境的副本加 `HF_HUB_OFFLINE=1`(沒有快取的模型直接失敗,不下載) | `outputs/image-<stamp>.png` | 〔觀察〕CHG-20261026-01 T2 |
| `media.make_cover(prompt, title)` / `add_title(img, title)` | 1360×768 → 放大裁切 1920×1080 → 底部 28% 模糊帶 + 白字黑邊標題(120→48 px,最多 2 行) | `outputs/cover-<stamp>.png` | 〔觀察〕超過 2 行未處理(ADP-3) |
| `media.split_sentences(script, max_chars=24)` | 依 `。！？!?；;\n` 切,過長再依 `，,、` 切,仍過長硬切 | `list[str]` | 〔觀察〕 |
| `media.make_srt(script, seconds, path)` | 依字數比例分配時間 | 寫 SRT,回傳 path | 〔觀察〕 |
| `media.wav_seconds(path)` | 以標準庫 `struct` 解析 RIFF/WAVE:只讀 chunk 標頭與 `fmt `(最多 64 位元組),以 `seek` 跳過其他 chunk,data 不讀進記憶體;支援 PCM(1)、IEEE float(3)、WAVE_FORMAT_EXTENSIBLE(0xFFFE,以 SubFormat 前 2 位元組判斷 1 或 3);長度 = data 位元組數 ÷(聲道數 × 每樣本位元組數)÷ 取樣率;chunk 長度(含奇數補位)不得超出檔尾、最多走訪 `WAV_MAX_CHUNKS`(1024)個 chunk | 回傳 `float` 秒數;不是 RIFF/WAVE、缺 `fmt ` 或 `data`、不支援的編碼、欄位為 0、data 不是整數個樣本框、讀檔失敗(`OSError`,以 `from None` 斷開例外鏈)一律丟繁中 `ValueError`,訊息不含路徑 | 〔觀察〕CHG-20261026-01 T4;`RF64`、64 位元 float 未支援 |
| `media.make_video(image, audio, script="")` | ffmpeg:loop 圖 + 音訊(+ mov_text 字幕),`-r 10 -tune stillimage`,`-shortest`;以檔頭前 12 位元組判斷音檔為 RIFF/WAVE(不看副檔名)時,在輸出檔名前加 `-t <wav_seconds(),小數 3 位>`,影片軌與音軌長度差 ≤ 0.1 秒 | `outputs/podcast-<stamp>.mp4`(+ 同名 `.srt`) | 〔觀察〕CHG-20261026-01 T5;非 WAV 不加 `-t`(片尾可能多出靜止畫面),帶字幕時讀不到長度而失敗(ADP-7) |
| `heavy_lock.heavy_lock(timeout=None, on_wait=None)` | 跨行程排他鎖 `outputs/.heavy.lock`(`fcntl.flock(LOCK_EX)`);被占用時呼叫一次 `on_wait()` 再每 0.05 秒重試 | context manager;`timeout` 秒數逾時丟 `TimeoutError`;`outputs/` 或鎖檔是符號連結丟 `OSError`;鎖檔不讀不寫不刪 | 〔觀察〕ADP-2 的處理(T4) |
| `local_models_mcp.busy()` | 重讀設定 → `heavy_lock(on_wait=…)` → `redirect_stdout(stderr)` | context manager;等待時寫 stderr | 〔觀察〕 |
| `safe_fs`(`open_dir` / `open_child` / `open_file` / `open_path` / `open_or_create` / `create_excl` / `lstat` / `entries` / `unlink` / `rmtree` / `rename` / `replace` / `link` / `closing`) | 自受信根目錄(呼叫端給的固定目錄,例 `project.ROOT`、`outputs/`、`static/`)以 `dir_fd` 逐層 `O_NOFOLLOW` 開啟;名稱一律單一路徑元件(空、`.`、`..`、含 `/` 或 NUL 丟 `ValueError`);任一層是符號連結或不是預期種類丟 `UnsafePathError`(`OSError` 子類別),不存在丟 `FileNotFoundError`;`rmtree` 以 fd 遞迴,遇符號連結只 unlink 連結本身;`open_or_create` 以「先開、不存在再 `O_EXCL` 建立」迴圈取代 `O_CREAT`(macOS 上多行程 `openat(O_CREAT \| O_NOFOLLOW)` 實測偶發 ENOENT) | 回傳 fd(呼叫端關閉);訊息不含本機路徑 | 〔觀察〕CHG-20261022-04 DIR-5 修正 4 |
| `settings`(`load` / `save` / `validate` / `load_meta` / `voice_options`) | 模型設定的唯一存取點,儲存層為 SQLite `config/studio.db` | 見下方「設定模組介面」 | 〔觀察〕 |
| `nodes.REGISTRY` | 十種節點的宣告與 `run()`,包住 `taiwan_ai` / `media` | 見下方「節點登錄表」 | 〔觀察〕 |
| `executor.validate(graph)` / `run(graph, emit, cancel=None)` | 驗證流程圖;依拓撲順序在呼叫端執行緒執行並以 `emit(dict)` 推送事件 | 見下方「流程圖格式與執行事件」 | 〔觀察〕 |
| `studio_server.create_app(...)` / `main()` | FastAPI app;`main()` 以 uvicorn 綁 `127.0.0.1:7860`,啟動時呼叫 `project.startup_cleanup()` | 見下方「HTTP API」「專案 API」 | 〔觀察〕 |
| `mcp_registry`(`status` / `plan` / `apply` / `paths` / `server_argv` / `redact_env` / `scrub`、`Busy`) | local-models 在 Claude Code / Codex 的 user scope 註冊管理;只操作名稱 `local-models`;寫設定只經官方 CLI 的 `mcp add` / `mcp remove`,不直接寫 `.claude.json`、`config.toml`、`~/.claude/settings.json` | 見檔尾「CHG-20261022-02」節 | 〔觀察〕CHG-20261022-02 T1 |
| `names`(`name_char_ok`、`normalize_name`、`fold_name`) | 工作流程名稱與專案名稱共用的中英日白名單(1~64 字、逐字比對,不做正規化);只差大小寫的比對鍵 | 不合格回錯誤;規則單一來源 | 〔觀察〕CHG-20261022-04 T2 |
| `project`(`Project`、`validate`、`split_segments`、`tts_text`、`load` / `save` / `update` / `restore` / `trash`、`startup_cleanup`、`subdir`) | 專案資料模型與存取:分句(句尾標點、換行、強制停頓)、停頓與語速正規化、標註驗證與 `字[:注音]` 產生、原子寫入 + history 10 版、`.trash/` 30 天、專案層級鎖 | 不合法丟 `ProjectError`(繁中 `.errors`);`ProjectNotFound`(404)、`ProjectExists`(409);見 `data.md`「專案資料」 | 〔觀察〕CHG-20261022-04 T2、T6、T8 |
| `timeline.layout(project)` | 純函式:片段起訖(變速後長度 + 停頓)、字幕 cue(超過 24 字切段)、畫面軌起訖、總長(不含片尾停頓) | `{segments:[{id,start,end}], cues:[{segment,start,end,text}], images:[{id,file,start,end}], duration}`;沒有音檔的片段長度 0、不丟錯 | 〔觀察〕CHG-20261022-04 T4;預覽與匯出共用 |
| `tts_service`(`load_voice`、`cache_key`、`dirty_segments`、`synth`、`apply_speed`) | 只合成髒片段、內容雜湊快取、逐句 emit、語速後製(ffmpeg `atempo`,單一濾鏡) | `synth(project, emit)` 就地更新片段 `audio`、不存檔,回 `{"synthesized", "cached"}`;每句 emit `{"type": "segment", "id", "seconds", "cached", "audio"}`;`apply_speed(segment, project)` 回變速後音檔路徑;失敗丟 `SynthError`(繁中、不含路徑) | 〔觀察〕CHG-20261022-04 T3;`audio` 欄位為 DIR-5 修正 M-1 |
| `breezy_worker.py` | 在 BreezyVoice venv 執行的子程序:`synth`(逐句合成、模型載入一次)、`annotate`(逐字注音) | 見下方「BreezyVoice worker 介面」 | 〔觀察〕CHG-20261022-04 T1 |
| `media.render_timeline(project, layout)` | 依 layout 匯出 MP4 + SRT;片段音檔經 `tts_service.apply_speed` 取變速後檔案、以 `O_NOFOLLOW` 開啟經 stdin 交給 ffmpeg 解碼(以 `-t` 截在片段需要的長度);畫面以 concat demuxer 依 0.1 秒格排程 | `(mp4, srt)`;輸出以 `taiwan_ai.reserve()` 佔位;未合成或路徑不安全丟 `RenderError`(`.segments` 列出片段);逾時 max(600, 音訊秒數 × 2) 秒 | 〔觀察〕CHG-20261022-04 T5;`-t` 解碼截止為 DIR-5 修正 M-3 |
| `media.sniff_binary(ext, head)` | 依檔頭位元組判斷內容與副檔名是否相符(png、jpg、webp、wav、mp3、m4a) | `/api/upload` 與「送到素材庫」節點共用,規則單一來源 | 〔觀察〕DIR-5 修正 M-5(由 `studio_server._sniff_binary` 搬出,規則不變) |

〔觀察〕設定中的 `roles.image`(`model`、`steps`)與 `roles.voice`(`wav`、`text`)由 `media.generate_image()`、`taiwan_ai.speak()` 在每次呼叫時經 `taiwan_ai.role_config()` → `settings.load()` 讀取(CHG-20261026-01 T1、T2);網頁節點與 MCP 工具共用這兩個函式,MCP 不需另改即跟上。`--base-model` 仍固定 `schnell`,設定頁沒有對應欄位〔觀察〕;換成 dev 系模型時以錯誤基底執行(CHG-20261026-01 代決 3)。

## 元件關係圖

```mermaid
classDiagram
  class studio_server {
    create_app(runner, token, static_dir, root, ollama_tags)
    main()
    中介層:Host / Origin / X-Studio-Token
  }
  class executor {
    MAX_NODES = 100
    validate(graph) list~Error~
    run(graph, emit, cancel)
  }
  class nodes {
    PORT_TYPES
    FILE_KINDS
    REGISTRY: dict~str, NodeSpec~
  }
  class settings {
    DEFAULTS
    DB_PATH = config/studio.db
    load() dict
    save(cfg)
    validate(cfg, available_models) list
    load_meta() dict
    voice_options() list
  }
  class heavy_lock {
    LOCK_PATH = outputs/.heavy.lock
    heavy_lock(timeout, on_wait)
  }
  class taiwan_ai {
    OUT / OLLAMA / MFLUX / BREEZY
    TW: str
    model(role) str
    role_config(role) dict
    chat(role, prompt, system, think) str
    local_open(req, timeout)
    unload_llms()
    speak(text) Path
    to_tw(text) str
    stamp() str
    reserve(prefix, suffix, also) Path
  }
  class media {
    VIDEO_W=1920 / VIDEO_H=1080
    FLUX_W=1360 / FLUX_H=768
    generate_image(prompt, w, h) Path
    to_16x9(path) Image
    add_title(img, title) Image
    make_cover(prompt, title) Path
    split_sentences(script) list
    make_srt(script, seconds, path)
    wav_seconds(path) float
    make_video(image, audio, script) Path
  }
  class local_models_mcp {
    busy()
    reason() / taiwanize() / generate_image()
    speak() / make_video() / list_models()
  }
  class mcp_registry {
    NAME = "local-models"
    CLIENTS / ACTIONS
    paths() dict
    server_argv() list
    status(check) dict
    plan(client, action) dict
    apply(client, action) dict
    redact_env(text, known) str
    scrub(text) str
  }
  studio_server ..> executor
  studio_server ..> mcp_registry
  studio_server ..> nodes
  studio_server ..> settings
  studio_server ..> taiwan_ai
  executor ..> nodes
  executor ..> heavy_lock
  nodes ..> media
  nodes ..> taiwan_ai
  local_models_mcp ..> heavy_lock
  local_models_mcp ..> settings
  local_models_mcp ..> media
  local_models_mcp ..> taiwan_ai
  media ..> taiwan_ai
  taiwan_ai ..> settings
```

## 介面 / API 契約

### HTTP API(〔觀察〕`studio_server.py` 檔頭與實作)

所有回應帶 `X-Content-Type-Options: nosniff` 與 `Content-Security-Policy: default-src 'self'; media-src 'self' blob:; img-src 'self' blob: data:`;不送任何 CORS 標頭。

| 方法與路徑 | 輸入 | 成功 | 錯誤 |
|------------|------|------|------|
| `GET /` | — | 首頁 HTML,`__STUDIO_TOKEN__` 換成啟動權杖 | — |
| `GET /static/{path}` | — | `static/` 之下的真實檔案 | 404 |
| `GET /api/nodes` | — | `{"nodes": [NodeSpec 宣告…], "port_types": [...]}` | — |
| `POST /api/run` | 本文 = 流程圖 JSON(≤ 4 MB) | 200 `{"run_id"}` | 400 `{"errors": [{node_id, port, message}]}`;409 已有執行中的 run;413 |
| `GET /api/run/{id}/events` | `Last-Event-ID`(選填) | SSE:`id: <序號>` + `data: <事件 JSON>`,送完 `finished` 結束;保留最近 20 次 run 供重連 | 404 |
| `POST /api/run/{id}/cancel` | — | 設定取消旗標 | 404 |
| `POST /api/upload?name=<原始檔名>` | 本文 = 檔案原始位元組(非 multipart) | 200 `{"file_id": "uploads/<uuid>.<ext>", "kind", "url": "/files/outputs/uploads/<uuid>.<ext>"}` | 400 副檔名不在白名單、內容嗅探不符、文字非 UTF-8 / 含 NUL / 超過 20000 字;413 超過 200 MB(串流累計,超過即中止並刪暫存檔) |
| `GET /files/{path}` | `path` 第一層必須是 `outputs` | 已知媒體型別 inline;文字 `text/plain; charset=utf-8`;其他 `application/octet-stream` + attachment | 404:絕對路徑、任一層以 `.` 開頭、指向 `outputs/` 外、非一般檔案 |
| `GET /api/workflows/{name}` | — | `{"name", "graph"}` | 400 名稱不合規則;404;422 檔案損壞或超過 4 MB |
| `PUT /api/workflows/{name}` | `{"graph", "overwrite": bool(預設 false)}` | 寫入 `workflows/<name>.json`(暫存檔 `.wf-<uuid>.tmp` 後以 `os.link` 排他建立,或 `os.replace` 覆寫) | 400 名稱不合規則或 graph 未通過 `executor.validate`;409 已存在且未帶 `overwrite:true`,或已有只差大小寫(casefold 相等)的其他名稱(不論 overwrite);413;500 目錄不安全 |
| `GET /api/config` | — | `settings.load()` 結果 + `meta: {imported_from, imported_at}`(有 `_warnings` 時 `meta` 為 `{}`) | — |
| `PUT /api/config` | 設定 JSON(≤ 256 KB;`_warnings`、`meta` 會被忽略) | 200 `{"saved": true, "config"}` | 413;422 非物件或 `validate` 有錯;503 Ollama 連不上(不儲存);500 `save()` 失敗(本文不含本機路徑) |
| `GET /api/models` | — | `{"ollama": {"reachable", "models", "error"?}, "mflux": {"installed"}, "breezyvoice": {"installed"}, "voices": [...]}` | Ollama 連不上時 `reachable=false`,仍回 200 |
| `POST /api/mcp/status`(CHG-20261022-02) | `{"check": bool}`(可省略或空本文,預設 false;≤ 4 KB) | 200 `mcp_registry.status(check)`;`check:false` 不呼叫 `claude mcp get` | 413;422 不是 JSON 物件、有 `check` 以外欄位、`check` 不是布林;409 另一個 MCP 操作執行中 |
| `POST /api/mcp/plan`(CHG-20261022-02) | `{"client", "action"}`(≤ 4 KB) | 200 `mcp_registry.plan()`:`{"argv", "files", "current", "warnings"}`(只組指令、不執行) | 413;422 多餘欄位、列舉外的值、狀態不允許該動作;409 |
| `POST /api/mcp/apply`(CHG-20261022-02) | `{"client", "action", "confirm": true}`(≤ 4 KB) | 200 `mcp_registry.apply()`:`{"ok", "exit_code", "output", "status"}`;CLI 失敗或結果狀態不符仍回 200(`ok: false`) | 413;422 多餘欄位;400 `confirm` 不是 JSON `true`(不呼叫 CLI);422 列舉外的值、狀態不允許;409 |

- 〔觀察〕**安全中介層**:所有請求(含 GET、`/files/*`、SSE)的 `Host` 必須是 `127.0.0.1:7860` 或 `localhost:7860`;GET / HEAD 以外另要求 `Origin` 完整等於 `http://127.0.0.1:7860` 或 `http://localhost:7860`(缺少或 `null` 拒絕,`OPTIONS` 一律拒絕),且 `X-Studio-Token` 等於啟動權杖(`hmac.compare_digest`)。不符一律 403。
- 〔觀察〕啟動權杖每次啟動以 `secrets.token_urlsafe(32)` 產生,只存在記憶體與首頁 HTML。
- 〔觀察〕工作流程名稱規則(PR #44):名稱視為 Unicode 字串逐字比對中英日白名單——ASCII 英數、`-`、`_`、CJK 統一漢字(U+4E00–U+9FFF)與擴充 A(U+3400–U+4DBF)、平假名(U+3041–U+3096)、片假名(U+30A1–U+30FA)、「ー」、「々」,1~64 字,不做正規化。
- 〔觀察〕`outputs/`、`outputs/uploads/`、`workflows/` 逐層不得是符號連結、解析後必須等於 repo 內對應目錄;不符時寫入回 500、讀取回 404,且不建立任何東西。
- 〔觀察〕錯誤訊息與事件字串一律經 `scrub()`,不送本機絕對路徑;未預期例外回 500,本文不含例外內容。

### 流程圖格式與執行事件(〔觀察〕`executor.py` 檔頭)

- 流程圖:`{"nodes": [{"id": 字串, "kind", "params", "pinned"?, "outputs"?, "pos"?}], "edges": [{"from": {"node", "port"}, "to": {"node", "port"}}]}`;`executor.validate` 只讀 `nodes`、`edges`,其他頂層鍵(例如 `workflows/default.json` 的 `steps`)與節點的 `pos` 不影響驗證。
- 驗證錯誤 `Error(node_id, port, message)`:未知種類、未宣告的參數、參數不合格(`Param.check`)、型別不符、有環、必填未接、格式錯誤、節點數超過 100;釘住的節點只能是「全部輸出埠為 text」且每個輸出都有字串編輯值(≤ 20000 字)。
- 事件:`started`(拓撲順序)→ 每個節點 `running` / `waiting` / `done`(outputs)/ `error` / `skipped`(`pinned` 或 `upstream`)/ `cancelled` → `finished`(`ok` / `error` / `cancelled`)。
- `workflows/default.json` 另有頂層 `steps`:`[{"id", "label", "nodes": [節點 id…], "note"?}]`,定義步驟精靈預設步驟 ① 文稿 ② 語音 ③ 封面 ④ 剪輯 ⑤ 匯出(④ 沒有節點,屬 CHG-20261022-04);精靈只讀 `GET /api/workflows/default` 的 `steps`(不新增清單端點,修訂 3 R3-1);前端畫布存檔(`buildGraph()`)保留載入時的 `steps`,移除已不在畫布上的節點 id,原本有節點、移除後變空的步驟刪除,原本就沒有節點的步驟保留(修訂 3 R3-2)。

### 節點登錄表(〔觀察〕`nodes.py`)

| 種類 | 輸入埠 | 輸出埠 | 參數 | 重型 |
|------|--------|--------|------|------|
| `text-input` | — | `text` | `text`(≤ 20000 字) | 否 |
| `file-input` | — | 依 `kind`:`text` / `image` / `audio` 其一 | `file`(相對 `outputs/`,≤ 512 字)、`kind`(text / image / audio) | 否 |
| `write-script` | `topic: text` | `text` | `style`(五種風格)、`minutes`(0.5–10,步進 0.5)、`notes`(≤ 2000 字) | 是 |
| `revise` | `text: text` | `text` | `instruction`(≤ 2000 字) | 是 |
| `taiwanize` | `text: text` | `text` | — | 是 |
| `speak` | `text: text` | `audio` | — | 是 |
| `cover-prompt` | `title: text`、`script: text`(選填) | `text` | `extra`(≤ 2000 字) | 是 |
| `generate-image` | `prompt: text` | `image` | `width`、`height`(256–2048,16 倍數;預設 1360×768) | 是 |
| `add-title` | `image: image`、`title: text` | `image` | — | 否 |
| `make-video` | `image: image`、`audio: audio`、`script: text`(選填) | `video` | `subtitles`(預設 true) | 是 |
| `send-to-library` | `text`、`image`、`audio`(皆選填,至少一個) | `text`(結果訊息) | `project`(專案名稱,`names.py` 白名單;專案須已存在) | 否 |

- 〔觀察〕`send-to-library`(CHG-20261022-04 T8):文字存成 `.txt`;圖片與音訊只收 `outputs/` 之下的一般檔案——檔案本身與其在 `outputs/` 之下的每一層目錄都不得是符號連結(解析前逐層 `lstat`,DIR-5 修正 M-4)、解析後在 `outputs/` 內、`O_NOFOLLOW` 開啟、副檔名在 `project.ASSET_EXTS`、≤ 200 MB、檔頭經 `media.sniff_binary` 與副檔名相符(DIR-5 修正 M-5);複製到 `assets/<新 id>` 後經 `project.update()` 登記,失敗刪除複製檔。
- 〔觀察〕埠型別只有 `text`、`image`、`audio`、`video`;文字輸出是字串,其餘是 `outputs/` 下的檔案路徑。節點本身不取重型鎖,由 `executor` 對 `heavy=True` 的節點包 `heavy_lock()`。
- 〔觀察〕`write-script` 只做推理模型起稿(字數 = 分鐘 × 250),台灣用語潤飾拆成 `taiwanize` 節點(舊版 `write_script` 一次做完)。

### 設定模組介面(〔觀察〕`settings.py`)

| 介面 | 契約 |
|------|------|
| `load() -> dict` | 回 `{"roles", "review_seats"}`;路徑不安全、資料庫不是 SQLite / 損壞、`schema_version` 不明、內容不合法 → `DEFAULTS` 副本 + `_warnings`(同時寫 stderr),不修改資料庫;資料庫不存在時依 `config/models.json` 決定回預設值或一次性匯入 |
| `save(cfg) -> None` | 先 `validate(cfg, None)`,不合格丟 `ValueError(錯誤清單)`;`BEGIN IMMEDIATE` 單一交易 DELETE + INSERT 整批改寫 `roles`、`review_seats`,例外即 ROLLBACK;路徑不安全、非 SQLite、版本不明丟 `ValueError`,不寫入、不覆寫、不重建 |
| `validate(cfg, available_models) -> list[str]` | 逐欄繁中錯誤;`available_models=None` 只做結構檢查;否決席(risk、security、compliance)不得為 `glm4:*`;`codex` 只限否決席;暫停的 27B 不得用於審議席位;聲線檔限 `tools/BreezyVoice/data/`、`outputs/uploads/` 之下 |
| `load_meta() -> dict` | 唯讀取得 `imported_from`、`imported_at`;不建立、不匯入、不寫入 |
| `voice_options() -> list[str]` | 允許目錄下可選的 `.wav`(repo 相對路徑,不跟隨符號連結) |

- 〔觀察〕儲存層:既有資料庫先以唯讀 URI(`mode=ro`)讀 `meta.schema_version`,此階段不下 PRAGMA;版本已知或新建時才以讀寫模式開啟並設 `journal_mode=WAL`、`busy_timeout=5000`;資料庫檔由本模組排他建立(權限 0600)。
- 〔觀察〕SQL 一律參數化(`?` 佔位),表名、欄名寫死在 `settings.py`;其他模組不得直接開資料庫或讀 `config/models.json`。
- 〔觀察〕路徑規則:`config/`、`studio.db`、`-wal`、`-shm` 逐層不得是符號連結、解析後必須位於 repo 內。

### MCP 工具(〔觀察〕`local_models_mcp.py`)

| 工具 | 輸入 | 輸出 | 錯誤行為 |
|------|------|------|----------|
| `reason` | `prompt: str`, `think: bool = True`, `heavy: bool = False`(true 時用 `reason_heavy` 角色) | 台灣正體文字 | 空回答 → `RuntimeError` |
| `taiwanize` | `text: str` | 台灣正體文字 | Ollama 錯誤直接拋出 |
| `generate_image` | `prompt: str`, `width: int = 1024`, `height: int = 1024` | PNG 絕對路徑 | 非 16 倍數 → `ValueError`;mflux 失敗 → `CalledProcessError` |
| `speak` | `text: str` | WAV 絕對路徑 | 子程序失敗 → `CalledProcessError`(子程序 stderr 被丟棄;本行程 stderr 印離線提示) |
| `make_video` | `image_path: str`, `audio_path: str`, `script: str = ""` | MP4 路徑 | 檔案不存在 → `FileNotFoundError`;非 WAV 或壞掉的 WAV + script → 繁中 `ValueError`(ADP-7;CHG-20261026-01 T4 前為 `wave.Error`) |
| `list_models` | — | JSON 字串:`roles`(reason、taiwan、reason_heavy 取自設定;image 的 `model` 也取自設定 `roles.image.model`(CHG-20261026-01 T2);另有 speech)、`loaded_now`、`outputs` | Ollama 沒開 → `URLError` |

〔觀察〕工具名稱、參數、回傳格式在 CHG-20261022-01 前後相同;每次工具呼叫重讀設定,重型工具在 `heavy_lock()` 內執行。

### 外部引擎契約(〔觀察〕)
- Ollama:`POST /api/chat`、`POST /api/generate`(卸載)、`GET /api/tags`、`GET /api/ps`
- mflux:`--model <roles.image.model> --base-model schnell --steps <roles.image.steps> --width --height --prompt --output`;環境變數為目前環境加 `HF_HUB_OFFLINE=1`(CHG-20261026-01 T2)
- ffmpeg(imageio-ffmpeg 內附):`-y -loop 1 -i <圖> -i <音>`(+ `-i <srt> -map 0:v -map 1:a -map 2:s -c:s mov_text -metadata:s:s:0 language=chi`)`-vf scale…,pad… -c:v libx264 -tune stillimage -r 10 -pix_fmt yuv420p -c:a aac -b:a 192k -shortest`(WAV 時再加 `-t <秒數>`)`<輸出>`(CHG-20261026-01 T5)
- BreezyVoice:`single_inference.py --content_to_synthesize --speaker_prompt_audio_path <roles.voice.wav 絕對路徑> --speaker_prompt_text_transcription <roles.voice.text> --output_path`,cwd = `tools/BreezyVoice`;環境變數只有 `PYTHONUTF8`、`PATH`、`HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`(ADP-12)
- BreezyVoice worker(CHG-20261022-04):`tools/BreezyVoice/.venv/bin/python breezy_worker.py synth --jobs <json> --out <dir>` 或 `annotate --input <json>`,argv list、cwd = `tools/BreezyVoice`、`env = taiwan_ai.BREEZY_ENV`(見下節)

### 專案 API(〔觀察〕`studio_server.py` 檔頭;CHG-20261022-04 T6)

沿用同一個安全中介層(Host / Origin / Token);{p} 為專案名稱(`names.py` 白名單,含 `.`、`..`、`%2e%2e`、`/` 一律 400)。本文一律 JSON 物件(上限 `MAX_PROJECT_BODY`,不認得的欄位 422)。錯誤 `{"error", "errors"?, "segments"?}`,經 `scrub()`、不含本機絕對路徑。修改類端點一律「載入 → 修改 → 存檔(舊版移入 history)」並回「專案檢視」View = `{"project", "layout", "forced_breaks"}` + `"version"`。

| 方法與路徑 | 輸入 | 成功 | 錯誤 |
|------------|------|------|------|
| `GET /api/projects` | — | `{"projects": [{"name", "updated"}]}` | — |
| `POST /api/projects` | `{"name", "script"?}` | 201 View;語速與停頓預設取自設定資料庫 `voice_defaults` | 400 名稱;409 已存在;422 |
| `GET /api/projects/{p}` | — | View(合成期間疊上本次已完成的句子,不存檔,DIR-5 修正 M-1) | 404 |
| `PUT /api/projects/{p}` | `{"script"?, "forced_breaks"?, "defaults"?}` | View;帶 script 或 forced_breaks 時重新分句,文字相同的片段沿用 id、停頓、語速、標註與音檔 | 422 |
| `DELETE /api/projects/{p}` | `{"confirm": true}` | 移到 `.trash/<名稱>-<時間戳>-<4 碼>/` | 400 未確認;409 合成或匯出中 |
| `GET /api/projects/{p}/history` / `POST …/restore` | `{"version"}` | 版本清單 / View(還原 = 一次存檔) | 404 |
| `PATCH /api/projects/{p}/segments/{id}` | `{"text"?, "pause_after"?, "speed"?, "annotations"?, "split_at"?, "merge_next"?, "audio_file"?, "audio"?}` | View;改文字或標註時清除自錄音檔;`audio_file` 以上傳的 WAV 取代該句(只看前 12 位元組 RIFF/WAVE,長度以 `media.wav_seconds()` 取得) | 404;422(注音、停頓、語速、非 WAV「取代語音只接受 WAV 音檔」) |
| `POST /api/projects/{p}/assets` | `{"file_id", "label"?}` | View + `{"asset"}`(素材庫匯入;CHG 修訂 4 補記) | 404;422 |
| `POST /api/projects/{p}/images` | `{"asset"}` 或 `{"file_id"}`,`start`?、`end`? | View + `{"image"}` | 404;422 |
| `PATCH` / `DELETE /api/projects/{p}/images/{id}` | `{"start"?, "end"?}` / — | View;刪除只移除時間軸項目,不刪素材檔(CHG 修訂 4 補記) | 404;422 |
| `GET /api/projects/{p}/media/{id}` | — | 只依 id 解析到該專案 `assets/<id>.<ext>` 或 `audio/<id>.wav`(含合成中途已完成、尚未寫回的音檔);白名單副檔名、`nosniff`;送合成原檔(變速前),預覽以 `playbackRate` 套語速 | 404:不存在、他專案、符號連結 |
| `POST /api/projects/{p}/synth` | — | `{"run_id", "events"}`;SSE 事件 waiting、segment(`id`、`seconds`、`cached`、`audio`)、error、finished(`status`、`synthesized`、`cached`、`saved`);結束(含中途失敗)時一次寫回,只更新同 id、合成文字未變、期間沒改成自錄音檔的片段 | 404;409 已有工作 |
| `POST /api/projects/{p}/render` | — | `{"run_id", "events"}`;finished 帶 `mp4`、`srt`(`outputs/…`) | 400 未合成或過期片段(`segments` 列出);409 |
| `POST /api/annotate` | `{"text": 1~500 字}` | `{"text", "zhuyin": [每字注音或 null]}`;`heavy_lock` 內執行,逾時 60 秒 | 422;500 worker 失敗;409 |

- 〔觀察〕synth、render、annotate 與 `/api/run` 共用同一個「執行中」旗標(`Runs.active`),第二個一律 409;`projects/` 不經 `/files/*` 提供。
- 〔觀察〕合成中途可試聽(DIR-5 修正 M-1):伺服器在該次合成期間以記憶體記錄「已完成、尚未寫回」的片段音檔(`{片段 id: (合成當時的含標註文字, audio)}`),疊到 View、layout 與 media 端點上;前端收到 segment 事件即重新讀取 View。結束時一次 `merge_audio()` 寫回後清除記錄,一次合成最多新增一個 history 版本。

### BreezyVoice worker 介面(〔觀察〕`breezy_worker.py`、`tts_service.py`;CHG-20261022-04 T1、T3)

- `synth --jobs <json> --out <dir>`:jobs 檔 = `{"prompt_wav": 聲線檔絕對路徑, "prompt_text": 聲線逐字稿(必填,不走 Whisper 後備), "model"?: 預設 MediaTek-Research/BreezyVoice-300M, "jobs": [{"id": ^[0-9a-f]{8,32}$, "text": 1–500 字}]}`;jobs 檔由 `tts_service` 寫在 `projects/<名稱>/tmp/`(`O_EXCL`、`O_NOFOLLOW`、0600),用完即刪,使用者文字不進 argv。
- `CustomCosyVoice` 只建構一次;`G2PWConverter` 建立後把 `num_workers` 屬性設為 0(建構子把 0 當假值忽略);每句各自呼叫 `single_inference`,寫完 `<out>/<id>.wav` 即在 stdout 印一行 `{"id", "seconds"}`;全部完成後寫 `<out>/result.json`。標註以聲調符號書寫,送進 BreezyVoice 前轉成數字聲調。
- `annotate --input <json>`:`{"text": 1–500 字}` → stdout 一個 JSON 陣列(每字聲調符號格式注音,非中文字 null);只用 G2PW。
- 環境由 `tts_service` 直接傳入 `taiwan_ai.BREEZY_ENV`(含 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`,修訂 3 R3-3,ADP-12);模型快取缺失時離線失敗(結束碼 3,訊息提示需在建置期允許連網時先下載一次),不下載。結束碼:2 輸入不合法、3 模型載入失敗、4 合成失敗;錯誤訊息繁中、不含本機絕對路徑。
- 逾時(`tts_service`):第一個進度行最多等 `MODEL_LOAD_TIMEOUT`(180 秒,含模型載入)+ `SENTENCE_TIMEOUT`(120 秒),之後每句 120 秒;整批上限 = 180 + 句數 × 120 秒;逾時即終止子程序(CHG 修訂 4,RS-7)。
- 合成快取鍵 = SHA-256(JSON 陣列 [含標註的合成文字, 聲線檔 SHA-256, 聲線逐字稿 SHA-256, 模型名]);合成音檔以鍵前 32 碼內容定址存於 `audio/`,命中即重用;變速結果以「音檔 id + 語速」另存 `audio/<id>-s<語速×100>.wav`。聲線每次 `synth()` 經 `taiwan_ai.role_config("voice")` 讀一次。

## 設計決策與取捨

| 決策 | 選項 | 取捨理由 | 等級 |
|------|------|----------|------|
| ~~Gradio 網頁~~(已由下方 CHG-20261022-01 各列取代) | 網頁 vs CLI | 文案需反覆編輯,文字框比終端機好用 | 〔推論〕`PODCAST_STUDIO.md §5` |
| 文案 `think=False` | 開 vs 關思考 | 寫作不需長推理;開思考吃 context、慢 3–5 倍 | 〔推論〕`§5` |
| 每分鐘 250 字 | — | 台灣華語播報常見語速 | 〔推論〕`§5` |
| 標題用字型疊上 | FLUX 畫字 vs 字型疊加 | FLUX 無法正確產生中文字 | 〔推論〕`§5` |
| 生圖前卸載 LLM | 同時載入 vs 卸載 | FLUX 峰值 14–16 GB,與 LLM 並存超出記憶體 | 〔推論〕`§5` |
| ~~2 fps~~ 10 fps、`-tune stillimage` | 2 fps vs 10 fps | 靜態畫面,降低檔案大小與編碼時間;CHG-20261026-01 T5 改為 10 fps:影格間隔 0.1 秒,`-t` 截止誤差才能 ≤ 0.1 秒(2 fps 實測誤差達 0.5 秒) | 〔推論〕`§5`;〔觀察〕CHG-20261026-01 T5 |
| imageio-ffmpeg | 系統 ffmpeg vs 內附 | 沒有 Homebrew,免額外安裝 | 〔推論〕`§5` |
| 軟字幕 mov_text + `.srt` | 硬字幕 vs 軟字幕 | 已確認決策 | 〔推論〕`§8 #3` |
| 依字數比例分配字幕時間 | 對齊模型 vs 比例 | 沒有語音對齊模型;誤差約 1–2 秒 | 〔觀察〕`media.py:94` 註解 |
| MCP 全部重型工具共用一把鎖 | 並行 vs 序列 | 24 GB 同時只撐得住一個重型工作;CHG-20261022-01 起改為與網頁共用的跨行程檔案鎖 | 〔觀察〕`local_models_mcp.busy()` 註解 |
| `tools/` 不入 git,以 patches + 腳本重建 | vendor vs 重建 | 已確認決策 | 〔推論〕`§8 #4` |
| 數字正規化自製 `tn/` | WeTextProcessing vs 自製 | pynini 無 macOS ARM wheel | 〔觀察〕`setup_breezyvoice.sh:16` 註解 |
| 網頁框架:FastAPI + 原生 JS | FastAPI + 原生 JS vs Gradio + 嵌入 vs ComfyUI | 本機沒有 node/npm,前端必須免建置、離線;節點編輯器與 HTML5 拖放用原生 JS 最直接 | 〔觀察〕CHG-20261022-01(使用者選定) |
| 節點編輯器:Drawflow 0.0.60 vendored | Drawflow vs LiteGraph.js vs 自寫 SVG | 單一 JS + CSS、MIT、免建置;存進 `static/vendor/` 並記 SHA-256,不從 CDN 載入 | 〔觀察〕CHG-20261022-01(代決,確認閘覆核) |
| 伺服器端依拓撲順序逐一執行 + SSE | 伺服器端 vs 前端逐節點呼叫 | 24 GB 同時只撐一個重型工作;前端關掉分頁也不中斷 | 〔觀察〕CHG-20261022-01 |
| 四種埠型別,前後端各檢查一次 | — | 現有資料只有 text / image / audio / video;前端可被繞過,執行前伺服器再驗證 | 〔觀察〕CHG-20261022-01 |
| 人工修改以「釘住」節點保留 | pinned vs 每次重跑覆蓋 | FR-2 要保留人工修改;只有全文字輸出的節點可釘住 | 〔觀察〕CHG-20261022-01(代決) |
| MCP 每次工具呼叫重讀設定 | 每次重讀 vs 啟動時讀一次 | 使用者期待改了就生效 | 〔觀察〕CHG-20261022-01 |
| 跨行程鎖 `fcntl.flock` | 檔案鎖 vs 行程內鎖 | 網頁與 MCP 是兩個行程;持鎖行程結束時由作業系統釋放 | 〔觀察〕CHG-20261022-01(ADP-2) |
| 卸載目前載入的全部模型 | 卸載設定中的模型 vs `/api/ps` 全部 | heavy 模型會殘留(ADP-1) | 〔觀察〕CHG-20261022-01 |
| 移除 CLI 與 Gradio 版 | 保留並存 vs 刪除 | 使用者決定不需要 CLI;新版涵蓋 FR-1~7;git 可回復 | 〔觀察〕CHG-20261022-01 |
| 防跨站:Host + Origin + 啟動權杖 | 不防 vs 檢查 | 瀏覽器中任何網站都能對 `127.0.0.1:7860` 發請求(CSRF / DNS rebinding) | 〔觀察〕CHG-20261022-01(代決) |
| 上傳上限 200 MB、內容嗅探、伺服器產生檔名 | — | 不信任用戶端檔名與 `Content-Type` | 〔觀察〕CHG-20261022-01(代決) |
| 沿用埠 7860 | 沿用 vs 換新 | 使用者習慣與 `launch.json` | 〔觀察〕CHG-20261022-01(代決) |
| 預設範本兼作步驟精靈預設步驟 | 範本與步驟分開 vs 同一檔 | 修訂 1:畫布移入「設定 › 進階流程」,用來調整精靈步驟;`workflows/default.json` 頂層 `steps` 供 CHG-20261022-04 的精靈讀取 | 〔觀察〕CHG-20261022-01 修訂 1、T13 |
| 模型設定改存 SQLite `config/studio.db`(修訂 2) | `config/models.json` vs SQLite | 使用者選定;不入 git,網頁存設定不弄髒工作樹;`load/save/validate` 簽名不變 | 〔觀察〕CHG-20261022-01 修訂 2(使用者選定) |
| WAL + `busy_timeout=5000` + `BEGIN IMMEDIATE`(修訂 2) | 每次寫整檔 vs SQLite 交易 | 網頁寫、MCP 讀可能同時發生;版本不明或非 SQLite 時不下會寫入的 PRAGMA | 〔觀察〕CHG-20261022-01 修訂 2(代決) |
| 舊 JSON 一次性匯入後停用(修訂 2) | 持續雙寫 vs 一次性匯入 | 匯入不刪原檔;`config/models.json` 移出 git | 〔觀察〕CHG-20261022-01 修訂 2(代決) |
| 工作流程名稱中英日白名單 | 只限 ASCII vs Unicode 字母數字 vs 中英日白名單 | 使用者要能用中文命名,多次審議無交集時採最低限度 | 〔觀察〕CHG-20261022-01 派發記錄(使用者 2026-10-26 指示,PR #44) |
| CI 平台:GitHub Actions 代管 ubuntu runner | 代管 vs self-hosted runner | Mac 不常駐外部任務程式、CI 不搶 GPU 記憶體;本機模型審議在開 PR 前於本機跑,判決以 PR 留言帶進 CI(代決) | 〔觀察〕CHG-20261022-03 |
| 治理腳本 vendored 進 `.ai-sdlc/` | 從 `kamira/ai-skills` checkout vs vendored | 上游 repo 為 private,跨 repo checkout 需秘密;vendored v1.66.0 不需秘密,以 `SHA256SUMS` 驗完整性;腳本隨本 repo 公開(使用者確認閘同意) | 〔觀察〕CHG-20261022-03 |
| commit 引用 CHG 編號,只改 `docs/knowledge/` 豁免 | 全部要 CHG vs knowledge 豁免 | knowledge 條目是帳本寫入,不是變更 | 〔觀察〕CHG-20261022-03 |
| plan-check 只查 PR 改到、尚未完成驗收的 CHG | 全部 CHG vs PR 改到的 CHG | 已收尾的舊 CHG 不該因新規則變紅;狀態寫明「計畫待補」的草稿只警告 | 〔觀察〕CHG-20261022-03 |
| lint 範圍 `ruff check --select E9,F` | ruff 全規則 vs `E9,F` | 既有程式 `E9,F` 全過;全規則會把既有風格債變成紅燈,更嚴格的規則另開 CHG | 〔觀察〕CHG-20261022-03 |
| 本機審議閘:本機跑、CI 驗留言 | CI 內跑模型 vs 本機跑、CI 驗留言 | 代管 runner 連不到本機 Ollama;`review` job 只驗擁有者留言、目前 head sha、六席意見留言,以及排在其後、同 CHG 的統整判定留言為 `merge`(CHG-20261028-02 起不再看否決席) | 〔觀察〕CHG-20261022-03;CHG-20261028-02 |
| CD 定義為本機部署腳本 `scripts/deploy_local.sh` | 不做 vs 本機部署腳本 vs 發 release | 產品只在本機執行,沒有遠端環境;部署後驗證只綁本機(fail-closed) | 〔觀察〕CHG-20261022-03 |
| main 保護以 ruleset 強制 | ruleset vs 只靠約定 | repo 已公開,ruleset 免費可用;要求四項 check 通過、禁止直接 push 與 force push、只允許 squash | 〔觀察〕CHG-20261022-03 |
| security、compliance 審議席由 Codex 擔任 | 本機 Ollama vs Codex(雲端,`-s read-only`) | 實審中 8B / 27B 輸出截斷或誤判,Codex 回答證據具體;送審資料送出本機屬使用者明示例外,前提是 repo 已公開 | 〔觀察〕DIR-1 第 9 條(使用者 2026-10-23 指示) |
| 暫停 27B 審議 | 27B vs Codex 擔任高風險 risk 席 | 使用者指示暫不使用 27B;`scripts/local_review.py` 預設仍含 27B,暫停期間須帶 `--codex-seats risk,security,compliance` | 〔觀察〕DIR-1 第 10 條(使用者 2026-10-23 指示) |
| Codex 席以 PR head 唯讀檢視讀完整程式 | 整份 diff 內嵌 vs 分檔送審 vs PR head 唯讀檢視 | Codex 席沿用 Ollama 的 `num_ctx` 截短時,大 CHG 讓 diff 被截到空(PR #76);Codex 以 `-s read-only` 執行,可唯讀執行 `git show`、`git diff`。diff 經過濾後 ≤ 30000 字內嵌完整 diff,超過改用 `.review/` 下以 head sha 建立的 `--no-checkout` detached worktree(不寫出追蹤檔以免依 PR 的 `.gitattributes` 觸發本機 filter、停用 hook、比對 HEAD、審完移除;Codex 只以 `git show`、`git diff --no-textconv --no-ext-diff` 讀取);本次與舊檢視共用安全清理:只有 `.git` 才刪指標檔與空目錄並 prune,不用 git 的強制移除與遞迴刪除,否則留下並警告;同名舊目錄另須為登記的 worktree;Ollama 席不變 | 〔觀察〕CHG-20261028-01(使用者 2026-10-28 核准 C-2) |
| 審議席改為意見、單一統整席判定 | 派發者裁決 vs 單一獨立統整席判定 vs 多數決 | 否決席常因送審內容不完整或模型誤讀判 fail / no-answer 卻仍擋 merge;使用者選擇由獨立 Opus medium 子代理讀意見、CHG 與完整程式後判定 `merge` / `fix`;`claude -p` OAuth 過期,故子代理只寫 JSON、由 `scripts/review_decision.py` 驗證後貼出(不呼叫模型,`model`、`effort` 取自參數);標記與 `[verdict]` 行格式不變;非 think 的 Ollama 席 CHG 過大時截短 CHG | 〔觀察〕CHG-20261028-02(使用者 2026-10-28 指示);DIR-6 |
| 27B 逾時 30 分鐘改派 Codex | 重派 27B vs 改派 Codex | 27B 太慢;某階段逾時或累計超過 30 分鐘沒有回答即改派,該席判決的模型欄註明改派 | 〔觀察〕DIR-1(使用者 2026-10-23 規則) |
| 生圖與聲線設定每次呼叫重讀 | 每次呼叫重讀 vs 行程啟動時讀一次 | 與 `taiwan_ai.model()` 一致;MCP 是長駐行程,改了設定要立刻生效 | 〔觀察〕CHG-20261026-01 代決 1 |
| 新增 `taiwan_ai.role_config()` | 新增介面 vs 各處直接讀 `settings.load()["roles"]` | `_role()` 要求 `model` 鍵,`voice` 沒有;設定只經 `settings.load()` 存取 | 〔觀察〕CHG-20261026-01 代決 1 |
| `generate-image` 節點不加 `steps` 參數 | 節點參數 vs 只走設定 | 避免兩個來源互相覆蓋 | 〔觀察〕CHG-20261026-01 代決 2 |
| `--base-model schnell` 維持固定 | 固定 vs 開放設定 | 設定 schema v1 不變;代價是 dev 系模型以錯誤基底執行 | 〔觀察〕CHG-20261026-01 代決 3 |
| mflux 子程序加 `HF_HUB_OFFLINE=1` | 允許下載 vs 離線 vs 只准預設模型 | 不送資料出本機;沒有快取直接失敗,同 ADP-12 對 BreezyVoice 的做法 | 〔觀察〕CHG-20261026-01 代決 4 |
| 移除 `FLUX`、`VOICE_WAV`、`VOICE_TEXT` | 保留 vs 移除 | 保留會留下第二個「看似生效」的來源;預設值只留在 `settings.DEFAULTS` | 〔觀察〕CHG-20261026-01 代決 5 |
| 不新增 `settings.validate` 規則 | 新增 vs 沿用 | 既有規則已限制步數、聲線檔目錄、文字長度與控制字元;argv 為 list、不經 shell | 〔觀察〕CHG-20261026-01 代決 6 |
| 覆寫 `default` 只記錄、不擋 | 只記錄 vs 伺服器拒絕 | 使用者指示範圍只限 `.gitignore`;覆寫後 `deploy_local.sh` 中止,以 `git checkout` 復原 | 〔觀察〕CHG-20261026-01 代決 7 |
| `workflows/` 只追蹤 `default.json`,不改 `deploy_local.sh` | `.gitignore` vs 改存檔位置 vs 改部署判定 | 使用者指示;已忽略的檔案不算工作樹不乾淨 | 〔觀察〕CHG-20261026-01 代決 8 |
| WAV 長度自寫 RIFF/WAVE 解析 | 自寫 `struct` 解析 vs imageio-ffmpeg | imageio-ffmpeg 只附 `ffmpeg`、沒有 `ffprobe`,改用要另開子程序解析文字;自寫不加依賴、精度為取樣等級 | 〔觀察〕CHG-20261026-01 代決 10 |
| WAV 加 `-t <秒數>` 並保留 `-shortest`;非 WAV 沿用 | `-shortest` vs `-t` vs `-shortest -t` vs `+shortest` 旗標 | 實測 `-loop 1` + `-shortest` 對 17 秒的聲音產出約 50 秒的影片;`-t` 才能截止 | 〔觀察〕CHG-20261026-01 代決 11、12 |
| 壞檔丟繁中 `ValueError`、訊息不含路徑 | 沿用 `wave.Error` vs 自訂 | 節點與 MCP 會把錯誤訊息顯示給使用者 | 〔觀察〕CHG-20261026-01 代決 13 |
| 合成粒度 B2:逐句輸出、模型載入一次、G2PW `num_workers = 0` | A 整段 vs B 逐句 vs B2 vs C 每句新行程 | 2026-10-26 實測(10 句、267 字,CPU):B2 總耗時 610.3 秒(A 655.9 / 662.0 秒)、第一段可播放 92.8 秒(A 要等全部完成)、峰值 RSS 12.13 GiB(A 10.78 GiB);B 變慢的原因是 G2PW 每次呼叫以 DataLoader 產生子行程;整段合成本來就逐句推論後串接,逐句輸出不損失跨句韻律 | 〔觀察〕CHG-20261022-04 修訂 2 |
| 停頓以分句合成 + 精確靜音 | 文字插入標點 vs 分句合成插靜音 | 模型沒有停頓指令;靜音長度精確到 0.1 秒;逗號不切,要加長時設強制停頓 | 〔觀察〕CHG-20261022-04 |
| 片段快取以內容雜湊、音檔內容定址 | 每次全部重合成 vs 快取 | 使用者會反覆改少數幾句;鍵含聲線逐字稿,只改逐字稿也會重合成(修訂 3 R3-4) | 〔觀察〕CHG-20261022-04 |
| 語速以 ffmpeg `atempo` 後製 | 模型參數 vs 後製 | BreezyVoice 沒有語速參數;只改語速不重新合成,變速結果另行快取 | 〔觀察〕CHG-20261022-04 修訂 1 |
| 專案檔 JSON + history 10 版 | JSON vs SQLite | 單人、可人工檢視;拖拉編輯容易誤改,保留最近 10 版 | 〔觀察〕CHG-20261022-04 |
| 預覽在瀏覽器排程播放,匯出才用 ffmpeg | 先渲染 vs 即時播放 | 片段都是獨立 WAV;預覽與匯出共用 `timeline.layout()` | 〔觀察〕CHG-20261022-04 |
| 時間軸前端自寫 | 第三方套件 vs 自寫 | 免建置、離線;只需三軌、拖拉空隙與圖片 | 〔觀察〕CHG-20261022-04(代決) |
| 語音預設存設定資料庫 `voice_defaults` 表(schema 2) | 另建設定檔 vs 設定資料庫 | 設定只經 `settings.py`;升級在單一交易內,失敗維持 v1 | 〔觀察〕CHG-20261022-04 修訂 2 |
| 自錄音檔只收 RIFF/WAVE | 全部上傳音檔 vs 只收 WAV vs 轉檔 | `media.wav_seconds()` 只讀得到 WAV 長度;不新增轉檔子程序路徑 | 〔觀察〕CHG-20261022-04 修訂 3 R3-6 |
| 合成中途以記憶體疊加試聽,結束一次寫回 | 每句存檔 vs 記憶體疊加 | 不新增端點;每句存檔會讓 history 10 版被合成中間狀態洗掉 | 〔觀察〕DIR-5 修正 M-1(使用者故事 13) |
| 停頓空隙的點擊範圍以交界為中心至少 12px、層級高於選取片段 | 依停頓寬度 vs 最小點擊範圍 | 停頓可設 0 秒(使用者 2026-10-27),逗號強制停頓預設 0 秒,0 秒空隙是常態 | 〔觀察〕DIR-5 修正 M-2 |
| 第一句另給 180 秒模型載入額度 | 每句 120 秒含載入 vs 另給載入額度 | 改聲調後只重合成一句是主要路徑,實測載入約 70–93 秒,原規定餘裕約 27 秒 | 〔觀察〕CHG-20261022-04 修訂 4(使用者 2026-10-28 核准,RS-7) |
| 專案名稱沿用中英日白名單 | ASCII `^[\w\-]{1,64}$` vs 中英日白名單 | 驗收要建立「夜市的味道」;與工作流程名稱同一套規則(使用者 2026-10-26「中英日即可」) | 〔觀察〕CHG-20261022-04 修訂 4(RS-3) |

## 採用模式

- 〔觀察〕**單檔 PEP 723 script**:每個入口檔自帶依賴宣告,`uv run --script` 執行;沒有 package / `pyproject.toml`。
- 〔觀察〕**共用模組以 `sys.path.insert` + import 重用**(`taiwan_ai` 只是函式庫,CLI 已於 CHG-20261022-01 T2 移除)。
- 〔觀察〕**節點包裝**:`nodes.py` 以 `NodeSpec` 包住既有 `taiwan_ai` / `media` 函式,不改其行為;新功能以新節點種類加入登錄表。
- 〔觀察〕**單一存取點**:模型設定只經 `settings.py`;輸出檔名只經 `taiwan_ai.reserve()`;重型工作只在 `heavy_lock()` 內。
- 〔觀察〕**檔案存取經目錄 fd**(CHG-20261022-04 DIR-5 修正 4):本程式自己對專案資料、`outputs/`、`static/`、`workflows/`、`config/` 的讀寫刪搬一律經 `safe_fs`(dir_fd 逐層 `O_NOFOLLOW`),回應與複製讀同一個 fd;`project.subdir()` 回傳的路徑只交給外部程式(worker、ffmpeg)。外部程式只能以路徑存取的位置(BreezyVoice worker 的 `--out`、mflux 輸出、匯出時 ffmpeg 的暫存與輸出、SQLite 資料庫)列為殘餘風險,見 CHG-20261022-04 修訂 4 補記 20。outputs/ 佔位檔以 `media.open_reserved()` / `save_image()` 經目錄 fd 寫入,匯出與生圖的暫存目錄以 `media._mkdtemp_out()` 建立並保留 fd(修正 5,補記 22)。
- 〔觀察〕**外部引擎一律子程序或 HTTP**,不在行程內載入模型——讓記憶體可由卸載控制。
- 〔觀察〕~~**重複**:mflux 指令在 `taiwan_ai.image()` 與 `media.generate_image()` 各一份~~——`taiwan_ai.image()` 隨 CLI 移除(CHG-20261022-01 T2),只剩 `media.generate_image()`。

## CHG-20261029-01:後製管線、shape worker、合音與合音詞庫

〔觀察〕程式為準;實測依據見 CHG-20261029-01「先實測後製方案」,修訂 1(T10~T14)的實測依據見同 CHG「### 修訂 1 實測」。

- **`breezy_worker.py shape --jobs <json> --out <dir>`**(BreezyVoice venv):jobs `[{id, input, chars, connect, clarity, word_clarity, pitch}]`;輸入檔名限 `--out` 目錄內的一般檔;預設參數原樣複製(不 import numpy);其餘只用 venv 已有的 numpy / scipy / librosa / soundfile,不載入模型、不連網。順序(`shape_audio`):連音(縮短 30–120 ms 字間空隙 + 估計字界交叉淡化)→ 清晰度(整句,方案 C,見下)→ 字詞清晰度(以該值處理整句後的對應樣本換入,邊緣 20 ms 交叉淡化)→ 語調(見下)。每句完成時 stdout 一行 `{"id", "seconds", "pitch_points"}`(修訂 1 T11:`pitch_points` 為實際套用的語調內部點 `[[位置, 半音], …]`,位置與半音取到小數 6 位;沒有語調時為 `null`)。結束碼 2 / 3 / 4。
- **清晰度方案 C**(修訂 1 T10,`breezy_worker.clarity(y, sr, p)`;取代 v2 的「子音弱化 c×24 dB → 頻譜時間平滑 c×120 ms → 共振峰中性化 → 音節對比 → p < 25% 時 2 階低通」):100% 回傳輸入本身;c = 1 − p/100,依序 ① `_lenite`:子音幀(非靜音且 3 kHz 以上能量比為句內前 `CONS_TOP` 35%,遮罩 `CONS_MASK` 30 ms 平滑)的倒頻譜包絡往前後最近母音幀的包絡內插 c × `LEN_W`(1.0),保留細部與原相位,還原原幀能量後整幀衰減 c × `C_ATT`(16 dB)→ ② `_smear_transitions`:轉接區(子音遮罩 > 0.2,或比 150 ms 平滑能量低 `VALLEY_DB` 3 dB 以上的非靜音能量谷,前後各擴 `TRANS_PAD` 0.05 秒)權重 1、其他處權重 `SM_OUT` 0.2,做 c × `SM_S`(0.16 秒)的幅度譜移動平均,相位用原音 → ③ `_neutral_vowel`:每幀包絡往母音幀平均包絡靠 c × `NEU_C`(0.3),每幀能量不變、靜音幀不動 → ④ `_contrast`:往 150 ms 平滑能量靠攏 c × `CONTRAST`(0.3,單幀 ±12 dB)→ ⑤ `_tilt_comp`:以原音判定的母音幀比較原音與處理後的長期平均功率譜,1/3 八度平滑(60 Hz 以下以 60 Hz 計)後的差值限 ±`TILT_MAX`(12 dB),作為全句固定 EQ 套回 → ⑥ RMS 對齊原音。輸出長度不變,不使用低通。
- **語調**(修訂 1 T11):`expand_points(points, voiced_at) -> {points, auto, amplified}`(純 Python):中間點 semi > 0 且大於前一點為峰、semi < 0 且小於前一點為谷(首尾點不處理);在「前一點 → 該點」這段的後 `PREDIP_FRAC`(0.3)處插入一點,值 = 使用者原始折線在該處的值 ∓ `PREDIP_SEMI`(1 半音);峰谷值 × `PEAK_GAIN`(1.2);一律以使用者原始點計算;內部點截到 ±`INNER_LIMIT`(7.2),每句最多 30 點。`snap_voiced()`(吸附有聲幀):插入點所在幀無聲時,改用同段開區間內、與兩端至少差 `SNAP_GAP`(0.01)、最近的有聲幀(距離相同取較前者),值在新位置重算;該段沒有有聲幀時不插入(峰谷仍放大)。`pitch_curve(points, roundness, t)`:f(t) = (1 − r) × 折線(t) + r × PCHIP(t)(scipy `PchipInterpolator`),r = 圓弧 / 100,經過每個內部點,點外維持端點值;取代修訂前的二次貝茲。`apply_pitch()` 以 pyin(70–400 Hz)只估一次 F0,有聲旗標同時供吸附與 TD-PSOLA 使用,回傳(音訊, 內部點)。
- **`tts_service`**:`trim()`(ffmpeg `silenceremove` + `areverse`、10 ms 淡入淡出,濾鏡只含程式常數)、`post_process(project, ids, emit)`(快取命中、預設參數不啟動 shape、其餘一次 shape worker;逾時 30 + 5 × 句數 秒;不進 heavy_lock)、`ensure_post()`、`stale_post()`;`synth()` 每句完成即裁切;`apply_speed()` 以後製檔為來源;`_run_worker` 抽出 `_run_proc`(synth 與 shape 共用)。修訂 1:`shape_job()` 以 `project.model_units()` 換算 `chars` 與字詞清晰度區段(送模型的字,含顯示字);`_shape_pitch_points()` 驗證 shape 回傳的 `pitch_points`(有語調的句子須為 2–30 點、位置 0–1 嚴格遞增、半音 ±7.2;沒有語調須為 `null`),不合格即該句後製失敗(`SynthError`)、沿用上一份後製檔,合格者寫入 `segment.post.pitch_points`;後製檔快取命中時只沿用專案內同一後製鍵已寫入的 `pitch_points`(`_known_pitch_points`),找不到則不寫。
- **後製鍵**(`project.post_key()`;修訂 1 T10):SHA-256(JSON 陣列 [audio.id, `POST_VERSION`(1), 裁切參數, connect, clarity, word_clarity, pitch] + 演算法標記)。演算法標記取自 `project.POST_ALGO = {"clarity": 2, "pitch": 2}`,只在用到時附加為陣列最後一項的物件:清晰度 < 100% 或有字詞清晰度時含 `clarity`、有語調時含 `pitch`;都沒有時不附加,只有裁切或連音的片段鍵與修訂前相同。`is_post_fresh()` 比對 `post.key`,修訂前寫入的清晰度 / 語調後製因此過期,依 D20 以新演算法重做(不重新合成)。
- **`timeline.layout()`**:片段長度 = 後製有效時 `post.seconds` ÷ 語速,否則 `audio.seconds` ÷ 語速(預覽、匯出唯一來源)。
- **API**:`PATCH /api/projects/{p}/segments/{id}` 新增 `connect`、`clarity`、`word_clarity`、`pitch`、`fusions`、`fusion_off`(自錄音檔設韻律回 422);`POST /api/projects/{p}/post`(`{"segments": [...]}` 或省略 = 全部過期;共用執行中旗標,忙碌 409;回專案檢視);專案檢視多 `post_stale`;media 端點可送後製檔;匯出前補做過期後製;合成後接著後製非預設參數的片段。修訂 1:`fusions` 每組可帶選填 `display`(空字串或 1~4 個漢字,其他回 422);PATCH 寫入 `post`(含 `pitch_points`)仍回 422;不新增端點。
- **`fusion.py`**(純函式):`split_zhuyin`、`fuse` / `explain`(D24 規則 + D25 合法化表)、`suggest`(4 → 3 → 2 字最長匹配不重疊)、`marks`(on / off / pending)、`pair_options` / `default_option` / `combine`(3~4 字選一對,D37)、`is_syllable`;`POST /api/fusion/suggest`(`text`、`zhuyin`、`index`、`length`、`exclude`)。`project.tts_text()`:合音帶 `display` 時(修訂 1 T12)整個範圍換成顯示字(經 `escape_text`)、不加注音;沒有時 n 字合成 k 音節、前 k 字依序帶 `[:音節]`、刪除其餘字;範圍外的聲調標註照帶 `[:注音]`。`project.fused_out()` 回不送模型的原字索引(帶顯示字時為整個範圍);`project.model_units()` 回送模型的每個漢字依序對應的原文索引(第 j 個顯示字對應範圍內第 min(j, length − 1) 字),供估計字界與字詞清晰度換算(R1-5)。
- **合音詞庫端點**:`GET /api/settings/fusions`、`PUT` / `DELETE /api/settings/fusions/{word}`(PUT `{"restore": true}` 還原預設詞)、`GET /api/settings/fusions/export?format=json|csv`、`POST /api/settings/fusions/import/preview`(multipart,只在記憶體解析;請求本體上限 = 設定值 + 16 KB,超過即 413)、`POST /api/settings/fusions/import/apply`(預覽代號 128 位元、單次、10 分鐘;交易內比對詞庫版本 SHA-256,不符 409)、`GET` / `PUT /api/settings/fusions/limits`。
- **前端**:屬性面板「韻律」「語調」百分比拉桿(放開才送出,PATCH 後 `POST /post`)、聲調區「念法 / 清晰度 / 合音」模式;時間軸語調折線 SVG(菱形關鍵點)、合音徽章;文稿框以「textarea + 上方標記層」畫三色波浪底線、點擊浮動窗啟用 / 停用、啟用處常駐浮層(D42);設定頁「合音詞庫」子頁(差異預覽五欄表、拒絕原因區塊、上限設定)。
- **前端(修訂 1 T13、T14)**:`static/edit.js`——整句清晰度拉桿與字詞清晰度彈出框的拉桿低於 25%(`CLARITY_WARN_BELOW`)時,在說明下方顯示固定文字警告(`.clarity-warn`、`role="status"`、`textContent`),拖動中即時顯示、開啟已存的片段也顯示,只警告、不阻擋;圓弧說明加「曲線一律經過每個實際套用的點」,關鍵點列加「· 自動補點 N 個(虛線,不可拖曳)」;由詞庫套用合音時 PATCH 帶 `display`(文稿框「啟用」、合音模式、屬性面板句子文字三處;規則與手動為空字串);屬性面板合音模式「已套用」列表下方兩列——「送模型」(`.fusion-sent`,純函式 `ttsText(seg)` 與 `project.tts_text()` 同規則,只在前端試算、不呼叫伺服器)與「字幕」(`.fusion-subtitle`,原文加「(原文不變)」),套用或移除合音後重繪,一律 `textContent`;合音模式彈出框不含這兩列,貼近視窗右緣時往左移。`static/timeline.js`——`pitchPath()` 以同一 PCHIP 混合公式繪製(每 2 px 取樣、經過每個點),縱軸 ±7.2 半音、±6 處畫淡色參考線,拖曳仍限 ±6;`pitchModel()`:後製有效、不在後製中且帶 `post.pitch_points` 時依 worker 實際套用的點繪製,否則以 `expandPoints()` 試算(不含吸附)並以淡色虛線(`is-estimate`)繪製;自動補點畫成虛線圓(`.tl-pitch-auto`)、放大峰谷畫成虛線菱形(`.tl-pitch-amp`)並以虛線連到使用者菱形,兩者都不可選取、不可拖曳。

## CHG-20261022-02:MCP 註冊管理

〔觀察〕以整合分支 T1~T3 的程式為準(`mcp_registry.py` 檔頭與實作、`studio_server.py` 檔頭「MCP 註冊 API」);修改步驟 1 的 CLI 實機查證見 CHG-20261022-02。

### `mcp_registry` 介面

- 常數:`NAME = "local-models"`、`CLIENTS = ("claude", "codex")`、`ACTIONS = ("register", "remove")`;子程序逾時 30 秒、單一字串上限 64 KB、讀檔上限 16 MB。
- `paths()` → `{"claude_cli", "codex_cli", "uv", "claude_json", "claude_settings", "repo_mcp_json", "codex_home"}`,每次呼叫依當下環境變數決定(規則見 `data.md`「repo 外讀寫的檔案」);CLI 與 `uv` 固定為 `~/.local/bin/` 下的絕對路徑。
- `server_argv()` → `[<uv>, "run", "--script", <repo>/local_models_mcp.py]`。
- `status(check)` → `{"server", "claude", "codex"}`;每個 client 為 `{"installed", "state", "command", "args", "env_keys", "timeout", "health", "detail", "shadowed"}`。`state` ∈ `registered`(command 等於 `~/.local/bin/uv` 的絕對路徑且 args 逐元素等於 `["run", "--script", "<repo>/local_models_mcp.py"]`)/ `other` / `absent` / `unknown`;`timeout`:Claude 為 `{"key": "MCP_TOOL_TIMEOUT", "value", "suggested": "1800000"}`,Codex 為 `{"key": "tool_timeout_sec", "value", "snippet"}`(`snippet` 為手動補的 TOML 片段);`shadowed` 為 `true` / `false` / `null`(無法判斷;Codex 一律 `false`);`env_keys` 只有鍵名。
- `plan(client, action)` → `{"argv", "files", "current", "warnings"}`,只組指令、不執行;未安裝、`unknown`、已 `registered` 再註冊、`other` 註冊(只能先移除,D15)、`absent` 移除一律丟繁中 `ValueError`。
- `apply(client, action)` → `{"ok", "exit_code", "output", "status"}`;執行 `plan` 的指令後一律重新查狀態(同 `check=False`),結束碼 0 且新狀態符合預期(`register` → `registered`、`remove` → `absent`)才 `ok: true`;逾時時 `exit_code` 為 null、`output` 為「CLI 執行逾時(超過 30 秒)」。
- `redact_env(text, known)`:已知 env 值、`鍵=值` 行的值、JSON `env` 物件的值一律換成「(已設定)」;`scrub(text)`:repo → `<repo>`、家目錄 → `~`,不在家目錄或 repo 之下的 `CLAUDE_CONFIG_DIR` / `CODEX_HOME` 顯示為變數名。回傳字串一律依「`redact_env()` → `scrub()` → 截斷 64 KB」處理。
- `Busy`:`status` / `plan` / `apply` 共用一把行程內非阻塞鎖,執行中時其他呼叫丟 `Busy`(端點 409);不進 `heavy_lock`。`client` / `action` 列舉檢查在取鎖與呼叫 CLI 之前。

### 外部 CLI 契約(Claude Code 2.1.308、codex-cli 0.163.0 實機查證)

- 子程序一律 argv list、`shell=False`、`stdin=DEVNULL`、逾時 30 秒、cwd = repo 根目錄;環境沿用伺服器行程,另一律帶 `UV_OFFLINE=1`、`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`、`DISABLE_TELEMETRY=1`。
- `claude mcp add -s user local-models -- <uv> run --script <repo>/local_models_mcp.py`:寫入 `.claude.json` 頂層 `mcpServers.local-models`;同名已存在時結束碼 1。
- `claude mcp remove local-models -s user`:不存在時結束碼 1。
- `claude mcp get local-models`:人讀文字(Scope / Status / Type / Command / Args / Environment),**會實際啟動註冊內容指定的程式做健康檢查**;本程式只在「重新檢查」且 Claude 為 `registered`、未被遮蔽時呼叫。Status 行含 `Connected` 或 ✔ → `ok`,含 fail / error 或 ✘ / ✗ → `failed`,其他 → `unknown`;非 0 結束 → `failed`;逾時或無法執行 → `unknown`。
- `codex mcp add local-models -- <uv> run --script <repo>/local_models_mcp.py`:寫入 `config.toml` 的 `[mcp_servers.local-models]`;**沒有設定 `tool_timeout_sec` 的參數**,同名再 add 為無聲覆寫並丟失手動加的 `tool_timeout_sec`。
- `codex mcp remove local-models`:不存在時**也回 0**,因此成功與否以重新查到的狀態判斷。
- `codex mcp get local-models --json`:JSON(`transport.command` / `args` / `env`、`tool_timeout_sec` 等),不啟動 MCP 伺服器;結束碼 1 且 stderr 含 `No MCP server named` → `absent`,其他非 0 或非 JSON → `unknown`。
- 〔觀察〕三個 `/api/mcp/*` 端點一律 POST(GET 回 405),沿用 Host / Origin / `X-Studio-Token` 中介層;瀏覽器只能傳 `client`、`action` 兩個列舉值與布林 `check`、`confirm`,指令、路徑、名稱與 scope 全部由 `mcp_registry` 組出;回應一律 `Cache-Control: no-store`(安全中介層直接回的 403 除外),錯誤回應 `{"error": 繁中訊息}`。

### 設計決策(CHG-20261022-02,使用者 2026-10-30 確認閘核准)

| 決策 | 選項 | 取捨理由 | 等級 |
|------|------|----------|------|
| D8 狀態查詢:Claude 端唯讀解析 `.claude.json` 頂層 `mcpServers["local-models"]`,Codex 端 `codex mcp get --json`,`claude mcp get` 只在按「重新檢查」時呼叫 | (a) 每次開頁都跑 CLI vs (b) vs (c) 兩邊都只在按鈕時呼叫 vs (d) 也唯讀解析 `config.toml` | `claude mcp get` / `list` 會啟動 MCP 伺服器(數秒、多一個 `uv` 行程);(c) 開頁看不到狀態;(d) 需 Python 3.11 的 `tomllib` 或加依賴。解析失敗或超過 16 MB 顯示「狀態不明」,不顯示成未註冊 | 〔觀察〕CHG-20261022-02 D8 (b) |
| D9 Codex `tool_timeout_sec` 只提醒 | (a) 只提醒 + TOML 片段 vs (b) 移除另需勾選 vs (c) 網頁改寫 `config.toml` | `codex mcp add` 無法設定;(c) 違反 FR-22「不直接編輯」、可能弄壞註解與其他段落 | 〔觀察〕CHG-20261022-02 D9 (a) |
| D10 CLI 設定目錄沿用伺服器行程的 `CLAUDE_CONFIG_DIR` / `CODEX_HOME`,解析的檔案路徑依同一規則 | (a) 沿用環境變數 vs (b) 實機驗證直接動真實設定 | 真 CLI 可先在暫存設定目錄完整跑一輪,真實設定只在使用者同意後動一次 | 〔觀察〕CHG-20261022-02 D10 (a) |
| D11 驗收不破壞現有設定:唯讀檢視 → 暫存設定目錄完整操作 → 經同意才對真實設定做一輪(先備份) | (a) 三段 vs (b) 只做前兩段 vs (c) 直接操作真實設定 | 現況兩邊都已註冊;真實設定的移除會讓 Codex 失去 `tool_timeout_sec` | 〔觀察〕CHG-20261022-02 D11 (a) |
| D12 指令預覽沿用 `scrub()`:家目錄顯示 `~`、repo 顯示 `<repo>`,確認框註明執行時展開為絕對路徑 | (a) 沿用 `scrub()` vs (b) 顯示完整絕對路徑 | 不破例送本機絕對路徑;指令每個元素仍逐一列出 | 〔觀察〕CHG-20261022-02 D12 (a) |
