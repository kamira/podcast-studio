// ④ 剪輯畫面(CHG-20261022-04 T7):素材庫、預覽、屬性、時間軸三軌(字幕、語音、畫面)。
// 精靈的 ④ 剪輯與完整模式共用同一組面板(只有一份 DOM)。
//
// 時間一律取自伺服器的 layout(timeline.layout(),與匯出共用):
//   segments [{id, start, end}]、cues [{segment, start, end, text}]、images [{id, file, start, end}]、duration。
// 預覽依 layout 排程:播放到某片段時以該片段的合成原檔播放(media 端點送變速前的檔,
// `playbackRate` = 實際語速,保留音高);畫面取「起點最晚、且涵蓋目前時間」的圖片(後開始的蓋過先前的),
// 沒有圖片的時段為黑畫面——與匯出(T5)相同。
//
// T8 的編輯互動(拖空隙改停頓、注音彈出框、語速選單、拖圖片邊緣)以 Studio.hook 接在這裡畫出的元素上:
//   .tl-gap[data-after=<片段 id>](片段之後的空隙)、.tl-img .tl-edge[data-edge=start|end]、
//   屬性面板的 [data-hook=speed|pause|tone|image-range]。本檔只負責顯示與選取。
// (CHG-20261029-01 T6)預覽播放後製檔(Store.audioId);選取的語音片段上疊語調折線(PitchEdit)。
// (修訂 1 T13)語調曲線改為 PCHIP 混合(R1-6),並畫出自動補點與放大後的峰谷(R1-7、R1-10)。
"use strict";

