//! 以 Python 為錨的 `safe_fs` 預期結果表(CHG-20261125-01 代決 10)。
//!
//! 表在 `tests/data/safe_fs_expected.json`:`{單元: {案例 id: {"all": <結果>, "linux"?, "macos"?, "windows"?}}}`,
//! `<結果>` 為協定形式的 `{"outcome", "value" | "code" | "class"}`。
//!
//! - macOS / Linux:**Python 實際結果等於表中該平台的預期**(表以 Python 為錨,不能憑空寫);
//! - 三平台:**Rust 實際結果等於表中該平台的預期**——Windows 的 Rust 行為因此經由表間接等於
//!   macOS / Linux 上的 Python 行為(Python `safe_fs` 在 Windows 無法執行,事實 D);
//! - 每個 fixture 案例都有預期;`windows` 欄只出現在「### 行為差異清單」列出的案例(筆數以常數寫明);
//! - 性質:凡 macOS / Linux 預期為拒絕(`unsafe`、`invalid_name`)的案例,三平台的預期都是拒絕;
//! - 每個案例結束後兩邊的暫存工作目錄都不存在。
//!
//! Windows 無法建立符號連結時(本機未啟用開發人員模式):`GITHUB_ACTIONS=true` 判失敗;本機印出略過案例數。
//! 本檔所有測試以同一把鎖依序執行,工作目錄殘留檢查才不會被其他測試的進行中目錄干擾。

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::Duration;

use parity::py::{self, Adapter, DEFAULT_TIMEOUT_SECS};
use parity::{Fixture, Outcome, Registry};
use serde_json::Value;

static LOCK: Mutex<()> = Mutex::new(());

/// 「### 行為差異清單」允許出現在 `windows` 欄的案例(單元、案例 id、差異編號)。
const WINDOWS_CASES: &[(&str, &str, &str)] = &[
    ("safe_fs.check_name", "three_dots", "D-4"),
    ("safe_fs.check_name", "backslash", "D-4"),
    ("safe_fs.check_name", "colon", "D-4"),
    ("safe_fs.check_name", "trailing_dot", "D-4"),
    ("safe_fs.check_name", "trailing_space", "D-4"),
    ("safe_fs.check_name", "star", "D-4"),
    ("safe_fs.check_name", "question", "D-4"),
    ("safe_fs.check_name", "lt", "D-4"),
    ("safe_fs.check_name", "gt", "D-4"),
    ("safe_fs.check_name", "pipe", "D-4"),
    ("safe_fs.check_name", "quote", "D-4"),
    ("safe_fs.check_name", "ctrl_01", "D-4"),
    ("safe_fs.check_name", "ctrl_1f", "D-4"),
    // T2:Windows 上硬連結的來源是連結時回 Unsafe(Linux / macOS 連結到符號連結本身)
    ("safe_fs.link", "src_symlink_file_out", "D-3"),
    ("safe_fs.link", "src_symlink_dir_out", "D-3"),
    ("safe_fs.link", "src_symlink_in", "D-3"),
    ("safe_fs.link", "src_dangling", "D-3"),
    // T2:目錄改名到自己的子目錄內,Windows 只給「拒絕存取」(Linux / macOS 為 EINVAL)
    ("safe_fs.rename", "dir_into_itself", "D-7"),
];

/// `windows` 欄的筆數(與 `WINDOWS_CASES` 相同;改動差異清單時兩者一起改)。
const WINDOWS_ENTRY_COUNT: usize = 18;

/// 依差異編號分的 `windows` 欄筆數(T3:與「### 行為差異清單」D-3、D-4、D-7 記載的筆數一致;
/// 合計等於 `WINDOWS_ENTRY_COUNT`)。
const WINDOWS_COUNT_BY_DIFF: &[(&str, usize)] = &[("D-3", 4), ("D-4", 13), ("D-7", 1)];

/// 差異清單中屬 Windows 機制的編號(代決 12)。
const WINDOWS_DIFF_IDS: &[&str] = &[
    "D-3", "D-4", "D-5", "D-6", "D-7", "D-8", "D-9", "D-10", "D-11",
];

/// 工作目錄名稱前綴(Rust 單元 `parity-safe-fs-rs-`、Python 轉接器 `parity-safe-fs-py-`)。
const WORK_PREFIX: &str = "parity-safe-fs-";

/// Windows 無法建立符號連結時 Rust 單元回的錯誤類別。
const SYMLINK_PRIVILEGE: &str = "symlink_privilege";

fn lock() -> std::sync::MutexGuard<'static, ()> {
    LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

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

fn in_ci() -> bool {
    std::env::var_os("GITHUB_ACTIONS").is_some_and(|v| v == "true")
}

fn manifest() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn fixtures() -> Vec<Fixture> {
    let dir = manifest().join("fixtures").join("safe_fs");
    let mut paths: Vec<PathBuf> = std::fs::read_dir(&dir)
        .unwrap_or_else(|e| panic!("讀不到 fixtures/safe_fs:{e}"))
        .map(|e| e.expect("讀取目錄項失敗").path())
        .filter(|p| p.extension().is_some_and(|x| x == "json"))
        .collect();
    paths.sort();
    assert!(!paths.is_empty(), "fixtures/safe_fs 沒有 fixture");
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
        .join("safe_fs_expected.json");
    let text = std::fs::read_to_string(&path).expect("讀不到預期結果表");
    serde_json::from_str(&text).expect("預期結果表格式錯誤")
}

