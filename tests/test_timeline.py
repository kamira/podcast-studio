# -*- coding: utf-8 -*-
"""timeline.py 的測試(CHG-20261022-04 T4:時間軸計算)。

`layout()` 是純函式:只讀專案物件,不讀檔、不碰 projects/。
"""
from __future__ import annotations

import project as pj
import timeline as tl

SEG_IDS = [f"{i:016x}" for i in range(1, 20)]
IMG_ASSET = "a0a0a0a0a0a0a0a0"
IMG_ASSET2 = "b0b0b0b0b0b0b0b0"


def seg(i, text="大家好。", seconds=1.15, speed=None, brk="sentence", pause=None, audio=True):
    return {"id": SEG_IDS[i], "text": text, "break": brk, "pause_after": pause, "speed": speed,
            "annotations": [],
            "audio": {"id": f"{i + 0xc0:016x}", "seconds": seconds} if audio else None}


def image(i, asset=IMG_ASSET, start=0.0, end=None):
    return {"id": f"{i + 0xe0:016x}", "asset": asset, "start": start, "end": end}


def make(segments, images=(), **defaults):
    d = dict(pj.DEFAULTS)
    d.update(defaults)
    return pj.Project(name="測試", segments=list(segments), images=list(images), defaults=d)


# ---------- 片段 ----------

def test_片段長度採變速後長度():
    # speed null → 預設 1.15:2.3 秒 ÷ 1.15 = 2.0;明定 speed 2.0:3.0 ÷ 2.0 = 1.5
    p = make([seg(0, seconds=2.3, pause=0.0), seg(1, seconds=3.0, speed=2.0, brk="end")])
    out = tl.layout(p)
    a, b = out["segments"]
    assert a["id"] == SEG_IDS[0] and (a["start"], a["end"]) == (0.0, 2.0)
    assert (b["start"], b["end"]) == (2.0, 3.5)
    assert out["duration"] == 3.5


def test_片段起點等於前一段終點加停頓():
    p = make([
        seg(0, seconds=1.0, speed=1.0, brk="sentence"),             # 句號預設 0.4
        seg(1, seconds=1.0, speed=1.0, brk="paragraph"),            # 段落預設 0.8
        seg(2, seconds=1.0, speed=1.0, brk="forced"),               # 強制停頓 = 逗號預設 0.0
        seg(3, seconds=1.0, speed=1.0, brk="sentence", pause=1.5),  # 使用者指定 1.5
        seg(4, seconds=1.0, speed=1.0, brk="end"),
    ])
    s = tl.layout(p)["segments"]
    starts = [x["start"] for x in s]
    assert starts == [0.0, 1.4, 3.2, 4.2, 6.7]
    for prev, cur, gap in zip(s, s[1:], (0.4, 0.8, 0.0, 1.5)):
        assert round(cur["start"] - prev["end"], 3) == gap


def test_總長不含最後一段之後的停頓():
    p = make([seg(0, seconds=1.0, speed=1.0, brk="sentence", pause=2.0)])
    out = tl.layout(p)
    assert out["segments"][0]["end"] == 1.0
    assert out["duration"] == 1.0


def test_時間一律取到毫秒且不累積誤差():
    # 1/3 秒 × 3 段:每段 0.333,停頓 0;起訖以毫秒整數累加
    p = make([seg(i, seconds=1 / 3, speed=1.0, brk="forced") for i in range(3)])
    out = tl.layout(p)
    assert [(x["start"], x["end"]) for x in out["segments"]] == [
        (0.0, 0.333), (0.333, 0.666), (0.666, 0.999)]
    assert out["duration"] == 0.999
    for c in out["cues"]:
        assert round(c["start"], 3) == c["start"] and round(c["end"], 3) == c["end"]


def test_沒有音檔的片段長度為零且不加停頓也不出字幕():
    p = make([
        seg(0, seconds=1.0, speed=1.0, brk="sentence"),
        seg(1, text="還沒合成。", brk="sentence", audio=False),
        seg(2, seconds=1.0, speed=1.0, brk="end"),
    ])
    out = tl.layout(p)
    a, b, c = out["segments"]
    assert (b["start"], b["end"]) == (1.4, 1.4)  # 放在前一段停頓之後,長度 0
    assert c["start"] == 1.4  # 只算第一段之後的 0.4,未合成片段的停頓不計
    assert [x["text"] for x in out["cues"]] == ["大家好", "大家好"]
    assert out["duration"] == 2.4


def test_沒有任何片段時總長為零():
    out = tl.layout(make([], images=[image(0)]))
    assert out == {"segments": [], "cues": [], "images": [], "duration": 0.0}


# ---------- 字幕 cue ----------

