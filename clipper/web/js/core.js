// War room: state, helpers, the overview refresh, live shows, search and the review queue.

const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const KIND = { pick: "Clip", short: "Short", segment: "Segment", compilation: "Comp" };
const DEC = { pending: "waiting", changes_requested: "changes", approved: "approved", rejected: "rejected" };
let STATE = null, TAB = "waiting", SHOW = "", KINDF = "", Q = "", BUSY = new Set(), OPEN = null;
let ST = "", FROM = "", TO = "", SORT = "", THITS = {}, THQ = "", PICK = new Set(), LASTPICK = null;
let VISIBLE = [], SEL = null, SEL_AT = 0;  // the queue in display order; the keyboard's item
const PHONE = matchMedia("(max-width: 700px)");

function mmss(s) { if (s == null) return ""; s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`; }
function ago(iso) { if (!iso) return ""; const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 90) return "just now"; if (s < 5400) return Math.round(s / 60) + " min ago"; if (s < 129600) return Math.round(s / 3600) + " h ago"; return Math.round(s / 86400) + " d ago"; }
function usd(x) { return x == null ? "?" : x < 0.1 ? `$${(+x).toFixed(3)}` : `$${(+x).toFixed(2)}`; }
// #203: before an action that makes model calls, what it should cost (from past ones) and a yes
function retakeCost() {  // " (a retake, about $0.020)" when a note starts one here
  const rc = STATE?.retake_cost;
  return rc?.auto ? ` (starts a retake${rc.avg_usd != null ? `, about ${usd(rc.avg_usd)}` : ""})` : "";
}
function retakeOk(n) {
  const rc = STATE?.retake_cost, a = rc?.avg_usd;
  if (!rc?.auto) return true;  // [retake] auto off: nothing runs from here
  return confirm(`This starts ${n} retake${n > 1 ? "s" : ""} (a model call each)${a != null ? `, about ${usd(a * n)}` : ""}. Go?`);
}
function toast(msg, err, undoId) { const t = document.createElement("div"); t.className = "toast" + (err ? " err" : ""); t.textContent = msg;
  $("#announce").textContent = msg;  // a live region that is always there, so screen readers say it
  if (undoId) { const b = document.createElement("button"); b.className = "btn"; b.textContent = "Undo"; b.onclick = () => { t.remove(); undo(undoId); }; t.appendChild(b); }
  else if (err && /serve log/.test(msg)) { const b = document.createElement("button"); b.className = "btn"; b.textContent = "Log"; b.onclick = () => { t.remove(); showLog({ which: "serve", q: "", bad: true }); }; t.appendChild(b); }
  document.querySelectorAll(".toast").forEach(o => o.remove());
  document.body.appendChild(t); setTimeout(() => t.remove(), undoId ? 10000 : err ? 7000 : 3500); }
async function api(path, body) {
  // A read gives up after 2 min, so a hung one can't hold the timer's refresh (pollRefresh) for good.
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
    : { signal: AbortSignal.timeout(120000) });
  const j = await r.json().catch(() => ({ error: r.statusText }));
  if (r.status === 401) throw new Error("signed out: open the war room's ?key= link again");  // #206: the cookie went
  if (!r.ok || j.error) throw new Error(j.error || r.statusText);
  return j;
}

function tick() {
  const tz = STATE?.timezone || undefined;
  $("#clock").textContent = new Date().toLocaleString([], { timeZone: tz, weekday: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  // #201: the clock ticks whatever the last poll did; this says how old the numbers are
  const age = STATE ? Math.max(0, (Date.now() - new Date(STATE.generated_at)) / 1000) : null, u = $("#updated");
  const was = age === null ? "" : age < 90 ? `${Math.round(age)} s ago` : ago(STATE.generated_at);
  u.textContent = DOWN ? `${DOWN.signedOut ? "signed out: open the ?key= link again" : "serve isn't answering"}${was ? " · data from " + was : ""}` : age === null ? "no data yet" : `updated ${was}`;
  u.title = DOWN ? `${DOWN.msg}\nTrying again every 15 s.` : "How old the numbers on this page are";
  u.className = "updated " + (DOWN || age > 120 ? "bad" : age === null || age > 45 ? "mid" : "");
}
setInterval(tick, 1000);

// #201: a failed refresh says so once and in the header, not in a toast every 15 s; before the
// first answer the panels say why they are empty.
let DOWN = null;  // {msg} while the overview isn't coming back
const FIRST = ["#streams", "#retakes", "#health", "#grid", "#schedule"];
function waiting(msg) {
  if (STATE) return;
  for (const s of FIRST) $(s).innerHTML = `<div class="empty">${msg ? `Can't load: ${esc(msg)}.<br>Trying again every 15 s.` : "Loading…"}</div>`;
}
waiting();
let MUTATED = 0;  // bumped when an action's answer changes STATE.items here
let REFRESHING = 0;  // overviews asked for and not answered yet
// The timer's refresh waits for one still on its way: a slow serve (a cold cache over the NAS
// share takes a minute) got another request every 15 s on top. An action's refresh always goes.
function pollRefresh() { if (!REFRESHING) refresh(); }
async function refresh() {
  REFRESHING++;
  try {
    const asked = MUTATED, s = await api("/api/overview");
    if (DOWN) { if (!DOWN.signedOut) toast("serve is answering again"); DOWN = null; }
    // #202: answers aren't ordered, and one asked for before an action landed would undo it for a
    // moment; the refresh each action starts afterwards brings the real state
    if (asked !== MUTATED || STATE && new Date(s.generated_at) < new Date(STATE.generated_at)) return;
    STATE = s; render();
  }
  catch (e) {
    if (!DOWN) toast("Refresh failed: " + e.message, true);
    DOWN = { msg: e.message, signedOut: e.message.startsWith("signed out") }; waiting(e.message); tick();
  }
  finally { REFRESHING--; }
}
// ---------- live shows ----------

let LIVE = [], LIVEMENU = null;
const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* private window */ } } };
function since(iso) {  // Kick gives "2026-10-23 01:02:03" in UTC
  if (!iso) return "";
  const t = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso.replace(" ", "T") + "Z");
  const m = Math.max(0, Math.round((Date.now() - t) / 60000));
  return isNaN(m) ? "" : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}`;
}
function recording(l) {  // the NAS recorder's stream for this channel, if it's on it now
  return (STATE?.streams || []).find(s => s.channel === l.channel && ["recording", "paused", "finalizing"].includes(s.recording));
}
async function refreshLive() {
  try { LIVE = await api("/api/live"); renderLive(); }
  catch (e) { /* Kick unreachable: keep the last state */ }
}
function renderLive() {
  $("#lives").innerHTML = LIVE.map((l, n) => {
    const rec = l.live ? recording(l) : null;
    const tip = l.live ? `${l.title || ""}\nlive ${since(l.since)} · ${rec ? "recording " + rec.id : "NOT recording"}` : `${l.channel}: offline, click for the channel`;
    // #206: the chip's label is a real button (keyboard, screen readers); its menu sits beside it
    return `<span class="live ${l.live ? "on" : ""}" data-live="${n}" title="${esc(tip)}"><button class="chipb" type="button"><span class="dot"></span>${esc(l.show_name)}${l.live
      ? ` · LIVE${l.viewers != null ? " · " + l.viewers.toLocaleString() : ""}${l.since ? " · " + since(l.since) : ""} <span class="rec ${rec ? "" : "off"}">${rec ? "● REC" : "○ no rec"}</span>` : ""}</button>${LIVEMENU === n ? liveMenu(l, rec) : ""}</span>`;
  }).join("");
}
function liveMenu(l, rec) {
  return `<div class="livemenu" data-menu>
    <div class="info">${esc(l.channel)} · ${rec ? `recording ${esc(rec.id)} (${esc(rec.recording)})` : "the NAS isn't recording it"}</div>
    <button class="btn" data-watch="here" data-ch="${esc(l.channel)}">▶ Watch here</button>
    <button class="btn" data-watch="window" data-ch="${esc(l.channel)}">↗ Open in a new window</button>
    <label><input type="checkbox" id="liveRemember"> Always do this (shift-click a chip to ask again)</label></div>`;
}
function watch(ch, where) {
  const name = LIVE.find(l => l.channel === ch)?.show_name || ch;
  if (where === "window") { window.open(`https://kick.com/${encodeURIComponent(ch)}`, `kick-${ch}`); return; }
  const d = $("#dock"); d.hidden = false;
  d.innerHTML = `<div class="d-head"><span class="dot" style="width:8px;height:8px;border-radius:50%;background:var(--red)"></span><b>${esc(name)}</b><span style="color:var(--faint)">${esc(ch)}</span>
    <span class="sp"></span><button class="btn" id="dSize" title="Bigger / smaller">⤢</button>
    <button class="btn" id="dPop" title="Open on kick.com in a new window instead">↗</button>
    <button class="btn" id="dClose" title="Close the player">×</button></div>
    <iframe src="https://player.kick.com/${encodeURIComponent(ch)}?autoplay=true" allow="autoplay; fullscreen; picture-in-picture" allowfullscreen></iframe>`;
  d.classList.toggle("big", !!store.get("warroom.dock.big"));
  $("#dSize").onclick = () => { d.classList.toggle("big"); store.set("warroom.dock.big", d.classList.contains("big") ? "1" : null); };
  $("#dPop").onclick = () => { closeDock(); watch(ch, "window"); };
  $("#dClose").onclick = closeDock;
}
function closeDock() { const d = $("#dock"); d.innerHTML = ""; d.hidden = true; }
function liveClick(e, t) {
  if (e.target.closest("[data-menu]")) {
    const w = e.target.closest("[data-watch]");
    if (!w) return;
    if ($("#liveRemember")?.checked) store.set("warroom.watch", w.dataset.watch);
    LIVEMENU = null; renderLive(); watch(w.dataset.ch, w.dataset.watch);
    return;
  }
  const n = +t.dataset.live, l = LIVE[n];
  if (!l.live) { window.open(`https://kick.com/${encodeURIComponent(l.channel)}`, "_blank"); return; }
  const remembered = store.get("warroom.watch");
  if (remembered && !e.shiftKey) { watch(l.channel, remembered); return; }
  if (e.shiftKey) store.set("warroom.watch", null);  // shift-click: forget the choice, ask
  LIVEMENU = LIVEMENU === n ? null : n; renderLive();
}

