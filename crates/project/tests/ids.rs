//! L1:`new_id`、`token_hex` 與 `Clock`(CHG-20261128-01 代決 13 T3 補記)。
//!
//! 亂數值本身不比對(兩邊各自取亂數):斷言格式與 Python 相同(`new_id` 16 碼、`rand4` 4 碼小寫十六進位,
//! 皆符合 `ID_RE` 的字元集)、大量產生不重複(含多執行緒)。macOS / Linux 另以 Python 子程序產生 `new_id`,
//! 確認 Python 的輸出符合同一個格式判定(Windows 無法執行 `project.py`,只斷言 Rust)。

#[cfg(unix)]
mod common;

use std::collections::HashSet;
use std::time::{SystemTime, UNIX_EPOCH};

use project::ids::{Clock, SystemClock, new_id, random_u64, token_hex};

/// Python `secrets.token_hex(n)` 的格式:`2n` 個 `[0-9a-f]`。
fn is_token(s: &str, nbytes: usize) -> bool {
    s.len() == nbytes * 2 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

#[test]
fn new_id_has_python_format() {
    for _ in 0..1000 {
        let id = new_id();
        assert!(is_token(&id, 8), "{id}");
        assert!(project::pystr::is_id(&id), "{id}");
    }
    for n in [0, 1, 2, 7, 8, 9, 16, 33] {
        assert!(is_token(&token_hex(n), n), "{n}");
    }
}

#[test]
fn ids_do_not_repeat_across_threads() {
    let per = 20_000;
    let handles: Vec<_> = (0..4)
        .map(|_| std::thread::spawn(move || (0..per).map(|_| new_id()).collect::<Vec<_>>()))
        .collect();
    let mut seen = HashSet::new();
    for h in handles {
        for id in h.join().expect("執行緒") {
            assert!(seen.insert(id), "重複的 id");
        }
    }
    assert_eq!(seen.len(), 4 * per);
    let words: HashSet<u64> = (0..10_000).map(|_| random_u64()).collect();
    assert_eq!(words.len(), 10_000);
    // 每個十六進位位置都出現多種字元(不是固定值或只用部分字元)
    let ids: Vec<String> = (0..2000).map(|_| new_id()).collect();
    for pos in 0..16 {
        let distinct: HashSet<u8> = ids.iter().map(|id| id.as_bytes()[pos]).collect();
        assert!(
            distinct.len() >= 12,
            "第 {pos} 位只有 {} 種字元",
            distinct.len()
        );
    }
}

#[test]
fn system_clock_values() {
    let c = SystemClock;
    let before = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("紀元後")
        .as_millis() as i64;
    let now = c.now_ms();
    let after = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("紀元後")
        .as_millis() as i64;
    assert!(before <= now && now <= after, "{before} {now} {after}");
    assert!(is_token(&c.rand4(), 2));
    assert!(is_token(&c.token_hex8(), 8));
    // 可注入固定值的實作(T6 的比對與測試做法)
    struct Fixed;
    impl Clock for Fixed {
        fn now_ms(&self) -> i64 {
            1_700_000_000_123
        }
        fn rand4(&self) -> String {
            "beef".into()
        }
        fn token_hex8(&self) -> String {
            "0123456789abcdef".into()
        }
    }
    let f: &dyn Clock = &Fixed;
    assert_eq!(
        (f.now_ms(), f.rand4(), f.token_hex8()),
        (1_700_000_000_123, "beef".into(), "0123456789abcdef".into())
    );
}

#[cfg(unix)]
#[test]
fn python_new_id_has_the_same_format() {
    let reqs: Vec<(&str, Vec<serde_json::Value>)> =
        (0..200).map(|_| ("new_id", Vec::new())).collect();
    let got = common::call(&reqs);
    let mut seen = HashSet::new();
    for out in got {
        let id = match out {
            common::PyOut::Value(serde_json::Value::String(id)) => id,
            common::PyOut::Value(v) => panic!("Python new_id 須回字串:{v}"),
            common::PyOut::Errors(e) => panic!("Python new_id 回錯誤:{e:?}"),
            common::PyOut::Exception(x) => panic!("Python new_id 丟出 {x}"),
        };
        assert!(is_token(&id, 8), "Python {id}");
        assert!(is_token(&new_id(), 8));
        seen.insert(id);
    }
    assert_eq!(seen.len(), 200);
    println!("new_id:Python 200 筆與 Rust 同一格式(16 碼小寫十六進位)");
}
