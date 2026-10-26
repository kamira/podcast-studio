# static/vendor — 第三方前端檔

本目錄只放 Drawflow 一套第三方前端檔(CHG-20261022-01 前置 P1;Global Constraints「唯一第三方前端檔是 `static/vendor/drawflow.*`」)。網頁執行時一律從本機 `/static/vendor/` 載入,不經 CDN。

## Drawflow

| 項目 | 內容 |
|------|------|
| 套件 | `drawflow`(npm) |
| 版本 | 0.0.60(固定版本;下載當時 npm `latest` 標籤所指版本,發布於 2024-09-03) |
| 來源 | `https://registry.npmjs.org/drawflow/-/drawflow-0.0.60.tgz`(取其中 `package/dist/` 的兩個檔) |
| tarball 完整性 | npm `integrity` `sha512-UGj1+1DnWCVY6vJXBb6Ayt7KpPINBGqncVqpYDumVeBdIKxlBGn0tksp2yciVzQjkYsjFwMMDEdoVyWzUa2wkA==`、`shasum` `313eadb43190a04ebba16d2cb49d1c9516030a15`(下載後已比對相符) |
| 上游專案 | `https://github.com/jerosoler/Drawflow` |
| 授權 | MIT(Copyright (c) 2020 Jero Soler);全文見同目錄 `LICENSE.drawflow`,亦附於下方 |
| 下載日期 | 2026-10-26(UTC+0) |
| 同意紀錄 | 使用者於 CHG-20261022-01 確認閘(2026-10-23)同意下載 Drawflow 一次 |

### 檔案與 SHA-256

`tests/test_static.py` 會比對下表與實際檔案;更新版本時必須同時改這張表。

| 檔案 | 來源路徑(tarball 內) | SHA-256 |
|------|------------------------|---------|
| `drawflow.min.js` | `package/dist/drawflow.min.js` | `caf8c4f15b75e27169632ed2122715e52aeb5930b9adca6f519bd82a485a31e2` |
| `drawflow.min.css` | `package/dist/drawflow.min.css` | `57e5b37f72d95f97597263f17ef0ae9f0a0cd7b966e039b9f43508040d5dedf2` |
| `LICENSE.drawflow` | `package/LICENSE` | `daf3d5445e32f01fd8c1fa52d1956daae15c506ef8a657b189837f60c2c6639c` |

### CSP 相容性(下載時閱讀原始碼確認)

伺服器的 CSP 為 `default-src 'self'; media-src 'self' blob:; img-src 'self' blob: data:`(不允許 `unsafe-eval`、`unsafe-inline`)。

- `drawflow.min.js` 不含 `eval(`、`Function(` 建構式、`document.write`、`fetch` / `XMLHttpRequest`,不需要 `unsafe-eval`,也不發出任何網路請求;其中的 `http://www.w3.org/2000/svg` 只是 `createElementNS` 的 SVG 命名空間字串,不是網路位址;`import(` 是 Drawflow 自己的 `import()` 方法(載入 JSON 匯出),不是動態載入模組。
- 樣式一律以 `element.style.xxx = …`(CSSOM)設定,不寫 `style` 屬性字串,不受 `style-src` 限制。
- `innerHTML` 只用於清空容器、刪除鈕的「x」,以及 `addNode(..., typenode=false)` 時插入呼叫端給的 HTML。本專案的 `static/app.js` 一律以 `typenode=true` 搭配 `registerNode()` 傳入 DOM 節點(cloneNode),不經 `innerHTML` 插入任何內容。
- `drawflow.min.css` 不含 `url(` 或 `@import`,不載入外部資源。

### 授權全文

```
MIT License

Copyright (c) 2020 Jero Soler

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
