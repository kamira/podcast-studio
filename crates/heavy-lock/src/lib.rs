//! 跨行程重型工作鎖:`heavy_lock.py` 的 Rust 版(CHG-20261127-01,路線圖第 4 筆)。
//!
//! 24 GB 統一記憶體同時只撐得住一個重型工作(LLM、生圖、語音、影片),所以 Python 網頁版、Python MCP、
//! 之後的 Rust 應用程式與 Rust MCP 共用同一把鎖:`<root>/outputs/.heavy.lock`(Guideline §6.1)。
//!
//! 介面:[`acquire`]、[`acquire_with`] 取得 [`HeavyLock`],`Drop` 時釋放;常數 [`LOCK_DIR`]、[`LOCK_NAME`]、
//! [`POLL_INTERVAL`];錯誤 [`Error`]。
//!
//! 行為(與 Python 逐步相同,代決 6~8):
//!
//! - 以 `safe-fs` 自受信根目錄 `root` 開啟 `outputs/`(不存在就建立)與鎖檔(不存在就以 `0o600` 建立;
//!   Windows 忽略權限位元),隨即釋放目錄 handle。`outputs/` 或鎖檔是連結、型別不符時回
//!   [`Error::Path`](`safe_fs::ErrorKind::Unsafe`);路徑判斷全部沿用 `safe-fs`,本 crate 不另寫。
//! - 以標準函式庫 [`std::fs::File::try_lock`] 取得排他鎖(文件:Unix 為 `flock(LOCK_EX | LOCK_NB)`、
//!   Windows 為 `LockFileEx(LOCKFILE_EXCLUSIVE_LOCK | LOCKFILE_FAIL_IMMEDIATELY)`)。被占用時先呼叫一次
//!   `on_wait`,**之後**才計算期限,再每 [`POLL_INTERVAL`] 重試;每次重試失敗後才判斷是否逾時。
//!   等待一律以 `try_lock` 輪詢(含 `timeout = None`),不用阻塞的 `lock()`(代決 7)。
//! - 持鎖行程結束(含被終止)時由作業系統釋放,不留死鎖。
//! - 每次取鎖各自開一個新的檔案 handle:同一行程不同執行緒彼此互斥;**同一執行緒不可巢狀取鎖**
//!   (會等到自己:有逾時時回 [`Error::Timeout`],沒有逾時時形同死結;代決 8)。
//! - 鎖檔只用來上鎖,內容一律不讀不寫,也不刪除(刪除會讓兩個行程各鎖到不同的檔)。
//!
//! 過渡期呼叫端傳入的 `root` 必須與 Python `heavy_lock.ROOT`(repo 根目錄)是同一個目錄,
//! 否則兩版各鎖各的、互斥失效(CHG-20261127-01「### 呼叫端指引」)。
//!
//! 本 crate 不含任何平台條件編譯;平台差異由標準函式庫與 `safe-fs` 承擔。
#![forbid(unsafe_code)]

use std::fmt;
use std::fs::{File, TryLockError};
use std::io;
use std::path::Path;
use std::time::{Duration, Instant};

use safe_fs::{DirOptions, FileAccess};

/// 鎖檔所在目錄(相對受信根目錄)。
pub const LOCK_DIR: &str = "outputs";
/// 鎖檔名稱。
pub const LOCK_NAME: &str = ".heavy.lock";
/// 被占用時的重試間隔(Python `POLL_SECONDS = 0.05`)。
pub const POLL_INTERVAL: Duration = Duration::from_millis(50);

/// 建立 `outputs/` 的權限位元(只在 Unix 生效,受 umask 影響)。
const DIR_MODE: u32 = 0o755;
/// 建立鎖檔的權限位元(與 Python 相同;只在 Unix 生效)。
const LOCK_FILE_MODE: u32 = 0o600;

/// 取鎖錯誤。`Display` 為繁體中文,不含任何本機路徑(行為差異 D-1)。
#[derive(Debug)]
pub enum Error {
    /// 等待超過指定的逾時(Python `TimeoutError`);值為呼叫端給的逾時。
    Timeout(Duration),
    /// 開啟 `outputs/` 或鎖檔失敗;類別沿用 [`safe_fs::ErrorKind`](連結或型別不符為 `Unsafe`)。
    Path(safe_fs::Error),
    /// `try_lock` 回「被占用」以外的錯誤(不重試,立即回報)。
    Lock(io::Error),
}

