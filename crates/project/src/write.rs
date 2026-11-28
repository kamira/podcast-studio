//! 寫入(`project.py` 的 `open_project` / `open_subdir` 的建立分支、`save` / `_save_locked`、`_archive_current`、
//! `_write_atomic`、`_rotate`、`update`、`restore`、`create`,以及時間戳的 `_stamp` / `_stamp_ms` / `_format_ms`;
//! CHG-20261128-01 T6,代決 6、13、15)。
//!
//! 逐步與 Python 相同(「## 設計圖」(2)):
//!
//! 1. 驗證並以 Python 相同位元組寫出(`_dump(validate(to_dict()))`,5 MB 上限);
//! 2. 開啟專案目錄,不存在時建立(新名稱先經 `names::normalize_new_name`,Windows 保留名稱被拒,行為差異 D-7);
//! 3. 取專案鎖([`ProjectLock`],阻塞);
//! 4. `history/`(不存在時建立):在鎖內讀出最新的版本時間戳,現在 ≤ 最新時取最新 + 1 毫秒(**嚴格遞增**,
//!    CHG-20261122-01);以硬連結把目前的 `project.json` 排他建立成 `history/<時間戳>-<4 碼>.json`
//!    (撞名只換隨機碼重試,最多 [`LINK_TRIES`] 次;不覆寫任何既有項目、不退回複製);
//! 5. 原子寫入:排他建立暫存檔 `.project-<16 碼>.tmp`(撞名重試)→ 寫入 → `sync_all` → 關閉 → `replace` 成
//!    `project.json`;任何一步失敗都刪除暫存檔;
//! 6. 輪替([`rotate`]):`history/` 只留最近 [`HISTORY_KEEP`] 版。
//!
//! **輪替的刪除選取規則**(「## 安全影響」irreversible-delete;與 Python `_history_entries` + `_rotate` 逐字一致):
//! 只看該專案 `history/` 內、名稱完全符合 `HISTORY_RE`(`\d{8}-\d{6}-\d{3}-[0-9a-f]{4}\.json`,ASCII)**且**不跟隨連結
//! 取得的型別是一般檔的項目;依 `(時間戳, mtime_ns, 檔名)` 排序後刪除最前面(最舊)的 `總數 − 10` 個。連結(含指向
//! 一般檔的連結)、目錄、FIFO 等非一般檔、名稱不符(含大寫十六進位、非 UTF-8)的項目一律不計入、不刪除;
//! `history/` 本身是連結或不是目錄時拒絕輪替(回警告、不刪任何東西)。刪除只經 `safe-fs` 相對 `history/` handle 的
//! `unlink`(不跟隨連結)。
//!
//! 時間與隨機碼一律取自注入的 [`Clock`](代決 13;正式使用 [`crate::ids::SystemClock`]):`now_ms`(版本時間戳)、
//! `rand4`(版本檔名的 4 碼)、`token_hex8`(暫存檔名、`create` 產生的片段 id)。呼叫順序與 Python 相同,
//! 比對單元注入固定序列即可得到與 Python 相同的結果。
//!
//! 警告(Python `log.warning`)以回傳值交呼叫端([`Saved::warnings`]、[`Updated::warnings`],行為差異 D-10);
//! 失敗時 Python 另記的「拒絕存檔」警告不另回傳,錯誤本身即是同一資訊(D-10 T6 補記)。
//!
//! 同一執行緒持有某專案的 [`ProjectLock`] 時不得再呼叫本模組對同一專案的寫入 API(會等到自己,代決 11)。

use std::ffi::OsStr;
use std::io::Write;
use std::path::Path;

use safe_fs::{DirOptions, EntryKind, FileAccess, SafeDir};
use studio_core::names::{self, NewNameError};

use crate::checks::check_name;
use crate::constants::{HISTORY_KEEP, LINK_TRIES, NAME_HINT, PROJECT_FILE, PROJECTS_NAME};
use crate::ids::Clock;
use crate::lock::ProjectLock;
use crate::model::{Defaults, Project, Segment};
use crate::read::{
    self, HISTORY_DIR, NOT_FOUND, Subdir, case_variant, child_in, history_entries, io_error,
};
use crate::text::split_segments;
use crate::validate::validate_and_dump;
use crate::{Error, ErrorKind, Result};

