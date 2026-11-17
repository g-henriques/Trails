/* Trilhos de Portugal — single page app (no build step). */
"use strict";

const DIFFICULTIES = ["Fácil", "Médio", "Algo difícil", "Difícil", "Muito difícil"];
const DIFF_COLOR = {
  "Fácil": "var(--d1)", "Médio": "var(--d2)", "Algo difícil": "var(--d3)",
  "Difícil": "var(--d4)", "Muito difícil": "var(--d5)", "": "var(--d0)",
};
const DIFF_HEX = { "Fácil": "#2e9e5b", "Médio": "#d39b12", "Algo difícil": "#e46f24", "Difícil": "#cf3a3a", "Muito difícil": "#7b3fa0", "": "#7d8a83" };
const PAGE = 150;

const state = { trails: [], districts: [], filtered: [], shown: PAGE, byId: new Map() };
const $ = (sel, root = document) => root.querySelector(sel);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fold(s) {
  return String(s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}
function fmtKm(km) {
  return km == null ? "—" : `${km.toLocaleString("pt-PT", { maximumFractionDigits: 1 })} km`;
}
function fmtDuration(min) {
  if (!min) return "—";
  const h = Math.floor(min / 60), m = min % 60;
  return h ? `${h} h${m ? ` ${String(m).padStart(2, "0")}` : ""}` : `${m} min`;
}
function approx(flag) { return flag ? "≈ " : ""; }
function fmtDate(iso) {
  if (!iso) return "nunca";
  return new Date(iso).toLocaleString("pt-PT", { dateStyle: "short", timeStyle: "short" });
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok && r.status !== 409) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

/* ---------------------------------------------------------------- tiles */

function baseLayers() {
  const osm = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  });
  const topo = L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
    maxZoom: 17, attribution: 'Mapa: &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA), dados &copy; OpenStreetMap',
  });
  const sat = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
    maxZoom: 19, attribution: "Imagens &copy; Esri",
  });
  return { "Mapa": osm, "Topográfico": topo, "Satélite": sat };
}
function makeMap(el, opts = {}) {
  const layers = baseLayers();
  const map = L.map(el, { zoomControl: true, ...opts }).setView([39.6, -8.0], 7);
  layers["Mapa"].addTo(map);
  L.control.layers(layers, null, { position: "topright" }).addTo(map);
  L.control.scale({ imperial: false }).addTo(map);
  return map;
}

/* ---------------------------------------------------------------- main map + list */

let mainMap, cluster, markers = new Map();

function initMainMap() {
  mainMap = makeMap("map");
  cluster = L.markerClusterGroup({ showCoverageOnHover: false, maxClusterRadius: 45, chunkedLoading: true });
  mainMap.addLayer(cluster);
  $("#legend").innerHTML = DIFFICULTIES.map((d) => `<div><span class="dot" style="background:${DIFF_COLOR[d]}"></span>${d}</div>`).join("");
}

function popupHtml(t) {
  return `<div class="popup"><span class="name">${esc(t.ref)} · ${esc(t.name)}</span>
    <span class="meta">${approx(t.distance_source === "gpx")}${fmtKm(t.distance_km)} · ${approx(t.difficulty_estimated)}${esc(t.difficulty || "—")} · ${esc(t.municipality || t.district)}</span><br>
    <a class="btn primary" href="#/trilho/${encodeURIComponent(t.id)}">Ver trilho</a></div>`;
}

function renderMarkers() {
  cluster.clearLayers();
  markers.clear();
  const layers = [];
  for (const t of state.filtered) {
    if (t.lat == null) continue;
    const m = L.circleMarker([t.lat, t.lon], {
      radius: 7, weight: 2, color: "#fff", fillColor: DIFF_HEX[t.difficulty] || DIFF_HEX[""], fillOpacity: 0.95,
    }).bindPopup(() => popupHtml(t));
    markers.set(t.id, m);
    layers.push(m);
  }
  cluster.addLayers(layers);
}

function readFilters() {
  const f = new FormData($("#filters"));
  return {
    q: fold(f.get("q")).trim(), d: f.get("d") || "", k: f.get("k") || "", km: f.get("km") || "",
    t: f.get("t") || "", gpx: f.get("gpx") === "1",
    diff: [...document.querySelectorAll("#difficulty-chips .chip[aria-pressed=true]")].map((c) => c.dataset.value),
  };
}

