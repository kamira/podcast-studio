//! 契約測試:JSON 行協定(CHG-20261123-01 T2)。
//!
//! L1:Rust 側解析——三種回應、`\r\n` 行尾、未知 `outcome`、缺 `id`、id 順序不符、訊息文字不比對。
//! L2:提供方與讀取方雙側——以子程序啟動真的 `py/adapter.py`,它輸出的每一行都要被 Rust 解析器接受。

use std::io::Write;
use std::process::{Command, Stdio};

use parity::py::{adapter_path, resolve_python};
use parity::{
    Case, Fixture, Outcome, ProtocolError, encode_request, match_responses, parse_response,
};
use serde_json::{Value, json};

fn case(id: &str, input: Value) -> Case {
    Case {
        id: id.to_string(),
        input,
    }
}

#[test]
fn parses_three_outcomes() {
    let ok = parse_response(r#"{"id": "a", "outcome": "ok", "value": 3}"#).unwrap();
    assert_eq!(ok.id, "a");
    assert_eq!(ok.outcome, Outcome::Ok(json!(3)));

    let reject =
        parse_response(r#"{"id": "b", "outcome": "reject", "code": "not_digits"}"#).unwrap();
    assert_eq!(
        reject.outcome,
        Outcome::Reject {
            code: "not_digits".to_string()
        }
    );

    let error = parse_response(r#"{"id": "c", "outcome": "error", "class": "type"}"#).unwrap();
    assert_eq!(
        error.outcome,
        Outcome::Error {
            class: "type".to_string()
        }
    );
}

#[test]
fn ok_value_may_be_null() {
    let r = parse_response(r#"{"id": "a", "outcome": "ok", "value": null}"#).unwrap();
    assert_eq!(r.outcome, Outcome::Ok(Value::Null));
}

#[test]
fn accepts_crlf_line_ending() {
    let r = parse_response("{\"id\": \"a\", \"outcome\": \"ok\", \"value\": 1}\r\n").unwrap();
    assert_eq!(r.outcome, Outcome::Ok(json!(1)));
    let r = parse_response("{\"id\": \"a\", \"outcome\": \"ok\", \"value\": 1}\r").unwrap();
    assert_eq!(r.id, "a");
}

#[test]
fn message_text_is_not_compared() {
    // 人看的訊息文字不比對:只差 message 的兩行解析成相同結果。
    let a = parse_response(r#"{"id": "a", "outcome": "reject", "code": "x", "message": "甲"}"#)
        .unwrap();
    let b = parse_response(r#"{"id": "a", "outcome": "reject", "code": "x", "message": "乙"}"#)
        .unwrap();
    assert_eq!(a, b);
}

#[test]
fn rejects_unknown_outcome() {
    let err = parse_response(r#"{"id": "a", "outcome": "maybe"}"#).unwrap_err();
    assert!(matches!(err, ProtocolError::BadLine(_)), "{err:?}");
}

#[test]
fn rejects_missing_id() {
    let err = parse_response(r#"{"outcome": "ok", "value": 1}"#).unwrap_err();
    assert!(matches!(err, ProtocolError::BadLine(_)), "{err:?}");
}

#[test]
fn rejects_missing_payload_fields() {
    for line in [
        r#"{"id": "a", "outcome": "ok"}"#,
        r#"{"id": "a", "outcome": "reject"}"#,
        r#"{"id": "a", "outcome": "error", "class": 3}"#,
        "不是 JSON",
        "[]",
    ] {
        assert!(parse_response(line).is_err(), "應拒絕:{line}");
    }
}

#[test]
fn encodes_request_as_single_line() {
    let line = encode_request(&case("x", json!({"a": "多\n行"})));
    assert!(!line.contains('\n'), "請求必須是單行:{line}");
    let v: Value = serde_json::from_str(&line).unwrap();
    assert_eq!(v, json!({"id": "x", "input": {"a": "多\n行"}}));
}

#[test]
fn match_responses_in_order() {
    let cases = vec![case("a", json!(1)), case("b", json!(2))];
    let responses = vec![
        parse_response(r#"{"id": "a", "outcome": "ok", "value": 1}"#).unwrap(),
        parse_response(r#"{"id": "b", "outcome": "error", "class": "type"}"#).unwrap(),
    ];
    let outcomes = match_responses(&cases, responses).unwrap();
    assert_eq!(outcomes.len(), 2);
    assert_eq!(outcomes[0], Outcome::Ok(json!(1)));
}

#[test]
fn match_responses_rejects_wrong_order() {
    let cases = vec![case("a", json!(1)), case("b", json!(2))];
    let responses = vec![
        parse_response(r#"{"id": "b", "outcome": "ok", "value": 1}"#).unwrap(),
        parse_response(r#"{"id": "a", "outcome": "ok", "value": 1}"#).unwrap(),
    ];
    let err = match_responses(&cases, responses).unwrap_err();
    assert!(matches!(err, ProtocolError::IdMismatch { .. }), "{err:?}");
}

#[test]
fn match_responses_rejects_wrong_count() {
    let cases = vec![case("a", json!(1)), case("b", json!(2))];
    let responses = vec![parse_response(r#"{"id": "a", "outcome": "ok", "value": 1}"#).unwrap()];
    let err = match_responses(&cases, responses).unwrap_err();
    assert!(
        matches!(err, ProtocolError::CountMismatch { .. }),
        "{err:?}"
    );
}

/// L2:真的 Python 轉接器輸出的每一行,Rust 解析器都接受,且 id 依序對應。
#[test]
fn real_adapter_output_is_accepted() {
    let python = resolve_python(None, std::env::var_os("PARITY_PYTHON"));
    for unit in ["demo.parse_count", "demo.parse_count_drift"] {
        let file = format!(
            "{}/fixtures/demo/{}.json",
            env!("CARGO_MANIFEST_DIR"),
            unit.trim_start_matches("demo.")
        );
        let fixture = Fixture::parse(&std::fs::read_to_string(&file).unwrap()).unwrap();
        let mut child = Command::new(&python)
            .arg(adapter_path())
            .args(["--unit", unit])
            .env("PYTHONUTF8", "1")
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("無法啟動 Python 轉接器");
        {
            let mut stdin = child.stdin.take().unwrap();
            for c in &fixture.cases {
                writeln!(stdin, "{}", encode_request(c)).unwrap();
            }
        }
        let out = child.wait_with_output().unwrap();
        assert!(
            out.status.success(),
            "轉接器失敗:{}",
            String::from_utf8_lossy(&out.stderr)
        );
        let text = String::from_utf8(out.stdout).expect("轉接器輸出須為 UTF-8");
        assert!(!text.contains('\r'), "轉接器換行須為 \\n");
        let responses: Vec<_> = text
            .lines()
            .map(|l| parse_response(l).unwrap_or_else(|e| panic!("解析失敗:{l}:{e}")))
            .collect();
        let outcomes = match_responses(&fixture.cases, responses).expect("id 須依序對應");
        assert_eq!(outcomes.len(), fixture.cases.len());
    }
}
