# AI Guideline — podcast-studio

- 專案:podcast-studio(本機 AI 模型串接:Podcast 工作室網頁 + local-models MCP 伺服器)
- 分支(Branch):main
- 版本:v1.6(變更歷程見 §11)
- 日期:2026-10-22
- 狀態:草稿(v1.0 為導入反推;v1.1 新增 FR-16~23、v1.2 新增 FR-24~30、v1.3 新增 FR-31~34 並修改 FR-16/24;v1.5 修改 FR-21、FR-25、FR-31、FR-34 並新增 ADP-12,待 CHG-20261022-01/04 修訂 2 確認閘)
- 帳本落點:`docs/`(宣告,非推論。導入時 `docs/` 不存在,無 site generator / 生成產物佔用)
- 導入方式:adopt(存量導入,程式已存在、從未治理)
- Governance-baseline: 7da165b6048897abb8acac4bba69db9677827471
- Adopt-commit: e7b1d0cc03ee52e6208d0617717659cf579bcf94

> **來源等級標記**(本文件與 `docs/structure/*.md` 通用):
> - **〔觀察〕** 程式裡直接讀得到
> - **〔推論〕** 由證據推得的意圖(證據註明出處,如 `PODCAST_STUDIO.md §8`)
> - **〔未知〕** 判斷不了,需要人回答
>
> 未標等級的敘述不得視為規範。**被寫進本文件不代表該行為正確**——見 §9「導入盤點:具名待補項」。

## 1. 背景與目標

〔推論,來源:`PODCAST_STUDIO.md §1`〕在本機(Apple M5 Pro / 24 GB 統一記憶體)完成一集 podcast 影片,全程離線,不送資料到雲端服務:

1. 依需求產生口播文案
2. 使用者可直接編輯文案,或下指令請 AI 修改
3. 依最終文案產生台灣口音語音
4. 產生 podcast 靜態封面(含中文標題)
5. 封面 + 語音合成 MP4 影片(含字幕)

〔推論,來源:`PODCAST_STUDIO.md §10`〕另把同一組本機模型包成 MCP 伺服器(`local-models`),讓 Claude Code / Codex 等 agent 直接呼叫。

成功定義:〔未知〕使用者尚未給出可量測的成功標準(見 §6 待確認 Q3)。

## 2. 範圍

### 納入
- 〔觀察〕Gradio 網頁 `podcast_studio.py`(四分頁:文案 / 語音 / 封面 / 影片),監聽 `127.0.0.1:7860`
- 〔觀察〕MCP 伺服器 `local_models_mcp.py`(stdio,6 個工具:`reason`、`taiwanize`、`generate_image`、`speak`、`make_video`、`list_models`)
- 〔觀察〕共用模組 `taiwan_ai.py`(模型設定、`chat()`、`speak()`、`unload_llms()`、`to_tw()`)與 `media.py`(封面、字幕、影片)
- 〔觀察〕`taiwan_ai.py` 本身也是 CLI(`ask` / `code` / `image` / `speak` / `auto`)——**已棄用**(Q2),程式待 CHG 移除
- 〔觀察〕BreezyVoice macOS 重建:`setup_breezyvoice.sh` + `patches/breezyvoice-mac/`

### 不納入(明確排除)
- 〔推論,來源:`PODCAST_STUDIO.md §1`〕任何雲端 API / 對外網路服務
- 〔推論,來源:`PODCAST_STUDIO.md §8 #1`〕多人對談(目前只支援單人主持)
- 〔推論,來源:`PODCAST_STUDIO.md §10.5 #7`〕MCP `write_code` 工具(已移除,由 `reason` 兼任)
- 〔推論,來源:`PODCAST_STUDIO.md §10.5 #6`〕Claude 桌面 app 聊天的 MCP 註冊
- 〔觀察〕`tools/BreezyVoice/`(第三方 repo,不納入 git,由腳本重建)、`outputs/`(產出物)

## 3. 利害關係人

| 角色 | 關注點 |
|------|--------|
| 使用者(專案擁有者,單人) | 本機產出 podcast 影片、文案可控、台灣用語正確、離線 |
| MCP client(Claude Code / Codex) | 工具契約穩定、長時間工具不逾時、stdout 不被汙染 |

〔推論〕單人專案,沒有其他決策者。

## 4. 功能需求

