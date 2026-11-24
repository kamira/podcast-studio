//! 產品模組的比對測試(CHG-20261124-01 T1,代決 12)。
//!
//! L2:列舉 `crates/parity/fixtures/` 下 `demo/` 以外的每個 fixture,以函式庫 API 執行 Rust 單元、
//! 以子程序執行真的 Python 轉接器,逐案例比對,斷言全部一致(之後各筆只要加 fixture 就自動納入)。
//! L4:以真的 `parity` 執行檔跑 `value_checks.is_int`,exit 0、最後一行「結論:一致」;
//! `parity list` 列出 `value_checks.is_int`。對應 `features/names.feature`「數值檢查兩邊一致」。
//!
//! 直譯器沿用比對工具的取得順序:環境變數 `PARITY_PYTHON` → 平台預設值。

use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

use parity::py::{self, Adapter, DEFAULT_TIMEOUT_SECS};
use parity::{Fixture, Outcome, Registry, compare};

fn fixtures_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures")
}

fn collect_json(dir: &Path, out: &mut Vec<PathBuf>) {
    let entries =
        std::fs::read_dir(dir).unwrap_or_else(|e| panic!("讀不到目錄 {}:{e}", dir.display()));
    for entry in entries {
        let path = entry.expect("讀取目錄項失敗").path();
        if path.is_dir() {
            collect_json(&path, out);
        } else if path.extension().is_some_and(|ext| ext == "json") {
            out.push(path);
        }
    }
}

/// `fixtures/` 下 `demo/` 以外的全部 fixture(依路徑排序)。
fn module_fixtures() -> Vec<PathBuf> {
    let root = fixtures_root();
    let mut out = Vec::new();
    let entries =
        std::fs::read_dir(&root).unwrap_or_else(|e| panic!("讀不到 {}:{e}", root.display()));
    for entry in entries {
        let path = entry.expect("讀取目錄項失敗").path();
        if path.is_dir() && path.file_name().is_some_and(|n| n != "demo") {
            collect_json(&path, &mut out);
        }
    }
    out.sort();
    out
}

fn relative(path: &Path) -> String {
    path.strip_prefix(fixtures_root())
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/")
}

#[test]
fn module_fixtures_include_value_checks_is_int() {
    let found: Vec<String> = module_fixtures().iter().map(|p| relative(p)).collect();
    assert!(
        found.iter().any(|p| p == "value_checks/is_int.json"),
        "{found:?}"
    );
    assert!(
        found.iter().all(|p| !p.starts_with("demo/")),
        "demo/ 不應納入:{found:?}"
    );
}

#[test]
fn every_module_fixture_is_consistent() {
    let registry = Registry::builtin();
    let adapter = Adapter {
        python: py::resolve_python(None, std::env::var_os("PARITY_PYTHON")),
        script: py::adapter_path(),
        timeout: Duration::from_secs(DEFAULT_TIMEOUT_SECS),
    };
    let fixtures = module_fixtures();
    assert!(!fixtures.is_empty(), "找不到任何產品模組的 fixture");
    let mut failures = Vec::new();
    for path in &fixtures {
        let shown = relative(path);
        let text =
            std::fs::read_to_string(path).unwrap_or_else(|e| panic!("讀不到 fixture {shown}:{e}"));
        let fixture = Fixture::parse(&text).unwrap_or_else(|e| panic!("{e}({shown})"));
        assert!(
            registry.contains(&fixture.unit),
            "Rust 端沒有登錄單元 {:?}({shown})",
            fixture.unit
        );
        let rust: Vec<Outcome> = fixture
            .cases
            .iter()
            .map(|c| {
                registry
                    .run(&fixture.unit, &c.input)
                    .expect("單元已確認登錄")
            })
            .collect();
        let python = py::run(&adapter, &fixture.unit, &fixture.cases)
            .unwrap_or_else(|e| panic!("{shown}:{e}"));
        let mut report = compare(&fixture, &rust, &python);
        report.fixture = shown;
        if !report.is_consistent() {
            failures.push(report.to_text());
        }
    }
    assert!(
        failures.is_empty(),
        "以下 fixture 兩邊結果不一致:\n{}",
        failures.join("\n")
    );
}

fn parity_bin(args: &[&str]) -> Command {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_parity"));
    cmd.args(args);
    cmd.env_remove("PARITY_TEST_SLEEP");
    cmd
}

/// L4:場景「數值檢查兩邊一致」。
#[test]
fn parity_run_value_checks_is_int_exits_0() {
    let fixture = fixtures_root().join("value_checks").join("is_int.json");
    let out = parity_bin(&["run", "--unit", "value_checks.is_int", "--fixture"])
        .arg(&fixture)
        .output()
        .expect("無法執行 parity");
    let stdout = String::from_utf8(out.stdout).expect("輸出須為 UTF-8");
    assert_eq!(
        out.status.code(),
        Some(0),
        "stdout={stdout} stderr={}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert_eq!(stdout.lines().last(), Some("結論:一致"), "{stdout}");
    assert!(stdout.contains("單元:value_checks.is_int"), "{stdout}");
    assert!(stdout.contains("不一致數:0"), "{stdout}");
}

#[test]
fn parity_list_shows_value_checks_is_int() {
    let out = parity_bin(&["list"]).output().expect("無法執行 parity");
    assert_eq!(out.status.code(), Some(0));
    let stdout = String::from_utf8(out.stdout).expect("輸出須為 UTF-8");
    assert!(
        stdout.lines().any(|l| l == "value_checks.is_int"),
        "{stdout}"
    );
}
