//! L1:回收與清理(CHG-20261128-01 T7;`project::cleanup`)。
//!
//! 移植 `tests/test_project.py` 的回收與清理案例:`trash` 需確認、不覆蓋既有項目;30 天邊界(差 1 毫秒、差 1 微秒);
//! 回收區與 `tmp/` 內的連結不跟隨(刪連結本身、不動目標);`projects/`、回收區、專案目錄、`tmp/`、`history/` 是連結時拒絕;
//! 清空 `tmp/` 不動專案資料;沒有 `projects/`;清單取得後被換成連結的競態(回收項目、`tmp/`、回收前的 `.trash/`)。
//! 另有:名稱內時間戳的解析(`TRASH_RE` 沒有 `re.ASCII`——Unicode 十進位數字;`strptime` 的交替順序;CPython 3.11.18
//! 的參考表)、`unlink_in` 與 `check_quota`、行為差異 D-9 在 macOS / Linux 的對照(另一個程式持有專案 handle 時,
//! 鎖內改名成功、其寫入落在回收項目內,與 Python 相同)與存檔中回收的壓力測試(資料一律可載入)。macOS / Linux 另以
//! Python 子程序對同一棵樹確認 `startup_cleanup` 的結果相同(含換行與 Unicode 數字的名稱)、`_parse_stamp` 的語料一致、
//! 十進位數字表與 Python `unicodedata` 一致。
//!
//! 夾具只建在測試自己排他建立的暫存目錄;開頭與結尾確認 repo 工作副本的 `projects/` 不變。Windows 無權限建立符號
//! 連結時,連結案例先 `assert!(!in_ci())` 再印略過。

mod common;

#[cfg(unix)]
use std::io::Write;
#[cfg(unix)]
use std::path::Path;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
#[cfg(unix)]
use std::sync::mpsc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use common::{BASE_MS, ProjectsGuard, TempTree, TestClock, in_ci};
use project::cleanup::{
    self, CONFIRM_REQUIRED, Cleanup, IN_USE, NO_UNIQUE_TRASH, QUOTA_EXCEEDED, parse_stamp,
    trash_stamp,
};
use project::constants::MAX_ASSET_BYTES;
use project::ids::SystemClock;
use project::read::{self, Subdir};
use project::write::{self, format_ms};
use project::{ErrorKind, Project};

const P: &str = "夜市的味道";
const DAY_MS: i64 = 86_400_000;

fn setup(prefix: &str) -> (ProjectsGuard, TempTree) {
    let guard = ProjectsGuard::new();
    let t = TempTree::new(prefix);
    (guard, t)
}

fn at_ms(ms: i64) -> SystemTime {
    UNIX_EPOCH + Duration::from_millis(ms.unsigned_abs())
}

fn stamp(ms: i64) -> String {
    format_ms(ms).expect("時間戳")
}

/// 回收項目(與 `tests/test_project.py` 的 `make_trash_entry` 相同:`audio/` 與 `project.json`),回傳名稱。
fn make_entry(t: &TempTree, prefix: &str, ms: i64) -> String {
    let name = format!("{prefix}-{}-abcd", stamp(ms));
    t.dir(&format!("projects/.trash/{name}/audio"));
    t.file(&format!("projects/.trash/{name}/project.json"), b"{}");
    name
}

fn exists(t: &TempTree, rel: &str) -> bool {
    std::fs::symlink_metadata(t.join(rel)).is_ok()
}

fn save(t: &TempTree, name: &str, clock: &TestClock) {
    write::save(&t.path, &Project::new(name), clock).expect("存檔");
}

fn cleanup_at(t: &TempTree, ms: i64) -> Cleanup {
    cleanup::startup_cleanup(&t.path, at_ms(ms)).expect("startup_cleanup")
}

/// 外部目錄(連結目標):`keep.txt` 與 `sub/deep.txt`。
fn outside(t: &TempTree) -> std::path::PathBuf {
    t.file("outside/keep.txt", "不可刪除".as_bytes());
    t.file("outside/sub/deep.txt", "深層".as_bytes());
    t.join("outside")
}

/// `outside/` 之下的快照(連結目標不得改變)。
fn outside_snapshot(t: &TempTree) -> Vec<(String, common::Entry)> {
    t.snapshot()
        .into_iter()
        .filter(|(k, _)| k.starts_with("outside"))
        .collect()
}

#[track_caller]
fn skip_without_symlink(made: bool, what: &str) -> bool {
    if !made {
        assert!(!in_ci(), "CI 上無法建立符號連結,判失敗:{what}");
        println!("略過(本機無權限建立符號連結):{what}");
    }
    !made
}

// ---------- trash ----------

#[test]
fn trash_requires_confirm_and_never_overwrites() {
    let (_g, t) = setup("project-t7-trash-");
    let clock = TestClock::with_rand(BASE_MS, &["abcd", "abcd", "1234"]);
    save(&t, P, &clock);
    let e = cleanup::trash(&t.path, P, false, &clock).expect_err("需要確認");
    assert_eq!(e.kind(), ErrorKind::Invalid);
    assert_eq!(e.errors(), [CONFIRM_REQUIRED]);
    assert!(!exists(&t, "projects/.trash"), "未確認時不建立回收區");
    let first = cleanup::trash(&t.path, P, true, &clock).expect("第一次回收");
    assert_eq!(first, format!("{P}-20261027-120000-000-abcd"));
    let first_before = t.snapshot();
    save(&t, P, &clock);
    let second = cleanup::trash(&t.path, P, true, &clock).expect("第二次回收");
    assert_eq!(
        second,
        format!("{P}-20261027-120000-000-1234"),
        "撞名時換隨機碼"
    );
    let mut got: Vec<String> = std::fs::read_dir(t.join("projects/.trash"))
        .expect("讀取回收區")
        .map(|e| e.expect("目錄項").file_name().to_string_lossy().to_string())
        .collect();
    got.sort();
    let mut want = vec![first.clone(), second];
    want.sort();
    assert_eq!(got, want);
    assert!(!exists(&t, &format!("projects/{P}")));
    assert!(exists(&t, &format!("projects/.trash/{first}/project.json")));
    let after = t.snapshot();
    for (k, v) in &first_before {
        if k.starts_with(&format!("projects/.trash/{first}")) {
            assert_eq!(after.get(k), Some(v), "既有的回收項目不得被覆蓋:{k}");
        }
    }
}

