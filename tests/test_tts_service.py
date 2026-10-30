# -*- coding: utf-8 -*-
"""tts_service.py 的測試(CHG-20261022-04 T3:合成服務)。

- 專案目錄(project.ROOT)、聲線檔(taiwan_ai.ROOT)、重型工作鎖(heavy_lock.LOCK_PATH)一律指到暫存目錄,
  不碰 repo 內的 projects/、outputs/、config/。
- 聲線設定以替身 `taiwan_ai.role_config` 提供(並計數)。
- worker 以替身腳本代替(`_worker_argv` 換成本機 Python 執行替身),但仍經 tts_service 的 Popen 路徑:
  argv list、`env=taiwan_ai.BREEZY_ENV`、`cwd=BREEZY`。替身把每次呼叫、每行進度的時間記在自己的目錄,
  設定(每句間隔、失敗模式)也從自己的目錄讀(環境變數一律是 BREEZY_ENV,無法用環境變數傳設定)。
- 語速測試實跑 imageio-ffmpeg 附帶的 ffmpeg。
"""
from __future__ import annotations

import contextlib
import json
import math
import struct
import subprocess
import sys
import textwrap
import time
import wave
from pathlib import Path

import pytest

import heavy_lock as hl
import media
import project as pj
import taiwan_ai
import tts_service as tts

FAKE_WORKER = textwrap.dedent('''
    # -*- coding: utf-8 -*-
    import json, math, os, struct, sys, time, wave
    HERE = os.path.dirname(os.path.abspath(__file__))
    conf = {}
    if os.path.exists(os.path.join(HERE, "conf.json")):
        with open(os.path.join(HERE, "conf.json"), encoding="utf-8") as f:
            conf = json.load(f)
    args = sys.argv[1:]
    assert args[0] == "synth", args
    jobs_path, out = args[args.index("--jobs") + 1], args[args.index("--out") + 1]
    with open(jobs_path, encoding="utf-8") as f:
        body = json.load(f)
    def log(name, row):
        with open(os.path.join(HERE, name), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\\n")
    log("calls.log", {"jobs": body["jobs"], "prompt_text": body["prompt_text"], "model": body.get("model"),
                      "cwd": os.getcwd(), "env": dict(os.environ)})
    print("BreezyVoice 雜訊 /Users/someone/secret/model.bin", file=sys.stderr, flush=True)
    if conf.get("fail"):
        print("Traceback: File /Users/someone/tools/BreezyVoice/x.py line 3", file=sys.stderr)
        print(conf["fail"], file=sys.stderr, flush=True)
        sys.exit(conf.get("code", 4))
    secs = conf.get("seconds", 0.5)
    rate = 16000
    for n, job in enumerate(body["jobs"]):
        time.sleep(conf.get("delay", 0) if n else conf.get("first_delay", 0))
        frames = int(secs * rate)
        with wave.open(os.path.join(out, job["id"] + ".wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
            w.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate)))
                                   for i in range(frames)))
        sys.stdout.write(json.dumps({"id": job["id"], "seconds": secs}) + "\\n")
        sys.stdout.flush()
        log("progress.log", {"id": job["id"], "t": time.time()})
    log("end.log", {"t": time.time()})
''')


def write_sine(path: Path, seconds: float, rate: int = 16000) -> None:
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate)))
                               for i in range(frames)))