| 編號 | 需求 | 優先級 | 來源等級 / 備註 |
|------|------|--------|------|
| FR-1 | 依主題、風格(5 種)、長度(0.5–10 分鐘)、補充要求產生單人口播稿;推理模型寫初稿 → Breeze2 潤飾 → OpenCC `s2twp` | P0 | 〔觀察〕`podcast_studio.py:22-33` |
| FR-2 | 文案可手動編輯,或依自然語言指示由推理模型改寫全文 | P0 | 〔觀察〕`podcast_studio.py:36-41,80-83` |
| FR-3 | 依文案合成台灣華語語音(BreezyVoice),輸出 `outputs/speech-*.wav`;合成前清除 Markdown 符號 | P0 | 〔觀察〕`taiwan_ai.py:128-142` |
| FR-4 | 產生 16:9 封面:推理模型寫英文 prompt → 卸載 LLM → FLUX 1360×768 → 縮放裁切 1920×1080 → 底部疊中文標題 | P0 | 〔觀察〕`podcast_studio.py:52-59`、`media.py:17-71` |
| FR-5 | 封面 + 音訊合成 1920×1080 H.264/AAC MP4 | P0 | 〔觀察〕`media.py:111-124` |
| FR-6 | 字幕:依標點切句(單句上限 24 字,再以逗號切)、依字數比例分配時間;MP4 內嵌軟字幕(mov_text)並另存 `.srt` | P1 | 〔觀察〕`media.py:79-108`;〔推論〕決策來源 `PODCAST_STUDIO.md §8 #3` |
| FR-7 | 語音與封面兩步互不依賴,可任意順序 | P1 | 〔推論〕`PODCAST_STUDIO.md §2`;〔觀察〕兩個按鈕無相依 |
| FR-8 | MCP 工具 `reason(prompt, think=true, heavy=false)` 回傳台灣正體文字 | P0 | 〔觀察〕`local_models_mcp.py:32-48` |
| FR-9 | MCP 工具 `taiwanize(text)` | P1 | 〔觀察〕`local_models_mcp.py:51-57` |
| FR-10 | MCP 工具 `generate_image(prompt, width=1024, height=1024)`,寬高須為 16 倍數 | P0 | 〔觀察〕`local_models_mcp.py:60-69` |
| FR-11 | MCP 工具 `speak(text)` 回傳 WAV 絕對路徑 | P0 | 〔觀察〕`local_models_mcp.py:72-79` |
| FR-12 | MCP 工具 `make_video(image_path, audio_path, script="")`,有 script 時附字幕 | P1 | 〔觀察〕`local_models_mcp.py:82-92` |
| FR-13 | MCP 工具 `list_models()` 回報各角色模型、是否已下載、目前載入記憶體的模型 | P2 | 〔觀察〕`local_models_mcp.py:95-107` |
| FR-14 | `taiwan_ai.py` CLI:`ask`(可 `--speak`/`--heavy`/`--no-think`)、`code`、`image`、`speak`、`auto` | — | 〔觀察〕程式仍存在 `taiwan_ai.py:89-171`。**棄用**(2026-10-22,使用者決定「不需要 CLI,只要網頁」;見 Q2)。程式移除須另開 CHG,導入不動程式 |
| FR-15 | 在 macOS ARM 重建 BreezyVoice(移除 CUDA/Linux 套件,以簡易數字正規化取代 WeTextProcessing) | P1 | 〔觀察〕`setup_breezyvoice.sh` |

優先級為〔推論〕:依 `PODCAST_STUDIO.md §1` 的五步流程定 P0,其餘依附屬程度定。待使用者確認。

### 4.1 v1.1 新增需求(2026-10-22,使用者提出;未實作)

> 來源:使用者指示「不需要 CLI 只要網頁」「網頁能配置模型 MCP 並且可以 drag and drop」「兩者都要可支援」(檔案拖放 + 節點拖拉編排),並選定:網頁架構 = FastAPI + 原生 JS;模型設定範圍 = 角色模型(與 MCP 共用)+ MCP 註冊管理。
> FR-1~7 的**功能**保留,**實作**由 Gradio 四分頁改為節點畫布(CHG-20261022-01)。

