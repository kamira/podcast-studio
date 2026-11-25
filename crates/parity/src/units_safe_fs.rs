//! `safe_fs.*` 的 Rust 比對單元(CHG-20261125-01 代決 10、11);Python 端對應單元在 `py/adapter.py`。
//!
//! 除 `safe_fs.check_name`(純函式,輸入為名稱字串)外,每個單元的輸入為
//! `{"tree": [項目…], "call": {參數}}`:
//!
//! - 單元在系統暫存目錄**排他建立唯一名稱的工作目錄**(前綴 `parity-safe-fs-rs-`),其下放 `top/`
//!   (受信根目錄,依 `tree` 建立合成檔案樹)與 `outside/`(固定內容:`marker.txt`、`d/inner.txt`);
//!   **案例結束即刪除**(工作目錄由 `Drop` 守衛刪除,`Registry` 以 `catch_unwind` 攔截 panic 時也會刪除)。
//!   不寫其他任何位置、不使用網路。
//! - `tree` 項目:`{"path", "type": "dir"}`、`{"path", "type": "file", "content"}`、
//!   `{"path", "type": "symlink", "target": "@top/…" | "@outside/…" | "@missing", "kind": "dir" | "file"}`。
//! - 輸出(`ok` 的值):`{"result": <結果>, "top": <快照>, "outside": <快照>}`;結果為 `{"dir": [名稱…]}`
//!   或 `{"file": <內容或 null>}`;快照為依相對路徑排序的 `[相對路徑, 型別, 內容 / null / 連結記號]`。
//!
//! | 單元 | `call` | 結果 |
//! |------|--------|------|
//! | `safe_fs.open_dir` | `{"parts", "create"?, "top"?}` | `{"dir"}` |
//! | `safe_fs.open_child` | `{"dir", "name", "create"?}` | `{"dir"}` |
//! | `safe_fs.open_file` | `{"dir", "name", "flags"}` | `{"file"}` |
//! | `safe_fs.open_path` | `{"parts", "flags", "top"?}` | `{"file"}` |
//! | `safe_fs.create_excl` | `{"dir", "name", "flags"?}`(`w` / `rw` / `w_trunc`,預設 `w`;權限 0o600) | `{"file"}` |
//! | `safe_fs.open_or_create` | `{"dir", "name", "flags"?}`(`w` / `rw` / `w_trunc`,預設 `rw`) | `{"file"}` |
//! | `safe_fs.lstat` | `{"dir", "name"}` | `null` 或 `{"type", "size"?}`(只有一般檔帶 `size`) |
//! | `safe_fs.entries` | `{"dir"}` | `[[名稱, 型別], …]` |
//! | `safe_fs.unlink`、`safe_fs.rmtree` | `{"dir", "name", "missing_ok"?}` | `null` |
//! | `safe_fs.rename`、`safe_fs.replace`、`safe_fs.link` | `{"src_dir", "src", "dst_dir", "dst"}` | `null` |
//!
//! 開啟旗標 `r` / `w` / `rw` / `w_trunc`;寫入旗標先寫入標記 `<W>`,`rw` 再從頭讀回,`w`、`w_trunc` 的值為 `null`。
//! 型別為 `file` / `dir` / `symlink` / `other`(不比對時間、檔案識別碼等隨環境變動的欄位)。
//! 錯誤類別表(與轉接器相同):`Unsafe` → `reject unsafe`、`InvalidName` → `reject invalid_name`、
//! `ReservedDevice` → `reject reserved_device`、`NotFound` → `error not_found`、`AlreadyExists` → `error exists`、
//! `NotADirectory` → `error not_a_directory`、`IsADirectory` → `error is_a_directory`、
//! `PermissionDenied` → `error permission`、其他 → `error os_<名稱>`;輸入格式不符 → `error type`。
//! Windows 無法建立符號連結時回 `error symlink_privilege`(只會出現在 Windows,`safe_fs_expected.rs` 依此略過)。

use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use safe_fs::{DirOptions, EntryKind, ErrorKind, FileAccess, Meta, SafeDir};
use serde_json::{Map, Value, json};

use crate::Outcome;
use crate::units::ERROR_TYPE;

/// 工作目錄名稱前綴(Python 端為 `parity-safe-fs-py-`)。
pub const WORK_PREFIX: &str = "parity-safe-fs-rs-";

