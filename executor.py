"""圖執行器(CHG-20261022-01 T6)。

把前端送來的流程圖(節點 + 連線)先驗證,再依拓撲順序逐一執行 `nodes.REGISTRY` 的節點。

流程圖格式(JSON,由 T7 的 `POST /api/run` 收下後原樣交給本模組):
  {
    "nodes": [
      {"id": "n1", "kind": "text-input", "params": {"text": "台灣夜市文化"}},
      {"id": "n2", "kind": "taiwanize", "params": {},
       "pinned": true, "outputs": {"text": "使用者改過的文字"}}      # 釘住的節點(選填)
    ],
    "edges": [
      {"from": {"node": "n1", "port": "text"}, "to": {"node": "n2", "port": "text"}}
    ]
  }
  - `id` 是非空字串且不重複;`params` 省略時用宣告的預設值,出現未宣告的參數算錯誤。
  - `pinned` 為 true 時必須帶 `outputs`,只能釘住「所有輸出埠都是 text」的節點,值一律是字串
    (≤ nodes.MAX_TEXT 字)——不接受使用者直接指定圖片 / 音訊 / 影片路徑,避免繞過 outputs/ 限制。

介面:
  Error(node_id, port, message)   驗證錯誤;port 在埠錯誤時是埠名、參數錯誤時是參數名、
                                  整張圖的錯誤為 None;`as_dict()` 供 HTTP 回應使用
  validate(graph) -> list[Error]  未知種類、型別不符、有環、必填未接、參數不合格(Param.check)、
                                  格式錯誤、節點數超過 MAX_NODES;合法回空清單,絕不丟例外
  GraphError(ValueError)          run() 收到不合法的圖時丟出,`.errors` 為 validate 的結果
  run(graph, emit, cancel=None)   依拓撲順序在呼叫端執行緒執行;emit(dict) 推送事件;
                                  cancel 為 threading.Event(或任何有 is_set() 的物件),
                                  設定後不再啟動新節點,進行中的節點跑完

事件(emit 收到的 dict,T7 以 SSE 原樣轉送):
  {"type": "started", "order": [節點 id…]}                     拓撲順序
  {"type": "node", "node": id, "status": "running"}
  {"type": "node", "node": id, "status": "waiting", "message": "等待其他工作"}   重型鎖被占用
  {"type": "node", "node": id, "status": "done", "outputs": {埠: 值}}
  {"type": "node", "node": id, "status": "error", "message": 錯誤訊息}
  {"type": "node", "node": id, "status": "skipped", "reason": "pinned", "outputs": {埠: 編輯值}}
  {"type": "node", "node": id, "status": "skipped", "reason": "upstream", "message": "略過:上游失敗"}
  {"type": "node", "node": id, "status": "cancelled", "message": "已取消"}
  {"type": "finished", "status": "ok" | "error" | "cancelled"}

約定:
  - 節點本身不取重型鎖;`heavy=True` 的節點由本模組包在 `heavy_lock.heavy_lock()` 內執行。
    等鎖時以短逾時重試,期間可被取消(取消時該節點不啟動)。
  - 節點失敗只影響直接或間接依賴它的下游;沒有依賴關係的旁支照跑。
  - 釘住的節點不呼叫 run,也不受上游失敗影響,下游拿到編輯值。
"""
from __future__ import annotations

import contextlib
from typing import Any, Callable, NamedTuple

import heavy_lock
import nodes

MAX_NODES = 100            # 單一工作流程節點數上限(Global Constraints)
LOCK_RETRY_SECONDS = 0.2   # 等待重型鎖時每隔多久檢查一次是否取消
UPSTREAM_FAILED = "略過:上游失敗"
WAITING = "等待其他工作"
CANCELLED = "已取消"


class Error(NamedTuple):
    node_id: str | None
    port: str | None
    message: str

    def as_dict(self) -> dict:
        return {"node_id": self.node_id, "port": self.port, "message": self.message}


class GraphError(ValueError):
    def __init__(self, errors: list[Error]):
        super().__init__("流程圖不合法:" + ";".join(e.message for e in errors))
        self.errors = errors


def _endpoint(v) -> tuple[str, str] | None:
    if not isinstance(v, dict):
        return None
    node, port = v.get("node"), v.get("port")
    if not (isinstance(node, str) and isinstance(port, str)):
        return None
    return node, port


