# language: zh-TW
# CHG-20261101-02 · DIR-1 第 13 條(修訂 1:分組繳交)
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

  場景: 送審資料超過 Opus 席單份上限
    假設 經過濾後的 diff 與 CHG 合計放不進一份 20000 字的請求
    當 腳本寫出 Opus 席的請求檔
    那麼 改為分組繳交,每份 prompt 不超過 20000 字,不建立唯讀檢視、不 fetch
    而且 答案檔路徑只出現在每份 prompt 最末段

  場景: 同一席不得同時由 Codex 與 Opus 擔任
    當 以 --codex-seats risk,security,compliance 執行且未改 --opus-seats
    那麼 腳本以退出碼 2 結束,說明 risk 依 DIR-1 第 13 條由 Opus 擔任

  場景: 以 --opus-seats none 改回本機 Ollama
    當 以 --opus-seats none 執行
    那麼 consistency 由 glm4:latest 擔任,risk 依風險分級由本機 Ollama 擔任,不寫任何請求檔

  場景: Codex 額度用盡時由 Opus 代答
    當 以 --codex-seats none --opus-seats risk,consistency,security,compliance 執行
    那麼 security、compliance 的模型標記為 claude-opus-medium(代 Codex,額度用盡)

  # CHG-20261101-02 修訂 1:分組繳交
  場景: 送審資料超過單份上限時分組繳交
    假設 CHG 與 diff 合計放不進一份 20000 字的請求
    當 腳本跑到 Opus 席
    那麼 CHG 依章節、diff 依直系呼叫、引用參數與共用參數的函式分組
    而且 每組一份請求檔,每份 prompt 不超過 20000 字,stderr 提示第 k / n 組
    而且 派發者一次只交接一份,每份各派一個全新的 review-seat-opus 子代理

  場景: 各組答案合成單席判決
    假設 Opus 席分成多組送審
    當 各組答案為 pass、concern 與沒有回答
    那麼 該席判決為 concern,留言列出沒有回答的組

  場景: 部分組沒有回答時不得合成 pass
    假設 Opus 席分成多組送審
    當 已回答的組都是 pass,另有一組沒有回答
    那麼 該席判決為 concern
    而且 標記與 [verdict] 行格式不變

  場景: 組數超過上限
    假設 CHG 或 diff 的分組超過各 2 組
    當 腳本跑到 Opus 席
    那麼 多出的單位不送審,留言列出未送審的章節、檔案或函式

  場景: 每席累計時間上限
    假設 Opus 席累計已用 60 分鐘
    那麼 剩下的組不再發請求,記為沒有回答

  場景: 全部組都沒有回答時記 no-answer
    假設 Opus 席所有組都沒有回答或都未派發
    而且 另有未送審的單位
    那麼 該席判決為 no-answer

  場景: 有未送審的單位時不得合成 pass
    假設 Opus 席有超出組數上限而未送審的單位
    當 已送審的組都是 pass
    那麼 該席判決為 concern,留言列出未送審的單位

  場景: Opus 代答 compliance 時不截參考資料
    當 以 --codex-seats none --opus-seats risk,consistency,security,compliance 執行
    那麼 compliance 不派發、記 no-answer 並註明參考資料須完整送達
    而且 security 照常由 Opus 代答

  場景: 固定部分超過單份上限
    假設 每組都附的固定部分超過 20000 字
    當 腳本組出 Opus 席的請求
    那麼 依序把參考資料、變更檔案清單、CHG 表頭截半並記注意行
    而且 截半後仍超過時該組不寫請求檔,記為沒有回答並註明未派發
