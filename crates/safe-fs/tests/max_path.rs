//! H-4 / 代決 13:深層路徑不受路徑長度上限影響(CHG-20261125-01 T3;三平台執行)。
//!
//! 以 `open_dir(create=true)` 建立 30 層、每層 20 字元(完整路徑超過 600 字元)的目錄,在最底層
//! `create_excl`、寫入、以 `open_path` 讀回,最後由最上層 `rmtree` 刪除,全部經 `safe-fs`。
//! Windows 上開啟類以相對 handle 進行、變更類以完整路徑經 Win32(D-12),長度由標準函式庫自動加的
//! `\\?\` 前綴承擔(上限約 32767 字元),不是 `MAX_PATH`;macOS / Linux 以相對 handle 避開 `PATH_MAX`。

mod common;

use std::io::{Read, Write};

use common::WorkDir;
use safe_fs::{DirOptions, FileAccess, open_dir, open_path};

const NONE: &[&str] = &[];

#[test]
fn deep_path_30_levels_of_20_chars() {
    let w = WorkDir::new();
    let top = w.top();
    let parts: Vec<String> = (0..30)
        .map(|i| format!("level-{i:02}-abcdefghijk"))
        .collect();
    assert!(parts.iter().all(|p| p.chars().count() == 20));
    let abs = std::path::absolute(&top).unwrap();
    let full_len = abs.to_string_lossy().chars().count()
        + parts.iter().map(|p| p.len() + 1).sum::<usize>()
        + "/f.txt".len();
    assert!(full_len > 600, "完整路徑只有 {full_len} 字元");
    let refs: Vec<&str> = parts.iter().map(String::as_str).collect();

    // 建立 30 層
    let deep = open_dir(
        &top,
        &refs,
        DirOptions {
            create: true,
            ..DirOptions::default()
        },
    )
    .unwrap();
    // 最底層排他建立並寫入
    {
        let mut f = deep
            .create_excl("f.txt", FileAccess::Write { truncate: false }, 0o600)
            .unwrap();
        f.write_all(b"deep-path").unwrap();
    }
    drop(deep);
    // 自最上層以 open_path 讀回
    let mut all = refs.clone();
    all.push("f.txt");
    let mut s = String::new();
    open_path(&top, &all, FileAccess::Read)
        .unwrap()
        .read_to_string(&mut s)
        .unwrap();
    assert_eq!(s, "deep-path");
    // 由最上層 rmtree 刪除
    let d = open_dir(&top, NONE, DirOptions::default()).unwrap();
    d.rmtree(refs[0], false).unwrap();
    assert!(d.entry_names().unwrap().is_empty(), "top 應已清空");
    println!("max_path:完整路徑 {full_len} 字元(30 層 × 20 字元):建立、寫入、讀回、rmtree 皆成功");
}
