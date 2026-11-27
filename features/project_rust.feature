# language: zh-TW
# CHG-20261128-01 · 專案資料模型(project.py)的 Rust 移植;Python 版的既有 feature(timeline_editor 等)不改
# T1 · 對應測試:crates/parity/tests/project_parity.rs(「數值四捨五入與錯誤訊息與 Python 相同」:
# rust_matches_expected_on_this_platform、python_matches_expected_on_macos_and_linux、parity_run_project_fixtures_exit_0;
# fixture 為 crates/parity/fixtures/project/normalize_speed.json、normalize_pause.json 等六份),
# 四捨五入邊界與錯誤訊息另見 crates/project/tests/checks.rs
# T1 新增第一個場景;T2~T7 各補場景(CHG-20261128-01「### Behaviour spec」)
功能: 專案資料模型的 Rust 移植

  場景: 數值四捨五入與錯誤訊息與 Python 相同
    假設 同一組語速與停頓輸入分別交給 Python 的 project 與 Rust 的 project
    那麼 四捨五入後的值與錯誤訊息逐字相同
