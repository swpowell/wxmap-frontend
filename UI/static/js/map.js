// map.js - Weather Map Application JavaScript
// =============================================================================

// =============================================================================
// CONFIGURATION
// =============================================================================

// CloudFront Configuration
// Set this to your CloudFront distribution domain for production
// For local dev (direct to Flask), use empty string
const CF_DOMAIN = "https://d2gf8w4b0qadys.cloudfront.net";

// Model-specific configuration
const MODEL_CONFIG = {
  graphcast:          { maxFhr: 240, fhrStep: 6 },
  gfs:                { maxFhr: 384, fhrStep: 6 },
  aigfs:              { maxFhr: 384, fhrStep: 6 },
  "navgem-graphcast": { maxFhr: 240, fhrStep: 6 },
  "atlat-gfs":        { maxFhr: 240, fhrStep: 6 }
};

const MODEL_LABELS = {
  graphcast: "GraphCast",
  gfs: "GFS",
  aigfs: "AIGFS",
  "navgem-graphcast": "NAVGEM-GC",
  "atlas-gfs": "Atlas-GFS" 
};

// Product configuration with colorbar info
// Gradients match the colormaps in styles.py
const PRODUCT_CONFIG = {
  t2m: {
    label: "2m Temperature",
    hoverVars: ["2t"],
    level: null,
    units: "tempK_to_FC",
    colorbar: {
      // RdYlBu_r colormap (red=hot, blue=cold)
      gradient: "linear-gradient(to right, #313695, #4575b4, #74add1, #abd9e9, #e0f3f8, #ffffbf, #fee090, #fdae61, #f46d43, #d73027, #a50026)",
      vmin: -40,
      vmax: 50,
      displayUnits: "°C"
    }
  },
  gh500: {
    label: "500 hPa Heights",
    hoverVars: ["gh"],
    level: 500,
    units: "meters_to_dam",
    colorbar: {
      // Spectral_r colormap
      gradient: "linear-gradient(to right, #5e4fa2, #3288bd, #66c2a5, #abdda4, #e6f598, #ffffbf, #fee08b, #fdae61, #f46d43, #d53e4f, #9e0142)",
      vmin: 480,
      vmax: 600,
      displayUnits: "dam"
    }
  },
  prmsl: {
    label: "Sea Level Pressure",
    hoverVars: ["prmsl"],
    level: null,
    units: "pa_to_hpa",
    colorbar: {
      // Viridis colormap
      gradient: "linear-gradient(to right, #440154, #482878, #3e4989, #31688e, #26838f, #1f9e89, #35b779, #6ece58, #b5de2b, #fde725)",
      vmin: 960,
      vmax: 1050,
      displayUnits: "hPa"
    }
  }
};

// Hover tooltip tuning
const HOVER_MIN_ZOOM = 5;
const HOVER_INTERVAL_MS = 150;
const HOVER_EPS_DEG = 0.02;

// Availability cache TTL
const AVAIL_TTL_MS = 2 * 60 * 1000;

// =============================================================================
// STATE
// =============================================================================

let MODEL_NAME = "graphcast";
let MODEL_DATE = "20251109";
let MODEL_INIT = "00";
let MODEL_FHR = "000";
let DISPLAY_TZ = "UTC";

// Hover state
let hoverPopup = null;
let lastFetchController = null;
let hoverTimer = null;
let hoverSeq = 0;
let lastLngLat = null;
let refreshPending = false;

// Availability cache
const availabilityCache = new Map();

// =============================================================================
// DOM ELEMENTS (initialized after DOMContentLoaded)
// =============================================================================

let $product, $chkGh, $ghProduct;
let $drawer, $drawerToggle, $drawerClose;
let $modelButtons, $runGrid, $runMeta;
let $fhrSlider, $fhrLabel, $validTimeLabel, $tzSelect;
let $bottomFhrSlider, $bottomValidLabel, $bottomFhrLabel, $bottomTzLabel;
let $colorbarContainer, $colorbarTitle, $colorbarGradient, $colorbarMin, $colorbarMax, $colorbarUnits;

