# -*- coding: utf-8 -*-
"""時間軸計算(CHG-20261022-04 T4)。

`layout(project)` 依專案資料算出語音片段、字幕 cue、畫面軌的起訖與總長;預覽(T7)與匯出(T5)共用
同一份結果,避免兩邊不一致。純函式:只讀傳入的 `project.Project`,不讀檔、不改動專案。

回傳:
  {"segments": [{"id", "start", "end"}],               依專案片段順序,每個片段一筆
   "cues":     [{"segment", "start", "end", "text"}],  字幕;"segment" 為所屬片段 id
   "images":   [{"id", "file", "start", "end"}],       畫面軌;"file" 為圖片素材 id(不是路徑)
   "duration": 總長}
所有時間單位為秒,一律以整數毫秒累加後換算(取到 0.001 秒,不累積浮點誤差)。

規則:
  - 片段長度 = 合成長度(audio.seconds,變速前)÷ 實際語速(`project.effective_speed`),即變速後長度。
    (CHG-20261029-01 T3)後製有效(`project.is_post_fresh`)時改取後製後長度 post.seconds;
    post 過期時退回 audio.seconds。預覽與匯出共用(唯一來源)。
  - 片段起點 = 前一個有音檔片段的終點 + 該片段之後的停頓(`project.effective_pause`)。
  - 總長 = 最後一個有音檔片段的終點;最後一段之後的停頓不計入(片尾不留靜音)。
  - 沒有音檔的片段(尚未合成)長度為 0、放在目前位置,本身的停頓不計、不產生字幕;
    不丟錯誤,讓預覽在合成途中也能排出已完成的片段。匯出(T5)須自行拒絕仍有片段沒有音檔的專案。
  - 字幕 = 片段;片段文字超過 CUE_MAX_CHARS 字時切成 ceil(字數 ÷ CUE_MAX_CHARS) 個 cue,
    切點優先放在理想位置前後 CUE_SNAP 字內的標點之後,各 cue 時間依字數比例分配,
    第一個 cue 起點等於片段起點、最後一個 cue 終點等於片段終點;cue 文字去掉尾端標點與空白,
    去掉後為空的 cue 不輸出。
  - 畫面軌依起點排序;end 為 null 時撐到總長;終點超出總長截到總長,起點不在總長之內的圖片丟棄。
    圖片起訖在專案檔已四捨五入到 0.1 秒(`project.validate`);匯出時再取到 0.1 秒由 T5 處理。
"""
from __future__ import annotations

import math

import project as pj

CUE_MAX_CHARS = 24
CUE_SNAP = 4
CUE_BREAKS = frozenset("，、,;；:：…—　 \t")
CUE_TRIM = "。，、,.;；:：　 \t"


def _ms(seconds) -> int:
    """秒 → 整數毫秒(四捨五入,0.5 進位)。"""
    return int(math.floor(float(seconds) * 1000 + 0.5))


def _sec(ms: int) -> float:
    return ms / 1000


def split_cue_text(text: str) -> list[str]:
    """把片段文字切成每段 ≤ CUE_MAX_CHARS 字的字幕文字(原文不增刪,串接後等於原文)。"""
    n_chars = len(text)
    if n_chars <= CUE_MAX_CHARS:
        return [text]
    n = math.ceil(n_chars / CUE_MAX_CHARS)
    cuts, prev = [], 0
    for k in range(1, n):
        left = n - k  # 切點之後還要分出的段數
        lo = max(prev + 1, n_chars - CUE_MAX_CHARS * left)
        hi = min(prev + CUE_MAX_CHARS, n_chars - left)
        ideal = min(max(round(k * n_chars / n), lo), hi)
        snaps = [c for c in range(max(lo, ideal - CUE_SNAP), min(hi, ideal + CUE_SNAP) + 1)
                 if text[c - 1] in CUE_BREAKS]
        cut = min(snaps, key=lambda c: (abs(c - ideal), -c)) if snaps else ideal
        cuts.append(cut)
        prev = cut
    bounds = [0, *cuts, n_chars]
    return [text[a:b] for a, b in zip(bounds, bounds[1:])]


def _share(span: int, part: int, total: int) -> int:
    """span × part ÷ total 的整數毫秒(四捨五入,0.5 進位,與 _ms 一致)。"""
    return (2 * span * part + total) // (2 * total)


def _cues(seg_id: str, text: str, start: int, end: int) -> list[dict]:
    parts = split_cue_text(text)
    total = sum(len(p) for p in parts) or 1
    out, done = [], 0
    for i, part in enumerate(parts):
        a = start + _share(end - start, done, total)
        done += len(part)
        b = end if i == len(parts) - 1 else start + _share(end - start, done, total)
        shown = part.strip().rstrip(CUE_TRIM)
        if shown:
            out.append({"segment": seg_id, "start": _sec(a), "end": _sec(b), "text": shown})
    return out


def segment_seconds(seg: dict) -> float:
    """片段長度(變速前):後製有效時取後製後長度(CHG-20261029-01 D18),否則退回合成 / 自錄長度。"""
    if pj.is_post_fresh(seg):
        return seg["post"]["seconds"]
    return seg["audio"]["seconds"]


def layout(project) -> dict:
    """依專案算出片段、字幕 cue、畫面軌與總長(規則見模組說明)。"""
    segments, cues = [], []
    cursor = 0      # 最後一個有音檔片段的終點(毫秒)
    pending = 0     # 該片段之後的停頓(毫秒)
    for seg in project.segments:
        start = cursor + pending
        audio = seg.get("audio")
        if not audio:
            segments.append({"id": seg["id"], "start": _sec(start), "end": _sec(start)})
            continue
        end = start + _ms(segment_seconds(seg) / pj.effective_speed(project, seg))
        segments.append({"id": seg["id"], "start": _sec(start), "end": _sec(end)})
        cues.extend(_cues(seg["id"], seg["text"], start, end))
        cursor, pending = end, _ms(pj.effective_pause(project, seg))
    duration = cursor

    images = []
    for img in sorted(project.images, key=lambda m: m["start"]):
        start = _ms(img["start"])
        end = duration if img.get("end") is None else min(_ms(img["end"]), duration)
        if start >= duration or end <= start:
            continue
        images.append({"id": img["id"], "file": img["asset"], "start": _sec(start), "end": _sec(end)})
    return {"segments": segments, "cues": cues, "images": images, "duration": _sec(duration)}
