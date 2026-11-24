# language: zh-TW
# CHG-20261022-03 · DIR-2
功能: CI/CD 三類檢查與本機審議閘

  場景: 帳本漂移讓 PR 變紅
    假設 PR 改了程式結構但沒有更新 docs/structure
    當 CI 執行 document job
    那麼 document job 失敗並列出漂移項目

  場景: commit 沒有引用 CHG 編號
    假設 PR 中有一個改動 media.py 的 commit 訊息沒有 CHG 編號
    當 CI 執行 chg job
    那麼 chg job 失敗並列出該 commit

  場景: 純 knowledge 更新豁免 CHG 編號
    假設 PR 中的 commit 只改動 docs/knowledge/
    當 CI 執行 chg job
    那麼 chg job 通過

  場景: 執行計畫格式不合格
    假設 PR 新增的 CHG 缺少 Global Constraints
    當 CI 執行 chg job
    那麼 chg job 失敗並指出缺少的節

  場景: 程式錯誤
    假設 PR 引入一個未定義的變數
    當 CI 執行 program job
    那麼 program job 失敗

  場景: 中風險 CHG 缺本機審議
    假設 PR 對應的 CHG 風險分級為中
    而且 PR 沒有對應目前 head 的 local-review 留言
    當 CI 執行 review job
    那麼 review job 失敗並提示執行 scripts/local_review.py

  場景: 統整席判定需修正(CHG-20261028-02 / DIR-6:意見席判 fail 不再單獨擋下)
    假設 local-review 意見留言中 security 席判 fail
    而且 其後擁有者對目前 head 的 review-decision 判定為 fix
    當 CI 執行 review job
    那麼 review job 失敗

  場景: 本機部署只在乾淨的 main 上執行
    假設 目前在其他分支
    當 我執行 scripts/deploy_local.sh
    那麼 腳本中止並提示切回 main

  場景: 非擁有者偽造審議留言
    假設 PR 改到程式路徑
    而且 一個非 repo 擁有者的帳號貼了合格的 local-review 標記留言
    當 CI 執行 review job
    那麼 review job 失敗

  場景: 自評低風險但改到程式
    假設 PR 對應的 CHG 自評低風險且改到 media.py
    而且 PR 沒有 local-review 留言
    當 CI 執行 review job
    那麼 review job 失敗

  場景: fork PR 不在本機執行
    假設 PR 來自 fork
    當 我執行 scripts/local_review.py
    那麼 腳本拒絕並提示不在本機執行 fork 的內容

  場景: 程式碼中的秘密
    假設 PR 新增的 Python 檔含 GitHub 個人存取權杖樣式字串
    當 CI 執行 document job
    那麼 document job 失敗並指出檔案與行號

  場景: PR 修改治理檢查本身
    假設 PR 把 scripts/ci/commit_check.py 改成永遠成功
    而且 PR 中有一個沒有 CHG 編號的程式 commit
    當 CI 執行 chg job
    那麼 chg job 仍失敗,因為執行的是 base 分支的檢查腳本
    而且 review job 因治理基礎設施路徑未獲擁有者核准而失敗

  場景: 程式變更不自動 merge
    假設 PR 自評低風險且改到 media.py,CI 全綠
    那麼 主 agent 停下等使用者核准,不自動 merge

  場景: CI 腳本拒絕以 - 開頭的範圍
    假設 commit_check、plan_check_changed、secret_scan 共用 scripts/ci/gitutil.py 的 parse_range
    當 以 --range=-x..HEAD 或 --range HEAD..-x 執行任一支腳本
    那麼 腳本不呼叫 git 處理該範圍,stderr 印出「參數錯誤:--range 必須是 <base>..<head> 格式」
    而且 退出碼為 2

  # CHG-20261123-01 T3:Rust 三平台矩陣 rust-platform 與彙總 job rust(代決 8、9、10)
  場景: Rust 三平台任一失敗讓 rust 檢查變紅
    假設 PR 的 Rust 程式在 ubuntu、macos、windows 其中一個平台的 fmt、clippy、test、release 建置、比對工具示範或 cargo-deny 失敗
    當 CI 執行 rust-platform 矩陣與彙總 job rust
    那麼 rust 檢查失敗
    而且 main ruleset 把 rust 列為必要檢查,PR 不能 merge
