# 修正指示(DIR)

> 索引在 `docs/knowledge/INDEX.md`。同一規則只留一條現行版本;更新時改寫本條並把舊敘述移到「歷史」。

## DIR-1 — 審議會席位一律由本機邏輯模型(Ollama)擔任

- 日期 / 分支:2026-10-22(UTC+0)/ 全分支
- tags/scope:review, llm
- 情境:任何需要審議會(review-panel)的時候——CHG 決策審議、A1 代問企劃審議、逐 task review、末端審查。各席提供意見,合併與否由統整席判定(DIR-6)。
- 規則:
  1. 審議席位由本機 Ollama 上的邏輯模型擔任(Qwen、GLM 等),**不派 Claude subagent 當意見席**;例外見第 9 條(security、compliance 由 Codex 擔任)與第 12 條(Codex 擔任的席位暫由 Opus 子代理代答);統整席由 Claude subagent 擔任(DIR-6)。派發者(主 agent)負責組派發包、呼叫模型、收意見、派統整席(DIR-6);**核准權仍在使用者**(確認閘不轉包)。
  2. 呼叫方式:直接 `POST http://localhost:11434/api/chat`(`stream:false`、`keep_alive:0`、`num_ctx` ≥ 16384,派發包大時用 32768)。MCP `reason` 工具只能選到兩個模型,不夠分席,不用它。
  3. **一次只跑一席**(24 GB 記憶體):每席跑完以 `keep_alive:0` 卸載,再派下一席。
  4. 判決必須是一行合格的 `[verdict] <席位> | <model> | pass/fail/concern | <證據指向> | <理由>`;**格式不合格就等於「沒有回答」**→ 重派一次,仍不合格就交給使用者。建議以 Ollama `format`(JSON schema)強制結構,再由派發者轉成判決行;`<model>` 欄由派發者填入實際模型名,不信任模型自填。第三欄為建議等級,不具否決效力(DIR-6)。
  5. 席位分配(依 2026-10-22 實測調整;詳見下方證據):

     | 席位 | 模型 | 備註 |
     |------|------|------|
     | risk(原否決席,現為意見席;glm4 不得擔任,見第 6 條) | 中 / 低風險:`hf.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M`,`think:true`;**高風險:Codex(codex-cli,read-only sandbox,雲端)** | 2026-10-23 使用者指示「目前先暫不使用27B模型審議」(第 10 條);27B 暫停期間 |
     | security(原否決席,現為意見席;glm4 不得擔任,見第 6 條) | Codex(codex-cli,read-only sandbox,雲端) | 2026-10-23 使用者決定(第 9 條);以 `--codex-seats none` 改回 Ollama 時沿用舊配置:`qwen3:8b`,高風險 CHG 用 27B |
     | compliance(原否決席,現為意見席;glm4 不得擔任,見第 6 條) | Codex(codex-cli,read-only sandbox,雲端) | 2026-10-23 使用者決定(第 9 條);改回 Ollama 時用 `huihui_ai/qwen3-abliterated:8b`,`think:true` |
     | impact | `glm4:latest` | glm4 不擔任 risk / security / compliance(第 6 條) |
     | drift | `qwen3:8b`,`think:true` | |
     | consistency | `glm4:latest` | glm4 不擔任 risk / security / compliance(第 6 條) |

  6. **glm4 不得擔任 risk / security / compliance 席**。
  7. 不用作席位:`Llama-Breeze2-8B`(台灣用語潤飾,不是邏輯模型)、`qwen2.5-coder:7b`(CLI 專用,隨 CLI 棄用)。
  8. 限制要寫進每次審議記錄:目前只有 Qwen、GLM 兩個模型家族,席位間會共享盲點;本機 8–9B 模型的判斷力低於雲端大模型——**意見席的建議與統整席的判定都不能取代使用者在確認閘的判斷**。
  9. **Codex 席位(2026-10-23 使用者指示「使用 codex 協助」)**:
     - security、compliance 兩席由 Codex(`codex exec`)擔任;`scripts/local_review.py` 以 `--codex-seats <逗號清單>` 覆寫,`--codex-seats none` 表示全部用 Ollama。
     - Codex 是雲端模型,送審資料(PR、CHG、diff、參考文件)會送出本機;這是第 1 條「本機模型」原則與「不送資料出本機」的**使用者明示例外**,前提是 repo 已公開。例外只限審議席位,不擴及其他用途。
     - Codex 一律只用 `-s read-only`;不得使用 `workspace-write`、`danger-full-access`、`--dangerously-bypass-approvals-and-sandbox`。prompt 由 stdin 傳入,判決以 `--output-schema`(與 Ollama 席同一份 JSON schema)強制;格式不合格、失敗或逾時(15 分鐘)= 沒有回答 → 重派一次,仍失敗記 `no-answer`。
     - 判決行與留言的 `<model>` 欄記 `codex-cli <版本>`(取自 `codex --version`),留言註明哪些席位由 Codex 擔任。
  10. **暫停 27B(2026-10-23 使用者指示,原話:「目前先暫不使用27B模型審議」)**:
     - 暫停期間任何審議席位一律不使用 `hf.co/HauhauCS/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF:IQ3_M`。
     - 原本由 27B 擔任的席位(高風險 CHG 的 risk 席)改由 Codex 擔任,與第 9 條同一套限制(read-only、prompt 經 stdin、記錄實際版本)。
     - 執行方式:`python3 scripts/local_review.py --pr <N> --codex-seats risk,security,compliance`;`scripts/local_review.py` 的預設值仍含 27B,**暫停期間不得省略此參數**。
     - 恢復須使用者明示;恢復時把本條移入「歷史」。
  11. **送審 CHG 去除審議紀錄**:送給所有席位(Ollama 與 Codex)的 CHG 刪除「## 審議」節(標題到下一個 `## ` 標題或檔尾),獨立階段不得看到前輪判決。統整席(DIR-6)不受此限。
  12. **Codex 額度用盡時由 Opus 代答(2026-10-28 使用者指示,原話:「codex 先暫時使用opus代答直到我說可以恢復code」;同日恢復 Codex,原話:「繼續處理 codex額度已恢復 若再次用完額度則採用opus」)**:
     - Codex 擔任的席位(risk、security、compliance)預設仍由 Codex 擔任(第 9 條)。Codex 因用量額度用盡而無法作答時,該次審議改由派發者每席各派一個全新、互不相通的 Opus(effort medium)子代理代答,留言模型標記註明代答(例:`claude-opus-medium(代 Codex,額度用盡)`)。
     - 代答時,意見席把待審資料交給雲端 Claude 屬資料外送例外,範圍同 DIR-6 第 10 條(本 repo PR 的待審資料)。
     - 本條是第 1 條「不派 Claude subagent 當意見席」與第 9 條(Codex 擔任)的條件式例外;代答的席位仍是意見席(DIR-6),統整席另派(DIR-6 第 2 條)。
