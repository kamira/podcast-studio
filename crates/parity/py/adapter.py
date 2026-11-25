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
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import shutil
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