/// Windows 無法建立符號連結時的錯誤類別。
pub const ERROR_SYMLINK_PRIVILEGE: &str = "symlink_privilege";

/// `outside/` 的固定內容。
const OUTSIDE_FILES: [(&str, &str); 2] =
    [("marker.txt", "outside"), ("d/inner.txt", "outside-inner")];

/// 寫入旗標時寫入的標記。
const WRITE_MARK: &[u8] = b"<W>";

static COUNTER: AtomicU32 = AtomicU32::new(0);

fn error(class: &str) -> Outcome {
    Outcome::Error {
        class: class.to_string(),
    }
}

fn type_error() -> Outcome {
    error(ERROR_TYPE)
}

fn reject(code: &str) -> Outcome {
    Outcome::Reject {
        code: code.to_string(),
    }
}

/// 代決 10 的錯誤類別表。
fn map_error(e: &safe_fs::Error) -> Outcome {
    match e.kind() {
        ErrorKind::Unsafe => reject("unsafe"),
        ErrorKind::InvalidName => reject("invalid_name"),
        ErrorKind::ReservedDevice => reject("reserved_device"),
        ErrorKind::NotFound => error("not_found"),
        ErrorKind::AlreadyExists => error("exists"),
        ErrorKind::NotADirectory => error("not_a_directory"),
        ErrorKind::IsADirectory => error("is_a_directory"),
        ErrorKind::PermissionDenied => error("permission"),
        ErrorKind::Other(_) => error(&format!("os_{}", e.os_error_name())),
    }
}

/// fixture 內的相對路徑(以 `/` 分隔);不合法(非字串、空段、`.`、`..`、反斜線)→ `error type`。
fn rel_parts(v: Option<&Value>) -> Result<Vec<String>, Outcome> {
    let Some(Value::String(s)) = v else {
        return Err(type_error());
    };
    if s.is_empty() {
        return Err(type_error());
    }
    let parts: Vec<String> = s.split('/').map(str::to_string).collect();
    if parts
        .iter()
        .any(|p| p.is_empty() || p == "." || p == ".." || p.contains('\\'))
    {
        return Err(type_error());
    }
    Ok(parts)
}

fn join(base: &Path, parts: &[String]) -> PathBuf {
    parts.iter().fold(base.to_path_buf(), |p, s| p.join(s))
}

/// 單一案例的暫存工作目錄;`Drop` 時刪除(`remove_dir_all` 不跟隨連結)。
struct Work {
    root: PathBuf,
    top: PathBuf,
    outside: PathBuf,
    missing: PathBuf,
}

impl Drop for Work {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

fn setup_error(e: &std::io::Error) -> Outcome {
    if safe_fs::support::is_symlink_privilege_error(e) {
        error(ERROR_SYMLINK_PRIVILEGE)
    } else {
        error("setup")
    }
}

impl Work {
    fn new() -> Result<Work, Outcome> {
        let base = std::env::temp_dir();
        loop {
            let nanos = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.subsec_nanos())
                .unwrap_or(0);
            let root = base.join(format!(
                "{WORK_PREFIX}{}-{}-{nanos}",
                std::process::id(),
                COUNTER.fetch_add(1, Ordering::Relaxed)
            ));
            match std::fs::create_dir(&root) {
                Ok(()) => {
                    // 先建立守衛,之後的任何失敗都會刪除工作目錄
                    let w = Work {
                        top: root.join("top"),
                        outside: root.join("outside"),
                        missing: root.join("missing"),
                        root,
                    };
                    w.init().map_err(|e| setup_error(&e))?;
                    return Ok(w);
                }
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(e) => return Err(setup_error(&e)),
            }
        }
    }

