//! L1:十進位四捨五入、單值檢查、Python 字串語意與常數(CHG-20261128-01 T1,代決 7~9)。
//!
//! 預期值是 CPython 3.11.18 對 repo 內 `project.py` 的實際結果(施工者開發機以 `-I -B` 唯讀執行後貼進本檔;
//! 錯誤訊息逐字)。Python 會丟出非 `ProjectError` 例外的輸入(`decimal.InvalidOperation`)不在 fixture,
//! 以本檔 `huge_values_that_crash_python_are_range_errors_in_rust` 鎖住 Rust 的行為(行為差異 D-5,T1 補記)。

use project::checks::{
    check_name, check_percent, check_pitch_points, check_zhuyin, is_number, normalize_pause,
    normalize_speed,
};
use project::constants;
use project::pystr::{
    char_len, is_hanzi, is_id, is_key, marked_to_digit, py_isspace, py_lstrip, py_rstrip, py_strip,
};
use project::{Error, ErrorKind};
use serde_json::{Value, json};

const SPEED_RANGE: &str = "語速必須在 0.5~2.0 倍之間";
const PAUSE_RANGE: &str = "停頓必須在 0~5 秒之間";
const ZHUYIN_MSG: &str = "注音標註只接受注音符號與聲調符號(ˊˇˋ˙),長度 1 到 5";
const PITCH_WHERE: &str = "segments[0].post.pitch_points";

fn messages(e: &Error) -> Vec<&str> {
    e.errors().iter().map(String::as_str).collect()
}

fn speed(v: Value) -> Result<f64, Vec<String>> {
    normalize_speed(&v, "語速").map_err(|e| e.errors().to_vec())
}

fn pause(v: Value) -> Result<f64, Vec<String>> {
    normalize_pause(&v, "停頓").map_err(|e| e.errors().to_vec())
}

fn err(msg: &str) -> Result<f64, Vec<String>> {
    Err(vec![msg.to_string()])
}

/// 比較浮點時連正負號一起比(-0.0 與 0.0 不同)。
fn same(a: Result<f64, Vec<String>>, b: Result<f64, Vec<String>>) -> bool {
    match (a, b) {
        (Ok(x), Ok(y)) => x.to_bits() == y.to_bits(),
        (a, b) => a == b,
    }
}

#[test]
fn normalize_speed_rounding_boundaries_match_python() {
    for (v, want) in [
        (json!(0.475), Ok(0.5)),
        (json!(0.474), err(SPEED_RANGE)),
        (json!(0.4749999999999999), err(SPEED_RANGE)),
        (json!(2.024), Ok(2.0)),
        (json!(2.025), err(SPEED_RANGE)),
        (json!(1.025), Ok(1.05)),
        (json!(1.075), Ok(1.1)),
        (json!(1.125), Ok(1.15)),
        (json!(1.175), Ok(1.2)),
        (json!(0.525), Ok(0.55)),
        (json!(1.975), Ok(2.0)),
        (json!(1.9749999999999999), Ok(1.95)),
        (json!(0.30000000000000004), err(SPEED_RANGE)),
        (json!(1), Ok(1.0)),
        (json!(2), Ok(2.0)),
        (json!(0), err(SPEED_RANGE)),
        (json!(-1), err(SPEED_RANGE)),
        (json!(1.0000000000000002), Ok(1.0)),
        (json!(0.9999999999999999), Ok(1.0)),
        (json!(3), err(SPEED_RANGE)),
        (json!(1e-300), err(SPEED_RANGE)),
        (json!(4.9e26), err(SPEED_RANGE)),
        (json!(10_000_000_000_000_000_000u64), err(SPEED_RANGE)),
        (json!(i64::MIN), err(SPEED_RANGE)),
        (json!(1.15), Ok(1.15)),
    ] {
        let shown = v.to_string();
        let got = speed(v);
        assert!(
            same(got.clone(), want.clone()),
            "{shown}:預期 {want:?},得到 {got:?}"
        );
    }
}

#[test]
fn normalize_speed_rejects_non_numbers() {
    for v in [
        json!(true),
        json!("1"),
        json!(null),
        json!([1]),
        json!({"a": 1}),
    ] {
        assert_eq!(speed(v.clone()), err("語速必須是數字"), "{v}");
    }
}

