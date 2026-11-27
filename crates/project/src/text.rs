//! 分句、合成文字與合音(`project.py` 的 `split_segments`、`escape_text`、`tts_text`、`fused_out`、
//! `model_units`、`shift_marks`;CHG-20261128-01 T3,代決 8)。
//!
//! 長度與索引一律以 Unicode 碼位計(Python `len` / 索引),空白以 Python 3.11 的 `str.strip()` 判定
//! ([`crate::pystr`])。
//!
//! 片段參數(`&Segment`)的前提與 Python 呼叫端相同:片段來自 [`crate::validate::validate`](或
//! [`crate::Project::from_dict`])。[`tts_text`] 與 Python 相同,先重新檢查標註與合音(片段的欄位是公開的,
//! 呼叫端可能自行組出);其餘函式不檢查,未驗證的資料不會 panic,但結果只在已驗證的片段上與 Python 相同。
//!
//! 與 Python 的 API 形態差異(行為差異 D-12 T3 補記):`split_segments` 的強制停頓位置為 `&[i64]`——Python 對
//! 非整數、布林回「強制停頓位置超出文稿範圍」,Rust 由型別排除,從 JSON 取值的呼叫端自行檢查;文稿不是字串、
//! 片段不是物件、片段文字不是字串(Python「文稿必須是文字」「片段格式不正確」「片段文字是空的」的一部分)
//! 同樣由型別排除。

use std::collections::{BTreeSet, HashMap, HashSet};

use serde::Serialize;

use crate::constants::{
    CLOSERS, MAX_SCRIPT, MAX_SEGMENT_TEXT, MAX_SEGMENTS, NEWLINES, SENTENCE_ENDERS,
};
use crate::model::{Fusion, FusionOff, Segment};
use crate::pystr::{char_len, is_hanzi, py_lstrip, py_strip};
use crate::validate::{check_annotations, check_fusions};
use crate::{Error, Result};

/// `tts_text` 重新檢查標註與合音時,訊息中的位置名稱(Python `"片段"`)。
const TTS_LABEL: &str = "片段";

/// 分句的結果(Python `{"text", "break", "start", "end"}`;`start` / `end` 為原文的碼位索引,
/// `text` = 原文 `[start, end)`)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Piece {
    pub text: String,
    /// 切點種類(JSON 鍵 `break`):`sentence`、`paragraph`、`forced`、`end`。
    #[serde(rename = "break")]
    pub brk: String,
    pub start: usize,
    pub end: usize,
}

/// Python `_emit_piece`:`[start, end)` 去除前後空白後非空才加入。
fn emit_piece(pieces: &mut Vec<Piece>, chars: &[char], start: usize, end: usize, kind: &str) {
    let raw: String = chars[start..end].iter().collect();
    let stripped = py_strip(&raw);
    if stripped.is_empty() {
        return;
    }
    let s = start + (char_len(&raw) - char_len(py_lstrip(&raw)));
    pieces.push(Piece {
        text: stripped.to_string(),
        brk: kind.to_string(),
        start: s,
        end: s + char_len(stripped),
    });
}

