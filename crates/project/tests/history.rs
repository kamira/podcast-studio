//! L1:`history/` 封存、嚴格遞增時間戳與輪替(CHG-20261128-01 T6;`project::write`)。
//!
//! 移植 `tests/test_project.py` 的 history 案例(第 11 次存檔留 10 版、不相干檔案不計、撞名重試只換隨機碼、同一毫秒
//! 12 次嚴格遞增、時鐘倒退取最新 + 1 毫秒、`update` / `restore` 也嚴格遞增、`history/` 是連結時拒絕存檔與輪替),
//! 加上**輪替的刪除選取規則**(「## 安全影響」irreversible-delete):只刪名稱完全符合 `HISTORY_RE` 的一般檔中最舊的,
//! 連結(指向檔案、目錄或不存在的目標)、目錄、FIFO、名稱不符(大寫、位數不對、副檔名不同、非 UTF-8)一律不計入、
//! 不刪除,連結目標不變;`history/` 本身是連結或不是目錄時拒絕輪替並回警告。另有時間戳的格式化與解析
//! (`_format_ms` / `_stamp_ms`,有效範圍與 CPython 3.11 相同)與行為差異 D-5(無效日期的版本檔名、超出範圍的
//! 時間戳:Rust 拒絕存檔、不寫入,Python 丟未分類例外——macOS / Linux 以 Python 子程序確認)。
//! 夾具只建在測試自己排他建立的暫存目錄;開頭與結尾確認 repo 工作副本的 `projects/` 不變。Windows 無權限建立
//! 符號連結時,連結案例先 `assert!(!in_ci())` 再印略過數。

mod common;

use std::collections::BTreeMap;
use std::path::PathBuf;

use common::{BASE_MS, ProjectsGuard, TempTree, TestClock, in_ci};
use project::constants::HISTORY_KEEP;
use project::read;
use project::write::{
    self, BAD_HISTORY_NAME, NO_UNIQUE_VERSION, STAMP_OUT_OF_RANGE, format_ms, stamp_ms,
};
use project::{ErrorKind, Project};

const P: &str = "夜市的味道";

fn setup() -> (ProjectsGuard, TempTree) {
    let guard = ProjectsGuard::new();
    let t = TempTree::new("project-t6-history-");
    t.dir("projects");
    (guard, t)
}

fn proj(script: &str) -> Project {
    let mut p = Project::new(P);
    p.script = script.to_string();
    p
}

fn hist(t: &TempTree) -> PathBuf {
    t.join(&format!("projects/{P}/history"))
}

/// `history/` 內的名稱(排序後)。
fn names_in(t: &TempTree) -> Vec<String> {
    let mut v: Vec<String> = std::fs::read_dir(hist(t))
        .expect("讀取 history")
        .map(|e| e.expect("目錄項").file_name().to_string_lossy().to_string())
        .collect();
    v.sort();
    v
}

