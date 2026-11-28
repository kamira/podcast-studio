//! 讀 JSON 原文與完整驗證(`project.py` 的 `_read_project_file` 讀檔之後的部分、`validate` 與 `_validate_*`、
//! `_check_*`;CHG-20261128-01 T2,代決 5、6、9)。
//!
//! - [`parse_project_json`]:原文位元組 → JSON 值(5 MB 上限、UTF-8、`serde_json` 解析、須為物件)。
//! - [`validate`]:JSON 值 → [`Project`];**累積全部錯誤訊息**,順序與逐字內容同 Python。
//! - [`dump_validated`]:Python `_dump(validate(data))`——以同一個寫出器的位元組長度檢查 5 MB 上限。
//! - [`check_version`]:Python `_check_version`(載入時另外呼叫,T4)。
//!
//! 已知差異(「### 行為差異清單」):D-1 `-0`、D-2 超出 64 位元的整數、D-3 `NaN` / `Infinity` / 超出範圍的浮點、
//! D-4 巢狀超過 128 層、D-5 Python 丟出未分類例外的輸入、D-6 `audio` / `post` / 素材的鍵順序與 `defaults`
//! 的訊息順序、D-15 `schema_version` 訊息中 `repr` 對 Unicode 未指派碼位的顯示。

use std::collections::HashSet;

use serde_json::{Map, Number, Value};
use studio_core::value_checks::is_int;

use crate::checks::{
    check_name, check_percent, check_pitch_points, check_zhuyin, normalize_pause, normalize_speed,
};
use crate::constants::{
    ASSET_EXTS, ASSET_KEYS, BREAKS, DEFAULTS, FUSION_DISPLAY_MAX, FUSION_MAX, FUSION_MIN,
    FUSION_SOURCES, IMAGE_KEYS, MAX_ASSET_BYTES, MAX_ASSETS, MAX_AUDIO_SECONDS, MAX_FUSION_OFF,
    MAX_FUSIONS, MAX_LABEL, MAX_PROJECT_BYTES, MAX_SCRIPT, MAX_SEGMENT_TEXT, MAX_SEGMENTS,
    MAX_WORD_CLARITY, NAME_HINT, OLD_SCHEMA_VERSIONS, PITCH_POINTS_MAX, PITCH_POINTS_MIN,
    POST_KEYS, POST_OPTIONAL, SCHEMA_VERSION, SEGMENT_KEYS, SEGMENT_KEYS_V1, SEMI_MAX_TEXT,
    SEMI_MIN_TEXT, TOP_KEYS,
};
use crate::model::{
    Annotation, Asset, Audio, Defaults, Fusion, FusionOff, Image, Pitch, PitchPoint, Post, Project,
    Segment, WordClarity,
};
use crate::pyjson::{dump_bytes, float_repr};
use crate::pystr::{char_len, is_hanzi, is_id, is_key, py_strip};
use crate::round::{PAUSE_STEP, POS_STEP, SEMI_STEP, round_half_up};
use crate::{Error, Result};

/// 原文無法解析(Python `json.loads` 失敗、UTF-8 解碼失敗)。
pub const CORRUPT: &str = "專案檔內容損壞,無法讀取";
/// 原文的頂層不是物件。
pub const NOT_OBJECT: &str = "專案檔必須是 JSON 物件";

/// 語調關鍵點位置的步數範圍(0 / 0.01 = 0、1 / 0.01 = 100)。
const POS_STEPS: std::ops::RangeInclusive<i128> = 0..=100;
/// 語調關鍵點半音的步數範圍(−6 / 0.1 = −60、6 / 0.1 = 60)。
const SEMI_STEPS: std::ops::RangeInclusive<i128> = -60..=60;

const MIB: usize = 1024 * 1024;
const GIB: u64 = 1024 * 1024 * 1024;

/// Python `_read_project_file` 讀到位元組之後的部分:超過 5 MB 拒絕;UTF-8 解碼與 JSON 解析失敗為
/// 「專案檔內容損壞」;頂層須為物件。
///
/// 解析用 `serde_json`(開 `float_roundtrip`):重複鍵取最後一個(與 Python 相同);
/// 不接受 `NaN` / `Infinity` / 超出 `f64` 範圍的數(D-3)、巢狀超過 128 層(D-4)、不成對代理的跳脫(D-5)。
pub fn parse_project_json(raw: &[u8]) -> Result<Value> {
    if raw.len() > MAX_PROJECT_BYTES {
        return Err(Error::invalid(format!(
            "專案檔超過 {} MB,拒絕載入",
            MAX_PROJECT_BYTES / MIB
        )));
    }
    let text = std::str::from_utf8(raw).map_err(|_| Error::invalid(CORRUPT))?;
    let data: Value = serde_json::from_str(text).map_err(|_| Error::invalid(CORRUPT))?;
    if !data.is_object() {
        return Err(Error::invalid(NOT_OBJECT));
    }
    Ok(data)
}

/// Python `validate(data)`(= `Project.from_dict`):驗證並正規化;不合法回 `Invalid`,列出全部錯誤。
pub fn validate(data: &Value) -> Result<Project> {
    validate_and_dump(data).map(|(project, _)| project)
}

/// Python `_dump(validate(data))`:驗證後以 Python 相同位元組寫出(結尾換行);寫出超過 5 MB 回錯誤。
pub fn dump_validated(data: &Value) -> Result<Vec<u8>> {
    validate_and_dump(data).map(|(_, bytes)| bytes)
}

