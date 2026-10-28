# -*- coding: utf-8 -*-
"""studio_server.py 專案 API 的測試(CHG-20261022-04 T6)。

- 專案目錄(project.ROOT)、上傳與輸出目錄(create_app 的 root、taiwan_ai.OUT)、重型工作鎖
  (heavy_lock.LOCK_PATH)、聲線檔(taiwan_ai.ROOT)一律指到暫存目錄,不碰 repo 內的 projects/、outputs/、config/。
- 聲線設定以替身 `taiwan_ai.role_config` 提供,不讀設定資料庫。
- 合成:以替身 worker 腳本(本機 Python 執行)走真正的 `tts_service.synth` 路徑(CI 為 Linux、沒有 BreezyVoice);
  匯出與注音以 `create_app(render=…, annotate=…)` 換成替身,不跑 ffmpeg 與 G2PW。
- 以 FastAPI TestClient 呼叫 ASGI app,不開網路埠。
"""
from __future__ import annotations

import json
import math
import struct
import sys
import textwrap
import threading
import wave
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import heavy_lock as hl
import media
import project as pj
import settings
import studio_server as ss
import taiwan_ai
import tts_service as tts

BASE = "http://127.0.0.1:7860"
ORIGIN = "http://127.0.0.1:7860"
TOKEN = "測試用權杖-T6-專案".encode("utf-8").hex()
NAME = "夜市的味道"
SCRIPT = "今天天氣真好。我們去夜市吧！\n你有試過嗎？"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 64
MP3_ID3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 256

FAKE_WORKER = textwrap.dedent('''
    # -*- coding: utf-8 -*-
    import json, os, struct, sys, time, wave
    HERE = os.path.dirname(os.path.abspath(__file__))
    conf = {}
    if os.path.exists(os.path.join(HERE, "conf.json")):
        with open(os.path.join(HERE, "conf.json"), encoding="utf-8") as f:
            conf = json.load(f)
    args = sys.argv[1:]
    jobs_path, out = args[args.index("--jobs") + 1], args[args.index("--out") + 1]
    with open(jobs_path, encoding="utf-8") as f:
        body = json.load(f)
    with open(os.path.join(HERE, "calls.log"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"jobs": body["jobs"], "jobs_path": jobs_path}, ensure_ascii=False) + "\\n")
    for n, job in enumerate(body["jobs"]):
        if n:
            time.sleep(conf.get("delay", 0))
        if conf.get("fail_at") == n:
            print("✗ 第 %d 句合成失敗:替身" % (n + 1), file=sys.stderr, flush=True)
            sys.exit(4)
        with wave.open(os.path.join(out, job["id"] + ".wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(struct.pack("<h", 1000) * 8000)
        sys.stdout.write(json.dumps({"id": job["id"], "seconds": 0.5}) + "\\n")
        sys.stdout.flush()
''')


# ---------- 音檔 ----------

def pcm_wav(seconds: float = 0.4, rate: int = 16000) -> bytes:
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 7))) for i in range(int(seconds * rate))))
    return buf.getvalue()


def float32_wav(frames: int = 12345, rate: int = 22050, channels: int = 2) -> bytes:
    """IEEE float(format 3)WAV;`wave` 模組寫不出來,手動組 RIFF。"""
    data = struct.pack(f"<{frames * channels}f", *([0.25] * frames * channels))
    fmt = struct.pack("<HHIIHH", 3, channels, rate, rate * channels * 4, channels * 4, 32)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


# ---------- 環境 ----------

class Env:
    def __init__(self, tmp: Path):
        self.repo = tmp / "repo"
        self.fake = tmp / "fake"
        self.breezy = tmp / "breezy"
        self.render_calls: list = []
        self.annotate_calls: list = []
        self.render_gate: threading.Event | None = None
        self.render_started = threading.Event()

    def worker_calls(self) -> list[dict]:
        p = self.fake / "calls.log"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def render(self, proj):
        self.render_calls.append([s["id"] for s in proj.segments])
        self.render_started.set()
        if self.render_gate is not None:
            assert self.render_gate.wait(10), "測試沒有放行匯出"
        out = Path(taiwan_ai.OUT)
        out.mkdir(exist_ok=True)
        mp4, srt = out / "podcast-測試.mp4", out / "podcast-測試.srt"
        mp4.write_bytes(b"mp4")
        srt.write_text("1\n", encoding="utf-8")
        return mp4, srt

    def annotate(self, text):
        self.annotate_calls.append(text)
        return ["ㄐㄧㄣ" if "一" <= c <= "鿿" else None for c in text]