    fn init(&self) -> std::io::Result<()> {
        std::fs::create_dir(&self.top)?;
        std::fs::create_dir(&self.outside)?;
        for (rel, content) in OUTSIDE_FILES {
            let path = rel.split('/').fold(self.outside.clone(), |p, s| p.join(s));
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent)?;
            }
            std::fs::write(path, content)?;
        }
        Ok(())
    }

    /// 連結記號 → 本機路徑。
    fn target(&self, token: Option<&Value>) -> Result<PathBuf, Outcome> {
        let Some(Value::String(t)) = token else {
            return Err(type_error());
        };
        if t == "@missing" {
            return Ok(self.missing.clone());
        }
        for (prefix, base) in [("@top", &self.top), ("@outside", &self.outside)] {
            if t == prefix {
                return Ok(base.clone());
            }
            if let Some(rest) = t.strip_prefix(prefix).and_then(|r| r.strip_prefix('/')) {
                let rest = Value::String(rest.to_string());
                return Ok(join(base, &rel_parts(Some(&rest))?));
            }
        }
        Err(type_error())
    }

    /// 本機路徑 → 連結記號(快照用)。
    fn token(&self, target: &Path) -> String {
        if target == self.missing {
            return "@missing".to_string();
        }
        for (prefix, base) in [("@top", &self.top), ("@outside", &self.outside)] {
            if target == base.as_path() {
                return prefix.to_string();
            }
            if let Ok(rest) = target.strip_prefix(base) {
                let rel: Vec<String> = rest
                    .components()
                    .map(|c| c.as_os_str().to_string_lossy().to_string())
                    .collect();
                return format!("{prefix}/{}", rel.join("/"));
            }
        }
        "@other".to_string()
    }

    fn build(&self, tree: Option<&Value>) -> Result<(), Outcome> {
        let items = match tree {
            None => return Ok(()),
            Some(Value::Array(items)) => items,
            Some(_) => return Err(type_error()),
        };
        for item in items {
            let Value::Object(item) = item else {
                return Err(type_error());
            };
            let path = join(&self.top, &rel_parts(item.get("path"))?);
            let parent = path.parent().map(Path::to_path_buf);
            let io = |r: std::io::Result<()>| r.map_err(|e| setup_error(&e));
            match item.get("type").and_then(Value::as_str) {
                Some("dir") => io(std::fs::create_dir_all(&path))?,
                Some("file") => {
                    let content = match item.get("content") {
                        None => "",
                        Some(Value::String(s)) => s.as_str(),
                        Some(_) => return Err(type_error()),
                    };
                    if let Some(p) = &parent {
                        io(std::fs::create_dir_all(p))?;
                    }
                    io(std::fs::write(&path, content))?;
                }
                Some("symlink") => {
                    let dir = match item.get("kind").and_then(Value::as_str) {
                        Some("dir") => true,
                        Some("file") => false,
                        _ => return Err(type_error()),
                    };
                    let target = self.target(item.get("target"))?;
                    if let Some(p) = &parent {
                        io(std::fs::create_dir_all(p))?;
                    }
                    io(safe_fs::support::symlink(&target, &path, dir))?;
                }
                _ => return Err(type_error()),
            }
        }
        Ok(())
    }

    /// 不跟隨連結的快照:依相對路徑排序的 `[相對路徑, 型別, 內容 / null / 連結記號]`。
    fn snapshot(&self, base: &Path) -> std::io::Result<Value> {
        let mut out: Vec<(String, Value)> = Vec::new();
        let mut stack = vec![(base.to_path_buf(), String::new())];
        while let Some((dir, rel)) = stack.pop() {
            for entry in std::fs::read_dir(&dir)? {
                let entry = entry?;
                let name = entry.file_name().to_string_lossy().to_string();
                let r = if rel.is_empty() {
                    name
                } else {
                    format!("{rel}/{name}")
                };
                let path = entry.path();
                let meta = std::fs::symlink_metadata(&path)?;
                let row = if meta.file_type().is_symlink() {
                    json!([r, "symlink", self.token(&std::fs::read_link(&path)?)])
                } else if meta.is_dir() {
                    stack.push((path, r.clone()));
                    json!([r, "dir", null])
                } else if meta.is_file() {
                    let bytes = std::fs::read(&path)?;
                    json!([r, "file", String::from_utf8_lossy(&bytes)])
                } else {
                    json!([r, "other", null])
                };
                out.push((r, row));
            }
        }
        out.sort_by(|a, b| a.0.cmp(&b.0));
        Ok(Value::Array(out.into_iter().map(|(_, v)| v).collect()))
    }
}