/// Python `_check_version(v)`:`2` 或舊版 `1`(整數,不含布林與浮點);其他值以 Python `repr`
/// (前 20 個碼位)顯示在訊息中。
pub fn check_version(v: Option<&Value>) -> Result<i64> {
    if let Some(Value::Number(n)) = v
        && let Some(i) = n.as_i64()
        && (i == SCHEMA_VERSION || OLD_SCHEMA_VERSIONS.contains(&i))
    {
        return Ok(i);
    }
    let shown = match v {
        None | Some(Value::Null) => "None".to_string(),
        Some(Value::Bool(true)) => "True".to_string(),
        Some(Value::Bool(false)) => "False".to_string(),
        Some(Value::Number(n)) => number_repr(n),
        Some(Value::String(s)) => py_repr(s),
        Some(Value::Array(_)) => "list".to_string(),
        Some(Value::Object(_)) => "dict".to_string(),
    };
    let shown: String = shown.chars().take(20).collect();
    Err(Error::invalid(format!(
        "專案檔版本 {shown} 不支援,拒絕載入(本版讀 schema_version {SCHEMA_VERSION},舊版 1 會自動升級)"
    )))
}

fn number_repr(n: &Number) -> String {
    if n.is_f64() {
        float_repr(n.as_f64().unwrap_or(f64::NAN))
    } else {
        n.to_string()
    }
}

/// Python `repr(str)`:有單引號且沒有雙引號時用雙引號,否則用單引號;`\` 與所用引號前加 `\`;
/// `\t` `\n` `\r` 短跳脫;其他不可列印字元以 `\xhh` / `\uhhhh` / `\Uhhhhhhhh`(小寫十六進位)顯示。
fn py_repr(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if !py_printable(c) => {
                let cp = u32::from(c);
                if cp < 0x100 {
                    out.push_str(&format!("\\x{cp:02x}"));
                } else if cp < 0x10000 {
                    out.push_str(&format!("\\u{cp:04x}"));
                } else {
                    out.push_str(&format!("\\U{cp:08x}"));
                }
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// Python `str.isprintable()`(單一字元)的近似:ASCII 控制字元與 DEL、C1 控制字元、空格以外的空白類(Zs)、
/// 行與段落分隔(Zl、Zp)、格式字元(Cf)、私用區(Co)、非字元,以及 Unicode 14 的平面 2、3、14 未指派區段與
/// 平面 4~13 為不可列印;其他 Unicode 14 未指派碼位(Cn)Python 視為不可列印、本函式視為可列印(行為差異 D-15)。
fn py_printable(c: char) -> bool {
    let cp = u32::from(c);
    if cp < 0x20 || cp == 0x7F {
        return false;
    }
    if cp < 0x7F {
        return true;
    }
    if cp & 0xFFFE == 0xFFFE {
        return false;
    }
    !matches!(
        cp,
        0x80..=0xA0
            | 0xAD
            | 0x600..=0x605
            | 0x61C
            | 0x6DD
            | 0x70F
            | 0x890..=0x891
            | 0x8E2
            | 0x1680
            | 0x180E
            | 0x2000..=0x200F
            | 0x2028..=0x202F
            | 0x205F..=0x206F
            | 0x3000
            | 0xD800..=0xF8FF
            | 0xFDD0..=0xFDEF
            | 0xFEFF
            | 0xFFF0..=0xFFFB
            | 0x110BD
            | 0x110CD
            | 0x13430..=0x13438
            | 0x1BCA0..=0x1BCA3
            | 0x1D173..=0x1D17A
            | 0x2A6E0..=0x2A6FF
            | 0x2B739..=0x2B73F
            | 0x2B81E..=0x2B81F
            | 0x2CEA2..=0x2CEAF
            | 0x2EBE1..=0x2F7FF
            | 0x2FA1E..=0x2FFFF
            | 0x3134B..=0xE00FF
            | 0xE01F0..=0x10FFFF
    )
}

/// Python `_try`:失敗時把訊息加入累積清單、回 `None`。
fn attempt<T>(errors: &mut Vec<String>, r: Result<T>) -> Option<T> {
    match r {
        Ok(v) => Some(v),
        Err(e) => {
            errors.extend(e.errors().iter().cloned());
            None
        }
    }
}

/// Python `_check_keys`:須為物件、沒有 `allowed` 以外的鍵(依碼位排序、串接後取前 200 字)、`required` 都在。
fn check_keys<'a>(
    v: &'a Value,
    allowed: &[&str],
    required: &[&str],
    label: &str,
    errors: &mut Vec<String>,
) -> Option<&'a Map<String, Value>> {
    let Value::Object(obj) = v else {
        errors.push(format!("{label} 必須是物件"));
        return None;
    };
    let mut unknown: Vec<&str> = obj
        .keys()
        .map(String::as_str)
        .filter(|k| !allowed.contains(k))
        .collect();
    if !unknown.is_empty() {
        unknown.sort_unstable();
        let joined: String = unknown.join(", ").chars().take(200).collect();
        errors.push(format!("{label} 有不認得的欄位:{joined}"));
        return None;
    }
    let missing: Vec<&str> = required
        .iter()
        .copied()
        .filter(|k| !obj.contains_key(*k))
        .collect();
    if !missing.is_empty() {
        errors.push(format!("{label} 缺少欄位:{}", missing.join(", ")));
        return None;
    }
    Some(obj)
}

