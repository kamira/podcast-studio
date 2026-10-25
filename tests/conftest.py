"""pytest 共用設定:把 repo 根目錄放進 sys.path,讓測試可直接 import 根目錄的模組。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
