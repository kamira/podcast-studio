# language: zh-TW
# CHG-20261128-01 · 專案資料模型(project.py)的 Rust 移植;Python 版的既有 feature(timeline_editor 等)不改
# T1 · 對應測試:crates/parity/tests/project_parity.rs(「數值四捨五入與錯誤訊息與 Python 相同」:
# rust_matches_expected_on_this_platform、python_matches_expected_on_macos_and_linux、parity_run_project_fixtures_exit_0;
# fixture 為 crates/parity/fixtures/project/normalize_speed.json、normalize_pause.json 等六份),
# 四捨五入邊界與錯誤訊息另見 crates/project/tests/checks.rs
# T2 · 對應測試:crates/parity/tests/project_parity.rs(fixture crates/parity/fixtures/project/validate_text.json,
# 單元 project.validate_text:兩邊各自解析同一份原文,接受時比對 _dump 字串、拒絕時比對錯誤訊息清單),
# 每個錯誤訊息分支與寫出位元組另見 crates/project/tests/validate.rs,刻意差異 D-1~D-6 見 crates/project/tests/json_differences.rs
# T3 · 對應測試:crates/parity/tests/project_parity.rs(fixture crates/parity/fixtures/project/ 的 split_segments、escape_text、
# tts_text、fused_out、model_units、shift_marks(「分句與合成文字與 Python 相同」)與 post_params、post_key、is_post_fresh、
# is_default_post、effective(「後製鍵與 Python 相同」);片段類單元兩邊都先以 validate 驗證),
# 移植的 Python 案例與未驗證片段的重新檢查見 crates/project/tests/text.rs、post.rs,自寫 SHA-256 見 crates/project/tests/sha256.rs
# T4 · 對應測試:crates/parity/tests/project_parity.rs(fixture crates/parity/fixtures/project/ 的 load、load_version、
# list_history、list_projects、disk_usage;合成檔案樹,結果另含呼叫後的樹與 changed(須為空))與 crates/project/tests/read.rs
# (「Rust 載入既有專案的結果與 Python 相同且不改動任何檔案」);crates/project/tests/readonly_tool.rs(Unix,「唯讀比對只回報數量」:
# 六行輸出與前後雜湊清單),非 UTF-8 目錄項見 crates/project/tests/non_utf8.rs,Windows 保留裝置名見 crates/project/tests/windows.rs
# T1 新增第一個場景;T2~T7 各補場景(CHG-20261128-01「### Behaviour spec」)
功能: 專案資料模型的 Rust 移植

  場景: 數值四捨五入與錯誤訊息與 Python 相同
    假設 同一組語速與停頓輸入分別交給 Python 的 project 與 Rust 的 project
    那麼 四捨五入後的值與錯誤訊息逐字相同

  場景: 專案檔驗證結果與錯誤訊息與 Python 相同
    假設 同一份專案檔原文分別交給兩邊
    那麼 兩邊同時接受或同時拒絕,拒絕時的錯誤訊息清單逐字相同

  場景: Rust 寫出的位元組與 Python 相同
    假設 一份兩邊都接受的專案檔
    那麼 Rust 寫出的位元組與 Python 的 _dump 結果相同

  場景: 分句與合成文字與 Python 相同
    假設 同一段文稿與標註、合音
    那麼 兩邊的分句結果與合成文字相同

  場景: 後製鍵與 Python 相同
    假設 同一個片段的後製參數
    那麼 兩邊算出的後製鍵相同

  場景: Rust 載入既有專案的結果與 Python 相同且不改動任何檔案
    假設 projects/ 內有一般專案、v1 專案、損壞的專案與 history 版本
    當 Rust 以唯讀方式列出並載入全部專案與版本
    那麼 每一筆結果與 Python 相同
    而且 載入前後 projects/ 內每個檔案的雜湊不變、沒有新增任何檔案

  場景: 唯讀比對只回報數量
    當 唯讀比對工具跑完
    那麼 輸出只有專案數、版本數、一致與不一致的筆數與雜湊是否一致,沒有任何專案名稱或內容
