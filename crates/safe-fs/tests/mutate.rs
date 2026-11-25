//! L1:建立、查詢、刪除、搬移類函式(`create_excl`、`open_or_create`、`lstat`、`entries`、`unlink`、
//! `rmtree`、`rename`、`replace`、`hard_link`)與 D-1、D-2、D-8(CHG-20261125-01 T2)。
//!
//! 對應 Python `tests/test_safe_fs.py` 的 `test_create_excl_does_not_follow_or_overwrite`、`test_open_or_create`、
//! `test_entries_lists_with_lstat`、`test_rmtree_removes_tree_but_never_follows_links`、
//! `test_rmtree_on_symlink_removes_only_the_link`、`test_dir_fd_survives_swap_of_its_path`、
//! `test_rename_replace_link`;三平台執行。符號連結案例在 Windows 本機無權限時印出略過(CI 上判失敗,
//! 見 `common::symlink`);Windows 的 junction 案例在 `windows.rs`。

mod common;

use std::ffi::OsString;
use std::io::{Read, Write};

use common::{LinkKind, WorkDir, snapshot, symlink};
use safe_fs::{DirOptions, EntryKind, ErrorKind, FileAccess, SafeDir, open_dir};

const NONE: &[&str] = &[];
const W: FileAccess = FileAccess::Write { truncate: false };
const RW: FileAccess = FileAccess::ReadWrite { truncate: false };

fn read_all(mut f: std::fs::File) -> String {
    let mut s = String::new();
    f.read_to_string(&mut s).expect("讀取失敗");
    s
}

fn top_dir(w: &WorkDir) -> SafeDir {
    open_dir(&w.top(), NONE, DirOptions::default()).unwrap()
}

#[test]
fn create_excl_does_not_follow_or_overwrite() {
    let w = WorkDir::new();
    std::fs::create_dir(w.top().join("b")).unwrap();
    std::fs::write(w.top().join("plain"), "plain").unwrap();
    let d = top_dir(&w);
    let before = snapshot(&w.outside());
    for name in ["plain", "b"] {
        let e = d.create_excl(name, W, 0o600).unwrap_err();
        assert_eq!(e.kind(), ErrorKind::AlreadyExists, "{name}");
    }
    assert_eq!(
        std::fs::read_to_string(w.top().join("plain")).unwrap(),
        "plain"
    );
    if symlink(
        &w.outside().join("new.txt"),
        &w.top().join("dangling"),
        LinkKind::File,
    ) && symlink(
        &w.outside().join("marker.txt"),
        &w.top().join("fl"),
        LinkKind::File,
    ) {
        for name in ["dangling", "fl"] {
            let e = d.create_excl(name, RW, 0o600).unwrap_err();
            assert_eq!(e.kind(), ErrorKind::AlreadyExists, "{name}");
        }
    }
    let mut f = d.create_excl("fresh.txt", W, 0o600).unwrap();
    f.write_all(b"x").unwrap();
    drop(f);
    assert_eq!(
        std::fs::read_to_string(w.top().join("fresh.txt")).unwrap(),
        "x"
    );
    assert!(!w.outside().join("new.txt").exists());
    assert_eq!(snapshot(&w.outside()), before);
    for bad in ["", ".", "..", "a/b", "x\0y"] {
        assert_eq!(
            d.create_excl(bad, W, 0o600).unwrap_err().kind(),
            ErrorKind::InvalidName,
            "{bad:?}"
        );
    }
}

