//! 讀取(`project.py` 的 `open_project`、`open_subdir`(不建立)、`_case_variant`、`_read_project_file`、`load`、
//! `load_version`、`list_history`、`list_projects`、`disk_usage`;CHG-20261128-01 T4,代決 15~17、20)。
//!
//! 全部以 `safe-fs` 的 [`SafeDir`] 自資料根目錄 `root` 逐層、不跟隨連結開啟(`root` 之下為 `projects/`,
//! 與 Python `project.ROOT` 相同;`root` 本身視為受信目錄)。**本模組不建立、不修改、不刪除任何東西**:
//! 目錄一律 `create: false` 開啟、檔案一律 [`FileAccess::Read`],不取專案鎖、不建立 `.lock`(代決 20;
//! `tests/read.rs` 以呼叫前後的樹快照鎖住)。
//!
//! 錯誤訊息逐字與 Python 相同;類別對照見 [`crate::error`]。平台差異只有 Windows 的保留裝置名(行為差異 D-8):
//! `safe-fs` 對 `CON`、`nul`、`COM1` 等回 `ReservedDevice`,開啟名稱為保留裝置名的既有專案時回
//! [`RESERVED_DEVICE`](`Invalid`),[`list_projects`] 略過這些項目(無法以 handle 取得中繼資料;T4 補記);
//! macOS / Linux 照常載入。非 UTF-8 的目錄項(代決 16):[`list_projects`]、[`case_variant`]、[`list_history`] 略過。

use std::ffi::OsStr;
use std::io::Read;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use safe_fs::{DirOptions, EntryKind, FileAccess, SafeDir};
use serde_json::Value;
use studio_core::names;

use crate::checks::check_name;
use crate::constants::{MAX_PROJECT_BYTES, PROJECT_FILE, PROJECTS_NAME};
use crate::model::Project;
use crate::validate::{check_version, parse_project_json};
use crate::{Error, ErrorKind, Result};

/// 專案不存在(Python `ProjectNotFound("找不到這個專案")`)。
pub const NOT_FOUND: &str = "找不到這個專案";
/// 已有只差大小寫的其他專案(Python `_project_fd`,`create=False`)。
pub const CASE_VARIANT: &str = "找不到這個專案(已有只差大小寫的其他專案)";
/// 版本不存在或版本 id 格式不對(Python `load_version`)。
pub const VERSION_NOT_FOUND: &str = "找不到這個版本";
/// 專案檔是連結或不是一般檔案(Python `_read_project_file` 的 `UnsafePathError`)。
pub const FILE_UNSAFE: &str = "專案檔無法讀取(可能是符號連結或不是一般檔案)";
/// 專案檔無法開啟的其他作業系統錯誤(Python `_read_project_file` 的 `OSError`)。
pub const FILE_UNREADABLE: &str = "專案檔無法讀取(可能是符號連結)";
/// 專案檔內的名稱與專案目錄不符(Python `load`)。
pub const NAME_MISMATCH: &str = "專案檔的名稱與專案目錄不符,拒絕載入";
/// 版本檔內的名稱與專案不符(Python `load_version`)。
pub const VERSION_NAME_MISMATCH: &str = "版本檔的名稱與專案不符,拒絕還原";
/// Windows 上開啟名稱為保留裝置名的既有專案(行為差異 D-8;Python 不在 Windows 執行)。
pub const RESERVED_DEVICE: &str = "專案名稱是 Windows 保留裝置名,無法在 Windows 上開啟";

/// `history/` 的目錄名稱。
pub const HISTORY_DIR: &str = "history";
/// 專案目錄的 `_child` 標籤(錯誤訊息中的「資料目錄「專案」」)。
const PROJECT_LABEL: &str = "專案";

/// 專案子目錄(Python `SUBDIRS = ("audio", "assets", "tmp")`;Python 對其他名稱丟 `ValueError`,Rust 由型別排除,
/// 行為差異 D-12 T4 補記)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Subdir {
    Audio,
    Assets,
    Tmp,
}

