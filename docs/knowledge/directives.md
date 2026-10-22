# 修正指示(DIR)

> 索引在 `docs/knowledge/INDEX.md`。同一規則只留一條現行版本;更新時改寫本條並把舊敘述移到「歷史」。

## DIR-1 — 審議會席位一律由本機邏輯模型(Ollama)擔任

- 日期 / 分支:2026-10-22(UTC+0)/ 全分支
- tags/scope:review, llm
- 情境:任何需要審議會(review-panel)的時候——CHG 決策審議、A1 代問企劃審議、逐 task review、末端審查。
- 規則:
  1. 審議席位由本機 Ollama 上的邏輯模型擔任(Qwen、GLM 等),**不派 Claude subagent 當席位**。派發者(主 agent)負責組派發包、呼叫模型、收判決、裁決;**核准權仍在使用者**(確認閘不轉包)。
  2. 呼叫方式:直接 `POST http://localhost:11434/api/chat`(`stream:false`、`keep_alive:0`、`num_ctx` ≥ 16384,派發包大時用 32768)。MCP `reason` 工具只能選到兩個模型,不夠分席,不用它。
  3. **一次只跑一席**(24 GB 記憶體):每席跑完以 `keep_alive:0` 卸載,再派下一席。
  4. 判決必須是一行合格的 `[verdict] <席位> | <model> | pass/fail/concern | <證據指向> | <理由>`;**格式不合格就等於「沒有回答」**→ 重派一次,仍不合格就交給使用者。建議以 Ollama `format`(JSON schema)強制結構,再由派發者轉成判決行;`<model>` 欄由派發者填入實際模型名,不信任模型自填。
  5. 席位分配(依 2026-10-22 實測調整;詳見下方證據):

     | 席位 | 模型 | 備註 |
     |------|------|------|
     | risk(否決席) | `hf.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M`,`think:true` | 高風險 CHG 改用 `hf.co/HauhauCS/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF:IQ3_M`(慢,可能 >10 分鐘) |
     | security(否決席) | `qwen3:8b`,`think:true` | |
     | compliance(否決席) | `huihui_ai/qwen3-abliterated:8b`,`think:true` | 未實測;第一次使用時先跑對照題 |
     | impact | `glm4:latest` | 非否決席 |
     | drift | `qwen3:8b`,`think:true` | |
     | consistency | `glm4:latest` | 非否決席 |

  6. **glm4 不得擔任否決席**(risk / security / compliance)。
  7. 不用作席位:`Llama-Breeze2-8B`(台灣用語潤飾,不是邏輯模型)、`qwen2.5-coder:7b`(CLI 專用,隨 CLI 棄用)。
  8. 限制要寫進每次審議記錄:目前只有 Qwen、GLM 兩個模型家族,席位間會共享盲點;本機 8–9B 模型的判斷力低於雲端大模型——**否決席 pass 不能取代使用者在確認閘的判斷**。
- 原因:使用者指示(2026-10-22):「審議交由 qwen, glm 等可用的本地邏輯模型」。推得的理由:與本專案「不送資料出本機」一致,並讓席位跨模型分散。
- 證據(2026-10-22 對照題:CHG 把 Gradio `server_name` 由 `127.0.0.1` 改 `0.0.0.0`,依 Guideline §5 應判 fail):
  - `qwen3:8b`(think)→ fail,理由正確;`<model>` 欄填錯
  - `Qwen3.5-9B Aggressive`(think)→ fail,理由正確;`<model>` 欄填錯
  - `glm4:latest`(同一份 NFR 脈絡)→ **concern**,而且判決行格式壞掉(把模板佔位字原樣輸出);沒給 NFR 脈絡時 → **pass**
- 信度:使用者確認(席位分配表與第 4、6 條為 agent 依實測推得,使用者可推翻)
- 狀態:生效