function initDOMElements() {
  // Controls
  $product = document.getElementById('productSelect');
  $chkGh = document.getElementById('ghContours');
  $ghProduct = document.getElementById('ghProductSelect');

  // Drawer
  $drawer = document.getElementById('drawer');
  $drawerToggle = document.getElementById('drawerToggle');
  $drawerClose = document.getElementById('drawerClose');
  $modelButtons = document.getElementById('modelButtons');
  $runGrid = document.getElementById('runGrid');
  $runMeta = document.getElementById('runMeta');
  $fhrSlider = document.getElementById('fhrSlider');
  $fhrLabel = document.getElementById('fhrLabel');
  $validTimeLabel = document.getElementById('validTimeLabel');
  $tzSelect = document.getElementById('tzSelect');

  // Bottom bar
  $bottomFhrSlider = document.getElementById('bottomFhrSlider');
  $bottomValidLabel = document.getElementById('bottomValidLabel');
  $bottomFhrLabel = document.getElementById('bottomFhrLabel');
  $bottomTzLabel = document.getElementById('bottomTzLabel');

  // Colorbar
  $colorbarContainer = document.getElementById('colorbarContainer');
  $colorbarTitle = document.getElementById('colorbarTitle');
  $colorbarGradient = document.getElementById('colorbarGradient');
  $colorbarMin = document.getElementById('colorbarMin');
  $colorbarMax = document.getElementById('colorbarMax');
  $colorbarUnits = document.getElementById('colorbarUnits');
}

// =============================================================================
// UTILITY FUNCTIONS
// =============================================================================

function pad2(n) {
  return String(n).padStart(2, '0');
}

function pad3(n) {
  return String(n).padStart(3, '0');
}

function formatYYYYMMDD(d) {
  const y = d.getFullYear();
  const m = pad2(d.getMonth() + 1);
  const da = pad2(d.getDate());
  return `${y}${m}${da}`;
}

