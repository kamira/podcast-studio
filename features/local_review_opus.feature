# language: zh-TW
# CHG-20261101-02 · DIR-1 第 13 條
功能: 本機審議的 risk、consistency 席由 Opus 子代理擔任

  場景: 預設由 Opus 擔任 risk、consistency
    假設 擁有者的 PR 待審
    當 派發者以預設參數從 main 的工作副本背景執行 scripts/local_review.py
    那麼 risk、consistency 各寫出一份請求檔,stderr 提示請求檔與答案檔路徑
    而且 impact、drift 由本機 Ollama 擔任,security、compliance 由 Codex 擔任
    而且 意見留言中 risk、consistency 的模型為 claude-opus-medium

  場景: 子代理寫入合格答案
    假設 派發者把請求檔的 prompt 原樣交給全新的 Opus 子代理
    當 子代理在 15 分鐘內寫入符合判決 schema 的答案檔
    那麼 該席判決採用答案內容,請求檔與答案檔隨後被刪除

  場景: 答案檔還在寫入時不讀
    假設 子代理寫入答案檔的過程中檔案大小或修改時間仍在變動
    當 腳本每 5 秒輪詢答案檔
    那麼 連續兩次輪詢大小與修改時間都不變才讀取答案檔

  場景: 答案格式不合格或逾時
    假設 答案檔不是合格 JSON、不符判決 schema,或 15 分鐘內沒有答案檔
    當 腳本判定沒有回答
    那麼 以新的請求檔重派一次
    而且 第二次仍沒有合格答案時該席記 no-answer,舊請求的遲到答案不採用

  場景: 答案檔超過大小上限
    假設 答案檔超過 256 KB
    當 腳本讀取答案檔
    那麼 該次判為格式不合格,以新的請求檔重派一次

  場景: 請求檔不指定 outputs 讀取路徑且 prompt 自足
    當 腳本寫出 Opus 席的請求檔
    那麼 子代理 prompt 中出現的 outputs/ 路徑只有答案檔的寫出路徑
    而且 CHG、diff 與參考資料全部內嵌,prompt 不含要子代理讀檔或執行 git 的指示

  場景: 席位子代理只有寫入工具
    假設 repo 內有 .claude/agents/review-seat-opus.md
    那麼 其 frontmatter 的 model 為 opus、effort 為 medium、tools 只有 Write

  場景: diff 超過 Opus 席內嵌上限
    假設 經過濾後的 diff 超過 30000 字,或整份 prompt 會超過 60000 字
    當 腳本寫出 Opus 席的請求檔
    那麼 diff(必要時連同 CHG)截短並記注意行,整份 prompt 不超過 60000 字,不建立唯讀檢視、不 fetch
    而且 答案檔路徑只出現在 prompt 最末段

  場景: 同一席不得同時由 Codex 與 Opus 擔任
    當 以 --codex-seats risk,security,compliance 執行且未改 --opus-seats
    那麼 腳本以退出碼 2 結束,說明 risk 依 DIR-1 第 13 條由 Opus 擔任

  場景: 以 --opus-seats none 改回本機 Ollama
    當 以 --opus-seats none 執行
    那麼 consistency 由 glm4:latest 擔任,risk 依風險分級由本機 Ollama 擔任,不寫任何請求檔

  場景: Codex 額度用盡時由 Opus 代答
    當 以 --codex-seats none --opus-seats risk,consistency,security,compliance 執行
    那麼 security、compliance 的模型標記為 claude-opus-medium(代 Codex,額度用盡)
