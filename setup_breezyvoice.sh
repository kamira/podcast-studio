#!/bin/sh
# 在 macOS (Apple Silicon) 重建 BreezyVoice:clone 官方 repo,套用 patches/breezyvoice-mac 的調整。
# 需要 uv(https://docs.astral.sh/uv/)。
set -eu

ROOT=$(cd "$(dirname "$0")" && pwd)
DEST="$ROOT/tools/BreezyVoice"
PATCH="$ROOT/patches/breezyvoice-mac"

if [ ! -f "$DEST/single_inference.py" ]; then
  git clone --depth 1 https://github.com/mtkresearch/BreezyVoice.git "$DEST"
fi

cd "$DEST"
uv venv --allow-existing --python 3.10 .venv
# 官方 requirements 是 Linux/CUDA 版;這份移除了 CUDA、deepspeed、ttsfrd、WeTextProcessing(pynini 無 macOS ARM wheel)
uv pip install --python .venv/bin/python --build-constraints "$PATCH/build-constraints.txt" -r "$PATCH/requirements-mac.txt"

# 以簡易數字正規化模組取代 WeTextProcessing
SITE=$(.venv/bin/python -c 'import site; print(site.getsitepackages()[0])')
rm -rf "$SITE/tn"
cp -R "$PATCH/tn" "$SITE/tn"

.venv/bin/python -c 'from tn.chinese.normalizer import Normalizer; print(Normalizer().normalize("2026年共12345人"))'
echo "BreezyVoice 已就緒:$DEST"
