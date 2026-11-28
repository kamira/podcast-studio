//! L1:後製參數與後製鍵、實際語速與停頓(CHG-20261128-01 T3;`project.py` 的 `post_params`、`is_default_post`、
//! `post_key`、`is_post_fresh`、`effective_speed`、`effective_pause`)。移植 `tests/test_project.py` 的後製案例:
//! 修訂前的固定鍵(`OLD_KEYS`)、演算法標記只附用到的部分、語速與停頓不在鍵內、`null` 與常數同義。
//! 對應 `features/project_rust.feature`「後製鍵與 Python 相同」(大量變化輸入對 Python `hashlib` 的比對見
//! `crates/parity/fixtures/project/post_key.json`)。

use project::Project;
use project::model::Segment;
use project::post::{
    NOT_SYNTHESIZED, effective_pause, effective_speed, is_default_post, is_post_fresh, post_key,
    post_params,
};
use project::pystr::is_key;
use project::sha256::hex_digest;
use project::validate::validate;
use serde_json::{Value, json};

const KEY_A: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const KEY_B: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

/// Python `tests/test_project.py` 的 `OLD_KEYS`:修訂前的公式(不附演算法標記)算出的固定值。
const OLD_TRIM: &str = "a5b2f459ffc3121aac89c686a3457996dea02d342f48af0170bfafa55fb331eb";
const OLD_CONNECT50: &str = "9ea0ec0dbbdf0067580762b11d4a3f5549948ce3f2d70f2d3db1fd46f75bcc96";
const OLD_CLARITY50: &str = "e3084a4d56a48ee60d9b24dcf4f34269ad555d6bdbdee5deeb8b45fafcfdeffd";
const OLD_WC: &str = "b821890912bbd68300bc293b7cd12f7163a28db8f29a10d102b8383b31264a4e";
const OLD_PITCH: &str = "1900fad4a3651feff3e23e72e326129316c6c5ee80760a6c9861f6da47d2243b";

fn project_with(segment: Value, defaults: Option<Value>) -> Project {
    let mut data = json!({"schema_version": 2, "name": "p", "segments": [segment]});
    if let Some(d) = defaults {
        data["defaults"] = d;
    }
    validate(&data).unwrap_or_else(|e| panic!("須合法:{e}"))
}

/// Python `synth_seg(**kw)`:合成片段(audio 帶 key)經驗證。
fn synth(extra: Value) -> Segment {
    let mut s = json!({"id": "0123456789abcdef", "text": "大家好，歡迎收聽夜市的味道。",
                       "audio": {"id": &KEY_A[..32], "seconds": 2.5, "key": KEY_A}});
    if let (Some(obj), Value::Object(more)) = (s.as_object_mut(), extra) {
        obj.extend(more);
    }
    project_with(s, None).segments.remove(0)
}

fn pitch(points: &[(f64, f64)], roundness: Value) -> Value {
    let pts: Vec<Value> = points
        .iter()
        .map(|(p, v)| json!({"pos": p, "semi": v}))
        .collect();
    json!({"points": pts, "roundness": roundness})
}

fn wc(start: usize, end: usize, value: i64) -> Value {
    json!({"start": start, "end": end, "value": value})
}

fn post_of(key: &str) -> Value {
    json!({"id": &key[..32], "key": key, "seconds": 2.0, "trim": 0.5})
}

#[test]
fn post_params_fill_program_constants() {
    let p = post_params(&synth(json!({})));
    assert_eq!((p.connect, p.clarity), (0, 100));
    assert!(p.word_clarity.is_empty() && p.pitch.is_none());
    assert_eq!(
        p.to_value(),
        json!({"trim": {"threshold_db": -40, "keep_ms": 30, "fade_ms": 10}, "connect": 0, "clarity": 100,
               "word_clarity": [], "pitch": null})
    );
    let p = post_params(&synth(
        json!({"pitch": pitch(&[(0.0, 0.0), (1.0, 2.0)], Value::Null)}),
    ));
    assert_eq!(
        p.to_value()["pitch"],
        json!({"points": [[0.0, 0.0], [1.0, 2.0]], "roundness": 50})
    );
    let p = post_params(&synth(
        json!({"connect": 30, "clarity": 0, "word_clarity": [wc(8, 10, 40)]}),
    ));
    assert_eq!(
        p.to_value()["word_clarity"],
        json!([{"start": 8, "end": 10, "value": 40}])
    );
    assert_eq!((p.connect, p.clarity), (30, 0));
}

