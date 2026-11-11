// War room: phone view, keyboard, layouts, then start: runs last, once everything above is defined.

// ---------- phone ----------

function setView(v) {
  document.body.dataset.view = v; store.set("warroom.view", v);
  document.querySelectorAll("#views button").forEach(b => b.classList.toggle("on", b.dataset.view === v));
  const today = v === "schedule" && PHONE.matches && $(".day.today");
  today ? today.scrollIntoView({ block: "start" }) : scrollTo(0, 0);
}
setView(store.get("warroom.view") || "queue");

async function playLow(i, full) {  // phones: a 360p copy made by the server (lowres/ beside the preview)
  const tap = $("#mTap");
  if (!tap || (tap.dataset.wait && !full)) return;
  const play = url => {
    const v = document.createElement("video");
    Object.assign(v, { controls: true, playsInline: true, autoplay: true, src: url, poster: i.thumbnail_url || "" });
    tap.replaceWith(v); v.play().catch(() => { /* needs a tap after waiting: the controls are there */ });
  };
  if (full) return play(i.video_url);
  try {
    const r = await api("/api/lowres?src=" + encodeURIComponent(i.video_url));
    if (OPEN !== i.ref || !$("#mTap")) return;
    if (r.ready) return play(r.url);
    tap.dataset.wait = "1";
    tap.querySelector("span").innerHTML = `<b>⏳</b>Making a phone-size copy${r.ahead ? ` (${r.ahead} ahead of it)` : ""}…
      <span class="alt" data-full style="position:static">Play full quality instead (${r.full_mb} MB)</span>`;
    setTimeout(() => { if (OPEN === i.ref && $("#mTap")) { delete $("#mTap").dataset.wait; playLow(i); } }, 8000);
  } catch (e) { toast(e.message, true); }
}

let TOUCH = null;  // swipe left / right in the item window: next / previous
$("#modal").addEventListener("touchstart", e => {
  const t = e.touches[0], v = e.target.closest("video");
  TOUCH = e.touches.length > 1 || e.target.closest("input, textarea") || (v && t.clientY > v.getBoundingClientRect().bottom - 60)
    ? null : { x: t.clientX, y: t.clientY, at: Date.now() };  // not on the video's scrub bar
}, { passive: true });
$("#modal").addEventListener("touchend", e => {
  if (!TOUCH) return;
  const t = e.changedTouches[0], dx = t.clientX - TOUCH.x, dy = t.clientY - TOUCH.y;
  if (Date.now() - TOUCH.at < 700 && Math.abs(dx) > 70 && Math.abs(dx) > 2 * Math.abs(dy)) move(dx < 0 ? 1 : -1);
  TOUCH = null;
}, { passive: true });

// ---------- keyboard ----------

const KEYS = [
  ["Queue", [["J K", "next / previous item (in the item window too)"], ["Enter", "open the item"], ["Esc", "close the window, or clear the selection"]]],
  ["Decide", [["A", "approve (in the item window: then the next item)"], ["R", "reject, asks why"], ["C", "retake note"],
              ["H", "hold / release"], ["P", "pin a release time"], ["U", "reopen an approved item"]]],
  ["Video (item window)", [["Space", "play / pause"], ["← →", "back / forward 5 s"], ["F", "picture in picture"], ["O", "open the video in its own window"],
                           ["B", "box a face on the paused frame: who it is"]]],
  ["Other", [["1 2 3 4 5 6", "queue tabs"], ["/", "search"], ["L", "watch the live show"], ["?", "this list"]]],
];
function showKeys() {
  const k = $("#keys");
  k.innerHTML = `<div class="m-head"><b>Keyboard shortcuts</b><button class="x" onclick="this.closest('dialog').close()">×</button></div>
    <div class="k-body">${KEYS.map(([h, rows]) => `<h4>${h}</h4>` + rows.map(([a, b]) =>
      `<span>${a.split(" ").map(x => `<kbd>${esc(x)}</kbd>`).join(" ")}</span><span>${esc(b)}</span>`).join("")).join("")}
    <span></span><span style="color:var(--faint);font-size:12px;margin-top:8px">Off while you type in a box (Esc leaves the box).</span></div>`;
  k.showModal();
}
$("#keysBtn").onclick = showKeys;

