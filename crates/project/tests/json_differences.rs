//! L1:「### 行為差異清單」D-1~D-6(與 T2 補記)逐條鎖住 Rust 行為(CHG-20261128-01 T2,代決 5、6)。
//!
//! 這些案例 Python 會丟出非 `ProjectError` 的例外、或結果刻意與 Rust 不同,所以**不**放進比對 fixture
//! (CHG-20261124-01 代決 11 先例)。每一條先斷言 Rust 的結果(三平台);在 macOS / Linux 另以 Python 子程序
//! 確認 Python 側的對應行為(D-5 確認 Python 丟出的例外類別),證明清單中的差異是真的差異。
//!
//! Python 子程序:`[<python>, "-I", "-B", "-X", "utf8", "-c", <腳本>, <repo>]`,直譯器取 `PARITY_PYTHON`,沒有時
//! `python3`;repo 根目錄附加在 `sys.path` 末端,匯入後核對 `project`、`names`、`safe_fs`、`value_checks`、
//! `breezy_worker` 的 `__file__` 來自 repo 根目錄,`project.ROOT` 改指 Python 自己以 `tempfile.mkdtemp` 建立的
//! 暫存目錄(先 `realpath`);每個原文寫進該暫存目錄的 `project.json`,以 `project._read_project_file` 讀回後
//! `validate` 與 `_dump`,結束時刪除暫存目錄。Python 無法啟動時判失敗,不略過;測試前後確認 repo 的 `projects/` 不變。

use project::validate::{dump_validated, parse_project_json};
#[cfg(unix)]
use serde_json::Value;

const CORRUPT: &str = "專案檔內容損壞,無法讀取";
const VERSION_TAIL: &str = "不支援,拒絕載入(本版讀 schema_version 2,舊版 1 會自動升級)";
const KEY: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

/// Rust 的結果:接受時為 `_dump` 字串,拒絕時為錯誤訊息清單。
#[derive(Debug, Clone, PartialEq, Eq)]
enum Out {
    Dump(String),
    Rejected(Vec<String>),
    /// 只出現在 Python 側:非 `ProjectError` 的例外類別名稱。
    Exception(String),
}

fn rust(raw: &str) -> Out {
    match parse_project_json(raw.as_bytes()).and_then(|v| dump_validated(&v)) {
        Ok(b) => Out::Dump(String::from_utf8(b).expect("UTF-8")),
        Err(e) => Out::Rejected(e.errors().to_vec()),
    }
}

fn rejected(msgs: &[&str]) -> Out {
    Out::Rejected(msgs.iter().map(|m| m.to_string()).collect())
}

fn ver(shown: &str) -> Out {
    Out::Rejected(vec![format!("專案檔版本 {shown} {VERSION_TAIL}")])
}

fn dump_text(o: &Out) -> &str {
    match o {
        Out::Dump(d) => d,
        other => panic!("預期接受:{other:?}"),
    }
}

/// JSON 字串內的 `\uXXXX` 跳脫(執行期組字,避免原始碼出現跳脫序列)。
fn esc(hex: &str) -> String {
    format!("{}u{hex}", '\\')
}

