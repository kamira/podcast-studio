# -*- coding: utf-8 -*-
"""project.py 的測試(CHG-20261022-04 T2:專案資料模型)。

每個測試把 project.ROOT 指到暫存目錄(`<tmp>/repo`),不碰 repo 內的 `projects/`;
「projects/ 外」的連結目標放在 `<tmp>/outside`。時間一律以注入的 now 判定。
"""
from __future__ import annotations

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
    assert raw["schema_version"] == 1


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


def test_schema_version_2_refused(projects):
    d = projects / "夜市的味道"
    d.mkdir()
    body = data()
    body["schema_version"] = 2
    (d / "project.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(pj.ProjectError) as e:
        pj.load("夜市的味道")
    assert "版本" in errors_of(e)
    for v in (2, 0, "1", None, True, 1.0):
        body["schema_version"] = v
        with pytest.raises(pj.ProjectError):
            pj.validate(body)
    with pytest.raises(pj.ProjectError):
        pj.save(pj.Project(name="夜市的味道", schema_version=2))


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
        assert pj._rotate(projects / "夜市的味道", warnings) == []
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