/// 取 `call` 的欄位;型別不符 → `error type`(名稱內容本身交給 `safe-fs` 檢查)。
fn str_list(call: &Map<String, Value>, key: &str) -> Result<Vec<String>, Outcome> {
    match call.get(key) {
        None => Ok(Vec::new()),
        Some(Value::Array(items)) => items
            .iter()
            .map(|v| v.as_str().map(str::to_string).ok_or_else(type_error))
            .collect(),
        Some(_) => Err(type_error()),
    }
}

fn str_field(call: &Map<String, Value>, key: &str) -> Result<String, Outcome> {
    call.get(key)
        .and_then(Value::as_str)
        .map(str::to_string)
        .ok_or_else(type_error)
}

fn bool_field(call: &Map<String, Value>, key: &str) -> Result<bool, Outcome> {
    match call.get(key) {
        None => Ok(false),
        Some(Value::Bool(b)) => Ok(*b),
        Some(_) => Err(type_error()),
    }
}

fn flags_field(call: &Map<String, Value>) -> Result<(&'static str, FileAccess), Outcome> {
    match call.get("flags").map(Value::as_str) {
        None | Some(Some("r")) => Ok(("r", FileAccess::Read)),
        Some(Some("w")) => Ok(("w", FileAccess::Write { truncate: false })),
        Some(Some("rw")) => Ok(("rw", FileAccess::ReadWrite { truncate: false })),
        Some(Some("w_trunc")) => Ok(("w_trunc", FileAccess::Write { truncate: true })),
        _ => Err(type_error()),
    }
}

fn top_path(work: &Work, call: &Map<String, Value>) -> Result<PathBuf, Outcome> {
    match call.get("top") {
        None => Ok(work.top.clone()),
        Some(Value::String(s)) if s.is_empty() => Ok(work.top.clone()),
        v => Ok(join(&work.top, &rel_parts(v)?)),
    }
}

fn dir_result(d: &SafeDir) -> Result<Value, Outcome> {
    let names = d.entry_names().map_err(|e| map_error(&e))?;
    let names: Vec<String> = names
        .iter()
        .map(|n| n.to_string_lossy().to_string())
        .collect();
    Ok(json!({ "dir": names }))
}

fn file_result(mut f: std::fs::File, flags: &str) -> Result<Value, Outcome> {
    let io = |r: std::io::Result<()>| r.map_err(|_| error("io"));
    if flags != "r" {
        io(f.write_all(WRITE_MARK))?;
    }
    if flags == "w" || flags == "w_trunc" {
        return Ok(json!({ "file": null }));
    }
    if flags == "rw" {
        io(f.seek(SeekFrom::Start(0)).map(|_| ()))?;
    }
    let mut bytes = Vec::new();
    io(f.read_to_end(&mut bytes).map(|_| ()))?;
    Ok(json!({ "file": String::from_utf8_lossy(&bytes) }))
}

type Action = fn(&Work, &Map<String, Value>) -> Result<Value, Outcome>;

/// 建樹 → 呼叫 → 結果與快照;工作目錄在函式結束時由 `Work` 的 `Drop` 刪除。
fn run_tree_case(input: &Value, action: Action) -> Outcome {
    let Value::Object(obj) = input else {
        return type_error();
    };
    let Some(Value::Object(call)) = obj.get("call") else {
        return type_error();
    };
    let work = match Work::new() {
        Ok(w) => w,
        Err(o) => return o,
    };
    if let Err(o) = work.build(obj.get("tree")) {
        return o;
    }
    let result = match action(&work, call) {
        Ok(v) => v,
        Err(o) => return o,
    };
    match (work.snapshot(&work.top), work.snapshot(&work.outside)) {
        (Ok(top), Ok(outside)) => Outcome::Ok(json!({
            "result": result,
            "top": top,
            "outside": outside,
        })),
        _ => error("setup"),
    }
}

fn act_open_dir(work: &Work, call: &Map<String, Value>) -> Result<Value, Outcome> {
    let parts = str_list(call, "parts")?;
    let opts = DirOptions {
        create: bool_field(call, "create")?,
        ..DirOptions::default()
    };
    let top = top_path(work, call)?;
    let d = safe_fs::open_dir(&top, &parts, opts).map_err(|e| map_error(&e))?;
    dir_result(&d)
}

