#!/usr/bin/env python3
"""非功能性驗證閘:依專案型態調用,並引入第三種狀態「不適用」(CHG-20260804-02)。

前九張 CHG 補的都是功能正確性方向(對不對);這一張補的是快不快、穩不穩、會不會漏。
但**不是一律要求**——對一個 CLI/library repo 課負載測試的稅,會讓人把整套關掉。
這正是 review-panel 那句話的另一面:深度是為風險加的,不是對所有事情課的稅。

所以這裡的重點不是九道新閘,而是**三態要分得開**:

  · 通過   —— 驗了,而且過了
  · 未涵蓋 —— 該驗,但這個環境驗不了(工具不存在、無頭環境)
  · 不適用 —— 這個專案型態根本不需要驗這一項

把「不適用」讀成「通過」,與把「未涵蓋」讀成「通過」是同一個錯誤(KN-001):
兩者的後續動作完全不同——未涵蓋要補環境,不適用是永久結論。
而「不適用」**同樣要具名理由**,否則只要宣稱「我不是後端」,整塊非功能性就消失了。
"""
from __future__ import annotations
import json
import re
from pathlib import Path

from . import quality_judge as QJ
from .exec_util import run_shell

TABLE = Path(__file__).resolve().parent.parent.parent / "assets" / "nonfunctional_checks.json"
PROFILE_FILE = Path(".ai-sdlc") / "profile.json"
KNOWN_PROFILES = ("backend-service", "frontend-web", "cli-tool", "library",
                  "data-pipeline", "docs-only")

SPEC_RE = re.compile(r"^###\s*Non-functional checks", re.MULTILINE)
KIND_RE = re.compile(r"^\s*-\s*kind:\s*(\S+)", re.MULTILINE)
CMD_RE = re.compile(r"^\s*-\s*cmd:\s*(\S.*)$", re.MULTILINE)
ARTIFACTS_RE = re.compile(r"^\s*-\s*artifacts:\s*(\S.*)$", re.MULTILINE)
# 逐類延後(CHG-20260804-03):整段 `n/a` 會把已接上的類別也一起豁免掉,
# 逐類 defer 才能誠實表達「做了兩類、延後三類」。無理由的 defer 視同未宣告。
DEFER_RE = re.compile(r"^\s*-\s*defer:[ \t]*(.*)$", re.MULTILINE)
EXEMPT_RE = re.compile(
    r"^[ \t]*[-*]?[ \t]*Non-functional:[ \t]*n/?a[ \t]*[（(]?[ \t]*([^）)\n]*)",
    re.IGNORECASE | re.MULTILINE)
AUTOPILOT_VER_RE = re.compile(r"ai-sdlc-autopilot\s*v(\d+)\.(\d+)", re.IGNORECASE)
# 前瞻適用:沿用 INTERACTION_SINCE / QUALITY_SINCE 的既有形態,
# 升級不讓既有流水線立刻全紅。
NONFUNCTIONAL_SINCE = (1, 14)


def load_kinds(path=None) -> dict:
    p = Path(path) if path else TABLE
    return json.loads(p.read_text(encoding="utf-8-sig")).get("kinds", {})


def required(text: str) -> bool:
    if re.search(r"Acceptance-operation:\s*n/?a|docs-only", text, re.IGNORECASE):
        return False
    m = AUTOPILOT_VER_RE.search(text)
    if m is None:
        return False
    # 明確早退而不是 `bool(m) and m.group(...)` 的短路:後者在執行期正確,
    # 但 mypy narrow 不到,於是每個這樣寫的檔案都得欠一筆基線豁免。
    # 既有檔案的那幾筆已在基線裡具名;新程式碼不該再欠。
    return (int(m.group(1)), int(m.group(2))) >= NONFUNCTIONAL_SINCE


