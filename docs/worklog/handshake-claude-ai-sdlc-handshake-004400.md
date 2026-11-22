# 進場握手 — claude(worktree `ai-sdlc-handshake-004400`)

```
[handshake] branch: claude/ai-sdlc-handshake-004400(自 main 8ac06a3)| role: 主 agent(派發者,DIR-3)(RW: 僅本 worktree;docs/ 帳本可寫,程式須先有 CHG)
toolchain: NOT_RUN — plugin 探測找不到 requirements-dev.txt / pyproject.toml(本 repo 依 Guideline §8 以 PEP 723 檔頭宣告依賴,探測的宣告來源與本 repo 慣例不符);repo 自有載具 scripts/check.sh 判定為 BLOCKED — 本機無 uv(PATH 與 ~/.local/bin 皆無);python3 3.14.2 可用;未佈建(安裝 uv 需下載,待使用者決定)
worktree: clean;commits since e3289d7(ACC-20261102-02 收尾):all governed(doc_integrity_check --commits-since 通過,僅 8ac06a3 一筆,引用 CHG-20261103-01)
baseline: degraded(本分支無 upstream)——手動補對:git fetch 後 HEAD..origin/main 為空,與 origin/main 同步
current Guideline: v1.17;unclosed: CHG-20261103-01(高風險、Autonomy: halt)——確認閘已核准,草案 PR #150 第 2 輪統整判定 merge、已於 2026-11-03T06:30:31Z 合入 main;T1、T2 未開工,整合分支 chg/CHG-20261103-01-impl 尚未建立,無 ACC
must follow (knowledge): DIR-1~7(審議席位、每步 PR/CI、主 agent 只派發、整合分支批次審議、統整席判定、威脅模型)、KN-1(CHG 不寫狀態觸發字)、KN-2(收尾首行寫「已收尾」)
next I will: 等使用者指示;若續作 CHG-20261103-01,先在 CHG 補記草案 PR 第 2 輪結果與合入,再由 main 建整合分支、派 T1/T2;halt: 程式 task PR 合入與合併 PR merge 皆停下等使用者核准
```

## 對帳發現

- 帳本落後:CHG-20261103-01「## 審議」只記到第 1 輪 fix;「## 狀態」仍寫「待草案 PR 審議」。實際上第 2 輪統整判定可合併(head `7f05b2d`),PR #150 已合入(squash 為 main `8ac06a3`)。需在續作時補記。
- 結構文件:doc_integrity_check 結構同步通過;`value_checks.py`、`scripts/ci/gitutil.py` 尚不存在,與 T1/T2 未開工一致,無漂移。
- `docs/coordination.md` 不存在:單人模式,無 claim。`docs/.onboarding` 不存在:導入已完成。
- Skill 版本自檢:最近紀錄 CHG `ai-sdlc v1.66`、ACC `ai-sdlc v1.68.1`;執行中 skill 1.69.1(plugin 1.68.1)——紀錄較舊,無須升級。

## 工具鏈重探(2026-11-22,使用者已裝 uv 0.13.9)

- Windows 原生跑 `scripts/check.sh`(main 8ac06a3,未改任何程式):
  - 全部 tests/:收集階段 22 個錯誤——`settings.py:89 import fcntl`(20 筆)、`pwd`(2 筆)在 Windows 不存在;產品目標平台是 macOS,CI 是 ubuntu-latest。
  - 只跑 tests/ci:12 failed / 192 passed / 7 skipped;失敗集中在符號連結與路徑(deploy_local、review_gate、review_decision、plan_check_changed)。
- 判定:BLOCKED——本機基準即非全綠,無法以 TDD 分辨 T1/T2 造成的回歸;CHG-20261103-01 的 T1 改 `settings.py`、`project.py`,正好落在 Windows 匯入不了的模組。
- 可用替代:WSL Ubuntu(WSL2)有 git、python3,無 uv。

## WSL 基準(2026-11-22,使用者選 WSL)

- 佈建:WSL Ubuntu 以 astral 官方安裝程式裝 uv 0.13.9(`~/.local/bin/uv`);由本機 repo clone 到 `~/work/podcast-studio`,detach 在 main `8ac06a3`。
- `./scripts/check.sh`:1 failed / 1717 passed / 16 skipped(143 秒)。
- 失敗:`tests/test_project.py::test_eleventh_save_keeps_10_history`,連跑 8 次 8 次失敗(本機決定性失敗,不是偶發)。main 上 CI(ubuntu)同一 commit 為 success。
- 根因(〔推論〕,未改程式):版本 id = `_stamp()`(毫秒)+ `_rand4()`(隨機 4 位 hex),`list_history` 依檔名排序;同一毫秒內多次存檔時排序由隨機尾碼決定,不是存檔順序。後果不只測試紅燈:保留最近 10 版的修剪可能刪掉較新的版本(資料誤刪,屬 DIR-7 範圍內)。
- 影響:CHG-20261103-01 的 T1 test (2)、T2 test (2) 與「### 本機閘」都要求 `UV_OFFLINE=1 ./scripts/check.sh` 全過;本機達不到。這是 main 既有缺陷,不得在 CHG-20261103-01 順手修。

## 即時狀態

branch/role/scope: docs/CHG-20261122-01-draft / 主 agent / CHG-20261122-01 草案審議;CHG-20261122-02 草案
doing:
- CHG-20261122-01(PR #151,head c4e936a):六席意見已貼(risk fail;security、consistency pass;impact、drift 本機無 Ollama、compliance 參考資料超過 Opus 單份上限 → no-answer);DIR-6 統整判定 fix 已貼。修項 F-1 改判高 + Autonomy halt、F-2 安全影響 irreversible-delete 改「是(有限)」、F-3 風險 (2) 措辭、F-4 回滾說明、F-5 依使用者決定改寫確認閘說法。needs_user:改判高後確認閘是否仍由派發者自動進行。
- CHG-20261122-02(draft PR #152,架構調整 Rust 原生 GUI,風險高):草案在 scratchpad clone 完成並以 gh 推送;CI program 失敗(§5 超過審議參考資料 3000 字)已修為 §6.1,head 3a3c40b;確認閘待使用者逐條覆核(代決 1~26、Q5~Q12)。
- 審議在 `outputs/review-main`(worktree 內 main 的 clone)執行,因 hook 禁止寫入主工作副本;主工作副本 `outputs/.tmp/local_review/opus/` 殘留第一次中止的兩個請求檔(32580-…),待人工刪除。
- 更新:使用者答「仍由你自動進行」→ 第 1 輪修正 1c0a6bb → 第 2 輪統整 merge → PR #151 squash 合入 main 4646b57。整合分支 chg/CHG-20261122-01-impl @4646b57。T1(PR #153,head 73d4596)施工者與派發者在 WSL 各自驗證全綠(1731 passed / 0 failed)。
next: 等使用者核准 T1 PR #153 合入整合分支;核准後把核准原話寫進 CHG「## 派發記錄」並 commit(DIR-5 第 8 條),再 merge,接著 DIR-5 第 2~4 條審議與合併 PR
last-updated: 2026-11-22 12:30(UTC+0)
last-updated: 2026-11-22 00:00(UTC+0)