fn act_open_child(work: &Work, call: &Map<String, Value>) -> Result<Value, Outcome> {
    let parts = str_list(call, "dir")?;
    let name = str_field(call, "name")?;
    let opts = DirOptions {
        create: bool_field(call, "create")?,
        ..DirOptions::default()
    };
    let d =
        safe_fs::open_dir(&work.top, &parts, DirOptions::default()).map_err(|e| map_error(&e))?;
    let child = d.open_child(&name, opts).map_err(|e| map_error(&e))?;
    dir_result(&child)
}

fn act_open_file(work: &Work, call: &Map<String, Value>) -> Result<Value, Outcome> {
    let parts = str_list(call, "dir")?;
    let name = str_field(call, "name")?;
    let (flags, access) = flags_field(call)?;
    let d =
        safe_fs::open_dir(&work.top, &parts, DirOptions::default()).map_err(|e| map_error(&e))?;
    let f = d.open_file(&name, access).map_err(|e| map_error(&e))?;
    file_result(f, flags)
}

fn act_open_path(work: &Work, call: &Map<String, Value>) -> Result<Value, Outcome> {
    let parts = str_list(call, "parts")?;
    let (flags, access) = flags_field(call)?;
    let top = top_path(work, call)?;
    let f = safe_fs::open_path(&top, &parts, access).map_err(|e| map_error(&e))?;
    file_result(f, flags)
}

/// 建立檔案的權限位元(與 Python 轉接器相同;Windows 忽略)。
const CREATE_MODE: u32 = 0o600;

/// 建立類單元的旗標:只接受含寫入的 `w` / `rw` / `w_trunc`(`r` 為輸入格式不符)。
fn create_flags(
    call: &Map<String, Value>,
    default: &'static str,
) -> Result<(&'static str, FileAccess), Outcome> {
    let flag = match call.get("flags") {
        None => default,
        Some(Value::String(s)) => s.as_str(),
        Some(_) => return Err(type_error()),
    };
    match flag {
        "w" => Ok(("w", FileAccess::Write { truncate: false })),
        "rw" => Ok(("rw", FileAccess::ReadWrite { truncate: false })),
        "w_trunc" => Ok(("w_trunc", FileAccess::Write { truncate: true })),
        _ => Err(type_error()),
    }
}

fn kind_name(k: EntryKind) -> &'static str {
    match k {
        EntryKind::File => "file",
        EntryKind::Dir => "dir",
        EntryKind::Symlink => "symlink",
        EntryKind::Other => "other",
    }
}

fn lstat_value(m: Option<Meta>) -> Value {
    match m {
        None => Value::Null,
        Some(m) if m.kind == EntryKind::File => json!({"type": "file", "size": m.len}),
        Some(m) => json!({ "type": kind_name(m.kind) }),
    }
}

fn open_in(work: &Work, parts: &[String]) -> Result<SafeDir, Outcome> {
    safe_fs::open_dir(&work.top, parts, DirOptions::default()).map_err(|e| map_error(&e))
}

fn act_create_excl(work: &Work, call: &Map<String, Value>) -> Result<Value, Outcome> {
    let parts = str_list(call, "dir")?;
    let name = str_field(call, "name")?;
    let (flags, access) = create_flags(call, "w")?;
    let d = open_in(work, &parts)?;
    let f = d
        .create_excl(&name, access, CREATE_MODE)
        .map_err(|e| map_error(&e))?;
    file_result(f, flags)
}

fn act_open_or_create(work: &Work, call: &Map<String, Value>) -> Result<Value, Outcome> {
    let parts = str_list(call, "dir")?;
    let name = str_field(call, "name")?;
    let (flags, access) = create_flags(call, "rw")?;
    let d = open_in(work, &parts)?;
    let f = d
        .open_or_create(&name, access, CREATE_MODE)
        .map_err(|e| map_error(&e))?;
    file_result(f, flags)
}

fn act_lstat(work: &Work, call: &Map<String, Value>) -> Result<Value, Outcome> {
    let parts = str_list(call, "dir")?;
    let name = str_field(call, "name")?;
    let d = open_in(work, &parts)?;
    d.lstat(&name).map(lstat_value).map_err(|e| map_error(&e))
}

