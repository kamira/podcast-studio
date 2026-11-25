//! 測試共用:排他建立的暫存工作目錄(`Drop` 時刪除)、快照、建立連結(CHG-20261125-01 代決 10)。
//!
//! 測試只在自己排他建立的工作目錄內建立、修改、刪除檔案(Global Constraints)。
//! 工作目錄下有 `top/`(受信根目錄)與 `outside/`(含標記檔,模擬根目錄外的資料)。
#![allow(dead_code)]

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

static COUNTER: AtomicU32 = AtomicU32::new(0);

/// 暫存工作目錄;`Drop` 時以 `std::fs::remove_dir_all` 刪除(不跟隨連結),
/// 測試 panic 時也會清理。
pub struct WorkDir {
    root: PathBuf,
}

impl WorkDir {
    pub fn new() -> Self {
        let base = std::env::temp_dir();
        loop {
            let nanos = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.subsec_nanos())
                .unwrap_or(0);
            let name = format!(
                "safe-fs-test-{}-{}-{nanos}",
                std::process::id(),
                COUNTER.fetch_add(1, Ordering::Relaxed)
            );
            let root = base.join(name);
            match std::fs::create_dir(&root) {
                Ok(()) => {
                    let w = WorkDir { root };
                    std::fs::create_dir(w.top()).expect("建立 top 失敗");
                    std::fs::create_dir(w.outside()).expect("建立 outside 失敗");
                    std::fs::write(w.outside().join("marker.txt"), "outside")
                        .expect("寫入標記失敗");
                    return w;
                }
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(e) => panic!("無法建立暫存工作目錄:{e}"),
            }
        }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn top(&self) -> PathBuf {
        self.root.join("top")
    }

    pub fn outside(&self) -> PathBuf {
        self.root.join("outside")
    }
}

impl Drop for WorkDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

/// 不跟隨連結的快照:依相對路徑排序的 `(相對路徑, 型別, 內容)`。
pub fn snapshot(dir: &Path) -> Vec<(String, &'static str, String)> {
    fn walk(base: &Path, dir: &Path, out: &mut Vec<(String, &'static str, String)>) {
        for entry in std::fs::read_dir(dir).expect("讀取目錄失敗") {
            let path = entry.expect("讀取目錄項失敗").path();
            let rel = path
                .strip_prefix(base)
                .expect("路徑不在快照根目錄下")
                .to_string_lossy()
                .replace('\\', "/");
            let meta = std::fs::symlink_metadata(&path).expect("lstat 失敗");
            if meta.file_type().is_symlink() {
                out.push((rel, "symlink", String::new()));
            } else if meta.is_dir() {
                out.push((rel, "dir", String::new()));
                walk(base, &path, out);
            } else {
                let content = std::fs::read_to_string(&path).unwrap_or_default();
                out.push((rel, "file", content));
            }
        }
    }
    let mut out = Vec::new();
    walk(dir, dir, &mut out);
    out.sort();
    out
}

/// 是否在 CI(GitHub Actions)上執行:無法建立符號連結時 CI 判失敗、本機略過(代決 10)。
pub fn in_ci() -> bool {
    std::env::var_os("GITHUB_ACTIONS").is_some_and(|v| v == "true")
}

/// 連結指向的種類(Windows 的符號連結分檔案與目錄)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LinkKind {
    Dir,
    File,
}

/// 建立符號連結。成功回 `true`;Windows 本機無權限時印出略過並回 `false`,CI 上無權限即 panic。
pub fn symlink(target: &Path, link: &Path, kind: LinkKind) -> bool {
    #[cfg(unix)]
    {
        let _ = kind;
        std::os::unix::fs::symlink(target, link).expect("建立符號連結失敗");
        true
    }
    #[cfg(windows)]
    {
        let r = match kind {
            LinkKind::Dir => std::os::windows::fs::symlink_dir(target, link),
            LinkKind::File => std::os::windows::fs::symlink_file(target, link),
        };
        match r {
            Ok(()) => true,
            // ERROR_PRIVILEGE_NOT_HELD
            Err(e) if e.raw_os_error() == Some(1314) => {
                assert!(!in_ci(), "CI 上無法建立符號連結(無權限),判失敗:{e}");
                println!("略過:無權限建立符號連結");
                false
            }
            Err(e) => panic!("建立符號連結失敗:{e}"),
        }
    }
}

/// `cmd /c` 會重新解析整個命令列的字元(安全影響 (f)、C-3);路徑含有任一個就判測試失敗,不略過。
pub const CMD_METACHARS: &[char] = &['&', '|', '<', '>', '^', '%', '"', '(', ')', '!', '\n', '\r'];

/// 以 `cmd /d /c mklink /J "<連結>" "<目標>"` 建立 junction(只在測試中;不需權限、不得略過)。
///
/// 命令列組法:程式固定為 `%SystemRoot%\System32\cmd.exe`;參數以 `raw_arg` 整行傳入
/// `/d /c mklink /J "<連結>" "<目標>"`——`/d` 停用 AutoRun,兩個路徑各以一對雙引號包住
/// (路徑本身已斷言不含雙引號與上列元字元,所以引號不會被提前結束,cmd 也沒有可展開或串接的字元)。
#[cfg(windows)]
pub fn junction(link: &Path, target: &Path) {
    use std::os::windows::process::CommandExt;
    for (what, p) in [("連結路徑", link), ("目標路徑", target)] {
        let s = p
            .to_str()
            .unwrap_or_else(|| panic!("{what}不是合法 Unicode"));
        if let Some(c) = s.chars().find(|c| CMD_METACHARS.contains(c)) {
            panic!("{what}含 cmd 元字元 {c:?},不得以 cmd /c mklink /J 建立 junction");
        }
    }
    let system_root = std::env::var_os("SystemRoot").expect("缺少 SystemRoot 環境變數");
    let cmd = Path::new(&system_root).join("System32").join("cmd.exe");
    let line = format!(
        "/d /c mklink /J \"{}\" \"{}\"",
        link.to_str().expect("已檢查"),
        target.to_str().expect("已檢查")
    );
    let out = std::process::Command::new(cmd)
        .raw_arg(line)
        .output()
        .expect("無法執行 cmd");
    assert!(
        out.status.success(),
        "mklink /J 失敗(exit {:?})",
        out.status.code()
    );
    let meta = std::fs::symlink_metadata(link).expect("junction 建立後 lstat 失敗");
    assert!(meta.file_type().is_symlink(), "建立的不是 junction");
}

/// Windows 的 `\\?\` 完整路徑(不經 DOS 裝置名轉換;代決 9 預先建立保留名稱項目用)。
#[cfg(windows)]
pub fn verbatim(p: &Path) -> PathBuf {
    let abs = std::path::absolute(p).expect("無法取得絕對路徑");
    let s = abs.to_str().expect("暫存路徑須為 Unicode");
    if s.starts_with(r"\\?\") {
        abs
    } else {
        PathBuf::from(format!(r"\\?\{s}"))
    }
}
