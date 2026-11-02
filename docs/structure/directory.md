# 目錄結構

> 導入反推(2026-10-22,基準點 `7da165b6048897abb8acac4bba69db9677827471`)。來源等級見 `docs/ai-guideline.md` 表頭。
> 2026-10-23 依 CHG-20261022-03 補入 CI/CD 相關目錄(以 repo 追蹤檔為準)。
> 2026-10-26 依 CHG-20261022-01 結構文件更新:新增網頁伺服器、節點引擎、設定模組、`static/`、`config/`、`workflows/`、`tests/`;移除 Gradio 版 `podcast_studio.py`(以 T1~T13 合併後的 main 為準)。
> 2026-10-26 依 CHG-20261026-01 結構文件更新:`workflows/` 只追蹤 `default.json`,使用者另存的檔案被 `.gitignore` 忽略。
> 2026-10-28 依 CHG-20261022-04 結構文件更新(DIR-5 修正輪,以整合分支 `chg/CHG-20261022-04-impl` 的 T1~T9 與修正為準):新增 `breezy_worker.py`、`names.py`、`project.py`、`timeline.py`、`tts_service.py`、`projects/`(ignored)、`static/project.js`、`static/timeline.js`、`static/wizard.js`、`static/edit.js`、`tests/integration/`;`nodes.py` 為十一種節點;`docs/changes/review/` 放 DIR-5 統整報告。
> 2026-10-28 依 CHG-20261028-01 結構文件更新:新增 `.review/`(本機審議 Codex 席的 PR head 唯讀檢視,ignored);`scripts/local_review.py` 限制改記。
> 2026-10-28 依 CHG-20261028-02 結構文件更新:新增 `scripts/review_decision.py`(統整席判定留言,治理基礎設施)、`outputs/.tmp/review_decision/`(判定 JSON,ignored)、`features/review_decision.feature`;`review_gate.py` 改驗意見留言與統整判定。
> 2026-10-30 依 CHG-20261030-02 結構文件更新:職責表新增 `docs/changes/screens/`(給統整席核對的畫面截圖,DIR-6 第 11 條)。
> 2026-10-30 依 CHG-20261022-02 結構文件更新(以整合分支 `chg/CHG-20261022-02-impl` 的 T1~T3 為準):新增 `mcp_registry.py`、`tests/test_mcp_registry.py`、`tests/test_server_mcp.py`、`tests/integration/test_mcp_registry_real.py`、`features/mcp_registry.feature`;設定頁新增「MCP 註冊」子頁;`tests/ui_check.py` 新增 `mcp` 組。
> 2026-11-01 依 CHG-20261101-02 結構文件更新:新增 `.claude/agents/review-seat-opus.md`(本機審議 Opus 席的專案子代理定義,只有 Write 工具)、`features/local_review_opus.feature`;`scripts/local_review.py` 新增 opus 引擎與 `outputs/.tmp/local_review/opus/` 暫存(請求檔 / 答案檔)。
> 2026-11-01 依 CHG-20261101-02 修訂 1 結構文件更新:`scripts/local_review.py` 職責列補 Opus 席分組繳交,compliance 席參考資料上限改為 24000 字。

## 樹狀

