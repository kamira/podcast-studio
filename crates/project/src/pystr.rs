//! Python 字串語意(CHG-20261128-01 代決 8)。
//!
//! - 長度與索引一律以 Unicode 碼位計([`char_len`];Python `len(str)`)。
//! - 空白字元集為 CPython 3.11 `str.isspace()` 為真的 29 個碼位([`py_isspace`];`str.strip()` 去除的字元相同),
//!   不是 Rust `char::is_whitespace`(後者不含 U+001C~U+001F,事實 D)。
//! - `project.py` 與 `breezy_worker.py` 的正規表示式以手寫比對重現,不引入 regex crate:
//!   `ID_RE`([`is_id`])、`KEY_RE`([`is_key`])、`breezy_worker.MARKED_RE`([`marked_to_digit`])。
//!   這些樣式只用明列的 ASCII 字元類與注音範圍,`re.ASCII` 不影響結果。

use crate::constants::{HANZI_RANGES, TONE_MARKS};

/// Python `str.isspace()`(CPython 3.11;單一字元)。
pub fn py_isspace(c: char) -> bool {
    matches!(
        u32::from(c),
        0x09..=0x0D
            | 0x1C..=0x20
            | 0x85
            | 0xA0
            | 0x1680
            | 0x2000..=0x200A
            | 0x2028
            | 0x2029
            | 0x202F
            | 0x205F
            | 0x3000
    )
}

/// Python `str.strip()`(無參數)。
pub fn py_strip(s: &str) -> &str {
    s.trim_matches(py_isspace)
}

/// Python `str.lstrip()`(無參數)。
pub fn py_lstrip(s: &str) -> &str {
    s.trim_start_matches(py_isspace)
}

/// Python `str.rstrip()`(無參數)。
pub fn py_rstrip(s: &str) -> &str {
    s.trim_end_matches(py_isspace)
}

/// Python `len(str)`:碼位數。
pub fn char_len(s: &str) -> usize {
    s.chars().count()
}

/// Python `project._is_hanzi`:`HANZI_RANGES` 內的碼位。
pub fn is_hanzi(c: char) -> bool {
    let cp = u32::from(c);
    HANZI_RANGES.iter().any(|&(lo, hi)| (lo..=hi).contains(&cp))
}

/// 注音符號 ㄅ(U+3105)~ㄩ(U+3129)。
pub fn is_zhuyin_letter(c: char) -> bool {
    ('\u{3105}'..='\u{3129}').contains(&c)
}

/// 聲調符號 ˊ ˇ ˋ ˙。
pub fn is_tone_mark(c: char) -> bool {
    TONE_MARKS.iter().any(|&(m, _)| m == c)
}

/// Python `project.ZHUYIN_CHARS` 的成員(注音符號或聲調符號)。
pub fn is_zhuyin_char(c: char) -> bool {
    is_zhuyin_letter(c) || is_tone_mark(c)
}

/// `breezy_worker.marked_to_digit`:聲調符號格式(ㄏㄠˇ、˙ㄉㄜ、ㄉㄜ˙、ㄐㄧㄣ)→ 數字聲調(ㄏㄠ3);不合法回 `None`。
///
/// 對應 `MARKED_RE = (˙?)([ㄅ-ㄩ]{1,4})([ˊˇˋ˙]?)` 的 `fullmatch`,前後都有輕聲記號時不合法。
pub fn marked_to_digit(zhuyin: &str) -> Option<String> {
    let chars: Vec<char> = zhuyin.chars().collect();
    let lead = chars.first() == Some(&'˙');
    let start = usize::from(lead);
    let body_len = chars[start..]
        .iter()
        .take_while(|&&c| is_zhuyin_letter(c))
        .count();
    if !(1..=4).contains(&body_len) {
        return None;
    }
    let after = start + body_len;
    let tail = match chars.get(after) {
        Some(&c) if is_tone_mark(c) => Some(c),
        _ => None,
    };
    if after + usize::from(tail.is_some()) != chars.len() || (lead && tail.is_some()) {
        return None;
    }
    let mark = if lead { Some('˙') } else { tail };
    let digit = match mark {
        None => '1',
        Some(m) => TONE_MARKS
            .iter()
            .find(|&&(t, _)| t == m)
            .map_or('1', |&(_, d)| d),
    };
    let mut out: String = chars[start..after].iter().collect();
    out.push(digit);
    Some(out)
}

fn all_lower_hex(s: &str) -> bool {
    s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// `ID_RE.fullmatch`:`[0-9a-f]{8,32}`。
pub fn is_id(s: &str) -> bool {
    (8..=32).contains(&s.len()) && all_lower_hex(s)
}

/// `KEY_RE.fullmatch`:`[0-9a-f]{64}`。
pub fn is_key(s: &str) -> bool {
    s.len() == 64 && all_lower_hex(s)
}
