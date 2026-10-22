#!/usr/bin/env python3
"""本機閘的壞樣本金絲雀 —— 證明每一道閘**真的會擋下它該擋的東西**。stdlib-only。

宣稱清冊三個來源:`wiring` 由 `assertion_probe` 驗、`behave` 由 `behave_step_probe`
驗(CHG-20260813-06),而 `gate` 那 21 條一直是 `pending`,理由「壞樣本金絲雀尚未建置」。

## 為什麼不用注入式(兩席一致駁回)

> `check_probe_leftovers` 這道閘存在的**唯一理由**就是注入式探針漏過一次
> (`tmpada5_jpq.py`,斷言被反轉的那次);而且在真樹上改壞來源會讓
> `verifier_integrity --check` 在探測中途變紅、與工作樹賽跑,失敗時的殘局
> 比 behave 探針更糟——**它要弄壞的正是驗證器本身**。(fable)

所以壞樣本一律活在 `git worktree` 開出來的 fixture 裡,而**不碰真樹一個位元組**。

## 三段式,而每一段都可能是「探不到」而不是「沒問題」

  第一段 **抽指令**:從 `.github/ci_local.sh` 的 step 行抽出實跑指令,
         與 `build_claim_manifest.gate_claims` 同一組 regex。
         **手抄一份指令表會與接線分岔,而分岔沒有東西看著**(KN-005)。
  第二段 **基線綠**:在未變異的 fixture 上跑一次,必須 rc 0。
         不綠 → `baseline-red`:那道閘在乾淨的樹上就紅,變異的紅證明不了任何事
         (CHG-20260813-06 學到的同一條)。
  第三段 **壞樣本**:套用一個具名變異,再跑一次,必須 rc != 0。
         紅 → `blocks`;照樣綠 → **`fail-open`**,那是問題不是狀態。

## 五態,而第五態是實測逼出來的

| 判定 | 意思 |
|---|---|
| `blocks` | 基線綠、壞樣本紅**且輸出指名了它**——這道閘擋得住 |
| `fail-open` | 壞樣本照樣綠,**而樣本已被證明在射程內**——它沒在擋。問題,不是狀態 |
| `baseline-red` | 乾淨的 fixture 上就紅——探不了,不是「沒擋」 |
| `unverified-blocked` | 沒有壞樣本可造,或前置不在——具名阻礙 |
| `sample-out-of-range` | **樣本沒打中這道閘的射程**——儀器的問題,不是被測物的 |
| `no-sample` | 壞樣本尚未登記——**不是「這道閘沒問題」** |

`fail-open` 與其餘各態**必須分得開**:前者是被測物有缺陷,其餘是儀器搆不到。
混在一起,一道真的 fail-open 的閘會被讀成「這台機器驗不了」。

而 `sample-out-of-range` 是**第一輪實測逼出來的**:兩道閘被判成 `fail-open`,
追下去兩條都是我的樣本打錯射程(`bilingual pairs` 只驗「有 en 缺 zh」而我種了
zh-only;`path case sensitivity` 只讀 JSON 宣告檔而我種在 markdown 裡)。
**誣指一道好閘是 fail-open,會讓人去改一道本來就對的閘**
——與 behave 探針的「未執行 ≠ 裝飾」同一條。

退出碼:0 全部擋得住 | 1 有 fail-open | 2 環境/參數錯誤 | 3 射程外/未證明
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, NamedTuple

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(os.environ.get("GATE_CANARY_REPO")
            or Path(__file__).resolve().parents[3]).resolve()
CI_LOCAL = Path(".github/ci_local.sh")

# 與 `build_claim_manifest.gate_claims` 同一組判準:名字是**字面值**,
# 註解行與帶 `$` 的展開都不是呼叫點(CHG-20260813-08 / #59 訂下的)。
STEP_RX = re.compile(r'(?:gated_)?step\s+"([^"]+)"')

# ── 單樣本的操作預算(CHG-20260814-02,C2 第三筆)────────────────────────
#
# **`MAX_PROBE_BUDGET` 是治理過的上限,`DEFAULT_PROBE_BUDGET` 只是預設值。**
# 兩者分開是這一筆買到的東西,而它起因於一個被兩席同時抓到的錯誤推論:
# 我原本要拿「一輪要 80 分鐘 > timeout 900 秒」證明某道閘不可驗,而
#
# > 這首先證明的是**目前探針預算不足**,不自動證明閘本質不可驗;
# > 否則確有拿 blocked 遮蔽儀器設計缺陷之嫌。(codex)
#
# 同一份裁決裡我又提議「每則樣本可自訂 timeout」——那兩條互斥:
# 一個可自由填的 timeout 欄位,填大一點阻礙就消失,而**沒有人簽過名**。
#
# 收斂後的形狀:上限是一個**活在錨定檔裡的常數**(改它 = 動驗證器 =
# `verifier_integrity --update --chg` + 重簽),per-sample 預算是**上限內的撥盤**,
# 上限之上沒有撥盤。於是 `unverified-blocked` 的定義從「超過預設值」
# 變成「**實測超過治理過的預算**」——前者是實作細節,後者是決定。
MAX_PROBE_BUDGET = 1800
DEFAULT_PROBE_BUDGET = 900


def text_digest(text: str) -> str:
    """與 `build_claim_manifest.text_digest` **必須同構**——artifact 帶的 digest
    要拿去和清冊比對,兩邊算法分岔就等於永遠失配。"""
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()[:12]


def _strip_reason_arg(rest: str) -> str:
    """`gated_step <名稱> <理由> <指令…>` —— 剝掉**理由**那個參數,留下指令。

    理由是 `"$(_why_quick)"` 這種命令替換;重播環境裡那些函式不存在,
    留著會讓 bash 拿空字串當指令名(rc 127),而那會被讀成 `baseline-red`
    ——**看起來像閘壞了,實際上是重播沒接對**。
    """
    s = rest.lstrip()
    if not s.startswith('"'):
        return rest
    end = s.find('"', 1)
    if end < 0:
        return rest
    return s[end + 1:]


def parse_steps(src: str) -> dict[str, dict]:
    """{step 名: {"raw": 原始跨度(名字之後), "cmd": 正規化重播指令}}。

    **一個解析器,兩種用途**——digest 覆蓋完整原始跨度,重播由同一個物件剝出。
    第一版讓兩者永久分岔,而 codex 席直接駁回:

    > 不接受永久分岔。應先解析成同一個「原始行跨度＋正規化重播命令」物件;
    > digest 覆蓋完整原始跨度,重播由同物件剝理由參數。
    > **續行未入 digest 是完整性漏洞**,必須同筆修並更新 claims。

    那個漏洞是實的:`ci_local.sh` 有兩道閘用反斜線續行,而**真正的指令在下一行**
    ——舊版的 digest 只蓋到 `"$(_why_quick)" \`,於是把續行上的
    `PYTHONIOENCODING=cp932` 改掉,清冊的過期偵測**完全看不到**。
    """
    lines = src.splitlines()
    out: dict[str, dict] = {}
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.lstrip().startswith("#"):
            i += 1
            continue
        m = STEP_RX.search(ln)
        if not m or "$" in m.group(1):
            i += 1
            continue
        span = [ln]
        # **續行也是這一道閘的一部分。** 少了它,digest 蓋不到真正跑的指令。
        while span[-1].rstrip().endswith("\\") and i + 1 < len(lines):
            i += 1
            span.append(lines[i])
        raw = " ".join(x.rstrip().removesuffix("\\").strip() for x in span)
        after = raw[raw.index(m.group(0)) + len(m.group(0)):]
        cmd = _strip_reason_arg(after) if "gated_step" in m.group(0) else after
        name = m.group(1)
        if name in out:
            out[name]["raw"] += "|" + after
            out[name]["digest"] = text_digest(out[name]["digest"] + "|" + text_digest(after))
        else:
            out[name] = {"raw": after, "cmd": cmd, "digest": text_digest(after)}
        i += 1
    return out


def discover(repo: Path) -> dict[str, dict]:
    """讀 `ci_local.sh` 並解析出每一道閘。**唯一的指令來源是接線本身。**"""
    try:
        return parse_steps((repo / CI_LOCAL).read_text(encoding="utf-8"))
    except OSError:
        return {}


def _git(repo: Path, *args: str, timeout: int = 300) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, p.stdout + p.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, str(e)


class Fixture:
    """`git worktree add <tmp> HEAD` —— **同 commit 的位元組由建構保證**。

    比「複製腳本進 fixture 再以 SHA 驗同一性」更強也更便宜:沒有第二份可分岔的複本。

    > 另須防工作樹內**未提交內容**造成正式執行與 HEAD fixture 分岔。(codex)

    所以建 fixture 前先問一次 `git status --porcelain`:髒樹上跑金絲雀,
    量到的是 HEAD 而不是你正在跑的那份程式——**那是一個安靜的錯誤答案**。
    """

    def __init__(self, repo: Path, allow_dirty: bool = False):
        self.repo, self.allow_dirty = repo, allow_dirty
        self.path: Path | None = None
        self.dirty: list[str] = []

    def __enter__(self) -> "Fixture":
        rc, out = _git(self.repo, "status", "--porcelain")
        if rc == 0:
            self.dirty = [ln for ln in out.splitlines() if ln.strip()]
        d = Path(tempfile.mkdtemp(prefix="gate-canary-"))
        self.path = d / "fixture"
        rc, out = _git(self.repo, "worktree", "add", "--detach",
                       str(self.path), "HEAD")
        if rc != 0:
            shutil.rmtree(d, ignore_errors=True)
            self.path = None
            self.error = out
        return self

    def __exit__(self, *exc) -> None:
        if self.path is None:
            return
        _git(self.repo, "worktree", "remove", "--force", str(self.path))
        shutil.rmtree(self.path.parent, ignore_errors=True)


def resolve_py() -> str:
    """用**與 `ci_local.sh` 同一套順序**解析直譯器,而且回**名字不回絕對路徑**。

    第一版把 `PY` 設成 `sys.executable`(絕對 Windows 路徑),於是
    `bash -c "$PY -m py_compile …"` 在 Git Bash 下回 **127**,
    而 `'"$PY"' -m json.tool` 回 **1**——三道閘一起被判成 `baseline-red`
    (「這道閘在乾淨的樹上就紅」)。**那是我沒接好環境,不是閘壞了。**

    這一格的一般化教訓:金絲雀原樣重播指令,就得**原樣重建它的環境**;
    環境接錯時的失效方向是「看起來像被測物有缺陷」,而那是最貴的一種錯誤答案。
    """
    for cand in ("./.venv/bin/python3", "python3", "python"):
        try:
            p = subprocess.run(["bash", "-c", f'command -v {cand} >/dev/null && '
                                              f'{cand} -c "import sys"'],
                               capture_output=True, timeout=60)
            if p.returncode == 0:
                return cand
        except (OSError, subprocess.TimeoutExpired):
            continue
    # **不回 `sys.executable`。** 那正是缺陷①裡「Git Bash 執行不了」的絕對路徑,
    # 回它等於把那個缺陷原樣還魂——而失效方向是「看起來像閘壞了」。
    # 回空字串讓指令當場 `command not found`,由 `baseline-red` / 具名阻礙承接(fable)。
    return ""


def run_gate(cwd: Path, cmd: str, timeout: int = DEFAULT_PROBE_BUDGET,
             env: dict[str, str] | None = None) -> tuple[int, str]:
    """原樣重播接線上的那一行。`--repo .` 在 fixture 的 cwd 下就是 fixture 自己,
    所以**吃 `--repo` 的與吃 cwd 的都不必改寫**——21 道裡有 11 道是後者。

    **指令原樣重播,那它的環境也要原樣供給。** 接線上的每一行都引用
    `ci_local.sh` 開頭解析出來的 `$PY`,而抽出來的字串裡它還是一個未展開的變數:
    不供給就是 `command not found`(rc 127),而那會被讀成 `baseline-red`
    ——「這道閘在乾淨的樹上就紅」,一個**看起來像閘壞了、實際上是我沒接好環境**
    的錯誤答案。實測第一次跑就踩到。
    """
    # **硬截止在外層,不靠欄位驗證。** 樣本宣告的預算再大也跑不過治理過的上限
    # ——「宣告一個大數字」與「真的獲准跑那麼久」必須是兩件事(codex)。
    timeout = min(timeout, MAX_PROBE_BUDGET)
    # **這兩個 env 是「重建 `ci_local.sh` 自己供給每個 step 的環境」,維持全域。**
    # 樣本專屬的環境走 `setup` 宣告(見 `Sample`),不混進這裡:混進來的話,
    # 一個樣本改掉的環境會無聲地跟著下一道閘走。
    base_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PY": resolve_py()}
    try:
        p = subprocess.run(["bash", "-c", cmd], cwd=str(cwd), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env={**base_env, **(env or {})})
        return p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    except OSError as e:
        return -2, str(e)


class Sample(NamedTuple):
    """一則壞樣本的完整宣告。**每一格都是宣告式的**,沒有一格是自由副作用。

    `preflight` / `setup` 分成兩層,是兩席在 C2-C 定形時各自堅持的一半:

    > 不同意手填、可任意改環境的逃生口。形狀應是宣告式 `preflight`:
    > 只回 `ready / blocked(reason,evidence) / error`;工具在場性在此探測。
    > 種產物與給 env 屬 fixture setup,**不應混入 preflight,
    > 否則 blocked 可被副作用偽造**。(codex)

    > setup **具名進 artifact row**——seeded fixture 上的 `blocks`
    > 與裸 fixture 上的 `blocks` 是**兩個不同強度的宣稱**;setup 失敗要判
    > `unverified-blocked`(具名),不是讓它漏到 `baseline-red`;
    > **KN-008 第 0 問套用到 setup**:宣稱種了產物,就驗產物真的在。(fable)

    執行順序固定:`preflight(純觀測)→ setup(種產物/給 env)→ 基線 → mutate → 變異輪`。

    · `preflight(fx) -> (str, str)`:`("ready", "")` / `("blocked", 理由)` / `("error", 理由)`。
      **零副作用**——它只回報看到什麼。`blocked` 是「觀測到前置不在」,
      `error` 是「**探測器自己壞了**」,兩者都落 `unverified-blocked` 而措辭必須分開
      (KN-003:「探不了」與「探壞了」不是同一件事)。
    · `setup(fx) -> (str|None, dict)`:回具名失敗理由或 `None`,以及一份**宣告**
      `{"files": [相對路徑…], "env": {…}, "note": "…"}`。harness 逐項驗證宣告為真。
    · `also_green`:差分對照輪的指令。這一輪紅**而對照輪綠**才算 `blocks`。
    · `budget`:預算內的撥盤,上限由 `MAX_PROBE_BUDGET` 治理。
    """

    mutate: Callable[[Path], "str | None"]
    witness: str | None = None
    preflight: Callable[[Path], tuple[str, str]] | None = None
    setup: Callable[[Path], tuple["str | None", dict]] | None = None
    also_green: str | None = None
    budget: int = DEFAULT_PROBE_BUDGET


def verify_setup(fx: Path, decl: dict) -> str | None:
    """**KN-008 第 0 問,套用在 setup 上**:宣稱種了產物,就驗產物真的在。

    與 `_edit_json` 的第 0 問同構,而且同樣**做進 harness 不留給各樣本自律**
    ——`replace()` 是 no-op 那個缺陷(C2 第二筆的④)如果只靠樣本自己檢查,
    它會在 setup 這一層原樣重演一次。

    驗三件事:宣告的檔案存在且非空、宣告是 JSON 的 parse 得動、宣告的 env 值非空。
    驗不過回具名理由 → `unverified-blocked`,**不是讓它漏到 `baseline-red`**
    (漏過去的話,失效方向會變成「這道閘在乾淨的樹上就紅」,而那是最貴的錯誤答案)。
    """
    for rel in decl.get("files", []):
        p = fx / rel
        if not p.is_file():
            return f"setup 宣稱種了 {rel},而它不在 fixture 裡——**產物沒種下去**"
        try:
            body = p.read_text(encoding="utf-8")
        except OSError as e:  # diffcov-exempt: syserr — 上一行剛確認過 is_file(),要在兩行之間安全且跨平台地製造讀取失敗需注入檔案系統故障 [signoff: codex+fable @ CHG-20260814-02]
            return f"setup 種的 {rel} 讀不回來({e})"  # diffcov-exempt: syserr — 上一行剛確認過 is_file(),要在這之間安全且跨平台可重複地製造讀取失敗需注入檔案系統故障 [signoff: codex+fable @ CHG-20260814-02]
        if not body.strip():
            return f"setup 種的 {rel} 是空的——空產物與沒有產物不是同一件事(KN-006)"
        if rel.endswith(".json"):
            try:
                json.loads(body)
            except ValueError as e:
                return f"setup 種的 {rel} 不是合法 JSON({e})——閘會紅在解析而不是內容"
    for k, v in (decl.get("env") or {}).items():
        if not str(v).strip():
            return f"setup 宣告的環境變數 {k} 是空字串——空值會被讀成「沒給」(KN-003)"
    return None


def probe_gate(fixture: Path, cmd: str, s: Sample) -> tuple[str, str, dict]:
    """回 (判定, 證據, 進 artifact row 的附註)。

    **`witness` 是歸因**:壞樣本種下去之後,閘轉紅時的輸出必須指名它。
    沒有這一段的話,「閘紅了」與「閘因為別的原因紅了」分不開——
    而 behave 探針在 CHG-20260813-06 已經為同一個問題付過一次學費。

    更貴的是反方向:**閘照樣綠**時,分不出「樣本沒打中它的射程」與「它真的放行」。
    實測兩次:`bilingual pairs` 只驗「有 en 缺 zh」而我種了 zh-only、
    `path case sensitivity` 只讀 JSON 宣告檔而我種在 markdown 裡
    ——兩條都被判成 `fail-open`,而**兩條都是我的樣本打錯射程**。
    所以樣本要自己聲明 witness,而綠燈時的判定是
    **`sample-out-of-range`(樣本沒打中)**,不是 `fail-open`,除非樣本已被證明在射程內。

    **`also_green` 是差分**(C2 第三筆):三道 runner 閘跑的是**同一批測試**,
    差別只在編碼環境。所以「紅了」證明不了它們各自存在的理由——**要證明的是那條軸**。
    判準是這一輪紅 **而對照輪綠**;少了後半,三道閘會共用一份證據,
    而**共用證據等於其中兩道沒有證據**。
    """
    extra: dict = {}
    if s.budget > MAX_PROBE_BUDGET:
        # 宣告值超過治理過的上限 → **具名紅,不是靜靜地夾到上限**。
        # 靜靜夾住的話,逃生口只是換了個位置:欄位照樣可以填任何數字,
        # 而沒有人會知道它被夾過(`run_gate` 那一層仍會硬截止,兩層都要)。
        return ("unverified-blocked",
                f"這則樣本宣告 {s.budget}s,超過治理過的上限 {MAX_PROBE_BUDGET}s"
                f"——**上限之上沒有撥盤**,調升要動錨定檔", extra)

    if s.preflight is not None:
        state, why = s.preflight(fixture)
        if state == "blocked":
            return "unverified-blocked", f"preflight:前置不在——{why}", extra
        if state != "ready":
            # `error` 與任何非預期的回傳值一起走這條:**探測器自己壞了**,
            # 而那與「觀測到前置不在」必須在措辭上分得開(KN-003)。
            return "unverified-blocked", f"preflight **自己壞了**({state}):{why}", extra

    if s.setup is not None:
        setup_why, decl = s.setup(fixture)
        if setup_why:
            return "unverified-blocked", f"setup 失敗:{setup_why}", extra
        why0 = verify_setup(fixture, decl)
        if why0:
            return "unverified-blocked", why0, extra
        # **seeded fixture 上的 `blocks` 比裸 fixture 上的弱一級,而 row 要說出來。**
        extra["setup"] = {"files": list(decl.get("files", [])),
                          "env": sorted((decl.get("env") or {})),
                          "note": decl.get("note", "")}
        env = dict(decl.get("env") or {})
    else:
        env = {}

    # **量下來,別用推論。** 「這道閘太貴所以驗不了」如果沒有數字,它就是散文豁免
    # ——本筆的第一版正是拿一行 docstring 裡的「約 40 分鐘」當觀測引用(KN-009)。
    # monotonic clock:牆鐘會被系統校時往回撥,而那會讓預算判定無聲地錯(codex)。
    t0 = time.monotonic()
    rc, out = run_gate(fixture, cmd, s.budget, env)
    extra["baseline_s"] = round(time.monotonic() - t0, 1)
    if rc == -1:
        return ("unverified-blocked",
                f"基線在 {min(s.budget, MAX_PROBE_BUDGET)}s 的預算內跑不完"
                f"——**沒驗成,不是閘壞了**", extra)
    if rc != 0:
        return "baseline-red", f"未變異的 fixture 上就 rc={rc}——變異的紅證明不了任何事", extra
    if s.also_green is not None:
        rc0, _ = run_gate(fixture, s.also_green, s.budget, env)
        if rc0 != 0:
            return "baseline-red", (f"差分的對照輪在未變異的 fixture 上就 rc={rc0}"
                                    f"——**兩輪都要先綠**,差分才有意義"), extra
    blocked = s.mutate(fixture)
    if blocked:
        return "unverified-blocked", blocked, extra
    rc2, out2 = run_gate(fixture, cmd, s.budget, env)
    if rc2 == -1:
        return "unverified-blocked", "壞樣本上逾時——沒驗成,不是轉紅", extra
    if rc2 == 0:
        # **綠燈分不出兩件事**:樣本沒打中射程,或閘真的放行。
        # 沒有射程證明時一律歸前者——誤指一道好閘是 fail-open,
        # 會讓人去改一道本來就對的閘(與 behave 探針的「未執行 ≠ 裝飾」同一條)。
        # 射程證明兩邊都算:基線輸出提過它,**或**變異後的輸出提過它
        # ——「掃到了卻放行」一樣是在射程內的鐵證,而第一版只看基線,
        # 於是對真實的閘來說 `fail-open` 這條分支近乎走不到(fable)。
        if s.witness and (s.witness in out or s.witness in out2):
            where = "基線" if s.witness in out else "變異後"
            return "fail-open", (f"壞樣本放行了,而該閘的{where}輸出提過 {s.witness!r}"
                                 f"——**樣本在射程內而它沒擋**"), extra
        return "sample-out-of-range", ("壞樣本放行了,而**無法證明它落在這道閘的射程內**"
                                       "——那不是「閘沒在擋」,是樣本沒打中"), extra
    if s.witness and s.witness not in out2:
        return "sample-out-of-range", (f"閘紅了,而輸出沒有指名 {s.witness!r}"
                                       f"——**紅在別的地方**,歸因不到這個壞樣本"), extra
    if s.also_green is not None:
        rc3, out3 = run_gate(fixture, s.also_green, s.budget, env)
        if rc3 == 0:
            return "blocks", (f"**差分成立**:這一輪 rc={rc2}、對照輪 rc=0"
                              f"——擋住的正是這道閘存在的那條軸"), extra
        # 對照輪也紅:兩種情況,而處置完全相反。
        if s.witness and s.witness in out3:
            return "sample-out-of-range", (f"兩輪都紅(對照輪 rc={rc3})且對照輪也指名了"
                                           f"{s.witness!r}——**這個樣本不是軸專屬的**,"
                                           f"證明不了這道閘比隔壁那道多擋了什麼"), extra
        return "unverified-blocked", (f"對照輪 rc={rc3} 而輸出沒有指名 {s.witness!r}"
                                      f"——**對照輪紅在別處**,差分證不成;"
                                      f"那是儀器的問題,不記在樣本頭上"), extra
    # **證據字串不得說一件沒做過的檢查。** 第一版不論有沒有 witness 都寫
    # 「且輸出指名了它」,而 `witness=None` 時那段歸因整個跳過
    # ——`apply_gate_probe` 又把這句原文抄進清冊的 `evidence` 欄,
    # 於是**證據欄記載了一件沒發生的檢查**(KN-006 的形狀,
    # 發生在專門抓 KN-006 的工具自己身上)。(fable)
    if s.witness:
        return "blocks", (f"基線綠(rc=0)、壞樣本紅(rc={rc2}),"
                          f"且輸出指名了 {s.witness!r}:擋得住"), extra
    return "blocks", (f"基線綠(rc=0)、壞樣本紅(rc={rc2}),"
                      f"**但未做歸因**(這則樣本沒有 witness):證據弱一級"), extra


# ── 壞樣本登記簿(CHG-20260814-01,C2 第二筆)────────────────────────────
#
# 每一則是**一個具名的變異**:對 fixture 做一件事,而那件事正是這道閘宣稱要擋的。
# 回 `None` 表示樣本種好了;回字串表示**造不出來**(具名阻礙 → `unverified-blocked`)。
#
# 這張表是**手寫的資料**,而手寫的表會與現實分岔——所以下面有一條對帳:
# **發現器掃到的每一道閘,必須要嘛有樣本、要嘛在輸出上具名**。
# 缺席本身就是抗議,而那正是本 repo 在 21 個閘呼叫點上學到的同一條。


def _touch(fx: Path, rel: str, body: str) -> None:
    p = fx / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _edit_json(fx: Path, rel: str, fn) -> str | None:
    """讀 → 改 → 寫回。讀不到就回**具名理由**,不是靜靜地當作樣本種好了
    ——那會讓一道沒被驗到的閘被記成「擋得住」。"""
    p = fx / rel
    try:
        before = p.read_text(encoding="utf-8")
        d = json.loads(before)
    except (OSError, ValueError) as e:
        return f"讀不到 fixture 的 {rel}({e})"
    why = fn(d)
    if why:
        return why
    after = json.dumps(d, ensure_ascii=False, indent=2) + "\n"
    # **KN-008 第 0 問做進 harness**:變異套用了嗎?
    # 沒套用而回報「種好了」的話,閘照樣綠會被讀成 `fail-open` 或
    # `sample-out-of-range`——兩個都是錯的答案,而錯在儀器不在被測物。
    # 實測踩到:`files[0]` 排序後是 `.github/…`,`replace("skills/", …)` 是 no-op。
    if json.loads(after) == json.loads(before):
        return f"變異沒有改動 {rel} 的任何一格——**壞樣本沒種下去**(KN-008 第 0 問)"
    p.write_text(after, encoding="utf-8")
    return None


def _bad_doc_integrity(fx: Path) -> str | None:
    """CHG 缺必填欄——`doc_integrity_check` 明文檢查欄位齊全。"""
    _touch(fx, "docs/ai-sdlc-suite/changes/CHG-20991231-99.md",
           "# CHG-20991231-99 — 缺欄的壞樣本\n\n沒有 Project、沒有 Date、沒有狀態。\n")
    return None


def _bad_bilingual(fx: Path) -> str | None:
    """**只有中文側的孤兒譯稿**——這道閘歷史上無聲放行的那個方向。

    C2 第二筆時我第一版就是種 zh-only,而當時 `bilingual_check` 走的是 `*.md`
    (英側)再查 zh 在不在——**反方向根本不在它的迴圈裡**,於是它照樣綠,
    金絲雀把它判成 `fail-open`。那時的結論是「我的樣本打錯射程,不是閘壞了」,
    樣本因此改種 en-only,而那個單向性登記成待補項 #61。

    **CHG-20260815-04 把 #61 修掉了**,所以樣本換回 zh-only:金絲雀的獨特價值是
    證明「接上線的閘擋得住」,而孤兒譯稿正是這道閘歷史上放行過的方向
    ——釘在它身上,邊際資訊量最大。舊方向(有 en 缺 zh)由
    `test_bilingual_check.py` 的斷言每輪守著,不會失守(兩席)。

    一則就夠:`SAMPLES` 是「閘名 → 單一 Sample」,而 `witness` 是單一字串
    ——兩個方向的壞檔種進同一個 fixture,witness 只能指名其中一個,
    另一個對判定**零貢獻,純裝飾**。
    """
    _touch(fx, "skills/ai-sdlc-autopilot/references/canary-orphan.zh-tw.md",
           "# 金絲雀樣本\n\n## 一\n\n中文側存在而英側缺席。\n")
    return None


def _bad_json(fx: Path) -> str | None:
    """把被 glob 到的其中一份 JSON 弄壞。"""
    _touch(fx, "skills/ai-sdlc-autopilot/assets/canary_broken.json", "{ 這不是 JSON")
    return None


def _bad_plan_check(fx: Path) -> str | None:
    """有 Global Constraints 而 task 編號不連續——plan-check 的判準之一。"""
    _touch(fx, "docs/ai-sdlc-suite/changes/CHG-20991231-98.md",
           "# CHG-20991231-98\n\n### Global Constraints\n\n- 無\n\n"
           "### Tasks\n\n- [x] T1. 甲\n  - interfaces: a / b\n  - test: x\n"
           "- [x] T7. 乙\n  - interfaces: a / b\n  - test: x\n")
    return None


def _bad_static(fx: Path) -> str | None:
    """`shell=True` 是 `static_check` 明文擋的一條,而這個檔不在 allowlist 裡。"""
    _touch(fx, "skills/ai-sdlc-autopilot/scripts/canary_bad_static.py",
           "import subprocess\n\n\ndef go(cmd):\n"
           "    return subprocess.run(cmd, shell=True)\n")
    return None


def _wrong_case_path(d: dict) -> str | None:
    """挑一條**真的以 `skills/` 開頭**的宣告路徑改大小寫。

    第一版寫 `files[0].replace("skills/", …)`,而排序後的 `files[0]` 是
    `.github/…`——**replace 是 no-op,壞樣本從來沒種下去**,於是閘照樣綠
    而金絲雀誤判。第 0 問現在做進 `_edit_json` 了,這種錯會被當場擋下。
    """
    files = d.get("files")
    if not isinstance(files, list) or not files:
        return "錨定清單是空的,改不出大小寫錯誤"
    for i, f in enumerate(files):
        if isinstance(f, str) and f.startswith("skills/"):
            files[i] = "Skills/" + f[len("skills/"):]
            return None
    return "錨定清單裡沒有以 skills/ 開頭的路徑,改不出大小寫錯誤"


def _bad_path_case(fx: Path) -> str | None:
    """**宣告檔裡**的路徑大小寫寫錯——Windows 上 `exists()` 不分,逐段比對才分得出來。

    第一版把錯的大小寫寫在 CHG markdown 裡,而 `check_path_case` 讀的是
    **JSON 宣告檔**(`SOURCES`),markdown 根本不在它的射程內——照樣綠,
    而金絲雀誤判成 `fail-open`。同一課:壞樣本要打在那道閘真正讀的地方。
    """
    return _edit_json(fx, "skills/ai-sdlc-autopilot/assets/verifier_manifest.json",
                      _wrong_case_path)


def _bad_probe_leftover(fx: Path) -> str | None:
    """探針變體殘留——**這一道的壞樣本天生只能活在 fixture 裡**,最乾淨的一道。"""
    _touch(fx, "skills/ai-sdlc-autopilot/scripts/_probe_tmp_canary.py", "# 殘留\n")
    return None


def _tamper_first_claim(d: dict) -> str | None:
    if not d.get("claims"):
        return "fixture 的清冊是空的,改不出不一致"
    d["claims"][0]["claim_text"] = str(d["claims"][0].get("claim_text", "")) + "(被動過手腳)"
    return None


def _bad_claim_manifest(fx: Path) -> str | None:
    """手改清冊一格——`--check` 必須抓到它與實際抽取結果不一致。"""
    return _edit_json(fx, "skills/ai-sdlc-autopilot/assets/claim_manifest.json",
                      _tamper_first_claim)


def _bad_doc_claims(fx: Path) -> str | None:
    """文件宣稱清冊同形。"""
    return _edit_json(fx, "skills/ai-sdlc-autopilot/assets/doc_claims.json",
                      _tamper_first_claim)


def _bad_scope_declared(fx: Path) -> str | None:
    """新增一道**沒有宣告適用對象**的閘——「所有 CI 都明訂適用對象」的反例。"""
    p = fx / CI_LOCAL
    try:
        src = p.read_text(encoding="utf-8")
    except OSError as e:
        return f"讀不到 fixture 的接線({e})"
    p.write_text(src + '\nstep "金絲雀沒有宣告的閘"   true\n', encoding="utf-8")
    return None


def _bad_py_compile(fx: Path) -> str | None:
    _touch(fx, "skills/ai-sdlc-autopilot/scripts/canary_syntax_error.py",
           "def broken(:\n    pass\n")
    return None


def _bad_plugin_copies(fx: Path) -> str | None:
    """複本與來源分岔一個位元組——`build_suite --check` 的整個存在理由。"""
    p = fx / "plugins/ai-sdlc-suite/skills/ai-sdlc-autopilot/SKILL.md"
    try:
        p.write_text(p.read_text(encoding="utf-8") + "\n<!-- 金絲雀:複本被動過 -->\n",
                     encoding="utf-8")
    except OSError as e:
        return f"讀不到 fixture 的 plugin 複本({e})"
    return None


def _drop_catalog_version(d: dict) -> str | None:
    for pl in d.get("plugins", []):
        if isinstance(pl, dict) and "version" in pl:
            pl["version"] = "0.0.1"
            return None
    return "marketplace 裡找不到帶版號的 plugin 條目"


def _bad_catalog(fx: Path) -> str | None:
    """marketplace 與 plugin.json 的版號不合。"""
    return _edit_json(fx, ".claude-plugin/marketplace.json", _drop_catalog_version)


# 甲組 13 道:純 fixture、變異單一、期待非零退出碼。
# `(變異, witness)`。**witness 是歸因**:閘轉紅時的輸出必須指名這個壞樣本,
# 否則它可能紅在別的地方;閘照樣綠而基線輸出提過它,才敢說 `fail-open`。
# ── 乙丙組 8 則(CHG-20260814-02,C2 第三筆)──────────────────────────
#
# 三道 runner 閘跑的是**同一批測試**,差別只在編碼環境。所以「它紅了」證明不了
# 它們各自存在的理由——**要證明的是那條軸**。判準是差分:這一輪紅**而對照輪綠**。
# 少了後半,三道閘會共用一份證據,而**共用證據等於其中兩道沒有證據**。

DEFAULT_ROUND = "bash .github/run_tests.sh"
CP932_ROUND = "bash -c 'PYTHONIOENCODING=cp932 bash .github/run_tests.sh'"

# **三則 planted 測試檔一律不 print。** 本 repo 有一道結構閘要求
# 「會輸出到 stdout 的進入點必須自己釘住 utf-8」,而那道閘在**三輪都紅**
# ——第一版的樣本因此被判 `sample-out-of-range`(而那是判對的:紅得不是軸專屬)。
# 不輸出的檔在那道閘的射程外,於是紅只剩下我們要證明的那一條。
# 歸因仍然成立:`run_tests.sh` 的失敗清單會列出檔名。

FAILING_TEST = """import sys


def main():
    checks = [("金絲雀:這一條必敗", False)]
    return 1 if [n for n, ok in checks if not ok] else 0


if __name__ == "__main__":
    sys.exit(main())
"""

# cp932 那條軸:**問 stdio 編碼編不編得出非 ASCII**——那正是這道閘存在的理由。
# 為什麼不 print?本 repo 有一道結構閘要求「會輸出到 stdout 的進入點必須自己釘
# utf-8」,而它的判準是 `print(` 這個**字面 token**——連寫在字串裡都算。
# 釘了就永遠不炸,不釘就被那道閘擋下,**兩條路都紅在別的地方**。
# 不輸出而直接問編碼,打中的才是這道閘自己那條軸。
CP932_TEST = """import sys


def main():
    "\N{ROCKET} 金絲雀".encode(sys.stdout.encoding or "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""

# UTF-8 那條軸的鏡像:不明寫 `encoding=` 的 `open()` 走的正是平台預設編碼。
# `PYTHONUTF8=1` 那一輪預設變成 UTF-8,讀 cp950 位元組即 UnicodeDecodeError;
# 對照的 cp932 輪沒設 `PYTHONUTF8`(它只動 stdio),預設仍是平台編碼,讀得回來。
#
# 位元組**放資料檔不放 `.py`**:原始碼固定以 UTF-8 解析,壞位元組會讓檔案
# 自己 SyntaxError,於是三輪全紅——差分當場塌掉。(fable)
NATIVE_BYTES_TEST = """import sys
from pathlib import Path


def main():
    p = Path(__file__).with_name("canary_native_bytes.txt")
    with open(p) as fh:            # noqa: PTH123 —— **刻意**不寫 encoding=
        fh.read()
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""

FAILING_FEATURE = """# language: zh-TW
功能: 金絲雀
  場景: 必敗的一條
    那麼 金絲雀步驟應該失敗
"""

FAILING_FEATURE_STEPS = '''from behave import then  # noqa: F401


@then('金絲雀步驟應該失敗')
def step_canary_fail(context):
    assert False, "金絲雀:這一條必敗"
'''

SHELL_TRUE_SRC = '''"""金絲雀:一支帶 `shell=True` 且不在具名豁免清單裡的檔。"""
import subprocess


def run_it(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True)  # noqa: S602
'''

UNCOVERED_SRC = '''"""金絲雀:一支**沒有任何測試碰過**的新模組。"""


def never_called(a, b):
    total = a + b
    if total > 0:
        return total
    return 0
'''


def _behave_present(fx: Path) -> tuple[str, str]:
    """**純觀測**:behave 在不在。零副作用——不裝、不改環境、不種檔案。

    > 不同意手填、可任意改環境的逃生口。形狀應是宣告式 `preflight`:
    > 只回 `ready / blocked(reason,evidence) / error`;工具在場性在此探測。(codex)

    缺席時這道閘**根本不會跑到規格層**,而那是具名阻礙不是「擋得住」。
    """
    rc, out = run_gate(fx, '"$PY" -c "import behave; print(behave.__version__)"', 120)
    if rc == 0:
        return "ready", f"behave {out.strip()[:40]}"
    if rc in (-1, -2):
        return "error", f"探測 behave 時儀器自己出錯(rc={rc}):{out[:80]}"
    return "blocked", f"behave 不在這個環境(rc={rc})——規格層不會執行"


def _utf8_axis_exists(fx: Path) -> tuple[str, str]:
    """**純觀測**:UTF-8 那條差分軸存不存在。

    判準是**行為不是名字**(CHG-20260810-01 付過的學費:編碼名稱是系統語言
    設定的產物,同一台機器改個設定就從 cp950 變 cp1252,兩者行為同軸)。
    所以用 `lib.capabilities` 的往返探針:UTF-8 寫進去、用平台預設編碼讀回來,
    內容一致嗎?一致 = 這台機器原生就是 UTF-8 = **差分軸不存在**,
    UTF-8 輪與對照輪跑的是同一件事,樣本不可能只紅一邊。
    """
    rc, out = run_gate(
        fx, '"$PY" -c "import sys; sys.path.insert(0, '
            "'skills/ai-sdlc-autopilot/scripts'); from lib.capabilities import supports; "
            'print(\'yes\' if supports(\'utf8_locale\') else \'no\')"', 120)
    if rc != 0:
        return "error", f"往返探針自己跑不起來(rc={rc}):{out[:80]}"
    ans = out.strip().splitlines()[-1].strip() if out.strip() else ""
    if ans == "yes":
        return ("blocked", "平台預設編碼往返成功 = 這台機器原生就是 UTF-8"
                           "——**差分軸不存在**,UTF-8 輪與對照輪跑的是同一件事")
    if ans == "no":
        return "ready", "平台預設編碼往返失敗 = 非 UTF-8 機器,差分軸在"
    return "error", f"往返探針回了看不懂的答案:{ans!r}"


def _setup_coverage_artifact(fx: Path) -> tuple[str | None, dict]:
    """種一份合成 coverage 產物,並宣告 `DIFFCOV_CHG`。

    這道閘在 fixture 裡有兩個前置條件,而**兩個都不是輔助,是命脈**:

    1. fixture 是 `--detach` 的 worktree,`derive_chg` 的 `rev-parse --abbrev-ref`
       回 `HEAD` → 推不出編號;而 `changed` 必然非空(三點 diff 從 merge-base 起算,
       本分支已 commit 的 `.py` 全在裡面)。於是不給 `DIFFCOV_CHG` 時這道閘
       **在乾淨的 fixture 上就 exit 1**——一道好閘被記成 `baseline-red`。
    2. 樣本路徑必須**真的出現在 report 的 missing_lines 裡**:沒有條目時
       `miss is None` → `out_of_scope` → rc 0,壞樣本會靜靜飄過去。

    `DIFFCOV_CHG` 因此不是「多給一個 env」,而是**頂替了分支名推導那條路**
    ——row 裡要說出這件事(見 `note`):分支推導那條路徑沒有被本次證明行使過。
    """
    rel = "skills/ai-sdlc-autopilot/scripts/canary_uncovered.py"
    _touch(fx, "artifacts/coverage-report.json", json.dumps(
        {"files": {rel: {"missing_lines": [4, 5, 6, 7, 8]}}}, ensure_ascii=False) + "\n")
    return None, {"files": ["artifacts/coverage-report.json"],
                  "env": {"DIFFCOV_CHG": "CHG-20260814-02"},
                  "note": "fixture 是 detached worktree,分支名推導那條路徑"
                          "**未被本次證明行使**;編號由 setup 宣告的 env 頂替"}


def _bump_test_counts(fx: Path) -> str | None:
    """把**每一處**手抄的 `test_*.py` 支數 +1。

    清單從 fixture 自己的 `doc_counts.json` 讀——那份檔案就是本 repo 用來對帳
    手抄數字的資料。**手抄第二份清單就是 KN-005 的形狀**:實測時我只補了
    `AGENTS.md`,而 `docs/worklog/handshake-autopilot.md` 也有一處,
    於是對照輪照樣紅,差分再次塌掉。

    不補的話,`doc counts` 那道閘會在**每一輪**紅,而那條紅與這則樣本要證明的
    軸無關——它會蓋掉差分。**companion edit 是為了讓紅可歸因,不是為了讓它變綠。**
    """
    try:
        spec = json.loads((fx / "skills/ai-sdlc-autopilot/assets/doc_counts.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return f"讀不到 fixture 的 doc_counts.json({e})——補不了手抄的支數"
    claims = [c for c in spec.get("claims", []) if c.get("measure") == "test_files"]
    if not claims:
        return "doc_counts.json 裡沒有 test_files 的宣稱——樣本過期了"
    for c in claims:
        f = fx / c["file"]
        try:
            src = f.read_text(encoding="utf-8")
        except OSError as e:  # diffcov-exempt: syserr — worktree 剛簽出的檔案讀不回來屬系統層故障 [signoff: codex+fable @ CHG-20260814-02]
            return f"讀不到 fixture 的 {c['file']}({e})"  # diffcov-exempt: syserr — 同上:worktree 剛簽出的檔案讀不回來屬系統層故障 [signoff: codex+fable @ CHG-20260814-02]
        after, n = re.subn(c["pattern"],
                           lambda m: m.group(0).replace(m.group(1),
                                                        str(int(m.group(1)) + 1)),
                           src, count=1)
        if n != 1:
            return f"{c['file']} 裡的支數宣稱命中 {n} 次(應為 1)——樣本過期了"
        f.write_text(after, encoding="utf-8")
    return None


def _plant_test(fx: Path, rel: str, body: str) -> str | None:
    """種一支測試檔,**並把每一處手抄的支數一起帶上**。"""
    _touch(fx, rel, body)
    return _bump_test_counts(fx)


def _bad_tests(fx: Path) -> str | None:
    """一支必敗的測試——`run_tests.sh` 宣稱它會把失敗彙總成非零。"""
    return _plant_test(fx, "skills/ai-sdlc-autopilot/scripts/test_canary_failing.py",
                       FAILING_TEST)


def _bad_cp932(fx: Path) -> str | None:
    """子行程裸印非 ASCII:**只在 cp932 那一輪**炸,對照的預設輪照樣綠。"""
    return _plant_test(fx, "skills/ai-sdlc-autopilot/scripts/test_canary_cp932.py",
                       CP932_TEST)


def _bad_utf8_round(fx: Path) -> str | None:
    """鏡像樣本:**只在 UTF-8 那一輪**失敗。"""
    p = fx / "skills/ai-sdlc-autopilot/scripts/canary_native_bytes.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_bytes("金絲雀:平台預設編碼的位元組\n".encode("cp950"))
    except (OSError, UnicodeEncodeError) as e:  # diffcov-exempt: syserr — 這台機器原生就是 cp950,要讓它編不出 cp950 得換一台機器 [signoff: codex+fable @ CHG-20260814-02]
        return f"寫不出 cp950 位元組({e})——造不出這條軸的樣本"  # diffcov-exempt: syserr — 這台機器原生就是 cp950,要讓它編碼失敗得換一台機器 [signoff: codex+fable @ CHG-20260814-02]
    return _plant_test(
        fx, "skills/ai-sdlc-autopilot/scripts/test_canary_native_bytes.py",
        NATIVE_BYTES_TEST)


def _bad_behave(fx: Path) -> str | None:
    """一條必敗的場景,**而且是「失敗」不是「未定義」**。

    未定義的步驟也會讓 `run_gherkin.sh` 紅,但那條紅的訊息是 undefined 計數,
    歸因不到這個樣本頭上。所以場景與它的步驟實作一起種下去。
    """
    _touch(fx, "features/canary_failing.feature", FAILING_FEATURE)
    _touch(fx, "features/steps/canary_failing_steps.py", FAILING_FEATURE_STEPS)
    return None


def _bad_verifier_integrity(fx: Path) -> str | None:
    """竄改一個**在錨定清單內**的檔案——這道閘存在的唯一理由就是抓這個。

    **不改 `verifier_integrity.py` 自己**:自噬會讓紅的形狀不可預測
    (它可能在讀基線時就死,而那條紅歸因不到竄改)。
    """
    manifest = fx / "skills/ai-sdlc-autopilot/assets/verifier_manifest.json"
    try:
        files = json.loads(manifest.read_text(encoding="utf-8")).get("files") or []
    except (OSError, ValueError) as e:
        return f"讀不到 fixture 的錨定清單({e})"
    for rel in sorted(files):
        if rel.endswith("verifier_integrity.py"):
            continue
        target = fx / rel
        if target.is_file():
            before = target.read_bytes()
            target.write_bytes(before + "\n# 金絲雀:被竄改過\n".encode("utf-8"))
            # KN-008 第 0 問:位元組真的變了嗎?
            if target.read_bytes() == before:
                return f"寫回 {rel} 之後位元組沒變——**竄改沒種下去**"  # diffcov-exempt: defensive — 追加位元組後內容必變,這是 KN-008 第 0 問的防禦性守衛 [signoff: codex+fable @ CHG-20260814-02]
            return None
    return "錨定清單裡的檔案在 fixture 中都不存在(或只剩驗證器自己),改不出竄改"


def _bad_operational_verify(fx: Path) -> str | None:
    """把 fixture 的 `verifier_integrity.py --check` 中和成「永遠不報改動」。

    `verify.sh` 的 [3] 段自己種一個探針(往 `static_check.py` 追加一行),
    然後**要求 `--check` 紅**;中和之後它照樣綠,於是 verify.sh 印出
    「驗證器被改過卻沒紅 —— 錨定等於不存在」並 exit 1。**這道閘擋的正是這個。**

    用具名的字面替換而不是盲改:找不到插入點就回具名理由。
    改壞語法的話 `verify.sh` 會在 [3] 的第一次 `--check` 就死,witness 不會出現,
    判定塌成 `sample-out-of-range`——白跑一輪 15 分鐘。
    """
    p = fx / "skills/ai-sdlc-autopilot/scripts/verifier_integrity.py"
    anchor = "    if changed or added or removed:"
    try:
        src = p.read_text(encoding="utf-8")
    except OSError as e:
        return f"讀不到 fixture 的 verifier_integrity.py({e})"
    if anchor not in src:
        return f"在 verifier_integrity.py 裡找不到插入點 {anchor.strip()!r}——樣本過期了"
    after = src.replace(anchor, "    if False and (changed or added or removed):", 1)
    if after == src:
        return "替換之後內容沒變——**中和沒套用**(KN-008 第 0 問)"  # diffcov-exempt: defensive — 上面已確認 anchor 在,而 anchor 與替換字串不同,replace 必然改到東西;這是第 0 問的防禦性守衛 [signoff: codex+fable @ CHG-20260814-02]
    p.write_text(after, encoding="utf-8")
    return None


def _bad_quality(fx: Path) -> str | None:
    """種一支帶 `shell=True` 且**不在具名豁免清單裡**的檔。

    `run_quality.sh` 的 judge 把它讀成一個相對基線的**新增** bandit 發現(B602)。
    誠實揭露:同一支檔也沒有任何測試碰得到,所以 coverage 那一軸可能同時轉紅
    ——**兩軸的紅都是這道閘真的在擋**,而 evidence 會照實寫兩軸,不寫成單軸。
    """
    _touch(fx, "skills/ai-sdlc-autopilot/scripts/canary_shell_true.py", SHELL_TRUE_SRC)
    return None


def _bad_diff_coverage(fx: Path) -> str | None:
    """種一支**未追蹤的、沒有任何測試碰過的** `.py`。

    未追蹤的新檔**整支都算改動行**(這道閘自己的註解寫明了),而 setup 種的
    合成 report 把它的行列進 `missing_lines`——於是它是「未登記的未覆蓋改動行」。

    **放 `skills/` 下不放 `.github/`**:coverage 的 `--source` 不含 `.github`,
    種在那裡會恆走 `out_of_scope`(rc 0),等於量到一個真閘給不出的 `blocks`
    ——比不驗更糟。(fable)
    """
    _touch(fx, "skills/ai-sdlc-autopilot/scripts/canary_uncovered.py", UNCOVERED_SRC)
    return None


SAMPLES = {
    "doc integrity": Sample(_bad_doc_integrity, "CHG-20991231-99"),
    "bilingual pairs": Sample(_bad_bilingual, "canary-orphan"),
    # **五則原本 `witness=None`**,而那讓它們的 `blocks` 比另外八則弱
    # ——「程序擋下了」不等於「由目標閘擋下」,可能是旁路錯誤(codex)。
    # 清冊上兩者長得一模一樣,那正是「判準比證據弱」。這裡逐則補上該閘
    # **擋下時真的會印的措辭**。
    "JSON validity": Sample(_bad_json, "Expecting"),
    "autopilot plan-check": Sample(_bad_plan_check, "CHG-20991231-98"),
    "static & security check": Sample(_bad_static, "canary_bad_static"),
    "path case sensitivity (Linux 相容)": Sample(_bad_path_case, "Skills/"),
    "probe leftovers": Sample(_bad_probe_leftover, "_probe_tmp_canary"),
    "claim manifest": Sample(_bad_claim_manifest, "與實際掃描結果不一致"),
    "doc claims": Sample(_bad_doc_claims, "與實際掃描結果不一致"),
    "check scope declared": Sample(_bad_scope_declared, "金絲雀沒有宣告的閘"),
    "py_compile all scripts": Sample(_bad_py_compile, "canary_syntax_error"),
    "plugin skills copies": Sample(_bad_plugin_copies, "複本與來源不同步"),
    "catalog version": Sample(_bad_catalog, "catalog-check 未通過"),
    # ── 乙丙組 8 則(C2 第三筆)。witness 一律取該載具**自己原始碼裡的措辭**。
    "run all test_*.py": Sample(_bad_tests, "test_canary_failing.py"),
    "run all test_*.py(非 UTF-8 stdio 輪:cp932)": Sample(
        _bad_cp932, "test_canary_cp932.py", also_green=DEFAULT_ROUND),
    "run all test_*.py(UTF-8 profile 輪:= hosted runner 的編碼環境)": Sample(
        _bad_utf8_round, "test_canary_native_bytes.py",
        preflight=_utf8_axis_exists, also_green=CP932_ROUND),
    "run behaviour specs": Sample(_bad_behave, "canary_failing.feature",
                                  preflight=_behave_present),
    "verifier integrity": Sample(_bad_verifier_integrity,
                                 "驗證器已被修改,但基線未經授權更新"),
    "operational verify": Sample(_bad_operational_verify, "驗證器被改過卻沒紅",
                                 preflight=_behave_present),
    # 預設 900s 不夠:C2-C 實測裸跑 402s,而這台機器在負載下會超過預設值
    # ——實測到的是 `unverified-blocked`(「基線在預算內跑不完」),而那個判定是對的。
    # 處置是**動撥盤不動上限**:預算調到治理過的 `MAX_PROBE_BUDGET`,
    # 上限本身一格都沒動(改它才需要重簽錨定)。這正是 C2-C 把
    # 「預設值」與「治理過的上限」分開的用途。
    "delegated quality checks": Sample(_bad_quality, "canary_shell_true.py",
                                      budget=MAX_PROBE_BUDGET),
    "diff coverage": Sample(_bad_diff_coverage, "未登記的未覆蓋改動行",
                            setup=_setup_coverage_artifact),
}


def reconcile(gates: dict) -> list:
    """**樣本表與接線的兩個方向都要對帳。**

    這張表是手寫的資料,而手寫的表會與現實分岔(KN-005)。
    表裡有而接線沒有 = 名字漂了;接線有而表裡沒有 = 新閘沒人驗。
    只守一邊的話,另一邊就是無聲的——而無聲正是這一整套機制要消滅的東西。
    """
    problems = []
    ghost = sorted(set(SAMPLES) - set(gates))
    if ghost:
        problems.append(f"{len(ghost)} 則樣本對不到任何閘(名字漂了?):{ghost}")
    return problems


def probe_all(repo: Path, only=None):
    """逐閘探。**每一道閘用自己的 fixture**——共用一個 worktree 會讓前一道的壞樣本
    污染下一道的基線,於是判定塌縮成 `baseline-red`,而那看起來像閘壞了。"""
    gates = discover(repo)
    rows, problems = [], reconcile(gates)
    for name in sorted(gates):
        if only and name != only:
            continue
        g = gates[name]
        row = {"claim_id": f"gate::{name}", "condition_digest": g["digest"]}
        if name not in SAMPLES:
            row.update({"verdict": "no-sample",
                        "evidence": "壞樣本尚未登記——**不是「這道閘沒問題」**"})
            rows.append(row)
            continue
        with Fixture(repo) as fx:
            if fx.path is None:
                row.update({"verdict": "unverified-blocked",
                            "evidence": f"fixture 建不起來:{getattr(fx, 'error', '')[:120]}"})
            else:
                if fx.dirty:
                    # 髒樹:fixture 站在 HEAD,而正式執行跑的是工作樹。
                    # 這不是「沒問題」,是**量到的不是你在跑的那份程式**(codex)。
                    row["dirty_worktree"] = len(fx.dirty)
                # **沒有全域預算覆寫**:每則樣本用自己宣告的預算,
                # 而上限由 `MAX_PROBE_BUDGET` 治理(見該常數的註解)。
                v, why, extra = probe_gate(fx.path, g["cmd"], SAMPLES[name])
                row.update({"verdict": v, "evidence": why, **extra})
        rows.append(row)
    return rows, problems


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--list", action="store_true", help="只列出發現的閘與其 digest")
    ap.add_argument("--gate", default=None, help="只探這一道(除錯用)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv[1:])
    repo = Path(a.repo).resolve()

    gates = discover(repo)
    if not gates:
        print("❌ 一道閘都沒發現——**發現階段壞了**,而那不是「這個 repo 沒有閘」(KN-001)")
        return 2

    if a.list:
        rows = [{"claim_id": f"gate::{n}", "condition_digest": g["digest"],
                 "cmd": " ".join(g["cmd"].split())[:80]}
                for n, g in sorted(gates.items())]
        if a.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            for r in rows:
                print(f"{r['condition_digest']}  {r['claim_id']}")
            print(f"\n共 {len(rows)} 道閘。")
        return 0

    rows, problems = probe_all(repo, only=a.gate)
    by: dict[str, list[str]] = {}
    for r in rows:
        by.setdefault(r["verdict"], []).append(r["claim_id"])

    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        marks = {"blocks": "✅", "fail-open": "❌", "baseline-red": "⚠️",
                 "unverified-blocked": "⚠️", "no-sample": "○",
                 "sample-out-of-range": "⚠️"}
        for r in rows:
            print(f"{marks.get(r['verdict'], '?')} {r['verdict']:<20} {r['claim_id']}")
            if r["verdict"] != "blocks":
                print(f"     {r['evidence'][:110]}")
        for p in problems:
            print(f"❌ 對帳:{p}")
        dirty = sum(1 for r in rows if r.get("dirty_worktree"))
        if dirty:
            print(f"\n⚠️ 工作樹有未提交內容:fixture 站在 HEAD,而正式執行跑的是工作樹"
                  f"——**量到的不是你在跑的那份程式**({dirty} 道閘受影響)")

    bad = len(by.get("fail-open", []))
    proven = len(by.get("blocks", []))
    unproven = len(rows) - proven - bad
    if not a.json:
        print(f"\n擋得住 {proven} / **fail-open {bad}** / 未證明 {unproven}(共 {len(rows)} 道)")
    # **`--json` 只吐 JSON,不混人看的摘要。** 第一版把下面幾行的 `print` 留在外面,
    # 於是 artifact 的尾巴多了兩行中文,解析當場炸掉——而
    # CHG-20260805-06 為同一個坑付過一次學費(`assertion_probe` 的 `--json`)。
    say = (lambda *x: None) if a.json else print
    if problems:
        return 2
    if bad:
        say(f"\n❌ {bad} 道閘對壞樣本放行——它們沒在擋任何東西。")
        return 1
    if unproven:
        say(f"\n⚠️ **射程外/未證明**:{unproven} 道還沒有壞樣本或探不了"
            f"——**那不是「沒有 fail-open」**。")
        return 3
    say("\n✅ 每一道閘都對壞樣本轉紅了。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))  # diffcov-exempt: defensive — `__main__` 入口,覆蓋率是以匯入方式跑的,永遠走不到;判定邏輯全在 `main()` 內且已逐條受測 [signoff: codex+fable @ CHG-20260813-09]