- 原因:使用者指示(2026-10-22):「審議交由 qwen, glm 等可用的本地邏輯模型」。推得的理由:與本專案「不送資料出本機」一致,並讓席位跨模型分散。
- 證據(2026-10-22 對照題:CHG 把 Gradio `server_name` 由 `127.0.0.1` 改 `0.0.0.0`,依 Guideline §5 應判 fail):
  - `qwen3:8b`(think)→ fail,理由正確;`<model>` 欄填錯
  - `Qwen3.5-9B Aggressive`(think)→ fail,理由正確;`<model>` 欄填錯
  - `glm4:latest`(同一份 NFR 脈絡)→ **concern**,而且判決行格式壞掉(把模板佔位字原樣輸出);沒給 NFR 脈絡時 → **pass**
- 每席逾時:27B 30 分鐘;逾時改派 Codex(使用者 2026-10-23 規則,原話:「如果本地高階模型審議需要花超過30分鐘轉為codex」);其餘 15 分鐘、重派一次。審議一律**序列**執行,不並行(使用者指示 2026-10-22)。
  - 27B 席逾時,或累計超過 30 分鐘仍沒有回答,不再用 27B 重派,該席直接由 Codex(`-s read-only`、prompt 由 stdin)作答;Codex 也沒有回答時依第 9 條重派 Codex 一次,仍失敗記 `no-answer`。
  - 改派後判決行與留言的 `<model>` 欄記 `codex-cli <版本>(27B 逾時 30 分鐘改派)`,留言說明欄列出改派的席位。改派會把該席送審資料送往雲端,適用第 9 條的使用者明示例外。
