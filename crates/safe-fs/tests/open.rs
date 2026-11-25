//! L1:`check_name` 與開啟類函式(`open_dir`、`open_child`、`open_file`、`open_path`)(CHG-20261125-01 T1)。
//!
//! 對應 Python `tests/test_safe_fs.py` 的 `test_open_dir_rejects_symlink_at_any_level`、
//! `test_open_file_rejects_symlink_and_non_regular` 等;三平台執行。符號連結案例在 Windows 本機
//! 無權限時印出略過(CI 上判失敗,見 `common::symlink`);Windows 的 junction 案例在 `windows.rs`。

mod common;

use std::ffi::{OsStr, OsString};
use std::io::{Read, Write};

use common::{LinkKind, WorkDir, snapshot, symlink};
use safe_fs::{DirOptions, ErrorKind, FileAccess, check_name, open_dir, open_path};

const NONE: &[&str] = &[];

fn create() -> DirOptions {
    DirOptions {
        create: true,
        ..DirOptions::default()
    }
}

fn read_all(mut f: std::fs::File) -> String {
    let mut s = String::new();
    f.read_to_string(&mut s).expect("讀取失敗");
    s
}

#[test]
fn check_name_rejects_on_every_platform() {
    for name in ["", ".", "..", "a/b", "/a", "a/", "x\0y"] {
        let e = check_name(OsStr::new(name)).expect_err(name);
        assert_eq!(e.kind(), ErrorKind::InvalidName, "{name:?}");
    }
}

#[test]
fn check_name_windows_only_rules() {
    // 代決 8:Windows 另拒絕 `\`、`:`、`<>"|?*`、控制字元、結尾句點與空白;macOS / Linux 與 Python 相同。
    for name in [
        "a\\b", "a:b", "f.txt.", "f.txt ", "a*b", "a?b", "a<b", "a>b", "a|b", "a\"b", "\u{1}",
        "a\u{1f}b", "...",
    ] {
        let r = check_name(OsStr::new(name));
        if cfg!(windows) {
            assert_eq!(
                r.expect_err(name).kind(),
                ErrorKind::InvalidName,
                "{name:?}"
            );
        } else {
            assert!(r.is_ok(), "{name:?}");
        }
    }
}

#[test]
fn check_name_accepts_ordinary_names() {
    for name in [".a", "a.b", "con", "漢字", "a b", "~x", "a\u{7f}b"] {
        assert!(check_name(OsStr::new(name)).is_ok(), "{name:?}");
    }
}

#[test]
fn check_name_does_not_panic_on_non_unicode() {
    #[cfg(unix)]
    let name = {
        use std::os::unix::ffi::OsStringExt;
        OsString::from_vec(vec![b'a', 0xff, b'b'])
    };
    #[cfg(windows)]
    let name = {
        use std::os::windows::ffi::OsStringExt;
        OsString::from_wide(&[0x61, 0xD800, 0x62])
    };
    assert!(check_name(&name).is_ok());
}

#[test]
fn open_dir_walks_existing_levels_and_creates_when_asked() {
    let w = WorkDir::new();
    std::fs::create_dir_all(w.top().join("a").join("b")).unwrap();
    std::fs::write(w.top().join("a").join("b").join("f.txt"), "inside").unwrap();
    let d = open_dir(&w.top(), &["a", "b"], DirOptions::default()).unwrap();
    assert_eq!(
        read_all(d.open_file("f.txt", FileAccess::Read).unwrap()),
        "inside"
    );

    let e = open_dir(&w.top(), &["a", "nope"], DirOptions::default()).unwrap_err();
    assert_eq!(e.kind(), ErrorKind::NotFound);

    open_dir(&w.top(), &["x", "y", "z"], create()).unwrap();
    assert!(w.top().join("x").join("y").join("z").is_dir());
    // 已存在時 create=true 照常開啟
    open_dir(&w.top(), &["x", "y"], create()).unwrap();
}

#[test]
fn open_dir_checks_every_name_before_opening_anything() {
    let w = WorkDir::new();
    let e = open_dir(&w.top(), &["new", ".."], create()).unwrap_err();
    assert_eq!(e.kind(), ErrorKind::InvalidName);
    assert!(
        !w.top().join("new").exists(),
        "名稱不合法時不得建立任何東西"
    );
}

#[test]
fn open_dir_top_rules() {
    let w = WorkDir::new();
    let e = open_dir(&w.root().join("missing"), NONE, DirOptions::default()).unwrap_err();
    assert_eq!(e.kind(), ErrorKind::NotFound);
    std::fs::write(w.root().join("plain"), "x").unwrap();
    let e = open_dir(&w.root().join("plain"), NONE, DirOptions::default()).unwrap_err();
    assert_eq!(e.kind(), ErrorKind::NotADirectory);
    // top 本身是連結時跟隨(top 視為受信)
    if symlink(&w.outside(), &w.root().join("toplink"), LinkKind::Dir) {
        let d = open_dir(&w.root().join("toplink"), NONE, DirOptions::default()).unwrap();
        assert_eq!(
            read_all(d.open_file("marker.txt", FileAccess::Read).unwrap()),
            "outside"
        );
    }
}