/// 物件的鍵集合是否恰為 `keys`(Python `set(obj) == {…}`)。
fn has_exact_keys(obj: &Map<String, Value>, keys: &[&str]) -> bool {
    obj.len() == keys.len() && keys.iter().all(|k| obj.contains_key(*k))
}

/// Python `_check_id`:`ID_RE` 且在 `seen`(素材、片段、畫面共用)中沒有出現過。
fn check_id(v: &Value, label: &str, errors: &mut Vec<String>, seen: &mut HashSet<String>) -> bool {
    let Value::String(id) = v else {
        errors.push(format!("{label}.id 格式不正確"));
        return false;
    };
    if !is_id(id) {
        errors.push(format!("{label}.id 格式不正確"));
        return false;
    }
    if !seen.insert(id.clone()) {
        errors.push(format!("{label}.id 重複"));
        return false;
    }
    true
}

/// `_is_number(v) and v >= 0`。
fn non_negative_number(v: &Value) -> Option<&Number> {
    match v {
        Value::Number(n) if !is_negative(n) => Some(n),
        _ => None,
    }
}

fn is_negative(n: &Number) -> bool {
    match n.as_i64() {
        Some(i) => i < 0,
        None => n.as_f64().is_some_and(|f| f < 0.0),
    }
}

/// 非負整數值(`value_checks.is_int(v) and v >= 0`)。
fn non_negative_int(v: &Value) -> Option<u64> {
    if is_int(v) { v.as_u64() } else { None }
}

fn is_null_or_missing(v: Option<&Value>) -> bool {
    matches!(v, None | Some(Value::Null))
}

fn all_hanzi(chars: &[char]) -> bool {
    chars.iter().all(|&c| is_hanzi(c))
}

/// `text[a:b]`(碼位;`b` 超出時截到結尾)。
fn slice(chars: &[char], a: usize, b: usize) -> &[char] {
    let b = b.min(chars.len());
    &chars[a.min(b)..b]
}

fn validate_and_dump(data: &Value) -> Result<(Project, Vec<u8>)> {
    let Value::Object(obj) = data else {
        return Err(Error::invalid(NOT_OBJECT));
    };
    let version = check_version(obj.get("schema_version"))?;
    let mut errors: Vec<String> = Vec::new();
    if check_keys(
        data,
        &TOP_KEYS,
        &["schema_version", "name"],
        "專案",
        &mut errors,
    )
    .is_none()
    {
        return Err(Error::invalid_all(errors));
    }
    let name = match &obj["name"] {
        Value::String(s) => attempt(&mut errors, check_name(s).map(str::to_string)),
        _ => {
            errors.push(NAME_HINT.to_string());
            None
        }
    };
    let script = match obj.get("script") {
        None => String::new(),
        Some(Value::String(s)) => {
            if char_len(s) > MAX_SCRIPT {
                errors.push(format!("文稿超過 {MAX_SCRIPT} 字"));
            }
            s.clone()
        }
        Some(_) => {
            errors.push("script 必須是文字".to_string());
            String::new()
        }
    };
    let defaults = validate_defaults(obj.get("defaults"), &mut errors);

    let mut seen: HashSet<String> = HashSet::new();
    let assets: Vec<Option<Asset>> = list(obj, "assets", &mut errors, MAX_ASSETS)
        .iter()
        .enumerate()
        .map(|(i, a)| validate_asset(a, &format!("assets[{i}]"), &mut errors, &mut seen))
        .collect();
    let total: u128 = assets.iter().flatten().map(|a| u128::from(a.bytes)).sum();
    if total > u128::from(MAX_ASSET_BYTES) {
        errors.push(format!("素材總量超過 {} GB", MAX_ASSET_BYTES / GIB));
    }
    let image_assets: HashSet<String> = assets
        .iter()
        .flatten()
        .filter(|a| a.kind == "image")
        .map(|a| a.id.clone())
        .collect();
    let segments: Vec<Option<Segment>> = list(obj, "segments", &mut errors, MAX_SEGMENTS)
        .iter()
        .enumerate()
        .map(|(i, s)| {
            validate_segment(
                s,
                &format!("segments[{i}]"),
                &mut errors,
                &mut seen,
                version,
            )
        })
        .collect();
    let images: Vec<Option<Image>> = list(obj, "images", &mut errors, MAX_ASSETS)
        .iter()
        .enumerate()
        .map(|(i, m)| {
            validate_image(
                m,
                &format!("images[{i}]"),
                &mut errors,
                &mut seen,
                &image_assets,
            )
        })
        .collect();
    if errors.is_empty() {
        // Python:sum(s["audio"]["seconds"] / _speed_of(defaults, s) ...),由左到右累加
        let mut seconds = 0.0f64;
        for s in segments.iter().flatten() {
            if let Some(audio) = &s.audio {
                let speed = s.speed.unwrap_or(defaults.speed);
                seconds += audio.seconds.as_f64().unwrap_or(f64::INFINITY) / speed;
            }
        }
        if seconds > MAX_AUDIO_SECONDS as f64 {
            errors.push(format!(
                "專案音訊總長超過 {} 分鐘(以變速後長度計)",
                MAX_AUDIO_SECONDS / 60
            ));
        }
    }
    if !errors.is_empty() {
        return Err(Error::invalid_all(errors));
    }
    // 沒有錯誤時每一項都已驗證成功(Python 的 None 只會伴隨錯誤訊息出現)
    let all = || Error::invalid_all(vec![NAME_HINT.to_string()]);
    let project = Project {
        schema_version: SCHEMA_VERSION,
        name: name.ok_or_else(all)?,
        script,
        defaults,
        segments: segments
            .into_iter()
            .collect::<Option<Vec<_>>>()
            .ok_or_else(all)?,
        images: images
            .into_iter()
            .collect::<Option<Vec<_>>>()
            .ok_or_else(all)?,
        assets: assets
            .into_iter()
            .collect::<Option<Vec<_>>>()
            .ok_or_else(all)?,
    };
    let bytes = dump_bytes(&project);
    if bytes.len() > MAX_PROJECT_BYTES {
        return Err(Error::invalid(format!(
            "專案檔超過 {} MB",
            MAX_PROJECT_BYTES / MIB
        )));
    }
    Ok((project, bytes))
}

