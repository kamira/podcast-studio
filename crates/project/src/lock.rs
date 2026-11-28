//! 專案鎖(`project.py` 的 `_project_lock`;CHG-20261128-01 T5,代決 11、12)。
//!
//! 同一專案的存檔一律序列化,跨執行緒也跨行程,且與 Python 網頁版互斥(過渡期兩版並存,路線圖代決 13):
//!
//! - 在已開啟的專案目錄 handle 內以 `safe-fs` [`SafeDir::open_or_create`] 開啟(不存在就以 `0o600` 排他建立;
//!   Windows 忽略權限位元,行為差異 D-14)鎖檔 [`LOCK_FILE`](`.lock`),讀寫開啟、不截斷;一律不跟隨連結。
//!   開啟失敗(鎖檔是連結、目錄或其他作業系統錯誤)回 `UnsafePath`([`LOCK_UNSAFE`],與 Python 相同)。
//! - 以標準函式庫**阻塞的** [`std::fs::File::lock`] 上排他鎖(文件:Unix 為 `flock(LOCK_EX)`、Windows 為
//!   `LockFileEx(LOCKFILE_EXCLUSIVE_LOCK)`),與 Python `fcntl.flock(fd, LOCK_EX)` 相同**沒有逾時**;被訊號中斷
//!   (`Interrupted`)時重試(Python 3.5 起 `flock` 遇 `EINTR` 自動重試,PEP 475),其他錯誤回 `Fs`。
//!   不提供非阻塞取鎖的公開 API(Python 沒有)。
//! - [`ProjectLock`] 的 `Drop`(含 panic 展開)先 `unlock` 再關閉鎖檔;持鎖行程結束(含被終止)時由作業系統釋放。
//! - 每次取鎖各自開一個新的檔案 handle:同一行程不同執行緒彼此互斥;**同一執行緒持有某專案的
//!   [`ProjectLock`] 時再取同一專案的鎖會等到自己(形同死結)**,與 Python 相同——「載入 → 修改 → 存檔」用寫入 API
//!   的 `update`(T6),不要自己組合(「### 呼叫端指引」)。
//! - 鎖檔只用來上鎖,內容一律不讀不寫,也不刪除(刪除會讓兩個行程各鎖到不同的檔)。
//!
//! 過渡期呼叫端傳入讀取 / 寫入 API 的資料根目錄 `root` 必須與 Python `project.ROOT`(repo 根目錄)是同一個目錄,
//! 否則兩版各鎖各的 `.lock`、互斥失效(「### 呼叫端指引」第 1 列)。
//!
//! 不把與 `heavy-lock` 共通的部分抽成共用 crate(代決 12);本模組不含平台條件編譯。

use std::fmt;
use std::fs::File;
use std::io;

use safe_fs::{FileAccess, SafeDir};

use crate::constants::LOCK_FILE;
use crate::read::io_error;
use crate::{Error, ErrorKind, Result};

/// 鎖檔無法開啟(Python `_project_lock` 的 `UnsafePath`)。
pub const LOCK_UNSAFE: &str = "無法取得專案鎖(鎖檔可能是符號連結)";
/// 建立鎖檔的權限位元(與 Python 相同;只在 Unix 生效,受 umask 影響)。
const LOCK_FILE_MODE: u32 = 0o600;

/// 持有中的專案鎖;`Drop` 時(含 panic 展開)先解鎖再關閉鎖檔。
///
/// 不提供讀寫鎖檔的方法;鎖檔永不刪除。不持有專案目錄的 handle(與 Python 相同,專案目錄由呼叫端持有與關閉)。
#[must_use = "鎖在 ProjectLock 被丟棄時立即釋放"]
pub struct ProjectLock {
    file: File,
}

impl fmt::Debug for ProjectLock {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("ProjectLock { .. }")
    }
}

impl ProjectLock {
    /// Python `_project_lock(pfd)`:取得專案目錄 `project`(例:[`crate::read::open_project`] 的結果)內 `.lock` 的
    /// 排他鎖,**阻塞直到取得**(沒有逾時)。
    ///
    /// 鎖檔開啟失敗 → `UnsafePath`([`LOCK_UNSAFE`]);上鎖失敗(`Interrupted` 以外)→ `Fs`。
    pub fn acquire(project: &SafeDir) -> Result<ProjectLock> {
        let file = project
            .open_or_create(
                LOCK_FILE,
                FileAccess::ReadWrite { truncate: false },
                LOCK_FILE_MODE,
            )
            .map_err(|_| Error::new(ErrorKind::UnsafePath, vec![LOCK_UNSAFE.to_string()]))?;
        loop {
            match file.lock() {
                Ok(()) => return Ok(ProjectLock { file }),
                Err(e) if e.kind() == io::ErrorKind::Interrupted => {}
                Err(e) => return Err(io_error(&e)),
            }
        }
    }
}

impl Drop for ProjectLock {
    fn drop(&mut self) {
        // 解鎖失敗時忽略:隨後關閉檔案 handle 也會釋放鎖(Python 的 `finally` 也只是依序 `LOCK_UN`、`close`)
        let _ = self.file.unlock();
    }
}
