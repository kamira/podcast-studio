//! 比對工具與測試用的輔助函式(**不是產品介面**;產品程式不得呼叫)。
//!
//! 建立符號連結的平台差異集中在本 crate(CHG-20261125-01 Global Constraints:平台條件編譯只在
//! `crates/safe-fs/` 與測試檔),`crates/parity` 的 `safe_fs.*` 比對單元建合成檔案樹時呼叫。

use std::io;
use std::path::Path;

/// 建立指向 `target` 的符號連結 `link`;Windows 依 `dir` 建立目錄或檔案符號連結,其他平台忽略 `dir`。
pub fn symlink(target: &Path, link: &Path, dir: bool) -> io::Result<()> {
    #[cfg(unix)]
    {
        let _ = dir;
        std::os::unix::fs::symlink(target, link)
    }
    #[cfg(windows)]
    {
        if dir {
            std::os::windows::fs::symlink_dir(target, link)
        } else {
            std::os::windows::fs::symlink_file(target, link)
        }
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = (target, link, dir);
        Err(io::Error::from(io::ErrorKind::Unsupported))
    }
}

/// 錯誤是否為「無權限建立符號連結」(Windows `ERROR_PRIVILEGE_NOT_HELD`,未啟用開發人員模式時);
/// 其他平台一律 `false`。
pub fn is_symlink_privilege_error(e: &io::Error) -> bool {
    cfg!(windows) && e.raw_os_error() == Some(1314)
}
