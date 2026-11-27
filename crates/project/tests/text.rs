//! L1:分句、合成文字與合音(CHG-20261128-01 T3;`project.py` 的 `split_segments`、`escape_text`、`tts_text`、
//! `fused_out`、`model_units`、`shift_marks`)。移植 `tests/test_project.py` 的對應案例,另加上限與 Python 空白字元集;
//! `tts_text` 對「未經驗證的片段」重新檢查標註與合音的訊息,在 macOS / Linux 另以 Python 子程序確認逐字相同
//! (Windows 無法執行 `project.py`,只斷言 Rust 等於寫死的預期)。
//! 對應 `features/project_rust.feature`「分句與合成文字與 Python 相同」(比對 fixture 見 `crates/parity`)。

#[cfg(unix)]
mod common;

use project::model::{Annotation, Fusion, FusionOff, Segment, WordClarity};
use project::text::{
    Piece, escape_text, fused_out, model_units, shift_marks, split_segments, tts_text,
};
use project::validate::validate;
use serde_json::{Value, json};

/// 驗證後的片段(Python `pj.validate(data(segments=[seg(...)]))["segments"][0]`)。
fn seg(text: &str, extra: Value) -> Segment {
    let mut s = json!({"id": "0123456789abcdef", "text": text});
    if let (Some(obj), Value::Object(more)) = (s.as_object_mut(), extra) {
        obj.extend(more);
    }
    let p = validate(&json!({"schema_version": 2, "name": "p", "segments": [s]}))
        .unwrap_or_else(|e| panic!("片段須合法:{e}"));
    p.segments.into_iter().next().expect("一個片段")
}

fn fz(index: usize, zhuyin: &str, source: &str, length: usize) -> Value {
    json!({"index": index, "length": length, "zhuyin": zhuyin, "source": source})
}

fn fzd(index: usize, zhuyin: &str, length: usize, display: &str) -> Value {
    json!({"index": index, "length": length, "zhuyin": zhuyin, "source": "lexicon", "display": display})
}

fn pieces(text: &str, forced: &[i64]) -> Vec<(String, String)> {
    split_segments(text, forced)
        .expect("可分句")
        .into_iter()
        .map(|p| (p.text, p.brk))
        .collect()
}

fn owned(v: &[(&str, &str)]) -> Vec<(String, String)> {
    v.iter()
        .map(|(a, b)| (a.to_string(), b.to_string()))
        .collect()
}

fn err(r: project::Result<impl std::fmt::Debug>) -> Vec<String> {
    match r {
        Ok(v) => panic!("預期錯誤,得到 {v:?}"),
        Err(e) => e.errors().to_vec(),
    }
}

fn chars(s: &str) -> Vec<char> {
    s.chars().collect()
}

#[test]
fn split_by_sentence_punctuation_and_newline() {
    let text = "大家好，歡迎收聽。今天來聊台灣夜市！你去過嗎？」\n\n第二段；分號也切;半形也切!最後沒有標點";
    assert_eq!(
        pieces(text, &[]),
        owned(&[
            ("大家好，歡迎收聽。", "sentence"),
            ("今天來聊台灣夜市！", "sentence"),
            ("你去過嗎？」", "paragraph"),
            ("第二段；", "sentence"),
            ("分號也切;", "sentence"),
            ("半形也切!", "sentence"),
            ("最後沒有標點", "end"),
        ])
    );
    let c = chars(text);
    for p in split_segments(text, &[]).expect("可分句") {
        assert_eq!(c[p.start..p.end].iter().collect::<String>(), p.text);
    }
}

#[test]
fn split_newline_without_punctuation_and_blank_pieces() {
    assert_eq!(
        pieces("  第一行\n第二行。\n\n  \n", &[]),
        owned(&[("第一行", "paragraph"), ("第二行。", "paragraph")])
    );
    assert_eq!(
        split_segments("", &[]).expect("空文稿"),
        Vec::<Piece>::new()
    );
    let got: Vec<String> = pieces("好!?」)再來。", &[])
        .into_iter()
        .map(|p| p.0)
        .collect();
    assert_eq!(got, ["好!?」)", "再來。"]);
    // \r 也是換行;\r\n 是兩個切點(中間為空白片段,沿用前一句升為 paragraph)
    assert_eq!(
        pieces("一\r\n二", &[]),
        owned(&[("一", "paragraph"), ("二", "end")])
    );
}

