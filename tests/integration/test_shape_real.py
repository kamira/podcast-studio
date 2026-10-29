"""breezy_worker.py shape 的真 venv 整合測試(CHG-20261029-01 T2)。

不列入 `./scripts/check.sh`(`tests/conftest.py` 只在命令列明確指定 `tests/integration/` 時收集)。
worker 與量測輔助(`shape_metrics.py`)都以 BreezyVoice venv 的 Python 執行(numpy、scipy、librosa、
soundfile),環境一律 `taiwan_ai.BREEZY_ENV`;pytest 本身以 check.sh 同一組套件的環境執行:
  uv run --no-project --python '>=3.10' --with pytest --with opencc-python-reimplemented \\
      python -m pytest -s -p no:cacheprovider tests/integration/test_shape_real.py

訊號:`shape_metrics.make` 產生的合成語音替身(擦音 + 有共振峰的母音 + 指定的靜音)。另可以環境變數
`SHAPE_REAL_WAVS`(以 `:` 分隔的 WAV 路徑,例如 BreezyVoice 的試聽檔)加測真語音的清晰度;未設定時略過。
需要真 BreezyVoice 的測試一次只跑一件;shape 不載入模型,不需要 heavy_lock。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import taiwan_ai as tai

HERE = Path(__file__).resolve().parent
WORKER = HERE.parents[1] / "breezy_worker.py"
METRICS = HERE / "shape_metrics.py"
PY = tai.BREEZY / ".venv/bin/python"
IN_ID = "0123456789abcdef"

pytestmark = pytest.mark.skipif(not PY.exists(), reason="本機沒有 tools/BreezyVoice/.venv")

# 在 worker 行程內把 socket 換成計數替身,結束時把建立次數印到 stderr(不改 worker 程式)
SOCKET_WRAP = r"""
import os, runpy, socket, sys
made = []
class _S(socket.socket):
    def __init__(self, *a, **kw):
        made.append(1)
        super().__init__(*a, **kw)
socket.socket = _S
_real = socket.create_connection
def _cc(*a, **kw):
    made.append(1)
    return _real(*a, **kw)
socket.create_connection = _cc
worker = sys.argv[1]
sys.argv = sys.argv[1:]
code = 0
try:
    runpy.run_path(worker, run_name="__main__")
except SystemExit as e:
    code = e.code or 0
finally:
    os.write(2, ("SOCKETS=%d ENV=%s\n" % (len(made), ",".join(
        "%s=%s" % (k, os.environ.get(k)) for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "PATH")))).encode())
