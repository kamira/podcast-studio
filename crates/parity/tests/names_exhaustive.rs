//! `names.fold_name` 與 `names.name_char_ok` 的全碼位比對(CHG-20261124-01 T2,代決 7)。
//!
//! L2:以子程序啟動真的 Python 轉接器,對 U+0000~U+10FFFF 全部 Unicode 純量值
//! (不含 U+D800~U+DFFF)比對兩邊比對單元的結果,不一致時列出碼位。
//!
//! - `name_char_ok`:一個請求一個碼位,不受 Unicode 版本影響,一律比對、不一致即失敗。
//! - `fold_name`:完整大小寫摺疊逐字進行、不依上下文,所以把連續碼位串成一個字串分批送出;
//!   某批不一致時再逐碼位查出是哪些碼位。執行中 Python 的 `unicodedata.unidata_version`
//!   與摺疊表表頭版本([`CASEFOLD_UNICODE_VERSION`])相同時一律比對;不同時,環境變數
//!   `GITHUB_ACTIONS` 為 `true`(CI)即判失敗、不略過,只有本機才印出「略過」與差異碼位數。
//! - 逾時:每次啟動轉接器沿用 `DEFAULT_TIMEOUT_SECS`(60 秒,不改比對工具契約);
//!   請求依碼位範圍分批,每批各啟動一次轉接器,批次大小以最慢的平台(Windows)估算。
//!   各批耗時以 `cargo test -- --nocapture` 可見。
//!
//! 直譯器沿用比對工具的取得順序:環境變數 `PARITY_PYTHON` → 平台預設值
//! (CI 未設 `PARITY_PYTHON`,即 `actions/setup-python` 安裝的 3.11)。

use std::process::Command;
use std::time::{Duration, Instant};

use parity::py::{self, Adapter, DEFAULT_TIMEOUT_SECS};
use parity::{Case, Outcome, Registry};
use serde_json::Value;
use studio_core::names::CASEFOLD_UNICODE_VERSION;

/// `name_char_ok` 每次啟動轉接器送出的碼位範圍大小(一個請求一個碼位)。
/// 本機 Windows(Python 3.11、debug 建置)實測一批約 2.2~2.5 秒,與 60 秒逾時相距甚遠。
const CHAR_BATCH_SPAN: u32 = 0x2_0000;

/// `fold_name` 每個請求串接的碼位數;全部碼位一次啟動轉接器送完。
const FOLD_CHARS_PER_CASE: usize = 2048;

/// 不一致時最多列出的碼位數。
const MAX_LISTED: usize = 50;

fn adapter() -> Adapter {
    Adapter {
        python: py::resolve_python(None, std::env::var_os("PARITY_PYTHON")),
        script: py::adapter_path(),
        timeout: Duration::from_secs(DEFAULT_TIMEOUT_SECS),
    }
}

fn scalars_in(lo: u32, hi_exclusive: u32) -> Vec<char> {
    (lo..hi_exclusive).filter_map(char::from_u32).collect()
}

fn all_scalars() -> Vec<char> {
    scalars_in(0, 0x11_0000)
}

fn cp_label(c: char) -> String {
    format!("U+{:04X}", c as u32)
}

fn one_char_case(c: char) -> Case {
    Case {
        id: cp_label(c),
        input: Value::String(c.to_string()),
    }
}

/// 啟動一次轉接器並計時;逾時或協定錯誤即讓測試失敗。
fn run_python(unit: &str, cases: &[Case], label: &str) -> Vec<Outcome> {
    let started = Instant::now();
    let out = py::run(&adapter(), unit, cases).unwrap_or_else(|e| panic!("{unit} {label}:{e}"));
    println!(
        "{unit} {label}:{} 個請求,轉接器耗時 {:.2} 秒",
        cases.len(),
        started.elapsed().as_secs_f64()
    );
    out
}

fn run_rust(unit: &str, cases: &[Case]) -> Vec<Outcome> {
    let registry = Registry::builtin();
    cases
        .iter()
        .map(|c| registry.run(unit, &c.input).expect("單元須已登錄"))
        .collect()
}

