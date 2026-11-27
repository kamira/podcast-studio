//! `project.*` 的 Rust 比對單元(CHG-20261128-01 代決 10);Python 端對應單元在 `py/adapter.py`
//! (Python 端只在單元函式內延後 `import project`,Windows 上無法載入,三平台以預期結果表
//! `tests/data/project_expected.json` 斷言 Rust,見 `tests/project_parity.rs`)。
//!
//! 結果一律以 `ok` 回傳,錯誤訊息清單因此逐字比對(代決 9):接受時 `{"value", "repr"?}`——`repr` 為
//! `post_key` 用的緊湊、鍵排序寫法(Python `json.dumps(值, ensure_ascii=False, sort_keys=True)`),
//! 連 `-0.0` 與整數 / 浮點的寫法一起比對;拒絕時 `{"class", "errors"}`。輸入格式不符 → `error type`。
//!
//! | 單元 | 輸入 | 接受時的值 |
//! |------|------|------------|
//! | `project.constants` | 忽略 | 常數物件(集合為排序後字串、`Decimal` 為字串、`whitespace` 為 `str.isspace` 為真的全部碼位) |
//! | `project.normalize_speed` | `{"v", "where"?}`(未給 `where` 用「語速」) | `{"value": 浮點, "repr"}` |
//! | `project.normalize_pause` | `{"v", "where"?}`(未給 `where` 用「停頓」) | `{"value": 浮點, "repr"}` |
//! | `project.check_zhuyin` | 任意 JSON | `{"value": 字串}` |
//! | `project.check_pitch_points` | `{"points", "where"}` | `{"value": [[位置, 半音], …], "repr"}` |
//! | `project.py_json` | 任意 JSON 值 | `{"dump": _dump 字串, "sorted": 緊湊排序字串}` |
//! | `project.validate_text` | JSON 原文字串(以 UTF-8 編碼)、`{"hex": 位元組的十六進位}` 或 `{"parts": [片段…]}` | `{"dump": _dump(validate(…)) 字串或 {"parts"}}` |
//!
//! `project.validate_text`(T2):兩邊各自解析原文——Rust 以 [`project::validate::parse_project_json`]
//! (= Python `_read_project_file` 讀到位元組之後的部分:5 MB 上限、UTF-8、JSON、須為物件),Python 端把位元組寫進
//! 自己建立的暫存目錄後呼叫真的 `_read_project_file`——所以 JSON 層的差異也測得到;接著 `validate` 與 `_dump`。
//!
//! 精簡形式(DIR-6 第 1 輪 F-1):超長的重複內容不直接寫進 fixture 與預期結果表。輸入 `{"parts": [片段…]}` 的片段為
//! 字串或 `{"repeat": 非空字串, "count": 1~5242881}`,依序串接後以 UTF-8 編碼([`parts_text`]);接受時的 dump 有同一
//! 字元連續 1000 個以上時改回 `{"dump": {"parts": […]}}`([`compact_dump`],以 [`parts_text`] 展開即得原字串)。
//! 轉接器的 `_project_parts_text`、`_project_compact_dump` 是同一規則。
//!
//! `project.py_json` 的物件鍵:fixture 經 `serde_json` 解析後鍵已依碼位排序,送給 Python 的請求也是排序後的鍵,
//! 兩邊收到同一個鍵順序(依建構順序寫出由 `crates/project/tests/pyjson.rs` 的 `Entries` 與結構測試鎖住)。

use project::pyjson::{self, Entries};
use project::{Error, checks, constants, pystr, validate};
use serde_json::{Map, Value, json};

use crate::Outcome;
use crate::units::ERROR_TYPE;

fn type_error() -> Outcome {
    Outcome::Error {
        class: ERROR_TYPE.to_string(),
    }
}

fn error_result(e: &Error) -> Outcome {
    Outcome::Ok(json!({"class": e.class(), "errors": e.errors()}))
}

fn value_result(value: Value) -> Outcome {
    let repr = pyjson::dumps_sorted(&value);
    Outcome::Ok(json!({"value": value, "repr": repr}))
}

fn pairs_object<V: Clone + Into<Value>>(pairs: &[(&str, V)]) -> Value {
    Value::Object(
        pairs
            .iter()
            .map(|(k, v)| ((*k).to_string(), v.clone().into()))
            .collect::<Map<String, Value>>(),
    )
}