function prettyMD(d) {
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

function parseRunDateUTC(yyyymmdd, initHH) {
  const y = Number(yyyymmdd.slice(0, 4));
  const m = Number(yyyymmdd.slice(4, 6)) - 1;
  const d = Number(yyyymmdd.slice(6, 8));
  const hh = Number(initHH);
  return new Date(Date.UTC(y, m, d, hh, 0, 0));
}

function computeValidDate() {
  const init = parseRunDateUTC(MODEL_DATE, MODEL_INIT);
  const f = Number(MODEL_FHR);
  return new Date(init.getTime() + f * 3600 * 1000);
}

function formatValid(dateObj, timeZone) {
  try {
    const fmt = new Intl.DateTimeFormat('en-US', {
      timeZone,
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false
    });
    return fmt.format(dateObj).replace(',', '');
  } catch (e) {
    return dateObj.toISOString().slice(0, 16).replace('T', ' ');
  }
}

// =============================================================================
// AVAILABILITY CHECKS
// =============================================================================

function readyMarkerUrl(model, yyyymmdd, initHH) {
  const base = CF_DOMAIN || "";
  
  // NavGem-GraphCast uses a different _READY marker path structure
  // Its tiles are served from: /tiles/navgem-graphcast/{date}/{init}/{product}/{fhr}/...
  // But _READY marker is in the zarr directory in S3
  // For now, use the standard tile path - adjust if your pre-render pipeline differs
  return `${base}/tiles/${model}/${yyyymmdd}/${initHH}/fhr/000/_READY`;
}

async function isRunAvailable(model, yyyymmdd, initHH) {
  const key = `${model}|${yyyymmdd}|${initHH}`;
  const now = Date.now();

  const cached = availabilityCache.get(key);
  if (cached && (now - cached.t) < AVAIL_TTL_MS) return cached.ok;

  const url = readyMarkerUrl(model, yyyymmdd, initHH);

  try {
    const r = await fetch(url, { method: "HEAD", cache: "no-store" });
    const ok = r.ok;
    availabilityCache.set(key, { ok, t: now });
    return ok;
  } catch (e) {
    availabilityCache.set(key, { ok: false, t: now });
    return false;
  }
}

function isFutureCycleUTC(yyyymmdd, initHH) {
  const y = Number(yyyymmdd.slice(0, 4));
  const m = Number(yyyymmdd.slice(4, 6)) - 1;
  const d = Number(yyyymmdd.slice(6, 8));
  const hh = Number(initHH);
  const cycleTime = new Date(Date.UTC(y, m, d, hh, 0, 0));
  return cycleTime.getTime() > Date.now();
}

async function pickLatestAvailableRun(model) {
  const cyclesDesc = ["18", "12", "06", "00"];
  const today = new Date();
  for (let dayOffset = 0; dayOffset < 5; dayOffset++) {
    const d = new Date(today);
    d.setDate(today.getDate() - dayOffset);
    const ymd = formatYYYYMMDD(d);

    for (const hh of cyclesDesc) {
      if (isFutureCycleUTC(ymd, hh)) continue;
      if (await isRunAvailable(model, ymd, hh)) {
        return { date: ymd, init: hh };
      }
    }
  }
  return null;
}

// =============================================================================
// UI BUILDERS
// =============================================================================

function setActiveButtons(container, predicate) {
  [...container.querySelectorAll('.btn')].forEach(b => {
    b.classList.toggle('active', predicate(b));
  });
}

function buildModelButtons() {
  // NEW: Added navgem-graphcast to the list
  const models = ["gfs", "graphcast", "aigfs", "navgem-graphcast", "atlas-gfs"];
  $modelButtons.innerHTML = models.map(m => (
    `<button class="btn ${m === MODEL_NAME ? 'active' : ''}" data-model="${m}">${MODEL_LABELS[m] || m}</button>`
  )).join('');

  $modelButtons.querySelectorAll('button').forEach(btn => {
    btn.addEventListener('click', () => {
      MODEL_NAME = btn.dataset.model;
      buildModelButtons();
      configureFhrSlider();
      setFhrHours(Number($fhrSlider.value), { refresh: false });
      rebuildRunGrid();
      refreshAllData();
      updateRunMeta();
    });
  });
}

async function rebuildRunGrid() {
  const cycles = ["00", "06", "12", "18"];
  const today = new Date();
  const days = [];
  for (let i = 0; i < 5; i++) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    days.push(d);
  }

  // Build skeleton
  $runGrid.innerHTML = days.map((d, idx) => {
    const ymd = formatYYYYMMDD(d);
    const label = prettyMD(d);
    return `
      <div class="runRow" data-ymd="${ymd}">
        <div class="runDate">
          ${label}
          <span class="sub">${ymd.slice(0, 4)}-${ymd.slice(4, 6)}-${ymd.slice(6, 8)}</span>
        </div>
        <div class="cycleRow" data-cycle-row="${ymd}">
          <span style="font-size:12px;color:var(--muted)">Loading…</span>
        </div>
      </div>
    `;
  }).join('');

  // Fill buttons with async availability
  for (const d of days) {
    const ymd = formatYYYYMMDD(d);
    const row = $runGrid.querySelector(`[data-cycle-row="${ymd}"]`);
    if (!row) continue;

    const visibleCycles = cycles.filter(hh => !isFutureCycleUTC(ymd, hh));

    if (visibleCycles.length === 0) {
      row.innerHTML = `<span style="font-size:12px;color:var(--muted)">No cycles</span>`;
      continue;
    }

    const checks = await Promise.all(
      visibleCycles.map(async (hh) => {
        const ok = await isRunAvailable(MODEL_NAME, ymd, hh);
        return { hh, ok };
      })
    );

    row.innerHTML = checks.map(({ hh, ok }) => {
      const active = (ymd === MODEL_DATE && hh === MODEL_INIT);
      const disabled = !ok;
      const cls = ["btn", "small", active ? "active" : "", disabled ? "disabled" : ""].join(" ").trim();
      const disAttr = disabled ? "disabled" : "";
      return `<button ${disAttr} class="${cls}" data-date="${ymd}" data-init="${hh}">${hh}Z</button>`;
    }).join("");

    row.querySelectorAll("button:not(:disabled)").forEach(btn => {
      btn.addEventListener("click", () => {
        MODEL_DATE = btn.dataset.date;
        MODEL_INIT = btn.dataset.init;
        clearHover();
        rebuildRunGrid();
        refreshAllData();
        updateRunMeta();
        updateValidTimeLabel();
      });
    });
  }
}

