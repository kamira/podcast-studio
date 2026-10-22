# 目錄結構

> 導入反推(2026-10-22,基準點 `7da165b6048897abb8acac4bba69db9677827471`)。來源等級見 `docs/ai-guideline.md` 表頭。

## 樹狀

```
podcast-studio/
├── podcast_studio.py          # 入口:Gradio 網頁
├── local_models_mcp.py        # 入口:MCP 伺服器(stdio)
├── taiwan_ai.py               # 模型層 + CLI 入口
├── media.py                   # 媒體層
├── setup_breezyvoice.sh       # BreezyVoice 重建
├── patches/
│   └── breezyvoice-mac/
│       ├── requirements-mac.txt
│       ├── build-constraints.txt
│       └── tn/                # 取代 WeTextProcessing 的數字正規化
│           ├── chinese/normalizer.py
│           └── english/normalizer.py
├── PODCAST_STUDIO.md          # 人寫的設計文件(導入前即存在)
├── AGENTS.md                  # 治理入口(導入新增)
├── docs/                      # 治理帳本(導入新增)
│   ├── ai-guideline.md
│   ├── structure/{directory,logical,design,data}.md
│   ├── changes/               # CHG-YYYYMMDD-NN.md
│   ├── acceptance/            # ACC-YYYYMMDD-NN.md
│   └── knowledge/{INDEX.md,vocabulary.json}
├── .claude/launch.json        # Claude 桌面 app 啟動網頁
├── .gitignore
├── outputs/                   # 產出物(ignored)
└── tools/BreezyVoice/         # 第三方 repo + .venv(ignored,由腳本重建)
```

## 各目錄職責

| 路徑 | 職責 | 備註 | 等級 |
|------|------|------|------|
| `/`(根目錄 `.py`) | 全部程式碼;平鋪,沒有 package | 入口檔以 `sys.path.insert` 互相 import | 〔觀察〕 |
| `patches/breezyvoice-mac/` | 對第三方 BreezyVoice 的 macOS 調整 | 由 `setup_breezyvoice.sh` 複製進 venv site-packages | 〔觀察〕 |
| `docs/` | 治理帳本 | 落點宣告在 Guideline 表頭 | 〔觀察〕 |
| `outputs/` | 產出檔 | `.gitignore` | 〔觀察〕 |
| `tools/` | 第三方工具與 venv(約 1.9 GB) | `.gitignore`;無 `.git` | 〔觀察〕 |
| `.claude/` | Claude 桌面 app 設定 | `launch.json` 用 `uv` 絕對路徑 | 〔觀察〕 |

## 命名與放置規則

- 〔觀察〕Python 檔 snake_case,平鋪在根目錄;共用邏輯放 `taiwan_ai.py`(模型)或 `media.py`(媒體)。
- 〔觀察〕對 `tools/` 下第三方程式的修改一律放 `patches/<工具>-<平台>/`,不直接改 `tools/`。
- 〔觀察〕產出檔一律 `outputs/<種類>-<stamp>.<副檔名>`。
- 〔推論〕新增入口(新介面)應沿用「單檔 PEP 723 script + import 共用模組」;〔未知〕專案成長後是否改成 package(目前 4 檔、521 行,無此需要)。
- 〔觀察〕測試目錄不存在(ADP-6)。
