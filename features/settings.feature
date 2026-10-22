# language: zh-TW
# CHG-20261022-01 · FR-21, FR-23
功能: 模型設定(網頁與 MCP 共用)

  背景:
    假設 工作室網頁已在 127.0.0.1:7860 啟動
    而且 Ollama 已下載 qwen3:8b 與 glm4:latest

  場景: 變更推理模型後 MCP 立即使用
    當 我在設定頁把「推理」改成 qwen3:8b 並儲存
    那麼 config/models.json 的推理模型是 qwen3:8b
    而且 MCP 工具 list_models 回報推理模型是 qwen3:8b

  場景: 未下載的模型不能存
    當 我送出推理模型為「not-a-model:1b」的設定
    那麼 伺服器回應 422
    而且 config/models.json 不變

  場景: 否決席不能用 glm4
    當 我開啟審議席位的 security 下拉選單
    那麼 選項中沒有 glm4:latest
    當 我直接送出 security 席為 glm4:latest 的設定
    那麼 伺服器回應 422

  場景: 設定檔損壞時回到預設值
    假設 config/models.json 內容不是合法 JSON
    當 我開啟設定頁
    那麼 畫面顯示預設值與「設定檔無法解析,已使用預設值」警告
