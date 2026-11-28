//! 唯讀比對工具端到端(CHG-20261128-01 T4,代決 19、20;退出條件 (2) 的工具;test (8))。
//!
//! `#![cfg(unix)]`:`readonly_check.py` 會 import 頂層 import `fcntl` 的 `project`,Windows 無法執行(代決 10);Windows 上
//! `project-readonly-dump` 的讀取行為由 `tests/read.rs` 與預期結果表覆蓋。
//!
//! 在測試自己排他建立的暫存目錄組一份合成 repo(複製 repo 根目錄的 `project.py`、`names.py`、`safe_fs.py`、
//! `value_checks.py`、`breezy_worker.py`,加上含一般專案、v1 專案、壞檔、名稱不符、版本 3、保留名稱、連結、`.trash/`、
//! `history/` 版本(含壞版本與名稱不符的版本)的 `projects/`),以 argv 清單執行
//! `[python, -I, -B, -X, utf8, readonly_check.py, --repo, <合成 repo>, --rust, <project-readonly-dump>]`,斷言:
//!
//! - 印出的六行與預期逐字相同(只有數量與是 / 否,不含任何專案名稱、版本 id);
//! - **間接寫入**:比對前後整個暫存目錄(合成 repo 全部檔案含 `.py`、連結目標所在的 `outside/`)的快照與 SHA-256 清單
//!   完全相同——Python 與 Rust 兩側都沒有新增、修改、刪除任何檔案或目錄(含 `.lock`、`history/`、`tmp/`、`__pycache__`);
//! - 把合成 repo 改成唯讀權限再跑一次,六行相同(任何寫入嘗試都只會失敗,結果仍一致);
//! - 工具本身的偵測:清單有變動、寫入嘗試被攔下、結果不一致時,第五行或第三、四行反映出來、結論「不通過」;
//!   未以 `-I -B` 啟動時拒絕執行;Rust 執行檔的 `--help` 寫明前置(確認備份可用、關閉 Python 網頁與 MCP 伺服器)。
#![cfg(unix)]

mod common;

use std::collections::BTreeMap;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

use common::{ProjectsGuard, TempTree};

const MODULES: [&str; 5] = [
    "project.py",
    "names.py",
    "safe_fs.py",
    "value_checks.py",
    "breezy_worker.py",
];

fn tool() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("py")
        .join("readonly_check.py")
}

fn dump_bin() -> &'static str {
    env!("CARGO_BIN_EXE_project-readonly-dump")
}

