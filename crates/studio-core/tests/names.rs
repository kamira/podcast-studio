//! `studio_core::names` 相容部分的 L1 測試(CHG-20261124-01 T2,代決 3、7)。
//!
//! 期望值取自 Python `names.py`(事實 A、B):常數逐項相同、白名單區間兩端與兩端外一碼、
//! 長度以 Unicode 純量值計 1~64、合格時回傳原字串切片、`fold_name` 等於 `str.casefold`。
//! 全部碼位與 Python 的比對在 `crates/parity/tests/names_exhaustive.rs`。

use studio_core::names::{
    CASEFOLD_UNICODE_VERSION, MAX_NAME, NAME_ASCII, NAME_RANGES, fold_name, name_char_ok,
    normalize_name,
};

fn ch(cp: u32) -> char {
    char::from_u32(cp).unwrap_or_else(|| panic!("U+{cp:04X} 不是 Unicode 純量值"))
}

// ---------- 常數 ----------

#[test]
fn max_name_is_64() {
    assert_eq!(MAX_NAME, 64);
}

#[test]
fn name_ascii_matches_python_sorted_by_code_point() {
    // Python:"".join(sorted(NAME_ASCII))
    assert_eq!(
        NAME_ASCII,
        "-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijklmnopqrstuvwxyz"
    );
    assert_eq!(NAME_ASCII.chars().count(), 64);
    let chars: Vec<char> = NAME_ASCII.chars().collect();
    assert!(chars.windows(2).all(|w| w[0] < w[1]), "須依碼位嚴格遞增");
}

#[test]
fn name_ranges_match_python_in_order() {
    assert_eq!(
        NAME_RANGES,
        &[
            (0x4E00, 0x9FFF),
            (0x3400, 0x4DBF),
            (0x3041, 0x3096),
            (0x30A1, 0x30FA),
            (0x30FC, 0x30FC),
            (0x3005, 0x3005),
        ]
    );
}

#[test]
fn whitelist_size_is_27826() {
    // 事實 B:64 + 20992 + 6592 + 86 + 90 + 1 + 1
    let n = (0..=0x10FFFFu32)
        .filter_map(char::from_u32)
        .filter(|&c| name_char_ok(c))
        .count();
    assert_eq!(n, 27826);
}

#[test]
fn casefold_table_version_is_python_3_11_unicode() {
    // 基準為 CI 所用的 Python 3.11(代決 7),其 unicodedata.unidata_version 為 14.0.0。
    assert_eq!(CASEFOLD_UNICODE_VERSION, "14.0.0");
}

// ---------- name_char_ok ----------

#[test]
fn name_char_ok_accepts_every_ascii_whitelist_char() {
    for c in NAME_ASCII.chars() {
        assert!(name_char_ok(c), "{c:?}");
    }
}

#[test]
fn name_char_ok_rejects_other_ascii() {
    for cp in 0..0x80u32 {
        let c = ch(cp);
        assert_eq!(name_char_ok(c), NAME_ASCII.contains(c), "U+{cp:04X}");
    }
    for c in [
        ' ', '.', '/', '\\', ':', '*', '?', '"', '<', '>', '|', '\0', '\n', '\u{7F}',
    ] {
        assert!(!name_char_ok(c), "{c:?}");
    }
}

#[test]
fn name_char_ok_range_boundaries() {
    let cases: &[(u32, bool)] = &[
        (0x3040, false),
        (0x3041, true),
        (0x3096, true),
        (0x3097, false),
        (0x30A0, false),
        (0x30A1, true),
        (0x30FA, true),
        (0x30FB, false), // 中點「・」
        (0x30FC, true),  // 長音符「ー」
        (0x30FD, false),
        (0x3004, false),
        (0x3005, true), // 疊字符「々」
        (0x3006, false),
        (0x33FF, false),
        (0x3400, true),
        (0x4DBF, true),
        (0x4DC0, false),
        (0x4DFF, false),
        (0x4E00, true),
        (0x9FFF, true),
        (0xA000, false),
    ];
    for &(cp, want) in cases {
        assert_eq!(name_char_ok(ch(cp)), want, "U+{cp:04X}");
    }
}

#[test]
fn name_char_ok_rejects_lookalikes_and_invisibles() {
    for cp in [
        0x00E9, // é
        0x00DF, // ß
        0xAC00, // 韓文
        0xFF21, // 全形 A
        0xFF10, // 全形 0
        0x2160, // 羅馬數字 Ⅰ
        0x3000, // 全形空白
        0x0301, // 組合重音符
        0x3099, // 濁點組合記號
        0x200B, // 零寬空白
        0x200D, // 零寬連接
        0x202E, // 方向覆寫
        0xFF0F, // 全形斜線
        0x00B9, // 上標 1
        0x10FFFF,
    ] {
        assert!(!name_char_ok(ch(cp)), "U+{cp:04X}");
    }
}

