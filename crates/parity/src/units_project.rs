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
//! | `project.split_segments` | `{"text": 字串或 {"parts"}, "forced"?: [整數…]}` | `{"value": [{"text", "break", "start", "end"}…]}` |
//! | `project.escape_text` | 字串 | `{"value": 字串}` |
//! | `project.tts_text` | `{"segment"}` | `{"value": 合成文字}` |
//! | `project.fused_out` | `{"segment"}` | `{"value": [索引…](遞增)}` |
//! | `project.model_units` | `{"segment"}` | `{"value": [索引…]}` |
//! | `project.shift_marks` | `{"segment", "old": 字串, "new": 字串}` | `{"value": {"fusions", "fusion_off", "cleared"}}` |
//! | `project.post_params` | `{"segment"}` | `{"value": 後製參數, "repr"}` |
//! | `project.post_key` | `{"segment"}` | `{"value": 64 碼後製鍵}` |
//! | `project.is_post_fresh` | `{"segment"}` | `{"value": 布林}` |
//! | `project.is_default_post` | `{"segment"}` | `{"value": 布林}` |
//! | `project.effective` | `{"segment", "defaults"?}` | `{"value": {"speed", "pause"}, "repr"}` |
//!
//! 片段類單元(T3):兩邊都把 `segment` 放進 `{"schema_version": 2, "name": "p", "segments": [segment]}`(`effective` 另把
//! 給定的 `defaults` 放進去)以 `validate` 驗證——Python 呼叫端傳入的就是驗證後的片段;驗證失敗時回 `validate` 的
//! `{"class", "errors"}`,通過時對驗證後的片段呼叫函式(Rust `project::text` / `project::post`,Python 同名函式;
//! `effective` 為 `effective_speed` 與 `effective_pause`,Python 以 `Project.from_dict` 建立專案)。
//! `split_segments` 的 `forced` 只接受整數(不含布林、64 位元範圍內):Rust API 為 `&[i64]`,非整數由型別排除
//! (D-12 T3 補記),兩邊都回 `error type`。
//!
//! 片段類與文字類單元的精簡形式(T3 補記,同字元連續上限 64):輸入中的文字欄位——`split_segments` 的 `text`、
//! `escape_text` 的輸入本身、片段類的 `segment.text`、`shift_marks` 的 `old` / `new`——可為字串或 `{"parts": [片段…]}`
//! ([`text_value`],展開規則同 [`parts_text`];不合格為 `error type`,片段的 `text` 在驗證前展開);接受時的 `value` 內
//! 每個字串有同一字元連續 [`RUN_MIN`](65)個以上時改為 `{"parts": […]}`([`compact_strings`],以 [`compact_dump`]
//! 的規則逐一轉換,可逆且確定)。轉接器的 `_project_text_value`、`_project_compact_strings` 是同一規則。
//!
//! `project.validate_text`(T2):兩邊各自解析原文——Rust 以 [`project::validate::parse_project_json`]
//! (= Python `_read_project_file` 讀到位元組之後的部分:5 MB 上限、UTF-8、JSON、須為物件),Python 端把位元組寫進
//! 自己建立的暫存目錄後呼叫真的 `_read_project_file`——所以 JSON 層的差異也測得到;接著 `validate` 與 `_dump`。
//!
//! 精簡形式(DIR-6 第 1 輪 F-1):超長的重複內容不直接寫進 fixture 與預期結果表。輸入 `{"parts": [片段…]}` 的片段為
//! 字串或 `{"repeat": 非空字串, "count": 1~5242881}`,依序串接後以 UTF-8 編碼([`parts_text`]);接受時的 dump 有同一
//! 字元連續 65 個以上(T3 補記:原為 1000)時改回 `{"dump": {"parts": […]}}`([`compact_dump`],以 [`parts_text`] 展開即得原字串)。
//! 轉接器的 `_project_parts_text`、`_project_compact_dump` 是同一規則。
//!
//! `project.py_json` 的物件鍵:fixture 經 `serde_json` 解析後鍵已依碼位排序,送給 Python 的請求也是排序後的鍵,
//! 兩邊收到同一個鍵順序(依建構順序寫出由 `crates/project/tests/pyjson.rs` 的 `Entries` 與結構測試鎖住)。

use project::model::Segment;
use project::pyjson::{self, Entries};
use project::{Error, Project, checks, constants, post, pystr, text, validate};
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
/// 結果中同一字元連續達此長度才改用 `parts`(T3 補記:原為 1000;fixture 與預期結果表的同字元連續上限為 64)。
pub const RUN_MIN: usize = 65;

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

// ---------- T3:純函式 ----------

/// 文字欄位:字串或 `{"parts": [片段…]}`(恰此一鍵,[`parts_text`] 展開);其他回 `None`(→ `error type`)。
pub fn text_value(v: &Value) -> Option<String> {
    match v {
        Value::String(s) => Some(s.clone()),
        Value::Object(o) if o.len() == 1 && o.contains_key("parts") => parts_text(&o["parts"]),
        _ => None,
    }
}

