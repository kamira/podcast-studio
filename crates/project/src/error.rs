//! 錯誤型別(CHG-20261128-01 代決 9)。
//!
//! [`Error`] 帶類別與**錯誤訊息清單**(繁體中文,逐字與 Python 相同、含累積順序);`Display` 以「;」串接,
//! 與 Python `ProjectError.__str__` 相同。訊息不含本機路徑(Python 相同)。
//!
//! | Python | [`ErrorKind`] | [`Error::class`] |
//! |--------|---------------|------------------|
//! | `ProjectError` | `Invalid` | `project_error` |
//! | `UnsafePath` | `UnsafePath` | `unsafe_path` |
//! | `ProjectNotFound` | `NotFound` | `not_found` |
//! | `ProjectExists` | `Exists` | `exists` |
//! | `QuotaExceeded` | `Quota` | `quota` |
//! | 直接丟出的 `OSError` | `Fs(safe_fs::ErrorKind)` | `os_error` |

use std::fmt;

/// 錯誤類別。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorKind {
    /// 專案資料不合法或操作被拒絕(Python `ProjectError`)。
    Invalid,
    /// 專案資料目錄是連結或解析到別處(Python `UnsafePath`)。
    UnsafePath,
    /// 專案或指定的版本不存在(Python `ProjectNotFound`)。
    NotFound,
    /// 新增專案時已有同名(含只差大小寫)的專案(Python `ProjectExists`)。
    Exists,
    /// 磁碟用量超過上限(Python `QuotaExceeded`)。
    Quota,
    /// 檔案系統錯誤(Python 直接丟出的 `OSError`);值為 `safe-fs` 的錯誤類別,原錯誤見 [`Error::fs_error`]。
    Fs(safe_fs::ErrorKind),
}

/// `project` 的錯誤。
#[derive(Debug)]
pub struct Error {
    kind: ErrorKind,
    errors: Vec<String>,
    fs: Option<safe_fs::Error>,
}

/// `project` 的結果型別。
pub type Result<T> = std::result::Result<T, Error>;

impl Error {
    /// 指定類別與訊息清單。
    pub fn new(kind: ErrorKind, errors: Vec<String>) -> Self {
        Self {
            kind,
            errors,
            fs: None,
        }
    }

    /// 一則訊息的 `Invalid`(Python `ProjectError("…")`)。
    pub fn invalid(message: impl Into<String>) -> Self {
        Self::new(ErrorKind::Invalid, vec![message.into()])
    }

    /// 多則訊息的 `Invalid`(Python `ProjectError([…])`,累積的驗證錯誤)。
    pub fn invalid_all(errors: Vec<String>) -> Self {
        Self::new(ErrorKind::Invalid, errors)
    }

    /// 錯誤類別。
    pub fn kind(&self) -> ErrorKind {
        self.kind
    }

    /// 錯誤訊息清單(Python `ProjectError.errors`)。
    pub fn errors(&self) -> &[String] {
        &self.errors
    }

    /// 比對單元用的類別字串(見模組說明的對照表)。
    pub fn class(&self) -> &'static str {
        match self.kind {
            ErrorKind::Invalid => "project_error",
            ErrorKind::UnsafePath => "unsafe_path",
            ErrorKind::NotFound => "not_found",
            ErrorKind::Exists => "exists",
            ErrorKind::Quota => "quota",
            ErrorKind::Fs(_) => "os_error",
        }
    }

    /// `Fs` 類錯誤的原始 `safe-fs` 錯誤。
    pub fn fs_error(&self) -> Option<&safe_fs::Error> {
        self.fs.as_ref()
    }
}

impl From<safe_fs::Error> for Error {
    fn from(e: safe_fs::Error) -> Self {
        Self {
            kind: ErrorKind::Fs(e.kind()),
            errors: vec![e.to_string()],
            fs: Some(e),
        }
    }
}

impl fmt::Display for Error {
    /// 訊息清單以「;」串接(Python `";".join(errors)`)。
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.errors.join(";"))
    }
}

impl std::error::Error for Error {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        self.fs
            .as_ref()
            .map(|e| e as &(dyn std::error::Error + 'static))
    }
}