def _parse(graph) -> tuple[list[dict], list[tuple[str, str, str, str]], list[Error]]:
    """檢查整體格式;回傳 (節點清單, 連線 (來源, 來源埠, 目標, 目標埠), 錯誤)。"""
    if not isinstance(graph, dict):
        return [], [], [Error(None, None, "流程圖必須是物件")]
    raw_nodes, raw_edges = graph.get("nodes"), graph.get("edges", [])
    if not isinstance(raw_nodes, list):
        return [], [], [Error(None, None, "nodes 必須是清單")]
    if not isinstance(raw_edges, list):
        return [], [], [Error(None, None, "edges 必須是清單")]
    if len(raw_nodes) > MAX_NODES:
        return [], [], [Error(None, None, f"節點數超過上限 {MAX_NODES} 個")]
    errors: list[Error] = []
    seen: set[str] = set()
    good: list[dict] = []
    for i, n in enumerate(raw_nodes):
        nid = n.get("id") if isinstance(n, dict) else None
        if not isinstance(nid, str) or not nid:
            errors.append(Error(None, None, f"第 {i + 1} 個節點缺少字串 id"))
            continue
        if nid in seen:
            errors.append(Error(nid, None, "節點 id 重複"))
            continue
        seen.add(nid)
        if not isinstance(n.get("params", {}), dict):
            errors.append(Error(nid, None, "params 必須是物件"))
            continue
        good.append(n)
    edges = []
    for i, e in enumerate(raw_edges):
        src = _endpoint(e.get("from")) if isinstance(e, dict) else None
        dst = _endpoint(e.get("to")) if isinstance(e, dict) else None
        if src is None or dst is None:
            errors.append(Error(None, None, f"第 {i + 1} 條連線格式不對"))
            continue
        missing = [x for x in (src[0], dst[0]) if x not in seen]
        if missing:
            errors.append(Error(missing[0], None, "連線指向不存在的節點"))
            continue
        edges.append((*src, *dst))
    return good, edges, errors


def _check_node(n: dict) -> list[Error]:
    """種類、參數、釘住值。"""
    nid, kind = n["id"], n.get("kind")
    spec = nodes.REGISTRY.get(kind) if isinstance(kind, str) else None
    if spec is None:
        return [Error(nid, None, f"未知的節點種類:{kind}")]
    errors = []
    params = n.get("params") or {}
    for name in params:
        if name not in spec.params:
            errors.append(Error(nid, str(name), f"未知的參數:{name}"))
    for name, p in spec.params.items():
        value = params.get(name, p.default)
        msg = p.check(value)
        if msg:
            errors.append(Error(nid, name, msg))
    if errors:
        return errors
    pinned = n.get("pinned", False)
    if not isinstance(pinned, bool):
        return [Error(nid, None, "pinned 必須是是/否")]
    if pinned:
        ports = spec.output_ports(params)
        outs = n.get("outputs")
        if any(p.type != "text" for p in ports.values()):
            return [Error(nid, None, "只有文字輸出的節點可以釘住")]
        if not isinstance(outs, dict) or set(outs) != set(ports):
            return [Error(nid, None, "釘住的節點必須提供每個輸出埠的編輯值")]
        for port, v in outs.items():
            if not isinstance(v, str):
                return [Error(nid, port, "編輯值必須是文字")]
            if len(v) > nodes.MAX_TEXT:
                return [Error(nid, port, f"編輯值超過 {nodes.MAX_TEXT} 字")]
    return []


def _toposort(ids: list[str], edges) -> tuple[list[str], list[str]]:
    """Kahn 演算法,同層依節點清單順序;回傳 (順序, 留在環上的節點)。"""
    indeg = {i: 0 for i in ids}
    succ: dict[str, list[str]] = {i: [] for i in ids}
    for s, _, d, _ in edges:
        succ[s].append(d)
        indeg[d] += 1
    pos = {i: k for k, i in enumerate(ids)}
    ready = [i for i in ids if indeg[i] == 0]
    order = []
    while ready:
        ready.sort(key=pos.__getitem__)
        cur = ready.pop(0)
        order.append(cur)
        for d in succ[cur]:
            indeg[d] -= 1
            if indeg[d] == 0:
                ready.append(d)
    return order, [i for i in ids if i not in set(order)]


def validate(graph) -> list[Error]:
    """回傳錯誤清單;合法回 []。只讀不寫,不呼叫任何節點。"""
    try:
        return _validate(graph)
    except Exception as e:  # 保險:任何意外格式都回錯誤,不讓伺服器 500
        return [Error(None, None, f"流程圖格式不對({type(e).__name__})")]


