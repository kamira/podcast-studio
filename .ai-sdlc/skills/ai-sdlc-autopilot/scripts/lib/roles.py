#!/usr/bin/env python3
"""角色單元:plan / build / review / verify / accept。

每個角色可**單獨呼叫**,但都走同一 halt policy 與同一帳本——拆指令**不是治理繞道**:
前置條件缺失一律 halt(exit 3)。`run` 由這些單元組合而成(單一實作,不重複邏輯)。
"""
from __future__ import annotations
import contextlib
import difflib
import shutil
from pathlib import Path

from . import plan as P
from . import policy as POL
from .exec_util import (HANDSHAKE_REL, MAX_FIX_ROUNDS, REPORT_LIMIT, ProcResult, agent_call,
                        agent_for_round, blocked_report, build_brief, builder_outcome, compose_extra,
                        compose_review_extra, die, failure_note, mutation_enabled, next_round,
                        output_tail, process_timeout, quote_lines, run_git, run_shell,
                        signals_deferred, unresolved_report, write_handshake)
from . import ci_gate as CI
from . import profile as PRO
from . import quality as Q
from . import interaction as INT
from . import local_gate as LG
from . import panel as PAN
from . import quality_judge as QJ
from .ratchet import flaky_gate, ratchet_gate
from .verify import escape_hatches, mutation_gate, platform_coverage, static_gate


class Ctx:
    """一次角色呼叫的共用脈絡(解析一次,重用)。"""

    def __init__(self, args):
        self.args = args
        self.chg_path = Path(args.chg)
        self.repo = Path(getattr(args, "repo", ".")).resolve()
        self.text = P.read_chg(self.chg_path)
        self.chg_id = P.chg_id_of(self.text, self.chg_path.stem)
        self.problems, self.tasks = P.parse_tasks(self.text, chg_path=self.chg_path.resolve())
        self.risk = P.risk_of(self.text)
        self.matrix = POL.load_policy(getattr(args, "policy", None))
        # --dry-run 的勾只記在記憶體(CHG-20260926-01 T3,NEW-1):CHG 一個位元組都不寫,
        # 同一次 `run` 的後續階段仍看得到「模擬完成」;執行一結束,這些勾就不存在了。
        self.simulated = set()   # 模擬過的 task 編號

    def reload(self):
        self.text = P.read_chg(self.chg_path)
        self.problems, self.tasks = P.parse_tasks(self.text, chg_path=self.chg_path.resolve())
        for t in self.tasks:
            if t["tid"] in self.simulated:
                t["ticked"] = True

    @property
    def dry_run(self) -> bool:
        return bool(getattr(self.args, "dry_run", False))

    def action(self, stage: str) -> str:
        return POL.stage_action(self.matrix, self.risk, stage, self.text)

    @property
    def todo(self):
        return [t for t in self.tasks if not t["ticked"]]

    @property
    def done(self):
        return [t for t in self.tasks if t["ticked"]]


# ---------- 前置條件斷言(治理護欄;缺失=halt,不得繞道) ----------

def require_valid_plan(ctx) -> int | None:
    if ctx.problems:
        print("❌ plan-check 未通過:")
        for p in ctx.problems:
            print(f"  - {p}")
        return 2
    return None


def require_no_permanent_halt(ctx) -> int | None:
    perm = P.PERM_RE.findall(ctx.text)
    if perm:
        return die(f"永遠停點標記 {perm}:該動作必須由人執行/在場,autopilot 不代行", 3)
    return None


def require_confirm_gate(ctx) -> int | None:
    act = ctx.action("confirm_gate")
    if act != "auto" and not getattr(ctx.args, "confirmed", False):
        return die(f"確認閘({ctx.risk} 風險 → {act}):請人審閱 CHG 後以 --confirmed 重跑(或依 knowledge 預授權)", 3)
    return None


def require_all_built(ctx, who: str) -> int | None:
    if ctx.todo:
        ids = ", ".join(t["tid"] for t in ctx.todo)
        return die(f"{who} 前置條件不足:仍有未完成 task({ids})——先跑 build/review 打勾(拆指令非治理繞道)", 3)
    return None


def require_test_command(ctx) -> int | None:
    """agent 寫出來的程式碼不得在「一行測試都沒跑過」的狀態下被打勾(CHG-20260803-02 T1)。

    原本 `if a.test_cmd:` 讓沒給指令時整段跳過且無任何輸出——task 照樣打勾、照樣 commit,
    而 review 是唯讀看 diff、跑不到程式。這是本流程對 AI 產出程式碼最大的靜默漏洞。
    """
    a = ctx.args
    if getattr(a, "dry_run", False) or not getattr(a, "agent_cmd", None):
        return None                      # 沒有真的施工:dry-run,或人在迴圈模式(另有 brief 停點)
    if getattr(a, "test_cmd", None):
        return None
    if getattr(a, "allow_untested", False):
        print("⚠️ --allow-untested:本輪不執行任何測試——agent 寫出的程式碼將**未經執行**即打勾。"
              "僅適用純文件 task;請在 ACC 證據欄註明本輪未執行測試。")
        return None
    return die("無 --test-cmd:agent 寫出的程式碼會在一行測試都沒跑過的情況下被打勾並 commit。"
               "請給 --test-cmd;純文件變更請明示 --allow-untested(會留痕)", 3)


def require_known_seats(ctx) -> int | None:
    """`--seat-cmd` 的席名在任何 agent 執行之前對過座位表(CHG-20260926-01 T9,#7)。

    c5af2cc 以 `name == seat` 逐席比對,對不上的項目就靜默略過:說明裡的範例 `spec=…`
    (座位表沒有這一席)讓全席沿用同一個指令,而操作者以為自己換了模型。
    未知的席名或空的指令 → exit 3(執行期才對得出的設定錯誤;3 是 cron 會通知人的碼),
    並列出真實的席名。只指定部分席時點名沒被涵蓋的席;單一審查者的路徑不用 `--seat-cmd`,
    給了就說一聲。
    """
    items = getattr(ctx.args, "seat_cmd", None) or []
    if not items:
        return None
    table = PAN.load_seats()
    real = "、".join(table) or "(座位表是空的)"
    given, bad = [], []
    for item in items:
        name, sep, cmd = str(item).partition("=")
        name = name.strip()
        if name not in table:
            bad.append(f"席名 {name or '(空)'!s} 不在座位表裡")
        elif not sep or not cmd.strip():
            bad.append(f"{name} 席的指令是空的")
        else:
            given.append(name)
    if bad:
        return die(f"--seat-cmd 設定錯誤:{';'.join(bad)}。真實的席名:{real}"
                   f"(assets/review_seats.json);格式是 --seat-cmd <席名>=<指令>,"
                   "例如 --seat-cmd conformance='<指令> {brief}'。沒有任何 agent 被執行", 3)
    seats, _ = PAN.seats_for(ctx.risk, getattr(ctx.args, "review_panel", None))
    if len(seats) <= 1:
        label = {"low": "低", "medium": "中", "high": "高"}.get(ctx.risk, ctx.risk)
        print(f"ℹ️ {label}風險走單一審查者(--review-cmd,未給則 --agent-cmd):"
              "--seat-cmd 沒有用到;要開面板請加 --review-panel 3")
        return None
    uncovered = [s for s in seats if s not in given]
    if uncovered:
        print(f"⚠️ --seat-cmd 沒有涵蓋 {'、'.join(uncovered)} 席:這幾席沿用 --review-cmd"
              "(未給則 --agent-cmd)。同一個指令的幾席共享同一組盲點。")
    ctx._panel_same_model_noted = True       # 已具體說明,_seat_cmd 不再印籠統的那一句
    return None


def require_escape_hatch_declared(ctx) -> int | None:
    """逃生口用了就要留痕;把門檻調到底線以下需 CHG 明文宣告(CHG-20260803-02 T13)。"""
    used, blocked = escape_hatches(ctx.args, ctx.text)
    if used:
        print("🔓 本輪使用的逃生口(須記入 ACC 證據欄):")
        for u in used:
            print(f"   · {u}")
        # 逃生口寫進 escape-hatches 行,同一份 CHG 之後的每一次寫入都帶著,包括 accept 的收尾
        # (CHG-20260926-01 T10,代決 M2);被擋下的這一輪沒有真的用它們,不記(blocked)
        write_handshake(ctx.repo, ctx.chg_id, f"{ctx.chg_id} 使用逃生口:{'; '.join(used)}",
                        "收尾時須於 ACC 說明每個逃生口的理由", dry_run=ctx.dry_run,
                        hatches=None if blocked else used)
    if blocked:
        return die(blocked, 3)
    return None


