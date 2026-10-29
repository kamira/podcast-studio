# -*- coding: utf-8 -*-
"""fusion.py 與合音 API 的測試(CHG-20261029-01 T7:合音規則,D24、D25)。純函式;API 以 TestClient 呼叫。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import breezy_worker
import fusion as fu
import studio_server as ss


@pytest.mark.parametrize("z,parts", [
    ("ㄓㄜˋ", ("ㄓ", "", "ㄜ", "ˋ")), ("ㄧㄤˋ", ("", "ㄧ", "ㄤ", "ˋ")), ("ㄓ", ("ㄓ", "", "", "")),
    ("˙ㄇㄣ", ("ㄇ", "", "ㄣ", "˙")), ("ㄇㄣ˙", ("ㄇ", "", "ㄣ", "˙")), ("ㄐㄩㄝˊ", ("ㄐ", "ㄩ", "ㄝ", "ˊ")),
    ("ㄦˊ", ("", "", "ㄦ", "ˊ")), ("ㄧ", ("", "ㄧ", "", "")),
])
def test_split_zhuyin(z, parts):
    assert fu.split_zhuyin(z) == parts


@pytest.mark.parametrize("bad", ["", "ˊ", "ㄏㄠㄠ", "˙ㄉㄜ˙", "abc", None, "ㄚㄓ"])
def test_split_zhuyin不合法(bad):
    with pytest.raises(ValueError):
        fu.split_zhuyin(bad)


@pytest.mark.parametrize("a,b,want", [
    ("ㄓㄜˋ", "ㄧㄤˋ", "ㄐㄧㄤˋ"),        # 這樣(規則 3 顎化)
    ("ㄓ", "ㄉㄠˋ", "ㄓㄠˋ"),             # 知道(規則結果;詞庫覆寫為 ㄗㄠˋ)
    ("ㄨㄛˇ", "˙ㄇㄣ", "ㄨㄣˇ"),          # 我們(A 零聲母取 A 介音;B 輕聲取 A 聲調)
    ("ㄕㄣˊ", "˙ㄇㄜ", "ㄕㄜˊ"),          # 什麼
    ("ㄋㄚˋ", "ㄧㄤˋ", "ㄋㄧㄤˋ"),        # 那樣(規則 12 照用)
    ("ㄔ", "ㄧㄢˊ", "ㄑㄧㄢˊ"),            # 規則 4
    ("ㄙㄨㄛˇ", "ㄧˇ", "ㄒㄧˇ"),          # 所以(規則 5)
    ("ㄐㄩㄝˊ", "˙ㄉㄜ", "ㄓㄜˊ"),        # 覺得(規則 7 去顎化)
    ("ㄅㄨˋ", "ㄍㄨㄛˋ", "ㄅㄛˋ"),        # 不過(規則 8)
    ("ㄅㄨˋ", "ㄧㄠˋ", "ㄅㄧㄠˋ"),        # 不要(預設詞庫初版)
])
def test_fuse合音(a, b, want):
    got, reason = fu.fuse(a, b)
    assert got == want, reason
    assert breezy_worker.marked_to_digit(got) is not None     # worker 認得的格式


@pytest.mark.parametrize("a,b,reason", [
    ("ㄐㄧㄡˋ", "ㄕˋ", fu.SKIP_NO_FINAL),      # 就是(規則 1)
    ("ㄋㄚˋ", "ㄦˊ", fu.SKIP_ER),               # 那兒(規則 2)
    ("ㄖㄨˋ", "ㄧㄡˇ", fu.SKIP_RI),             # 規則 6
    ("ㄅㄨˋ", "ㄩㄢˋ", fu.SKIP_ILLEGAL),        # 不願 → ㄅㄩㄢ(規則 9)
    ("ㄉㄚˋ", "ㄩㄢˊ", fu.SKIP_ILLEGAL),        # ㄉㄩㄢ(規則 9)
    ("ㄋㄚˇ", "ㄩㄚ", fu.SKIP_ILLEGAL),         # ㄋㄩㄚ:ㄩ 接 ㄚ(規則 9)
    ("ㄧˊ", "ㄧㄤˋ", fu.SKIP_SAME),             # 一樣 → ㄧㄤˋ = 樣(規則 11)
    ("ㄏㄠˇ", "x", fu.SKIP_BAD),
])
def test_fuse跳過(a, b, reason):
    got, why = fu.fuse(a, b)
    assert got is None and why == reason


def test_規則10在規則1之後不會出現():
    # 規則 10(零聲母、無介音、無韻母):規則 1 已擋下「B 沒有韻母也沒有介音」,B 沒有介音時必有韻母,
    # 所以合成結果必有介音或韻母;以窮舉確認沒有任何組合會產生空音節
    samples = ["ㄚ", "ㄧ", "ㄨㄛˇ", "ㄧㄤˋ", "˙ㄇㄣ", "ㄦˊ", "ㄓ", "ㄩㄝˋ"]
    for a in samples:
        for b in samples:
            r = fu.explain(a, b)
            assert r["reason"] != fu.SKIP_EMPTY


def test_explain附推導():
    r = fu.explain("ㄓㄜˋ", "ㄧㄤˋ")
    assert r == {"zhuyin": "ㄐㄧㄤˋ", "reason": "顎化(ㄓ → ㄐ)", "raw": "ㄓㄧㄤˋ"}


@pytest.mark.parametrize("z,ok", [("ㄐㄧㄤˋ", True), ("ㄗㄠˋ", True), ("ㄓㄧ", False), ("ㄐㄚ", False),
                                  ("ㄅ", False), ("ㄓ", True), ("ㄈㄧ", False), ("ㄏㄠㄠ", False)])
def test_is_syllable(z, ok):
    assert fu.is_syllable(z) is ok


def test_suggest依詞庫_由左到右不重疊():
    lex = {"知道": "ㄗㄠˋ", "這樣": "ㄐㄧㄤˋ", "樣子": "ㄧㄤˋ"}
    assert fu.suggest("我也不知道", None, lex) == [{"index": 3, "zhuyin": "ㄗㄠˋ", "source": "lexicon"}]
    assert fu.suggest("就是這樣子", None, lex) == [{"index": 2, "zhuyin": "ㄐㄧㄤˋ", "source": "lexicon"}]
    assert fu.suggest("知道", [None, "ㄉㄠˋ"], lex) == []          # 缺注音的位置略過
    assert fu.suggest("我也不知道", None, {}) == []


# ---------- POST /api/fusion/suggest ----------

TOKEN = "測試用權杖-T7-合音".encode("utf-8").hex()
AUTH = {"Origin": "http://127.0.0.1:7860", "X-Studio-Token": TOKEN}


@pytest.fixture
def client(tmp_path):
    app = ss.create_app(runner=lambda g, emit, c=None: None, token=TOKEN, root=tmp_path,
                        static_dir=tmp_path / "no-static", fusion_lexicon=lambda: {"知道": "ㄗㄠˋ"})
    return TestClient(app, base_url="http://127.0.0.1:7860")


def test_api_suggest單組推導與詞庫覆寫(client):
    text = "是這樣啊,我也不知道"
    zh = ["ㄕˋ", "ㄓㄜˋ", "ㄧㄤˋ", "ㄚ", None, "ㄨㄛˇ", "ㄧㄝˇ", "ㄅㄨˋ", "ㄓ", "ㄉㄠˋ"]
    r = client.post("/api/fusion/suggest", json={"text": text, "zhuyin": zh, "index": 1}, headers=AUTH)
    assert r.status_code == 200, r.text
    f = r.json()["fusion"]
    assert (f["word"], f["rule"], f["raw"], f["zhuyin"], f["source"]) == ("這樣", "ㄐㄧㄤˋ", "ㄓㄧㄤˋ", "ㄐㄧㄤˋ", "rule")
    assert r.json()["suggestions"] == [{"index": 8, "zhuyin": "ㄗㄠˋ", "source": "lexicon"}]
    f = client.post("/api/fusion/suggest", json={"text": text, "zhuyin": zh, "index": 8}, headers=AUTH).json()["fusion"]
    assert (f["rule"], f["lexicon"], f["zhuyin"], f["source"]) == ("ㄓㄠˋ", "ㄗㄠˋ", "ㄗㄠˋ", "lexicon")


def test_api_suggest不合音與錯誤輸入(client):
    text = "就是"
    f = client.post("/api/fusion/suggest", json={"text": text, "zhuyin": ["ㄐㄧㄡˋ", "ㄕˋ"], "index": 0},
                    headers=AUTH).json()["fusion"]
    assert f["zhuyin"] is None and f["source"] is None and "不合音" in f["reason"]
    for body in ({"text": ""}, {"text": "好" * 501}, {"text": "就是", "index": 1}, {"text": "就,", "index": 0},
                 {"text": "就是", "zhuyin": ["ㄐㄧㄡˋ"]}, {"text": "就是", "other": 1}):
        assert client.post("/api/fusion/suggest", json=body, headers=AUTH).status_code == 422, body
    assert client.post("/api/fusion/suggest", json={"text": "就是"},
                       headers={"Origin": "http://127.0.0.1:7860"}).status_code == 403
