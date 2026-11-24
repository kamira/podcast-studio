# -*- coding: utf-8 -*-
"""並行比對工具 Python 轉接器的測試(CHG-20261123-01 T2)。

L1:示範單元 `demo.parse_count`、`demo.parse_count_drift` 各案例的 Python 端期望結果(上限都是 1000)。
L2(提供方側):轉接器輸出符合 JSON 行協定——每行一個 JSON 物件、換行一律 `\\n`、UTF-8、
三型回應欄位齊全、id 依序對應;未知單元 exit 2;`demo.sleep` 只在 `PARITY_TEST_SLEEP=1` 時登錄。
約束:轉接器只用標準函式庫(本筆不 import 任何產品模組)。

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


# ---------- 約束:只用標準函式庫 ----------

def test_轉接器只_import_標準函式庫():
    tree = ast.parse(ADAPTER.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "不得相對 import"
            names.add(node.module.split(".")[0])
    names.discard("__future__")
    assert names, "應至少 import 標準函式庫模組"
    outside = sorted(n for n in names if n not in sys.stdlib_module_names)
    assert outside == [], f"轉接器只能用標準函式庫:{outside}"
