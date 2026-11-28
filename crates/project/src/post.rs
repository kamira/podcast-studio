//! 後製參數與後製鍵、實際語速與停頓(`project.py` 的 `post_params`、`is_default_post`、`post_key`、
//! `is_post_fresh`、`effective_speed`、`effective_pause`;CHG-20261128-01 T3,代決 18)。
//!
//! 片段參數的前提與 Python 呼叫端相同:片段來自 [`crate::validate::validate`](見 [`crate::text`] 模組說明)。
//!
//! 後製鍵 = SHA-256(Python `json.dumps([audio.id, POST_VERSION, 裁切參數, connect, clarity, word_clarity, pitch]`
//! 加上演算法標記`, ensure_ascii=False, sort_keys=True)` 的 UTF-8 位元組)的小寫十六進位;寫法用
//! [`crate::pyjson::dumps_sorted`](浮點依 Python `repr`),雜湊用自寫的 [`crate::sha256`]。演算法標記只在用到時
//! 附加為陣列最後一項:清晰度 < 100% 或有字詞清晰度時含 `"clarity": 2`、有語調時含 `"pitch": 2`;都沒有時不附加,
//! 鍵與修訂前相同。語速、停頓不在鍵內。

use serde_json::{Map, Value, json};

use crate::constants::{BREAK_PAUSE, POST_ALGO, POST_VERSION, PROSODY_DEFAULTS, TRIM_PARAMS};
use crate::model::{Audio, Defaults, Project, Segment, WordClarity};
use crate::pyjson::dumps_sorted;
use crate::sha256::hex_digest;
use crate::{Error, Result};

/// 自錄音檔(沒有 `audio.key`)或沒有音檔的片段求後製鍵時的訊息。
pub const NOT_SYNTHESIZED: &str = "只有合成的片段有後製(自錄音檔不做後製)";

/// [`post_params`] 的語調(Python `{"points": [[pos, semi]], "roundness"}`;`roundness` 為 `null` 時取 50)。
#[derive(Debug, Clone, PartialEq)]
pub struct PostPitch {
    pub points: Vec<[f64; 2]>,
    pub roundness: i64,
}

/// Python `post_params(segment)` 的結果(`trim` 一律是 [`TRIM_PARAMS`],見 [`PostParams::to_value`])。
#[derive(Debug, Clone, PartialEq)]
pub struct PostParams {
    pub connect: i64,
    pub clarity: i64,
    pub word_clarity: Vec<WordClarity>,
    pub pitch: Option<PostPitch>,
}

fn prosody_default(key: &str) -> i64 {
    PROSODY_DEFAULTS
        .iter()
        .find(|(k, _)| *k == key)
        .map_or(0, |(_, v)| *v)
}

fn pairs(entries: &[(&str, i64)]) -> Value {
    Value::Object(
        entries
            .iter()
            .map(|(k, v)| ((*k).to_string(), Value::from(*v)))
            .collect::<Map<String, Value>>(),
    )
}

fn pitch_value(pitch: Option<&PostPitch>) -> Value {
    pitch.map_or(
        Value::Null,
        |p| json!({"points": p.points, "roundness": p.roundness}),
    )
}

impl PostParams {
    /// Python `post_params` 回傳的 dict:`{"trim", "connect", "clarity", "word_clarity": [{"start", "end",
    /// "value"}], "pitch"}`。
    pub fn to_value(&self) -> Value {
        let word_clarity: Vec<Value> = self
            .word_clarity
            .iter()
            .map(|w| json!({"start": w.start, "end": w.end, "value": w.value}))
            .collect();
        json!({
            "trim": pairs(&TRIM_PARAMS),
            "connect": self.connect,
            "clarity": self.clarity,
            "word_clarity": word_clarity,
            "pitch": pitch_value(self.pitch.as_ref()),
        })
    }
}

