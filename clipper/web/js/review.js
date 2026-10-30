// War room: acting on items: decisions, undo, select and bulk edits, the item window, style checks.

// ---------- actions ----------

async function act(ref, action, extra = {}) {
  BUSY.add(ref); renderQueue();
  try {
    const r = await api("/api/action", { ref, action, ...extra });
    const i = STATE.items.findIndex(x => x.ref === ref);
    if (i >= 0) STATE.items[i] = { ...STATE.items[i], ...r.row };
    toast(`${{ approve: "Approved", reject: "Rejected", changes: "Changes requested for", reopen: "Reopened", hold: extra.on ? "Held" : "Released", pin: extra.text ? "Pinned" : "Unpinned", edit: "Saved", thumbtext: "New thumbnail words for" }[action]} ${ref.split("/")[1].split("_")[0]}${r.note ? " — " + r.note : ""}`, false, r.undo);
    if (OPEN === ref) fillModalState(STATE.items.find(x => x.ref === ref));
    refresh();
  } catch (e) { toast(e.message, true); }
  finally { BUSY.delete(ref); renderQueue(); }
}
function upsert(row) { const k = STATE.items.findIndex(x => x.ref === row.ref); if (k >= 0) STATE.items[k] = { ...STATE.items[k], ...row }; }

// ---------- #23 undo ----------

async function undo(id) {
  try {
    const r = await api("/api/undo", { id });
    (r.rows || []).forEach(upsert);
    const f = r.failed || [];
    toast(`Undone (${r.undone})${f.length ? `; ${f.length} failed: ${f[0].ref.split("/")[1]}: ${f[0].error}` : ""}${r.note ? " — " + r.note : ""}`, !!f.length);
    if ($("#tool").open && $("#tool").dataset.kind === "undo") showUndo();
    renderQueue(); refresh();
  } catch (e) { toast(e.message, true); }
}
async function showUndo() {
  const d = $("#tool"); d.dataset.kind = "undo";
  let list = [];
  try { list = (await api("/api/undo")).actions; } catch (e) { return toast(e.message, true); }
  d.innerHTML = `<div class="m-head"><b>Recent war room actions</b><span class="sp" style="color:var(--faint);font-size:12px">the last 20, newest first</span><button class="x" data-close>×</button></div>
    <div class="tbody"><div class="undo-list">${list.map(a => `<span class="f">${esc(ago(a.at))}</span>
      <span class="${a.undone ? "gone" : ""}">${esc(a.label)}${a.uploaded ? ` <span style="color:var(--amber)" title="An upload can't be undone: change it in YouTube Studio">· ${a.uploaded} on YouTube</span>` : ""}</span>
      ${a.undone ? `<span class="f">undone</span>` : `<button class="btn" data-undo="${esc(a.id)}">Undo</button>`}`).join("")
      || `<span></span><span class="f">Nothing yet.</span><span></span>`}</div></div>`;
  if (!d.open) d.showModal();
}

// ---------- #17 select, bulk actions, find and replace ----------

