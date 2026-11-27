//! L1:同一行程內的取鎖語意(CHG-20261127-01 T1;三平台執行)。
//!
//! 對應 Python `tests/test_heavy_lock.py` 中鎖本身的測試(建立 `outputs/` 與鎖檔、例外時釋放、
//! 鎖檔或 `outputs/` 是連結時拒絕、四執行緒互斥、`on_wait` 呼叫次數、逾時),以及行為差異 D-1、D-2、D-4
//! 與交施工補充 (b)(`Error::Path`、`Error::Lock` 的 `Display` 不含暫存根目錄)。
//! 符號連結案例在 Windows 本機無權限時印出略過與略過數(CI 上判失敗,見 `common::symlink`)。
//! 等待相關案例一律以通道交握、斷言因果順序,不以睡眠長短推斷。

mod common;

use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::mpsc;
use std::time::{Duration, Instant};

use common::{RELEASE_LIMIT, START_LIMIT, WorkDir, snapshot, symlink};
use heavy_lock::{Error, LOCK_DIR, LOCK_NAME, POLL_INTERVAL, acquire, acquire_with};
use safe_fs::ErrorKind;

fn lock_len(w: &WorkDir) -> u64 {
    std::fs::symlink_metadata(w.lock_path())
        .expect("鎖檔不存在")
        .len()
}

fn assert_unsafe(r: Result<heavy_lock::HeavyLock, Error>, what: &str) -> Error {
    match r {
        Err(e @ Error::Path(_)) => {
            let Error::Path(inner) = &e else {
                unreachable!()
            };
            assert_eq!(inner.kind(), ErrorKind::Unsafe, "{what}:{e}");
            e
        }
        Err(e) => panic!("{what}:應為 Error::Path(Unsafe),實際為 {e:?}"),
        Ok(_) => panic!("{what}:應被拒絕,卻取得鎖"),
    }
}

fn root_str(w: &WorkDir) -> String {
    w.root().to_string_lossy().into_owned()
}

#[test]
fn constants_match_python() {
    assert_eq!(LOCK_DIR, "outputs");
    assert_eq!(LOCK_NAME, ".heavy.lock");
    assert_eq!(POLL_INTERVAL, Duration::from_millis(50));
}

#[test]
fn creates_outputs_and_empty_lock_file_then_reacquires() {
    // D-2:鎖檔只會在 root/outputs/.heavy.lock
    let w = WorkDir::new();
    let lock = acquire(&w.top(), None).expect("第一次取鎖失敗");
    assert!(w.top().join("outputs").is_dir(), "outputs/ 未建立");
    assert!(
        std::fs::symlink_metadata(w.lock_path())
            .expect("鎖檔未建立")
            .is_file()
    );
    assert_eq!(lock_len(&w), 0, "鎖檔應為空檔");
    drop(lock);
    let again = acquire(&w.top(), Some(Duration::from_millis(500))).expect("釋放後無法再取得");
    drop(again);
    // 取得與釋放後鎖檔仍存在且長度 0(不讀不寫不刪)
    assert_eq!(lock_len(&w), 0);
    let names: Vec<_> = std::fs::read_dir(w.top().join("outputs"))
        .unwrap()
        .map(|e| e.unwrap().file_name())
        .collect();
    assert_eq!(names, vec![std::ffi::OsString::from(".heavy.lock")]);
}

#[test]
fn released_after_panic() {
    let w = WorkDir::new();
    let top = w.top();
    let r = catch_unwind(AssertUnwindSafe(|| {
        let _lock = acquire(&top, None).expect("取鎖失敗");
        panic!("持鎖中的 panic(測試刻意觸發)");
    }));
    assert!(r.is_err());
    let again = acquire(&top, Some(Duration::from_millis(500))).expect("panic 後鎖未釋放");
    drop(again);
    assert_eq!(lock_len(&w), 0);
}

#[test]
fn lock_file_symlink_rejected_and_target_unchanged() {
    let w = WorkDir::new();
    std::fs::create_dir(w.top().join("outputs")).unwrap();
    let target = w.outside().join("marker.txt");
    if !symlink(&target, &w.lock_path(), false) {
        return;
    }
    let before = snapshot(&w.outside());
    let e = assert_unsafe(acquire(&w.top(), Some(Duration::ZERO)), "鎖檔是連結");
    assert_eq!(std::fs::read_to_string(&target).unwrap(), "outside");
    assert_eq!(snapshot(&w.outside()), before);
    // 交施工補充 (b):Error::Path 的 Display 不含暫存根目錄
    assert!(!e.to_string().contains(&root_str(&w)), "{e}");
}