/// Python `split_segments(text, forced_breaks)`:把文稿切成語音片段。
///
/// 句尾標點(`。！？!?；;`)為切點,緊接的句尾標點與右引號、右括號併入同一句(`sentence`);換行為切點
/// (`paragraph`;句尾標點後接換行時,該句升為 `paragraph`);`forced_breaks` 中的原文索引在該字之後切開
/// (`forced`);文末沒有標點的殘句為 `end`。空白片段略過。
///
/// 錯誤(順序同 Python):文稿超過 50000 字;強制停頓位置不在 `0..字數`;任一片段超過 500 字(回報第一個);
/// 片段超過 2000 個。
pub fn split_segments(text: &str, forced_breaks: &[i64]) -> Result<Vec<Piece>> {
    let chars: Vec<char> = text.chars().collect();
    let n = chars.len();
    if n > MAX_SCRIPT {
        return Err(Error::invalid(format!("文稿超過 {MAX_SCRIPT} 字")));
    }
    let mut forced = HashSet::new();
    for &b in forced_breaks {
        match usize::try_from(b) {
            Ok(b) if b < n => {
                forced.insert(b);
            }
            _ => return Err(Error::invalid("強制停頓位置超出文稿範圍")),
        }
    }
    let mut pieces: Vec<Piece> = Vec::new();
    let (mut start, mut i) = (0, 0);
    while i < n {
        let ch = chars[i];
        if NEWLINES.contains(ch) {
            let before = pieces.len();
            emit_piece(&mut pieces, &chars, start, i, "paragraph");
            if pieces.len() == before {
                let blank = py_strip(&chars[start..i].iter().collect::<String>()).is_empty();
                if let Some(last) = pieces.last_mut().filter(|_| blank) {
                    last.brk = "paragraph".to_string();
                }
            }
            i += 1;
            start = i;
            continue;
        }
        if SENTENCE_ENDERS.contains(ch) {
            let mut j = i + 1;
            while j < n && (SENTENCE_ENDERS.contains(chars[j]) || CLOSERS.contains(chars[j])) {
                j += 1;
            }
            emit_piece(&mut pieces, &chars, start, j, "sentence");
            start = j;
            i = j;
            continue;
        }
        if forced.contains(&i) {
            emit_piece(&mut pieces, &chars, start, i + 1, "forced");
            start = i + 1;
        }
        i += 1;
    }
    emit_piece(&mut pieces, &chars, start, n, "end");
    if let Some(k) = pieces
        .iter()
        .position(|p| char_len(&p.text) > MAX_SEGMENT_TEXT)
    {
        return Err(Error::invalid(format!(
            "第 {} 句超過 {MAX_SEGMENT_TEXT} 字,請加句尾標點或強制停頓",
            k + 1
        )));
    }
    if pieces.len() > MAX_SEGMENTS {
        return Err(Error::invalid(format!("片段超過 {MAX_SEGMENTS} 個")));
    }
    Ok(pieces)
}

/// Python `escape_text`:BreezyVoice 標註語法的 `[`、`]` 換成全形 `［`、`］`(一對一,索引不變)。
pub fn escape_text(text: &str) -> String {
    text.replace('[', "［").replace(']', "］")
}

/// 合音帶顯示字(Python `f.get("display")` 為真:有這個鍵且不是空字串)。
fn display_of(f: &Fusion) -> Option<&str> {
    f.display.as_deref().filter(|d| !d.is_empty())
}

/// Python `tts_text(segment)`:合成文字。
///
/// 原文跳脫後,在每個標註的字後插入 `[:注音]`;合音(注音路線)前 k 字依序帶 `[:音節]`、刪除其餘字;帶顯示字的
/// 合音整段換成顯示字、不加注音。與 Python 相同,先以 `"片段"` 為位置名稱重新檢查標註與合音
/// (訊息與 Python `_check_annotations`、`_check_fusions` 逐字相同);片段文字是空字串時回「片段文字是空的」。
pub fn tts_text(segment: &Segment) -> Result<String> {
    if segment.text.is_empty() {
        return Err(Error::invalid("片段文字是空的"));
    }
    let chars: Vec<char> = segment.text.chars().collect();
    let value = segment.to_value();
    let checked = check_annotations(&chars, value.get("annotations"), TTS_LABEL)?;
    let fusions = check_fusions(
        &chars,
        value.get("fusions"),
        &checked,
        &segment.word_clarity,
        TTS_LABEL,
    )?;
    let anns: HashMap<usize, &str> = checked
        .iter()
        .map(|a| (a.index, a.zhuyin.as_str()))
        .collect();
    let mut fus: HashMap<usize, &str> = HashMap::new();
    let mut shown: HashMap<usize, &str> = HashMap::new();
    let mut skip: HashSet<usize> = HashSet::new();
    for f in &fusions {
        if let Some(d) = display_of(f) {
            shown.insert(f.index, d);
            skip.extend(f.index..f.index + f.length);
            continue;
        }
        let parts: Vec<&str> = f.zhuyin.split(' ').collect();
        for j in 0..f.length {
            match parts.get(j) {
                Some(p) => {
                    fus.insert(f.index + j, p);
                }
                None => {
                    skip.insert(f.index + j);
                }
            }
        }
    }
    let mut out = String::new();
    for (i, ch) in escape_text(&segment.text).chars().enumerate() {
        if let Some(d) = shown.get(&i) {
            out.push_str(&escape_text(d));
        }
        if skip.contains(&i) {
            continue;
        }
        out.push(ch);
        if let Some(z) = fus.get(&i).or_else(|| anns.get(&i)) {
            out.push_str("[:");
            out.push_str(z);
            out.push(']');
        }
    }
    Ok(out)
}

