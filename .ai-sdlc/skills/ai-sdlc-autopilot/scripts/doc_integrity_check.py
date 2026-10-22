#!/usr/bin/env python3
"""
doc_integrity_check.py — 文檔抗漂移的機器檢查 / doc-integrity enforcement

把 doc-integrity 從「靠自律遵守」變成「CI / pre-commit 可擋」。它不替你寫文件的語意內容
(那需要人/agent),但會把可機器判斷的漂移擋下,逼你補齊。

檢查項:
  1) 結構漂移:本次(staged)改了結構性程式(預設比對 models / schema / migration / .proto),
     卻沒有一併更動 docs/structure/ → 失敗。(對應「改結構就要同步結構文件」)
  2) CHG↔ACC 連結:docs/changes/ 內狀態為「已實作 / Implemented」(非草稿、非暫停)的 CHG,
     若 docs/acceptance/ 沒有任何 ACC 提到它 → 失敗。(對應「當場驗收、不可懸空」;
     「暫停 / Paused」為合法 WIP,跳過)
  3) 模板欄位 lint:CHG 必填 風險分級/Risk、實作者/Implemented by、狀態/Status;
     ACC 必填 驗收者/Verifier、結論/Conclusion、風險分級/Risk。缺 → 失敗。
     (--require-branch / --require-commit 額外強制 Branch、Commit/PR 欄)
  4) secrets 掃描:docs/ 內出現疑似金鑰/token/私鑰 → 失敗。(文件長存共用,不可含 secrets)
  5) commit 治理掃描(--commits-since <ref>):<ref>..HEAD 的每個 commit message 都應
     引用 CHG/XCHG 編號;沒有 → 失敗。(對應「commit 粒度 / commit 錨定」)
  6) 知識庫先建:受治理 repo(有 docs/changes/)必有 docs/knowledge/ → 缺 = 失敗。
     (對應 knowledge「先建」與 handshake 進場補建;容器不存在,自主記錄永遠不會發生)
  7b) 驗收者身分:Skill ≥ v1.65 的**高風險**已實作 CHG,其 ACC 的「驗收者」
     不得等於 CHG 的「實作者」→ 失敗。比不出來也失敗(fail-closed)。
     (對應 independent-acceptance「驗收者 ≠ 實作者」與 ci-cd 的身分檢查 gate)
  7) 重複性檢查欄:Skill ≥ v1.17 的 CHG 必有「重複性檢查/Recurrence check」欄 → 缺 = 失敗。
     (前瞻適用;對應 modification-guide 第 7 步收尾比對)

用法 / usage:
  # pre-commit(staged 結構漂移 + CHG/ACC + 欄位 + secrets):
  python3 doc_integrity_check.py --staged
  # 全 repo 掃描(CI / 手動):
  python3 doc_integrity_check.py --repo .
  # 進場 handshake 的 commit 掃描(錨點=最後治理 commit / tag):
  python3 doc_integrity_check.py --repo . --commits-since <anchor>
  # 自訂結構性路徑(regex,可多個):
  python3 doc_integrity_check.py --staged --structural 'models/' 'schema' '\\.proto$'
  # 逃生口:--no-field-lint / --no-secret-scan

退出碼:0 = 通過;1 = 偵測到問題;2 = 環境/參數錯誤。
"""
from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# 釘住輸出編碼(CHG-20260803-01 T1):不依賴主控台/locale 的 ambient 編碼。
# 非 UTF-8 主控台(如 Windows cp932)印 CJK/emoji 會 UnicodeEncodeError;
# 釘住後同一份程式在任何平台的輸出行為一致。errors="replace" 確保永不因輸出而崩潰。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_STRUCTURAL = [r"models?/", r"schema", r"migrations?/", r"\.proto$", r"entities?/"]
CHG_RE = re.compile(r"X?CHG-\d{8}-\d+", re.IGNORECASE)
# 視為「已實作、應有 ACC」的狀態字樣
IMPLEMENTED_HINTS = ["已實作", "已驗收", "implemented", "accepted", "待驗收", "待 acceptance", "pending acceptance"]
DRAFT_HINTS = ["草稿", "draft"]
PAUSED_HINTS = ["暫停", "paused"]
# CHG-lite:低風險 + 內嵌自驗 → 豁免獨立 ACC 檔(見 modification-guide「CHG-lite」)
SELF_ACC_RE = re.compile(r"自驗|self-?verified", re.IGNORECASE)
LOW_RISK_RE = re.compile(r"(風險分級|Risk)\s*[::]\s*[^\n]{0,40}?(低|low)", re.IGNORECASE)
# 審議會:高風險已實作 CHG 必附審議判決(見 review-panel)
HIGH_RISK_RE = re.compile(r"(風險分級|Risk)\s*[::]\s*[^\n]{0,40}?(高|high)", re.IGNORECASE)
VERDICT_RE = re.compile(r"\[verdict\]|審議判決|Review verdicts", re.IGNORECASE)

# --- 欄位 lint(雙語;pattern 命中任一即算有該欄) ---
CHG_REQUIRED_FIELDS = {
    # Risk 允許行首或 lite 單行式的「| Risk:」位置
    "風險分級/Risk": re.compile(r"(風險分級|(^|\|)\s*\-?\s*Risk)\s*[::]", re.MULTILINE),
    "實作者/Implemented by": re.compile(r"(實作者|Implemented by)\s*[::]"),
    "狀態/Status": re.compile(r"^##\s*(狀態|Status)\b", re.MULTILINE),
}
ACC_REQUIRED_FIELDS = {
    "驗收者/Verifier": re.compile(r"(驗收者|Verifier)\s*[::]"),
    # 段標題也算(CHG-20260804-17)。原本只認 `結論:`,而 ACC 模板寫的是 `## 結論`,
    # 於是 20 份格式完全正確的 ACC 被判缺欄。同一支檔案裡 CHG 的「狀態」早就是用
    # `^##` 判的——這裡是漏網。放寬方向:原本能過的仍然能過。
    "結論/Conclusion": re.compile(r"(結論|Conclusion)\s*[::]|^##\s*(結論|Conclusion)\b",
                                 re.MULTILINE),
    "風險分級/Risk": re.compile(r"(風險分級|(^|\|)\s*\-?\s*Risk)\s*[::]", re.MULTILINE),
}
# 雙語(CHG-20260803-02):原本只認英文 "Branch",而本套件的 CHG 模板中文寫「分支」——
# 於是 `--require-branch` 對任何中文 CHG 都必然失敗,是一道永遠無法通過的閘。
# 其餘每個欄位樣式都是雙語的,這裡是漏網。變更方向只放寬不收緊:原本能過的仍然能過。
BRANCH_FIELD = re.compile(r"(Branch|分支)\s*[::]?")
COMMIT_FIELD = re.compile(r"Commit/PR\s*[::]")

# --- secrets 掃描(保守樣式,避免誤殺) ---
SECRET_PATTERNS = [
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub token", re.compile(r"(ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,})")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.")),
    ("credential assignment", re.compile(
        r"(?i)(password|passwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*['\"][A-Za-z0-9+/_\-]{12,}['\"]")),
]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True).stdout


def git_staged_files(repo: Path) -> list[str]:
    try:
        out = git(repo, "diff", "--cached", "--name-only")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [l.strip() for l in out.splitlines() if l.strip()]


# 帳本的落點是**資料**,不是寫死在函式裡的一行:新增一個候選要能靠編輯這個常數完成。
# `docs/` 是預設;`sdlc_docs/` 是 `docs/` 已經被別人佔用時的落點——目標專案的 `docs/`
# 可能是 MkDocs / Docusaurus 的發佈根(帳本會被建進別人的產品文件),也可能是
# Sphinx 的生成產物(下一次重新生成把帳本刪掉,而且無聲)。見 references/onboarding.md。
DOC_ROOT_NAMES = ("docs", "sdlc_docs")


def doc_roots(repo: Path) -> list[Path]:
    """實際存在的候選文件根目錄,依 `DOC_ROOT_NAMES` 的順序。

    與 `ledger_roots()` 的差別是刻意的:這支回答「有沒有一個文件目錄」,
    那支回答「有沒有一本帳」。**只有後者能當治理與否的判準**——
    `docs/` 存在不代表這裡被治理,它可能整個屬於別人。
    """
    return [repo / n for n in DOC_ROOT_NAMES if (repo / n).is_dir()]