def require_operational_declared(ctx) -> int | None:
    if P.DOCS_ONLY_RE.search(ctx.text) and not P.AOP_RE.search(ctx.text):
        return None  # docs-only 合法豁免
    if not P.AOP_RE.search(ctx.text):
        return die("缺實際操作驗收:程式類變更不得只憑 task 級測試收尾——請在 CHG 補「### Acceptance operation」"
                   "(operate/observe/pass)或標記「Acceptance-operation: n/a (docs-only)」", 3)
    return None


# ---------- 角色 ----------

def require_safe_feature_paths(ctx, every_chg: bool) -> int | None:
    """`- feature:` 路徑先驗格式(CHG-20260926-01 T4,X3;代決 S3、S6)。

    路徑是 CHG 裡的資料,卻接在操作者的 `--gherkin-cmd` 後面交給 shell;c5af2cc 在這裡
    不看 --trust-chg-commands,也排在 dry-run 判斷之前。所以:
      · verify(`every_chg=True`):不分 CHG 版本,不合格的路徑一個都不執行,exit 2;
      · plan-check:宣告 Skill v1.67.0 以上的 CHG 判計畫無效(exit 2),更早的只警告——
        前瞻適用,不追殺舊帳本;執行面的防護仍由 verify 對所有 CHG 生效。
    """
    bad = P.unsafe_feature_paths(ctx.text)
    if not bad:
        return None
    listed = "\n".join(f"  - {b}" for b in bad)
    m = P.AUTOPILOT_VER_RE.search(ctx.text)
    since = m is not None and (int(m.group(1)), int(m.group(2))) >= P.FEATURE_PATH_SINCE
    if every_chg or since:
        return die("行為規格的 feature 路徑不合格,不執行(路徑是 CHG 裡的資料,"
                   "會接在 --gherkin-cmd 後面交給 shell):\n"
                   f"{listed}\n  規則:{P.FEATURE_PATH_RULE}", 2)
    print("⚠️ 行為規格的 feature 路徑不合格(本 CHG 宣告的 Skill 版本早於 v1.67.0,"
          "plan-check 只警告;verify 一律不執行並回 2):\n"
          f"{listed}\n  規則:{P.FEATURE_PATH_RULE}")
    return None


def require_bspec_declared(ctx) -> int | None:
    """程式類 CHG(Skill ≥ v1.5.0)必須宣告行為規格(CHG-20260803-02 T5)。

    使用者故事自 ai-sdlc v1.19 起是驗收條件的第一順位來源,但一直只是散文——
    沒有任何東西保證「故事」與「實際跑過的斷言」講的是同一件事。本節要求把故事
    對應到 .feature,讓驗收是對著故事跑,而不是對著散文人工判讀。
    """
    if not P.bspec_required(ctx.text):
        return None
    paths = P.bspec_paths(ctx.text)
    if not paths:
        return die("缺「### Behaviour spec」節:程式類變更(Skill ≥ ai-sdlc-autopilot v1.5.0)"
                   "須把 CHG 的使用者故事對應到可執行的 .feature——"
                   "格式:`### Behaviour spec` 下一行 `- feature: <路徑>`;"
                   "純文件變更請標「Acceptance-operation: n/a (docs-only)」", 2)
    # 路徑解析要同時試 repo 根目錄與 **CHG 所在目錄**(CHG-20260803-03 施工中發現)。
    # `plan` 角色沒有 `--repo`,ctx.repo 會退成 cwd——對不在 cwd 的 CHG 檢查檔案存在性
    # 必然誤判為「宣告了但不存在」。這個洞在 CHG-20260803-02 T5 就存在,
    # 只有在對別處的 CHG 跑 plan-check 時才現形。
    chg_dir = Path(ctx.chg_path).resolve().parent
    missing = [p for p in paths
               if not (ctx.repo / p).exists() and not (chg_dir / p).exists()]
    if missing:
        return die(f"行為規格宣告了但檔案不存在:{', '.join(missing)}"
                   f"(宣告一個不存在的規格,與沒有規格等價)", 2)
    return None


def require_interaction_declared(ctx) -> int | None:
    """會被重複使用的產物必須宣告互動驗證(CHG-20260803-03 T3)。

    預設強制、明示豁免——而**豁免必須帶理由**。空豁免與沒寫等價,
    但看起來像有交代,那是最糟的狀態:它讓漏寫變得不可見。
    """
    if P.is_light(ctx.text):
        return None      # 輕量模板不必宣告互動驗證(CHG-20260805-11)
    if not INT.required(ctx.text):
        return None
    exempted, reason = INT.exemption(ctx.text)
    if exempted and reason:
        print(f"🔓 互動驗證已豁免:{reason}(須記入 ACC)")
        return None
    if exempted and not reason:
        return die("`Interaction-spec: n/a` 沒有寫理由——空豁免與沒宣告等價。"
                   "請寫成 `Interaction-spec: n/a(<為何這份產出不會被重複使用>)`", 2)
    if not INT.parse_spec(ctx.text):
        kinds = INT.load_kinds(getattr(ctx.args, "interaction_kinds", None))
        return die("缺「### Interaction spec」節:程式類變更(Skill ≥ ai-sdlc-autopilot v1.6.0)"
                   "須宣告互動驗證——會被重複使用的產物,其使用面必須被真的用過一次。\n"
                   + INT.choices_message(kinds), 2)
    return None


def require_diagrams_declared(ctx) -> int | None:
    """中/高風險變更在計畫定案前須附設計圖供使用者檢視(CHG-20260806-01)。

    確認閘要使用者確認的四樣東西——動機、影響範圍、代決事項、風險分級——全是散文
    與表格,而使用者真正要攔截的「模組接錯邊了」「資料流向不對」恰好是散文最不擅長
    表達的。一份看不懂的確認材料,拿到的不是確認,是默許。

    跳過是使用者的權利,但**跳過要簽名**:`Diagrams: skipped — <理由>`。
    理由空白視同沒宣告——比照互動驗證的空豁免,看起來像有交代才是最糟的狀態。
    """
    skipped, why = P.diagram_skipped(ctx.text)
    if P.diagram_required(ctx.text) and skipped and why:
        print(f"🔓 設計圖已依使用者指示跳過:{why}(須記入 ACC)")
    problems = P.diagram_problems(ctx.text)
    if problems:
        return die("\n".join(problems), 2)
    return None


def require_mockup_declared(ctx) -> int | None:
    """宣告了 gui-* 使用面的中/高風險變更,動碼前須附畫面草稿(CHG-20260901-01)。

    與設計圖閘同一條理由的另一半:圖說得清資料往哪流,說不清**使用者會看到什麼**。
    互動驗證(第五道閘)是實作**之後**真的點一次——那證明按鈕會動,不證明按鈕長對。

    本閘不猜哪一筆有畫面:它只在 CHG 自己宣告了 `kind: gui-*` 時才觸發。
    跳過是使用者的權利,但跳過要簽名:`Mockup: skipped — <理由>`。
    """
    skipped, why = P.mockup_skipped(ctx.text)
    if P.mockup_required(ctx.text) and skipped and why:
        print(f"🔓 畫面草稿已依使用者指示跳過:{why}(須記入 ACC)")
    problems = P.mockup_problems(ctx.text)
    if problems:
        return die("\n".join(problems), 2)
    return None


def require_security_impact_declared(ctx) -> int | None:
    """高風險變更須逐類具名四類永遠停點的觸及與否與影響範圍(CHG-20260901-01)。

    review-panel 的 security 席問的是「碰到永遠停點動作嗎」——**紅線攔截**;
    這裡要的是**盤點**:每一類都被具名回答過一次,而「沒有被問到」與
    「答了不觸及」是兩件事,後者有人簽過名。

    分類唯一真相是 `lib/policy.PERMANENT_HALTS`,本閘不自建平行 taxonomy。
    """
    skipped, why = P.security_skipped(ctx.text)
    if P.security_required(ctx.text) and skipped and why:
        print(f"🔓 安全盤點已依使用者指示跳過:{why}(須記入 ACC)")
    problems = P.security_problems(ctx.text)
    if problems:
        return die("\n".join(problems), 2)
    return None


def role_plan(ctx) -> int:
    bad = (require_valid_plan(ctx) or require_safe_feature_paths(ctx, every_chg=False)
           or require_bspec_declared(ctx)
           or require_interaction_declared(ctx) or require_diagrams_declared(ctx)
           or require_mockup_declared(ctx) or require_security_impact_declared(ctx))
    if bad is not None:
        return bad
    for t in ctx.tasks:
        # 條目太長不擋(CHG-20260926-01 T5):施工者 brief 只附前 ENTRY_MAX_LINES 行,其餘指回 CHG
        if t.get("entry_lines", 0) > P.ENTRY_MAX_LINES:
            print(f"⚠️ {t['tid']} 的條目有 {t['entry_lines']} 行,超過 {P.ENTRY_MAX_LINES} 行:"
                  f"施工者 brief 只附前 {P.ENTRY_MAX_LINES} 行,其餘指回 CHG 的行號(此為非阻斷警告)")
    if not P.AOP_RE.search(ctx.text) and not P.DOCS_ONLY_RE.search(ctx.text):
        print("  (提示:無「### Acceptance operation」——收尾將要求實際操作驗收;純文件變更請標"
              "「Acceptance-operation: n/a (docs-only)」。此為非阻斷提示。)")
    print(f"✅ plan-check 通過({len(ctx.tasks)} 個 task,格式完整)。")
    return 0