#[test]
fn normalize_pause_rounding_boundaries_match_python() {
    for (v, want) in [
        (json!(0), Ok(0.0)),
        (json!(0.0), Ok(0.0)),
        (json!(-0.0), Ok(-0.0)),
        (json!(-0.01), Ok(-0.0)),
        (json!(-0.04), Ok(-0.0)),
        (json!(-0.05), err(PAUSE_RANGE)),
        (json!(0.05), Ok(0.1)),
        (json!(0.04), Ok(0.0)),
        (json!(0.15), Ok(0.2)),
        (json!(0.25), Ok(0.3)),
        (json!(0.35), Ok(0.4)),
        (json!(0.45), Ok(0.5)),
        (json!(2.55), Ok(2.6)),
        (json!(4.95), Ok(5.0)),
        (json!(5), Ok(5.0)),
        (json!(5.04), Ok(5.0)),
        (json!(5.05), err(PAUSE_RANGE)),
        (json!(1e-300), Ok(0.0)),
        (json!(9.9e26), err(PAUSE_RANGE)),
        (json!(0.7999999999999999), Ok(0.8)),
        (json!(3.3333333333333335), Ok(3.3)),
        (json!(0.30000000000000004), Ok(0.3)),
    ] {
        let shown = v.to_string();
        let got = pause(v);
        assert!(
            same(got.clone(), want.clone()),
            "{shown}:預期 {want:?},得到 {got:?}"
        );
    }
}

#[test]
fn labels_are_prefixed_verbatim() {
    let e = normalize_pause(&json!("x"), "defaults.pause_comma").unwrap_err();
    assert_eq!(messages(&e), ["defaults.pause_comma必須是數字"]);
    let e = normalize_speed(&json!(9), "segments[3].speed").unwrap_err();
    assert_eq!(messages(&e), ["segments[3].speed必須在 0.5~2.0 倍之間"]);
    assert_eq!(e.kind(), ErrorKind::Invalid);
    assert_eq!(e.class(), "project_error");
}

#[test]
fn huge_values_that_crash_python_are_range_errors_in_rust() {
    // Python:四捨五入後的整數超過 28 位數時 Decimal.quantize 丟 InvalidOperation(未分類例外);
    // Rust 回該欄位的範圍錯誤(行為差異 D-5,T1 補記)。語速 5e26 / 0.05 = 1e28(29 位)即超過。
    for v in [json!(5e26), json!(1e27), json!(1e300), json!(-1e300)] {
        assert_eq!(speed(v.clone()), err(SPEED_RANGE), "{v}");
    }
    // 停頓 5e26 / 0.1 = 5e27(28 位)Python 仍是範圍錯誤;1e27 起才丟例外
    for v in [json!(5e26), json!(1e27), json!(1e300)] {
        assert_eq!(pause(v.clone()), err(PAUSE_RANGE), "{v}");
    }
}

#[test]
fn is_number_follows_python_is_number() {
    assert!(is_number(&json!(0)));
    assert!(is_number(&json!(-1.5)));
    assert!(is_number(&json!(u64::MAX)));
    for v in [
        json!(true),
        json!(false),
        json!("1"),
        json!(null),
        json!([]),
        json!({}),
    ] {
        assert!(!is_number(&v), "{v}");
    }
}

#[test]
fn check_zhuyin_matches_python() {
    for (z, ok) in [
        ("ㄏㄠˇ", true),
        ("˙ㄉㄜ", true),
        ("ㄉㄜ˙", true),
        ("ㄐㄧㄣ", true),
        ("ㄅㄆㄇㄈˋ", true),
        ("˙ㄅㄆㄇㄈ", true),
        ("ㄅ", true),
        ("ㄩ", true),
        ("ㄅㄆㄇㄈㄉ", false),
        ("˙ㄅㄆㄇㄈˋ", false),
        ("", false),
        ("ˇ", false),
        ("˙", false),
        ("˙ㄉㄜ˙", false),
        ("ˊㄏㄠ", false),
        ("ㄏˇㄠ", false),
        ("ㄏㄠ3", false),
        ("ㄏㄠˇˇ", false),
        ("\u{3104}", false),
        ("\u{312a}", false),
        ("ㄏㄠ ", false),
    ] {
        let got = check_zhuyin(&json!(z));
        if ok {
            assert_eq!(got.ok().as_deref(), Some(z), "{z}");
        } else {
            let e = got.expect_err(z);
            assert_eq!(messages(&e), [ZHUYIN_MSG], "{z}");
        }
    }
    for v in [json!(5), json!(null), json!(["ㄏ"]), json!(true)] {
        let e = check_zhuyin(&v).expect_err("非字串");
        assert_eq!(messages(&e), [ZHUYIN_MSG], "{v}");
    }
}

