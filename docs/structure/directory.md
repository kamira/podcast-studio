# 目錄結構

> 導入反推(2026-10-22,基準點 `7da165b6048897abb8acac4bba69db9677827471`)。來源等級見 `docs/ai-guideline.md` 表頭。
> 2026-10-23 依 CHG-20261022-03 補入 CI/CD 相關目錄(以 repo 追蹤檔為準)。
> 2026-10-26 依 CHG-20261022-01 結構文件更新:新增網頁伺服器、節點引擎、設定模組、`static/`、`config/`、`workflows/`、`tests/`;移除 Gradio 版 `podcast_studio.py`(以 T1~T13 合併後的 main 為準)。
> 2026-10-26 依 CHG-20261026-01 結構文件更新:`workflows/` 只追蹤 `default.json`,使用者另存的檔案被 `.gitignore` 忽略。

## 樹狀

```
podcast-studio/
├── studio_server.py           # 入口:網頁伺服器(FastAPI,只綁 127.0.0.1:7860)
├── local_models_mcp.py        # 入口:MCP 伺服器(stdio)
├── executor.py                # 流程圖驗證與依拓撲順序執行
├── nodes.py                   # 節點登錄表(十種節點)
├── settings.py                # 模型設定讀寫(SQLite config/studio.db)
├── heavy_lock.py              # 跨行程重型工作鎖(outputs/.heavy.lock)
├── taiwan_ai.py               # 模型層(共用函式庫;CLI 已移除)
├── media.py                   # 媒體層
├── static/                    # 網頁前端(原生 HTML/JS/CSS,免建置)
│   ├── index.html             # 首頁、設定頁(含「進階流程」節點畫布)、免責說明頁
│   ├── app.js
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
│   ├── acceptance/            # ACC-YYYYMMDD-NN.md(另有 .gitkeep)
│   └── knowledge/{INDEX.md,directives.md,patterns.md,vocabulary.json}
├── features/                  # 行為規格(Gherkin .feature,CHG 的 Behaviour spec)
│   ├── ci.feature             # CHG-20261022-03
│   └── …                      # canvas、file_dnd、heavy_lock、pause_tone、settings、timeline_editor、wizard_speed
├── scripts/                   # 開發流程腳本(CHG-20261022-03)
│   ├── ci/
│   │   ├── commit_check.py        # commit 引用 CHG 編號檢查
│   │   ├── plan_check_changed.py  # PR 改到的 CHG 做 plan-check
│   │   ├── review_gate.py         # 驗本機審議留言 / infra-approved
│   │   ├── secret_scan.py         # 全 repo 與 commit 範圍秘密掃描
│   │   └── secret_allowlist.txt   # 秘密掃描具名豁免清單
│   ├── local_review.py        # 本機審議會(DIR-1),以 PR 留言貼判決
│   ├── check.sh               # 產品測試載具:以 uv 暫時環境執行 pytest
│   └── deploy_local.sh        # 本機部署(main → 重啟 7860 的 studio_server.py)
├── tests/
│   ├── conftest.py            # 把 repo 根目錄放進 sys.path
│   ├── test_*.py              # 產品程式的 pytest(CHG-20261022-01)
│   ├── ui_check.py            # UI 互動檢查(Playwright 無頭 Chromium;不屬 pytest,見下表)
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
│   └── worktrees/             # agent worktree(ignored,CHG-20261022-03 T9)
├── .gitignore
├── outputs/                   # 產出物(ignored);uploads/ 為上傳檔,.heavy.lock 為重型鎖,ui_check/ 為 UI 檢查截圖
└── tools/BreezyVoice/         # 第三方 repo + .venv(ignored,由腳本重建)
```

## 各目錄職責

