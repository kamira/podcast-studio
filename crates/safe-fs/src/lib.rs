//! 安全路徑層:`safe_fs.py` 的 Rust 版(CHG-20261125-01,路線圖第 3 筆)。
//!
//! 所有操作自一個受信根目錄出發,以 cap-std 的能力式目錄 handle 逐層進行:
//!
//! - 每次只對**單一名稱**、相對上一層已開啟的 handle 操作;名稱先經 [`check_name`]。
//! - 中間各層以「不跟隨連結」的方式開啟,再以**同一個 handle** 的中繼資料確認是目錄;
//!   是連結(Windows:帶名稱代理位元的重新解析點,含符號連結、junction、WSL 連結)或不是目錄
//!   → [`ErrorKind::Unsafe`];不存在 → [`ErrorKind::NotFound`]。指向根目錄內部的連結也拒絕。
//! - 最後一層的檔案以不跟隨連結的方式開啟,再以 handle 確認是一般檔;之後讀寫一律用回傳的
//!   [`std::fs::File`],不再以路徑重開。
//! - 錯誤訊息不含本機路徑(見 [`Error`])。
//!
//! 平台差異只在本 crate(代決 3):Windows 的名稱另加嚴(代決 8)、保留裝置名明確失敗(代決 9)。
//! Windows 上 cap-std 的開啟以 `NtCreateFile` 相對目錄 handle 進行(不經 DOS 路徑轉換),
//! 但**變更類操作**(建立目錄、刪除檔案與目錄、改名、硬連結)與列舉目錄,由 handle 取回完整路徑後
//! 串接名稱再呼叫標準函式庫(經 Win32 路徑轉換;開工閘紀錄見本 CHG 派發記錄,D-12)——所以這些操作的
//! 名稱安全完全依賴 [`check_name`] 的 Windows 加嚴與保留裝置名的事前拒絕,兩者都在呼叫 cap-std **之前**檢查。
//!
//! 介面:[`check_name`]、[`open_dir`]、[`open_path`];[`SafeDir`] 的 `open_child`、`open_file`、
//! `entry_names`(T1)與 `create_excl`、`open_or_create`、`lstat`、`entries`、`unlink`、`rmtree`、
//! `rename`、`replace`、`hard_link`(T2);比對工具與測試用的 [`support`]。
#![forbid(unsafe_code)]

mod error;
mod meta;
mod name;
pub mod support;

use std::ffi::{OsStr, OsString};
use std::fmt;
use std::fs::File;
use std::io;
use std::path::Path;

use cap_fs_ext::{DirExt, FollowSymlinks, OpenOptionsFollowExt};
use cap_std::fs::{Dir, OpenOptions};

pub use error::{Error, ErrorKind, Result, errno_name};
pub use meta::{EntryKind, Meta};
pub use name::{check_name, reserved_device};

use name::check_openable;

/// `open_or_create` 的重試次數(與 Python `tries=100` 相同)。
const OPEN_OR_CREATE_TRIES: usize = 100;

/// `rmtree` 同時保持開啟的目錄 handle 上限(D-2):超過時關閉較淺層的 handle,回到該層時
/// 自最近仍開著的上層逐層以不跟隨方式重新開啟。macOS 預設每個行程只能開 256 個檔案,
/// 若每層都保持開啟,1500 層的目錄樹會在開啟時失敗;64 層留下足夠餘裕給同一行程的其他 handle。
const RMTREE_MAX_OPEN: usize = 64;

/// Unix 的 `O_NONBLOCK`(D-1:開啟 FIFO 等特殊檔時不阻塞;一般檔不受影響)。
#[cfg(all(
    any(target_os = "linux", target_os = "android"),
    not(any(target_arch = "mips", target_arch = "mips64", target_arch = "sparc64"))
))]
const O_NONBLOCK: i32 = 0o4000;
#[cfg(all(
    any(target_os = "linux", target_os = "android"),
    any(target_arch = "mips", target_arch = "mips64")
))]
const O_NONBLOCK: i32 = 0x80;
#[cfg(all(
    any(target_os = "linux", target_os = "android"),
    target_arch = "sparc64"
))]
const O_NONBLOCK: i32 = 0x4000;
#[cfg(all(unix, not(any(target_os = "linux", target_os = "android"))))]
const O_NONBLOCK: i32 = 0x0004;

