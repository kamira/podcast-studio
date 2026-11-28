//! L1:Windows 機制(CHG-20261128-01 T4;行為差異 D-8)。T7 另補 D-9(回收時其他 handle 持有專案目錄)、回收與清理的
//! D-8,以及 `rmtree` 中途失敗不還原。
//!
//! D-8:Windows 上開啟名稱為保留裝置名(`CON`、`nul`、`COM1` 等,`safe-fs` `ReservedDevice`)的既有專案時,讀取 API
//! 明確失敗——`Invalid`「專案名稱是 Windows 保留裝置名,無法在 Windows 上開啟」,不開到裝置、不改動任何東西;
//! `list_projects` 略過這些項目(無法以 handle 取得中繼資料),其他專案照常列出;只差大小寫的判定先於開啟,
//! 照常回「找不到這個專案(已有只差大小寫的其他專案)」。夾具以 `\\?\` 形式的暫存路徑建立(才建得出這些名稱)。
//!
//! D-9(代決 14):Windows 上開著的目錄 handle 或其內檔案的 handle 會擋住該目錄的改名。`trash` 在專案鎖內改名時自己就
//! 持有專案目錄與鎖檔 handle,所以第一次改名一定失敗(本檔先以 `safe-fs` 直接證明),關閉這兩個 handle 後**在鎖外**以同一個
//! 回收名稱重試一次。鎖外的空窗以測試呈現:另一個程式(執行緒 B)在回收進行中已開啟專案目錄並等鎖——空窗期間 B 取得鎖、
//! 寫入專案;B 的 handle 擋住重試,回收回「專案正在使用中」,專案完整留在原處(含 B 的寫入)、回收區沒有任何項目。
//! 另一個 handle 持有專案目錄或其內檔案時同樣回「專案正在使用中」且磁碟不變;關閉後回收成功。
#![cfg(windows)]

mod common;

use std::io::Write;
use std::sync::mpsc;
use std::time::{Duration, Instant, UNIX_EPOCH};

use common::{BASE_MS, ProjectsGuard, TempTree, TestClock};
use project::cleanup::{self, IN_USE};
use project::lock::ProjectLock;
use project::read::{self, CASE_VARIANT, RESERVED_DEVICE, Subdir};
use project::write::{self, format_ms};
use project::{ErrorKind, Project};
use safe_fs::DirOptions;

const RESERVED: [&str; 6] = ["CON", "nul", "COM1", "aux", "LPT9", "Prn"];
const VID: &str = "20260101-000000-000-abcd";