#[test]
fn split_forced_breaks() {
    let text = "大家好，歡迎收聽。今天，天氣好。";
    let c = chars(text);
    let comma = c.iter().position(|&x| x == '，').expect("逗號") as i64;
    let last = c.iter().rposition(|&x| x == '，').expect("逗號") as i64;
    assert_eq!(
        pieces(text, &[comma]),
        owned(&[
            ("大家好，", "forced"),
            ("歡迎收聽。", "sentence"),
            ("今天，天氣好。", "sentence"),
        ])
    );
    let two: Vec<String> = pieces(text, &[comma, last])
        .into_iter()
        .map(|p| p.0)
        .collect();
    assert_eq!(two, ["大家好，", "歡迎收聽。", "今天，", "天氣好。"]);
    // 重複的位置與一個位置相同;強制停頓後接換行時升為 paragraph
    assert_eq!(pieces(text, &[comma, comma]), pieces(text, &[comma]));
    assert_eq!(
        pieces("好，\n再見", &[1]),
        owned(&[("好，", "paragraph"), ("再見", "end")])
    );
    for bad in [-1, c.len() as i64, i64::MAX, i64::MIN] {
        assert_eq!(
            err(split_segments(text, &[bad])),
            ["強制停頓位置超出文稿範圍"]
        );
    }
}

#[test]
fn split_strips_python_whitespace_and_counts_code_points() {
    // U+001C~U+001F 在 Python 是空白(Rust char::is_whitespace 不是);U+200B 不是空白
    let text = "\u{1c}\u{1f}a。\u{3000}𠀀字\u{200b}";
    let got = split_segments(text, &[]).expect("可分句");
    assert_eq!(
        got,
        vec![
            Piece {
                text: "a。".into(),
                brk: "sentence".into(),
                start: 2,
                end: 4
            },
            Piece {
                text: "𠀀字\u{200b}".into(),
                brk: "end".into(),
                start: 5,
                end: 8
            },
        ]
    );
}

#[test]
fn split_limits_and_error_order() {
    // 50000 字(全空白)可以、50001 字拒絕;文稿過長先於強制停頓的檢查
    assert_eq!(
        split_segments(&" ".repeat(50000), &[]).expect("上限內"),
        Vec::<Piece>::new()
    );
    assert_eq!(
        err(split_segments(&" ".repeat(50001), &[-1])),
        ["文稿超過 50000 字"]
    );
    // 單句 500 字可以、501 字回報第一個超長的句號
    let ok = format!("{}。", "字".repeat(499));
    assert_eq!(split_segments(&ok, &[]).expect("500 字").len(), 1);
    let bad = format!("短。{}。{}。", "字".repeat(500), "字".repeat(600));
    assert_eq!(
        err(split_segments(&bad, &[])),
        ["第 2 句超過 500 字,請加句尾標點或強制停頓"]
    );
    // 片段 2000 個可以、2001 個拒絕;超長句的檢查先於片段數
    assert_eq!(
        split_segments(&"好。".repeat(2000), &[])
            .expect("2000 句")
            .len(),
        2000
    );
    assert_eq!(
        err(split_segments(&"好。".repeat(2001), &[])),
        ["片段超過 2000 個"]
    );
    let both = format!("{}{}", "好。".repeat(2001), "字".repeat(501));
    assert_eq!(
        err(split_segments(&both, &[])),
        ["第 2002 句超過 500 字,請加句尾標點或強制停頓"]
    );
}

#[test]
fn escape_is_one_to_one() {
    assert_eq!(escape_text("a[b]c[[]]"), "a［b］c［［］］");
    assert_eq!(escape_text("［］"), "［］");
    let s = "好[:ㄅ]𠀀";
    assert_eq!(chars(&escape_text(s)).len(), chars(s).len());
}

#[test]
fn tts_text_escapes_brackets_and_inserts_annotations() {
    assert_eq!(
        tts_text(&seg("好[:ㄅ]", json!({}))).expect("ok"),
        "好［:ㄅ］"
    );
    assert_eq!(
        tts_text(&seg("好[:ㄅㄚ]吃", json!({}))).expect("ok"),
        "好［:ㄅㄚ］吃"
    );
    let s = seg(
        "今天好熱",
        json!({"annotations": [{"index": 2, "zhuyin": "ㄏㄠˋ"}]}),
    );
    assert_eq!(tts_text(&s).expect("ok"), "今天好[:ㄏㄠˋ]熱");
    let s2 = seg(
        "你好[嗎]的",
        json!({"annotations": [{"index": 5, "zhuyin": "˙ㄉㄜ"}, {"index": 1, "zhuyin": "ㄏㄠˇ"}]}),
    );
    assert_eq!(tts_text(&s2).expect("ok"), "你好[:ㄏㄠˇ]［嗎］的[:˙ㄉㄜ]");
    assert_eq!(
        s2.annotations.iter().map(|a| a.index).collect::<Vec<_>>(),
        [1, 5]
    );
}

