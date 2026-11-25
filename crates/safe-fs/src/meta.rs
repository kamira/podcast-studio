//! 不跟隨連結的中繼資料(CHG-20261125-01 代決 6;Python `lstat` 回傳的 `os.stat_result` 中呼叫端用到的欄位)。

use std::time::SystemTime;

use cap_fs_ext::MetadataExt;

use crate::error::{self, Result};

/// 項目型別(Python `S_ISREG` / `S_ISDIR` / `S_ISLNK`;其他如 FIFO、裝置為 `Other`)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EntryKind {
    File,
    Dir,
    /// 符號連結;Windows 上為帶名稱代理位元的重新解析點(符號連結、junction、WSL 連結,D-3)。
    Symlink,
    Other,
}

/// `SafeDir::lstat` 的結果。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Meta {
    pub kind: EntryKind,
    /// 大小(位元組;Python `st_size`)。
    pub len: u64,
    /// 最後修改時間(Python `st_mtime` / `st_mtime_ns`)。
    pub modified: SystemTime,
    /// 檔案識別碼:Unix 為 `(st_dev, st_ino)`;Windows 為(磁碟區序號, 檔案索引),由 cap-std 以開啟後的
    /// handle 取得。同一檔案的硬連結相同,不同檔案不同(呼叫端以此辨識同一個檔案,不以路徑比對)。
    pub identity: (u64, u64),
}

impl Meta {
    /// 由 cap-std 的中繼資料轉換(cap-std 的 `symlink_metadata` 在 Windows 上以開啟後的 handle 取得,
    /// 磁碟區序號與檔案索引一定存在)。
    pub(crate) fn from_cap(m: &cap_std::fs::Metadata) -> Result<Meta> {
        let ft = m.file_type();
        let kind = if ft.is_symlink() {
            EntryKind::Symlink
        } else if ft.is_dir() {
            EntryKind::Dir
        } else if ft.is_file() {
            EntryKind::File
        } else {
            EntryKind::Other
        };
        let modified = m.modified().map_err(error::from_io)?.into_std();
        Ok(Meta {
            kind,
            len: m.len(),
            modified,
            identity: (MetadataExt::dev(m), MetadataExt::ino(m)),
        })
    }
}