class Env:
    def __init__(self, tmp: Path):
        self.repo = tmp / "repo"
        self.fake = tmp / "fake"
        self.breezy = tmp / "breezy"
        self.voice = {"wav": "voice/voice.wav", "text": "在密碼學中,加密是將明文資訊改變為難以讀取的密文內容。"}
        self.role_calls = 0
        self.popen: list[dict] = []
        self.held = False
        self.emits: list[tuple[float, dict]] = []

    def conf(self, **kw):
        (self.fake / "conf.json").write_text(json.dumps(kw), encoding="utf-8")

    def calls(self) -> list[dict]:
        p = self.fake / "calls.log"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def log(self, name) -> list[dict]:
        p = self.fake / name
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def emit(self, ev):
        self.emits.append((time.time(), ev))

    def segment_emits(self):
        return [ev for _, ev in self.emits if ev.get("type") == "segment"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    tmp = tmp_path.resolve()
    e = Env(tmp)
    for d in (e.repo, e.fake, e.breezy, e.repo / "voice"):
        d.mkdir(parents=True, exist_ok=True)
    (e.fake / "worker.py").write_text(FAKE_WORKER, encoding="utf-8")
    write_sine(e.repo / "voice" / "voice.wav", 0.3)
    monkeypatch.setattr(pj, "ROOT", e.repo)
    monkeypatch.setattr(taiwan_ai, "ROOT", e.repo)
    monkeypatch.setattr(hl, "ROOT", e.repo)
    monkeypatch.setattr(hl, "LOCK_PATH", e.repo / "outputs" / ".heavy.lock")
    monkeypatch.setattr(tts, "BREEZY", e.breezy)

    def role_config(role):
        assert role == "voice"
        e.role_calls += 1
        return dict(e.voice)

    monkeypatch.setattr(taiwan_ai, "role_config", role_config)
    monkeypatch.setattr(tts, "_worker_argv",
                        lambda jobs, out: [sys.executable, str(e.fake / "worker.py"), "synth",
                                           "--jobs", str(jobs), "--out", str(out)])
    real_popen = subprocess.Popen

    class RecPopen(real_popen):
        def __init__(self, args, *a, **kw):
            e.popen.append({"args": args, "kw": kw, "held": e.held})
            super().__init__(args, *a, **kw)

    monkeypatch.setattr(subprocess, "Popen", RecPopen)
    real_lock = hl.heavy_lock

    @contextlib.contextmanager
    def rec_lock(*a, **kw):
        with real_lock(*a, **kw):
            e.held = True
            try:
                yield
            finally:
                e.held = False

    monkeypatch.setattr(hl, "heavy_lock", rec_lock)
    return e


def seg(text, **kw):
    return {"id": kw.pop("id", pj.new_id()), "text": text, **kw}


def make_project(texts=("今天天氣真好。", "我們去夜市吧。", "你有試過嗎？")):
    p = pj.Project(name="夜市的味道", segments=[seg(t) for t in texts])
    pj.save(p)
    return pj.load("夜市的味道")


def worker_jobs(env) -> list[list[str]]:
    return [[j["text"] for j in c["jobs"]] for c in env.calls()]


def tmp_dir():
    return pj.subdir("夜市的味道", "tmp")


# ---------- 只合成髒片段與快取 ----------

def test_三句全新_合成三句(env):
    p = make_project()
    out = tts.synth(p, env.emit)
    assert worker_jobs(env) == [["今天天氣真好。", "我們去夜市吧。", "你有試過嗎？"]]
    assert out["synthesized"] == [s["id"] for s in p.segments] and out["cached"] == []
    assert [ev["id"] for ev in env.segment_emits()] == [s["id"] for s in p.segments]
    adir = pj.subdir("夜市的味道", "audio")
    for s in p.segments:
        a = s["audio"]
        assert len(a["key"]) == 64 and a["id"] == a["key"][:32]
        assert a["seconds"] == pytest.approx(0.5, abs=0.01)
        assert (adir / f"{a['id']}.wav").is_file()
    assert tts.dirty_segments(p) == []
    assert list(tmp_dir().iterdir()) == []          # worker 輸入檔與輸出暫存目錄用完即刪
    pj.save(p)                                       # 更新後的片段仍通過 T2 驗證
    assert pj.load("夜市的味道").segments[0]["audio"] == p.segments[0]["audio"]


def test_改第二句_只合成第二句(env):
    p = make_project()
    tts.synth(p, env.emit)
    first = [dict(s["audio"]) for s in p.segments]
    p.segments[1]["text"] = "我們去士林夜市吧。"
    assert [s["id"] for s in tts.dirty_segments(p)] == [p.segments[1]["id"]]
    out = tts.synth(p, env.emit)
    assert worker_jobs(env)[1:] == [["我們去士林夜市吧。"]]
    assert out["synthesized"] == [p.segments[1]["id"]]
    assert p.segments[0]["audio"] == first[0] and p.segments[2]["audio"] == first[2]
    assert p.segments[1]["audio"]["key"] != first[1]["key"]


def test_改標註_該句變髒(env):
    p = make_project()
    tts.synth(p, env.emit)
    p.segments[0]["annotations"] = [{"index": 4, "zhuyin": "ㄓㄣ"}]
    assert [s["id"] for s in tts.dirty_segments(p)] == [p.segments[0]["id"]]
    tts.synth(p, env.emit)
    assert worker_jobs(env)[1:] == [["今天天氣真[:ㄓㄣ]好。"]]


def test_改回原文字_快取命中_零次合成(env):
    p = make_project()
    tts.synth(p, env.emit)
    original = dict(p.segments[1]["audio"])
    p.segments[1]["text"] = "我們去士林夜市吧。"
    tts.synth(p, env.emit)
    assert len(env.calls()) == 2
    p.segments[1]["text"] = "我們去夜市吧。"
    env.emits.clear()
    out = tts.synth(p, env.emit)
    assert len(env.calls()) == 2                     # 零次合成
    assert out == {"synthesized": [], "cached": [p.segments[1]["id"]]}
    assert p.segments[1]["audio"] == original
    assert env.segment_emits() == [{"type": "segment", "id": p.segments[1]["id"],
                                    "seconds": original["seconds"], "cached": True, "audio": original,
                                    "post": p.segments[1]["post"]}]


def test_相同文字的兩句_只合成一次(env):
    p = make_project(("好。", "好。", "不好。"))
    tts.synth(p, env.emit)
    assert worker_jobs(env) == [["好。", "不好。"]]
    assert p.segments[0]["audio"] == p.segments[1]["audio"]


def test_聲線檔改變_全部變髒(env):
    p = make_project()
    tts.synth(p, env.emit)
    write_sine(env.repo / "voice" / "voice.wav", 0.4)
    assert len(tts.dirty_segments(p)) == 3
    out = tts.synth(p, env.emit)
    assert len(out["synthesized"]) == 3 and out["cached"] == []
    assert len(worker_jobs(env)[1]) == 3


def test_只改聲線逐字稿_全部變髒_快取不命中(env):
    p = make_project()
    tts.synth(p, env.emit)
    old = {s["id"]: s["audio"]["key"] for s in p.segments}
    env.voice["text"] = "換了一份逐字稿。"
    assert len(tts.dirty_segments(p)) == 3
    out = tts.synth(p, env.emit)
    assert out["cached"] == [] and len(out["synthesized"]) == 3
    assert env.calls()[1]["prompt_text"] == "換了一份逐字稿。"
    assert all(s["audio"]["key"] != old[s["id"]] for s in p.segments)


def test_快取鍵包含模型名與各組成(env):
    v = tts.load_voice()
    s = seg("今天天氣真好。")
    k = tts.cache_key(s, v)
    assert len(k) == 64
    assert tts.cache_key(s, tts.Voice(v.wav, v.text, v.wav_sha, v.text_sha, "Other/Model")) != k
    assert tts.cache_key(s, tts.Voice(v.wav, v.text, "0" * 64, v.text_sha)) != k
    assert tts.cache_key(seg("今天天氣真好。", annotations=[{"index": 0, "zhuyin": "ㄐㄧㄣ"}]), v) != k


def test_外部提供的音檔_不算髒(env):
    p = make_project(("今天天氣真好。",))
    p.segments[0]["audio"] = {"id": "ab" * 8, "seconds": 1.0}
    assert tts.dirty_segments(p) == []


def test_音檔被刪掉_該句變髒(env):
    p = make_project()
    tts.synth(p, env.emit)
    (pj.subdir("夜市的味道", "audio") / f"{p.segments[2]['audio']['id']}.wav").unlink()
    assert [s["id"] for s in tts.dirty_segments(p)] == [p.segments[2]["id"]]


def test_role_config_一次synth只呼叫一次(env):
    p = make_project()
    env.role_calls = 0
    tts.synth(p, env.emit)
    assert env.role_calls == 1
    env.role_calls = 0
    tts.synth(p, env.emit)                           # 全部命中也一樣只讀一次
    assert env.role_calls == 1


# ---------- worker 啟動方式與逐句 emit ----------

def test_worker_以argv_list_BREEZY_ENV_cwd_在heavy_lock內啟動(env):
    p = make_project()
    tts.synth(p, env.emit)
    workers = [c for c in env.popen if str(env.fake / "worker.py") in c["args"]]
    assert len(workers) == 1
    call = workers[0]
    assert isinstance(call["args"], list)
    assert call["kw"]["env"] == taiwan_ai.BREEZY_ENV
    assert call["kw"]["env"] is taiwan_ai.BREEZY_ENV
    assert Path(call["kw"]["cwd"]) == env.breezy
    assert not call["kw"].get("shell")
    assert call["held"] is True                      # heavy_lock 內
    worker_env = env.calls()[0]["env"]
    assert worker_env["HF_HUB_OFFLINE"] == "1" and worker_env["TRANSFORMERS_OFFLINE"] == "1"
    assert Path(env.calls()[0]["cwd"]).resolve() == env.breezy
    assert env.calls()[0]["model"] == tts.MODEL
    # 使用者文字只在 jobs 檔裡,不在命令列
    assert not any(s["text"] in a for s in p.segments for a in call["args"])


def test_正式worker_argv():
    argv = tts._worker_argv(Path("/x/jobs.json"), Path("/x/out"))
    assert argv == [str(taiwan_ai.BREEZY / ".venv" / "bin" / "python"), str(tts.WORKER), "synth",
                    "--jobs", "/x/jobs.json", "--out", "/x/out"]
    assert tts.WORKER.name == "breezy_worker.py"


def test_逐句emit_不等worker結束(env):
    env.conf(delay=1.0)
    p = make_project()
    tts.synth(p, env.emit)
    progress = env.log("progress.log")
    end = env.log("end.log")[0]["t"]
    seg_emits = [(t, ev) for t, ev in env.emits if ev.get("type") == "segment"]
    assert len(progress) == 3 and len(seg_emits) == 3
    first_emit = seg_emits[0][0]
    assert first_emit - progress[0]["t"] < 0.5
    assert first_emit < end - 1.5                    # worker 還在合成後兩句時就已送出


def test_segment事件帶音檔id(env):
    """DIR-5 統整 M-1:segment 事件帶 audio(id、seconds、key),伺服器據此讓合成中途即可試聽。"""
    p = make_project()
    tts.synth(p, env.emit)
    evs = env.segment_emits()
    assert len(evs) == 3
    for ev, s in zip(evs, p.segments):
        assert ev["audio"]["id"] == s["audio"]["id"] and ev["audio"]["key"] == s["audio"]["key"]
        assert ev["audio"]["seconds"] == ev["seconds"]
        assert (pj.subdir("夜市的味道", "audio") / f"{ev['audio']['id']}.wav").is_file()


def test_第一句另給模型載入額度(env, monkeypatch):
    """CHG 修訂 4(RS-7):第一個進度行可等 MODEL_LOAD_TIMEOUT + SENTENCE_TIMEOUT 秒。"""
    monkeypatch.setattr(tts, "SENTENCE_TIMEOUT", 1.0)
    monkeypatch.setattr(tts, "MODEL_LOAD_TIMEOUT", 2.0)
    env.conf(first_delay=1.8)                         # 超過每句 1 秒,但在 1 + 2 秒內
    p = make_project(texts=("今天天氣真好。",))
    out = tts.synth(p, env.emit)
    assert out["synthesized"] == [p.segments[0]["id"]]


def test_第一句超過模型載入額度_逾時(env, monkeypatch):
    monkeypatch.setattr(tts, "SENTENCE_TIMEOUT", 1.0)
    monkeypatch.setattr(tts, "MODEL_LOAD_TIMEOUT", 1.0)
    env.conf(first_delay=3.0)
    p = make_project(texts=("今天天氣真好。",))
    with pytest.raises(tts.SynthError, match="逾時"):
        tts.synth(p, env.emit)
    assert env.segment_emits() == []


def test_第二句起_只給每句額度(env, monkeypatch):
    monkeypatch.setattr(tts, "SENTENCE_TIMEOUT", 1.0)
    monkeypatch.setattr(tts, "MODEL_LOAD_TIMEOUT", 2.0)
    env.conf(delay=2.0)                               # 第一句立即完成,第二句超過每句 1 秒
    p = make_project(texts=("今天天氣真好。", "我們去夜市吧。"))
    with pytest.raises(tts.SynthError, match="逾時"):
        tts.synth(p, env.emit)
    assert [ev["id"] for ev in env.segment_emits()] == [p.segments[0]["id"]]


def test_worker失敗_訊息只取worker的繁中訊息且遮蔽路徑(env):
    env.conf(fail="✗ 第 1 句(abcd1234)合成失敗:/Users/someone/private/x.wav 損毀", code=4)
    p = make_project()
    with pytest.raises(tts.SynthError) as ei:
        tts.synth(p, env.emit)
    msg = str(ei.value)
    assert msg.startswith("✗ 第 1 句")
    assert "/Users" not in msg and "Traceback" not in msg and "雜訊" not in msg
    assert "<路徑>" in msg
    assert env.segment_emits() == []
    assert list(tmp_dir().iterdir()) == []           # 失敗也清掉 worker 輸入檔
    assert all(s["audio"] is None for s in p.segments)


def test_worker失敗_沒有worker訊息時依結束碼給固定訊息(env):
    env.conf(fail="Some English error at " + str(env.repo), code=3)
    p = make_project()
    with pytest.raises(tts.SynthError) as ei:
        tts.synth(p, env.emit)
    assert str(ei.value) == tts.EXIT_HINTS[3]


def test_mask_遮蔽repo與家目錄():
    out = tts._mask(f"✗ 錯誤 {tts.ROOT}/tools/x 與 {Path.home()}/a/b 與 /opt/secret/file")
    assert str(tts.ROOT) not in out and str(Path.home()) not in out and "/opt/secret" not in out


# ---------- 語速 ----------

def test_改某句語速_worker零次_只對該句跑一次atempo_長度約為原長除以語速(env, monkeypatch):
    env.conf(seconds=2.0)
    p = make_project()
    tts.synth(p, env.emit)
    atempo: list[list[str]] = []
    real_run = subprocess.run

    def rec_run(cmd, *a, **kw):
        if isinstance(cmd, list) and any(str(x).startswith("atempo=") for x in cmd):
            atempo.append(cmd)
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "run", rec_run)
    for s in p.segments:                              # 先以預設語速 1.15 備好三句
        tts.apply_speed(s, p)
    assert len(atempo) == 3
    atempo.clear()
    calls_before = len(env.calls())

    p.segments[1]["speed"] = 1.3
    assert tts.dirty_segments(p) == []
    tts.synth(p, env.emit)
    paths = [tts.apply_speed(s, p) for s in p.segments]
    assert len(env.calls()) == calls_before           # worker 零次呼叫
    assert len(atempo) == 1                           # 只對該句跑一次
    cmd = atempo[0]
    assert isinstance(cmd, list) and "atempo=1.30" in cmd
    # DIR-5 修正 4:來源經 stdin(pipe:0)、輸出寫 /dev/fd/<n>,argv 不含專案路徑
    assert "pipe:0" in cmd and cmd[-1].startswith("/dev/fd/")
    assert not any(str(pj.subdir("夜市的味道", "audio")) in str(x) for x in cmd)
    sped = media.wav_seconds(paths[1])
    assert sped == pytest.approx(2.0 / 1.3, rel=0.03)
    assert media.wav_seconds(paths[0]) == pytest.approx(2.0 / 1.15, rel=0.03)
    # 再跑一次全部命中
    for s in p.segments:
        tts.apply_speed(s, p)
    assert len(atempo) == 1
    assert [x.name for x in pj.subdir("夜市的味道", "audio").iterdir() if x.name.startswith(".")] == []


