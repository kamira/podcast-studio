# -*- coding: utf-8 -*-
"""shape 整合測試的量測輔助(CHG-20261029-01 T2)。

只由 BreezyVoice venv 的 Python 執行(需要 numpy、scipy、librosa、soundfile);pytest 端以子程序呼叫,
引數與結果一律為 JSON。不寫 repo、不連網。指標定義同 CHG「先實測後製方案」:
  子音能量 = 原音判定為子音的幀(非靜音且 3 kHz 以上能量比為前 25%)在 3 kHz 以上的總能量變化(dB)
  包絡離散度 = 各幀倒頻譜包絡到全句平均包絡的距離平均(變化 %,負值 = 往中性靠)
(修訂 1 T10)另以 CHG「修訂 1 實測 (a)」的定義(幀分類同 worker:非靜音且 3 kHz 以上能量比為前 35% 為子音幀,
其餘非靜音幀為母音幀):
  母音「低−中高」= 母音幀在 125 / 250 / 500 Hz 八度帶的能量變化平均 − 1 k / 2 kHz 八度帶的能量變化平均(dB;正 = 悶)
  非子音幀 ≥ 4 kHz = 母音幀 4 kHz 以上的總能量變化(dB;大幅負值 = 高頻被削)

用法:python shape_metrics.py <命令> '<JSON 引數>'
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

import breezy_worker as bw  # noqa: E402

SR = 22050


def _load(path):
    y, sr = sf.read(path, dtype="float64", always_2d=True)
    return y.mean(axis=1), sr


def _save(path, y, sr=SR):
    sf.write(path, np.clip(y, -1, 1), sr, subtype="PCM_16", format="WAV")


def _vowel(n, f0, formants, rng):
    t = np.arange(n) / SR
    f = f0 * (1 + 0.03 * np.sin(2 * np.pi * 3 * t))          # 輕微音高起伏
    phase = 2 * np.pi * np.cumsum(f) / SR
    out = np.zeros(n)
    for h in range(1, 40):
        fh = f0 * h
        if fh > SR / 2 - 500:
            break
        amp = sum(1.0 / (1 + ((fh - fm) / bw_) ** 2) for fm, bw_ in formants) / h ** 0.3
        out += amp * np.sin(h * phase + rng.uniform(0, 2 * np.pi))
    env = np.minimum(1, np.minimum(np.arange(n), n - np.arange(n)) / (0.02 * SR))
    return out / np.max(np.abs(out)) * env


def _fricative(n, rng):
    from scipy.signal import butter, sosfilt
    noise = rng.standard_normal(n)
    hp = sosfilt(butter(4, 3500, btype="high", fs=SR, output="sos"), noise)
    env = np.minimum(1, np.minimum(np.arange(n), n - np.arange(n)) / (0.01 * SR))
    return hp / np.max(np.abs(hp)) * env


VOWELS = [[(800, 90), (1200, 110), (2500, 160)], [(300, 60), (2300, 120), (3000, 160)],
          [(350, 60), (800, 90), (2400, 160)], [(500, 80), (1000, 100), (2600, 160)],
          [(400, 70), (2000, 120), (2800, 160)]]


def make(args):
    """合成語音替身:音節 = (擦音 50 ms +)母音 180 ms;gaps 為各音節之後的靜音秒數(0 = 緊接)。"""
    rng = np.random.default_rng(args.get("seed", 7))
    parts = [np.zeros(int(0.05 * SR))]
    for k, gap in enumerate(args["gaps"] + [0]):
        if k % 2 == 0:
            parts.append(0.35 * _fricative(int(0.05 * SR), rng))
        parts.append(0.6 * _vowel(int(0.18 * SR), args.get("f0", 140), VOWELS[k % len(VOWELS)], rng))
        if gap:
            parts.append(np.zeros(int(gap * SR)))
    parts.append(np.zeros(int(0.05 * SR)))
    _save(args["out"], np.concatenate(parts))
    return {"chars": len(args["gaps"]) + 1}


def silences(args):
    """句內數位靜音段(|樣本| < −40 dBFS、≥ 10 ms)的秒數,不含句首句尾。"""
    y, sr = _load(args["path"])
    quiet = np.abs(y) < 10 ** (-40 / 20)
    runs, i, n = [], 0, len(y)
    while i < n:
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j < n and quiet[j]:
            j += 1
        if i > 0 and j < n and (j - i) / sr >= 0.01:
            runs.append((j - i) / sr)
        i = j
    return {"runs": runs, "seconds": n / sr}


def _stft_mag(y):
    import librosa
    return np.abs(librosa.stft(y, n_fft=1024, hop_length=128, window="hann"))


def _envelope(mag):
    cep = np.fft.irfft(np.log(mag + 1e-7), axis=0)
    cep[30:-30] = 0
    return np.fft.rfft(cep, axis=0).real[:mag.shape[0]]


def clar(args):
    """原音與處理後的子音能量變化、包絡離散度變化、整體 RMS 差(可限定區段 [start_s, end_s))。"""
    y, sr = _load(args["orig"])
    z, _ = _load(args["proc"])
    So, Sz = _stft_mag(y), _stft_mag(z)
    n = min(So.shape[1], Sz.shape[1])
    So, Sz = So[:, :n], Sz[:, :n]
    freqs = np.fft.rfftfreq(1024, 1 / sr)
    pw = So ** 2
    tot = pw.sum(0) + 1e-12
    hf = pw[freqs >= 3000].sum(0) / tot
    edb = 10 * np.log10(tot)
    active = edb > edb.max() - 45
    region = np.ones(n, bool)
    if args.get("start_s") is not None:
        t = np.arange(n) * 128 / sr
        region = (t >= args["start_s"]) & (t < args["end_s"])
    mask = active & (hf >= np.percentile(hf[active], 75)) & region
    hi = freqs >= 3000

    def hf_db(S):
        return 10 * np.log10((S[hi][:, mask] ** 2).sum() + 1e-12)

    act = active & region
    eo, ez = _envelope(So), _envelope(Sz)
    disp_o = np.mean(np.sqrt(((eo - eo[:, active].mean(1, keepdims=True))[:, act] ** 2).mean(0)))
    disp_z = np.mean(np.sqrt(((ez - ez[:, active].mean(1, keepdims=True))[:, act] ** 2).mean(0)))
    rms = 20 * np.log10(np.sqrt(np.mean(z ** 2)) / np.sqrt(np.mean(y ** 2)))
    # 修訂 1:母音幀(原音分類,子音 = 前 35%)的「低−中高」與 4 kHz 以上變化
    vow = act & ~(active & (hf >= np.percentile(hf[active], 100 - 35)))

    def octs(S):
        lv = (S[:, vow] ** 2).mean(1)
        return np.array([10 * np.log10(lv[(freqs >= c / np.sqrt(2)) & (freqs < c * np.sqrt(2))].mean() + 1e-20)
                         for c in (125, 250, 500, 1000, 2000)])

    def v4k(S):
        return 10 * np.log10((S[freqs >= 4000][:, vow] ** 2).sum() + 1e-20)

    d = octs(Sz) - octs(So)
    return {"consonant_db": float(hf_db(Sz) - hf_db(So)), "dispersion_pct": float(100 * (disp_z / disp_o - 1)),
            "rms_db": float(rms), "frames": int(mask.sum()),
            "vowel_lowmid_db": float(d[:3].mean() - d[3:].mean()), "nonconsonant_4k_db": float(v4k(Sz) - v4k(So))}


def identity(args):
    """clarity(y, sr, 100) 是否逐樣本等於輸入(修訂 1 T10)。"""
    y, sr = _load(args["path"])
    bw._load_numpy()
    z = bw.clarity(y, sr, 100)
    return {"same": bool(len(z) == len(y) and np.array_equal(z, y))}


def timing(args):
    """以 clarity(p) 處理整句的耗時(秒;不含 import,先暖機一次)。"""
    import time
    y, sr = _load(args["path"])
    bw._load_numpy()
    bw.clarity(y, sr, 50)
    out = {}
    for p in args["ps"]:
        t = time.perf_counter()
        bw.clarity(y, sr, p)
        out[str(p)] = time.perf_counter() - t
    return {"seconds": out, "len": len(y) / sr}


def lowpass_calls(args):
    """以 clarity(p) 處理時呼叫 sosfiltfilt(低通)的次數。"""
    import scipy.signal
    y, sr = _load(args["path"])
    calls = []
    real = scipy.signal.sosfiltfilt

    def spy(*a, **kw):
        calls.append(1)
        return real(*a, **kw)

    scipy.signal.sosfiltfilt = spy
    bw._load_numpy()
    bw.clarity(y, sr, args["p"])
    return {"calls": len(calls)}


def bounds(args):
    y, sr = _load(args["path"])
    bw._load_numpy()
    return {"bounds": [int(b) for b in bw.estimate_bounds(y, sr, args["chars"])], "sr": sr}


def same_outside(args):
    """兩檔在 [lo, hi) 樣本以外是否逐樣本相同(int16)。"""
    a, _ = sf.read(args["a"], dtype="int16")
    b, _ = sf.read(args["b"], dtype="int16")
    if len(a) != len(b):
        return {"same": False, "reason": "長度不同"}
    keep = np.ones(len(a), bool)
    keep[max(0, args["lo"]):args["hi"]] = False
    return {"same": bool(np.array_equal(a[keep], b[keep])), "inside_diff": int(np.abs(
        a[~keep].astype(int) - b[~keep].astype(int)).max())}


def pitch(args):
    """以 pyin 量處理後相對原音的半音,與目標曲線比較:中位誤差、P90、長度。
    (修訂 1 T11)points 為 worker 實際套用的內部點(shape stdout 的 pitch_points),目標 = 內部曲線。"""
    import librosa
    y, sr = _load(args["orig"])
    z, _ = _load(args["proc"])
    bw._load_numpy()

    def f0(x):
        f, v, _ = librosa.pyin(x, fmin=70, fmax=400, sr=sr, frame_length=1024, hop_length=256)
        return f, v

    fa, va = f0(y)
    fb, vb = f0(z)
    n = min(len(fa), len(fb))
    ok = va[:n] & vb[:n] & ~np.isnan(fa[:n]) & ~np.isnan(fb[:n])
    t = librosa.times_like(fa[:n], sr=sr, hop_length=256)[ok] / (len(y) / sr)
    got = 12 * np.log2(fb[:n][ok] / fa[:n][ok])
    want = bw.pitch_curve(args["points"], args["roundness"], np.minimum(t, 1.0))
    err = np.abs(got - want)
    return {"median": float(np.median(err)), "p90": float(np.percentile(err, 90)), "frames": int(ok.sum()),
            "len_in": len(y) / sr, "len_out": len(z) / sr}


def _f0_semi(x, sr):
    import librosa
    f, v, _ = librosa.pyin(x.astype(np.float32), fmin=70, fmax=400, sr=sr, frame_length=1024, hop_length=256)
    return np.where(v & ~np.isnan(f), 12 * np.log2(np.where(np.isnan(f), 1, f) / 100), np.nan)


def curve(args):
    """(修訂 1 T11)pitch_curve 對 expand_points(全部有聲)的內部點:圓弧 0 / 50 / 100 時經過每一點的最大誤差、
    0% 與折線、100% 與 scipy PchipInterpolator 在 0–1 密格點上的最大差。"""
    from scipy.interpolate import PchipInterpolator
    bw._load_numpy()
    inner = bw.expand_points(args["points"], None)["points"]
    xs, ys = np.array(inner, float).T
    out = {"inner": inner}
    for r in (0, 50, 100):
        out[f"through_{r}"] = float(np.max(np.abs(bw.pitch_curve(inner, r, xs) - ys)))
    g = np.linspace(0, 1, 4001)
    out["lin_0"] = float(np.max(np.abs(bw.pitch_curve(inner, 0, g) - np.interp(g, xs, ys))))
    out["pchip_100"] = float(np.max(np.abs(bw.pitch_curve(inner, 100, g) - PchipInterpolator(xs, ys)(g))))
    return out


def pitch_swing(args):
    """(修訂 1 T11,同 CHG「修訂 1 實測 (c)」的定義)位移峰谷 = 輸出與原音逐幀 F0 差(半音,5 幀中位數平滑)
    的最大減最小;整句起伏 = 輸出 F0 第 5–95 百分位差;誤差中位 = |逐幀差 − 內部曲線| 的中位數;
    auto 每點 ±40 ms 內原音的有聲幀數。"""
    y, sr = _load(args["orig"])
    z, _ = _load(args["proc"])
    bw._load_numpy()
    nat, out = _f0_semi(y, sr), _f0_semi(z, sr)
    n = min(len(nat), len(out))
    d = out[:n] - nat[:n]
    ok = ~np.isnan(d)
    sm = np.full_like(d, np.nan)
    for i in np.where(ok)[0]:
        sm[i] = np.nanmedian(d[max(0, i - 2):i + 3])
    dur = len(y) / sr
    tt = np.arange(n) * 256 / sr
    want = bw.pitch_curve(args["points"], args["roundness"], np.minimum(tt / dur, 1.0))
    near = []
    for x, _v in args.get("auto", []):
        m = np.abs(np.arange(len(nat)) * 256 / sr - x * dur) <= 0.04
        near.append(int((m & ~np.isnan(nat)).sum()))
    return {"swing": float(np.nanmax(sm) - np.nanmin(sm)),
            "range_out": float(np.nanpercentile(out, 95) - np.nanpercentile(out, 5)),
            "range_in": float(np.nanpercentile(nat, 95) - np.nanpercentile(nat, 5)),
            "median": float(np.nanmedian(np.abs(d[ok] - want[ok]))), "auto_voiced": near,
            "len_in": len(y) / sr, "len_out": len(z) / sr}


def voiced_near(args):
    """原音(pyin)在各位置 ±40 ms 內的有聲幀數,以及總有聲幀數。"""
    y, sr = _load(args["path"])
    bw._load_numpy()
    nat = _f0_semi(y, sr)
    t = np.arange(len(nat)) * 256 / sr
    dur = len(y) / sr
    return {"near": [int(((np.abs(t - x * dur) <= 0.04) & ~np.isnan(nat)).sum()) for x in args["xs"]],
            "frames": [int((np.abs(t - x * dur) <= 0.04).sum()) for x in args["xs"]], "voiced": int((~np.isnan(nat)).sum()),
            "total": len(nat)}


def pitch_timing(args):
    """apply_pitch 處理整句的耗時(秒;含 pyin,先暖機一次)。"""
    import time
    y, sr = _load(args["path"])
    bw._load_numpy()
    pitch = {"points": args["points"], "roundness": args["roundness"]}
    bw.apply_pitch(y, sr, pitch)
    t = time.perf_counter()
    bw.apply_pitch(y, sr, pitch)
    return {"seconds": time.perf_counter() - t, "len": len(y) / sr}


if __name__ == "__main__":
    cmd, arg = sys.argv[1], json.loads(sys.argv[2])
    print(json.dumps({"make": make, "silences": silences, "clar": clar, "lowpass_calls": lowpass_calls,
                      "bounds": bounds, "same_outside": same_outside, "pitch": pitch, "identity": identity,
                      "timing": timing, "curve": curve, "pitch_swing": pitch_swing, "voiced_near": voiced_near,
                      "pitch_timing": pitch_timing}[cmd](arg)))
