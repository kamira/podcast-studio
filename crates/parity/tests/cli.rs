//! 契約測試:`parity` 命令列、文字報告最後一行與退出碼 0 / 1 / 2(CHG-20261123-01 T2)。
//!
//! L3 / L4:以真的 `parity` 執行檔與真的 Python 直譯器跑兩個示範 fixture,
//! 對應 `features/parity.feature` 三個場景;另測直譯器不存在、轉接器逾時、參數錯誤。

use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::time::{Duration, Instant};

use serde_json::Value;

fn demo_fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("fixtures")
        .join("demo")
        .join(name)
}

fn parity(args: &[&str]) -> Command {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_parity"));
    cmd.args(args);
    cmd.env_remove("PARITY_TEST_SLEEP");
    cmd
}

fn run_demo(unit: &str, file: &str, extra: &[&str]) -> Output {
    let fixture = demo_fixture(file);
    let mut cmd = parity(&["run", "--unit", unit, "--fixture"]);
    cmd.arg(&fixture).args(extra);
    cmd.output().expect("無法執行 parity")
}

fn text(bytes: &[u8]) -> String {
    String::from_utf8(bytes.to_vec()).expect("輸出須為 UTF-8")
}

fn last_line(s: &str) -> &str {
    s.lines().last().unwrap_or("")
}

/// 場景:兩邊結果一致。
#[test]
fn consistent_demo_exits_0() {
    let out = run_demo("demo.parse_count", "parse_count.json", &[]);
    let stdout = text(&out.stdout);
    assert_eq!(
        out.status.code(),
        Some(0),
        "stdout={stdout} stderr={}",
        text(&out.stderr)
    );
    assert_eq!(last_line(&stdout), "結論:一致");
    assert!(stdout.contains("單元:demo.parse_count"), "{stdout}");
    assert!(stdout.contains("parse_count.json"), "{stdout}");
    assert!(stdout.contains("案例數:12"), "{stdout}");
    assert!(stdout.contains("一致數:12"), "{stdout}");
    assert!(stdout.contains("不一致數:0"), "{stdout}");
}

/// 場景:兩邊結果不一致。
#[test]
fn drift_demo_exits_1_and_lists_edge_1000() {
    let out = run_demo("demo.parse_count_drift", "parse_count_drift.json", &[]);
    let stdout = text(&out.stdout);
    assert_eq!(
        out.status.code(),
        Some(1),
        "stdout={stdout} stderr={}",
        text(&out.stderr)
    );
    assert_eq!(last_line(&stdout), "結論:不一致");
    assert!(stdout.contains("不一致數:1"), "{stdout}");
    let line = stdout
        .lines()
        .find(|l| l.contains("edge_1000"))
        .unwrap_or_else(|| panic!("報告須列出 edge_1000:{stdout}"));
    // 兩邊結果摘要
    assert!(
        line.contains("Python") && line.contains("ok 1000"),
        "{line}"
    );
    assert!(
        line.contains("Rust") && line.contains("reject out_of_range"),
        "{line}"
    );
}

#[test]
fn json_format_has_same_content() {
    let out = run_demo(
        "demo.parse_count_drift",
        "parse_count_drift.json",
        &["--format", "json"],
    );
    assert_eq!(out.status.code(), Some(1));
    let v: Value = serde_json::from_slice(&out.stdout).expect("--format json 須輸出 JSON");
    assert_eq!(v["unit"], "demo.parse_count_drift");
    assert_eq!(v["cases"], 12);
    assert_eq!(v["matched"], 11);
    assert_eq!(v["mismatched"], 1);
    assert_eq!(v["conclusion"], "不一致");
    assert_eq!(v["mismatches"][0]["id"], "edge_1000");
    assert_eq!(v["mismatches"][0]["python"]["outcome"], "ok");
    assert_eq!(v["mismatches"][0]["rust"]["code"], "out_of_range");
}

fn assert_adapter_failure(out: &Output) {
    let stdout = text(&out.stdout);
    let stderr = text(&out.stderr);
    assert_eq!(
        out.status.code(),
        Some(2),
        "stdout={stdout} stderr={stderr}"
    );
    assert!(!stdout.contains("結論"), "轉接器失敗不得輸出結論:{stdout}");
    assert!(stderr.contains("Python"), "stderr 須說明原因:{stderr}");
}