def test_語速1_直接用原音檔(env, monkeypatch):
    p = make_project(("今天天氣真好。",))
    tts.synth(p, env.emit)
    p.segments[0]["speed"] = 1.0
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("不應呼叫 ffmpeg"))
    path = tts.apply_speed(p.segments[0], p)
    assert path.name == f"{p.segments[0]['post']['id']}.wav"     # CHG-20261029-01:來源為後製檔


def test_未合成_不能變速(env):
    p = make_project(("今天天氣真好。",))
    with pytest.raises(tts.SynthError):
        tts.apply_speed(p.segments[0], p)


def test_audio目錄是符號連結_拒絕寫入(env, tmp_path):
    p = make_project(("今天天氣真好。",))
    outside = tmp_path / "outside"
    outside.mkdir()
    pdir = pj.subdir("夜市的味道", "tmp", create=True).parent
    (pdir / "audio").symlink_to(outside, target_is_directory=True)
    with pytest.raises(pj.UnsafePath):
        tts.synth(p, env.emit)
    assert list(outside.iterdir()) == [] and env.calls() == []


def test_subdir_只接受三種子目錄(env):
    make_project(("今天天氣真好。",))
    with pytest.raises(ValueError):
        pj.subdir("夜市的味道", "history")
    with pytest.raises(FileNotFoundError):
        pj.subdir("夜市的味道", "assets")
    with pytest.raises(pj.ProjectError):
        pj.subdir("不存在的專案", "audio")