/// 開啟目錄的選項(Python `create=`、`mode=`)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DirOptions {
    /// 不存在的層先建立再開啟。
    pub create: bool,
    /// 建立目錄的權限位元(只在 Unix 生效,受 umask 影響;Windows 忽略,繼承上層 ACL,D-8)。
    pub mode: u32,
}

impl Default for DirOptions {
    fn default() -> Self {
        Self {
            create: false,
            mode: 0o755,
        }
    }
}

/// 開啟檔案的存取方式(取代 Python 的 `flags` 整數;呼叫端只用到唯讀、唯寫、讀寫與唯寫截斷)。
///
/// 建立檔案([`SafeDir::create_excl`]、[`SafeDir::open_or_create`])須帶寫入(`Write` 或 `ReadWrite`);
/// 以 `Read` 建立時回 `Other`(標準函式庫不允許只讀建立)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FileAccess {
    Read,
    Write { truncate: bool },
    ReadWrite { truncate: bool },
}

impl FileAccess {
    fn writes(self) -> bool {
        !matches!(self, FileAccess::Read)
    }

    /// 不跟隨連結;Unix 另加 `O_NONBLOCK`(D-1)。
    fn options(self) -> OpenOptions {
        let mut o = OpenOptions::new();
        match self {
            FileAccess::Read => {
                o.read(true);
            }
            FileAccess::Write { truncate } => {
                o.write(true).truncate(truncate);
            }
            FileAccess::ReadWrite { truncate } => {
                o.read(true).write(true).truncate(truncate);
            }
        }
        o.follow(FollowSymlinks::No);
        #[cfg(unix)]
        {
            use cap_std::fs::OpenOptionsExt;
            o.custom_flags(O_NONBLOCK);
        }
        o
    }
}

/// 開啟時要的型別(Windows 決定錯誤類別用;Unix 依 errno 判定,不需要)。
#[derive(Debug, Clone, Copy)]
#[cfg_attr(unix, allow(dead_code))]
enum Want {
    Dir,
    File { writes: bool },
}

/// 變更類操作(Windows 由錯誤碼改回與 Linux 相同的類別用,D-7)。
#[derive(Debug, Clone, Copy)]
#[cfg_attr(unix, allow(dead_code))]
enum Op {
    Create,
    Unlink,
    RemoveDir,
}

/// 已開啟的目錄 handle;`Drop` 時關閉。之後的開啟、建立、刪除、搬移都相對它進行。
pub struct SafeDir {
    dir: Dir,
}

impl fmt::Debug for SafeDir {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("SafeDir { .. }")
    }
}

/// 自受信根目錄 `top` 起逐層開啟 `parts`(皆為目錄),回傳最後一層的 handle。
///
/// **`top` 必須是呼叫端給的受信目錄**——來自程式內固定設定(例:應用程式資料目錄下的
/// `projects/`),**不得來自使用者輸入**;`top` 本身會**跟隨連結**(視為受信,與 Python 相同),
/// 只有 `parts` 的每一層才拒絕連結。
///
/// 先檢查全部 `parts` 的名稱(任一不合法即 `InvalidName`;Windows 上任一為保留裝置名即
/// `ReservedDevice`),不開任何東西;`top` 不存在 `NotFound`、是一般檔 `NotADirectory`。
/// `opts.create` 時不存在的層先建立再以不跟隨方式開啟;已是一般檔或連結(含懸空連結)者 `Unsafe`。
pub fn open_dir<S: AsRef<OsStr>>(top: &Path, parts: &[S], opts: DirOptions) -> Result<SafeDir> {
    for p in parts {
        check_openable(p.as_ref())?;
    }
    let dir = Dir::open_ambient_dir(top, cap_std::ambient_authority()).map_err(error::from_io)?;
    let mut cur = SafeDir { dir };
    for p in parts {
        cur = cur.open_child(p, opts)?;
    }
    Ok(cur)
}