/// Python `_list(data, key, errors, limit)`:沒有這個鍵為空清單;不是陣列或超過上限時記錯誤並當成空清單。
fn list<'a>(
    obj: &'a Map<String, Value>,
    key: &str,
    errors: &mut Vec<String>,
    limit: usize,
) -> &'a [Value] {
    match obj.get(key) {
        None => &[],
        Some(Value::Array(v)) if v.len() <= limit => v,
        Some(Value::Array(_)) => {
            errors.push(format!("{key} 超過 {limit} 個"));
            &[]
        }
        Some(_) => {
            errors.push(format!("{key} 必須是陣列"));
            &[]
        }
    }
}

/// Python `_validate_defaults`:未給的鍵用 `DEFAULTS`;不合格的值記錯誤、保留預設。
///
/// 多個鍵不合格時,訊息依鍵的碼位順序累積(Python 依輸入檔的鍵順序;行為差異 D-6,T2 補記)。
fn validate_defaults(d: Option<&Value>, errors: &mut Vec<String>) -> Defaults {
    let mut out = Defaults::default();
    let Some(d) = d.filter(|v| !v.is_null()) else {
        return out;
    };
    let keys: Vec<&str> = DEFAULTS.iter().map(|(k, _)| *k).collect();
    let Some(obj) = check_keys(d, &keys, &[], "defaults", errors) else {
        return out;
    };
    for (k, v) in obj {
        let label = format!("defaults.{k}");
        let r = if k == "speed" {
            normalize_speed(v, &label)
        } else {
            normalize_pause(v, &label)
        };
        if let Some(x) = attempt(errors, r) {
            match k.as_str() {
                "speed" => out.speed = x,
                "pause_comma" => out.pause_comma = x,
                "pause_sentence" => out.pause_sentence = x,
                _ => out.pause_paragraph = x,
            }
        }
    }
    out
}

fn validate_segment(
    s: &Value,
    label: &str,
    errors: &mut Vec<String>,
    seen: &mut HashSet<String>,
    version: i64,
) -> Option<Segment> {
    let allowed: &[&str] = if version == SCHEMA_VERSION {
        &SEGMENT_KEYS
    } else {
        &SEGMENT_KEYS_V1
    };
    let obj = check_keys(s, allowed, &["id", "text"], label, errors)?;
    let mut ok = check_id(&obj["id"], label, errors, seen);
    let text = match &obj["text"] {
        Value::String(t) if !py_strip(t).is_empty() => t,
        _ => {
            errors.push(format!("{label}.text 是空的"));
            return None;
        }
    };
    let chars: Vec<char> = text.chars().collect();
    if chars.len() > MAX_SEGMENT_TEXT {
        errors.push(format!("{label}.text 超過 {MAX_SEGMENT_TEXT} 字"));
        return None;
    }
    let brk = match obj.get("break") {
        None => Some("end".to_string()),
        Some(Value::String(b)) if BREAKS.contains(&b.as_str()) => Some(b.clone()),
        Some(_) => None,
    };
    if brk.is_none() {
        errors.push(format!("{label}.break 只能是 {}", BREAKS.join("、")));
        ok = false;
    }
    let pause_given = !is_null_or_missing(obj.get("pause_after"));
    let pause_after = match obj.get("pause_after") {
        Some(v) if pause_given => {
            attempt(errors, normalize_pause(v, &format!("{label}.pause_after")))
        }
        _ => None,
    };
    let speed_given = !is_null_or_missing(obj.get("speed"));
    let speed = match obj.get("speed") {
        Some(v) if speed_given => attempt(errors, normalize_speed(v, &format!("{label}.speed"))),
        _ => None,
    };
    let annotations = attempt(
        errors,
        check_annotations(&chars, obj.get("annotations"), label),
    );
    let mut audio = None;
    if let Some(a) = obj.get("audio").filter(|v| !v.is_null()) {
        let audio_label = format!("{label}.audio");
        match check_keys(
            a,
            &["id", "seconds", "key"],
            &["id", "seconds"],
            &audio_label,
            errors,
        ) {
            None => ok = false,
            Some(am) => {
                let key = am.get("key");
                match (&am["id"], non_negative_number(&am["seconds"]), key) {
                    (Value::String(id), _, _) if !is_id(id) => {
                        errors.push(format!("{label}.audio.id 格式不正確"));
                        ok = false;
                    }
                    (Value::String(_), None, _) => {
                        errors.push(format!("{label}.audio.seconds 必須是非負數字"));
                        ok = false;
                    }
                    (Value::String(id), Some(seconds), key) => match key {
                        Some(Value::String(k)) if is_key(k) => {
                            audio = Some(Audio {
                                id: id.clone(),
                                seconds: seconds.clone(),
                                key: Some(k.clone()),
                            });
                        }
                        Some(_) => {
                            errors.push(format!("{label}.audio.key 格式不正確"));
                            ok = false;
                        }
                        None => {
                            audio = Some(Audio {
                                id: id.clone(),
                                seconds: seconds.clone(),
                                key: None,
                            });
                        }
                    },
                    _ => {
                        errors.push(format!("{label}.audio.id 格式不正確"));
                        ok = false;
                    }
                }
            }
        }
    }
    if (pause_given && pause_after.is_none())
        || (speed_given && speed.is_none())
        || annotations.is_none()
    {
        ok = false;
    }
    // CHG-20261029-01 的 v2 欄位(v1 不允許這些鍵,一律得到預設值)
    let n_err = errors.len();
    let null = Value::Null;
    let connect = attempt(
        errors,
        check_percent(
            obj.get("connect").unwrap_or(&null),
            &format!("{label}.connect(連音)"),
        ),
    );
    let clarity = attempt(
        errors,
        check_percent(
            obj.get("clarity").unwrap_or(&null),
            &format!("{label}.clarity(清晰度)"),
        ),
    );
    let word_clarity = attempt(
        errors,
        check_word_clarity(&chars, obj.get("word_clarity"), label),
    );
    let pitch = attempt(errors, check_pitch(obj.get("pitch"), label));
    let post = attempt(errors, check_post(obj.get("post"), label));
    let fusions = match (&annotations, &word_clarity) {
        (Some(anns), Some(wc)) => attempt(
            errors,
            check_fusions(&chars, obj.get("fusions"), anns, wc, label),
        ),
        _ => Some(Vec::new()),
    };
    let fusion_off = attempt(
        errors,
        check_fusion_off(&chars, obj.get("fusion_off"), label),
    );
    if let (Some(on), Some(off)) = (&fusions, &fusion_off)
        && off.iter().any(|d| {
            on.iter()
                .any(|f| f.index == d.index && f.length == d.length)
        })
    {
        errors.push(format!("{label}:同一範圍不能同時啟用與停用合音"));
        ok = false;
    }
    if errors.len() != n_err || !ok {
        return None;
    }
    Some(Segment {
        id: obj["id"].as_str()?.to_string(),
        text: text.clone(),
        brk: brk?,
        pause_after,
        speed,
        annotations: annotations?,
        audio,
        connect: connect?,
        clarity: clarity?,
        word_clarity: word_clarity?,
        pitch: pitch?,
        post: post?,
        fusions: fusions?,
        fusion_off: fusion_off?,
    })
}

