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
                                    "seconds": original["seconds"], "cached": True, "audio": original}]


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
    assert path.name == f"{p.segments[0]['audio']['id']}.wav"


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
    src = pj.subdir("夜市的味道", "audio") / f"{p.segments[0]['audio']['id']}.wav"
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
    assert [x.name for x in moved.iterdir()] == [f"{p.segments[0]['audio']['id']}.wav"]


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
    src = inspect.getsource(tts._run_worker)
    assert "pj.open_subdir(project_name, \"tmp\"" in src and "tmp_dir.parent" not in src