/// 場景:Python 轉接器無法執行——直譯器不存在。
#[test]
fn missing_interpreter_exits_2() {
    let missing = Path::new(env!("CARGO_TARGET_TMPDIR")).join("沒有這個直譯器");
    let out = run_demo(
        "demo.parse_count",
        "parse_count.json",
        &["--python", missing.to_str().unwrap()],
    );
    assert_adapter_failure(&out);
    assert!(
        text(&out.stderr).contains("無法啟動"),
        "{}",
        text(&out.stderr)
    );
}

#[test]
fn missing_interpreter_from_env_exits_2() {
    let missing = Path::new(env!("CARGO_TARGET_TMPDIR")).join("沒有這個直譯器");
    let mut cmd = parity(&["run", "--unit", "demo.parse_count", "--fixture"]);
    cmd.arg(demo_fixture("parse_count.json"))
        .env("PARITY_PYTHON", &missing);
    let out = cmd.output().unwrap();
    assert_adapter_failure(&out);
}

/// 場景:Python 轉接器無法執行——超過逾時仍未回應。
#[test]
fn adapter_timeout_exits_2() {
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join("cli");
    std::fs::create_dir_all(&dir).unwrap();
    let fixture = dir.join("sleep.json");
    std::fs::write(
        &fixture,
        r#"{"unit": "demo.sleep", "cases": [{"id": "s", "input": 30}]}"#,
    )
    .unwrap();
    let started = Instant::now();
    let out = parity(&["run", "--unit", "demo.sleep", "--timeout", "1", "--fixture"])
        .arg(&fixture)
        .env("PARITY_TEST_SLEEP", "1")
        .output()
        .unwrap();
    assert!(
        started.elapsed() < Duration::from_secs(20),
        "逾時後須終止子程序,實際耗時 {:?}",
        started.elapsed()
    );
    assert_adapter_failure(&out);
    assert!(text(&out.stderr).contains("逾時"), "{}", text(&out.stderr));
}

#[test]
fn sleep_unit_absent_without_env() {
    let out = parity(&["list"]).output().unwrap();
    assert_eq!(out.status.code(), Some(0));
    let stdout = text(&out.stdout);
    assert!(stdout.lines().any(|l| l == "demo.parse_count"), "{stdout}");
    assert!(
        stdout.lines().any(|l| l == "demo.parse_count_drift"),
        "{stdout}"
    );
    assert!(!stdout.contains("demo.sleep"), "{stdout}");
}

#[test]
fn bad_arguments_exit_2() {
    let fixture = demo_fixture("parse_count.json");
    let f = fixture.to_str().unwrap();
    let cases: Vec<Vec<&str>> = vec![
        vec![],
        vec!["nope"],
        vec!["run"],
        vec!["run", "--unit", "demo.parse_count"],
        vec!["run", "--fixture", f],
        vec!["run", "--unit", "demo.nope", "--fixture", f],
        vec![
            "run",
            "--unit",
            "demo.parse_count",
            "--fixture",
            f,
            "--format",
            "xml",
        ],
        vec![
            "run",
            "--unit",
            "demo.parse_count",
            "--fixture",
            f,
            "--timeout",
            "0",
        ],
        vec![
            "run",
            "--unit",
            "demo.parse_count",
            "--fixture",
            f,
            "--timeout",
            "abc",
        ],
        vec![
            "run",
            "--unit",
            "demo.parse_count",
            "--fixture",
            f,
            "--bogus",
        ],
        vec!["run", "--unit"],
        vec!["list", "extra"],
    ];
    for args in cases {
        let out = parity(&args).output().unwrap();
        let stdout = text(&out.stdout);
        let stderr = text(&out.stderr);
        assert_eq!(
            out.status.code(),
            Some(2),
            "{args:?} stdout={stdout} stderr={stderr}"
        );
        assert!(!stdout.contains("結論"), "{args:?}:{stdout}");
        assert!(!stderr.trim().is_empty(), "{args:?} 須在 stderr 說明");
    }
}
