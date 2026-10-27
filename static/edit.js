// 編輯互動(CHG-20261022-04 T8):拖拉片段空隙改停頓、屬性面板輸入停頓、點字開注音彈出框並套用、
// 每句語速選單(預設 / 0.5–2.0×)、畫面軌拖邊緣改起訖。
//
// 一律以 T7 留下的鉤子(Studio.hook)接在 timeline.js / project.js 畫出的元素上,精靈 ④ 與完整模式
// 共用同一組面板,因此兩處行為相同:
//   "voice-row"      ② 語音每一句的 [data-hook="speed"] → 語速選單
//   "segment-props"  屬性面板的 [data-hook="speed"]、[data-hook="pause"]、[data-hook="tone"] .tone-char
//   "image-props"    屬性面板的 [data-hook="image-range"] → 起訖輸入
//   "timeline"       .tl-gap[data-after](片段之後的空隙)、.tl-img .tl-edge[data-edge](圖片邊緣)
// 修改一律經 Studio.Store.patchSegment / patchImage(伺服器驗證範圍並回傳新的專案檢視);
// 改語速只送 PATCH(伺服器只重做變速,不重新合成);改聲調送 PATCH 後只合成這一句(髒片段)。
//
// 約束(同 app.js):原生 JS、不動態執行字串程式碼、使用者與伺服器文字一律 textContent。
"use strict";

