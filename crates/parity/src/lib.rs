//! Rust 遷移的並行比對工具(CHG-20261123-01 T2,代決 5~7)。
//!
//! 同一組 fixture 交給 Python 模組(經 `py/adapter.py` 轉接器,子程序 + JSON 行協定)
//! 與 Rust 對應單元(同行程,經 [`Registry`]),逐案例比對結果,產出「一致 / 不一致」報告。
//!
//! - fixture 檔:`{"unit": "<單元名>", "cases": [{"id": "<案例 id>", "input": <任意 JSON>}, …]}`,
//!   案例 id 在檔內唯一;只放合成資料。
//! - JSON 行協定:請求 `{"id", "input"}`;回應 `ok`(`value`)、`reject`(`code`)、`error`(`class`)三型。
//! - 判定:`outcome` 相同,且 `value` 以 JSON 值完全相等(整數 1 與浮點 1.0 不同)、
//!   `code` / `class` 字串相同才算一致;人看的訊息文字不比對。
//!
//! 本 crate 不使用網路;唯一的子程序是 Python 轉接器(見 [`py`])。只有 `safe_fs.*` 單元會在系統暫存目錄
//! 建立唯一名稱的工作目錄、放合成檔案樹,案例結束即刪除(見 [`units_safe_fs`];CHG-20261125-01 代決 11);
//! 不寫其他任何位置。
#![forbid(unsafe_code)]

pub mod demo;
pub mod py;
pub mod units;
pub mod units_safe_fs;

use std::collections::{BTreeMap, HashSet};
use std::fmt;
use std::panic::{AssertUnwindSafe, catch_unwind};

use serde::Deserialize;
use serde_json::{Map, Value, json};

/// 單一案例的結果(兩邊共用的三型)。
#[derive(Debug, Clone, PartialEq)]
pub enum Outcome {
    /// 正常結果;`Value` 以 JSON 值完全相等比較。
    Ok(Value),
    /// 預期內的拒絕(例:Python 的 `ValueError` 依情境對應的代碼)。
    Reject { code: String },
    /// 未預期的錯誤;Rust 單元 panic 記為類別 `panic`。
    Error { class: String },
}

impl Outcome {
    /// 報告用的一行摘要,例:`ok 3`、`reject out_of_range`、`error type`。
    pub fn summary(&self) -> String {
        match self {
            Outcome::Ok(v) => format!("ok {v}"),
            Outcome::Reject { code } => format!("reject {code}"),
            Outcome::Error { class } => format!("error {class}"),
        }
    }

    /// 協定形式的 JSON(不含 `id`)。
    pub fn to_json(&self) -> Value {
        match self {
            Outcome::Ok(v) => json!({"outcome": "ok", "value": v}),
            Outcome::Reject { code } => json!({"outcome": "reject", "code": code}),
            Outcome::Error { class } => json!({"outcome": "error", "class": class}),
        }
    }
}

/// fixture 內的一個案例;`input` 可為任意 JSON(含 `null`),但欄位必須存在。
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct Case {
    pub id: String,
    pub input: Value,
}

/// 一個 fixture 檔(一檔一個單元)。
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct Fixture {
    pub unit: String,
    pub cases: Vec<Case>,
}

/// fixture 解析錯誤(命令列對應 exit 2)。
#[derive(Debug, Clone, PartialEq)]
pub enum FixtureError {
    /// 不是 JSON、缺 `unit` / `cases` / `id` / `input`、型別不符。
    Format(String),
    /// 案例 id 在檔內重複。
    DuplicateId(String),
}

impl fmt::Display for FixtureError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            FixtureError::Format(msg) => write!(f, "fixture 格式錯誤:{msg}"),
            FixtureError::DuplicateId(id) => write!(f, "fixture 案例 id 重複:{id}"),
        }
    }
}

impl std::error::Error for FixtureError {}

impl Fixture {
    /// 解析 fixture 檔內容(容許 `\r\n`、容許多出的欄位,供日後以選用欄位擴充)。
    pub fn parse(text: &str) -> Result<Fixture, FixtureError> {
        let fixture: Fixture =
            serde_json::from_str(text).map_err(|e| FixtureError::Format(e.to_string()))?;
        let mut seen = HashSet::new();
        for case in &fixture.cases {
            if !seen.insert(case.id.as_str()) {
                return Err(FixtureError::DuplicateId(case.id.clone()));
            }
        }
        Ok(fixture)
    }
}

/// Rust 單元介面:輸入任意 JSON,回傳三型之一。
pub type UnitFn = fn(&Value) -> Outcome;

/// Rust 單元登錄表(單元名 → 函式)。
#[derive(Debug, Clone, Default)]
pub struct Registry {
    units: BTreeMap<String, UnitFn>,
}

impl Registry {
    /// 空的登錄表。
    pub fn new() -> Self {
        Self::default()
    }

