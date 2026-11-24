//! 比對工具的示範單元(CHG-20261123-01 代決 7)。
//!
//! 只存在於比對工具內、不屬任何產品模組,保留作比對工具的自我測試;不得被產品程式引用。
//! Python 端的同名單元在 `py/adapter.py`,兩邊的規則與拒絕代碼必須逐字相同:
//!
//! - 輸入不是 JSON 字串 → `error` `type`
//! - 字串為空或含 ASCII `0`–`9` 以外的字元 → `reject` `not_digits`
//! - 轉成整數後為 0 或大於上限 → `reject` `out_of_range`,否則 `ok`(整數)
//!
//! `demo.parse_count` 上限 1000(兩邊相同,示範「結論:一致」);
//! `demo.parse_count_drift` 的 Rust 端上限刻意為 999(Python 端 1000),示範「結論:不一致」。

use serde_json::{Value, json};

use crate::Outcome;

fn reject(code: &str) -> Outcome {
    Outcome::Reject {
        code: code.to_string(),
    }
}

fn parse_with_max(input: &Value, max: u64) -> Outcome {
    let Value::String(s) = input else {
        return Outcome::Error {
            class: "type".to_string(),
        };
    };
    if s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit()) {
        return reject("not_digits");
    }
    // 去掉前導 0 後超過 4 位數必定大於上限;如此任意長度的數字字串都不會溢位。
    let digits = s.trim_start_matches('0');
    if digits.len() > 4 {
        return reject("out_of_range");
    }
    let n: u64 = if digits.is_empty() {
        0
    } else {
        digits.parse().expect("至多 4 位 ASCII 數字必可轉成整數")
    };
    if n == 0 || n > max {
        reject("out_of_range")
    } else {
        Outcome::Ok(json!(n))
    }
}

/// `demo.parse_count`:上限 1000。
pub fn parse_count(input: &Value) -> Outcome {
    parse_with_max(input, 1000)
}

/// `demo.parse_count_drift`:Rust 端上限刻意為 999(Python 端 1000)。
pub fn parse_count_drift(input: &Value) -> Outcome {
    parse_with_max(input, 999)
}

/// `demo.sleep`:測試專用,只在環境變數 `PARITY_TEST_SLEEP=1` 時登錄。
/// Rust 端不等待、直接回 `ok null`;Python 端睡 `input` 秒,用來測轉接器逾時。
pub fn sleep(_input: &Value) -> Outcome {
    Outcome::Ok(Value::Null)
}