/// `open_dir(top, parts[..n-1])` 後以 [`SafeDir::open_file`] 開啟最後一段;`parts` 為空 `InvalidName`。
pub fn open_path<S: AsRef<OsStr>>(top: &Path, parts: &[S], access: FileAccess) -> Result<File> {
    let Some((last, dirs)) = parts.split_last() else {
        return Err(Error::new(ErrorKind::InvalidName));
    };
    open_dir(top, dirs, DirOptions::default())?.open_file(last, access)
}

/// `rmtree` 堆疊的一層:目錄名稱、(可能已關閉的)handle、尚未處理的子項(反向排序,`pop` 取最小)。
struct Frame {
    name: OsString,
    dir: Option<SafeDir>,
    pending: Vec<OsString>,
}

impl SafeDir {
    /// 開啟本目錄內名為 `name` 的子目錄(不跟隨連結,以 handle 確認是目錄);
    /// `opts.create` 時不存在就先建立(已存在不算錯誤)再開啟。
    pub fn open_child(&self, name: impl AsRef<OsStr>, opts: DirOptions) -> Result<SafeDir> {
        let name = name.as_ref();
        check_openable(name)?;
        match self.open_subdir(name) {
            Err(e) if e.kind() == ErrorKind::NotFound && opts.create => {}
            other => return other,
        }
        match self.mkdir(name, opts.mode) {
            Ok(()) => {}
            Err(e) if e.kind() == io::ErrorKind::AlreadyExists => {}
            Err(e) => return Err(error::from_io(e)),
        }
        self.open_subdir(name)
    }

    /// 開啟本目錄內名為 `name` 的一般檔(不跟隨連結;開啟後以 handle 確認是一般檔,否則 `Unsafe`)。
    /// 檔案不存在時 `NotFound`(不建立)。Unix 上以 `O_NONBLOCK` 開啟,FIFO 不會阻塞而回 `Unsafe`(D-1)。
    pub fn open_file(&self, name: impl AsRef<OsStr>, access: FileAccess) -> Result<File> {
        let name = name.as_ref();
        check_openable(name)?;
        match self.dir.open_with(name, &access.options()) {
            Ok(f) => regular_file(f),
            Err(e) => Err(self.open_error(
                name,
                e,
                Want::File {
                    writes: access.writes(),
                },
            )),
        }
    }

    /// 在本目錄內排他建立一般檔 `name`(Python `create_excl`):名稱已存在——含目錄、連結與懸空連結——
    /// 回 `AlreadyExists`,不跟隨、不覆蓋。`mode` 為 Unix 權限位元(受 umask 影響;Windows 忽略,D-8)。
    pub fn create_excl(
        &self,
        name: impl AsRef<OsStr>,
        access: FileAccess,
        mode: u32,
    ) -> Result<File> {
        let name = name.as_ref();
        check_openable(name)?;
        let mut o = access.options();
        o.create_new(true);
        #[cfg(unix)]
        {
            use cap_std::fs::OpenOptionsExt;
            o.mode(mode);
        }
        #[cfg(not(unix))]
        let _ = mode;
        match self.dir.open_with(name, &o) {
            Ok(f) => regular_file(f),
            Err(e) => Err(self.mutate_error(name, e, Op::Create)),
        }
    }