/// 最小的專案檔原文。
fn pjson(name: &str) -> Vec<u8> {
    format!(r#"{{"schema_version": 2, "name": "{name}"}}"#).into_bytes()
}

fn stamp_of(vid: &str) -> &str {
    &vid[..vid.len() - "-xxxx".len()]
}

fn skip_link(what: &str) {
    assert!(!in_ci(), "CI 上無法建立符號連結,判失敗:{what}");
    println!("略過(Windows 無權限建立符號連結):{what}");
}

#[test]
fn stamp_ms_and_format_ms_are_inverse() {
    for s in [
        "20261027-120000-000",
        "20261027-120000-999",
        "19700101-000000-001",
        "20261231-235959-500",
        "10000101-000000-000",
        "99991231-235959-999",
        "20240229-235959-999",
    ] {
        let ms = stamp_ms(s).unwrap_or_else(|| panic!("{s}"));
        assert_eq!(format_ms(ms).expect("可格式化"), s);
    }
    assert_eq!(stamp_ms("20261027-120000-000"), Some(BASE_MS));
    assert_eq!(
        format_ms(stamp_ms("20261027-235959-999").expect("有效") + 1).expect("可格式化"),
        "20261028-000000-000"
    );
    assert_eq!(format_ms(-1).expect("可格式化"), "19691231-235959-999");
    // 整數毫秒的時間與時間戳一致(Python test_stamp_ms與整數毫秒的時間一致)
    for frac in [0, 125, 500, 875] {
        assert_eq!(
            stamp_ms(&format_ms(BASE_MS + frac).expect("可格式化")),
            Some(BASE_MS + frac)
        );
    }
}

/// `_stamp_ms` 的有效範圍(`strptime("%Y%m%d-%H%M%S")`,施工時以 CPython 3.11.18 逐例核對);macOS / Linux 另以
/// Python 子程序確認 Python 對同一組的結果(有效 → 相同毫秒、無效 → `ValueError`)。
#[test]
fn stamp_ms_validity_matches_python_strptime() {
    let cases = [
        ("20261027-120059-000", true),
        ("20261027-120060-000", false),
        ("20261027-120061-000", false),
        ("00000101-000000-000", false),
        ("00010101-000000-000", true),
        ("09991231-235959-999", true),
        ("20250229-000000-000", false),
        ("20240229-000000-000", true),
        ("21000229-000000-000", false),
        ("20000229-000000-000", true),
        ("20261300-000000-000", false),
        ("20260100-000000-000", false),
        ("20261301-000000-000", false),
        ("20260001-000000-000", false),
        ("20260431-000000-000", false),
        ("20260430-000000-000", true),
        ("20261027-240000-000", false),
        ("20261027-236000-000", false),
        ("20261027-235959-999", true),
        ("99991231-235959-999", true),
    ];
    for (s, ok) in cases {
        assert_eq!(stamp_ms(s).is_some(), ok, "{s}");
    }
    #[cfg(unix)]
    {
        use common::{PyOut, call};
        use serde_json::json;
        let reqs: Vec<(&str, Vec<serde_json::Value>)> =
            cases.iter().map(|(s, _)| ("_stamp_ms", vec![json!(s)])).collect();
        for ((s, _), got) in cases.iter().zip(call(&reqs)) {
            match stamp_ms(s) {
                Some(ms) => assert_eq!(got, PyOut::Value(json!(ms)), "{s}"),
                None => assert_eq!(got, PyOut::Exception("ValueError".to_string()), "{s}"),
            }
        }
    }
}

#[test]
fn format_ms_accepts_only_four_digit_years() {
    let first = stamp_ms("10000101-000000-000").expect("有效");
    let last = stamp_ms("99991231-235959-999").expect("有效");
    assert!(format_ms(first).is_ok() && format_ms(last).is_ok());
    for ms in [first - 1, last + 1, i64::MIN, i64::MAX] {
        let e = format_ms(ms).expect_err("超出範圍");
        assert_eq!(e.kind(), ErrorKind::Invalid);
        assert_eq!(e.errors(), [STAMP_OUT_OF_RANGE]);
    }
}

/// Python test_eleventh_save_keeps_10_history:12 次存檔 → `history/` 10 個,最舊的兩個被刪。
#[test]
fn eleventh_save_keeps_10_history() {
    let (_g, t) = setup();
    let clock = TestClock::at(BASE_MS);
    let mut ids = Vec::new();
    for i in 0..12 {
        clock.set(BASE_MS + i * 1000);
        let saved = write::save(&t.path, &proj(&format!("第 {i} 版")), &clock).expect("存檔");
        assert!(saved.warnings.is_empty());
        ids.push(saved.version);
    }
    assert_eq!(ids[0], None);
    let vids: Vec<String> = ids[1..].iter().map(|v| v.clone().expect("版本")).collect();
    assert_eq!(names_in(&t).len(), HISTORY_KEEP);
    assert_eq!(read::list_history(&t.path, P).expect("列出"), vids[1..]);
    let newest = std::fs::read_to_string(hist(&t).join(format!("{}.json", vids[10]))).expect("讀取");
    assert!(newest.contains("\"script\": \"第 10 版\""));
    for name in names_in(&t) {
        assert!(read::history_stamp(&name).is_some(), "{name}");
    }
}

/// Python test_同一毫秒連續存檔12次版本時間戳嚴格遞增:時間戳依序 +1 毫秒、只留最後 10 個、被刪的是最舊的。
#[test]
fn same_millisecond_12_saves_are_strictly_increasing() {
    let (_g, t) = setup();
    let clock = TestClock::at(BASE_MS);
    let mut ids = Vec::new();
    for i in 0..12 {
        ids.push(
            write::save(&t.path, &proj(&format!("第 {i} 版")), &clock)
                .expect("存檔")
                .version,
        );
    }
    assert_eq!(ids[0], None);
    let vids: Vec<String> = ids[1..].iter().map(|v| v.clone().expect("版本")).collect();
    let stamps: Vec<&str> = vids.iter().map(|v| stamp_of(v)).collect();
    let want: Vec<String> = (0..11).map(|k| format!("20261027-120000-{k:03}")).collect();
    assert_eq!(stamps, want);
    for v in &vids {
        assert!(read::is_version_id(v), "{v}");
    }
    assert_eq!(read::list_history(&t.path, P).expect("列出"), vids[1..]);
    let mut files: Vec<String> = vids[1..].iter().map(|v| format!("{v}.json")).collect();
    files.sort();
    assert_eq!(names_in(&t), files);
    assert!(!hist(&t).join(format!("{}.json", vids[0])).exists(), "被刪的是最舊的那一個");
    let newest = std::fs::read_to_string(hist(&t).join(format!("{}.json", vids[10]))).expect("讀取");
    assert!(newest.contains("\"script\": \"第 10 版\""));
}

/// Python test_時鐘倒退時新版本接在最新時間戳加一毫秒。
#[test]
fn clock_going_backwards_uses_latest_plus_one() {
    let (_g, t) = setup();
    let clock = TestClock::at(BASE_MS + 10_000);
    assert_eq!(write::save(&t.path, &proj(""), &clock).expect("存檔").version, None);
    let v1 = write::save(&t.path, &proj(""), &clock).expect("存檔").version.expect("版本");
    assert_eq!(stamp_of(&v1), "20261027-120010-000");
    clock.set(BASE_MS);
    let v2 = write::save(&t.path, &proj(""), &clock).expect("存檔").version.expect("版本");
    assert_eq!(stamp_of(&v2), "20261027-120010-001");
    assert_eq!(read::list_history(&t.path, P).expect("列出"), [v1, v2]);
}

/// Python test_history_names_unique_when_clock_and_random_collide:撞名(同名的目錄)時只換隨機碼重試,不覆寫該目錄。
#[test]
fn collision_retries_with_a_new_random_code_only() {
    let (_g, t) = setup();
    let clock = TestClock::with_rand(BASE_MS, &["aaaa", "aaaa", "bbbb", "cccc"]);
    assert_eq!(write::save(&t.path, &proj(""), &clock).expect("存檔").version, None);
    let blocker = hist(&t).join("20261027-120000-001-aaaa.json");
    std::fs::create_dir_all(&blocker).expect("建立目錄");
    let v: Vec<String> = (0..3)
        .map(|_| {
            write::save(&t.path, &proj(""), &clock)
                .expect("存檔")
                .version
                .expect("版本")
        })
        .collect();
    assert_eq!(
        v,
        [
            "20261027-120000-000-aaaa",
            "20261027-120000-001-bbbb",
            "20261027-120000-002-cccc"
        ]
    );
    assert!(blocker.is_dir());
    assert_eq!(std::fs::read_dir(&blocker).expect("讀取").count(), 0, "沒有覆寫該目錄");
}

/// 撞名重試 16 次仍失敗 → `Invalid`「無法建立不重複的版本檔名」;`project.json` 與 `history/` 不變。
#[test]
fn version_link_tries_exhausted_changes_nothing() {
    let (_g, t) = setup();
    let clock = TestClock::with_rand(BASE_MS, &["aaaa"]);
    write::save(&t.path, &proj("舊"), &clock).expect("存檔");
    std::fs::create_dir_all(hist(&t).join("20261027-120000-000-aaaa.json")).expect("建立目錄");
    let before = t.snapshot();
    let e = write::save(&t.path, &proj("新"), &clock).expect_err("撞名用盡");
    assert_eq!(e.kind(), ErrorKind::Invalid);
    assert_eq!(e.errors(), [NO_UNIQUE_VERSION]);
    assert_eq!(t.snapshot(), before);
}

/// 刪除選取規則:名稱完全符合 `HISTORY_RE` 的一般檔才計入、才可能被刪;其他一律不動(含連結目標)。
#[test]
fn rotation_deletes_only_the_oldest_matching_regular_files() {
    let (_g, t) = setup();
    let clock = TestClock::at(BASE_MS);
    let h = format!("projects/{P}/history");
    t.file(&format!("projects/{P}/project.json"), &pjson(P));
    for i in 0..11 {
        t.file(&format!("{h}/20260101-0000{i:02}-000-abcd.json"), format!("舊 {i}").as_bytes());
    }
    // 名稱不符(全部用比版本更舊的 2024 年:若被誤計入會最先被刪)
    let unrelated = [
        "note.txt",
        "20240101-000000-000-ABCD.json",
        "20240101-000000-00-abcd.json",
        "20240101-000000-000-abcd.json.bak",
        "20240102-000000-000-abcd.JSON",
        "20240101-000000-000-abcde.json",
        "20240101-000000-000abcd.json",
        "x20240101-000000-000-abcd.json",
        ".20240101-000000-000-abcd.json",
        "20240101-000000-000-abcd.jso",
        "20240101\u{ff10}00000-000-abcd.json",
    ];
    for n in unrelated {
        t.file(&format!("{h}/{n}"), "不相干".as_bytes());
    }
    // 名稱符合但不是一般檔:目錄(含內容)
    t.file(&format!("{h}/20250101-000000-000-dddd.json/inner.txt"), "目錄內".as_bytes());
    // 名稱符合的連結(指向檔案、目錄、不存在的目標)
    t.file("outside/old.json", b"outside");
    t.file("outside/dir/x.txt", b"outside-dir");
    let mut links = 0;
    for (n, target, dir) in [
        ("20250101-000000-001-eeee.json", t.join("outside/old.json"), false),
        ("20250101-000000-002-ffff.json", t.join("outside/dir"), true),
        ("20250101-000000-003-aaaa.json", t.join("outside/missing"), false),
    ] {
        if t.symlink(&format!("{h}/{n}"), &target, dir) {
            links += 1;
        } else {
            skip_link(n);
        }
    }
    // FIFO(macOS / Linux,以 Python 的 os.mkfifo 建立)與非 UTF-8 名稱(Linux)
    #[cfg(unix)]
    let fifo = {
        let path = t.join(&format!("{h}/20250101-000000-004-bbbb.json"));
        let st = std::process::Command::new(common::python())
            .args(["-I", "-B", "-c", "import os, sys; os.mkfifo(sys.argv[1])"])
            .arg(&path)
            .status()
            .expect("啟動 Python");
        assert!(st.success(), "建立 FIFO");
        path
    };
    #[cfg(target_os = "linux")]
    let raw = {
        use std::os::unix::ffi::OsStrExt;
        let name = std::ffi::OsStr::from_bytes(b"20250101-000000-005-\xff\xfe.json");
        let path = hist(&t).join(name);
        std::fs::write(&path, b"raw").expect("建立非 UTF-8 名稱");
        path
    };
    let outside_before = t
        .snapshot()
        .into_iter()
        .filter(|(k, _)| k.starts_with("outside"))
        .collect::<BTreeMap<_, _>>();
    let others_before = t
        .snapshot()
        .into_iter()
        .filter(|(k, _)| k.starts_with(&format!("{h}/")) && !k.starts_with(&format!("{h}/20260101-")))
        .collect::<BTreeMap<_, _>>();
    let saved = write::save(&t.path, &proj("新"), &clock).expect("存檔");
    assert_eq!(saved.version.as_deref(), Some("20261027-120000-000-abcd"));
    assert!(saved.warnings.is_empty());
    let after = t.snapshot();
    // 12 個版本 → 刪最舊的兩個
    for i in 0..11 {
        let p = format!("{h}/20260101-0000{i:02}-000-abcd.json");
        assert_eq!(after.contains_key(&p), i >= 2, "{p}");
    }
    let mut want: Vec<String> = (2..11).map(|i| format!("20260101-0000{i:02}-000-abcd")).collect();
    want.push("20261027-120000-000-abcd".to_string());
    assert_eq!(read::list_history(&t.path, P).expect("列出"), want);
    for (k, v) in &others_before {
        assert_eq!(after.get(k), Some(v), "不得改動:{k}");
    }
    let outside_after: BTreeMap<_, _> = after
        .into_iter()
        .filter(|(k, _)| k.starts_with("outside"))
        .collect();
    assert_eq!(outside_after, outside_before, "連結目標不變");
    #[cfg(unix)]
    assert!(std::fs::symlink_metadata(&fifo).is_ok(), "FIFO 不得刪除");
    #[cfg(target_os = "linux")]
    assert_eq!(std::fs::read(&raw).expect("非 UTF-8 名稱仍在"), b"raw");
    println!("輪替選取規則:名稱不符 {} 個、目錄 1 個、連結 {links} 個不計入且未刪除", unrelated.len());
}

/// 同一時間戳時依 mtime、再依檔名排序(`(stamp, mtime_ns, 檔名)`,CHG-20261122-01)。
#[test]
fn rotation_order_is_stamp_then_mtime_then_name() {
    let (_g, t) = setup();
    let clock = TestClock::at(BASE_MS);
    let h = format!("projects/{P}/history");
    write::save(&t.path, &proj(""), &clock).expect("存檔");
    // 名稱越大 mtime 越舊:最舊的是 000a(不是名稱最小的 0001)
    for i in 1..=10u64 {
        t.file_at(&format!("{h}/20260101-000000-000-{i:04x}.json"), b"{}", 1_767_225_600_000 + (20 - i) * 1000);
    }
    write::save(&t.path, &proj("新"), &clock).expect("存檔");
    assert!(!hist(&t).join("20260101-000000-000-000a.json").exists());
    assert!(hist(&t).join("20260101-000000-000-0001.json").exists());
    let list = read::list_history(&t.path, P).expect("列出");
    assert_eq!(list[0], "20260101-000000-000-0009");
    assert_eq!(list.len(), HISTORY_KEEP);
}

/// Python test_update產生的版本時間戳也嚴格遞增、test_restore產生的版本時間戳也嚴格遞增。
#[test]
fn update_and_restore_are_strictly_increasing() {
    let (_g, t) = setup();
    let clock = TestClock::at(BASE_MS);
    write::create(&t.path, P, "第一句。", None, &clock).expect("建立");
    let v: Vec<String> = [1.1, 1.2, 1.3]
        .iter()
        .map(|s| {
            write::update(&t.path, P, &clock, |p| {
                p.defaults.speed = *s;
                Ok(())
            })
            .expect("修改")
            .version
            .expect("版本")
        })
        .collect();
    let stamps: Vec<&str> = v.iter().map(|x| stamp_of(x)).collect();
    assert_eq!(
        stamps,
        ["20261027-120000-000", "20261027-120000-001", "20261027-120000-002"]
    );
    assert_eq!(read::list_history(&t.path, P).expect("列出"), v);

    let (_g2, t2) = setup();
    let mut p = proj("第 0 版");
    write::save(&t2.path, &p, &clock).expect("存檔");
    p.script = "第 1 版".to_string();
    let v1 = write::save(&t2.path, &p, &clock).expect("存檔").version.expect("版本");
    p.script = "第 2 版".to_string();
    let v2 = write::save(&t2.path, &p, &clock).expect("存檔").version.expect("版本");
    let v3 = write::restore(&t2.path, P, &v1, &clock).expect("還原").version.expect("版本");
    assert_eq!(
        [stamp_of(&v1), stamp_of(&v2), stamp_of(&v3)],
        ["20261027-120000-000", "20261027-120000-001", "20261027-120000-002"]
    );
    assert_eq!(read::list_history(&t2.path, P).expect("列出"), [v1, v2, v3]);
    assert_eq!(read::load(&t2.path, P).expect("載入").script, "第 0 版");
}

/// Python test_history_rotation_refuses_symlinked_dirs:專案目錄或 `history/` 是連結時存檔與輪替都拒絕,連結目標不變。
#[test]
fn symlinked_project_or_history_refuses_save_and_rotation() {
    for which in ["project", "history"] {
        let (_g, t) = setup();
        for i in 0..12 {
            t.file(&format!("outside/20260101-0000{i:02}-000-abcd.json"), b"{}");
        }
        t.file("outside/project.json", br#"{"schema_version": 2, "name": "x"}"#);
        let made = if which == "project" {
            t.symlink(&format!("projects/{P}"), &t.join("outside"), true)
        } else {
            t.file(&format!("projects/{P}/project.json"), &pjson(P));
            t.symlink(&format!("projects/{P}/history"), &t.join("outside"), true)
        };
        if !made {
            skip_link(which);
            continue;
        }
        let outside = |s: &BTreeMap<String, common::Entry>| -> BTreeMap<String, common::Entry> {
            s.iter().filter(|(k, _)| k.starts_with("outside")).map(|(k, v)| (k.clone(), v.clone())).collect()
        };
        let before = outside(&t.snapshot());
        let e = write::save(&t.path, &proj("新內容"), &TestClock::at(BASE_MS)).expect_err("拒絕");
        assert_eq!(e.kind(), ErrorKind::UnsafePath, "{which}");
        let label = if which == "project" { "專案" } else { "history" };
        assert_eq!(e.errors(), [format!("資料目錄「{label}」是符號連結,不接受")]);
        assert_eq!(outside(&t.snapshot()), before, "{which}");
        if which == "history" {
            let dir = read::open_project(&t.path, P).expect("開啟");
            let mut warnings = Vec::new();
            assert_eq!(write::rotate(&dir, P, &mut warnings).expect("輪替"), Vec::<String>::new());
            assert_eq!(
                warnings,
                [format!("專案「{P}」的 history 目錄不安全,拒絕輪替:資料目錄「history」是符號連結,不接受")]
            );
            assert_eq!(outside(&t.snapshot()), before);
        }
    }
}

/// `history/` 不是目錄(一般檔)時:存檔拒絕、輪替回警告,該檔不變(三平台,不需要連結)。
#[test]
fn history_that_is_not_a_directory_is_refused() {
    let (_g, t) = setup();
    t.file(&format!("projects/{P}/project.json"), &pjson(P));
    t.file(&format!("projects/{P}/history"), "不是目錄".as_bytes());
    let e = write::save(&t.path, &proj("新"), &TestClock::at(BASE_MS)).expect_err("拒絕");
    assert_eq!(e.kind(), ErrorKind::UnsafePath);
    assert_eq!(e.errors(), ["資料目錄「history」不在預期位置"]);
    let dir = read::open_project(&t.path, P).expect("開啟");
    let mut warnings = Vec::new();
    assert!(write::rotate(&dir, P, &mut warnings).expect("輪替").is_empty());
    assert_eq!(
        warnings,
        [format!("專案「{P}」的 history 目錄不安全,拒絕輪替:資料目錄「history」不在預期位置")]
    );
    assert_eq!(std::fs::read(hist(&t)).expect("讀取"), "不是目錄".as_bytes());
    // 沒有 history/ 時輪替什麼都不做
    std::fs::remove_file(hist(&t)).expect("刪除");
    assert!(write::rotate(&dir, P, &mut warnings).expect("輪替").is_empty());
}

/// D-5:`history/` 內有符合 `HISTORY_RE` 但日期無效的版本檔名時 Rust 拒絕存檔、不寫入任何東西
/// (Python `_stamp_ms` 丟 `ValueError`;macOS / Linux 以 Python 子程序確認)。
#[test]
fn invalid_history_date_refuses_save() {
    for bad in ["20261332-000000-000-abcd", "20250229-000000-000-abcd", "20261027-120060-000-abcd"] {
        let (_g, t) = setup();
        let clock = TestClock::at(BASE_MS);
        write::save(&t.path, &proj("舊"), &clock).expect("存檔");
        t.file(&format!("projects/{P}/history/{bad}.json"), b"{}");
        let before = t.snapshot();
        let e = write::save(&t.path, &proj("新"), &clock).expect_err("拒絕");
        assert_eq!(e.kind(), ErrorKind::Invalid, "{bad}");
        assert_eq!(e.errors(), [BAD_HISTORY_NAME]);
        assert_eq!(t.snapshot(), before, "{bad}:不寫入任何東西");
        #[cfg(unix)]
        {
            use common::{PyOut, call_each};
            use serde_json::json;
            let got = call_each(&[(
                t.path.as_path(),
                "@save_dict",
                vec![json!({"schema_version": 2, "name": P, "script": "新"})],
            )]);
            assert_eq!(got, [PyOut::Exception("ValueError".to_string())], "{bad}");
        }
    }
}

/// D-5 T6 補記:版本時間戳超出 1000~9999 年時拒絕存檔、不寫入(現在時間太早,或 history 最新已是 9999 年最後一毫秒)。
#[test]
fn stamp_out_of_range_refuses_save() {
    let (_g, t) = setup();
    let clock = TestClock::at(BASE_MS);
    write::save(&t.path, &proj("舊"), &clock).expect("存檔");
    let before = t.snapshot();
    clock.set(stamp_ms("10000101-000000-000").expect("有效") - 1);
    let e = write::save(&t.path, &proj("新"), &clock).expect_err("拒絕");
    assert_eq!(e.errors(), [STAMP_OUT_OF_RANGE]);
    assert_eq!(t.snapshot(), before);
    clock.set(BASE_MS);
    t.file(&format!("projects/{P}/history/99991231-235959-999-abcd.json"), b"{}");
    let before = t.snapshot();
    let e = write::save(&t.path, &proj("新"), &clock).expect_err("拒絕");
    assert_eq!(e.errors(), [STAMP_OUT_OF_RANGE]);
    assert_eq!(t.snapshot(), before);
}
