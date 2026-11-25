//! 產品模組的比對測試(CHG-20261124-01 T1,代決 12)。
//!
//! L2:列舉 `crates/parity/fixtures/` 下 `demo/` 以外的每個 fixture,以函式庫 API 執行 Rust 單元、
//! 以子程序執行真的 Python 轉接器,逐案例比對,斷言全部一致(之後各筆只要加 fixture 就自動納入)。
//! L4:以真的 `parity` 執行檔跑 `value_checks.is_int` 與四個 `names.*` fixture,exit 0、
//! 最後一行「結論:一致」;`parity list` 列出這些單元。對應 `features/names.feature`
//! 「數值檢查兩邊一致」「名稱規則兩邊一致」「只差大小寫的比對鍵與 Python 相同」(T2)。
//! 另斷言 fixture 不含單獨代理碼位與超出 `u64` / 低於 `i64` 的整數字面值(CHG-20261124-01 T2 test (4))。
//!
//! `safe_fs.*`(CHG-20261125-01 T1,代決 10):macOS / Linux 上 `fixtures/safe_fs/` 照常以真轉接器比對,
//! L4 以真執行檔跑 14 個 `safe_fs.*` fixture(T1 五個開啟類、T2 九個);**Windows 上略過
//! `fixtures/safe_fs/`**(Python `safe_fs` 在 Windows 無法執行,事實 D)並印出原因,其他 fixture
//! 照舊比對——Windows 的對照改由 `safe_fs_expected.rs` 進行。
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

/// Windows 上略過的 fixture 目錄(Python 端無法在 Windows 執行)。
const SKIP_ON_WINDOWS: &str = "safe_fs";

