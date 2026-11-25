//! 產品模組的 Rust 比對單元(CHG-20261124-01 代決 6)。
//!
//! 每個單元呼叫 `studio_core` 的函式,把結果轉成比對協定的 [`Outcome`];
//! 協定型別只在本 crate,產品核心不依賴比對協定。Python 端對應單元在 `py/adapter.py`。
//!
//! | 單元名 | 輸入 | `ok` 的值 | `reject` | `error` |
//! |--------|------|-----------|----------|---------|
//! | `value_checks.is_int` | 任意 JSON | `true` / `false` | 無 | 無 |

use serde_json::Value;

use crate::Outcome;

/// `value_checks.is_int`:回 `ok true` / `ok false`。
pub fn value_checks_is_int(input: &Value) -> Outcome {
    Outcome::Ok(Value::Bool(studio_core::value_checks::is_int(input)))
}