/// `heavy-lock` 的結果型別。
pub type Result<T> = std::result::Result<T, Error>;

impl fmt::Display for Error {
    /// 只寫類別說明、秒數與錯誤碼,不轉出底層錯誤訊息(可能含路徑)。
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Timeout(t) => write!(f, "等待重型工作鎖超過 {} 秒", t.as_secs_f64()),
            Error::Path(e) => write!(f, "無法開啟重型工作鎖檔:{e}"),
            Error::Lock(e) => match e.raw_os_error() {
                Some(code) => write!(f, "重型工作鎖上鎖失敗(錯誤碼 {code})"),
                None => f.write_str("重型工作鎖上鎖失敗"),
            },
        }
    }
}

impl std::error::Error for Error {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Error::Timeout(_) => None,
            Error::Path(e) => Some(e),
            Error::Lock(e) => Some(e),
        }
    }
}

/// 持有中的重型工作鎖;`Drop` 時(含 panic 展開)先解鎖再關閉鎖檔。
///
/// 不提供讀寫鎖檔的方法;鎖檔永不刪除。
#[must_use = "鎖在 HeavyLock 被丟棄時立即釋放"]
pub struct HeavyLock {
    file: File,
}

impl fmt::Debug for HeavyLock {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("HeavyLock { .. }")
    }
}

impl Drop for HeavyLock {
    fn drop(&mut self) {
        // 解鎖失敗時忽略:隨後關閉檔案 handle 也會釋放鎖
        let _ = self.file.unlock();
    }
}

/// 取得重型工作鎖,不需要等待通知時使用;見 [`acquire_with`]。
pub fn acquire(root: &Path, timeout: Option<Duration>) -> Result<HeavyLock> {
    acquire_with(root, timeout, || {})
}

/// 取得 `root/outputs/.heavy.lock` 的排他鎖。
///
/// - `root`:呼叫端給的受信根目錄(Python 的 `ROOT`),**不得來自使用者輸入**;鎖檔位置固定,
///   不收任意鎖檔路徑(行為差異 D-2)。
/// - `timeout`:`None` 一直等;`Some(t)` 自 `on_wait` 返回後起算,逾時回 [`Error::Timeout`]
///   (`Duration::ZERO` 仍重試一次;期限溢位時視同不逾時,行為差異 D-4)。
/// - `on_wait`:第一次取鎖失敗時呼叫一次(例:顯示「等待其他工作」);沒有等待時不呼叫。
///   不要在其中阻塞;需要取消時以短逾時反覆呼叫本函式,在每次 `Timeout` 後檢查取消。
pub fn acquire_with(
    root: &Path,
    timeout: Option<Duration>,
    on_wait: impl FnOnce(),
) -> Result<HeavyLock> {
    let file = open_lock_file(root)?;
    if try_lock(&file)? {
        return Ok(HeavyLock { file });
    }
    on_wait();
    let deadline = timeout.and_then(|t| Instant::now().checked_add(t));
    loop {
        if try_lock(&file)? {
            return Ok(HeavyLock { file });
        }
        if let (Some(t), Some(d)) = (timeout, deadline)
            && Instant::now() >= d
        {
            return Err(Error::Timeout(t));
        }
        std::thread::sleep(POLL_INTERVAL);
    }
}

/// 經 `safe-fs` 開啟(必要時建立)`root/outputs/` 與鎖檔;目錄 handle 只在開檔期間持有。
fn open_lock_file(root: &Path) -> Result<File> {
    let dir = safe_fs::open_dir(
        root,
        &[LOCK_DIR],
        DirOptions {
            create: true,
            mode: DIR_MODE,
        },
    )
    .map_err(Error::Path)?;
    let file = dir
        .open_or_create(
            LOCK_NAME,
            FileAccess::ReadWrite { truncate: false },
            LOCK_FILE_MODE,
        )
        .map_err(Error::Path)?;
    drop(dir);
    Ok(file)
}

/// 非阻塞取鎖:取得回 `true`、被占用回 `false`,其他錯誤回 [`Error::Lock`]。
fn try_lock(file: &File) -> Result<bool> {
    match file.try_lock() {
        Ok(()) => Ok(true),
        Err(TryLockError::WouldBlock) => Ok(false),
        Err(TryLockError::Error(e)) => Err(Error::Lock(e)),
    }
}
