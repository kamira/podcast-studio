# -*- coding: utf-8 -*-
"""並行比對工具的 Python 轉接器(CHG-20261123-01 T2,代決 5、6;CHG-20261124-01 T1 起接上產品模組)。

用途:由 Rust 比對器 `parity` 以子程序啟動(argv 清單、不經 shell),
把同一組 fixture 案例交給 Python 端的單元,回傳結果供比對。

協定(JSON 行,UTF-8,一行一個 JSON 物件,換行一律 `\\n`):
- 啟動:`python adapter.py --unit <單元名>`;單元名不存在時在 stderr 印錯誤、exit 2。
- 請求(stdin):`{"id": …, "input": <任意 JSON>}`;容許 `\\r\\n` 行尾,空行略過。
- 回應(stdout,依請求順序):
  `{"id": …, "outcome": "ok", "value": <JSON>}`、
  `{"id": …, "outcome": "reject", "code": "<拒絕代碼>"}`、
  `{"id": …, "outcome": "error", "class": "<錯誤類別>"}`。
  `message` 等其他欄位只供人看,比對器不比對。
- 請求不是合法 JSON 物件或缺 `id` / `input` 時,在 stderr 印錯誤、exit 2。

拒絕代碼與錯誤類別由各單元在兩邊各自定義同一組字串;對應表寫在本檔的單元函式內,
並列入該模組 CHG 的契約清單。單元內未預期的例外記為 `error`,類別為例外類別名稱。

import 的範圍:標準函式庫,加上明列的產品模組 `names`、`value_checks`、`safe_fs`(CHG-20261124-01 代決 13、
CHG-20261125-01 代決 11;
`tests/test_parity_adapter.py` 以 AST 斷言集合以外的 import 會失敗)。產品模組以 repo 根目錄
(本檔往上三層)插入 `sys.path[0]` 後 import,執行的是 repo 內受版本控制的程式;
只 import 本身只用標準函式庫、不依賴 `fcntl` 等平台模組的產品模組,三平台都能載入。
產品模組單元:`value_checks.is_int`(CHG-20261124-01 T1)、`names.name_char_ok`、`names.normalize_name`、
`names.fold_name`、`names.constants`(T2);拒絕代碼 `invalid`、錯誤類別 `type`(代決 6)。
`safe_fs.*` 單元(CHG-20261125-01 代決 10):輸入 `{"tree": [項目…], "call": {參數}}`;**單元會在系統暫存目錄
建立唯一名稱的工作目錄(`tempfile.mkdtemp`),其下放 `top/`(受信根目錄,依 `tree` 建立合成檔案樹)與
`outside/`(固定的標記檔),呼叫 `safe_fs` 後取結果與快照,案例結束即刪除工作目錄**;不寫其他任何位置。
`safe_fs.*` 共 14 個單元:`check_name` 與開啟類四個(T1)、建立 / 查詢 / 刪除 / 搬移類九個(T2:`create_excl`、
`open_or_create`、`lstat`、`entries`、`unlink`、`rmtree`、`rename`、`replace`、`link`)。
`project.*` 單元(CHG-20261128-01 代決 10):`project.py` 頂層 import `fcntl`,在 Windows 上無法載入,
所以**不在檔頂 import**,只在 `project_*` / `_project_*` 函式內延後 `import project`(事實 H;
`tests/test_parity_adapter.py` 以 AST 斷言);Windows 上這些單元回報 import 失敗的錯誤類別,
其他單元不受影響。T1 六個單元:`project.constants`、`project.normalize_speed`、`project.normalize_pause`、
`project.check_zhuyin`、`project.check_pitch_points`、`project.py_json`;T2 加 `project.validate_text`(輸入 JSON 原文,
在自己以 `tempfile.mkdtemp` 建立的暫存目錄寫一個檔案,以 `_read_project_file` 讀回後 `validate`,接受時回
`{"dump": _dump 字串}`,案例結束即刪除;輸入另可寫成 `{"hex"}` 或精簡形式 `{"parts"}`,dump 有同一字元連續
65 個以上(T3 補記:原為 1000)時改回 `{"dump": {"parts": […]}}`,見 `_project_compact_dump`);結果一律以 `ok` 回傳——
接受時 `{"value", "repr"?}`(`repr` 為 `json.dumps(值, ensure_ascii=False, sort_keys=True)`,
連 `-0.0` 與整數 / 浮點的寫法一起比對),拒絕時 `{"class", "errors"}`(錯誤類別與逐字的錯誤訊息清單)。
T3 加純函式 11 個單元:`project.split_segments`(`{"text": 字串或 {"parts"}, "forced"?: [整數…]}`)、
`project.escape_text`(字串),以及片段類的 `project.tts_text`、`project.fused_out`、`project.model_units`、
`project.shift_marks`(另需 `old`、`new`)、`project.post_params`、`project.post_key`、`project.is_post_fresh`、
`project.is_default_post`、`project.effective`(`effective_speed` 與 `effective_pause`,另可給 `defaults`)——
文字欄位(`split_segments` 的 `text`、`escape_text` 的輸入、`segment.text`、`shift_marks` 的 `old` / `new`)可為字串或
`{"parts"}`(`_project_text_value`),接受時 `value` 內同一字元連續 65 個以上的字串改為 `{"parts"}`
(`_project_compact_strings`;與 Rust 同一規則);
片段類輸入 `{"segment"}`,先放進 `{"schema_version": 2, "name": "p", "segments": [segment]}` 以 `validate`
(`effective` 以 `Project.from_dict`)驗證,失敗回 `{"class", "errors"}`,通過才對驗證後的片段呼叫函式。
T4 加讀取 5 個單元:`project.load`、`project.load_version`、`project.list_history`、`project.list_projects`、
`project.disk_usage`——輸入 `{"tree", "outside"?, "name"?, "vid"?}`,在自己以 `tempfile.mkdtemp` 建立的工作目錄
(前綴 `parity-project-fs-py-`)下建立 `root/`(暫時設為 `project.ROOT`)與 `outside/` 的合成檔案樹,呼叫前後各取快照,
回 `{"result", "tree", "changed"}`(`changed` 為前後有任何不同的路徑,讀取不得改動任何東西);案例結束即刪除工作目錄、
還原 `project.ROOT`。
T6 加寫入 2 個單元:`project.save_sequence`(`{"tree", "outside"?, "clock", "ops"}`:依序執行 `save`、`update`、`restore`、
`create`、讀取與 `fs` 操作)與 `project.create`——同一種工作目錄;案例期間暫時把 `project.time`、`project.secrets` 換成
依序取用 `clock` 序列的替身(`now` / `rand4` / `hex8`,用完後重複最後一個)、在 `project` logger 掛上收集警告的 handler,
回每步結果與最後帶內容的樹;案例結束一律還原 `project.ROOT`、`project.time`、`project.secrets` 並刪除工作目錄。
"""
from __future__ import annotations

import argparse
import errno
import json
import logging
import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path

# 先 import 完標準函式庫,再把 repo 根目錄放到 sys.path 最前面,確保 import 到的是根目錄的產品模組。
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import names  # noqa: E402
import safe_fs  # noqa: E402
import value_checks  # noqa: E402


class Reject(Exception):
    """單元回報預期內的拒絕(對應 `outcome: reject`)。"""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class UnitError(Exception):
    """單元回報已分類的錯誤(對應 `outcome: error`,類別為 `cls`)。"""

    def __init__(self, cls):
        super().__init__(cls)
        self.cls = cls


# ---------- 示範單元(代決 7;只存在於比對工具內,不屬任何產品模組) ----------

DIGITS = "0123456789"


def _parse_with_max(value, maximum):
    # 規則與 crates/parity/src/demo.rs 逐字相同;只認 ASCII 數字(str.isdigit 會接受全形數字)。
    if not isinstance(value, str):
        raise UnitError("type")
    if value == "" or any(ch not in DIGITS for ch in value):
        raise Reject("not_digits")
    n = int(value)
    if n == 0 or n > maximum:
        raise Reject("out_of_range")
    return n