function move(d) {
  if (!VISIBLE.length) return;
  const at = VISIBLE.indexOf(OPEN || SEL);
  if (OPEN && at < 0) return;  // opened from the schedule: not in the queue list
  const to = VISIBLE[at < 0 ? 0 : Math.max(0, Math.min(VISIBLE.length - 1, at + d))];
  if (OPEN && to !== OPEN && !discardOk()) return;
  SEL = to;
  renderQueue();
  if (OPEN) { if (SEL !== OPEN) openModal(SEL); return; }
  $(".card.sel")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}
function keyAct(key, ref) {
  const i = STATE.items.find(x => x.ref === ref);
  if (!i) return;
  if (key === "a") {
    if (i.decision === "approved") return toast("Already approved.");
    if (OPEN && !discardOk()) return;  // approving moves on to the next item
    const at = VISIBLE.indexOf(ref), next = VISIBLE[at + 1] || VISIBLE[at - 1];
    act(ref, "approve");
    if (OPEN) { if (next && next !== ref) { SEL = next; openModal(next); } else closeModal(true); }
  } else if (key === "u") { if (i.decision === "approved") act(ref, "reopen"); else toast("Only approved items reopen."); }
  else if (key === "r") { const n = ask(`Reject ${i.short}? Say why (kept as a scoring example):`, true); if (n) act(ref, "reject", { text: n }); }
  else if (key === "c") {
    if (OPEN) { $("#fChanges").focus(); return; }
    const n = ask("What should change? clipper will re-cut / re-thumbnail it from this note:", true); if (n) act(ref, "changes", { text: n });
  } else if (key === "h") act(ref, "hold", { on: !i.hold });
  else if (key === "p") { if (OPEN !== ref) openModal(ref); $("#fPin").scrollIntoView({ block: "center" }); $("#fPin").focus(); }
}
document.addEventListener("keydown", e => {
  if (e.ctrlKey || e.metaKey || e.altKey || !STATE) return;
  const tg = e.target;
  if (tg.matches?.("input, textarea, select, [contenteditable]")) { if (e.key === "Escape" && OPEN) { e.preventDefault(); tg.blur(); } return; }
  if ($("#keys").open) { if (e.key === "?") $("#keys").close(); return; }
  if ($("#tool").open) return;
  const k = e.key.length === 1 ? e.key.toLowerCase() : e.key, video = OPEN ? $("#modal video") : null;
  const take = () => e.preventDefault();
  if (k === "?") { take(); showKeys(); }
  else if (k === "j" || k === "k") { take(); move(k === "j" ? 1 : -1); }
  else if (k === "Enter" && !OPEN && SEL && !tg.closest?.("button, a")) { take(); openModal(SEL); }
  else if (k === "Escape" && !OPEN) { if (LIVEMENU !== null) { LIVEMENU = null; renderLive(); } else if (SEL) { SEL = null; renderQueue(); } }
  else if ("arhcpu".includes(k) && k.length === 1 && (OPEN || SEL)) { take(); keyAct(k, OPEN || SEL); }
  else if (k === " " && video && tg !== video) { take(); video.paused ? video.play() : video.pause(); }
  else if ((k === "ArrowLeft" || k === "ArrowRight") && video && tg !== video) { take(); video.currentTime += k === "ArrowRight" ? 5 : -5; }
  else if (k === "f" && video) {
    take();
    if (document.pictureInPictureElement) document.exitPictureInPicture();
    else if (video.requestPictureInPicture) video.requestPictureInPicture().catch(err => toast(err.message, true));
  }
  else if (k === "o" && (OPEN || SEL)) { take(); popOut(OPEN || SEL); }
  else if (k === "b" && video) { take(); pickFace(OPEN); }
  else if (/^[1-6]$/.test(k) && !OPEN) { take(); TAB = Object.keys(TABS)[+k - 1]; SEL = null; renderQueue(); }
  else if (k === "/" && !OPEN) { take(); $("#q").focus(); }
  else if (k === "l") {
    const on = LIVE.filter(l => l.live);
    if (!on.length) return toast("Nothing is live right now.");
    take();
    if (on.length === 1) watch(on[0].channel, store.get("warroom.watch") || "here");
    else { LIVEMENU = LIVE.indexOf(on[0]); renderLive(); }
  }
});