#[test]
fn outputs_symlink_to_outside_rejected() {
    let w = WorkDir::new();
    if !symlink(&w.outside(), &w.top().join("outputs"), true) {
        return;
    }
    let before = snapshot(&w.outside());
    let e = assert_unsafe(acquire(&w.top(), None), "outputs/ 指向外部");
    assert_eq!(snapshot(&w.outside()), before, "外部目錄內不得建立任何東西");
    assert!(!e.to_string().contains(&root_str(&w)), "{e}");
}

#[test]
fn outputs_symlink_to_inside_rejected() {
    let w = WorkDir::new();
    let inner = w.top().join("inner");
    std::fs::create_dir(&inner).unwrap();
    if !symlink(&inner, &w.top().join("outputs"), true) {
        return;
    }
    let e = assert_unsafe(acquire(&w.top(), None), "outputs/ 指向內部");
    assert!(snapshot(&inner).is_empty(), "內部目錄內不得建立任何東西");
    assert!(!e.to_string().contains(&root_str(&w)), "{e}");
}

#[test]
fn outputs_regular_file_rejected() {
    let w = WorkDir::new();
    std::fs::write(w.top().join("outputs"), "not a dir").unwrap();
    let e = assert_unsafe(acquire(&w.top(), None), "outputs 是一般檔");
    assert_eq!(
        std::fs::read_to_string(w.top().join("outputs")).unwrap(),
        "not a dir"
    );
    assert!(!e.to_string().contains(&root_str(&w)), "{e}");
}

#[test]
fn four_threads_are_mutually_exclusive() {
    let w = WorkDir::new();
    let top = w.top();
    let inside = Arc::new(AtomicUsize::new(0));
    let max = Arc::new(AtomicUsize::new(0));
    let entries = Arc::new(AtomicUsize::new(0));
    let barrier = Arc::new(std::sync::Barrier::new(4));
    let handles: Vec<_> = (0..4)
        .map(|_| {
            let (top, inside, max, entries, barrier) = (
                top.clone(),
                inside.clone(),
                max.clone(),
                entries.clone(),
                barrier.clone(),
            );
            std::thread::spawn(move || {
                barrier.wait();
                for _ in 0..5 {
                    let lock = acquire(&top, Some(RELEASE_LIMIT)).expect("取鎖失敗");
                    let now = inside.fetch_add(1, Ordering::SeqCst) + 1;
                    max.fetch_max(now, Ordering::SeqCst);
                    entries.fetch_add(1, Ordering::SeqCst);
                    std::thread::sleep(Duration::from_millis(5));
                    inside.fetch_sub(1, Ordering::SeqCst);
                    drop(lock);
                }
            })
        })
        .collect();
    for h in handles {
        h.join().expect("執行緒 panic");
    }
    assert_eq!(entries.load(Ordering::SeqCst), 20);
    assert_eq!(max.load(Ordering::SeqCst), 1, "持鎖區間計數超過 1");
}

#[test]
fn nested_on_same_thread_times_out() {
    // 代決 8:同一執行緒巢狀取鎖會等到自己;有逾時時回 Timeout
    let w = WorkDir::new();
    let _outer = acquire(&w.top(), None).expect("外層取鎖失敗");
    match acquire(&w.top(), Some(Duration::from_millis(200))) {
        Err(Error::Timeout(_)) => {}
        other => panic!("巢狀取鎖應回 Timeout,實際為 {other:?}"),
    }
}

#[test]
fn on_wait_not_called_without_waiting() {
    let w = WorkDir::new();
    let called = AtomicBool::new(false);
    let lock = acquire_with(&w.top(), None, || called.store(true, Ordering::SeqCst))
        .expect("取鎖失敗");
    drop(lock);
    assert!(!called.load(Ordering::SeqCst), "沒有等待卻呼叫了 on_wait");
}

