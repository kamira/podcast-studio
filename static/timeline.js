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
      Props.render();
    },

    onClick(e) {
      const t = e.target;
      if (!t || !t.closest) return;
      const seg = t.closest("[data-segment]");
      if (seg && !t.closest(".tl-gap")) {
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
  S.Preview = Preview;
  S.Props = Props;
  S.Library = Library;

  Timeline.init();
  Preview.init();
  Library.init();
  Props.render();
})();