@pytest.fixture
def env(tmp_path, monkeypatch):
    tmp = tmp_path.resolve()
    e = Env(tmp)
    for d in (e.repo, e.fake, e.breezy, e.repo / "voice"):
        d.mkdir(parents=True, exist_ok=True)
    (e.fake / "worker.py").write_text(FAKE_WORKER, encoding="utf-8")
    (e.repo / "voice" / "voice.wav").write_bytes(pcm_wav(0.2))
    monkeypatch.setattr(pj, "ROOT", e.repo)
    monkeypatch.setattr(settings, "ROOT", e.repo)   # T8:新專案的語音預設讀設定資料庫(不碰 repo 內的 config/)
    monkeypatch.setattr(settings, "CONFIG_PATH", e.repo / "config" / "models.json")
    monkeypatch.setattr(taiwan_ai, "ROOT", e.repo)
    monkeypatch.setattr(taiwan_ai, "OUT", e.repo / "outputs")
    monkeypatch.setattr(hl, "ROOT", e.repo)
    monkeypatch.setattr(hl, "LOCK_PATH", e.repo / "outputs" / ".heavy.lock")
    monkeypatch.setattr(tts, "BREEZY", e.breezy)
    monkeypatch.setattr(taiwan_ai, "role_config",
                        lambda role: {"wav": "voice/voice.wav", "text": "這是聲線逐字稿。"})
    monkeypatch.setattr(tts, "_worker_argv",
                        lambda jobs, out: [sys.executable, str(e.fake / "worker.py"), "synth",
                                           "--jobs", str(jobs), "--out", str(out)])
    return e


@pytest.fixture
def client(env):
    app = ss.create_app(runner=lambda g, emit, c=None: None, token=TOKEN, static_dir=env.repo / "no-static",
                        root=env.repo, render=env.render, annotate=env.annotate)
    return TestClient(app, base_url=BASE)


def auth(**extra):
    return {"Origin": ORIGIN, "X-Studio-Token": TOKEN, **extra}


def create(client, name=NAME, script=SCRIPT):
    r = client.post("/api/projects", json={"name": name, "script": script}, headers=auth())
    assert r.status_code == 201, r.text
    return r.json()


def get(client, name=NAME) -> dict:
    r = client.get(f"/api/projects/{name}")
    assert r.status_code == 200, r.text
    return r.json()


def upload(client, filename, data) -> str:
    r = client.post("/api/upload", params={"name": filename}, content=data, headers=auth())
    assert r.status_code == 200, r.text
    return r.json()["file_id"]


def patch_segment(client, sid, body, name=NAME):
    return client.patch(f"/api/projects/{name}/segments/{sid}", json=body, headers=auth())


def read_sse(client, run_id) -> list[dict]:
    events = []
    with client.stream("GET", f"/api/run/{run_id}/events") as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
    return events


def record_all(client, name=NAME) -> dict:
    """新增專案後每一句都以自錄 WAV 取代(外部音檔不算髒),讓專案可以匯出。"""
    view = create(client, name)
    for s in view["project"]["segments"]:
        fid = upload(client, "錄音.wav", pcm_wav(0.3))
        r = patch_segment(client, s["id"], {"audio_file": fid}, name)
        assert r.status_code == 200, r.text
    return get(client, name)


def no_abs_path(text: str, env) -> bool:
    return str(env.repo) not in text and str(Path.home()) not in text


# ---------- 中介層與名稱 ----------

@pytest.mark.parametrize("method,path", [
    ("post", "/api/projects"), ("put", f"/api/projects/{NAME}"), ("delete", f"/api/projects/{NAME}"),
    ("patch", f"/api/projects/{NAME}/segments/0123456789abcdef"), ("post", f"/api/projects/{NAME}/synth"),
    ("post", f"/api/projects/{NAME}/render"), ("post", "/api/annotate"),
    ("post", f"/api/projects/{NAME}/images"), ("post", f"/api/projects/{NAME}/restore"),
])
def test_missing_token_is_403(client, env, method, path):
    create(client)
    r = client.request(method.upper(), path, json={"confirm": True, "name": "x", "text": "好"},
                       headers={"Origin": ORIGIN})
    assert r.status_code == 403
    assert (env.repo / "projects" / NAME / "project.json").is_file()


@pytest.mark.parametrize("path", ["/api/projects", f"/api/projects/{NAME}",
                                  f"/api/projects/{NAME}/media/0123456789abcdef"])
def test_wrong_host_is_403_even_for_get(client, path):
    create(client)
    r = client.get(path, headers={"Host": "evil.example:7860"})
    assert r.status_code == 403


