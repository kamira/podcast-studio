//! 單值檢查(`project.py` 第 218~378 行的公開檢查;CHG-20261128-01 T1)。
//!
//! 輸入為 `serde_json::Value`(Python 端是任意型別),錯誤訊息逐字與 Python 相同(`label` 即 Python 的 `where`,
//! 原樣前置)。數值:Python `_is_number` 為「`int` 或 `float`、不是 `bool`、有限」——`serde_json` 的數字一律有限
//! (`NaN`、`Infinity` 在解析時就被拒絕,行為差異 D-3),所以等於 `Value::Number`。

use serde_json::Value;
use studio_core::{names, value_checks};

use crate::constants::{
    INNER_POINTS_MAX, INNER_SEMI_LIMIT, NAME_HINT, PAUSE_MAX_TEXT, PAUSE_MIN_TEXT, SPEED_MAX_TEXT,
    SPEED_MIN_TEXT, ZHUYIN_MAX,
};
use crate::pyjson::float_repr;
use crate::pystr::{char_len, is_zhuyin_char, marked_to_digit};
use crate::round::{PAUSE_STEP, SPEED_STEP, round_half_up};
use crate::{Error, Result};

/// 語速的步數範圍(0.5 / 0.05 = 10、2.0 / 0.05 = 40)。
const SPEED_STEPS: std::ops::RangeInclusive<i128> = 10..=40;
/// 停頓的步數範圍(0 / 0.1 = 0、5 / 0.1 = 50)。
const PAUSE_STEPS: std::ops::RangeInclusive<i128> = 0..=50;

/// 注音標註不合格時的訊息(Python `check_zhuyin`)。
pub const ZHUYIN_MESSAGE: &str = "注音標註只接受注音符號與聲調符號(ˊˇˋ˙),長度 1 到 5";

/// Python `_is_number`。
pub fn is_number(v: &Value) -> bool {
    matches!(v, Value::Number(_))
}

/// Python `check_name`:既有專案名稱檢查(`names.normalize_name`);不合格回 `Invalid`(`NAME_HINT`)。
///
/// 建立新名稱另以 `names::normalize_new_name` 檢查(代決 15,T6)。
pub fn check_name(name: &str) -> Result<&str> {
    names::normalize_name(name).ok_or_else(|| Error::invalid(NAME_HINT))
}

/// Python `normalize_speed(v, where)`:四捨五入到 0.05 後必須在 0.5~2.0。
///
/// Python 對四捨五入後超過 28 位數的值丟 `InvalidOperation`(未分類例外);Rust 回範圍錯誤(行為差異 D-5,T1 補記)。
pub fn normalize_speed(v: &Value, label: &str) -> Result<f64> {
    let Value::Number(n) = v else {
        return Err(Error::invalid(format!("{label}必須是數字")));
    };
    match round_half_up(n, SPEED_STEP) {
        Some(r) if SPEED_STEPS.contains(&r.steps()) => Ok(r.to_f64()),
        _ => Err(Error::invalid(format!(
            "{label}必須在 {SPEED_MIN_TEXT}~{SPEED_MAX_TEXT} 倍之間"
        ))),
    }
}

/// Python `normalize_pause(v, where)`:四捨五入到 0.1 後必須在 0~5(`-0.04` 得 `-0.0`,與 Python 相同)。
///
/// 超過 28 位數的處置同 [`normalize_speed`]。
pub fn normalize_pause(v: &Value, label: &str) -> Result<f64> {
    let Value::Number(n) = v else {
        return Err(Error::invalid(format!("{label}必須是數字")));
    };
    match round_half_up(n, PAUSE_STEP) {
        Some(r) if PAUSE_STEPS.contains(&r.steps()) => Ok(r.to_f64()),
        _ => Err(Error::invalid(format!(
            "{label}必須在 {PAUSE_MIN_TEXT}~{PAUSE_MAX_TEXT} 秒之間"
        ))),
    }
}

/// Python `check_zhuyin(z)`:只接受注音符號與聲調符號、1~5 字,且是 `breezy_worker.marked_to_digit` 認得的格式。
pub fn check_zhuyin(z: &Value) -> Result<String> {
    match z {
        Value::String(s)
            if (1..=ZHUYIN_MAX).contains(&char_len(s))
                && s.chars().all(is_zhuyin_char)
                && marked_to_digit(s).is_some() =>
        {
            Ok(s.clone())
        }
        _ => Err(Error::invalid(ZHUYIN_MESSAGE)),
    }
}

/// Python `check_percent(v, where)`:`null` 或 0~100 的整數(`value_checks.is_int`)。
pub fn check_percent(v: &Value, label: &str) -> Result<Option<i64>> {
    if v.is_null() {
        return Ok(None);
    }
    if value_checks::is_int(v)
        && let Some(i) = v.as_i64()
        && (0..=100).contains(&i)
    {
        return Ok(Some(i));
    }
    Err(Error::invalid(format!(
        "{label}必須是 0 到 100 的整數(百分比)"
    )))
}

/// Python `check_pitch_points(points, where)`:2~30 個 `[位置, 半音]`,位置 0~1 嚴格遞增、半音 ±7.2;
/// 回傳的位置與半音一律為浮點(整數照 Python `float(int)` 轉換,`-0.0` 保留)。檢查順序與 Python 相同。
pub fn check_pitch_points(points: &Value, label: &str) -> Result<Vec<[f64; 2]>> {
    let count_error = || Error::invalid(format!("{label} 必須是 2 到 {INNER_POINTS_MAX} 個語調點"));
    let Value::Array(list) = points else {
        return Err(count_error());
    };
    if !(2..=INNER_POINTS_MAX).contains(&list.len()) {
        return Err(count_error());
    }
    let limit = float_repr(INNER_SEMI_LIMIT);
    let mut out = Vec::with_capacity(list.len());
    for q in list {
        let pair = match q {
            Value::Array(pair) if pair.len() == 2 && pair.iter().all(is_number) => pair,
            _ => {
                return Err(Error::invalid(format!(
                    "{label} 每個語調點必須是 [位置, 半音] 兩個數字"
                )));
            }
        };
        // 數字一律可轉成浮點(超出 f64 的整數不會出現,行為差異 D-2);邊界比較與 Python 的 int / float 比較結果相同
        let pos = pair[0].as_f64().unwrap_or(f64::NAN);
        let semi = pair[1].as_f64().unwrap_or(f64::NAN);
        if !(0.0..=1.0).contains(&pos) {
            return Err(Error::invalid(format!("{label} 的位置必須在 0 到 1 之間")));
        }
        if !(-INNER_SEMI_LIMIT..=INNER_SEMI_LIMIT).contains(&semi) {
            return Err(Error::invalid(format!(
                "{label} 的音高必須在 \u{2212}{limit} 到 +{limit} 半音之間"
            )));
        }
        out.push([pos, semi]);
    }
    if out.windows(2).any(|w| w[1][0] <= w[0][0]) {
        return Err(Error::invalid(format!("{label} 的位置必須嚴格遞增")));
    }
    Ok(out)
}
