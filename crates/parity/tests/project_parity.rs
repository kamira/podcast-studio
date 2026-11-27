//! `project.*` 比對單元與以 Python 為錨的預期結果表(CHG-20261128-01 代決 10;T1 起)。
//!
//! 表在 `tests/data/project_expected.json`:`{單元: {案例 id: {"all": <結果>, "windows"?}}}`,
//! `<結果>` 為協定形式的 `{"outcome", "value" | "code" | "class"}`。
//!
//! - macOS / Linux:**Python 實際結果等於表**(表以 Python 為錨,由真轉接器產生,不能憑空寫);
//! - 三平台:**Rust 實際結果等於表中該平台的預期**——`project.py` 頂層 import `fcntl`,Python 端在 Windows
//!   無法執行(事實 H),Windows 的 Rust 行為因此經由表間接等於 macOS / Linux 上的 Python 行為;
//! - 每個 fixture 案例都有預期;`windows` 欄只出現在「### 行為差異清單」列出的案例(T1、T2 為 0 筆,以常數鎖住);
//! - 每份 fixture 的案例數以常數寫明,且不低於該 task L2 的下限;`project.validate_text`(T2)另固定接受 / 拒絕筆數;
//! - L2(macOS / Linux):以真 `parity` 執行檔跑每份 `fixtures/project/*.json`,exit 0、最後一行「結論:一致」。
//!   對應 `features/project_rust.feature`「數值四捨五入與錯誤訊息與 Python 相同」(T1)、
//!   「專案檔驗證結果與錯誤訊息與 Python 相同」「Rust 寫出的位元組與 Python 相同」(T2,`validate_text.json`)。

use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;

use parity::py::{self, Adapter, DEFAULT_TIMEOUT_SECS};
use parity::{Fixture, Outcome, Registry};
use serde_json::Value;

/// 「### 行為差異清單」允許出現在 `windows` 欄的案例(單元、案例 id、差異編號);T1、T2 沒有。
const WINDOWS_CASES: &[(&str, &str, &str)] = &[];

/// `windows` 欄的筆數(與 `WINDOWS_CASES` 相同;改動差異清單時兩者一起改)。
const WINDOWS_ENTRY_COUNT: usize = 0;

/// 每份 fixture 的(單元、檔名、案例數、該 task L2 的下限)。
const FIXTURES: &[(&str, &str, usize, usize)] = &[
    ("project.constants", "constants.json", 1, 1),
    ("project.normalize_speed", "normalize_speed.json", 41, 30),
    ("project.normalize_pause", "normalize_pause.json", 37, 30),
    ("project.check_zhuyin", "check_zhuyin.json", 45, 40),
    (
        "project.check_pitch_points",
        "check_pitch_points.json",
        28,
        20,
    ),
    ("project.py_json", "py_json.json", 47, 40),
    ("project.validate_text", "validate_text.json", 265, 150),
];

/// `project.validate_text`(T2)的接受 / 拒絕筆數(以預期結果表計)。
const VALIDATE_TEXT_ACCEPTED: usize = 61;
const VALIDATE_TEXT_REJECTED: usize = 204;

fn platform() -> &'static str {
    if cfg!(target_os = "linux") {
        "linux"
    } else if cfg!(target_os = "macos") {
        "macos"
    } else if cfg!(windows) {
        "windows"
    } else {
        "other"
    }
}

fn manifest() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn fixture_dir() -> PathBuf {
    manifest().join("fixtures").join("project")
}

fn fixtures() -> Vec<Fixture> {
    let mut paths: Vec<PathBuf> = std::fs::read_dir(fixture_dir())
        .unwrap_or_else(|e| panic!("讀不到 fixtures/project:{e}"))
        .map(|e| e.expect("讀取目錄項失敗").path())
        .filter(|p| p.extension().is_some_and(|x| x == "json"))
        .collect();
    paths.sort();
    assert!(!paths.is_empty(), "fixtures/project 沒有 fixture");
    paths
        .iter()
        .map(|p| {
            let text = std::fs::read_to_string(p).expect("讀取 fixture 失敗");
            Fixture::parse(&text).unwrap_or_else(|e| panic!("{}:{e}", p.display()))
        })
        .collect()
}

type Table = BTreeMap<String, BTreeMap<String, BTreeMap<String, Value>>>;

fn table() -> Table {
    let path = manifest()
        .join("tests")
        .join("data")
        .join("project_expected.json");
    let text = std::fs::read_to_string(&path).expect("讀不到預期結果表");
    serde_json::from_str(&text).expect("預期結果表格式錯誤")
}

fn to_outcome(v: &Value) -> Outcome {
    let mut obj = serde_json::Map::new();
    obj.insert("id".to_string(), Value::String("x".to_string()));
    obj.extend(v.as_object().expect("預期須為物件").clone());
    parity::parse_response(&Value::Object(obj).to_string())
        .expect("預期須為合法結果")
        .outcome
}