def _validate(graph) -> list[Error]:
    node_list, edges, errors = _parse(graph)
    valid: dict[str, Any] = {}       # 種類與參數皆合格的節點 → NodeSpec
    for n in node_list:
        errs = _check_node(n)
        errors.extend(errs)
        if not errs:
            valid[n["id"]] = nodes.REGISTRY[n["kind"]]
    by_id = {n["id"]: n for n in node_list}
    connected: dict[tuple[str, str], str] = {}
    attempted: set[tuple[str, str]] = set()   # 有連線但連線本身有錯的輸入,不再報「未連線」
    for s, sp, d, dp in edges:
        attempted.add((d, dp))
        if s not in valid or d not in valid:
            continue  # 已有節點層級的錯誤,不重複報
        out_ports = valid[s].output_ports(by_id[s].get("params") or {})
        in_ports = valid[d].inputs
        if sp not in out_ports:
            errors.append(Error(s, sp, f"沒有輸出埠 {sp}"))
            continue
        if dp not in in_ports:
            errors.append(Error(d, dp, f"沒有輸入埠 {dp}"))
            continue
        if out_ports[sp].type != in_ports[dp].type:
            errors.append(Error(d, dp, f"型別不符:{out_ports[sp].type} 不能接到 {in_ports[dp].type}"))
            continue
        if (d, dp) in connected:
            errors.append(Error(d, dp, "同一個輸入只能接一條線"))
            continue
        connected[(d, dp)] = s
    for nid, spec in valid.items():
        if by_id[nid].get("pinned"):
            continue  # 釘住的節點不執行,不需要輸入
        for port, p in spec.inputs.items():
            if p.required and (nid, port) not in attempted:
                errors.append(Error(nid, port, f"必填輸入「{p.label or port}」未連線"))
    _, cyclic = _toposort([n["id"] for n in node_list], edges)
    if cyclic:
        errors.append(Error(cyclic[0], None, "流程圖有環,無法決定執行順序"))
    return errors


def run(graph, emit: Callable[[dict], Any], cancel=None) -> None:
    """驗證後依拓撲順序執行;不合法丟 GraphError(不執行任何節點)。"""
    errors = validate(graph)
    if errors:
        raise GraphError(errors)
    node_list, edges, _ = _parse(graph)
    by_id = {n["id"]: n for n in node_list}
    order, _ = _toposort(list(by_id), edges)
    incoming: dict[str, list[tuple[str, str, str]]] = {i: [] for i in by_id}
    for s, sp, d, dp in edges:
        incoming[d].append((s, sp, dp))

    def cancelled() -> bool:
        return cancel is not None and cancel.is_set()

    results: dict[str, dict] = {}   # 成功或釘住節點的輸出
    failed: set[str] = set()        # 失敗、略過或取消的節點(下游一律略過)
    status = "ok"
    emit({"type": "started", "order": list(order)})
    for nid in order:
        n = by_id[nid]
        spec = nodes.REGISTRY[n["kind"]]
        if cancelled():
            emit({"type": "node", "node": nid, "status": "cancelled", "message": CANCELLED})
            failed.add(nid)
            status = "cancelled"
            continue
        if n.get("pinned"):
            results[nid] = dict(n["outputs"])
            emit({"type": "node", "node": nid, "status": "skipped", "reason": "pinned",
                  "outputs": dict(n["outputs"])})
            continue
        if any(s in failed for s, _, _ in incoming[nid]):
            emit({"type": "node", "node": nid, "status": "skipped", "reason": "upstream",
                  "message": UPSTREAM_FAILED})
            failed.add(nid)
            continue
        inputs = {dp: results[s][sp] for s, sp, dp in incoming[nid]}
        params = n.get("params") or {}
        with contextlib.ExitStack() as stack:
            if spec.heavy and not _acquire_heavy(stack, nid, emit, cancelled):
                emit({"type": "node", "node": nid, "status": "cancelled", "message": CANCELLED})
                failed.add(nid)
                status = "cancelled"
                continue
            emit({"type": "node", "node": nid, "status": "running"})
            try:
                out = spec.run(inputs, params)
                missing = [p for p in spec.output_ports(params) if not isinstance(out, dict) or p not in out]
                if missing:
                    raise RuntimeError(f"節點沒有產生輸出:{'、'.join(missing)}")
            except Exception as e:
                failed.add(nid)
                if status == "ok":
                    status = "error"
                emit({"type": "node", "node": nid, "status": "error", "message": _message(e)})
                continue
        ports = spec.output_ports(params)
        results[nid] = {p: out[p] for p in ports}
        emit({"type": "node", "node": nid, "status": "done", "outputs": dict(results[nid])})
    if status == "ok" and failed:
        status = "error"
    emit({"type": "finished", "status": status})


def _acquire_heavy(stack: contextlib.ExitStack, nid: str, emit, cancelled) -> bool:
    """取得重型鎖並登記到 stack;等待期間被取消回 False。鎖被占用時只推送一次 waiting。"""
    notified = False

    def on_wait():
        nonlocal notified
        if not notified:
            notified = True
            emit({"type": "node", "node": nid, "status": "waiting", "message": WAITING})

    while True:
        if cancelled():
            return False
        try:
            stack.enter_context(heavy_lock.heavy_lock(timeout=LOCK_RETRY_SECONDS, on_wait=on_wait))
            return True
        except TimeoutError:
            continue


def _message(e: Exception) -> str:
    msg = str(e).strip()
    return msg or type(e).__name__
