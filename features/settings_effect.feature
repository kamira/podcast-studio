# language: zh-TW
# CHG-20261026-01 · FR-3、FR-4、FR-10、FR-11、FR-21(生圖與聲線設定接上程式)
功能: 生圖與聲線設定在網頁與 MCP 都生效

  背景:
    假設 工作室網頁已在 127.0.0.1:7860 啟動
    而且 mflux-generate 與 BreezyVoice 已安裝

  場景: 沒改過設定時生圖與語音的參數與先前相同
    假設 設定資料庫的生圖與聲線設定等於預設值
    當 我執行「生圖」節點與「語音」節點
    那麼 mflux 的指令列含 --model mflux-community/flux-1-schnell-mflux-q4 與 --steps 4
    而且 BreezyVoice 的指令列含 tools/BreezyVoice/data/example.wav 與官方範例逐字稿

  場景: 改生圖步數後網頁與 MCP 都使用新步數
    當 我在設定頁把生圖步數改成 2 並儲存
    而且 我執行「生圖」節點
    而且 MCP client 呼叫 generate_image
    那麼 兩次 mflux 的指令列都含 --steps 2
    而且 MCP 工具 list_models 回報的生圖模型等於設定頁的值

  場景: 換聲線後網頁與 MCP 都使用新聲線
    假設 我已上傳一個 wav 聲線檔
    當 我在設定頁選擇該聲線檔並儲存
    而且 我執行「語音」節點
    而且 MCP client 呼叫 speak
    那麼 兩次 BreezyVoice 的指令列都以該聲線檔作為聲線樣本

  場景: 設定改動不需重新啟動
    假設 MCP 伺服器已啟動並呼叫過一次 generate_image
    當 我在設定頁把生圖步數改成 6 並儲存
    而且 MCP client 再呼叫一次 generate_image
    那麼 這次 mflux 的指令列含 --steps 6

  場景: 生圖不在執行期下載模型
    當 我執行「生圖」節點
    那麼 mflux 子程序的環境含 HF_HUB_OFFLINE=1
    而且 mflux 子程序沒有對外連線

  場景: 不合法的生圖步數不能存
    當 我直接送出生圖步數為 99 的設定
    那麼 伺服器回應 422
    而且 設定資料庫的內容不變