def test_wrong_host_on_post_is_403(client, env):
    r = client.post("/api/projects", json={"name": "別的"}, headers=auth(Host="evil.example"))
    assert r.status_code == 403
    assert not (env.repo / "projects").exists()


def test_encoded_dotdot_name_is_400(client):
    create(client)
    for method in ("GET", "PUT", "DELETE"):
        r = client.request(method, "/api/projects/%2e%2e", json={"confirm": True}, headers=auth())
        assert r.status_code == 400, (method, r.status_code)
        assert "專案名稱" in r.json()["error"]
    r = client.get("/api/projects/%2E%2E/history")
    assert r.status_code == 400


@pytest.mark.parametrize("bad", ["a..b", "..", "x.y", "夜市 味道", "a/b", "", "x" * 65])
def test_name_with_dots_or_bad_chars_is_400(client, env, bad):
    r = client.post("/api/projects", json={"name": bad}, headers=auth())
    assert r.status_code == 400
    assert not list((env.repo / "projects").glob("*")) if (env.repo / "projects").exists() else True


def test_name_with_dotdot_in_path_is_400(client):
    r = client.get("/api/projects/a..b")
    assert r.status_code == 400


def test_files_endpoint_does_not_serve_projects(client, env):
    create(client)
    assert (env.repo / "projects" / NAME / "project.json").is_file()
    for rel in (f"projects/{NAME}/project.json", f"outputs/../projects/{NAME}/project.json",
                f"outputs/%2e%2e/projects/{NAME}/project.json"):
        r = client.get(f"/files/{rel}")
        assert r.status_code == 404, rel


def test_no_workflow_list_endpoint(client):
    r = client.get("/api/workflows")
    assert r.status_code in (404, 405)


# ---------- 新增、列表、載入、存檔 ----------

def test_create_splits_script_and_lists(client, env):
    view = create(client)
    segs = view["project"]["segments"]
    assert [s["text"] for s in segs] == ["今天天氣真好。", "我們去夜市吧！", "你有試過嗎？"]
    assert [s["break"] for s in segs] == ["sentence", "paragraph", "sentence"]
    assert all(pj.ID_RE.fullmatch(s["id"]) and s["audio"] is None for s in segs)
    assert view["project"]["defaults"] == pj.DEFAULTS
    assert view["layout"]["duration"] == 0
    r = client.get("/api/projects")
    assert r.status_code == 200
    assert [p["name"] for p in r.json()["projects"]] == [NAME]
    assert client.post("/api/projects", json={"name": NAME}, headers=auth()).status_code == 409


def test_create_uses_voice_defaults_hook(env):
    app = ss.create_app(token=TOKEN, static_dir=env.repo / "x", root=env.repo,
                        voice_defaults=lambda: {**pj.DEFAULTS, "speed": 1.3, "pause_sentence": 0.6})
    c = TestClient(app, base_url=BASE)
    view = create(c)
    assert view["project"]["defaults"]["speed"] == 1.3
    assert view["project"]["defaults"]["pause_sentence"] == 0.6


def test_new_project_defaults_come_from_settings_voice_defaults(client, env):
    """T8:預設的 _voice_defaults() 讀設定資料庫的 voice_defaults(設定頁「語音預設」)。"""
    import copy
    cfg = copy.deepcopy(settings.DEFAULTS)
    cfg["voice_defaults"] = {"speed": 1.4, "pause_comma": 0.1, "pause_sentence": 0.7, "pause_paragraph": 1.2}
    settings.save(cfg)
    view = create(client)
    assert view["project"]["defaults"] == cfg["voice_defaults"]
    assert ss._voice_defaults() == cfg["voice_defaults"]


def test_voice_defaults_fall_back_to_project_defaults(env, monkeypatch):
    monkeypatch.setattr(settings, "load", lambda: {"voice_defaults": {"speed": 9}})
    assert ss._voice_defaults() == pj.DEFAULTS
    monkeypatch.setattr(settings, "load", lambda: {})
    assert ss._voice_defaults() == pj.DEFAULTS


def test_create_rejects_bad_script_with_422(client):
    r = client.post("/api/projects", json={"name": NAME, "script": "字" * 501}, headers=auth())
    assert r.status_code == 422
    assert "500" in r.json()["error"]


def test_get_missing_project_is_404(client):
    assert client.get("/api/projects/不存在").status_code == 404


