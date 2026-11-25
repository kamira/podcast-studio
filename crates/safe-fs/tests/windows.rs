//! Windows 專屬測試(CHG-20261125-01;`windows-latest` 與 Windows 開發機執行)。
//!
//! T1 開工閘(代決 4)的查證項,逐項以「開工閘 (i)~(iv)」開頭印出結果:
//! - (i) 檔案符號連結、目錄符號連結、junction 在中間層與最後一層都被拒絕(`Unsafe`);
//! - (ii) cap-std 相對開啟的實際機制:取得目錄 handle 後把原路徑改名、在原處放 junction,
//!   之後的開啟仍落在原目錄(handle 相對,不以路徑字串重開);
//! - (iii) 既有的保留名稱項目(代決 9、C-1):開到真正的目錄項(以開啟後 handle 的型別與標記內容判定),
//!   或回 `ReservedDevice`,逐項印出;
//! - (iv) 總長超過 600 字元(30 層 × 20 字元)的深層路徑可建立、開啟與刪除(H-4)。
//!
//! T2 另加(逐項以「T2」開頭印出實測結果):
//! - K-1 / D-12:每個變更類函式都在呼叫 cap-std **之前**拒絕結尾句點、結尾空白、`...` 與保留裝置名,
//!   磁碟完全不變(若名稱交給 cap-std,完整路徑經 Win32 轉換會改到別的項目);
//! - K-2:持有子目錄 handle 時改名上層目錄的結果;`rmtree` 刪除子目錄前先關閉它的 handle;
//! - C-2:取得目錄 handle 後原路徑改名並換成 junction 的實際結果,之後的建立與刪除仍落在原目錄;
//! - C-4 / D-3:硬連結的來源是 junction 時回 `Unsafe`;
//! - D-7 / D-9:變更類錯誤類別與開啟中 handle 對改名的影響;junction 對應 fixture 的符號連結案例。
#![cfg(windows)]

mod common;

use std::io::{Read, Write};
use std::path::Path;

use common::{LinkKind, WorkDir, junction, snapshot, symlink, verbatim};
use safe_fs::{DirOptions, EntryKind, ErrorKind, FileAccess, open_dir, open_path};

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
        && symlink(
            &top.join("a").join("inner"),
            &top.join("a").join("sin"),
            LinkKind::Dir,
        )
        && symlink(
            &w.outside().join("marker.txt"),
            &top.join("sfile"),
            LinkKind::File,
        )
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