function applyFilters({ fit = false } = {}) {
  const f = readFilters();
  const [kmMin, kmMax] = f.km ? f.km.split("-").map((x) => (x === "" ? null : Number(x))) : [null, null];
  state.filtered = state.trails.filter((t) => {
    if (f.d && t.district !== f.d) return false;
    if (f.k && !(f.k === "PR" ? ["PR", "PRC", "PRL"].includes(t.kind) : t.kind === f.k)) return false;
    if (f.t && t.route_type !== f.t) return false;
    if (f.gpx && !t.has_track) return false;
    if (f.diff.length && !f.diff.includes(t.difficulty)) return false;
    if (kmMin != null && !(t.distance_km >= kmMin)) return false;
    if (kmMax != null && !(t.distance_km < kmMax)) return false;
    if (f.q && !t._search.includes(f.q)) return false;
    return true;
  });
  state.shown = PAGE;
  renderList();
  renderMarkers();
  saveFiltersToHash(f);
  if (fit) fitToFiltered(f);
}

function fitToFiltered(f) {
  const pts = state.filtered.filter((t) => t.lat != null).map((t) => [t.lat, t.lon]);
  if (pts.length) {
    mainMap.fitBounds(L.latLngBounds(pts).pad(0.08), { maxZoom: 13 });
  } else if (f.d) {
    const d = state.districts.find((x) => x.name === f.d);
    if (d) mainMap.setView([d.lat, d.lon], 9);
  }
}

function renderList() {
  const list = $("#list");
  const total = state.filtered.length;
  const withGpx = state.filtered.filter((t) => t.has_track).length;
  $("#count").textContent = `${total.toLocaleString("pt-PT")} trilhos · ${withGpx.toLocaleString("pt-PT")} com GPX`;
  if (!total) {
    list.innerHTML = state.trails.length
      ? `<li class="empty">Nenhum trilho corresponde aos filtros.</li>`
      : `<li class="empty">Ainda não há trilhos. A primeira recolha de dados começa automaticamente ao arrancar e demora alguns minutos.</li>`;
    return;
  }
  const items = state.filtered.slice(0, state.shown).map((t) => `
    <li><a href="#/trilho/${encodeURIComponent(t.id)}" data-id="${esc(t.id)}">
      <span class="dot" style="background:${DIFF_COLOR[t.difficulty] || DIFF_COLOR[""]}"></span>
      <span class="name"><span class="ref">${esc(t.ref)}</span>${esc(t.name)}</span>
      <span class="meta">
        <span>${esc(t.municipality ? `${t.municipality} · ${t.district}` : t.district || "Distrito por apurar")}</span>
        <span>${approx(t.distance_source === "gpx")}${fmtKm(t.distance_km)}</span>
        <span>${approx(t.difficulty_estimated)}${esc(t.difficulty || "—")}</span>
        <span>${approx(t.duration_estimated)}${fmtDuration(t.duration_min)}</span>
        ${t.has_track ? "" : '<span class="tag nogpx">sem GPX</span>'}
        ${t.closed ? '<span class="tag closed">encerrado</span>' : ""}
      </span></a></li>`);
  if (total > state.shown) {
    items.push(`<li class="more"><button type="button" id="more">Mostrar mais ${Math.min(PAGE, total - state.shown)} de ${total - state.shown}</button></li>`);
  }
  list.innerHTML = items.join("");
}

function saveFiltersToHash(f) {
  const p = new URLSearchParams();
  const raw = new FormData($("#filters"));
  for (const k of ["q", "d", "k", "km", "t", "gpx"]) if (raw.get(k)) p.set(k, raw.get(k));
  if (f.diff.length) p.set("dif", f.diff.join(","));
  const hash = `#/${p.toString() ? `?${p}` : ""}`;
  if (location.hash !== hash && (location.hash.startsWith("#/?") || location.hash === "" || location.hash === "#/")) {
    history.replaceState(null, "", hash);
  }
}

function loadFiltersFromHash(params) {
  const form = $("#filters");
  for (const k of ["q", "d", "k", "km", "t"]) form.elements[k].value = params.get(k) || "";
  form.elements.gpx.checked = params.get("gpx") === "1";
  const dif = (params.get("dif") || "").split(",").filter(Boolean);
  document.querySelectorAll("#difficulty-chips .chip").forEach((c) => c.setAttribute("aria-pressed", String(dif.includes(c.dataset.value))));
}

