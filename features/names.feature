# language: zh-TW
# CHG-20261124-01 · 對應測試:crates/parity/tests/module_parity.rs(T1、T2)、crates/parity/tests/names_exhaustive.rs、crates/studio-core/tests/names.rs(T2)
# T1 新增第一個場景;T2、T3 依序補場景(CHG-20261124-01「### Behaviour spec」)
功能: 名稱規則與數值檢查的 Rust 移植

  場景: 數值檢查兩邊一致
    假設 value_checks.is_int 的 Python 版與 Rust 版
    當 以 parity run 對 crates/parity/fixtures/value_checks/is_int.json 執行比對
    那麼 整數回 true,布林、浮點、字串、null、陣列與物件回 false
    而且 最後一行為「結論:一致」,退出碼為 0

  場景: 名稱規則兩邊一致
    假設 names 的常數、name_char_ok、normalize_name 的 Python 版與 Rust 版
    當 以 parity run 對 crates/parity/fixtures/names/ 下的 fixture 逐一執行比對
    那麼 白名單邊界、全形字、組合記號、零寬字元、空字串、64 與 65 字、非字串輸入的接受與拒絕兩邊相同
    而且 每份報告最後一行為「結論:一致」

  場景: 只差大小寫的比對鍵與 Python 相同
    假設 目錄中有名稱 straße,使用者要使用名稱 STRASSE
    當 兩邊各以 fold_name 取比對鍵
    那麼 兩個比對鍵都是 strasse,兩邊都判定為只差大小寫的衝突