#[test]
fn open_dir_rejects_symlink_or_file_at_any_level() {
    let w = WorkDir::new();
    let top = w.top();
    std::fs::create_dir_all(top.join("a").join("inner")).unwrap();
    std::fs::write(top.join("a").join("plain"), "x").unwrap();
    let before = snapshot(&w.outside());
    // 一般檔不論在中間層或最後一層都拒絕
    for parts in [&["a", "plain"][..], &["a", "plain", "x"][..]] {
        let e = open_dir(&top, parts, DirOptions::default()).unwrap_err();
        assert_eq!(e.kind(), ErrorKind::Unsafe, "{parts:?}");
    }
    let mut made = 0;
    if symlink(&w.outside(), &top.join("out"), LinkKind::Dir) {
        made += 1;
    }
    if symlink(
        &top.join("a").join("inner"),
        &top.join("a").join("in"),
        LinkKind::Dir,
    ) {
        made += 1;
    }
    if made == 0 {
        return;
    }
    for parts in [
        &["out"][..],
        &["out", "x"][..],
        &["a", "in"][..],
        &["a", "in", "x"][..],
    ] {
        for opts in [DirOptions::default(), create()] {
            let e = open_dir(&top, parts, opts).unwrap_err();
            assert_eq!(
                e.kind(),
                ErrorKind::Unsafe,
                "{parts:?} create={}",
                opts.create
            );
        }
    }
    assert_eq!(snapshot(&w.outside()), before, "outside/ 不得有任何變動");
    assert!(!top.join("a").join("inner").join("x").exists());
}

#[test]
fn open_dir_create_over_dangling_link_is_unsafe() {
    let w = WorkDir::new();
    let before = snapshot(&w.outside());
    if !symlink(
        &w.outside().join("ghost"),
        &w.top().join("dang"),
        LinkKind::Dir,
    ) {
        return;
    }
    let e = open_dir(&w.top(), &["dang"], create()).unwrap_err();
    assert_eq!(e.kind(), ErrorKind::Unsafe);
    assert_eq!(snapshot(&w.outside()), before, "不得在連結目標建立任何東西");
}

#[test]
fn open_file_rejects_symlink_and_non_regular() {
    let w = WorkDir::new();
    let top = w.top();
    std::fs::create_dir(top.join("sub")).unwrap();
    std::fs::write(top.join("ok.txt"), "ok").unwrap();
    let d = open_dir(&top, NONE, DirOptions::default()).unwrap();
    assert_eq!(
        read_all(d.open_file("ok.txt", FileAccess::Read).unwrap()),
        "ok"
    );
    let e = d.open_file("sub", FileAccess::Read).unwrap_err();
    assert_eq!(e.kind(), ErrorKind::Unsafe);
    let e = d.open_file("missing", FileAccess::Read).unwrap_err();
    assert_eq!(e.kind(), ErrorKind::NotFound);
    let e = d.open_file("a/b", FileAccess::Read).unwrap_err();
    assert_eq!(e.kind(), ErrorKind::InvalidName);
    let before = snapshot(&w.outside());
    if symlink(
        &w.outside().join("marker.txt"),
        &top.join("fl"),
        LinkKind::File,
    ) {
        for access in [
            FileAccess::Read,
            FileAccess::Write { truncate: true },
            FileAccess::ReadWrite { truncate: false },
        ] {
            let e = d.open_file("fl", access).unwrap_err();
            assert_eq!(e.kind(), ErrorKind::Unsafe, "{access:?}");
        }
    }
    assert_eq!(snapshot(&w.outside()), before);
}

#[test]
fn open_file_write_and_truncate() {
    let w = WorkDir::new();
    std::fs::write(w.top().join("f.txt"), "0123456789").unwrap();
    let d = open_dir(&w.top(), NONE, DirOptions::default()).unwrap();
    {
        let mut f = d
            .open_file("f.txt", FileAccess::Write { truncate: false })
            .unwrap();
        f.write_all(b"ab").unwrap();
    }
    assert_eq!(
        std::fs::read_to_string(w.top().join("f.txt")).unwrap(),
        "ab23456789"
    );
    {
        let mut f = d
            .open_file("f.txt", FileAccess::Write { truncate: true })
            .unwrap();
        f.write_all(b"xy").unwrap();
    }
    assert_eq!(
        std::fs::read_to_string(w.top().join("f.txt")).unwrap(),
        "xy"
    );
    let mut f = d
        .open_file("f.txt", FileAccess::ReadWrite { truncate: false })
        .unwrap();
    let mut s = String::new();
    f.read_to_string(&mut s).unwrap();
    assert_eq!(s, "xy");
    // 檔案不存在時不建立(開啟類函式不建立檔案)
    let e = d
        .open_file("new.txt", FileAccess::Write { truncate: true })
        .unwrap_err();
    assert_eq!(e.kind(), ErrorKind::NotFound);
    assert!(!w.top().join("new.txt").exists());
}