/// Python `_check_annotations`(`tts_text` 也以位置名稱「片段」呼叫,T3)。
pub(crate) fn check_annotations(
    chars: &[char],
    anns: Option<&Value>,
    label: &str,
) -> Result<Vec<Annotation>> {
    let list = match anns {
        None | Some(Value::Null) => return Ok(Vec::new()),
        Some(Value::Array(list)) => list,
        Some(_) => return Err(Error::invalid(format!("{label}.annotations 必須是陣列"))),
    };
    let mut out: Vec<Annotation> = Vec::with_capacity(list.len());
    for a in list {
        let obj = match a {
            Value::Object(obj) if has_exact_keys(obj, &["index", "zhuyin"]) => obj,
            _ => {
                return Err(Error::invalid(format!(
                    "{label}.annotations 每項必須恰為 index、zhuyin 兩個欄位"
                )));
            }
        };
        let i = match non_negative_int(&obj["index"]).and_then(|i| usize::try_from(i).ok()) {
            Some(i) if i < chars.len() => i,
            _ => {
                return Err(Error::invalid(format!(
                    "{label}.annotations 的索引超出句子範圍"
                )));
            }
        };
        if !is_hanzi(chars[i]) {
            return Err(Error::invalid(format!(
                "{label}.annotations 只能標在漢字上"
            )));
        }
        if out.iter().any(|x| x.index == i) {
            return Err(Error::invalid(format!(
                "{label}.annotations 同一個字重複標註"
            )));
        }
        let zhuyin = check_zhuyin(&obj["zhuyin"])
            .map_err(|e| Error::invalid(format!("{label}:{}", first(&e))))?;
        out.push(Annotation { index: i, zhuyin });
    }
    out.sort_by_key(|a| a.index);
    Ok(out)
}

fn first(e: &Error) -> &str {
    e.errors().first().map_or("", String::as_str)
}

