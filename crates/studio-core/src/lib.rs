//! podcast-studio 的 Rust 產品核心。
//!
//! Rust 遷移路線圖(CHG-20261122-02)由第 2 筆起,各 Python 模組的 Rust 版逐一加入本 crate,
//! 並以 `crates/parity` 的並行比對工具確認與 Python 版行為一致。
//!
//! 目前的模組:
//! - [`value_checks`]:跨模組共用的值檢查 `is_int`(CHG-20261124-01 T1)。
//! - [`names`]:使用者命名規則的相容部分(常數、`name_char_ok`、`normalize_name`、`fold_name`;CHG-20261124-01 T2)。
#![forbid(unsafe_code)]

pub mod names;
pub mod value_checks;
