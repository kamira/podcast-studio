//! 測試共用(CHG-20261127-01 代決 10):排他建立的暫存工作目錄、建立連結、標記檔交握、
//! 以測試執行檔自己重新執行自己的持鎖輔助行程。
//!
//! - 測試只在自己排他建立的工作目錄內建立、修改、刪除檔案(Global Constraints)。工作目錄下有
//!   `top/`(傳給 `heavy_lock::acquire` 的受信根目錄,鎖檔在 `top/outputs/.heavy.lock`)與
//!   `outside/`(含標記檔,模擬根目錄外的資料);交握用的標記檔直接放在工作目錄下。
//! - 一律以標記檔交握、斷言因果順序,不以睡眠長短推斷兩行程是否重疊。
//! - 輔助行程:`std::env::current_exe()` 以 argv 清單 `--exact heavy_lock_helper --nocapture` 重新執行,
//!   以環境變數傳角色與工作目錄;需啟動輔助行程的測試執行檔(`cross_process.rs`、`cross_lang.rs`)都要定義
//!   `#[test] fn heavy_lock_helper()` 呼叫 [`run_helper_if_requested`](沒有該環境變數時立即返回;
//!   `lock.rs` 不啟動輔助行程,不需定義)。不新增 `[[bin]]`、範例程式或 dev-dependency。
#![allow(dead_code)]

use std::ffi::OsStr;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicU32, AtomicUsize, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

static COUNTER: AtomicU32 = AtomicU32::new(0);
static SKIPPED: AtomicUsize = AtomicUsize::new(0);

/// 子行程等待開始(取得鎖、看到 `ready`)的上限。
pub const START_LIMIT: Duration = Duration::from_secs(30);
/// 子行程等待釋放訊號(`waiting` 或 `release`)與父行程等待子行程結束的上限。
pub const RELEASE_LIMIT: Duration = Duration::from_secs(60);
/// 逾時案例的短逾時。
pub const SHORT_TIMEOUT: Duration = Duration::from_millis(300);
/// 逾時案例的耗時上限(放寬,代決 10 (i))。
pub const TIMEOUT_UPPER: Duration = Duration::from_secs(10);

/// 持鎖方取得鎖後寫出。
pub const READY: &str = "ready";
/// 等待方的 `on_wait` 寫出。
pub const WAITING: &str = "waiting";
/// 父行程要持鎖方釋放時寫出。
pub const RELEASE: &str = "release";
/// 持鎖方釋放前寫出(等待方取得後斷言它已存在)。
pub const RELEASING: &str = "releasing";

/// 輔助行程的角色與工作目錄。
pub const ROLE_ENV: &str = "HEAVY_LOCK_TEST_ROLE";
pub const DIR_ENV: &str = "HEAVY_LOCK_TEST_DIR";
/// 每個測試執行檔裡的輔助測試名稱。
pub const HELPER_TEST: &str = "heavy_lock_helper";

/// `try` 角色的結束碼:以短逾時取鎖回 `Timeout`。用非 0 專用碼,避免輔助行程沒有執行到角色時
/// (例:libtest 找不到符合的測試名稱而以 0 結束)被誤判為逾時。
pub const EXIT_TIMEOUT: i32 = 20;
/// `try` 角色的結束碼:取得鎖(不該發生)。
pub const EXIT_ACQUIRED: i32 = 10;
/// 開啟或上鎖錯誤(`Timeout` 以外的 [`heavy_lock::Error`])。
pub const EXIT_ERROR: i32 = 11;
/// `hold` 角色等不到開始(取鎖逾時)或釋放訊號。
pub const EXIT_NO_SIGNAL: i32 = 12;
/// 角色不明或工作目錄缺漏。
pub const EXIT_USAGE: i32 = 2;