```
podcast-studio/
├── studio_server.py           # 入口:網頁伺服器(FastAPI,只綁 127.0.0.1:7860)
├── local_models_mcp.py        # 入口:MCP 伺服器(stdio)
├── executor.py                # 流程圖驗證與依拓撲順序執行
├── nodes.py                   # 節點登錄表(十一種節點;含「送到素材庫」)
├── settings.py                # 模型設定與語音預設讀寫(SQLite config/studio.db,schema 2)
├── heavy_lock.py              # 跨行程重型工作鎖(outputs/.heavy.lock)
├── taiwan_ai.py               # 模型層(共用函式庫;CLI 已移除)
├── media.py                   # 媒體層(含時間軸匯出 render_timeline、內容嗅探 sniff_binary)
├── names.py                   # 名稱規則:工作流程名稱與專案名稱共用的中英日白名單(CHG-20261022-04 T2)
├── safe_fs.py                 # 安全路徑工具:自受信根目錄以 dir_fd 逐層 O_NOFOLLOW 開啟、讀寫刪搬(CHG-20261022-04 DIR-5 修正 4)
├── project.py                 # 專案資料模型:分句、停頓、標註、版本、.trash(CHG-20261022-04 T2)
├── timeline.py                # 時間軸計算 layout():片段、字幕 cue、畫面軌(CHG-20261022-04 T4)
├── tts_service.py             # 逐句合成服務:髒片段、快取、語速後製(CHG-20261022-04 T3)
├── mcp_registry.py            # local-models 的 MCP 註冊管理(Claude Code / Codex user scope,只經官方 CLI;CHG-20261022-02 T1)
├── breezy_worker.py           # BreezyVoice worker(在 BreezyVoice venv 執行:synth / annotate;CHG-20261022-04 T1)
├── static/                    # 網頁前端(原生 HTML/JS/CSS,免建置)
│   ├── index.html             # 首頁(精靈 / 完整模式兩張卡片)、精靈、完整模式、設定頁(含「進階流程」節點畫布)、免責說明頁
│   ├── app.js                 # 共用工具 window.Studio、路由、設定頁、節點畫布
│   ├── project.js             # 專案狀態(Store)、文稿 / 封面 / 語音 / 匯出面板、逐句合成(CHG-20261022-04 T7)
│   ├── timeline.js            # 時間軸三軌、預覽、屬性面板、素材庫(T7)
│   ├── wizard.js              # 首頁、步驟精靈、完整模式(T7)
│   ├── edit.js                # 停頓、聲調、語速、畫面軌拖拉等編輯互動(T8)
│   ├── style.css
│   └── vendor/                # 唯一第三方前端檔:Drawflow 0.0.60(MIT)
│       ├── drawflow.min.js
│       ├── drawflow.min.css
│       ├── LICENSE.drawflow
│       └── README.md          # 版本、來源、SHA-256、授權
├── workflows/
│   └── default.json           # 預設流程範本(兼作步驟精靈的預設步驟)
├── config/                    # 使用者設定(目錄內全部 ignored,repo 不追蹤任何檔)
│   ├── studio.db              # SQLite 設定資料庫(另有 -wal、-shm)
│   ├── .studio.db.lock        # 初始化用跨行程鎖
│   └── models.json            # 舊 JSON 設定,只作一次性匯入來源(T3 起移出 git)
├── setup_breezyvoice.sh       # BreezyVoice 重建
├── patches/
│   └── breezyvoice-mac/
│       ├── requirements-mac.txt
│       ├── build-constraints.txt
│       └── tn/                # 取代 WeTextProcessing 的數字正規化
│           ├── chinese/normalizer.py
│           └── english/normalizer.py
├── PODCAST_STUDIO.md          # 人寫的設計文件(導入前即存在)
├── AGENTS.md                  # 治理入口(導入新增)
├── docs/                      # 治理帳本(導入新增)
│   ├── ai-guideline.md
│   ├── structure/{directory,logical,design,data}.md
│   ├── changes/               # CHG-YYYYMMDD-NN.md(另有 .gitkeep)
│   │   └── review/            # DIR-5 統整報告 <CHG-ID>-summary.md(不屬 CHG 檔名規則)
│   ├── acceptance/            # ACC-YYYYMMDD-NN.md(另有 .gitkeep)
│   └── knowledge/{INDEX.md,directives.md,patterns.md,vocabulary.json}
├── features/                  # 行為規格(Gherkin .feature,CHG 的 Behaviour spec)
│   ├── ci.feature             # CHG-20261022-03
│   └── …                      # canvas、file_dnd、heavy_lock、local_review_opus、mcp_registry、pause_tone、settings、timeline_editor、wizard_speed
├── scripts/                   # 開發流程腳本(CHG-20261022-03)
│   ├── ci/
│   │   ├── commit_check.py        # commit 引用 CHG 編號檢查
│   │   ├── plan_check_changed.py  # PR 改到的 CHG 做 plan-check
│   │   ├── review_gate.py         # 驗意見留言與統整判定 / infra-approved
│   │   ├── secret_scan.py         # 全 repo 與 commit 範圍秘密掃描
│   │   └── secret_allowlist.txt   # 秘密掃描具名豁免清單
│   ├── local_review.py        # 本機審議會(DIR-1),以 PR 留言貼各席意見
│   ├── review_decision.py     # 驗證統整席判定 JSON,以 PR 留言貼判定(DIR-6)
│   ├── check.sh               # 產品測試載具:以 uv 暫時環境執行 pytest
│   └── deploy_local.sh        # 本機部署(main → 重啟 7860 的 studio_server.py)
├── tests/
│   ├── conftest.py            # 把 repo 根目錄放進 sys.path
│   ├── test_*.py              # 產品程式的 pytest(CHG-20261022-01)
│   ├── ui_check.py            # UI 互動檢查(Playwright 無頭 Chromium;不屬 pytest,見下表)
│   ├── integration/           # 真模型 / 真 CLI 整合測試(test_breezy_worker_real.py 以 BreezyVoice venv 執行;test_mcp_registry_real.py 用真 claude / codex CLI 與暫存設定目錄;不列入 check.sh)
│   └── ci/                    # scripts/ 的單元測試(unittest)
├── .ai-sdlc/                  # vendored ai-sdlc v1.66.0(不修改內容)
│   ├── README.md              # 版本、來源、MIT 授權、更新方式
│   ├── SHA256SUMS             # 每檔 SHA-256,CI 由 trusted/ 驗證
│   └── skills/ai-sdlc-autopilot/
│       ├── scripts/           # doc_integrity_check.py、autopilot_runner.py 等(含 lib/)
│       └── assets/            # 政策與基準 JSON
├── .github/
│   └── workflows/ci.yml       # CI:document / chg / program / review
├── .claude/
│   ├── launch.json            # Claude 桌面 app 啟動網頁(uv run --script studio_server.py)
│   ├── agents/
│   │   └── review-seat-opus.md  # 本機審議 Opus 意見席的子代理定義(只有 Write 工具;CHG-20261101-02)
│   └── worktrees/             # agent worktree(ignored,CHG-20261022-03 T9)
├── .gitignore
├── outputs/                   # 產出物(ignored);uploads/ 為上傳檔,.heavy.lock 為重型鎖,ui_check/ 為 UI 檢查截圖,.tmp/ 為 /api/annotate 暫存,.tmp/review_decision/ 為統整席判定 JSON,.tmp/local_review/ 為本機審議 Codex 席暫存與 Opus 席請求檔 / 答案檔(opus/)
├── projects/                  # 專案資料(ignored,使用者資料):<名稱>/{project.json,history/,audio/,assets/,tmp/,.lock}、.trash/
├── .review/                   # 本機審議 Codex 席的 PR head 唯讀檢視(ignored,審完即移除;CHG-20261028-01)
└── tools/BreezyVoice/         # 第三方 repo + .venv(ignored,由腳本重建)
```

