//! L1:行為差異 D-7——以 Windows 保留名稱建立新專案被拒(CHG-20261128-01 T6,代決 15)。
//!
//! 新名稱一律經 `names::normalize_new_name`:`create`、`save` 到尚不存在的專案、`open_project_create` /
//! `open_subdir_create` 的建立分支,對 24 個保留名稱(`CON`、`PRN`、`AUX`、`NUL`、`COM0`~`COM9`、`LPT0`~`LPT9`)的
//! 大小寫寫法三平台一律回 `Invalid`([`RESERVED_NEW_NAME`]),**不建立任何目錄**(含 `projects/`)。既有的保留名稱
//! 專案(相容名稱 `normalize_name` 接受)在 macOS / Linux 照常存檔,Windows 上回 [`RESERVED_DEVICE`](D-8)。
//! 前綴或延伸(`CONX`、`COM10`、`LPT`、`NULL`)不是保留名稱,照常建立。macOS / Linux 另以 Python 子程序確認
//! Python `create` 接受保留名稱(證明這是真的差異)。夾具只建在測試自己排他建立的暫存目錄。

mod common;

use common::{BASE_MS, ProjectsGuard, TempTree, TestClock};
use project::read::{self, RESERVED_DEVICE, Subdir};
use project::write::{self, RESERVED_NEW_NAME};
use project::{ErrorKind, Project};

const RESERVED: [&str; 24] = [
    "CON", "PRN", "AUX", "NUL", "COM0", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7",
    "COM8", "COM9", "LPT0", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
];

/// 每個名稱的三種寫法:大寫、小寫、首字母以外小寫(例:`CON`、`con`、`Con`)。
fn spellings() -> Vec<String> {
    RESERVED
        .iter()
        .flat_map(|n| {
            let lower = n.to_ascii_lowercase();
            let mixed = format!("{}{}", &n[..1], &lower[1..]);
            [n.to_string(), lower, mixed]
        })
        .collect()
}

#[track_caller]
fn reserved_err<T: std::fmt::Debug>(r: project::Result<T>, name: &str) {
    let e = r.expect_err("預期拒絕");
    assert_eq!(e.kind(), ErrorKind::Invalid, "{name}:{e}");
    assert_eq!(e.errors(), [RESERVED_NEW_NAME], "{name}");
}

#[test]
fn reserved_names_cannot_be_created_and_nothing_is_created() {
    let _g = ProjectsGuard::new();
    let clock = TestClock::at(BASE_MS);
    let names = spellings();
    assert_eq!(names.len(), 72);
    for with_projects in [false, true] {
        let t = TempTree::new("project-t6-names-");
        if with_projects {
            t.dir("projects");
        }
        let before = t.snapshot();
        for name in &names {
            reserved_err(write::create(&t.path, name, "一。", None, &clock), name);
            reserved_err(
                write::save(&t.path, &Project::new(name.as_str()), &clock),
                name,
            );
            reserved_err(write::open_project_create(&t.path, name), name);
            reserved_err(
                write::open_subdir_create(&t.path, name, Subdir::Audio),
                name,
            );
            assert_eq!(t.snapshot(), before, "{name}:不建立任何目錄");
        }
    }
    println!(
        "保留名稱 {} 種寫法 × 4 個建立路徑:全部拒絕、沒有建立任何目錄",
        names.len()
    );
}

#[test]
fn prefixes_and_extensions_of_reserved_names_are_allowed() {
    let _g = ProjectsGuard::new();
    let t = TempTree::new("project-t6-names-");
    let clock = TestClock::at(BASE_MS);
    for name in [
        "CONX", "COM10", "LPT", "NULL", "AUXX", "CON1", "con_1", "PRN-2",
    ] {
        write::create(&t.path, name, "一。", None, &clock).unwrap_or_else(|e| panic!("{name}:{e}"));
        assert_eq!(read::load(&t.path, name).expect("載入").name, name);
    }
}

/// 既有的保留名稱專案(例:從 macOS 帶來的 `CON`):macOS / Linux 照常存檔;Windows 上無法開啟(D-8)。
#[test]
fn existing_reserved_project_is_saved_except_on_windows() {
    let _g = ProjectsGuard::new();
    let clock = TestClock::at(BASE_MS);
    for name in ["CON", "nul", "COM1"] {
        let t = TempTree::new("project-t6-names-");
        t.file(
            &format!("projects/{name}/project.json"),
            format!(r#"{{"schema_version": 2, "name": "{name}"}}"#).as_bytes(),
        );
        let mut p = Project::new(name);
        p.script = "新".to_string();
        let r = write::save(&t.path, &p, &clock);
        if cfg!(windows) {
            let e = r.expect_err("Windows 上無法開啟");
            assert_eq!(e.kind(), ErrorKind::Invalid, "{name}");
            assert_eq!(e.errors(), [RESERVED_DEVICE], "{name}");
        } else {
            let v = r.expect("既有專案照常存檔").version.expect("版本");
            assert_eq!(read::load(&t.path, name).expect("載入").script, "新");
            assert_eq!(read::list_history(&t.path, name).expect("列出"), [v]);
            write::update(&t.path, name, &clock, |p| {
                p.script = "再改".to_string();
                Ok(())
            })
            .expect("修改");
        }
        // 已有只差大小寫的既有項目時,建立另一個寫法回「只差大小寫」而不是保留名稱訊息(檢查順序同 Python)
        let other = if name.chars().all(|c| !c.is_ascii_lowercase()) {
            name.to_ascii_lowercase()
        } else {
            name.to_ascii_uppercase()
        };
        let e = write::save(&t.path, &Project::new(other.as_str()), &clock).expect_err("拒絕");
        assert_eq!(e.kind(), ErrorKind::Exists, "{other}");
    }
}

/// Python 接受以保留名稱建立新專案(差異是真的,不是兩邊都拒;macOS / Linux)。
#[cfg(unix)]
#[test]
fn python_accepts_reserved_names_for_new_projects() {
    use common::{PyOut, call_in};
    use serde_json::json;
    let _g = ProjectsGuard::new();
    let t = TempTree::new("project-t6-names-py-");
    let got = call_in(&t.path, &[("create", vec![json!("CON"), json!("一。")])]);
    assert!(
        matches!(&got[0], PyOut::Value(v) if v["dump"].as_str().is_some_and(|d| d.contains("\"name\": \"CON\""))),
        "{got:?}"
    );
    assert!(t.join("projects/CON/project.json").is_file());
}
