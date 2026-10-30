# language: zh-TW
# CHG-20261022-02 · FR-22(使用者故事 1~7;D7~D17)
功能: 設定頁的 MCP 註冊管理(Claude Code / Codex)

  背景:
    假設 工作室網頁已在 127.0.0.1:7860 啟動
    而且 ~/.local/bin/claude、~/.local/bin/codex、~/.local/bin/uv 都存在

  場景: 顯示兩個 client 的註冊狀態與指令
    假設 local-models 已在 Claude Code 與 Codex 的 user scope 註冊,指向本 repo 的 local_models_mcp.py
    當 我進入「設定 › MCP 註冊」
    那麼 畫面顯示 local-models 伺服器指令「~/.local/bin/uv run --script <repo>/local_models_mcp.py」
    而且 Claude Code 與 Codex 都顯示「已註冊(指向本 repo)」
    而且 畫面上的路徑以「~」與「<repo>」顯示,不出現本機絕對路徑

  場景: 開頁不啟動 MCP 伺服器
    當 我進入「設定 › MCP 註冊」
    那麼 claude mcp get 與 claude mcp list 的執行次數為 0
    而且 沒有 local_models_mcp.py 行程被啟動
    而且 Claude Code 的連線顯示「尚未檢查」

  場景: 重新檢查只對指向本 repo 的註冊做健康檢查
    假設 Claude Code 的 local-models 指向本 repo,且本 repo 沒有同名的 local / project scope 註冊
    當 我按「重新檢查」
    那麼 claude mcp get local-models 以 repo 根目錄為工作目錄執行恰 1 次
    而且 Claude Code 顯示連線結果

  場景: 註冊內容不是本 repo 時不做健康檢查
    假設 Claude Code 的 local-models 指向其他位置
    當 我按「重新檢查」
    那麼 claude mcp get 的執行次數為 0
    而且 Claude Code 顯示「不做健康檢查:註冊內容不是本 repo 的伺服器」

  場景: 本 repo 有同名 local scope 註冊時提示遮蔽
    假設 ~/.claude.json 的 projects 中本 repo 那一項有 local-models
    當 我按「重新檢查」
    那麼 claude mcp get 的執行次數為 0
    而且 畫面顯示「本 repo 有同名 local / project scope 註冊,會優先於 user scope」

  場景: 註冊前確認框顯示完整指令
    假設 Codex 尚未註冊 local-models
    當 我按「註冊到 Codex」
    那麼 確認框逐一列出「~/.local/bin/codex mcp add local-models -- ~/.local/bin/uv run --script <repo>/local_models_mcp.py」
    而且 確認框列出會修改的檔案 ~/.codex/config.toml
    當 我按「取消」
    那麼 codex mcp add 的執行次數為 0
    當 我再按「註冊到 Codex」並按「確認執行」
    那麼 Codex 顯示「已註冊(指向本 repo)」

  場景: 移除只影響 local-models
    假設 Claude Code 的 user scope 另有其他 MCP 伺服器
    當 我在 Claude Code 列按「移除」並確認
    那麼 執行的指令是「~/.local/bin/claude mcp remove local-models -s user」
    而且 其他 MCP 伺服器的設定不變

  場景: CLI 失敗時顯示錯誤原文
    假設 claude mcp add 回非 0 結束碼與錯誤訊息
    當 我在 Claude Code 列按「註冊」並確認
    那麼 畫面顯示 CLI 的錯誤原文(去除 env 值、路徑以「~」「<repo>」顯示)
    而且 Claude Code 不顯示「已註冊」

  場景: 移除後以重新查到的狀態判斷成功與否
    假設 codex mcp remove 回結束碼 0,但註冊仍在
    當 我在 Codex 列按「移除」並確認
    那麼 畫面顯示操作未成功,Codex 仍顯示「已註冊」

  場景: env 值不送到瀏覽器
    假設 local-models 的 env 有 SECRET_KEY,值為「秘密值-7f3a」
    當 我進入「設定 › MCP 註冊」並開啟移除確認框
    那麼 畫面列出鍵名 SECRET_KEY,值顯示「(已設定)」
    而且 畫面與 API 回應都不含「秘密值-7f3a」

  場景: MCP_TOOL_TIMEOUT 未設定只提醒
    假設 ~/.claude/settings.json 的 env 沒有 MCP_TOOL_TIMEOUT
    當 我進入「設定 › MCP 註冊」
    那麼 Claude Code 列顯示 MCP_TOOL_TIMEOUT 未設定的提醒與建議值
    而且 ~/.claude/settings.json 的內容不變

  場景: Codex 沒有 tool_timeout_sec 時提醒並給片段
    假設 Codex 的 local-models 沒有 tool_timeout_sec
    當 我進入「設定 › MCP 註冊」
    那麼 Codex 列顯示「沒有 tool_timeout_sec;長工具可能逾時」
    而且 畫面提供可複製的片段「[mcp_servers.local-models]」與「tool_timeout_sec = 1800」
    而且 ~/.codex/config.toml 的內容不變

  場景: 移除 Codex 前警告逾時設定會消失
    假設 Codex 的 local-models 有 tool_timeout_sec = 1800
    當 我在 Codex 列按「移除」
    那麼 確認框顯示「tool_timeout_sec 會一起消失,重新註冊也不會帶回」
    而且 確認框提供恢復用的片段「tool_timeout_sec = 1800」

  場景: 未安裝的 client 停用按鈕
    假設 ~/.local/bin/codex 不存在
    當 我進入「設定 › MCP 註冊」
    那麼 Codex 顯示「未安裝」,註冊與移除按鈕停用

  場景: 跨站請求與缺確認一律拒絕
    當 外站對 /api/mcp/apply 送出請求
    那麼 伺服器回應 403,CLI 執行次數為 0
    當 帶正確權杖但沒有 confirm:true 呼叫 /api/mcp/apply
    那麼 伺服器回應 400,CLI 執行次數為 0
    當 帶正確權杖與 confirm:true 但 client 為「x」
    那麼 伺服器回應 422,CLI 執行次數為 0