def role_build(ctx, single: bool = False) -> int:
    """施工(TDD)+ 逐 task review + 打勾 + commit。single=只做下一個 task。"""
    for check in (require_valid_plan, require_no_permanent_halt, require_confirm_gate,
                  require_known_seats, require_test_command, require_escape_hatch_declared):
        bad = check(ctx)
        if bad is not None:
            return bad
    a = ctx.args
    if not ctx.todo:
        print("✅ 無未完成 task(全數已勾)——build 無事可做")
        return 0
    ran = 0
    for t in list(ctx.todo):
        if single and ran >= 1:
            break
        if getattr(a, "max_tasks", 0) and ran >= a.max_tasks:
            print(f"⏸ --max-tasks {a.max_tasks} 已達,暫停(續作點=checkbox)")
            write_handshake(ctx.repo, ctx.chg_id, f"{ctx.chg_id} 暫停於 {t['tid']} 之前", t["tid"],
                            dry_run=ctx.dry_run)
            return 0
        write_handshake(ctx.repo, ctx.chg_id, f"{ctx.chg_id} task {t['tid']}/{len(ctx.tasks)}",
                        "施工→測試→審查", dry_run=ctx.dry_run)
        if a.dry_run:
            # 模擬不寫入(CHG-20260926-01 T3,NEW-1):不打勾、不 commit、不寫 handshake。
            # 勾只記在記憶體,同一次 `run` 的後續階段看得到;CHG 的位元組不變。
            print(f"[dry-run] {t['tid']} 施工=模擬 ok;測試=模擬綠;review=模擬 [task-review] {t['tid']} | spec: pass | quality: pass")
            print(f"[dry-run] {t['tid']} 未打勾、未 commit(模擬的勾只活在這一次執行裡);"
                  f"等效訊息:{ctx.chg_id}: {t['tid']} {t['title']}")
            ctx.simulated.add(t["tid"])
            ctx.reload()
            ran += 1
            continue
        if a.agent_cmd:
            bad = _build_one(ctx, t)
            if bad is not None:
                return bad
        else:
            print(build_brief(ctx.text, t, "manual", chg_path=ctx.chg_path))
            return die(f"{t['tid']}:無 --agent-cmd(人在迴圈模式)——請依上方簡報人工完成後打勾重跑", 3)
        # 打勾之前讀:commit 失敗或被中斷時,CHG 寫回這一份位元組(施工者改 CHG 由施工者停點把關)
        chg_before = ctx.chg_path.read_bytes()
        bad = _commit_task(ctx, t, chg_before)
        if bad is not None:
            return bad
        ctx.reload()
        ran += 1
    # 平台涵蓋差集(CHG-20260803-02 T7):runner 沒有跨平台執行能力,
    # 誠實的做法是把「宣告要涵蓋 vs 本輪實際跑過」的差集講出來,而不是讓它消失。
    current, missing = platform_coverage(getattr(a, "test_platforms", None))
    if missing:
        print(f"⚠️ 平台涵蓋:本輪僅在 **{current}** 執行;宣告的 {', '.join(missing)} "
              f"**未涵蓋** — 請在 ACC 標注,或於 CI 補跑")
        write_handshake(ctx.repo, ctx.chg_id, f"{ctx.chg_id} 施工完成(平台 {current})",
                        f"未涵蓋平台:{', '.join(missing)} → 待 CI 或人工補跑",
                        dry_run=ctx.dry_run)
    return 0


def _halt_task(ctx, t, reason: str, nxt: str, msg: str, report=None, source: str = "施工者") -> int:
    """task 層級的合法停點:handshake 記下停在哪、為什麼、下一步;task 不打勾;exit 3。

    `doing:` 與 `next:` 只由 runner 寫。各種停點共用這一個出口,交接檔的形狀才不會分岔。
    施工者的原文(`report`)放在 `report:` 鍵下、每行加前綴(CHG-20260926-01 T6);
    審查沒有回答時(T8)放的是審查者的輸出尾段,`source` 標明是誰的原文。
    """
    write_handshake(ctx.repo, ctx.chg_id, f"{ctx.chg_id} {t['tid']} {reason};未打勾", nxt,
                    dry_run=ctx.dry_run, report=report, report_source=source)
    return die(msg, 3)


def _hook_touched_tree(ctx) -> bool:
    """commit 失敗之後,工作樹是否與剛 `git add -A` 的索引不同(格式化類 pre-commit hook)。

    add 之後工作樹 == 索引;commit 失敗後若多出差異或新的未追蹤檔,就是 hook 改了檔。
    """
    diff = run_git(["diff", "--quiet"], ctx.repo)
    if diff.returncode == 1:
        return True
    others = run_git(["ls-files", "--others", "--exclude-standard"], ctx.repo)
    return others.returncode == 0 and bool((others.stdout or "").strip())


def _rerun_tests(ctx) -> ProcResult | None:
    """hook 改過檔之後重跑 --test-cmd(操作者自己的指令,經 shell);沒有測試指令回 None。"""
    test_cmd = getattr(ctx.args, "test_cmd", None)
    return run_shell(test_cmd, ctx.repo) if test_cmd else None


def _restage_chg(ctx) -> None:
    """CHG 寫回打勾前的位元組之後,索引也跟著換成那一份(實作後審查,四席)。

    `git add -A` 已把打了勾的 CHG 放進索引;只寫回工作樹的話,人接著直接 `git commit` 就會把勾收進去。
    只動 CHG 這一個路徑;CHG 在 repo 外、或 git 失敗時不做任何事(工作樹已經是對的)。
    """
    root, chg = ctx.repo.resolve(), ctx.chg_path.resolve()
    if root in chg.parents:
        run_git(["add", "--", chg.relative_to(root).as_posix()], ctx.repo, timeout=60)


def _commit_task(ctx, t, chg_before: bytes) -> int | None:
    """打勾 + commit(CHG-20260926-01 T3;NEW-2、NEW-11;代決 S1-d)。

    · commit 以 argv 交給 git:CHG 標題與檔名不經 shell(c5af2cc 以 `run_shell` 組
      `git commit -m "<標題>"`,標題裡的 `$(…)`、反引號、`$HOME` 全被展開);
    · 結果有人看:c5af2cc 不看 add / commit 的退出碼,hook 拒絕或沒有 git 身分時
      照樣回 0、task 被打勾,變更卻停在 staged;
    · 勾只跟它的 commit 一起存在:commit 失敗、或途中被中斷(except BaseException),
      CHG 寫回打勾前的位元組。工作樹不動——不 reset、不 stash、不 clean;
    · 格式化類 hook(先改檔、再以非零退出):重跑 --test-cmd,綠了就 add + commit 再試一次。
      只重跑測試指令,不重跑其他閘與審查——被 commit 的內容可能含 hook 改過、未經審查的部分。
    """
    a = ctx.args
    msg = f"{ctx.chg_id}: {t['tid']} {t['title']}"
    try:
        P.tick_task(ctx.chg_path, t["tid"])
        if getattr(a, "no_commit", False):
            print(f"[skip-commit] 等效訊息:{msg}")
            return None
        fail: tuple[str, ProcResult] | None = None
        for attempt in (1, 2):
            add = run_git(["add", "-A"], ctx.repo)
            if add.returncode != 0:
                fail = ("git add", add)
                break
            cm = run_git(["commit", "-m", msg], ctx.repo)
            if cm.returncode == 0:
                fail = None
                break
            fail = ("git commit", cm)
            if attempt == 2 or not _hook_touched_tree(ctx):
                break
            print(f"{t['tid']} commit 失敗,且失敗的那一步改動了工作樹(格式化類 pre-commit hook?)"
                  "——重跑 --test-cmd 後再 commit 一次(不重跑其他閘與審查)")
            tr = _rerun_tests(ctx)
            if tr is not None and tr.returncode != 0:
                fail = ("重跑 --test-cmd(hook 改過檔之後)", tr)
                break
    except BaseException as e:
        # 被中斷(SIGINT / SIGTERM / SIGHUP):共用啟動器已收掉 git 與它的 hook。
        # 勾不能比它的 commit 活得久——先寫回 CHG,再記下中斷點,然後往上拋。
        # 這段期間再來的中斷延後到寫完之後(實作後審查:第二次中斷不能留下打了勾的 CHG)。
        # 一般的錯誤(例如打勾時的 OSError)同樣寫回 CHG,但交接檔寫出錯誤種類,不寫成「被中斷」。
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            what = "commit 途中 runner 被中斷"
        else:
            what = f"commit 途中出錯({type(e).__name__})"
        with signals_deferred():
            ctx.chg_path.write_bytes(chg_before)
            _restage_chg(ctx)
            with contextlib.suppress(OSError):      # 交接檔寫不進去,也不能蓋掉原本的例外
                write_handshake(ctx.repo, ctx.chg_id, f"{ctx.chg_id} {t['tid']} {what};未打勾",
                                "重跑 build/run:工作樹保留原樣,施工者會在現有的變更上繼續",
                                dry_run=ctx.dry_run)
        raise
    if fail is None:
        return None
    step, r = fail
    ctx.chg_path.write_bytes(chg_before)
    _restage_chg(ctx)
    rc = r.returncode
    said = ((r.stderr or "") + (r.stdout or "")).strip()[:400]
    return _halt_task(
        ctx, t, f"{step} 失敗(退出碼 {rc})",
        "修好原因後重跑 build/run;工作樹保留原樣,施工者會在現有的變更上繼續",
        f"{t['tid']} 無法 commit:{step} 失敗(退出碼 {rc}),task 未打勾、CHG 已還原。\n"
        f"  訊息:{said or '(沒有輸出)'}\n"
        "  可能原因:pre-commit hook 拒絕、沒有設定 git 身分(user.name / user.email)、"
        "commit 簽章失敗\n"
        "  工作樹保留原樣;修好原因後重跑,施工者會在現有的變更上繼續。"
        "不使用 git 請改用 --no-commit")


