//! 專案資料型別(Python `Project` 與 `validate` 的輸出;CHG-20261128-01 T2,代決 6)。
//!
//! - 欄位宣告順序 = Python `validate` 建構 dict 的順序,寫出([`crate::pyjson::dump_bytes`])時依此順序。
//!   `audio`、`post`、素材在 Python 是 `dict(…)` 複製(保留輸入檔的鍵順序),Rust 以 Python 程式自己產生時的順序
//!   寫出:`audio` 為 `id`、`seconds`、`key`;`post` 為 `id`、`key`、`seconds`、`trim`、`pitch_points`;
//!   素材為 `id`、`kind`、`ext`、`bytes`、`label`(行為差異 D-6)。
//! - 原樣保留的數值(`audio.seconds`、`post.seconds`、`post.trim`)以 `serde_json::Number` 保存,
//!   不改整數 / 浮點表示;其他數值為正規化後的浮點或整數。
//! - 「沒有這個鍵」與「值為 `null`」在 Python 寫出結果不同的欄位:`audio.key`、`post.pitch_points`、
//!   合音的 `display` 為 `Option`(`None` = 沒有這個鍵);素材的 `label` 為 `Option<Option<String>>`
//!   (`None` = 沒有這個鍵、`Some(None)` = `null`)。
//!
//! [`Project::to_dict`] 對應 Python `Project.to_dict`;寫出一律先驗證([`Project::dump_bytes`] =
//! `_dump(validate(self.to_dict()))`,Python `save` 的做法),不提供未驗證的寫出。

use serde::Serialize;
use serde_json::{Map, Number, Value};

use crate::constants::{DEFAULTS, SCHEMA_VERSION};
use crate::validate::{dump_validated, validate};
use crate::{Error, Result};

/// 語速與停頓的專案預設(Python `defaults`,鍵順序同 `DEFAULTS`)。
#[derive(Debug, Clone, Copy, PartialEq, Serialize)]
pub struct Defaults {
    pub speed: f64,
    pub pause_comma: f64,
    pub pause_sentence: f64,
    pub pause_paragraph: f64,
}

impl Default for Defaults {
    /// Python `DEFAULTS`:語速 1.15、逗號 0.0、句末 0.4、段落 0.8。
    fn default() -> Self {
        let [speed, pause_comma, pause_sentence, pause_paragraph] = DEFAULTS.map(|(_, v)| v);
        Self {
            speed,
            pause_comma,
            pause_sentence,
            pause_paragraph,
        }
    }
}

/// 注音標註(`{"index", "zhuyin"}`)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Annotation {
    pub index: usize,
    pub zhuyin: String,
}

/// 片段的音檔(`{"id", "seconds", "key"?}`)。
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Audio {
    pub id: String,
    pub seconds: Number,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub key: Option<String>,
}

/// 字詞清晰度(`{"start", "end", "value"}`)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct WordClarity {
    pub start: usize,
    pub end: usize,
    pub value: i64,
}

/// 語調關鍵點(`{"pos", "semi"}`)。
#[derive(Debug, Clone, Copy, PartialEq, Serialize)]
pub struct PitchPoint {
    pub pos: f64,
    pub semi: f64,
}

/// 語調(`{"points", "roundness"}`)。
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Pitch {
    pub points: Vec<PitchPoint>,
    pub roundness: Option<i64>,
}

/// 後製結果(`{"id", "key", "seconds", "trim", "pitch_points"?}`)。
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Post {
    pub id: String,
    pub key: String,
    pub seconds: Number,
    pub trim: Number,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pitch_points: Option<Vec<[f64; 2]>>,
}

/// 合音(`{"index", "length", "zhuyin", "source", "display"?}`)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Fusion {
    pub index: usize,
    pub length: usize,
    pub zhuyin: String,
    pub source: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub display: Option<String>,
}

/// 停用合音的位置(`{"index", "length"}`)。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct FusionOff {
    pub index: usize,
    pub length: usize,
}

/// 片段(鍵順序同 Python `SEGMENT_KEYS`)。
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Segment {
    pub id: String,
    pub text: String,
    /// 切點種類(JSON 鍵 `break`)。
    #[serde(rename = "break")]
    pub brk: String,
    pub pause_after: Option<f64>,
    pub speed: Option<f64>,
    pub annotations: Vec<Annotation>,
    pub audio: Option<Audio>,
    pub connect: Option<i64>,
    pub clarity: Option<i64>,
    pub word_clarity: Vec<WordClarity>,
    pub pitch: Option<Pitch>,
    pub post: Option<Post>,
    pub fusions: Vec<Fusion>,
    pub fusion_off: Vec<FusionOff>,
}

