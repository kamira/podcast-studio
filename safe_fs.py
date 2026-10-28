"""安全路徑工具(CHG-20261022-04 DIR-5 修正 4):以 dir_fd 逐層操作,杜絕「先以路徑檢查、再以路徑操作」的競態。

只用標準庫。所有操作都從一個受信根目錄(呼叫端給的 repo 內固定目錄,例 `project.ROOT`)出發:

  - 中間各層一律以 `os.open(名稱, O_RDONLY | O_NOFOLLOW | O_DIRECTORY, dir_fd=上一層)` 開啟;
    任一層是符號連結或不是目錄 → 丟 UnsafePathError(OSError 子類別),不存在 → FileNotFoundError。
  - 最後一層的檔案以 `O_NOFOLLOW` 開啟後 `fstat` 確認是一般檔;之後的讀寫一律用這個 fd,不再以路徑重開。
  - 刪除、搬移、建立連結都以 `dir_fd` 對「單一名稱」操作(unlink / rename / link 本身不跟隨最後一層)。
  - 刪除目錄樹以 fd 遞迴走訪;遇到符號連結只刪連結本身,不跟隨。

名稱(name / parts 的每一段)必須是單一路徑元件:不可為空、`.`、`..`,不可含 `/` 或 NUL,否則丟 ValueError。
訊息不含本機路徑,可直接記錄。

介面:
  open_dir(top, parts=(), *, create=False, mode=0o755) -> fd    受信根目錄 top 起逐層開到 parts 的目錄
  open_child(dirfd, name, *, create=False, mode=0o755) -> fd    開啟(或建立)dirfd 內的單一子目錄
  open_file(dirfd, name, flags=os.O_RDONLY) -> fd               開啟 dirfd 內的一般檔(O_NOFOLLOW + fstat)
  open_path(top, parts, flags=os.O_RDONLY) -> fd                open_dir(top, parts[:-1]) + open_file
  create_excl(dirfd, name, mode=0o600, flags=os.O_WRONLY) -> fd 排他建立(O_CREAT | O_EXCL | O_NOFOLLOW)
  open_or_create(dirfd, name, flags=os.O_RDWR, mode=0o600) -> fd 開啟或排他建立一般檔(鎖檔用)
  lstat(dirfd, name) -> os.stat_result | None                   不跟隨;不存在回 None
  entries(dirfd) -> [(name, lstat 結果)]                        os.scandir(fd) 列舉,依名稱排序
  unlink(dirfd, name, missing_ok=False)
  rmtree(dirfd, name, missing_ok=False)                         以 fd 遞迴刪除;符號連結只刪連結本身
  rename(src_dirfd, src, dst_dirfd, dst) / link(src_dirfd, src, dst_dirfd, dst)
  closing(fd)                                                   with 區塊結束時關閉 fd
"""
from __future__ import annotations

import contextlib
import errno
import os
import stat

_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_LINK_ERRNOS = {errno.ELOOP, errno.ENOTDIR, getattr(errno, "EMLINK", errno.ELOOP)}


class UnsafePathError(OSError):
    """路徑中某一層是符號連結、不是預期的目錄,或目標不是一般檔。"""


def _check_name(name) -> str:
    if (not isinstance(name, str) or not name or name in (".", "..") or "/" in name or "\x00" in name):
        raise ValueError("路徑元件不合法")
    return name


@contextlib.contextmanager
def closing(fd: int):
    try:
        yield fd
    finally:
        os.close(fd)


def _open_subdir(dirfd: int, name: str) -> int:
    try:
        return os.open(name, os.O_RDONLY | _NOFOLLOW | _DIRECTORY | _CLOEXEC, dir_fd=dirfd)
    except FileNotFoundError:
        raise
    except OSError as e:
        if e.errno in _LINK_ERRNOS:
            raise UnsafePathError(f"「{name}」是符號連結或不是目錄,不接受") from None
        raise


def open_dir(top, parts=(), *, create: bool = False, mode: int = 0o755) -> int:
    """自受信根目錄 top 起逐層開啟 parts(皆為目錄),回傳最後一層的 fd(呼叫端負責關閉)。

    create=True 時不存在的層以 mkdir(dir_fd) 建立後再以 O_NOFOLLOW 開啟;建立與開啟之間被換成
    符號連結也會在開啟時被拒絕。top 本身視為受信(呼叫端給的固定目錄),以 O_DIRECTORY 開啟。"""
    parts = [_check_name(p) for p in parts]
    fd = os.open(os.fspath(top), os.O_RDONLY | _DIRECTORY | _CLOEXEC)
    try:
        for part in parts:
            nxt = open_child(fd, part, create=create, mode=mode)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def open_child(dirfd: int, name: str, *, create: bool = False, mode: int = 0o755) -> int:
    """開啟 dirfd 內名為 name 的子目錄(O_NOFOLLOW | O_DIRECTORY);create=True 時不存在就先建立。"""
    _check_name(name)
    try:
        return _open_subdir(dirfd, name)
    except FileNotFoundError:
        if not create:
            raise FileNotFoundError(errno.ENOENT, f"「{name}」不存在") from None
    with contextlib.suppress(FileExistsError):
        os.mkdir(name, mode, dir_fd=dirfd)
    return _open_subdir(dirfd, name)


