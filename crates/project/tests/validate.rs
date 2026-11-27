//! L1:完整 `validate`、讀 JSON 原文、`Project` 型別與 `to_dict`、寫出(5 MB 上限)、v1 升級
//! (CHG-20261128-01 T2,代決 5、6、9)。
//!
//! 預期的錯誤訊息逐字取自 `project.py`(`validate` 與 `_validate_*`、`_check_*`),接受時的寫出字串為
//! CPython 的實際輸出(施工者以真轉接器 `project.validate_text` 對同一份原文產生,貼進本檔);
//! 兩邊的逐案比對另見 `crates/parity/fixtures/project/validate_text.json` 與預期結果表。
//! 每一個錯誤訊息分支至少一個案例,另鎖住錯誤訊息的累積順序與正規化結果。

use project::model::{Asset, Audio, Defaults, Image, Pitch, PitchPoint, Post, Project, Segment};
use project::validate::{dump_validated, parse_project_json, validate};
use project::{Error, ErrorKind};
use serde_json::{Number, Value, json};

const VERSION_TAIL: &str = "不支援,拒絕載入(本版讀 schema_version 2,舊版 1 會自動升級)";
const NAME_HINT: &str =
    "專案名稱只能用中文、日文假名、英文字母、數字、底線、連字號,長度 1 到 64 字";
const KEY: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

fn parse(text: &str) -> Value {
    parse_project_json(text.as_bytes()).unwrap_or_else(|e| panic!("解析失敗:{e}\n{text}"))
}

/// `_dump(validate(json.loads(text)))` 的字串。
fn dump_of(text: &str) -> String {
    let bytes = dump_validated(&parse(text)).unwrap_or_else(|e| panic!("驗證失敗:{e}\n{text}"));
    String::from_utf8(bytes).expect("UTF-8")
}

/// 從原文到驗證的錯誤(解析或驗證任一步失敗)。
fn error_of_bytes(raw: &[u8]) -> Error {
    match parse_project_json(raw).and_then(|v| dump_validated(&v)) {
        Ok(b) => panic!("預期失敗,得到:{}", String::from_utf8_lossy(&b)),
        Err(e) => e,
    }
}

fn errors_of(text: &str) -> Vec<String> {
    let e = error_of_bytes(text.as_bytes());
    assert_eq!(e.kind(), ErrorKind::Invalid, "{text}");
    assert_eq!(e.class(), "project_error");
    e.errors().to_vec()
}

fn p(extra: &str) -> String {
    format!(r#"{{"schema_version": 2, "name": "測試專案"{extra}}}"#)
}

/// 一個片段(文字為 6 個漢字「我們今天很好」)加上額外欄位。
fn ps(extra: &str) -> String {
    p(&format!(
        r#", "segments": [{{"id": "0000000000000001", "text": "我們今天很好"{extra}}}]"#
    ))
}

const ASSET: &str = r#"{"id": "00000000000000a3", "kind": "image", "ext": "png", "bytes": 1024}"#;

const MINIMAL_DUMP: &str = r#"{
 "schema_version": 2,
 "name": "測試專案",
 "script": "",
 "defaults": {
  "speed": 1.15,
  "pause_comma": 0.0,
  "pause_sentence": 0.4,
  "pause_paragraph": 0.8
 },
 "segments": [],
 "images": [],
 "assets": []
}
"#;

const ALL_FIELDS_TEXT: &str = r#"{"schema_version": 2, "name": "測試專案", "segments": [{"id": "0000000000000001", "text": "我們今天很好", "break": "sentence", "pause_after": 0.35, "speed": 1.075, "annotations": [{"index": 5, "zhuyin": "ㄏㄠˇ"}, {"index": 0, "zhuyin": "ㄨㄛˇ"}], "audio": {"id": "0000000000000002", "seconds": 3.25, "key": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}, "connect": 40, "clarity": 100, "word_clarity": [{"start": 4, "end": 6, "value": 30}, {"start": 0, "end": 2, "value": 80}], "pitch": {"points": [{"pos": 0, "semi": 0}, {"pos": 0.505, "semi": -2.05}, {"pos": 1, "semi": 6}], "roundness": 50}, "post": {"id": "0123456789abcdef0123456789abcdef", "key": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", "seconds": 2.5, "trim": 0.125, "pitch_points": [[0, 0], [0.5, -7.2], [1, 7.2]]}, "fusions": [{"index": 2, "length": 2, "zhuyin": "ㄐㄧㄢ", "source": "rule", "display": "今"}], "fusion_off": [{"index": 4, "length": 2}, {"index": 4, "length": 2}, {"index": 0, "length": 3}]}]}"#;

const ALL_FIELDS_DUMP: &str = r#"{
 "schema_version": 2,
 "name": "測試專案",
 "script": "",
 "defaults": {
  "speed": 1.15,
  "pause_comma": 0.0,
  "pause_sentence": 0.4,
  "pause_paragraph": 0.8
 },
 "segments": [
  {
   "id": "0000000000000001",
   "text": "我們今天很好",
   "break": "sentence",
   "pause_after": 0.4,
   "speed": 1.1,
   "annotations": [
    {
     "index": 0,
     "zhuyin": "ㄨㄛˇ"
    },
    {
     "index": 5,
     "zhuyin": "ㄏㄠˇ"
    }
   ],
   "audio": {
    "id": "0000000000000002",
    "seconds": 3.25,
    "key": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
   },
   "connect": 40,
   "clarity": 100,
   "word_clarity": [
    {
     "start": 0,
     "end": 2,
     "value": 80
    },
    {
     "start": 4,
     "end": 6,
     "value": 30
    }
   ],
   "pitch": {
    "points": [
     {
      "pos": 0.0,
      "semi": 0.0
     },
     {
      "pos": 0.51,
      "semi": -2.1
     },
     {
      "pos": 1.0,
      "semi": 6.0
     }
    ],
    "roundness": 50
   },
   "post": {
    "id": "0123456789abcdef0123456789abcdef",
    "key": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "seconds": 2.5,
    "trim": 0.125,
    "pitch_points": [
     [
      0.0,
      0.0
     ],
     [
      0.5,
      -7.2
     ],
     [
      1.0,
      7.2
     ]
    ]
   },
   "fusions": [
    {
     "index": 2,
     "length": 2,
     "zhuyin": "ㄐㄧㄢ",
     "source": "rule",
     "display": "今"
    }
   ],
   "fusion_off": [
    {
     "index": 0,
     "length": 3
    },
    {
     "index": 4,
     "length": 2
    }
   ]
  }
 ],
 "images": [],
 "assets": []
}
"#;

