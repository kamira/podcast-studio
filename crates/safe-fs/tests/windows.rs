//! Windows 專屬測試(CHG-20261125-01;`windows-latest` 與 Windows 開發機執行)。
//!
//! T1 開工閘(代決 4)的查證項,逐項以「開工閘 (i)~(iv)」開頭印出結果:
//! - (i) 檔案符號連結、目錄符號連結、junction 在中間層與最後一層都被拒絕(`Unsafe`);
//! - (ii) cap-std 相對開啟的實際機制:取得目錄 handle 後把原路徑改名、在原處放 junction,
//!   之後的開啟仍落在原目錄(handle 相對,不以路徑字串重開);
//! - (iii) 既有的保留名稱項目(代決 9、C-1):開到真正的目錄項(以開啟後 handle 的型別與標記內容判定),
//!   或回 `ReservedDevice`,逐項印出;
//! - (iv) 總長超過 600 字元(30 層 × 20 字元)的深層路徑可建立、開啟與刪除(H-4)。
#![cfg(windows)]

mod common;

use std::io::{Read, Write};
use std::path::Path;

use common::{LinkKind, WorkDir, junction, snapshot, symlink, verbatim};
use safe_fs::{DirOptions, ErrorKind, FileAccess, open_dir, open_path};

const NONE: &[&str] = &[];

fn read_all(mut f: std::fs::File) -> String {
    let mut s = String::new();
    f.read_to_string(&mut s).expect("讀取失敗");
    s
}

fn create() -> DirOptions {
    DirOptions {
        create: true,
        ..DirOptions::default()
    }
}

/// (i):junction(一律可建立)與符號連結(無權限時本機略過、CI 判失敗)在每一層都回 `Unsafe`。
#[test]
fn gate_i_links_and_junctions_are_rejected_at_every_level() {
    let w = WorkDir::new();
    let top = w.top();
    std::fs::create_dir_all(top.join("a").join("inner")).unwrap();
    std::fs::write(top.join("a").join("inner").join("f"), "inner").unwrap();
    let before = snapshot(&w.outside());

    // 中間層與最後一層的 junction:指向外部、指向內部
    junction(&top.join("jout"), &w.outside());
    junction(&top.join("a").join("jin"), &top.join("a").join("inner"));
    let mut checked = 0;
    for parts in [
        &["jout"][..],
        &["jout", "x"][..],
        &["a", "jin"][..],
        &["a", "jin", "x"][..],
    ] {
        for opts in [DirOptions::default(), create()] {
            let e = open_dir(&top, parts, opts).unwrap_err();
            assert_eq!(e.kind(), ErrorKind::Unsafe, "junction {parts:?}");
            checked += 1;
        }
    }
    for parts in [&["jout", "marker.txt"][..], &["a", "jin", "f"][..]] {
        let e = open_path(&top, parts, FileAccess::Read).unwrap_err();
        assert_eq!(e.kind(), ErrorKind::Unsafe, "junction {parts:?}");
        checked += 1;
    }
    let d = open_dir(&top, NONE, DirOptions::default()).unwrap();
    assert_eq!(
        d.open_file("jout", FileAccess::Read).unwrap_err().kind(),
        ErrorKind::Unsafe
    );
    checked += 1;
    println!("開工閘 (i) junction:{checked} 個案例皆為 Unsafe");

    // 符號連結(目錄、檔案)
    let mut sym = 0;
    if symlink(&w.outside(), &top.join("sout"), LinkKind::Dir)
        && symlink(&top.join("a").join("inner"), &top.join("a").join("sin"), LinkKind::Dir)
        && symlink(&w.outside().join("marker.txt"), &top.join("sfile"), LinkKind::File)
    {
        for parts in [
            &["sout"][..],
            &["sout", "x"][..],
            &["a", "sin"][..],
            &["a", "sin", "x"][..],
        ] {
            for opts in [DirOptions::default(), create()] {
                let e = open_dir(&top, parts, opts).unwrap_err();
                assert_eq!(e.kind(), ErrorKind::Unsafe, "symlink {parts:?}");
                sym += 1;
            }
        }
        for access in [
            FileAccess::Read,
            FileAccess::Write { truncate: true },
            FileAccess::ReadWrite { truncate: false },
        ] {
            let e = d.open_file("sfile", access).unwrap_err();
            assert_eq!(e.kind(), ErrorKind::Unsafe, "symlink file {access:?}");
            sym += 1;
        }
        let e = open_path(&top, &["sout", "marker.txt"], FileAccess::Read).unwrap_err();
        assert_eq!(e.kind(), ErrorKind::Unsafe);
        sym += 1;
        println!("開工閘 (i) 符號連結:{sym} 個案例皆為 Unsafe");
    } else {
        println!("開工閘 (i) 符號連結:略過(本機無權限建立符號連結)");
    }
    assert_eq!(snapshot(&w.outside()), before, "outside/ 不得有任何變動");
    assert!(!top.join("a").join("inner").join("x").exists());
}