/// 接受時的值:每個字串依 [`compact_dump`] 的規則轉換(同字元連續 ≥ [`RUN_MIN`] 改為 `{"parts"}`),陣列與物件遞迴處理。
pub fn compact_strings(v: Value) -> Value {
    match v {
        Value::String(s) => compact_dump(&s),
        Value::Array(list) => Value::Array(list.into_iter().map(compact_strings).collect()),
        Value::Object(obj) => Value::Object(
            obj.into_iter()
                .map(|(k, x)| (k, compact_strings(x)))
                .collect(),
        ),
        other => other,
    }
}

/// `{"value": 精簡後的值}`。
fn text_ok(v: Value) -> Outcome {
    Outcome::Ok(json!({ "value": compact_strings(v) }))
}

/// 片段類單元的輸入:`{"segment", "defaults"?}` → 交給 `validate` 的專案;缺 `segment` 回 `None`(→ `error type`)。
fn segment_project(input: &Value, with_defaults: bool) -> Option<Value> {
    let obj = input.as_object()?;
    let mut segment = obj.get("segment")?.clone();
    if let Some(t) = segment.get_mut("text")
        && t.is_object()
    {
        *t = Value::String(text_value(t)?);
    }
    let mut data = json!({"schema_version": 2, "name": "p", "segments": [segment]});
    if with_defaults && let Some(d) = obj.get("defaults") {
        data["defaults"] = d.clone();
    }
    Some(data)
}

/// 驗證後的專案(片段在 `segments[0]`);輸入格式不符回 `Err(type_error)`、驗證失敗回 `Err(錯誤結果)`。
fn validated(input: &Value, with_defaults: bool) -> Result<Project, Outcome> {
    let data = segment_project(input, with_defaults).ok_or_else(type_error)?;
    validate::validate(&data).map_err(|e| error_result(&e))
}

fn on_segment(input: &Value, f: impl FnOnce(&Segment) -> Outcome) -> Outcome {
    match validated(input, false) {
        Ok(p) => p.segments.first().map_or_else(type_error, f),
        Err(o) => o,
    }
}

/// `project.split_segments`:`{"text": 字串或 {"parts"}, "forced"?: [整數…]}`。
pub fn project_split_segments(input: &Value) -> Outcome {
    let Some(obj) = input.as_object() else {
        return type_error();
    };
    let Some(text) = obj.get("text").and_then(text_value) else {
        return type_error();
    };
    let forced: Vec<i64> = match obj.get("forced") {
        None => Vec::new(),
        Some(Value::Array(list)) => match list.iter().map(Value::as_i64).collect() {
            Some(v) => v,
            None => return type_error(),
        },
        Some(_) => return type_error(),
    };
    match text::split_segments(&text, &forced) {
        Ok(pieces) => text_ok(json!(pieces)),
        Err(e) => error_result(&e),
    }
}

/// `project.escape_text`:輸入為字串或 `{"parts"}`。
pub fn project_escape_text(input: &Value) -> Outcome {
    match text_value(input) {
        Some(s) => text_ok(json!(text::escape_text(&s))),
        None => type_error(),
    }
}

/// `project.tts_text`。
pub fn project_tts_text(input: &Value) -> Outcome {
    on_segment(input, |s| match text::tts_text(s) {
        Ok(t) => text_ok(json!(t)),
        Err(e) => error_result(&e),
    })
}

/// `project.fused_out`(遞增排序的索引)。
pub fn project_fused_out(input: &Value) -> Outcome {
    on_segment(input, |s| text_ok(json!(text::fused_out(s))))
}

/// `project.model_units`。
pub fn project_model_units(input: &Value) -> Outcome {
    on_segment(input, |s| text_ok(json!(text::model_units(s))))
}

/// `project.shift_marks`:另需 `old`、`new`(字串或 `{"parts"}`)。
pub fn project_shift_marks(input: &Value) -> Outcome {
    let (Some(old), Some(new)) = (
        input.get("old").and_then(text_value),
        input.get("new").and_then(text_value),
    ) else {
        return type_error();
    };
    on_segment(input, |s| text_ok(json!(text::shift_marks(s, &old, &new))))
}

/// `project.post_params`。
pub fn project_post_params(input: &Value) -> Outcome {
    on_segment(input, |s| value_result(post::post_params(s).to_value()))
}

/// `project.post_key`。
pub fn project_post_key(input: &Value) -> Outcome {
    on_segment(input, |s| match post::post_key(s) {
        Ok(k) => text_ok(json!(k)),
        Err(e) => error_result(&e),
    })
}

