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
//!
//! T3 另加(逐項以「T3」開頭印出實測結果;T2 印出的 D-9 三項、一般檔改名到 junction、K-2 的錯誤類別
//! 改以斷言鎖住,T3-2):
//! - H-1 / D-5:以 `\?\` 預先建立的 `con` 目錄、`nul` 一般檔、`COM1` 目錄,在 `open_dir`、`open_file`、
//!   `lstat`、`entries`、`rmtree` 逐項印出「開到目錄項」或 `ReservedDevice`;`create_excl` 不開到裝置;
//!   `rmtree` 中途失敗時已刪的項目不還原;
//! - C-1:`CONIN$`、`CONOUT$`、`CLOCK$` 在建立、改名、硬連結、刪除各函式都是磁碟上的目錄項;
//! - D-6:只差大小寫的名稱開到同一項目;D-10:開著 handle 就刪除或搬移;D-11:8.3 短檔名別名;
//! - W-6:NTFS 大小寫對照與 `fold_name` 的定點比較(CI 與本機都跑)與全碼位(BMP)查證(`#[ignore]`)。
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

/// K-2:持有子目錄 handle 時改名上層目錄的實際結果(T3 以斷言鎖住:上層改名錯誤碼 5;外部持有子目錄 handle 時
/// `rmtree` 上層為 `Other`、原始錯誤碼 32);`rmtree` 自己開的 handle 一定先關閉再刪除,
/// 而外部持有的子目錄 handle 會使該目錄無法刪除(共用違規)。
#[test]
fn k2_ancestor_rename_while_child_handle_is_open() {
    let w = WorkDir::new();
    let top = w.top();
    std::fs::create_dir_all(top.join("p").join("c")).unwrap();
    std::fs::write(top.join("p").join("c").join("f"), "child").unwrap();
    let child = open_dir(&top, &["p", "c"], DirOptions::default()).unwrap();
    // T3(T3-2)鎖住實測結果:子目錄 handle 開著時改名上層目錄失敗,錯誤碼 5(拒絕存取)
    let e = std::fs::rename(top.join("p"), top.join("p2")).unwrap_err();
    assert_eq!(e.raw_os_error(), Some(5), "改名上層失敗的原因:{e}");
    println!("T2 K-2 子目錄 handle 開著時改名上層目錄:失敗(錯誤碼 5)");
    assert_eq!(
        read_all(child.open_file("f", FileAccess::Read).unwrap()),
        "child"
    );
    // 外部持有子目錄 handle 時 rmtree 上層:刪不掉該子目錄。T3(T3-2)鎖住:Other,原始錯誤碼 32(共用違規);
    // 子目錄內的 f 已先刪除、不還原(rmtree 中途失敗時已刪的項目不還原,與 Python 相同)
    let d = open_dir(&top, NONE, DirOptions::default()).unwrap();
    let e = d.rmtree("p", false).unwrap_err();
    println!(
        "T2 K-2 外部持有子目錄 handle 時 rmtree 上層:失敗 {:?}(錯誤碼 {:?})",
        e.kind(),
        e.raw_os_error()
    );
    assert_eq!(e.kind(), ErrorKind::Other(Some(32)));
    assert_eq!(e.raw_os_error(), Some(32));
    assert!(
        !top.join("p").join("c").join("f").exists(),
        "已刪的 f 不還原"
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
    // 一般檔改名到 junction 上(Linux:取代連結本身)。T3(T3-2)鎖住實測結果:成功,目標變成一般檔,
    // 連結目標(outside/d)不受影響
    d.rename("top.txt", &a, "out2").unwrap();
    let k = a.lstat("out2").unwrap().unwrap().kind;
    assert_eq!(k, EntryKind::File);
    assert_eq!(
        read_all(a.open_file("out2", FileAccess::Read).unwrap()),
        "top"
    );
    println!("T2 D-9 一般檔改名到既有 junction(目錄連結)上:成功,目標變成 {k:?}");
    a.unlink("out2", false).unwrap();
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

/// D-9:改名的目標或來源有開啟中的檔案 handle 時的實際結果(T3 以斷言鎖住:三項皆成功,開著的 handle 仍讀到
/// 改名前的內容)。
#[test]
fn d9_rename_with_open_handles() {
    let w = WorkDir::new();
    let top = w.top();
    let d = open_dir(&top, NONE, DirOptions::default()).unwrap();
    let report = |what: &str, r: &safe_fs::Result<()>| match r {
        Ok(()) => println!("T2 D-9 {what}:成功"),
        Err(e) => println!(
            "T2 D-9 {what}:失敗 {:?}(錯誤碼 {:?})",
            e.kind(),
            e.raw_os_error()
        ),
    };
    // T3(T3-2):三項實測結果皆為成功,以斷言鎖住(不是共用違規)
    std::fs::write(top.join("src"), "new").unwrap();
    std::fs::write(top.join("dst"), "old").unwrap();
    // 目標開啟中(safe-fs 開的檔案 handle)
    let held = d.open_file("dst", FileAccess::Read).unwrap();
    let r = d.replace("src", &d, "dst");
    report("取代既有目標(目標的檔案 handle 開啟中)", &r);
    r.unwrap();
    // 開著的 handle 仍讀到被取代前的內容
    assert_eq!(read_all(held), "old");
    assert_eq!(std::fs::read_to_string(top.join("dst")).unwrap(), "new");
    // 來源開啟中
    std::fs::write(top.join("src2"), "new2").unwrap();
    let held = d.open_file("src2", FileAccess::Read).unwrap();
    let r = d.rename("src2", &d, "dst");
    report("改名來源(來源的檔案 handle 開啟中)", &r);
    r.unwrap();
    assert_eq!(read_all(held), "new2");
    assert!(d.lstat("src2").unwrap().is_none());
    // 以寫入方式開啟中的目標
    std::fs::write(top.join("src3"), "new3").unwrap();
    let held = d.open_file("dst", RW).unwrap();
    let r = d.rename("src3", &d, "dst");
    report("取代既有目標(目標以讀寫開啟中)", &r);
    r.unwrap();
    drop(held);
    assert_eq!(std::fs::read_to_string(top.join("dst")).unwrap(), "new3");
}

// ---------------------------------------------------------------------------------------------
// T3(CHG-20261125-01):保留裝置名、主控台名稱、大小寫、開著 handle 的刪除與搬移、8.3 短檔名、W-6
// ---------------------------------------------------------------------------------------------

/// 結果印成「開到目錄項」或 `ReservedDevice`;其他結果(開到裝置、別的錯誤)判失敗。
fn h1_outcome<T>(what: &str, r: safe_fs::Result<T>, check: impl FnOnce(T)) -> &'static str {
    match r {
        Ok(v) => {
            check(v);
            "開到目錄項"
        }
        Err(e) => {
            assert_eq!(e.kind(), ErrorKind::ReservedDevice, "{what}");
            "ReservedDevice"
        }
    }
}

/// H-1 / 代決 9(列入 Acceptance operation):以 `\\?\` 預先建立 `con` 目錄(內含標記檔)、`nul` 一般檔、
/// `COM1` 目錄,再以 `open_dir`、`open_file`、`lstat`、`entries`、`rmtree` 操作;每一項斷言「開到該目錄項
/// (讀到標記)」或 `ReservedDevice` 兩者之一,逐項印出是哪一種;回 `ReservedDevice` 時磁碟不變。
/// 另斷言 `create_excl(d, "con")` 不開到裝置,以及 `rmtree` 中途失敗時已刪的項目不還原。
#[test]
fn h1_reserved_names_in_every_function() {
    let w = WorkDir::new();
    let top = w.top();
    let r = verbatim(&top.join("r"));
    std::fs::create_dir(&r).unwrap();
    let items: [(&str, bool); 3] = [("con", true), ("nul", false), ("COM1", true)];
    for (name, is_dir) in items {
        let p = r.join(name);
        if is_dir {
            std::fs::create_dir(&p).unwrap();
            std::fs::write(p.join("marker.txt"), format!("marker-{name}")).unwrap();
        } else {
            std::fs::write(&p, format!("marker-{name}")).unwrap();
        }
    }
    let before = vsnap(&top);
    let d = open_dir(&top, &["r"], DirOptions::default()).unwrap();
    let mut refused = 0;
    let mut opened = 0;
    let mut tally = |o: &str| {
        if o == "ReservedDevice" {
            refused += 1;
        } else {
            opened += 1;
        }
    };
    for (name, is_dir) in items {
        let marker = format!("marker-{name}");
        let kind = if is_dir { "目錄" } else { "一般檔" };
        // open_dir(目錄)或 open_file(一般檔):開到者須讀到標記
        let (what, o) = if is_dir {
            (
                "open_dir",
                h1_outcome(
                    name,
                    open_dir(&top, &["r", name], DirOptions::default()),
                    |sub| {
                        assert_eq!(
                            read_all(sub.open_file("marker.txt", FileAccess::Read).unwrap()),
                            marker
                        );
                    },
                ),
            )
        } else {
            (
                "open_file",
                h1_outcome(name, d.open_file(name, FileAccess::Read), |f| {
                    assert!(f.metadata().unwrap().is_file());
                    assert_eq!(read_all(f), marker);
                }),
            )
        };
        tally(o);
        println!("T3 H-1 {what} {name}({kind}):{o}");
        let o = h1_outcome(name, d.lstat(name), |m| {
            let m = m.expect("lstat 開到目錄項時應存在");
            let want = if is_dir {
                EntryKind::Dir
            } else {
                EntryKind::File
            };
            assert_eq!(m.kind, want);
        });
        tally(o);
        println!("T3 H-1 lstat {name}({kind}):{o}");
    }
    // entries:列舉 r 本身(含三個保留名稱項目)
    let o = h1_outcome("entries", d.entries(), |list| {
        let names: Vec<String> = list
            .iter()
            .map(|(n, _)| n.to_string_lossy().to_string())
            .collect();
        assert_eq!(names, ["COM1", "con", "nul"]);
    });
    tally(o);
    println!("T3 H-1 entries(r 內含 con、nul、COM1):{o}");
    assert_eq!(
        vsnap(&top),
        before,
        "回 ReservedDevice 時磁碟不得有任何變動"
    );
    // rmtree:開到者須刪掉該目錄項本身
    for (name, is_dir) in items {
        let kind = if is_dir { "目錄" } else { "一般檔" };
        let o = h1_outcome(name, d.rmtree(name, false), |()| {
            assert!(std::fs::symlink_metadata(r.join(name)).is_err());
        });
        tally(o);
        if o == "ReservedDevice" {
            assert!(
                std::fs::symlink_metadata(r.join(name)).is_ok(),
                "{name} 回 ReservedDevice 時不得刪除"
            );
        }
        println!("T3 H-1 rmtree {name}({kind}):{o}");
    }
    // create_excl:不得開到裝置(建立出磁碟上的檔案,或 ReservedDevice 且沒有建立任何東西)
    std::fs::create_dir(top.join("c")).unwrap();
    let c = open_dir(&top, &["c"], DirOptions::default()).unwrap();
    let mut created = 0;
    for name in ["con", "CON.txt", "nul"] {
        let o = h1_outcome(name, c.create_excl(name, W, 0o600), |f| {
            assert!(f.metadata().unwrap().is_file());
            assert!(
                std::fs::symlink_metadata(verbatim(&top.join("c").join(name)))
                    .unwrap()
                    .is_file()
            );
        });
        tally(o);
        if o == "ReservedDevice" {
            assert!(
                std::fs::symlink_metadata(verbatim(&top.join("c").join(name))).is_err(),
                "create_excl {name} 回 ReservedDevice 時不得建立任何東西"
            );
        } else {
            created += 1;
        }
        println!("T3 H-1 create_excl {name}:{o}");
    }
    assert_eq!(
        std::fs::read_dir(verbatim(&top.join("c"))).unwrap().count(),
        created,
        "c/ 的項目數須等於 create_excl 開到目錄項的次數(回 ReservedDevice 者不得建立任何東西)"
    );
    println!("T3 H-1 合計:開到目錄項 {opened}、ReservedDevice {refused}");

    // rmtree 中途失敗:子項依碼位順序處理,a.txt 先刪除,遇到 con 以 ReservedDevice 失敗,z.txt 留下;
    // 已刪的 a.txt 不還原(Python 的 rmtree 中途失敗同樣不還原)
    let p = verbatim(&top.join("p"));
    std::fs::create_dir(&p).unwrap();
    std::fs::write(p.join("a.txt"), "a").unwrap();
    std::fs::create_dir(p.join("con")).unwrap();
    std::fs::write(p.join("z.txt"), "z").unwrap();
    let t = open_dir(&top, NONE, DirOptions::default()).unwrap();
    let e = t.rmtree("p", false).unwrap_err();
    assert_eq!(e.kind(), ErrorKind::ReservedDevice);
    assert!(
        std::fs::symlink_metadata(p.join("a.txt")).is_err(),
        "a.txt 已刪除、不還原"
    );
    assert!(std::fs::symlink_metadata(p.join("con")).unwrap().is_dir());
    assert!(
        std::fs::symlink_metadata(p.join("z.txt"))
            .unwrap()
            .is_file()
    );
    println!(
        "T3 H-1 rmtree 中途遇到 con:ReservedDevice;先處理的 a.txt 已刪除、不還原,con 與 z.txt 留下"
    );
}

/// C-1:`CONIN$`、`CONOUT$`、`CLOCK$`(cap-std 不擋、`reserved_device` 也不擋)在建立、查詢、改名、硬連結、
/// 刪除各函式都對應磁碟上的目錄項,不會開到主控台裝置。CI `windows-latest` 再確認一次。
#[test]
fn c1_console_names_are_disk_entries_in_every_function() {
    let w = WorkDir::new();
    let top = w.top();
    let d = open_dir(&top, NONE, DirOptions::default()).unwrap();
    for name in ["CONIN$", "CONOUT$", "CLOCK$", "conin$.txt"] {
        let on_disk = || std::fs::symlink_metadata(verbatim(&top.join(name)));
        {
            let mut f = d.create_excl(name, RW, 0o600).unwrap();
            assert!(f.metadata().unwrap().is_file());
            f.write_all(b"console-name").unwrap();
        }
        assert!(on_disk().unwrap().is_file(), "{name} 未建立出磁碟上的檔案");
        assert_eq!(d.lstat(name).unwrap().unwrap().kind, EntryKind::File);
        assert_eq!(
            read_all(d.open_file(name, FileAccess::Read).unwrap()),
            "console-name"
        );
        d.rename(name, &d, "renamed").unwrap();
        assert!(on_disk().is_err());
        d.rename("renamed", &d, name).unwrap();
        assert!(on_disk().unwrap().is_file());
        d.hard_link(name, &d, "hl").unwrap();
        assert_eq!(
            read_all(d.open_file("hl", FileAccess::Read).unwrap()),
            "console-name"
        );
        d.unlink("hl", false).unwrap();
        d.unlink(name, false).unwrap();
        assert!(on_disk().is_err(), "{name} unlink 後磁碟上應已刪除");
        drop(
            d.open_child(
                name,
                DirOptions {
                    create: true,
                    ..DirOptions::default()
                },
            )
            .unwrap(),
        );
        assert!(on_disk().unwrap().is_dir(), "{name} 未建立出磁碟上的目錄");
        d.rmtree(name, false).unwrap();
        assert!(on_disk().is_err(), "{name} rmtree 後磁碟上應已刪除");
        println!(
            "T3 C-1 {name}:create_excl、lstat、open_file、rename、hard_link、unlink、open_child(建立)、rmtree 皆對應磁碟上的目錄項"
        );
    }
    assert!(vsnap(&top).is_empty());
}

/// D-6:NTFS 不分大小寫——只差大小寫的名稱開到同一項目(Linux 找不到;macOS APFS 預設與此相同)。
#[test]
fn d6_case_only_names_open_the_same_entry() {
    let w = WorkDir::new();
    let top = w.top();
    let d = open_dir(&top, NONE, DirOptions::default()).unwrap();
    drop(d.create_excl("Abc.txt", W, 0o600).unwrap());
    std::fs::write(top.join("Abc.txt"), "abc").unwrap();
    assert_eq!(
        read_all(d.open_file("aBC.TXT", FileAccess::Read).unwrap()),
        "abc"
    );
    assert_eq!(
        d.lstat("ABC.TXT").unwrap().unwrap().identity,
        d.lstat("Abc.txt").unwrap().unwrap().identity
    );
    assert_eq!(
        d.create_excl("ABC.TXT", W, 0o600).unwrap_err().kind(),
        ErrorKind::AlreadyExists
    );
    assert_eq!(d.entry_names().unwrap(), ["Abc.txt"]);
    println!("T3 D-6 只差大小寫的名稱:開到同一項目(identity 相同),排他建立回 AlreadyExists");
}

/// D-10:開著 handle 就刪除或搬移(`studio_server.py:1064-1065`、`tts_service.py:254-256`、`project.py:1403`)。
/// 實測結果以斷言鎖住:檔案 handle 開著時 `unlink` 成功、名稱立即消失(NTFS POSIX 刪除語意)、handle 照常
/// 可讀寫、同名可再建立;檔案改名到其他目錄成功,之後的寫入落在新位置;持有目錄 handle 時改名該目錄為
/// 共用違規(`Other`,32);只持有目錄內檔案的 handle 時改名該目錄為拒絕存取(`PermissionDenied`,5)。
#[test]
fn d10_unlink_and_rename_while_handle_is_open() {
    use std::io::Seek;
    let w = WorkDir::new();
    let top = w.top();
    let d = open_dir(&top, NONE, DirOptions::default()).unwrap();
    // 匿名暫存檔手法:create_excl 後立刻 unlink,繼續用 handle
    let mut f = d.create_excl("tmp", RW, 0o600).unwrap();
    f.write_all(b"abc").unwrap();
    d.unlink("tmp", false).unwrap();
    assert!(d.lstat("tmp").unwrap().is_none(), "unlink 後名稱應立即消失");
    assert!(d.entry_names().unwrap().is_empty());
    f.seek(std::io::SeekFrom::Start(0)).unwrap();
    let mut s = String::new();
    f.read_to_string(&mut s).unwrap();
    assert_eq!(s, "abc");
    f.write_all(b"def").unwrap();
    drop(d.create_excl("tmp", W, 0o600).unwrap());
    assert_eq!(
        d.lstat("tmp").unwrap().unwrap().len,
        0,
        "同名再建立的是新檔"
    );
    drop(f);
    println!("T3 D-10 檔案 handle 開著時 unlink:成功,名稱立即消失,handle 照常讀寫,同名可再建立");
    // 開著的檔案改名到其他目錄
    std::fs::create_dir(top.join("trash")).unwrap();
    let t = open_dir(&top, &["trash"], DirOptions::default()).unwrap();
    let mut g = d.create_excl("g", RW, 0o600).unwrap();
    g.write_all(b"g").unwrap();
    d.rename("g", &t, "g2").unwrap();
    g.write_all(b"2").unwrap();
    drop(g);
    assert_eq!(read_all(t.open_file("g2", FileAccess::Read).unwrap()), "g2");
    println!("T3 D-10 檔案 handle 開著時改名到其他目錄:成功,之後的寫入落在新位置");
    // project.py:1403:持有專案目錄 handle 與其內鎖檔時把專案目錄搬到 .trash/
    std::fs::create_dir(top.join("proj")).unwrap();
    let p = open_dir(&top, &["proj"], DirOptions::default()).unwrap();
    let lock = p.open_or_create(".lock", RW, 0o600).unwrap();
    let e = d.rename("proj", &t, "proj").unwrap_err();
    assert_eq!(e.kind(), ErrorKind::Other(Some(32)));
    println!(
        "T3 D-10 持有目錄 handle 時改名該目錄:失敗 {:?}(錯誤碼 32,共用違規)",
        e.kind()
    );
    drop(p);
    let e = d.rename("proj", &t, "proj").unwrap_err();
    assert_eq!(e.kind(), ErrorKind::PermissionDenied);
    assert_eq!(e.raw_os_error(), Some(5));
    println!(
        "T3 D-10 只持有目錄內檔案的 handle 時改名該目錄:失敗 {:?}(錯誤碼 5,拒絕存取)",
        e.kind()
    );
    drop(lock);
    d.rename("proj", &t, "proj").unwrap();
    assert!(t.lstat("proj").unwrap().unwrap().kind == EntryKind::Dir);
    println!("T3 D-10 關閉全部 handle 後改名該目錄:成功");
}

/// D-11:8.3 短檔名別名。磁碟區保留短檔名時,別名開到同一項目(identity 相同),且型別檢查照常——
/// junction 的別名同樣是 `Symlink`,開啟回 `Unsafe`,所以別名無法繞過連結拒絕。磁碟區不保留短檔名
/// (例:CI 的磁碟區可能停用)時印出並略過斷言。
#[test]
fn d11_short_name_alias_opens_the_same_entry_and_type_check_applies() {
    let w = WorkDir::new();
    let top = w.top();
    std::fs::write(top.join("longfilename-abc.txt"), "long").unwrap();
    junction(&top.join("longjunctionname"), &w.outside());
    let before = snapshot(&w.outside());
    let d = open_dir(&top, NONE, DirOptions::default()).unwrap();
    let long = d.lstat("longfilename-abc.txt").unwrap().unwrap();
    let alias = d.lstat("LONGFI~1.TXT").unwrap();
    let Some(alias) = alias.filter(|m| m.identity == long.identity) else {
        println!("T3 D-11 本磁碟區不保留 8.3 短檔名(LONGFI~1.TXT 不存在):略過斷言");
        return;
    };
    assert_eq!(alias.kind, EntryKind::File);
    assert_eq!(
        read_all(d.open_file("LONGFI~1.TXT", FileAccess::Read).unwrap()),
        "long"
    );
    assert_eq!(
        d.lstat("LONGJU~1").unwrap().unwrap().kind,
        EntryKind::Symlink
    );
    assert_eq!(
        d.open_child("LONGJU~1", DirOptions::default())
            .unwrap_err()
            .kind(),
        ErrorKind::Unsafe
    );
    assert_eq!(
        d.open_file("LONGJU~1", FileAccess::Read)
            .unwrap_err()
            .kind(),
        ErrorKind::Unsafe
    );
    assert_eq!(
        open_path(&top, &["LONGJU~1", "marker.txt"], FileAccess::Read)
            .unwrap_err()
            .kind(),
        ErrorKind::Unsafe
    );
    // 變更類(完整路徑經 Win32)以別名刪除的是原檔
    d.unlink("LONGFI~1.TXT", false).unwrap();
    assert!(d.lstat("longfilename-abc.txt").unwrap().is_none());
    assert_eq!(snapshot(&w.outside()), before, "outside/ 不得有任何變動");
    println!(
        "T3 D-11 8.3 別名:LONGFI~1.TXT 開到原檔(identity 相同)、以別名 unlink 刪除原檔;junction 別名 LONGJU~1 為 Symlink、開啟回 Unsafe"
    );
}

/// W-6 定點(代決 14;CI 與本機都跑):在空目錄建立第一個名稱的目錄,再以 `lstat` 查第二個名稱;
/// 印出「NTFS 是否視為同名」與「`fold_name` 是否相等」。NTFS 結果依磁碟區的 `$UpCase` 表而定,只印出、
/// 不斷言;`fold_name` 的結果固定,斷言之。
#[test]
fn w6_ntfs_case_mapping_targeted() {
    let w = WorkDir::new();
    let top = w.top();
    let d = open_dir(&top, NONE, DirOptions::default()).unwrap();
    let create = DirOptions {
        create: true,
        ..DirOptions::default()
    };
    // (建立的名稱, 查的名稱, fold_name 是否相等)
    let pairs: [(&str, &str, bool); 6] = [
        ("\u{131}", "I", false),
        ("\u{131}", "i", false),
        ("I", "\u{131}", false),
        ("\u{df}", "ss", true),
        ("\u{212a}", "k", true),
        ("\u{17f}", "s", true),
    ];
    for (i, (made, query, fold_eq)) in pairs.into_iter().enumerate() {
        let sub = d.open_child(format!("w{i}"), create).unwrap();
        drop(sub.open_child(made, create).unwrap());
        let same = sub.lstat(query).unwrap().is_some();
        let folded = common::fold_name(made) == common::fold_name(query);
        assert_eq!(
            folded, fold_eq,
            "fold_name({made:?}) 與 fold_name({query:?})"
        );
        println!(
            "T3 W-6 定點 {made}(U+{:04X})/ {query}:NTFS 視為同名 {}、fold_name 相等 {}",
            u32::from(made.chars().next().unwrap()),
            if same { "是" } else { "否" },
            if folded { "是" } else { "否" }
        );
    }
}

/// W-6 全面查證(`#[ignore]`;T3 與 Acceptance operation 以 `--ignored` 在 Windows 開發機執行):
/// U+0000~U+FFFF 中可作為 Windows 檔名的每個字元(`check_name` 合格、不是保留裝置名、不是代理單位),
/// 依碼位順序在空目錄內以 `create_excl` 排他建立單字元檔名(內容為碼位);`AlreadyExists` 即表示 NTFS 視它
/// 與先前某字元同名,讀回既有檔案的內容得到配對。最後印出「NTFS 同名但 `fold_name` 不同」與
/// 「`fold_name` 相同但 NTFS 不同名」兩組的數量與前 50 筆,以及耗時。只記錄、不以常數鎖住(`$UpCase` 依磁碟區)。
#[test]
#[ignore = "W-6 全面查證:建立約 6 萬個檔案,只在 Windows 開發機以 --ignored 執行"]
fn w6_ntfs_case_mapping_full_bmp() {
    use std::collections::BTreeMap;
    use std::ffi::OsStr;
    let started = std::time::Instant::now();
    let w = WorkDir::new();
    let top = w.top();
    let d = open_dir(&top, NONE, DirOptions::default()).unwrap();
    // class[c]:c 所屬 NTFS 同名類的代表字元(最先建立成功的那一個)
    let mut class: BTreeMap<char, char> = BTreeMap::new();
    let mut skipped = 0usize;
    let mut failed: Vec<(char, String)> = Vec::new();
    for cp in 0u32..=0xFFFF {
        let Some(ch) = char::from_u32(cp) else {
            continue; // 代理單位
        };
        let name = ch.to_string();
        let os = OsStr::new(&name);
        if safe_fs::check_name(os).is_err() || safe_fs::reserved_device(os) {
            skipped += 1;
            continue;
        }
        match d.create_excl(&name, W, 0o600) {
            Ok(mut f) => {
                f.write_all(format!("{cp:04X}").as_bytes()).unwrap();
                class.insert(ch, ch);
            }
            Err(e) if e.kind() == ErrorKind::AlreadyExists => {
                let hex = read_all(d.open_file(&name, FileAccess::Read).unwrap());
                let partner = char::from_u32(u32::from_str_radix(&hex, 16).unwrap()).unwrap();
                let rep = class[&partner];
                class.insert(ch, rep);
            }
            Err(e) => failed.push((ch, format!("{:?}", e.kind()))),
        }
    }
    let created = class.iter().filter(|(c, r)| c == r).count();
    let probe_secs = started.elapsed().as_secs_f64();
    let show = |c: char| format!("U+{:04X}", u32::from(c));
    // 第一組:NTFS 同名但 fold_name 不同
    let mut members: BTreeMap<char, Vec<char>> = BTreeMap::new();
    for (&c, &r) in &class {
        members.entry(r).or_default().push(c);
    }
    let mut ntfs_only = Vec::new();
    for group in members.values() {
        for (i, &a) in group.iter().enumerate() {
            for &b in &group[i + 1..] {
                if common::fold_name(&a.to_string()) != common::fold_name(&b.to_string()) {
                    ntfs_only.push((a, b));
                }
            }
        }
    }
    // 第二組:fold_name 相同但 NTFS 不同名
    let mut by_fold: BTreeMap<String, Vec<char>> = BTreeMap::new();
    for &c in class.keys() {
        by_fold
            .entry(common::fold_name(&c.to_string()))
            .or_default()
            .push(c);
    }
    let mut fold_only = Vec::new();
    for group in by_fold.values() {
        for (i, &a) in group.iter().enumerate() {
            for &b in &group[i + 1..] {
                if class[&a] != class[&b] {
                    fold_only.push((a, b));
                }
            }
        }
    }
    println!(
        "T3 W-6 全面查證:檢查 {} 個字元(略過 {skipped} 個不合格或保留名稱、代理單位另計),建立 {created} 個,NTFS 同名 {} 個,其他錯誤 {}",
        class.len() + failed.len(),
        class.len() - created,
        failed.len()
    );
    for (c, k) in failed.iter().take(10) {
        println!("T3 W-6 其他錯誤:{} {k}", show(*c));
    }
    println!(
        "T3 W-6 第一組(NTFS 同名但 fold_name 不同):{} 組",
        ntfs_only.len()
    );
    for (a, b) in ntfs_only.iter().take(50) {
        println!(
            "T3 W-6   {} {a} ~ {} {b}(fold:{:?} / {:?})",
            show(*a),
            show(*b),
            common::fold_name(&a.to_string()),
            common::fold_name(&b.to_string())
        );
    }
    println!(
        "T3 W-6 第二組(fold_name 相同但 NTFS 不同名):{} 組",
        fold_only.len()
    );
    for (a, b) in fold_only.iter().take(50) {
        println!(
            "T3 W-6   {} {a} / {} {b}(fold:{:?})",
            show(*a),
            show(*b),
            common::fold_name(&a.to_string())
        );
    }
    drop(d);
    drop(w);
    println!(
        "T3 W-6 耗時:建立與查證 {probe_secs:.1} 秒,含清理 {:.1} 秒",
        started.elapsed().as_secs_f64()
    );
}