def test_put_script_keeps_unchanged_segments(client):
    view = record_all(client)
    old = view["project"]["segments"]
    r = client.put(f"/api/projects/{NAME}", json={"script": "今天天氣真好。我們去吃宵夜吧！\n你有試過嗎？"},
                   headers=auth())
    assert r.status_code == 200, r.text
    new = r.json()["project"]["segments"]
    assert new[0]["id"] == old[0]["id"] and new[0]["audio"] == old[0]["audio"]
    assert new[2]["id"] == old[2]["id"] and new[2]["audio"] == old[2]["audio"]
    assert new[1]["id"] != old[1]["id"] and new[1]["audio"] is None
    assert r.json()["version"] is not None


def test_put_defaults_and_empty_save(client):
    create(client)
    r = client.put(f"/api/projects/{NAME}", json={"defaults": {"speed": 1.23}}, headers=auth())
    assert r.status_code == 200
    assert r.json()["project"]["defaults"]["speed"] == 1.25
    r = client.put(f"/api/projects/{NAME}", json={"defaults": {"speed": 2.5}}, headers=auth())
    assert r.status_code == 422
    r = client.put(f"/api/projects/{NAME}", content=b"", headers=auth())
    assert r.status_code == 200
    r = client.put(f"/api/projects/{NAME}", json={"bogus": 1}, headers=auth())
    assert r.status_code == 422


# ---------- 刪除 ----------

def test_delete_without_confirm_is_400(client, env):
    create(client)
    for body in (None, {}, {"confirm": False}, {"confirm": "true"}, {"confirm": 1}):
        kw = {"json": body} if body is not None else {}
        r = client.request("DELETE", f"/api/projects/{NAME}", headers=auth(), **kw)
        assert r.status_code == 400, body
    assert (env.repo / "projects" / NAME / "project.json").is_file()


def test_delete_moves_to_trash(client, env):
    create(client)
    r = client.request("DELETE", f"/api/projects/{NAME}", json={"confirm": True}, headers=auth())
    assert r.status_code == 200, r.text
    trash = env.repo / "projects" / ".trash"
    assert not (env.repo / "projects" / NAME).exists()
    assert (trash / r.json()["trash"] / "project.json").is_file()
    assert client.get(f"/api/projects/{NAME}").status_code == 404
    assert client.request("DELETE", f"/api/projects/{NAME}", json={"confirm": True},
                          headers=auth()).status_code == 404


def test_delete_same_name_twice_gives_two_trash_dirs(client, env):
    names = []
    for _ in range(2):
        create(client)
        r = client.request("DELETE", f"/api/projects/{NAME}", json={"confirm": True}, headers=auth())
        assert r.status_code == 200
        names.append(r.json()["trash"])
    trash = env.repo / "projects" / ".trash"
    assert names[0] != names[1]
    assert sorted(p.name for p in trash.iterdir()) == sorted(names)
    assert all(n.startswith(NAME + "-") for n in names)


# ---------- 版本 ----------

def test_restore_adds_history_and_restores_content(client, env):
    create(client)
    client.put(f"/api/projects/{NAME}", json={"script": "第一版。"}, headers=auth())
    client.put(f"/api/projects/{NAME}", json={"script": "第二版。"}, headers=auth())
    versions = client.get(f"/api/projects/{NAME}/history").json()["versions"]
    assert len(versions) == 2
    target = versions[-1]   # 存「第二版」之前的內容 = 第一版
    expected = json.loads((env.repo / "projects" / NAME / "history" / f"{target}.json").read_text("utf-8"))
    assert expected["script"] == "第一版。"
    r = client.post(f"/api/projects/{NAME}/restore", json={"version": target}, headers=auth())
    assert r.status_code == 200, r.text
    assert len(client.get(f"/api/projects/{NAME}/history").json()["versions"]) == 3
    assert r.json()["project"] == expected
    assert pj.load(NAME).to_dict() == expected


@pytest.mark.parametrize("vid", ["../x", "20261027-000000-000-zzzz", "20990101-000000-000-abcd", 5])
def test_restore_unknown_version_is_404(client, vid):
    create(client)
    r = client.post(f"/api/projects/{NAME}/restore", json={"version": vid}, headers=auth())
    assert r.status_code == 404


# ---------- 片段 ----------

def test_invalid_zhuyin_is_422(client):
    seg = create(client)["project"]["segments"][0]
    for z in ("hao3", "ㄏㄠ4", "ㄏㄠˋ[", "", "ㄏㄠˋㄏㄠˋ"):
        r = patch_segment(client, seg["id"], {"annotations": [{"index": 4, "zhuyin": z}]})
        assert r.status_code == 422, z
    assert get(client)["project"]["segments"][0]["annotations"] == []
    r = patch_segment(client, seg["id"], {"annotations": [{"index": 4, "zhuyin": "ㄏㄠˇ"}]})
    assert r.status_code == 200, r.text
    assert r.json()["project"]["segments"][0]["annotations"] == [{"index": 4, "zhuyin": "ㄏㄠˇ"}]