impl Segment {
    /// 只有 id 與文字的片段(其他欄位同 Python 驗證後的預設:`break` 為 `end`,其餘為 `null` 或空陣列)。
    pub fn new(id: impl Into<String>, text: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            text: text.into(),
            brk: "end".to_string(),
            pause_after: None,
            speed: None,
            annotations: Vec::new(),
            audio: None,
            connect: None,
            clarity: None,
            word_clarity: Vec::new(),
            pitch: None,
            post: None,
            fusions: Vec::new(),
            fusion_off: Vec::new(),
        }
    }
}

/// 畫面軌的圖片(`{"id", "asset", "start", "end"}`)。
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Image {
    pub id: String,
    pub asset: String,
    pub start: f64,
    pub end: Option<f64>,
}

/// 素材(`{"id", "kind", "ext", "bytes", "label"?}`)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Asset {
    pub id: String,
    pub kind: String,
    pub ext: String,
    pub bytes: u64,
    /// `None` = 沒有這個鍵;`Some(None)` = `null`。
    #[serde(skip_serializing_if = "Option::is_none")]
    pub label: Option<Option<String>>,
}

/// 專案(Python `Project`;欄位順序 = 寫出的頂層鍵順序 `TOP_KEYS`)。
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Project {
    pub schema_version: i64,
    pub name: String,
    pub script: String,
    pub defaults: Defaults,
    pub segments: Vec<Segment>,
    pub images: Vec<Image>,
    pub assets: Vec<Asset>,
}

impl Project {
    /// Python `Project(name=…)`:空文稿、沒有片段 / 畫面 / 素材、`DEFAULTS`、schema 2。
    pub fn new(name: impl Into<String>) -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            name: name.into(),
            script: String::new(),
            defaults: Defaults::default(),
            segments: Vec::new(),
            images: Vec::new(),
            assets: Vec::new(),
        }
    }

    /// Python `Project.from_dict`(= [`validate`])。
    pub fn from_dict(data: &Value) -> Result<Self> {
        validate(data)
    }

    /// Python `Project.to_dict`:交給 [`validate`] 的 JSON 值。
    ///
    /// 非有限浮點(`NaN`、`±inf`)不寫成 `null`(那會被當成「沒有設定」而通過驗證),而是寫成字串
    /// `"NaN"` / `"Infinity"` / `"-Infinity"`,讓 [`validate`] 以該欄位「必須是數字」類訊息拒絕——
    /// 與 Python 對 `float("nan")` 等值的訊息相同(Python `_is_number` 為假)。
    pub fn to_dict(&self) -> Value {
        obj(vec![
            ("schema_version", Value::from(self.schema_version)),
            ("name", Value::from(self.name.as_str())),
            ("script", Value::from(self.script.as_str())),
            ("defaults", defaults_value(&self.defaults)),
            (
                "segments",
                Value::Array(self.segments.iter().map(segment_value).collect()),
            ),
            (
                "images",
                Value::Array(self.images.iter().map(image_value).collect()),
            ),
            (
                "assets",
                Value::Array(self.assets.iter().map(asset_value).collect()),
            ),
        ])
    }

    /// Python `_dump(validate(self.to_dict()))`:驗證後以 Python 相同位元組寫出;不合法(含超過 5 MB)回錯誤。
    pub fn dump_bytes(&self) -> Result<Vec<u8>> {
        dump_validated(&self.to_dict())
    }
}

impl TryFrom<&Value> for Project {
    type Error = Error;

    fn try_from(data: &Value) -> Result<Self> {
        validate(data)
    }
}

fn obj(entries: Vec<(&str, Value)>) -> Value {
    Value::Object(
        entries
            .into_iter()
            .map(|(k, v)| (k.to_string(), v))
            .collect::<Map<String, Value>>(),
    )
}

/// 浮點 → JSON 值;非有限值寫成字串(見 [`Project::to_dict`])。
fn float(x: f64) -> Value {
    match Number::from_f64(x) {
        Some(n) => Value::Number(n),
        None if x.is_nan() => Value::from("NaN"),
        None if x > 0.0 => Value::from("Infinity"),
        None => Value::from("-Infinity"),
    }
}

