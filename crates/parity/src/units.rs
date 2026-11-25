//! 產品模組的 Rust 比對單元(CHG-20261124-01 代決 6)。
//!
//! 每個單元呼叫 `studio_core` 的函式,把結果轉成比對協定的 [`Outcome`];
//! 協定型別只在本 crate,產品核心不依賴比對協定。Python 端對應單元在 `py/adapter.py`。
//!
//! | 單元名 | 輸入 | `ok` 的值 | `reject` | `error` |
//! |--------|------|-----------|----------|---------|
//! | `value_checks.is_int` | 任意 JSON | `true` / `false` | 無 | 無 |
//! | `names.name_char_ok` | JSON 字串,恰為一個碼位 | `true` / `false` | 無 | `type`:輸入不是恰好一個碼位的字串 |
//! | `names.normalize_name` | 任意 JSON | 原字串 | `invalid`:不合格或非字串 | 無 |
//! | `names.fold_name` | JSON 字串 | 摺疊後字串 | 無 | `type`:非字串輸入 |
//! | `names.constants` | 忽略 | `{"max_name", "ascii", "ranges"}` | 無 | 無 |

use serde_json::{Value, json};
use studio_core::names;

use crate::Outcome;

/// 拒絕代碼:名稱不合格(Python `normalize_name` 回 `None`)。
pub const REJECT_INVALID: &str = "invalid";

/// 錯誤類別:輸入型別不符(Python 端為 `TypeError` / `AttributeError`)。
pub const ERROR_TYPE: &str = "type";

fn type_error() -> Outcome {
    Outcome::Error {
        class: ERROR_TYPE.to_string(),
    }
}

fn reject_invalid() -> Outcome {
    Outcome::Reject {
        code: REJECT_INVALID.to_string(),
    }
}

/// `value_checks.is_int`:回 `ok true` / `ok false`。
pub fn value_checks_is_int(input: &Value) -> Outcome {
    Outcome::Ok(Value::Bool(studio_core::value_checks::is_int(input)))
}

/// `names.name_char_ok`:輸入須為恰好一個碼位的字串,否則 `error type`。
pub fn names_name_char_ok(input: &Value) -> Outcome {
    let Value::String(s) = input else {
        return type_error();
    };
    let mut chars = s.chars();
    match (chars.next(), chars.next()) {
        (Some(ch), None) => Outcome::Ok(Value::Bool(names::name_char_ok(ch))),
        _ => type_error(),
    }
}

/// `names.normalize_name`:合格回 `ok <原字串>`;不合格與非字串輸入回 `reject invalid`。
pub fn names_normalize_name(input: &Value) -> Outcome {
    match input.as_str().and_then(names::normalize_name) {
        Some(name) => Outcome::Ok(Value::String(name.to_string())),
        None => reject_invalid(),
    }
}

/// `names.fold_name`:字串回 `ok <摺疊鍵>`;非字串輸入回 `error type`。
pub fn names_fold_name(input: &Value) -> Outcome {
    match input {
        Value::String(s) => Outcome::Ok(Value::String(names::fold_name(s))),
        _ => type_error(),
    }
}

/// `names.constants`:忽略輸入,回 `MAX_NAME`、`NAME_ASCII`(依碼位排序)與 `NAME_RANGES`(原順序)。
pub fn names_constants(_input: &Value) -> Outcome {
    let ranges: Vec<Value> = names::NAME_RANGES
        .iter()
        .map(|&(lo, hi)| json!([lo, hi]))
        .collect();
    Outcome::Ok(json!({
        "max_name": names::MAX_NAME,
        "ascii": names::NAME_ASCII,
        "ranges": ranges,
    }))
}

#[cfg(test)]
mod tests {
    //! L1:Rust 單元的結果轉換。
    use super::*;

    #[test]
    fn name_char_ok_maps_non_single_char_to_type_error() {
        for input in [
            json!(""),
            json!("ab"),
            json!(5),
            json!(null),
            json!(true),
            json!([1]),
            json!({"a": 1}),
        ] {
            assert_eq!(names_name_char_ok(&input), type_error(), "{input}");
        }
        assert_eq!(names_name_char_ok(&json!("a")), Outcome::Ok(json!(true)));
        assert_eq!(names_name_char_ok(&json!(".")), Outcome::Ok(json!(false)));
    }

    #[test]
    fn normalize_name_maps_none_and_non_string_to_reject_invalid() {
        for input in [json!(""), json!("a.b"), json!(5), json!(null), json!(["a"])] {
            assert_eq!(names_normalize_name(&input), reject_invalid(), "{input}");
        }
        assert_eq!(
            names_normalize_name(&json!("CON")),
            Outcome::Ok(json!("CON"))
        );
    }

    #[test]
    fn fold_name_maps_non_string_to_type_error() {
        assert_eq!(names_fold_name(&json!(1)), type_error());
        assert_eq!(names_fold_name(&json!(null)), type_error());
        assert_eq!(names_fold_name(&json!("ABC")), Outcome::Ok(json!("abc")));
    }

    #[test]
    fn constants_shape() {
        assert_eq!(
            names_constants(&Value::Null),
            Outcome::Ok(json!({
                "max_name": 64,
                "ascii": "-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijklmnopqrstuvwxyz",
                "ranges": [
                    [0x4E00, 0x9FFF],
                    [0x3400, 0x4DBF],
                    [0x3041, 0x3096],
                    [0x30A1, 0x30FA],
                    [0x30FC, 0x30FC],
                    [0x3005, 0x3005]
                ],
            }))
        );
    }
}