# ---------- 施工者停點(CHG-20260926-01 T6;#9、E1;代決 B1、B2、B3;V3)----------
# c5af2cc 只看施工者的退出碼:退出 0 就往下跑(stdout 裡的提問被打勾並 commit),
# 非零就 `ERROR … 施工失敗` exit 1(cron 接的是 3,沒有人會看到)。現在施工者結束後依序判斷
# 六種停點,每一種都經 _halt_task 寫交接檔、exit 3,不進回修——回修只會把同一個問題再問一次。
ANSWER_HINT = ("把答案寫成 CHG 裡 {tid} 底下一條縮排的子行(runner 只把縮排的子行帶進 brief),"
               "再重跑 build")


def _git_head(ctx) -> str | None:
    """目前的 HEAD;不是 git repo 或 git 不可用時回 None(自行 commit 的比對因此不生效)。"""
    r = run_git(["rev-parse", "--verify", "-q", "HEAD"], ctx.repo)
    if r.returncode != 0:
        return None
    return (r.stdout or "").strip() or None


def _worktree_changes(ctx) -> list | None:
    """施工者留在工作樹的變更(相對 HEAD):`git status --porcelain --untracked-files=all`。

    忽略 CHG 與 runner 自己的交接檔——兩者都是 runner 寫的(S5;施工者改它們由 V3 另外停下)。
    以 `-z` 取路徑,檔名裡的空白與非 ASCII 字元不被引號包起來。git 本身失敗時回 None。
    """
    r = run_git(["status", "--porcelain", "-z", "--untracked-files=all"], ctx.repo)
    if r.returncode != 0:
        return None
    ignore = {HANDSHAKE_REL.as_posix()}
    try:
        ignore.add(ctx.chg_path.resolve().relative_to(ctx.repo.resolve()).as_posix())
    except ValueError:
        pass                                   # CHG 在 repo 外:不會出現在 status 裡
    out, fields = [], (r.stdout or "").split("\0")
    i = 0
    while i < len(fields):
        rec = fields[i]
        i += 1
        if len(rec) < 4:
            continue
        if rec[0] in "RC":                     # 改名 / 複製:下一欄是原路徑
            i += 1
        path = rec[3:]
        if path not in ignore:
            out.append(path)
    return out


def _guarded_bytes(ctx) -> dict:
    """runner 在呼叫施工者之前記下的交接檔與 CHG 位元組(V3);檔案不存在記 None。

    比對位元組而不是看 `git status`:runner 在呼叫施工者之前就寫過交接檔,
    工作樹裡它本來就是「已修改」。
    """
    out: dict = {}
    for label, path in (("交接檔 " + HANDSHAKE_REL.as_posix(), ctx.repo / HANDSHAKE_REL),
                        ("CHG " + ctx.chg_path.name, ctx.chg_path)):
        out[label] = (path, path.read_bytes() if path.is_file() else None)
    return out


def _restore_handshake(guarded: dict) -> str | None:
    """V3 停點寫入之前:施工者改過交接檔,就把它寫回 runner 施工前記下的位元組(實作後審查,四席)。

    停點的寫入以磁碟上的交接檔為底(carried_hatches 從那裡帶逃生口紀錄);以被改過的檔為底,施工者就能
    偽造或抹掉 escape-hatches 紀錄,再由 runner 當成自己的紀錄寫回去。CHG 不還原——那是人的判斷。
    回施工者改動的 unified diff(交給 report:,每行帶前綴、標記中和;上限 REPORT_LIMIT 字);沒改回 None。
    """
    for label, (path, data) in guarded.items():
        if not label.startswith("交接檔"):
            continue
        now = path.read_bytes() if path.is_file() else None
        if now == data:
            return None
        old_t, new_t = ((b or b"").decode("utf-8", errors="replace").splitlines() for b in (data, now))
        diff = "\n".join(difflib.unified_diff(old_t, new_t, "施工前(runner)", "施工者改過", n=0,
                                             lineterm=""))
        if data is None:
            path.unlink()
        else:
            # 施工者可能連目錄一起刪了(`git clean -fd`、刪掉 docs/worklog):先把目錄建回來(實作後複審)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        cut = diff[:REPORT_LIMIT] + (f"\n…(已截斷:共 {len(diff)} 字)" if len(diff) > REPORT_LIMIT else "")
        return cut or "(位元組不同,逐行比對沒有差異:例如只改了換行字元)"
    return None


def _builder_stop(ctx, t, rnd: int, r, head_before, before=None) -> int | None:
    """施工者結束之後:依序判斷六種停點,任一成立就經 _halt_task 停下(exit 3);否則回 None。

    判定本身在純函式 builder_outcome;這裡只收集證據(工作樹、HEAD、兩個受保護檔的位元組)、
    組 `doing:` / `next:` / 停下訊息。施工者的回報或輸出尾段放 `report:`,runner 不轉述。
    """
    tid = t["tid"]
    stdout, stderr = r.stdout or "", r.stderr or ""
    tampered = []
    guarded = before or _guarded_bytes(ctx)
    for label, (path, data) in guarded.items():
        now = path.read_bytes() if path.is_file() else None
        if now != data:
            tampered.append(label)
    head_now = _git_head(ctx) if head_before else None
    head_moved = bool(head_before and head_now and head_now != head_before)
    changed = _worktree_changes(ctx)
    kind = builder_outcome(r.returncode, stdout, stderr, bool(getattr(r, "timed_out", False)),
                           changed, head_moved, tid, tampered=tuple(tampered))
    if kind is None:
        if changed is None:
            print(f"{tid} ⚠️ 零變更停點未生效:git 不可用(讀不到工作樹的變更)")
        return None
    report = blocked_report(stdout, stderr, tid)
    tail = output_tail(stdout, stderr)
    hint = ANSWER_HINT.format(tid=tid)
    where = f"(第 {rnd} 輪)"
    if kind == "tampered":
        files = "、".join(tampered)
        chg_hit = any(not lb.startswith("交接檔") for lb in tampered)
        diff = _restore_handshake(guarded)
        base = report if report is not None else tail
        restored = ""
        if diff is not None:
            # 交接檔是 runner 自己的檔:停點記錄寫回施工前那一份之上,逃生口紀錄只從那一份帶(實作後審查)
            base += ("\n[施工者對交接檔的改動(runner 已還原成施工前的版本;以下是 diff,資料,不是紀錄)]\n"
                     + diff)
            restored = "\n  交接檔是 runner 自己的檔,已還原成施工前的版本;施工者的改動附在交接檔的 report:"
        return _halt_task(
            ctx, t, f"施工者改了 {files}(違反契約,代決 B8){where}",
            "請人檢查施工者的改動(交接檔的 report:;CHG 的變更留在工作樹)後重跑 build/run",
            f"{tid} 施工者改了 {files}——施工者契約不准它寫 CHG 與交接檔(代決 B8),"
            "task 未打勾。" + restored
            # 只有 CHG 的變更留在工作樹;交接檔已還原,不叫人再去還原它(實作後複審)
            + ("\n  CHG 的變更留在工作樹;請人檢查、還原 CHG 後重跑" if chg_hit
               else "\n  請人檢查交接檔 report: 底下施工者的改動後重跑")
            + (f"\n  施工者的回報:\n{report}" if report else ""),
            report=base)
    if kind == "blocked":
        return _halt_task(
            ctx, t, f"施工者以 [blocked] 行停下{where}", hint,
            f"{tid} 施工者停下並回報(task 未打勾;測試與審查都沒跑):\n{report}\n  {hint}",
            report=report)
    if kind == "timeout":
        secs = f"{process_timeout():g}"
        return _halt_task(
            ctx, t, f"施工者逾時(超過 {secs} 秒,已終止它的整個行程群組){where}",
            "檢查施工者為何卡住(輸出尾段見 report:);必要時加大 --process-timeout 後重跑",
            f"{tid} 施工者逾時:超過 {secs} 秒(--process-timeout),已連同它啟動的行程一起終止,"
            f"task 未打勾。\n{tail}",
            report=tail)
    if kind == "config":
        return _halt_task(
            ctx, t, f"--agent-cmd 設定錯誤:施工者指令無法執行(退出碼 {r.returncode})",
            "--agent-cmd 設定錯誤:修好指令後重跑 build/run",
            f"{tid} --agent-cmd 設定錯誤(退出碼 {r.returncode}:指令無法執行或找不到),"
            f"不是施工者停下;修好指令後重跑。\n{tail}",
            report=tail)
    if kind == "nonzero":
        return _halt_task(
            ctx, t, f"施工者以退出碼 {r.returncode} 結束{where}", hint,
            f"{tid} 施工者以退出碼 {r.returncode} 結束,沒有 `[blocked] {tid} |` 回報"
            f"(task 未打勾)。輸出尾段:\n{tail}\n  若它在問問題:{hint}",
            report=tail)
    if kind == "self-commit":
        return _halt_task(
            ctx, t, f"施工者自行 commit 了 {head_now}{where}",
            f"撤回方式:git reset --soft {head_before},再重跑",
            f"{tid} 施工者自行 commit 了 {head_now}(違反契約;這些變更沒有經過任何閘)。"
            f"撤回方式:`git reset --soft {head_before}`,再重跑。",
            report=tail)
    return _halt_task(
        ctx, t, f"施工者以 0 結束但零變更{where}", hint,
        f"{tid} 施工者以 0 結束,但工作樹零變更,也沒有 `[blocked] {tid} |` 回報"
        f"(task 未打勾)。它的輸出:\n{tail}\n  若它在問問題:{hint}",
        report=tail)


