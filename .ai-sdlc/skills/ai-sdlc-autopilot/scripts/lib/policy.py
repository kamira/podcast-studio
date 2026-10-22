#!/usr/bin/env python3
"""停點政策:永遠停點(硬編碼)、風險×階段矩陣、Autonomy 加嚴。治理語意錨定 ai-sdlc autonomy.md。"""
from __future__ import annotations
import json
from pathlib import Path

from .plan import AUTONOMY_HALT_RE

# 永遠停點:硬編碼——任何設定檔不可移除或放寬
PERMANENT_HALTS = ("irreversible-delete", "payments", "prod-migration", "security-boundary")
STAGES = ("confirm_gate", "task_review", "operational_verify", "acceptance", "pr", "merge")
ACTIONS = {"auto", "confirm", "halt", "halt_independent"}
# medium 的 merge 由 auto 收緊為 halt(CHG-20260803-02 T5)。
# 原值與 ai-sdlc 的 halt_policy.json(before_merge_or_release/medium = halt)矛盾——
# drive 層比治理層寬鬆,等同讓中風險變更繞過治理層的合併停點。本 skill 自己的
# 「tighten only」與「讀 ai-sdlc、不修改它」都蘊含 drive 層不得放寬。
# 由 features/halt_policy.feature 的跨層場景持續把關,再漂移就會紅。
DEFAULT_POLICY = {
    "low":    {"confirm_gate": "auto",    "task_review": "auto", "operational_verify": "auto", "acceptance": "auto",             "pr": "auto", "merge": "auto"},
    "medium": {"confirm_gate": "confirm", "task_review": "auto", "operational_verify": "auto", "acceptance": "auto",             "pr": "auto", "merge": "halt"},
    "high":   {"confirm_gate": "halt",    "task_review": "auto", "operational_verify": "halt", "acceptance": "halt_independent", "pr": "auto", "merge": "halt"},
}


def parse_policy_text(text: str) -> tuple[dict | None, str | None]:
    """把 policy 檔內容解析成矩陣。回 `(矩陣 or None, 錯誤訊息 or None)`,**絕不拋例外**。

    從 `load_policy` 抽出來的純解析(CHG-20260810-04,待補項 #25)。
    分工是刻意的:**解析**是 fuzz 關心的(任何輸入都不得崩潰),
    **拋例外**是給呼叫端的契約(`test_lib_units` 兩條斷言釘著,不動它)。

    抽出來時 fuzz 當場抓到三種崩潰,全都是未捕捉的:
      · `json.loads` **完全沒有 try**——任何非 JSON 的 policy 檔直接 traceback,
        而 `halt_gate` / `roles` 兩個呼叫端都沒接(367/400 輪失敗)。
      · 合法 JSON 但不是 dict(例如一個陣列)→ `data.get` 拋 `AttributeError`。
      · `permanent_halts` 不是可迭代物件 → `set()` 拋 `TypeError`。
    **合法 JSON 不等於預期形狀**——本輪這一族在三個檔案裡各出現一次。
    """
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        return None, f"policy 不是合法 JSON:{exc}"
    if not isinstance(data, dict):
        return None, f"policy 必須是物件,實得 {type(data).__name__}"
    cfg_perm = data.get("permanent_halts")
    if cfg_perm is not None:
        try:
            shrunk = not set(PERMANENT_HALTS) <= set(cfg_perm)
        except TypeError:
            return None, "permanent_halts 必須是字串集合"
        if shrunk:
            return None, ("policy 不得縮減 permanent_halts(硬清單:%s)"
                          % ", ".join(PERMANENT_HALTS))
    matrix = data.get("defaults", DEFAULT_POLICY)
    err = validate_matrix(matrix)
    return (None, err) if err else (matrix, None)


def validate_matrix(matrix) -> str | None:
    """值域檢查。回錯誤訊息或 None。"""
    if not isinstance(matrix, dict):
        return f"policy 的 defaults 必須是物件,實得 {type(matrix).__name__}"
    for risk, stages in matrix.items():
        if not isinstance(stages, dict):
            return f"policy 值域錯誤:{risk} 必須是物件"
        for st, act in stages.items():
            if st not in STAGES or act not in ACTIONS:
                return f"policy 值域錯誤:{risk}.{st}={act}"
    return None


def load_policy(path):
    """載入停點矩陣;驗證值域,且 permanent_halts 不可縮減(硬清單以程式碼為準)。

    **對呼叫端的契約不變:壞 policy 一律 `raise ValueError`。** 停點矩陣壞掉時
    靜默用預設值,等於在使用者以為自己收緊了閘的情況下放行——這裡 fail-loud 是對的。
    改變的只是「解析」被抽了出去(見 `parse_policy_text`)。
    """
    if not path:
        err = validate_matrix(DEFAULT_POLICY)
        if err:
            raise ValueError(err)
        return DEFAULT_POLICY
    matrix, err = parse_policy_text(Path(path).read_text(encoding="utf-8"))
    if err:
        raise ValueError(err)
    return matrix


def stage_action(matrix, risk: str, stage: str, chg_text: str) -> str:
    act = matrix.get(risk, matrix["high"]).get(stage, "halt")
    if AUTONOMY_HALT_RE.search(chg_text) and stage in ("confirm_gate", "merge"):
        act = "halt"  # Autonomy 欄只准加嚴
    return act