#[test]
fn trash_collisions_and_errors_leave_the_project_in_place() {
    let (_g, t) = setup("project-t7-trash-");
    let clock = TestClock::at(BASE_MS);
    save(&t, P, &clock);
    let dest = format!("{P}-{}-abcd", stamp(BASE_MS));
    t.file(&format!("projects/.trash/{dest}"), b"existing");
    let before = t.snapshot();
    let e = cleanup::trash(&t.path, P, true, &clock).expect_err("撞名用盡");
    assert_eq!(e.errors(), [NO_UNIQUE_TRASH]);
    assert_eq!(t.snapshot(), before, "用盡時不改動任何東西");
    // 不存在、名稱不合規則、只差大小寫
    for (name, kind) in [
        ("其他", ErrorKind::NotFound),
        ("a/b", ErrorKind::Invalid),
        ("", ErrorKind::Invalid),
    ] {
        let e = cleanup::trash(&t.path, name, true, &clock).expect_err(name);
        assert_eq!(e.kind(), kind, "{name}");
    }
    save(&t, "Ep1", &clock);
    let e = cleanup::trash(&t.path, "ep1", true, &clock).expect_err("只差大小寫");
    assert_eq!(e.errors(), [read::CASE_VARIANT]);
    assert_eq!(
        cleanup::trash(&t.path.join("missing"), P, true, &clock)
            .expect_err("沒有資料根目錄")
            .kind(),
        ErrorKind::NotFound
    );
}

#[test]
fn trash_symlinked_dirs_are_refused_and_targets_unchanged() {
    let (_g, t) = setup("project-t7-trash-");
    let out = outside(&t);
    let clock = TestClock::at(BASE_MS);
    let root = t.join("root");
    std::fs::create_dir(&root).expect("root");
    write::save(&root, &Project::new(P), &clock).expect("存檔");
    if skip_without_symlink(
        t.symlink("root/projects/.trash", &out, true),
        "trash 的 .trash 連結",
    ) {
        return;
    }
    let before = t.snapshot();
    let e = cleanup::trash(&root, P, true, &clock).expect_err(".trash 是連結");
    assert_eq!(e.kind(), ErrorKind::UnsafePath);
    assert_eq!(e.errors(), ["資料目錄「.trash」是符號連結,不接受"]);
    assert_eq!(t.snapshot(), before, "連結目標與專案都不變");
    std::fs::remove_file(t.join("root/projects/.trash"))
        .or_else(|_| std::fs::remove_dir(t.join("root/projects/.trash")))
        .expect("移除連結");
    // 鎖檔是連結:`.trash/` 先建立(與 Python 相同),取鎖時拒絕,專案不搬
    std::fs::remove_file(t.join(&format!("root/projects/{P}/.lock"))).expect("移除鎖檔");
    assert!(t.symlink(
        &format!("root/projects/{P}/.lock"),
        &out.join("keep.txt"),
        false
    ));
    let before = outside_snapshot(&t);
    let e = cleanup::trash(&root, P, true, &clock).expect_err("鎖檔是連結");
    assert_eq!(e.errors(), [project::lock::LOCK_UNSAFE]);
    assert_eq!(outside_snapshot(&t), before);
    assert!(exists(&t, &format!("root/projects/{P}/project.json")));
}

/// Python `test_race_trash_dir_swapped_before_move_project_not_moved_outside`:開啟 `.trash/`、取得專案鎖之後、改名之前
/// 把 `.trash/` 換成指向外部的連結——改名相對已開啟的 handle,專案搬進原本的回收區(被移開的位置),不會搬到外部。
/// Windows 上開著 handle 的目錄不能改名,替換本身失敗,回收照常完成。
#[test]
fn race_trash_dir_swapped_before_move_project_not_moved_outside() {
    let (_g, t) = setup("project-t7-race-");
    let out = outside(&t);
    let clock = TestClock::at(BASE_MS);
    save(&t, P, &clock);
    t.dir("projects/.trash");
    let probe = t.join("probe");
    if skip_without_symlink(t.symlink("probe", &out, true), "回收前換掉 .trash") {
        return;
    }
    std::fs::remove_file(&probe)
        .or_else(|_| std::fs::remove_dir(&probe))
        .expect("移除探測連結");
    let before = outside_snapshot(&t);
    let trash = t.join("projects/.trash");
    let moved = t.join("projects/.trash-moved");
    let swapped = Arc::new(AtomicBool::new(false));
    let flag = Arc::clone(&swapped);
    let target = out.clone();
    clock.before_next_now(move || {
        if std::fs::rename(&trash, &moved).is_ok() {
            safe_fs::support::symlink(&target, &trash, true).expect("建立連結");
            flag.store(true, Ordering::SeqCst);
        }
    });
    let dest = cleanup::trash(&t.path, P, true, &clock).expect("回收");
    assert_eq!(outside_snapshot(&t), before, "沒有搬進連結目標");
    let swapped = swapped.load(Ordering::SeqCst);
    assert_eq!(
        swapped,
        !cfg!(windows),
        "macOS / Linux 上替換成功;Windows 上開著 handle 的 .trash/ 不能改名"
    );
    if swapped {
        assert!(exists(
            &t,
            &format!("projects/.trash-moved/{dest}/project.json")
        ));
        println!("競態(.trash 換成連結):專案搬進原本的回收區(handle 相對),連結目標不變");
    } else {
        assert!(exists(&t, &format!("projects/.trash/{dest}/project.json")));
        println!("競態(.trash 換成連結):Windows 上開著 handle 的 .trash/ 不能改名,回收照常");
    }
}

// ---------- 30 天邊界 ----------

#[test]
fn startup_cleanup_trash_30_day_boundary() {
    let (_g, t) = setup("project-t7-age-");
    let keep = make_entry(&t, "保留", BASE_MS - 29 * DAY_MS - 23 * 3_600_000);
    let gone = make_entry(&t, "刪除", BASE_MS - 30 * DAY_MS);
    let older = make_entry(&t, "更舊", BASE_MS - 45 * DAY_MS);
    let one_ms = make_entry(&t, "差一毫秒", BASE_MS - 30 * DAY_MS + 1);
    let r = cleanup_at(&t, BASE_MS);
    assert!(exists(&t, &format!("projects/.trash/{keep}")));
    assert!(
        exists(&t, &format!("projects/.trash/{one_ms}")),
        "差 1 毫秒滿 30 天不刪"
    );
    assert!(
        !exists(&t, &format!("projects/.trash/{gone}")),
        "剛好滿 30 天刪除"
    );
    assert!(!exists(&t, &format!("projects/.trash/{older}")));
    let mut want = vec![gone, older];
    want.sort();
    assert_eq!(r.trash_removed, want);
    assert!(r.warnings.is_empty(), "{:?}", r.warnings);
    // 以注入的 now 判定:把 now 往前推 1 天就保留
    let keep2 = make_entry(&t, "再一個", BASE_MS - 30 * DAY_MS);
    cleanup_at(&t, BASE_MS - DAY_MS);
    assert!(exists(&t, &format!("projects/.trash/{keep2}")));
    // 微秒:差 1 微秒滿 30 天不刪、剛好刪除(Python `datetime` 的微秒)
    let us = |x: u64| UNIX_EPOCH + Duration::from_micros(BASE_MS as u64 * 1000 + x);
    let r = cleanup::startup_cleanup(&t.path, us(999)).expect("差一微秒");
    assert_eq!(r.trash_removed, [keep2], "差 1 微秒滿 30 天的項目保留");
    assert!(exists(&t, &format!("projects/.trash/{one_ms}")));
    let r = cleanup::startup_cleanup(&t.path, us(1000)).expect("剛好");
    assert_eq!(r.trash_removed, [one_ms]);
    // 微秒以下捨去(Python `datetime` 只有微秒;早於紀元以 floor 取微秒;Windows 的 SystemTime 以 100 奈秒為單位)
    assert_eq!(
        cleanup::timestamp_seconds(UNIX_EPOCH + Duration::from_nanos(1_999)),
        1e-6
    );
    assert_eq!(
        cleanup::timestamp_seconds(UNIX_EPOCH - Duration::from_nanos(1_500)),
        -2e-6
    );
    println!("30 天邊界:剛好滿 30 天刪除、差 1 毫秒與差 1 微秒保留");
}

