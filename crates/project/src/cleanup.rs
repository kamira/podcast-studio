//! 回收與清理(`project.py` 的 `trash`、`startup_cleanup`(`_clean_trash`、`_clean_project`、`_parse_stamp`)、
//! `unlink_in`、`check_quota`;CHG-20261128-01 T7,代決 13、14、16)。
//!
//! **刪除的選取規則**(「## 安全影響」irreversible-delete;與 Python 逐字一致,由 `project.startup_cleanup` 比對單元與
//! `tests/cleanup.rs` 鎖住):
//!
//! 1. **回收區**([`startup_cleanup`]):只看 `projects/.trash/` 內的項目。不跟隨連結取得的型別是連結者一律不刪
//!    (記警告);名稱須完全符合 `TRASH_RE`(`(.+)-(\d{8}-\d{6}-\d{3})-[0-9a-f]{4}`,**沒有** `re.ASCII`:`\d` 為
//!    Unicode 十進位數字,`.` 不含換行),時間戳以 CPython 3.11 `strptime("%Y%m%d-%H%M%S")` 相同的規則解析
//!    ([`parse_stamp`]);不符或無效者不刪(記警告)。年齡只看名稱內的時間戳(UTC),不看檔案時間:
//!    `現在 − 時間戳 ≥ 30 × 86400` 秒(兩邊都是 Python 相同算式的浮點秒數)才以 `safe-fs` 的 `rmtree` 刪除整個項目
//!    (以 handle 逐層、不跟隨連結;項目內的連結只刪連結本身,不動目標)。
//! 2. **`tmp/` 清空**([`startup_cleanup`]):`projects/` 內每個名稱合規則的專案目錄,其 `tmp/` 內的每個項目以 `rmtree`
//!    刪除;`project.json`、`history/`、`audio/`、`assets/` 與 `tmp/` 本身不動。
//! 3. **單一檔案**([`unlink_in`]):專案子目錄內一個名稱的 `unlink`(不跟隨連結、不存在不算錯)。
//!
//! `projects/`、`.trash/`、專案目錄、`tmp/` 是連結或不是目錄時拒絕處理(記警告,不刪任何東西);各層以 handle 開啟,
//! 列舉之後被換成連結也不會刪到外面(`rmtree` 重新以不跟隨方式確認型別)。`rmtree` 中途失敗(例:Windows 上回收項目內
//! 有保留裝置名)時錯誤往上傳、已刪部分不還原(與 Python 相同;「### 呼叫端指引」:啟動時的呼叫端不得因此中止)。
//!
//! **Windows 上的回收**(代決 14、行為差異 D-9):先照 Python 的順序在專案鎖內改名;Windows 上自己持有的專案目錄與
//! 鎖檔 handle 會讓改名失敗(`PermissionDenied` 或其他作業系統錯誤),此時關閉這兩個 handle、**在鎖外以同一個回收名稱
//! 重試一次**,仍失敗回 [`IN_USE`]。改名是原子的,不留部分狀態。本模組不含平台條件編譯:macOS / Linux 第一次就成功。
//!
//! 警告以回傳值交呼叫端([`Cleanup::warnings`],行為差異 D-10);非 UTF-8 的名稱在警告與 [`Cleanup::trash_removed`]
//! 中以替代字元顯示(D-11),刪除時以原始名稱操作(代決 16)。

use std::ffi::OsStr;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use safe_fs::{DirOptions, EntryKind, SafeDir};
use studio_core::names;

use crate::checks::check_name;
use crate::constants::{LINK_TRIES, MAX_ASSET_BYTES, TRASH_DAYS, TRASH_NAME};
use crate::ids::Clock;
use crate::lock::ProjectLock;
use crate::read::{
    self, NOT_FOUND, RESERVED_DEVICE, Subdir, child, child_in, project_in, root_dir,
};
use crate::write::format_ms;
use crate::{Error, ErrorKind, Result};