const V1_TEXT: &str = r#"{"schema_version": 1, "name": "舊專案", "script": "稿", "segments": [{"id": "0000000000000001", "text": "我們今天很好", "break": "paragraph", "pause_after": 1.0, "speed": 0.9, "annotations": [{"index": 0, "zhuyin": "ㄨㄛˇ"}], "audio": {"id": "0000000000000002", "seconds": 2}}]}"#;

const V1_DUMP: &str = r#"{
 "schema_version": 2,
 "name": "舊專案",
 "script": "稿",
 "defaults": {
  "speed": 1.15,
  "pause_comma": 0.0,
  "pause_sentence": 0.4,
  "pause_paragraph": 0.8
 },
 "segments": [
  {
   "id": "0000000000000001",
   "text": "我們今天很好",
   "break": "paragraph",
   "pause_after": 1.0,
   "speed": 0.9,
   "annotations": [
    {
     "index": 0,
     "zhuyin": "ㄨㄛˇ"
    }
   ],
   "audio": {
    "id": "0000000000000002",
    "seconds": 2
   },
   "connect": null,
   "clarity": null,
   "word_clarity": [],
   "pitch": null,
   "post": null,
   "fusions": [],
   "fusion_off": []
  }
 ],
 "images": [],
 "assets": []
}
"#;

const ASSETS_TEXT: &str = r#"{"schema_version": 2, "name": "測試專案", "assets": [{"id": "00000000000000a3", "kind": "image", "ext": "png", "bytes": 1024}, {"id": "abcdef0123456789", "kind": "audio", "ext": "m4a", "bytes": 0, "label": "旁白"}, {"id": "00000000000000b1", "kind": "text", "ext": "md", "bytes": 7, "label": null}], "images": [{"id": "abcdef0123456780", "asset": "00000000000000a3", "start": 0, "end": null}, {"id": "0000000000000002", "asset": "00000000000000a3", "start": 1.25, "end": 3.05}]}"#;

const ASSETS_DUMP: &str = r#"{
 "schema_version": 2,
 "name": "測試專案",
 "script": "",
 "defaults": {
  "speed": 1.15,
  "pause_comma": 0.0,
  "pause_sentence": 0.4,
  "pause_paragraph": 0.8
 },
 "segments": [],
 "images": [
  {
   "id": "abcdef0123456780",
   "asset": "00000000000000a3",
   "start": 0.0,
   "end": null
  },
  {
   "id": "0000000000000002",
   "asset": "00000000000000a3",
   "start": 1.3,
   "end": 3.1
  }
 ],
 "assets": [
  {
   "id": "00000000000000a3",
   "kind": "image",
   "ext": "png",
   "bytes": 1024
  },
  {
   "id": "abcdef0123456789",
   "kind": "audio",
   "ext": "m4a",
   "bytes": 0,
   "label": "旁白"
  },
  {
   "id": "00000000000000b1",
   "kind": "text",
   "ext": "md",
   "bytes": 7,
   "label": null
  }
 ]
}
"#;

