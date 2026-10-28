# language: zh-TW
# CHG-20261028-02 · DIR-6
功能: 審議席提供意見,單一獨立統整席判定可否合併

  場景: 各席判決改為不具否決效力的意見
    假設 擁有者的 PR 需要審議
    當 從 main 的工作副本執行 scripts/local_review.py
    那麼 各席的系統說明註明建議等級不具否決效力,且不含「否決席」字樣
    而且 留言標題為「審議意見」,local-review 標記與 [verdict] 行格式不變

  場景: 非 think 的 Ollama 席在 CHG 過大時截短 CHG
    假設 CHG 全文大到 diff 截到 0 仍放不進 num_ctx − NUM_PREDICT
    當 執行本機審議
    那麼 impact、consistency 席的 CHG 以 clip() 截到放得進,並記「CHG 由 N 字截短為 M 字」注意行
    而且 留言說明列列出「CHG 截斷」的席位
    而且 think 席與 Codex 席的送審內容不截 CHG

  場景: 統整席判定以腳本貼出
    假設 目前 head 已有擁有者的六席意見留言
    而且 統整席子代理已寫出合格的判定 JSON
    當 派發者執行 scripts/review_decision.py --pr <編號> --decision-file <JSON> --model <實際模型> --effort medium
    那麼 PR 多一則判定留言,恰有一個 review-decision 標記,sha 為目前 head
    而且 標記的 model、effort 取自參數,不取自 JSON

  場景: 判定 JSON 的文字不能偽造標記
    假設 判定 JSON 的文字含 <!--、-->、三個反引號或偽造的 review-decision 標記
    當 執行 review_decision.py
    那麼 留言仍只有一個 review-decision 標記,JSON 文字都在程式碼區塊內

  場景: 不合格的判定不貼出
    假設 判定 JSON 的 head_sha 不是目前 head,或欄位不合格,或 chg 與意見留言不同
    當 執行 review_decision.py
    那麼 腳本以退出碼 1 拒絕,列出原因,不呼叫 gh pr comment

  場景: 沒有六席意見留言時不貼判定
    假設 目前 head 沒有擁有者的意見留言,或意見標記缺席位
    當 執行 review_decision.py
    那麼 腳本以退出碼 1 拒絕

  場景: fork 或非擁有者的 PR
    假設 PR 來自 fork,或作者不是 repo 擁有者,或不在 main 執行
    當 執行 review_decision.py
    那麼 腳本以退出碼 1 拒絕,不呼叫 gh pr comment

  場景: CI 審議閘看統整判定
    假設 PR 需要審議,目前 head 有擁有者的六席意見留言
    當 意見留言之後有擁有者對目前 head、同一 CHG 的判定留言且判定為 merge
    那麼 CI review 檢查通過,即使有意見席判 fail 或沒有回答
    但是 判定為 fix、缺判定、判定排在意見留言之前、sha 不是目前 head 或非擁有者貼出時,review 檢查不通過