## 各目錄職責

| 路徑 | 職責 | 備註 | 等級 |
|------|------|------|------|
| `/`(根目錄 `.py`) | 全部 Python 程式碼;平鋪,沒有 package | 入口檔(`studio_server.py`、`local_models_mcp.py`)以 `sys.path.insert` import 共用模組;相依方向見 `logical.md` | 〔觀察〕 |
| `static/` | 網頁前端:`index.html`(首頁「精靈」「完整模式」兩張卡片、右上角 ⚙ → 設定頁;設定頁子頁含「語音預設」與「進階流程」節點畫布;底部「免責說明與授權」連結)、`app.js`(共用工具 `window.Studio`、hash 路由、設定頁、畫布)、`project.js`(專案狀態與文稿 / 封面 / 語音 / 匯出面板)、`timeline.js`(時間軸三軌、預覽、屬性、素材庫)、`wizard.js`(首頁、精靈、完整模式)、`edit.js`(停頓、聲調、語速、畫面軌編輯)、`style.css` | 原生 JS,不經建置、不從 CDN 載入;由 `studio_server.py` 只解析 `static/` 之下的真實路徑送出;首頁的 `__STUDIO_TOKEN__` 換成啟動權杖;後四個模組依序載入,以 `Studio.hook(name, fn)` 串接(CHG-20261022-04 T7、T8) | 〔觀察〕 |
| `projects/` | 專案資料(CHG-20261022-04):`<名稱>/project.json`(`schema_version: 1`)、`history/`(最近 10 版)、`audio/`(合成與自錄音檔)、`assets/`(素材)、`tmp/`(worker 輸入檔,用完即刪)、`.lock`(專案層級鎖);`.trash/<名稱>-<時間戳>-<4 碼>/` 為刪除的專案(30 天後啟動時清除) | `.gitignore`(使用者資料);只經 `project.py` 存取;不經 `/files/*` 提供,音檔與素材只經 `/api/projects/{p}/media/{id}` 送出 | 〔觀察〕 |
| 根目錄 CHG-20261022-04 模組 | `safe_fs.py`(安全路徑工具,只用標準庫,不 import repo 內其他模組;`project`、`studio_server`、`nodes`、`media`、`tts_service`、`heavy_lock`、`settings` 共用,DIR-5 修正 4)、`names.py`(名稱白名單,`studio_server.py` 與 `project.py` 共用)、`project.py`(資料模型)、`timeline.py`(純函式 `layout()`)、`tts_service.py`(合成服務)、`breezy_worker.py`(以 BreezyVoice venv 執行、只用標準庫與 BreezyVoice 模組,不 import repo 內其他模組) | 相依方向見 `logical.md` | 〔觀察〕 |
| `static/vendor/` | Drawflow 0.0.60 的 `drawflow.min.js`、`drawflow.min.css`、`LICENSE.drawflow` 與 `README.md` | 唯一第三方前端檔(CHG-20261022-01 前置 P1);`tests/test_static.py` 比對 README 記載的 SHA-256 | 〔觀察〕 |
| `workflows/` | 工作流程 JSON(executor 流程圖格式);`default.json` 為預設範本 | 由 `GET/PUT /api/workflows/{name}` 讀寫;使用者另存的檔案(與存檔暫存檔 `.wf-*.tmp`)也落在這裡;git 只追蹤 `default.json`,其他檔案由 `.gitignore` 的 `workflows/*`、`!workflows/default.json` 忽略,存檔後工作樹仍乾淨(CHG-20261026-01 T3);日後不在此新增其他追蹤檔 | 〔觀察〕 |
| `config/` | 使用者設定:`studio.db`(與 `-wal`、`-shm`)、`.studio.db.lock`、舊 `models.json` | 全部列入 `.gitignore`;只經 `settings.py` 存取,`/files/*` 不提供 | 〔觀察〕 |
| `patches/breezyvoice-mac/` | 對第三方 BreezyVoice 的 macOS 調整 | 由 `setup_breezyvoice.sh` 複製進 venv site-packages | 〔觀察〕 |
| `docs/` | 治理帳本 | 落點宣告在 Guideline 表頭 | 〔觀察〕 |
| `outputs/` | 產出檔;`uploads/` 上傳檔、`.heavy.lock` 重型鎖檔、`.studio.pid` 與 `studio_server.log`(部署腳本)、`ui_check/` UI 檢查截圖、`.tmp/review_decision/pr-<編號>-<sha12>.json` 統整席判定 JSON(派發者於 merge 後手動清理;CHG-20261028-02) | `.gitignore`;網頁 `/files/*` 只提供本目錄之下、不以 `.` 開頭的檔案 | 〔觀察〕 |
| `tools/` | 第三方工具與 venv(約 1.9 GB) | `.gitignore`;無 `.git` | 〔觀察〕 |
| `.claude/` | Claude 桌面 app 設定(追蹤檔為 `launch.json` 與 `agents/review-seat-opus.md`) | `launch.json` 用 `uv` 絕對路徑,參數 `run --script studio_server.py`(不帶設定檔路徑);`.claude/worktrees/` 是 Claude 桌面 app 為派發施工建立的 agent worktree,列入 `.gitignore`,避免主工作副本被判為不乾淨而讓 `scripts/deploy_local.sh` 中止(CHG-20261022-03 T9) | 〔觀察〕 |
| `.claude/agents/` | Claude Code 專案子代理定義(CHG-20261101-02) | `review-seat-opus.md` 為本機審議 Opus 意見席(DIR-1 第 13 條):frontmatter `model: opus`、`effort: medium`、`tools: Write`,本文只有固定約束(只依 prompt 內資料判斷、唯一動作是寫答案檔);派發者一律以 subagent_type `review-seat-opus` 派 Opus 席;目前不在治理基礎設施清單(CHG-20261101-02 代決 26,列入後續改善) | 〔觀察〕 |
| `docs/knowledge/` | 使用者教過的規則:`INDEX.md`、`directives.md`(DIR-n)、`patterns.md`、`vocabulary.json` | 只改本目錄的 commit 免引用 CHG 編號(CHG-20261022-03) | 〔觀察〕 |
| `features/` | 行為規格(Gherkin `.feature`),由各 CHG 的「Behaviour spec」引用 | `ci.feature` 屬 CHG-20261022-03(「統整席判定需修正」場景由 CHG-20261028-02 改寫);`local_review_codex.feature` 屬 CHG-20261028-01;`review_decision.feature` 屬 CHG-20261028-02;`canvas`、`file_dnd`、`heavy_lock`、`settings` 屬 CHG-20261022-01;`pause_tone`、`timeline_editor`、`wizard_speed` 屬 CHG-20261022-04(對應功能隨該筆合併 PR 進 main);`mcp_registry` 屬 CHG-20261022-02;`local_review_opus` 屬 CHG-20261101-02 | 〔觀察〕 |
| `scripts/ci/` | CI 治理檢查:`commit_check.py`、`plan_check_changed.py`、`review_gate.py`(驗意見留言與統整判定)、`secret_scan.py`、`secret_allowlist.txt` | 只用 Python 標準函式庫;CI 一律從 base 分支的 trusted/ 副本(CI 執行期目錄,不在 repo 內)執行 | 〔觀察〕 |
| `scripts/local_review.py` | 本機審議會(DIR-1 六席,各席獨立作答一次),以 `gh pr comment` 貼出各席意見(建議等級不具否決效力,DIR-6) | 只讀 PR 資料、主工作副本不 checkout;拒絕 fork 與非擁有者 PR;diff 經過濾後超過 30000 字時,以 head sha 在 `.review/pr-<編號>-<sha12>/` 建 `--no-checkout` 的 detached worktree(不寫出追蹤檔、停用 hook)供 Codex 席以 `git show` / `git diff --no-textconv --no-ext-diff` 唯讀讀取完整程式,審完(含例外)以 `safe_remove_view()` 移除——本次與舊檢視共用,只在目錄內僅有 `.git` 時刪該指標檔與空目錄並 `git worktree prune`(不用 git 的強制移除、不遞迴刪除),否則留下目錄並警告;同名舊目錄另須是登記的 worktree,否則拒絕、不刪;Ollama 席仍依 `num_ctx` 截短(CHG-20261028-01);非 think 的 Ollama 席在 diff 截到 0 仍放不進時截短 CHG 並記注意行(CHG-20261028-02);互讀階段已移除;compliance 席參考資料去除 directive「歷史」區塊、上限 24000 字(CHG-20261028-02 DIR-5 修正;CHG-20261101-02 修訂 1 提高);opus 引擎(CHG-20261101-02):risk、consistency 預設由 Opus 子代理擔任,跑到 Opus 席時在 `outputs/.tmp/local_review/opus/` 寫請求檔(`<stem>.prompt.md`、`<stem>.request.json`)、輪詢答案檔 `<stem>.answer.json`(上限 256 KB、本機嚴格 schema 檢查,每次 15 分鐘、重派一次換新 stem),每次嘗試結束刪除該 stem 的檔、目錄空了以非遞迴 rmdir 移除;每份請求 prompt 上限 20000 字,超過時 CHG 依章節、diff 依函式關聯分組(每席 CHG、diff 各至多 2 組,超出不送審),每組一份請求檔(stem 含 `g<組>`)、逐份交接,各組判決合成單席(CHG-20261101-02 修訂 1);不新增網路呼叫;`--opus-seats` 覆寫,與 `--codex-seats` 重疊 exit 2 | 〔觀察〕 |
| `scripts/review_decision.py` | 驗證統整席子代理寫出的判定 JSON,以 `gh pr comment` 貼出帶 `review-decision` 標記的判定留言(DIR-6) | 治理基礎設施(需 `infra-approved`);不呼叫模型、不寫檔;拒絕 fork、非擁有者 PR、不在 main 執行;需目前 head 的六席意見留言;`model`、`effort` 只取自參數;JSON 文字經 `sanitize()` 放在程式碼區塊內(CHG-20261028-02) | 〔觀察〕 |
| `.review/` | 本機審議 Codex 席的 PR head 唯讀檢視 | `.gitignore`;只由 `scripts/local_review.py` 建立與移除,平時不存在(CHG-20261028-01) | 〔觀察〕 |
| `scripts/check.sh` | 產品測試載具(CHG-20261022-01 T1):以 uv 暫時環境帶 pytest、fastapi 等依賴執行 `pytest -q tests/` | 可帶測試路徑或 pytest 參數;CI `program` job 與 `scripts/deploy_local.sh` 都會執行 | 〔觀察〕 |
| `scripts/deploy_local.sh` | 本機部署:在乾淨的 main 上 `pull --ff-only`,重啟 7860 網頁伺服器(`uv run --script studio_server.py`),驗證只綁本機 | 只寫入 `outputs/` 下的 pidfile 與 log | 〔觀察〕 |
| `tests/`(根層) | 產品程式的 pytest:設定、節點、執行器、重型鎖、伺服器核心 / 檔案 / 設定 API、前端靜態檢查、預設範本 | 以 `./scripts/check.sh` 執行;模型呼叫一律以替身取代;ADP-6 的結案以 ACC 為準 | 〔觀察〕 |
| `tests/ui_check.py` | UI 互動檢查(CHG-20261022-01 T14):PEP 723 script(playwright 1.64.1),本行程內以替身執行器與替身 Ollama 清單啟動 `create_app()`(隨機空閒埠,設定資料庫與 `outputs/`、`workflows/` 放暫存目錄),以無頭 Chromium 操作 ⚙ → 設定 › 進階流程、拖節點、連線、拖檔、釘住、設定存檔;截圖寫 `outputs/ui_check/` | `uv run --script tests/ui_check.py`;需先安裝 Chromium(前置 P2);檔名不是 `test_*.py`,`./scripts/check.sh` 與 CI 不執行 | 〔觀察〕 |
| `tests/ui_check.py`(CHG-20261022-04 T9 擴充) | 拆成 `canvas`(上一列)與 `editor` 兩組,各用自己的暫存目錄與伺服器;不帶參數兩組都跑,`--only editor` 只跑 editor;editor 的合成走真正的 `tts_service.synth`,只把 worker 換成本機 Python 替身,匯出與注音以替身取代;截圖 `home.png`、`wizard.png`、`full.png`、`editor.png`、`tone.png`、`disclaimer.png` | DIR-5 修正輪加入「合成中途試聽」與「停頓 0 秒仍可拖拉」情境 | 〔觀察〕 |
| `tests/ui_check.py`(CHG-20261022-02 T3 擴充) | 新增 `mcp` 組(`--only mcp`):假 `claude` / `codex` CLI 與暫存 `HOME`、`CLAUDE_CONFIG_DIR`、`CODEX_HOME`,操作「設定 › MCP 註冊」子頁與確認框;截圖 `mcp.png`、`mcp-confirm.png`;結束後比對真實設定不變 | 不帶 `--only` 時依 canvas → editor → mcp 執行 | 〔觀察〕 |
| `tests/integration/` | 真模型整合測試(`test_breezy_worker_real.py`:兩句合成與 `annotate`);真 CLI 整合測試(`test_mcp_registry_real.py`,CHG-20261022-02 T1) | `test_breezy_worker_real.py` 以 `tools/BreezyVoice/.venv/bin/python -m pytest` 在本機執行;`test_mcp_registry_real.py` 以 `./scripts/check.sh tests/integration/test_mcp_registry_real.py` 手動執行(`tests/conftest.py` 只在命令列明確指定時收集);皆不列入 `./scripts/check.sh` 預設範圍與 CI | 〔觀察〕 |
| `docs/changes/review/` | DIR-5 統整報告(`<CHG-ID>-summary.md`) | 檔名不符 `CHG-*.md`,不受 `doc_integrity_check` 的 CHG 欄位檢查與 `plan_check_changed.py` 檢查(使用者 2026-10-28 決定,RS-41) | 〔觀察〕 |
| `docs/changes/screens/` | 給統整席核對的畫面截圖(`<CHG-ID>/<task>-<原檔名>`,由派發者自 `outputs/ui_check/` 複製並 commit 進該 PR,DIR-6 第 11 條) | 檔名不符 `CHG-*.md`,不受 `doc_integrity_check` 的 CHG 欄位檢查與 `plan_check_changed.py` 檢查;`docs/` 下非 `*.py`、`*.sh`,屬 `review_gate.py` 免審議路徑;截圖隨 repo 公開(CHG-20261030-02) | 〔推論〕(本筆不建目錄,第一個使用者建立) |
| `tests/ci/` | `scripts/` 的單元測試 | `python3 -m unittest discover -s tests/ci -v`(`./scripts/check.sh` 也會一併收集) | 〔觀察〕 |
| `.ai-sdlc/` | vendored ai-sdlc v1.66.0:`skills/ai-sdlc-autopilot/{scripts,assets}`、`SHA256SUMS`、`README.md` | 內容一律不修改;版本與來源記在 `.ai-sdlc/README.md` | 〔觀察〕 |
| `.github/workflows/ci.yml` | GitHub Actions CI:`document`、`chg`、`program`、`review` 四個 job | main ruleset 要求四項全過 | 〔觀察〕 |