function picked() { return [...PICK].filter(r => STATE.items.some(i => i.ref === r)); }
function renderBulk() {
  const n = picked().length, b = $("#bulk");
  b.hidden = !n;
  if (!n) return;
  b.innerHTML = `<b>${n} selected</b>
    <button class="btn good" data-bulk="approve">✓ Approve</button>
    <button class="btn" data-bulk="hold">Hold</button><button class="btn" data-bulk="release">Release</button>
    <button class="btn" data-bulk="pin" title="Publish at for all of them">📌 Pin…</button>
    <button class="btn warn" data-bulk="changes">✎ Same retake note…</button>
    <button class="btn" data-bulk="reopen">↺ Reopen</button>
    <button class="btn" data-bulk="replace">Find and replace…</button>
    <button class="btn" data-bulk="clear">Clear</button>`;
}
function pick(ref, on, range) {
  if (range && LASTPICK && VISIBLE.includes(LASTPICK) && VISIBLE.includes(ref)) {
    const [a, b] = [VISIBLE.indexOf(LASTPICK), VISIBLE.indexOf(ref)].sort((x, y) => x - y);
    VISIBLE.slice(a, b + 1).forEach(r => on ? PICK.add(r) : PICK.delete(r));
  } else on ? PICK.add(ref) : PICK.delete(ref);
  LASTPICK = ref;
  renderQueue();
}
async function bulk(action) {
  const refs = picked(), n = refs.length;
  if (action === "clear") { PICK.clear(); renderQueue(); return; }
  if (action === "replace") return showReplace();
  const extra = {};
  let api_action = action;
  if (action === "hold" || action === "release") { api_action = "hold"; extra.on = action === "hold"; }
  if (action === "pin") {
    const t = ask(`Publish at for ${n} items: YYYY-MM-DD HH:MM, or a date (each gets that day's first slot, so give times to spread them); empty unpins`, false);
    if (t === null) return; extra.text = t;
  }
  if (action === "changes") { const t = ask(`What should change, for all ${n}? clipper re-cuts each from this note:`, true); if (!t) return; extra.text = t; }
  if (!["changes", "pin"].includes(action) && !confirm(`${action[0].toUpperCase() + action.slice(1)} ${n} item${n > 1 ? "s" : ""}?`)) return;
  refs.forEach(r => BUSY.add(r)); renderQueue();
  try {
    const r = await api("/api/bulk", { action: api_action, refs, ...extra });
    (r.rows || []).forEach(upsert);
    const f = r.failed || [];
    toast(`${action}: ${r.done} done${f.length ? `, ${f.length} failed (${f[0].ref.split("/")[1]}: ${f[0].error})` : ""}`, !!f.length && !r.done, r.undo);
    refresh();
  } catch (e) { toast(e.message, true); }
  finally { refs.forEach(r => BUSY.delete(r)); renderQueue(); }
}
const REP_FIELDS = { title: "Title", description: "Description", hashtags: "Hashtags", tags: "Tags", playlist: "Playlist" };
let REP_LAST = "";  // the inputs the last dry run was for: Apply needs a preview of exactly these
function repBody(dry) {
  return { refs: picked(), find: $("#rFind").value, replace: $("#rRepl").value, dry_run: dry,
    fields: Object.keys(REP_FIELDS).filter(f => $(`#rf_${f}`).checked),
    word: $("#rWord").checked, case: $("#rCase").checked, regex: $("#rRegex").checked };
}
function showReplace() {
  const d = $("#tool"), n = picked().length; d.dataset.kind = "replace";
  d.innerHTML = `<div class="m-head"><b>Find and replace</b><span style="color:var(--faint);font-size:12px">in ${n} selected item${n > 1 ? "s" : ""}, through Kitsu</span><button class="x" data-close>×</button></div>
    <div class="tbody">
      <input class="search" id="rFind" placeholder="Find">
      <input class="search" id="rRepl" placeholder="Replace with (empty deletes it)">
      <div>${Object.entries(REP_FIELDS).map(([f, l]) => `<label><input type="checkbox" id="rf_${f}" ${["title", "description", "hashtags"].includes(f) ? "checked" : ""}>${l}</label>`).join("")}</div>
      <div><label><input type="checkbox" id="rWord" checked>Whole words</label><label><input type="checkbox" id="rCase">Match case</label><label><input type="checkbox" id="rRegex">Regular expression</label></div>
      <div><button class="btn" id="rPrev">Preview (dry run)</button> <button class="btn primary" id="rApply" disabled>Apply</button></div>
      <div class="rep-list" id="rList"><span class="f">Preview first: every line that would change is listed before anything is written.</span></div>
    </div>`;
  const stale = () => { $("#rApply").disabled = JSON.stringify(repBody(true)) !== REP_LAST; };
  d.querySelectorAll("input").forEach(el => el.addEventListener("input", stale));
  $("#rPrev").onclick = async () => {
    const body = repBody(true);
    try {
      const r = await api("/api/replace", body);
      const lines = r.items.reduce((s, it) => s + it.lines.length, 0);
      $("#rList").innerHTML = `<div class="f">${r.items.length} of ${r.checked} items would change, ${lines} lines:</div>` + r.items.map(it => `<div class="it">
        <b>${esc(it.short)}</b>${it.uploaded ? ` <span style="color:var(--amber)">· on YouTube already (Kitsu only; the live video keeps its text)</span>` : ""}
        ${it.lines.map(l => `<div><span class="f">${esc(l.field)}</span> <del>${esc(l.before)}</del><br><span class="f">${" ".repeat(l.field.length)}</span> <ins>${esc(l.after)}</ins></div>`).join("")}</div>`).join("");
      REP_LAST = JSON.stringify(body);
      $("#rApply").disabled = !r.items.length;
      $("#rApply").textContent = `Apply to ${r.items.length} item${r.items.length === 1 ? "" : "s"}`;
    } catch (e) { $("#rList").innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; }
  };
  $("#rApply").onclick = async () => {
    $("#rApply").disabled = true;
    try {
      const r = await api("/api/replace", repBody(false));
      (r.rows || []).forEach(upsert);
      const f = r.failed || [];
      toast(`Replaced in ${r.done} items${f.length ? `; ${f.length} failed (${f[0].ref.split("/")[1]}: ${f[0].error})` : ""}`, !!f.length && !r.done, r.undo);
      d.close(); renderQueue(); refresh();
    } catch (e) { toast(e.message, true); $("#rApply").disabled = false; }
  };
  d.showModal(); $("#rFind").focus();
}

