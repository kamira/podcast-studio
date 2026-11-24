//! 契約測試:比對 fixture 檔格式 `{"unit", "cases": [{"id", "input"}]}`(CHG-20261123-01 T2,L1)。
//!
//! 函式層斷言 `Fixture::parse` 的接受與拒絕;命令列層斷言格式錯誤時 `parity run` exit 2、
//! stderr 有繁中原因、stdout 不輸出結論。暫存 fixture 一律寫在 `CARGO_TARGET_TMPDIR`(建置目錄內)。

use std::path::{Path, PathBuf};
use std::process::Command;

use parity::{Fixture, FixtureError};
use serde_json::json;

fn demo_fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("fixtures")
        .join("demo")
        .join(name)
}

fn tmp_fixture(name: &str, text: &str) -> PathBuf {
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join("fixture_format");
    std::fs::create_dir_all(&dir).expect("無法建立暫存目錄");
    let path = dir.join(name);
    std::fs::write(&path, text).expect("無法寫入暫存 fixture");
    path
}

#[test]
fn parses_valid_fixture() {
    let f = Fixture::parse(r#"{"unit": "demo.parse_count", "cases": [{"id": "a", "input": "3"}, {"id": "b", "input": null}]}"#)
        .expect("合法 fixture 應可解析");
    assert_eq!(f.unit, "demo.parse_count");
    assert_eq!(f.cases.len(), 2);
    assert_eq!(f.cases[0].id, "a");
    assert_eq!(f.cases[0].input, json!("3"));
    assert_eq!(f.cases[1].input, json!(null));
}

#[test]
fn accepts_crlf_and_extra_fields() {
    // 協定擴充只以新增選用欄位進行:多出的欄位不影響解析;Windows 換行亦可。
    let text = "{\r\n\"unit\": \"u\",\r\n\"note\": \"x\",\r\n\"cases\": [{\"id\": \"a\", \"input\": 1, \"memo\": 2}]\r\n}\r\n";
    let f = Fixture::parse(text).expect("CRLF 與多餘欄位應可解析");
    assert_eq!(f.cases[0].input, json!(1));
}

#[test]
fn rejects_missing_unit() {
    let err = Fixture::parse(r#"{"cases": []}"#).unwrap_err();
    assert!(matches!(err, FixtureError::Format(_)), "{err:?}");
    assert!(err.to_string().contains("unit"), "{err}");
}

#[test]
fn rejects_missing_cases() {
    let err = Fixture::parse(r#"{"unit": "u"}"#).unwrap_err();
    assert!(matches!(err, FixtureError::Format(_)), "{err:?}");
    assert!(err.to_string().contains("cases"), "{err}");
}

#[test]
fn rejects_case_without_input() {
    // `input` 可為任意 JSON(含 null),但欄位本身必須存在。
    let err = Fixture::parse(r#"{"unit": "u", "cases": [{"id": "a"}]}"#).unwrap_err();
    assert!(matches!(err, FixtureError::Format(_)), "{err:?}");
}

#[test]
fn rejects_duplicate_case_id() {
    let err = Fixture::parse(
        r#"{"unit": "u", "cases": [{"id": "a", "input": 1}, {"id": "a", "input": 2}]}"#,
    )
    .unwrap_err();
    assert_eq!(err, FixtureError::DuplicateId("a".to_string()));
    assert!(err.to_string().contains("重複"), "{err}");
}

#[test]
fn rejects_non_json() {
    let err = Fixture::parse("這不是 JSON").unwrap_err();
    assert!(matches!(err, FixtureError::Format(_)), "{err:?}");
}

#[test]
fn demo_fixtures_have_required_cases() {
    let required = [
        "plain_3",
        "edge_1",
        "edge_1000",
        "zero",
        "over_1001",
        "letters",
        "empty",
        "not_string_number",
        "not_string_null",
    ];
    for (file, unit) in [
        ("parse_count.json", "demo.parse_count"),
        ("parse_count_drift.json", "demo.parse_count_drift"),
    ] {
        let text = std::fs::read_to_string(demo_fixture(file)).expect("讀不到示範 fixture");
        let f = Fixture::parse(&text).expect("示範 fixture 應可解析");
        assert_eq!(f.unit, unit);
        let ids: Vec<&str> = f.cases.iter().map(|c| c.id.as_str()).collect();
        for id in required {
            assert!(ids.contains(&id), "{file} 缺少案例 {id}");
        }
    }
}

fn run_parity(unit: &str, fixture: &Path) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_parity"))
        .args(["run", "--unit", unit, "--fixture"])
        .arg(fixture)
        .output()
        .expect("無法執行 parity")
}

fn assert_exit2_without_conclusion(out: &std::process::Output) {
    let stdout = String::from_utf8_lossy(&out.stdout);
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert_eq!(
        out.status.code(),
        Some(2),
        "stdout={stdout} stderr={stderr}"
    );
    assert!(!stdout.contains("結論"), "格式錯誤不得輸出結論:{stdout}");
    assert!(
        stderr.contains("fixture"),
        "stderr 應說明 fixture 錯誤:{stderr}"
    );
}

#[test]
fn cli_exit2_on_bad_fixtures() {
    let cases = [
        (
            "missing_unit.json",
            r#"{"cases": [{"id": "a", "input": "1"}]}"#,
        ),
        ("missing_cases.json", r#"{"unit": "demo.parse_count"}"#),
        (
            "dup_id.json",
            r#"{"unit": "demo.parse_count", "cases": [{"id": "a", "input": "1"}, {"id": "a", "input": "2"}]}"#,
        ),
        ("not_json.json", "not json"),
    ];
    for (name, text) in cases {
        let path = tmp_fixture(name, text);
        let out = run_parity("demo.parse_count", &path);
        assert_exit2_without_conclusion(&out);
    }
}

#[test]
fn cli_exit2_when_fixture_unit_differs() {
    let path = tmp_fixture(
        "other_unit.json",
        r#"{"unit": "demo.parse_count_drift", "cases": [{"id": "a", "input": "1"}]}"#,
    );
    let out = run_parity("demo.parse_count", &path);
    assert_exit2_without_conclusion(&out);
}

#[test]
fn cli_exit2_when_fixture_missing() {
    let path = Path::new(env!("CARGO_TARGET_TMPDIR")).join("沒有這個檔.json");
    let out = run_parity("demo.parse_count", &path);
    assert_exit2_without_conclusion(&out);
}