/// 案例在指定平台的預期:有該平台欄用它,否則用 `all`。
fn expected(entry: &BTreeMap<String, Value>, platform: &str) -> Outcome {
    to_outcome(
        entry
            .get(platform)
            .or_else(|| entry.get("all"))
            .expect("缺少 all 欄"),
    )
}

#[test]
fn every_case_has_an_expectation_and_windows_column_is_limited() {
    let table = table();
    let mut fixture_keys = BTreeSet::new();
    for f in fixtures() {
        for c in &f.cases {
            fixture_keys.insert((f.unit.clone(), c.id.clone()));
        }
    }
    let mut table_keys = BTreeSet::new();
    let mut windows = BTreeSet::new();
    for (unit, cases) in &table {
        for (id, entry) in cases {
            table_keys.insert((unit.clone(), id.clone()));
            assert!(entry.contains_key("all"), "{unit} {id} 缺少 all 欄");
            for k in entry.keys() {
                assert!(
                    ["all", "windows"].contains(&k.as_str()),
                    "{unit} {id} 有不認得的欄位 {k}"
                );
            }
            if entry.contains_key("windows") {
                windows.insert((unit.clone(), id.clone()));
            }
        }
    }
    assert_eq!(fixture_keys, table_keys, "fixture 案例與預期表必須一一對應");
    let allowed: BTreeSet<(String, String)> = WINDOWS_CASES
        .iter()
        .map(|(u, i, _)| (u.to_string(), i.to_string()))
        .collect();
    assert_eq!(windows, allowed, "windows 欄只能出現在差異清單列出的案例");
    assert_eq!(windows.len(), WINDOWS_ENTRY_COUNT);
    assert_eq!(WINDOWS_CASES.len(), WINDOWS_ENTRY_COUNT);
}

#[test]
fn fixture_case_counts_are_fixed_and_meet_the_minimum() {
    let found: BTreeMap<String, usize> = fixtures()
        .into_iter()
        .map(|f| (f.unit, f.cases.len()))
        .collect();
    let want: BTreeMap<String, usize> = FIXTURES
        .iter()
        .map(|(u, _, n, _)| (u.to_string(), *n))
        .collect();
    assert_eq!(found, want, "fixture 與案例數須與 FIXTURES 相同");
    for (unit, file, n, min) in FIXTURES {
        assert!(n >= min, "{unit} 案例數 {n} 低於下限 {min}");
        assert!(fixture_dir().join(file).is_file(), "缺少 {file}");
    }
    let total: usize = FIXTURES.iter().map(|(_, _, n, _)| n).sum();
    println!("fixtures/project:{} 份、{total} 個案例", FIXTURES.len());
}

/// `project.validate_text` 的預期:接受時只有 `dump`(案例 id 以 `ok_` 開頭),拒絕時為 `{"class", "errors"}`;
/// 接受 / 拒絕筆數固定。
#[test]
fn validate_text_expectations_split_into_accept_and_reject() {
    let table = table();
    let unit = &table["project.validate_text"];
    let (mut accepted, mut rejected) = (0, 0);
    for (id, entry) in unit {
        let all = &entry["all"];
        assert_eq!(all["outcome"], "ok", "{id}");
        let obj = all["value"].as_object().expect("物件");
        if obj.contains_key("dump") {
            assert_eq!(obj.len(), 1, "{id}");
            // dump 為字串,或精簡形式 {"parts"}(可展開)
            let dump = &obj["dump"];
            assert!(
                dump.is_string() || parity::units_project::parts_text(&dump["parts"]).is_some(),
                "{id}"
            );
            assert!(id.starts_with("ok_"), "接受案例 id 以 ok_ 開頭:{id}");
            accepted += 1;
        } else {
            assert_eq!(obj["class"], "project_error", "{id}");
            assert!(!id.starts_with("ok_"), "{id}");
            rejected += 1;
        }
    }
    assert_eq!(
        (accepted, rejected),
        (VALIDATE_TEXT_ACCEPTED, VALIDATE_TEXT_REJECTED)
    );
    println!("project.validate_text:接受 {accepted} 筆、拒絕 {rejected} 筆");
}

/// 審議轉送的上限(DIR-6 第 1 輪 F-1):每行最多字元數、同一字元最多連續數。
const MAX_LINE_CHARS: usize = 12000;
const MAX_RUN: usize = 999;