function render() {
  const s = STATE, c = s.counts;
  $("#counters").innerHTML = [
    ["Waiting", c.pending, c.pending ? "hot" : ""], ["Changes", c.changes || 0, c.changes ? "warn" : ""],
    ["Approved", c.approved || 0, ""], ["Unslotted", c.approved_unscheduled, c.approved_unscheduled ? "warn" : ""],
    ["Next 7 d", c.scheduled_7d, ""], ["Uploaded", c.uploaded, ""],
  ].map(([l, n, k]) => `<div class="counter ${k}"><b>${n}</b><span>${l}</span></div>`).join("");
  const first = !$("#schedule").children.length;
  renderStreams(); renderRetakes(); renderHealth(); renderQueue(); renderUploads(); renderSchedule(); renderLive(); tick();
  if (first && document.body.dataset.view === "schedule") setView("schedule");  // opened on it: go to today
  if (OPEN) { const r = s.items.find(i => i.ref === OPEN); if (r && !$("#modal").contains(document.activeElement)) fillModalState(r); }
}

function renderStreams() {
  const st = STATE.streams;
  $("#pipeSub").textContent = st.filter(x => x.run_status === "running").length ? "running" : "";
  $("#streams").innerHTML = st.slice(0, 10).map(x => {
    const done = STATE.stages.filter(g => x.stages[g] === "done").length;
    const status = x.recording && x.recording !== "done" ? `● ${x.recording}` : x.run_status === "running" ? "running"
      : x.run_status === "failed" ? "failed" : done === STATE.stages.length ? "done" : x.published ? "in review" : `${done}/${STATE.stages.length}`;
    return `<div class="stream" title="${esc(x.run_error)}">
      <div class="top"><span class="tag">${esc(x.show_name || x.channel)}</span><b style="font-size:12px">${esc(x.started_at.slice(0, 10))}</b>
        <span style="margin-left:auto;font-size:11px;color:${x.run_status === "failed" ? "var(--red)" : x.run_status === "running" ? "var(--accent)" : "var(--faint)"}">${esc(status)}</span></div>
      <div class="t">${esc(x.title)}</div>
      <div class="stages" role="img" aria-label="${esc(STATE.stages.map(g => `${g} ${x.stages[g] || "not yet"}`).join(", "))}">${STATE.stages.map(g => `<div class="stage ${esc(x.stages[g] || "")}" title="${g}: ${x.stages[g] || "not yet"}"></div>`).join("")}</div>
      <div class="meta"><span>${esc(x.id)}</span>${x.faces ? `<button class="lnk" data-faces="${esc(x.id)}" title="Name this stream's face groups">👤 faces</button>` : ""}<span>${x.duration_s ? (x.duration_s / 3600).toFixed(1) + " h" : ""}</span></div></div>`;
  }).join("") || `<div class="empty">No streams yet</div>`;
}