    /// 開啟本目錄內的一般檔,不存在就排他建立(鎖檔用;Python `open_or_create`):
    /// 「先開、不存在再排他建立、撞到已存在就重開」最多 100 次,用盡回 `AlreadyExists`。
    /// 一律不跟隨連結;不是一般檔回 `Unsafe`。
    pub fn open_or_create(
        &self,
        name: impl AsRef<OsStr>,
        access: FileAccess,
        mode: u32,
    ) -> Result<File> {
        let name = name.as_ref();
        check_openable(name)?;
        for _ in 0..OPEN_OR_CREATE_TRIES {
            match self.open_file(name, access) {
                Err(e) if e.kind() == ErrorKind::NotFound => {}
                other => return other,
            }
            match self.create_excl(name, access, mode) {
                Err(e) if e.kind() == ErrorKind::AlreadyExists => {}
                other => return other,
            }
        }
        Err(Error::new(ErrorKind::AlreadyExists))
    }

    /// 不跟隨連結取得 `name` 的中繼資料;不存在回 `None`(Python `lstat`)。
    pub fn lstat(&self, name: impl AsRef<OsStr>) -> Result<Option<Meta>> {
        let name = name.as_ref();
        check_openable(name)?;
        match self.dir.symlink_metadata(name) {
            Ok(m) => Meta::from_cap(&m).map(Some),
            Err(e) => {
                let e = error::from_io(e);
                if e.kind() == ErrorKind::NotFound {
                    Ok(None)
                } else {
                    Err(e)
                }
            }
        }
    }

    /// 列舉本目錄的名稱(不含 `.`、`..`),依碼位排序(與 Python `sorted(os.listdir(fd))` 相同)。
    pub fn entry_names(&self) -> Result<Vec<OsString>> {
        let mut names = Vec::new();
        for entry in self.dir.entries().map_err(error::from_io)? {
            names.push(entry.map_err(error::from_io)?.file_name());
        }
        names.sort();
        Ok(names)
    }

    /// 列舉本目錄的項目與不跟隨連結的中繼資料,依名稱的碼位排序;列舉途中消失的項目略過
    /// (Python `entries`)。Windows 上名稱不合格(D-4)或是保留裝置名(D-5)的項目使整個列舉失敗。
    pub fn entries(&self) -> Result<Vec<(OsString, Meta)>> {
        let mut out = Vec::new();
        for name in self.entry_names()? {
            if let Some(meta) = self.lstat(&name)? {
                out.push((name, meta));
            }
        }
        Ok(out)
    }

    /// 刪除本目錄內的 `name`(一般檔或連結本身,不跟隨;Python `unlink`)。不存在時依 `missing_ok`
    /// 回傳或 `NotFound`;是目錄時依平台錯誤(Linux `IsADirectory`、macOS `PermissionDenied`;
    /// Windows 改回與 Linux 相同的 `IsADirectory`,D-7)。
    pub fn unlink(&self, name: impl AsRef<OsStr>, missing_ok: bool) -> Result<()> {
        let name = name.as_ref();
        check_openable(name)?;
        match self.dir.remove_file_or_symlink(name) {
            Ok(()) => Ok(()),
            Err(e) => {
                let e = self.mutate_error(name, e, Op::Unlink);
                if missing_ok && e.kind() == ErrorKind::NotFound {
                    Ok(())
                } else {
                    Err(e)
                }
            }
        }
    }

