// War room: the side panels: pop-out, uploads, the schedule (drag to move), cover editor, analytics, costs, Modal.

// ---------- pop-out (#26) ----------

function popOut(ref) {
  const i = STATE.items.find(x => x.ref === ref);
  if (!i?.video_url) return;
  const v = OPEN === ref ? $("#modal video") : null;
  const t = v ? v.currentTime : 0;
  if (v) v.pause();
  window.open(`/watch?src=${encodeURIComponent(i.video_url)}&t=${t.toFixed(1)}&title=${encodeURIComponent(i.short + " · " + i.title)}`,
    `clipper-watch-${ref.replace(/\W/g, "_")}`, "popup,width=960,height=560");
}

// ---------- uploads (#21) ----------

function renderUploads() {
  const u = STATE.uploads, el = $("#uploads");
  if (!u) { el.innerHTML = ""; return; }
  const tz = STATE.timezone, hm = iso => new Date(iso).toLocaleTimeString([], { timeZone: tz, hour: "2-digit", minute: "2-digit" });
  const pct = u.max_per_24h ? Math.min(100, 100 * u.last_24h / u.max_per_24h) : 0;
  const r = u.last_run || {};
  const runAt = r.at ? new Date(r.at.replace(" ", "T")) : null;
  const logLine = x => `<div class="${/error|Error|failed|Traceback|Exceeded/.test(x) ? "bad" : /youtu\.be/.test(x) ? "ok" : ""}">${esc(x.trim())}</div>`;
  el.innerHTML = `
    <div class="cap"><b style="color:var(--text)">Uploads</b> ${u.last_24h} / ${u.max_per_24h} in 24 h
      <span class="meter"><i class="${u.room ? "" : "full"}" style="width:${pct}%"></i></span>
      ${u.room ? `${u.room} more now` : u.frees_at ? `next room ${hm(u.frees_at)}` : "at the cap"}</div>
    <div>${u.auto_minutes ? `Automatic every ${u.auto_minutes} min` : "By hand (clipper upload)"}${r.running_since
      ? ` · <span style="color:var(--accent)">uploading now (since ${esc(r.running_since.slice(11, 16))})</span>` : ""}${runAt
      ? ` · last run ${ago(runAt.toISOString())}, ${r.exit === "0" ? `<span style="color:var(--green)">ok</span>` : `<span style="color:var(--red)">exit ${esc(r.exit)}</span>`}` : ""}</div>
    ${r.lines?.length ? `<div class="log">${r.lines.map(logLine).join("")}</div>` : ""}
    ${u.next.length ? `<div class="nx">${u.next.map(e => `<span class="${e.due ? "due" : ""}">${new Date(e.publish_at).toLocaleString([], { timeZone: tz, weekday: "short", hour: "2-digit", minute: "2-digit" })}</span><span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${esc(e.title)}">${esc(e.title)}</span>`).join("")}</div>` : ""}`;
}

// ---------- #20 drag and drop on the schedule ----------

