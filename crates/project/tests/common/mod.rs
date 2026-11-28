//! 測試共用程式(CHG-20261128-01 T3 起;T4 擴充)。各測試檔只用到其中一部分,所以整個模組 `allow(dead_code)`
//! (各檔以 `mod common;` 或 `#[cfg(unix)] mod common;` 引入;每個測試檔各自編譯一份)。
//!
//! **Python 子程序**([`call`]、[`call_in`];只在 macOS / Linux 使用——`project.py` 頂層 import `fcntl`,Windows 無法
//! 執行):`[<python>, "-I", "-B", "-X", "utf8", "-c", <腳本>, <repo>]`(Global Constraints):直譯器取 `PARITY_PYTHON`,
//! 沒有時 `python3`;repo 根目錄附加在 `sys.path` 末端,匯入後核對 `project`、`names`、`safe_fs`、`value_checks`、
//! `breezy_worker` 的 `__file__` 來自 repo 根目錄;`project.ROOT` 預設改指 Python 自己以 `tempfile.mkdtemp` 建立的
//! 暫存目錄(先 `realpath`),結束時刪除;請求帶 `root` 時(T4,[`call_in`])改指該目錄(測試自己建立的暫存樹)。
//! 每行請求 `{"fn": 函式名, "args": [參數…], "root"?}` 呼叫 `project.<函式>(*參數)`;回傳 `Project` 時轉成
//! `{"dump": _dump(validate(to_dict()))}`。Python 無法啟動時判失敗,不略過;呼叫前後確認 repo 工作副本的 `projects/`
//! 狀態不變。
//!
//! **暫存樹**([`TempTree`];T4,三平台):在系統暫存目錄排他建立唯一名稱的目錄(先 `canonicalize`;Windows 為
//! `\\?\` 形式,才建得出 `CON` 等保留裝置名的目錄),`Drop` 時刪除;[`TempTree::snapshot`] 為不跟隨連結的快照
//! (型別、修改時間、內容或連結目標),比對讀取 API 呼叫前後是否完全相同。[`ProjectsGuard`] 在建立時與 `Drop` 時
//! 確認 repo 工作副本的 `projects/` 狀態不變(Global Constraints)。
#![allow(dead_code)]

use std::collections::BTreeMap;
use std::ffi::OsString;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{Value, json};

const SCRIPT: &str = r#"
import json, os, pathlib, shutil, sys, tempfile
repo = sys.argv[1]
sys.path.append(repo)
import project as pj, names, safe_fs, value_checks, breezy_worker
for m in (pj, names, safe_fs, value_checks, breezy_worker):
    assert os.path.dirname(os.path.realpath(m.__file__)) == repo, m.__file__
work = os.path.realpath(tempfile.mkdtemp(prefix="project-t3-py-"))
default_root = pathlib.Path(work)
sys.stdout.reconfigure(encoding="utf-8", newline="\n")
try:
    for line in sys.stdin.read().split("\n"):
        if not line:
            continue
        req = json.loads(line)
        pj.ROOT = pathlib.Path(os.path.realpath(req["root"])) if "root" in req else default_root
        try:
            v = getattr(pj, req["fn"])(*req["args"])
            if isinstance(v, pj.Project):
                v = {"dump": pj._dump(pj.validate(v.to_dict())).decode("utf-8")}
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

/// CI 上執行(`GITHUB_ACTIONS == "true"`):略過路徑一律判失敗。
pub fn in_ci() -> bool {
    std::env::var_os("GITHUB_ACTIONS").is_some_and(|v| v == "true")
}

pub fn python() -> OsString {
    std::env::var_os("PARITY_PYTHON")
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| OsString::from("python3"))
}

pub fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .expect("無法取得 repo 根目錄")
}

