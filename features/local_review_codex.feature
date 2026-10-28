# language: zh-TW
# CHG-20261028-01 · DIR-1 第 9 條
功能: 本機審議的 Codex 席讀得到完整程式

  場景: 大 diff 時 Codex 席以 PR head 唯讀檢視讀程式
    假設 擁有者的 PR 經過濾後的 diff 超過 30000 字
    當 從 main 的工作副本執行 scripts/local_review.py
    那麼 Codex 席以 -C 指向 .review/pr-<編號>-<sha12> 的 --no-checkout detached worktree
    而且 該目錄除 .git 指標檔外沒有任何追蹤檔
    而且 Codex 席的 prompt 含 base 與 head 的 sha、變更檔案清單、diffstat,以及 git show 與 git diff --no-textconv --no-ext-diff 的讀法
    而且 Codex 席的 prompt 指示不得以 git show 或 git diff 讀 CHG 檔
    而且 Codex 席的 prompt 不含截短後的 diff

  場景: 小 diff 時 Codex 席收到完整 diff
    假設 擁有者的 PR 經過濾後的 diff 不超過 30000 字
    當 執行本機審議
    那麼 不建立唯讀檢視
    而且 Codex 席的 prompt 含完整 diff,不依 Ollama 的 num_ctx 截短

  場景: Ollama 席維持截短
    假設 擁有者的 PR 的 diff 放不進 Ollama 的 num_ctx
    當 執行本機審議
    那麼 Ollama 席的 diff 依 num_ctx 截短並註明截斷

  場景: 審完一律移除唯讀檢視
    假設 已為 Codex 席建立唯讀檢視
    當 審議完成或中途丟出例外
    那麼 .review/ 之下的檢視與其 worktree 登記都被移除

  場景: 審到的內容與標記一致
    假設 唯讀檢視的 HEAD 不等於 GitHub 回報的 head sha
    當 執行本機審議
    那麼 腳本以退出碼 2 中止,不呼叫任何審議席,也不留下檢視

  場景: 不刪除不屬於本腳本的同名目錄
    假設 .review/pr-<編號>-<sha12> 已存在,但不是 git worktree list 登記的 worktree
    當 執行本機審議
    那麼 腳本以退出碼 2 拒絕,該目錄與其中檔案原封不動

  場景: 登記的舊檢視含其他檔案時不刪
    假設 .review/pr-<編號>-<sha12> 是登記的 worktree,但目錄內除 .git 外還有使用者的檔案
    當 執行本機審議
    那麼 腳本以退出碼 2 拒絕,不呼叫 git worktree remove,該目錄與其中檔案原封不動

  場景: 只含 .git 的登記舊檢視被安全清除
    假設 .review/pr-<編號>-<sha12> 是登記的 worktree,目錄內只有 .git 指標檔
    當 執行本機審議
    那麼 腳本只刪 .git 指標檔與空目錄並 git worktree prune,不使用 git worktree remove --force

  場景: fork 與非擁有者 PR 不取用內容
    假設 PR 來自 fork 或作者不是 repo 擁有者
    當 執行本機審議
    那麼 腳本以退出碼 1 拒絕,不執行 git fetch 也不建立檢視