#[test]
fn is_default_post_cases() {
    assert!(is_default_post(&synth(json!({}))));
    assert!(is_default_post(&synth(
        json!({"connect": 0, "clarity": 100})
    )));
    assert!(!is_default_post(&synth(json!({"connect": 1}))));
    assert!(!is_default_post(&synth(json!({"clarity": 99}))));
    assert!(!is_default_post(&synth(
        json!({"word_clarity": [wc(8, 10, 40)]})
    )));
    assert!(!is_default_post(&synth(
        json!({"pitch": pitch(&[(0.0, 0.0), (1.0, 0.0)], json!(50))})
    )));
}

#[test]
fn key_is_stable_and_ignores_speed_and_pause() {
    let k = post_key(&synth(json!({}))).expect("合成片段");
    assert_eq!(k.len(), 64);
    assert!(is_key(&k));
    assert_eq!(post_key(&synth(json!({}))).expect("ok"), k);
    assert_eq!(
        post_key(&synth(json!({"connect": 0, "clarity": 100}))).expect("ok"),
        k,
        "null 與常數同義"
    );
    assert_eq!(post_key(&synth(json!({"speed": 1.5}))).expect("ok"), k);
    assert_eq!(
        post_key(&synth(json!({"pause_after": 2.0}))).expect("ok"),
        k
    );
    let others = [
        json!({"connect": 1}),
        json!({"clarity": 99}),
        json!({"word_clarity": [wc(8, 10, 40)]}),
        json!({"word_clarity": [wc(8, 10, 41)]}),
        json!({"pitch": pitch(&[(0.0, 0.0), (1.0, 1.0)], json!(50))}),
        json!({"pitch": pitch(&[(0.0, 0.0), (1.0, 1.0)], json!(0))}),
        json!({"audio": {"id": &KEY_B[..32], "seconds": 2.5, "key": KEY_B}}),
    ];
    let keys: std::collections::BTreeSet<String> = others
        .iter()
        .map(|o| post_key(&synth(o.clone())).expect("ok"))
        .collect();
    assert!(!keys.contains(&k));
    assert_eq!(keys.len(), others.len());
}

#[test]
fn old_keys_unchanged_for_trim_and_connect_only() {
    assert_eq!(post_key(&synth(json!({}))).expect("ok"), OLD_TRIM);
    assert_eq!(
        post_key(&synth(json!({"connect": 50}))).expect("ok"),
        OLD_CONNECT50
    );
    assert_eq!(
        post_key(&synth(json!({"connect": 50, "speed": 1.5}))).expect("ok"),
        OLD_CONNECT50
    );
}

#[test]
fn clarity_word_clarity_and_pitch_change_the_key_and_stale_posts() {
    for (extra, old) in [
        (json!({"clarity": 50}), OLD_CLARITY50),
        (json!({"word_clarity": [wc(8, 10, 40)]}), OLD_WC),
        (
            json!({"pitch": pitch(&[(0.0, 0.0), (1.0, 1.0)], json!(50))}),
            OLD_PITCH,
        ),
    ] {
        let s = synth(extra.clone());
        let k = post_key(&s).expect("ok");
        assert_ne!(k, old, "{extra}");
        assert!(is_key(&k));
        let mut stale = extra.clone();
        stale["post"] = post_of(old);
        assert!(
            !is_post_fresh(&synth(stale)),
            "修訂前寫入的 post 失效:{extra}"
        );
        let mut fresh = extra.clone();
        fresh["post"] = post_of(&k);
        assert!(is_post_fresh(&synth(fresh)), "{extra}");
    }
    assert_eq!(
        post_key(&synth(json!({"clarity": 50, "speed": 1.5}))).expect("ok"),
        post_key(&synth(json!({"clarity": 50}))).expect("ok")
    );
}

