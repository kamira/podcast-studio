//! Python 格式的 JSON 寫出器(CHG-20261128-01 代決 6)。
//!
//! - [`dump`]:與 Python `json.dumps(v, ensure_ascii=False, indent=1) + "\n"` 逐位元組相同
//!   (= `project._dump` 解碼後的字串;[`dump_bytes`] 為其 UTF-8 位元組)——縮排 1 個空白、項目分隔 `,` + 換行、
//!   鍵值分隔 `: `、空陣列 `[]`、空物件 `{}`。
//! - [`dumps_sorted`]:與 Python `json.dumps(v, ensure_ascii=False, sort_keys=True)` 相同(`post_key` 的寫法)——
//!   分隔 `, ` 與 `: `,物件鍵依碼位排序。
//! - 字串跳脫(兩者相同):`"` → `\"`、`\` → `\\`、`\b` `\f` `\n` `\r` `\t` 短跳脫、其他 U+0000~U+001F 為
//!   小寫十六進位的 `\u00XX`;其餘字元(含 U+007F、U+2028、非 ASCII)原樣輸出——`serde_json` 的跳脫規則與
//!   Python `ensure_ascii=False` 相同(`tests/pyjson.rs` 鎖住)。
//! - 浮點一律以 [`float_repr`](Python `float.__repr__`)寫出;整數照原值。
//!
//! 物件鍵的順序:[`dump`] 依呼叫端型別的序列化順序(結構依欄位宣告順序、[`Entries`] 依陣列順序、
//! `serde_json::Value` 的物件依碼位排序——本 workspace 不開 `preserve_order`),Python 則依 dict 的建構順序;
//! 呼叫端以型別欄位順序對齊 Python 的建構順序(代決 6)。[`dumps_sorted`] 一律先轉成 `Value` 再寫出,鍵必定排序。

use std::io;

use serde::Serialize;
use serde::ser::SerializeMap;
use serde_json::ser::{Formatter, PrettyFormatter, Serializer};

/// 依陣列順序寫出的物件(Python 依建構順序寫出的 dict)。
#[derive(Debug, Clone, Copy)]
pub struct Entries<'a, V>(pub &'a [(&'a str, V)]);

impl<V: Serialize> Serialize for Entries<'_, V> {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut map = serializer.serialize_map(Some(self.0.len()))?;
        for (k, v) in self.0 {
            map.serialize_entry(k, v)?;
        }
        map.end()
    }
}

/// 科學記號字串(`1.2345e-7`、`1e16`)→(有效數字, 第一位數字的十進位指數)。
fn split_sci(sci: &str) -> (String, i32) {
    let (mantissa, exp) = sci.split_once('e').unwrap_or((sci, "0"));
    let exp: i32 = exp.parse().unwrap_or(0);
    (mantissa.chars().filter(|c| *c != '.').collect(), exp)
}

/// 「數字 + 指數」組回可解析的字串(`d.ddd e exp`)。
fn join_sci(digits: &str, exp: i32) -> String {
    let (first, rest) = digits.split_at(1.min(digits.len()));
    format!("{first}.{rest}0e{exp}")
}

