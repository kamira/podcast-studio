//! 測試共用的 Python 子程序(CHG-20261128-01 T3 起;只在 macOS / Linux 以 `#[cfg(unix)] mod common;` 引入——
//! `project.py` 頂層 import `fcntl`,Windows 無法執行)。
//!
//! `[<python>, "-I", "-B", "-X", "utf8", "-c", <腳本>, <repo>]`(Global Constraints):直譯器取 `PARITY_PYTHON`,沒有時
//! `python3`;repo 根目錄附加在 `sys.path` 末端,匯入後核對 `project`、`names`、`safe_fs`、`value_checks`、
//! `breezy_worker` 的 `__file__` 來自 repo 根目錄;`project.ROOT` 改指 Python 自己以 `tempfile.mkdtemp` 建立的
//! 暫存目錄(先 `realpath`),結束時刪除。每行請求 `{"fn": 函式名, "args": [參數…]}` 呼叫 `project.<函式>(*參數)`。
//! Python 無法啟動時判失敗,不略過;呼叫前後確認 repo 工作副本的 `projects/` 狀態不變。

use std::ffi::OsString;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use serde_json::{Value, json};

const SCRIPT: &str = r#"
import json, os, pathlib, shutil, sys, tempfile
repo = sys.argv[1]
sys.path.append(repo)
import project as pj, names, safe_fs, value_checks, breezy_worker
for m in (pj, names, safe_fs, value_checks, breezy_worker):
    assert os.path.dirname(os.path.realpath(m.__file__)) == repo, m.__file__
work = os.path.realpath(tempfile.mkdtemp(prefix="project-t3-py-"))
pj.ROOT = pathlib.Path(work)
sys.stdout.reconfigure(encoding="utf-8", newline="\n")
try:
    for line in sys.stdin.read().split("\n"):
        if not line:
            continue
        req = json.loads(line)
        try:
            v = getattr(pj, req["fn"])(*req["args"])
            r = {"value": sorted(v) if isinstance(v, set) else v}
        except pj.ProjectError as e:
            r = {"errors": list(e.errors)}
        except Exception as e:
            r = {"exception": type(e).__name__}
        sys.stdout.write(json.dumps(r, ensure_ascii=False) + "\n")
finally:
    shutil.rmtree(work)
"#;

/// Python 端一次呼叫的結果。
#[derive(Debug, Clone, PartialEq)]
pub enum PyOut {
    Value(Value),
    Errors(Vec<String>),
    Exception(String),
}

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

/// 依序呼叫 `project.<函式>(*參數)`,回傳每一筆的結果(`set` 轉成排序後的陣列)。
pub fn call(requests: &[(&str, Vec<Value>)]) -> Vec<PyOut> {
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
    let input: String = requests
        .iter()
        .map(|(f, args)| json!({"fn": f, "args": args}).to_string() + "\n")
        .collect();
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
    let got: Vec<PyOut> = text
        .split('\n')
        .filter(|l| !l.is_empty())
        .map(|l| {
            let v: Value = serde_json::from_str(l).expect("Python 輸出");
            if let Some(x) = v.get("value") {
                PyOut::Value(x.clone())
            } else if let Some(e) = v.get("errors") {
                PyOut::Errors(
                    e.as_array()
                        .expect("陣列")
                        .iter()
                        .map(|m| m.as_str().expect("字串").to_string())
                        .collect(),
                )
            } else {
                PyOut::Exception(v["exception"].as_str().expect("字串").to_string())
            }
        })
        .collect();
    assert_eq!(got.len(), requests.len());
    got
}
