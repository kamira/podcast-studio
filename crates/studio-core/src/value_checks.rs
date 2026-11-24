//! 跨模組共用的值檢查(Python `value_checks.py` 的 Rust 版;CHG-20261124-01 T1,代決 3~5)。
//!
//! 純函式:不讀寫檔案、不讀環境變數、不分平台。
//!
//! 值域為 `serde_json::Value`:在此值域內與 Python `value_checks.is_int` 等價。
//! JSON 原文層級的已知差異(行為差異清單 D-5、D-6)不屬本函式:serde_json(未開
//! `arbitrary_precision`)把原文 `-0` 解析為浮點 `-0.0`、把超出 `u64` / 低於 `i64` 的整數
//! 解析為浮點,因此本函式回 `false`;Python `json.loads` 對兩者給 `int`,`is_int` 為 `True`。
//! 處置交給讀 JSON 的呼叫端(路線圖第 5 筆 `project`、第 9 筆 `settings`)。

use serde_json::Value;

/// `v` 是整數才為 `true`:`Value::Number` 且可表示為 `i64` 或 `u64`。
///
/// 對應 Python `isinstance(v, int) and not isinstance(v, bool)`:布林(`Value::Bool`)、
/// 浮點(含 `1.0`、`-0.0`、`1e2`)、字串、`null`、陣列、物件一律為 `false`。
pub fn is_int(v: &Value) -> bool {
    match v {
        Value::Number(n) => n.is_i64() || n.is_u64(),
        _ => false,
    }
}
