#!/usr/bin/env bash
# 測試載具(CHG-20261022-01 T1):以 uv 建立暫時環境執行 pytest。
#
# 用法:
#   ./scripts/check.sh                       跑 tests/ 全部(含 tests/ci/)
#   ./scripts/check.sh tests/test_settings.py 只跑指定的測試檔 / 目錄
#   ./scripts/check.sh -k load               其他 pytest 參數接在 tests/ 之後
#
# uv 的來源:
#   1. PATH 上的 uv,或 ~/.local/bin/uv
#   2. 找不到且 CI=true(GitHub Actions):以 python3 -m pip install uv==0.13.5 安裝後使用
#   3. 找不到且不在 CI:印出安裝說明,exit 2
#
# 寫入範圍:只寫 repo 內(且關閉 pytest 快取,不產生 .pytest_cache/)。
# 例外:pip 與 uv 自身的快取與資料目錄(套件快取、暫時環境、uv 管理的 Python
# 直譯器;本機在 ~/.cache/uv、~/.local/share/uv 一帶),以及 CI 中 pip 安裝 uv 的位置。
set -eu

cd "$(dirname "$0")/.."

UV=""
if command -v uv >/dev/null 2>&1; then
  UV="$(command -v uv)"
elif [ -x "${HOME:-}/.local/bin/uv" ]; then
  UV="${HOME}/.local/bin/uv"
elif [ "${CI:-}" = "true" ]; then
  echo "check.sh:CI 環境找不到 uv,以 pip 安裝 uv==0.13.5" >&2
  python3 -m pip install --quiet --disable-pip-version-check uv==0.13.5
  UV="$(python3 -c 'import uv; print(uv.find_uv_bin())')"
else
  cat >&2 <<'MSG'
check.sh:找不到 uv。請先安裝(擇一):
  curl -LsSf https://astral.sh/uv/install.sh | sh
  brew install uv
安裝後確認 `uv --version` 可執行,或 ~/.local/bin/uv 存在,再重跑本腳本。
MSG
  exit 2
fi

# 參數中有實際存在的測試路徑(可帶 ::節點)時只跑指定路徑;否則跑 tests/ 全部
has_path=0
for a in "$@"; do
  case "$a" in
    -*) ;;
    *) if [ -e "${a%%::*}" ]; then has_path=1; fi ;;
  esac
done
if [ "$has_path" = 1 ]; then
  set -- "$@"
else
  set -- tests/ "$@"
fi

exec "$UV" run --no-project --python '>=3.10' \
  --with pytest \
  --with httpx \
  --with fastapi \
  --with uvicorn \
  --with python-multipart \
  --with opencc-python-reimplemented \
  --with imageio-ffmpeg \
  --with 'pillow<13' \
  python -m pytest -q -p no:cacheprovider "$@"
