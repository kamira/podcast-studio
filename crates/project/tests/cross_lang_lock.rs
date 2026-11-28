//! L2:專案鎖的 Python 對 Rust 跨語言互斥(CHG-20261128-01 T5,退出條件 (3);只在 Unix 編譯執行,即 macOS / Linux)。
//!
//! 證明 Rust `ProjectLock` 與 repo 根目錄 `project.py` 的 `_project_lock`(唯讀 import,不改)互斥,也就是 Rust 在
//! Unix 上是 `flock` 語意(路線圖代決 13;事實 J 由本檔證明)。`project.py` 頂層 import `fcntl`,Windows 無法執行。
//!
//! - Python 子行程:argv 清單 `[<python>, -I, -B, -X, utf8, -c, <程式>, <repo>, <top>, <work>, <專案名稱>, <角色>]`,
//!   不經 shell;直譯器取 `PARITY_PYTHON`(空字串視為未提供),沒有時 `python3`。程式只把 repo 根目錄附加在
//!   `sys.path` 末端,匯入後核對 `project`、`names`、`safe_fs`、`value_checks`、`breezy_worker` 的 `__file__` 來自
//!   repo 根目錄,再把 `project.ROOT` 改指暫存資料根目錄(`canonicalize` 過)。Python 取鎖一律走
//!   `project.open_project(名稱)` + `project._project_lock(pfd)`,與網頁版存檔相同。
//! - 每個案例:斷言 Python 算出的鎖檔位置位於暫存工作目錄之下;測試前後 repo 工作副本的 `projects/`(項目清單與
//!   mtime)與其中每個 `projects/*/.lock` 的狀態(存在與否、長度、mtime、inode)不變。
//! - Python 無法啟動時測試判失敗,不略過。
//! - 一律以標記檔交握、斷言因果順序(代決 11):取鎖方在 0.5 秒觀察期內沒有返回,且對方寫出 `releasing` 後才返回、
//!   返回時 `releasing` 已存在;其他時間斷言只有 Python 被終止後 Rust 2 秒內取得。
#![cfg(unix)]

mod common;

use std::ffi::OsString;
use std::io::Read;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::time::{Duration, Instant};

use common::{
    ACQUIRED, LOCK_PROJECT, LockThread, LockWork, OBSERVE, ProjectsGuard, READY, RELEASE,
    RELEASE_LIMIT, RELEASING, START_LIMIT, WAITING, lock_project, python, repo_root,
};

/// Python 直譯器旗標:隔離模式、不寫 `.pyc`、UTF-8 模式。
const PY_FLAGS: [&str; 4] = ["-I", "-B", "-X", "utf8"];
/// Python 被終止後 Rust 取得的門檻(與 heavy-lock 跨語言測試相同)。
const KILL_RELEASE_LIMIT: Duration = Duration::from_secs(2);
/// Python 寫出其鎖檔位置的檔名(在暫存工作目錄下)。
const PY_LOCK_PATH: &str = "py_lock_path";
/// Python 子行程結束碼:0 為成立(未攔下的例外為 1;其餘以數字寫死在程式內:2 用法或匯入來源不符、12 等不到訊號、
/// 13 取得時 `releasing` 不存在、14 區辨不符)。
const PY_OK: i32 = 0;
/// 只有 Linux 限定的區辨測試使用;macOS 上不編譯,避免 clippy `dead_code`(CHG-20261127-01 合併 PR 教訓)。
#[cfg(target_os = "linux")]
const PY_LOCKF_MISMATCH: i32 = 14;

/// Python 子行程程式。參數一律經 `sys.argv` 傳入,不內插進程式字串。
///
/// 角色:
/// - `hold`:`open_project` + `_project_lock` → 寫 `ready` → 等 `waiting` 或 `release`(至多 60 秒)→ 寫 `releasing`
///   → 離開 `with`。
/// - `wait`:寫 `waiting` → `open_project` + `_project_lock`(阻塞)→ 取得時確認 `releasing` 已存在、寫 `acquired`。
/// - `lockf`:讀寫開啟同一鎖檔,`fcntl.lockf(LOCK_EX | LOCK_NB)` 應取得、`fcntl.flock(LOCK_EX | LOCK_NB)` 應被擋。
const PY_PROGRAM: &str = r#"
import fcntl, os, sys, time
from pathlib import Path

repo, top, work = (Path(a) for a in sys.argv[1:4])
name, role = sys.argv[4], sys.argv[5]
# 只從 repo 根目錄匯入產品模組:附加在 sys.path 末端(不遮蔽標準函式庫),匯入後核對來源
sys.path.append(str(repo))
import project as pj, names, safe_fs, value_checks, breezy_worker
for mod in (pj, names, safe_fs, value_checks, breezy_worker):
    if Path(mod.__file__).resolve().parent != repo:
        print(f"匯入來源不符:{mod.__name__}", file=sys.stderr)
        sys.exit(2)
