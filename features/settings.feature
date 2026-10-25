# language: zh-TW
# CHG-20261022-01 · FR-21, FR-23(修訂 2:設定改存 SQLite `config/studio.db`,設定頁由首頁右上角 ⚙ 開啟)
功能: 模型設定(網頁與 MCP 共用)

  背景:
    假設 工作室網頁已在 127.0.0.1:7860 啟動
    而且 Ollama 已下載 qwen3:8b 與 glm4:latest

  場景: 從首頁右上角圖示進入設定
    當 我開啟首頁並按右上角的設定圖示
    那麼 畫面顯示設定頁,頁首有「← 首頁」
    而且 首頁與設定頁都沒有分頁列

  場景: 變更推理模型後 MCP 立即使用
    當 我在設定頁把「推理」改成 qwen3:8b 並儲存
    那麼 設定資料庫 config/studio.db 的推理模型是 qwen3:8b
    而且 MCP 工具 list_models 回報推理模型是 qwen3:8b
    而且 git 工作樹沒有任何變更

  場景: 未下載的模型不能存
    當 我送出推理模型為「not-a-model:1b」的設定
    那麼 伺服器回應 422
    而且 設定資料庫的內容不變

  場景: 否決席不能用 glm4
    當 我開啟審議席位的 security 下拉選單
    那麼 選項中沒有 glm4:latest
    當 我直接送出 security 席為 glm4:latest 的設定
    那麼 伺服器回應 422

  場景: 舊的 JSON 設定只匯入一次
    假設 設定資料庫不存在
    而且 config/models.json 的推理模型是 qwen3:8b
    當 我開啟設定頁
    那麼 推理模型顯示 qwen3:8b,並提示「已從 config/models.json 匯入一次」
    當 我再把 config/models.json 的推理模型改成 glm4:latest 並重新開啟設定頁
    那麼 推理模型仍顯示 qwen3:8b

  場景: 設定資料庫損壞時回到預設值
    假設 config/studio.db 的內容不是 SQLite 資料庫
    當 我開啟設定頁
    那麼 畫面顯示預設值與「設定資料庫無法讀取,已使用預設值」警告
    而且 config/studio.db 的內容沒有被改寫

  場景: 網頁存檔時 MCP 同時讀取
    當 網頁連續儲存設定 200 次,同時 MCP 呼叫 list_models 200 次
    那麼 每次 list_models 都成功,並回報其中一份完整的設定