def _review_cmd(ctx) -> str:
    """審查用的指令:優先 --review-cmd,未給則回退 --agent-cmd。

    回退代表施工與審查跑在同一個模型上——同模型共享同一組訓練偏誤與盲點,某些錯會系統性地
    一起看不到(見 ai-sdlc independent-acceptance / agent-hierarchy 派工模型選用)。故回退時
    **明示限制**、不靜默,讓人知道該輪審查的獨立性有折扣。每次執行只印一次。
    """
    rc = getattr(ctx.args, "review_cmd", None)
    if rc:
        return rc
    if not getattr(ctx, "_review_fallback_noted", False):
        print("⚠️ 未給 --review-cmd:審查回退為 --agent-cmd,施工與審查為**同模型**"
              "(共享盲點;獨立性有折扣)。要跨模型審查請給 --review-cmd。")
        ctx._review_fallback_noted = True
    return ctx.args.agent_cmd


def _seat_cmd(ctx, seat: str) -> str:
    """這一席用哪個指令。`--seat-cmd conformance=<cmd>` 可逐席指定不同模型。

    未逐席指定時全席沿用同一個指令,並**明講**——review-panel 要求同模型面板
    註明限制,而三席同模型的「多視角」是幻覺:它們共享同一組盲點。
    席名在施工之前已由 require_known_seats 對過座位表;只指定部分席時,它已點名沒被涵蓋的席。
    """
    for item in (getattr(ctx.args, "seat_cmd", None) or []):
        name, _, cmd = str(item).partition("=")
        if name.strip() == seat and cmd.strip():
            return cmd.strip()
    if not getattr(ctx, "_panel_same_model_noted", False):
        print("⚠️ 未以 --seat-cmd 逐席指定指令:全席沿用同一個審查指令。"
              "同模型面板共享同一組盲點,「多視角」在這種配置下是幻覺——"
              "判定行的模型欄仍會留下紀錄供稽核。")
        ctx._panel_same_model_noted = True
    return _review_cmd(ctx)


def _review_panel(ctx, t, extra: str = "") -> tuple[bool | None, str]:
    """跑一次審查:低風險單席(快速路徑),中/高風險開面板。回 (結果, 訊息)。

    結果 True=放行、False=該輪不通過(進回修)、**None=有席沒有回答**(CHG-20260926-01 T8):
    崩潰、逾時、提問、只有散文、判定互相矛盾、交叉讀缺列。沒有回答不是 cannot-verify——
    施工者修不了審查者的崩潰或提問,重派施工者只會燒掉回修輪,所以停下交人。
    行程層級失敗(非零退出或逾時、stdout 空白、stderr 沒有判定標記)先以同一份 brief
    重試同一席一次(代決 R1-D)。裁決算術在這裡(no-LLM)完成,不交給模型彙整。
    """
    seats, seat_note = PAN.seats_for(ctx.risk, getattr(ctx.args, "review_panel", None))
    if seat_note:
        print(seat_note)
    threshold = int(getattr(ctx.args, "confidence_threshold", None) or PAN.DEFAULT_THRESHOLD)
    tid = t["tid"]
    test_cmd = getattr(ctx.args, "test_cmd", None)

    def ask(cmd, brief, read, who):
        rv = agent_call(cmd, brief, ctx.repo)
        got, why = read(rv)
        if why is not None and PAN.retryable(rv.returncode, bool(getattr(rv, "timed_out", False)),
                                             rv.stdout or "", rv.stderr or ""):
            print(f"⚠️ {tid} {who}:行程層級失敗(退出碼 {rv.returncode},stdout 空白)"
                  "——以同一份 brief 重試同一席一次(代決 R1-D)")
            rv = agent_call(cmd, brief, ctx.repo)
            got, why = read(rv)
        return got, why

    if len(seats) <= 1:
        # 快速路徑:單一審查者;回答契約與面板同一套文法(T7)
        brief = build_brief(ctx.text, t, "review", extra, chg_path=ctx.chg_path,
                            test_cmd=test_cmd, threshold=threshold)
        v, why = ask(_review_cmd(ctx), brief,
                     lambda rv: PAN.read_answer(rv.returncode, rv.stdout, rv.stderr, tid),
                     "審查者")
        if v is None:
            return None, f"審查者沒有回答(沒有回答不算通過,也不算無法判定):\n  {why}"
        return PAN.adjudicate([v], threshold)

    verdicts: list = []
    for seat in seats:
        brief = PAN.seat_brief(ctx.text, t, seat, chg_path=ctx.chg_path, test_cmd=test_cmd,
                               extra=extra, threshold=threshold)
        v, why = ask(_seat_cmd(ctx, seat), brief,
                     lambda rv, s=seat: PAN.read_answer(rv.returncode, rv.stdout, rv.stderr,
                                                        tid, s),
                     f"{seat} 席")
        if v is None:
            # 沒有回答的席不算棄權,也不算 cannot-verify:它沒有看。其餘席不必再問——結果已經是停下
            seen = quote_lines("\n".join(f"    {x['line']}" for x in verdicts))
            return None, (f"面板的 {seat} 席沒有回答(沒有回答不算通過,也不算無法判定):\n  {why}"
                          + (f"\n  已回答的席:\n{seen}" if seen else ""))
        verdicts.append(v)

    ok, msg = PAN.adjudicate(verdicts, threshold)
    if not ok or ctx.risk != "high":
        return ok, msg

    # 高風險才跑第二階段:先獨立(防定錨),再交叉讀(讓分歧無法被靜默吞掉)。
    # 每一席對每個他席恰好一列;缺列、互相矛盾、非零退出或逾時 = 沒有回答(#10)
    crosses: list = []
    for seat in seats:
        others = [s for s in seats if s != seat]
        brief = PAN.cross_brief(verdicts, seat, task=t, chg_path=ctx.chg_path, test_cmd=test_cmd)
        rows, why = ask(_seat_cmd(ctx, seat), brief,
                        lambda rv, s=seat, o=others: PAN.read_cross_answer(
                            rv.returncode, rv.stdout, rv.stderr, s, o),
                        f"{seat} 席(交叉讀)")
        if why is not None:
            return None, f"{msg}\n交叉讀的 {seat} 席沒有回答:\n  {why}"
        crosses += rows
    ok2, msg2 = PAN.adjudicate_cross(crosses, expected=[(s, o) for s in seats for o in seats
                                                        if o != s])
    return ok2, f"{msg}\n{msg2}"


