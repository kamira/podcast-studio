# language: zh-TW
# CHG-20261125-01 · 對應測試:crates/parity/tests/module_parity.rs、crates/parity/tests/safe_fs_expected.rs、crates/safe-fs/tests/open.rs(T1「開啟類函式兩邊一致」)、crates/safe-fs/tests/windows.rs(T1「Windows 拒絕符號連結與 junction」)、crates/safe-fs/tests/mutate.rs 與 crates/safe-fs/tests/windows.rs 的 junction 案例與 crates/parity/fixtures/safe_fs/rmtree.json(T2「刪除目錄樹不跟隨連結」)
# T1 新增兩個場景;T2、T3 依序補場景(CHG-20261125-01「### Behaviour spec」)
功能: 安全路徑層的 Rust 移植

  場景: 開啟類函式兩邊一致
    假設 safe_fs 的 check_name、open_dir、open_child、open_file、open_path 的 Python 版與 Rust 版
    當 在 macOS 或 Linux 以 parity run 對 crates/parity/fixtures/safe_fs/ 下的開啟類 fixture 逐一執行比對
    那麼 連結、一般檔、不存在、不合法名稱的接受與拒絕及錯誤類別兩邊相同
    而且 根目錄外的 outside 內容沒有任何變動
    而且 每份報告最後一行為「結論:一致」

  場景: Windows 拒絕符號連結與 junction
    假設 Windows 上受信根目錄內有指向外部的目錄符號連結、檔案符號連結與 junction
    當 以 safe-fs 逐層開啟經過它們的路徑
    那麼 每一層都回 Unsafe,與 macOS / Linux 上 Python 拒絕符號連結的結果相同
    而且 連結目標內的檔案沒有被讀取或改動

  場景: 刪除目錄樹不跟隨連結
    假設 要刪除的目錄樹內有指向外部目錄的連結
    當 以 rmtree 刪除該目錄樹
    那麼 目錄樹與連結本身都被刪除
    而且 連結目標的內容完全不變