function initFilters() {
  $("#difficulty-chips").innerHTML = DIFFICULTIES.map((d) =>
    `<button type="button" class="chip" data-value="${d}" aria-pressed="false"><span class="dot" style="background:${DIFF_COLOR[d]}"></span>${d}</button>`).join("");
  const form = $("#filters");
  let timer;
  form.addEventListener("input", (e) => {
    clearTimeout(timer);
    timer = setTimeout(() => applyFilters({ fit: e.target.name === "d" }), e.target.name === "q" ? 200 : 0);
  });
  form.addEventListener("submit", (e) => e.preventDefault());
  $("#difficulty-chips").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    chip.setAttribute("aria-pressed", String(chip.getAttribute("aria-pressed") !== "true"));
    applyFilters();
  });
  $("#reset").addEventListener("click", () => {
    loadFiltersFromHash(new URLSearchParams());
    applyFilters();
    mainMap.setView([39.6, -8.0], 7);
  });
  $("#list").addEventListener("click", (e) => {
    if (e.target.id === "more") { state.shown += PAGE; renderList(); }
  });
  $("#list").addEventListener("mouseover", (e) => {
    const a = e.target.closest("a[data-id]");
    const m = a && markers.get(a.dataset.id);
    if (m && cluster.hasLayer(m)) {
      const visible = cluster.getVisibleParent(m);
      if (visible === m) m.setStyle({ radius: 11 });
      a.addEventListener("mouseleave", () => m.setStyle({ radius: 7 }), { once: true });
    }
  });
}

/* ---------------------------------------------------------------- districts view */

let districtMap, districtLayer;

function renderDistricts() {
  const grid = $("#district-grid");
  const byDistrict = new Map(state.districts.map((d) => [d.name, { ...d, diff: {} }]));
  for (const t of state.trails) {
    const d = byDistrict.get(t.district);
    if (d) d.diff[t.difficulty || ""] = (d.diff[t.difficulty || ""] || 0) + 1;
  }
  grid.innerHTML = [...byDistrict.values()].map((d) => {
    const bar = [...DIFFICULTIES, ""].map((k) => d.diff[k]
      ? `<span style="width:${(100 * d.diff[k]) / d.count}%;background:${DIFF_COLOR[k]}" title="${k || "Sem dados"}: ${d.diff[k]}"></span>` : "").join("");
    return `<a class="district-card" href="#/?d=${encodeURIComponent(d.name)}">
      <h3>${esc(d.name)}</h3>
      <div class="nums"><span><b>${d.count}</b>trilhos</span><span><b>${d.with_track}</b>com GPX</span><span><b>${d.km.toLocaleString("pt-PT")}</b>km</span></div>
      <div class="bar">${bar}</div></a>`;
  }).join("");

  if (!districtMap) {
    districtMap = makeMap("district-map");
    districtLayer = L.layerGroup().addTo(districtMap);
  }
  districtLayer.clearLayers();
  const max = Math.max(1, ...state.districts.map((d) => d.count));
  for (const d of state.districts) {
    const size = Math.round(26 + 30 * Math.sqrt(d.count / max));
    const icon = L.divIcon({ className: "", html: `<div class="district-label" style="width:${size}px;height:${size}px">${d.count}</div>`, iconSize: [size, size] });
    L.marker([d.lat, d.lon], { icon, title: d.name })
      .on("click", () => { location.hash = `#/?d=${encodeURIComponent(d.name)}`; })
      .bindTooltip(`${d.name}: ${d.count} trilhos (${d.with_track} com GPX)`)
      .addTo(districtLayer);
  }
  setTimeout(() => {
    districtMap.invalidateSize();
    districtMap.fitBounds([[36.9, -10], [42.2, -6.1]]);
  }, 50);
}

/* ---------------------------------------------------------------- trail view */

let trailMap;