| 編號 | 需求 | 優先級 | 對應 CHG |
|------|------|--------|----------|
| FR-16 | 網頁以**節點畫布**編排流程:從節點清單拖出節點、拖線連接輸出→輸入;連線依型別(text / image / audio / video)檢查,型別不符不准連;按「執行」依拓撲順序**逐一**執行,每個節點顯示狀態(待執行/執行中/完成/失敗)與輸出預覽 | P0 | CHG-20261022-01 |
| FR-17 | 開啟網頁即載入**預設範本**,等同原四步流程(主題 → 文案 → 台灣用語 → 語音;標題 → 封面;封面 + 語音 + 文案 → 影片) | P0 | CHG-20261022-01 |
| FR-18 | 流程可存檔 / 載入(JSON,存 `workflows/`) | P1 | CHG-20261022-01 |
| FR-19 | **檔案拖放**:把 `.txt/.md`、`.png/.jpg/.webp`、`.wav/.mp3/.m4a` 拖到畫布空白處 → 建立對應型別的「檔案輸入」節點;拖到既有節點的輸入口 → 以該檔取代此輸入。上傳檔存 `outputs/uploads/` | P0 | CHG-20261022-01 |
| FR-20 | 文字節點的輸出可在節點上直接編輯,下游節點使用編輯後的文字(取代 FR-2 的文字框);「AI 修改」是一種節點 | P0 | CHG-20261022-01 |
| FR-21 | **模型設定頁**:每個角色(推理、重型推理、台灣用語、生圖、語音聲線)從 Ollama 已下載模型 / 已安裝工具中選擇,並設定參數(think、num_ctx、steps);存 `config/models.json`;網頁與 local-models MCP **共用同一份**,MCP 每次工具呼叫重讀 | P0 | CHG-20261022-01 |
| FR-22 | **MCP 註冊管理**:設定頁顯示 local-models 在 Claude Code / Codex 的註冊狀態,可註冊或移除;一律透過官方 CLI(`claude mcp …`、`codex mcp …`),不直接編輯 `~/.claude.json`、`~/.codex/config.toml` | P1 | CHG-20261022-02 |
| FR-23 | 設定頁可調整**審議席位模型**(DIR-1 的席位分配表),存在同一份 `config/models.json` | P2 | CHG-20261022-01 |
| FR-14 | (重申)CLI 棄用:移除 `taiwan_ai.py` 的 CLI 與 `code` 角色 | — | CHG-20261022-01 |

### 4.2 v1.2 新增需求(2026-10-22,使用者提出;未實作)

> 來源:使用者指示「畫面編輯採取影片編輯畫面」「可以設定說話語音中斷時間長度(增加停頓感)以及設定特定字詞或句的語調」,並選定:時間軸為主畫面、節點畫布改為「生成流程」分頁;語調 = 注音聲調標註(BreezyVoice 原生支援)。

| 編號 | 需求 | 優先級 | 對應 CHG |
|------|------|--------|----------|
| FR-24 | 主畫面為**影片編輯器**:素材庫(拖入檔案 + 生成流程產出)、預覽視窗(瀏覽器內即時播放畫面 + 語音 + 字幕,不必先渲染)、屬性面板、時間軸(畫面軌、語音軌、字幕軌)。節點畫布(FR-16)移到「生成流程」分頁 | P0 | CHG-20261022-04 |
| FR-25 | 語音**逐句分段合成**:文稿依句尾標點切成語音片段;改一句只重新合成該句 | P0 | CHG-20261022-04 |
| FR-26 | **停頓長度**:全域預設(逗號、句號、段落各一個秒數)+ 每個句間個別覆寫;可在時間軸上拖拉片段間空隙調整;範圍 0–5 秒,精度 0.1 秒 | P0 | CHG-20261022-04 |
| FR-27 | **聲調標註**:在文稿或字幕軌選取字詞,顯示自動注音,可改注音與聲調(一~四聲、輕聲);存為 BreezyVoice 的 `字[:注音]` 標記;只重新合成受影響的句 | P0 | CHG-20261022-04 |
| FR-28 | 字幕時間依**實際片段長度**計算(取代依字數比例估算);長句在片段內再切成 ≤ 24 字的字幕 | P1 | CHG-20261022-04 |
| FR-29 | 畫面軌可放多張圖,拖拉調整起訖;預設一張封面撐滿全長 | P1 | CHG-20261022-04 |
| FR-30 | 依時間軸**匯出** MP4(1920×1080)+ SRT;專案可存檔 / 載入,存檔保留最近 10 個版本 | P0 | CHG-20261022-04 |

### 4.3 v1.3 新增與修改(2026-10-23,使用者提出;未實作)

> 來源:使用者指示(2026-10-23):「語音部分語速太慢 要能設定某段的語速倍率」「不要用 tab 也不要在上面說明順序與本機說明」「每個步驟都是下一步」「需要 TAB 的只有建立 podcast、設定與免責說明授權」;並選定:節點畫布放進「設定」當進階、語速 = 全域預設 + 每句覆寫。

| 編號 | 需求 | 優先級 | 對應 CHG |
|------|------|--------|----------|
| FR-31 | 網頁**只有三個分頁**:建立 Podcast、設定、免責說明與授權;頁首與各頁**不放**步驟順序說明或「本機執行」之類的說明文字 | P0 | CHG-20261022-04 |
| FR-32 | 「建立 Podcast」為**步驟精靈**:每次只顯示一個步驟,只有「上一步 / 下一步」;步驟由「設定 › 進階流程」目前選用的流程決定,預設 ① 文稿 ② 語音 ③ 封面 ④ 剪輯(時間軸)⑤ 匯出;每一步都可拖入自己的檔案取代該步的 AI 產出 | P0 | CHG-20261022-04 |
| FR-33 | **語速倍率**:設定中有全域預設(預設 1.15×);語音步驟與剪輯步驟中每一句可個別覆寫,範圍 0.5–2.0×、精度 0.05;以 ffmpeg `atempo` 變速並保留音高;**只改語速不重新合成** | P0 | CHG-20261022-04 |
| FR-34 | 「免責說明與授權」分頁:AI 生成內容聲明、各模型與元件的授權(逐項列出,來源取自各模型卡 / LICENSE,取不到標「未知」)、聲線複製須取得聲音所有人同意的提醒 | P1 | CHG-20261022-04 |
| FR-16(修改) | 節點畫布由主畫面改放「設定 › 進階流程」,用來調整精靈的步驟與模型;一般使用不需要看到它 | P1 | CHG-20261022-01 |
| FR-24(修改) | 時間軸編輯器不再是主畫面,改為精靈的「④ 剪輯」步驟;素材庫、預覽、屬性、三軌時間軸的內容不變 | P0 | CHG-20261022-04 |

