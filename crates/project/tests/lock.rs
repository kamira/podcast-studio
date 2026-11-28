//! L1:專案鎖 `ProjectLock`(CHG-20261128-01 T5;`project::lock`,三平台)。
//!
//! 對應 Python `_project_lock(pfd)`:`safe-fs` `open_or_create(".lock", ReadWrite { truncate: false }, 0o600)` 取得檔案
//! handle 後以阻塞的 `File::lock()` 上鎖,`Drop` 時先 `unlock` 再關閉(代決 11)。本檔鎖住:建立 `.lock`(長度 0;
//! Unix 權限 0600,行為差異 D-14)、離開後可再取得、`catch_unwind` 攔下 panic 後可再取得、鎖檔是連結或目錄 →
//! `UnsafePath`(訊息與 Python 逐字相同)且連結目標不變、同行程不同執行緒互斥(另一執行緒在 0.5 秒觀察期內不返回、
//! 對方寫出 `releasing` 後才返回)、四執行緒互斥(共享計數器不超過 1)、鎖檔取得與釋放後仍在且為空。
//!
//! 取鎖一律在另一執行緒以觀察期等待([`common::LockThread`]),錯誤時判失敗而不是讓測試永遠卡住。夾具只建在測試
//! 自己排他建立的暫存目錄;每個案例開頭與結尾確認 repo 工作副本的 `projects/` 不變。Windows 無權限建立符號連結時,
//! 連結案例先 `assert!(!in_ci())` 再印略過數。

mod common;

use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use common::{
    LockThread, LockWork, OBSERVE, ProjectsGuard, RELEASE_LIMIT, RELEASING, in_ci, lock_project,
};
use project::ErrorKind;
use project::lock::{LOCK_UNSAFE, ProjectLock};

static SKIPPED: AtomicUsize = AtomicUsize::new(0);

/// 在另一執行緒取鎖,至多等 [`RELEASE_LIMIT`];卡住或失敗即判失敗。
fn acquire_in_thread(w: &LockWork) -> ProjectLock {
    let t = LockThread::spawn(w);
    let (r, _, elapsed) = t.wait(RELEASE_LIMIT).expect("取鎖未在期限內返回");
    println!("取鎖耗時 {elapsed:?}");
    r.expect("取鎖失敗")
}

fn skip_symlink() {
    assert!(!in_ci(), "CI 上無法建立符號連結(無權限),判失敗");
    let n = SKIPPED.fetch_add(1, Ordering::Relaxed) + 1;
    println!("略過:無權限建立符號連結(本測試執行檔累計略過 {n} 個案例)");
}

#[test]
fn creates_empty_lock_file() {
    let _g = ProjectsGuard::new();
    let w = LockWork::new();
    assert!(!w.lock_path().exists(), "取鎖前不應有 .lock");
    let lock = acquire_in_thread(&w);
    let meta = std::fs::symlink_metadata(w.lock_path()).expect("取鎖後應有 .lock");
    assert!(meta.is_file(), "{meta:?}");
    assert_eq!(meta.len(), 0, "鎖檔應沒有內容");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = meta.permissions().mode() & 0o777;
        println!(".lock 權限位元:{mode:o}");
        assert_eq!(mode, 0o600, "Unix 上 .lock 權限應為 0600(與 Python 相同)");
    }
    drop(lock);
    let meta = std::fs::symlink_metadata(w.lock_path()).expect("釋放後 .lock 應仍在");
    assert!(meta.is_file(), "{meta:?}");
    assert_eq!(meta.len(), 0, "釋放後鎖檔應仍沒有內容");
}

#[test]
fn reacquire_after_drop() {
    let _g = ProjectsGuard::new();
    let w = LockWork::new();
    let first = acquire_in_thread(&w);
    drop(first);
    let second = acquire_in_thread(&w);
    drop(second);
    // 既有的空 .lock 沿用(不截斷、不重建)
    let meta = std::fs::symlink_metadata(w.lock_path()).expect(".lock 應仍在");
    assert_eq!(meta.len(), 0);
}

#[test]
fn reacquire_after_panic_is_caught() {
    let _g = ProjectsGuard::new();
    let w = LockWork::new();
    let top = w.top();
    let r = catch_unwind(AssertUnwindSafe(|| {
        let _lock = lock_project(&top).expect("取鎖失敗");
        panic!("持鎖期間的 panic(測試刻意觸發)");
    }));
    assert!(r.is_err(), "應攔下 panic");
    let lock = acquire_in_thread(&w);
    drop(lock);
}

