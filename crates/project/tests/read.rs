//! L1:讀取 API(CHG-20261128-01 T4;`project::read`)。
//!
//! 移植 `tests/test_project.py` 的載入類案例(名稱不符、版本 3 拒絕、壞檔、5 MB、只差大小寫、`projects/` 與專案目錄是
//! 連結、`load_version` 的版本 id 格式),加上 `list_history` 的排序與略過規則、`list_projects`、`disk_usage`、
//! `open_subdir`;**讀取 API 不建立任何東西**(呼叫前後樹快照相同,`projects/`、`.lock`、`history/` 都不會被建立)。
//! 錯誤訊息逐字與 Python 相同(macOS / Linux 另以 Python 子程序對同一棵樹確認)。夾具只建在測試自己排他建立的暫存
//! 目錄;開頭與結尾確認 repo 工作副本的 `projects/` 不變。Windows 無權限建立符號連結時,連結案例先 `assert!(!in_ci())`
//! 再印略過數。

mod common;

use std::time::{Duration, UNIX_EPOCH};

use common::{ProjectsGuard, TempTree, in_ci};
use project::constants::{MAX_PROJECT_BYTES, NAME_HINT};
use project::read::{
    self, CASE_VARIANT, FILE_UNSAFE, NAME_MISMATCH, NOT_FOUND, Subdir, VERSION_NAME_MISMATCH,
    VERSION_NOT_FOUND,
};
use project::validate::{CORRUPT, NOT_OBJECT, check_version, dump_validated, parse_project_json};
use project::{Error, ErrorKind};
use serde_json::json;

const P: &str = "夜市的味道";
const VID: &str = "20260102-030405-678-abcd";

fn body(name: &str) -> String {
    json!({"schema_version": 2, "name": name}).to_string()
}

fn full(name: &str) -> String {
    json!({"schema_version": 2, "name": name, "script": "我們今天很好。",
           "defaults": {"speed": 1.0},
           "segments": [{"id": "0000000000000001", "text": "我們今天很好", "speed": 1.075,
                         "annotations": [{"index": 0, "zhuyin": "ㄨㄛˇ"}]}]})
    .to_string()
}

fn v1(name: &str) -> String {
    json!({"schema_version": 1, "name": name, "segments": [
        {"id": "0000000000000003", "text": "我們", "audio": {"id": "0000000000000004", "seconds": 2}}]})
    .to_string()
}

fn setup() -> (ProjectsGuard, TempTree) {
    let guard = ProjectsGuard::new();
    let t = TempTree::new("project-t4-read-");
    t.dir("projects");
    (guard, t)
}

fn proj(t: &TempTree, name: &str, content: &str) {
    t.file(&format!("projects/{name}/project.json"), content.as_bytes());
}

#[track_caller]
fn err<T: std::fmt::Debug>(r: project::Result<T>, kind: ErrorKind, msgs: &[&str]) -> Error {
    let e = r.expect_err("預期失敗");
    assert_eq!(e.kind(), kind, "{e}");
    assert_eq!(e.errors(), msgs, "{e}");
    e
}

fn skip_link(what: &str) {
    assert!(!in_ci(), "CI 上無法建立符號連結,判失敗:{what}");
    println!("略過(Windows 無權限建立符號連結):{what}");
}

#[test]
fn load_returns_the_validated_project_and_same_bytes_as_dump() {
    let (_g, t) = setup();
    proj(&t, P, &full(P));
    let p = read::load(&t.path, P).expect("載入");
    let want =
        dump_validated(&parse_project_json(full(P).as_bytes()).expect("解析")).expect("驗證");
    assert_eq!(p.dump_bytes().expect("寫出"), want);
    assert_eq!(p.name, P);
    assert_eq!(p.segments.len(), 1);
}

#[test]
fn v1_project_is_upgraded_in_memory() {
    let (_g, t) = setup();
    proj(&t, P, &v1(P));
    let before = t.snapshot();
    let p = read::load(&t.path, P).expect("載入 v1");
    assert_eq!(p.schema_version, 2);
    assert_eq!(t.snapshot(), before, "只在記憶體升級,不寫回");
}