function ask(label, required) { const t = prompt(label); if (t === null) return null; if (required && !t.trim()) { toast("That needs some text.", true); return null; } return t.trim(); }

document.addEventListener("click", e => {
  const lv = e.target.closest("[data-live]");
  if (lv) { liveClick(e, lv); return; }
  if (LIVEMENU !== null) { LIVEMENU = null; renderLive(); }
  const v = e.target.closest("#views button, [data-move]");  // not body[data-view]
  if (v?.dataset.view) { setView(v.dataset.view); return; }
  if (v?.dataset.move) { move(+v.dataset.move); return; }
  const pop = e.target.closest("[data-pop]");
  if (pop) { popOut(pop.dataset.pop); return; }
  const pk = e.target.closest("[data-pick]");
  if (pk) { pick(pk.dataset.pick, pk.checked, e.shiftKey); return; }
  if (e.target.closest(".pickl")) return;
  const bk = e.target.closest("[data-bulk]");
  if (bk) { bulk(bk.dataset.bulk); return; }
  const un = e.target.closest("[data-undo]");
  if (un) { undo(un.dataset.undo); return; }
  if (e.target.closest("[data-close]")) { e.target.closest("dialog").close(); return; }
  if (e.target.id === "undoBtn") { showUndo(); return; }
  if (e.target.id === "pickAll") { VISIBLE.forEach(r => PICK.add(r)); renderQueue(); return; }
  if (e.target.closest("a[href]")) return;  // links inside a clickable row (YouTube) just open
  const t = e.target.closest("[data-tab],[data-act],[data-open]");
  if (!t) return;
  if (t.dataset.tab) { TAB = t.dataset.tab; SEL = null; renderQueue(); return; }
  if (t.dataset.act) {
    const ref = t.dataset.ref, a = t.dataset.act;
    if (a === "changes") { const n = ask("What should change? clipper will re-cut / re-thumbnail it from this note:", true); if (n) act(ref, "changes", { text: n }); }
    else if (a === "reject") { const n = ask("Why reject it? (kept as a scoring example)", true); if (n) act(ref, "reject", { text: n }); }
    else if (a === "approve" && OPEN === ref) keyAct("a", ref);  // in the item window: then the next item
    else act(ref, a);
    return;
  }
  if (t.dataset.open) { if (VISIBLE.includes(t.dataset.open)) SEL = t.dataset.open; openModal(t.dataset.open); }
});
document.addEventListener("change", e => {
  if (e.target.id === "showSel") { SHOW = e.target.value; renderQueue(); }
  if (e.target.id === "kindSel") { KINDF = e.target.value; renderQueue(); }
  if (e.target.id === "stSel") { ST = e.target.value; renderQueue(); }
  if (e.target.id === "fromD") { FROM = e.target.value; renderQueue(); }
  if (e.target.id === "toD") { TO = e.target.value; renderQueue(); }
});
document.addEventListener("input", e => { if (e.target.id === "q") { Q = e.target.value; searchTranscripts(); const pos = e.target.selectionStart; renderQueue(); const q = $("#q"); q.focus(); q.setSelectionRange(pos, pos); } });
$("#syncBtn").onclick = async () => { try { await api("/api/sync", {}); toast("Syncing from Kitsu…"); setTimeout(refresh, 4000); } catch (e) { toast(e.message, true); } };
$("#replanBtn").onclick = async () => { try { await api("/api/replan", {}); toast("Schedule replanned"); refresh(); } catch (e) { toast(e.message, true); } };

// ---------- modal ----------