/// 暫存工作目錄;`Drop` 時以 `std::fs::remove_dir_all` 刪除(不跟隨連結),測試 panic 時也會清理。
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
                "heavy-lock-test-{}-{}-{nanos}",
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

    /// 自既有的工作目錄路徑取得(輔助行程用;不建立、`Drop` 時不刪除——由父行程刪除)。
    fn borrowed(root: PathBuf) -> std::mem::ManuallyDrop<Self> {
        std::mem::ManuallyDrop::new(WorkDir { root })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    /// 受信根目錄(Python 的 `ROOT`)。
    pub fn top(&self) -> PathBuf {
        self.root.join("top")
    }

    pub fn outside(&self) -> PathBuf {
        self.root.join("outside")
    }

    /// 鎖檔的預期位置 `top/outputs/.heavy.lock`。
    pub fn lock_path(&self) -> PathBuf {
        self.top().join("outputs").join(".heavy.lock")
    }

    pub fn marker(&self, name: &str) -> PathBuf {
        self.root.join(name)
    }

    pub fn touch(&self, name: &str) {
        std::fs::write(self.marker(name), b"").expect("寫入標記檔失敗");
    }

    pub fn has(&self, name: &str) -> bool {
        self.marker(name).exists()
    }

    /// 等到 `names` 任一標記檔出現;逾 `limit` 回 `false`。
    pub fn wait_any(&self, names: &[&str], limit: Duration) -> bool {
        let start = Instant::now();
        loop {
            if names.iter().any(|n| self.has(n)) {
                return true;
            }
            if start.elapsed() >= limit {
                return false;
            }
            std::thread::sleep(Duration::from_millis(5));
        }
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

/// 是否在 CI(GitHub Actions)上執行:無法建立符號連結時 CI 判失敗、本機略過(CHG-20261125-01 代決 10)。
pub fn in_ci() -> bool {
    std::env::var_os("GITHUB_ACTIONS").is_some_and(|v| v == "true")
}

/// 以 `safe_fs::support::symlink` 建立符號連結。成功回 `true`;Windows 本機無權限時印出略過與
/// 本測試執行檔累計的略過數並回 `false`;CI 上無權限即 panic。
pub fn symlink(target: &Path, link: &Path, dir: bool) -> bool {
    match safe_fs::support::symlink(target, link, dir) {
        Ok(()) => true,
        Err(e) if safe_fs::support::is_symlink_privilege_error(&e) => {
            assert!(!in_ci(), "CI 上無法建立符號連結(無權限),判失敗:{e}");
            let n = SKIPPED.fetch_add(1, Ordering::Relaxed) + 1;
            println!("略過:無權限建立符號連結(本測試執行檔累計略過 {n} 個案例)");
            false
        }
        Err(e) => panic!("建立符號連結失敗:{e}"),
    }
}

/// 以測試執行檔自己重新執行自己的輔助行程;`Drop` 時 kill 並回收,不留孤兒行程。
pub struct Helper {
    child: Option<Child>,
}

impl Helper {
    /// 以角色 `role` 啟動輔助行程(argv 清單、不經 shell);工作目錄以環境變數傳入。
    pub fn spawn(role: &str, w: &WorkDir) -> Helper {
        let exe = std::env::current_exe().expect("無法取得測試執行檔路徑");
        let child = Command::new(exe)
            .args(["--exact", HELPER_TEST, "--nocapture"])
            .env(ROLE_ENV, role)
            .env(DIR_ENV, w.root())
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::inherit())
            .spawn()
            .expect("無法啟動輔助行程");
        Helper { child: Some(child) }
    }

    fn child(&mut self) -> &mut Child {
        self.child.as_mut().expect("輔助行程已回收")
    }

    /// 等輔助行程結束,至多 `limit`;逾時回 `None`(之後由 `Drop` kill)。
    pub fn wait_limit(&mut self, limit: Duration) -> Option<ExitStatus> {
        let start = Instant::now();
        loop {
            if let Some(status) = self.child().try_wait().expect("查詢輔助行程失敗") {
                self.child = None;
                return Some(status);
            }
            if start.elapsed() >= limit {
                return None;
            }
            std::thread::sleep(Duration::from_millis(10));
        }
    }

    /// 終止輔助行程並等到回收(`Child::wait()` 返回)。
    pub fn kill_and_wait(&mut self) -> ExitStatus {
        let mut child = self.child.take().expect("輔助行程已回收");
        child.kill().expect("無法終止輔助行程");
        child.wait().expect("無法回收輔助行程")
    }
}

impl Drop for Helper {
    fn drop(&mut self) {
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

/// 輔助行程入口:沒有 [`ROLE_ENV`] 時立即返回(一般測試執行時);有時執行角色並以結束碼結束行程。
///
/// 角色:
/// - `hold`:以 [`START_LIMIT`] 取鎖 → 寫 `ready` → 等 `waiting` 或 `release`(至多 [`RELEASE_LIMIT`])
///   → 寫 `releasing` → 釋放 → 結束碼 0;取鎖逾時或等不到訊號以 [`EXIT_NO_SIGNAL`] 結束(作業系統釋放鎖),
///   其他取鎖錯誤以 [`EXIT_ERROR`] 結束。
/// - `try`:以 [`SHORT_TIMEOUT`] 取鎖,回 `Timeout` 以 [`EXIT_TIMEOUT`] 結束,取得以 [`EXIT_ACQUIRED`]、
///   其他錯誤以 [`EXIT_ERROR`] 結束。
pub fn run_helper_if_requested() {
    let Some(role) = std::env::var_os(ROLE_ENV) else {
        return;
    };
    let Some(dir) = std::env::var_os(DIR_ENV) else {
        std::process::exit(EXIT_USAGE);
    };
    let w = WorkDir::borrowed(PathBuf::from(dir));
    let code = if role == OsStr::new("hold") {
        helper_hold(&w)
    } else if role == OsStr::new("try") {
        helper_try(&w)
    } else {
        EXIT_USAGE
    };
    std::process::exit(code);
}

fn helper_hold(w: &WorkDir) -> i32 {
    let lock = match heavy_lock::acquire(&w.top(), Some(START_LIMIT)) {
        Ok(lock) => lock,
        Err(heavy_lock::Error::Timeout(_)) => return EXIT_NO_SIGNAL,
        Err(_) => return EXIT_ERROR,
    };
    w.touch(READY);
    if !w.wait_any(&[WAITING, RELEASE], RELEASE_LIMIT) {
        return EXIT_NO_SIGNAL;
    }
    w.touch(RELEASING);
    drop(lock);
    0
}

fn helper_try(w: &WorkDir) -> i32 {
    match heavy_lock::acquire(&w.top(), Some(SHORT_TIMEOUT)) {
        Err(heavy_lock::Error::Timeout(_)) => EXIT_TIMEOUT,
        Ok(_lock) => EXIT_ACQUIRED,
        Err(_) => EXIT_ERROR,
    }
}
