#!/usr/bin/env python3
"""互動驗證閘(CHG-20260803-03)。stdlib-only、無 LLM、不綁任何測試驅動。

**為什麼需要**:前四道閘驗的都是程式碼層。一次性腳本錯了就重寫,成本自負;但
**會被重複使用**的產物,每次複用都在放大同一個未被驗證的假設——而錯誤往往不在
邏輯,而在**使用面**:按鈕點下去沒反應、Tab 走不到、CLI 缺必填參數卻回 0、
函式庫的錯誤路徑丟出未文件化的例外。這些位置單元測試照不到,而 agent 寫測試時
傾向覆蓋它自己想得到的呼叫路徑——使用面的問題恰恰出在「使用者會這樣用,寫的人沒想到」。

**runner 在這裡只做三件事**(不安裝、不驅動、不挑工具):
  1. 宣告的種類是否存在於分類表(表可由使用者增補)
  2. 宣告的指令跑不跑得過
  3. **宣告的產物是否真的出現** —— 否則 `--interaction-cmd 'echo ok'` 就能過關,
     而那正是這套機制最想防的東西。同變異閘的邏輯:不能只信「它說它跑過了」。
"""
from __future__ import annotations
import json
import re
import shlex
import shutil
from pathlib import Path

from .exec_util import run_shell

KINDS_ASSET = Path(__file__).resolve().parent.parent.parent / "assets" / "interaction_kinds.json"

SPEC_RE = re.compile(r"^###\s*Interaction spec", re.MULTILINE)
KIND_RE = re.compile(r"^\s*-\s*kind:\s*(\S+)", re.MULTILINE)
CMD_RE = re.compile(r"^\s*-\s*cmd:\s*(\S.*)$", re.MULTILINE)
ARTIFACTS_RE = re.compile(r"^\s*-\s*artifacts:\s*(\S.*)$", re.MULTILINE)
# 豁免必須帶理由:空豁免與沒寫等價,而「等價卻看起來像有交代」正是最糟的狀態
# 注意 `[ \t]*` 而非 `\s*`:`\s` 含換行,會讓理由的擷取跨到下一行去——
# 實測把緊接的 `### Acceptance operation` 標題當成了豁免理由,於是空豁免看起來像有交代。
#
# 而且必須**錨在行首**(允許前置的清單記號)。實測的另一個坑:CHG 內
# 「說明豁免語法」的那一行——`Interaction-spec: n/a(<理由>)`——會被當成真的豁免,
# 於是任何提到這個語法的 CHG 都意外豁免了自己。行首錨定 + 排除行內反引號可以擋掉。
EXEMPT_RE = re.compile(
    r"^[ \t]*[-*]?[ \t]*Interaction-spec:[ \t]*n/?a[ \t]*[（(]?[ \t]*([^）)\n]*)",
    re.IGNORECASE | re.MULTILINE)
AUTOPILOT_VER_RE = re.compile(r"ai-sdlc-autopilot\s*v(\d+)\.(\d+)", re.IGNORECASE)
INTERACTION_SINCE = (1, 6)


def load_kinds(path=None) -> dict:
    """載入分類表。使用者可用 --interaction-kinds 指向自己的表(可增補種類)。"""
    p = Path(path) if path else KINDS_ASSET
    return json.loads(p.read_text(encoding="utf-8-sig")).get("kinds", {})


def required(text: str) -> bool:
    """本 CHG 是否需要互動規格:程式類 + Skill ≥ v1.6.0(前瞻適用)。"""
    if re.search(r"Acceptance-operation:\s*n/?a|docs-only", text, re.IGNORECASE):
        return False
    m = AUTOPILOT_VER_RE.search(text)
    if m is None:
        return False
    # 明確早退而不是 `bool(m) and m.group(...)`:後者在執行期正確,但型別上
    # narrow 不掉,於是每個這樣寫的檔案都得欠一筆基線豁免(CHG-20260804-09)。
    return (int(m.group(1)), int(m.group(2))) >= INTERACTION_SINCE