sys.exit(code)
"""


def metric(cmd: str, **args):
    r = subprocess.run([str(PY), str(METRICS), cmd, json.dumps(args, ensure_ascii=False)], cwd=tai.BREEZY,
                       env=dict(tai.BREEZY_ENV), capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.splitlines()[-1])


def shape(out: Path, jobs: list[dict]):
    p = out.parent / "jobs.json"
    p.write_text(json.dumps({"jobs": jobs}, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([str(PY), "-c", SOCKET_WRAP, str(WORKER), "shape", "--jobs", str(p), "--out", str(out)],
                       cwd=tai.BREEZY, env=dict(tai.BREEZY_ENV), capture_output=True, text=True,
                       encoding="utf-8", timeout=30 + 5 * len(jobs) + 60)
    assert r.returncode == 0, r.stderr[-2000:]
    tail = [ln for ln in r.stderr.splitlines() if ln.startswith("SOCKETS=")][-1]
    assert tail.startswith("SOCKETS=0 "), tail                       # 不開任何 socket
    want = ",".join(f"{k}={tai.BREEZY_ENV[k]}" for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "PATH"))
    assert tail.split("ENV=")[1] == want, tail                       # 以 BREEZY_ENV 執行(離線)
    return [json.loads(x) for x in r.stdout.splitlines()]


def job(jid, **kw):
    j = {"id": jid, "input": f"{IN_ID}.wav", "chars": 6, "connect": 0, "clarity": 100,
         "word_clarity": [], "pitch": None}
    j.update(kw)
    return j


@pytest.fixture
def work(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    return out


def make(out: Path, gaps, **kw) -> int:
    return metric("make", out=str(out / f"{IN_ID}.wav"), gaps=gaps, **kw)["chars"]


def test_連音100_短空隙縮短80以上_長空隙不動_預設輸出逐位元組相同(work):
    chars = make(work, [0.06, 0.06, 0.2, 0.06, 0])
    rows = shape(work, [job("aaaa0001", chars=chars, connect=100), job("aaaa0002", chars=chars)])
    assert [r["id"] for r in rows] == ["aaaa0001", "aaaa0002"]
    assert (work / "aaaa0002.wav").read_bytes() == (work / f"{IN_ID}.wav").read_bytes()
    before = metric("silences", path=str(work / f"{IN_ID}.wav"))
    after = metric("silences", path=str(work / "aaaa0001.wav"))
    short_b = sum(x for x in before["runs"] if x < 0.12)
    short_a = sum(x for x in after["runs"] if x < 0.12)
    long_b = [x for x in before["runs"] if x >= 0.12]
    long_a = [x for x in after["runs"] if x >= 0.12]
    print(f"\n[連音 100%] 短空隙 {short_b:.3f} → {short_a:.3f} 秒;長空隙 {long_b} → {long_a};"
          f"長度 {before['seconds']:.3f} → {after['seconds']:.3f}")
    assert short_b == pytest.approx(0.18, abs=0.01)
    assert short_a <= short_b * 0.2
    assert len(long_a) == 1 and abs(long_a[0] - long_b[0]) <= 0.010


def _speech_inputs(work):
    """(標籤, 輸入檔, 字數):合成語音替身,另加 SHAPE_REAL_WAVS 指定的真語音。"""
    items = [("替身", work / f"{IN_ID}.wav", make(work, [0, 0.04, 0, 0, 0.05, 0, 0]))]
    for k, p in enumerate(x for x in os.environ.get("SHAPE_REAL_WAVS", "").split(":") if x):
        dst = work / f"{'%016x' % (k + 1)}.wav"
        shutil.copyfile(p, dst)
        items.append((Path(p).name, dst, 12))
    return items


def test_清晰度方案C_0_25_50(work):
    """修訂 1 T10(R1-1):方案 C。0% 子音 ≥ 3 kHz 降 ≥ 18 dB、離散度降 ≥ 20%、RMS 差 ≤ 0.5 dB;25% 與 0% 不悶
    (母音「低−中高」≤ +1.0 dB)、不削高頻(非子音幀 ≥ 4 kHz 變化 ≥ −2 dB);50% 子音降 ≥ 8 dB;不含低通。"""
    for label, src, chars in _speech_inputs(work):
        rows = shape(work, [job("cccc0000", input=src.name, chars=chars, clarity=0),
                            job("cccc0025", input=src.name, chars=chars, clarity=25),
                            job("cccc0050", input=src.name, chars=chars, clarity=50)])
        assert len(rows) == 3
        m = {p: metric("clar", orig=str(src), proc=str(work / f"cccc00{p:02d}.wav")) for p in (0, 25, 50)}
        print(f"\n[清晰度 {label}] " + ";".join(f"{p}%:{m[p]}" for p in (50, 25, 0)))
        assert m[0]["consonant_db"] <= -18
        assert m[0]["dispersion_pct"] <= -20
        for p in (0, 25, 50):
            assert abs(m[p]["rms_db"]) <= 0.5, p
        for p in (0, 25):
            assert m[p]["vowel_lowmid_db"] <= 1.0, p                         # 不悶
            assert m[p]["nonconsonant_4k_db"] >= -2.0, p                     # 不削高頻
        assert m[50]["consonant_db"] <= -8
        for p in (0, 25, 50):
            assert metric("lowpass_calls", path=str(src), p=p)["calls"] == 0, p  # 方案 C 不用低通


def test_清晰度100逐樣本等於輸入_每句處理半秒內(work):
    for label, src, _ in _speech_inputs(work):
        assert metric("identity", path=str(src))["same"], label
        t = metric("timing", path=str(src), ps=[0, 25, 50, 75])
        print(f"\n[清晰度耗時 {label}] {t}")
        assert max(t["seconds"].values()) <= 0.5


def test_字詞清晰度只影響區段(work):
    chars = make(work, [0, 0.04, 0, 0, 0.05, 0, 0])
    src = work / f"{IN_ID}.wav"
    shape(work, [job("dddd0001", chars=chars, clarity=80),
                 job("dddd0002", chars=chars, clarity=80, word_clarity=[{"start": 2, "end": 4, "value": 0}])])
    b = metric("bounds", path=str(src), chars=chars)
    sr, s, e = b["sr"], b["bounds"][2], b["bounds"][4]
    half = int(0.01 * sr) + 1
    same = metric("same_outside", a=str(work / "dddd0001.wav"), b=str(work / "dddd0002.wav"),
                  lo=s - half, hi=e + half)
    assert same["same"], same
    assert same["inside_diff"] > 0
    ra = metric("clar", orig=str(src), proc=str(work / "dddd0001.wav"), start_s=s / sr, end_s=e / sr)
    rb = metric("clar", orig=str(src), proc=str(work / "dddd0002.wav"), start_s=s / sr, end_s=e / sr)
    print(f"\n[字詞清晰度] 區段 {s / sr:.3f}–{e / sr:.3f} 秒:整句 80% {ra};區段 0% {rb}")
    assert rb["consonant_db"] < ra["consonant_db"] - 6
    assert rb["dispersion_pct"] < ra["dispersion_pct"]


def test_語調5點誤差中位1半音以內_長度不變(work):
    """修訂 1 T11:誤差對 worker 實際套用的內部曲線(stdout 的 pitch_points)計算。"""
    chars = make(work, [0] * 9, f0=150)
    pts = [[0, 0], [0.25, 3], [0.55, -2], [0.8, 2], [1.0, -3]]
    [row] = shape(work, [job("eeee0001", chars=chars, pitch={"points": pts, "roundness": 50})])
    inner = row["pitch_points"]
    assert isinstance(inner, list) and 5 <= len(inner) <= 8
    m = metric("pitch", orig=str(work / f"{IN_ID}.wav"), proc=str(work / "eeee0001.wav"), points=inner,
               roundness=50)
    print(f"\n[語調] 內部點 {inner};{m}")
    assert m["frames"] > 50
    assert m["median"] <= 1.0
    assert abs(m["len_out"] - m["len_in"]) <= 0.010


FIVE = [[0, 0], [0.25, 3], [0.5, -2], [0.75, 2], [1, 0]]


def test_pitch_curve經過每個實際套用的點_0為折線_100為PCHIP():
    m = metric("curve", points=FIVE)
    print(f"\n[語調曲線] {m}")
    assert len(m["inner"]) == 8
    for r in (0, 50, 100):
        assert m[f"through_{r}"] <= 1e-9, r
    assert m["lin_0"] <= 1e-9 and m["pchip_100"] <= 1e-9


def _auto(inner, user):
    xs = {round(x, 6) for x, _ in user}
    return [q for q in inner if round(q[0], 6) not in xs]


def test_真語音語調圓弧100位移峰谷7半音以上_自動補點附近有聲_每句1秒內(work):
    wavs = [x for x in os.environ.get("SHAPE_REAL_WAVS", "").split(":") if x]
    if not wavs:
        pytest.skip("未設定 SHAPE_REAL_WAVS")
    for k, p in enumerate(wavs):
        src = work / f"{'%016x' % (k + 1)}.wav"
        shutil.copyfile(p, src)
        res = {}
        for r in (0, 50, 100):
            jid = f"ffff{k:04x}{r:04x}0000"[:16]
            [row] = shape(work, [job(jid, input=src.name, chars=12, pitch={"points": FIVE, "roundness": r})])
            inner = row["pitch_points"]
            auto = _auto(inner, FIVE)
            m = metric("pitch_swing", orig=str(src), proc=str(work / f"{jid}.wav"), points=inner, roundness=r,
                       auto=auto)
            res[r] = (inner, auto, m)
            print(f"\n[語調 {Path(p).name} 圓弧 {r}%] 內部點 {inner};自動補點 {auto};{m}")
            assert abs(m["len_out"] - m["len_in"]) <= 0.010
            assert m["median"] <= 1.0
            assert len(auto) == 3 and all(n >= 1 for n in m["auto_voiced"]), m["auto_voiced"]
        assert res[100][2]["swing"] >= 7.0
        t = metric("pitch_timing", path=str(src), points=FIVE, roundness=100)
        print(f"[語調耗時 {Path(p).name}] {t}")
        assert t["seconds"] <= 1.0