fn sorted_chars(s: &str) -> String {
    let mut chars: Vec<char> = s.chars().collect();
    chars.sort_unstable();
    chars.into_iter().collect()
}

/// `project.constants`:忽略輸入,回 `project.py` 的常數(與轉接器的 `project_constants` 同一組鍵)。
pub fn project_constants(_input: &Value) -> Outcome {
    use constants as c;
    let asset_exts: Map<String, Value> = c::ASSET_EXTS
        .iter()
        .map(|(k, exts)| ((*k).to_string(), json!(exts)))
        .collect();
    let whitespace: Vec<u32> = (0..=0x10FFFFu32)
        .filter_map(char::from_u32)
        .filter(|&ch| pystr::py_isspace(ch))
        .map(u32::from)
        .collect();
    let hanzi: Vec<Value> = c::HANZI_RANGES
        .iter()
        .map(|&(lo, hi)| json!([lo, hi]))
        .collect();
    let entries: Vec<(&str, Value)> = vec![
        ("schema_version", json!(c::SCHEMA_VERSION)),
        ("old_schema_versions", json!(c::OLD_SCHEMA_VERSIONS)),
        ("projects_name", json!(c::PROJECTS_NAME)),
        ("trash_name", json!(c::TRASH_NAME)),
        ("project_file", json!(c::PROJECT_FILE)),
        ("lock_file", json!(c::LOCK_FILE)),
        ("history_keep", json!(c::HISTORY_KEEP)),
        ("trash_days", json!(c::TRASH_DAYS)),
        (
            "speed",
            json!([c::SPEED_MIN_TEXT, c::SPEED_MAX_TEXT, c::SPEED_STEP_TEXT]),
        ),
        (
            "pause",
            json!([c::PAUSE_MIN_TEXT, c::PAUSE_MAX_TEXT, c::PAUSE_STEP_TEXT]),
        ),
        (
            "semi",
            json!([c::SEMI_MIN_TEXT, c::SEMI_MAX_TEXT, c::SEMI_STEP_TEXT]),
        ),
        ("pos_step", json!(c::POS_STEP_TEXT)),
        ("defaults", json!(pairs_object(&c::DEFAULTS))),
        ("defaults_dump", json!(pyjson::dump(&Entries(&c::DEFAULTS)))),
        ("break_pause", json!(pairs_object(&c::BREAK_PAUSE))),
        ("breaks", json!(c::BREAKS)),
        ("max_script", json!(c::MAX_SCRIPT)),
        ("max_segment_text", json!(c::MAX_SEGMENT_TEXT)),
        ("max_segments", json!(c::MAX_SEGMENTS)),
        ("max_project_bytes", json!(c::MAX_PROJECT_BYTES)),
        ("max_assets", json!(c::MAX_ASSETS)),
        ("max_asset_bytes", json!(c::MAX_ASSET_BYTES)),
        ("max_audio_seconds", json!(c::MAX_AUDIO_SECONDS)),
        ("max_label", json!(c::MAX_LABEL)),
        ("zhuyin_max", json!(c::ZHUYIN_MAX)),
        (
            "prosody_defaults",
            json!(pairs_object(&c::PROSODY_DEFAULTS)),
        ),
        (
            "prosody_defaults_dump",
            json!(pyjson::dump(&Entries(&c::PROSODY_DEFAULTS))),
        ),
        (
            "pitch_points",
            json!([c::PITCH_POINTS_MIN, c::PITCH_POINTS_MAX]),
        ),
        ("inner_points_max", json!(c::INNER_POINTS_MAX)),
        ("inner_semi_limit", json!(c::INNER_SEMI_LIMIT)),
        ("max_word_clarity", json!(c::MAX_WORD_CLARITY)),
        ("max_fusions", json!(c::MAX_FUSIONS)),
        ("fusion", json!([c::FUSION_MIN, c::FUSION_MAX])),
        ("max_fusion_off", json!(c::MAX_FUSION_OFF)),
        ("fusion_display_max", json!(c::FUSION_DISPLAY_MAX)),
        ("fusion_sources", json!(c::FUSION_SOURCES)),
        ("post_version", json!(c::POST_VERSION)),
        ("post_algo", json!(pairs_object(&c::POST_ALGO))),
        ("trim_params", json!(pairs_object(&c::TRIM_PARAMS))),
        (
            "trim_params_dump",
            json!(pyjson::dump(&Entries(&c::TRIM_PARAMS))),
        ),
        ("asset_exts", json!(asset_exts)),
        ("zhuyin_chars", json!(sorted_chars(c::ZHUYIN_CHARS))),
        ("sentence_enders", json!(sorted_chars(c::SENTENCE_ENDERS))),
        ("closers", json!(sorted_chars(c::CLOSERS))),
        ("newlines", json!(sorted_chars(c::NEWLINES))),
        ("hanzi_ranges", json!(hanzi)),
        ("name_hint", json!(c::NAME_HINT)),
        ("link_tries", json!(c::LINK_TRIES)),
        ("top_keys", json!(c::TOP_KEYS)),
        ("segment_keys_v1", json!(c::SEGMENT_KEYS_V1)),
        ("segment_keys", json!(c::SEGMENT_KEYS)),
        ("post_keys", json!(c::POST_KEYS)),
        ("post_optional", json!(c::POST_OPTIONAL)),
        ("image_keys", json!(c::IMAGE_KEYS)),
        ("asset_keys", json!(c::ASSET_KEYS)),
        ("whitespace", json!(whitespace)),
    ];
    Outcome::Ok(Value::Object(
        entries
            .into_iter()
            .map(|(k, v)| (k.to_string(), v))
            .collect(),
    ))
}

