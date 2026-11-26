# 模式記錄(KN)

> 索引在 `docs/knowledge/INDEX.md`。

## KN-1 — CHG 全文不得出現 `doc_integrity_check` 的狀態觸發字

- tier:shallow
- 日期 / 分支:2026-10-23(UTC+0)/ 全分支
- tags/scope:cicd, review
- 規則:草稿或施工中的 CHG,全文(不只「## 狀態」節)一律不寫 `已實作`、`已驗收`、`implemented`、`accepted`、`待驗收`、`pending acceptance` 這些字樣,也不寫 PEM 私鑰區塊開頭行的原文;需要時改寫(例:「驗收完成」「做完了」「PEM 私鑰區塊的開頭行」)。
- 原因:vendored `doc_integrity_check.classify_status()` 對**整份檔案**做子字串比對;只要出現上述字樣,即使狀態是「草稿」也會被判成已實作並要求 ACC。2026-10-23 實例:套件名 `opencc-python-re`+`implemented`、規則描述「略過狀態為已驗收者」、引用的模型判決「已實作」,讓 CHG-01/03/04 全部被誤判;`check_secrets` 也把計畫中的私鑰樣本行當成秘密。
- 處置邊界:vendored 腳本不改(CHG-20261022-03 全域約束);這是上游 ai-sdlc-suite 的缺陷,修正應在上游進行。
- 觸發證據:CHG-20261022-01、CHG-20261022-03、CHG-20261022-04(同一次 doc_integrity_check 失敗)
- 計數:seen 1 / applied 1 / last-applied 2026-10-23
- 狀態:觀察中

## KN-2 — CHG 收尾時「## 狀態」首行要寫「已收尾」

- tier:shallow
- 日期 / 分支:2026-11-02(UTC+0)/ 全分支
- tags/scope:cicd, review
- 規則:CHG 收尾時,「## 狀態」節的第一個非空行要含「已收尾」(例:「已收尾(2026-11-02)——驗收報告 ACC-…判定通過」),或與其他筆相同、以「已」與「驗收」連寫開頭的寫法;不要只寫「收尾(…)」。這一行只在收尾時寫;草稿與施工中的 CHG 仍依 KN-1 不寫觸發字(「已收尾」不是 KN-1 觸發字)。
- 原因:上游 ai-sdlc-suite 外掛的 SessionStart hook(`hooks/session_start.py`,v1.68.1)只看「## 狀態」節第一個非空行,不含它認得的完成字樣就列入「未收尾 CHG」並提示先收尾。2026-11-02 實例:CHG-20261022-02、CHG-20261030-02、CHG-20261101-01 首行只寫「收尾(…)」,三筆的驗收都判定通過,卻被列為未收尾。
- 處置邊界:上游 hook 不改(同 KN-1);由本 repo 的帳本用字配合,修正見 CHG-20261102-01。
- 觸發證據:CHG-20261022-02、CHG-20261030-02、CHG-20261101-01(同一次 SessionStart hook 誤報)
- 計數:seen 1 / applied 1 / last-applied 2026-11-02
- 狀態:觀察中

## KN-3 — 帳本與文件中的家目錄路徑一律寫成 `~`,不寫作業系統帳號名稱

- tier:shallow
- 日期 / 分支:2026-11-24(UTC+0)/ 全分支
- tags/scope:security, review
- 規則:寫進 repo 的任何文字(CHG、ACC、Guideline、KN、結構文件、PR 說明)出現本機路徑時,家目錄一律寫成 `~`,不寫含作業系統帳號名稱的絕對路徑:macOS `/Users/<帳號>/…` → `~/…`;Linux / WSL `/home/<帳號>/…` → `~/…`;Windows `C:\Users\<帳號>\…`(含 8.3 短檔名形式 `<帳號前幾字>~1`)→ `~/…`(斜線一律寫 `/`,例:`~/AppData/Local/Temp/…`)。代理工作暫存目錄沿用既有寫法 `…/scratchpad/…`。同一份記錄同時出現兩個平台的家目錄時,仍寫 `~`,並在該段的環境說明註明是哪一個平台。貼命令輸出當證據時,先改寫路徑前綴再貼,其餘輸出逐字保留;若要表達「輸出不含帳號路徑」這類檢查,寫成 `/Users/<帳號>` 等佔位寫法,不寫真實帳號。
- 原因:ACC 是驗收證據,驗收者習慣把命令輸出原樣貼上(pytest 警告摘要、`rustup show`、`cargo install`、`lsof`、hook 的 `cwd` 等),輸出內含家目錄絕對路徑,帳號名稱因此進入公開 repo。2026-11-24 審視發現 15 份 ACC、2 份 CHG 與 Guideline 共 49 行(ACC 45 行、CHG 3 行、Guideline 1 行)含兩個平台(macOS、WSL Linux)的帳號路徑;使用者指示「改成~」。CHG-20261022-03 安全影響 (h) 原先「已知且接受」本機路徑公開,本條取代該接受。
- 檢查:提交帳本前在 repo 根目錄執行 `git grep -n -I -E '/home/[a-z][a-z0-9_-]*/|/Users/[A-Za-z][A-Za-z0-9._-]*|[A-Za-z]:[/\]+Users[/\]+[A-Za-z]|[A-Z0-9]{1,6}~[0-9][/\]' -- docs`,應無輸出;有輸出時逐行判斷(CI runner 帳號、測試用假帳號不算個人帳號,可保留,但仍以改寫為佳)。
- 處置邊界:只規範文字寫法;git 歷史中既有的路徑不改寫(改寫歷史要強制推送,屬不可逆操作,使用者未要求)。自動化檢查(把上述樣式加進 CI 或秘密掃描)另開 CHG,見 CHG-20261124-02 代決 4。`PODCAST_STUDIO.md`、`.claude/launch.json` 這類實際要執行的設定與指令,`~` 不一定會被展開,改寫前須確認可用,見 CHG-20261124-02 代決 3。
- 觸發證據:ACC-20261122-01、ACC-20261123-01、ACC-20261124-01 等 15 份 ACC(CHG-20261124-02「影響範圍」逐檔列出)
- 計數:seen 1 / applied 1 / last-applied 2026-11-24
- 狀態:觀察中

## KN-4 — 在 Windows 上 clone 一律關閉換行轉換,提交時逐檔 stage

- tier:shallow
- 日期 / 分支:2026-11-25(UTC+0)/ 全分支
- tags/scope:cicd
- 規則:在 Windows 開發機為本 repo 建工作副本時,一律 `git clone -c core.autocrlf=false <repo>`(既有副本先確認 `git config core.autocrlf` 為 `false`);提交時只以檔名逐一 `git add <路徑>`,不用 `git add -A`、`git add .` 或 `git commit -a`;commit 前以 `git diff --cached --stat` 確認只有預期的檔案、行數合理。派發 Windows 上的施工者、起草者、驗收者時,派發提示寫明這兩點。
- 原因:repo 內檔案一律 LF;Windows 的 Git 預設 `core.autocrlf=true` 會在工作副本把檔案換成 CRLF,整批 stage 時就把只有換行差異的檔案全部提交。2026-11-25 實例:CHG-20261124-01 建整合分支時,派發者把工作副本的 CRLF 換行整批提交為 `399f6d4`(181 檔只有換行差異)並推上整合分支,隨即以還原 commit `339de92` 把樹還原(未強制推送、未改寫歷史;見該 CHG「## 派發記錄」整合分支列)。同日稍後又險些重犯(派發者轉述,未進 repo)。
- 處置邊界:只規範工作方式;不新增 `.gitattributes` 或 CI 檢查(若要做,另開 CHG;`.github/`、`scripts/ci/` 屬治理基礎設施)。
- 觸發證據:CHG-20261124-01「## 派發記錄」整合分支列(`399f6d4`、`339de92`)
- 計數:seen 2 / applied 1 / last-applied 2026-11-25
- 狀態:觀察中