#[test]
fn tts_text_fusions_zhuyin_route() {
    let s = seg(
        "是這樣啊",
        json!({"fusions": [fz(1, "ㄐㄧㄤˋ", "rule", 2)]}),
    );
    assert_eq!(tts_text(&s).expect("ok"), "是這[:ㄐㄧㄤˋ]啊");
    let s = seg(
        "是這樣啊，我也不知道",
        json!({"fusions": [fz(8, "ㄗㄠˋ", "lexicon", 2), fz(1, "ㄐㄧㄤˋ", "rule", 2)]}),
    );
    assert_eq!(
        s.fusions.iter().map(|f| f.index).collect::<Vec<_>>(),
        [1, 8]
    );
    assert_eq!(
        tts_text(&s).expect("ok"),
        "是這[:ㄐㄧㄤˋ]啊，我也不知[:ㄗㄠˋ]"
    );
    assert_eq!(fused_out(&s).into_iter().collect::<Vec<_>>(), [2, 9]);
    let s = seg(
        "我也不知道",
        json!({"fusions": [fz(2, "ㄅㄨˋ ㄗㄠˋ", "lexicon", 3)]}),
    );
    assert_eq!(tts_text(&s).expect("ok"), "我也不[:ㄅㄨˋ]知[:ㄗㄠˋ]");
    assert_eq!(fused_out(&s).into_iter().collect::<Vec<_>>(), [4]);
    let s = seg(
        "怎麼樣都可以",
        json!({"fusions": [fz(0, "ㄗㄣˇ ㄧㄤˋ", "lexicon", 3)]}),
    );
    assert_eq!(tts_text(&s).expect("ok"), "怎[:ㄗㄣˇ]麼[:ㄧㄤˋ]都可以");
    let s = seg(
        "就是這樣",
        json!({"fusions": [fz(0, "ㄐㄧㄡˋ ㄕˋ ㄐㄧㄤˋ", "rule", 4)]}),
    );
    assert_eq!(
        tts_text(&s).expect("ok"),
        "就[:ㄐㄧㄡˋ]是[:ㄕˋ]這[:ㄐㄧㄤˋ]"
    );
}

const R1_TEXT: &str = "是這樣啊，我也不知道。";

#[test]
fn tts_text_display_replaces_fusion_range() {
    let s = seg(
        R1_TEXT,
        json!({"fusions": [fzd(1, "ㄐㄧㄤˋ", 2, "醬"), fzd(7, "ㄅㄨˋ ㄗㄠˋ", 3, "不造")]}),
    );
    assert_eq!(tts_text(&s).expect("ok"), "是醬啊，我也不造。");
    // 空字串顯示字等同沒有顯示字(Python 以真假值判定)
    for display in [None, Some("")] {
        let mut fus = vec![
            fz(1, "ㄐㄧㄤˋ", "lexicon", 2),
            fz(7, "ㄅㄨˋ ㄗㄠˋ", "lexicon", 3),
        ];
        if let Some(d) = display {
            for f in &mut fus {
                f["display"] = json!(d);
            }
        }
        let s = seg(R1_TEXT, json!({"fusions": fus}));
        assert_eq!(
            tts_text(&s).expect("ok"),
            "是這[:ㄐㄧㄤˋ]啊，我也不[:ㄅㄨˋ]知[:ㄗㄠˋ]。"
        );
        assert_eq!(fused_out(&s).into_iter().collect::<Vec<_>>(), [2, 9]);
    }
    // 顯示字比原範圍長
    let s = seg(
        "是這樣啊好",
        json!({"fusions": [fzd(1, "ㄐㄧㄤˋ", 2, "醬醬醬")]}),
    );
    assert_eq!(tts_text(&s).expect("ok"), "是醬醬醬啊好");
}

