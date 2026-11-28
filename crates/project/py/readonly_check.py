# -*- coding: utf-8 -*-
"""使用者 Mac 唯讀比對工具(CHG-20261128-01 代決 19、20;T4;退出條件 (2))。

用法(在 repo 根目錄;必須以 `-I -B` 啟動):

    cargo build --locked --offline --release -p project --bin project-readonly-dump
    python3 -I -B -X utf8 crates/project/py/readonly_check.py --repo . --rust target/release/project-readonly-dump

前置(確認閘 Q3,缺一就停下、不要執行):
  (1) 先確認 Time Machine 等備份可用;無法確認備份可用時停下,不執行本工具;
  (2) 比對期間關閉 Python 網頁(studio_server.py)與 MCP 伺服器(local_models_mcp.py)。

做什麼(只用標準函式庫,**不寫任何檔案、不送出本機**):
  1. 比對前走訪 `<repo>/projects/` 全部項目(不跟隨連結),在記憶體中記錄每個檔案的相對路徑、大小、修改時間與
     SHA-256、每個目錄與連結;
  2. 以 argv 清單啟動 Rust 的 `project-readonly-dump --root <repo>`,從 stdout 讀結果(只在記憶體中);
  3. 以 repo 內的 `project.py` 執行 `list_projects()`、每個專案 `load()`、`list_history()`、每個版本 `load_version()`,
     載入結果取 `_dump(validate(...))` 或錯誤類別與訊息——這四個函式只以唯讀方式開檔、不取專案鎖、不建立 `.lock`、
     `tmp/` 或 `history/`;Python 端執行期間另以「攔下寫入」的替身取代 `os.open` 的寫入旗標、`os.mkdir`、`os.rename`、
     `os.unlink`、`fcntl.flock` 等,任何寫入嘗試都會被擋下並計數;
  4. 逐筆比對兩邊的結果;
  5. 比對後再走訪一次,與比對前的清單逐項比較;
  6. 只印六行數量與「是 / 否」,**不印任何專案名稱、版本 id、雜湊值或內容**。

輸出(數字依資料而定):

    唯讀比對:專案 N 個(Python 列出 N、Rust 列出 N;清單一致:是)
    版本檔:M 個
    載入結果一致:N+M 筆;不一致:0 筆(其中兩邊都拒絕載入:Z 筆)
    不一致的序號:無
    檔案清單與雜湊:比對前後各 K 個檔案、J 個目錄;一致:是
    結論:通過

「不一致的序號」只列序號與兩邊的結果類別;檔案清單有任何變動、或 Python 端有任何寫入嘗試被擋下時,第五行為「一致:否」、
結論為「不通過」——此時**停下**,不要重跑、也不要自行還原,交使用者決定。exit code:通過 0、不通過 1、用法錯誤 2。
"""
from __future__ import annotations

import argparse
import builtins
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

PROJECTS_NAME = "projects"
CHUNK = 1 << 20
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND | getattr(os, "O_EXCL", 0)
RUST_TIMEOUT = 3600


# ---------- 檔案清單與雜湊(比對前後各一次;只在記憶體中) ----------

def _sha256(path: str) -> str:
    """以唯讀、不跟隨連結的方式開檔計算 SHA-256(一般檔才會呼叫)。"""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    h = hashlib.sha256()
    try:
        while True:
            b = os.read(fd, CHUNK)
            if not b:
                break
            h.update(b)
    finally:
        os.close(fd)
    return h.hexdigest()


def manifest(repo: Path) -> dict:
    """`<repo>/projects/` 的清單:{相對路徑: (型別, 大小, mtime_ns, SHA-256 或連結目標)};不跟隨連結。

    `projects/` 本身也列入(鍵為 `""`);不存在時為空清單。"""
    base = os.path.join(os.fspath(repo), PROJECTS_NAME)
    out = {}

    def record(path: str, rel: str) -> None:
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            out[rel] = ("link", st.st_size, st.st_mtime_ns, os.readlink(path))
        elif stat.S_ISDIR(st.st_mode):
            out[rel] = ("dir", 0, st.st_mtime_ns, None)
            with os.scandir(path) as it:
                children = sorted(e.name for e in it)
            for name in children:
                record(os.path.join(path, name), name if rel == "" else rel + "/" + name)
        elif stat.S_ISREG(st.st_mode):
            out[rel] = ("file", st.st_size, st.st_mtime_ns, _sha256(path))
        else:
            out[rel] = ("other", st.st_size, st.st_mtime_ns, None)

    if os.path.lexists(base):
        record(base, "")
    return out


def manifest_counts(m: dict) -> tuple[int, int]:
    """(檔案數, 目錄數);`projects/` 本身不算在目錄數內。"""
    files = sum(1 for k, v in m.items() if v[0] == "file")
    dirs = sum(1 for k, v in m.items() if v[0] == "dir" and k != "")
    return files, dirs


