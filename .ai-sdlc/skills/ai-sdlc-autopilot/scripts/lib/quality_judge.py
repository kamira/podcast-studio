#!/usr/bin/env python3
"""委派驗證的產物判讀:基線、分級、只准往下棘輪(CHG-20260804-01)。

CHG-20260803-06 建了委派層的**位置**(分類表、宣告解析、產物存在性),
但那四類至今一次也沒跑過。讀完實作才知道原因不只是「還沒做」,是**現在做會壞**:

  · `run_gate` 以**退出碼**判生死 → mypy 在本 repo 有 72 個發現 → 這道閘從第一天就紅
  · 改看**產物存在**則等於沒判 → 一份塞滿錯誤的報告照樣「存在且非空」

恆紅與恆綠一樣等於沒有訊號(KN-001)。所以判讀的單位不是「有沒有發現」,
而是**相對基線的差集**:既有的入基線(每條具名理由),新增的一律擋。

基線是止血點,不是免死金牌——它**只准往下**:發現消失時會被提示移除。
指紋不含行號(沿用 CHG-20260803-06 的 allowlist 決策:含行號每次改動都要重填,
而那會逼人把整份清單當噪音略過)。
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

# 有 judge 的種類:這些的退出碼不再定生死,改判產物內容。
DEFAULT_BASELINE = (Path(__file__).resolve().parent.parent.parent
                    / "assets" / "quality_baseline.json")
JUDGED_KINDS = ("typecheck", "sast", "dependency-audit", "coverage")
# 非功能性也有判讀器的種類(CHG-20260804-03)。沿用同一套機制,不另立第二套——
# 否則會重蹈 CHG-20260804-02 的覆轍:只驗「產物存在」等於沒驗
# (一份寫著「12 個 GPL 相依」的報告照樣存在且非空)。
JUDGED_NONFUNCTIONAL = ("license-compliance", "build-reproducibility",
                        "api-contract", "property-fuzz", "performance")
# 效能的門檻抓**倍數**不抓百分比:要抓的是演算法級退步,不是排程雜訊。
# 會無故轉紅的閘,人會在第一時間關掉它——那與沒有閘是同一件事。
PERF_TOLERANCE = 2.0
# 量測雜訊的上界,**獨立於**偵測門檻且更嚴(CHG-20260804-18)。
#
# 舊版的穩定性斷言拿 PERF_TOLERANCE 當自己的界線,那是循環的:它只證明了
# 「雜訊小於偵測門檻」,而偵測門檻本來就該遠大於雜訊,否則它偵測到的可能只是雜訊。
# 用同一個數字當兩者的界線,等於什麼都沒證明。
#
# 這個常數斷言的是「同一份程式碼連量兩次的差距遠小於 PERF_TOLERANCE」——
# 那才是讓 2.0× 這個門檻有意義的前提。它變紅代表**量測方法壞了**,
# 不代表程式碼變慢了,兩者的處置完全不同。
PERF_STABILITY_MAX = 1.25
# 寬鬆授權:不感染散布物。其餘一律指名交人判斷,包含查不到的
# ——查不到不等於沒有(KN-004)。
PERMISSIVE_LICENSES = ("MIT", "BSD", "APACHE", "PSF", "PYTHON SOFTWARE FOUNDATION",
                       "ISC", "ZLIB", "UNLICENSE", "CC0", "PUBLIC DOMAIN")
# bandit 的分級門檻:嚴重度與信心都要夠高才擋。
# 65 個 LOW 全擋會逼人關閉整道閘;全報則等於沒有閘——與 C1 的信心門檻同一個心智模型。
BLOCKING_SEVERITY = ("HIGH", "MEDIUM")
BLOCKING_CONFIDENCE = ("HIGH", "MEDIUM")
# 覆蓋率的容忍帶。存基線時**無條件捨去**到小數兩位(四捨五入會讓基線高於實測值,
# 下一次一模一樣的跑就會被擋——這是實測時差點埋進去的坑),再加這個帶寬吸收量測抖動。
COVERAGE_TOLERANCE = 0.05


def fingerprint(finding: dict) -> str:
    """發現的指紋。**不含行號**——行號會漂移,規則與檔案不會。

    含訊息摘要:同檔同規則的不同問題仍要分得開。
    """
    key = "|".join([
        str(finding.get("kind", "")),
        str(finding.get("rule", "")),
        str(finding.get("file", "")).replace("\\", "/"),
        str(finding.get("message", ""))[:120].strip(),
    ])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def load_baseline(path) -> tuple[dict, str | None]:
    """載入基線。回 (基線, 錯誤訊息)。

    每一條都必須有非空理由——豁免要留下署名,不是靜默略過
    (沿用 static_allowlist 的既有形態)。
    """
    p = Path(path)
    if not p.is_file():
        return {"findings": {}, "coverage": {}, "api_contract": {},
                "performance": {}}, None
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as exc:
        # 讀不到基線就照跑,會把所有既有發現變成新發現、淹沒真發現;
        # 而忽略錯誤又等於接受一個壞掉的設定(沿用 allowlist 損毀的處置)
        return {}, f"基線檔無法解析:{exc}"
    # **合法 JSON 不等於預期形狀**(CHG-20260810-04):`json.loads("[]")` 會成功,
    # 下一行的 `.get()` 才拋 `AttributeError`——那不在上面 except 的射程內。
    # 本輪 fuzz 在三個檔案裡各抓到一次同一族(policy / profile / quality_judge)。
    if not isinstance(data, dict):
        return {}, f"基線檔必須是物件,實得 {type(data).__name__}"
    findings = data.get("findings", {}) or {}
    bad = [fp for fp, item in findings.items()
           if not str((item or {}).get("reason", "")).strip()]
    if bad:
        return {}, ("基線有 {} 條沒有寫理由:{}\n"
                    "  豁免必須署名——沒有理由的豁免與靜默略過等價,"
                    "但看起來像有交代,那是最糟的狀態。").format(len(bad), ", ".join(bad[:5]))
    # api_contract 也要帶回:它與 findings/coverage 一樣是「上一次的已知狀態」,
    # 濾掉的話判讀器會永遠看到「尚無快照」而永遠放行——一道恆綠的閘。
    return {"findings": findings,
            "coverage": data.get("coverage", {}) or {},
            "api_contract": data.get("api_contract", {}) or {},
            "performance": data.get("performance", {}) or {}}, None


# ── 逐工具解析器(全部 stdlib;工具本身只在 CI 的 requirements-dev 內)──────

def parse_mypy(text: str) -> tuple[list, str | None]:
    """mypy `--output=json`:逐行一個 JSON 物件。"""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            return [], f"mypy 產物有無法解析的行:{line[:80]}"
        if (d.get("severity") or "error").lower() != "error":
            continue
        out.append({"kind": "typecheck", "rule": d.get("code") or "?",
                    "file": d.get("file", ""), "message": d.get("message", ""),
                    "severity": "HIGH", "confidence": "HIGH"})
    return out, None


def parse_bandit(text: str) -> tuple[list, str | None]:
    try:
        d = json.loads(text or "{}")
    except ValueError as exc:
        return [], f"bandit 產物不是合法 JSON:{exc}"
    # `json.loads` 成功不代表拿到的是物件——合法 JSON 也可能是 `1` 或 `[]`。
    # 三個判讀器都在這一步崩潰過(CHG-20260808-01,fuzz 加了新種子之後才現形),
    # 而那與 vocabulary.json 的 R1 是**同一個根因**:解析成功後沒驗頂層型別。
    #
    # 兩層的處置刻意不同:**頂層**型別不對 → 整份報告不是我們以為的東西,回錯誤;
    # **單一元素**型別不對 → 那一筆發現壞掉,其餘仍是有效資訊,略過它而不是整份丟掉。
    if not isinstance(d, dict):
        return [], f"bandit 產物頂層應為物件,實得 {type(d).__name__}"
    out = []
    for r in d.get("results", []) or []:
        if not isinstance(r, dict):
            continue
        out.append({"kind": "sast", "rule": r.get("test_id", "?"),
                    "file": r.get("filename", ""), "message": r.get("issue_text", ""),
                    "severity": (r.get("issue_severity") or "").upper(),
                    "confidence": (r.get("issue_confidence") or "").upper()})
    return out, None


def parse_pip_audit(text: str) -> tuple[list, str | None]:
    try:
        d = json.loads(text or "{}")
    except ValueError as exc:
        return [], f"pip-audit 產物不是合法 JSON:{exc}"
    # 這一支本來就接受兩種頂層(物件或陣列),但**第三種**(數字/字串/null)
    # 會在 `d.get` 直接崩潰。同 parse_bandit 的註解:頂層錯 → 回錯誤,元素錯 → 略過。
    if isinstance(d, list):
        deps = d
    elif isinstance(d, dict):
        deps = d.get("dependencies") or []
    else:
        return [], f"pip-audit 產物頂層應為物件或陣列,實得 {type(d).__name__}"
    out = []
    for dep in deps:
        if not isinstance(dep, dict):
            continue
        for v in dep.get("vulns", []) or []:
            if not isinstance(v, dict):
                continue
            out.append({"kind": "dependency-audit", "rule": v.get("id", "?"),
                        "file": f"{dep.get('name', '?')}=={dep.get('version', '?')}",
                        "message": (v.get("description") or "")[:120],
                        # CVE 不是風格問題:預設全擋,入基線需具名理由
                        "severity": "HIGH", "confidence": "HIGH"})
    return out, None


def parse_coverage(text: str) -> tuple[float | None, str | None]:
    try:
        d = json.loads(text or "{}")
    except ValueError as exc:
        return None, f"coverage 產物不是合法 JSON:{exc}"
    if not isinstance(d, dict):
        return None, f"coverage 產物頂層應為物件,實得 {type(d).__name__}"
    totals = d.get("totals")
    if not isinstance(totals, dict):
        return None, "coverage 產物的 totals 不是物件"
    pct = totals.get("percent_covered")
    if pct is None:
        return None, "coverage 產物缺 totals.percent_covered"
    return float(pct), None


def parse_coverage_lines(text: str) -> tuple[tuple[int, int] | None, str | None]:
    """回 ((已覆蓋行數, 可覆蓋行數), None) 或 (None, 錯誤)。

    棘輪改用**整數行數**而不是四捨五入的百分比(CHG-20260811-03,兩席交叉比對後的
    規則 1)。理由是實測出來的:基線 85.92 對實測 85.9202%,餘裕 0.0002 個百分點
    ——那個懸崖**是把連續量壓成兩位數百分比製造出來的**,不是真的風險。
    而處理它的直覺做法(加緩衝)只是把懸崖搬到另一個任意位置。
    比整數對就沒有這個懸崖,也不需要任何緩衝。
    """
    try:
        d = json.loads(text)
        totals = d["totals"]
        covered, stmts = totals["covered_lines"], totals["num_statements"]
    except (ValueError, KeyError, TypeError) as exc:
        return None, f"coverage 產物缺整數行數(covered_lines / num_statements):{exc}"
    if not isinstance(covered, int) or not isinstance(stmts, int) or stmts <= 0:
        return None, f"coverage 行數不是正整數:covered={covered!r} statements={stmts!r}"
    return (covered, stmts), None


PARSERS = {"typecheck": parse_mypy, "sast": parse_bandit,
           "dependency-audit": parse_pip_audit}


def blocking(finding: dict) -> bool:
    """這個發現該不該擋。嚴重度與信心都要夠高——低的列出但不擋。"""
    return (finding.get("severity", "HIGH").upper() in BLOCKING_SEVERITY
            and finding.get("confidence", "HIGH").upper() in BLOCKING_CONFIDENCE)


def judge_findings(kind: str, text: str, baseline: dict) -> tuple[bool, str, dict]:
    """判讀一份發現型產物。回 (是否放行, 訊息, 明細)。"""
    parser = PARSERS.get(kind)
    if parser is None:
        return True, f"{kind}:無對應判讀器,回退為既有處置(僅驗產物存在)", {}
    findings, err = parser(text)
    if err:
        # 解析不出來 → 擋。看不懂的報告不等於沒問題(KN-004)
        return False, f"{kind}:{err}\n  看不懂的報告不等於沒問題,故擋下。", {}

    base = (baseline or {}).get("findings", {}) or {}
    seen, new, waived, ignored = set(), [], [], []
    for f in findings:
        fp = fingerprint(f)
        seen.add(fp)
        if fp in base:
            waived.append((fp, f, base[fp].get("reason", "")))
        elif blocking(f):
            new.append((fp, f))
        else:
            ignored.append((fp, f))
    gone = [fp for fp in base if fp not in seen and base[fp].get("kind", kind) == kind]

    detail = {"total": len(findings), "new": len(new), "waived": len(waived),
              "ignored": len(ignored), "gone": len(gone)}
    parts = [f"{kind}:共 {len(findings)} 個發現"
             f"(基線內 {len(waived)} / 未達門檻 {len(ignored)} / 新增 {len(new)})"]
    if ignored:
        parts.append("  未達門檻(列出但不擋——噪音會逼人把整道閘關掉):")
        parts += [f"    · [{f['severity']}/{f['confidence']}] {f['rule']} "
                  f"{f['file']}:{f['message'][:60]}" for _fp, f in ignored[:8]]
        if len(ignored) > 8:
            parts.append(f"    …另有 {len(ignored) - 8} 項")
    if waived:
        parts.append("  基線內(每條都有署名理由):")
        parts += [f"    · {f['rule']} {f['file']} — {reason}"
                  for _fp, f, reason in waived[:5]]
        if len(waived) > 5:
            parts.append(f"    …另有 {len(waived) - 5} 項")
    if gone:
        # 基線只准往下:修掉的東西不該還留著豁免,否則它會被悄悄加回去
        parts.append(f"  ⚠️ 基線有 {len(gone)} 條在本次產物中**已不存在**——"
                     f"請移除該條(基線只准往下棘輪):{', '.join(gone[:5])}")
    if new:
        parts.append("  ❌ **新增發現**(基線是止血點,不是免死金牌):")
        parts += [f"    · {f['rule']} {f['file']}:{f['message'][:80]}\n"
                  f"      指紋 {fp}(確認為誤報請連同理由寫入基線)" for fp, f in new]
        return False, "\n".join(parts), detail
    return True, "\n".join(parts), detail


def judge_coverage(text: str, baseline: dict) -> tuple[bool, str, dict]:
    """覆蓋率棘輪:只准升不准降。與 B 支的測試棘輪同形。"""
    pct, err = parse_coverage(text)
    if err or pct is None:
        # `or pct is None` 不是防禦性冗贅:parse_coverage 的不變式是「err 與 pct 互斥」,
        # 但那只存在於註解裡,型別上 pct 仍是 float | None。這一條是本 repo 的
        # typecheck 閘在**自己的第一輪**抓到的——把不變式寫進程式碼,不是只寫進腦袋。
        return False, f"coverage:{err or '缺覆蓋率數字'}\n  看不懂的報告不等於沒問題,故擋下。", {}
    cov = (baseline or {}).get("coverage") or {}
    base = float(cov.get("total", 0) or 0)
    detail = {"before": base, "after": pct}

    # --- 規則 1:能比整數就比整數(CHG-20260811-03)---
    #
    # 基線同時記百分比(給人看)與整數行數(給閘用)。有整數就走整數,
    # **完整精度、零容差**;沒有整數(舊基線)才退回百分比,而且**明講降級**
    # ——降級要看得出來,不是靜靜換一種判法(KN-003)。
    b_cov, b_stmt = cov.get("covered"), cov.get("statements")
    got, lerr = parse_coverage_lines(text)
    if isinstance(b_cov, int) and isinstance(b_stmt, int) and b_stmt > 0 and got:
        covered, stmts = got
        detail |= {"covered": covered, "statements": stmts,
                   "base_covered": b_cov, "base_statements": b_stmt}
        # 比**比值**不比裸行數:重構刪掉整批程式時裸行數必降,而那不是退步。
        # 但比值也不是萬能——刪掉未覆蓋的行同樣會把比值推高,所以**分母的變化
        # 一律印出來**,讓「靠刪程式達標」這件事看得見而不是藏在一個百分比裡。
        d_stmt = stmts - b_stmt
        moved = (f";可覆蓋行數 {b_stmt} → {stmts}({d_stmt:+d})" if d_stmt else "")
        if covered * b_stmt < b_cov * stmts:      # covered/stmts < b_cov/b_stmt,整數比較不落精度
            return False, (
                f"coverage:**覆蓋率下降** {b_cov}/{b_stmt} → {covered}/{stmts}"
                f"({base:.2f}% → {pct:.2f}%){moved}。\n"
                "  補了功能沒補測試會被看見——這是棘輪,不是門檻。\n"
                "  正當的下降請連同理由更新基線(需指名授權的 CHG)。"), detail
        if covered * b_stmt > b_cov * stmts:
            return True, (f"coverage:{b_cov}/{b_stmt} → {covered}/{stmts}"
                          f"({base:.2f}% → {pct:.2f}%,上升){moved}。"
                          f"可更新基線把這個高度鎖住。"), detail
        return True, f"coverage:{covered}/{stmts}({pct:.2f}%,與基線同比值){moved}", detail

    # 降級路徑:舊基線沒有整數行數,或產物讀不到行數。
    why = "基線未記整數行數" if not isinstance(b_cov, int) else (lerr or "產物無行數")
    if pct < base - COVERAGE_TOLERANCE:
        return False, (f"coverage:**覆蓋率下降** {base:.1f}% → {pct:.1f}%。\n"
                       "  補了功能沒補測試會被看見——這是棘輪,不是門檻。\n"
                       "  正當的下降請連同理由更新基線(需指名授權的 CHG)。"), detail
    if pct > base:
        return True, (f"coverage:{base:.1f}% → {pct:.1f}%(上升 {pct - base:.1f} 個百分點)。"
                      f"⚪ **以百分比降級判定**({why});可更新基線把這個高度鎖住。"), detail
    return True, f"coverage:{pct:.1f}%(與基線持平)⚪ **以百分比降級判定**({why})", detail


def judge(kind: str, text: str, baseline: dict) -> tuple[bool, str, dict]:
    """判讀入口。"""
    if kind == "coverage":
        return judge_coverage(text, baseline)
    if kind == "license-compliance":
        return judge_license(text, baseline)
    if kind == "build-reproducibility":
        return judge_build_repro(text, baseline)
    if kind == "api-contract":
        return judge_api_contract(text, baseline)
    if kind == "property-fuzz":
        return judge_property_fuzz(text, baseline)
    if kind == "performance":
        return judge_performance(text, baseline)
    return judge_findings(kind, text, baseline)


def judge_license(text: str, baseline: dict) -> tuple[bool, str, dict]:
    """授權合規。**本專案自己沒有 LICENSE 即擋**——查別人卻不查自己是最容易漏的一格。

    相依的 copyleft/未知授權只**指名**不自動擋:只在 CI/開發期使用的相依不會感染
    散布出去的產物。但它必須被指名,由人決定是移除還是具名接受。
    """
    try:
        d = json.loads(text or "{}")
    except ValueError as exc:
        return False, f"license:產物不是合法 JSON:{exc}\n  看不懂的報告不等於沒問題,故擋下。", {}
    # `json.loads` 成功不代表拿到物件——`1` / `[]` / `"x"` 都是合法 JSON,而後續的
    # `.get()` 與 `in` 會當場崩潰。CHG-20260808-01 一次抓到 **7 個**同族崩潰,全在本檔。
    # 位置刻意在 try/except **之外**:放進 except 裡會排在 return 之後,是死碼
    # ——一個永遠不會執行的守衛,和沒有守衛一樣(CHG-20260805-04 記過同一形狀)。
    if not isinstance(d, dict):
        return False, f"license:產物頂層應為物件,實得 {type(d).__name__}——看不懂的報告不等於沒問題。", {}
    project = d.get("project") or {}
    deps = d.get("dependencies") or []
    base = (baseline or {}).get("findings", {}) or {}

    flagged = []
    for dep in deps:
        lic = str(dep.get("license", "")).upper()
        if any(p in lic for p in PERMISSIVE_LICENSES):
            continue
        f = {"kind": "license-compliance", "rule": dep.get("license", "?"),
             "file": f"{dep.get('name')}=={dep.get('version')}", "message": "非寬鬆或未知授權"}
        if fingerprint(f) not in base:
            flagged.append(f)

    detail = {"deps": len(deps), "flagged": len(flagged),
              "license_file": project.get("license_file")}
    parts = [f"license:{len(deps)} 個相依"]
    if not project.get("license_file"):
        parts.append("  ❌ **本專案沒有 LICENSE 檔**(找過:"
                     f"{', '.join(project.get('searched', []))})。\n"
                     "     要靠 marketplace 散布的東西,自己得先有授權可依。")
        return False, "\n".join(parts), detail
    parts.append(f"  · 本專案授權檔:{project['license_file']}")
    if flagged:
        parts.append(f"  ⚠️ {len(flagged)} 個相依為非寬鬆或未知授權——**指名但不自動擋**"
                     "(僅開發期使用不感染散布物;要擋請寫進基線的反面政策):")
        parts += [f"    · {f['file']} — {f['rule']}" for f in flagged[:10]]
        if len(flagged) > 10:
            parts.append(f"    …另有 {len(flagged) - 10} 個")
    else:
        parts.append("  · 相依授權全為寬鬆型")
    return True, "\n".join(parts), detail


def judge_build_repro(text: str, baseline: dict) -> tuple[bool, str, dict]:
    """建置可重現。兩件不同的事都要成立:同步冪等 + 已提交複本 == 重建結果。"""
    try:
        d = json.loads(text or "{}")
    except ValueError as exc:
        return False, f"build-repro:產物不是合法 JSON:{exc}\n  看不懂的報告不等於沒問題。", {}
    # `json.loads` 成功不代表拿到物件——`1` / `[]` / `"x"` 都是合法 JSON,而後續的
    # `.get()` 與 `in` 會當場崩潰。CHG-20260808-01 一次抓到 **7 個**同族崩潰,全在本檔。
    # 位置刻意在 try/except **之外**:放進 except 裡會排在 return 之後,是死碼
    # ——一個永遠不會執行的守衛,和沒有守衛一樣(CHG-20260805-04 記過同一形狀)。
    if not isinstance(d, dict):
        return False, f"build-repro:產物頂層應為物件,實得 {type(d).__name__}——看不懂的報告不等於沒問題。", {}
    if "identical" not in d:
        return False, "build-repro:產物缺 identical 欄位——判不出來就不放行", {}
    detail = {"idempotent": d.get("idempotent"),
              "committed_matches_build": d.get("committed_matches_build")}
    if d.get("identical"):
        return True, ("build-repro:同一份原始碼兩次建置結果一致,"
                      "且已提交的 plugin 複本與重建結果相符"), detail
    parts = ["build-repro:**不可重現**"]
    if not d.get("idempotent"):
        parts.append("  · 同步跑兩次結果不同——建置本身不穩定")
    if not d.get("committed_matches_build"):
        parts.append("  · 已提交的複本與重新建置的結果不符"
                     "——建置產物被手改過,或忘了跑同步")
    files = d.get("differing_files") or []
    if files:
        parts.append("  差異檔:")
        parts += [f"    · {f}" for f in files[:10]]
    return False, "\n".join(parts), detail


def judge_api_contract(text: str, baseline: dict) -> tuple[bool, str, dict]:
    """對外契約:**只擋破壞性變更**,新增一律放行。

    破壞性 = 模組消失、公開函式消失、必填參數被移除或改名、CLI 旗標消失。
    這幾件事會讓已經在用的呼叫端在**執行期**才炸——而那時它已經不在這個 repo 裡了。

    新增不擋:加一個函式、加一個旗標、把必填改成有預設值,都不會弄壞既有呼叫端。
    """
    try:
        cur = json.loads(text or "{}")
    except ValueError as exc:
        return False, f"api-contract:產物不是合法 JSON:{exc}\n  看不懂的報告不等於沒問題。", {}
    # `json.loads` 成功不代表拿到物件——`1` / `[]` / `"x"` 都是合法 JSON,而後續的
    # `.get()` 與 `in` 會當場崩潰。CHG-20260808-01 一次抓到 **7 個**同族崩潰,全在本檔。
    # 位置刻意在 try/except **之外**:放進 except 裡會排在 return 之後,是死碼
    # ——一個永遠不會執行的守衛,和沒有守衛一樣(CHG-20260805-04 記過同一形狀)。
    if not isinstance(cur, dict):
        return False, f"api-contract:產物頂層應為物件,實得 {type(cur).__name__}——看不懂的報告不等於沒問題。", {}
    prev = (baseline or {}).get("api_contract") or {}
    if not prev:
        return True, ("api-contract:尚無已記錄的契約快照——本輪僅建立基準。"
                      "這不是「相容性通過」,下一輪起才有得比。"), {"baseline": False}

    breaks = []
    pm, cm = prev.get("modules", {}), cur.get("modules", {})
    for mod, fns in pm.items():
        if mod not in cm:
            breaks.append(f"模組消失:{mod}")
            continue
        for fn, sig in fns.items():
            now = cm[mod].get(fn)
            if now is None:
                breaks.append(f"公開函式消失:{mod}::{fn}")
                continue
            gone = [p_ for p_ in sig.get("required", []) if p_ not in now.get("params", [])]
            if gone:
                breaks.append(f"必填參數消失:{mod}::{fn}({', '.join(gone)})")
            added_req = [p_ for p_ in now.get("required", [])
                         if p_ not in sig.get("params", [])]
            if added_req:
                breaks.append(f"新增了必填參數:{mod}::{fn}({', '.join(added_req)})"
                              "——既有呼叫端會少傳")
    gone_flags = [f for f in prev.get("cli_flags", []) if f not in cur.get("cli_flags", [])]
    if gone_flags:
        breaks.append(f"CLI 旗標消失:{', '.join(gone_flags)}")

    detail = {"modules": len(cm), "breaks": len(breaks)}
    if breaks:
        return False, ("api-contract:**破壞性變更**——既有呼叫端會在執行期才炸:\n"
                       + "\n".join(f"    · {b}" for b in breaks)
                       + "\n    刻意的破壞請連同版本與遷移說明更新契約快照(需指名授權的 CHG)。"), detail
    added = sum(len(v) for v in cm.values()) - sum(len(v) for v in pm.values())
    return True, (f"api-contract:{len(cm)} 個模組、"
                  f"{sum(len(v) for v in cm.values())} 個公開函式、"
                  f"{len(cur.get('cli_flags', []))} 個旗標;無破壞性變更"
                  + (f"(淨增 {added} 個公開函式)" if added > 0 else "")), detail


def judge_property_fuzz(text: str, baseline: dict) -> tuple[bool, str, dict]:
    """屬性測試:不變式是「解析器不得拋出未捕捉的例外」,任何失敗即擋。"""
    try:
        d = json.loads(text or "{}")
    except ValueError as exc:
        return False, f"property-fuzz:產物不是合法 JSON:{exc}\n  看不懂的報告不等於沒問題。", {}
    # `json.loads` 成功不代表拿到物件——`1` / `[]` / `"x"` 都是合法 JSON,而後續的
    # `.get()` 與 `in` 會當場崩潰。CHG-20260808-01 一次抓到 **7 個**同族崩潰,全在本檔。
    # 位置刻意在 try/except **之外**:放進 except 裡會排在 return 之後,是死碼
    # ——一個永遠不會執行的守衛,和沒有守衛一樣(CHG-20260805-04 記過同一形狀)。
    if not isinstance(d, dict):
        return False, f"property-fuzz:產物頂層應為物件,實得 {type(d).__name__}——看不懂的報告不等於沒問題。", {}
    if "failures" not in d or "cases" not in d:
        return False, "property-fuzz:產物缺 failures/cases 欄位——判不出來就不放行", {}
    fails = d.get("failures") or []
    detail = {"cases": d.get("cases"), "failures": len(fails), "seed": d.get("seed")}
    if not d.get("cases"):
        # 跑了 0 次而回報 0 個失敗,是恆真回報——與沒跑過等價(KN-001)
        return False, "property-fuzz:呼叫次數為 0——0 次失敗不是通過,是沒跑過", detail
    if fails:
        by: dict[str, list] = {}
        for f in fails:
            by.setdefault(f.get("target", "?"), []).append(f)
        parts = [f"property-fuzz:**{len(fails)} 次崩潰**(seed={d.get('seed')},可重現):"]
        for tgt, items in list(by.items())[:6]:
            parts.append(f"    · {tgt} × {len(items)} — {items[0].get('exception', '')}")
            parts.append(f"      輸入:{items[0].get('input_repr', '')[:120]}")
        parts.append("    崩潰會讓治理流程停在一個沒有指引的 traceback 上,"
                     "而那與『正確擋下』在退出碼上分不開。")
        return False, "\n".join(parts), detail
    return True, (f"property-fuzz:{d['cases']} 次呼叫、{len(d.get('targets', []))} 個目標,"
                  f"無崩潰(seed={d.get('seed')})"), detail


def judge_performance(text: str, baseline: dict) -> tuple[bool, str, dict]:
    """效能回歸。比的是**比值**(目標時間 / 同一輪校準負載時間),不是秒數。

    共用的 CI runner 速度差好幾倍,拿絕對秒數設門檻會讓這道閘三不五時無故轉紅。
    比值把機器快慢在分子分母上約掉,剩下的才是跨機器可比的量。
    """
    try:
        cur = json.loads(text or "{}")
    except ValueError as exc:
        return False, f"performance:產物不是合法 JSON:{exc}\n  看不懂的報告不等於沒問題。", {}
    # `json.loads` 成功不代表拿到物件——`1` / `[]` / `"x"` 都是合法 JSON,而後續的
    # `.get()` 與 `in` 會當場崩潰。CHG-20260808-01 一次抓到 **7 個**同族崩潰,全在本檔。
    # 位置刻意在 try/except **之外**:放進 except 裡會排在 return 之後,是死碼
    # ——一個永遠不會執行的守衛,和沒有守衛一樣(CHG-20260805-04 記過同一形狀)。
    if not isinstance(cur, dict):
        return False, f"performance:產物頂層應為物件,實得 {type(cur).__name__}——看不懂的報告不等於沒問題。", {}
    if cur.get("error"):
        return False, f"performance:{cur['error']}——量不出來就不放行", {}
    targets = cur.get("targets") or {}
    if not targets:
        # 量了 0 個目標而回報沒有退步,是恆真回報(KN-001)
        return False, "performance:0 個目標——沒有量到任何東西,不是「沒有退步」", {}

    prev = ((baseline or {}).get("performance") or {}).get("targets") or {}
    if not prev:
        return True, (f"performance:{len(targets)} 個目標,尚無基準——本輪僅建立基線。"
                      "這不是「沒有退步」,下一輪起才有得比。"), {"baseline": False}

    tol = float(((baseline or {}).get("performance") or {}).get("tolerance") or PERF_TOLERANCE)
    regressed, improved = [], []
    for name, now in targets.items():
        before = prev.get(name)
        if not before:
            continue                       # 新目標:沒有可退步的基準
        b, a = float(before.get("ratio", 0)), float(now.get("ratio", 0))
        if b <= 0:
            continue
        if a > b * tol:
            regressed.append((name, b, a, a / b))
        elif a < b / tol:
            improved.append((name, b, a))
    gone = [n for n in prev if n not in targets]

    detail = {"targets": len(targets), "regressed": len(regressed),
              "improved": len(improved), "tolerance": tol}
    parts = [f"performance:{len(targets)} 個目標(門檻 {tol}×,比的是相對校準負載的比值)"]
    if gone:
        parts.append(f"  ⚠️ 基準裡有 {len(gone)} 個目標本輪沒量到:{', '.join(gone[:5])}"
                     "——移除目標等於移除觀測點,請確認是刻意的")
    if improved:
        parts.append(f"  · {len(improved)} 個目標變快了(可更新基準把這個高度鎖住):"
                     + ", ".join(f"{n} {b:.4f}→{a:.4f}" for n, b, a in improved[:5]))
    if regressed:
        parts.append("  ❌ **效能退步**——這些閘是逐 task 跑的,慢下來的代價會被乘上 task 數,"
                     "而變慢的閘會被關掉:")
        parts += [f"    · {n}:{b:.4f} → {a:.4f}(慢了 {r:.1f} 倍)"
                  for n, b, a, r in regressed]
        parts.append("    刻意的退步(例如換上更嚴格的演算法)請連同理由更新基準"
                     "(需指名授權的 CHG)。")
        return False, "\n".join(parts), detail
    parts.append("  · 無退步")
    return True, "\n".join(parts), detail