def ledger_roots(repo: Path) -> list[Path]:
    """找出所有帳本根目錄——**找,不假設**。

    同一個錯誤修過兩次,這是第三次:

    · 第一次(CHG-20260804-17):工具寫死 `docs/changes/`。對分目錄帳本的 repo
      (本 repo 就是)那些檢查**全部靜默空轉**——一份缺實作者、缺狀態、沒有對應
      ACC 的 CHG 放在 `docs/ai-sdlc-suite/changes/` 會得到 ✅,放進 `docs/changes/`
      立刻被擋三項。差別只在目錄,而回報「沒問題」比回報「有問題」更難被發現:
      沒有人會去追查一個綠燈(KN-001 的鏡像)。
    · 第二次(本筆,CHG-20260811-01):那次只修了**分目錄**那一層,`docs` 這個
      名字本身還是寫死的,而兜底 `return roots or [docs]` 更糟——找不到任何帳本時
      **回傳一個假設**。呼叫端拿到一個可能不存在的目錄,掃出零筆,然後回報沒問題。

    所以現在:候選根目錄是資料(`DOC_ROOT_NAMES`),**找不到就回空清單**。
    空清單是明確的「未治理」訊號,呼叫端必須自己決定怎麼處理(見 `main()` 的三態)。
    單帳本專案行為不變(仍回 `[docs]`),多的是 `sdlc_docs/` 與空集合這兩種。
    """
    roots: list[Path] = []
    for base in (repo / n for n in DOC_ROOT_NAMES):
        if (base / "changes").is_dir():
            roots.append(base)
        if base.is_dir():
            roots += sorted(d for d in base.iterdir()
                            if d.is_dir() and (d / "changes").is_dir())
    return roots


ARCHIVED_MARKER = ".archived"


def archived_reason(root: Path, reader=None) -> str | None:
    """這本帳是不是**封存**的(併入/結案後不再有人寫)——是就回理由,否則回 None。

    `ledger_roots()` 用**目錄形狀**發現帳本,而目錄形狀說得出「這是一本帳」,
    說不出「這本已經沒有人寫了」。差別是實在的:封存帳本裡的 knowledge 條目
    **結構上不可能升也不可能退**(不會有新 CHG 去套用它把 `applied` 加到門檻,
    也不會有使用者去糾正它讓它失效——規範的兩種轉移都被封住),
    把它算進「現在有什麼要處理」的統計,等於在總數裡混進一個永遠不會動的東西
    (CHG-20260810-02)。

    宣告方式:帳本根目錄下的 `.archived`,**內容即理由**。兩個方向都是刻意的:

    · 理由空白視同**沒宣告**——空白豁免看起來像有交代,比漏寫更糟(KN-006)。
    · 沒宣告一律倒向**「活的」**——多算一本的代價是統計多幾筆,
      漏算的代價是整本帳沒有人看管,而且不會有人抗議(KN-004,KN-001 的鏡像)。

    **封存只影響「要處理什麼」,不影響「內容對不對」**:本檔的各項檢查
    照樣掃封存帳本。把閘的範圍縮小是這個概念最容易犯、代價最大的誤用。
    """
    read = reader or (lambda p: p.read_text(encoding="utf-8"))
    marker = root / ARCHIVED_MARKER
    try:
        if not marker.is_file():
            return None
        reason = read(marker).strip()
    except (OSError, ValueError):
        # `ValueError` 是為了 `UnicodeDecodeError`——它**不是** `OSError`,
        # 而 `read_text(encoding="utf-8")` 碰到非 UTF-8 位元組就拋它。
        # 本函式的呼叫端是 `doc_integrity_check` 自己(`ci_local.sh` 第一步、git hook),
        # 一個未捕捉的例外會讓整條治理流程停在沒有指引的 traceback(KN-003)。
        # 讀不出來 → 當作沒宣告 → 倒向「活的」,與空白理由同一個方向(KN-004)。
        return None
    return reason or None


def is_archived(root: Path, reader=None) -> bool:
    """`archived_reason()` 的布林版。判定邏輯只有一份,見該函式。"""
    return archived_reason(root, reader) is not None


def check_structural_sync(changed: list[str], structural: list[str]) -> list[str]:
    pats = [re.compile(p, re.IGNORECASE) for p in structural]
    structural_changed = [f for f in changed
                          if any(p.search(f) for p in pats) and not f.startswith("docs/")]
    docs_structure_changed = any(f.startswith("docs/structure/") for f in changed)
    problems = []
    if structural_changed and not docs_structure_changed:
        problems.append("改了結構性程式卻未同步 docs/structure/ — 觸發檔:\n    "
                        + "\n    ".join(structural_changed))
    return problems


def classify_status(text: str) -> str:
    low = text.lower()
    if any(h in low for h in PAUSED_HINTS):
        return "paused"
    is_draft = any(h in low for h in DRAFT_HINTS) and not any(
        h in low for h in ("已實作", "已驗收", "implemented", "accepted"))
    if is_draft:
        return "draft"
    if any(h.lower() in low for h in IMPLEMENTED_HINTS):
        return "implemented_or_accepted"
    return "unknown"


def check_chg_acc(repo: Path, docs: Path | None = None) -> list[str]:
    # `docs` 選填:不給就沿用 `repo/docs`。**簽章保持向後相容**——
    # 這些函式被 MCP server 與 hooks 以模組方式匯入,api-contract 閘會擋
    # 「必填參數消失 / 新增必填參數」,而它擋得對(CHG-20260804-17)。
    docs = docs if docs is not None else repo / "docs"
    changes = sorted((docs / "changes").glob("CHG-*.md")) if (docs / "changes").is_dir() else []
    acc_dir = docs / "acceptance"
    acc_text = ""
    if acc_dir.is_dir():
        for a in acc_dir.glob("ACC-*.md"):
            acc_text += a.read_text(encoding="utf-8", errors="ignore") + "\n"
    problems = []
    for chg in changes:
        text = chg.read_text(encoding="utf-8", errors="ignore")
        status = classify_status(text)
        if status in ("draft", "paused"):  # 草稿與暫停(合法 WIP)不要求 ACC
            continue
        if status == "unknown":
            continue
        if SELF_ACC_RE.search(text) and LOW_RISK_RE.search(text):
            continue  # CHG-lite:低風險內嵌自驗,免獨立 ACC
        m = CHG_RE.search(chg.stem) or CHG_RE.search(text)
        chg_id = m.group(0) if m else chg.stem
        if chg_id.lower() not in acc_text.lower():
            problems.append(f"{chg.name}({chg_id})已實作但 docs/acceptance/ 找不到對應 ACC — 驗收懸空")
        if HIGH_RISK_RE.search(text) and not VERDICT_RE.search(text):
            problems.append(f"{chg.name}({chg_id})為高風險且已實作,但無審議判決([verdict] / 審議判決節)— 高風險必須全席審議(見 review-panel)")
    return problems


# 身分比對(CHG-20260901-01)。`modification-guide` 的分級表寫著高風險「必須獨立驗收
# (驗收者 ≠ 實作者)」,`ci-cd` 更把它寫成 gate 5「身分檢查」——而本檔原本只驗
# ACC **有沒有「驗收者」這個欄位**,值是誰從來沒有人比對過;repo 附的 CI 樣板也只有
# 兩步,那道 gate 5 一次都沒有跑過。**一道宣告了而不存在的閘,比沒有那道閘更貴。**
#
# 這條規則刻意**沒有跳過通道**:圖與盤點是給人看的材料,人有權決定不看;
# 獨立性是結構性事實,放棄它等於放棄獨立驗收本身。要放寬的正確通道是把風險評成
# 中/低——而風險降級另有把關(高風險清單命中不接受自評降級)。
AUTOPILOT_VER_RE = re.compile(r"ai-sdlc-autopilot\s*v(\d+)\.(\d+)", re.IGNORECASE)
IDENTITY_SINCE = (1, 65)
IMPLEMENTER_RE = re.compile(r"^\s*[-*]?\s*(?:實作者|Implemented by)\s*[:：](?P<v>[^\n]*)",
                            re.MULTILINE | re.IGNORECASE)
VERIFIER_RE = re.compile(r"^\s*[-*]?\s*(?:驗收者|Verifier)\s*[:：](?P<v>[^\n]*)",
                         re.MULTILINE | re.IGNORECASE)
# 欄位值到此為止:`|`(lite 的單行式分隔)、`←`(模板註記)、括號(本 repo 的慣例是
# 把 session/降級說明寫在括號裡,那是**註解不是身分**)。
IDENTITY_CUT_RE = re.compile(r"[|｜（(←]")
# 自己承認不獨立的寫法。`Verifier: self` 與實作者字面不同,卻正是這道閘要抓的東西。
SELF_VERIFIER_TOKENS = {"self", "自驗", "自驗收", "同上", "same", "implementer", "同一人"}