#[test]
fn trash_created_by_trash_is_cleaned_after_30_days() {
    let (_g, t) = setup("project-t7-age-");
    let clock = TestClock::at(BASE_MS);
    save(&t, P, &clock);
    let name = cleanup::trash(&t.path, P, true, &clock).expect("回收");
    cleanup_at(&t, BASE_MS + 29 * DAY_MS + 23 * 3_600_000);
    assert!(exists(&t, &format!("projects/.trash/{name}")));
    let r = cleanup_at(&t, BASE_MS + 30 * DAY_MS);
    assert!(!exists(&t, &format!("projects/.trash/{name}")));
    assert_eq!(r.trash_removed, [name]);
}

// ---------- 連結不跟隨 ----------

#[test]
fn startup_cleanup_trash_symlinks_not_followed() {
    let (_g, t) = setup("project-t7-links-");
    let out = outside(&t);
    t.dir("projects/.trash");
    let old = stamp(BASE_MS - 90 * DAY_MS);
    let link_dir = format!("projects/.trash/外連目錄-{old}-abcd");
    let link_file = format!("projects/.trash/外連檔案-{old}-abcd");
    if skip_without_symlink(t.symlink(&link_dir, &out, true), "回收區內的連結") {
        return;
    }
    assert!(t.symlink(&link_file, &out.join("keep.txt"), false));
    // 過期的一般回收項目,內部含指向外部的連結:項目刪除,連結目標不動
    let expired = make_entry(&t, "過期", BASE_MS - 31 * DAY_MS);
    assert!(t.symlink(&format!("projects/.trash/{expired}/外連"), &out, true));
    assert!(t.symlink(
        &format!("projects/.trash/{expired}/audio/外連.txt"),
        &out.join("keep.txt"),
        false
    ));
    let before = outside_snapshot(&t);
    let r = cleanup_at(&t, BASE_MS);
    assert!(
        exists(&t, &link_dir) && exists(&t, &link_file),
        "連結本身不刪"
    );
    assert!(!exists(&t, &format!("projects/.trash/{expired}")));
    assert_eq!(outside_snapshot(&t), before);
    assert_eq!(r.trash_removed, [expired]);
    assert_eq!(r.warnings.len(), 2);
    assert!(
        r.warnings.iter().all(|w| w.contains("符號連結")),
        "{:?}",
        r.warnings
    );
}

#[test]
fn startup_cleanup_tmp_symlink_not_followed() {
    let (_g, t) = setup("project-t7-links-");
    let out = outside(&t);
    save(&t, P, &TestClock::at(BASE_MS));
    t.dir(&format!("projects/{P}/tmp"));
    if skip_without_symlink(
        t.symlink(&format!("projects/{P}/tmp/外連目錄"), &out, true),
        "tmp 內的連結",
    ) {
        return;
    }
    assert!(t.symlink(
        &format!("projects/{P}/tmp/外連檔案"),
        &out.join("keep.txt"),
        false
    ));
    let before = outside_snapshot(&t);
    let r = cleanup_at(&t, BASE_MS);
    assert_eq!(outside_snapshot(&t), before);
    assert_eq!(r.tmp_cleared, 2);
    assert_eq!(
        std::fs::read_dir(t.join(&format!("projects/{P}/tmp")))
            .expect("tmp")
            .count(),
        0
    );
}

/// Python `test_startup_cleanup_refuses_symlinked_dirs`(`.trash`、`project`、`tmp`、`history`)與
/// `test_projects_root_symlink_refused`。
#[test]
fn startup_cleanup_refuses_symlinked_dirs() {
    let _g = ProjectsGuard::new();
    let mut ran = 0;
    for which in [".trash", "project", "tmp", "history", "projects"] {
        let t = TempTree::new("project-t7-refuse-");
        let out = outside(&t);
        let old = stamp(BASE_MS - 90 * DAY_MS);
        t.dir(&format!("outside/舊的-{old}-abcd"));
        t.file("outside/tmp/殘留.json", b"x");
        t.file(&format!("outside/{old}-abcd.json"), b"{}");
        let root = t.join("root");
        std::fs::create_dir(&root).expect("root");
        write::save(&root, &Project::new("正常"), &TestClock::at(BASE_MS)).expect("存檔");
        let made = match which {
            ".trash" => t.symlink("root/projects/.trash", &out, true),
            "project" => t.symlink("root/projects/外連專案", &out, true),
            "projects" => {
                std::fs::rename(t.join("root/projects"), t.join("root/projects-real"))
                    .expect("搬開");
                t.symlink("root/projects", &out, true)
            }
            sub => {
                t.dir("root/projects/外連子目錄");
                t.symlink(&format!("root/projects/外連子目錄/{sub}"), &out, true)
            }
        };
        if skip_without_symlink(made, which) {
            continue;
        }
        let before = outside_snapshot(&t);
        let r = cleanup::startup_cleanup(&root, at_ms(BASE_MS)).expect("startup_cleanup");
        assert_eq!(outside_snapshot(&t), before, "{which}");
        assert!(!r.warnings.is_empty(), "{which} 應記警告");
        assert!(r.trash_removed.is_empty() && r.tmp_cleared == 0, "{which}");
        if which != "projects" {
            assert!(exists(&t, "root/projects/正常/project.json"));
        }
        println!("拒絕處理連結({which}):{:?}", r.warnings);
        ran += 1;
    }
    println!("連結拒絕案例:執行 {ran} 個");
}