/// 以 Windows 保留名稱建立新專案(行為差異 D-7;Python 接受)。
pub const RESERVED_NEW_NAME: &str = "專案名稱是 Windows 保留名稱(例如 CON、COM1),請改用其他名稱";
/// 建立時已有只差大小寫的同名專案(Python `_project_fd`,`create=True`)。
pub const CASE_EXISTS: &str = "已有只差大小寫的同名專案,請改用其他名稱";
/// `create` 時已有同名(含只差大小寫)的專案。
pub const EXISTS: &str = "已有同名的專案,請改用其他名稱";
/// `update` 的修改改變了專案名稱。
pub const RENAME_IN_UPDATE: &str = "不能在修改時改變專案名稱";
/// 目前的 `project.json` 不是一般檔(Python `_archive_current`)。
pub const NOT_REGULAR: &str = "專案檔不是一般檔案(可能是符號連結),拒絕存檔";
/// 版本檔名撞名重試 [`LINK_TRIES`] 次仍失敗。
pub const NO_UNIQUE_VERSION: &str = "無法建立不重複的版本檔名";
/// 暫存檔名撞名重試 [`LINK_TRIES`] 次仍失敗。
pub const NO_TEMP: &str = "無法建立暫存檔";
/// `history/` 內有符合 `HISTORY_RE` 但日期時間無效的版本檔名(行為差異 D-5;Python 丟 `ValueError`)。
pub const BAD_HISTORY_NAME: &str = "history 內有無法解析的版本檔名,拒絕存檔";
/// 版本時間戳超出可記錄的範圍(行為差異 D-5 T6 補記;Python 丟 `OverflowError` 或寫出不符命名規則的檔名)。
pub const STAMP_OUT_OF_RANGE: &str = "版本時間戳超出可記錄的範圍(1000 年至 9999 年),拒絕存檔";

/// 暫存檔名的前綴與後綴(`.project-<16 碼>.tmp`)。
pub const TEMP_PREFIX: &str = ".project-";
pub const TEMP_SUFFIX: &str = ".tmp";
/// 暫存檔的權限位元(與 Python 相同;只在 Unix 生效,受 umask 影響;Windows 忽略,D-14)。
const TEMP_MODE: u32 = 0o644;
/// 建立目錄的選項(Python `_child(..., create=True)`:`mkdir` 的權限 0o755)。
const CREATE: DirOptions = DirOptions {
    create: true,
    mode: 0o755,
};
/// 專案目錄的 `_child` 標籤。
const PROJECT_LABEL: &str = "專案";

/// [`save`]、[`restore`] 的結果:這次移入 `history/` 的版本 id(第一次存檔為 `None`)與警告。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Saved {
    pub version: Option<String>,
    pub warnings: Vec<String>,
}

/// [`update`] 的結果(Python `(Project, 版本 id, change 的回傳值)`)與警告。
#[derive(Debug, Clone, PartialEq)]
pub struct Updated<T> {
    pub project: Project,
    pub version: Option<String>,
    pub extra: T,
    pub warnings: Vec<String>,
}

// ---------- 時間戳(`YYYYmmdd-HHMMSS-mmm`,UTC) ----------

const MS_PER_DAY: i64 = 86_400_000;

/// 公曆日期 → UNIX 紀元起的日數(Howard Hinnant 的 `days_from_civil`)。
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// UNIX 紀元起的日數 → 公曆日期(`civil_from_days`)。
fn civil_from_days(z: i64) -> (i64, i64, i64) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    (y, m, d)
}

fn is_leap(y: i64) -> bool {
    (y % 4 == 0 && y % 100 != 0) || y % 400 == 0
}

fn days_in_month(y: i64, m: i64) -> i64 {
    match m {
        2 if is_leap(y) => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    }
}