#[test]
fn other_thread_waits_until_release() {
    // 同一行程不同執行緒:每次取鎖各開新的 File,彼此互斥(代決 11)
    let _g = ProjectsGuard::new();
    let w = LockWork::new();
    let held = acquire_in_thread(&w);
    let t = LockThread::spawn(&w);
    assert!(
        t.wait(OBSERVE).is_none(),
        "另一執行緒持鎖時,取鎖執行緒不應在 {OBSERVE:?} 觀察期內返回"
    );
    w.touch(RELEASING);
    drop(held);
    let (r, seen, elapsed) = t.wait(RELEASE_LIMIT).expect("釋放後取鎖執行緒未返回");
    println!("同行程另一執行緒在釋放後取得:releasing 已存在={seen},自開始耗時 {elapsed:?}");
    let lock = r.expect("釋放後應取得");
    assert!(seen, "應在對方寫出 releasing 並釋放後才取得");
    assert!(elapsed >= OBSERVE, "{elapsed:?}");
    drop(lock);
}

#[test]
fn four_threads_are_mutually_exclusive() {
    const THREADS: usize = 4;
    const ROUNDS: usize = 25;
    let _g = ProjectsGuard::new();
    let w = LockWork::new();
    let inside = Arc::new(AtomicUsize::new(0));
    let max_seen = Arc::new(AtomicUsize::new(0));
    let total = Arc::new(AtomicUsize::new(0));
    let handles: Vec<_> = (0..THREADS)
        .map(|_| {
            let top = w.top();
            let inside = Arc::clone(&inside);
            let max_seen = Arc::clone(&max_seen);
            let total = Arc::clone(&total);
            std::thread::spawn(move || {
                for _ in 0..ROUNDS {
                    let lock = lock_project(&top).expect("取鎖失敗");
                    let now = inside.fetch_add(1, Ordering::SeqCst) + 1;
                    max_seen.fetch_max(now, Ordering::SeqCst);
                    std::thread::sleep(Duration::from_millis(1));
                    total.fetch_add(1, Ordering::SeqCst);
                    inside.fetch_sub(1, Ordering::SeqCst);
                    drop(lock);
                }
            })
        })
        .collect();
    for h in handles {
        h.join().expect("取鎖執行緒 panic");
    }
    let max = max_seen.load(Ordering::SeqCst);
    println!(
        "四執行緒各取鎖 {ROUNDS} 次:同時在鎖內的最大數 {max},合計 {}",
        total.load(Ordering::SeqCst)
    );
    assert_eq!(max, 1, "同時在鎖內的執行緒不得超過 1");
    assert_eq!(total.load(Ordering::SeqCst), THREADS * ROUNDS);
    let meta = std::fs::symlink_metadata(w.lock_path()).expect(".lock 應仍在");
    assert_eq!(meta.len(), 0, "鎖檔應沒有內容");
}

fn assert_unsafe(w: &LockWork) {
    let dir = project::read::open_project(&w.top(), common::LOCK_PROJECT).expect("開啟專案");
    let e = ProjectLock::acquire(&dir).expect_err("鎖檔不是一般檔時應拒絕");
    assert_eq!(e.kind(), ErrorKind::UnsafePath, "{e:?}");
    assert_eq!(e.errors(), [LOCK_UNSAFE]);
    assert_eq!(e.to_string(), "無法取得專案鎖(鎖檔可能是符號連結)");
}

#[test]
fn lock_file_symlink_to_file_is_rejected() {
    let _g = ProjectsGuard::new();
    let w = LockWork::new();
    let target = w.tree.join("outside/marker.txt");
    if !w.tree.symlink(
        &format!("top/projects/{}/.lock", common::LOCK_PROJECT),
        &target,
        false,
    ) {
        skip_symlink();
        return;
    }
    let before = w.tree.snapshot();
    assert_unsafe(&w);
    assert_eq!(w.tree.snapshot(), before, "連結與其目標都不得改變");
    assert_eq!(std::fs::read(&target).expect("讀取目標"), b"outside");
}

#[test]
fn lock_file_dangling_symlink_is_rejected() {
    let _g = ProjectsGuard::new();
    let w = LockWork::new();
    let target = w.tree.join("outside/missing.txt");
    if !w.tree.symlink(
        &format!("top/projects/{}/.lock", common::LOCK_PROJECT),
        &target,
        false,
    ) {
        skip_symlink();
        return;
    }
    let before = w.tree.snapshot();
    assert_unsafe(&w);
    assert_eq!(w.tree.snapshot(), before, "不得經由連結建立目標");
    assert!(!target.exists(), "不得經由連結建立目標");
}

#[test]
fn lock_file_symlink_to_dir_is_rejected() {
    let _g = ProjectsGuard::new();
    let w = LockWork::new();
    let target = w.tree.join("outside");
    if !w.tree.symlink(
        &format!("top/projects/{}/.lock", common::LOCK_PROJECT),
        &target,
        true,
    ) {
        skip_symlink();
        return;
    }
    let before = w.tree.snapshot();
    assert_unsafe(&w);
    assert_eq!(w.tree.snapshot(), before, "連結與其目標都不得改變");
}

#[test]
fn lock_file_directory_is_rejected() {
    let _g = ProjectsGuard::new();
    let w = LockWork::new();
    w.tree
        .dir(&format!("top/projects/{}/.lock", common::LOCK_PROJECT));
    let before = w.tree.snapshot();
    assert_unsafe(&w);
    assert_eq!(w.tree.snapshot(), before);
}