#[test]
fn startup_cleanup_clears_tmp_and_keeps_project_data() {
    let (_g, t) = setup("project-t7-tmp-");
    let clock = TestClock::at(BASE_MS);
    save(&t, P, &clock);
    clock.set(BASE_MS + 1);
    save(&t, P, &clock);
    let pdir = format!("projects/{P}");
    t.file(&format!("{pdir}/audio/abcdef12.wav"), b"RIFF");
    t.file(&format!("{pdir}/assets/abcdef12.png"), b"PNG");
    t.file(&format!("{pdir}/tmp/jobs.json"), b"{}");
    t.file(&format!("{pdir}/tmp/out/a.wav"), b"x");
    let keep = |t: &TempTree| -> Vec<(String, Option<Vec<u8>>)> {
        t.snapshot()
            .into_iter()
            .filter(|(k, _)| k.starts_with(&pdir) && !k.starts_with(&format!("{pdir}/tmp")))
            .map(|(k, (_, _, c))| (k, c))
            .collect()
    };
    let before = keep(&t);
    let r = cleanup_at(&t, BASE_MS);
    assert_eq!(r.tmp_cleared, 2);
    assert_eq!(
        std::fs::read_dir(t.join(&format!("{pdir}/tmp")))
            .expect("tmp")
            .count(),
        0
    );
    assert!(exists(&t, &format!("{pdir}/tmp")), "tmp/ 本身保留");
    assert_eq!(
        keep(&t),
        before,
        "project.json、history/、audio/、assets/ 不動"
    );
    assert_eq!(read::load(&t.path, P).expect("載入").name, P);
    assert_eq!(read::list_history(&t.path, P).expect("版本").len(), 1);
}

#[test]
fn startup_cleanup_without_projects_dir() {
    let (_g, t) = setup("project-t7-empty-");
    assert_eq!(cleanup_at(&t, BASE_MS), Cleanup::default());
    assert!(!exists(&t, "projects"), "不建立 projects/");
    assert_eq!(
        cleanup::startup_cleanup(&t.path.join("missing"), at_ms(BASE_MS)).expect("沒有根目錄"),
        Cleanup::default()
    );
    assert!(t.snapshot().is_empty());
}

#[test]
fn startup_cleanup_warns_on_names_and_regular_files() {
    let (_g, t) = setup("project-t7-names-");
    t.file("projects/壞 名稱/tmp/x", b"x");
    t.file("projects/.hidden/tmp/x", b"x");
    t.file("projects/檔案", b"x");
    t.file("projects/.trash/沒有時間戳/x", b"x");
    t.file(&format!("projects/{P}/tmp"), b"x");
    let before = t.snapshot();
    let r = cleanup_at(&t, BASE_MS);
    assert_eq!(t.snapshot(), before, "只記警告,不刪任何東西");
    assert_eq!(
        r.warnings,
        [
            "回收區項目「沒有時間戳」名稱不含時間戳,略過".to_string(),
            "projects 目錄內有名稱不合規則的項目,略過".to_string(),
            format!("專案「{P}」的 tmp 目錄是符號連結或不在預期位置,拒絕清空"),
            "專案目錄「檔案」是符號連結或不在預期位置,拒絕處理".to_string(),
        ]
    );
}

// ---------- 名稱內的時間戳 ----------

/// CPython 3.11.18 的 `project._parse_stamp`(原樣)對同一組輸入的結果(施工時以 3.11.18 產生;`\u{..}` 為 Unicode
/// 十進位數字:全形 U+FF10~、阿拉伯-印度 U+0660~、天城 U+0966~、數學粗體 U+1D7CE~)。
const PY311_PARSE_STAMP: &[(&str, Option<f64>)] = &[
    ("20261027-120000-000", Some(1793102400.0)),
    ("20260927-120000-000", Some(1790510400.0)),
    ("20260927-120000-001", Some(1790510400.001)),
    ("19700101-000000-000", Some(0.0)),
    ("19691231-235959-999", Some(-0.0010000000000000009)),
    ("00010101-000000-000", Some(-62135596800.0)),
    ("99991231-235959-999", Some(253402300799.999)),
    ("00000101-000000-000", None),
    ("20240229-000000-000", Some(1709164800.0)),
    ("20250229-000000-000", None),
    ("21000229-000000-000", None),
    ("20000229-000000-000", Some(951782400.0)),
    ("20251301-000000-000", None),
    ("20250001-000000-000", None),
    ("20250100-000000-000", None),
    ("20250132-000000-000", None),
    ("20250431-000000-000", None),
    ("20250430-235959-500", Some(1746057599.5)),
    ("20250101-240000-000", None),
    ("20250101-236000-000", None),
    ("20250101-235960-000", None),
    ("20250101-235961-000", None),
    ("20250101-006000-000", None),
    ("20250101-000099-000", None),
    ("20250101-099999-000", None),
    ("20250101-190000-000", Some(1735758000.0)),
    (
        "\u{ff12}\u{ff10}\u{ff12}\u{ff15}\u{ff10}\u{ff11}\u{ff10}\u{ff11}-\u{ff10}\u{ff10}\u{ff10}\u{ff10}\u{ff10}\u{ff10}-\u{ff10}\u{ff10}\u{ff10}",
        None,
    ),
    (
        "\u{ff12}\u{ff10}\u{ff12}\u{ff15}0101-000000-000",
        Some(1735689600.0),
    ),
    ("2025\u{ff10}\u{ff11}01-000000-000", None),
    ("202501\u{ff11}\u{ff15}-000000-000", None),
    ("2025011\u{ff15}-000000-000", Some(1736899200.0)),
    ("20250101-1\u{ff15}0000-000", Some(1735743600.0)),
    ("20250101-2\u{ff13}0000-000", None),
    ("20250101-0\u{ff15}5\u{ff19}00-000", Some(1735711140.0)),
    ("20250101-00006\u{ff10}-000", None),
    (
        "20250101-000000-\u{660}\u{660}\u{667}",
        Some(1735689600.007),
    ),
    (
        "\u{662}\u{660}\u{662}\u{665}\u{660}\u{661}\u{660}\u{661}-\u{660}\u{660}\u{660}\u{660}\u{660}\u{660}-\u{661}\u{662}\u{663}",
        None,
    ),
    (
        "20250101-\u{967}\u{968}\u{966}\u{966}\u{966}\u{966}-000",
        None,
    ),
    (
        "20250101-000000-\u{1d7d7}\u{1d7d7}\u{1d7d7}",
        Some(1735689600.999),
    ),
    ("2025013\u{ff11}-000000-000", None),
    ("2025012\u{ff19}-000000-000", Some(1738108800.0)),
    ("20250101-0\u{ff19}0000-000", Some(1735722000.0)),
    ("20250101-000\u{ff15}00-000", Some(1735689900.0)),
    ("20250101-00000\u{ff15}-000", Some(1735689605.0)),
];