/// Python `_format_ms(ms)` / `_stamp(ms / 1000)`:UNIX 紀元起的整數毫秒 → `YYYYmmdd-HHMMSS-mmm`(UTC)。
///
/// 只接受 1000 年至 9999 年(四位數年份;Python 在此範圍外丟 `OverflowError`,或在部分平台以三位數年份寫出
/// 不符 `HISTORY_RE` 的檔名),其他回 `Invalid`([`STAMP_OUT_OF_RANGE`],行為差異 D-5 T6 補記)。
pub fn format_ms(ms: i64) -> Result<String> {
    let days = ms.div_euclid(MS_PER_DAY);
    let rem = ms.rem_euclid(MS_PER_DAY);
    let (y, m, d) = civil_from_days(days);
    if !(1000..=9999).contains(&y) {
        return Err(Error::invalid(STAMP_OUT_OF_RANGE));
    }
    let (hh, mm, ss, mmm) = (
        rem / 3_600_000,
        rem / 60_000 % 60,
        rem / 1000 % 60,
        rem % 1000,
    );
    Ok(format!("{y:04}{m:02}{d:02}-{hh:02}{mm:02}{ss:02}-{mmm:03}"))
}

fn digits(s: &[u8]) -> Option<i64> {
    if s.is_empty() || !s.iter().all(u8::is_ascii_digit) {
        return None;
    }
    Some(s.iter().fold(0, |n, b| n * 10 + i64::from(b - b'0')))
}

/// Python `_stamp_ms(s)`:`YYYYmmdd-HHMMSS-mmm` → UNIX 紀元起的整數毫秒;Python 的
/// `datetime.strptime(s[:15], "%Y%m%d-%H%M%S")` 會丟 `ValueError` 的回 `None`。
///
/// 有效範圍與 CPython 3.11 相同(施工時以 CPython 3.11.18 逐例核對):年 0001~9999(`0000` 無效)、月 01~12、
/// 日 01~該月天數(含閏年)、時 00~23、分 00~59、秒 00~59(`60`、`61` 雖符合 `%S` 的樣式,但 `datetime` 拒絕)。
pub fn stamp_ms(stamp: &str) -> Option<i64> {
    let b = stamp.as_bytes();
    if b.len() != 19 || b[8] != b'-' || b[15] != b'-' {
        return None;
    }
    let (y, mo, d) = (digits(&b[0..4])?, digits(&b[4..6])?, digits(&b[6..8])?);
    let (h, mi, s, ms) = (
        digits(&b[9..11])?,
        digits(&b[11..13])?,
        digits(&b[13..15])?,
        digits(&b[16..19])?,
    );
    if !(1..=9999).contains(&y)
        || !(1..=12).contains(&mo)
        || !(1..=days_in_month(y, mo)).contains(&d)
        || h > 23
        || mi > 59
        || s > 59
    {
        return None;
    }
    Some(days_from_civil(y, mo, d) * MS_PER_DAY + ((h * 60 + mi) * 60 + s) * 1000 + ms)
}

// ---------- 開啟(建立分支) ----------

/// 新名稱檢查(代決 15):`names::normalize_new_name`;Windows 保留名稱 → `Invalid`([`RESERVED_NEW_NAME`])。
fn check_new_name(name: &str) -> Result<()> {
    match names::normalize_new_name(name) {
        Ok(_) => Ok(()),
        Err(NewNameError::Reserved) => Err(Error::invalid(RESERVED_NEW_NAME)),
        Err(NewNameError::Invalid) => Err(Error::invalid(NAME_HINT)),
    }
}

fn not_found() -> Error {
    Error::new(ErrorKind::NotFound, vec![NOT_FOUND.to_string()])
}

fn is_fs_not_found(e: &Error) -> bool {
    e.kind() == ErrorKind::Fs(safe_fs::ErrorKind::NotFound)
}

/// 資料根目錄 `root` 本身(受信目錄,跟隨連結;不存在 → `Fs(NotFound)`)。
fn open_top(root: &Path) -> Result<SafeDir> {
    Ok(safe_fs::open_dir(
        root,
        &[] as &[&str],
        DirOptions::default(),
    )?)
}

/// `projects/` 是否已有名稱**完全相同**的項目(只看目錄列舉,不開啟;Windows 上也能判定保留裝置名的項目)。
fn listed(projects: &SafeDir, name: &str) -> Result<bool> {
    Ok(projects
        .entry_names()?
        .iter()
        .any(|e| e.as_os_str() == OsStr::new(name)))
}

