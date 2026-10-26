# 修正指示(DIR)

> 索引在 `docs/knowledge/INDEX.md`。同一規則只留一條現行版本;更新時改寫本條並把舊敘述移到「歷史」。

## DIR-1 — 審議會席位一律由本機邏輯模型(Ollama)擔任

- 日期 / 分支:2026-10-22(UTC+0)/ 全分支
- tags/scope:review, llm
- 情境:任何需要審議會(review-panel)的時候——CHG 決策審議、A1 代問企劃審議、逐 task review、末端審查。
- 規則:
  1. 審議席位由本機 Ollama 上的邏輯模型擔任(Qwen、GLM 等),**不派 Claude subagent 當席位**;例外見第 9 條(security、compliance 由 Codex 擔任)。派發者(主 agent)負責組派發包、呼叫模型、收判決、裁決;**核准權仍在使用者**(確認閘不轉包)。
  2. 呼叫方式:直接 `POST http://localhost:11434/api/chat`(`stream:false`、`keep_alive:0`、`num_ctx` ≥ 16384,派發包大時用 32768)。MCP `reason` 工具只能選到兩個模型,不夠分席,不用它。
  3. **一次只跑一席**(24 GB 記憶體):每席跑完以 `keep_alive:0` 卸載,再派下一席。
  4. 判決必須是一行合格的 `[verdict] <席位> | <model> | pass/fail/concern | <證據指向> | <理由>`;**格式不合格就等於「沒有回答」**→ 重派一次,仍不合格就交給使用者。建議以 Ollama `format`(JSON schema)強制結構,再由派發者轉成判決行;`<model>` 欄由派發者填入實際模型名,不信任模型自填。
  5. 席位分配(依 2026-10-22 實測調整;詳見下方證據):

     | 席位 | 模型 | 備註 |
     |------|------|------|
     | risk(否決席) | 中 / 低風險:`hf.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M`,`think:true`;**高風險:Codex(codex-cli,read-only sandbox,雲端)** | 2026-10-23 使用者指示「目前先暫不使用27B模型審議」(第 10 條);27B 暫停期間 |
     | security(否決席) | Codex(codex-cli,read-only sandbox,雲端) | 2026-10-23 使用者決定(第 9 條);以 `--codex-seats none` 改回 Ollama 時沿用舊配置:`qwen3:8b`,高風險 CHG 用 27B |
     | compliance(否決席) | Codex(codex-cli,read-only sandbox,雲端) | 2026-10-23 使用者決定(第 9 條);改回 Ollama 時用 `huihui_ai/qwen3-abliterated:8b`,`think:true` |
     | impact | `glm4:latest` | 非否決席 |
     | drift | `qwen3:8b`,`think:true` | |
     | consistency | `glm4:latest` | 非否決席 |

  6. **glm4 不得擔任否決席**(risk / security / compliance)。
  7. 不用作席位:`Llama-Breeze2-8B`(台灣用語潤飾,不是邏輯模型)、`qwen2.5-coder:7b`(CLI 專用,隨 CLI 棄用)。
  8. 限制要寫進每次審議記錄:目前只有 Qwen、GLM 兩個模型家族,席位間會共享盲點;本機 8–9B 模型的判斷力低於雲端大模型——**否決席 pass 不能取代使用者在確認閘的判斷**。
  9. **Codex 席位(2026-10-23 使用者指示「使用 codex 協助」)**:
     - security、compliance 兩個否決席由 Codex(`codex exec`)擔任;`scripts/local_review.py` 以 `--codex-seats <逗號清單>` 覆寫,`--codex-seats none` 表示全部用 Ollama。
     - Codex 是雲端模型,送審資料(PR、CHG、diff、參考文件)會送出本機;這是第 1 條「本機模型」原則與「不送資料出本機」的**使用者明示例外**,前提是 repo 已公開。例外只限審議席位,不擴及其他用途。
     - Codex 一律只用 `-s read-only`;不得使用 `workspace-write`、`danger-full-access`、`--dangerously-bypass-approvals-and-sandbox`。prompt 由 stdin 傳入,判決以 `--output-schema`(與 Ollama 席同一份 JSON schema)強制;格式不合格、失敗或逾時(15 分鐘)= 沒有回答 → 重派一次,仍失敗記 `no-answer`。
     - 判決行與留言的 `<model>` 欄記 `codex-cli <版本>`(取自 `codex --version`),留言註明哪些席位由 Codex 擔任。
  10. **送審 CHG 去除審議紀錄**:送給所有席位(Ollama 與 Codex)的 CHG 刪除「## 審議」節(標題到下一個 `## ` 標題或檔尾),獨立階段不得看到前輪判決。
  10. **暫停 27B(2026-10-23 使用者指示,原話:「目前先暫不使用27B模型審議」)**:
     - 暫停期間任何審議席位一律不使用 `hf.co/HauhauCS/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF:IQ3_M`。
     - 原本由 27B 擔任的席位(高風險 CHG 的 risk 席)改由 Codex 擔任,與第 9 條同一套限制(read-only、prompt 經 stdin、記錄實際版本)。
     - 執行方式:`python3 scripts/local_review.py --pr <N> --codex-seats risk,security,compliance`;`scripts/local_review.py` 的預設值仍含 27B,**暫停期間不得省略此參數**。
     - 恢復須使用者明示;恢復時把本條移入「歷史」。
