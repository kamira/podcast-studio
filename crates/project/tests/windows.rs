//! L1:Windows 機制(CHG-20261128-01 T4;行為差異 D-8)。T7 另補 D-9(回收時其他 handle 持有專案目錄)。
//!
//! D-8:Windows 上開啟名稱為保留裝置名(`CON`、`nul`、`COM1` 等,`safe-fs` `ReservedDevice`)的既有專案時,讀取 API
//! 明確失敗——`Invalid`「專案名稱是 Windows 保留裝置名,無法在 Windows 上開啟」,不開到裝置、不改動任何東西;
//! `list_projects` 略過這些項目(無法以 handle 取得中繼資料),其他專案照常列出;只差大小寫的判定先於開啟,
//! 照常回「找不到這個專案(已有只差大小寫的其他專案)」。夾具以 `\\?\` 形式的暫存路徑建立(才建得出這些名稱)。
#![cfg(windows)]

mod common;

use common::{ProjectsGuard, TempTree};
use project::ErrorKind;
use project::read::{self, CASE_VARIANT, RESERVED_DEVICE, Subdir};

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
