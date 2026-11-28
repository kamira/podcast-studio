//! L2:專案鎖的 Rust 對 Rust 跨行程互斥與行程終止後釋放(CHG-20261128-01 T5;三平台執行)。
//!
//! 持鎖或取鎖的另一方是測試執行檔自己重新執行自己的輔助行程(做法沿用 CHG-20261127-01 代決 10;見
//! `common::Helper`、`common::run_helper_if_requested`),以標記檔交握、斷言因果順序。專案鎖沒有逾時(代決 11),
//! 「對方持鎖時取不到」以「取鎖方在 0.5 秒觀察期內沒有返回,且對方寫出 `releasing` 後才返回、返回時 `releasing`
//! 已存在」斷言。唯一的其他時間斷言:持鎖子行程被終止後,自 `Child::wait()` 返回起 Unix 2 秒、Windows 10 秒內取得
//! (CHG-20261127-01 行為差異 D-3 的門檻),一律印出實際耗時。

mod common;

use std::time::Instant;

use common::{
    ACQUIRED, EXIT_RELEASED, EXIT_WAITED, Helper, LockThread, LockWork, OBSERVE, ProjectsGuard,
    READY, RELEASE, RELEASE_LIMIT, RELEASING, START_LIMIT, WAITING, lock_project,
};

/// 輔助行程入口(沒有角色環境變數時立即返回)。
#[test]
fn project_lock_helper() {
    common::run_helper_if_requested();
}

/// 被終止後取得的門檻:Unix 2 秒、Windows 10 秒(CHG-20261127-01 事實 G、D-3)。
fn release_threshold() -> std::time::Duration {
    if cfg!(windows) {
        std::time::Duration::from_secs(10)
    } else {
        std::time::Duration::from_secs(2)
    }
}

fn start_holder(w: &LockWork) -> Helper {
    let helper = Helper::spawn("hold", w);
    assert!(
        w.wait_any(&[READY], START_LIMIT),
        "輔助行程未在期限內取得鎖"
    );
    helper
}

#[test]
fn parent_waits_while_child_holds() {
    let _g = ProjectsGuard::new();
    let w = LockWork::new();
    let mut helper = start_holder(&w);
    let t = LockThread::spawn(&w);
    let early = t.wait(OBSERVE);
    assert!(
        early.is_none(),
        "子行程持鎖時,父行程取鎖不應在 {OBSERVE:?} 觀察期內返回"
    );
    w.touch(RELEASE);
    let (r, seen, elapsed) = t.wait(RELEASE_LIMIT).expect("子行程釋放後父行程未取得");
    println!(
        "子行程持鎖時父行程取鎖:觀察期 {OBSERVE:?} 內未返回;子行程寫出 releasing 並釋放後取得,\
         releasing 已存在={seen},自開始耗時 {elapsed:?}"
    );
    let lock = r.expect("子行程釋放後應取得");
    assert!(seen, "父行程應在子行程寫出 releasing 並釋放後才取得");
    drop(lock);
    let status = helper.wait_limit(RELEASE_LIMIT).expect("輔助行程未結束");
    assert_eq!(status.code(), Some(EXIT_RELEASED), "{status:?}");
}

#[test]
fn child_waits_while_parent_holds() {
    let _g = ProjectsGuard::new();
    let w = LockWork::new();
    let lock = lock_project(&w.top()).expect("父行程取鎖失敗");
    let mut helper = Helper::spawn("wait", &w);
    assert!(
        w.wait_any(&[WAITING], START_LIMIT),
        "輔助行程未在期限內開始取鎖"
    );
    let start = Instant::now();
    std::thread::sleep(OBSERVE);
    let running = helper.running();
    let acquired_early = w.has(ACQUIRED);
    println!(
        "父行程持鎖時子行程取鎖:觀察期 {OBSERVE:?} 後子行程仍在等待={running},已取得={acquired_early}"
    );
    assert!(running, "父行程持鎖時,子行程不應在觀察期內結束");
    assert!(!acquired_early, "父行程持鎖時,子行程不應取得");
    w.touch(RELEASING);
    drop(lock);
    let status = helper.wait_limit(RELEASE_LIMIT).expect("輔助行程未結束");
    println!(
        "父行程寫出 releasing 並釋放後子行程結束:{status:?},自觀察期開始耗時 {:?}",
        start.elapsed()
    );
    assert_eq!(
        status.code(),
        Some(EXIT_WAITED),
        "子行程應在父行程寫出 releasing 並釋放後才取得:{status:?}"
    );
    assert!(w.has(ACQUIRED));
}

#[test]
fn lock_released_after_holder_is_killed() {
    let _g = ProjectsGuard::new();
    let w = LockWork::new();
    let mut helper = start_holder(&w);
    let status = helper.kill_and_wait();
    let start = Instant::now();
    let threshold = release_threshold();
    let t = LockThread::spawn(&w);
    let got = t.wait(threshold);
    let elapsed = start.elapsed();
    println!("持鎖子行程被終止({status:?})後取得專案鎖的實際耗時:{elapsed:?}(門檻 {threshold:?})");
    let (r, _, _) = got.expect("被終止後未在門檻內取得");
    let lock = r.expect("被終止後應取得");
    assert!(!w.has(RELEASING), "子行程應是被終止,而非自行釋放");
    drop(lock);
    let meta = std::fs::symlink_metadata(w.lock_path()).expect("鎖檔應仍存在");
    assert_eq!(meta.len(), 0, "鎖檔應沒有內容");
}
