# -*- coding: utf-8 -*-
"""並行比對工具 Python 轉接器的測試(CHG-20261123-01 T2;CHG-20261124-01 T1、CHG-20261125-01 T1 擴充)。

L1:示範單元 `demo.parse_count`、`demo.parse_count_drift` 各案例的 Python 端期望結果(上限都是 1000);
產品模組單元 `value_checks.is_int` 各案例的 Python 端期望結果(CHG-20261124-01 代決 6);
`names.name_char_ok`、`names.normalize_name`、`names.fold_name`、`names.constants` 各案例的 Python 端
期望結果與「拒絕代碼 `invalid`、錯誤類別 `type`」的對應(CHG-20261124-01 T2);`str.casefold` 逐字進行、
分批串接的結果等於逐字結果(全碼位比對分批的前提)。
`safe_fs.*` 14 個單元(CHG-20261125-01 T1 五個開啟類、T2 九個建立 / 查詢 / 刪除 / 搬移類):fixture 每個案例的 Python 結果等於以 Python 為錨的預期結果表
(`crates/parity/tests/data/safe_fs_expected.json`,macOS / Linux 欄),`check_name` 直接案例,
案例結束後轉接器的暫存工作目錄不存在(Python `safe_fs` 不能在 Windows 執行,這些測試在 Windows 略過)。
L2(提供方側):轉接器輸出符合 JSON 行協定——每行一個 JSON 物件、換行一律 `\\n`、UTF-8、
三型回應欄位齊全、id 依序對應;未知單元 exit 2;`demo.sleep` 只在 `PARITY_TEST_SLEEP=1` 時登錄;
轉接器 import 的 `value_checks`、`names`、`safe_fs` 是 repo 根目錄的產品模組。
`project.*` 25 個單元(CHG-20261128-01 T1 六個、T2 `validate_text`、T3 純函式 11 個、T4 讀取 5 個、T6 寫入 2 個;
`validate_text` 與 T4 讀取類案例結束後轉接器的暫存目錄不存在、`project.ROOT` 還原,讀取前後合成檔案樹沒有任何改變;
T6 寫入類案例結束後 `project.ROOT`、`project.time`、`project.secrets` 與 `project` logger 的 handler 還原、工作目錄不存在):fixture 每個案例的 Python 結果等於以 Python 為錨的預期結果表
(`crates/parity/tests/data/project_expected.json`);轉接器載入的 `project`、`breezy_worker` 是 repo 根目錄的
產品模組(`project.py` 頂層 import `fcntl`,這些測試在 Windows 略過)。
約束:轉接器只 import 標準函式庫與明列的產品模組 `{names, value_checks, safe_fs, project}`(CHG-20261124-01 代決 13、
CHG-20261125-01 代決 11、CHG-20261128-01 代決 10);`project` 只能在 `project_*` / `_project_*` 函式內延後 import,
不得在檔頂 import(事實 H)。

轉接器以子程序執行(與 `parity` 比對器相同的呼叫方式),不 import 進測試行程。
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "crates" / "parity" / "py" / "adapter.py"
FIXTURES = ROOT / "crates" / "parity" / "fixtures" / "demo"


def run_adapter(unit, requests, env_extra=None, timeout=30):
    env = dict(os.environ)
    env.pop("PARITY_TEST_SLEEP", None)
    env.update({"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    if env_extra:
        env.update(env_extra)
    data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests)
    return subprocess.run(
        [sys.executable, str(ADAPTER), "--unit", unit],
        input=data.encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=timeout,
        check=False,
    )


def responses(proc):
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    raw = proc.stdout
    assert b"\r" not in raw, "換行須為 \\n"
    text = raw.decode("utf-8")
    assert text == "" or text.endswith("\n")
    return [json.loads(line) for line in text.splitlines()]


def ok(v):
    return {"outcome": "ok", "value": v}


def reject(code):
    return {"outcome": "reject", "code": code}


def error(cls):
    return {"outcome": "error", "class": cls}


# 示範 fixture 的共同案例:Python 端兩個單元規則相同(上限 1000)。
EXPECTED = {
    "plain_3": ok(3),
    "edge_1": ok(1),
    "edge_1000": ok(1000),
    "zero": reject("out_of_range"),
    "over_1001": reject("out_of_range"),
    "letters": reject("not_digits"),
    "empty": reject("not_digits"),
    "not_string_number": error("type"),
    "not_string_null": error("type"),
    "leading_zeros": ok(7),
    "fullwidth_digit": reject("not_digits"),
    "huge": reject("out_of_range"),
}


def strip(resp):
    """只留比對用的欄位(訊息文字不比對)。"""
    return {k: v for k, v in resp.items() if k in ("outcome", "value", "code", "class")}


# ---------- L1:示範單元 ----------

@pytest.mark.parametrize("unit,file", [
    ("demo.parse_count", "parse_count.json"),
    ("demo.parse_count_drift", "parse_count_drift.json"),
])
def test_示範單元各案例(unit, file):
    fixture = json.loads((FIXTURES / file).read_text(encoding="utf-8"))
    assert fixture["unit"] == unit
    got = responses(run_adapter(unit, fixture["cases"]))
    assert [r["id"] for r in got] == [c["id"] for c in fixture["cases"]]
    for case, resp in zip(fixture["cases"], got):
        assert strip(resp) == EXPECTED[case["id"]], case["id"]


def test_ok_的值是整數不是浮點():
    (resp,) = responses(run_adapter("demo.parse_count", [{"id": "a", "input": "1"}]))
    assert resp["value"] == 1 and type(resp["value"]) is int


def test_bool_輸入是型別錯誤():
    (resp,) = responses(run_adapter("demo.parse_count", [{"id": "a", "input": True}]))
    assert strip(resp) == error("type")


# ---------- L2:協定 ----------

def test_回應欄位齊全且id依序對應():
    reqs = [{"id": f"c{i}", "input": s} for i, s in enumerate(["5", "x", None, "中文"])]
    got = responses(run_adapter("demo.parse_count", reqs))
    assert [r["id"] for r in got] == [r["id"] for r in reqs]
    for r in got:
        assert r["outcome"] in ("ok", "reject", "error")
        need = {"ok": "value", "reject": "code", "error": "class"}[r["outcome"]]
        assert need in r


def test_容許_CRLF_請求():
    env = dict(os.environ, PYTHONUTF8="1")
    env.pop("PARITY_TEST_SLEEP", None)
    proc = subprocess.run(
        [sys.executable, str(ADAPTER), "--unit", "demo.parse_count"],
        input=b'{"id": "a", "input": "3"}\r\n\r\n{"id": "b", "input": "4"}\r\n',
        capture_output=True, env=env, timeout=30, check=False,
    )
    got = responses(proc)
    assert [strip(r) for r in got] == [ok(3), ok(4)]


def test_非_ASCII_以_UTF8_輸出():
    (resp,) = responses(run_adapter("demo.parse_count", [{"id": "中文案例", "input": "1"}]))
    assert resp["id"] == "中文案例"


def test_未知單元_exit_2():
    proc = run_adapter("demo.nope", [])
    assert proc.returncode == 2
    assert proc.stdout == b""
    assert "demo.nope" in proc.stderr.decode("utf-8")


def test_缺少_unit_參數_exit_2():
    proc = subprocess.run(
        [sys.executable, str(ADAPTER)], input=b"", capture_output=True, timeout=30, check=False,
    )
    assert proc.returncode == 2


def test_請求不是合法_JSON_exit_2():
    env = dict(os.environ, PYTHONUTF8="1")
    proc = subprocess.run(
        [sys.executable, str(ADAPTER), "--unit", "demo.parse_count"],
        input=b"not json\n", capture_output=True, env=env, timeout=30, check=False,
    )
    assert proc.returncode == 2
    assert proc.stdout == b""


def test_sleep_單元只在環境變數開啟時登錄():
    assert run_adapter("demo.sleep", []).returncode == 2
    got = responses(run_adapter("demo.sleep", [{"id": "s", "input": 0}],
                                env_extra={"PARITY_TEST_SLEEP": "1"}))
    assert strip(got[0]) == ok(None)


# ---------- L1:產品模組單元 value_checks.is_int(CHG-20261124-01 T1) ----------

VALUE_CHECKS_FIXTURE = ROOT / "crates" / "parity" / "fixtures" / "value_checks" / "is_int.json"

# Python `value_checks.is_int` 的期望結果:int(非 bool)為 True,其餘為 False。
IS_INT_EXPECTED = {
    "int_zero": True,
    "int_one": True,
    "int_negative": True,
    "int_i64_max": True,
    "int_i64_min": True,
    "int_u64_max": True,
    "bool_true": False,
    "bool_false": False,
    "float_one": False,
    "float_zero": False,
    "float_negative_zero": False,
    "float_exponent": False,
    "float_fraction": False,
    "string_digit": False,
    "string_empty": False,
    "null": False,
    "array_of_int": False,
    "array_empty": False,
    "object": False,
}


def test_value_checks_is_int_各案例():
    fixture = json.loads(VALUE_CHECKS_FIXTURE.read_text(encoding="utf-8"))
    assert fixture["unit"] == "value_checks.is_int"
    assert sorted(c["id"] for c in fixture["cases"]) == sorted(IS_INT_EXPECTED)
    got = responses(run_adapter("value_checks.is_int", fixture["cases"]))
    assert [r["id"] for r in got] == [c["id"] for c in fixture["cases"]]
    for case, resp in zip(fixture["cases"], got):
        assert strip(resp) == ok(IS_INT_EXPECTED[case["id"]]), case["id"]


def test_value_checks_is_int_JSON_原文的負零與大整數在_Python_端為整數():
    # 行為差異清單 D-5、D-6 的 Python 端:json.loads 對 -0 與超出 u64 / 低於 i64 的整數給 int。
    # Rust 端(serde_json)得浮點,由 crates/studio-core/tests/value_checks.rs 鎖住;不進比對 fixture。
    env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    env.pop("PARITY_TEST_SLEEP", None)
    proc = subprocess.run(
        [sys.executable, str(ADAPTER), "--unit", "value_checks.is_int"],
        input=(b'{"id": "neg_zero", "input": -0}\n'
               b'{"id": "over_u64", "input": 18446744073709551616}\n'
               b'{"id": "under_i64", "input": -9223372036854775809}\n'),
        capture_output=True, env=env, timeout=30, check=False,
    )
    got = responses(proc)
    assert [r["id"] for r in got] == ["neg_zero", "over_u64", "under_i64"]
    assert [strip(r) for r in got] == [ok(True)] * 3


def test_轉接器_import_的是_repo_根目錄的產品模組():
    # 以子程序載入轉接器(run_name 不是 __main__,不會進入 main),取出它 import 的模組;
    # 工作目錄設在轉接器目錄,確認不是靠呼叫端的工作目錄找到產品模組。
    probe = (
        "import runpy, sys\n"
        "g = runpy.run_path(sys.argv[1], run_name='adapter_probe')\n"
        "print(g['value_checks'].__file__)\n"
        "print(g['names'].__file__)\n"
        "print(g['safe_fs'].__file__)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe, str(ADAPTER)],
        capture_output=True, timeout=30, check=False, cwd=str(ADAPTER.parent),
        env=dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1"),
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    files = [Path(line).resolve() for line in proc.stdout.decode("utf-8").splitlines()]
    assert files == [ROOT / "value_checks.py", ROOT / "names.py", ROOT / "safe_fs.py"]


# ---------- L1:產品模組單元 names.*(CHG-20261124-01 T2) ----------

NAMES_FIXTURES = ROOT / "crates" / "parity" / "fixtures" / "names"


def load_names_fixture(file, unit):
    fixture = json.loads((NAMES_FIXTURES / file).read_text(encoding="utf-8"))
    assert fixture["unit"] == unit
    got = responses(run_adapter(unit, fixture["cases"]))
    assert [r["id"] for r in got] == [c["id"] for c in fixture["cases"]]
    return fixture["cases"], got


# name_char_ok:白名單內為 True;輸入不是恰好一個碼位的字串為 error type(Python 端 TypeError)。
NAME_CHAR_OK_TRUE = {
    "ascii_lower_a", "ascii_upper_z", "ascii_digit_0", "ascii_hyphen", "ascii_underscore",
    "hiragana_first_u3041", "hiragana_last_u3096", "katakana_first_u30a1", "katakana_last_u30fa",
    "prolonged_mark_u30fc", "iteration_mark_u3005", "ext_a_first_u3400", "ext_a_last_u4dbf",
    "cjk_first_u4e00", "cjk_last_u9fff",
}
NAME_CHAR_OK_TYPE_ERROR = {
    "type_empty_string", "type_two_chars", "type_int", "type_null", "type_bool", "type_array",
    "type_object",
}


def test_names_name_char_ok_各案例():
    cases, got = load_names_fixture("name_char_ok.json", "names.name_char_ok")
    assert NAME_CHAR_OK_TRUE | NAME_CHAR_OK_TYPE_ERROR <= {c["id"] for c in cases}
    for case, resp in zip(cases, got):
        cid = case["id"]
        if cid in NAME_CHAR_OK_TYPE_ERROR:
            want = error("type")
        else:
            want = ok(cid in NAME_CHAR_OK_TRUE)
        assert strip(resp) == want, cid


# normalize_name:合格回原字串;不合格與非字串輸入一律 reject invalid(Python 端回 None)。
NORMALIZE_OK = {
    "ascii_one_char", "ascii_64_chars", "cjk_one_char", "cjk_64_chars", "mixed_good", "hiragana",
    "katakana_prolonged", "iteration_mark", "ext_a_edge", "reserved_con_compat",
    "reserved_com1_compat", "reserved_lpt9_compat",
}


def test_names_normalize_name_各案例():
    cases, got = load_names_fixture("normalize_name.json", "names.normalize_name")
    ids = {c["id"] for c in cases}
    assert NORMALIZE_OK <= ids
    assert {"ascii_65_chars", "cjk_65_chars", "empty", "non_string_int", "non_string_null"} <= ids
    for case, resp in zip(cases, got):
        cid = case["id"]
        want = ok(case["input"]) if cid in NORMALIZE_OK else reject("invalid")
        assert strip(resp) == want, cid


# fold_name:Python str.casefold 的結果;非字串輸入為 error type(Python 端 AttributeError)。
FOLD_EXPECTED = {
    "ascii_upper": "strasse",
    "ascii_mixed": "abcxyz-_09",
    "sharp_s_word": "strasse",
    "sharp_s": "ss",
    "capital_sharp_s": "ss",
    "kelvin_sign": "k",
    "long_s": "s",
    "ligature_ff": "ff",
    "ligature_st": "st",
    "dotted_capital_i": "i\u0307",
    "dotless_small_i": "\u0131",
    "greek_capital_sigma": "\u03c3",
    "greek_final_sigma": "\u03c3",
    "greek_word": "\u03c3\u03b1\u03c3",
    "cherokee_small_a": "\u13a0",
    "cherokee_capital_a": "\u13a0",
    "fullwidth_capital_a": "\uff41",
    "n_preceded_by_apostrophe": "\u02bcn",
    "cyrillic": "\u043c\u043e\u0441\u043a\u0432\u0430",
    "deseret_capital": "\U00010428",
    "cjk_kana_unchanged": "漢字かなカナ",
    "whitelist_mixed": "my_project-01ゆめ",
    "empty": "",
    "control_and_space": "a\u0000 b",
}
FOLD_TYPE_ERROR = {
    "non_string_int", "non_string_null", "non_string_bool", "non_string_array",
    "non_string_object",
}


def test_names_fold_name_各案例():
    cases, got = load_names_fixture("fold_name.json", "names.fold_name")
    assert {c["id"] for c in cases} == set(FOLD_EXPECTED) | FOLD_TYPE_ERROR
    for case, resp in zip(cases, got):
        cid = case["id"]
        want = error("type") if cid in FOLD_TYPE_ERROR else ok(FOLD_EXPECTED[cid])
        assert strip(resp) == want, cid


def test_names_constants():
    cases, got = load_names_fixture("constants.json", "names.constants")
    assert len(cases) == 1
    assert strip(got[0]) == ok({
        "max_name": 64,
        "ascii": "-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijklmnopqrstuvwxyz",
        "ranges": [[0x4E00, 0x9FFF], [0x3400, 0x4DBF], [0x3041, 0x3096], [0x30A1, 0x30FA],
                   [0x30FC, 0x30FC], [0x3005, 0x3005]],
    })


def test_casefold_逐字進行_分批串接等於逐字結果():
    # crates/parity/tests/names_exhaustive.rs 把連續碼位串成一個字串分批送出;前提是 casefold 不依上下文。
    chars = [chr(c) for c in range(0x110000) if not 0xD800 <= c <= 0xDFFF]
    batch = 2048
    for i in range(0, len(chars), batch):
        chunk = chars[i:i + batch]
        assert "".join(chunk).casefold() == "".join(ch.casefold() for ch in chunk), hex(ord(chunk[0]))


# ---------- L1:產品模組單元 safe_fs.*(CHG-20261125-01 T1) ----------

SAFE_FS_FIXTURES = ROOT / "crates" / "parity" / "fixtures" / "safe_fs"
SAFE_FS_EXPECTED = ROOT / "crates" / "parity" / "tests" / "data" / "safe_fs_expected.json"
SAFE_FS_UNITS = ["check_name", "open_dir", "open_child", "open_file", "open_path",
                 "create_excl", "open_or_create", "lstat", "entries", "unlink", "rmtree",
                 "rename", "replace", "link"]
needs_posix_safe_fs = pytest.mark.skipif(
    sys.platform == "win32", reason="Python safe_fs 在 Windows 無法執行(CHG-20261125-01 事實 D)")


def _platform_column():
    return "macos" if sys.platform == "darwin" else "linux"


def _safe_fs_work_dirs():
    base = Path(tempfile.gettempdir())
    return {p.name for p in base.iterdir() if p.name.startswith("parity-safe-fs-py-")}


@needs_posix_safe_fs
@pytest.mark.parametrize("name", SAFE_FS_UNITS)
def test_safe_fs_各案例等於預期結果表(name):
    fixture = json.loads((SAFE_FS_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    unit = f"safe_fs.{name}"
    assert fixture["unit"] == unit
    table = json.loads(SAFE_FS_EXPECTED.read_text(encoding="utf-8"))[unit]
    assert set(table) == {c["id"] for c in fixture["cases"]}
    got = responses(run_adapter(unit, fixture["cases"]))
    assert [r["id"] for r in got] == [c["id"] for c in fixture["cases"]]
    col = _platform_column()
    for case, resp in zip(fixture["cases"], got):
        entry = table[case["id"]]
        assert strip(resp) == entry.get(col, entry["all"]), case["id"]


@needs_posix_safe_fs
def test_safe_fs_check_name_直接案例():
    reqs = [{"id": n or "empty", "input": n} for n in ["", ".", "..", "a/b", "x\x00y", "...", "a:b", "con"]]
    reqs.append({"id": "number", "input": 5})
    got = [strip(r) for r in responses(run_adapter("safe_fs.check_name", reqs))]
    assert got == [reject("invalid_name")] * 5 + [ok(None)] * 3 + [error("type")]


@needs_posix_safe_fs
def test_safe_fs_rmtree_不跟隨連結且根目錄外不變():
    """場景「刪除目錄樹不跟隨連結」(T2):目錄樹與連結本身都刪除,outside/ 快照不變。"""
    tree = [{"path": "t/sub/x.txt", "type": "file", "content": "x"},
            {"path": "t/out", "type": "symlink", "target": "@outside/d", "kind": "dir"},
            {"path": "t/sub/fl", "type": "symlink", "target": "@outside/marker.txt", "kind": "file"},
            {"path": "keep.txt", "type": "file", "content": "k"}]
    reqs = [{"id": "rm", "input": {"tree": tree, "call": {"dir": [], "name": "t"}}},
            {"id": "again", "input": {"tree": [], "call": {"dir": [], "name": "t"}}},
            {"id": "again_ok", "input": {"tree": [], "call": {"dir": [], "name": "t", "missing_ok": True}}}]
    got = [strip(r) for r in responses(run_adapter("safe_fs.rmtree", reqs))]
    outside = [["d", "dir", None], ["d/inner.txt", "file", "outside-inner"], ["marker.txt", "file", "outside"]]
    assert got[0] == ok({"result": None, "top": [["keep.txt", "file", "k"]], "outside": outside})
    assert got[1:] == [error("not_found"), ok({"result": None, "top": [], "outside": outside})]


@needs_posix_safe_fs
def test_safe_fs_建立類只接受寫入旗標():
    reqs = [{"id": f"{u}-{flag}", "input": {"tree": [], "call": {"dir": [], "name": "x", "flags": flag}}}
            for u in ("c", "o") for flag in ("r", "x", 1)]
    for unit in ("safe_fs.create_excl", "safe_fs.open_or_create"):
        got = [strip(r) for r in responses(run_adapter(unit, reqs))]
        assert got == [error("type")] * len(reqs), unit


@needs_posix_safe_fs
def test_safe_fs_案例結束後工作目錄不存在():
    before = _safe_fs_work_dirs()
    for name in SAFE_FS_UNITS[1:]:
        fixture = json.loads((SAFE_FS_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        responses(run_adapter(f"safe_fs.{name}", fixture["cases"]))
    assert _safe_fs_work_dirs() <= before


# ---------- L1:產品模組單元 project.*(CHG-20261128-01 T1) ----------

PROJECT_FIXTURES = ROOT / "crates" / "parity" / "fixtures" / "project"
PROJECT_EXPECTED = ROOT / "crates" / "parity" / "tests" / "data" / "project_expected.json"
PROJECT_UNITS = ["constants", "normalize_speed", "normalize_pause", "check_zhuyin", "check_pitch_points",
                 "py_json", "validate_text",
                 # T3:純函式
                 "split_segments", "escape_text", "tts_text", "fused_out", "model_units", "shift_marks",
                 "post_params", "post_key", "is_post_fresh", "is_default_post", "effective",
                 # T4:讀取(合成檔案樹)
                 "load", "load_version", "list_history", "list_projects", "disk_usage",
                 # T6:寫入(合成檔案樹 + 注入的時鐘與隨機碼)
                 "save_sequence", "create"]
needs_posix_project = pytest.mark.skipif(
    sys.platform == "win32", reason="project.py 頂層 import fcntl,在 Windows 無法載入(CHG-20261128-01 事實 H)")


def responses_split_on_newline(proc):
    """同 `responses`,但只以 `\\n` 分行:project.* 的結果字串可含 U+2028、U+001C 等,
    `str.splitlines` 會在這些字元切開(協定只以 `\\n` 分行)。"""
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    raw = proc.stdout
    assert b"\r" not in raw, "換行須為 \\n"
    text = raw.decode("utf-8")
    assert text == "" or text.endswith("\n")
    return [json.loads(line) for line in text.split("\n") if line]


@needs_posix_project
@pytest.mark.parametrize("name", PROJECT_UNITS)
def test_project_各案例等於預期結果表(name):
    fixture = json.loads((PROJECT_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    unit = f"project.{name}"
    assert fixture["unit"] == unit
    table = json.loads(PROJECT_EXPECTED.read_text(encoding="utf-8"))[unit]
    assert set(table) == {c["id"] for c in fixture["cases"]}
    got = responses_split_on_newline(run_adapter(unit, fixture["cases"]))
    assert [r["id"] for r in got] == [c["id"] for c in fixture["cases"]]
    for case, resp in zip(fixture["cases"], got):
        entry = table[case["id"]]
        assert strip(resp) == entry.get("linux", entry["all"]), case["id"]


@needs_posix_project
def test_project_單元的錯誤訊息逐字放在_ok_的值內():
    reqs = [{"id": "s", "input": {"v": "x"}}, {"id": "p", "input": {"v": -0.01}},
            {"id": "bad", "input": 5}]
    got = [strip(r) for r in responses_split_on_newline(run_adapter("project.normalize_pause", reqs))]
    assert got == [ok({"class": "project_error", "errors": ["停頓必須是數字"]}),
                   ok({"value": -0.0, "repr": "-0.0"}), error("type")]


def _project_work_dirs():
    return {p.name for p in Path(tempfile.gettempdir()).glob("parity-project-*")}


@needs_posix_project
def test_project_validate_text_輸入格式與暫存目錄():
    """T2:輸入為 JSON 原文字串或 `{"hex"}`,其他格式為 error type;案例結束後轉接器的暫存目錄不存在。"""
    before = _project_work_dirs()
    reqs = [{"id": "s", "input": "[]"}, {"id": "h", "input": {"hex": "ff"}},
            {"id": "n", "input": 1}, {"id": "x", "input": {"hex": "zz"}},
            {"id": "ok", "input": '{"schema_version": 1, "name": "v1"}'}]
    got = [strip(r) for r in responses_split_on_newline(run_adapter("project.validate_text", reqs))]
    assert got[:4] == [ok({"class": "project_error", "errors": ["專案檔必須是 JSON 物件"]}),
                       ok({"class": "project_error", "errors": ["專案檔內容損壞,無法讀取"]}),
                       error("type"), error("type")]
    assert got[4]["outcome"] == "ok"
    assert got[4]["value"]["dump"].startswith('{\n "schema_version": 2,\n "name": "v1",')
    assert _project_work_dirs() <= before


def _parts_expand(parts):
    return "".join(p if isinstance(p, str) else p["repeat"] * p["count"] for p in parts)


@needs_posix_project
def test_project_validate_text_的_parts_輸入():
    """DIR-6 第 1 輪 F-1:`{"parts"}` 依序串接後以 UTF-8 編碼;不合格一律 error type。"""
    head, tail = '{"schema_version": 2, "name": "x", "script": "', '"}'
    good = [{"id": "p", "input": {"parts": [head, {"repeat": "字", "count": 3}, "", tail]}},
            {"id": "s", "input": head + "字字字" + tail}]
    bad = [[], "x", [1], [None], [{"repeat": "a"}], [{"repeat": "a", "count": 1, "x": 1}],
           [{"repeat": "", "count": 1}], [{"repeat": 1, "count": 1}], [{"repeat": "a", "count": 0}],
           [{"repeat": "a", "count": -1}], [{"repeat": "a", "count": 1.0}], [{"repeat": "a", "count": True}],
           [{"repeat": "a", "count": 5242882}]]
    reqs = good + [{"id": f"b{i}", "input": {"parts": b}} for i, b in enumerate(bad)]
    reqs.append({"id": "both", "input": {"parts": [head, tail], "hex": ""}})
    got = [strip(r) for r in responses_split_on_newline(run_adapter("project.validate_text", reqs))]
    assert got[0] == got[1] and got[0]["outcome"] == "ok" and isinstance(got[0]["value"]["dump"], str)
    assert got[2:] == [error("type")] * (len(bad) + 1)


@needs_posix_project
def test_project_validate_text_的_dump_精簡門檻與可逆性():
    """dump 的同一字元連續 64 個照舊為字串、65 個改為 `{"parts"}`(T3 補記:原為 999 / 1000);展開即得原 dump。"""
    head, tail = '{"schema_version": 2, "name": "x", "script": "', '"}'
    reqs = [{"id": str(n), "input": {"parts": [head, {"repeat": "a", "count": n}, tail]}} for n in (64, 65, 50000)]
    got = [strip(r) for r in responses_split_on_newline(run_adapter("project.validate_text", reqs))]
    assert isinstance(got[0]["value"]["dump"], str)
    for n, r in zip((65, 50000), got[1:]):
        parts = r["value"]["dump"]["parts"]
        assert {"repeat": "a", "count": n} in parts
        assert all(isinstance(p, str) or (len(p["repeat"]) == 1 and p["count"] >= 65) for p in parts)
        assert not any(isinstance(a, str) and isinstance(b, str) for a, b in zip(parts, parts[1:])), "相鄰字串段須合併"
        assert f'"script": "{"a" * n}",' in _parts_expand(parts)
    assert _parts_expand(got[1]["value"]["dump"]["parts"]).replace("a" * 65, "a" * 64) == got[0]["value"]["dump"]


@needs_posix_project
def test_project_片段類單元先驗證再呼叫():
    """T3:片段類單元把 `segment` 放進最小專案以 `validate` 驗證;失敗回 validate 的訊息,缺 `segment` 為 error type。"""
    seg = {"id": "0123456789abcdef", "text": "是這樣啊",
           "fusions": [{"index": 1, "length": 2, "zhuyin": "ㄐㄧㄤˋ", "source": "rule"}]}
    reqs = [{"id": "ok", "input": {"segment": seg}}, {"id": "bad", "input": {"segment": {**seg, "text": " "}}},
            {"id": "missing", "input": {"seg": seg}}, {"id": "list", "input": [seg]}]
    got = [strip(r) for r in responses_split_on_newline(run_adapter("project.tts_text", reqs))]
    assert got == [ok({"value": "是這[:ㄐㄧㄤˋ]啊"}),
                   ok({"class": "project_error", "errors": ["segments[0].text 是空的"]}),
                   error("type"), error("type")]
    got = [strip(r) for r in responses_split_on_newline(run_adapter("project.fused_out", reqs[:1]))]
    assert got == [ok({"value": [2]})], "set 以遞增陣列回傳"


@needs_posix_project
def test_project_文字欄位的_parts_輸入與結果精簡():
    """T3 補記(同字元連續上限 64):`escape_text` 的輸入、`segment.text`、`shift_marks` 的 `old` / `new`、
    `split_segments` 的 `text` 可為 `{"parts"}`;接受時 `value` 內同字元連續 64 個照舊、65 個改為 `{"parts"}`(可逆)。"""
    got = [strip(r) for r in responses_split_on_newline(run_adapter("project.escape_text", [
        {"id": "64", "input": {"parts": [{"repeat": "[", "count": 64}]}},
        {"id": "65", "input": "[" * 65},
        {"id": "bad", "input": {"parts": []}},
        {"id": "extra", "input": {"parts": ["a"], "x": 1}}]))]
    assert got == [ok({"value": "［" * 64}), ok({"value": {"parts": [{"repeat": "［", "count": 65}]}}),
                   error("type"), error("type")]
    seg = {"id": "0123456789abcdef", "text": {"parts": [{"repeat": "字", "count": 499}, "。"]}}
    got = [strip(r) for r in responses_split_on_newline(run_adapter("project.tts_text", [
        {"id": "p", "input": {"segment": seg}},
        {"id": "bad", "input": {"segment": {**seg, "text": {"parts": [1]}}}},
        {"id": "obj", "input": {"segment": {**seg, "text": {"x": "a"}}}}]))]
    assert got[0] == ok({"value": {"parts": [{"repeat": "字", "count": 499}, "。"]}})
    assert _parts_expand(got[0]["value"]["value"]["parts"]) == "字" * 499 + "。"
    assert got[1:] == [error("type"), error("type")]
    got = [strip(r) for r in responses_split_on_newline(run_adapter("project.shift_marks", [
        {"id": "p", "input": {"segment": {"id": "0123456789abcdef", "text": "好"},
                              "old": {"parts": [{"repeat": "a", "count": 3}]}, "new": "aaab"}},
        {"id": "bad", "input": {"segment": {"id": "0123456789abcdef", "text": "好"}, "old": {"parts": []}, "new": ""}}]))]
    assert got == [ok({"value": {"fusions": [], "fusion_off": [], "cleared": 0}}), error("type")]
    got = [strip(r) for r in responses_split_on_newline(run_adapter("project.split_segments", [
        {"id": "p", "input": {"text": {"parts": [{"repeat": "字", "count": 500}]}}}]))]
    assert got[0]["value"]["value"][0] == {"text": {"parts": [{"repeat": "字", "count": 500}]}, "break": "end",
                                           "start": 0, "end": 500}


def _project_fs_work_dirs():
    return {p.name for p in Path(tempfile.gettempdir()).glob("parity-project-fs-py-*")}


@needs_posix_project
def test_project_讀取類單元_暫存目錄刪除且讀取前後樹不變():
    """T4:讀取類 5 個單元的每個案例,`changed` 為空(讀取不改動任何東西)、樹含 root/ 與 outside/;
    案例結束後工作目錄不存在。"""
    before = _project_fs_work_dirs()
    for name in ("load", "load_version", "list_history", "list_projects", "disk_usage"):
        fixture = json.loads((PROJECT_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        got = responses_split_on_newline(run_adapter(f"project.{name}", fixture["cases"]))
        for case, resp in zip(fixture["cases"], got):
            if resp["outcome"] != "ok":
                assert resp == {"id": case["id"], "outcome": "error", "class": "type"}, case["id"]
                continue
            assert resp["value"]["changed"] == [], case["id"]
            assert ["root", "dir", None] in resp["value"]["tree"], case["id"]
    assert _project_fs_work_dirs() <= before


@needs_posix_project
def test_project_讀取類單元的輸入格式():
    reqs = [{"id": "no_tree", "input": {"name": "a"}}, {"id": "bad_tree", "input": {"tree": 5, "name": "a"}},
            {"id": "bad_item", "input": {"tree": [{"path": "a", "type": "fifo"}], "name": "a"}},
            {"id": "dotdot", "input": {"tree": [{"path": "../x", "type": "dir"}], "name": "a"}},
            {"id": "bad_mtime", "input": {"tree": [{"path": "a", "type": "file", "mtime": 1.5}], "name": "a"}},
            {"id": "name_int", "input": {"tree": [], "name": 1}},
            {"id": "ok", "input": {"tree": [], "name": "a"}}]
    got = [strip(r) for r in responses_split_on_newline(run_adapter("project.load", reqs))]
    assert got[:6] == [error("type")] * 6
    assert got[6] == ok({"result": {"class": "not_found", "errors": ["找不到這個專案"]},
                         "tree": [["outside", "dir", None], ["root", "dir", None]], "changed": []})


T6_CLOCK = {"now": [1793102400000], "rand4": ["abcd"], "hex8": ["0000000000000001", "0000000000000002"]}


@needs_posix_project
def test_project_寫入類單元_替身還原且工作目錄刪除():
    """T6:案例結束後(含中途 error type)`project.ROOT`、`project.time`、`project.secrets` 與 logger 的 handler 還原,
    工作目錄不存在;時鐘序列用完後重複最後一個。"""
    probe = (
        "import json, runpy, sys, logging\n"
        "g = runpy.run_path(sys.argv[1], run_name='adapter_probe')\n"
        "import project as pj\n"
        "saved = (pj.ROOT, pj.time, pj.secrets, list(logging.getLogger('project').handlers))\n"
        "clock = json.loads(sys.argv[2])\n"
        "ok = g['project_save_sequence']({'tree': [], 'clock': clock, 'ops': [\n"
        "    {'op': 'save', 'project': {'schema_version': 2, 'name': 'p'}},\n"
        "    {'op': 'save', 'project': {'schema_version': 2, 'name': 'p'}, 'script': 'a'},\n"
        "    {'op': 'save', 'project': {'schema_version': 2, 'name': 'p'}, 'script': 'b'}]})\n"
        "try:\n"
        "    g['project_save_sequence']({'tree': [], 'clock': clock, 'ops': [\n"
        "        {'op': 'save', 'project': {'schema_version': 2, 'name': 'p'}}, {'op': 'nope'}]})\n"
        "except g['UnitError'] as e:\n"
        "    assert e.cls == 'type'\n"
        "now = (pj.ROOT, pj.time, pj.secrets, list(logging.getLogger('project').handlers))\n"
        "assert now == saved, '替身須還原'\n"
        "print(json.dumps([s.get('value') for s in ok['steps']]))\n"
    )
    before = _project_fs_work_dirs()
    proc = subprocess.run(
        [sys.executable, "-c", probe, str(ADAPTER), json.dumps(T6_CLOCK)],
        capture_output=True, timeout=60, check=False, cwd=str(ADAPTER.parent),
        env=dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1"),
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert json.loads(proc.stdout.decode("utf-8")) == [None, "20261027-120000-000-abcd", "20261027-120000-001-abcd"]
    assert _project_fs_work_dirs() <= before


@needs_posix_project
def test_project_寫入類單元的輸入格式():
    ok_ops = [{"op": "list_history", "name": "p"}]
    bad = [
        {"tree": [], "ops": ok_ops},
        {"tree": [], "clock": {**T6_CLOCK, "now": []}, "ops": ok_ops},
        {"tree": [], "clock": {**T6_CLOCK, "now": [4102444800000]}, "ops": ok_ops},
        {"tree": [], "clock": {**T6_CLOCK, "now": [True]}, "ops": ok_ops},
        {"tree": [], "clock": {**T6_CLOCK, "rand4": ["ABCD"]}, "ops": ok_ops},
        {"tree": [], "clock": {**T6_CLOCK, "hex8": ["0"]}, "ops": ok_ops},
        {"tree": [], "clock": {**T6_CLOCK, "x": 1}, "ops": ok_ops},
        {"tree": [], "clock": T6_CLOCK, "ops": []},
        {"tree": [], "clock": T6_CLOCK, "ops": [{"op": "nope"}]},
        {"tree": [], "clock": T6_CLOCK, "ops": [{"op": "save", "project": [1]}]},
        {"tree": [], "clock": T6_CLOCK, "ops": [{"op": "save", "project": {}, "x": 1}]},
        {"tree": [], "clock": T6_CLOCK, "ops": [{"op": "update", "name": "p", "change": {"x": 1}}]},
        {"tree": [], "clock": T6_CLOCK, "ops": [{"op": "update", "name": "p", "change": {"speed": True}}]},
        {"tree": [], "clock": T6_CLOCK, "ops": ok_ops, "extra": 1},
    ]
    reqs = [{"id": str(i), "input": b} for i, b in enumerate(bad)]
    reqs.append({"id": "ok", "input": {"tree": [], "clock": T6_CLOCK, "ops": ok_ops}})
    got = [strip(r) for r in responses_split_on_newline(run_adapter("project.save_sequence", reqs))]
    assert got[:-1] == [error("type")] * len(bad)
    assert got[-1] == ok({"steps": [{"value": []}], "tree": [["outside", "dir", None], ["root", "dir", None]]})
    creq = [{"id": "d", "input": {"tree": [], "clock": T6_CLOCK, "name": "p", "defaults": {"x": 1}}},
            {"id": "n", "input": {"tree": [], "clock": T6_CLOCK, "name": 1}},
            {"id": "s", "input": {"tree": [], "clock": T6_CLOCK, "name": "p", "defaults": {"speed": "1"}}}]
    got = [strip(r) for r in responses_split_on_newline(run_adapter("project.create", creq))]
    assert got == [error("type")] * 3


@needs_posix_project
def test_project_split_segments_的_forced_只接受_64_位元整數():
    """T3:Rust API 為 `&[i64]`;布林、浮點、字串、超出 64 位元一律 error type,範圍外的整數為 ProjectError。"""
    text = "大家好，歡迎。"
    bad = [[True], [1.0], ["3"], 3, [2 ** 63], [-(2 ** 63) - 1]]
    reqs = [{"id": f"b{i}", "input": {"text": text, "forced": b}} for i, b in enumerate(bad)]
    reqs += [{"id": "min", "input": {"text": text, "forced": [-(2 ** 63)]}},
             {"id": "parts", "input": {"text": {"parts": [{"repeat": " ", "count": 50001}]}}},
             {"id": "none", "input": {"text": text}}]
    got = [strip(r) for r in responses_split_on_newline(run_adapter("project.split_segments", reqs))]
    assert got[:len(bad)] == [error("type")] * len(bad)
    assert got[len(bad):] == [ok({"class": "project_error", "errors": ["強制停頓位置超出文稿範圍"]}),
                              ok({"class": "project_error", "errors": ["文稿超過 50000 字"]}),
                              ok({"value": [{"text": "大家好，歡迎。", "break": "sentence", "start": 0, "end": 7}]})]


@needs_posix_project
def test_轉接器延後_import_的_project_是_repo_根目錄的產品模組():
    probe = (
        "import runpy, sys\n"
        "g = runpy.run_path(sys.argv[1], run_name='adapter_probe')\n"
        "assert 'project' not in sys.modules, '不得在檔頂 import project'\n"
        "g['project_constants'](None)\n"
        "print(sys.modules['project'].__file__)\n"
        "print(sys.modules['breezy_worker'].__file__)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe, str(ADAPTER)],
        capture_output=True, timeout=60, check=False, cwd=str(ADAPTER.parent),
        env=dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1"),
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    files = [Path(line).resolve() for line in proc.stdout.decode("utf-8").splitlines()]
    assert files == [ROOT / "project.py", ROOT / "breezy_worker.py"]


def project_imports_outside_units(source):
    """回傳 `import project` 出現在 `project_*` / `_project_*` 函式以外的行號(含檔頂)。"""
    tree = ast.parse(source)
    allowed = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith(("project_", "_project_")):
            allowed.update(id(n) for n in ast.walk(node))
    bad = []
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module.split(".")[0]]
        if "project" in names and id(node) not in allowed:
            bad.append(node.lineno)
    return bad


def test_project_只在_project_單元內延後_import():
    source = ADAPTER.read_text(encoding="utf-8")
    assert project_imports_outside_units(source) == []
    assert "import project as pj" in source
    # 反向確認:在暫存副本(只在記憶體中)的檔頂或其他函式加 import project,斷言會被找出
    assert project_imports_outside_units("import project\n" + source) == [1]
    assert project_imports_outside_units(source + "\ndef other():\n    import project\n") != []


# ---------- 約束:只 import 標準函式庫與明列的產品模組 ----------

# 轉接器可 import 的產品模組(CHG-20261124-01 代決 13、CHG-20261125-01 代決 11;
# 只放三平台都能載入、不依賴 fcntl 等的模組)。`project` 依賴 fcntl,只能在 project.* 單元內延後 import
# (CHG-20261128-01 代決 10,由 test_project_只在_project_單元內延後_import 斷言)。
ALLOWED_PRODUCT_MODULES = frozenset({"names", "value_checks", "safe_fs", "project"})


def imports_outside_allowed(source):
    """回傳轉接器原始碼中,標準函式庫與明列產品模組以外的 import(排序後的頂層模組名)。"""
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "不得相對 import"
            names.add(node.module.split(".")[0])
    names.discard("__future__")
    assert names, "應至少 import 標準函式庫模組"
    return sorted(n for n in names
                  if n not in sys.stdlib_module_names and n not in ALLOWED_PRODUCT_MODULES)


def test_轉接器只_import_標準函式庫與明列的產品模組():
    outside = imports_outside_allowed(ADAPTER.read_text(encoding="utf-8"))
    assert outside == [], f"轉接器只能 import 標準函式庫與 {sorted(ALLOWED_PRODUCT_MODULES)}:{outside}"


def test_明列集合以外的_import_仍會被擋():
    # 反向確認:在轉接器原始碼的暫存副本(只在記憶體中,不寫檔)加一行 import requests,斷言會被找出。
    source = ADAPTER.read_text(encoding="utf-8") + "\nimport requests\n"
    assert imports_outside_allowed(source) == ["requests"]