function configureFhrSlider() {
  const cfg = MODEL_CONFIG[MODEL_NAME] || MODEL_CONFIG.graphcast;
  $fhrSlider.min = "0";
  $fhrSlider.max = String(cfg.maxFhr);
  $fhrSlider.step = String(cfg.fhrStep);

  const cur = Number(MODEL_FHR);
  const step = cfg.fhrStep;
  const max = cfg.maxFhr;

  let next = cur;
  if (!Number.isFinite(next) || next < 0 || next > max || (next % step !== 0)) next = 0;

  $fhrSlider.value = String(next);
  MODEL_FHR = pad3(next);
  $fhrLabel.textContent = `+${MODEL_FHR}h`;
  syncBottomSliderRange();
}

function buildTimezoneSelect() {
  const tzs = [
    { value: "UTC", label: "UTC" },
    { value: "America/Los_Angeles", label: "US Pacific" },
    { value: "America/Denver", label: "US Mountain" },
    { value: "America/Chicago", label: "US Central" },
    { value: "America/New_York", label: "US Eastern" },
    { value: "Europe/London", label: "London" },
    { value: "Europe/Paris", label: "Central Europe" },
    { value: "Asia/Tokyo", label: "Tokyo" },
    { value: "Australia/Sydney", label: "Sydney" }
  ];

  $tzSelect.innerHTML = tzs.map(t => `<option value="${t.value}">${t.label}</option>`).join('');
  $tzSelect.value = DISPLAY_TZ;

  $tzSelect.addEventListener('change', () => {
    DISPLAY_TZ = $tzSelect.value;
    updateValidTimeLabel();
    updateBottomBarLabels();
  });
}

function updateRunMeta() {
  $runMeta.innerHTML = `Selected: <b>${MODEL_LABELS[MODEL_NAME] || MODEL_NAME}</b> · <b>${MODEL_DATE}</b> · <b>${MODEL_INIT}Z</b>`;
}

function updateValidTimeLabel() {
  const v = computeValidDate();
  const s = formatValid(v, DISPLAY_TZ);
  $validTimeLabel.textContent = `Valid: ${s} (${DISPLAY_TZ})`;
}

function updateBottomBarLabels() {
  const v = computeValidDate();
  $bottomValidLabel.textContent = `${formatValid(v, DISPLAY_TZ)} (${DISPLAY_TZ})`;
  $bottomFhrLabel.textContent = `+${MODEL_FHR}h`;
  $bottomTzLabel.textContent = DISPLAY_TZ;
}

function syncBottomSliderRange() {
  $bottomFhrSlider.min = $fhrSlider.min;
  $bottomFhrSlider.max = $fhrSlider.max;
  $bottomFhrSlider.step = $fhrSlider.step;
}

function setFhrHours(hours, { refresh = false } = {}) {
  const cfg = MODEL_CONFIG[MODEL_NAME] || MODEL_CONFIG.graphcast;
  const step = cfg.fhrStep;
  const max = cfg.maxFhr;

  let h = Number(hours);
  if (!Number.isFinite(h)) h = 0;
  h = Math.max(0, Math.min(max, h));
  h = Math.round(h / step) * step;

  MODEL_FHR = pad3(h);

  $fhrSlider.value = String(h);
  if ($bottomFhrSlider) $bottomFhrSlider.value = String(h);

  $fhrLabel.textContent = `+${MODEL_FHR}h`;
  updateValidTimeLabel();
  updateBottomBarLabels();

  if (refresh && !refreshPending) {
    refreshPending = true;
    requestAnimationFrame(() => {
      refreshAllData();
      refreshPending = false;
    });
  }
}

// =============================================================================
// COLORBAR
// =============================================================================