#[test]
fn name_in_file_must_match_the_directory() {
    let (_g, t) = setup();
    proj(&t, P, &body("其他"));
    err(read::load(&t.path, P), ErrorKind::Invalid, &[NAME_MISMATCH]);
    proj(&t, "a", r#"{"schema_version": 2}"#);
    err(
        read::load(&t.path, "a"),
        ErrorKind::Invalid,
        &[NAME_MISMATCH],
    );
}

#[test]
fn schema_version_3_is_refused() {
    let (_g, t) = setup();
    proj(&t, P, &json!({"schema_version": 3, "name": P}).to_string());
    let e = read::load(&t.path, P).expect_err("拒絕");
    let want = check_version(Some(&json!(3))).expect_err("版本 3");
    assert_eq!(e.errors(), want.errors());
    assert!(e.errors()[0].contains("版本"));
}

#[test]
fn bad_files_are_rejected_without_local_paths() {
    let (_g, t) = setup();
    t.dir(&format!("projects/{P}"));
    err(read::load(&t.path, P), ErrorKind::NotFound, &[NOT_FOUND]);
    for (content, msg) in [
        ("{壞掉", CORRUPT),
        ("", CORRUPT),
        ("[]", NOT_OBJECT),
        ("\u{feff}{}", CORRUPT),
    ] {
        proj(&t, P, content);
        let e = err(read::load(&t.path, P), ErrorKind::Invalid, &[msg]);
        let shown = e.to_string();
        assert!(!shown.contains(&*t.path.to_string_lossy()), "{shown}");
    }
    t.file(&format!("projects/{P}/project.json"), b"{\"a\": \"\xff\"}");
    err(read::load(&t.path, P), ErrorKind::Invalid, &[CORRUPT]);
}

#[test]
fn size_limit_is_5_mb_on_the_raw_bytes() {
    let (_g, t) = setup();
    let b = body(P);
    let mut exact = b.clone().into_bytes();
    exact.resize(MAX_PROJECT_BYTES, b' ');
    t.file(&format!("projects/{P}/project.json"), &exact);
    assert!(read::load(&t.path, P).is_ok(), "恰好 5 MB 可載入");
    exact.push(b' ');
    t.file(&format!("projects/{P}/project.json"), &exact);
    err(
        read::load(&t.path, P),
        ErrorKind::Invalid,
        &["專案檔超過 5 MB,拒絕載入"],
    );
}

#[test]
fn case_only_variant_is_not_found() {
    let (_g, t) = setup();
    proj(&t, "Ep1", &body("Ep1"));
    for name in ["ep1", "EP1"] {
        err(
            read::load(&t.path, name),
            ErrorKind::NotFound,
            &[CASE_VARIANT],
        );
        err(
            read::load_version(&t.path, name, VID),
            ErrorKind::NotFound,
            &[CASE_VARIANT],
        );
        assert_eq!(
            read::list_history(&t.path, name).expect("空清單"),
            Vec::<String>::new()
        );
        err(
            read::disk_usage(&t.path, name),
            ErrorKind::NotFound,
            &[CASE_VARIANT],
        );
    }
    assert_eq!(read::load(&t.path, "Ep1").expect("本身").name, "Ep1");
    let projects = safe_fs::open_dir(&t.path, &["projects"], safe_fs::DirOptions::default())
        .expect("開啟 projects/");
    assert_eq!(
        read::case_variant(&projects, "ep1").expect("列舉"),
        Some("Ep1".to_string())
    );
    assert_eq!(read::case_variant(&projects, "Ep1").expect("列舉"), None);
    assert_eq!(read::case_variant(&projects, "其他").expect("列舉"), None);
}

#[test]
fn not_a_directory_is_unexpected_location() {
    let (_g, t) = setup();
    t.file(&format!("projects/{P}"), b"x");
    err(
        read::load(&t.path, P),
        ErrorKind::UnsafePath,
        &["資料目錄「專案」不在預期位置"],
    );
    let t2 = TempTree::new("project-t4-read-");
    t2.file("projects", b"x");
    err(
        read::load(&t2.path, P),
        ErrorKind::UnsafePath,
        &["資料目錄「projects」不在預期位置"],
    );
    err(
        read::list_projects(&t2.path),
        ErrorKind::UnsafePath,
        &["資料目錄「projects」不在預期位置"],
    );
}

#[test]
fn symlinked_directories_and_files_are_refused_and_targets_unchanged() {
    let (_g, t) = setup();
    let outside = TempTree::new("project-t4-outside-");
    outside.file("p/project.json", body(P).as_bytes());
    outside.file(&format!("projects/{P}/project.json"), body(P).as_bytes());
    outside.file("v.json", body(P).as_bytes());
    let before = outside.snapshot();
    let mut skipped = 0;

    if t.symlink(&format!("projects/{P}"), &outside.join("p"), true) {
        for r in [
            read::load(&t.path, P).map(|_| ()),
            read::list_history(&t.path, P).map(|_| ()),
            read::disk_usage(&t.path, P).map(|_| ()),
            read::open_project(&t.path, P).map(|_| ()),
        ] {
            err(
                r,
                ErrorKind::UnsafePath,
                &["資料目錄「專案」是符號連結,不接受"],
            );
        }
    } else {
        skipped += 1;
    }
    proj(&t, "b", &body("b"));
    if t.symlink("projects/b/history", &outside.path, true) {
        err(
            read::list_history(&t.path, "b"),
            ErrorKind::UnsafePath,
            &["資料目錄「history」是符號連結,不接受"],
        );
        err(
            read::load_version(&t.path, "b", VID),
            ErrorKind::UnsafePath,
            &["資料目錄「history」是符號連結,不接受"],
        );
    } else {
        skipped += 1;
    }
    if t.symlink("projects/c/project.json", &outside.join("v.json"), false) {
        err(read::load(&t.path, "c"), ErrorKind::Invalid, &[FILE_UNSAFE]);
    } else {
        skipped += 1;
    }
    if t.symlink("projects/d/assets", &outside.path, true) {
        proj(&t, "d", &body("d"));
        err(
            read::disk_usage(&t.path, "d"),
            ErrorKind::UnsafePath,
            &["資料目錄「assets」是符號連結,不接受"],
        );
        err(
            read::open_subdir(&t.path, "d", Subdir::Assets),
            ErrorKind::UnsafePath,
            &["資料目錄「assets」是符號連結,不接受"],
        );
    } else {
        skipped += 1;
    }
    let t2 = TempTree::new("project-t4-read-");
    if t2.symlink("projects", &outside.join("projects"), true) {
        err(
            read::load(&t2.path, P),
            ErrorKind::UnsafePath,
            &["資料目錄「projects」是符號連結,不接受"],
        );
        err(
            read::list_projects(&t2.path),
            ErrorKind::UnsafePath,
            &["資料目錄「projects」是符號連結,不接受"],
        );
    } else {
        skipped += 1;
    }
    assert_eq!(outside.snapshot(), before, "連結目標不得改變");
    if skipped > 0 {
        skip_link(&format!("連結案例 {skipped} 組"));
    }
}

#[test]
fn load_version_id_format_and_not_found() {
    let (_g, t) = setup();
    proj(&t, P, &body(P));
    t.file(
        &format!("projects/{P}/history/{VID}.json"),
        full(P).as_bytes(),
    );
    let p = read::load_version(&t.path, P, VID).expect("版本");
    assert_eq!(p.script, "我們今天很好。");
    for vid in [
        "",
        "20260102-030405-678-abc",
        "20260102-030405-678-ABCD",
        "20260102-030405-678-abcd.json",
        "\u{ff12}0260102-030405-678-abcd",
        "../20260102-030405-678-abcd",
        "20260102-030405-678-abce",
        "20260102_030405-678-abcd",
    ] {
        err(
            read::load_version(&t.path, P, vid),
            ErrorKind::NotFound,
            &[VERSION_NOT_FOUND],
        );
        assert!(!read::is_version_id(vid) || vid == "20260102-030405-678-abce");
    }
    proj(&t, "無版本", &body("無版本"));
    err(
        read::load_version(&t.path, "無版本", VID),
        ErrorKind::NotFound,
        &[VERSION_NOT_FOUND],
    );
    err(
        read::load_version(&t.path, "不存在", VID),
        ErrorKind::NotFound,
        &[NOT_FOUND],
    );
    t.file(
        &format!("projects/{P}/history/20260101-000000-000-0000.json"),
        body("其他").as_bytes(),
    );
    err(
        read::load_version(&t.path, P, "20260101-000000-000-0000"),
        ErrorKind::Invalid,
        &[VERSION_NAME_MISMATCH],
    );
    t.file(
        &format!("projects/{P}/history/20260101-000000-000-1111.json"),
        v1(P).as_bytes(),
    );
    let old = read::load_version(&t.path, P, "20260101-000000-000-1111").expect("v1 版本");
    assert_eq!(old.schema_version, 2);
    // 名稱與版本 id 都不合格時,先報名稱(與 Python 相同的檢查順序)
    err(
        read::load_version(&t.path, "a b", "bad"),
        ErrorKind::Invalid,
        &[NAME_HINT],
    );
}

#[test]
fn list_history_order_and_skips() {
    let (_g, t) = setup();
    proj(&t, P, &body(P));
    assert_eq!(
        read::list_history(&t.path, P).expect("沒有 history"),
        Vec::<String>::new()
    );
    let h = |n: &str| format!("projects/{P}/history/{n}");
    let ms = 1_767_225_600_000;
    t.file_at(&h("20260101-000000-000-0000.json"), b"{}", ms + 2000);
    t.file_at(&h("20260101-000000-000-ffff.json"), b"{}", ms + 1000);
    t.file_at(&h("20260101-000000-000-8888.json"), b"{}", ms + 1500);
    t.file_at(&h("20260101-000000-001-1111.json"), b"{}", ms);
    t.file_at(&h("20261399-999999-999-2222.json"), b"{}", ms);
    for junk in [
        "README.txt",
        "20260101-000000-000-abcd.json.bak",
        "20260101-000000-00-abcd.json",
        "20260101-000000-000-ABCD.json",
        ".20260101-000000-000-abcd.json",
        "20260101-000000-000-abcd",
        "\u{ff12}0260101-000000-000-abcd.json",
    ] {
        t.file(&h(junk), b"{}");
    }
    t.dir(&h("20260101-000000-002-3333.json"));
    assert_eq!(
        read::list_history(&t.path, P).expect("清單"),
        [
            "20260101-000000-000-ffff",
            "20260101-000000-000-8888",
            "20260101-000000-000-0000",
            "20260101-000000-001-1111",
            "20261399-999999-999-2222",
        ]
    );
    assert_eq!(
        read::list_history(&t.path, "不存在").expect("空"),
        Vec::<String>::new()
    );
    let t2 = TempTree::new("project-t4-read-");
    assert_eq!(
        read::list_history(&t2.path, P).expect("沒有 projects/"),
        Vec::<String>::new()
    );
    t.file("projects/b/history", b"x");
    proj(&t, "b", &body("b"));
    err(
        read::list_history(&t.path, "b"),
        ErrorKind::UnsafePath,
        &["資料目錄「history」不在預期位置"],
    );
    assert_eq!(
        read::history_stamp("20260101-000000-000-abcd.json"),
        Some("20260101-000000-000")
    );
    assert_eq!(read::history_stamp("20260101-000000-000-abcd.jsonx"), None);
}

#[test]
fn list_projects_rules_and_updated_time() {
    let (_g, t) = setup();
    let ms = 1_767_225_600_000u64;
    t.file_at("projects/b/project.json", body("b").as_bytes(), ms + 500);
    t.file_at("projects/夜市/project.json", "{壞".as_bytes(), ms);
    t.file_at("projects/B2/project.json", body("其他").as_bytes(), 0);
    t.file("projects/.hidden/project.json", b"{}");
    t.file("projects/a b/project.json", b"{}");
    t.file("projects/caf\u{e9}/project.json", b"{}");
    t.file("projects/file", b"x");
    t.dir("projects/no_file/history");
    t.dir("projects/dir_file/project.json");
    t.dir("projects/.trash/x-20260101-000000-000-abcd");
    let list = read::list_projects(&t.path).expect("列出");
    let names: Vec<&str> = list.iter().map(|p| p.name.as_str()).collect();
    assert_eq!(names, ["B2", "b", "夜市"]);
    assert_eq!(list[0].updated, UNIX_EPOCH);
    assert_eq!(
        list[1].updated,
        UNIX_EPOCH + Duration::from_millis(ms + 500)
    );
    assert_eq!(read::mtime_seconds(list[1].updated), 1_767_225_600.5);
    let t2 = TempTree::new("project-t4-read-");
    assert!(
        read::list_projects(&t2.path)
            .expect("沒有 projects/")
            .is_empty()
    );
    assert!(
        read::list_projects(&t2.path.join("missing"))
            .expect("沒有 root")
            .is_empty()
    );
}

#[test]
fn disk_usage_counts_regular_files_in_assets_and_audio() {
    let (_g, t) = setup();
    proj(&t, P, &body(P));
    assert_eq!(read::disk_usage(&t.path, P).expect("沒有子目錄"), 0);
    t.file(&format!("projects/{P}/assets/a.png"), b"12345");
    t.file(&format!("projects/{P}/assets/.hidden"), b"12");
    t.file(&format!("projects/{P}/assets/sub/deep.png"), b"123456");
    t.file(&format!("projects/{P}/audio/b.wav"), "字字".as_bytes());
    t.file(&format!("projects/{P}/tmp/x"), b"1234567");
    t.file(&format!("projects/{P}/history/{VID}.json"), b"12345678");
    assert_eq!(read::disk_usage(&t.path, P).expect("合計"), 5 + 2 + 6);
    err(
        read::disk_usage(&t.path, "不存在"),
        ErrorKind::NotFound,
        &[NOT_FOUND],
    );
    t.file("projects/x/audio", b"x");
    proj(&t, "x", &body("x"));
    err(
        read::disk_usage(&t.path, "x"),
        ErrorKind::UnsafePath,
        &["資料目錄「audio」不在預期位置"],
    );
}

#[test]
fn open_subdir_is_read_only_and_reports_missing() {
    let (_g, t) = setup();
    proj(&t, P, &body(P));
    t.file(&format!("projects/{P}/audio/a.wav"), b"1");
    let before = t.snapshot();
    let audio = read::open_subdir(&t.path, P, Subdir::Audio).expect("audio");
    assert_eq!(audio.entry_names().expect("列舉"), ["a.wav"]);
    for sub in [Subdir::Assets, Subdir::Tmp] {
        let e = read::open_subdir(&t.path, P, sub).expect_err("不存在");
        assert_eq!(e.kind(), ErrorKind::Fs(safe_fs::ErrorKind::NotFound));
    }
    err(
        read::open_subdir(&t.path, "不存在", Subdir::Tmp),
        ErrorKind::NotFound,
        &[NOT_FOUND],
    );
    assert_eq!(t.snapshot(), before, "不建立子目錄");
    assert_eq!(
        [Subdir::Audio, Subdir::Assets, Subdir::Tmp].map(Subdir::as_str),
        ["audio", "assets", "tmp"]
    );
}

#[test]
fn invalid_names_are_rejected_by_every_api() {
    let (_g, t) = setup();
    for name in [
        "",
        "a b",
        ".trash",
        "..",
        "a/b",
        "caf\u{e9}",
        &"ab".repeat(33),
    ] {
        err(read::load(&t.path, name), ErrorKind::Invalid, &[NAME_HINT]);
        err(
            read::load_version(&t.path, name, VID),
            ErrorKind::Invalid,
            &[NAME_HINT],
        );
        err(
            read::list_history(&t.path, name),
            ErrorKind::Invalid,
            &[NAME_HINT],
        );
        err(
            read::disk_usage(&t.path, name),
            ErrorKind::Invalid,
            &[NAME_HINT],
        );
        err(
            read::open_project(&t.path, name).map(|_| ()),
            ErrorKind::Invalid,
            &[NAME_HINT],
        );
    }
}

/// 讀取 API 不建立任何東西:沒有 `projects/` 時不會建立,專案內不會出現 `.lock`、`history/`、`tmp/`,
/// 樹的快照(型別、修改時間、內容)呼叫前後完全相同。
#[test]
fn read_apis_create_nothing() {
    let _g = ProjectsGuard::new();
    let empty = TempTree::new("project-t4-read-");
    let before = empty.snapshot();
    let _ = read::load(&empty.path, P);
    let _ = read::load_version(&empty.path, P, VID);
    let _ = read::list_history(&empty.path, P);
    let _ = read::list_projects(&empty.path);
    let _ = read::disk_usage(&empty.path, P);
    let _ = read::open_project(&empty.path, P);
    let _ = read::open_subdir(&empty.path, P, Subdir::Tmp);
    assert_eq!(empty.snapshot(), before);
    assert!(!empty.join("projects").exists());

    let (_g2, t) = setup();
    proj(&t, P, &full(P));
    t.file(
        &format!("projects/{P}/history/{VID}.json"),
        v1(P).as_bytes(),
    );
    proj(&t, "空", &body("空"));
    proj(&t, "壞", "{壞");
    t.dir("projects/.trash");
    let before = t.snapshot();
    for name in [P, "空", "壞", "不存在", "Ep1"] {
        let _ = read::load(&t.path, name);
        let _ = read::load_version(&t.path, name, VID);
        let _ = read::list_history(&t.path, name);
        let _ = read::disk_usage(&t.path, name);
        for sub in [Subdir::Audio, Subdir::Assets, Subdir::Tmp] {
            let _ = read::open_subdir(&t.path, name, sub);
        }
    }
    let _ = read::list_projects(&t.path);
    assert_eq!(t.snapshot(), before, "讀取 API 不得改動任何東西");
    for n in [".lock", "tmp", "audio", "assets"] {
        assert!(!t.join(&format!("projects/{P}/{n}")).exists(), "{n}");
    }
    assert!(!t.join("projects/空/history").exists());
}

/// Python `_read_project_file` 的 `OSError` 分支:專案檔沒有讀取權限 →「專案檔無法讀取(可能是符號連結)」。
#[cfg(unix)]
#[test]
fn unreadable_project_file_is_reported() {
    use std::os::unix::fs::PermissionsExt;
    let (_g, t) = setup();
    proj(&t, P, &body(P));
    let file = t.join(&format!("projects/{P}/project.json"));
    std::fs::set_permissions(&file, std::fs::Permissions::from_mode(0o000)).expect("chmod");
    if std::fs::read(&file).is_ok() {
        assert!(!in_ci(), "CI 上以 root 執行,無法測權限,判失敗");
        println!("略過:以 root 執行,權限不生效");
        return;
    }
    err(
        read::load(&t.path, P),
        ErrorKind::Invalid,
        &[read::FILE_UNREADABLE],
    );
    std::fs::set_permissions(&file, std::fs::Permissions::from_mode(0o644)).expect("chmod");
}

/// macOS / Linux:同一棵樹交給 Python `project`(`ROOT` 指向同一個暫存目錄),結果與 Rust 相同。
#[cfg(unix)]
#[test]
fn python_reads_the_same_tree_the_same_way() {
    use common::PyOut;
    use serde_json::Value;

    let (_g, t) = setup();
    proj(&t, P, &full(P));
    proj(&t, "Ep1", &v1("Ep1"));
    proj(&t, "壞", "{壞");
    proj(&t, "名不符", &body("其他"));
    t.file(
        &format!("projects/{P}/history/{VID}.json"),
        v1(P).as_bytes(),
    );
    t.file(&format!("projects/{P}/assets/a"), b"123");
    let before = t.snapshot();
    let names = [P, "Ep1", "ep1", "壞", "名不符", "不存在", "a b"];
    let mut reqs: Vec<(&str, Vec<Value>)> = vec![("list_projects", vec![])];
    for n in names {
        reqs.push(("load", vec![json!(n)]));
        reqs.push(("list_history", vec![json!(n)]));
        reqs.push(("load_version", vec![json!(n), json!(VID)]));
        reqs.push(("disk_usage", vec![json!(n)]));
    }
    let py = common::call_in(&t.path, &reqs);
    let dump = |r: project::Result<project::Project>| match r {
        Ok(p) => PyOut::Value(
            json!({"dump": String::from_utf8(p.dump_bytes().expect("寫出")).expect("UTF-8")}),
        ),
        Err(e) => PyOut::Errors(e.errors().to_vec()),
    };
    let value = |r: project::Result<Value>| match r {
        Ok(v) => PyOut::Value(v),
        Err(e) => PyOut::Errors(e.errors().to_vec()),
    };
    let mut rust = vec![value(read::list_projects(&t.path).map(|l| {
        Value::Array(
            l.iter()
                .map(|p| json!({"name": p.name, "updated": read::mtime_seconds(p.updated)}))
                .collect(),
        )
    }))];
    for n in names {
        rust.push(dump(read::load(&t.path, n)));
        rust.push(value(read::list_history(&t.path, n).map(|v| json!(v))));
        rust.push(dump(read::load_version(&t.path, n, VID)));
        rust.push(value(read::disk_usage(&t.path, n).map(|v| json!(v))));
    }
    assert_eq!(py, rust);
    assert_eq!(t.snapshot(), before, "兩邊都不得改動任何東西");
}

/// 代決 20 (b):唯讀比對執行檔的原始碼(含註解與 use 列)不含寫入類函式名稱,只匯入讀取 API
/// (與 test (9) 的 grep 同一組字串;不放寬)。
#[test]
fn readonly_dump_source_calls_only_read_apis() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("src")
        .join("bin")
        .join("project-readonly-dump.rs");
    let src = std::fs::read_to_string(path).expect("讀取原始碼");
    for word in [
        "save",
        "update",
        "restore",
        "create",
        "trash",
        "startup_cleanup",
        "unlink_in",
        "check_quota",
    ] {
        assert!(!src.contains(word), "原始碼不得含 {word}");
    }
    assert!(src.contains("use project::read::{list_history, list_projects, load, load_version};"));
}