/// `fixtures/` 下 `demo/` 以外的全部 fixture(依路徑排序);Windows 上另略過 `safe_fs/` 並印出原因。
fn module_fixtures() -> Vec<PathBuf> {
    let root = fixtures_root();
    let mut out = Vec::new();
    let entries =
        std::fs::read_dir(&root).unwrap_or_else(|e| panic!("讀不到 {}:{e}", root.display()));
    for entry in entries {
        let path = entry.expect("讀取目錄項失敗").path();
        if !path.is_dir() || path.file_name().is_none_or(|n| n == "demo") {
            continue;
        }
        if cfg!(windows) && path.file_name().is_some_and(|n| n == SKIP_ON_WINDOWS) {
            println!(
                "略過 fixtures/{SKIP_ON_WINDOWS}/:Python safe_fs 在 Windows 無法執行(CHG-20261125-01 代決 10),改由 safe_fs_expected.rs 對照"
            );
            continue;
        }
        collect_json(&path, &mut out);
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
    for name in [
        "names/constants.json",
        "names/fold_name.json",
        "names/name_char_ok.json",
        "names/normalize_name.json",
    ] {
        assert!(found.iter().any(|p| p == name), "缺少 {name}:{found:?}");
    }
    assert!(
        found.iter().all(|p| !p.starts_with("demo/")),
        "demo/ 不應納入:{found:?}"
    );
    for name in SAFE_FS_FIXTURES.map(|(_, f)| format!("safe_fs/{f}")) {
        assert_eq!(
            found.contains(&name),
            !cfg!(windows),
            "{name}(Windows 上略過、其他平台納入):{found:?}"
        );
    }
}

/// `safe_fs.*` 的 14 個單元與 fixture(T1 五個開啟類、T2 九個建立 / 查詢 / 刪除 / 搬移類)。
const SAFE_FS_FIXTURES: [(&str, &str); 14] = [
    ("safe_fs.check_name", "check_name.json"),
    ("safe_fs.open_dir", "open_dir.json"),
    ("safe_fs.open_child", "open_child.json"),
    ("safe_fs.open_file", "open_file.json"),
    ("safe_fs.open_path", "open_path.json"),
    ("safe_fs.create_excl", "create_excl.json"),
    ("safe_fs.open_or_create", "open_or_create.json"),
    ("safe_fs.lstat", "lstat.json"),
    ("safe_fs.entries", "entries.json"),
    ("safe_fs.unlink", "unlink.json"),
    ("safe_fs.rmtree", "rmtree.json"),
    ("safe_fs.rename", "rename.json"),
    ("safe_fs.replace", "replace.json"),
    ("safe_fs.link", "link.json"),
];

/// L4:場景「開啟類函式兩邊一致」(T1)與「刪除目錄樹不跟隨連結」(T2)——以真執行檔與真轉接器跑
/// 14 個 `safe_fs.*` fixture(macOS / Linux)。
#[test]
fn parity_run_safe_fs_fixtures_exit_0() {
    if cfg!(windows) {
        println!("略過:Python safe_fs 在 Windows 無法執行(CHG-20261125-01 代決 10)");
        return;
    }
    for (unit, file) in SAFE_FS_FIXTURES {
        let fixture = fixtures_root().join("safe_fs").join(file);
        let out = parity_bin(&["run", "--unit", unit, "--fixture"])
            .arg(&fixture)
            .output()
            .expect("無法執行 parity");
        let stdout = String::from_utf8(out.stdout).expect("輸出須為 UTF-8");
        assert_eq!(
            out.status.code(),
            Some(0),
            "{unit}:stdout={stdout} stderr={}",
            String::from_utf8_lossy(&out.stderr)
        );
        assert_eq!(stdout.lines().last(), Some("結論:一致"), "{unit}:{stdout}");
        assert!(stdout.contains("不一致數:0"), "{stdout}");
    }
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
fn parity_list_shows_module_units() {
    let out = parity_bin(&["list"]).output().expect("無法執行 parity");
    assert_eq!(out.status.code(), Some(0));
    let stdout = String::from_utf8(out.stdout).expect("輸出須為 UTF-8");
    for unit in [
        "value_checks.is_int",
        "names.name_char_ok",
        "names.normalize_name",
        "names.fold_name",
        "names.constants",
        "safe_fs.check_name",
        "safe_fs.open_dir",
        "safe_fs.open_child",
        "safe_fs.open_file",
        "safe_fs.open_path",
        "safe_fs.create_excl",
        "safe_fs.open_or_create",
        "safe_fs.lstat",
        "safe_fs.entries",
        "safe_fs.unlink",
        "safe_fs.rmtree",
        "safe_fs.rename",
        "safe_fs.replace",
        "safe_fs.link",
    ] {
        assert!(stdout.lines().any(|l| l == unit), "缺少 {unit}:{stdout}");
    }
}

/// L4:場景「名稱規則兩邊一致」「只差大小寫的比對鍵與 Python 相同」——四個 `names.*` fixture。
#[test]
fn parity_run_names_fixtures_exit_0() {
    for (unit, file) in [
        ("names.constants", "constants.json"),
        ("names.name_char_ok", "name_char_ok.json"),
        ("names.normalize_name", "normalize_name.json"),
        ("names.fold_name", "fold_name.json"),
    ] {
        let fixture = fixtures_root().join("names").join(file);
        let out = parity_bin(&["run", "--unit", unit, "--fixture"])
            .arg(&fixture)
            .output()
            .expect("無法執行 parity");
        let stdout = String::from_utf8(out.stdout).expect("輸出須為 UTF-8");
        assert_eq!(
            out.status.code(),
            Some(0),
            "{unit}:stdout={stdout} stderr={}",
            String::from_utf8_lossy(&out.stderr)
        );
        assert_eq!(stdout.lines().last(), Some("結論:一致"), "{unit}:{stdout}");
        assert!(stdout.contains(&format!("單元:{unit}")), "{stdout}");
        assert!(stdout.contains("不一致數:0"), "{stdout}");
    }
}

/// 場景「只差大小寫的比對鍵與 Python 相同」:straße 與 STRASSE 兩邊的比對鍵都是 strasse。
#[test]
fn fold_name_case_conflict_key_is_the_same_on_both_sides() {
    let registry = Registry::builtin();
    let cases = vec![
        parity::Case {
            id: "existing".to_string(),
            input: serde_json::json!("stra\u{00DF}e"),
        },
        parity::Case {
            id: "requested".to_string(),
            input: serde_json::json!("STRASSE"),
        },
    ];
    let adapter = Adapter {
        python: py::resolve_python(None, std::env::var_os("PARITY_PYTHON")),
        script: py::adapter_path(),
        timeout: Duration::from_secs(DEFAULT_TIMEOUT_SECS),
    };
    let python = py::run(&adapter, "names.fold_name", &cases).expect("轉接器失敗");
    let want = Outcome::Ok(serde_json::json!("strasse"));
    for (case, py_out) in cases.iter().zip(&python) {
        assert_eq!(py_out, &want, "Python {}", case.id);
        assert_eq!(
            registry.run("names.fold_name", &case.input).as_ref(),
            Some(&want),
            "Rust {}",
            case.id
        );
    }
}

/// 掃描 JSON 原文,回傳字串以外的整數字面值(不含小數點與指數者)。
fn integer_literals(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut chars = text.chars().peekable();
    let mut in_string = false;
    while let Some(c) = chars.next() {
        if in_string {
            match c {
                '\\' => {
                    chars.next();
                }
                '"' => in_string = false,
                _ => {}
            }
        } else if c == '"' {
            in_string = true;
        } else if c == '-' || c.is_ascii_digit() {
            let mut token = c.to_string();
            while let Some(&n) = chars.peek() {
                if n.is_ascii_digit() || matches!(n, '.' | 'e' | 'E' | '+' | '-') {
                    token.push(n);
                    chars.next();
                } else {
                    break;
                }
            }
            if !token.contains(['.', 'e', 'E']) {
                out.push(token);
            }
        }
    }
    out
}

#[test]
fn integer_literal_scanner_finds_integers_outside_strings() {
    let found =
        integer_literals(r#"{"a": "-0 99 \" 7", "b": [18446744073709551616, -3, 1.5, 2e3]}"#);
    assert_eq!(found, ["18446744073709551616", "-3"]);
}

#[test]
fn serde_json_rejects_lone_surrogates() {
    // fixture 能被 serde_json 解析,即代表其中沒有單獨代理碼位。
    assert!(serde_json::from_str::<serde_json::Value>(r#""\ud800""#).is_err());
    assert!(serde_json::from_str::<serde_json::Value>(r#""\udc00x""#).is_err());
    assert!(serde_json::from_str::<serde_json::Value>(r#""\ud83d\ude00""#).is_ok());
}

/// CHG-20261124-01 T2 test (4):fixture 沒有單獨代理碼位、沒有超出 `u64` / 低於 `i64` 的整數。
#[test]
fn module_fixtures_have_no_lone_surrogates_or_out_of_range_integers() {
    let fixtures = module_fixtures();
    assert!(!fixtures.is_empty());
    for path in fixtures {
        let shown = relative(&path);
        let text =
            std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("讀不到 fixture {shown}:{e}"));
        serde_json::from_str::<serde_json::Value>(&text)
            .unwrap_or_else(|e| panic!("{shown} 無法解析(可能含單獨代理碼位):{e}"));
        for token in integer_literals(&text) {
            assert!(
                token.parse::<i64>().is_ok() || token.parse::<u64>().is_ok(),
                "{shown} 含超出 u64 / 低於 i64 的整數字面值 {token}"
            );
        }
    }
}