# ---------- 攔下寫入(Python 端執行期間) ----------

class WriteGuard:
    """以替身取代會寫入檔案系統的函式;任何寫入嘗試都擋下(丟 PermissionError)並計數。"""

    def __init__(self):
        self.attempts = 0
        self._saved = []

    def _block(self, *_args, **_kwargs):
        self.attempts += 1
        raise PermissionError("唯讀比對攔下寫入")

    def _patch(self, obj, attr, func):
        if hasattr(obj, attr):
            self._saved.append((obj, attr, getattr(obj, attr)))
            setattr(obj, attr, func)

    def install(self, fcntl_module=None):
        real_open = os.open
        real_builtin_open = builtins.open

        def guarded_open(path, flags, *args, **kwargs):
            if flags & WRITE_FLAGS:
                return self._block()
            return real_open(path, flags, *args, **kwargs)

        def guarded_builtin_open(file, mode="r", *args, **kwargs):
            if any(c in mode for c in "wax+"):
                return self._block()
            return real_builtin_open(file, mode, *args, **kwargs)

        self._patch(os, "open", guarded_open)
        self._patch(builtins, "open", guarded_builtin_open)
        for attr in ("mkdir", "makedirs", "rename", "renames", "replace", "remove", "unlink", "rmdir",
                     "removedirs", "link", "symlink", "utime", "chmod", "chown", "lchown", "truncate",
                     "ftruncate", "write", "fsync", "mkfifo", "mknod"):
            self._patch(os, attr, self._block)
        if fcntl_module is not None:
            for attr in ("flock", "lockf"):
                self._patch(fcntl_module, attr, self._block)
        return self

    def uninstall(self):
        while self._saved:
            obj, attr, func = self._saved.pop()
            setattr(obj, attr, func)


# ---------- 兩邊的結果 ----------

def _error_value(e, pj) -> dict:
    for cls, name in ((pj.UnsafePath, "unsafe_path"), (pj.ProjectNotFound, "not_found"),
                      (pj.ProjectExists, "exists"), (pj.QuotaExceeded, "quota")):
        if isinstance(e, cls):
            return {"class": name, "errors": list(e.errors)}
    return {"class": "project_error", "errors": list(e.errors)}


def _call(pj, fn, *args):
    """呼叫讀取函式;ProjectError → {"class", "errors"},OSError → {"class": "os_error"}。"""
    try:
        return {"ok": fn(*args)}
    except pj.ProjectError as e:
        return _error_value(e, pj)
    except OSError:
        return {"class": "os_error"}


def _loaded(pj, r) -> dict:
    if "ok" in r:
        return {"dump": pj._dump(pj.validate(r["ok"].to_dict())).decode("utf-8")}
    return r


def python_records(pj) -> list:
    """與 Rust `project-readonly-dump` 相同順序、相同形式的紀錄。"""
    listed = _call(pj, pj.list_projects)
    if "ok" not in listed:
        return [{"projects": listed}]
    names = [p["name"] for p in listed["ok"]]
    out = [{"projects": {"value": names}}]
    for name in names:
        out.append({"load": name, "result": _loaded(pj, _call(pj, pj.load, name))})
        hist = _call(pj, pj.list_history, name)
        if "ok" not in hist:
            out.append({"history": name, "result": hist})
            continue
        out.append({"history": name, "result": {"value": hist["ok"]}})
        for vid in hist["ok"]:
            out.append({"version": [name, vid], "result": _loaded(pj, _call(pj, pj.load_version, name, vid))})
    return out