def exemption(text: str) -> tuple[bool, str | None]:
    """回 (有無豁免標記, 理由)。理由為空 → 視同未宣告。

    行內程式碼(反引號包住的)一律不算——那是在**說明語法**,不是在宣告豁免。
    """
    for m in EXEMPT_RE.finditer(text):
        line = text[text.rfind("\n", 0, m.start()) + 1:
                    (text.find("\n", m.start()) + 1) or len(text)]
        if "`" in line:
            continue          # 文件示例,不是宣告
        reason = (m.group(1) or "").strip()
        return True, (reason or None)
    return False, None


def parse_spec(text: str) -> list[dict]:
    """解析 `### Interaction spec` 節,回 [{kind, cmd, artifacts:[...]}, ...]。"""
    m = SPEC_RE.search(text)
    if not m:
        return []
    seg = text[m.start():]
    nxt = re.search(r"^###\s", seg[3:], re.MULTILINE)
    if nxt:
        seg = seg[:nxt.start() + 3]
    kinds = KIND_RE.findall(seg)
    cmds = CMD_RE.findall(seg)
    arts = ARTIFACTS_RE.findall(seg)
    out = []
    for i, k in enumerate(kinds):
        out.append({
            "kind": k,
            "cmd": cmds[i].strip() if i < len(cmds) else None,
            "artifacts": [a.strip() for a in arts[i].split(",")] if i < len(arts) else [],
        })
    return out


def choices_message(kinds: dict) -> str:
    """未選擇種類時列出全部可選項。**不得挑一個當預設**——
    靜默的預設會替使用者做掉一個他從未想過的決定。"""
    lines = ["請選擇互動驗證的種類(無預設值,必須明確指定):"]
    for key, spec in kinds.items():
        surf = spec.get("surface", [])
        lines.append(f"  · {key} — {spec.get('label', '')}")
        for s in surf[:3]:
            lines.append(f"      要驗到:{s}")
        if len(surf) > 3:
            lines.append(f"      (另有 {len(surf) - 3} 項,見 assets/interaction_kinds.json)")
        lines.append(f"      必要產物:{', '.join(spec.get('artifacts', [])) or '(未定義)'}")
    lines.append("分類表可增補:在 assets/interaction_kinds.json 的 kinds 底下新增條目,"
                 "或以 --interaction-kinds 指向自己的表。")
    return "\n".join(lines)


def _first_token(cmd: str):
    """取指令的第一個 token(去掉外層引號);解析不出回 None。"""
    try:
        toks = shlex.split(cmd, posix=False)
    except ValueError:
        return None
    return toks[0].strip('"\'') if toks else None


def _launchable(cmd: str) -> bool:
    """第一個 token 是否為可解析的可執行檔。解析不出時保守回 True(交給實跑判定)。"""
    tok = _first_token(cmd)
    if not tok:
        return True
    return Path(tok).exists() or shutil.which(tok) is not None


def check_artifacts(repo: Path, artifacts: list[str]) -> list[str]:
    """回缺少的產物清單。存在但為空檔一律視同缺少——0 bytes 的截圖不是證據。"""
    missing = []
    for rel in artifacts:
        if not rel:
            continue
        p = Path(repo) / rel
        if not p.exists() or (p.is_file() and p.stat().st_size == 0):
            missing.append(rel)
    return missing


