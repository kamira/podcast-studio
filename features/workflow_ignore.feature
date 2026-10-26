# language: zh-TW
# CHG-20261026-01 · FR-18(使用者另存的工作流程不讓工作樹變髒)
功能: 使用者另存的工作流程列入 git 忽略

  背景:
    假設 工作室網頁已在 127.0.0.1:7860 啟動
    而且 git 工作樹是乾淨的

  場景: 另存工作流程後工作樹仍乾淨
    當 我在「設定 › 進階流程」載入預設範本並存成「夜市流程」
    那麼 workflows/夜市流程.json 存在
    而且 git status --porcelain --untracked-files=all 沒有輸出
    而且 scripts/deploy_local.sh 不會以「工作樹不乾淨」中止

  場景: 預設範本仍受 git 追蹤
    那麼 git ls-files workflows 只列出 workflows/default.json
    而且 workflows/default.json 沒有被 .gitignore 忽略

  場景: 覆寫預設範本會讓工作樹變髒
    當 我把畫布存檔覆寫成「default」並確認覆寫
    那麼 git status 顯示 workflows/default.json 已修改
    而且 scripts/deploy_local.sh 以「工作樹不乾淨」中止
    當 我執行 git checkout -- workflows/default.json
    那麼 git 工作樹回到乾淨