/// Python `_check_word_clarity`。
fn check_word_clarity(
    chars: &[char],
    items: Option<&Value>,
    label: &str,
) -> Result<Vec<WordClarity>> {
    let list = match items {
        None | Some(Value::Null) => return Ok(Vec::new()),
        Some(Value::Array(list)) => list,
        Some(_) => return Err(Error::invalid(format!("{label}.word_clarity 必須是陣列"))),
    };
    if list.len() > MAX_WORD_CLARITY {
        return Err(Error::invalid(format!(
            "{label}.word_clarity 每句最多 {MAX_WORD_CLARITY} 段"
        )));
    }
    let mut out: Vec<WordClarity> = Vec::with_capacity(list.len());
    for it in list {
        let obj = match it {
            Value::Object(obj) if has_exact_keys(obj, &["start", "end", "value"]) => obj,
            _ => {
                return Err(Error::invalid(format!(
                    "{label}.word_clarity 每項必須恰為 start、end、value 三個欄位"
                )));
            }
        };
        let range = match (is_int(&obj["start"]), is_int(&obj["end"])) {
            (true, true) => match (non_negative_int(&obj["start"]), obj["end"].as_u64()) {
                (Some(a), Some(b)) if a < b && b <= chars.len() as u64 => {
                    usize::try_from(a).ok().zip(usize::try_from(b).ok())
                }
                _ => None,
            },
            _ => None,
        };
        let Some((a, b)) = range else {
            return Err(Error::invalid(format!(
                "{label}.word_clarity 的範圍必須在句子之內且起點小於終點"
            )));
        };
        if !all_hanzi(slice(chars, a, b)) {
            return Err(Error::invalid(format!(
                "{label}.word_clarity 只能設定在漢字上(不可含標點或其他字元)"
            )));
        }
        let value_label = format!("{label}.word_clarity 的清晰度");
        let Some(value) = check_percent(&obj["value"], &value_label)? else {
            return Err(Error::invalid(format!(
                "{value_label}必須是 0 到 100 的整數(百分比)"
            )));
        };
        out.push(WordClarity {
            start: a,
            end: b,
            value,
        });
    }
    out.sort_by_key(|w| w.start);
    if out.windows(2).any(|w| w[1].start < w[0].end) {
        return Err(Error::invalid(format!(
            "{label}.word_clarity 的範圍不可重疊"
        )));
    }
    Ok(out)
}

/// Python `_check_pitch`。四捨五入後超過 28 位數(Python `InvalidOperation`)時回該欄位的範圍錯誤(D-5,T2 補記)。
fn check_pitch(p: Option<&Value>, label: &str) -> Result<Option<Pitch>> {
    let obj = match p {
        None | Some(Value::Null) => return Ok(None),
        Some(Value::Object(obj)) if has_exact_keys(obj, &["points", "roundness"]) => obj,
        Some(_) => {
            return Err(Error::invalid(format!(
                "{label}.pitch 必須恰為 points、roundness 兩個欄位"
            )));
        }
    };
    let pts = match &obj["points"] {
        Value::Array(pts) if (PITCH_POINTS_MIN..=PITCH_POINTS_MAX).contains(&pts.len()) => pts,
        _ => {
            return Err(Error::invalid(format!(
                "{label}.pitch 的關鍵點必須是 {PITCH_POINTS_MIN} 到 {PITCH_POINTS_MAX} 個"
            )));
        }
    };
    let pos_error = || Error::invalid(format!("{label}.pitch 關鍵點位置必須是 0 到 1 的數字"));
    let semi_range = || {
        Error::invalid(format!(
            "{label}.pitch 關鍵點音高必須在 {SEMI_MIN_TEXT} 到 +{SEMI_MAX_TEXT} 半音之間"
        ))
    };
    let mut out: Vec<PitchPoint> = Vec::with_capacity(pts.len());
    for q in pts {
        let q = match q {
            Value::Object(q) if has_exact_keys(q, &["pos", "semi"]) => q,
            _ => {
                return Err(Error::invalid(format!(
                    "{label}.pitch 每個關鍵點必須恰為 pos、semi 兩個欄位"
                )));
            }
        };
        let Value::Number(pos) = &q["pos"] else {
            return Err(pos_error());
        };
        let pos = match round_half_up(pos, POS_STEP) {
            Some(r) if POS_STEPS.contains(&r.steps()) => r.to_f64(),
            _ => return Err(pos_error()),
        };
        let Value::Number(semi) = &q["semi"] else {
            return Err(Error::invalid(format!(
                "{label}.pitch 關鍵點音高必須是 {SEMI_MIN_TEXT} 到 +{SEMI_MAX_TEXT} 半音的數字"
            )));
        };
        let semi = match round_half_up(semi, SEMI_STEP) {
            // Python `float(semi) + 0.0`:-0.0 變 0.0
            Some(r) if SEMI_STEPS.contains(&r.steps()) => r.to_f64() + 0.0,
            _ => return Err(semi_range()),
        };
        out.push(PitchPoint { pos, semi });
    }
    if out.windows(2).any(|w| w[1].pos <= w[0].pos) {
        return Err(Error::invalid(format!(
            "{label}.pitch 關鍵點位置必須嚴格遞增"
        )));
    }
    let roundness = check_percent(&obj["roundness"], &format!("{label}.pitch 的圓弧"))?;
    Ok(Some(Pitch {
        points: out,
        roundness,
    }))
}

/// Python `_check_post`。
fn check_post(post: Option<&Value>, label: &str) -> Result<Option<Post>> {
    let obj = match post {
        None | Some(Value::Null) => return Ok(None),
        Some(Value::Object(obj))
            if POST_KEYS.iter().all(|k| obj.contains_key(*k))
                && obj.keys().all(|k| {
                    POST_KEYS.contains(&k.as_str()) || POST_OPTIONAL.contains(&k.as_str())
                }) =>
        {
            obj
        }
        Some(_) => return Err(Error::invalid(format!("{label}.post 格式不正確"))),
    };
    let id = match &obj["id"] {
        Value::String(id) if is_id(id) => id.clone(),
        _ => return Err(Error::invalid(format!("{label}.post.id 格式不正確"))),
    };
    let key = match &obj["key"] {
        Value::String(k) if is_key(k) => k.clone(),
        _ => return Err(Error::invalid(format!("{label}.post.key 格式不正確"))),
    };
    let mut nums = Vec::with_capacity(2);
    for k in ["seconds", "trim"] {
        match non_negative_number(&obj[k]) {
            Some(n) => nums.push(n.clone()),
            None => {
                return Err(Error::invalid(format!("{label}.post.{k} 必須是非負數字")));
            }
        }
    }
    let pitch_points = match obj.get("pitch_points") {
        None => None,
        Some(v) => Some(check_pitch_points(
            v,
            &format!("{label}.post.pitch_points"),
        )?),
    };
    let trim = nums.pop().unwrap_or_else(|| Number::from(0));
    let seconds = nums.pop().unwrap_or_else(|| Number::from(0));
    Ok(Some(Post {
        id,
        key,
        seconds,
        trim,
        pitch_points,
    }))
}