fn p(extra: &str) -> String {
    format!(r#"{{"schema_version": 2, "name": "x"{extra}}}"#)
}

fn ps(extra: &str) -> String {
    p(&format!(
        r#", "segments": [{{"id": "00000001", "text": "我們今天很好"{extra}}}]"#
    ))
}

const ASSET: &str = r#"{"id": "000000a3", "kind": "image", "ext": "png", "bytes": 1}"#;

fn img(m: &str) -> String {
    p(&format!(r#", "assets": [{ASSET}], "images": [{m}]"#))
}

/// (原文, Rust 預期, Python 預期)。
type Case = (String, Out, Out);

/// macOS / Linux:以 Python 子程序確認每一筆的 Python 結果。Windows 無法執行 `project.py`(頂層 import `fcntl`,
/// 事實 H),只斷言 Rust。
fn check(name: &str, cases: &[Case]) {
    for (raw, want_rust, _) in cases {
        assert_eq!(&rust(raw), want_rust, "{name} Rust:{raw:.200}");
    }
    #[cfg(unix)]
    {
        let raws: Vec<&str> = cases.iter().map(|(r, _, _)| r.as_str()).collect();
        let got = py::run(&raws);
        for ((raw, _, want_py), got) in cases.iter().zip(got) {
            assert_eq!(&got, want_py, "{name} Python:{raw:.200}");
        }
        println!("{name}:{} 筆,Rust 與 Python 各自符合預期", cases.len());
    }
    #[cfg(not(unix))]
    println!(
        "{name}:{} 筆 Rust 符合預期(Python 端在 Windows 無法執行,略過 Python 確認)",
        cases.len()
    );
}

#[test]
fn d1_negative_zero_literal() {
    let pause = rust(&ps(r#", "pause_after": -0"#));
    let pause_py =
        Out::Dump(dump_text(&pause).replace("\"pause_after\": -0.0,", "\"pause_after\": 0.0,"));
    assert_ne!(pause, pause_py);
    let start = rust(&img(
        r#"{"id": "abcdef01", "asset": "000000a3", "start": -0}"#,
    ));
    let start_py = Out::Dump(dump_text(&start).replace("\"start\": -0.0,", "\"start\": 0.0,"));
    assert_ne!(start, start_py);
    let connect_py = rust(&ps(r#", "connect": 0"#));
    let ann_py = rust(&ps(r#", "annotations": [{"index": 0, "zhuyin": "ㄨㄛˇ"}]"#));
    check(
        "D-1",
        &[
            // 整數欄位:Rust 拒絕(訊息同該欄位的整數錯誤),Python 視為 0
            (
                ps(r#", "connect": -0"#),
                rejected(&["segments[0].connect(連音)必須是 0 到 100 的整數(百分比)"]),
                connect_py,
            ),
            (
                ps(r#", "annotations": [{"index": -0, "zhuyin": "ㄨㄛˇ"}]"#),
                rejected(&["segments[0].annotations 的索引超出句子範圍"]),
                ann_py,
            ),
            (
                r#"{"schema_version": -0, "name": "x"}"#.into(),
                ver("-0.0"),
                ver("0"),
            ),
            // 數值欄位:Rust 寫出 -0.0,Python 寫出 0.0
            (ps(r#", "pause_after": -0"#), pause.clone(), pause_py),
            (
                img(r#"{"id": "abcdef01", "asset": "000000a3", "start": -0}"#),
                start.clone(),
                start_py,
            ),
        ],
    );
}

#[test]
fn d2_integers_beyond_64_bits() {
    let big = "18446744073709551616"; // 2^64
    let trim = rust(&ps(&format!(
        r#", "post": {{"id": "00000002", "key": "{KEY}", "seconds": 1, "trim": {big}}}"#
    )));
    assert!(dump_text(&trim).contains("\"trim\": 1.8446744073709552e+19\n"));
    let trim_py = Out::Dump(dump_text(&trim).replace("1.8446744073709552e+19", big));
    check(
        "D-2",
        &[
            // 原樣保留欄位:Rust 寫成浮點表示,Python 寫回整數
            (
                ps(&format!(
                    r#", "post": {{"id": "00000002", "key": "{KEY}", "seconds": 1, "trim": {big}}}"#
                )),
                trim,
                trim_py,
            ),
            // 整數欄位:Rust 以型別錯誤拒絕,Python 進入範圍檢查(訊息不同)
            (
                p(&format!(
                    r#", "assets": [{{"id": "000000a3", "kind": "image", "ext": "png", "bytes": {big}}}]"#
                )),
                rejected(&["assets[0].bytes 必須是非負整數"]),
                rejected(&["素材總量超過 2 GB"]),
            ),
            // 低於 i64:兩邊訊息恰好相同(型別錯誤與範圍錯誤共用一則訊息)
            (
                ps(r#", "connect": -9223372036854775809"#),
                rejected(&["segments[0].connect(連音)必須是 0 到 100 的整數(百分比)"]),
                rejected(&["segments[0].connect(連音)必須是 0 到 100 的整數(百分比)"]),
            ),
            (
                format!(r#"{{"schema_version": {big}, "name": "x"}}"#),
                ver("1.8446744073709552e+"),
                ver(big),
            ),
        ],
    );
}

/// D-3 與非有限浮點:Rust 解析失敗;Python 解析成功、由欄位檢查以該欄位的訊息拒絕——這些訊息也就是
/// `Project::to_dict` 遇到非有限浮點時 Rust `validate` 給的訊息(`tests/validate.rs`)。
#[test]
fn d3_non_finite_numbers() {
    let mut cases: Vec<Case> = ["NaN", "Infinity", "-Infinity", "1e400", "-1e400"]
        .iter()
        .map(|lit| {
            (
                ps(&format!(r#", "speed": {lit}"#)),
                rejected(&[CORRUPT]),
                rejected(&["segments[0].speed必須是數字"]),
            )
        })
        .collect();
    // 與 tests/validate.rs non_finite_floats_are_rejected_like_python 同一組欄位
    let all = format!(
        r#"{{"schema_version": 2, "name": "x", "defaults": {{"pause_comma": NaN}}, "segments": [{{"id": "00000001", "text": "好", "pause_after": NaN, "speed": Infinity, "pitch": {{"points": [{{"pos": NaN, "semi": 0}}, {{"pos": 1, "semi": NaN}}], "roundness": null}}, "post": {{"id": "00000002", "key": "{KEY}", "seconds": 1, "trim": 0, "pitch_points": [[0, -Infinity], [1, 0]]}}}}], "assets": [{{"id": "00000003", "kind": "image", "ext": "png", "bytes": 1}}], "images": [{{"id": "00000004", "asset": "00000003", "start": NaN}}, {{"id": "00000005", "asset": "00000003", "start": 0, "end": Infinity}}]}}"#
    );
    cases.push((
        all,
        rejected(&[CORRUPT]),
        rejected(&[
            "defaults.pause_comma必須是數字",
            "segments[0].pause_after必須是數字",
            "segments[0].speed必須是數字",
            "segments[0].pitch 關鍵點位置必須是 0 到 1 的數字",
            "segments[0].post.pitch_points 每個語調點必須是 [位置, 半音] 兩個數字",
            "images[0].start 必須是非負數字",
            "images[1].end 必須是數字或 null",
        ]),
    ));
    check("D-3", &cases);
}

#[test]
fn d4_nesting_deeper_than_128() {
    let nested = |n: usize| {
        p(&format!(
            r#", "script": {}{}"#,
            "[".repeat(n),
            "]".repeat(n)
        ))
    };
    // 頂層物件算一層:script 內 126 層陣列(合計 127 層)兩邊相同
    let script_error = rejected(&["script 必須是文字"]);
    check(
        "D-4",
        &[
            (nested(126), script_error.clone(), script_error.clone()),
            (nested(200), rejected(&[CORRUPT]), script_error.clone()),
            (nested(900), rejected(&[CORRUPT]), script_error),
        ],
    );
}

#[test]
fn d5_python_raises_unclassified_exceptions() {
    let exc = |name: &str| Out::Exception(name.to_string());
    let huge_int = format!("1{}", "0".repeat(400));
    let pitch = |pos: &str, semi: &str| {
        ps(&format!(
            r#", "pitch": {{"points": [{{"pos": {pos}, "semi": {semi}}}, {{"pos": 1, "semi": 0}}], "roundness": null}}"#
        ))
    };
    check(
        "D-5",
        &[
            // 超過 1.8e308 的整數:Python math.isfinite 丟 OverflowError;Rust 解析失敗
            (
                ps(&format!(r#", "speed": {huge_int}"#)),
                rejected(&[CORRUPT]),
                exc("OverflowError"),
            ),
            // 不成對代理:Python 載入成功、_dump 的 encode 丟 UnicodeEncodeError;Rust 解析失敗
            (
                p(&format!(r#", "script": "a{}b""#, esc("d800"))),
                rejected(&[CORRUPT]),
                exc("UnicodeEncodeError"),
            ),
            (
                p(&format!(r#", "script": "{}""#, esc("dc00"))),
                rejected(&[CORRUPT]),
                exc("UnicodeEncodeError"),
            ),
            // 四捨五入後超過 28 位數:Python Decimal.quantize 丟 InvalidOperation;Rust 回該欄位的範圍錯誤
            // (T1 補記:normalize_speed / normalize_pause;T2 補記:語調關鍵點、畫面軌 start / end)
            (
                ps(r#", "speed": 1e30"#),
                rejected(&["segments[0].speed必須在 0.5~2.0 倍之間"]),
                exc("InvalidOperation"),
            ),
            (
                p(r#", "defaults": {"pause_comma": 1e30}"#),
                rejected(&["defaults.pause_comma必須在 0~5 秒之間"]),
                exc("InvalidOperation"),
            ),
            (
                pitch("1e30", "0"),
                rejected(&["segments[0].pitch 關鍵點位置必須是 0 到 1 的數字"]),
                exc("InvalidOperation"),
            ),
            (
                pitch("0", "-1e30"),
                rejected(&["segments[0].pitch 關鍵點音高必須在 -6 到 +6 半音之間"]),
                exc("InvalidOperation"),
            ),
            (
                img(r#"{"id": "abcdef01", "asset": "000000a3", "start": 1e30}"#),
                rejected(&["images[0].start 必須是非負數字"]),
                exc("InvalidOperation"),
            ),
            (
                img(r#"{"id": "abcdef01", "asset": "000000a3", "start": 0, "end": -1e30}"#),
                rejected(&["images[0].end 必須是數字或 null"]),
                exc("InvalidOperation"),
            ),
        ],
    );
}

/// D-6:`audio`、`post`、素材的鍵以 Python 程式產生時的順序寫出(Python 保留輸入檔的順序);內容相同。
/// T2 補記:`defaults` 內多個欄位不合法時,Rust 依鍵的碼位順序累積訊息(Python 依輸入檔順序)。
#[test]
fn d6_key_order() {
    let audio = ps(
        r#", "audio": {"seconds": 1.5, "key": "KEY", "id": "00000002"}"#
            .replace("KEY", KEY)
            .as_str(),
    );
    let post = ps(&format!(
        r#", "post": {{"trim": 0, "pitch_points": [[0, 0], [1, 1]], "seconds": 2, "key": "{KEY}", "id": "00000002"}}"#
    ));
    let asset = p(
        r#", "assets": [{"label": "圖", "bytes": 5, "ext": "png", "kind": "image", "id": "000000a3"}]"#,
    );
    let canonical = [
        rust(&ps(&format!(
            r#", "audio": {{"id": "00000002", "seconds": 1.5, "key": "{KEY}"}}"#
        ))),
        rust(&ps(&format!(
            r#", "post": {{"id": "00000002", "key": "{KEY}", "seconds": 2, "trim": 0, "pitch_points": [[0, 0], [1, 1]]}}"#
        ))),
        rust(&p(
            r#", "assets": [{"id": "000000a3", "kind": "image", "ext": "png", "bytes": 5, "label": "圖"}]"#,
        )),
    ];
    for (text, want) in [&audio, &post, &asset].into_iter().zip(&canonical) {
        assert_eq!(&rust(text), want, "Rust 以固定鍵順序寫出");
    }
    #[cfg(unix)]
    {
        let got = py::run(&[audio.as_str(), post.as_str(), asset.as_str()]);
        for (g, want) in got.iter().zip(&canonical) {
            let (g, want) = (dump_text(g), dump_text(want));
            assert_ne!(g, want, "Python 保留輸入順序,位元組不同");
            let gv: Value = serde_json::from_str(g).unwrap();
            let wv: Value = serde_json::from_str(want).unwrap();
            assert_eq!(gv, wv, "內容相同");
        }
        println!("D-6:3 筆鍵順序不同、內容相同");
    }
    check(
        "D-6(defaults 訊息順序,T2 補記)",
        &[(
            p(r#", "defaults": {"speed": 9, "pause_comma": -1}"#),
            rejected(&[
                "defaults.pause_comma必須在 0~5 秒之間",
                "defaults.speed必須在 0.5~2.0 倍之間",
            ]),
            rejected(&[
                "defaults.speed必須在 0.5~2.0 倍之間",
                "defaults.pause_comma必須在 0~5 秒之間",
            ]),
        )],
    );
}

/// D-15(T2 補記):`schema_version` 為字串時訊息中的 Python `repr`。Rust 依 Python 規則加引號與跳脫
/// (ASCII 控制字元、C1 控制字元、非空白的空格類、行與段落分隔、常見格式字元、私用區、平面 4~13、非字元);
/// 其他 Unicode 14 未指派碼位 Python 以 `\uXXXX` 顯示,Rust 原樣顯示。
#[test]
fn d15_version_repr_of_unassigned_code_points() {
    let unassigned = char::from_u32(0x378).expect("碼位");
    check(
        "D-15",
        &[
            (
                format!(r#"{{"schema_version": "{}", "name": "x"}}"#, esc("0378")),
                ver(&format!("'{unassigned}'")),
                ver(&format!("'{}'", esc("0378"))),
            ),
            // 列舉到的不可列印字元兩邊相同
            (
                format!(
                    r#"{{"schema_version": "{}{}{}{}", "name": "x"}}"#,
                    esc("0085"),
                    esc("200b"),
                    esc("e000"),
                    esc("feff")
                ),
                ver(
                    &format!("'{}x85{}{}{}", '\\', esc("200b"), esc("e000"), esc("feff"))
                        .chars()
                        .take(20)
                        .collect::<String>(),
                ),
                ver(
                    &format!("'{}x85{}{}{}", '\\', esc("200b"), esc("e000"), esc("feff"))
                        .chars()
                        .take(20)
                        .collect::<String>(),
                ),
            ),
        ],
    );
}

/// 不是差異、但 fixture 放不下的大小上限:Python 也在同一點拒絕(只在 macOS / Linux 確認)。
#[test]
fn size_limits_agree_with_python() {
    const MAX: usize = 5 * 1024 * 1024;
    let big = |n: usize| {
        let anns: Vec<String> = (0..500)
            .map(|i| format!(r#"{{"index":{i},"zhuyin":"ㄅ"}}"#))
            .collect();
        let text = "字".repeat(500);
        let segs: Vec<String> = (0..n)
            .map(|i| {
                format!(
                    r#"{{"id":"{i:016x}","text":"{text}","annotations":[{}]}}"#,
                    anns.join(",")
                )
            })
            .collect();
        format!(
            r#"{{"schema_version":2,"name":"大專案","segments":[{}]}}"#,
            segs.join(",")
        )
    };
    let under = big(150);
    let under_rust = rust(&under);
    let mut padded = " ".repeat(MAX - p("").len() + 1);
    padded.push_str(&p(""));
    check(
        "大小上限",
        &[
            (
                big(250),
                rejected(&["專案檔超過 5 MB"]),
                rejected(&["專案檔超過 5 MB"]),
            ),
            (under, under_rust.clone(), under_rust),
            (
                padded,
                rejected(&["專案檔超過 5 MB,拒絕載入"]),
                rejected(&["專案檔超過 5 MB,拒絕載入"]),
            ),
        ],
    );
}

#[cfg(unix)]
mod py {
    //! Python 子程序(只在 macOS / Linux)。
    use super::Out;
    use std::ffi::OsString;
    use std::io::Write;
    use std::path::{Path, PathBuf};
    use std::process::{Command, Stdio};

    const SCRIPT: &str = r#"
import json, os, shutil, sys, tempfile
repo = sys.argv[1]
sys.path.append(repo)
import project as pj, names, safe_fs, value_checks, breezy_worker
for m in (pj, names, safe_fs, value_checks, breezy_worker):
    assert os.path.dirname(os.path.realpath(m.__file__)) == repo, m.__file__
work = os.path.realpath(tempfile.mkdtemp(prefix="project-t2-diff-"))
pj.ROOT = __import__("pathlib").Path(work)
sys.stdout.reconfigure(encoding="utf-8", newline="\n")
try:
    for line in sys.stdin.read().split("\n"):
        if not line:
            continue
        with open(os.path.join(work, pj.PROJECT_FILE), "wb") as f:
            f.write(bytes.fromhex(line))
        fd = os.open(work, os.O_RDONLY | os.O_DIRECTORY)
        try:
            data = pj._read_project_file(fd, pj.PROJECT_FILE)
            r = {"dump": pj._dump(pj.validate(data)).decode("utf-8")}
        except pj.ProjectError as e:
            r = {"errors": list(e.errors)}
        except Exception as e:
            r = {"exception": type(e).__name__}
        finally:
            os.close(fd)
        sys.stdout.write(json.dumps(r) + "\n")
finally:
    shutil.rmtree(work)
"#;

    fn python() -> OsString {
        std::env::var_os("PARITY_PYTHON")
            .filter(|v| !v.is_empty())
            .unwrap_or_else(|| OsString::from("python3"))
    }

    fn repo_root() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .canonicalize()
            .expect("無法取得 repo 根目錄")
    }

    /// repo 工作副本 `projects/` 的狀態(不存在為 `None`;存在時為項目名稱與 mtime 的清單)。
    fn projects_state(repo: &Path) -> Option<Vec<(OsString, std::time::SystemTime)>> {
        let dir = repo.join("projects");
        std::fs::symlink_metadata(&dir).ok()?;
        let mut out: Vec<_> = std::fs::read_dir(&dir)
            .expect("讀取 projects/")
            .map(|e| {
                let e = e.expect("目錄項");
                let m = e.metadata().expect("metadata");
                (e.file_name(), m.modified().expect("mtime"))
            })
            .collect();
        out.sort();
        Some(out)
    }

    fn hex(s: &str) -> String {
        s.bytes().map(|b| format!("{b:02x}")).collect()
    }

    /// 每個原文在 Python 的結果(順序相同)。
    pub fn run(raws: &[&str]) -> Vec<Out> {
        let repo = repo_root();
        let before = projects_state(&repo);
        let mut child = Command::new(python())
            .args(["-I", "-B", "-X", "utf8", "-c", SCRIPT])
            .arg(&repo)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap_or_else(|e| panic!("無法啟動 Python 直譯器 {:?}(判失敗,不略過):{e}", python()));
        let input: String = raws.iter().map(|r| hex(r) + "\n").collect();
        let mut stdin = child.stdin.take().expect("stdin");
        let writer = std::thread::spawn(move || {
            stdin.write_all(input.as_bytes()).expect("寫入 stdin");
        });
        let out = child.wait_with_output().expect("等待 Python");
        writer.join().expect("寫入執行緒");
        assert!(
            out.status.success(),
            "Python 失敗:{}",
            String::from_utf8_lossy(&out.stderr)
        );
        assert_eq!(projects_state(&repo), before, "repo 的 projects/ 不得改變");
        let text = String::from_utf8(out.stdout).expect("UTF-8");
        let got: Vec<Out> = text
            .split('\n')
            .filter(|l| !l.is_empty())
            .map(|l| {
                let v: serde_json::Value = serde_json::from_str(l).expect("Python 輸出");
                if let Some(d) = v.get("dump") {
                    Out::Dump(d.as_str().expect("字串").to_string())
                } else if let Some(e) = v.get("errors") {
                    Out::Rejected(
                        e.as_array()
                            .expect("陣列")
                            .iter()
                            .map(|m| m.as_str().expect("字串").to_string())
                            .collect(),
                    )
                } else {
                    Out::Exception(v["exception"].as_str().expect("字串").to_string())
                }
            })
            .collect();
        assert_eq!(got.len(), raws.len());
        got
    }
}