// ---------- layouts: drag panels between zones, save named layouts ----------
// A layout is {zones: {zone: [panel, ...]}, widths: {left: px, right: px}}. The one in use is
// kept by this browser; named ones are saved in clipper (/api/layouts, every machine sees them).
const ZONES = ["top", "left", "middle", "right", "bottom", "hidden"];
const DEFAULT_LAYOUT = { zones: { left: ["pipeline", "retakes", "health", "modal", "costs", "usage"], middle: ["queue"],
  right: ["schedule"], bottom: ["stats"] }, widths: { left: 300, right: 340 } };
const WIDTHS = [260, 300, 340, 420, 520, 640];  // side column widths, px
const DESKTOP = matchMedia("(min-width: 701px)");
const HOME = [...document.querySelectorAll("[data-panel]")].map(p => [p, p.parentNode]);  // the page as written (phones)
const PANEL = new Map(HOME.map(([p]) => [p.dataset.panel, p]));  // by name; saved names are never used as selectors
let LAYOUT = currentLayout(), LAYOUT_NAME = store.get("warroom.layoutName") || "", SAVED = {}, EDITING = false;
let PDRAG = null;
const MARK = Object.assign(document.createElement("div"), { className: "drop-mark" });

function currentLayout() {
  try { const l = JSON.parse(store.get("warroom.layout")); if (l && l.zones) return l; } catch { /* none yet */ }
  return structuredClone(DEFAULT_LAYOUT);
}
function zoneEl(z) {
  let el = $(`main > .zone[data-zone="${z}"]`);
  if (!el) {
    el = Object.assign(document.createElement("div"), { className: "zone" });
    el.dataset.zone = z;
    const side = z === "left" || z === "right";
    el.innerHTML = `<div class="zone-h">${z === "hidden" ? "hidden: drag a panel here to hide it" : z}${side
      ? ` <select data-width="${z}" title="Column width">${WIDTHS.map(px => `<option value="${px}">${px} px</option>`).join("")}</select>` : ""}</div>`;
    $("main").append(el);
  }
  return el;
}
function panels(el) { return [...el.querySelectorAll(":scope > section[data-panel]")]; }
function applyLayout() {
  const main = $("main");
  if (!DESKTOP.matches) {  // phones: the page as written, one view at a time
    for (const [p, parent] of HOME) parent.append(p);
    main.querySelectorAll(":scope > .zone").forEach(z => z.remove());
    document.body.classList.remove("layout", "editing");
    return;
  }
  const placed = new Set();
  for (const z of ZONES) {
    const el = zoneEl(z);
    for (const name of LAYOUT.zones[z] || []) {
      const p = PANEL.get(name);
      if (p && !placed.has(p)) { el.append(p); placed.add(p); }
    }
  }
  for (const [p] of HOME) {  // a panel the layout doesn't know (new since it was saved): its default place
    if (placed.has(p)) continue;
    const z = ZONES.find(z => (DEFAULT_LAYOUT.zones[z] || []).includes(p.dataset.panel)) || "middle";
    zoneEl(z).append(p);
  }
  const used = z => EDITING || panels(zoneEl(z)).length > 0;
  let cols = ["left", "middle", "right"].filter(used);
  if (!cols.length) cols = ["middle"];
  const w = { ...DEFAULT_LAYOUT.widths, ...(LAYOUT.widths || {}) };
  const size = cols.map(z => z === "middle" || !cols.includes("middle") && z === cols.at(-1) ? "minmax(0, 1fr)" : `${w[z]}px`);
  const rows = [["top"], [], ["bottom"], ["hidden"]].map((r, i) => i === 1 ? cols : cols.map(() => r[0]))
    .filter((r, i) => i === 1 || (i === 3 ? EDITING : used(r[0])));
  main.style.setProperty("--cols", size.join(" "));
  main.style.setProperty("--areas", rows.map(r => `"${r.join(" ")}"`).join(" "));
  for (const z of ZONES) {
    const el = zoneEl(z);
    el.style.display = (z === "hidden" ? EDITING : used(z)) && (rows.flat().includes(z)) ? "" : "none";
    const sel = el.querySelector("select[data-width]");
    if (sel) sel.value = String(w[z]);
  }
  for (const [p] of HOME) {
    const h = p.querySelector(":scope > h2");
    h.draggable = EDITING;
    if (EDITING) { h.tabIndex = 0; h.title = "Drag, or ↑/↓ move, ←/→ zone, H hide"; }
    else { h.removeAttribute("tabindex"); h.removeAttribute("title"); }
  }
  document.body.classList.add("layout");
  document.body.classList.toggle("editing", EDITING);
}
function readLayout() {
  const zones = {};
  for (const z of ZONES) zones[z] = panels(zoneEl(z)).map(p => p.dataset.panel);
  return { zones, widths: { ...DEFAULT_LAYOUT.widths, ...(LAYOUT.widths || {}) } };
}
function keepLayout() { store.set("warroom.layout", JSON.stringify(LAYOUT)); store.set("warroom.layoutName", LAYOUT_NAME || null); }
function sameLayout(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
function useLayout(lay, name) { LAYOUT = structuredClone(lay); LAYOUT_NAME = name; keepLayout(); applyLayout(); }

document.addEventListener("dragstart", e => {
  const h = EDITING && e.target.closest?.("section[data-panel] > h2");
  if (!h) return;
  PDRAG = h.parentNode;
  PDRAG.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", PDRAG.dataset.panel);
});
document.addEventListener("dragover", e => {
  const zone = PDRAG && e.target.closest?.("main > .zone");
  if (!zone) return;
  e.preventDefault();
  const row = zone.dataset.zone === "hidden";
  const before = panels(zone).filter(p => p !== PDRAG).find(p => {
    const r = p.getBoundingClientRect();
    return row ? e.clientY < r.bottom && e.clientX < r.left + r.width / 2 : e.clientY < r.top + r.height / 2;
  });
  if (before) zone.insertBefore(MARK, before); else zone.append(MARK);
});
document.addEventListener("drop", e => {
  if (!PDRAG || !MARK.parentNode) return;
  e.preventDefault();
  MARK.parentNode.insertBefore(PDRAG, MARK);
  MARK.remove();
  LAYOUT = readLayout(); keepLayout(); applyLayout();
});
document.addEventListener("dragend", () => { PDRAG?.classList.remove("dragging"); PDRAG = null; MARK.remove(); });
document.addEventListener("change", e => {
  const side = e.target.dataset?.width;
  if (!side) return;
  LAYOUT.widths = { ...DEFAULT_LAYOUT.widths, ...(LAYOUT.widths || {}), [side]: +e.target.value };
  keepLayout(); applyLayout();
});

function closeLayoutMenu() { $("#layoutMenu")?.remove(); }
async function openLayoutMenu() {
  try { SAVED = (await api("/api/layouts")).layouts || {}; } catch (e) { toast("Saved layouts: " + e.message, true); }
  closeLayoutMenu();
  const changed = LAYOUT_NAME && SAVED[LAYOUT_NAME] && !sameLayout(SAVED[LAYOUT_NAME], LAYOUT);
  const m = Object.assign(document.createElement("div"), { className: "layout-menu", id: "layoutMenu" });
  m.innerHTML = `<button data-lay="edit">${EDITING ? "Done arranging" : "Arrange panels…"}<span>drag titles</span></button>
    ${changed ? `<button data-lay="save">Save “${esc(LAYOUT_NAME)}”</button>` : ""}
    <button data-lay="saveas">Save as…</button>
    <div class="sep"></div><div class="lbl">SAVED LAYOUTS</div>
    ${Object.keys(SAVED).sort().map(n => `<div class="row"><button data-lay="use" data-name="${esc(n)}" class="${n === LAYOUT_NAME ? "on" : ""}">${esc(n)}${n === LAYOUT_NAME && changed ? " (changed)" : ""}</button><button class="del" data-lay="del" data-name="${esc(n)}" title="Delete">×</button></div>`).join("")
      || `<div class="lbl" style="font-weight:400;letter-spacing:0">none yet</div>`}
    <div class="sep"></div><button data-lay="default">Default layout</button>`;
  document.body.append(m);
  const r = $("#layoutBtn").getBoundingClientRect();
  m.style.top = `${r.bottom + 6}px`; m.style.right = `${innerWidth - r.right}px`;
}
async function saveLayoutAs(name) {
  try {
    SAVED = (await api("/api/layouts", { name, layout: LAYOUT })).layouts;
    LAYOUT_NAME = name; keepLayout(); toast(`Layout “${name}” saved`);
  } catch (e) { toast(e.message, true); }
}
$("#layoutBtn").onclick = e => { e.stopPropagation(); $("#layoutMenu") ? closeLayoutMenu() : openLayoutMenu(); };
document.addEventListener("click", async e => {
  const b = e.target.closest?.("#layoutMenu [data-lay]");
  if (!b) { if (!e.target.closest?.("#layoutMenu")) closeLayoutMenu(); return; }
  const what = b.dataset.lay, name = b.dataset.name;
  closeLayoutMenu();
  if (what === "edit") { EDITING = !EDITING; applyLayout(); if (EDITING) toast("Drag a panel by its title. Layout → Done when finished."); }
  else if (what === "save") saveLayoutAs(LAYOUT_NAME);
  else if (what === "saveas") { const n = prompt("Save this layout as", LAYOUT_NAME || "My layout"); if (n && n.trim()) saveLayoutAs(n.trim()); }
  else if (what === "use") useLayout(SAVED[name], name);
  else if (what === "default") useLayout(DEFAULT_LAYOUT, "");
  else if (what === "del" && confirm(`Delete the layout “${name}”?`)) {
    try { SAVED = (await api("/api/layouts", { name, delete: true })).layouts; if (name === LAYOUT_NAME) { LAYOUT_NAME = ""; keepLayout(); } toast(`Deleted “${name}”`); }
    catch (err) { toast(err.message, true); }
  }
});
// Keyboard arranging (no mouse): on a focused panel title, ↑/↓ move it within its zone,
// ←/→ to the previous/next zone, H to the hidden tray. Caught before the queue's own keys.
document.addEventListener("keydown", e => {
  if (!EDITING) return;
  if (e.key === "Escape") { EDITING = false; applyLayout(); return; }
  const h = e.target.closest?.("section[data-panel] > h2");
  const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  if (!h || !["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "h"].includes(k)) return;
  e.preventDefault(); e.stopImmediatePropagation();
  const p = h.parentNode, zone = p.parentNode, z = ZONES.indexOf(zone.dataset.zone);
  if (k === "ArrowUp" && p.previousElementSibling?.matches("section")) zone.insertBefore(p, p.previousElementSibling);
  else if (k === "ArrowDown" && p.nextElementSibling) p.nextElementSibling.after(p);
  else if (k === "ArrowLeft" || k === "ArrowRight") {
    const to = ZONES[Math.min(ZONES.length - 1, Math.max(0, z + (k === "ArrowLeft" ? -1 : 1)))];
    zoneEl(to).append(p);
  } else if (k === "h") zoneEl("hidden").append(p);
  LAYOUT = readLayout(); keepLayout(); applyLayout();
  h.focus();
  toast(`${p.dataset.panel} → ${p.parentNode.dataset.zone}`);
}, true);
DESKTOP.addEventListener("change", applyLayout);
addEventListener("resize", () => { if (DESKTOP.matches !== document.body.classList.contains("layout")) applyLayout(); });
applyLayout();

// Everything that polls the server: [refresh, every ms]. Each runs now, then on its timer.
const POLL = [[refresh, 15000], [refreshLive, 60000], [refreshCloud, 60000], [refreshCosts, 300000],
              [refreshUsage, 300000], [refreshStats, 900000]];
loadHash();
// #201: a tab in the background doesn't poll; coming back runs whatever fell due while it was away.
const POLLED = new Map();
function poll(fn, every) {
  if (document.hidden || Date.now() - (POLLED.get(fn) || 0) < every - 1000) return;
  POLLED.set(fn, Date.now()); fn();
}
for (const [fn, every] of POLL) { poll(fn, every); setInterval(() => poll(fn, every), every); }
document.addEventListener("visibilitychange", () => { for (const [fn, every] of POLL) poll(fn, every); });
