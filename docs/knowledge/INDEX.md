# Knowledge INDEX(讀這裡,不是整份檔)

> 單檔模式。條目超過約 30 條時改拆檔(`docs/knowledge/entries/`),INDEX 改由 `knowledge_index.py` 生成。
> tags 必須先註冊在 `vocabulary.json`。

| id | tier | tags/scope | 一句規則 | 狀態 |
|----|------|-----------|----------|------|
| [DIR-1](directives.md#dir-1--審議會席位一律由本機邏輯模型ollama擔任) | 使用者確認 | 全分支 · review, llm | 審議席位由本機 Ollama 邏輯模型擔任,序列一次一席;glm4 不當否決席;security、compliance 例外由 Codex(read-only、雲端,使用者 2026-10-23 決定)擔任;送審 CHG 去除審議紀錄;27B 席超過 30 分鐘沒有回答改派 Codex(使用者 2026-10-23 規則),其餘 15 分鐘逾時重派一次;27B 暫停(高風險 risk 改 Codex,須帶 --codex-seats) | 生效 |
| [DIR-2](directives.md#dir-2--每個步驟都走-commit--push--pr--ci--merge) | 使用者確認 | 全分支 · cicd, review | 每步驟分支→PR→CI 綠→merge;只有純 docs PR 自動 merge,程式變更一律等使用者核准 | 生效 |
| [DIR-3](directives.md#dir-3--主-agentclaude專注派發實作與審查交給工作者) | 使用者確認 | 全分支 · review, llm, interaction | 主 agent 只派發;本機模型優先,不合格改派 Claude subagent | 生效 |
| [DIR-4](directives.md#dir-4--預授權已過確認閘的-chg審議無否決ci-全綠即由派發者-merge) | 使用者確認 | 全分支 · cicd, review | 已過確認閘的 CHG:審議無否決、CI 全綠即自行 merge;已 merge 功能的修正另開分支;出事自動失效 | 生效 |
| [KN-1](patterns.md#kn-1--chg-全文不得出現-doc_integrity_check-的狀態觸發字) | shallow | 全分支 · cicd, review | CHG 全文不寫已實作/已驗收/implemented 等觸發字與私鑰樣本行 | 觀察中(seen 1 / applied 1) |

_entries: 5 · 建立:2026-10-22(UTC+0)· 導入時零條目;DIR-1~3 於導入後加入_