# ---------- DIR-5 修正 4:檢查後、操作前換成符號連結 ----------

def test_變速來源在檢查後被換成連結_拒絕且不讀外部(env, tmp_path, monkeypatch):
    p = make_project(("今天天氣真好。",))
    tts.synth(p, env.emit)
    p.segments[0]["speed"] = 1.3
    outside = tmp_path / "outside.wav"
    write_sine(outside, 0.5)
    src = pj.subdir("夜市的味道", "audio") / f"{p.segments[0]['post']['id']}.wav"   # 變速來源 = 後製檔
    real = tts._regular_in

    def swapping(dirfd, name):
        ok = real(dirfd, name)
        if name == src.name and ok and not src.is_symlink():
            src.unlink()
            src.symlink_to(outside)
        return ok
    monkeypatch.setattr(tts, "_regular_in", swapping)
    with pytest.raises(tts.SynthError):
        tts.apply_speed(p.segments[0], p)
    assert [x.name for x in src.parent.iterdir() if "-s130" in x.name] == []


def test_合成產物移入時audio目錄已被換成連結_不寫到外部(env, tmp_path, monkeypatch):
    p = make_project(("今天天氣真好。",))
    outside = tmp_path / "outside"
    outside.mkdir()
    real = pj.open_subdir

    def swapping(name, sub, **kw):
        fd = real(name, sub, **kw)
        adir = pj._top() / "projects" / name / "audio"
        if sub == "audio" and kw.get("create") and not adir.is_symlink():
            adir.rename(adir.with_name("audio-moved"))
            adir.symlink_to(outside, target_is_directory=True)
        return fd
    monkeypatch.setattr(pj, "open_subdir", swapping)
    tts.synth(p, env.emit)
    assert list(outside.iterdir()) == []
    moved = pj._top() / "projects" / "夜市的味道" / "audio-moved"
    assert sorted(x.name for x in moved.iterdir()) == sorted(
        {f"{p.segments[0]['audio']['id']}.wav", f"{p.segments[0]['post']['id']}.wav"})   # 合成檔 + 裁切檔