    /// 內建單元:示範單元 `demo.parse_count`、`demo.parse_count_drift`;
    /// 產品模組單元 `value_checks.is_int`、`names.name_char_ok`、`names.normalize_name`、
    /// `names.fold_name`、`names.constants`(CHG-20261124-01,見 [`units`]);
    /// `safe_fs.check_name`、`safe_fs.open_dir`、`safe_fs.open_child`、`safe_fs.open_file`、`safe_fs.open_path`
    /// (CHG-20261125-01 T1)與 `safe_fs.create_excl`、`safe_fs.open_or_create`、`safe_fs.lstat`、
    /// `safe_fs.entries`、`safe_fs.unlink`、`safe_fs.rmtree`、`safe_fs.rename`、`safe_fs.replace`、`safe_fs.link`
    /// (T2;見 [`units_safe_fs`]);
    /// 環境變數 `PARITY_TEST_SLEEP=1` 時另登錄測試專用的 `demo.sleep`。
    /// 路線圖第 2 筆起,各模組的 Rust 單元在此登錄。
    pub fn builtin() -> Self {
        let mut r = Self::new();
        r.register("demo.parse_count", demo::parse_count);
        r.register("demo.parse_count_drift", demo::parse_count_drift);
        r.register("value_checks.is_int", units::value_checks_is_int);
        r.register("names.name_char_ok", units::names_name_char_ok);
        r.register("names.normalize_name", units::names_normalize_name);
        r.register("names.fold_name", units::names_fold_name);
        r.register("names.constants", units::names_constants);
        r.register("safe_fs.check_name", units_safe_fs::safe_fs_check_name);
        r.register("safe_fs.open_dir", units_safe_fs::safe_fs_open_dir);
        r.register("safe_fs.open_child", units_safe_fs::safe_fs_open_child);
        r.register("safe_fs.open_file", units_safe_fs::safe_fs_open_file);
        r.register("safe_fs.open_path", units_safe_fs::safe_fs_open_path);
        r.register("safe_fs.create_excl", units_safe_fs::safe_fs_create_excl);
        r.register(
            "safe_fs.open_or_create",
            units_safe_fs::safe_fs_open_or_create,
        );
        r.register("safe_fs.lstat", units_safe_fs::safe_fs_lstat);
        r.register("safe_fs.entries", units_safe_fs::safe_fs_entries);
        r.register("safe_fs.unlink", units_safe_fs::safe_fs_unlink);
        r.register("safe_fs.rmtree", units_safe_fs::safe_fs_rmtree);
        r.register("safe_fs.rename", units_safe_fs::safe_fs_rename);
        r.register("safe_fs.replace", units_safe_fs::safe_fs_replace);
        r.register("safe_fs.link", units_safe_fs::safe_fs_link);
        if std::env::var_os("PARITY_TEST_SLEEP").is_some_and(|v| v == "1") {
            r.register("demo.sleep", demo::sleep);
        }
        r
    }

    /// 登錄單元;同名者覆蓋。
    pub fn register(&mut self, name: &str, unit: UnitFn) {
        self.units.insert(name.to_string(), unit);
    }

    /// 已登錄的單元名(依字典序)。
    pub fn names(&self) -> Vec<&str> {
        self.units.keys().map(String::as_str).collect()
    }

    pub fn contains(&self, name: &str) -> bool {
        self.units.contains_key(name)
    }

    /// 執行單元;未登錄回 `None`。單元 panic 記為 `Error { class: "panic" }`。
    pub fn run(&self, name: &str, input: &Value) -> Option<Outcome> {
        let unit = *self.units.get(name)?;
        Some(
            catch_unwind(AssertUnwindSafe(|| unit(input))).unwrap_or_else(|_| Outcome::Error {
                class: "panic".to_string(),
            }),
        )
    }
}

/// 轉接器的一行回應。
#[derive(Debug, Clone, PartialEq)]
pub struct Response {
    pub id: String,
    pub outcome: Outcome,
}

/// 協定錯誤(命令列對應 exit 2)。
#[derive(Debug, Clone, PartialEq)]
pub enum ProtocolError {
    /// 單行不是合法回應。
    BadLine(String),
    /// 回應 id 與請求不符(依序對應)。
    IdMismatch {
        index: usize,
        expected: String,
        got: String,
    },
    /// 回應行數與請求數不同。
    CountMismatch { expected: usize, got: usize },
}

impl fmt::Display for ProtocolError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ProtocolError::BadLine(msg) => write!(f, "回應格式錯誤:{msg}"),
            ProtocolError::IdMismatch {
                index,
                expected,
                got,
            } => write!(
                f,
                "第 {} 筆回應的 id 與請求不符:預期 {expected:?},收到 {got:?}",
                index + 1
            ),
            ProtocolError::CountMismatch { expected, got } => {
                write!(f, "回應筆數與請求不符:預期 {expected} 筆,收到 {got} 筆")
            }
        }
    }
}

