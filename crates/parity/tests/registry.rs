//! 契約測試:Rust 單元介面(登錄名 → `fn(&serde_json::Value) -> Outcome`)與示範單元(CHG-20261123-01 T2)。
//!
//! L1:登錄表查詢、panic 攔截為 `Error { class: "panic" }`、示範單元各案例的 Rust 端期望結果。
//! L2:Rust 登錄表的單元名,Python 轉接器都有回應;未知單元轉接器 exit 2。

use std::io::Write;
use std::process::{Command, Stdio};

use parity::py::{adapter_path, resolve_python};
use parity::{Outcome, Registry};
use serde_json::{Value, json};

fn ok(v: Value) -> Outcome {
    Outcome::Ok(v)
}

fn reject(code: &str) -> Outcome {
    Outcome::Reject {
        code: code.to_string(),
    }
}

fn error(class: &str) -> Outcome {
    Outcome::Error {
        class: class.to_string(),
    }
}

fn always_panics(_: &Value) -> Outcome {
    panic!("刻意 panic(測試用)");
}

fn echo(v: &Value) -> Outcome {
    Outcome::Ok(v.clone())
}

#[test]
fn builtin_contains_demo_units() {
    let r = Registry::builtin();
    let names = r.names();
    assert!(names.contains(&"demo.parse_count"), "{names:?}");
    assert!(names.contains(&"demo.parse_count_drift"), "{names:?}");
    assert!(r.contains("demo.parse_count"));
    assert!(!r.contains("demo.nope"));
}

#[test]
fn unknown_unit_returns_none() {
    assert_eq!(Registry::builtin().run("demo.nope", &json!("1")), None);
}

#[test]
fn registered_unit_runs() {
    let mut r = Registry::new();
    r.register("t.echo", echo);
    assert_eq!(r.run("t.echo", &json!([1, 2])), Some(ok(json!([1, 2]))));
    assert_eq!(r.names(), vec!["t.echo"]);
}

#[test]
fn panic_is_recorded_as_error_panic() {
    let mut r = Registry::new();
    r.register("t.panic", always_panics);
    assert_eq!(r.run("t.panic", &json!(null)), Some(error("panic")));
}

/// 示範 fixture 的共同案例:(id, 輸入, parse_count 期望, parse_count_drift 期望)。
fn demo_expectations() -> Vec<(&'static str, Value, Outcome, Outcome)> {
    vec![
        ("plain_3", json!("3"), ok(json!(3)), ok(json!(3))),
        ("edge_1", json!("1"), ok(json!(1)), ok(json!(1))),
        // drift 的 Rust 端上限刻意為 999。
        ("edge_1000", json!("1000"), ok(json!(1000)), reject("out_of_range")),
        ("zero", json!("0"), reject("out_of_range"), reject("out_of_range")),
        ("over_1001", json!("1001"), reject("out_of_range"), reject("out_of_range")),
        ("letters", json!("12a"), reject("not_digits"), reject("not_digits")),
        ("empty", json!(""), reject("not_digits"), reject("not_digits")),
        ("not_string_number", json!(42), error("type"), error("type")),
        ("not_string_null", json!(null), error("type"), error("type")),
        ("leading_zeros", json!("0007"), ok(json!(7)), ok(json!(7))),
        ("fullwidth_digit", json!("\u{ff13}"), reject("not_digits"), reject("not_digits")),
        (
            "huge",
            json!("99999999999999999999"),
            reject("out_of_range"),
            reject("out_of_range"),
        ),
    ]
}

#[test]
fn demo_units_rust_side() {
    let r = Registry::builtin();
    for (id, input, plain, drift) in demo_expectations() {
        assert_eq!(
            r.run("demo.parse_count", &input),
            Some(plain),
            "demo.parse_count 案例 {id}"
        );
        assert_eq!(
            r.run("demo.parse_count_drift", &input),
            Some(drift),
            "demo.parse_count_drift 案例 {id}"
        );
    }
}

#[test]
fn demo_ok_value_is_integer_not_float() {
    // 整數 1 與浮點 1.0 視為不同:Rust 端必須回整數。
    let r = Registry::builtin();
    let out = r.run("demo.parse_count", &json!("1")).unwrap();
    assert_eq!(out, ok(json!(1)));
    assert_ne!(out, ok(json!(1.0)));
}

fn run_adapter(unit: &str, stdin_text: &str) -> std::process::Output {
    let python = resolve_python(None, std::env::var_os("PARITY_PYTHON"));
    let mut child = Command::new(python)
        .arg(adapter_path())
        .args(["--unit", unit])
        .env("PYTHONUTF8", "1")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("無法啟動 Python 轉接器");
    child
        .stdin
        .take()
        .unwrap()
        .write_all(stdin_text.as_bytes())
        .unwrap();
    child.wait_with_output().unwrap()
}

#[test]
fn adapter_answers_every_registered_unit() {
    for name in Registry::builtin().names() {
        let out = run_adapter(name, "{\"id\": \"p\", \"input\": \"5\"}\n");
        assert!(
            out.status.success(),
            "轉接器對 {name} 失敗:{}",
            String::from_utf8_lossy(&out.stderr)
        );
        let line = String::from_utf8(out.stdout).unwrap();
        let resp = parity::parse_response(line.trim_end()).expect("回應須合法");
        assert_eq!(resp.id, "p");
        assert_eq!(resp.outcome, ok(json!(5)), "{name}");
    }
}

#[test]
fn adapter_exits_2_on_unknown_unit() {
    let out = run_adapter("demo.nope", "");
    assert_eq!(out.status.code(), Some(2));
    assert!(out.stdout.is_empty());
    assert!(!out.stderr.is_empty());
}
