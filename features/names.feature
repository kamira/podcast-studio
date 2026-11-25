# language: zh-TW
# CHG-20261124-01 · 對應測試:crates/parity/tests/module_parity.rs(T1、T2)、crates/parity/tests/names_exhaustive.rs、crates/studio-core/tests/names.rs(T2)、crates/studio-core/tests/names_windows.rs、crates/parity/tests/names_differences.rs(T3)
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

  場景: 新名稱拒絕 Windows 保留名稱(三平台一致)
    假設 使用者在 macOS、Windows 或 Linux 上要建立名為 con、COM1 或 Lpt9 的專案
    當 以 normalize_new_name 檢查名稱
    那麼 結果為 Windows 保留名稱的拒絕
    而且 名稱 CONX、COM10、NULL 不受影響

  場景: 既有的保留名稱仍可讀取
    假設 projects/ 內已有 Python 網頁版建立的專案 con
    當 以 normalize_name 檢查既有名稱
    那麼 名稱合格,與 Python 版結果相同
