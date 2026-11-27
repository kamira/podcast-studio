# language: zh-TW
# CHG-20261127-01 · 對應測試:crates/heavy-lock/tests/cross_process.rs(T1「Rust 行程之間互斥」:parent_times_out_while_child_holds、
# parent_waits_until_child_releases、child_times_out_while_parent_holds;「持鎖行程被終止後由作業系統釋放」:
# lock_released_after_holder_is_killed),同行程語意見 crates/heavy-lock/tests/lock.rs
# T1 新增兩個場景;T2 補「Python 持鎖時 Rust 等待」「Rust 持鎖時 Python 等待」(CHG-20261127-01「### Behaviour spec」)
功能: 重型工作鎖的 Rust 移植

  場景: Rust 行程之間互斥
    假設 一個 Rust 行程以 heavy-lock 持有 outputs/.heavy.lock
    當 另一個 Rust 行程以 0.3 秒逾時取鎖
    那麼 取鎖回報逾時
    而且 持鎖行程釋放後,等待中的行程才取得鎖,等待通知只呼叫一次

  場景: 持鎖行程被終止後由作業系統釋放
    假設 一個持有重型鎖的 Rust 行程被終止
    那麼 另一個行程在 macOS 與 Linux 上 2 秒內、在 Windows 上 10 秒內取得重型鎖
    而且 鎖檔仍在且沒有內容