#[test]
fn parse_stamp_matches_cpython_311_reference() {
    for (s, want) in PY311_PARSE_STAMP {
        let got = parse_stamp(s);
        assert_eq!(
            got.map(f64::to_bits),
            want.map(f64::to_bits),
            "{s:?}:Rust {got:?},CPython 3.11 {want:?}"
        );
    }
    println!(
        "_parse_stamp:{} 個 CPython 3.11.18 參考案例一致",
        PY311_PARSE_STAMP.len()
    );
}

#[test]
fn trash_stamp_follows_trash_re() {
    let s = "20260101-000000-000";
    let ok = [
        format!("x-{s}-abcd"),
        format!("我-的-專案-{s}-0f9a"),
        format!("--{s}-abcd"),
        format!("x-{s}-abcd-{s}-abcd"),
        format!("\r-{s}-abcd"),
    ];
    for n in &ok {
        assert_eq!(trash_stamp(n).as_deref(), Some(s), "{n:?}");
    }
    let bad = [
        format!("-{s}-abcd"),
        format!("x\n-{s}-abcd"),
        format!("\n-{s}-abcd"),
        format!("x-{s}-ABCD"),
        format!("x-{s}-abc"),
        format!("x-{s}-abcde"),
        format!("x{s}-abcd"),
        format!("x-{s}-abcd.bak"),
        "x-2026010-0000000-000-abcd".to_string(),
        "x-20260101-000000-00-abcd".to_string(),
        "x-20260101_000000-000-abcd".to_string(),
        "x-20260101-000000-000-\u{ff41}bcd".to_string(),
        String::new(),
    ];
    for n in &bad {
        assert_eq!(trash_stamp(n), None, "{n:?}");
    }
    // `\d` 為 Unicode 十進位數字(沒有 `re.ASCII`)
    assert_eq!(
        trash_stamp("x-\u{ff12}0260101-000000-000-abcd").as_deref(),
        Some("\u{ff12}0260101-000000-000")
    );
    assert_eq!(cleanup::decimal_value('\u{ff19}'), Some(9));
    assert_eq!(
        cleanup::decimal_value('\u{2460}'),
        None,
        "圈號數字不是十進位數字"
    );
    assert_eq!(cleanup::decimal_value('a'), None);
}

// ---------- unlink_in、check_quota ----------

#[test]
fn unlink_in_removes_one_entry_and_ignores_missing() {
    let (_g, t) = setup("project-t7-unlink-");
    save(&t, P, &TestClock::at(BASE_MS));
    t.file(&format!("projects/{P}/assets/a.png"), b"x");
    t.file(&format!("projects/{P}/assets/b.png"), b"y");
    cleanup::unlink_in(&t.path, P, Subdir::Assets, "a.png").expect("刪除");
    assert!(!exists(&t, &format!("projects/{P}/assets/a.png")));
    assert!(exists(&t, &format!("projects/{P}/assets/b.png")));
    let before = t.snapshot();
    for (name, sub, file) in [
        (P, Subdir::Assets, "a.png"),
        (P, Subdir::Audio, "a.wav"),
        ("其他", Subdir::Tmp, "x"),
        ("a/b", Subdir::Assets, "b.png"),
        ("", Subdir::Assets, "b.png"),
    ] {
        cleanup::unlink_in(&t.path, name, sub, file).expect("不存在不算錯");
    }
    assert_eq!(t.snapshot(), before);
    t.dir(&format!("projects/{P}/assets/dir"));
    let e = cleanup::unlink_in(&t.path, P, Subdir::Assets, "dir").expect_err("目錄");
    assert_eq!(
        e.kind(),
        ErrorKind::Fs(safe_fs::ErrorKind::IsADirectory),
        "{e}"
    );
    let e = cleanup::unlink_in(&t.path, P, Subdir::Assets, "..").expect_err("名稱不合法");
    assert_eq!(e.kind(), ErrorKind::Fs(safe_fs::ErrorKind::InvalidName));
}

#[test]
fn unlink_in_symlinks_only_remove_the_link() {
    let (_g, t) = setup("project-t7-unlink-");
    let out = outside(&t);
    save(&t, P, &TestClock::at(BASE_MS));
    t.dir(&format!("projects/{P}/assets"));
    if skip_without_symlink(
        t.symlink(
            &format!("projects/{P}/assets/a.png"),
            &out.join("keep.txt"),
            false,
        ),
        "unlink_in 的連結",
    ) {
        return;
    }
    let before = outside_snapshot(&t);
    cleanup::unlink_in(&t.path, P, Subdir::Assets, "a.png").expect("刪連結");
    assert!(!exists(&t, &format!("projects/{P}/assets/a.png")));
    assert_eq!(outside_snapshot(&t), before);
    // 子目錄是連結:不算錯、什麼都不做(Python 的 UnsafePath 被攔下)
    assert!(t.symlink(&format!("projects/{P}/audio"), &out, true));
    cleanup::unlink_in(&t.path, P, Subdir::Audio, "keep.txt").expect("不算錯");
    assert_eq!(outside_snapshot(&t), before);
}

#[test]
fn check_quota_boundary_and_errors() {
    let (_g, t) = setup("project-t7-quota-");
    save(&t, P, &TestClock::at(BASE_MS));
    t.file(&format!("projects/{P}/assets/a.png"), b"abc");
    t.file(&format!("projects/{P}/audio/a.wav"), b"abcd");
    t.file(&format!("projects/{P}/assets/sub/big.bin"), b"0123456789");
    cleanup::check_quota(&t.path, P, MAX_ASSET_BYTES - 7).expect("剛好等於上限");
    let e = cleanup::check_quota(&t.path, P, MAX_ASSET_BYTES - 6).expect_err("超過 1 位元組");
    assert_eq!(e.kind(), ErrorKind::Quota);
    assert_eq!(e.errors(), [QUOTA_EXCEEDED]);
    assert_eq!(
        cleanup::check_quota(&t.path, P, u64::MAX)
            .expect_err("溢位也算超過")
            .kind(),
        ErrorKind::Quota
    );
    assert_eq!(
        cleanup::check_quota(&t.path, "其他", 0)
            .expect_err("不存在")
            .kind(),
        ErrorKind::NotFound
    );
}

// ---------- 競態與 D-9 ----------

