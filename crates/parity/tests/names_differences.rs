//! 刻意差異的 Python 端確認(CHG-20261124-01 T3,代決 11)。
//!
//! L2:以子程序啟動真的 Python 轉接器,確認「### 行為差異清單」D-1~D-3 的每一種寫法
//! (24 個保留名稱 × 8 種 ASCII 大小寫組合 = 192 種)在 Python `names.normalize_name` 下為 `ok`,
//! Rust 相容單元 `names.normalize_name` 結果相同,而 Rust 新名稱函式 `normalize_new_name` 回
//! `Err(Reserved)`——證明清單中的差異是真的差異,不是兩邊都拒。
//!
//! 另以同一方式確認 W-4、W-7 不是差異(W-4 兩邊都拒、新名稱為 `Err(Invalid)`;
//! W-7 兩邊都接受、新名稱也接受)。
//!
//! 這些案例**不**放進比對 fixture(代決 11 (a)):比對單元只登錄相容函式,
//! `crates/parity/fixtures/` 下的 fixture 全部預期一致。
//!
//! 直譯器沿用比對工具的取得順序:環境變數 `PARITY_PYTHON` → 平台預設值;
//! 每次啟動轉接器沿用 `DEFAULT_TIMEOUT_SECS`(不改比對工具契約)。

use std::collections::BTreeSet;
use std::time::Duration;

use parity::py::{self, Adapter, DEFAULT_TIMEOUT_SECS};
use parity::{Case, Outcome, Registry};
use serde_json::Value;
use studio_core::names::{NewNameError, is_windows_reserved, normalize_new_name};

const UNIT: &str = "names.normalize_name";

/// 代決 10(確認閘 2026-11-25 採 (a)):24 個保留名稱。
const RESERVED: [&str; 24] = [
    "CON", "PRN", "AUX", "NUL", "COM0", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7",
    "COM8", "COM9", "LPT0", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
];

/// 本 CHG「### 行為差異清單」D-1~D-3 的寫法總數:24 × 8。
const RESERVED_SPELLINGS: usize = 192;

/// D-1~D-3 各自的名稱數(D-1:`CON` 等 4 個;D-2:`COM1`~`COM9`、`LPT1`~`LPT9` 共 18 個;
/// D-3:`COM0`、`LPT0` 共 2 個)。
const D1_NAMES: usize = 4;
const D2_NAMES: usize = 18;
const D3_NAMES: usize = 2;

fn adapter() -> Adapter {
    Adapter {
        python: py::resolve_python(None, std::env::var_os("PARITY_PYTHON")),
        script: py::adapter_path(),
        timeout: Duration::from_secs(DEFAULT_TIMEOUT_SECS),
    }
}

fn ascii_case_variants(name: &str) -> Vec<String> {
    let mut out = vec![String::new()];
    for c in name.chars() {
        let forms: Vec<char> = if c.is_ascii_alphabetic() {
            vec![c.to_ascii_uppercase(), c.to_ascii_lowercase()]
        } else {
            vec![c]
        };
        out = out
            .iter()
            .flat_map(|prefix| {
                forms.iter().map(move |&f| {
                    let mut s = prefix.clone();
                    s.push(f);
                    s
                })
            })
            .collect();
    }
    out
}

fn reserved_spellings() -> Vec<String> {
    RESERVED
        .iter()
        .flat_map(|n| ascii_case_variants(n))
        .collect()
}

fn cases_for(names: &[String]) -> Vec<Case> {
    names
        .iter()
        .enumerate()
        .map(|(i, n)| Case {
            id: format!("{i:03}_{n}"),
            input: Value::String(n.clone()),
        })
        .collect()
}

fn run_both(names: &[String]) -> (Vec<Outcome>, Vec<Outcome>) {
    let cases = cases_for(names);
    let python = py::run(&adapter(), UNIT, &cases).unwrap_or_else(|e| panic!("{UNIT}:{e}"));
    let registry = Registry::builtin();
    let rust = cases
        .iter()
        .map(|c| registry.run(UNIT, &c.input).expect("單元須已登錄"))
        .collect();
    (python, rust)
}

#[test]
fn difference_list_counts_match_the_chg() {
    assert_eq!(D1_NAMES + D2_NAMES + D3_NAMES, RESERVED.len());
    let distinct: BTreeSet<&str> = RESERVED.iter().copied().collect();
    assert_eq!(distinct.len(), 24);
    let d1 = RESERVED
        .iter()
        .filter(|n| ["CON", "PRN", "AUX", "NUL"].contains(n))
        .count();
    let d3 = RESERVED.iter().filter(|n| n.ends_with('0')).count();
    assert_eq!(d1, D1_NAMES);
    assert_eq!(d3, D3_NAMES);
    assert_eq!(RESERVED.len() - d1 - d3, D2_NAMES);
    let spellings: BTreeSet<String> = reserved_spellings().into_iter().collect();
    assert_eq!(spellings.len(), RESERVED_SPELLINGS);
}

#[test]
fn python_accepts_every_reserved_spelling_and_rust_new_name_rejects_it() {
    let names = reserved_spellings();
    assert_eq!(names.len(), RESERVED_SPELLINGS);
    let (python, rust) = run_both(&names);
    let mut not_accepted = Vec::new();
    for (i, name) in names.iter().enumerate() {
        let want = Outcome::Ok(Value::String(name.clone()));
        if python[i] != want {
            not_accepted.push(format!("{name}:Python {}", python[i].summary()));
        }
        assert_eq!(rust[i], want, "{name:?}:Rust 相容單元須與 Python 相同");
        assert!(is_windows_reserved(name), "{name:?}");
        assert_eq!(
            normalize_new_name(name),
            Err(NewNameError::Reserved),
            "{name:?}"
        );
    }
    assert!(
        not_accepted.is_empty(),
        "以下保留名稱在 Python normalize_name 不是 ok(差異清單不成立):\n{}",
        not_accepted.join("\n")
    );
    println!(
        "差異確認:{} 個保留名稱、{} 種寫法在 Python normalize_name 皆為 ok,Rust normalize_new_name 皆為 Err(Reserved)",
        RESERVED.len(),
        names.len()
    );
}

#[test]
fn w4_is_not_a_difference_both_sides_reject() {
    let names: Vec<String> = [
        "CON.txt",
        "nul.json",
        "COM\u{00B9}",
        "COM\u{00B2}",
        "COM\u{00B3}",
        "LPT\u{00B9}",
        "LPT\u{00B2}",
        "LPT\u{00B3}",
        "CONIN$",
        "CONOUT$",
        "CLOCK$",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    let (python, rust) = run_both(&names);
    let want = Outcome::Reject {
        code: "invalid".to_string(),
    };
    for (i, name) in names.iter().enumerate() {
        assert_eq!(python[i], want, "{name:?}:Python");
        assert_eq!(rust[i], want, "{name:?}:Rust");
        assert_eq!(
            normalize_new_name(name),
            Err(NewNameError::Invalid),
            "{name:?}"
        );
    }
}

#[test]
fn w7_is_not_a_difference_both_sides_accept() {
    let names: Vec<String> = [
        "CONX", "CON1", "COM10", "LPT", "NULL", "AUXX", "AUX_", "NUL-1",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    let (python, rust) = run_both(&names);
    for (i, name) in names.iter().enumerate() {
        let want = Outcome::Ok(Value::String(name.clone()));
        assert_eq!(python[i], want, "{name:?}:Python");
        assert_eq!(rust[i], want, "{name:?}:Rust");
        assert_eq!(normalize_new_name(name), Ok(name.as_str()), "{name:?}");
    }
}
