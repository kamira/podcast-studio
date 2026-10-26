"""跨行程重型工作鎖(CHG-20261022-01 T4,ADP-2):網頁與 MCP 共用同一把。

24 GB 統一記憶體同時只撐得住一個重型工作(LLM、生圖、語音、影片),所以兩個行程的
重型工作一律在 `heavy_lock()` 內執行。

介面:
  LOCK_PATH                         鎖檔,repo 內的 `outputs/.heavy.lock`
  heavy_lock(timeout=None, on_wait=None)
                                    context manager;以 `fcntl.flock(LOCK_EX)` 取得排他鎖。
                                    鎖已被占用時先呼叫一次 `on_wait()`(例如在節點上顯示
                                    「等待其他工作」),再每 POLL_SECONDS 秒重試一次。
                                    timeout 為 None 時一直等;為秒數時逾時丟 TimeoutError。

行為說明:
  - flock 綁在開啟的檔案描述上,持鎖行程結束(含被 kill)時由作業系統自動釋放,不留死鎖。
  - 同一行程內不同執行緒各自開檔,也彼此互斥;同一執行緒不可巢狀取鎖(會等到自己,形同死結)。
  - 建立 `outputs/` 前後都檢查它不是符號連結、解析後等於 repo 內的 `outputs/`,不符丟 OSError
    且不建立任何東西;鎖檔以 O_NOFOLLOW 開啟,`outputs/.heavy.lock` 是符號連結時丟 OSError。
  - 鎖檔只用來上鎖,內容一律不讀不寫,也不刪除(刪除會讓兩個行程各鎖到不同的檔)。
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCK_PATH = ROOT / "outputs" / ".heavy.lock"
POLL_SECONDS = 0.05


def _try_lock(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _check_outputs(outputs: Path) -> None:
    """outputs/ 不得是符號連結,且解析後必須等於 repo 內的 outputs/;不符丟 OSError。"""
    expected = Path(ROOT).resolve() / "outputs"
    if outputs.is_symlink():
        raise OSError(f"重型工作鎖目錄「{outputs}」是符號連結,不接受")
    try:
        resolved = outputs.resolve()
    except (OSError, RuntimeError):
        raise OSError(f"重型工作鎖目錄「{outputs}」無法解析") from None
    if resolved != expected:
        raise OSError(f"重型工作鎖目錄必須是 {expected}(實際為 {resolved})")


@contextlib.contextmanager
def heavy_lock(timeout=None, on_wait=None):
    """取得跨行程排他鎖;離開 with 區塊(含例外)時釋放。"""
    path = Path(LOCK_PATH)
    # 建立 outputs/ 前後都檢查(依 PR #37 審議修正):不經符號連結、不越出 repo;不符不建立任何東西
    _check_outputs(path.parent)
    path.parent.mkdir(exist_ok=True)
    _check_outputs(path.parent)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags, 0o600)
    try:
        if not _try_lock(fd):
            if on_wait is not None:
                on_wait()
            deadline = None if timeout is None else time.monotonic() + timeout
            while not _try_lock(fd):
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError(f"等待重型工作鎖超過 {timeout} 秒({path})")
                time.sleep(POLL_SECONDS)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
