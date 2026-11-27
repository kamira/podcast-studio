//! 識別碼、隨機碼與時鐘(CHG-20261128-01 代決 13;T3)。
//!
//! - [`new_id`]:Python `new_id()`(`secrets.token_hex(8)`)——16 個小寫十六進位字元,伺服器產生的片段 /
//!   圖片 / 素材 id。
//! - [`token_hex`]:Python `secrets.token_hex(n)` 的格式(`2n` 個小寫十六進位字元)。
//! - [`Clock`]:寫入類 API(T6 起)注入的時間與隨機碼來源;[`SystemClock`] 為預設實作(系統時間、
//!   [`token_hex`]),比對與測試注入固定值。
//!
//! 亂數來源(代決 13 T3 補記):`std::random` 在 1.100.0 未穩定(T1 查證,E0658),不引入 `rand` / `getrandom`,
//! 改以標準函式庫的 `std::hash::RandomState` 加全域計數器——`RandomState` 的金鑰由作業系統亂數取種子
//! (每個執行緒一次,之後每次 `new()` 遞增),以它對「計數器值」做 SipHash 得到 64 位元。識別碼與暫存檔名只需
//! **不重複**(每個用途都有撞名重試或重複檢查),不是祕密,這個做法足夠;格式與 Python 相同,比對以格式驗證、
//! 不比對值(`tests/ids.rs`)。

use std::hash::{BuildHasher, Hasher, RandomState};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

static COUNTER: AtomicU64 = AtomicU64::new(0);

/// 64 位元的亂數(見模組說明)。
pub fn random_u64() -> u64 {
    let mut h = RandomState::new().build_hasher();
    h.write_u64(COUNTER.fetch_add(1, Ordering::Relaxed));
    h.finish()
}

/// Python `secrets.token_hex(nbytes)` 的格式:`2 × nbytes` 個小寫十六進位字元。
pub fn token_hex(nbytes: usize) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(nbytes * 2);
    let mut word = 0u64;
    for i in 0..nbytes {
        if i % 8 == 0 {
            word = random_u64();
        }
        let b = (word >> ((i % 8) * 8)) as u8;
        out.push(char::from(HEX[usize::from(b >> 4)]));
        out.push(char::from(HEX[usize::from(b & 0x0f)]));
    }
    out
}

/// Python `new_id()`:伺服器產生的片段 / 圖片 / 素材 id(16 碼小寫十六進位)。
pub fn new_id() -> String {
    token_hex(8)
}

/// 寫入類 API 的時間與隨機碼來源(代決 13;T6 起由 `save` 等注入)。
pub trait Clock {
    /// 現在時刻:UNIX 紀元起的毫秒數(早於紀元為負)。
    fn now_ms(&self) -> i64;
    /// 版本檔與回收項目名稱的 4 碼隨機碼(Python `secrets.token_hex(2)`)。
    fn rand4(&self) -> String;
    /// 暫存檔名的 16 碼隨機碼(Python `secrets.token_hex(8)`)。
    fn token_hex8(&self) -> String;
}

/// 預設的 [`Clock`]:系統時間與 [`token_hex`]。
#[derive(Debug, Clone, Copy, Default)]
pub struct SystemClock;

impl Clock for SystemClock {
    fn now_ms(&self) -> i64 {
        match SystemTime::now().duration_since(UNIX_EPOCH) {
            Ok(d) => i64::try_from(d.as_millis()).unwrap_or(i64::MAX),
            Err(e) => i64::try_from(e.duration().as_millis()).map_or(i64::MIN, |ms| -ms),
        }
    }

    fn rand4(&self) -> String {
        token_hex(2)
    }

    fn token_hex8(&self) -> String {
        token_hex(8)
    }
}
