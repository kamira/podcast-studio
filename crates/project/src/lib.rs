//! 專案資料模型(`project.py` 的 Rust 版;CHG-20261128-01)。
//!
//! 路線圖第 5 筆:專案檔 schema 2 的讀取 / 驗證 / 以 Python 相同位元組寫出、`history/` 與專案鎖。
//! 依 task 逐步加入(T1~T7);本筆沒有任何產品呼叫端,Python 版行為不變。
//!
//! 目前的模組(T1):
//! - [`error`]:[`Error`]、[`ErrorKind`]——錯誤類別與逐字與 Python 相同的錯誤訊息清單(代決 9)。
//! - [`pyjson`]:與 Python `_dump`(`json.dumps(ensure_ascii=False, indent=1)` + 換行)逐位元組相同的寫出器,
//!   以及 `post_key` 用的緊湊、鍵排序寫法;浮點依 Python `repr`(代決 6)。
//! - [`round`]:`Decimal(str(v))` + `ROUND_HALF_UP` 的十進位四捨五入,全程整數運算(代決 7)。
//! - [`pystr`]:Python 字串語意——碼位長度、3.11 的空白字元集與 `strip`、漢字與注音、`ID_RE` 等樣式(代決 8)。
//! - [`constants`]:`project.py` 的常數。
//! - [`checks`]:單值檢查 `check_name`、`normalize_speed`、`normalize_pause`、`check_zhuyin`、`check_percent`、
//!   `check_pitch_points`。
//!
//! JSON 解析用 `serde_json`(開 `float_roundtrip`,浮點解析正確捨入,與 Python `float()` 相同;代決 5)。
//! `src/` 不含平台條件編譯(平台差異由標準函式庫與 `safe-fs` 承擔;Global Constraints)。
#![forbid(unsafe_code)]

pub mod checks;
pub mod constants;
pub mod error;
pub mod pyjson;
pub mod pystr;
pub mod round;

pub use error::{Error, ErrorKind, Result};