/// `trash` 的 `confirm` 不是 `true`(Python `confirm is not True`)。
pub const CONFIRM_REQUIRED: &str = "刪除專案需要確認(confirm: true)";
/// 回收名稱撞名重試 [`LINK_TRIES`] 次仍失敗。
pub const NO_UNIQUE_TRASH: &str = "無法建立不重複的回收名稱";
/// 鎖內改名與鎖外重試都因其他程式持有專案而失敗(代決 14、行為差異 D-9;Python 不在 Windows 執行)。
pub const IN_USE: &str = "專案正在使用中,請關閉其他程式後再試";
/// 回收時間戳超出 1000 年至 9999 年(行為差異 D-5 T7 補記;Python `_stamp` 丟 `OverflowError` 或寫出三位數年份)。
pub const TRASH_STAMP_OUT_OF_RANGE: &str = "回收時間戳超出可記錄的範圍(1000 年至 9999 年),拒絕回收";
/// 素材與自錄音檔合計超過上限(Python `QuotaExceeded`;`MAX_ASSET_BYTES / 1024 ** 3` 以 `:g` 顯示為 `2`)。
pub const QUOTA_EXCEEDED: &str = "素材與自錄音檔合計超過 2 GB 上限,無法再加入";
/// `projects/` 內名稱不合規則(含非 UTF-8)的項目(Python `startup_cleanup`)。
pub const BAD_PROJECT_NAME: &str = "projects 目錄內有名稱不合規則的項目,略過";

/// 建立 `.trash/` 的選項(Python `_child(rfd, TRASH_NAME, TRASH_NAME, create=True)`)。
const CREATE: DirOptions = DirOptions {
    create: true,
    mode: 0o755,
};

/// [`startup_cleanup`] 的結果(Python `{"trash_removed", "tmp_cleared", "warnings"}`)。
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Cleanup {
    /// 刪除的回收項目名稱(依名稱的碼位順序;非 UTF-8 以替代字元顯示,D-11)。
    pub trash_removed: Vec<String>,
    /// 清除的 `tmp/` 項目數(全部專案合計)。
    pub tmp_cleared: u64,
    /// 警告(Python `log.warning`;D-10)。
    pub warnings: Vec<String>,
}

fn is_fs_not_found(e: &Error) -> bool {
    e.kind() == ErrorKind::Fs(safe_fs::ErrorKind::NotFound)
}

fn not_found() -> Error {
    Error::new(ErrorKind::NotFound, vec![NOT_FOUND.to_string()])
}

// ---------- 回收 ----------

/// Windows 共用違規一類的改名失敗(代決 14:`PermissionDenied` 或其他作業系統錯誤)。
fn rename_blocked(e: &safe_fs::Error) -> bool {
    matches!(
        e.kind(),
        safe_fs::ErrorKind::PermissionDenied | safe_fs::ErrorKind::Other(_)
    )
}

