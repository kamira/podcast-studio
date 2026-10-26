// Podcast 工作室前端(CHG-20261022-01 T10):首頁 ⚙ 入口、設定頁子頁切換、「設定 › 進階流程」節點畫布。
//
// 約束(CHG Global Constraints):
//   - 原生 JS,不經建置;唯一第三方檔是 /static/vendor/drawflow.*;不從 CDN 載入任何資源。
//   - CSP `default-src 'self'`:不動態執行字串程式碼、不用行內腳本;樣式只用 class 與 CSSOM。
//   - 使用者或模型產生的文字一律用 textContent / value 顯示,不以 HTML 字串插入 DOM。
//   - 所有請求帶 X-Studio-Token(取自 <meta name="studio-token">)。
//   - Drawflow 的數字 id 送到 API 前一律轉成字串。
//
// 檔案拖放與工作流程存檔 / 載入對話框屬 T11,設定頁表單屬 T12;這裡只留位置。
"use strict";

(function () {
  // 埠型別:必須與 nodes.PORT_TYPES / GET /api/nodes 的 port_types 一致(tests/test_static.py 檢查)
  const PORT_TYPES = ["audio", "image", "text", "video"];
  const PORT_LABELS = { text: "文字", image: "圖片", audio: "音訊", video: "影片" };

  // 節點清單分組;不在表內的種類列在「其他」
  const PALETTE_GROUPS = [
    ["輸入", ["text-input", "file-input"]],
    ["文字", ["write-script", "revise", "taiwanize", "cover-prompt"]],
    ["媒體", ["speak", "generate-image", "add-title", "make-video"]],
  ];

  // 輸出埠由參數決定的節點:file-input 只有 kind 對應的一個輸出埠(同 nodes.py dynamic_outputs)
  const DYNAMIC_OUTPUT_PARAM = { "file-input": "kind" };

  const STATUS_LABELS = {
    idle: "待執行",
    queued: "排隊中",
    running: "執行中",
    waiting: "等待其他工作",
    done: "完成",
    error: "失敗",
    skipped: "略過",
    cancelled: "已取消",
  };

  const TOKEN = (document.querySelector('meta[name="studio-token"]') || {}).content || "";
  const SUBPAGES = ["models", "seats", "voice", "flow"];
  const NODE_TEMPLATE = "studio-node";
  const LONG_TEXT = 200;          // 字串參數上限超過此值改用多行輸入框
  const TOAST_MS = 6000;

  // ---------- 共用 ----------

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = String(text);
    return e;
  }

  async function api(method, url, body) {
    const headers = { "X-Studio-Token": TOKEN };
    const opts = { method, headers, credentials: "same-origin", cache: "no-store" };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(url, opts);
    } catch (e) {
      return { ok: false, status: 0, data: null };
    }
    let data = null;
    try {
      data = await res.json();
    } catch (e) {
      data = null;
    }
    return { ok: res.ok, status: res.status, data };
  }

  function errorText(r) {
    const d = r.data || {};
    if (typeof d.error === "string") return d.error;
    if (Array.isArray(d.errors) && d.errors.length) {
      const first = d.errors[0];
      return typeof first === "string" ? first : String((first && first.message) || "");
    }
    return r.status === 0 ? "無法連線到伺服器" : "請求失敗(HTTP " + r.status + ")";
  }

  let toastTimer = null;
  function toast(message, isError) {
    const t = document.getElementById("toast");
    t.textContent = String(message);
    t.classList.toggle("toast-error", !!isError);
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.hidden = true; }, TOAST_MS);
  }

  function setStatusBar(message) {
    const s = document.getElementById("statusbar");
    if (s) s.textContent = String(message);
  }

  // SSE 事件裡的輸出檔路徑(相對於 repo,例 outputs/cover-….png)→ /files/ URL;不合規則回 null
  function fileUrl(rel) {
    if (typeof rel !== "string" || !rel.startsWith("outputs/") || rel.indexOf("\\") !== -1) return null;
    const parts = rel.split("/");
    if (parts.length < 2 || parts.some(function (p) { return !p || p.charAt(0) === "."; })) return null;
    return "/files/" + parts.map(encodeURIComponent).join("/");
  }

  // ---------- 頁面切換(hash 路由,不是網頁分頁) ----------

  function route() {
    const h = location.hash || "#/";
    const m = /^#\/settings(?:\/([a-z]+))?\/?$/.exec(h);
    const home = document.getElementById("page-home");
    const settings = document.getElementById("page-settings");
    if (!m) {
      home.hidden = false;
      settings.hidden = true;
      return;
    }
    const sub = SUBPAGES.indexOf(m[1]) !== -1 ? m[1] : "models";
    home.hidden = true;
    settings.hidden = false;
    document.querySelectorAll(".subpage").forEach(function (p) { p.hidden = p.dataset.sub !== sub; });
    document.querySelectorAll(".subnav-link").forEach(function (a) {
      if (a.dataset.sub === sub) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    if (sub === "flow") Flow.open();
  }

  // ---------- 節點畫布 ----------

  const Flow = {
    editor: null,
    specs: {},             // kind → spec(GET /api/nodes)
    nodes: new Map(),      // Drawflow id(字串)→ 節點狀態
    runId: null,
    source: null,
    started: false,
    addOffset: 0,

    async open() {
      if (this.started) return;
      this.started = true;
      if (typeof window.Drawflow !== "function") {
        setStatusBar("節點編輯器載入失敗(static/vendor/drawflow.min.js)");
        return;
      }
      const r = await api("GET", "/api/nodes");
      if (!r.ok || !r.data || !Array.isArray(r.data.nodes)) {
        this.started = false;
        setStatusBar("無法取得節點清單:" + errorText(r));
        return;
      }
      const serverTypes = Array.isArray(r.data.port_types) ? r.data.port_types.slice().sort() : [];
      if (serverTypes.join(",") !== PORT_TYPES.join(",")) {
        toast("前端與伺服器的埠型別不一致,請重新整理頁面", true);
      }
      r.data.nodes.forEach((s) => { if (s && typeof s.kind === "string") this.specs[s.kind] = s; });
      this.initEditor();
      this.buildPalette();
      document.getElementById("btn-run").addEventListener("click", () => this.run());
      document.getElementById("btn-stop").addEventListener("click", () => this.stop());
      await this.loadDefault();
    },

    initEditor() {
      const container = document.getElementById("drawflow");
      const editor = new window.Drawflow(container);
      editor.reroute = false;
      editor.draggable_inputs = false;   // 在節點內的輸入框打字或選字時不拖動節點
      editor.start();
      const tpl = el("div", "node-body");
      editor.registerNode(NODE_TEMPLATE, tpl);
      editor.on("connectionCreated", (c) => this.onConnection(c));
      editor.on("nodeRemoved", (id) => { this.nodes.delete(String(id)); });
      this.editor = editor;

      // 從節點清單拖進畫布(檔案拖放屬 T11,這裡只接受節點種類)
      container.addEventListener("dragover", (e) => {
        if (e.dataTransfer && Array.from(e.dataTransfer.types).indexOf("application/x-studio-node") !== -1) {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
          container.classList.add("drop-target");
        }
      });
      container.addEventListener("dragleave", () => container.classList.remove("drop-target"));
      container.addEventListener("drop", (e) => {
        container.classList.remove("drop-target");
        const kind = e.dataTransfer ? e.dataTransfer.getData("application/x-studio-node") : "";
        if (!kind || !this.specs[kind]) return;
        e.preventDefault();
        const p = this.canvasPoint(e.clientX, e.clientY);
        this.addNode(kind, p.x, p.y);
      });
    },

    canvasPoint(clientX, clientY) {
      const rect = this.editor.precanvas.getBoundingClientRect();
      const zoom = this.editor.zoom || 1;
      return { x: (clientX - rect.left) / zoom, y: (clientY - rect.top) / zoom };
    },

    buildPalette() {
      const root = document.getElementById("palette-groups");
      root.textContent = "";
      const listed = new Set();
      const groups = PALETTE_GROUPS.map(function (g) { return [g[0], g[1].slice()]; });
      const others = Object.keys(this.specs).filter(function (k) {
        return !PALETTE_GROUPS.some(function (g) { return g[1].indexOf(k) !== -1; });
      });
      if (others.length) groups.push(["其他", others]);
      groups.forEach(([title, kinds]) => {
        const available = kinds.filter((k) => this.specs[k] && !listed.has(k));
        if (!available.length) return;
        root.appendChild(el("h3", "palette-group-title", "── " + title + " ──"));
        available.forEach((kind) => {
          listed.add(kind);
          const b = el("button", "palette-item", this.specs[kind].label || kind);
          b.type = "button";
          b.draggable = true;
          b.dataset.kind = kind;
          b.title = "拖到畫布,或點一下加入";
          b.addEventListener("dragstart", (e) => {
            e.dataTransfer.setData("application/x-studio-node", kind);
            e.dataTransfer.effectAllowed = "copy";
          });
          b.addEventListener("click", () => {
            const rect = this.editor.container.getBoundingClientRect();
            const p = this.canvasPoint(rect.left + 40 + this.addOffset, rect.top + 40 + this.addOffset);
            this.addOffset = (this.addOffset + 30) % 240;
            this.addNode(kind, p.x, p.y);
          });
          root.appendChild(b);
        });
      });
    },

    // 節點的輸入 / 輸出埠清單(依宣告順序;對應 Drawflow 的 input_1… / output_1…)
    inPortsOf(spec) {
      return Object.keys(spec.inputs || {}).map(function (name) {
        const p = spec.inputs[name];
        return { name: name, type: p.type, label: p.label || name, required: p.required !== false };
      });
    },

    outPortsOf(spec, params) {
      const dyn = DYNAMIC_OUTPUT_PARAM[spec.kind];
      return Object.keys(spec.outputs || {})
        .filter(function (name) { return !dyn || name === params[dyn]; })
        .map(function (name) {
          const p = spec.outputs[name];
          return { name: name, type: p.type, label: p.label || name };
        });
    },

    addNode(kind, x, y, init) {
      const spec = this.specs[kind];
      if (!spec) return null;
      init = init || {};
      const params = {};
      Object.keys(spec.params || {}).forEach(function (name) {
        const d = spec.params[name].default;
        params[name] = d === null || d === undefined ? "" : d;
      });
      const given = init.params && typeof init.params === "object" ? init.params : {};
      Object.keys(given).forEach(function (name) {
        if (Object.prototype.hasOwnProperty.call(params, name)) params[name] = given[name];
      });
      const inPorts = this.inPortsOf(spec);
      const outPorts = this.outPortsOf(spec, params);
      const id = String(this.editor.addNode(kind, inPorts.length, outPorts.length,
        Math.round(x), Math.round(y), "studio-node", {}, NODE_TEMPLATE, true));
      const st = {
        id: id, kind: kind, spec: spec, params: params, inPorts: inPorts, outPorts: outPorts,
        pinned: false, values: {}, refs: {},
      };
      this.nodes.set(id, st);
      this.renderNode(st);
      if (init.pinned === true && init.outputs && typeof init.outputs === "object") {
        const vals = {};
        outPorts.forEach(function (p) {
          if (typeof init.outputs[p.name] === "string") vals[p.name] = init.outputs[p.name];
        });
        this.showOutputs(st, vals);
        if (this.canPin(st) && Object.keys(vals).length === outPorts.length) this.setPinned(st, true);
      }
      return st;
    },

    nodeEl(st) {
      return document.getElementById("node-" + st.id);
    },

    // 埠的顏色與提示(加在 Drawflow 的 class 之後,不動前兩個 class)
    decoratePorts(st) {
      const node = this.nodeEl(st);
      if (!node) return;
      const paint = function (elems, ports) {
        elems.forEach(function (e, i) {
          PORT_TYPES.forEach(function (t) { e.classList.remove("port-" + t); });
          const p = ports[i];
          if (!p) return;
          e.classList.add("port-" + p.type);
          e.title = p.label + "(" + (PORT_LABELS[p.type] || p.type) + ")";
        });
      };
      paint(Array.from(node.querySelectorAll(".inputs > .input")), st.inPorts);
      paint(Array.from(node.querySelectorAll(".outputs > .output")), st.outPorts);
    },

    renderNode(st) {
      const node = this.nodeEl(st);
      const body = node.querySelector(".node-body");
      body.textContent = "";
      const spec = st.spec;

      const head = el("div", "node-head");
      head.appendChild(el("span", "node-title", spec.label || st.kind));
      const status = el("span", "node-status st-idle", STATUS_LABELS.idle);
      head.appendChild(status);
      const remove = el("button", "node-remove", "✕");
      remove.type = "button";
      remove.title = "移除節點";
      remove.setAttribute("aria-label", "移除節點");
      remove.addEventListener("click", (e) => {
        e.stopPropagation();
        this.editor.removeNodeId("node-" + st.id);
      });
      head.appendChild(remove);
      body.appendChild(head);

      const ports = el("div", "node-ports");
      body.appendChild(ports);
      st.refs.ports = ports;
      this.renderPortHint(st);

      const form = el("div", "node-params");
      Object.keys(spec.params || {}).forEach((name) => {
        form.appendChild(this.paramControl(st, name, spec.params[name]));
      });
      body.appendChild(form);

      const message = el("div", "node-message");
      message.hidden = true;
      body.appendChild(message);

      const output = el("div", "node-output");
      output.hidden = true;
      body.appendChild(output);

      const pin = el("div", "pin-bar");
      pin.hidden = true;
      pin.appendChild(el("span", "", "📌 已釘住(執行時不重跑,下游用這段文字)"));
      const unpin = el("button", "btn btn-small", "解除釘住");
      unpin.type = "button";
      unpin.addEventListener("click", () => this.setPinned(st, false));
      pin.appendChild(unpin);
      body.appendChild(pin);

      st.refs.status = status;
      st.refs.message = message;
      st.refs.output = output;
      st.refs.pin = pin;
      this.decoratePorts(st);
    },

    renderPortHint(st) {
      const box = st.refs.ports;
      box.textContent = "";
      const fmt = function (p, optional) {
        return p.label + "(" + (PORT_LABELS[p.type] || p.type) + (optional ? ",選填" : "") + ")";
      };
      if (st.inPorts.length) {
        box.appendChild(el("div", "", "輸入:" + st.inPorts.map(function (p) { return fmt(p, !p.required); }).join("、")));
      }
      if (st.outPorts.length) {
        box.appendChild(el("div", "", "輸出:" + st.outPorts.map(function (p) { return fmt(p, false); }).join("、")));
      }
    },

    paramControl(st, name, p) {
      const wrap = el("label", "param");
      const label = el("span", "param-label", p.label || name);
      let input;
      const value = st.params[name];
      if (p.type === "bool") {
        wrap.className = "param param-check";
        input = document.createElement("input");
        input.type = "checkbox";
        input.checked = value === true;
        input.addEventListener("change", () => { st.params[name] = input.checked; });
        wrap.appendChild(input);
        wrap.appendChild(label);
        return wrap;
      }
      if (p.type === "enum") {
        input = document.createElement("select");
        (p.choices || []).forEach(function (c) {
          const o = el("option", "", c);
          o.value = String(c);
          input.appendChild(o);
        });
        input.value = String(value);
        input.addEventListener("change", () => {
          const choice = (p.choices || []).find(function (c) { return String(c) === input.value; });
          st.params[name] = choice === undefined ? input.value : choice;
          if (DYNAMIC_OUTPUT_PARAM[st.kind] === name) this.refreshOutputs(st);
        });
      } else if (p.type === "int" || p.type === "float") {
        input = document.createElement("input");
        input.type = "number";
        if (typeof p.minimum === "number") input.min = String(p.minimum);
        if (typeof p.maximum === "number") input.max = String(p.maximum);
        input.step = typeof p.multiple_of === "number" ? String(p.multiple_of) : (p.type === "int" ? "1" : "any");
        input.value = value === "" ? "" : String(value);
        input.addEventListener("input", () => {
          const v = input.value.trim();
          const n = Number(v);
          st.params[name] = v === "" || !Number.isFinite(n) ? p.default : n;
        });
      } else {
        const long = typeof p.max_len !== "number" || p.max_len > LONG_TEXT;
        input = document.createElement(long ? "textarea" : "input");
        if (!long) input.type = "text";
        if (long) input.rows = 2;
        if (typeof p.max_len === "number") input.maxLength = p.max_len;
        input.value = typeof value === "string" ? value : "";
        input.addEventListener("input", () => { st.params[name] = input.value; });
      }
      wrap.appendChild(label);
      wrap.appendChild(input);
      return wrap;
    },

    // file-input 改變檔案種類:輸出埠型別跟著換,原有連線移除
    refreshOutputs(st) {
      const next = this.outPortsOf(st.spec, st.params);
      const data = this.editor.drawflow.drawflow[this.editor.module].data[st.id];
      let removed = 0;
      if (data) {
        Object.keys(data.outputs).forEach((cls) => {
          data.outputs[cls].connections.slice().forEach((c) => {
            this.editor.removeSingleConnection(st.id, c.node, cls, c.output);
            removed += 1;
          });
        });
      }
      st.outPorts = next;
      this.renderPortHint(st);
      this.decoratePorts(st);
      this.clearOutputs(st);
      if (removed) toast("檔案種類已改變,原有的 " + removed + " 條連線已移除");
    },

    portIndex(cls) {
      const m = /_(\d+)$/.exec(String(cls));
      return m ? parseInt(m[1], 10) - 1 : -1;
    },

    // 連線型別檢查:不符即移除連線並提示(伺服器執行前會再檢查一次)
    onConnection(c) {
      const out = this.nodes.get(String(c.output_id));
      const inn = this.nodes.get(String(c.input_id));
      const op = out && out.outPorts[this.portIndex(c.output_class)];
      const ip = inn && inn.inPorts[this.portIndex(c.input_class)];
      const drop = (message) => {
        this.editor.removeSingleConnection(c.output_id, c.input_id, c.output_class, c.input_class);
        if (message) toast(message, true);
      };
      if (!op || !ip) {
        drop("無法辨識這條連線的埠");
        return;
      }
      if (op.type !== ip.type) {
        drop("型別不符:" + op.type + " 不能接到 " + ip.type);
        return;
      }
      const data = this.editor.drawflow.drawflow[this.editor.module].data[String(c.input_id)];
      const conns = data && data.inputs[c.input_class] ? data.inputs[c.input_class].connections : [];
      if (conns.length > 1) {
        drop("同一個輸入只能接一條線");
        return;
      }
      const sel = ".connection.node_in_node-" + c.input_id + ".node_out_node-" + c.output_id +
        "." + c.output_class + "." + c.input_class;
      const svg = this.editor.container.querySelector(sel);
      if (svg) svg.classList.add("type-" + op.type);
    },

    canPin(st) {
      return st.outPorts.length > 0 && st.outPorts.every(function (p) { return p.type === "text"; });
    },

    setPinned(st, on) {
      st.pinned = !!on && this.canPin(st);
      st.refs.pin.hidden = !st.pinned;
      const node = this.nodeEl(st);
      if (node) node.classList.toggle("is-pinned", st.pinned);
    },

    clearOutputs(st) {
      st.values = {};
      st.refs.output.textContent = "";
      st.refs.output.hidden = true;
      this.setPinned(st, false);
    },

    // 顯示節點輸出:文字可編輯(編輯後釘住);圖、音訊、影片以 /files/ 預覽
    showOutputs(st, outputs) {
      const box = st.refs.output;
      box.textContent = "";
      st.values = {};
      st.outPorts.forEach((p) => {
        if (!Object.prototype.hasOwnProperty.call(outputs, p.name)) return;
        const value = outputs[p.name];
        st.values[p.name] = value;
        if (st.outPorts.length > 1) box.appendChild(el("div", "output-label", p.label));
        if (p.type === "text") {
          const ta = document.createElement("textarea");
          ta.value = typeof value === "string" ? value : String(value);
          ta.setAttribute("aria-label", (st.spec.label || st.kind) + " 的輸出文字");
          ta.disabled = !this.canPin(st);
          ta.addEventListener("input", () => {
            st.values[p.name] = ta.value;
            this.setPinned(st, true);
          });
          box.appendChild(ta);
          return;
        }
        const url = fileUrl(value);
        if (!url) {
          box.appendChild(el("div", "output-label", "無法預覽:" + String(value)));
          return;
        }
        let media;
        if (p.type === "image") {
          media = document.createElement("img");
          media.alt = p.label;
        } else {
          media = document.createElement(p.type === "video" ? "video" : "audio");
          media.controls = true;
          media.preload = "metadata";
        }
        media.src = url;
        box.appendChild(media);
        const link = el("a", "output-link", "開啟檔案");
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        box.appendChild(link);
      });
      box.hidden = !box.firstChild;
    },

    setNodeStatus(st, status, message, isInfo) {
      const s = st.refs.status;
      s.className = "node-status st-" + status;
      s.textContent = STATUS_LABELS[status] || status;
      const m = st.refs.message;
      if (message) {
        m.textContent = String(message);
        m.classList.toggle("info", !!isInfo);
        m.hidden = false;
      } else {
        m.textContent = "";
        m.hidden = true;
      }
      const node = this.nodeEl(st);
      if (node) node.classList.toggle("is-error", status === "error");
    },

    // 送給 POST /api/run 的流程圖(executor 格式;id 一律字串)
    buildGraph() {
      const data = this.editor.drawflow.drawflow[this.editor.module].data;
      const graph = { nodes: [], edges: [] };
      Object.keys(data).forEach((key) => {
        const st = this.nodes.get(String(key));
        if (!st) return;
        const n = data[key];
        const node = {
          id: String(key), kind: st.kind, params: Object.assign({}, st.params),
          pos: { x: Math.round(n.pos_x), y: Math.round(n.pos_y) },
        };
        if (st.pinned) {
          node.pinned = true;
          node.outputs = {};
          st.outPorts.forEach(function (p) {
            const v = st.values[p.name];
            node.outputs[p.name] = typeof v === "string" ? v : "";
          });
        }
        graph.nodes.push(node);
        Object.keys(n.outputs).forEach((cls) => {
          const op = st.outPorts[this.portIndex(cls)];
          n.outputs[cls].connections.forEach((c) => {
            const target = this.nodes.get(String(c.node));
            const ip = target && target.inPorts[this.portIndex(c.output)];
            if (!op || !ip) return;
            graph.edges.push({ from: { node: String(key), port: op.name }, to: { node: String(c.node), port: ip.name } });
          });
        });
      });
      return graph;
    },

    // 載入 executor 格式的流程圖(預設範本 workflows/default.json;存檔 / 載入對話框屬 T11)
    loadGraph(graph) {
      this.editor.clear();
      this.nodes.clear();
      const list = graph && Array.isArray(graph.nodes) ? graph.nodes : [];
      const edges = graph && Array.isArray(graph.edges) ? graph.edges : [];
      const pos = this.autoLayout(list, edges);
      const idMap = {};
      const unknown = [];
      list.forEach((n) => {
        if (!n || typeof n.id !== "string") return;
        if (!this.specs[n.kind]) {
          unknown.push(String(n.kind));
          return;
        }
        const p = n.pos && Number.isFinite(n.pos.x) && Number.isFinite(n.pos.y) ? n.pos : pos[n.id];
        const st = this.addNode(n.kind, p.x, p.y, n);
        if (st) idMap[n.id] = st;
      });
      edges.forEach((e) => {
        const from = e && e.from && idMap[e.from.node];
        const to = e && e.to && idMap[e.to.node];
        if (!from || !to) return;
        const oi = from.outPorts.findIndex(function (p) { return p.name === e.from.port; });
        const ii = to.inPorts.findIndex(function (p) { return p.name === e.to.port; });
        if (oi < 0 || ii < 0) return;
        this.editor.addConnection(from.id, to.id, "output_" + (oi + 1), "input_" + (ii + 1));
      });
      if (unknown.length) toast("略過未知的節點種類:" + unknown.join("、"), true);
    },

    // 沒有座標的節點依拓撲深度排成欄
    autoLayout(list, edges) {
      const depth = {};
      const ids = list.filter(function (n) { return n && typeof n.id === "string"; }).map(function (n) { return n.id; });
      ids.forEach(function (id) { depth[id] = 0; });
      for (let round = 0; round < ids.length; round += 1) {
        let changed = false;
        edges.forEach(function (e) {
          const a = e && e.from && e.from.node;
          const b = e && e.to && e.to.node;
          if (a in depth && b in depth && depth[b] < depth[a] + 1) {
            depth[b] = depth[a] + 1;
            changed = true;
          }
        });
        if (!changed) break;
      }
      const rows = {};
      const out = {};
      ids.forEach(function (id) {
        const d = Math.min(depth[id], ids.length);
        rows[d] = (rows[d] || 0) + 1;
        out[id] = { x: 40 + d * 300, y: 40 + (rows[d] - 1) * 260 };
      });
      return out;
    },

    async loadDefault() {
      const r = await api("GET", "/api/workflows/default");
      if (r.ok && r.data && r.data.graph) {
        this.loadGraph(r.data.graph);
        setStatusBar("已載入預設流程");
      } else if (r.status === 404) {
        setStatusBar("尚無預設流程;請從左側節點清單拖出節點");
      } else {
        setStatusBar("預設流程載入失敗:" + errorText(r));
      }
    },

    setRunning(on) {
      document.getElementById("btn-run").disabled = on;
      document.getElementById("btn-stop").disabled = !on;
    },

    async run() {
      if (this.runId) return;
      const graph = this.buildGraph();
      if (!graph.nodes.length) {
        toast("畫布上沒有節點", true);
        return;
      }
      this.nodes.forEach((st) => this.setNodeStatus(st, "idle"));
      this.setRunning(true);
      setStatusBar("送出流程…");
      const r = await api("POST", "/api/run", graph);
      if (!r.ok || !r.data || typeof r.data.run_id !== "string") {
        this.setRunning(false);
        if (r.status === 400 && r.data && Array.isArray(r.data.errors)) {
          this.showValidationErrors(r.data.errors);
        } else {
          setStatusBar("無法執行:" + errorText(r));
          toast("無法執行:" + errorText(r), true);
        }
        return;
      }
      this.runId = r.data.run_id;
      setStatusBar("執行中…");
      const es = new EventSource("/api/run/" + encodeURIComponent(this.runId) + "/events");
      this.source = es;
      es.onmessage = (m) => {
        let ev;
        try {
          ev = JSON.parse(m.data);
        } catch (e) {
          return;
        }
        this.onEvent(ev);
      };
      es.onerror = () => {
        // 瀏覽器會以 Last-Event-ID 自動重連;連線被關閉且無法重連時才結束
        if (es.readyState === EventSource.CLOSED && this.source === es) {
          this.finish("error", "與伺服器的連線中斷");
        }
      };
    },

    showValidationErrors(errors) {
      const general = [];
      errors.forEach((e) => {
        const msg = String((e && e.message) || "");
        const st = e && e.node_id !== null && e.node_id !== undefined ? this.nodes.get(String(e.node_id)) : null;
        if (st) {
          const prev = st.refs.message.hidden ? "" : st.refs.message.textContent + "\n";
          this.setNodeStatus(st, "error", prev + msg);
        } else {
          general.push(msg);
        }
      });
      const first = errors.length ? String(errors[0].message || "") : "";
      setStatusBar("流程圖有 " + errors.length + " 個問題,未執行");
      toast("流程圖有 " + errors.length + " 個問題:" + (general[0] || first), true);
    },

    onEvent(ev) {
      if (!ev || typeof ev !== "object") return;
      if (ev.type === "started") {
        (Array.isArray(ev.order) ? ev.order : []).forEach((id) => {
          const st = this.nodes.get(String(id));
          if (st) this.setNodeStatus(st, "queued");
        });
        return;
      }
      if (ev.type === "error") {
        toast("執行錯誤:" + String(ev.message || ""), true);
        return;
      }
      if (ev.type === "finished") {
        this.finish(ev.status);
        return;
      }
      if (ev.type !== "node") return;
      const st = this.nodes.get(String(ev.node));
      if (!st) return;
      const name = st.spec.label || st.kind;
      switch (ev.status) {
        case "running":
          this.setNodeStatus(st, "running");
          setStatusBar(name + " 執行中…");
          break;
        case "waiting":
          this.setNodeStatus(st, "waiting", ev.message || STATUS_LABELS.waiting, true);
          setStatusBar(name + " 執行中…(" + String(ev.message || STATUS_LABELS.waiting) + ")");
          break;
        case "done":
          this.setNodeStatus(st, "done");
          if (ev.outputs && typeof ev.outputs === "object") {
            this.setPinned(st, false);
            this.showOutputs(st, ev.outputs);
          }
          break;
        case "skipped":
          if (ev.reason === "pinned") {
            this.setNodeStatus(st, "skipped", "已釘住,沿用編輯過的文字", true);
          } else {
            this.setNodeStatus(st, "skipped", ev.message || "略過:上游失敗", true);
          }
          break;
        case "error":
          this.setNodeStatus(st, "error", ev.message || "執行失敗");
          break;
        case "cancelled":
          this.setNodeStatus(st, "cancelled", ev.message || STATUS_LABELS.cancelled, true);
          break;
        default:
          break;
      }
    },

    finish(status, message) {
      if (this.source) {
        this.source.close();
        this.source = null;
      }
      this.runId = null;
      this.setRunning(false);
      const text = message || (status === "ok" ? "執行完成" : status === "cancelled" ? "已停止" : "執行結束:有節點失敗");
      setStatusBar(text);
      if (status !== "ok") toast(text, status === "error");
    },

    async stop() {
      if (!this.runId) return;
      const r = await api("POST", "/api/run/" + encodeURIComponent(this.runId) + "/cancel");
      if (r.ok) {
        setStatusBar("已要求停止:進行中的節點跑完後停止");
      } else {
        toast("停止失敗:" + errorText(r), true);
      }
    },
  };

  // ---------- 啟動 ----------

  function start() {
    document.getElementById("open-settings").addEventListener("click", function () {
      location.hash = "#/settings";
    });
    window.addEventListener("hashchange", route);
    route();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
