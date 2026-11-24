# -*- coding: utf-8 -*-
"""並行比對工具的 Python 轉接器(CHG-20261123-01 T2,代決 5、6)。

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

只用標準函式庫;路線圖第 2 筆起可 import 被比對的產品模組;本筆不 import 任何產品模組。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time


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


def build_units(environ=None):
    """單元登錄字典(單元名 → 函式)。路線圖第 2 筆起,各模組的 Python 端單元在此登錄。"""
    environ = os.environ if environ is None else environ
    units = {
        "demo.parse_count": demo_parse_count,
        "demo.parse_count_drift": demo_parse_count_drift,
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