/// `project.is_post_fresh`。
pub fn project_is_post_fresh(input: &Value) -> Outcome {
    on_segment(input, |s| text_ok(json!(post::is_post_fresh(s))))
}

/// `project.is_default_post`。
pub fn project_is_default_post(input: &Value) -> Outcome {
    on_segment(input, |s| text_ok(json!(post::is_default_post(s))))
}

/// `project.effective`:`effective_speed` 與 `effective_pause`(專案的 `defaults` 可由輸入給定)。
pub fn project_effective(input: &Value) -> Outcome {
    match validated(input, true) {
        Ok(p) => p.segments.first().map_or_else(type_error, |s| {
            value_result(json!({
                "speed": post::effective_speed(&p, s),
                "pause": post::effective_pause(&p, s),
            }))
        }),
        Err(o) => o,
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
            " ".repeat(RUN_MIN - 1),
            "z".repeat(RUN_MIN + 7)
        );
        let c = compact_dump(&multi);
        assert_eq!(c["parts"].as_array().map(Vec::len), Some(3));
        assert_eq!(parts_text(&c["parts"]), Some(multi.clone()));
        assert_eq!(compact_dump(&multi), c, "確定性");
        assert_eq!(compact_dump(""), json!(""));
        // 實際的 validate_text:script 64 個字元照舊為字串、65 個改為 parts
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
    fn text_units_accept_parts_and_compact_long_runs() {
        // 門檻 64 / 65:64 個連續照舊為字串、65 個改為 parts,展開即得原字串
        let s64 = "字".repeat(RUN_MIN - 1);
        let s65 = "字".repeat(RUN_MIN);
        assert_eq!(compact_strings(json!([s64.clone()])), json!([s64]));
        let c = compact_strings(json!({"a": [s65.clone()], "n": 1, "b": true}));
        assert_eq!(
            c,
            json!({"a": [{"parts": [{"repeat": "字", "count": 65}]}], "n": 1, "b": true})
        );
        assert_eq!(parts_text(&c["a"][0]["parts"]), Some(s65.clone()));
        // 文字欄位:字串或 {"parts"};其他(含多餘的鍵、不合格的片段)為 None
        assert_eq!(text_value(&json!("x")), Some("x".to_string()));
        assert_eq!(
            text_value(&json!({"parts": ["a", {"repeat": "b", "count": 2}]})),
            Some("abb".to_string())
        );
        for bad in [
            json!(1),
            json!(null),
            json!({"parts": []}),
            json!({"parts": [{"repeat": "", "count": 1}]}),
            json!({"parts": ["a"], "x": 1}),
            json!({"text": "a"}),
        ] {
            assert_eq!(text_value(&bad), None, "{bad}");
        }
        let parts = json!({"parts": [{"repeat": "[", "count": 70}]});
        assert_eq!(
            project_escape_text(&parts),
            Outcome::Ok(json!({"value": {"parts": [{"repeat": "［", "count": 70}]}}))
        );
        assert_eq!(project_escape_text(&json!({"parts": []})), type_error());
        // 片段類:segment.text 可為 parts(驗證前展開);不合格為 error type
        let seg = |text: Value| json!({"segment": {"id": "0123456789abcdef", "text": text}});
        let long = json!({"parts": [{"repeat": "字", "count": 499}, "。"]});
        let Outcome::Ok(v) = project_tts_text(&seg(long)) else {
            panic!("預期 ok");
        };
        assert_eq!(
            v,
            json!({"value": {"parts": [{"repeat": "字", "count": 499}, "。"]}})
        );
        assert_eq!(project_tts_text(&seg(json!({"parts": [1]}))), type_error());
        assert_eq!(project_tts_text(&seg(json!({"x": "a"}))), type_error());
        assert_eq!(
            project_tts_text(&seg(json!({"parts": ["好"]}))),
            project_tts_text(&seg(json!("好")))
        );
        // shift_marks 的 old / new 與 split_segments 的 text 同樣可為 parts
        let shift = json!({"segment": {"id": "0123456789abcdef", "text": "好"},
                           "old": {"parts": [{"repeat": "a", "count": 3}]}, "new": "aaab"});
        assert_eq!(
            project_shift_marks(&shift),
            Outcome::Ok(json!({"value": {"fusions": [], "fusion_off": [], "cleared": 0}}))
        );
        let shift_bad = json!({"segment": {"id": "0123456789abcdef", "text": "好"}, "old": {"parts": []}, "new": ""});
        assert_eq!(project_shift_marks(&shift_bad), type_error());
        let Outcome::Ok(v) =
            project_split_segments(&json!({"text": {"parts": [{"repeat": "字", "count": 500}]}}))
        else {
            panic!("預期 ok");
        };
        assert_eq!(
            v["value"][0]["text"],
            json!({"parts": [{"repeat": "字", "count": 500}]})
        );
        assert_eq!(v["value"][0]["end"], json!(500));
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
