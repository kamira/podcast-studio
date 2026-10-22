#!/usr/bin/env python3
"""委派型驗證閘:型別 / 覆蓋率 / SAST / 相依漏洞(CHG-20260803-06)。

**為什麼委派**:這四類需要外部工具或漏洞資料庫,runner 不能內建也不該安裝。
能用 stdlib 的 AST 做到的靜態與安全檢查是**內建 always-on**(`static_check.py`);
需要工具的落在這裡,採與互動閘相同的「宣告 + 指令 + 產物」模式。

**與互動閘共用的三條規則**(刻意一致,使用者不必記兩套):
  1. 宣告的種類必須在分類表內(表可增補)
  2. 未宣告種類 → halt 並列出可選項,**不挑預設**
  3. **產物必須真的出現** —— 退出碼 0 是聲稱,產物才是證據

**與互動閘不同的一點**:互動驗證是「會被重複使用的產物」才要求;這四類是
**程式類變更一律要求**,因為型別錯誤與已知 CVE 不會因為「這段只用一次」而變得無害。
"""
from __future__ import annotations
import json
import re
import shlex
import shutil
from pathlib import Path

from . import quality_judge as QJ
from .exec_util import run_shell

TABLE = Path(__file__).resolve().parent.parent.parent / "assets" / "quality_checks.json"

SPEC_RE = re.compile(r"^###\s*Quality checks", re.MULTILINE)
KIND_RE = re.compile(r"^\s*-\s*kind:\s*(\S+)", re.MULTILINE)
CMD_RE = re.compile(r"^\s*-\s*cmd:\s*(\S.*)$", re.MULTILINE)
ARTIFACTS_RE = re.compile(r"^\s*-\s*artifacts:\s*(\S.*)$", re.MULTILINE)
EXEMPT_RE = re.compile(
    r"^[ \t]*[-*]?[ \t]*Quality-checks:[ \t]*n/?a[ \t]*[（(]?[ \t]*([^）)\n]*)",
    re.IGNORECASE | re.MULTILINE)
AUTOPILOT_VER_RE = re.compile(r"ai-sdlc-autopilot\s*v(\d+)\.(\d+)", re.IGNORECASE)
QUALITY_SINCE = (1, 9)


def load_kinds(path=None) -> dict:
    p = Path(path) if path else TABLE
    return json.loads(p.read_text(encoding="utf-8-sig")).get("kinds", {})


def required(text: str) -> bool:
    if re.search(r"Acceptance-operation:\s*n/?a|docs-only", text, re.IGNORECASE):
        return False
    m = AUTOPILOT_VER_RE.search(text)
    if m is None:
        return False
    # 明確早退而不是 `bool(m) and m.group(...)`:後者在執行期正確,但型別上
    # narrow 不掉,於是每個這樣寫的檔案都得欠一筆基線豁免(CHG-20260804-09)。
    return (int(m.group(1)), int(m.group(2))) >= QUALITY_SINCE


def exemption(text: str) -> tuple[bool, str | None]:
    """同互動閘:行內程式碼(反引號)是語法說明,不算宣告;空豁免視同未宣告。"""
    for m in EXEMPT_RE.finditer(text):
        line = text[text.rfind("\n", 0, m.start()) + 1:
                    (text.find("\n", m.start()) + 1) or len(text)]
        if "`" in line:
            continue
        reason = (m.group(1) or "").strip()
        return True, (reason or None)
    return False, None


def parse_spec(text: str) -> list[dict]:
    m = SPEC_RE.search(text)
    if not m:
        return []
    seg = text[m.start():]
    nxt = re.search(r"^###\s", seg[3:], re.MULTILINE)
    if nxt:
        seg = seg[:nxt.start() + 3]
    kinds, cmds, arts = KIND_RE.findall(seg), CMD_RE.findall(seg), ARTIFACTS_RE.findall(seg)
    return [{"kind": k,
             "cmd": cmds[i].strip() if i < len(cmds) else None,
             "artifacts": [a.strip() for a in arts[i].split(",")] if i < len(arts) else []}
            for i, k in enumerate(kinds)]


def choices_message(kinds: dict) -> str:
    lines = ["請宣告要跑哪幾類委派驗證(無預設值,必須明確指定):"]
    for key, spec in kinds.items():
        lines.append(f"  · {key} — {spec.get('label', '')}")
        for q in spec.get("asks", []):
            lines.append(f"      要回答:{q}")
        lines.append(f"      必要產物:{', '.join(spec.get('artifacts', [])) or '(未定義)'}"
                     f";可用工具舉例:{', '.join(spec.get('example_tools', []))}")
    lines.append("分類表可增補:assets/quality_checks.json,或以 --quality-kinds 指向自己的表。")
    return "\n".join(lines)