/// D-8:Unix 上 `mode` 照傳(新檔 0o600、新目錄依 `DirOptions::mode`);Windows 忽略(繼承上層 ACL)。
#[cfg(unix)]
#[test]
fn mode_bits_are_applied_on_unix() {
    use std::os::unix::fs::PermissionsExt;
    let w = WorkDir::new();
    let d = top_dir(&w);
    drop(d.create_excl("secret", W, 0o600).unwrap());
    let mode = std::fs::metadata(w.top().join("secret"))
        .unwrap()
        .permissions()
        .mode();
    assert_eq!(mode & 0o777, 0o600);
    drop(d.open_or_create("lock", RW, 0o600).unwrap());
    let mode = std::fs::metadata(w.top().join("lock"))
        .unwrap()
        .permissions()
        .mode();
    assert_eq!(mode & 0o777, 0o600);
    open_dir(
        &w.top(),
        &["private"],
        DirOptions {
            create: true,
            mode: 0o700,
        },
    )
    .unwrap();
    let mode = std::fs::metadata(w.top().join("private"))
        .unwrap()
        .permissions()
        .mode();
    assert_eq!(mode & 0o777, 0o700);
}

#[test]
fn open_or_create_opens_existing_creates_missing_rejects_links() {
    let w = WorkDir::new();
    let d = top_dir(&w);
    {
        let mut f = d.open_or_create("lock", RW, 0o600).unwrap();
        f.write_all(b"1").unwrap();
    }
    // 已存在:直接開(不截斷)
    assert_eq!(read_all(d.open_or_create("lock", RW, 0o600).unwrap()), "1");
    assert!(w.top().join("lock").is_file());
    std::fs::create_dir(w.top().join("sub")).unwrap();
    assert_eq!(
        d.open_or_create("sub", RW, 0o600).unwrap_err().kind(),
        ErrorKind::IsADirectory
    );
    let before = snapshot(&w.outside());
    if symlink(
        &w.outside().join("marker.txt"),
        &w.top().join("ln"),
        LinkKind::File,
    ) && symlink(
        &w.outside().join("ghost"),
        &w.top().join("dang"),
        LinkKind::File,
    ) {
        for name in ["ln", "dang"] {
            let e = d.open_or_create(name, RW, 0o600).unwrap_err();
            assert_eq!(e.kind(), ErrorKind::Unsafe, "{name}");
        }
    }
    assert_eq!(snapshot(&w.outside()), before);
    assert!(!w.outside().join("ghost").exists());
}

#[test]
fn lstat_reports_kind_size_and_identity() {
    let w = WorkDir::new();
    std::fs::write(w.top().join("f"), "12345").unwrap();
    std::fs::write(w.top().join("g"), "other").unwrap();
    std::fs::create_dir(w.top().join("sub")).unwrap();
    let d = top_dir(&w);
    assert_eq!(d.lstat("missing").unwrap(), None);
    let f = d.lstat("f").unwrap().unwrap();
    assert_eq!((f.kind, f.len), (EntryKind::File, 5));
    assert!(f.modified > std::time::UNIX_EPOCH);
    assert_eq!(d.lstat("sub").unwrap().unwrap().kind, EntryKind::Dir);
    // 檔案識別碼:同一檔案的硬連結相同、不同檔案不同(settings 以此辨識同一個資料庫檔)
    d.hard_link("f", &d, "f2").unwrap();
    let f2 = d.lstat("f2").unwrap().unwrap();
    let g = d.lstat("g").unwrap().unwrap();
    assert_eq!(f.identity, f2.identity);
    assert_ne!(f.identity, g.identity);
    assert_eq!(d.lstat("a/b").unwrap_err().kind(), ErrorKind::InvalidName);
    if symlink(&w.outside(), &w.top().join("ln"), LinkKind::Dir) {
        assert_eq!(d.lstat("ln").unwrap().unwrap().kind, EntryKind::Symlink);
    }
}

#[test]
fn entries_are_sorted_with_lstat() {
    let w = WorkDir::new();
    std::fs::create_dir(w.top().join("b")).unwrap();
    for n in ["Z", "a", "_x", "~t", "漢字"] {
        std::fs::write(w.top().join(n), n).unwrap();
    }
    let linked = symlink(&w.outside(), &w.top().join("ln"), LinkKind::Dir);
    let d = top_dir(&w);
    let got: Vec<(OsString, EntryKind)> = d
        .entries()
        .unwrap()
        .into_iter()
        .map(|(n, m)| (n, m.kind))
        .collect();
    let mut want: Vec<(OsString, EntryKind)> = ["Z", "_x", "a", "b", "~t", "漢字"]
        .iter()
        .map(|n| {
            let k = if *n == "b" {
                EntryKind::Dir
            } else {
                EntryKind::File
            };
            (OsString::from(n), k)
        })
        .collect();
    if linked {
        want.insert(4, (OsString::from("ln"), EntryKind::Symlink));
    }
    assert_eq!(got, want);
}