def normalise_identity(value: str) -> str:
    """把身分欄正規化到「同一個 agent 的兩種寫法會相等」的程度。

    去粗體/反引號、截掉括號註記與模板箭頭、收斂空白、轉小寫。**刻意不做模糊比對**:
    這道閘要判的是「同一個 id 被填了兩次」,而不是猜兩個名字是不是同一個人。
    """
    v = IDENTITY_CUT_RE.split(value, 1)[0]
    v = re.sub(r"[*`_~]", "", v)
    v = re.sub(r"\s+", " ", v)
    return v.strip(" \t—–-:：·。,,").lower()


def check_verifier_identity(repo: Path, docs: Path | None = None) -> list[str]:
    """高風險 CHG 的「實作者」與其 ACC 的「驗收者」必須不同(v1.65 起前瞻強制)。

    比不出來一律**擋下**(fail-closed):獨立性是這道閘唯一的產物,
    而「查無」在這裡不能等於「沒問題」——那正是本 repo 最貴的一種綠燈。
    找不到對應 ACC 則不在本閘的職責內,由 `check_chg_acc` 的懸空驗收擋。
    """
    # `docs` 選填:不給就沿用 `repo/docs`。**簽章保持向後相容**——
    # 這些函式被 MCP server 與 hooks 以模組方式匯入,api-contract 閘會擋
    # 「必填參數消失 / 新增必填參數」,而它擋得對(CHG-20260804-17)。
    docs = docs if docs is not None else repo / "docs"
    ch_dir = docs / "changes"
    acc_dir = docs / "acceptance"
    if not ch_dir.is_dir() or not acc_dir.is_dir():
        return []
    accs = [(a, a.read_text(encoding="utf-8", errors="ignore"))
            for a in sorted(acc_dir.glob("ACC-*.md"))]
    problems: list[str] = []
    for chg in sorted(ch_dir.glob("CHG-*.md")):
        text = chg.read_text(encoding="utf-8", errors="ignore")
        if classify_status(text) != "implemented_or_accepted":
            continue
        if not HIGH_RISK_RE.search(text):
            continue
        m = AUTOPILOT_VER_RE.search(text)
        if not m or (int(m.group(1)), int(m.group(2))) < IDENTITY_SINCE:
            continue  # 新規則只往後適用(見 doc-integrity):既有記錄一張都不追殺
        cid = CHG_RE.search(chg.stem) or CHG_RE.search(text)
        chg_id = cid.group(0) if cid else chg.stem
        mine = [(a, t) for a, t in accs if chg_id.lower() in t.lower()]
        if not mine:
            continue  # 懸空驗收由 check_chg_acc 擋,不在這裡重複報
        im = IMPLEMENTER_RE.search(text)
        implementer = normalise_identity(im.group("v")) if im else ""
        for acc, atext in mine:
            vm = VERIFIER_RE.search(atext)
            verifier = normalise_identity(vm.group("v")) if vm else ""
            if not implementer or not verifier:
                problems.append(
                    f"{chg.name} 為高風險,但與 {acc.name} 的身分**比不出**"
                    f"(實作者={implementer or '空'} / 驗收者={verifier or '空'})— "
                    "查無不等於沒問題,兩欄都要填得出可比對的 id(見 independent-acceptance)")
                continue
            if verifier in SELF_VERIFIER_TOKENS or verifier == implementer:
                problems.append(
                    f"{chg.name} 為高風險,但 {acc.name} 的驗收者與實作者是同一個"
                    f"({verifier})— 球員不可兼裁判,高風險必須獨立驗收。"
                    "若確為不同執行個體,請在欄位寫出可區分的 agent id"
                    "(見 agent-hierarchy 的編號慣例),不要只寫模型/廠商名")
    return problems


def check_fields(repo: Path, require_branch: bool, require_commit: bool,
                 docs: Path | None = None) -> list[str]:
    # `docs` 選填:不給就沿用 `repo/docs`。**簽章保持向後相容**——
    # 這些函式被 MCP server 與 hooks 以模組方式匯入,api-contract 閘會擋
    # 「必填參數消失 / 新增必填參數」,而它擋得對(CHG-20260804-17)。
    docs = docs if docs is not None else repo / "docs"
    problems = []

    def lint(files, required, kind):
        for f in files:
            text = f.read_text(encoding="utf-8", errors="ignore")
            missing = [name for name, pat in required.items() if not pat.search(text)]
            if require_branch and not BRANCH_FIELD.search(text):
                missing.append("Branch")
            if require_commit and not COMMIT_FIELD.search(text):
                missing.append("Commit/PR")
            if missing:
                problems.append(f"{f.name}({kind})缺必填欄:{', '.join(missing)}")

    ch_dir = docs / "changes"
    ac_dir = docs / "acceptance"
    if ch_dir.is_dir():
        lint(sorted(ch_dir.glob("CHG-*.md")), CHG_REQUIRED_FIELDS, "CHG")
    if ac_dir.is_dir():
        lint(sorted(ac_dir.glob("ACC-*.md")), ACC_REQUIRED_FIELDS, "ACC")
    return problems


def check_secrets(repo: Path) -> list[str]:
    docs = repo / "docs"
    if not docs.is_dir():
        return []
    problems = []
    for f in sorted(docs.rglob("*.md")):
        text = f.read_text(encoding="utf-8", errors="ignore")
        for name, pat in SECRET_PATTERNS:
            m = pat.search(text)
            if m:
                shown = m.group(0)[:12] + "…"
                problems.append(f"{f.relative_to(repo)} 疑似含 secret({name}:{shown})— 文件不可含 secrets,改以名稱/位置引用")
                break  # 一檔報一次即可
    return problems


def check_regression_pointers(repo: Path, docs: Path | None = None) -> list[str]:
    # `docs` 選填:不給就沿用 `repo/docs`。**簽章保持向後相容**——
    # 這些函式被 MCP server 與 hooks 以模組方式匯入,api-contract 閘會擋
    # 「必填參數消失 / 新增必填參數」,而它擋得對(CHG-20260804-17)。
    docs = docs if docs is not None else repo / "docs"
    """迴歸集腐爛檢查:regression.md 反引號內的檔案指向必須存在(被刪的測試=靜默作廢的承諾)。"""
    reg = docs / "acceptance" / "regression.md"
    if not reg.is_file():
        return []
    problems = []
    for token in re.findall(r"`([^`\n]+)`", reg.read_text(encoding="utf-8", errors="ignore")):
        cand = token.split("::")[0].split()[0].strip()  # 去掉 pytest ::節點與參數
        if cand.startswith(("http://", "https://")):
            continue
        if "/" not in cand and "." not in cand:
            continue  # 純指令名(如 `make`)不驗
        if not (repo / cand).exists():
            problems.append(f"docs/acceptance/regression.md 指向的 `{cand}` 不存在 — 迴歸承諾已腐爛(補檔或更新指向)")
    return problems


KN_TIERS = {"shallow", "deep", "user-confirmed"}
KN_STATUS = {"observing", "active", "retired"}
KN_KNOWN_FIELDS = {"id", "tier", "rule", "tags", "keywords", "status", "branch", "date",
                   "evidence", "counters", "source_quote", "reason", "note", "history"}


UNCOVERED_RE = re.compile(r"^#{2,3}\s*.*(未涵蓋|Uncovered|not covered)", re.MULTILINE | re.IGNORECASE)
BACKLOG_REF_RE = re.compile(r"#\d+|已登記|待補項|backlog")
# 前瞻起點:既有 ACC 沒有這個慣例,立即生效會整片變紅(沿用既有做法)。
REGISTRY_SINCE = "20260807"


