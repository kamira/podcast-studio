//! Windows 補充名稱規則的 L1 測試(CHG-20261124-01 T3,代決 8~11)。
//!
//! 鎖住「### 行為差異清單」D-1~D-3 與 §6 的 W-1~W-7:
//!
//! - 代決 10 集合內 24 個名稱的全部 ASCII 大小寫組合(每個 8 種,共 192 種寫法):
//!   `is_windows_reserved` 為 `true`、相容的 `normalize_name` 照 Python 接受、
//!   新名稱的 `normalize_new_name` 回 `Err(Reserved)`(刻意與 Python 不同)。
//! - W-4(帶副檔名、上標數字、`CONIN$` 等)、W-5(結尾句點與空白、`\`、`:` 等)逐例回 `Err(Invalid)`,
//!   且 `normalize_name` 為 `None`(與 Python 相同,不是差異)。
//! - W-7(前綴或延伸,如 `CONX`、`COM10`)三者皆合格。
//! - 性質:凡 `normalize_new_name` 接受者,`normalize_name` 必接受且回同一切片(Global Constraints)。
//! - 三平台一致:本檔沒有任何平台條件,CI 三格執行同一組斷言(代決 8)。
//!
//! Python 端確實接受 D-1~D-3 名稱的確認在 `crates/parity/tests/names_differences.rs`。

use std::collections::BTreeSet;

use studio_core::names::{NewNameError, is_windows_reserved, normalize_name, normalize_new_name};

/// 代決 10(確認閘 2026-11-25 採 (a)):24 個保留名稱。
const RESERVED: [&str; 24] = [
    "CON", "PRN", "AUX", "NUL", "COM0", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7",
    "COM8", "COM9", "LPT0", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
];

/// 每個名稱 3 個字母,各有大小寫兩種寫法(數字沒有大小寫):2³ = 8。
const SPELLINGS_PER_NAME: usize = 8;

/// 24 × 8 = 192 種寫法(本 CHG「### 行為差異清單」D-1~D-3、§6 結論)。
const RESERVED_SPELLINGS: usize = 192;

/// 一個名稱的全部 ASCII 大小寫組合。
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

fn all_reserved_spellings() -> Vec<String> {
    RESERVED
        .iter()
        .flat_map(|n| ascii_case_variants(n))
        .collect()
}

// ---------- 集合與寫法數 ----------

#[test]
fn reserved_set_has_24_names_and_192_spellings() {
    let names: BTreeSet<&str> = RESERVED.iter().copied().collect();
    assert_eq!(names.len(), 24, "保留名稱須為 24 個且不重複");
    for n in RESERVED {
        assert_eq!(ascii_case_variants(n).len(), SPELLINGS_PER_NAME, "{n}");
    }
    let spellings: BTreeSet<String> = all_reserved_spellings().into_iter().collect();
    assert_eq!(spellings.len(), RESERVED_SPELLINGS);
    assert_eq!(RESERVED.len() * SPELLINGS_PER_NAME, RESERVED_SPELLINGS);
    println!(
        "保留名稱 {} 個、寫法 {} 種:{}",
        RESERVED.len(),
        spellings.len(),
        RESERVED.join(" ")
    );
}

#[test]
fn reserved_set_includes_com0_and_lpt0() {
    // 代決 10 (a):在 CHG-20261122-02 代決 14 原列之外多加 COM0、LPT0(W-3、D-3)。
    for name in ["COM0", "com0", "LPT0", "lpt0"] {
        assert!(is_windows_reserved(name), "{name}");
        assert_eq!(
            normalize_new_name(name),
            Err(NewNameError::Reserved),
            "{name}"
        );
    }
}

// ---------- D-1~D-3:192 種寫法 ----------

#[test]
fn every_reserved_spelling_is_reserved_for_new_names_only() {
    let spellings = all_reserved_spellings();
    assert_eq!(spellings.len(), RESERVED_SPELLINGS);
    for name in &spellings {
        assert!(is_windows_reserved(name), "{name:?} 應為保留名稱");
        // 相容函式與 Python 相同:接受,回原字串(代決 9:既有名稱照常可讀)。
        assert_eq!(normalize_name(name), Some(name.as_str()), "{name:?}");
        // 新名稱函式刻意更嚴(D-1~D-3)。
        assert_eq!(
            normalize_new_name(name),
            Err(NewNameError::Reserved),
            "{name:?}"
        );
    }
}

#[test]
fn reserved_spellings_cover_mixed_case_examples() {
    // W-1、W-2 列的例子與 feature 場景的名稱。
    for name in [
        "CON", "con", "Con", "cOn", "Nul", "nUL", "com1", "COM1", "Lpt9", "lPt9", "aux", "PrN",
    ] {
        assert_eq!(
            normalize_new_name(name),
            Err(NewNameError::Reserved),
            "{name:?}"
        );
        assert_eq!(normalize_name(name), Some(name), "{name:?}");
    }
}