#[test]
fn unlink_and_missing_ok() {
    let w = WorkDir::new();
    std::fs::write(w.top().join("f"), "f").unwrap();
    std::fs::create_dir(w.top().join("sub")).unwrap();
    let d = top_dir(&w);
    d.unlink("f", false).unwrap();
    assert!(!w.top().join("f").exists());
    assert_eq!(
        d.unlink("f", false).unwrap_err().kind(),
        ErrorKind::NotFound
    );
    d.unlink("f", true).unwrap();
    // 對目錄 unlink:Linux EISDIR、macOS EPERM;Windows 改回與 Linux 相同(D-7)
    let want = if cfg!(target_os = "macos") {
        ErrorKind::PermissionDenied
    } else {
        ErrorKind::IsADirectory
    };
    assert_eq!(d.unlink("sub", true).unwrap_err().kind(), want);
    assert!(w.top().join("sub").is_dir());
    let before = snapshot(&w.outside());
    if symlink(&w.outside(), &w.top().join("ln"), LinkKind::Dir) {
        d.unlink("ln", false).unwrap();
        assert!(std::fs::symlink_metadata(w.top().join("ln")).is_err());
    }
    assert_eq!(snapshot(&w.outside()), before);
}

#[test]
fn rmtree_removes_tree_but_never_follows_links() {
    let w = WorkDir::new();
    let victim = w.top().join("a").join("victim");
    std::fs::create_dir_all(victim.join("x").join("y")).unwrap();
    std::fs::write(victim.join("x").join("y").join("z.txt"), "z").unwrap();
    symlink(&w.outside(), &victim.join("out-dir"), LinkKind::Dir);
    symlink(
        &w.outside().join("marker.txt"),
        &victim.join("x").join("out-file"),
        LinkKind::File,
    );
    let before = snapshot(&w.outside());
    let d = open_dir(&w.top(), &["a"], DirOptions::default()).unwrap();
    d.rmtree("victim", false).unwrap();
    d.rmtree("victim", true).unwrap();
    assert_eq!(
        d.rmtree("victim", false).unwrap_err().kind(),
        ErrorKind::NotFound
    );
    assert!(!victim.exists());
    assert_eq!(snapshot(&w.outside()), before);
    // 一般檔:只刪本身
    std::fs::write(w.top().join("a").join("f"), "f").unwrap();
    d.rmtree("f", false).unwrap();
    assert!(!w.top().join("a").join("f").exists());
    assert_eq!(
        d.rmtree("..", true).unwrap_err().kind(),
        ErrorKind::InvalidName
    );
}

#[test]
fn rmtree_on_symlink_removes_only_the_link() {
    let w = WorkDir::new();
    let before = snapshot(&w.outside());
    if !symlink(&w.outside(), &w.top().join("ln"), LinkKind::Dir) {
        return;
    }
    top_dir(&w).rmtree("ln", false).unwrap();
    assert!(std::fs::symlink_metadata(w.top().join("ln")).is_err());
    assert_eq!(snapshot(&w.outside()), before);
}