function openModal(ref) {
  const i = STATE.items.find(x => x.ref === ref);
  if (!i) { toast("Not published to Kitsu yet"); return; }
  OPEN = ref;
  const m = $("#modal");
  const hist = STATE.retakes.filter(h => `${h.stream}/${h.key}` === ref);
  m.innerHTML = `
    <div class="m-head"><span class="kind ${esc(i.kind)}">${esc(KIND[i.kind] || i.kind)}</span><b>${esc(i.short)}</b><span class="tag">${esc(i.show_name)}</span>
      <span id="mState" class="state ${esc(i.decision)}" style="position:static"></span>
      <button class="x" onclick="closeModal()">×</button></div>
    <div class="m-body">
      <div class="player">
        ${!i.video_url ? `<div class="empty">No preview file</div>` : PHONE.matches
          ? `<button class="tap ${i.kind === "short" ? "vertical" : ""}" id="mTap">${i.thumbnail_url ? `<img src="${i.thumbnail_url}" alt="">` : ""}<span><b>▶</b>Play (phone size)</span></button>`
          : `<video controls preload="metadata" src="${i.video_url}"></video>`}
        ${i.video_url && !PHONE.matches ? `<div class="row" style="margin-top:6px"><button class="btn" data-pop="${esc(i.ref)}" title="Open the video in its own window (O)">⧉ Pop out</button></div>` : ""}
        ${i.thumbnail_url ? `<img src="${i.thumbnail_url}" alt="thumbnail">` : ""}
        ${i.thumbnail_url && i.kind !== "short" ? `<div class="row" style="margin-top:6px"><button class="btn" data-cover="${esc(i.ref)}" title="Frame, face box, photos and words; re-renders without model calls">🖼 Edit cover</button></div>` : ""}
        <div class="kv">
          <b>Length</b><span>${mmss(i.duration_s)}</span>
          <b>Stream</b><span>${esc(i.stream)} · ${esc(i.date)}</span>
          ${i.score ? `<b>Score</b><span>${esc(i.score)} · ${esc(i.category)} · ${esc(i.found_by)}</span>` : ""}
          <b>Revision</b><span>${esc(i.revision)} ${i.synced_at ? "· synced " + ago(i.synced_at) : ""}</span>
          <b>Links</b><span><a href="${esc(i.kitsu_url)}" target="_blank">Kitsu</a>${i.youtube_url ? ` · <a href="${esc(i.youtube_url)}" target="_blank">YouTube</a>` : ""}</span>
        </div>
        ${hist.length ? `<div class="form"><label>Retakes</label>${hist.map(h => `<div class="ev" style="padding:6px 0"><div class="w">${esc(ago(h.at))}</div><q>${esc(h.comment)}</q><div class="r-${esc(h.result)}">${esc(h.summary || h.unsupported || h.error)}</div></div>`).join("")}</div>` : ""}
      </div>
      <div class="form">
        <div class="row" id="mActs"></div>
        <label>Ask for changes (clipper does the retake)</label>
        <textarea id="fChanges" placeholder="e.g. start 5 s earlier · the thumbnail shows the back of his head · use the cams layout"></textarea>
        <div class="row"><button class="btn warn" id="bChanges">✎ Send retake note</button><button class="btn bad" id="bReject">✕ Reject with this as the reason</button></div>
        <label>YouTube title</label><input id="fTitle" maxlength="100" data-check="title"><div class="warn-line" id="w_title"></div>
        <label>Description</label><textarea id="fDesc" style="min-height:120px" data-check="description"></textarea><div class="warn-line" id="w_description"></div>
        <label>Hashtags</label><input id="fTags" data-check="hashtags"><div class="warn-line" id="w_hashtags"></div>
        <div class="row"><button class="btn" id="bSave">Save title / description / hashtags</button></div>
        ${i.kind !== "short" ? `<label>Thumbnail words</label><input id="fThumb" maxlength="40" data-check="thumb_text"><div class="warn-line" id="w_thumb_text"></div><div class="row"><button class="btn" id="bThumb">Re-render thumbnail</button></div>` : ""}
        <label>Pin release time</label><input id="fPin" type="datetime-local">
        <div class="hint" id="pinHint"></div>
        <div class="row"><button class="btn" id="bPin">Pin</button><button class="btn" id="bUnpin">Unpin</button></div>
      </div>
    </div>`;
  fillModalState(i, true);
  $("#bChanges").onclick = () => { const n = $("#fChanges").value.trim(); if (!n) return toast("Write what should change.", true); act(ref, "changes", { text: n }); $("#fChanges").value = ""; };
  $("#bReject").onclick = () => { const n = $("#fChanges").value.trim(); if (!n) return toast("Write the reason in the box first.", true); act(ref, "reject", { text: n }); };
  $("#bSave").onclick = () => {
    const cur = STATE.items.find(x => x.ref === ref), values = {};
    if ($("#fTitle").value.trim() !== cur.title) values["YouTube title"] = $("#fTitle").value;
    if ($("#fDesc").value.trim() !== (cur.description || "").trim()) values["YouTube description"] = $("#fDesc").value;
    if ($("#fTags").value.trim() !== (cur.hashtags || "").trim()) values["Hashtags"] = $("#fTags").value;
    if (!Object.keys(values).length) return toast("Nothing changed.");
    act(ref, "edit", { values });
  };
  if ($("#bThumb")) $("#bThumb").onclick = () => act(ref, "thumbtext", { text: $("#fThumb").value });
  $("#bPin").onclick = () => { const v = $("#fPin").value; if (!v) return toast("Pick a date and time.", true); act(ref, "pin", { text: v.replace("T", " ") }); };
  $("#bUnpin").onclick = () => act(ref, "pin", { text: "" });
  showIssues(i.issues || []);
  m.querySelectorAll("[data-check]").forEach(el => el.addEventListener("input", () => checkSoon(ref)));
  if ($("#mTap")) $("#mTap").onclick = e => playLow(i, !!e.target.closest("[data-full]"));
  if (!m.open) m.showModal();  // J / K swap the item in an open window
  m.querySelector(".m-body").scrollTop = 0;
}
function fillModalState(i, fields) {
  if (!i || !$("#mActs")) return;
  $("#mState").className = `state ${i.decision}`; $("#mState").textContent = (DEC[i.decision] || i.decision) + (i.hold ? " · held" : "");
  const r = esc(i.ref), queued = VISIBLE.includes(i.ref);
  $("#mActs").innerHTML = (queued ? `<button class="btn nav" data-move="-1" title="Previous (K, or swipe right)">‹</button>` : "")
    + (i.decision !== "approved" ? `<button class="btn good" data-act="approve" data-ref="${r}" title="Approve, then the next item (A)">✓ Approve</button>` : `<button class="btn" data-act="reopen" data-ref="${r}">↺ Reopen</button>`)
    + `<button class="btn warn" data-act="changes" data-ref="${r}" title="Retake note (C)">✎</button>`
    + `<button class="btn bad" data-act="reject" data-ref="${r}" title="Reject (R)">✕</button>`
    + `<button class="btn" id="bHold" title="${i.hold ? "Release hold" : "Hold"} (H)">${i.hold ? "▶" : "⏸"}${PHONE.matches ? "" : i.hold ? " Release" : " Hold"}</button>`
    + (queued ? `<button class="btn nav" data-move="1" title="Next (J, or swipe left)">›</button>` : "");
  $("#bHold").onclick = () => act(i.ref, "hold", { on: !i.hold });
  $("#pinHint").textContent = i.scheduled ? `Scheduled ${new Date(i.scheduled).toLocaleString()}${i.publish_at ? " (pinned)" : ""}` : i.decision === "approved" ? "Approved, waiting for a free slot" : "Gets a slot once approved";
  if (fields) {
    $("#fTitle").value = i.title || ""; $("#fDesc").value = i.description || ""; $("#fTags").value = i.hashtags || "";
    if ($("#fThumb")) $("#fThumb").value = i.thumb_text || "";
    if (i.publish_at) $("#fPin").value = i.publish_at.replace(" ", "T").slice(0, 16);
  }
}
// ---------- style checks (#19) ----------

let CHECK_T = null;
function showIssues(issues) {
  document.querySelectorAll("#modal .warn-line").forEach(w => { w.textContent = ""; });
  for (const x of issues) { const w = $(`#w_${x.field}`); if (w) w.textContent += (w.textContent ? " · " : "⚠ ") + x.msg; }
}
function checkSoon(ref) {  // same rules as the server, as you type
  clearTimeout(CHECK_T);
  CHECK_T = setTimeout(async () => {
    if (OPEN !== ref) return;
    const values = {};
    document.querySelectorAll("#modal [data-check]").forEach(el => { values[el.dataset.check] = el.value; });
    try { const r = await api("/api/check", { ref, values }); if (OPEN === ref) showIssues(r.issues); } catch { /* keep the last */ }
  }, 400);
}
