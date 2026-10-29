// Podcast 工作室前端(CHG-20261022-01 T10、T11、T12):首頁 ⚙ 入口、設定頁子頁切換、「設定 › 進階流程」節點畫布、
// 檔案拖放(T11)與工作流程存檔 / 載入對話框(T11)。
//
// 約束(CHG Global Constraints):
//   - 原生 JS,不經建置;唯一第三方檔是 /static/vendor/drawflow.*;不從 CDN 載入任何資源。
//   - CSP `default-src 'self'`:不動態執行字串程式碼、不用行內腳本;樣式只用 class 與 CSSOM。
//   - 使用者或模型產生的文字一律用 textContent / value 顯示,不以 HTML 字串插入 DOM。
//   - 所有請求帶 X-Studio-Token(取自 <meta name="studio-token">)。
//   - Drawflow 的數字 id 送到 API 前一律轉成字串。
//
// 設定頁表單(T12):模型、審議席位子頁、還原預設、儲存(422 逐欄顯示)。
//
// CHG-20261022-04 T7:本檔另提供共用工具 `window.Studio`(el、api、toast、uploadFile、watchRun…)與
// hash 路由登錄(`Studio.addRoute`、`Studio.pageHooks`),首頁、精靈、完整模式與剪輯畫面在
// project.js、timeline.js、wizard.js;畫布存檔(buildGraph)保留載入時的 steps(carrySteps,修訂 3 R3-2)。
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
    ["專案", ["send-to-library"]],
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

  // 上傳副檔名白名單 → 檔案種類(埠型別):必須與 studio_server.UPLOAD_KINDS(= nodes.FILE_KINDS)
  // 一致(tests/test_static.py 檢查)。前端先擋,伺服器仍會再檢查副檔名與內容。
  const UPLOAD_KINDS = {".m4a": "audio", ".md": "text", ".jpeg": "image", ".jpg": "image", ".mp3": "audio", ".png": "image", ".txt": "text", ".wav": "audio", ".webp": "image"};
  const MAX_UPLOAD_MB = 200;        // 同 studio_server.MAX_UPLOAD
  const MAX_UPLOAD_NAME = 255;      // 同 studio_server.MAX_UPLOAD_NAME

  // 工作流程名稱:與 studio_server._normalize_name 相同的中英日白名單(ASCII 英數、-、_、
  // CJK 統一漢字 U+4E00–9FFF 與擴充 A U+3400–4DBF、平假名 U+3041–3096、片假名 U+30A1–30FA、
  // 「ー」U+30FC、「々」U+3005),1~64 字,不做正規化(tests/test_static.py 比對兩邊結果)
  const WORKFLOW_NAME_RE = /^[A-Za-z0-9_\-一-鿿㐀-䶿ぁ-ゖァ-ヺー々]{1,64}$/;
  const WORKFLOW_NAME_HINT = "工作流程名稱只能用中文、日文假名、英文字母、數字、底線、連字號,長度 1 到 64 字";
  // 伺服器 409 訊息含此字串時表示「只差大小寫」的衝突:不提供覆寫,只顯示伺服器訊息
  const CASE_COLLISION_MARK = "只差大小寫";

  const TOKEN = (document.querySelector('meta[name="studio-token"]') || {}).content || "";
  const SUBPAGES = ["models", "seats", "voice", "flow", "fusion"];
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
    t.classList.toggle("is-error", !!isError);   // CHG-20261029-01 T9:錯誤提示的共通標記
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

  // ---------- 檔案(T11) ----------

  // 原始檔名的副檔名(小寫);規則同 studio_server._upload_ext:只看最後一段、主檔名不可空白
  function uploadExt(name) {
    if (typeof name !== "string" || !name || name.length > MAX_UPLOAD_NAME || name.indexOf("\u0000") !== -1) return null;
    const base = name.replace(/\\/g, "/").split("/").pop();
    const dot = base.lastIndexOf(".");
    if (dot <= 0) return null;
    const ext = "." + base.slice(dot + 1).toLowerCase();
    return Object.prototype.hasOwnProperty.call(UPLOAD_KINDS, ext) ? ext : null;
  }

  function supportedExts() {
    return Object.keys(UPLOAD_KINDS).sort().join(" ");
  }

  // 前端先擋:不支援的副檔名與超過上限的檔案不上傳。合格回 {kind},否則回 {error}
  function checkFile(file) {
    const ext = uploadExt(file && file.name);
    if (!ext) return { error: "不支援此檔案類型:「" + String(file && file.name) + "」(可用 " + supportedExts() + ")" };
    if (typeof file.size === "number" && file.size > MAX_UPLOAD_MB * 1024 * 1024) {
      return { error: "「" + file.name + "」超過 " + MAX_UPLOAD_MB + " MB 上限" };
    }
    return { kind: UPLOAD_KINDS[ext] };
  }

  function draggingFiles(e) {
    return !!(e.dataTransfer && Array.from(e.dataTransfer.types || []).indexOf("Files") !== -1);
  }

  // 拖曳經過時只拿得到 MIME 型別(拿不到檔名),僅用來預先標示可接受的輸入口;猜不到回 null
  function guessDragKind(e) {
    const items = e.dataTransfer && e.dataTransfer.items ? Array.from(e.dataTransfer.items) : [];
    let kind = null;
    for (const it of items) {
      if (it.kind !== "file") continue;
      const t = String(it.type || "");
      const k = t === "text/plain" || t === "text/markdown" ? "text"
        : /^image\/(png|jpeg|webp)$/.test(t) ? "image"
          : /^audio\//.test(t) ? "audio" : null;
      if (!k || (kind && kind !== k)) return null;
      kind = k;
    }
    return kind;
  }

  // 以原始位元組上傳(同 T8 的 POST /api/upload?name=);失敗回 null 並提示
  async function uploadFile(file) {
    const mb = (file.size / (1024 * 1024)).toFixed(1);
    setStatusBar("上傳中:「" + file.name + "」(" + mb + " MB)…");
    let res;
    try {
      res = await fetch("/api/upload?name=" + encodeURIComponent(file.name), {
        method: "POST", body: file, credentials: "same-origin", cache: "no-store",
        headers: { "X-Studio-Token": TOKEN },
      });
    } catch (e) {
      setStatusBar("上傳失敗:「" + file.name + "」");
      toast("上傳失敗:「" + file.name + "」:無法連線到伺服器", true);
      return null;
    }
    let data = null;
    try {
      data = await res.json();
    } catch (e) {
      data = null;
    }
    const r = { ok: res.ok, status: res.status, data: data };
    if (!r.ok || !data || typeof data.file_id !== "string" || PORT_TYPES.indexOf(data.kind) === -1) {
      setStatusBar("上傳失敗:「" + file.name + "」");
      toast("上傳失敗:「" + file.name + "」:" + (r.ok ? "伺服器回應格式不符" : errorText(r)), true);
      return null;
    }
    setStatusBar("已上傳:「" + file.name + "」");
    return data;
  }

  // ---------- 頁面切換(hash 路由,不是網頁分頁) ----------

  const FORM_SUBPAGES = ["models", "seats", "voice"];   // 共用「還原預設 / 儲存」與提示列的子頁(voice:T8)
  let currentPage = null;
  // 其他模組登錄的路由({re, page, enter(match, entering)})與頁面進出鉤子(page → {enter, leave})
  const extraRoutes = [];
  const pageHooks = {};

  function showPage(name) {
    document.querySelectorAll("body > section.page").forEach(function (sec) {
      sec.hidden = sec.dataset.page !== name;
    });
    const entering = currentPage !== name;
    if (entering && currentPage && pageHooks[currentPage] && pageHooks[currentPage].leave) {
      pageHooks[currentPage].leave();
    }
    currentPage = name;
    if (entering && pageHooks[name] && pageHooks[name].enter) pageHooks[name].enter();
    return entering;
  }

  function route() {
    const h = location.hash || "#/";
    if (/^#\/disclaimer\/?$/.test(h)) {
      showPage("disclaimer");
      return;
    }
    for (const r of extraRoutes) {
      const hit = r.re.exec(h);
      if (hit) {
        const entering = showPage(r.page);
        r.enter(hit, entering);
        return;
      }
    }
    const m = /^#\/settings(?:\/([a-z]+))?\/?$/.exec(h);
    if (!m) {
      showPage("home");
      return;
    }
    const sub = SUBPAGES.indexOf(m[1]) !== -1 ? m[1] : "models";
    // 由其他頁面進入設定頁時重新讀取設定與模型清單
    if (showPage("settings")) Settings.open();
    const formSub = FORM_SUBPAGES.indexOf(sub) !== -1;
    document.getElementById("cfg-notices").hidden = !formSub;
    document.getElementById("cfg-actions").hidden = !formSub;
    document.querySelectorAll(".subpage").forEach(function (p) { p.hidden = p.dataset.sub !== sub; });
    document.querySelectorAll(".subnav-link").forEach(function (a) {
      if (a.dataset.sub === sub) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    if (sub === "flow") Flow.open();
    if (sub === "fusion") FusionLib.open();
  }

  // ---------- 精靈步驟的保留規則(修訂 3 R3-2) ----------
  //
  // steps:載入時的 steps;present:{載入時的節點 id: 目前畫布上的節點 id}(只含仍在畫布上的節點)。
  // 回傳存檔用的 steps:各步驟的節點 id 換成目前的 id、已不在畫布上的移除;原本有節點、移除後變空的
  // 步驟整個刪除;原本就沒有節點的步驟(例:④ 剪輯)保留;格式不認得的步驟原樣保留(由精靈顯示錯誤)。
  // 載入時沒有 steps(不是陣列)回 undefined,存檔不帶 steps。
  function carrySteps(steps, present) {
    if (!Array.isArray(steps)) return undefined;
    const out = [];
    steps.forEach(function (step) {
      const copy = JSON.parse(JSON.stringify(step === undefined ? null : step));
      if (!step || typeof step !== "object" || !Array.isArray(step.nodes) || step.nodes.length === 0) {
        out.push(copy);
        return;
      }
      copy.nodes = step.nodes.filter(function (id) {
        return typeof id === "string" && Object.prototype.hasOwnProperty.call(present, id);
      }).map(function (id) { return present[id]; });
      if (copy.nodes.length) out.push(copy);
    });
    return out;
  }
  // carrySteps 結束

  // ---------- 節點畫布 ----------

  const Flow = {
    editor: null,
    specs: {},             // kind → spec(GET /api/nodes)
    nodes: new Map(),      // Drawflow id(字串)→ 節點狀態
    runId: null,
    source: null,
    started: false,
    addOffset: 0,
    loadedSteps: null,     // 載入時流程圖的 steps(精靈步驟;修訂 3 R3-2)

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
      Workflows.init();
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

      // 從節點清單拖進畫布;或從檔案總管拖檔案進來(T11)
      container.addEventListener("dragover", (e) => {
        if (draggingFiles(e)) {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
          container.classList.add("drop-target");
          this.markDropPorts(guessDragKind(e), e.target);
          return;
        }
        if (e.dataTransfer && Array.from(e.dataTransfer.types).indexOf("application/x-studio-node") !== -1) {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
          container.classList.add("drop-target");
        }
      });
      container.addEventListener("dragleave", (e) => {
        if (e.relatedTarget && container.contains(e.relatedTarget)) return;
        container.classList.remove("drop-target");
        this.clearDropPorts();
      });
      container.addEventListener("drop", (e) => {
        container.classList.remove("drop-target");
        this.clearDropPorts();
        if (draggingFiles(e)) {
          e.preventDefault();
          e.stopPropagation();
          this.onFileDrop(Array.from(e.dataTransfer.files || []), e.target, e.clientX, e.clientY);
          return;
        }
        const kind = e.dataTransfer ? e.dataTransfer.getData("application/x-studio-node") : "";
        if (!kind || !this.specs[kind]) return;
        e.preventDefault();
        const p = this.canvasPoint(e.clientX, e.clientY);
        this.addNode(kind, p.x, p.y);
      });
    },

    // ---------- 檔案拖放(T11) ----------

    // 由 DOM 元素找出節點輸入口:{st, index};不是輸入口回 null
    inputPortAt(target) {
      const portEl = target && target.closest ? target.closest(".drawflow-node .inputs > .input") : null;
      if (!portEl) return null;
      const nodeEl = portEl.closest(".drawflow-node");
      const st = nodeEl ? this.nodes.get(String(nodeEl.id).replace(/^node-/, "")) : null;
      const cls = Array.from(portEl.classList).find(function (c) { return /^input_\d+$/.test(c); });
      const index = cls ? this.portIndex(cls) : -1;
      if (!st || index < 0 || !st.inPorts[index]) return null;
      return { st: st, index: index, el: portEl };
    },

    // 拖檔經過:可接受的輸入口高亮、不相符的變灰(猜不到檔案種類時只標示游標下的輸入口)
    markDropPorts(kind, target) {
      const container = this.editor.container;
      if (this.dropKind !== kind || !this.dropMarked) {
        this.clearDropPorts();
        this.dropMarked = true;
        this.dropKind = kind;
        if (kind) {
          this.nodes.forEach((st) => {
            const node = this.nodeEl(st);
            if (!node) return;
            node.querySelectorAll(".inputs > .input").forEach(function (e, i) {
              const p = st.inPorts[i];
              if (p) e.classList.add(p.type === kind ? "port-accept" : "port-reject");
            });
          });
        }
      }
      const hit = this.inputPortAt(target);
      container.querySelectorAll(".port-hover").forEach(function (e) {
        if (!hit || e !== hit.el) e.classList.remove("port-hover");
      });
      if (hit) hit.el.classList.add("port-hover");
    },

    clearDropPorts() {
      this.dropMarked = false;
      this.dropKind = null;
      if (!this.editor) return;
      this.editor.container.querySelectorAll(".port-accept, .port-reject, .port-hover").forEach(function (e) {
        e.classList.remove("port-accept", "port-reject", "port-hover");
      });
    },

    async onFileDrop(files, target, clientX, clientY) {
      if (!files.length) return;
      const hit = this.inputPortAt(target);
      if (hit) {
        if (files.length > 1) {
          toast("拖到輸入口時一次只能放一個檔案", true);
          return;
        }
        await this.dropOnPort(files[0], hit);
        return;
      }
      if (target && target.closest && target.closest(".drawflow-node")) {
        toast("請把檔案拖到畫布空白處(新建檔案節點),或拖到節點左側的輸入口(取代該輸入)", true);
        return;
      }
      const p = this.canvasPoint(clientX, clientY);
      const problems = [];
      let placed = 0;
      for (const f of files) {
        const c = checkFile(f);
        if (c.error) {
          problems.push(c.error);
          continue;
        }
        const up = await uploadFile(f);
        if (!up) continue;
        const st = this.addFileNode(up, f.name, p.x + placed * 30, p.y + placed * 30);
        if (st) placed += 1;
      }
      if (problems.length) toast(problems.join("\n"), true);
      if (placed) setStatusBar("已加入 " + placed + " 個檔案節點");
    },

    addFileNode(up, name, x, y) {
      const st = this.addNode("file-input", x, y, { params: { file: up.file_id, kind: up.kind } });
      if (st) this.setNodeStatus(st, "idle", "來源檔案:" + name, true);
      return st;
    },

    // 拖到輸入口:型別相符才上傳,新建 file-input 節點接到這個輸入,原本的連線移除
    async dropOnPort(file, hit) {
      const st = hit.st;
      const port = st.inPorts[hit.index];
      const c = checkFile(file);
      if (c.error) {
        toast(c.error, true);
        return;
      }
      const nodeName = st.spec.label || st.kind;
      if (c.kind !== port.type) {
        toast("型別不符:「" + file.name + "」是" + (PORT_LABELS[c.kind] || c.kind) + "檔,「" + nodeName +
          "」的「" + port.label + "」輸入要" + (PORT_LABELS[port.type] || port.type) + ",輸入不變", true);
        return;
      }
      const up = await uploadFile(file);
      if (!up) return;
      if (up.kind !== port.type) {
        toast("型別不符:伺服器判定「" + file.name + "」是" + (PORT_LABELS[up.kind] || up.kind) + "檔,輸入不變", true);
        return;
      }
      if (!this.nodes.has(st.id)) {
        toast("目標節點已被移除,檔案改放到畫布上", true);
      }
      const data = this.editor.drawflow.drawflow[this.editor.module].data[st.id];
      const inCls = "input_" + (hit.index + 1);
      const x = data ? data.pos_x - 300 : 40;
      const y = data ? data.pos_y + hit.index * 40 : 40;
      const fresh = this.addFileNode(up, file.name, Math.max(0, x), Math.max(0, y));
      if (!fresh || !data || !this.nodes.has(st.id)) return;
      const conns = data.inputs[inCls] ? data.inputs[inCls].connections.slice() : [];
      conns.forEach((cn) => {
        this.editor.removeSingleConnection(cn.node, st.id, cn.input, inCls);
      });
      this.editor.addConnection(fresh.id, st.id, "output_1", inCls);
      toast("「" + nodeName + "」的「" + port.label + "」輸入已改用「" + file.name + "」");
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
      const present = {};    // 載入時的節點 id → 目前畫布上的 id(只含仍在畫布上的節點)
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
        if (typeof st.origId === "string") present[st.origId] = String(key);
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
      // 修訂 3(R3-2):保留載入時的 steps,只移除已不在畫布上的節點 id
      const steps = carrySteps(this.loadedSteps, present);
      if (steps !== undefined) graph.steps = steps;
      return graph;
    },

    // 載入 executor 格式的流程圖(預設範本 workflows/default.json,或載入對話框取得的流程)
    loadGraph(graph) {
      this.editor.clear();
      this.nodes.clear();
      // 精靈步驟(修訂 3):載入時的 steps 原樣保留,存檔時由 carrySteps 對應到目前畫布
      this.loadedSteps = graph && Array.isArray(graph.steps) ? JSON.parse(JSON.stringify(graph.steps)) : null;
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
        if (st) {
          st.origId = n.id;
          idMap[n.id] = st;
        }
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

    // 伺服器 400 回來的錯誤顯示在對應節點上;action 為「未執行」或「未存檔」
    showValidationErrors(errors, action) {
      action = action || "未執行";
      this.nodes.forEach((st) => this.setNodeStatus(st, "idle"));
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
      setStatusBar("流程圖有 " + errors.length + " 個問題," + action);
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

  // ---------- 工作流程存檔 / 載入對話框(T11) ----------
  //
  // 伺服器沒有列出流程的端點(T8 規格),載入時請使用者輸入名稱。
  // 存檔:名稱先以 WORKFLOW_NAME_RE 檢查;同名(409)先問「已有同名流程,要覆寫嗎?」,
  // 確認後才帶 overwrite:true 重送;只差大小寫的 409 只顯示伺服器訊息,不提供覆寫。

  const Workflows = {
    mode: "save",
    current: null,     // 目前畫布對應的流程名稱(載入或存檔成功後)
    busy: false,
    ready: false,

    $(id) {
      return document.getElementById(id);
    },

    init() {
      if (this.ready) return;
      this.ready = true;
      this.$("btn-save").addEventListener("click", () => this.open("save"));
      this.$("btn-load").addEventListener("click", () => this.open("load"));
      this.$("wf-form").addEventListener("submit", (e) => {
        e.preventDefault();
        this.submit(false);
      });
      this.$("wf-overwrite").addEventListener("click", () => this.submit(true));
      this.$("wf-cancel").addEventListener("click", () => this.close());
      this.$("wf-name").addEventListener("input", () => this.reset());
      ["btn-save", "btn-load"].forEach((id) => {
        const b = this.$(id);
        b.disabled = false;
        b.title = id === "btn-save" ? "把目前的流程存檔" : "依名稱載入已存的流程";
      });
    },

    open(mode) {
      if (Flow.runId) {
        toast("流程執行中,請等它結束或先停止", true);
        return;
      }
      this.mode = mode;
      const save = mode === "save";
      this.$("wf-dialog-title").textContent = save ? "存檔" : "載入流程";
      this.$("wf-submit").textContent = save ? "存檔" : "載入";
      const input = this.$("wf-name");
      input.value = save && this.current ? this.current : "";
      this.reset();
      const d = this.$("wf-dialog");
      if (typeof d.showModal === "function") {
        if (!d.open) d.showModal();
      } else {
        d.setAttribute("open", "");
      }
      input.focus();
      input.select();
    },

    close() {
      const d = this.$("wf-dialog");
      if (typeof d.close === "function" && d.open) d.close();
      else d.removeAttribute("open");
    },

    reset() {
      this.$("wf-error").hidden = true;
      this.$("wf-error").textContent = "";
      this.$("wf-confirm").hidden = true;
      this.$("wf-submit").disabled = false;
    },

    showError(message) {
      const e = this.$("wf-error");
      e.textContent = String(message);
      e.hidden = false;
      this.$("wf-confirm").hidden = true;
    },

    setBusy(on) {
      this.busy = on;
      ["wf-overwrite", "wf-name"].forEach((id) => { this.$(id).disabled = on; });
      // 正在詢問是否覆寫時,「存檔」維持停用,只能按「覆寫」或「取消」(或改名稱)
      this.$("wf-submit").disabled = on || !this.$("wf-confirm").hidden;
    },

    setCurrent(name) {
      this.current = name;
      this.$("flow-name").textContent = "流程:" + name;
    },

    async submit(overwrite) {
      if (this.busy) return;
      const name = this.$("wf-name").value;   // 不修剪、不正規化,與伺服器規則一致
      if (!WORKFLOW_NAME_RE.test(name)) {
        this.showError(WORKFLOW_NAME_HINT);
        return;
      }
      this.setBusy(true);
      try {
        if (this.mode === "save") await this.save(name, overwrite === true);
        else await this.load(name);
      } finally {
        this.setBusy(false);
      }
    },

    async save(name, overwrite) {
      const graph = Flow.buildGraph();
      if (!graph.nodes.length) {
        this.showError("畫布上沒有節點,無法存檔");
        return;
      }
      const r = await api("PUT", "/api/workflows/" + encodeURIComponent(name), { graph: graph, overwrite: overwrite });
      if (r.ok) {
        this.close();
        this.setCurrent(name);
        setStatusBar("已存檔:「" + name + "」");
        toast("已存檔:「" + name + "」");
        return;
      }
      if (r.status === 409) {
        const message = errorText(r);
        if (message.indexOf(CASE_COLLISION_MARK) !== -1 || overwrite) {
          this.showError(message);
          return;
        }
        this.$("wf-error").hidden = true;
        this.$("wf-confirm").hidden = false;
        this.$("wf-submit").disabled = true;
        this.$("wf-overwrite").focus();
        return;
      }
      if (r.status === 400 && r.data && Array.isArray(r.data.errors)) {
        const onNodes = r.data.errors.some(function (e) { return e && e.node_id !== null && e.node_id !== undefined; });
        if (onNodes) {
          this.close();
          Flow.showValidationErrors(r.data.errors, "未存檔");
          return;
        }
      }
      this.showError("存檔失敗:" + errorText(r));
    },

    async load(name) {
      const r = await api("GET", "/api/workflows/" + encodeURIComponent(name));
      if (r.ok && r.data && r.data.graph && typeof r.data.graph === "object") {
        this.close();
        Flow.loadGraph(r.data.graph);
        this.setCurrent(name);
        setStatusBar("已載入:「" + name + "」");
        return;
      }
      if (r.status === 404) {
        this.showError("找不到這個流程");
        return;
      }
      this.showError("載入失敗:" + errorText(r));
    },
  };

  // ---------- 設定頁表單(T12) ----------
  //
  // 資料來源:GET /api/config(目前設定 + _warnings + meta.imported_from / imported_at)、
  // GET /api/models(Ollama 清單、mflux / BreezyVoice 是否安裝、可選聲線檔)。
  // 儲存:PUT /api/config(帶 X-Studio-Token);422 逐欄顯示、503 表示 Ollama 未啟動。
  // 欄位一律以 data-field="roles.reason.model" 這類路徑對應 settings.DEFAULTS 的鍵。

  // 預設值:settings.DEFAULTS 的 JSON 副本(「還原預設」用;tests/test_static.py 檢查與 settings.DEFAULTS 相等)
  const SETTINGS_DEFAULTS = {"roles": {"reason": {"model": "hf.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M", "think": true, "num_ctx": 16384}, "reason_heavy": {"model": "hf.co/HauhauCS/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF:IQ3_M", "think": true, "num_ctx": 16384}, "taiwan": {"model": "hf.co/mradermacher/Llama-Breeze2-8B-Instruct-text-only-GGUF:Q4_K_M", "think": null, "num_ctx": 16384}, "image": {"model": "mflux-community/flux-1-schnell-mflux-q4", "steps": 4}, "voice": {"wav": "tools/BreezyVoice/data/example.wav", "text": "在密碼學中，加密是將明文資訊改變為難以讀取的密文內容，使之不可讀的方法。只有擁有解密方法的對象，經由解密過程，才能將密文還原為正常可讀的內容。"}}, "review_seats": {"risk": "hf.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M", "impact": "glm4:latest", "drift": "qwen3:8b", "compliance": "codex", "security": "codex", "consistency": "glm4:latest"}, "voice_defaults": {"speed": 1.15, "pause_comma": 0.0, "pause_sentence": 0.4, "pause_paragraph": 0.8}};

  // 審議席位規則:與 settings.validate 一致(CODEX、VETO_SEATS、SEATS、PAUSED_REVIEW_MODELS、
  // 否決席不得用 glm4:*);tests/test_static.py 比對常數,並在有 JS 執行環境時實跑 seatAllows
  const SEAT_RULES = {"codex": "codex", "veto_seats": ["risk", "security", "compliance"], "seats": ["risk", "impact", "drift", "compliance", "security", "consistency"], "paused": ["hf.co/HauhauCS/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF:IQ3_M"], "veto_banned_prefix": "glm4:"};

  // 數值欄位範圍:同 settings.NUM_CTX_RANGE、STEPS_RANGE、MAX_TEXT_LEN,以及語音預設的
  // SPEED_RANGE / SPEED_STEP、PAUSE_RANGE / PAUSE_STEP(CHG-20261022-04 T8;tests/test_static.py 檢查)
  const FIELD_LIMITS = {"num_ctx": [2048, 65536], "steps": [1, 8], "text_max": 20000, "name_max": 300, "speed": [0.5, 2.0], "speed_step": 0.05, "pause": [0.0, 5.0], "pause_step": 0.1};

  const OLLAMA_DOWN_TEXT = "Ollama 未啟動,無法儲存模型設定";
  const CODEX_LABEL = "Codex";

  // 審議席位下拉的唯一規則(seat 為席位名稱,model 為模型名稱或 SEAT_RULES.codex)
  function seatAllows(seat, model) {
    const veto = SEAT_RULES.veto_seats.indexOf(seat) !== -1;
    if (model === SEAT_RULES.codex) return veto;
    if (SEAT_RULES.paused.indexOf(model) !== -1) return false;
    if (veto && String(model).indexOf(SEAT_RULES.veto_banned_prefix) === 0) return false;
    return true;
  }
  // seatAllows 結束

  // 伺服器錯誤訊息「roles.reason.model:模型…」→ {field, message};沒有欄位前綴回 null
  function splitFieldError(text) {
    const s = String(text);
    const i = s.indexOf(":");
    if (i <= 0) return null;
    const key = s.slice(0, i);
    if (!/^[a-z_]+(\.[a-z_]+)+$/.test(key)) return null;
    return { field: key, message: s.slice(i + 1) };
  }

  function getPath(obj, path) {
    return path.split(".").reduce(function (o, k) {
      return o && typeof o === "object" && Object.prototype.hasOwnProperty.call(o, k) ? o[k] : undefined;
    }, obj);
  }

  function setPath(obj, path, value) {
    const keys = path.split(".");
    let o = obj;
    keys.slice(0, -1).forEach(function (k) {
      if (!o[k] || typeof o[k] !== "object") o[k] = {};
      o = o[k];
    });
    o[keys[keys.length - 1]] = value;
  }

  const Settings = {
    loaded: false,
    loading: false,
    saving: false,
    models: null,          // GET /api/models 的回應
    ollamaUp: false,

    $(id) { return document.getElementById(id); },

    fields() {
      return Array.from(document.querySelectorAll("#page-settings [data-field]"));
    },

    field(path) {
      return this.fields().find(function (f) { return f.dataset.field === path; }) || null;
    },

    // 第一次進入設定頁(或由別的頁面回來)時重新讀取
    async open() {
      if (this.loading) return;
      this.loading = true;
      this.$("cfg-notices").hidden = false;
      this.$("cfg-loading").hidden = false;
      this.$("cfg-save").disabled = true;
      const both = await Promise.all([api("GET", "/api/config"), api("GET", "/api/models")]);
      this.loading = false;
      this.$("cfg-loading").hidden = true;
      const cfg = both[0];
      const models = both[1];
      this.models = models.ok && models.data ? models.data : null;
      this.ollamaUp = !!(this.models && this.models.ollama && this.models.ollama.reachable === true);
      this.showTools();
      this.clearErrors();
      if (!cfg.ok || !cfg.data || typeof cfg.data !== "object") {
        this.showTopErrors(["讀取設定失敗:" + errorText(cfg)]);
        this.fill(SETTINGS_DEFAULTS);
        this.loaded = false;
        this.updateSaveState();
        return;
      }
      this.showNotices(cfg.data);
      this.fill(cfg.data);
      this.loaded = true;
      this.updateSaveState();
    },

    updateSaveState() {
      const down = this.$("cfg-ollama-down");
      down.textContent = OLLAMA_DOWN_TEXT;
      down.hidden = this.ollamaUp;
      this.$("cfg-save").disabled = !this.loaded || !this.ollamaUp || this.saving;
    },

    showTools() {
      const m = this.models || {};
      const mark = function (id, info, name) {
        const e = document.getElementById(id);
        const ok = !!(info && info.installed === true);
        e.textContent = ok ? "✔ " + name + " 已安裝" : "✘ " + name + " 未安裝";
        e.classList.toggle("tool-ok", ok);
        e.classList.toggle("tool-missing", !ok);
      };
      mark("cfg-mflux", m.mflux, "mflux");
      mark("cfg-breezy", m.breezyvoice, "BreezyVoice");
    },

    // _warnings 橫幅與「已匯入一次」提示
    showNotices(data) {
      const w = this.$("cfg-warnings");
      w.replaceChildren();
      const warnings = Array.isArray(data._warnings) ? data._warnings : [];
      warnings.forEach(function (t) { w.appendChild(el("div", "", "⚠ " + String(t))); });
      w.hidden = warnings.length === 0;
      const meta = data.meta && typeof data.meta === "object" ? data.meta : {};
      const imp = this.$("cfg-imported");
      if (typeof meta.imported_from === "string" && meta.imported_from) {
        const at = typeof meta.imported_at === "string" && meta.imported_at ? "(" + meta.imported_at + ")" : "";
        imp.textContent = "ⓘ 已從 " + meta.imported_from + " 匯入一次" + at + ",之後只讀設定資料庫";
        imp.hidden = false;
      } else {
        imp.textContent = "";
        imp.hidden = true;
      }
    },

    // 下拉選項:Ollama 清單(或聲線檔清單)+ 目前值(不在清單內時標註)
    options(f, current) {
      const kind = f.dataset.options;
      const m = this.models || {};
      const ollama = m.ollama && Array.isArray(m.ollama.models) ? m.ollama.models.filter(function (x) { return typeof x === "string"; }) : [];
      let list = [];
      if (kind === "ollama") {
        list = ollama.map(function (n) { return { value: n, label: n }; });
      } else if (kind === "voice") {
        const voices = Array.isArray(m.voices) ? m.voices.filter(function (x) { return typeof x === "string"; }) : [];
        list = voices.map(function (n) { return { value: n, label: n }; });
      } else if (kind === "seat") {
        const seat = f.dataset.field.split(".")[1];
        list = ollama.filter(function (n) { return seatAllows(seat, n); })
          .map(function (n) { return { value: n, label: n }; });
        if (seatAllows(seat, SEAT_RULES.codex)) list.unshift({ value: SEAT_RULES.codex, label: CODEX_LABEL });
      }
      if (typeof current === "string" && current && !list.some(function (o) { return o.value === current; })) {
        const missing = kind === "voice" ? "(找不到檔案)" : (this.ollamaUp ? "(未下載)" : "");
        const label = current === SEAT_RULES.codex ? CODEX_LABEL : current + missing;
        list.unshift({ value: current, label: label });
      }
      return list;
    },

    fill(data) {
      const self = this;
      this.fields().forEach(function (f) {
        const v = getPath(data, f.dataset.field);
        if (f.dataset.options) {
          f.replaceChildren();
          self.options(f, v).forEach(function (o) {
            const opt = el("option", "", o.label);
            opt.value = o.value;
            f.appendChild(opt);
          });
          f.value = typeof v === "string" ? v : "";
        } else if (f.dataset.kind === "think") {
          f.value = v === true ? "true" : v === false ? "false" : "null";
        } else {
          f.value = v === undefined || v === null ? "" : String(v);
        }
      });
    },

    collect() {
      const out = {};
      this.fields().forEach(function (f) {
        let v = f.value;
        if (f.dataset.kind === "think") v = v === "true" ? true : v === "false" ? false : null;
        // 整數欄位:不是整數就原樣送出,由伺服器回 422 指出欄位
        else if (f.dataset.kind === "int" && /^-?\d+$/.test(String(v).trim())) v = parseInt(String(v).trim(), 10);
        // 小數欄位(語音預設):不是數字就原樣送出,由伺服器回 422 指出欄位
        else if (f.dataset.kind === "num" && /^-?\d+(\.\d+)?$/.test(String(v).trim())) v = Number(String(v).trim());
        setPath(out, f.dataset.field, v);
      });
      return out;
    },

    clearErrors() {
      this.$("cfg-errors").replaceChildren();
      this.$("cfg-errors").hidden = true;
      document.querySelectorAll("#page-settings .field-error").forEach(function (e) { e.remove(); });
      this.fields().forEach(function (f) { f.removeAttribute("aria-invalid"); });
      document.querySelectorAll(".subnav-link").forEach(function (a) { a.classList.remove("has-error"); });
    },

    showTopErrors(list) {
      const box = this.$("cfg-errors");
      list.forEach(function (t) { box.appendChild(el("div", "", String(t))); });
      box.hidden = box.childElementCount === 0;
    },

    // 422:能對應到欄位的訊息放在該欄位下,其餘列在頂端
    showFieldErrors(errors) {
      const self = this;
      const top = [];
      errors.forEach(function (raw) {
        const text = typeof raw === "string" ? raw : String((raw && raw.message) || raw);
        const parsed = splitFieldError(text);
        const f = parsed ? self.field(parsed.field) : null;
        if (!f) {
          top.push(text);
          return;
        }
        f.setAttribute("aria-invalid", "true");
        const holder = f.closest(".field") || f.parentNode;
        holder.appendChild(el("span", "field-error", parsed.message));
        const sub = f.closest(".subpage");
        const link = sub && document.querySelector('.subnav-link[data-sub="' + sub.dataset.sub + '"]');
        if (link) link.classList.add("has-error");
      });
      const fieldCount = errors.length - top.length;
      if (fieldCount > 0) top.unshift("有 " + fieldCount + " 個欄位不合格,請看各欄位下方的說明(標紅的子頁)");
      this.showTopErrors(top);
    },

    reset() {
      this.clearErrors();
      this.fill(SETTINGS_DEFAULTS);
      toast("已填入預設值,按「儲存」後才會生效");
    },

    async save() {
      if (this.saving || !this.loaded) return;
      this.saving = true;
      this.updateSaveState();
      this.clearErrors();
      const r = await api("PUT", "/api/config", this.collect());
      this.saving = false;
      if (r.ok && r.data && r.data.saved === true) {
        if (r.data.config && typeof r.data.config === "object") {
          this.showNotices(r.data.config);
          this.fill(r.data.config);
        }
        this.updateSaveState();
        toast("設定已儲存");
        return;
      }
      if (r.status === 503) this.ollamaUp = false;
      this.updateSaveState();
      if (r.status === 422 && r.data && Array.isArray(r.data.errors)) {
        this.showFieldErrors(r.data.errors);
        toast("設定未儲存:有欄位不合格", true);
        return;
      }
      const extra = r.data && Array.isArray(r.data.errors) ? r.data.errors.map(String) : [];
      this.showTopErrors(["設定未儲存:" + errorText(r)].concat(extra.filter(function (t) { return t !== errorText(r); })));
      toast("設定未儲存", true);
    },

    init() {
      const self = this;
      this.$("cfg-save").addEventListener("click", function () { self.save(); });
      this.$("cfg-reset").addEventListener("click", function () { self.reset(); });
      // 數值範圍與長度上限取自 FIELD_LIMITS,不在 HTML 另寫一份(伺服器仍會再驗證)
      this.fields().forEach(function (f) {
        const leaf = f.dataset.field.split(".").pop();
        const range = FIELD_LIMITS[leaf];
        if (f.dataset.kind === "int" && Array.isArray(range)) {
          f.min = String(range[0]);
          f.max = String(range[1]);
        } else if (f.tagName === "TEXTAREA") {
          f.maxLength = FIELD_LIMITS.text_max;
        } else if (f.tagName === "INPUT" && f.type === "text") {
          f.maxLength = FIELD_LIMITS.name_max;
        }
      });
      // 否決席標記取自 SEAT_RULES,不在 HTML 另寫一份
      document.querySelectorAll("#page-settings .seat[data-seat]").forEach(function (s) {
        if (SEAT_RULES.veto_seats.indexOf(s.dataset.seat) === -1) return;
        const mark = el("span", "veto-mark", "⛔");
        mark.title = "否決席";
        s.querySelector(".field-label").appendChild(mark);
      });
      // 欄位一改就清掉該欄位的錯誤說明
      this.fields().forEach(function (f) {
        f.addEventListener("change", function () {
          f.removeAttribute("aria-invalid");
          const holder = f.closest(".field");
          if (holder) holder.querySelectorAll(".field-error").forEach(function (e) { e.remove(); });
        });
      });
    },
  };

  // ---------- 啟動 ----------

  // ---------- 共用工具(T7:給 project.js、timeline.js、wizard.js 使用) ----------

  // 訂閱 /api/run/{id}/events(executor 事件,或專案 synth / render 事件);每則事件呼叫 onEvent,
  // 收到 finished(或連線中斷且無法重連)時結束,回傳 finished 事件
  function watchRun(runId, onEvent) {
    return new Promise(function (resolve) {
      const es = new EventSource("/api/run/" + encodeURIComponent(runId) + "/events");
      let done = false;
      const end = function (ev) {
        if (done) return;
        done = true;
        es.close();
        resolve(ev);
      };
      es.onmessage = function (m) {
        let ev;
        try {
          ev = JSON.parse(m.data);
        } catch (e) {
          return;
        }
        if (!ev || typeof ev !== "object") return;
        if (onEvent) onEvent(ev);
        if (ev.type === "finished") end(ev);
      };
      es.onerror = function () {
        if (es.readyState === EventSource.CLOSED) end({ type: "finished", status: "error", message: "與伺服器的連線中斷" });
      };
    });
  }

  // ---------- 設定 › 合音詞庫(CHG-20261029-01 T9;D29~D32、D36) ----------
  //
  // 列表(新增、編輯注音、啟用、刪除自訂詞 / 還原預設詞)、匯出 JSON / CSV、匯入差異預覽與套用、匯入上限。
  // 匯入:檔案以 multipart 送 /import/preview(只在伺服器記憶體解析);一致時只顯示一行狀態、沒有確認按鈕;
  // 不一致時顯示警告列、圖例與「保留|類別|詞|合音注音|啟用」五欄差異表,儲存格只有值或「原值 → 新值」;
  // 被拒詞條以類別「拒絕」列出(灰底、沒有保留勾選框),原因只列在 #fusion-reject-reasons。
  // 跳出訊息只有三類:匯入失敗(409 / 寫入失敗)、格式錯誤(422)、超過上限(413,沿用伺服器訊息)。

  const FUSION_HOMOPHONES = { "這樣": "醬", "那樣": "釀", "知道": "造", "什麼": "啥", "我們": "穩" };
  const FUSION_KIND_LABEL = { add: "新增", update: "修改", disable: "停用", delete: "刪除", reject: "拒絕" };

  // 差異表的一列(純函式):回傳 {kind, word, keepable, keepDefault, cells: [{text, cls}](合音注音、啟用)}
  function fusionDiffRow(change) {
    const onOff = function (v) { return v ? "啟用" : "停用"; };
    const o = change.old, n = change.new;
    const cell = function (a, b, cls) {
      if (a === null || a === undefined) return { text: b, cls: "" };
      if (b === null || b === undefined || a === b) return { text: a, cls: "" };
      return { text: a + " → " + b, cls: cls };
    };
    let z, en;
    if (change.kind === "add") {
      z = { text: n.zhuyin, cls: "" };
      en = { text: onOff(n.enabled), cls: "" };
    } else if (change.kind === "delete") {
      z = { text: o.zhuyin, cls: "" };
      en = { text: onOff(o.enabled), cls: "" };
    } else {
      z = cell(o.zhuyin, n.zhuyin, "diff-upd");
      en = cell(onOff(o.enabled), onOff(n.enabled), change.kind === "disable" ? "diff-dis" : "diff-upd");
    }
    return { kind: change.kind, word: change.word, keepable: true,
             keepDefault: change.kind === "delete" || change.kind === "disable", cells: [z, en] };
  }

  // multipart/form-data 本文(以 Blob 自組邊界與欄位)
  async function postFile(url, file) {
    const boundary = "----studio" + Math.random().toString(16).slice(2) + Date.now().toString(16);
    const safeName = String(file.name || "fusions").replace(/["\r\n\\]/g, "_");
    const head = "--" + boundary + "\r\nContent-Disposition: form-data; name=\"file\"; filename=\""
      + safeName + "\"\r\nContent-Type: application/octet-stream\r\n\r\n";
    const body = new Blob([head, file, "\r\n--" + boundary + "--\r\n"]);
    let res;
    try {
      res = await fetch(url, { method: "POST", credentials: "same-origin", cache: "no-store", body: body,
        headers: { "X-Studio-Token": TOKEN, "Content-Type": "multipart/form-data; boundary=" + boundary } });
    } catch (e) {
      return { ok: false, status: 0, data: null };
    }
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    return { ok: res.ok, status: res.status, data: data };
  }

  const FusionLib = {
    inited: false,
    words: [],
    preview: null,

    $(id) { return document.getElementById(id); },

    init() {
      if (this.inited) return;
      this.inited = true;
      this.$("fusion-add").addEventListener("click", () => this.add());
      this.$("fusion-import").addEventListener("click", () => this.$("fusion-file").click());
      this.$("fusion-file").addEventListener("change", (e) => {
        const f = e.target.files && e.target.files[0];
        e.target.value = "";
        if (f) this.importFile(f);
      });
      this.$("fusion-limits-save").addEventListener("click", () => this.saveLimits());
      this.$("fusion-export-json").addEventListener("click", () => this.download("json"));
      this.$("fusion-export-csv").addEventListener("click", () => this.download("csv"));
    },

    // 匯出:同源 GET 下載(瀏覽器存檔,不經伺服器寫檔)
    download(fmt) {
      const a = document.createElement("a");
      a.href = "/api/settings/fusions/export?format=" + (fmt === "csv" ? "csv" : "json");
      a.download = "合音詞庫." + (fmt === "csv" ? "csv" : "json");
      document.body.appendChild(a);
      a.click();
      a.remove();
    },

    async open() {
      this.init();
      const [r, l] = await Promise.all([api("GET", "/api/settings/fusions"), api("GET", "/api/settings/fusions/limits")]);
      if (r.ok && r.data) {
        this.words = r.data.words || [];
        this.render();
      } else toast("無法讀取合音詞庫:" + errorText(r), true);
      if (l.ok && l.data) {
        this.$("fusion-max-kb").value = String(l.data.max_kb);
        this.$("fusion-max-rows").value = String(l.data.max_rows);
      }
    },

    render() {
      const body = this.$("fusion-rows");
      body.replaceChildren();
      this.words.forEach((w) => body.appendChild(this.row(w)));
      if (!this.words.length) {
        const tr = el("tr");
        const td = el("td", "muted", "詞庫是空的");
        td.colSpan = 6;
        tr.appendChild(td);
        body.appendChild(tr);
      }
    },

    row(w) {
      const tr = el("tr");
      tr.dataset.word = w.word;
      tr.appendChild(el("td", "fusion-word", w.word));
      const ztd = el("td");
      const z = document.createElement("input");
      z.type = "text";
      z.className = "fusion-zhuyin";
      z.maxLength = 6;
      z.value = w.zhuyin;
      z.setAttribute("aria-label", "「" + w.word + "」的合音注音");
      ztd.appendChild(z);
      tr.appendChild(ztd);
      tr.appendChild(el("td", "muted", FUSION_HOMOPHONES[w.word] || "—"));
      tr.appendChild(el("td", "", w.source === "default" ? "預設" : "自訂"));
      const etd = el("td");
      const en = document.createElement("input");
      en.type = "checkbox";
      en.checked = !!w.enabled;
      en.setAttribute("aria-label", "啟用「" + w.word + "」");
      en.addEventListener("change", () => this.save(w.word, z.value.trim(), en.checked));
      etd.appendChild(en);
      tr.appendChild(etd);
      const act = el("td", "fusion-actions");
      const save = el("button", "btn btn-small", "儲存");
      save.type = "button";
      save.addEventListener("click", () => this.save(w.word, z.value.trim(), en.checked));
      act.appendChild(save);
      if (w.source === "default") {
        const rs = el("button", "btn btn-small", "還原");
        rs.type = "button";
        rs.addEventListener("click", () => this.put(w.word, { restore: true }, "已還原「" + w.word + "」"));
        act.appendChild(rs);
      } else {
        const del = el("button", "btn btn-small", "刪除");
        del.type = "button";
        del.addEventListener("click", () => this.remove(w.word));
        act.appendChild(del);
      }
      tr.appendChild(act);
      return tr;
    },

    async put(word, body, okText) {
      const r = await api("PUT", "/api/settings/fusions/" + encodeURIComponent(word), body);
      if (!r.ok) {
        toast("無法儲存:" + errorText(r), true);
        return false;
      }
      this.words = r.data.words || [];
      this.render();
      toast(okText);
      return true;
    },

    save(word, zhuyin, enabled) {
      return this.put(word, { zhuyin: zhuyin, enabled: enabled }, "已儲存「" + word + "」");
    },

    async add() {
      const word = this.$("fusion-new-word").value.trim();
      const zhuyin = this.$("fusion-new-zhuyin").value.trim();
      if (!word || !zhuyin) {
        toast("請輸入詞與合音注音", true);
        return;
      }
      if (this.words.some(function (w) { return w.word === word; })
          && !window.confirm("「" + word + "」已在詞庫中,要覆寫嗎?")) return;
      if (await this.save(word, zhuyin, true)) {
        this.$("fusion-new-word").value = "";
        this.$("fusion-new-zhuyin").value = "";
      }
    },

    async remove(word) {
      if (!window.confirm("刪除自訂詞「" + word + "」?")) return;
      const r = await api("DELETE", "/api/settings/fusions/" + encodeURIComponent(word));
      if (!r.ok) {
        toast("無法刪除:" + errorText(r), true);
        return;
      }
      this.words = r.data.words || [];
      this.render();
      toast("已刪除「" + word + "」");
    },

    async saveLimits() {
      const kb = Number(this.$("fusion-max-kb").value);
      const rows = Number(this.$("fusion-max-rows").value);
      const r = await api("PUT", "/api/settings/fusions/limits", { max_kb: kb, max_rows: rows });
      if (!r.ok) {
        toast("無法儲存上限:" + errorText(r), true);
        return;
      }
      toast("匯入上限已儲存:" + r.data.max_kb + " KB、" + r.data.max_rows + " 筆");
    },

    async importFile(file) {
      const box = this.$("fusion-preview");
      const r = await postFile("/api/settings/fusions/import/preview", file);
      if (!r.ok) {
        // 跳出訊息三類:超過上限(413,伺服器訊息即「超過允許大小上限(N KB)」等)、格式錯誤(422)、其他匯入失敗
        if (r.status === 413) toast(errorText(r), true);
        else if (r.status === 422) toast("格式錯誤:" + errorText(r), true);
        else toast("匯入失敗:" + errorText(r), true);
        return;
      }
      this.preview = r.data;
      this.renderPreview(box, r.data);
    },

    renderPreview(box, p) {
      box.replaceChildren();
      box.hidden = false;
      const rejected = p.rejected || [];
      if (p.status === "same") {
        box.appendChild(el("p", "fusion-same", "與現有詞庫一致,無需更動"));
      } else {
        const counts = {};
        p.changes.forEach(function (c) { counts[c.kind] = (counts[c.kind] || 0) + 1; });
        const warn = el("p", "notice notice-warn fusion-warn", "⚠ 匯入會更動詞庫:" + ["add", "update", "disable", "delete"]
          .filter(function (k) { return counts[k]; })
          .map(function (k) { return FUSION_KIND_LABEL[k] + " " + counts[k] + " 筆"; }).join("、"));
        box.appendChild(warn);
        const legend = el("p", "fusion-legend");
        [["diff-add", "新增"], ["diff-upd", "修改"], ["diff-dis", "停用"], ["diff-del", "刪除"], ["diff-rej", "拒絕"]]
          .forEach(function (x) { legend.appendChild(el("span", "legend-chip " + x[0], x[1])); });
        box.appendChild(legend);
      }
      if (p.status !== "same" || rejected.length) box.appendChild(this.table(p.changes || [], rejected));
      if (rejected.length) {
        const rr = el("div", "notice notice-info fusion-reject-reasons");
        rr.id = "fusion-reject-reasons";
        rejected.forEach(function (x) { rr.appendChild(el("div", "reject-line", x.word + ":" + x.reason)); });
        box.appendChild(rr);
      }
      if (p.status !== "same") box.appendChild(this.actions(p));
    },

    table(changes, rejected) {
      const t = el("table", "fusion-diff");
      t.id = "fusion-diff";
      const head = el("tr");
      ["保留", "類別", "詞", "合音注音", "啟用"].forEach(function (h) { head.appendChild(el("th", "", h)); });
      const thead = el("thead");
      thead.appendChild(head);
      t.appendChild(thead);
      const tb = el("tbody");
      changes.forEach(function (c) {
        const d = fusionDiffRow(c);
        const tr = el("tr", "kind-" + d.kind + (d.kind === "add" ? " diff-add" : d.kind === "delete" ? " diff-del" : ""));
        tr.dataset.word = d.word;
        const k = el("td");
        const box = document.createElement("input");
        box.type = "checkbox";
        box.className = "fusion-keep";
        box.checked = d.keepDefault;
        box.dataset.word = d.word;
        box.setAttribute("aria-label", "保留「" + d.word + "」的現值");
        k.appendChild(box);
        tr.append(k, el("td", "fusion-kind", FUSION_KIND_LABEL[d.kind]), el("td", "", d.word));
        d.cells.forEach(function (c2) { tr.appendChild(el("td", c2.cls, c2.text)); });
        tb.appendChild(tr);
      });
      rejected.forEach(function (x) {
        const tr = el("tr", "kind-reject diff-rej");
        tr.dataset.word = x.word;
        tr.append(el("td"), el("td", "fusion-kind", "拒絕"), el("td", "", x.word), el("td"), el("td"));
        tb.appendChild(tr);
      });
      t.appendChild(tb);
      return t;
    },

    actions(p) {
      const wrap = el("div", "fusion-apply");
      const mk = function (value, label, checked) {
        const lab = el("label", "fusion-mode");
        const r = document.createElement("input");
        r.type = "radio";
        r.name = "fusion-mode";
        r.value = value;
        r.checked = checked;
        lab.append(r, el("span", "", label));
        return lab;
      };
      wrap.append(mk("all", "全部覆蓋", false), mk("custom", "自訂(勾選「保留」的不更新)", true));
      const go = el("button", "btn btn-primary", "");
      go.type = "button";
      go.id = "fusion-apply";
      const count = () => {
        const all = wrap.querySelector('input[value="all"]').checked;
        const kept = all ? 0 : document.querySelectorAll("#fusion-diff .fusion-keep:checked").length;
        go.textContent = "確認匯入(更動 " + (p.changes.length - kept) + " 筆)";
        document.querySelectorAll("#fusion-diff .fusion-keep").forEach(function (b) { b.disabled = all; });
      };
      this.$("fusion-preview").onchange = count;   // 模式切換與「保留」勾選(每次預覽重設,不累積監聽)
      go.addEventListener("click", () => this.apply(p, wrap.querySelector('input[value="all"]').checked));
      wrap.appendChild(go);
      setTimeout(count, 0);
      return wrap;
    },

    async apply(p, all) {
      const body = { preview_id: p.preview_id, base_version: p.base_version };
      if (all) body.apply = "all";
      else body.keep = Array.from(document.querySelectorAll("#fusion-diff .fusion-keep:checked"))
        .map(function (b) { return b.dataset.word; });
      const r = await api("POST", "/api/settings/fusions/import/apply", body);
      if (!r.ok) {
        toast("匯入失敗:" + errorText(r), true);
        return;
      }
      this.words = r.data.words || [];
      this.render();
      this.$("fusion-preview").replaceChildren();
      this.$("fusion-preview").hidden = true;
      this.preview = null;
    },
  };

  window.Studio = Object.assign(window.Studio || {}, {
    TOKEN: TOKEN,
    NAME_RE: WORKFLOW_NAME_RE,         // 專案名稱與工作流程名稱同一套中英日白名單(names.py)
    NAME_HINT: WORKFLOW_NAME_HINT.replace("工作流程", "專案"),
    PORT_LABELS: PORT_LABELS,
    el: el,
    api: api,
    errorText: errorText,
    toast: toast,
    fileUrl: fileUrl,
    uploadExt: uploadExt,
    checkFile: checkFile,
    uploadFile: uploadFile,
    draggingFiles: draggingFiles,
    watchRun: watchRun,
    isRunning: function () { return !!Flow.runId; },
    addRoute: function (re, page, enter) { extraRoutes.push({ re: re, page: page, enter: enter }); },
    pageHooks: pageHooks,
    route: route,
    FusionLib: FusionLib,
    fusionDiffRow: fusionDiffRow,
  });

  // ---------- 啟動 ----------

  function start() {
    Settings.init();
    document.querySelectorAll("#open-settings, .open-settings").forEach(function (b) {
      b.addEventListener("click", function () { location.hash = "#/settings"; });
    });
    // 檔案沒落在畫布或拖放區([data-drop])上時不讓瀏覽器直接開啟該檔(會離開本頁)
    document.addEventListener("dragover", function (e) {
      if (!draggingFiles(e)) return;
      const canvas = document.getElementById("drawflow");
      if (canvas && canvas.contains(e.target)) return;
      if (e.target && e.target.closest && e.target.closest("[data-drop]")) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "none";
    });
    document.addEventListener("drop", function (e) {
      if (draggingFiles(e)) e.preventDefault();
    });
    window.addEventListener("hashchange", route);
    route();
  }

  // 其他模組(defer 依序載入)在 DOMContentLoaded 之前登錄路由;一律等 DOMContentLoaded 再啟動
  if (document.readyState === "complete") start();
  else document.addEventListener("DOMContentLoaded", start);
})();
