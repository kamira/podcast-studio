#!/usr/bin/env python3
"""
bilingual_check.py — en↔zh-tw 雙語結構漂移檢查 / bilingual structural drift check

本 skill 的每份文件都有 `.md`(英)與 `.zh-tw.md`(繁中)兩份,靠人工同步——本腳本把
「兩語言結構是否還平行」變成機器可驗:比對每對檔案的 章節標題數(##/###)、程式碼區塊數,
不比語意(語意一致性交給 reviewer)。結構數對不上,幾乎必然是某次只改了單邊。

用法:
  python3 bilingual_check.py --skill-dir skills/ai-sdlc
退出碼:0 = 通過;1 = 有漂移;2 = 參數錯誤。
"""
from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path

# 釘住輸出編碼(CHG-20260803-01 T1):不依賴主控台/locale 的 ambient 編碼。
# 非 UTF-8 主控台(如 Windows cp932)印 CJK/emoji 會 UnicodeEncodeError;
# 釘住後同一份程式在任何平台的輸出行為一致。errors="replace" 確保永不因輸出而崩潰。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def profile(f: Path) -> dict:
    text = f.read_text(encoding="utf-8", errors="ignore")
    return {
        "h2": len(re.findall(r"^## ", text, re.MULTILINE)),
        "h3": len(re.findall(r"^### ", text, re.MULTILINE)),
        "fence": len(re.findall(r"^```", text, re.MULTILINE)),
    }


def pairs(skill_dir: Path):
    for en in sorted(skill_dir.rglob("*.md")):
        if en.name.endswith(".zh-tw.md"):
            continue
        zh = en.with_name(en.name[:-3] + ".zh-tw.md")
        yield en, zh


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill-dir", default=".")
    args = ap.parse_args(argv[1:])
    root = Path(args.skill_dir).resolve()
    if not root.is_dir():
        print(f"找不到目錄:{root}")
        return 2

    problems = []
    checked = 0
    for en, zh in pairs(root):
        if not zh.exists():
            problems.append(f"{en.relative_to(root)} 缺對應的 zh-tw 版")
            continue
        checked += 1
        pe, pz = profile(en), profile(zh)
        diffs = [f"{k}: en={pe[k]} zh={pz[k]}" for k in pe if pe[k] != pz[k]]
        if diffs:
            problems.append(f"{en.relative_to(root)} ↔ zh-tw 結構不平行({'; '.join(diffs)})— 多半是只改了單邊")

    # **反方向:只有中文側的孤兒譯稿**(待補項 #61)。
    #
    # `pairs()` 只從英側出發,而它第一件事就是 `continue` 掉所有 `.zh-tw.md`
    # ——於是一支沒有英側的譯稿**整個不會被 yield,永遠不會被檢查**。
    # 這道閘因此只守一個方向,而另一個方向是無聲的。
    #
    # 同一類問題(配對破缺,單邊動了),所以同一個退出碼、同一份清單;
    # **但措辭分開**,因為修復動作方向相反:缺譯是「把譯本補上」,
    # 孤兒多半是「英側改名或刪除時沒帶上譯本」——修法是改英側或刪孤兒,不是補譯。
    for zh in sorted(root.rglob("*.zh-tw.md")):
        if not zh.with_name(zh.name[:-len(".zh-tw.md")] + ".md").exists():
            problems.append(f"{zh.relative_to(root)} 缺對應的英文版"
                            f"(**孤兒譯稿**——多半是英側改名或刪除時沒同步)")

    if problems:
        print(f"❌ 雙語檢查未通過(共查 {checked} 對):")
        for p in problems:
            print(f"  - {p}")
        print("\n請把落後的那一邊補到與另一邊平行(語意由審閱者把關,本檢查只看結構)。")
        return 1
    print(f"✅ 雙語檢查通過({checked} 對檔案結構平行:##/###/``` 數一致)。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
