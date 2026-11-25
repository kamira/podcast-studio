//! 錯誤型別(CHG-20261125-01 代決 6、10)。
//!
//! 類別與 Python `safe_fs` 的例外一一對應;`Display` 訊息**不含任何本機路徑**
//! (沿用 `safe_fs.py` 第 12 行的約定,安全影響 (g))。
//!
//! | Python | [`ErrorKind`] |
//! |--------|---------------|
//! | `UnsafePathError` | `Unsafe` |
//! | `ValueError` | `InvalidName` |
//! | (無;只在 Windows) | `ReservedDevice` |
//! | `FileNotFoundError` | `NotFound` |
//! | `FileExistsError` | `AlreadyExists` |
//! | `NotADirectoryError` | `NotADirectory` |
//! | `IsADirectoryError` | `IsADirectory` |
//! | `PermissionError` | `PermissionDenied` |
//! | 其他 `OSError` | `Other(code)` |

use std::fmt;
use std::io;

/// 錯誤類別。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorKind {
    /// 名稱不是合法的單一路徑元件(Python `ValueError`)。
    InvalidName,
    /// 某一層是連結(Windows:帶名稱代理位元的重新解析點)、中間層不是目錄,或目標不是一般檔
    /// (Python `UnsafePathError`)。
    Unsafe,
    /// Windows 保留裝置名:實作無法以 handle 相對開啟真正的目錄項,明確拒絕,絕不開到裝置(代決 9)。
    ReservedDevice,
    NotFound,
    AlreadyExists,
    NotADirectory,
    IsADirectory,
    PermissionDenied,
    /// 其他作業系統錯誤;值為原始錯誤碼(Unix 為 errno、Windows 為 Win32 錯誤碼),沒有時為 `None`。
    Other(Option<i32>),
}

/// `safe-fs` 的錯誤。
#[derive(Debug)]
pub struct Error {
    kind: ErrorKind,
    source: Option<io::Error>,
}

/// `safe-fs` 的結果型別。
pub type Result<T> = std::result::Result<T, Error>;

impl Error {
    /// 只有類別、沒有底層作業系統錯誤的錯誤。
    pub(crate) fn new(kind: ErrorKind) -> Self {
        Self { kind, source: None }
    }

    /// 帶底層作業系統錯誤的錯誤。
    pub(crate) fn with_source(kind: ErrorKind, source: io::Error) -> Self {
        Self {
            kind,
            source: Some(source),
        }
    }

    /// 錯誤類別。
    pub fn kind(&self) -> ErrorKind {
        self.kind
    }

    /// 底層作業系統錯誤碼(若有)。
    pub fn raw_os_error(&self) -> Option<i32> {
        self.source.as_ref().and_then(io::Error::raw_os_error)
    }
}

impl fmt::Display for Error {
    /// 只寫類別說明與錯誤碼,不寫任何路徑或名稱以外的本機資訊。
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self.kind {
            ErrorKind::InvalidName => f.write_str("路徑元件不合法"),
            ErrorKind::Unsafe => {
                f.write_str("路徑中有連結、不是預期的目錄,或目標不是一般檔案,不接受")
            }
            ErrorKind::ReservedDevice => f.write_str("名稱是 Windows 保留裝置名,無法安全開啟"),
            ErrorKind::NotFound => f.write_str("項目不存在"),
            ErrorKind::AlreadyExists => f.write_str("項目已存在"),
            ErrorKind::NotADirectory => f.write_str("不是目錄"),
            ErrorKind::IsADirectory => f.write_str("是目錄"),
            ErrorKind::PermissionDenied => f.write_str("權限不足"),
            ErrorKind::Other(Some(code)) => write!(f, "作業系統錯誤(錯誤碼 {code})"),
            ErrorKind::Other(None) => f.write_str("作業系統錯誤"),
        }
    }
}