/// (ii):cap-std 在 Windows 的實際機制。
///
/// - 開啟:以 `\\?\` 預先建立 `f.txt.`(結尾句點)與 `f.txt` 兩個不同的檔案、`d.` 與 `d` 兩個不同的目錄;
///   cap-std 以名稱 `f.txt.` / `d.` 相對目錄 handle 開啟時讀到的是**結尾帶句點的那一個**——
///   表示開啟走 `NtCreateFile` 相對目錄 handle,不經 Win32 路徑轉換(Win32 會去掉結尾句點)。
/// - 變更類:cap-std 的 `create_dir("g.")` 在磁碟上建立的是 `g`(結尾句點被去掉)——表示建立目錄是
///   由 handle 取回完整路徑、串接名稱後經 Win32 路徑轉換(cap-primitives `windows/fs/get_path.rs`
///   的 `concatenate`);`safe-fs` 在 Windows 拒絕結尾句點與空白等名稱(代決 8)即為此故。
/// - 目錄 handle 開啟中時把原路徑改名:實測結果印出;若改名成功,已開啟的 handle 仍須指向原目錄,
///   且經 `top` 重新開啟遇到換上的 junction 為 `Unsafe`。
#[test]
fn gate_ii_open_is_relative_to_the_directory_handle() {
    use cap_fs_ext::DirExt;
    let w = WorkDir::new();
    let top = w.top();
    let vtop = verbatim(&top);
    std::fs::write(vtop.join("f.txt."), "with-dot").unwrap();
    std::fs::write(vtop.join("f.txt"), "without-dot").unwrap();
    std::fs::create_dir(vtop.join("d.")).unwrap();
    std::fs::write(vtop.join("d.").join("m"), "dir-with-dot").unwrap();
    std::fs::create_dir(vtop.join("d")).unwrap();
    std::fs::write(vtop.join("d").join("m"), "dir-without-dot").unwrap();
    let cap = cap_std::fs::Dir::open_ambient_dir(&top, cap_std::ambient_authority()).unwrap();
    assert_eq!(
        cap.read_to_string("f.txt.").unwrap(),
        "with-dot",
        "檔案開啟經過 Win32 路徑轉換"
    );
    let d = cap.open_dir_nofollow("d.").unwrap();
    assert_eq!(
        d.read_to_string("m").unwrap(),
        "dir-with-dot",
        "目錄開啟經過 Win32 路徑轉換"
    );
    println!(
        "開工閘 (ii) 開啟:相對目錄 handle(NtCreateFile),名稱不經 Win32 路徑轉換(結尾句點保留)"
    );

    let created = match cap.create_dir("g.") {
        Ok(()) => {
            let dot = std::fs::symlink_metadata(vtop.join("g.")).is_ok();
            let plain = std::fs::symlink_metadata(vtop.join("g")).is_ok();
            assert!(dot ^ plain, "應恰好建立一個目錄");
            if plain {
                "建立出 g(結尾句點被去掉:完整路徑串接後經 Win32 轉換)"
            } else {
                "建立出 g.(未經 Win32 轉換)"
            }
        }
        Err(e) => panic!("create_dir(\"g.\") 失敗:{e}"),
    };
    println!("開工閘 (ii) 變更類 create_dir:{created}");

    std::fs::create_dir_all(top.join("a").join("sub")).unwrap();
    std::fs::write(top.join("a").join("f"), "original").unwrap();
    std::fs::write(top.join("a").join("sub").join("g"), "original-sub").unwrap();
    let sd = open_dir(&top, &["a"], DirOptions::default()).unwrap();
    match std::fs::rename(top.join("a"), top.join("moved")) {
        Ok(()) => {
            junction(&top.join("a"), &w.outside());
            std::fs::write(w.outside().join("f"), "outside-f").unwrap();
            assert_eq!(
                read_all(sd.open_file("f", FileAccess::Read).unwrap()),
                "original"
            );
            let sub = sd.open_child("sub", DirOptions::default()).unwrap();
            assert_eq!(
                read_all(sub.open_file("g", FileAccess::Read).unwrap()),
                "original-sub"
            );
            assert_eq!(
                open_dir(&top, &["a"], DirOptions::default())
                    .unwrap_err()
                    .kind(),
                ErrorKind::Unsafe
            );
            println!("開工閘 (ii) 原路徑改名並換成 junction 後,handle 相對開啟仍讀到原目錄內容");
        }
        Err(e) => {
            // ERROR_SHARING_VIOLATION:cap-std 開啟目錄時未給刪除共用,handle 開著時原路徑不能被改名或換掉
            assert_eq!(e.raw_os_error(), Some(32), "改名失敗的原因不是共用違規:{e}");
            assert_eq!(
                read_all(sd.open_file("f", FileAccess::Read).unwrap()),
                "original"
            );
            println!(
                "開工閘 (ii) 目錄 handle 開啟中時原路徑無法改名(錯誤碼 32,共用違規),原路徑無法被換成 junction"
            );
        }
    }
}

/// (iii) 的一個項目:以 `\\?\` 預先建立的保留名稱目錄項。
struct Reserved {
    /// 所在子目錄(大小寫變形各放一個子目錄,避免 NTFS 不分大小寫而同名)
    dir: &'static str,
    name: &'static str,
    is_dir: bool,
}