def test_segment_pause_speed_and_bad_values(client):
    seg = create(client)["project"]["segments"][1]
    r = patch_segment(client, seg["id"], {"pause_after": 1.53, "speed": 1.3})
    assert r.status_code == 200
    got = r.json()["project"]["segments"][1]
    assert (got["pause_after"], got["speed"]) == (1.5, 1.3)
    assert patch_segment(client, seg["id"], {"pause_after": 5.5}).status_code == 422
    assert patch_segment(client, seg["id"], {"speed": 0.45}).status_code == 422
    assert patch_segment(client, seg["id"], {"nope": 1}).status_code == 422


@pytest.mark.parametrize("sid", ["zzzz", "0123456789abcdef", "ABCDEF0123456789"])
def test_unknown_segment_is_404(client, sid):
    create(client)
    assert patch_segment(client, sid, {"pause_after": 1}).status_code == 404


def test_text_edit_updates_script_and_clears_external_audio(client):
    view = record_all(client)
    seg = view["project"]["segments"][1]
    assert seg["audio"] and "key" not in seg["audio"]
    r = patch_segment(client, seg["id"], {"text": "我們去逛夜市吧！"})
    assert r.status_code == 200
    body = r.json()
    assert body["project"]["script"] == "今天天氣真好。我們去逛夜市吧！\n你有試過嗎？"
    assert body["project"]["segments"][1]["audio"] is None   # 自錄音檔已不符新文字
    assert body["project"]["segments"][0]["audio"] == view["project"]["segments"][0]["audio"]
    assert patch_segment(client, seg["id"], {"text": "換\n行"}).status_code == 422