    /// 刪除本目錄內的 `name`(Python `rmtree`):目錄以 handle 逐層刪除其內容後刪除本身,
    /// 其他(含連結)只刪項目本身,一律不跟隨。不存在時依 `missing_ok` 回傳或 `NotFound`。
    ///
    /// 以明確的堆疊迭代,不受呼叫堆疊深度限制(D-2);同時開啟的目錄 handle 不超過 64 個,超過時關閉
    /// 較淺層的 handle,回到該層時自最近仍開著的上層逐層以不跟隨方式重新開啟(每層仍拒絕連結)。
    /// 刪除子目錄前一定先關閉它的 handle(Windows 上開著的目錄 handle 不允許刪除該目錄,K-2)。
    /// 子項名稱在 Windows 上不合格或是保留裝置名時,以 `InvalidName` / `ReservedDevice` 失敗(D-4、D-5)。
    pub fn rmtree(&self, name: impl AsRef<OsStr>, missing_ok: bool) -> Result<()> {
        let name = name.as_ref();
        check_openable(name)?;
        let sub = match self.lstat(name)? {
            None if missing_ok => return Ok(()),
            None => return Err(Error::new(ErrorKind::NotFound)),
            Some(m) if m.kind != EntryKind::Dir => return self.unlink(name, true),
            Some(_) => match self.open_subdir(name) {
                Ok(d) => d,
                // lstat 之後被換成連結:只刪連結本身
                Err(e) if e.kind() == ErrorKind::Unsafe => return self.unlink(name, true),
                Err(e) if e.kind() == ErrorKind::NotFound => return Ok(()),
                Err(e) => return Err(e),
            },
        };
        let mut stack = vec![Frame {
            name: name.to_os_string(),
            pending: pending_names(&sub)?,
            dir: Some(sub),
        }];
        while let Some(depth) = stack.len().checked_sub(1) {
            if stack[depth].dir.is_none() {
                self.reopen(&mut stack, depth)?;
            }
            if let Some(child) = stack[depth].pending.pop() {
                let Some(dir) = stack[depth].dir.as_ref() else {
                    return Err(Error::new(ErrorKind::Other(None)));
                };
                if let Some(sub) = dir.remove_or_open_child(&child)? {
                    let pending = pending_names(&sub)?;
                    stack.push(Frame {
                        name: child,
                        dir: Some(sub),
                        pending,
                    });
                    limit_open(&mut stack);
                }
                continue;
            }
            // 本層已清空:先關閉本層 handle,再由上一層(或 self)刪除本層
            let Some(done) = stack.pop() else { break };
            drop(done.dir);
            let parent = match stack.last_mut() {
                None => self,
                Some(frame) => {
                    if frame.dir.is_none() {
                        let d = stack.len() - 1;
                        self.reopen(&mut stack, d)?;
                    }
                    match stack.last().and_then(|f| f.dir.as_ref()) {
                        Some(dir) => dir,
                        None => return Err(Error::new(ErrorKind::Other(None))),
                    }
                }
            };
            parent.remove_dir(&done.name)?;
        }
        Ok(())
    }

    /// 把本目錄內的 `src` 改名為 `dst_dir` 內的 `dst`(Python `rename`):兩個名稱都先檢查;
    /// 目標是既有的一般檔時覆蓋、目錄改名到既有的空目錄時取代(與 POSIX `rename` 相同;Windows 由
    /// 標準函式庫以 POSIX 語意的取代方式進行,實測結果相同,D-9)。連結只改名連結本身,不跟隨。
    /// 目錄改名到既有的一般檔或連結回 `NotADirectory`(Windows 上事先檢查,否則會以目錄取代該檔案)。
    pub fn rename(
        &self,
        src: impl AsRef<OsStr>,
        dst_dir: &SafeDir,
        dst: impl AsRef<OsStr>,
    ) -> Result<()> {
        let (src, dst) = (src.as_ref(), dst.as_ref());
        check_openable(src)?;
        check_openable(dst)?;
        // Windows 的改名會以目錄取代既有的一般檔或連結(實測;Linux 為 ENOTDIR),先擋下(D-9)
        #[cfg(not(unix))]
        if let (Some(s), Some(d)) = (self.lstat(src)?, dst_dir.lstat(dst)?)
            && s.kind == EntryKind::Dir
            && d.kind != EntryKind::Dir
        {
            return Err(Error::new(ErrorKind::NotADirectory));
        }
        self.dir
            .rename(src, &dst_dir.dir, dst)
            .map_err(|e| self.rename_error(src, dst_dir, dst, e))
    }

