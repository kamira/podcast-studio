//! 使用者命名規則(Python `names.py` 的 Rust 版;CHG-20261124-01 T2、T3,代決 3、7~11)。
//!
//! 與 Python 等價的相容部分:常數、[`name_char_ok`]、[`normalize_name`]、[`fold_name`]。
//! 規則與 `names.py` 相同:
//!
//! - 名稱視為 Unicode 字串,不做正規化或其他編碼轉換;長度以 Unicode 純量值個數計,1~[`MAX_NAME`]。
//! - 逐字比對明確的中英日白名單([`NAME_ASCII`]、[`NAME_RANGES`]);不用 Unicode 類別。
//! - 只差大小寫的比對鍵為 [`fold_name`](Unicode 完整大小寫摺疊,等於 Python `str.casefold`)。
//!
//! 刻意比 Python 嚴格的新名稱規則(T3,代決 8~10):[`normalize_new_name`] 在 [`normalize_name`]
//! 之上再拒絕 Windows 保留名稱([`is_windows_reserved`]),**三平台一致**、**只用於新名稱**
//! (建立專案、另存新工作流程、改名目標、複製或另存為);既有名稱的讀取、列舉、載入、清理與比對
//! 一律用相容的 [`normalize_name`],讓 Python 網頁版已建立的 `con` 這類項目照常可讀。
//!
//! 純函式:不讀寫檔案、不讀環境變數、不分平台。與 Python 的等價以
//! `crates/parity` 的比對單元 `names.*`(fixture 與全碼位比對)證明;刻意的差異不進比對 fixture,
//! 由 `crates/studio-core/tests/names_windows.rs` 鎖住、`crates/parity/tests/names_differences.rs`
//! 確認 Python 端確實接受(代決 11)。

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
/// 這是與 Python `normalize_name` 等價的相容函式,用於既有名稱的讀取、列舉、載入與比對;
/// 新名稱改用 [`normalize_new_name`]。
pub fn normalize_name(name: &str) -> Option<&str> {
    let len = name.chars().count();
    if !(1..=MAX_NAME).contains(&len) {
        return None;
    }
    name.chars().all(name_char_ok).then_some(name)
}

/// Windows 保留裝置名(代決 10,確認閘 2026-11-25 採 (a)):`CON`、`PRN`、`AUX`、`NUL`、
/// `COM0`~`COM9`、`LPT0`~`LPT9`,共 24 個。上標數字形式與帶 `$` 的裝置名已被白名單拒絕,不列入(W-4)。
const WINDOWS_RESERVED: [&str; 24] = [
    "CON", "PRN", "AUX", "NUL", "COM0", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7",
    "COM8", "COM9", "LPT0", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
];

/// 新名稱不合格的原因([`normalize_new_name`] 的錯誤)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NewNameError {
    /// 不合白名單或長度([`normalize_name`] 回 `None`)。
    Invalid,
    /// 合白名單,但整個名稱是 Windows 保留裝置名([`is_windows_reserved`])。
    Reserved,
}

/// 整個名稱是否等於 Windows 保留裝置名(ASCII 不分大小寫;24 個名稱共 192 種寫法)。
///
/// 只比對整個名稱:`CON.txt`、`CONX`、`COM10` 都不算(前兩者或被白名單拒絕、或不是保留名稱)。
pub fn is_windows_reserved(name: &str) -> bool {
    WINDOWS_RESERVED
        .iter()
        .any(|reserved| reserved.eq_ignore_ascii_case(name))
}

/// 新名稱檢查:先套用相容的 [`normalize_name`],再拒絕 Windows 保留名稱;合格回傳原字串切片。
///
/// 用於會產生新目錄名或檔名的路徑(建立專案、另存新工作流程、改名目標、複製或另存為)。
/// 三平台一致(代決 8),只在相容函式之上再拒絕,不接受相容函式拒絕的名稱。
/// 與 Python `normalize_name` 刻意不同:Python 接受 `con`、`COM1` 等(本 CHG 行為差異 D-1~D-3)。
pub fn normalize_new_name(name: &str) -> Result<&str, NewNameError> {
    let name = normalize_name(name).ok_or(NewNameError::Invalid)?;
    if is_windows_reserved(name) {
        return Err(NewNameError::Reserved);
    }
    Ok(name)
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