function updateColorbar(product) {
  const pcfg = PRODUCT_CONFIG[product];

  if (!pcfg || !pcfg.colorbar || !$colorbarContainer) {
    if ($colorbarContainer) $colorbarContainer.style.display = 'none';
    return;
  }

  const cb = pcfg.colorbar;
  $colorbarContainer.style.display = 'block';
  $colorbarTitle.textContent = pcfg.label;
  $colorbarGradient.style.background = cb.gradient;
  $colorbarMin.textContent = cb.vmin;
  $colorbarMax.textContent = cb.vmax;
  $colorbarUnits.textContent = cb.displayUnits;
}

// =============================================================================
// URL BUILDERS
// =============================================================================

function tilesUrl(product) {
  const base = CF_DOMAIN || "";
  const fhr = MODEL_FHR;
  return `${base}/tiles/${MODEL_NAME}/${MODEL_DATE}/${MODEL_INIT}/${product}/${fhr}/{z}/{x}/{y}.png`;
}

function contoursUrl(product) {
  const base = CF_DOMAIN || "";
  const fhr = MODEL_FHR;
  return `${base}/contours/${MODEL_NAME}/${MODEL_DATE}/${MODEL_INIT}/${product}/${fhr}/{z}/{x}/{y}.png`;
}

// =============================================================================
// TILE REFRESH
// =============================================================================

function refreshTiles() {
  const product = $product.value;
  if (!product) return;

  if (map.getLayer('field')) map.removeLayer('field');
  if (map.getSource('field')) map.removeSource('field');

  map.addSource('field', {
    type: 'raster',
    tiles: [tilesUrl(product)],
    tileSize: 512,
    maxzoom: 5
  });

  map.addLayer({
    id: 'field',
    type: 'raster',
    source: 'field',
    paint: {
      'raster-resampling': 'nearest',
      'raster-opacity': 0.7
    }
  });

  // Update colorbar when tiles change
  updateColorbar(product);
}

function refreshContours() {
  if (!$chkGh.checked) {
    if (map.getLayer('ghcontours')) map.removeLayer('ghcontours');
    if (map.getSource('ghcontours')) map.removeSource('ghcontours');
    return;
  }

  const ghProduct = $ghProduct.value;
  if (!ghProduct) return;

  if (map.getLayer('ghcontours')) map.removeLayer('ghcontours');
  if (map.getSource('ghcontours')) map.removeSource('ghcontours');

  map.addSource('ghcontours', {
    type: 'raster',
    tiles: [contoursUrl(ghProduct)],
    tileSize: 256
  });
  map.addLayer({
    id: 'ghcontours',
    type: 'raster',
    source: 'ghcontours',
    paint: { 'raster-opacity': 1.0 }
  });
}

function refreshAllData() {
  refreshTiles();
  refreshContours();
}

// =============================================================================
// HOVER TOOLTIP
// =============================================================================

function formatValue(value, units) {
  if (!Number.isFinite(value)) return "—";

  if (units === "tempK_to_FC") {
    const c = value - 273.15;
    const f = c * 9 / 5 + 32;
    return `${f.toFixed(1)}°F / ${c.toFixed(1)}°C`;
  }

  if (units === "meters") {
    return `${value.toFixed(0)} m`;
  }

  if (units === "meters_to_dam") {
    return `${(value / 10).toFixed(0)} dam`;
  }

  if (units === "pa_to_hpa") {
    return `${(value / 100).toFixed(1)} hPa`;
  }

  return value.toFixed(2);
}

function clearHover() {
  if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = null; }
  if (lastFetchController) { lastFetchController.abort(); lastFetchController = null; }
  if (hoverPopup) hoverPopup.remove();
  lastLngLat = null;
}