/// `{"v": 任意 JSON, "where"?: 字串}`;格式不符回 `None`(→ `error type`)。
fn number_input<'a>(input: &'a Value, default: &'a str) -> Option<(&'a Value, &'a str)> {
    let obj = input.as_object()?;
    let v = obj.get("v")?;
    let label = match obj.get("where") {
        None | Some(Value::Null) => default,
        Some(Value::String(s)) => s.as_str(),
        Some(_) => return None,
    };
    Some((v, label))
}

fn normalize(input: &Value, default: &str, f: fn(&Value, &str) -> project::Result<f64>) -> Outcome {
    let Some((v, label)) = number_input(input, default) else {
        return type_error();
    };
    match f(v, label) {
        Ok(x) => value_result(json!(x)),
        Err(e) => error_result(&e),
    }
}

/// `project.normalize_speed`。
pub fn project_normalize_speed(input: &Value) -> Outcome {
    normalize(input, "語速", checks::normalize_speed)
}

/// `project.normalize_pause`。
pub fn project_normalize_pause(input: &Value) -> Outcome {
    normalize(input, "停頓", checks::normalize_pause)
}

/// `project.check_zhuyin`。
pub fn project_check_zhuyin(input: &Value) -> Outcome {
    match checks::check_zhuyin(input) {
        Ok(z) => Outcome::Ok(json!({"value": z})),
        Err(e) => error_result(&e),
    }
}

/// `project.check_pitch_points`。
pub fn project_check_pitch_points(input: &Value) -> Outcome {
    let Some(obj) = input.as_object() else {
        return type_error();
    };
    let (Some(points), Some(Value::String(label))) = (obj.get("points"), obj.get("where")) else {
        return type_error();
    };
    match checks::check_pitch_points(points, label) {
        Ok(out) => value_result(json!(out)),
        Err(e) => error_result(&e),
    }
}

/// `project.py_json`。
pub fn project_py_json(input: &Value) -> Outcome {
    Outcome::Ok(json!({"dump": pyjson::dump(input), "sorted": pyjson::dumps_sorted(input)}))
}

/// `{"repeat", "count"}` 的 `count` 上限(5 MB + 1)。
pub const REPEAT_MAX: u64 = 5_242_881;
/// dump 中同一字元連續達此長度才改用 `parts`。
pub const RUN_MIN: usize = 1000;