    /// Python `replace`:與 [`SafeDir::rename`] 相同(POSIX 上 `os.rename` 與 `os.replace` 都覆蓋既有目標;
    /// Windows 上 cap-std 的改名也以取代方式進行,D-9)。
    pub fn replace(
        &self,
        src: impl AsRef<OsStr>,
        dst_dir: &SafeDir,
        dst: impl AsRef<OsStr>,
    ) -> Result<()> {
        self.rename(src, dst_dir, dst)
    }

    /// 為本目錄內的 `src` 在 `dst_dir` 內建立硬連結 `dst`(Python `link`):目標已存在——含連結——
    /// 回 `AlreadyExists`。Unix 上來源是符號連結時不跟隨(連結到符號連結本身,與 Python
    /// `follow_symlinks=False` 相同);**Windows 上來源是連結(符號連結、junction)時回 `Unsafe`**
    /// (`CreateHardLinkW` 是否跟隨連結不由呼叫端控制,D-3、C-4)。
    pub fn hard_link(
        &self,
        src: impl AsRef<OsStr>,
        dst_dir: &SafeDir,
        dst: impl AsRef<OsStr>,
    ) -> Result<()> {
        let (src, dst) = (src.as_ref(), dst.as_ref());
        check_openable(src)?;
        check_openable(dst)?;
        #[cfg(not(unix))]
        if let Some(m) = self.lstat(src)?
            && m.kind == EntryKind::Symlink
        {
            return Err(Error::new(ErrorKind::Unsafe));
        }
        self.dir
            .hard_link(src, &dst_dir.dir, dst)
            .map_err(|e| self.link_error(src, dst_dir, dst, e))
    }

    fn open_subdir(&self, name: &OsStr) -> Result<SafeDir> {
        match self.dir.open_dir_nofollow(name) {
            Ok(dir) => {
                let meta = dir.dir_metadata().map_err(error::from_io)?;
                if meta.is_dir() && !meta.file_type().is_symlink() {
                    Ok(SafeDir { dir })
                } else {
                    Err(Error::new(ErrorKind::Unsafe))
                }
            }
            Err(e) => Err(self.open_error(name, e, Want::Dir)),
        }
    }

    /// `rmtree` 對單一子項:不存在略過;不是目錄(含連結)刪除本身;是目錄回傳開啟的 handle
    /// (呼叫端先清空再刪除);`lstat` 之後被換成連結時只刪連結本身(與 Python 相同)。
    fn remove_or_open_child(&self, name: &OsStr) -> Result<Option<SafeDir>> {
        check_openable(name)?;
        match self.lstat(name)? {
            None => Ok(None),
            Some(m) if m.kind != EntryKind::Dir => self.unlink(name, true).map(|()| None),
            Some(_) => match self.open_subdir(name) {
                Ok(d) => Ok(Some(d)),
                Err(e) if e.kind() == ErrorKind::Unsafe => self.unlink(name, true).map(|()| None),
                Err(e) if e.kind() == ErrorKind::NotFound => Ok(None),
                Err(e) => Err(e),
            },
        }
    }

    /// 刪除本目錄內已清空的子目錄 `name`(名稱已檢查)。
    fn remove_dir(&self, name: &OsStr) -> Result<()> {
        self.dir
            .remove_dir(name)
            .map_err(|e| self.mutate_error(name, e, Op::RemoveDir))
    }

    /// 重新開啟 `stack[k]` 的 handle:自最近仍開著的上層(或 `self`)逐層以不跟隨方式開啟,
    /// 只保留最深的 `RMTREE_MAX_OPEN` 層。
    fn reopen(&self, stack: &mut [Frame], k: usize) -> Result<()> {
        let first = stack[..k]
            .iter()
            .rposition(|f| f.dir.is_some())
            .map_or(0, |j| j + 1);
        let mut transient: Option<SafeDir> = None;
        for i in first..=k {
            let opened = {
                let parent = match &transient {
                    Some(t) => t,
                    None if i == 0 => self,
                    None => match stack[i - 1].dir.as_ref() {
                        Some(d) => d,
                        None => return Err(Error::new(ErrorKind::Other(None))),
                    },
                };
                parent.open_subdir(&stack[i].name)?
            };
            if i + RMTREE_MAX_OPEN > k {
                stack[i].dir = Some(opened);
                transient = None;
            } else {
                transient = Some(opened);
            }
        }
        Ok(())
    }

