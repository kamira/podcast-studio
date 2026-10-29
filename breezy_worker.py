# -*- coding: utf-8 -*-
"""BreezyVoice 逐句合成 worker(CHG-20261022-04 T1)。

以 BreezyVoice venv 的 Python 執行,cwd 一律為 `tools/BreezyVoice`(模組與 `G2PWModel/` 都以 cwd 找),
環境一律由呼叫端傳入 `taiwan_ai.BREEZY_ENV`(含 HF_HUB_OFFLINE=1、TRANSFORMERS_OFFLINE=1;修訂 3,R3-3)。
本檔只用標準庫與 BreezyVoice venv 內的模組,不 import repo 內其他模組。

子命令:
  synth --jobs <json> --out <dir>
      jobs 檔:{"prompt_wav": 聲線檔絕對路徑, "prompt_text": 聲線逐字稿(必填),
               "model": 模型名(可省略,預設 MediaTek-Research/BreezyVoice-300M),
               "jobs": [{"id": ^[0-9a-f]{8,32}$, "text": 單句文字(1–500 字)}, …]}
      CustomCosyVoice 只建構一次;G2PWConverter 建立後把 num_workers 設為 0(建構子把 0 當假值忽略);
      每句各自呼叫 `single_inference.single_inference`(不把多句串成一次推論),寫完 `<out>/<id>.wav`
      即在 stdout 印一行 {"id", "seconds"} JSON;全部完成後寫 `<out>/result.json`({"results": [...]})。
      句中 `字[:注音]` 標註以聲調符號(ˊˇˋ˙)書寫,送進 BreezyVoice 前轉成它的數字聲調(1–5);
      標註以外的 `[`、`]` 一律拒絕(上游應已轉成全形)。
  annotate --input <json>
      輸入檔:{"text": 1–500 字};stdout 印一個 JSON 陣列,長度等於字數,每字為聲調符號格式的注音
      (一聲不標、輕聲以 ˙ 開頭),非中文字為 null。只用 G2PW,不載入語音模型。
  shape --jobs <json> --out <dir>(CHG-20261029-01 T2)
      後製:連音 → 清晰度(整句,再疊字詞;修訂 1 起為方案 C,不含低通)→ 語調(TD-PSOLA)。jobs 檔:
        {"jobs": [{"id": ^[0-9a-f]{8,32}$(輸出 <out>/<id>.wav), "input": 「十六進位 id + .wav」(<out> 內的
                   一般檔,不可是符號連結), "chars": 估計字界用的字數(1–500), "connect": 0–100, "clarity": 0–100,
                   "word_clarity": [{"start", "end", "value": 0–100}](字索引以 chars 計,≤ 32 段、不重疊),
                   "pitch": null 或 {"points": [[位置 0–1, 半音 −6–6]](2–16 點、位置嚴格遞增), "roundness": 0–100}}]}
      全部參數為預設(連音 0、清晰度 100、無字詞、無語調)時原樣複製輸入(逐位元組相同,不 import numpy);
      其他情況只 import BreezyVoice venv 已有的 numpy、scipy、librosa、soundfile,不載入任何模型、不連網。
      每句寫完即在 stdout 印一行 {"id", "seconds"}。只在 --out 目錄內讀寫。

stdout 只輸出上述 JSON:BreezyVoice 自己印到 stdout 的訊息一律改送 stderr。
錯誤訊息一律為繁體中文、印到 stderr、不含本機絕對路徑與堆疊;結束碼:2 參數或輸入不合法、
3 模型載入失敗(含快取不存在)、4 合成失敗。不走 Whisper 轉寫後備路徑:聲線逐字稿缺少即拒絕。
"""
import argparse
import json
import os
import re
import sys

DEFAULT_MODEL = "MediaTek-Research/BreezyVoice-300M"
MAX_TEXT = 500
MAX_JOBS = 2000
ID_RE = re.compile(r"[0-9a-f]{8,32}", re.ASCII)
MODEL_RE = re.compile(r"[A-Za-z0-9][\w.\-]*/[\w.\-]+", re.ASCII)
# 注音符號 ㄅ(U+3105)–ㄩ(U+3129);聲調符號 ˊ(二聲)ˇ(三聲)ˋ(四聲)˙(輕聲)
ZHUYIN = "ㄅ-ㄩ"
TONE_MARKS = {"ˊ": "2", "ˇ": "3", "ˋ": "4", "˙": "5"}
DIGIT_MARKS = {"1": "", "2": "ˊ", "3": "ˇ", "4": "ˋ"}
ANNOT_RE = re.compile(r"\[:([^\[\]]*)\]")
MARKED_RE = re.compile(rf"(˙?)([{ZHUYIN}]{{1,4}})([ˊˇˋ˙]?)")
DIGIT_RE = re.compile(rf"([{ZHUYIN}]{{1,4}})([1-5])")

LOAD_FAILED_HINT = ("✗ 載入語音模型失敗。執行期不連網下載模型(HF_HUB_OFFLINE=1);若 BreezyVoice 或"
                    " bert-base-chinese 的 Hugging Face 模型快取不存在,需在建置期(允許連網)先下載一次再試。")
