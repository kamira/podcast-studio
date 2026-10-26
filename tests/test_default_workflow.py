# -*- coding: utf-8 -*-
"""預設範本與舊入口退場的測試(CHG-20261022-01 T13)。

- `workflows/default.json` 是 executor 格式的流程圖,重現舊版 Gradio 四步流程,
  並以頂層 `steps` 定義「建立 Podcast」步驟精靈的預設步驟(① 文稿 ② 語音 ③ 封面 ④ 剪輯 ⑤ 匯出)。
  executor.validate 只讀 `nodes`、`edges`,`steps` 與節點的 `pos` 不影響驗證。
- 舊入口 `podcast_studio.py` 已刪除;`.claude/launch.json` 改啟動 `studio_server.py`。
- 設定資料庫與 UI 檢查截圖不入 git。
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import executor
import nodes
import studio_server as ss

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "workflows" / "default.json"
REQUIRED_KINDS = {"write-script", "taiwanize", "speak", "cover-prompt",
                  "generate-image", "add-title", "make-video"}
STEP_LABELS = ["文稿", "語音", "封面", "剪輯", "匯出"]


@pytest.fixture(scope="module")
def graph() -> dict:
    return json.loads(DEFAULT.read_text(encoding="utf-8"))


def _edges(graph) -> set[tuple[str, str, str, str]]:
    return {(e["from"]["node"], e["from"]["port"], e["to"]["node"], e["to"]["port"])
            for e in graph["edges"]}


def _ids_by_kind(graph) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for n in graph["nodes"]:
        out.setdefault(n["kind"], []).append(n["id"])
    return out


# ---------- workflows/default.json ----------

def test_default_validates_without_errors(graph):
    assert executor.validate(graph) == []


def test_default_covers_old_four_step_flow(graph):
    kinds = {n["kind"] for n in graph["nodes"]}
    assert REQUIRED_KINDS <= kinds
    assert kinds <= set(nodes.REGISTRY)


def test_default_edges_reproduce_old_flow(graph):
    by = _ids_by_kind(graph)
    one = {k: v[0] for k, v in by.items() if len(v) == 1}
    for kind in REQUIRED_KINDS:
        assert kind in one, f"{kind} 應只有一個節點"
    edges = _edges(graph)
    # 主題 → 文案 → 台灣用語 → 語音
    assert any(s in by["text-input"] and d == one["write-script"] for s, _, d, _ in edges)
    assert (one["write-script"], "text", one["taiwanize"], "text") in edges
    assert (one["taiwanize"], "text", one["speak"], "text") in edges
    # 標題 → 封面 prompt → 生圖 → 疊標題
    assert (one["cover-prompt"], "text", one["generate-image"], "prompt") in edges
    assert (one["generate-image"], "image", one["add-title"], "image") in edges
    titles = {s for s, _, d, p in edges if d == one["cover-prompt"] and p == "title"}
    assert titles and titles <= set(by["text-input"])
    assert {s for s, _, d, p in edges if d == one["add-title"] and p == "title"} == titles
    # 封面 + 語音 + 文案 → 影片
    assert (one["add-title"], "image", one["make-video"], "image") in edges
    assert (one["speak"], "audio", one["make-video"], "audio") in edges
    assert (one["taiwanize"], "text", one["make-video"], "script") in edges


def test_default_nodes_have_string_ids_and_positions(graph):
    for n in graph["nodes"]:
        assert isinstance(n["id"], str) and n["id"]
        pos = n.get("pos")
        assert isinstance(pos, dict), n["id"]
        for axis in ("x", "y"):
            v = pos.get(axis)
            assert isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v), n["id"]
    positions = [(n["pos"]["x"], n["pos"]["y"]) for n in graph["nodes"]]
    assert len(set(positions)) == len(positions), "節點座標不可重疊"


def test_default_wizard_steps(graph):
    steps = graph.get("steps")
    assert isinstance(steps, list)
    assert [s["label"] for s in steps] == STEP_LABELS
    step_ids = [s["id"] for s in steps]
    assert all(isinstance(i, str) and i for i in step_ids)
    assert len(set(step_ids)) == len(step_ids)
    node_ids = {n["id"] for n in graph["nodes"]}
    listed: list[str] = []
    for s in steps:
        assert isinstance(s["nodes"], list)
        listed.extend(s["nodes"])
    assert set(listed) <= node_ids, "步驟引用了不存在的節點"
    assert len(listed) == len(set(listed)), "同一節點不可屬於兩個步驟"
    assert set(listed) == node_ids, "每個節點都要屬於某個步驟"
    by_label = {s["label"]: s for s in steps}
    kind = {n["id"]: n["kind"] for n in graph["nodes"]}
    assert {kind[i] for i in by_label["文稿"]["nodes"]} >= {"write-script", "taiwanize"}
    assert {kind[i] for i in by_label["語音"]["nodes"]} == {"speak"}
    assert {kind[i] for i in by_label["封面"]["nodes"]} >= {"cover-prompt", "generate-image", "add-title"}
    assert by_label["剪輯"]["nodes"] == []   # 時間軸剪輯屬 CHG-20261022-04
    assert by_label["剪輯"].get("note")
    assert {kind[i] for i in by_label["匯出"]["nodes"]} == {"make-video"}


def test_default_served_by_workflow_api(graph):
    """前端進入「設定 › 進階流程」時以 GET /api/workflows/default 載入。"""
    client = TestClient(ss.create_app(root=ROOT), base_url="http://127.0.0.1:7860")
    r = client.get("/api/workflows/default")
    assert r.status_code == 200
    assert r.json() == {"name": "default", "graph": graph}


# ---------- 舊入口退場 ----------

def test_gradio_entry_removed():
    assert not (ROOT / "podcast_studio.py").exists()


def test_launch_json_starts_studio_server():
    cfg = json.loads((ROOT / ".claude" / "launch.json").read_text(encoding="utf-8"))
    (conf,) = cfg["configurations"]
    assert conf["runtimeArgs"] == ["run", "--script", "studio_server.py"]
    assert conf["port"] == ss.PORT == 7860
    assert Path(conf["runtimeExecutable"]).name == "uv"


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(),
                    reason="不在 git 工作副本中")
def test_user_data_is_git_ignored():
    paths = ["config/studio.db", "config/studio.db-wal", "outputs/ui_check/x.png"]
    r = subprocess.run(["git", "check-ignore", *paths], cwd=ROOT,
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == paths