async function renderTrail(id) {
  const el = $("#trail");
  el.innerHTML = `<p class="empty">A carregar…</p>`;
  let t;
  try {
    t = await api(`/api/trails/${encodeURIComponent(id)}`);
  } catch {
    el.innerHTML = `<a class="back" href="#/">← Voltar</a><p class="empty">Trilho não encontrado.</p>`;
    return;
  }
  document.title = `${t.ref} ${t.name} · Trilhos de Portugal`;
  const officialKm = t.official_distance_km, trackKm = t.track_km;
  const distSub = officialKm && trackKm ? `Traçado GPX: ${fmtKm(trackKm)}` : officialKm ? "Distância oficial" : trackKm ? "Medida no traçado GPX" : "";
  const typeName = { PR: "Pequena Rota", PRC: "Pequena Rota circular", PRL: "Pequena Rota linear", PL: "Percurso Local", GR: "Grande Rota" }[t.kind] || t.kind;
  el.innerHTML = `
    <a class="back" href="javascript:history.length > 1 ? history.back() : (location.hash = '#/')">← Voltar à lista</a>
    <header>
      <h1><span class="ref">${esc(t.ref)} · ${esc(typeName)}</span>${esc(t.name)}</h1>
      <div class="where">${esc([t.municipality, t.district].filter(Boolean).join(" · ") || "Distrito por apurar")}</div>
    </header>
    ${t.closed ? `<div class="alert">Este percurso está assinalado como encerrado. ${esc(t.notes)}</div>` : ""}
    <section class="stats">
      <div class="stat"><div class="label">Distância</div><div class="value">${approx(t.distance_source === "gpx")}${fmtKm(t.distance_km)}</div><div class="sub">${esc(distSub)}</div></div>
      <div class="stat"><div class="label">Dificuldade</div><div class="value"><span class="dot" style="background:${DIFF_COLOR[t.difficulty] || DIFF_COLOR[""]}"></span>${approx(t.difficulty_estimated)}${esc(t.difficulty || "—")}</div><div class="sub">${t.difficulty_estimated ? "Estimada pela distância" : t.difficulty ? "Indicada pela entidade" : ""}</div></div>
      <div class="stat"><div class="label">Duração</div><div class="value">${approx(t.duration_estimated)}${fmtDuration(t.duration_min)}</div><div class="sub">${t.duration_estimated ? "Estimada (sem paragens)" : t.duration_min ? "Indicada pela entidade" : ""}</div></div>
      <div class="stat"><div class="label">Traçado</div><div class="value">${esc(t.route_type || "—")}</div><div class="sub">${esc(typeName)}</div></div>
    </section>
    <div class="actions">
      ${t.has_track ? `<a class="btn primary" href="/api/trails/${encodeURIComponent(t.id)}/gpx" download>⬇ Descarregar GPX</a>` : `<span class="btn" aria-disabled="true">Sem GPX disponível</span>`}
      ${t.website ? `<a class="btn" href="${esc(t.website)}" target="_blank" rel="noopener">Página oficial ↗</a>` : ""}
      ${t.osm_url ? `<a class="btn" href="${esc(t.osm_url)}" target="_blank" rel="noopener">Ver no OpenStreetMap ↗</a>` : ""}
      ${t.has_track ? `<a class="btn" href="https://www.google.com/maps/dir/?api=1&destination=${t.lat},${t.lon}" target="_blank" rel="noopener">Como chegar ao início ↗</a>` : ""}
    </div>
    ${t.has_track ? `<div id="trail-map" class="trail-map"></div>` : `<div class="trail-map-empty">Ainda não há traçado deste percurso no OpenStreetMap${t.osm_relation ? " (a descarregar na próxima atualização)" : ""}.<br>Consulte a página oficial para obter o GPX.</div>`}
    <section class="details"><dl>
      <dt>Referência</dt><dd>${esc(t.ref)}</dd>
      ${t.promoter ? `<dt>Entidade promotora</dt><dd>${esc(t.promoter)}</dd>` : ""}
      <dt>Concelho / ilha</dt><dd>${esc(t.municipality || "—")}</dd>
      <dt>Distrito / região</dt><dd>${esc(t.district || "—")}</dd>
      ${t.notes && !t.closed ? `<dt>Notas</dt><dd>${esc(t.notes)}</dd>` : ""}
      <dt>Fontes</dt><dd>${esc(t.sources.join(", "))}</dd>
      <dt>Atualizado</dt><dd>${fmtDate(t.updated_at)}${t.track_fetched_at ? ` · traçado ${fmtDate(t.track_fetched_at)}` : ""}</dd>
    </dl></section>`;

  if (trailMap) { trailMap.remove(); trailMap = null; }
  if (!t.has_track) return;
  trailMap = makeMap("trail-map");
  const lines = t.track.map((seg) => seg.map(([lat, lon]) => [lat, lon]));
  L.polyline(lines, { color: "#fff", weight: 8, opacity: 0.8 }).addTo(trailMap);
  const line = L.polyline(lines, { color: "#d0383a", weight: 4 }).addTo(trailMap);
  const first = lines[0][0], lastSeg = lines[lines.length - 1], last = lastSeg[lastSeg.length - 1];
  L.circleMarker(first, { radius: 8, color: "#fff", weight: 2, fillColor: "#2e9e5b", fillOpacity: 1 }).bindTooltip("Início").addTo(trailMap);
  if (Math.abs(first[0] - last[0]) + Math.abs(first[1] - last[1]) > 0.0005) {
    L.circleMarker(last, { radius: 8, color: "#fff", weight: 2, fillColor: "#cf3a3a", fillOpacity: 1 }).bindTooltip("Fim").addTo(trailMap);
  }
  trailMap.fitBounds(line.getBounds().pad(0.08));
}