### 4.4 v1.5 修改(2026-10-26,使用者提出;未實作)

> 來源:使用者指示(2026-10-25/26):「可透過使用sqlite管理模型配置並且能在local網站中設定」「進入後首先會是兩個card 分別是精靈和完整模式 然後右上角有icon能進行設定」「如果模型的語音輸出整段會花比較時間 可以評估改採分批每句輸出來改善 實際上已測試後得到的結論為主 這樣在調整間隔也比較有彈性」;並選定:完整模式 = 單頁完整編輯器(文稿、語音、封面、時間軸同一畫面,無步驟);免責說明與授權 = 首頁底部連結(設定頁也可進入)。

| 編號 | 需求 | 優先級 | 對應 CHG |
|------|------|--------|----------|
| FR-21(修改) | 模型設定改存 SQLite(`config/studio.db`,不入 git),在網頁設定頁管理;網頁與 local-models MCP 仍共用同一份,MCP 每次工具呼叫重讀;既有 `config/models.json` 只在資料庫不存在時匯入一次 | P0 | CHG-20261022-01 修訂 2 |
| FR-25(修改) | 逐句合成定案為「逐句輸出、模型載入一次、G2PW `num_workers = 0`」,每句完成即可播放(依 2026-10-26 實測,見 CHG-20261022-04 修訂 2) | P0 | CHG-20261022-04 修訂 2 |
| FR-31(修改) | 取代「只有三個分頁」:首頁只有「精靈」「完整模式」兩張卡片,右上角設定圖示開啟設定頁,底部有「免責說明與授權」連結;網頁不使用分頁列;頁首與各頁**不放**步驟順序說明或「本機執行」之類的說明文字 | P0 | CHG-20261022-04 修訂 2、CHG-20261022-01 修訂 2 |
| FR-35 | 「完整模式」:單一畫面的完整編輯器,文稿、語音、封面、素材庫、預覽、屬性、時間軸同時可見,沒有上一步 / 下一步;與精靈共用同一份專案資料 | P0 | CHG-20261022-04 修訂 2 |
| FR-34(修改) | 「免責說明與授權」由分頁改為首頁底部與設定頁底部的連結開啟;內容不變 | P1 | CHG-20261022-04 修訂 2 |

## 5. 非功能需求

| 類別 | 要求 | 來源等級 |
|------|------|----------|
| 隱私 / 離線 | 推理、生圖、語音、影片全部在本機執行;不呼叫雲端 API | 〔觀察〕程式中對外連線只有 `localhost:11434`(Ollama);〔例外,觀察〕`setup_breezyvoice.sh` 會 `git clone` GitHub 與下載套件(建置期,非執行期) |
| 安全 | 網頁只監聽 `127.0.0.1`;MCP 只走 stdio,不開網路埠;Gradio `allowed_paths` 只開 `outputs/` | 〔觀察〕`podcast_studio.py:114`、`local_models_mcp.py:112` |
| 記憶體 | 24 GB 統一記憶體同時只跑一個重型工作;生圖前卸載 LLM(FLUX 峰值約 14–16 GB) | 〔觀察〕MCP 有全域鎖 `local_models_mcp.py:22`;〔觀察〕網頁沒有對應的鎖,見 ADP-2;〔觀察〕heavy 模型不在卸載清單,見 ADP-1 |
| 效能 | 語音 CPU 約 9 分鐘 / 每分鐘音檔;生圖約 20 秒;`reason(heavy, think)` 可能 >10 分鐘 | 〔推論〕數字來自 `PODCAST_STUDIO.md §6, §11` 與程式註解,**未在導入時量測** |
| 逾時 | LLM 請求 1800 秒;MCP client 端需設 `MCP_TOOL_TIMEOUT=1800000`(Claude Code)/ `tool_timeout_sec=1800`(Codex) | 〔觀察〕`taiwan_ai.py:68`;〔推論〕client 端設定見 `PODCAST_STUDIO.md §10.3`(在 repo 外,無法驗證) |
| MCP 協定 | 工具執行期間 stdout 必須導向 stderr | 〔觀察〕`local_models_mcp.py:25-29` |
| 可維護性 | 單檔 PEP 723 `uv run --script`,依賴宣告在檔頭 | 〔觀察〕 |
| 相容性 | macOS(STHeiti 字型路徑、`~/.local/bin/mflux-generate`、`/Users/weiss/.local/bin/uv` 絕對路徑) | 〔觀察〕`media.py:12`、`taiwan_ai.py:48`、`.claude/launch.json` |
| 離線前端(v1.1) | 前端免建置(本機沒有 node/npm);第三方 JS/CSS 一律存進 repo(`static/vendor/`),執行期不從 CDN 載入 | 使用者決定(2026-10-22) |
| 上傳(v1.1) | 只收白名單副檔名;單檔上限 200 MB;檔名由伺服器重新產生(不信任使用者檔名);只能讀寫 `outputs/` 之下,拒絕路徑穿越 | 新需求 FR-19 的安全要求 |
| 重型工作互斥(v1.1) | 網頁與 MCP **跨行程**共用一把檔案鎖,同時只跑一個重型工作(解 ADP-2) | 新需求 |
| 全域設定(v1.1) | 寫 repo 外設定(MCP 註冊)必須使用者在網頁上逐次按下確認,且只透過官方 CLI | 新需求 FR-22 |
| 開發流程(v1.4) | merge 前 CI 全綠:`document`、`chg`、`program`、`review` 四項 check 全部通過才能 merge 進 main;由 main ruleset 強制 | CHG-20261022-03 / DIR-2 |