fn opt_float(x: Option<f64>) -> Value {
    x.map_or(Value::Null, float)
}

fn defaults_value(d: &Defaults) -> Value {
    obj(vec![
        ("speed", float(d.speed)),
        ("pause_comma", float(d.pause_comma)),
        ("pause_sentence", float(d.pause_sentence)),
        ("pause_paragraph", float(d.pause_paragraph)),
    ])
}

fn segment_value(s: &Segment) -> Value {
    let annotations = s
        .annotations
        .iter()
        .map(|a| {
            obj(vec![
                ("index", Value::from(a.index)),
                ("zhuyin", Value::from(a.zhuyin.as_str())),
            ])
        })
        .collect();
    let audio = s.audio.as_ref().map_or(Value::Null, |a| {
        let mut e = vec![
            ("id", Value::from(a.id.as_str())),
            ("seconds", Value::Number(a.seconds.clone())),
        ];
        if let Some(k) = &a.key {
            e.push(("key", Value::from(k.as_str())));
        }
        obj(e)
    });
    let word_clarity = s
        .word_clarity
        .iter()
        .map(|w| {
            obj(vec![
                ("start", Value::from(w.start)),
                ("end", Value::from(w.end)),
                ("value", Value::from(w.value)),
            ])
        })
        .collect();
    let pitch = s.pitch.as_ref().map_or(Value::Null, |p| {
        let points = p
            .points
            .iter()
            .map(|q| obj(vec![("pos", float(q.pos)), ("semi", float(q.semi))]))
            .collect();
        obj(vec![
            ("points", Value::Array(points)),
            ("roundness", Value::from(p.roundness)),
        ])
    });
    let post = s.post.as_ref().map_or(Value::Null, |p| {
        let mut e = vec![
            ("id", Value::from(p.id.as_str())),
            ("key", Value::from(p.key.as_str())),
            ("seconds", Value::Number(p.seconds.clone())),
            ("trim", Value::Number(p.trim.clone())),
        ];
        if let Some(points) = &p.pitch_points {
            let points = points
                .iter()
                .map(|[a, b]| Value::Array(vec![float(*a), float(*b)]))
                .collect();
            e.push(("pitch_points", Value::Array(points)));
        }
        obj(e)
    });
    let fusions = s
        .fusions
        .iter()
        .map(|f| {
            let mut e = vec![
                ("index", Value::from(f.index)),
                ("length", Value::from(f.length)),
                ("zhuyin", Value::from(f.zhuyin.as_str())),
                ("source", Value::from(f.source.as_str())),
            ];
            if let Some(d) = &f.display {
                e.push(("display", Value::from(d.as_str())));
            }
            obj(e)
        })
        .collect();
    let fusion_off = s
        .fusion_off
        .iter()
        .map(|f| {
            obj(vec![
                ("index", Value::from(f.index)),
                ("length", Value::from(f.length)),
            ])
        })
        .collect();
    obj(vec![
        ("id", Value::from(s.id.as_str())),
        ("text", Value::from(s.text.as_str())),
        ("break", Value::from(s.brk.as_str())),
        ("pause_after", opt_float(s.pause_after)),
        ("speed", opt_float(s.speed)),
        ("annotations", Value::Array(annotations)),
        ("audio", audio),
        ("connect", Value::from(s.connect)),
        ("clarity", Value::from(s.clarity)),
        ("word_clarity", Value::Array(word_clarity)),
        ("pitch", pitch),
        ("post", post),
        ("fusions", Value::Array(fusions)),
        ("fusion_off", Value::Array(fusion_off)),
    ])
}

fn image_value(m: &Image) -> Value {
    obj(vec![
        ("id", Value::from(m.id.as_str())),
        ("asset", Value::from(m.asset.as_str())),
        ("start", float(m.start)),
        ("end", opt_float(m.end)),
    ])
}

fn asset_value(a: &Asset) -> Value {
    let mut e = vec![
        ("id", Value::from(a.id.as_str())),
        ("kind", Value::from(a.kind.as_str())),
        ("ext", Value::from(a.ext.as_str())),
        ("bytes", Value::from(a.bytes)),
    ];
    if let Some(label) = &a.label {
        e.push(("label", label.as_deref().map_or(Value::Null, Value::from)));
    }
    obj(e)
}