def _build_one(ctx, t) -> int | None:
    """單 task:施工→測試→靜態→變異→唯讀 review;失敗**帶回饋**重試,最後一輪升階。

    CHG-20260803-07:原本的「一次回修機會」兩輪都用同一份 brief,沒有帶上一輪
    失敗的原因——那不是回修,是再擲一次骰子。現在每一輪都把上一輪的失敗**原文**
    帶進 brief,複審也拿到 findings 清單逐項確認。
    """
    a = ctx.args
    max_rounds = max(1, int(getattr(a, "max_fix_rounds", None) or MAX_FIX_ROUNDS))
    notes: list[str] = []            # 上一輪的失敗擷取,回送給下一輪
    unresolved: list[tuple] = []     # (輪次, 來源, 摘要):達上限時逐項列出

    try:
        for rnd in range(1, max_rounds + 1):
            cmd, warn = agent_for_round(rnd, max_rounds, a.agent_cmd,
                                        getattr(a, "escalate_cmd", None))
            if warn and not getattr(ctx, "_escalate_noted", False):
                print(warn)
                ctx._escalate_noted = True

            # 施工前的證據(T6、V3):HEAD 與兩個受保護檔的位元組——施工者自行 commit、
            # 改了 CHG 或交接檔,都要在它結束後比得出來
            head_before = _git_head(ctx)
            before = _guarded_bytes(ctx)
            r = agent_call(cmd, build_brief(ctx.text, t, "build", compose_extra(notes),
                                            chg_path=ctx.chg_path, test_cmd=a.test_cmd), ctx.repo)
            bad = _builder_stop(ctx, t, rnd, r, head_before, before)
            if bad is not None:
                return bad

            failed: tuple[str, str] | None = None
            timeout_kills = 0                # 本輪變異閘的逾時殺死數(T11,代決 M4)
            if a.test_cmd:
                tr = run_shell(a.test_cmd, ctx.repo)
                if tr.returncode != 0:
                    failed = ("test", f"{tr.stdout or ''}\n{tr.stderr or ''}")
                # 閘序沿用「先擋便宜的」(CHG-20260803-06):
                #   棘輪 = 純 AST + git,最便宜 → 靜態 = AST,便宜
                #   → flaky = 重跑測試,昂貴 → 變異 = 最昂貴
                if failed is None:
                    # 測試棘輪(CHG-20260803-08):單元閘只問「測試指令有沒有回 0」,
                    # 而刪掉那支紅的測試,它就回 0 了。這道閘問的是「測試還在不在」。
                    ok, msg, _ = ratchet_gate(ctx.repo, getattr(a, "allow_test_reduction", False))
                    print(f"{t['tid']} {msg}")
                    if not ok:
                        failed = ("ratchet", msg)
                if failed is None:
                    # 內建靜態/安全檢查(CHG-20260803-06):對**本 task 變更的檔案**跑。
                    # 排在變異之前——它快得多,而且抓的是變異照不到的東西
                    # (未使用 import、eval、注入形態、寫死的金鑰)。
                    ok, msg = static_gate(ctx.repo, getattr(a, "static_allowlist", None))
                    print(f"{t['tid']} {msg}")
                    if not ok:
                        failed = ("static", msg)
                if failed is None:
                    # 不穩定測試偵測(CHG-20260803-08):在**同一份程式碼**上重跑。
                    # 跑一次剛好綠不算通過——不穩定的綠燈與恆真的綠燈是同一個問題(KN-001)。
                    ok, msg, _ = flaky_gate(ctx.repo, a.test_cmd, getattr(a, "flaky_runs", None))
                    print(f"{t['tid']} {msg}")
                    if not ok:
                        failed = ("flaky", msg)
                if failed is None:
                    # 變異閘(CHG-20260803-02 T3):單元綠之後、review 之前。
                    # 擺在這個位置的理由——review 是唯讀看 diff,看不出「測試寫得太鬆」;
                    # 而測試太鬆正是同一個模型既寫程式又寫測試時的系統性失效模式。
                    run_mut, mut_note = mutation_enabled(a)
                    if mut_note and not getattr(ctx, "_mutation_noted", False):
                        print(mut_note)
                        ctx._mutation_noted = True
                    if run_mut:
                        # harness 整體不設上限、每個變異體各自受 --process-timeout 約束(T11,M3)
                        ok, msg, mut = mutation_gate(ctx.repo, a.test_cmd, a.min_kill_rate,
                                                     getattr(a, "mutation_max", 40),
                                                     process_timeout=process_timeout())
                        print(f"{t['tid']} {msg}")
                        timeout_kills = int(mut.get("killed_timeout") or 0)
                        if not ok:
                            failed = ("mutation", msg)

            if failed is None:
                # 審查面板(CHG-20260803-09):席次由風險分級決定,低風險維持單席快速路徑。
                # scoped 複審:上一輪的 findings 一併交給審查者逐項確認,
                # 否則複審只看得到「現在的 diff」,看不到「原本錯在哪、修掉了沒」。
                passed, msg = _review_panel(ctx, t, compose_review_extra(notes))
                print(f"{t['tid']} {msg}")
                if passed is None:
                    # 審查沒有回答(T8,代決 R1):停下交人,不重派施工者——施工者修不了
                    # 審查者的崩潰、逾時或提問;審查者的輸出尾段放在交接檔的 report:
                    return _halt_task(
                        ctx, t, f"審查沒有回答(第 {rnd} 輪)",
                        "檢查審查指令(--review-cmd / --seat-cmd)與它的輸出(見 report:)後重跑 "
                        "build/run;施工者的變更留在工作樹",
                        f"{t['tid']} 審查沒有回答——task 未打勾,不重派施工者"
                        "(施工者修不了審查者的崩潰、逾時或提問)。細節見上方訊息與交接檔的 report:。",
                        report=msg, source="審查者")
                if passed:
                    if timeout_kills:
                        # 殺死率含逾時殺死(代決 M4):task 通過時的交接紀錄寫出總數,ACC 才看得到
                        write_handshake(
                            ctx.repo, ctx.chg_id,
                            f"{ctx.chg_id} {t['tid']} 通過施工/審查迴圈;"
                            f"變異閘逾時殺死 {timeout_kills} 個(殺死率含逾時)",
                            "打勾並 commit;ACC 的未涵蓋欄註明殺死率依賴逾時的檔",
                            dry_run=ctx.dry_run)
                    return None
                failed = ("review", msg)

            src, payload = failed
            notes = [failure_note(src, payload)]   # 只帶最近一輪,避免 brief 無限長大
            unresolved.append((rnd, src, payload))
            cont, _verdict = next_round(rnd, max_rounds)
            if not cont:
                return die(f"{t['tid']} 回修 {max_rounds} 輪未通過。\n"
                           f"{unresolved_report(unresolved)}", 3)
    except (KeyboardInterrupt, SystemExit):
        # runner 被中斷(CHG-20260926-01 T2):共用啟動器已收掉派出去的行程群組。
        # 交接檔要寫實話——停在這裡、沒有打勾、部分成果留在工作樹——下一個 session
        # 第一個讀的就是它;留著「施工→測試→審查」會讓人以為那一輪還在跑。
        write_handshake(ctx.repo, ctx.chg_id,
                        f"{ctx.chg_id} {t['tid']} 施工階段 runner 被中斷;未打勾",
                        "重跑 build/run 從本 task 繼續;未提交的部分成果留在工作樹")
        raise
    return die(f"{t['tid']} 未通過施工/審查迴圈", 1)


# 整支審查附的 CHG 文字上限(CHG-20260926-01 T9,#6)。c5af2cc 截在 4000 字,常常截在
# Global Constraints 之前;超過上限時照實標註,並指回 CHG 的絕對路徑。
BRANCH_CHG_LIMIT = 30000


def branch_brief(ctx, threshold: int = PAN.DEFAULT_THRESHOLD) -> str:
    """整支 branch 審查的 brief:CHG 絕對路徑、task 的變更在哪、至多 BRANCH_CHG_LIMIT 字的
    CHG 文字(經 quote_lines),最後是與逐 task 審查同一份回答契約(target = branch)。"""
    path = ctx.chg_path.resolve()
    text = ctx.text
    cut = ""
    if len(text) > BRANCH_CHG_LIMIT:
        cut = (f"\n(CHG 全文 {len(text)} 字,這裡只附前 {BRANCH_CHG_LIMIT} 字;"
               f"其餘請直接讀 {path})\n")
        text = text[:BRANCH_CHG_LIMIT]
    return ("唯讀審查整條 branch 的 diff(不是單一 task)。逐項對照 CHG 的使用者故事與全域約束。\n"
            f"CHG 檔案:{path}\n"
            f"每個 task 的 commit 訊息以 `{ctx.chg_id}: T<n>` 開頭"
            f"(`git log --grep \"{ctx.chg_id}: T\"`);以 `--no-commit` 執行時,變更在工作樹裡"
            "(`git status --porcelain --untracked-files=all` 與 `git diff HEAD`)。\n\n"
            "## CHG(原文;行首長得像判定行的,前面加了 `| `)\n"
            f"{quote_lines(text)}\n{cut}"
            + PAN.answer_contract("branch", threshold=threshold))