/* ---------------------------------------------------------------- status */

async function refreshStatus() {
  let s;
  try { s = await api("/api/status"); } catch { return; }
  const el = $("#status");
  const running = s.running ? `<span>⟳ ${esc(s.phase || "A atualizar")}${s.total ? ` (${s.done}/${s.total})` : ""}</span>` : "";
  el.innerHTML = `${running}<span>${s.trails.toLocaleString("pt-PT")} trilhos · ${s.tracks.toLocaleString("pt-PT")} com GPX · atualizado ${fmtDate(s.last_success)}</span>
    ${s.running ? "" : `<button type="button" id="refresh" title="Voltar a recolher os dados agora">Atualizar</button>`}`;
  if (s.error && !s.running) el.title = `Última atualização falhou: ${s.error}`;
  const btn = $("#refresh");
  if (btn) btn.onclick = async () => {
    btn.disabled = true;
    await api("/api/scrape", { method: "POST" });
    refreshStatus();
  };
  if (s.running) {
    clearTimeout(refreshStatus.timer);
    refreshStatus.timer = setTimeout(async () => {
      const before = state.trails.length;
      await refreshStatus();
      if (state.trails.length === 0 || before === 0) await loadData();
    }, 5000);
  } else if (refreshStatus.wasRunning) {
    await loadData();
  }
  refreshStatus.wasRunning = s.running;
}

/* ---------------------------------------------------------------- router */

async function loadData() {
  const [trails, districts] = await Promise.all([api("/api/trails"), api("/api/districts")]);
  for (const t of trails) t._search = fold(`${t.ref} ${t.ref.replace(/\s/g, "")} ${t.name} ${t.municipality} ${t.district}`);
  state.trails = trails;
  state.districts = districts;
  state.byId = new Map(trails.map((t) => [t.id, t]));
  const sel = $("#filters").elements.d;
  const current = sel.value;
  sel.innerHTML = `<option value="">Todos os distritos</option>` +
    districts.map((d) => `<option value="${esc(d.name)}">${esc(d.name)} (${d.count})</option>`).join("");
  sel.value = current;
  route(true);
}

function show(view) {
  for (const v of ["map", "districts", "trail"]) $(`#view-${v}`).hidden = v !== view;
  document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === view));
}

function route(dataChanged = false) {
  const hash = location.hash || "#/";
  if (hash.startsWith("#/trilho/")) {
    show("trail");
    renderTrail(decodeURIComponent(hash.slice("#/trilho/".length)));
    return;
  }
  document.title = "Trilhos de Portugal";
  if (hash.startsWith("#/distritos")) {
    show("districts");
    renderDistricts();
    return;
  }
  show("map");
  const params = new URLSearchParams(hash.includes("?") ? hash.slice(hash.indexOf("?") + 1) : "");
  const before = $("#filters").elements.d.value;
  loadFiltersFromHash(params);
  setTimeout(() => mainMap.invalidateSize(), 0);
  const district = params.get("d") || "";
  if (dataChanged || route.lastHash !== hash) applyFilters({ fit: !!district && (district !== before || dataChanged) });
  route.lastHash = location.hash;
}

window.addEventListener("hashchange", () => route());

initMainMap();
initFilters();
loadData().catch((e) => { $("#list").innerHTML = `<li class="empty">Erro a carregar os dados: ${esc(e.message)}</li>`; });
refreshStatus();
setInterval(refreshStatus, 60000);
