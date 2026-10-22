#!/usr/bin/env python3
"""
governance_health.py — 治理健康度報告 / governance health report(唯讀、不設閘門)

回答「這個 repo 的治理流程本身健不健康」:CHG 狀態分佈、懸空驗收、暫停項、停滯 claim、
緊急追溯與文件同步(漂移)次數、ACC 通過率、迴歸集規模、歸檔量。定期跑(或 CI 非阻斷),
趨勢異常(懸空變多、緊急通道常用、通過率下滑)就是流程問題的訊號;回顧發現寫進 knowledge。

用法:
  python3 governance_health.py --repo .            # 人讀報告
  python3 governance_health.py --repo . --json     # 機器可讀
  python3 governance_health.py --repo . --lease-days 2   # 停滯 claim 判定天數(預設 1)

退出碼:恆為 0(報告非閘門;要擋提交用 doc_integrity_check.py)。
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doc_integrity_check import (  # noqa: E402  帳本位置與狀態的單一真相
    archived_reason, is_archived, ledger_roots)

# 釘住輸出編碼(CHG-20260803-01 T1):不依賴主控台/locale 的 ambient 編碼。
# 非 UTF-8 主控台(如 Windows cp932)印 CJK/emoji 會 UnicodeEncodeError;
# 釘住後同一份程式在任何平台的輸出行為一致。errors="replace" 確保永不因輸出而崩潰。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

CHG_RE = re.compile(r"X?CHG-\d{8}-\d+", re.IGNORECASE)
DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

PAUSED = ["暫停", "paused"]
ACCEPTED = ["已驗收", "accepted"]
IMPLEMENTED = ["已實作", "implemented", "待驗收", "pending acceptance"]
DRAFT = ["草稿", "draft"]
EMERGENCY = ["緊急", "emergency", "追溯", "retroactive"]
DOCSYNC = ["文件同步", "doc sync", "doc-sync"]
IN_PROGRESS = ["進行中", "in progress"]

PASS_HINTS = ["部分通過", "partial pass", "通過", "pass", "未通過", "fail"]  # 順序:長字先判


def read(f: Path) -> str:
    return f.read_text(encoding="utf-8", errors="ignore")


def classify_chg(text: str) -> str:
    low = text.lower()
    # 取「## 狀態 / Status」段之後的內容優先判斷,退回全文
    m = re.search(r"^##\s*(狀態|Status)\b(.*?)(?=^#|\Z)", text, re.MULTILINE | re.DOTALL)
    scope = (m.group(2) if m else text).lower()
    for hints, label in ((PAUSED, "paused"), (ACCEPTED, "accepted"),
                         (IMPLEMENTED, "implemented"), (DRAFT, "draft")):
        if any(h in scope for h in hints):
            return label
    for hints, label in ((PAUSED, "paused"), (ACCEPTED, "accepted"),
                         (IMPLEMENTED, "implemented"), (DRAFT, "draft")):
        if any(h in low for h in hints):
            return label
    return "unknown"


def acc_conclusion(text: str) -> str:
    m = re.search(r"(結論|Conclusion)\s*[::]\s*(.+)", text)
    if not m:
        return "unknown"
    val = m.group(2).strip().lower()
    if "部分" in val or "partial" in val:
        return "partial"
    if "未通過" in val or "fail" in val:
        return "fail"
    if "通過" in val or "pass" in val:
        return "pass"
    return "unknown"


def newest_date(text: str) -> date | None:
    ds = [date(int(y), int(mo), int(d)) for y, mo, d in DATE_RE.findall(text)
          if 2000 <= int(y) <= 2100 and 1 <= int(mo) <= 12 and 1 <= int(d) <= 31]
    return max(ds) if ds else None


# 升級門檻。**引用規範,不自己定**:`references/knowledge.zh-tw.md` 的生命週期表寫
# 「shallow → deep 條件為 `applied ≥ 3` 且無糾正(閾值可調)」。改門檻時兩邊一起改。
PROMOTE_APPLIED = 3
KN_HEAD_RE = re.compile(r"^##\s+((?:KN|DIR)-[\w.]+)", re.MULTILINE)
KN_TIER_RE = re.compile(r"^-\s*tier\s*[::]\s*(\w[\w-]*)", re.MULTILINE)
KN_COUNT_RE = re.compile(r"^-\s*計數\s*[::]\s*(.+)$", re.MULTILINE)


def parse_knowledge_entries(kn_paths, reader) -> list[dict]:
    """把各帳本的 knowledge 條目讀成結構。單檔模式與拆檔模式都要涵蓋。

    讀不到的欄位回 `None` **而不是猜一個預設值**——「沒寫」與「寫了 0」
    的後續處置不同(前者是模板沒填,後者是還沒套用過)。
    """
    out: list[dict] = []
    for kn_path in kn_paths:
        for kf in sorted(kn_path.rglob("*.json")):
            if "archive" in kf.parts or kf.name == "vocabulary.json":
                continue
            try:
                kd = json.loads(reader(kf))
            except (ValueError, OSError):
                continue          # 壞檔由 doc_integrity fail-loud 把關
            if not isinstance(kd, dict) or not kd.get("id"):
                continue
            c = kd.get("counters") or {}
            out.append({"id": kd.get("id"), "tier": kd.get("tier"),
                        "seen": c.get("seen"), "applied": c.get("applied"),
                        "last_applied": c.get("last-applied") or c.get("last_applied"),
                        "where": kf.as_posix()})
        for kf in sorted(kn_path.rglob("*.md")):
            if "archive" in kf.parts or kf.name == "INDEX.md":
                continue
            text = reader(kf)
            for chunk in re.split(r"(?=^##\s+(?:KN|DIR)-)", text, flags=re.MULTILINE):
                m = KN_HEAD_RE.match(chunk)
                if not m:
                    continue
                tier_m = KN_TIER_RE.search(chunk)
                cnt_m = KN_COUNT_RE.search(chunk)
                line = cnt_m.group(1) if cnt_m else ""
                def _num(key: str):
                    mm = re.search(rf"{key}\s+(\d+)", line)
                    return int(mm.group(1)) if mm else None
                out.append({"id": m.group(1),
                            "tier": (tier_m.group(1).lower() if tier_m else None),
                            "seen": _num("seen"), "applied": _num("applied"),
                            "last_applied": (re.search(r"last-applied\s+([\d-]+)", line)
                                             or [None, None])[1]
                            if "last-applied" in line else None,
                            "where": kf.as_posix()})
    return out


def _rel(path: str | None, repo: Path) -> str:
    """路徑一律相對 repo 顯示。絕對路徑在報告裡只會擠掉真正的訊息。"""
    if not path:
        return "?"
    try:
        return Path(path).resolve().relative_to(repo).as_posix()
    except ValueError:
        return path


def ladder_health(entries: list[dict]) -> list[str]:
    """階梯健康。**只驗規範寫明的事**——見 `references/knowledge` 的生命週期表。

    刻意**沒有**「放太久就退場」那一條:規範只定義「被使用者糾正才失效」,
    加一個天數門檻等於在 check 裡發明新規則,而那個數字會是拍的。
    """
    problems = []
    for e in entries:
        if str(e.get("id", "")).startswith("DIR"):
            continue              # directive 不走 KN 的升級梯
        tier, applied = e.get("tier"), e.get("applied")
        if tier == "shallow" and isinstance(applied, int) and applied >= PROMOTE_APPLIED:
            problems.append(
                f"{e['id']} 仍是 shallow,但 applied={applied} 已達升級門檻 "
                f"{PROMOTE_APPLIED} — 依 references/knowledge 應升 deep(或說明為何不升)")
        # 沒有計數就**永遠不會升級**——階梯的動力來源不見了,而它不會抗議。
        missing = [k for k, v in (("applied", e.get("applied")),
                                  ("last-applied", e.get("last_applied"))) if v is None]
        # `seen` 是**建立 shallow record 的觸發計數**(規範:第二次出現就觸發),
        # 升 deep 之後相關的是 `applied`。對 deep 也要求 `seen` 會產生誤報——
        # 實測本 repo 有 4 條 deep 沒寫 seen,而那是慣例不是缺陷。
        if tier == "shallow" and e.get("seen") is None:
            missing.append("seen")
        if missing:
            problems.append(
                f"{e['id']}({e.get('tier') or 'tier 未寫'})缺計數欄:{', '.join(missing)}"
                f" — 沒有計數,階梯永遠動不了")
    return problems


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--lease-days", type=int, default=1, help="claim 超過幾天無更新視為停滯")
    ap.add_argument("--hanging-max", type=int, default=3, help="懸空驗收警戒值(超過→警示)")
    ap.add_argument("--stale-max", type=int, default=0, help="停滯 claim 警戒值(超過→警示)")
    ap.add_argument("--gate", action="store_true", help="超過警戒值時退出碼 1(預設報告不擋)")
    args = ap.parse_args(argv[1:])
    repo = Path(args.repo).resolve()
    today = datetime.now(timezone.utc).date()  # 治理慣例:一律 UTC+0

    r: dict = {"repo": str(repo), "date": today.isoformat(), "timezone": "UTC+0"}

    # --- CHG 狀態分佈 + 懸空 + 緊急/文件同步 ---
    # 帳本可能分成多本(docs/<ledger>/)。原本寫死 docs/changes/,對本 repo 一律回報
    # 「0 筆 CHG、懸空 0」——一份看起來很健康的空報告(CHG-20260804-17)。
    roots = ledger_roots(repo)
    chg_files = sorted(f for d in roots if (d / "changes").is_dir()
                       for f in (d / "changes").glob("CHG-*.md"))
    acc_files = sorted(f for d in roots if (d / "acceptance").is_dir()
                       for f in (d / "acceptance").glob("ACC-*.md"))
    acc_text = "".join(read(a).lower() + "\n" for a in acc_files)

    status_counts = {"draft": 0, "implemented": 0, "paused": 0, "accepted": 0, "unknown": 0}
    hanging, paused_list, emergency_n, docsync_n = [], [], 0, 0
    lite_n, preauth_n = 0, 0
    lite_re = re.compile(r"自驗|self-?verified", re.IGNORECASE)
    lowrisk_re = re.compile(r"(風險分級|Risk)\s*[::]\s*[^\n]{0,40}?(低|low)", re.IGNORECASE)
    preauth_re = re.compile(r"預授權|pre-?auth", re.IGNORECASE)
    if True:
        for chg in chg_files:
            text = read(chg)
            st = classify_chg(text)
            status_counts[st] += 1
            low = text.lower()
            if sum(h in low for h in EMERGENCY) >= 2:
                emergency_n += 1
            if any(h in low for h in DOCSYNC):
                docsync_n += 1
            if lite_re.search(text) and lowrisk_re.search(text):
                lite_n += 1
            if preauth_re.search(text):
                preauth_n += 1
            chg_id = (CHG_RE.search(chg.stem) or CHG_RE.search(text))
            cid = chg_id.group(0) if chg_id else chg.stem
            if st == "implemented" and cid.lower() not in acc_text:
                hanging.append(cid)
            if st == "paused":
                paused_list.append(cid)
    r["chg_status"] = status_counts
    r["hanging_acceptance"] = hanging
    r["paused"] = paused_list
    r["emergency_retroactive_chg"] = emergency_n
    r["doc_sync_chg(drift_signal)"] = docsync_n
    r["lite_chg"] = lite_n
    r["preauth_usage"] = preauth_n

    # --- ACC 結論率 ---
    concl = {"pass": 0, "partial": 0, "fail": 0, "unknown": 0}
    for a in acc_files:
        concl[acc_conclusion(read(a))] += 1
    total_acc = sum(concl.values())
    r["acc_conclusions"] = concl
    r["acc_pass_rate"] = round(concl["pass"] / total_acc, 2) if total_acc else None

    # --- 停滯 claim(coordination.md + coordination/claims/*.md)---
    stale, active = [], 0
    sources = [led / "coordination.md" for led in roots]
    for led in roots:  # 不叫 d:下面的 d 是日期,撞名會讓型別檢查與人都讀錯
        claims_dir = led / "coordination" / "claims"
        if claims_dir.is_dir():
            sources += sorted(claims_dir.glob("*.md"))
    for src in sources:
        if not src.is_file():
            continue
        for line in read(src).splitlines():
            low = line.lower()
            if any(h in low for h in IN_PROGRESS):
                active += 1
                d = newest_date(line) or newest_date(read(src))
                if d and (today - d).days > args.lease_days:
                    stale.append(f"{src.name}: {line.strip()[:70]}")
    r["claims_in_progress"] = active
    r["stale_claims(lease_days=%d)" % args.lease_days] = stale

    # --- knowledge 階梯統計(DIR / deep / shallow)---
    #
    # **只計活帳本**(CHG-20260810-02)。封存帳本裡的條目結構上不可能升也不可能退,
    # 把它算進總數,總數就不再回答「現在有什麼要處理」——而它看起來仍像在回答。
    # 實測本 repo:`shallow 5` 裡有 1 條在 `docs/ai-sdlc/`(唯讀保留),
    # 停在 `applied 1 / last-applied 2026-07-06`,而使用者連五輪把整個階梯讀成停滯。
    # 封存的不會消失,改為**具名**出現在警告(見下)——三態要分得開:
    # 活的 / 封存(不適用)/ 缺計數(未涵蓋)。
    kn_dir_n = kn_deep = kn_shallow = 0
    live_roots = [d for d in roots if not is_archived(d, read)]
    archived_roots = [d for d in roots if is_archived(d, read)]
    kn_paths = [d / "knowledge" for d in live_roots if (d / "knowledge").is_dir()]
    kn_paths_archived = [d / "knowledge" for d in archived_roots
                         if (d / "knowledge").is_dir()]
    for kn_path in kn_paths:
     if True:
        for kf in sorted(kn_path.rglob("*.json")):  # JSON 正典:結構化讀,不 regex
            if "archive" in kf.parts:
                continue
            try:
                kd = json.loads(read(kf))
            except json.JSONDecodeError:
                continue  # 壞檔由 doc_integrity fail-loud 把關,統計端跳過
            tier = kd.get("tier")
            if tier == "user-confirmed" or str(kd.get("id", "")).startswith("DIR"):
                kn_dir_n += 1
            elif tier == "deep":
                kn_deep += 1
            elif tier == "shallow":
                kn_shallow += 1
        for kf in sorted(kn_path.rglob("*.md")):  # 過渡期 md 條目
            if "archive" in kf.parts or kf.name == "INDEX.md":
                continue
            kt = read(kf)
            kn_dir_n += len(re.findall(r"^##\s*DIR-", kt, re.MULTILINE))
            kn_deep += len(re.findall(r"tier\s*[::]\s*deep", kt, re.IGNORECASE))
            kn_shallow += len(re.findall(r"tier\s*[::]\s*shallow", kt, re.IGNORECASE))
    r["knowledge"] = {"directives": kn_dir_n, "deep": kn_deep, "shallow": kn_shallow}

    # --- knowledge 階梯健康(CHG-20260809-01)---
    #
    # 這幾個數字被印了很久,而**後面從來沒有任何判定**。輸出那一行還附了一句
    # 「shallow 長期不升不退=該 review」——那是寫死在格式字串裡的說明,每次都印,
    # 而我(本 skill 的作者)連續四輪把它讀成觸發中的警告,還向使用者報告成
    # 「已連四輪提醒」。**一個沒有判定在後面的數字,遲早會被讀成一個判定。**
    #
    # 這裡只驗**規範已經寫明的事**,不定義新規則:
    #   · `references/knowledge` 的生命週期只有兩種轉移——升級(`applied ≥ 3` 無糾正)
    #     與「被使用者糾正才失效」。**沒有「放太久就退場」這條**,所以這裡也不加。
    #     加它等於在 check 裡發明規範沒有的規則,而門檻會是拍出來的
    #     (CHG-20260806-15 的 STUB_MAX_LINES 已經教過一次)。
    kn_entries = parse_knowledge_entries(kn_paths, read)
    r["knowledge"]["entries"] = kn_entries
    ladder_problems = ladder_health(kn_entries)

    # 封存帳本裡的條目:**不進統計,但必須具名**(CHG-20260810-02)。
    # 這是結構性判準,不是時間性的——CHG-20260809-01 明文拒絕過「放太久就退場」,
    # 因為那個天數會是拍的。「這本帳已封存」不是我挑的閾值,是宣告出來的事實。
    kn_archived = parse_knowledge_entries(kn_paths_archived, read)
    r["knowledge"]["archived_entries"] = kn_archived
    frozen = [e for e in kn_archived
              if e.get("tier") == "shallow" and not str(e.get("id", "")).startswith("DIR")]
    for e in frozen:
        ladder_problems.append(
            f"{e['id']}(shallow,位於**封存**帳本 {_rel(e.get('where'), repo)})升不了也退不了"
            f" — 規範的兩種轉移(applied≥{PROMOTE_APPLIED} 升級 / 使用者糾正失效)"
            f"都需要有人寫那本帳,而它已封存。需具名處置:遷移到活帳本,"
            f"或確認其規則已由活條目涵蓋而就地留存")
    r["knowledge"]["ladder_problems"] = ladder_problems
    # 理由**全文**進 JSON(機器可讀那份不截斷),人讀那份只取第一行——
    # 一份輸出同時服務兩種讀者時,截斷要發生在呈現層,不是資料層。
    r["knowledge"]["archived_ledgers"] = [
        {"root": _rel(d.as_posix(), repo), "reason": archived_reason(d, read)}
        for d in archived_roots]

    # --- 迴歸集規模 + 歸檔量 ---
    # `roots` 可能是**空的**——`ledger_roots()` 自 CHG-20260811-01 起找不到帳本就回空清單,
    # 不再兜底回傳 `[docs]`。舊的兜底一直在替這裡撐著:`regs[0]` 假設至少有一本帳,
    # 而未治理的 repo 一本都沒有。空清單時沒有迴歸集可讀,`reg_items` 就是 0。
    regs = [d / "acceptance" / "regression.md" for d in roots]
    reg = next((f for f in regs if f.is_file()), None)
    reg_items = 0
    if reg is not None:
        rows = [l for l in read(reg).splitlines() if l.strip().startswith(("|", "- "))]
        reg_items = max(0, len([l for l in rows if not re.match(r"^\|[\s\-:|]+\|$", l.strip())]) - 1) \
            if any(l.strip().startswith("|") for l in rows) else len(rows)
    r["regression_items"] = reg_items
    r["archived"] = {
        "changes": sum(len(list((d / "changes" / "archive").glob("*.md")))
                       for d in roots if (d / "changes" / "archive").is_dir()),
        "acceptance": sum(len(list((d / "acceptance" / "archive").glob("*.md")))
                          for d in roots if (d / "acceptance" / "archive").is_dir()),
    }

    # --- 閾值 → 行動(超標=先收尾、停開新需求;見 doc-integrity)---
    warnings = []
    if len(hanging) > args.hanging_max:
        warnings.append(f"懸空驗收 {len(hanging)} > {args.hanging_max}:先收尾再開新需求")
    if len(stale) > args.stale_max:
        warnings.append(f"停滯 claim {len(stale)} > {args.stale_max}:先依租約規則接管/釋放")
    total_chg = sum(status_counts.values())
    if total_chg >= 10 and emergency_n / total_chg > 0.1:
        warnings.append(f"緊急/追溯占比 {emergency_n}/{total_chg} 超過 10%:正常流程太慢,檢討流程本身")
    if total_chg >= 5 and (kn_dir_n + kn_deep + kn_shallow) == 0:
        warnings.append(f"{total_chg} 筆 CHG 但 knowledge 0 筆:收尾「重複性檢查」可能被系統性跳過,"
                        "或知識庫未 bootstrap(見 knowledge「先建」/ handshake 進場補建)")
    # 階梯健康(CHG-20260809-01)。**這兩條平常是沉默的**——
    # 一個健康檢查的正常狀態就是不說話,它說話的時候才有意義。
    warnings += ladder_problems
    r["warnings"] = warnings

    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 1 if (args.gate and warnings) else 0

    print(f"# 治理健康度 — {r['repo']}({r['date']} UTC+0)\n")
    if not chg_files:
        # 一份全是 0 的報告與「健康」長得一樣(CHG-20260805-04)。
        print("⚪ 找不到任何 CHG(docs/changes/ 或 docs/<ledger>/changes/)"
              " — **不適用**,不是「健康」:這裡沒有治理紀錄可看。\n")
    sc = r["chg_status"]
    print(f"CHG 狀態:草稿 {sc['draft']} | 已實作 {sc['implemented']} | 暫停 {sc['paused']}"
          f" | 已驗收 {sc['accepted']} | 無法判讀 {sc['unknown']}")
    print(f"懸空驗收:{len(hanging)}"
          + (f" ← {', '.join(hanging)}(需優先收尾)" if hanging
             else ("(健康)" if chg_files else "(無帳本,不適用)")))
    print(f"暫停中:{len(paused_list)}" + (f" ← {', '.join(paused_list)}" if paused_list else ""))
    print(f"ACC 結論:通過 {concl['pass']} / 部分 {concl['partial']} / 未通過 {concl['fail']}"
          f"(通過率 {r['acc_pass_rate']})")
    print(f"緊急/追溯 CHG:{emergency_n}(常態性偏高=正常流程太慢的訊號)")
    print(f"文件同步 CHG(漂移訊號):{docsync_n}")
    print(f"lite 佔比:{lite_n}/{sum(status_counts.values())};預授權使用:{preauth_n}(異常偏高=白名單/邊界該 review)")
    # 括號內是**怎麼讀這個數字**的說明,與鄰居兩行同一種寫法(條件句)。
    # 舊版寫的是「shallow 長期不升不退=該 review」——那是**斷言句**,讀起來像
    # 「它現在就不升不退」,而它每次都印。真正的判定現在在 warnings 裡
    # (`applied` 達門檻卻沒升、缺計數欄),而那兩條**平常是沉默的**。
    print(f"knowledge 階梯(**活帳本**):DIR {kn_dir_n} / deep {kn_deep} / shallow {kn_shallow}"
          f"(shallow 若久未升 deep,回頭看它是不是根本不重複;真正該動的會列在下方警告)")
    for a in r["knowledge"]["archived_ledgers"]:
        n = len([e for e in kn_archived if not str(e.get("id", "")).startswith("DIR")])
        why = (a["reason"] or "").splitlines()[0] if a["reason"] else ""
        print(f"  · 封存帳本 {a['root']}:{n} 條**不計入上列** — {why}")
        print(f"    封存不等於沒問題;該處理的列在下方警告")
    print(f"進行中 claim:{active};停滯:{len(stale)}")
    for s in stale:
        print(f"  - {s}")
    print(f"迴歸集項目:{reg_items};歸檔:changes {r['archived']['changes']} / acceptance {r['archived']['acceptance']}")
    for w in warnings:
        print(f"⚠️  {w}")
    return 1 if (args.gate and warnings) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