def check_uncovered_registered(repo: Path, docs: Path | None = None) -> list[str]:
    """ACC 的「未涵蓋」節必須連得到登記簿(CHG-20260806-14)。

    同一個形狀在三筆之內出現三次:CHG-20260806-10 補登記、-13 又補登記、
    而 -14 動筆時還有兩項只寫在 ACC 裡。**前兩次的處置都是「這次記得登記」**——
    那是靠記性,而 KN-005 已實證三次:靠記性維持的規則會重複違反。

    lint 該問的是「這句話連得到別處嗎」,不是「你寫得對不對」——所以只要求
    該節出現登記簿條目編號(`#N`)或明寫已登記,不比對內容(措辭會變,比對必然脆弱)。

    **空講一句「未涵蓋」而不留追蹤點,與沒講是同一件事。**
    """
    docs = docs if docs is not None else repo / "docs"
    problems = []
    for accs in sorted(docs.glob("*/acceptance")) or [docs / "acceptance"]:
        if not accs.is_dir():
            continue
        for f in sorted(accs.glob("ACC-*.md")):
            date = f.stem.split("-")[1] if len(f.stem.split("-")) > 1 else ""
            if date < REGISTRY_SINCE:
                continue          # 前瞻適用:不追殺存量
            text = f.read_text(encoding="utf-8", errors="replace")
            m = UNCOVERED_RE.search(text)
            if m is None:
                continue
            seg = text[m.start():]
            nxt = re.search(r"^#{2,3}\s", seg[3:], re.MULTILINE)
            if nxt:
                seg = seg[:nxt.start() + 3]
            if not BACKLOG_REF_RE.search(seg):
                rel = f.relative_to(repo) if f.is_relative_to(repo) else f
                problems.append(
                    f"{rel} 的「未涵蓋」節沒有連到登記簿 — 請寫出登記簿條目編號"
                    f"(如 `#13`)或明寫已登記。**空講一句未涵蓋而不留追蹤點,"
                    f"與沒講是同一件事**(這一族問題已在三筆之內出現三次)")
    return problems


def check_directive_shape(repo: Path, docs: Path | None = None) -> list[str]:
    r"""md 模式的 directive 必須寫成 `## DIR-<序號>`(CHG-20260806-07)。

    觸發證據:CHG-20260806-05 把使用者的常設授權記成 `## KN-007` + `tier:**directive**`。
    `governance_health` 用 `^##\s*DIR-` 數 directive、用 `tier\s*[::]\s*shallow` 數 shallow
    ——兩個都不命中,於是那條 directive **既不算 directive 也不算 shallow**,回報 0。

    不只是統計難看:進場握手要載入的「現行 directive」靠的就是這個形狀。
    **一條找不到的 directive,和沒有這條 directive 是同一件事。**

    順帶擋掉加粗體:`tier:**directive**` 會讓所有 `tier: directive` 的比對落空——
    又一次「字串比對分不出意圖」,而這次是寫的人自己讓比對失效的。
    """
    docs = docs if docs is not None else repo / "docs"
    problems = []
    for kn_path in sorted(docs.glob("*/knowledge")) or [docs / "knowledge"]:
        if not kn_path.is_dir():
            continue
        for f in sorted(kn_path.rglob("*.md")):
            if "archive" in f.parts:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            rel = f.relative_to(repo) if f.is_relative_to(repo) else f
            # 每個 `## <標題>` 區塊各自判:tier 宣告為 directive 的,標題必須是 DIR-
            blocks = re.split(r"^(##\s+.*)$", text, flags=re.MULTILINE)
            for i in range(1, len(blocks) - 1, 2):
                head, body = blocks[i], blocks[i + 1]
                # 只取 tier 的**值**:前導的 markdown 記號 + 英數字。
                # 第一版寫成 `(\S+)`,而中文沒有空格——它把後面整段括號說明都吃進去,
                # 於是合法的「tier:directive(說明…)」被誤報。
                # **我剛寫的 lint 犯了它要抓的那一族錯**:比對範圍沒有界定清楚。
                m = re.search(r"^-\s*tier\s*[::]\s*(?P<mark>\**)(?P<v>[A-Za-z-]+)",
                              body, re.MULTILINE)
                if m is None or m.group("v").lower() != "directive":
                    continue
                if m.group("mark"):
                    problems.append(
                        f"{rel} 「{head.strip()}」的 tier 加了粗體記號——"
                        f"`tier:**directive**` 會讓 `tier: directive` 的比對落空,請寫成純 `directive`")
                if not re.match(r"^##\s+DIR-", head):
                    problems.append(
                        f"{rel} 「{head.strip()}」宣告 tier=directive 卻不是 `## DIR-<序號>` 標題——"
                        f"governance_health 與進場握手都靠這個形狀找 directive,"
                        f"找不到的 directive 等同不存在(見 knowledge 的 directive 格式)")
    return problems


def check_knowledge_entries(repo: Path, docs: Path | None = None) -> list[str]:
    # `docs` 選填:不給就沿用 `repo/docs`。**簽章保持向後相容**——
    # 這些函式被 MCP server 與 hooks 以模組方式匯入,api-contract 閘會擋
    # 「必填參數消失 / 新增必填參數」,而它擋得對(CHG-20260804-17)。
    docs = docs if docs is not None else repo / "docs"
    """JSON 條目 fail-loud 驗證:解析不了/缺必填/enum 錯/id≠檔名/未知欄位(打錯欄名=資料靜默消失)→ 擋。"""
    entries = docs / "knowledge" / "entries"
    if not entries.is_dir():
        return []
    problems = []
    vocab = None
    vocab_file = docs / "knowledge" / "vocabulary.json"
    if vocab_file.is_file():
        # `except json.JSONDecodeError` 擋不住**讀檔階段**的失敗:
        # 非合法 UTF-8 的檔案會在 `read_text` 就炸掉,而那是這份檔案唯一
        # 同時打中兩個讀取者的形狀(CHG-20260808-01 的 R2)。
        # `utf-8-sig`:BOM 在 Windows 上極常見,舊版會讓它變成解析失敗。
        try:
            vocab = json.loads(vocab_file.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError, UnicodeDecodeError) as e:
            problems.append(f"docs/knowledge/vocabulary.json 讀取或解析失敗(fail-loud):"
                            f"{type(e).__name__}:{e}")
    for f in sorted(entries.glob("*.json")):
        rel = f"docs/knowledge/entries/{f.name}"
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            problems.append(f"{rel} JSON 解析失敗(fail-loud,不跳過):{e}")
            continue
        missing = [k for k in ("id", "tier", "rule", "tags", "status") if k not in d]
        if missing:
            problems.append(f"{rel} 缺必填欄:{', '.join(missing)}(schema:assets/knowledge_entry.schema.json)")
            continue
        if d["id"] != f.stem:
            problems.append(f"{rel} id「{d['id']}」≠ 檔名「{f.stem}」— 檔名即 id")
        if d["tier"] not in KN_TIERS:
            problems.append(f"{rel} tier「{d['tier']}」不在 {sorted(KN_TIERS)}")
        if d["status"] not in KN_STATUS:
            problems.append(f"{rel} status「{d['status']}」不在 {sorted(KN_STATUS)}")
        if not isinstance(d["tags"], list) or not d["tags"]:
            problems.append(f"{rel} tags 須為非空陣列(小寫英文檢索鍵)")
        elif isinstance(vocab, dict):
            unregistered = [t for t in d["tags"] if t not in vocab or str(t).startswith("_")]
            if unregistered:
                problems.append(f"{rel} tags {unregistered} 未註冊於 vocabulary.json — 先登記再用(擋 tag 增殖)")
        if "keywords" in d and (not isinstance(d["keywords"], list)
                                or not all(isinstance(x, str) for x in d["keywords"])):
            problems.append(f"{rel} keywords 須為字串陣列(自由語言命中詞)")
        unknown = set(d) - KN_KNOWN_FIELDS
        if unknown:
            problems.append(f"{rel} 未知欄位 {sorted(unknown)} — 打錯欄名=資料靜默消失(schema 為準)")
    return problems


STUB_FILES = ["CLAUDE.md", "GEMINI.md", ".cursorrules", ".windsurfrules",
              ".github/copilot-instructions.md"]


# stub 的判準(CHG-20260806-15,待補項 #15)。
#
# 前一版量的是**總非空行數 ≤ 15**,而那是代理指標:它把標題、指標、以及檔案自己的
# 內容混在一起。一份寫得仔細的 stub(標題 + 指標 + 兩句說明為什麼)可能有 6 行而完全
# 合規;一份 6 行的規則清單則不合規——**總行數分不出這兩者**。
#
# 要量的是扣掉標題與指標之後**還剩多少**,那才是「承載正典以外的內容」的直接測量。
#
# 門檻從哪來:實測本機兩個真實 stub 的自有內容行數分別是 **0 與 1**
# (`desktop-shell/CLAUDE.md`、本 repo 的 `CLAUDE.md`)。取 3 留三倍餘裕。
# **document 那一側沒有樣本可蒐集**——在受治理的 repo 裡規則本來就禁止它存在,
# 「等它出現再訂門檻」等的是一件不該發生的事,所以門檻從 stub 那一側訂。
# **何時該重新評估**:蒐集到更多真實 stub(尤其自有內容 ≥ 2 的)時。
STUB_MAX_OWN_LINES = 3
# 這兩個訊號**不需要任何門檻**——它們抓的是「文件的形狀」,而不是「多長」。
STUB_LIST_ITEMS_MAX = 2   # 條列 ≥ 3 項 = 規則清單