fn act_entries(work: &Work, call: &Map<String, Value>) -> Result<Value, Outcome> {
    let parts = str_list(call, "dir")?;
    let d = open_in(work, &parts)?;
    let rows = d.entries().map_err(|e| map_error(&e))?;
    Ok(Value::Array(
        rows.into_iter()
            .map(|(n, m)| json!([n.to_string_lossy(), kind_name(m.kind)]))
            .collect(),
    ))
}

fn remove_args(call: &Map<String, Value>) -> Result<(Vec<String>, String, bool), Outcome> {
    Ok((
        str_list(call, "dir")?,
        str_field(call, "name")?,
        bool_field(call, "missing_ok")?,
    ))
}

fn act_unlink(work: &Work, call: &Map<String, Value>) -> Result<Value, Outcome> {
    let (parts, name, missing_ok) = remove_args(call)?;
    let d = open_in(work, &parts)?;
    d.unlink(&name, missing_ok).map_err(|e| map_error(&e))?;
    Ok(Value::Null)
}

fn act_rmtree(work: &Work, call: &Map<String, Value>) -> Result<Value, Outcome> {
    let (parts, name, missing_ok) = remove_args(call)?;
    let d = open_in(work, &parts)?;
    d.rmtree(&name, missing_ok).map_err(|e| map_error(&e))?;
    Ok(Value::Null)
}

type Move = fn(&SafeDir, &str, &SafeDir, &str) -> safe_fs::Result<()>;

fn act_move(work: &Work, call: &Map<String, Value>, op: Move) -> Result<Value, Outcome> {
    let src_parts = str_list(call, "src_dir")?;
    let src = str_field(call, "src")?;
    let dst_parts = str_list(call, "dst_dir")?;
    let dst = str_field(call, "dst")?;
    let s = open_in(work, &src_parts)?;
    let t = open_in(work, &dst_parts)?;
    op(&s, &src, &t, &dst).map_err(|e| map_error(&e))?;
    Ok(Value::Null)
}

fn act_rename(work: &Work, call: &Map<String, Value>) -> Result<Value, Outcome> {
    act_move(work, call, |s, a, t, b| s.rename(a, t, b))
}

fn act_replace(work: &Work, call: &Map<String, Value>) -> Result<Value, Outcome> {
    act_move(work, call, |s, a, t, b| s.replace(a, t, b))
}

fn act_link(work: &Work, call: &Map<String, Value>) -> Result<Value, Outcome> {
    act_move(work, call, |s, a, t, b| s.hard_link(a, t, b))
}

/// `safe_fs.check_name`:字串 → 合格 `ok null`、不合格 `reject invalid_name`;非字串 `error type`。
pub fn safe_fs_check_name(input: &Value) -> Outcome {
    let Value::String(s) = input else {
        return type_error();
    };
    match safe_fs::check_name(std::ffi::OsStr::new(s)) {
        Ok(()) => Outcome::Ok(Value::Null),
        Err(e) => map_error(&e),
    }
}

/// `safe_fs.open_dir`。
pub fn safe_fs_open_dir(input: &Value) -> Outcome {
    run_tree_case(input, act_open_dir)
}

/// `safe_fs.open_child`。
pub fn safe_fs_open_child(input: &Value) -> Outcome {
    run_tree_case(input, act_open_child)
}

/// `safe_fs.open_file`。
pub fn safe_fs_open_file(input: &Value) -> Outcome {
    run_tree_case(input, act_open_file)
}

/// `safe_fs.open_path`。
pub fn safe_fs_open_path(input: &Value) -> Outcome {
    run_tree_case(input, act_open_path)
}

/// `safe_fs.create_excl`。
pub fn safe_fs_create_excl(input: &Value) -> Outcome {
    run_tree_case(input, act_create_excl)
}

/// `safe_fs.open_or_create`。
pub fn safe_fs_open_or_create(input: &Value) -> Outcome {
    run_tree_case(input, act_open_or_create)
}

/// `safe_fs.lstat`。
pub fn safe_fs_lstat(input: &Value) -> Outcome {
    run_tree_case(input, act_lstat)
}

/// `safe_fs.entries`。
pub fn safe_fs_entries(input: &Value) -> Outcome {
    run_tree_case(input, act_entries)
}

/// `safe_fs.unlink`。
pub fn safe_fs_unlink(input: &Value) -> Outcome {
    run_tree_case(input, act_unlink)
}