/// Python `trash(name, confirm=…)`:把專案移到 `projects/.trash/<名稱>-<時間戳>-<4 碼>/`,回傳回收名稱。
///
/// `confirm` 必須為 `true`([`CONFIRM_REQUIRED`],先於名稱檢查);不覆蓋既有項目——回收名稱已存在(含目錄、檔案、
/// 連結)時換時間戳與隨機碼重試,最多 [`LINK_TRIES`] 次([`NO_UNIQUE_TRASH`])。時間戳與隨機碼取自 `clock`(每次嘗試
/// 各取一個 `now_ms` 與一個 `rand4`,與 Python 相同)。順序與 Python 相同:`projects/`(不存在 → `NotFound`)→ 專案目錄
/// (只差大小寫、不存在 → `NotFound`;連結 → `UnsafePath`)→ `.trash/`(不存在就建立;連結或不是目錄 → `UnsafePath`)
/// → 專案鎖 → 改名(`safe-fs` `rename`,相對 `projects/` 與 `.trash/` 的 handle,不經路徑)。
///
/// Windows 上鎖內改名失敗時關閉專案目錄與鎖檔 handle、在鎖外以同一個名稱重試一次(重試前再確認名稱仍未被使用),
/// 仍失敗回 [`IN_USE`](見模組說明與行為差異 D-9)。
pub fn trash(root: &Path, name: &str, confirm: bool, clock: &dyn Clock) -> Result<String> {
    if !confirm {
        return Err(Error::invalid(CONFIRM_REQUIRED));
    }
    let name = check_name(name)?;
    let projects = match root_dir(root) {
        Ok(d) => d,
        Err(e) if is_fs_not_found(&e) => return Err(not_found()),
        Err(e) => return Err(e),
    };
    let project = project_in(&projects, name)?;
    let trash_dir = child_in(&projects, TRASH_NAME, TRASH_NAME, CREATE)?;
    let lock = ProjectLock::acquire(&project)?;
    for _ in 0..LINK_TRIES {
        let stamp =
            format_ms(clock.now_ms()).map_err(|_| Error::invalid(TRASH_STAMP_OUT_OF_RANGE))?;
        let dest = format!("{name}-{stamp}-{}", clock.rand4());
        if trash_dir.lstat(&dest)?.is_some() {
            continue;
        }
        return match projects.rename(name, &trash_dir, &dest) {
            Ok(()) => Ok(dest),
            Err(e) if rename_blocked(&e) => {
                // 代決 14:自己的專案目錄與鎖檔 handle 會擋住 Windows 上的改名——關閉後在鎖外重試一次
                drop(lock);
                drop(project);
                if trash_dir.lstat(&dest)?.is_some() {
                    return Err(Error::invalid(NO_UNIQUE_TRASH));
                }
                match projects.rename(name, &trash_dir, &dest) {
                    Ok(()) => Ok(dest),
                    Err(e) if rename_blocked(&e) => Err(Error::invalid(IN_USE)),
                    Err(e) => Err(e.into()),
                }
            }
            Err(e) => Err(e.into()),
        };
    }
    Err(Error::invalid(NO_UNIQUE_TRASH))
}

// ---------- 回收區的時間戳 ----------

/// Unicode 14.0.0(CPython 3.11 的 `unicodedata`)十進位數字(`Nd`,`str.isdecimal()`)每一組 0~9 的 0 的碼位。
/// Unicode 規定十進位數字一律以 0~9 連續編碼,所以 66 組 × 10 = 660 個碼位即全部(施工時以 CPython 3.11.18 確認;
/// Python `re` 的 `\d`(無 `re.ASCII`)與 `int()` 認得的數字正是這些)。
pub const DECIMAL_ZEROS: [u32; 66] = [
    0x30, 0x660, 0x6f0, 0x7c0, 0x966, 0x9e6, 0xa66, 0xae6, 0xb66, 0xbe6, 0xc66, 0xce6, 0xd66,
    0xde6, 0xe50, 0xed0, 0xf20, 0x1040, 0x1090, 0x17e0, 0x1810, 0x1946, 0x19d0, 0x1a80, 0x1a90,
    0x1b50, 0x1bb0, 0x1c40, 0x1c50, 0xa620, 0xa8d0, 0xa900, 0xa9d0, 0xa9f0, 0xaa50, 0xabf0, 0xff10,
    0x104a0, 0x10d30, 0x11066, 0x110f0, 0x11136, 0x111d0, 0x112f0, 0x11450, 0x114d0, 0x11650,
    0x116c0, 0x11730, 0x118e0, 0x11950, 0x11c50, 0x11d50, 0x11da0, 0x16a60, 0x16ac0, 0x16b50,
    0x1d7ce, 0x1d7d8, 0x1d7e2, 0x1d7ec, 0x1d7f6, 0x1e140, 0x1e2f0, 0x1e950, 0x1fbf0,
];

/// Python `re` 的 `\d`(無 `re.ASCII`)與 `int()`:十進位數字的值;不是十進位數字回 `None`。
pub fn decimal_value(c: char) -> Option<u32> {
    let cp = u32::from(c);
    DECIMAL_ZEROS
        .iter()
        .find(|&&z| (z..z + 10).contains(&cp))
        .map(|&z| cp - z)
}

fn is_digit(c: char) -> bool {
    decimal_value(c).is_some()
}