const RESERVED: &[Reserved] = &[
    Reserved {
        dir: "r1",
        name: "con",
        is_dir: true,
    },
    Reserved {
        dir: "r1",
        name: "nul",
        is_dir: false,
    },
    Reserved {
        dir: "r1",
        name: "COM1",
        is_dir: true,
    },
    Reserved {
        dir: "r1",
        name: "con.txt",
        is_dir: false,
    },
    Reserved {
        dir: "r1",
        name: "CONIN$",
        is_dir: false,
    },
    Reserved {
        dir: "r1",
        name: "CONOUT$",
        is_dir: true,
    },
    Reserved {
        dir: "r1",
        name: "COM\u{b9}",
        is_dir: true,
    },
    Reserved {
        dir: "r1",
        name: "LPT1",
        is_dir: false,
    },
    Reserved {
        dir: "r1",
        name: "AUX",
        is_dir: true,
    },
    Reserved {
        dir: "r1",
        name: "PRN.log",
        is_dir: false,
    },
    Reserved {
        dir: "r2",
        name: "Con",
        is_dir: true,
    },
    Reserved {
        dir: "r2",
        name: "NuL",
        is_dir: false,
    },
    Reserved {
        dir: "r2",
        name: "cOm1",
        is_dir: true,
    },
    Reserved {
        dir: "r2",
        name: "CON.TXT",
        is_dir: false,
    },
    Reserved {
        dir: "r2",
        name: "conin$",
        is_dir: false,
    },
    Reserved {
        dir: "r2",
        name: "Conout$",
        is_dir: true,
    },
    Reserved {
        dir: "r2",
        name: "com\u{b9}",
        is_dir: true,
    },
    Reserved {
        dir: "r3",
        name: "CON",
        is_dir: false,
    },
    Reserved {
        dir: "r3",
        name: "Nul.Txt",
        is_dir: true,
    },
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
                    let f =
                        open_path(&top, &[r.dir, r.name, "marker.txt"], FileAccess::Read).unwrap();
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
    for name in [
        "con",
        "NUL",
        "COM1",
        "con.txt",
        "CONIN$",
        "CONOUT$",
        "conout$x",
        "CLOCK$",
        "COM\u{b9}",
    ] {
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
    let parts: Vec<String> = (0..30)
        .map(|i| format!("level-{i:02}-abcdefghijk"))
        .collect();
    assert!(parts.iter().all(|p| p.chars().count() == 20));
    let full_len =
        top.to_string_lossy().chars().count() + parts.iter().map(|p| p.len() + 1).sum::<usize>();
    assert!(full_len > 600, "完整路徑只有 {full_len} 字元");
    let refs: Vec<&str> = parts.iter().map(String::as_str).collect();
    let deep = open_dir(&top, &refs, create()).unwrap();
    // 以 \\?\ 放入檔案(開工閘當時 T1 尚無建立檔案的函式;T2 的 create_excl 見 mutate.rs)
    let mut file_path = verbatim(&top);
    for p in &parts {
        file_path.push(p);
    }
    std::fs::write(file_path.join("f.txt"), "deep-0").unwrap();
    {
        let mut f = deep
            .open_file("f.txt", FileAccess::Write { truncate: true })
            .unwrap();
        f.write_all(b"deep-1").unwrap();
    }
    let mut all = refs.clone();
    all.push("f.txt");
    assert_eq!(
        read_all(open_path(&top, &all, FileAccess::Read).unwrap()),
        "deep-1"
    );
    drop(deep);

    // 刪除:以 cap-std 的 remove_file / remove_dir 由最深層往上。兩者在 Windows **不是** handle 相對刪除——
    // 由目錄 handle 取回完整路徑、串接名稱後經 Win32(開工閘 (ii)、D-12);完整路徑超過 MAX_PATH 仍成功是因為
    // 標準函式庫對長路徑自動加 \\?\(上限約 32767 字元,K-3)。T2 的 rmtree 同此機制(mutate.rs 1500 層)
    remove_tree_with_cap_std(&top, &refs);
    assert!(
        std::fs::read_dir(&top).unwrap().next().is_none(),
        "top 應已清空"
    );
    println!("開工閘 (iv) 完整路徑 {full_len} 字元:建立、寫入、讀回、刪除皆成功");
}

fn remove_tree_with_cap_std(top: &Path, parts: &[&str]) {
    use cap_fs_ext::DirExt;
    let mut dirs =
        vec![cap_std::fs::Dir::open_ambient_dir(top, cap_std::ambient_authority()).unwrap()];
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

// ---------------------------------------------------------------------------------------------
// T2(CHG-20261125-01):變更類函式在 Windows 的名稱安全、handle 與連結
// ---------------------------------------------------------------------------------------------

const W: FileAccess = FileAccess::Write { truncate: false };
const RW: FileAccess = FileAccess::ReadWrite { truncate: false };

/// 以 `\\?\` 讀的快照(名稱結尾有句點等項目也照實列出)。
fn vsnap(p: &Path) -> Vec<(String, &'static str, String)> {
    snapshot(&verbatim(p))
}

/// K-1 / D-12:結尾句點、結尾空白、`...`、保留裝置名在每個變更類函式都於呼叫 cap-std 之前被拒絕。
///
/// 磁碟上先放 `x`(一般檔)與 `dd`(目錄,內含 `f`):若名稱 `x.`、`x ` 交給 cap-std,變更類操作會以完整路徑
/// 經 Win32 轉換去掉結尾而改到 `x`;所以「全部回 `InvalidName` / `ReservedDevice` 且快照完全不變」即表示
/// 檢查發生在呼叫 cap-std 之前。另以 `\\?\` 建立內含 `y.` 的目錄 `holder`,`entries` 與 `rmtree` 遇到它時
/// 以 `InvalidName` 失敗、什麼都不刪。
#[test]
fn k1_mutating_functions_reject_ambiguous_names_before_cap_std() {
    let w = WorkDir::new();
    let top = w.top();
    std::fs::write(top.join("x"), "victim").unwrap();
    std::fs::create_dir(top.join("dd")).unwrap();
    std::fs::write(top.join("dd").join("f"), "in-dd").unwrap();
    let holder = verbatim(&top.join("holder"));
    std::fs::create_dir(&holder).unwrap();
    std::fs::write(holder.join("y."), "dotted").unwrap();
    let before = vsnap(&top);
    let outside = snapshot(&w.outside());

    let invalid = ["x.", "x ", "x. ", "dd.", "dd ", "...", "x..."];
    let reserved = [
        "con",
        "NUL",
        "com1.txt",
        "LPT1",
        "aux .log",
        "CON.TXT",
        "COM\u{b9}",
    ];
    let want = |n: &str| {
        if reserved.contains(&n) {
            ErrorKind::ReservedDevice
        } else {
            ErrorKind::InvalidName
        }
    };
    let d = open_dir(&top, NONE, DirOptions::default()).unwrap();
    let mut checked = 0;
    for name in invalid.iter().chain(reserved.iter()).copied() {
        let k = want(name);
        let mut results: Vec<(&str, ErrorKind)> = vec![
            (
                "open_dir(create)",
                open_dir(&top, &[name], create()).unwrap_err().kind(),
            ),
            (
                "open_child(create)",
                d.open_child(name, create()).unwrap_err().kind(),
            ),
            (
                "create_excl",
                d.create_excl(name, W, 0o600).unwrap_err().kind(),
            ),
            (
                "open_or_create",
                d.open_or_create(name, RW, 0o600).unwrap_err().kind(),
            ),
            ("unlink", d.unlink(name, false).unwrap_err().kind()),
            (
                "unlink(missing_ok)",
                d.unlink(name, true).unwrap_err().kind(),
            ),
            ("rmtree", d.rmtree(name, false).unwrap_err().kind()),
            (
                "rmtree(missing_ok)",
                d.rmtree(name, true).unwrap_err().kind(),
            ),
            (
                "rename(src)",
                d.rename(name, &d, "renamed").unwrap_err().kind(),
            ),
            ("rename(dst)", d.rename("x", &d, name).unwrap_err().kind()),
            (
                "replace(src)",
                d.replace(name, &d, "renamed").unwrap_err().kind(),
            ),
            ("replace(dst)", d.replace("x", &d, name).unwrap_err().kind()),
            (
                "hard_link(src)",
                d.hard_link(name, &d, "hl").unwrap_err().kind(),
            ),
            (
                "hard_link(dst)",
                d.hard_link("x", &d, name).unwrap_err().kind(),
            ),
            ("lstat", d.lstat(name).unwrap_err().kind()),
        ];
        for (op, got) in results.drain(..) {
            assert_eq!(got, k, "{op}({name:?})");
            checked += 1;
        }
        assert_eq!(vsnap(&top), before, "{name:?} 之後磁碟有變動");
    }
    let h = open_dir(&top, &["holder"], DirOptions::default()).unwrap();
    assert_eq!(h.entries().unwrap_err().kind(), ErrorKind::InvalidName);
    drop(h);
    assert_eq!(
        d.rmtree("holder", false).unwrap_err().kind(),
        ErrorKind::InvalidName
    );
    assert_eq!(vsnap(&top), before, "rmtree(holder) 之後磁碟有變動");
    assert_eq!(snapshot(&w.outside()), outside);
    println!(
        "T2 K-1:{} 個名稱 × 15 個函式共 {checked} 次皆在呼叫 cap-std 前回 InvalidName / ReservedDevice,磁碟不變;\
         entries 與 rmtree 遇到 \\\\?\\ 建立的 `y.` 回 InvalidName、未刪任何項目",
        invalid.len() + reserved.len()
    );
}

/// K-2:持有子目錄 handle 時改名上層目錄的實際結果(印出);`rmtree` 自己開的 handle 一定先關閉再刪除,
/// 而外部持有的子目錄 handle 會使該目錄無法刪除(共用違規)。
#[test]
fn k2_ancestor_rename_while_child_handle_is_open() {
    let w = WorkDir::new();
    let top = w.top();
    std::fs::create_dir_all(top.join("p").join("c")).unwrap();
    std::fs::write(top.join("p").join("c").join("f"), "child").unwrap();
    let child = open_dir(&top, &["p", "c"], DirOptions::default()).unwrap();
    match std::fs::rename(top.join("p"), top.join("p2")) {
        Ok(()) => {
            assert_eq!(
                read_all(child.open_file("f", FileAccess::Read).unwrap()),
                "child"
            );
            drop(child.create_excl("g", W, 0o600).unwrap());
            assert!(top.join("p2").join("c").join("g").is_file());
            println!(
                "T2 K-2 子目錄 handle 開著時改名上層目錄:成功(不是共用違規);handle 仍指向原目錄,建立落在改名後的位置"
            );
            std::fs::rename(top.join("p2"), top.join("p")).unwrap();
        }
        Err(e) => {
            let code = e.raw_os_error();
            assert!(matches!(code, Some(5 | 32)), "改名上層失敗的原因:{e}");
            println!("T2 K-2 子目錄 handle 開著時改名上層目錄:失敗(錯誤碼 {code:?})");
        }
    }
    // 外部持有子目錄 handle 時 rmtree 上層:刪不掉該子目錄
    let d = open_dir(&top, NONE, DirOptions::default()).unwrap();
    let e = d.rmtree("p", false).unwrap_err();
    println!(
        "T2 K-2 外部持有子目錄 handle 時 rmtree 上層:失敗 {:?}(錯誤碼 {:?})",
        e.kind(),
        e.raw_os_error()
    );
    assert!(top.join("p").join("c").is_dir());
    drop(child);
    // 沒有外部 handle:rmtree 自己的 handle 先關閉再刪除,成功
    d.rmtree("p", false).unwrap();
    assert!(!top.join("p").exists());
    println!("T2 K-2 沒有外部 handle 時 rmtree:成功(子目錄 handle 先關閉再刪除)");
}

/// C-2(代決 10):取得目錄 handle 後把原路徑改名並在原處放 junction。實測:改名以共用違規(錯誤碼 32)
/// 失敗,原路徑無法被換掉;若改名成功,之後的 `create_excl` 與 `unlink` 仍須落在原目錄、不碰 junction 目標。
#[test]
fn dir_handle_survives_swap_to_junction() {
    let w = WorkDir::new();
    let top = w.top();
    let b = top.join("a").join("b");
    std::fs::create_dir_all(&b).unwrap();
    std::fs::write(b.join("f.txt"), "f").unwrap();
    std::fs::write(w.outside().join("f.txt"), "outside-f").unwrap();
    let before = snapshot(&w.outside());
    let d = open_dir(&top, &["a", "b"], DirOptions::default()).unwrap();
    let moved = top.join("a").join("b-moved");
    let outcome = match std::fs::rename(&b, &moved) {
        Ok(()) => {
            junction(&b, &w.outside());
            drop(d.create_excl("new.txt", W, 0o600).unwrap());
            d.unlink("f.txt", false).unwrap();
            assert!(moved.join("new.txt").is_file());
            assert!(!moved.join("f.txt").exists());
            "改名成功並換成 junction;之後的建立與刪除落在原目錄"
        }
        Err(e) => {
            assert_eq!(e.raw_os_error(), Some(32), "改名失敗的原因不是共用違規:{e}");
            drop(d.create_excl("new.txt", W, 0o600).unwrap());
            d.unlink("f.txt", false).unwrap();
            assert!(b.join("new.txt").is_file());
            assert!(!b.join("f.txt").exists());
            "改名失敗(錯誤碼 32,共用違規),原路徑無法換成 junction;之後的建立與刪除落在原目錄"
        }
    };
    assert_eq!(snapshot(&w.outside()), before, "outside/ 不得有任何變動");
    println!("T2 C-2:{outcome}");
}

/// junction 對應 fixture 中的符號連結案例(本機無符號連結權限時的 Windows 證據;D-3)。
#[test]
fn junctions_in_mutating_functions_behave_like_symlinks_on_linux() {
    let w = WorkDir::new();
    let top = w.top();
    std::fs::write(top.join("top.txt"), "top").unwrap();
    std::fs::create_dir_all(top.join("a").join("b")).unwrap();
    std::fs::write(top.join("a").join("b").join("f.txt"), "inside").unwrap();
    junction(&top.join("out"), &w.outside().join("d"));
    junction(&top.join("a").join("in"), &top.join("a").join("b"));
    junction(&top.join("dang"), &w.root().join("missing"));
    let before = snapshot(&w.outside());
    let d = open_dir(&top, NONE, DirOptions::default()).unwrap();
    let a = open_dir(&top, &["a"], DirOptions::default()).unwrap();

    for n in ["out", "dang"] {
        assert_eq!(d.lstat(n).unwrap().unwrap().kind, EntryKind::Symlink, "{n}");
        assert_eq!(
            d.create_excl(n, W, 0o600).unwrap_err().kind(),
            ErrorKind::AlreadyExists,
            "create_excl {n}"
        );
        assert_eq!(
            d.open_or_create(n, RW, 0o600).unwrap_err().kind(),
            ErrorKind::Unsafe,
            "open_or_create {n}"
        );
        assert_eq!(
            d.hard_link(n, &a, "hl").unwrap_err().kind(),
            ErrorKind::Unsafe,
            "hard_link {n}(C-4)"
        );
    }
    assert!(a.lstat("hl").unwrap().is_none());
    let kinds: Vec<(String, EntryKind)> = d
        .entries()
        .unwrap()
        .into_iter()
        .map(|(n, m)| (n.to_string_lossy().to_string(), m.kind))
        .collect();
    assert_eq!(
        kinds,
        vec![
            ("a".to_string(), EntryKind::Dir),
            ("dang".to_string(), EntryKind::Symlink),
            ("out".to_string(), EntryKind::Symlink),
            ("top.txt".to_string(), EntryKind::File),
        ]
    );
    // 改名 junction:只改名連結本身
    d.rename("out", &a, "out2").unwrap();
    assert_eq!(a.lstat("out2").unwrap().unwrap().kind, EntryKind::Symlink);
    // 一般檔改名到 junction 上:實測結果印出(Linux:取代連結本身)
    let over = match d.rename("top.txt", &a, "out2") {
        Ok(()) => {
            let k = a.lstat("out2").unwrap().unwrap().kind;
            format!("成功,目標變成 {k:?}")
        }
        Err(e) => format!("失敗 {:?}(錯誤碼 {:?})", e.kind(), e.raw_os_error()),
    };
    println!("T2 D-9 一般檔改名到既有 junction(目錄連結)上:{over}");
    // unlink / rmtree junction:只刪連結本身
    if a.lstat("out2")
        .unwrap()
        .is_some_and(|m| m.kind == EntryKind::Symlink)
    {
        a.unlink("out2", false).unwrap();
    }
    d.rmtree("dang", false).unwrap();
    a.unlink("in", false).unwrap();
    assert!(top.join("a").join("b").join("f.txt").is_file());
    // rmtree 含 junction 的目錄樹:連結目標不變
    std::fs::create_dir_all(top.join("t").join("sub")).unwrap();
    std::fs::write(top.join("t").join("sub").join("x"), "x").unwrap();
    junction(&top.join("t").join("out"), &w.outside());
    junction(&top.join("t").join("sub").join("in"), &top.join("a"));
    d.rmtree("t", false).unwrap();
    assert!(!top.join("t").exists());
    assert!(top.join("a").join("b").join("f.txt").is_file());
    assert_eq!(snapshot(&w.outside()), before, "outside/ 不得有任何變動");
    println!(
        "T2 D-3:junction 的 lstat / entries 為 Symlink、create_excl 為 AlreadyExists、open_or_create 與 hard_link 來源為 Unsafe、unlink / rmtree 只刪連結本身"
    );
}

/// D-7:變更類的錯誤類別在 Windows 改回與 Linux 相同(實測印出原始錯誤碼)。
#[test]
fn d7_error_kinds_match_linux() {
    let w = WorkDir::new();
    let top = w.top();
    std::fs::create_dir_all(top.join("a").join("b")).unwrap();
    std::fs::write(top.join("a").join("b").join("k"), "k").unwrap();
    std::fs::write(top.join("top.txt"), "top").unwrap();
    std::fs::create_dir(top.join("e")).unwrap();
    let d = open_dir(&top, NONE, DirOptions::default()).unwrap();
    let a = open_dir(&top, &["a"], DirOptions::default()).unwrap();
    // 注意:a/b 的目錄 handle 開著時,對 b 的 unlink 會先遇到共用違規(錯誤碼 32),所以最後才開
    let rows: Vec<(&str, safe_fs::Error, ErrorKind)> = vec![
        (
            "unlink(目錄)",
            a.unlink("b", false).unwrap_err(),
            ErrorKind::IsADirectory,
        ),
        (
            "create_excl(既有目錄)",
            a.create_excl("b", W, 0o600).unwrap_err(),
            ErrorKind::AlreadyExists,
        ),
        (
            "open_or_create(既有目錄)",
            a.open_or_create("b", RW, 0o600).unwrap_err(),
            ErrorKind::IsADirectory,
        ),
        (
            "rename(一般檔→既有目錄)",
            d.rename("top.txt", &a, "b").unwrap_err(),
            ErrorKind::IsADirectory,
        ),
        (
            "rename(目錄→既有一般檔)",
            a.rename("b", &d, "top.txt").unwrap_err(),
            ErrorKind::NotADirectory,
        ),
        (
            "hard_link(來源是目錄)",
            a.hard_link("b", &d, "x").unwrap_err(),
            ErrorKind::PermissionDenied,
        ),
        (
            "hard_link(目標已存在)",
            d.hard_link("top.txt", &a, "b").unwrap_err(),
            ErrorKind::AlreadyExists,
        ),
    ];
    for (what, e, want) in rows {
        println!(
            "T2 D-7 {what}:{:?}(原始錯誤碼 {:?})",
            e.kind(),
            e.raw_os_error()
        );
        assert_eq!(e.kind(), want, "{what}");
    }
    // 目錄改名到自己的子目錄內:只開 a/b 的 handle(a 本身的 handle 開著時會先遇到共用違規)
    drop(a);
    let ab = open_dir(&top, &["a", "b"], DirOptions::default()).unwrap();
    let e = d.rename("a", &ab, "x").unwrap_err();
    println!(
        "T2 D-7 rename(目錄→自己的子目錄):{:?}(原始錯誤碼 {:?};Linux 為 EINVAL,列入 windows 欄)",
        e.kind(),
        e.raw_os_error()
    );
    assert_eq!(e.kind(), ErrorKind::PermissionDenied);
    drop(ab);
    let a = open_dir(&top, &["a"], DirOptions::default()).unwrap();
    // 目錄改名到非空目錄:Windows 145(ERROR_DIR_NOT_EMPTY)名稱與 Linux 相同為 ENOTEMPTY
    std::fs::create_dir(top.join("ne")).unwrap();
    std::fs::write(top.join("ne").join("z"), "z").unwrap();
    let e = a.rename("b", &d, "ne").unwrap_err();
    println!(
        "T2 D-7 rename(目錄→非空目錄):{:?},名稱 {}",
        e.kind(),
        e.os_error_name()
    );
    assert_eq!(e.os_error_name(), "ENOTEMPTY");
    // 目錄改名到空目錄:與 Linux 相同,取代空目錄
    a.rename("b", &d, "e").unwrap();
    assert!(top.join("e").join("k").is_file());
    println!("T2 D-9 rename(目錄→既有空目錄):成功(與 Linux 相同,取代空目錄)");
}

/// D-9:改名的目標或來源有開啟中的檔案 handle 時的實際結果(印出)。
#[test]
fn d9_rename_with_open_handles() {
    let w = WorkDir::new();
    let top = w.top();
    let d = open_dir(&top, NONE, DirOptions::default()).unwrap();
    let report = |what: &str, r: safe_fs::Result<()>| match r {
        Ok(()) => println!("T2 D-9 {what}:成功"),
        Err(e) => println!(
            "T2 D-9 {what}:失敗 {:?}(錯誤碼 {:?})",
            e.kind(),
            e.raw_os_error()
        ),
    };
    std::fs::write(top.join("src"), "new").unwrap();
    std::fs::write(top.join("dst"), "old").unwrap();
    // 目標開啟中(safe-fs 開的檔案 handle)
    let held = d.open_file("dst", FileAccess::Read).unwrap();
    let r = d.replace("src", &d, "dst");
    let ok_dst = r.is_ok();
    report("取代既有目標(目標的檔案 handle 開啟中)", r);
    drop(held);
    if ok_dst {
        assert_eq!(std::fs::read_to_string(top.join("dst")).unwrap(), "new");
    } else {
        d.replace("src", &d, "dst").unwrap();
    }
    // 來源開啟中
    std::fs::write(top.join("src2"), "new2").unwrap();
    let held = d.open_file("src2", FileAccess::Read).unwrap();
    let r = d.rename("src2", &d, "dst");
    report("改名來源(來源的檔案 handle 開啟中)", r);
    drop(held);
    // 以寫入方式開啟中的目標
    std::fs::write(top.join("src3"), "new3").unwrap();
    let held = d.open_file("dst", RW).unwrap();
    let r = d.rename("src3", &d, "dst");
    report("取代既有目標(目標以讀寫開啟中)", r);
    drop(held);
}