/// `{"parts": [片段…]}` 的片段陣列展開成字串;空陣列、未知鍵、布林、0、超過上限等回 `None`(→ `error type`)。
pub fn parts_text(parts: &Value) -> Option<String> {
    let list = parts.as_array().filter(|l| !l.is_empty())?;
    let mut out = String::new();
    for part in list {
        match part {
            Value::String(s) => out.push_str(s),
            Value::Object(obj) if obj.len() == 2 => {
                let rep = obj.get("repeat")?.as_str().filter(|r| !r.is_empty())?;
                let count = obj.get("count")?;
                let n = count
                    .as_u64()
                    .filter(|n| count.is_u64() && (1..=REPEAT_MAX).contains(n))?;
                out.push_str(&rep.repeat(usize::try_from(n).ok()?));
            }
            _ => return None,
        }
    }
    Some(out)
}

/// 接受時的 dump:沒有同一字元連續 [`RUN_MIN`] 個以上時為字串;否則為 `{"parts": […]}`——每一段最長的同字元連續
/// (≥ [`RUN_MIN`])寫成 `{"repeat": 字元, "count": 長度}`,其餘相鄰字元合併為字串段。[`parts_text`] 展開即得原字串。
pub fn compact_dump(text: &str) -> Value {
    let chars: Vec<char> = text.chars().collect();
    let mut parts: Vec<Value> = Vec::new();
    let mut buf = String::new();
    let mut i = 0;
    while i < chars.len() {
        let mut j = i;
        while j < chars.len() && chars[j] == chars[i] {
            j += 1;
        }
        if j - i >= RUN_MIN {
            if !buf.is_empty() {
                parts.push(Value::String(std::mem::take(&mut buf)));
            }
            parts.push(json!({"repeat": chars[i].to_string(), "count": j - i}));
        } else {
            buf.extend(&chars[i..j]);
        }
        i = j;
    }
    if !parts.iter().any(Value::is_object) {
        return Value::String(text.to_string());
    }
    if !buf.is_empty() {
        parts.push(Value::String(buf));
    }
    json!({ "parts": parts })
}

/// `project.validate_text` 的輸入位元組;格式不符回 `None`(→ `error type`)。
fn text_bytes(input: &Value) -> Option<Vec<u8>> {
    match input {
        Value::String(s) => Some(s.as_bytes().to_vec()),
        Value::Object(obj) if obj.len() == 1 && obj.contains_key("parts") => {
            parts_text(&obj["parts"]).map(String::into_bytes)
        }
        Value::Object(obj) if obj.len() == 1 => {
            let hex = obj.get("hex")?.as_str()?;
            if hex.len() % 2 != 0 || !hex.is_ascii() {
                return None;
            }
            (0..hex.len())
                .step_by(2)
                .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).ok())
                .collect()
        }
        _ => None,
    }
}

/// `project.validate_text`。
pub fn project_validate_text(input: &Value) -> Outcome {
    let Some(raw) = text_bytes(input) else {
        return type_error();
    };
    match validate::parse_project_json(&raw).and_then(|data| validate::dump_validated(&data)) {
        Ok(bytes) => match String::from_utf8(bytes) {
            Ok(dump) => Outcome::Ok(json!({ "dump": compact_dump(&dump) })),
            Err(_) => type_error(),
        },
        Err(e) => error_result(&e),
    }
}

#[cfg(test)]
mod tests {
    //! L1:輸入格式與結果轉換。
    use super::*;

    #[test]
    fn number_units_reject_malformed_input_as_type_error() {
        for input in [
            json!(1),
            json!({}),
            json!({"where": "x"}),
            json!({"v": 1, "where": 5}),
        ] {
            assert_eq!(project_normalize_speed(&input), type_error(), "{input}");
            assert_eq!(project_normalize_pause(&input), type_error(), "{input}");
        }
    }

    #[test]
    fn pitch_unit_requires_points_and_string_where() {
        for input in [
            json!(null),
            json!({"points": []}),
            json!({"points": [], "where": 1}),
            json!({"where": "w"}),
        ] {
            assert_eq!(project_check_pitch_points(&input), type_error(), "{input}");
        }
    }

    #[test]
    fn validate_text_input_forms() {
        for input in [
            json!(1),
            json!(null),
            json!({"hex": "0"}),
            json!({"hex": "zz"}),
            json!({"hex": "00", "x": 1}),
            json!({"text": "{}"}),
        ] {
            assert_eq!(project_validate_text(&input), type_error(), "{input}");
        }
        assert_eq!(
            project_validate_text(&json!("[]")),
            Outcome::Ok(json!({"class": "project_error", "errors": ["專案檔必須是 JSON 物件"]}))
        );
        assert_eq!(
            project_validate_text(&json!({"hex": "ff"})),
            Outcome::Ok(json!({"class": "project_error", "errors": ["專案檔內容損壞,無法讀取"]}))
        );
    }

