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
// (CHG-20261029-01 T5)屬性面板「韻律」「語調」小節的百分比拉桿([data-hook="prosody"]、
// [data-hook="pitch-props"])與聲調區的「念法 / 清晰度」模式;改這些只 PATCH 後呼叫 POST /post(不重新合成)。
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

  // 空隙的可拖拉點擊範圍(DIR-5 統整 M-2):停頓 × zoom 不足 GAP_HIT 像素時(含 0 秒),
  // 以兩句交界的中點為中心、寬 GAP_HIT 像素;否則就是停頓本身的範圍。回傳 {left, width}(像素)
  const GAP_HIT = 12;
  function gapBox(start, pause, zoom) {
    const w = pause * zoom;
    if (w >= GAP_HIT) return { left: start * zoom, width: w };
    return { left: start * zoom + w / 2 - GAP_HIT / 2, width: GAP_HIT };
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
    ToneMode.bar(holder, seg);
    if (ToneMode.mode === "clarity") {
      mountClarityChars(holder, seg);
      holder.after(el("p", "muted tone-hint", "拖曳選取字詞設定清晰度(橫條長度 = 清晰度,紫色為個別設定)"));
      return;
    }
    if (ToneMode.mode === "fusion") {
      mountFusionChars(holder, seg);
      holder.after(el("p", "muted tone-hint", "拖曳選取相鄰 2~4 個漢字合音(字幕仍顯示原文;套用後重新合成這一句)"));
      return;
    }
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
      const place = function (v) {
        const box = gapBox(prev.end, v, zoom);
        gap.style.left = box.left + "px";
        gap.style.width = box.width + "px";
      };
      place(pause0);
      gap.addEventListener("pointerdown", function (e) {
        let value = pause0;
        dragOn(gap, e, function (dx) {
          value = pauseFromDrag(pause0, dx, zoom);
          place(value);
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

  // ---------- 韻律:連音、清晰度、圓弧拉桿與字詞清晰度(CHG-20261029-01 T5) ----------
  //
  // 拉桿一律 0–100 整數、步進 1%,右側 <output> 即時顯示「NN%」(input 事件),放開(change)才送出;
  // 「預設」按鈕送 null(取程式常數:連音 0%、清晰度 100%、圓弧 50%)。送出 = PATCH 片段後呼叫
  // POST /post(只重做這一句的後製,不重新合成);目前有合成 / 匯出在跑(409)時稍後重試。
  // 自錄音檔片段不做後製:拉桿停用並說明原因(D4)。

  const PROSODY_DEFAULTS = { connect: 0, clarity: 100, roundness: 50 };
  // (修訂 1 R1-3)清晰度低於 25% 時在拉桿說明下方警告;只警告、不阻擋(照常送出、不跳訊息)
  const CLARITY_WARN_BELOW = 25;
  const CLARITY_WARN_TEXT = "⚠ 低於 25% 會過度含糊、聲音偏悶,不建議使用;需要含糊感時建議用 50% 或 25%。";
  const POST_RETRY_MS = 1000;
  const POST_RETRIES = 120;

  // 字詞清晰度:把 [start, end) 設為 value(null = 清除),移除與它重疊的既有區段;依起點排序(純函式)
  function withWordClarity(list, start, end, value) {
    const out = (list || []).filter(function (w) { return w.end <= start || w.start >= end; })
      .map(function (w) { return { start: w.start, end: w.end, value: w.value }; });
    if (value !== null && value !== undefined) out.push({ start: start, end: end, value: value });
    out.sort(function (a, b) { return a.start - b.start; });
    return out;
  }

  // 每個字的清晰度(個別設定者優先,否則整句;null 取 100)與是否個別設定(純函式)
  function charClarity(seg) {
    const n = Array.from(seg.text).length;
    const base = typeof seg.clarity === "number" ? seg.clarity : PROSODY_DEFAULTS.clarity;
    const out = [];
    for (let i = 0; i < n; i++) out.push({ value: base, own: false });
    (seg.word_clarity || []).forEach(function (w) {
      for (let i = w.start; i < w.end && i < n; i++) out[i] = { value: w.value, own: true };
    });
    return out;
  }

  function isSynthesized(seg) {
    return !!(seg && seg.audio && ("key" in seg.audio));
  }

  function isExternal(seg) {
    return !!(seg && seg.audio && !("key" in seg.audio));
  }

  function sleep(ms) {
    return new Promise(function (r) { setTimeout(r, ms); });
  }

  const Post = {
    running: new Set(),     // 後製中的片段 id(屬性面板顯示「⏳ 後製中」)
    autoDone: {},           // 專案名稱 → 已自動補做過(開啟舊專案時只補一次)

    chain: Promise.resolve(),

    // 只重做指定片段的後製;本頁的後製請求一次一個(排隊),忙碌(409)時每秒重試,最多 2 分鐘
    run(ids) {
      const next = this.chain.then(() => this.runOne(ids));
      this.chain = next.catch(function () { return null; });
      return next;
    },

    async runOne(ids) {
      const name = Store.name;
      (ids || []).forEach((id) => this.running.add(id));
      S.Props && S.Props.render();
      try {
        for (let k = 0; k < POST_RETRIES; k++) {
          if (Store.name !== name) return null;
          if (!Store.busy && !S.isRunning()) {
            const r = await Store.mutate("POST", "/post", ids ? { segments: ids } : {});
            if (r.ok || r.stale || r.status !== 409) {
              if (!r.ok && !r.stale) S.toast("後製失敗:" + S.errorText(r), true);
              return r;
            }
          }
          await sleep(POST_RETRY_MS);
        }
        S.toast("後製等待逾時:目前的工作結束後請再調整一次", true);
        return null;
      } finally {
        (ids || []).forEach((id) => this.running.delete(id));
        S.Props && S.Props.render();
        S.PitchEdit && S.PitchEdit.refresh();   // 修訂 1 R1-10:後製結束後語調改以實際套用的點重畫
      }
    },
  };

  // PATCH 片段後只重做這一句的後製
  async function patchAndPost(seg, body, okText) {
    const r = await Store.patchSegment(seg.id, body);
    if (!r.ok) {
      if (!r.stale) S.toast("無法修改:" + S.errorText(r), true);
      return r;
    }
    if (okText) S.toast(okText + "(只重做後製,不重新合成)");
    if (isSynthesized(seg)) await Post.run([seg.id]);
    return r;
  }

  // 清晰度警告(R1-3):淺橙底、role="status",以 textContent 寫入固定文字;低於門檻才顯示
  function clarityWarn() {
    const p = el("p", "clarity-warn", "");
    p.setAttribute("role", "status");
    p.hidden = true;
    return p;
  }

  function setClarityWarn(node, value) {
    const on = typeof value === "number" && value < CLARITY_WARN_BELOW;
    node.textContent = on ? CLARITY_WARN_TEXT : "";
    node.hidden = !on;
  }

  // 百分比拉桿:{label, value(null = 預設), dflt, disabled, onCommit(v | null), onInput(v)?}
  function percentSlider(o) {
    const wrap = el("div", "pct-slider");
    const input = document.createElement("input");
    input.type = "range";
    input.min = "0";
    input.max = "100";
    input.step = "1";
    input.className = "pct-input";
    const v = typeof o.value === "number" ? o.value : o.dflt;
    input.value = String(v);
    input.setAttribute("aria-label", o.label + "(百分比)");
    const out = document.createElement("output");
    out.className = "pct-output";
    out.textContent = v + "%";
    const reset = el("button", "btn btn-small pct-reset", "預設");
    reset.type = "button";
    reset.title = o.label + "改回預設(" + o.dflt + "%)";
    reset.disabled = !!o.disabled || typeof o.value !== "number";
    input.disabled = !!o.disabled;
    input.addEventListener("input", function () {
      out.textContent = input.value + "%";
      if (o.onInput) o.onInput(Number(input.value));
    });
    input.addEventListener("change", function () { o.onCommit(Number(input.value)); });
    reset.addEventListener("click", function () { o.onCommit(null); });
    wrap.append(input, out, reset);
    return wrap;
  }

  function mountProsody(holder, seg) {
    if (!holder) return;
    const ext = isExternal(seg);
    const rows = [];
    const add = function (key, label, hint, warn) {
      const row = el("div", "pct-row");
      row.appendChild(el("span", "prop-label", label));
      row.appendChild(percentSlider({
        label: label, value: seg[key], dflt: PROSODY_DEFAULTS[key], disabled: ext,
        onCommit: function (v) {
          const body = {};
          body[key] = v;
          patchAndPost(seg, body, v === null ? label + "改回預設" : label + "改為 " + v + "%");
        },
        onInput: warn ? function (v) { setClarityWarn(warn, v); } : null,
      }));
      rows.push(row, el("p", "muted pct-hint", hint));
      if (warn) {
        setClarityWarn(warn, seg[key]);
        rows.push(warn);
      }
    };
    add("connect", "連音", "字與字之間的黏著;句與句之間仍由「停頓」決定");
    add("clarity", "清晰度", "100% 字正腔圓,越低越含糊;個別字詞可在下方另設", ext ? null : clarityWarn());
    if (ext) rows.push(el("p", "muted pct-hint is-disabled-note", "自錄音檔不做後製"));
    holder.replaceChildren(...rows);
    holder.classList.add("has-editor");
  }

  function mountPitchProps(holder, seg) {
    if (!holder) return;
    const ext = isExternal(seg);
    const pitch = seg.pitch;
    const rows = [];
    const row = el("div", "pct-row");
    row.appendChild(el("span", "prop-label", "圓弧"));
    row.appendChild(percentSlider({
      label: "圓弧", value: pitch ? pitch.roundness : null, dflt: PROSODY_DEFAULTS.roundness,
      disabled: ext || !pitch,
      onCommit: function (v) {
        patchAndPost(seg, { pitch: { points: pitch.points, roundness: v } },
          v === null ? "圓弧改回預設" : "圓弧改為 " + v + "%");
      },
    }));
    rows.push(row, el("p", "muted pct-hint", "折線轉角的圓弧程度:0% 為直線折角,100% 為最圓滑;"
      + "曲線一律經過每個實際套用的點(含放大後的峰谷與自動補點)"));
    const info = el("p", "pitch-info");
    info.dataset.hook = "pitch-info";
    if (ext) info.textContent = "自錄音檔不做後製";
    else if (!pitch) info.textContent = "沒有語調;在時間軸選取的語音片段上點兩下新增關鍵點";
    else {
      const k = S.PitchEdit ? S.PitchEdit.selected(seg.id) : -1;
      let text = "關鍵點 " + pitch.points.length + " 個";
      // (修訂 1 R1-10)自動補點數:與時間軸同一份資料(後製有效時為 worker 的結果,否則為試算)
      const model = S.pitchModel ? S.pitchModel(seg) : null;
      if (model && model.auto.length) text += " · 自動補點 " + model.auto.length + " 個(虛線,不可拖曳)";
      if (k >= 0 && pitch.points[k]) {
        const p = pitch.points[k];
        text += " · 選取中:#" + (k + 1) + "(位置 " + Math.round(p.pos * 100) + "%," + semiText(p.semi) + ")";
      }
      info.textContent = text;
    }
    rows.push(info);
    const clear = el("button", "btn btn-small", "清除語調");
    clear.type = "button";
    clear.disabled = ext || !pitch;
    clear.addEventListener("click", function () { patchAndPost(seg, { pitch: null }, "已清除語調"); });
    rows.push(clear);
    holder.replaceChildren(...rows);
    holder.classList.add("has-editor");
  }

  function semiText(v) {
    const n = Number(v);
    return (n > 0 ? "+" : n < 0 ? "−" : "") + Math.abs(n).toFixed(1) + " 半音";
  }

  // 聲調區的模式:念法(注音彈出框)/ 清晰度(拖曳選取字詞)
  const ToneMode = {
    mode: "tone",
    modes: [["tone", "念法"], ["clarity", "清晰度"], ["fusion", "合音"]],

    bar(holder, seg) {
      const bar = el("div", "tone-modes");
      bar.setAttribute("role", "group");
      bar.setAttribute("aria-label", "聲調區模式");
      this.modes.forEach((m) => {
        const b = el("button", "btn btn-small tone-mode" + (this.mode === m[0] ? " is-active" : ""), m[1]);
        b.type = "button";
        b.dataset.mode = m[0];
        b.setAttribute("aria-pressed", this.mode === m[0] ? "true" : "false");
        b.addEventListener("click", () => {
          this.mode = m[0];
          Tone.close();
          WordPopup.close();
          FusionPopup.close();
          S.Props.render();
        });
        bar.appendChild(b);
      });
      holder.before(bar);
    },
  };

  // 清晰度模式:每字下方橫條(長度 = 清晰度,個別設定者紫色),拖曳選取字詞後開彈出框
  function mountClarityChars(holder, seg) {
    const list = charClarity(seg);
    const chars = Array.from(seg.text);
    holder.classList.add("is-clarity-mode");
    holder.querySelectorAll(".tone-char[data-index]").forEach(function (c) {
      const i = Number(c.dataset.index);
      const bar = el("span", "clarity-bar" + (list[i] && list[i].own ? " is-own" : ""));
      bar.style.width = (list[i] ? list[i].value : 100) + "%";
      c.appendChild(bar);
      if (isHanzi(chars[i])) {
        c.classList.add("is-selectable");
        c.title = "拖曳選取字詞,設定清晰度";
      }
    });
    if (isExternal(seg)) return;
    let sel = null;
    const mark = function () {
      holder.querySelectorAll(".tone-char").forEach(function (c) {
        const i = Number(c.dataset.index);
        c.classList.toggle("is-range", !!sel && i >= Math.min(sel.a, sel.b) && i <= Math.max(sel.a, sel.b));
      });
    };
    const at = function (x, y) {
      const e = document.elementFromPoint(x, y);
      const c = e && e.closest ? e.closest(".tone-char[data-index]") : null;
      return c && holder.contains(c) ? Number(c.dataset.index) : null;
    };
    const move = function (e) {
      const i = at(e.clientX, e.clientY);
      if (i !== null && sel) {
        sel.b = i;
        mark();
      }
    };
    const up = function () {
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", up);
      if (!sel) return;
      const s = Math.min(sel.a, sel.b);
      const e = Math.max(sel.a, sel.b) + 1;
      sel = null;
      if (!chars.slice(s, e).every(isHanzi)) {
        mark();
        S.toast("字詞清晰度只能設定在漢字上(不可含標點)", true);
        return;
      }
      WordPopup.open(seg, s, e, holder.querySelector('.tone-char[data-index="' + s + '"]'));
    };
    holder.addEventListener("pointerdown", function (e) {
      const i = at(e.clientX, e.clientY);
      if (i === null || !isHanzi(chars[i]) || e.button !== 0) return;
      e.preventDefault();
      sel = { a: i, b: i };
      mark();
      document.addEventListener("pointermove", move);
      document.addEventListener("pointerup", up);
    });
  }

  const WordPopup = {
    box: null,
    seg: null,
    range: null,

    build() {
      const box = el("div", "tone-popup word-popup");
      box.id = "word-popup";
      box.setAttribute("role", "dialog");
      box.setAttribute("aria-modal", "false");
      box.setAttribute("aria-labelledby", "word-title");
      box.hidden = true;
      const head = el("div", "tone-head");
      this.title = el("strong", "tone-title", "");
      this.title.id = "word-title";
      const close = el("button", "btn btn-small tone-close", "×");
      close.type = "button";
      close.setAttribute("aria-label", "關閉");
      close.addEventListener("click", () => this.close());
      head.append(this.title, close);
      this.sliderSlot = el("div", "word-slider");
      this.note = el("p", "tone-auto", "");
      this.warn = clarityWarn();
      const actions = el("div", "tone-actions");
      this.clearBtn = el("button", "btn btn-small", "清除");
      this.clearBtn.type = "button";
      this.clearBtn.addEventListener("click", () => this.apply(null));
      this.applyBtn = el("button", "btn btn-small btn-primary", "套用");
      this.applyBtn.type = "button";
      this.applyBtn.addEventListener("click", () => this.apply(Number(this.input.value)));
      actions.append(this.clearBtn, this.applyBtn);
      box.append(head, this.sliderSlot, this.note, this.warn, actions);
      box.addEventListener("keydown", (e) => { if (e.key === "Escape") this.close(); });
      document.body.appendChild(box);
      document.addEventListener("pointerdown", (e) => {
        if (!box.hidden && !box.contains(e.target) && !(e.target.closest && e.target.closest(".tone-char"))) this.close();
      });
      this.box = box;
    },

    open(seg, start, end, anchor) {
      if (!this.box) this.build();
      this.seg = seg;
      this.range = [start, end];
      const word = Array.from(seg.text).slice(start, end).join("");
      const own = (seg.word_clarity || []).find(function (w) { return w.start === start && w.end === end; });
      const base = typeof seg.clarity === "number" ? seg.clarity : PROSODY_DEFAULTS.clarity;
      this.title.textContent = "「" + word + "」的清晰度 — 第 " + (start + 1) + (end - start > 1 ? "–" + end : "") + " 字";
      const warn = this.warn;
      const slider = percentSlider({ label: "「" + word + "」的清晰度", value: own ? own.value : base,
        dflt: base, onCommit: function () {}, onInput: function (v) { setClarityWarn(warn, v); } });
      slider.querySelector(".pct-reset").remove();
      this.input = slider.querySelector("input");
      this.sliderSlot.replaceChildren(slider);
      this.note.textContent = "未個別設定的字沿用整句清晰度(" + base + "%)";
      setClarityWarn(this.warn, own ? own.value : base);
      this.clearBtn.disabled = !(seg.word_clarity || []).some(function (w) { return w.start < end && w.end > start; });
      this.applyBtn.disabled = false;
      this.box.hidden = false;
      const r = anchor ? anchor.getBoundingClientRect() : { left: 100, bottom: 100 };
      this.box.style.left = Math.max(8, r.left + window.scrollX) + "px";
      this.box.style.top = Math.max(8, r.bottom + window.scrollY + 6) + "px";
      this.input.focus();
    },

    close() {
      if (!this.box || this.box.hidden) return;
      this.box.hidden = true;
      this.seg = null;
    },

    async apply(value) {
      const seg = this.seg;
      if (!seg) return;
      const [s, e] = this.range;
      this.applyBtn.disabled = true;
      this.clearBtn.disabled = true;
      const r = await patchAndPost(seg, { word_clarity: withWordClarity(seg.word_clarity, s, e, value) },
        value === null ? "已清除字詞清晰度" : "字詞清晰度改為 " + value + "%");
      if (r && r.ok) this.close();
      else {
        this.applyBtn.disabled = false;
        this.clearBtn.disabled = false;
      }
    },
  };

  // 開啟專案(含 v1 升級來的舊專案)時,對沒有有效後製的片段補做一次(D20)
  // (只看開啟後的第一個檢視;之後的修改由各自的 patchAndPost 處理)
  function autoPost(view) {
    if (!view || !Store.name || Post.autoDone[Store.name]) return;
    Post.autoDone[Store.name] = true;
    if ((view.post_stale || []).length) Post.run(null);
  }

  // ---------- 合音(CHG-20261029-01 T9;D23~D28、D37~D41) ----------
  //
  // 聲調區「合音」模式:拖曳選取相鄰 2~4 個漢字 → 彈出框(兩字:詞庫值或規則推導;3~4 字:詞庫值,
  // 否則列出相鄰兩字的選項只合併一對,D37)→ 可改注音、存入合音詞庫、套用(該句改送標註文字,重新合成)。
  // 文稿框的三色波浪底線與常駐浮層見下方 ScriptMarks(D40~D43)。字幕、時間軸一律顯示原文。

  const FUSION_MAX = 4;
  // 「原詞 → 合音注音」的箭頭(以跳脫字元書寫:靜態檢查不允許頁面字串出現步驟箭頭,這裡是對應關係)
  const ARROW = " \u2192 ";
  const INITIALS = "ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙ";
  const SOURCE_LABEL = { lexicon: "詞庫", rule: "規則", manual: "手動" };

  // n 字合音的注音:1~n−1 個以單一半形空白分隔、worker 認得的音節(純函式)
  function fusionZhuyinOk(z, n) {
    const parts = String(z || "").split(" ");
    return parts.length >= 1 && parts.length <= n - 1 && parts.every(zhuyinOk);
  }

  // 一組合音的欄位副本;display(修訂 1 R1-4)有才帶(舊專案檔的合音不補欄位,送模型的文字不變)
  function fusionItem(f) {
    const out = { index: f.index, length: f.length, zhuyin: f.zhuyin, source: f.source };
    if (typeof f.display === "string") out.display = f.display;
    return out;
  }

  // 套用一組合音後的 fusions(移除與它重疊者;依起點排序)(純函式)。新套用的 item 一律帶 display
  // (由詞庫套用時為該詞當時的顯示字,其餘為空字串,R1-4)
  function withFusion(list, item) {
    const end = item.index + item.length;
    const out = (list || []).filter(function (f) { return f.index + f.length <= item.index || f.index >= end; })
      .map(fusionItem);
    out.push(fusionItem(Object.assign({ display: "" }, item)));
    out.sort(function (a, b) { return a.index - b.index; });
    return out;
  }

  // 這一句送給模型的文字(同 project.tts_text():`[`、`]` 換全形;合音帶顯示字時整段換成顯示字、不加注音;
  // 沒有顯示字時前 k 字帶 `[:音節]`、刪除其餘字(D26);範圍外的聲調標註帶 `[:注音]`)(純函式;T14 前端試算,
  // 只供屬性面板合音模式「送模型」列顯示,實際送出仍以伺服器為準)
  function ttsText(seg) {
    const esc = function (t) { return String(t).replace(/\[/g, "［").replace(/\]/g, "］"); };
    const anns = {};
    (seg.annotations || []).forEach(function (a) { anns[a.index] = a.zhuyin; });
    const fus = {};
    const shown = {};
    const skip = new Set();
    (seg.fusions || []).forEach(function (f) {
      if (f.display) {
        shown[f.index] = f.display;
        for (let j = 0; j < f.length; j++) skip.add(f.index + j);
        return;
      }
      const parts = String(f.zhuyin || "").split(" ");
      for (let j = 0; j < f.length; j++) {
        if (j < parts.length) fus[f.index + j] = parts[j];
        else skip.add(f.index + j);
      }
    });
    const out = [];
    Array.from(esc(seg.text || "")).forEach(function (ch, i) {
      if (Object.prototype.hasOwnProperty.call(shown, i)) out.push(esc(shown[i]));
      if (skip.has(i)) return;
      out.push(ch);
      if (Object.prototype.hasOwnProperty.call(fus, i)) out.push("[:" + fus[i] + "]");
      else if (Object.prototype.hasOwnProperty.call(anns, i)) out.push("[:" + anns[i] + "]");
    });
    return out.join("");
  }

  // 詞庫詞比對(同 fusion.suggest:4 → 3 → 2 字、每層由左至右、不重疊;exclude 為不提示的字元索引)(純函式)
  function matchLexicon(text, lexicon, exclude) {
    const chars = Array.from(text || "");
    const used = new Set(exclude || []);
    const out = [];
    for (let n = FUSION_MAX; n >= 2; n--) {
      let i = 0;
      while (i <= chars.length - n) {
        const word = chars.slice(i, i + n).join("");
        let free = true;
        for (let k = i; k < i + n; k++) if (used.has(k) || !isHanzi(chars[k])) free = false;
        if (free && Object.prototype.hasOwnProperty.call(lexicon, word)) {
          out.push({ index: i, length: n, word: word, zhuyin: lexicon[word] });
          for (let k = i; k < i + n; k++) used.add(k);
          i += n;
          continue;
        }
        i += 1;
      }
    }
    return out.sort(function (a, b) { return a.index - b.index; });
  }

  // 片段中不再提示的字元索引:已套用合音、聲調標註、關閉紀錄
  function fusionExcluded(seg) {
    const out = [];
    (seg.fusions || []).forEach(function (f) { for (let k = 0; k < f.length; k++) out.push(f.index + k); });
    (seg.annotations || []).forEach(function (a) { out.push(a.index); });
    (seg.fusion_off || []).forEach(function (d) { for (let k = 0; k < d.length; k++) out.push(d.index + k); });
    return out;
  }

  // words:啟用中的詞(供建議);all:完整詞庫含停用詞(判斷詞是否已存在,D32);
  // display:啟用中的詞的顯示字(修訂 1 R1-4,由詞庫套用時寫入片段)
  const Lexicon = {
    words: null,
    all: null,
    display: {},
    async load(force) {
      if (this.words && !force) return this.words;
      const r = await S.api("GET", "/api/settings/fusions");
      const out = {};
      const all = {};
      const display = {};
      if (r.ok && r.data && Array.isArray(r.data.words)) {
        r.data.words.forEach(function (w) {
          all[w.word] = w.zhuyin;
          if (w.enabled) {
            out[w.word] = w.zhuyin;
            display[w.word] = typeof w.display === "string" ? w.display : "";
          }
        });
      }
      this.words = out;
      this.all = all;
      this.display = display;
      return out;
    },
  };

  // 句子的逐字注音:G2PW 自動注音(Tone.cache)再疊上聲調標註
  async function sentenceZhuyin(seg) {
    let list = Tone.cache[seg.text];
    if (!list) {
      const r = await S.api("POST", "/api/annotate", { text: seg.text });
      if (!r.ok || !r.data || !Array.isArray(r.data.zhuyin)) return null;
      list = r.data.zhuyin;
      Tone.cache[seg.text] = list;
    }
    const out = list.slice();
    (seg.annotations || []).forEach(function (a) { out[a.index] = a.zhuyin; });
    return out;
  }

  function splitInitial(z) {
    const body = String(z || "").replace(/^˙/, "");
    return INITIALS.indexOf(body.charAt(0)) !== -1 ? body.charAt(0) : "";
  }

  async function resynthOne(okText) {
    if (S.Voice && !S.Voice.running && !Store.busy && !S.isRunning()) {
      S.toast(okText + ",這一句重新合成中…");
      await S.Voice.synth();
    } else {
      S.toast(okText + ";目前有工作在執行,結束後請按「逐句合成」重新合成這一句");
    }
  }

  const FusionPopup = {
    box: null,
    seg: null,
    range: null,
    info: null,
    edited: false,

    build() {
      const box = el("div", "tone-popup fusion-popup");
      box.id = "fusion-popup";
      box.setAttribute("role", "dialog");
      box.setAttribute("aria-modal", "false");
      box.setAttribute("aria-labelledby", "fusion-title");
      box.hidden = true;
      const head = el("div", "tone-head");
      this.title = el("strong", "tone-title", "");
      this.title.id = "fusion-title";
      const close = el("button", "btn btn-small tone-close", "×");
      close.type = "button";
      close.setAttribute("aria-label", "關閉");
      close.addEventListener("click", () => this.close());
      head.append(this.title, close);
      this.derive = el("p", "tone-auto fusion-derive", "");
      this.options = el("div", "fusion-options");
      this.options.setAttribute("role", "radiogroup");
      this.options.setAttribute("aria-label", "合併哪兩個字");
      const custom = el("label", "tone-custom");
      custom.appendChild(el("span", "", "合音注音:"));
      this.input = document.createElement("input");
      this.input.type = "text";
      this.input.className = "tone-input fusion-input";
      this.input.maxLength = 24;
      this.input.autocomplete = "off";
      this.input.spellcheck = false;
      this.input.setAttribute("aria-label", "合音注音(多個音節以半形空白分隔)");
      this.input.addEventListener("input", () => {
        this.edited = true;
        this.sync();
      });
      custom.appendChild(this.input);
      this.error = el("p", "tone-error", "");
      this.error.setAttribute("role", "alert");
      const actions = el("div", "tone-actions");
      this.saveBtn = el("button", "btn btn-small", "存入合音詞庫");
      this.saveBtn.type = "button";
      this.saveBtn.addEventListener("click", () => this.saveLexicon());
      this.applyBtn = el("button", "btn btn-small btn-primary", "套用");
      this.applyBtn.type = "button";
      this.applyBtn.addEventListener("click", () => this.apply());
      actions.append(this.saveBtn, this.applyBtn);
      box.append(head, this.derive, this.options, custom, this.error, actions);
      box.addEventListener("keydown", (e) => { if (e.key === "Escape") this.close(); });
      document.body.appendChild(box);
      document.addEventListener("pointerdown", (e) => {
        if (!box.hidden && !box.contains(e.target) && !(e.target.closest && e.target.closest(".tone-char"))) this.close();
      });
      this.box = box;
    },

    async open(seg, start, end, anchor) {
      if (!this.box) this.build();
      this.seg = seg;
      this.range = [start, end];
      this.edited = false;
      this.info = null;
      const chars = Array.from(seg.text);
      const word = chars.slice(start, end).join("");
      const n = end - start;
      this.title.textContent = "合音「" + word + "」";
      this.options.replaceChildren();
      this.input.value = "";
      this.error.textContent = "";
      this.box.hidden = false;
      const r = anchor ? anchor.getBoundingClientRect() : { left: 100, bottom: 100 };
      // 不超出視窗右緣(T14:原本片段靠右時彈出框超出視窗、內容被切掉)
      const maxLeft = document.documentElement.clientWidth - this.box.offsetWidth - 8;
      this.box.style.left = Math.max(8, Math.min(r.left, maxLeft) + window.scrollX) + "px";
      this.box.style.top = Math.max(8, r.bottom + window.scrollY + 6) + "px";
      if (n > FUSION_MAX || !chars.slice(start, end).every(isHanzi) || n < 2) {
        this.derive.textContent = n < 2 ? "合音需要相鄰 2 個以上的漢字" : "合音最多 " + FUSION_MAX + " 個漢字(不可含標點)";
        this.input.disabled = true;
        this.applyBtn.disabled = true;
        this.saveBtn.disabled = true;
        return;
      }
      this.input.disabled = false;
      this.derive.textContent = "查詢注音中…";
      this.applyBtn.disabled = true;
      this.saveBtn.disabled = true;
      const zh = await sentenceZhuyin(seg);
      if (this.seg !== seg) return;
      const res = await S.api("POST", "/api/fusion/suggest", { text: seg.text, zhuyin: zh || undefined,
                                                              index: start, length: n });
      if (this.seg !== seg) return;
      if (!res.ok || !res.data || !res.data.fusion) {
        this.derive.textContent = "無法取得合音建議(" + S.errorText(res) + "),可直接輸入注音";
        this.sync();
        return;
      }
      const f = res.data.fusion;
      this.info = f;
      if (n === 2) {
        if (f.lexicon) this.derive.textContent = "詞庫:" + f.lexicon + (f.rule ? "(規則為 " + f.rule + ")" : "");
        else if (f.zhuyin) {
          const rest = String(f.b || "").replace(/^˙/, "").slice(splitInitial(f.b).length);
          this.derive.textContent = (splitInitial(f.a) || "(零聲母)") + " + " + rest + ARROW + f.reason + ARROW + f.zhuyin;
        } else this.derive.textContent = "不合音:" + f.reason;
      } else if (f.lexicon) {
        this.derive.textContent = "詞庫:" + f.lexicon;
      } else {
        this.derive.textContent = "選一對相鄰的字合併,其餘字維持原讀音";
        (f.options || []).forEach((o, k) => {
          const lab = el("label", "tone-choice fusion-option");
          const radio = document.createElement("input");
          radio.type = "radio";
          radio.name = "fusion-option";
          radio.value = String(k);
          radio.disabled = !o.combined;
          radio.checked = k === f.default;
          radio.addEventListener("change", () => {
            this.input.value = o.combined || "";
            this.edited = false;
            this.sync();
          });
          const pair = chars.slice(start + o.offset, start + o.offset + o.length).join("");
          lab.append(radio, el("span", "", pair + ":" + (o.zhuyin ? (SOURCE_LABEL[o.source] || "") + " " + o.zhuyin
            : "跳過(" + o.reason + ")")));
          this.options.appendChild(lab);
        });
      }
      this.input.value = f.zhuyin || "";
      this.sync();
    },

    source() {
      if (this.edited) return "manual";
      return (this.info && this.info.source) || "manual";
    },

    sync() {
      const n = this.range ? this.range[1] - this.range[0] : 0;
      const z = this.input.value.trim();
      const ok = fusionZhuyinOk(z, n);
      this.error.textContent = z && !ok ? "合音注音須為 1~" + (n - 1) + " 個音節(以半形空白分隔),音節數少於字數" : "";
      this.applyBtn.disabled = !ok;
      this.saveBtn.disabled = !ok;
    },

    close() {
      if (!this.box || this.box.hidden) return;
      this.box.hidden = true;
      this.seg = null;
    },

    async saveLexicon() {
      const seg = this.seg;
      if (!seg) return;
      const word = Array.from(seg.text).slice(this.range[0], this.range[1]).join("");
      const z = this.input.value.trim();
      await Lexicon.load(true);
      const lex = Lexicon.all || {};
      if (Object.prototype.hasOwnProperty.call(lex, word) && !window.confirm("「" + word + "」已在合音詞庫中,要覆寫嗎?")) return;
      const r = await S.api("PUT", "/api/settings/fusions/" + encodeURIComponent(word), { zhuyin: z, enabled: true });
      if (!r.ok) {
        this.error.textContent = "無法存入合音詞庫:" + S.errorText(r);
        return;
      }
      await Lexicon.load(true);
      S.toast("已存入合音詞庫:「" + word + "」" + ARROW + z);
    },

    // 由詞庫套用(選取的整個詞命中詞庫、注音未改)時的顯示字;其餘為空字串(修訂 1 R1-4)
    async display(word, z, source) {
      if (source !== "lexicon" || !this.info || this.info.lexicon !== z) return "";
      await Lexicon.load(true);
      return Lexicon.words && Lexicon.words[word] === z ? (Lexicon.display[word] || "") : "";
    },

    async apply() {
      const seg = this.seg;
      if (!seg) return;
      const [s, e] = this.range;
      const z = this.input.value.trim();
      const source = this.source();
      this.applyBtn.disabled = true;
      const display = await this.display(Array.from(seg.text).slice(s, e).join(""), z, source);
      if (this.seg !== seg) return;
      const r = await Store.patchSegment(seg.id, { fusions: withFusion(seg.fusions, { index: s, length: e - s,
        zhuyin: z, source: source, display: display }) });
      if (!r.ok) {
        this.applyBtn.disabled = false;
        this.error.textContent = "無法套用:" + S.errorText(r);
        return;
      }
      this.close();
      await resynthOne("已套用合音");
    },
  };

  async function removeFusion(seg, index) {
    const r = await Store.patchSegment(seg.id, { fusions: (seg.fusions || []).filter(function (f) { return f.index !== index; }) });
    if (!r.ok) {
      S.toast("無法移除合音:" + S.errorText(r), true);
      return;
    }
    await resynthOne("已移除合音");
  }

  // 合音模式:已套用的合音以框線標示並列出(可 ✕ 移除);拖曳選取 2~4 個漢字開彈出框
  function mountFusionChars(holder, seg) {
    const chars = Array.from(seg.text);
    holder.classList.add("is-fusion-mode");
    const fused = {};
    (seg.fusions || []).forEach(function (f) { for (let k = 0; k < f.length; k++) fused[f.index + k] = f; });
    holder.querySelectorAll(".tone-char[data-index]").forEach(function (c) {
      const i = Number(c.dataset.index);
      if (fused[i]) {
        c.classList.add("is-fused");
        if (fused[i].index === i) c.classList.add("is-fused-start");
      }
      if (isHanzi(chars[i])) c.classList.add("is-selectable");
    });
    const list = el("ul", "fusion-list");
    (seg.fusions || []).forEach(function (f) {
      const li = el("li", "fusion-item");
      li.appendChild(el("span", "", chars.slice(f.index, f.index + f.length).join("") + ARROW + f.zhuyin
        + "(" + (SOURCE_LABEL[f.source] || f.source) + ")"));
      const x = el("button", "btn btn-small", "✕");
      x.type = "button";
      x.setAttribute("aria-label", "移除合音「" + chars.slice(f.index, f.index + f.length).join("") + "」");
      x.addEventListener("click", function () { removeFusion(seg, f.index); });
      li.appendChild(x);
      list.appendChild(li);
    });
    // T14:「已套用」下方的「送模型」(依已套用的合音算出整句送出的文字,有顯示字時為顯示字,R1-4)
    // 與「字幕」(原文)兩列,照已核准的 after-fusion.png;一律以 textContent 寫入
    const sentRow = el("div", "prop-row fusion-row");
    sentRow.append(el("span", "prop-label", "送模型"), el("code", "fusion-sent", ttsText(seg)));
    const subRow = el("div", "prop-row fusion-row");
    subRow.append(el("span", "prop-label", "字幕"), el("span", "fusion-subtitle", seg.text + "(原文不變)"));
    holder.after(list, sentRow, subRow);
    if (isExternal(seg)) return;
    let sel = null;
    const mark = function () {
      holder.querySelectorAll(".tone-char").forEach(function (c) {
        const i = Number(c.dataset.index);
        c.classList.toggle("is-range", !!sel && i >= Math.min(sel.a, sel.b) && i <= Math.max(sel.a, sel.b));
      });
    };
    const at = function (x, y) {
      const t = document.elementFromPoint(x, y);
      const c = t && t.closest ? t.closest(".tone-char[data-index]") : null;
      return c && holder.contains(c) ? Number(c.dataset.index) : null;
    };
    const move = function (e) {
      const i = at(e.clientX, e.clientY);
      if (i !== null && sel) {
        sel.b = i;
        mark();
      }
    };
    const up = function () {
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup", up);
      if (!sel) return;
      const s = Math.min(sel.a, sel.b);
      const e = Math.max(sel.a, sel.b) + 1;
      sel = null;
      FusionPopup.open(seg, s, e, holder.querySelector('.tone-char[data-index="' + s + '"]'));
    };
    holder.addEventListener("pointerdown", function (e) {
      const i = at(e.clientX, e.clientY);
      if (i === null || !isHanzi(chars[i]) || e.button !== 0) return;
      e.preventDefault();
      sel = { a: i, b: i };
      mark();
      document.addEventListener("pointermove", move);
      document.addEventListener("pointerup", up);
    });
  }

  // ---------- 文稿中各片段的位置 ----------

  // 文稿中各片段的位置(同 studio_server._spans:依序往後找)
  function segmentSpans(script, segments) {
    const out = [];
    let cur = 0;
    segments.forEach(function (s) {
      const i = script.indexOf(s.text, cur);
      if (i < 0) {
        out.push(null);
        return;
      }
      out.push([i, i + s.text.length]);
      cur = i + s.text.length;
    });
    return out;
  }

  // ---------- 文稿框的合音標示(D40~D43;做法 A:textarea + 上方標記層,D42) ----------
  //
  // 標記層與 textarea 同一組排版(複製計算後的字型、行高、內距與換行規則),文字透明、不接收點擊,只畫
  // 三色波浪底線:黃 = 未決定(詞庫命中)、綠 = 已啟用(片段 fusions)、紅 = 已停用(片段 fusion_off)。
  // 已啟用處在標記層上方常駐浮層(顯示 display 或合音注音)遮住原詞;點擊浮層或點在黃 / 紅標記內開浮動窗
  // (「啟用」「停用」)。IME 組字期間不重算、不開浮動窗;捲動與 resize 時重新同步。標示資料以片段為準:
  // 啟用 / 停用時文稿未存檔就先存檔分句,再 PATCH 該片段;未存檔的改動以位移調整已決定的位置(同 D41)。

  const MIRROR_PROPS = ["fontFamily", "fontSize", "fontWeight", "fontStyle", "lineHeight", "letterSpacing",
    "wordSpacing", "tabSize", "textIndent", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
    "borderTopWidth", "borderRightWidth", "borderBottomWidth", "borderLeftWidth", "borderStyle", "boxSizing",
    "whiteSpace", "overflowWrap", "wordBreak", "textAlign"];

  // 改動範圍之前不動、之後位移、重疊者清除(同 project.shift_marks)(純函式)
  function shiftDecided(decided, oldText, newText) {
    let p = 0;
    const minLen = Math.min(oldText.length, newText.length);
    while (p < minLen && oldText[p] === newText[p]) p++;
    let s = 0;
    while (s < minLen - p && oldText[oldText.length - 1 - s] === newText[newText.length - 1 - s]) s++;
    const oldEnd = oldText.length - s;
    const delta = newText.length - oldText.length;
    const out = [];
    decided.forEach(function (d) {
      if (d.start + d.len <= p) out.push(d);
      else if (d.start >= oldEnd && (p < oldEnd || d.start >= p)) out.push(Object.assign({}, d, { start: d.start + delta }));
    });
    return out;
  }

  // 已啟用合音的浮層文字:片段帶 display(修訂 1 R1-4)時以它為準(即送模型的字);舊專案檔沒有此欄位時
  // 沿用詞庫中注音相同者的顯示字(D43)
  function shownDisplay(f, entry) {
    if (typeof f.display === "string") return f.display;
    return entry && entry.zhuyin === f.zhuyin ? entry.display : "";
  }

  const ScriptMarks = {
    ta: null,
    mirror: null,
    wrap: null,
    pop: null,
    text: "",
    decided: [],          // [{start, len, state: on | off, zhuyin, display}](文稿座標)
    lexicon: {},          // {詞: {zhuyin, display}}
    composing: false,
    target: null,

    async init() {
      const ta = document.getElementById("script-text");
      if (!ta || this.ta) return;
      this.ta = ta;
      const wrap = el("div", "fusion-wrap");
      ta.parentNode.insertBefore(wrap, ta);
      wrap.appendChild(ta);
      const mirror = el("div", "fusion-mirror");
      mirror.setAttribute("aria-hidden", "true");
      wrap.appendChild(mirror);
      this.wrap = wrap;
      this.mirror = mirror;
      const pop = el("div", "fusion-pop");
      pop.id = "fusion-pop";
      pop.setAttribute("role", "dialog");
      pop.setAttribute("aria-label", "合音");
      pop.hidden = true;
      this.popTitle = el("div", "fusion-pop-title", "");
      const row = el("div", "fusion-pop-row");
      const on = el("button", "btn btn-small b-on", "啟用");
      on.type = "button";
      on.id = "fusion-pop-on";
      const off = el("button", "btn btn-small b-off", "停用");
      off.type = "button";
      off.id = "fusion-pop-off";
      row.append(on, off);
      pop.append(this.popTitle, row);
      pop.addEventListener("mousedown", function (e) { e.preventDefault(); });   // 不搶文稿框的焦點
      on.addEventListener("click", () => this.decide("on"));
      off.addEventListener("click", () => this.decide("off"));
      document.body.appendChild(pop);
      this.pop = pop;
      document.addEventListener("mousedown", (e) => {
        if (!pop.hidden && !pop.contains(e.target) && !(e.target.classList && e.target.classList.contains("fusion-pill"))
            && e.target !== ta) this.closePop();
      });
      ta.addEventListener("compositionstart", () => {
        this.composing = true;
        this.closePop();
      });
      ta.addEventListener("compositionend", () => {
        this.composing = false;
        this.sync();
      });
      ta.addEventListener("input", () => { if (!this.composing) this.sync(); });
      ta.addEventListener("scroll", () => this.render());
      ta.addEventListener("click", () => this.onClick());
      if (window.ResizeObserver) new ResizeObserver(() => this.render()).observe(ta);
      window.addEventListener("resize", () => this.render());
      Store.on(() => this.fromView());
      await this.loadLexicon();
      this.fromView();
    },

    async loadLexicon() {
      const r = await S.api("GET", "/api/settings/fusions");
      const out = {};
      if (r.ok && r.data && Array.isArray(r.data.words)) {
        r.data.words.forEach(function (w) { if (w.enabled) out[w.word] = { zhuyin: w.zhuyin, display: w.display || "" }; });
      }
      this.lexicon = out;
    },

    // 由專案檢視(已存檔的片段)重建已決定的位置
    fromView() {
      if (!this.ta || !Store.view) return;
      const view = Store.view;
      if (S.ScriptPanel && S.ScriptPanel.dirty) {   // 未存檔:保留目前的位移結果,只重畫
        this.sync();
        return;
      }
      const script = view.project.script;
      const spans = segmentSpans(script, view.project.segments);
      const out = [];
      const lex = this.lexicon;
      this.annotated = new Set();
      view.project.segments.forEach((s, k) => {
        const sp = spans[k];
        if (!sp) return;
        (s.fusions || []).forEach(function (f) {
          const word = s.text.slice(f.index, f.index + f.length);
          const e = lex[word];
          out.push({ start: sp[0] + f.index, len: f.length, state: "on", zhuyin: f.zhuyin, display: shownDisplay(f, e) });
        });
        (s.fusion_off || []).forEach(function (d) {
          const e = lex[s.text.slice(d.index, d.index + d.length)];
          out.push({ start: sp[0] + d.index, len: d.length, state: "off", zhuyin: e ? e.zhuyin : "",
                     display: e ? e.display : "" });
        });
        (s.annotations || []).forEach((a) => this.annotated.add(sp[0] + a.index));
      });
      this.decided = out;
      this.text = this.ta.value === script ? script : this.ta.value;
      this.render();
    },

    sync() {
      if (!this.ta) return;
      const v = this.ta.value;
      if (v !== this.text) {
        this.decided = shiftDecided(this.decided, this.text, v);
        if (this.annotated) {
          const moved = shiftDecided(Array.from(this.annotated).map(function (i) { return { start: i, len: 1 }; }),
            this.text, v);
          this.annotated = new Set(moved.map(function (d) { return d.start; }));
        }
        this.text = v;
      }
      this.render();
    },

    marks() {
      const exclude = [];
      this.decided.forEach(function (d) { for (let k = 0; k < d.len; k++) exclude.push(d.start + k); });
      (this.annotated || new Set()).forEach(function (i) { exclude.push(i); });
      const flat = {};
      Object.keys(this.lexicon).forEach((w) => { flat[w] = this.lexicon[w].zhuyin; });
      const pending = matchLexicon(this.text, flat, exclude).map((h) => ({ start: h.index, len: h.length,
        state: "pending", zhuyin: h.zhuyin, display: this.lexicon[h.word] ? this.lexicon[h.word].display : "" }));
      return this.decided.concat(pending).sort(function (a, b) { return a.start - b.start; });
    },

    render() {
      const ta = this.ta;
      const mirror = this.mirror;
      if (!ta || !mirror) return;
      const cs = getComputedStyle(ta);
      MIRROR_PROPS.forEach(function (k) { mirror.style[k] = cs[k]; });
      mirror.style.borderColor = "transparent";
      mirror.style.width = ta.offsetWidth - (ta.offsetWidth - ta.clientWidth - ta.clientLeft * 2) + "px";
      mirror.style.height = ta.offsetHeight + "px";
      mirror.style.left = ta.offsetLeft + "px";
      mirror.style.top = ta.offsetTop + "px";
      mirror.replaceChildren();
      const text = this.text;
      let pos = 0;
      const marks = this.marks();
      marks.forEach(function (m) {
        if (m.start < pos || m.start + m.len > text.length) return;
        if (m.start > pos) mirror.appendChild(document.createTextNode(text.slice(pos, m.start)));
        const sp = el("span", "mk mark-" + m.state, text.slice(m.start, m.start + m.len));
        sp.dataset.start = String(m.start);
        sp.dataset.len = String(m.len);
        mirror.appendChild(sp);
        pos = m.start + m.len;
      });
      mirror.appendChild(document.createTextNode(text.slice(pos) + "\n"));
      mirror.scrollTop = ta.scrollTop;
      mirror.scrollLeft = ta.scrollLeft;
      // 浮層:啟用處常駐,遮住原詞;捲出可視範圍則不顯示
      this.wrap.querySelectorAll(".fusion-pill").forEach(function (p) { p.remove(); });
      const base = this.wrap.getBoundingClientRect();
      const taRect = ta.getBoundingClientRect();
      marks.forEach((m) => {
        if (m.state !== "on") return;
        const span = mirror.querySelector('.mk[data-start="' + m.start + '"]');
        const rects = span ? span.getClientRects() : [];
        if (!rects.length) return;
        const r = rects[0];
        if (r.bottom < taRect.top || r.top > taRect.bottom) return;
        const pill = el("div", "fusion-pill", m.display || m.zhuyin);
        pill.dataset.start = String(m.start);
        pill.title = "合音:" + text.slice(m.start, m.start + m.len) + ARROW + m.zhuyin;
        pill.style.left = (r.left - base.left - 2) + "px";
        pill.style.top = (r.top - base.top - 1) + "px";
        pill.style.width = (r.width + 4) + "px";
        pill.style.height = (r.height + 2) + "px";
        pill.addEventListener("mousedown", function (e) { e.preventDefault(); });
        pill.addEventListener("click", (e) => {
          e.stopPropagation();
          this.openPop(m, pill.getBoundingClientRect());
        });
        this.wrap.appendChild(pill);
      });
    },

    // 屬性面板句子文字的標記(D40,DIR-5 修正 U-1):規則同文稿框,座標為句內索引(逐字)
    segMarks(seg) {
      const lex = this.lexicon;
      const chars = Array.from(seg.text || "");
      const word = function (i, n) { return chars.slice(i, i + n).join(""); };
      const out = [];
      (seg.fusions || []).forEach(function (f) {
        const e = lex[word(f.index, f.length)];
        out.push({ index: f.index, len: f.length, state: "on", zhuyin: f.zhuyin, display: shownDisplay(f, e) });
      });
      (seg.fusion_off || []).forEach(function (d) {
        const e = lex[word(d.index, d.length)];
        out.push({ index: d.index, len: d.length, state: "off", zhuyin: e ? e.zhuyin : "", display: e ? e.display : "" });
      });
      const exclude = [];
      out.forEach(function (d) { for (let k = 0; k < d.len; k++) exclude.push(d.index + k); });
      (seg.annotations || []).forEach(function (a) { exclude.push(a.index); });
      const flat = {};
      Object.keys(lex).forEach(function (w) { flat[w] = lex[w].zhuyin; });
      matchLexicon(seg.text, flat, exclude).forEach(function (h) {
        out.push({ index: h.index, len: h.length, state: "pending", zhuyin: h.zhuyin,
                   display: lex[h.word] ? lex[h.word].display : "" });
      });
      out.forEach(function (m) { m.segId = seg.id; m.word = word(m.index, m.len); });
      return out.sort(function (a, b) { return a.index - b.index; });
    },

    // 把屬性面板的句子文字換成帶標記的文字;點擊標記開同一個浮動窗(#fusion-pop)
    mountProps(box, seg) {
      if (!box || !seg || !this.pop) return;
      const chars = Array.from(seg.text || "");
      box.replaceChildren();
      let pos = 0;
      this.segMarks(seg).forEach((m) => {
        if (m.index < pos || m.index + m.len > chars.length) return;
        if (m.index > pos) box.appendChild(document.createTextNode(chars.slice(pos, m.index).join("")));
        const sp = el("span", "mk mark-" + m.state, m.word);
        sp.dataset.index = String(m.index);
        sp.dataset.len = String(m.len);
        sp.addEventListener("click", (e) => {
          e.stopPropagation();
          this.openPop(m, sp.getBoundingClientRect());
        });
        box.appendChild(sp);
        pos = m.index + m.len;
      });
      if (pos < chars.length) box.appendChild(document.createTextNode(chars.slice(pos).join("")));
    },

    onClick() {
      const ta = this.ta;
      if (this.composing || ta.selectionStart !== ta.selectionEnd) return;
      const pos = ta.selectionStart;
      const m = this.marks().find(function (x) { return pos >= x.start && pos <= x.start + x.len && x.state !== "on"; });
      if (!m) {
        this.closePop();
        return;
      }
      const span = this.mirror.querySelector('.mk[data-start="' + m.start + '"]');
      if (span) this.openPop(m, span.getBoundingClientRect());
    },

    openPop(m, rect) {
      this.target = m;
      const word = m.segId ? m.word : this.text.slice(m.start, m.start + m.len);
      this.popTitle.textContent = word + ARROW + m.zhuyin;
      this.pop.hidden = false;
      this.pop.style.left = (rect.left + window.scrollX) + "px";
      this.pop.style.top = (rect.bottom + window.scrollY + 8) + "px";
    },

    closePop() {
      if (this.pop) this.pop.hidden = true;
      this.target = null;
    },

    // 啟用 / 停用:文稿未存檔就先存檔分句,再 PATCH 該詞所在片段(fusions 與 fusion_off 互斥)
    async decide(state) {
      const m = this.target;
      this.closePop();
      if (!m || !m.zhuyin) return;
      let seg = null;
      let at = -1;
      if (m.segId) {
        seg = Store.segment(m.segId);
        at = m.index;
      } else {
        if (S.ScriptPanel && S.ScriptPanel.dirty && !(await S.ScriptPanel.save())) return;
        const view = Store.view;
        const spans = segmentSpans(view.project.script, view.project.segments);
        view.project.segments.forEach(function (s, k) {
          const sp = spans[k];
          if (sp && m.start >= sp[0] && m.start + m.len <= sp[1]) {
            seg = s;
            at = m.start - sp[0];
          }
        });
      }
      if (!seg) {
        S.toast("找不到這個詞所在的句子", true);
        return;
      }
      const same = function (x) { return x.index === at && x.length === m.len; };
      const off = (seg.fusion_off || []).filter(function (x) { return !same(x); })
        .map(function (x) { return { index: x.index, length: x.length }; });
      let fusions = (seg.fusions || []).filter(function (x) { return !same(x); });
      if (state === "on") {
        fusions = withFusion(fusions, { index: at, length: m.len, zhuyin: m.zhuyin, source: "lexicon",
                                        display: m.display || "" });
      }
      else off.push({ index: at, length: m.len });
      const r = await Store.patchSegment(seg.id, { fusions: fusions, fusion_off: off });
      if (!r.ok) {
        S.toast("無法" + (state === "on" ? "啟用" : "停用") + "合音:" + S.errorText(r), true);
        return;
      }
      S.toast(state === "on" ? "已啟用合音,這一句需要重新合成" : "已停用這個合音");
    },
  };

  // ---------- 掛上鉤子 ----------

  S.hook("voice-row", function (a) {
    mountSpeed(a.row.querySelector('[data-hook="speed"]'), a.segment, "第 " + (a.index + 1) + " 句語速", "語速");
  });

  S.hook("segment-props", function (a) {
    const box = a.container;
    mountSpeed(box.querySelector('[data-hook="speed"]'), a.segment, "片段 #" + (a.index + 1) + " 語速");
    mountPause(box.querySelector('[data-hook="pause"]'), a.segment, a.view);
    mountTone(box.querySelector('[data-hook="tone"]'), a.segment);
    mountProsody(box.querySelector('[data-hook="prosody"]'), a.segment);
    mountPitchProps(box.querySelector('[data-hook="pitch-props"]'), a.segment);
    ScriptMarks.mountProps(box.querySelector(".prop-text"), a.segment);
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
  Store.on(function (view) {
    Tone.close();
    WordPopup.close();
    FusionPopup.close();
    autoPost(view);
  });
  ScriptMarks.init();

  S.Edit = { Tone: Tone, speedOptions: speedOptions, pauseFromDrag: pauseFromDrag, edgeFromDrag: edgeFromDrag,
    gapBox: gapBox, GAP_HIT: GAP_HIT, Post: Post, patchAndPost: patchAndPost, ToneMode: ToneMode,
    WordPopup: WordPopup, withWordClarity: withWordClarity, charClarity: charClarity, semiText: semiText,
    PROSODY_DEFAULTS: PROSODY_DEFAULTS, FusionPopup: FusionPopup, ScriptMarks: ScriptMarks, shiftDecided: shiftDecided, Lexicon: Lexicon,
    matchLexicon: matchLexicon, withFusion: withFusion, ttsText: ttsText, fusionZhuyinOk: fusionZhuyinOk,
    CLARITY_WARN_BELOW: CLARITY_WARN_BELOW, CLARITY_WARN_TEXT: CLARITY_WARN_TEXT };
})();
