//! L2:專案檔雙向往返(退出條件 (1) 後半;CHG-20261128-01 代決 10、T6)。`#![cfg(unix)]`:Python 端為真的
//! `project.py`(頂層 import `fcntl`,Windows 無法執行)。
//!
//! 語料:`crates/parity/fixtures/project/validate_text.json` 的全部接受案例(`ok_` 開頭),加上預期結果表中
//! `project.save_sequence`、`project.create` 最後的樹裡的每個 `project.json` 與 `history/` 版本檔(Python 寫出的檔案)
//! 中 Rust 可載入者。每個語料各用一個測試自己排他建立的暫存根目錄;開頭與結尾確認 repo 工作副本的 `projects/` 不變。
//!
//! - **Rust → Python**:Rust `save` 寫出 `project.json` → Python `load` 後 `_dump(validate(...))`,與 Rust 寫出的檔案逐位元組相同;
//! - **Python → Rust**:Python `save`(輸入為 Rust 寫出的同一份內容,鍵順序因此與 Python 程式自己產生時相同——
//!   手動編輯造成的鍵順序差異屬行為差異 D-6,由 `json_differences.rs` 鎖住)→ Rust `load` 後 `dump_bytes`,與 Python
//!   寫出的檔案逐位元組相同;
//! - **history**:Rust 連續存 3 次後 Python `list_history` / `load_version` 與 Rust 相同、版本檔與其 dump 逐位元組相同;
//!   反之 Python 連續存 3 次後 Rust 相同。
//!
//! 以名稱為 Windows 保留名稱的語料(例:`CON`,validate_text 的「既有專案檔」案例):Rust 只能存到**既有**的保留名稱專案
//! (D-7),所以 Rust → Python 方向先建立空的專案目錄。執行時印出兩個方向的語料筆數與一致筆數。
#![cfg(unix)]

mod common;

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use common::{BASE_MS, ProjectsGuard, PyOut, TempTree, TestClock, call_each};
use project::read;
use project::validate::{parse_project_json, validate};
use project::write;
use project::Project;
use serde_json::{Value, json};
use studio_core::names::is_windows_reserved;

fn parity_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../parity")
}

/// `{"parts": [片段…]}` 展開(同 `parity::units_project::parts_text`;fixture 已由 parity 的測試驗證格式)。
fn parts(v: &Value) -> String {
    v.as_array()
        .expect("parts 為陣列")
        .iter()
        .map(|p| match p {
            Value::String(s) => s.clone(),
            other => other["repeat"]
                .as_str()
                .expect("repeat")
                .repeat(other["count"].as_u64().expect("count") as usize),
        })
        .collect()
}

fn text_bytes(v: &Value) -> Vec<u8> {
    match v {
        Value::String(s) => s.as_bytes().to_vec(),
        Value::Object(o) if o.contains_key("parts") => parts(&o["parts"]).into_bytes(),
        Value::Object(o) => {
            let hex = o["hex"].as_str().expect("hex");
            (0..hex.len())
                .step_by(2)
                .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).expect("十六進位"))
                .collect()
        }
        other => panic!("不認得的輸入:{other}"),
    }
}

/// 語料:(來源說明, 驗證後的專案)。
fn corpus() -> (Vec<(String, Project)>, usize) {
    let mut out = Vec::new();
    let fixture: Value = serde_json::from_str(
        &std::fs::read_to_string(parity_dir().join("fixtures/project/validate_text.json"))
            .expect("讀取 validate_text.json"),
    )
    .expect("fixture");
    let mut accepted = 0;
    for c in fixture["cases"].as_array().expect("cases") {
        let id = c["id"].as_str().expect("id");
        if !id.starts_with("ok_") {
            continue;
        }
        accepted += 1;
        let data = parse_project_json(&text_bytes(&c["input"])).expect("接受案例可解析");
        out.push((format!("validate_text {id}"), validate(&data).expect("接受案例可驗證")));
    }
    let table: Value = serde_json::from_str(
        &std::fs::read_to_string(parity_dir().join("tests/data/project_expected.json"))
            .expect("讀取預期結果表"),
    )
    .expect("預期結果表");
    let mut seen = BTreeSet::new();
    for unit in ["project.save_sequence", "project.create"] {
        for (id, entry) in table[unit].as_object().expect("單元") {
            for row in entry["all"]["value"]["tree"].as_array().expect("tree") {
                let path = row[0].as_str().expect("路徑");
                if row[1] != "file" || !path.ends_with(".json") || !path.contains("/projects/") {
                    continue;
                }
                let content = match &row[2] {
                    Value::String(s) => s.clone(),
                    Value::Object(o) if o.contains_key("parts") => parts(&o["parts"]),
                    _ => continue,
                };
                let Ok(data) = parse_project_json(content.as_bytes()) else {
                    continue;
                };
                let Ok(p) = validate(&data) else {
                    continue;
                };
                if seen.insert(p.dump_bytes().expect("寫出")) {
                    out.push((format!("{unit} {id} {path}"), p));
                }
            }
        }
    }
    (out, accepted)
}