#[test]
fn accepted_files_dump_like_python() {
    assert_eq!(dump_of(&p("")), MINIMAL_DUMP);
    assert_eq!(dump_of(ALL_FIELDS_TEXT), ALL_FIELDS_DUMP);
    assert_eq!(dump_of(V1_TEXT), V1_DUMP);
    assert_eq!(dump_of(ASSETS_TEXT), ASSETS_DUMP);
    // 前後空白、縮排與 \u 跳脫不影響結果
    assert_eq!(dump_of(&format!(" \t\r\n{}\n ", p(""))), MINIMAL_DUMP);
    assert_eq!(
        dump_of(r#"{"schema_version":2,"name":"\u6e2c\u8a66\u5c08\u6848"}"#),
        MINIMAL_DUMP
    );
    // 重複鍵取最後一個(Python dict 相同)
    assert_eq!(
        dump_of(r#"{"schema_version":3,"name":"x","schema_version":2,"name":"測試專案"}"#),
        MINIMAL_DUMP
    );
}

#[test]
fn normalization_results() {
    let pj = validate(&parse(ALL_FIELDS_TEXT)).expect("接受");
    let s = &pj.segments[0];
    assert_eq!(s.pause_after, Some(0.4));
    assert_eq!(s.speed, Some(1.1));
    assert_eq!(
        s.annotations.iter().map(|a| a.index).collect::<Vec<_>>(),
        [0, 5]
    );
    assert_eq!(
        s.word_clarity.iter().map(|w| w.start).collect::<Vec<_>>(),
        [0, 4]
    );
    assert_eq!(
        s.fusion_off
            .iter()
            .map(|f| (f.index, f.length))
            .collect::<Vec<_>>(),
        [(0, 3), (4, 2)]
    );
    let pitch = s.pitch.as_ref().expect("pitch");
    assert_eq!(
        pitch.points[1],
        PitchPoint {
            pos: 0.51,
            semi: -2.1
        }
    );
    // 語調位置 -0.004 → -0.0(Python float(Decimal("-0.00")));半音 -0.04 → 0.0(Python `+ 0.0`)
    let pj = validate(&parse(&ps(
        r#", "pitch": {"points": [{"pos": -0.004, "semi": -0.04}, {"pos": 1, "semi": 0}], "roundness": null}"#,
    )))
    .expect("接受");
    let pt = pj.segments[0].pitch.as_ref().expect("pitch").points[0];
    assert_eq!(pt.pos.to_bits(), (-0.0f64).to_bits());
    assert_eq!(pt.semi.to_bits(), 0.0f64.to_bits());
    // 停頓 -0.04 → -0.0(Python 相同)
    let d = dump_of(&ps(r#", "pause_after": -0.04"#));
    assert!(d.contains("\"pause_after\": -0.0,"), "{d}");
    // 原樣保留:audio.seconds、post.seconds、post.trim 的整數 / 浮點表示
    let d = dump_of(&ps(&format!(
        r#", "audio": {{"id": "00000002", "seconds": 3}}, "post": {{"id": "00000003", "key": "{KEY}", "seconds": 12, "trim": 1E1}}"#
    )));
    assert!(d.contains("\"seconds\": 3\n"), "{d}");
    assert!(d.contains("\"seconds\": 12,"), "{d}");
    assert!(d.contains("\"trim\": 10.0\n"), "{d}");
    // 畫面軌:start 0.04 → 0.0、end 0.06 → 0.1
    let pj = validate(&parse(&p(&format!(
        r#", "assets": [{ASSET}], "images": [{{"id": "abcdef01", "asset": "00000000000000a3", "start": 0.04, "end": 0.06}}]"#
    ))))
    .expect("接受");
    assert_eq!((pj.images[0].start, pj.images[0].end), (0.0, Some(0.1)));
    // defaults 依 DEFAULTS 的鍵順序寫出,四捨五入到步長
    let pj = validate(&parse(&p(
        r#", "defaults": {"pause_paragraph": 0.25, "speed": 1.124}"#,
    )))
    .expect("接受");
    assert_eq!(
        pj.defaults,
        Defaults {
            speed: 1.1,
            pause_comma: 0.0,
            pause_sentence: 0.4,
            pause_paragraph: 0.3
        }
    );
}

#[test]
fn v1_upgrade_fills_v2_fields_and_rejects_v2_keys() {
    let pj = validate(&parse(V1_TEXT)).expect("v1 接受");
    assert_eq!(pj.schema_version, 2);
    let s = &pj.segments[0];
    assert_eq!((s.connect, s.clarity), (None, None));
    assert!(s.word_clarity.is_empty() && s.fusions.is_empty() && s.fusion_off.is_empty());
    assert!(s.pitch.is_none() && s.post.is_none());
    for key in [
        "connect",
        "clarity",
        "word_clarity",
        "pitch",
        "post",
        "fusions",
        "fusion_off",
    ] {
        let text = format!(
            r#"{{"schema_version": 1, "name": "x", "segments": [{{"id": "00000001", "text": "好", "{key}": null}}]}}"#
        );
        assert_eq!(
            errors_of(&text),
            [format!("segments[0] 有不認得的欄位:{key}")]
        );
    }
}

/// (說明, 原文, 錯誤訊息清單)。
fn reject_cases() -> Vec<(&'static str, String, Vec<String>)> {
    let s = |v: &[&str]| v.iter().map(|x| x.to_string()).collect::<Vec<_>>();
    let ver = |shown: &str| vec![format!("專案檔版本 {shown} {VERSION_TAIL}")];
    let seg = |extra: &str| ps(extra);
    let w = "segments[0]";
    let f2 = |extra: &str| {
        seg(&format!(
            r#", "fusions": [{{"index": 0, "length": 2, "zhuyin": "ㄏㄠ", "source": "rule"{extra}}}]"#
        ))
    };
    let img = |m: &str| p(&format!(r#", "assets": [{ASSET}], "images": [{m}]"#));
    let pitch = |pts: &str, r: &str| {
        seg(&format!(
            r#", "pitch": {{"points": [{pts}], "roundness": {r}}}"#
        ))
    };
    let post = |body: &str| seg(&format!(r#", "post": {{{body}}}"#));
    vec![
        // JSON 層(_read_project_file)
        ("空", String::new(), s(&["專案檔內容損壞,無法讀取"])),
        ("陣列", "[]".into(), s(&["專案檔必須是 JSON 物件"])),
        ("null", "null".into(), s(&["專案檔必須是 JSON 物件"])),
        ("結尾逗號", r#"{"a":1,}"#.into(), s(&["專案檔內容損壞,無法讀取"])),
        ("多餘內容", r#"{} {}"#.into(), s(&["專案檔內容損壞,無法讀取"])),
        ("字串內控制字元", "{\"a\":\"\u{1}\"}".into(), s(&["專案檔內容損壞,無法讀取"])),
        ("BOM", format!("\u{feff}{}", p("")), s(&["專案檔內容損壞,無法讀取"])),
        // 版本(_check_version,直接丟出)
        ("版本缺少", r#"{"name": "x"}"#.into(), ver("None")),
        ("版本 3", p("").replace("2,", "3,"), ver("3")),
        ("版本 2.0", r#"{"schema_version": 2.0, "name": "x"}"#.into(), ver("2.0")),
        ("版本 1.5e-10", r#"{"schema_version": 1.5e-10, "name": "x"}"#.into(), ver("1.5e-10")),
        ("版本字串", r#"{"schema_version": "2"}"#.into(), ver("'2'")),
        ("版本字串含單引號", r#"{"schema_version": "it's"}"#.into(), ver("\"it's\"")),
        ("版本字串兩種引號", r#"{"schema_version": "a'b\"c"}"#.into(), ver("'a\\'b\"c'")),
        ("版本字串截斷 20 字", r#"{"schema_version": "abcdefghijklmnopqrstuvwxyz"}"#.into(), ver("'abcdefghijklmnopqrs")),
        ("版本字串跳脫", r#"{"schema_version": "a\\b\n\t\r\u0001\u007f"}"#.into(), ver("'a\\\\b\\n\\t\\r\\x01\\x7f'")),
        ("版本字串 NBSP", r#"{"schema_version": "\u00a0\u3000"}"#.into(), ver("'\\xa0\\u3000'")),
        ("版本 true", r#"{"schema_version": true}"#.into(), ver("True")),
        ("版本 false", r#"{"schema_version": false}"#.into(), ver("False")),
        ("版本 null", r#"{"schema_version": null}"#.into(), ver("None")),
        ("版本陣列", r#"{"schema_version": [2]}"#.into(), ver("list")),
        ("版本物件", r#"{"schema_version": {}}"#.into(), ver("dict")),
        ("版本 u64 上限", r#"{"schema_version": 18446744073709551615}"#.into(), ver("18446744073709551615")),
        ("版本先於欄位檢查", r#"{"schema_version": 9, "bogus": 1}"#.into(), ver("9")),
        // 頂層
        ("不認得的欄位(排序)", p(r#", "zeta": 1, "Alpha": 2, "中文": 3"#), s(&["專案 有不認得的欄位:Alpha, zeta, 中文"])),
        ("缺少 name", r#"{"schema_version": 2}"#.into(), s(&["專案 缺少欄位:name"])),
        ("名稱不合格", p("").replace("測試專案", "a b"), s(&[NAME_HINT])),
        ("名稱不是字串", r#"{"schema_version": 2, "name": 5}"#.into(), s(&[NAME_HINT])),
        ("script 不是文字", p(r#", "script": null"#), s(&["script 必須是文字"])),
        ("文稿超過", p(&format!(r#", "script": "{}""#, "a".repeat(50001))), s(&["文稿超過 50000 字"])),
        ("defaults 不是物件", p(r#", "defaults": []"#), s(&["defaults 必須是物件"])),
        ("defaults 不認得", p(r#", "defaults": {"volume": 1}"#), s(&["defaults 有不認得的欄位:volume"])),
        ("defaults 語速型別", p(r#", "defaults": {"speed": "1"}"#), s(&["defaults.speed必須是數字"])),
        ("defaults 語速範圍", p(r#", "defaults": {"speed": 2.03}"#), s(&["defaults.speed必須在 0.5~2.0 倍之間"])),
        ("defaults 停頓範圍", p(r#", "defaults": {"pause_comma": 5.05}"#), s(&["defaults.pause_comma必須在 0~5 秒之間"])),
        ("assets 不是陣列", p(r#", "assets": null"#), s(&["assets 必須是陣列"])),
        ("assets 超過", p(&format!(r#", "assets": [{}]"#, vec!["0"; 201].join(","))), s(&["assets 超過 200 個"])),
        ("segments 超過", p(&format!(r#", "segments": [{}]"#, vec!["0"; 2001].join(","))), s(&["segments 超過 2000 個"])),
        ("images 不是陣列", p(r#", "images": 1"#), s(&["images 必須是陣列"])),
        ("images 超過", p(&format!(r#", "images": [{}]"#, vec!["0"; 201].join(","))), s(&["images 超過 200 個"])),
        // 素材
        ("素材不是物件", p(r#", "assets": [1]"#), s(&["assets[0] 必須是物件"])),
        ("素材缺少欄位", p(r#", "assets": [{"id": "00000001"}]"#), s(&["assets[0] 缺少欄位:kind, ext, bytes"])),
        ("素材 id 大寫", p(r#", "assets": [{"id": "ABCDEF01", "kind": "image", "ext": "png", "bytes": 1}]"#), s(&["assets[0].id 格式不正確"])),
        ("素材 id 重複", p(&format!(r#", "assets": [{ASSET}, {ASSET}]"#)), s(&["assets[1].id 重複"])),
        ("素材 kind", p(r#", "assets": [{"id": "00000001", "kind": "video", "ext": "png", "bytes": 1}]"#), s(&["assets[0].kind 只能是 image、audio、text"])),
        ("素材 ext", p(r#", "assets": [{"id": "00000001", "kind": "image", "ext": "gif", "bytes": 1}]"#), s(&["assets[0].ext 不在白名單內"])),
        ("素材 bytes 浮點", p(r#", "assets": [{"id": "00000001", "kind": "image", "ext": "png", "bytes": 1.0}]"#), s(&["assets[0].bytes 必須是非負整數"])),
        ("素材 label 控制字元", p(r#", "assets": [{"id": "00000001", "kind": "image", "ext": "png", "bytes": 1, "label": "a\u007f"}]"#), s(&["assets[0].label 必須是 255 字以內、不含控制字元的文字"])),
        ("素材總量", p(r#", "assets": [{"id": "00000001", "kind": "image", "ext": "png", "bytes": 2147483648}, {"id": "00000002", "kind": "text", "ext": "txt", "bytes": 1}]"#), s(&["素材總量超過 2 GB"])),
        ("素材多個錯誤", p(r#", "assets": [{"id": "bad", "kind": "image", "ext": "gif", "bytes": -5, "label": "\u0000"}]"#),
            s(&["assets[0].id 格式不正確", "assets[0].ext 不在白名單內", "assets[0].bytes 必須是非負整數", "assets[0].label 必須是 255 字以內、不含控制字元的文字"])),
        // 片段
        ("片段不是物件", p(r#", "segments": ["x"]"#), s(&["segments[0] 必須是物件"])),
        ("片段不認得", seg(r#", "volume": 1"#), s(&["segments[0] 有不認得的欄位:volume"])),
        ("片段缺少", p(r#", "segments": [{}]"#), s(&["segments[0] 缺少欄位:id, text"])),
        ("片段 id", seg("").replace("0000000000000001", "X"), s(&["segments[0].id 格式不正確"])),
        ("片段 id 與素材重複", p(&format!(r#", "assets": [{ASSET}], "segments": [{{"id": "00000000000000a3", "text": "好"}}]"#)), s(&["segments[0].id 重複"])),
        ("文字是空白", seg("").replace("我們今天很好", r" \u3000\t\u001c\u0085"), s(&["segments[0].text 是空的"])),
        ("文字不是字串", p(r#", "segments": [{"id": "00000001", "text": 5}]"#), s(&["segments[0].text 是空的"])),
        ("文字超過", seg("").replace("我們今天很好", &"字".repeat(501)), s(&["segments[0].text 超過 500 字"])),
        ("break", seg(r#", "break": null"#), s(&["segments[0].break 只能是 sentence、paragraph、forced、end"])),
        ("pause_after", seg(r#", "pause_after": 5.05"#), s(&["segments[0].pause_after必須在 0~5 秒之間"])),
        ("speed", seg(r#", "speed": false"#), s(&["segments[0].speed必須是數字"])),
        ("annotations 不是陣列", seg(r#", "annotations": {}"#), s(&["segments[0].annotations 必須是陣列"])),
        ("annotations 欄位", seg(r#", "annotations": [{"index": 0}]"#), s(&["segments[0].annotations 每項必須恰為 index、zhuyin 兩個欄位"])),
        ("annotations 索引", seg(r#", "annotations": [{"index": 1.0, "zhuyin": "ㄇㄣ"}]"#), s(&["segments[0].annotations 的索引超出句子範圍"])),
        ("annotations 非漢字", seg("").replace("我們今天很好", "你好,世界") .replace("\"}]", "\", \"annotations\": [{\"index\": 2, \"zhuyin\": \"ㄇㄣ\"}]}]"), s(&["segments[0].annotations 只能標在漢字上"])),
        ("annotations 重複", seg(r#", "annotations": [{"index": 1, "zhuyin": "ㄇㄣ"}, {"index": 1, "zhuyin": "ㄇㄣˊ"}]"#), s(&["segments[0].annotations 同一個字重複標註"])),
        ("annotations 注音", seg(r#", "annotations": [{"index": 1, "zhuyin": "˙ㄇㄣ˙"}]"#), s(&["segments[0]:注音標註只接受注音符號與聲調符號(ˊˇˋ˙),長度 1 到 5"])),
        ("audio 不是物件", seg(r#", "audio": []"#), s(&["segments[0].audio 必須是物件"])),
        ("audio 不認得", seg(r#", "audio": {"id": "00000002", "seconds": 1, "path": "x"}"#), s(&["segments[0].audio 有不認得的欄位:path"])),
        ("audio 缺少", seg(r#", "audio": {"id": "00000002"}"#), s(&["segments[0].audio 缺少欄位:seconds"])),
        ("audio id", seg(r#", "audio": {"id": "zz", "seconds": 1}"#), s(&["segments[0].audio.id 格式不正確"])),
        ("audio seconds", seg(r#", "audio": {"id": "00000002", "seconds": -0.5}"#), s(&["segments[0].audio.seconds 必須是非負數字"])),
        ("audio key", seg(r#", "audio": {"id": "00000002", "seconds": 1, "key": null}"#), s(&["segments[0].audio.key 格式不正確"])),
        ("connect", seg(r#", "connect": 50.0"#), s(&["segments[0].connect(連音)必須是 0 到 100 的整數(百分比)"])),
        ("clarity", seg(r#", "clarity": -1"#), s(&["segments[0].clarity(清晰度)必須是 0 到 100 的整數(百分比)"])),
        ("word_clarity 不是陣列", seg(r#", "word_clarity": 1"#), s(&["segments[0].word_clarity 必須是陣列"])),
        ("word_clarity 超過", seg(&format!(r#", "word_clarity": [{}]"#, vec![r#"{"start": 0, "end": 1, "value": 1}"#; 33].join(","))), s(&["segments[0].word_clarity 每句最多 32 段"])),
        ("word_clarity 欄位", seg(r#", "word_clarity": [{"start": 0, "end": 1}]"#), s(&["segments[0].word_clarity 每項必須恰為 start、end、value 三個欄位"])),
        ("word_clarity 範圍", seg(r#", "word_clarity": [{"start": 0, "end": 7, "value": 1}]"#), s(&["segments[0].word_clarity 的範圍必須在句子之內且起點小於終點"])),
        ("word_clarity 非漢字", seg("").replace("我們今天很好", "你好,世界").replace("\"}]", "\", \"word_clarity\": [{\"start\": 1, \"end\": 3, \"value\": 1}]}]"), s(&["segments[0].word_clarity 只能設定在漢字上(不可含標點或其他字元)"])),
        ("word_clarity 值 null", seg(r#", "word_clarity": [{"start": 0, "end": 2, "value": null}]"#), s(&["segments[0].word_clarity 的清晰度必須是 0 到 100 的整數(百分比)"])),
        ("word_clarity 值範圍", seg(r#", "word_clarity": [{"start": 0, "end": 2, "value": 101}]"#), s(&["segments[0].word_clarity 的清晰度必須是 0 到 100 的整數(百分比)"])),
        ("word_clarity 重疊", seg(r#", "word_clarity": [{"start": 2, "end": 4, "value": 1}, {"start": 0, "end": 3, "value": 2}]"#), s(&["segments[0].word_clarity 的範圍不可重疊"])),
        ("pitch 欄位", seg(r#", "pitch": {"points": []}"#), s(&["segments[0].pitch 必須恰為 points、roundness 兩個欄位"])),
        ("pitch 點數", pitch(r#"{"pos": 0, "semi": 0}"#, "1"), s(&["segments[0].pitch 的關鍵點必須是 2 到 16 個"])),
        ("pitch 點欄位", pitch(r#"{"pos": 0}, {"pos": 1, "semi": 0}"#, "1"), s(&["segments[0].pitch 每個關鍵點必須恰為 pos、semi 兩個欄位"])),
        ("pitch 位置型別", pitch(r#"{"pos": "0", "semi": 0}, {"pos": 1, "semi": 0}"#, "1"), s(&["segments[0].pitch 關鍵點位置必須是 0 到 1 的數字"])),
        ("pitch 位置範圍", pitch(r#"{"pos": 0, "semi": 0}, {"pos": 1.005, "semi": 0}"#, "1"), s(&["segments[0].pitch 關鍵點位置必須是 0 到 1 的數字"])),
        ("pitch 半音型別", pitch(r#"{"pos": 0, "semi": null}, {"pos": 1, "semi": 0}"#, "1"), s(&["segments[0].pitch 關鍵點音高必須是 -6 到 +6 半音的數字"])),
        ("pitch 半音範圍", pitch(r#"{"pos": 0, "semi": 6.05}, {"pos": 1, "semi": 0}"#, "1"), s(&["segments[0].pitch 關鍵點音高必須在 -6 到 +6 半音之間"])),
        ("pitch 遞增", pitch(r#"{"pos": 0.5, "semi": 0}, {"pos": 0.504, "semi": 0}"#, "1"), s(&["segments[0].pitch 關鍵點位置必須嚴格遞增"])),
        ("pitch 圓弧", pitch(r#"{"pos": 0, "semi": 0}, {"pos": 1, "semi": 0}"#, "200"), s(&["segments[0].pitch 的圓弧必須是 0 到 100 的整數(百分比)"])),
        ("post 格式", seg(r#", "post": 1"#), s(&["segments[0].post 格式不正確"])),
        ("post 多餘欄位", post(&format!(r#""id": "00000002", "key": "{KEY}", "seconds": 1, "trim": 0, "x": 1"#)), s(&["segments[0].post 格式不正確"])),
        ("post id", post(&format!(r#""id": "x", "key": "{KEY}", "seconds": 1, "trim": 0"#)), s(&["segments[0].post.id 格式不正確"])),
        ("post key", post(r#""id": "00000002", "key": "abc", "seconds": 1, "trim": 0"#), s(&["segments[0].post.key 格式不正確"])),
        ("post seconds", post(&format!(r#""id": "00000002", "key": "{KEY}", "seconds": -1, "trim": 0"#)), s(&["segments[0].post.seconds 必須是非負數字"])),
        ("post trim", post(&format!(r#""id": "00000002", "key": "{KEY}", "seconds": 1, "trim": "0""#)), s(&["segments[0].post.trim 必須是非負數字"])),
        ("post pitch_points", post(&format!(r#""id": "00000002", "key": "{KEY}", "seconds": 1, "trim": 0, "pitch_points": [[0, 0]]"#)), s(&["segments[0].post.pitch_points 必須是 2 到 30 個語調點"])),
        ("fusions 不是陣列", seg(r#", "fusions": {}"#), s(&["segments[0].fusions 必須是陣列"])),
        ("fusions 超過", seg("").replace("我們今天很好", &"字".repeat(40)).replace("\"}]", &format!("\", \"fusions\": [{}]}}]", (0..17).map(|i| format!(r#"{{"index": {}, "length": 2, "zhuyin": "ㄏㄠ", "source": "rule"}}"#, 2 * i)).collect::<Vec<_>>().join(","))), s(&["segments[0].fusions 每句最多 16 組"])),
        ("fusions 欄位", seg(r#", "fusions": [{"index": 0, "length": 2, "zhuyin": "ㄏㄠ"}]"#), s(&["segments[0].fusions 每項必須恰為 index、length、zhuyin、source 四個欄位(另可選填 display)"])),
        ("fusions 字數", seg(r#", "fusions": [{"index": 0, "length": 2.0, "zhuyin": "ㄏㄠ", "source": "rule"}]"#), s(&["segments[0].fusions 的字數必須是 2 到 4"])),
        ("fusions 位置", seg(r#", "fusions": [{"index": 5, "length": 2, "zhuyin": "ㄏㄠ", "source": "rule"}]"#), s(&["segments[0].fusions 的位置超出句子範圍"])),
        ("fusions 非漢字", seg("").replace("我們今天很好", "你好,世界").replace("\"}]", "\", \"fusions\": [{\"index\": 1, \"length\": 2, \"zhuyin\": \"ㄏㄠ\", \"source\": \"rule\"}]}]"), s(&["segments[0].fusions 只能合併相鄰的漢字"])),
        ("fusions 來源", seg(r#", "fusions": [{"index": 0, "length": 2, "zhuyin": "ㄏㄠ", "source": "auto"}]"#), s(&["segments[0].fusions 的來源只能是 rule、lexicon、manual"])),
        ("fusions 音節數", seg(r#", "fusions": [{"index": 0, "length": 3, "zhuyin": 1, "source": "rule"}]"#), s(&["segments[0]:合音注音的音節數必須少於字數(1 到 2 個,以半形空白分隔)"])),
        ("fusions 注音", seg(r#", "fusions": [{"index": 0, "length": 4, "zhuyin": "ㄏㄠ  ㄏㄠ", "source": "rule"}]"#), s(&["segments[0]:合音注音標註只接受注音符號與聲調符號(ˊˇˋ˙),長度 1 到 5"])),
        ("fusions 顯示字", f2(r#", "display": null"#), s(&["segments[0]:合音的顯示字必須是空字串或 1 到 4 個漢字"])),
        ("fusions 重疊", seg(r#", "fusions": [{"index": 1, "length": 2, "zhuyin": "ㄏㄠ", "source": "rule"}, {"index": 0, "length": 2, "zhuyin": "ㄏㄠ", "source": "rule"}]"#), s(&["segments[0].fusions 的範圍不可重疊"])),
        ("fusions 與標註", seg(r#", "annotations": [{"index": 1, "zhuyin": "ㄇㄣ"}], "fusions": [{"index": 0, "length": 2, "zhuyin": "ㄏㄠ", "source": "rule"}]"#), s(&["segments[0]:合音範圍內有聲調標註,請先清除該字的念法"])),
        ("fusions 與字詞清晰度", seg(r#", "word_clarity": [{"start": 1, "end": 3, "value": 1}], "fusions": [{"index": 0, "length": 2, "zhuyin": "ㄏㄠ", "source": "rule"}]"#), s(&["segments[0]:字詞清晰度的範圍必須完整包含或完全不含合音的字"])),
        ("fusions 在標註錯誤時不檢查", seg(r#", "annotations": 5, "fusions": 9"#), s(&["segments[0].annotations 必須是陣列"])),
        ("fusion_off 不是陣列", seg(r#", "fusion_off": "x""#), s(&["segments[0].fusion_off 必須是最多 32 筆的陣列"])),
        ("fusion_off 欄位", seg(r#", "fusion_off": [{"index": 0}]"#), s(&["segments[0].fusion_off 每項必須恰為 index、length 兩個欄位"])),
        ("fusion_off 範圍", seg(r#", "fusion_off": [{"index": 0, "length": 1}]"#), s(&["segments[0].fusion_off 的範圍超出句子"])),
        ("合音啟用與停用", seg(r#", "fusions": [{"index": 0, "length": 2, "zhuyin": "ㄏㄠ", "source": "rule"}], "fusion_off": [{"index": 0, "length": 2}]"#), s(&["segments[0]:同一範圍不能同時啟用與停用合音"])),
        // 畫面軌
        ("畫面不是物件", p(r#", "images": [null]"#), s(&["images[0] 必須是物件"])),
        ("畫面缺少", p(r#", "images": [{"id": "abcdef01"}]"#), s(&["images[0] 缺少欄位:asset, start"])),
        ("畫面素材", img(r#"{"id": "abcdef01", "asset": "00000002", "start": 0}"#), s(&["images[0].asset 不是本專案的圖片素材"])),
        ("畫面 start", img(r#"{"id": "abcdef01", "asset": "00000000000000a3", "start": -0.1}"#), s(&["images[0].start 必須是非負數字"])),
        ("畫面 end 型別", img(r#"{"id": "abcdef01", "asset": "00000000000000a3", "start": 0, "end": "1"}"#), s(&["images[0].end 必須是數字或 null"])),
        ("畫面 end 不大於 start", img(r#"{"id": "abcdef01", "asset": "00000000000000a3", "start": 1.0, "end": 1.04}"#), s(&["images[0].end 必須大於 start"])),
        ("畫面 id 與片段重複", p(&format!(r#", "assets": [{ASSET}], "segments": [{{"id": "abcdef01", "text": "好"}}], "images": [{{"id": "abcdef01", "asset": "00000000000000a3", "start": 0}}]"#)), s(&["images[0].id 重複"])),
        // 音訊總長(其他錯誤時不計算)
        ("音訊總長", p(r#", "defaults": {"speed": 0.5}, "segments": [{"id": "00000001", "text": "好", "audio": {"id": "00000002", "seconds": 1800.5}}]"#), s(&["專案音訊總長超過 60 分鐘(以變速後長度計)"])),
        ("其他錯誤時不算總長", p(r#", "script": 1, "segments": [{"id": "00000001", "text": "好", "audio": {"id": "00000002", "seconds": 99999}}]"#), s(&["script 必須是文字"])),
        // 累積順序
        ("片段多個錯誤的順序", seg(r#", "break": "x", "pause_after": 9, "speed": "a", "annotations": [1], "audio": {"id": "x", "seconds": 1}, "connect": -1, "clarity": 1000, "word_clarity": 7, "pitch": 1, "post": 2, "fusions": 3, "fusion_off": 4"#).replace("0000000000000001", "X"),
            [
                "id 格式不正確", "break 只能是 sentence、paragraph、forced、end", "pause_after必須在 0~5 秒之間", "speed必須是數字",
                "annotations 每項必須恰為 index、zhuyin 兩個欄位", "audio.id 格式不正確", "connect(連音)必須是 0 到 100 的整數(百分比)",
                "clarity(清晰度)必須是 0 到 100 的整數(百分比)", "word_clarity 必須是陣列", "pitch 必須恰為 points、roundness 兩個欄位",
                "post 格式不正確", "fusion_off 必須是最多 32 筆的陣列",
            ].iter().map(|m| format!("{w}.{m}")).collect()),
        ("跨區的累積順序", p(r#", "script": 1, "defaults": {"speed": 9}, "assets": [{"id": "x", "kind": "image", "ext": "png", "bytes": 1}], "segments": [{"id": "00000001", "text": ""}], "images": [{"id": "y", "asset": "z", "start": 0}]"#),
            s(&["script 必須是文字", "defaults.speed必須在 0.5~2.0 倍之間", "assets[0].id 格式不正確", "segments[0].text 是空的", "images[0].id 格式不正確", "images[0].asset 不是本專案的圖片素材"])),
    ]
}

#[test]
fn every_error_branch_has_the_python_message() {
    let cases = reject_cases();
    for (label, text, want) in &cases {
        assert_eq!(&errors_of(text), want, "{label}");
    }
    println!("拒絕案例:{} 個", cases.len());
}

#[test]
fn unknown_keys_are_truncated_to_200_chars() {
    let keys: Vec<String> = (0..20).map(|i| format!("key_{i:03}_xxxxxxxxxx")).collect();
    let extra: String = keys.iter().map(|k| format!(r#", "{k}": 0"#)).collect();
    let joined = keys.join(", ");
    let want: String = joined.chars().take(200).collect();
    assert_eq!(
        errors_of(&p(&extra)),
        [format!("專案 有不認得的欄位:{want}")]
    );
}

#[test]
fn raw_size_and_dump_size_limits() {
    const MAX: usize = 5 * 1024 * 1024;
    // 原文剛好 5 MB 可以讀、多 1 位元組拒絕(_read_project_file)
    let body = p("");
    let mut raw = vec![b' '; MAX - body.len()];
    raw.extend_from_slice(body.as_bytes());
    assert_eq!(raw.len(), MAX);
    assert!(parse_project_json(&raw).is_ok());
    raw.insert(0, b' ');
    let e = parse_project_json(&raw).expect_err("超過 5 MB");
    assert_eq!(e.errors(), ["專案檔超過 5 MB,拒絕載入"]);
    // 寫出超過 5 MB(縮排使標註陣列膨脹):原文小於 5 MB,寫出大於 5 MB
    let big = big_project(250);
    assert!(big.len() < MAX, "{}", big.len());
    assert_eq!(errors_of(&big), ["專案檔超過 5 MB"]);
    let small = big_project(150);
    assert!(dump_validated(&parse(&small)).expect("接受").len() <= MAX);
}

/// `n` 個 500 字的片段,每個字都有注音標註(寫出時每個標註約 49 位元組)。
fn big_project(n: usize) -> String {
    let anns: Vec<String> = (0..500)
        .map(|i| format!(r#"{{"index":{i},"zhuyin":"ㄅ"}}"#))
        .collect();
    let anns = anns.join(",");
    let text = "字".repeat(500);
    let segs: Vec<String> = (0..n)
        .map(|i| format!(r#"{{"id":"{i:016x}","text":"{text}","annotations":[{anns}]}}"#))
        .collect();
    format!(
        r#"{{"schema_version":2,"name":"大專案","segments":[{}]}}"#,
        segs.join(",")
    )
}

#[test]
fn project_type_round_trips_through_to_dict() {
    for text in [ALL_FIELDS_TEXT, V1_TEXT, ASSETS_TEXT] {
        let pj = validate(&parse(text)).expect("接受");
        let again = Project::from_dict(&pj.to_dict()).expect("to_dict 再驗證");
        assert_eq!(again, pj);
        assert_eq!(
            pj.dump_bytes().expect("寫出"),
            dump_validated(&parse(text)).unwrap()
        );
    }
    let pj = Project::new("新專案");
    assert_eq!(pj.schema_version, 2);
    assert_eq!(pj.defaults, Defaults::default());
    assert_eq!(
        String::from_utf8(pj.dump_bytes().expect("寫出")).unwrap(),
        MINIMAL_DUMP.replace("測試專案", "新專案")
    );
    // to_dict 的鍵與 Python Project.to_dict 相同
    let d = pj.to_dict();
    let keys: Vec<&str> = d.as_object().unwrap().keys().map(String::as_str).collect();
    let mut want = vec![
        "schema_version",
        "name",
        "script",
        "defaults",
        "segments",
        "images",
        "assets",
    ];
    want.sort_unstable();
    assert_eq!(keys, want);
}

#[test]
fn to_dict_validation_catches_out_of_range_fields() {
    let mut pj = Project::new("x");
    pj.name = "a b".into();
    pj.defaults.speed = 3.0;
    pj.segments.push(Segment::new("00000001", "  "));
    pj.images.push(Image {
        id: "abcdef01".into(),
        asset: "00000002".into(),
        start: 1.0,
        end: Some(0.5),
    });
    let e = pj.dump_bytes().expect_err("不合法");
    assert_eq!(
        e.errors(),
        [
            NAME_HINT,
            "defaults.speed必須在 0.5~2.0 倍之間",
            "segments[0].text 是空的",
            "images[0].asset 不是本專案的圖片素材",
            "images[0].end 必須大於 start",
        ]
    );
}

/// 非有限浮點(Python `float("nan")` 等):`to_dict` 不寫成 `null`,而是讓 `validate` 以該欄位「必須是數字」類訊息拒絕,
/// 與 Python 對 `nan` / `inf` 欄位的訊息相同(Python 的對照見 `json_differences.rs` D-3);寫出路徑因此不會遇到非有限值。
#[test]
fn non_finite_floats_are_rejected_like_python() {
    for bad in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        let mut pj = Project::new("x");
        pj.defaults.pause_comma = bad;
        let mut s = Segment::new("00000001", "好");
        s.speed = Some(bad);
        s.pause_after = Some(bad);
        s.pitch = Some(Pitch {
            points: vec![
                PitchPoint {
                    pos: bad,
                    semi: 0.0,
                },
                PitchPoint {
                    pos: 1.0,
                    semi: bad,
                },
            ],
            roundness: None,
        });
        s.post = Some(Post {
            id: "00000002".into(),
            key: KEY.into(),
            seconds: Number::from(1),
            trim: Number::from(0),
            pitch_points: Some(vec![[0.0, bad], [1.0, 0.0]]),
        });
        pj.segments.push(s);
        pj.assets.push(Asset {
            id: "00000003".into(),
            kind: "image".into(),
            ext: "png".into(),
            bytes: 1,
            label: None,
        });
        pj.images.push(Image {
            id: "00000004".into(),
            asset: "00000003".into(),
            start: bad,
            end: None,
        });
        pj.images.push(Image {
            id: "00000005".into(),
            asset: "00000003".into(),
            start: 0.0,
            end: Some(bad),
        });
        let e = pj.dump_bytes().expect_err("非有限值");
        assert_eq!(
            e.errors(),
            [
                "defaults.pause_comma必須是數字",
                "segments[0].pause_after必須是數字",
                "segments[0].speed必須是數字",
                "segments[0].pitch 關鍵點位置必須是 0 到 1 的數字",
                "segments[0].post.pitch_points 每個語調點必須是 [位置, 半音] 兩個數字",
                "images[0].start 必須是非負數字",
                "images[1].end 必須是數字或 null",
            ],
            "{bad}"
        );
    }
}

#[test]
fn model_holds_optional_fields_like_python() {
    let pj = validate(&parse(ASSETS_TEXT)).expect("接受");
    assert_eq!(pj.assets[0].label, None);
    assert_eq!(pj.assets[1].label, Some(Some("旁白".into())));
    assert_eq!(pj.assets[2].label, Some(None));
    let pj = validate(&parse(&ps(
        r#", "audio": {"id": "00000002", "seconds": 1E1}"#,
    )))
    .unwrap();
    assert_eq!(
        pj.segments[0].audio,
        Some(Audio {
            id: "00000002".into(),
            seconds: serde_json::from_str("10.0").unwrap(),
            key: None
        })
    );
    assert_eq!(pj.segments[0].brk, "end");
}

#[test]
fn errors_display_joined_with_semicolon() {
    let e = error_of_bytes(p(r#", "script": 1, "defaults": []"#).as_bytes());
    assert_eq!(e.to_string(), "script 必須是文字;defaults 必須是物件");
    // validate 直接收非物件
    let e = validate(&json!([1])).expect_err("非物件");
    assert_eq!(e.errors(), ["專案檔必須是 JSON 物件"]);
}