## 命名與放置規則

- 〔觀察〕Python 檔 snake_case,平鋪在根目錄;共用邏輯放 `taiwan_ai.py`(模型)或 `media.py`(媒體);節點包裝放 `nodes.py`,設定存取只經 `settings.py`。
- 〔觀察〕對 `tools/` 下第三方程式的修改一律放 `patches/<工具>-<平台>/`,不直接改 `tools/`。
- 〔觀察〕產出檔一律 `outputs/<種類>-<stamp>.<副檔名>`。
- 〔推論〕新增入口(新介面)應沿用「單檔 PEP 723 script + import 共用模組」;〔未知〕專案成長後是否改成 package(2026-10-26 根目錄 8 個 `.py`、約 3000 行)。
- 〔觀察〕前端檔一律放 `static/`,第三方檔只放 `static/vendor/` 並在其 `README.md` 記錄版本、來源與 SHA-256。
- 〔觀察〕使用者資料(`config/studio.db` 等)與產出物一律 ignored,讓網頁存設定不弄髒工作樹(`scripts/deploy_local.sh` 遇到不乾淨的工作樹會中止)。
- 〔觀察〕產品程式的測試放 `tests/test_<模組>.py`(pytest,CHG-20261022-01);CI 與部署腳本的測試放 `tests/ci/`(unittest,CHG-20261022-03)。
- 〔觀察〕開發流程腳本放 `scripts/`,其中 CI 檢查放 `scripts/ci/`、對應測試放 `tests/ci/`;治理基礎設施路徑(`.github/`、`scripts/ci/`、`tests/ci/`、`.ai-sdlc/`、`scripts/local_review.py`、`scripts/review_decision.py`、`scripts/deploy_local.sh`)的 PR 另需擁有者 `infra-approved` 留言(DIR-2 第 3 條)。
- 〔觀察〕`.ai-sdlc/` 只能整批重新複製上游(見 `.ai-sdlc/README.md`「更新方式」),不得逐檔修改。