fn project_file(root: &Path, name: &str) -> PathBuf {
    root.join("projects").join(name).join("project.json")
}

fn dump_of(out: &PyOut) -> Option<String> {
    match out {
        PyOut::Value(v) => v["dump"].as_str().map(str::to_string),
        _ => None,
    }
}

#[test]
fn rust_and_python_round_trip_each_other() {
    let _g = ProjectsGuard::new();
    let (corpus, accepted) = corpus();
    assert!(corpus.len() >= accepted, "語料不少於 validate_text 的接受案例數");
    let base = TempTree::new("project-t6-roundtrip-");
    let clock = TestClock::at(BASE_MS);

    // Rust → Python
    let mut reqs = Vec::new();
    let mut rust_files = Vec::new();
    for (i, (_, p)) in corpus.iter().enumerate() {
        let root = base.join(&format!("r{i}"));
        std::fs::create_dir_all(&root).expect("建立根目錄");
        if is_windows_reserved(&p.name) {
            std::fs::create_dir_all(root.join("projects").join(&p.name)).expect("既有的保留名稱專案");
        }
        write::save(&root, p, &clock).expect("Rust 存檔");
        rust_files.push(std::fs::read(project_file(&root, &p.name)).expect("讀取"));
        reqs.push((root, p.name.clone()));
    }
    let calls: Vec<(&Path, &str, Vec<Value>)> =
        reqs.iter().map(|(r, n)| (r.as_path(), "load", vec![json!(n)])).collect();
    let got = call_each(&calls);
    let mut r2p = 0;
    for (((what, _), bytes), out) in corpus.iter().zip(&rust_files).zip(&got) {
        assert_eq!(dump_of(out).map(String::into_bytes).as_ref(), Some(bytes), "Rust → Python:{what}");
        r2p += 1;
    }

    // Python → Rust(輸入為 Rust 寫出的同一份內容)
    let mut roots = Vec::new();
    for i in 0..corpus.len() {
        let root = base.join(&format!("p{i}"));
        std::fs::create_dir_all(&root).expect("建立根目錄");
        roots.push(root);
    }
    let texts: Vec<String> = rust_files
        .iter()
        .map(|b| String::from_utf8(b.clone()).expect("UTF-8"))
        .collect();
    let calls: Vec<(&Path, &str, Vec<Value>)> = roots
        .iter()
        .zip(&texts)
        .map(|(r, t)| (r.as_path(), "@save_text", vec![json!(t)]))
        .collect();
    for (out, (what, _)) in call_each(&calls).iter().zip(&corpus) {
        assert_eq!(out, &PyOut::Value(Value::Null), "Python 存檔:{what}");
    }
    let mut p2r = 0;
    for ((root, (what, p)), rust_bytes) in roots.iter().zip(&corpus).zip(&rust_files) {
        let py_bytes = std::fs::read(project_file(root, &p.name)).expect("讀取 Python 寫出的檔案");
        let loaded = read::load(root, &p.name).expect("Rust 載入 Python 寫出的檔案");
        assert_eq!(loaded.dump_bytes().expect("寫出"), py_bytes, "Python → Rust:{what}");
        assert_eq!(&py_bytes, rust_bytes, "兩版寫出的位元組相同:{what}");
        p2r += 1;
    }
    println!(
        "往返:語料 {} 筆(validate_text 接受案例 {accepted} 筆、save_sequence / create 的 Python 寫出檔 {} 筆)",
        corpus.len(),
        corpus.len() - accepted
    );
    println!("Rust → Python:語料 {} 筆、一致 {r2p} 筆", corpus.len());
    println!("Python → Rust:語料 {} 筆、一致 {p2r} 筆", corpus.len());
    assert_eq!((r2p, p2r), (corpus.len(), corpus.len()));
}