/// 鍵的原文:Python `json.dumps(parts, ensure_ascii=False, sort_keys=True)`(逐字寫出,與寫出器無關)。
#[test]
fn algorithm_marks_only_for_used_parts() {
    let a = &KEY_A[..32];
    let trim = r#"{"fade_ms": 10, "keep_ms": 30, "threshold_db": -40}"#;
    let pts = r#"{"points": [[0.0, 0.0], [1.0, 1.0]], "roundness": 50}"#;
    let key = |s: String| hex_digest(s.as_bytes());
    let p01 = pitch(&[(0.0, 0.0), (1.0, 1.0)], json!(50));
    assert_eq!(
        key(format!(r#"["{a}", 1, {trim}, 0, 100, [], null]"#)),
        OLD_TRIM
    );
    for (extra, text) in [
        (
            json!({"clarity": 50}),
            format!(r#"["{a}", 1, {trim}, 0, 50, [], null, {{"clarity": 2}}]"#),
        ),
        (
            json!({"word_clarity": [wc(8, 10, 40)]}),
            format!(r#"["{a}", 1, {trim}, 0, 100, [[8, 10, 40]], null, {{"clarity": 2}}]"#),
        ),
        (
            json!({"pitch": p01.clone()}),
            format!(r#"["{a}", 1, {trim}, 0, 100, [], {pts}, {{"pitch": 2}}]"#),
        ),
        (
            json!({"clarity": 50, "pitch": p01}),
            format!(r#"["{a}", 1, {trim}, 0, 50, [], {pts}, {{"clarity": 2, "pitch": 2}}]"#),
        ),
        (
            json!({"connect": 30}),
            format!(r#"["{a}", 1, {trim}, 30, 100, [], null]"#),
        ),
        (
            json!({"pitch": pitch(&[(0.0, -6.0), (0.33, 1.2), (1.0, 6.0)], Value::Null)}),
            format!(
                r#"["{a}", 1, {trim}, 0, 100, [], {{"points": [[0.0, -6.0], [0.33, 1.2], [1.0, 6.0]], "roundness": 50}}, {{"pitch": 2}}]"#
            ),
        ),
    ] {
        assert_eq!(
            post_key(&synth(extra.clone())).expect("ok"),
            key(text),
            "{extra}"
        );
    }
}

#[test]
fn is_post_fresh_and_self_recorded_audio() {
    let s = synth(json!({}));
    assert!(!is_post_fresh(&s), "沒有 post");
    let k = post_key(&s).expect("ok");
    let fresh = synth(json!({"post": post_of(&k)}));
    assert!(is_post_fresh(&fresh));
    let mut changed = fresh.clone();
    changed.connect = Some(30);
    assert!(!is_post_fresh(&changed));
    // 自錄音檔(audio 沒有 key):不後製,post_key 回錯誤
    let ext = project_with(
        json!({"id": "0123456789abcdef", "text": "自錄。", "audio": {"id": &KEY_B[..32], "seconds": 1.0},
               "post": post_of(&k)}),
        None,
    )
    .segments
    .remove(0);
    assert!(!is_post_fresh(&ext));
    let e = post_key(&ext).expect_err("自錄音檔");
    assert_eq!(e.errors(), [NOT_SYNTHESIZED]);
    assert_eq!(e.class(), "project_error");
    // 沒有音檔
    let none = project_with(json!({"id": "0123456789abcdef", "text": "好。"}), None)
        .segments
        .remove(0);
    assert!(!is_post_fresh(&none));
    assert_eq!(
        post_key(&none).expect_err("沒有音檔").errors(),
        ["只有合成的片段有後製(自錄音檔不做後製)"]
    );
}

#[test]
fn effective_speed_and_pause() {
    let defaults =
        json!({"speed": 0.8, "pause_comma": 0.2, "pause_sentence": 0.5, "pause_paragraph": 1.5});
    for (brk, want) in [
        ("sentence", 0.5),
        ("paragraph", 1.5),
        ("forced", 0.2),
        ("end", 0.0),
    ] {
        let p = project_with(
            json!({"id": "0123456789abcdef", "text": "好。", "break": brk}),
            Some(defaults.clone()),
        );
        assert_eq!(effective_pause(&p, &p.segments[0]), want, "{brk}");
        assert_eq!(effective_speed(&p, &p.segments[0]), 0.8);
    }
    let p = project_with(
        json!({"id": "0123456789abcdef", "text": "好。", "break": "end", "pause_after": 2.0, "speed": 1.5}),
        None,
    );
    assert_eq!(effective_pause(&p, &p.segments[0]), 2.0);
    assert_eq!(effective_speed(&p, &p.segments[0]), 1.5);
    // 專案預設(DEFAULTS):語速 1.15、句末 0.4、段落 0.8、逗號 0.0
    let p = project_with(
        json!({"id": "0123456789abcdef", "text": "好。", "break": "paragraph"}),
        None,
    );
    assert_eq!(effective_speed(&p, &p.segments[0]), 1.15);
    assert_eq!(effective_pause(&p, &p.segments[0]), 0.8);
    // 未驗證的切點種類(欄位直接組出)取 0(Python BREAK_PAUSE.get 為 None)
    let mut s = p.segments[0].clone();
    s.brk = "x".to_string();
    assert_eq!(effective_pause(&p, &s), 0.0);
}