## CHG-20261029-01 新增

| 路徑 | 用途 |
|------|------|
| `fusion.py` | 合音規則、比對與標示(純函式,只用標準庫) |
| `features/prosody_trim.feature` | 片段靜音裁切、連音、清晰度、語調折線、合音與合音詞庫的行為規格 |
| `tests/test_fusion.py` | 合音規則與 `/api/fusion/suggest` 的測試 |
| `tests/integration/test_shape_real.py`、`tests/integration/shape_metrics.py` | `breezy_worker shape` 的真 venv 整合測試與量測輔助(不列入 `check.sh`) |

## CHG-20261022-02 新增

| 路徑 | 用途 |
|------|------|
| `mcp_registry.py` | local-models 的 MCP 註冊管理模組:Claude Code 與 Codex 的 user scope 註冊狀態查詢、指令組裝(`plan`)與執行(`apply`);寫設定只經 `~/.local/bin/claude`、`~/.local/bin/codex` 的 `mcp add` / `mcp remove`;只用標準庫,不匯入 repo 內其他模組 |
| `tests/test_mcp_registry.py` | `mcp_registry.py` 的 pytest:以假 CLI 腳本與暫存 `CLAUDE_CONFIG_DIR` / `CODEX_HOME` 驗證狀態判斷、指令、env 值遮蔽、鎖與真實設定不變 |
| `tests/test_server_mcp.py` | `studio_server.py` 的 `/api/mcp/status`、`/api/mcp/plan`、`/api/mcp/apply` 端點測試(安全中介層、`confirm`、欄位與列舉檢查、409) |
| `tests/integration/test_mcp_registry_real.py` | 真 CLI 整合測試:暫存 `CLAUDE_CONFIG_DIR` / `CODEX_HOME` 下跑註冊 → 查詢 → 移除 → 查詢,斷言真實設定不變(不列入 `check.sh`,本機手動執行) |
| `features/mcp_registry.feature` | MCP 註冊管理的行為規格(使用者故事 1~7:狀態顯示、開頁不啟動伺服器、重新檢查、確認框、移除、逾時提醒、跨站拒絕) |
