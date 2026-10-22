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
