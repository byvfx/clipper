// War room: investigations (#108). Each archive subject's leads dossier (`clipper investigate`),
// the same markdown as leads.md, rendered to read. Discord's lead posts link here:
// #inv=jer&run=<made_at digits>&lead=L03 (lead ids repeat across runs; the run says which).

let INV = null;  // {subject, run, lead}
let INVWIN = 0;

// The dossier's markdown: # and ## headings, paragraphs, "- " items with indented "> " quotes
// and link lines under them, **bold**, `code` and bare links. Nothing else is needed.
function mdInline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/https?:\/\/[^\s<>"']+[^\s<>"'.,;:)]/g, u => `<a href="${u}" target="_blank" rel="noopener noreferrer">${u}</a>`);
}
function mdRender(md) {
  const out = [];
  let para = [], list = null;
  const flushPara = () => { if (para.length) out.push(`<p>${para.map(mdInline).join("<br>")}</p>`); para = []; };
  const flushList = () => { if (list) out.push(`<ul>${list.map(li => `<li>${li.join("")}</li>`).join("")}</ul>`); list = null; };
  for (const line of md.split(/\r?\n/)) {
    const h = line.match(/^(#{1,2}) (.*)$/);
    if (h) {
      flushPara(); flushList();
      const id = h[2].match(/^(L\d+)\b/);
      out.push(`<h${h[1].length + 1}${id ? ` id="inv-${id[1]}"` : ""}>${mdInline(h[2])}</h${h[1].length + 1}>`);
    } else if (/^- /.test(line)) {
      flushPara();
      (list ||= []).push([mdInline(line.slice(2))]);
    } else if (list && /^ {2}\S/.test(line)) {
      const t = line.trim();
      list[list.length - 1].push(t.startsWith("> ") ? `<blockquote>${mdInline(t.slice(2))}</blockquote>` : `<div>${mdInline(t)}</div>`);
    } else if (!line.trim()) {
      flushPara(); flushList();
    } else {
      flushList(); para.push(line);
    }
  }
  flushPara(); flushList();
  return out.join("");
}

async function showInvestigations(subject, lead, run) {
  const d = $("#tool");
  const win = ++INVWIN;
  d.dataset.kind = "inv"; d.classList.remove("cover"); d.classList.add("facesw");
  if (!d.open) {
    d.innerHTML = `<div class="m-head"><b>Investigations</b><button class="x" data-close>×</button></div><div class="tbody">Loading…</div>`;
    d.showModal();
  }
  try {
    const q = new URLSearchParams();
    if (subject) q.set("subject", subject);
    if (run) q.set("run", run);
    const r = await api("/api/investigations?" + q);
    if (win !== INVWIN || !d.open || d.dataset.kind !== "inv") return;
    INV = { subject: r.subject, run: r.run, lead };
    const opts = r.subjects.map(s => `<option value="${esc(s.subject)}"${s.subject === r.subject ? " selected" : ""}>${esc(s.subject)}${s.about ? " · " + esc(s.about) : ""} · ${esc(s.made_at.slice(0, 10))} · ${s.kept} leads</option>`).join("");
    d.innerHTML = `<div class="m-head"><b>Investigations</b>${r.subjects.length ? `<select id="invSubject" title="Research only, never uploaded: leads.md">${opts}</select>` : ""}<button class="x" data-close>×</button></div>
      <div class="md">${r.missing_run ? `<p class="old">That run of ${esc(r.subject)}'s leads isn't on file any more. <button class="btn" data-invnow>Open the current one</button></p>`
        : r.markdown ? (r.run !== r.current ? `<p class="old">An earlier run of these leads (the link's). <button class="btn" data-invnow>Open the current one</button></p>` : "") + mdRender(r.markdown)
        : `<div class="empty">No investigations yet: clipper investigate run &lt;subject&gt;.</div>`}</div>`;
    if (lead) d.querySelector("#inv-" + CSS.escape(lead))?.scrollIntoView({ block: "start" });
  } catch (e) {
    const box = d.querySelector(".tbody");
    if (box) box.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`;
    else toast(e.message, true);
  }
}

document.addEventListener("click", e => {
  if (e.target.closest?.("#invBtn")) showInvestigations(INV?.subject);
  if (e.target.closest?.("[data-invnow]")) { invHash(null); showInvestigations(INV?.subject); }
});
// The link's place in the hash (core.js's saveHash keeps inv/run/lead), dropped once the window closes.
function invHash(p) {
  const h = new URLSearchParams(location.hash.slice(1));
  for (const k of ["inv", "run", "lead"]) h.delete(k);
  if (p) for (const [k, v] of Object.entries(p)) if (v) h.set(k, v);
  const s = h.toString();
  history.replaceState(null, "", location.pathname + location.search + (s ? "#" + s : ""));
}
document.addEventListener("change", e => {
  if (e.target.id === "invSubject") { invHash(null); showInvestigations(e.target.value); }
});
$("#tool").addEventListener("close", () => { if ($("#tool").dataset.kind === "inv") { INV = null; INVWIN++; invHash(null); } });
function invFromHash() {  // a link from Discord: open the window at that lead
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get("inv")) showInvestigations(p.get("inv"), p.get("lead") || "", p.get("run") || "");
}
window.addEventListener("hashchange", invFromHash);
invFromHash();