/// 非負有限浮點的 Python `repr` 有效數字與第一位的十進位指數(`0.0` 為 `("0", 0)`)。
///
/// 數字為最短往返表示,長度相同的候選有兩個時取離真值較近者——與 Python(David Gay `dtoa` mode 0)相同。
/// Rust 的 `{:e}` 也是最短往返表示,但真值**正好落在兩個候選中間**時取較大者,Python 則取末位為偶數者
/// (例:真值 `178441013631068.125` 的 repr 是 `178441013631068.12`,Rust `{:e}` 為 `…068.13`)。
/// 這種情形只會出現在 16 位以上的有效數字(浮點間距大於十進位末位),此時以精確的十進位展開確認是否正好在中間,
/// 是就改用末位減一(偶數)且能往返的候選。
pub(crate) fn repr_digits(x: f64) -> (String, i32) {
    let (digits, exp) = split_sci(&format!("{x:e}"));
    let last_odd = digits.bytes().last().is_some_and(|b| (b - b'0') % 2 == 1);
    if digits.len() < 15 || !last_odd {
        return (digits, exp);
    }
    // 精確展開:f64 的十進位展開最多約 770 位有效數字,精度 800 必定精確(其後為 0)
    let (exact, exact_exp) = split_sci(&format!("{x:.800e}"));
    let exact = exact.trim_end_matches('0');
    let mut lower = digits.clone().into_bytes();
    if let Some(b) = lower.last_mut() {
        *b -= 1;
    }
    let lower = String::from_utf8(lower).unwrap_or_default();
    let is_tie = exact_exp == exp
        && exact.len() == digits.len() + 1
        && exact.starts_with(lower.as_str())
        && exact.ends_with('5');
    if is_tie && join_sci(&lower, exp).parse::<f64>().ok() == Some(x) {
        return (lower.trim_end_matches('0').to_string(), exp);
    }
    (digits, exp)
}

/// Python `repr(float)`:最短往返表示的數字(見 [`repr_digits`]);十進位指數 < −4 或 ≥ 16 時用科學記號
/// (`1e+16`、`1.5e-07`,指數帶正負號且至少兩位),否則用小數(整數值補 `.0`);`inf`、`-inf`、`nan`。
pub fn float_repr(v: f64) -> String {
    if v.is_nan() {
        return "nan".to_string();
    }
    if v.is_infinite() {
        return if v > 0.0 { "inf" } else { "-inf" }.to_string();
    }
    let sign = if v.is_sign_negative() { "-" } else { "" };
    let (digits, exp) = repr_digits(v.abs());
    let body = if !(-4..16).contains(&exp) {
        let (first, rest) = digits.split_at(1);
        let mantissa = if rest.is_empty() {
            first.to_string()
        } else {
            format!("{first}.{rest}")
        };
        let exp_sign = if exp < 0 { '-' } else { '+' };
        format!("{mantissa}e{exp_sign}{:02}", exp.unsigned_abs())
    } else if exp >= 0 {
        let int_len = exp.unsigned_abs() as usize + 1;
        if digits.len() <= int_len {
            format!("{digits}{}.0", "0".repeat(int_len - digits.len()))
        } else {
            let (int_part, frac) = digits.split_at(int_len);
            format!("{int_part}.{frac}")
        }
    } else {
        let zeros = exp.unsigned_abs() as usize - 1;
        format!("0.{}{digits}", "0".repeat(zeros))
    };
    format!("{sign}{body}")
}

/// `indent=1` 的寫法:結構交給 `PrettyFormatter`,浮點改用 [`float_repr`]。
struct PyPretty<'a> {
    inner: PrettyFormatter<'a>,
}

impl Formatter for PyPretty<'_> {
    fn write_f32<W: ?Sized + io::Write>(&mut self, w: &mut W, value: f32) -> io::Result<()> {
        w.write_all(float_repr(f64::from(value)).as_bytes())
    }

    fn write_f64<W: ?Sized + io::Write>(&mut self, w: &mut W, value: f64) -> io::Result<()> {
        w.write_all(float_repr(value).as_bytes())
    }

    fn begin_array<W: ?Sized + io::Write>(&mut self, w: &mut W) -> io::Result<()> {
        self.inner.begin_array(w)
    }

    fn end_array<W: ?Sized + io::Write>(&mut self, w: &mut W) -> io::Result<()> {
        self.inner.end_array(w)
    }

    fn begin_array_value<W: ?Sized + io::Write>(
        &mut self,
        w: &mut W,
        first: bool,
    ) -> io::Result<()> {
        self.inner.begin_array_value(w, first)
    }

    fn end_array_value<W: ?Sized + io::Write>(&mut self, w: &mut W) -> io::Result<()> {
        self.inner.end_array_value(w)
    }

    fn begin_object<W: ?Sized + io::Write>(&mut self, w: &mut W) -> io::Result<()> {
        self.inner.begin_object(w)
    }

    fn end_object<W: ?Sized + io::Write>(&mut self, w: &mut W) -> io::Result<()> {
        self.inner.end_object(w)
    }

    fn begin_object_key<W: ?Sized + io::Write>(
        &mut self,
        w: &mut W,
        first: bool,
    ) -> io::Result<()> {
        self.inner.begin_object_key(w, first)
    }

    fn end_object_key<W: ?Sized + io::Write>(&mut self, w: &mut W) -> io::Result<()> {
        self.inner.end_object_key(w)
    }

    fn begin_object_value<W: ?Sized + io::Write>(&mut self, w: &mut W) -> io::Result<()> {
        self.inner.begin_object_value(w)
    }

    fn end_object_value<W: ?Sized + io::Write>(&mut self, w: &mut W) -> io::Result<()> {
        self.inner.end_object_value(w)
    }
}