/// repo 工作副本 `projects/` 的狀態(不存在為 `None`;存在時為項目名稱與 mtime 的清單)。
pub fn projects_state(repo: &Path) -> Option<Vec<(OsString, SystemTime)>> {
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

/// 建立時記下 repo 工作副本 `projects/` 的狀態,`Drop` 時(測試結束)確認不變。
pub struct ProjectsGuard {
    repo: PathBuf,
    before: Option<Vec<(OsString, SystemTime)>>,
}

impl ProjectsGuard {
    pub fn new() -> Self {
        let repo = repo_root();
        let before = projects_state(&repo);
        Self { repo, before }
    }
}

impl Drop for ProjectsGuard {
    fn drop(&mut self) {
        if !std::thread::panicking() {
            assert_eq!(
                projects_state(&self.repo),
                self.before,
                "repo 的 projects/ 不得改變"
            );
        }
    }
}

fn run_python(requests: &[Value]) -> Vec<PyOut> {
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
    let input: String = requests.iter().map(|r| r.to_string() + "\n").collect();
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

/// 依序呼叫 `project.<函式>(*參數)`,回傳每一筆的結果(`set` 轉成排序後的陣列)。
pub fn call(requests: &[(&str, Vec<Value>)]) -> Vec<PyOut> {
    let reqs: Vec<Value> = requests
        .iter()
        .map(|(f, args)| json!({"fn": f, "args": args}))
        .collect();
    run_python(&reqs)
}

/// 同 [`call`],但 `project.ROOT` 指向 `root`(測試自己建立的暫存樹;T4)。
pub fn call_in(root: &Path, requests: &[(&str, Vec<Value>)]) -> Vec<PyOut> {
    let root = root.to_str().expect("暫存目錄須為 UTF-8 路徑");
    let reqs: Vec<Value> = requests
        .iter()
        .map(|(f, args)| json!({"fn": f, "args": args, "root": root}))
        .collect();
    run_python(&reqs)
}

static TREE_COUNTER: AtomicU32 = AtomicU32::new(0);

/// 快照中一個項目的狀態(型別、修改時間(奈秒)、內容或連結目標)。
pub type Entry = (&'static str, i128, Option<Vec<u8>>);

/// 測試自己排他建立的暫存目錄;`Drop` 時刪除(`remove_dir_all` 不跟隨連結)。
pub struct TempTree {
    pub path: PathBuf,
}

impl TempTree {
    pub fn new(prefix: &str) -> Self {
        let base = std::env::temp_dir();
        loop {
            let nanos = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.subsec_nanos())
                .unwrap_or(0);
            let p = base.join(format!(
                "{prefix}{}-{}-{nanos}",
                std::process::id(),
                TREE_COUNTER.fetch_add(1, Ordering::Relaxed)
            ));
            match std::fs::create_dir(&p) {
                Ok(()) => {
                    let mut t = TempTree { path: p };
                    t.path = std::fs::canonicalize(&t.path).expect("canonicalize");
                    return t;
                }
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(e) => panic!("無法建立暫存目錄:{e}"),
            }
        }
    }

    pub fn join(&self, rel: &str) -> PathBuf {
        rel.split('/').fold(self.path.clone(), |p, s| p.join(s))
    }

    pub fn dir(&self, rel: &str) {
        std::fs::create_dir_all(self.join(rel)).expect("建立目錄");
    }

    /// 寫入一般檔(上層目錄自動建立),修改時間設為 `mtime_ms`(UNIX 紀元起毫秒)。
    pub fn file_at(&self, rel: &str, content: &[u8], mtime_ms: u64) {
        let p = self.join(rel);
        if let Some(parent) = p.parent() {
            std::fs::create_dir_all(parent).expect("建立上層目錄");
        }
        std::fs::write(&p, content).expect("寫入檔案");
        std::fs::OpenOptions::new()
            .write(true)
            .open(&p)
            .and_then(|f| f.set_modified(UNIX_EPOCH + Duration::from_millis(mtime_ms)))
            .expect("設定修改時間");
    }

    /// 寫入一般檔,修改時間為 2026-01-01T00:00:00Z。
    pub fn file(&self, rel: &str, content: &[u8]) {
        self.file_at(rel, content, 1_767_225_600_000);
    }

    /// 建立符號連結;Windows 無權限時回 `false`(呼叫端先 `assert!(!in_ci())` 再略過)。
    pub fn symlink(&self, rel: &str, target: &Path, dir: bool) -> bool {
        let p = self.join(rel);
        if let Some(parent) = p.parent() {
            std::fs::create_dir_all(parent).expect("建立上層目錄");
        }
        match safe_fs::support::symlink(target, &p, dir) {
            Ok(()) => true,
            Err(e) if safe_fs::support::is_symlink_privilege_error(&e) => false,
            Err(e) => panic!("無法建立符號連結:{e}"),
        }
    }

    /// 不跟隨連結的快照:相對路徑(以 `/` 分隔)→ 狀態。
    pub fn snapshot(&self) -> BTreeMap<String, Entry> {
        let mut out = BTreeMap::new();
        let mut stack = vec![(self.path.clone(), String::new())];
        while let Some((dir, rel)) = stack.pop() {
            for entry in std::fs::read_dir(&dir).expect("read_dir") {
                let entry = entry.expect("目錄項");
                let name = entry.file_name().to_string_lossy().to_string();
                let r = if rel.is_empty() {
                    name
                } else {
                    format!("{rel}/{name}")
                };
                let path = entry.path();
                let meta = std::fs::symlink_metadata(&path).expect("symlink_metadata");
                let mtime = meta
                    .modified()
                    .map(project::read::mtime_ns)
                    .unwrap_or_default();
                let state: Entry = if meta.file_type().is_symlink() {
                    let t = std::fs::read_link(&path).expect("read_link");
                    (
                        "symlink",
                        mtime,
                        Some(t.to_string_lossy().as_bytes().to_vec()),
                    )
                } else if meta.is_dir() {
                    stack.push((path, r.clone()));
                    ("dir", mtime, None)
                } else if meta.is_file() {
                    ("file", mtime, Some(std::fs::read(&path).expect("讀取")))
                } else {
                    ("other", mtime, None)
                };
                out.insert(r, state);
            }
        }
        out
    }
}

impl Drop for TempTree {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}