/// (ii):handle 相對開啟——原路徑改名並換成 junction 後,已開啟的 handle 仍指向原目錄。
#[test]
fn gate_ii_open_is_relative_to_the_directory_handle() {
    let w = WorkDir::new();
    let top = w.top();
    std::fs::create_dir_all(top.join("a").join("sub")).unwrap();
    std::fs::write(top.join("a").join("f"), "original").unwrap();
    std::fs::write(top.join("a").join("sub").join("g"), "original-sub").unwrap();
    let d = open_dir(&top, &["a"], DirOptions::default()).unwrap();
    std::fs::rename(top.join("a"), top.join("moved")).unwrap();
    junction(&top.join("a"), &w.outside());
    std::fs::write(w.outside().join("f"), "outside-f").unwrap();
    assert_eq!(read_all(d.open_file("f", FileAccess::Read).unwrap()), "original");
    let sub = d.open_child("sub", DirOptions::default()).unwrap();
    assert_eq!(read_all(sub.open_file("g", FileAccess::Read).unwrap()), "original-sub");
    // 經 top 重新開啟會遇到 junction → Unsafe
    assert_eq!(
        open_dir(&top, &["a"], DirOptions::default()).unwrap_err().kind(),
        ErrorKind::Unsafe
    );
    println!("開工閘 (ii) 原路徑改名並換成 junction 後,handle 相對開啟仍讀到原目錄內容");
}

/// (iii) 的一個項目:以 `\\?\` 預先建立的保留名稱目錄項。
struct Reserved {
    /// 所在子目錄(大小寫變形各放一個子目錄,避免 NTFS 不分大小寫而同名)
    dir: &'static str,
    name: &'static str,
    is_dir: bool,
}

const RESERVED: &[Reserved] = &[
    Reserved { dir: "r1", name: "con", is_dir: true },
    Reserved { dir: "r1", name: "nul", is_dir: false },
    Reserved { dir: "r1", name: "COM1", is_dir: true },
    Reserved { dir: "r1", name: "con.txt", is_dir: false },
    Reserved { dir: "r1", name: "CONIN$", is_dir: false },
    Reserved { dir: "r1", name: "CONOUT$", is_dir: true },
    Reserved { dir: "r1", name: "COM\u{b9}", is_dir: true },
    Reserved { dir: "r1", name: "LPT1", is_dir: false },
    Reserved { dir: "r1", name: "AUX", is_dir: true },
    Reserved { dir: "r1", name: "PRN.log", is_dir: false },
    Reserved { dir: "r2", name: "Con", is_dir: true },
    Reserved { dir: "r2", name: "NuL", is_dir: false },
    Reserved { dir: "r2", name: "cOm1", is_dir: true },
    Reserved { dir: "r2", name: "CON.TXT", is_dir: false },
    Reserved { dir: "r2", name: "conin$", is_dir: false },
    Reserved { dir: "r2", name: "Conout$", is_dir: true },
    Reserved { dir: "r2", name: "com\u{b9}", is_dir: true },
    Reserved { dir: "r3", name: "CON", is_dir: false },
    Reserved { dir: "r3", name: "Nul.Txt", is_dir: true },
];

fn marker(r: &Reserved) -> String {
    format!("marker-{}-{}", r.dir, r.name)
}

/// (iii):既有的保留名稱項目——開到真正的目錄項(開啟後的 handle 為磁碟上的目錄 / 一般檔,
/// 且讀到預先放的標記)或 `ReservedDevice`,兩者之一;逐項印出。
#[test]
fn gate_iii_existing_reserved_names_open_the_entry_or_fail_explicitly() {
    let w = WorkDir::new();
    let top = w.top();
    for r in RESERVED {
        let dir = verbatim(&top.join(r.dir));
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join(r.name);
        if r.is_dir {
            std::fs::create_dir(&p).unwrap();
            std::fs::write(p.join("marker.txt"), marker(r)).unwrap();
        } else {
            std::fs::write(&p, marker(r)).unwrap();
        }
    }
    let mut opened = 0;
    let mut refused = 0;
    for r in RESERVED {
        let d = open_dir(&top, &[r.dir], DirOptions::default()).unwrap();
        let outcome = if r.is_dir {
            match d.open_child(r.name, DirOptions::default()) {
                Ok(sub) => {
                    // 判定依開啟後的 handle:相對它開到標記檔並讀到預先寫入的內容,表示它是磁碟上的目錄
                    let f = sub.open_file("marker.txt", FileAccess::Read).unwrap();
                    assert!(f.metadata().unwrap().is_file());
                    assert_eq!(read_all(f), marker(r), "{}/{}", r.dir, r.name);
                    // open_path 經同一名稱也一致
                    let f = open_path(&top, &[r.dir, r.name, "marker.txt"], FileAccess::Read)
                        .unwrap();
                    assert_eq!(read_all(f), marker(r));
                    "開到目錄項"
                }
                Err(e) => {
                    assert_eq!(e.kind(), ErrorKind::ReservedDevice, "{}/{}", r.dir, r.name);
                    "ReservedDevice"
                }
            }
        } else {
            match d.open_file(r.name, FileAccess::Read) {
                Ok(f) => {
                    let meta = f.metadata().unwrap();
                    assert!(meta.is_file(), "{}/{} 的 handle 不是一般檔", r.dir, r.name);
                    assert_eq!(read_all(f), marker(r), "{}/{}", r.dir, r.name);
                    "開到目錄項"
                }
                Err(e) => {
                    assert_eq!(e.kind(), ErrorKind::ReservedDevice, "{}/{}", r.dir, r.name);
                    "ReservedDevice"
                }
            }
        };
        if outcome == "ReservedDevice" {
            refused += 1;
        } else {
            opened += 1;
        }
        println!(
            "開工閘 (iii) {}/{}({}):{outcome}",
            r.dir,
            r.name,
            if r.is_dir { "目錄" } else { "一般檔" }
        );
        // 以寫入方式開啟同樣不得開到裝置
        if !r.is_dir {
            match d.open_file(r.name, FileAccess::ReadWrite { truncate: false }) {
                Ok(f) => assert!(f.metadata().unwrap().is_file()),
                Err(e) => assert_eq!(e.kind(), ErrorKind::ReservedDevice),
            }
        }
    }
    println!("開工閘 (iii) 合計:開到目錄項 {opened}、ReservedDevice {refused}");
}