fn body(name: &str) -> String {
    format!(r#"{{"schema_version": 2, "name": "{name}"}}"#)
}

fn full(name: &str) -> String {
    format!(
        r#"{{"schema_version": 2, "name": "{name}", "script": "我們今天很好。", "defaults": {{"speed": 1.0}}, "segments": [{{"id": "0000000000000001", "text": "我們今天很好", "speed": 1.075, "annotations": [{{"index": 0, "zhuyin": "ㄨㄛˇ"}}]}}]}}"#
    )
}

fn v1(name: &str) -> String {
    format!(
        r#"{{"schema_version": 1, "name": "{name}", "segments": [{{"id": "0000000000000003", "text": "我們", "audio": {{"id": "0000000000000004", "seconds": 2}}}}]}}"#
    )
}

/// 合成 repo:`<暫存>/repo`(產品模組 + `projects/`)與 `<暫存>/outside`(連結目標)。
fn synthetic_repo(t: &TempTree) -> PathBuf {
    let src = common::repo_root();
    for m in MODULES {
        let bytes = std::fs::read(src.join(m)).expect("讀取產品模組");
        t.file(&format!("repo/{m}"), &bytes);
    }
    let p = |rel: &str| format!("repo/projects/{rel}");
    t.file(&p("一般/project.json"), full("一般").as_bytes());
    t.file(&p("一般/.lock"), b"");
    t.file(&p("一般/tmp/x.wav"), b"x");
    t.file(&p("一般/assets/a.png"), b"123");
    t.file_at(
        &p("一般/history/20260101-000000-000-aaaa.json"),
        body("一般").as_bytes(),
        1_767_225_600_000,
    );
    t.file(
        &p("一般/history/20260101-000000-001-bbbb.json"),
        v1("一般").as_bytes(),
    );
    t.file(
        &p("一般/history/20260101-000000-002-cccc.json"),
        "{壞".as_bytes(),
    );
    t.file(
        &p("一般/history/20260101-000000-003-dddd.json"),
        body("其他").as_bytes(),
    );
    t.file(&p("一般/history/README.txt"), b"x");
    t.file(&p("舊專案/project.json"), v1("舊專案").as_bytes());
    t.file(&p("壞/project.json"), "{壞掉".as_bytes());
    t.file(&p("名不符/project.json"), body("其他").as_bytes());
    t.file(
        &p("版本3/project.json"),
        r#"{"schema_version": 3, "name": "版本3"}"#.as_bytes(),
    );
    t.file(&p("CON/project.json"), body("CON").as_bytes());
    t.file(&p("歷史連結/project.json"), body("歷史連結").as_bytes());
    t.file(
        &p("無檔/history/20260101-000000-000-aaaa.json"),
        body("無檔").as_bytes(),
    );
    t.file(&p("a b/project.json"), body("a b").as_bytes());
    t.file(
        &p(".trash/舊-20250101-000000-000-abcd/project.json"),
        body("舊").as_bytes(),
    );
    t.file("outside/p/project.json", body("連結").as_bytes());
    t.file("outside/v.json", body("檔連結").as_bytes());
    assert!(t.symlink(&p("連結"), &t.join("outside/p"), true));
    assert!(t.symlink(&p("檔連結/project.json"), &t.join("outside/v.json"), false));
    assert!(t.symlink(&p("歷史連結/history"), &t.join("outside"), true));
    t.join("repo")
}

/// `projects/` 之下(不含本身)的一般檔數與目錄數(不跟隨連結;與工具的計數規則相同,由測試獨立計算)。
fn count(dir: &Path) -> (usize, usize) {
    let (mut files, mut dirs) = (0, 0);
    for e in std::fs::read_dir(dir).expect("read_dir") {
        let e = e.expect("目錄項");
        let m = std::fs::symlink_metadata(e.path()).expect("lstat");
        if m.file_type().is_symlink() {
            continue;
        }
        if m.is_dir() {
            dirs += 1;
            let (f, d) = count(&e.path());
            files += f;
            dirs += d;
        } else if m.is_file() {
            files += 1;
        }
    }
    (files, dirs)
}

fn run_tool(repo: &Path, extra_flags: &[&str]) -> Output {
    let mut args = vec!["-I", "-B", "-X", "utf8"];
    args.retain(|a| !extra_flags.contains(a));
    Command::new(common::python())
        .args(args)
        .arg(tool())
        .arg("--repo")
        .arg(repo)
        .arg("--rust")
        .arg(dump_bin())
        .output()
        .expect("啟動 Python(判失敗,不略過)")
}

/// 整個暫存目錄的 SHA-256 清單(相對路徑 → 型別、內容雜湊或連結目標);以本 crate 自寫的 SHA-256 計算。
fn hash_list(t: &TempTree) -> BTreeMap<String, (String, String)> {
    t.snapshot()
        .into_iter()
        .map(|(rel, (kind, _, content))| {
            let digest = match (kind, content) {
                ("file", Some(c)) => project::sha256::hex_digest(&c),
                ("symlink", Some(c)) => String::from_utf8_lossy(&c).to_string(),
                _ => String::new(),
            };
            (rel, (kind.to_string(), digest))
        })
        .collect()
}

fn expected_lines(repo: &Path) -> String {
    let (files, dirs) = count(&repo.join("projects"));
    format!(
        "唯讀比對:專案 7 個(Python 列出 7、Rust 列出 7;清單一致:是)\n\
         版本檔:4 個\n\
         載入結果一致:11 筆;不一致:0 筆(其中兩邊都拒絕載入:5 筆)\n\
         不一致的序號:無\n\
         檔案清單與雜湊:比對前後各 {files} 個檔案、{dirs} 個目錄;一致:是\n\
         結論:通過\n"
    )
}

#[test]
fn six_lines_match_and_nothing_is_written() {
    let _g = ProjectsGuard::new();
    let t = TempTree::new("project-t4-tool-");
    let repo = synthetic_repo(&t);
    let want = expected_lines(&repo);
    let snap_before = t.snapshot();
    let hashes_before = hash_list(&t);

    let out = run_tool(&repo, &[]);
    let stdout = String::from_utf8(out.stdout).expect("UTF-8");
    assert_eq!(
        out.status.code(),
        Some(0),
        "stdout={stdout} stderr={}",
        String::from_utf8_lossy(&out.stderr)
    );
    print!("{stdout}");
    assert_eq!(stdout, want);
    assert_eq!(stdout.lines().count(), 6);
    for secret in [
        "一般",
        "舊專案",
        "名不符",
        "版本3",
        "CON",
        "歷史連結",
        "20260101",
        "aaaa",
    ] {
        assert!(
            !stdout.contains(secret),
            "輸出不得含專案名稱或版本 id:{secret}"
        );
    }

    let hashes_after = hash_list(&t);
    assert_eq!(
        t.snapshot(),
        snap_before,
        "比對前後整個暫存目錄的快照(含修改時間)必須相同"
    );
    assert_eq!(
        hashes_after, hashes_before,
        "比對前後的 SHA-256 清單必須相同"
    );
    assert!(!t.join("repo/__pycache__").exists());
    assert!(!t.join("repo/projects/版本3/.lock").exists());
    println!(
        "前後雜湊清單:{} 個項目、{} 個檔案;一致",
        hashes_before.len(),
        hashes_before.values().filter(|(k, _)| k == "file").count()
    );
}

fn set_mode_recursive(dir: &Path, dir_mode: u32, file_mode: u32) {
    for e in std::fs::read_dir(dir).expect("read_dir") {
        let e = e.expect("目錄項");
        let m = std::fs::symlink_metadata(e.path()).expect("lstat");
        if m.file_type().is_symlink() {
            continue;
        }
        if m.is_dir() {
            set_mode_recursive(&e.path(), dir_mode, file_mode);
            std::fs::set_permissions(e.path(), std::fs::Permissions::from_mode(dir_mode))
                .expect("chmod");
        } else {
            std::fs::set_permissions(e.path(), std::fs::Permissions::from_mode(file_mode))
                .expect("chmod");
        }
    }
}

/// 合成 repo 全部改成唯讀權限(目錄 0555、檔案 0444)再跑一次:六行相同、快照不變。
#[test]
fn read_only_permissions_give_the_same_result() {
    let _g = ProjectsGuard::new();
    let t = TempTree::new("project-t4-tool-");
    let repo = synthetic_repo(&t);
    let want = expected_lines(&repo);
    set_mode_recursive(&repo, 0o555, 0o444);
    std::fs::set_permissions(&repo, std::fs::Permissions::from_mode(0o555)).expect("chmod");
    let before = t.snapshot();
    let out = run_tool(&repo, &[]);
    let after = t.snapshot();
    std::fs::set_permissions(&repo, std::fs::Permissions::from_mode(0o755)).expect("chmod");
    set_mode_recursive(&repo, 0o755, 0o644);
    let stdout = String::from_utf8(out.stdout).expect("UTF-8");
    assert_eq!(out.status.code(), Some(0), "{stdout}");
    assert_eq!(stdout, want);
    assert_eq!(after, before);
}

/// 未以 `-I -B` 啟動時拒絕執行(exit 2,stdout 為空)。
#[test]
fn tool_refuses_to_run_without_isolated_and_no_bytecode_flags() {
    let _g = ProjectsGuard::new();
    let t = TempTree::new("project-t4-tool-");
    let repo = synthetic_repo(&t);
    let before = t.snapshot();
    for missing in [&["-B"][..], &["-I"][..]] {
        let out = run_tool(&repo, missing);
        assert_eq!(out.status.code(), Some(2), "缺 {missing:?}");
        assert!(out.stdout.is_empty());
    }
    assert_eq!(t.snapshot(), before);
}

/// 工具自己的偵測(載入工具模組的函式直接呼叫):清單變動 → 一致:否;寫入嘗試被攔下並計數、檔案沒有被建立;
/// 兩邊結果不一致 → 列出序號與兩邊的類別,不列名稱。
#[test]
fn tool_detects_changes_blocked_writes_and_mismatches() {
    let _g = ProjectsGuard::new();
    let t = TempTree::new("project-t4-tool-");
    let repo = synthetic_repo(&t);
    let script = r#"
import fcntl, os, runpy, sys
from pathlib import Path
m = runpy.run_path(sys.argv[1], run_name="readonly_probe")
repo = Path(sys.argv[2])
before = m["manifest"](repo)
target = repo / "projects" / "壞" / "project.json"
os.utime(target, ns=(1, 1))
after = m["manifest"](repo)
cmp = {"py_names": ["x"], "rs_names": ["x"], "rows": [(True, False, "ok", "ok")], "versions": 0, "list_same": True}
lines = m["report"](cmp, before, after, 0)
print(lines[4].endswith("一致:否"), lines[5])
guard = m["WriteGuard"]().install(fcntl)
blocked = 0
for attempt in (lambda: os.open(str(repo / "new.txt"), os.O_WRONLY | os.O_CREAT, 0o600),
                lambda: os.mkdir(str(repo / "newdir")),
                lambda: open(str(repo / "new2.txt"), "w"),
                lambda: fcntl.flock(0, fcntl.LOCK_EX),
                lambda: os.rename(str(target), str(target) + ".x")):
    try:
        attempt()
    except PermissionError:
        blocked += 1
fd = os.open(str(target), os.O_RDONLY)
os.close(fd)
guard.uninstall()
print(blocked, guard.attempts, sorted(p.name for p in repo.iterdir() if p.name.startswith("new")))
lines = m["report"](cmp, before, before, guard.attempts)
print(lines[4].endswith("一致:否"), lines[5])
py = [{"projects": {"value": ["a"]}}, {"load": "a", "result": {"dump": "x"}},
      {"history": "a", "result": {"value": ["v"]}}, {"version": ["a", "v"], "result": {"dump": "y"}}]
rs = [{"projects": {"value": ["a"]}}, {"load": "a", "result": {"dump": "x"}},
      {"history": "a", "result": {"value": ["v"]}}, {"version": ["a", "v"], "result": {"class": "project_error", "errors": ["e"]}}]
lines = m["report"](m["compare"](py, rs), before, before, 0)
print(lines[2]); print(lines[3]); print(lines[5])
lines = m["report"](m["compare"](py, [{"projects": {"value": ["b"]}}]), before, before, 0)
print(lines[0]); print(lines[5])
"#;
    let out = Command::new(common::python())
        .args(["-I", "-B", "-X", "utf8", "-c", script])
        .arg(tool())
        .arg(&repo)
        .output()
        .expect("啟動 Python");
    let stdout = String::from_utf8(out.stdout).expect("UTF-8");
    assert!(
        out.status.success(),
        "{stdout}{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert_eq!(
        stdout,
        "True 結論:不通過\n\
         5 5 []\n\
         True 結論:不通過\n\
         載入結果一致:1 筆;不一致:1 筆(其中兩邊都拒絕載入:0 筆)\n\
         不一致的序號:2(ok/project_error)\n\
         結論:不通過\n\
         唯讀比對:專案 1 個(Python 列出 1、Rust 列出 1;清單一致:否)\n\
         結論:不通過\n"
    );
}

/// Rust 執行檔:`--help` 寫明前置、參數錯誤 exit 2;輸出為 JSON 行,第一行是專案清單。
#[test]
fn dump_binary_usage_and_output_format() {
    let _g = ProjectsGuard::new();
    let help = Command::new(dump_bin())
        .arg("--help")
        .output()
        .expect("執行");
    assert_eq!(help.status.code(), Some(0));
    let text = String::from_utf8(help.stdout).expect("UTF-8");
    assert!(text.contains("確認備份可用"), "{text}");
    assert!(text.contains("關閉 Python 網頁與 MCP 伺服器"), "{text}");
    for bad in [
        &[][..],
        &["--root"][..],
        &["--x", "y"][..],
        &["--root", "a", "b"][..],
    ] {
        let out = Command::new(dump_bin()).args(bad).output().expect("執行");
        assert_eq!(out.status.code(), Some(2), "{bad:?}");
        assert!(out.stdout.is_empty());
    }
    let t = TempTree::new("project-t4-tool-");
    let repo = synthetic_repo(&t);
    let before = t.snapshot();
    let out = Command::new(dump_bin())
        .arg("--root")
        .arg(&repo)
        .output()
        .expect("執行");
    assert_eq!(out.status.code(), Some(0));
    assert_eq!(t.snapshot(), before);
    let text = String::from_utf8(out.stdout).expect("UTF-8");
    let lines: Vec<serde_json::Value> = text
        .split('\n')
        .filter(|l| !l.is_empty())
        .map(|l| serde_json::from_str(l).expect("JSON 行"))
        .collect();
    assert_eq!(
        lines[0]["projects"]["value"],
        serde_json::json!(["CON", "一般", "名不符", "壞", "歷史連結", "版本3", "舊專案"])
    );
    // 7 個專案各 2 行(載入、版本清單)+ 4 個版本
    assert_eq!(lines.len(), 1 + 7 * 2 + 4);
}