fn body(name: &str) -> String {
    format!(r#"{{"schema_version": 2, "name": "{name}"}}"#)
}

#[track_caller]
fn d8<T: std::fmt::Debug>(r: project::Result<T>) {
    let e = r.expect_err("D-8 應明確失敗");
    assert_eq!(e.kind(), ErrorKind::Invalid, "{e}");
    assert_eq!(e.errors(), [RESERVED_DEVICE]);
}

#[test]
fn reserved_device_projects_fail_explicitly_and_are_skipped_in_listing() {
    let _g = ProjectsGuard::new();
    let t = TempTree::new("project-t4-win-");
    assert!(
        t.path.to_string_lossy().starts_with(r"\\?\"),
        "夾具須以 verbatim 形式的路徑建立"
    );
    for name in RESERVED.iter().copied().chain(["a", "CONX", "COM10"]) {
        t.file(
            &format!("projects/{name}/project.json"),
            body(name).as_bytes(),
        );
        t.file(
            &format!("projects/{name}/history/{VID}.json"),
            body(name).as_bytes(),
        );
        t.file(&format!("projects/{name}/assets/x.png"), b"123");
    }
    let before = t.snapshot();
    for name in RESERVED {
        d8(read::load(&t.path, name));
        d8(read::load_version(&t.path, name, VID));
        d8(read::list_history(&t.path, name));
        d8(read::disk_usage(&t.path, name));
        d8(read::open_project(&t.path, name));
        d8(read::open_subdir(&t.path, name, Subdir::Assets));
    }
    let names: Vec<String> = read::list_projects(&t.path)
        .expect("list_projects")
        .into_iter()
        .map(|p| p.name)
        .collect();
    assert_eq!(names, ["COM10", "CONX", "a"]);
    for name in ["a", "CONX", "COM10"] {
        assert_eq!(
            read::load(&t.path, name).expect("一般名稱照常載入").name,
            name
        );
        assert_eq!(read::list_history(&t.path, name).expect("版本"), [VID]);
        assert_eq!(read::disk_usage(&t.path, name).expect("用量"), 3);
    }
    // 只差大小寫的判定先於開啟:`con` 對 `CON` 是只差大小寫的其他專案
    let e = read::load(&t.path, "con").expect_err("只差大小寫");
    assert_eq!(e.kind(), ErrorKind::NotFound);
    assert_eq!(e.errors(), [CASE_VARIANT]);
    assert_eq!(t.snapshot(), before, "不得改動任何東西");
    println!(
        "D-8:{} 個保留裝置名的既有專案在每個讀取 API 都明確失敗,list_projects 略過",
        RESERVED.len()
    );
}

// ---------- T7:回收與清理 ----------

const P: &str = "夜市的味道";
const CREATE: DirOptions = DirOptions {
    create: true,
    mode: 0o755,
};

fn saved(prefix: &str) -> (ProjectsGuard, TempTree, TestClock) {
    let g = ProjectsGuard::new();
    let t = TempTree::new(prefix);
    let clock = TestClock::at(BASE_MS);
    write::save(&t.path, &Project::new(P), &clock).expect("存檔");
    (g, t, clock)
}

fn entries(t: &TempTree, rel: &str) -> Vec<String> {
    let mut v: Vec<String> = std::fs::read_dir(t.join(rel))
        .expect("讀取目錄")
        .map(|e| e.expect("目錄項").file_name().to_string_lossy().to_string())
        .collect();
    v.sort();
    v
}

fn blocked(kind: safe_fs::ErrorKind) -> bool {
    matches!(
        kind,
        safe_fs::ErrorKind::PermissionDenied | safe_fs::ErrorKind::Other(_)
    )
}

fn base_time() -> std::time::SystemTime {
    UNIX_EPOCH + Duration::from_millis(BASE_MS as u64)
}

/// 代決 14 的前提:Windows 上持有專案目錄與鎖檔 handle 時,在鎖內改名失敗;`trash` 因此在鎖外重試,結果與 Python 相同。
#[test]
fn d9_own_handles_block_the_rename_inside_the_lock_and_trash_retries_outside() {
    let (_g, t, clock) = saved("project-t7-win-d9-");
    {
        let projects =
            safe_fs::open_dir(&t.path, &["projects"], DirOptions::default()).expect("projects");
        let project = projects.open_child(P, DirOptions::default()).expect("專案");
        let trash = projects.open_child(".trash", CREATE).expect(".trash");
        let lock = ProjectLock::acquire(&project).expect("鎖");
        let e = projects
            .rename(P, &trash, "probe")
            .expect_err("持有 handle 時改名應失敗");
        assert!(blocked(e.kind()), "{:?}", e.kind());
        println!(
            "D-9:鎖內改名失敗({:?}),代決 14 的鎖外重試是必要的",
            e.kind()
        );
        drop(lock);
    }
    let dest = cleanup::trash(&t.path, P, true, &clock).expect("鎖外重試成功");
    assert_eq!(
        dest,
        format!("{P}-{}-abcd", format_ms(BASE_MS).expect("時間戳"))
    );
    assert_eq!(
        entries(&t, &format!("projects/.trash/{dest}")),
        [".lock", "history", "project.json"]
    );
    assert!(!t.join(&format!("projects/{P}")).exists());
}

/// 另一個 handle 持有專案目錄:回「專案正在使用中」且磁碟不變;關閉後回收成功。
#[test]
fn d9_another_handle_on_the_project_dir_reports_in_use_and_changes_nothing() {
    let (_g, t, clock) = saved("project-t7-win-d9-");
    t.dir("projects/.trash");
    let other =
        safe_fs::open_dir(&t.path, &["projects", P], DirOptions::default()).expect("另一個 handle");
    let before = t.snapshot();
    let e = cleanup::trash(&t.path, P, true, &clock).expect_err("使用中");
    assert_eq!(e.kind(), ErrorKind::Invalid);
    assert_eq!(e.errors(), [IN_USE]);
    assert_eq!(t.snapshot(), before, "磁碟不變");
    drop(other);
    let dest = cleanup::trash(&t.path, P, true, &clock).expect("關閉後成功");
    assert_eq!(entries(&t, "projects/.trash"), [dest]);
    println!("D-9:另一個目錄 handle 開著時回「專案正在使用中」且磁碟不變,關閉後回收成功");
}

/// 另一個程式開著專案內的檔案(標準函式庫的唯讀開啟):回「專案正在使用中」且磁碟不變;關閉後回收成功。
#[test]
fn d9_an_open_file_inside_the_project_reports_in_use_and_changes_nothing() {
    let (_g, t, clock) = saved("project-t7-win-d9-");
    t.dir("projects/.trash");
    let f = std::fs::File::open(t.join(&format!("projects/{P}/project.json"))).expect("開啟");
    let before = t.snapshot();
    let e = cleanup::trash(&t.path, P, true, &clock).expect_err("使用中");
    assert_eq!(e.errors(), [IN_USE]);
    assert_eq!(t.snapshot(), before, "磁碟不變");
    drop(f);
    cleanup::trash(&t.path, P, true, &clock).expect("關閉後成功");
    println!("D-9:專案內的檔案開著時回「專案正在使用中」且磁碟不變,關閉後回收成功");
}

/// D-9 鎖外重試的空窗:回收已取得專案鎖、改名之前(`now_ms` 掛勾),執行緒 B 開啟專案目錄並開始等鎖。回收的鎖內改名
/// 失敗、釋放鎖與 handle——空窗期間 B 取得鎖並寫入專案;B 仍開著的 handle 擋住鎖外重試,回收回「專案正在使用中」。
/// 結果:專案完整留在原處(含 B 的寫入、可載入),回收區沒有任何項目(Unix 上 B 的寫入則與 Python 相同落在回收項目內,
/// 見 `tests/cleanup.rs`)。
#[test]
fn d9_gap_writer_that_waited_for_the_lock_keeps_the_project_in_place() {
    let (_g, t, clock) = saved("project-t7-win-gap-");
    let (opened_tx, opened_rx) = mpsc::channel();
    let (acquired_tx, acquired_rx) = mpsc::channel::<Instant>();
    let (finish_tx, finish_rx) = mpsc::channel::<()>();
    let (handle_tx, handle_rx) = mpsc::channel();
    let root = t.path.clone();
    clock.before_next_now(move || {
        let h = std::thread::spawn(move || {
            let dir = read::open_project(&root, P).expect("B 開啟專案");
            opened_tx.send(()).expect("通知");
            let lock = ProjectLock::acquire(&dir).expect("B 取鎖");
            acquired_tx.send(Instant::now()).expect("通知");
            let mut f = dir
                .create_excl(
                    "b.txt",
                    safe_fs::FileAccess::Write { truncate: false },
                    0o644,
                )
                .expect("B 寫入");
            f.write_all("B 的寫入".as_bytes()).expect("寫入");
            drop(f);
            drop(lock);
            finish_rx
                .recv_timeout(Duration::from_secs(60))
                .expect("等待結束");
            drop(dir);
        });
        opened_rx
            .recv_timeout(Duration::from_secs(30))
            .expect("B 已開啟");
        std::thread::sleep(Duration::from_millis(200));
        handle_tx.send(h).expect("交出 B");
    });
    let e = cleanup::trash(&t.path, P, true, &clock).expect_err("B 的 handle 擋住重試");
    let returned = Instant::now();
    assert_eq!(e.errors(), [IN_USE]);
    let acquired = acquired_rx
        .recv_timeout(Duration::from_secs(30))
        .expect("B 在回收釋放鎖後取得鎖");
    finish_tx.send(()).expect("通知 B 結束");
    handle_rx
        .recv_timeout(Duration::from_secs(5))
        .expect("B")
        .join()
        .expect("B 執行緒");
    assert!(entries(&t, "projects/.trash").is_empty(), "回收區沒有項目");
    assert_eq!(
        std::fs::read_to_string(t.join(&format!("projects/{P}/b.txt"))).expect("B 的寫入"),
        "B 的寫入"
    );
    assert_eq!(read::load(&t.path, P).expect("專案可載入").name, P);
    println!(
        "D-9 空窗:B 在回收釋放鎖後取得鎖並寫入專案(取得時刻{}回收返回),回收回「專案正在使用中」,專案完整留在原處",
        if acquired <= returned {
            "早於"
        } else {
            "晚於"
        }
    );
}

/// D-8 與回收、清理:Windows 上名稱為保留裝置名的既有專案無法回收(`Invalid`,不建立 `.trash/`)、`unlink_in` 不算錯、
/// `check_quota` 回 D-8;啟動清理對它記警告並略過,其他專案照常清空 `tmp/`(D-8 T7 補記)。
#[test]
fn d8_reserved_device_projects_in_trash_and_cleanup() {
    let _g = ProjectsGuard::new();
    let t = TempTree::new("project-t7-win-d8-");
    t.file("projects/CON/project.json", body("CON").as_bytes());
    t.file("projects/CON/tmp/x", b"x");
    t.file("projects/nul/tmp/x", b"x");
    t.file("projects/a/project.json", body("a").as_bytes());
    t.file("projects/a/tmp/y", b"y");
    let before = t.snapshot();
    let clock = TestClock::at(BASE_MS);
    d8(cleanup::trash(&t.path, "CON", true, &clock));
    d8(cleanup::check_quota(&t.path, "CON", 0));
    cleanup::unlink_in(&t.path, "CON", Subdir::Tmp, "x").expect("不算錯");
    assert_eq!(t.snapshot(), before, "不改動任何東西(不建立 .trash/)");
    let r = cleanup::startup_cleanup(&t.path, base_time()).expect("清理");
    assert_eq!(r.tmp_cleared, 1);
    assert_eq!(
        r.warnings,
        [
            "專案「CON」是 Windows 保留裝置名,無法在 Windows 上清理,略過",
            "專案「nul」是 Windows 保留裝置名,無法在 Windows 上清理,略過",
        ]
    );
    assert!(t.join("projects/CON/tmp/x").exists() && t.join("projects/nul/tmp/x").exists());
    assert!(!t.join("projects/a/tmp/y").exists());
    println!("D-8(回收與清理):保留裝置名的專案無法回收、清理時記警告略過,其他專案照常");
}

/// `rmtree` 中途失敗(回收項目內有保留裝置名)時錯誤往上傳、已刪部分不還原(與 Python 相同;「### 呼叫端指引」:
/// 啟動時的呼叫端不得因此中止)。
#[test]
fn rmtree_failure_on_a_reserved_name_propagates_and_is_not_restored() {
    let _g = ProjectsGuard::new();
    let t = TempTree::new("project-t7-win-partial-");
    let old = format_ms(BASE_MS - 31 * 86_400_000).expect("時間戳");
    let e = format!("projects/.trash/過期-{old}-abcd");
    t.file(&format!("{e}/0.txt"), b"0");
    t.file(&format!("{e}/CON/x.txt"), b"x");
    t.file(&format!("{e}/z.txt"), b"z");
    let err = cleanup::startup_cleanup(&t.path, base_time()).expect_err("rmtree 中途失敗");
    assert_eq!(
        err.kind(),
        ErrorKind::Fs(safe_fs::ErrorKind::ReservedDevice)
    );
    assert!(!t.join(&format!("{e}/0.txt")).exists(), "已刪部分不還原");
    assert!(t.join(&format!("{e}/CON/x.txt")).exists() && t.join(&format!("{e}/z.txt")).exists());
    println!("rmtree 中途失敗:錯誤往上傳({err}),已刪的 0.txt 不還原");
}
