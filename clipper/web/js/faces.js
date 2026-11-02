// War room: who is who on screen (#54). A stream's Faces window names and enrolls its face
// groups; the box tool picks one face on the item window's paused video.

let FC = null;  // {stream, data, filter: "all" | "unnamed" | "named"}
let FP = null;  // {ref, stream, t, frame (data URL), box, found, busy}
let FACEWIN = 0;  // which faces window is open: an answer for an older one is dropped
const faceWin = (win, kind) => win === FACEWIN && $("#tool").open && $("#tool").dataset.kind === kind;
const knownList = known => `<datalist id="fcKnown">${Object.keys(known || {}).map(n => `<option value="${esc(n)}">`).join("")}</datalist>`;
const knownChips = known => Object.entries(known || {}).map(([n, k]) =>
  `<span class="fk">${esc(n)} <i>${k}</i><button data-fcforget="${esc(n)}" title="Forget ${esc(n)}'s face (every sample)">×</button></span>`).join("")
  || `<span style="color:var(--faint)">nobody yet</span>`;

// ---------- a stream's groups ----------

async function showFaces(stream) {
  const d = $("#tool");
  const win = ++FACEWIN, filter = FC?.stream === stream ? FC.filter : "all";
  FP = null; FC = null;
  d.dataset.kind = "faces"; d.classList.remove("cover"); d.classList.add("facesw");
  d.innerHTML = `<div class="m-head"><b>Faces</b><span style="color:var(--faint);font-size:12px">${esc(stream)}</span><button class="x" data-close>×</button></div><div class="tbody">Loading…</div>`;
  if (!d.open) d.showModal();
  let data;
  try { data = await api("/api/faces?stream=" + encodeURIComponent(stream)); }
  catch (e) { if (faceWin(win, "faces")) d.querySelector(".tbody").innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`; return; }
  if (!faceWin(win, "faces")) return;
  FC = { stream, data, filter, win };
  renderFaces();
}
function groupWho(g) {
  if (g.name) return `<b>${esc(g.name)}</b> <span class="by">${g.by === "face" ? `by face ${(+g.match).toFixed(2)}` : "by hand"}</span>`;
  return `<b style="color:var(--faint)">?</b>${g.closest ? ` <span class="by">closest ${esc(g.closest)} ${(+g.match).toFixed(2)}</span>` : ""}`;
}
function renderFaces() {
  const { data, filter } = FC, d = $("#tool");
  const shown = data.groups.filter(g => filter === "all" || (filter === "named") === !!g.name);
  d.innerHTML = `<div class="m-head"><b>Faces</b><span style="color:var(--faint);font-size:12px">${esc(data.stream)}${data.scanned ? ` · ${data.faces} faces in ${data.frames} keyframes` : ""}</span><button class="x" data-close>×</button></div>
    <div class="tbody">
      <div class="fknown"><span class="f">Known faces (every show)</span>${knownChips(data.known)}</div>
      ${!data.scanned ? `<div class="empty">Not scanned yet: <code>clipper faces ${esc(data.stream)}</code> (about 8 min on a quiet NAS). The box tool in an item window works without it.</div>` : `
      <div class="f" style="font-size:12px;color:var(--dim)">Each sheet's first row is the group's clearest faces, the second a spread over the stream (a group that's two people shows there).
        Name keeps it for this stream; Name + enroll keeps the face for every stream and names its look-alikes here.</div>
      <div><span class="seg">${["all", "unnamed", "named"].map(f => `<button data-fcfilter="${f}" class="${filter === f ? "on" : ""}">${f}</button>`).join("")}</span></div>
      <div class="fgroups">${shown.map(g => `<div class="fg" data-fg="${esc(g.key)}">
          <div class="fh"><span class="k">${esc(g.key)}</span>${groupWho(g)}<span class="f">${g.n} faces · ${mmss(g.first_s)}–${mmss(g.last_s)} · ${g.median_px} px</span></div>
          ${g.sheet_url ? `<img loading="lazy" src="${esc(g.sheet_url)}" alt="${esc(g.key)} faces">` : `<div class="f">no sheet</div>`}
          <div class="fa"><input class="search" list="fcKnown" data-fcname="${esc(g.key)}" placeholder="Who is this?" value="${esc(g.name || "")}">
            <button class="btn" data-fcset="${esc(g.key)}">Name</button><button class="btn primary" data-fcset="${esc(g.key)}" data-enroll="1">Name + enroll</button>
            ${g.by === "face" ? `<button class="btn" data-fcset="${esc(g.key)}" data-same="1" title="Keep ${esc(g.name)} as a hand name">✓ Right</button>` : ""}
            ${g.name ? `<button class="btn" data-fcclear="${esc(g.key)}">Clear</button>` : ""}</div></div>`).join("") || `<div class="empty">None here.</div>`}</div>
      ${data.more ? `<div class="f" style="color:var(--faint);font-size:12px">… and ${data.more} smaller groups without a sheet.</div>` : ""}`}
    </div>${knownList(data.known)}`;
}
async function facesName(key, name, enroll) {
  const row = document.querySelector(`[data-fg="${key}"]`);  // rewriting faces.json on the NAS takes a few seconds
  row?.querySelectorAll("button").forEach(x => { x.disabled = true; });
  const btn = row?.querySelector(enroll ? "[data-enroll]" : name ? "[data-fcset]:not([data-enroll])" : "[data-fcclear]");
  if (btn) btn.textContent = "Saving…";
  const { stream, win } = FC;
  try {
    const r = await api("/api/faces/name", { stream, names: { [key]: name }, enroll });
    toast(name ? `${key} is ${name}${enroll ? ", enrolled" : ""}` : `${key} cleared`);
    if (faceWin(win, "faces")) { FC.data = r; renderFaces(); }
  } catch (e) { toast(e.message, true); if (faceWin(win, "faces")) renderFaces(); }
}
async function facesForget(name, stream) {
  if (!confirm(`Forget ${name}'s face? Every sample goes; groups named by it lose the name.`)) return;
  const win = FACEWIN;
  try {
    const r = await api("/api/faces/forget", { stream, name });
    toast(`Forgot ${name}`);
    if (faceWin(win, "faces") && FC?.stream === stream) { FC.data = r; renderFaces(); }
    else if (faceWin(win, "facepick") && FP?.stream === stream) { FP.known = r.known; renderPick(); }
  } catch (e) { toast(e.message, true); }
}

// ---------- the box tool ----------

function pickFace(ref) {
  const i = STATE.items.find(x => x.ref === ref), v = OPEN === ref ? $("#modal video") : null;
  if (!i || !v) return;
  if (v.readyState < 2 || !v.videoWidth) return toast("Play the video to the face first.", true);
  v.pause();
  FC = null;
  const win = ++FACEWIN;
  const c = document.createElement("canvas");
  c.width = v.videoWidth; c.height = v.videoHeight;
  c.getContext("2d").drawImage(v, 0, 0);
  FP = { ref, stream: i.stream, t: v.currentTime, frame: c.toDataURL("image/jpeg", 0.85), box: null, found: null, busy: false, known: {}, win };
  const d = $("#tool");
  d.dataset.kind = "facepick"; d.classList.remove("cover"); d.classList.add("facesw");
  if (!d.open) d.showModal();
  renderPick();
  api("/api/faces?stream=" + encodeURIComponent(i.stream)).then(r => { if (faceWin(win, "facepick")) { FP.known = r.known; renderPick(); } }).catch(() => {});
}
function renderPick() {
  const { box: b, found: f } = FP;
  $("#tool").innerHTML = `<div class="m-head"><b>Who's this?</b><span style="color:var(--faint);font-size:12px">${esc(FP.ref)} · ${mmss(FP.t)}</span><button class="x" data-close>×</button></div>
    <div class="cv"><div>
      <div class="boxwrap fpwrap" id="fpWrap"><img src="${FP.frame}" alt="" draggable="false">${b ? `<div class="box" style="left:${b.x * 100}%;top:${b.y * 100}%;width:${b.width * 100}%;height:${b.height * 100}%"></div>` : ""}</div>
      <div class="f" style="font-size:11px;color:var(--faint);margin-top:6px">Drag a box round one face, forehead to chin. clipper finds it in the full-size frame.</div>
    </div><div class="form">
      ${FP.busy ? `<div class="f">Looking…</div>` : f ? `
        <div class="fpres"><img src="${f.tile}" alt="the face"><div>
          <div>${f.closest && f.match >= 0.6 ? `Looks like <b>${esc(f.closest)}</b>` : f.closest ? `Nobody known (closest ${esc(f.closest)})` : "Nobody known yet"}${f.closest ? ` <span class="by">${(+f.match).toFixed(2)}</span>` : ""}</div>
          <div class="f" style="font-size:11px;color:var(--faint)">${f.px} px tall · 0.7+ is a match</div></div></div>
        <label>Who is it?</label><input class="search" id="fpName" list="fcKnown" value="${esc(f.closest && f.match >= 0.6 ? f.closest : "")}" placeholder="Name">
        <button class="btn primary" id="fpKeep">Keep this face</button>
        <div class="f" style="font-size:11px;color:var(--faint)">Adds it to the known faces (every show) and names ${esc(FP.stream)}'s look-alike groups.</div>`
        : `<div class="f" style="color:var(--dim)">Draw a box on the frame.</div>`}
      <div class="fknown" style="margin-top:10px"><span class="f">Known faces</span>${knownChips(FP.known)}</div>
      <div><button class="btn" data-faces="${esc(FP.stream)}">This stream's groups</button></div>
    </div></div>${knownList(FP.known)}`;
  wirePick();
}
function wirePick() {
  const wrap = $("#fpWrap");
  wrap.addEventListener("pointerdown", e => {
    if (FP.busy) return;
    e.preventDefault();
    const R = wrap.getBoundingClientRect(), x0 = (e.clientX - R.left) / R.width, y0 = (e.clientY - R.top) / R.height;
    let el = wrap.querySelector(".box");
    if (!el) { el = document.createElement("div"); el.className = "box"; wrap.appendChild(el); }
    const clamp = v => Math.min(1, Math.max(0, v));
    const move = ev => {
      const x1 = clamp((ev.clientX - R.left) / R.width), y1 = clamp((ev.clientY - R.top) / R.height);
      FP.box = { x: Math.min(x0, x1), y: Math.min(y0, y1), width: Math.abs(x1 - x0), height: Math.abs(y1 - y0) };
      Object.assign(el.style, { left: FP.box.x * 100 + "%", top: FP.box.y * 100 + "%", width: FP.box.width * 100 + "%", height: FP.box.height * 100 + "%" });
    };
    const up = () => {
      removeEventListener("pointermove", move); removeEventListener("pointerup", up);
      if (!FP.box || FP.box.width < 0.01 || FP.box.height < 0.01) { FP.box = null; renderPick(); return; }
      lookUp();
    };
    addEventListener("pointermove", move); addEventListener("pointerup", up);
  });
  const keep = $("#fpKeep");
  if (keep) keep.onclick = async () => {
    const name = $("#fpName").value.trim();
    if (!name) return toast("Type who it is first.", true);
    keep.disabled = true;
    const { win } = FP;
    try {
      const r = await api("/api/faces/enroll", { stream: FP.stream, name, embedding: FP.found.embedding });
      toast(`Kept as ${name} (${r.samples} sample${r.samples > 1 ? "s" : ""})`);
      if (!faceWin(win, "facepick")) return;
      Object.assign(FP, { known: r.known, box: null, found: null });
      renderPick();
    } catch (e) { toast(e.message, true); keep.disabled = false; }
  };
}
async function lookUp() {
  FP.busy = true; FP.found = null; renderPick();
  const { win } = FP;
  try {
    const r = await api("/api/faces/pick", { ref: FP.ref, t: FP.t, box: FP.box });
    if (!faceWin(win, "facepick")) return;
    FP.found = r;
  } catch (e) { if (!faceWin(win, "facepick")) return; toast(e.message, true); }
  FP.busy = false; renderPick();
}

document.addEventListener("click", e => {
  const t = e.target.closest?.("[data-faces],[data-facepick],[data-fcfilter],[data-fcset],[data-fcclear],[data-fcforget]");
  if (!t) return;
  const ds = t.dataset;
  if (ds.faces) return showFaces(ds.faces);
  if (ds.facepick) return pickFace(ds.facepick);
  if (ds.fcforget) return facesForget(ds.fcforget, FC?.stream || FP?.stream);
  if (!FC) return;
  if (ds.fcfilter) { FC.filter = ds.fcfilter; renderFaces(); return; }
  if (ds.fcclear) return facesName(ds.fcclear, "", false);
  const key = ds.fcset, g = FC.data.groups.find(x => x.key === key);
  const name = ds.same ? g.name : $(`[data-fcname="${key}"]`).value.trim();
  if (!name) return toast("Type who it is first.", true);
  facesName(key, name, !!ds.enroll);
});
document.addEventListener("keydown", e => {
  if (e.key === "Enter" && e.target.matches?.("[data-fcname]")) { e.preventDefault(); $(`[data-fcset="${e.target.dataset.fcname}"]`).click(); }
  if (e.key === "Enter" && e.target.id === "fpName") { e.preventDefault(); $("#fpKeep")?.click(); }
});
$("#tool").addEventListener("close", () => { $("#tool").classList.remove("facesw"); FC = null; FP = null; FACEWIN++; });