/// Python `open_project(name, create=True)`:`projects/<名稱>/` 的 handle;`projects/` 與專案目錄不存在時建立。
///
/// 名稱不合規則 → `Invalid`(`NAME_HINT`);`root` 不存在 → `NotFound`;已有只差大小寫的其他專案 → `Exists`
/// ([`CASE_EXISTS`]);任一層是連結或不是目錄 → `UnsafePath`(訊息同 [`crate::read::open_project`])。
/// **專案目錄尚不存在時**名稱另經 `names::normalize_new_name`:Windows 保留名稱(`CON`、`COM1` 等 24 個)三平台一律
/// `Invalid`([`RESERVED_NEW_NAME`],行為差異 D-7),此時不建立任何目錄(含 `projects/`)。既有的保留名稱專案在
/// macOS / Linux 照常開啟,Windows 上回 [`crate::read::RESERVED_DEVICE`](D-8)。
pub fn open_project_create(root: &Path, name: &str) -> Result<SafeDir> {
    let name = check_name(name)?;
    let top = match open_top(root) {
        Ok(d) => d,
        Err(e) if is_fs_not_found(&e) => return Err(not_found()),
        Err(e) => return Err(e),
    };
    let projects = match child_in(&top, PROJECTS_NAME, PROJECTS_NAME, DirOptions::default()) {
        Ok(d) => d,
        Err(e) if is_fs_not_found(&e) => {
            // `projects/` 不存在:專案必然不存在,新名稱檢查先於建立任何目錄
            check_new_name(name)?;
            child_in(&top, PROJECTS_NAME, PROJECTS_NAME, CREATE)?
        }
        Err(e) => return Err(e),
    };
    if case_variant(&projects, name)?.is_some() {
        return Err(Error::new(ErrorKind::Exists, vec![CASE_EXISTS.to_string()]));
    }
    if !listed(&projects, name)? {
        check_new_name(name)?;
    }
    match child_in(&projects, name, PROJECT_LABEL, CREATE) {
        Err(e) if is_fs_not_found(&e) => Err(not_found()),
        other => other,
    }
}

/// Python `open_subdir(name, sub, create=True)`:專案子目錄的 handle;專案目錄與子目錄不存在時建立
/// (專案目錄的規則同 [`open_project_create`])。
pub fn open_subdir_create(root: &Path, name: &str, sub: Subdir) -> Result<SafeDir> {
    let project = open_project_create(root, name)?;
    child_in(&project, sub.as_str(), sub.as_str(), CREATE)
}

// ---------- 存檔 ----------

/// Python `_archive_current(pfd, hfd)`:把目前的 `project.json` 以硬連結排他建立到 `history/`;沒有舊檔回 `None`。
///
/// 版本時間戳嚴格遞增(CHG-20261122-01):已持專案鎖,讀最新時間戳與建檔不會交錯;現在 ≤ `history/` 最新時間戳
/// (同一毫秒內存檔或時鐘倒退)時取最新 + 1 毫秒。撞名(含同名的目錄、連結)只換隨機碼重試。
fn archive_current(
    project: &SafeDir,
    history: &SafeDir,
    clock: &dyn Clock,
) -> Result<Option<String>> {
    match project.lstat(PROJECT_FILE)? {
        None => return Ok(None),
        Some(m) if m.kind != EntryKind::File => {
            return Err(Error::new(
                ErrorKind::UnsafePath,
                vec![NOT_REGULAR.to_string()],
            ));
        }
        Some(_) => {}
    }
    let mut now = clock.now_ms();
    format_ms(now)?;
    let entries = history_entries(history)?;
    if !entries.is_empty() {
        let mut latest = i64::MIN;
        for (stamp, _, _) in &entries {
            latest = latest.max(stamp_ms(stamp).ok_or_else(|| Error::invalid(BAD_HISTORY_NAME))?);
        }
        if now <= latest {
            now = latest.saturating_add(1);
        }
    }
    let stamp = format_ms(now)?;
    for _ in 0..LINK_TRIES {
        let vid = format!("{stamp}-{}", clock.rand4());
        match project.hard_link(PROJECT_FILE, history, format!("{vid}.json")) {
            Ok(()) => return Ok(Some(vid)),
            Err(e) if e.kind() == safe_fs::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e.into()),
        }
    }
    Err(Error::invalid(NO_UNIQUE_VERSION))
}

