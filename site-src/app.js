// Family website front end. Plain JavaScript, no build step; data from data/public.json
// plus, from the sign-in app, /api/me and /api/private (living relatives this reader may see).
// Settings (the site's name, whose name the pages use, regions and countries) come from
// site-src/site.json through public.json; see site-src/README.md. Claude, 9 November 2026;
// reworked for readers who are not researchers, 15 November 2026; made project-neutral, 17 November 2026.
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const fold = s => String(s || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
const Y0 = 1450, Y1 = 2030;
// Dark or white text, whichever reads better on a fill colour.
function inkOn(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex).trim()); if (!m) return "#fff";
  const c = [0, 2, 4].map(i => parseInt(m[1].slice(i, i + 2), 16) / 255).map(v => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
  const L = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  return L > 0.2 ? "#1b1916" : "#fff";   // above 0.2 the dark ink has the higher contrast
}
// Country legend: [colour variable, label], in palette-slot order (adjacent slots stay distinct), set in boot().
let COUNTRIES = [], COUNTRY_MATCH = [], REGION_WORDS = [];
// Whose name the pages use for the root person ("Alex's ancestors"), and who receives contributions.
let ROOTNAME = "", OWNER = "";

let D, ME = {}, P, F, PL, IMG, H, LINES;
const FILEKEY = {};   // saved file name -> image key, for links in the research notes