/// `TRASH_RE.fullmatch(name)` 的第 2 組(時間戳 `\d{8}-\d{6}-\d{3}`,19 個字元);不符回 `None`。
///
/// 樣式 `(.+)-(\d{8}-\d{6}-\d{3})-[0-9a-f]{4}`:後段固定 25 個字元,所以時間戳是倒數第 24~5 個字元;前段至少一個
/// 字元且不含換行(`.` 不含 `\n`);`\d` 為 Unicode 十進位數字(沒有 `re.ASCII`)、`[0-9a-f]` 只有 ASCII。
pub fn trash_stamp(name: &str) -> Option<String> {
    let c: Vec<char> = name.chars().collect();
    let n = c.len();
    if n < 26 || c[..n - 25].contains(&'\n') {
        return None;
    }
    let tail = &c[n - 25..];
    let digits = |r: std::ops::Range<usize>| tail[r].iter().all(|&ch| is_digit(ch));
    let ok = tail[0] == '-'
        && digits(1..9)
        && tail[9] == '-'
        && digits(10..16)
        && tail[16] == '-'
        && digits(17..20)
        && tail[20] == '-'
        && tail[21..]
            .iter()
            .all(|ch| matches!(ch, '0'..='9' | 'a'..='f'));
    ok.then(|| tail[1..20].iter().collect())
}

/// CPython 3.11 `_strptime` 的一個欄位:`(樣式, 長度)` 依優先順序;`A` 為 ASCII 範圍、`D` 為 `\d`。
#[derive(Clone, Copy)]
enum Atom {
    Ascii(char, char),
    Digit,
}

/// `%m`、`%d`、`%H`、`%M`、`%S` 的候選(依 `_strptime.TimeRE` 的交替順序;`%d` 的 ` [1-9]` 與 3.13 起 `%H` 的 ` \d`
/// 需要空白,這裡的輸入只有數字與 `-`,不會符合,省略)。
const MONTH: &[&[Atom]] = &[
    &[Atom::Ascii('1', '1'), Atom::Ascii('0', '2')],
    &[Atom::Ascii('0', '0'), Atom::Ascii('1', '9')],
    &[Atom::Ascii('1', '9')],
];
const DAY: &[&[Atom]] = &[
    &[Atom::Ascii('3', '3'), Atom::Ascii('0', '1')],
    &[Atom::Ascii('1', '2'), Atom::Digit],
    &[Atom::Ascii('0', '0'), Atom::Ascii('1', '9')],
    &[Atom::Ascii('1', '9')],
];
const HOUR: &[&[Atom]] = &[
    &[Atom::Ascii('2', '2'), Atom::Ascii('0', '3')],
    &[Atom::Ascii('0', '1'), Atom::Digit],
    &[Atom::Digit],
];
const MINUTE: &[&[Atom]] = &[&[Atom::Ascii('0', '5'), Atom::Digit], &[Atom::Digit]];
const SECOND: &[&[Atom]] = &[
    &[Atom::Ascii('6', '6'), Atom::Ascii('0', '1')],
    &[Atom::Ascii('0', '5'), Atom::Digit],
    &[Atom::Digit],
];

fn atom_ok(a: Atom, c: char) -> bool {
    match a {
        Atom::Ascii(lo, hi) => (lo..=hi).contains(&c),
        Atom::Digit => is_digit(c),
    }
}

/// 欄位的數值(Python `int(found_dict[...])`)。
fn value_of(c: &[char]) -> Option<i64> {
    c.iter()
        .try_fold(0i64, |n, &ch| Some(n * 10 + i64::from(decimal_value(ch)?)))
}

/// `%Y` 之後的各段(依格式字串的順序)。
#[derive(Clone, Copy)]
enum Step {
    /// 一個欄位:候選(依交替順序)與它在結果陣列中的位置。
    Field(&'static [&'static [Atom]], usize),
    /// 字面的 `-`。
    Dash,
}

const STEPS: [Step; 6] = [
    Step::Field(MONTH, 1),
    Step::Field(DAY, 2),
    Step::Dash,
    Step::Field(HOUR, 3),
    Step::Field(MINUTE, 4),
    Step::Field(SECOND, 5),
];