# ---------- DIR-5 修正 5:tmp/ 自受信根逐層開啟 ----------

def test_worker暫存目錄開啟後被換成連結_不從外部讀取也不寫外部(env, tmp_path, monkeypatch):
    p = make_project(("今天天氣真好。",))
    pj.subdir("夜市的味道", "tmp", create=True)
    outside = tmp_path / "outside-tmp"
    outside.mkdir()
    real = pj.open_subdir

    def swapping(name, sub, **kw):
        fd = real(name, sub, **kw)
        d = pj._top() / "projects" / name / "tmp"
        if sub == "tmp" and not d.is_symlink():
            d.rename(d.with_name("tmp-moved"))
            d.symlink_to(outside, target_is_directory=True)
        return fd
    monkeypatch.setattr(pj, "open_subdir", swapping)
    with pytest.raises(tts.SynthError):   # worker 依路徑寫入的輸出目錄不存在於連結目標,合成失敗
        tts.synth(p, env.emit)
    assert list(outside.iterdir()) == []
    assert list((pj._top() / "projects" / "夜市的味道" / "tmp-moved").iterdir()) == []   # 輸出目錄以 fd 清除
    assert p.segments[0]["audio"] is None


def test_run_worker的tmp不以可替換路徑當起點():
    import inspect
    src = inspect.getsource(tts._run_proc)
    assert "pj.open_subdir(project_name, \"tmp\"" in src and "tmp_dir.parent" not in src


# ---------- CHG-20261029-01 T3:片段靜音裁切與後製管線 ----------

FAKE_SHAPE = textwrap.dedent('''
    # -*- coding: utf-8 -*-
    # 替身 shape worker:把輸入照抄成輸出,連音 p% 時去掉開頭 p% × 0.2 秒(模擬句子變短);呼叫記到 shape.log
    import json, os, sys, wave
    HERE = os.path.dirname(os.path.abspath(__file__))
    args = sys.argv[1:]
    assert args[0] == "shape", args
    jobs_path, out = args[args.index("--jobs") + 1], args[args.index("--out") + 1]
    with open(jobs_path, encoding="utf-8") as f:
        body = json.load(f)
    with open(os.path.join(HERE, "shape.log"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"jobs": body["jobs"], "inputs": sorted(os.listdir(out))}, ensure_ascii=False) + "\\n")
    for job in body["jobs"]:
        with wave.open(os.path.join(out, job["input"]), "rb") as r:
            params, frames = r.getparams(), r.readframes(r.getnframes())
        cut = int(job["connect"] / 100 * 0.2 * params.framerate) * params.sampwidth
        with wave.open(os.path.join(out, job["id"] + ".wav"), "wb") as w:
            w.setparams(params)
            w.writeframes(frames[cut:])
        # 修訂 1 T11:回傳 pitch_points(有語調時照抄使用者點;shape_pitch.json 存在時改回傳其內容,
        # 內容為 {"missing": true} 時不帶這個欄位)
        row = {"id": job["id"], "seconds": 0,
               "pitch_points": job["pitch"]["points"] if job["pitch"] else None}
        ctl = os.path.join(HERE, "shape_pitch.json")
        if os.path.exists(ctl):
            with open(ctl, encoding="utf-8") as f:
                over = json.load(f)
            if over == {"missing": True}:
                row.pop("pitch_points")
            else:
                row["pitch_points"] = over
        sys.stdout.write(json.dumps(row) + "\\n")
        sys.stdout.flush()
''')


@pytest.fixture
def shaped(env, monkeypatch):
    (env.fake / "shape.py").write_text(FAKE_SHAPE, encoding="utf-8")
    monkeypatch.setattr(tts, "_shape_argv", lambda jobs, out: [sys.executable, str(env.fake / "shape.py"),
                                                               "shape", "--jobs", str(jobs), "--out", str(out)])
    return env


def shape_calls(env) -> list[dict]:
    return env.log("shape.log")


def write_padded(path: Path, head=0.6, body=1.0, tail=0.25, rate=22050):
    """前 head 秒、後 tail 秒為 −60 dBFS 噪聲,中間為 −6 dBFS 的 440 Hz 正弦波。"""
    import random
    rnd = random.Random(3)
    quiet = int(32768 * 10 ** (-60 / 20))
    frames = []
    for i in range(int(head * rate)):
        frames.append(rnd.randint(-quiet, quiet))
    for i in range(int(body * rate)):
        frames.append(int(16000 * math.sin(2 * math.pi * 440 * i / rate)))
    for i in range(int(tail * rate)):
        frames.append(rnd.randint(-quiet, quiet))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(frames)}h", *frames))


def quiet_edges(path: Path, db=-40.0):
    with wave.open(str(path), "rb") as r:
        rate, n = r.getframerate(), r.getnframes()
        pcm = struct.unpack(f"<{n}h", r.readframes(n))
    thr = 32768 * 10 ** (db / 20)
    loud = [i for i, v in enumerate(pcm) if abs(v) >= thr]
    return loud[0] / rate, (n - 1 - loud[-1]) / rate, n / rate


def test_trim_前後低音量各剩002到005秒(tmp_path):
    src = tmp_path / "src.wav"
    write_padded(src)
    seconds, head, tail = tts.trim(src, tmp_path / "out.wav")
    a, b, total = quiet_edges(tmp_path / "out.wav")
    assert 0.02 <= a <= 0.05 and 0.02 <= b <= 0.05
    assert seconds == pytest.approx(total, abs=0.002)
    assert seconds == pytest.approx(1.0 + 0.06, abs=0.03)
    assert head == pytest.approx(0.6, abs=0.01) and tail == pytest.approx(0.25, abs=0.01)