function renderRetakes() {
  const r = STATE.retakes, rc = STATE.retake_cost || {};
  // #203: what retakes cost, each on its row and this month's in the title
  $("#retakeSub").textContent = STATE.health.retake_running ? "working…"
    : rc.month_n ? `${rc.month_n} this month · ${usd(rc.month_usd)}` : "";
  $("#retakes").innerHTML = r.map(h => `<div class="ev">
      <div class="w">${esc(ago(h.at))} · ${esc(h.stream)}/${esc(h.key.split("_")[0])}${h.cost_usd != null ? " · " + usd(h.cost_usd) : ""}</div>
      <q>${esc(h.comment)}</q>
      <div class="r-${esc(h.result)}">${h.result === "applied" ? "✓ " + esc(h.summary || h.applied.join("; "))
        : h.result === "unsupported" ? "⚠ " + esc(h.unsupported || "not applied") : h.result === "error" ? "✕ " + esc(h.error) : esc(h.result)}</div></div>`).join("")
    || `<div class="empty">No retakes yet. Set an item to retake with a note and Clippa re-cuts it.</div>`;
}

function renderHealth() {
  const h = STATE.health, svc = h.services || {};
  // serve and the bot from their heartbeats (streams/.serve, .bot: any machine), with what serve is running
  const lines = ["serve", "bot"].filter(k => svc[k]).map(k => {
    const s = svc[k], busy = s.running && s.running.length ? ` · ${s.running.join(", ")} running` : "";
    return `<div>${esc(s.label)}: <span class="${s.level}">${esc(s.text)}</span>${esc(busy)}</div>`;
  }).join("");
  const last = svc.serve && svc.serve.last ? Object.entries(svc.serve.last)
    .filter(([, j]) => j.exit).map(([k, j]) => `${k} exit ${j.exit} ${ago(j.at)}`) : [];
  $("#health").innerHTML = lines + `
    ${last.length ? `<div class="bad" style="font-size:12px">failed: ${esc(last.join("; "))}</div>` : ""}
    <div>Retakes: <span class="${h.retake_running ? "mid" : "ok"}">${h.retake_running ? "working" : "idle"}</span></div>
    <div>Kitsu sync: <span class="${h.sync.error ? "bad" : h.sync.running ? "mid" : "ok"}">${h.sync.running ? "syncing…" : h.sync.error ? esc(h.sync.error) : h.sync.last ? ago(h.sync.last) : "via serve"}</span></div>
    <div>YouTube uploads: <span class="${h.auto_upload_minutes ? "ok" : "mid"}">${h.auto_upload_minutes ? "automatic, every " + h.auto_upload_minutes + " min" : "by hand (clipper upload)"}</span></div>
    <div>Schedule: ${STATE.schedule_generated_at ? "planned " + ago(STATE.schedule_generated_at) : "never planned"}</div>`;
  renderBusy();
}