/// 自第 `step` 段、位置 `i` 起比對,依交替順序回溯,回傳第一個成功組合的結尾位置。
fn walk(c: &[char], i: usize, step: usize, vals: &mut [i64; 6]) -> Option<usize> {
    let Some(&s) = STEPS.get(step) else {
        return Some(i);
    };
    match s {
        Step::Dash => {
            if c.get(i) == Some(&'-') {
                walk(c, i + 1, step + 1, vals)
            } else {
                None
            }
        }
        Step::Field(alts, k) => {
            for alt in alts {
                let end = i + alt.len();
                if end <= c.len() && alt.iter().zip(&c[i..end]).all(|(&a, &ch)| atom_ok(a, ch)) {
                    vals[k] = value_of(&c[i..end])?;
                    if let Some(e) = walk(c, end, step + 1, vals) {
                        return Some(e);
                    }
                }
            }
            None
        }
    }
}

/// `%Y%m%d-%H%M%S` 的樣式比對,依正規表示式的回溯順序找**第一個**成功的組合(`re.match` 不錨定結尾),
/// 回傳 `(結尾位置, [年, 月, 日, 時, 分, 秒])`。
fn strptime_match(c: &[char]) -> Option<(usize, [i64; 6])> {
    if c.len() < 4 || !c[..4].iter().all(|&ch| is_digit(ch)) {
        return None;
    }
    let mut vals = [value_of(&c[..4])?, 0, 0, 0, 0, 0];
    let end = walk(c, 4, 0, &mut vals)?;
    Some((end, vals))
}

const SECS_PER_DAY: i64 = 86_400;

/// 公曆日期 → UNIX 紀元起的日數(`days_from_civil`)。
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

fn days_in_month(y: i64, m: i64) -> i64 {
    match m {
        2 if (y % 4 == 0 && y % 100 != 0) || y % 400 == 0 => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    }
}

/// Python `_parse_stamp(s)`:`datetime.strptime(s[:15], "%Y%m%d-%H%M%S")`(UTC)的 `timestamp()` 加
/// `int(s[16:19]) / 1000`(浮點秒數,算式與 Python 相同);`strptime` 或 `int` 會丟 `ValueError` 的回 `None`。
///
/// 與 CPython 3.11 相同:`re.match` 取第一個成功的交替組合,沒有用完 15 個字元即無效(「unconverted data remains」);
/// `\d` 為 Unicode 十進位數字,但 `%m` 與各欄位的第一位(`%Y` 與單獨的 `\d` 除外)只認 ASCII;年 0001~9999、
/// 日依月份與閏年、時 00~23、分秒 00~59(`60`、`61` 符合 `%S` 的樣式但 `datetime` 拒絕)。毫秒段為 3 個十進位數字。
pub fn parse_stamp(s: &str) -> Option<f64> {
    let c: Vec<char> = s.chars().collect();
    let head = c.get(..15)?;
    let (end, [y, mo, d, h, mi, sec]) = strptime_match(head)?;
    if end != head.len()
        || !(1..=9999).contains(&y)
        || !(1..=12).contains(&mo)
        || !(1..=days_in_month(y, mo)).contains(&d)
        || h > 23
        || mi > 59
        || sec > 59
    {
        return None;
    }
    let ms = value_of(c.get(16..19)?)?;
    let secs = days_from_civil(y, mo, d) * SECS_PER_DAY + (h * 60 + mi) * 60 + sec;
    Some(secs as f64 + ms as f64 / 1000.0)
}

/// Python `datetime.timestamp()`(帶時區):UNIX 紀元起的秒數,`微秒 / 10**6` 的正確捨入(微秒以下捨去——Python
/// `datetime` 只有微秒;早於紀元以 floor 取微秒)。以十進位字串解析得到正確捨入(與 Python 整數真除法相同)。
pub fn timestamp_seconds(now: SystemTime) -> f64 {
    let micros: i128 = match now.duration_since(UNIX_EPOCH) {
        Ok(d) => (d.as_nanos() / 1000) as i128,
        Err(e) => -(e.duration().as_nanos().div_ceil(1000) as i128),
    };
    let (sign, abs) = if micros < 0 {
        ("-", micros.unsigned_abs())
    } else {
        ("", micros.unsigned_abs())
    };
    format!("{sign}{}.{:06}", abs / 1_000_000, abs % 1_000_000)
        .parse::<f64>()
        .unwrap_or(f64::NAN)
}