- 原因:使用者指示(2026-10-22):「審議交由 qwen, glm 等可用的本地邏輯模型」。推得的理由:與本專案「不送資料出本機」一致,並讓席位跨模型分散。
- 證據(2026-10-22 對照題:CHG 把 Gradio `server_name` 由 `127.0.0.1` 改 `0.0.0.0`,依 Guideline §5 應判 fail):
  - `qwen3:8b`(think)→ fail,理由正確;`<model>` 欄填錯
  - `Qwen3.5-9B Aggressive`(think)→ fail,理由正確;`<model>` 欄填錯
  - `glm4:latest`(同一份 NFR 脈絡)→ **concern**,而且判決行格式壞掉(把模板佔位字原樣輸出);沒給 NFR 脈絡時 → **pass**
- 每席逾時:27B 30 分鐘;逾時改派 Codex(使用者 2026-10-23 規則,原話:「如果本地高階模型審議需要花超過30分鐘轉為codex」);其餘 15 分鐘、重派一次。審議一律**序列**執行,不並行(使用者指示 2026-10-22)。
  - 27B 席某一階段逾時,或累計超過 30 分鐘仍沒有回答,不再用 27B 重派,該席該階段直接由 Codex(`-s read-only`、prompt 由 stdin)作答;Codex 也沒有回答時依第 9 條重派 Codex 一次,仍失敗記 `no-answer`。改派每席每階段各自判定:第一階段改派,互讀階段仍先試 27B。
  - 改派後判決行與留言的 `<model>` 欄記 `codex-cli <版本>(27B 逾時 30 分鐘改派)`,留言說明欄列出改派的席位與階段。改派會把該席送審資料送往雲端,適用第 9 條的使用者明示例外。
