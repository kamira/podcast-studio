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
        raise WorkerError("✗ 參數不正確:synth 需要 --jobs 與 --out;annotate 需要 --input")


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
    try:
        args = parser.parse_args(argv)
        {"synth": cmd_synth, "annotate": cmd_annotate}[args.cmd](args, emit)
    except WorkerError as e:
        print(str(e), file=sys.stderr)
        return e.code
    return 0


if __name__ == "__main__":
    sys.exit(main())