/// 預設分隔(`, `、`: `)的緊湊寫法,浮點用 [`float_repr`]。
struct PyCompact;

impl Formatter for PyCompact {
    fn write_f32<W: ?Sized + io::Write>(&mut self, w: &mut W, value: f32) -> io::Result<()> {
        w.write_all(float_repr(f64::from(value)).as_bytes())
    }

    fn write_f64<W: ?Sized + io::Write>(&mut self, w: &mut W, value: f64) -> io::Result<()> {
        w.write_all(float_repr(value).as_bytes())
    }

    fn begin_array_value<W: ?Sized + io::Write>(
        &mut self,
        w: &mut W,
        first: bool,
    ) -> io::Result<()> {
        if first { Ok(()) } else { w.write_all(b", ") }
    }

    fn begin_object_key<W: ?Sized + io::Write>(
        &mut self,
        w: &mut W,
        first: bool,
    ) -> io::Result<()> {
        if first { Ok(()) } else { w.write_all(b", ") }
    }

    fn begin_object_value<W: ?Sized + io::Write>(&mut self, w: &mut W) -> io::Result<()> {
        w.write_all(b": ")
    }
}

fn write_with<T: Serialize + ?Sized, F: Formatter>(value: &T, formatter: F) -> Vec<u8> {
    let mut out = Vec::new();
    let mut ser = Serializer::with_formatter(&mut out, formatter);
    // 寫入 Vec 不會有 I/O 錯誤;序列化錯誤只會來自產生非字串物件鍵的 Serialize 實作
    // (本 crate 的型別與 serde_json::Value 都只產生字串鍵),屬程式錯誤,不是輸入資料造成的。
    value
        .serialize(&mut ser)
        .expect("JSON 寫出失敗:物件鍵必須是字串");
    out
}

/// Python `json.dumps(v, ensure_ascii=False, indent=1) + "\n"` 的 UTF-8 位元組(= `project._dump`)。
pub fn dump_bytes<T: Serialize + ?Sized>(value: &T) -> Vec<u8> {
    let mut out = write_with(
        value,
        PyPretty {
            inner: PrettyFormatter::with_indent(b" "),
        },
    );
    out.push(b'\n');
    out
}

/// [`dump_bytes`] 的字串形式。
pub fn dump<T: Serialize + ?Sized>(value: &T) -> String {
    String::from_utf8(dump_bytes(value)).expect("serde_json 只寫出 UTF-8")
}

/// Python `json.dumps(v, ensure_ascii=False, sort_keys=True)`(`post_key` 的寫法;結尾不加換行)。
pub fn dumps_sorted<T: Serialize + ?Sized>(value: &T) -> String {
    let sorted = serde_json::to_value(value).expect("JSON 轉換失敗:物件鍵必須是字串");
    String::from_utf8(write_with(&sorted, PyCompact)).expect("serde_json 只寫出 UTF-8")
}