impl std::error::Error for ProtocolError {}

/// 編碼一個請求為單行 JSON(不含換行;字串內的換行以 `\n` 跳脫)。
pub fn encode_request(case: &Case) -> String {
    json!({"id": case.id, "input": case.input}).to_string()
}

fn string_field(obj: &Map<String, Value>, key: &str, line: &str) -> Result<String, ProtocolError> {
    match obj.get(key) {
        Some(Value::String(s)) => Ok(s.clone()),
        Some(_) => Err(ProtocolError::BadLine(format!("{key} 須為字串:{line}"))),
        None => Err(ProtocolError::BadLine(format!("缺少 {key}:{line}"))),
    }
}

/// 解析一行回應(容許 `\r\n` / `\r` 行尾;多出的欄位如 `message` 忽略)。
pub fn parse_response(line: &str) -> Result<Response, ProtocolError> {
    let line = line.trim_end_matches(['\r', '\n']);
    let value: Value = serde_json::from_str(line)
        .map_err(|e| ProtocolError::BadLine(format!("不是 JSON({e}):{line}")))?;
    let Value::Object(obj) = value else {
        return Err(ProtocolError::BadLine(format!("不是 JSON 物件:{line}")));
    };
    let id = string_field(&obj, "id", line)?;
    let outcome = match string_field(&obj, "outcome", line)?.as_str() {
        "ok" => Outcome::Ok(
            obj.get("value")
                .cloned()
                .ok_or_else(|| ProtocolError::BadLine(format!("缺少 value:{line}")))?,
        ),
        "reject" => Outcome::Reject {
            code: string_field(&obj, "code", line)?,
        },
        "error" => Outcome::Error {
            class: string_field(&obj, "class", line)?,
        },
        other => {
            return Err(ProtocolError::BadLine(format!(
                "未知的 outcome {other:?}:{line}"
            )));
        }
    };
    Ok(Response { id, outcome })
}

/// 檢查回應依序對應請求(筆數相同、id 逐一相同),取出結果。
pub fn match_responses(
    cases: &[Case],
    responses: Vec<Response>,
) -> Result<Vec<Outcome>, ProtocolError> {
    if responses.len() != cases.len() {
        return Err(ProtocolError::CountMismatch {
            expected: cases.len(),
            got: responses.len(),
        });
    }
    cases
        .iter()
        .zip(responses)
        .enumerate()
        .map(|(index, (case, resp))| {
            if resp.id == case.id {
                Ok(resp.outcome)
            } else {
                Err(ProtocolError::IdMismatch {
                    index,
                    expected: case.id.clone(),
                    got: resp.id,
                })
            }
        })
        .collect()
}

/// 一個不一致案例。
#[derive(Debug, Clone, PartialEq)]
pub struct Mismatch {
    pub id: String,
    pub python: Outcome,
    pub rust: Outcome,
}

/// 比對報告。
#[derive(Debug, Clone, PartialEq)]
pub struct Report {
    pub unit: String,
    /// fixture 路徑(顯示用,由呼叫端填入)。
    pub fixture: String,
    pub total: usize,
    pub matched: usize,
    pub mismatches: Vec<Mismatch>,
}

/// 逐案例比對;`rust`、`python` 須與 `fixture.cases` 等長且同序(呼叫端保證,不符即 panic)。
pub fn compare(fixture: &Fixture, rust: &[Outcome], python: &[Outcome]) -> Report {
    assert_eq!(rust.len(), fixture.cases.len(), "Rust 結果筆數與案例數不同");
    assert_eq!(
        python.len(),
        fixture.cases.len(),
        "Python 結果筆數與案例數不同"
    );
    let mismatches: Vec<Mismatch> = fixture
        .cases
        .iter()
        .zip(rust.iter().zip(python))
        .filter(|(_, (r, p))| r != p)
        .map(|(case, (r, p))| Mismatch {
            id: case.id.clone(),
            python: p.clone(),
            rust: r.clone(),
        })
        .collect();
    Report {
        unit: fixture.unit.clone(),
        fixture: String::new(),
        total: fixture.cases.len(),
        matched: fixture.cases.len() - mismatches.len(),
        mismatches,
    }
}

impl Report {
    pub fn is_consistent(&self) -> bool {
        self.mismatches.is_empty()
    }

