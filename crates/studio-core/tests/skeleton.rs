//! T1 骨架測試(CHG-20261123-01):確認 `studio-core` 能被連結,
//! 且 `rust-version` 與 workspace 根目錄 `rust-toolchain.toml` 釘的版號相同。

use std::path::{Path, PathBuf};

// 只要 crate 不存在或無法連結,這一行就會讓測試編譯失敗。
use studio_core as _;

/// workspace 根目錄的 `rust-toolchain.toml`(本 crate 在 `crates/studio-core/`,往上兩層)。
fn toolchain_file() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .expect("找不到 workspace 根目錄")
        .join("rust-toolchain.toml")
}

/// 取出 `channel` 欄位的字串值(容許 `\r\n` 行尾)。
fn pinned_channel() -> String {
    let path = toolchain_file();
    let text =
        std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("讀不到 {}:{e}", path.display()));
    text.lines()
        .map(str::trim)
        .find_map(|line| {
            let (key, value) = line.split_once('=')?;
            (key.trim() == "channel").then(|| value.trim().trim_matches('"').to_string())
        })
        .unwrap_or_else(|| panic!("{} 沒有 channel 欄位", path.display()))
}

#[test]
fn toolchain_channel_is_full_version() {
    let channel = pinned_channel();
    let parts: Vec<&str> = channel.split('.').collect();
    assert_eq!(
        parts.len(),
        3,
        "channel 須為完整版號 x.y.z,實際為 {channel:?}"
    );
    assert!(
        parts
            .iter()
            .all(|p| !p.is_empty() && p.chars().all(|c| c.is_ascii_digit())),
        "channel 須為完整版號 x.y.z,實際為 {channel:?}"
    );
}

#[test]
fn rust_version_matches_toolchain() {
    assert_eq!(
        env!("CARGO_PKG_RUST_VERSION"),
        pinned_channel(),
        "Cargo.toml 的 rust-version 須與 rust-toolchain.toml 的 channel 相同"
    );
}