/// 逐碼位比對,回傳不一致的(碼位標籤, Python, Rust)。
fn per_char_mismatches(unit: &str, chars: &[char], label: &str) -> Vec<(String, Outcome, Outcome)> {
    let cases: Vec<Case> = chars.iter().copied().map(one_char_case).collect();
    let python = run_python(unit, &cases, label);
    let rust = run_rust(unit, &cases);
    cases
        .iter()
        .zip(python.into_iter().zip(rust))
        .filter(|(_, (p, r))| p != r)
        .map(|(c, (p, r))| (c.id.clone(), p, r))
        .collect()
}

fn describe(mismatches: &[(String, Outcome, Outcome)]) -> String {
    let mut s: Vec<String> = mismatches
        .iter()
        .take(MAX_LISTED)
        .map(|(id, p, r)| format!("  - {id}:Python {};Rust {}", p.summary(), r.summary()))
        .collect();
    if mismatches.len() > MAX_LISTED {
        s.push(format!("  …另有 {} 個", mismatches.len() - MAX_LISTED));
    }
    // 每行前加換行;沒有不一致時為空字串。
    s.iter().map(|line| format!("\n{line}")).collect()
}

/// 執行中 Python 的 `unicodedata.unidata_version`。
fn python_unicode_version() -> String {
    let out = Command::new(adapter().python)
        .args([
            "-c",
            "import unicodedata, sys; sys.stdout.write(unicodedata.unidata_version)",
        ])
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .output()
        .expect("無法啟動 Python 直譯器");
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    String::from_utf8(out.stdout)
        .expect("輸出須為 UTF-8")
        .trim()
        .to_string()
}

/// `fold_name` 全碼位比對的判定方式。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum FoldVerdict {
    /// 版本相同:比對,不一致即失敗。
    Compare,
    /// 版本不同且在 CI:判失敗(不略過)。
    FailInCi,
    /// 版本不同且在本機:印出略過訊息與差異碼位數,不判失敗。
    SkipLocally,
}

/// 依執行中 Python 的 Unicode 版本、摺疊表表頭版本與環境變數 `GITHUB_ACTIONS` 決定判定方式。
fn fold_verdict(
    python_version: &str,
    table_version: &str,
    github_actions: Option<&str>,
) -> FoldVerdict {
    if python_version == table_version {
        FoldVerdict::Compare
    } else if github_actions == Some("true") {
        FoldVerdict::FailInCi
    } else {
        FoldVerdict::SkipLocally
    }
}

#[test]
fn fold_verdict_compares_when_versions_match() {
    assert_eq!(fold_verdict("14.0.0", "14.0.0", None), FoldVerdict::Compare);
    assert_eq!(
        fold_verdict("14.0.0", "14.0.0", Some("true")),
        FoldVerdict::Compare
    );
}

#[test]
fn fold_verdict_fails_in_ci_when_versions_differ() {
    assert_eq!(
        fold_verdict("15.0.0", "14.0.0", Some("true")),
        FoldVerdict::FailInCi
    );
    assert_eq!(
        fold_verdict("16.0.0", "14.0.0", Some("true")),
        FoldVerdict::FailInCi
    );
}

#[test]
fn fold_verdict_skips_only_outside_ci() {
    assert_eq!(
        fold_verdict("15.0.0", "14.0.0", None),
        FoldVerdict::SkipLocally
    );
    // 只有字面值 true 才算 CI(GitHub Actions 的設定值)。
    assert_eq!(
        fold_verdict("15.0.0", "14.0.0", Some("false")),
        FoldVerdict::SkipLocally
    );
    assert_eq!(
        fold_verdict("15.0.0", "14.0.0", Some("")),
        FoldVerdict::SkipLocally
    );
}

#[test]
fn scalar_value_count_excludes_surrogates() {
    assert_eq!(all_scalars().len(), 0x11_0000 - 0x800);
}