    #[cfg(unix)]
    fn mkdir(&self, name: &OsStr, mode: u32) -> io::Result<()> {
        use cap_std::fs::{DirBuilder, DirBuilderExt};
        let mut b = DirBuilder::new();
        b.mode(mode);
        self.dir.create_dir_with(name, &b)
    }

    #[cfg(not(unix))]
    fn mkdir(&self, name: &OsStr, _mode: u32) -> io::Result<()> {
        self.dir.create_dir(name)
    }

    /// Unix:與 Python 相同,依 errno 判定——`ELOOP`、`ENOTDIR`、`EMLINK`(不跟隨連結時遇到連結
    /// 或不是目錄)為 `Unsafe`;`ENXIO`(以 `O_NONBLOCK` 寫入方式開啟沒有讀取端的 FIFO,D-1)時
    /// 以不跟隨方式確認不是一般檔即 `Unsafe`;其餘依 PEP 3151 的對應。
    #[cfg(unix)]
    fn open_error(&self, name: &OsStr, e: io::Error, _want: Want) -> Error {
        match e.raw_os_error() {
            Some(error::ELOOP | error::EMLINK | error::ERRNO_ENOTDIR) => {
                Error::with_source(ErrorKind::Unsafe, e)
            }
            Some(error::ENXIO) => match self.dir.symlink_metadata(name) {
                Ok(m) if !m.is_file() => Error::with_source(ErrorKind::Unsafe, e),
                _ => error::from_io(e),
            },
            _ => error::from_io(e),
        }
    }

    /// Windows:錯誤碼與 Unix 不同(例:開啟目錄當檔案只給「拒絕存取」、開啟懸空 junction 給
    /// 「找不到」),所以再以不跟隨方式取得該名稱的型別,改回與 Linux 相同的類別:連結或非一般檔為
    /// `Unsafe`,以寫入方式開啟目錄為 `IsADirectory`(D-7)。名稱不存在時照原錯誤。
    #[cfg(not(unix))]
    fn open_error(&self, name: &OsStr, e: io::Error, want: Want) -> Error {
        let Ok(m) = self.dir.symlink_metadata(name) else {
            return error::from_io(e);
        };
        let kind = if m.file_type().is_symlink() {
            ErrorKind::Unsafe
        } else {
            match want {
                Want::Dir if !m.is_dir() => ErrorKind::Unsafe,
                Want::File { writes: true } if m.is_dir() => ErrorKind::IsADirectory,
                Want::File { .. } if !m.is_file() => ErrorKind::Unsafe,
                _ => return error::from_io(e),
            }
        };
        Error::with_source(kind, e)
    }

    #[cfg(unix)]
    fn mutate_error(&self, _name: &OsStr, e: io::Error, _op: Op) -> Error {
        error::from_io(e)
    }

    /// Windows 的變更類錯誤改回與 Linux 相同的類別(D-7):「拒絕存取」時以不跟隨方式查該名稱——
    /// 排他建立遇到既有項目為 `AlreadyExists`(Linux `EEXIST`)、`unlink` 遇到目錄為 `IsADirectory`
    /// (Linux `EISDIR`)、刪除目錄遇到非空目錄為 `os_ENOTEMPTY` 的對應(Windows 本身已回 145)。
    #[cfg(not(unix))]
    fn mutate_error(&self, name: &OsStr, e: io::Error, op: Op) -> Error {
        if error::classify(&e) != ErrorKind::PermissionDenied {
            return error::from_io(e);
        }
        let Ok(m) = self.dir.symlink_metadata(name) else {
            return error::from_io(e);
        };
        let kind = match op {
            Op::Create => ErrorKind::AlreadyExists,
            Op::Unlink if m.is_dir() && !m.file_type().is_symlink() => ErrorKind::IsADirectory,
            _ => return error::from_io(e),
        };
        Error::with_source(kind, e)
    }