def rust_records(rust: str, repo: Path):
    """啟動 Rust 執行檔(argv 清單、不經 shell),回傳紀錄清單;失敗回 None。"""
    try:
        proc = subprocess.run([rust, "--root", os.fspath(repo)], stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=RUST_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return [json.loads(line) for line in proc.stdout.decode("utf-8").split("\n") if line]
    except ValueError:
        return None


def _kind(result) -> str:
    if result is None:
        return "missing"
    if "dump" in result or "value" in result:
        return "ok"
    return str(result.get("class", "unknown"))


def compare(py: list, rs: list) -> dict:
    """逐筆比對。紀錄為:每個專案一筆(載入結果與版本清單都相同才算一致)、每個版本一筆;以 Python 的清單為準。"""
    py_names = py[0]["projects"].get("value") if py and "projects" in py[0] else None
    rs_names = rs[0]["projects"].get("value") if rs and "projects" in rs[0] else None
    index_rs = {}
    for r in rs[1:]:
        for key in ("load", "history"):
            if key in r:
                index_rs[(key, r[key])] = r["result"]
        if "version" in r:
            index_rs[("version", tuple(r["version"]))] = r["result"]
    hist_by_name = {x["history"]: x["result"] for x in py[1:] if "history" in x}
    rows = []   # (一致?, 兩邊都拒絕?, Python 類別, Rust 類別)
    versions = 0
    for r in py[1:]:
        if "load" in r:
            name = r["load"]
            hist_py = hist_by_name.get(name)
            hist_rs = index_rs.get(("history", name))
            load_rs = index_rs.get(("load", name))
            same = r["result"] == load_rs and hist_py == hist_rs
            both_reject = same and _kind(r["result"]) not in ("ok", "missing")
            rows.append((same, both_reject, _kind(r["result"]), _kind(load_rs)))
        elif "version" in r:
            versions += 1
            got = index_rs.get(("version", tuple(r["version"])))
            same = r["result"] == got
            both_reject = same and _kind(r["result"]) not in ("ok", "missing")
            rows.append((same, both_reject, _kind(r["result"]), _kind(got)))
    return {"py_names": py_names, "rs_names": rs_names, "rows": rows, "versions": versions,
            "list_same": py_names is not None and py_names == rs_names and py[0] == rs[0]}


def report(cmp: dict, before: dict, after: dict, attempts: int) -> list:
    """六行輸出(只有數量與是 / 否)。"""
    py_n = len(cmp["py_names"]) if cmp["py_names"] is not None else 0
    rs_n = len(cmp["rs_names"]) if cmp["rs_names"] is not None else 0
    rows = cmp["rows"]
    same = sum(1 for r in rows if r[0])
    diff = len(rows) - same
    both = sum(1 for r in rows if r[1])
    bad = [f"{i}({r[2]}/{r[3]})" for i, r in enumerate(rows, 1) if not r[0]]
    files, dirs = manifest_counts(before)
    unchanged = before == after and attempts == 0
    yes = lambda b: "是" if b else "否"  # noqa: E731
    ok = cmp["list_same"] and diff == 0 and unchanged
    return [
        f"唯讀比對:專案 {py_n} 個(Python 列出 {py_n}、Rust 列出 {rs_n};清單一致:{yes(cmp['list_same'])})",
        f"版本檔:{cmp['versions']} 個",
        f"載入結果一致:{same} 筆;不一致:{diff} 筆(其中兩邊都拒絕載入:{both} 筆)",
        f"不一致的序號:{'、'.join(bad) if bad else '無'}",
        f"檔案清單與雜湊:比對前後各 {files} 個檔案、{dirs} 個目錄;一致:{yes(unchanged)}",
        f"結論:{'通過' if ok else '不通過'}",
    ]


def import_project(repo: Path):
    """匯入 repo 內的 `project`(及其相依的產品模組),並核對來源都在 repo 根目錄。"""
    sys.path.append(os.fspath(repo))
    import project as pj  # noqa: PLC0415 — 依 --repo 決定匯入位置
    for mod in ("project", "names", "safe_fs", "value_checks", "breezy_worker"):
        f = Path(sys.modules[mod].__file__).resolve()
        if f.parent != repo:
            raise SystemExit(f"readonly_check:{mod} 不是來自 --repo 指定的目錄")
    return pj


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="唯讀比對 Python 與 Rust 載入既有專案的結果(只印數量;不寫任何檔案、不送出本機)。"
                    "前置:先確認備份可用,無法確認就停下、不要執行;比對期間關閉 Python 網頁與 MCP 伺服器。",
        epilog="必須以 python3 -I -B -X utf8 啟動。")
    parser.add_argument("--repo", required=True, help="放真實專案的 repo 根目錄(其下為 projects/ 與 project.py)")
    parser.add_argument("--rust", required=True, help="project-readonly-dump 執行檔的路徑")
    args = parser.parse_args(argv)
    if not (sys.flags.isolated and sys.flags.dont_write_bytecode):
        print("readonly_check:請以 python3 -I -B -X utf8 啟動(不寫 __pycache__、不讀環境設定)", file=sys.stderr)
        return 2
    sys.dont_write_bytecode = True
    repo = Path(args.repo).resolve()
    if not (repo / "project.py").is_file():
        print("readonly_check:--repo 必須是含 project.py 的 repo 根目錄", file=sys.stderr)
        return 2
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")

    before = manifest(repo)
    rs = rust_records(args.rust, repo)
    pj = import_project(repo)
    guard = WriteGuard().install(sys.modules.get("fcntl"))
    try:
        py = python_records(pj)
    finally:
        guard.uninstall()
    after = manifest(repo)

    if rs is None:
        rs = [{"projects": {"class": "rust_failed"}}]
    lines = report(compare(py, rs), before, after, guard.attempts)
    sys.stdout.write("\n".join(lines) + "\n")
    return 0 if lines[-1] == "結論:通過" else 1


def run(argv=None) -> int:
    """`main` 的外層:未預期的例外只印例外類別,不印追蹤訊息(追蹤訊息可能含專案名稱或路徑)。"""
    try:
        return main(argv)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 — 只印類別,不印細節
        print(f"readonly_check:執行失敗({type(e).__name__});為避免輸出專案名稱不印細節。請停下,不要重跑。",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(run())
