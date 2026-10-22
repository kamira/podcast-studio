#!/usr/bin/env python3
"""合併前的本機自主檢查閘(CHG-20260806-04)。無 LLM、stdlib-only。

**為什麼在 CI 閘之外還要這一道**:CHG-20260806-01 帶著一項具名的「未涵蓋」合併進 main
——行為規格層沒有執行。ACC 寫明代償量測抓不到三件事(未定義步驟、`environment.py`
相依、場景大綱綁定)。環境補起來後 behave 第一次真的跑,抓到的第一個東西就在那張清單上:
步驟片語撞名 → behave 在載入階段中止 → 23 份 feature 一份都沒被執行。那筆 CHG 當時
已經在 main 裡了。

合併前原本只有一道閘(CI 全綠,CHG-20260803-04),而它有兩個洞:
  1. CI 可能根本跑不起來,理由與程式無關(額度、帳務、runner 排隊)。
     實測:PR #17/#18 的所有 job 都在 3–9 秒失敗於 `recent account payments have failed`。
  2. CI 跑得起來時,它也不是本機——本機有的東西(開發者的 venv、真實檔案系統、
     平台特性)CI 不一定有,反過來也是。

**僅限程式**(使用者裁示)。判定不新發明第三套:沿用 `DOCS_ONLY_RE` 與 `is_light`。
每多一套判定就多一個邊界,而邊界是這個 repo 反覆失效的地方(CHG-20260805-11)。
"""
from __future__ import annotations
import re
import shutil
from pathlib import Path

from . import plan as P

# 前瞻適用起點:比照 BSPEC_SINCE / DIAGRAM_SINCE,不追殺既有 CHG
LOCAL_GATE_SINCE = (1, 22)

SECTION_RE = re.compile(r"^#{2,3}\s*(?:本機閘|Local\s+gate)\s*$",
                        re.MULTILINE | re.IGNORECASE)
CMD_RE = re.compile(r"^-\s*cmd:\s*(?P<v>\S.*?)\s*$", re.MULTILINE)
PASS_RE = re.compile(r"^-\s*pass:\s*(?P<v>\S.*?)\s*$", re.MULTILINE)

# 慣例位置的探測順序。**這是資料,不是猜測**——新增一種載具是改這張表,不是改流程。
# 每一項寫成 (人看得懂的名字, 判斷它存在的方式, 要跑的指令)。
CONVENTIONS: tuple[tuple[str, str, str], ...] = (
    (".github/ci_local.sh", "file", "bash .github/ci_local.sh"),
    ("scripts/check.sh", "file", "bash scripts/check.sh"),
    ("Makefile:check", "make-target:check", "make check"),
    ("Makefile:ci", "make-target:ci", "make ci"),
    ("justfile:check", "just-target:check", "just check"),
)


def required(text: str) -> bool:
    """本 CHG 是否必須過本機閘:程式類 + Skill ≥ v1.22(前瞻適用)。

    「僅限程式」的兩個訊號都是既有的:
      · `Acceptance-operation: n/a (docs-only)` —— 純文件
      · `Template: lite / classic` —— 輕量模板
    """
    if P.DOCS_ONLY_RE.search(text):
        return False
    if P.is_light(text):
        return False
    m = P.AUTOPILOT_VER_RE.search(text)
    if m is None:
        return False        # 未宣告版本 → 行為與本閘上線前完全相同
    return (int(m.group(1)), int(m.group(2))) >= LOCAL_GATE_SINCE


def declared(text: str) -> tuple[str | None, str | None]:
    """回傳 CHG 宣告的 (cmd, pass);查無回 (None, None)。"""
    body = P.section_body(text, SECTION_RE)
    if not body:
        return None, None
    c = CMD_RE.search(body)
    p = PASS_RE.search(body)
    return (c.group("v") if c else None), (p.group("v") if p else None)


def _has_target(repo: Path, filename: str, target: str) -> bool:
    """判斷 Makefile / justfile 裡有沒有那個 target。

    只讀檔案、比對行首的 `target:`——不呼叫 `make -n`,因為那會**執行** Makefile 裡
    的求值(shell 展開、include),而這一步只是在探測,還沒到該執行任何東西的時候。
    """
    f = repo / filename
    if not f.is_file():
        return False
    try:
        src = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return re.search(rf"^{re.escape(target)}\s*:", src, re.MULTILINE) is not None


def conventional(repo: Path) -> tuple[str | None, str | None]:
    """依 CONVENTIONS 的順序找本專案的慣例載具;回傳 (名字, 指令),查無回 (None, None)。"""
    repo = Path(repo)
    for name, kind, cmd in CONVENTIONS:
        if kind == "file":
            if (repo / name).is_file():
                return name, cmd
        elif kind.startswith("make-target:"):
            if _has_target(repo, "Makefile", kind.split(":", 1)[1]) and shutil.which("make"):
                return name, cmd
        elif kind.startswith("just-target:"):
            if _has_target(repo, "justfile", kind.split(":", 1)[1]) and shutil.which("just"):
                return name, cmd
    return None, None


def resolve(text: str, repo: Path, operator_cmd: str | None,
            trust_chg: bool) -> tuple[str | None, str, str | None]:
    """決定要跑什麼。回傳 (cmd, source, blocked_reason)。

    優先序刻意是這一條:**操作者 > CHG 宣告(需明示信任)> 慣例位置 > 查無**。

    `### 本機閘` 的 `cmd` 來自 **repo 內容**,不是操作者打的。只要有人能讓一份 CHG
    進到 repo(例如來自 fork 的 PR),autopilot 就會在**合併前**執行那份檔案裡寫的
    任何東西。所以這裡沿用 CHG-20260803-05 對互動閘劃的**同一條線,一字不改**:
    預設不執行。不為新閘另開一個後門。
    """
    if operator_cmd:
        return operator_cmd, "操作者(--local-gate-cmd)", None
    chg_cmd, _ = declared(text)
    if chg_cmd:
        if not trust_chg:
            return None, "CHG 宣告", (
                "「### 本機閘」宣告了 cmd,但那是**內容驅動執行**——指令來自 repo 內容,"
                "不是你打的。合併前執行它需要明示信任。\n"
                f"    宣告的指令:{chg_cmd}\n"
                "    要嘛自己給:--local-gate-cmd '<你的指令>'\n"
                "    要嘛明示信任:--trust-chg-commands(執行前會印出原文)")
        return chg_cmd, "CHG 宣告(--trust-chg-commands)", None
    name, cmd = conventional(repo)
    if cmd:
        return cmd, f"慣例位置({name})", None
    return None, "查無", (
        "找不到本機檢查載具——合併前的本機自主檢查**沒有跑過**,而那不等於通過。\n"
        "    三條路擇一:\n"
        "      · 在 CHG 補「### 本機閘」節(`- cmd:` / `- pass:`)\n"
        "      · 用 --local-gate-cmd '<指令>' 直接給\n"
        f"      · 在專案裡建立慣例載具:{', '.join(n for n, _, _ in CONVENTIONS)}\n"
        "    真的跑不動時用 --allow-no-local-gate 明示放行(會留痕,收尾須寫進 ACC)。\n"
        "    這裡 fail-closed:合併是單向門,查不到即不准合併(與 CI 閘同向)。")