def test_trim_濾鏡只含程式常數():
    f = tts._trim_filter()
    assert "start_threshold=-40dB" in f and "start_silence=0.030" in f and "detection=peak" in f
    assert f.count("areverse") == 2 and "afade=t=in:d=0.010" in f


def padded_project(env, n=2, **seg_kw):
    """已有合成音檔(前後低音量)的專案:audio 帶 key,檔案放在 audio/。"""
    p = pj.Project(name="夜市的味道", segments=[seg(t, **seg_kw) for t in ("今天天氣真好。", "我們去夜市吧。")[:n]])
    pj.save(p)
    adir = pj.subdir("夜市的味道", "audio", create=True)
    for i, s in enumerate(p.segments):
        key = f"{i + 1:x}" * 64
        write_padded(adir / f"{key[:32]}.wav")
        s["audio"] = {"id": key[:32], "seconds": round(media.wav_seconds(adir / f"{key[:32]}.wav"), 3), "key": key}
    pj.save(p)
    return pj.load("夜市的味道")


def test_預設參數只裁切_不啟動shape(shaped):
    p = padded_project(shaped)
    out = tts.post_process(p, None, shaped.emit)
    assert out == {"processed": [s["id"] for s in p.segments], "cached": []}
    assert shape_calls(shaped) == [] and shaped.calls() == []
    adir = pj.subdir("夜市的味道", "audio")
    for s in p.segments:
        assert pj.is_post_fresh(s)
        assert s["post"]["seconds"] == pytest.approx(1.06, abs=0.03)
        assert s["post"]["trim"] == pytest.approx(s["audio"]["seconds"] - s["post"]["seconds"], abs=0.002)
        a, b, _ = quiet_edges(adir / f"{s['post']['id']}.wav")
        assert a <= 0.05 and b <= 0.05
    assert tts.stale_post(p) == []
    pj.save(p)                                        # post 欄位通過專案驗證
    assert [ev["type"] for _, ev in shaped.emits] == ["post", "post"]


def test_改連音只啟動shape一次_synth零次_同參數第二次全部命中(shaped):
    p = padded_project(shaped)
    tts.post_process(p, None, shaped.emit)
    trimmed = p.segments[0]["post"]["seconds"]
    p.segments[0]["connect"] = 50
    assert [s["id"] for s in tts.stale_post(p)] == [p.segments[0]["id"]]
    out = tts.post_process(p, [p.segments[0]["id"]], shaped.emit)
    assert out == {"processed": [p.segments[0]["id"]], "cached": []}
    calls = shape_calls(shaped)
    assert len(calls) == 1 and shaped.calls() == []
    [job] = calls[0]["jobs"]
    assert job["connect"] == 50 and job["clarity"] == 100 and job["chars"] == 6     # 「今天天氣真好」6 個漢字
    assert job["input"] == f"{tts._trim_key(p.segments[0])[:32]}.wav" and calls[0]["inputs"] == [job["input"]]
    assert p.segments[0]["post"]["seconds"] == pytest.approx(trimmed - 0.1, abs=0.002)
    assert p.segments[0]["post"]["id"] == pj.post_key(p.segments[0])[:32]
    shaped.emits.clear()
    again = tts.post_process(p, [s["id"] for s in p.segments], shaped.emit)
    assert again == {"processed": [], "cached": [s["id"] for s in p.segments]}
    assert len(shape_calls(shaped)) == 1
    assert list(tmp_dir().iterdir()) == []            # 輸入複本與輸出暫存目錄用完即刪


def test_shape工作的字詞清晰度索引換成念出的字(shaped):
    s = seg("大家好，歡迎收聽夜市的味道。", word_clarity=[{"start": 8, "end": 10, "value": 40}])
    s["audio"] = {"id": "a" * 32, "seconds": 1.0, "key": "a" * 64}
    job = tts.shape_job(pj.validate(pj.Project(name="夜市的味道", segments=[s]).to_dict())["segments"][0],
                        "b" * 32, "c" * 32)
    assert job["chars"] == 12 and job["word_clarity"] == [{"start": 7, "end": 9, "value": 40}]


def test_shape失敗訊息為繁中且不含路徑(shaped, monkeypatch):
    p = padded_project(shaped, n=1)
    (shaped.fake / "shape.py").write_text(
        "import sys\nprint('✗ 第 1 句後製失敗:/Users/someone/x.wav', file=sys.stderr)\nsys.exit(4)\n",
        encoding="utf-8")
    p.segments[0]["clarity"] = 40
    with pytest.raises(tts.SynthError) as e:
        tts.post_process(p, None, shaped.emit)
    assert "後製失敗" in str(e.value) and "/Users/" not in str(e.value)
    assert not pj.is_post_fresh(p.segments[0])


def test_自錄音檔不裁切不後製(shaped):
    p = padded_project(shaped, n=1)
    adir = pj.subdir("夜市的味道", "audio")
    p.segments[0]["audio"] = {"id": "e" * 16, "seconds": p.segments[0]["audio"]["seconds"]}
    write_padded(adir / f"{'e' * 16}.wav")
    before = sorted(x.name for x in adir.iterdir())
    p.segments[0]["connect"] = 80
    assert tts.post_process(p, None, shaped.emit) == {"processed": [], "cached": []}
    assert tts.post_process(p, [p.segments[0]["id"]], shaped.emit) == {"processed": [], "cached": []}
    assert sorted(x.name for x in adir.iterdir()) == before and shape_calls(shaped) == []
    assert tts.apply_speed({**p.segments[0], "speed": 1.0}, p).name == f"{'e' * 16}.wav"


def test_synth每句完成即裁切_post指向裁切檔(env):
    p = make_project(("今天天氣真好。",))
    tts.synth(p, env.emit)
    s = p.segments[0]
    assert pj.is_post_fresh(s) and s["post"]["id"] == tts._trim_key(s)[:32]
    [ev] = env.segment_emits()
    assert ev["post"] == s["post"]