G2PW_MISSING_HINT = ("✗ 找不到 G2PW 注音模型(tools/BreezyVoice/G2PWModel)。執行期不連網下載;"
                     "需在建置期(允許連網)先讓 BreezyVoice 下載一次 G2PW 模型再試。")


class WorkerError(Exception):
    """可預期的失敗:訊息直接給使用者看(繁中、不含路徑),code 為結束碼。"""

    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


class _ArgParser(argparse.ArgumentParser):
    def error(self, message):
        raise WorkerError("✗ 參數不正確:synth、shape 需要 --jobs 與 --out;annotate 需要 --input")


def marked_to_digit(zhuyin):
    """聲調符號格式(ㄏㄠˇ、˙ㄉㄜ、ㄉㄜ˙、ㄐㄧㄣ)→ BreezyVoice 的數字聲調(ㄏㄠ3);不合法回 None。"""
    m = MARKED_RE.fullmatch(zhuyin)
    if not m or (m.group(1) and m.group(3)):
        return None
    mark = m.group(1) or m.group(3)
    return m.group(2) + (TONE_MARKS[mark] if mark else "1")


def digit_to_marked(zhuyin):
    """G2PW 的數字聲調(ㄏㄠ3、ㄉㄜ5)→ 聲調符號格式(ㄏㄠˇ、˙ㄉㄜ);不認得的格式回 None。"""
    m = DIGIT_RE.fullmatch(zhuyin or "")
    if not m:
        return None
    body, tone = m.groups()
    return "˙" + body if tone == "5" else body + DIGIT_MARKS[tone]


def to_breezy_text(text):
    """把句中的 `[:聲調符號注音]` 轉成 BreezyVoice 的 `[:數字聲調注音]`;其他方括號或不合法標註丟 WorkerError。"""
    def repl(m):
        digit = marked_to_digit(m.group(1))
        if digit is None:
            raise WorkerError("✗ 注音標註不合法:只接受注音符號與聲調符號(ˊˇˋ˙)")
        return f"[:{digit}]"

    converted = ANNOT_RE.sub(repl, text)
    if "[" in ANNOT_RE.sub("", text) or "]" in ANNOT_RE.sub("", text):
        raise WorkerError("✗ 句子含有標註以外的方括號,已拒絕")
    return converted


def _read_json(path, what):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        raise WorkerError(f"✗ 無法讀取{what}(檔案不存在或不是合法的 JSON)") from None


def _check_text(text, label):
    if not isinstance(text, str) or not text.strip():
        raise WorkerError(f"✗ {label}是空的")
    if len(text) > MAX_TEXT:
        raise WorkerError(f"✗ {label}超過 {MAX_TEXT} 字")


def load_jobs(path):
    """讀取並驗證 jobs 檔;回傳 (prompt_wav, prompt_text, model, [(id, BreezyVoice 文字)])。"""
    body = _read_json(path, " jobs 檔")
    if not isinstance(body, dict):
        raise WorkerError("✗ jobs 檔格式不正確")
    prompt_text = body.get("prompt_text")
    if not isinstance(prompt_text, str) or not prompt_text.strip():
        # 不走 single_inference 的 Whisper 轉寫後備路徑(會載入 openai/whisper-base)
        raise WorkerError("✗ 缺少聲線逐字稿,已拒絕合成(不以語音辨識代替)")
    prompt_wav = body.get("prompt_wav")
    if not isinstance(prompt_wav, str) or not os.path.isfile(prompt_wav):
        raise WorkerError("✗ 聲線檔不存在")
    model = body.get("model", DEFAULT_MODEL)
    if not isinstance(model, str) or not MODEL_RE.fullmatch(model):
        raise WorkerError("✗ 模型名稱格式不正確")
    jobs = body.get("jobs")
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= MAX_JOBS:
        raise WorkerError(f"✗ jobs 必須是 1–{MAX_JOBS} 句")
    seen, out = set(), []
    for n, job in enumerate(jobs, 1):
        if not isinstance(job, dict):
            raise WorkerError(f"✗ 第 {n} 句格式不正確")
        jid, text = job.get("id"), job.get("text")
        if not isinstance(jid, str) or not ID_RE.fullmatch(jid):
            raise WorkerError(f"✗ 第 {n} 句的 id 格式不正確")
        if jid in seen:
            raise WorkerError(f"✗ 第 {n} 句的 id 重複")
        seen.add(jid)
        _check_text(text, f"第 {n} 句")
        try:
            out.append((jid, to_breezy_text(text)))
        except WorkerError as e:
            raise WorkerError(f"✗ 第 {n} 句:{str(e).lstrip('✗ ')}") from None
    return prompt_wav, prompt_text, model, out


def _require_g2pw_model():
    # G2PWConverter 在 G2PWModel/version 不存在時會以 requests 直接下載,不受 HF_HUB_OFFLINE 約束,先擋下
    if not os.path.isfile(os.path.join("G2PWModel", "version")):
        raise WorkerError(G2PW_MISSING_HINT, 3)