/// Python `test_race_trash_entry_swapped_after_listing_not_followed` 與 `test_race_tmp_dir_swapped_after_open_...`:
/// 列舉之後、刪除之前把過期回收項目(或 `tmp/`)換成指向外部的連結。Rust 沒有可注入的掛勾,改以另一個執行緒在
/// 啟動清理進行中反覆替換(每回合重建夾具),斷言每一回合連結目標都不變;刪除以 handle 相對進行,被換成連結時只刪
/// 連結本身。刪完內容、最後以名稱刪除項目本身時該名稱已是連結(或替換中暫時不存在)者,`rmdir` 失敗、錯誤往上傳(與 Python
/// `safe_fs.rmtree` 最後的 `os.rmdir` 相同),連結目標同樣不變(印出各結果的回合數)。
#[test]
fn race_entries_swapped_during_cleanup_never_touch_outside() {
    let _g = ProjectsGuard::new();
    let t = TempTree::new("project-t7-race-");
    let out = outside(&t);
    if skip_without_symlink(t.symlink("probe", &out, true), "清理中替換成連結") {
        return;
    }
    let before = outside_snapshot(&t);
    let (mut link_left, mut removed, mut failed) = (0, 0, 0);
    for round in 0..40 {
        let root = t.join(&format!("r{round}"));
        let expired = format!("過期-{}-abcd", stamp(BASE_MS - 31 * DAY_MS));
        let entry = root.join("projects").join(".trash").join(&expired);
        let tmp = root.join("projects").join(P).join("tmp");
        std::fs::create_dir_all(entry.join("audio")).expect("建立");
        std::fs::write(entry.join("project.json"), b"{}").expect("寫入");
        std::fs::create_dir_all(&tmp).expect("tmp");
        std::fs::write(tmp.join("殘留.json"), b"x").expect("寫入");
        let (e2, t2, o2) = (entry.clone(), tmp.clone(), out.clone());
        let spin = round * 50;
        let swapper = std::thread::spawn(move || {
            for _ in 0..spin {
                std::hint::spin_loop();
            }
            for p in [&e2, &t2] {
                let moved = p.with_extension("moved");
                if std::fs::rename(p, &moved).is_ok() {
                    safe_fs::support::symlink(&o2, p, true).expect("連結");
                }
            }
        });
        let r = cleanup::startup_cleanup(&root, at_ms(BASE_MS));
        swapper.join().expect("替換執行緒");
        match r {
            Ok(_) => {}
            // 刪完內容、最後以名稱刪除項目本身時該名稱已換成連結(或正在替換、暫時不存在):對該名稱的 rmdir 失敗(不跟隨),
            // 錯誤往上傳——與 Python `os.rmdir(name, dir_fd=...)` 相同(「### 呼叫端指引」:啟動時的呼叫端記警告、下次再試)
            Err(e)
                if matches!(
                    e.kind(),
                    ErrorKind::Fs(safe_fs::ErrorKind::NotADirectory | safe_fs::ErrorKind::NotFound)
                ) =>
            {
                failed += 1
            }
            // Windows:替換與刪除交錯時的共用違規一類錯誤同樣往上傳
            Err(e) if cfg!(windows) && matches!(e.kind(), ErrorKind::Fs(_)) => failed += 1,
            Err(e) => panic!("第 {round} 回合:{e:?}"),
        }
        assert_eq!(outside_snapshot(&t), before, "第 {round} 回合連結目標改變");
        if std::fs::symlink_metadata(&entry).is_ok_and(|m| m.file_type().is_symlink()) {
            link_left += 1;
        } else {
            removed += 1;
        }
    }
    println!(
        "清理中替換成連結:40 回合連結目標皆不變(回收項目留下連結 {link_left} 回合、已不存在 {removed} 回合;其中錯誤往上傳 {failed} 回合)"
    );
}

/// 行為差異 D-9 的 macOS / Linux 對照(Windows 見 `tests/windows.rs`):另一個程式(執行緒 B)在回收進行中已開啟專案目錄、
/// 正在等專案鎖——Unix 上開著的 handle 不擋改名,回收在鎖內改名成功;B 之後取得鎖,經自己的 handle 寫入的檔案落在
/// 回收項目內(與 Python「存檔已開啟專案目錄、正在等鎖時被回收」相同),不損毀資料。
#[cfg(unix)]
#[test]
fn d9_unix_waiting_writer_lands_in_the_trash_entry_like_python() {
    let (_g, t) = setup("project-t7-d9-");
    let clock = TestClock::at(BASE_MS);
    save(&t, P, &clock);
    let (b, written) = waiting_writer(&clock, &t.path);
    let dest = cleanup::trash(&t.path, P, true, &clock).expect("Unix 上鎖內改名成功");
    written
        .recv_timeout(Duration::from_secs(30))
        .expect("B 寫入");
    b.join().expect("B");
    assert!(!exists(&t, &format!("projects/{P}")));
    assert!(
        exists(&t, &format!("projects/.trash/{dest}/b.txt")),
        "B 的寫入在回收項目內"
    );
    assert!(exists(&t, &format!("projects/.trash/{dest}/project.json")));
    println!("D-9(unix):B 的寫入落在回收項目 {dest} 內(與 Python 相同)");
}

/// 執行緒 B:在 `now_ms` 掛勾(回收已取得專案鎖、改名之前)內開啟專案目錄並開始等鎖;取得後寫 `b.txt`。
#[cfg(unix)]
fn waiting_writer(
    clock: &TestClock,
    root: &Path,
) -> (std::thread::JoinHandle<()>, mpsc::Receiver<()>) {
    let (opened_tx, opened_rx) = mpsc::channel();
    let (written_tx, written_rx) = mpsc::channel();
    let slot: Arc<std::sync::Mutex<Option<std::thread::JoinHandle<()>>>> = Arc::default();
    let slot2 = Arc::clone(&slot);
    let root = root.to_path_buf();
    clock.before_next_now(move || {
        let h = std::thread::spawn(move || {
            let dir = read::open_project(&root, P).expect("B 開啟專案");
            opened_tx.send(()).expect("通知");
            let lock = project::lock::ProjectLock::acquire(&dir).expect("B 取鎖");
            let mut f = dir
                .create_excl(
                    "b.txt",
                    safe_fs::FileAccess::Write { truncate: false },
                    0o644,
                )
                .expect("B 寫入");
            f.write_all(b"B").expect("寫入");
            drop(f);
            drop(lock);
            written_tx.send(()).expect("通知");
        });
        opened_rx
            .recv_timeout(Duration::from_secs(30))
            .expect("B 已開啟");
        std::thread::sleep(Duration::from_millis(200));
        *slot2.lock().expect("slot") = Some(h);
    });
    let b = std::thread::spawn(move || {
        let h = loop {
            if let Some(h) = slot.lock().expect("slot").take() {
                break h;
            }
            std::thread::sleep(Duration::from_millis(5));
        };
        h.join().expect("B 執行緒");
    });
    (b, written_rx)
}