let SHOWFREE = (() => { try { return localStorage.getItem("warroom.free") !== "0"; } catch { return true; } })();
let DRAG = null;
function localPin(iso) {  // "2026-10-29T14:00:00-07:00" -> "2026-10-29 14:00" in the schedule's zone
  const d = new Date(iso), tz = STATE.timezone;
  const p = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
    .formatToParts(d).map(x => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`;
}
function ruleWarnings(at, kind, ref) {
  const r = STATE.rules || {}, t = new Date(at), w = [];
  const others = STATE.schedule.filter(e => e.key !== ref);
  const day = localPin(at).slice(0, 10);
  const sameDay = others.filter(e => localPin(e.publish_at).slice(0, 10) === day).length;
  if (r.max_per_day && sameDay >= r.max_per_day) w.push(`${day} already has ${sameDay} (max ${r.max_per_day} a day)`);
  const near = others.find(e => Math.abs(new Date(e.publish_at) - t) < (r.min_gap_minutes || 0) * 60000);
  if (near) w.push(`within ${r.min_gap_minutes} min of "${near.title.slice(0, 50)}"`);
  return w;
}
async function dropPin(ref, at, slotKind, kind) {
  const text = localPin(at), warn = ruleWarnings(at, kind, ref);
  if (slotKind && kind && slotKind !== kind) warn.push(`it's a ${slotKind} slot and this is a ${kind}`);
  if (warn.length && !confirm(`Pin for ${text}?\n\n` + warn.map(x => "• " + x).join("\n"))) return;
  await act(ref, "pin", { text });
}
document.addEventListener("dragstart", e => {
  const s = e.target.closest?.("[data-dref]");
  if (!s) return;
  DRAG = { ref: s.dataset.dref, kind: s.dataset.dkind };
  e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", DRAG.ref);
});
document.addEventListener("dragend", () => { DRAG = null; document.querySelectorAll(".over").forEach(x => x.classList.remove("over")); });
document.addEventListener("dragover", e => {
  if (!DRAG) return;
  const t = e.target.closest?.("[data-dslot],[data-unpin],[data-dday]");
  document.querySelectorAll(".over").forEach(x => x !== t && x.classList.remove("over"));
  if (t) { e.preventDefault(); t.classList.add("over"); }
});
document.addEventListener("drop", async e => {
  if (!DRAG) return;
  const t = e.target.closest?.("[data-dslot],[data-unpin],[data-dday]");
  if (!t) return;
  e.preventDefault();
  const { ref, kind } = DRAG; DRAG = null;
  t.classList.remove("over");
  if (t.dataset.unpin !== undefined) return act(ref, "pin", { text: "" });
  if (t.dataset.dslot) return dropPin(ref, t.dataset.dslot, t.dataset.dkind, kind);
  const hm = prompt(`Time on ${t.dataset.dday} (HH:MM, ${STATE.timezone})`, "12:00");
  if (!hm) return;
  if (!/^\d{1,2}:\d{2}$/.test(hm.trim())) return toast("Use HH:MM", true);
  const [h, m] = hm.trim().split(":").map(Number);
  const guess = new Date(`${t.dataset.dday}T${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:00`);
  const text = `${t.dataset.dday} ${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
  const warn = ruleWarnings(guess.toISOString(), kind, ref);
  if (warn.length && !confirm(`Pin for ${text}?\n\n` + warn.map(x => "• " + x).join("\n"))) return;
  act(ref, "pin", { text });
});
document.addEventListener("change", e => {
  if (e.target.id === "showFree") { SHOWFREE = e.target.checked; try { localStorage.setItem("warroom.free", SHOWFREE ? "1" : "0"); } catch { /* private window */ } renderSchedule(); }
});

// ---------- #18 cover editor ----------

let CV = null;  // {ref, info, changes, box: "face" | "inset_face"}
async function showCover(ref) {
  const d = $("#tool");
  d.dataset.kind = "cover"; d.classList.add("cover");
  d.innerHTML = `<div class="m-head"><b>Cover</b><span style="color:var(--faint);font-size:12px">${esc(ref)}</span><button class="x" data-close>×</button></div><div class="tbody">Loading the frames…</div>`;
  if (!d.open) d.showModal();
  let info;
  try { info = await api("/api/cover?ref=" + encodeURIComponent(ref)); } catch (e) { d.querySelector(".tbody").innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; return; }
  CV = { ref, info, changes: {}, box: "face" };
  renderCover();
}
function cvVal(k) { return k in CV.changes ? CV.changes[k] : CV.info.fields[k]; }
function renderCover() {
  const { info } = CV, enq = info.style === "enquirer", main = cvVal("frame"), inset = cvVal("inset_frame");
  const frameUrl = n => info.frames[n - 1]?.url;
  const boxFrame = CV.box === "face" ? main : inset;
  const b = cvVal(CV.box) || { x: 0.35, y: 0.2, width: 0.3, height: 0.4 };
  const hl = cvVal("headline") || [];
  // A person's best photo, or (#41) one of their cast photos by label: "Jer|angry" (a name has no |).
  const photoSel = (id, k) => {
    const v = (cvVal(k) || "") + (cvVal(k + "_label") ? "|" + cvVal(k + "_label") : "");
    const opt = (val, text) => `<option value="${esc(val)}" ${val === v ? "selected" : ""}>${esc(text)}</option>`;
    return `<select id="${id}">${opt("", "(none: use the frame)")}${info.photos.map(p => opt(p, p) + (info.photo_labels?.[p] || []).map(l => opt(p + "|" + l, `${p} · ${l}`)).join("")).join("")}</select>`;
  };
  $("#tool").innerHTML = `<div class="m-head"><b>Cover</b><span style="color:var(--faint);font-size:12px">${esc(CV.ref)} · ${esc(info.style)}</span><button class="x" data-close>×</button></div>
    <div class="cv"><div>
      <img class="shot" id="cvShot" src="${esc(CV.preview || info.cover_url)}" alt="cover">
      <div class="f" style="font-size:11px;color:var(--faint);margin-top:6px">${CV.preview ? "Preview (not saved)" : "Current cover"} · click a frame to use it${enq ? "; shift-click for the inset" : ""}</div>
      <div class="frames">${info.frames.map(f => `<button data-cvframe="${f.n}" class="${f.n === main ? "main" : f.n === inset ? "inset" : ""}" title="${f.t.toFixed(1)} s"><img loading="lazy" src="${esc(f.url)}" alt=""><span>${f.n}${f.n === main ? " main" : f.n === inset ? " inset" : ""}</span></button>`).join("")}</div>
      ${enq ? `<div style="margin-top:10px;display:flex;gap:8px;align-items:center;font-size:12px;color:var(--dim)">Face box on <span class="seg"><button data-cvbox="face" class="${CV.box === "face" ? "on" : ""}">main</button><button data-cvbox="inset_face" class="${CV.box === "inset_face" ? "on" : ""}">inset</button></span> drag to move, corner to resize</div>
      ${boxFrame ? `<div class="boxwrap" id="cvWrap"><img src="${esc(frameUrl(boxFrame))}" alt="" draggable="false"><div class="box ${CV.box === "inset_face" ? "inset" : ""}" id="cvBox" style="left:${b.x * 100}%;top:${b.y * 100}%;width:${b.width * 100}%;height:${b.height * 100}%"><i data-resize></i></div></div>` : ""}` : ""}
    </div>
    <div class="form">
      ${enq ? `<label>Headline (up to 3 lines)</label>${[0, 1, 2].map(k => `<input class="search" data-cvhl="${k}" value="${esc(hl[k] || "")}">`).join("")}
        <div class="row2"><div><label>Attribution</label><input class="search" data-cvf="attribution" value="${esc(cvVal("attribution") || "")}"></div>
          <div><label>Band kicker</label><input class="search" data-cvf="band_kicker" value="${esc(cvVal("band_kicker") || "")}"></div></div>
        <label>Band headline</label><input class="search" data-cvf="band_headline" value="${esc(cvVal("band_headline") || "")}">
        <label>Subhead</label><input class="search" data-cvf="subhead" value="${esc(cvVal("subhead") || "")}">
        <label>Teaser (shows on this stream's other covers)</label><input class="search" data-cvf="teaser" value="${esc(cvVal("teaser") || "")}">
        <div class="row2"><div><label>Main face photo</label>${photoSel("cvPhoto", "photo")}</div><div><label>Teaser photo</label>${photoSel("cvTeaserPhoto", "teaser_photo")}</div></div>`
        : `<label>Words (at most ${info.max_words})</label><input class="search" data-cvf="text" value="${esc(cvVal("text") || "")}">`}
      <div style="display:flex;gap:6px;margin-top:6px"><button class="btn" id="cvPrev">Preview</button><button class="btn primary" id="cvSave" ${Object.keys(CV.changes).length ? "" : "disabled"}>Save and re-render</button></div>
      <div class="f" style="font-size:11px;color:var(--faint)">Saving re-renders without model calls and re-attaches the cover in Kitsu${cvVal("teaser") !== CV.info.fields.teaser || "teaser_photo" in CV.changes || "teaser_photo_label" in CV.changes ? "; a teaser change re-renders every cover of the stream" : ""}. An uploaded video's YouTube thumbnail isn't changed.</div>
    </div></div>`;
  wireCover();
}
function wireCover() {
  const set = (k, v) => { CV.changes[k] = v; $("#cvSave").disabled = false; };
  document.querySelectorAll("[data-cvhl]").forEach(el => el.addEventListener("input", () => set("headline", [...document.querySelectorAll("[data-cvhl]")].map(x => x.value).filter(x => x.trim()))));
  document.querySelectorAll("[data-cvf]").forEach(el => el.addEventListener("input", () => set(el.dataset.cvf, el.value)));
  const setPhoto = k => e => { const [who, label = ""] = e.target.value.split("|"); set(k, who); set(k + "_label", label); };
  $("#cvPhoto")?.addEventListener("change", setPhoto("photo"));
  $("#cvTeaserPhoto")?.addEventListener("change", setPhoto("teaser_photo"));
  $("#cvPrev").onclick = () => coverSend(true);
  $("#cvSave").onclick = () => coverSend(false);
  const wrap = $("#cvWrap"), box = $("#cvBox");
  if (!wrap || !box) return;
  box.addEventListener("pointerdown", e => {
    e.preventDefault();
    const R = wrap.getBoundingClientRect(), b0 = { ...(cvVal(CV.box) || { x: 0.35, y: 0.2, width: 0.3, height: 0.4 }) };
    const resize = e.target.dataset.resize !== undefined, x0 = e.clientX, y0 = e.clientY;
    const move = ev => {
      const dx = (ev.clientX - x0) / R.width, dy = (ev.clientY - y0) / R.height, b = { ...b0 };
      if (resize) { b.width = Math.min(1 - b.x, Math.max(0.04, b0.width + dx)); b.height = Math.min(1 - b.y, Math.max(0.04, b0.height + dy)); }
      else { b.x = Math.min(1 - b.width, Math.max(0, b0.x + dx)); b.y = Math.min(1 - b.height, Math.max(0, b0.y + dy)); }
      Object.assign(box.style, { left: b.x * 100 + "%", top: b.y * 100 + "%", width: b.width * 100 + "%", height: b.height * 100 + "%" });
      CV.changes[CV.box] = b;
    };
    const up = () => { removeEventListener("pointermove", move); removeEventListener("pointerup", up); $("#cvSave").disabled = false; };
    addEventListener("pointermove", move); addEventListener("pointerup", up);
  });
}
async function coverSend(preview) {
  const btn = preview ? $("#cvPrev") : $("#cvSave");
  btn.disabled = true; btn.textContent = preview ? "Rendering…" : "Saving…";
  try {
    const r = await api("/api/cover", { ref: CV.ref, changes: CV.changes, preview });
    if (preview) { CV.preview = r.url; renderCover(); return; }
    if (r.row) upsert(r.row);
    toast("Cover saved and re-rendered");
    CV.info.cover_url = r.url; CV.preview = null;
    Object.assign(CV.info.fields, CV.changes); CV.changes = {};
    renderCover(); refresh();
  } catch (e) { toast(e.message, true); btn.disabled = false; btn.textContent = preview ? "Preview" : "Save and re-render"; }
}
document.addEventListener("click", e => {
  const c = e.target.closest?.("[data-cover]");
  if (c) { showCover(c.dataset.cover); return; }
  const f = e.target.closest?.("[data-cvframe]");
  if (f && CV) {
    const n = +f.dataset.cvframe;
    if (e.shiftKey && CV.info.style === "enquirer") { CV.changes.inset_frame = n; CV.box = "inset_face"; }
    else { CV.changes.frame = n; CV.box = "face"; }
    renderCover(); return;
  }
  const bx = e.target.closest?.("[data-cvbox]");
  if (bx && CV) { CV.box = bx.dataset.cvbox; renderCover(); }
});
$("#tool").addEventListener("close", () => { $("#tool").classList.remove("cover"); CV = null; });

// ---------- Analytics (#42): the same numbers as `clipper analytics --report` ----------

let STATS_MONTH = "";
const kindName = k => KIND[k] || (k === "clip" ? "Clip" : k);  // schedule/analytics say "clip", review "pick"
async function refreshStats() {
  const el = $("#stats");
  let a;
  try { a = await api("/api/analytics" + (STATS_MONTH ? "?month=" + STATS_MONTH : "")); } catch (e) { a = { error: e.message }; }
  if (a.error) { el.innerHTML = `<div class="bad" style="color:var(--red)">${esc(a.error)}</div>`; return; }
  renderStats(a);
}
function monthsBack(n) {
  const out = [], d = new Date(); d.setDate(1);
  for (let k = 0; k < n; k++) { out.push(d.toISOString().slice(0, 7)); d.setMonth(d.getMonth() - 1); }
  return out;
}
function renderStats(a) {
  const n = v => (+v || 0).toLocaleString(), t = a.totals, p = a.prev_totals;
  const sel = $("#statsMonth");
  if (!sel.options.length) sel.innerHTML = monthsBack(4).map(m => `<option>${m}</option>`).join("");
  sel.value = a.month;
  $("#statsSub").textContent = a.fetched_at ? "YouTube data as of " + ago(a.fetched_at) : "";
  const delta = (x, y) => y > 0 ? `<span class="d ${x >= y ? "up" : "down"}">${x >= y ? "▲" : "▼"} ${Math.abs(Math.round((x - y) / y * 100))}% vs ${esc(a.prev_month)}</span>` : `<span class="d">${esc(a.prev_month)}: no data</span>`;
  const tile = (label, value, d) => `<div class="tile"><span>${label}</span><b>${value}</b>${d}</div>`;
  const kinds = Object.entries(a.uploads_by_kind).map(([k, v]) => `${v} ${kindName(k)}${v > 1 ? "s" : ""}`).join(" · ");
  const tiles = tile("Views", n(t.views), delta(t.views, p.views))
    + tile("Watch time", (+t.watch_hours).toLocaleString() + " h", delta(t.watch_hours, p.watch_hours))
    + tile("Subscribers", (t.subs >= 0 ? "+" : "") + n(t.subs), delta(t.subs, p.subs))
    + tile("Published", n(a.uploads), `<span class="d">${esc(kinds) || "none"}${a.still_scheduled ? ` · ${a.still_scheduled} more to come` : ""}</span>`);
  const totalT = a.traffic.reduce((s, x) => s + x.views, 0) || 1, maxT = Math.max(1, ...a.traffic.map(x => x.views));
  const traffic = a.traffic.slice(0, 8).map(x => `<span class="lab">${esc(x.label)}</span>
      <span class="track"><div class="fill" style="width:${x.views / maxT * 100}%"></div></span>
      <span class="v">${n(x.views)} · ${Math.round(x.views / totalT * 100)}%</span>`).join("");
  const group = (title, rows) => {
    if (!rows.length) return "";
    const max = Math.max(1, ...rows.map(r => r.median_views));
    return `<div><h3>${title}</h3><div class="hb">${rows.map(r => `<span class="lab ${r.n < 3 ? "few" : ""}" title="${r.n} video${r.n > 1 ? "s" : ""}${r.n < 3 ? ": too few to read much into" : ""}">${esc(["pick", "clip", "short", "segment", "compilation"].includes(r.key) ? kindName(r.key) + "s" : r.key)} <span class="few">(${r.n})</span></span>
      <span class="track"><div class="fill" style="width:${r.median_views / max * 100}%;${r.n < 3 ? "opacity:.45" : ""}"></div></span>
      <span class="v" title="median 7-day views · average % watched · 7-day views in all">${r.median_views} · ${Math.round(r.pct)}% · ${n(r.views)}</span>`).join("")}</div></div>`;
  };
  const vids = rows => rows.map(r => `<div><span class="n">${n(r.d7_views)}</span> <span class="few">${Math.round(r.d7_pct || 0)}% · ${esc(kindName(r.kind))} · ${esc(r.show)}</span> <a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a></div>`).join("");
  $("#stats").innerHTML = `<div class="tiles">${tiles}</div>
    <div class="two">
      <div><h3>Views per day</h3>${dailyChart(a.daily)}</div>
      <div><h3>Where views came from</h3>${traffic ? `<div class="hb">${traffic}</div>` : `<div class="note">No traffic data for this month yet.</div>`}</div>
    </div>
    ${a.with_d7 ? `<div><h3>First 7 days of this month's uploads</h3><div class="note">Each bar is the median 7-day views; then average % watched and the group's 7-day views in all. Faded rows have fewer than 3 videos.</div></div>
    <div class="four">${group("By show", a.by_show)}${group("By kind", a.by_kind)}${group("By weekday published", a.by_weekday)}${group("By hour published", a.by_hour)}</div>
    <div class="two"><div><h3>Best (7-day views)</h3><div class="vids">${vids(a.best)}</div></div>
      <div><h3>Weakest</h3><div class="vids">${vids(a.worst) || `<div class="note">Needs more than 8 videos with 7-day numbers.</div>`}</div></div></div>`
      : `<div class="note">None of this month's uploads is 8 days old yet, so there are no 7-day numbers to compare.</div>`}
    <div class="note">Report as a file: <code>clipper analytics --report${a.month ? " --month " + esc(a.month) : ""}</code> → reports/analytics/${esc(a.month)}.md + .csv</div>
    <div class="tip" id="statTip" hidden></div>`;
}
function dailyChart(days) {
  if (!days.length) return `<div class="note">No daily numbers for this month yet.</div>`;
  const W = 640, H = 170, L = 34, B = 18, T = 6, max = Math.max(1, ...days.map(d => d.views));
  const step = Math.pow(10, Math.floor(Math.log10(max))), top = Math.ceil(max / step) * step;
  const y = v => T + (H - T - B) * (1 - v / top), slot = (W - L) / days.length, bw = Math.max(2, slot - 2);
  const ticks = [0, top / 2, top].map(v => `<line class="gl" x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 5}" y="${y(v) + 3}" text-anchor="end">${v >= 1000 ? (v / 1000) + "k" : v}</text>`).join("");
  const bars = days.map((d, k) => {
    const x = L + k * slot + 1, h = Math.max(1, y(0) - y(d.views)), r = Math.min(4, bw / 2, h);
    const path = `M${x},${y(0)} v${-(h - r)} q0,${-r} ${r},${-r} h${bw - 2 * r} q${r},0 ${r},${r} v${h - r} z`;
    return `<g data-tip="${esc(d.day)}|${d.views}|${d.minutes}|${d.subs}"><rect class="hit" x="${L + k * slot}" y="${T}" width="${slot}" height="${H - T - B}"/><path class="bar" d="${path}"/></g>`;
  }).join("");
  const labels = days.filter((d, k) => k % Math.ceil(days.length / 6) === 0).map(d => {
    const k = days.indexOf(d);
    return `<text x="${L + k * slot + slot / 2}" y="${H - 4}" text-anchor="middle">${d.day.slice(5)}</text>`;
  }).join("");
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Views per day">${ticks}${bars}${labels}</svg>`;
}
document.addEventListener("mousemove", e => {
  const tip = $("#statTip");
  if (!tip) return;
  const g = e.target.closest?.("[data-tip]");
  if (!g) { tip.hidden = true; return; }
  const [day, v, m, s] = g.dataset.tip.split("|");
  tip.innerHTML = `${new Date(day + "T12:00:00").toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })}<br><b>${(+v).toLocaleString()}</b> views<br>${Math.round(m / 60 * 10) / 10} h watched<br>${s >= 0 ? "+" : ""}${s} subscribers`;
  tip.hidden = false;
  tip.style.left = Math.min(innerWidth - 150, e.clientX + 12) + "px"; tip.style.top = (e.clientY + 12) + "px";
});
document.addEventListener("change", e => { if (e.target.id === "statsMonth") { STATS_MONTH = e.target.value; refreshStats(); } });

// ---------- Costs (#50) ----------

async function refreshCosts() {
  const el = $("#costs");
  let c;
  try { c = await api("/api/costs"); } catch (e) { c = { error: e.message }; }
  if (c.error) { el.innerHTML = `<div class="bad">${esc(c.error)}</div>`; return; }
  const usd = v => "$" + (+v || 0).toFixed(2);
  const list = (o, n) => Object.entries(o || {}).slice(0, n).map(([k, v]) => `${esc(k)} ${usd(v)}`).join(", ") || "-";
  const cr = Object.entries(c.credits || {});
  $("#costSub").textContent = c.month;
  el.innerHTML = `
    <div class="row"><span class="big">${usd(c.net_usd)}</span><span>this month${cr.length ? ` after ${cr.map(([k, v]) => `${esc(k)} credit ${usd(v)}`).join(", ")}` : ""}</span></div>
    <div>${list(c.by_kind)}${c.estimated_usd ? ` <span class="f" title="Modal runs are our per-run estimates; earlier model spend was read back from output files">(${usd(c.estimated_usd)} estimated)</span>` : ""}</div>
    <div class="sub">By stage</div><div>${list(c.by_stage, 6)}</div>
    <div class="sub">By show</div><div>${list(c.by_show, 6)}</div>
    <div class="f">${c.calls.toLocaleString()} calls and runs · all time ${usd(c.all_time_usd)} since ${esc(c.since || "-")} · revenue once monetized</div>`;
}

// ---------- Modal (#49) ----------

let CLOUD = null;
async function refreshCloud() {
  try { CLOUD = await api("/api/modal"); } catch (e) { CLOUD = { error: e.message }; }
  renderCloud();
}
function renderCloud() {
  const m = CLOUD, el = $("#cloud");
  if (!m) return;
  if (m.error) { el.innerHTML = `<div class="bad">${esc(m.error)}</div>`; $("#cloudSub").textContent = ""; return; }
  const usd = v => "$" + (+v || 0).toFixed(2), hrs = s => (s / 3600).toFixed(1) + " h", mins = s => Math.round(s / 60) + " min";
  const sp = m.spend || {}, up = (m.containers?.apps || {})[m.app] || [];
  const cap = m.budget || sp.credits || 0, pct = cap ? Math.min(100, 100 * sp.metered / cap) : 0;
  const level = m.budget ? (sp.metered > m.budget ? "over" : pct > 80 ? "warn" : "") : sp.metered > sp.credits ? "over" : "";
  const run = r => `<span class="w" title="${esc(r.what)}">${esc(r.kind === "stream" ? r.what : r.what.replace("/", " · "))}</span>`;
  $("#cloudSub").textContent = m.running.length ? `${m.running.length} running` : up.length ? `${up.length} up` : "idle";
  el.innerHTML = `
    ${sp.error ? `<div class="bad">billing: ${esc(sp.error)}</div>` : `
    <div class="row"><span class="big">${usd(sp.metered)}</span><span>this month${m.budget ? ` of your ${usd(m.budget)} budget` : ` · ${usd(sp.credits)} credit`}</span></div>
    <div class="row"><span class="meter"><i class="${level}" style="width:${pct}%"></i></span><span class="${sp.billed > 0 ? "bad" : ""}">${usd(sp.billed)} billed</span></div>
    <div><span class="${sp.per_hour > 0 ? "now" : ""}">${usd(sp.per_hour)}/h now</span> · ${usd(sp.last_24h)} last 24 h · ${Object.entries(sp.by_app || {}).map(([a, v]) => `${esc(a.replace("clipper-", ""))} ${usd(v)}`).join(", ")}</div>
    <div class="f" title="Each run's call time + 30 s idle at the GPU's price. Modal's billing (above) is the real number.">our per-run estimates: ${usd(m.est_month_usd)} this month, ${usd(m.est_24h_usd)} in 24 h</div>`}
    <div>GPUs: <b style="color:var(--text)">${up.length}</b> / ${m.max_containers} up${up.length ? `, oldest since ${esc(new Date(up[0]).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}` : ""}${m.containers?.error ? ` <span class="bad">(${esc(m.containers.error)})</span>` : ""}</div>
    ${m.running.length ? `<div class="sub">Running now</div><div class="runs">${m.running.slice(0, 8).map(r => `${run(r)}<span class="now">${mins(r.elapsed_s)}</span><span>${esc(r.gpu || "default")} · ${hrs(r.audio_s)}</span>`).join("")}</div>` : ""}
    ${m.failures_24h.length ? `<div class="sub">Failed in 24 h</div><div class="runs">${m.failures_24h.slice(0, 5).map(r => `${run(r)}<span class="f">${esc(ago(r.at))}</span><span class="bad" title="${esc(r.error)}">${esc((r.error || "").slice(0, 40))}</span>`).join("")}</div>` : ""}
    ${m.recent.length ? `<div class="sub">Recent runs</div><div class="runs">${m.recent.slice(0, 6).map(r => `${run(r)}<span title="${esc(r.gpu)}, call ${mins(r.call_s || 0)}">${hrs(r.audio_s || 0)}${r.call_s ? " · " + Math.round(r.audio_s / r.call_s) + "×" : ""}</span><span class="${r.ok === false ? "bad" : "f"}">${r.ok === false ? "failed" : "~" + usd(r.est_usd)}</span>`).join("")}</div>` : ""}
    ${m.archives.map(a => `<div class="sub">Archive · ${esc(a.subject)}</div>
      <div class="row"><span class="meter"><i style="width:${a.items ? 100 * a.done / a.items : 0}%"></i></span><span>${a.done.toLocaleString()} / ${a.items.toLocaleString()}</span></div>
      <div>${Math.round(a.hours_done).toLocaleString()} / ${Math.round(a.hours_total).toLocaleString()} h${a.errors ? ` · <span class="bad">${a.errors} errors</span>` : ""}${a.items_per_hour ? ` · ${a.items_per_hour}/h, ETA ${a.eta_h} h` : a.left ? ` · ${a.left} left, not running` : " · done"} · ~${usd(a.est_usd)}</div>`).join("")}
    ${m.links?.[m.app] ? `<div><a href="${esc(m.links[m.app])}" target="_blank" rel="noopener">Open ${esc(m.app)} on modal.com ↗</a></div>` : ""}`;
}

function closeModal() { OPEN = null; const v = $("#modal video"); if (v) v.pause(); $("#modal").close(); }
$("#modal").addEventListener("close", () => { OPEN = null; const v = $("#modal video"); if (v) v.pause(); });
