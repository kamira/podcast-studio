"""breezy_worker.py 真模型整合測試(CHG-20261022-04 T1)。

不列入 `./scripts/check.sh`:`tests/conftest.py` 只在命令列明確指定 `tests/integration/` 下的路徑時才收集。
需要本機已建好的 `tools/BreezyVoice/.venv` 與 Hugging Face 模型快取;worker 以 `taiwan_ai.BREEZY_ENV`
離線執行(HF_HUB_OFFLINE=1、TRANSFORMERS_OFFLINE=1)。CI 沒有 BreezyVoice,缺 venv 時整檔略過。

BreezyVoice venv 沒有安裝 pytest,所以測試本身以 check.sh 同一組套件的環境執行,只有 worker 用 BreezyVoice venv:
  uv run --no-project --python '>=3.10' --with pytest --with opencc-python-reimplemented \\
      python -m pytest -s -p no:cacheprovider tests/integration/test_breezy_worker_real.py
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

import taiwan_ai as tai

WORKER = Path(__file__).resolve().parents[2] / "breezy_worker.py"
PY = tai.BREEZY / ".venv/bin/python"

pytestmark = pytest.mark.skipif(not PY.exists(), reason="本機沒有 tools/BreezyVoice/.venv")

SENTENCES = [{"id": "0000000a", "text": "大家好,歡迎收聽今天的節目。"},
             {"id": "0000000b", "text": "今天天氣真好[:ㄏㄠˇ]。"}]


def run_worker(*args, timeout):
    t0 = time.perf_counter()
    r = subprocess.run([str(PY), str(WORKER), *args], cwd=tai.BREEZY, env=dict(tai.BREEZY_ENV),
                       capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    return r, time.perf_counter() - t0


def test_兩句真模型逐句合成(tmp_path):
    voice = tai.role_config("voice")
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps({"prompt_wav": str(tai.ROOT / voice["wav"]), "prompt_text": voice["text"],
                                "jobs": SENTENCES}, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    # 整批逾時 = 句數 × 120 秒,另加模型載入的寬限
    r, elapsed = run_worker("synth", "--jobs", str(jobs), "--out", str(out),
                            timeout=len(SENTENCES) * 120 + 300)
    assert r.returncode == 0, r.stderr[-2000:]
    rows = [json.loads(line) for line in r.stdout.splitlines()]
    assert [row["id"] for row in rows] == [s["id"] for s in SENTENCES]
    for s, row in zip(SENTENCES, rows):
        assert (out / f"{s['id']}.wav").stat().st_size > 44
        assert row["seconds"] > 0.3
    assert json.loads((out / "result.json").read_text(encoding="utf-8"))["results"] == rows
    print(f"\n[timing] synth 兩句總耗時 {elapsed:.1f} 秒;各句長度 {[row['seconds'] for row in rows]} 秒")


def test_annotate真G2PW(tmp_path):
    inp = tmp_path / "annotate.json"
    inp.write_text(json.dumps({"text": "今天天氣真好"}, ensure_ascii=False), encoding="utf-8")
    r, elapsed = run_worker("annotate", "--input", str(inp), timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    zhuyin = json.loads(r.stdout)
    assert len(zhuyin) == 6 and all(isinstance(z, str) and z for z in zhuyin)
    print(f"\n[timing] annotate 耗時 {elapsed:.1f} 秒;結果 {zhuyin}")