    #[cfg(unix)]
    fn rename_error(&self, _src: &OsStr, _dst_dir: &SafeDir, _dst: &OsStr, e: io::Error) -> Error {
        error::from_io(e)
    }

    /// Windows 的改名錯誤改回與 Linux 相同的類別(D-7、D-9):「拒絕存取」時以不跟隨方式查兩端——
    /// 一般檔改名到既有目錄為 `IsADirectory`(Linux `EISDIR`)、目錄改名到既有一般檔為 `NotADirectory`
    /// (Linux `ENOTDIR`)。
    #[cfg(not(unix))]
    fn rename_error(&self, src: &OsStr, dst_dir: &SafeDir, dst: &OsStr, e: io::Error) -> Error {
        if error::classify(&e) != ErrorKind::PermissionDenied {
            return error::from_io(e);
        }
        let (Ok(s), Ok(d)) = (
            self.dir.symlink_metadata(src),
            dst_dir.dir.symlink_metadata(dst),
        ) else {
            return error::from_io(e);
        };
        let real_dir = |m: &cap_std::fs::Metadata| m.is_dir() && !m.file_type().is_symlink();
        let kind = match (real_dir(&s), real_dir(&d)) {
            (false, true) => ErrorKind::IsADirectory,
            (true, false) => ErrorKind::NotADirectory,
            _ => return error::from_io(e),
        };
        Error::with_source(kind, e)
    }

    #[cfg(unix)]
    fn link_error(&self, _src: &OsStr, _dst_dir: &SafeDir, _dst: &OsStr, e: io::Error) -> Error {
        error::from_io(e)
    }

    /// Windows 的硬連結錯誤:目標已存在時 Win32 給「已存在」,與 Linux 相同;「拒絕存取」且來源是目錄時
    /// 照原樣(`PermissionDenied`,與 Linux `EPERM` 相同)。
    #[cfg(not(unix))]
    fn link_error(&self, _src: &OsStr, dst_dir: &SafeDir, dst: &OsStr, e: io::Error) -> Error {
        if error::classify(&e) == ErrorKind::PermissionDenied
            && dst_dir.dir.symlink_metadata(dst).is_ok()
        {
            return Error::with_source(ErrorKind::AlreadyExists, e);
        }
        error::from_io(e)
    }
}

/// 開啟後以 handle 確認是一般檔(不是連結、目錄、FIFO 或裝置),否則 `Unsafe`。
fn regular_file(f: cap_std::fs::File) -> Result<File> {
    let f = f.into_std();
    let meta = f.metadata().map_err(error::from_io)?;
    if meta.is_file() && !meta.file_type().is_symlink() {
        Ok(f)
    } else {
        Err(Error::new(ErrorKind::Unsafe))
    }
}

/// 目錄的子項名稱,反向排序(`pop` 依碼位由小到大取出)。
fn pending_names(dir: &SafeDir) -> Result<Vec<OsString>> {
    let mut names = dir.entry_names()?;
    names.reverse();
    Ok(names)
}

/// 開啟中的 handle 超過上限時,關閉最淺層的一個(開啟中的 handle 一律是堆疊最深的連續幾層)。
fn limit_open(stack: &mut [Frame]) {
    let open = stack.iter().filter(|f| f.dir.is_some()).count();
    if open > RMTREE_MAX_OPEN
        && let Some(f) = stack.iter_mut().find(|f| f.dir.is_some())
    {
        f.dir = None;
    }
}