#[test]
fn reserved_count_over_ascii_letter_and_digit_strings_is_192() {
    // 反向確認集合沒有多擋:長度 3 的字母字串與「3 個字母 + 1 個數字」的字串中,
    // 被判為保留名稱的恰為 192 種寫法。
    let letters: Vec<char> = ('A'..='Z').chain('a'..='z').collect();
    let digits: Vec<char> = ('0'..='9').collect();
    let mut found = BTreeSet::new();
    let mut s = String::with_capacity(4);
    for &a in &letters {
        for &b in &letters {
            for &c in &letters {
                s.clear();
                s.extend([a, b, c]);
                if is_windows_reserved(&s) {
                    found.insert(s.clone());
                }
                for &d in &digits {
                    s.truncate(3);
                    s.push(d);
                    if is_windows_reserved(&s) {
                        found.insert(s.clone());
                    }
                }
            }
        }
    }
    let want: BTreeSet<String> = all_reserved_spellings().into_iter().collect();
    assert_eq!(found, want);
}

// ---------- W-4:白名單已拒,`Err(Invalid)` ----------

#[test]
fn w4_reserved_with_extension_superscript_or_dollar_is_invalid() {
    // 交接(DIR-6 第 4 輪):逐例斷言。
    let cases = [
        "CON.txt",
        "nul.json",
        "COM\u{00B9}", // COM¹
        "COM\u{00B2}", // COM²
        "COM\u{00B3}", // COM³
        "LPT\u{00B9}", // LPT¹
        "LPT\u{00B2}", // LPT²
        "LPT\u{00B3}", // LPT³
        "CONIN$",
        "CONOUT$",
        "CLOCK$",
    ];
    assert_eq!(cases.len(), 11);
    for name in cases {
        assert_eq!(normalize_name(name), None, "{name:?}");
        assert_eq!(
            normalize_new_name(name),
            Err(NewNameError::Invalid),
            "{name:?}"
        );
        assert!(!is_windows_reserved(name), "{name:?} 不在代決 10 集合內");
    }
}

#[test]
fn w4_examples_one_by_one() {
    // 與上一條相同的資料,逐條展開,失敗時直接指出是哪一例。
    assert_eq!(normalize_new_name("CON.txt"), Err(NewNameError::Invalid));
    assert_eq!(normalize_name("CON.txt"), None);
    assert_eq!(normalize_new_name("nul.json"), Err(NewNameError::Invalid));
    assert_eq!(normalize_name("nul.json"), None);
    assert_eq!(
        normalize_new_name("COM\u{00B9}"),
        Err(NewNameError::Invalid)
    );
    assert_eq!(normalize_name("COM\u{00B9}"), None);
    assert_eq!(
        normalize_new_name("COM\u{00B2}"),
        Err(NewNameError::Invalid)
    );
    assert_eq!(normalize_name("COM\u{00B2}"), None);
    assert_eq!(
        normalize_new_name("COM\u{00B3}"),
        Err(NewNameError::Invalid)
    );
    assert_eq!(normalize_name("COM\u{00B3}"), None);
    assert_eq!(
        normalize_new_name("LPT\u{00B9}"),
        Err(NewNameError::Invalid)
    );
    assert_eq!(normalize_name("LPT\u{00B9}"), None);
    assert_eq!(
        normalize_new_name("LPT\u{00B2}"),
        Err(NewNameError::Invalid)
    );
    assert_eq!(normalize_name("LPT\u{00B2}"), None);
    assert_eq!(
        normalize_new_name("LPT\u{00B3}"),
        Err(NewNameError::Invalid)
    );
    assert_eq!(normalize_name("LPT\u{00B3}"), None);
    assert_eq!(normalize_new_name("CONIN$"), Err(NewNameError::Invalid));
    assert_eq!(normalize_name("CONIN$"), None);
    assert_eq!(normalize_new_name("CONOUT$"), Err(NewNameError::Invalid));
    assert_eq!(normalize_name("CONOUT$"), None);
    assert_eq!(normalize_new_name("CLOCK$"), Err(NewNameError::Invalid));
    assert_eq!(normalize_name("CLOCK$"), None);
}

// ---------- W-5:白名單已拒,`Err(Invalid)` ----------

#[test]
fn w5_trailing_dot_space_and_forbidden_chars_are_invalid() {
    let cases = [
        "name.",
        "name ",
        "CON.",
        "CON ",
        "a\\b",
        "CON\\x",
        "a:b",
        "CON:stream",
        "a/b",
        "a*b",
        "a?b",
        "a\"b",
        "a<b",
        "a>b",
        "a|b",
        "a\u{0000}b",
        "a\u{0001}b",
        "a\u{001F}b",
        "tab\t",
        "line\n",
        "a\u{007F}b",
        "\u{FF23}\u{FF2F}\u{FF2E}", // 全形 ＣＯＮ
        "",
    ];
    for name in cases {
        assert_eq!(normalize_name(name), None, "{name:?}");
        assert_eq!(
            normalize_new_name(name),
            Err(NewNameError::Invalid),
            "{name:?}"
        );
    }
    let too_long = "a".repeat(65);
    assert_eq!(normalize_new_name(&too_long), Err(NewNameError::Invalid));
}

// ---------- W-7:前綴或延伸不受影響 ----------