/// 連續存 3 次後兩邊的 `list_history` / `load_version` 相同,版本檔逐位元組等於該版本的 dump(Rust 寫、Python 讀,
/// 以及 Python 寫、Rust 讀)。
#[test]
fn history_written_by_one_side_reads_the_same_on_the_other() {
    let _g = ProjectsGuard::new();
    let (corpus, _) = corpus();
    let base = TempTree::new("project-t6-roundtrip-h-");
    let clock = TestClock::at(BASE_MS);
    let picked: Vec<&(String, Project)> = corpus
        .iter()
        .filter(|(_, p)| !is_windows_reserved(&p.name))
        .take(12)
        .collect();
    assert!(picked.len() >= 10);
    let versions = |p: &Project| -> Vec<Project> {
        (0..3)
            .map(|k| {
                let mut q = p.clone();
                q.script = format!("{}(第 {k} 版)", q.script.chars().take(100).collect::<String>());
                q
            })
            .collect()
    };
    let (mut rust_side, mut python_side) = (0, 0);
    for (i, (what, p)) in picked.iter().enumerate() {
        let vs = versions(p);
        // Rust 寫 → Python 讀
        let root = base.join(&format!("r{i}"));
        std::fs::create_dir_all(&root).expect("建立");
        let mut vids = Vec::new();
        for v in &vs {
            if let Some(id) = write::save(&root, v, &clock).expect("Rust 存檔").version {
                vids.push(id);
            }
        }
        assert_eq!(read::list_history(&root, &p.name).expect("列出"), vids);
        let mut calls: Vec<(&Path, &str, Vec<Value>)> =
            vec![(root.as_path(), "list_history", vec![json!(p.name)])];
        calls.extend(vids.iter().map(|v| (root.as_path(), "load_version", vec![json!(p.name), json!(v)])));
        let got = call_each(&calls);
        assert_eq!(got[0], PyOut::Value(json!(vids)), "{what}");
        for (k, v) in vids.iter().enumerate() {
            let file = std::fs::read(root.join("projects").join(&p.name).join("history").join(format!("{v}.json")))
                .expect("讀取版本檔");
            assert_eq!(file, vs[k].dump_bytes().expect("寫出"), "{what} {v}");
            assert_eq!(dump_of(&got[k + 1]).map(String::into_bytes), Some(file), "{what} {v}");
        }
        rust_side += 1;

        // Python 寫 → Rust 讀
        let root = base.join(&format!("p{i}"));
        std::fs::create_dir_all(&root).expect("建立");
        let texts: Vec<String> = vs
            .iter()
            .map(|v| String::from_utf8(v.dump_bytes().expect("寫出")).expect("UTF-8"))
            .collect();
        let mut calls: Vec<(&Path, &str, Vec<Value>)> =
            texts.iter().map(|t| (root.as_path(), "@save_text", vec![json!(t)])).collect();
        calls.push((root.as_path(), "list_history", vec![json!(p.name)]));
        let got = call_each(&calls);
        let py_vids: Vec<String> = got[..3]
            .iter()
            .filter_map(|o| match o {
                PyOut::Value(Value::String(s)) => Some(s.clone()),
                PyOut::Value(Value::Null) => None,
                other => panic!("Python 存檔失敗:{other:?}"),
            })
            .collect();
        assert_eq!(got[3], PyOut::Value(json!(py_vids)), "{what}");
        assert_eq!(read::list_history(&root, &p.name).expect("列出"), py_vids, "{what}");
        for (k, v) in py_vids.iter().enumerate() {
            let file = std::fs::read(root.join("projects").join(&p.name).join("history").join(format!("{v}.json")))
                .expect("讀取版本檔");
            let loaded = read::load_version(&root, &p.name, v).expect("Rust 載入 Python 的版本");
            assert_eq!(loaded.dump_bytes().expect("寫出"), file, "{what} {v}");
            assert_eq!(file, texts[k].as_bytes(), "{what} {v}");
        }
        python_side += 1;
    }
    println!("history 往返:Rust 寫 → Python 讀 {rust_side} 個專案、Python 寫 → Rust 讀 {python_side} 個專案,各 3 次存檔");
}