#[test]
fn marked_to_digit_matches_breezy_worker() {
    assert_eq!(marked_to_digit("ㄏㄠˇ").as_deref(), Some("ㄏㄠ3"));
    assert_eq!(marked_to_digit("ㄏㄠˊ").as_deref(), Some("ㄏㄠ2"));
    assert_eq!(marked_to_digit("ㄏㄠˋ").as_deref(), Some("ㄏㄠ4"));
    assert_eq!(marked_to_digit("˙ㄉㄜ").as_deref(), Some("ㄉㄜ5"));
    assert_eq!(marked_to_digit("ㄉㄜ˙").as_deref(), Some("ㄉㄜ5"));
    assert_eq!(marked_to_digit("ㄐㄧㄣ").as_deref(), Some("ㄐㄧㄣ1"));
    assert_eq!(marked_to_digit("ㄅㄆㄇㄈ").as_deref(), Some("ㄅㄆㄇㄈ1"));
    for z in ["", "ˇ", "˙ㄉㄜ˙", "ㄅㄆㄇㄈㄉ", "ㄏˇㄠ", "ㄏㄠˇˇ", "abc"] {
        assert_eq!(marked_to_digit(z), None, "{z}");
    }
}

#[test]
fn check_percent_matches_python() {
    let w = "segments[0].connect";
    let msg = "segments[0].connect必須是 0 到 100 的整數(百分比)";
    assert_eq!(check_percent(&json!(null), w).unwrap(), None);
    assert_eq!(check_percent(&json!(0), w).unwrap(), Some(0));
    assert_eq!(check_percent(&json!(100), w).unwrap(), Some(100));
    for v in [
        json!(101),
        json!(-1),
        json!(50.0),
        json!(true),
        json!("5"),
        json!(-0.0),
    ] {
        let e = check_percent(&v, w).expect_err("應拒絕");
        assert_eq!(messages(&e), [msg], "{v}");
    }
}

fn pitch(points: Value) -> Result<Vec<[f64; 2]>, Vec<String>> {
    check_pitch_points(&points, PITCH_WHERE).map_err(|e| e.errors().to_vec())
}

#[test]
fn check_pitch_points_matches_python() {
    assert_eq!(
        pitch(json!([[0, 0], [1, 0]])),
        Ok(vec![[0.0, 0.0], [1.0, 0.0]])
    );
    assert_eq!(
        pitch(json!([[0, -7.2], [1, 7.2]])),
        Ok(vec![[0.0, -7.2], [1.0, 7.2]])
    );
    let got = pitch(json!([[0, -0.0], [1, 7]])).unwrap();
    assert_eq!(got[0][1].to_bits(), (-0.0f64).to_bits(), "-0.0 須保留");
    assert_eq!(got[1], [1.0, 7.0]);
    let count = "segments[0].post.pitch_points 必須是 2 到 30 個語調點";
    let shape = "segments[0].post.pitch_points 每個語調點必須是 [位置, 半音] 兩個數字";
    for (points, msg) in [
        (
            json!([[0, -7.21], [1, 0]]),
            "segments[0].post.pitch_points 的音高必須在 −7.2 到 +7.2 半音之間",
        ),
        (json!([[0, 0]]), count),
        (
            json!([[0, 0], [0, 0]]),
            "segments[0].post.pitch_points 的位置必須嚴格遞增",
        ),
        (json!([[0, 0], [1, "1"]]), shape),
        (json!([[0, true], [1, 0]]), shape),
        (json!([[0, 0], [1]]), shape),
        (json!([[0, 0], {"pos": 1}]), shape),
        (
            json!([[-0.0001, 0], [1, 0]]),
            "segments[0].post.pitch_points 的位置必須在 0 到 1 之間",
        ),
        (json!("x"), count),
        (json!(null), count),
    ] {
        assert_eq!(
            pitch(points.clone()),
            Err(vec![msg.to_string()]),
            "{points}"
        );
    }
    let thirty: Vec<Value> = (0..30).map(|i| json!([f64::from(i) / 29.0, 0])).collect();
    assert_eq!(pitch(Value::Array(thirty)).map(|v| v.len()), Ok(30));
    let thirty_one: Vec<Value> = (0..31).map(|i| json!([f64::from(i) / 30.0, 0])).collect();
    assert_eq!(
        pitch(Value::Array(thirty_one)),
        Err(vec![count.to_string()])
    );
}

#[test]
fn check_name_uses_names_rules() {
    let e = check_name("a.b").unwrap_err();
    assert_eq!(
        messages(&e),
        ["專案名稱只能用中文、日文假名、英文字母、數字、底線、連字號,長度 1 到 64 字"]
    );
    // 既有名稱沿用 normalize_name:保留名稱照常合格(建立新名稱另以 normalize_new_name 檢查,T6)
    assert_eq!(check_name("CON").unwrap(), "CON");
}