def check_entry_point(repo: Path) -> list[str]:
    """進入點在 root、適用任何 AI:治理專案必有 AGENTS.md;工具專屬檔只准當指向它的 stub。

    「受治理」的判定走 `ledger_roots`,不寫死 `docs/changes/`——否則分目錄帳本的 repo
    永遠判為未治理,這條規則對它們**從未被執行過**(CHG-20260804-17)。
    """
    problems = []
    governed = any((d / "changes").is_dir() for d in ledger_roots(repo))
    agents = repo / "AGENTS.md"
    if governed and not agents.is_file():
        problems.append("治理專案缺 root 進入點 AGENTS.md — 進入點要在 root、讓任何 AI 最快識別(見 SKILL 入口錨點)")
    if agents.is_file():
        for s in STUB_FILES:
            f = repo / s
            if not f.is_file():
                continue
            src = f.read_text(encoding="utf-8", errors="ignore")
            lines = [x for x in src.splitlines() if x.strip()]
            if "AGENTS.md" not in src:
                problems.append(f"{s} 存在但未指向 AGENTS.md — 工具檔只放兩行 stub,內容不得分岔")
                continue
            # 待補項 #3:只驗「出現過 AGENTS.md 這個字串」擋不住
            # **塞滿自己的內容、末尾順帶提一句**——而入口錨點那條規則就是靠這裡執行的。
            # 分得出兩者的是**長度**與**位置**:stub 是指標,不是文件。
            # 門檻刻意寬鬆:擋的是「塞滿」,不是「多寫了兩句」。
            head = "\n".join(lines[:5])
            if "AGENTS.md" not in head:
                problems.append(
                    f"{s} 的 AGENTS.md 指標不在前 5 個非空行內 — 那是**順帶提一句**,"
                    f"不是指標。工具專屬檔只准當指向正典的指標(見 SKILL 入口錨點)")
                continue
            # 自有內容 = 非空、非標題、且不含 AGENTS.md 的行。扣掉標題與指標之後
            # 還剩多少,才是「承載正典以外的內容」——不是總長度那個代理指標。
            own = [x for x in lines
                   if not x.lstrip().startswith("#") and "AGENTS.md" not in x]
            sections = [x for x in lines if x.startswith("## ")]
            bullets = [x for x in own if re.match(r"^\s*([-*+]|\d+[.)])\s", x)]
            why = None
            if sections:
                why = f"有 {len(sections)} 個 `##` 章節 — 那是文件的形狀,不是指標"
            elif len(bullets) > STUB_LIST_ITEMS_MAX:
                why = f"有 {len(bullets)} 項條列 — 那是規則清單,不是指標"
            elif len(own) > STUB_MAX_OWN_LINES:
                why = (f"扣掉標題與指標後仍有 {len(own)} 行自有內容"
                       f"(上限 {STUB_MAX_OWN_LINES},量自真實 stub 的 0 與 1)")
            if why:
                problems.append(
                    f"{s} 不是 stub 而是另一份文件:{why} — "
                    f"工具專屬檔只准當**指向 AGENTS.md 的指標**;"
                    f"承載自己的內容必然與正典分岔(見 SKILL 入口錨點)")
    return problems


# 容許雙語括號式 `重複性檢查(Recurrence check):`(CHG-20260804-17)。
# 本 repo 的 7 份 CHG 全是這個寫法,原模式一份都認不出來——打開閘之後的第一批訊號
# 有七成是模式過窄造成的誤報,而不是帳本真的缺欄。
RECURRENCE_RE = re.compile(r"(重複性檢查|Recurrence check)\s*(\([^)]*\)|（[^）]*）)?\s*[::]",
                           re.IGNORECASE)
SKILL_VER_RE = re.compile(r"Skill\s*[::]\s*ai-sdlc\s*v(\d+)\.(\d+)", re.IGNORECASE)
RECURRENCE_SINCE = (1, 17)


def check_knowledge_bootstrap(repo: Path, docs: Path | None = None) -> list[str]:
    """知識庫先建(v1.16)+存量補建(v1.17):受治理 repo 必有 docs/knowledge/——容器不存在,自主記錄永遠不會發生。"""
    # `docs` 選填:不給就沿用 `repo/docs`。**簽章保持向後相容**——
    # 這些函式被 MCP server 與 hooks 以模組方式匯入,api-contract 閘會擋
    # 「必填參數消失 / 新增必填參數」,而它擋得對(CHG-20260804-17)。
    docs = docs if docs is not None else repo / "docs"
    if (docs / "changes").is_dir() and not (docs / "knowledge").is_dir():
        return ["治理專案缺 docs/knowledge/ — 知識庫要先建(空 INDEX 也是合法知識庫):"
                "建 knowledge.md(INDEX)+ vocabulary.json(見 knowledge「先建」;存量專案進場補建見 handshake)"]
    return []


def check_recurrence_field(repo: Path, docs: Path | None = None) -> list[str]:
    # `docs` 選填:不給就沿用 `repo/docs`。**簽章保持向後相容**——
    # 這些函式被 MCP server 與 hooks 以模組方式匯入,api-contract 閘會擋
    # 「必填參數消失 / 新增必填參數」,而它擋得對(CHG-20260804-17)。
    docs = docs if docs is not None else repo / "docs"
    """收尾「重複性檢查」欄(v1.17 起前瞻強制):散文步驟無欄位對應=實測次次被略過(見 modification-guide 第 7 步)。"""
    ch_dir = docs / "changes"
    if not ch_dir.is_dir():
        return []
    problems = []
    for chg in sorted(ch_dir.glob("CHG-*.md")):
        text = chg.read_text(encoding="utf-8", errors="ignore")
        m = SKILL_VER_RE.search(text)
        if not m or (int(m.group(1)), int(m.group(2))) < RECURRENCE_SINCE:
            continue  # 新規則只往後適用(見 doc-integrity):舊版記錄與缺 Skill 欄者豁免
        if not RECURRENCE_RE.search(text):
            problems.append(f"{chg.name} 依 v1.17+ 寫成但缺「重複性檢查/Recurrence check」欄 — "
                            "收尾必比對動機是否重複並記結果,「無重複」也要寫(見 modification-guide 第 7 步)")
    return problems


def check_knowledge_index(repo: Path, docs: Path | None = None) -> list[str]:
    # `docs` 選填:不給就沿用 `repo/docs`。**簽章保持向後相容**——
    # 這些函式被 MCP server 與 hooks 以模組方式匯入,api-contract 閘會擋
    # 「必填參數消失 / 新增必填參數」,而它擋得對(CHG-20260804-17)。
    docs = docs if docs is not None else repo / "docs"
    """拆檔模式的輕量交叉檢查:條目檔 id ↔ INDEX.md 雙向存在(完整比對交給 knowledge_index.py --check)。"""
    entries = docs / "knowledge" / "entries"
    if not entries.is_dir():
        return []
    index = docs / "knowledge" / "INDEX.md"
    if not index.is_file():
        return ["docs/knowledge/entries/ 存在但無 INDEX.md — 拆檔模式的 INDEX 是生成物:跑 scripts/knowledge_index.py"]
    idx_text = index.read_text(encoding="utf-8", errors="ignore")
    problems = []
    file_ids = {f.stem for f in entries.glob("*.md")} | {f.stem for f in entries.glob("*.json")}
    for fid in sorted(file_ids):
        if fid not in idx_text:
            problems.append(f"knowledge 條目 {fid} 不在 INDEX.md — INDEX 過期,重跑 knowledge_index.py")
    for iid in re.findall(r"\|\s*((?:KN|DIR)-[\w.]+)\s*\|", idx_text):
        if iid not in file_ids:
            problems.append(f"INDEX.md 列了 {iid} 但 entries/ 無此檔 — INDEX 過期或條目被移走未重生")
    return problems



COVERAGE_ID_RE = re.compile(r"^\|\s*(?:~~)?([ABCD]-\d+)", re.MULTILINE)
COVERAGE_CLOSED_RE = re.compile(r"^#+\s*([^\n]*?)收尾", re.MULTILINE)