## 6. 限制與假設

- 限制:
  - 〔觀察〕只能在 macOS 執行(硬寫字型路徑與工具路徑)
  - 〔觀察〕依賴本機 Ollama、`mflux-generate`、`tools/BreezyVoice/.venv`;三者都不在 repo 內
  - 〔推論,來源 `PODCAST_STUDIO.md §6`〕BreezyVoice 只有官方範例女聲;FLUX 畫不出中文字
- 假設:
  - 〔推論〕使用者是唯一操作者,網頁與 MCP 不會被多人同時使用
  - 〔推論〕每分鐘 250 字是台灣華語播報語速(`podcast_studio.py:25` 註解)
- 待確認(決策,需使用者回答;逐題處理):
  - **Q1** `podcast_studio.py` / `media.py` 目前標示「暫停、未端到端測試」(`PODCAST_STUDIO.md §11`)——仍在範圍內、要恢復嗎?建議:保留在範圍,以 ADP-6 的端到端驗收作為恢復的第一筆變更。
  - ~~**Q2** `taiwan_ai.py` 的 CLI 仍要維護嗎?~~ **已決定(2026-10-22,使用者)**:不需要 CLI,只要網頁。FR-14 棄用;`taiwan_ai.py` 保留為共用函式庫;CLI 程式(`ask`/`code`/`image`/`auto`/`main`)與 `code` 角色的移除走導入後的 CHG。
  - **導入期間使用者提出的新需求**(不屬於現況盤點,**不在導入 commit 內實作或定案**,導入後以 CHG 進 requirement-analysis):網頁能配置模型(與 MCP 共用)、網頁支援 drag and drop。
  - **Q3** 驗收標準的量化門檻(例:2 分鐘稿端到端幾分鐘內產出、字幕時間誤差上限)。建議:先以「端到端跑完且產出可播放 MP4 + SRT」為門檻,效能數字等量測後再訂。
  - **Q4** `PODCAST_STUDIO.md` 與本帳本的關係:保留為人寫的設計文件(本帳本引用它),或日後併入 `docs/structure/`?建議:保留不動,本帳本只引用。

## 7. 驗收條件

> 導入**不是**驗收(治理基準點第 6 條)。以下條件**全部未驗證**,狀態以 `docs/acceptance/` 的 ACC 為準。
> v1.6(2026-10-26,CHG-20261022-01 結構文件更新):網頁由 Gradio 四分頁改為 `studio_server.py`(首頁右上角 ⚙ → 設定 › 進階流程的節點畫布),驗收條件改寫為新介面;FR-1~7 的功能要求不變。