/// CPython 3.11.18 `str.isspace()` 為真的全部碼位(`str.strip()` 去除的字元相同)。
const PY_WHITESPACE: [u32; 29] = [
    0x9, 0xa, 0xb, 0xc, 0xd, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680, 0x2000, 0x2001,
    0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f,
    0x205f, 0x3000,
];

#[test]
fn python_whitespace_set_is_exact() {
    let got: Vec<u32> = (0..=0x10FFFFu32)
        .filter_map(char::from_u32)
        .filter(|&c| py_isspace(c))
        .map(u32::from)
        .collect();
    assert_eq!(got, PY_WHITESPACE);
    // Rust 的 char::is_whitespace 不含 U+001C~U+001F(事實 D)
    assert!(!'\u{1c}'.is_whitespace() && py_isspace('\u{1c}'));
}

#[test]
fn strip_uses_python_whitespace() {
    assert_eq!(py_strip("\u{1c} 中文\u{3000}\n"), "中文");
    assert_eq!(py_lstrip("\u{85}\u{a0}a b "), "a b ");
    assert_eq!(py_rstrip(" a b\u{2028}\u{205f}"), " a b");
    assert_eq!(
        py_strip("\u{200b}x\u{200b}"),
        "\u{200b}x\u{200b}",
        "U+200B 不是空白"
    );
    assert_eq!(py_strip(" \t\r\n"), "");
}

#[test]
fn lengths_are_counted_in_code_points() {
    assert_eq!(char_len(""), 0);
    assert_eq!(char_len("中文"), 2);
    assert_eq!(char_len("😀a"), 2);
    assert_eq!(char_len("e\u{301}"), 2);
}

#[test]
fn hanzi_ranges_match_python() {
    for (cp, want) in [
        (0x4E00, true),
        (0x9FFF, true),
        (0x4DFF, false),
        (0x3400, true),
        (0x4DBF, true),
        (0x33FF, false),
        (0xF900, true),
        (0xFAFF, true),
        (0xFB00, false),
        (0x20000, true),
        (0x323AF, true),
        (0x323B0, false),
        (0x3105, false),
        (u32::from('a'), false),
    ] {
        assert_eq!(is_hanzi(char::from_u32(cp).unwrap()), want, "{cp:#x}");
    }
    assert_eq!(constants::HANZI_RANGES.len(), 4);
}

#[test]
fn id_and_key_patterns() {
    assert!(is_id("0123abcd"));
    assert!(is_id(&"f".repeat(32)));
    assert!(!is_id("0123abc"));
    assert!(!is_id(&"f".repeat(33)));
    assert!(!is_id("0123ABCD"));
    assert!(!is_id("0123abcg"));
    assert!(!is_id("0123abc\u{ff11}"));
    assert!(is_key(&"0a".repeat(32)));
    assert!(!is_key(&"0a".repeat(31)));
    assert!(!is_key(&format!("{}\n", "a".repeat(64))));
}

#[test]
fn error_display_joins_with_semicolon() {
    let e = Error::invalid_all(vec!["甲".to_string(), "乙".to_string()]);
    assert_eq!(e.to_string(), "甲;乙");
    assert_eq!(e.kind(), ErrorKind::Invalid);
    for (kind, class) in [
        (ErrorKind::Invalid, "project_error"),
        (ErrorKind::UnsafePath, "unsafe_path"),
        (ErrorKind::NotFound, "not_found"),
        (ErrorKind::Exists, "exists"),
        (ErrorKind::Quota, "quota"),
    ] {
        assert_eq!(Error::new(kind, vec!["x".to_string()]).class(), class);
    }
}

#[test]
fn constants_match_project_py() {
    assert_eq!(constants::SCHEMA_VERSION, 2);
    assert_eq!(constants::OLD_SCHEMA_VERSIONS, [1]);
    assert_eq!(constants::HISTORY_KEEP, 10);
    assert_eq!(constants::TRASH_DAYS, 30);
    assert_eq!(constants::MAX_PROJECT_BYTES, 5 * 1024 * 1024);
    assert_eq!(constants::MAX_ASSET_BYTES, 2 * 1024 * 1024 * 1024);
    assert_eq!(
        constants::DEFAULTS,
        [
            ("speed", 1.15),
            ("pause_comma", 0.0),
            ("pause_sentence", 0.4),
            ("pause_paragraph", 0.8)
        ]
    );
    assert_eq!(constants::INNER_SEMI_LIMIT, 7.2);
    assert_eq!(constants::ZHUYIN_CHARS.chars().count(), 37 + 4);
}