impl Subdir {
    /// 目錄名稱(也是錯誤訊息的標籤)。
    pub fn as_str(self) -> &'static str {
        match self {
            Subdir::Audio => "audio",
            Subdir::Assets => "assets",
            Subdir::Tmp => "tmp",
        }
    }
}

/// [`list_projects`] 的一筆(Python `{"name", "updated"}`;`updated` 為專案檔的修改時間)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProjectEntry {
    pub name: String,
    pub updated: SystemTime,
}

/// Python `float` 的 `st_mtime`(`秒 + 奈秒 × 1e-9`,與 CPython `fill_time` 同一算式;早於紀元為負)。
pub fn mtime_seconds(t: SystemTime) -> f64 {
    match t.duration_since(UNIX_EPOCH) {
        Ok(d) => d.as_secs() as f64 + f64::from(d.subsec_nanos()) * 1e-9,
        Err(e) => -(e.duration().as_secs_f64()),
    }
}

/// Python `st_mtime_ns`(UNIX 紀元起的奈秒,早於紀元為負)。
pub fn mtime_ns(t: SystemTime) -> i128 {
    match t.duration_since(UNIX_EPOCH) {
        Ok(d) => d.as_nanos() as i128,
        Err(e) => -(e.duration().as_nanos() as i128),
    }
}

/// `STAMP_RE` 的一段:`n` 個 ASCII 數字(`re.ASCII` 的 `\d`)。
fn ascii_digits(s: &[u8]) -> bool {
    s.iter().all(u8::is_ascii_digit)
}