def demo_parse_count(value):
    """demo.parse_count:上限 1000(與 Rust 端相同)。"""
    return _parse_with_max(value, 1000)


def demo_parse_count_drift(value):
    """demo.parse_count_drift:Python 端上限 1000(Rust 端刻意為 999)。"""
    return _parse_with_max(value, 1000)


def demo_sleep(value):
    """demo.sleep:測試專用,睡 `value` 秒後回 None;只在 PARITY_TEST_SLEEP=1 時登錄。"""
    time.sleep(float(value))
    return None


# ---------- 產品模組單元(CHG-20261124-01 代決 6;Rust 端在 crates/parity/src/units.rs) ----------

def value_checks_is_int(value):
    """value_checks.is_int:任意 JSON 輸入,回 True / False;沒有 reject 與 error。"""
    return value_checks.is_int(value)


# 拒絕代碼與錯誤類別(與 crates/parity/src/units.rs 的 REJECT_INVALID、ERROR_TYPE 相同)
REJECT_INVALID = "invalid"
ERROR_TYPE = "type"


def names_name_char_ok(value):
    """names.name_char_ok:輸入不是恰好一個碼位的字串時,`names.name_char_ok` 丟 TypeError → error type。"""
    try:
        return names.name_char_ok(value)
    except TypeError:
        raise UnitError(ERROR_TYPE) from None


def names_normalize_name(value):
    """names.normalize_name:合格回原字串;`None`(不合格或非字串)→ reject invalid。"""
    result = names.normalize_name(value)
    if result is None:
        raise Reject(REJECT_INVALID)
    return result


def names_fold_name(value):
    """names.fold_name:回 `str.casefold` 的結果;非字串輸入丟 AttributeError / TypeError → error type。"""
    try:
        return names.fold_name(value)
    except (AttributeError, TypeError):
        raise UnitError(ERROR_TYPE) from None


def names_constants(_value):
    """names.constants:忽略輸入;回 MAX_NAME、依碼位排序的 NAME_ASCII、原順序的 NAME_RANGES。"""
    return {
        "max_name": names.MAX_NAME,
        "ascii": "".join(sorted(names.NAME_ASCII)),
        "ranges": [[lo, hi] for lo, hi in names.NAME_RANGES],
    }


# ---------- safe_fs.* 單元(CHG-20261125-01 代決 10;Rust 端在 crates/parity/src/units_safe_fs.rs) ----------

# 工作目錄名稱前綴;outside/ 的固定內容;寫入旗標時寫入的標記;開啟旗標(與 units_safe_fs.rs 相同)
SAFE_FS_PREFIX = "parity-safe-fs-py-"
OUTSIDE_FILES = (("marker.txt", "outside"), ("d/inner.txt", "outside-inner"))
WRITE_MARK = b"<W>"
FLAGS = {"r": os.O_RDONLY, "w": os.O_WRONLY, "rw": os.O_RDWR, "w_trunc": os.O_WRONLY | os.O_TRUNC}


def _type_error():
    return UnitError(ERROR_TYPE)


def _rel_parts(path):
    """fixture 內的相對路徑(以 `/` 分隔);不合法(非字串、空段、`.`、`..`、反斜線)→ error type。"""
    if not isinstance(path, str) or path == "":
        raise _type_error()
    parts = path.split("/")
    if any(p in ("", ".", "..") or "\\" in p for p in parts):
        raise _type_error()
    return parts


