"""settings.py 的測試(CHG-20261022-01 T1)。

每個測試把 settings.ROOT / CONFIG_PATH 指到暫存目錄,不動 repo 內的 config/models.json。
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

import settings

ALL = {
    settings.DEFAULTS["roles"][r]["model"] for r in settings.LLM_ROLES
} | {v for v in settings.DEFAULTS["review_seats"].values() if v != settings.CODEX}

REPO_CONFIG = Path(settings.__file__).resolve().parent / "config" / "models.json"


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr(settings, "ROOT", root)
    monkeypatch.setattr(settings, "CONFIG_PATH", root / "config" / "models.json")
    (root / "tools/BreezyVoice/data").mkdir(parents=True)
    (root / "outputs/uploads").mkdir(parents=True)
    return root


def cfg():
    return copy.deepcopy(settings.DEFAULTS)


# ---- load ----

def test_檔案不存在回預設值(env):
    assert not settings.CONFIG_PATH.exists()
    got = settings.load()
    assert got == settings.DEFAULTS
    assert "_warnings" not in got
    got["roles"]["reason"]["model"] = "x"  # 回傳的是副本
    assert settings.DEFAULTS["roles"]["reason"]["model"] != "x"


def test_壞JSON回預設值並帶警告(env, capsys):
    settings.CONFIG_PATH.parent.mkdir(parents=True)
    settings.CONFIG_PATH.write_text("{ 不是 JSON", encoding="utf-8")
    got = settings.load()
    warnings = got.pop("_warnings")
    assert warnings == [settings.WARN_BAD_JSON] == ["設定檔無法解析,已使用預設值"]
    assert got == settings.DEFAULTS
    assert "設定檔無法解析" in capsys.readouterr().err


def test_結構不合法的檔案回預設值並帶警告(env):
    settings.CONFIG_PATH.parent.mkdir(parents=True)
    bad = cfg()
    bad["roles"]["image"]["steps"] = 99
    settings.CONFIG_PATH.write_text(json.dumps(bad), encoding="utf-8")
    got = settings.load()
    assert got.pop("_warnings") == [settings.WARN_INVALID]
    assert got == settings.DEFAULTS


# ---- validate ----

def test_預設值在模型都已下載時合格(env):
    assert settings.validate(cfg(), ALL) == []


def test_未下載的模型回錯誤(env):
    c = cfg()
    c["roles"]["reason"]["model"] = "not-a-model:1b"
    errs = settings.validate(c, ALL)
    assert any(e.startswith("roles.reason.model") and "尚未下載" in e for e in errs)
    c = cfg()
    c["review_seats"]["drift"] = "not-a-model:1b"
    assert any(e.startswith("review_seats.drift") for e in settings.validate(c, ALL))


def test_結構檢查不看模型清單(env):
    c = cfg()
    c["roles"]["reason"]["model"] = "not-a-model:1b"
    assert settings.validate(c, None) == []


@pytest.mark.parametrize("seat", ["risk", "security", "compliance"])
def test_否決席設glm4回錯誤(env, seat):
    c = cfg()
    c["review_seats"][seat] = "glm4:latest"
    errs = settings.validate(c, ALL | {"glm4:latest"})
    assert any(e.startswith(f"review_seats.{seat}") and "glm4" in e for e in errs)


def test_非否決席可用glm4_但不可用codex(env):
    c = cfg()
    c["review_seats"]["impact"] = "glm4:latest"
    assert settings.validate(c, ALL) == []
    c["review_seats"]["impact"] = settings.CODEX
    assert any(e.startswith("review_seats.impact") for e in settings.validate(c, ALL))


def test_否決席可設codex且不需在模型清單(env):
    c = cfg()
    for s in settings.VETO_SEATS:
        c["review_seats"][s] = settings.CODEX
    assert settings.validate(c, ALL) == []


@pytest.mark.parametrize("wav", [
    "/etc/passwd",
    "outputs/other/a.wav",
    "tools/BreezyVoice/data/../../../x.wav",
    "outputs/uploads",
    "",
])
def test_允許目錄外的聲線路徑回錯誤(env, wav):
    c = cfg()
    c["roles"]["voice"]["wav"] = wav
    errs = settings.validate(c, ALL)
    assert any(e.startswith("roles.voice.wav") for e in errs), errs


def test_允許目錄內的聲線路徑合格(env):
    c = cfg()
    c["roles"]["voice"]["wav"] = "outputs/uploads/me.wav"
    assert settings.validate(c, ALL) == []
    c["roles"]["voice"]["wav"] = str(env / "tools/BreezyVoice/data/example.wav")
    assert settings.validate(c, ALL) == []


def test_符號連結逐層拒絕(env, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside").resolve()
    (outside / "x.wav").write_bytes(b"RIFF")
    # 允許目錄內的檔案是指向外部的連結
    (env / "outputs/uploads/evil.wav").symlink_to(outside / "x.wav")
    # 允許目錄內的子目錄是連結
    (env / "outputs/uploads/sub").symlink_to(outside)
    # 繞道:允許目錄外的連結指回允許目錄
    (env / "outputs/alias").symlink_to(env / "outputs/uploads")
    # 允許目錄內指向允許目錄內的連結也拒絕(逐層一律不接受連結)
    (env / "outputs/uploads/ok.wav").write_bytes(b"RIFF")
    (env / "outputs/uploads/link.wav").symlink_to(env / "outputs/uploads/ok.wav")
    for wav in ("outputs/uploads/evil.wav", "outputs/uploads/sub/x.wav",
                "outputs/alias/ok.wav", "outputs/uploads/link.wav"):
        c = cfg()
        c["roles"]["voice"]["wav"] = wav
        errs = settings.validate(c, ALL)
        assert any(e.startswith("roles.voice.wav") for e in errs), (wav, errs)


def test_允許目錄本身是符號連結時拒絕(env, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside2").resolve()
    (env / "outputs/uploads").rmdir()
    (env / "outputs/uploads").symlink_to(outside)
    c = cfg()
    c["roles"]["voice"]["wav"] = "outputs/uploads/x.wav"
    errs = settings.validate(c, ALL)
    assert any(e.startswith("roles.voice.wav") for e in errs), errs


@pytest.mark.parametrize("path,value", [
    (("roles", "image", "steps"), 0),
    (("roles", "image", "steps"), 9),
    (("roles", "image", "steps"), True),
    (("roles", "reason", "num_ctx"), 1024),
    (("roles", "reason", "num_ctx"), 131072),
    (("roles", "reason", "num_ctx"), "16384"),
    (("roles", "reason", "think"), "yes"),
    (("roles", "voice", "text"), ""),
])
def test_數值與型別範圍(env, path, value):
    c = cfg()
    c[path[0]][path[1]][path[2]] = value
    errs = settings.validate(c, ALL)
    assert any(e.startswith(".".join(path)) for e in errs), errs


def test_邊界值合格(env):
    c = cfg()
    c["roles"]["image"]["steps"] = 8
    c["roles"]["reason"]["num_ctx"] = 65536
    c["roles"]["taiwan"]["num_ctx"] = 2048
    assert settings.validate(c, ALL) == []


def test_未知鍵與缺鍵回錯誤(env):
    c = cfg()
    c["extra"] = 1
    c["roles"]["reason"]["temperature"] = 0.5
    c["review_seats"]["judge"] = "qwen3:8b"
    del c["roles"]["image"]["steps"]
    errs = settings.validate(c, ALL)
    for prefix in ("設定.extra", "roles.reason.temperature", "review_seats.judge", "roles.image.steps"):
        assert any(e.startswith(prefix) for e in errs), (prefix, errs)


def test_非物件回錯誤(env):
    assert settings.validate([], ALL)
    assert settings.validate({"roles": [], "review_seats": {}}, ALL)


# ---- save ----

def test_save後load相等(env):
    c = cfg()
    c["roles"]["reason"]["model"] = "qwen3:8b"
    c["roles"]["image"]["steps"] = 2
    settings.save(c)
    assert settings.load() == c
    assert json.loads(settings.CONFIG_PATH.read_text(encoding="utf-8")) == c


def test_save去除警告鍵(env):
    c = cfg()
    c["_warnings"] = ["x"]
    settings.save(c)
    assert "_warnings" not in settings.load()


def test_save拒絕結構不合格且不動原檔(env):
    settings.save(cfg())
    before = settings.CONFIG_PATH.read_bytes()
    bad = cfg()
    bad["roles"]["image"]["steps"] = 100
    with pytest.raises(ValueError):
        settings.save(bad)
    assert settings.CONFIG_PATH.read_bytes() == before


def test_save中斷不留半個檔(env, monkeypatch):
    settings.save(cfg())
    before = settings.CONFIG_PATH.read_bytes()

    def boom(*a, **k):
        raise OSError("模擬寫到一半中斷")

    monkeypatch.setattr(settings.os, "replace", boom)
    c = cfg()
    c["roles"]["reason"]["model"] = "qwen3:8b"
    with pytest.raises(OSError):
        settings.save(c)
    assert settings.CONFIG_PATH.read_bytes() == before
    assert os.listdir(settings.CONFIG_PATH.parent) == ["models.json"]


def test_save中斷且原本無檔時不產生檔案(env, monkeypatch):
    real_fsync = os.fsync

    def boom(fd):
        real_fsync(fd)
        raise OSError("模擬寫入後當機")

    monkeypatch.setattr(settings.os, "fsync", boom)
    with pytest.raises(OSError):
        settings.save(cfg())
    assert not settings.CONFIG_PATH.exists()
    assert os.listdir(settings.CONFIG_PATH.parent) == []


# ---- repo 內的設定檔 ----

def test_repo內config等於DEFAULTS():
    text = REPO_CONFIG.read_text(encoding="utf-8")
    assert json.loads(text) == settings.DEFAULTS
    assert text == json.dumps(settings.DEFAULTS, ensure_ascii=False, indent=2) + "\n"


# ---- 依審議修正:暫停中的 27B 不得擔任審議席位(DIR-1 第 10 條) ----

HEAVY = "hf.co/HauhauCS/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF:IQ3_M"


@pytest.mark.parametrize("seat", settings.SEATS)
def test_任一席位設27B回錯誤(env, seat):
    assert HEAVY in settings.PAUSED_REVIEW_MODELS
    c = cfg()
    c["review_seats"][seat] = HEAVY
    for avail in (ALL | {HEAVY}, None):
        errs = settings.validate(c, avail)
        assert any(e.startswith(f"review_seats.{seat}") and "DIR-1 第 10 條" in e for e in errs), errs


def test_DEFAULTS仍通過且reason_heavy不受影響(env):
    assert settings.DEFAULTS["roles"]["reason_heavy"]["model"] == HEAVY
    assert settings.validate(cfg(), ALL) == []
    assert settings.validate(cfg(), None) == []


# ---- 依審議修正:設定檔讀寫逐層拒絕符號連結 ----

def _outside_with_config(tmp_path_factory, name):
    outside = tmp_path_factory.mktemp(name).resolve()
    c = cfg()
    c["roles"]["reason"]["model"] = "外部檔案的值"
    (outside / "models.json").write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    return outside


def test_config目錄是符號連結時不讀不寫外部(env, tmp_path_factory):
    outside = _outside_with_config(tmp_path_factory, "outside_cfg")
    before = sorted(os.listdir(outside))
    original = (outside / "models.json").read_bytes()
    (env / "config").symlink_to(outside)
    got = settings.load()
    assert got.pop("_warnings") == [settings.WARN_UNSAFE_PATH]
    assert got == settings.DEFAULTS
    with pytest.raises(ValueError):
        settings.save(cfg())
    assert sorted(os.listdir(outside)) == before  # 沒有暫存檔
    assert (outside / "models.json").read_bytes() == original


def test_models_json是符號連結時不讀不寫外部(env, tmp_path_factory):
    outside = _outside_with_config(tmp_path_factory, "outside_file")
    before = sorted(os.listdir(outside))
    original = (outside / "models.json").read_bytes()
    (env / "config").mkdir()
    settings.CONFIG_PATH.symlink_to(outside / "models.json")
    got = settings.load()
    assert got.pop("_warnings") == [settings.WARN_UNSAFE_PATH]
    assert got == settings.DEFAULTS
    with pytest.raises(ValueError):
        settings.save(cfg())
    assert os.listdir(env / "config") == ["models.json"]  # 沒有暫存檔
    assert settings.CONFIG_PATH.is_symlink()
    assert sorted(os.listdir(outside)) == before
    assert (outside / "models.json").read_bytes() == original


def test_CONFIG_PATH不在repo內時拒絕(env, monkeypatch, tmp_path_factory):
    outside = _outside_with_config(tmp_path_factory, "outside_path")
    monkeypatch.setattr(settings, "CONFIG_PATH", outside / "models.json")
    got = settings.load()
    assert got.pop("_warnings") == [settings.WARN_UNSAFE_PATH]
    with pytest.raises(ValueError):
        settings.save(cfg())
    assert sorted(os.listdir(outside)) == ["models.json"]


def test_config目錄不存在時save在repo內建立(env):
    assert not (env / "config").exists()
    settings.save(cfg())
    assert (env / "config").is_dir() and not (env / "config").is_symlink()
    assert settings.load() == settings.DEFAULTS
