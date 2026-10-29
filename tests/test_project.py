# -*- coding: utf-8 -*-
"""project.py 的測試(CHG-20261022-04 T2:專案資料模型)。

每個測試把 project.ROOT 指到暫存目錄(`<tmp>/repo`),不碰 repo 內的 `projects/`;
「projects/ 外」的連結目標放在 `<tmp>/outside`。時間一律以注入的 now 判定。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import names
import project as pj
import studio_server as ss

BASE = datetime(2026, 10, 27, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = (tmp_path / "repo").resolve()
    root.mkdir()
    monkeypatch.setattr(pj, "ROOT", root)
    return root


@pytest.fixture
def projects(repo):
    p = repo / "projects"
    p.mkdir()
    return p


@pytest.fixture
def outside(tmp_path):
    o = tmp_path / "outside"
    (o / "sub").mkdir(parents=True)
    (o / "keep.txt").write_text("不可刪除", encoding="utf-8")
    (o / "sub" / "deep.txt").write_text("深層", encoding="utf-8")
    return o


def snapshot(d: Path) -> dict:
    return {str(p.relative_to(d)): (p.read_bytes() if p.is_file() else None) for p in sorted(d.rglob("*"))}


def seg(text="今天天氣真好。", **kw):
    s = {"id": kw.pop("id", pj.new_id()), "text": text}
    s.update(kw)
    return s


def proj(name="夜市的味道", **kw):
    return pj.Project(name=name, **kw)


def data(**kw):
    d = proj().to_dict()
    d.update(kw)
    return d


def errors_of(exc_info) -> str:
    return ";".join(exc_info.value.errors)


# ---------- 語速 ----------

def test_speed_rounded_to_005():
    out = pj.validate(data(segments=[seg(speed=1.23)]))
    assert out["segments"][0]["speed"] == 1.25
    assert pj.normalize_speed(1.225) == 1.25   # 四捨五入,不是銀行家捨入
    assert pj.normalize_speed(2) == 2.0


@pytest.mark.parametrize("bad", [0.45, 2.05, 2.5, -1, True, "1.2", float("nan"), float("inf")])
def test_speed_out_of_range_rejected(bad):
    with pytest.raises(pj.ProjectError):
        pj.validate(data(segments=[seg(speed=bad)]))


def test_speed_null_uses_default_115():
    p = pj.Project.from_dict(data(segments=[seg(speed=None)]))
    assert p.segments[0]["speed"] is None
    assert pj.effective_speed(p, p.segments[0]) == 1.15
    assert pj.DEFAULTS["speed"] == 1.15
    p2 = pj.Project.from_dict(data(segments=[seg(speed=1.3)]))
    assert pj.effective_speed(p2, p2.segments[0]) == 1.3


# ---------- 停頓 ----------

def test_pause_rounded_and_range():
    out = pj.validate(data(segments=[seg(pause_after=5.04)]))
    assert out["segments"][0]["pause_after"] == 5.0
    assert pj.normalize_pause(0.25) == 0.3
    for bad in (-1, 5.06, 7, "1", None, True):
        with pytest.raises(pj.ProjectError):
            pj.normalize_pause(bad)
    with pytest.raises(pj.ProjectError):
        pj.validate(data(segments=[seg(pause_after=-1)]))


def test_pause_defaults_by_break():
    p = pj.Project.from_dict(data(segments=[
        seg("一。", **{"break": "sentence"}), seg("二", **{"break": "forced"}),
        seg("三。", **{"break": "paragraph"}), seg("四", **{"break": "end"}),
        seg("五。", **{"break": "sentence", "pause_after": 1.5}),
    ]))
    got = [pj.effective_pause(p, s) for s in p.segments]
    assert got == [0.4, 0.0, 0.8, 0.0, 1.5]
    with pytest.raises(pj.ProjectError):
        pj.validate(data(defaults={"speed": 1.15, "pause_sentence": 6}))


def test_pause_zero_survives_save_and_load(projects):
    """DIR-5 統整 M-2:停頓 0 秒存檔後讀回仍為 0(不被當成 null 改回預設)。"""
    p = proj(segments=[seg("一。", **{"break": "sentence", "pause_after": 0}),
                       seg("二。", **{"break": "sentence"})])
    pj.save(p)
    back = pj.load("夜市的味道")
    assert back.segments[0]["pause_after"] == 0 and back.segments[0]["pause_after"] is not None
    assert pj.effective_pause(back, back.segments[0]) == 0
    assert pj.effective_pause(back, back.segments[1]) == 0.4


# ---------- 專案名稱 ----------

@pytest.mark.parametrize("name", ["café", "straße", "Ünïcödé", "ＡＢＣ", "한국어", "аbc", "a b", "a.b",
                                  "../x", "..", "", "a" * 65, "東京・タワー", None, 1])
def test_project_name_non_ascii_letters_rejected(name):
    with pytest.raises(pj.ProjectError) as e:
        pj.check_name(name)
    assert "專案名稱" in errors_of(e)
    with pytest.raises(pj.ProjectError):
        pj.validate(data(name=name))


@pytest.mark.parametrize("name", ["夜市的味道", "ep_01-初稿", "ポッドキャスト", "ひらがな", "a" * 64])
def test_project_name_cjk_whitelist_allowed(name):
    assert pj.check_name(name) == name


def test_name_rule_single_source_shared_with_workflows():
    """專案名稱與工作流程名稱共用 names.py 的同一套規則。"""
    assert ss._normalize_name is names.normalize_name
    assert ss._fold_name is names.fold_name
    assert ss.WORKFLOW_NAME_RANGES is names.NAME_RANGES
    assert ss.MAX_WORKFLOW_NAME == names.MAX_NAME == 64


def test_case_only_collision_rejected(projects):
    pj.save(proj("Ep1"))
    with pytest.raises(pj.ProjectError) as e:
        pj.save(proj("ep1"))
    assert "大小寫" in errors_of(e)
    with pytest.raises(pj.ProjectError):
        pj.load("EP1")
    assert pj.load("Ep1").name == "Ep1"


# ---------- 上限 ----------

def test_text_limits():
    with pytest.raises(pj.ProjectError):
        pj.validate(data(script="字" * (pj.MAX_SCRIPT + 1)))
    pj.validate(data(script="字" * pj.MAX_SCRIPT))
    with pytest.raises(pj.ProjectError):
        pj.validate(data(segments=[seg("字" * 501)]))
    with pytest.raises(pj.ProjectError):
        pj.validate(data(segments=[seg("字") for _ in range(pj.MAX_SEGMENTS + 1)]))
    with pytest.raises(pj.ProjectError):
        pj.split_segments("字" * (pj.MAX_SCRIPT + 1))
    with pytest.raises(pj.ProjectError) as e:
        pj.split_segments("字" * 501 + "。")
    assert "500" in errors_of(e)
    with pytest.raises(pj.ProjectError):
        pj.split_segments("一。" * (pj.MAX_SEGMENTS + 1))


def asset(kind="image", ext="png", size=1, **kw):
    return {"id": kw.pop("id", pj.new_id()), "kind": kind, "ext": ext, "bytes": size, **kw}


def test_asset_limits():
    pj.validate(data(assets=[asset() for _ in range(pj.MAX_ASSETS)]))
    with pytest.raises(pj.ProjectError) as e:
        pj.validate(data(assets=[asset() for _ in range(pj.MAX_ASSETS + 1)]))
    assert "200" in errors_of(e)
    with pytest.raises(pj.ProjectError) as e:
        pj.validate(data(assets=[asset(size=pj.MAX_ASSET_BYTES), asset(size=1)]))
    assert "2 GB" in errors_of(e)
    for bad in (asset(ext="svg"), asset(kind="video", ext="mp4"), asset(size=-1), asset(label="a\nb"),
                {"id": pj.new_id(), "kind": "image", "ext": "png", "bytes": 1, "path": "/etc/passwd"}):
        with pytest.raises(pj.ProjectError):
            pj.validate(data(assets=[bad]))


def test_audio_total_limit_counts_speed():
    a = {"id": pj.new_id(), "seconds": 1900.0}
    segs = [seg(audio=dict(a, id=pj.new_id())), seg(audio=dict(a, id=pj.new_id()))]
    with pytest.raises(pj.ProjectError) as e:
        pj.validate(data(segments=segs, defaults={"speed": 1.0}))
    assert "60 分鐘" in errors_of(e)
    # 以變速後長度計:3800 秒 ÷ 1.15 ≈ 3304 秒 < 3600
    pj.validate(data(segments=segs))


def test_project_json_size_limit(projects, monkeypatch):
    monkeypatch.setattr(pj, "MAX_PROJECT_BYTES", 2000)
    with pytest.raises(pj.ProjectError) as e:
        pj.validate(data(script="字" * 1000))
    assert "MB" in errors_of(e)
    d = projects / "夜市的味道"
    d.mkdir()
    (d / "project.json").write_text(json.dumps(data(script="字" * 1000), ensure_ascii=False), encoding="utf-8")
    with pytest.raises(pj.ProjectError):
        pj.load("夜市的味道")


def test_images_reference_image_assets():
    img = asset()
    txt = asset(kind="text", ext="txt")
    out = pj.validate(data(assets=[img, txt], images=[{"id": pj.new_id(), "asset": img["id"], "start": 1.04}]))
    assert out["images"][0] == {"id": out["images"][0]["id"], "asset": img["id"], "start": 1.0, "end": None}
    for bad in ({"asset": txt["id"], "start": 0}, {"asset": pj.new_id(), "start": 0},
                {"asset": img["id"], "start": 2, "end": 1}, {"asset": img["id"], "start": -1},
                {"asset": img["id"], "start": 0, "file": "/tmp/x.png"}):
        with pytest.raises(pj.ProjectError):
            pj.validate(data(assets=[img, txt], images=[dict(bad, id=pj.new_id())]))


def test_ids_server_format_and_unique():
    for bad in ("../x", "ABCDEF12", "abc", "g" * 8, 12345678):
        with pytest.raises(pj.ProjectError):
            pj.validate(data(segments=[seg(id=bad)]))
    with pytest.raises(pj.ProjectError):
        pj.validate(data(segments=[seg(id="a" * 8), seg(id="a" * 8)]))
    with pytest.raises(pj.ProjectError):
        pj.validate(data(unknown=1))


# ---------- 分句 ----------

def test_split_by_sentence_punctuation_and_newline():
    text = "大家好，歡迎收聽。今天來聊台灣夜市！你去過嗎？」\n\n第二段；分號也切;半形也切!最後沒有標點"
    got = [(p["text"], p["break"]) for p in pj.split_segments(text, [])]
    assert got == [
        ("大家好，歡迎收聽。", "sentence"),
        ("今天來聊台灣夜市！", "sentence"),
        ("你去過嗎？」", "paragraph"),
        ("第二段；", "sentence"),
        ("分號也切;", "sentence"),
        ("半形也切!", "sentence"),
        ("最後沒有標點", "end"),
    ]
    for p in pj.split_segments(text, []):
        assert text[p["start"]:p["end"]] == p["text"]


def test_split_newline_without_punctuation_and_blank_pieces():
    got = [(p["text"], p["break"]) for p in pj.split_segments("  第一行\n第二行。\n\n  \n", [])]
    assert got == [("第一行", "paragraph"), ("第二行。", "paragraph")]
    assert pj.split_segments("", []) == []
    assert [p["text"] for p in pj.split_segments("好!?」)再來。", [])] == ["好!?」)", "再來。"]


def test_split_forced_breaks():
    text = "大家好，歡迎收聽。今天，天氣好。"
    comma = text.index("，")
    got = [(p["text"], p["break"]) for p in pj.split_segments(text, [comma])]
    assert got == [("大家好，", "forced"), ("歡迎收聽。", "sentence"), ("今天，天氣好。", "sentence")]
    two = [p["text"] for p in pj.split_segments(text, [comma, text.rindex("，")])]
    assert two == ["大家好，", "歡迎收聽。", "今天，", "天氣好。"]
    for bad in ([-1], [len(text)], ["3"], [True]):
        with pytest.raises(pj.ProjectError):
            pj.split_segments(text, bad)


# ---------- 合成文字與標註 ----------

def test_tts_text_escapes_brackets_to_fullwidth():
    assert pj.tts_text(seg("好[:ㄅ]")) == "好［:ㄅ］"
    assert pj.tts_text(seg("好[:ㄅㄚ]吃")) == "好［:ㄅㄚ］吃"
    assert "[" not in pj.tts_text(seg("a[b]c")) and "]" not in pj.tts_text(seg("a[b]c"))


def test_tts_text_annotation_at_index():
    s = seg("今天好熱", annotations=[{"index": 2, "zhuyin": "ㄏㄠˋ"}])
    assert pj.tts_text(s) == "今天好[:ㄏㄠˋ]熱"
    s2 = seg("你好[嗎]的", annotations=[{"index": 5, "zhuyin": "˙ㄉㄜ"}, {"index": 1, "zhuyin": "ㄏㄠˇ"}])
    assert pj.tts_text(s2) == "你好[:ㄏㄠˇ]［嗎］的[:˙ㄉㄜ]"
    out = pj.validate(data(segments=[s2]))
    assert [a["index"] for a in out["segments"][0]["annotations"]] == [1, 5]


@pytest.mark.parametrize("z", ["hao4", "ㄏㄠ4", "ㄏㄠ]", "[:ㄏ", "", "ㄏㄠˋㄏㄠˋ", "ㄏㄠˋˋ", "˙ㄉㄜˋ",
                               "ㄅㄆㄇㄈㄉㄊ", "ㄏ ㄠ", 3, None])
def test_non_zhuyin_annotation_rejected(z):
    s = seg("今天好熱", annotations=[{"index": 2, "zhuyin": z}])
    with pytest.raises(pj.ProjectError):
        pj.tts_text(s)
    with pytest.raises(pj.ProjectError):
        pj.validate(data(segments=[s]))


def test_annotation_index_rules():
    for anns in ([{"index": 9, "zhuyin": "ㄏㄠˋ"}], [{"index": 4, "zhuyin": "ㄏㄠˋ"}],
                 [{"index": 0, "zhuyin": "ㄏㄠˋ"}, {"index": 0, "zhuyin": "ㄏㄠˇ"}],
                 [{"index": True, "zhuyin": "ㄏㄠˋ"}], [{"index": 0, "zhuyin": "ㄏㄠˋ", "x": 1}]):
        with pytest.raises(pj.ProjectError):
            pj.tts_text(seg("今天好熱。", annotations=anns))


def test_tts_text_accepted_by_worker_format():
    """T2 產生的合成文字,worker 一律能轉成 BreezyVoice 的數字聲調。"""
    import breezy_worker
    s = seg("你好[嗎]的", annotations=[{"index": 1, "zhuyin": "ㄏㄠˋ"}, {"index": 5, "zhuyin": "˙ㄉㄜ"}])
    assert breezy_worker.to_breezy_text(pj.tts_text(s)) == "你好[:ㄏㄠ4]［嗎］的[:ㄉㄜ5]"


# ---------- 存檔、版本 ----------

def test_save_load_round_trip(projects):
    p = proj(script="今天好熱。", segments=[seg("今天好熱。", speed=1.23, annotations=[{"index": 2, "zhuyin": "ㄏㄠˋ"}])])
    assert pj.save(p) is None
    got = pj.load("夜市的味道")
    assert got.segments[0]["speed"] == 1.25
    assert got.to_dict() == pj.validate(p.to_dict())
    raw = json.loads((projects / "夜市的味道" / "project.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2


def test_eleventh_save_keeps_10_history(projects):
    p = proj()
    ids = []
    for i in range(12):
        p.script = f"第 {i} 版"
        ids.append(pj.save(p))
    hist = projects / "夜市的味道" / "history"
    files = sorted(os.listdir(hist))
    assert len(files) == 10
    assert pj.list_history("夜市的味道") == ids[2:]          # 最舊的被刪,保留最近 10 個
    newest = json.loads((hist / f"{ids[-1]}.json").read_text(encoding="utf-8"))
    assert newest["script"] == "第 10 版"
    for fn in files:
        assert pj.HISTORY_RE.fullmatch(fn), fn


def test_history_rotation_ignores_unrelated_files(projects):
    p = proj()
    pj.save(p)
    hist = projects / "夜市的味道" / "history"
    (hist / "note.txt").write_text("x", encoding="utf-8")
    for _ in range(12):
        pj.save(p)
    assert (hist / "note.txt").exists()
    assert len(pj.list_history("夜市的味道")) == 10


def test_history_names_unique_when_clock_and_random_collide(projects, monkeypatch):
    monkeypatch.setattr(pj.time, "time", lambda: BASE.timestamp())
    seq = iter(["aaaa", "aaaa", "aaaa", "bbbb", "cccc"])
    monkeypatch.setattr(pj, "_rand4", lambda: next(seq))
    p = proj()
    pj.save(p)
    v1 = pj.save(p)
    v2 = pj.save(p)
    v3 = pj.save(p)
    assert v1.endswith("-aaaa") and v2.endswith("-bbbb") and v3.endswith("-cccc")
    assert len({v1, v2, v3}) == 3
    assert v1.startswith("20261027-120000-000-")


def test_concurrent_saves_from_two_threads(projects):
    pj.save(proj())
    rounds = 15
    barrier = threading.Barrier(2)
    results, errors = [], []

    def worker(tag):
        barrier.wait()
        for i in range(rounds):
            p = proj(script=f"{tag}-{i}" * 200, segments=[seg(f"{tag}第{i}句。")])
            try:
                results.append(pj.save(p))
            except Exception as e:  # pragma: no cover - 失敗時列出
                errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("甲", "乙")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(results) == 2 * rounds and len(set(results)) == 2 * rounds   # history 檔名不重複
    pdir = projects / "夜市的味道"
    got = pj.load("夜市的味道")                                            # 不是壞檔
    assert got.script.split("-")[0] in ("甲", "乙")
    assert len(os.listdir(pdir / "history")) == 10
    for fn in os.listdir(pdir / "history"):
        json.loads((pdir / "history" / fn).read_text(encoding="utf-8"))
    assert not [f for f in os.listdir(pdir) if f.endswith(".tmp")]


def test_schema_version_3_refused(projects):
    d = projects / "夜市的味道"
    d.mkdir()
    body = data()
    body["schema_version"] = 3
    (d / "project.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(pj.ProjectError) as e:
        pj.load("夜市的味道")
    assert "版本" in errors_of(e)
    for v in (3, 0, "1", "2", None, True, 1.0, 2.0):
        body["schema_version"] = v
        with pytest.raises(pj.ProjectError):
            pj.validate(body)
    with pytest.raises(pj.ProjectError):
        pj.save(pj.Project(name="夜市的味道", schema_version=3))


def test_load_rejects_bad_files(projects, outside):
    d = projects / "夜市的味道"
    d.mkdir()
    with pytest.raises(pj.ProjectError):
        pj.load("夜市的味道")
    (d / "project.json").write_text("{壞掉", encoding="utf-8")
    with pytest.raises(pj.ProjectError) as e:
        pj.load("夜市的味道")
    assert str(projects) not in str(e.value)
    os.unlink(d / "project.json")
    (outside / "p.json").write_text(json.dumps(data(), ensure_ascii=False), encoding="utf-8")
    os.symlink(outside / "p.json", d / "project.json")
    with pytest.raises(pj.ProjectError):
        pj.load("夜市的味道")
    other = projects / "其他"
    other.mkdir()
    (other / "project.json").write_text(json.dumps(data(), ensure_ascii=False), encoding="utf-8")
    with pytest.raises(pj.ProjectError):
        pj.load("其他")   # 檔內名稱與目錄不符


def test_trash_requires_confirm_and_never_overwrites(projects):
    pj.save(proj())
    with pytest.raises(pj.ProjectError):
        pj.trash("夜市的味道", confirm=False)
    with pytest.raises(pj.ProjectError):
        pj.trash("夜市的味道", confirm="true")
    first = pj.trash("夜市的味道", confirm=True, now=BASE.timestamp())
    pj.save(proj())
    second = pj.trash("夜市的味道", confirm=True, now=BASE.timestamp())
    assert first != second
    assert sorted(os.listdir(projects / ".trash")) == sorted([first, second])
    assert not (projects / "夜市的味道").exists()
    assert (projects / ".trash" / first / "project.json").is_file()


# ---------- 啟動清理 ----------

def make_trash_entry(projects, name, when: datetime) -> Path:
    stamp = when.strftime("%Y%m%d-%H%M%S") + "-000"
    d = projects / ".trash" / f"{name}-{stamp}-abcd"
    (d / "audio").mkdir(parents=True)
    (d / "project.json").write_text("{}", encoding="utf-8")
    return d


def test_startup_cleanup_trash_30_day_boundary(projects):
    keep = make_trash_entry(projects, "保留", BASE - timedelta(days=29, hours=23))
    gone = make_trash_entry(projects, "刪除", BASE - timedelta(days=30))
    older = make_trash_entry(projects, "更舊", BASE - timedelta(days=45))
    r = pj.startup_cleanup(BASE)
    assert keep.exists()
    assert not gone.exists() and not older.exists()
    assert sorted(r["trash_removed"]) == sorted([gone.name, older.name])
    # 以注入的 now 判定:同樣的項目,把 now 往前推 1 天就都保留
    keep2 = make_trash_entry(projects, "再一個", BASE - timedelta(days=30))
    pj.startup_cleanup(BASE - timedelta(days=1))
    assert keep2.exists()
    pj.startup_cleanup(BASE.timestamp())   # epoch 秒數也可
    assert not keep2.exists()


def test_trash_created_by_trash_is_cleaned_after_30_days(projects):
    pj.save(proj())
    name = pj.trash("夜市的味道", confirm=True, now=BASE.timestamp())
    pj.startup_cleanup(BASE + timedelta(days=29, hours=23))
    assert (projects / ".trash" / name).exists()
    pj.startup_cleanup(BASE + timedelta(days=30))
    assert not (projects / ".trash" / name).exists()


def test_startup_cleanup_trash_symlinks_not_followed(projects, outside, caplog):
    before = snapshot(outside)
    (projects / ".trash").mkdir()
    old = (BASE - timedelta(days=90)).strftime("%Y%m%d-%H%M%S") + "-000"
    link_dir = projects / ".trash" / f"外連目錄-{old}-abcd"
    link_file = projects / ".trash" / f"外連檔案-{old}-abcd"
    os.symlink(outside, link_dir)
    os.symlink(outside / "keep.txt", link_file)
    # 過期的一般回收項目,內部含指向外部的符號連結:項目刪除,連結目標不動
    expired = make_trash_entry(projects, "過期", BASE - timedelta(days=31))
    os.symlink(outside, expired / "外連")
    os.symlink(outside / "keep.txt", expired / "audio" / "外連.txt")
    with caplog.at_level(logging.WARNING, logger="project"):
        r = pj.startup_cleanup(BASE)
    assert os.path.islink(link_dir) and os.path.islink(link_file)   # 連結本身不刪
    assert not expired.exists()
    assert snapshot(outside) == before
    assert any("符號連結" in w for w in r["warnings"])
    assert "符號連結" in caplog.text


def test_startup_cleanup_tmp_symlink_not_followed(projects, outside):
    before = snapshot(outside)
    pj.save(proj())
    tmp = projects / "夜市的味道" / "tmp"
    tmp.mkdir()
    os.symlink(outside, tmp / "外連目錄")
    os.symlink(outside / "keep.txt", tmp / "外連檔案")
    pj.startup_cleanup(BASE)
    assert snapshot(outside) == before
    assert os.listdir(tmp) == []


@pytest.mark.parametrize("which", [".trash", "project", "tmp", "history"])
def test_startup_cleanup_refuses_symlinked_dirs(projects, outside, caplog, which):
    old = (BASE - timedelta(days=90)).strftime("%Y%m%d-%H%M%S") + "-000"
    (outside / f"舊的-{old}-abcd").mkdir()
    (outside / "tmp").mkdir()
    (outside / "tmp" / "殘留.json").write_text("x", encoding="utf-8")
    (outside / f"{old}-abcd.json").write_text("{}", encoding="utf-8")
    pj.save(proj("正常"))
    if which == ".trash":
        os.symlink(outside, projects / ".trash")
    elif which == "project":
        os.symlink(outside, projects / "外連專案")
    else:
        pdir = projects / "外連子目錄"
        pdir.mkdir()
        os.symlink(outside, pdir / which)
    before = snapshot(outside)
    with caplog.at_level(logging.WARNING, logger="project"):
        r = pj.startup_cleanup(BASE)
    assert snapshot(outside) == before
    assert r["warnings"], "應記警告"
    assert caplog.records
    assert (projects / "正常" / "project.json").is_file()


@pytest.mark.parametrize("which", ["project", "history"])
def test_history_rotation_refuses_symlinked_dirs(projects, outside, caplog, which):
    """專案目錄或 history/ 是符號連結時,存檔與輪替都拒絕處理,連結目標不變。"""
    for i in range(12):
        (outside / f"20260101-0000{i:02d}-000-abcd.json").write_text("{}", encoding="utf-8")
    (outside / "project.json").write_text(json.dumps(data(), ensure_ascii=False), encoding="utf-8")
    if which == "project":
        os.symlink(outside, projects / "夜市的味道")
    else:
        pdir = projects / "夜市的味道"
        pdir.mkdir()
        (pdir / "project.json").write_text(json.dumps(data(), ensure_ascii=False), encoding="utf-8")
        os.symlink(outside, pdir / "history")
    before = snapshot(outside)
    with caplog.at_level(logging.WARNING, logger="project"):
        with pytest.raises(pj.UnsafePath):
            pj.save(proj(script="新內容"))
    assert snapshot(outside) == before
    assert "拒絕" in caplog.text
    # 輪替本身也拒絕處理符號連結的 history/
    if which == "history":
        warnings = []
        pfd = os.open(projects / "夜市的味道", os.O_RDONLY)   # DIR-5 修正 4:_rotate 改收專案目錄 fd
        try:
            assert pj._rotate(pfd, "夜市的味道", warnings) == []
        finally:
            os.close(pfd)
        assert warnings and snapshot(outside) == before


def test_projects_root_symlink_refused(repo, outside, caplog):
    os.symlink(outside, repo / "projects")
    before = snapshot(outside)
    with caplog.at_level(logging.WARNING, logger="project"):
        r = pj.startup_cleanup(BASE)
    assert r["warnings"]
    with pytest.raises(pj.UnsafePath):
        pj.save(proj())
    assert snapshot(outside) == before


def test_startup_cleanup_clears_tmp_and_keeps_project_data(projects):
    p = proj()
    pj.save(p)
    pj.save(p)
    pdir = projects / "夜市的味道"
    for sub in ("audio", "assets", "tmp"):
        (pdir / sub).mkdir(exist_ok=True)
    (pdir / "audio" / "abcdef12.wav").write_bytes(b"RIFF")
    (pdir / "assets" / "abcdef12.png").write_bytes(b"PNG")
    (pdir / "tmp" / "jobs.json").write_text("{}", encoding="utf-8")
    (pdir / "tmp" / "out").mkdir()
    (pdir / "tmp" / "out" / "a.wav").write_bytes(b"x")
    keep = {k: v for k, v in snapshot(pdir).items() if not k.startswith("tmp")}
    r = pj.startup_cleanup(BASE)
    assert os.listdir(pdir / "tmp") == []
    assert r["tmp_cleared"] == 2
    assert {k: v for k, v in snapshot(pdir).items() if not k.startswith("tmp")} == keep
    assert pj.load("夜市的味道").name == "夜市的味道"
    assert len(pj.list_history("夜市的味道")) == 1


def test_startup_cleanup_without_projects_dir(repo):
    assert pj.startup_cleanup(BASE) == {"trash_removed": [], "tmp_cleared": 0, "warnings": []}
    assert not (repo / "projects").exists()


# ---------- update()(CHG-20261022-04 T8:載入—修改—存檔全程持專案鎖) ----------

def test_update修改後存檔並回傳版本與change的回傳值(repo):
    pj.create("夜市的味道", "第一句。")
    got, vid, extra = pj.update("夜市的味道", lambda p: p.defaults.update(speed=1.3) or "改好了")
    assert extra == "改好了" and vid is not None
    assert got.defaults["speed"] == 1.3 == pj.load("夜市的味道").defaults["speed"]
    assert vid in pj.list_history("夜市的味道")


def test_update的change丟例外時不存檔(repo):
    pj.create("夜市的味道", "第一句。")
    before = pj.list_history("夜市的味道")

    def boom(p):
        p.defaults["speed"] = 1.9
        raise RuntimeError("中途失敗")

    with pytest.raises(RuntimeError):
        pj.update("夜市的味道", boom)
    assert pj.load("夜市的味道").defaults["speed"] == pj.DEFAULTS["speed"]
    assert pj.list_history("夜市的味道") == before


def test_update驗證不過時不存檔且不能改名(repo):
    pj.create("夜市的味道", "第一句。")
    with pytest.raises(pj.ProjectError):
        pj.update("夜市的味道", lambda p: p.defaults.update(speed=5))
    with pytest.raises(pj.ProjectError):
        pj.update("夜市的味道", lambda p: setattr(p, "name", "別的名字"))
    assert pj.load("夜市的味道").defaults["speed"] == pj.DEFAULTS["speed"]


def test_update專案不存在丟ProjectNotFound(repo):
    with pytest.raises(pj.ProjectNotFound):
        pj.update("沒有這個", lambda p: None)


def test_兩個執行緒同時update不會互相覆蓋(repo):
    pj.create("夜市的味道", "第一句。")
    n = 8
    barrier = threading.Barrier(n)
    errors = []

    def add(i):
        def change(p):
            p.assets = [*p.assets, {"id": f"{i:08x}{'0' * 8}", "kind": "text", "ext": "txt", "bytes": i}]
        try:
            barrier.wait(5)
            pj.update("夜市的味道", change)
        except Exception as e:  # pragma: no cover - 失敗時記錄
            errors.append(e)

    threads = [threading.Thread(target=add, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert errors == []
    assert sorted(a["bytes"] for a in pj.load("夜市的味道").assets) == list(range(n))


# ---------- DIR-5 修正 4:檢查後、操作前換成符號連結的競態 ----------

def _swap_to_link(path: Path, target: Path) -> None:
    """把 path(目錄或檔案)搬到旁邊,原位置改成指向 target 的符號連結。"""
    path.rename(path.with_name(path.name + "-moved"))
    path.symlink_to(target, target_is_directory=target.is_dir())


def _child_hook(monkeypatch, label, action):
    """包住 pj._child:開到 label 目錄的 fd 之後執行 action()(模擬檢查完成、操作之前的替換)。"""
    real = pj._child

    def wrapped(dirfd, part, lbl, create):
        fd = real(dirfd, part, lbl, create)
        if lbl == label:
            action()
        return fd
    monkeypatch.setattr(pj, "_child", wrapped)


def test_race_tmp_dir_swapped_after_open_cleanup_stays_inside(projects, outside, monkeypatch):
    pj.save(proj())
    tmp = projects / "夜市的味道" / "tmp"
    tmp.mkdir()
    (tmp / "殘留.json").write_text("x", encoding="utf-8")
    before = snapshot(outside)
    _child_hook(monkeypatch, "tmp", lambda: _swap_to_link(tmp, outside))
    r = pj.startup_cleanup(BASE)
    assert snapshot(outside) == before                     # 連結目標內容不變
    assert r["tmp_cleared"] == 1 and list((projects / "夜市的味道" / "tmp-moved").iterdir()) == []


def test_race_trash_entry_swapped_after_listing_not_followed(projects, outside, monkeypatch):
    expired = make_trash_entry(projects, "過期", BASE - timedelta(days=31))
    before = snapshot(outside)
    real = pj.safe_fs.entries

    def stale(dirfd):   # 列舉(含 lstat)之後、刪除之前把過期項目換成指向外部的連結
        got = real(dirfd)
        if expired.is_dir() and not expired.is_symlink():
            _swap_to_link(expired, outside)
        return got
    monkeypatch.setattr(pj.safe_fs, "entries", stale)
    pj.startup_cleanup(BASE)
    assert snapshot(outside) == before
    assert not os.path.lexists(expired)                    # 只刪連結本身


def test_race_history_swapped_after_open_rotation_stays_inside(projects, outside, monkeypatch):
    for i in range(12):
        (outside / f"20260101-0000{i:02d}-000-abcd.json").write_text("{}", encoding="utf-8")
    p = proj()
    for _ in range(12):
        pj.save(p)
    before = snapshot(outside)
    hdir = projects / "夜市的味道" / "history"
    done = []

    def swap():   # 第 1 次是移入 history,第 2 次是輪替開啟 history/ 之後、刪除之前
        done.append(1)
        if len(done) == 2:
            _swap_to_link(hdir, outside)
    _child_hook(monkeypatch, "history", swap)
    pj.save(p)
    assert snapshot(outside) == before
    assert len(list((projects / "夜市的味道" / "history-moved").iterdir())) == pj.HISTORY_KEEP


def test_race_trash_dir_swapped_before_move_project_not_moved_outside(projects, outside, monkeypatch):
    pj.save(proj())
    (projects / ".trash").mkdir()
    before = snapshot(outside)
    _child_hook(monkeypatch, pj.TRASH_NAME, lambda: _swap_to_link(projects / ".trash", outside))
    moved = pj.trash("夜市的味道", confirm=True)
    assert snapshot(outside) == before                     # 沒有搬進連結目標
    assert (projects / ".trash-moved" / moved / "project.json").is_file()


# ---------- CHG-20261029-01 T1:schema 2(連音、清晰度、字詞清晰度、語調、後製) ----------

V1_SEGMENT_KEYS = ("id", "text", "break", "pause_after", "speed", "annotations", "audio")
KEY_A = "a" * 64
KEY_B = "b" * 64


def v1_body(**kw):
    body = data(segments=[seg("大家好，歡迎收聽夜市的味道。", id="0123456789abcdef",
                              audio={"id": KEY_A[:32], "seconds": 2.5, "key": KEY_A}),
                          seg("今天好熱。", id="fedcba9876543210")], **kw)
    body["schema_version"] = 1
    for s in body["segments"]:
        for k in list(s):
            if k not in V1_SEGMENT_KEYS:
                del s[k]
    return body


def synth_seg(**kw):
    s = seg("大家好，歡迎收聽夜市的味道。", audio={"id": KEY_A[:32], "seconds": 2.5, "key": KEY_A})
    s.update(kw)
    return pj.validate(data(segments=[s]))["segments"][0]


def test_v1專案載入後補預設值_存檔為v2且v1舊檔在history(projects):
    d = projects / "夜市的味道"
    d.mkdir()
    raw_v1 = json.dumps(v1_body(), ensure_ascii=False)
    (d / "project.json").write_text(raw_v1, encoding="utf-8")
    got = pj.load("夜市的味道")
    assert got.schema_version == 2
    for s in got.segments:
        assert s["connect"] is None and s["clarity"] is None
        assert s["word_clarity"] == [] and s["pitch"] is None and s["post"] is None
    vid = pj.save(got)
    assert vid is not None
    assert json.loads((d / "project.json").read_text(encoding="utf-8"))["schema_version"] == 2
    assert (d / "history" / f"{vid}.json").read_text(encoding="utf-8") == raw_v1
    assert pj.load_version("夜市的味道", vid).schema_version == 2   # 舊版本也可讀(還原時升級)


def test_v1專案不得含v2欄位():
    body = v1_body()
    body["segments"][0]["connect"] = 50
    with pytest.raises(pj.ProjectError):
        pj.validate(body)


def test_prosody預設常數():
    assert pj.PROSODY_DEFAULTS == {"connect": 0, "clarity": 100, "roundness": 50}
    assert pj.SCHEMA_VERSION == 2


@pytest.mark.parametrize("field,value", [("connect", 101), ("connect", -1), ("clarity", -1), ("clarity", 101),
                                         ("connect", 50.5), ("connect", "50"), ("clarity", True),
                                         ("connect", float("nan"))])
def test_連音清晰度範圍(field, value):
    with pytest.raises(pj.ProjectError) as e:
        synth_seg(**{field: value})
    assert "0" in errors_of(e)


def test_連音清晰度合法值():
    s = synth_seg(connect=0, clarity=100)
    assert (s["connect"], s["clarity"]) == (0, 100)
    s = synth_seg(connect=35, clarity=70)
    assert (s["connect"], s["clarity"]) == (35, 70)


def pitch(points, roundness=50):
    return {"points": [{"pos": p, "semi": v} for p, v in points], "roundness": roundness}


def test_語調圓弧必須是0到100整數():
    for bad in ("50", 50.5, 101, -1, True):
        with pytest.raises(pj.ProjectError):
            synth_seg(pitch=pitch([(0, 0), (1, 0)], bad))
    assert synth_seg(pitch=pitch([(0, 0), (1, 0)], None))["pitch"]["roundness"] is None


def test_語調半音四捨五入到01_超出範圍拒絕():
    s = synth_seg(pitch=pitch([(0, 6.04), (1, -6.04)]))
    assert [p["semi"] for p in s["pitch"]["points"]] == [6.0, -6.0]
    for bad in (6.06, -6.06, 7, "3"):
        with pytest.raises(pj.ProjectError) as e:
            synth_seg(pitch=pitch([(0, bad), (1, 0)]))
        assert "半音" in errors_of(e)


def test_語調位置四捨五入到001():
    s = synth_seg(pitch=pitch([(0, 0), (0.505, 3), (1, 0)]))
    assert [p["pos"] for p in s["pitch"]["points"]] == [0.0, 0.51, 1.0]
    for bad in (-0.01, 1.01, "0.5"):
        with pytest.raises(pj.ProjectError):
            synth_seg(pitch=pitch([(0, 0), (bad, 0), (1, 0)]))


def test_語調關鍵點數量與遞增():
    with pytest.raises(pj.ProjectError):
        synth_seg(pitch=pitch([(0.5, 1)]))
    with pytest.raises(pj.ProjectError):
        synth_seg(pitch=pitch([(i / 16, 0) for i in range(17)]))
    assert len(synth_seg(pitch=pitch([(i / 15, 0) for i in range(16)]))["pitch"]["points"]) == 16
    with pytest.raises(pj.ProjectError) as e:
        synth_seg(pitch=pitch([(0, 0), (0.5, 1), (0.5, 2)]))
    assert "遞增" in errors_of(e)
    with pytest.raises(pj.ProjectError):   # 0.501 與 0.504 取到 0.01 後相同 → 不遞增
        synth_seg(pitch=pitch([(0.501, 0), (0.504, 1)]))
    with pytest.raises(pj.ProjectError):
        synth_seg(pitch={"points": [{"pos": 0, "semi": 0}, {"pos": 1, "semi": 0}], "roundness": 50, "x": 1})
    with pytest.raises(pj.ProjectError):
        synth_seg(pitch={"points": [[0, 0], [1, 0]], "roundness": 50})


def wc(start, end, value):
    return {"start": start, "end": end, "value": value}


def test_字詞清晰度合法():
    # 「大家好，歡迎收聽夜市的味道。」:夜市 = 索引 8、9
    s = synth_seg(word_clarity=[wc(8, 10, 40), wc(0, 2, 60)])
    assert s["word_clarity"] == [wc(0, 2, 60), wc(8, 10, 40)]   # 依起點排序


@pytest.mark.parametrize("bad", [
    [wc(8, 10, 40), wc(9, 11, 40)],      # 重疊
    [wc(12, 60, 40)],                    # 超出句長
    [wc(2, 5, 40)],                      # 含標點「，」
    [wc(13, 14, 40)],                    # 落在標點「。」
    [wc(5, 5, 40)],                      # 空區段
    [wc(8, 10, 101)],                    # 值超出範圍
    [wc(8, 10, "40")],
    [{"start": 8, "end": 10}],
    [wc(i, i + 1, 50) for i in range(14, 47)],   # 33 段
])
def test_字詞清晰度不合法(bad):
    s = seg("大家好，歡迎收聽夜市的味道。" + "夜市" * 20, word_clarity=bad)
    with pytest.raises(pj.ProjectError):
        pj.validate(data(segments=[s]))


def test_字詞清晰度上限32段():
    text = "夜" * 40
    ok = [wc(i, i + 1, 50) for i in range(32)]
    assert len(pj.validate(data(segments=[seg(text, word_clarity=ok)]))["segments"][0]["word_clarity"]) == 32


def test_post欄位格式():
    good = {"id": KEY_B[:32], "key": KEY_B, "seconds": 2.1, "trim": 0.4}
    assert synth_seg(post=good)["post"] == good
    for bad in ({"id": "x", "key": KEY_B, "seconds": 1, "trim": 0},
                {"id": KEY_B[:32], "key": "short", "seconds": 1, "trim": 0},
                {"id": KEY_B[:32], "key": KEY_B, "seconds": -1, "trim": 0},
                {"id": KEY_B[:32], "key": KEY_B, "seconds": 1},
                {"id": KEY_B[:32], "key": KEY_B, "seconds": 1, "trim": 0, "path": "/etc/passwd"}):
        with pytest.raises(pj.ProjectError):
            synth_seg(post=bad)


def test_PATCH不能寫入post():
    assert "post" not in ss.SEGMENT_PATCH_KEYS


# ---------- CHG-20261029-01 修訂 1 T11:post.pitch_points(R1-10;伺服器產生,PATCH 不能寫入) ----------

PP8 = [[0, 0], [0.175, 1.1], [0.25, 3.6], [0.425, 0.5], [0.5, -2.4], [0.675, -0.2], [0.75, 2.4], [1, 0]]


def test_post含pitch_points可載入且存檔往返():
    good = {"id": KEY_B[:32], "key": KEY_B, "seconds": 2.1, "trim": 0.4, "pitch_points": PP8}
    assert synth_seg(post=good)["post"] == good
    edge = [[i / 29, 7.2 if i % 2 else -7.2] for i in range(30)]          # 30 點、±7.2 為上限
    assert synth_seg(post={**good, "pitch_points": edge})["post"]["pitch_points"] == edge


@pytest.mark.parametrize("bad", [
    [[i / 30, 0] for i in range(31)],                    # 31 點
    [[0, 0], [0.5, 1], [0.5, 2]],                         # 位置不遞增
    [[0, 0], [0.6, 1], [0.4, 2]],
    [[0, 0], [1, 7.3]],                                   # 超過 ±7.2
    [[0, 0], [1, -7.3]],
    [[0, 0], [1.01, 1]], [[-0.01, 0], [1, 1]],            # 位置超出 0–1
    [[0, 0]], [],                                         # 少於 2 點
    [[0, "1"], [1, 0]], [[0, True], [1, 0]], [[0, 0, 0], [1, 0]], [{"pos": 0, "semi": 0}, [1, 0]],
    [[0, float("nan")], [1, 0]], None, "x", {"0": 0},
])
def test_post的pitch_points不合格為錯誤(bad):
    with pytest.raises(pj.ProjectError):
        synth_seg(post={"id": KEY_B[:32], "key": KEY_B, "seconds": 2.1, "trim": 0.4, "pitch_points": bad})


def test_PATCH仍不能寫入post_含pitch_points():
    assert "post" not in ss.SEGMENT_PATCH_KEYS and "pitch_points" not in ss.SEGMENT_PATCH_KEYS


def test_post_params以程式常數補預設():
    p = pj.post_params(synth_seg())
    assert p["connect"] == 0 and p["clarity"] == 100 and p["word_clarity"] == [] and p["pitch"] is None
    assert p["trim"] == pj.TRIM_PARAMS
    p = pj.post_params(synth_seg(pitch=pitch([(0, 0), (1, 2)], None)))
    assert p["pitch"] == {"points": [[0.0, 0.0], [1.0, 2.0]], "roundness": 50}
    assert pj.is_default_post(synth_seg())
    assert pj.is_default_post(synth_seg(connect=0, clarity=100))
    assert not pj.is_default_post(synth_seg(connect=1))
    assert not pj.is_default_post(synth_seg(word_clarity=[wc(8, 10, 40)]))


def test_post_key同參數相同_改任一參數不同_改語速不變():
    base = synth_seg()
    k = pj.post_key(base)
    assert len(k) == 64 and pj.KEY_RE.fullmatch(k)
    assert pj.post_key(synth_seg()) == k
    assert pj.post_key(synth_seg(connect=0, clarity=100)) == k          # null 與常數同義
    assert pj.post_key(synth_seg(speed=1.5)) == k
    assert pj.post_key(synth_seg(pause_after=2.0)) == k
    others = [synth_seg(connect=1), synth_seg(clarity=99), synth_seg(word_clarity=[wc(8, 10, 40)]),
              synth_seg(word_clarity=[wc(8, 10, 41)]), synth_seg(pitch=pitch([(0, 0), (1, 1)])),
              synth_seg(pitch=pitch([(0, 0), (1, 1)], 0)),
              synth_seg(audio={"id": KEY_B[:32], "seconds": 2.5, "key": KEY_B})]
    keys = {pj.post_key(s) for s in others}
    assert k not in keys and len(keys) == len(others)


def test_is_post_fresh():
    s = synth_seg()
    assert not pj.is_post_fresh(s)
    k = pj.post_key(s)
    s = synth_seg(post={"id": k[:32], "key": k, "seconds": 2.0, "trim": 0.5})
    assert pj.is_post_fresh(s)
    assert not pj.is_post_fresh({**s, "connect": 30})
    ext = pj.validate(data(segments=[seg("自錄。", audio={"id": KEY_B[:32], "seconds": 1.0},
                                         post={"id": k[:32], "key": k, "seconds": 2.0, "trim": 0.5})]))
    assert not pj.is_post_fresh(ext["segments"][0])      # 自錄音檔不後製
    assert not pj.is_post_fresh(pj.validate(data(segments=[seg()]))["segments"][0])
    with pytest.raises(pj.ProjectError):
        pj.post_key(ext["segments"][0])


# ---------- CHG-20261029-01 修訂 1 T10:後製鍵演算法標記(R1-11) ----------

# 以修訂前的公式(SHA-256(JSON 陣列 [audio.id, POST_VERSION, 裁切參數, connect, clarity, word_clarity,
# pitch]),不附演算法標記)對下列片段算出的固定值;只有裁切、只有連音的片段修訂後必須仍是這個鍵。
OLD_KEYS = {
    "trim": "a5b2f459ffc3121aac89c686a3457996dea02d342f48af0170bfafa55fb331eb",
    "connect50": "9ea0ec0dbbdf0067580762b11d4a3f5549948ce3f2d70f2d3db1fd46f75bcc96",
    "clarity50": "e3084a4d56a48ee60d9b24dcf4f34269ad555d6bdbdee5deeb8b45fafcfdeffd",
    "wc": "b821890912bbd68300bc293b7cd12f7163a28db8f29a10d102b8383b31264a4e",
    "pitch": "1900fad4a3651feff3e23e72e326129316c6c5ee80760a6c9861f6da47d2243b",
}


def rev1_cases():
    return {"trim": synth_seg(), "connect50": synth_seg(connect=50), "clarity50": synth_seg(clarity=50),
            "wc": synth_seg(word_clarity=[wc(8, 10, 40)]), "pitch": synth_seg(pitch=pitch([(0, 0), (1, 1)]))}


def test_演算法標記常數():
    assert pj.POST_VERSION == 1
    assert pj.POST_ALGO == {"clarity": 2, "pitch": 2}


def test_只有裁切或連音的片段鍵與修訂前相同():
    c = rev1_cases()
    assert pj.post_key(c["trim"]) == OLD_KEYS["trim"]
    assert pj.post_key(c["connect50"]) == OLD_KEYS["connect50"]
    assert pj.post_key(synth_seg(connect=50, speed=1.5)) == OLD_KEYS["connect50"]     # 改語速仍不改鍵


def test_有清晰度字詞清晰度或語調的片段鍵改變_修訂前的post過期():
    for name in ("clarity50", "wc", "pitch"):
        s = rev1_cases()[name]
        k = pj.post_key(s)
        assert k != OLD_KEYS[name], name
        assert pj.KEY_RE.fullmatch(k)
        old = OLD_KEYS[name]
        stale = {**s, "post": {"id": old[:32], "key": old, "seconds": 2.0, "trim": 0.5}}
        assert not pj.is_post_fresh(stale), name                         # 修訂前寫入的 post 失效
        assert pj.is_post_fresh({**s, "post": {"id": k[:32], "key": k, "seconds": 2.0, "trim": 0.5}})
    # 改語速仍不改鍵
    assert pj.post_key(synth_seg(clarity=50, speed=1.5)) == pj.post_key(rev1_cases()["clarity50"])
    assert pj.post_key(synth_seg(pitch=pitch([(0, 0), (1, 1)]), speed=0.8)) == pj.post_key(rev1_cases()["pitch"])


def test_演算法標記只附用到的部分():
    def key(parts):
        return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    a = KEY_A[:32]
    trim = dict(pj.TRIM_PARAMS)
    pts = {"points": [[0.0, 0.0], [1.0, 1.0]], "roundness": 50}
    assert pj.post_key(synth_seg(clarity=50)) == key([a, 1, trim, 0, 50, [], None, {"clarity": 2}])
    assert pj.post_key(synth_seg(word_clarity=[wc(8, 10, 40)])) == key(
        [a, 1, trim, 0, 100, [[8, 10, 40]], None, {"clarity": 2}])
    assert pj.post_key(synth_seg(pitch=pitch([(0, 0), (1, 1)]))) == key([a, 1, trim, 0, 100, [], pts, {"pitch": 2}])
    assert pj.post_key(synth_seg(clarity=50, pitch=pitch([(0, 0), (1, 1)]))) == key(
        [a, 1, trim, 0, 50, [], pts, {"clarity": 2, "pitch": 2}])
    assert pj.post_key(synth_seg(connect=30)) == key([a, 1, trim, 30, 100, [], None])


# ---------- CHG-20261029-01 T7:片段合音 ----------

def fz(index, zhuyin="ㄐㄧㄤˋ", source="rule", length=2):
    return {"index": index, "length": length, "zhuyin": zhuyin, "source": source}


def test_tts_text套用合音_第一字標註刪第二字():
    s = pj.validate(data(segments=[seg("是這樣啊", fusions=[fz(1)])]))["segments"][0]
    assert pj.tts_text(s) == "是這[:ㄐㄧㄤˋ]啊"
    s = pj.validate(data(segments=[seg("是這樣啊，我也不知道", fusions=[fz(8, "ㄗㄠˋ", "lexicon"), fz(1)])]))["segments"][0]
    assert [f["index"] for f in s["fusions"]] == [1, 8]
    assert pj.tts_text(s) == "是這[:ㄐㄧㄤˋ]啊，我也不知[:ㄗㄠˋ]"
    assert pj.fused_out(s) == {2, 9}


def test_v1專案載入後合音為空陣列(projects):
    d = projects / "夜市的味道"
    d.mkdir()
    (d / "project.json").write_text(json.dumps(v1_body(), ensure_ascii=False), encoding="utf-8")
    assert all(s["fusions"] == [] for s in pj.load("夜市的味道").segments)


@pytest.mark.parametrize("text,kw", [
    ("是這樣啊", {"fusions": [fz(1)], "annotations": [{"index": 2, "zhuyin": "ㄧㄤˋ"}]}),   # 範圍內有標註
    ("是這樣啊", {"fusions": [fz(1)], "annotations": [{"index": 1, "zhuyin": "ㄓㄜˋ"}]}),
    ("這樣樣子", {"fusions": [fz(0), fz(1)]}),                                                # 重疊
    ("是這，啊", {"fusions": [fz(1)]}),                                                       # 第二字不是漢字
    ("是這樣啊", {"fusions": [fz(3)]}),                                                       # 沒有第二字
    ("是這樣啊", {"fusions": [fz(1)], "word_clarity": [{"start": 2, "end": 4, "value": 40}]}),  # 字詞清晰度切過
    ("是這樣啊", {"fusions": [fz(1, "ㄐㄧㄤ4")]}),
    ("是這樣啊", {"fusions": [fz(1, source="auto")]}),
    ("是這樣啊", {"fusions": [{"index": 1, "zhuyin": "ㄐㄧㄤˋ", "source": "rule"}]}),        # 缺 length
    ("是這樣啊", {"fusions": [fz(1, length=1)]}),
    ("是這樣啊是這", {"fusions": [fz(0, "ㄕˋ ㄐㄧㄤˋ ㄚ ㄕˋ", length=5)]}),
    ("我不知道耶", {"fusions": [fz(1, "ㄅㄨˋ ㄓ ㄉㄠˋ", length=3)]}),                       # 三字配三音節
    ("我不知，耶", {"fusions": [fz(1, "ㄅㄨˋ ㄗㄠˋ", length=3)]}),                           # 範圍含非漢字
    ("我不知道耶", {"fusions": [fz(1, "ㄅㄨˋ  ㄗㄠˋ", length=3)]}),                         # 兩個空白
    ("我不知道耶", {"fusions": [fz(1, "ㄅㄨˋ ㄗㄠˋ", length=3), fz(3)]}),                     # 重疊
    ("這樣" * 17, {"fusions": [fz(2 * k) for k in range(17)]}),                               # 超過 16 組
])
def test_合音不合法(text, kw):
    with pytest.raises(pj.ProjectError):
        pj.validate(data(segments=[seg(text, **kw)]))


def test_字詞清晰度完整包含合音範圍可以():
    s = pj.validate(data(segments=[seg("是這樣啊", fusions=[fz(1)],
                                       word_clarity=[{"start": 0, "end": 3, "value": 40}])]))["segments"][0]
    assert s["fusions"] == [fz(1)]


def test_套用合音後cache_key改變_移除後回到原鍵():
    import tts_service as tts
    voice = tts.Voice(wav=Path("/dev/null"), text="逐字稿", wav_sha="a" * 64, text_sha="b" * 64)
    base = pj.validate(data(segments=[seg("是這樣啊")]))["segments"][0]
    fused = pj.validate(data(segments=[{**base, "fusions": [fz(1)]}]))["segments"][0]
    k0, k1 = tts.cache_key(base, voice), tts.cache_key(fused, voice)
    assert k0 != k1
    assert tts.cache_key({**fused, "fusions": []}, voice) == k0


def test_shape工作的字數以送模型的字為準():
    import tts_service as tts
    s = pj.validate(data(segments=[seg("是這樣啊，我也不知道", fusions=[fz(1), fz(8, "ㄗㄠˋ", "lexicon")],
                                       word_clarity=[{"start": 5, "end": 7, "value": 30}],
                                       audio={"id": KEY_A[:32], "seconds": 2.0, "key": KEY_A})]))["segments"][0]
    job = tts.shape_job(s, "c" * 32, "d" * 32)
    assert job["chars"] == 7                                   # 9 個漢字 − 2 個合音第二字
    assert job["word_clarity"] == [{"start": 3, "end": 5, "value": 30}]


def test_三字合音_tts_text前k字帶音節刪其餘字():
    s = pj.validate(data(segments=[seg("我也不知道", fusions=[fz(2, "ㄅㄨˋ ㄗㄠˋ", "lexicon", 3)])]))["segments"][0]
    assert pj.tts_text(s) == "我也不[:ㄅㄨˋ]知[:ㄗㄠˋ]"
    assert pj.fused_out(s) == {4}
    s = pj.validate(data(segments=[seg("怎麼樣都可以", fusions=[fz(0, "ㄗㄣˇ ㄧㄤˋ", "lexicon", 3)])]))["segments"][0]
    assert pj.tts_text(s) == "怎[:ㄗㄣˇ]麼[:ㄧㄤˋ]都可以"
    s = pj.validate(data(segments=[seg("就是這樣", fusions=[fz(0, "ㄐㄧㄡˋ ㄕˋ ㄐㄧㄤˋ", "rule", 4)])]))["segments"][0]
    assert pj.tts_text(s) == "就[:ㄐㄧㄡˋ]是[:ㄕˋ]這[:ㄐㄧㄤˋ]"


def test_fusion_off驗證_與fusions互斥():
    ok = pj.validate(data(segments=[seg("是這樣啊", fusion_off=[{"index": 1, "length": 2}])]))
    assert ok["segments"][0]["fusion_off"] == [{"index": 1, "length": 2}]
    for bad in ([{"index": 35, "length": 2}], [{"index": 0, "length": 5}], [{"index": 0}],
                [{"index": 0, "length": 2}] * 33, "x"):
        with pytest.raises(pj.ProjectError):
            pj.validate(data(segments=[seg("是這樣啊" * 9, fusion_off=bad)]))
    with pytest.raises(pj.ProjectError) as e:
        pj.validate(data(segments=[seg("是這樣啊", fusions=[fz(1)], fusion_off=[{"index": 1, "length": 2}])]))
    assert "同時" in errors_of(e)


def test_shift_marks依位移調整_改動範圍內清除():
    s = {"fusions": [fz(4, "ㄅㄨˋ ㄗㄠˋ", "lexicon", 3)],
         "fusion_off": [{"index": 0, "length": 2}, {"index": 8, "length": 2}]}
    old = "是這樣啊我不知道耶好的"
    got = pj.shift_marks(s, old, "是這樣啊，我不知道耶好的")          # 索引 4 插入一字
    assert got["fusions"] == [fz(5, "ㄅㄨˋ ㄗㄠˋ", "lexicon", 3)] and got["cleared"] == 0
    assert got["fusion_off"] == [{"index": 0, "length": 2}, {"index": 9, "length": 2}]
    got = pj.shift_marks(s, old, "是這樣啊我不道耶好的")              # 刪掉「知」(被合音的字)
    assert got["fusions"] == [] and got["cleared"] == 1
    assert got["fusion_off"] == [{"index": 0, "length": 2}, {"index": 7, "length": 2}]


# ---------- CHG-20261029-01 修訂 1 T12:合音送顯示字(R1-4、R1-5) ----------

def fzd(index, zhuyin, length, display, source="lexicon"):
    return {**fz(index, zhuyin, source, length), "display": display}


R1_TEXT = "是這樣啊，我也不知道。"


def test_tts_text有顯示字時以顯示字取代合音範圍且不加注音():
    s = pj.validate(data(segments=[seg(R1_TEXT, fusions=[fzd(1, "ㄐㄧㄤˋ", 2, "醬"),
                                                          fzd(7, "ㄅㄨˋ ㄗㄠˋ", 3, "不造")])]))["segments"][0]
    assert pj.tts_text(s) == "是醬啊，我也不造。"
    assert "[:" not in pj.tts_text(s)
    assert s["fusions"][0]["display"] == "醬" and s["fusions"][1]["display"] == "不造"


@pytest.mark.parametrize("display", [None, ""])
def test_tts_text沒有顯示字時沿用注音標註(display):
    fus = [fz(1, "ㄐㄧㄤˋ", "lexicon"), fz(7, "ㄅㄨˋ ㄗㄠˋ", "lexicon", 3)]
    if display is not None:
        fus = [{**f, "display": display} for f in fus]
    s = pj.validate(data(segments=[seg(R1_TEXT, fusions=fus)]))["segments"][0]
    assert pj.tts_text(s) == "是這[:ㄐㄧㄤˋ]啊，我也不[:ㄅㄨˋ]知[:ㄗㄠˋ]。"


def test_修訂前的合音專案檔可載入_tts_text與cache_key不變(projects):
    import tts_service as tts
    voice = tts.Voice(wav=Path("/dev/null"), text="逐字稿", wav_sha="a" * 64, text_sha="b" * 64)
    old = [fz(1, "ㄐㄧㄤˋ", "lexicon"), fz(7, "ㄅㄨˋ ㄗㄠˋ", "lexicon", 3)]
    body = data(segments=[seg(R1_TEXT, id="0123456789abcdef", fusions=old)])
    d = projects / "夜市的味道"
    d.mkdir()
    (d / "project.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    s = pj.load("夜市的味道").segments[0]
    assert s["fusions"] == old                                      # 不自動補 display(不回填)
    assert pj.tts_text(s) == "是這[:ㄐㄧㄤˋ]啊，我也不[:ㄅㄨˋ]知[:ㄗㄠˋ]。"
    # 以修訂前的公式(注音標註路線的合成文字 + 聲線 + 模型)算出的鍵比對
    assert tts.cache_key(s, voice) == hashlib.sha256(json.dumps(
        ["是這[:ㄐㄧㄤˋ]啊，我也不[:ㄅㄨˋ]知[:ㄗㄠˋ]。", "a" * 64, "b" * 64, voice.model],
        ensure_ascii=False).encode("utf-8")).hexdigest()


@pytest.mark.parametrize("display", ["是這樣啊是", "醬a", "a", "醬 ", 1, None, ["醬"], "［"])
def test_顯示字不合法回錯誤(display):
    with pytest.raises(pj.ProjectError) as e:
        pj.validate(data(segments=[seg(R1_TEXT, fusions=[fzd(1, "ㄐㄧㄤˋ", 2, display)])]))
    assert "顯示字" in errors_of(e)


def test_加上顯示字後cache_key改變():
    import tts_service as tts
    voice = tts.Voice(wav=Path("/dev/null"), text="逐字稿", wav_sha="a" * 64, text_sha="b" * 64)
    base = pj.validate(data(segments=[seg(R1_TEXT, fusions=[fz(1, "ㄐㄧㄤˋ", "lexicon")])]))["segments"][0]
    shown = pj.validate(data(segments=[{**base, "fusions": [fzd(1, "ㄐㄧㄤˋ", 2, "醬")]}]))["segments"][0]
    assert tts.cache_key(base, voice) != tts.cache_key(shown, voice)
    blank = pj.validate(data(segments=[{**base, "fusions": [fzd(1, "ㄐㄧㄤˋ", 2, "")]}]))["segments"][0]
    assert tts.cache_key(blank, voice) == tts.cache_key(base, voice)


def test_shape工作以送模型的字換算_顯示字():
    import tts_service as tts
    s = pj.validate(data(segments=[seg("我也不知道", fusions=[fzd(2, "ㄅㄨˋ ㄗㄠˋ", 3, "不造")],
                                       word_clarity=[{"start": 2, "end": 5, "value": 30}],
                                       audio={"id": KEY_A[:32], "seconds": 2.0, "key": KEY_A})]))["segments"][0]
    job = tts.shape_job(s, "c" * 32, "d" * 32)
    assert job["chars"] == 4                                     # 我、也 + 不、造
    assert job["word_clarity"] == [{"start": 2, "end": 4, "value": 30}]
    # 顯示字比原範圍長、合音前後都有字詞清晰度
    s = pj.validate(data(segments=[seg("是這樣啊好", fusions=[fzd(1, "ㄐㄧㄤˋ", 2, "醬醬醬")],
                                       word_clarity=[{"start": 0, "end": 1, "value": 20},
                                                     {"start": 1, "end": 3, "value": 30},
                                                     {"start": 4, "end": 5, "value": 40}],
                                       audio={"id": KEY_A[:32], "seconds": 2.0, "key": KEY_A})]))["segments"][0]
    assert pj.tts_text(s) == "是醬醬醬啊好"
    job = tts.shape_job(s, "c" * 32, "d" * 32)
    assert job["chars"] == 6
    assert job["word_clarity"] == [{"start": 0, "end": 1, "value": 20}, {"start": 1, "end": 4, "value": 30},
                                   {"start": 5, "end": 6, "value": 40}]