- [ ] FR-1/2/17/20:`uv run --script studio_server.py` 後從首頁 ⚙ 進入「設定 › 進階流程」,載入預設範本,填主題與標題按「執行」;2 分鐘稿字數落在 500 ± 20%,全文無簡體字(OpenCC 反向檢查);在文字節點上編輯輸出後該節點標為釘住,重跑時不重新產生、下游使用編輯值
- [ ] FR-3/11:合成語音產出可播放 WAV,長度 > 0;語音合成期間 BreezyVoice 子程序對外連線數為 0(ADP-12)
- [ ] FR-4/10:封面為 1920×1080 PNG,標題完整落在底部帶狀區(含長標題,見 ADP-3)
- [ ] FR-5/6/12:MP4 為 1920×1080 H.264/AAC,含一條 mov_text 字幕軌,另存 `.srt` 且最後一段結束時間 = 音檔長度(±0.5 秒)
- [ ] FR-16:從節點清單拖出節點並連線;型別不符的連線被移除並提示;不合法的流程圖送出執行時回 400 並指出節點與埠;每個節點顯示狀態與預覽,失敗節點的下游標「略過:上游失敗」
- [ ] FR-18:流程可存檔 / 載入;同名存檔須確認才覆寫
- [ ] FR-19:把 `.txt` 文稿拖到畫布空白處建立檔案輸入節點、拖到節點輸入口取代該輸入;白名單外的副檔名被拒;超過 200 MB 回 413
- [ ] FR-21/23:設定頁改推理模型並儲存後,MCP `list_models` 反映新值;`config/studio.db` 存在且 `git status --porcelain` 無輸出;否決席不能存成 `glm4:*`
- [ ] FR-8~13:MCP stdio client 列出 6 個工具,逐一呼叫成功,stdout 只有 JSON-RPC
- [ ] NFR 記憶體與互斥:網頁重型節點執行期間呼叫 MCP `generate_image` 會等待,兩個重型工作的執行區間不重疊;`reason(heavy=true)` 之後立即 `generate_image` 不 OOM(見 ADP-1、ADP-2)
- [ ] NFR 安全:錯誤 `Host` 的請求(含 GET)、缺 `X-Studio-Token` 或錯誤 `Origin` 的改變狀態請求一律 403;`/files/` 取不到 `outputs/` 以外的檔案
- [ ] FR-15:乾淨環境執行 `setup_breezyvoice.sh` 成功,正規化測試輸出含「兩千零二十六年」

## 8. AI 開發約定

- **進場**:每個 session 先讀 `AGENTS.md` → 本文件 → `docs/structure/*.md` → `docs/knowledge/INDEX.md`。
- **任何修改先走 modification-guide**,在 `docs/changes/` 開 `CHG-YYYYMMDD-NN.md`;第一筆受治理變更必須在 Adopt-commit 之後。
- **禁止替基準點以前補造 CHG**:基準點前沒有治理,這件事本身就是記錄。
- **§9 待補項不得被「順手」修掉**:要修就開 CHG,並在 CHG 引用 ADP 編號。
- **語言**:使用者面文字、程式註解、文件用繁體中文與台灣用語(沿用 `taiwan_ai.TW`)。
- **依賴**:維持 PEP 723 單檔 script 形式(`uv run --script`),新依賴寫進檔頭 `dependencies`。
- **MCP 工具**:工具內一律用 `busy()`(鎖 + stdout 導向 stderr);新增重型工具必須進同一把鎖。
- **不送資料出本機**:新增任何網路呼叫屬於高風險變更(違反 §5 隱私)。
- **產出物**寫到 `outputs/`,回傳絕對路徑;`outputs/` 不入 git。
- **`tools/BreezyVoice/` 的修改**一律落在 `patches/breezyvoice-mac/` 並由 `setup_breezyvoice.sh` 套用,不直接改 `tools/`。
- **結構設計圖**:預設畫給使用者看並等確認(使用者未表示要跳過)。
- **CI/CD(v1.4,CHG-20261022-03 / DIR-2)**:
  - 每個步驟一個 PR:開自己的分支(`chg/<CHG 編號>-<task>` 或 `docs/<主題>`),commit message 帶 CHG 編號(只改 `docs/knowledge/` 者除外),不直接 push main。
  - merge 前 `document`、`chg`、`program`、`review` 四項 check 必須全部通過。
  - 改到任何程式路徑的 PR,不論風險分級,merge 前一律等使用者核准;只改 `docs/`、`features/` 的 PR 可在 CI 綠後自動 merge。
  - 改到治理基礎設施路徑(`.github/`、`scripts/ci/`、`tests/ci/`、`.ai-sdlc/`、`scripts/local_review.py`、`scripts/deploy_local.sh`)的 PR,另需擁有者留言 `<!-- infra-approved sha=<head> -->`。
  - main 只允許 squash merge,squash 標題帶 CHG 編號。
  - 本機審議以 `scripts/local_review.py` 執行;DIR-1 第 10 條暫停 27B 期間,一律帶 `--codex-seats risk,security,compliance`。

## 9. 導入盤點:具名待補項

> 反推過程中發現的缺陷、空洞與債。**導入不修、也不追認**:每一項要修都要開 CHG 並引用編號;要結案要有 ACC。
> 來源等級:〔觀察〕= 程式路徑讀得到;影響若未重現則標〔推論〕。