| 路徑 | 職責 | 備註 | 等級 |
|------|------|------|------|
| `/`(根目錄 `.py`) | 全部 Python 程式碼;平鋪,沒有 package | 入口檔(`studio_server.py`、`local_models_mcp.py`)以 `sys.path.insert` import 共用模組;相依方向見 `logical.md` | 〔觀察〕 |
| `static/` | 網頁前端:`index.html`(首頁右上角 ⚙ → 設定頁;設定頁子頁含「進階流程」節點畫布)、`app.js`、`style.css` | 原生 JS,不經建置、不從 CDN 載入;由 `studio_server.py` 只解析 `static/` 之下的真實路徑送出;首頁的 `__STUDIO_TOKEN__` 換成啟動權杖 | 〔觀察〕 |
| `static/vendor/` | Drawflow 0.0.60 的 `drawflow.min.js`、`drawflow.min.css`、`LICENSE.drawflow` 與 `README.md` | 唯一第三方前端檔(CHG-20261022-01 前置 P1);`tests/test_static.py` 比對 README 記載的 SHA-256 | 〔觀察〕 |
| `workflows/` | 工作流程 JSON(executor 流程圖格式);`default.json` 為預設範本 | 由 `GET/PUT /api/workflows/{name}` 讀寫;使用者另存的檔案(與存檔暫存檔 `.wf-*.tmp`)也落在這裡;git 只追蹤 `default.json`,其他檔案由 `.gitignore` 的 `workflows/*`、`!workflows/default.json` 忽略,存檔後工作樹仍乾淨(CHG-20261026-01 T3);日後不在此新增其他追蹤檔 | 〔觀察〕 |
| `config/` | 使用者設定:`studio.db`(與 `-wal`、`-shm`)、`.studio.db.lock`、舊 `models.json` | 全部列入 `.gitignore`;只經 `settings.py` 存取,`/files/*` 不提供 | 〔觀察〕 |
| `patches/breezyvoice-mac/` | 對第三方 BreezyVoice 的 macOS 調整 | 由 `setup_breezyvoice.sh` 複製進 venv site-packages | 〔觀察〕 |
| `docs/` | 治理帳本 | 落點宣告在 Guideline 表頭 | 〔觀察〕 |
| `outputs/` | 產出檔;`uploads/` 上傳檔、`.heavy.lock` 重型鎖檔、`.studio.pid` 與 `studio_server.log`(部署腳本)、`ui_check/` UI 檢查截圖 | `.gitignore`;網頁 `/files/*` 只提供本目錄之下、不以 `.` 開頭的檔案 | 〔觀察〕 |
| `tools/` | 第三方工具與 venv(約 1.9 GB) | `.gitignore`;無 `.git` | 〔觀察〕 |
| `.claude/` | Claude 桌面 app 設定(追蹤檔只有 `launch.json`) | `launch.json` 用 `uv` 絕對路徑,參數 `run --script studio_server.py`(不帶設定檔路徑);`.claude/worktrees/` 是 Claude 桌面 app 為派發施工建立的 agent worktree,列入 `.gitignore`,避免主工作副本被判為不乾淨而讓 `scripts/deploy_local.sh` 中止(CHG-20261022-03 T9) | 〔觀察〕 |
| `docs/knowledge/` | 使用者教過的規則:`INDEX.md`、`directives.md`(DIR-n)、`patterns.md`、`vocabulary.json` | 只改本目錄的 commit 免引用 CHG 編號(CHG-20261022-03) | 〔觀察〕 |
| `features/` | 行為規格(Gherkin `.feature`),由各 CHG 的「Behaviour spec」引用 | `ci.feature` 屬 CHG-20261022-03;`canvas`、`file_dnd`、`heavy_lock`、`settings` 屬 CHG-20261022-01;`pause_tone`、`timeline_editor`、`wizard_speed` 屬 CHG-20261022-04,對應功能不在 main 上 | 〔觀察〕 |
| `scripts/ci/` | CI 治理檢查:`commit_check.py`、`plan_check_changed.py`、`review_gate.py`、`secret_scan.py`、`secret_allowlist.txt` | 只用 Python 標準函式庫;CI 一律從 base 分支的 trusted/ 副本(CI 執行期目錄,不在 repo 內)執行 | 〔觀察〕 |
| `scripts/local_review.py` | 本機審議會(DIR-1 六席兩階段),以 `gh pr comment` 貼出判決 | 只讀 PR 資料、不 checkout;拒絕 fork 與非擁有者 PR | 〔觀察〕 |
| `scripts/check.sh` | 產品測試載具(CHG-20261022-01 T1):以 uv 暫時環境帶 pytest、fastapi 等依賴執行 `pytest -q tests/` | 可帶測試路徑或 pytest 參數;CI `program` job 與 `scripts/deploy_local.sh` 都會執行 | 〔觀察〕 |
| `scripts/deploy_local.sh` | 本機部署:在乾淨的 main 上 `pull --ff-only`,重啟 7860 網頁伺服器(`uv run --script studio_server.py`),驗證只綁本機 | 只寫入 `outputs/` 下的 pidfile 與 log | 〔觀察〕 |
| `tests/`(根層) | 產品程式的 pytest:設定、節點、執行器、重型鎖、伺服器核心 / 檔案 / 設定 API、前端靜態檢查、預設範本 | 以 `./scripts/check.sh` 執行;模型呼叫一律以替身取代;ADP-6 的結案以 ACC 為準 | 〔觀察〕 |
| `tests/ui_check.py` | UI 互動檢查(CHG-20261022-01 T14):PEP 723 script(playwright 1.64.1),本行程內以替身執行器與替身 Ollama 清單啟動 `create_app()`(隨機空閒埠,設定資料庫與 `outputs/`、`workflows/` 放暫存目錄),以無頭 Chromium 操作 ⚙ → 設定 › 進階流程、拖節點、連線、拖檔、釘住、設定存檔;截圖寫 `outputs/ui_check/` | `uv run --script tests/ui_check.py`;需先安裝 Chromium(前置 P2);檔名不是 `test_*.py`,`./scripts/check.sh` 與 CI 不執行 | 〔觀察〕 |
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
- 〔觀察〕開發流程腳本放 `scripts/`,其中 CI 檢查放 `scripts/ci/`、對應測試放 `tests/ci/`;治理基礎設施路徑(`.github/`、`scripts/ci/`、`tests/ci/`、`.ai-sdlc/`、`scripts/local_review.py`、`scripts/deploy_local.sh`)的 PR 另需擁有者 `infra-approved` 留言(DIR-2 第 3 條)。
- 〔觀察〕`.ai-sdlc/` 只能整批重新複製上游(見 `.ai-sdlc/README.md`「更新方式」),不得逐檔修改。
