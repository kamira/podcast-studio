//! L2:Python 對 Rust 跨語言互斥(CHG-20261127-01 T2;只在 Unix 編譯執行,即 macOS / Linux CI 格)。
//!
//! 證明 Rust `heavy_lock` 與 repo 根目錄的 `heavy_lock.py`(唯讀 import,不改)互斥,也就是 Rust 在 Unix 上
//! 是 `flock` 語意(路線圖代決 13;事實 E 由本檔證明)。案例 (i)~(vii) 對應 CHG「### Tasks」T2 的 L2。
//!
//! - Python 子行程:argv 清單 `[<python>, -I, -B, -X, utf8, -c, <程式>, <repo>, <top>, <work>, <角色>]`,不經 shell;
//!   直譯器取 `PARITY_PYTHON`(空字串視為未提供),沒有時 `python3`。`-I` 隔離模式(忽略 `PYTHON*` 環境變數、
//!   不把目前目錄與使用者 site-packages 放進 `sys.path`),所以以 `-B`(不寫 `.pyc`)、`-X utf8` 取代
//!   `PYTHONDONTWRITEBYTECODE`、`PYTHONUTF8`。程式只把 repo 根目錄附加在 `sys.path` 末端,匯入後核對
//!   `heavy_lock`、`safe_fs` 確實來自 repo 根目錄,再把 `heavy_lock.ROOT`、`heavy_lock.LOCK_PATH` 改指向暫存根目錄
//!   (`canonicalize` 過,避免 macOS `/var` → `/private/var`;做法同 `tests/test_heavy_lock.py`)。
//! - 每個案例:斷言 Python 改寫後的 `LOCK_PATH` 位於暫存工作目錄之下;測試前後工作副本的
//!   `outputs/.heavy.lock` 狀態(存在與否、長度、mtime、inode)不變(CHG「## 審議」交施工補充 (a))。
//! - Python 無法啟動時測試判失敗,不略過(代決 10)。
//! - 一律以標記檔交握、斷言因果順序;時間斷言只有 (i) 逾時與 (v) 被終止後 2 秒內取得(代決 10)。
#![cfg(unix)]

mod common;

use std::ffi::OsString;
use std::io::Read;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{Duration, Instant};

use common::{
    Helper, READY, RELEASE, RELEASE_LIMIT, RELEASING, SHORT_TIMEOUT, START_LIMIT, TIMEOUT_UPPER,
    WAITING, WorkDir,
};
use heavy_lock::{Error, acquire, acquire_with};

/// 輔助行程入口(沒有角色環境變數時立即返回);案例 (vi) 用 T1 的 Rust 持鎖輔助行程。
#[test]
fn heavy_lock_helper() {
    common::run_helper_if_requested();
}

/// Python 直譯器旗標:隔離模式、不寫 `.pyc`、UTF-8 模式(`-I` 會忽略對應的環境變數)。
const PY_FLAGS: [&str; 4] = ["-I", "-B", "-X", "utf8"];
/// Python 被終止後 Rust 取得的門檻(與 Python 測試相同,代決 10 (ii))。
const KILL_RELEASE_LIMIT: Duration = Duration::from_secs(2);
/// Python 寫出改寫後 `LOCK_PATH` 的檔名(在暫存工作目錄下)。
const PY_LOCK_PATH: &str = "py_lock_path";

/// Python 子行程結束碼:0 為成立;`lockf` 角色以 14 表示「lockf 未取得或 flock 未被擋」。
/// 其餘(程式內以數字寫死):2 用法或匯入來源不符、10 不該取得卻取得、12 等不到訊號、13 取得時 `releasing`
/// 不存在或 `on_wait` 不是一次、20 `acquire_2s` 逾時;未攔下的例外為 1。
const PY_OK: i32 = 0;
/// 只有 Linux 限定的 (vii) 使用;macOS 上不編譯,避免 clippy `dead_code`(合併 PR #189 CI macOS 格)。
#[cfg(target_os = "linux")]
const PY_LOCKF_MISMATCH: i32 = 14;