def _g2pw():
    _require_g2pw_model()
    try:
        from g2pw import G2PWConverter
        conv = G2PWConverter()
    except Exception:
        raise WorkerError(LOAD_FAILED_HINT, 3) from None
    # 建構子參數 num_workers=0 會被當成假值改用設定檔的 2;每次呼叫都會開 DataLoader 子行程(約 19 秒),
    # 必須在建立後把屬性設為 0(2026-10-26 實測 B2)
    conv.num_workers = 0
    return conv


def _write_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def cmd_synth(args, emit):
    prompt_wav, prompt_text, model, jobs = load_jobs(args.jobs)
    if not os.path.isdir(args.out):
        raise WorkerError("✗ 輸出目錄不存在")
    _require_g2pw_model()
    try:
        import single_inference as si
        import torchaudio
    except Exception:
        raise WorkerError("✗ 無法載入 BreezyVoice 模組(請確認以 tools/BreezyVoice 的 venv 執行)", 3) from None
    try:
        cosyvoice = si.CustomCosyVoice(model)  # 同一次呼叫只建構一次
    except Exception:
        raise WorkerError(LOAD_FAILED_HINT, 3) from None
    converter = _g2pw()
    results = []
    for n, (jid, text) in enumerate(jobs, 1):
        final = os.path.join(args.out, f"{jid}.wav")
        tmp = os.path.join(args.out, f"{jid}.part.wav")  # 副檔名須為 .wav,torchaudio 依副檔名決定格式
        try:
            si.single_inference(prompt_wav, text, tmp, cosyvoice, converter, prompt_text)
            info = torchaudio.info(tmp)
            seconds = round(info.num_frames / info.sample_rate, 3)
            os.replace(tmp, final)
        except Exception as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise WorkerError(f"✗ 第 {n} 句({jid})合成失敗:{type(e).__name__}", 4) from None
        row = {"id": jid, "seconds": seconds}
        results.append(row)
        emit(row)
    _write_json_atomic(os.path.join(args.out, "result.json"), {"results": results})


def cmd_annotate(args, emit):
    body = _read_json(args.input, "輸入檔")
    text = body.get("text") if isinstance(body, dict) else None
    _check_text(text, "要標註的文字")
    converter = _g2pw()
    try:
        raw = converter(text)[0]
    except Exception as e:
        raise WorkerError(f"✗ 注音標註失敗:{type(e).__name__}", 4) from None
    emit([digit_to_marked(z) if z else None for z in raw])


# ---------- shape:後製(CHG-20261029-01 T2) ----------
# 參數與演算法依 CHG-20261029-01 D5~D12 與「拉桿百分比對應」表;實測依據見該 CHG「先實測後製方案」。

WAV_NAME_RE = re.compile(r"[0-9a-f]{8,32}\.wav", re.ASCII)
SHAPE_KEYS = {"id", "input", "chars", "connect", "clarity", "word_clarity", "pitch"}
MAX_WORD_CLARITY = 32
PITCH_POINTS = (2, 16)
SEMI_LIMIT = 6.0
# 連音(D5、D6)
GAP_REL_DB = -30.0          # 句內低於句峰值 −30 dB 的段落
GAP_MIN, GAP_MAX = 0.03, 0.12  # 字間空隙 30–120 ms;長於 120 ms(多為逗號處)不動
GAP_XFADE = 0.012           # 縮空隙切點的等功率交叉淡化
BOUND_OVERLAP = 0.03        # 字界交叉淡化長度 = p × 30 ms
# 清晰度(修訂 1 R1-1:方案 C;取代 D7 v2 的 ①~⑥,拿掉 ⑤ 低通)
N_FFT, HOP, LIFTER = 1024, 128, 30
CONS_TOP = 35               # 子音幀 = 非靜音且 3 kHz 以上能量比為句內前 35%
CONS_MASK = 0.03            # 子音遮罩平滑長度(秒)
LEN_W = 1.0                 # ① 子音包絡往前後母音包絡內插的權重(× c)
C_ATT = 16.0                # ① 子音幀整幀衰減(dB,× c)
SM_S = 0.16                 # ② 轉接區幅度譜移動平均長度(秒,× c)
SM_OUT = 0.2                # ② 轉接區以外的平滑權重
TRANS_PAD = 0.05            # ② 轉接區向前後擴張(秒)
VALLEY_DB = 3.0             # ② 能量谷:比 150 ms 平滑能量低 3 dB 以上
NEU_C = 0.3                 # ③ 往母音平均包絡靠的權重(× c)
CONTRAST = 0.3              # ④ 音節對比降低(沿用)
TILT_MAX = 12.0             # ⑤ 長期頻譜傾斜補償的上下限(dB)
WORD_XFADE = 0.02           # 字詞清晰度區段邊緣交叉淡化(D8)
# 語調(D11)
F0_MIN, F0_MAX = 70, 400

np = None   # numpy:只在需要訊號處理時才載入(預設參數不 import)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def _check_pct(v, label, n):
    if not _is_int(v) or not 0 <= v <= 100:
        raise WorkerError(f"✗ 第 {n} 句的{label}必須是 0–100 的整數")
    return v


