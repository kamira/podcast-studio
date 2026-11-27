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
> 2026-11-22 依 CHG-20261103-01 結構文件更新(T1):根目錄新增 `value_checks.py`(`project`、`settings` 共用的 `is_int`)與 `tests/test_value_checks.py`。
> 2026-11-22 依 CHG-20261103-01 結構文件更新(T2):`scripts/ci/` 新增 `gitutil.py`(`commit_check`、`plan_check_changed`、`secret_scan` 共用的 `GitError`、git 執行與 `--range` 解析)。
> 2026-11-23 依 CHG-20261123-01 結構文件更新(T1):根目錄新增 Rust virtual workspace(`Cargo.toml`、`Cargo.lock`、`rust-toolchain.toml`)與 `crates/studio-core/`(空骨架);`.gitignore` 補 `/target/`。
> 2026-11-23 依 CHG-20261123-01 結構文件更新(T2):新增 `crates/parity/`(並行比對工具:比對器、JSON 行協定、Rust 單元登錄表、示範單元、Python 轉接器 `py/adapter.py`、示範 fixture `fixtures/demo/`)、`tests/test_parity_adapter.py`、`features/parity.feature`;`Cargo.lock` 新增 `serde`、`serde_json` 及其遞移相依。
> 2026-11-23 依 CHG-20261123-01 結構文件更新(T3):根目錄新增 `deny.toml`(cargo-deny 設定);`.github/workflows/ci.yml` 新增 `rust-platform`(Rust 三平台矩陣)與 `rust`(彙總)兩個 job,共六個 job;main ruleset 要求五項(`rust` 由使用者於 CHG-20261123-01 合併 PR 合入 main 後加入 ruleset,執行計畫第 8 步;ACC 以唯讀讀取 ruleset 確認);新增 `tests/test_ci_rust_job.py`;`features/ci.feature` 新增一個場景;兩個 crate 的 `Cargo.toml` 設 `publish = false`(DIR-5 一次修正補註)。
> 2026-11-25 依 CHG-20261124-01 結構文件更新(T1):`crates/studio-core/` 不再是空骨架——新增 `src/value_checks.rs`(`is_int`)、`tests/value_checks.rs`,相依 `serde_json`;`crates/parity/` 新增 `src/units.rs`(產品模組比對單元)、`fixtures/value_checks/is_int.json`、`tests/module_parity.rs`,以 path 相依 `studio-core`,轉接器 import 產品模組 `names`、`value_checks`;新增 `features/names.feature`;`Cargo.lock` 只多兩條相依邊(package 不變)。
> 2026-11-25 依 CHG-20261124-01 結構文件更新(T2,DIR-5 一次修正補記):`crates/studio-core/` 新增 `src/names.rs`(常數、`name_char_ok`、`normalize_name`、`fold_name`)、`src/names/casefold_table.rs`(Python 3.11 / Unicode 14.0.0 摺疊表)、`tests/names.rs`;`crates/parity/` 新增 `fixtures/names/`(四份)、`tests/names_exhaustive.rs`,`src/units.rs` 加 `names.*` 四個比對單元。
> 2026-11-25 依 CHG-20261124-01 結構文件更新(T3,DIR-5 一次修正補記):`crates/studio-core/src/names.rs` 加 `normalize_new_name`、`is_windows_reserved`、`NewNameError`;新增 `crates/studio-core/tests/names_windows.rs`、`crates/parity/tests/names_differences.rs`。
> 2026-11-25 依 CHG-20261125-01 結構文件更新(T1):新增 workspace 成員 `crates/safe-fs/`(`safe_fs.py` 的 Rust 版;相依 `cap-std`、`cap-fs-ext`;平台條件編譯只在此 crate);`crates/parity/` 以 path 相依 `safe-fs`,新增 `src/units_safe_fs.rs`、`fixtures/safe_fs/`(五份)、`tests/data/safe_fs_expected.json`、`tests/safe_fs_expected.rs`,轉接器 import `safe_fs`,「工具不寫檔」改為「只有 `safe_fs.*` 單元在系統暫存目錄建立並刪除合成檔案樹」;新增 `features/safe_fs.feature`;`Cargo.lock` 新增 41 個 package(既有 13 個不變)。
> 2026-11-25 依 CHG-20261125-01 結構文件更新(T2,DIR-5 一次修正補記):`crates/safe-fs/` 新增 `src/meta.rs`(`Meta`、`EntryKind`)、`tests/mutate.rs`,`src/lib.rs` 加建立、查詢、刪除、搬移類;`crates/parity/fixtures/safe_fs/` 增為 14 份,`src/units_safe_fs.rs` 與轉接器加九個單元。
> 2026-11-25 依 CHG-20261125-01 結構文件更新(T3,DIR-5 一次修正補記):`crates/safe-fs/` 新增 `tests/max_path.rs`;`tests/common/mod.rs` 加 `fold_name`(以 `#[path]` 唯讀引用 studio-core 摺疊表);`tests/windows.rs` 加 H-1、C-1、D-6、D-10、D-11、W-6。
> 2026-11-27 依 CHG-20261127-01 結構文件更新(T1):新增 workspace 成員 `crates/heavy-lock/`(`heavy_lock.py` 的 Rust 版;相依只有 `safe-fs`,不含平台條件編譯)與 `features/heavy_lock_rust.feature`;`Cargo.lock` 只新增 `heavy-lock` 一個 package。
> 2026-11-27 依 CHG-20261127-01 結構文件更新(T2,DIR-5 一次修正補記):`crates/heavy-lock/tests/cross_lang.rs`;職責表 `Error` 補 `Timeout(Duration)`。
> 2026-11-28 依 CHG-20261128-01 結構文件更新(T1):新增 workspace 成員 `crates/project/`(`project.py` 的 Rust 版;相依 `studio-core`、`safe-fs`、`serde`、`serde_json`(開 `float_roundtrip`);`src/` 不含平台條件編譯)與 `features/project_rust.feature`;`crates/parity/` 以 path 相依 `project`,新增 `src/units_project.rs`、`fixtures/project/`(六份)、`tests/project_parity.rs`、`tests/data/project_expected.json`,`tests/module_parity.rs` 在 Windows 另略過 `fixtures/project/`;轉接器只在 `project.*` 單元內延後 import `project`;`Cargo.lock` 只新增 `project` 一個 package。
> 2026-11-28 依 CHG-20261128-01 結構文件更新(T2):`crates/project/` 新增 `src/model.rs`(`Project` 與片段、素材等型別、`to_dict`)、`src/validate.rs`(讀 JSON 原文、完整驗證、5 MB 上限)、`tests/validate.rs`、`tests/json_differences.rs`;`crates/parity/fixtures/project/` 新增 `validate_text.json`(單元 `project.validate_text`)。

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
├── value_checks.py            # 跨模組共用的值檢查:is_int(project、settings 共用;breezy_worker 保留本地副本)(CHG-20261103-01 T1)
├── project.py                 # 專案資料模型:分句、停頓、標註、版本、.trash(CHG-20261022-04 T2)
├── timeline.py                # 時間軸計算 layout():片段、字幕 cue、畫面軌(CHG-20261022-04 T4)
├── tts_service.py             # 逐句合成服務:髒片段、快取、語速後製(CHG-20261022-04 T3)
├── mcp_registry.py            # local-models 的 MCP 註冊管理(Claude Code / Codex user scope,只經官方 CLI;CHG-20261022-02 T1)
├── breezy_worker.py           # BreezyVoice worker(在 BreezyVoice venv 執行:synth / annotate;CHG-20261022-04 T1)
├── Cargo.toml                 # Rust virtual workspace:members = crates/*、resolver 3、edition 2024(CHG-20261123-01 T1)
├── Cargo.lock                 # cargo 產生並入 git;會解析相依的 cargo 指令帶 --locked
├── rust-toolchain.toml        # 釘版工具鏈(完整版號,含 rustfmt、clippy;與 rust-version 相同)
├── deny.toml                  # cargo-deny 設定:授權白名單、bans、只允許 crates.io、漏洞公告(CHG-20261123-01 T3)
├── crates/                    # Rust 程式(CHG-20261123-01 起)
│   ├── studio-core/           # 產品核心:value_checks、names(CHG-20261124-01 起);Cargo.toml、src/lib.rs、src/value_checks.rs、src/names.rs、src/names/casefold_table.rs、tests/skeleton.rs、tests/value_checks.rs、tests/names.rs、tests/names_windows.rs
│   ├── safe-fs/               # 安全路徑層(lib safe_fs;CHG-20261125-01):src/lib.rs(SafeDir 的開啟類與建立、查詢、刪除、搬移類,open_dir、open_path)、src/error.rs(錯誤型別)、src/meta.rs(Meta、EntryKind;T2)、src/name.rs(check_name、Windows 保留裝置名)、src/support.rs(比對與測試用建立連結);tests/open.rs、tests/mutate.rs(三平台,含 D-1、D-2;T2)、tests/max_path.rs(三平台;T3)、tests/windows.rs(Windows 專屬,含開工閘查證)、tests/common/(含 W-6 用的 fold_name;T3)
│   ├── heavy-lock/            # 跨行程重型工作鎖(lib heavy_lock;CHG-20261127-01):src/lib.rs(acquire、acquire_with、HeavyLock、Error;以 safe-fs 開啟 outputs/.heavy.lock、標準函式庫 File::try_lock 上鎖);tests/lock.rs(三平台,同行程)、tests/cross_process.rs(三平台,Rust 對 Rust 跨行程,測試執行檔自我重新執行為輔助行程)、tests/cross_lang.rs(#![cfg(unix)],macOS / Linux,Python 對 Rust 跨語言互斥與 Linux 區辨測試)、tests/common/(暫存工作目錄、建立連結、標記檔交握、輔助行程)
│   ├── project/               # 專案資料模型(lib project,project.py 的 Rust 版;CHG-20261128-01;目前沒有產品呼叫端):Cargo.toml;src/lib.rs(模組說明、#![forbid(unsafe_code)])、src/error.rs(Error、ErrorKind、Result)、src/pyjson.rs(與 Python _dump 逐位元組相同的寫出器、post_key 用的緊湊排序寫法、float_repr、Entries)、src/round.rs(十進位四捨五入)、src/pystr.rs(Python 字串語意:空白字元集與 strip、漢字、注音、marked_to_digit、ID_RE / KEY_RE)、src/constants.rs(project.py 的常數)、src/checks.rs(單值檢查);tests/pyjson.rs(寫出器與 float_roundtrip 解析對 Python 參考輸出)、tests/checks.rs(四捨五入邊界、錯誤訊息、字串語意、常數)(T1);src/model.rs(Project、Defaults、Segment、Annotation、Audio、WordClarity、Pitch、PitchPoint、Post、Fusion、FusionOff、Image、Asset;to_dict、dump_bytes)、src/validate.rs(parse_project_json、validate、dump_validated、check_version);tests/validate.rs(每個錯誤訊息分支、寫出位元組、v1 升級、5 MB 上限、to_dict 與非有限浮點)、tests/json_differences.rs(行為差異 D-1~D-6、D-15,Unix 以 Python 子程序確認)(T2)
│   └── parity/                # 並行比對工具(執行檔 parity;CHG-20261123-01 T2)
│       ├── src/               # lib.rs(協定型別、fixture、登錄表、比對與報告)、py.rs(啟動轉接器)、main.rs(命令列)、demo.rs(示範單元)、units.rs(產品模組比對單元,CHG-20261124-01)、units_safe_fs.rs(safe_fs.* 比對單元與合成檔案樹,CHG-20261125-01)、units_project.rs(project.* 比對單元,CHG-20261128-01)
│       ├── py/adapter.py      # Python 轉接器(標準函式庫 + 明列的產品模組 names、value_checks、safe_fs;project 只在 project.* 單元內延後 import;JSON 行協定)
│       ├── fixtures/demo/     # 示範 fixture:parse_count.json、parse_count_drift.json(合成資料)
│       ├── fixtures/value_checks/ # 產品模組 fixture:is_int.json(合成資料;CHG-20261124-01)
│       ├── fixtures/names/    # 產品模組 fixture:name_char_ok、normalize_name、fold_name、constants(合成資料;CHG-20261124-01)
│       ├── fixtures/safe_fs/  # 產品模組 fixture 14 份(合成檔案樹;CHG-20261125-01):開啟類 check_name、open_dir、open_child、open_file、open_path(T1);建立 / 查詢 / 刪除 / 搬移類 create_excl、open_or_create、lstat、entries、unlink、rmtree、rename、replace、link(T2)
│       ├── fixtures/project/  # 產品模組 fixture(合成資料;CHG-20261128-01):constants、normalize_speed、normalize_pause、check_zhuyin、check_pitch_points、py_json(T1)、validate_text(T2)
│       └── tests/             # fixture_format.rs、protocol.rs、registry.rs、cli.rs、module_parity.rs(demo/ 以外 fixture 的真轉接器比對;Windows 略過 safe_fs/、project/)、names_exhaustive.rs(全碼位比對)、names_differences.rs(刻意差異的 Python 端確認)、safe_fs_expected.rs 與 data/safe_fs_expected.json(以 Python 為錨的預期結果表)、project_parity.rs 與 data/project_expected.json(project.* 的預期結果表,CHG-20261128-01)
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
│   ├── ci.feature             # CHG-20261022-03(「Rust 三平台任一失敗讓 rust 檢查變紅」場景屬 CHG-20261123-01 T3)
│   ├── names.feature          # CHG-20261124-01:名稱規則與數值檢查的 Rust 移植(T1 起逐 task 補場景)
│   ├── safe_fs.feature        # CHG-20261125-01:安全路徑層的 Rust 移植(T1 起逐 task 補場景)
│   ├── heavy_lock_rust.feature # CHG-20261127-01:重型工作鎖的 Rust 移植(T1 兩個場景,T2 補跨語言場景;Python 版仍是 heavy_lock.feature)
│   ├── project_rust.feature   # CHG-20261128-01:專案資料模型的 Rust 移植(T1 起逐 task 補場景;Python 版的既有 feature 不改)
│   ├── parity.feature         # CHG-20261123-01 T2:並行比對工具
│   └── …                      # canvas、file_dnd、heavy_lock、local_review_opus、mcp_registry、pause_tone、settings、timeline_editor、wizard_speed
├── scripts/                   # 開發流程腳本(CHG-20261022-03)
│   ├── ci/
│   │   ├── commit_check.py        # commit 引用 CHG 編號檢查
│   │   ├── gitutil.py             # CI 腳本共用:GitError、git 執行、範圍解析(CHG-20261103-01)
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
│   ├── test_parity_adapter.py # 比對工具 Python 轉接器的 pytest(CHG-20261123-01 T2)
│   ├── test_ci_rust_job.py    # CI Rust job、deny.toml、ci.feature 新場景的結構測試(CHG-20261123-01 T3)
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
│   └── workflows/ci.yml       # CI:document / chg / program / review / rust-platform(三平台矩陣)/ rust(彙總)
├── .claude/
│   ├── launch.json            # Claude 桌面 app 啟動網頁(uv run --script studio_server.py)
│   ├── agents/
│   │   └── review-seat-opus.md  # 本機審議 Opus 意見席的子代理定義(只有 Write 工具;CHG-20261101-02)
│   └── worktrees/             # agent worktree(ignored,CHG-20261022-03 T9)
├── .gitignore                 # 含 Rust 建置產物 /target/(CHG-20261123-01 T1)
├── outputs/                   # 產出物(ignored);uploads/ 為上傳檔,.heavy.lock 為重型鎖,ui_check/ 為 UI 檢查截圖,.tmp/ 為 /api/annotate 暫存,.tmp/review_decision/ 為統整席判定 JSON,.tmp/local_review/ 為本機審議 Codex 席暫存與 Opus 席請求檔 / 答案檔(opus/)
├── projects/                  # 專案資料(ignored,使用者資料):<名稱>/{project.json,history/,audio/,assets/,tmp/,.lock}、.trash/
├── .review/                   # 本機審議 Codex 席的 PR head 唯讀檢視(ignored,審完即移除;CHG-20261028-01)
└── tools/BreezyVoice/         # 第三方 repo + .venv(ignored,由腳本重建)
```

## 各目錄職責

| 路徑 | 職責 | 備註 | 等級 |
|------|------|------|------|
| `/`(根目錄 `.py`) | 全部 Python 程式碼;平鋪,沒有 package | 入口檔(`studio_server.py`、`local_models_mcp.py`)以 `sys.path.insert` import 共用模組;相依方向見 `logical.md`;Rust 程式在 `crates/`(CHG-20261123-01 起) | 〔觀察〕 |
| `Cargo.toml`、`Cargo.lock`、`rust-toolchain.toml`(Rust workspace 與釘版工具鏈) | 根目錄 virtual workspace:`members = ["crates/*"]`、`resolver = "3"`、`[workspace.package]` 的 `edition = "2024"` 與 `rust-version`(與 `rust-toolchain.toml` 的 `channel` 同一完整版號);`rust-toolchain.toml` 另列 `rustfmt`、`clippy` 元件、`profile = "minimal"`;`Cargo.lock` 入 git | rustup 由工作目錄往上找 `rust-toolchain.toml`,repo 內任何位置都套用同一釘版;會解析相依的 cargo 指令帶 `--locked`(`cargo fmt` 除外);升級工具鏈另開 CHG;建置產物 `target/` 由 `.gitignore` 忽略;成員目錄名與 package 名相同(kebab-case) | 〔觀察〕CHG-20261123-01 T1 |
| `crates/studio-core/` | Rust 產品核心:`Cargo.toml`(相依只有 `serde_json` 1.0.154,預設 feature,CHG-20261124-01 代決 4)、`src/lib.rs`(crate 說明文件、`#![forbid(unsafe_code)]`、`pub mod value_checks`、`pub mod names`)、`src/value_checks.rs`(`is_int(&serde_json::Value) -> bool`,Python `value_checks.is_int` 的 Rust 版,純函式)、`src/names.rs`(與 Python 等價的相容部分:常數、`name_char_ok`、`normalize_name`、`fold_name`;只用於新名稱的 `normalize_new_name`、`is_windows_reserved`、`NewNameError`;純函式、不分平台)、`src/names/casefold_table.rs`(`fold_name` 的摺疊表,CPython 3.11.18 / Unicode 14.0.0、1530 筆;表頭記產生指令與 SHA-256;Unicode 升版另開 CHG)、`tests/skeleton.rs`(crate 可連結、`rust-version` 與 `rust-toolchain.toml` 版號一致)、`tests/value_checks.rs`(`is_int` 與 serde_json 對 `-0`、超出範圍整數的解析結果,行為差異 D-5、D-6)、`tests/names.rs`(相容部分)、`tests/names_windows.rs`(Windows 保留名稱 24 個、192 種寫法只擋新名稱,行為差異 D-1~D-3) | 路線圖第 2 筆起逐模組加入;`Cargo.toml` 設 `publish = false`(不發佈;cargo-deny 依此略過,CHG-20261123-01 T3),之後在 `crates/` 新增的成員也須設定 | 〔觀察〕CHG-20261123-01 T1、CHG-20261124-01 T1、T2、T3 |
| `crates/parity/` | 並行比對工具(執行檔 `parity`):`src/lib.rs`(協定型別 `Outcome`、`Fixture`、Rust 單元登錄表 `Registry`、`compare` 與文字 / JSON 報告)、`src/py.rs`(以 argv 清單啟動 `py/adapter.py`、逾時終止)、`src/main.rs`(`run`、`list` 子命令,退出碼 0 / 1 / 2)、`src/demo.rs` 與轉接器內的示範單元 `demo.parse_count`、`demo.parse_count_drift`(測試專用 `demo.sleep` 只在 `PARITY_TEST_SLEEP=1` 時登錄);`src/units.rs` 與轉接器內的產品模組單元 `value_checks.is_int` 與 `names.name_char_ok`、`names.normalize_name`、`names.fold_name`、`names.constants`(CHG-20261124-01);`fixtures/<模組>/` 放 fixture;`tests/` 為契約測試,`tests/module_parity.rs` 對 `fixtures/` 下 `demo/` 以外的每個 fixture 以真轉接器比對,`tests/names_exhaustive.rs` 對全部 Unicode 純量值比對 `names.fold_name`、`names.name_char_ok`,`tests/names_differences.rs` 以真轉接器確認刻意差異的名稱在 Python 端合格;`src/units_safe_fs.rs` 與轉接器內的 `safe_fs.*` 14 個單元(開啟類 5、建立 / 查詢 / 刪除 / 搬移類 9)、`fixtures/safe_fs/`、以 Python 為錨的預期結果表 `tests/data/safe_fs_expected.json` 與 `tests/safe_fs_expected.rs`(Windows 上 `module_parity.rs` 略過 `safe_fs/`,改由預期結果表對照;CHG-20261125-01)(皆由 `cargo test` 在三平台 CI 執行);`src/units_project.rs` 與轉接器內的 `project.*` 單元(T1 六個:`constants`、`normalize_speed`、`normalize_pause`、`check_zhuyin`、`check_pitch_points`、`py_json`;T2 的 `validate_text` 在轉接器自己以 `tempfile.mkdtemp` 建立的暫存目錄寫一個原文檔,以 `_read_project_file` 讀回,案例結束即刪除;超長的重複內容以 `{"parts"}` 精簡形式寫入 fixture 與預期結果表,`tests/project_parity.rs` 守門每行 ≤ 12000 字元、同一字元連續 ≤ 999 個)、`fixtures/project/`、以 Python 為錨的預期結果表 `tests/data/project_expected.json` 與 `tests/project_parity.rs`(Windows 上 `module_parity.rs` 另略過 `project/`,改由預期結果表對照;CHG-20261128-01) | 外部相依只有 `serde`(derive)、`serde_json`,另以 path 相依 `studio-core`(CHG-20261124-01);另以 path 相依 `safe-fs`(CHG-20261125-01)與 `project`(CHG-20261128-01);轉接器只 import 標準函式庫與明列的產品模組 `names`、`value_checks`、`safe_fs`、`project`(`project` 只能在 `project.*` 單元內延後 import,CHG-20261128-01 事實 H)(repo 根目錄插入 `sys.path[0]`;集合以外的 import 由 `tests/test_parity_adapter.py` 的 AST 斷言擋下);fixture 只放合成資料,不得放使用者資料;不使用網路;只有 `safe_fs.*` 單元(`src/units_safe_fs.rs` 與轉接器)與轉接器的 `project.validate_text`(CHG-20261128-01 T2)在系統暫存目錄建立唯一名稱的工作目錄、放合成檔案或檔案樹,案例結束即刪除,不寫其他任何位置;示範單元只屬比對工具,不得被產品程式引用;Python 端測試為 `tests/test_parity_adapter.py`(由 `scripts/check.sh` 收集);`Cargo.toml` 設 `publish = false`(不發佈;cargo-deny 依此略過,CHG-20261123-01 T3) | 〔觀察〕CHG-20261123-01 T2、CHG-20261124-01 T1、T2、T3、CHG-20261125-01 T1、T2、CHG-20261128-01 T1、T2 |
| `crates/safe-fs/` | 安全路徑層(lib `safe_fs`,`safe_fs.py` 的 Rust 版;CHG-20261125-01):`src/lib.rs`(`SafeDir`、`open_dir`、`open_path`、`FileAccess`、`DirOptions`;`SafeDir` 的建立、查詢、刪除、搬移類 `create_excl`、`open_or_create`、`lstat`、`entries`、`unlink`、`rmtree`、`rename`、`replace`、`hard_link`,T2)、`src/error.rs`(`Error`、`ErrorKind`)、`src/meta.rs`(`Meta`、`EntryKind`;T2)、`src/name.rs`(`check_name`、`reserved_device`)、`src/support.rs`(比對與測試用的建立符號連結,不是產品介面);`tests/open.rs`(三平台)、`tests/mutate.rs`(三平台,含 D-1、D-2;T2)、`tests/max_path.rs`(三平台,深層路徑;T3)、`tests/windows.rs`(`#[cfg(windows)]`,含 T1 開工閘查證與 T3 的 H-1、C-1、D-6、D-10、D-11、W-6)、`tests/common/`(暫存工作目錄 `Drop` 守衛、快照、建立連結與 junction;W-6 用的 `fold_name` 以 `#[path]` 唯讀引用 `crates/studio-core/src/names/casefold_table.rs`,T3) | 第三方直接相依只有 `cap-std`、`cap-fs-ext`(授權與傳遞相依見 CHG-20261125-01 派發記錄);`#![forbid(unsafe_code)]`;平台條件編譯(`cfg(windows)` / `cfg(unix)`)只在此 crate 與測試檔;`Cargo.toml` 設 `publish = false` | 〔觀察〕CHG-20261125-01 T1、T2、T3 |
| `crates/heavy-lock/` | 跨行程重型工作鎖(lib `heavy_lock`,`heavy_lock.py` 的 Rust 版;CHG-20261127-01):`src/lib.rs`(常數 `LOCK_DIR`、`LOCK_NAME`、`POLL_INTERVAL`;`acquire`、`acquire_with`、`HeavyLock`(`Drop` 時解鎖)、`Error::{Timeout(Duration), Path, Lock}`;以 `safe-fs` 的 `open_dir`、`open_or_create` 開啟 `<root>/outputs/.heavy.lock`,以標準函式庫 `File::try_lock` 輪詢取鎖)、`tests/lock.rs`(三平台,同行程語意與行為差異 D-1、D-2、D-4)、`tests/cross_process.rs`(三平台,Rust 對 Rust 跨行程互斥與被終止後釋放,D-3)、`tests/cross_lang.rs`(`#![cfg(unix)]`,macOS / Linux,Python 對 Rust 跨語言互斥與 Linux 區辨測試;Python 子行程以 `-I -B -X utf8` 啟動、唯讀匯入 repo 根目錄的 `heavy_lock.py`)、`tests/common/`(暫存工作目錄 `Drop` 守衛、建立連結、標記檔交握、以測試執行檔自我重新執行的持鎖輔助行程) | 相依只有 workspace 內的 `safe-fs`(path 相依帶 `version = "=0.0.0"`),不引入第三方 crate;`#![forbid(unsafe_code)]`;`src/` 不含平台條件編譯(平台差異由標準函式庫與 `safe-fs` 承擔);鎖檔只鎖、不讀不寫不刪;目前沒有呼叫端(路線圖第 10、14、16、22 筆);`Cargo.toml` 設 `publish = false` | 〔觀察〕CHG-20261127-01 T1、T2 |
| `crates/project/` | 專案資料模型(lib `project`,`project.py` 的 Rust 版;CHG-20261128-01):`Cargo.toml`;`src/lib.rs`(模組說明、`#![forbid(unsafe_code)]`、再匯出 `Error`、`ErrorKind`、`Result`)、`src/error.rs`(錯誤類別與逐字訊息清單,`Display` 以「;」串接)、`src/pyjson.rs`(`dump` / `dump_bytes` 與 Python `_dump` 逐位元組相同、`dumps_sorted` 為 `post_key` 的緊湊排序寫法、`float_repr`、`Entries`)、`src/round.rs`(`Decimal(str(v))` + `ROUND_HALF_UP` 的整數運算)、`src/pystr.rs`(Python 3.11 的空白字元集與 `strip`、碼位長度、漢字、注音、`marked_to_digit`、`ID_RE` / `KEY_RE`)、`src/constants.rs`(`project.py` 的常數)、`src/checks.rs`(`check_name`、`normalize_speed`、`normalize_pause`、`check_zhuyin`、`check_percent`、`check_pitch_points`);`tests/pyjson.rs`(寫出器與 `float_roundtrip` 解析對 CPython 3.11 參考輸出)、`tests/checks.rs`(四捨五入邊界、錯誤訊息、字串語意、常數)(T1);`src/model.rs`(`Project` 與片段、素材等型別,欄位順序即寫出的鍵順序;`to_dict`、`dump_bytes` = `_dump(validate(to_dict()))`)、`src/validate.rs`(`parse_project_json`、`validate`、`dump_validated`、`check_version`);`tests/validate.rs`、`tests/json_differences.rs`(T2) | 相依只有 workspace 內的 `studio-core`、`safe-fs`(path 相依帶 `version = "=0.0.0"`)與 `Cargo.lock` 既有的 `serde`(derive)、`serde_json`(開 `float_roundtrip`,不新增 package);`#![forbid(unsafe_code)]`;`src/` 不含平台條件編譯(平台差異由標準函式庫與 `safe-fs` 承擔);目前沒有產品呼叫端(路線圖第 6、7、13~16、18、21 筆);`Cargo.toml` 設 `publish = false` | 〔觀察〕CHG-20261128-01 T1、T2 |
| `deny.toml` | cargo-deny 設定(CHG-20261123-01 代決 10):`[graph] targets` 列三平台六個目標三元組;`licenses` 白名單只列寬鬆授權(不含 copyleft),`[licenses.private] ignore = true` 只略過標記 `publish = false` 的 workspace 成員;`bans` 禁止萬用字元版本、多版本只警告;`sources` 只允許 crates.io、不允許 git 相依;`advisories` 一律阻擋(含 unmaintained、unsound、yanked),`ignore` 為空 | CI 只在 `rust-platform` 的 ubuntu 一格以 `cargo deny --locked check` 執行;任何放寬(補列授權、豁免公告、其他來源)須另經 CHG;結構測試 `tests/test_ci_rust_job.py` | 〔觀察〕CHG-20261123-01 T3 |
| `static/` | 網頁前端:`index.html`(首頁「精靈」「完整模式」兩張卡片、右上角 ⚙ → 設定頁;設定頁子頁含「語音預設」與「進階流程」節點畫布;底部「免責說明與授權」連結)、`app.js`(共用工具 `window.Studio`、hash 路由、設定頁、畫布)、`project.js`(專案狀態與文稿 / 封面 / 語音 / 匯出面板)、`timeline.js`(時間軸三軌、預覽、屬性、素材庫)、`wizard.js`(首頁、精靈、完整模式)、`edit.js`(停頓、聲調、語速、畫面軌編輯)、`style.css` | 原生 JS,不經建置、不從 CDN 載入;由 `studio_server.py` 只解析 `static/` 之下的真實路徑送出;首頁的 `__STUDIO_TOKEN__` 換成啟動權杖;後四個模組依序載入,以 `Studio.hook(name, fn)` 串接(CHG-20261022-04 T7、T8) | 〔觀察〕 |
| `projects/` | 專案資料(CHG-20261022-04):`<名稱>/project.json`(`schema_version: 1`)、`history/`(最近 10 版)、`audio/`(合成與自錄音檔)、`assets/`(素材)、`tmp/`(worker 輸入檔,用完即刪)、`.lock`(專案層級鎖);`.trash/<名稱>-<時間戳>-<4 碼>/` 為刪除的專案(30 天後啟動時清除) | `.gitignore`(使用者資料);只經 `project.py` 存取;不經 `/files/*` 提供,音檔與素材只經 `/api/projects/{p}/media/{id}` 送出 | 〔觀察〕 |
| 根目錄 CHG-20261022-04 模組 | `safe_fs.py`(安全路徑工具,只用標準庫,不 import repo 內其他模組;`project`、`studio_server`、`nodes`、`media`、`tts_service`、`heavy_lock`、`settings` 共用,DIR-5 修正 4)、`names.py`(名稱白名單,`studio_server.py` 與 `project.py` 共用)、`project.py`(資料模型)、`timeline.py`(純函式 `layout()`)、`tts_service.py`(合成服務)、`breezy_worker.py`(以 BreezyVoice venv 執行、只用標準庫與 BreezyVoice 模組,不 import repo 內其他模組) | 相依方向見 `logical.md` | 〔觀察〕 |
| 根目錄 `value_checks.py` | 跨模組共用的值檢查 `is_int`(只用標準庫,不 import repo 內其他模組;`project`、`settings` 共用;`breezy_worker.py` 在 BreezyVoice venv 執行,保留本地副本) | 測試:`tests/test_value_checks.py` | 〔觀察〕CHG-20261103-01 T1 |
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
| `features/` | 行為規格(Gherkin `.feature`),由各 CHG 的「Behaviour spec」引用 | `ci.feature` 屬 CHG-20261022-03(「統整席判定需修正」場景由 CHG-20261028-02 改寫;「Rust 三平台任一失敗讓 rust 檢查變紅」場景由 CHG-20261123-01 T3 新增);`local_review_codex.feature` 屬 CHG-20261028-01;`review_decision.feature` 屬 CHG-20261028-02;`canvas`、`file_dnd`、`heavy_lock`、`settings` 屬 CHG-20261022-01;`pause_tone`、`timeline_editor`、`wizard_speed` 屬 CHG-20261022-04(對應功能隨該筆合併 PR 進 main);`mcp_registry` 屬 CHG-20261022-02;`local_review_opus` 屬 CHG-20261101-02;`parity` 屬 CHG-20261123-01;`names` 屬 CHG-20261124-01;`safe_fs` 屬 CHG-20261125-01;`heavy_lock_rust` 屬 CHG-20261127-01(`heavy_lock` 仍為 Python 版,不改);`project_rust` 屬 CHG-20261128-01(Python 版的既有 feature 不改) | 〔觀察〕 |
| `scripts/ci/` | CI 治理檢查:`commit_check.py`、`plan_check_changed.py`、`review_gate.py`(驗意見留言與統整判定)、`secret_scan.py`、`secret_allowlist.txt`;`gitutil.py`(`GitError`、git 執行、`--range` 解析,`commit_check`、`plan_check_changed`、`secret_scan` 共用;CI 從 trusted 副本同目錄載入,CHG-20261103-01) | 只用 Python 標準函式庫;CI 一律從 base 分支的 trusted/ 副本(CI 執行期目錄,不在 repo 內)執行 | 〔觀察〕 |
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
| `.github/workflows/ci.yml` | GitHub Actions CI:`document`、`chg`、`program`、`review`、`rust-platform`(Rust 三平台矩陣:ubuntu、macos、windows)、`rust`(彙總)六個 job | main ruleset 要求五項全過(`document`、`chg`、`program`、`review`、`rust`;現為四項,`rust` 於 CHG-20261123-01 合併 PR 合入 main 後由使用者加入 ruleset,執行計畫第 8 步;ACC observe (4) 以唯讀讀取 ruleset 確認);`rust-platform` 各格的檢查名稱不列入 ruleset | 〔觀察〕;CHG-20261123-01 T3 |

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
