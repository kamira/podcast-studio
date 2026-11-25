//! 安全路徑層:`safe_fs.py` 的 Rust 版(CHG-20261125-01,路線圖第 3 筆)。
//!
//! 所有操作自一個受信根目錄出發,以 cap-std 的能力式目錄 handle 逐層進行:
//!
//! - 每次只對**單一名稱**、相對上一層已開啟的 handle 操作;名稱先經 [`check_name`]。
//! - 中間各層以「不跟隨連結」的方式開啟,再以**同一個 handle** 的中繼資料確認是目錄;
//!   是連結(Windows:帶名稱代理位元的重新解析點,含符號連結、junction、WSL 連結)或不是目錄
//!   → [`ErrorKind::Unsafe`];不存在 → [`ErrorKind::NotFound`]。指向根目錄內部的連結也拒絕。
//! - 最後一層的檔案以不跟隨連結的方式開啟,再以 handle 確認是一般檔;之後讀寫一律用回傳的
//!   [`std::fs::File`],不再以路徑重開。
//! - 錯誤訊息不含本機路徑(見 [`Error`])。
//!
//! 平台差異只在本 crate(代決 3):Windows 的名稱另加嚴(代決 8)、保留裝置名明確失敗(代決 9)。
//! Windows 上 cap-std 的開啟以 `NtCreateFile` 相對目錄 handle 進行(不經 DOS 路徑轉換),
//! 但建立目錄等變更類操作由 handle 取回完整路徑後串接名稱再呼叫標準函式庫(開工閘紀錄見本 CHG 派發記錄)。
//!
//! 介面(T1):[`check_name`]、[`open_dir`]、[`open_path`]、[`SafeDir::open_child`]、[`SafeDir::open_file`]。
#![forbid(unsafe_code)]

mod error;
mod name;

use std::ffi::OsStr;
use std::fmt;
use std::fs::File;
use std::io;
use std::path::Path;

use cap_fs_ext::{DirExt, FollowSymlinks, OpenOptionsFollowExt};
use cap_std::fs::{Dir, OpenOptions};

pub use error::{Error, ErrorKind, Result, errno_name};
pub use name::{check_name, reserved_device};

use name::check_openable;

/// 開啟目錄的選項(Python `create=`、`mode=`)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DirOptions {
    /// 不存在的層先建立再開啟。
    pub create: bool,
    /// 建立目錄的權限位元(只在 Unix 生效,受 umask 影響;Windows 忽略,繼承上層 ACL,D-8)。
    pub mode: u32,
}

impl Default for DirOptions {
    fn default() -> Self {
        Self {
            create: false,
            mode: 0o755,
        }
    }
}

/// 開啟檔案的存取方式(取代 Python 的 `flags` 整數;呼叫端只用到唯讀、唯寫、讀寫與唯寫截斷)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FileAccess {
    Read,
    Write { truncate: bool },
    ReadWrite { truncate: bool },
}

impl FileAccess {
    fn writes(self) -> bool {
        !matches!(self, FileAccess::Read)
    }

    fn options(self) -> OpenOptions {
        let mut o = OpenOptions::new();
        match self {
            FileAccess::Read => {
                o.read(true);
            }
            FileAccess::Write { truncate } => {
                o.write(true).truncate(truncate);
            }
            FileAccess::ReadWrite { truncate } => {
                o.read(true).write(true).truncate(truncate);
            }
        }
        o.follow(FollowSymlinks::No);
        o
    }
}

/// 開啟時要的型別(Windows 決定錯誤類別用;Unix 依 errno 判定,不需要)。
#[derive(Debug, Clone, Copy)]
#[cfg_attr(unix, allow(dead_code))]
enum Want {
    Dir,
    File { writes: bool },
}

/// 已開啟的目錄 handle;`Drop` 時關閉。之後的開啟、建立都相對它進行。
pub struct SafeDir {
    dir: Dir,
}

impl fmt::Debug for SafeDir {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("SafeDir { .. }")
    }
}

/// 自受信根目錄 `top` 起逐層開啟 `parts`(皆為目錄),回傳最後一層的 handle。
///
/// **`top` 必須是呼叫端給的受信目錄**——來自程式內固定設定(例:應用程式資料目錄下的
/// `projects/`),**不得來自使用者輸入**;`top` 本身會**跟隨連結**(視為受信,與 Python 相同),
/// 只有 `parts` 的每一層才拒絕連結。
///
/// 先檢查全部 `parts` 的名稱(任一不合法即 `InvalidName`;Windows 上任一為保留裝置名即
/// `ReservedDevice`),不開任何東西;`top` 不存在 `NotFound`、是一般檔 `NotADirectory`。
/// `opts.create` 時不存在的層先建立再以不跟隨方式開啟;已是一般檔或連結(含懸空連結)者 `Unsafe`。
pub fn open_dir<S: AsRef<OsStr>>(top: &Path, parts: &[S], opts: DirOptions) -> Result<SafeDir> {
    for p in parts {
        check_openable(p.as_ref())?;
    }
    let dir = Dir::open_ambient_dir(top, cap_std::ambient_authority()).map_err(error::from_io)?;
    let mut cur = SafeDir { dir };
    for p in parts {
        cur = cur.open_child(p, opts)?;
    }
    Ok(cur)
}