function setupHoverTooltip() {
  hoverPopup = new mapboxgl.Popup({
    closeButton: false,
    closeOnClick: false,
    className: 'hover-tooltip'
  });

  map.on('mousemove', (e) => {
    if (map.getZoom() < HOVER_MIN_ZOOM) {
      clearHover();
      return;
    }

    const { lng, lat } = e.lngLat;

    if (lastLngLat) {
      const dlng = Math.abs(lng - lastLngLat.lng);
      const dlat = Math.abs(lat - lastLngLat.lat);
      if (dlng < HOVER_EPS_DEG && dlat < HOVER_EPS_DEG) return;
    }
    lastLngLat = { lng, lat };

    if (hoverTimer) clearTimeout(hoverTimer);
    const mySeq = ++hoverSeq;

    hoverTimer = setTimeout(async () => {
      if (lastFetchController) lastFetchController.abort();
      lastFetchController = new AbortController();

      const product = $product.value;
      const pcfg = PRODUCT_CONFIG[product] || {};
      const hoverVars = pcfg.hoverVars || [product];
      const level = (pcfg.level != null) ? pcfg.level : null;

      async function fetchPointValue(varName) {
        const params = new URLSearchParams({
          model: MODEL_NAME,
          date: MODEL_DATE,
          init: MODEL_INIT,
          fhr: MODEL_FHR,
          var: varName,
          lon: lng.toFixed(4),
          lat: lat.toFixed(4)
        });
        if (level != null) params.set("level", String(level));

        const url = `/point_value?${params.toString()}`;
        const r = await fetch(url, { signal: lastFetchController.signal });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      }

      try {
        let data = null;

        for (const vname of hoverVars) {
          const d = await fetchPointValue(vname);
          if (d && d.value != null && Number.isFinite(d.value)) {
            data = d;
            break;
          }
        }

        if (mySeq !== hoverSeq) return;
        if (map.getZoom() < HOVER_MIN_ZOOM) return;

        if (data && data.value != null && Number.isFinite(data.value)) {
          const formatted = formatValue(data.value, pcfg.units || "");
          hoverPopup
            .setLngLat([lng, lat])
            .setHTML(`<div style="text-align:center;font-weight:700">${formatted}</div>`)
            .addTo(map);
        } else {
          hoverPopup.remove();
        }
      } catch (err) {
        if (err.name !== 'AbortError') console.error('hover fetch failed:', err);
        hoverPopup.remove();
      }
    }, HOVER_INTERVAL_MS);
  });

  map.on('mouseleave', clearHover);
  map.on('zoom', () => { if (map.getZoom() < HOVER_MIN_ZOOM) clearHover(); });
}

// =============================================================================
// DRAWER
// =============================================================================

function openDrawer() {
  $drawer.classList.add('open');
}

function closeDrawer() {
  $drawer.classList.remove('open');
}

function setupDrawer() {
  $drawerToggle.addEventListener('click', () => {
    $drawer.classList.contains('open') ? closeDrawer() : openDrawer();
  });
  $drawerClose.addEventListener('click', closeDrawer);
}

// =============================================================================
// EVENT BINDINGS
// =============================================================================

function setupEventBindings() {
  $fhrSlider.addEventListener('input', () => setFhrHours($fhrSlider.value, { refresh: false }));
  $fhrSlider.addEventListener('change', () => setFhrHours($fhrSlider.value, { refresh: true }));

  $bottomFhrSlider.addEventListener('input', () => setFhrHours($bottomFhrSlider.value, { refresh: false }));
  $bottomFhrSlider.addEventListener('change', () => setFhrHours($bottomFhrSlider.value, { refresh: true }));

  $product.addEventListener('change', refreshTiles);
  $chkGh.addEventListener('change', refreshContours);
  $ghProduct.addEventListener('change', refreshContours);
}

// =============================================================================
// INITIALIZATION
// =============================================================================

async function initApp() {
  DISPLAY_TZ = "UTC";
  MODEL_NAME = "gfs";

  const latest = await pickLatestAvailableRun("gfs");
  if (latest) {
    MODEL_DATE = latest.date;
    MODEL_INIT = latest.init;
  } else {
    MODEL_DATE = formatYYYYMMDD(new Date());
    MODEL_INIT = "00";
  }

  MODEL_FHR = "000";

  buildModelButtons();
  buildTimezoneSelect();
  configureFhrSlider();
  setFhrHours(Number($fhrSlider.value), { refresh: false });

  await rebuildRunGrid();
  updateRunMeta();
  updateValidTimeLabel();

  refreshAllData();
  setupHoverTooltip();

  openDrawer();
}

// Entry point - called when map loads
function onMapLoad() {
  initDOMElements();
  setupDrawer();
  setupEventBindings();
  initApp();
}