def test_split_and_merge_forced_break(client):
    seg = create(client)["project"]["segments"][0]
    r = patch_segment(client, seg["id"], {"split_at": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    segs = body["project"]["segments"]
    assert [s["text"] for s in segs[:2]] == ["今天", "天氣真好。"]
    assert segs[0]["break"] == "forced" and segs[1]["id"] == body["new_segment"]
    assert body["forced_breaks"] == [1]
    # 重新分句(只改別句)時帶回 forced_breaks,強制停頓保留
    r = client.put(f"/api/projects/{NAME}", json={"script": body["project"]["script"],
                                                  "forced_breaks": body["forced_breaks"]}, headers=auth())
    assert [s["id"] for s in r.json()["project"]["segments"]] == [s["id"] for s in segs]
    r = patch_segment(client, seg["id"], {"merge_next": True})
    assert r.status_code == 200
    assert r.json()["project"]["segments"][0]["text"] == "今天天氣真好。"
    assert patch_segment(client, seg["id"], {"merge_next": True}).status_code == 422
    assert patch_segment(client, seg["id"], {"split_at": 6}).status_code == 422


# ---------- 自錄音檔取代(修訂 3,R3-6) ----------

def test_replace_with_mp3_content_is_422_and_segment_unchanged(client, env):
    seg = create(client)["project"]["segments"][0]
    before = get(client)["project"]
    fid = upload(client, "錄音.mp3", MP3_ID3)
    r = patch_segment(client, seg["id"], {"audio_file": fid})
    assert r.status_code == 422
    msg = r.json()["error"]
    assert msg == ss.WAV_ONLY and "WAV" in msg and "音檔" in msg
    assert get(client)["project"] == before
    adir = env.repo / "projects" / NAME / "audio"
    assert not adir.exists() or list(adir.iterdir()) == []


def test_replace_with_float32_wav_uses_wav_seconds(client, env):
    seg = create(client)["project"]["segments"][2]
    data = float32_wav()
    fid = upload(client, "錄音.wav", data)
    r = patch_segment(client, seg["id"], {"audio_file": fid})
    assert r.status_code == 200, r.text
    audio = r.json()["project"]["segments"][2]["audio"]
    expected = media.wav_seconds(env.repo / "outputs" / fid)
    assert audio["seconds"] == expected
    assert "key" not in audio
    stored = env.repo / "projects" / NAME / "audio" / f"{audio['id']}.wav"
    assert stored.read_bytes() == data
    assert media.wav_seconds(stored) == expected
    # 外部音檔不算髒:合成時不覆蓋
    assert seg["id"] not in [s["id"] for s in tts.dirty_segments(pj.load(NAME))]


def test_replace_with_unknown_upload_is_404(client):
    seg = create(client)["project"]["segments"][0]
    for fid in ("uploads/" + "0" * 32 + ".wav", "../project.json", "uploads/x.wav", 3):
        assert patch_segment(client, seg["id"], {"audio_file": fid}).status_code == 404


# ---------- 素材、畫面軌與 media 端點 ----------

def test_media_by_asset_id_and_404_for_unknown_or_other_project(client, env):
    create(client)
    create(client, "別的專案", "另一個專案。")
    fid = upload(client, "封面.png", PNG)
    r = client.post(f"/api/projects/{NAME}/images", json={"file_id": fid, "start": 1.04}, headers=auth())
    assert r.status_code == 200, r.text
    body = r.json()
    img = body["project"]["images"][0]
    assert img["start"] == 1.0 and img["id"] == body["image"]
    aid = img["asset"]
    assert (env.repo / "projects" / NAME / "assets" / f"{aid}.png").read_bytes() == PNG
    ok = client.get(f"/api/projects/{NAME}/media/{aid}")
    assert ok.status_code == 200
    assert ok.content == PNG
    assert ok.headers["content-type"] == "image/png"
    assert ok.headers["x-content-type-options"] == "nosniff"
    assert client.get(f"/api/projects/別的專案/media/{aid}").status_code == 404   # 他專案的 id
    assert client.get(f"/api/projects/{NAME}/media/0123456789abcdef").status_code == 404   # 不存在
    assert client.get(f"/api/projects/{NAME}/media/{aid}.png").status_code == 404
    assert client.get(f"/api/projects/{NAME}/media/%2e%2e").status_code == 404
    assert client.get(f"/api/projects/不存在/media/{aid}").status_code == 404


def test_media_serves_segment_audio_and_rejects_symlink(client, env):
    view = record_all(client)
    aid = view["project"]["segments"][0]["audio"]["id"]
    r = client.get(f"/api/projects/{NAME}/media/{aid}")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    path = env.repo / "projects" / NAME / "audio" / f"{aid}.wav"
    outside = env.repo / "secret.wav"
    outside.write_bytes(pcm_wav(0.1))
    path.unlink()
    path.symlink_to(outside)
    assert client.get(f"/api/projects/{NAME}/media/{aid}").status_code == 404


def test_images_patch_delete_and_assets(client, env):
    create(client)
    fid = upload(client, "封面.png", PNG)
    asset = client.post(f"/api/projects/{NAME}/assets", json={"file_id": fid, "label": "封面"},
                        headers=auth()).json()["asset"]
    assert asset["kind"] == "image" and asset["label"] == "封面" and asset["bytes"] == len(PNG)
    r = client.post(f"/api/projects/{NAME}/images", json={"asset": asset["id"], "start": 0, "end": 3},
                    headers=auth())
    iid = r.json()["image"]
    r = client.patch(f"/api/projects/{NAME}/images/{iid}", json={"start": 1, "end": 0.5}, headers=auth())
    assert r.status_code == 422
    r = client.patch(f"/api/projects/{NAME}/images/{iid}", json={"start": 1, "end": 2.26}, headers=auth())
    assert r.status_code == 200
    assert r.json()["project"]["images"][0]["end"] == 2.3
    r = client.delete(f"/api/projects/{NAME}/images/{iid}", headers=auth())
    assert r.status_code == 200 and r.json()["project"]["images"] == []
    assert (env.repo / "projects" / NAME / "assets" / f"{asset['id']}.png").is_file()   # 素材檔不刪
    txt = upload(client, "文稿.txt", "台灣。".encode("utf-8"))
    r = client.post(f"/api/projects/{NAME}/images", json={"file_id": txt}, headers=auth())
    assert r.status_code == 422
    assert not any(p.suffix == ".txt" for p in (env.repo / "projects" / NAME / "assets").iterdir())


# ---------- 合成(SSE)----------

def test_synth_streams_segments_saves_and_empties_tmp(client, env):
    create(client)
    r = client.post(f"/api/projects/{NAME}/synth", headers=auth())
    assert r.status_code == 200, r.text
    events = read_sse(client, r.json()["run_id"])
    segs = [e for e in events if e["type"] == "segment"]
    assert len(segs) == 3
    assert events[-1]["type"] == "finished" and events[-1]["status"] == "ok"
    assert events[-1]["saved"] == 3
    proj = pj.load(NAME)
    assert all(s["audio"] and len(s["audio"]["key"]) == 64 for s in proj.segments)
    tmp = env.repo / "projects" / NAME / "tmp"
    assert tmp.is_dir() and list(tmp.iterdir()) == []
    # 第二次:全部快取命中,不再啟動 worker
    calls = len(env.worker_calls())
    r = client.post(f"/api/projects/{NAME}/synth", headers=auth())
    events = read_sse(client, r.json()["run_id"])
    assert len(env.worker_calls()) == calls
    assert events[-1]["status"] == "ok"


def test_synth_failure_keeps_done_segments_and_empties_tmp(client, env):
    create(client)
    (env.fake / "conf.json").write_text(json.dumps({"fail_at": 1}), encoding="utf-8")
    r = client.post(f"/api/projects/{NAME}/synth", headers=auth())
    events = read_sse(client, r.json()["run_id"])
    errors = [e for e in events if e["type"] == "error"]
    assert errors and "替身" in errors[0]["message"]
    assert no_abs_path(json.dumps(events, ensure_ascii=False), env)
    assert events[-1]["status"] == "error"
    proj = pj.load(NAME)
    assert proj.segments[0]["audio"] is not None and proj.segments[1]["audio"] is None
    assert list((env.repo / "projects" / NAME / "tmp").iterdir()) == []
    assert not Path(env.worker_calls()[0]["jobs_path"]).exists()


def test_synth_mid_run_audio_playable_history_once_and_edit_not_overwritten(client, env):
    """DIR-5 統整 M-1(使用者故事 13):替身 worker 三句、每句間隔 1 秒。
    第一句完成後、finished 前:專案檢視第一句有 audio、layout 該句 end > start、media 端點 200;
    合成期間改第二句文字 → 結束後第二句沒有被寫入舊音檔;finished 後 history 只比合成前多 1。"""
    import time as _time
    view = create(client)
    sids = [s["id"] for s in view["project"]["segments"]]
    assert len(sids) == 3
    (env.fake / "conf.json").write_text(json.dumps({"delay": 1.0}), encoding="utf-8")
    r = client.post(f"/api/projects/{NAME}/synth", headers=auth())
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    deadline = _time.monotonic() + 10
    while True:
        v = get(client)
        if v["project"]["segments"][0]["audio"]:
            break
        assert _time.monotonic() < deadline, "第一句一直沒有出現在專案檢視"
        _time.sleep(0.05)
    assert v["project"]["segments"][2]["audio"] is None           # 整批尚未結束
    lay0 = next(x for x in v["layout"]["segments"] if x["id"] == sids[0])
    assert lay0["end"] > lay0["start"]
    aid = v["project"]["segments"][0]["audio"]["id"]
    m = client.get(f"/api/projects/{NAME}/media/{aid}")
    assert m.status_code == 200 and m.headers["content-type"] == "audio/wav"
    assert pj.load(NAME).segments[0]["audio"] is None                # 中途沒有存檔
    r = patch_segment(client, sids[1], {"text": "我們去士林夜市吧！"})   # 合成期間改第二句
    assert r.status_code == 200, r.text
    assert r.json()["project"]["segments"][0]["audio"] == v["project"]["segments"][0]["audio"]
    versions = len(client.get(f"/api/projects/{NAME}/history").json()["versions"])
    events = read_sse(client, run_id)
    assert events[-1]["type"] == "finished" and events[-1]["status"] == "ok"
    assert all("audio" in e for e in events if e["type"] == "segment")
    assert len(client.get(f"/api/projects/{NAME}/history").json()["versions"]) == versions + 1
    proj = pj.load(NAME)
    assert proj.segments[0]["audio"]["id"] == aid
    assert proj.segments[1]["text"] == "我們去士林夜市吧！" and proj.segments[1]["audio"] is None
    assert proj.segments[2]["audio"] is not None
    final = get(client)
    assert final["project"]["segments"][1]["audio"] is None             # 結束後不再疊記憶體中的音檔


def test_synth_unknown_project_is_404_and_does_not_hold_flag(client):
    assert client.post("/api/projects/不存在/synth", headers=auth()).status_code == 404
    create(client)
    assert client.post(f"/api/projects/{NAME}/synth", headers=auth()).status_code == 200


# ---------- 匯出 ----------

def test_render_without_synth_is_400_and_lists_segments(client, env):
    view = create(client)
    r = client.post(f"/api/projects/{NAME}/render", headers=auth())
    assert r.status_code == 400
    assert r.json()["segments"] == [s["id"] for s in view["project"]["segments"]]
    assert "尚未合成" in r.json()["error"]
    assert env.render_calls == []


def test_render_lists_stale_synthesized_segments(client, env):
    create(client)
    r = client.post(f"/api/projects/{NAME}/synth", headers=auth())
    read_sse(client, r.json()["run_id"])
    seg = get(client)["project"]["segments"][1]
    patch_segment(client, seg["id"], {"text": "我們去逛夜市吧！"})
    r = client.post(f"/api/projects/{NAME}/render", headers=auth())
    assert r.status_code == 400
    assert r.json()["segments"] == [seg["id"]]


def test_render_runs_and_synth_during_render_is_409(client, env):
    record_all(client)
    env.render_gate = threading.Event()
    r = client.post(f"/api/projects/{NAME}/render", headers=auth())
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    assert env.render_started.wait(10)
    r2 = client.post(f"/api/projects/{NAME}/synth", headers=auth())
    assert r2.status_code == 409
    assert client.post("/api/annotate", json={"text": "好"}, headers=auth()).status_code == 409
    assert client.request("DELETE", f"/api/projects/{NAME}", json={"confirm": True},
                          headers=auth()).status_code == 409
    env.render_gate.set()
    events = read_sse(client, run_id)
    assert events[-1] == {"type": "finished", "status": "ok", "mp4": "outputs/podcast-測試.mp4",
                          "srt": "outputs/podcast-測試.srt"}
    assert client.post(f"/api/projects/{NAME}/synth", headers=auth()).status_code == 200


# ---------- 注音 ----------

def test_annotate_limits(client, env):
    r = client.post("/api/annotate", json={"text": "好" * 501}, headers=auth())
    assert r.status_code == 422
    assert env.annotate_calls == []
    r = client.post("/api/annotate", json={"text": "今天a"}, headers=auth())
    assert r.status_code == 200
    assert r.json() == {"text": "今天a", "zhuyin": ["ㄐㄧㄣ", "ㄐㄧㄣ", None]}
    assert client.post("/api/annotate", json={"text": ""}, headers=auth()).status_code == 422


def test_annotate_default_runs_worker_and_removes_tmp(env, monkeypatch):
    calls = []

    class Done:
        returncode = 0
        stdout = json.dumps(["ㄏㄠˇ"], ensure_ascii=False) + "\n"
        stderr = ""

    def fake_run(argv, **kw):
        inp = Path(argv[argv.index("--input") + 1])
        calls.append({"argv": argv, "kw": kw, "input": json.loads(inp.read_text("utf-8")), "path": inp})
        return Done()

    monkeypatch.setattr(ss.subprocess, "run", fake_run)
    app = ss.create_app(token=TOKEN, static_dir=env.repo / "x", root=env.repo)
    r = TestClient(app, base_url=BASE).post("/api/annotate", json={"text": "好"}, headers=auth())
    assert r.status_code == 200, r.text
    assert r.json()["zhuyin"] == ["ㄏㄠˇ"]
    c = calls[0]
    assert c["input"] == {"text": "好"} and "好" not in " ".join(c["argv"])
    assert c["kw"]["env"] == taiwan_ai.BREEZY_ENV and c["kw"]["cwd"] == str(env.breezy)
    assert c["kw"]["timeout"] == ss.ANNOTATE_TIMEOUT
    assert c["path"].parent == env.repo / "outputs" / ".tmp" and not c["path"].exists()


# ---------- 啟動清理 ----------

def test_main_calls_startup_cleanup(monkeypatch):
    seen = []
    monkeypatch.setattr(pj, "startup_cleanup",
                        lambda now: seen.append(now) or {"trash_removed": [], "tmp_cleared": 0, "warnings": []})

    class FakeUvicorn:
        @staticmethod
        def run(app, **kw):
            seen.append(("run", kw["host"], kw["port"]))

    monkeypatch.setattr(ss, "_import_uvicorn", lambda: FakeUvicorn)
    ss.main()
    assert isinstance(seen[0], datetime) and seen[0].tzinfo is timezone.utc
    assert seen[1] == ("run", "127.0.0.1", 7860)


def test_startup_cleanup_failure_does_not_block(monkeypatch):
    def boom(now):
        raise OSError("/Users/someone/secret")
    monkeypatch.setattr(pj, "startup_cleanup", boom)
    assert ss.startup_cleanup() == {}


def test_error_bodies_have_no_absolute_paths(client, env):
    create(client)
    (env.repo / "projects" / NAME / "project.json").write_text("{壞掉", encoding="utf-8")
    r = client.get(f"/api/projects/{NAME}")
    assert r.status_code == 422
    assert no_abs_path(r.text, env)