impl std::error::Error for Error {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        self.source
            .as_ref()
            .map(|e| e as &(dyn std::error::Error + 'static))
    }
}

/// 依原始錯誤碼決定類別(Unix:PEP 3151 的同一組 errno 對應,不依 `std::io::ErrorKind`;代決 10)。
#[cfg(unix)]
pub(crate) fn classify(e: &io::Error) -> ErrorKind {
    match e.raw_os_error() {
        Some(code) => match code {
            errno::ENOENT => ErrorKind::NotFound,
            errno::EEXIST => ErrorKind::AlreadyExists,
            errno::ENOTDIR => ErrorKind::NotADirectory,
            errno::EISDIR => ErrorKind::IsADirectory,
            errno::EACCES | errno::EPERM => ErrorKind::PermissionDenied,
            other => ErrorKind::Other(Some(other)),
        },
        None => classify_by_kind(e),
    }
}

/// 依錯誤類別決定(Windows:Win32 錯誤碼經標準函式庫對應;平台差異見 D-7)。
#[cfg(not(unix))]
pub(crate) fn classify(e: &io::Error) -> ErrorKind {
    match classify_by_kind(e) {
        ErrorKind::Other(_) => ErrorKind::Other(e.raw_os_error()),
        k => k,
    }
}

fn classify_by_kind(e: &io::Error) -> ErrorKind {
    match e.kind() {
        io::ErrorKind::NotFound => ErrorKind::NotFound,
        io::ErrorKind::AlreadyExists => ErrorKind::AlreadyExists,
        io::ErrorKind::NotADirectory => ErrorKind::NotADirectory,
        io::ErrorKind::IsADirectory => ErrorKind::IsADirectory,
        io::ErrorKind::PermissionDenied => ErrorKind::PermissionDenied,
        _ => ErrorKind::Other(e.raw_os_error()),
    }
}

/// 把作業系統錯誤轉成 [`Error`](類別依 [`classify`])。
pub(crate) fn from_io(e: io::Error) -> Error {
    let kind = classify(&e);
    Error::with_source(kind, e)
}

/// errno 名稱(比對輸出 `error os_<名稱>` 用;Python 端為 `errno.errorcode`)。
/// 只列 `safe_fs` 操作可能遇到的值;查不到回 `None`。
pub fn errno_name(code: i32) -> Option<&'static str> {
    errno::NAMES
        .iter()
        .find(|(c, _)| *c == code)
        .map(|(_, n)| *n)
}

/// errno 數值。Linux 與 macOS 的前 34 個相同,其後不同,依平台分列。
#[allow(dead_code)]
mod errno {
    pub const EPERM: i32 = 1;
    pub const ENOENT: i32 = 2;
    pub const EIO: i32 = 5;
    pub const EBADF: i32 = 9;
    pub const EACCES: i32 = 13;
    pub const EBUSY: i32 = 16;
    pub const EEXIST: i32 = 17;
    pub const EXDEV: i32 = 18;
    pub const ENOTDIR: i32 = 20;
    pub const EISDIR: i32 = 21;
    pub const EINVAL: i32 = 22;
    pub const ETXTBSY: i32 = 26;
    pub const ENOSPC: i32 = 28;
    pub const EROFS: i32 = 30;
    pub const EMLINK: i32 = 31;

    #[cfg(any(target_os = "macos", target_os = "ios"))]
    mod platform {
        pub const ELOOP: i32 = 62;
        pub const ENAMETOOLONG: i32 = 63;
        pub const ENOTEMPTY: i32 = 66;
        pub const EDQUOT: i32 = 69;
    }
    #[cfg(not(any(target_os = "macos", target_os = "ios")))]
    mod platform {
        pub const ELOOP: i32 = 40;
        pub const ENAMETOOLONG: i32 = 36;
        pub const ENOTEMPTY: i32 = 39;
        pub const EDQUOT: i32 = 122;
    }
    pub use platform::*;

    pub const NAMES: &[(i32, &str)] = &[
        (EPERM, "EPERM"),
        (ENOENT, "ENOENT"),
        (EIO, "EIO"),
        (EBADF, "EBADF"),
        (EACCES, "EACCES"),
        (EBUSY, "EBUSY"),
        (EEXIST, "EEXIST"),
        (EXDEV, "EXDEV"),
        (ENOTDIR, "ENOTDIR"),
        (EISDIR, "EISDIR"),
        (EINVAL, "EINVAL"),
        (ETXTBSY, "ETXTBSY"),
        (ENOSPC, "ENOSPC"),
        (EROFS, "EROFS"),
        (EMLINK, "EMLINK"),
        (ELOOP, "ELOOP"),
        (ENAMETOOLONG, "ENAMETOOLONG"),
        (ENOTEMPTY, "ENOTEMPTY"),
        (EDQUOT, "EDQUOT"),
    ];
}

#[cfg(unix)]
pub(crate) use errno::{ELOOP, EMLINK, ENOTDIR as ERRNO_ENOTDIR};