def _check_shape_job(job, n, out_dir, seen):
    if not isinstance(job, dict) or set(job) != SHAPE_KEYS:
        raise WorkerError(f"✗ 第 {n} 句格式不正確")
    jid, src = job["id"], job["input"]
    if not isinstance(jid, str) or not ID_RE.fullmatch(jid):
        raise WorkerError(f"✗ 第 {n} 句的 id 格式不正確")
    if jid in seen:
        raise WorkerError(f"✗ 第 {n} 句的 id 重複")
    seen.add(jid)
    if not isinstance(src, str) or not WAV_NAME_RE.fullmatch(src) or src == f"{jid}.wav":
        raise WorkerError(f"✗ 第 {n} 句的輸入檔名不正確")
    path = os.path.join(out_dir, src)
    if os.path.islink(path) or not os.path.isfile(path):
        raise WorkerError(f"✗ 第 {n} 句的輸入檔不存在(或不是一般檔案)")
    chars = job["chars"]
    if not _is_int(chars) or not 1 <= chars <= MAX_TEXT:
        raise WorkerError(f"✗ 第 {n} 句的字數必須是 1–{MAX_TEXT} 的整數")
    _check_pct(job["connect"], "連音", n)
    _check_pct(job["clarity"], "清晰度", n)
    words = job["word_clarity"]
    if not isinstance(words, list) or len(words) > MAX_WORD_CLARITY:
        raise WorkerError(f"✗ 第 {n} 句的字詞清晰度最多 {MAX_WORD_CLARITY} 段")
    spans = []
    for w in words:
        if not isinstance(w, dict) or set(w) != {"start", "end", "value"}:
            raise WorkerError(f"✗ 第 {n} 句的字詞清晰度格式不正確")
        a, b = w["start"], w["end"]
        if not _is_int(a) or not _is_int(b) or not 0 <= a < b <= chars:
            raise WorkerError(f"✗ 第 {n} 句的字詞清晰度範圍不正確")
        spans.append((a, b, _check_pct(w["value"], "字詞清晰度", n)))
    spans.sort()
    if any(y[0] < x[1] for x, y in zip(spans, spans[1:])):
        raise WorkerError(f"✗ 第 {n} 句的字詞清晰度範圍重疊")
    pitch = job["pitch"]
    if pitch is not None:
        if not isinstance(pitch, dict) or set(pitch) != {"points", "roundness"}:
            raise WorkerError(f"✗ 第 {n} 句的語調格式不正確")
        pts = pitch["points"]
        if not isinstance(pts, list) or not PITCH_POINTS[0] <= len(pts) <= PITCH_POINTS[1]:
            raise WorkerError(f"✗ 第 {n} 句的語調關鍵點必須是 {PITCH_POINTS[0]}–{PITCH_POINTS[1]} 個")
        clean = []
        for q in pts:
            if (not isinstance(q, list) or len(q) != 2 or not all(_is_num(x) for x in q)
                    or not 0 <= q[0] <= 1 or not -SEMI_LIMIT <= q[1] <= SEMI_LIMIT):
                raise WorkerError(f"✗ 第 {n} 句的語調關鍵點不正確(位置 0–1、音高 −6–6 半音)")
            clean.append((float(q[0]), float(q[1])))
        if any(b[0] <= a[0] for a, b in zip(clean, clean[1:])):
            raise WorkerError(f"✗ 第 {n} 句的語調關鍵點位置必須嚴格遞增")
        pitch = {"points": clean, "roundness": _check_pct(pitch["roundness"], "圓弧", n)}
    return {"id": jid, "input": path, "chars": chars, "connect": job["connect"], "clarity": job["clarity"],
            "word_clarity": spans, "pitch": pitch}


def load_shape_jobs(path, out_dir):
    """讀取並驗證 shape 的 jobs 檔(所有檔名只在 out_dir 內);不合法丟 WorkerError(結束碼 2)。"""
    if not os.path.isdir(out_dir):
        raise WorkerError("✗ 輸出目錄不存在")
    body = _read_json(path, " jobs 檔")
    jobs = body.get("jobs") if isinstance(body, dict) else None
    if not isinstance(body, dict) or set(body) != {"jobs"} or not isinstance(jobs, list) \
            or not 1 <= len(jobs) <= MAX_JOBS:
        raise WorkerError(f"✗ jobs 檔格式不正確(jobs 必須是 1–{MAX_JOBS} 句)")
    seen = set()
    return [_check_shape_job(job, n, out_dir, seen) for n, job in enumerate(jobs, 1)]


def _is_default(job):
    return job["connect"] == 0 and job["clarity"] == 100 and not job["word_clarity"] and job["pitch"] is None


def _wav_seconds(path):
    import wave
    with wave.open(path, "rb") as w:
        return round(w.getnframes() / w.getframerate(), 3)


def _load_numpy():
    global np
    if np is None:
        try:
            import numpy
        except Exception:
            raise WorkerError("✗ 無法載入 numpy(請確認以 tools/BreezyVoice 的 venv 執行)", 3) from None
        np = numpy


