# language: zh-TW
# CHG-20261124-01 · 對應測試:crates/parity/tests/module_parity.rs(T1)
# T1 新增第一個場景;T2、T3 依序補場景(CHG-20261124-01「### Behaviour spec」)
功能: 名稱規則與數值檢查的 Rust 移植

  場景: 數值檢查兩邊一致
    假設 value_checks.is_int 的 Python 版與 Rust 版
    當 以 parity run 對 crates/parity/fixtures/value_checks/is_int.json 執行比對
    那麼 整數回 true,布林、浮點、字串、null、陣列與物件回 false
    而且 最後一行為「結論:一致」,退出碼為 0