/// Python `post_params(segment)`:後製參數,`null` 一律取程式常數(連音 0、清晰度 100、圓弧 50)。
pub fn post_params(segment: &Segment) -> PostParams {
    PostParams {
        connect: segment
            .connect
            .unwrap_or_else(|| prosody_default("connect")),
        clarity: segment
            .clarity
            .unwrap_or_else(|| prosody_default("clarity")),
        word_clarity: segment.word_clarity.clone(),
        pitch: segment.pitch.as_ref().map(|p| PostPitch {
            points: p.points.iter().map(|q| [q.pos, q.semi]).collect(),
            roundness: p.roundness.unwrap_or_else(|| prosody_default("roundness")),
        }),
    }
}

/// Python `is_default_post(segment)`:連音 0%、清晰度 100%、沒有字詞清晰度、沒有語調(後製結果等於裁切結果)。
pub fn is_default_post(segment: &Segment) -> bool {
    let p = post_params(segment);
    p.connect == 0 && p.clarity == 100 && p.word_clarity.is_empty() && p.pitch.is_none()
}

/// 合成片段(`audio` 帶 `key`)的音檔 id。
fn synthesized_audio_id(segment: &Segment) -> Option<&str> {
    match &segment.audio {
        Some(Audio {
            id, key: Some(_), ..
        }) => Some(id),
        _ => None,
    }
}

fn algo(name: &str) -> i64 {
    POST_ALGO
        .iter()
        .find(|(k, _)| *k == name)
        .map_or(0, |(_, v)| *v)
}

/// Python `post_key(segment)`:後製鍵(64 碼小寫十六進位)。只對合成片段有意義,其他回 `Invalid`
/// 「只有合成的片段有後製(自錄音檔不做後製)」。
pub fn post_key(segment: &Segment) -> Result<String> {
    let id = synthesized_audio_id(segment).ok_or_else(|| Error::invalid(NOT_SYNTHESIZED))?;
    let p = post_params(segment);
    let word_clarity: Vec<Value> = p
        .word_clarity
        .iter()
        .map(|w| json!([w.start, w.end, w.value]))
        .collect();
    let mut parts = vec![
        Value::from(id),
        Value::from(POST_VERSION),
        pairs(&TRIM_PARAMS),
        Value::from(p.connect),
        Value::from(p.clarity),
        Value::Array(word_clarity),
        pitch_value(p.pitch.as_ref()),
    ];
    let mut marks: Vec<(&str, i64)> = Vec::new();
    if p.clarity < 100 || !p.word_clarity.is_empty() {
        marks.push(("clarity", algo("clarity")));
    }
    if p.pitch.is_some() {
        marks.push(("pitch", algo("pitch")));
    }
    if !marks.is_empty() {
        parts.push(pairs(&marks));
    }
    Ok(hex_digest(dumps_sorted(&Value::Array(parts)).as_bytes()))
}

/// Python `is_post_fresh(segment)`:片段的 `post` 與目前參數相符(只比對資料,不檢查檔案是否存在)。
pub fn is_post_fresh(segment: &Segment) -> bool {
    match (&segment.post, synthesized_audio_id(segment)) {
        (Some(post), Some(_)) => post_key(segment).is_ok_and(|k| k == post.key),
        _ => false,
    }
}

/// Python `effective_speed(project, segment)`:片段實際語速——`speed` 為 `null` 時用專案的語速預設。
pub fn effective_speed(project: &Project, segment: &Segment) -> f64 {
    segment.speed.unwrap_or(project.defaults.speed)
}

fn default_pause(defaults: &Defaults, key: &str) -> Option<f64> {
    match key {
        "pause_comma" => Some(defaults.pause_comma),
        "pause_sentence" => Some(defaults.pause_sentence),
        "pause_paragraph" => Some(defaults.pause_paragraph),
        _ => None,
    }
}

/// Python `effective_pause(project, segment)`:片段之後的停頓——`pause_after` 為 `null` 時依 `break` 取停頓預設
/// (`sentence` → `pause_sentence`、`paragraph` → `pause_paragraph`、`forced` → `pause_comma`),其他(文末)為 0。
pub fn effective_pause(project: &Project, segment: &Segment) -> f64 {
    if let Some(p) = segment.pause_after {
        return p;
    }
    BREAK_PAUSE
        .iter()
        .find(|(brk, _)| *brk == segment.brk)
        .and_then(|(_, key)| default_pause(&project.defaults, key))
        .unwrap_or(0.0)
}