def load_profile(repo) -> tuple[list | None, str | None]:
    """讀專案型態宣告。回 (型態清單 or None, 錯誤訊息)。

    **未宣告回 None 而不是猜**:猜錯會靜默放行整塊非功能性,
    而那是最難發現的失效。呼叫端對 None 的處置是「視為全部適用」——
    錯誤方向倒向多驗而非少驗。
    """
    p = Path(repo) / PROFILE_FILE
    if not p.is_file():
        return None, None
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as exc:
        return None, f"{PROFILE_FILE} 無法解析:{exc}(讀不到型態就照跑,等於靜默略過整塊非功能性)"
    # **合法 JSON 不等於預期形狀**(CHG-20260810-04):`json.loads("[]")` 會成功,
    # 下一行的 `.get()` 才拋 `AttributeError`——那不在上面 except 的射程內。
    # 本輪 fuzz 在三個檔案裡各抓到一次同一族(policy / profile / quality_judge)。
    if not isinstance(data, dict):
        return None, (f"{PROFILE_FILE} 必須是物件,實得 {type(data).__name__}"
                      f"(讀不到型態就照跑,等於靜默略過整塊非功能性)")
    profiles = data.get("profiles") or []
    if isinstance(profiles, str):
        profiles = [profiles]
    unknown = [p_ for p_ in profiles if p_ not in KNOWN_PROFILES]
    if unknown:
        return None, (f"未知的專案型態:{', '.join(unknown)}\n"
                      f"  已知型態:{', '.join(KNOWN_PROFILES)}(可在分類表增補)")
    return (profiles or None), None


def applicable(profiles, kinds: dict) -> tuple[list, list]:
    """回 (適用的種類, 不適用的種類)。

    profiles 為 None(未宣告)時**全部適用**——最保守的那一邊。
    """
    if not profiles:
        return sorted(kinds), []
    on: list[str] = []
    off: list[str] = []
    for key, spec in kinds.items():
        (on if set(spec.get("applies_to", [])) & set(profiles) else off).append(key)
    return sorted(on), sorted(off)


def parse_spec(text: str) -> list[dict]:
    m = SPEC_RE.search(text)
    if not m:
        return []
    seg = text[m.start():]
    nxt = re.search(r"^###\s", seg[3:], re.MULTILINE)
    if nxt:
        seg = seg[:nxt.start() + 3]
    # 逐項解析:每個 `- kind:` 之後、下一個 `- kind:` 之前的那一段才屬於它,
    # 否則 defer 與 cmd 混在一起時位置會對錯人
    out = []
    marks = [m.start() for m in KIND_RE.finditer(seg)] + [len(seg)]
    for i, m in enumerate(KIND_RE.finditer(seg)):
        block = seg[marks[i]:marks[i + 1]]
        cmd = CMD_RE.search(block)
        arts = ARTIFACTS_RE.search(block)
        dfr = DEFER_RE.search(block)
        out.append({"kind": m.group(1),
                    "cmd": cmd.group(1).strip() if cmd else None,
                    "artifacts": [a.strip() for a in arts.group(1).split(",")] if arts else [],
                    "defer": dfr.group(1).strip() if dfr else None})
    return out


def exemption(text: str) -> tuple[bool, str | None]:
    """同互動閘與委派閘:行內程式碼是語法說明不算宣告;**空豁免視同未宣告**。"""
    for m in EXEMPT_RE.finditer(text):
        line = text[text.rfind("\n", 0, m.start()) + 1:
                    (text.find("\n", m.start()) + 1) or len(text)]
        if "`" in line:
            continue
        reason = (m.group(1) or "").strip()
        return True, (reason or None)
    return False, None


def choices_message(keys, kinds: dict) -> str:
    lines = ["請宣告要跑哪幾類非功能性驗證(無預設值,必須明確指定):"]
    for key in keys:
        spec = kinds.get(key, {})
        lines.append(f"  · {key} — {spec.get('label', '')}")
        for q in spec.get("asks", []):
            lines.append(f"      要回答:{q}")
        lines.append(f"      必要產物:{', '.join(spec.get('artifacts', [])) or '(未定義)'}"
                     f";可用工具舉例:{', '.join(spec.get('example_drivers', []))}")
    lines.append("分類表可增補:assets/nonfunctional_checks.json;"
                 "專案型態宣告於 .ai-sdlc/profile.json。")
    return "\n".join(lines)


def na_message(off: list, kinds: dict, profiles) -> str:
    """「不適用」的訊息。刻意**不含**任何肯定式的通過措辭。"""
    rows = [f"    · {k}({kinds.get(k, {}).get('label', '')}):"
            f"{kinds.get(k, {}).get('_why_scoped') or kinds.get(k, {}).get('_why_not_cli') or '型態不符'}"
            for k in off]
    return (f"非功能性驗證:下列 {len(off)} 類**不適用**於本專案型態"
            f"({', '.join(profiles or ['(未宣告)'])})——\n"
            + "\n".join(rows)
            + "\n    「不適用」是永久結論,與「未涵蓋」(該驗但驗不了)不同,"
            "兩者都不是「驗過了」。")