#[test]
fn model_units_follow_display_and_zhuyin_route() {
    // 注音路線:9 個漢字 − 2 個合音第二字
    let s = seg(
        "是這樣啊，我也不知道",
        json!({"fusions": [fz(1, "ㄐㄧㄤˋ", "rule", 2), fz(8, "ㄗㄠˋ", "lexicon", 2)]}),
    );
    assert_eq!(model_units(&s), [0, 1, 3, 5, 6, 7, 8]);
    // 顯示字:我、也 + 不、造(第 j 個顯示字對應範圍內第 min(j, length − 1) 個字)
    let s = seg(
        "我也不知道",
        json!({"fusions": [fzd(2, "ㄅㄨˋ ㄗㄠˋ", 3, "不造")]}),
    );
    assert_eq!(model_units(&s), [0, 1, 2, 3]);
    assert_eq!(fused_out(&s).into_iter().collect::<Vec<_>>(), [2, 3, 4]);
    // 顯示字比範圍長:末字重複對應
    let s = seg(
        "是這樣啊好",
        json!({"fusions": [fzd(1, "ㄐㄧㄤˋ", 2, "醬醬醬")]}),
    );
    assert_eq!(model_units(&s), [0, 1, 2, 2, 3, 4]);
    // 非漢字不送模型
    let s = seg("A，好𠀀", json!({}));
    assert_eq!(model_units(&s), [2, 3]);
}

#[test]
fn shift_marks_moves_and_clears() {
    let mut s = Segment::new("0123456789abcdef", "是這樣啊我不知道耶好的");
    s.fusions = vec![Fusion {
        index: 4,
        length: 3,
        zhuyin: "ㄅㄨˋ ㄗㄠˋ".into(),
        source: "lexicon".into(),
        display: None,
    }];
    s.fusion_off = vec![
        FusionOff {
            index: 0,
            length: 2,
        },
        FusionOff {
            index: 8,
            length: 2,
        },
    ];
    let old = "是這樣啊我不知道耶好的";
    let got = shift_marks(&s, old, "是這樣啊，我不知道耶好的");
    assert_eq!(got.cleared, 0);
    assert_eq!(got.fusions[0].index, 5);
    assert_eq!(
        got.fusion_off,
        [
            FusionOff {
                index: 0,
                length: 2
            },
            FusionOff {
                index: 9,
                length: 2
            }
        ]
    );
    let got = shift_marks(&s, old, "是這樣啊我不道耶好的");
    assert!(got.fusions.is_empty());
    assert_eq!(got.cleared, 1);
    assert_eq!(
        got.fusion_off,
        [
            FusionOff {
                index: 0,
                length: 2
            },
            FusionOff {
                index: 7,
                length: 2
            }
        ]
    );
    // 文字不變:全部保留;整段換掉:全部清除;只在結尾加字:之前的保留
    assert_eq!(shift_marks(&s, old, old).fusions, s.fusions);
    let all = shift_marks(&s, old, "完全不同");
    assert_eq!(
        (all.fusions.len(), all.fusion_off.len(), all.cleared),
        (0, 0, 1)
    );
    let tail = shift_marks(&s, old, "是這樣啊我不知道耶好的嗎");
    assert_eq!((tail.fusions.len(), tail.fusion_off.len()), (1, 2));
    // 顯示字隨位移保留
    s.fusions[0].display = Some("不造".into());
    let got = shift_marks(&s, old, "嗯是這樣啊我不知道耶好的");
    assert_eq!(got.fusions[0].index, 5);
    assert_eq!(got.fusions[0].display.as_deref(), Some("不造"));
}

#[test]
fn shift_marks_insertion_at_boundaries() {
    // 純插入(改動範圍為空):在合音起點插入 → 位移;在合音內部插入 → 清除;在合音終點插入 → 保留
    let mut s = Segment::new("0123456789abcdef", "甲乙丙丁");
    s.fusion_off = vec![FusionOff {
        index: 1,
        length: 2,
    }];
    let at = |new: &str| shift_marks(&s, "甲乙丙丁", new).fusion_off;
    assert_eq!(
        at("甲X乙丙丁"),
        [FusionOff {
            index: 2,
            length: 2
        }]
    );
    assert_eq!(at("甲乙X丙丁"), Vec::<FusionOff>::new());
    assert_eq!(
        at("甲乙丙X丁"),
        [FusionOff {
            index: 1,
            length: 2
        }]
    );
}

