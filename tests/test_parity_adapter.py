# -*- coding: utf-8 -*-
"""並行比對工具 Python 轉接器的測試(CHG-20261123-01 T2;CHG-20261124-01 T1 擴充)。

L1:示範單元 `demo.parse_count`、`demo.parse_count_drift` 各案例的 Python 端期望結果(上限都是 1000);
產品模組單元 `value_checks.is_int` 各案例的 Python 端期望結果(CHG-20261124-01 代決 6);
`names.name_char_ok`、`names.normalize_name`、`names.fold_name`、`names.constants` 各案例的 Python 端
期望結果與「拒絕代碼 `invalid`、錯誤類別 `type`」的對應(CHG-20261124-01 T2);`str.casefold` 逐字進行、
分批串接的結果等於逐字結果(全碼位比對分批的前提)。
L2(提供方側):轉接器輸出符合 JSON 行協定——每行一個 JSON 物件、換行一律 `\\n`、UTF-8、
三型回應欄位齊全、id 依序對應;未知單元 exit 2;`demo.sleep` 只在 `PARITY_TEST_SLEEP=1` 時登錄;
轉接器 import 的 `value_checks` 是 repo 根目錄的產品模組。
約束:轉接器只 import 標準函式庫與明列的產品模組 `{names, value_checks}`(CHG-20261124-01 代決 13)。

轉接器以子程序執行(與 `parity` 比對器相同的呼叫方式),不 import 進測試行程。
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
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
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe, str(ADAPTER)],
        capture_output=True, timeout=30, check=False, cwd=str(ADAPTER.parent),
        env=dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1"),
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    files = [Path(line).resolve() for line in proc.stdout.decode("utf-8").splitlines()]
    assert files == [ROOT / "value_checks.py", ROOT / "names.py"]


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


# ---------- 約束:只 import 標準函式庫與明列的產品模組 ----------

# 轉接器可 import 的產品模組(CHG-20261124-01 代決 13;只放三平台都能載入、不依賴 fcntl 等的模組)。
ALLOWED_PRODUCT_MODULES = frozenset({"names", "value_checks"})


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
