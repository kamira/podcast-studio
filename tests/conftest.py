"""pytest 共用設定:把 repo 根目錄放進 sys.path,讓測試可直接 import 根目錄的模組。

`tests/integration/` 是需要本機真模型的整合測試(CHG-20261022-04 T1),只在命令列明確指定該目錄或其下的
檔案時才收集;`./scripts/check.sh`(跑 `tests/`)與 CI 一律不收集。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

INTEGRATION = ROOT / "tests" / "integration"


def _under(path, base):
    return path == base or base in path.parents


def pytest_ignore_collect(collection_path, config):
    path = Path(collection_path).resolve()
    if not _under(path, INTEGRATION):
        return None
    cwd = Path(config.invocation_params.dir)
    for arg in config.args:
        target = (cwd / str(arg).split("::")[0]).resolve()
        if _under(target, INTEGRATION):
            return None
    return True
