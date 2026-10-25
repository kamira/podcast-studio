# 目錄結構

> 導入反推(2026-10-22,基準點 `7da165b6048897abb8acac4bba69db9677827471`)。來源等級見 `docs/ai-guideline.md` 表頭。
> 2026-10-23 依 CHG-20261022-03 補入 CI/CD 相關目錄(以 repo 追蹤檔為準)。

## 樹狀

```
podcast-studio/
├── podcast_studio.py          # 入口:Gradio 網頁
├── local_models_mcp.py        # 入口:MCP 伺服器(stdio)
├── taiwan_ai.py               # 模型層 + CLI 入口
├── media.py                   # 媒體層
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
│   └── deploy_local.sh        # 本機部署(main → 重啟 7860)
├── tests/
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
│   ├── launch.json            # Claude 桌面 app 啟動網頁
│   └── worktrees/             # agent worktree(ignored,CHG-20261022-03 T9)
├── .gitignore
├── outputs/                   # 產出物(ignored)
└── tools/BreezyVoice/         # 第三方 repo + .venv(ignored,由腳本重建)
```

## 各目錄職責

| 路徑 | 職責 | 備註 | 等級 |
|------|------|------|------|
| `/`(根目錄 `.py`) | 全部程式碼;平鋪,沒有 package | 入口檔以 `sys.path.insert` 互相 import | 〔觀察〕 |
| `patches/breezyvoice-mac/` | 對第三方 BreezyVoice 的 macOS 調整 | 由 `setup_breezyvoice.sh` 複製進 venv site-packages | 〔觀察〕 |
| `docs/` | 治理帳本 | 落點宣告在 Guideline 表頭 | 〔觀察〕 |
| `outputs/` | 產出檔 | `.gitignore` | 〔觀察〕 |
| `tools/` | 第三方工具與 venv(約 1.9 GB) | `.gitignore`;無 `.git` | 〔觀察〕 |
| `.claude/` | Claude 桌面 app 設定(追蹤檔只有 `launch.json`) | `launch.json` 用 `uv` 絕對路徑;`.claude/worktrees/` 是 Claude 桌面 app 為派發施工建立的 agent worktree,列入 `.gitignore`,避免主工作副本被判為不乾淨而讓 `scripts/deploy_local.sh` 中止(CHG-20261022-03 T9) | 〔觀察〕 |
| `docs/knowledge/` | 使用者教過的規則:`INDEX.md`、`directives.md`(DIR-n)、`patterns.md`、`vocabulary.json` | 只改本目錄的 commit 免引用 CHG 編號(CHG-20261022-03) | 〔觀察〕 |
| `features/` | 行為規格(Gherkin `.feature`),由各 CHG 的「Behaviour spec」引用 | `features/ci.feature` 屬 CHG-20261022-03;其餘屬 CHG-20261022-01 / 04,對應功能不在 main 上 | 〔觀察〕 |
| `scripts/ci/` | CI 治理檢查:`commit_check.py`、`plan_check_changed.py`、`review_gate.py`、`secret_scan.py`、`secret_allowlist.txt` | 只用 Python 標準函式庫;CI 一律從 base 分支的 trusted/ 副本(CI 執行期目錄,不在 repo 內)執行 | 〔觀察〕 |
| `scripts/local_review.py` | 本機審議會(DIR-1 六席兩階段),以 `gh pr comment` 貼出判決 | 只讀 PR 資料、不 checkout;拒絕 fork 與非擁有者 PR | 〔觀察〕 |
| `scripts/deploy_local.sh` | 本機部署:在乾淨的 main 上 `pull --ff-only`,重啟 7860 網頁伺服器,驗證只綁本機 | 只寫入 `outputs/` 下的 pidfile 與 log | 〔觀察〕 |
| `tests/ci/` | `scripts/` 的單元測試 | `python3 -m unittest discover -s tests/ci -v`;產品程式本身仍無測試(ADP-6) | 〔觀察〕 |
| `.ai-sdlc/` | vendored ai-sdlc v1.66.0:`skills/ai-sdlc-autopilot/{scripts,assets}`、`SHA256SUMS`、`README.md` | 內容一律不修改;版本與來源記在 `.ai-sdlc/README.md` | 〔觀察〕 |
| `.github/workflows/ci.yml` | GitHub Actions CI:`document`、`chg`、`program`、`review` 四個 job | main ruleset 要求四項全過 | 〔觀察〕 |

## 命名與放置規則

- 〔觀察〕Python 檔 snake_case,平鋪在根目錄;共用邏輯放 `taiwan_ai.py`(模型)或 `media.py`(媒體)。
- 〔觀察〕對 `tools/` 下第三方程式的修改一律放 `patches/<工具>-<平台>/`,不直接改 `tools/`。
- 〔觀察〕產出檔一律 `outputs/<種類>-<stamp>.<副檔名>`。
- 〔推論〕新增入口(新介面)應沿用「單檔 PEP 723 script + import 共用模組」;〔未知〕專案成長後是否改成 package(目前 4 檔、521 行,無此需要)。
- 〔觀察〕產品程式沒有測試(ADP-6);`tests/ci/` 只涵蓋 CI 與部署腳本(CHG-20261022-03)。
- 〔觀察〕開發流程腳本放 `scripts/`,其中 CI 檢查放 `scripts/ci/`、對應測試放 `tests/ci/`;治理基礎設施路徑(`.github/`、`scripts/ci/`、`tests/ci/`、`.ai-sdlc/`、`scripts/local_review.py`、`scripts/deploy_local.sh`)的 PR 另需擁有者 `infra-approved` 留言(DIR-2 第 3 條)。
- 〔觀察〕`.ai-sdlc/` 只能整批重新複製上游(見 `.ai-sdlc/README.md`「更新方式」),不得逐檔修改。