/// 守門:`fixtures/project/*.json` 與預期結果表每行 ≤ 12000 字元、同一字元連續 ≤ 999 個——超長的重複內容
/// 一律改用 `validate_text` 的 `{"parts"}` 精簡形式(輸入)與 `{"dump": {"parts"}}`(結果)。
#[test]
fn fixture_and_table_lines_stay_short() {
    let mut files: Vec<PathBuf> = std::fs::read_dir(fixture_dir())
        .expect("讀取 fixtures/project")
        .map(|e| e.expect("目錄項").path())
        .filter(|p| p.extension().is_some_and(|x| x == "json"))
        .collect();
    files.push(
        manifest()
            .join("tests")
            .join("data")
            .join("project_expected.json"),
    );
    files.sort();
    for path in &files {
        let text = std::fs::read_to_string(path).expect("讀取");
        let (mut longest, mut run_max) = (0, 0);
        for (n, line) in text.split('\n').enumerate() {
            let chars: Vec<char> = line.chars().collect();
            longest = longest.max(chars.len());
            let mut run = 0;
            for (i, c) in chars.iter().enumerate() {
                run = if i > 0 && chars[i - 1] == *c {
                    run + 1
                } else {
                    1
                };
                run_max = run_max.max(run);
            }
            assert!(
                chars.len() <= MAX_LINE_CHARS,
                "{} 第 {} 行 {} 字元",
                path.display(),
                n + 1,
                chars.len()
            );
        }
        assert!(
            run_max <= MAX_RUN,
            "{} 有 {run_max} 個連續相同字元",
            path.display()
        );
        println!(
            "{}:最長行 {longest} 字元、最長連續同字元 {run_max} 個",
            path.file_name().unwrap_or_default().to_string_lossy()
        );
    }
}

#[test]
fn every_project_unit_is_registered() {
    let registry = Registry::builtin();
    for (unit, _, _, _) in FIXTURES {
        assert!(registry.contains(unit), "Rust 端沒有登錄 {unit}");
    }
}

#[test]
fn rust_matches_expected_on_this_platform() {
    let table = table();
    let registry = Registry::builtin();
    let plat = platform();
    let mut checked = 0;
    let mut failures = Vec::new();
    for f in fixtures() {
        for c in &f.cases {
            let got = registry.run(&f.unit, &c.input).expect("已登錄");
            let want = expected(&table[&f.unit][&c.id], plat);
            if got != want {
                failures.push(format!(
                    "{} {}:預期 {};Rust {}",
                    f.unit,
                    c.id,
                    want.summary(),
                    got.summary()
                ));
            }
            checked += 1;
        }
    }
    assert!(
        failures.is_empty(),
        "Rust 與預期不同:\n{}",
        failures.join("\n")
    );
    println!("Rust 等於預期({plat}):{checked} 個案例");
}

fn adapter() -> Adapter {
    Adapter {
        python: py::resolve_python(None, std::env::var_os("PARITY_PYTHON")),
        script: py::adapter_path(),
        timeout: Duration::from_secs(DEFAULT_TIMEOUT_SECS),
    }
}

#[test]
fn python_matches_expected_on_macos_and_linux() {
    let plat = platform();
    if plat == "windows" {
        println!(
            "略過 Python 端:project.py 頂層 import fcntl,在 Windows 無法執行(CHG-20261128-01 事實 H)"
        );
        return;
    }
    let table = table();
    let adapter = adapter();
    let mut checked = 0;
    let mut failures = Vec::new();
    for f in fixtures() {
        let got = py::run(&adapter, &f.unit, &f.cases).unwrap_or_else(|e| panic!("{}:{e}", f.unit));
        for (c, out) in f.cases.iter().zip(got) {
            let want = expected(&table[&f.unit][&c.id], plat);
            if out != want {
                failures.push(format!(
                    "{} {}:預期 {};Python {}",
                    f.unit,
                    c.id,
                    want.summary(),
                    out.summary()
                ));
            }
            checked += 1;
        }
    }
    assert!(
        failures.is_empty(),
        "Python 與預期不同:\n{}",
        failures.join("\n")
    );
    println!("Python 等於預期({plat}):{checked} 個案例");
}

/// L2:場景「數值四捨五入與錯誤訊息與 Python 相同」——以真執行檔與真轉接器跑每份 fixture(macOS / Linux)。
#[test]
fn parity_run_project_fixtures_exit_0() {
    if platform() == "windows" {
        println!("略過:project.py 在 Windows 無法執行(CHG-20261128-01 事實 H),改由預期結果表對照");
        return;
    }
    for (unit, file, n, _) in FIXTURES {
        let out = Command::new(env!("CARGO_BIN_EXE_parity"))
            .args(["run", "--unit", unit, "--fixture"])
            .arg(fixture_dir().join(file))
            .env_remove("PARITY_TEST_SLEEP")
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
        assert!(stdout.contains(&format!("案例數:{n}\n")), "{stdout}");
        assert!(stdout.contains("不一致數:0"), "{stdout}");
    }
}