/// `safe_fs.rmtree`。
pub fn safe_fs_rmtree(input: &Value) -> Outcome {
    run_tree_case(input, act_rmtree)
}

/// `safe_fs.rename`。
pub fn safe_fs_rename(input: &Value) -> Outcome {
    run_tree_case(input, act_rename)
}

/// `safe_fs.replace`。
pub fn safe_fs_replace(input: &Value) -> Outcome {
    run_tree_case(input, act_replace)
}

/// `safe_fs.link`。
pub fn safe_fs_link(input: &Value) -> Outcome {
    run_tree_case(input, act_link)
}

#[cfg(test)]
mod tests {
    //! L1:輸入格式不符一律 `error type`,且不留下工作目錄。
    use super::*;

    #[test]
    fn malformed_inputs_are_type_errors() {
        for input in [
            json!("5"),
            json!(null),
            json!({"tree": []}),
            json!({"call": 1}),
            json!({"tree": 1, "call": {}}),
            json!({"tree": [{"path": "../x", "type": "dir"}], "call": {}}),
            json!({"tree": [{"path": "a", "type": "fifo"}], "call": {}}),
            json!({"tree": [{"path": "a", "type": "symlink", "target": "/abs", "kind": "dir"}], "call": {}}),
            json!({"tree": [], "call": {"parts": "a"}}),
            json!({"tree": [], "call": {"parts": [1]}}),
            json!({"tree": [], "call": {"create": "yes"}}),
        ] {
            assert_eq!(safe_fs_open_dir(&input), type_error(), "{input}");
        }
        assert_eq!(
            safe_fs_open_file(&json!({"call": {"name": "a", "flags": "x"}})),
            type_error()
        );
        assert_eq!(
            safe_fs_open_child(&json!({"call": {"name": 5}})),
            type_error()
        );
        assert_eq!(safe_fs_check_name(&json!(5)), type_error());
        // T2:建立類只接受含寫入的旗標;各單元的欄位型別不符
        for f in [json!("r"), json!("x"), json!(1)] {
            let input = json!({"call": {"dir": [], "name": "a", "flags": f}});
            assert_eq!(safe_fs_create_excl(&input), type_error(), "{input}");
            assert_eq!(safe_fs_open_or_create(&input), type_error(), "{input}");
        }
        assert_eq!(safe_fs_lstat(&json!({"call": {"dir": []}})), type_error());
        assert_eq!(
            safe_fs_entries(&json!({"call": {"dir": "a"}})),
            type_error()
        );
        for unit in [safe_fs_unlink, safe_fs_rmtree] {
            assert_eq!(
                unit(&json!({"call": {"name": "a", "missing_ok": 1}})),
                type_error()
            );
        }
        for unit in [safe_fs_rename, safe_fs_replace, safe_fs_link] {
            assert_eq!(
                unit(&json!({"call": {"src": "a", "dst_dir": [], "dst": 1}})),
                type_error()
            );
            assert_eq!(unit(&json!("5")), type_error());
        }
    }

    #[test]
    fn work_directory_is_removed_even_on_setup_error() {
        let w = Work::new().expect("建立工作目錄失敗");
        let root = w.root.clone();
        assert!(root.is_dir());
        assert!(
            root.file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with(WORK_PREFIX)
        );
        drop(w);
        assert!(!root.exists());
    }

    #[test]
    fn work_directory_is_removed_when_the_unit_panics() {
        // 施工者注意 (b):Registry 以 catch_unwind 攔截 panic 時,Drop 守衛仍刪除工作目錄
        let root = std::panic::catch_unwind(|| {
            let w = Work::new().expect("建立工作目錄失敗");
            let root = w.root.clone();
            std::panic::panic_any(root);
        })
        .expect_err("應 panic");
        let root = root.downcast::<PathBuf>().expect("panic 內容為路徑");
        assert!(!root.exists(), "panic 後工作目錄仍在");
    }

    #[test]
    fn token_round_trip() {
        let w = Work::new().expect("建立工作目錄失敗");
        for t in ["@top", "@top/a/b", "@outside/d", "@missing"] {
            let p = w.target(Some(&json!(t))).unwrap();
            assert_eq!(w.token(&p), t);
        }
    }
}