/// Python `fused_out(segment)`:合音範圍內不送模型的原字索引——注音路線 n 字合成 k 個音節時為後 n − k 字;
/// 帶顯示字時整個範圍都不送。
pub fn fused_out(segment: &Segment) -> BTreeSet<usize> {
    let mut out = BTreeSet::new();
    for f in &segment.fusions {
        let k = if display_of(f).is_some() {
            0
        } else {
            f.zhuyin.split(' ').count()
        };
        out.extend(f.index.saturating_add(k)..f.index.saturating_add(f.length));
    }
    out
}

/// Python `model_units(segment)`:送模型的每個漢字依序對應的原文索引——合音範圍外的原字、注音路線留下的前 k 字,
/// 以及顯示字(第 j 個顯示字對應到合音範圍內第 min(j, length − 1) 個字)。
pub fn model_units(segment: &Segment) -> Vec<usize> {
    let mut shown: HashMap<usize, &Fusion> = HashMap::new();
    for f in segment.fusions.iter().filter(|f| display_of(f).is_some()) {
        shown.insert(f.index, f);
    }
    let skip = fused_out(segment);
    let mut out = Vec::new();
    for (i, ch) in segment.text.chars().enumerate() {
        if let Some(f) = shown.get(&i) {
            let last = f.length.saturating_sub(1);
            let n = f.display.as_deref().map_or(0, char_len);
            out.extend((0..n).map(|j| i.saturating_add(j.min(last))));
        }
        if is_hanzi(ch) && !skip.contains(&i) {
            out.push(i);
        }
    }
    out
}

/// [`shift_marks`] 的結果(Python `{"fusions", "fusion_off", "cleared"}`)。
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ShiftedMarks {
    pub fusions: Vec<Fusion>,
    pub fusion_off: Vec<FusionOff>,
    /// 被清除的合音筆數。
    pub cleared: usize,
}

/// Python `_changed_range(old, new)`:以共同前綴與共同後綴求出改動範圍——(舊文字中的起點, 終點)。
fn changed_range(old: &[char], new: &[char]) -> (usize, usize) {
    let common = old.len().min(new.len());
    let mut p = 0;
    while p < common && old[p] == new[p] {
        p += 1;
    }
    let mut q = 0;
    while q < common - p && old[old.len() - 1 - q] == new[new.len() - 1 - q] {
        q += 1;
    }
    (p, old.len() - q)
}

/// Python `_shift` 對單一項目的判定:改動範圍之前的保留、之後的依長度差位移、重疊者清除(`None`)。
fn shifted_index(
    i: usize,
    n: usize,
    (a, b): (usize, usize),
    old_len: usize,
    new_len: usize,
) -> Option<usize> {
    let end = i.saturating_add(n);
    if end <= a && !(a == b && end > a) {
        Some(i)
    } else if i >= b && (a < b || i >= a) {
        // i ≥ b = 舊字數 − 共同後綴,且新字數 ≥ 共同後綴,所以 i + 新字數 − 舊字數 ≥ 0
        Some(i.saturating_add(new_len).saturating_sub(old_len))
    } else {
        None
    }
}

/// Python `shift_marks(segment, old_text, new_text)`:句子文字由 `old_text` 改成 `new_text` 時調整合音狀態——
/// 改動範圍之前的紀錄不動、之後的依位移調整、與改動範圍重疊者清除。
pub fn shift_marks(segment: &Segment, old_text: &str, new_text: &str) -> ShiftedMarks {
    let old: Vec<char> = old_text.chars().collect();
    let new: Vec<char> = new_text.chars().collect();
    let range = changed_range(&old, &new);
    let fusions: Vec<Fusion> = segment
        .fusions
        .iter()
        .filter_map(|f| {
            shifted_index(f.index, f.length, range, old.len(), new.len())
                .map(|index| Fusion { index, ..f.clone() })
        })
        .collect();
    let fusion_off = segment
        .fusion_off
        .iter()
        .filter_map(|d| {
            shifted_index(d.index, d.length, range, old.len(), new.len())
                .map(|index| FusionOff { index, ..*d })
        })
        .collect();
    ShiftedMarks {
        cleared: segment.fusions.len() - fusions.len(),
        fusions,
        fusion_off,
    }
}