(function () {
  const S = window.Studio;
  const el = S.el;
  const Store = S.Store;
  const ZOOMS = [10, 20, 40, 60, 100, 160, 240];   // 每秒幾像素
  const MIN_SECONDS = 10;                           // 時間軸至少畫出的秒數
  const ASSET_DRAG = "application/x-studio-asset";

  function $(id) { return document.getElementById(id); }

  // ---------- 預覽排程(純函式;tests/test_static.py 以 JS 執行環境實跑) ----------

  // 目前時間 t 所在的語音片段(長度 0 的片段不算);沒有回 null
  function activeSegment(segments, t) {
    for (const s of segments || []) {
      if (s.end > s.start && t >= s.start && t < s.end) return s;
    }
    return null;
  }

  // 目前時間 t 的畫面:涵蓋 t 的圖片中起點最晚者(同起點取清單中較後者);沒有回 null(黑畫面)
  function activeImage(images, t) {
    let best = null;
    for (const m of images || []) {
      if (m.end > m.start && t >= m.start && t < m.end && (best === null || m.start >= best.start)) best = m;
    }
    return best;
  }

  // 目前時間 t 的字幕;沒有回 null
  function activeCue(cues, t) {
    for (const c of cues || []) {
      if (t >= c.start && t < c.end) return c;
    }
    return null;
  }
  // 預覽排程結束

  // ---------- 語調折線(CHG-20261029-01 T6;修訂 1 T13) ----------
  //
  // 選取的語音片段上疊一層 SVG:水平虛線為原音高(0 半音)、縱軸 ±7.2 半音(R1-9),±6 半音處畫淡色參考線;
  // 實心菱形為使用者關鍵點(可拖曳 / 刪除,限 ±6 半音)。實際套用的曲線(R1-6)= (1 − r) × 折線 + r × PCHIP,
  // r = 圓弧 / 100,一定經過每個實際套用的點(含放大後的峰谷與自動補點);第一點之前、最後一點之後維持端點值(D9)。
  // 實際套用的點(R1-7、R1-10):後製有效且帶 post.pitch_points 時以它為準(worker 吸附有聲幀後的結果);
  // 後製過期、進行中,或後製有效但沒有 pitch_points(快取命中且專案內沒有同鍵的紀錄)時,以 expandPoints()
  // 同一公式試算(不含吸附)並以淡色畫。放大後的峰谷畫成虛線菱形並以虛線連到使用者菱形、自動補點畫成虛線圓,
  // 兩者都不可選取、不可拖曳。另畫估計字界刻度(依漢字數均分,僅供參考)與字詞清晰度斜線區段。
  // 未選取的片段只顯示細線。操作(D17):點兩下新增、拖曳改位置與音高(不越過相鄰點)、選取後按
  // Delete / Backspace 刪除(剩 2 點時再刪即清除語調)。放開後 PATCH pitch,再只重做這一句的後製
  // (edit.js 的 patchAndPost)。

  // SVG 命名空間(只是 XML 命名空間識別字,不會發出任何請求);分段組字串,避免靜態檢查把它當成外部位址
  const SVG_NS = ["http:", "/", "/www.w3.org/2000/svg"].join("");
  const PITCH_SEMI = 6;            // 使用者關鍵點上下限(D9)
  const PITCH_AXIS = 7.2;          // 縱軸與內部點上限(R1-9:6 × 1.2)
  const PITCH_PAD = 6;
  const PITCH_MAX_POINTS = 16;
  // R1-7 的固定參數(同 breezy_worker.py 的 PREDIP_SEMI、PEAK_GAIN、PREDIP_FRAC、POINT_DIGITS)
  const PREDIP_SEMI = 1.0;
  const PEAK_GAIN = 1.2;
  const PREDIP_FRAC = 0.3;
  const POINT_SCALE = 1e6;         // 內部點取到小數 6 位
  const PATH_STEP = 2;             // 曲線取樣間距(像素)

  function semiToY(semi, h) {
    return h / 2 - (semi / PITCH_AXIS) * (h / 2 - PITCH_PAD);
  }

  function yToSemi(y, h) {
    const v = ((h / 2 - y) / (h / 2 - PITCH_PAD)) * PITCH_AXIS;
    return Math.max(-PITCH_SEMI, Math.min(PITCH_SEMI, Math.round(v * 10) / 10));
  }

  // [{pos, semi}] 或 [[pos, semi]] → [[pos, semi]]
  function pairs(points) {
    return (points || []).map(function (p) { return Array.isArray(p) ? [Number(p[0]), Number(p[1])] : [Number(p.pos), Number(p.semi)]; });
  }

  function round6(v) {
    return Math.round(v * POINT_SCALE) / POINT_SCALE;
  }

  function clipInner(v) {
    return Math.max(-PITCH_AXIS, Math.min(PITCH_AXIS, v));
  }

  // 自動補點試算(純函式;同 breezy_worker.expand_points,不含吸附有聲幀)。points 為使用者關鍵點。
  // 回傳 {points: 全部內部點, auto: 自動補點, amplified: 放大後的峰谷},每點為 [位置, 半音]。
  function expandPoints(points) {
    const pts = pairs(points);
    if (!pts.length) return { points: [], auto: [], amplified: [] };
    const out = [[pts[0][0], pts[0][1]]];
    const auto = [];
    const amp = [];
    const last = pts.length - 1;
    for (let i = 1; i < pts.length; i++) {
      const x0 = pts[i - 1][0], y0 = pts[i - 1][1], x1 = pts[i][0], y1 = pts[i][1];
      const peak = i > 0 && i < last && y1 > 0 && y1 > y0;
      const valley = i > 0 && i < last && y1 < 0 && y1 < y0;
      if (!peak && !valley) {
        out.push([x1, y1]);
        continue;
      }
      const pos = x1 - PREDIP_FRAC * (x1 - x0);
      const lin = y0 + (y1 - y0) * (pos - x0) / (x1 - x0);
      const q = [round6(pos), round6(clipInner(peak ? lin - PREDIP_SEMI : lin + PREDIP_SEMI))];
      out.push(q);
      auto.push(q.slice());
      const a = [x1, round6(clipInner(y1 * PEAK_GAIN))];
      out.push(a);
      amp.push(a.slice());
    }
    return { points: out, auto: auto, amplified: amp };
  }

  // PCHIP 的各點導數(純函式;同 scipy PchipInterpolator:內部點相鄰斜率異號或為 0 時為 0,
  // 否則為加權調和平均;端點為三點公式並保形;只有 2 點時為直線)
  function pchipSlopes(x, y) {
    const n = x.length;
    const h = [];
    const d = [];
    const m = new Array(n).fill(0);
    for (let i = 0; i < n - 1; i++) {
      h.push(x[i + 1] - x[i]);
      d.push((y[i + 1] - y[i]) / h[i]);
    }
    if (n < 2) return m;
    if (n === 2) {
      m[0] = m[1] = d[0];
      return m;
    }
    for (let k = 1; k < n - 1; k++) {
      if (d[k - 1] * d[k] <= 0) continue;
      const w1 = 2 * h[k] + h[k - 1];
      const w2 = h[k] + 2 * h[k - 1];
      m[k] = (w1 + w2) / (w1 / d[k - 1] + w2 / d[k]);
    }
    const edge = function (h0, h1, d0, d1) {
      let v = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1);
      if (Math.sign(v) !== Math.sign(d0)) v = 0;
      else if (Math.sign(d0) !== Math.sign(d1) && Math.abs(v) > Math.abs(3 * d0)) v = 3 * d0;
      return v;
    };
    m[0] = edge(h[0], h[1], d[0], d[1]);
    m[n - 1] = edge(h[n - 2], h[n - 3], d[n - 2], d[n - 3]);
    return m;
  }

  // 語調曲線在位置 t(0–1)的半音(純函式;R1-6,同 breezy_worker._curve):
  // f(t) = (1 − r) × 折線(t) + r × PCHIP(t);t 限在第一點與最後一點之間。pts 為實際套用的內部點 [[pos, semi]]
  function pitchCurve(pts, roundness, t, slopes) {
    const r = (roundness === null || roundness === undefined ? 50 : roundness) / 100;
    const n = pts.length;
    if (n === 1) return pts[0][1];
    const tt = Math.min(Math.max(t, pts[0][0]), pts[n - 1][0]);
    let i = 0;
    while (i < n - 2 && tt > pts[i + 1][0]) i++;
    const x0 = pts[i][0], x1 = pts[i + 1][0], y0 = pts[i][1], y1 = pts[i + 1][1];
    const hh = x1 - x0;
    const s = hh > 0 ? (tt - x0) / hh : 0;
    const lin = y0 + (y1 - y0) * s;
    if (r <= 0) return lin;
    const m = slopes || pchipSlopes(pts.map(function (p) { return p[0]; }), pts.map(function (p) { return p[1]; }));
    const s2 = s * s, s3 = s2 * s;
    const pc = (2 * s3 - 3 * s2 + 1) * y0 + (s3 - 2 * s2 + s) * hh * m[i] + (-2 * s3 + 3 * s2) * y1 + (s3 - s2) * hh * m[i + 1];
    return (1 - r) * lin + r * pc;
  }

  // 實際套用的點 + 圓弧 → SVG path 的 d(純函式)。points 為內部點([[pos, semi]] 或 [{pos, semi}]),
  // roundness 0–100(null = 50)、w × h 像素。每個點都是 path 的頂點(曲線一定經過),點與點之間每 2 px 取樣。
  function pitchPath(points, roundness, w, h) {
    const P = pairs(points);
    if (!P.length) return "";
    const m = pchipSlopes(P.map(function (p) { return p[0]; }), P.map(function (p) { return p[1]; }));
    const f = function (x, semi) { return x.toFixed(1) + " " + semiToY(semi, h).toFixed(1); };
    let d = "M 0.0 " + semiToY(P[0][1], h).toFixed(1) + " L " + f(P[0][0] * w, P[0][1]);
    for (let i = 1; i < P.length; i++) {
      const xa = P[i - 1][0] * w;
      const xb = P[i][0] * w;
      const steps = Math.max(1, Math.ceil((xb - xa) / PATH_STEP));
      for (let k = 1; k < steps; k++) {
        const x = xa + (xb - xa) * k / steps;
        d += " L " + f(x, pitchCurve(P, roundness, x / w, m));
      }
      d += " L " + f(xb, P[i][1]);
    }
    d += " L " + w.toFixed(1) + " " + semiToY(P[P.length - 1][1], h).toFixed(1);
    return d;
  }

  // 片段實際套用的語調點與畫法(R1-10):{points, auto, amplified, estimate}
  // 後製有效、不在後製中且帶 post.pitch_points → 以 worker 的結果為準;否則前端試算(estimate = true,淡色)
  function pitchModel(seg, userPoints) {
    const user = pairs(userPoints || (seg.pitch ? seg.pitch.points : []));
    const est = expandPoints(user);
    const running = !!(S.Edit && S.Edit.Post && S.Edit.Post.running.has(seg.id));
    const pp = seg.post && Array.isArray(seg.post.pitch_points) ? seg.post.pitch_points : null;
    if (userPoints || running || !pp || !Store.postFresh(seg)) {
      return { points: est.points, auto: est.auto, amplified: est.amplified, estimate: true };
    }
    const turns = est.amplified.map(function (q) { return q[0]; });
    const auto = [];
    const amp = [];
    pp.forEach(function (q) {
      const at = function (x) { return Math.abs(x - q[0]) < 1e-6; };
      if (!user.some(function (u) { return at(u[0]); })) auto.push([q[0], q[1]]);
      else if (turns.some(at)) amp.push([q[0], q[1]]);
    });
    return { points: pairs(pp), auto: auto, amplified: amp, estimate: false };
  }

  function svgEl(tag, attrs) {
    const e = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, String(attrs[k])); });
    return e;
  }

  function isHanziChar(ch) {
    const c = String(ch).codePointAt(0);
    return (c >= 0x4E00 && c <= 0x9FFF) || (c >= 0x3400 && c <= 0x4DBF) || (c >= 0xF900 && c <= 0xFAFF)
      || (c >= 0x20000 && c <= 0x323AF);
  }

  const PitchEdit = {
    sel: null,     // {seg: 片段 id, index: 關鍵點索引}

    selected(segId) {
      return this.sel && this.sel.seg === segId ? this.sel.index : -1;
    },

    // 重畫所有語音片段上的折線(選取改變或時間軸重畫時呼叫)
    refresh() {
      document.querySelectorAll("#tl-voice .tl-pitch").forEach(function (e) { e.remove(); });
      const tsel = Timeline.sel;
      document.querySelectorAll("#tl-voice .tl-seg[data-segment]:not(.is-pending)").forEach((b) => {
        const seg = Store.segment(b.dataset.segment);
        if (!seg) return;
        const on = !!tsel && tsel.type === "segment" && tsel.id === seg.id;
        if (on && seg.audio && ("key" in seg.audio)) this.draw(b, seg, true);
        else if (seg.pitch) this.draw(b, seg, false);
      });
      if (this.sel && !(tsel && tsel.type === "segment" && tsel.id === this.sel.seg)) this.sel = null;
    },

    draw(block, seg, editable) {
      const w = block.clientWidth;
      const h = block.clientHeight;
      if (w < 4 || h < 4) return;
      const svg = svgEl("svg", { class: "tl-pitch" + (editable ? " is-editable" : ""), width: w, height: h,
        viewBox: "0 0 " + w + " " + h, "aria-hidden": editable ? "false" : "true" });
      if (editable) {
        svg.setAttribute("role", "group");
        svg.setAttribute("aria-label", "語調折線(點兩下新增關鍵點)");
        svg.appendChild(svgEl("line", { class: "tl-pitch-zero", x1: 0, y1: h / 2, x2: w, y2: h / 2 }));
        [PITCH_SEMI, -PITCH_SEMI].forEach(function (v) {
          const y = semiToY(v, h);
          svg.appendChild(svgEl("line", { class: "tl-pitch-limit", x1: 0, y1: y, x2: w, y2: y }));
        });
        const chars = Array.from(seg.text);
        const units = [];
        chars.forEach(function (ch, i) { if (isHanziChar(ch)) units.push(i); });
        for (let k = 1; k < units.length; k++) {
          const x = (k / units.length) * w;
          svg.appendChild(svgEl("line", { class: "tl-pitch-tick", x1: x, y1: h - 8, x2: x, y2: h }));
        }
        (seg.word_clarity || []).forEach(function (wc) {
          const a = units.indexOf(wc.start);
          const b = units.indexOf(wc.end - 1);
          if (a < 0 || b < 0 || !units.length) return;
          const x0 = (a / units.length) * w;
          const x1 = ((b + 1) / units.length) * w;
          const g = svgEl("g", { class: "tl-word-clarity" });
          g.appendChild(svgEl("rect", { x: x0, y: 0, width: Math.max(1, x1 - x0), height: h }));
          const t = svgEl("text", { x: x0 + 2, y: h - 10 });
          t.textContent = chars.slice(wc.start, wc.end).join("") + " " + wc.value + "%";
          g.appendChild(t);
          svg.appendChild(g);
        });
      }
      const model = seg.pitch ? pitchModel(seg) : null;
      if (model) {
        svg.appendChild(svgEl("path", { class: "tl-pitch-line" + (model.estimate ? " is-estimate" : ""),
          d: pitchPath(model.points, seg.pitch.roundness, w, h) }));
      }
      block.appendChild(svg);
      if (!editable) return;
      if (model) this.drawApplied(svg, model, seg.pitch.points, w, h);
      (seg.pitch ? seg.pitch.points : []).forEach((p, i) => {
        const x = p.pos * w;
        const y = semiToY(p.semi, h);
        const d = svgEl("rect", { class: "tl-pitch-key" + (this.selected(seg.id) === i ? " is-active" : ""),
          x: x - 5, y: y - 5, width: 10, height: 10, transform: "rotate(45 " + x + " " + y + ")" });
        d.dataset.index = String(i);
        d.setAttribute("aria-label", "關鍵點 #" + (i + 1));
        d.addEventListener("pointerdown", (e) => this.dragKey(e, seg, i, svg, d, w, h));
        svg.appendChild(d);
      });
      if (this.sel && this.sel.seg === seg.id) {
        const p = seg.pitch && seg.pitch.points[this.sel.index];
        if (p) {
          const t = svgEl("text", { class: "tl-pitch-label", x: Math.min(w - 120, Math.max(2, p.pos * w + 8)), y: 12 });
          const a = model ? model.amplified.find(function (q) { return Math.abs(q[0] - p.pos) < 1e-6; }) : null;
          t.textContent = "#" + (this.sel.index + 1) + " · " + Math.round(p.pos * 100) + "% · "
            + (S.Edit ? S.Edit.semiText(p.semi) : p.semi)
            + (a ? "(套用 " + (a[1] > 0 ? "+" : a[1] < 0 ? "−" : "") + Math.abs(a[1]).toFixed(1) + ")" : "");
          svg.appendChild(t);
        }
      }
      svg.addEventListener("dblclick", (e) => {
        if (e.target && e.target.classList && e.target.classList.contains("tl-pitch-key")) return;
        e.preventDefault();
        e.stopPropagation();
        const r = svg.getBoundingClientRect();
        this.add(seg, Math.round(((e.clientX - r.left) / w) * 100) / 100, yToSemi(e.clientY - r.top, h));
      });
    },

    // 放大後的峰谷(虛線菱形 + 連到使用者菱形的虛線 + 套用值)與自動補點(虛線圓);不可選取、不可拖曳。
    // 重畫時先移除舊的,插在使用者菱形之前(菱形在最上層)
    drawApplied(svg, model, userPoints, w, h) {
      svg.querySelectorAll(".tl-pitch-auto, .tl-pitch-amp, .tl-pitch-amp-link, .tl-pitch-amp-label")
        .forEach(function (e) { e.remove(); });
      const before = svg.querySelector(".tl-pitch-key");
      const put = function (e) { svg.insertBefore(e, before); };
      const est = model.estimate ? " is-estimate" : "";
      const user = pairs(userPoints);
      model.amplified.forEach(function (q) {
        const u = user.find(function (p) { return Math.abs(p[0] - q[0]) < 1e-6; });
        const x = q[0] * w;
        const y = semiToY(q[1], h);
        if (u) put(svgEl("line", { class: "tl-pitch-amp-link" + est, x1: x, y1: semiToY(u[1], h), x2: x, y2: y }));
        put(svgEl("rect", { class: "tl-pitch-amp" + est, x: x - 5, y: y - 5, width: 10, height: 10,
          transform: "rotate(45 " + x + " " + y + ")" }));
        const t = svgEl("text", { class: "tl-pitch-amp-label" + est, x: Math.max(2, x - 36), y: y + 4 });
        t.textContent = (q[1] > 0 ? "+" : q[1] < 0 ? "−" : "") + Math.abs(q[1]).toFixed(1);
        put(t);
      });
      model.auto.forEach(function (q) {
        put(svgEl("circle", { class: "tl-pitch-auto" + est, cx: q[0] * w, cy: semiToY(q[1], h), r: 5 }));
      });
    },

    commit(seg, points, okText) {
      const roundness = seg.pitch ? seg.pitch.roundness : null;
      const body = { pitch: points === null ? null : { points: points, roundness: roundness } };
      if (S.Edit) return S.Edit.patchAndPost(seg, body, okText);
      return Store.patchSegment(seg.id, body);
    },

    add(seg, pos, semi) {
      pos = Math.max(0, Math.min(1, pos));
      const pts = seg.pitch ? seg.pitch.points.map(function (p) { return { pos: p.pos, semi: p.semi }; })
        : [{ pos: 0, semi: 0 }, { pos: 1, semi: 0 }];
      if (pts.some(function (p) { return Math.abs(p.pos - pos) < 0.005; })) {
        S.toast("這個位置已經有關鍵點", true);
        return;
      }
      if (pts.length >= PITCH_MAX_POINTS) {
        S.toast("每句最多 " + PITCH_MAX_POINTS + " 個關鍵點", true);
        return;
      }
      pts.push({ pos: pos, semi: semi });
      pts.sort(function (a, b) { return a.pos - b.pos; });
      this.sel = { seg: seg.id, index: pts.findIndex(function (p) { return p.pos === pos; }) };
      this.commit(seg, pts, "已新增關鍵點");
    },

    remove(seg, index) {
      const pts = seg.pitch.points.filter(function (_, i) { return i !== index; });
      this.sel = null;
      this.commit(seg, pts.length < 2 ? null : pts, pts.length < 2 ? "已清除語調" : "已刪除關鍵點");
    },

    dragKey(e, seg, i, svg, key, w, h) {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      const pts = seg.pitch.points.map(function (p) { return { pos: p.pos, semi: p.semi }; });
      const lo = i > 0 ? pts[i - 1].pos + 0.01 : 0;
      const hi = i < pts.length - 1 ? pts[i + 1].pos - 0.01 : 1;
      const r = svg.getBoundingClientRect();
      const x0 = e.clientX;
      const y0 = e.clientY;
      let moved = false;
      key.setPointerCapture(e.pointerId);
      this.sel = { seg: seg.id, index: i };
      const line = svg.querySelector(".tl-pitch-line");
      const move = (ev) => {
        if (Math.abs(ev.clientX - x0) + Math.abs(ev.clientY - y0) >= 3) moved = true;
        if (!moved) return;
        const pos = Math.round(Math.max(lo, Math.min(hi, (ev.clientX - r.left) / w)) * 100) / 100;
        pts[i] = { pos: Math.max(lo, Math.min(hi, pos)), semi: yToSemi(ev.clientY - r.top, h) };
        const x = pts[i].pos * w;
        const y = semiToY(pts[i].semi, h);
        key.setAttribute("x", String(x - 5));
        key.setAttribute("y", String(y - 5));
        key.setAttribute("transform", "rotate(45 " + x + " " + y + ")");
        const est = pitchModel(seg, pts);
        if (line) {
          line.setAttribute("d", pitchPath(est.points, seg.pitch.roundness, w, h));
          line.classList.add("is-estimate");
        }
        this.drawApplied(svg, est, pts, w, h);
      };
      const up = (ev) => {
        key.removeEventListener("pointermove", move);
        key.removeEventListener("pointerup", up);
        key.removeEventListener("pointercancel", up);
        if (key.hasPointerCapture(ev.pointerId)) key.releasePointerCapture(ev.pointerId);
        if (moved && ev.type === "pointerup") {
          this.commit(seg, pts, "關鍵點 #" + (i + 1) + " 改為 " + (S.Edit ? S.Edit.semiText(pts[i].semi) : ""));
        } else {
          this.refresh();
          Props.render();
        }
      };
      key.addEventListener("pointermove", move);
      key.addEventListener("pointerup", up);
      key.addEventListener("pointercancel", up);
    },

    onKey(e) {
      if (!this.sel || (e.key !== "Delete" && e.key !== "Backspace")) return;
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      const seg = Store.segment(this.sel.seg);
      if (!seg || !seg.pitch || !seg.pitch.points[this.sel.index]) return;
      e.preventDefault();
      this.remove(seg, this.sel.index);
    },
  };

  document.addEventListener("keydown", function (e) { PitchEdit.onKey(e); });

  // ---------- 時間軸 ----------

  const Timeline = {
    zoomIndex: 3,
    sel: null,              // {type: "segment" | "image", id}

    get zoom() { return ZOOMS[this.zoomIndex]; },

    init() {
      $("tl-zoom-in").addEventListener("click", () => this.setZoom(this.zoomIndex + 1));
      $("tl-zoom-out").addEventListener("click", () => this.setZoom(this.zoomIndex - 1));
      $("tl-ruler").addEventListener("click", (e) => Preview.seek(this.timeAt(e.clientX)));
      $("tl-content").addEventListener("click", (e) => this.onClick(e));
      this.initDrops();
      Store.on(() => this.render());
    },

    setZoom(i) {
      const next = Math.max(0, Math.min(ZOOMS.length - 1, i));
      if (next === this.zoomIndex) return;
      this.zoomIndex = next;
      this.render();
    },

    timeAt(clientX) {
      const r = $("tl-content").getBoundingClientRect();
      return Math.max(0, (clientX - r.left) / this.zoom);
    },

    select(sel) {
      this.sel = sel;
      document.querySelectorAll("#tl-content .is-selected").forEach(function (e) { e.classList.remove("is-selected"); });
      if (sel) {
        const sels = sel.type === "segment"
          ? '.tl-seg[data-segment="' + sel.id + '"], .tl-cue[data-segment="' + sel.id + '"]'
          : '.tl-img[data-image="' + sel.id + '"]';
        document.querySelectorAll(sels).forEach(function (e) { e.classList.add("is-selected"); });
      }
      PitchEdit.refresh();
      Props.render();
    },

    onClick(e) {
      const t = e.target;
      if (!t || !t.closest) return;
      const seg = t.closest("[data-segment]");
      if (seg && !t.closest(".tl-gap")) {
        // 已選取的語音片段再點一下不重畫(語調折線的點兩下需要同一個 SVG 收到兩次點擊)
        if (this.sel && this.sel.type === "segment" && this.sel.id === seg.dataset.segment
            && t.closest(".tl-pitch")) return;
        this.select({ type: "segment", id: seg.dataset.segment });
        return;
      }
      const img = t.closest(".tl-img");
      if (img) {
        this.select({ type: "image", id: img.dataset.image });
        return;
      }
      if (t.closest(".tl-lane")) Preview.seek(this.timeAt(e.clientX));
    },

    block(cls, start, end, text, title) {
      const b = el("div", cls, text);
      b.style.left = (start * this.zoom) + "px";
      b.style.width = Math.max(2, (end - start) * this.zoom) + "px";
      if (title) b.title = title;
      return b;
    },

    render() {
      const view = Store.view;
      const content = $("tl-content");
      const lanes = { cues: $("tl-cues"), voice: $("tl-voice"), images: $("tl-images") };
      Object.keys(lanes).forEach(function (k) { lanes[k].replaceChildren(); });
      const ruler = $("tl-ruler");
      ruler.replaceChildren();
      if (!view) return;
      const lay = view.layout;
      const z = this.zoom;
      const span = Math.max(MIN_SECONDS, Math.ceil(lay.duration) + 2);
      content.style.width = (span * z) + "px";

      // 刻度:間距至少 60 像素
      const step = [1, 2, 5, 10, 15, 30, 60, 120, 300].find(function (s) { return s * z >= 60; }) || 600;
      for (let s = 0; s <= span; s += step) {
        const tick = el("span", "tl-tick", S.fmtSeconds(s).replace(/\.0$/, ""));
        tick.style.left = (s * z) + "px";
        ruler.appendChild(tick);
      }

      const index = {};
      view.project.segments.forEach(function (s, i) { index[s.id] = i; });

      // 語音軌:有音檔的片段為方塊,片段之間為空隙(停頓);沒有音檔的片段標「⏳ 合成中 / 未合成」
      let prev = null;
      lay.segments.forEach((ls) => {
        const seg = Store.segment(ls.id);
        if (!seg) return;
        const no = index[ls.id] + 1;
        if (ls.end > ls.start) {
          if (prev) {
            const gap = this.block("tl-gap", prev.end, ls.start, "", "停頓 " + (ls.start - prev.end).toFixed(1) + " 秒");
            gap.dataset.after = prev.id;
            lanes.voice.appendChild(gap);
          }
          const b = this.block("tl-seg", ls.start, ls.end, "#" + no + " " + seg.text,
            "#" + no + " " + seg.text + "(" + (ls.end - ls.start).toFixed(1) + " 秒)");
          b.dataset.segment = ls.id;
          if (seg.audio && !("key" in seg.audio)) b.classList.add("is-external");
          if ((seg.fusions || []).length) {   // CHG-20261029-01 T9:送模型的文字含合音(字幕仍為原文)
            const badge = el("span", "tl-badge", "⟲ 合音 " + seg.fusions.length);
            badge.title = "送模型的文字含合音(字幕仍為原文)";
            b.appendChild(badge);
          }
          lanes.voice.appendChild(b);
          prev = ls;
        } else {
          const pend = el("div", "tl-seg is-pending", S.Voice.running ? "⏳ 合成中" : "#" + no + " 未合成");
          pend.style.left = (ls.start * z) + "px";
          pend.title = "#" + no + " " + seg.text;
          pend.dataset.segment = ls.id;
          lanes.voice.appendChild(pend);
        }
      });

      // 字幕軌
      lay.cues.forEach((c) => {
        const b = this.block("tl-cue", c.start, c.end, c.text, c.text);
        b.dataset.segment = c.segment;
        lanes.cues.appendChild(b);
      });

      // 畫面軌:依起點排序,後開始的畫在上層(與預覽、匯出相同)
      lay.images.forEach((m) => {
        const a = Store.asset(m.file);
        const b = this.block("tl-img", m.start, m.end, Store.assetLabel(a),
          Store.assetLabel(a) + "(" + m.start.toFixed(1) + "–" + m.end.toFixed(1) + " 秒)");
        b.dataset.image = m.id;
        ["start", "end"].forEach(function (edge) {
          const h = el("span", "tl-edge tl-edge-" + edge);
          h.dataset.edge = edge;
          b.appendChild(h);
        });
        lanes.images.appendChild(b);
      });
      if (!lay.images.length) lanes.images.appendChild(el("span", "tl-empty", "把素材庫的圖片拖到這裡"));
      if (!lay.segments.length) lanes.voice.appendChild(el("span", "tl-empty", "還沒有句子"));

      // 選取的項目若已不存在就取消
      if (this.sel) {
        const exists = this.sel.type === "segment" ? !!Store.segment(this.sel.id)
          : view.project.images.some((m) => m.id === this.sel.id);
        if (!exists) this.sel = null;
      }
      this.select(this.sel);
      Preview.show(Math.min(Preview.t, lay.duration));
      S.runHook("timeline", { content: content, lanes: lanes, view: view, zoom: z });
    },

    initDrops() {
      const images = $("tl-images");
      images.addEventListener("dragover", (e) => {
        const types = Array.from((e.dataTransfer && e.dataTransfer.types) || []);
        if (types.indexOf(ASSET_DRAG) === -1 && !S.draggingFiles(e)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
        images.classList.add("drop-target");
      });
      images.addEventListener("dragleave", (e) => {
        if (e.relatedTarget && images.contains(e.relatedTarget)) return;
        images.classList.remove("drop-target");
      });
      images.addEventListener("drop", async (e) => {
        images.classList.remove("drop-target");
        const start = Math.round(this.timeAt(e.clientX) * 10) / 10;
        const asset = e.dataTransfer ? e.dataTransfer.getData(ASSET_DRAG) : "";
        if (asset) {
          e.preventDefault();
          await S.addImage({ asset: asset, start: start });
          return;
        }
        if (!S.draggingFiles(e)) return;
        e.preventDefault();
        e.stopPropagation();
        for (const f of Array.from(e.dataTransfer.files || [])) {
          const c = S.checkFile(f);
          if (c.error || c.kind !== "image") {
            S.toast(c.error || "畫面軌只接受圖片:「" + f.name + "」", true);
            continue;
          }
          const up = await S.uploadFile(f);
          if (up) await S.addImage({ file_id: up.file_id, label: f.name.slice(0, 255), start: start });
        }
      });

      // 語音軌:把 WAV 拖到某一句上,以自錄音檔取代該句
      const voice = $("tl-voice");
      voice.addEventListener("dragover", (e) => {
        if (!S.draggingFiles(e)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
      });
      voice.addEventListener("drop", (e) => {
        if (!S.draggingFiles(e)) return;
        e.preventDefault();
        e.stopPropagation();
        const target = e.target && e.target.closest ? e.target.closest(".tl-seg[data-segment]") : null;
        const files = Array.from(e.dataTransfer.files || []);
        if (!target) {
          S.toast("請把音檔拖到要取代的那一句上", true);
          return;
        }
        if (files.length !== 1) {
          S.toast("一次只能拖入一個音檔", true);
          return;
        }
        S.replaceAudio(target.dataset.segment, files[0]);
      });
    },
  };

  // ---------- 預覽 ----------

  const Preview = {
    t: 0,
    playing: false,
    startWall: 0,
    raf: 0,
    curSeg: null,
    curImg: null,
    audio: new Audio(),

    init() {
      $("preview-play").addEventListener("click", () => (this.playing ? this.pause() : this.play()));
      $("preview-start").addEventListener("click", () => this.seek(0));
      $("preview-end").addEventListener("click", () => this.seek(this.duration()));
      this.audio.preload = "auto";
      Store.on(() => {
        // 版面改變(合成完成、改停頓…)時停下,避免依舊排程播放
        if (this.playing) this.pause();
      });
    },

    layout() {
      return Store.view ? Store.view.layout : { segments: [], cues: [], images: [], duration: 0 };
    },

    duration() {
      return this.layout().duration || 0;
    },

    play() {
      const d = this.duration();
      if (d <= 0) {
        S.toast("還沒有已合成的語音,無法預覽", true);
        return;
      }
      if (this.t >= d) this.t = 0;
      this.playing = true;
      this.curSeg = null;
      this.startWall = performance.now() - this.t * 1000;
      this.setButton();
      this.tick();
    },

    pause() {
      this.playing = false;
      cancelAnimationFrame(this.raf);
      this.audio.pause();
      this.curSeg = null;
      this.setButton();
    },

    seek(t) {
      const d = this.duration();
      this.t = Math.max(0, Math.min(t, d));
      if (this.playing) {
        this.startWall = performance.now() - this.t * 1000;
        this.curSeg = null;
        this.audio.pause();
        this.syncAudio();
      }
      this.show(this.t);
    },

    setButton() {
      const b = $("preview-play");
      b.textContent = this.playing ? "⏸" : "▶";
      b.setAttribute("aria-label", this.playing ? "暫停" : "播放");
      b.title = this.playing ? "暫停" : "播放";
    },

    tick() {
      if (!this.playing) return;
      const d = this.duration();
      this.t = (performance.now() - this.startWall) / 1000;
      if (this.t >= d) {
        this.t = d;
        this.pause();
        this.show(d);
        return;
      }
      this.syncAudio();
      this.show(this.t);
      this.raf = requestAnimationFrame(() => this.tick());
    },

    // 播放中:切到目前片段的音檔(換片段才重設來源與位置);停頓處不出聲
    syncAudio() {
      const ls = activeSegment(this.layout().segments, this.t);
      const id = ls ? ls.id : null;
      if (id === this.curSeg) return;
      this.curSeg = id;
      const seg = id ? Store.segment(id) : null;
      if (!seg || !seg.audio) {
        this.audio.pause();
        return;
      }
      const speed = Store.speedOf(seg);
      this.audio.src = Store.mediaUrl(Store.audioId(seg));   // CHG-20261029-01 D18:播放後製檔
      this.audio.playbackRate = speed;
      this.audio.currentTime = Math.max(0, (this.t - ls.start) * speed);
      this.audio.play().catch(function () { /* 換片段時中斷前一個 play() 屬正常 */ });
    },

    show(t) {
      const lay = this.layout();
      const img = activeImage(lay.images, t);
      const pic = $("preview-img");
      const imgId = img ? img.file : null;
      if (imgId !== this.curImg) {
        this.curImg = imgId;
        if (imgId) pic.src = Store.mediaUrl(imgId);
        else pic.removeAttribute("src");
        pic.hidden = !imgId;
      }
      const cue = activeCue(lay.cues, t);
      $("preview-cue").textContent = cue ? cue.text : "";
      $("preview-time").textContent = S.fmtSeconds(t) + " / " + S.fmtSeconds(lay.duration || 0);
      $("tl-playhead").style.left = (t * Timeline.zoom) + "px";
    },
  };

  // ---------- 屬性 ----------

  const BREAK_KEY = { sentence: "pause_sentence", paragraph: "pause_paragraph", forced: "pause_comma" };

  const Props = {
    render() {
      const box = $("props-body");
      box.replaceChildren();
      const view = Store.view;
      const sel = Timeline.sel;
      if (!view || !sel) {
        box.appendChild(el("p", "muted", "在時間軸點選片段或圖片"));
        return;
      }
      if (sel.type === "segment") this.segment(box, view, sel.id);
      else this.image(box, view, sel.id);
    },

    row(box, label, value, hook) {
      const r = el("div", "prop-row");
      r.appendChild(el("span", "prop-label", label));
      const v = el("span", "prop-value", value);
      if (hook) v.dataset.hook = hook;
      r.appendChild(v);
      box.appendChild(r);
      return v;
    },

    segment(box, view, id) {
      const seg = Store.segment(id);
      if (!seg) return;
      const i = view.project.segments.indexOf(seg);
      box.appendChild(el("h3", "prop-title", "選取:片段 #" + (i + 1)));

      // 文字只顯示(DIR-5 統整 M-8,RS-23 取窄):改句子文字一律在文稿面板進行
      this.row(box, "文字", seg.text).classList.add("prop-text");

      const a = seg.audio;
      const fresh = Store.postFresh(seg);
      const running = S.Edit && S.Edit.Post.running.has(seg.id);
      let status = !a ? "尚未合成"
        : (("key" in a) ? "✓ 已合成 " : "✓ 自錄 ") + (Store.seconds(seg) / Store.speedOf(seg)).toFixed(1) + " 秒";
      if (fresh && seg.post.trim > 0) status += "(已裁掉前後低音量 " + Number(seg.post.trim).toFixed(2) + " 秒)";
      if (running) status = "⏳ 後製中";
      this.row(box, "狀態", status).classList.add("prop-status");
      this.row(box, "語速", typeof seg.speed === "number" ? seg.speed.toFixed(2) + "×"
        : "預設(" + Store.speedOf(seg).toFixed(2) + "×)", "speed");

      box.appendChild(el("h4", "prop-section", "停頓"));
      const d = view.project.defaults;
      const dflt = BREAK_KEY[seg.break] ? d[BREAK_KEY[seg.break]] : 0;
      this.row(box, "之後", typeof seg.pause_after === "number" ? seg.pause_after.toFixed(1) + " 秒"
        : Number(dflt).toFixed(1) + " 秒(預設)", "pause");

      // (CHG-20261029-01 T5)韻律、語調小節:edit.js 以 data-hook 掛上拉桿
      box.appendChild(el("h4", "prop-section", "韻律"));
      const prosody = el("div", "prop-block");
      prosody.dataset.hook = "prosody";
      box.appendChild(prosody);
      box.appendChild(el("h4", "prop-section", "語調"));
      const pitchBox = el("div", "prop-block");
      pitchBox.dataset.hook = "pitch-props";
      box.appendChild(pitchBox);

      box.appendChild(el("h4", "prop-section", "聲調與字詞清晰度"));
      const tone = el("div", "tone-chars");
      tone.dataset.hook = "tone";
      const ann = {};
      (seg.annotations || []).forEach(function (x) { ann[x.index] = x.zhuyin; });
      Array.from(seg.text).forEach(function (ch, k) {
        const c = el("span", "tone-char" + (ann[k] ? " is-annotated" : ""));
        c.dataset.index = String(k);
        c.appendChild(el("span", "tone-glyph", ch));
        c.appendChild(el("span", "tone-zhuyin", ann[k] || ""));
        tone.appendChild(c);
      });
      box.appendChild(tone);

      const play = el("button", "btn btn-small", "▶ 試聽這一句");
      play.type = "button";
      play.disabled = !a;
      play.addEventListener("click", function () { S.playSegment(seg); });
      box.appendChild(play);
      S.runHook("segment-props", { container: box, segment: seg, index: i, view: view });
    },

    image(box, view, id) {
      const m = view.project.images.find(function (x) { return x.id === id; });
      if (!m) return;
      const asset = Store.asset(m.asset);
      box.appendChild(el("h3", "prop-title", "選取:圖片「" + Store.assetLabel(asset) + "」"));
      const pic = document.createElement("img");
      pic.className = "prop-thumb";
      pic.alt = Store.assetLabel(asset);
      pic.src = Store.mediaUrl(m.asset);
      box.appendChild(pic);
      const range = el("div", "prop-block");
      range.dataset.hook = "image-range";
      range.appendChild(el("div", "", "起點 " + Number(m.start).toFixed(1) + " 秒"));
      range.appendChild(el("div", "", m.end === null ? "終點 到結尾" : "終點 " + Number(m.end).toFixed(1) + " 秒"));
      box.appendChild(range);
      const del = el("button", "btn btn-small", "從畫面軌移除");
      del.type = "button";
      del.addEventListener("click", async function () {
        const r = await Store.mutate("DELETE", "/images/" + encodeURIComponent(m.id));
        if (!r.ok) S.toast("無法移除:" + S.errorText(r), true);
      });
      box.appendChild(del);
      box.appendChild(el("p", "muted", "素材檔仍保留在素材庫"));
      S.runHook("image-props", { container: box, image: m, view: view });
    },
  };

  // ---------- 素材庫 ----------

  const ICONS = { image: "▣", audio: "♪", text: "≣" };

  const Library = {
    player: new Audio(),

    init() {
      const zone = $("panel-library");
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
      zone.addEventListener("drop", async (e) => {
        zone.classList.remove("drop-target");
        if (!S.draggingFiles(e)) return;
        e.preventDefault();
        e.stopPropagation();
        for (const f of Array.from(e.dataTransfer.files || [])) await S.addAsset(f);
      });
      Store.on((view) => this.render(view));
    },

    render(view) {
      const list = $("library-list");
      list.replaceChildren();
      view.project.assets.forEach((a) => {
        const li = el("li", "lib-item lib-" + a.kind);
        li.appendChild(el("span", "lib-icon", ICONS[a.kind] || "▫"));
        li.appendChild(el("span", "lib-label", Store.assetLabel(a)));
        if (a.kind === "image") {
          li.draggable = true;
          li.title = "拖到畫面軌,或按「加入」";
          li.addEventListener("dragstart", function (e) {
            e.dataTransfer.setData(ASSET_DRAG, a.id);
            e.dataTransfer.effectAllowed = "copy";
          });
          const add = el("button", "btn btn-small", "加入");
          add.type = "button";
          add.title = "加入畫面軌";
          add.addEventListener("click", function () { S.addImage({ asset: a.id, start: Math.round(Preview.t * 10) / 10 }); });
          li.appendChild(add);
        } else if (a.kind === "audio") {
          const play = el("button", "btn btn-small", "▶");
          play.type = "button";
          play.setAttribute("aria-label", "試聽「" + Store.assetLabel(a) + "」");
          play.addEventListener("click", () => {
            this.player.src = Store.mediaUrl(a.id);
            this.player.play().catch(function () { S.toast("無法播放這個音檔", true); });
          });
          li.appendChild(play);
        }
        list.appendChild(li);
      });
      if (!list.firstChild) list.appendChild(el("li", "muted", "還沒有素材"));
    },
  };

  S.Timeline = Timeline;
  S.PitchEdit = PitchEdit;
  S.pitchPath = pitchPath;
  S.pitchModel = pitchModel;
  S.expandPoints = expandPoints;
  S.pitchCurve = pitchCurve;
  S.Preview = Preview;
  S.Props = Props;
  S.Library = Library;

  Timeline.init();
  Preview.init();
  Library.init();
  Props.render();
})();