def run_gate(repo, text: str, kinds: dict, profiles, trust_chg: bool = False,
             baseline: dict | None = None):
    """回 (狀態, 訊息);狀態 ∈ {'ok', 'halt', 'uncovered', 'not-applicable'}。"""
    on, off = applicable(profiles, kinds)
    exempted, reason = exemption(text)
    if exempted and not reason:
        return "halt", ("`Non-functional: n/a` 沒有寫理由——空豁免與沒宣告等價,"
                        "但看起來像有交代,那是最糟的狀態。")
    if exempted:
        return "not-applicable", (f"非功能性驗證已具名豁免:{reason}(須記入 ACC)"
                                  + ("\n" + na_message(off, kinds, profiles) if off else ""))
    if not on:
        return "not-applicable", na_message(off, kinds, profiles)

    specs = parse_spec(text)
    declared = {s["kind"] for s in specs}
    missing = [k for k in on if k not in declared]
    if missing:
        head = (f"下列 {len(missing)} 類非功能性驗證**適用於本專案型態**"
                f"({', '.join(profiles or ['(未宣告 → 視為全部適用)'])})但未宣告:\n")
        return "halt", head + choices_message(missing, kinds) + (
            "\n" + na_message(off, kinds, profiles) if off else "")

    lines, uncovered = [], []
    for spec in specs:
        kind = spec["kind"]
        if kind not in kinds:
            return "halt", f"種類「{kind}」不在分類表內。\n" + choices_message(on, kinds)
        if kind in off:
            lines.append(f"  · {kind}:宣告了但本型態不適用——仍會執行(多驗不扣分)")
        if spec.get("defer") is not None:
            if not spec["defer"]:
                return "halt", (f"種類「{kind}」的 `- defer:` 沒有寫理由——"
                                "空延後與沒宣告等價,但看起來像有交代,那是最糟的狀態。")
            # 延後是**未涵蓋**,不是通過:後續由風險分級決定放行或停人
            uncovered.append(f"{kind}(具名延後:{spec['defer']})")
            continue
        cmd = spec["cmd"]
        if not cmd:
            return "halt", f"種類「{kind}」未指定 cmd:沒有指令就沒有可重跑的驗證"
        if not trust_chg:
            return "halt", (f"種類「{kind}」的指令來自 **CHG 檔案內容**,預設不執行"
                            f"(信任邊界同互動閘與委派閘,CHG-20260803-05)。\n"
                            f"  宣告的指令是:{cmd}\n  確認可信後明示 `--trust-chg-commands`。")
        want = spec["artifacts"] or kinds[kind].get("artifacts", [])
        if not want:
            return "halt", f"種類「{kind}」未宣告產物,無從稽核是否真的跑過"
        r = run_shell(cmd, Path(repo))
        if r.returncode != 0 and kind not in QJ.JUDGED_NONFUNCTIONAL:
            tail = (r.stdout or r.stderr or "").strip().splitlines()[-5:]
            return "halt", f"非功能性驗證未通過({kind}):\n  " + "\n  ".join(tail)
        miss = [a for a in want if not (Path(repo) / a).exists()]
        if miss:
            uncovered.append(f"{kind}(產物未出現:{', '.join(miss)})")
            continue
        if kind not in QJ.JUDGED_NONFUNCTIONAL:
            lines.append(f"  ✓ {kind}:產物齊備({', '.join(want)})")
            continue
        # 有判讀器的種類:**退出碼不定生死**,改判產物內容。
        # 只驗「產物存在」等於沒驗——一份寫著「12 個 GPL 相依」的報告
        # 照樣存在且非空(這正是 CHG-20260804-02 留下、本支要補的洞)。
        try:
            body = (Path(repo) / want[0]).read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            return "halt", f"{kind}:產物讀取失敗({exc})——讀不到的報告不等於沒問題"
        jok, jmsg, _ = QJ.judge(kind, body, baseline or {})
        if not jok:
            return "halt", f"非功能性驗證未通過({kind}):\n{jmsg}"
        lines.append(f"  ✓ {jmsg}")

    tail = ("\n" + na_message(off, kinds, profiles)) if off else ""
    if uncovered:
        return "uncovered", ("下列非功能性驗證**未涵蓋**(非通過):\n  "
                             + "\n  ".join(uncovered)
                             + ("\n" + "\n".join(lines) if lines else "") + tail)
    return "ok", "非功能性驗證通過:\n" + "\n".join(lines) + tail
