// 專案資料與面板(CHG-20261022-04 T7):專案狀態(Store)、專案選擇、文稿、封面、② 語音、⑤ 匯出,
// 以及「依預設流程的某一步跑節點」(AI 寫稿、AI 產生封面)。精靈與完整模式共用這些面板與同一組
// `/api/projects/*`;面板只有一份 DOM,精靈切換步驟時把面板搬進步驟區(wizard.js)。
//
// 約束(同 app.js):原生 JS、不動態執行字串程式碼、使用者與伺服器文字一律 textContent;
// 請求一律經 Studio.api(帶 X-Studio-Token);上傳一律經 Studio.uploadFile(原始位元組 + ?name=)。
"use strict";

(function () {
  const S = window.Studio;
  const el = S.el;
  const MAX_SCRIPT = 50000;            // 同 project.MAX_SCRIPT
  const TEXT_EXTS = [".txt", ".md"];

  function $(id) { return document.getElementById(id); }

  function setText(id, text) {
    const e = $(id);
    if (e) e.textContent = text === undefined || text === null ? "" : String(text);
  }

  function fmtSeconds(sec) {
    const s = Math.max(0, Number(sec) || 0);
    const m = Math.floor(s / 60);
    const r = s - m * 60;
    return String(m).padStart(2, "0") + ":" + r.toFixed(1).padStart(4, "0");
  }

  // ---------- 專案狀態 ----------
  //
  // view = 伺服器的「專案檢視」{project, layout, forced_breaks};修改類端點回傳同格式 + version。
  // 監聽者以 Store.on(fn) 登記,每次 view 更新都會被呼叫(fn(view))。

  const Store = {
    name: null,
    view: null,
    busy: false,          // 有 synth / render / 節點執行在跑
    listeners: [],

    on(fn) { this.listeners.push(fn); },

    url(sub) {
      return "/api/projects/" + encodeURIComponent(this.name) + (sub || "");
    },

    mediaUrl(id) {
      return this.url("/media/" + encodeURIComponent(id));
    },

    set(view) {
      if (!view || !view.project || !view.layout) return;
      this.view = view;
      this.listeners.forEach(function (fn) {
        try {
          fn(view);
        } catch (e) {
          // 單一面板出錯不影響其他面板
          S.toast("畫面更新失敗:" + String(e && e.message ? e.message : e), true);
        }
      });
    },

    async open(name) {
      if (this.name === name && this.view) {
        this.set(this.view);
        return true;
      }
      this.name = name;
      this.view = null;
      const r = await S.api("GET", this.url());
      if (this.name !== name) return false;
      if (!r.ok || !r.data || !r.data.project) {
        this.name = null;
        S.toast("無法開啟專案「" + name + "」:" + S.errorText(r), true);
        return false;
      }
      this.set(r.data);
      return true;
    },

    // 重新讀取專案檢視;同時有多個 reload 時只採用最後送出的那一個(合成期間每句完成都會 reload)
    reloadSeq: 0,
    async reload() {
      if (!this.name) return;
      const name = this.name;
      const seq = ++this.reloadSeq;
      const r = await S.api("GET", this.url());
      if (seq !== this.reloadSeq || this.name !== name) return;
      if (r.ok && r.data && r.data.project) this.set(r.data);
    },

    // 修改類請求:成功時以回傳的 view 更新畫面;回傳 Studio.api 的結果
    async mutate(method, sub, body) {
      const r = await S.api(method, this.url(sub), body);
      if (r.ok && r.data && r.data.project) this.set(r.data);
      return r;
    },

    patchSegment(id, body) {
      return this.mutate("PATCH", "/segments/" + encodeURIComponent(id), body);
    },

    patchImage(id, body) {
      return this.mutate("PATCH", "/images/" + encodeURIComponent(id), body);
    },

    segment(id) {
      const p = this.view && this.view.project;
      return p ? p.segments.find(function (s) { return s.id === id; }) || null : null;
    },

    asset(id) {
      const p = this.view && this.view.project;
      return p ? p.assets.find(function (a) { return a.id === id; }) || null : null;
    },

    // 片段實際語速:speed 為 null 時用專案的語速預設(同 project.effective_speed)
    speedOf(seg) {
      const d = this.view ? this.view.project.defaults : {};
      return seg && typeof seg.speed === "number" ? seg.speed : (typeof d.speed === "number" ? d.speed : 1);
    },

    assetLabel(a) {
      if (!a) return "(素材已移除)";
      return typeof a.label === "string" && a.label ? a.label : a.id + "." + a.ext;
    },
  };

  // ---------- 專案選擇(精靈與完整模式沒有指定專案時) ----------

  const Picker = {
    mode: "wizard",

    init() {
      $("picker-form").addEventListener("submit", (e) => {
        e.preventDefault();
        this.create();
      });
      $("picker-name").addEventListener("input", () => { $("picker-error").hidden = true; });
    },

    showError(text) {
      const e = $("picker-error");
      e.textContent = String(text);
      e.hidden = false;
    },

    href(name) {
      return "#/" + this.mode + "/" + encodeURIComponent(name);
    },

    async show(mode, container) {
      this.mode = mode;
      container.appendChild($("project-picker"));
      $("picker-error").hidden = true;
      const list = $("picker-list");
      list.replaceChildren(el("li", "muted", "讀取中…"));
      const r = await S.api("GET", "/api/projects");
      list.replaceChildren();
      const items = r.ok && r.data && Array.isArray(r.data.projects) ? r.data.projects.slice() : [];
      items.sort(function (a, b) { return (b.updated || 0) - (a.updated || 0); });
      if (!r.ok) list.appendChild(el("li", "muted", "無法取得專案清單:" + S.errorText(r)));
      else if (!items.length) list.appendChild(el("li", "muted", "還沒有專案,請在上方建立"));
      items.forEach((p) => {
        if (!p || typeof p.name !== "string") return;
        const li = el("li", "picker-item");
        const a = el("a", "picker-link", p.name);
        a.href = this.href(p.name);
        li.appendChild(a);
        if (typeof p.updated === "number") li.appendChild(el("span", "muted", fmtDate(p.updated)));
        list.appendChild(li);
      });
      $("picker-name").focus();
    },

    async create() {
      const name = $("picker-name").value;
      if (!S.NAME_RE.test(name)) {
        this.showError(S.NAME_HINT);
        return;
      }
      $("picker-create").disabled = true;
      const r = await S.api("POST", "/api/projects", { name: name });
      $("picker-create").disabled = false;
      if (r.ok && r.data && r.data.project) {
        Store.name = name;
        Store.set(r.data);
        $("picker-name").value = "";
        location.hash = this.href(name);
        return;
      }
      this.showError(r.status === 409 ? "已有同名專案,請換一個名稱或從下方清單開啟" : "建立失敗:" + S.errorText(r));
    },
  };

  function fmtDate(epoch) {
    const d = new Date(epoch * 1000);
    const two = function (n) { return String(n).padStart(2, "0"); };
    return two(d.getMonth() + 1) + "/" + two(d.getDate()) + " " + two(d.getHours()) + ":" + two(d.getMinutes());
  }

  // ---------- 依預設流程的某一步跑節點(AI 寫稿、AI 產生封面、其他自訂步驟) ----------
  //
  // 預設流程 = GET /api/workflows/default(修訂 3 R3-1:精靈只讀這一份的 steps)。
  // 子流程 = 該步驟的節點 + 步驟內的連線;從步驟外接進來的文字輸入改接一個「文字」節點,內容為
  // 目前專案的文稿;步驟內內容空白的「文字」節點填入面板上的主題 / 標題。

  const Flows = {
    graph: null,
    specs: null,

    async workflow(force) {
      if (this.graph && !force) return { graph: this.graph };
      const r = await S.api("GET", "/api/workflows/default");
      if (!r.ok || !r.data || !r.data.graph || typeof r.data.graph !== "object") {
        return { error: r.status === 404 ? "找不到預設流程(workflows/default.json)" : "預設流程讀取失敗:" + S.errorText(r) };
      }
      this.graph = r.data.graph;
      return { graph: this.graph };
    },

    async nodeSpecs() {
      if (this.specs) return this.specs;
      const r = await S.api("GET", "/api/nodes");
      if (!r.ok || !r.data || !Array.isArray(r.data.nodes)) return null;
      const specs = {};
      r.data.nodes.forEach(function (s) { if (s && typeof s.kind === "string") specs[s.kind] = s; });
      this.specs = specs;
      return specs;
    },

    findStep(graph, stepId) {
      const steps = graph && Array.isArray(graph.steps) ? graph.steps : [];
      return steps.find(function (s) { return s && s.id === stepId; }) || null;
    },

    // 回傳 {graph} 或 {error}
    subgraph(graph, step, specs, seed, script) {
      const ids = new Set(Array.isArray(step.nodes) ? step.nodes : []);
      if (!ids.size) return { error: "這一步沒有可執行的節點" };
      const nodes = [];
      let needSeed = false;
      (graph.nodes || []).forEach(function (n) {
        if (!n || !ids.has(n.id)) return;
        const copy = { id: n.id, kind: n.kind, params: Object.assign({}, n.params || {}) };
        if (n.pinned === true && n.outputs) {
          copy.pinned = true;
          copy.outputs = n.outputs;
        }
        if (n.kind === "text-input" && !String(copy.params.text || "").trim()) {
          copy.params.text = seed;
          needSeed = true;
        }
        nodes.push(copy);
      });
      if (needSeed && !String(seed || "").trim()) return { error: "seed" };
      const edges = [];
      let ext = 0;
      for (const e of graph.edges || []) {
        if (!e || !e.from || !e.to || !ids.has(e.to.node)) continue;
        if (ids.has(e.from.node)) {
          edges.push(e);
          continue;
        }
        const target = nodes.find(function (n) { return n.id === e.to.node; });
        const spec = target && specs[target.kind];
        const port = spec && spec.inputs ? spec.inputs[e.to.port] : null;
        if (!port || port.type !== "text") {
          return { error: "這一步需要其他步驟產生的" + (S.PORT_LABELS[port && port.type] || "檔案") + ",請在「設定 › 進階流程」執行完整流程" };
        }
        if (!String(script || "").trim()) return { error: "請先完成文稿,這一步會用到文稿內容" };
        ext += 1;
        const xid = "文稿-" + ext;
        nodes.push({ id: xid, kind: "text-input", params: { text: script } });
        edges.push({ from: { node: xid, port: "text" }, to: { node: e.to.node, port: e.to.port } });
      }
      return { graph: { nodes: nodes, edges: edges } };
    },

    // 跑某一步;want 為要取的輸出型別(text / image / …)。回傳 {value, outputs} 或 {error}
    async runStep(stepId, seed, want, onStatus) {
      if (Store.busy || S.isRunning()) return { error: "已有工作在執行,請等它結束" };
      const wf = await this.workflow(true);
      if (wf.error) return { error: wf.error };
      const step = this.findStep(wf.graph, stepId);
      if (!step) return { error: "預設流程沒有「" + stepId + "」這一步" };
      const specs = await this.nodeSpecs();
      if (!specs) return { error: "無法取得節點清單" };
      const script = Store.view ? Store.view.project.script : "";
      const sub = this.subgraph(wf.graph, step, specs, seed, script);
      if (sub.error) return sub;
      Store.busy = true;
      try {
        const r = await S.api("POST", "/api/run", sub.graph);
        if (!r.ok || !r.data || typeof r.data.run_id !== "string") {
          const errs = r.data && Array.isArray(r.data.errors) ? r.data.errors : [];
          return { error: "無法執行:" + (errs.length ? String(errs[0].message || "") : S.errorText(r)) };
        }
        let order = sub.graph.nodes.map(function (n) { return n.id; });
        const outputs = {};
        const errors = [];
        const fin = await S.watchRun(r.data.run_id, function (ev) {
          if (ev.type === "started" && Array.isArray(ev.order)) order = ev.order.map(String);
          if (ev.type === "error") errors.push(String(ev.message || ""));
          if (ev.type !== "node") return;
          const n = sub.graph.nodes.find(function (x) { return x.id === String(ev.node); });
          const label = n && specs[n.kind] ? specs[n.kind].label : String(ev.node);
          if (ev.status === "running" && onStatus) onStatus(label + " 執行中…");
          if (ev.status === "waiting" && onStatus) onStatus(label + ":" + String(ev.message || "等待其他工作"));
          if (ev.status === "error") errors.push(label + ":" + String(ev.message || "執行失敗"));
          if ((ev.status === "done" || ev.status === "skipped") && ev.outputs && typeof ev.outputs === "object") {
            outputs[String(ev.node)] = ev.outputs;
          }
        });
        if (fin.status !== "ok") return { error: errors[0] || fin.message || "執行失敗" };
        // 取執行順序中最後一個產生指定型別輸出的節點(文字輸入節點本身不算)
        for (let i = order.length - 1; i >= 0; i -= 1) {
          const n = sub.graph.nodes.find(function (x) { return x.id === order[i]; });
          const spec = n && specs[n.kind];
          const outs = outputs[order[i]];
          if (!spec || !outs || n.kind === "text-input") continue;
          for (const port of Object.keys(spec.outputs || {})) {
            if (spec.outputs[port].type === want && typeof outs[port] === "string" && outs[port]) {
              return { value: outs[port], outputs: outputs };
            }
          }
        }
        return { error: "這一步沒有產生" + (S.PORT_LABELS[want] || want), outputs: outputs };
      } finally {
        Store.busy = false;
      }
    },
  };

  // 節點輸出的檔案(outputs/…)複製進上傳區,回傳上傳結果({file_id, kind});失敗回 null
  async function importOutput(rel, name) {
    const url = S.fileUrl(rel);
    if (!url) return null;
    let blob;
    try {
      const res = await fetch(url, { credentials: "same-origin", cache: "no-store" });
      if (!res.ok) return null;
      blob = await res.blob();
    } catch (e) {
      return null;
    }
    const file = new File([blob], name, { type: blob.type });
    return S.uploadFile(file);
  }

  // ---------- 文稿面板 ----------

  const ScriptPanel = {
    dirty: false,
    shownFor: null,       // 目前文稿框對應的專案名稱 + 文稿(偵測伺服器端是否更新)

    init() {
      const ta = $("script-text");
      ta.addEventListener("input", () => {
        this.dirty = true;
        setText("script-status", "文稿已修改,尚未存檔");
      });
      $("script-save").addEventListener("click", () => this.save());
      $("script-synth").addEventListener("click", () => this.synthAll());
      $("script-ai").addEventListener("click", () => this.generate());
      ta.addEventListener("dragover", (e) => {
        if (!S.draggingFiles(e)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
        ta.classList.add("drop-target");
      });
      ta.addEventListener("dragleave", () => ta.classList.remove("drop-target"));
      ta.addEventListener("drop", (e) => {
        ta.classList.remove("drop-target");
        if (!S.draggingFiles(e)) return;
        e.preventDefault();
        e.stopPropagation();
        this.dropFile(Array.from(e.dataTransfer.files || []));
      });
      Store.on((view) => this.render(view));
    },

    render(view) {
      const key = Store.name + "\u0000" + view.project.script;
      if (this.dirty && this.shownFor && this.shownFor.split("\u0000")[0] === Store.name) return;
      if (this.shownFor === key) return;
      $("script-text").value = view.project.script;
      this.shownFor = key;
      this.dirty = false;
      setText("script-status", view.project.segments.length ? "共 " + view.project.segments.length + " 句" : "");
    },

    async dropFile(files) {
      if (files.length !== 1) {
        S.toast("一次只能拖入一個文稿檔", true);
        return;
      }
      const f = files[0];
      const ext = S.uploadExt(f.name);
      if (!ext || TEXT_EXTS.indexOf(ext) === -1) {
        S.toast("文稿只接受 .txt 或 .md 檔:「" + f.name + "」", true);
        return;
      }
      let text;
      try {
        text = await f.text();
      } catch (e) {
        S.toast("無法讀取「" + f.name + "」", true);
        return;
      }
      if (text.indexOf("\u0000") !== -1 || text.length > MAX_SCRIPT) {
        S.toast("「" + f.name + "」不是文字檔或超過 " + MAX_SCRIPT + " 字", true);
        return;
      }
      $("script-text").value = text;
      this.dirty = true;
      setText("script-status", "已放入「" + f.name + "」,尚未存檔");
    },

    // 存檔文稿;文稿沒改就不送。回傳 true 表示已是最新
    async save() {
      if (!Store.name || !Store.view) return false;
      if (!this.dirty) return true;
      const script = $("script-text").value;
      const body = { script: script };
      // 文稿沒變時才帶回強制停頓位置(字元索引只對原文稿有效)
      if (script === Store.view.project.script) body.forced_breaks = Store.view.forced_breaks || [];
      $("script-save").disabled = true;
      const r = await Store.mutate("PUT", "", body);
      $("script-save").disabled = false;
      if (!r.ok) {
        setText("script-status", "存檔失敗:" + S.errorText(r));
        S.toast("文稿存檔失敗:" + S.errorText(r), true);
        return false;
      }
      this.dirty = false;
      this.shownFor = null;
      this.render(Store.view);
      setText("script-status", "文稿已存檔,共 " + Store.view.project.segments.length + " 句");
      return true;
    },

    async generate() {
      const topic = $("script-topic").value.trim();
      setText("script-status", "AI 寫稿中…");
      $("script-ai").disabled = true;
      const r = await Flows.runStep("script", topic, "text", function (t) { setText("script-status", t); });
      $("script-ai").disabled = false;
      if (r.error) {
        const msg = r.error === "seed" ? "請先填寫主題" : r.error;
        setText("script-status", msg);
        S.toast("AI 寫稿未完成:" + msg, true);
        return;
      }
      $("script-text").value = r.value;
      this.dirty = true;
      setText("script-status", "AI 已寫好文稿,確認後請按「存檔文稿」");
    },

    async synthAll() {
      if (!(await this.save())) return;
      await Voice.synth();
    },
  };

  // ---------- 封面面板 ----------

  const CoverPanel = {
    init() {
      $("cover-ai").addEventListener("click", () => this.generate());
      const zone = $("panel-cover");
      zone.addEventListener("dragover", (e) => {
        if (!S.draggingFiles(e)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
        zone.classList.add("drop-target");
      });
      zone.addEventListener("dragleave", (e) => {
        if (e.relatedTarget && zone.contains(e.relatedTarget)) return;
        zone.classList.remove("drop-target");
      });
      zone.addEventListener("drop", (e) => {
        zone.classList.remove("drop-target");
        if (!S.draggingFiles(e)) return;
        e.preventDefault();
        e.stopPropagation();
        this.dropFiles(Array.from(e.dataTransfer.files || []));
      });
      Store.on((view) => this.render(view));
    },

    render(view) {
      const list = $("cover-list");
      list.replaceChildren();
      const used = new Set(view.project.images.map(function (m) { return m.asset; }));
      view.project.assets.filter(function (a) { return a.kind === "image"; }).forEach((a) => {
        const li = el("li", "cover-item" + (used.has(a.id) ? " is-used" : ""));
        const img = document.createElement("img");
        img.src = Store.mediaUrl(a.id);
        img.alt = Store.assetLabel(a);
        img.loading = "lazy";
        li.appendChild(img);
        li.appendChild(el("span", "cover-label", Store.assetLabel(a) + (used.has(a.id) ? "(已在畫面軌)" : "")));
        const add = el("button", "btn btn-small", "加入畫面軌");
        add.type = "button";
        add.addEventListener("click", () => addImage({ asset: a.id }));
        li.appendChild(add);
        list.appendChild(li);
      });
      if (!list.firstChild) list.appendChild(el("li", "muted", "還沒有圖片"));
    },

    async dropFiles(files) {
      for (const f of files) {
        const c = S.checkFile(f);
        if (c.error || c.kind !== "image") {
          S.toast(c.error || "封面只接受圖片(.png .jpg .jpeg .webp):「" + f.name + "」", true);
          continue;
        }
        const up = await S.uploadFile(f);
        if (!up) continue;
        await addImage({ file_id: up.file_id, label: f.name });
      }
    },

    async generate() {
      const title = $("cover-title").value.trim();
      setText("cover-status", "AI 產生封面中…");
      $("cover-ai").disabled = true;
      try {
        const r = await Flows.runStep("cover", title, "image", function (t) { setText("cover-status", t); });
        if (r.error) {
          const msg = r.error === "seed" ? "請先填寫標題" : r.error;
          setText("cover-status", msg);
          S.toast("封面未完成:" + msg, true);
          return;
        }
        const up = await importOutput(r.value, "封面.png");
        if (!up) {
          setText("cover-status", "封面已產生,但無法加入專案");
          return;
        }
        const ok = await addImage({ file_id: up.file_id, label: "封面:" + (title || "未命名") });
        setText("cover-status", ok ? "封面已加入畫面軌" : "封面無法加入畫面軌");
      } finally {
        $("cover-ai").disabled = false;
      }
    },
  };

  // 把圖片放上畫面軌:body 為 {asset} 或 {file_id, label},可帶 start
  async function addImage(body) {
    if (!Store.name) return false;
    const r = await Store.mutate("POST", "/images", body);
    if (!r.ok) {
      S.toast("無法加入畫面軌:" + S.errorText(r), true);
      return false;
    }
    return true;
  }

  // 把檔案加入素材庫(不放上時間軸)
  async function addAsset(file) {
    const c = S.checkFile(file);
    if (c.error) {
      S.toast(c.error, true);
      return false;
    }
    const up = await S.uploadFile(file);
    if (!up) return false;
    const r = await Store.mutate("POST", "/assets", { file_id: up.file_id, label: file.name.slice(0, 255) });
    if (!r.ok) {
      S.toast("無法加入素材庫:" + S.errorText(r), true);
      return false;
    }
    return true;
  }

  // 以自錄 WAV 取代一句(修訂 3 R3-6:伺服器只收 RIFF/WAVE;前端先擋副檔名)
  async function replaceAudio(segId, file) {
    if (S.uploadExt(file && file.name) !== ".wav") {
      S.toast("取代語音只接受 WAV 音檔:「" + String(file && file.name) + "」", true);
      return false;
    }
    const c = S.checkFile(file);
    if (c.error) {
      S.toast(c.error, true);
      return false;
    }
    const up = await S.uploadFile(file);
    if (!up) return false;
    const r = await Store.patchSegment(segId, { audio_file: up.file_id });
    if (!r.ok) {
      S.toast("無法取代語音:" + S.errorText(r), true);
      return false;
    }
    S.toast("已用「" + file.name + "」取代這一句");
    return true;
  }

  // ---------- ② 語音 ----------
  //
  // 逐句合成:POST /api/projects/{p}/synth → SSE(waiting、segment、error、finished);每句完成即重新讀取
  // 專案檢視(伺服器把本次已完成的句子疊在檢視上,DIR-5 統整 M-1),該句立即出現在語音軌並可試聽;
  // 結束時再讀一次(伺服器在 finished 前把已合成的句子寫回專案)。

  const Voice = {
    progress: {},        // 片段 id → 合成中的狀態文字(只在合成期間)
    running: false,

    init() {
      $("voice-synth").addEventListener("click", () => this.synth());
      Store.on((view) => this.render(view));
    },

    statusOf(seg) {
      if (this.progress[seg.id]) return this.progress[seg.id];
      const a = seg.audio;
      if (!a) return this.running ? "⏳ 合成中" : "尚未合成";
      const sec = (Number(a.seconds) / Store.speedOf(seg)).toFixed(1);
      return ("key" in a ? "✓ 已合成 " : "✓ 自錄 ") + sec + " 秒";
    },

    render(view) {
      const list = $("voice-list");
      list.replaceChildren();
      view.project.segments.forEach((seg, i) => {
        const li = el("li", "voice-row");
        li.dataset.segment = seg.id;
        li.dataset.drop = "voice-row";
        li.appendChild(el("span", "voice-no", "句 " + (i + 1)));
        li.appendChild(el("span", "voice-text", seg.text));
        const speed = el("span", "voice-speed", typeof seg.speed === "number"
          ? "語速 " + seg.speed.toFixed(2) + "×" : "語速 預設(" + Store.speedOf(seg).toFixed(2) + "×)");
        speed.dataset.hook = "speed";       // T8:改為每句語速選單
        li.appendChild(speed);
        li.appendChild(el("span", "voice-state", this.statusOf(seg)));
        const play = el("button", "btn btn-small", "▶");
        play.type = "button";
        play.title = "試聽這一句";
        play.setAttribute("aria-label", "試聽第 " + (i + 1) + " 句");
        play.disabled = !seg.audio;
        play.addEventListener("click", () => playSegment(seg));
        li.appendChild(play);
        li.addEventListener("dragover", (e) => {
          if (!S.draggingFiles(e)) return;
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
          li.classList.add("drop-target");
        });
        li.addEventListener("dragleave", (e) => {
          if (e.relatedTarget && li.contains(e.relatedTarget)) return;
          li.classList.remove("drop-target");
        });
        li.addEventListener("drop", (e) => {
          li.classList.remove("drop-target");
          if (!S.draggingFiles(e)) return;
          e.preventDefault();
          e.stopPropagation();
          const files = Array.from(e.dataTransfer.files || []);
          if (files.length !== 1) {
            S.toast("一次只能拖入一個音檔", true);
            return;
          }
          replaceAudio(seg.id, files[0]);
        });
        S.runHook("voice-row", { row: li, segment: seg, index: i, view: view });
        list.appendChild(li);
      });
      if (!list.firstChild) list.appendChild(el("li", "muted", "還沒有句子,請先在文稿步驟寫好文稿並存檔"));
    },

    async synth() {
      if (!Store.name || !Store.view) return;
      if (this.running || Store.busy || S.isRunning()) {
        S.toast("已有工作在執行,請等它結束", true);
        return;
      }
      const r = await S.api("POST", Store.url("/synth"));
      if (!r.ok || !r.data || typeof r.data.run_id !== "string") {
        const msg = r.status === 409 ? "已有工作在執行,請等它結束" : S.errorText(r);
        setText("voice-status", "無法合成:" + msg);
        setText("script-status", "無法合成:" + msg);
        S.toast("無法合成:" + msg, true);
        return;
      }
      this.running = true;
      Store.busy = true;
      this.progress = {};
      let done = 0;
      const total = Store.view.project.segments.length;
      const status = function (t) {
        setText("voice-status", t);
        setText("script-status", t);
      };
      status("合成中…");
      this.render(Store.view);
      S.runHook("synth-progress", { running: true });
      const fin = await S.watchRun(r.data.run_id, (ev) => {
        if (ev.type === "waiting") status("等待其他工作結束…");
        if (ev.type === "segment" && typeof ev.id === "string") {
          done += 1;
          const seg = Store.segment(ev.id);
          const sec = Number(ev.seconds) / (seg ? Store.speedOf(seg) : 1);
          this.progress[ev.id] = (ev.cached ? "✓ 沿用 " : "✓ 已合成 ") + sec.toFixed(1) + " 秒";
          status("合成中… " + done + " / " + total + " 句");
          this.render(Store.view);
          Store.reload();   // 不等整批結束:該句音檔已可試聽,重新讀取後語音軌、試聽按鈕與預覽一起更新
          S.runHook("synth-progress", { running: true, segment: ev.id, seconds: ev.seconds });
        }
        if (ev.type === "error") S.toast(String(ev.message || "合成失敗"), true);
      });
      this.running = false;
      Store.busy = false;
      this.progress = {};
      await Store.reload();
      this.render(Store.view);
      const n = Array.isArray(fin.synthesized) ? fin.synthesized.length : 0;
      const c = Array.isArray(fin.cached) ? fin.cached.length : 0;
      status(fin.status === "ok" ? "合成完成:新合成 " + n + " 句、沿用 " + c + " 句" : "合成未完成");
      S.runHook("synth-progress", { running: false });
    },
  };

  // 試聽單一句(合成原檔 + playbackRate = 語速,同預覽)
  const solo = new Audio();
  function playSegment(seg) {
    if (!seg || !seg.audio) return;
    solo.pause();
    solo.src = Store.mediaUrl(seg.audio.id);
    solo.playbackRate = Store.speedOf(seg);
    solo.play().catch(function () { S.toast("無法播放這一句", true); });
  }

  // ---------- ⑤ 匯出 ----------

  const Exporter = {
    init() {
      $("export-run").addEventListener("click", () => this.run());
    },

    async run() {
      if (!Store.name) return;
      if (Store.busy || S.isRunning()) {
        S.toast("已有工作在執行,請等它結束", true);
        return;
      }
      if (!(await ScriptPanel.save())) return;
      const links = $("export-links");
      links.replaceChildren();
      setText("export-status", "準備匯出…");
      const r = await S.api("POST", Store.url("/render"));
      if (!r.ok || !r.data || typeof r.data.run_id !== "string") {
        const msg = S.errorText(r);
        setText("export-status", "無法匯出:" + msg);
        S.toast("無法匯出:" + msg, true);
        if (r.data && Array.isArray(r.data.segments)) S.runHook("unsynthesized", { segments: r.data.segments });
        return;
      }
      Store.busy = true;
      let err = "";
      const fin = await S.watchRun(r.data.run_id, function (ev) {
        if (ev.type === "waiting") setText("export-status", "等待其他工作結束…");
        if (ev.type === "render") setText("export-status", "匯出中…");
        if (ev.type === "error") err = String(ev.message || "匯出失敗");
      });
      Store.busy = false;
      if (fin.status !== "ok") {
        setText("export-status", err || "匯出失敗");
        S.toast(err || "匯出失敗", true);
        return;
      }
      setText("export-status", "匯出完成");
      S.toast("匯出完成");
      [["MP4 影片", fin.mp4], ["SRT 字幕", fin.srt]].forEach(function (pair) {
        const url = S.fileUrl(pair[1]);
        if (!url) return;
        const li = el("li", "");
        const a = el("a", "export-link", pair[0] + ":" + String(pair[1]).split("/").pop());
        a.href = url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        li.appendChild(a);
        links.appendChild(li);
      });
    },
  };

  // ---------- 鉤子(T8 以 Studio.hook(name, fn) 加入編輯互動) ----------
  //
  // 目前會呼叫的鉤子:
  //   "voice-row"       {row, segment, index, view}:② 語音的每一句(row 內 [data-hook="speed"] 為語速)
  //   "segment-props"   {container, segment, index, view}:屬性面板選取片段(timeline.js)
  //   "image-props"     {container, image, view}:屬性面板選取圖片(timeline.js)
  //   "timeline"        {content, lanes, view, zoom}:時間軸重畫後(.tl-gap 為片段空隙、.tl-edge 為圖片邊緣)
  //   "synth-progress"  {running, segment?, seconds?}
  //   "unsynthesized"   {segments}:匯出被拒時未合成的片段 id
  const hooks = {};
  S.hook = function (name, fn) {
    (hooks[name] = hooks[name] || []).push(fn);
  };
  S.runHook = function (name, arg) {
    (hooks[name] || []).forEach(function (fn) {
      try {
        fn(arg);
      } catch (e) {
        S.toast("擴充功能「" + name + "」出錯:" + String(e && e.message ? e.message : e), true);
      }
    });
  };

  S.Store = Store;
  S.Picker = Picker;
  S.Flows = Flows;
  S.ScriptPanel = ScriptPanel;
  S.Voice = Voice;
  S.Exporter = Exporter;
  S.addImage = addImage;
  S.addAsset = addAsset;
  S.replaceAudio = replaceAudio;
  S.playSegment = playSegment;
  S.fmtSeconds = fmtSeconds;
  S.fmtDate = fmtDate;

  // defer 腳本執行時 DOM 已解析完成;路由由 app.js 在 DOMContentLoaded 時才開始
  Picker.init();
  ScriptPanel.init();
  CoverPanel.init();
  Voice.init();
  Exporter.init();
})();
