"""nodes.py 節點登錄表的測試(CHG-20261022-01 T5)。

模型與外部程序一律以 monkeypatch 替身取代(`taiwan_ai.chat`、`taiwan_ai.speak`、
`media.generate_image`、`media.make_video`),不連網、不呼叫 Ollama / mflux / BreezyVoice / ffmpeg。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

import media
import nodes
import taiwan_ai as tai

PORT_TYPES = {"text", "image", "audio", "video"}
KINDS = {"text-input", "file-input", "write-script", "revise", "taiwanize", "speak",
         "cover-prompt", "generate-image", "add-title", "make-video"}
HEAVY = {"write-script", "revise", "taiwanize", "speak", "cover-prompt", "generate-image", "make-video"}


# ---------- 替身 ----------

class FakeAI:
    """替身 taiwan_ai.chat / speak:記錄每次呼叫,回傳固定內容。"""

    def __init__(self, out: Path):
        self.out = out
        self.chats: list[dict] = []
        self.spoken: list[str] = []

    def chat(self, role, prompt, system="", think=None):
        self.chats.append({"role": role, "prompt": prompt, "system": system, "think": think})
        return f"{role} 回覆"

    def speak(self, text):
        self.spoken.append(text)
        p = tai.reserve("speech", ".wav")
        p.write_bytes(b"RIFF")
        return p


@pytest.fixture
def out(monkeypatch, tmp_path):
    d = tmp_path / "outputs"
    d.mkdir()
    monkeypatch.setattr(tai, "OUT", d)
    return d


@pytest.fixture
def ai(monkeypatch, out):
    fake = FakeAI(out)
    monkeypatch.setattr(tai, "chat", fake.chat)
    monkeypatch.setattr(tai, "speak", fake.speak)
    return fake


@pytest.fixture
def fake_media(monkeypatch, out):
    calls: dict[str, list] = {"generate_image": [], "make_video": []}

    def generate_image(prompt, width=1024, height=1024):
        calls["generate_image"].append((prompt, width, height))
        p = tai.reserve("image", ".png")
        Image.new("RGB", (width, height), "navy").save(p)
        return p

    def make_video(image_path, audio_path, script=""):
        calls["make_video"].append((str(image_path), str(audio_path), script))
        p = tai.reserve("podcast", ".mp4")
        p.write_bytes(b"MP4")
        return p

    def add_title(img, title):  # 替身:CI(Linux)沒有 media.FONT 的 macOS 字型
        calls.setdefault("add_title", []).append((img.size, title))
        return img

    monkeypatch.setattr(media, "generate_image", generate_image)
    monkeypatch.setattr(media, "make_video", make_video)
    monkeypatch.setattr(media, "add_title", add_title)
    return calls


def _run(kind, inputs=None, params=None):
    return nodes.REGISTRY[kind].run(inputs or {}, params or {})


# ---------- 登錄表結構與埠型別 ----------

def test_登錄表恰為十種節點():
    assert set(nodes.REGISTRY) == KINDS
    for kind, spec in nodes.REGISTRY.items():
        assert isinstance(spec, nodes.NodeSpec)
        assert spec.kind == kind


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_每種節點的埠型別符合全域約束(kind):
    spec = nodes.REGISTRY[kind]
    assert spec.outputs, f"{kind} 沒有輸出埠"
    for port in [*spec.inputs.values(), *spec.outputs.values()]:
        assert port.type in PORT_TYPES
    assert nodes.PORT_TYPES == frozenset(PORT_TYPES)


def test_各節點宣告的埠():
    def types(ports):
        return {k: p.type for k, p in ports.items()}

    R = nodes.REGISTRY
    assert types(R["text-input"].inputs) == {}
    assert types(R["text-input"].outputs) == {"text": "text"}
    assert types(R["file-input"].inputs) == {}
    assert types(R["file-input"].outputs) == {"text": "text", "image": "image", "audio": "audio"}
    assert types(R["write-script"].inputs) == {"topic": "text"}
    assert types(R["revise"].inputs) == {"text": "text"}
    assert types(R["taiwanize"].inputs) == {"text": "text"}
    for k in ("write-script", "revise", "taiwanize", "cover-prompt"):
        assert types(R[k].outputs) == {"text": "text"}
    assert types(R["speak"].inputs) == {"text": "text"}
    assert types(R["speak"].outputs) == {"audio": "audio"}
    assert types(R["cover-prompt"].inputs) == {"title": "text", "script": "text"}
    assert R["cover-prompt"].inputs["script"].required is False
    assert types(R["generate-image"].inputs) == {"prompt": "text"}
    assert types(R["generate-image"].outputs) == {"image": "image"}
    assert types(R["add-title"].inputs) == {"image": "image", "title": "text"}
    assert types(R["add-title"].outputs) == {"image": "image"}
    assert types(R["make-video"].inputs) == {"image": "image", "audio": "audio", "script": "text"}
    assert R["make-video"].inputs["script"].required is False
    assert types(R["make-video"].outputs) == {"video": "video"}


def test_heavy旗標只標在LLM生圖語音影片節點():
    assert {k for k, s in nodes.REGISTRY.items() if s.heavy} == HEAVY


def test_參數宣告型別合法且預設值通過自身驗證():
    for kind, spec in nodes.REGISTRY.items():
        for name, p in spec.params.items():
            assert p.type in nodes.PARAM_TYPES, (kind, name)
            if p.type == "str":
                assert p.max_len is not None and p.max_len <= nodes.MAX_TEXT, (kind, name)
            if p.default is not None:
                assert p.check(p.default) is None, (kind, name)


@pytest.mark.parametrize("kind,name,bad", [
    ("generate-image", "width", 17),
    ("generate-image", "width", 4096),
    ("generate-image", "height", 128),
    ("generate-image", "width", "1024"),
    ("generate-image", "width", True),
    ("write-script", "minutes", 0.25),
    ("write-script", "minutes", 11),
    ("write-script", "minutes", 1.3),
    ("write-script", "style", "饒舌"),
    ("text-input", "text", "字" * 20001),
    ("text-input", "text", 123),
    ("make-video", "subtitles", "yes"),
    ("file-input", "kind", "video"),
])
def test_參數超出宣告範圍時check回錯誤訊息(kind, name, bad):
    msg = nodes.REGISTRY[kind].params[name].check(bad)
    assert isinstance(msg, str) and msg


def test_spec_dict可序列化且不含run():
    import json
    d = nodes.REGISTRY["generate-image"].spec_dict()
    json.dumps(d)
    assert d["kind"] == "generate-image" and d["heavy"] is True
    assert d["outputs"] == {"image": {"type": "image", "required": True, "label": "圖片"}}
    assert d["params"]["width"]["multiple_of"] == 16
    assert "run" not in d


# ---------- 以替身執行每個節點 ----------

def test_text_input回傳文字():
    assert _run("text-input", params={"text": "台灣夜市文化"}) == {"text": "台灣夜市文化"}


def test_write_script字數等於分鐘乘250並寫入prompt(ai):
    res = _run("write-script", {"topic": "台灣夜市文化"},
               {"minutes": 1.5, "style": "知識科普", "notes": "加入小故事"})
    assert set(res) == {"text"}
    assert len(ai.chats) == 1
    c = ai.chats[0]
    assert c["role"] == "reason" and c["think"] is False
    assert "長度:約 375 字" in c["prompt"]
    assert "主題:台灣夜市文化" in c["prompt"] and "風格:知識科普" in c["prompt"]
    assert "補充要求:加入小故事" in c["prompt"]
    assert tai.TW in c["system"]


@pytest.mark.parametrize("minutes,chars", [(0.5, 125), (2, 500), (10, 2500)])
def test_write_script各長度字數(ai, minutes, chars):
    _run("write-script", {"topic": "主題"}, {"minutes": minutes})
    assert f"約 {chars} 字" in ai.chats[0]["prompt"]


def test_write_script預設參數(ai):
    _run("write-script", {"topic": "主題"})
    p = ai.chats[0]["prompt"]
    assert "約 500 字" in p and "風格:輕鬆聊天" in p and "補充要求:無" in p


def test_write_script主題空白時失敗(ai):
    with pytest.raises(ValueError, match="主題"):
        _run("write-script", {"topic": "  "})
    assert ai.chats == []


def test_revise依指示修改並轉台灣用語(ai, monkeypatch):
    monkeypatch.setattr(tai, "to_tw", lambda s: f"TW[{s}]")
    res = _run("revise", {"text": "原稿"}, {"instruction": "開頭更吸引人"})
    assert res == {"text": "TW[reason 回覆]"}
    c = ai.chats[0]
    assert c["role"] == "reason" and c["think"] is False
    assert "指示:開頭更吸引人" in c["prompt"] and "原稿:\n原稿" in c["prompt"]


def test_revise缺指示時失敗(ai):
    with pytest.raises(ValueError):
        _run("revise", {"text": "原稿"}, {"instruction": ""})
    assert ai.chats == []


def test_taiwanize以taiwan角色潤飾並轉換(ai, monkeypatch):
    monkeypatch.setattr(tai, "to_tw", lambda s: f"TW[{s}]")
    res = _run("taiwanize", {"text": "草稿內容"})
    assert res == {"text": "TW[taiwan 回覆]"}
    c = ai.chats[0]
    assert c["role"] == "taiwan" and c["system"] == tai.TW
    assert c["prompt"].endswith("\n\n草稿內容")


def test_speak回傳outputs下的音檔(ai, out):
    res = _run("speak", {"text": "大家好"})
    assert set(res) == {"audio"}
    assert Path(res["audio"]).parent == out and Path(res["audio"]).exists()
    assert ai.spoken == ["大家好"]


def test_speak文字空白時失敗(ai):
    with pytest.raises(ValueError):
        _run("speak", {"text": "  "})
    assert ai.spoken == []


def test_cover_prompt取標題與文案前600字(ai):
    res = _run("cover-prompt", {"title": "夜市的味道", "script": "字" * 1000}, {"extra": "暖色調"})
    assert res == {"text": "reason 回覆"}
    c = ai.chats[0]
    assert c["role"] == "reason" and c["think"] is False
    assert c["prompt"] == f"節目標題:夜市的味道\n內容摘要:{'字' * 600}\n額外要求:暖色調"
    assert "No text" in c["system"]


def test_cover_prompt沒接文案也可執行(ai):
    _run("cover-prompt", {"title": "夜市的味道"})
    assert ai.chats[0]["prompt"] == "節目標題:夜市的味道\n內容摘要:\n額外要求:無"


def test_cover_prompt標題空白時失敗(ai):
    with pytest.raises(ValueError, match="標題"):
        _run("cover-prompt", {"title": ""})


def test_generate_image預設尺寸同原封面(fake_media, out):
    res = _run("generate-image", {"prompt": "night market"})
    assert set(res) == {"image"} and Path(res["image"]).parent == out
    assert fake_media["generate_image"] == [("night market", media.FLUX_W, media.FLUX_H)]


def test_generate_image傳入寬高(fake_media):
    _run("generate-image", {"prompt": "p"}, {"width": 512, "height": 256})
    assert fake_media["generate_image"] == [("p", 512, 256)]


def _add_title_case(out):
    src = tai.reserve("image", ".png")
    Image.new("RGB", (1360, 768), "navy").save(src)
    before = src.read_bytes()
    res = _run("add-title", {"image": str(src), "title": "夜市的味道"})
    assert set(res) == {"image"}
    p = Path(res["image"])
    assert p != src and p.parent == out and p.name.startswith("cover-")
    assert Image.open(p).size == (media.VIDEO_W, media.VIDEO_H)
    assert src.read_bytes() == before


def test_add_title存成新的16比9封面且不改原圖(fake_media, out):
    _add_title_case(out)
    assert fake_media["add_title"] == [((media.VIDEO_W, media.VIDEO_H), "夜市的味道")]


@pytest.mark.skipif(not Path(media.FONT).exists(), reason="本機沒有 media.FONT 字型(例如 Linux CI)")
def test_add_title以真實字型疊標題(out):
    _add_title_case(out)


def test_add_title失敗時不留佔位檔(out, monkeypatch):
    src = tai.reserve("image", ".png")
    Image.new("RGB", (64, 64)).save(src)

    def boom(img, title):
        raise RuntimeError("字型壞了")

    monkeypatch.setattr(media, "add_title", boom)
    with pytest.raises(RuntimeError):
        _run("add-title", {"image": str(src), "title": "標題"})
    assert sorted(x.name for x in out.iterdir()) == [src.name]


def test_make_video含字幕(fake_media, out):
    res = _run("make-video", {"image": "/x/c.png", "audio": "/x/a.wav", "script": "文案"})
    assert set(res) == {"video"} and Path(res["video"]).parent == out
    assert fake_media["make_video"] == [("/x/c.png", "/x/a.wav", "文案")]


def test_make_video關閉字幕或沒接文案時不傳文案(fake_media):
    _run("make-video", {"image": "c.png", "audio": "a.wav", "script": "文案"}, {"subtitles": False})
    _run("make-video", {"image": "c.png", "audio": "a.wav"})
    assert [c[2] for c in fake_media["make_video"]] == ["", ""]


# ---------- file-input ----------

@pytest.fixture
def uploads(out):
    d = out / "uploads"
    d.mkdir()
    return d


def test_file_input文字檔輸出內容(uploads):
    (uploads / "a.txt").write_text("我的文稿", encoding="utf-8")
    res = _run("file-input", params={"file": "uploads/a.txt", "kind": "text"})
    assert res == {"text": "我的文稿"}
    assert set(res) == set(nodes.REGISTRY["file-input"].output_ports({"kind": "text"}))


@pytest.mark.parametrize("name,kind", [("b.png", "image"), ("c.wav", "audio"), ("d.mp3", "audio")])
def test_file_input媒體檔輸出路徑(uploads, name, kind):
    (uploads / name).write_bytes(b"x")
    res = _run("file-input", params={"file": f"uploads/{name}", "kind": kind})
    assert res == {kind: str((uploads / name).resolve())}
    assert set(nodes.REGISTRY["file-input"].output_ports({"kind": kind})) == {kind}


@pytest.mark.parametrize("rel", ["../secret.txt", "/etc/passwd", "uploads/../../secret.txt"])
def test_file_input拒絕outputs以外的路徑(uploads, tmp_path, rel):
    (tmp_path / "secret.txt").write_text("秘密", encoding="utf-8")
    with pytest.raises(ValueError):
        _run("file-input", params={"file": rel, "kind": "text"})


def test_file_input拒絕指向外部的符號連結(uploads, tmp_path):
    (tmp_path / "secret.txt").write_text("秘密", encoding="utf-8")
    (uploads / "link.txt").symlink_to(tmp_path / "secret.txt")
    with pytest.raises(ValueError):
        _run("file-input", params={"file": "uploads/link.txt", "kind": "text"})


def test_file_input副檔名與種類不符時失敗(uploads):
    (uploads / "b.png").write_bytes(b"x")
    with pytest.raises(ValueError, match="不符"):
        _run("file-input", params={"file": "uploads/b.png", "kind": "audio"})


def test_file_input檔案不存在時失敗(uploads):
    with pytest.raises(ValueError):
        _run("file-input", params={"file": "uploads/none.txt", "kind": "text"})


# ---------- 共通:每個節點回傳宣告的輸出鍵 ----------

def _sample_inputs(out):
    img = tai.reserve("image", ".png")
    Image.new("RGB", (64, 64)).save(img)
    (out / "uploads").mkdir(exist_ok=True)
    (out / "uploads" / "a.txt").write_text("文稿", encoding="utf-8")
    return {
        "text-input": ({}, {"text": "主題"}),
        "file-input": ({}, {"file": "uploads/a.txt", "kind": "text"}),
        "write-script": ({"topic": "主題"}, {}),
        "revise": ({"text": "原稿"}, {"instruction": "改短"}),
        "taiwanize": ({"text": "原稿"}, {}),
        "speak": ({"text": "大家好"}, {}),
        "cover-prompt": ({"title": "標題", "script": "文案"}, {}),
        "generate-image": ({"prompt": "p"}, {}),
        "add-title": ({"image": str(img), "title": "標題"}, {}),
        "make-video": ({"image": str(img), "audio": "a.wav", "script": "文案"}, {}),
    }


def test_每個節點以替身執行都回傳宣告的輸出鍵(ai, fake_media, out):
    samples = _sample_inputs(out)
    assert set(samples) == KINDS
    for kind, (inputs, params) in samples.items():
        spec = nodes.REGISTRY[kind]
        res = spec.run(inputs, params)
        assert set(res) == set(spec.output_ports(params)), kind
        assert set(res) <= set(spec.outputs), kind
        assert all(isinstance(v, str) for v in res.values()), kind


def test_節點本身不取重型鎖(ai, fake_media, out, monkeypatch):
    import heavy_lock

    def forbidden(*a, **k):
        raise AssertionError("節點不得自行取 heavy_lock(由執行器負責)")

    monkeypatch.setattr(heavy_lock, "heavy_lock", forbidden)
    for kind, (inputs, params) in _sample_inputs(out).items():
        nodes.REGISTRY[kind].run(inputs, params)
    import ast
    tree = ast.parse(Path(nodes.__file__).read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert "heavy_lock" not in imported