/// Python 子行程程式。參數一律經 `sys.argv` 傳入,不內插進程式字串。
///
/// 角色:
/// - `hold`:以 30 秒逾時取鎖 → 寫 `ready` → 等 `waiting` 或 `release`(至多 60 秒)→ 寫 `releasing` → 離開 `with`。
/// - `try_short`:`heavy_lock(timeout=0.3)`,丟 `TimeoutError` 時印出並以 0 結束。
/// - `wait_long`:`heavy_lock(timeout=30, on_wait=寫 waiting)`,取得後確認 `releasing` 已存在、`on_wait` 恰一次。
/// - `acquire_2s`:`heavy_lock(timeout=2)` 取得。
/// - `lockf`:讀寫開啟同一鎖檔,`fcntl.lockf(LOCK_EX | LOCK_NB)` 應取得、`fcntl.flock(LOCK_EX | LOCK_NB)` 應被擋。
const PY_PROGRAM: &str = r#"
import fcntl, os, sys, time
from pathlib import Path

repo, top, work = (Path(a) for a in sys.argv[1:4])
role = sys.argv[4]
# 只從 repo 根目錄匯入產品模組:附加在 sys.path 末端(不遮蔽標準函式庫),匯入後核對來源
sys.path.append(str(repo))
import heavy_lock
import safe_fs
for mod in (heavy_lock, safe_fs):
    if Path(mod.__file__).resolve().parent != repo:
        print(f"匯入來源不符:{mod.__name__}", file=sys.stderr)
        sys.exit(2)
heavy_lock.ROOT = top
heavy_lock.LOCK_PATH = top / "outputs" / ".heavy.lock"
if not (top.is_relative_to(work) and heavy_lock.LOCK_PATH.is_relative_to(work)):
    print("LOCK_PATH 不在暫存工作目錄之下", file=sys.stderr)
    sys.exit(2)
(work / "py_lock_path").write_text(str(heavy_lock.LOCK_PATH), encoding="utf-8")
print(f"python {sys.version.split()[0]} role={role}", flush=True)


def mark(name):
    (work / name).write_text("", encoding="utf-8")


def has(name):
    return (work / name).exists()


def wait_any(names, limit):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if any(has(n) for n in names):
            return True
        time.sleep(0.005)
    return any(has(n) for n in names)


if role == "hold":
    try:
        with heavy_lock.heavy_lock(timeout=30):
            mark("ready")
            if not wait_any(["waiting", "release"], 60):
                sys.exit(12)
            mark("releasing")
    except TimeoutError:
        sys.exit(12)
    print("result=released")
    sys.exit(0)
elif role == "try_short":
    start = time.monotonic()
    try:
        with heavy_lock.heavy_lock(timeout=0.3):
            print("result=acquired")
            sys.exit(10)
    except TimeoutError as e:
        print(f"result=timeout ({type(e).__name__}) elapsed={time.monotonic() - start:.3f}s")
        sys.exit(0)
elif role == "wait_long":
    calls = []

    def on_wait():
        calls.append(1)
        mark("waiting")

    with heavy_lock.heavy_lock(timeout=30, on_wait=on_wait):
        seen = has("releasing")
    print(f"result=acquired releasing_seen={seen} on_wait_calls={len(calls)}")
    sys.exit(0 if seen and len(calls) == 1 else 13)
elif role == "acquire_2s":
    start = time.monotonic()
    try:
        with heavy_lock.heavy_lock(timeout=2):
            pass
    except TimeoutError:
        print(f"result=timeout elapsed={time.monotonic() - start:.3f}s")
        sys.exit(20)
    print(f"result=acquired elapsed={time.monotonic() - start:.3f}s")
    sys.exit(0)
elif role == "lockf":
    fd = os.open(heavy_lock.LOCK_PATH, os.O_RDWR | os.O_NOFOLLOW)
    try:
        try:
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lockf_ok = True
        except OSError:
            lockf_ok = False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            flock_ok = True
        except OSError:
            flock_ok = False
    finally:
        os.close(fd)
    print(f"result=lockf_acquired={lockf_ok} flock_acquired={flock_ok}")
    sys.exit(0 if lockf_ok and not flock_ok else 14)
else:
    sys.exit(2)
"#;

/// 直譯器:`PARITY_PYTHON`(空字串視為未提供),沒有時 `python3`(與 `crates/parity` 的 Unix 規則相同)。
fn python() -> OsString {
    std::env::var_os("PARITY_PYTHON")
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| OsString::from("python3"))
}

/// repo 根目錄(`CARGO_MANIFEST_DIR/../..`,`canonicalize` 過)。
fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .expect("無法取得 repo 根目錄")
}

