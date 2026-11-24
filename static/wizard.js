// 首頁、精靈與完整模式(CHG-20261022-04 T7)。
//
// hash 路由(app.js 的 route() 登錄;不是網頁分頁):
//   #/                      首頁:兩張卡片(精靈、完整模式)+ 最近的專案
//   #/wizard[/<專案>[/<n>]] 精靈:第 n 步(1 起算);步驟清單取自 GET /api/workflows/default 的 steps
//                           (修訂 3 R3-1,不寫死;steps 缺失或格式不對時顯示繁中錯誤,不以預設步驟替代)
//   #/full[/<專案>]         完整模式:文稿、封面、素材庫、預覽、屬性、時間軸同一個畫面,沒有步驟
// 沒有指定專案時顯示專案選擇(建立新專案或開啟既有專案)。
// 各步驟依 step.id 對應面板:script 文稿、voice 語音、cover 封面、edit 剪輯、export 匯出;
// 其他 id 顯示通用面板(列出節點並可執行這一步)。面板只有一份 DOM,換步驟時搬到步驟區。
"use strict";

(function () {
  const S = window.Studio;
  const el = S.el;
  const Store = S.Store;
  const WIZARD_RE = /^#\/wizard(?:\/([^/]+))?(?:\/(\d+))?\/?$/;
  const FULL_RE = /^#\/full(?:\/([^/]+))?\/?$/;
  const RESTORE_HINT = "請以 git checkout -- workflows/default.json 復原預設流程";

  function $(id) { return document.getElementById(id); }

  // ---------- 精靈步驟檢查(純函式;tests/test_static.py 以 JS 執行環境實跑) ----------

  // steps 合格回 "",否則回繁中錯誤訊息
  function checkSteps(steps) {
    if (steps === undefined || steps === null) return "預設流程沒有步驟設定(steps)";
    if (!Array.isArray(steps) || steps.length === 0) return "預設流程的步驟設定(steps)必須是非空的清單";
    for (let i = 0; i < steps.length; i += 1) {
      const s = steps[i];
      const where = "第 " + (i + 1) + " 個步驟";
      if (!s || typeof s !== "object" || Array.isArray(s)) return where + "格式不對";
      if (typeof s.id !== "string" || !s.id) return where + "缺少 id";
      if (typeof s.label !== "string" || !s.label.trim()) return where + "缺少名稱(label)";
      if (!Array.isArray(s.nodes) || s.nodes.some(function (n) { return typeof n !== "string"; })) {
        return where + "的節點清單(nodes)格式不對";
      }
    }
    const ids = steps.map(function (s) { return s.id; });
    if (new Set(ids).size !== ids.length) return "步驟 id 重複";
    return "";
  }
  // 精靈步驟檢查結束

  function stepMark(i) {
    return i < 20 ? String.fromCharCode(0x2460 + i) : "(" + (i + 1) + ")";
  }

  function decodeName(raw) {
    if (!raw) return null;
    let name;
    try {
      name = decodeURIComponent(raw);
    } catch (e) {
      return "";
    }
    return S.NAME_RE.test(name) ? name : "";
  }

  function setProjectLabels(name) {
    document.querySelectorAll(".proj-name").forEach(function (e) { e.textContent = name || "—"; });
  }

  // ---------- 面板擺放 ----------

  const Panels = {
    // 全部放回原位:完整模式的 slot,或收回 #parking
    home() {
      document.querySelectorAll("#page-full .slot[data-slot]").forEach(function (slot) {
        const p = $("panel-" + slot.dataset.slot);
        if (p && p.parentNode !== slot) slot.appendChild(p);
      });
      ["project-picker", "panel-voice", "panel-generic"].forEach(function (id) {
        const p = $(id);
        if (p.parentNode !== $("parking")) $("parking").appendChild(p);
      });
    },
  };

  // ---------- 首頁 ----------

  const Home = {
    async enter() {
      const box = $("home-recent");
      const r = await S.api("GET", "/api/projects");
      const list = r.ok && r.data && Array.isArray(r.data.projects) ? r.data.projects.filter(function (p) {
        return p && typeof p.name === "string";
      }) : [];
      if (!list.length) {
        box.hidden = true;
        return;
      }
      list.sort(function (a, b) { return (b.updated || 0) - (a.updated || 0); });
      const p = list[0];
      $("home-recent-name").textContent = p.name + (typeof p.updated === "number" ? "(" + S.fmtDate(p.updated) + ")" : "");
      $("home-recent-wizard").href = "#/wizard/" + encodeURIComponent(p.name);
      $("home-recent-full").href = "#/full/" + encodeURIComponent(p.name);
      box.hidden = false;
    },
  };

  // ---------- 精靈 ----------

  const Wizard = {
    steps: null,
    index: 0,
    seq: 0,            // 路由序號:較舊的非同步結果不覆蓋較新的

    init() {
      $("wizard-prev").addEventListener("click", () => this.go(this.index - 1));
      $("wizard-next").addEventListener("click", () => this.go(this.index + 1));
    },

    showError(text) {
      const e = $("wizard-error");
      e.textContent = text;
      e.hidden = !text;
    },

    async enter(m) {
      const seq = ++this.seq;
      const name = decodeName(m[1]);
      const n = m[2] ? parseInt(m[2], 10) : 1;
      S.Preview.pause();
      Panels.home();
      $("wizard-body").replaceChildren();
      $("wizard-nav").hidden = true;
      $("wizard-steps").replaceChildren();
      this.showError("");
      setProjectLabels(name || null);

      const wf = await S.Flows.workflow(true);
      if (seq !== this.seq) return;
      if (wf.error) {
        this.showError(wf.error + "。" + RESTORE_HINT);
        return;
      }
      const problem = checkSteps(wf.graph.steps);
      if (problem) {
        this.steps = null;
        this.showError("精靈無法顯示步驟:" + problem + "。" + RESTORE_HINT);
        return;
      }
      this.steps = wf.graph.steps;
      this.index = Math.max(0, Math.min(this.steps.length - 1, (Number.isFinite(n) ? n : 1) - 1));
      this.renderSteps();

      if (name === "") S.toast(S.NAME_HINT, true);
      if (!name) {
        setProjectLabels(null);
        S.Picker.show("wizard", $("wizard-body"));
        return;
      }
      const ok = await Store.open(name);
      if (seq !== this.seq) return;
      if (!ok) {
        location.hash = "#/wizard";
        return;
      }
      setProjectLabels(name);
      this.renderBody();
    },

    renderSteps() {
      const ol = $("wizard-steps");
      ol.replaceChildren();
      this.steps.forEach((s, i) => {
        const li = el("li", "wizard-step" + (i === this.index ? " is-current" : ""), stepMark(i) + " " + s.label);
        if (i === this.index) li.setAttribute("aria-current", "step");
        ol.appendChild(li);
      });
    },

    renderBody() {
      const body = $("wizard-body");
      Panels.home();
      body.replaceChildren();
      const step = this.steps[this.index];
      const title = el("h2", "wizard-title", stepMark(this.index) + " " + step.label);
      body.appendChild(title);
      if (step.id === "script") body.appendChild($("panel-script"));
      else if (step.id === "voice") body.appendChild($("panel-voice"));
      else if (step.id === "cover") body.appendChild($("panel-cover"));
      else if (step.id === "export") body.appendChild($("panel-export"));
      else if (step.id === "edit") {
        const grid = el("div", "editor-grid");
        ["library", "preview", "props", "timeline"].forEach(function (k) {
          const cell = el("div", "editor-cell editor-" + k);
          cell.appendChild($("panel-" + k));
          grid.appendChild(cell);
        });
        body.appendChild(grid);
        S.Timeline.render();
      } else {
        Generic.show(step, body);
      }
      $("wizard-nav").hidden = false;
      $("wizard-prev").disabled = this.index === 0;
      $("wizard-next").disabled = this.index >= this.steps.length - 1;
    },

    async go(i) {
      if (!this.steps || i < 0 || i >= this.steps.length || !Store.name) return;
      // 離開文稿步驟前先存檔(沒改不送)
      if (S.ScriptPanel.dirty && !(await S.ScriptPanel.save())) return;
      location.hash = "#/wizard/" + encodeURIComponent(Store.name) + "/" + (i + 1);
    },
  };

  // 不認得的步驟:列出節點、可執行這一步(用預設流程的節點與參數)
  const Generic = {
    step: null,

    init() {
      $("generic-run").addEventListener("click", () => this.run());
    },

    async show(step, body) {
      this.step = step;
      $("generic-title").textContent = step.label;
      $("generic-note").textContent = typeof step.note === "string" ? step.note : "這一步使用預設流程中的下列節點;參數請在「設定 › 進階流程」調整。";
      $("generic-status").textContent = "";
      $("generic-outputs").replaceChildren();
      const list = $("generic-nodes");
      list.replaceChildren();
      const specs = await S.Flows.nodeSpecs();
      const graph = S.Flows.graph || { nodes: [] };
      step.nodes.forEach(function (id) {
        const n = graph.nodes.find(function (x) { return x && x.id === id; });
        const label = n && specs && specs[n.kind] ? specs[n.kind].label : id;
        list.appendChild(el("li", "", label));
      });
      $("generic-run").disabled = !step.nodes.length;
      body.appendChild($("panel-generic"));
    },

    async run() {
      if (!this.step) return;
      const status = $("generic-status");
      status.textContent = "執行中…";
      const r = await S.Flows.runStep(this.step.id, "", "text", function (t) { status.textContent = t; });
      if (r.error) {
        status.textContent = r.error === "seed" ? "這一步的文字輸入是空的,請在「設定 › 進階流程」填寫" : r.error;
        return;
      }
      status.textContent = "完成";
      const out = $("generic-outputs");
      out.replaceChildren();
      const ta = document.createElement("textarea");
      ta.className = "generic-text";
      ta.readOnly = true;
      ta.rows = 6;
      ta.value = r.value;
      ta.setAttribute("aria-label", "這一步的輸出");
      out.appendChild(ta);
    },
  };

  // ---------- 完整模式 ----------

  const Full = {
    seq: 0,

    init() {
      $("full-save").addEventListener("click", () => this.save());
      $("full-restore").addEventListener("click", () => this.restore());
      $("full-render").addEventListener("click", () => {
        $("panel-export").scrollIntoView({ block: "nearest" });
        S.Exporter.run();
      });
    },

    async enter(m) {
      const seq = ++this.seq;
      const name = decodeName(m[1]);
      S.Preview.pause();
      Panels.home();
      if (name === "") S.toast(S.NAME_HINT, true);
      if (!name) {
        setProjectLabels(null);
        $("full-body").hidden = true;
        $("full-tools").hidden = true;
        $("full-picker-slot").hidden = false;
        S.Picker.show("full", $("full-picker-slot"));
        return;
      }
      $("full-picker-slot").hidden = true;
      const ok = await Store.open(name);
      if (seq !== this.seq) return;
      if (!ok) {
        location.hash = "#/full";
        return;
      }
      setProjectLabels(name);
      $("full-body").hidden = false;
      $("full-tools").hidden = false;
      S.Timeline.render();
      this.versions();
    },

    async save() {
      if (!Store.name) return;
      if (S.ScriptPanel.dirty) {
        if (await S.ScriptPanel.save()) S.toast("已存檔");
        this.versions();
        return;
      }
      const r = await Store.mutate("PUT", "", {});
      S.toast(r.ok ? "已存檔" : "存檔失敗:" + S.errorText(r), !r.ok);
      this.versions();
    },

    // 版本清單(history,新到舊);版本 id 為 UTC 時間戳,顯示成本地時間
    async versions() {
      const sel = $("full-versions");
      const r = await S.api("GET", Store.url("/history"));
      sel.replaceChildren();
      const cur = el("option", "", "版本:目前");
      cur.value = "";
      sel.appendChild(cur);
      const list = r.ok && r.data && Array.isArray(r.data.versions) ? r.data.versions.slice().reverse() : [];
      list.forEach(function (v) {
        if (typeof v !== "string") return;
        const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})-/.exec(v);
        let label = v;
        if (m) {
          const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]));
          label = S.fmtDate(d.getTime() / 1000) + ":" + String(d.getSeconds()).padStart(2, "0");
        }
        const o = el("option", "", label);
        o.value = v;
        sel.appendChild(o);
      });
      $("full-restore").disabled = list.length === 0;
    },

    async restore() {
      const v = $("full-versions").value;
      if (!v) {
        S.toast("請先在版本清單選擇要還原的版本", true);
        return;
      }
      const label = $("full-versions").selectedOptions[0].textContent;
      if (!window.confirm("要還原到 " + label + " 的版本嗎?目前的內容會先存入版本清單。")) return;
      S.ScriptPanel.dirty = false;      // 還原後文稿框改顯示還原的版本
      const r = await Store.mutate("POST", "/restore", { version: v });
      S.toast(r.ok ? "已還原到 " + label + " 的版本" : "還原失敗:" + S.errorText(r), !r.ok);
      this.versions();
    },
  };

  S.checkSteps = checkSteps;
  S.Wizard = Wizard;

  Wizard.init();
  Generic.init();
  Full.init();
  S.addRoute(WIZARD_RE, "wizard", function (m) { Wizard.enter(m); });
  S.addRoute(FULL_RE, "full", function (m) { Full.enter(m); });
  S.pageHooks.home = { enter: function () { Home.enter(); } };
  S.pageHooks.wizard = { leave: function () { S.Preview.pause(); } };
  S.pageHooks.full = { leave: function () { S.Preview.pause(); } };
})();