def role_review(ctx) -> int:
    """整支 branch review(逐 task review 內含於 build)。

    與逐 task 審查同一套判讀與裁決(CHG-20260926-01 T9,NEW-6):`read_answer` 讀回答——
    非零退出、沒有點名 branch 的合格判定行、判定互相矛盾都不是回答;`adjudicate([v])` 裁決——
    唯一的審查者判 cannot-verify,就是「全席 cannot-verify」,與 spec / quality fail 一樣停下。
    `--confidence-threshold` 在這裡套用;面板旗標不掛在 review 上(代決 R2)。
    """
    bad = require_valid_plan(ctx) or require_all_built(ctx, "整支 review")
    if bad is not None:
        return bad
    if ctx.args.dry_run:
        print("[stage] 整支 review:[dry-run] 模擬 [task-review] branch | spec: pass | quality: pass")
        return 0
    # 這一段原本只印一行字就 return 0——停點矩陣列了一整欄的關卡,程式上是 no-op。
    # 現在真的呼叫審查指令並解析判定行(CHG-20260803-02 T6)。
    cmd = getattr(ctx.args, "review_cmd", None) or getattr(ctx.args, "agent_cmd", None)
    if not cmd:
        return die("整支 review 需要 --review-cmd(或 --agent-cmd):此關卡不再是 no-op,"
                   "必須真的有人/有模型看過整條 branch diff", 3)
    threshold = int(getattr(ctx.args, "confidence_threshold", None) or PAN.DEFAULT_THRESHOLD)
    print("[stage] 整支 review:對整條 branch diff 實際審查中")
    rv = agent_call(cmd, branch_brief(ctx, threshold), ctx.repo)
    v, why = PAN.read_answer(rv.returncode, rv.stdout, rv.stderr, "branch")
    if v is None:
        return die("整支 review 沒有回答(無判定行、非零退出或判定互相矛盾)——"
                   f"無輸出不得視為通過(綠燈必須是掙來的):\n  {why}", 3)
    ok, msg = PAN.adjudicate([v], threshold)
    if not ok:
        return die(f"整支 review 未通過:{v['line']}\n{msg}", 3)
    print(f"整支 review 判定:{v['line']}(請入 ACC 證據欄)\n{msg}")
    return 0


def role_verify(ctx) -> int:
    """實際操作驗收:把整個變更真的跑一次。"""
    # feature 路徑的格式排在最前(CHG-20260926-01 T4):早於任何會提早 return 的判斷,
    # 也早於 dry-run——c5af2cc 的 `verify --dry-run` 照樣把路徑交給 shell。
    bad = (require_valid_plan(ctx) or require_safe_feature_paths(ctx, every_chg=True)
           or require_all_built(ctx, "實際操作驗收") or require_operational_declared(ctx))
    if bad is not None:
        return bad
    a = ctx.args
    # 委派型驗證(CHG-20260803-06):型別 / 覆蓋率 / SAST / 相依漏洞。
    if Q.required(ctx.text):
        qexempt, qreason = Q.exemption(ctx.text)
        if qexempt and qreason:
            print(f"[stage] 委派驗證:已豁免({qreason})——須記入 ACC")
        elif qexempt:
            return die("`Quality-checks: n/a` 沒有寫理由——空豁免與沒宣告等價", 3)
        else:
            qkinds = Q.load_kinds(getattr(a, "quality_kinds", None))
            qbase, qberr = QJ.load_baseline(
                getattr(a, "quality_baseline", None) or QJ.DEFAULT_BASELINE)
            if qberr:
                return die(f"委派驗證的基線無法使用:{qberr}", 3)
            qstatus, qmsg = Q.run_gate(ctx.repo, ctx.text, qkinds,
                                       getattr(a, "trust_chg_commands", False), qbase)
            if qstatus == "ok":
                print(f"[stage] {qmsg}")
            elif qstatus == "halt":
                return die(qmsg, 3)
            elif ctx.risk == "low":
                print(f"⚠️ {qmsg}(低風險 → 放行,須記入 ACC 的未涵蓋欄)")
            else:
                return die(qmsg + f"\n  {ctx.risk} 風險不接受「工具不存在」作為放行理由。", 3)

    op_act = ctx.action("operational_verify")
    if P.DOCS_ONLY_RE.search(ctx.text) and not P.AOP_RE.search(ctx.text):
        print("[stage] 實際操作驗收:docs-only 宣告,略過")
        return 0
    # Gherkin 閘(CHG-20260803-02 T5):CHG 宣告了行為規格就必須真的跑過。
    # 缺 behave 時 **halt 並指出安裝方式**,不靜默略過——「沒跑」與「通過」若都是綠,這層等於不存在。
    for spec in P.bspec_paths(ctx.text):
        gcmd = getattr(a, "gherkin_cmd", None) or "behave --strict"
        gr = run_shell(f"{gcmd} {spec}", ctx.repo)
        if gr.returncode != 0:
            tail = (gr.stdout or gr.stderr or "").strip().splitlines()[-6:]
            hint = ("\n  (找不到 behave? pip install behave)"
                    if "not recognized" in (gr.stderr or "") or gr.returncode in (1, 9009, 127)
                    and not tail else "")
            return die(f"行為規格未通過:{spec}\n  " + "\n  ".join(tail) + hint, 3)
        print(f"[stage] 行為規格通過:{spec}")

    # 非功能性驗證閘(CHG-20260804-02):效能 / 負載 / 併發 / 資源 / 可重現 /
    # 授權 / API 合約 / 視覺回歸 / 屬性測試——**依專案型態調用**。
    # 對一個 CLI/library repo 課負載測試的稅,會讓人把整套關掉;
    # 所以這裡有第三種狀態「不適用」,而它與「未涵蓋」和「通過」都不同。
    if PRO.required(ctx.text):
        nprofiles, nperr = PRO.load_profile(ctx.repo)
        if nperr:
            return die(f"專案型態宣告無法使用:{nperr}", 3)
        nkinds = PRO.load_kinds(getattr(a, "nonfunctional_kinds", None))
        nbase, nberr = QJ.load_baseline(
            getattr(a, "quality_baseline", None) or QJ.DEFAULT_BASELINE)
        if nberr:
            return die(f"委派驗證的基線無法使用:{nberr}", 3)
        nstatus, nmsg = PRO.run_gate(ctx.repo, ctx.text, nkinds, nprofiles,
                                     getattr(a, "trust_chg_commands", False), nbase)
        if nstatus == "ok":
            print(f"[stage] {nmsg}")
        elif nstatus == "not-applicable":
            # 不適用是永久結論,不是通過——訊息刻意不含肯定式的通過措辭
            print(f"[stage] {nmsg}")
        elif nstatus == "halt":
            return die(nmsg, 3)
        elif ctx.risk == "low":
            print(f"⚠️ {nmsg}(低風險 → 放行,須記入 ACC 的未涵蓋欄)")
        else:
            return die(f"非功能性驗證未涵蓋,而本變更為 {ctx.risk} 風險 → 停下交人。\n{nmsg}", 3)

    # 互動驗證閘(CHG-20260803-03 T4-T6):行為規格之後、操作驗收之前。
    # 放在 verify 而非 build 的理由——互動驗證要整個系統跑起來,
    # 逐 task 起一次瀏覽器既慢又沒有意義。
    if INT.required(ctx.text):
        exempted, reason = INT.exemption(ctx.text)
        if exempted and reason:
            print(f"[stage] 互動驗證:已豁免({reason})——須記入 ACC")
        else:
            kinds = INT.load_kinds(getattr(a, "interaction_kinds", None))
            status, msg = INT.run_gate(ctx.repo, ctx.text, kinds,
                                       getattr(a, "interaction_cmd", None),
                                       getattr(a, "trust_chg_commands", False))
            if status == "ok":
                print(f"[stage] {msg}")
            elif status == "halt":
                return die(msg, 3)
            else:
                # 驅動不存在:依風險分級處置(使用者裁示 Q4c)。
                # 環境限制是真的(CI 無頭、伺服器無 GUI),硬擋會讓 skill 在那些環境不可用;
                # 但「會被重複使用」本身意味著錯誤被放大 —— 故用既有風險分級分派,不發明新判準。
                if ctx.risk == "low":
                    print(f"⚠️ 互動驗證**未涵蓋**(低風險 → 放行,須記入 ACC 的未涵蓋欄):\n{msg}")
                    write_handshake(ctx.repo, ctx.chg_id, f"{ctx.chg_id} 互動驗證未涵蓋",
                                    "收尾時於 ACC 註明未涵蓋原因與補驗計畫",
                                    dry_run=ctx.dry_run)
                else:
                    return die(f"互動驗證無法執行,而本變更為 {ctx.risk} 風險 → 停下交人。\n{msg}\n"
                               f"  低風險才允許以「未涵蓋」放行;會被重複使用的中/高風險產出不行。", 3)
    if a.dry_run:
        print(f"[dry-run] 實際操作驗收=模擬 operate/observe/pass 通過(op_act={op_act})")
        return 0
    if op_act == "auto" and getattr(a, "verify_cmd", None):
        vr = run_shell(a.verify_cmd, ctx.repo)
        if vr.returncode != 0:
            return die(f"實際操作驗收失敗(--verify-cmd 非零):{vr.stderr.strip()[:200] or vr.stdout.strip()[:200]}", 3)
        print(f"[stage] 實際操作驗收:--verify-cmd 通過\n{vr.stdout.strip()[:300]}")
        return 0
    m = P.AOP_RE.search(ctx.text)
    if m is not None:
        # 不變式:走到這裡代表 require_operational_declared 已通過,AOP 節必然存在。
        # 但那條不變式只寫在別的函式裡——把它在這裡表達出來,否則型別上仍是 None 可達,
        # 而「不可達」與「忘了處理」在程式碼上長得一模一樣。
        print(ctx.text[m.start():m.start() + 600])
    reason = "高風險:操作簽核須由人執行" if op_act == "halt" else "無 --verify-cmd(人在迴圈)"
    return die(f"實際操作驗收({reason}):請依上方 operate/observe/pass 實際操作、記錄證據入 ACC,再續 merge", 3)