#[test]
fn w7_prefixes_and_extensions_are_not_reserved() {
    for name in [
        "CONX", "CON1", "COM10", "LPT", "NULL", "AUXX", "AUX_", "NUL-1", "COM", "CO", "LPT10",
        "conx", "com10", "null", "_CON", "CON_", "PRN-", "ZCOM1", "COM1A", "COM-1",
    ] {
        assert!(!is_windows_reserved(name), "{name:?}");
        assert_eq!(normalize_name(name), Some(name), "{name:?}");
        let out = normalize_new_name(name).unwrap_or_else(|e| panic!("{name:?}:{e:?}"));
        assert!(std::ptr::eq(out, name), "{name:?} 須回傳原字串切片");
    }
}

#[test]
fn ordinary_new_names_are_accepted() {
    for name in [
        "a",
        "podcast_01",
        "第一集",
        "ひらがな",
        "カタカナー",
        "人々",
        "夜市的味道",
    ] {
        assert_eq!(normalize_new_name(name), Ok(name), "{name:?}");
    }
    let n64 = "漢".repeat(64);
    assert_eq!(normalize_new_name(&n64), Ok(n64.as_str()));
}

// ---------- 性質:新名稱只能更嚴 ----------

/// `crates/studio-core/tests/names.rs` 使用的合格與不合格名稱,加上本檔的案例。
fn sample_names() -> Vec<String> {
    let mut v: Vec<String> = [
        // names.rs 的合格名稱
        "a",
        "Z",
        "0",
        "-",
        "_",
        "podcast_01",
        "第一集",
        "ひらがな",
        "カタカナー",
        "人々",
        "mix中文かなカナ_-09",
        "CON",
        "com1",
        "My_Project-01ゆめ",
        // names.rs 的不合格名稱
        " ",
        "a b",
        ".",
        "..",
        "a.b",
        "a/b",
        "a\\b",
        "a:b",
        "con.txt",
        "name.",
        "name ",
        "café",
        "straße",
        "한국어",
        "ＡＢＣ",
        "Ⅻ",
        "中・点",
        "a\u{0301}",
        "\u{0301}",
        "a\u{200B}b",
        "a\u{202E}b",
        "は\u{3099}",
        "a\u{0000}",
        "tab\t",
        "line\n",
        "a／b",
        "COM\u{00B9}",
        "CONIN$",
        "",
        // W-7
        "CONX",
        "CON1",
        "COM10",
        "LPT",
        "NULL",
        "AUXX",
        "AUX_",
        "NUL-1",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    v.push("a".repeat(64));
    v.push("a".repeat(65));
    v.push("漢".repeat(64));
    v.push("漢".repeat(65));
    v.extend(all_reserved_spellings());
    v
}

fn assert_new_is_stricter(name: &str) {
    match normalize_new_name(name) {
        Ok(out) => {
            let compat =
                normalize_name(name).unwrap_or_else(|| panic!("{name:?}:新名稱接受而相容函式拒絕"));
            assert!(std::ptr::eq(out, compat), "{name:?} 兩者須回同一切片");
            assert!(std::ptr::eq(out, name), "{name:?} 須回原字串切片");
            assert!(!is_windows_reserved(name), "{name:?}");
        }
        Err(NewNameError::Invalid) => {
            assert_eq!(normalize_name(name), None, "{name:?}");
        }
        Err(NewNameError::Reserved) => {
            assert_eq!(normalize_name(name), Some(name), "{name:?}");
            assert!(is_windows_reserved(name), "{name:?}");
        }
    }
}

#[test]
fn new_name_accepted_implies_compat_accepted_on_samples() {
    for name in sample_names() {
        assert_new_is_stricter(&name);
    }
}

#[test]
fn new_name_accepted_implies_compat_accepted_on_every_single_scalar() {
    // 全部 Unicode 純量值各當一個字的名稱:同時確認不 panic。
    for c in (0..=0x10FFFFu32).filter_map(char::from_u32) {
        assert_new_is_stricter(&c.to_string());
    }
}

#[test]
fn new_name_differs_from_compat_only_on_reserved_spellings() {
    // 長度 3~4、由保留名稱用到的字母(大小寫)與數字組成的全部字串:
    // 兩函式結果不同者,恰為 192 種保留寫法。
    let alphabet: Vec<char> = "CONPRAUXLTMconpraux ltm0123456789"
        .chars()
        .filter(|c| !c.is_whitespace())
        .collect();
    let mut differing = BTreeSet::new();
    let mut buf = String::new();
    for &a in &alphabet {
        for &b in &alphabet {
            for &c in &alphabet {
                buf.clear();
                buf.extend([a, b, c]);
                if normalize_new_name(&buf).ok() != normalize_name(&buf) {
                    differing.insert(buf.clone());
                }
                for &d in &alphabet {
                    buf.truncate(3);
                    buf.push(d);
                    if normalize_new_name(&buf).ok() != normalize_name(&buf) {
                        differing.insert(buf.clone());
                    }
                }
            }
        }
    }
    let want: BTreeSet<String> = all_reserved_spellings().into_iter().collect();
    assert_eq!(differing, want);
}
