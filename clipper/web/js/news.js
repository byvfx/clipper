// War room: news (#110). Stories about the shows' people from `clipper news`: dismiss what isn't
// news, add a story to a show's next stream as context, or search Google News by hand.

let NS = null;  // {data, days, hits, q, ctx: {ids, show, text}}
let NEWSWIN = 0;
const newsWin = win => win === NEWSWIN && $("#tool").open && $("#tool").dataset.kind === "news";

async function showNews(days) {
  const d = $("#tool");
  const win = ++NEWSWIN;
  d.dataset.kind = "news"; d.classList.remove("cover"); d.classList.add("facesw");
  if (!NS || !d.open) d.innerHTML = `<div class="m-head"><b>News</b><button class="x" data-close>×</button></div><div class="tbody">Loading…</div>`;
  if (!d.open) d.showModal();
  days = days ?? NS?.days ?? 3;
  try {
    const data = await api("/api/news?days=" + days);
    if (!newsWin(win)) return;
    NS = { ...(NS || {}), data, days, win };
    renderNews();
  } catch (e) {
    const box = d.querySelector(".tbody");
    if (box) box.innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`;
    else toast(e.message, true);
  }
}
function renderNews() {
  const { data, hits, q, ctx } = NS, shows = data.shows;
  const showOpts = sel => Object.entries(shows).map(([k, n]) => `<option value="${esc(k)}"${k === sel ? " selected" : ""}>${esc(n)}</option>`).join("");
  const story = s => `<div class="nst${s.dismissed ? " off" : ""}">
      <div class="nh"><b>#${s.id} ${esc(s.headline)}</b><span class="by">${esc(s.rating)} · ${esc(s.person || "?")}${s.show ? " · " + esc(shows[s.show] || s.show) : ""} · ${ago(s.last_at)}</span>
        ${s.dismissed ? `<span class="by">dismissed</span>` : ""}${s.context_at ? `<span class="by">in context</span>` : ""}${s.posted_at ? `<span class="by">posted</span>` : ""}</div>
      <div>${esc(s.why)}</div>
      <div class="nsrc">${s.sources.slice(0, 6).map(x => `<a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.source || "web")}: ${esc(x.title)}</a>`).join("")}</div>
      ${s.dismissed ? "" : `<div class="nbar"><button class="btn" data-nctx="${s.id}">Tonight's context…</button><button class="btn" data-ndis="${s.id}">Dismiss</button></div>`}
      ${ctx && ctx.ids[0] === s.id ? `<div class="nctx">${esc(ctx.text)}</div><div class="nbar"><span class="by">for</span><select id="newsShow">${showOpts(ctx.show)}</select><button class="btn primary" id="newsAdd">Add to its next stream</button><button class="btn" id="newsCancel">Cancel</button></div>` : ""}
    </div>`;
  const bad = data.feeds.filter(f => f.error);
  const live = data.stories.filter(s => !s.dismissed), off = data.stories.filter(s => s.dismissed);
  $("#tool").innerHTML = `<div class="m-head"><b>News</b><span style="color:var(--faint);font-size:12px">${data.check_minutes ? `checked every ${data.check_minutes} min` : "the bot's check is off ([news] check_minutes)"} · ${data.feeds.length} sources${bad.length ? ` · <span style="color:var(--red)" title="${esc(bad.map(f => f.name + ": " + f.error).join("\n"))}">${bad.length} failing</span>` : ""}</span><button class="x" data-close>×</button></div>
    <div class="news">
      <div class="nbar"><input class="search" id="newsQ" placeholder="Search Google News (e.g. a name)" value="${esc(q || "")}"><button class="btn" id="newsGo">Search</button>
        <span class="by">stories from the last</span><select id="newsDays">${[1, 3, 7, 30].map(n => `<option value="${n}"${n == NS.days ? " selected" : ""}>${n} day${n > 1 ? "s" : ""}</option>`).join("")}</select></div>
      ${hits ? `<div class="nst"><div class="nh"><b>${hits.length} result(s) for “${esc(q)}”</b><button class="btn" id="newsClear">Clear</button></div><div class="nsrc">${hits.map(x => `<a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc((x.published || "").slice(0, 10))} ${esc(x.source || "")}: ${esc(x.title)}</a>`).join("") || `<span class="by">nothing</span>`}</div></div>` : ""}
      ${live.map(story).join("") || `<div class="empty">No stories in the last ${NS.days} day(s).</div>`}
      ${off.length ? `<div class="f">Dismissed</div>${off.map(story).join("")}` : ""}
    </div>`;
}
async function newsPost(path, body, msg) {
  const { win } = NS;
  try {
    const r = await api(path, body);
    if (msg) toast(msg);
    if (newsWin(win) && r.stories) { NS.data = r; NS.ctx = null; renderNews(); }
    return r;
  } catch (e) { toast(e.message, true); }
}

document.addEventListener("click", async e => {
  const t = e.target.closest?.("#newsBtn,[data-nctx],[data-ndis],#newsAdd,#newsCancel,#newsGo,#newsClear");
  if (!t) return;
  if (t.id === "newsBtn") return showNews();
  if (!NS) return;
  const ds = t.dataset;
  if (ds.ndis) return newsPost("/api/news/dismiss", { ids: [+ds.ndis], days: NS.days }, "Dismissed");
  if (ds.nctx) {
    const win = NS.win;
    const r = await newsPost("/api/news/context", { ids: [+ds.nctx], preview: true });
    if (r && NS && newsWin(win)) { NS.ctx = { ids: r.ids, show: r.show, text: r.text }; renderNews(); }
    return;
  }
  if (t.id === "newsCancel") { NS.ctx = null; return renderNews(); }
  if (t.id === "newsAdd") {
    const show = $("#newsShow").value;
    return newsPost("/api/news/context", { ids: NS.ctx.ids, show, days: NS.days }, `Added to ${NS.data.shows[show] || show}'s next stream`);
  }
  if (t.id === "newsClear") { NS.hits = null; NS.q = ""; return renderNews(); }
  if (t.id === "newsGo") {
    const q = $("#newsQ").value.trim();
    if (!q) return toast("Type something to search for.", true);
    t.disabled = true;
    const win = NS.win;  // the window may close, or be replaced, before Google answers
    try {
      const r = await api("/api/news/search?q=" + encodeURIComponent(q));
      if (NS && newsWin(win)) { NS.hits = r.hits; NS.q = q; renderNews(); }
    } catch (err) { if (NS && newsWin(win)) { toast(err.message, true); t.disabled = false; } }
  }
});
document.addEventListener("change", e => {
  if (e.target.id === "newsDays" && NS) showNews(+e.target.value);
});
document.addEventListener("keydown", e => {
  if (e.key === "Enter" && e.target.id === "newsQ") { e.preventDefault(); $("#newsGo").click(); }
});
$("#tool").addEventListener("close", () => { if ($("#tool").dataset.kind === "news") { NS = null; NEWSWIN++; } });