fn to_outcome(v: &Value) -> Outcome {
    let line = serde_json::json!({"id": "x"})
        .as_object()
        .cloned()
        .map(|mut o| {
            o.extend(v.as_object().expect("預期須為物件").clone());
            Value::Object(o).to_string()
        });
    parity::parse_response(&line.expect("已組出"))
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

fn is_reject(o: &Outcome) -> bool {
    matches!(o, Outcome::Reject { .. })
}

#[test]
fn every_case_has_an_expectation_and_windows_column_is_limited() {
    let _g = lock();
    let table = table();
    let fixtures = fixtures();
    let mut fixture_keys = BTreeSet::new();
    for f in &fixtures {
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
                    ["all", "linux", "macos", "windows"].contains(&k.as_str()),
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
    for (_, _, diff) in WINDOWS_CASES {
        assert!(
            WINDOWS_DIFF_IDS.contains(diff),
            "{diff} 不是 Windows 機制差異"
        );
    }
}

/// T3:`windows` 欄依差異編號的筆數與差異清單一致,合計為 `WINDOWS_ENTRY_COUNT`。
#[test]
fn windows_column_counts_match_difference_list() {
    let _g = lock();
    let mut by_diff: BTreeMap<&str, usize> = BTreeMap::new();
    for (_, _, diff) in WINDOWS_CASES {
        *by_diff.entry(diff).or_default() += 1;
    }
    let want: BTreeMap<&str, usize> = WINDOWS_COUNT_BY_DIFF.iter().copied().collect();
    assert_eq!(by_diff, want, "windows 欄依差異編號的筆數與差異清單不一致");
    assert_eq!(want.values().sum::<usize>(), WINDOWS_ENTRY_COUNT);
}

/// T3:屬 Windows 機制的差異編號為 D-3~D-12(代決事項 12;D-12 為 T2 補列的記錄項)。
#[test]
fn windows_diff_ids_are_d3_to_d12() {
    let want: Vec<String> = (3..=12).map(|n| format!("D-{n}")).collect();
    assert_eq!(
        WINDOWS_DIFF_IDS, want,
        "Windows 機制差異的編號與差異清單不一致"
    );
}

#[test]
fn rejections_are_never_looser_than_python() {
    // 凡 macOS / Linux 預期為拒絕的案例,三平台的預期都必須是拒絕(Global Constraints)。
    let _g = lock();
    for (unit, cases) in table() {
        for (id, entry) in cases {
            let anchors = ["linux", "macos"].map(|p| expected(&entry, p));
            if anchors.iter().any(is_reject) {
                for p in ["linux", "macos", "windows"] {
                    assert!(
                        is_reject(&expected(&entry, p)),
                        "{unit} {id}:Python 拒絕,{p} 的預期卻不是拒絕"
                    );
                }
            }
        }
    }
}

#[test]
fn rust_matches_expected_on_this_platform() {
    let _g = lock();
    let table = table();
    let registry = Registry::builtin();
    let plat = platform();
    let (mut checked, mut skipped) = (0, 0);
    let mut failures = Vec::new();
    for f in fixtures() {
        assert!(registry.contains(&f.unit), "Rust 端沒有登錄 {}", f.unit);
        for c in &f.cases {
            let got = registry.run(&f.unit, &c.input).expect("已登錄");
            if got
                == (Outcome::Error {
                    class: SYMLINK_PRIVILEGE.to_string(),
                })
            {
                assert!(!in_ci(), "CI 上無法建立符號連結,判失敗:{} {}", f.unit, c.id);
                skipped += 1;
                continue;
            }
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
    if skipped > 0 {
        println!("略過:無權限建立符號連結({skipped} 個案例)");
    }
}

#[test]
fn python_matches_expected_on_macos_and_linux() {
    let _g = lock();
    let plat = platform();
    if plat == "windows" {
        println!("略過 Python 端:Python safe_fs 在 Windows 無法執行(CHG-20261125-01 事實 D)");
        return;
    }
    let table = table();
    let adapter = Adapter {
        python: py::resolve_python(None, std::env::var_os("PARITY_PYTHON")),
        script: py::adapter_path(),
        timeout: Duration::from_secs(DEFAULT_TIMEOUT_SECS),
    };
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

fn work_dirs(base: &Path) -> BTreeSet<String> {
    std::fs::read_dir(base)
        .map(|it| {
            it.filter_map(|e| e.ok())
                .map(|e| e.file_name().to_string_lossy().to_string())
                .filter(|n| n.starts_with(WORK_PREFIX))
                .collect()
        })
        .unwrap_or_default()
}

#[test]
fn work_directories_are_removed_after_each_case() {
    let _g = lock();
    let base = std::env::temp_dir();
    let before = work_dirs(&base);
    let registry = Registry::builtin();
    for f in fixtures() {
        for c in &f.cases {
            registry.run(&f.unit, &c.input).expect("已登錄");
            let now = work_dirs(&base);
            assert!(
                now.is_subset(&before),
                "Rust 單元 {} {} 結束後工作目錄仍在",
                f.unit,
                c.id
            );
        }
        if platform() != "windows" {
            let adapter = Adapter {
                python: py::resolve_python(None, std::env::var_os("PARITY_PYTHON")),
                script: py::adapter_path(),
                timeout: Duration::from_secs(DEFAULT_TIMEOUT_SECS),
            };
            py::run(&adapter, &f.unit, &f.cases).unwrap_or_else(|e| panic!("{}:{e}", f.unit));
            assert!(
                work_dirs(&base).is_subset(&before),
                "Python 轉接器 {} 結束後工作目錄仍在",
                f.unit
            );
        }
    }
}
