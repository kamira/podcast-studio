# language: zh-TW
# CHG-20261128-01 · 專案資料模型(project.py)的 Rust 移植;Python 版的既有 feature(timeline_editor 等)不改
# T1 · 對應測試:crates/parity/tests/project_parity.rs(「數值四捨五入與錯誤訊息與 Python 相同」:
# rust_matches_expected_on_this_platform、python_matches_expected_on_macos_and_linux、parity_run_project_fixtures_exit_0;
# fixture 為 crates/parity/fixtures/project/normalize_speed.json、normalize_pause.json 等六份),
# 四捨五入邊界與錯誤訊息另見 crates/project/tests/checks.rs
# T2 · 對應測試:crates/parity/tests/project_parity.rs(fixture crates/parity/fixtures/project/validate_text.json,
# 單元 project.validate_text:兩邊各自解析同一份原文,接受時比對 _dump 字串、拒絕時比對錯誤訊息清單),
# 每個錯誤訊息分支與寫出位元組另見 crates/project/tests/validate.rs,刻意差異 D-1~D-6 見 crates/project/tests/json_differences.rs
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
