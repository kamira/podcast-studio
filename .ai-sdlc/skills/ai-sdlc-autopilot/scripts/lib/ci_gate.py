#!/usr/bin/env python3
"""合併前的 CI 閘(CHG-20260803-04)。stdlib-only、無 LLM。

**為什麼存在**:實際發生過——在 `tests (windows-latest)` 還是 pending 時就按下合併。
事後那個 job 是綠的,但那是運氣不是判斷。合併難以回收,而「還沒跑完」與「跑過了是綠的」
在人眼裡很容易看成同一件事,尤其當其他三個 job 已經打勾的時候。

**與哨兵的方向相反**:`autopilot_sentinels` 查不到狀態時 fail-open(Tier A 退回基線),
因為輪詢不該擋住任何人。**這裡必須 fail-closed**:查不到 CI 狀態 = 不准合併。
同一套「無法評估」的處置,在不同的閘上有相反的正確答案,取決於**失敗的代價往哪邊倒**。
"""
from __future__ import annotations
import json
import shutil
import subprocess

# gh 的 bucket 值:pass / fail / pending / skipping / cancel
GREEN = {"pass", "skipping"}
BLOCKING = {"fail", "cancel"}


def query_cmd(pr: str | None = None) -> list[str]:
    """用 `shutil.which` 解析出的**絕對路徑**當 argv[0],不是裸的 "gh"。

    Windows 的 `CreateProcess` 不做 PATHEXT 解析:`gh` 若是 `gh.bat`/`gh.cmd`,
    `shutil.which("gh")` 找得到(它認 PATHEXT),但 `subprocess.run(["gh", ...])`
    會直接 WinError 2。兩者不一致會讓「gh 存在」與「gh 叫得動」分岔。
    """
    exe = shutil.which("gh") or "gh"
    ref = [pr] if pr else []
    return [exe, "pr", "checks", *ref, "--json", "name,bucket,state"]


def parse_checks(raw: str | None):
    """把 `gh` 的輸出解析成 checks。回 (checks, error)。

    **從 `fetch` 抽出來,不是為了整潔,是為了讓它進得了 fuzz 的射程**
    (CHG-20260808-04,待補項 #25 的高風險兩條之一)。`fetch` 的輸入來自
    `subprocess`,注入不了;而**用 `override` 餵變異文字等於把 fuzz 產物當
    shell 指令執行**——那條路直接排除。

    抽出來的副作用是好的:`fetch` 從此沒有解析呼叫,於是它**自動離開 fuzz 清冊**。
    修法把 gap 消掉,而不是登記它。

    輸入是**外部工具的輸出**,所以合法 JSON 也可能不是陣列——
    `json.loads` 成功不代表拿到預期的形狀,那是本 repo 一次抓到 7 個同族崩潰的根因
    (CHG-20260808-01)。
    """
    try:
        data = json.loads(raw or "[]")
    except ValueError as e:
        return None, f"CI 狀態不是合法 JSON({e})"
    if not isinstance(data, list):
        return None, "CI 狀態格式非預期(應為陣列)"
    return data, None


def fetch(repo, pr: str | None = None, override: str | None = None):
    """回 (checks, error)。checks 為 [{name,bucket}, ...];取不到時 error 非 None。"""
    if override:
        try:
            r = subprocess.run(override, shell=True, cwd=str(repo), capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=120)
        except Exception as e:  # noqa: BLE001
            return None, f"自訂 CI 查詢指令無法執行:{e}"
        if r.returncode not in (0, 8):     # gh 在有失敗檢查時回 8,仍有 JSON
            return None, f"自訂 CI 查詢指令回 {r.returncode}:{(r.stderr or '').strip()[:200]}"
        raw = r.stdout
    else:
        if shutil.which("gh") is None:
            return None, "找不到 gh CLI,無法確認 CI 狀態"
        try:
            r = subprocess.run(query_cmd(pr), cwd=str(repo), capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=120)
        except Exception as e:  # noqa: BLE001
            return None, f"查詢 CI 狀態失敗:{e}"
        if r.returncode not in (0, 8):
            return None, f"查詢 CI 狀態失敗(gh 回 {r.returncode}):{(r.stderr or '').strip()[:200]}"
        raw = r.stdout
    return parse_checks(raw)


def decide(checks, error, allow_no_ci: bool = False) -> tuple[bool, str]:
    """回 (可否合併, 訊息)。**任何非「全部完成且綠」的狀態都不放行。**"""
    if error is not None:
        return False, (f"合併前**無法確認** CI 狀態:{error}\n"
                       f"  查不到狀態不等於狀態沒問題。合併難以回收,故此處 fail-closed。\n"
                       f"  若此專案沒有 CI,請明示 --allow-no-ci(會留痕)。")
    if not checks:
        if allow_no_ci:
            return True, "CI 閘:此專案沒有任何檢查,已由 --allow-no-ci 明示放行(逃生口,須記入 ACC)"
        return False, ("合併前查無任何 CI 檢查。\n"
                       "  「沒有檢查」與「檢查都通過」不是同一件事——前者代表沒有人在看。\n"
                       "  若此專案確實不接 CI,請明示 --allow-no-ci(會留痕)。")

    pending = [c.get("name", "?") for c in checks
               if (c.get("bucket") or "").lower() not in GREEN | BLOCKING]
    failing = [c.get("name", "?") for c in checks
               if (c.get("bucket") or "").lower() in BLOCKING]
    if failing:
        return False, f"CI 有 {len(failing)} 項失敗,不得合併:{', '.join(failing)}"
    if pending:
        return False, (f"CI 尚有 {len(pending)} 項**未完成**,不得合併:{', '.join(pending)}\n"
                       f"  pending 不是綠燈。其他項目已經打勾時最容易誤判——"
                       f"  這道閘就是為了那一刻而存在。")
    return True, f"CI 閘:{len(checks)} 項檢查全數完成且為綠"


def check(repo, pr=None, override=None, allow_no_ci=False) -> tuple[bool, str]:
    checks, error = fetch(repo, pr, override)
    return decide(checks, error, allow_no_ci)
