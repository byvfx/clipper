// War room: the cast library (#41). Each regular's photos for covers and the weekly: keep or
// drop the new ones, fix a label, find more from Wikimedia Commons, their channels, image
// search, a web address or a stream's named faces.

let CS = null;  // {person, data, win}
let CASTWIN = 0;
let CASTSHOWN = null;  // whose page is on screen: its typed drafts survive a refresh
const CASTDRAFTS = ["castNew", "castAka", "castCh", "castUrl", "castStream"];
const castWin = win => win === CASTWIN && $("#tool").open && $("#tool").dataset.kind === "cast";

async function showCast(person) {
  const d = $("#tool");
  const win = ++CASTWIN;
  person = person ?? CS?.person ?? "";
  d.dataset.kind = "cast"; d.classList.remove("cover"); d.classList.add("facesw");
  if (!CS || !d.open) d.innerHTML = `<div class="m-head"><b>Cast</b><button class="x" data-close>×</button></div><div class="tbody">Loading…</div>`;
  if (!d.open) d.showModal();
  try {
    const data = await api("/api/cast" + (person ? "?person=" + encodeURIComponent(person) : ""));
    if (!castWin(win)) return;
    CS = { person, data, win };
    renderCast();
  } catch (e) {
    if (!castWin(win)) return;
    const box = d.querySelector(".tbody");  // only before the first render
    if (box) box.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`;
    else { toast(e.message, true); if (CS?.data.job.running) castPoll(); }
  }
}
function castPoll() {  // while a find runs: its log and the photos it adds
  const win = CS?.win;
  setTimeout(() => { if (CS && castWin(win)) showCast(CS.person); }, 2500);
}
function renderCast() {
  const { data } = CS, p = data.person, job = data.job, min = data.min_photos;
  // A refresh while a job runs keeps what was being typed, and where.
  const same = CASTSHOWN === (p?.name ?? ""), focus = document.activeElement;
  const drafts = same ? Object.fromEntries(CASTDRAFTS.map(id => [id, $("#" + id)?.value]).filter(([, v]) => v !== undefined)) : {};
  const at = same && CASTDRAFTS.includes(focus?.id) ? [focus.id, focus.selectionStart, focus.selectionEnd] : null;
  CASTSHOWN = p?.name ?? "";
  const who = data.people.map(x => `<button class="cpp${x.name === p?.name ? " on" : ""}" data-castp="${esc(x.name)}">
      <b>${esc(x.name)}</b><span class="by">${x.kept} kept${x.new ? ` · ${x.new} new` : ""}</span>${x.kept < min ? `<span class="cwarn" title="fewer than ${min} kept">!</span>` : ""}</button>`).join("");
  const running = job.running ? `<div class="cjob">${esc(job.what)} for ${esc(job.person)}… <span class="by">${esc(job.log.at(-1) || "")}</span></div>`
    : job.error ? `<div class="cjob" style="color:var(--red)">${esc(job.error)}</div>`
    : job.log.length && job.person === p?.name ? `<div class="cjob">${esc(job.log.at(-1))}</div>` : "";
  const tile = ph => `<div class="ct${ph.status === "new" ? " new" : ""}" data-ct="${ph.id}">
      <img loading="lazy" src="${esc(ph.url)}" alt="${esc(p.name)} ${esc(ph.label)}">
      <div class="cl"><select data-clabel="${ph.id}">${["", ...data.labels].map(l => `<option${l === ph.label ? " selected" : ""} value="${l}">${l || "label?"}</option>`).join("")}</select>
        <span class="by">${esc(ph.source)}${ph.match != null ? ` ${(+ph.match).toFixed(2)}` : ""} · ${ph.px}px</span></div>
      ${ph.flag ? `<div class="cf" title="${esc(ph.flag)}">⚠ ${esc(ph.flag)}</div>` : ""}
      ${ph.page || ph.licence ? `<div class="by">${ph.page?.startsWith("http") ? `<a href="${esc(ph.page)}" target="_blank" rel="noopener">source</a>` : esc(ph.page || "")}${ph.licence ? ` · ${esc(ph.licence)}` : ""}${ph.author ? ` · ${esc(ph.author)}` : ""}</div>` : ""}
      <div class="ca">${ph.status === "new" ? `<button class="btn primary" data-ckeep="${ph.id}">Keep</button>` : `<span class="by">kept</span>`}<button class="btn" data-cdrop="${ph.id}">Drop</button></div></div>`;
  const fresh = p ? p.photos.filter(x => x.status === "new") : [], kept = p ? p.photos.filter(x => x.status === "kept") : [];
  $("#tool").innerHTML = `<div class="m-head"><b>Cast</b><span style="color:var(--faint);font-size:12px">photos for covers and the weekly · ${min}+ each</span><button class="x" data-close>×</button></div>
    <div class="cast"><div class="cpeople">${who}
        <div class="cnew"><input class="search" id="castNew" placeholder="Someone new"><button class="btn" id="castAdd">Add</button></div></div>
      <div class="cperson">${!p ? `<div class="empty">Pick someone on the left.</div>` : `
        <div class="fh"><b style="font-size:16px">${esc(p.name)}</b><span class="by">${p.enrolled ? "face enrolled: only photos like them are taken" : "face not enrolled: the biggest face is taken (enroll in a stream's Faces window)"}</span></div>
        <div class="cform"><label>Other names</label><input class="search" id="castAka" value="${esc(p.aliases.join(", "))}" placeholder="other names, comma between">
          <label>Channels</label><input class="search" id="castCh" value="${esc(p.channels.join(" "))}" placeholder="YouTube or Kick channel addresses, space between">
          <span></span><div><button class="btn" id="castSave">Save</button></div></div>
        <div class="cfind"><span class="f">Find</span>
          <button class="btn" data-cfind="commons" title="Wikimedia Commons: freely licensed">Commons</button>
          <button class="btn" data-cfind="channels" title="Their channels: avatar and recent video thumbnails"${p.channels.length ? "" : " disabled"}>Channels</button>
          <button class="btn" data-cfind="search" title="${data.search ? "Brave image search (photo agencies skipped)" : "Needs $BRAVE_API_KEY"}"${data.search ? "" : " disabled"}>Image search</button>
          <input class="search" id="castUrl" placeholder="or a photo's web address"><button class="btn" id="castUrlBtn">Add</button>
          <input class="search" id="castStream" placeholder="or a stream id" style="width:150px"><button class="btn" id="castGrab" title="That stream's sharpest faces of ${esc(p.name)} (named in its Faces window)">Grab</button></div>
        ${running}
        ${fresh.length ? `<div class="f">New: keep the good ones (⚠ = the labeller's doubt)</div><div class="cgrid">${fresh.map(tile).join("")}</div>` : ""}
        <div class="f">Kept (${kept.length + (p.single ? 1 : 0)})</div>
        <div class="cgrid">${kept.map(tile).join("")}${p.single ? `<div class="ct"><img src="${esc(p.single_url)}" alt=""><div class="by">${esc(p.single)} (a single photo file)</div></div>` : ""}${!kept.length && !p.single ? `<div class="empty">None kept yet.</div>` : ""}</div>`}
      </div></div>`;
  for (const [id, v] of Object.entries(drafts)) { const el = $("#" + id); if (el) el.value = v; }
  if (at) { const el = $("#" + at[0]); if (el) { el.focus(); el.setSelectionRange(at[1], at[2]); } }
  if (job.running) castPoll();
}
async function castPost(path, body, msg) {
  const { win } = CS;
  try {
    const r = await api(path, { person: CS.person, ...body });
    if (msg) toast(msg);
    if (castWin(win)) { CS.data = r; renderCast(); }
  } catch (e) { toast(e.message, true); }
}

document.addEventListener("click", e => {
  const t = e.target.closest?.("#castBtn,[data-castp],[data-ckeep],[data-cdrop],[data-cfind],#castSave,#castAdd,#castUrlBtn,#castGrab");
  if (!t) return;
  const ds = t.dataset;
  if (t.id === "castBtn") return showCast();
  if (!CS) return;
  if (ds.castp) return showCast(ds.castp);
  if (t.id === "castAdd") {
    const name = $("#castNew").value.trim();
    if (!name) return toast("Type a name first.", true);
    CS.person = name;
    return castPost("/api/cast/set", { person: name }, `Added ${name}`);
  }
  if (ds.ckeep) return castPost("/api/cast/mark", { ids: [ds.ckeep], status: "keep" });
  if (ds.cdrop) return castPost("/api/cast/mark", { ids: [ds.cdrop], status: "drop" });
  if (t.id === "castSave") {
    const aliases = $("#castAka").value.split(",").map(s => s.trim()).filter(Boolean);
    const channels = $("#castCh").value.split(/\s+/).filter(Boolean);
    return castPost("/api/cast/set", { aliases, channels }, "Saved");
  }
  if (ds.cfind) return castPost("/api/cast/run", { what: "find", sources: [ds.cfind] });
  if (t.id === "castUrlBtn") {
    const url = $("#castUrl").value.trim();
    $("#castUrl").value = "";  // not kept as a draft once it's on its way
    return castPost("/api/cast/run", { what: "add", url });
  }
  if (t.id === "castGrab") return castPost("/api/cast/run", { what: "grab", stream: $("#castStream").value.trim() });
});
document.addEventListener("change", e => {
  const id = e.target.dataset?.clabel;
  if (id && CS && e.target.value) castPost("/api/cast/label", { id, label: e.target.value });
});
document.addEventListener("keydown", e => {
  if (e.key === "Enter" && e.target.id === "castNew") { e.preventDefault(); $("#castAdd").click(); }
  if (e.key === "Enter" && e.target.id === "castUrl") { e.preventDefault(); $("#castUrlBtn").click(); }
});
$("#tool").addEventListener("close", () => { CS = null; CASTSHOWN = null; CASTWIN++; });