/// 工作副本 `outputs/.heavy.lock` 的狀態(不跟隨連結)。
#[derive(Debug, PartialEq, Eq)]
enum RepoLock {
    Missing,
    Present {
        len: u64,
        mtime: i64,
        mtime_nsec: i64,
        ino: u64,
    },
}

fn repo_lock_state(repo: &Path) -> RepoLock {
    match std::fs::symlink_metadata(repo.join("outputs").join(".heavy.lock")) {
        Ok(m) => RepoLock::Present {
            len: m.len(),
            mtime: m.mtime(),
            mtime_nsec: m.mtime_nsec(),
            ino: m.ino(),
        },
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => RepoLock::Missing,
        Err(e) => panic!("讀取工作副本鎖檔狀態失敗:{e}"),
    }
}

/// 一個跨語言案例:暫存工作目錄、`canonicalize` 過的路徑與測試前的工作副本鎖檔狀態。
struct Case {
    name: &'static str,
    w: WorkDir,
    repo: PathBuf,
    work: PathBuf,
    top: PathBuf,
    before: RepoLock,
}

impl Case {
    fn new(name: &'static str) -> Case {
        let repo = repo_root();
        let before = repo_lock_state(&repo);
        println!("{name} 測試前工作副本 outputs/.heavy.lock:{before:?}");
        let w = WorkDir::new();
        let work = w.root().canonicalize().expect("canonicalize 工作目錄失敗");
        let top = w.top().canonicalize().expect("canonicalize top 失敗");
        Case {
            name,
            w,
            repo,
            work,
            top,
            before,
        }
    }

    /// 以角色 `role` 啟動 Python 子行程。啟動失敗即判失敗(不略過)。
    fn spawn(&self, role: &'static str) -> Py {
        let child = Command::new(python())
            .args(PY_FLAGS)
            .arg("-c")
            .arg(PY_PROGRAM)
            .arg(&self.repo)
            .arg(&self.top)
            .arg(&self.work)
            .arg(role)
            .current_dir(&self.work)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap_or_else(|e| panic!("無法啟動 Python 直譯器 {:?}(判失敗,不略過):{e}", python()));
        Py {
            child: Some(child),
            role,
        }
    }

    /// 斷言 Python 改寫後的 `LOCK_PATH` 位於暫存工作目錄之下,且工作副本鎖檔未被建立或修改。
    fn finish(self) {
        let written = std::fs::read_to_string(self.work.join(PY_LOCK_PATH))
            .expect("Python 未寫出改寫後的 LOCK_PATH");
        let lock_path = PathBuf::from(written);
        assert!(
            lock_path.starts_with(&self.work),
            "Python LOCK_PATH 不在暫存工作目錄之下"
        );
        assert_eq!(lock_path, self.top.join("outputs").join(".heavy.lock"));
        let after = repo_lock_state(&self.repo);
        println!(
            "{} 測試後工作副本 outputs/.heavy.lock:{after:?}(LOCK_PATH 位於暫存工作目錄之下)",
            self.name
        );
        assert_eq!(
            self.before, after,
            "工作副本 outputs/.heavy.lock 被建立或修改"
        );
    }
}

/// Python 子行程;`Drop` 時 kill 並回收,不留孤兒行程。
struct Py {
    child: Option<Child>,
    role: &'static str,
}

impl Py {
    fn child(&mut self) -> &mut Child {
        self.child.as_mut().expect("Python 子行程已回收")
    }

    /// 回收後讀出 stdout 與 stderr(輸出很短,結束後讀取不會塞住管線)。
    fn collect(child: &mut Child) -> String {
        let mut out = String::new();
        if let Some(mut s) = child.stdout.take() {
            let _ = s.read_to_string(&mut out);
        }
        if let Some(mut s) = child.stderr.take() {
            let mut err = String::new();
            let _ = s.read_to_string(&mut err);
            if !err.is_empty() {
                out.push_str("[stderr] ");
                out.push_str(&err);
            }
        }
        out
    }

    /// 等子行程結束,至多 `limit`;逾時判失敗(之後由 `Drop` kill)。回傳結束狀態與輸出。
    fn wait(&mut self, limit: Duration) -> (ExitStatus, String) {
        let start = Instant::now();
        loop {
            if let Some(status) = self.child().try_wait().expect("查詢 Python 子行程失敗") {
                let mut child = self.child.take().expect("Python 子行程已回收");
                let out = Self::collect(&mut child);
                println!(
                    "Python 子行程({})結束 {status:?},輸出:{}",
                    self.role,
                    out.trim()
                );
                return (status, out);
            }
            assert!(
                start.elapsed() < limit,
                "Python 子行程({})未在 {limit:?} 內結束",
                self.role
            );
            std::thread::sleep(Duration::from_millis(10));
        }
    }