pj.ROOT = top
lock_path = pj._top() / pj.PROJECTS_NAME / name / pj.LOCK_FILE
if not (pj._top().is_relative_to(work) and lock_path.is_relative_to(work)):
    print("鎖檔位置不在暫存工作目錄之下", file=sys.stderr)
    sys.exit(2)
(work / "py_lock_path").write_text(str(lock_path), encoding="utf-8")
print(f"python {sys.version.split()[0]} role={role}", flush=True)


def mark(n):
    (work / n).write_text("", encoding="utf-8")


def has(n):
    return (work / n).exists()


def wait_any(ns, limit):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if any(has(n) for n in ns):
            return True
        time.sleep(0.005)
    return any(has(n) for n in ns)


if role == "hold":
    with safe_fs.closing(pj.open_project(name)) as pfd, pj._project_lock(pfd):
        mark("ready")
        if not wait_any(["waiting", "release"], 60):
            sys.exit(12)
        mark("releasing")
    print("result=released")
    sys.exit(0)
elif role == "wait":
    mark("waiting")
    start = time.monotonic()
    with safe_fs.closing(pj.open_project(name)) as pfd, pj._project_lock(pfd):
        seen = has("releasing")
        if seen:
            mark("acquired")
    print(f"result=acquired releasing_seen={seen} elapsed={time.monotonic() - start:.3f}s")
    sys.exit(0 if seen else 13)
elif role == "lockf":
    fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW)
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

/// 一個 `.lock` 的狀態(不跟隨連結):長度、mtime(秒、奈秒)、inode。
type LockState = (u64, i64, i64, u64);

/// repo 工作副本 `projects/*/.lock` 的狀態(依專案目錄名稱排序;`projects/` 不存在為空清單)。
fn repo_lock_states(repo: &Path) -> Vec<(OsString, Option<LockState>)> {
    let Ok(entries) = std::fs::read_dir(repo.join("projects")) else {
        return Vec::new();
    };
    let mut out: Vec<_> = entries
        .map(|e| {
            let e = e.expect("目錄項");
            let state = std::fs::symlink_metadata(e.path().join(".lock"))
                .ok()
                .map(|m| (m.len(), m.mtime(), m.mtime_nsec(), m.ino()));
            (e.file_name(), state)
        })
        .collect();
    out.sort();
    out
}

/// 一個跨語言案例:暫存工作目錄、`canonicalize` 過的路徑與測試前的工作副本狀態。
struct Case {
    name: &'static str,
    w: LockWork,
    repo: PathBuf,
    before: Vec<(OsString, Option<LockState>)>,
    _guard: ProjectsGuard,
}

impl Case {
    fn new(name: &'static str) -> Case {
        let guard = ProjectsGuard::new();
        let repo = repo_root();
        let before = repo_lock_states(&repo);
        println!("{name} 測試前工作副本 projects/*/.lock:{before:?}");
        Case {
            name,
            w: LockWork::new(),
            repo,
            before,
            _guard: guard,
        }
    }