    /// 結論字串:「一致」或「不一致」。
    pub fn conclusion(&self) -> &'static str {
        if self.is_consistent() {
            "一致"
        } else {
            "不一致"
        }
    }

    /// 文字報告;最後一行固定為「結論:一致」或「結論:不一致」(結尾有換行)。
    pub fn to_text(&self) -> String {
        let mut s = format!(
            "單元:{}\nfixture:{}\n案例數:{}\n一致數:{}\n不一致數:{}\n",
            self.unit,
            self.fixture,
            self.total,
            self.matched,
            self.mismatches.len()
        );
        if !self.mismatches.is_empty() {
            s.push_str("不一致案例:\n");
            for m in &self.mismatches {
                s.push_str(&format!(
                    "  - {}:Python {};Rust {}\n",
                    m.id,
                    m.python.summary(),
                    m.rust.summary()
                ));
            }
        }
        s.push_str(&format!("結論:{}\n", self.conclusion()));
        s
    }

    /// JSON 報告(與文字報告同內容)。
    pub fn to_json(&self) -> Value {
        json!({
            "unit": self.unit,
            "fixture": self.fixture,
            "cases": self.total,
            "matched": self.matched,
            "mismatched": self.mismatches.len(),
            "mismatches": self.mismatches.iter().map(|m| json!({
                "id": m.id,
                "python": m.python.to_json(),
                "rust": m.rust.to_json(),
            })).collect::<Vec<_>>(),
            "conclusion": self.conclusion(),
        })
    }
}

#[cfg(test)]
mod tests {
    //! L1:比對器判定規則。
    use super::*;

    fn fixture(ids: &[&str]) -> Fixture {
        Fixture {
            unit: "t.unit".to_string(),
            cases: ids
                .iter()
                .map(|id| Case {
                    id: id.to_string(),
                    input: Value::Null,
                })
                .collect(),
        }
    }

    fn reject(code: &str) -> Outcome {
        Outcome::Reject {
            code: code.to_string(),
        }
    }

    fn error(class: &str) -> Outcome {
        Outcome::Error {
            class: class.to_string(),
        }
    }

    fn single(r: Outcome, p: Outcome) -> Report {
        compare(&fixture(&["a"]), &[r], &[p])
    }

    #[test]
    fn same_results_are_consistent() {
        let f = fixture(&["a", "b", "c"]);
        let res = [Outcome::Ok(json!({"x": [1, "y"]})), reject("r"), error("e")];
        let report = compare(&f, &res, &res);
        assert!(report.is_consistent());
        assert_eq!((report.total, report.matched), (3, 3));
        assert!(report.to_text().ends_with("結論:一致\n"));
    }

    #[test]
    fn different_outcome_is_mismatch() {
        assert!(!single(Outcome::Ok(json!(1)), reject("x")).is_consistent());
        assert!(!single(reject("x"), error("x")).is_consistent());
    }

    #[test]
    fn different_value_is_mismatch() {
        assert!(!single(Outcome::Ok(json!(1)), Outcome::Ok(json!(2))).is_consistent());
        assert!(!single(Outcome::Ok(json!("1")), Outcome::Ok(json!(1))).is_consistent());
    }

    #[test]
    fn integer_and_float_differ() {
        assert!(!single(Outcome::Ok(json!(1)), Outcome::Ok(json!(1.0))).is_consistent());
        // 從協定解析而來的值也一樣
        let int: Value = serde_json::from_str("1").unwrap();
        let float: Value = serde_json::from_str("1.0").unwrap();
        assert!(!single(Outcome::Ok(int), Outcome::Ok(float)).is_consistent());
    }

    #[test]
    fn object_key_order_does_not_matter() {
        let a: Value = serde_json::from_str(r#"{"a": 1, "b": 2}"#).unwrap();
        let b: Value = serde_json::from_str(r#"{"b": 2, "a": 1}"#).unwrap();
        assert!(single(Outcome::Ok(a), Outcome::Ok(b)).is_consistent());
    }

    #[test]
    fn different_code_or_class_is_mismatch() {
        assert!(!single(reject("a"), reject("b")).is_consistent());
        assert!(!single(error("a"), error("b")).is_consistent());
    }

    #[test]
    fn message_text_does_not_matter() {
        let p =
            parse_response(r#"{"id": "a", "outcome": "error", "class": "type", "message": "甲"}"#)
                .unwrap();
        assert!(single(error("type"), p.outcome).is_consistent());
    }

    #[test]
    fn report_lists_mismatch_with_both_sides() {
        let f = fixture(&["a", "edge"]);
        let mut report = compare(
            &f,
            &[Outcome::Ok(json!(1)), reject("out_of_range")],
            &[Outcome::Ok(json!(1)), Outcome::Ok(json!(1000))],
        );
        report.fixture = "x.json".to_string();
        assert_eq!(report.matched, 1);
        let text = report.to_text();
        assert!(
            text.contains("  - edge:Python ok 1000;Rust reject out_of_range\n"),
            "{text}"
        );
        assert_eq!(text.lines().last(), Some("結論:不一致"));
        let j = report.to_json();
        assert_eq!(j["mismatched"], 1);
        assert_eq!(j["conclusion"], "不一致");
    }
}