def test_synth時非預設參數的post保持過期(env):
    p = make_project(("今天天氣真好。",))
    p.segments[0]["clarity"] = 30
    tts.synth(p, env.emit)
    assert p.segments[0]["post"] is None and tts.stale_post(p) == [p.segments[0]]


def test_改語速只重跑atempo且來源為後製檔(shaped, monkeypatch):
    p = padded_project(shaped, n=1)
    p.segments[0]["connect"] = 40
    tts.post_process(p, None, shaped.emit)
    shapes = len(shape_calls(shaped))
    atempo: list = []
    real_run = subprocess.run

    def rec_run(cmd, *a, **kw):
        if isinstance(cmd, list) and any(str(x).startswith("atempo=") for x in cmd):
            atempo.append(cmd)
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "run", rec_run)
    p.segments[0]["speed"] = 1.3
    path = tts.apply_speed(p.segments[0], p)
    assert len(atempo) == 1 and len(shape_calls(shaped)) == shapes and shaped.calls() == []
    assert path.name == f"{p.segments[0]['post']['id']}-s130.wav"
    assert media.wav_seconds(path) == pytest.approx(p.segments[0]["post"]["seconds"] / 1.3, rel=0.03)


def test_ensure_post補做所有過期片段(shaped):
    p = padded_project(shaped)
    p.segments[1]["pitch"] = {"points": [{"pos": 0.0, "semi": 0.0}, {"pos": 1.0, "semi": 2.0}], "roundness": None}
    out = tts.ensure_post(p, shaped.emit)
    assert sorted(out["processed"]) == sorted(s["id"] for s in p.segments)
    assert tts.stale_post(p) == [] and len(shape_calls(shaped)) == 1
    assert shape_calls(shaped)[0]["jobs"][0]["pitch"] == {"points": [[0.0, 0.0], [1.0, 2.0]], "roundness": 50}


def test_兩片段加15秒停頓匯出後silencedetect量得15秒(shaped, monkeypatch):
    import re as _re

    import imageio_ffmpeg
    import timeline
    p = padded_project(shaped)
    p.defaults["speed"] = 1.0
    p.segments[0]["pause_after"] = 1.5
    tts.ensure_post(p, shaped.emit)
    pj.save(p)
    out = Path(taiwan_ai.ROOT) / "outputs"
    monkeypatch.setattr(taiwan_ai, "OUT", out)
    mp4, _ = media.render_timeline(p, timeline.layout(p))
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-i", str(mp4),
                        "-af", "silencedetect=noise=-40dB:d=0.3", "-f", "null", "-"], capture_output=True, timeout=120)
    found = [float(x) for x in _re.findall(r"silence_duration: ([\d.]+)", r.stderr.decode("utf-8", "replace"))]
    assert len(found) == 1 and found[0] == pytest.approx(1.5, abs=0.1), found


def test_後製檔不在_視為過期_匯出前重做且長度與layout一致(shaped, monkeypatch):
    """(DIR-5 修正 M-3)post 資料仍有效但後製檔已被刪:stale_post 列為過期;依匯出流程補做後
    後製檔重新產生、變速來源為後製檔,匯出長度與 timeline.layout() 一致(句尾不被截掉)。"""
    import re as _re

    import imageio_ffmpeg
    import timeline
    p = padded_project(shaped)
    p.defaults["speed"] = 1.0
    p.segments[0]["connect"] = 50
    tts.ensure_post(p, shaped.emit)
    pj.save(p)
    p = pj.load("夜市的味道")
    s0 = p.segments[0]
    adir = pj.subdir("夜市的味道", "audio")
    (adir / f"{s0['post']['id']}.wav").unlink()
    assert pj.is_post_fresh(s0)                       # 只比對資料仍相符
    assert [s["id"] for s in tts.stale_post(p)] == [s0["id"]]
    stale = [s["id"] for s in tts.stale_post(p)]      # 同 studio_server 匯出 work():stale_post → post_fn
    out = tts.post_process(p, stale, shaped.emit)
    assert out["processed"] == [s0["id"]]
    assert (adir / f"{s0['post']['id']}.wav").is_file() and tts.stale_post(p) == []
    assert tts.ensure_post(p, shaped.emit) == {"processed": [], "cached": []}
    assert tts.apply_speed(s0, p).name == f"{s0['post']['id']}.wav"
    lay = timeline.layout(p)
    out_dir = Path(taiwan_ai.ROOT) / "outputs"
    monkeypatch.setattr(taiwan_ai, "OUT", out_dir)
    mp4, _ = media.render_timeline(p, lay)
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-hide_banner", "-i", str(mp4),
                        "-f", "null", "-"], capture_output=True, timeout=120)
    h, m, sec = _re.findall(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr.decode("utf-8", "replace"))[0]
    assert int(h) * 3600 + int(m) * 60 + float(sec) == pytest.approx(lay["duration"], abs=0.1)
    # 匯出音源(後製檔)與 layout 的片段長度相同,句尾不會被 layout 長度截掉
    src = adir / f"{s0['post']['id']}.wav"
    assert media.wav_seconds(src) == pytest.approx(lay["segments"][0]["end"] - lay["segments"][0]["start"], abs=0.002)


def test_後製檔不在_自錄音檔與未合成片段仍不列過期(shaped):
    p = padded_project(shaped, n=1)
    p.segments[0]["audio"] = {"id": "e" * 16, "seconds": 1.0}
    assert tts.stale_post(p) == []
    p.segments[0]["audio"] = None
    assert tts.stale_post(p) == []


# ---------- CHG-20261029-01 修訂 1 T11:shape 回傳的 pitch_points 寫進 segment.post(R1-10) ----------

def _pitch(*pts, roundness=50):
    return {"points": [{"pos": a, "semi": b} for a, b in pts], "roundness": roundness}