def run_gate(repo: Path, text: str, kinds: dict, override_cmd: str | None = None,
             trust_chg: bool = False):
    """執行互動驗證閘。

    回 (狀態, 訊息);狀態 ∈ {'ok', 'halt', 'uncovered'}。
    'uncovered' 由呼叫端依風險分級決定放行或 halt——runner 不在這裡替治理層做決定。
    """
    specs = parse_spec(text)
    if not specs:
        # 區分兩種:節根本不存在 vs 節存在但沒選種類。後者是「使用者開始寫了但沒選」,
        # 訊息要講清楚是哪一種,否則他會以為自己漏了整個節。
        head = ("宣告了「### Interaction spec」節但**未指定 kind**——必須明確選一種。"
                if SPEC_RE.search(text) else
                "缺「### Interaction spec」節。\n"
                "會被重複使用的產物,其使用面必須被真的用過一次,而且要可重跑。")
        return "halt", head + "\n" + choices_message(kinds)

    msgs = []
    for spec in specs:
        kind = spec["kind"]
        if kind not in kinds:
            return "halt", (f"互動種類「{kind}」不在分類表內。\n" + choices_message(kinds))
        # ---- 信任邊界(CHG-20260803-05)----
        # 指令有兩種來源:操作者打的(--interaction-cmd)與 **repo 內容**(CHG 檔案的文字)。
        # 後者是內容驅動執行:只要有人能讓一份 CHG 進到 repo(例如 fork 的 PR),
        # 這裡就會以 shell 跑它寫的任何東西,而且產物存在就算通過。
        # 故預設**不執行**內容來源的指令;要執行必須明示 --trust-chg-commands。
        if override_cmd:
            cmd, origin = override_cmd, "操作者(--interaction-cmd)"
        elif spec["cmd"] and trust_chg:
            cmd, origin = spec["cmd"], "CHG 內容(已由 --trust-chg-commands 明示信任)"
        elif spec["cmd"]:
            return "halt", (
                f"種類「{kind}」的指令來自 **CHG 檔案內容**,預設不執行。\n"
                f"  這是**內容驅動執行**:能讓一份 CHG 進到 repo 的人,就能讓 autopilot\n"
                f"  以 shell 執行任意指令,且只要產物存在就會被判為通過。\n"
                f"  宣告的指令是:{spec['cmd']}\n"
                f"  兩條路:(1) 以 `--interaction-cmd` 由操作者提供指令(建議);\n"
                f"          (2) 確認這份 CHG 可信後,明示 `--trust-chg-commands`。")
        else:
            return "halt", (f"種類「{kind}」未指定 cmd:沒有指令就沒有可重跑的驗證。\n"
                            f"必要產物:{', '.join(kinds[kind].get('artifacts', []))}")
        # 不論來源都把原文印出來——被執行的東西不該只存在於某個檔案的某一行裡
        print(f"  [互動驗證] 即將執行({origin}):{cmd}")
        want = spec["artifacts"] or kinds[kind].get("artifacts", [])
        if not want:
            return "halt", f"種類「{kind}」未宣告產物,且分類表也沒有預設——無從稽核是否真的執行過"

        # 驅動存不存在,**執行前先解析**,不靠事後嗅錯誤訊息。
        # 訊息在各平台各 shell 都不一樣——bash 說 "command not found"、
        # dash 說 ": not found"、cmd.exe 說 "is not recognized";退出碼也不可靠
        # (POSIX 只有 8 bits)。同 autopilot_sentinels 的 launchable() 教訓。
        if not _launchable(cmd):
            return "uncovered", (f"互動驗證無法在本環境執行(驅動不存在:"
                                 f"{_first_token(cmd)}):{kind}")

        r = run_shell(cmd, repo)
        if r.returncode != 0:
            tail = (r.stdout or r.stderr or "").strip().splitlines()[-5:]
            return "halt", f"互動驗證失敗({kind}):\n  " + "\n  ".join(tail)

        missing = check_artifacts(repo, want)
        if missing:
            return "halt", (f"互動驗證指令回報成功,但**宣告的產物沒有出現**:{', '.join(missing)}\n"
                            f"  指令回 0 不等於真的操作過——沒有產物就無法稽核。\n"
                            f"  實際檢查的路徑以 repo 根目錄為基準:{repo}")
        # 來源要進訊息,不只進 stdout——ACC 該記的是「這道驗證是誰的指令跑出來的」。
        # 內容來源(即使已明示信任)與操作者來源的證據強度不同。
        msgs.append(f"  ✓ {kind}:產物齊備({', '.join(want)});指令來源:{origin}")

    return "ok", "互動驗證通過:\n" + "\n".join(msgs)