/// Python `_write_atomic(pfd, payload)`:暫存檔(排他建立)→ 寫入 → `sync_all` → 關閉 → `replace` 成 `project.json`;
/// 失敗時刪除暫存檔(刪除失敗忽略,與 Python 相同)後回錯誤。不持有 `project.json` 的 handle(Windows 上取代時
/// 目標被其他程式唯讀開著仍成功,`tests/write.rs` 鎖住)。
fn write_atomic(project: &SafeDir, payload: &[u8], clock: &dyn Clock) -> Result<()> {
    let mut opened = None;
    for _ in 0..LINK_TRIES {
        let tmp = format!("{TEMP_PREFIX}{}{TEMP_SUFFIX}", clock.token_hex8());
        match project.create_excl(&tmp, FileAccess::Write { truncate: false }, TEMP_MODE) {
            Ok(f) => {
                opened = Some((tmp, f));
                break;
            }
            Err(e) if e.kind() == safe_fs::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e.into()),
        }
    }
    let Some((tmp, mut file)) = opened else {
        return Err(Error::invalid(NO_TEMP));
    };
    let written = file
        .write_all(payload)
        .and_then(|()| file.flush())
        .and_then(|()| file.sync_all())
        .map_err(|e| io_error(&e));
    drop(file);
    let result = written.and_then(|()| Ok(project.replace(&tmp, project, PROJECT_FILE)?));
    if result.is_err() {
        let _ = project.unlink(&tmp, false);
    }
    result
}

/// Python `_rotate(pfd, pname, warnings)`:`history/` 只保留最近 [`HISTORY_KEEP`] 個版本,回傳刪除的檔名(舊到新)。
///
/// 刪除選取規則見模組說明(與 Python 逐字一致)。`history/` 不存在回空清單;`history/` 是連結或不是目錄時拒絕輪替,
/// 在 `warnings` 加一則警告(「專案「名稱」的 history 目錄不安全,拒絕輪替:…」)並回空清單,不刪除任何東西。
/// 呼叫端須持有該專案的 [`ProjectLock`](存檔流程內部呼叫;公開供測試與之後的呼叫端)。
pub fn rotate(project: &SafeDir, name: &str, warnings: &mut Vec<String>) -> Result<Vec<String>> {
    let history = match child_in(project, HISTORY_DIR, HISTORY_DIR, DirOptions::default()) {
        Ok(d) => d,
        Err(e) if is_fs_not_found(&e) => return Ok(Vec::new()),
        Err(e) if e.kind() == ErrorKind::UnsafePath => {
            let reason = e.errors().first().cloned().unwrap_or_default();
            warnings.push(format!(
                "專案「{name}」的 history 目錄不安全,拒絕輪替:{reason}"
            ));
            return Ok(Vec::new());
        }
        Err(e) => return Err(e),
    };
    let entries = history_entries(&history)?;
    let excess = entries.len().saturating_sub(HISTORY_KEEP);
    let mut removed = Vec::with_capacity(excess);
    for (_, _, file) in entries.into_iter().take(excess) {
        history.unlink(&file, true)?;
        removed.push(file);
    }
    Ok(removed)
}

/// Python `_save_locked(pfd, name, data, warnings)`:已持專案鎖時的存檔本體(舊檔移入 `history/` → 原子寫入 → 輪替)。
fn save_locked(
    project: &SafeDir,
    name: &str,
    payload: &[u8],
    clock: &dyn Clock,
    warnings: &mut Vec<String>,
) -> Result<Option<String>> {
    let version = {
        let history = child_in(project, HISTORY_DIR, HISTORY_DIR, CREATE)?;
        archive_current(project, &history, clock)?
    };
    write_atomic(project, payload, clock)?;
    rotate(project, name, warnings)?;
    Ok(version)
}