// ---------- data ----------
async function boot() {
  try {
    const r0 = await fetch("data/public.json");
    if (!r0.ok) throw new Error(r0.status);
    D = await r0.json();
  } catch (e) {
    $("#view").innerHTML = `<div class="home"><h1>The family history could not be loaded</h1><p class="lead">You may have been signed out. <a href="signin">Sign in again</a>, or try once more in a minute.</p></div>`;
    return;
  }
  try { const r = await fetch("api/me"); if (r.ok) ME = await r.json(); } catch (e) { /* no app: read-only preview */ }
  try {
    const r = await fetch("api/private");
    if (r.ok) {
      const pv = await r.json();
      for (const [k, v] of Object.entries(pv.people || {})) D.people[k] = Object.assign({}, D.people[k], v);
      for (const [k, v] of Object.entries(pv.fams || {})) Object.assign(D.fams[k], v);
      Object.assign(D.images, pv.images || {});
    }
  } catch (e) { /* ignore */ }
  P = D.people; F = D.fams; PL = D.places; IMG = D.images; H = D.history; LINES = D.lines;
  const site = D.site || {};
  ROOTNAME = site.root_name || "the family"; OWNER = site.owner_name || "the family historian";
  const cs = (site.countries || []).filter(c => c.color >= 1 && c.color <= 6);
  COUNTRY_MATCH = cs.map(c => [new RegExp(c.match), `--c-${c.color}`]);
  COUNTRIES = [...cs].sort((a, b) => a.color - b.color).map(c => [`--c-${c.color}`, c.label]).concat([["--c-un", "Not known"]]);
  REGION_WORDS = (site.regions || []).map(([re, code]) => [new RegExp(re), code]);
  for (const [k, v] of Object.entries(IMG)) FILEKEY[v.file] = k;
  ME.anchor = ME.anchor || D.root;
  renderMe();
  setupHeader();
  $("#view").addEventListener("click", viewClick);
  // The page a relative asked for before signing in (saved by the sign-in page; see family-app/app.py).
  try {
    const n = JSON.parse(localStorage.getItem("family_next") || "null");
    localStorage.removeItem("family_next");
    if (n && Date.now() - n.t < 3 * 3600e3 && /^#\/[\w\/%.~-]*$/.test(n.h) && (!location.hash || location.hash === "#/")) history.replaceState(null, "", n.h);
  } catch (e) { /* storage not available */ }
  addEventListener("hashchange", route);
  addEventListener("popstate", () => { if (!(history.state && history.state.lightbox)) hideLightbox(); });
  addEventListener("resize", () => { clearTimeout(J.rz); J.rz = setTimeout(() => { if (current === "journey") drawTimeline(); if (current === "lives") drawLives(); lbFit(); }, 150); });
  document.addEventListener("pointerdown", e => { if (!e.target.closest("svg")) tip(); });
  route();
}

const isLiving = k => P[k] && P[k].lv;
const known = k => P[k] && P[k].n;
const nameOf = k => known(k) ? P[k].n : "Living relative";
const yearsOf = k => {
  const p = P[k]; if (!p || !p.n) return "";
  const b = p.by ? p.by : (p.est ? "about " + p.est : "?");
  return p.lv ? `born ${b}` : `${b} – ${p.dy || "?"}`;
};
const parentsOf = k => (P[k]?.famc || []).flatMap(f => [F[f]?.h, F[f]?.w]).filter(Boolean);
const childrenOf = k => (P[k]?.fams || []).flatMap(f => F[f]?.c || []);
const spousesOf = k => (P[k]?.fams || []).map(f => F[f].h === k ? F[f].w : F[f].h).filter(Boolean);
const siblingsOf = k => [...new Set((P[k]?.famc || []).flatMap(f => F[f]?.c || []))].filter(x => x !== k);
const birthYear = k => P[k]?.by || P[k]?.est || null;
const endYear = k => P[k]?.dy || (birthYear(k) ? Math.min(birthYear(k) + 70, new Date().getFullYear()) : null);

// ---------- relationships ----------
function upMap(k) {
  const d = {[k]: 0}, q = [k];
  while (q.length) { const x = q.shift(); for (const p of parentsOf(x)) if (!(p in d)) { d[p] = d[x] + 1; q.push(p); } }
  return d;
}
const UPC = {};
const upMapC = k => UPC[k] || (UPC[k] = upMap(k));   // filled only after the private data is merged
const ord = n => n + (["th", "st", "nd", "rd"][(n % 100 - 20) % 10] || ["th", "st", "nd", "rd"][n % 100] || "th");
function word(sx, m, f, n) { return sx === "M" ? m : sx === "F" ? f : n; }
function greats(n, base) { return n <= 0 ? base : n === 1 ? "great-" + base : ord(n) + " great-" + base; }
function relation(from, to, noSpouse) {
  if (!from || !to || from === to) return from === to ? "you" : "";
  const a = upMapC(from), b = upMapC(to), sx = P[to]?.sx;
  let best = null;
  for (const x in a) if (x in b && (!best || a[x] + b[x] < a[best] + b[best])) best = x;
  if (best) {
    const da = a[best], dt = b[best];
    if (dt === 0) return da === 1 ? word(sx, "father", "mother", "parent") : da === 2 ? word(sx, "grandfather", "grandmother", "grandparent") : greats(da - 2, word(sx, "grandfather", "grandmother", "grandparent"));
    if (da === 0) return dt === 1 ? word(sx, "son", "daughter", "child") : dt === 2 ? word(sx, "grandson", "granddaughter", "grandchild") : greats(dt - 2, word(sx, "grandson", "granddaughter", "grandchild"));
    if (da === 1 && dt === 1) return word(sx, "brother", "sister", "sibling");
    if (da === 1) return greats(dt - 2, word(sx, "nephew", "niece", "nephew or niece"));
    if (dt === 1) return greats(da - 2, word(sx, "uncle", "aunt", "uncle or aunt"));
    const deg = Math.min(da, dt) - 1, rem = Math.abs(da - dt);
    return `${ord(deg)} cousin${rem ? (rem === 1 ? " once removed" : rem === 2 ? " twice removed" : ` ${rem} times removed`) : ""}`;
  }
  if (noSpouse) return "";
  for (const s of spousesOf(to)) {
    const r = relation(from, s, true);
    if (r && r !== "you" && !r.includes(" of ")) return `${word(sx, "husband", "wife", "spouse")} of your ${r}`;
    if (r === "you") return word(sx, "husband", "wife", "spouse");
  }
  return "";
}
const relLabel = k => {
  const r = relation(ME.anchor, k);
  if (!r) return "";
  if (r === "you") return "You";
  const who = ME.anchor === D.root && !ME.self ? ROOTNAME + "'s " : "Your ";
  return r.includes(" of your ") ? r.replace(" of your ", ME.anchor === D.root && !ME.self ? ` of ${ROOTNAME}'s ` : " of your ") : who + r;
};

// ---------- header, search ----------
function renderMe() {
  const el = $("#me");
  if (!ME.email) { el.textContent = ""; return; }
  el.innerHTML = `<span class="who">${esc(ME.name || ME.email)}</span>${ME.admin ? ' <a href="admin">Admin</a>' : ""} <a href="auth/signout">Sign out</a>`;
}
let searchIndex = [];
// A name search on any input: results show each person's years and how they are related to the reader.
function attachSearch(q, box, onPick) {
  let hi = 0, hits = [];
  const close = () => { box.style.display = "none"; };
  const mine = upMapC(ME.anchor);
  const show = () => {
    const t = fold(q.value).trim().split(/\s+/).filter(Boolean);
    hits = t.length ? searchIndex.filter(x => t.every(w => x.s.includes(w))) : [];
    hits.sort((a, b) => (a.k in mine ? mine[a.k] : 99) - (b.k in mine ? mine[b.k] : 99) || P[a.k].n.localeCompare(P[b.k].n));
    const more = hits.length - 12; hits = hits.slice(0, 12);
    hi = 0;
    box.innerHTML = hits.map((x, i) => `<a href="#/p/${x.k}" data-k="${x.k}" role="option" class="${i === hi ? "hi" : ""}">${esc(P[x.k].n)} <small>${esc(yearsOf(x.k))}</small><span class="r">${esc(relLabel(x.k))}</span></a>`).join("")
      + (more > 0 ? `<a role="option"><span class="r">and ${more} more: type more of the name</span></a>` : "") || (t.length ? '<a role="option">No one found by that name</a>' : "");
    box.style.display = t.length ? "block" : "none";
  };
  const pick = k => { q.value = ""; close(); onPick(k); };
  q.addEventListener("input", show);
  q.addEventListener("keydown", e => {
    if (e.key === "Enter" && hits[hi]) { e.preventDefault(); pick(hits[hi].k); }
    if (e.key === "Escape") { q.value = ""; close(); }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { hi = Math.max(0, Math.min(hits.length - 1, hi + (e.key === "ArrowDown" ? 1 : -1))); [...box.children].forEach((a, i) => a.classList.toggle("hi", i === hi)); e.preventDefault(); }
  });
  box.addEventListener("click", e => { const a = e.target.closest("a[data-k]"); e.preventDefault(); if (a) pick(a.dataset.k); });
  document.addEventListener("click", e => { if (e.target !== q && !box.contains(e.target)) close(); });
}
function setupHeader() {
  searchIndex = Object.keys(P).filter(known).map(k => ({k, s: fold([P[k].n, ...(P[k].alt || [])].join(" "))}));
  attachSearch($("#q"), $("#qres"), k => { closeMenu(); location.hash = "#/p/" + k; });
  const top = $("#top"), mb = $("#menubtn");
  mb.addEventListener("click", () => { const o = !top.classList.contains("open"); top.classList.toggle("open", o); mb.setAttribute("aria-expanded", String(o)); mb.textContent = o ? "Close" : "Menu"; });
  $("#menu").addEventListener("click", e => { if (e.target.closest(".tabs a")) closeMenu(); });
}
function closeMenu() { $("#top").classList.remove("open"); const mb = $("#menubtn"); mb.setAttribute("aria-expanded", "false"); mb.textContent = "Menu"; }

// ---------- routing ----------
let current = null;
const TAB_OF = {"": "home", stories: "stories", story: "stories", tree: "tree", kin: "tree", timeline: "tree", related: "tree", map: "map", line: "map", service: "service", records: "records", numbers: "numbers"};
function route() {
  hideLightbox(); closeMenu(); tip();
  const h = location.hash.replace(/^#\/?/, "").split("/");
  const tab = TAB_OF[h[0]] || "";
  document.querySelectorAll(".tabs a").forEach(a => { const on = a.dataset.tab === tab; a.classList.toggle("on", on); if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); });
  const journey = h[0] === "map" || h[0] === "line";
  if (current === "story" && h[0] !== "story") endStory();
  if (current === "service") endService();
  if (current === "journey" && !journey) endJourney();
  if (!journey) window.scrollTo(0, 0);
  if (h[0] === "service") return showService(h[1] && P[h[1]] ? h[1] : null);
  if (h[0] === "stories") return showStories();
  if (h[0] === "numbers") return showNumbers();
  if (h[0] === "story") { const st = (D.stories || []).find(x => x.slug === h[1]); return st ? showStory(st) : showStories(); }
  if (h[0] === "tree") return showTree(h[1] && P[h[1]] ? h[1] : ME.anchor);
  if (h[0] === "kin") return showKin(h[1] && P[h[1]] ? h[1] : null);
  if (h[0] === "timeline") return showLives(h[1] && P[h[1]] ? h[1] : null);
  if (h[0] === "related") return showRelated(P[h[1]] ? h[1] : null, P[h[2]] ? h[2] : null);
  if (h[0] === "records") return showRecords();
  if (h[0] === "p" && P[h[1]]) return showPerson(h[1]);
  if (h[0] === "map") return showJourney({person: P[h[1]] ? h[1] : null});
  if (h[0] === "line") return showJourney({line: decodeURIComponent(h[1] || ""), person: h[2]});
  showHome();
}
// One click handler for every page: buttons marked with data- attributes.
function viewClick(ev) {
  const t = ev.target;
  const g = t.closest("[data-go]"); if (g && g.dataset.go) return go(g.dataset.go);
  const im = t.closest("[data-img]"); if (im) return openImage(im.dataset.img);
  if (t.closest("[data-back]")) return history.length > 1 ? history.back() : (location.hash = "#/");
  if (t.closest("[data-print]")) return window.print();
  if (t.closest("[data-add]")) { const f = $("#add"); if (f) { f.scrollIntoView({behavior: "smooth", block: "start"}); $("textarea", f).focus({preventScroll: true}); } return; }
  if (t.closest("[data-surprise]")) return surprise();
  if (t.closest("[data-crumbs]")) return t.closest(".crumbs").classList.add("all");
  if (t.closest("[data-closesel]")) return showSelCard(null);
  const li = t.closest(".panel li.placed");
  if (li && J.map && coords(li.dataset.pk)) J.map.flyTo(coords(li.dataset.pk), 10, {duration: 1});
}

// ---------- journey view ----------
const J = {map: null, line: null, chain: [], sel: null, year: 1600, playing: null, pinned: false, region: null, layers: null};

function lineFor(k) {
  return LINES.find(l => l.chain.includes(k));
}
function showJourney({line, person}) {
  let L = line ? LINES.find(l => l.sn === line) : person ? lineFor(person) : null;
  if (!L) { const mine = upMapC(ME.anchor); L = (J.line && person) ? J.line : J.line || LINES.find(l => l.chain.some(k => k in mine)) || LINES[0]; }
  if (current !== "journey") buildJourneyShell();
  if (J.line !== L) setLine(L);
  select(person && P[person] ? person : (J.sel && J.chain.includes(J.sel) && !line ? J.sel : L.chain[0]), !!person);
}
function buildJourneyShell() {
  current = "journey";
  $("#view").innerHTML = `
  <div class="journey">
    <section class="mapwrap" aria-label="Map">
      <div class="mapctl">
        <label class="linepick">Family line <select id="linesel">${LINES.map(l => `<option value="${esc(l.sn)}">${esc(l.sn)} (${l.gens} generations)</option>`).join("")}</select></label>
        <label class="linepick">Show <select id="allsel"><option value="">This family line only</option><option value="sel">Every ancestor of the person selected</option><option value="me">${ME.anchor === D.root && !ME.self ? `All of ${esc(ROOTNAME)}'s ancestors` : "All your ancestors"}</option></select></label>
        <div class="allnote" id="allnote" hidden></div></div>
      <div class="mapbox"><div class="map" id="map"></div><div class="yearbig" id="yearbig"></div></div></section>
    <section class="timeline" aria-label="Timeline">
      <div class="controls">
        <button class="btn" id="play" type="button">Play the journey</button>
        <button class="btn ghost" id="restart" type="button">Start again</button>
        <span class="hint">Drag the timeline, or tap a bar to meet that person.</span>
        <span class="legend" id="tllegend"></span>
      </div>
      <svg class="tl" id="tl" role="slider" tabindex="0" aria-label="Year" aria-valuemin="${Y0}" aria-valuemax="${Y1}"></svg>
    </section>
    <aside class="panel" id="panel" aria-live="polite"></aside>
  </div>`;
  J.map = L.map("map", {zoomControl: true});
  const dark = matchMedia("(prefers-color-scheme: dark)").matches && document.documentElement.dataset.theme !== "light";
  L.tileLayer(`https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_${dark ? "Dark" : "Light"}_Gray_Base/MapServer/tile/{z}/{y}/{x}`,
    {maxZoom: 16, attribution: "Tiles &copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors"}).addTo(J.map);
  J.all = L.layerGroup().addTo(J.map);
  J.layers = L.layerGroup().addTo(J.map);
  J.allMode = "";
  J.hl = L.layerGroup().addTo(J.map);
  J.here = L.marker([0, 0], {icon: L.divIcon({className: "", html: '<div class="pulse"></div>', iconSize: [18, 18], iconAnchor: [9, 9]}), interactive: false, keyboard: false});
  J.line = null;
  $("#linesel").addEventListener("change", e => { location.hash = "#/line/" + encodeURIComponent(e.target.value); });
  $("#allsel").addEventListener("change", e => { J.allMode = e.target.value; drawAll(true); });
  $("#play").addEventListener("click", togglePlay);
  $("#restart").addEventListener("click", () => { stop(); J.pinned = false; setYear(J.start); J.map.flyToBounds(J.bounds, {padding: [30, 30], duration: 1}); });
  const svg = $("#tl");
  J.ro = new ResizeObserver(() => { clearTimeout(J.rz); J.rz = setTimeout(() => { if (current === "journey" && Math.abs(svg.getBoundingClientRect().height - J.HT) > 2) drawTimeline(); }, 120); });
  J.ro.observe(svg);
  const scrub = ev => { const r = svg.getBoundingClientRect(); J.pinned = false; setYear(tlYear((ev.clientX - r.left) * J.W / r.width)); };
  svg.addEventListener("pointerdown", ev => { if (ev.target.closest(".life")) return; stop(); svg.setPointerCapture(ev.pointerId); scrub(ev); svg.onpointermove = scrub; });
  svg.addEventListener("pointerup", () => { svg.onpointermove = null; });
  svg.addEventListener("keydown", ev => { if (ev.key === "ArrowRight" || ev.key === "ArrowLeft") { J.pinned = false; setYear(J.year + (ev.key === "ArrowRight" ? 5 : -5)); ev.preventDefault(); } });
}
function endJourney() { stop(); if (J.ro) J.ro.disconnect(); if (J.map) J.map.remove(); J.map = null; J.line = null; J.chain = []; J.sel = null; }
const coords = pk => PL[pk] ? [PL[pk].lat, PL[pk].lon] : null;
const isEurope = c => c[1] > -30;
const regionsOf = pk => REGION_WORDS.filter(([re]) => re.test(pk || "")).map(([, r]) => r);
function arc(a, b) {
  const pts = [], n = 32, lift = Math.abs(b[1] - a[1]) * 0.18;
  for (let i = 0; i <= n; i++) { const t = i / n; pts.push([a[0] + (b[0] - a[0]) * t + Math.sin(Math.PI * t) * lift, a[1] + (b[1] - a[1]) * t]); }
  return pts;
}
function setLine(Lc) {
  J.line = Lc; J.chain = Lc.chain.filter(k => known(k));
  $("#linesel").value = Lc.sn;
  // Each person's part of the route runs from about age 22 until the next generation reaches 22.
  const take = i => i === 0 ? -Infinity : (birthYear(J.chain[i]) || 0) + 22;
  const path = [], all = [];
  J.chain.forEach((k, i) => (P[k].ev || []).forEach(e => {
    const c = coords(e.pk); if (!c || !e.y) return;
    all.push({y: e.y, pk: e.pk, k});
    if (e.y >= take(i) && (i === J.chain.length - 1 || e.y < take(i + 1))) path.push({y: e.y, pk: e.pk, k});
  }));
  path.sort((a, b) => a.y - b.y);
  J.route = path.filter((s, i) => i === 0 || s.pk !== path[i - 1].pk);
  J.layers.clearLayers(); J.hl.clearLayers(); J.segs = []; J.markers = {};
  for (let i = 1; i < J.route.length; i++) {
    const a = coords(J.route[i - 1].pk), b = coords(J.route[i].pk);
    const pts = isEurope(a) !== isEurope(b) ? arc(a, b) : [a, b];
    L.polyline(pts, {color: css("--muted"), weight: 1.5, opacity: .45, dashArray: "4 6"}).addTo(J.layers);
    J.segs.push({y: J.route[i].y, line: L.polyline(pts, {color: css("--accent"), weight: 3, opacity: 0}).addTo(J.layers)});
  }
  const keys = [...new Set(all.map(s => s.pk))];
  keys.forEach(pk => {
    const c = coords(pk);
    J.markers[pk] = L.circleMarker(c, {radius: 5, color: css(isEurope(c) ? "--map-europe" : "--map-america"), weight: 2, fillColor: css("--surface"), fillOpacity: 1})
      .bindTooltip(esc(PL[pk].name) + (PL[pk].approx ? " (approximate)" : ""), {direction: "top"}).addTo(J.layers)
      .on("click", () => { const s = all.filter(s => s.pk === pk); if (s.length) go(s[s.length - 1].k); });
  });
  J.bounds = keys.length ? L.latLngBounds(keys.map(coords)) : L.latLngBounds([[25, -100], [60, 20]]);
  J.map.fitBounds(J.bounds, {padding: [30, 30], maxZoom: 9});
  J.start = Math.floor((birthYear(J.chain[0]) || 1600) - 5);
  J.lanes = [];
  J.chain.forEach(k => { const b = birthYear(k) || 1600; let l = J.lanes.findIndex(end => end < b - 2); if (l < 0) { J.lanes.push(0); l = J.lanes.length - 1; } J.lanes[l] = endYear(k) || b + 60; P[k]._lane = l; });
  const regs = new Set(["world"]); J.chain.forEach(k => (P[k].regions || []).forEach(r => regs.add(r)));
  J.hist = H.filter(h => h.regions.some(r => regs.has(r)) && !h.near || (h.people || []).some(x => J.chain.includes(x)) || h.near && J.chain.some(k => (P[k].ev || []).some(e => e.pk && new RegExp(h.near).test(e.pk) && e.y >= h.y - 30 && e.y <= (h.y2 || h.y) + 30)));
  J.y0 = Math.max(Y0, J.start - 10); J.y1 = Math.min(Y1, new Date().getFullYear() + 5);
  J.year = J.start; J.region = null;
  drawTimeline();
}
const tlX = y => 14 + (y - J.y0) / (J.y1 - J.y0) * (J.W - 28);
const tlYear = x => Math.round(J.y0 + (x - 14) / (J.W - 28) * (J.y1 - J.y0));
const colorFor = k => { const r = P[k]?.regions || [], b = (P[k]?.ev || []).find(e => /Born|Christened|Baptized/.test(e.t)); const br = b ? regionKey(b.pk) : null; return css(br || "--c-un"); };
function regionKey(place) {
  if (!place) return null;
  const hit = COUNTRY_MATCH.find(([re]) => re.test(place));
  return hit ? hit[1] : null;
}
function drawTimeline() {
  const svg = $("#tl"), NS = "http://www.w3.org/2000/svg";
  const r = svg.getBoundingClientRect(); J.W = r.width || 800; const HT = J.HT = r.height || 160;
  svg.setAttribute("viewBox", `0 0 ${J.W} ${HT}`); svg.innerHTML = "";
  const el = (t, a, p = svg) => { const e = document.createElementNS(NS, t); for (const k in a) e.setAttribute(k, a[k]); p.appendChild(e); return e; };
  const laneTop = 34, laneH = Math.min(32, (HT - laneTop - 24) / Math.max(1, J.lanes.length));
  const step = (J.y1 - J.y0) > 300 ? 50 : 25;
  for (let y = Math.ceil(J.y0 / step) * step; y <= J.y1; y += step) {
    el("line", {x1: tlX(y), x2: tlX(y), y1: laneTop - 4, y2: HT - 22, stroke: css("--line")});
    el("text", {x: tlX(y), y: HT - 5, "text-anchor": "middle", "font-size": 14, fill: css("--muted")}).textContent = y;
  }
  J.hist.forEach((h, i) => {
    const x = tlX(h.y), w = h.y2 ? Math.max(3, tlX(h.y2) - x) : 3;
    const g = el("g", {});
    el("rect", {x, y: 6 + (i % 2) * 11, width: w, height: 8, rx: 2, fill: css("--history"), opacity: .55}, g);
    g.addEventListener("pointerenter", ev => tip(ev, `<b>${esc(h.title)}</b> ${h.y}${h.y2 ? "–" + h.y2 : ""}<br>${esc(h.text)}`));
    g.addEventListener("pointerleave", () => tip());
  });
  J.chain.forEach(k => {
    const b = birthYear(k) || 1600, x = tlX(b), x2 = tlX(endYear(k) || b + 60), y = laneTop + P[k]._lane * laneH;
    const g = el("g", {class: "life" + (J.sel === k ? " sel" : ""), "data-id": k});
    el("rect", {x, y, width: Math.max(4, x2 - x), height: laneH - 6, rx: 3, fill: colorFor(k), "stroke-dasharray": P[k].dy ? "" : "3 2"}, g);
    el("text", {x: x + 6, y: y + (laneH - 6) / 2 + 1, "font-size": 14, fill: inkOn(colorFor(k)), "dominant-baseline": "middle", "pointer-events": "none"}, g).textContent = (x2 - x) > P[k].n.split(" ")[0].length * 8 + 12 ? P[k].n.split(" ")[0] : "";
    g.addEventListener("pointerdown", ev => { ev.stopPropagation(); go(k); });
    g.addEventListener("pointerenter", ev => tip(ev, `<b>${esc(P[k].n)}</b><br>${esc(yearsOf(k))}`));
    g.addEventListener("pointerleave", () => tip());
  });
  const head = el("g", {id: "head", "pointer-events": "none"});
  el("line", {x1: 0, x2: 0, y1: 2, y2: HT - 20, stroke: css("--ink"), "stroke-width": 2}, head);
  el("circle", {cx: 0, cy: 4, r: 5, fill: css("--ink")}, head);
  const used = new Set(J.chain.map(k => colorFor(k)));
  $("#tllegend").innerHTML = COUNTRIES.map(([v, t]) => [v, v === "--c-un" ? "Birthplace unknown" : "Born in " + t])
    .filter(([v]) => used.has(css(v))).map(([v, t]) => `<span><i style="background:var(${v})"></i>${t}</span>`).join("") + '<span><i style="background:var(--history)"></i>History</span>';
  setYear(J.year, true);
}
function focusFor(y) { let f = J.chain[0]; J.chain.forEach(k => { if ((birthYear(k) || 9999) + 22 <= y) f = k; }); return f; }
function setYear(y, quiet) {
  J.year = Math.max(J.y0, Math.min(J.y1, y));
  $("#yearbig").textContent = Math.floor(J.year);
  $("#tl").setAttribute("aria-valuenow", Math.floor(J.year));
  const h = $("#head"); if (h) h.setAttribute("transform", `translate(${tlX(J.year)},0)`);
  J.segs.forEach(s => s.line.setStyle({opacity: s.y <= J.year + 0.01 ? .9 : 0}));
  const done = J.route.filter(s => s.y <= J.year + 0.01), cur = done[done.length - 1];
  if (cur) {
    J.here.setLatLng(coords(cur.pk)).addTo(J.map);
    const reg = isEurope(coords(cur.pk)) ? "eu" : "am";
    if (J.playing && reg !== J.region) {
      J.region = reg;
      const pts = J.route.map(s => coords(s.pk)).filter(c => isEurope(c) === (reg === "eu"));
      J.map.flyToBounds(L.latLngBounds(pts), {padding: [40, 40], duration: 1.6, maxZoom: 8});
    }
  } else J.map.removeLayer(J.here);
  if (!J.pinned && !quiet) { const f = focusFor(J.year); if (f !== J.sel) select(f, false); }
}
function togglePlay() {
  if (J.playing) return stop();
  if (J.year >= (endYear(J.chain[J.chain.length - 1]) || J.y1) - 1) setYear(J.start);
  J.pinned = false; J.region = null; $("#play").textContent = "Pause";
  let last = performance.now();
  const span = (J.y1 - J.start), speed = Math.max(6, span / 35);
  const tick = now => { const dt = (now - last) / 1000; last = now; setYear(J.year + dt * speed); if (J.year >= J.y1 - 1) return stop(); J.playing = requestAnimationFrame(tick); };
  J.playing = requestAnimationFrame(tick);
}
function stop() { if (J.playing) cancelAnimationFrame(J.playing); J.playing = null; const b = $("#play"); if (b) b.textContent = "Play the journey"; }
const go = k => { location.hash = "#/map/" + k; };
// Pins for every ancestor of one person, one pin per place, at their birthplace (or earliest place on the map).
function homeOf(k) {
  const ev = (P[k].ev || []).filter(e => coords(e.pk));
  const b = ev.find(e => /Born|Christened|Baptized/.test(e.t)) || [...ev].sort((a, c) => (a.y || 9999) - (c.y || 9999))[0];
  return b ? b.pk : null;
}
function drawAll(fly) {
  J.all.clearLayers();
  const note = $("#allnote");
  if (!J.allMode) { note.hidden = true; return; }
  const root = J.allMode === "me" ? ME.anchor : J.sel;
  const anc = Object.keys(upMap(root)).filter(k => k !== root && known(k));
  const by = {}; let unmapped = 0;
  anc.forEach(k => { const pk = homeOf(k); if (pk) (by[pk] ||= []).push(k); else unmapped++; });
  Object.entries(by).forEach(([pk, ks]) => {
    ks.sort((a, b) => (birthYear(a) || 0) - (birthYear(b) || 0));
    const names = ks.slice(0, 6).map(k => `${esc(P[k].n)} <small>${esc(yearsOf(k))}</small>`).join("<br>") + (ks.length > 6 ? `<br><small>and ${ks.length - 6} more</small>` : "");
    L.circleMarker(coords(pk), {radius: 4 + 2.2 * Math.sqrt(ks.length), color: css("--surface"), weight: 1.5, fillColor: colorFor(ks[0]), fillOpacity: .85})
      .bindTooltip(`<b>${esc(PL[pk].name)}</b>${PL[pk].approx ? " (approximate)" : ""}<br>${names}`, {direction: "top"})
      .bindPopup(`<b>${esc(PL[pk].name)}</b><br>${ks.map(k => `<a href="#/map/${k}">${esc(P[k].n)}</a> <small>${esc(yearsOf(k))}</small>`).join("<br>")}`, {maxHeight: 260})
      .addTo(J.all);
  });
  const n = Object.keys(by).length, you = root === ME.anchor && (ME.self || ME.anchor !== D.root);
  note.hidden = false;
  note.innerHTML = `${you ? "Your" : esc(P[root].n) + "'s"} ${anc.length} known ancestors, at ${n} place${n === 1 ? "" : "s"}, each pinned where they were born or first recorded${unmapped ? `; ${unmapped} have no place on the map yet` : ""}. Larger pins hold more people; colours show the country of birth.`;
  if (fly && n) J.map.flyToBounds(L.latLngBounds(Object.keys(by).map(coords)), {padding: [30, 30], maxZoom: 9, duration: 1});
}
function select(k, fromUser) {
  if (fromUser) { J.pinned = true; stop(); }
  J.sel = k;
  if (J.allMode === "sel") drawAll(false);
  document.querySelectorAll(".life").forEach(g => g.classList.toggle("sel", g.dataset.id === k));
  Object.values(J.markers).forEach(m => m.setRadius(5));
  J.hl.clearLayers();
  const pks = [...new Set((P[k].ev || []).map(e => e.pk).filter(pk => coords(pk)))];
  pks.forEach(pk => {
    if (J.markers[pk]) J.markers[pk].setRadius(8);
    else L.circleMarker(coords(pk), {radius: 7, color: css("--accent"), weight: 2, fillColor: css("--surface"), fillOpacity: 1}).bindTooltip(esc(PL[pk].name), {direction: "top"}).addTo(J.hl);
  });
  if (fromUser) {
    if (J.chain.includes(k) && birthYear(k)) setYear(Math.min(endYear(k) || birthYear(k) + 30, birthYear(k) + 30), true);
    if (pks.length) J.map.flyToBounds(L.latLngBounds(pks.map(coords)), {padding: [60, 60], maxZoom: 10, duration: 1.2});
  }
  renderPanel(k);
}

// ---------- a person's life and times, shared by the map panel and the person page ----------
function worldOf(k) {
  const p = P[k], by = birthYear(k), end = endYear(k);
  const life = (p.ev || []).filter(e => e.y).map(e => ({...e, kind: "e"}));
  // Where the person was at a given year: the regions of their last placed event before it (or their first).
  const stops = (p.ev || []).filter(e => e.y && e.pk).map(e => ({y: e.y, r: regionsOf(e.pk), pk: e.pk})).filter(s => s.r.length).sort((a, b) => a.y - b.y);
  const whereAt = y => { let s0 = stops[0] || {r: [], pk: ""}; for (const s of stops) if (s.y <= y + 0.5) s0 = s; return s0; };
  const relevant = h => {
    if (h.regions.includes("world") || (h.people || []).includes(k)) return true;
    const from = Math.max(h.y, by), to = Math.min(h.y2 || h.y, end);
    const years = [from, ...stops.map(s => s.y).filter(y => y > from && y <= to)];
    // "near" narrows a local event to people recorded at a matching place at the time.
    const near = h.near ? new RegExp(h.near) : null;
    return years.some(y => { const w = whereAt(y); return near ? near.test(w.pk) : w.r.some(r => h.regions.includes(r)); });
  };
  const hist = by ? H.filter(h => (h.y2 || h.y) >= by && h.y <= end && relevant(h)).map(h => ({y: h.y + .001, kind: "h", h})) : [];
  const items = [...life, ...hist].sort((a, b) => a.y - b.y);
  const undated = (p.ev || []).filter(e => !e.y && (e.tx || e.pl)).map(e => ({...e, kind: "e"}));
  const age = y => (by && y - by >= 1 && (!end || y <= end)) ? ` · age ${Math.floor(y - by)}` : "";
  const li = it => it.kind === "h"
    ? `<li class="h"><div class="when">${it.h.y}${it.h.y2 ? "–" + it.h.y2 : ""}${age(it.h.y)} · in the wider world</div><div class="what">${esc(it.h.title)}</div>${it.h.text ? `<div>${esc(it.h.text)}</div>` : ""}</li>`
    : `<li class="${coords(it.pk) ? "placed" : ""}" data-pk="${esc(it.pk || "")}"><div class="when">${esc(it.d)}${it.age != null ? " · age " + it.age : ""}</div><div class="what">${esc(it.t)}${it.tx ? ": " + esc(it.tx) : ""}</div>${it.pl ? `<div class="where">${esc(it.pl)}</div>` : ""}</li>`;
  return {items, undated, li};
}
const firstName = k => P[k].n.split(" ")[0];
const linkBadgeOf = p => p.link === "probable" ? '<span class="badge" title="The link to the parents rests on a probable identification">probable parents</span>'
  : /hypothesis/.test(p.link || "") ? '<span class="badge" title="The parents shown are a working theory">parents uncertain</span>' : "";

// ---------- person panel beside the map ----------
function renderPanel(k) {
  const p = P[k], panel = $("#panel");
  if (!known(k)) { panel.innerHTML = `<h2>Living relative</h2><p class="intro">This person is living. Their details are shown only to close family.</p>`; return; }
  const i = J.chain.indexOf(k), w = worldOf(k), rel = relLabel(k), first = esc(firstName(k));
  panel.innerHTML = `
    ${i >= 0 ? `<div class="pnav"><button type="button" data-go="${J.chain[i - 1] || ""}" ${i > 0 ? "" : "disabled"}>&lsaquo; Earlier generation</button><button type="button" data-go="${J.chain[i + 1] || ""}" ${i < J.chain.length - 1 ? "" : "disabled"}>Next generation &rsaquo;</button></div>` : ""}
    ${rel ? `<div class="rel">${esc(rel)}</div>` : ""}
    <h2>${esc(p.n)}</h2>
    <div class="dates">${esc(yearsOf(k))}${linkBadgeOf(p)}</div>
    ${p.intro ? `<p class="intro">${esc(p.intro)}</p>` : ""}
    <p><a class="btn" href="#/p/${k}">Open ${first}'s page &rsaquo;</a></p>
    ${w.items.length ? `<h3>${first}'s life and times</h3><p class="hint">Tap an event to see its place on the map.</p><ol class="world">${w.items.map(w.li).join("")}</ol>` : ""}
    <p class="foot">${D.site?.history_footer ? esc(D.site.history_footer) + " " : ""}Places marked approximate are shown at the nearest town or commune.</p>`;
  panel.scrollTop = 0;
}

// ---------- person page: the story first, then family, life, records and the research notes ----------
const recCard = k => {
  const r = IMG[k], who = [...new Set(r.people.filter(known).map(nameOf))];
  const names = who.slice(0, 3).join(", ") + (who.length > 3 ? ` and ${who.length - 3} more` : "");
  return `<button class="rec" type="button" data-img="${k}"><img loading="lazy" src="${esc(r.th)}" alt=""><div><b>${esc(r.title || r.cite.split(",").slice(0, 2).join(","))}</b><span>${esc(names)}</span></div></button>`;
};
// A citation as HTML: saved files the site may show become links to the image, and web addresses become links.
function citeHtml(t) {
  return esc(t).replace(/sources\/[^ ;),]+/g, m => { const key = FILEKEY[m.replace(/\.$/, "").split("/").pop()]; return key ? `<button type="button" class="linkbtn" data-img="${key}">${m}</button>` : m; })
    .replace(/https?:\/\/[^\s)<]+/g, u => { const v = u.replace(/[.,;]+$/, ""); return `<a href="${v}" target="_blank" rel="noopener">${v}</a>${u.slice(v.length)}`; });
}
function researchNotes(k) {
  const p = P[k];
  const notes = (p.ev || []).filter(e => e.ev || (e.src || []).length || (e.nt || []).length).map(e => {
    const lab = evLabel(e.ev);
    return `<div class="note"><b>${esc(e.t)}${e.d ? ", " + esc(e.d) : ""}</b> ${lab ? evBadge(lab) : ""}${e.ev ? `<div class="nt">${esc(e.ev)}</div>` : ""}${(e.nt || []).map(n => `<div class="nt">${citeHtml(n)}</div>`).join("")}${(e.src || []).map(c => `<div class="src">${citeHtml(c)}</div>`).join("")}</div>`;
  }).join("") + (p.notes || []).filter(n => !/^Parents:/.test(n)).map(n => `<div class="note nt">${citeHtml(n)}</div>`).join("");
  const pnote = (p.notes || []).find(n => /^Parents:/.test(n));
  const pgrade = pnote ? `<div class="parentgrade">${evBadge(pnote.match(/^Parents: (established)/) ? ["Parents shown by the records", "green", 1] : /^Parents: probable/.test(pnote) ? ["Parents probably right", "amber", 1] : ["Parents a theory", "amber", 0])}<div class="nt">${citeHtml(pnote)}</div></div>` : "";
  return `<details class="notes"><summary>Research notes: how we know</summary>
      <p class="hint">Each fact with its sources, and how sure the research is of it.</p>
      <details class="evhelp"><summary>What the coloured dots mean</summary><div class="evkey">${EV_LABELS.map(l => evBadge(l)).join("")}</div></details>${pgrade}
      ${notes || '<p class="note">No research notes are shown for this person.</p>'}</details>`;
}
function showPerson(k) {
  current = "person"; stop();
  const p = P[k], view = $("#view"), back = `<button class="back" type="button" data-back>&lsaquo; Back</button>`;
  if (!known(k)) { view.innerHTML = `<article class="person">${back}<header class="phead"><h1>Living relative</h1><p class="intro">This person is living. Their details are shown only to close family.</p></header></article>`; return; }
  const w = worldOf(k), rel = relLabel(k), first = esc(firstName(k));
  const imgs = [...new Set(p.img || [])].filter(x => IMG[x]).sort((a, b) => (IMG[a].yr || 9999) - (IMG[b].yr || 9999));
  const card = (x, kind) => known(x)
    ? `<a class="card" href="#/p/${x}"><div class="k">${kind}</div><div class="nm">${esc(P[x].n)}</div><div class="yr">${esc(yearsOf(x))}</div></a>`
    : `<div class="card lv"><div class="k">${kind}</div><div class="nm">Living relative</div></div>`;
  const fam = [
    ...parentsOf(k).map(x => card(x, word(P[x]?.sx, "Father", "Mother", "Parent"))),
    ...spousesOf(k).map(x => card(x, word(P[x]?.sx, "Husband", "Wife", "Spouse"))),
    ...siblingsOf(k).map(x => card(x, word(P[x]?.sx, "Brother", "Sister", "Sibling"))),
    ...childrenOf(k).map(x => card(x, word(P[x]?.sx, "Son", "Daughter", "Child"))),
  ];
  const onMap = (p.ev || []).some(e => coords(e.pk));
  const relBtn = rel && k !== ME.anchor && known(ME.anchor) ? `<a class="btn ghost" href="#/related/${ME.anchor}/${k}">${ME.self || ME.anchor !== D.root ? "How you are related" : `How ${esc(ROOTNAME)} is related`}</a>` : "";
  view.innerHTML = `<article class="person">${back}
    <header class="phead">
      ${rel ? `<div class="rel">${esc(rel)}</div>` : ""}
      <h1>${esc(p.n)}</h1>
      <div class="dates">${esc(yearsOf(k))}${linkBadgeOf(p)}</div>
      ${p.intro ? `<p class="intro">${esc(p.intro)}</p>` : ""}
      <div class="actions">
        ${ME.email ? `<button class="btn" type="button" data-add>Add a memory or photo</button>` : ""}
        ${onMap ? `<a class="btn ghost" href="#/map/${k}">See ${first} on the map</a>` : ""}
        <a class="btn ghost" href="#/kin/${k}">${first}'s family tree</a>
        ${relBtn}
        ${(D.service || []).some(x => x.person === k) ? `<a class="btn ghost" href="#/service/${k}">Military service</a>` : ""}
        <button class="btn ghost" type="button" data-print>Print this page</button>
      </div>
    </header>
    ${(p.story || []).length ? `<section><h2>${first}'s story</h2><div class="bio">${p.story.map(x => `<h4>${esc(x.head)}</h4>${x.html}`).join("")}</div></section>` : ""}
    ${fam.length ? `<section><h2>Family</h2><div class="cards">${fam.join("")}</div></section>` : ""}
    ${w.items.length ? `<section><h2>Life and times</h2><p class="hint">What the records show of ${first}'s life, with what was happening around ${p.sx === "F" ? "her" : p.sx === "M" ? "him" : "them"}.</p><ol class="world">${w.items.map(w.li).join("")}</ol></section>` : ""}
    ${w.undated.length ? `<section><h2>Also recorded</h2><ol class="world">${w.undated.map(w.li).join("")}</ol></section>` : ""}
    ${imgs.length ? `<section><h2>Records you can see</h2><p class="hint">Tap a record to look at it closely.</p><div class="grid">${imgs.map(recCard).join("")}</div></section>` : ""}
    ${contribForm(k)}
    ${researchNotes(k)}
    ${D.site?.history_footer ? `<p class="foot">${esc(D.site.history_footer)}</p>` : ""}
  </article>`;
}
// ---------- evidence labels (site-src/README.md, "Evidence labels") ----------
const EV_LABELS = [["Seen in the original record", "green", 1], ["Stated in a later record", "green", 0], ["From an index", "blue", 0],
  ["From a published history", "grey", 0], ["Worked out from a record", "grey", 1], ["Probably right", "amber", 1],
  ["A lead, not yet checked", "amber", 0], ["Family memory", "purple", 1], ["Records disagree", "red", 0]];
function evLabel(ev) {
  const m = /^Evidence:\s*([^.]+)/.exec(ev || ""); if (!m) return null;
  const v = m[1].toLowerCase();
  const i = v.startsWith("direct, original") ? 0 : v.startsWith("direct, informant") || v.startsWith("direct") ? 1 : v.startsWith("index") ? 2
    : v.startsWith("secondary") ? 3 : v.startsWith("derived") ? 4 : v.startsWith("inferred, probable") ? 5
    : v.startsWith("inferred") || v.startsWith("lead") || v.startsWith("source not recorded") ? 6 : v.startsWith("family") ? 7 : v.startsWith("conflict") ? 8 : -1;
  return i < 0 ? null : EV_LABELS[i];
}
const evBadge = ([t, c, solid]) => `<span class="ev ${c}${solid ? " solid" : ""}"><i></i>${esc(t)}</span>`;

// ---------- contributions from relatives ----------
function contribForm(k) {
  if (!ME.email || !known(k)) return "";
  const first = esc(firstName(k));
  return `<form class="contrib" id="add" data-person="${k}"><h2>Do you know something about ${first}?</h2>
    <p class="msg">A memory, a correction, a photo or a document: it goes to ${esc(OWNER)}, who will add it to the family history with your name.</p>
    <label for="cmsg">What you remember or know</label>
    <textarea id="cmsg" name="message" rows="4" maxlength="5000" required></textarea>
    <label for="cfile">A photo or document, if you have one</label>
    <input id="cfile" type="file" name="photo" accept="image/*,application/pdf">
    <div><button class="btn" type="submit">Send to ${esc(OWNER)}</button> <span class="msg" data-out role="status"></span></div></form>`;
}
document.addEventListener("submit", async ev => {
  const f = ev.target.closest("form.contrib"); if (!f) return;
  ev.preventDefault();
  const out = $("[data-out]", f), btn = $("button[type=submit]", f), fd = new FormData(f);
  fd.append("person", f.dataset.person);
  const file = fd.get("photo");
  if (file && file.size > 15 * 1024 * 1024) { out.textContent = "That file is over 15 MB; please send a smaller one."; return; }
  btn.disabled = true; out.textContent = "Sending…";
  try {
    const r = await fetch("api/contribute", {method: "POST", body: fd});
    if (!r.ok) throw new Error((await r.text()) || r.status);
    f.reset(); out.textContent = `Thank you. ${OWNER} has it.`;
  } catch (e) { out.textContent = "That did not go through: " + e.message; }
  btn.disabled = false;
});

function tip(ev, html) {
  const t = $("#tip");
  if (!html) { t.style.display = "none"; return; }
  t.innerHTML = html; t.style.display = "block";
  t.style.left = Math.min(ev.clientX + 12, innerWidth - 290) + "px"; t.style.top = Math.max(8, ev.clientY - 70) + "px";
}

// ---------- family tree (fan chart) ----------
let treeLegend = "";
// The fan's centre, and the trail of centres from the reader (or the site's root) to it, shown as breadcrumbs.
// The centre is in the address (#/tree/<id>); the trail is rebuilt from the tree when the page is reloaded.
const FAN = {centre: null, trail: [], pick: null, paths: {}};
const youName = () => ME.anchor === D.root && !ME.self ? ROOTNAME : "You";
const crumbName = k => k === ME.anchor ? youName() : nameOf(k);
function fanTrail(k) {
  const t = FAN.trail, n = FAN.pick;
  FAN.pick = null;
  // Centred from a slice of the chart: the trail goes on through the people between the old centre and that slice,
  // so a person reached by two lines (pedigree collapse) keeps the line the reader followed.
  if (n && n.k === k && t[t.length - 1] === FAN.centre) { const up = []; for (let x = n; x.up; x = x.up) up.unshift(x.k); return (FAN.trail = [...t, ...up]); }
  const i = t.indexOf(k);
  if (i >= 0) return (FAN.trail = t.slice(0, i + 1));
  if (t.length && parentsOf(t[t.length - 1]).includes(k)) return (FAN.trail = [...t, k]);
  for (const base of [ME.anchor, D.root]) {
    if (k === base) return (FAN.trail = [k]);
    const {prev} = upPrev(base);
    if (k in prev) { const out = [k]; while (out[0] !== base) out.unshift(prev[out[0]]); return (FAN.trail = out); }
  }
  return (FAN.trail = [k]);
}
function fanCrumbs() {
  let t = FAN.trail;
  if (t[0] !== ME.anchor && t[0] !== D.root) t = [ME.anchor, ...t];
  if (t.length < 2) return "";
  const long = t.length > 6;
  const items = t.map((k, i) => `<li${long && i > 0 && i < t.length - 4 ? ' class="far"' : ""}>${i === t.length - 1
    ? `<span aria-current="page">${esc(crumbName(k))}</span>` : `<a href="#/tree/${k}">${esc(crumbName(k))}</a>`}</li>`);
  if (long) items.splice(1, 0, `<li class="gap"><button type="button" data-crumbs aria-label="Show all ${t.length} people on the way">&hellip;</button></li>`);
  return `<nav class="crumbs" aria-label="The line from ${esc(crumbName(t[0]))} to the centre of the chart"><ol>${items.join("")}</ol></nav>`;
}
function showTree(root) {
  current = "tree"; stop();
  fanTrail(root); FAN.centre = root;
  treeLegend = `<div class="fanlegend"><b>Colour: where each person was born</b>${countryKey()}<span><i style="border:1.5px dashed var(--ink);background:none"></i>Probable parents</span><span><i style="border:1.5px dotted var(--ink);background:none"></i>Parents a theory</span></div>`;
  $("#view").innerHTML = `<div class="treeview"><div class="treebar">${treeSwitch("fan", root)}<span class="hint">Tap a name for details. Double-click a name, or choose "Centre the fan here", to put that person in the middle.${FAN.trail.length > 1 || root !== ME.anchor ? " Tap the centre to step back one generation." : ""} Use + and − to zoom, and drag to move.</span>${fanCrumbs()}</div><div class="fanwrap" id="fan"><div class="selcard" id="selcard" hidden></div></div><aside class="panel" id="panel"></aside></div>`;
  J.chain = []; J.map = null; J.line = null;
  drawFan(root);
  renderPanelLite(root);
}
// A person's two parents in fan order: the father first (clockwise), then the mother; a parent of unknown sex takes the free side.
function fanParents(k) {
  const ps = [...new Set(parentsOf(k))], sx = x => P[x]?.sx;
  let fa = ps.find(x => sx(x) === "M") || null, mo = ps.find(x => sx(x) === "F") || null;
  for (const x of ps) if (x !== fa && x !== mo) { if (!fa) fa = x; else if (!mo) mo = x; }
  return [fa, mo];
}
// The longest form of a name that fits a slice: `len` along the text, `ht` across it, the font at most `cap`.
// Two lines (first name over surname) in the inner rings; elsewhere one line, shortened to an initial and surname,
// the surname or the initials; null when nothing would ever be readable.
function fanLabel(nm, first, last, len, ht, cap, stack) {
  const cw = 0.55, fs = Math.min(cap, ht * 0.8);
  const two = Math.min(cap, ht / 2.3, len / (cw * Math.max(first.length, last.length, 1)));
  const twoOk = first !== last && two >= Math.min(cap, 8);
  if (stack && twoOk) return {fs: two, lines: [first, last]};
  const fit = [nm, `${first[0]}. ${last}`].find(x => x.length * cw * fs <= len);
  if (fit) return {fs, lines: [fit]};
  if (twoOk) return {fs: two, lines: [first, last]};
  if (last.length * cw * fs <= len) return {fs, lines: [last]};
  const ini = (first[0] || "") + (first === last ? "" : last[0] || "");
  const f2 = Math.min(fs, len / (cw * Math.max(ini.length, 1)));
  return f2 >= 0.4 ? {fs: f2, lines: [ini]} : null;
}
function drawFan(root) {
  // Every generation the tree has. Each person's slice is shared between their parents by how much is known above
  // each (the number of ends of line, where a person with no known parents counts as one), so the angle goes where
  // the ancestry is known and an unknown parent takes none. In the first four rings each known parent keeps at least
  // a quarter of the slice, so close family stays readable. A person reached by two lines (pedigree collapse) is drawn
  // in each place. The first view frames three to five generations; the reader zooms out for the rest.
  const NS = "http://www.w3.org/2000/svg", wrap = $("#fan");
  const W = {}, busy = new Set();
  const weight = k => {
    if (k in W) return W[k];
    if (busy.has(k)) return 1;   // a loop in the data (someone their own ancestor): count once and stop
    busy.add(k);
    const ps = fanParents(k).filter(Boolean), w = ps.length ? ps.reduce((a, x) => a + weight(x), 0) : 1;
    busy.delete(k);
    return (W[k] = w);
  };
  const onPath = (n, k) => { for (let x = n; x; x = x.up) if (x.k === k) return true; return false; };
  const a0 = Math.PI * 0.75, a1 = Math.PI * 2.25; // 270 degrees, clockwise from lower left to lower right, open at the bottom
  const gens = [[{k: root, s: a0, e: a1, up: null}]];
  const MAX = 12000;   // slices; past this (heavy pedigree collapse) the outer generations are left off
  let total = 0, cut = false;
  for (;;) {
    const g = gens.length, next = [];
    for (const n of gens[g - 1]) {
      const ps = fanParents(n.k).filter(x => x && !onPath(n, x));
      if (!ps.length) continue;
      let f = ps.length === 1 ? 1 : weight(ps[0]) / (weight(ps[0]) + weight(ps[1]));
      if (ps.length === 2) { const m = g <= 4 ? 0.25 : 0.04; f = Math.max(m, Math.min(1 - m, f)); }
      const mid = n.s + (n.e - n.s) * f;
      next.push({k: ps[0], s: n.s, e: ps.length === 1 ? n.e : mid, up: n});
      if (ps.length === 2) next.push({k: ps[1], s: mid, e: n.e, up: n});
    }
    if (!next.length) break;
    if (total + next.length > MAX) { cut = true; break; }
    total += next.length;
    gens.push(next);
  }
  const G = gens.length;   // rings, the centre included
  const ringW = g => g <= 12 ? [0, 70, 75, 75, 70, 65, 60, 55, 52, 50, 48, 46, 44][g] : Math.max(34, 44 - (g - 12) * 1.5);
  const rad = [70]; for (let g = 1; g < G; g++) rad.push(rad[g - 1] + ringW(g));
  const r0 = rad[0], least = r0 + 70 + 75 + 75;
  const side = Math.min(wrap.clientWidth, wrap.clientHeight * 1.16), frame = side < 520 ? 3 : side < 1000 ? 4 : 5;
  const R = Math.max(least, rad[G - 1]), R6 = Math.min(R, Math.max(least, rad[Math.min(frame, G - 1)]));
  const cx = 0, cy = 0;
  const svg = document.createElementNS(NS, "svg");
  const full = {x: -R - 20, y: -R - 20, w: 2 * R + 40, h: R * 1.72 + 40}, first = {x: -R6 - 20, y: -R6 - 20, w: 2 * R6 + 40, h: R6 * 1.72 + 40};
  const el = (t, a, p = svg) => { const e = document.createElementNS(NS, t); for (const k in a) e.setAttribute(k, a[k]); p.appendChild(e); return e; };
  const pt = (r, a) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  const times = {};
  gens.forEach(row => row.forEach(n => { times[n.k] = (times[n.k] || 0) + 1; }));
  FAN.paths = {};
  for (let g = 1; g < G; g++) {
    const rIn = rad[g - 1], rOut = rad[g];
    for (const n of gens[g]) {
      const {k, s, e} = n, span = e - s;
      if (span * rOut < 0.02) continue;   // thinner than a hair even at the closest zoom
      const big = span > Math.PI ? 1 : 0;
      const [x1, y1] = pt(rIn, s), [x2, y2] = pt(rOut, s), [x3, y3] = pt(rOut, e), [x4, y4] = pt(rIn, e);
      const d = `M${x1},${y1} L${x2},${y2} A${rOut},${rOut} 0 ${big} 1 ${x3},${y3} L${x4},${y4} A${rIn},${rIn} 0 ${big} 0 ${x1},${y1}Z`;
      const fill = known(k) ? colorFor(k) : css("--c-un");
      const link = P[n.up.k]?.link || "", sw = Math.min(1.5, rOut * span * 0.12);
      const path = el("path", {d, fill, "data-k": k, stroke: css("--surface"), style: `stroke-width:${sw.toFixed(3)}`});
      if (/probable|hypothesis/.test(link)) { path.setAttribute("stroke", css("--ink")); path.setAttribute("stroke-opacity", ".7"); path.setAttribute("stroke-dasharray", /probable/.test(link) ? `${sw * 3.3} ${sw * 2}` : `${sw} ${sw * 2}`); }
      (FAN.paths[k] ||= []).push(path);
      path.addEventListener("click", () => { if (FZ.dragged) return; tip(); fanSelect(k, n); });
      path.addEventListener("dblclick", () => { if (FZ.dragged || !known(k)) return; FAN.pick = n; location.hash = "#/tree/" + k; });
      path.addEventListener("pointerenter", ev => tip(ev, `<b>${esc(nameOf(k))}</b><br>${esc(yearsOf(k))}<br>${esc(relLabel(k))}${/probable/.test(link) ? "<br><i>Probable parent of " + esc(nameOf(n.up.k)) + "</i>" : /hypothesis/.test(link) ? "<br><i>A theory, not yet shown by a record</i>" : ""}${times[k] > 1 ? `<br><i>In ${times[k]} places on this chart: descended from by more than one line</i>` : ""}`));
      path.addEventListener("pointerleave", () => tip());
      if (!known(k)) continue;
      // Labels: along the arc where the slice is wider than it is deep, else along the radius; the font fits the slice.
      const mid = (s + e) / 2, rm = (rIn + rOut) / 2, [tx, ty] = pt(rm, mid);
      const nm = nameOf(k), fn = nm.split(" ")[0], last = P[k]?.sn || nm.split(" ").slice(-1)[0];
      const arcW = rm * Math.min(span, Math.PI) * 0.9, depth = (rOut - rIn) * 0.9, along = arcW >= depth * 1.15;
      const cap0 = g <= 2 ? 22 : g === 3 ? 17 : g === 4 ? 13.5 : g === 5 ? 10.5 : 9.5, cap = along ? Math.max(cap0, Math.min(17, depth / 2.6)) : cap0;
      const lab = fanLabel(nm, fn, last, along ? arcW : depth, along ? depth : arcW, cap, g <= 4);
      if (!lab) continue;
      let deg = mid * 180 / Math.PI;
      if (along) { deg += 90; if (Math.sin(mid) > 0) deg += 180; } else if (Math.cos(mid) < 0) deg += 180;
      const t = el("text", {x: tx, y: ty, "text-anchor": "middle", "dominant-baseline": "middle", "font-size": lab.fs.toFixed(3), "data-fs": lab.fs, transform: `rotate(${deg.toFixed(2)} ${tx} ${ty})`, style: `fill:${inkOn(fill)}`});
      if (lab.lines.length === 2) {
        el("tspan", {x: tx, dy: "-0.5em"}, t).textContent = lab.lines[0];
        el("tspan", {x: tx, dy: "1.15em"}, t).textContent = lab.lines[1];
      } else t.textContent = lab.lines[0];
    }
  }
  const c = el("circle", {cx, cy, r: r0 - 4, fill: known(root) ? colorFor(root) : css("--c-un"), cursor: "pointer"});
  // Step back toward the reader: the previous centre on the trail, or the child of the centre person on the reader's line.
  const tr = FAN.trail;
  const toward = tr.length > 1 && tr[tr.length - 1] === root ? tr[tr.length - 2]
    : root === ME.anchor ? null : childrenOf(root).find(x => x === ME.anchor || root in upMap(ME.anchor) && x in upMap(ME.anchor)) || null;
  if (toward) { c.style.cursor = "pointer"; c.addEventListener("click", () => { location.hash = "#/tree/" + toward; }); }
  else c.style.cursor = "default";
  c.addEventListener("pointerenter", ev => tip(ev, toward ? `Back to ${esc(crumbName(toward))}` : `<b>${esc(crumbName(root))}</b>`));
  c.addEventListener("pointerleave", () => tip());
  const t = el("text", {x: cx, y: cy, "text-anchor": "middle", "font-size": 20, "data-fs": 20, "pointer-events": "none", style: `fill:${inkOn(known(root) ? colorFor(root) : css("--c-un"))}`});
  const cn = known(root) ? [firstName(root), P[root].sn || ""] : root === ME.anchor ? [youName(), ""] : ["Living", "relative"];
  el("tspan", {x: cx, dy: cn[1] ? "-0.4em" : "0.35em"}, t).textContent = cn[0];
  el("tspan", {x: cx, dy: "1.2em"}, t).textContent = cn[1];
  wrap.appendChild(svg);
  fanZoom(svg, wrap, full, first, G - 1, cut ? `The first ${G - 1} generations` : null);
}
// A tapped slice: outlined everywhere the person appears, with their details in the panel (and, on a phone, in a card on the chart).
function fanSelect(k, n) {
  if (!known(k)) return;
  FAN.pick = n;
  document.querySelectorAll("#fan .selo").forEach(o => o.remove());
  (FAN.paths[k] || []).forEach(p => {
    const o = p.cloneNode(false);
    o.removeAttribute("stroke-dasharray"); o.removeAttribute("stroke-opacity");
    o.setAttribute("class", "selo"); o.setAttribute("fill", "none"); o.setAttribute("stroke", css("--ink")); o.removeAttribute("style");
    p.ownerSVGElement.appendChild(o);
  });
  renderPanelLite(k);
  showSelCard(k);
}
function showSelCard(k) {
  const c = $("#selcard"); if (!c) return;
  if (!k || !known(k)) { c.hidden = true; return; }
  const rel = relLabel(k), first = esc(firstName(k));
  c.hidden = false;
  c.innerHTML = `<button type="button" class="x" data-closesel aria-label="Close">&times;</button>${rel ? `<div class="rel">${esc(rel)}</div>` : ""}<b>${esc(P[k].n)}</b> <span class="dates">${esc(yearsOf(k))}</span>
    <div class="row"><a class="btn" href="#/p/${k}">Open ${first}'s page</a>${current === "tree" && k !== FAN.centre ? `<a class="btn ghost" href="#/tree/${k}">Centre the fan here</a>` : ""}</div>`;
}
// Zoom and pan for the fan chart: wheel, pinch, drag, and buttons. Labels too small to read are hidden.
const FZ = {dragged: false};
function fanZoom(svg, wrap, full, start, gens, label) {
  let vb = {...start};
  const texts = [...svg.querySelectorAll("text[data-fs]")];
  const ppu = () => Math.min(svg.clientWidth / vb.w, svg.clientHeight / vb.h) || 1;
  const apply = () => {
    svg.setAttribute("viewBox", `${vb.x} ${vb.y} ${vb.w} ${vb.h}`);
    const s = ppu();
    texts.forEach(t => { t.style.display = t.dataset.fs * s >= 8.5 ? "" : "none"; });
  };
  const zoomAt = (f, ux, uy) => {
    const w = Math.max(full.w / 60, Math.min(full.w * 1.05, vb.w / f)), k = w / vb.w;
    vb = {x: ux - (ux - vb.x) * k, y: uy - (uy - vb.y) * k, w, h: vb.h * k};
    apply();
  };
  const toUser = (cx, cy) => { const r = svg.getBoundingClientRect(), s = ppu(); return [vb.x + (cx - r.left - (r.width - vb.w * s) / 2) / s, vb.y + (cy - r.top - (r.height - vb.h * s) / 2) / s]; };
  svg.addEventListener("wheel", ev => { ev.preventDefault(); tip(); const [ux, uy] = toUser(ev.clientX, ev.clientY); zoomAt(Math.exp(-ev.deltaY * 0.0015), ux, uy); }, {passive: false});
  const pts = new Map(); let last = null, moved = 0;
  svg.addEventListener("pointerdown", ev => { pts.set(ev.pointerId, [ev.clientX, ev.clientY]); FZ.dragged = false; moved = 0; last = null; });
  svg.addEventListener("pointermove", ev => {
    if (!pts.has(ev.pointerId)) return;
    const prev = pts.get(ev.pointerId); pts.set(ev.pointerId, [ev.clientX, ev.clientY]);
    if (pts.size === 1) {
      moved += Math.abs(ev.clientX - prev[0]) + Math.abs(ev.clientY - prev[1]);
      if (moved > 6) { if (!FZ.dragged) { FZ.dragged = true; svg.setPointerCapture(ev.pointerId); tip(); } const s = ppu(); vb.x -= (ev.clientX - prev[0]) / s; vb.y -= (ev.clientY - prev[1]) / s; apply(); }
    } else if (pts.size === 2) {
      FZ.dragged = true;
      const [a, b] = [...pts.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      if (last) { const [ux, uy] = toUser((a[0] + b[0]) / 2, (a[1] + b[1]) / 2); zoomAt(d / last, ux, uy); }
      last = d;
    }
  });
  const up = ev => { pts.delete(ev.pointerId); if (pts.size < 2) last = null; };
  svg.addEventListener("pointerup", up); svg.addEventListener("pointercancel", up);
  const bar = document.createElement("div"); bar.className = "fanzoom";
  bar.innerHTML = `<button type="button" data-z="out" aria-label="Zoom out">&minus;</button><button type="button" data-z="in" aria-label="Zoom in">+</button><button type="button" data-z="fit">${label || `All ${gens} generations`}</button>`;
  bar.addEventListener("click", ev => {
    const z = ev.target.dataset.z; if (!z) return;
    if (z === "fit") { vb = {...full}; apply(); return; }
    zoomAt(z === "in" ? 1.6 : 1 / 1.6, vb.x + vb.w / 2, vb.y + vb.h / 2);
  });
  wrap.appendChild(bar);
  apply();
  new ResizeObserver(apply).observe(svg);
}
function renderPanelLite(k) {
  const p = P[k], panel = $("#panel");
  if (!known(k)) {
    panel.innerHTML = `<h2>Your family tree</h2><p class="intro">${current === "kin"
      ? "Your ancestors run up the chart in couples. Under each person are their brothers and sisters, with any of their children the research has found. Choose how far back to go at the top; tap anyone for details."
      : current === "lives" ? LIVES_HELP
      : "The chart shows every generation of ancestors the research has found; zoom out to see the outer rings. A branch where more is known gets more of the circle. Colours show where each person was born. Tap a name for details, and double-click it to put that person in the centre."}</p>${treeLegend}`;
    return;
  }
  const first = esc(firstName(k));
  panel.innerHTML = `<div class="rel">${esc(relLabel(k))}</div><h2>${esc(p.n)}</h2><div class="dates">${esc(yearsOf(k))}</div>
    ${p.intro ? `<p class="intro">${esc(p.intro)}</p>` : ""}
    <p><a class="btn" href="#/p/${k}">Open ${first}'s page &rsaquo;</a></p>
    <p class="treebtns">${current !== "tree" ? `<a class="btn ghost" href="#/tree/${k}">${first}'s ancestors</a>` : k !== FAN.centre ? `<a class="btn ghost" href="#/tree/${k}">Centre the fan here</a>` : ""} <a class="btn ghost" href="#/kin/${k}">${first}'s family tree</a></p>
    <p class="hint">${current === "kin" ? `Each couple's children are shown beneath them: the one on the line and their brothers and sisters, with any children of theirs that the research has found. Relationships are to ${ME.self || ME.anchor !== D.root ? "you" : esc(ROOTNAME)}.`
      : current === "lives" ? LIVES_HELP
      : k === FAN.centre ? `The chart shows ${first}'s ancestors, every generation the research has found. Zoom out to see the outer rings.` : `Choose "Centre the fan here", or double-click ${first}'s name in the chart, to see ${first}'s ancestors in the middle.`}</p>
    ${treeLegend}`;
}
const countryKey = () => COUNTRIES.map(([v, t]) => `<span><i style="background:var(${v})"></i>${t}</span>`).join("");
const treeSwitch = (mode, k) => `<span class="treeswitch"><a href="#/tree/${k}" class="${mode === "fan" ? "on" : ""}">Fan of ancestors</a><a href="#/kin/${k}" class="${mode === "kin" ? "on" : ""}">Family tree with siblings</a><a href="#/timeline/${k}" class="${mode === "lives" ? "on" : ""}">Timeline</a></span>`;

// ---------- family tree with siblings: ancestors as couples, top down, with each couple's other children beside the line ----------
const KIN = {depth: 3};
const KIN_ROWS = ["parents", "grandparents", "great-grandparents"];
const backLabel = n => n <= 3 ? KIN_ROWS[n - 1] : `${ord(n - 2)} great-grandparents`;
function showKin(base) {
  current = "kin"; stop();
  base = base || ME.anchor;
  const deep = Math.max(1, Math.min(12, Math.max(0, ...Object.values(upMap(base)))));
  KIN.depth = Math.min(KIN.depth, deep);
  const opts = Array.from({length: deep}, (_, i) => `<option value="${i + 1}"${i + 1 === KIN.depth ? " selected" : ""}>${backLabel(i + 1)}</option>`).join("");
  treeLegend = `<div class="fanlegend"><b>Colour stripe: where each person was born</b>${countryKey()}<span><i style="border:2px solid var(--accent);background:none"></i>${ME.self ? "Your" : esc(ROOTNAME) + "'s"} direct line</span></div>`;
  $("#view").innerHTML = `<div class="treeview"><div class="treebar">${treeSwitch("kin", base)}
    <label class="kindepth">Go back to the <select id="kindepth">${opts}</select></label><span class="hint">Tap anyone for details. Use + and − to zoom, and drag to move.</span></div>
    <div class="fanwrap" id="kin"></div><aside class="panel" id="panel"></aside></div>`;
  $("#kindepth").addEventListener("change", e => { KIN.depth = +e.target.value; $("#kin svg")?.remove(); $(".fanzoom")?.remove(); drawKin(base); });
  J.chain = []; J.map = null; J.line = null;
  drawKin(base);
  renderPanelLite(base);
}
function drawKin(base) {
  const NS = "http://www.w3.org/2000/svg", wrap = $("#kin"), svg = document.createElementNS(NS, "svg");
  const el = (t, a, p = svg) => { const e = document.createElementNS(NS, t); for (const k in a) e.setAttribute(k, a[k]); p.appendChild(e); return e; };
  const BW = 240, GAP = 18, SLOT = BW + GAP, PAIR = 30, LINE = 19, BH = 54, VG = 64, N = KIN.depth;
  const fatherOf = k => parentsOf(k).find(x => P[x]?.sx === "M") || parentsOf(k)[0] || null;
  const motherOf = k => parentsOf(k).find(x => x !== fatherOf(k)) || null;
  const byBirth = (a, b) => (birthYear(a) || 9999) - (birthYear(b) || 9999);
  // Under each person on the line: their brothers and sisters, and any of their children the research has found.
  const listOf = k => {
    const out = [];
    const walk = (x, d) => childrenOf(x).slice().sort(byBirth).forEach(c => { if (out.length < 40) { out.push([c, d]); walk(c, d + 1); } });
    siblingsOf(k).sort(byBirth).forEach(s => { out.push([s, 0]); walk(s, 1); });
    return out;
  };
  const relWord = r => r === 0 ? "Brothers and sisters" : r === 1 ? "Aunts and uncles" : greats(r - 1, "aunts and uncles").replace(/^./, c => c.toUpperCase());
  const spanOf = {}, gen = {};
  const span = (k, g) => {
    gen[k] = g;
    let up = 0;
    if (g < N) { const fa = fatherOf(k), mo = motherOf(k); up = (fa ? span(fa, g + 1) : 0) + (mo ? span(mo, g + 1) : 0) + (fa && mo ? PAIR : 0); }
    return spanOf[k] = Math.max(SLOT, up);
  };
  span(base, 0);
  const pos = {};
  const place = (k, L, side) => {
    const fa = gen[k] < N ? fatherOf(k) : null, mo = gen[k] < N ? motherOf(k) : null;
    const wf = fa ? spanOf[fa] : 0, wm = mo ? spanOf[mo] : 0, tot = wf + wm + (fa && mo ? PAIR : 0);
    const x0 = L + (spanOf[k] - tot) / 2;
    if (fa) place(fa, x0, "f");
    if (mo) place(mo, x0 + wf + (fa ? PAIR : 0), "m");
    // A husband sits at the inner (right) edge of his block and a wife at the inner (left) edge, so couples stand together.
    const R = L + spanOf[k];
    pos[k] = {x: side === "f" ? R - BW : side === "m" ? L : (fa && mo ? x0 + wf + PAIR / 2 : L + spanOf[k] / 2) - BW / 2, row: gen[k]};
  };
  place(base, 0, "c");
  // Keep couples side by side: a father and mother centred over one child are pulled together.
  const list = {}, listH = {};
  Object.keys(pos).forEach(k => { list[k] = listOf(k); listH[k] = list[k].length ? 26 + list[k].length * LINE + 6 : 0; });
  const rowH = Array(N + 1).fill(BH);
  Object.keys(pos).forEach(k => { rowH[pos[k].row] = Math.max(rowH[pos[k].row], BH + listH[k]); });
  const rowY = []; let y = 0;
  for (let r = N; r >= 0; r--) { rowY[r] = y; y += rowH[r] + VG; }
  const mine = upMap(ME.anchor), forReader = base === ME.anchor;
  const g = el("g", {});
  const st = on => `stroke:${css(on ? "--accent" : "--muted")};stroke-width:${on ? 2.2 : 1.2};stroke-opacity:${on ? 1 : .55};fill:none`;
  Object.keys(pos).forEach(k => {
    if (gen[k] >= N) return;
    const fa = fatherOf(k), mo = motherOf(k), pa = [fa, mo].filter(x => x && pos[x]);
    if (!pa.length) return;
    const cy = rowY[gen[k] + 1] + BH / 2, mx = pa.length === 2 ? (pos[fa].x + BW + pos[mo].x) / 2 : pos[pa[0]].x + BW / 2, ys = rowY[gen[k]] - VG / 2;
    const on = k in mine;
    if (pa.length === 2) el("path", {d: `M${pos[fa].x + BW},${cy} H${pos[mo].x}`, style: st(on)}, g);
    el("path", {d: `M${mx},${pa.length === 2 ? cy : cy + BH / 2} V${ys} H${pos[k].x + BW / 2} V${rowY[gen[k]]}`, style: st(on)}, g);
  });
  const fit = (t, n) => t.length > n ? t.slice(0, n - 1) + "…" : t;
  const pick = (who, b) => { document.querySelectorAll(".kin.sel").forEach(e => e.classList.remove("sel")); b.classList.add("sel"); renderPanelLite(who); };
  Object.entries(pos).forEach(([k, p]) => {
    const x = p.x, y0 = rowY[p.row], kn = known(k), L = list[k];
    const b = el("g", {class: "kin" + (k === ME.anchor ? " me" : "") + (k in mine ? " line" : ""), "data-id": k}, g);
    if (L.length) {
      el("rect", {x: x + 4, y: y0 + BH - 4, width: BW - 8, height: listH[k] + 4, rx: 5, class: "sibs"}, b);
      el("text", {x: x + 12, y: y0 + BH + 16, "font-size": 13, "data-fs": 13, class: "sibhead"}, b).textContent = forReader ? relWord(p.row) : "Brothers and sisters";
      L.forEach(([c, d], i) => {
        const t = el("text", {x: x + 12 + d * 10, y: y0 + BH + 26 + (i + 1) * LINE - 5, "font-size": 12.5, "data-fs": 12.5, class: "sub kid" + (d ? " deep" : ""), "data-id": c}, b);
        const yrs = known(c) ? (P[c].by || P[c].est ? `${P[c].by ? "" : "c. "}${P[c].by || P[c].est}${P[c].dy ? "–" + P[c].dy : ""}` : "") : "";
        t.textContent = fit((d ? "↳ " : "") + nameOf(c) + (yrs ? "  " + yrs : ""), 34 - d * 2);
      });
    }
    el("rect", {x, y: y0, width: BW, height: BH, rx: 6, fill: css("--surface"), stroke: css(k in mine ? "--accent" : "--line"), "stroke-width": k in mine ? 2 : 1, class: "main"}, b);
    el("rect", {x, y: y0, width: 5, height: BH, rx: 2, fill: kn ? colorFor(k) : css("--c-un")}, b);
    const rel = relLabel(k), relS = rel && rel !== "You" ? (rel.startsWith("Your ") ? rel.slice(5) : rel.startsWith(ROOTNAME + "'s ") ? rel.slice(ROOTNAME.length + 3) : rel) : rel;
    el("text", {x: x + 13, y: y0 + 23, "font-size": 15.5, "data-fs": 15.5, class: "nm"}, b).textContent = fit(nameOf(k), 27);
    el("text", {x: x + 13, y: y0 + 43, "font-size": 12.5, "data-fs": 12.5, class: "sub"}, b).textContent = fit([kn ? yearsOf(k) : "", relS].filter(Boolean).join(" · "), 34);
    b.addEventListener("pointermove", ev => {
      const who = ev.target.closest("[data-id]")?.dataset.id || k, r = relLabel(who), sp = spousesOf(who).filter(known).map(x => P[x].n);
      tip(ev, `<b>${esc(nameOf(who))}</b>${known(who) ? "<br>" + esc(yearsOf(who)) : ""}${r ? "<br>" + esc(r) : ""}${sp.length ? "<br>Married " + sp.map(esc).join(", ") : ""}`);
    });
    b.addEventListener("pointerleave", () => tip());
    b.addEventListener("click", ev => { if (FZ.dragged) return; tip(); pick(ev.target.closest("[data-id]")?.dataset.id || k, b); });
  });
  const xs = Object.values(pos).map(p => p.x), minX = Math.min(...xs), maxX = Math.max(...xs) + BW;
  for (let r = 1; r <= N; r++) el("text", {x: minX - 18, y: rowY[r] + BH / 2 + 5, "text-anchor": "end", "font-size": 17, "data-fs": 17, class: "rowlab"}, g).textContent = backLabel(r)[0].toUpperCase() + backLabel(r).slice(1);
  wrap.appendChild(svg);
  const pad = 30, labW = 240, full = {x: minX - labW - pad, y: -pad - 50, w: maxX - minX + labW + 2 * pad, h: y - VG + 2 * pad + 50};
  const r = wrap.getBoundingClientRect(), W = r.width || 900, H = r.height || 700;
  let start = full;
  if (Math.min(W / full.w, H / full.h) < 1.05) {   // too small to read: start at a readable size, the base person at the bottom centre
    const s = 1.1, w = W / s, h = H / s, me = pos[base];
    start = {x: me.x + BW / 2 - w / 2, y: Math.max(full.y, full.y + full.h - h), w, h};
  }
  fanZoom(svg, wrap, full, start, N, `Show all ${Object.keys(pos).length + Object.values(list).reduce((a, l) => a + l.length, 0)} people`);
}

// ---------- ancestors on a timeline: each life a bar from birth to death, in bands by father's line ----------
// A band is one father-to-son line: everyone whose father, father's father and so on lead to the same earliest man,
// so a woman sits in her own father's band. The band's earliest known person is marked as where the line ends.
// Years come only from what the reader may see; a missing birth year is estimated and drawn hollow.
const LV = {base: null, ppy: 0, which: "all", hist: true, sel: null, data: null};
const LIVES_HELP = "Each bar is one ancestor's life, from birth to death, in bands by family line: a father, his father and so on. The mark at the left of a band is the earliest person found on that line. A dot means only the birth year is known; a hollow or dashed shape means the year is estimated. Tap anyone for details.";
function livesData(base) {
  const gen = upMap(base), ks = Object.keys(gen).filter(known), yr = {};
  // Years: the recorded birth; else the site's estimate from a close relative; else a child's birth less 30, a parent's
  // plus 30, or the death less 60. Only years in the data this reader has are used, so nothing hidden is worked out.
  ks.forEach(k => { const p = P[k]; yr[k] = p.by ? {b: p.by} : p.est ? {b: p.est, est: "from a close relative's dates"} : null; });
  const near = [...ks].sort((a, b) => gen[a] - gen[b]);
  const yearOf = x => yr[x] ? yr[x].b : known(x) && P[x].by ? P[x].by : null;
  near.forEach(k => { if (yr[k]) return; const ys = childrenOf(k).map(yearOf).filter(Boolean); if (ys.length) yr[k] = {b: Math.min(...ys) - 30, est: "about 30 years before a child's birth"}; });
  [...near].reverse().forEach(k => { if (yr[k]) return; const ys = parentsOf(k).map(yearOf).filter(Boolean); if (ys.length) yr[k] = {b: Math.max(...ys) + 30, est: "about 30 years after a parent's birth"}; });
  ks.forEach(k => { if (!yr[k] && P[k].dy) yr[k] = {b: P[k].dy - 60, est: "from the year of death"}; });
  ks.forEach(k => { const v = yr[k]; if (!v) return; v.d = P[k].dy || null; if (v.d && v.d < v.b) v.b = v.d; });
  const drawn = ks.filter(k => yr[k]);
  // Bands: the topmost known father of each person; ordered as the fan chart goes round, the reader's father's line first.
  const fatherOf = k => { const f = parentsOf(k).find(x => P[x]?.sx === "M"); return f && known(f) ? f : null; };
  const topOf = {};
  const top = k => { if (topOf[k]) return topOf[k]; let x = k; const seen = new Set([x]); for (let f; (f = fatherOf(x)) && !seen.has(f);) { seen.add(f); x = f; } return (topOf[k] = x); };
  const order = [], seen = new Set(), ordered = new Set();
  const stack = [base];
  while (stack.length) {   // depth first, father before mother, without recursion
    const k = stack.pop();
    if (seen.has(k)) continue;
    seen.add(k);
    if (yr[k] && !ordered.has(top(k))) { ordered.add(top(k)); order.push(top(k)); }
    const [fa, mo] = fanParents(k);
    if (mo) stack.push(mo);
    if (fa) stack.push(fa);
  }
  const byTop = {};
  drawn.forEach(k => (byTop[top(k)] ||= []).push(k));
  const bands = order.map(t => {
    const ks2 = byTop[t].sort((a, b) => yr[a].b - yr[b].b || gen[b] - gen[a]);
    return {top: t, sn: P[t].sn || P[t].n.split(" ").slice(-1)[0], ks: ks2, first: ks2[0]};
  });
  const ys = drawn.flatMap(k => [yr[k].b, yr[k].d || yr[k].b]);
  const y0 = Math.floor((Math.min(...ys) - 5) / 10) * 10;
  const y1 = Math.min(new Date().getFullYear() + 1, Math.max(...ys, known(base) && P[base].by ? P[base].by : 0) + 10);
  // Historical events as on the map's timeline: those of a region where an ancestor lived, those naming an ancestor,
  // and local ones ("near") only where an ancestor was recorded at a matching place within 30 years of them.
  const regs = new Set(["world"]); drawn.forEach(k => (P[k].regions || []).forEach(r => regs.add(r)));
  const hist = (H || []).filter(h => {
    if (h.y > y1 || (h.y2 || h.y) < y0) return false;
    if ((h.people || []).some(x => x in yr)) return true;
    if (!h.near) return (h.regions || []).some(r => regs.has(r));
    const re = new RegExp(h.near);
    return drawn.some(k => (P[k].ev || []).some(e => e.pk && e.y && re.test(e.pk) && e.y >= h.y - 30 && e.y <= (h.y2 || h.y) + 30));
  });
  return {base, yr, bands, y0, y1, hist, missing: ks.length - drawn.length};
}
function showLives(base) {
  current = "lives"; stop();
  base = base || ME.anchor;
  if (LV.base !== base) { LV.base = base; LV.ppy = 0; LV.sel = null; }
  treeLegend = `<div class="fanlegend"><b>Colour: where each person was born</b>${countryKey()}
    <span><i style="background:var(--muted)"></i>Birth to death</span><span><i style="background:var(--muted);border-radius:50%"></i>Only the birth year known</span>
    <span><i style="border:1.5px dashed var(--ink);background:none"></i>Birth year estimated</span><span><i style="background:var(--accent);width:5px"></i>Earliest known on the line</span>
    <span><i style="background:var(--history);opacity:.45;width:5px"></i>Historical event</span></div>`;
  const who = base === ME.anchor ? (youName() === "You" ? "your" : esc(ROOTNAME) + "'s") : esc(firstName(base)) + "'s";
  $("#view").innerHTML = `<div class="treeview"><div class="treebar">${treeSwitch("lives", base)}
    <span class="lvzoom"><button type="button" data-lz="out" aria-label="Zoom out: more years at once">&minus;</button><button type="button" data-lz="in" aria-label="Zoom in">+</button><button type="button" data-lz="fit">Fit all the years</button></span>
    <label class="kindepth">Show <select id="lvwhich"><option value="all">Every family line</option><option value="long"${LV.which === "long" ? " selected" : ""}>Lines of two or more people</option></select></label>
    <label class="lvhist"><input type="checkbox" id="lvhist"${LV.hist ? " checked" : ""}> History</label>
    <span class="hint">Each bar is one of ${who} ancestors. Tap a bar for details; scroll or drag sideways through the years.</span></div>
    <div class="lvwrap" id="lvwrap"><div class="lvscroll" id="lvscroll" tabindex="0" aria-label="Ancestors by year of birth"></div><div class="selcard" id="selcard" hidden></div></div><aside class="panel" id="panel"></aside></div>`;
  J.chain = []; J.map = null; J.line = null;
  LV.data = livesData(base);
  const box = $("#lvscroll");
  $("#lvwhich").addEventListener("change", e => { LV.which = e.target.value; drawLives(); });
  $("#lvhist").addEventListener("change", e => { LV.hist = e.target.checked; drawLives(); });
  $(".lvzoom").addEventListener("click", e => { const z = e.target.closest("[data-lz]")?.dataset.lz; if (z) livesZoom(z === "in" ? 1.6 : z === "out" ? 1 / 1.6 : 0); });
  // Pinch (two fingers on a phone, or a trackpad, which sends ctrl and the wheel) zooms the years; one finger scrolls.
  box.addEventListener("wheel", ev => { if (!ev.ctrlKey) return; ev.preventDefault(); livesZoom(Math.exp(-ev.deltaY * 0.01), ev.clientX); }, {passive: false});
  let pinch = null;
  box.addEventListener("touchstart", ev => { if (ev.touches.length === 2) pinch = Math.abs(ev.touches[0].clientX - ev.touches[1].clientX) || 1; }, {passive: true});
  box.addEventListener("touchmove", ev => {
    if (ev.touches.length !== 2 || !pinch) return;
    ev.preventDefault();
    const d = Math.abs(ev.touches[0].clientX - ev.touches[1].clientX) || 1;
    if (Math.abs(d / pinch - 1) > 0.08) { livesZoom(d / pinch, (ev.touches[0].clientX + ev.touches[1].clientX) / 2); pinch = d; }
  }, {passive: false});
  box.addEventListener("touchend", ev => { if (ev.touches.length < 2) pinch = null; });
  box.addEventListener("click", ev => { const g = ev.target.closest("[data-k]"); if (!g) return; tip(); livesSelect(g.dataset.k); });
  box.addEventListener("pointerover", ev => {
    const g = ev.target.closest("[data-k],[data-h]"); if (!g) return;
    if (g.dataset.h) { const h = LV.data.hist[+g.dataset.h]; return tip(ev, `<b>${esc(h.title)}</b> ${h.y}${h.y2 ? "–" + h.y2 : ""}${h.text ? "<br>" + esc(h.text) : ""}`); }
    tip(ev, livesTip(g.dataset.k));
  });
  box.addEventListener("pointerout", ev => { if (ev.target.closest("[data-k],[data-h]")) tip(); });
  box.addEventListener("scroll", () => tip(), {passive: true});
  drawLives(true);
  renderPanelLite(LV.sel || base);
}
function livesTip(k) {
  const v = LV.data.yr[k], b = LV.data.bands.find(x => x.ks.includes(k)), rel = relLabel(k);
  const yrs = v.est ? `born about ${v.b} (estimated ${v.est})${v.d ? `, died ${v.d}` : ""}` : yearsOf(k);
  return `<b>${esc(P[k].n)}</b><br>${esc(yrs)}${rel ? "<br>" + esc(rel) : ""}${b && b.first === k ? `<br><i>The earliest person found on the ${esc(b.sn)} line</i>` : ""}`;
}
function livesSelect(k) {
  LV.sel = k;
  document.querySelectorAll("#lvscroll .lv").forEach(g => g.classList.toggle("sel", g.dataset.k === k));
  renderPanelLite(k);
  showSelCard(k);
}
// Zoom by a factor (0: fit every year on the screen), keeping the year under the pointer, or in the middle, where it is.
function livesZoom(f, clientX) {
  const box = $("#lvscroll"), d = LV.data; if (!box || !d) return;
  const r = box.getBoundingClientRect(), LW = LV.LW, view = box.clientWidth - LW;
  const px = clientX != null ? Math.max(0, Math.min(view, clientX - r.left - LW)) : view / 2;
  const year = d.y0 + (box.scrollLeft + px - 12) / LV.ppy;
  LV.ppy = f ? LV.ppy * f : -1;
  drawLives();
  box.scrollLeft = 12 + (year - d.y0) * LV.ppy - px;
}
function drawLives(first) {
  const box = $("#lvscroll"), d = LV.data; if (!box || !d) return;
  if (!d.bands.length) { box.innerHTML = `<p class="lvnone">No years are known yet for these ancestors.</p>`; return; }
  const narrow = box.clientWidth < 600, LW = LV.LW = narrow ? 96 : 160, AX = 34, LANE = 24, PAD = 8;
  const span = d.y1 - d.y0, fit = Math.max(0.05, (box.clientWidth - LW - 24) / span);
  if (LV.ppy < 0) LV.ppy = fit;
  if (!LV.ppy) LV.ppy = Math.max(fit, narrow ? 1.5 : 0);
  LV.ppy = Math.max(fit, Math.min(30, LV.ppy));
  const ppy = LV.ppy, W = Math.ceil(span * ppy) + 24, X = y => 12 + (y - d.y0) * ppy;
  const bands = d.bands.filter(b => LV.which !== "long" || b.ks.length > 1);
  let y = 0;
  const lay = bands.map(b => {
    // Each band packs its lives into as few rows as will not overlap.
    const ends = [];
    const items = b.ks.map(k => {
      const v = d.yr[k], x = X(v.b), x2 = v.d ? Math.max(x + 6, X(v.d)) : x;
      let lane = ends.findIndex(e => e < x - 4);
      if (lane < 0) { lane = ends.length; ends.push(0); }
      ends[lane] = v.d ? x2 : x + 7;
      return {k, v, x, x2, lane};
    });
    items.forEach(it => { it.next = Math.min(Infinity, ...items.filter(o => o.lane === it.lane && o.x > it.x).map(o => o.x)); });
    const h = Math.max(46, ends.length * LANE + 2 * PAD), out = {b, y, h, items};
    y += h;
    return out;
  });
  const HT = y, tw = s => s.length * 7.2;
  const step = [5, 10, 20, 25, 50, 100, 200].find(s => s * ppy >= 70) || 200;
  let grid = "", axis = "", labels = "", body = "";
  lay.forEach((L, i) => {
    grid += `<rect class="band${i % 2 ? " alt" : ""}" x="0" y="${L.y}" width="${W}" height="${L.h}"></rect>`;
    const fy = d.yr[L.b.first];
    labels += `<rect class="band${i % 2 ? " alt" : ""}" x="0" y="${L.y}" width="${LW}" height="${L.h}"></rect><text class="bname" x="10" y="${L.y + 21}">${esc(L.b.sn.length > (narrow ? 10 : 17) ? L.b.sn.slice(0, narrow ? 9 : 16) + "…" : L.b.sn)}</text>
      <text class="bsub" x="10" y="${L.y + 38}">${narrow ? "" : `${L.b.ks.length} ${L.b.ks.length === 1 ? "person" : "people"}, `}from ${fy.est ? "c. " : ""}${fy.b}</text>`;
  });
  for (let t = Math.ceil(d.y0 / step) * step; t <= d.y1; t += step) {
    grid += `<line class="grid" x1="${X(t)}" x2="${X(t)}" y1="0" y2="${HT}"></line>`;
    if (X(t) > 18) axis += `<text x="${X(t)}" y="20" text-anchor="middle">${t}</text>`;
  }
  if (LV.hist) d.hist.forEach((h, i) => {
    const x = X(h.y), w = h.y2 ? Math.max(3, X(h.y2) - x) : 1.5;
    const kind = h.y2 ? "hist rg" : "hist pt";  // a period is drawn fainter than a single year, so overlapping periods do not darken the chart
    grid += `<rect class="${kind}" data-h="${i}" x="${x}" y="0" width="${w}" height="${HT}"></rect>`;
    axis += `<rect class="${kind}" data-h="${i}" x="${x}" y="26" width="${Math.max(w, 3)}" height="8"></rect>`;
  });
  lay.forEach(L => L.items.forEach(({k, v, x, x2, lane, next}) => {
    const top = L.y + PAD + lane * LANE, cyy = top + LANE / 2, c = colorFor(k), est = !!v.est;
    let g = `<g class="lv${LV.sel === k ? " sel" : ""}" data-k="${k}">`;
    if (L.b.first === k) g += `<rect class="end" x="${x - 6}" y="${top + 1}" width="4" height="${LANE - 2}" rx="1"></rect>`;
    g += v.d
      ? `<rect x="${x}" y="${top + 3}" width="${x2 - x}" height="${LANE - 6}" rx="4" fill="${c}"${est ? ` fill-opacity=".25" stroke="${c}" stroke-width="1.5" stroke-dasharray="4 3"` : ""}></rect>`
      : `<circle cx="${x}" cy="${cyy}" r="6" fill="${est ? css("--surface") : c}"${est ? ` stroke="${c}" stroke-width="2" stroke-dasharray="3 2"` : ""}></circle>`;
    // The name inside the bar when it fits, else after it when there is room before the next life in the row.
    const nm = P[k].n, fn = firstName(k), inside = v.d ? x2 - x - 10 : 0, after = next - (v.d ? x2 : x + 7) - 12;
    const txt = [nm, fn].find(s => tw(s) <= inside);
    if (txt) g += `<text x="${x + 5}" y="${cyy + 1}" style="fill:${est ? css("--ink") : inkOn(c)}">${esc(txt)}</text>`;
    else { const t2 = [nm, fn].find(s => tw(s) <= after); if (t2) g += `<text class="out" x="${(v.d ? x2 : x + 7) + 5}" y="${cyy + 1}">${esc(t2)}</text>`; }
    body += g + `<rect class="hit" x="${x - 7}" y="${top}" width="${Math.max(14, (v.d ? x2 - x : 0) + 14)}" height="${LANE}"></rect></g>`;
  }));
  const keepX = box.scrollLeft, keepY = box.scrollTop;
  box.innerHTML = `<div class="lvgrid" style="grid-template-columns:${LW}px ${W}px">
    <div class="lvcorner" style="height:${AX}px">${d.missing ? `<span title="${d.missing} ancestors have no year to place them by">${d.missing} not shown</span>` : ""}</div>
    <svg class="lvaxis" width="${W}" height="${AX}" aria-hidden="true">${axis}</svg>
    <svg class="lvlabels" width="${LW}" height="${HT}" aria-hidden="true">${labels}</svg>
    <svg class="lvchart" width="${W}" height="${HT}" role="img" aria-label="Lives of ${lay.reduce((a, L) => a + L.items.length, 0)} ancestors, ${d.y0} to ${d.y1}">${grid}${body}</svg></div>`;
  if (first) box.scrollLeft = box.scrollWidth;   // start at the recent end, nearest the reader
  else { box.scrollLeft = keepX; box.scrollTop = keepY; }
}

// ---------- stories (stage 4): text scrolls, the map follows ----------
function showStories() {
  current = "stories"; stop();
  const list = D.stories || [];
  $("#view").innerHTML = `<div class="records"><h1>Stories</h1><p class="sub">Chapters of the family's history, one for each branch. As you read down a chapter, the map follows along.</p>
    <div class="storylist">${list.map(st => `<a class="storycard" href="#/story/${esc(st.slug)}"><div class="k">${esc(st.line ? "The " + st.line + " line" : "")}</div>
      <h2>${esc(st.title)}</h2><p>${esc(st.subtitle)}</p></a>`).join("") || "<p>No chapters yet.</p>"}</div></div>`;
}
const S = {map: null, obs: null, layers: null, i: -1};
function showStory(st) {
  endStory(); current = "story"; stop();
  const steps = st.steps, chapters = D.stories || [], at = chapters.indexOf(st), prevSt = chapters[at - 1], nextSt = chapters[at + 1];
  $("#view").innerHTML = `<div class="story">
    <div class="storytext">
      <header class="storyhead"><a href="#/stories" class="back">&lsaquo; All stories</a><h1>${esc(st.title)}</h1><p>${esc(st.subtitle)}</p></header>
      ${steps.map((x, i) => `<section class="step${x.kind === "note" ? " note" : ""}" data-i="${i}">
        ${x.kind === "step" ? `<div class="stepmeta">${x.year ? Math.floor(x.year) : ""}${x.name ? " · " + esc(x.name) : ""}</div>` : ""}
        ${x.html}
        ${x.person && known(x.person) ? `<p class="steplink"><a href="#/p/${x.person}">${esc(P[x.person].n)}: life, family and records &rsaquo;</a></p>` : ""}
        ${x.src ? `<details class="srcnote"><summary>About the sources</summary>${x.src}</details>` : ""}
      </section>`).join("")}
      <div class="storynav">${prevSt ? `<a class="btn ghost" href="#/story/${esc(prevSt.slug)}">&lsaquo; ${esc(prevSt.title)}</a>` : `<a class="btn ghost" href="#/stories">&lsaquo; All stories</a>`}
        ${nextSt ? `<a class="btn" href="#/story/${esc(nextSt.slug)}">Next chapter: ${esc(nextSt.title)} &rsaquo;</a>` : `<a class="btn" href="#/stories">All stories</a>`}
        <button class="btn ghost" type="button" data-print>Print this chapter</button></div>
      ${D.site?.story_footer ? `<p class="foot">${esc(D.site.story_footer)}</p>` : ""}
    </div>
    <div class="storymap"><div class="map" id="smap"></div><div class="yearbig" id="syear"></div></div>
  </div>`;
  window.scrollTo(0, 0);
  S.map = L.map("smap", {zoomControl: true, scrollWheelZoom: false});
  const dark = matchMedia("(prefers-color-scheme: dark)").matches && document.documentElement.dataset.theme !== "light";
  L.tileLayer(`https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_${dark ? "Dark" : "Light"}_Gray_Base/MapServer/tile/{z}/{y}/{x}`,
    {maxZoom: 16, attribution: "Tiles &copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors"}).addTo(S.map);
  S.layers = L.layerGroup().addTo(S.map);
  S.pts = steps.filter(x => x.lat != null);
  S.pts.forEach(x => L.circleMarker([x.lat, x.lon], {radius: 5, color: css(x.lon > -30 ? "--map-europe" : "--map-america"), weight: 2, fillColor: css("--surface"), fillOpacity: 1})
    .bindTooltip(esc(x.name), {direction: "top"}).addTo(S.layers));
  for (let i = 1; i < S.pts.length; i++) {
    const a = [S.pts[i - 1].lat, S.pts[i - 1].lon], b = [S.pts[i].lat, S.pts[i].lon];
    const pts = isEurope(a) !== isEurope(b) ? arc(a, b) : [a, b];
    L.polyline(pts, {color: css("--muted"), weight: 1.5, opacity: .45, dashArray: "4 6"}).addTo(S.layers);
    S.pts[i].seg = L.polyline(pts, {color: css("--accent"), weight: 3, opacity: 0}).addTo(S.layers);
  }
  S.here = L.marker([0, 0], {icon: L.divIcon({className: "", html: '<div class="pulse"></div>', iconSize: [18, 18], iconAnchor: [9, 9]}), interactive: false, keyboard: false});
  S.map.fitBounds(L.latLngBounds(S.pts.map(x => [x.lat, x.lon])), {padding: [30, 30]});
  S.i = -1;
  S.obs = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) storyStep(steps, +e.target.dataset.i); }), {rootMargin: "-45% 0px -50% 0px"});
  document.querySelectorAll(".step").forEach(el => S.obs.observe(el));
  storyStep(steps, 0);
}
function storyStep(steps, i) {
  if (i === S.i) return;
  S.i = i;
  document.querySelectorAll(".step").forEach(el => el.classList.toggle("on", +el.dataset.i === i));
  const x = steps[i];
  if (x.kind === "note" || x.lat == null) {
    S.map.flyToBounds(L.latLngBounds(S.pts.map(p => [p.lat, p.lon])), {padding: [30, 30], duration: 1.2});
    S.pts.forEach(p => p.seg && p.seg.setStyle({opacity: .9}));
    $("#syear").textContent = "";
    return;
  }
  const k = S.pts.indexOf(x);
  S.pts.forEach((p, j) => p.seg && p.seg.setStyle({opacity: j <= k ? .9 : 0}));
  S.here.setLatLng([x.lat, x.lon]).addTo(S.map);
  const prev = S.pts[k - 1];
  if (prev && Math.abs(prev.lon - x.lon) > 20) S.map.flyToBounds(L.latLngBounds([[prev.lat, prev.lon], [x.lat, x.lon]]), {padding: [40, 40], duration: 1.8});
  else S.map.flyTo([x.lat, x.lon], x.zoom || 8, {duration: 1.6});
  $("#syear").textContent = x.year ? Math.floor(x.year) : "";
}
function endStory() {
  if (S.obs) S.obs.disconnect();
  if (S.map) S.map.remove();
  S.obs = null; S.map = null;
}

// ---------- military service: a small map of each person's route, stop by stop ----------
const SV = {map: null, layers: null, marks: []};
const SURE = {record: ["His own record", "solid"], index: ["From an index", "solid"], unit: ["His unit's record, not his own", "hollow"], guess: ["A guess, no record", "guess"],
  secondary: ["A family or county history only, no record seen", "guess"], lead: ["An online memorial only, no record seen", "guess"]};
function showService(k) {
  current = "service"; stop();
  const all = D.service || [];
  const rec = k && all.find(x => x.person === k);
  if (!rec) {
    const wars = [...new Set(all.map(x => x.war))];
    const first = x => x.stops.find(s => s.y)?.y || 0;
    wars.sort((a, b) => Math.min(...all.filter(x => x.war === a).map(first)) - Math.min(...all.filter(x => x.war === b).map(first)));
    $("#view").innerHTML = `<div class="records"><h1>Military service</h1><p class="sub">Family members whose service is traced in the records, stop by stop. Open one to follow the route on a map.</p>
      ${wars.map(w => `<h2 class="svwar">${esc(w)}</h2><div class="storylist">${all.filter(x => x.war === w).map(x => `<a class="storycard" href="#/service/${x.person}">
        <div class="k">${esc(x.branch)}</div><h2>${esc(nameOf(x.person))}</h2><p>${esc(x.unit)}</p></a>`).join("")}</div>`).join("") || "<p>None yet.</p>"}
      <p class="foot">Others in the tree have some record of service, or only a draft registration, and will be added as their records are checked.</p></div>`;
    return;
  }
  const stops = rec.stops;
  $("#view").innerHTML = `<div class="story service">
    <div class="storytext">
      <header class="storyhead"><a href="#/service" class="back">&lsaquo; All military service</a><h1>${esc(nameOf(k))}</h1>
        <p>${esc(rec.war)} · ${esc(rec.branch)}</p></header>
      <p class="svunit">${esc(rec.unit)}</p>
      <p class="svsum">${esc(rec.summary)}</p>
      <div class="svkey">${Object.entries(SURE).filter(([s]) => stops.some(x => x.sure === s)).map(([s, [lab, cls]]) => `<span><i class="dot ${cls}"></i>${esc(lab)}</span>`).join("")}</div>
      <ol class="svstops">${stops.map((x, i) => `<li class="${x.lat != null ? "placed" : ""} ${SURE[x.sure]?.[1] || ""}" data-i="${i}">
        <div class="when">${esc(x.date)}</div><div class="what">${esc(x.place)}</div><div>${esc(x.text)}</div>
        <div class="src">${esc((SURE[x.sure] || [""])[0])}. ${esc(x.src)}</div></li>`).join("")}</ol>
      <p class="steplink"><a href="#/p/${k}">${esc(nameOf(k))}: life, family and records &rsaquo;</a></p>
      <p class="foot">Places are approximate. Where only his unit's records survive, the map shows where the unit was, not proof of where he stood.</p>
    </div>
    <div class="storymap"><div class="map" id="svmap"></div><div class="yearbig" id="svyear"></div></div>
  </div>`;
  window.scrollTo(0, 0);
  SV.map = L.map("svmap", {zoomControl: true, scrollWheelZoom: false, worldCopyJump: false});
  const dark = matchMedia("(prefers-color-scheme: dark)").matches && document.documentElement.dataset.theme !== "light";
  L.tileLayer(`https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_${dark ? "Dark" : "Light"}_Gray_Base/MapServer/tile/{z}/{y}/{x}`,
    {maxZoom: 16, attribution: "Tiles &copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors"}).addTo(SV.map);
  SV.layers = L.layerGroup().addTo(SV.map);
  // Unwrap longitudes so a Pacific crossing runs west across the ocean, not east across the world.
  const pts = [];
  let prevLon = null;
  stops.forEach((x, i) => {
    if (x.lat == null) return;
    let lon = x.lon;
    if (prevLon != null) { while (lon - prevLon > 180) lon -= 360; while (prevLon - lon > 180) lon += 360; }
    prevLon = lon;
    pts.push({i, ll: [x.lat, lon], x});
  });
  for (let j = 1; j < pts.length; j++) {
    const a = pts[j - 1], b = pts[j], weak = [a.x.sure, b.x.sure].some(s => s !== "record" && s !== "index");
    const far = Math.abs(a.ll[1] - b.ll[1]) > 25 && isEurope(a.ll) !== isEurope(b.ll);
    L.polyline(far ? arc(a.ll, b.ll) : [a.ll, b.ll], {color: css("--accent"), weight: 2.5, opacity: .8, dashArray: weak ? "5 7" : null}).addTo(SV.layers);
  }
  SV.marks = [];
  pts.forEach((p, j) => {
    const cls = SURE[p.x.sure]?.[1] || "solid";
    const m = L.circleMarker(p.ll, {radius: 7, color: css("--accent"), weight: 2, dashArray: cls === "guess" ? "2 3" : null,
      fillColor: cls === "solid" ? css("--accent") : css("--surface"), fillOpacity: 1})
      .bindTooltip(`${j + 1}. ${esc(p.x.place)}`, {direction: "top"}).addTo(SV.layers);
    m.on("click", () => svFocus(p.i, true));
    SV.marks[p.i] = {m, ll: p.ll};
  });
  if (rec.center && pts.length < 2) SV.map.setView(rec.center.slice(0, 2), rec.center[2] || 6);
  else if (pts.length) SV.map.fitBounds(L.latLngBounds(pts.map(p => p.ll)), {padding: [30, 30], maxZoom: 12});
  else SV.map.setView([39, -77], 4);
  document.querySelectorAll(".svstops li").forEach(li => li.addEventListener("click", () => svFocus(+li.dataset.i, false)));
}
function svFocus(i, scroll) {
  const rec = (D.service || []).find(x => x.person === location.hash.split("/")[2]);
  document.querySelectorAll(".svstops li").forEach(li => li.classList.toggle("on", +li.dataset.i === i));
  const mk = SV.marks[i];
  if (mk) { SV.map.flyTo(mk.ll, Math.max(SV.map.getZoom(), 6), {duration: 1.2}); mk.m.openTooltip(); }
  const y = rec?.stops[i]?.y;
  $("#svyear").textContent = y ? Math.floor(y) : "";
  if (scroll) document.querySelector(`.svstops li[data-i="${i}"]`)?.scrollIntoView({behavior: "smooth", block: "center"});
}
function endService() { if (SV.map) SV.map.remove(); SV.map = null; SV.marks = []; }

// ---------- numbers (stage 5): statistics and repeated names ----------
const NAME_FORMS = {
  louis: "Louis", lewis: "Louis", ludwig: "Louis", ludovicus: "Louis", john: "John", johann: "John", johannes: "John", jean: "John", jan: "John",
  joannes: "John", hans: "John", jacob: "Jacob", jakob: "Jacob", jacobus: "Jacob", james: "James", jacques: "James", philip: "Philip", philipp: "Philip",
  phillip: "Philip", philippe: "Philip", philippus: "Philip", george: "George", georg: "George", georgius: "George", henry: "Henry", heinrich: "Henry",
  henri: "Henry", henricus: "Henry", william: "William", wilhelm: "William", guillaume: "William", wilhelmus: "William", charles: "Charles",
  karl: "Charles", carl: "Charles", carolus: "Charles", peter: "Peter", pierre: "Peter", petrus: "Peter", piotr: "Peter", mary: "Mary", maria: "Mary",
  marie: "Mary", ann: "Anne", anna: "Anne", anne: "Anne", catherine: "Catherine", katharina: "Catherine", catharina: "Catherine",
  katherine: "Catherine", catherina: "Catherine", elizabeth: "Elizabeth", elisabeth: "Elizabeth", elisabetha: "Elizabeth", margaret: "Margaret",
  margaretha: "Margaret", margarethe: "Margaret", marguerite: "Margaret", francis: "Francis", frances: "Frances", franz: "Francis",
  francois: "Francis", franciscus: "Francis", joseph: "Joseph", josef: "Joseph", josephus: "Joseph", michael: "Michael", michel: "Michael",
  thomas: "Thomas", tomasz: "Thomas", andrew: "Andrew", andreas: "Andrew", andre: "Andrew", andrzej: "Andrew", nicholas: "Nicholas",
  nicolas: "Nicholas", nikolaus: "Nicholas", nicolaus: "Nicholas", matthias: "Matthias", mathias: "Matthias", dorothea: "Dorothy",
  dorothee: "Dorothy", dorothy: "Dorothy", magdalena: "Magdalene", madeleine: "Magdalene", magdalene: "Magdalene", jeanne: "Jane",
  johanna: "Jane", jane: "Jane", joan: "Jane", anthony: "Anthony", anton: "Anthony", antoine: "Anthony", conrad: "Conrad", konrad: "Conrad",
  christian: "Christian", adam: "Adam", eva: "Eve", eve: "Eve", susanna: "Susanna", susan: "Susanna", barbara: "Barbara", salome: "Salome"};
const SKIP_WORDS = new Set(["me", "abbe", "deacon", "colonel", "col", "sr", "jr", "rev", "dr", "capt", "i", "ii", "iii", "the", "de", "du", "la", "le", "von", "van"]);
function givenNames(k) {
  const p = P[k]; if (!p || !p.n) return [];
  const sn = fold(p.sn || "").split(/\s+/);
  return fold(p.n).replace(/"[^"]*"/g, " ").replace(/[^a-z\s]/g, " ").split(/\s+/)
    .filter(w => w && !sn.includes(w) && !SKIP_WORDS.has(w) && w.length > 1)
    .map(w => NAME_FORMS[w] || w[0].toUpperCase() + w.slice(1));
}
function median(a) { const b = [...a].sort((x, y) => x - y), n = b.length; return n ? (n % 2 ? b[(n - 1) / 2] : (b[n / 2 - 1] + b[n / 2]) / 2) : null; }
const centuryOf = y => Math.floor(y / 100) * 100;
const centuryName = c => `${c}s`;
function birthCountry(k) {
  const b = (P[k]?.ev || []).find(e => /Born|Christened|Baptized/.test(e.t) && e.pk);
  return b ? (regionKey(b.pk) || "--c-un") : "--c-un";
}
// Horizontal bars: rows [{label, value, text, tip}]. On a narrow screen each label sits above its bar.
function hbars(rows, {max, labelW = 170, fmt = v => v, W = 640} = {}) {
  const stack = W < 520, bh = stack ? 48 : 28, gap = 2, H = rows.length * (bh + gap);
  const mx = max || Math.max(...rows.map(r => r.value), 1), x0 = stack ? 0 : labelW, w = W - x0 - 100;
  return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img">${rows.map((r, i) => {
    const y = i * (bh + gap), bw = Math.max(2, r.value / mx * w), by = stack ? y + 24 : y + 5, bhh = stack ? 18 : bh - 10;
    return `<g class="hov" data-tip="${esc(r.tip || "")}"><rect x="0" y="${y}" width="${W}" height="${bh}" fill="transparent"></rect>
      ${stack ? `<text x="0" y="${y + 16}">${esc(r.label)}</text>` : `<text x="${x0 - 8}" y="${y + bh / 2 + 5}" text-anchor="end">${esc(r.label)}</text>`}
      <rect x="${x0}" y="${by}" width="${bw}" height="${bhh}" rx="3" fill="${r.color || "var(--accent)"}"></rect>
      <text class="val" x="${x0 + bw + 6}" y="${by + bhh / 2 + 5}">${esc(r.text ?? fmt(r.value))}</text></g>`;
  }).join("")}</svg>`;
}
// Vertical bars: rows [{label, value, text, tip}]
function vbars(rows, {unit = "", W = 400} = {}) {
  const H = 230, top = 22, bot = 30, n = rows.length, slot = (W - 40) / Math.max(n, 1), bw = Math.min(46, slot - 8);
  const mx = Math.max(...rows.map(r => r.value), 1), sc = v => (H - top - bot) * v / mx;
  const ticks = [0, mx / 2, mx].map(v => `<line class="grid" x1="30" x2="${W}" y1="${H - bot - sc(v)}" y2="${H - bot - sc(v)}"></line><text x="26" y="${H - bot - sc(v) + 4}" text-anchor="end">${Math.round(v)}</text>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img">${ticks}${rows.map((r, i) => {
    const cx = 40 + slot * i + slot / 2, h = Math.max(2, sc(r.value));
    return `<g class="hov" data-tip="${esc(r.tip || "")}"><rect x="${cx - slot / 2}" y="${top}" width="${slot}" height="${H - top - bot}" fill="transparent"></rect>
      <rect x="${cx - bw / 2}" y="${H - bot - h}" width="${bw}" height="${h}" rx="3" fill="var(--accent)"></rect>
      <text class="val" x="${cx}" y="${H - bot - h - 5}" text-anchor="middle">${esc(r.text ?? Math.round(r.value) + unit)}</text>
      <text x="${cx}" y="${H - 8}" text-anchor="middle">${esc(r.label)}</text></g>`;
  }).join("")}</svg>`;
}
const table = (head, rows) => `<details><summary>Show as a table</summary><table><tr>${head.map(h => `<th>${esc(h)}</th>`).join("")}</tr>${rows.map(r => `<tr>${r.map(c => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</table></details>`;

function showNumbers() {
  current = "numbers"; stop();
  const root = D.root;
  // Direct ancestors and their generation (nearest path if a person appears twice).
  const gen = upMap(root); delete gen[root];
  const anc = Object.keys(gen);
  const maxG = Math.max(0, ...Object.values(gen));
  const known = Object.keys(P).filter(k => P[k].n);
  const dead = known.filter(k => P[k].dy && P[k].by);

  // Research frontier
  const front = [];
  for (let g = 1; g <= maxG; g++) { const n = anc.filter(k => gen[k] === g).length; front.push({g, n, of: 2 ** g}); }
  const frontRows = front.map(f => ({label: f.g === 1 ? "Parents" : f.g === 2 ? "Grandparents" : f.g === 3 ? "Great-grandparents" : `${ord(f.g - 2)} great-grandparents`,
    value: f.n / f.of * 100, text: `${f.n} of ${f.of.toLocaleString()}`, tip: `Generation ${f.g}: ${f.n} found of ${f.of.toLocaleString()} possible (${(f.n / f.of * 100).toFixed(f.n / f.of < .01 ? 2 : 0)}%)`}));

  // Origins by paper trail: each branch's share goes to the birth country of its earliest known ancestor.
  // When that ancestor's birthplace is not recorded, the branch goes to the nearest descendant on it whose is.
  const share = {};
  const walk = (k, w, depth, near) => {
    const own = birthCountry(k), c = own !== "--c-un" ? own : near;
    const ps = parentsOf(k);
    if (!ps.length || depth > 40) { share[c] = (share[c] || 0) + w; return; }
    ps.forEach(x => walk(x, w / 2, depth + 1, c));
    if (ps.length === 1) share[c] = (share[c] || 0) + w / 2;
  };
  walk(root, 1, 0, "--c-un");
  const orig = COUNTRIES.map(([v, t]) => ({v, t, f: share[v] || 0})).filter(o => o.f > 0.0005);
  let ox = 0;
  const CW = Math.max(250, Math.min(640, $("#view").clientWidth - 78)), CW2 = Math.min(420, CW), OW = CW;
  const origBar = `<svg viewBox="0 0 ${OW} 54" width="${OW}" height="54" role="img">${orig.map(o => {
    const w = o.f * OW, x = ox; ox += w;
    return `<g class="hov" data-tip="${esc(o.t)}: ${(o.f * 100).toFixed(1)}%"><rect x="${x + 1}" y="0" width="${Math.max(0, w - 2)}" height="30" rx="3" fill="var(${o.v})"></rect>
      ${w > 44 ? `<text class="val" x="${x + w / 2}" y="48" text-anchor="middle">${Math.round(o.f * 100)}%</text>` : ""}</g>`;
  }).join("")}</svg>`;

  // Lifespans by century of birth
  const byC = {};
  dead.forEach(k => { const a = P[k].dy - P[k].by; if (a >= 0 && a < 110) (byC[centuryOf(P[k].by)] ||= []).push(a); });
  const lifeRows = Object.keys(byC).map(Number).sort().filter(c => byC[c].length >= 5).map(c => ({label: centuryName(c), value: median(byC[c]),
    text: `${Math.round(median(byC[c]))}`, tip: `Born in the ${centuryName(c)}: ${byC[c].length} people, median age at death ${Math.round(median(byC[c]))}, youngest ${Math.min(...byC[c])}, oldest ${Math.max(...byC[c])}`, n: byC[c].length}));

  // Children per family, by century of the parents' births
  const kidsC = {};
  Object.values(F).forEach(f => {
    const ys = [f.h, f.w].map(x => P[x]?.by).filter(Boolean); if (!ys.length || !(f.c || []).length) return;
    (kidsC[centuryOf(Math.min(...ys))] ||= []).push(f.c.length);
  });
  const kidRows = Object.keys(kidsC).map(Number).sort().filter(c => kidsC[c].length >= 4).map(c => {
    const a = kidsC[c], m = a.reduce((x, y) => x + y, 0) / a.length;
    return {label: centuryName(c), value: m, text: m.toFixed(1), tip: `Parents born in the ${centuryName(c)}: ${a.length} families, ${m.toFixed(1)} children on average, most ${Math.max(...a)}`, n: a.length};
  });

  // Age at first marriage, by century of birth
  const mar = {};
  known.forEach(k => {
    const p = P[k]; if (!p.by) return;
    const ys = (p.fams || []).map(f => (F[f].ev || []).find(e => /Marri/i.test(e.t) && e.y)).filter(Boolean).map(e => e.y);
    if (!ys.length) return;
    const a = Math.floor(Math.min(...ys) - p.by); if (a < 13 || a > 70) return;
    (mar[centuryOf(p.by)] ||= {all: [], M: [], F: []}); mar[centuryOf(p.by)].all.push(a); (mar[centuryOf(p.by)][p.sx] || []).push(a);
  });
  const marRows = Object.keys(mar).map(Number).sort().filter(c => mar[c].all.length >= 5).map(c => {
    const m = mar[c], md = x => x.length ? Math.round(median(x)) : "?";
    return {label: centuryName(c), value: median(m.all), text: `${Math.round(median(m.all))}`, tip: `Born in the ${centuryName(c)}: ${m.all.length} people; median ${md(m.all)} (men ${md(m.M)}, women ${md(m.F)})`, n: m.all.length, men: md(m.M), women: md(m.F)};
  });

  // Most common first names
  const counts = {};
  known.forEach(k => { const g = givenNames(k)[0]; if (g) counts[g] = (counts[g] || 0) + 1; });
  const nameRows = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 15).map(([n, c]) => ({label: n, value: c, text: String(c), tip: `${n}: ${c} people (spellings such as Johann, Jean and John counted together)`}));

  // Repeated names along the direct line: a parent and the child on the root person's line who share a given name.
  const onLine = k => k === root || k in gen;
  const lineChild = k => childrenOf(k).find(onLine);
  const chains = [];
  anc.forEach(k => {
    givenNames(k).forEach(n => {
      if (parentsOf(k).some(p => onLine(p) && givenNames(p).includes(n))) return;   // not the start of a chain
      const ch = [k]; let c = lineChild(k);
      while (c && P[c]?.n && givenNames(c).includes(n)) { ch.push(c); c = lineChild(c); }
      if (ch.length >= 2) chains.push({n, ch});
    });
  });
  chains.sort((a, b) => b.ch.length - a.ch.length || (P[a.ch[0]].by || 0) - (P[b.ch[0]].by || 0));
  const seen = new Set(), chainsU = chains.filter(c => { const key = c.n + c.ch.join(); if (seen.has(key)) return false; seen.add(key); return true; });

  const sec = (title, cap, body, tbl) => `<section class="chart"><h2>${title}</h2><p class="cap">${cap}</p>${body}${tbl || ""}</section>`;
  $("#view").innerHTML = `<div class="numbers"><h1>The family in numbers</h1>
    <p class="sub">Worked out from everyone in the tree who has died. Living relatives are left out, and small groups (under five people) are not shown.</p>
    <div class="tiles">
      <div class="tile"><b>${anc.length}</b><span>direct ancestors found</span></div>
      <div class="tile"><b>${maxG}</b><span>generations back, at the furthest</span></div>
      <div class="tile"><b>${known.length}</b><span>people in the tree</span></div>
      <div class="tile"><b>${Object.keys(IMG).length}</b><span>original records to look at</span></div>
      <div class="tile"><b>${Object.keys(PL).length}</b><span>places on the map</span></div>
    </div>
    ${sec("How far back we have got", "Ancestors found in each generation, out of the number every person has (2 parents, 4 grandparents, 8 great-grandparents and so on).", hbars(frontRows, {max: 100, labelW: 215, W: CW}),
      table(["Generation", "Found", "Possible"], front.map((f, i) => [frontRows[i].label, f.n, f.of])))}
    ${sec("Where the family came from", `Each branch of the tree counted back to its earliest known ancestor and that person's country of birth (or, where it is not recorded, the birth country of the nearest descendant on the branch whose birthplace is), as shares of ${esc(ROOTNAME)}'s ancestry.${D.site?.origins_note ? " " + esc(D.site.origins_note) : ""}`, origBar +
      `<div class="legendrow">${orig.map(o => `<span><i style="background:var(${o.v})"></i>${o.t} ${(o.f * 100).toFixed(1)}%</span>`).join("")}</div>`,
      table(["Country", "Share"], orig.map(o => [o.t, (o.f * 100).toFixed(1) + "%"])))}
    <div class="cols2">
      ${sec("How long they lived", "Median age at death, by century of birth. Children who died young are included, which pulls the earlier centuries down.", vbars(lifeRows, {W: CW2}),
        table(["Born in", "People", "Median age at death"], lifeRows.map(r => [r.label, r.n, Math.round(r.value)])))}
      ${sec("Children per family", "Average number of children recorded in the tree, by century the parents were born. Records of babies who died soon after birth are often missing, so these are low.", vbars(kidRows, {W: CW2}),
        table(["Parents born in", "Families", "Children on average"], kidRows.map(r => [r.label, r.n, r.value.toFixed(1)])))}
    </div>
    <div class="cols2">
      ${sec("Age at first marriage", "Median age at a first recorded marriage, by century of birth. Tap or point at a bar for men and women separately.", vbars(marRows, {W: CW2}),
        table(["Born in", "People", "Median", "Men", "Women"], marRows.map(r => [r.label, r.n, Math.round(r.value), r.men, r.women])))}
      ${sec("The most common first names", "First given names of everyone in the tree, with spellings in other languages counted together (Johann, Jean and John are all John).", hbars(nameRows, {labelW: 100, W: CW2}),
        table(["Name", "People"], nameRows.map(r => [r.label, r.value])))}
    </div>
    ${sec("Names handed down", `Where a child on ${esc(ROOTNAME)}'s direct line carries a parent's given name, generation after generation. Spellings in other languages count as the same name (Louis and Lewis, Johann and John).`,
      `<ul class="chains">${chainsU.slice(0, 14).map(c => `<li><span class="nm">${esc(c.n)}, ${c.ch.length} generations:</span> ${c.ch.map(k => `<a href="#/p/${k}">${esc(P[k].n)}</a> (${P[k].by || "?"})`).join(" &rarr; ")}</li>`).join("") || "<li>None found.</li>"}</ul>`)}
  </div>`;
  document.querySelectorAll(".numbers .hov").forEach(g => {
    g.addEventListener("pointerenter", ev => g.dataset.tip && tip(ev, esc(g.dataset.tip)));
    g.addEventListener("pointermove", ev => g.dataset.tip && tip(ev, esc(g.dataset.tip)));
    g.addEventListener("pointerleave", () => tip());
  });
}

// ---------- records ----------
const REC = {group: "", q: "", n: 24};
const REC_GROUPS = ["Births and baptisms", "Marriages", "Deaths and burials", "Censuses", "Arrivals and citizenship", "Military", "Other records"];
function showRecords() {
  current = "records"; stop(); REC.n = 24;
  const all = Object.keys(IMG).sort((a, b) => (IMG[a].yr || 9999) - (IMG[b].yr || 9999));
  const count = g => all.filter(k => (IMG[k].group || "Other records") === g).length;
  $("#view").innerHTML = `<div class="records"><h1>Records</h1><p class="sub">${all.length} images of original records, oldest first. Tap one to look at it closely.</p>
    <div class="chips" role="group" aria-label="Kind of record"><button type="button" data-g="">All (${all.length})</button>${REC_GROUPS.filter(count).map(g => `<button type="button" data-g="${g}">${g} (${count(g)})</button>`).join("")}</div>
    <input id="rq" type="search" placeholder="Search by name, place or year" aria-label="Search the records" value="${esc(REC.q)}">
    <p class="hint" id="rcount" role="status"></p>
    <div class="grid" id="rgrid"></div><div class="morerow"><button class="btn ghost" id="rmore" type="button">Show more records</button></div></div>`;
  const draw = () => {
    const t = fold(REC.q).split(/\s+/).filter(Boolean);
    const list = all.filter(k => (!REC.group || (IMG[k].group || "Other records") === REC.group)
      && (!t.length || t.every(w => fold(IMG[k].title + " " + IMG[k].cite + " " + IMG[k].people.map(nameOf).join(" ")).includes(w))));
    $("#rgrid").innerHTML = list.slice(0, REC.n).map(recCard).join("");
    $("#rcount").textContent = list.length === all.length ? "" : list.length ? `${list.length} record${list.length === 1 ? "" : "s"} found.` : "No records found. Try a surname or a year.";
    $("#rmore").hidden = list.length <= REC.n;
    document.querySelectorAll(".chips button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.g === REC.group)));
  };
  draw();
  $("#rq").addEventListener("input", e => { REC.q = e.target.value; REC.n = 24; draw(); });
  $(".chips").addEventListener("click", e => { const b = e.target.closest("button"); if (b) { REC.group = b.dataset.g; REC.n = 24; draw(); } });
  $("#rmore").addEventListener("click", () => { REC.n += 48; draw(); });
}

// ---------- record viewer: opening a record adds a history entry, so the Back button closes it ----------
const LB = {key: null, z: 1, fitW: 0, opener: null};
function openImage(key) {
  const im = IMG[key], lb = $("#lightbox"); if (!im) return;
  LB.key = key; LB.z = 1; LB.opener = document.activeElement;
  const title = im.title || im.cite.split(",").slice(0, 2).join(","), who = im.people.filter(known);
  lb.innerHTML = `<div class="lbimg"><div class="imgbox"><img src="${esc(im.src)}" alt="${esc(title)}"></div>
      <div class="lbtools"><button type="button" data-z="out" aria-label="Make the image smaller">&minus;</button><button type="button" data-z="fit">Fit</button><button type="button" data-z="in" aria-label="Make the image larger">+</button></div></div>
    <div class="side"><button class="btn ghost lbclose" type="button">Close</button><h2>${esc(title)}</h2>
      ${im.tr ? `<p class="trans">${esc(im.tr)}</p><p class="hint">A translation from the research notes.</p>` : ""}
      ${who.length ? `<p class="who">In this record: ${who.map(x => `<a href="#/p/${x}">${esc(P[x].n)}</a>`).join(", ")}</p>` : ""}
      <details><summary>Where this record comes from</summary><p class="src">${citeHtml(im.cite).replace(/<button[^>]*>|<\/button>/g, "")}</p></details>
      <p class="hint">Use + and − to change the size of the image, or tap it.</p></div>`;
  lb.setAttribute("aria-label", title);
  lb.classList.add("on");
  lbFit();
  $(".imgbox img", lb).addEventListener("click", () => { LB.z = LB.z > 1 ? 1 : 2.5; lbApply(); });
  $(".lbtools", lb).addEventListener("click", e => { const z = e.target.dataset.z; if (!z) return; LB.z = z === "fit" ? 1 : Math.max(1, Math.min(8, LB.z * (z === "in" ? 1.5 : 1 / 1.5))); lbApply(); });
  $(".lbclose", lb).addEventListener("click", closeLightbox);
  // A link to a person replaces the viewer's history entry, so Back does not return to a closed viewer.
  lb.querySelectorAll(".who a").forEach(a => a.addEventListener("click", e => { e.preventDefault(); hideLightbox(); location.replace(a.href); }));
  if (!(history.state && history.state.lightbox)) history.pushState({lightbox: 1}, "");
  $(".lbclose", lb).focus();
}
function lbFit() {
  const box = $("#lightbox .imgbox"), im = IMG[LB.key]; if (!box || !im) return;
  // Fit the box; a small crop is enlarged, up to three times, so it can be read.
  LB.fitW = im.w * Math.min((box.clientWidth - 24) / im.w, (box.clientHeight - 24) / im.h, 3);
  lbApply();
}
function lbApply() { const img = $("#lightbox .imgbox img"); if (img) img.style.width = Math.round(LB.fitW * LB.z) + "px"; }
function closeLightbox() { if (history.state && history.state.lightbox) history.back(); else hideLightbox(); }
function hideLightbox() {
  const lb = $("#lightbox"); if (!lb.classList.contains("on")) return;
  lb.classList.remove("on"); lb.innerHTML = ""; LB.key = null;
  if (LB.opener && document.contains(LB.opener)) LB.opener.focus({preventScroll: true});
  LB.opener = null;
}
addEventListener("keydown", e => {
  const lb = $("#lightbox"); if (!lb.classList.contains("on")) return;
  if (e.key === "Escape") return closeLightbox();
  if (e.key === "Tab") {   // keep the keyboard inside the viewer while it is open
    const f = [...lb.querySelectorAll("button, a[href], summary")], i = f.indexOf(document.activeElement);
    e.preventDefault(); f[(i + (e.shiftKey ? -1 : 1) + f.length) % f.length].focus();
  }
});

// ---------- home: a welcome, a search, the reader's own family, the stories and the other pages ----------
const MONTHS_FULL = "January February March April May June July August September October November December".split(" ");
// Events with an exact date that fall today, or on the next day within a week that has any. Living people are
// left out, and so are dates the research notes grade as a lead, a possibility or a conflict.
function onThisDay() {
  const MON = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");
  const weak = e => /lead|possible|not recorded|conflict/i.test((e.ev || "").slice(0, 70));
  const verb = {Born: "was born", Died: "died", Christened: "was baptized", Baptized: "was baptized", Buried: "was buried"};
  const all = [];
  const add = (e, text, k) => { const m = /^(\d{1,2}) ([A-Z][a-z]{2}) (\d{4})$/.exec(e.d || ""); if (m && !weak(e)) all.push({day: +m[1], mon: MON.indexOf(m[2]), y: +m[3], text, k, pl: e.pl}); };
  for (const k in P) { const p = P[k]; if (!p.n || p.lv) continue; (p.ev || []).forEach(e => verb[e.t] && add(e, `${p.n} ${verb[e.t]}`, k)); }
  for (const f in F) {
    const fam = F[f]; if (!known(fam.h) || !known(fam.w) || P[fam.h].lv || P[fam.w].lv) continue;
    (fam.ev || []).forEach(e => e.t === "Married" && add(e, `${P[fam.h].n} and ${P[fam.w].n} were married`, fam.h));
  }
  const now = new Date();
  for (let off = 0; off < 7; off++) {
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + off);
    const hits = all.filter(x => x.mon === d.getMonth() && x.day === d.getDate()).sort((a, b) => a.y - b.y);
    if (hits.length) return {off, d, hits: hits.slice(0, 6), year: d.getFullYear()};
  }
  return null;
}
function surprise() {
  const mine = Object.keys(upMapC(ME.anchor)).filter(k => k !== ME.anchor && known(k) && !P[k].lv);
  const rich = mine.filter(k => (P[k].story || []).length || (P[k].img || []).length);
  const pool = rich.length > 5 ? rich : mine.length ? mine : Object.keys(P).filter(known);
  location.hash = "#/p/" + pool[Math.floor(Math.random() * pool.length)];
}
function showHome() {
  current = "home"; stop();
  const me = ME.anchor, linked = ME.self || me !== D.root;
  const mine = upMapC(me), gens = Math.max(0, ...Object.values(mine));
  const people = Object.keys(P).filter(known).length, stories = D.stories || [];
  const close = [me, ...parentsOf(me), ...parentsOf(me).flatMap(parentsOf)].filter(known);
  const big = k => `<a class="card" href="#/p/${k}"><div class="k">${esc(relLabel(k))}</div><div class="nm">${esc(P[k].n)}</div><div class="yr">${esc(yearsOf(k))}</div></a>`;
  const lines = LINES.slice(0, 8).map(l => `<a class="tile2" href="#/line/${encodeURIComponent(l.sn)}"><b>${esc(l.sn)}</b><span>${l.gens} generations, from ${esc(yearsOf(l.founder).split(" ")[0] === "about" ? "about " + (P[l.founder].est || "") : String(P[l.founder].by || ""))}</span></a>`).join("");
  const day = onThisDay();
  const hello = ME.name ? `Welcome, ${esc(ME.name.split(" ")[0])}` : "Welcome";
  $("#view").innerHTML = `<div class="home">
    <section class="hero" style="margin-top:0">
      <h1>${hello}</h1>
      <p class="lead">This is our family's history: ${people} people over ${gens} generations, with ${stories.length} stories and ${Object.keys(IMG).length} original records. Find someone you know, read a story, or follow a family across the map.</p>
      <div class="search bigsearch"><label for="hq">Find someone in the family</label>
        <input id="hq" type="search" autocomplete="off" placeholder="Type a name, for example ${esc((P[parentsOf(me)[0]] || {}).sn || (LINES[0] || {}).sn || "Smith")}"><div class="qres" id="hqres" role="listbox"></div></div>
    </section>
    ${close.length >= 2 ? `<section><h2>${linked ? "Start with you" : `Start with ${esc(ROOTNAME)}'s family`}</h2><p class="sub">Tap a name to read about that person. Each page leads on to their parents, and theirs.</p><div class="cards big">${close.map(big).join("")}</div></section>`
      : `<section><h2>The family lines</h2><p class="sub">Each surname, followed back as far as the records go.</p><div class="tilegrid">${lines}</div></section>`}
    ${stories.length ? `<section><h2>Read a story</h2><p class="sub">Each chapter tells how one branch of the family came to be where it is.</p>
      <div class="storylist">${stories.slice(0, 3).map(st => `<a class="storycard" href="#/story/${esc(st.slug)}"><div class="k">${esc(st.line ? "The " + st.line + " line" : "")}</div><h3>${esc(st.title)}</h3><p>${esc(st.subtitle)}</p></a>`).join("")}</div>
      ${stories.length > 3 ? `<a class="more" href="#/stories">All ${stories.length} stories &rsaquo;</a>` : ""}</section>` : ""}
    ${day ? `<section><h2>${day.off === 0 ? "On this day in the family" : `Coming up on ${day.d.getDate()} ${MONTHS_FULL[day.d.getMonth()]}`}</h2>
      <ul class="onthisday">${day.hits.map(x => `<li><span class="yr">${x.y}</span><a href="#/p/${x.k}">${esc(x.text)}</a>, ${day.year - x.y} years ago${x.pl ? `<div class="where">${esc(x.pl)}</div>` : "."}</li>`).join("")}</ul></section>` : ""}
    <section><h2>More to explore</h2><div class="tilegrid">
      <a class="tile2" href="#/tree"><b>Family tree</b><span>Everyone you descend from, in one picture.</span></a>
      <a class="tile2" href="#/timeline"><b>Timeline</b><span>Every ancestor's life, century by century, by family line.</span></a>
      <a class="tile2" href="#/map"><b>Map</b><span>Follow each family from Europe to America, year by year.</span></a>
      <a class="tile2" href="#/related"><b>How are we related?</b><span>Pick any two people and see the link between them.</span></a>
      <a class="tile2" href="#/service"><b>Military service</b><span>${(D.service || []).length} relatives' service, stop by stop on a map.</span></a>
      <a class="tile2" href="#/records"><b>Records</b><span>${Object.keys(IMG).length} original documents you can look at closely.</span></a>
      <a class="tile2" href="#/numbers"><b>Numbers</b><span>How long they lived, the names handed down, and more.</span></a>
    </div>
    <p style="margin-top:16px"><button class="btn" type="button" data-surprise>Surprise me: show me an ancestor</button></p></section>
    ${ME.email ? `<section class="askbox"><h2>Do you have a photo or a memory?</h2><p>Old photographs, letters and the stories you were told are the parts of this history no archive holds. Open any person's page and press "Add a memory or photo". It goes to ${esc(OWNER)}, who will add it with your name.</p></section>` : ""}
  </div>`;
  attachSearch($("#hq"), $("#hqres"), k => { location.hash = "#/p/" + k; });
}

// ---------- how are we related: the shortest path between two people through a shared ancestor ----------
function upPrev(k) {
  const d = {[k]: 0}, prev = {}, q = [k];
  while (q.length) { const x = q.shift(); for (const p of parentsOf(x)) if (!(p in d)) { d[p] = d[x] + 1; prev[p] = x; q.push(p); } }
  return {d, prev};
}
function kinPath(a, b) {
  const A = upPrev(a), B = upPrev(b);
  let top = null;
  for (const x in A.d) if (x in B.d && (!top || A.d[x] + B.d[x] < A.d[top] + B.d[top])) top = x;
  if (!top) return null;
  const down = (U, from) => { const out = []; let x = top; while (x !== from) { x = U.prev[x]; out.push(x); } return out; };
  const mate = spousesOf(top).find(m => m in A.d && m in B.d && A.d[m] === A.d[top] && B.d[m] === B.d[top]) || null;
  return {top, mate, left: down(A, a), right: down(B, b)};
}
function showRelated(a, b) {
  current = "related"; stop();
  a = a || (known(ME.anchor) ? ME.anchor : null);
  const whoA = a && known(a) ? P[a].n : "", whoB = b && known(b) ? P[b].n : "";
  const card = k => known(k) ? `<a class="card" href="#/p/${k}"><div class="nm">${esc(P[k].n)}</div><div class="yr">${esc(yearsOf(k))}</div></a>` : `<div class="card lv"><div class="nm">Living relative</div></div>`;
  const stem = '<div class="relstem"></div>';
  let out = `<p class="sub">Choose two people above to see how they are related.</p>`;
  if (a && b && a === b) out = `<p class="relsay">That is the same person twice. Choose someone else.</p>`;
  else if (a && b) {
    let path = kinPath(a, b), via = "";
    // No shared ancestor: try through a husband or wife on either side.
    if (!path) for (const m of spousesOf(b)) { const p2 = kinPath(a, m); if (p2) { path = p2; via = `${nameOf(b)} married ${nameOf(m)}.`; break; } }
    if (!path) for (const m of spousesOf(a)) { const p2 = kinPath(m, b); if (p2) { path = p2; via = `${nameOf(a)} married ${nameOf(m)}.`; break; } }
    const r = relation(a, b);
    const mine = a === ME.anchor && (ME.self || ME.anchor !== D.root);
    const say = r && r !== "you" ? `${esc(nameOf(b))} is ${mine ? "your" : esc(nameOf(a)) + "'s"} ${esc(r.replace(" of your ", mine ? " of your " : " of their "))}.` : "";
    if (!path) {
      // Not related by blood: do the two lines meet later, in someone descended from both?
      const dn = k => { const d = {[k]: 0}, q = [k]; while (q.length) { const x = q.shift(); for (const c of childrenOf(x)) if (!(c in d)) { d[c] = d[x] + 1; q.push(c); } } return d; };
      const da = dn(a), db = dn(b);
      let meet = null;
      for (const x in da) if (x in db && (!meet || da[x] + db[x] < da[meet] + db[meet])) meet = x;
      out = meet
        ? `<p class="relsay">${esc(nameOf(a))} and ${esc(nameOf(b))} are not related to each other by blood. Their two families come together in ${known(meet) ? esc(nameOf(meet)) : "a living relative"}, who descends from both.</p><div class="reltop">${card(meet)}</div>`
        : `<p class="relsay">The tree does not yet show a link between ${esc(nameOf(a))} and ${esc(nameOf(b))}.</p>`;
    }
    else {
      const col = ks => ks.map(k => stem + card(k)).join("");
      const one = !path.left.length || !path.right.length;
      out = `${say ? `<p class="relsay">${say}</p>` : ""}${via ? `<p class="sub">${esc(via)} The chart shows the link by blood.</p>` : ""}
        <p class="hint">${one ? "Read down the chart, from parent to child." : "Both lines come down from the same " + (path.mate ? "couple" : "ancestor") + " at the top. Read each side from parent to child."}</p>
        <div class="reltop">${card(path.top)}${path.mate ? card(path.mate) : ""}</div>
        <div class="relcols${one ? " one" : ""}">${path.left.length ? `<div>${col(path.left)}</div>` : ""}${path.right.length ? `<div>${col(path.right)}</div>` : ""}</div>`;
    }
  }
  $("#view").innerHTML = `<div class="related"><h1>How are we related?</h1>
    <p class="sub">Pick any two people in the family to see the link between them.</p>
    <div class="pickers">
      <div class="search"><label for="ra">First person</label><input id="ra" type="search" autocomplete="off" placeholder="${esc(whoA || "Type a name")}"><div class="qres" id="rares" role="listbox"></div>${whoA ? `<p class="hint">Now: ${esc(whoA)}</p>` : ""}</div>
      <div class="search"><label for="rb">Second person</label><input id="rb" type="search" autocomplete="off" placeholder="${esc(whoB || "Type a name")}"><div class="qres" id="rbres" role="listbox"></div>${whoB ? `<p class="hint">Now: ${esc(whoB)}</p>` : ""}</div>
    </div>
    <div id="relout">${out}</div></div>`;
  attachSearch($("#ra"), $("#rares"), k => { location.hash = `#/related/${k}/${b || ""}`; });
  attachSearch($("#rb"), $("#rbres"), k => { location.hash = `#/related/${a || k}/${a ? k : ""}`; });
}

addEventListener("DOMContentLoaded", () => { if (window.L) boot(); else addEventListener("load", boot); });