/// 未經驗證的片段(欄位直接組出):`tts_text` 與 Python 相同地重新檢查標註與合音。
fn raw_cases() -> Vec<(Segment, Vec<String>)> {
    let ann = |items: &[(usize, &str)]| -> Vec<Annotation> {
        items
            .iter()
            .map(|(i, z)| Annotation {
                index: *i,
                zhuyin: (*z).to_string(),
            })
            .collect()
    };
    let with = |text: &str, f: &dyn Fn(&mut Segment)| {
        let mut s = Segment::new("0123456789abcdef", text);
        f(&mut s);
        s
    };
    let fusion = |index, length, zhuyin: &str, source: &str| Fusion {
        index,
        length,
        zhuyin: zhuyin.into(),
        source: source.into(),
        display: None,
    };
    let zhuyin_msg = "片段:注音標註只接受注音符號與聲調符號(ˊˇˋ˙),長度 1 到 5".to_string();
    let mut out = vec![
        (with("", &|_| {}), vec!["片段文字是空的".to_string()]),
        (
            with("今天好熱。", &|s| s.annotations = ann(&[(9, "ㄏㄠˋ")])),
            vec!["片段.annotations 的索引超出句子範圍".to_string()],
        ),
        (
            with("今天好熱。", &|s| s.annotations = ann(&[(4, "ㄏㄠˋ")])),
            vec!["片段.annotations 只能標在漢字上".to_string()],
        ),
        (
            with("今天好熱。", &|s| {
                s.annotations = ann(&[(0, "ㄏㄠˋ"), (0, "ㄏㄠˇ")])
            }),
            vec!["片段.annotations 同一個字重複標註".to_string()],
        ),
        (
            with("是這樣啊", &|s| {
                s.fusions = vec![fusion(1, 2, "ㄐㄧㄤˋ", "rule")];
                s.annotations = ann(&[(2, "ㄧㄤˋ")]);
            }),
            vec!["片段:合音範圍內有聲調標註,請先清除該字的念法".to_string()],
        ),
        (
            with("是這樣啊", &|s| {
                s.fusions = vec![fusion(1, 2, "ㄐㄧㄤˋ", "rule")];
                s.word_clarity = vec![WordClarity {
                    start: 2,
                    end: 4,
                    value: 40,
                }];
            }),
            vec!["片段:字詞清晰度的範圍必須完整包含或完全不含合音的字".to_string()],
        ),
        (
            with("是這樣啊", &|s| {
                s.fusions = vec![fusion(3, 2, "ㄐㄧㄤˋ", "rule")]
            }),
            vec!["片段.fusions 的位置超出句子範圍".to_string()],
        ),
        (
            with("是這，啊", &|s| {
                s.fusions = vec![fusion(1, 2, "ㄐㄧㄤˋ", "rule")]
            }),
            vec!["片段.fusions 只能合併相鄰的漢字".to_string()],
        ),
        (
            with("是這樣啊", &|s| {
                s.fusions = vec![fusion(1, 1, "ㄐㄧㄤˋ", "rule")]
            }),
            vec!["片段.fusions 的字數必須是 2 到 4".to_string()],
        ),
        (
            with("是這樣啊", &|s| {
                s.fusions = vec![fusion(1, 2, "ㄐㄧㄤˋ", "auto")]
            }),
            vec!["片段.fusions 的來源只能是 rule、lexicon、manual".to_string()],
        ),
        (
            with("我不知道耶", &|s| {
                s.fusions = vec![fusion(1, 3, "ㄅㄨˋ ㄓ ㄉㄠˋ", "rule")]
            }),
            vec!["片段:合音注音的音節數必須少於字數(1 到 2 個,以半形空白分隔)".to_string()],
        ),
        (
            with("我不知道耶", &|s| {
                s.fusions = vec![fusion(1, 3, "ㄅㄨˋ  ㄗㄠˋ", "rule")]
            }),
            vec!["片段:合音注音的音節數必須少於字數(1 到 2 個,以半形空白分隔)".to_string()],
        ),
        (
            with("這樣樣子", &|s| {
                s.fusions = vec![
                    fusion(0, 2, "ㄐㄧㄤˋ", "rule"),
                    fusion(1, 2, "ㄐㄧㄤˋ", "rule"),
                ]
            }),
            vec!["片段.fusions 的範圍不可重疊".to_string()],
        ),
        (
            with("是這樣啊", &|s| {
                s.fusions = vec![fusion(1, 2, "ㄐㄧㄤ4", "rule")]
            }),
            vec!["片段:合音注音標註只接受注音符號與聲調符號(ˊˇˋ˙),長度 1 到 5".to_string()],
        ),
        (
            with("是這樣啊", &|s| {
                let mut f = fusion(1, 2, "ㄐㄧㄤˋ", "rule");
                f.display = Some("醬a".into());
                s.fusions = vec![f];
            }),
            vec!["片段:合音的顯示字必須是空字串或 1 到 4 個漢字".to_string()],
        ),
        (
            with(&"這樣".repeat(17), &|s| {
                s.fusions = (0..17)
                    .map(|k| fusion(2 * k, 2, "ㄐㄧㄤˋ", "rule"))
                    .collect()
            }),
            vec!["片段.fusions 每句最多 16 組".to_string()],
        ),
    ];
    for z in [
        "hao4",
        "ㄏㄠ4",
        "ㄏㄠ]",
        "[:ㄏ",
        "",
        "ㄏㄠˋㄏㄠˋ",
        "ㄏㄠˋˋ",
        "˙ㄉㄜˋ",
        "ㄅㄆㄇㄈㄉㄊ",
        "ㄏ ㄠ",
    ] {
        out.push((
            with("今天好熱", &|s| s.annotations = ann(&[(2, z)])),
            vec![zhuyin_msg.clone()],
        ));
    }
    // 檢查通過的未驗證片段:字詞清晰度超出句子不影響合成文字(Python 不檢查)
    out.push((
        with("是這樣啊", &|s| {
            s.fusions = vec![fusion(1, 2, "ㄐㄧㄤˋ", "rule")];
            s.word_clarity = vec![WordClarity {
                start: 0,
                end: 100,
                value: 40,
            }];
        }),
        Vec::new(),
    ));
    out
}