// ---------- normalize_name ----------

#[test]
fn normalize_name_length_bounds_ascii() {
    assert_eq!(normalize_name(""), None);
    assert_eq!(normalize_name("a"), Some("a"));
    let n64 = "a".repeat(64);
    let n65 = "a".repeat(65);
    assert_eq!(normalize_name(&n64), Some(n64.as_str()));
    assert_eq!(normalize_name(&n65), None);
}

#[test]
fn normalize_name_length_counts_scalar_values_not_bytes() {
    // 64 個 CJK 字為 192 位元組,仍合格(事實 B)。
    let n1 = "漢";
    let n64 = "漢".repeat(64);
    let n65 = "漢".repeat(65);
    assert_eq!(normalize_name(n1), Some(n1));
    assert_eq!(n64.len(), 192);
    assert_eq!(normalize_name(&n64), Some(n64.as_str()));
    assert_eq!(normalize_name(&n65), None);
}

#[test]
fn normalize_name_returns_the_same_slice() {
    let input = String::from("My_Project-01ゆめ");
    let out = normalize_name(&input).expect("應合格");
    assert!(std::ptr::eq(out, input.as_str()), "須回傳原字串切片");
}

#[test]
fn normalize_name_accepts_python_good_names() {
    for name in [
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
    ] {
        assert_eq!(normalize_name(name), Some(name), "{name:?}");
    }
}

#[test]
fn normalize_name_rejects_python_bad_names() {
    for name in [
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
    ] {
        assert_eq!(normalize_name(name), None, "{name:?}");
    }
}

// ---------- fold_name ----------

#[test]
fn fold_name_matches_python_examples() {
    // 事實 B 列的每個實例(Python str.casefold)。
    let cases: &[(&str, &str)] = &[
        ("\u{00DF}", "ss"),        // ß
        ("\u{1E9E}", "ss"),        // ẞ
        ("\u{212A}", "k"),         // KELVIN SIGN
        ("\u{017F}", "s"),         // ſ
        ("\u{FB00}", "ff"),        // ﬀ
        ("\u{FB06}", "st"),        // ﬆ
        ("\u{0130}", "i\u{0307}"), // İ
        ("\u{03A3}", "\u{03C3}"),  // Σ → σ
        ("\u{03C2}", "\u{03C3}"),  // ς → σ
        ("\u{AB70}", "\u{13A0}"),  // 切羅基文摺疊到大寫
        ("\u{FF21}", "\u{FF41}"),  // Ａ → ａ
        ("\u{0149}", "\u{02BC}n"), // ŉ
        ("STRASSE", "strasse"),
        ("stra\u{00DF}e", "strasse"),
        ("ABCxyz-_09", "abcxyz-_09"),
        ("漢字かなカナ", "漢字かなカナ"),
        ("", ""),
    ];
    for &(input, want) in cases {
        assert_eq!(fold_name(input), want, "{input:?}");
    }
}

#[test]
fn fold_name_non_ascii_folding_to_all_ascii_are_the_eleven() {
    // 事實 B:非 ASCII 而摺疊後全為 ASCII 的只有 11 個(U+00DF、U+017F、U+1E9E、U+212A、U+FB00~U+FB06)。
    let found: Vec<u32> = (0x80..=0x10FFFFu32)
        .filter_map(char::from_u32)
        .filter(|c| fold_name(&c.to_string()).is_ascii())
        .map(|c| c as u32)
        .collect();
    let mut want = vec![0x00DF, 0x017F, 0x1E9E, 0x212A];
    want.extend(0xFB00..=0xFB06);
    assert_eq!(found, want);
}

#[test]
fn fold_name_is_identity_on_whitelist_except_ascii_upper() {
    // 事實 B:白名單內的字元除 ASCII 大寫字母外,摺疊後都等於自己。
    for c in (0..=0x10FFFFu32)
        .filter_map(char::from_u32)
        .filter(|&c| name_char_ok(c))
    {
        let want = if c.is_ascii_uppercase() {
            c.to_ascii_lowercase().to_string()
        } else {
            c.to_string()
        };
        assert_eq!(fold_name(&c.to_string()), want, "U+{:04X}", c as u32);
    }
}

#[test]
fn fold_name_is_per_char_concatenation() {
    // 完整大小寫摺疊逐字進行、不依上下文(希臘文結尾 ς 不另處理)。
    let s = "ΣΑΣ ς Straße İ";
    let joined: String = s.chars().map(|c| fold_name(&c.to_string())).collect();
    assert_eq!(fold_name(s), joined);
    assert_eq!(fold_name("ΣΑΣ"), "σασ");
}

#[test]
fn fold_name_never_panics_on_any_scalar_value() {
    for c in (0..=0x10FFFFu32).filter_map(char::from_u32) {
        let _ = fold_name(&c.to_string());
    }
}