/// Python `_check_fusions`(`tts_text` 也以位置名稱「片段」呼叫,T3)。
pub(crate) fn check_fusions(
    chars: &[char],
    items: Option<&Value>,
    annotations: &[Annotation],
    word_clarity: &[WordClarity],
    label: &str,
) -> Result<Vec<Fusion>> {
    let list = match items {
        None | Some(Value::Null) => return Ok(Vec::new()),
        Some(Value::Array(list)) => list,
        Some(_) => return Err(Error::invalid(format!("{label}.fusions 必須是陣列"))),
    };
    if list.len() > MAX_FUSIONS {
        return Err(Error::invalid(format!(
            "{label}.fusions 每句最多 {MAX_FUSIONS} 組"
        )));
    }
    const REQUIRED: [&str; 4] = ["index", "length", "zhuyin", "source"];
    let mut out: Vec<Fusion> = Vec::with_capacity(list.len());
    for f in list {
        let obj = match f {
            Value::Object(obj)
                if REQUIRED.iter().all(|k| obj.contains_key(*k))
                    && obj
                        .keys()
                        .all(|k| REQUIRED.contains(&k.as_str()) || k == "display") =>
            {
                obj
            }
            _ => {
                return Err(Error::invalid(format!(
                    "{label}.fusions 每項必須恰為 index、length、zhuyin、source 四個欄位(另可選填 display)"
                )));
            }
        };
        let n = match obj["length"].as_i64() {
            Some(n)
                if is_int(&obj["length"])
                    && (FUSION_MIN as i64..=FUSION_MAX as i64).contains(&n) =>
            {
                n as usize
            }
            _ => {
                return Err(Error::invalid(format!(
                    "{label}.fusions 的字數必須是 {FUSION_MIN} 到 {FUSION_MAX}"
                )));
            }
        };
        let i = match non_negative_int(&obj["index"]).and_then(|i| usize::try_from(i).ok()) {
            Some(i) if i.checked_add(n).is_some_and(|e| e <= chars.len()) => i,
            _ => {
                return Err(Error::invalid(format!(
                    "{label}.fusions 的位置超出句子範圍"
                )));
            }
        };
        if !all_hanzi(slice(chars, i, i + n)) {
            return Err(Error::invalid(format!(
                "{label}.fusions 只能合併相鄰的漢字"
            )));
        }
        let source = match &obj["source"] {
            Value::String(s) if FUSION_SOURCES.contains(&s.as_str()) => s.clone(),
            _ => {
                return Err(Error::invalid(format!(
                    "{label}.fusions 的來源只能是 rule、lexicon、manual"
                )));
            }
        };
        let (zhuyin, parts): (String, Vec<&str>) = match &obj["zhuyin"] {
            Value::String(z) => (z.clone(), z.split(' ').collect()),
            _ => (String::new(), Vec::new()),
        };
        if !(1..n).contains(&parts.len()) {
            return Err(Error::invalid(format!(
                "{label}:合音注音的音節數必須少於字數(1 到 {} 個,以半形空白分隔)",
                n - 1
            )));
        }
        for part in &parts {
            check_zhuyin(&Value::String((*part).to_string()))
                .map_err(|e| Error::invalid(format!("{label}:合音{}", first(&e))))?;
        }
        let display = match obj.get("display") {
            None => None,
            Some(Value::String(d))
                if char_len(d) <= FUSION_DISPLAY_MAX && d.chars().all(is_hanzi) =>
            {
                Some(d.clone())
            }
            Some(_) => {
                return Err(Error::invalid(format!(
                    "{label}:合音的顯示字必須是空字串或 1 到 {FUSION_DISPLAY_MAX} 個漢字"
                )));
            }
        };
        out.push(Fusion {
            index: i,
            length: n,
            zhuyin,
            source,
            display,
        });
    }
    out.sort_by_key(|f| f.index);
    if out
        .windows(2)
        .any(|w| w[1].index < w[0].index + w[0].length)
    {
        return Err(Error::invalid(format!("{label}.fusions 的範圍不可重疊")));
    }
    for f in &out {
        let span = f.index..f.index + f.length;
        if annotations.iter().any(|a| span.contains(&a.index)) {
            return Err(Error::invalid(format!(
                "{label}:合音範圍內有聲調標註,請先清除該字的念法"
            )));
        }
        for w in word_clarity {
            let inside = span.clone().filter(|k| w.start <= *k && *k < w.end).count();
            if inside != 0 && inside != f.length {
                return Err(Error::invalid(format!(
                    "{label}:字詞清晰度的範圍必須完整包含或完全不含合音的字"
                )));
            }
        }
    }
    Ok(out)
}