def test_shape回傳的pitch_points寫進post_存檔往返_快取命中保留(shaped):
    p = padded_project(shaped)
    p.segments[0]["pitch"] = _pitch((0.0, 0.0), (0.5, 3.0), (1.0, 0.0))
    p.segments[1]["connect"] = 50
    tts.post_process(p, None, shaped.emit)
    assert p.segments[0]["post"]["pitch_points"] == [[0.0, 0.0], [0.5, 3.0], [1.0, 0.0]]
    assert "pitch_points" not in p.segments[1]["post"]                 # 沒有語調:不帶 pitch_points
    pj.save(p)
    q = pj.load("夜市的味道")
    assert q.segments[0]["post"]["pitch_points"] == [[0.0, 0.0], [0.5, 3.0], [1.0, 0.0]]
    assert pj.is_post_fresh(q.segments[0])
    again = tts.post_process(q, [s["id"] for s in q.segments], shaped.emit)
    assert again["cached"] == [s["id"] for s in q.segments] and len(shape_calls(shaped)) == 1
    assert q.segments[0]["post"]["pitch_points"] == [[0.0, 0.0], [0.5, 3.0], [1.0, 0.0]]


@pytest.mark.parametrize("bad", [
    [[i / 30, 0] for i in range(31)], [[0, 0], [0.5, 1], [0.5, 2]], [[0, 0], [1, 7.3]], [[0, "1"], [1, 0]],
    None, "x", {"missing": True},
])
def test_pitch_points格式不合_該句後製失敗_沿用上一份後製檔(shaped, bad):
    p = padded_project(shaped, n=1)
    p.segments[0]["pitch"] = _pitch((0.0, 0.0), (1.0, 2.0))
    tts.post_process(p, None, shaped.emit)
    prev = dict(p.segments[0]["post"])
    adir = pj.subdir("夜市的味道", "audio")
    before = sorted(x.name for x in adir.iterdir())
    p.segments[0]["pitch"] = _pitch((0.0, 0.0), (1.0, -2.0))
    new_id = pj.post_key(p.segments[0])[:32]
    (shaped.fake / "shape_pitch.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(tts.SynthError) as e:
        tts.post_process(p, None, shaped.emit)
    assert "後製失敗" in str(e.value) and "/Users/" not in str(e.value)
    assert p.segments[0]["post"] == prev and not pj.is_post_fresh(p.segments[0])
    assert sorted(x.name for x in adir.iterdir()) == before and not (adir / f"{new_id}.wav").exists()
    assert (adir / f"{prev['id']}.wav").exists()
    assert list(tmp_dir().iterdir()) == []


def test_沒有語調的句子回傳非null的pitch_points也算失敗(shaped):
    p = padded_project(shaped, n=1)
    p.segments[0]["connect"] = 40
    (shaped.fake / "shape_pitch.json").write_text(json.dumps([[0, 0], [1, 1]]), encoding="utf-8")
    with pytest.raises(tts.SynthError):
        tts.post_process(p, None, shaped.emit)
    assert p.segments[0]["post"] is None or not pj.is_post_fresh(p.segments[0])


# ---------- CHG-20261029-01 修訂 1 T12:合音送顯示字(R1-4、R1-5) ----------

def _fz(index, zhuyin, length, display=None):
    f = {"index": index, "length": length, "zhuyin": zhuyin, "source": "lexicon"}
    return f if display is None else {**f, "display": display}


def test_合音帶顯示字_worker收到顯示字且不加注音(env):
    p = make_project(("是這樣啊，我也不知道。",))
    p.segments[0]["fusions"] = [_fz(1, "ㄐㄧㄤˋ", 2, "醬"), _fz(7, "ㄅㄨˋ ㄗㄠˋ", 3, "不造")]
    tts.synth(p, env.emit)
    assert worker_jobs(env) == [["是醬啊，我也不造。"]]
    pj.save(p)
    assert pj.load("夜市的味道").segments[0]["fusions"][1]["display"] == "不造"


def test_既有合音補上顯示字_該句變髒_重新合成(env):
    p = make_project(("是這樣啊，我也不知道。", "今天天氣真好。"))
    p.segments[0]["fusions"] = [_fz(1, "ㄐㄧㄤˋ", 2), _fz(7, "ㄅㄨˋ ㄗㄠˋ", 3)]
    tts.synth(p, env.emit)
    assert worker_jobs(env) == [["是這[:ㄐㄧㄤˋ]啊，我也不[:ㄅㄨˋ]知[:ㄗㄠˋ]。", "今天天氣真好。"]]
    assert tts.dirty_segments(p) == []
    old = dict(p.segments[0]["audio"])
    p.segments[0]["fusions"] = [_fz(1, "ㄐㄧㄤˋ", 2, "醬"), _fz(7, "ㄅㄨˋ ㄗㄠˋ", 3, "不造")]
    assert [s["id"] for s in tts.dirty_segments(p)] == [p.segments[0]["id"]]
    tts.synth(p, env.emit)
    assert worker_jobs(env)[1:] == [["是醬啊，我也不造。"]]
    assert p.segments[0]["audio"]["key"] != old["key"]


def test_顯示字合音的shape工作以送模型的字換算(shaped):
    p = padded_project(shaped, n=1)
    s = p.segments[0]
    s["text"] = "我也不知道。"
    # 注音只有 1 個音節(注音路線會念 3 字)、顯示字 2 字:換算必須依顯示字
    s["fusions"] = [_fz(2, "ㄗㄠˋ", 3, "不造")]
    s["word_clarity"] = [{"start": 2, "end": 5, "value": 30}]
    tts.post_process(p, [s["id"]], shaped.emit)
    [job] = shape_calls(shaped)[0]["jobs"]
    assert job["chars"] == 4 and job["word_clarity"] == [{"start": 2, "end": 4, "value": 30}]
    s["fusions"] = [_fz(2, "ㄗㄠˋ", 3)]                         # 沒有顯示字:沿用注音路線(念「我也不」)
    assert tts.shape_job(s, "c" * 32, "d" * 32)["chars"] == 3