/// D-2:1500 層的目錄樹以 `rmtree` 刪除成功(Python 在約 1000 層丟 `RecursionError`;Rust 以明確的堆疊
/// 迭代,同時開啟的 handle 有上限)。印出完整路徑長度(K-3;Windows `\\?\` 路徑上限約 32767 字元)。
#[test]
fn rmtree_handles_1500_levels() {
    const DEPTH: usize = 1500;
    let w = WorkDir::new();
    let parts = vec!["d"; DEPTH];
    let deep = open_dir(
        &w.top(),
        &parts,
        DirOptions {
            create: true,
            ..DirOptions::default()
        },
    )
    .unwrap();
    drop(deep.create_excl("leaf.txt", W, 0o600).unwrap());
    drop(deep);
    let top_len = std::path::absolute(w.top())
        .unwrap()
        .to_string_lossy()
        .chars()
        .count();
    let full = top_len + DEPTH * 2 + "/leaf.txt".len();
    println!("D-2 rmtree {DEPTH} 層:最深檔案的完整路徑 {full} 字元");
    let start = std::time::Instant::now();
    top_dir(&w).rmtree("d", false).unwrap();
    println!("D-2 rmtree {DEPTH} 層:刪除成功,耗時 {:?}", start.elapsed());
    assert!(std::fs::read_dir(w.top()).unwrap().next().is_none());
}

/// C-2 / 代決 10(Unix):取得目錄 handle 後原路徑改名並換成連結,之後的 `create_excl` 與 `unlink`
/// 仍落在原目錄,不會寫到連結目標(對應 Python `test_dir_fd_survives_swap_of_its_path`)。
/// Windows 的對應(換成 junction)在 `windows.rs` 的 `dir_handle_survives_swap_to_junction`。
#[cfg(unix)]
#[test]
fn dir_handle_survives_swap_of_its_path() {
    let w = WorkDir::new();
    let b = w.top().join("a").join("b");
    std::fs::create_dir_all(&b).unwrap();
    std::fs::write(b.join("f.txt"), "f").unwrap();
    std::fs::write(w.outside().join("f.txt"), "outside-f").unwrap();
    let before = snapshot(&w.outside());
    let d = open_dir(&w.top(), &["a", "b"], DirOptions::default()).unwrap();
    std::fs::rename(&b, w.top().join("a").join("b-moved")).unwrap();
    symlink(&w.outside(), &b, LinkKind::Dir);
    drop(d.create_excl("new.txt", W, 0o600).unwrap());
    d.unlink("f.txt", false).unwrap();
    let moved = w.top().join("a").join("b-moved");
    assert!(moved.join("new.txt").is_file());
    assert!(!moved.join("f.txt").exists());
    assert_eq!(snapshot(&w.outside()), before);
}

/// D-1(Unix):`open_file` 遇到 FIFO 不阻塞(以 `O_NONBLOCK` 開啟),回 `Unsafe`;讀取與寫入方式都是。
/// Python 版以讀取或寫入方式開啟沒有另一端的 FIFO 都會阻塞(WSL 實測 3 秒內未返回)。
#[cfg(unix)]
#[test]
fn fifo_is_rejected_without_blocking() {
    use std::sync::mpsc;
    use std::time::Duration;
    let w = WorkDir::new();
    let status = std::process::Command::new("mkfifo")
        .arg(w.top().join("fifo"))
        .status()
        .expect("無法執行 mkfifo");
    assert!(status.success());
    let top = w.top();
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let d = open_dir(&top, NONE, DirOptions::default()).unwrap();
        let kinds: Vec<ErrorKind> = [FileAccess::Read, W, RW]
            .into_iter()
            .map(|a| d.open_file("fifo", a).unwrap_err().kind())
            .collect();
        let oc = d.open_or_create("fifo", W, 0o600).unwrap_err().kind();
        tx.send((kinds, oc)).unwrap();
    });
    let (kinds, oc) = rx
        .recv_timeout(Duration::from_secs(10))
        .expect("開啟 FIFO 阻塞超過 10 秒");
    assert_eq!(kinds, vec![ErrorKind::Unsafe; 3]);
    assert_eq!(oc, ErrorKind::Unsafe);
    assert_eq!(
        top_dir(&w).lstat("fifo").unwrap().unwrap().kind,
        EntryKind::Other
    );
}