    /// 以角色 `role` 啟動 Python 子行程。啟動失敗即判失敗(不略過)。
    fn spawn(&self, role: &'static str) -> Py {
        let child = Command::new(python())
            .args(PY_FLAGS)
            .arg("-c")
            .arg(PY_PROGRAM)
            .arg(&self.repo)
            .arg(self.w.top())
            .arg(self.w.root())
            .arg(LOCK_PROJECT)
            .arg(role)
            .current_dir(self.w.root())
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

    /// 斷言 Python 的鎖檔位置位於暫存工作目錄之下,且工作副本的鎖檔未被建立或修改。
    fn finish(self) {
        let written = std::fs::read_to_string(self.w.root().join(PY_LOCK_PATH))
            .expect("Python 未寫出鎖檔位置");
        let lock_path = PathBuf::from(written);
        assert!(
            lock_path.starts_with(self.w.root()),
            "Python 鎖檔位置不在暫存工作目錄之下"
        );
        assert_eq!(lock_path, self.w.lock_path(), "Python 與 Rust 應鎖同一個檔");
        let after = repo_lock_states(&self.repo);
        println!(
            "{} 測試後工作副本 projects/*/.lock:{after:?}(Python 鎖檔位於暫存工作目錄之下)",
            self.name
        );
        assert_eq!(self.before, after, "工作副本 projects/*/.lock 被建立或修改");
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

    fn running(&mut self) -> bool {
        self.child()
            .try_wait()
            .expect("查詢 Python 子行程失敗")
            .is_none()
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
fn rust_waits_while_python_holds() {
    let c = Case::new("(i)");
    let mut py = c.spawn("hold");
    py.wait_marker(&c, READY, START_LIMIT);
    let t = LockThread::spawn(&c.w);
    let early = t.wait(OBSERVE);
    assert!(
        early.is_none(),
        "Python 持鎖時,Rust 取鎖不應在 {OBSERVE:?} 觀察期內返回"
    );
    c.w.touch(RELEASE);
    let (r, seen, elapsed) = t.wait(RELEASE_LIMIT).expect("Python 釋放後 Rust 未取得");
    println!(
        "(i) Python 持鎖時 Rust 取鎖:觀察期 {OBSERVE:?} 內未返回;Python 寫出 releasing 並釋放後取得,\
         releasing 已存在={seen},自開始耗時 {elapsed:?}"
    );
    let lock = r.expect("Python 釋放後 Rust 應取得");
    assert!(seen, "Rust 應在 Python 寫出 releasing 並釋放後才取得");
    drop(lock);
    let (status, out) = py.wait(RELEASE_LIMIT);
    assert_eq!(status.code(), Some(PY_OK), "{out}");
    assert!(out.contains("result=released"), "{out}");
    c.finish();
}

#[test]
fn python_waits_while_rust_holds() {
    let c = Case::new("(ii)");
    let lock = lock_project(&c.w.top()).expect("Rust 取鎖失敗");
    let mut py = c.spawn("wait");
    py.wait_marker(&c, WAITING, START_LIMIT);
    let start = Instant::now();
    std::thread::sleep(OBSERVE);
    let running = py.running();
    let acquired_early = c.w.has(ACQUIRED);
    println!(
        "(ii) Rust 持鎖時 Python 取鎖:觀察期 {OBSERVE:?} 後 Python 仍在等待={running},已取得={acquired_early}"
    );
    assert!(running, "Rust 持鎖時,Python 不應在觀察期內結束");
    assert!(!acquired_early, "Rust 持鎖時,Python 不應取得");
    c.w.touch(RELEASING);
    drop(lock);
    let (status, out) = py.wait(RELEASE_LIMIT);
    println!(
        "(ii) Rust 寫出 releasing 並釋放後 Python 取得:{},自觀察期開始耗時 {:?}",
        out.trim(),
        start.elapsed()
    );
    assert_eq!(
        status.code(),
        Some(PY_OK),
        "Python 應在 Rust 寫出 releasing 並釋放後才取得:{out}"
    );
    assert!(out.contains("releasing_seen=True"), "{out}");
    assert!(c.w.has(ACQUIRED));
    c.finish();
}

#[test]
fn rust_acquires_after_python_holder_is_killed() {
    let c = Case::new("(iii)");
    let mut py = c.spawn("hold");
    py.wait_marker(&c, READY, START_LIMIT);
    let status = py.kill_and_wait();
    let start = Instant::now();
    let t = LockThread::spawn(&c.w);
    let got = t.wait(KILL_RELEASE_LIMIT);
    let elapsed = start.elapsed();
    println!(
        "(iii) Python 持鎖子行程被終止({status:?})後 Rust 取得的實際耗時:{elapsed:?}(門檻 {KILL_RELEASE_LIMIT:?})"
    );
    let (r, _, _) = got.expect("Python 被終止後 Rust 未在門檻內取得");
    let lock = r.expect("Python 被終止後 Rust 應取得");
    assert!(!c.w.has(RELEASING), "Python 應是被終止,而非自行釋放");
    drop(lock);
    c.finish();
}

/// Linux 限定的區辨測試:Linux 上 `flock` 與 POSIX 記錄鎖(`fcntl` / `lockf`)互不影響,所以 Rust 持鎖時 Python
/// `lockf` 可取得、`flock` 被擋,表示 Rust 持有的是 `flock` 鎖、不是 POSIX 記錄鎖。macOS 上兩者是否互相影響〔未知〕,不跑。
#[cfg(target_os = "linux")]
#[test]
fn linux_python_lockf_is_not_blocked_by_rust_lock() {
    let c = Case::new("(iv)");
    let lock = lock_project(&c.w.top()).expect("Rust 取鎖失敗");
    let mut py = c.spawn("lockf");
    let (status, out) = py.wait(RELEASE_LIMIT);
    println!(
        "(iv) Linux 區辨:Rust 持鎖時 Python 同一鎖檔 {}——lockf(POSIX 記錄鎖)可取得而 flock 被擋,\
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