/// 另一執行緒持鎖:取得後送 `ready`,收到 `waiting`(至多 RELEASE_LIMIT)後先設 `releasing` 再釋放。
fn spawn_holder(
    top: std::path::PathBuf,
) -> (
    std::thread::JoinHandle<bool>,
    mpsc::Sender<()>,
    Arc<AtomicBool>,
) {
    let (ready_tx, ready_rx) = mpsc::channel();
    let (wait_tx, wait_rx) = mpsc::channel::<()>();
    let releasing = Arc::new(AtomicBool::new(false));
    let rel = releasing.clone();
    let h = std::thread::spawn(move || {
        let lock = acquire(&top, Some(START_LIMIT)).expect("持鎖執行緒取鎖失敗");
        ready_tx.send(()).unwrap();
        let signaled = wait_rx.recv_timeout(RELEASE_LIMIT).is_ok();
        if signaled {
            rel.store(true, Ordering::SeqCst);
        }
        drop(lock);
        signaled
    });
    ready_rx
        .recv_timeout(START_LIMIT)
        .expect("持鎖執行緒未在期限內取得鎖");
    (h, wait_tx, releasing)
}

#[test]
fn on_wait_called_once_before_acquiring() {
    let w = WorkDir::new();
    let (h, wait_tx, releasing) = spawn_holder(w.top());
    let count = AtomicUsize::new(0);
    let lock = acquire_with(&w.top(), Some(RELEASE_LIMIT), || {
        count.fetch_add(1, Ordering::SeqCst);
        wait_tx.send(()).unwrap();
    })
    .expect("持鎖方釋放後應取得");
    assert!(
        releasing.load(Ordering::SeqCst),
        "應在持鎖方釋放(on_wait 之後)才取得"
    );
    assert_eq!(count.load(Ordering::SeqCst), 1, "on_wait 應恰好一次");
    drop(lock);
    assert!(h.join().unwrap());
}

#[test]
fn zero_timeout_when_held_returns_timeout() {
    let w = WorkDir::new();
    let _held = acquire(&w.top(), None).expect("取鎖失敗");
    let count = AtomicUsize::new(0);
    match acquire_with(&w.top(), Some(Duration::ZERO), || {
        count.fetch_add(1, Ordering::SeqCst);
    }) {
        Err(Error::Timeout(_)) => {}
        other => panic!("timeout = 0 被占用時應回 Timeout,實際為 {other:?}"),
    }
    assert_eq!(count.load(Ordering::SeqCst), 1);
}

#[test]
fn duration_max_does_not_panic_and_acquires_after_release() {
    // D-4:期限計算溢位視同不逾時
    let w = WorkDir::new();
    let (h, wait_tx, releasing) = spawn_holder(w.top());
    let lock = acquire_with(&w.top(), Some(Duration::MAX), || {
        wait_tx.send(()).unwrap();
    })
    .expect("Duration::MAX 應一直等到取得");
    assert!(releasing.load(Ordering::SeqCst));
    drop(lock);
    assert!(h.join().unwrap());
}

#[test]
fn timeout_waits_at_least_the_timeout() {
    let w = WorkDir::new();
    let _held = acquire(&w.top(), None).expect("取鎖失敗");
    let start = Instant::now();
    let r = acquire(&w.top(), Some(Duration::from_millis(300)));
    let elapsed = start.elapsed();
    assert!(matches!(r, Err(Error::Timeout(_))), "{r:?}");
    assert!(elapsed >= Duration::from_millis(300), "{elapsed:?}");
    assert!(elapsed < Duration::from_secs(10), "{elapsed:?}");
}

#[test]
fn error_display_has_no_local_path() {
    // D-1 與交施工補充 (b)
    let w = WorkDir::new();
    let root = root_str(&w);
    let _held = acquire(&w.top(), None).expect("取鎖失敗");
    let e = acquire(&w.top(), Some(Duration::from_millis(100))).expect_err("應逾時");
    let msg = e.to_string();
    assert!(matches!(e, Error::Timeout(_)));
    assert!(!msg.contains(&root), "{msg}");
    assert!(msg.contains("秒"), "{msg}");

    // 根目錄不存在 → Error::Path(NotFound),訊息不含路徑
    let missing = w.root().join("missing");
    let e = acquire(&missing, None).expect_err("根目錄不存在應失敗");
    match &e {
        Error::Path(inner) => assert_eq!(inner.kind(), ErrorKind::NotFound, "{e}"),
        other => panic!("應為 Error::Path,實際為 {other:?}"),
    }
    assert!(!e.to_string().contains(&root), "{e}");

    // Error::Lock 無法經公開 API 穩定觸發(try_lock 回 WouldBlock 以外的錯誤):直接建構,
    // 底層錯誤訊息刻意放入路徑,斷言 Display 不轉出底層訊息。
    let e = Error::Lock(std::io::Error::other(format!("含路徑 {root}")));
    assert!(!e.to_string().contains(&root), "{e}");
}
