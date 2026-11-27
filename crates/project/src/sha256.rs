//! SHA-256(FIPS 180-4 §6.2;CHG-20261128-01 代決 18)。
//!
//! 只供後製鍵 [`crate::post::post_key`](Python `hashlib.sha256(...).hexdigest()`)使用:後製鍵是快取鍵,
//! 碰撞只會讓過期的後製結果被重用,不是安全邊界,所以自寫、不引入 crate。正確性以
//! `tests/sha256.rs`(NIST 測試向量、0~130 位元組各長度與多區塊邊界、分段輸入、常數推導)與
//! 比對單元 `project.post_key`(對 Python `hashlib`)鎖住。
//!
//! 全程只用 `wrapping_*` 與位元運算,任何輸入都不會 panic;訊息長度以位元組計數、位元長度取模 2⁶⁴
//! (FIPS 180-4 的上限是 2⁶⁴ − 1 位元,實際用途遠小於此)。

/// 64 個回合常數:前 64 個質數立方根小數部分的前 32 位元(FIPS 180-4 §4.2.2)。
const K: [u32; 64] = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];

/// 初始雜湊值:前 8 個質數平方根小數部分的前 32 位元(FIPS 180-4 §5.3.3)。
const H0: [u32; 8] = [
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
];

/// 回合常數(測試以整數立方根重新推導、核對)。
pub const fn round_constants() -> [u32; 64] {
    K
}

/// 初始雜湊值(測試以整數平方根重新推導、核對)。
pub const fn initial_hash() -> [u32; 8] {
    H0
}

/// 可分段輸入的 SHA-256。
#[derive(Debug, Clone)]
pub struct Sha256 {
    state: [u32; 8],
    block: [u8; 64],
    filled: usize,
    total: u64,
}

impl Default for Sha256 {
    fn default() -> Self {
        Self::new()
    }
}

impl Sha256 {
    /// 新的雜湊狀態。
    pub const fn new() -> Self {
        Self {
            state: H0,
            block: [0; 64],
            filled: 0,
            total: 0,
        }
    }

    /// 加入資料(可多次呼叫;結果與一次加入全部資料相同)。
    pub fn update(&mut self, mut data: &[u8]) {
        self.total = self.total.wrapping_add(data.len() as u64);
        if self.filled > 0 {
            let take = (64 - self.filled).min(data.len());
            self.block[self.filled..self.filled + take].copy_from_slice(&data[..take]);
            self.filled += take;
            data = &data[take..];
            if self.filled < 64 {
                return;
            }
            let block = self.block;
            compress(&mut self.state, &block);
            self.filled = 0;
        }
        let (blocks, rest) = data.as_chunks::<64>();
        for block in blocks {
            compress(&mut self.state, block);
        }
        self.block[..rest.len()].copy_from_slice(rest);
        self.filled = rest.len();
    }

    /// 補位(`0x80`、補零到 56 mod 64、64 位元大端序的位元長度)後的 32 位元組摘要。
    pub fn finalize(mut self) -> [u8; 32] {
        let bits = self.total.wrapping_mul(8);
        let mut pad = [0u8; 64];
        pad[0] = 0x80;
        let pad_len = if self.filled < 56 {
            56 - self.filled
        } else {
            120 - self.filled
        };
        self.update(&pad[..pad_len]);
        self.update(&bits.to_be_bytes());
        let mut out = [0u8; 32];
        for (i, word) in self.state.iter().enumerate() {
            out[i * 4..i * 4 + 4].copy_from_slice(&word.to_be_bytes());
        }
        out
    }
}

/// 一個 512 位元區塊的壓縮函數(FIPS 180-4 §6.2.2)。
fn compress(state: &mut [u32; 8], block: &[u8; 64]) {
    let mut w = [0u32; 64];
    for (t, word) in block.as_chunks::<4>().0.iter().enumerate() {
        w[t] = u32::from_be_bytes(*word);
    }
    for t in 16..64 {
        let s0 = w[t - 15].rotate_right(7) ^ w[t - 15].rotate_right(18) ^ (w[t - 15] >> 3);
        let s1 = w[t - 2].rotate_right(17) ^ w[t - 2].rotate_right(19) ^ (w[t - 2] >> 10);
        w[t] = w[t - 16]
            .wrapping_add(s0)
            .wrapping_add(w[t - 7])
            .wrapping_add(s1);
    }
    let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut h] = *state;
    for t in 0..64 {
        let big_s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
        let ch = (e & f) ^ (!e & g);
        let t1 = h
            .wrapping_add(big_s1)
            .wrapping_add(ch)
            .wrapping_add(K[t])
            .wrapping_add(w[t]);
        let big_s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
        let maj = (a & b) ^ (a & c) ^ (b & c);
        let t2 = big_s0.wrapping_add(maj);
        h = g;
        g = f;
        f = e;
        e = d.wrapping_add(t1);
        d = c;
        c = b;
        b = a;
        a = t1.wrapping_add(t2);
    }
    for (s, v) in state.iter_mut().zip([a, b, c, d, e, f, g, h]) {
        *s = s.wrapping_add(v);
    }
}

/// 一次算出 `data` 的摘要。
pub fn digest(data: &[u8]) -> [u8; 32] {
    let mut h = Sha256::new();
    h.update(data);
    h.finalize()
}

/// 摘要的小寫十六進位字串(Python `hexdigest()`)。
pub fn hex_digest(data: &[u8]) -> String {
    use std::fmt::Write;
    digest(data)
        .iter()
        .fold(String::with_capacity(64), |mut s, b| {
            let _ = write!(s, "{b:02x}");
            s
        })
}