#[test]
fn name_char_ok_matches_python_for_every_scalar_value() {
    let started = Instant::now();
    let mut mismatches = Vec::new();
    let mut total = 0usize;
    let mut lo = 0u32;
    while lo < 0x11_0000 {
        let hi = (lo + CHAR_BATCH_SPAN).min(0x11_0000);
        let chars = scalars_in(lo, hi);
        total += chars.len();
        let label = format!("U+{lo:04X}~U+{:04X}", hi - 1);
        mismatches.extend(per_char_mismatches("names.name_char_ok", &chars, &label));
        lo = hi;
    }
    println!(
        "names.name_char_ok 全碼位:{total} 個碼位,不一致 {} 個,總耗時 {:.2} 秒",
        mismatches.len(),
        started.elapsed().as_secs_f64()
    );
    assert_eq!(total, 0x11_0000 - 0x800);
    assert!(
        mismatches.is_empty(),
        "names.name_char_ok 有 {} 個碼位兩邊不一致:{}",
        mismatches.len(),
        describe(&mismatches)
    );
}

#[test]
fn fold_name_matches_python_for_every_scalar_value() {
    let started = Instant::now();
    let python_version = python_unicode_version();
    let github_actions = std::env::var("GITHUB_ACTIONS").ok();
    let verdict = fold_verdict(
        &python_version,
        CASEFOLD_UNICODE_VERSION,
        github_actions.as_deref(),
    );

    // 串接連續碼位分批送出;id 記錄每批的起訖碼位。
    let chars = all_scalars();
    let chunks: Vec<&[char]> = chars.chunks(FOLD_CHARS_PER_CASE).collect();
    let cases: Vec<Case> = chunks
        .iter()
        .map(|chunk| Case {
            id: format!(
                "{}~{}",
                cp_label(chunk[0]),
                cp_label(chunk[chunk.len() - 1])
            ),
            input: Value::String(chunk.iter().collect()),
        })
        .collect();
    let python = run_python("names.fold_name", &cases, "全碼位串接");
    let rust = run_rust("names.fold_name", &cases);

    // 不一致的批次逐碼位重查,找出碼位。
    let bad_chars: Vec<char> = chunks
        .iter()
        .zip(python.iter().zip(&rust))
        .filter(|(_, (p, r))| p != r)
        .flat_map(|(chunk, _)| chunk.iter().copied())
        .collect();
    let mismatches = if bad_chars.is_empty() {
        Vec::new()
    } else {
        per_char_mismatches("names.fold_name", &bad_chars, "不一致批次逐碼位")
    };
    println!(
        "names.fold_name 全碼位:Python Unicode {python_version},摺疊表 {CASEFOLD_UNICODE_VERSION},\
         {} 個碼位({} 個請求),不一致 {} 個,總耗時 {:.2} 秒",
        chars.len(),
        cases.len(),
        mismatches.len(),
        started.elapsed().as_secs_f64()
    );
    // 批次不一致但逐碼位都一致,代表摺疊依上下文(違反分批的前提),同樣判失敗。
    assert!(
        bad_chars.is_empty() || !mismatches.is_empty(),
        "fold_name 批次結果不一致,但逐碼位全部一致:分批的前提(逐字摺疊)不成立"
    );

    match verdict {
        FoldVerdict::Compare => assert!(
            mismatches.is_empty(),
            "names.fold_name 有 {} 個碼位兩邊不一致:{}",
            mismatches.len(),
            describe(&mismatches)
        ),
        FoldVerdict::FailInCi => panic!(
            "CI 的 Python Unicode 版本 {python_version} 與摺疊表 {CASEFOLD_UNICODE_VERSION} 不同\
             (差異碼位 {} 個);CI 不得略過全碼位比對{}",
            mismatches.len(),
            describe(&mismatches)
        ),
        FoldVerdict::SkipLocally => println!(
            "略過:Python Unicode 版本 {python_version} 與摺疊表 {CASEFOLD_UNICODE_VERSION} 不同,\
             差異碼位 {} 個(不判失敗;以 PARITY_PYTHON 指向 Python 3.11 才會比對){}",
            mismatches.len(),
            describe(&mismatches)
        ),
    }
}