/// 存檔中回收的壓力測試(三平台;D-9 的空窗):B 不斷以 `save` 存檔,主執行緒回收。結果為成功(專案搬進回收區;
/// B 之後的存檔建立新專案)或 Windows 上的 [`IN_USE`];不論哪一種,回收區與 `projects/` 內每一份專案檔都能載入。
#[test]
fn trash_while_saving_never_corrupts() {
    let (_g, t) = setup("project-t7-stress-");
    let (mut ok, mut busy) = (0, 0);
    let transient = Arc::new(AtomicUsize::new(0));
    for round in 0..10 {
        let root = t.join(&format!("r{round}"));
        std::fs::create_dir(&root).expect("root");
        write::save(&root, &Project::new(P), &SystemClock).expect("存檔");
        let stop = Arc::new(AtomicBool::new(false));
        let saves = Arc::new(AtomicUsize::new(0));
        let (s2, n2, r2, f2) = (
            Arc::clone(&stop),
            Arc::clone(&saves),
            root.clone(),
            Arc::clone(&transient),
        );
        let b = std::thread::spawn(move || {
            let mut i = 0;
            while !s2.load(Ordering::SeqCst) {
                let mut p = Project::new(P);
                p.script = format!("第 {i} 版");
                match write::save(&r2, &p, &SystemClock) {
                    Ok(_) => {
                        n2.fetch_add(1, Ordering::SeqCst);
                    }
                    // Windows:回收的改名嘗試正以刪除權限開著專案目錄時,同時開啟其中的項目得到共用違規(錯誤碼 32,
                    // 實測)或拒絕存取;這次存檔失敗、不寫入(原子寫入),下一次照常(D-9 T7 補記)
                    Err(e)
                        if cfg!(windows)
                            && matches!(
                                e.kind(),
                                ErrorKind::Fs(
                                    safe_fs::ErrorKind::Other(Some(32))
                                        | safe_fs::ErrorKind::PermissionDenied
                                )
                            ) =>
                    {
                        f2.fetch_add(1, Ordering::SeqCst);
                    }
                    Err(e) => panic!("B 存檔:{e:?}"),
                }
                i += 1;
            }
        });
        while saves.load(Ordering::SeqCst) < 2 && !b.is_finished() {
            std::thread::yield_now();
        }
        let r = cleanup::trash(&root, P, true, &SystemClock);
        stop.store(true, Ordering::SeqCst);
        b.join().expect("B");
        match r {
            Ok(_) => ok += 1,
            Err(e) => {
                assert_eq!(e.errors(), [IN_USE], "{e}");
                if !cfg!(windows) {
                    panic!("只有 Windows 會回「使用中」");
                }
                busy += 1;
            }
        }
        let mut checked = 0;
        for (rel, (kind, _, content)) in common::snapshot_of(&root) {
            if kind == "file" && rel.ends_with(".json") {
                let v = project::validate::parse_project_json(&content.expect("內容"))
                    .unwrap_or_else(|e| panic!("{rel} 無法解析:{e}"));
                Project::from_dict(&v).unwrap_or_else(|e| panic!("{rel} 不合法:{e}"));
                checked += 1;
            }
        }
        assert!(checked > 0);
    }
    println!(
        "存檔中回收:成功 {ok} 回合、使用中 {busy} 回合、B 的存檔因共用違規失敗 {} 次;每一份專案檔與版本檔都能載入",
        transient.load(Ordering::SeqCst)
    );
}

/// Unix 上改名回 `PermissionDenied`(回收區沒有寫入權限)時同樣在鎖外重試一次,仍失敗回 [`IN_USE`](代決 14 不分平台;
/// Python 為 `PermissionError`,D-9 T7 補記),專案不變。以 root 執行時權限不生效:印略過(CI 不以 root 執行)。
#[cfg(unix)]
#[test]
fn unix_permission_denied_rename_retries_then_reports_in_use() {
    use std::os::unix::fs::PermissionsExt;
    let (_g, t) = setup("project-t7-perm-");
    let clock = TestClock::at(BASE_MS);
    save(&t, P, &clock);
    t.dir("projects/.trash");
    let trash = t.join("projects/.trash");
    std::fs::set_permissions(&trash, std::fs::Permissions::from_mode(0o555)).expect("chmod");
    let r = cleanup::trash(&t.path, P, true, &clock);
    std::fs::set_permissions(&trash, std::fs::Permissions::from_mode(0o755)).expect("chmod");
    match r {
        Err(e) => {
            assert_eq!(e.errors(), [IN_USE]);
            assert!(exists(&t, &format!("projects/{P}/project.json")));
            println!("回收區沒有寫入權限:鎖外重試後回「使用中」,專案不變");
        }
        Ok(_) => {
            assert!(!in_ci(), "CI 上權限應生效");
            println!("略過(以 root 執行,權限不生效)");
        }
    }
}

/// `rmtree` 中途失敗時錯誤往上傳、已刪部分不還原(與 Python 相同;「### 呼叫端指引」)。Unix:過期回收項目內有沒有寫入
/// 權限的子目錄(其內檔案無法刪除)。Windows 版(保留裝置名)見 `tests/windows.rs`。
#[cfg(unix)]
#[test]
fn rmtree_failure_propagates_and_is_not_restored() {
    use std::os::unix::fs::PermissionsExt;
    let (_g, t) = setup("project-t7-partial-");
    let name = make_entry(&t, "過期", BASE_MS - 31 * DAY_MS);
    let e = format!("projects/.trash/{name}");
    t.file(&format!("{e}/0.txt"), b"0");
    t.file(&format!("{e}/m/locked.txt"), b"x");
    t.file(&format!("{e}/z.txt"), b"z");
    let locked = t.join(&format!("{e}/m"));
    std::fs::set_permissions(&locked, std::fs::Permissions::from_mode(0o555)).expect("chmod");
    let r = cleanup::startup_cleanup(&t.path, at_ms(BASE_MS));
    std::fs::set_permissions(&locked, std::fs::Permissions::from_mode(0o755)).expect("chmod");
    match r {
        Err(err) => {
            assert_eq!(
                err.kind(),
                ErrorKind::Fs(safe_fs::ErrorKind::PermissionDenied)
            );
            assert!(!exists(&t, &format!("{e}/0.txt")), "已刪部分不還原");
            assert!(exists(&t, &format!("{e}/m/locked.txt")) && exists(&t, &format!("{e}/z.txt")));
            println!("rmtree 中途失敗:錯誤往上傳({err}),已刪的 0.txt 不還原");
        }
        Ok(_) => {
            assert!(!in_ci(), "CI 上權限應生效");
            println!("略過(以 root 執行,權限不生效)");
        }
    }
}