#[test]
fn rename_replace_link() {
    let w = WorkDir::new();
    std::fs::create_dir_all(w.top().join("a").join("b")).unwrap();
    std::fs::write(w.top().join("a").join("b").join("f.txt"), "f").unwrap();
    let a = open_dir(&w.top(), &["a"], DirOptions::default()).unwrap();
    let b = open_dir(&w.top(), &["a", "b"], DirOptions::default()).unwrap();
    b.hard_link("f.txt", &a, "hard.txt").unwrap();
    assert_eq!(
        b.hard_link("f.txt", &a, "hard.txt").unwrap_err().kind(),
        ErrorKind::AlreadyExists
    );
    a.rename("hard.txt", &a, "renamed.txt").unwrap();
    {
        let mut t = a.create_excl("tmp", W, 0o600).unwrap();
        t.write_all(b"new").unwrap();
    }
    a.replace("tmp", &a, "renamed.txt").unwrap();
    assert_eq!(
        std::fs::read_to_string(w.top().join("a").join("renamed.txt")).unwrap(),
        "new"
    );
    assert!(!w.top().join("a").join("tmp").exists());
    // 原檔不受取代影響(硬連結已被取代掉,原檔內容不變)
    assert_eq!(
        std::fs::read_to_string(w.top().join("a").join("b").join("f.txt")).unwrap(),
        "f"
    );
    // 目錄改名到既有一般檔:NotADirectory,且一般檔不被取代(Windows 事先檢查)
    std::fs::create_dir(w.top().join("a").join("dd")).unwrap();
    assert_eq!(
        a.rename("dd", &a, "renamed.txt").unwrap_err().kind(),
        ErrorKind::NotADirectory
    );
    assert_eq!(
        std::fs::read_to_string(w.top().join("a").join("renamed.txt")).unwrap(),
        "new"
    );
    for (s, t) in [("..", "x"), ("renamed.txt", "a/b"), ("", "x")] {
        assert_eq!(
            a.rename(s, &a, t).unwrap_err().kind(),
            ErrorKind::InvalidName
        );
        assert_eq!(
            a.hard_link(s, &a, t).unwrap_err().kind(),
            ErrorKind::InvalidName
        );
    }
}

/// C-4:硬連結的來源是符號連結。Unix 與 Python `follow_symlinks=False` 相同:連結到符號連結本身;
/// Windows 回 `Unsafe`(D-3;junction 的案例在 `windows.rs`)。
#[test]
fn hard_link_with_symlink_source() {
    let w = WorkDir::new();
    let before = snapshot(&w.outside());
    if !symlink(
        &w.outside().join("marker.txt"),
        &w.top().join("fl"),
        LinkKind::File,
    ) {
        return;
    }
    let d = top_dir(&w);
    let r = d.hard_link("fl", &d, "hl");
    if cfg!(unix) {
        r.expect("Unix 上應連結到符號連結本身");
        assert_eq!(d.lstat("hl").unwrap().unwrap().kind, EntryKind::Symlink);
    } else {
        assert_eq!(
            r.expect_err("Windows 上來源是連結時應回 Unsafe").kind(),
            ErrorKind::Unsafe
        );
        assert!(d.lstat("hl").unwrap().is_none());
    }
    assert_eq!(snapshot(&w.outside()), before);
}

#[test]
fn mutating_errors_do_not_contain_local_paths() {
    let w = WorkDir::new();
    std::fs::write(w.top().join("plain"), "x").unwrap();
    std::fs::create_dir(w.top().join("sub")).unwrap();
    let d = top_dir(&w);
    let root = w.root().to_string_lossy().to_string();
    let errors = [
        d.create_excl("plain", W, 0o600).unwrap_err(),
        d.unlink("missing", false).unwrap_err(),
        d.rmtree("missing", false).unwrap_err(),
        d.rename("missing", &d, "x").unwrap_err(),
        d.hard_link("plain", &d, "sub").unwrap_err(),
        d.open_or_create("sub", RW, 0o600).unwrap_err(),
    ];
    for e in errors {
        let text = e.to_string();
        assert!(!text.contains(&root), "{text}");
        assert!(!text.contains("safe-fs-test-"), "{text}");
    }
}