def _first_token(cmd: str):
    try:
        toks = shlex.split(cmd, posix=False)
    except ValueError:
        return None
    return toks[0].strip('"\'') if toks else None


def _launchable(cmd: str) -> bool:
    tok = _first_token(cmd)
    if not tok:
        return True
    return Path(tok).exists() or shutil.which(tok) is not None


def check_artifacts(repo: Path, artifacts: list[str]) -> list[str]:
    missing = []
    for rel in artifacts:
        if not rel:
            continue
        p = Path(repo) / rel
        if not p.exists() or (p.is_file() and p.stat().st_size == 0):
            missing.append(rel)
    return missing


def run_gate(repo: Path, text: str, kinds: dict, trust_chg: bool = False,
              baseline: dict | None = None):
    """回 (狀態, 訊息);狀態 ∈ {'ok', 'halt', 'uncovered'}。

    指令來源同互動閘:CHG 內容宣告的指令**預設不執行**(內容驅動執行),
    需 --trust-chg-commands 明示。這條規則刻意與互動閘一致,不另立第二套心智模型。
    """
    specs = parse_spec(text)
    if not specs:
        head = ("宣告了「### Quality checks」節但**未指定 kind**——必須明確選。"
                if SPEC_RE.search(text) else
                "缺「### Quality checks」節:程式類變更須宣告委派型驗證"
                "(型別 / 覆蓋率 / SAST / 相依漏洞)。")
        return "halt", head + "\n" + choices_message(kinds)

    lines, uncovered = [], []
    for spec in specs:
        kind = spec["kind"]
        if kind not in kinds:
            return "halt", f"驗證種類「{kind}」不在分類表內。\n" + choices_message(kinds)
        cmd = spec["cmd"]
        if not cmd:
            return "halt", f"種類「{kind}」未指定 cmd:沒有指令就沒有可重跑的驗證"
        if not trust_chg:
            return "halt", (
                f"種類「{kind}」的指令來自 **CHG 檔案內容**,預設不執行。\n"
                f"  同互動閘的信任邊界(CHG-20260803-05):能讓 CHG 進到 repo 的人\n"
                f"  就能讓 autopilot 執行任意 shell。\n"
                f"  宣告的指令是:{cmd}\n"
                f"  確認這份 CHG 可信後,明示 `--trust-chg-commands`。")
        want = spec["artifacts"] or kinds[kind].get("artifacts", [])
        if not want:
            return "halt", f"種類「{kind}」未宣告產物,無從稽核是否真的跑過"

        if not _launchable(cmd):
            uncovered.append(f"{kind}(工具不存在:{_first_token(cmd)})")
            continue
        print(f"  [委派驗證] 即將執行({kind}):{cmd}")
        r = run_shell(cmd, repo)
        judged = kind in QJ.JUDGED_KINDS
        if r.returncode != 0 and not judged:
            tail = (r.stdout or r.stderr or "").strip().splitlines()[-5:]
            return "halt", f"委派驗證未通過({kind}):\n  " + "\n  ".join(tail)
        # 產物**存在**這一關對所有種類都保留:指令回 0 不等於真的跑過。
        missing = check_artifacts(repo, want)
        if missing:
            return "halt", (f"{kind} 執行結束,但**宣告的產物沒有出現**:{', '.join(missing)}\n"
                            f"  指令回 0 不等於真的跑過。實際檢查的基準目錄:{repo}")
        if not judged:
            lines.append(f"  ✓ {kind}:產物齊備({', '.join(want)})")
            continue
        # 有判讀器的種類:**退出碼不定生死**。這幾個工具在有發現時本來就回非零,
        # 拿退出碼判等於這道閘從第一天就恆紅——而恆紅與恆綠一樣等於沒有訊號(KN-001)。
        # 判的是**相對基線的差集**:既有的入基線(具名理由),新增的一律擋。
        art = Path(repo) / want[0]
        try:
            # 變數名刻意不叫 text:那是本函式的參數(CHG 全文),覆寫它會在多種類
            # 迴圈的第二圈埋一個很難看出來的坑
            artifact_text = art.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            return "halt", f"{kind}:產物讀取失敗({exc})——讀不到的報告不等於沒問題"
        ok, msg, _detail = QJ.judge(kind, artifact_text, baseline or {})
        if not ok:
            return "halt", f"委派驗證未通過({kind}):\n{msg}"
        lines.append(f"  ✓ {msg}")

    if uncovered:
        return "uncovered", ("下列委派驗證**未涵蓋**(工具不存在,非通過):\n  "
                            + "\n  ".join(uncovered)
                            + ("\n" + "\n".join(lines) if lines else ""))
    return "ok", "委派驗證通過:\n" + "\n".join(lines)
