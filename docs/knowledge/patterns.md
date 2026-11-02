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