/// Python `save(project)`:驗證後原子寫入 `project.json`;寫入前把舊檔移入 `history/`,`history/` 只留最近 10 個。
///
/// 同一專案的存檔以專案鎖序列化(跨執行緒、跨行程,也與 Python 網頁版互斥)。專案目錄、`history/` 是連結或
/// 不是目錄、`project.json` 不是一般檔時拒絕(`UnsafePath`),不寫入連結目標。專案目錄不存在時建立
/// ([`open_project_create`] 的新名稱規則)。
pub fn save(root: &Path, project: &Project, clock: &dyn Clock) -> Result<Saved> {
    let (data, payload) = validate_and_dump(&project.to_dict())?;
    let name = data.name.as_str();
    let dir = open_project_create(root, name)?;
    let lock = ProjectLock::acquire(&dir)?;
    let mut warnings = Vec::new();
    let version = save_locked(&dir, name, &payload, clock, &mut warnings)?;
    drop(lock);
    Ok(Saved { version, warnings })
}

/// Python `update(name, change)`:「載入 → `change(&mut project)` → 驗證 → 存檔」全程持專案鎖。
///
/// `change` 回錯誤、驗證不過或改了名稱([`RENAME_IN_UPDATE`])時不存檔。專案不存在 → `NotFound`(不建立)。
/// 回傳存檔後的專案(= `Project.from_dict(驗證結果)`)、版本 id、`change` 的回傳值與警告。
pub fn update<T>(
    root: &Path,
    name: &str,
    clock: &dyn Clock,
    change: impl FnOnce(&mut Project) -> Result<T>,
) -> Result<Updated<T>> {
    let name = check_name(name)?;
    let dir = read::open_project(root, name)?;
    let lock = ProjectLock::acquire(&dir)?;
    let mut project = read::load(root, name)?;
    let extra = change(&mut project)?;
    let (data, payload) = validate_and_dump(&project.to_dict())?;
    if data.name != name {
        return Err(Error::invalid(RENAME_IN_UPDATE));
    }
    let mut warnings = Vec::new();
    let version = save_locked(&dir, name, &payload, clock, &mut warnings)?;
    drop(lock);
    drop(dir);
    Ok(Updated {
        project: Project::from_dict(&data.to_dict())?,
        version,
        extra,
        warnings,
    })
}

/// Python `restore(name, vid)`:還原版本 = 一次存檔(先把目前版本移入 `history/`,再原子寫入該版本的內容)。
pub fn restore(root: &Path, name: &str, vid: &str, clock: &dyn Clock) -> Result<Saved> {
    save(root, &read::load_version(root, name, vid)?, clock)
}

/// Python `create(name, script, defaults)`:新增專案——文稿依 `split_segments` 切成片段(id 取自 `clock.token_hex8()`,
/// 依片段順序),驗證後存檔;已有同名(含只差大小寫)→ `Exists`([`EXISTS`])。
///
/// 名稱先 `check_name`、再經 `names::normalize_new_name`(代決 15):Windows 保留名稱三平台一律 `Invalid`
/// ([`RESERVED_NEW_NAME`],D-7),此時不建立任何目錄。`defaults` 為 `None` 時用 `DEFAULTS`(Python `defaults or DEFAULTS`)。
pub fn create(
    root: &Path,
    name: &str,
    script: &str,
    defaults: Option<Defaults>,
    clock: &dyn Clock,
) -> Result<Project> {
    let name = check_name(name)?;
    check_new_name(name)?;
    let pieces = split_segments(script, &[])?;
    let mut draft = Project::new(name);
    draft.script = script.to_string();
    draft.defaults = defaults.unwrap_or_default();
    draft.segments = pieces
        .into_iter()
        .map(|p| {
            let mut s = Segment::new(clock.token_hex8(), p.text);
            s.brk = p.brk;
            s
        })
        .collect();
    let project = Project::from_dict(&draft.to_dict())?;
    {
        let top = open_top(root)?;
        let projects = child_in(&top, PROJECTS_NAME, PROJECTS_NAME, CREATE)?;
        if case_variant(&projects, name)?.is_some() || projects.lstat(name)?.is_some() {
            return Err(Error::new(ErrorKind::Exists, vec![EXISTS.to_string()]));
        }
    }
    save(root, &project, clock)?;
    Ok(project)
}