#[test]
fn tts_text_rechecks_unvalidated_segments_like_python() {
    let cases = raw_cases();
    for (s, want) in &cases {
        match tts_text(s) {
            Ok(t) => assert!(want.is_empty(), "{:?} 預期 {want:?},得到 {t}", s.text),
            Err(e) => assert_eq!(e.errors(), want.as_slice(), "{:?}", s.text),
        }
    }
    #[cfg(unix)]
    {
        let reqs: Vec<(&str, Vec<Value>)> = cases
            .iter()
            .map(|(s, _)| ("tts_text", vec![s.to_value()]))
            .collect();
        let got = common::call(&reqs);
        for ((s, want), py) in cases.iter().zip(got) {
            match py {
                common::PyOut::Errors(e) => assert_eq!(&e, want, "Python {:?}", s.text),
                common::PyOut::Value(v) => {
                    assert!(want.is_empty(), "Python {:?} 接受:{v}", s.text);
                    assert_eq!(v, json!(tts_text(s).expect("ok")));
                }
                common::PyOut::Exception(x) => panic!("Python {:?} 丟出 {x}", s.text),
            }
        }
        println!("tts_text 未驗證片段:{} 筆,Rust 與 Python 相同", cases.len());
    }
    #[cfg(not(unix))]
    println!(
        "tts_text 未驗證片段:{} 筆 Rust 符合預期(Python 端在 Windows 無法執行,略過 Python 確認)",
        cases.len()
    );
}

/// 未驗證片段上 `fused_out`、`model_units`、`shift_marks` 也與 Python 相同(Python 不檢查,Rust 不 panic)。
#[test]
fn unchecked_helpers_match_python_on_raw_segments() {
    let mut s = Segment::new("0123456789abcdef", "甲乙丙丁戊");
    s.fusions = vec![
        Fusion {
            index: 3,
            length: 4,
            zhuyin: "ㄅ".into(),
            source: "rule".into(),
            display: None,
        },
        Fusion {
            index: 0,
            length: 2,
            zhuyin: "ㄅ ㄆ ㄇ".into(),
            source: "rule".into(),
            display: Some("".into()),
        },
    ];
    assert_eq!(fused_out(&s).into_iter().collect::<Vec<_>>(), [4, 5, 6]);
    assert_eq!(model_units(&s), [0, 1, 2, 3]);
    #[cfg(unix)]
    {
        let v = s.to_value();
        let got = common::call(&[
            ("fused_out", vec![v.clone()]),
            ("model_units", vec![v.clone()]),
            (
                "shift_marks",
                vec![v, json!("甲乙丙丁戊"), json!("甲乙X丙丁戊")],
            ),
        ]);
        assert_eq!(got[0], common::PyOut::Value(json!([4, 5, 6])));
        assert_eq!(got[1], common::PyOut::Value(json!([0, 1, 2, 3])));
        let shifted = shift_marks(&s, "甲乙丙丁戊", "甲乙X丙丁戊");
        assert_eq!(
            got[2],
            common::PyOut::Value(serde_json::to_value(&shifted).expect("JSON"))
        );
    }
}