def check_coverage_registry(repo: Path) -> list[str]:
    """未涵蓋登記簿不得自相矛盾(CHG-20260804-06;CHG-20260804-09 修誤報)。

    這份檔案的用途是讓「測試全綠」不被讀成「全部都驗過了」。
    那它自己就不能說謊——而它踩過兩種說謊:

      · **重複的 ID**:同一個編號指兩件事,讀的人不知道哪個才算
      · **宣告收尾卻還列著**:某節標題寫「C-23 收尾」,別處卻仍有一列 C-23 未涵蓋

    第二種特別隱蔽:兩邊都是真話,但合起來是假的。

    **收尾小節裡的表不算未涵蓋項**——那是「已收了什麼」的摘要,不是待辦。
    第一版沒有分開這兩者,結果第一次有人在收尾小節裡放摘要表就誤報了
    (而那個人是這道檢查的作者)。寬鬆的比對把摘要讀成待辦,是 KN-003 的形狀。
    """
    problems = []
    for path in sorted(repo.glob("docs/*/acceptance/verification-coverage.md")):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(repo).as_posix()

        # 依 `##` 標題切節:標題含「收尾」的那一節,其表格是已收摘要,不是待辦
        open_ids, closed = [], set()
        heading, in_closed = "", False
        for line in text.splitlines():
            if line.startswith("#"):
                heading = line
                in_closed = "收尾" in heading
                if in_closed:
                    closed |= set(re.findall(r"[ABCD]-\d+", heading))
                continue
            m = re.match(r"\|\s*(?:~~)?([ABCD]-\d+)", line)
            if m and not in_closed:
                open_ids.append(m.group(1))

        dup = sorted({i for i in open_ids if open_ids.count(i) > 1})
        if dup:
            problems.append(f"{rel}:登記簿有重複的 ID {', '.join(dup)}"
                            f"——同一個編號指兩件事,讀的人不知道哪個才算")
        still = sorted(closed & set(open_ids))
        if still:
            problems.append(f"{rel}:{', '.join(still)} 已於某節標題宣告收尾,"
                            f"卻仍以未涵蓋項列著——兩邊都是真話,合起來是假的")
    return problems


def check_commits(repo: Path, since: str, runner=None) -> list[str]:
    r"""掃描 `since..HEAD` 的 commit 是否都引用 CHG 編號。

    `runner` 是注入點(CHG-20260809-02,待補項 #24),形狀比照同檔的 `check_baseline`
    ——**出貨路徑用的就是這一個**,測試專用的注入等於沒測到出貨的那條路。

    為什麼需要它:這支讀的是 `git log` 的輸出,**沒有檔案可以注入**,
    於是它是 11 支 `check_*` 裡唯一進不了 fuzz 射程的一支。而另外兩條路都更差:
    真的建 repo 把變異文字塞進 commit message,等於把 fuzz 產物送進 subprocess
    (CHG-20260808-04 的 Global Constraint 排除了這一類);抽成純函式則會把
    「解析」與「取得下一段輸入」切成兩個半截,而它們在這裡是交錯的。
    """
    run = runner if runner is not None else (lambda *a: git(repo, *a))
    if not (repo / ".git").exists():
        return [f"--commits-since 需要 git repo(未偵測到 .git)— 無 git 模式下 commit 錨定不適用(見 handshake 降級模式)"]
    try:
        out = run("log", "--pretty=%h\t%s", f"{since}..HEAD")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        hint = ";偵測到 shallow clone,請 `git fetch --unshallow` 或在完整 clone 執行" \
            if (repo / ".git" / "shallow").exists() else ""
        return [f"無法讀取 {since}..HEAD 的 commits(錨點存在嗎?{hint}):{e}"]
    problems = []
    for line in out.splitlines():
        if not line.strip():
            continue
        h, _, subject = line.partition("\t")
        try:
            body = run("log", "-1", "--pretty=%B", h)
        except (subprocess.CalledProcessError, FileNotFoundError):
            body = subject
        if not CHG_RE.search(body):
            problems.append(f"commit {h}「{subject[:60]}」未引用任何 CHG/XCHG 編號 — 未治理工作(見 commit 粒度)")
    return problems


GUIDELINE_NAME = "ai-guideline.md"
BASELINE_FIELD = "Governance-baseline"
ADOPT_FIELD = "Adopt-commit"
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# 導入 commit 只准動治理檔。清單是**資料**——新增一種治理產物是編輯這裡。
# 判準用「路徑的第一段」而不是副檔名:治理產物有 .md 也有 .json,
# 而別人的 .md 一樣多得很;能區分的是它住在哪一本帳裡,不是它叫什麼。
# 導入 commit 的白名單。**`acceptance` 刻意不在裡面**(CHG-20260816-02,待補項 #40)。
#
# adopt 的立約是「**不追認既有缺陷**」,而治理基準點第 6 條是「已盤點不等於已驗收」。
# 把 `acceptance` 放進白名單,等於允許一個導入 commit 合法夾帶 ACC 檔
# ——**「追認」就這樣藉導入之名進來了,而那正是 adopt 說好不做的那件事**。
#
# 這一格是第 6 條唯一機器判得動的形式:驗收要不要做、做得對不對,事後驗不了;
# 但「導入這一筆裡有沒有 ACC」是位元組上的事實。
GOVERNANCE_DIRS = ("changes", "structure", "knowledge", "worklog")
GOVERNANCE_FILES = (GUIDELINE_NAME, "CHANGELOG.md", ".archived")


# 表頭宣告的**行文法**,寫成編譯好的 regex:`[bullet] Field[:：] value`。
# 逐段的意思:可有可無的項目符號 → 欄位名(大小寫不拘)→ 半形或全形冒號 → 其餘即值。
# `fullmatch` 錨住整行,所以 `Governance-baseline-foo:` 這種**前綴不完整**的行
# 不會命中,而散文裡順口提到欄位名的那一行也不會(它前面還有字)。
#
# **這個 regex 是該行文法的正式實作,不是為了讓掃描器看見而放的標記。**
# 由來要寫清楚:改寫前的版本用 `startswith` 逐段切字串,語義相同,但
# property-fuzz 的清冊掃描器認的是「有沒有用 regex/ast」,所以那一版**整支
# 對棘輪隱形**——它是真正解析人手寫文字的地方,卻沒有任何東西替它把關。
# 裁決席位(codex,DIR-2 本 session 授予)判:改寫成語義自然的最小 regex,
# 而不是放寬掃描器判準(`splitlines`/`startswith` 到處都是,過度命中的代價更高)。
# **若日後這個文法變得不適合用 regex 表達,正確的做法是給掃描器一個顯式的
# 「這是解析器」宣告入口,而不是在這裡塞一個哨兵 regex。**
BASELINE_DECL_RE = re.compile(
    r"\s*[-*]*\s*(?P<field>" + BASELINE_FIELD + "|" + ADOPT_FIELD + r")\s*[:：](?P<value>.*)",
    re.IGNORECASE)


def parse_baseline_decl(text: str) -> tuple[str | None, str | None]:
    """從 Guideline 表頭讀出 (基準 SHA, 導入 commit SHA);沒宣告回 (None, None)。

    只回**字面值**,不判斷格式對不對——格式的判定連同理由一起留在
    `check_governance_baseline()`,因為「abc1234 不是完整 SHA」這句話要能被
    回報出去,而一個回 None 的解析器把「沒宣告」與「宣告錯了」壓成同一種結果
    (KN-003:兩者要走的後續動作完全不同,一個是不適用、一個是擋下)。

    **同名欄位以第一次出現為準**;宣告了但值是空白會回 `""`(不是 `None`)——
    「空白」與「沒宣告」必須分得開,前者會走到 40-hex 判定被擋下,
    而空白豁免比漏寫更糟(KN-006)。
    """
    got: dict[str, str] = {}
    for line in text.splitlines():
        m = BASELINE_DECL_RE.fullmatch(line.rstrip())
        if not m:
            continue
        field = (BASELINE_FIELD if m.group("field").lower() == BASELINE_FIELD.lower()
                 else ADOPT_FIELD)
        if field not in got:
            got[field] = m.group("value").strip()
    return got.get(BASELINE_FIELD), got.get(ADOPT_FIELD)