fn lower_hex(s: &[u8]) -> bool {
    s.iter().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// `STAMP_RE` + `-` + 4 碼小寫十六進位(`VERSION_RE` 的內容,24 個位元組)。
fn version_bytes(b: &[u8]) -> bool {
    b.len() == 24
        && ascii_digits(&b[0..8])
        && b[8] == b'-'
        && ascii_digits(&b[9..15])
        && b[15] == b'-'
        && ascii_digits(&b[16..19])
        && b[19] == b'-'
        && lower_hex(&b[20..24])
}

/// `VERSION_RE.fullmatch(vid)`:`\d{8}-\d{6}-\d{3}-[0-9a-f]{4}`(`re.ASCII`)。
pub fn is_version_id(vid: &str) -> bool {
    version_bytes(vid.as_bytes())
}

/// `HISTORY_RE.fullmatch(name)` 的第 1 組(時間戳 `YYYYmmdd-HHMMSS-mmm`);不符回 `None`。
pub fn history_stamp(name: &str) -> Option<&str> {
    let stem = name.strip_suffix(".json")?;
    version_bytes(stem.as_bytes()).then(|| &stem[..19])
}

/// 檔案系統錯誤中的「不存在」(Python 的 `FileNotFoundError`)。
fn is_fs_not_found(e: &Error) -> bool {
    e.kind() == ErrorKind::Fs(safe_fs::ErrorKind::NotFound)
}

/// 讀檔時的 I/O 錯誤(Python 直接丟出的 `OSError`);訊息只有錯誤碼,不含路徑。專案鎖的上鎖錯誤也用此轉換(T5)。
pub(crate) fn io_error(e: &std::io::Error) -> Error {
    let code = e.raw_os_error();
    let message = match code {
        Some(c) => format!("作業系統錯誤(錯誤碼 {c})"),
        None => "作業系統錯誤".to_string(),
    };
    Error::new(
        ErrorKind::Fs(safe_fs::ErrorKind::Other(code)),
        vec![message],
    )
}

/// Python `_child(dirfd, part, label, create=False)`:開啟子目錄 `part`。
///
/// 是連結 → `UnsafePath`「資料目錄「label」是符號連結,不接受」;不是目錄(或檢查後消失)→ `UnsafePath`
/// 「資料目錄「label」不在預期位置」;不存在 → `Fs(NotFound)`(Python `FileNotFoundError(label)`,由呼叫端轉換);
/// Windows 保留裝置名 → `Invalid`([`RESERVED_DEVICE`],D-8);其他作業系統錯誤 → `Fs`。
pub(crate) fn child(dir: &SafeDir, part: &str, label: &str) -> Result<SafeDir> {
    child_in(dir, part, label, DirOptions::default())
}

/// [`child`] 的一般形式:`opts.create` 時不存在就建立(Python `_child(..., create=True)`;只供寫入 API 使用,T6)。
/// 錯誤的對應與 [`child`] 相同。
pub(crate) fn child_in(
    dir: &SafeDir,
    part: &str,
    label: &str,
    opts: DirOptions,
) -> Result<SafeDir> {
    match dir.open_child(part, opts) {
        Ok(d) => Ok(d),
        Err(e) => match e.kind() {
            safe_fs::ErrorKind::Unsafe => {
                let is_link =
                    matches!(dir.lstat(part), Ok(Some(m)) if m.kind == EntryKind::Symlink);
                let message = if is_link {
                    format!("資料目錄「{label}」是符號連結,不接受")
                } else {
                    format!("資料目錄「{label}」不在預期位置")
                };
                Err(Error::new(ErrorKind::UnsafePath, vec![message]))
            }
            safe_fs::ErrorKind::ReservedDevice => Err(Error::invalid(RESERVED_DEVICE)),
            _ => Err(Error::from(e)),
        },
    }
}

/// Python `_root_fd(create=False)`:`root` 之下的 `projects/`;`root` 或 `projects/` 不存在 → `Fs(NotFound)`
/// (T7 起 `pub(crate)`,回收與清理共用)。
pub(crate) fn root_dir(root: &Path) -> Result<SafeDir> {
    let top = safe_fs::open_dir(root, &[] as &[&str], DirOptions::default())?;
    child(&top, PROJECTS_NAME, PROJECTS_NAME)
}

/// Python `_case_variant(rfd, name)`:`projects/` 內與 `name` 只差大小寫(`fold_name` 相等、本身不同)的既有項目;
/// 點開頭的項目不算。非 UTF-8 的目錄項略過(摺疊鍵不可能相等,代決 16)。
pub fn case_variant(projects: &SafeDir, name: &str) -> Result<Option<String>> {
    let key = names::fold_name(name);
    for entry in projects.entry_names()? {
        let Some(entry) = entry.to_str() else {
            continue;
        };
        if entry != name && !entry.starts_with('.') && names::fold_name(entry) == key {
            return Ok(Some(entry.to_string()));
        }
    }
    Ok(None)
}

/// Python `open_project(name)`(`create=False`):`projects/<名稱>/` 的 handle。
///
/// 名稱不合規則 → `Invalid`(`NAME_HINT`);`root` / `projects/` / 專案不存在 → `NotFound`;已有只差大小寫的其他
/// 專案 → `NotFound`([`CASE_VARIANT`]);任一層是連結或不是目錄 → `UnsafePath`。不建立任何東西;
/// 建立分支屬寫入 API(T6)。
pub fn open_project(root: &Path, name: &str) -> Result<SafeDir> {
    let name = check_name(name)?;
    let projects = match root_dir(root) {
        Ok(d) => d,
        Err(e) if is_fs_not_found(&e) => {
            return Err(Error::new(ErrorKind::NotFound, vec![NOT_FOUND.to_string()]));
        }
        Err(e) => return Err(e),
    };
    project_in(&projects, name)
}

/// Python `_project_fd(rfd, name, create=False)`:已開啟的 `projects/` 內的專案目錄(名稱已檢查;T7 起與回收共用)。
/// 已有只差大小寫的其他專案 → `NotFound`([`CASE_VARIANT`]);不存在 → `NotFound`;是連結或不是目錄 → `UnsafePath`。
pub(crate) fn project_in(projects: &SafeDir, name: &str) -> Result<SafeDir> {
    if case_variant(projects, name)?.is_some() {
        return Err(Error::new(
            ErrorKind::NotFound,
            vec![CASE_VARIANT.to_string()],
        ));
    }
    match child(projects, name, PROJECT_LABEL) {
        Err(e) if is_fs_not_found(&e) => {
            Err(Error::new(ErrorKind::NotFound, vec![NOT_FOUND.to_string()]))
        }
        other => other,
    }
}

/// Python `open_subdir(name, sub)`(`create=False`):專案子目錄的 handle;子目錄不存在 → `Fs(NotFound)`
/// (Python `FileNotFoundError`),是連結或不是目錄 → `UnsafePath`。
pub fn open_subdir(root: &Path, name: &str, sub: Subdir) -> Result<SafeDir> {
    let project = open_project(root, name)?;
    child(&project, sub.as_str(), sub.as_str())
}

/// Python `_read_project_file(dirfd, filename)`:唯讀開啟一般檔、最多讀 5 MB + 1 位元組,再
/// [`parse_project_json`](5 MB 上限、UTF-8、JSON、須為物件)。
///
/// 不存在 → `NotFound`([`NOT_FOUND`]);是連結或不是一般檔 → `Invalid`([`FILE_UNSAFE`]);其他開啟錯誤 →
/// `Invalid`([`FILE_UNREADABLE`]);讀取中的 I/O 錯誤 → `Fs`。
pub fn read_project_file(dir: &SafeDir, filename: &str) -> Result<Value> {
    let file = match dir.open_file(filename, FileAccess::Read) {
        Ok(f) => f,
        Err(e) => {
            return Err(match e.kind() {
                safe_fs::ErrorKind::NotFound => {
                    Error::new(ErrorKind::NotFound, vec![NOT_FOUND.to_string()])
                }
                safe_fs::ErrorKind::Unsafe => Error::invalid(FILE_UNSAFE),
                _ => Error::invalid(FILE_UNREADABLE),
            });
        }
    };
    let mut raw = Vec::new();
    file.take(MAX_PROJECT_BYTES as u64 + 1)
        .read_to_end(&mut raw)
        .map_err(|e| io_error(&e))?;
    parse_project_json(&raw)
}

/// 載入後的共同檢查:版本、檔內名稱、完整驗證(Python `_check_version` → 名稱比對 → `Project.from_dict`)。
fn checked(data: &Value, name: &str, mismatch: &str) -> Result<Project> {
    check_version(data.get("schema_version"))?;
    if data.get("name").and_then(Value::as_str) != Some(name) {
        return Err(Error::invalid(mismatch));
    }
    Project::from_dict(data)
}

/// Python `load(name)`:讀 `projects/<名稱>/project.json`;schema 1 在記憶體升級為 2,其他版本拒絕;
/// 檔內名稱須與目錄相同。
pub fn load(root: &Path, name: &str) -> Result<Project> {
    let name = check_name(name)?;
    let project = open_project(root, name)?;
    let data = read_project_file(&project, PROJECT_FILE)?;
    checked(&data, name, NAME_MISMATCH)
}

/// Python `_history_fd(name)`:`history/` 的 handle(專案不存在 → `NotFound`,`history/` 不存在 → `Fs(NotFound)`)。
fn history_dir(root: &Path, name: &str) -> Result<SafeDir> {
    let project = open_project(root, name)?;
    child(&project, HISTORY_DIR, HISTORY_DIR)
}

/// Python `_history_entries(hfd)`:符合 `HISTORY_RE` 的一般檔 `(時間戳, mtime_ns, 檔名)`,依此排序
/// (CHG-20261122-01 的排序鍵)。名稱不符的項目(含非 UTF-8)不取中繼資料、直接略過。
pub(crate) fn history_entries(history: &SafeDir) -> Result<Vec<(String, i128, String)>> {
    let mut out = Vec::new();
    for entry in history.entry_names()? {
        let Some(file) = entry.to_str() else {
            continue;
        };
        let Some(stamp) = history_stamp(file) else {
            continue;
        };
        if let Some(meta) = history.lstat(OsStr::new(file))?
            && meta.kind == EntryKind::File
        {
            out.push((stamp.to_string(), mtime_ns(meta.modified), file.to_string()));
        }
    }
    out.sort();
    Ok(out)
}

/// Python `list_history(name)`:版本 id(舊到新);專案或 `history/` 不存在回空清單。
pub fn list_history(root: &Path, name: &str) -> Result<Vec<String>> {
    let name = check_name(name)?;
    let history = match history_dir(root, name) {
        Ok(d) => d,
        Err(e) if is_fs_not_found(&e) || e.kind() == ErrorKind::NotFound => {
            return Ok(Vec::new());
        }
        Err(e) => return Err(e),
    };
    Ok(history_entries(&history)?
        .into_iter()
        .map(|(_, _, file)| file[..file.len() - ".json".len()].to_string())
        .collect())
}

/// Python `load_version(name, vid)`:讀 `history/<版本 id>.json`;版本 id 格式不對、`history/` 或版本檔不存在 →
/// `NotFound`([`VERSION_NOT_FOUND`]);專案不存在 → `NotFound`([`NOT_FOUND`]);檔內名稱須與專案相同。
pub fn load_version(root: &Path, name: &str, vid: &str) -> Result<Project> {
    let name = check_name(name)?;
    let version_not_found = || Error::new(ErrorKind::NotFound, vec![VERSION_NOT_FOUND.to_string()]);
    if !is_version_id(vid) {
        return Err(version_not_found());
    }
    let history = match history_dir(root, name) {
        Ok(d) => d,
        Err(e) if is_fs_not_found(&e) => return Err(version_not_found()),
        Err(e) => return Err(e),
    };
    let data = match read_project_file(&history, &format!("{vid}.json")) {
        Ok(d) => d,
        Err(e) if e.kind() == ErrorKind::NotFound => return Err(version_not_found()),
        Err(e) => return Err(e),
    };
    checked(&data, name, VERSION_NAME_MISMATCH)
}

/// Python `list_projects()`:`projects/` 下有一般檔 `project.json` 的專案,依名稱(碼位)排序。
///
/// `projects/` 不存在回空清單;點開頭、名稱不合規則、非 UTF-8(代決 16)、不是目錄(含連結)、開不了、沒有一般檔
/// `project.json` 的項目略過;Windows 上名稱為保留裝置名的項目略過(D-8 T4 補記)。名稱先過濾再取中繼資料。
pub fn list_projects(root: &Path) -> Result<Vec<ProjectEntry>> {
    let projects = match root_dir(root) {
        Ok(d) => d,
        Err(e) if is_fs_not_found(&e) => return Ok(Vec::new()),
        Err(e) => return Err(e),
    };
    let mut out = Vec::new();
    for entry in projects.entry_names()? {
        let Some(name) = entry.to_str() else {
            continue;
        };
        if name.starts_with('.') || names::normalize_name(name).is_none() {
            continue;
        }
        let meta = match projects.lstat(&entry) {
            Ok(Some(m)) => m,
            Ok(None) => continue,
            Err(e) if e.kind() == safe_fs::ErrorKind::ReservedDevice => continue,
            Err(e) => return Err(e.into()),
        };
        if meta.kind != EntryKind::Dir {
            continue;
        }
        let Ok(project) = projects.open_child(&entry, DirOptions::default()) else {
            continue;
        };
        if let Some(file) = project.lstat(PROJECT_FILE)?
            && file.kind == EntryKind::File
        {
            out.push(ProjectEntry {
                name: name.to_string(),
                updated: file.modified,
            });
        }
    }
    Ok(out)
}

/// Python `disk_usage(name)`:專案 `assets/` 與 `audio/` 內一般檔(不跟隨連結)的位元組合計;子目錄不存在不算錯。
pub fn disk_usage(root: &Path, name: &str) -> Result<u64> {
    let mut total = 0u64;
    for sub in [Subdir::Assets, Subdir::Audio] {
        let dir = match open_subdir(root, name, sub) {
            Ok(d) => d,
            Err(e) if is_fs_not_found(&e) => continue,
            Err(e) => return Err(e),
        };
        for (_, meta) in dir.entries()? {
            if meta.kind == EntryKind::File {
                total = total.saturating_add(meta.len);
            }
        }
    }
    Ok(total)
}