def open_file(dirfd: int, name: str, flags: int = os.O_RDONLY) -> int:
    """開啟 dirfd 內名為 name 的一般檔:O_NOFOLLOW 開啟後 fstat 確認;不是一般檔丟 UnsafePathError。"""
    _check_name(name)
    try:
        fd = os.open(name, flags | _NOFOLLOW | _CLOEXEC, dir_fd=dirfd)
    except FileNotFoundError:
        raise
    except OSError as e:
        if e.errno in _LINK_ERRNOS:
            raise UnsafePathError(f"「{name}」是符號連結,不接受") from None
        raise
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise UnsafePathError(f"「{name}」不是一般檔案")
    return fd


def open_path(top, parts, flags: int = os.O_RDONLY) -> int:
    """open_dir(top, parts[:-1]) 後以 open_file 開啟最後一段;回傳檔案 fd。"""
    parts = list(parts)
    if not parts:
        raise ValueError("路徑元件不合法")
    with closing(open_dir(top, parts[:-1])) as d:
        return open_file(d, parts[-1], flags)


def create_excl(dirfd: int, name: str, mode: int = 0o600, flags: int = os.O_WRONLY) -> int:
    """在 dirfd 內排他建立 name(已存在——含符號連結——丟 FileExistsError,不跟隨)。"""
    _check_name(name)
    return os.open(name, flags | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC, mode, dir_fd=dirfd)


def open_or_create(dirfd: int, name: str, flags: int = os.O_RDWR, mode: int = 0o600, tries: int = 100) -> int:
    """開啟 dirfd 內的一般檔,不存在就排他建立(鎖檔用);一律 O_NOFOLLOW,不是一般檔丟 UnsafePathError。

    不用「O_CREAT 不帶 O_EXCL」:macOS 上多個行程以 openat(O_CREAT | O_NOFOLLOW) 同時建立同一檔時
    實測偶發 ENOENT;改為「先開、不存在再 O_EXCL 建立、撞到就重開」的迴圈。"""
    _check_name(name)
    for _ in range(tries):
        try:
            return open_file(dirfd, name, flags)
        except FileNotFoundError:
            pass
        try:
            fd = create_excl(dirfd, name, mode, flags)
        except FileExistsError:
            continue
        if not stat.S_ISREG(os.fstat(fd).st_mode):  # pragma: no cover - 剛排他建立的必為一般檔
            os.close(fd)
            raise UnsafePathError(f"「{name}」不是一般檔案")
        return fd
    raise FileExistsError(errno.EEXIST, f"「{name}」無法開啟或建立")


def lstat(dirfd: int, name: str):
    _check_name(name)
    try:
        return os.stat(name, dir_fd=dirfd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def entries(dirfd: int) -> list:
    """列舉 dirfd 的項目:[(名稱, lstat 結果)],依名稱排序;列舉途中消失的項目略過。"""
    out = []
    with os.scandir(dirfd) as it:
        names = [e.name for e in it]
    for n in sorted(names):
        st = lstat(dirfd, n)
        if st is not None:
            out.append((n, st))
    return out


def unlink(dirfd: int, name: str, missing_ok: bool = False) -> None:
    _check_name(name)
    try:
        os.unlink(name, dir_fd=dirfd)
    except FileNotFoundError:
        if not missing_ok:
            raise


def rmtree(dirfd: int, name: str, missing_ok: bool = False) -> None:
    """刪除 dirfd 內的 name:目錄以 fd 遞迴刪除,其他(含符號連結)只 unlink 本身,一律不跟隨。"""
    st = lstat(dirfd, name)
    if st is None:
        if missing_ok:
            return
        raise FileNotFoundError(errno.ENOENT, f"「{name}」不存在")
    if not stat.S_ISDIR(st.st_mode):
        unlink(dirfd, name, missing_ok=True)
        return
    try:
        sub = _open_subdir(dirfd, name)
    except UnsafePathError:   # lstat 之後被換成符號連結:只刪連結本身
        unlink(dirfd, name, missing_ok=True)
        return
    except FileNotFoundError:
        return
    with closing(sub):
        for child, _ in entries(sub):
            rmtree(sub, child, missing_ok=True)
    os.rmdir(name, dir_fd=dirfd)


def rename(src_dirfd: int, src: str, dst_dirfd: int, dst: str) -> None:
    os.rename(_check_name(src), _check_name(dst), src_dir_fd=src_dirfd, dst_dir_fd=dst_dirfd)


def replace(src_dirfd: int, src: str, dst_dirfd: int, dst: str) -> None:
    os.replace(_check_name(src), _check_name(dst), src_dir_fd=src_dirfd, dst_dir_fd=dst_dirfd)


def link(src_dirfd: int, src: str, dst_dirfd: int, dst: str) -> None:
    """建立硬連結(目標已存在——含符號連結——丟 FileExistsError;來源是符號連結時不跟隨)。"""
    os.link(_check_name(src), _check_name(dst), src_dir_fd=src_dirfd, dst_dir_fd=dst_dirfd,
            follow_symlinks=False)
