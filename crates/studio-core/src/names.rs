//! 使用者命名規則(Python `names.py` 的 Rust 版;CHG-20261124-01 T2,代決 3、7)。
//!
//! 本模組目前只有與 Python 等價的相容部分:常數、[`name_char_ok`]、[`normalize_name`]、
//! [`fold_name`]。規則與 `names.py` 相同:
//!
//! - 名稱視為 Unicode 字串,不做正規化或其他編碼轉換;長度以 Unicode 純量值個數計,1~[`MAX_NAME`]。
//! - 逐字比對明確的中英日白名單([`NAME_ASCII`]、[`NAME_RANGES`]);不用 Unicode 類別。
//! - 只差大小寫的比對鍵為 [`fold_name`](Unicode 完整大小寫摺疊,等於 Python `str.casefold`)。
//!
//! 純函式:不讀寫檔案、不讀環境變數、不分平台。與 Python 的等價以
//! `crates/parity` 的比對單元 `names.*`(fixture 與全碼位比對)證明。

mod casefold_table;

/// 名稱長度上限(Unicode 純量值個數)。
pub const MAX_NAME: usize = 64;

/// 白名單中的 ASCII 字元(英文大小寫字母、數字、`-`、`_` 共 64 字),依碼位排序。
pub const NAME_ASCII: &str = "-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijklmnopqrstuvwxyz";

/// 白名單中的非 ASCII 碼位閉區間,順序與 Python `NAME_RANGES` 相同。
pub const NAME_RANGES: &[(u32, u32)] = &[
    (0x4E00, 0x9FFF), // CJK 統一漢字
    (0x3400, 0x4DBF), // CJK 統一漢字擴充 A
    (0x3041, 0x3096), // 平假名
    (0x30A1, 0x30FA), // 片假名(不含中點「・」U+30FB)
    (0x30FC, 0x30FC), // 長音符「ー」
    (0x3005, 0x3005), // 疊字符「々」
];

/// [`fold_name`] 所用摺疊表的 Unicode 版本(產生該表時 Python 的 `unicodedata.unidata_version`)。
pub const CASEFOLD_UNICODE_VERSION: &str = casefold_table::UNICODE_VERSION;

/// 單一字元是否在中英日白名單內。
pub fn name_char_ok(ch: char) -> bool {
    if ch.is_ascii() {
        return NAME_ASCII.contains(ch);
    }
    let cp = u32::from(ch);
    NAME_RANGES.iter().any(|&(lo, hi)| lo <= cp && cp <= hi)
}

/// 名稱檢查;合格回傳原字串切片(直接當檔名或目錄名),不合格回 `None`。
///
/// 這是與 Python `normalize_name` 等價的相容函式,用於既有名稱的讀取、列舉、載入與比對。
pub fn normalize_name(name: &str) -> Option<&str> {
    let len = name.chars().count();
    if !(1..=MAX_NAME).contains(&len) {
        return None;
    }
    name.chars().all(name_char_ok).then_some(name)
}

/// 比對「只差大小寫」用的鍵:Unicode 完整大小寫摺疊(等於 Python `str.casefold`)。
///
/// 逐字查表、不依上下文;不檢查輸入是否為合格名稱(呼叫端對任意目錄項名稱取鍵)。
pub fn fold_name(name: &str) -> String {
    let mut out = String::with_capacity(name.len());
    for ch in name.chars() {
        match fold_char(ch) {
            Some(folded) => out.push_str(folded),
            None => out.push(ch),
        }
    }
    out
}

/// 摺疊後與原字不同的字元回傳摺疊結果;其餘回 `None`。
fn fold_char(ch: char) -> Option<&'static str> {
    casefold_table::TABLE
        .binary_search_by_key(&ch, |&(key, _)| key)
        .ok()
        .map(|i| casefold_table::TABLE[i].1)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn casefold_table_is_strictly_sorted() {
        let table = casefold_table::TABLE;
        assert!(table.windows(2).all(|w| w[0].0 < w[1].0));
    }

    #[test]
    fn casefold_table_entries_change_the_char() {
        for &(key, folded) in casefold_table::TABLE {
            assert!(!folded.is_empty(), "U+{:04X}", u32::from(key));
            assert_ne!(folded, key.to_string(), "U+{:04X}", u32::from(key));
        }
    }

    #[test]
    fn casefold_table_size_matches_header() {
        assert_eq!(casefold_table::TABLE.len(), 1530);
    }
}
