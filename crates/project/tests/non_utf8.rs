//! L1:非 UTF-8 目錄項(CHG-20261128-01 代決 16;CHG-20261124-01 D-7;Acceptance operation (3))。
//!
//! - Linux:以非 UTF-8 位元組(`0xFF 0xFE`)建立專案目錄、`history/` 版本檔名與素材檔名;
//! - Windows:以不成對 UTF-16 代理(`0xD800`)建立同樣的目錄項;
//! - macOS:斷言檔案系統拒絕建立非 UTF-8 名稱(〔推論〕由 CI 證明;不成立時停下回報)。
//!
//! 結果(代決 16):`list_projects` 略過、`case_variant` 略過(摺疊鍵不可能相等)、`list_history` 略過(`HISTORY_RE`
//! 不會符合)、`disk_usage` 照常計入一般檔的大小(Python 以 `surrogateescape` 名稱同樣計入);呼叫前後樹快照相同。
//! Linux 另以 Python 子程序對同一棵樹確認結果相同。`--nocapture` 印出每個 API 的處理結果。

mod common;

#[cfg(any(target_os = "linux", windows))]
use std::ffi::{OsStr, OsString};

#[cfg(any(target_os = "linux", windows))]
use common::{ProjectsGuard, TempTree};
#[cfg(any(target_os = "linux", windows))]
use project::read;
#[cfg(target_os = "linux")]
use serde_json::json;

#[cfg(any(target_os = "linux", windows))]
const VALID_VID: &str = "20260101-000000-000-abcd";

/// 在暫存樹建立含非 UTF-8 名稱 `bad` 的夾具,逐一檢查讀取 API;回傳暫存樹(Linux 另交給 Python 確認)。
#[cfg(any(target_os = "linux", windows))]
fn check_apis(bad: &OsStr, platform: &str) -> TempTree {
    assert!(bad.to_str().is_none(), "夾具名稱必須不是合法 UTF-8");
    let t = TempTree::new("project-t4-nonutf8-");
    t.file(
        "projects/a/project.json",
        br#"{"schema_version": 2, "name": "a"}"#,
    );
    t.file(
        &format!("projects/a/history/{VALID_VID}.json"),
        br#"{"schema_version": 2, "name": "a"}"#,
    );
    t.file("projects/a/assets/ok.png", b"1");
    let projects = t.path.join("projects");
    // 非 UTF-8 的專案目錄(內含一般檔 project.json)、版本檔名、素材檔名
    let bad_dir = projects.join(bad);
    std::fs::create_dir(&bad_dir).expect("建立非 UTF-8 目錄");
    std::fs::write(bad_dir.join("project.json"), b"{}").expect("寫入");
    let mut version: OsString = OsString::from("20260101-000000-000-abcd");
    version.push(bad);
    version.push(".json");
    std::fs::write(projects.join("a").join("history").join(&version), b"{}").expect("寫入");
    std::fs::write(projects.join("a").join("assets").join(bad), b"1234").expect("寫入");
    let before = t.snapshot();

    let list = read::list_projects(&t.path).expect("list_projects");
    let names: Vec<&str> = list.iter().map(|p| p.name.as_str()).collect();
    assert_eq!(names, ["a"]);
    println!("非 UTF-8({platform}):list_projects 略過 1 項,列出 {names:?}");

    let dir = safe_fs::open_dir(&t.path, &["projects"], safe_fs::DirOptions::default())
        .expect("開啟 projects/");
    assert_eq!(dir.entry_names().expect("列舉").len(), 2, "目錄項確實存在");
    assert_eq!(read::case_variant(&dir, "a").expect("case_variant"), None);
    assert_eq!(read::load(&t.path, "a").expect("load").name, "a");
    println!("非 UTF-8({platform}):case_variant 略過,load 照常");

    let vids = read::list_history(&t.path, "a").expect("list_history");
    assert_eq!(vids, [VALID_VID]);
    println!(
        "非 UTF-8({platform}):list_history 略過 1 項,列出 {} 個版本",
        vids.len()
    );

    assert_eq!(read::disk_usage(&t.path, "a").expect("disk_usage"), 1 + 4);
    println!("非 UTF-8({platform}):disk_usage 計入非 UTF-8 名稱的一般檔(合計 5 位元組)");

    assert_eq!(t.snapshot(), before, "讀取 API 不得改動任何東西");
    t
}

#[cfg(target_os = "linux")]
#[test]
fn linux_non_utf8_bytes_are_skipped_and_python_agrees() {
    use std::os::unix::ffi::OsStrExt;

    use common::PyOut;

    let _g = ProjectsGuard::new();
    let t = check_apis(OsStr::from_bytes(b"\xff\xfe"), "linux");
    let before = t.snapshot();
    let py = common::call_in(
        &t.path,
        &[
            ("list_projects", vec![]),
            ("list_history", vec![json!("a")]),
            ("disk_usage", vec![json!("a")]),
        ],
    );
    assert_eq!(
        py,
        [
            PyOut::Value(json!([{"name": "a", "updated": 1_767_225_600.0}])),
            PyOut::Value(json!([VALID_VID])),
            PyOut::Value(json!(5)),
        ]
    );
    assert_eq!(t.snapshot(), before);
    println!("非 UTF-8(linux):Python 的 list_projects、list_history、disk_usage 結果相同");
}

#[cfg(windows)]
#[test]
fn windows_unpaired_surrogates_are_skipped() {
    use std::os::windows::ffi::OsStringExt;

    let _g = ProjectsGuard::new();
    let bad = OsString::from_wide(&[0xD800, 0x61]);
    drop(check_apis(&bad, "windows"));
}

/// macOS(APFS)拒絕建立非 UTF-8 名稱(〔推論〕,CI 證明)。
#[cfg(target_os = "macos")]
#[test]
fn macos_refuses_non_utf8_names() {
    use std::ffi::OsStr;
    use std::os::unix::ffi::OsStrExt;

    let _g = common::ProjectsGuard::new();
    let t = common::TempTree::new("project-t4-nonutf8-");
    let r = std::fs::create_dir(t.path.join(OsStr::from_bytes(b"\xff\xfe")));
    assert!(r.is_err(), "macOS 應拒絕建立非 UTF-8 名稱");
    println!(
        "非 UTF-8(macos):檔案系統拒絕建立({:?})",
        r.err().map(|e| e.raw_os_error())
    );
}