def _ever_declared(repo: Path, rel: str, run) -> bool:
    """這份 Guideline 的歷史裡,`Adopt-commit:` 出現過嗎?

    這一條擋的是**第四條偽造路徑**:前面每一項都靠「宣告了什麼」去驗,
    而把宣告刪掉之後,這道閘會回報「不適用」——**而不適用看起來跟通過一樣**
    (KN-001 的鏡像)。`git log -S` 找的是「這個字串的出現次數變過的 commit」,
    所以宣告過再刪掉會留下痕跡,從未宣告則什麼都找不到。
    """
    try:
        out = run("log", "-S", f"{ADOPT_FIELD}:", "--oneline", "--", rel)
    except (subprocess.CalledProcessError, FileNotFoundError):
        # 查不到歷史時不得倒向「沒宣告過」——那正好是被刪掉的那一格。
        # 但也不能倒向「宣告過」而誤殺乾淨的 repo,所以交給呼叫端當作
        # 「無法評估」處理(見 check_governance_baseline 的 git 前置檢查)。
        return False
    return bool(out.strip())


def check_governance_baseline(repo: Path, docs: Path | None = None,
                              runner=None, verbose: bool = False) -> list[str]:
    r"""治理基準點:文件宣告的錨,在 git 裡成不成立(CHG-20260811-02,待補項 30)。

    `onboarding` 的六條最低限度裡的**第 2 條**:另建一個導入 commit,並記下它的
    parent SHA。先做這一條而不是「禁止替基準前補造 CHG」,採的是獨立審查席位的論證:

    > 它建立可由 Git 客觀驗證的治理時間邊界;沒有它,後續「不得補造舊 CHG」
    > 「第一筆變更在導入之後」「基準前後差集」都沒有可信錨點。

    **先有錨,才談得上相對於錨的規則。**

    三態,而且「不適用」不是通過:沒宣告的 repo(絕大多數——它們不是導入來的)
    回空清單,但 `verbose=True` 時會具名說出「不適用」。把它混進通過的訊息裡,
    等於讓一個從沒被檢查的東西看起來像被檢查過了。

    `runner` 是注入點,形狀比照同檔的 `check_baseline` / `check_commits`
    ——**出貨路徑用的就是這一個**。
    """
    docs = docs if docs is not None else repo / "docs"
    guideline = docs / GUIDELINE_NAME
    rel = guideline.relative_to(repo).as_posix() if guideline.is_relative_to(repo) \
        else str(guideline)
    text = ""
    if guideline.is_file():
        try:
            text = guideline.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return [f"{rel} 讀不到({e})— 讀不到不等於沒問題"]
    baseline, adopt = parse_baseline_decl(text)

    is_git = (repo / ".git").exists()
    run = runner if runner is not None else (lambda *a: git(repo, *a))

    if baseline is None and adopt is None:
        # 從未宣告 → 不適用。但要先確認**真的**從未宣告過,而不是被刪掉了。
        if is_git and _ever_declared(repo, rel, run):
            return [f"{rel} 的歷史裡**曾經宣告**過 {ADOPT_FIELD}:,現在卻不見了 — "
                    f"宣告不准悄悄消失(刪掉之後這道閘會回報「不適用」,"
                    f"而不適用看起來跟通過一樣)"]
        return [f"⚪ 治理基準點:**不適用** — {rel} 未宣告 {BASELINE_FIELD} / "
                f"{ADOPT_FIELD},這個 repo 不是導入來的。"
                f"**這不是「通過」**,是沒有東西可檢查"] if verbose else []

    if baseline is None or adopt is None:
        missing = BASELINE_FIELD if baseline is None else ADOPT_FIELD
        return [f"{rel} 只宣告了其中一個欄位(缺 {missing})— "
                f"**兩個欄位要一起**才有意義:只有基準等於沒有錨,"
                f"只有導入 commit 等於沒有起點"]

    bad = [f"{f}={v!r}" for f, v in ((BASELINE_FIELD, baseline), (ADOPT_FIELD, adopt))
           if not FULL_SHA_RE.match(v)]
    if bad:
        return [f"{rel} 的宣告不是**完整 SHA**(40 位十六進位):{', '.join(bad)} — "
                f"不是日期、不是 tag(tag 會移動)、不是簡寫(簡寫會撞)"]

    if not is_git:
        # 查不到錨點與錨點不成立,後果同向(KN-004)。
        return [f"治理基準點需要 git 才驗得了(未偵測到 .git),而 {rel} 宣告了基準 — "
                f"查不到不等於沒問題"]

    try:
        parents = run("rev-list", "--parents", "-n", "1", adopt).split()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return [f"{rel} 宣告的 {ADOPT_FIELD}={adopt[:12]}… 在這個 repo 裡**找不到** — "
                f"宣告了一個不存在的 commit"]
    if not parents:
        return [f"讀不到 {adopt[:12]}… 的 parent(git 回了空)— 查不到不等於沒問題"]

    got_parents = parents[1:]
    if len(got_parents) != 1:
        why = "root commit,沒有「導入前的狀態」" if not got_parents \
            else f"merge commit({len(got_parents)} 個 parent),邊界會糊掉"
        return [f"{ADOPT_FIELD}={adopt[:12]}… 的 **parent 不是剛好一個**:{why}"]

    if got_parents[0] != baseline:
        return [f"**parent 對不上**:{ADOPT_FIELD}={adopt[:12]}… 的 parent 是 "
                f"{got_parents[0][:12]}…,而宣告的 {BASELINE_FIELD} 是 "
                f"{baseline[:12]}… — 錨點對不上,則所有「相對於基準」的規則全部懸空"]

    try:
        changed = run("show", "--name-only", "--pretty=format:", adopt).splitlines()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        return [f"讀不到 {adopt[:12]}… 動了哪些檔({e})— 查不到不等於沒問題"]
    stray = [c.strip() for c in changed if c.strip() and not _is_governance_path(c.strip())]
    if stray:
        return [f"導入 commit **夾帶**了非治理檔({len(stray)} 個):"
                f"{', '.join(sorted(stray)[:8])} — 導入這一筆只准放治理文件,"
                f"否則實質變更會藉導入之名逃過整條變更流程"]
    # 規則 4 與規則 5:帳本與受治理變更都必須在導入**之後**(#30 殘留)。
    return _check_after_adopt(repo, baseline, adopt, run)


def _check_after_adopt(repo: Path, baseline: str, adopt: str, run) -> list[str]:
    """治理基準點**規則 4 與規則 5**(CHG-20260816-02,#30 殘留)。

    這兩條七份 ACC 都寫著「殘留」而沒有進展,而 ACC-20260811-02 自己就寫了
    「與 `check_commits` 同形,共用得上」——**不需要 manifest,讀既有兩個欄位 + git 就夠**。
    #41 要的 schema 化清冊因此不是這兩條的前置。

    · **規則 4**:任何 CHG 檔的**首次入庫**都必須在導入 commit 之後。
      在導入之前就有 CHG,代表帳本是**回填**的——而回填的帳本描述的是
      「導入前就存在的東西」,那正是 adopt 立約說不追認的。
    · **規則 5**:第一筆**受治理變更**(commit 訊息帶 CHG 編號)必須在導入之後。
      在導入之前就宣稱受治理,那個治理沒有基準可對。

    兩條都只在**本 repo 自己有宣告**時才跑得動;沒宣告時回空,由既有的
    「未宣告 = 不適用」那條路承接(**不是通過**)。
    """
    problems: list[str] = []
    try:
        # 導入 commit 自己的時間戳,當作「之後」的判準基準。
        adopt_ts = run("show", "-s", "--format=%ct", adopt).strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        return [f"讀不到導入 commit 的時間({e})— 查不到不等於沒問題"]

    # 規則 4:CHG 檔首次入庫的時間。`--diff-filter=A` 只看新增,
    # `--reverse` 讓最早的那一筆排在最前。
    try:
        added = run("log", "--reverse", "--diff-filter=A", "--format=%ct %H",
                    "--", "docs/*/changes/CHG-*.md").splitlines()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        return [f"讀不到 CHG 檔的入庫歷史({e})— 查不到不等於沒問題"]
    for line in added[:1]:                      # 只看最早的一筆
        ts, sha = (line.split(None, 1) + [""])[:2]
        if ts.isdigit() and int(ts) < int(adopt_ts):
            problems.append(
                f"**規則 4**:最早的 CHG 檔在 {sha[:12]}… 就入庫了,而它**早於導入 commit** "
                f"{adopt[:12]}… — 帳本是回填的,而回填的帳本描述的是導入前就存在的東西")

    # 規則 5:第一筆帶 CHG 編號的 commit。
    try:
        governed = run("log", "--reverse", "--format=%ct %H %s",
                       "--grep", r"CHG-[0-9]\{8\}-[0-9]\{2\}", "-E").splitlines()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        return problems + [f"讀不到受治理 commit 的歷史({e})— 查不到不等於沒問題"]
    for line in governed[:1]:
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and int(parts[0]) < int(adopt_ts):
            problems.append(
                f"**規則 5**:第一筆受治理變更 {parts[1][:12]}… **早於導入 commit** "
                f"{adopt[:12]}… — 那筆治理沒有基準可對")
    return problems