// #24: one search box over every stream's titles, descriptions, topics, hashtags and, from
// the server, the items' captions; filters combine, and live in the URL (#q=...&st=...).
const STATUS = { unslotted: i => i.decision === "approved" && !i.scheduled && !i.uploaded && !i.dropped,
  scheduled: i => !!i.scheduled && !i.uploaded, uploaded: i => i.uploaded, held: i => i.hold,
  pinned: i => !!i.publish_at, dropped: i => !!i.dropped, issues: i => (i.issues || []).length > 0 };
const STATUS_LABEL = { unslotted: "Approved, no slot", scheduled: "Scheduled", uploaded: "Uploaded", held: "Held",
  pinned: "Pinned", dropped: "Not going out (stale / over budget)", issues: "Has style issues" };
function textOf(i) {
  return [i.title, i.hook, i.ref, i.description, i.topic, i.hashtags, i.playlist, i.category, i.thumb_text]
    .join(" ").toLowerCase();
}
function filtered() {
  let items = STATE.items;
  if (SHOW) items = items.filter(i => i.show === SHOW);
  if (KINDF) items = items.filter(i => i.kind === KINDF);
  if (ST && STATUS[ST]) items = items.filter(STATUS[ST]);
  if (FROM) items = items.filter(i => (i.date || "") >= FROM);
  if (TO) items = items.filter(i => (i.date || "") <= TO);
  if (Q.trim()) {
    const ws = Q.toLowerCase().split(/\s+/).filter(Boolean);
    items = items.filter(i => { const t = textOf(i); return ws.every(w => t.includes(w)) || (THQ === Q && THITS[i.ref]); });
  }
  return items;
}
let THT = null;
function searchTranscripts() {
  clearTimeout(THT);
  if (Q.trim().length < 3) { THITS = {}; THQ = ""; return; }
  THT = setTimeout(async () => {
    const q = Q;
    $("#queueSub").textContent = "searching transcripts…";
    try { const r = await api("/api/search?q=" + encodeURIComponent(q)); if (q === Q) { THITS = r.hits || {}; THQ = q; renderQueue(); } }
    catch (e) { toast(e.message, true); }
  }, 700);
}
function saveHash() {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries({ tab: TAB === "waiting" ? "" : TAB, show: SHOW, kind: KINDF, q: Q, st: ST, from: FROM, to: TO, sort: SORT })) if (v) p.set(k, v);
  const was = new URLSearchParams(location.hash.slice(1));  // an investigation link (investigate.js) stays
  for (const k of ["inv", "run", "lead"]) if (was.get(k)) p.set(k, was.get(k));
  const h = p.toString();
  if (h !== location.hash.slice(1)) history.replaceState(null, "", location.pathname + location.search + (h ? "#" + h : ""));
}
function loadHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  TAB = TABS[p.get("tab")] ? p.get("tab") : "waiting"; SHOW = p.get("show") || ""; KINDF = p.get("kind") || "";
  Q = p.get("q") || ""; ST = p.get("st") || ""; FROM = p.get("from") || ""; TO = p.get("to") || "";
  SORT = Object.hasOwn(SORTS, p.get("sort") || "") ? p.get("sort") || "" : "";
  if (Q) searchTranscripts();
}
window.addEventListener("hashchange", () => { loadHash(); if (STATE) renderQueue(); });
// The link's ?key= has set the cookie by now: keep it out of the address bar, screenshots and copied links.
const URLQ = new URLSearchParams(location.search);
if (URLQ.has("key")) { URLQ.delete("key"); history.replaceState(null, "", location.pathname + (URLQ.toString() ? "?" + URLQ : "") + location.hash); }
const TABS = { waiting: i => i.decision === "pending", changes: i => i.decision === "changes_requested",
  approved: i => i.decision === "approved" && !i.uploaded, live: i => i.uploaded, rejected: i => i.decision === "rejected", all: () => true };