/// Python `_check_fusion_off`:去除重複後依 `(index, length)` 排序。
fn check_fusion_off(chars: &[char], items: Option<&Value>, label: &str) -> Result<Vec<FusionOff>> {
    let list = match items {
        None | Some(Value::Null) => return Ok(Vec::new()),
        Some(Value::Array(list)) if list.len() <= MAX_FUSION_OFF => list,
        Some(_) => {
            return Err(Error::invalid(format!(
                "{label}.fusion_off 必須是最多 {MAX_FUSION_OFF} 筆的陣列"
            )));
        }
    };
    let mut out: Vec<FusionOff> = Vec::with_capacity(list.len());
    for d in list {
        let obj = match d {
            Value::Object(obj) if has_exact_keys(obj, &["index", "length"]) => obj,
            _ => {
                return Err(Error::invalid(format!(
                    "{label}.fusion_off 每項必須恰為 index、length 兩個欄位"
                )));
            }
        };
        let n = obj["length"].as_i64().filter(|n| {
            is_int(&obj["length"]) && (FUSION_MIN as i64..=FUSION_MAX as i64).contains(n)
        });
        let i = non_negative_int(&obj["index"]).and_then(|i| usize::try_from(i).ok());
        let (i, n) = match (i, n) {
            (Some(i), Some(n)) if i.checked_add(n as usize).is_some_and(|e| e <= chars.len()) => {
                (i, n as usize)
            }
            _ => {
                return Err(Error::invalid(format!("{label}.fusion_off 的範圍超出句子")));
            }
        };
        let item = FusionOff {
            index: i,
            length: n,
        };
        if !out.contains(&item) {
            out.push(item);
        }
    }
    out.sort_by_key(|f| (f.index, f.length));
    Ok(out)
}

/// Python `_validate_asset`。
fn validate_asset(
    a: &Value,
    label: &str,
    errors: &mut Vec<String>,
    seen: &mut HashSet<String>,
) -> Option<Asset> {
    let obj = check_keys(
        a,
        &ASSET_KEYS,
        &["id", "kind", "ext", "bytes"],
        label,
        errors,
    )?;
    let mut ok = check_id(&obj["id"], label, errors, seen);
    let (kind, exts) = match &obj["kind"] {
        Value::String(k) => match ASSET_EXTS.iter().find(|(name, _)| name == k) {
            Some((name, exts)) => (*name, *exts),
            None => {
                errors.push(format!("{label}.kind 只能是 image、audio、text"));
                return None;
            }
        },
        _ => {
            errors.push(format!("{label}.kind 只能是 image、audio、text"));
            return None;
        }
    };
    let ext = match &obj["ext"] {
        Value::String(e) if exts.contains(&e.as_str()) => Some(e.clone()),
        _ => {
            errors.push(format!("{label}.ext 不在白名單內"));
            ok = false;
            None
        }
    };
    let bytes = non_negative_int(&obj["bytes"]);
    if bytes.is_none() {
        errors.push(format!("{label}.bytes 必須是非負整數"));
        ok = false;
    }
    let label_value = match obj.get("label") {
        None => Some(None),
        Some(Value::Null) => Some(Some(None)),
        Some(Value::String(s))
            if char_len(s) <= MAX_LABEL
                && !s.chars().any(|c| u32::from(c) < 32 || u32::from(c) == 127) =>
        {
            Some(Some(Some(s.clone())))
        }
        Some(_) => None,
    };
    if label_value.is_none() {
        errors.push(format!(
            "{label}.label 必須是 {MAX_LABEL} 字以內、不含控制字元的文字"
        ));
        ok = false;
    }
    if !ok {
        return None;
    }
    Some(Asset {
        id: obj["id"].as_str()?.to_string(),
        kind: kind.to_string(),
        ext: ext?,
        bytes: bytes?,
        label: label_value?,
    })
}

/// Python `_validate_image`。`start` / `end` 四捨五入到 0.1;超過 28 位數(Python `InvalidOperation`)時回
/// 該欄位的型別錯誤訊息(D-5,T2 補記)。
fn validate_image(
    m: &Value,
    label: &str,
    errors: &mut Vec<String>,
    seen: &mut HashSet<String>,
    image_assets: &HashSet<String>,
) -> Option<Image> {
    let obj = check_keys(m, &IMAGE_KEYS, &["id", "asset", "start"], label, errors)?;
    let mut ok = check_id(&obj["id"], label, errors, seen);
    let asset = match &obj["asset"] {
        Value::String(a) if image_assets.contains(a) => Some(a.clone()),
        _ => {
            errors.push(format!("{label}.asset 不是本專案的圖片素材"));
            ok = false;
            None
        }
    };
    let start = non_negative_number(&obj["start"])
        .and_then(|n| round_half_up(n, PAUSE_STEP))
        .map(|r| r.to_f64());
    let Some(start) = start else {
        errors.push(format!("{label}.start 必須是非負數字"));
        return None;
    };
    let end = match obj.get("end") {
        None | Some(Value::Null) => None,
        Some(Value::Number(n)) => match round_half_up(n, PAUSE_STEP) {
            Some(r) => Some(r.to_f64()),
            None => {
                errors.push(format!("{label}.end 必須是數字或 null"));
                return None;
            }
        },
        Some(_) => {
            errors.push(format!("{label}.end 必須是數字或 null"));
            return None;
        }
    };
    if let Some(e) = end
        && e <= start
    {
        errors.push(format!("{label}.end 必須大於 start"));
        ok = false;
    }
    if !ok {
        return None;
    }
    Some(Image {
        id: obj["id"].as_str()?.to_string(),
        asset: asset?,
        start,
        end,
    })
}