// ---------- 啟動清理 ----------

fn display(name: &OsStr) -> String {
    name.to_string_lossy().into_owned()
}

/// Python `_clean_trash(rfd, now_ts, result)`。
fn clean_trash(projects: &SafeDir, now_ts: f64, result: &mut Cleanup) -> Result<()> {
    let trash_dir = match child(projects, TRASH_NAME, TRASH_NAME) {
        Ok(d) => d,
        Err(e) if is_fs_not_found(&e) => return Ok(()),
        Err(e) if e.kind() == ErrorKind::UnsafePath => {
            let reason = e.errors().first().cloned().unwrap_or_default();
            result
                .warnings
                .push(format!("回收區不安全,拒絕清除:{reason}"));
            return Ok(());
        }
        Err(e) => return Err(e),
    };
    let limit = (TRASH_DAYS * 86_400) as f64;
    for entry in trash_dir.entry_names()? {
        let Some(meta) = trash_dir.lstat(&entry)? else {
            continue;
        };
        let shown = display(&entry);
        if meta.kind == EntryKind::Symlink {
            result
                .warnings
                .push(format!("回收區項目「{shown}」是符號連結,不跟隨、不刪除"));
            continue;
        }
        let Some(ts) = trash_stamp(&shown).and_then(|s| parse_stamp(&s)) else {
            result
                .warnings
                .push(format!("回收區項目「{shown}」名稱不含時間戳,略過"));
            continue;
        };
        if now_ts - ts >= limit {
            // 以 handle 遞迴;內部連結只刪連結本身(列舉後被換成連結時也只刪連結本身)
            trash_dir.rmtree(&entry, true)?;
            result.trash_removed.push(shown);
        }
    }
    Ok(())
}

/// Python `_clean_project(rfd, name, result)`。
fn clean_project(projects: &SafeDir, name: &str, result: &mut Cleanup) -> Result<()> {
    let project = match child(projects, name, "專案") {
        Ok(d) => d,
        Err(e) if e.kind() == ErrorKind::UnsafePath || is_fs_not_found(&e) => {
            result.warnings.push(format!(
                "專案目錄「{name}」是符號連結或不在預期位置,拒絕處理"
            ));
            return Ok(());
        }
        Err(e) if e.errors() == [RESERVED_DEVICE] => {
            // 行為差異 D-8 T7 補記:Windows 上名稱為保留裝置名的既有專案無法開啟,略過並記警告
            result.warnings.push(format!(
                "專案「{name}」是 Windows 保留裝置名,無法在 Windows 上清理,略過"
            ));
            return Ok(());
        }
        Err(e) => return Err(e),
    };
    for sub in ["history", "audio", "assets"] {
        if let Some(m) = project.lstat(sub)?
            && m.kind == EntryKind::Symlink
        {
            result
                .warnings
                .push(format!("專案「{name}」的 {sub} 目錄是符號連結,拒絕處理"));
        }
    }
    let tmp = match child(&project, "tmp", "tmp") {
        Ok(d) => d,
        Err(e) if is_fs_not_found(&e) => return Ok(()),
        Err(e) if e.kind() == ErrorKind::UnsafePath => {
            result.warnings.push(format!(
                "專案「{name}」的 tmp 目錄是符號連結或不在預期位置,拒絕清空"
            ));
            return Ok(());
        }
        Err(e) => return Err(e),
    };
    drop(project);
    for entry in tmp.entry_names()? {
        if tmp.lstat(&entry)?.is_none() {
            continue;
        }
        tmp.rmtree(&entry, true)?;
        result.tmp_cleared += 1;
    }
    Ok(())
}