def _is_governance_path(rel: str) -> bool:
    """這個路徑屬於治理帳本嗎?

    認的是**路徑裡有沒有帳本的形狀**,不是副檔名——治理產物有 .md 也有 .json,
    而別人的 .md 一樣多。`docs/x/changes/CHG-1.md` 與 `sdlc_docs/knowledge/…`
    都算,`src/app.py` 不算。落點名不寫死(見 `DOC_ROOT_NAMES` 的同一個理由)。
    """
    parts = Path(rel).parts
    if not parts:
        return False
    if parts[0] not in DOC_ROOT_NAMES:
        return False
    return (any(p in GOVERNANCE_DIRS for p in parts)
            or parts[-1] in GOVERNANCE_FILES)


def check_baseline(repo: Path, runner=None) -> tuple[int, list[str]]:
    r"""對一次遠端基準(CHG-20260806-12,待補項 #7)。回傳 (exit_code, 訊息)。

    握手第 1 步原本只跑 `git status`——而 **`git status` 只看得到本機**。
    2026-08-04 實測:本機乾淨、`--commits-since` 全過,而基準在開工時就已過期
    (遠端早已推進兩筆)。三種代價全部真的發生:撞號、白工、文件數字過期。

    **本地乾淨不等於基準最新。**

    不誤殺是硬條件(KN-004:代價往哪邊倒):非 git、無 upstream、fetch 失敗
    一律 **exit 0** 並明講「這不是落後」——只有**明確落後**才回非零。
    在 CI 或無網路環境誤擋一次的代價,遠高於漏報一次。
    """
    def _default(*a):
        r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        return r.returncode, r.stdout.strip()

    # 注入點(CHG-20260806-14,待補項 #13):**出貨路徑用的就是這一個**。
    # 測試專用的注入等於沒測到出貨的那條路。
    git = runner or _default

    if git("rev-parse", "--git-dir")[0] != 0:
        return 0, ["⚪ 基準檢查**降級**:這不是 git repo——**不是落後**。"
                   "commit 錨定與掃描均不適用,請在 ack 中聲明降級模式(見 handshake 第 1 步)"]
    if git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")[0] != 0:
        return 0, ["⚪ 基準檢查**降級**:目前分支沒有 upstream——**不是落後**。"
                   "設定後再對(`git branch -u origin/<分支>`),或在 ack 中聲明"]
    if git("fetch", "--quiet")[0] != 0:
        return 0, ["⚪ 基準檢查**降級**:`git fetch` 失敗(離線?)——**不是落後**。"
                   "有網路時請重跑,並在 ack 中聲明本次未對到基準"]
    behind = [x for x in git("rev-list", "--oneline", "HEAD..@{u}")[1].splitlines() if x]
    ahead = [x for x in git("rev-list", "--oneline", "@{u}..HEAD")[1].splitlines() if x]
    if behind:
        return 1, [f"❌ 落後 upstream {len(behind)} 筆——**先對齊再開工**。"
                   "在過期的樹上開工的代價是撞號、白工、文件數字過期(待補項 #7 的原始證據)",
                   *[f"    · {c}" for c in behind[:5]],
                   "    修正:`git pull --rebase`(或 `git merge origin/<分支>`)後重跑本檢查"]
    return 0, [f"✅ 基準最新(超前 {len(ahead)} 筆、落後 0 筆)"]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--staged", action="store_true", help="檢查 git staged 變更的結構漂移")
    ap.add_argument("--structural", nargs="*", default=DEFAULT_STRUCTURAL)
    ap.add_argument("--commits-since", metavar="REF", help="掃描 REF..HEAD 的 commit 是否都引用 CHG 編號")
    ap.add_argument("--check-baseline", action="store_true",
                    help="對一次遠端基準(握手第 1 步):fetch 後比對落後/超前。"
                         "非 git / 無 upstream / fetch 失敗一律降級 exit 0——只有明確落後才非零")
    ap.add_argument("--require-branch", action="store_true", help="欄位 lint 額外強制 Branch 欄(多分支專案)")
    ap.add_argument("--require-commit", action="store_true", help="欄位 lint 額外強制 Commit/PR 欄")
    ap.add_argument("--no-field-lint", action="store_true")
    ap.add_argument("--no-secret-scan", action="store_true")
    args = ap.parse_args(argv[1:])
    repo = Path(args.repo).resolve()

    problems: list[str] = []
    if args.check_baseline:
        # 刻意**獨立於其他檢查**:它問的是「你在對的樹上嗎」,
        # 而那要在讀任何 docs 之前先知道(handshake 第 1 步)。
        code, msgs = check_baseline(repo)
        for m in msgs:
            print(m)
        if code != 0:
            return code
    if args.staged:
        changed = git_staged_files(repo)
        problems += check_structural_sync(changed, args.structural)
    # 沒有帳本時,「通過」是一句假話(CHG-20260805-04)。兩種情況要分開,
    # 因為 KN-004:「查不到」該 fail-open 還是 fail-closed,取決於代價往哪邊倒。
    #
    #   · 連 docs/ 都沒有 → **不適用**。非治理專案被誤擋的代價低(別跑這支就好),
    #     所以 exit 0——但訊息不得含任何肯定式的通過措辭。
    #   · 有 docs/ 卻沒有任何 changes/ → **問題**。那是治理 repo 掉了帳本的形狀,
    #     回綠的代價高:這道 lint 存在的理由就是抓這種事。
    has_ledger = any((d / "changes").is_dir() for d in ledger_roots(repo))
    if not has_ledger:
        existing = doc_roots(repo)
        if existing:
            names = " / ".join(p.name for p in existing)
            problems.append(
                f"有 {names} 卻找不到任何 changes/ — 治理專案的帳本不見了?"
                f"(檢查 <root>/changes/ 或 <root>/<ledger>/changes/,"
                f"root ∈ {'/'.join(DOC_ROOT_NAMES)};"
                f"若這不是治理專案,不要對它跑這支 lint)")
        else:
            print(f"⚪ {repo} 底下沒有帳本"
                  f"(找過 {', '.join(f'{n}/changes/' for n in DOC_ROOT_NAMES)}"
                  f" 與各自的 <ledger>/changes/)"
                  f" — **不適用**,不是「沒問題」:這裡沒有東西可檢查。")
            return 1 if problems else 0

    # 逐帳本跑(單帳本專案 roots 只有一個,行為與從前相同)。
    # 多帳本時在每條訊息前掛帳本名——否則「CHG-… 缺 ACC」看不出是哪一本的。
    roots = ledger_roots(repo)
    for docs in roots:
        tag = f"[{docs.name}] " if len(roots) > 1 else ""
        per: list[str] = []
        per += check_chg_acc(repo, docs)
        per += check_verifier_identity(repo, docs)
        if not args.no_field_lint:
            per += check_fields(repo, args.require_branch, args.require_commit, docs)
        per += check_regression_pointers(repo, docs)
        per += check_knowledge_bootstrap(repo, docs)
        per += check_recurrence_field(repo, docs)
        per += check_knowledge_entries(repo, docs)
        per += check_directive_shape(repo, docs)
        per += check_uncovered_registered(repo, docs)
        per += check_knowledge_index(repo, docs)
        per += check_governance_baseline(repo, docs)
        problems += [tag + x for x in per]
    if not args.no_secret_scan:
        problems += check_secrets(repo)
    problems += check_entry_point(repo)
    problems += check_coverage_registry(repo)
    if args.commits_since:
        problems += check_commits(repo, args.commits_since)

    if problems:
        print("❌ doc-integrity 檢查未通過:")
        for p in problems:
            print(f"  - {p}")
        print("\n請補齊(改結構→更新 docs/structure;已實作 CHG→補 ACC;缺欄→補模板欄;"
              "secret→改名稱/位置引用;未治理 commit→補開 CHG)後再提交。")
        return 1
    print("✅ doc-integrity 檢查通過(結構同步 + CHG↔ACC + 欄位 + secrets"
          + (" + commit 治理" if args.commits_since else "") + ")。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