- 證據補充(2026-10-22 CHG-20261022-01 / -03 實審):`qwen3:8b` 在約 3 萬字的 CHG 上擔任 security 席,兩筆共 9 點發現全部與送審原文矛盾(例:稱未檢查 GET 的 Host,原文第 255 行明文規定);`Qwen3.8-27B` 擔任 risk 席的判決與原文相符、證據具體。使用者因此決定高風險 security 席改用 27B。
- 證據補充(2026-10-23 PR #12 本機審議):security 席(27B,think)兩次都輸出截斷、沒有回答;compliance 席(8B)把送審 CHG 內的前輪審議紀錄當成本輪事實而判 fail。派發者以 `codex exec -s read-only --output-schema … -o …` 手動試跑:security 44 秒回 concern,證據指到 `.github/workflows/ci.yml` 的具體行號;compliance 回 pass。使用者因此指示「使用 codex 協助」,並決定 security、compliance 改由 Codex 擔任、送審 CHG 去除審議紀錄。
- 信度:使用者確認(席位分配表與第 4、6 條為 agent 依實測推得,使用者可推翻;高風險 security 席 27B、第 9 條 Codex 席位為使用者決定)
- 狀態:生效
- 歷史:
  - 2026-10-28 使用者恢復 Codex(原話:「繼續處理 codex額度已恢復 若再次用完額度則採用opus」),第 12 條由「暫由 Opus 代答至另行通知」改為「額度用盡時由 Opus 代答」。原敘述:
      12. **Codex 席暫由 Opus 代答(2026-10-28 使用者指示,原話:「codex 先暫時使用opus代答直到我說可以恢復code」;寫入本條經使用者 2026-10-28 核准,原話:「都照建議」)**:
         - Codex 擔任的席位(risk、security、compliance)暫由派發者每席各派一個全新、互不相通的 Opus(effort medium)子代理代答,留言模型標記註明代答(例:`claude-opus-medium(代 Codex,使用者 2026-10-28 指示至另行通知)`)。
         - 代答期間,意見席把待審資料交給雲端 Claude 屬資料外送例外,範圍同 DIR-6 第 10 條(本 repo PR 的待審資料)。
         - 本條是第 1 條「不派 Claude subagent 當意見席」與第 9 條(Codex 擔任)的暫行例外;代答的席位仍是意見席(DIR-6),統整席另派(DIR-6 第 2 條)。
         - 恢復須使用者明示恢復 Codex;恢復時把本條移入「歷史」。
  - 2026-10-28 依 CHG-20261028-02 統整報告 U-2(使用者原話:「都照建議」;代答指示原話:「codex 先暫時使用opus代答直到我說可以恢復code」)新增第 12 條、第 1 條補引用。原敘述:
    - 第 1 條(句首):審議席位由本機 Ollama 上的邏輯模型擔任(Qwen、GLM 等),**不派 Claude subagent 當意見席**;例外見第 9 條(security、compliance 由 Codex 擔任);統整席由 Claude subagent 擔任(DIR-6)。
  - 2026-10-28 依 CHG-20261028-02 DIR-5 統整報告修訂:M-1 移除互讀階段(使用者原話:「目前不需要交叉審議 因為有統一的最後審議席所以只要最後進行統整裁決即可」);M-3 現行條文去除「否決席」用語(統整報告 RS-7)。原敘述:
    - 每席逾時第 1 點:27B 席某一階段逾時,或累計超過 30 分鐘仍沒有回答,不再用 27B 重派,該席該階段直接由 Codex(`-s read-only`、prompt 由 stdin)作答;Codex 也沒有回答時依第 9 條重派 Codex 一次,仍失敗記 `no-answer`。改派每席每階段各自判定:第一階段改派,互讀階段仍先試 27B。
    - 每席逾時第 2 點:改派後判決行與留言的 `<model>` 欄記 `codex-cli <版本>(27B 逾時 30 分鐘改派)`,留言說明欄列出改派的席位與階段。改派會把該席送審資料送往雲端,適用第 9 條的使用者明示例外。
    - 第 5 條席位表 impact 列:| impact | `glm4:latest` | 非否決席 |
    - 第 5 條席位表 consistency 列:| consistency | `glm4:latest` | 非否決席 |
    - 第 9 條第 1 點:security、compliance 兩個否決席由 Codex(`codex exec`)擔任;`scripts/local_review.py` 以 `--codex-seats <逗號清單>` 覆寫,`--codex-seats none` 表示全部用 Ollama。
  - 2026-10-28 依 CHG-20261028-02(使用者原話:「改為審議席提供意見 最後統整為單一獨立審議席全部審議再決議如何修」)修訂。原敘述:
    - 情境:任何需要審議會(review-panel)的時候——CHG 決策審議、A1 代問企劃審議、逐 task review、末端審查。
    - 第 1 條:審議席位由本機 Ollama 上的邏輯模型擔任(Qwen、GLM 等),**不派 Claude subagent 當席位**;例外見第 9 條(security、compliance 由 Codex 擔任)。派發者(主 agent)負責組派發包、呼叫模型、收判決、裁決;**核准權仍在使用者**(確認閘不轉包)。
    - 第 4 條:判決必須是一行合格的 `[verdict] <席位> | <model> | pass/fail/concern | <證據指向> | <理由>`;**格式不合格就等於「沒有回答」**→ 重派一次,仍不合格就交給使用者。建議以 Ollama `format`(JSON schema)強制結構,再由派發者轉成判決行;`<model>` 欄由派發者填入實際模型名,不信任模型自填。
    - 第 5 條席位表 risk 列:| risk(否決席) | 中 / 低風險:`hf.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M`,`think:true`;**高風險:Codex(codex-cli,read-only sandbox,雲端)** | 2026-10-23 使用者指示「目前先暫不使用27B模型審議」(第 10 條);27B 暫停期間 |
    - 第 5 條席位表 security 列:| security(否決席) | Codex(codex-cli,read-only sandbox,雲端) | 2026-10-23 使用者決定(第 9 條);以 `--codex-seats none` 改回 Ollama 時沿用舊配置:`qwen3:8b`,高風險 CHG 用 27B |
    - 第 5 條席位表 compliance 列:| compliance(否決席) | Codex(codex-cli,read-only sandbox,雲端) | 2026-10-23 使用者決定(第 9 條);改回 Ollama 時用 `huihui_ai/qwen3-abliterated:8b`,`think:true` |
    - 第 6 條:**glm4 不得擔任否決席**(risk / security / compliance)。
    - 第 8 條:限制要寫進每次審議記錄:目前只有 Qwen、GLM 兩個模型家族,席位間會共享盲點;本機 8–9B 模型的判斷力低於雲端大模型——**否決席 pass 不能取代使用者在確認閘的判斷**。
    - 條次(本次重新編號):原規則有兩個「第 10 條」。「暫停 27B」維持第 10 條(產品程式 `settings.validate()` 的錯誤訊息與其測試引用此條次);「送審 CHG 去除審議紀錄」改為第 11 條,移到「暫停 27B」之後。依據:CHG-20261028-02 草案 PR #84 審議發現,派發者 2026-10-28 指示消除條次歧義。
    - 原第一個第 10 條(送審 CHG 去除審議紀錄,現第 11 條):**送審 CHG 去除審議紀錄**:送給所有席位(Ollama 與 Codex)的 CHG 刪除「## 審議」節(標題到下一個 `## ` 標題或檔尾),獨立階段不得看到前輪判決。
  - 2026-10-23 risk 席高風險改由 Codex(第 10 條,暫停 27B)取代。原席位表 risk 列:
    | risk(否決席) | `hf.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M`,`think:true` | 高風險 CHG 改用 `hf.co/HauhauCS/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF:IQ3_M`(慢,可能 >10 分鐘) |
  - 2026-10-23 由使用者規則取代(27B 30 分鐘改派 Codex)。原敘述:
    - 每席逾時:27B 45 分鐘、其餘 15 分鐘;逾時 = 沒有回答,重派一次。審議一律**序列**執行,不並行(使用者指示 2026-10-22)。
  - 2026-10-23 由使用者決定 security、compliance 改由 Codex 擔任(第 9 條,PR #15)取代。原敘述:
    - 第 1 條:審議席位由本機 Ollama 上的邏輯模型擔任(Qwen、GLM 等),**不派 Claude subagent 當席位**。派發者(主 agent)負責組派發包、呼叫模型、收判決、裁決;**核准權仍在使用者**(確認閘不轉包)。
    - 席位表 security 列:| security(否決席) | `qwen3:8b`,`think:true` | **高風險 CHG 改用 `hf.co/HauhauCS/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF:IQ3_M`**(2026-10-22 使用者決定;逾時 45 分鐘) |
    - 席位表 compliance 列:| compliance(否決席) | `huihui_ai/qwen3-abliterated:8b`,`think:true` | 未實測;第一次使用時先跑對照題 |
    - 信度:使用者確認(席位分配表與第 4、6 條為 agent 依實測推得,使用者可推翻;高風險 security 席 27B 為使用者決定)

## DIR-2 — 每個步驟都走 commit → push → PR → CI → merge

- 日期 / 分支:2026-10-22(UTC+0)/ 全分支
- tags/scope:cicd, review
- 情境:任何會進 repo 的步驟(帳本、CHG、程式、CI 設定本身)。
- 規則:
  1. 每個步驟開自己的分支(`chg/<CHG 編號>-<task>` 或 `docs/<主題>`),commit message 帶 CHG 編號,push,開 PR,CI 全綠後 merge 回 `main`。不直接 push `main`。
  2. CI 必須涵蓋三類:**document**(doc_integrity_check、knowledge INDEX 新鮮度)、**CHG**(plan-check、commit 引用 CHG 編號)、**program**(`./scripts/check.sh` 測試、語法與 lint)。
  3. merge 的停點(只准加嚴):**只有只改 `docs/`、`features/` 的 PR** 可在 CI 綠後自動 merge;**任何改到程式路徑的 PR,不論風險分級,merge 前都停下等使用者核准**;改到治理基礎設施(`.github/`、`scripts/ci/`、`tests/ci/`、`.ai-sdlc/`、`scripts/local_review.py`、`scripts/review_decision.py`、`scripts/deploy_local.sh`)另需擁有者 `infra-approved` 留言。
  4. 2026-10-22 收緊緣由:CHG-20261022-03 審議 security 席(27B)指出「自評低風險的程式變更可自動 merge 並部署到本機」,且 CI 檢查若從 PR head 執行可被 PR 自改。
- 原因:使用者指示(2026-10-22):「每次步驟都要 commit push pr merge 並且要設好完整 CICD(包含 document, chg, program)」。
- 信度:使用者確認(第 3 條的停點為治理層既有規則,agent 依 halt policy 補上)
- 狀態:生效(前置未完成:GitHub 遠端與 `gh` 尚未就緒)
- 歷史:
  - 2026-10-28 依 CHG-20261028-02 代決 19(使用者 2026-10-28「全部照草案核准」)第 3 條治理基礎設施清單加入 `scripts/review_decision.py`。原敘述:
    - 第 3 條:merge 的停點(只准加嚴):**只有只改 `docs/`、`features/` 的 PR** 可在 CI 綠後自動 merge;**任何改到程式路徑的 PR,不論風險分級,merge 前都停下等使用者核准**;改到治理基礎設施(`.github/`、`scripts/ci/`、`tests/ci/`、`.ai-sdlc/`、`scripts/local_review.py`、`scripts/deploy_local.sh`)另需擁有者 `infra-approved` 留言。

## DIR-3 — 主 agent(Claude)專注派發,實作與審查交給工作者

- 日期 / 分支:2026-10-22(UTC+0)/ 全分支
- tags/scope:review, llm, interaction
- 情境:CHG 的 task 施工、審查、文件草稿、CI 失敗分析。
- 規則:
  1. 主 agent 負責:拆 task、組派發包、派工、收結果、裁決、對使用者回報;**不親自寫實作**。
  2. 工作者優先順序:本機模型(Ollama)能勝任的先給本機模型——審議席位(DIR-1)、文件草稿、測試案例草稿、commit / PR 說明、CI 日誌初判;本機模型產出不合格(測試不過或格式不合)時,改派 Claude subagent,並在 CHG 記錄改派原因。**例外(2026-10-26 修訂)**:CHG、ACC 等治理文件草案(含修訂、驗收報告)可直接派 Claude subagent,不須先試本機模型,也不需記錄改派原因;已起草的 CHG-20261026-01 依此追認。**例外二(2026-10-26 修訂)**:中、高風險 CHG 的程式 task 可直接派 Claude subagent,不須先試本機模型,派發記錄寫明施工者即可;本機模型仍用於低風險 CHG 的 task 與審議席位(DIR-1)。已施工的中、高風險程式 task(CHG-20261022-01、CHG-20261026-01 等)依此追認。
  3. 本機模型的產出一律經測試或審查才採用,不直接信任。
- 原因:使用者指示(2026-10-22):「可以使用本地模型協助處理,claude 專注於派發工作」。
- 修訂(2026-10-26):第 2 條加入治理文件草案例外,使用者原話:「PR #51 拆成 T1、T2 兩個 PR(之後 T3、T4、T5 也各自一個 PR);DIR-3 第 2 條修訂為「CHG / ACC 等治理文件草案可直接派 Claude subagent,不須先試本機模型」,以我今天的指示為依據,並對已起草的 CHG-20261026-01 追認。」
- 修訂(2026-10-26,第二次):第 2 條加入中、高風險程式 task 例外,使用者原話:「DIR-3 第 2 條再加例外:中、高風險 CHG 的程式 task 可直接派 Claude subagent,不須先試本機模型(本機模型仍用於低風險與審議席);以我今天的指示為依據,並追認已施工的 task。」
- 信度:使用者確認(第 2 條的分工與改派規則為 agent 推得,使用者可推翻)
- 狀態:生效

## DIR-4 — 預授權:已過確認閘的 CHG,統整判定可合併、CI 全綠即由派發者 merge

- 日期 / 分支:2026-10-25(UTC+0)/ 全分支
- tags/scope:cicd, review
- 情境:屬於**已通過使用者確認閘**的 CHG 的 task PR、依審議修正的 PR、帳本 PR。
- 規則:
  1. 同時符合下列條件時,派發者不必再逐次請使用者核准,可自行貼 `infra-approved`(註明依 DIR-4)並 squash merge:
     - PR 對應的 CHG 已通過使用者確認閘;
     - 同一 head 已有六席意見留言,且統整席(DIR-6)對目前 head 判定 `merge`;
     - CI 四項(document、chg、program、review)全綠。
  2. **不適用**(仍須停下問使用者):新 CHG 或 CHG 修訂的確認閘;統整判定 `fix` 而派發者擬不修正直接 merge;統整判定列有 `needs_user` 事項;同一 PR 連續兩輪判定 `fix`;統整 JSON 兩次格式不合格;變更 GitHub ruleset、repo 安全設定或其他 repo 外設定;任何需要繞過 main 保護的操作;驗收(ACC)判定為未通過時的處置選擇。
  3. **功能需要修正**(已 merge 的功能事後發現要改):另開分支與 PR,不回頭改已 merge 的分支。
  4. 每次依本條 merge,在 PR 留言與 CHG「## 派發記錄」註明「依 DIR-4 預授權 merge」;使用次數計入治理健康度。
  5. **自動失效**:依本條 merge 的變更若事後被迴歸、驗收或使用者發現弄壞東西,本條立即失效,直到使用者重新授權。
- 取捨(派發者註記):CHG-20261022-03 安全影響 (j)(q) 以「程式變更一律使用者核准 merge」作為殘留風險的補償控制;本條移除這一層,補償控制改為「獨立統整席判定 merge(核對完整程式)+ CI 全綠 + 第 5 條自動失效」。原以 risk、security、compliance 三席無 fail 為條件的補償控制已由 DIR-6 取代(CHG-20261028-02)。
- 原因:使用者指示(2026-10-25,原話):「往後就直接該CHG沒問題就等CICD後直接merge,功能需要修正則另開branch」。
- 信度:使用者確認(適用條件與不適用清單為 agent 依預授權「必須寫窄」的規範補上,使用者可推翻)
- 狀態:生效
- 歷史:
  - 2026-10-28 依 CHG-20261028-02 DIR-5 統整報告 M-3(RS-7,現行條文去除「否決席」用語)修訂。原敘述:
    - 取捨(派發者註記)末句:原「否決席無 fail」的補償控制已由 DIR-6 取代(CHG-20261028-02)。
  - 2026-10-28 依 CHG-20261028-02(使用者原話:「改為審議席提供意見 最後統整為單一獨立審議席全部審議再決議如何修」)修訂。原敘述:
    - 標題:DIR-4 — 預授權:已過確認閘的 CHG,審議無否決、CI 全綠即由派發者 merge
    - 第 1 條第 2 點:本機審議(DIR-1)全部席位都有回答,否決席(risk、security、compliance)沒有 fail、沒有 no-answer;
    - 第 2 條:**不適用**(仍須停下問使用者):新 CHG 或 CHG 修訂的確認閘;任何否決席 fail 或 no-answer;需要推翻否決席;變更 GitHub ruleset、repo 安全設定或其他 repo 外設定;任何需要繞過 main 保護的操作;驗收(ACC)判定為未通過時的處置選擇。
    - 取捨(派發者註記):CHG-20261022-03 安全影響 (j)(q) 以「程式變更一律使用者核准 merge」作為殘留風險的補償控制;本條移除這一層,補償控制改為「否決席無 fail + CI 全綠 + 第 5 條自動失效」。

## DIR-5 — 批次審議:全部 task 完成後逐一審議,單一模型統整,開發模型一次修正

- 日期 / 分支:2026-10-27(UTC+0)/ 全分支
- tags/scope:cicd, review
- 情境:已過確認閘的 CHG,自 CHG-20261022-04 T3 起的施工。
- 規則:
  1. 施工期:由 main 開整合分支 `chg/<CHG-ID>-impl`;每個 task 仍各開一個 PR(DIR-2 第 1 條),但 base 為整合分支,施工期不跑本機審議、不跑 CI(`ci.yml` 只對 main 觸發)、不合進 main;CI 四項於第 5 條的合併 PR 執行。
  2. 全部 task 完成後,對每個 task PR 逐一跑本機審議(DIR-1 席位不變)。
  3. 審議全部完成後,由**單一模型**(預設 Opus、effort medium,與施工者不同的子代理;使用者可改指定)讀取全部審議留言,統整成一份報告:每項發現的出處(PR、席位)、是否成立、跨 task 的共同問題、建議修正。報告寫在整合分支的 `docs/changes/review/<CHG-ID>-summary.md`(不放在 `docs/changes/` 根目錄,避免符合 CI `chg` 檢查的 CHG 檔名規則)。此報告是施工期的修正依據,不是合併判定;合併判定見第 5 條。
  4. 由開發的模型依統整報告在整合分支一次修正。
  5. 以一個 PR 將整合分支合進 main;該 PR 跑六席意見、統整席判定(DIR-6)與 CI 四項,merge 條件同 DIR-4 第 1 條(統整判定 `merge`、CI 全綠);不適用時停下問使用者。
  6. 合併 PR 統整判定 `fix` 時依 DIR-6 第 7 條處理;連續兩輪 `fix` 依 DIR-4 第 2 條交使用者。
  7. DIR-1「獨立階段不得看到前輪判決」的例外:第 5 條合併 PR 送審內容中的統整報告(`docs/changes/review/<CHG-ID>-summary.md`)與 CHG 的修訂補記、派發記錄,屬本條規定的必要產出,必然記載第 2 條個別審議的結果,不視為違反該隔離要求;送審時仍去除 CHG「## 審議」節,並指示各席以程式與規格獨立判斷,不得以前輪判決作為本輪判決的依據。合併 PR 的統整席(DIR-6)本來就讀全部意見與前輪紀錄,不需本例外。
- 原因:使用者指示(2026-10-27,原話):「目前審議調整為全部修改完後再進行個別審議,並且審議完後再統一整理全部審議一次 統整報告再由開發的model進行修改」;補充:「目前審議調整為全部修改完後再進行個別審議,並且審議完後再統一由單一模型整理全部審議一次 統整報告再由開發的model進行修改」
- 信度:使用者確認(整合分支機制、統整報告落點與預設統整模型為 agent 補上的執行細節,使用者可推翻)
- 狀態:生效
- 歷史:
  - 2026-10-28 依 CHG-20261028-02(使用者原話:「改為審議席提供意見 最後統整為單一獨立審議席全部審議再決議如何修」)修訂。原敘述:
    - 第 3 條:審議全部完成後,由**單一模型**(預設 Opus、effort medium,與施工者不同的子代理;使用者可改指定)讀取全部審議留言,統整成一份報告:每項發現的出處(PR、席位)、是否成立、跨 task 的共同問題、建議修正。報告寫在整合分支的 `docs/changes/review/<CHG-ID>-summary.md`(不放在 `docs/changes/` 根目錄,避免符合 CI `chg` 檢查的 CHG 檔名規則)。
    - 第 5 條:以一個 PR 將整合分支合進 main;該 PR 跑完整本機審議與 CI 四項,merge 條件同 DIR-4 第 1 條(否決席無 fail、全部席位有回答、CI 全綠);不適用時停下問使用者。
    - 第 6 條:修正後仍有否決席 fail 時,依 DIR-4 第 2 條交使用者。
    - 第 7 條:DIR-1「獨立階段不得看到前輪判決」的例外:第 5 條合併 PR 送審內容中的統整報告(`docs/changes/review/<CHG-ID>-summary.md`)與 CHG 的修訂補記、派發記錄,屬本條規定的必要產出,必然記載第 2 條個別審議的結果,不視為違反該隔離要求;送審時仍去除 CHG「## 審議」節,並指示各席以程式與規格獨立判斷,不得以前輪判決作為本輪判決的依據。
  - 2026-10-28 新增第 7 條(無原敘述)。依據:CHG-20261022-04 合併 PR #76 本機審議 compliance 席指出統整報告與 DIR-1 隔離要求衝突,使用者 2026-10-28 選擇「Amend DIR-5 exemption」(原話)。
  - 2026-10-28 修訂(第 1 條補明 task PR 不跑 CI、CI 於合併 PR 執行;第 3 條報告落點改為 `docs/changes/review/`。依據:CHG-20261022-04 DIR-5 統整報告 RS-1、RS-41,使用者 2026-10-28 指示照報告建議處理(原話節錄:「RS-41 change the DIR-5 report location and git mv the file; RS-1 add the CI wording to DIR-5」)。)原敘述:
    - 第 1 條:施工期:由 main 開整合分支 `chg/<CHG-ID>-impl`;每個 task 仍各開一個 PR(DIR-2 第 1 條),但 base 為整合分支,施工期不跑本機審議、不合進 main。
    - 第 3 條(落點句):報告寫在整合分支的 `docs/changes/<CHG-ID>-review-summary.md`。

## DIR-6 — 審議席提供意見,單一獨立統整席判定可否合併

- 日期 / 分支:2026-10-28(UTC+0)/ 全分支
- tags/scope:cicd, review, llm
- 情境:任何需要審議的 PR(`review_gate.py` 判定需要審議者),自 CHG-20261028-02 合進 main 之後的下一個 PR 起;第一個適用者為 PR #76。
- 規則:
  1. **意見席**:DIR-1 席位表的六席(risk、impact、drift、compliance、security、consistency)只提供意見。各席輸出的 `pass / concern / fail` 是建議等級,**不具否決效力**;沒有回答(`no-answer`)也不擋 merge。席位分配、序列執行、逾時、Codex 限制、送審 CHG 去除「## 審議」節等規定照 DIR-1。
  2. **統整席**:同一 head 的意見留言貼出後,派發者派**一個**獨立 Claude subagent 擔任統整席(預設 Opus、effort medium;使用者可改指定)。統整席不得是該 PR 的施工者,也不得是同一 PR 前一輪的統整席子代理(每輪新派)。
  3. 統整席讀取:該 head 的意見留言全文、CHG 全文(含「## 審議」節與派發記錄)、完整程式(`gh pr diff`;過大時 `git fetch origin <head sha>` 後以 `git show <head>:<路徑>` 讀取)。統整席只讀,不改檔、不另開 PR、不貼留言;唯一寫出物是判定 JSON(`outputs/.tmp/review_decision/pr-<編號>-<sha12>.json`,格式見 `scripts/review_decision.py --schema`)。
  4. 統整席判定 `merge`(可合併)或 `fix`(需修正):逐席列出建議等級、是否採納與理由;每項發現核對程式後判斷是否成立;`no-answer` 的席位逐一列出,並由統整席以該席的判決問題自行檢視、寫出結果;`fix` 必須列出修法(位置、改法、理由);需使用者決定的事項列入 `needs_user`,有任何一項即不得判 `merge`。
  5. 派發者以 `python3 scripts/review_decision.py --pr <編號> --decision-file <JSON> --model <實際模型> --effort <等級>` 貼出判定留言(標記 `<!-- review-decision CHG-… sha=<head> decision=merge|fix model=… effort=… -->`)。`model`、`effort` 由派發者填,不信任子代理自填。**派發者不得修改 JSON 內容**;格式不合格重派一次,仍不合格交使用者。
  6. CI `review` 檢查:需要擁有者對目前 head 的意見留言(標記含六席)與排在其後、CHG 相同的擁有者判定留言,且判定為 `merge`。
  7. 判定 `fix`:由開發的模型依修法清單修正,產生新 head 後重跑意見與統整(新派子代理)。同一 PR **連續兩輪**判定 `fix` 時停下交使用者。
  8. 統整席的職責就是讀全部意見與前輪紀錄,**不受 DIR-1 第 11 條(送審 CHG 去除審議紀錄;獨立階段不得看到前輪判決)約束**;意見席的隔離不變。
  9. 統整判定 `merge` 不能取代使用者在確認閘的判斷;判定只決定 DIR-4 預授權的條件是否成立。
  10. **統整席的資料外送例外**:統整席是 Claude subagent(雲端,Anthropic),讀取的待審資料會送出本機。這是 DIR-1 第 9 條(Codex 席,只限審議席位)之外、另一項**使用者明示**的資料外送例外(使用者 2026-10-28 選擇統整席為「Opus medium(獨立子代理)」,確認閘原話「全部照草案核准」)。範圍只限:本 repo(已公開)PR 的意見留言、CHG 全文、PR diff 與 head 的檔案內容;不含 `config/studio.db`、`outputs/` 產出物、使用者聲線檔或其他 repo 外的資料,也不擴及審議以外的用途。施工者本來就是 Claude subagent(DIR-3),統整席不是新的資料去處。
- 原因:使用者指示(2026-10-28,原話):「改為審議席提供意見 最後統整為單一獨立審議席全部審議再決議如何修」;追問後選擇:合併由統整席決定、統整席 Opus medium、CI 閘改看統整判定、PR #76 重審暫停待新流程生效。與 DIR-2 第 3 條「merge 的停點只准加嚴」的關係:本條移除 risk、security、compliance 三席的單席否決,是**使用者明示指示的規則變更**(2026-10-28 原話如上;確認閘 2026-10-28 原話「全部照草案核准」),不是 agent 自行放寬;DIR-2 第 3 條的停點本身(程式變更須使用者核准或依 DIR-4 預授權、治理基礎設施需 `infra-approved`)不變。
- 信度:使用者確認(第 2 條模型、第 6 條閘、第 10 條統整席為雲端 Claude 為使用者選擇;呼叫方式、標記格式、JSON 欄位、連續兩輪停下、隔離不適用為 agent 補上的執行細節,見 CHG-20261028-02 代決事項,使用者可推翻)
- 狀態:生效(CHG-20261028-02 合進 main 時)
- 歷史:
  - 2026-10-28 依 CHG-20261028-02 DIR-5 統整報告 M-3(RS-7,現行條文去除「否決席」用語)修訂。原敘述:
    - 原因(停點關係句):本條移除否決席的單席否決,是**使用者明示指示的規則變更**,……
  - 2026-10-28 依 CHG-20261028-02 DIR-5 統整報告 M-4(CHG 記錄補正時一併調整用語,意思不變)修訂。原敘述:
    - 第 3 條(片段):統整席只讀,不改檔、不開 PR、不貼留言;


## DIR-7 — 威脅模型:已能寫入本機檔案的攻擊者不在範圍

- 日期 / 分支:2026-10-28(UTC+0)/ 全分支
- tags/scope:security, review
- 情境:審議意見席(特別是 security 席)、統整席(DIR-6)與開發模型評估安全發現時;第一個適用者為 PR #76(CHG-20261022-04)統整判定的 F-1(匯出 MP4 佔位檔被換成符號連結)。
- 規則:
  1. **使用情境**:本專案只在本機單人使用(網頁只綁 `127.0.0.1:7860`,MCP 為 stdio)。
  2. **範圍外**:需要攻擊者先取得本機檔案寫入能力才成立的威脅——例如把檔案或目錄換成符號連結、在 `projects/`、`outputs/`、`config/` 等專案目錄內放檔或改檔、檢查與使用之間的 TOCTOU 競態——**不列為 security 必修項,也不作為統整席判定 `fix` 的理由**。
  3. 審議意見席與統整席遇到第 2 條這類發現,記為「威脅模型外(DIR-7)」即可,不要求修正;開發模型也不主動為這類威脅追加防護。
  4. **仍在範圍**:網路面的防護(Host / Origin / Token 中介層、只綁 `127.0.0.1`、不送資料出本機、新增網路呼叫屬高風險變更),以及會造成資料毀損、誤刪或誤覆寫的程式缺陷(不論是否涉及符號連結,只要正常使用就會發生)。
  5. **既有防護保留**:已完成的 `safe_fs` dir_fd 逐層 `O_NOFOLLOW` 防護(CHG-20261022-04 DIR-5 修正 2~5)保留,不回退;本條只決定之後不再因第 2 條的威脅要求修正。
- 原因:使用者指示(2026-10-28,原話):「這些風險屬於本機,前提是要入侵到本地 既然能入侵到本地那麼這些安全考慮都是無用的」。同日先前對 CHG-20261022-04 外部程式只能以路徑存取的殘留風險,使用者原話:「接受」。
- 信度:使用者確認
- 狀態:生效
