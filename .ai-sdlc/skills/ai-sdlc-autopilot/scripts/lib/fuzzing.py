#!/usr/bin/env python3
"""模糊測試引擎(CHG-20260804-04)。stdlib `random`,固定種子。

抽成獨立模組的理由是 KN-001:**一道第一次跑就全綠的閘,得先證明它的紅燈可達。**
引擎與目標清單分開,測試才能注入一個故意會炸的目標,證明它真的抓得到——
否則「0 個失敗」與「引擎壞掉」在輸出上完全一樣。
"""
from __future__ import annotations
import random
import string
import traceback

# 控制字元、CJK、代理對邊界、以及解析器最在意的分隔符都要在字母表裡
ALPHABET = string.printable + "。、「」《》——…‥·中文𝔘𝔫𝔦\x00\x1b\r\n\t|:"


def mutate(rnd: random.Random, text: str) -> str:
    """七種變異:截斷、插入雜訊、刪字、重複、換行洗牌、破壞分隔符、整段亂碼。"""
    op = rnd.randrange(7)
    if not text and op not in (1, 6):
        op = 1
    if op == 0:
        return text[:rnd.randrange(len(text) + 1)]
    if op == 1:
        pos = rnd.randrange(len(text) + 1)
        junk = "".join(rnd.choice(ALPHABET) for _ in range(rnd.randrange(1, 20)))
        return text[:pos] + junk + text[pos:]
    if op == 2:
        pos = rnd.randrange(len(text))
        return text[:pos] + text[pos + 1:]
    if op == 3:
        return text * rnd.randrange(2, 5)
    if op == 4:
        lines = text.split("\n")
        rnd.shuffle(lines)
        return "\n".join(lines)
    if op == 5:
        return text.replace(":", rnd.choice(["", "::", ":\n", "\x00"]))
    return "".join(rnd.choice(ALPHABET) for _ in range(rnd.randrange(0, 3000)))


def run_fuzz(targets, seeds, cases: int, seed: int) -> dict:
    """對每個目標餵變異過的輸入。不變式:**不得拋出未捕捉的例外**。

    回空、回 None、回報錯誤都可以;炸掉不行——崩潰會讓整條治理流程停在一個
    沒有指引的 traceback 上,而那與「正確擋下」在退出碼上分不開(KN-003)。
    """
    rnd = random.Random(seed)
    failures, calls = [], 0
    for _ in range(cases):
        text = mutate(rnd, rnd.choice(seeds))
        for name, fn in targets:
            calls += 1
            try:
                fn(text)
            except Exception:                      # noqa: BLE001 —— 這正是要抓的
                failures.append({
                    "target": name,
                    "input_repr": repr(text[:160]),
                    "exception": traceback.format_exc().strip().splitlines()[-1]})
    return {"version": 1, "seed": seed, "cases": calls,
            "targets": [n for n, _ in targets],
            "invariant": "解析器面對任何輸入都不得拋出未捕捉的例外",
            "failures": failures}