(function () {
  const S = window.Studio;
  const el = S.el;
  const Store = S.Store;

  // ---------- 純函式(tests/test_static.py 以 JS 執行環境實跑) ----------

  // 同 project.py:語速 0.5–2.0、取到 0.05;停頓 0–5 秒、取到 0.1;圖片起訖取到 0.1 秒
  const SPEED_MIN = 0.5;
  const SPEED_MAX = 2.0;
  const PAUSE_MAX = 5;
  const IMAGE_MIN_SPAN = 0.1;
  // 同 breezy_worker.MARKED_RE:輕聲可寫在前(˙ㄉㄜ)或後(ㄉㄜ˙),不可兩邊都有
  const ZHUYIN_RE = /^(˙?)([ㄅ-ㄩ]{1,4})([ˊˇˋ˙]?)$/;
  const TONES = [["", "一聲"], ["ˊ", "二聲"], ["ˇ", "三聲"], ["ˋ", "四聲"], ["˙", "輕聲"]];

  // 以整數運算取到 step 的倍數(避免 0.1 + 0.2 之類的浮點誤差);step 只用 0.05、0.1
  function roundTo(v, step) {
    const k = Math.round(1 / step);
    return Math.round(Number(v) * k) / k;
  }

  function clamp(v, lo, hi) {
    return Math.min(hi, Math.max(lo, v));
  }

  // 語速選單的選項:第一項為「預設」(value ""),其餘 0.50×–2.00×,間隔 0.05,共 31 項
  function speedOptions() {
    const out = [{ value: "", label: "預設" }];
    for (let n = SPEED_MIN * 100; n <= SPEED_MAX * 100; n += 5) {
      const v = n / 100;
      out.push({ value: v.toFixed(2), label: v.toFixed(2) + "×" });
    }
    return out;
  }

  // 片段的語速 → 選單的 value(null 為 "")
  function speedValue(speed) {
    return typeof speed === "number" ? roundTo(speed, 0.05).toFixed(2) : "";
  }

  // 拖拉空隙:原停頓 pause0 秒、水平位移 dx 像素、每秒 zoom 像素 → 新停頓(0–5、取到 0.1)
  function pauseFromDrag(pause0, dx, zoom) {
    return clamp(roundTo(Number(pause0) + dx / zoom, 0.1), 0, PAUSE_MAX);
  }

  // 拖拉圖片邊緣:{start, end}(layout 中的秒數)、edge = start | end → 新的該邊秒數(取到 0.1)
  function edgeFromDrag(range, edge, dx, zoom) {
    const v = (edge === "start" ? range.start : range.end) + dx / zoom;
    if (edge === "start") return clamp(roundTo(v, 0.1), 0, roundTo(range.end - IMAGE_MIN_SPAN, 0.1));
    return Math.max(roundTo(range.start + IMAGE_MIN_SPAN, 0.1), roundTo(v, 0.1));
  }

  // 注音是否為 worker 認得的聲調符號格式
  function zhuyinOk(z) {
    const m = ZHUYIN_RE.exec(String(z || ""));
    return !!m && !(m[1] && m[3]);
  }

  // 注音去掉聲調符號(ㄏㄠˇ → ㄏㄠ、˙ㄉㄜ → ㄉㄜ);不合格回 ""
  function zhuyinBody(z) {
    const m = ZHUYIN_RE.exec(String(z || ""));
    return m && !(m[1] && m[3]) ? m[2] : "";
  }

  // 同一個注音的五種聲調(輕聲寫在前,同 G2PW 輸出的格式)
  function toneVariants(z) {
    const body = zhuyinBody(z);
    if (!body) return [];
    return TONES.map(function (t) {
      return { zhuyin: t[0] === "˙" ? "˙" + body : body + t[0], label: t[1] };
    });
  }

  // 是否為可標註的漢字(同 project.HANZI_RANGES)
  function isHanzi(ch) {
    const c = String(ch).codePointAt(0);
    return (c >= 0x4E00 && c <= 0x9FFF) || (c >= 0x3400 && c <= 0x4DBF) || (c >= 0xF900 && c <= 0xFAFF)
      || (c >= 0x20000 && c <= 0x323AF);
  }

  // 套用一個字的標註後的 annotations(zhuyin 為 null 表示清除該字)
  function withAnnotation(annotations, index, zhuyin) {
    const out = (annotations || []).filter(function (a) { return a.index !== index; })
      .map(function (a) { return { index: a.index, zhuyin: a.zhuyin }; });
    if (zhuyin) out.push({ index: index, zhuyin: zhuyin });
    out.sort(function (a, b) { return a.index - b.index; });
    return out;
  }
  // 純函式結束

  function fmt1(v) {
    return Number(v).toFixed(1);
  }

  // ---------- 語速選單(② 語音每一句與屬性面板共用) ----------

  function speedSelect(seg, label) {
    const sel = document.createElement("select");
    sel.className = "speed-select";
    sel.setAttribute("aria-label", label);
    const def = Store.view ? Store.view.project.defaults.speed : 1;
    speedOptions().forEach(function (o) {
      const opt = el("option", "", o.value === "" ? "預設(" + Number(def).toFixed(2) + "×)" : o.label);
      opt.value = o.value;
      sel.appendChild(opt);
    });
    const current = speedValue(seg.speed);
    sel.value = current;
    sel.addEventListener("change", async function () {
      const v = sel.value;
      sel.disabled = true;
      const r = await Store.patchSegment(seg.id, { speed: v === "" ? null : Number(v) });
      sel.disabled = false;
      if (!r.ok) {
        sel.value = current;
        S.toast("無法修改語速:" + S.errorText(r), true);
        return;
      }
      S.toast(v === "" ? "語速改回預設" : "語速改為 " + v + "×(只重新變速,不重新合成)");
    });
    return sel;
  }

  function mountSpeed(holder, seg, label, prefix) {
    if (!holder) return;
    holder.replaceChildren(speedSelect(seg, label));
    if (prefix) holder.prepend(el("span", "speed-prefix", prefix));
    holder.classList.add("has-editor");
  }

  // ---------- 停頓輸入(屬性面板) ----------

  function mountPause(holder, seg, view) {
    if (!holder) return;
    const keys = { sentence: "pause_sentence", paragraph: "pause_paragraph", forced: "pause_comma" };
    const d = view.project.defaults;
    const dflt = keys[seg.break] ? Number(d[keys[seg.break]]) : 0;
    const input = document.createElement("input");
    input.type = "number";
    input.className = "pause-input";
    input.min = "0";
    input.max = String(PAUSE_MAX);
    input.step = "0.1";
    input.inputMode = "decimal";
    input.placeholder = fmt1(dflt);
    input.value = typeof seg.pause_after === "number" ? fmt1(seg.pause_after) : "";
    input.setAttribute("aria-label", "這一句之後的停頓秒數(空白 = 預設 " + fmt1(dflt) + " 秒)");
    const reset = el("button", "btn btn-small", "預設");
    reset.type = "button";
    reset.title = "改回預設停頓";
    reset.disabled = typeof seg.pause_after !== "number";
    async function send(value) {
      input.disabled = true;
      reset.disabled = true;
      const r = await Store.patchSegment(seg.id, { pause_after: value });
      input.disabled = false;
      if (!r.ok) {
        S.toast("無法修改停頓:" + S.errorText(r), true);
        input.value = typeof seg.pause_after === "number" ? fmt1(seg.pause_after) : "";
        reset.disabled = typeof seg.pause_after !== "number";
        return;
      }
      S.toast(value === null ? "停頓改回預設" : "停頓改為 " + fmt1(value) + " 秒");
    }
    input.addEventListener("change", function () {
      const raw = String(input.value).trim();
      if (raw === "") {
        if (typeof seg.pause_after === "number") send(null);
        return;
      }
      const n = Number(raw);
      if (!isFinite(n) || n < 0 || n > PAUSE_MAX) {
        S.toast("停頓必須是 0 到 " + PAUSE_MAX + " 秒", true);
        input.value = typeof seg.pause_after === "number" ? fmt1(seg.pause_after) : "";
        return;
      }
      send(roundTo(n, 0.1));
    });
    reset.addEventListener("click", function () { send(null); });
    holder.replaceChildren(input, el("span", "prop-unit", "秒"), reset);
    holder.classList.add("has-editor");
  }

  // ---------- 注音彈出框 ----------

  const Tone = {
    box: null,
    cache: {},            // 句子文字 → 逐字注音(POST /api/annotate 的結果)
    seg: null,
    index: -1,
    anchor: null,

    build() {
      const box = el("div", "tone-popup");
      box.id = "tone-popup";
      box.setAttribute("role", "dialog");
      box.setAttribute("aria-modal", "false");
      box.hidden = true;
      const head = el("div", "tone-head");
      this.title = el("strong", "tone-title", "");
      box.setAttribute("aria-labelledby", "tone-title");
      this.title.id = "tone-title";
      const close = el("button", "btn btn-small tone-close", "×");
      close.type = "button";
      close.setAttribute("aria-label", "關閉");
      close.addEventListener("click", () => this.close());
      head.appendChild(this.title);
      head.appendChild(close);
      box.appendChild(head);
      this.auto = el("p", "tone-auto", "");
      box.appendChild(this.auto);
      this.choices = el("div", "tone-choices");
      this.choices.setAttribute("role", "radiogroup");
      this.choices.setAttribute("aria-label", "聲調");
      box.appendChild(this.choices);
      const custom = el("label", "tone-custom");
      custom.appendChild(el("span", "", "自訂:"));
      this.input = document.createElement("input");
      this.input.type = "text";
      this.input.maxLength = 5;
      this.input.autocomplete = "off";
      this.input.spellcheck = false;
      this.input.className = "tone-input";
      this.input.setAttribute("aria-label", "自訂注音(聲調符號 ˊˇˋ˙)");
      this.input.addEventListener("input", () => this.syncChoices());
      custom.appendChild(this.input);
      box.appendChild(custom);
      const light = el("label", "tone-light");
      this.light = document.createElement("input");
      this.light.type = "checkbox";
      this.light.addEventListener("change", () => {
        const body = zhuyinBody(this.input.value);
        if (body) this.input.value = this.light.checked ? "˙" + body : body;
        this.syncChoices();
      });
      light.appendChild(this.light);
      light.appendChild(el("span", "", "輕聲"));
      box.appendChild(light);
      this.error = el("p", "tone-error", "");
      this.error.setAttribute("role", "alert");
      box.appendChild(this.error);
      const actions = el("div", "tone-actions");
      this.clearBtn = el("button", "btn btn-small", "清除");
      this.clearBtn.type = "button";
      this.clearBtn.addEventListener("click", () => this.apply(null));
      this.applyBtn = el("button", "btn btn-small btn-primary", "套用");
      this.applyBtn.type = "button";
      this.applyBtn.addEventListener("click", () => this.apply(this.input.value.trim()));
      actions.appendChild(this.clearBtn);
      actions.appendChild(this.applyBtn);
      box.appendChild(actions);
      box.addEventListener("keydown", (e) => {
        if (e.key === "Escape") this.close();
        if (e.key === "Enter" && e.target === this.input) this.apply(this.input.value.trim());
      });
      document.body.appendChild(box);
      document.addEventListener("pointerdown", (e) => {
        if (!box.hidden && !box.contains(e.target) && !(e.target.closest && e.target.closest(".tone-char"))) this.close();
      });
      this.box = box;
    },

    open(seg, index, anchor) {
      if (!this.box) this.build();
      this.seg = seg;
      this.index = index;
      this.anchor = anchor;
      const ch = Array.from(seg.text)[index];
      const ann = (seg.annotations || []).find(function (a) { return a.index === index; });
      this.title.textContent = "「" + ch + "」的念法";
      this.error.textContent = "";
      this.input.value = ann ? ann.zhuyin : "";
      this.clearBtn.disabled = !ann;
      this.box.hidden = false;
      this.place();
      this.renderChoices(ann ? ann.zhuyin : "");
      this.loadAuto(seg, index);
      this.input.focus();
    },

    close() {
      if (!this.box || this.box.hidden) return;
      this.box.hidden = true;
      if (this.anchor && this.anchor.isConnected) this.anchor.focus();
      this.seg = null;
    },

    place() {
      const r = this.anchor.getBoundingClientRect();
      const w = this.box.offsetWidth || 240;
      const h = this.box.offsetHeight || 220;
      let left = r.left + window.scrollX;
      let top = r.bottom + window.scrollY + 6;
      if (left + w > window.scrollX + document.documentElement.clientWidth - 8) {
        left = window.scrollX + document.documentElement.clientWidth - w - 8;
      }
      if (top + h > window.scrollY + document.documentElement.clientHeight - 8) top = r.top + window.scrollY - h - 6;
      this.box.style.left = Math.max(8, left) + "px";
      this.box.style.top = Math.max(8, top) + "px";
    },

    async loadAuto(seg, index) {
      const text = seg.text;
      let list = this.cache[text];
      if (!list) {
        this.auto.textContent = "自動:查詢中…";
        const r = await S.api("POST", "/api/annotate", { text: text });
        if (this.seg !== seg || this.index !== index) return;   // 期間已換到別的字
        if (!r.ok || !r.data || !Array.isArray(r.data.zhuyin)) {
          this.auto.textContent = "自動:無法取得(" + S.errorText(r) + "),可直接輸入自訂注音";
          return;
        }
        list = r.data.zhuyin;
        this.cache[text] = list;
      }
      const auto = typeof list[index] === "string" && zhuyinOk(list[index]) ? list[index] : "";
      this.auto.textContent = auto ? "自動:" + auto : "自動:(沒有注音)";
      if (!this.input.value && auto) this.input.value = auto;
      this.renderChoices(this.input.value || auto);
    },

    renderChoices(z) {
      this.choices.replaceChildren();
      const variants = toneVariants(z);
      variants.forEach((v) => {
        const lab = el("label", "tone-choice");
        const radio = document.createElement("input");
        radio.type = "radio";
        radio.name = "tone-choice";
        radio.value = v.zhuyin;
        radio.addEventListener("change", () => {
          this.input.value = v.zhuyin;
          this.syncChoices();
        });
        lab.appendChild(radio);
        lab.appendChild(el("span", "", v.zhuyin + "(" + v.label + ")"));
        this.choices.appendChild(lab);
      });
      this.syncChoices();
    },

    syncChoices() {
      const z = this.input.value.trim();
      if (zhuyinBody(z) && !this.choices.querySelector("input")) this.renderChoices(z);
      this.choices.querySelectorAll("input[type=radio]").forEach(function (r) { r.checked = r.value === z; });
      this.light.checked = z.indexOf("˙") !== -1;
      this.error.textContent = z && !zhuyinOk(z) ? "只接受注音符號與聲調符號(ˊˇˋ˙),例如 ㄏㄠˋ" : "";
      this.applyBtn.disabled = !zhuyinOk(z);
    },

    async apply(z) {
      const seg = this.seg;
      if (!seg) return;
      if (z !== null && !zhuyinOk(z)) {
        this.error.textContent = "只接受注音符號與聲調符號(ˊˇˋ˙),例如 ㄏㄠˋ";
        return;
      }
      this.applyBtn.disabled = true;
      this.clearBtn.disabled = true;
      const r = await Store.patchSegment(seg.id, { annotations: withAnnotation(seg.annotations, this.index, z) });
      if (!r.ok) {
        this.applyBtn.disabled = false;
        this.clearBtn.disabled = false;
        this.error.textContent = "無法套用:" + S.errorText(r);
        return;
      }
      this.box.hidden = true;
      this.seg = null;
      // 只有這一句變成需要重新合成;沒有其他工作在跑時直接合成(只合成髒片段)
      if (S.Voice && !S.Voice.running && !Store.busy && !S.isRunning()) {
        S.toast("已套用,這一句重新合成中…");
        S.Voice.synth();
      } else {
        S.toast("已套用;目前有工作在執行,結束後請按「逐句合成」重新合成這一句");
      }
    },
  };

  function mountTone(holder, seg) {
    if (!holder) return;
    holder.querySelectorAll(".tone-char[data-index]").forEach(function (c) {
      const index = Number(c.dataset.index);
      const ch = Array.from(seg.text)[index];
      if (!isHanzi(ch)) return;
      c.classList.add("is-clickable");
      c.tabIndex = 0;
      c.setAttribute("role", "button");
      c.setAttribute("aria-label", "改「" + ch + "」的念法");
      c.title = "點一下改「" + ch + "」的念法";
      c.addEventListener("click", function () { Tone.open(seg, index, c); });
      c.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          Tone.open(seg, index, c);
        }
      });
    });
    holder.after(el("p", "muted tone-hint", "點字改念法(注音聲調)"));
  }

  // ---------- 圖片起訖輸入(屬性面板) ----------

  function mountImageRange(holder, image) {
    if (!holder) return;
    const mk = function (label, value, placeholder) {
      const lab = el("label", "range-field");
      lab.appendChild(el("span", "prop-label", label));
      const i = document.createElement("input");
      i.type = "number";
      i.min = "0";
      i.step = "0.1";
      i.inputMode = "decimal";
      i.className = "range-input";
      i.value = value;
      if (placeholder) i.placeholder = placeholder;
      i.setAttribute("aria-label", label + "(秒)");
      lab.appendChild(i);
      lab.appendChild(el("span", "prop-unit", "秒"));
      return { lab: lab, input: i };
    };
    const start = mk("起點", fmt1(image.start));
    const end = mk("終點", image.end === null ? "" : fmt1(image.end), "到結尾");
    const apply = el("button", "btn btn-small", "套用起訖");
    apply.type = "button";
    apply.addEventListener("click", async function () {
      const s = Number(start.input.value);
      const rawEnd = String(end.input.value).trim();
      const e = rawEnd === "" ? null : Number(rawEnd);
      if (!isFinite(s) || s < 0 || (e !== null && (!isFinite(e) || e <= s))) {
        S.toast("起點必須 ≥ 0,終點必須大於起點(空白 = 到結尾)", true);
        return;
      }
      apply.disabled = true;
      const r = await Store.patchImage(image.id, { start: roundTo(s, 0.1), end: e === null ? null : roundTo(e, 0.1) });
      apply.disabled = false;
      if (!r.ok) S.toast("無法修改起訖:" + S.errorText(r), true);
      else S.toast("已修改圖片起訖");
    });
    holder.replaceChildren(start.lab, end.lab, apply);
    holder.classList.add("has-editor");
  }

  // ---------- 時間軸拖拉:空隙(停頓)與圖片邊緣(起訖) ----------

  let suppressClick = false;

  // 共用的拖拉流程:onMove(dx) 更新畫面、onDone(dx) 送出;移動不到 3 像素視為點擊,不送出
  function dragOn(handle, e, onMove, onDone) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const x0 = e.clientX;
    let moved = false;
    handle.setPointerCapture(e.pointerId);
    handle.classList.add("is-dragging");
    const move = function (ev) {
      const dx = ev.clientX - x0;
      if (Math.abs(dx) >= 3) moved = true;
      if (moved) onMove(dx);
    };
    const up = function (ev) {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      handle.removeEventListener("pointercancel", up);
      handle.classList.remove("is-dragging");
      if (handle.hasPointerCapture(ev.pointerId)) handle.releasePointerCapture(ev.pointerId);
      if (!moved) return;
      suppressClick = true;   // 放開時的 click 不當成選取或跳到該時間
      setTimeout(function () { suppressClick = false; }, 0);
      if (ev.type === "pointerup") onDone(ev.clientX - x0);
      else S.Timeline.render();
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
    handle.addEventListener("pointercancel", up);
  }

  function wireGaps(lanes, view, zoom) {
    const lay = view.layout;
    lanes.voice.querySelectorAll(".tl-gap[data-after]").forEach(function (gap) {
      const after = gap.dataset.after;
      const i = lay.segments.findIndex(function (s) { return s.id === after; });
      if (i === -1) return;
      const prev = lay.segments[i];
      const next = lay.segments.slice(i + 1).find(function (s) { return s.end > s.start; });
      if (!next) return;
      const pause0 = roundTo(next.start - prev.end, 0.1);
      gap.classList.add("is-draggable");
      gap.title = "停頓 " + fmt1(pause0) + " 秒(左右拖拉調整)";
      gap.setAttribute("role", "slider");
      gap.setAttribute("aria-label", "停頓秒數");
      gap.setAttribute("aria-valuemin", "0");
      gap.setAttribute("aria-valuemax", String(PAUSE_MAX));
      gap.setAttribute("aria-valuenow", fmt1(pause0));
      const left = prev.end * zoom;
      gap.addEventListener("pointerdown", function (e) {
        let value = pause0;
        dragOn(gap, e, function (dx) {
          value = pauseFromDrag(pause0, dx, zoom);
          gap.style.left = left + "px";
          gap.style.width = Math.max(2, value * zoom) + "px";
          gap.dataset.value = fmt1(value) + " 秒";
          gap.setAttribute("aria-valuenow", fmt1(value));
        }, async function () {
          delete gap.dataset.value;
          if (value === pause0) {
            S.Timeline.render();
            return;
          }
          const r = await Store.patchSegment(after, { pause_after: value });
          if (!r.ok) {
            S.toast("無法修改停頓:" + S.errorText(r), true);
            S.Timeline.render();
            return;
          }
          S.toast("停頓改為 " + fmt1(value) + " 秒");
        });
      });
      // 鍵盤:左右鍵每次 0.1 秒
      gap.tabIndex = 0;
      gap.addEventListener("keydown", async function (e) {
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
        e.preventDefault();
        const v = clamp(roundTo(pause0 + (e.key === "ArrowRight" ? 0.1 : -0.1), 0.1), 0, PAUSE_MAX);
        if (v === pause0) return;
        const r = await Store.patchSegment(after, { pause_after: v });
        if (!r.ok) S.toast("無法修改停頓:" + S.errorText(r), true);
      });
    });
  }

  function wireEdges(lanes, view, zoom) {
    const lay = view.layout;
    lanes.images.querySelectorAll(".tl-img .tl-edge[data-edge]").forEach(function (h) {
      const block = h.closest(".tl-img");
      const id = block.dataset.image;
      const lm = lay.images.find(function (m) { return m.id === id; });
      if (!lm) return;
      const edge = h.dataset.edge;
      const range = { start: lm.start, end: lm.end };
      h.title = (edge === "start" ? "拖拉調整起點(" : "拖拉調整終點(") + fmt1(edge === "start" ? lm.start : lm.end) + " 秒)";
      h.addEventListener("pointerdown", function (e) {
        let value = edge === "start" ? range.start : range.end;
        S.Timeline.select({ type: "image", id: id });
        dragOn(h, e, function (dx) {
          value = edgeFromDrag(range, edge, dx, zoom);
          const s = edge === "start" ? value : range.start;
          const en = edge === "end" ? value : range.end;
          block.style.left = (s * zoom) + "px";
          block.style.width = Math.max(2, (en - s) * zoom) + "px";
          block.dataset.value = fmt1(s) + "–" + fmt1(en) + " 秒";
        }, async function () {
          delete block.dataset.value;
          const before = edge === "start" ? range.start : range.end;
          if (roundTo(value, 0.1) === roundTo(before, 0.1)) {
            S.Timeline.render();
            return;
          }
          const body = {};
          body[edge] = value;
          const r = await Store.patchImage(id, body);
          if (!r.ok) {
            S.toast("無法修改圖片起訖:" + S.errorText(r), true);
            S.Timeline.render();
            return;
          }
          S.toast((edge === "start" ? "起點" : "終點") + "改為 " + fmt1(value) + " 秒");
        });
      });
    });
  }

  // ---------- 掛上鉤子 ----------

  S.hook("voice-row", function (a) {
    mountSpeed(a.row.querySelector('[data-hook="speed"]'), a.segment, "第 " + (a.index + 1) + " 句語速", "語速");
  });

  S.hook("segment-props", function (a) {
    const box = a.container;
    mountSpeed(box.querySelector('[data-hook="speed"]'), a.segment, "片段 #" + (a.index + 1) + " 語速");
    mountPause(box.querySelector('[data-hook="pause"]'), a.segment, a.view);
    mountTone(box.querySelector('[data-hook="tone"]'), a.segment);
  });

  S.hook("image-props", function (a) {
    mountImageRange(a.container.querySelector('[data-hook="image-range"]'), a.image);
  });

  S.hook("timeline", function (a) {
    wireGaps(a.lanes, a.view, a.zoom);
    wireEdges(a.lanes, a.view, a.zoom);
  });

  // 拖拉結束時的 click 不交給時間軸(避免選取或跳到該時間)
  const content = document.getElementById("tl-content");
  if (content) {
    content.addEventListener("click", function (e) {
      if (suppressClick) {
        e.stopPropagation();
        e.preventDefault();
      }
    }, true);
  }

  // 專案換了或重新整理時關掉彈出框
  Store.on(function () { Tone.close(); });

  S.Edit = { Tone: Tone, speedOptions: speedOptions, pauseFromDrag: pauseFromDrag, edgeFromDrag: edgeFromDrag };
})();