#[test]
fn open_child_opens_subdir_and_rejects_file() {
    let w = WorkDir::new();
    std::fs::create_dir(w.top().join("s")).unwrap();
    std::fs::write(w.top().join("s").join("m"), "m").unwrap();
    std::fs::write(w.top().join("f"), "f").unwrap();
    let d = open_dir(&w.top(), NONE, DirOptions::default()).unwrap();
    let s = d.open_child("s", DirOptions::default()).unwrap();
    assert_eq!(read_all(s.open_file("m", FileAccess::Read).unwrap()), "m");
    assert_eq!(
        d.open_child("f", DirOptions::default()).unwrap_err().kind(),
        ErrorKind::Unsafe
    );
    assert_eq!(
        d.open_child("f", create()).unwrap_err().kind(),
        ErrorKind::Unsafe
    );
    assert_eq!(
        d.open_child("n", DirOptions::default()).unwrap_err().kind(),
        ErrorKind::NotFound
    );
    assert_eq!(
        d.open_child("", create()).unwrap_err().kind(),
        ErrorKind::InvalidName
    );
    d.open_child("n", create()).unwrap();
    assert!(w.top().join("n").is_dir());
}

#[test]
fn open_path_reads_last_component_as_file() {
    let w = WorkDir::new();
    std::fs::create_dir_all(w.top().join("a")).unwrap();
    std::fs::write(w.top().join("a").join("f"), "deep").unwrap();
    assert_eq!(
        read_all(open_path(&w.top(), &["a", "f"], FileAccess::Read).unwrap()),
        "deep"
    );
    let e = open_path(&w.top(), NONE, FileAccess::Read).unwrap_err();
    assert_eq!(e.kind(), ErrorKind::InvalidName);
    let e = open_path(&w.top(), &["a"], FileAccess::Read).unwrap_err();
    assert_eq!(e.kind(), ErrorKind::Unsafe);
    let e = open_path(&w.top(), &["nope", "f"], FileAccess::Read).unwrap_err();
    assert_eq!(e.kind(), ErrorKind::NotFound);
}

#[test]
fn dir_handle_keeps_pointing_at_the_original_directory() {
    // 取得目錄 handle 後原路徑改名,之後的開啟仍落在原目錄(handle 相對,不以路徑重開)。
    let w = WorkDir::new();
    std::fs::create_dir(w.top().join("a")).unwrap();
    std::fs::write(w.top().join("a").join("f"), "original").unwrap();
    let d = open_dir(&w.top(), &["a"], DirOptions::default()).unwrap();
    if let Err(e) = std::fs::rename(w.top().join("a"), w.top().join("moved")) {
        // Windows:目錄 handle 開著時原路徑不能改名(共用違規,錯誤碼 32);路徑無法被換掉,
        // handle 仍指向原目錄。其他平台改名必須成功。
        assert!(
            cfg!(windows) && e.raw_os_error() == Some(32),
            "改名失敗:{e}"
        );
        assert_eq!(
            read_all(d.open_file("f", FileAccess::Read).unwrap()),
            "original"
        );
        return;
    }
    std::fs::create_dir(w.top().join("a")).unwrap();
    std::fs::write(w.top().join("a").join("f"), "replacement").unwrap();
    assert_eq!(
        read_all(d.open_file("f", FileAccess::Read).unwrap()),
        "original"
    );
}

#[test]
fn error_messages_do_not_contain_local_paths() {
    let w = WorkDir::new();
    std::fs::write(w.top().join("plain"), "x").unwrap();
    let root = w.root().to_string_lossy().to_string();
    let errors = [
        open_dir(&w.top(), &["missing"], DirOptions::default()).unwrap_err(),
        open_dir(&w.top(), &["plain"], DirOptions::default()).unwrap_err(),
        open_dir(&w.root().join("nope"), NONE, DirOptions::default()).unwrap_err(),
        open_path(&w.top(), &["plain", "x"], FileAccess::Read).unwrap_err(),
        check_name(OsStr::new("a/b")).unwrap_err(),
    ];
    for e in errors {
        let text = e.to_string();
        assert!(!text.contains(&root), "{text}");
        assert!(!text.contains("safe-fs-test-"), "{text}");
        assert!(!text.is_empty());
    }
}
