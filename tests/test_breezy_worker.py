"""breezy_worker.py 的測試(CHG-20261022-04 T1)。

以假的 BreezyVoice 目錄(假的 `single_inference`、`g2pw`、`torchaudio` 模組)當 worker 的 cwd,
以子程序實際執行 worker,環境一律用 `taiwan_ai.BREEZY_ENV`(修訂 3,R3-3);不載入真模型、不連網。
假模組把每次呼叫記到 cwd 的 `calls.log`(一行一個 JSON),供測試計數。
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import taiwan_ai as tai

WORKER = Path(__file__).resolve().parents[1] / "breezy_worker.py"
PROMPT_TEXT = "在密碼學中，加密是將明文資訊改變為難以讀取的密文內容。"

# 假的 single_inference:模型建構子呼叫 snapshot_download(讀到 HF_HUB_OFFLINE=1 且快取不存在時丟例外,
# 否則記一次「網路下載」);單句推論寫一個 0.5 秒的 16 位元 PCM WAV,並故意印字到 stdout(worker 必須擋掉)。
FAKE_SI = textwrap.dedent('''
    import json, os, wave
    print("假 single_inference 匯入時印到 stdout")

    def _log(**kw):
        with open("calls.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(kw, ensure_ascii=False) + "\\n")

    class CacheMissing(Exception):
        pass

    def snapshot_download(repo_id):
        if os.environ.get("HF_HUB_OFFLINE") == "1":
            if os.path.exists("cache_ok"):
                return "fake_model_dir"
            raise CacheMissing("快取不存在:/Users/someone/.cache/huggingface/hub/" + repo_id)
        _log(call="download", repo=repo_id)
        return "fake_model_dir"

    class CustomCosyVoice:
        def __init__(self, model_dir):
            _log(call="ctor", model=model_dir)
            if not os.path.exists(model_dir):
                snapshot_download(model_dir)

    def transcribe_audio(path):
        _log(call="transcribe")
        return "轉寫"

    def single_inference(speaker_prompt_audio_path, content_to_synthesize, output_path, cosyvoice,
                         bopomofo_converter, speaker_prompt_text_transcription=None):
        if not speaker_prompt_text_transcription:
            transcribe_audio(speaker_prompt_audio_path)
        _log(call="infer", text=content_to_synthesize, out=os.path.basename(output_path),
             num_workers=bopomofo_converter.num_workers, prompt_text=speaker_prompt_text_transcription)
        print("Synthesizing:", content_to_synthesize)
        if os.path.exists("fail_infer"):
            raise RuntimeError("假推論失敗")
        with wave.open(output_path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
            w.writeframes(b"\\x00\\x00" * 11025)
''')

# 假的 g2pw:建構子與真的一樣把 num_workers=0 當成假值(改用設定檔的 2);呼叫時回固定注音(數字聲調)
FAKE_G2PW = textwrap.dedent('''
    import json

    def _log(**kw):
        with open("calls.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(kw, ensure_ascii=False) + "\\n")

    TABLE = {"今": "ㄐㄧㄣ1", "天": "ㄊㄧㄢ1", "氣": "ㄑㄧ4", "真": "ㄓㄣ1", "好": "ㄏㄠ3", "的": "ㄉㄜ5",
             "來": "ㄌㄞ2"}

    class G2PWConverter:
        def __init__(self, model_dir="G2PWModel/", num_workers=None, **kw):
            self.num_workers = num_workers if num_workers else 2
            _log(call="g2pw_ctor")

        def __call__(self, sentences):
            if isinstance(sentences, str):
                sentences = [sentences]
            _log(call="g2pw_call", num_workers=self.num_workers)
            return [[TABLE.get(c) for c in s] for s in sentences]
''')

# 假的 torchaudio:info() 以標準庫 wave 讀長度(真 torchaudio 2.3.1 的 info 回傳同名欄位)
FAKE_TORCHAUDIO = textwrap.dedent('''
    import wave

    class _Info:
        def __init__(self, frames, rate):
            self.num_frames, self.sample_rate = frames, rate

    def info(path):
        with wave.open(str(path), "rb") as w:
            return _Info(w.getnframes(), w.getframerate())
''')


@pytest.fixture
def breezy(tmp_path):
    """假的 BreezyVoice 目錄:假模組、G2PWModel/version、聲線檔;預設模型快取存在。"""
    d = tmp_path / "BreezyVoice"
    (d / "g2pw").mkdir(parents=True)
    (d / "single_inference.py").write_text(FAKE_SI, encoding="utf-8")
    (d / "g2pw" / "__init__.py").write_text(FAKE_G2PW, encoding="utf-8")
    (d / "torchaudio.py").write_text(FAKE_TORCHAUDIO, encoding="utf-8")
    (d / "G2PWModel").mkdir()
    (d / "G2PWModel" / "version").write_text("2", encoding="utf-8")
    (d / "data").mkdir()
    (d / "data" / "voice.wav").write_bytes(b"RIFF")
    (d / "cache_ok").write_text("", encoding="utf-8")
    return d


def calls(d):
    log = d / "calls.log"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def count(d, name):
    return sum(1 for c in calls(d) if c["call"] == name)


def run_worker(d, *args):
    return subprocess.run([sys.executable, str(WORKER), *args], cwd=d, env=dict(tai.BREEZY_ENV),
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


def write_jobs(tmp_path, d, jobs, **over):
    body = {"prompt_wav": str(d / "data" / "voice.wav"), "prompt_text": PROMPT_TEXT, "jobs": jobs}
    body.update(over)
    p = tmp_path / "jobs.json"
    p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return p


THREE = [{"id": "aaaa0001", "text": "大家好,歡迎收聽。"},
         {"id": "bbbb0002", "text": "今天來聊台灣夜市!"},
         {"id": "cccc0003", "text": "你有試過嗎?"}]


def test_三句只載入一次模型且逐句輸出(tmp_path, breezy):
    out = tmp_path / "out"
    out.mkdir()
    r = run_worker(breezy, "synth", "--jobs", str(write_jobs(tmp_path, breezy, THREE)), "--out", str(out))
    assert r.returncode == 0, r.stderr
    assert count(breezy, "ctor") == 1
    assert count(breezy, "infer") == 3
    assert count(breezy, "download") == 0
    # 逐句推論:每次只送一句,不跨句串接
    assert [c["text"] for c in calls(breezy) if c["call"] == "infer"] == [j["text"] for j in THREE]
    assert sorted(p.name for p in out.glob("*.wav")) == ["aaaa0001.wav", "bbbb0002.wav", "cccc0003.wav"]
    # stdout 只有三行進度 JSON,依序、鍵恰為 id 與 seconds(BreezyVoice 自己的 print 不得混入)
    lines = r.stdout.splitlines()
    assert len(lines) == 3
    rows = [json.loads(line) for line in lines]
    assert [row["id"] for row in rows] == ["aaaa0001", "bbbb0002", "cccc0003"]
    assert all(set(row) == {"id", "seconds"} for row in rows)
    assert all(row["seconds"] == pytest.approx(0.5) for row in rows)
    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert result["results"] == rows


def test_G2PW建立後num_workers設為0(tmp_path, breezy):
    out = tmp_path / "out"
    out.mkdir()
    r = run_worker(breezy, "synth", "--jobs", str(write_jobs(tmp_path, breezy, THREE[:1])), "--out", str(out))
    assert r.returncode == 0, r.stderr
    assert count(breezy, "g2pw_ctor") == 1
    assert [c["num_workers"] for c in calls(breezy) if c["call"] == "infer"] == [0]


def test_缺聲線逐字稿不呼叫轉寫(tmp_path, breezy):
    out = tmp_path / "out"
    out.mkdir()
    for bad in ("", "   ", None):
        jobs = write_jobs(tmp_path, breezy, THREE, prompt_text=bad)
        r = run_worker(breezy, "synth", "--jobs", str(jobs), "--out", str(out))
        assert r.returncode != 0
        assert "逐字稿" in r.stderr
    body = json.loads(jobs.read_text(encoding="utf-8"))
    del body["prompt_text"]
    jobs.write_text(json.dumps(body), encoding="utf-8")
    r = run_worker(breezy, "synth", "--jobs", str(jobs), "--out", str(out))
    assert r.returncode != 0
    assert count(breezy, "transcribe") == 0
    assert count(breezy, "ctor") == 0
    assert not list(out.glob("*.wav"))


def test_模型快取缺失時離線失敗不下載(tmp_path, breezy):
    (breezy / "cache_ok").unlink()
    out = tmp_path / "out"
    out.mkdir()
    r = run_worker(breezy, "synth", "--jobs", str(write_jobs(tmp_path, breezy, THREE)), "--out", str(out))
    assert r.returncode != 0
    assert count(breezy, "download") == 0
    assert not list(out.glob("*.wav"))
    assert r.stdout == ""
    # 與 taiwan_ai.SPEAK_FAILED_HINT 同義:執行期不連網、在建置期(允許連網)先下載模型快取;不提示 setup 腳本
    assert "HF_HUB_OFFLINE=1" in r.stderr
    assert "建置期" in r.stderr and "下載" in r.stderr and "快取" in r.stderr
    assert "setup_breezyvoice" not in r.stderr
    assert "/Users/" not in r.stderr and str(tmp_path) not in r.stderr
    assert "Traceback" not in r.stderr


def test_G2PW模型缺失時不建構轉換器(tmp_path, breezy):
    # 真的 G2PWConverter 在 G2PWModel/version 不存在時會以 requests 下載(不受 HF_HUB_OFFLINE 管),必須先擋
    (breezy / "G2PWModel" / "version").unlink()
    out = tmp_path / "out"
    out.mkdir()
    r = run_worker(breezy, "synth", "--jobs", str(write_jobs(tmp_path, breezy, THREE)), "--out", str(out))
    assert r.returncode != 0
    assert count(breezy, "g2pw_ctor") == 0
    assert "G2PW" in r.stderr and "建置期" in r.stderr
    assert str(tmp_path) not in r.stderr
    r = run_worker(breezy, "annotate", "--input", str(_annotate_input(tmp_path, "今天")))
    assert r.returncode != 0
    assert count(breezy, "g2pw_ctor") == 0


@pytest.mark.parametrize("job", [
    {"id": "../evil01", "text": "你好。"},
    {"id": "AAAA0001", "text": "你好。"},
    {"id": "abc", "text": "你好。"},
    {"id": "aaaa0001", "text": ""},
    {"id": "aaaa0001", "text": "好" * 501},
    {"id": "aaaa0001", "text": "插入[:ㄏㄠ3]語法"},
    {"id": "aaaa0001", "text": "未配對的[括號"},
    {"id": "aaaa0001", "text": "好[:abc]"},
])
def test_不合法的jobs拒絕且不載入模型(tmp_path, breezy, job):
    out = tmp_path / "out"
    out.mkdir()
    r = run_worker(breezy, "synth", "--jobs", str(write_jobs(tmp_path, breezy, [job])), "--out", str(out))
    assert r.returncode != 0
    assert count(breezy, "ctor") == 0
    assert not list(out.glob("*.wav")) and not list(tmp_path.glob("*.wav"))
    assert str(tmp_path) not in r.stderr


def test_重複id拒絕(tmp_path, breezy):
    out = tmp_path / "out"
    out.mkdir()
    jobs = [{"id": "aaaa0001", "text": "一。"}, {"id": "aaaa0001", "text": "二。"}]
    r = run_worker(breezy, "synth", "--jobs", str(write_jobs(tmp_path, breezy, jobs)), "--out", str(out))
    assert r.returncode != 0
    assert count(breezy, "ctor") == 0


def test_聲調符號標註轉成BreezyVoice數字聲調(tmp_path, breezy):
    out = tmp_path / "out"
    out.mkdir()
    jobs = [{"id": "aaaa0001", "text": "今天好[:ㄏㄠˋ]的[:˙ㄉㄜ]來[:ㄌㄞˊ]天[:ㄊㄧㄢ]好[:ㄏㄠˇ]的[:ㄉㄜ˙]。"}]
    r = run_worker(breezy, "synth", "--jobs", str(write_jobs(tmp_path, breezy, jobs)), "--out", str(out))
    assert r.returncode == 0, r.stderr
    [infer] = [c for c in calls(breezy) if c["call"] == "infer"]
    assert infer["text"] == "今天好[:ㄏㄠ4]的[:ㄉㄜ5]來[:ㄌㄞ2]天[:ㄊㄧㄢ1]好[:ㄏㄠ3]的[:ㄉㄜ5]。"
    assert infer["prompt_text"] == PROMPT_TEXT


def test_推論失敗時非0結束且不留下WAV(tmp_path, breezy):
    out = tmp_path / "out"
    out.mkdir()
    (breezy / "fail_infer").write_text("", encoding="utf-8")
    r = run_worker(breezy, "synth", "--jobs", str(write_jobs(tmp_path, breezy, THREE)), "--out", str(out))
    assert r.returncode != 0
    assert r.stdout == ""
    assert not (out / "result.json").exists()
    assert not list(out.glob("*.wav"))
    assert "第 1 句" in r.stderr
    assert str(tmp_path) not in r.stderr and "Traceback" not in r.stderr


def _annotate_input(tmp_path, text):
    p = tmp_path / "annotate.json"
    p.write_text(json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8")
    return p


def test_annotate輸出逐字注音陣列(tmp_path, breezy):
    r = run_worker(breezy, "annotate", "--input", str(_annotate_input(tmp_path, "今天天氣真好")))
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == ["ㄐㄧㄣ", "ㄊㄧㄢ", "ㄊㄧㄢ", "ㄑㄧˋ", "ㄓㄣ", "ㄏㄠˇ"]
    assert count(breezy, "ctor") == 0  # annotate 只用 G2PW,不載入語音模型
    assert [c["num_workers"] for c in calls(breezy) if c["call"] == "g2pw_call"] == [0]


def test_annotate非中文字為null且輕聲以點號開頭(tmp_path, breezy):
    r = run_worker(breezy, "annotate", "--input", str(_annotate_input(tmp_path, "好的,A來")))
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == ["ㄏㄠˇ", "˙ㄉㄜ", None, None, "ㄌㄞˊ"]


@pytest.mark.parametrize("text", ["", "好" * 501])
def test_annotate長度限制(tmp_path, breezy, text):
    r = run_worker(breezy, "annotate", "--input", str(_annotate_input(tmp_path, text)))
    assert r.returncode != 0
    assert count(breezy, "g2pw_ctor") == 0


def test_不認得的子命令與缺參數非0結束(tmp_path, breezy):
    assert run_worker(breezy, "speak").returncode != 0
    assert run_worker(breezy, "synth", "--jobs", str(tmp_path / "none.json")).returncode != 0
    r = run_worker(breezy, "synth", "--jobs", str(tmp_path / "none.json"), "--out", str(tmp_path))
    assert r.returncode != 0
    assert str(tmp_path) not in r.stderr


# ---------- CHG-20261029-01 T2:shape 子命令(連音、清晰度、字詞清晰度、語調) ----------
# 本檔的 Python 沒有 numpy:這裡只測參數驗證與「預設參數 = 原樣複製」;實際訊號處理的量測在
# tests/integration/test_shape_real.py(BreezyVoice venv)。

SHAPE_IN = "0123456789abcdef.wav"


def _shape_out(tmp_path):
    out = tmp_path / "shape-out"
    out.mkdir()
    import wave as _wave
    with _wave.open(str(out / SHAPE_IN), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(bytes(range(256)) * 40)
    return out


def _shape_job(**kw):
    job = {"id": "fedcba9876543210", "input": SHAPE_IN, "chars": 12, "connect": 0, "clarity": 100,
           "word_clarity": [], "pitch": None}
    job.update(kw)
    return job


def _run_shape(tmp_path, breezy, jobs, out):
    p = tmp_path / "shape-jobs.json"
    p.write_text(json.dumps({"jobs": jobs}, ensure_ascii=False), encoding="utf-8")
    return run_worker(breezy, "shape", "--jobs", str(p), "--out", str(out))


def test_shape預設參數輸出與輸入逐位元組相同_不需numpy(tmp_path, breezy):
    out = _shape_out(tmp_path)
    r = _run_shape(tmp_path, breezy, [_shape_job()], out)
    assert r.returncode == 0, r.stderr
    rows = [json.loads(x) for x in r.stdout.splitlines()]
    assert rows == [{"id": "fedcba9876543210", "seconds": pytest.approx(10240 / 2 / 22050, abs=0.001),
                     "pitch_points": None}]                  # 修訂 1 T11:沒有語調時 pitch_points 為 null
    assert (out / "fedcba9876543210.wav").read_bytes() == (out / SHAPE_IN).read_bytes()
    assert count(breezy, "ctor") == 0 and count(breezy, "g2pw_ctor") == 0   # 不載入任何模型


@pytest.mark.parametrize("over", [
    {"connect": "50"}, {"clarity": "100"}, {"connect": 50.0}, {"connect": 101}, {"clarity": -1},
    {"connect": True}, {"chars": "12"}, {"chars": 0}, {"chars": 501},
    {"id": "../evil01"}, {"id": "ABCDEF01"}, {"id": "abc"},
    {"input": "../0123456789abcdef.wav"}, {"input": "sub/0123456789abcdef.wav"},
    {"input": "/etc/passwd"}, {"input": "0123456789abcdef.WAV"}, {"input": "abcdef0123.wav"},
    {"word_clarity": [{"start": 0, "end": 2, "value": "40"}]},
    {"word_clarity": [{"start": 0, "end": 13, "value": 40}]},
    {"word_clarity": [{"start": 0, "end": 3, "value": 40}, {"start": 2, "end": 4, "value": 40}]},
    {"word_clarity": [{"start": i, "end": i + 1, "value": 1} for i in range(12)] * 3},
    {"pitch": {"points": [[0, "3"], [1, 0]], "roundness": 50}},
    {"pitch": {"points": [[0, 7], [1, 0]], "roundness": 50}},
    {"pitch": {"points": [[0.5, 0], [0.4, 0]], "roundness": 50}},
    {"pitch": {"points": [[0, 0]], "roundness": 50}},
    {"pitch": {"points": [[0, 0], [1, 0]], "roundness": "50"}},
    {"pitch": {"points": [[0, 0], [1, 0]]}},
    {"extra": 1},
])
def test_shape不合法的jobs結束碼2且不寫檔(tmp_path, breezy, over):
    out = _shape_out(tmp_path)
    r = _run_shape(tmp_path, breezy, [_shape_job(**over)], out)
    assert r.returncode == 2, r.stderr
    assert sorted(p.name for p in out.iterdir()) == [SHAPE_IN]
    assert r.stdout == ""
    assert str(tmp_path) not in r.stderr and "Traceback" not in r.stderr


def test_shape輸入是符號連結拒絕(tmp_path, breezy):
    out = _shape_out(tmp_path)
    (tmp_path / "outside.wav").write_bytes((out / SHAPE_IN).read_bytes())
    (out / SHAPE_IN).unlink()
    (out / SHAPE_IN).symlink_to(tmp_path / "outside.wav")
    r = _run_shape(tmp_path, breezy, [_shape_job()], out)
    assert r.returncode == 2
    assert not (out / "fedcba9876543210.wav").exists()


def test_shape的jobs不是物件或缺參數(tmp_path, breezy):
    out = _shape_out(tmp_path)
    p = tmp_path / "bad.json"
    for body in ("[]", '{"jobs": {}}', '{"jobs": []}', "{壞"):
        p.write_text(body, encoding="utf-8")
        r = run_worker(breezy, "shape", "--jobs", str(p), "--out", str(out))
        assert r.returncode == 2
    assert run_worker(breezy, "shape", "--jobs", str(p)).returncode == 2
    r = run_worker(breezy, "shape", "--jobs", str(p), "--out", str(tmp_path / "none"))
    assert r.returncode == 2


# ---------- CHG-20261029-01 修訂 1 T11:語調自動補點與吸附(R1-7、R1-8、R1-9;純 Python,不需 numpy) ----------

import breezy_worker as bw  # noqa: E402

FIVE = [(0.0, 0.0), (0.25, 3.0), (0.5, -2.0), (0.75, 2.0), (1.0, 0.0)]


def _all_voiced(x0, x1, xd):
    return xd


def _close(got, want):
    assert len(got) == len(want), got
    for (a, b), (c, d) in zip(got, want):
        assert abs(a - c) <= 1e-9 and abs(b - d) <= 1e-9, (got, want)


def test_expand_points_5點全部有聲回8點():
    r = bw.expand_points(FIVE, _all_voiced)
    _close(r["points"], [(0, 0), (0.175, 1.1), (0.25, 3.6), (0.425, 0.5), (0.5, -2.4), (0.675, -0.2),
                         (0.75, 2.4), (1, 0)])
    _close(r["auto"], [(0.175, 1.1), (0.425, 0.5), (0.675, -0.2)])
    _close(r["amplified"], [(0.25, 3.6), (0.5, -2.4), (0.75, 2.4)])


def test_expand_points_插入點無聲改到同段最近有聲處_值在新位置重算():
    calls = []

    def voiced_at(x0, x1, xd):
        calls.append((x0, x1, xd))
        return 0.2 if x1 == 0.25 else xd          # 第 1 段的 0.175 無聲,最近有聲在 0.2

    r = bw.expand_points(FIVE, voiced_at)
    assert [round(c[2], 9) for c in calls] == [0.175, 0.425, 0.675]
    assert [(c[0], c[1]) for c in calls] == [(0.0, 0.25), (0.25, 0.5), (0.5, 0.75)]   # 以使用者原始點分段
    _close(r["auto"][:1], [(0.2, 3 * 0.2 / 0.25 - 1)])                               # 折線 2.4 − 1 = 1.4
    _close(r["points"][:3], [(0, 0), (0.2, 1.4), (0.25, 3.6)])


def test_expand_points_該段全無聲不插入_峰谷仍放大():
    r = bw.expand_points(FIVE, lambda x0, x1, xd: None if x1 == 0.5 else xd)
    _close(r["auto"], [(0.175, 1.1), (0.675, -0.2)])
    _close(r["points"], [(0, 0), (0.175, 1.1), (0.25, 3.6), (0.5, -2.4), (0.675, -0.2), (0.75, 2.4), (1, 0)])
    _close(r["amplified"], [(0.25, 3.6), (0.5, -2.4), (0.75, 2.4)])


def test_expand_points_首尾不處理_只有2點不變_非峰谷不動():
    for pts in ([(0.0, 3.0), (1.0, -3.0)], [(0.0, -6.0), (1.0, 6.0)]):
        r = bw.expand_points(pts, _all_voiced)
        _close(r["points"], pts)
        assert r["auto"] == [] and r["amplified"] == []
    # 2(正值但不比前一點高)、−0.5(負值但不比前一點低)不是峰谷;−1 是谷
    pts = [(0.0, 4.0), (0.3, 2.0), (0.6, -1.0), (0.8, -0.5), (1.0, 5.0)]
    r = bw.expand_points(pts, _all_voiced)
    _close(r["points"], [(0.0, 4.0), (0.3, 2.0), (0.51, 2.0 - 3.0 * 0.7 + 1), (0.6, -1.2), (0.8, -0.5), (1.0, 5.0)])
    _close(r["amplified"], [(0.6, -1.2)])


def test_expand_points_16點交錯峰谷最多30點且在正負7點2內():
    pts = [(i / 15, 6.0 if i % 2 else -6.0) for i in range(16)]
    r = bw.expand_points(pts, _all_voiced)
    assert len(r["points"]) <= 30 and len(r["points"]) == 16 + 14
    assert all(abs(s) <= 7.2 + 1e-12 for _, s in r["points"])
    assert all(b[0] > a[0] for a, b in zip(r["points"], r["points"][1:]))
    assert max(s for _, s in r["points"]) == pytest.approx(7.2) and min(s for _, s in r["points"]) == pytest.approx(-7.2)


def test_snap_voiced_所在幀有聲不動_無聲取同段最近有聲幀_距離相同取前者_離端點至少001():
    times = [i / 100 for i in range(101)]                      # 幀位置 0.00、0.01、…、1.00
    voiced = [False] * 101
    for i in (30, 40, 41, 60):
        voiced[i] = True
    assert bw.snap_voiced(times, voiced, 0.25, 0.5, 0.401) == 0.401   # 所在幀(0.40)有聲:位置不變
    assert bw.snap_voiced(times, voiced, 0.25, 0.5, 0.35) == 0.30     # 0.30 與 0.40 等距:取前者
    assert bw.snap_voiced(times, voiced, 0.25, 0.5, 0.37) == 0.40
    assert bw.snap_voiced(times, voiced, 0.5, 0.6, 0.57) is None      # 0.60 = x1,與端點差 < 0.01
    assert bw.snap_voiced(times, voiced, 0.5, 0.61, 0.58) == 0.60
    assert bw.snap_voiced(times, voiced, 0.7, 0.9, 0.85) is None      # 同段沒有有聲幀
    assert bw.snap_voiced(times, voiced, 0.0, 0.29, 0.2) is None      # 0.30 在段外