// ---------- macOS / Linux:對 Python 確認 ----------

/// 十進位數字表與 Python `unicodedata` 相同(Python 的 Unicode 版本為 14.0.0 時完全相同,較新時表為其子集)。
#[cfg(unix)]
#[test]
fn decimal_table_matches_python_unicodedata() {
    let out = common::call(&[("@decimal_zeros", vec![])]);
    let common::PyOut::Value(v) = &out[0] else {
        panic!("Python:{out:?}");
    };
    let version = v["version"].as_str().expect("版本");
    let zeros: Vec<u32> = v["zeros"]
        .as_array()
        .expect("陣列")
        .iter()
        .map(|z| z.as_u64().expect("整數") as u32)
        .collect();
    let ours = cleanup::DECIMAL_ZEROS.to_vec();
    if version == "14.0.0" {
        assert_eq!(zeros, ours);
    } else {
        assert!(ours.iter().all(|z| zeros.contains(z)), "{version}");
    }
    println!(
        "十進位數字表:Python unicodedata {version},{} 組",
        zeros.len()
    );
}

/// `_parse_stamp` 的語料(固定種子的 3000 筆:各位置混用 ASCII 與 Unicode 十進位數字)與 Python 相同。
#[cfg(unix)]
#[test]
fn parse_stamp_corpus_matches_python() {
    use serde_json::json;
    let pool: Vec<char> = "0123456789"
        .chars()
        .chain([
            '\u{ff10}',
            '\u{ff11}',
            '\u{ff12}',
            '\u{ff15}',
            '\u{ff19}',
            '\u{660}',
            '\u{669}',
            '\u{966}',
            '\u{1d7ce}',
        ])
        .collect();
    let mut seed: u64 = 0x5eed_7a5c;
    let mut next = move |n: usize| {
        seed ^= seed << 13;
        seed ^= seed >> 7;
        seed ^= seed << 17;
        (seed % n as u64) as usize
    };
    let mut corpus = Vec::new();
    for _ in 0..3000 {
        let base = format!(
            "{:04}{:02}{:02}-{:02}{:02}{:02}-{:03}",
            next(10000),
            next(14),
            next(33),
            next(26),
            next(62),
            next(62),
            next(1000)
        );
        let s: String = base
            .chars()
            .map(|c| {
                if c.is_ascii_digit() && next(4) == 0 {
                    pool[next(pool.len())]
                } else {
                    c
                }
            })
            .collect();
        corpus.push(s);
    }
    let reqs: Vec<(&str, Vec<serde_json::Value>)> = corpus
        .iter()
        .map(|s| ("_parse_stamp", vec![json!(s)]))
        .collect();
    let got = common::call(&reqs);
    let mut valid = 0;
    for (s, py) in corpus.iter().zip(got) {
        let want = match py {
            common::PyOut::Value(serde_json::Value::Null) => None,
            common::PyOut::Value(v) => Some(v.as_f64().expect("浮點")),
            other => panic!("{s:?}:{other:?}"),
        };
        assert_eq!(
            parse_stamp(s).map(f64::to_bits),
            want.map(f64::to_bits),
            "{s:?}"
        );
        valid += usize::from(want.is_some());
    }
    println!(
        "_parse_stamp 語料:{} 筆與 Python 相同(有效 {valid} 筆)",
        corpus.len()
    );
}

/// 同一棵樹(含前段有換行、Unicode 數字時間戳、非一般檔與連結的名稱)分別交給 Rust 與 Python 的 `startup_cleanup`,
/// 結果與清理後的樹(型別與內容)相同。
#[cfg(unix)]
#[test]
fn startup_cleanup_matches_python_on_the_same_tree() {
    use serde_json::json;
    let _g = ProjectsGuard::new();
    let build = |t: &TempTree| {
        let out = outside(t);
        let old = stamp(BASE_MS - 40 * DAY_MS);
        for n in [
            format!("換\n行-{old}-abcd"),
            format!("\n-{old}-abcd"),
            format!("x\r-{old}-abcd"),
            "全形-\u{ff12}0251201-000000-000-abcd".to_string(),
            "阿拉伯-20251201-000000-\u{661}\u{662}\u{663}-abcd".to_string(),
            format!("剛好-{}-abcd", stamp(BASE_MS - 30 * DAY_MS)),
            format!("差一毫秒-{}-abcd", stamp(BASE_MS - 30 * DAY_MS + 1)),
            "沒有時間戳".to_string(),
        ] {
            t.file(&format!("root/projects/.trash/{n}/project.json"), b"{}");
        }
        t.file(&format!("root/projects/.trash/檔案-{old}-abcd"), b"x");
        assert!(t.symlink(&format!("root/projects/.trash/連結-{old}-abcd"), &out, true));
        t.file(&format!("root/projects/{P}/project.json"), b"{}");
        t.file(&format!("root/projects/{P}/tmp/a/b.txt"), b"b");
        assert!(t.symlink(&format!("root/projects/{P}/tmp/外連"), &out, true));
        t.file("root/projects/壞 名稱/tmp/x", b"x");
        assert!(t.symlink("root/projects/外連專案", &out, true));
    };
    let (rt, pt) = (
        TempTree::new("project-t7-cmp-rs-"),
        TempTree::new("project-t7-cmp-py-"),
    );
    build(&rt);
    build(&pt);
    let r = cleanup::startup_cleanup(&rt.join("root"), at_ms(BASE_MS)).expect("Rust");
    let py = common::call_in(
        &pt.join("root"),
        &[("startup_cleanup", vec![json!(BASE_MS as f64 / 1000.0)])],
    );
    assert_eq!(
        py,
        [common::PyOut::Value(json!({
            "trash_removed": r.trash_removed,
            "tmp_cleared": r.tmp_cleared,
            "warnings": r.warnings,
        }))]
    );
    let shape = |t: &TempTree| -> Vec<(String, &'static str, Option<Vec<u8>>)> {
        t.snapshot()
            .into_iter()
            .map(|(k, (kind, _, c))| (k, kind, if kind == "symlink" { None } else { c }))
            .collect()
    };
    assert_eq!(shape(&rt), shape(&pt));
    assert_eq!(r.trash_removed.len(), 5, "{:?}", r.trash_removed);
    println!(
        "startup_cleanup 與 Python 相同:刪除 {} 項、tmp 清除 {} 項、警告 {} 則",
        r.trash_removed.len(),
        r.tmp_cleared,
        r.warnings.len()
    );
}