def test_短片段一個cue且等於片段起訖():
    p = make([seg(0, text="今天來聊台灣夜市。", seconds=2.0, speed=1.0, brk="end")])
    (c,) = tl.layout(p)["cues"]
    assert c == {"start": 0.0, "end": 2.0, "text": "今天來聊台灣夜市", "segment": SEG_IDS[0]}


def test_30字片段切成2個cue且cue終點等於片段終點():
    text = "一二三四五六七八九十" * 3  # 30 字,沒有標點
    assert len(text) == 30
    p = make([seg(0, seconds=1.0, speed=1.0, pause=0.5),
              seg(1, text=text, seconds=3.0, speed=1.0, brk="end")])
    out = tl.layout(p)
    s1 = out["segments"][1]
    cues = [c for c in out["cues"] if c["segment"] == SEG_IDS[1]]
    assert len(cues) == 2
    assert cues[0]["start"] == s1["start"] == 1.5
    assert cues[-1]["end"] == s1["end"] == 4.5
    assert cues[0]["end"] == cues[1]["start"]
    assert "".join(c["text"] for c in cues) == text
    assert all(len(c["text"]) <= tl.CUE_MAX_CHARS for c in cues)
    # 依字數比例分配時間:15 + 15 字 → 各 1.5 秒
    assert cues[0]["end"] == 3.0


def test_長片段優先在逗號後切開且每個cue不超過上限():
    text = "今天我們要聊的是台灣夜市的小吃文化,從士林到饒河街每一攤都有故事。"
    p = make([seg(0, text=text, seconds=4.0, speed=1.0, brk="end")])
    cues = tl.layout(p)["cues"]
    assert len(cues) == 2
    assert cues[0]["text"] == "今天我們要聊的是台灣夜市的小吃文化"  # 逗號後切開,cue 尾端標點不顯示
    assert cues[1]["text"] == "從士林到饒河街每一攤都有故事"
    assert cues[-1]["end"] == 4.0


def test_很長的片段切成多個cue且時間連續():
    text = "台" * 100
    p = make([seg(0, text=text, seconds=10.0, speed=1.0, brk="end")])
    cues = tl.layout(p)["cues"]
    assert len(cues) == 5  # ceil(100 / 24)
    assert cues[0]["start"] == 0.0 and cues[-1]["end"] == 10.0
    for a, b in zip(cues, cues[1:]):
        assert a["end"] == b["start"]
    assert all(len(c["text"]) <= tl.CUE_MAX_CHARS for c in cues)
    assert sum(len(c["text"]) for c in cues) == 100


def test_split_cue_text_每段不超過上限():
    for n in (1, 23, 24, 25, 48, 49, 500):
        parts = tl.split_cue_text("字," * (n // 2) + "字" * (n % 2))
        assert "".join(parts) == "字," * (n // 2) + "字" * (n % 2)
        assert all(0 < len(x) <= tl.CUE_MAX_CHARS for x in parts)


# ---------- 畫面軌 ----------

def test_預設一張圖撐滿全長():
    p = make([seg(0, seconds=2.0, speed=1.0), seg(1, seconds=1.0, speed=1.0, brk="end")],
             images=[image(0, start=0.0, end=None)])
    out = tl.layout(p)
    assert out["duration"] == 3.4
    assert out["images"] == [{"id": f"{0xe0:016x}", "file": IMG_ASSET, "start": 0.0, "end": 3.4}]


def test_圖片起訖超出總長被截斷():
    p = make([seg(0, seconds=5.0, speed=1.0, brk="end")],
             images=[image(0, start=0.0, end=3.0),
                     image(1, asset=IMG_ASSET2, start=3.0, end=9.0),   # 終點超出 → 截到 5.0
                     image(2, start=5.0, end=6.0),                     # 起點已在總長之後 → 丟棄
                     image(3, start=7.0, end=None)])                   # 同上
    imgs = tl.layout(p)["images"]
    assert [(m["file"], m["start"], m["end"]) for m in imgs] == [
        (IMG_ASSET, 0.0, 3.0), (IMG_ASSET2, 3.0, 5.0)]


def test_圖片依起點排序():
    p = make([seg(0, seconds=4.0, speed=1.0, brk="end")],
             images=[image(0, asset=IMG_ASSET2, start=2.0, end=None), image(1, start=0.0, end=2.0)])
    imgs = tl.layout(p)["images"]
    assert [(m["file"], m["start"], m["end"]) for m in imgs] == [
        (IMG_ASSET, 0.0, 2.0), (IMG_ASSET2, 2.0, 4.0)]


def test_layout不改動專案物件():
    p = make([seg(0, seconds=2.0)], images=[image(0)])
    before = p.to_dict()
    tl.layout(p)
    assert p.to_dict() == before
