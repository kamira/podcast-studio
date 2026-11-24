//! `studio_core::value_checks` 的測試(CHG-20261124-01 T1)。
//!
//! L1:`is_int` 與 Python `value_checks.is_int` 在 `serde_json::Value` 值域內等價(代決 3、5)。
//! 另以 `serde_json::from_str` 解析 JSON 原文,鎖住 serde_json 對 `-0`、超出 `u64` / 低於 `i64`
//! 的整數、`1e2`、`1.0` 的實際解析結果(行為差異清單 D-5、D-6;事實 G 由〔推論〕改為〔觀察〕)。

use serde_json::{Value, json};
use studio_core::value_checks::is_int;

fn parse(text: &str) -> Value {
    serde_json::from_str(text).unwrap_or_else(|e| panic!("無法解析 {text:?}:{e}"))
}

#[test]
fn integers_are_int() {
    for v in [
        json!(0),
        json!(1),
        json!(-3),
        json!(i64::MAX),
        json!(i64::MIN),
        json!(u64::MAX),
    ] {
        assert!(is_int(&v), "{v} 應為整數");
    }
}

#[test]
fn non_integers_are_not_int() {
    for v in [
        json!(true),
        json!(false),
        json!(1.0),
        json!(0.0),
        json!(-0.0),
        parse("1e2"),
        json!("1"),
        json!(null),
        json!([1]),
        json!({"a": 1}),
    ] {
        assert!(!is_int(&v), "{v} 不應為整數");
    }
}

#[test]
fn integer_literals_from_json_text_are_int() {
    // 範圍內的整數字面值經 serde_json 解析後仍為整數,與 Python `json.loads` 相同。
    for text in [
        "0",
        "1",
        "-3",
        "9223372036854775807",
        "-9223372036854775808",
        "18446744073709551615",
    ] {
        assert!(is_int(&parse(text)), "{text} 應為整數");
    }
}

/// D-5:JSON 原文 `-0`。Python `json.loads("-0")` 得 `int` 0(`is_int` 為 `True`);
/// serde_json(未開 `arbitrary_precision`)得 `f64` 的 `-0.0`,`is_int` 為 `false`。
#[test]
fn d5_negative_zero_parses_as_float() {
    let v = parse("-0");
    let f = v.as_f64().expect("-0 應解析為數字");
    assert!(v.is_f64(), "-0 應解析為浮點,實際 {v:?}");
    assert!(!v.is_i64() && !v.is_u64(), "-0 不應為整數,實際 {v:?}");
    assert!(f == 0.0 && f.is_sign_negative(), "-0 應為 -0.0,實際 {f:?}");
    assert!(!is_int(&v), "D-5:Rust 端 is_int(-0) 為 false");
}

/// D-6:JSON 原文中超出 `u64` / 低於 `i64` 範圍的整數。Python 得 `int`(`is_int` 為 `True`);
/// serde_json 得 `f64`,`is_int` 為 `false`。
#[test]
fn d6_out_of_range_integers_parse_as_float() {
    // 兩者最接近的 f64 分別為 2^64 與 -2^63。
    for (text, expected) in [
        ("18446744073709551616", 2f64.powi(64)),
        ("-9223372036854775809", -(2f64.powi(63))),
    ] {
        let v = parse(text);
        assert!(v.is_f64(), "{text} 應解析為浮點,實際 {v:?}");
        assert!(!v.is_i64() && !v.is_u64(), "{text} 不應為整數,實際 {v:?}");
        assert_eq!(v.as_f64(), Some(expected), "{text}");
        assert!(!is_int(&v), "D-6:Rust 端 is_int({text}) 為 false");
    }
}

/// `1e2`、`1.0` 在兩邊都是浮點(Python `json.loads` 給 `float`),`is_int` 兩邊皆為 false。
#[test]
fn float_literals_parse_as_float() {
    for (text, expected) in [("1e2", 100.0_f64), ("1.0", 1.0_f64)] {
        let v = parse(text);
        assert!(v.is_f64(), "{text} 應解析為浮點,實際 {v:?}");
        assert_eq!(v.as_f64(), Some(expected), "{text}");
        assert!(!is_int(&v), "{text}");
    }
}