/// `open_dir(top, parts[..n-1])` 後以 [`SafeDir::open_file`] 開啟最後一段;`parts` 為空 `InvalidName`。
pub fn open_path<S: AsRef<OsStr>>(top: &Path, parts: &[S], access: FileAccess) -> Result<File> {
    let Some((last, dirs)) = parts.split_last() else {
        return Err(Error::new(ErrorKind::InvalidName));
    };
    open_dir(top, dirs, DirOptions::default())?.open_file(last, access)
}

impl SafeDir {
    /// 開啟本目錄內名為 `name` 的子目錄(不跟隨連結,以 handle 確認是目錄);
    /// `opts.create` 時不存在就先建立(已存在不算錯誤)再開啟。
    pub fn open_child(&self, name: impl AsRef<OsStr>, opts: DirOptions) -> Result<SafeDir> {
        let name = name.as_ref();
        check_openable(name)?;
        match self.open_subdir(name) {
            Err(e) if e.kind() == ErrorKind::NotFound && opts.create => {}
            other => return other,
        }
        match self.mkdir(name, opts.mode) {
            Ok(()) => {}
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {}
            Err(e) => return Err(error::from_io(e)),
        }
        self.open_subdir(name)
    }

    /// 開啟本目錄內名為 `name` 的一般檔(不跟隨連結;開啟後以 handle 確認是一般檔,否則 `Unsafe`)。
    /// 檔案不存在時 `NotFound`(不建立)。
    pub fn open_file(&self, name: impl AsRef<OsStr>, access: FileAccess) -> Result<File> {
        let name = name.as_ref();
        check_openable(name)?;
        match self.dir.open_with(name, &access.options()) {
            Ok(f) => {
                let f = f.into_std();
                let meta = f.metadata().map_err(error::from_io)?;
                if meta.is_file() && !meta.file_type().is_symlink() {
                    Ok(f)
                } else {
                    Err(Error::new(ErrorKind::Unsafe))
                }
            }
            Err(e) => Err(self.open_error(
                name,
                e,
                Want::File {
                    writes: access.writes(),
                },
            )),
        }
    }

    fn open_subdir(&self, name: &OsStr) -> Result<SafeDir> {
        match self.dir.open_dir_nofollow(name) {
            Ok(dir) => {
                let meta = dir.dir_metadata().map_err(error::from_io)?;
                if meta.is_dir() && !meta.file_type().is_symlink() {
                    Ok(SafeDir { dir })
                } else {
                    Err(Error::new(ErrorKind::Unsafe))
                }
            }
            Err(e) => Err(self.open_error(name, e, Want::Dir)),
        }
    }

    #[cfg(unix)]
    fn mkdir(&self, name: &OsStr, mode: u32) -> io::Result<()> {
        use cap_std::fs::{DirBuilder, DirBuilderExt};
        let mut b = DirBuilder::new();
        b.mode(mode);
        self.dir.create_dir_with(name, &b)
    }

    #[cfg(not(unix))]
    fn mkdir(&self, name: &OsStr, _mode: u32) -> io::Result<()> {
        self.dir.create_dir(name)
    }

    /// Unix:與 Python 相同,依 errno 判定——`ELOOP`、`ENOTDIR`、`EMLINK`(不跟隨連結時遇到連結
    /// 或不是目錄)為 `Unsafe`,其餘依 PEP 3151 的對應。
    #[cfg(unix)]
    fn open_error(&self, _name: &OsStr, e: io::Error, _want: Want) -> Error {
        match e.raw_os_error() {
            Some(error::ELOOP | error::EMLINK | error::ERRNO_ENOTDIR) => {
                Error::with_source(ErrorKind::Unsafe, e)
            }
            _ => error::from_io(e),
        }
    }

    /// Windows:錯誤碼與 Unix 不同(例:開啟目錄當檔案只給「拒絕存取」),所以除了「找不到」以外,
    /// 再以不跟隨方式取得該名稱的型別,改回與 Linux 相同的類別:連結或非一般檔為 `Unsafe`,
    /// 以寫入方式開啟目錄為 `IsADirectory`(D-7)。
    #[cfg(not(unix))]
    fn open_error(&self, name: &OsStr, e: io::Error, want: Want) -> Error {
        if e.kind() == io::ErrorKind::NotFound {
            return error::from_io(e);
        }
        let Ok(m) = self.dir.symlink_metadata(name) else {
            return error::from_io(e);
        };
        let kind = if m.file_type().is_symlink() {
            ErrorKind::Unsafe
        } else {
            match want {
                Want::Dir if !m.is_dir() => ErrorKind::Unsafe,
                Want::File { writes: true } if m.is_dir() => ErrorKind::IsADirectory,
                Want::File { .. } if !m.is_file() => ErrorKind::Unsafe,
                _ => return error::from_io(e),
            }
        };
        Error::with_source(kind, e)
    }
}