/// Python `startup_cleanup(now)`:清除 `projects/.trash/` 中滿 30 天的項目、清空各專案 `tmp/` 的內容。
///
/// `now` 為注入的現在時刻(Python 收 `datetime` 或秒數,行為差異 D-12 T7 補記);回收項目的年齡以名稱內的時間戳(UTC)
/// 判定,不看檔案時間。選取規則見模組說明。`root` 或 `projects/` 不存在時回空的結果、不建立任何東西;`projects/` 是連結
/// 或不是目錄時記警告並回傳。`projects/` 內點開頭的項目(含 `.trash/`)不當成專案;名稱不合規則(含非 UTF-8,代決 16)
/// 記警告並略過。`rmtree` 或列舉失敗時回 `Err`,已刪部分不還原(「### 呼叫端指引」)。
pub fn startup_cleanup(root: &Path, now: SystemTime) -> Result<Cleanup> {
    let now_ts = timestamp_seconds(now);
    let mut result = Cleanup::default();
    let projects = match root_dir(root) {
        Ok(d) => d,
        Err(e) if is_fs_not_found(&e) => return Ok(result),
        Err(e) if e.kind() == ErrorKind::UnsafePath => {
            let reason = e.errors().first().cloned().unwrap_or_default();
            result
                .warnings
                .push(format!("projects 目錄不安全,拒絕清理:{reason}"));
            return Ok(result);
        }
        Err(e) => return Err(e),
    };
    clean_trash(&projects, now_ts, &mut result)?;
    for entry in projects.entry_names()? {
        match projects.lstat(&entry) {
            Ok(None) => continue,
            Ok(Some(_)) => {}
            // Windows 的保留裝置名無法取得中繼資料:交給 clean_project 記警告(D-8 T7 補記)
            Err(e) if e.kind() == safe_fs::ErrorKind::ReservedDevice => {}
            Err(e) => return Err(e.into()),
        }
        if display(&entry).starts_with('.') {
            continue;
        }
        let Some(name) = entry
            .to_str()
            .filter(|n| names::normalize_name(n).is_some())
        else {
            result.warnings.push(BAD_PROJECT_NAME.to_string());
            continue;
        };
        clean_project(&projects, name, &mut result)?;
    }
    Ok(result)
}

// ---------- 子目錄的單一檔案與配額 ----------

/// Python `unlink_in(name, sub, filename)`:刪除專案子目錄內的單一項目(不跟隨連結,只刪連結本身;不存在不算錯)。
///
/// 與 Python 相同,子目錄開不了時**不算錯、什麼都不做**:名稱不合規則、專案不存在或只差大小寫、任一層是連結或不是目錄
/// (Python `ProjectError` 一類)、子目錄不存在(`FileNotFoundError`)。其他作業系統錯誤與刪除的錯誤(例:`filename`
/// 是目錄 → `Fs(IsADirectory)`、名稱不合法 → `Fs(InvalidName)`,Python 為 `ValueError`,D-12 T7 補記)回 `Err`。
pub fn unlink_in(root: &Path, name: &str, sub: Subdir, filename: &str) -> Result<()> {
    let dir = match read::open_subdir(root, name, sub) {
        Ok(d) => d,
        Err(e) if !matches!(e.kind(), ErrorKind::Fs(_)) || is_fs_not_found(&e) => return Ok(()),
        Err(e) => return Err(e),
    };
    dir.unlink(filename, true)?;
    Ok(())
}

/// Python `check_quota(name, incoming)`:目前的 [`read::disk_usage`](`assets/` 與 `audio/` 內一般檔的合計)加上
/// `incoming` 位元組超過 [`MAX_ASSET_BYTES`] 時回 `Quota`([`QUOTA_EXCEEDED`]);剛好等於上限不算超過。
/// `disk_usage` 的錯誤原樣回傳。呼叫端須在專案鎖內、複製前呼叫(Python 相同)。
pub fn check_quota(root: &Path, name: &str, incoming: u64) -> Result<()> {
    let used = read::disk_usage(root, name)?;
    match used.checked_add(incoming) {
        Some(total) if total <= MAX_ASSET_BYTES => Ok(()),
        _ => Err(Error::new(
            ErrorKind::Quota,
            vec![QUOTA_EXCEEDED.to_string()],
        )),
    }
}
