#!/usr/bin/env python3
"""性質 → 機制 → 三態,外加未涵蓋棘輪(CHG-20260807-02 T2)。

`capabilities.py` 只回答「支不支援」。這一層回答更重要的那個問題:
**不支援的時候,還有沒有別的機制能驗到同一個性質?**

## 為什麼機制要跟性質分開

那條界線斷言原本寫成「用 symlink 驗」。但它要驗的**性質**是
「自動發現不得走進指到 repo 外的重解析點」——symlink 只是驗它的一種**機制**。
機制綁死在性質上,就會出現這個假等式:**機制不支援 = 性質沒驗**。
本機實測正好是反例:symlink 建不出來(`WinError 1314`),
但 junction 建得出來且**不需要任何權限**,同一個性質驗得到。

## 支援多種就全部跑,不挑一種

`os.path.islink()` 對 junction 回 **False**。所以一個用 `is_symlink()` 判界線的實作
**會被 junction 繞過、卻被 symlink 抓到**——兩種機制測得到的洞不一樣。
挑一種等於自願放棄另一半的偵測力。

## 未涵蓋棘輪:這一層能不能被信任的關鍵

三態一旦存在,「把紅燈改成不支援」就成了讓它消失的按鈕。所以未涵蓋**不是自由欄位**:
已知項要在基線裡、每條要有署名理由、**基線外的新增一律擋**。
與本 repo 既有的 coverage 棘輪、`static_allowlist.json` 同形。

基線**只准往下**:一個離開清單的項目要被報出來(它代表某個環境變好了,
或某條驗證真的補上了),否則基線會留著一堆早就不成立的豁免——
一份說「還沒修」的登記簿與一份說「沒問題」的空報告是同一種錯。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, NamedTuple

BASELINE = Path(__file__).resolve().parent.parent.parent / "assets" / "uncovered_baseline.json"

PASS = "pass"
FAIL = "fail"
UNCOVERED = "uncovered"


class Mechanism(NamedTuple):
    """一種驗某個性質的做法。`requires` 是它需要的能力名。"""
    name: str
    requires: tuple[str, ...]
    run: Callable[[], bool]


class Outcome(NamedTuple):
    state: str            # pass / fail / uncovered
    used: tuple[str, ...]     # 實際跑過的機制
    skipped: tuple[str, ...]  # 因缺能力而沒跑的機制(具名,不靜靜跳過)
    detail: str


def run_property(prop: str, mechanisms, supports) -> Outcome:
    """跑一個性質:**所有支援的機制都跑**;一種都不支援才是未涵蓋。

    `supports(cap) -> bool` 由呼叫端注入(正常是 `capabilities.supports`)。
    """
    usable: list[Mechanism] = []
    skipped: list[str] = []
    for m in mechanisms:
        missing = [c for c in m.requires if not supports(c)]
        if missing:
            skipped.append(f"{m.name}(缺 {', '.join(missing)})")
        else:
            usable.append(m)
    # 用另一個名字而不是就地覆寫成 tuple:`Outcome` 的欄位型別是 tuple,
    # 而一個先是 list、後來變 tuple 的變數會讓型別檢查器每次都得猜。
    skipped_names: tuple[str, ...] = tuple(skipped)

    if not usable:
        return Outcome(
            UNCOVERED, (), skipped_names,
            f"{prop}:**未涵蓋** —— 試過的機制全部不支援:{'; '.join(skipped_names)}。"
            f"未涵蓋不是通過,也不是失敗;要補的是環境或是再找一種機制。")

    failures = []
    for m in usable:
        try:
            ok = bool(m.run())
        except Exception as exc:
            # 機制自己炸掉**算失敗,不算未涵蓋**:未涵蓋的意思是「這台機器做不到」,
            # 而一個支援的機制執行時崩潰是缺陷訊號,不能被降級成環境問題(KN-003)。
            failures.append(f"{m.name}(機制拋出 {type(exc).__name__}: {exc})")
            continue
        if not ok:
            failures.append(m.name)

    used = tuple(m.name for m in usable)
    if failures:
        return Outcome(FAIL, used, skipped_names,
                       f"{prop}:**未通過** —— 機制 {', '.join(failures)} 判定失敗。")
    return Outcome(PASS, used, skipped_names,
                   f"{prop}:通過(機制:{', '.join(used)})"
                   + (f";未跑:{'; '.join(skipped_names)}" if skipped_names else ""))


def load_baseline(path=None, scope=None) -> dict:
    """讀未涵蓋基線。給 `scope` 時只回該範圍的條目。

    **為什麼要分 scope**:棘輪會報告「已離開清單的項目,請從基線移除」,
    而那個判斷只有在**同一個收集者**的範圍內才成立。
    測試檔收集不到 `ci_local.sh` 的環境層未涵蓋項,若共用一個命名空間,
    它每跑一次都會宣告那些項目「已離開清單」——一個恆假的建議,
    而恆假的建議會訓練人忽略所有建議。
    """
    p = Path(path) if path else BASELINE
    try:
        entries = json.loads(p.read_text(encoding="utf-8-sig")).get("entries", {})
    except (OSError, ValueError):
        # 讀不到基線時**不得放行**:那會讓「刪掉基線檔」變成關閉棘輪的方法。
        return {"__unreadable__": {}}
    if scope is None:
        return entries
    return {k: v for k, v in entries.items() if (v or {}).get("scope") == scope}


def judge_uncovered(uncovered, baseline) -> tuple[bool, str]:
    """未涵蓋棘輪。回 (放行嗎, 訊息)。

    三種擋下:基線讀不到、出現基線外的新未涵蓋、基線條目沒有署名理由(KN-006)。
    另外報告「離開清單」的項目——基線只准往下。
    """
    if "__unreadable__" in baseline:
        return False, ("未涵蓋基線讀不到——**不得因此放行**:讀不到基線與沒有未涵蓋項\n"
                       "  在退出碼上長得一樣,而前者代表棘輪整個失效。")

    names = sorted(set(uncovered))
    unsigned = [k for k in names if k in baseline and not (baseline[k] or {}).get("reason")]
    if unsigned:
        return False, ("下列未涵蓋項在基線內但**沒有署名理由**:\n  · "
                       + "\n  · ".join(unsigned)
                       + "\n  空白豁免與沒宣告等價,但看起來像有交代——那比漏寫更糟(KN-006)。")

    new = [k for k in names if k not in baseline]
    if new:
        return False, ("出現**基線外的新未涵蓋項**,擋下:\n  · "
                       + "\n  · ".join(new)
                       + "\n  未涵蓋不得靜默長大——這正是「把紅燈改成不支援」會走的那條路。\n"
                         "  真的無法涵蓋,請寫進 assets/uncovered_baseline.json 並具名理由與 CHG 編號。")

    gone = [k for k in baseline if k not in names]
    msg = f"未涵蓋 {len(names)} 項,全部在基線內且具名。"
    if gone:
        msg += ("\n  下列項目**已離開未涵蓋清單**(環境變好或驗證補上了),"
                "請從基線移除:\n  · " + "\n  · ".join(sorted(gone)))
    return True, msg


def require_runnable(count: int, what: str = "檢查") -> tuple[bool, str]:
    """可跑集為空 = 失敗。

    「偵測壞了」與「全部合格」在退出碼上長得一樣,而後者更難發現——
    沒有人會去追查一個綠燈(KN-001)。
    """
    if count > 0:
        return True, f"可跑的{what}:{count} 項"
    return False, (f"可跑的{what}為 **0 項** —— 視同失敗。\n"
                   f"  這不是「全部通過」,是偵測或發現階段壞了;"
                   f"兩者在退出碼上一樣,所以這裡明確擋下。")