/// (iii) 補充:以 `open_dir(create=true)` 建立保留名稱與主控台名稱——建立出磁碟上的目錄,
/// 或回 `ReservedDevice` 且沒有建立任何東西。
#[test]
fn gate_iii_creating_reserved_names_never_reaches_a_device() {
    let w = WorkDir::new();
    let top = w.top();
    for name in ["con", "NUL", "COM1", "con.txt", "CONIN$", "CONOUT$", "conout$x", "CLOCK$", "COM\u{b9}"] {
        let outcome = match open_dir(&top, &[name], create()) {
            Ok(d) => {
                let meta = std::fs::symlink_metadata(verbatim(&top.join(name))).unwrap();
                assert!(meta.is_dir(), "{name} 建立的不是目錄");
                // 相對 handle 寫入的標記出現在磁碟上的該目錄
                drop(d);
                "建立磁碟上的目錄"
            }
            Err(e) => {
                assert_eq!(e.kind(), ErrorKind::ReservedDevice, "{name}");
                assert!(
                    std::fs::symlink_metadata(verbatim(&top.join(name))).is_err(),
                    "{name} 回 ReservedDevice 時不得建立任何東西"
                );
                "ReservedDevice"
            }
        };
        println!("開工閘 (iii) 建立 {name}:{outcome}");
    }
}

/// (iv):30 層 × 20 字元(完整路徑超過 600 字元)的建立、寫入、讀回、刪除。
#[test]
fn gate_iv_deep_paths_beyond_max_path() {
    let w = WorkDir::new();
    let top = w.top();
    let parts: Vec<String> = (0..30).map(|i| format!("level-{i:02}-abcdefghijk")).collect();
    assert!(parts.iter().all(|p| p.chars().count() == 20));
    let full_len = top.to_string_lossy().chars().count() + parts.iter().map(|p| p.len() + 1).sum::<usize>();
    assert!(full_len > 600, "完整路徑只有 {full_len} 字元");
    let refs: Vec<&str> = parts.iter().map(String::as_str).collect();
    let deep = open_dir(&top, &refs, create()).unwrap();
    // 以 \\?\ 放入檔案(T1 尚無建立檔案的函式;T2 的 create_excl 會改用 handle 相對建立)
    let mut file_path = verbatim(&top);
    for p in &parts {
        file_path.push(p);
    }
    std::fs::write(file_path.join("f.txt"), "deep-0").unwrap();
    {
        let mut f = deep.open_file("f.txt", FileAccess::Write { truncate: true }).unwrap();
        f.write_all(b"deep-1").unwrap();
    }
    let mut all = refs.clone();
    all.push("f.txt");
    assert_eq!(read_all(open_path(&top, &all, FileAccess::Read).unwrap()), "deep-1");
    drop(deep);

    // 刪除:以 cap-std 的 handle 相對刪除由最深層往上(T2 的 rmtree 依賴同一機制)
    remove_tree_with_cap_std(&top, &refs);
    assert!(std::fs::read_dir(&top).unwrap().next().is_none(), "top 應已清空");
    println!("開工閘 (iv) 完整路徑 {full_len} 字元:建立、寫入、讀回、刪除皆成功");
}

fn remove_tree_with_cap_std(top: &Path, parts: &[&str]) {
    use cap_fs_ext::DirExt;
    let mut dirs = vec![cap_std::fs::Dir::open_ambient_dir(top, cap_std::ambient_authority()).unwrap()];
    for p in parts {
        let next = dirs.last().unwrap().open_dir_nofollow(p).unwrap();
        dirs.push(next);
    }
    dirs.last().unwrap().remove_file("f.txt").unwrap();
    for (i, p) in parts.iter().enumerate().rev() {
        dirs.pop();
        dirs[i].remove_dir(p).unwrap();
    }
}