- 證據補充(2026-10-22 CHG-20261022-01 / -03 實審):`qwen3:8b` 在約 3 萬字的 CHG 上擔任 security 席,兩筆共 9 點發現全部與送審原文矛盾(例:稱未檢查 GET 的 Host,原文第 255 行明文規定);`Qwen3.8-27B` 擔任 risk 席的判決與原文相符、證據具體。使用者因此決定高風險 security 席改用 27B。
- 證據補充(2026-10-23 PR #12 本機審議):security 席(27B,think)兩次都輸出截斷、沒有回答;compliance 席(8B)把送審 CHG 內的前輪審議紀錄當成本輪事實而判 fail。派發者以 `codex exec -s read-only --output-schema … -o …` 手動試跑:security 44 秒回 concern,證據指到 `.github/workflows/ci.yml` 的具體行號;compliance 回 pass。使用者因此指示「使用 codex 協助」,並決定 security、compliance 改由 Codex 擔任、送審 CHG 去除審議紀錄。
- 信度:使用者確認(席位分配表與第 4、6 條為 agent 依實測推得,使用者可推翻;高風險 security 席 27B、第 9 條 Codex 席位為使用者決定)
- 狀態:生效
- 歷史:
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
  3. merge 的停點(只准加嚴):**只有只改 `docs/`、`features/` 的 PR** 可在 CI 綠後自動 merge;**任何改到程式路徑的 PR,不論風險分級,merge 前都停下等使用者核准**;改到治理基礎設施(`.github/`、`scripts/ci/`、`tests/ci/`、`.ai-sdlc/`、`scripts/local_review.py`、`scripts/deploy_local.sh`)另需擁有者 `infra-approved` 留言。
  4. 2026-10-22 收緊緣由:CHG-20261022-03 審議 security 席(27B)指出「自評低風險的程式變更可自動 merge 並部署到本機」,且 CI 檢查若從 PR head 執行可被 PR 自改。
- 原因:使用者指示(2026-10-22):「每次步驟都要 commit push pr merge 並且要設好完整 CICD(包含 document, chg, program)」。
- 信度:使用者確認(第 3 條的停點為治理層既有規則,agent 依 halt policy 補上)
- 狀態:生效(前置未完成:GitHub 遠端與 `gh` 尚未就緒)

## DIR-3 — 主 agent(Claude)專注派發,實作與審查交給工作者

- 日期 / 分支:2026-10-22(UTC+0)/ 全分支
- tags/scope:review, llm, interaction
- 情境:CHG 的 task 施工、審查、文件草稿、CI 失敗分析。
- 規則:
  1. 主 agent 負責:拆 task、組派發包、派工、收結果、裁決、對使用者回報;**不親自寫實作**。
  2. 工作者優先順序:本機模型(Ollama)能勝任的先給本機模型——審議席位(DIR-1)、文件草稿、測試案例草稿、commit / PR 說明、CI 日誌初判;本機模型產出不合格(測試不過或格式不合)時,改派 Claude subagent,並在 CHG 記錄改派原因。**例外(2026-10-26 修訂)**:CHG、ACC 等治理文件草案(含修訂、驗收報告)可直接派 Claude subagent,不須先試本機模型,也不需記錄改派原因;已起草的 CHG-20261026-01 依此追認。
  3. 本機模型的產出一律經測試或審查才採用,不直接信任。
- 原因:使用者指示(2026-10-22):「可以使用本地模型協助處理,claude 專注於派發工作」。
- 修訂(2026-10-26):第 2 條加入治理文件草案例外,使用者原話:「PR #51 拆成 T1、T2 兩個 PR(之後 T3、T4、T5 也各自一個 PR);DIR-3 第 2 條修訂為「CHG / ACC 等治理文件草案可直接派 Claude subagent,不須先試本機模型」,以我今天的指示為依據,並對已起草的 CHG-20261026-01 追認。」
- 信度:使用者確認(第 2 條的分工與改派規則為 agent 推得,使用者可推翻)
- 狀態:生效

## DIR-4 — 預授權:已過確認閘的 CHG,審議無否決、CI 全綠即由派發者 merge

- 日期 / 分支:2026-10-25(UTC+0)/ 全分支
- tags/scope:cicd, review
- 情境:屬於**已通過使用者確認閘**的 CHG 的 task PR、依審議修正的 PR、帳本 PR。
- 規則:
  1. 同時符合下列條件時,派發者不必再逐次請使用者核准,可自行貼 `infra-approved`(註明依 DIR-4)並 squash merge:
     - PR 對應的 CHG 已通過使用者確認閘;
     - 本機審議(DIR-1)全部席位都有回答,否決席(risk、security、compliance)沒有 fail、沒有 no-answer;
     - CI 四項(document、chg、program、review)全綠。
  2. **不適用**(仍須停下問使用者):新 CHG 或 CHG 修訂的確認閘;任何否決席 fail 或 no-answer;需要推翻否決席;變更 GitHub ruleset、repo 安全設定或其他 repo 外設定;任何需要繞過 main 保護的操作;驗收(ACC)判定為未通過時的處置選擇。
  3. **功能需要修正**(已 merge 的功能事後發現要改):另開分支與 PR,不回頭改已 merge 的分支。
  4. 每次依本條 merge,在 PR 留言與 CHG「## 派發記錄」註明「依 DIR-4 預授權 merge」;使用次數計入治理健康度。
  5. **自動失效**:依本條 merge 的變更若事後被迴歸、驗收或使用者發現弄壞東西,本條立即失效,直到使用者重新授權。
- 取捨(派發者註記):CHG-20261022-03 安全影響 (j)(q) 以「程式變更一律使用者核准 merge」作為殘留風險的補償控制;本條移除這一層,補償控制改為「否決席無 fail + CI 全綠 + 第 5 條自動失效」。
- 原因:使用者指示(2026-10-25,原話):「往後就直接該CHG沒問題就等CICD後直接merge,功能需要修正則另開branch」。
- 信度:使用者確認(適用條件與不適用清單為 agent 依預授權「必須寫窄」的規範補上,使用者可推翻)
- 狀態:生效

