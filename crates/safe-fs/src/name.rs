//! 名稱檢查(CHG-20261125-01 代決 8、9)。
//!
//! - [`check_name`]:三平台與 Python `_check_name` 相同(空字串、`.`、`..`、含 `/` 或 NUL 不合法);
//!   Windows 另拒絕 `\`、`:`、`<>"|?*`、U+0001~U+001F 控制字元、結尾句點或空白(D-4)——
//!   這些在 Win32 路徑規則下會改變名稱的意義(拆成多層、開到替代資料流、被去掉結尾)。
//! - [`reserved_device`]:Windows 保留裝置名。名稱檢查**不**擋保留名稱(代決 8);開啟或建立時
//!   cap-std 一律拒絕它們(回「找不到」),本 crate 事先辨認並改回 `ReservedDevice`,明確失敗、
//!   不開任何東西(代決 9、D-5)。

use std::ffi::OsStr;

use crate::error::{Error, ErrorKind, Result};

/// 名稱必須是單一路徑元件;不合法回 `InvalidName`。任何輸入(含非 UTF-8)都不會 panic。
pub fn check_name(name: &OsStr) -> Result<()> {
    if name.is_empty() || name == "." || name == ".." || !platform_ok(name) {
        return Err(Error::new(ErrorKind::InvalidName));
    }
    Ok(())
}

#[cfg(unix)]
fn platform_ok(name: &OsStr) -> bool {
    use std::os::unix::ffi::OsStrExt;
    !name.as_bytes().iter().any(|&b| b == b'/' || b == 0)
}

#[cfg(windows)]
fn platform_ok(name: &OsStr) -> bool {
    use std::os::windows::ffi::OsStrExt;
    let units: Vec<u16> = name.encode_wide().collect();
    let bad = |u: u16| {
        u < 0x20
            || matches!(
                char::from_u32(u32::from(u)),
                Some('/' | '\\' | ':' | '<' | '>' | '"' | '|' | '?' | '*')
            )
    };
    if units.iter().any(|&u| bad(u)) {
        return false;
    }
    !matches!(units.last(), Some(&u) if u == u16::from(b'.') || u == u16::from(b' '))
}

#[cfg(not(any(unix, windows)))]
fn platform_ok(name: &OsStr) -> bool {
    name.to_str().is_some_and(|s| !s.contains(['/', '\0']))
}

/// cap-std(`cap-primitives` 4.0.3 `windows/fs/open_impl.rs`)拒絕開啟的保留裝置名:
/// 取名稱第一個句點(自第 2 個位元組起找)之前的部分,去掉結尾空白、轉大寫後與下列名稱相同者。
#[cfg(windows)]
const RESERVED: &[&str] = &[
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM0",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "COM\u{b9}",
    "COM\u{b2}",
    "COM\u{b3}",
    "LPT0",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
    "LPT\u{b9}",
    "LPT\u{b2}",
    "LPT\u{b3}",
];

/// Windows 上名稱是否為 cap-std 會拒絕開啟的保留裝置名(例 `con`、`CON.txt`、`Com1`、`LPT¹`);
/// 其他平台一律 `false`。規則與 cap-std 相同,使「cap-std 拒絕」與「本 crate 回 `ReservedDevice`」完全對應。
pub fn reserved_device(name: &OsStr) -> bool {
    #[cfg(windows)]
    {
        let Some(s) = name.to_str() else {
            return false;
        };
        let bytes = s.as_bytes();
        let stem = match bytes
            .get(1..)
            .and_then(|rest| rest.iter().position(|&b| b == b'.'))
        {
            Some(i) if s != ".." => &s[..=i],
            _ => s,
        };
        let upper = stem.trim_end().to_uppercase();
        RESERVED.contains(&upper.as_str())
    }
    #[cfg(not(windows))]
    {
        let _ = name;
        false
    }
}

/// 名稱合格且(Windows)不是保留裝置名。
pub(crate) fn check_openable(name: &OsStr) -> Result<()> {
    check_name(name)?;
    if reserved_device(name) {
        return Err(Error::new(ErrorKind::ReservedDevice));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reserved_device_follows_cap_std_rule() {
        let hit = [
            "con",
            "CON",
            "Con",
            "con.txt",
            "CON.TXT.bak",
            "nul",
            "COM1",
            "com\u{b9}",
            "lpt0",
            "AUX.",
            "prn",
        ];
        let miss = [
            "CONIN$", "CONOUT$", "CLOCK$", "console", "com10", "xcon", ".con", "con_x", "漢字",
        ];
        for n in hit {
            assert_eq!(reserved_device(OsStr::new(n)), cfg!(windows), "{n}");
        }
        for n in miss {
            assert!(!reserved_device(OsStr::new(n)), "{n}");
        }
    }

    /// K-4:以 UTF-16 單位判定,非 UTF-8 名稱的主檔名是保留名稱時同樣擋下;不會 panic。
    #[cfg(windows)]
    #[test]
    fn reserved_device_works_on_utf16_units() {
        use std::ffi::OsString;
        use std::os::windows::ffi::OsStringExt;
        let w = |s: &str, tail: &[u16]| {
            let mut v: Vec<u16> = s.encode_utf16().collect();
            v.extend_from_slice(tail);
            OsString::from_wide(&v)
        };
        // 主檔名是保留名稱、副檔名含未配對代理單位:擋
        for n in [
            w("con.", &[0xD800]),
            w("NUL.", &[0xDC00, 0x61]),
            w("com1.", &[0xDFFF]),
            w("LPT\u{b9}.x", &[0xD800]),
            w("aux .", &[0xD83D]),
        ] {
            assert!(n.to_str().is_none());
            assert!(reserved_device(&n), "{n:?}");
        }
        // 主檔名本身含未配對代理單位:不是保留名稱
        for n in [
            w("con", &[0xD800]),
            w("", &[0xD800, 0x63, 0x6f, 0x6e]),
            w("co", &[0xDC00, 0x6e]),
            OsString::from_wide(&[0xD800]),
            OsString::from_wide(&[0x2e, 0xD800]),
        ] {
            assert!(!reserved_device(&n), "{n:?}");
        }
    }

    #[test]
    fn stem_is_found_after_the_first_byte() {
        // `.con` 的第一個位元組是句點,不當成分隔:整個名稱 `.con` 不是保留名稱
        assert!(!reserved_device(OsStr::new(".con")));
        assert!(!reserved_device(OsStr::new("..")));
    }
}