class _Work:
    """單一案例的暫存工作目錄:top/、outside/、missing(不存在,懸空連結用)。"""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix=SAFE_FS_PREFIX)
        self.top = os.path.join(self.root, "top")
        self.outside = os.path.join(self.root, "outside")
        self.missing = os.path.join(self.root, "missing")
        os.mkdir(self.top)
        os.mkdir(self.outside)
        for rel, content in OUTSIDE_FILES:
            path = os.path.join(self.outside, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(content)

    def target(self, token):
        """連結記號 → 本機路徑:`@top/…`、`@outside/…`、`@missing`。"""
        if token == "@missing":
            return self.missing
        for prefix, base in (("@top", self.top), ("@outside", self.outside)):
            if token == prefix:
                return base
            if isinstance(token, str) and token.startswith(prefix + "/"):
                return os.path.join(base, *_rel_parts(token[len(prefix) + 1:]))
        raise _type_error()

    def token(self, target):
        """本機路徑 → 連結記號(快照用)。"""
        if target == self.missing:
            return "@missing"
        for prefix, base in (("@top", self.top), ("@outside", self.outside)):
            if target == base:
                return prefix
            if target.startswith(base + os.sep):
                return prefix + "/" + target[len(base) + 1:].replace(os.sep, "/")
        return "@other"

    def build(self, tree):
        if not isinstance(tree, list):
            raise _type_error()
        for item in tree:
            if not isinstance(item, dict):
                raise _type_error()
            path = os.path.join(self.top, *_rel_parts(item.get("path")))
            kind = item.get("type")
            if kind == "dir":
                os.makedirs(path, exist_ok=True)
            elif kind == "file":
                content = item.get("content", "")
                if not isinstance(content, str):
                    raise _type_error()
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(content)
            elif kind == "symlink":
                link_kind = item.get("kind")
                if link_kind not in ("dir", "file"):
                    raise _type_error()
                os.makedirs(os.path.dirname(path), exist_ok=True)
                os.symlink(self.target(item.get("target")), path,
                           target_is_directory=(link_kind == "dir"))
            else:
                raise _type_error()

    def snapshot(self, base):
        """不跟隨連結的快照:依相對路徑排序的 [相對路徑, 型別, 內容或連結記號]。"""
        out = []

        def walk(directory, rel):
            with os.scandir(directory) as it:
                items = list(it)
            for e in items:
                r = e.name if rel == "" else rel + "/" + e.name
                if e.is_symlink():
                    out.append([r, "symlink", self.token(os.readlink(e.path))])
                elif e.is_dir(follow_symlinks=False):
                    out.append([r, "dir", None])
                    walk(e.path, r)
                elif e.is_file(follow_symlinks=False):
                    with open(e.path, "rb") as f:
                        out.append([r, "file", f.read().decode("utf-8", "replace")])
                else:
                    out.append([r, "other", None])

        walk(base, "")
        out.sort(key=lambda x: x[0])
        return out

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def _call_field(call, key, kind, default=None):
    """取 call 的欄位;型別不符 → error type(名稱內容本身交給 safe_fs 檢查)。"""
    value = call.get(key, default)
    if kind == "str" and not isinstance(value, str):
        raise _type_error()
    if kind == "bool" and not isinstance(value, bool):
        raise _type_error()
    if kind == "list" and (not isinstance(value, list) or not all(isinstance(x, str) for x in value)):
        raise _type_error()
    if kind == "flags" and (not isinstance(value, str) or value not in FLAGS):
        raise _type_error()
    return value


def _dir_result(fd):
    return {"dir": sorted(os.listdir(fd))}


def _file_result(fd, flags):
    """寫入旗標先寫入標記;`rw` 再從頭讀回全部內容,`w` / `w_trunc` 不讀(值為 null),`r` 讀全部內容。"""
    if flags != "r":
        os.write(fd, WRITE_MARK)
    if flags in ("w", "w_trunc"):
        return {"file": None}
    if flags == "rw":
        os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while True:
        b = os.read(fd, 65536)
        if not b:
            break
        chunks.append(b)
    return {"file": b"".join(chunks).decode("utf-8", "replace")}


def _map_safe_fs_error(e):
    """代決 10 的錯誤類別表(UnsafePathError 須先於其他 OSError 判斷)。"""
    if isinstance(e, safe_fs.UnsafePathError):
        return Reject("unsafe")
    if isinstance(e, ValueError):
        return Reject("invalid_name")
    for cls, name in ((FileNotFoundError, "not_found"), (FileExistsError, "exists"),
                      (NotADirectoryError, "not_a_directory"), (IsADirectoryError, "is_a_directory"),
                      (PermissionError, "permission")):
        if isinstance(e, cls):
            return UnitError(name)
    if e.errno is None:
        return UnitError("os_unknown")
    return UnitError("os_" + errno.errorcode.get(e.errno, f"errno{e.errno}"))


def _run_tree_case(value, action):
    """建樹 → 呼叫 → 結果與快照;案例結束一律刪除工作目錄(建樹失敗也一樣)。"""
    if not isinstance(value, dict) or not isinstance(value.get("call"), dict):
        raise _type_error()
    work = _Work()
    try:
        work.build(value.get("tree", []))
        try:
            result = action(work, value["call"])
        except (OSError, ValueError) as e:
            raise _map_safe_fs_error(e) from None
        return {"result": result, "top": work.snapshot(work.top),
                "outside": work.snapshot(work.outside)}
    finally:
        work.close()


def _top_path(work, call):
    rel = _call_field(call, "top", "str", "")
    return work.top if rel == "" else os.path.join(work.top, *_rel_parts(rel))


def safe_fs_check_name(value):
    """safe_fs.check_name:輸入為名稱字串;合格 ok null、不合格 reject invalid_name;非字串 error type。"""
    if not isinstance(value, str):
        raise _type_error()
    try:
        safe_fs._check_name(value)
    except ValueError:
        raise Reject("invalid_name") from None
    return None


def _act_open_dir(work, call):
    parts = _call_field(call, "parts", "list", [])
    create = _call_field(call, "create", "bool", False)
    top = _top_path(work, call)
    with safe_fs.closing(safe_fs.open_dir(top, parts, create=create)) as fd:
        return _dir_result(fd)


def _act_open_child(work, call):
    parts = _call_field(call, "dir", "list", [])
    name = _call_field(call, "name", "str")
    create = _call_field(call, "create", "bool", False)
    with safe_fs.closing(safe_fs.open_dir(work.top, parts)) as d:
        with safe_fs.closing(safe_fs.open_child(d, name, create=create)) as fd:
            return _dir_result(fd)


def _act_open_file(work, call):
    parts = _call_field(call, "dir", "list", [])
    name = _call_field(call, "name", "str")
    flags = _call_field(call, "flags", "flags", "r")
    with safe_fs.closing(safe_fs.open_dir(work.top, parts)) as d:
        with safe_fs.closing(safe_fs.open_file(d, name, FLAGS[flags])) as fd:
            return _file_result(fd, flags)


def _act_open_path(work, call):
    parts = _call_field(call, "parts", "list", [])
    flags = _call_field(call, "flags", "flags", "r")
    top = _top_path(work, call)
    with safe_fs.closing(safe_fs.open_path(top, parts, FLAGS[flags])) as fd:
        return _file_result(fd, flags)


def safe_fs_open_dir(value):
    """safe_fs.open_dir:call `{"parts", "create"?, "top"?}` → `{"dir": [名稱…]}`。"""
    return _run_tree_case(value, _act_open_dir)


def safe_fs_open_child(value):
    """safe_fs.open_child:call `{"dir", "name", "create"?}` → `{"dir": [名稱…]}`。"""
    return _run_tree_case(value, _act_open_child)


def safe_fs_open_file(value):
    """safe_fs.open_file:call `{"dir", "name", "flags"}` → `{"file": 內容或 null}`。"""
    return _run_tree_case(value, _act_open_file)


def safe_fs_open_path(value):
    """safe_fs.open_path:call `{"parts", "flags", "top"?}` → `{"file": 內容或 null}`。"""
    return _run_tree_case(value, _act_open_path)


# 建立類只接受含寫入的旗標(Rust 端建立檔案須帶寫入權限);`r` 視為輸入格式不符
CREATE_FLAGS = ("w", "rw", "w_trunc")


def _entry_type(st):
    """lstat 結果的型別:symlink / dir / file / other(與 Rust `EntryKind` 相同)。"""
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    if stat.S_ISDIR(st.st_mode):
        return "dir"
    if stat.S_ISREG(st.st_mode):
        return "file"
    return "other"


def _lstat_result(st):
    """`null`(不存在)或 `{"type", "size"?}`(只有一般檔帶 size;不比對時間、inode 等隨環境變動的欄位)。"""
    if st is None:
        return None
    out = {"type": _entry_type(st)}
    if out["type"] == "file":
        out["size"] = st.st_size
    return out


def _create_flags(call, default):
    flags = call.get("flags", default)
    if flags not in CREATE_FLAGS:
        raise _type_error()
    return flags


def _act_create_excl(work, call):
    parts = _call_field(call, "dir", "list", [])
    name = _call_field(call, "name", "str")
    flags = _create_flags(call, "w")
    with safe_fs.closing(safe_fs.open_dir(work.top, parts)) as d:
        with safe_fs.closing(safe_fs.create_excl(d, name, 0o600, FLAGS[flags])) as fd:
            return _file_result(fd, flags)


def _act_open_or_create(work, call):
    parts = _call_field(call, "dir", "list", [])
    name = _call_field(call, "name", "str")
    flags = _create_flags(call, "rw")
    with safe_fs.closing(safe_fs.open_dir(work.top, parts)) as d:
        with safe_fs.closing(safe_fs.open_or_create(d, name, FLAGS[flags])) as fd:
            return _file_result(fd, flags)


def _act_lstat(work, call):
    parts = _call_field(call, "dir", "list", [])
    name = _call_field(call, "name", "str")
    with safe_fs.closing(safe_fs.open_dir(work.top, parts)) as d:
        return _lstat_result(safe_fs.lstat(d, name))


def _act_entries(work, call):
    parts = _call_field(call, "dir", "list", [])
    with safe_fs.closing(safe_fs.open_dir(work.top, parts)) as d:
        return [[n, _entry_type(st)] for n, st in safe_fs.entries(d)]


def _act_remove(func):
    def act(work, call):
        parts = _call_field(call, "dir", "list", [])
        name = _call_field(call, "name", "str")
        missing_ok = _call_field(call, "missing_ok", "bool", False)
        with safe_fs.closing(safe_fs.open_dir(work.top, parts)) as d:
            func(d, name, missing_ok=missing_ok)
        return None
    return act


def _act_move(func):
    def act(work, call):
        src_parts = _call_field(call, "src_dir", "list", [])
        src = _call_field(call, "src", "str")
        dst_parts = _call_field(call, "dst_dir", "list", [])
        dst = _call_field(call, "dst", "str")
        with safe_fs.closing(safe_fs.open_dir(work.top, src_parts)) as s:
            with safe_fs.closing(safe_fs.open_dir(work.top, dst_parts)) as t:
                func(s, src, t, dst)
        return None
    return act


def safe_fs_create_excl(value):
    """safe_fs.create_excl:call `{"dir", "name", "flags"?(w / rw / w_trunc,預設 w)}` → `{"file"}`;權限 0o600。"""
    return _run_tree_case(value, _act_create_excl)


def safe_fs_open_or_create(value):
    """safe_fs.open_or_create:call `{"dir", "name", "flags"?(w / rw / w_trunc,預設 rw)}` → `{"file"}`。"""
    return _run_tree_case(value, _act_open_or_create)


def safe_fs_lstat(value):
    """safe_fs.lstat:call `{"dir", "name"}` → `null` 或 `{"type", "size"?}`。"""
    return _run_tree_case(value, _act_lstat)


def safe_fs_entries(value):
    """safe_fs.entries:call `{"dir"}` → `[[名稱, 型別], …]`(依名稱的碼位排序)。"""
    return _run_tree_case(value, _act_entries)


def safe_fs_unlink(value):
    """safe_fs.unlink:call `{"dir", "name", "missing_ok"?}` → `null`。"""
    return _run_tree_case(value, _act_remove(safe_fs.unlink))


def safe_fs_rmtree(value):
    """safe_fs.rmtree:call `{"dir", "name", "missing_ok"?}` → `null`。"""
    return _run_tree_case(value, _act_remove(safe_fs.rmtree))


def safe_fs_rename(value):
    """safe_fs.rename:call `{"src_dir", "src", "dst_dir", "dst"}` → `null`。"""
    return _run_tree_case(value, _act_move(safe_fs.rename))


def safe_fs_replace(value):
    """safe_fs.replace:call 同 rename → `null`。"""
    return _run_tree_case(value, _act_move(safe_fs.replace))


def safe_fs_link(value):
    """safe_fs.link:call 同 rename → `null`(硬連結;來源是符號連結時不跟隨)。"""
    return _run_tree_case(value, _act_move(safe_fs.link))


# ---------- project.* 單元(CHG-20261128-01 代決 10;Rust 端在 crates/parity/src/units_project.rs) ----------
# 只在 project_* / _project_* 函式內才 import project(project.py 頂層 import fcntl,Windows 上無法載入;事實 H)。


def _project_error_result(e, pj):
    """ProjectError 及其子類別 → `{"class", "errors"}`(類別字串與 Rust `Error::class` 相同)。"""
    for cls, name in ((pj.UnsafePath, "unsafe_path"), (pj.ProjectNotFound, "not_found"),
                      (pj.ProjectExists, "exists"), (pj.QuotaExceeded, "quota")):
        if isinstance(e, cls):
            return {"class": name, "errors": list(e.errors)}
    return {"class": "project_error", "errors": list(e.errors)}


def _sorted_json(value):
    """`post_key` 用的緊湊、鍵排序寫法(連 -0.0 與整數 / 浮點的寫法一起比對)。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _value_result(value):
    return {"value": value, "repr": _sorted_json(value)}


def _number_input(value):
    """`{"v": 任意 JSON, "where"?: 字串}`;格式不符 → error type。"""
    if not isinstance(value, dict) or "v" not in value:
        raise _type_error()
    where = value.get("where")
    if where is not None and not isinstance(where, str):
        raise _type_error()
    return value["v"], where


def project_constants(_value):
    """project.constants:忽略輸入;回 project.py 的常數(集合以排序後字串、Decimal 以 str 表示)、
    str.isspace 為真的全部碼位,另附三個 dict 常數依建構順序的 `_dump` 字串。"""
    import project as pj
    return {
        "schema_version": pj.SCHEMA_VERSION,
        "old_schema_versions": list(pj.OLD_SCHEMA_VERSIONS),
        "projects_name": pj.PROJECTS_NAME,
        "trash_name": pj.TRASH_NAME,
        "project_file": pj.PROJECT_FILE,
        "lock_file": pj.LOCK_FILE,
        "history_keep": pj.HISTORY_KEEP,
        "trash_days": pj.TRASH_DAYS,
        "speed": [str(pj.SPEED_MIN), str(pj.SPEED_MAX), str(pj.SPEED_STEP)],
        "pause": [str(pj.PAUSE_MIN), str(pj.PAUSE_MAX), str(pj.PAUSE_STEP)],
        "semi": [str(pj.SEMI_MIN), str(pj.SEMI_MAX), str(pj.SEMI_STEP)],
        "pos_step": str(pj.POS_STEP),
        "defaults": pj.DEFAULTS,
        "defaults_dump": pj._dump(pj.DEFAULTS).decode("utf-8"),
        "break_pause": pj.BREAK_PAUSE,
        "breaks": list(pj.BREAKS),
        "max_script": pj.MAX_SCRIPT,
        "max_segment_text": pj.MAX_SEGMENT_TEXT,
        "max_segments": pj.MAX_SEGMENTS,
        "max_project_bytes": pj.MAX_PROJECT_BYTES,
        "max_assets": pj.MAX_ASSETS,
        "max_asset_bytes": pj.MAX_ASSET_BYTES,
        "max_audio_seconds": pj.MAX_AUDIO_SECONDS,
        "max_label": pj.MAX_LABEL,
        "zhuyin_max": pj.ZHUYIN_MAX,
        "prosody_defaults": pj.PROSODY_DEFAULTS,
        "prosody_defaults_dump": pj._dump(pj.PROSODY_DEFAULTS).decode("utf-8"),
        "pitch_points": [pj.PITCH_POINTS_MIN, pj.PITCH_POINTS_MAX],
        "inner_points_max": pj.INNER_POINTS_MAX,
        "inner_semi_limit": pj.INNER_SEMI_LIMIT,
        "max_word_clarity": pj.MAX_WORD_CLARITY,
        "max_fusions": pj.MAX_FUSIONS,
        "fusion": [pj.FUSION_MIN, pj.FUSION_MAX],
        "max_fusion_off": pj.MAX_FUSION_OFF,
        "fusion_display_max": pj.FUSION_DISPLAY_MAX,
        "fusion_sources": list(pj.FUSION_SOURCES),
        "post_version": pj.POST_VERSION,
        "post_algo": pj.POST_ALGO,
        "trim_params": pj.TRIM_PARAMS,
        "trim_params_dump": pj._dump(pj.TRIM_PARAMS).decode("utf-8"),
        "asset_exts": {k: list(v) for k, v in pj.ASSET_EXTS.items()},
        "zhuyin_chars": "".join(sorted(pj.ZHUYIN_CHARS)),
        "sentence_enders": "".join(sorted(pj.SENTENCE_ENDERS)),
        "closers": "".join(sorted(pj.CLOSERS)),
        "newlines": "".join(sorted(pj.NEWLINES)),
        "hanzi_ranges": [list(r) for r in pj.HANZI_RANGES],
        "name_hint": pj.NAME_HINT,
        "link_tries": pj.LINK_TRIES,
        "top_keys": list(pj.TOP_KEYS),
        "segment_keys_v1": list(pj.SEGMENT_KEYS_V1),
        "segment_keys": list(pj.SEGMENT_KEYS),
        "post_keys": list(pj.POST_KEYS),
        "post_optional": list(pj.POST_OPTIONAL),
        "image_keys": list(pj.IMAGE_KEYS),
        "asset_keys": list(pj.ASSET_KEYS),
        "whitespace": [c for c in range(0x110000) if chr(c).isspace()],
    }


def _project_normalize(value, name):
    import project as pj
    v, where = _number_input(value)
    fn = getattr(pj, name)
    try:
        result = fn(v) if where is None else fn(v, where)
    except pj.ProjectError as e:
        return _project_error_result(e, pj)
    return _value_result(result)


def project_normalize_speed(value):
    """project.normalize_speed:輸入 `{"v", "where"?}`;未給 where 時用 Python 的預設「語速」。"""
    return _project_normalize(value, "normalize_speed")


def project_normalize_pause(value):
    """project.normalize_pause:輸入 `{"v", "where"?}`;未給 where 時用 Python 的預設「停頓」。"""
    return _project_normalize(value, "normalize_pause")


def project_check_zhuyin(value):
    """project.check_zhuyin:輸入為任意 JSON(注音標註)。"""
    import project as pj
    try:
        return {"value": pj.check_zhuyin(value)}
    except pj.ProjectError as e:
        return _project_error_result(e, pj)


def project_check_pitch_points(value):
    """project.check_pitch_points:輸入 `{"points": 任意 JSON, "where": 字串}`。"""
    import project as pj
    if not isinstance(value, dict) or "points" not in value or not isinstance(value.get("where"), str):
        raise _type_error()
    try:
        return _value_result(pj.check_pitch_points(value["points"], value["where"]))
    except pj.ProjectError as e:
        return _project_error_result(e, pj)


def project_py_json(value):
    """project.py_json:輸入任意 JSON 值;回 `_dump` 的字串與 `post_key` 用的緊湊排序寫法。"""
    import project as pj
    return {"dump": pj._dump(value).decode("utf-8"), "sorted": _sorted_json(value)}


# 精簡形式(CHG-20261128-01 T2 DIR-6 第 1 輪 F-1):超長的重複內容不直接寫進 fixture 與預期結果表。
PROJECT_REPEAT_MAX = 5242881      # `{"repeat", "count"}` 的 count 上限(= 5 MB + 1)
PROJECT_RUN_MIN = 65              # 結果中同一字元連續達此長度才改用 parts(T3 補記:原為 1000;上限 64)


def _project_parts_text(parts):
    """`{"parts": [片段…]}` 展開成字串:片段為字串,或 `{"repeat": 非空字串, "count": 1~PROJECT_REPEAT_MAX 的整數}`;
    空陣列、未知鍵、布林、0、超過上限等一律 error type(與 Rust `units_project::parts_text` 同一規則)。"""
    if not isinstance(parts, list) or not parts:
        raise _type_error()
    out = []
    for part in parts:
        if isinstance(part, str):
            out.append(part)
            continue
        if not isinstance(part, dict) or set(part) != {"repeat", "count"}:
            raise _type_error()
        rep, count = part["repeat"], part["count"]
        if (not isinstance(rep, str) or rep == "" or not isinstance(count, int) or isinstance(count, bool)
                or not 1 <= count <= PROJECT_REPEAT_MAX):
            raise _type_error()
        out.append(rep * count)
    return "".join(out)


def _project_compact_dump(text):
    """接受時的 dump:沒有同一字元連續 PROJECT_RUN_MIN 個以上時原樣回字串;否則回 `{"parts": […]}`——
    每一段最長的同字元連續(≥ PROJECT_RUN_MIN)寫成 `{"repeat": 字元, "count": 長度}`,其餘相鄰字元合併為字串段。
    以 `_project_parts_text` 展開即得原字串(與 Rust `units_project::compact_dump` 同一規則)。"""
    parts, buf, i, n = [], [], 0, len(text)
    while i < n:
        j = i
        while j < n and text[j] == text[i]:
            j += 1
        if j - i >= PROJECT_RUN_MIN:
            if buf:
                parts.append("".join(buf))
                buf = []
            parts.append({"repeat": text[i], "count": j - i})
        else:
            buf.append(text[i:j])
        i = j
    if not any(isinstance(p, dict) for p in parts):
        return text
    if buf:
        parts.append("".join(buf))
    return {"parts": parts}


def _project_text_bytes(value):
    """`project.validate_text` 的輸入:JSON 原文字串(以 UTF-8 編碼)、`{"hex": 位元組的十六進位}`,
    或 `{"parts": [片段…]}`(展開後以 UTF-8 編碼,見 `_project_parts_text`)。"""
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, dict) and set(value) == {"hex"} and isinstance(value["hex"], str):
        try:
            return bytes.fromhex(value["hex"])
        except ValueError:
            raise _type_error() from None
    if isinstance(value, dict) and set(value) == {"parts"}:
        return _project_parts_text(value["parts"]).encode("utf-8")
    raise _type_error()


def project_validate_text(value):
    """project.validate_text(T2):把原文位元組寫進自己以 `tempfile.mkdtemp` 建立的暫存目錄,以
    `project._read_project_file`(讀檔、5 MB 上限、UTF-8、`json.loads`、須為物件)讀回,再 `validate` 與 `_dump`;
    接受 → `{"dump": _dump 字串}`,拒絕 → `{"class", "errors"}`。案例結束即刪除暫存目錄。"""
    import project as pj
    raw = _project_text_bytes(value)
    work = tempfile.mkdtemp(prefix="parity-project-")
    try:
        with open(os.path.join(work, pj.PROJECT_FILE), "wb") as f:
            f.write(raw)
        dirfd = os.open(work, os.O_RDONLY | os.O_DIRECTORY)
        try:
            data = pj._read_project_file(dirfd, pj.PROJECT_FILE)
            return {"dump": _project_compact_dump(pj._dump(pj.validate(data)).decode("utf-8"))}
        except pj.ProjectError as e:
            return _project_error_result(e, pj)
        finally:
            os.close(dirfd)
    finally:
        shutil.rmtree(work)


# ---------- project.* T3:純函式(Rust 端 units_project.rs 的同名單元) ----------

PROJECT_I64 = 2 ** 63


def _project_text_value(v):
    """文字欄位:字串或 `{"parts": [片段…]}`(恰此一鍵,`_project_parts_text` 展開);其他 → error type。"""
    if isinstance(v, str):
        return v
    if isinstance(v, dict) and set(v) == {"parts"}:
        return _project_parts_text(v["parts"])
    raise _type_error()


def _project_compact_strings(v):
    """接受時的值:每個字串依 `_project_compact_dump` 轉換(同字元連續 ≥ PROJECT_RUN_MIN 改為 `{"parts"}`),
    陣列與物件遞迴處理(與 Rust `units_project::compact_strings` 同一規則)。"""
    if isinstance(v, str):
        return _project_compact_dump(v)
    if isinstance(v, list):
        return [_project_compact_strings(x) for x in v]
    if isinstance(v, dict):
        return {k: _project_compact_strings(x) for k, x in v.items()}
    return v


def _project_text_ok(v):
    return {"value": _project_compact_strings(v)}


def _project_segment_data(value, with_defaults=False):
    """片段類單元的輸入 `{"segment", "defaults"?}` → 交給 `validate` 的專案;缺 `segment` → error type。
    `segment.text` 為物件時以 `_project_text_value` 展開(驗證前)。"""
    if not isinstance(value, dict) or "segment" not in value:
        raise _type_error()
    segment = value["segment"]
    if isinstance(segment, dict) and isinstance(segment.get("text"), dict):
        segment = {**segment, "text": _project_text_value(segment["text"])}
    data = {"schema_version": 2, "name": "p", "segments": [segment]}
    if with_defaults and "defaults" in value:
        data["defaults"] = value["defaults"]
    return data


def _project_on_segment(value, fn):
    """驗證後的片段交給 `fn`;驗證失敗回 `{"class", "errors"}`。"""
    import project as pj
    data = _project_segment_data(value)
    try:
        segment = pj.validate(data)["segments"][0]
    except pj.ProjectError as e:
        return _project_error_result(e, pj)
    return fn(pj, segment)


def project_split_segments(value):
    """project.split_segments:`{"text": 字串或 {"parts"}, "forced"?: [整數…]}`(forced 只接受 64 位元範圍內的整數,
    不含布林;Rust API 為 `&[i64]`)。"""
    import project as pj
    if not isinstance(value, dict):
        raise _type_error()
    if "text" not in value:
        raise _type_error()
    text = _project_text_value(value["text"])
    forced = value.get("forced", [])
    if not isinstance(forced, list) or not all(
            isinstance(b, int) and not isinstance(b, bool) and -PROJECT_I64 <= b < PROJECT_I64 for b in forced):
        raise _type_error()
    try:
        return _project_text_ok(pj.split_segments(text, forced))
    except pj.ProjectError as e:
        return _project_error_result(e, pj)


def project_escape_text(value):
    """project.escape_text:輸入為字串或 `{"parts"}`。"""
    import project as pj
    return _project_text_ok(pj.escape_text(_project_text_value(value)))


def project_tts_text(value):
    """project.tts_text:`{"segment"}`(先驗證)。"""
    def run(pj, segment):
        try:
            return _project_text_ok(pj.tts_text(segment))
        except pj.ProjectError as e:
            return _project_error_result(e, pj)
    return _project_on_segment(value, run)


def project_fused_out(value):
    """project.fused_out:`{"segment"}` → 遞增排序的索引。"""
    return _project_on_segment(value, lambda pj, s: _project_text_ok(sorted(pj.fused_out(s))))


def project_model_units(value):
    """project.model_units:`{"segment"}`。"""
    return _project_on_segment(value, lambda pj, s: _project_text_ok(pj.model_units(s)))


def project_shift_marks(value):
    """project.shift_marks:`{"segment", "old", "new"}`(`old` / `new` 為字串或 `{"parts"}`)。"""
    if not isinstance(value, dict) or "old" not in value or "new" not in value:
        raise _type_error()
    old, new = _project_text_value(value["old"]), _project_text_value(value["new"])
    return _project_on_segment(value, lambda pj, s: _project_text_ok(pj.shift_marks(s, old, new)))


def project_post_params(value):
    """project.post_params:`{"segment"}` → `{"value", "repr"}`。"""
    return _project_on_segment(value, lambda pj, s: _value_result(pj.post_params(s)))


def project_post_key(value):
    """project.post_key:`{"segment"}`;自錄音檔或沒有音檔回 `{"class", "errors"}`。"""
    def run(pj, segment):
        try:
            return _project_text_ok(pj.post_key(segment))
        except pj.ProjectError as e:
            return _project_error_result(e, pj)
    return _project_on_segment(value, run)


def project_is_post_fresh(value):
    """project.is_post_fresh:`{"segment"}`。"""
    return _project_on_segment(value, lambda pj, s: _project_text_ok(pj.is_post_fresh(s)))


def project_is_default_post(value):
    """project.is_default_post:`{"segment"}`。"""
    return _project_on_segment(value, lambda pj, s: _project_text_ok(pj.is_default_post(s)))


def project_effective(value):
    """project.effective:`{"segment", "defaults"?}` → `effective_speed` 與 `effective_pause`
    (以 `Project.from_dict` 建立專案)。"""
    import project as pj
    data = _project_segment_data(value, with_defaults=True)
    try:
        proj = pj.Project.from_dict(data)
    except pj.ProjectError as e:
        return _project_error_result(e, pj)
    segment = proj.segments[0]
    return _value_result({"speed": pj.effective_speed(proj, segment), "pause": pj.effective_pause(proj, segment)})


# ---------- T4:讀取(合成檔案樹) ----------
# 輸入 `{"tree", "outside"?, "name"?, "vid"?}`;工作目錄(`tempfile.mkdtemp`,前綴 `parity-project-fs-py-`,先 realpath)
# 下放 `root/`(= `project.ROOT`)與 `outside/`;輸出 `{"result", "tree", "changed"}`;與 Rust 端同一規則
# (`crates/parity/src/units_project.rs`「T4:讀取」)。案例結束即刪除工作目錄,`project.ROOT` 還原。

PROJECT_FS_PREFIX = "parity-project-fs-py-"
PROJECT_DEFAULT_MTIME_MS = 1_767_225_600_000
INT64_MIN, INT64_MAX = -(2 ** 63), 2 ** 63 - 1


class _ProjectFsWork:
    """讀取類單元的暫存工作目錄:root/(資料根目錄)、outside/、missing(不存在,懸空連結用)。"""

    def __init__(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix=PROJECT_FS_PREFIX))
        self.root = os.path.join(self.base, "root")
        self.outside = os.path.join(self.base, "outside")
        self.missing = os.path.join(self.base, "missing")
        os.mkdir(self.root)
        os.mkdir(self.outside)

    def target(self, token):
        if token == "@missing":
            return self.missing
        for prefix, base in (("@root", self.root), ("@outside", self.outside)):
            if token == prefix:
                return base
            if isinstance(token, str) and token.startswith(prefix + "/"):
                return os.path.join(base, *_rel_parts(token[len(prefix) + 1:]))
        raise _type_error()

    def token(self, target):
        if target == self.missing:
            return "@missing"
        for prefix, base in (("@root", self.root), ("@outside", self.outside)):
            if target == base:
                return prefix
            if target.startswith(base + os.sep):
                return prefix + "/" + target[len(base) + 1:].replace(os.sep, "/")
        return "@other"

    def build(self, base, items):
        if items is None:
            return
        if not isinstance(items, list):
            raise _type_error()
        for item in items:
            if not isinstance(item, dict):
                raise _type_error()
            path = os.path.join(base, *_rel_parts(item.get("path")))
            kind = item.get("type")
            if kind == "dir":
                os.makedirs(path, exist_ok=True)
            elif kind == "file":
                content = _project_text_bytes(item["content"]) if "content" in item else b""
                mtime = item.get("mtime", PROJECT_DEFAULT_MTIME_MS)
                if not isinstance(mtime, int) or isinstance(mtime, bool) or not INT64_MIN <= mtime <= INT64_MAX:
                    raise _type_error()
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(content)
                os.utime(path, ns=(mtime * 1_000_000, mtime * 1_000_000))
            elif kind == "symlink":
                link_kind = item.get("kind")
                if link_kind not in ("dir", "file"):
                    raise _type_error()
                target = self.target(item.get("target"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                os.symlink(target, path, target_is_directory=(link_kind == "dir"))
            else:
                raise _type_error()

    def snapshot(self):
        """工作目錄下的快照(不跟隨連結):[(相對路徑, 輸出列, 狀態)],依相對路徑排序。"""
        out = []

        def walk(directory, rel):
            with os.scandir(directory) as it:
                items = list(it)
            for e in items:
                r = e.name if rel == "" else rel + "/" + e.name
                st = os.lstat(e.path)
                if stat.S_ISLNK(st.st_mode):
                    token = self.token(os.readlink(e.path))
                    out.append((r, [r, "symlink", token], ("symlink", st.st_mtime_ns, token.encode("utf-8"))))
                elif stat.S_ISDIR(st.st_mode):
                    out.append((r, [r, "dir", None], ("dir", st.st_mtime_ns, None)))
                    walk(e.path, r)
                elif stat.S_ISREG(st.st_mode):
                    with open(e.path, "rb") as f:
                        data = f.read()
                    out.append((r, [r, "file", None], ("file", st.st_mtime_ns, data)))
                else:
                    out.append((r, [r, "other", None], ("other", st.st_mtime_ns, None)))

        walk(self.base, "")
        out.sort(key=lambda x: x[0])
        return out

    def close(self):
        shutil.rmtree(self.base, ignore_errors=True)


def _project_read_error(e, pj):
    """讀取 API 的例外 → 結果:ProjectError → `{"class", "errors"}`,OSError → `{"class": "os_error"}`。"""
    if isinstance(e, pj.ProjectError):
        return _project_error_result(e, pj)
    return {"class": "os_error"}


def _project_loaded(pj, fn, *args):
    try:
        p = fn(*args)
    except (pj.ProjectError, OSError) as e:
        return _project_read_error(e, pj)
    return {"dump": _project_compact_dump(pj._dump(pj.validate(p.to_dict())).decode("utf-8"))}


def _project_value_call(pj, fn, *args):
    try:
        return {"value": fn(*args)}
    except (pj.ProjectError, OSError) as e:
        return _project_read_error(e, pj)


def _project_str_arg(value, key):
    v = value.get(key)
    if not isinstance(v, str):
        raise _type_error()
    return v


def _project_fs_case(value, action):
    """建樹 → 快照 → 呼叫(`project.ROOT` 指向 root/)→ 快照;案例結束一律刪除工作目錄並還原 `project.ROOT`。"""
    import project as pj
    if not isinstance(value, dict) or not isinstance(value.get("tree"), list):
        raise _type_error()
    work = _ProjectFsWork()
    saved = pj.ROOT
    try:
        work.build(work.root, value["tree"])
        work.build(work.outside, value.get("outside"))
        before = work.snapshot()
        pj.ROOT = Path(work.root)
        try:
            result = action(pj, value)
        finally:
            pj.ROOT = saved
        after = work.snapshot()
        b = {r: st for r, _, st in before}
        a = {r: st for r, _, st in after}
        changed = sorted(k for k in set(b) | set(a) if b.get(k) != a.get(k))
        return {"result": result, "tree": [row for _, row, _ in after], "changed": changed}
    finally:
        work.close()


def project_load(value):
    """project.load(T4):`{"tree", "name"}` → `{"dump"}` 或 `{"class", "errors"}`。"""
    return _project_fs_case(value, lambda pj, v: _project_loaded(pj, pj.load, _project_str_arg(v, "name")))


def project_load_version(value):
    """project.load_version(T4):`{"tree", "name", "vid"}`。"""
    return _project_fs_case(value, lambda pj, v: _project_loaded(
        pj, pj.load_version, _project_str_arg(v, "name"), _project_str_arg(v, "vid")))


def project_list_history(value):
    """project.list_history(T4):`{"tree", "name"}` → `{"value": [版本 id…]}`。"""
    return _project_fs_case(value, lambda pj, v: _project_value_call(pj, pj.list_history, _project_str_arg(v, "name")))


def project_list_projects(value):
    """project.list_projects(T4):`{"tree"}` → `{"value": [{"name", "updated"}…]}`。"""
    return _project_fs_case(value, lambda pj, v: _project_value_call(pj, pj.list_projects))


def project_disk_usage(value):
    """project.disk_usage(T4):`{"tree", "name"}` → `{"value": 位元組數}`。"""
    return _project_fs_case(value, lambda pj, v: _project_value_call(pj, pj.disk_usage, _project_str_arg(v, "name")))


# ---------- project.* T6:寫入(合成檔案樹 + 注入的時鐘與隨機碼;Rust 端 units_project.rs「T6:寫入」) ----------
# `project.save_sequence` 輸入 `{"tree", "outside"?, "clock", "ops"}`、`project.create` 輸入 `{"tree", "outside"?, "clock",
# "name", "script"?, "defaults"?}`;工作目錄同 T4。`clock` 的三個序列依呼叫順序取用、用完後重複最後一個:案例期間
# 暫時把 `project.time` 換成 `time()` 回 `now / 1000` 的替身、`project.secrets` 換成 `token_hex(2)` / `token_hex(8)`
# 取用 `rand4` / `hex8` 的替身,並在 `project` logger 掛上收集警告的 handler;案例結束一律還原並刪除工作目錄。

PROJECT_NOW_MAX_MS = 4_102_444_799_999     # 2099-12-31T23:59:59.999Z(此範圍內 fromtimestamp(ms / 1000) 取回同一毫秒)
PROJECT_CHANGE_FAILED = "修改中途失敗"
_PROJECT_HEX = frozenset("0123456789abcdef")


class _ProjectSeqClock:
    """依序取用固定序列的時鐘與隨機碼(用完後重複最後一個;與 Rust `SeqClock` 同一規則)。"""

    def __init__(self, spec):
        if not isinstance(spec, dict) or set(spec) != {"now", "rand4", "hex8"}:
            raise _type_error()
        for key in spec:
            if not isinstance(spec[key], list) or not spec[key]:
                raise _type_error()
        for n in spec["now"]:
            if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n <= PROJECT_NOW_MAX_MS:
                raise _type_error()
        for key, size in (("rand4", 4), ("hex8", 16)):
            for s in spec[key]:
                if not isinstance(s, str) or len(s) != size or not set(s) <= _PROJECT_HEX:
                    raise _type_error()
        self.items = {k: list(v) for k, v in spec.items()}
        self.pos = {k: 0 for k in spec}

    def _take(self, key):
        items = self.items[key]
        value = items[min(self.pos[key], len(items) - 1)]
        self.pos[key] += 1
        return value

    def time(self):
        """`project.time.time()` 的替身。"""
        return self._take("now") / 1000

    def token_hex(self, nbytes):
        """`project.secrets.token_hex()` 的替身:`_rand4()` 取 2 位元組、`new_id()` 與暫存檔名取 8 位元組。"""
        if nbytes == 2:
            return self._take("rand4")
        if nbytes == 8:
            return self._take("hex8")
        raise AssertionError(f"未預期的 token_hex({nbytes})")


class _ProjectWarnings(logging.Handler):
    """收集 `project` logger 的警告(Python `_warn` 以 log.warning 記錄;Rust 以回傳值交出,D-10)。"""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def _project_is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _project_only(op, allowed):
    if not set(op) <= set(allowed):
        raise _type_error()


def _project_create_defaults(v):
    """`create` 的 `defaults`:四鍵的子集、值為數字;格式不符 → error type。"""
    if v is None:
        return None
    if not isinstance(v, dict) or not set(v) <= {"speed", "pause_comma", "pause_sentence", "pause_paragraph"}:
        raise _type_error()
    if not all(_project_is_number(x) for x in v.values()):
        raise _type_error()
    return dict(v)


def _project_dump_value(pj, p):
    return _project_compact_dump(pj._dump(pj.validate(p.to_dict())).decode("utf-8"))


def _project_create_step(pj, op):
    name = _project_str_arg(op, "name")
    script = _project_text_value(op["script"]) if "script" in op else ""
    defaults = _project_create_defaults(op.get("defaults"))
    try:
        p = pj.create(name, script, defaults)
    except (pj.ProjectError, OSError) as e:
        return _project_read_error(e, pj)
    return {"value": {"dump": _project_dump_value(pj, p)}}


def _project_change(spec):
    if not isinstance(spec, dict) or not set(spec) <= {"script", "name", "speed", "fail", "return"}:
        raise _type_error()
    script = _project_text_value(spec["script"]) if "script" in spec else None
    if "name" in spec and not isinstance(spec["name"], str):
        raise _type_error()
    if "speed" in spec and not _project_is_number(spec["speed"]):
        raise _type_error()
    if "fail" in spec and not isinstance(spec["fail"], bool):
        raise _type_error()
    return script, spec


def _project_remove(work, items):
    """`fs` 操作的 `remove`:相對 `root/` 的路徑,不跟隨連結刪除(目錄連同內容)。"""
    if items is None:
        return
    if not isinstance(items, list):
        raise _type_error()
    for item in items:
        path = os.path.join(work.root, *_rel_parts(item))
        if stat.S_ISDIR(os.lstat(path).st_mode):
            shutil.rmtree(path)
        else:
            os.unlink(path)


def _project_run_op(pj, work, op, warns):
    if not isinstance(op, dict):
        raise _type_error()
    kind = op.get("op")
    warns.messages = []
    errors = (pj.ProjectError, OSError)
    if kind == "save":
        _project_only(op, ("op", "project", "script"))
        data = op.get("project")
        if not isinstance(data, dict):
            raise _type_error()
        script = _project_text_value(op["script"]) if "script" in op else None
        try:
            p = pj.Project.from_dict(data)
            if script is not None:
                p.script = script
            vid = pj.save(p)
        except errors as e:
            return _project_read_error(e, pj)
        return {"value": vid, "warnings": list(warns.messages)}
    if kind == "update":
        _project_only(op, ("op", "name", "change"))
        name = _project_str_arg(op, "name")
        script, spec = _project_change(op.get("change"))

        def change(p):
            if script is not None:
                p.script = script
            if "name" in spec:
                p.name = spec["name"]
            if "speed" in spec:
                p.defaults["speed"] = spec["speed"]
            if spec.get("fail"):
                raise pj.ProjectError(PROJECT_CHANGE_FAILED)
            return spec.get("return")

        try:
            got, vid, extra = pj.update(name, change)
        except errors as e:
            return _project_read_error(e, pj)
        return {"value": {"version": vid, "extra": extra, "dump": _project_dump_value(pj, got)},
                "warnings": list(warns.messages)}
    if kind == "restore":
        _project_only(op, ("op", "name", "vid"))
        name, vid = _project_str_arg(op, "name"), _project_str_arg(op, "vid")
        try:
            v = pj.restore(name, vid)
        except errors as e:
            return _project_read_error(e, pj)
        return {"value": v, "warnings": list(warns.messages)}
    if kind == "create":
        _project_only(op, ("op", "name", "script", "defaults"))
        return _project_create_step(pj, op)
    if kind == "load":
        _project_only(op, ("op", "name"))
        return _project_loaded(pj, pj.load, _project_str_arg(op, "name"))
    if kind == "load_version":
        _project_only(op, ("op", "name", "vid"))
        return _project_loaded(pj, pj.load_version, _project_str_arg(op, "name"), _project_str_arg(op, "vid"))
    if kind == "list_history":
        _project_only(op, ("op", "name"))
        return _project_value_call(pj, pj.list_history, _project_str_arg(op, "name"))
    if kind == "fs":
        _project_only(op, ("op", "add", "remove"))
        work.build(work.root, op.get("add"))
        _project_remove(work, op.get("remove"))
        return {"value": None}
    raise _type_error()


def _project_content_tree(work):
    """帶內容的快照:一般檔為字串(同字元連續 ≥ 65 改為 `{"parts"}`)或 `{"hex"}`(非 UTF-8)。"""
    rows = []
    for rel, row, state in work.snapshot():
        if state[0] == "file":
            try:
                content = _project_compact_dump(state[2].decode("utf-8"))
            except UnicodeDecodeError:
                content = {"hex": state[2].hex()}
            rows.append([rel, "file", content])
        else:
            rows.append(row)
    return rows


def _project_write_case(value, allowed, action):
    """建樹 → 依序執行(注入時鐘與隨機碼、收集警告)→ 帶內容的快照;一律還原並刪除工作目錄。"""
    import project as pj
    if not isinstance(value, dict) or not set(value) <= set(allowed) or not isinstance(value.get("tree"), list):
        raise _type_error()
    clock = _ProjectSeqClock(value.get("clock"))
    work = _ProjectFsWork()
    saved = (pj.ROOT, pj.time, pj.secrets)
    warns = _ProjectWarnings()
    logger = logging.getLogger("project")
    try:
        work.build(work.root, value["tree"])
        work.build(work.outside, value.get("outside"))
        pj.ROOT, pj.time, pj.secrets = Path(work.root), clock, clock
        logger.addHandler(warns)
        try:
            result = action(pj, work, value, warns)
        finally:
            logger.removeHandler(warns)
            pj.ROOT, pj.time, pj.secrets = saved
        result["tree"] = _project_content_tree(work)
        return result
    finally:
        work.close()


def project_save_sequence(value):
    """project.save_sequence(T6):`{"tree", "outside"?, "clock", "ops"}` → `{"steps", "tree"}`。"""
    def action(pj, work, v, warns):
        ops = v.get("ops")
        if not isinstance(ops, list) or not ops:
            raise _type_error()
        return {"steps": [_project_run_op(pj, work, op, warns) for op in ops]}
    return _project_write_case(value, ("tree", "outside", "clock", "ops"), action)


def project_create(value):
    """project.create(T6):`{"tree", "outside"?, "clock", "name", "script"?, "defaults"?}` → `{"result", "tree"}`。"""
    return _project_write_case(value, ("tree", "outside", "clock", "name", "script", "defaults"),
                               lambda pj, work, v, warns: {"result": _project_create_step(pj, v)})


def build_units(environ=None):
    """單元登錄字典(單元名 → 函式)。路線圖第 2 筆起,各模組的 Python 端單元在此登錄。"""
    environ = os.environ if environ is None else environ
    units = {
        "demo.parse_count": demo_parse_count,
        "demo.parse_count_drift": demo_parse_count_drift,
        "value_checks.is_int": value_checks_is_int,
        "names.name_char_ok": names_name_char_ok,
        "names.normalize_name": names_normalize_name,
        "names.fold_name": names_fold_name,
        "names.constants": names_constants,
        "safe_fs.check_name": safe_fs_check_name,
        "safe_fs.open_dir": safe_fs_open_dir,
        "safe_fs.open_child": safe_fs_open_child,
        "safe_fs.open_file": safe_fs_open_file,
        "safe_fs.open_path": safe_fs_open_path,
        "safe_fs.create_excl": safe_fs_create_excl,
        "safe_fs.open_or_create": safe_fs_open_or_create,
        "safe_fs.lstat": safe_fs_lstat,
        "safe_fs.entries": safe_fs_entries,
        "safe_fs.unlink": safe_fs_unlink,
        "safe_fs.rmtree": safe_fs_rmtree,
        "safe_fs.rename": safe_fs_rename,
        "safe_fs.replace": safe_fs_replace,
        "safe_fs.link": safe_fs_link,
        "project.constants": project_constants,
        "project.normalize_speed": project_normalize_speed,
        "project.normalize_pause": project_normalize_pause,
        "project.check_zhuyin": project_check_zhuyin,
        "project.check_pitch_points": project_check_pitch_points,
        "project.py_json": project_py_json,
        "project.validate_text": project_validate_text,
        "project.split_segments": project_split_segments,
        "project.escape_text": project_escape_text,
        "project.tts_text": project_tts_text,
        "project.fused_out": project_fused_out,
        "project.model_units": project_model_units,
        "project.shift_marks": project_shift_marks,
        "project.post_params": project_post_params,
        "project.post_key": project_post_key,
        "project.is_post_fresh": project_is_post_fresh,
        "project.is_default_post": project_is_default_post,
        "project.effective": project_effective,
        "project.load": project_load,
        "project.load_version": project_load_version,
        "project.list_history": project_list_history,
        "project.list_projects": project_list_projects,
        "project.disk_usage": project_disk_usage,
        "project.save_sequence": project_save_sequence,
        "project.create": project_create,
    }
    if environ.get("PARITY_TEST_SLEEP") == "1":
        units["demo.sleep"] = demo_sleep
    return units


# ---------- 協定 ----------

def respond(case_id, func, value):
    """執行單元並組出一行回應(不含換行)。"""
    try:
        result = func(value)
    except Reject as e:
        resp = {"id": case_id, "outcome": "reject", "code": e.code}
    except UnitError as e:
        resp = {"id": case_id, "outcome": "error", "class": e.cls}
    except Exception as e:  # noqa: BLE001 — 未預期的例外一律回報類別名稱
        resp = {"id": case_id, "outcome": "error", "class": type(e).__name__,
                "message": str(e)}
    else:
        resp = {"id": case_id, "outcome": "ok", "value": result}
    try:
        return json.dumps(resp, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as e:
        # 結果無法表示成標準 JSON(例:NaN、不可序列化的物件)
        return json.dumps({"id": case_id, "outcome": "error", "class": "unserializable",
                           "message": str(e)}, ensure_ascii=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description="parity 的 Python 轉接器(JSON 行協定)")
    parser.add_argument("--unit", required=True, help="單元名")
    args = parser.parse_args(argv)  # 參數錯誤時 argparse 以 exit 2 結束

    units = build_units()
    func = units.get(args.unit)
    if func is None:
        print(f"adapter:Python 端沒有登錄單元 {args.unit!r}(已登錄:{', '.join(sorted(units))})",
              file=sys.stderr)
        return 2

    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    for lineno, line in enumerate(sys.stdin, 1):
        line = line.rstrip("\r\n")
        if not line.strip():
            continue
        try:
            req = json.loads(line)
        except ValueError as e:
            print(f"adapter:第 {lineno} 行請求不是合法 JSON:{e}", file=sys.stderr)
            return 2
        if not isinstance(req, dict) or "id" not in req or "input" not in req:
            print(f"adapter:第 {lineno} 行請求須為含 id 與 input 的 JSON 物件", file=sys.stderr)
            return 2
        sys.stdout.write(respond(req["id"], func, req["input"]) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