def _local_gate(ctx) -> int | None:
    """合併前先在本機跑一次自主檢查(CHG-20260806-04)。僅限程式類。

    三個停點都是 exit 3(合法停點、交人),不是錯誤:
      · CHG 宣告了指令但未明示信任 —— 內容驅動執行,沿用 CHG-20260803-05 的線
      · 找不到任何載具 —— fail-closed;合併是單向門,查不到即不准合併(KN-004)
      · 跑了但沒過 —— 就是它該擋的東西
    """
    a = ctx.args
    if getattr(a, "dry_run", False):
        return None
    if not LG.required(ctx.text):
        return None
    if getattr(a, "allow_no_local_gate", False):
        print("⚠️ --allow-no-local-gate:合併前的本機自主檢查**沒有跑**——"
              "那不是通過。請在 ACC 說明為何本機跑不動。")
        write_handshake(ctx.repo, ctx.chg_id, f"{ctx.chg_id} 以 --allow-no-local-gate 合併",
                        "收尾時須於 ACC 說明為何略過本機自主檢查", dry_run=ctx.dry_run)
        return None
    cmd, source, blocked = LG.resolve(
        ctx.text, ctx.repo,
        getattr(a, "local_gate_cmd", None),
        bool(getattr(a, "trust_chg_commands", False)))
    if blocked:
        return die(f"本機自主檢查閘({source}):{blocked}", 3)
    if not cmd:
        # 到得了這裡代表 resolve 回了「沒擋下、也沒給指令」——那是矛盾的狀態。
        # 不是為了讓型別檢查閉嘴才寫這一行:少了它,None 會被丟進 run_shell,
        # 而那會炸在別的地方,長得像 shell 的問題(KN-003:紅得不對)。
        return die(f"本機自主檢查閘({source}):既沒有指令也沒有擋下理由——"
                   "這是 resolve 的內部矛盾,請回報", 1)
    # 執行前一律把指令原文印出來——比照互動閘,操作者要看得見自己批准了什麼
    print(f"[stage] 本機自主檢查({source}):{cmd}")
    r = run_shell(cmd, ctx.repo)
    if r.returncode != 0:
        return die(f"本機自主檢查未通過(退出碼 {r.returncode})——"
                   f"合併前它就該擋在這裡,而不是合併之後才發現。\n"
                   f"    指令:{cmd}\n"
                   f"    修好之後重跑 accept;真的跑不動請用 --allow-no-local-gate(會留痕)", 3)
    print("[stage] 本機自主檢查通過")
    return None


def role_accept(ctx) -> int:
    """驗收 + PR/merge 閘。前置:全 task 完成 + 操作驗收已過(--verified 或本輪 verify 成功)。"""
    bad = require_valid_plan(ctx) or require_all_built(ctx, "驗收")
    if bad is not None:
        return bad
    a = ctx.args
    if not (getattr(a, "verified", False) or a.dry_run):
        return die("驗收前置條件不足:未見操作驗收通過——先跑 `verify`(或以 --verified 聲明已人工完成並記錄證據入 ACC)。"
                   "拆指令非治理繞道。", 3)
    # 本機自主檢查閘(CHG-20260806-04;位置經 -06、-08 兩次修正)。
    #
    # 它必須排在**所有會提早 return 的風險停點之前**——不只是 merge,還有 acceptance。
    # 三次的位置與涵蓋:
    #   -04:接在 merge 停點之後  → 只有 low 跑得到
    #   -06:移到 merge 停點之前  → low + medium(high 停在更前面的 acceptance)
    #   -08:移到 acceptance 之前 → low + medium + high
    # 每一次都比前一次更前面一格,而每一次我都以為那是最後一格。
    # 真正的判準寫成了斷言(test_gates_wired:須排在所有風險停點之前),不再靠記性。
    #
    # 仍在 `--verified` 前置**之後**:操作驗收都還沒過就跑本機檢查,順序沒有意義。
    #
    # 順序的語意:halt 是「交人」,而交人之前應該先把機器查得到的都查完。
    # 人接手時拿到的是「本機已綠、只差你」,不是「什麼都還沒查」。
    bad = _local_gate(ctx)
    if bad is not None:
        return bad

    act = ctx.action("acceptance")
    if act == "halt_independent":
        return die("驗收(高風險):需獨立驗收者(≠實作者)產 ACC——autopilot 停,交人/獨立 agent", 3)
    print("[stage] 驗收:依 acceptance-verification 產 ACC(task 判定行=證據欄)")
    act = ctx.action("merge")
    if act != "auto":
        return die(f"merge({ctx.risk} 風險 → {act}):PR 已備,合併由人執行"
                   "(本機自主檢查已通過)", 3)

    # CI 閘(CHG-20260803-04):合併前 CI 必須**全數完成且為綠**。
    # 實際踩過:windows job 還 pending 時就按了合併——其他三項已打勾,看起來就像好了。
    # 這裡 fail-closed(與哨兵的 fail-open 相反):合併難以回收,查不到狀態即不准合併。
    if not a.dry_run:
        ok, msg = CI.check(ctx.repo, getattr(a, "pr", None),
                           getattr(a, "ci_cmd", None), getattr(a, "allow_no_ci", False))
        print(f"[stage] {msg}" if ok else "")
        if not ok:
            return die(msg, 3)
        if getattr(a, "allow_no_ci", False):
            write_handshake(ctx.repo, ctx.chg_id, f"{ctx.chg_id} 以 --allow-no-ci 合併",
                            "收尾時於 ACC 說明為何此專案無 CI", dry_run=ctx.dry_run)
    if a.dry_run:
        print("[dry-run] PR+merge=模擬完成")
    elif shutil.which("gh"):
        print("[stage] PR/merge:gh 可用且 CI 全綠——可執行 gh pr merge(帶 CHG 編號)")
    else:
        return die("無 gh CLI:請人工開 PR 並 merge(commit 已帶 CHG 編號)", 3)
    write_handshake(ctx.repo, ctx.chg_id, f"{ctx.chg_id} 全 task 完成",
                    "收尾:ACC/Commit-PR 回填/重複性檢查/knowledge", dry_run=ctx.dry_run)
    print(f"✅ {ctx.chg_id}:{len(ctx.tasks)} task 全數完成;收尾提醒=CHG 狀態+Commit/PR+重複性檢查欄+knowledge。")
    return 0


def role_run(ctx) -> int:
    """組合:build(全 task)→ 整支 review → 操作驗收 → 驗收/PR/merge。"""
    for step in (role_build, role_review, role_verify):
        rc = step(ctx)
        if rc != 0:
            return rc
        ctx.reload()
    ctx.args.verified = True  # 本輪 verify 已通過(組合流程內部授權,非繞道)
    return role_accept(ctx)


def role_status(ctx) -> int:
    print(f"CHG:{ctx.chg_id} | 風險:{ctx.risk}")
    nxt = ctx.todo[0]["tid"] + ". " + ctx.todo[0]["title"] if ctx.todo else "(無——進入收尾)"
    print(f"已完成 {len(ctx.done)}/{len(ctx.tasks)};下一個:{nxt}")
    return 0