    #[test]
    fn parts_input_valid_and_invalid() {
        assert_eq!(
            parts_text(&json!(["ab", {"repeat": "字x", "count": 2}, ""])),
            Some("ab字x字x".to_string())
        );
        assert_eq!(
            parts_text(&json!([{"repeat": "a", "count": REPEAT_MAX}])).map(|s| s.len()),
            Some(5_242_881)
        );
        for bad in [
            json!([]),
            json!("x"),
            json!([1]),
            json!([null]),
            json!([{"repeat": "a"}]),
            json!([{"repeat": "a", "count": 1, "x": 1}]),
            json!([{"repeat": "", "count": 1}]),
            json!([{"repeat": 1, "count": 1}]),
            json!([{"repeat": "a", "count": 0}]),
            json!([{"repeat": "a", "count": -1}]),
            json!([{"repeat": "a", "count": 1.0}]),
            json!([{"repeat": "a", "count": true}]),
            json!([{"repeat": "a", "count": REPEAT_MAX + 1}]),
        ] {
            assert_eq!(parts_text(&bad), None, "{bad}");
            assert_eq!(project_validate_text(&json!({"parts": bad})), type_error());
        }
        assert_eq!(
            project_validate_text(&json!({"parts": [], "hex": ""})),
            type_error()
        );
        // parts 與等價的字串輸入結果相同
        let text = format!(
            r#"{{"schema_version": 1, "name": "v1", "script": "{}"}}"#,
            "b".repeat(20)
        );
        assert_eq!(
            project_validate_text(
                &json!({"parts": [r#"{"schema_version": 1, "name": "v1", "script": ""#, {"repeat": "b", "count": 20}, r#""}"#]})
            ),
            project_validate_text(&json!(text))
        );
    }

    #[test]
    fn compact_dump_threshold_and_reversibility() {
        let under = format!("x{}y", "a".repeat(RUN_MIN - 1));
        assert_eq!(compact_dump(&under), json!(under));
        let at = format!("x{}y\n", "a".repeat(RUN_MIN));
        let c = compact_dump(&at);
        assert_eq!(
            c,
            json!({"parts": ["x", {"repeat": "a", "count": RUN_MIN}, "y\n"]})
        );
        assert_eq!(parts_text(&c["parts"]), Some(at));
        // 多段、非 ASCII、開頭與結尾就是連續段
        let multi = format!(
            "{}中{}{}",
            "字".repeat(1500),
            " ".repeat(999),
            "z".repeat(RUN_MIN + 7)
        );
        let c = compact_dump(&multi);
        assert_eq!(c["parts"].as_array().map(Vec::len), Some(3));
        assert_eq!(parts_text(&c["parts"]), Some(multi.clone()));
        assert_eq!(compact_dump(&multi), c, "確定性");
        assert_eq!(compact_dump(""), json!(""));
        // 實際的 validate_text:script 999 個字元照舊為字串、1000 個改為 parts
        for (n, compact) in [(RUN_MIN - 1, false), (RUN_MIN, true)] {
            let input = json!({"parts": [r#"{"schema_version": 2, "name": "x", "script": ""#, {"repeat": "a", "count": n}, r#""}"#]});
            let Outcome::Ok(v) = project_validate_text(&input) else {
                panic!("預期 ok");
            };
            assert_eq!(v["dump"].is_object(), compact, "{n}");
            let full = match &v["dump"] {
                Value::String(s) => s.clone(),
                other => parts_text(&other["parts"]).expect("可展開"),
            };
            assert!(full.contains(&format!("\"script\": \"{}\",", "a".repeat(n))));
        }
    }

    #[test]
    fn errors_are_reported_inside_ok() {
        assert_eq!(
            project_normalize_speed(&json!({"v": "x"})),
            Outcome::Ok(json!({"class": "project_error", "errors": ["語速必須是數字"]}))
        );
        assert_eq!(
            project_normalize_pause(&json!({"v": -0.01})),
            Outcome::Ok(json!({"value": -0.0, "repr": "-0.0"}))
        );
    }
}