    /// 等標記檔 `name` 出現,至多 `limit`;子行程先結束或逾時都判失敗。
    fn wait_marker(&mut self, case: &Case, name: &str, limit: Duration) {
        let start = Instant::now();
        loop {
            if case.w.has(name) {
                return;
            }
            if let Some(status) = self.child().try_wait().expect("查詢 Python 子行程失敗") {
                let mut child = self.child.take().expect("Python 子行程已回收");
                let out = Self::collect(&mut child);
                panic!(
                    "Python 子行程({})在寫出 {name} 前結束 {status:?}:{}",
                    self.role,
                    out.trim()
                );
            }
            assert!(
                start.elapsed() < limit,
                "Python 子行程({})未在 {limit:?} 內寫出 {name}",
                self.role
            );
            std::thread::sleep(Duration::from_millis(5));
        }
    }

    /// 終止子行程並等到回收(`Child::wait()` 返回)。
    fn kill_and_wait(&mut self) -> ExitStatus {
        let mut child = self.child.take().expect("Python 子行程已回收");
        child.kill().expect("無法終止 Python 子行程");
        child.wait().expect("無法回收 Python 子行程")
    }
}

impl Drop for Py {
    fn drop(&mut self) {
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[test]
fn python_interpreter_starts() {
    let out = Command::new(python())
        .args(PY_FLAGS)
        .args(["-c", "import sys; print(sys.version)"])
        .stdin(Stdio::null())
        .output()
        .unwrap_or_else(|e| panic!("無法啟動 Python 直譯器 {:?}(判失敗,不略過):{e}", python()));
    assert!(out.status.success(), "{out:?}");
    println!(
        "跨語言測試使用的 Python({:?}):{}",
        python(),
        String::from_utf8_lossy(&out.stdout).trim()
    );
}

#[test]
fn rust_times_out_while_python_holds() {
    // (i)
    let c = Case::new("(i)");
    let mut py = c.spawn("hold");
    py.wait_marker(&c, READY, START_LIMIT);
    let start = Instant::now();
    let r = acquire(&c.top, Some(SHORT_TIMEOUT));
    let elapsed = start.elapsed();
    println!("(i) Python 持鎖時 Rust 以 0.3 秒逾時取鎖:{r:?},耗時 {elapsed:?}");
    assert!(matches!(r, Err(Error::Timeout(_))), "{r:?}");
    assert!(elapsed >= SHORT_TIMEOUT, "{elapsed:?}");
    assert!(elapsed < TIMEOUT_UPPER, "{elapsed:?}");
    c.w.touch(RELEASE);
    let (status, _) = py.wait(RELEASE_LIMIT);
    assert_eq!(status.code(), Some(PY_OK), "{status:?}");
    c.finish();
}

#[test]
fn rust_waits_until_python_releases() {
    // (ii)
    let c = Case::new("(ii)");
    let mut py = c.spawn("hold");
    py.wait_marker(&c, READY, START_LIMIT);
    let count = AtomicUsize::new(0);
    let lock = acquire_with(&c.top, Some(RELEASE_LIMIT), || {
        count.fetch_add(1, Ordering::SeqCst);
        c.w.touch(WAITING);
    })
    .expect("Python 釋放後 Rust 應取得");
    let seen = c.w.has(RELEASING);
    let calls = count.load(Ordering::SeqCst);
    println!("(ii) Rust 在 Python 釋放後取得:releasing 已存在={seen},on_wait 次數={calls}");
    assert!(seen, "Rust 應在 Python 釋放後才取得");
    assert_eq!(calls, 1, "on_wait 應恰好一次");
    drop(lock);
    let (status, _) = py.wait(RELEASE_LIMIT);
    assert_eq!(status.code(), Some(PY_OK), "{status:?}");
    c.finish();
}

#[test]
fn python_times_out_while_rust_holds() {
    // (iii)
    let c = Case::new("(iii)");
    let lock = acquire(&c.top, None).expect("Rust 取鎖失敗");
    let mut py = c.spawn("try_short");
    let (status, out) = py.wait(RELEASE_LIMIT);
    println!(
        "(iii) Rust 持鎖時 Python heavy_lock(timeout=0.3):{}",
        out.trim()
    );
    assert_eq!(status.code(), Some(PY_OK), "Python 應丟 TimeoutError:{out}");
    assert!(out.contains("result=timeout"), "{out}");
    drop(lock);
    c.finish();
}

#[test]
fn python_waits_until_rust_releases() {
    // (iv)
    let c = Case::new("(iv)");
    let lock = acquire(&c.top, None).expect("Rust 取鎖失敗");
    let mut py = c.spawn("wait_long");
    py.wait_marker(&c, WAITING, START_LIMIT);
    c.w.touch(RELEASING);
    drop(lock);
    let (status, out) = py.wait(RELEASE_LIMIT);
    println!("(iv) Python 在 Rust 釋放後取得:{}", out.trim());
    assert_eq!(
        status.code(),
        Some(PY_OK),
        "Python 應在 Rust 釋放後才取得、on_wait 恰一次:{out}"
    );
    assert!(out.contains("releasing_seen=True"), "{out}");
    c.finish();
}

#[test]
fn rust_acquires_after_python_holder_is_killed() {
    // (v)
    let c = Case::new("(v)");
    let mut py = c.spawn("hold");
    py.wait_marker(&c, READY, START_LIMIT);
    let status = py.kill_and_wait();
    let start = Instant::now();
    let r = acquire(&c.top, Some(KILL_RELEASE_LIMIT));
    let elapsed = start.elapsed();
    println!(
        "(v) Python 持鎖子行程被終止({status:?})後 Rust 取得的實際耗時:{elapsed:?}(門檻 {KILL_RELEASE_LIMIT:?})"
    );
    let lock = r.expect("Python 被終止後 Rust 未在門檻內取得");
    assert!(elapsed < KILL_RELEASE_LIMIT, "{elapsed:?}");
    assert!(!c.w.has(RELEASING), "Python 應是被終止,而非自行釋放");
    drop(lock);
    c.finish();
}

#[test]
fn python_acquires_after_rust_holder_is_killed() {
    // (vi)
    let c = Case::new("(vi)");
    let mut helper = Helper::spawn("hold", &c.w);
    assert!(
        c.w.wait_any(&[READY], START_LIMIT),
        "Rust 持鎖輔助行程未在期限內取得鎖"
    );
    let status = helper.kill_and_wait();
    let start = Instant::now();
    let mut py = c.spawn("acquire_2s");
    let (py_status, out) = py.wait(RELEASE_LIMIT);
    println!(
        "(vi) Rust 持鎖輔助行程被終止({status:?})後 Python heavy_lock(timeout=2):{}(含 Python 啟動共 {:?})",
        out.trim(),
        start.elapsed()
    );
    assert_eq!(
        py_status.code(),
        Some(PY_OK),
        "Rust 被終止後 Python 應取得:{out}"
    );
    assert!(!c.w.has(RELEASING), "Rust 輔助行程應是被終止,而非自行釋放");
    c.finish();
}

/// (vii) Linux 限定的區辨測試:Linux 上 `flock` 與 POSIX 記錄鎖(`fcntl` / `lockf`)互不影響(事實 F),
/// 所以 Rust 持鎖時 Python `lockf` 可取得、`flock` 被擋,表示 Rust 不是以 POSIX 記錄鎖實作。
/// macOS 上兩者是否互相影響〔未知〕,不跑。
#[cfg(target_os = "linux")]
#[test]
fn linux_python_lockf_is_not_blocked_by_rust_lock() {
    let c = Case::new("(vii)");
    let lock = acquire(&c.top, None).expect("Rust 取鎖失敗");
    let mut py = c.spawn("lockf");
    let (status, out) = py.wait(RELEASE_LIMIT);
    println!(
        "(vii) Linux 區辨:Rust 持鎖時 Python 同一鎖檔 {}——lockf(POSIX 記錄鎖)可取得而 flock 被擋,\
         表示 Rust 持有的是 flock 鎖、不是 POSIX 記錄鎖",
        out.trim()
    );
    assert_eq!(
        status.code(),
        Some(PY_OK),
        "應為 lockf 可取得、flock 被擋(結束碼 {PY_LOCKF_MISMATCH} 表示不符):{out}"
    );
    drop(lock);
    c.finish();
}
