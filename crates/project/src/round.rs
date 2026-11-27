//! 十進位四捨五入(CHG-20261128-01 代決 7)。
//!
//! 重現 Python `project._round`:`(Decimal(str(v)) / step).quantize(Decimal("1"), ROUND_HALF_UP) * step`。
//!
//! - `Decimal(str(v))`:整數為原值;浮點為 `repr` 的數字(最短往返表示,與 Rust `{:e}` 的數字相同)。
//!   數字以整數 `D` 與十進位指數 `e` 表示(值 = `D × 10^e`),全程整數運算,不經浮點。
//! - 除以步長:步長為 `k × 10^-m`(0.05 = 5 × 10⁻²、0.1 = 1 × 10⁻¹、0.01 = 1 × 10⁻²),
//!   商 = `D × 10^(e+m) / k`;Python 的 `Decimal` 精度 28 位,這個除法在本模組的輸入範圍內都是精確的。
//! - `ROUND_HALF_UP`:正好一半時遠離零進位;正負號另記(`-0.01` 四捨五入後是 `-0`,與 Python 相同)。
//! - 四捨五入後的整數超過 28 位數時,Python 的 `quantize` 丟 `decimal.InvalidOperation`(未分類例外);
//!   [`round_half_up`] 回 `None`,由呼叫端回報該欄位的範圍錯誤(行為差異 D-5,T1 補記)。
//! - 結果 [`Rounded::to_f64`]:以十進位字串 `<步數 × k>e-<m>` 交給 `str::parse::<f64>`(正確捨入,
//!   與 Python `float(Decimal)` 相同),負號保留(含 `-0.0`)。

use serde_json::Number;

use crate::pyjson::repr_digits;

/// 步長 `coef × 10^-scale`。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Step {
    coef: u128,
    scale: u32,
}

impl Step {
    /// `coef × 10^-scale`;`coef` 須為 1~9(本 crate 只用 1 與 5)。
    pub const fn new(coef: u8, scale: u8) -> Self {
        Self {
            coef: coef as u128,
            scale: scale as u32,
        }
    }
}

/// 語速步長 0.05。
pub const SPEED_STEP: Step = Step::new(5, 2);
/// 停頓步長 0.1。
pub const PAUSE_STEP: Step = Step::new(1, 1);
/// 語調關鍵點位置步長 0.01。
pub const POS_STEP: Step = Step::new(1, 2);
/// 語調關鍵點半音步長 0.1。
pub const SEMI_STEP: Step = Step::new(1, 1);

/// Python `Decimal` 預設精度(位數);四捨五入後的整數須少於 `10^PRECISION`。
const PRECISION: u32 = 28;

/// 四捨五入的結果:`(-1)^negative × steps × step`。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Rounded {
    negative: bool,
    steps: u128,
    step: Step,
}

impl Rounded {
    /// 帶正負號的步數(`-0` 為 0;比較範圍用,與 Python `Decimal` 比較相同)。
    pub fn steps(&self) -> i128 {
        // steps < 10^28 < i128::MAX
        let n = i128::try_from(self.steps).unwrap_or(i128::MAX);
        if self.negative { -n } else { n }
    }

    /// Python `float(r)`(負號保留,含 `-0.0`)。
    pub fn to_f64(&self) -> f64 {
        let sign = if self.negative { "-" } else { "" };
        let text = format!("{sign}{}e-{}", self.steps * self.step.coef, self.step.scale);
        text.parse().unwrap_or(f64::NAN)
    }
}

/// `n` 的十進位表示:(負號, 係數 `D`, 指數 `e`),值 = `D × 10^e`(Python `Decimal(str(n))`)。
fn decimal_parts(n: &Number) -> (bool, u128, i32) {
    if let Some(u) = n.as_u64() {
        return (false, u128::from(u), 0);
    }
    if let Some(i) = n.as_i64() {
        return (i < 0, u128::from(i.unsigned_abs()), 0);
    }
    let f = n.as_f64().unwrap_or(0.0);
    // 與 Python `str(float)`(= repr)相同的數字,含正好在中間時取偶數的規則
    let (digits, exp) = repr_digits(f.abs());
    let coef: u128 = digits.parse().unwrap_or(0);
    let len = i32::try_from(digits.len()).unwrap_or(0);
    (f.is_sign_negative(), coef, exp - (len - 1))
}

fn digit_count(n: u128) -> u32 {
    n.checked_ilog10().map_or(1, |d| d + 1)
}

/// `n / d` 四捨五入到整數,正好一半時進位(`n`、`d` 皆非負、`d > 0`)。
fn div_half_up(n: u128, d: u128) -> u128 {
    let (q, r) = (n / d, n % d);
    if 2 * r >= d { q + 1 } else { q }
}

/// Python `_round(v, step)`;四捨五入後超過 28 位數(Python 丟 `InvalidOperation`)時回 `None`。
pub fn round_half_up(n: &Number, step: Step) -> Option<Rounded> {
    let (negative, coef, exp) = decimal_parts(n);
    let p = exp + i32::try_from(step.scale).unwrap_or(0);
    let steps = if coef == 0 {
        0
    } else if p >= 0 {
        let p = p.unsigned_abs();
        // 係數 × 10^p 超過 30 位數時商(÷ coef ≤ 5)必定超過 28 位數
        if digit_count(coef) + p > PRECISION + 2 {
            return None;
        }
        div_half_up(coef * 10u128.pow(p), step.coef)
    } else {
        let s = p.unsigned_abs();
        // 係數少於 21 位數,除以 10^31 以上後小於 0.5,四捨五入為 0
        if s > 30 {
            0
        } else {
            div_half_up(coef, step.coef * 10u128.pow(s))
        }
    };
    if steps >= 10u128.pow(PRECISION) {
        return None;
    }
    Some(Rounded {
        negative,
        steps,
        step,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn num(v: serde_json::Value) -> Number {
        match v {
            serde_json::Value::Number(n) => n,
            other => panic!("不是數字:{other}"),
        }
    }

    #[test]
    fn decimal_parts_follow_python_str() {
        assert_eq!(decimal_parts(&num(json!(1.025))), (false, 1025, -3));
        assert_eq!(decimal_parts(&num(json!(1e16))), (false, 1, 16));
        assert_eq!(decimal_parts(&num(json!(-0.0))), (true, 0, 0));
        assert_eq!(decimal_parts(&num(json!(-12))), (true, 12, 0));
        assert_eq!(
            decimal_parts(&num(json!(u64::MAX))),
            (false, u128::from(u64::MAX), 0)
        );
    }

    #[test]
    fn half_up_goes_away_from_zero() {
        let r = round_half_up(&num(json!(-0.525)), SPEED_STEP).unwrap();
        assert_eq!(r.steps(), -11);
        assert_eq!(r.to_f64(), -0.55);
        let r = round_half_up(&num(json!(-0.01)), PAUSE_STEP).unwrap();
        assert_eq!(r.steps(), 0);
        assert_eq!(r.to_f64().to_bits(), (-0.0f64).to_bits());
    }

    #[test]
    fn too_many_digits_is_none() {
        // 5e26 / 0.05 = 1e28(29 位)
        assert!(round_half_up(&num(json!(5e26)), SPEED_STEP).is_none());
        assert!(round_half_up(&num(json!(4.9e26)), SPEED_STEP).is_some());
        assert!(round_half_up(&num(json!(1e300)), PAUSE_STEP).is_none());
        assert_eq!(
            round_half_up(&num(json!(1e-300)), POS_STEP).map(|r| r.steps()),
            Some(0)
        );
    }
}
