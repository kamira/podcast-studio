//! L2:Rust 對 Rust 跨行程互斥與行程終止後釋放(CHG-20261127-01 T1;三平台執行)。
//!
//! 持鎖或取鎖的另一方是測試執行檔自己重新執行自己的輔助行程(代決 10;見 `common::Helper`、
//! `common::run_helper_if_requested`),以標記檔交握、斷言因果順序。唯二的時間斷言:
//! (i) 逾時耗時 ≥ 0.3 s 且 < 10 s;(iv) 自 `Child::wait()` 返回起,Unix 2 s、Windows 10 s 內取得
//! (行為差異 D-3),一律印出實際耗時。

mod common;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{Duration, Instant};

use common::{
    EXIT_TIMEOUT, Helper, READY, RELEASE, RELEASE_LIMIT, RELEASING, SHORT_TIMEOUT, START_LIMIT,
    TIMEOUT_UPPER, WAITING, WorkDir,
};
use heavy_lock::{Error, acquire, acquire_with};

/// 輔助行程入口(沒有角色環境變數時立即返回)。
#[test]
fn heavy_lock_helper() {
    common::run_helper_if_requested();
}

/// 被終止後取得的門檻:Unix 2 s(與 Python 測試相同)、Windows 10 s(事實 G、D-3)。
fn release_threshold() -> Duration {
    if cfg!(windows) {
        Duration::from_secs(10)
    } else {
        Duration::from_secs(2)
    }
}

fn start_holder(w: &WorkDir) -> Helper {
    let helper = Helper::spawn("hold", w);
    assert!(
        w.wait_any(&[READY], START_LIMIT),
        "輔助行程未在期限內取得鎖"
    );
    helper
}

#[test]
fn parent_times_out_while_child_holds() {
    // (i)
    let w = WorkDir::new();
    let mut helper = start_holder(&w);
    let start = Instant::now();
    let r = acquire(&w.top(), Some(SHORT_TIMEOUT));
    let elapsed = start.elapsed();
    println!("子行程持鎖時父行程以 0.3 秒逾時取鎖:耗時 {elapsed:?}");
    assert!(matches!(r, Err(Error::Timeout(_))), "{r:?}");
    assert!(elapsed >= SHORT_TIMEOUT, "{elapsed:?}");
    assert!(elapsed < TIMEOUT_UPPER, "{elapsed:?}");
    w.touch(RELEASE);
    let status = helper.wait_limit(RELEASE_LIMIT).expect("輔助行程未結束");
    assert!(status.success(), "{status:?}");
}

#[test]
fn parent_waits_until_child_releases() {
    // (ii)
    let w = WorkDir::new();
    let mut helper = start_holder(&w);
    let count = AtomicUsize::new(0);
    let lock = acquire_with(&w.top(), Some(RELEASE_LIMIT), || {
        count.fetch_add(1, Ordering::SeqCst);
        w.touch(WAITING);
    })
    .expect("子行程釋放後應取得");
    assert!(w.has(RELEASING), "父行程應在子行程釋放後才取得");
    assert_eq!(count.load(Ordering::SeqCst), 1, "on_wait 應恰好一次");
    drop(lock);
    let status = helper.wait_limit(RELEASE_LIMIT).expect("輔助行程未結束");
    assert!(status.success(), "{status:?}");
}

#[test]
fn child_times_out_while_parent_holds() {
    // (iii)
    let w = WorkDir::new();
    let lock = acquire(&w.top(), None).expect("父行程取鎖失敗");
    let mut helper = Helper::spawn("try", &w);
    let status = helper.wait_limit(RELEASE_LIMIT).expect("輔助行程未結束");
    assert_eq!(
        status.code(),
        Some(EXIT_TIMEOUT),
        "子行程應以結束碼回報 Timeout:{status:?}"
    );
    drop(lock);
}

#[test]
fn lock_released_after_holder_is_killed() {
    // (iv)
    let w = WorkDir::new();
    let mut helper = start_holder(&w);
    let status = helper.kill_and_wait();
    let start = Instant::now();
    let threshold = release_threshold();
    let r = acquire(&w.top(), Some(threshold));
    let elapsed = start.elapsed();
    println!("持鎖子行程被終止({status:?})後取得重型鎖的實際耗時:{elapsed:?}(門檻 {threshold:?})");
    let lock = r.expect("被終止後未在門檻內取得");
    assert!(elapsed < threshold, "{elapsed:?}");
    assert!(!w.has(RELEASING), "子行程應是被終止,而非自行釋放");
    drop(lock);
    let meta = std::fs::symlink_metadata(w.lock_path()).expect("鎖檔應仍存在");
    assert_eq!(meta.len(), 0, "鎖檔應沒有內容");
}
