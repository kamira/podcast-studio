# Knowledge INDEX(讀這裡,不是整份檔)

> 單檔模式。條目超過約 30 條時改拆檔(`docs/knowledge/entries/`),INDEX 改由 `knowledge_index.py` 生成。
> tags 必須先註冊在 `vocabulary.json`。

| id | tier | tags/scope | 一句規則 | 狀態 |
|----|------|-----------|----------|------|
| [DIR-1](directives.md#dir-1--審議會席位一律由本機邏輯模型ollama擔任) | 使用者確認 | 全分支 · review, llm | 審議席位由本機 Ollama 邏輯模型擔任,序列一次一席;各席為意見、不具否決效力(合併由統整席判定,DIR-6);glm4 不擔任 risk / security / compliance 席;security、compliance 例外由 Codex(read-only、雲端,使用者 2026-10-23 決定)擔任;送審 CHG 去除審議紀錄;27B 席超過 30 分鐘沒有回答改派 Codex(使用者 2026-10-23 規則),其餘 15 分鐘逾時重派一次;27B 暫停(高風險 risk 改 Codex,須帶 --codex-seats);Codex 擔任的席位暫由每席一個全新 Opus medium 子代理代答,至使用者明示恢復(第 12 條) | 生效 |
| [DIR-2](directives.md#dir-2--每個步驟都走-commit--push--pr--ci--merge) | 使用者確認 | 全分支 · cicd, review | 每步驟分支→PR→CI 綠→merge;只有純 docs PR 自動 merge,程式變更一律等使用者核准 | 生效 |
| [DIR-3](directives.md#dir-3--主-agentclaude專注派發實作與審查交給工作者) | 使用者確認 | 全分支 · review, llm, interaction | 主 agent 只派發;本機模型優先,不合格改派 Claude subagent(CHG / ACC 治理文件草案、中高風險程式 task 可直接派 Claude subagent) | 生效 |
| [DIR-4](directives.md#dir-4--預授權已過確認閘的-chg統整判定可合併ci-全綠即由派發者-merge) | 使用者確認 | 全分支 · cicd, review | 已過確認閘的 CHG:統整席判定 merge、CI 全綠即自行 merge;已 merge 功能的修正另開分支;出事自動失效 | 生效 |
| [DIR-5](directives.md#dir-5--批次審議全部-task-完成後逐一審議單一模型統整開發模型一次修正) | 使用者確認 | 全分支 · cicd, review | task PR 先合進整合分支;全部完成後逐一審議,單一模型統整,開發模型一次修正,再以一個 PR 合進 main(合併 PR 依統整席判定,DIR-6) | 生效 |
| [DIR-6](directives.md#dir-6--審議席提供意見單一獨立統整席判定可否合併) | 使用者確認 | 全分支 · cicd, review, llm | 六席只提供意見(fail、no-answer 不擋);派獨立 Opus medium 子代理統整並判定 merge / fix,以 review_decision.py 貼出;CI review 看該判定;連續兩輪 fix 交使用者 | 生效 |
| [KN-1](patterns.md#kn-1--chg-全文不得出現-doc_integrity_check-的狀態觸發字) | shallow | 全分支 · cicd, review | CHG 全文不寫已實作/已驗收/implemented 等觸發字與私鑰樣本行 | 觀察中(seen 1 / applied 1) |

_entries: 7 · 建立:2026-10-22(UTC+0)· 導入時零條目;DIR-1~3 於導入後加入_
