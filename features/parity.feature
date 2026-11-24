# language: zh-TW
# CHG-20261123-01 T2 · 對應測試:crates/parity/tests/cli.rs
功能: Rust 遷移的並行比對工具

  場景: 兩邊結果一致
    假設 示範單元 demo.parse_count 在 Python 轉接器與 Rust 單元的規則相同
    當 以 parity run 對 crates/parity/fixtures/demo/parse_count.json 執行比對
    那麼 報告列出案例數、一致數與不一致數 0
    而且 最後一行為「結論:一致」,退出碼為 0

  場景: 兩邊結果不一致
    假設 示範單元 demo.parse_count_drift 的 Rust 端上限刻意比 Python 端少 1
    當 以 parity run 對 crates/parity/fixtures/demo/parse_count_drift.json 執行比對
    那麼 報告列出不一致案例 edge_1000 與兩邊結果摘要
    而且 最後一行為「結論:不一致」,退出碼為 1

  場景: Python 轉接器無法執行
    假設 --python 指向不存在的直譯器,或轉接器超過逾時仍未回應
    當 以 parity run 執行比對
    那麼 stderr 印出繁體中文錯誤原因,不輸出結論
    而且 退出碼為 2
