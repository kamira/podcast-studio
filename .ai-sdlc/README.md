# .ai-sdlc — vendored 治理腳本

本目錄收錄 ai-sdlc 治理腳本的原樣副本,讓 CI 與本機不必依賴使用者機器上安裝的 plugin 即可執行 doc-integrity、plan-check 等檢查。由 CHG-20261022-03 T1 導入。

**本目錄下 `skills/` 內的檔案一律不修改內容。** 若需調整行為,請回上游修改後依「更新方式」重新複製。

## 版本與來源

| 項目 | 內容 |
|---|---|
| 套件 | ai-sdlc-suite |
| 版本 | v1.66.0 |
| Skill | `ai-sdlc-autopilot` |
| 上游 repo | `kamira/ai-skills`(private) |
| 複製來源(本機安裝路徑) | `/Users/weiss/Library/Application Support/Claude/local-agent-mode-sessions/e5fd91f1-cf0f-4a28-b32b-3955d755bbb2/28913395-86a9-4305-90fc-3d5369bdcad1/rpm/plugin_01REFttVfuvzc7y6YFi4pwBW/skills/ai-sdlc-autopilot/` |
| 導入日期 | 2026-10-23 |

## 收錄範圍

| 目的地 | 來源 | 說明 |
|---|---|---|
| `skills/ai-sdlc-autopilot/scripts/` | `scripts/`(含 `lib/`) | 排除 `test_*.py` 與 `__pycache__/` |
| `skills/ai-sdlc-autopilot/assets/` | `assets/` | 全部複製 |

未複製 `references/`、`evals/`、`SKILL.md` 等其他目錄與檔案:`doc_integrity_check.py` 與 `autopilot_runner.py plan-check` 在僅有 `scripts/` 與 `assets/` 的情況下即可正常執行(exit 0),故不需要。

## 驗證完整性

`SHA256SUMS` 記錄每個 vendored 檔案的 SHA-256(路徑相對於 `.ai-sdlc/`,依路徑排序;不含本 README 與 `SHA256SUMS` 本身)。在 `.ai-sdlc/` 目錄內執行:

```bash
cd .ai-sdlc
shasum -a 256 -c SHA256SUMS
```

每一行皆應顯示 `OK`;任何 `FAILED` 代表檔案被改動過。

## 更新方式

1. **另開 CHG**(`docs/changes/CHG-YYYYMMDD-NN.md`),說明升級版本與原因。
2. 刪除 `skills/ai-sdlc-autopilot/scripts/` 與 `assets/`,自新版本的安裝路徑重新複製(同樣排除 `test_*.py` 與 `__pycache__/`),例如:

   ```bash
   rsync -a --delete --exclude '__pycache__/' --exclude 'test_*.py' \
     "<新版 skill 路徑>/scripts" "<新版 skill 路徑>/assets" \
     .ai-sdlc/skills/ai-sdlc-autopilot/
   ```

3. 重建 `SHA256SUMS`:

   ```bash
   cd .ai-sdlc
   find skills -type f | LC_ALL=C sort | xargs shasum -a 256 > SHA256SUMS
   shasum -a 256 -c SHA256SUMS
   ```

4. 更新本 README 的版本、來源路徑與導入日期,並在 CHG 內記錄測試結果。

## 授權

上游 `kamira/ai-skills` 以 MIT 授權釋出,全文如下(取自 `gh api repos/kamira/ai-skills/license`):

```
MIT License

Copyright (c) 2026 kamira

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
