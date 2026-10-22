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

  場景: 否決席判 fail
    假設 local-review 留言中 security 席判 fail
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
