#!/usr/bin/env python3
"""
review_gate.py — CI 審議閘 / review gate

規則(CHG-20261022-03 T4、T10、T11;安全影響 (b)(c)(i)(n)):
1. 只採信 `author_association == OWNER` 的留言;其他人貼的標記一律忽略。
2. 需要審議 = PR 改到程式路徑(`*.py`、`*.sh`、`static/`、`.github/`、`scripts/`、`.ai-sdlc/`;
   `docs/`、`features/` 以外的位置,以及其中的 `*.py`、`*.sh`,一律從嚴視為程式路徑)
   **或** PR 改到的 CHG 風險分級為中 / 高
   **或** PR 內 commit 訊息引用的 CHG(在 head 的 `docs/changes/` 存在者)風險分級為中 / 高(T10)。
   讀不到分級一律從嚴視為需要審議;CHG 檔路徑上任一層(含 `docs/`、`docs/changes/`)是符號連結,
   或解析後不在 `docs/changes/` 底下,都視為讀不到。同一 CHG 既被改到又被
   commit 引用時取較嚴者(讀不到 > 高 > 中 > 低)。commit 引用但 `docs/changes/` 不存在的 CHG 不列入分級判定
   (該情況由 `plan_check_changed.py` 擋下)。訊息逐項寫出觸發原因;免審議時也如實寫出依據。
3. 需要審議時,必須有擁有者留言含 `<!-- local-review CHG-YYYYMMDD-NN sha=<head> seats=<json> -->`,
   且 sha 等於目前 head;標記中的 CHG 必須是該 PR 改到的 CHG 之一(PR 沒改到任何 CHG 時,
   必須是 PR 內 commit 訊息引用的 CHG 之一);多則符合時取最後一則。同一則留言含兩個以上 local-review 標記視為可疑、忽略。
   判決須含 DIR-1 六席;否決席(risk、security、compliance)任一 `fail`,或任何席位 `no-answer`
   → 失敗。缺少審議留言時,訊息提示執行 `scripts/local_review.py` 的方式(T11)。
4. PR 改到治理基礎設施路徑(`.github/`、`scripts/ci/`、`tests/ci/`、`.ai-sdlc/`、
   `scripts/local_review.py`、`scripts/deploy_local.sh`)時,另需擁有者留言
   `<!-- infra-approved sha=<head> -->`(sha 必須是目前 head)。

本閘只是「審議有做、否決席沒有 fail」的完整性檢查,不是授權;授權只有使用者核准 merge(安全影響 (n))。

結構:純函式 `evaluate()`(測試全打這層,不碰網路)+ CLI 層(`urllib` 呼叫 GitHub REST)。
CLI 的 token 取自環境變數 `GITHUB_TOKEN`;CHG 分級從 `--repo`(PR 資料的 checkout,只當資料)
讀取該 PR 改到的 `docs/changes/CHG-*.md`,以及 commit 訊息引用的 CHG 檔的標頭「風險分級:」那一行。
本腳本不寫入任何檔案。

用法 / usage:
  GITHUB_TOKEN=… python3 scripts/ci/review_gate.py --repo pr --pr <編號> [--github-repo <owner/name>]

退出碼:0 = 通過;1 = 審議閘不通過;2 = 參數錯誤或 GitHub API 失敗。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SEATS = ("risk", "impact", "drift", "compliance", "security", "consistency")
VETO_SEATS = ("risk", "security", "compliance")
VERDICTS = ("pass", "fail", "concern", "no-answer")

EXEMPT_PREFIXES = ("docs/", "features/")
PROGRAM_PREFIXES = ("static/", ".github/", "scripts/", ".ai-sdlc/")
PROGRAM_SUFFIXES = (".py", ".sh")
INFRA_PREFIXES = (".github/", "scripts/ci/", "tests/ci/", ".ai-sdlc/")
INFRA_FILES = ("scripts/local_review.py", "scripts/deploy_local.sh")

CHG_FILE_RE = re.compile(r"^docs/changes/CHG-\d{8}-\d{2}\.md$")
CHG_ID_RE = re.compile(r"CHG-\d{8}-\d{2}")
RISK_LINE_RE = re.compile(r"^-\s*風險分級\s*[::]\s*(低|中|高)")
LOCAL_REVIEW_RE = re.compile(
    r"<!--\s*local-review\s+(CHG-\d{8}-\d{2})\s+sha=([0-9a-f]{40})\s+seats=(\{.*?\})\s*-->",
    re.S)
LOCAL_REVIEW_ANY_RE = re.compile(r"<!--\s*local-review\b")
INFRA_RE = re.compile(r"<!--\s*infra-approved\s+sha=([0-9a-f]{40})\s*-->")


# ---------------------------------------------------------------- 路徑分類

def is_exempt_path(path: str) -> bool:
    """只改這類路徑可免審議:docs/、features/ 底下且不是 *.py、*.sh。"""
    return path.startswith(EXEMPT_PREFIXES) and not path.endswith(PROGRAM_SUFFIXES)


def is_program_path(path: str) -> bool:
    return path.endswith(PROGRAM_SUFFIXES) or path.startswith(PROGRAM_PREFIXES)


def is_infra_path(path: str) -> bool:
    return path.startswith(INFRA_PREFIXES) or path in INFRA_FILES


def read_risk(text: str) -> Optional[str]:
    """只讀 CHG 標頭(第一個 `## ` 之前)的「- 風險分級:」那一行;讀不到回傳 None。"""
    for line in text.splitlines():
        if line.startswith("## "):
            break
        m = RISK_LINE_RE.match(line.strip())
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------- 留言解析

def _is_owner(comment: dict) -> bool:
    return comment.get("author_association") == "OWNER"


def parse_local_review(body: str) -> Optional[dict]:
    """解析單則留言的 local-review 標記;沒有、超過一個或 json 不合格都回傳 None。"""
    if len(LOCAL_REVIEW_ANY_RE.findall(body or "")) != 1:
        return None
    m = LOCAL_REVIEW_RE.search(body)
    if not m:
        return None
    try:
        seats = json.loads(m.group(3))
    except ValueError:
        return None
    if not isinstance(seats, dict):
        return None
    for name, info in seats.items():
        if not isinstance(info, dict) or info.get("verdict") not in VERDICTS:
            return None
    return {"chg": m.group(1), "sha": m.group(2), "seats": seats}


def has_infra_approval(comments: Iterable[dict], head_sha: str) -> Tuple[bool, List[str]]:
    notes = []
    for c in comments:
        for sha in INFRA_RE.findall(c.get("body") or ""):
            if not _is_owner(c):
                notes.append("忽略非擁有者({})的 infra-approved 留言".format(
                    c.get("author_association")))
            elif sha != head_sha:
                notes.append("忽略 infra-approved 留言:sha {} 不是目前 head".format(sha[:12]))
            else:
                return True, notes
    return False, notes


# ---------------------------------------------------------------- 判定(純函式)

def allowed_chgs(files: Iterable[str], commit_chgs: Iterable[str]) -> List[str]:
    """標記可引用的 CHG:PR 改到的 CHG;PR 沒改到任何 CHG 時改用 commit 訊息引用的 CHG。"""
    changed = sorted({f.rsplit("/", 1)[-1][:-3] for f in files if CHG_FILE_RE.match(f)})
    return changed or sorted(set(commit_chgs))


def _chg_path(chg_id: str) -> str:
    return "docs/changes/{}.md".format(chg_id)


_SEVERITY = {"低": 0, "中": 1, "高": 2, None: 3}


def stricter(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """取較嚴的分級:讀不到 > 高 > 中 > 低;不認得的值視同讀不到。"""
    a = a if a in _SEVERITY else None
    b = b if b in _SEVERITY else None
    return a if _SEVERITY[a] >= _SEVERITY[b] else b


def read_chg_risk(repo: Path, rel: str) -> Tuple[bool, Optional[str]]:
    """讀 PR checkout 內 CHG 檔(`docs/changes/<檔名>`)的分級,逐層拒絕符號連結。

    回傳 (檔案是否存在, 分級)。從 repo 根到目標檔的任一路徑元件是符號連結、目標不是一般檔案,
    或 `target.resolve()` 不等於 `repo.resolve() / "docs" / "changes" / <檔名>` → (True, None),
    即記為讀不到分級(從嚴視為需要審議)。沒有符號連結且檔案不存在 → (False, None)。
    """
    if not CHG_FILE_RE.match(rel):
        return True, None
    cur = repo
    for part in Path(rel).parts:
        cur = cur / part
        if cur.is_symlink():
            return True, None
        if not os.path.lexists(cur):
            return False, None
    try:
        if not cur.is_file() or cur.resolve() != repo.resolve() / "docs" / "changes" / cur.name:
            return True, None
        return True, read_risk(cur.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return True, None


def review_command(pr: Optional[int] = None) -> str:
    """缺審議留言時提示的執行方式(T11)。"""
    return ("請執行 `python3 scripts/local_review.py --pr {}`"
            "(暫停 27B 期間加 `--codex-seats risk,security,compliance`)").format(
                pr if pr is not None else "<編號>")


def evaluate(files: Iterable[str], comments: Iterable[dict], head_sha: str,
             chg_risks: Dict[str, Optional[str]],
             commit_chgs: Iterable[str] = (),
             commit_chg_risks: Optional[Dict[str, Optional[str]]] = None,
             pr: Optional[int] = None) -> Tuple[bool, List[str]]:
    """審議閘判定。

    files:PR 改到的路徑(改名者新舊路徑都要列入);
    comments:GitHub issue comments(至少含 `author_association`、`body`),依時間先後排列;
    head_sha:目前 PR head 的完整 sha;
    chg_risks:PR 改到的 CHG 路徑 → 風險分級(「低」「中」「高」或 None = 讀不到);
    commit_chgs:PR 內 commit 訊息引用的 CHG 編號(PR 沒改到 CHG 時用來比對標記);
    commit_chg_risks:commit 引用且在 head 的 `docs/changes/` 存在的 CHG 編號 → 風險分級
        (「低」「中」「高」或 None = 讀不到);不存在的 CHG 不放進來;
    pr:PR 編號,只用於提示訊息。
    回傳 (是否通過, 訊息清單)。
    """
    files = list(files)
    commit_chgs = sorted(set(commit_chgs))
    commit_chg_risks = dict(commit_chg_risks or {})
    comments = list(comments)
    msgs: List[str] = []
    ok = True

    # 1. 治理基礎設施路徑:需擁有者 infra-approved
    infra = sorted(f for f in files if is_infra_path(f))
    if infra:
        approved, notes = has_infra_approval(comments, head_sha)
        msgs.extend(notes)
        if approved:
            msgs.append("治理基礎設施路徑已有擁有者核准(infra-approved,sha 對應目前 head)")
        else:
            ok = False
            msgs.append("需使用者核准:PR 改到治理基礎設施路徑({}),缺少擁有者留言 "
                        "<!-- infra-approved sha={} -->".format(", ".join(infra), head_sha))

    # 2. 是否需要審議
    reasons = []
    program = sorted(f for f in files if not is_exempt_path(f))
    if program:
        reasons.append("改到程式或非 docs/features 路徑:{}".format(", ".join(program[:10])
                                                              + (" …" if len(program) > 10 else "")))
    basis = []  # 免審議時如實列出的依據
    cited_by_path = {_chg_path(c): r for c, r in commit_chg_risks.items()}
    for path, own in sorted(chg_risks.items()):
        # 同一 CHG 也被 commit 引用時取較嚴者(CI 的兩條讀取路徑理應一致;不一致時從嚴)
        risk = stricter(own, cited_by_path[path]) if path in cited_by_path else own
        if risk in ("中", "高"):
            reasons.append("PR 改到 {},其風險分級為{}".format(path, risk))
        elif risk is None:
            reasons.append("PR 改到 {},讀不到其風險分級(從嚴視為需要審議)".format(path))
        else:
            basis.append("PR 改到 {},其風險分級為{}".format(path, risk))
    for chg_id, risk in sorted(commit_chg_risks.items()):
        if _chg_path(chg_id) in chg_risks:  # 已以「PR 改到」判過,不重複
            continue
        if risk in ("中", "高"):
            reasons.append("commit 引用 {},其風險分級為{}".format(chg_id, risk))
        elif risk is None:
            reasons.append("commit 引用 {},讀不到其風險分級(從嚴視為需要審議)".format(chg_id))
        else:
            basis.append("commit 引用 {},其風險分級為{}".format(chg_id, risk))
    missing_chgs = [c for c in commit_chgs if c not in commit_chg_risks]
    for chg_id in missing_chgs:
        basis.append("commit 引用 {},但 docs/changes/ 沒有該檔,不列入分級判定"
                     "(由 plan_check_changed 檢查)".format(chg_id))
    if not reasons:
        if not chg_risks:
            basis.insert(0, "未改到 CHG 檔")
        if not commit_chgs:
            basis.append("commit 未引用 CHG")
        msgs.append("免審議:只改 docs/、features/;" + ";".join(basis))
        return ok, msgs
    for r in reasons:
        msgs.append("需要審議:" + r)

    # 3. 找對應目前 head、CHG 相符的擁有者 local-review 留言(取最後一則)
    allowed = allowed_chgs(files, commit_chgs)
    chosen = None
    for c in comments:
        body = c.get("body") or ""
        if not LOCAL_REVIEW_ANY_RE.search(body):
            continue
        if not _is_owner(c):
            msgs.append("忽略非擁有者({})的 local-review 留言".format(c.get("author_association")))
            continue
        parsed = parse_local_review(body)
        if parsed is None:
            msgs.append("忽略格式不合格的 local-review 留言")
            continue
        if parsed["sha"] != head_sha:
            msgs.append("忽略 local-review 留言:sha {} 不是目前 head".format(parsed["sha"][:12]))
            continue
        if parsed["chg"] not in allowed:
            msgs.append("忽略 local-review 留言:{} 不是本 PR 改到或 commit 引用的 CHG({})".format(
                parsed["chg"], "、".join(allowed) or "無"))
            continue
        chosen = parsed
    if chosen is None:
        msgs.append("缺少擁有者對應目前 head({})的 local-review 審議留言;{}".format(
            head_sha[:12], review_command(pr)))
        return False, msgs

    seats = chosen["seats"]
    msgs.append("採用 {} 的審議留言(sha {})".format(chosen["chg"], head_sha[:12]))
    missing = [s for s in SEATS if s not in seats]
    if missing:
        ok = False
        msgs.append("審議缺少席位:{}".format(", ".join(missing)))
    for name in sorted(seats):
        verdict = seats[name]["verdict"]
        if verdict == "no-answer":
            ok = False
            msgs.append("{} 席沒有回答".format(name))
        elif verdict == "fail" and name in VETO_SEATS:
            ok = False
            msgs.append("否決席 {} 判 fail".format(name))
    if ok:
        msgs.append("審議閘通過(提醒:這只是完整性檢查,merge 仍需使用者核准)")
    return ok, msgs


# ---------------------------------------------------------------- CLI(GitHub REST)

class ApiError(Exception):
    """GitHub API 呼叫失敗。"""


def _get(url: str, token: str):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "podcast-studio-review-gate",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except (urllib.error.URLError, ValueError) as e:
        raise ApiError("GET {} 失敗:{}".format(url, e))


def _get_all(url: str, token: str) -> list:
    out = []
    page = 1
    while True:
        sep = "&" if "?" in url else "?"
        data = _get("{}{}per_page=100&page={}".format(url, sep, page), token)
        if not isinstance(data, list):
            raise ApiError("預期陣列:{}".format(url))
        out.extend(data)
        if len(data) < 100:
            return out
        page += 1


def collect(api: str, slug: str, pr: int, token: str, repo: Path):
    base = "{}/repos/{}".format(api.rstrip("/"), slug)
    info = _get("{}/pulls/{}".format(base, pr), token)
    head_sha = info["head"]["sha"]
    files = []
    for f in _get_all("{}/pulls/{}/files".format(base, pr), token):
        files.append(f["filename"])
        if f.get("previous_filename"):
            files.append(f["previous_filename"])
    comments = _get_all("{}/issues/{}/comments".format(base, pr), token)
    commit_chgs = sorted({m for c in _get_all("{}/pulls/{}/commits".format(base, pr), token)
                          for m in CHG_ID_RE.findall(((c.get("commit") or {}).get("message")) or "")})
    commit_chg_risks: Dict[str, Optional[str]] = {}
    for chg_id in commit_chgs:  # 符號連結不跟隨,記為讀不到
        exists, risk = read_chg_risk(repo, _chg_path(chg_id))
        if exists:
            commit_chg_risks[chg_id] = risk
    chg_risks: Dict[str, Optional[str]] = {}
    for path in sorted(set(files)):
        if not CHG_FILE_RE.match(path):
            continue
        exists, risk = read_chg_risk(repo, path)  # 逐層拒絕符號連結,記為讀不到
        if not exists:  # 刪除或改名前的舊路徑
            continue
        chg_risks[path] = risk
    return files, comments, head_sha, chg_risks, commit_chgs, commit_chg_risks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="CI 審議閘:驗證 PR 的本機審議留言與 infra 核准")
    ap.add_argument("--repo", required=True, type=Path, help="PR head 的 checkout(只當資料讀 CHG 分級)")
    ap.add_argument("--pr", required=True, type=int, help="PR 編號")
    ap.add_argument("--github-repo", default=os.environ.get("GITHUB_REPOSITORY", ""),
                    help="owner/name,預設取環境變數 GITHUB_REPOSITORY")
    args = ap.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("錯誤:缺少環境變數 GITHUB_TOKEN", file=sys.stderr)
        return 2
    if not re.match(r"^[\w.-]+/[\w.-]+$", args.github_repo or ""):
        print("錯誤:--github-repo 必須是 owner/name", file=sys.stderr)
        return 2
    if not args.repo.is_dir():
        print("錯誤:--repo 不是目錄:{}".format(args.repo), file=sys.stderr)
        return 2
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    try:
        files, comments, head_sha, chg_risks, commit_chgs, commit_chg_risks = collect(
            api, args.github_repo, args.pr, token, args.repo)
    except (ApiError, KeyError, TypeError) as e:
        print("錯誤:{}".format(e), file=sys.stderr)
        return 2

    ok, msgs = evaluate(files, comments, head_sha, chg_risks, commit_chgs, commit_chg_risks, args.pr)
    for m in msgs:
        print("- " + m)
    print("review gate:{}".format("通過" if ok else "不通過"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
