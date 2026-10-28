#!/usr/bin/env python3
"""
review_decision.py — 貼出統整席判定(DIR-6)/ post the review decision

CHG-20261028-02:審議席(DIR-1 六席)只提供意見;由派發者派一個獨立 Claude subagent(預設 Opus、effort medium)
擔任統整席,讀完同一 head 的意見留言、CHG 全文與完整程式後寫出判定 JSON
(`outputs/.tmp/review_decision/pr-<編號>-<sha12>.json`)。本腳本驗證該 JSON 後,以擁有者身分把判定貼回 PR,
留言結尾附唯一一個機器可讀標記供 CI 的 `scripts/ci/review_gate.py` 驗證:

  <!-- review-decision CHG-YYYYMMDD-NN sha=<head 40 hex> decision=merge|fix model=<模型> effort=<等級> -->

安全約束(CHG-20261028-02 安全影響 (b)(c)(d)(e)):
- 不呼叫任何模型;只讀判定 JSON,不寫、不刪任何檔案(JSON 由派發者於 merge 後自行清理)。
- 標記的 `model`、`effort` 只取自參數(派發者填入實際模型名,DIR-1 第 4 條),不取自 JSON;以白名單字元驗證。
- 只處理本 repo 分支、且作者為 repo 擁有者的 PR;fork 或非擁有者 PR、不在 main 執行,一律 exit 1。
- 貼出前確認:目前 head 有擁有者的六席意見留言(`local-review` 標記)、JSON 的 `head_sha` 等於目前 head、
  JSON 的 `chg` 與該意見留言相同、JSON 內容合格。
- JSON 內的文字一律經 `local_review.sanitize()` 去除 `<!--`、`-->`、打斷三個以上反引號,放在程式碼區塊內,
  單區塊上限 SEAT_CHAR_LIMIT 字;避免 JSON 內容偽造第二個標記。
- 子程序一律經 `local_review.run_cmd()`(argv list、不經 shell);不 checkout、不執行 PR 內容。

用法 / usage:
  python3 scripts/review_decision.py --pr <編號> --decision-file <路徑> --model <模型> --effort <等級> [--dry-run]
  python3 scripts/review_decision.py --schema

退出碼:0 = 已貼出,或 --dry-run / --schema 已印出;
        1 = 拒絕(fork、非擁有者、不在 main、目前 head 沒有合格的六席意見留言、JSON 的 head_sha 不是目前 head、
            JSON 不合格);
        2 = 參數錯誤或子程序 / API 失敗。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "ci"))
import local_review as lr  # noqa: E402  共用 run_cmd / gh / sanitize / clip
import review_gate  # noqa: E402  共用席位與 local-review 標記解析

MODEL_RE = re.compile(r"[A-Za-z0-9._:-]{1,64}")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
DECISIONS = ("merge", "fix")
SHA_RE = re.compile(r"[0-9a-f]{40}")
DECISION_DIR = "outputs/.tmp/review_decision"

_STR = {"type": "string", "minLength": 1}
DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["chg", "head_sha", "decision", "summary", "opinions", "unanswered", "fixes", "needs_user"],
    "properties": {
        "chg": {"type": "string", "pattern": r"^CHG-\d{8}-\d{2}$"},
        "head_sha": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
        "decision": {"type": "string", "enum": list(DECISIONS)},
        "summary": _STR,
        "opinions": {
            "type": "array", "minItems": len(review_gate.SEATS), "maxItems": len(review_gate.SEATS),
            "description": "六席各一列;advice 須等於意見標記中該席的值;no-answer 的席位由統整席以該席判決問題自行檢視,"
                           "結果寫在 reason",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["seat", "advice", "adopted", "reason"],
                "properties": {
                    "seat": {"type": "string", "enum": list(review_gate.SEATS)},
                    "advice": {"type": "string", "enum": list(review_gate.VERDICTS)},
                    "adopted": {"type": "boolean"},
                    "reason": _STR,
                }}},
        "unanswered": {"type": "array", "items": {"type": "string", "enum": list(review_gate.SEATS)},
                       "description": "必須等於意見標記中 verdict 為 no-answer 的席位"},
        "fixes": {
            "type": "array",
            "description": "decision=merge 時必須為空;decision=fix 時 fixes 與 needs_user 至少一項非空",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["id", "where", "what", "why"],
                "properties": {"id": _STR, "where": _STR, "what": _STR, "why": _STR}}},
        "needs_user": {"type": "array", "items": _STR,
                       "description": "需使用者決定的事項;有任何一項即不得判 merge"},
    },
}


def _nonempty_str(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def validate_decision(obj, head_sha: str, opinion_seats: Dict[str, dict]) -> List[str]:
    """驗證統整席判定 JSON;回傳繁中錯誤清單(空 = 合格)。

    opinion_seats:採用的意見留言標記中的 seats(`{"席位": {"model", "verdict"}}`)。
    `chg` 與意見留言是否相同由呼叫端另行比對。"""
    if not isinstance(obj, dict):
        return ["判定 JSON 必須是物件"]
    errs: List[str] = []
    required = DECISION_SCHEMA["required"]
    missing = [k for k in required if k not in obj]
    if missing:
        errs.append("缺少欄位:{}".format(", ".join(missing)))
    extra = sorted(k for k in obj if k not in required)
    if extra:
        errs.append("不接受的欄位:{}(model、effort 由參數提供,JSON 不收)".format(", ".join(map(str, extra))))
    if "chg" in obj and not (isinstance(obj["chg"], str) and review_gate.CHG_ID_RE.fullmatch(obj["chg"])):
        errs.append("chg 格式必須是 CHG-YYYYMMDD-NN")
    if "head_sha" in obj:
        if not (isinstance(obj["head_sha"], str) and SHA_RE.fullmatch(obj["head_sha"])):
            errs.append("head_sha 必須是 40 碼小寫十六進位")
        elif obj["head_sha"] != head_sha:
            errs.append("head_sha {} 不是目前 head {}".format(obj["head_sha"][:12], head_sha[:12]))
    decision = obj.get("decision")
    if "decision" in obj and decision not in DECISIONS:
        errs.append("decision 必須是 merge 或 fix")
    if "summary" in obj and not _nonempty_str(obj["summary"]):
        errs.append("summary 必須是非空字串")

    opinions = obj.get("opinions")
    if "opinions" in obj:
        if not isinstance(opinions, list):
            errs.append("opinions 必須是陣列")
        else:
            seen: List[str] = []
            for i, op in enumerate(opinions, 1):
                if not isinstance(op, dict):
                    errs.append("opinions 第 {} 列必須是物件".format(i))
                    continue
                keys = {"seat", "advice", "adopted", "reason"}
                if set(op) != keys:
                    errs.append("opinions 第 {} 列欄位必須恰為 seat、advice、adopted、reason".format(i))
                seat = op.get("seat")
                if seat not in review_gate.SEATS:
                    errs.append("opinions 第 {} 列席位 {} 不是 DIR-1 六席之一".format(i, seat))
                    continue
                seen.append(seat)
                expect = (opinion_seats.get(seat) or {}).get("verdict")
                if op.get("advice") not in review_gate.VERDICTS:
                    errs.append("opinions {} 席 advice 必須是 {}".format(seat, "/".join(review_gate.VERDICTS)))
                elif expect is not None and op.get("advice") != expect:
                    errs.append("opinions {} 席 advice {} 與意見標記 {} 不同".format(seat, op.get("advice"), expect))
                if not isinstance(op.get("adopted"), bool):
                    errs.append("opinions {} 席 adopted 必須是 true / false".format(seat))
                if not _nonempty_str(op.get("reason")):
                    errs.append("opinions {} 席 reason 必須是非空字串".format(seat))
            dup = sorted({s for s in seen if seen.count(s) > 1})
            if dup:
                errs.append("opinions 席位重複:{}".format(", ".join(dup)))
            lack = [s for s in review_gate.SEATS if s not in seen]
            if lack:
                errs.append("opinions 缺少席位:{}(必須六席各一列)".format(", ".join(lack)))

    unanswered = obj.get("unanswered")
    if "unanswered" in obj:
        expect_na = sorted(s for s, info in opinion_seats.items() if (info or {}).get("verdict") == "no-answer")
        if not isinstance(unanswered, list) or not all(isinstance(s, str) for s in unanswered):
            errs.append("unanswered 必須是字串陣列")
        elif len(set(unanswered)) != len(unanswered) or sorted(unanswered) != expect_na:
            errs.append("unanswered {} 與意見標記中沒有回答的席位 {} 不同".format(
                sorted(unanswered), expect_na))

    fixes = obj.get("fixes")
    if "fixes" in obj:
        if not isinstance(fixes, list):
            errs.append("fixes 必須是陣列")
        else:
            for i, fx in enumerate(fixes, 1):
                if not isinstance(fx, dict) or set(fx) != {"id", "where", "what", "why"} or \
                        not all(_nonempty_str(fx.get(k)) for k in ("id", "where", "what", "why")):
                    errs.append("fixes 第 {} 項必須恰含非空的 id、where、what、why".format(i))
    needs = obj.get("needs_user")
    if "needs_user" in obj:
        if not isinstance(needs, list) or not all(_nonempty_str(x) for x in needs):
            errs.append("needs_user 必須是非空字串陣列(可為空陣列)")

    if isinstance(fixes, list) and isinstance(needs, list):
        if decision == "merge" and (fixes or needs):
            errs.append("decision=merge 時 fixes 與 needs_user 必須為空")
        if decision == "fix" and not fixes and not needs:
            errs.append("decision=fix 時 fixes 與 needs_user 至少一項非空")
    return errs


def _block(text: str) -> List[str]:
    return ["```text", lr.clip(lr.sanitize(text), lr.SEAT_CHAR_LIMIT), "```"]


def build_decision_comment(chg: str, pr_no: int, head_sha: str, model: str, effort: str, obj: dict) -> str:
    """組判定留言;JSON 來源文字一律經 sanitize() 放在程式碼區塊內,結尾唯一一個 review-decision 標記。
    chg、head_sha、model、effort 須已驗證(格式由呼叫端保證)。"""
    decision = obj["decision"]
    label = "可合併(merge)" if decision == "merge" else "需修正(fix)"
    op_lines = []
    for op in obj.get("opinions") or []:
        op_lines.append("{} | 建議 {} | {} | {}".format(
            op.get("seat", ""), op.get("advice", ""), "採納" if op.get("adopted") else "不採納",
            str(op.get("reason", "")).replace("\n", " ")))
    fix_lines = []
    for fx in obj.get("fixes") or []:
        fix_lines.append("{}. 位置:{}\n   改法:{}\n   理由:{}".format(
            fx.get("id", ""), fx.get("where", ""), fx.get("what", ""), fx.get("why", "")))
    needs = ["{}. {}".format(i, x) for i, x in enumerate(obj.get("needs_user") or [], 1)]
    unanswered = obj.get("unanswered") or []
    parts = [
        "## 統整判定(DIR-6)— {}".format(chg),
        "",
        "- PR:#{},head `{}`".format(pr_no, head_sha),
        "- 判定:**{}**".format(label),
        "- 統整席:`{}`,effort `{}`(由派發者以參數填入,不取自子代理輸出)".format(model, effort),
        "- 由 `scripts/review_decision.py` 驗證判定 JSON 後貼出;派發者不得修改 JSON 內容。",
        "- 限制:統整席是單一模型,且與派發者同屬 Claude 家族,可能共享盲點或被 PR 內容 prompt injection;"
        "判定不能取代使用者在確認閘的判斷。",
        "- 未回答席位:{}".format("、".join(unanswered) if unanswered else "無"),
        "",
        "### 摘要",
    ]
    parts += _block(str(obj.get("summary", "")))
    parts += ["", "### 逐席意見(席位 | 建議等級 | 是否採納 | 理由)"]
    parts += _block("\n".join(op_lines) or "(無)")
    parts += ["", "### 修法清單"]
    parts += _block("\n".join(fix_lines) or "(無)")
    parts += ["", "### 需使用者決定的事項"]
    parts += _block("\n".join(needs) or "(無)")
    parts += ["", "<!-- review-decision {} sha={} decision={} model={} effort={} -->".format(
        chg, head_sha, decision, model, effort)]
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------- 主流程

def _decode_stream(text: str) -> List[dict]:
    """解析 `gh api --paginate` 的輸出:可能是多個 JSON 陣列或多個 JSON 物件相接。"""
    dec = json.JSONDecoder()
    out: List[dict] = []
    i, n = 0, len(text)
    while True:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            return out
        val, i = dec.raw_decode(text, i)
        if isinstance(val, list):
            out.extend(v for v in val if isinstance(v, dict))
        elif isinstance(val, dict):
            out.append(val)


def pick_opinion(comments: List[dict], head_sha: str):
    """回傳 (採用的意見標記或 None, 拒絕原因)。只採擁有者、sha = head、六席都在的最後一則。"""
    at_head = []
    for c in comments:
        if c.get("author_association") != "OWNER":
            continue
        parsed = review_gate.parse_local_review(c.get("body") or "")
        if parsed and parsed["sha"] == head_sha:
            at_head.append(parsed)
    if not at_head:
        return None, "目前 head({})沒有擁有者的意見留言(local-review 標記);請先執行 local_review.py".format(
            head_sha[:12])
    full = [p for p in at_head if all(s in p["seats"] for s in review_gate.SEATS)]
    if not full:
        lack = [s for s in review_gate.SEATS if s not in at_head[-1]["seats"]]
        return None, "目前 head 的意見標記缺少席位:{}(不能以 --seats 除錯執行的留言統整)".format(", ".join(lack))
    return full[-1], ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="貼出統整席判定(DIR-6):驗證統整席子代理寫出的判定 JSON,以 gh pr comment 貼出帶 "
                    "review-decision 標記的留言。不呼叫任何模型、不寫檔;fork 或非擁有者 PR 一律拒絕。")
    ap.add_argument("--pr", type=int, help="PR 編號")
    ap.add_argument("--decision-file", help="統整席判定 JSON(預設落點 {}/pr-<編號>-<sha12>.json)".format(DECISION_DIR))
    ap.add_argument("--model", help="統整席實際模型名(派發者填;字元限 A-Z a-z 0-9 . _ : -,最長 64)")
    ap.add_argument("--effort", help="統整席 effort:{}".format("|".join(EFFORTS)))
    ap.add_argument("--dry-run", action="store_true", help="只印出留言,不貼到 PR")
    ap.add_argument("--schema", action="store_true", help="印出判定 JSON 的 schema 後結束")
    args = ap.parse_args(argv)

    if args.schema:
        print(json.dumps(DECISION_SCHEMA, ensure_ascii=False, indent=2))
        return 0
    if args.pr is None or not args.decision_file or args.model is None or args.effort is None:
        print("錯誤:需要 --pr、--decision-file、--model、--effort", file=sys.stderr)
        return 2
    if not MODEL_RE.fullmatch(args.model):
        print("錯誤:--model 只能含 A-Z a-z 0-9 . _ : -,長度 1–64", file=sys.stderr)
        return 2
    if args.effort not in EFFORTS:
        print("錯誤:--effort 必須是 {}".format("、".join(EFFORTS)), file=sys.stderr)
        return 2

    try:
        branch = lr.run_cmd(["git", "-C", str(REPO_ROOT), "rev-parse", "--abbrev-ref", "HEAD"]).strip()
        if branch != "main":
            print("拒絕:review_decision.py 必須從 main 的工作副本執行(目前分支:{})".format(branch), file=sys.stderr)
            return 1
        repo = lr.gh_json("repos/{owner}/{repo}")
        slug = repo["full_name"]
        owner = repo["owner"]["login"]
        pr = lr.gh_json("repos/{}/pulls/{}".format(slug, args.pr))
        head_repo = (pr.get("head") or {}).get("repo") or {}
        base_repo = (pr.get("base") or {}).get("repo") or {}
        if not head_repo or head_repo.get("full_name") != base_repo.get("full_name") or \
                base_repo.get("full_name") != slug:
            print("拒絕:PR #{} 來自 fork({}),不處理 fork PR。".format(
                args.pr, head_repo.get("full_name", "已刪除的 repo")), file=sys.stderr)
            return 1
        author = (pr.get("user") or {}).get("login")
        if author != owner:
            print("拒絕:PR #{} 作者 {} 不是 repo 擁有者 {},不處理。".format(args.pr, author, owner), file=sys.stderr)
            return 1
        head_sha = pr["head"]["sha"]
        if not SHA_RE.fullmatch(head_sha or ""):
            raise lr.CmdError("head sha 格式不合格")
        raw = lr.gh("api", "--paginate", "repos/{}/issues/{}/comments".format(slug, args.pr))
        try:
            comments = _decode_stream(raw)
        except ValueError as e:
            raise lr.CmdError("留言清單不是 JSON:{}".format(e))
    except (lr.CmdError, KeyError, TypeError) as e:
        print("錯誤:{}".format(e), file=sys.stderr)
        return 2

    try:
        text = Path(args.decision_file).read_text(encoding="utf-8")
    except OSError as e:
        print("錯誤:讀不到判定 JSON:{}".format(e), file=sys.stderr)
        return 2
    try:
        obj = json.loads(text)
    except ValueError as e:
        print("拒絕:判定 JSON 無法解析:{}".format(e), file=sys.stderr)
        return 1

    opinion, why = pick_opinion(comments, head_sha)
    if opinion is None:
        print("拒絕:{}".format(why), file=sys.stderr)
        return 1
    errs = validate_decision(obj, head_sha, opinion["seats"])
    if isinstance(obj, dict) and obj.get("chg") != opinion["chg"]:
        errs.append("判定 JSON 的 chg {} 與意見留言的 {} 不同".format(obj.get("chg"), opinion["chg"]))
    if errs:
        print("拒絕:判定 JSON 不合格(請重派統整席,派發者不得自行修改 JSON):", file=sys.stderr)
        for e in errs:
            print("- " + e, file=sys.stderr)
        return 1

    comment = build_decision_comment(opinion["chg"], args.pr, head_sha, args.model, args.effort, obj)
    if args.dry_run:
        print(comment)
        return 0
    try:
        lr.gh("pr", "comment", str(args.pr), "--repo", slug, "--body-file", "-", input_text=comment)
    except lr.CmdError as e:
        print("錯誤:{}".format(e), file=sys.stderr)
        return 2
    print("已貼出 PR #{} 的統整判定({},{},sha {})".format(args.pr, opinion["chg"], obj["decision"], head_sha[:12]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