// #206: the queue's order. Newest stream first is the default; ties keep the ref order.
const newest = (a, b) => (b.date || "").localeCompare(a.date || "") || a.ref.localeCompare(b.ref);
const num = x => { const n = parseFloat(x); return isNaN(n) ? -Infinity : n; };
const SORTS = { "": ["Newest stream", newest], old: ["Oldest stream", (a, b) => -newest(a, b)],
  score: ["Score, high first", (a, b) => num(b.score) - num(a.score) || newest(a, b)],
  long: ["Longest", (a, b) => (b.duration_s || 0) - (a.duration_s || 0) || newest(a, b)],
  short: ["Shortest", (a, b) => (a.duration_s || Infinity) - (b.duration_s || Infinity) || newest(a, b)],  // no length: last
  kind: ["Kind", (a, b) => Object.keys(KIND).indexOf(a.kind) - Object.keys(KIND).indexOf(b.kind) || newest(a, b)] };
const TAB_LABEL = { waiting: "Waiting", changes: "Changes", approved: "Approved", live: "Uploaded", rejected: "Rejected", all: "All" };

function renderQueue() {
  const base = filtered();
  const shows = STATE.shows;
  // B12: the 15 s refresh rebuilt this bar under the cursor: the search box, selects and date
  // pickers lost focus mid-typing. While focus is in it, only the tabs' counts change.
  const bar = $("#queueBar");
  if (bar.firstChild && bar.contains(document.activeElement)) {
    bar.querySelectorAll("[data-tab]").forEach(t => {
      t.classList.toggle("on", TAB === t.dataset.tab);
      t.querySelector(".n").textContent = base.filter(TABS[t.dataset.tab]).length;
    });
  } else bar.innerHTML = `<span class="tabs">${Object.keys(TABS).map(t => `<button class="tab ${TAB === t ? "on" : ""}" data-tab="${t}">${TAB_LABEL[t]}<span class="n">${base.filter(TABS[t]).length}</span></button>`).join("")}</span>`
    + `<span class="sep"></span><select id="showSel"><option value="">All shows</option>${shows.map(s => `<option value="${s.key}" ${SHOW === s.key ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select>
       <select id="kindSel"><option value="">All kinds</option>${Object.entries(KIND).map(([k, v]) => `<option value="${k}" ${KINDF === k ? "selected" : ""}>${v}s</option>`).join("")}</select>
       <select id="stSel"><option value="">Any status</option>${Object.keys(STATUS).map(k => `<option value="${k}" ${ST === k ? "selected" : ""}>${STATUS_LABEL[k]}</option>`).join("")}</select>
       <select id="sortSel" aria-label="Sort the queue" title="Order">${Object.entries(SORTS).map(([k, [l]]) => `<option value="${k}" ${SORT === k ? "selected" : ""}>${l}</option>`).join("")}</select>
       <input type="date" class="date" id="fromD" value="${esc(FROM)}" title="Streams from this day">
       <input type="date" class="date" id="toD" value="${esc(TO)}" title="...to this day">
       <button class="btn" id="pickAll" title="Select every item shown, for a bulk action">☑ Select shown</button>
       <button class="btn" id="undoBtn" title="The last 20 war room actions, to undo">↶ Undo…</button>
       <input class="search" id="q" aria-label="Search the queue" placeholder="Search titles, descriptions, transcripts…" value="${esc(Q)}">`;
  const items = base.filter(TABS[TAB]).sort(SORTS[SORT][1]);
  VISIBLE = items.map(i => i.ref);
  if (SEL && !VISIBLE.includes(SEL)) SEL = VISIBLE[Math.min(SEL_AT, VISIBLE.length - 1)] || null;  // acted on: the next one
  SEL_AT = SEL ? VISIBLE.indexOf(SEL) : 0;
  $("#queueSub").textContent = `${items.length} shown` + (Q.trim().length >= 3 && THQ !== Q ? " · searching transcripts…" : "");
  renderBulk(); saveHash();
  $("#vnQueue").textContent = STATE.counts.pending || "";
  $("#grid").innerHTML = items.map(card).join("") || `<div class="empty">Nothing here${TAB === "waiting" ? " — all caught up" : ""}.</div>`;
}

function card(i) {
  const vertical = i.kind === "short";
  const media = i.thumbnail_url ? `<img loading="lazy" src="${i.thumbnail_url}" alt="">`
    : i.video_url ? `<video muted preload="${PHONE.matches ? "none" : "metadata"}" src="${i.video_url}#t=3"></video>` : "";
  const sched = i.scheduled ? `⏱ ${new Date(i.scheduled).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}` : i.publish_at ? `📌 ${esc(i.publish_at)}` : "";
  const hit = THQ === Q && THITS[i.ref] && !textOf(i).includes(Q.toLowerCase().split(/\s+/)[0]) ? THITS[i.ref] : "";
  return `<div class="card ${BUSY.has(i.ref) ? "busy" : ""} ${SEL === i.ref ? "sel" : ""} ${PICK.has(i.ref) ? "picked" : ""}" data-ref="${esc(i.ref)}">
    <div class="thumb ${vertical ? "vertical" : ""}" data-open="${esc(i.ref)}">${media}
      <span class="kind ${esc(i.kind)}">${esc(KIND[i.kind] || i.kind)}</span>
      <span class="state ${esc(i.decision)}">${esc(DEC[i.decision] || i.decision)}${i.hold ? " · held" : ""}</span>
      <span class="dur">${mmss(i.duration_s)}</span>${i.video_url ? `<button class="pop" data-pop="${esc(i.ref)}" title="Open the video in its own window">⧉</button>` : ""}</div>
    <div class="body">
      <div class="title" data-open="${esc(i.ref)}" role="button" tabindex="0">${esc(i.title)}</div>
      <div class="sub"><span>${esc(i.show_name)}</span><span>${esc(i.date)}</span><span>${esc(i.short.split("/")[1])}</span>${i.score ? `<span>★ ${esc(i.score)}</span>` : ""}${sched ? `<span>${sched}</span>` : ""}${i.issues?.length ? `<span class="issues" title="${esc(i.issues.map(x => x.msg).join("\n"))}">⚠ ${i.issues.length}</span>` : ""}</div>
      ${i.hook ? `<div class="hook">${esc(i.hook)}</div>` : ""}
      ${hit ? `<div class="hit" title="Said in the video">🗨 ${esc(hit)}</div>` : ""}
      ${i.decision === "changes_requested" && i.comment ? `<div class="note">✎ ${esc(i.comment)}</div>` : ""}
    </div>
    <div class="acts">
      <label class="pickl" title="Select (shift-click: a range)"><input type="checkbox" aria-label="Select" data-pick="${esc(i.ref)}" ${PICK.has(i.ref) ? "checked" : ""}></label>
      ${i.decision !== "approved" ? `<button class="btn good" data-act="approve" data-ref="${esc(i.ref)}" title="Approve (A)">✓ Approve</button>` : `<button class="btn" data-act="reopen" data-ref="${esc(i.ref)}" title="Reopen (U)">↺ Reopen</button>`}
      <button class="btn warn" data-act="changes" data-ref="${esc(i.ref)}" title="A retake note (C)">✎ Changes</button>
      <button class="btn bad" data-act="reject" data-ref="${esc(i.ref)}" title="Reject (R)" aria-label="Reject">✕</button>
    </div></div>`;
}

function renderSchedule() {
  const tz = STATE.timezone, days = {};
  const dayKey = d => new Date(d).toLocaleDateString("en-CA", { timeZone: tz });
  const today = dayKey(new Date());
  for (const e of STATE.schedule) (days[dayKey(e.publish_at)] ??= []).push(e);
  for (let k = 0; k < 14; k++) { const d = new Date(Date.now() + k * 864e5); days[dayKey(d)] ??= []; }
  $("#schedSub").textContent = `${STATE.counts.scheduled_7d} in 7 days`;
  const free = {};
  if (SHOWFREE) for (const f of STATE.free_slots || []) (free[dayKey(f.at)] ??= []).push(f);
  // Over budget or stale too (the scheduler won't place those, a pin will), newest stream first.
  const uns = STATE.items.filter(i => i.decision === "approved" && !i.scheduled && !i.uploaded)
    .sort(newest);
  const segs = uns.some(i => i.kind === "segment" && !i.hold);
  $("#schedTools").innerHTML = `<label title="Show the open slots to drop items on"><input type="checkbox" id="showFree" ${SHOWFREE ? "checked" : ""}> Open slots</label>
    <span class="unpin" data-unpin>⇣ drop here to unpin</span>
    ${segs ? `<button class="btn" data-nextkind="segment" title="Pin each approved segment without a slot to the next open segment slot, before it goes stale (a preview first)">⇥ Segments to open slots…</button>` : ""}
    <span>max ${STATE.rules?.max_per_day ?? "?"}/day · ${STATE.rules?.min_gap_minutes ?? "?"} min apart · drag a slot to move it</span>`;
  const why = i => i.hold ? `<span class="why">held</span>` : i.dropped ? `<span class="why" title="${esc(i.dropped)}">${i.dropped.startsWith("stale") ? "stale" : "over budget"}</span>` : "<span></span>";
  $("#unslotted").innerHTML = uns.length ? `<div class="ss" style="color:var(--faint);font-size:11px">Approved, no slot yet (${uns.length}): drag onto an open slot, or ⇥ for the next one</div>`
    + uns.slice(0, 60).map(i => `<div class="chip" draggable="true" data-dref="${esc(i.ref)}" data-dkind="${esc(i.kind === "pick" ? "clip" : i.kind)}" title="${esc(i.title)}"><span class="bar ${esc(i.kind === "pick" ? "clip" : i.kind)}" style="width:4px;height:16px;border-radius:2px;display:block"></span><span class="t">${esc(i.title)}</span>${why(i)}${!i.hold && i.kind !== "compilation" ? `<button class="nx" data-nextslot="${esc(i.ref)}" title="Pin to the next open slot (a preview first)">⇥</button>` : "<span></span>"}</div>`).join("") : "";
  $("#unslotted").hidden = !uns.length;
  for (const d of Object.keys(free)) days[d] ??= [];
  $("#schedule").innerHTML = Object.keys(days).sort().map(d => {
    const label = new Date(d + "T12:00:00").toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
    const rows = [...days[d].map(e => ({ e, at: e.publish_at })), ...(free[d] || []).map(f => ({ f, at: f.at }))]
      .sort((a, b) => a.at.localeCompare(b.at));
    return `<div class="day ${d === today ? "today" : d < today ? "past" : ""}"><h3 data-dday="${d}">${d === today ? "Today · " : ""}${label}<span class="n">${days[d].length || ""}</span></h3>
      ${rows.map(({ e, f }) => f ? `<div class="slot free" data-dslot="${esc(f.at)}" data-dkind="${esc(f.kind)}">
        <span class="tm">${new Date(f.at).toLocaleTimeString([], { timeZone: tz, hour: "2-digit", minute: "2-digit" })}</span>
        <span class="bar ${esc(f.kind)}"></span><span><div class="tt">open ${esc(f.kind)} slot</div></span></div>`
      : `<div class="slot" data-open="${esc(e.key)}" role="button" tabindex="0" ${e.status !== "uploaded" ? `draggable="true" data-dref="${esc(e.key)}" data-dkind="${esc(e.kind)}" data-dat="${esc(e.publish_at)}"` : ""}>
        <span class="tm">${new Date(e.publish_at).toLocaleTimeString([], { timeZone: tz, hour: "2-digit", minute: "2-digit" })}</span>
        <span class="bar ${esc(e.kind)}"></span>
        <span><div class="tt">${esc(e.title)}</div><div class="ss">${esc(e.show_name || "")} · ${e.kind}${e.status === "uploaded" ? ` · <span class="up">uploaded</span>${e.views != null ? ` · ${Number(e.views).toLocaleString()} views` : ""}${e.url ? ` · <a href="${esc(e.url)}" target="_blank" rel="noopener">YouTube</a>` : ""}` : e.source === "pinned" ? " · pinned" : ""}</div></span></div>`).join("")
      || `<div class="slot" style="cursor:default"><span></span><span></span><span class="ss">—</span></div>`}</div>`;
  }).join("");
}