| 編號 | 項目 | 證據 | 等級 | 影響 |
|------|------|------|------|------|
| ADP-1 | **由 CHG-20261022-01 處理**(T2:`unload_llms()` 改卸載 `/api/ps` 列出的全部模型;結案以 ACC 為準)。`unload_llms()` 只卸載 `MODELS.values()`;MCP `reason(heavy=true)` 在 `finally` 把 `MODELS["reason"]` 還原,於是 27B 模型(約 13 GB,`keep_alive` 10 分鐘)不在卸載清單 | `taiwan_ai.py:73-81`、`local_models_mcp.py:40-45`、`taiwan_ai.py:60` | 〔觀察〕路徑;〔推論〕OOM 未重現 | heavy 推理後 10 分鐘內呼叫 `generate_image`,27B + FLUX 14–16 GB 可能超過 24 GB |
| ADP-2 | **由 CHG-20261022-01 處理**(T4:跨行程 `heavy_lock()`,網頁與 MCP 共用 `outputs/.heavy.lock`;結案以 ACC 為準)。網頁沒有跨事件的鎖:文案(LLM)、語音(BreezyVoice)、封面(FLUX)三個事件各自排隊,可能同時執行;網頁與 MCP 兩個行程之間也沒有共用鎖 | `podcast_studio.py:104-110,114`(`queue()` 無全域 concurrency 設定);MCP 的鎖只在自己行程內 `local_models_mcp.py:22` | 〔觀察〕無鎖;〔推論〕Gradio 預設每個事件 concurrency 1、不同事件可並行 | 封面與文案同時跑時 LLM 未卸載 → 記憶體超量 |
| ADP-3 | `add_title()` 字級降到 48 仍超過 2 行時,迴圈結束但沿用 >2 行排版 | `media.py:42-52` | 〔觀察〕 | 長標題超出底部帶狀區或畫面 |
| ADP-4 | `speak()` 清 Markdown 的 regex 也刪掉 `-`、`_`、`|`(換成空白) | `taiwan_ai.py:130` | 〔觀察〕轉換;〔推論〕念法改變未重現 | 「2026-10-22」「COVID-19」被拆開,數字正規化結果改變 |
| ADP-5 | `setup_breezyvoice.sh` 以 `--depth 1` clone 上游 HEAD,未釘 commit | `setup_breezyvoice.sh:11` | 〔觀察〕 | 上游改版後 patches 可能失效,重建不可重現 |
| ADP-6 | **沒有任何自動化測試**;`PODCAST_STUDIO.md §11` 自述:網頁與 `media.py` 從未執行,MCP 的 `generate_image` / `speak` / `make_video` 未經 MCP 實測 | repo 內無 test 檔;`PODCAST_STUDIO.md §11` | 〔觀察〕 | §7 驗收條件全部無證據 |
| ADP-7 | MCP `make_video` 接受任意 `audio_path`,但帶 `script` 時以 `wave` 模組讀長度 | `media.py:74-76,116`、`local_models_mcp.py:83` | 〔觀察〕 | 傳入 MP3 / M4A 並附 script → `wave.Error`,工具失敗 |
| ADP-8 | **由 CHG-20261022-01 處理**(T2:`stamp()` 加毫秒與隨機碼,輸出檔以 `reserve()` 排他建立;結案以 ACC 為準)。輸出檔名以秒為單位的時間戳;同一秒內同前綴的兩次呼叫會覆寫 | `taiwan_ai.py:84-86` | 〔觀察〕;〔推論〕單人使用機率低 | 網頁與 MCP 同時 `speak` 時可能互蓋 |
| ADP-9 | `speak()` 子程序環境只給 `PYTHONUTF8`、`PATH`,沒有 `HOME` | `taiwan_ai.py:139` | 〔觀察〕;〔未知〕是否影響(文件稱 CLI 曾成功) | 依賴 `HOME` 的快取路徑(HF / matplotlib)可能寫到非預期位置或失敗 |
| ADP-10 | **已失效**:`podcast_studio.py` 已隨 CHG-20261022-01 T13 刪除,新版 `make-video` 節點的參數名為 `subtitles`。原內容:網頁勾選框變數名 `burn_subs`(燒錄),實作是軟字幕 mov_text;行為與 `PODCAST_STUDIO.md §8 #3` 一致,只是命名誤導 | `podcast_studio.py:62,99` | 〔觀察〕 | 後續修改者可能誤以為是硬字幕 |
| ADP-11 | `PODCAST_STUDIO.md` 標示「MCP 伺服器已實作並測試」等狀態——是**自述**,導入時未重驗 | `PODCAST_STUDIO.md` 開頭與 §11 | 〔觀察〕 | 不得當作驗收證據;已盤點 ≠ 已驗收 |
| ADP-12 | **由 CHG-20261022-01 處理**(T4:`taiwan_ai.speak()` 子程序環境加入 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`;CHG-20261022-04 的 `breezy_worker` 另行處理;結案以 ACC 為準)。BreezyVoice 每次載入模型都呼叫 `snapshot_download`(預設 `--model_path` 是 Hugging Face repo id `MediaTek-Research/BreezyVoice-300M`,不是本機路徑);G2PW 以 `BertTokenizer.from_pretrained('bert-base-chinese')` 載入分詞器;`speak()` 的子程序環境沒有 `HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE` | `tools/BreezyVoice/single_inference.py:207`(呼叫)、`:400`(預設值);`tools/BreezyVoice/cosyvoice/cli/cosyvoice.py:27`;`tools/BreezyVoice/.venv/lib/python3.10/site-packages/g2pw/api.py:84`;`taiwan_ai.py:194`(env 只有 `PYTHONUTF8`、`PATH`) | 〔觀察〕路徑;〔推論〕執行期實際連線未以封包觀察重現(2026-10-26 新增,非導入時盤點) | 每次語音合成都可能連線 Hugging Face 查詢模型版本,與 §5「不送資料出本機」衝突;修正規劃:CHG-20261022-01 T4(`speak()`)、CHG-20261022-04 T1(`breezy_worker`)設定兩個離線變數 |

## 10. 導入記錄(治理基準點)

- 基準點:`7da165b6048897abb8acac4bba69db9677827471`(導入當下所見的完整 SHA;為取得錨點,本 session 於 2026-10-22 先 `git init` 並把既有檔案原樣提交成此 commit,**內容未做任何修改**)
- 導入 commit:`e7b1d0cc03ee52e6208d0617717659cf579bcf94`(parent = 基準點;只含治理檔)。SHA 於下一筆 commit 補入表頭——導入 commit 不能包含自己的 SHA。`AGENTS.md` 與 `docs/acceptance/` 不在導入 commit 白名單內,也在下一筆加入
- 工作樹狀態(導入當下,基準 commit 之後):
  - 未提交變更:無
  - 未追蹤檔:無(`git status --porcelain` 只列出 ignored)
  - Ignored:`outputs/`(約 2.6 MB:`test_tts.wav`、`anime_pinup.png`、`test_flux.png`)、`tools/`(約 1.9 GB:`tools/BreezyVoice/`,含 `.venv` 1.2 GB、`G2PWModel` 606 MB;**沒有 `.git`**,無法得知對應的上游 commit,見 ADP-5)
  - Submodule:無
  - Git LFS:無
- 導入前的歷史:無(repo 在導入時才 `git init`)。導入前沒有治理,不補造 CHG。

## 11. 變更歷程

| 版本 | 日期 | 內容 | 依據 |
|------|------|------|------|
| v1.0 | 2026-10-22 | 導入反推(現況盤點) | Adopt-commit `e7b1d0cc03ee52e6208d0617717659cf579bcf94` |
| v1.1 | 2026-10-22 | FR-14 棄用(CLI);新增 FR-16~23(節點畫布、檔案拖放、模型設定、MCP 註冊管理、審議席位設定);新增 v1.1 非功能需求 | 使用者指示;CHG-20261022-01、CHG-20261022-02 |
| v1.3 | 2026-10-23 | 新增 FR-31~34(三分頁、步驟精靈、語速倍率、免責說明與授權);修改 FR-16(節點畫布移入設定)、FR-24(時間軸改為精靈步驟) | 使用者指示;CHG-20261022-01、CHG-20261022-04 修訂 1 |
| v1.2 | 2026-10-22 | 新增 FR-24~30(影片編輯器時間軸、逐句合成、停頓長度、聲調標註、實際長度字幕、多圖畫面軌、匯出與專案存檔);FR-16 節點畫布改為「生成流程」分頁 | 使用者指示;CHG-20261022-04 |
| v1.5 | 2026-10-26 | 修改 FR-21(設定改存 SQLite)、FR-25(逐句合成定案 B2)、FR-31(首頁雙卡片 + 設定圖示,取代三分頁)、FR-34(免責說明改為連結);新增 FR-35(完整模式);§9 新增 ADP-12(BreezyVoice 可能連線 Hugging Face) | 使用者指示;2026-10-26 實測與查證;CHG-20261022-01、CHG-20261022-04 修訂 2 |
| v1.4 | 2026-10-23 | §5 新增「merge 前 CI 全綠」;§8 新增 CI/CD 約定(每步驟 PR、四項檢查、程式變更使用者核准、infra-approved、只允許 squash、暫停 27B 期間的 `--codex-seats`) | CHG-20261022-03 結構文件更新;DIR-1、DIR-2 |
| v1.6 | 2026-10-26 | §7 驗收條件改寫為新介面(`studio_server.py`、設定 › 進階流程、檔案拖放、設定資料庫);§9 ADP-1、ADP-2、ADP-8、ADP-12 標「由 CHG-20261022-01 處理」(結案以 ACC 為準),ADP-10 隨 Gradio 版刪除失效 | CHG-20261022-01 結構文件更新 |
