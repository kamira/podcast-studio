"""跨模組共用的值檢查(CHG-20261103-01 T1):`project`、`settings` 共用同一個判斷來源。

只用標準庫,不 import repo 內其他模組。`breezy_worker.py` 在 BreezyVoice 的 venv 中以自己的
`sys.path` 執行,無法 import 本模組,因此保留本地副本 `_is_int`(兩者規則相同)。

介面:
  is_int(v) -> bool    v 是 int 且不是 bool 才為 True(bool 是 int 的子類別,須排除)
"""
from __future__ import annotations


def is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)