# --- 共用 ---

def _frame_db(y, sr, hop=0.005, win=0.02):
    h, w = max(1, int(sr * hop)), max(1, int(sr * win))
    n = 1 + max(0, (len(y) - w) // h)
    idx = np.minimum(np.arange(n)[:, None] * h + np.arange(w)[None, :], len(y) - 1)
    rms = np.sqrt(np.mean(y[idx] ** 2, axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-9), h, w


def _low_runs(y, sr):
    """句內低音量段(低於句峰值 GAP_REL_DB):回傳 [(起點樣本, 終點樣本, 秒數)](不含句首、句尾)。"""
    db, h, w = _frame_db(y, sr)
    low = db < db.max() + GAP_REL_DB
    runs, i = [], 0
    while i < len(low):
        if not low[i]:
            i += 1
            continue
        j = i
        while j < len(low) and low[j]:
            j += 1
        if i > 0 and j < len(low):
            a, b = i * h, (j - 1) * h + w
            runs.append((a, b, (b - a) / sr))
        i = j
    return runs


def _xfade_join(pieces, n):
    """依序以 n 個樣本的等功率交叉淡化接起各段。"""
    res = pieces[0].copy()
    for piece in pieces[1:]:
        k = min(n, len(res) // 3, len(piece) // 3)
        if k < 2:
            res = np.concatenate([res, piece])
            continue
        t = np.linspace(0, np.pi / 2, k)
        mix = res[-k:] * np.cos(t) + piece[:k] * np.sin(t)
        res = np.concatenate([res[:-k], mix, piece[k:]])
    return res


def _rms_match(z, y):
    return z * (np.sqrt(np.mean(y ** 2)) / (np.sqrt(np.mean(z ** 2)) + 1e-12))


def estimate_bounds(y, sr, chars):
    """以能量包絡估計字界(D8):回傳 chars + 1 個樣本位置(0 … len)。

    平滑 dB 包絡找峰,相鄰峰之間取最低點為候選谷,取最深的 chars − 1 個;找不到足夠的谷時依長度均分
    (長句誤差可達 ±1 字,已知限制)。"""
    from scipy.signal import find_peaks
    n = len(y)
    if chars <= 1 or n < 2:
        return [0, n]
    db, h, w = _frame_db(y, sr, hop=0.005, win=0.025)
    sm = np.convolve(db, np.ones(5) / 5, mode="same")
    peaks, _ = find_peaks(sm, distance=max(1, int(0.09 / 0.005)), prominence=2.0)
    valleys = []
    for p, q in zip(peaks, peaks[1:]):
        v = p + int(np.argmin(sm[p:q]))
        valleys.append((min(sm[p], sm[q]) - sm[v], v))
    valleys.sort(reverse=True)
    if len(valleys) < chars - 1:
        return [round(n * k / chars) for k in range(chars + 1)]
    chosen = sorted(min(n, v * h + w // 2) for _, v in valleys[:chars - 1])
    return [0, *chosen, n]


# --- 連音(D5、D6) ---

def connect(y, sr, p, chars):
    """字間空隙(30–120 ms)縮短 p,再在估計字界交叉淡化 p × 30 ms;長於 120 ms 的低音量段不動。"""
    if p <= 0:
        return y
    xf = int(GAP_XFADE * sr)
    pieces, last = [], 0
    for a, b, sec in _low_runs(y, sr):
        if not GAP_MIN <= sec <= GAP_MAX:
            continue
        keep = int((b - a) * (1 - p))
        cut_a, cut_b = a + keep // 2, b - (keep - keep // 2)
        if cut_b - cut_a <= xf:
            continue
        pieces.append(y[last:cut_a])
        last = cut_b
    pieces.append(y[last:])
    z = _xfade_join(pieces, xf) if len(pieces) > 1 else y.copy()
    ov = int(p * BOUND_OVERLAP * sr)
    if ov < 8:
        return z
    long_runs = [(a, b) for a, b, sec in _low_runs(z, sr) if sec > GAP_MAX]
    bounds = [b for b in estimate_bounds(z, sr, chars)[1:-1]
              if not any(a - ov <= b <= e + ov for a, e in long_runs)]
    if not bounds:
        return z
    return _xfade_join(np.split(z, bounds), ov)


# --- 清晰度(咬字含糊程度;修訂 1 R1-1 方案 C) ---

def _stft(y):
    import librosa
    return librosa.stft(y, n_fft=N_FFT, hop_length=HOP, window="hann")


def _istft(S, n):
    import librosa
    return librosa.istft(S, hop_length=HOP, window="hann", length=n)


def _frame_info(mag, sr):
    freqs = np.fft.rfftfreq(N_FFT, 1 / sr)
    pw = mag ** 2
    tot = pw.sum(0) + 1e-12
    hf = pw[freqs >= 3000].sum(0) / tot
    edb = 10 * np.log10(tot)
    active = edb > edb.max() - 45
    return freqs, hf, edb, active


def _envelope(mag):
    """倒頻譜平滑的對數頻譜包絡(每幀)。"""
    cep = np.fft.irfft(np.log(mag + 1e-7), axis=0)
    cep[LIFTER:-LIFTER] = 0
    return np.fft.rfft(cep, axis=0).real[:mag.shape[0]]


def _classify(mag, sr):
    """以 STFT 幅度譜分類幀:回 (freqs, active, cons, vow);子音幀 = 非靜音且 3 kHz 以上能量比為句內前
    CONS_TOP%,母音幀 = 非靜音且不是子音幀。"""
    freqs, hf, _, active = _frame_info(mag, sr)
    if not active.any():
        z = np.zeros(mag.shape[1], bool)
        return freqs, active, z, z
    cons = active & (hf >= np.percentile(hf[active], 100 - CONS_TOP))
    return freqs, active, cons, active & ~cons


def _lenite(y, sr, c):
    """① 子音弱化(弱化而非削除):子音幀的倒頻譜包絡往前後最近的母音幀包絡(依距離線性內插)靠
    c × LEN_W,保留子音本身的細部(雜訊)與原相位;先還原為原幀能量,再整幀衰減 c × C_ATT dB
    (只在子音遮罩內,遮罩 CONS_MASK 平滑)。回 (處理後訊號, 子音遮罩)。"""
    from scipy.ndimage import uniform_filter1d
    S = _stft(y)
    mag = np.abs(S)
    _, _, cons, vow = _classify(mag, sr)
    m = uniform_filter1d(cons.astype(float), size=max(1, int(CONS_MASK * sr / HOP)), mode="nearest")
    if vow.sum() < 2:
        return y, np.zeros(mag.shape[1])
    E = _envelope(mag)
    idx = np.where(vow)[0]
    t = np.arange(mag.shape[1])
    k = np.searchsorted(idx, t)
    a = idx[np.maximum(k - 1, 0)]
    b = idx[np.minimum(k, len(idx) - 1)]
    w = np.where(b != a, (t - a) / np.where(b != a, b - a, 1), 0.0)
    w = np.clip(w, 0, 1)
    Ev = (1 - w)[None, :] * E[:, a] + w[None, :] * E[:, b]
    lw = c * LEN_W * m
    new = np.exp(np.log(mag + 1e-7) + lw[None, :] * (Ev - E))
    e0 = (mag ** 2).sum(0)
    e1 = (new ** 2).sum(0) + 1e-20
    new *= np.sqrt(e0 / e1)[None, :] * 10 ** (-(c * C_ATT) * m / 20)[None, :]
    return _rms_match(_istft(new * np.exp(1j * np.angle(S)), len(y)), y), m


def _smear_transitions(y, sr, c, m):
    """② 轉接處時間平滑:轉接區(子音遮罩 > 0.2 的幀,或比 150 ms 平滑能量低 VALLEY_DB 以上的非靜音能量谷,
    兩者再向前後各擴 TRANS_PAD)以權重 1 做 c × SM_S 的幅度譜移動平均,其他地方權重 SM_OUT;
    權重本身以 30 ms 平滑;相位用原音。"""
    from scipy.ndimage import binary_dilation, uniform_filter1d
    w = max(1, int(round(c * SM_S * sr / HOP)))
    if w <= 1:
        return y
    S = _stft(y)
    mag = np.abs(S)
    _, _, edb, active = _frame_info(mag, sr)
    sm = uniform_filter1d(edb, size=max(1, int(0.15 * sr / HOP)), mode="nearest")
    valley = (edb < sm - VALLEY_DB) & active
    zone = binary_dilation((m[:mag.shape[1]] > 0.2) | valley, iterations=max(1, int(TRANS_PAD * sr / HOP)))
    wt = uniform_filter1d(np.where(zone, 1.0, SM_OUT), size=max(1, int(0.03 * sr / HOP)), mode="nearest")
    Ms = uniform_filter1d(mag, size=w, axis=1, mode="nearest")
    new = mag * (1 - wt) + Ms * wt
    return _rms_match(_istft(new * np.exp(1j * np.angle(S)), len(y)), y)


def _neutral_vowel(y, sr, c):
    """③ 共振峰中性化(輔助):每幀包絡往母音幀的平均包絡靠 c × NEU_C,並保持每幀能量不變
    (不改變整體頻譜傾斜);靜音幀不動。"""
    S = _stft(y)
    mag = np.abs(S)
    _, active, _, vow = _classify(mag, sr)
    if not vow.any():
        return y
    env = _envelope(mag)
    gain = np.exp(c * NEU_C * (env[:, vow].mean(1, keepdims=True) - env))
    gain[:, ~active] = 1.0
    e0 = (mag ** 2).sum(0)
    e1 = ((mag * gain) ** 2).sum(0) + 1e-20
    gain *= np.sqrt(e0 / e1)[None, :]
    return _rms_match(_istft(S * gain, len(y)), y)


def _contrast(y, sr, c):
    """④ 音節對比降低:幀能量往 150 ms 平滑能量靠攏 c × 0.3(單幀 ±12 dB)。"""
    from scipy.ndimage import uniform_filter1d
    S = _stft(y)
    _, _, edb, active = _frame_info(np.abs(S), sr)
    sm = 10 * np.log10(uniform_filter1d(10 ** (edb / 10), size=max(1, int(0.15 * sr / HOP)), mode="nearest"))
    gdb = np.clip(c * CONTRAST * (sm - edb), -12, 12)
    gdb[~active] = 0
    return _rms_match(_istft(S * 10 ** (gdb / 20)[None, :], len(y)), y)


def _tilt_comp(z, y, sr):
    """⑤ 長期頻譜傾斜補償(取代 D7 v2 的低通):以原音 y 判定的母音幀,比較原音與處理後 z 的長期平均功率譜,
    於 1/3 八度平滑(60 Hz 以下以 60 Hz 計)後取差值,上下各限 TILT_MAX dB,作為全句的固定 EQ 套回;
    ⑥ 最後 RMS 對齊原音。"""
    Sy, Sz = _stft(y), _stft(z)
    my = np.abs(Sy)
    freqs, _, _, vow = _classify(my, sr)
    if vow.sum() < 3:
        return _rms_match(z, y)
    ly = (my[:, vow] ** 2).mean(1)
    lz = (np.abs(Sz[:, vow]) ** 2).mean(1)
    g = np.zeros_like(freqs)
    for i, f in enumerate(freqs):
        f = max(f, 60.0)
        sel = (freqs >= f / 2 ** (1 / 6)) & (freqs <= f * 2 ** (1 / 6))
        g[i] = 10 * np.log10(ly[sel].mean() + 1e-20) - 10 * np.log10(lz[sel].mean() + 1e-20)
    g = np.clip(g, -TILT_MAX, TILT_MAX)
    return _rms_match(_istft(Sz * (10 ** (g / 20))[:, None], len(z)), y)


def clarity(y, sr, p):
    """清晰度 p(0–100,修訂 1 方案 C):100 不處理(回傳輸入本身);c = 1 − p/100,依序
    ① 子音弱化 → ② 轉接處時間平滑 → ③ 共振峰中性化 → ④ 音節對比降低 → ⑤ 長期頻譜傾斜補償 → ⑥ RMS 對齊原音。
    不使用低通;輸出長度不變。"""
    if p >= 100 or len(y) < N_FFT:
        return y
    c = 1 - p / 100
    z, m = _lenite(y, sr, c)
    z = _smear_transitions(z, sr, c, m)
    z = _neutral_vowel(z, sr, c)
    z = _contrast(z, sr, c)
    return _tilt_comp(z, y, sr)


def word_clarity(base, y, sr, spans, chars):
    """字詞清晰度(D8):以 y(整句清晰度之前)估計字界,把各區段換成以該值處理整句後的對應樣本,
    邊緣 20 ms 交叉淡化;區段外維持 base(整句清晰度的結果)。"""
    if not spans:
        return base
    bounds = estimate_bounds(y, sr, chars)
    out = base.copy()
    half = max(1, int(WORD_XFADE * sr / 2))
    cache = {}
    for a, b, value in spans:
        if value not in cache:
            cache[value] = clarity(y, sr, value) if value < 100 else y
        z = cache[value]
        s, e = bounds[a], bounds[b]
        w = np.zeros(len(base))
        w[s:e] = 1.0
        for edge, up in ((s, True), (e, False)):
            lo, hi = max(0, edge - half), min(len(base), edge + half)
            if hi > lo:
                ramp = np.linspace(0, 1, hi - lo) if up else np.linspace(1, 0, hi - lo)
                w[lo:hi] = ramp
        out = out * (1 - w) + z * w
    return out


# --- 語調(D9~D11) ---

def pitch_curve(points, roundness, t):
    """折線 + 圓弧(D10):每個中間關鍵點以二次貝茲取代尖角,切入 / 切出距離 = 圓弧 × 相鄰線段長 ÷ 2;
    第一點之前與最後一點之後維持端點值。t 為 0–1 的相對位置(陣列)。"""
    r = roundness / 100
    pts = [np.array(p, float) for p in points]
    path = [pts[0]]
    for i in range(1, len(pts) - 1):
        p0, p1, p2 = pts[i - 1], pts[i], pts[i + 1]
        a = p1 + (p0 - p1) * r * 0.5
        b = p1 + (p2 - p1) * r * 0.5
        for s in np.linspace(0, 1, 24):
            path.append((1 - s) ** 2 * a + 2 * (1 - s) * s * p1 + s ** 2 * b)
    path.append(pts[-1])
    path = np.array(path)
    order = np.argsort(path[:, 0], kind="stable")
    return np.interp(t, path[order, 0], path[order, 1])


def _f0(y, sr):
    import librosa
    f0, vflag, _ = librosa.pyin(y, fmin=F0_MIN, fmax=F0_MAX, sr=sr, frame_length=1024, hop_length=256)
    return f0, vflag


def psola(y, sr, semitone_at):
    """TD-PSOLA:依 pyin 的 F0 放音高標記,合成端以「週期 ÷ 音高比」的間距重放 2 週期 Hann 窗(長度不變);
    無聲段每 5 ms 原樣重放。semitone_at(秒) → 半音。"""
    f0, vflag = _f0(y.astype(np.float32), sr)
    out = np.zeros(len(y) + sr // 10)
    unv = max(1, int(0.005 * sr))
    marks, t = [], 0
    while t < len(y):
        i = min(int(t / 256), len(f0) - 1)
        if vflag[i] and not np.isnan(f0[i]):
            per = max(2, int(sr / f0[i]))
            w = y[t:t + per]
            t = t + int(np.argmax(w)) if len(w) else t
            marks.append((t, per, True))
            t += per
        else:
            marks.append((t, unv, False))
            t += unv
    ts, k = 0.0, 0
    while ts < len(y) and k < len(marks):
        while k + 1 < len(marks) and marks[k + 1][0] <= ts:
            k += 1
        m, per, voiced = marks[k]
        if voiced:
            ratio = 2 ** (semitone_at(ts / sr) / 12)
            seg = y[max(0, m - per):m + per]
            s0 = int(ts) - (m - max(0, m - per))
            if s0 >= 0 and len(seg):
                out[s0:s0 + len(seg)] += seg * np.hanning(len(seg))
            ts += per / ratio
        else:
            seg = y[m:m + 2 * unv]
            if len(seg):
                out[int(ts):int(ts) + len(seg)] += seg * np.hanning(len(seg))
            ts += unv
    return out[:len(y)]


def apply_pitch(y, sr, pitch):
    dur = len(y) / sr
    if dur <= 0:
        return y
    pts, r = pitch["points"], pitch["roundness"]
    return _rms_match(psola(y, sr, lambda t: float(pitch_curve(pts, r, min(1.0, t / dur)))), y)


def shape_audio(y, sr, job):
    """D12 的順序:連音 → 清晰度(整句,再疊字詞)→ 語調。y 為單聲道 float64。"""
    z = connect(y, sr, job["connect"] / 100, job["chars"])
    if job["clarity"] < 100 or job["word_clarity"]:
        base = clarity(z, sr, job["clarity"])
        z = word_clarity(base, z, sr, job["word_clarity"], job["chars"])
    if job["pitch"] is not None:
        z = apply_pitch(z, sr, job["pitch"])
    return z


def cmd_shape(args, emit):
    import shutil
    jobs = load_shape_jobs(args.jobs, args.out)
    sf = None
    for n, job in enumerate(jobs, 1):
        final = os.path.join(args.out, f"{job['id']}.wav")
        tmp = os.path.join(args.out, f"{job['id']}.part.wav")
        try:
            if _is_default(job):
                shutil.copyfile(job["input"], tmp)
                seconds = _wav_seconds(tmp)
            else:
                if sf is None:
                    _load_numpy()
                    try:
                        import soundfile as sf
                    except Exception:
                        raise WorkerError("✗ 無法載入 soundfile(請確認以 tools/BreezyVoice 的 venv 執行)",
                                          3) from None
                y, sr = sf.read(job["input"], dtype="float64", always_2d=True)
                y = y.mean(axis=1)
                z = np.clip(shape_audio(y, sr, job), -1.0, 1.0)
                sf.write(tmp, z, sr, subtype="PCM_16", format="WAV")
                seconds = round(len(z) / sr, 3)
            os.replace(tmp, final)
        except WorkerError:
            raise
        except Exception as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise WorkerError(f"✗ 第 {n} 句({job['id']})後製失敗:{type(e).__name__}", 4) from None
        emit({"id": job["id"], "seconds": seconds})


def _isolate_stdout():
    """回傳只給 JSON 用的 stdout;之後任何人(含 C 擴充)寫到 fd 1 的內容都改送 stderr。"""
    sys.stdout.flush()
    real = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return real


def main(argv=None):
    # sys.path[0] 是 repo 根目錄(本檔所在);換成 cwd(tools/BreezyVoice),避免 repo 內同名模組遮蔽 BreezyVoice
    sys.path[0] = os.getcwd()
    real_out = _isolate_stdout()

    def emit(obj):
        real_out.write(json.dumps(obj, ensure_ascii=False) + "\n")
        real_out.flush()

    parser = _ArgParser(prog="breezy_worker", add_help=False)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_synth = sub.add_parser("synth", add_help=False)
    p_synth.add_argument("--jobs", required=True)
    p_synth.add_argument("--out", required=True)
    p_ann = sub.add_parser("annotate", add_help=False)
    p_ann.add_argument("--input", required=True)
    p_shape = sub.add_parser("shape", add_help=False)
    p_shape.add_argument("--jobs", required=True)
    p_shape.add_argument("--out", required=True)
    try:
        args = parser.parse_args(argv)
        {"synth": cmd_synth, "annotate": cmd_annotate, "shape": cmd_shape}[args.cmd](args, emit)
    except WorkerError as e:
        print(str(e), file=sys.stderr)
        return e.code
    return 0


if __name__ == "__main__":
    sys.exit(main())
