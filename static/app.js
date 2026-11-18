let bulkQueue = [];
let currentQueueIdx = 0;
let bulkArtist = "";
let scoreChartInstance = null;

let allAlbumsData = [];
let allTracksData = [];
let currentLibraryView = 'artists';
let sortColumn = 'release_date';
let sortAsc = false;
let artistCache = [];
let artistSortColumn = 'name';
let artistSortAsc = true;

let currentAnalyticsSubView = 'single';
let compareChartInstance = null;

//Pagination state control
let currentPage = 1;
let pageSize = 25;

// Artist Dashboards View State
let currentDashboardView = 'groups'; // 'groups' or 'solos'
let allArtistsData = [];

// Group Dashboard State
let allArtistsSortColumn = 'artist';
let allArtistsSortAsc = true;
let allArtistsCurrentPage = 1;
let allArtistsPageSize = 15;

// Soloist Dashboard State
let soloArtistsSortColumn = 'artist';
let soloArtistsSortAsc = true;
let soloArtistsCurrentPage = 1;

// Single-Artist Trajectory Chart State
let chartAlignmentMode = 'calendar'; // 'calendar' or 'release'
let chartMetricMode = 'score'; // 'score' or 'qwar'
let currentArtistSummaryData = null; // Cache for snappy re-renders on toggle

// ============================================================================
// Q-WAR BENCHMARK THRESHOLDS & SCALE CONFIGURATION
// Used for the linear milestone bars and tier markers on the Deep Dive page.
// ============================================================================
const QWAR_BENCHMARKS = {
  catalogTotal: {
    maxScale: 140.0,       // Upper bound for the 100% bar width
    good: 50.0,            // Below 50: Developing (Slate), 50+: Good (Emerald)
    great: 80.0,           // 80+: Great (Sky Blue)
    ultimate: 110.0        // 110+: Ultimate (Purple)
  },
  qualityDensity: {
    maxScale: 2.5,         // Upper bound for the 100% bar width
    good: 1.0,             // Below 1.0: Developing (Slate), 1.0+: Good (Emerald)
    great: 1.4,            // 1.4+: Great (Sky Blue)
    ultimate: 2.0          // 2.0+: Ultimate (Purple)
  }
};

// Visual Tier List Thresholds (Geometric Mean Composite = sqrt(Q-WAR * (Rate * 50)))
// Easily adjustable per entity type without touching rendering logic.
const TIER_LIST_CONFIG = {
  minRatedTracks: 12,      // Rookie gate matching Q-WAR provisional threshold
  group: {
    S: 100.0,
    A: 85.0,
    B: 60.0,
    C: 40.0
    // Below C threshold falls into D tier
  },
  solo: {
    // Placeholder thresholds until solo artist catalog is populated
    S: 70.0,
    A: 50.0,
    B: 38.0,
    C: 25.0
  }
};

// Standard TierMaker Rainbow Palette (Coral Red -> Soft Blue) + Slate Provisional
const TIER_ROW_PALETTE = [
  { key: 'S', label: 'S', bgHex: '#ff7f7f', textClass: 'text-slate-950', subLabel: 'Ultimate' },
  { key: 'A', label: 'A', bgHex: '#ffbf7f', textClass: 'text-slate-950', subLabel: 'Great' },
  { key: 'B', label: 'B', bgHex: '#ffff7f', textClass: 'text-slate-950', subLabel: 'Good' },
  { key: 'C', label: 'C', bgHex: '#bfff7f', textClass: 'text-slate-950', subLabel: 'Hit or Miss' },
  { key: 'D', label: 'D', bgHex: '#7fbfff', textClass: 'text-slate-950', subLabel: 'Not for Me' },
  { key: 'P', label: 'TBD', bgHex: null, bgClass: 'bg-slate-700', textClass: 'text-slate-200', subLabel: '<12 trk' }
];

// Category Leaderboards Thresholds (Backlog #3)
const CATEGORY_LEADERS_CONFIG = {
  topN: 5,
  group: {
    minPerfectTracks: 12,
    minTitleTracks: 8,
    minBsideTracks: 12,
    minAlbumTracks: 8
  },
  solo: {
    minPerfectTracks: 12,
    minTitleTracks: 3,
    minBsideTracks: 6,
    minAlbumTracks: 8
  }
};

/**
 * Custom Chart.js plugin to draw horizontal background tier bands for Q-WAR and Rate modes.
 * Maps directly to QWAR_BENCHMARKS thresholds (Developing -> Good -> Great -> Ultimate).
 */
const qwarTierBackgroundPlugin = {
  id: 'qwarTierBackground',
  beforeDraw: (chart, args, options) => {
    const metricMode = options?.metricMode;
    if (!metricMode || metricMode === 'score') return;

    const { ctx, chartArea, scales } = chart;
    const yScale = scales?.y;
    if (!yScale || !chartArea) return;

    const { left, right, top, bottom } = chartArea;
    const width = right - left;

    let benchmarks;
    if (metricMode === 'qwar') {
      benchmarks = QWAR_BENCHMARKS.catalogTotal;
    } else if (metricMode === 'rate') {
      benchmarks = QWAR_BENCHMARKS.qualityDensity;
    } else {
      return;
    }

    const goodVal = benchmarks.good;
    const greatVal = benchmarks.great;
    const ultVal = benchmarks.ultimate;

    const getClampedPixel = (val) => {
      const p = yScale.getPixelForValue(val);
      return Math.max(top, Math.min(bottom, p));
    };

    const y0 = getClampedPixel(0);
    const yGood = getClampedPixel(goodVal);
    const yGreat = getClampedPixel(greatVal);
    const yUlt = getClampedPixel(ultVal);
    const yTop = top;

    ctx.save();

    // 1. Developing Band (0 to Good) - subtle slate 0.05
    if (y0 > yGood) {
      ctx.fillStyle = 'rgba(100, 116, 139, 0.05)';
      ctx.fillRect(left, yGood, width, y0 - yGood);
    }

    // 2. Good Band (Good to Great) - subtle emerald 0.08
    if (yGood > yGreat) {
      ctx.fillStyle = 'rgba(16, 185, 129, 0.15)';
      ctx.fillRect(left, yGreat, width, yGood - yGreat);
    }

    // 3. Great Band (Great to Ultimate) - subtle sky blue
    if (yGreat > yUlt) {
      ctx.fillStyle = 'rgba(56, 189, 248, 0.15)'; 0.09
      ctx.fillRect(left, yUlt, width, yGreat - yUlt);
    }

    // 4. Ultimate Band (Ultimate to Top) - subtle purple
    if (yUlt > yTop) {
      ctx.fillStyle = 'rgba(168, 85, 247, 0.15)'; 0.11
      ctx.fillRect(left, yTop, width, yUlt - yTop);
    }

    ctx.restore();
  }
};

function applyQwarBenchmarkLayout() {
  const c = QWAR_BENCHMARKS.catalogTotal;
  const devPts = (c.good / c.maxScale) * 100;
  const goodPts = ((c.great - c.good) / c.maxScale) * 100;
  const greatPts = ((c.ultimate - c.great) / c.maxScale) * 100;
  const ultPts = Math.max(0, 100 - (devPts + goodPts + greatPts));

  const segDevPts = document.getElementById("bar-pts-seg-dev");
  if (segDevPts) {
    segDevPts.style.width = `${devPts.toFixed(1)}%`;
    segDevPts.title = `Developing: <${c.good} pts`;
  }
  const segGoodPts = document.getElementById("bar-pts-seg-good");
  if (segGoodPts) {
    segGoodPts.style.width = `${goodPts.toFixed(1)}%`;
    segGoodPts.title = `Good: ${c.good}–${c.great} pts`;
  }
  const segGreatPts = document.getElementById("bar-pts-seg-great");
  if (segGreatPts) {
    segGreatPts.style.width = `${greatPts.toFixed(1)}%`;
    segGreatPts.title = `Great: ${c.great}–${c.ultimate} pts`;
  }
  const segUltPts = document.getElementById("bar-pts-seg-ult");
  if (segUltPts) {
    segUltPts.style.width = `${ultPts.toFixed(1)}%`;
    segUltPts.title = `Ultimate: ${c.ultimate}+ pts`;
  }

  const q = QWAR_BENCHMARKS.qualityDensity;
  const devRate = (q.good / q.maxScale) * 100;
  const goodRate = ((q.great - q.good) / q.maxScale) * 100;
  const greatRate = ((q.ultimate - q.great) / q.maxScale) * 100;
  const ultRate = Math.max(0, 100 - (devRate + goodRate + greatRate));

  const segDevRate = document.getElementById("bar-rate-seg-dev");
  if (segDevRate) {
    segDevRate.style.width = `${devRate.toFixed(1)}%`;
    segDevRate.title = `Developing: <${q.good} pts/track`;
  }
  const segGoodRate = document.getElementById("bar-rate-seg-good");
  if (segGoodRate) {
    segGoodRate.style.width = `${goodRate.toFixed(1)}%`;
    segGoodRate.title = `Good: ${q.good}–${q.great} pts/track`;
  }
  const segGreatRate = document.getElementById("bar-rate-seg-great");
  if (segGreatRate) {
    segGreatRate.style.width = `${greatRate.toFixed(1)}%`;
    segGreatRate.title = `Great: ${q.great}–${q.ultimate} pts/track`;
  }
  const segUltRate = document.getElementById("bar-rate-seg-ult");
  if (segUltRate) {
    segUltRate.style.width = `${ultRate.toFixed(1)}%`;
    segUltRate.title = `Ultimate: ${q.ultimate}+ pts/track`;
  }
}

function updateQwarLinearBars(compactedQwar, qwarRate) {
  const ptsPin = document.getElementById("qwar-pts-pin");
  const ratePin = document.getElementById("qwar-rate-pin");

  if (compactedQwar !== null && compactedQwar !== undefined && !isNaN(compactedQwar)) {
    const ptsRatio = Math.max(0, Math.min(compactedQwar / QWAR_BENCHMARKS.catalogTotal.maxScale, 1));
    const ptsPct = Math.max(1.5, Math.min(ptsRatio * 100, 98.5));
    if (ptsPin) {
      ptsPin.style.left = `calc(${ptsPct.toFixed(1)}% - 7px)`;
      ptsPin.classList.remove("hidden");
      ptsPin.title = `${parseFloat(compactedQwar).toFixed(2)} pts`;
    }
  } else if (ptsPin) {
    ptsPin.classList.add("hidden");
  }

  if (qwarRate !== null && qwarRate !== undefined && !isNaN(qwarRate)) {
    const rateRatio = Math.max(0, Math.min(qwarRate / QWAR_BENCHMARKS.qualityDensity.maxScale, 1));
    const ratePct = Math.max(1.5, Math.min(rateRatio * 100, 98.5));
    if (ratePin) {
      ratePin.style.left = `calc(${ratePct.toFixed(1)}% - 7px)`;
      ratePin.classList.remove("hidden");
      ratePin.title = `${parseFloat(qwarRate).toFixed(2)} pts/track`;
    }
  } else if (ratePin) {
    ratePin.classList.add("hidden");
  }
}

// ============================================================================
// CONSOLIDATED UI & DATA HELPERS (DRY)
// ============================================================================

/**
 * Generic null-safe ascending/descending record sorter for tables.
 */
function sortRecords(list, sortCol, isAsc, customExtractor = null) {
  list.sort((a, b) => {
    let valA = customExtractor ? customExtractor(a, sortCol) : a[sortCol];
    let valB = customExtractor ? customExtractor(b, sortCol) : b[sortCol];

    if (valA === null || valA === undefined) valA = isAsc ? Infinity : -Infinity;
    if (valB === null || valB === undefined) valB = isAsc ? Infinity : -Infinity;

    if (typeof valA === 'string' && typeof valB === 'string') {
      return isAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
    }
    return isAsc ? (valA - valB) : (valB - valA);
  });
}

/**
 * Normalizes release dates (YYYY -> YYYY-01-01, YYYY-MM -> YYYY-MM-01) and sorts albums chronologically.
 */
function prepareSortedChartAlbums(albums, excludeAdvanceSingles = false) {
  return (albums || [])
    .filter(a => !excludeAdvanceSingles || !a.is_advance_single)
    .map(a => {
      let d = a.release_date;
      if (d && d.length === 4) d = `${d}-01-01`;
      if (d && d.length === 7) d = `${d}-01`;
      return { ...a, release_date: d };
    })
    .filter(a => a.release_date && !isNaN(new Date(a.release_date).getTime()))
    .sort((a, b) => new Date(a.release_date) - new Date(b.release_date));
}

/**
 * Renders consistent inline member-count subtitle badge (e.g., " • 5 members" or " • 4 (5) members").
 * Accepts either (activeCount, totalCount) numbers or a comma-separated member string with '*' former markers.
 */
function formatGroupMemberCountBadge(activeOrMembersStr, maybeTotal = null) {
  let active = 0;
  let total = 0;
  if (typeof activeOrMembersStr === 'string') {
    if (!activeOrMembersStr.trim()) return '';
    const memArray = activeOrMembersStr.split(',').map(m => m.trim()).filter(Boolean);
    total = memArray.length;
    const former = memArray.filter(m => m.startsWith('*')).length;
    active = total - former;
  } else {
    active = activeOrMembersStr || 0;
    total = maybeTotal !== null ? maybeTotal : active;
  }

  if (total === 0) {
    return `<span class="text-slate-600 mx-1.5">•</span><span class="text-[10px] text-amber-500/80 font-mono">0 members</span>`;
  } else if (active !== total) {
    return `<span class="text-slate-600 mx-1.5">•</span><span class="text-[10px] text-slate-500 font-mono">${active} (${total}) members</span>`;
  }
  return `<span class="text-slate-600 mx-1.5">•</span><span class="text-[10px] text-slate-500 font-mono">${active} members</span>`;
}

/**
 * Generates consistent interactive parent group badge pills (active indigo vs former rose).
 */
function renderParentGroupBadge(pg, isCompact = false) {
  const isFormer = Boolean(pg.is_former);
  const isLinked = pg.group_id !== null && pg.group_id !== undefined;
  const prefix = isFormer ? "Ex-" : "";
  const label = `${prefix}${escapeHtml(pg.group_name)}`;

  if (!isLinked) {
    const unlinkedCls = isCompact
      ? "px-1.5 py-0.5 rounded border border-dashed border-slate-700/80 bg-slate-900/50 text-[10px] font-medium text-slate-400 cursor-default"
      : "px-2.5 py-1.5 rounded-lg border border-dashed border-slate-700/80 bg-slate-900/50 text-xs font-medium text-slate-400 truncate flex items-center gap-1.5 cursor-default";
    return `<span class="${unlinkedCls}" title="${label} (Not in library yet)">${label}</span>`;
  }

  const groupArg = escapeJsArg(pg.group_name);

  if (isCompact) {
    const badgeCls = isFormer
      ? "bg-rose-950/40 border-rose-800/80 text-rose-300 hover:border-rose-500"
      : "bg-indigo-950/80 border-indigo-700/70 text-indigo-200 hover:border-indigo-400";
    return `
      <button type="button" onclick="jumpToSingleArtist('${groupArg}')"
              class="px-1.5 py-0.5 rounded border text-[10px] font-semibold transition ${badgeCls}"
              title="${label}">
        ${label}
      </button>
    `;
  }

  const colorClasses = isFormer
    ? "bg-rose-950/40 hover:bg-rose-900/60 border-rose-800/80 hover:border-rose-500 text-rose-300"
    : "bg-indigo-950/80 hover:bg-indigo-900 border-indigo-700/70 hover:border-indigo-400 text-indigo-200";

  return `
    <button type="button" onclick="jumpToSingleArtist('${groupArg}')"
            class="px-2.5 py-1.5 rounded-lg border text-xs font-semibold truncate flex items-center gap-1.5 transition ${colorClasses}"
            title="${label}">
      <span>${label}</span>
      <span class="text-[10px] opacity-70">↗</span>
    </button>
  `;
}

/**
 * Generic helper to toggle styles and subtitle for a pair of segmented buttons.
 */
function updateSegmentedButtonPair({ activeVal, firstVal, firstBtnId, secondBtnId, subtitleId, firstSubText, secondSubText }) {
  const isFirst = (activeVal === firstVal);
  const btn1 = document.getElementById(firstBtnId);
  const btn2 = document.getElementById(secondBtnId);
  const subtitle = document.getElementById(subtitleId);

  if (btn1) {
    btn1.className = isFirst
      ? "px-2.5 py-1 rounded bg-indigo-600 text-white font-semibold transition"
      : "px-2.5 py-1 rounded text-slate-400 hover:text-white transition";
  }
  if (btn2) {
    btn2.className = !isFirst
      ? "px-2.5 py-1 rounded bg-indigo-600 text-white font-semibold transition"
      : "px-2.5 py-1 rounded text-slate-400 hover:text-white transition";
  }
  if (subtitle) {
    subtitle.innerText = isFirst ? firstSubText : secondSubText;
  }
}

/**
 * Toggles segmented button styling between Calendar Timeline and Substantive Release Sequence.
 */
function updateAlignmentButtonState(calBtnId, relBtnId, subtitleId, mode) {
  updateSegmentedButtonPair({
    activeVal: mode,
    firstVal: 'calendar',
    firstBtnId: calBtnId,
    secondBtnId: relBtnId,
    subtitleId: subtitleId,
    firstSubText: 'Calendar Timeline',
    secondSubText: 'Release Sequence'
  });
}

/**
 * Toggles segmented button styling across Track Score, Cumulative Q-WAR, and Q-WAR Rate.
 */
function updateMetricButtonState(scoreBtnId, qwarBtnId, rateBtnId, subtitleId, mode) {
  const scoreBtn = document.getElementById(scoreBtnId);
  const qwarBtn = document.getElementById(qwarBtnId);
  const rateBtn = document.getElementById(rateBtnId);
  const subEl = document.getElementById(subtitleId);

  const activeCls = "px-2.5 py-1 rounded bg-indigo-600 text-white font-semibold transition";
  const inactiveCls = "px-2.5 py-1 rounded text-slate-400 hover:text-white transition";

  if (scoreBtn) scoreBtn.className = (mode === 'score') ? activeCls : inactiveCls;
  if (qwarBtn) qwarBtn.className = (mode === 'qwar') ? activeCls : inactiveCls;
  if (rateBtn) rateBtn.className = (mode === 'rate') ? activeCls : inactiveCls;

  if (subEl) {
    if (mode === 'score') {
      subEl.textContent = 'Exponential Moving Average';
    } else if (mode === 'qwar') {
      subEl.textContent = 'Cumulative Q-WAR Progression';
    } else if (mode === 'rate') {
      subEl.textContent = 'Quality Density (Q-WAR Rate)';
    }
  }
}

function setChartAlignment(mode) {
  if (chartAlignmentMode === mode) return;
  chartAlignmentMode = mode;
  updateAlignmentButtonState("toggle-align-calendar", "toggle-align-release", "chart-alignment-subtitle", mode);
  if (currentArtistSummaryData) {
    renderTrajectoryChart(currentArtistSummaryData);
  }
}

function setChartMetric(mode) {
  if (chartMetricMode === mode) return;
  chartMetricMode = mode;
  updateMetricButtonState("toggle-metric-score", "toggle-metric-qwar", "toggle-metric-rate", "chart-metric-subtitle", mode);
  if (currentArtistSummaryData) {
    renderTrajectoryChart(currentArtistSummaryData);
  }
}

// Album View Sort State
let albumSortColumn = 'release_date';
let albumSortAsc = false;

function sortAlbumsTable(col) {
  if (albumSortColumn === col) {
    albumSortAsc = !albumSortAsc;
  } else {
    albumSortColumn = col;
    // Text columns default A-Z ascending, dates & numeric counts default descending
    albumSortAsc = (col === 'album_title' || col === 'artist' || col === 'album_type');
  }
  currentPage = 1;
  renderFilteredAlbums();
}

// Multi-Artist Compare Chart State
let compareAlignmentMode = 'calendar'; // 'calendar' or 'release'
let compareMetricMode = 'score'; // 'score' or 'qwar'

function setCompareAlignment(mode) {
  if (compareAlignmentMode === mode) return;
  compareAlignmentMode = mode;
  updateAlignmentButtonState("toggle-compare-calendar", "toggle-compare-release", "compare-alignment-subtitle", mode);
  renderComparisonAnalytics();
}

function setCompareMetric(mode) {
  if (compareMetricMode === mode) return;
  compareMetricMode = mode;
  updateMetricButtonState("toggle-compare-metric-score", "toggle-compare-metric-qwar", "toggle-compare-metric-rate", "compare-metric-subtitle", mode);
  renderComparisonAnalytics();
}

function getStatusBadge(status) {
  const s = status || "Active";
  if (s === "Disbanded") {
    return `<span class="whitespace-nowrap text-[10px] bg-rose-950 text-rose-400 border border-rose-800 px-1.5 py-0.5 rounded font-medium">Disbanded</span>`;
  } else if (s === "Hiatus") {
    return `<span class="whitespace-nowrap text-[10px] bg-amber-950 text-amber-400 border border-amber-800 px-1.5 py-0.5 rounded font-medium">Hiatus</span>`;
  }
  return `<span class="whitespace-nowrap text-[10px] bg-emerald-950 text-emerald-400 border border-emerald-800 px-1.5 py-0.5 rounded font-medium">Active</span>`;
}

function getGenerationBadge(gen) {
  if (!gen) return `<span class="text-[10px] text-slate-600">--</span>`;

  const genKey = gen.trim();
  let colorClasses = "bg-indigo-950 text-indigo-300 border-indigo-800"; // default

  if (genKey === "Gen 1") {
    colorClasses = "bg-slate-900 text-slate-300 border-slate-700";
  } else if (genKey === "Gen 2") {
    colorClasses = "bg-teal-950 text-teal-300 border-teal-800";
  } else if (genKey === "Gen 3") {
    colorClasses = "bg-sky-950 text-sky-300 border-sky-800";
  } else if (genKey === "Gen 4") {
    colorClasses = "bg-indigo-950 text-indigo-300 border-indigo-800";
  } else if (genKey === "Gen 5") {
    colorClasses = "bg-purple-950 text-purple-300 border-purple-800";
  }

  return `<span class="whitespace-nowrap inline-block text-[10px] ${colorClasses} border px-2 py-0.5 rounded font-medium">${escapeHtml(genKey)}</span>`;
}

function changePageSize(size) {
  pageSize = parseInt(size, 10);
  currentPage = 1;
  document.querySelectorAll(".lib-page-size, #lib-page-size").forEach(sel => {
    sel.value = String(pageSize);
  });
  if (currentLibraryView === 'artists') {
    loadLibraryArtists();
  } else if (currentLibraryView === 'albums') {
    renderFilteredAlbums();
  } else {
    applyFilters();
  }
}

function goToPage(page) {
  currentPage = page;
  if (currentLibraryView === 'artists') {
    loadLibraryArtists();
  } else if (currentLibraryView === 'albums') {
    renderFilteredAlbums();
  } else {
    applyFilters();
  }
}

function applyPaginationControls({ prefix, totalItems, currentPage, pageSize }) {
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  const validPage = Math.min(Math.max(1, currentPage), totalPages);

  const start = totalItems === 0 ? 0 : (validPage - 1) * pageSize + 1;
  const end = Math.min(totalItems, validPage * pageSize);

  const summaryEls = document.querySelectorAll(`.${prefix}-page-summary, #${prefix}-page-summary`);
  const currentEls = document.querySelectorAll(`.${prefix}-page-current, #${prefix}-page-current`);
  const totalEls = document.querySelectorAll(`.${prefix}-page-total, #${prefix}-page-total`);
  const prevBtns = document.querySelectorAll(`.${prefix}-btn-prev, #${prefix}-btn-prev`);
  const nextBtns = document.querySelectorAll(`.${prefix}-btn-next, #${prefix}-btn-next`);

  summaryEls.forEach(el => el.innerText = `Showing ${start}-${end} of ${totalItems}`);
  currentEls.forEach(el => el.innerText = validPage);
  totalEls.forEach(el => el.innerText = totalPages);

  prevBtns.forEach(btn => btn.disabled = (validPage <= 1));
  nextBtns.forEach(btn => btn.disabled = (validPage >= totalPages));

  return validPage;
}

function updatePaginationControls(totalItems) {
  currentPage = applyPaginationControls({
    prefix: "lib",
    totalItems,
    currentPage,
    pageSize
  });
}

let currentLibrarySubView = 'explorer';

function switchMainView(view) {
  const analyticsPane = document.getElementById("view-analytics");
  const managementPane = document.getElementById("view-management");
  const analyticsBtn = document.getElementById("nav-btn-analytics");
  const managementBtn = document.getElementById("nav-btn-management");

  if (view === "analytics") {
    analyticsPane.classList.remove("hidden");
    managementPane.classList.add("hidden");

    analyticsBtn.className = "px-4 py-1.5 rounded-lg bg-indigo-600 text-white font-semibold transition shadow-sm flex items-center gap-2";
    managementBtn.className = "px-4 py-1.5 rounded-lg text-slate-400 hover:text-white transition flex items-center gap-2";

    if (scoreChartInstance) {
      scoreChartInstance.resize();
    } else {
      renderAnalytics();
    }
  } else {
    managementPane.classList.remove("hidden");
    analyticsPane.classList.add("hidden");

    managementBtn.className = "px-4 py-1.5 rounded-lg bg-indigo-600 text-white font-semibold transition shadow-sm flex items-center gap-2";
    analyticsBtn.className = "px-4 py-1.5 rounded-lg text-slate-400 hover:text-white transition flex items-center gap-2";

    switchLibrarySubView(currentLibrarySubView || 'explorer');
  }
}

function switchLibrarySubView(subview) {
  currentLibrarySubView = subview;
  const explorerBtn = document.getElementById("subnav-lib-explorer");
  const watchlistBtn = document.getElementById("subnav-lib-watchlist");
  const ioBtn = document.getElementById("subnav-lib-io");

  const explorerPane = document.getElementById("view-lib-explorer");
  const watchlistPane = document.getElementById("view-lib-watchlist");
  const ioPane = document.getElementById("view-lib-io");

  [explorerBtn, watchlistBtn, ioBtn].forEach(btn => {
    if (btn) btn.className = "px-3.5 py-1.5 rounded-md text-slate-400 hover:text-white transition";
  });
  [explorerPane, watchlistPane, ioPane].forEach(pane => {
    if (pane) pane.classList.add("hidden");
  });

  if (subview === 'explorer') {
    if (explorerBtn) explorerBtn.className = "px-3.5 py-1.5 rounded-md bg-indigo-600 text-white font-semibold transition";
    if (explorerPane) explorerPane.classList.remove("hidden");
    switchLibraryView(currentLibraryView || 'artists');
  } else if (subview === 'watchlist') {
    if (watchlistBtn) watchlistBtn.className = "px-3.5 py-1.5 rounded-md bg-indigo-600 text-white font-semibold transition";
    if (watchlistPane) watchlistPane.classList.remove("hidden");
    loadWatchlist();
  } else if (subview === 'io') {
    if (ioBtn) ioBtn.className = "px-3.5 py-1.5 rounded-md bg-indigo-600 text-white font-semibold transition";
    if (ioPane) ioPane.classList.remove("hidden");
  }
}

function switchImportMode(mode) {
  const singleBtn = document.getElementById("import-mode-single-btn");
  const bulkBtn = document.getElementById("import-mode-bulk-btn");
  const singlePanel = document.getElementById("panel-single");
  const bulkPanel = document.getElementById("panel-bulk");

  if (mode === "single") {
    if (singleBtn) singleBtn.className = "px-3.5 py-1.5 rounded-md bg-indigo-600 text-white font-semibold transition";
    if (bulkBtn) bulkBtn.className = "px-3.5 py-1.5 rounded-md text-slate-400 hover:text-white transition";
    if (singlePanel) singlePanel.classList.remove("hidden");
    if (bulkPanel) bulkPanel.classList.add("hidden");
  } else {
    if (bulkBtn) bulkBtn.className = "px-3.5 py-1.5 rounded-md bg-indigo-600 text-white font-semibold transition";
    if (singleBtn) singleBtn.className = "px-3.5 py-1.5 rounded-md text-slate-400 hover:text-white transition";
    if (bulkPanel) bulkPanel.classList.remove("hidden");
    if (singlePanel) singlePanel.classList.add("hidden");
  }
}

function dismissStatusBanner(elementId) {
  const el = document.getElementById(elementId);
  if (!el) return;
  el.className = "hidden text-xs";
  el.innerHTML = "";
}

async function triggerAppSnapshot() {
  const btn = document.getElementById("snapshot-btn");
  const labelInput = document.getElementById("snapshot-label");
  const statusDiv = document.getElementById("snapshot-status");
  const label = labelInput ? labelInput.value.trim() : "";

  if (btn) {
    btn.disabled = true;
    btn.classList.add("opacity-60", "cursor-not-allowed");
    btn.innerHTML = `
      <svg class="animate-spin -ml-1 mr-1.5 h-3.5 w-3.5 text-white inline-block" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
      </svg>
      <span>Creating Snapshot...</span>
    `;
  }
  if (statusDiv) {
    statusDiv.className = "hidden text-xs";
    statusDiv.innerHTML = "";
  }

  try {
    const res = await fetch("/api/backup/snapshot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label: label || null })
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Snapshot failed");
    }

    if (statusDiv) {
      statusDiv.className = "mt-2 p-2.5 rounded bg-emerald-950/80 border border-emerald-800 text-emerald-300 text-xs flex items-center justify-between gap-2";
      statusDiv.innerHTML = `
        <span>✓ Snapshot successfully created in <code class="font-mono text-emerald-200">${escapeHtml(data.snapshot_folder)}</code>.</span>
        <button type="button" onclick="dismissStatusBanner('snapshot-status')" title="Dismiss" aria-label="Dismiss"
                class="text-emerald-400/70 hover:text-emerald-200 font-bold text-sm leading-none px-1 transition shrink-0">&times;</button>
      `;
    }
    if (labelInput) labelInput.value = "";
  } catch (err) {
    if (statusDiv) {
      statusDiv.className = "mt-2 p-2.5 rounded bg-rose-950/80 border border-rose-800 text-rose-300 text-xs flex items-center justify-between gap-2";
      statusDiv.innerHTML = `
        <span>✗ Failed to create snapshot: ${escapeHtml(err.message)}</span>
        <button type="button" onclick="dismissStatusBanner('snapshot-status')" title="Dismiss" aria-label="Dismiss"
                class="text-rose-400/70 hover:text-rose-200 font-bold text-sm leading-none px-1 transition shrink-0">&times;</button>
      `;
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.classList.remove("opacity-60", "cursor-not-allowed");
      btn.innerHTML = `<span>Create System Snapshot</span>`;
    }
  }
}

let pendingRestoreFile = null;

function handleRestoreFileSelect(file) {
  const labelEl = document.getElementById("restore-file-label");
  const metaEl = document.getElementById("restore-file-meta");
  const submitBtn = document.getElementById("restore-submit-btn");
  const statusDiv = document.getElementById("restore-status");

  if (statusDiv) {
    statusDiv.className = "hidden text-xs";
    statusDiv.innerHTML = "";
  }

  if (!file) {
    pendingRestoreFile = null;
    if (labelEl) {
      labelEl.innerHTML = `Drag &amp; drop a <code class="font-mono text-amber-300">.db</code> or <code class="font-mono text-cyan-300">.json</code> backup here, or <span class="text-amber-400 underline">browse</span>`;
    }
    if (metaEl) metaEl.textContent = "No file selected";
    if (submitBtn) submitBtn.disabled = true;
    return;
  }

  const lowerName = file.name.toLowerCase();
  if (!lowerName.endsWith(".db") && !lowerName.endsWith(".json")) {
    pendingRestoreFile = null;
    if (submitBtn) submitBtn.disabled = true;
    if (statusDiv) {
      statusDiv.className = "mt-2 p-2.5 rounded bg-rose-950/80 border border-rose-800 text-rose-300 text-xs flex items-center justify-between gap-2";
      statusDiv.innerHTML = `
        <span>✗ Unsupported file <code class="font-mono">${escapeHtml(file.name)}</code>. Please choose a .db or .json backup.</span>
        <button type="button" onclick="dismissStatusBanner('restore-status')" title="Dismiss" aria-label="Dismiss"
                class="text-rose-400/70 hover:text-rose-200 font-bold text-sm leading-none px-1 transition shrink-0">&times;</button>
      `;
    }
    return;
  }

  pendingRestoreFile = file;
  const sizeKb = (file.size / 1024).toFixed(1);
  if (labelEl) {
    labelEl.innerHTML = `Selected: <code class="font-mono text-amber-200">${escapeHtml(file.name)}</code>`;
  }
  if (metaEl) metaEl.textContent = `${sizeKb} KB — ready to restore`;
  if (submitBtn) submitBtn.disabled = false;
}

function initRestoreDropzone() {
  const dropzone = document.getElementById("restore-dropzone");
  if (!dropzone) return;

  ["dragenter", "dragover"].forEach((evtName) => {
    dropzone.addEventListener(evtName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.add("border-amber-400", "bg-amber-950/20");
    });
  });

  ["dragleave", "drop"].forEach((evtName) => {
    dropzone.addEventListener(evtName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.remove("border-amber-400", "bg-amber-950/20");
    });
  });

  dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer?.files?.[0];
    if (file) handleRestoreFileSelect(file);
  });
}

document.addEventListener("DOMContentLoaded", initRestoreDropzone);

async function triggerCatalogRestore() {
  if (!pendingRestoreFile) return;

  const confirmed = window.confirm(
    `Restore catalog from "${pendingRestoreFile.name}"?\n\n` +
    `This will overwrite your current database (preserving original track IDs from the backup).\n` +
    `A safety snapshot (pre_restore) will be created in backups/snapshots/ before any changes are made.`
  );
  if (!confirmed) return;

  const btn = document.getElementById("restore-submit-btn");
  const statusDiv = document.getElementById("restore-status");
  const fileInput = document.getElementById("restore-file-input");

  if (btn) {
    btn.disabled = true;
    btn.classList.add("opacity-60", "cursor-not-allowed");
    btn.innerHTML = `
      <svg class="animate-spin -ml-1 mr-1.5 h-3.5 w-3.5 text-white inline-block" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
      </svg>
      <span>Restoring...</span>
    `;
  }
  if (statusDiv) {
    statusDiv.className = "hidden text-xs";
    statusDiv.innerHTML = "";
  }

  try {
    const res = await fetch(`/api/restore?filename=${encodeURIComponent(pendingRestoreFile.name)}`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: pendingRestoreFile
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Restore failed");
    }

    const c = data.counts || {};
    if (statusDiv) {
      statusDiv.className = "mt-2 p-2.5 rounded bg-emerald-950/80 border border-emerald-800 text-emerald-300 text-xs flex items-center justify-between gap-2";
      statusDiv.innerHTML = `
        <span>
          ✓ Restored from <code class="font-mono text-emerald-200">${escapeHtml(pendingRestoreFile.name)}</code>:
          <strong>${c.artists ?? 0}</strong> artists, <strong>${c.albums ?? 0}</strong> albums, <strong>${c.tracks ?? 0}</strong> tracks,
          <strong>${c.artist_members ?? 0}</strong> roster links, <strong>${c.artist_watchlist ?? 0}</strong> watchlist
          (safety snapshot: <code class="font-mono text-emerald-200">${escapeHtml(data.snapshot_folder)}</code>).
        </span>
        <button type="button" onclick="dismissStatusBanner('restore-status')" title="Dismiss" aria-label="Dismiss"
                class="text-emerald-400/70 hover:text-emerald-200 font-bold text-sm leading-none px-1 transition shrink-0">&times;</button>
      `;
    }

    if (fileInput) fileInput.value = "";
    pendingRestoreFile = null;
    const labelEl = document.getElementById("restore-file-label");
    const metaEl = document.getElementById("restore-file-meta");
    if (labelEl) {
      labelEl.innerHTML = `Drag &amp; drop a <code class="font-mono text-amber-300">.db</code> or <code class="font-mono text-cyan-300">.json</code> backup here, or <span class="text-amber-400 underline">browse</span>`;
    }
    if (metaEl) metaEl.textContent = "No file selected";

    // Refresh all library, dashboard, and watchlist views to reflect restored state
    await Promise.allSettled([
      loadLibrary(true),
      loadLibraryArtists(),
      loadTracksData(),
      loadAllArtistsAnalytics(),
      loadWatchlist()
    ]);
  } catch (err) {
    if (statusDiv) {
      statusDiv.className = "mt-2 p-2.5 rounded bg-rose-950/80 border border-rose-800 text-rose-300 text-xs flex items-center justify-between gap-2";
      statusDiv.innerHTML = `
        <span>✗ Restore failed: ${escapeHtml(err.message)}</span>
        <button type="button" onclick="dismissStatusBanner('restore-status')" title="Dismiss" aria-label="Dismiss"
                class="text-rose-400/70 hover:text-rose-200 font-bold text-sm leading-none px-1 transition shrink-0">&times;</button>
      `;
    }
  } finally {
    if (btn) {
      btn.classList.remove("opacity-60", "cursor-not-allowed");
      btn.disabled = !pendingRestoreFile;
      btn.innerHTML = `
        <svg xmlns="http://www.w3.org/2000/svg" class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
        </svg>
        <span>Restore Catalog</span>
      `;
    }
  }
}

async function syncStcEmbeddings() {
  const btn = document.getElementById("stc-sync-btn");
  const statusDiv = document.getElementById("stc-sync-status");

  if (btn) {
    btn.disabled = true;
    btn.classList.add("opacity-60", "cursor-not-allowed");
    btn.innerHTML = `
      <svg class="animate-spin -ml-1 mr-1.5 h-3.5 w-3.5 text-white inline-block" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
      </svg>
      <span>Syncing &amp; Retraining...</span>
    `;
  }
  if (statusDiv) {
    statusDiv.className = "hidden text-xs";
    statusDiv.innerHTML = "";
  }

  try {
    const startRes = await fetch("/api/stc/sync", { method: "POST" });
    const startText = await startRes.text();
    let data = {};
    try { data = JSON.parse(startText); } catch (_) { data = { detail: startText || "STC sync failed" }; }
    if (!startRes.ok) {
      throw new Error(data.detail || "STC sync failed");
    }

    // Poll /api/stc/sync/status until background sync finishes
    if (data.running || data.status === "started" || data.status === "already_running") {
      while (true) {
        await new Promise((resolve) => setTimeout(resolve, 2500));
        const stRes = await fetch("/api/stc/sync/status");
        const stText = await stRes.text();
        let stData = {};
        try { stData = JSON.parse(stText); } catch (_) { stData = { detail: stText || "Failed to check sync status" }; }
        if (!stRes.ok) {
          throw new Error(stData.detail || "Failed to check STC sync status");
        }
        if (stData.running) {
          continue;
        }
        if (stData.status === "error") {
          throw new Error(stData.error || "STC sync failed");
        }
        data = stData.result || data;
        break;
      }
    }

    const newlyUnmatched = Array.isArray(data.newly_unmatched) ? data.newly_unmatched : [];
    const unmatchedHtml = newlyUnmatched.length > 0
      ? `<div class="mt-1.5 text-[11px] text-amber-300">New unmatched previews (${newlyUnmatched.length}): ${newlyUnmatched.map(t => `${escapeHtml(t.artist)} - ${escapeHtml(t.title)}`).join(", ")}</div>`
      : "";

    if (statusDiv) {
      statusDiv.className = "mt-2 p-2.5 rounded bg-emerald-950/80 border border-emerald-800 text-emerald-300 text-xs flex items-start justify-between gap-2";
      statusDiv.innerHTML = `
        <div>
          <div>✓ Synced <strong>${data.newly_embedded}</strong> new tracks (${data.newly_windows} window sets) — <strong>${data.total_cached}</strong> total cached in training set.</div>
          ${unmatchedHtml}
        </div>
        <button type="button" onclick="dismissStatusBanner('stc-sync-status')" title="Dismiss" aria-label="Dismiss"
                class="text-emerald-400/70 hover:text-emerald-200 font-bold text-sm leading-none px-1 transition shrink-0">&times;</button>
      `;
    }
  } catch (err) {
    if (statusDiv) {
      statusDiv.className = "mt-2 p-2.5 rounded bg-rose-950/80 border border-rose-800 text-rose-300 text-xs flex items-center justify-between gap-2";
      statusDiv.innerHTML = `
        <span>✗ STC sync failed: ${escapeHtml(err.message)}</span>
        <button type="button" onclick="dismissStatusBanner('stc-sync-status')" title="Dismiss" aria-label="Dismiss"
                class="text-rose-400/70 hover:text-rose-200 font-bold text-sm leading-none px-1 transition shrink-0">&times;</button>
      `;
    }
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.classList.remove("opacity-60", "cursor-not-allowed");
      btn.innerHTML = '<span>Sync &amp; Retrain Model</span>';
    }
  }
}

function switchLibraryView(view) {
  currentLibraryView = view;
  currentPage = 1;
  
  const artistBtn = document.getElementById("view-artists-btn");
  const albumBtn = document.getElementById("view-albums-btn");
  const trackBtn = document.getElementById("view-tracks-btn");

  const artistContainer = document.getElementById("library-artists-container");
  const albumContainer = document.getElementById("library-albums-container");
  const trackContainer = document.getElementById("library-tracks-container");

  const fbArtists = document.getElementById("filterbar-artists");
  const fbAlbums = document.getElementById("filterbar-albums");
  const fbTracks = document.getElementById("filterbar-tracks");

  // Reset button styles
  [artistBtn, albumBtn, trackBtn].forEach(b => {
    if (b) b.className = "px-3 py-1 rounded text-slate-400 hover:text-white transition";
  });
  // Toggle table containers
  if (artistContainer) artistContainer.classList.toggle("hidden", view !== 'artists');
  if (albumContainer) albumContainer.classList.toggle("hidden", view !== 'albums');
  if (trackContainer) trackContainer.classList.toggle("hidden", view !== 'tracks');

  // Toggle filter bars
  if (fbArtists) fbArtists.classList.toggle("hidden", view !== 'artists');
  if (fbAlbums) fbAlbums.classList.toggle("hidden", view !== 'albums');
  if (fbTracks) fbTracks.classList.toggle("hidden", view !== 'tracks');

  if (view === 'artists') {
    artistBtn.className = "px-3 py-1 rounded bg-indigo-600 text-white font-medium transition";
    loadLibraryArtists();
  } else if (view === 'albums') {
    albumBtn.className = "px-3 py-1 rounded bg-indigo-600 text-white font-medium transition";
    renderFilteredAlbums();
  } else {
    trackBtn.className = "px-3 py-1 rounded bg-indigo-600 text-white font-medium transition";
    if (allTracksData.length > 0) {
      applyFilters();
    } else {
      loadTracksData();
    }
  }
}

async function loadLibraryArtists() {
  const search = (document.getElementById("artist-filter-search")?.value || "").trim();
  const gen = document.getElementById("artist-filter-gen")?.value || "";
  const status = document.getElementById("artist-filter-status")?.value || "";
  const artistType = document.getElementById("artist-filter-type")?.value || "";

  const dir = artistSortAsc ? "asc" : "desc";
  const sortMap = {
    name: `name_${dir}`,
    generation: `gen_${dir}`,
    status: `status_${dir}`,
    album_count: `albums_${dir}`,
    track_count: `tracks_${dir}`,
    avg_rating: `avg_${dir}`,
  };
  const sortBy = sortMap[artistSortColumn] || "name_asc";

  try {
    let url = `/api/library/artists?search=${encodeURIComponent(search)}&sort_by=${sortBy}&page=${currentPage}&page_size=${pageSize}`;
    if (gen) url += `&generation=${encodeURIComponent(gen)}`;
    if (status) url += `&status=${encodeURIComponent(status)}`;
    if (artistType) url += `&artist_type=${encodeURIComponent(artistType)}`;

    const res = await fetch(url);
    const data = await res.json();
    const items = data.items || [];

    updatePaginationControls(data.total || 0);
    renderLibraryArtistsTable(items);
  } catch (err) {
    console.error("Failed to load library artists", err);
  }
}

function sortLibraryArtists(col) {
  if (artistSortColumn === col) {
    artistSortAsc = !artistSortAsc;
  } else {
    artistSortColumn = col;
    artistSortAsc = (col === 'name' || col === 'generation' || col === 'status');
  }
  currentPage = 1;
  loadLibraryArtists();
}

function renderLibraryArtistsTable(items) {
  const tbody = document.getElementById("library-artists-tbody");
  if (!tbody) return;

  if (!items || items.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" class="py-6 text-center text-slate-500">No artists match your criteria.</td></tr>`;
    return;
  }

  tbody.innerHTML = items.map(a => {
    const genBadge = a.generation ? getGenerationBadge(a.generation) : '--';
    const statusBadge = getStatusBadge(a.status);
    const scoreText = a.avg_rating !== null && a.avg_rating !== undefined ? parseFloat(a.avg_rating).toFixed(2) : '--';

    // Member Subtitle Logic (Inline format: " • X members")
    let memberSubtext = '';
    if ((a.artist_type || 'group') === 'group') {
      memberSubtext = formatGroupMemberCountBadge(a.active_member_count, a.total_member_count);
    }

    return `
      <tr class="hover:bg-slate-800/40">
        <td class="py-2.5 px-3">
          <div class="flex items-baseline whitespace-nowrap">
            <button type="button" onclick="directEditArtist(${a.id})" 
                    class="text-left font-semibold text-slate-200 hover:text-indigo-400 hover:underline transition">
              ${escapeHtml(a.name)}
            </button>
            ${memberSubtext}
          </div>
        </td>
        <td class="py-2.5 px-2">${genBadge}</td>
        <td class="py-2.5 px-2">${statusBadge}</td>
        <td class="py-2.5 px-2 text-center">
          <button type="button" onclick="filterAlbumsByArtist('${escapeJsArg(a.name)}')"
                  class="px-2 py-0.5 rounded bg-slate-900 hover:bg-slate-800 text-indigo-300 font-medium transition">
            ${a.album_count} albums ➔
          </button>
        </td>
        <td class="py-2.5 px-2 text-center">
          <button type="button" onclick="filterTracksByArtist('${escapeJsArg(a.name)}')"
                  class="text-slate-400 hover:text-slate-200 bg-slate-900 px-2 py-0.5 rounded border border-slate-800 transition">
            ${a.track_count} tracks ➔
          </button>
        </td>
        <td class="py-2.5 px-3 text-right font-mono font-bold text-amber-400">${scoreText}</td>
      </tr>
    `;
  }).join("");
}

let artistModalContext = 'library';

function openNewArtistModal(context = 'library') {
  artistModalContext = context;
  document.getElementById("edit-artist-id").value = "";
  document.getElementById("edit-artist-name").value = "";
  document.getElementById("edit-artist-debut").value = "";
  document.getElementById("edit-artist-gen").value = "";
  document.getElementById("edit-artist-status").value = "Active";
  document.getElementById("edit-artist-type").value = "solo"; // default to solo for quick soloist additions
  document.getElementById("edit-artist-members").value = "";
  document.getElementById("edit-artist-parent-groups").value = "";

  const titleEl = document.getElementById("artist-modal-title");
  if (titleEl) titleEl.textContent = "Create New Artist";

  toggleArtistTypeFields();
  document.getElementById("artist-modal").classList.remove("hidden");
  setTimeout(() => {
    const input = document.getElementById("edit-artist-name");
    if (input) input.focus();
  }, 50);
}

async function directEditArtist(artistId) {
  artistModalContext = 'library';
  const titleEl = document.getElementById("artist-modal-title");
  if (titleEl) titleEl.textContent = "Edit Artist Details";

  // 1. Reset all modal fields so old values never linger
  document.getElementById("edit-artist-id").value = "";
  document.getElementById("edit-artist-name").value = "Loading...";
  document.getElementById("edit-artist-debut").value = "";
  document.getElementById("edit-artist-gen").value = "";
  document.getElementById("edit-artist-members").value = "";

  // 2. Always fetch fresh artists cache to ensure newly added artists exist
  try {
    const res = await fetch("/api/artists");
    artistCache = await res.json();
  } catch (e) {
    console.error("Failed to refresh artists cache", e);
  }

  document.getElementById("artist-modal").classList.remove("hidden");
  startEditArtist(artistId);
}

async function fetchAlbum() {
  const artist = document.getElementById("search-artist").value.trim();
  const album = document.getElementById("search-album").value.trim();
  const status = document.getElementById("fetch-status");
  const fetchBtn = document.getElementById("fetch-btn");

  if (!artist || !album) return;
  status.className = "text-sm mt-3 text-amber-400";
  status.innerText = "Querying MusicBrainz...";
  status.classList.remove("hidden");
  fetchBtn.disabled = true;

  try {
    const res = await fetch(`/api/fetch?artist=${encodeURIComponent(artist)}&album=${encodeURIComponent(album)}`);
    if (!res.ok) throw new Error((await res.json()).detail || "Failed to fetch album");
    const data = await res.json();
    populateForm(data, null);
    status.className = "text-sm mt-3 text-emerald-400";
    status.innerText = "Metadata retrieved successfully!";
  } catch (err) {
    status.className = "text-sm mt-3 text-rose-400";
    status.innerText = `Error: ${err.message}`;
  } finally {
    fetchBtn.disabled = false;
  }
}

function openManualNewAlbum() {
  const artist = (document.getElementById("search-artist")?.value || "").trim();
  const album = (document.getElementById("search-album")?.value || "").trim();
  const status = document.getElementById("fetch-status");
  if (status) status.classList.add("hidden");

  // Clear any active bulk queue so staging behaves as a single manual release
  bulkQueue = [];
  currentQueueIdx = 0;

  populateForm({
    artist,
    title: album,
    release_date: "",
    album_type: "Single",
    mb_release_group_id: null,
    tracks: [
      {
        track_number: 1,
        title: album,
        rating: null,
        is_title_track: true,
        track_type: "core",
        notes: ""
      }
    ]
  }, null);

  document.getElementById("staging-section")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function fetchDiscography() {
  bulkArtist = document.getElementById("bulk-artist").value.trim();
  const status = document.getElementById("fetch-status");
  const searchBtn = document.getElementById("bulk-search-btn");

  if (!bulkArtist) return;
  status.className = "text-sm mt-3 text-amber-400";
  status.innerText = `Searching release groups for ${bulkArtist}...`;
  status.classList.remove("hidden");
  searchBtn.disabled = true;

  try {
    const res = await fetch(`/api/artist-discography?artist=${encodeURIComponent(bulkArtist)}`);
    if (!res.ok) throw new Error((await res.json()).detail || "Failed to load discography");
    const data = await res.json();
    renderDiscographyList(data.releases);
    status.classList.add("hidden");
  } catch (err) {
    status.className = "text-sm mt-3 text-rose-400";
    status.innerText = `Error: ${err.message}`;
  } finally {
    searchBtn.disabled = false;
  }
}

function renderDiscographyList(releases) {
  const container = document.getElementById("disco-list");
  container.innerHTML = "";

  releases.forEach((r) => { 
    const inLibrary = Boolean(r.in_library);
    const div = document.createElement("div");
    
    // Slightly dim row if already in library
    div.className = `flex items-center justify-between p-2 rounded transition ${
      inLibrary ? 'bg-slate-950/40 opacity-60 hover:opacity-100' : 'hover:bg-slate-900'
    }`;

    div.innerHTML = `
      <label class="flex items-center space-x-3 cursor-pointer select-none">
        <input type="checkbox" data-rg="${r.rg_id}" data-title="${escapeHtml(r.title)}" data-in-library="${inLibrary}"
               class="disco-checkbox h-4 w-4 rounded bg-slate-900 border-slate-700 text-indigo-600 focus:ring-0" 
               ${inLibrary ? '' : 'checked'} onchange="updateSelectedCount()">
        <span class="font-medium ${inLibrary ? 'text-slate-400' : 'text-slate-200'}">
          ${escapeHtml(r.title)}
        </span>
        ${inLibrary ? '<span class="text-[10px] bg-emerald-950/80 text-emerald-400 border border-emerald-800/80 px-1.5 py-0.2 rounded font-medium">In Library ✓</span>' : ''}
      </label>
      <div class="text-xs text-slate-500 flex items-center gap-2">
        <span class="bg-slate-900 px-2 py-0.5 rounded border border-slate-800">${escapeHtml(r.primary_type)}</span>
        <span class="font-mono">${r.release_date || 'No Date'}</span>
      </div>
    `;
    container.appendChild(div);
  });

  document.getElementById("disco-results").classList.remove("hidden");
  updateSelectedCount();
}

function updateSelectedCount() {
  const count = document.querySelectorAll(".disco-checkbox:checked").length;
  document.getElementById("selected-count").innerText = count;
}

function selectNewReleasesOnly() {
  document.querySelectorAll(".disco-checkbox").forEach(cb => {
    const inLibrary = cb.dataset.inLibrary === "true";
    cb.checked = !inLibrary;
  });
  updateSelectedCount();
}

function toggleAllReleases(select) {
  document.querySelectorAll(".disco-checkbox").forEach(cb => cb.checked = select);
  updateSelectedCount();
}

function startBulkQueue() {
  const selected = document.querySelectorAll(".disco-checkbox:checked");
  if (selected.length === 0) return;

  bulkQueue = Array.from(selected).map(cb => ({
    rg_id: cb.dataset.rg,
    title: cb.dataset.title
  }));

  currentQueueIdx = 0;
  document.getElementById("disco-results").classList.add("hidden");
  loadNextInQueue();
}

async function loadNextInQueue() {
  if (currentQueueIdx >= bulkQueue.length) {
    document.getElementById("queue-banner").classList.add("hidden");
    cancelEdit();
    alert("Bulk import queue complete!");
    loadLibrary();
    loadLibraryArtists();
    loadTracksData();
    initArtistSelector();
    loadWatchlist();
    return;
  }

  const item = bulkQueue[currentQueueIdx];
  const banner = document.getElementById("queue-banner");
  banner.classList.remove("hidden");
  document.getElementById("queue-index").innerText = currentQueueIdx + 1;
  document.getElementById("queue-total").innerText = bulkQueue.length;
  document.getElementById("queue-current-title").innerText = item.title;

  try {
    const res = await fetch(`/api/fetch-rg?rg_id=${item.rg_id}&artist=${encodeURIComponent(bulkArtist)}`);
    if (!res.ok) throw new Error("Could not load tracklist");
    const data = await res.json();
    populateForm(data, null);
    window.scrollTo({ top: banner.offsetTop - 20, behavior: 'smooth' });
  } catch (err) {
    alert(`Error loading ${item.title}: ${err.message}`);
    skipQueueItem();
  }
}

function skipQueueItem() {
  currentQueueIdx++;
  loadNextInQueue();
}

function populateForm(data, existingAlbumId) {
  document.getElementById("editing-album-id").value = existingAlbumId || "";
  document.getElementById("album-mbid").value = data.mb_release_group_id || "";
  document.getElementById("album-artist").value = data.artist || "";
  document.getElementById("album-title").value = data.title || "";
  document.getElementById("album-date").value = data.release_date || "";
  document.getElementById("album-type").value = data.album_type || "EP";

  const badge = document.getElementById("staging-badge");
  const title = document.getElementById("staging-title");
  if (existingAlbumId) {
    title.innerText = "2. Edit Saved Album & Track Ratings";
    badge.innerText = `Editing ID: ${existingAlbumId}`;
    badge.className = "text-xs bg-amber-950 text-amber-400 border border-amber-800 px-2.5 py-1 rounded-full font-mono";
  } else {
    title.innerText = "2. Review, Rate & Save";
    badge.innerText = "Staged";
    badge.className = "text-xs bg-indigo-950 text-indigo-400 border border-indigo-800 px-2.5 py-1 rounded-full font-mono";
  }

  const stagingSkipBtn = document.getElementById("staging-skip-btn");
  if (stagingSkipBtn) {
    if (bulkQueue.length > 0 && currentQueueIdx < bulkQueue.length) {
      stagingSkipBtn.classList.remove("hidden");
    } else {
      stagingSkipBtn.classList.add("hidden");
    }
  }

  const tbody = document.getElementById("track-rows");
  tbody.innerHTML = "";

  (data.tracks || []).forEach((track, index) => {
    const tr = createTrackRowElement({
      trackNumber: track.track_number || (index + 1),
      title: track.title,
      rating: track.rating,
      isTitleTrack: track.is_title_track,
      trackType: track.track_type,
      notes: track.notes
    });
    tbody.appendChild(tr);
  });

  document.getElementById("save-status")?.classList.add("hidden");
  document.getElementById("staging-section")?.classList.remove("hidden");

  const stagingDeleteBtn = document.getElementById("staging-delete-btn");
  if (stagingDeleteBtn) {
    if (existingAlbumId) {
      stagingDeleteBtn.classList.remove("hidden");
    } else {
      stagingDeleteBtn.classList.add("hidden");
    }
  }
}

function createTrackRowElement({
  trackNumber = 1,
  title = "",
  rating = null,
  isTitleTrack = false,
  trackType = "core",
  notes = "",
  placeholder = ""
} = {}) {
  const tr = document.createElement("tr");
  tr.className = "hover:bg-slate-800/50";
  const currentScore = (rating !== null && rating !== undefined) ? parseFloat(rating).toFixed(1) : "";
  const typeVal = trackType || "core";

  tr.innerHTML = `
    <td class="py-2 px-3 text-center">
      <input type="checkbox" class="track-include h-4 w-4 rounded bg-slate-950 border-slate-700 text-indigo-600 focus:ring-0 cursor-pointer" checked>
    </td>
    <td class="py-2 px-2 text-slate-400 font-mono text-xs">
      <div class="flex items-center justify-center gap-1.5">
        <span class="track-num-label w-4 text-right">${trackNumber}</span>
        <div class="flex flex-col text-[9px] leading-[9px] text-slate-500">
          <button type="button" onclick="moveTrackRow(this, -1)" class="hover:text-indigo-400 p-0.5" title="Move Up">▲</button>
          <button type="button" onclick="moveTrackRow(this, 1)" class="hover:text-indigo-400 p-0.5" title="Move Down">▼</button>
        </div>
      </div>
    </td>
    <td class="py-2 px-3">
      <input type="text" value="${escapeHtml(title)}" placeholder="${escapeHtml(placeholder)}" class="track-title w-full bg-transparent border-b border-transparent hover:border-slate-700 focus:border-indigo-500 focus:bg-slate-950 px-1 py-0.5 rounded text-sm text-slate-100">
    </td>
    <td class="py-2 px-3">
      <select class="track-rating w-full bg-slate-950 border border-slate-700 rounded px-2 py-1 text-sm text-slate-200">
        <option value="" class="text-slate-500 font-normal">Unrated</option>
        <option value="5.0" class="text-purple-400 font-bold" ${currentScore === "5.0" ? "selected" : ""}>5.0 (Flawless)</option>
        <option value="4.5" class="text-indigo-400 font-bold" ${currentScore === "4.5" ? "selected" : ""}>4.5 (Great)</option>
        <option value="4.0" class="text-indigo-400 font-bold" ${currentScore === "4.0" ? "selected" : ""}>4.0 (Solid)</option>
        <option value="3.5" class="text-sky-400 font-bold" ${currentScore === "3.5" ? "selected" : ""}>3.5 (Good)</option>
        <option value="3.0" class="text-sky-400 font-bold" ${currentScore === "3.0" ? "selected" : ""}>3.0 (Average)</option>
        <option value="2.5" class="text-teal-400 font-bold" ${currentScore === "2.5" ? "selected" : ""}>2.5 (Below Avg)</option>
        <option value="2.0" class="text-teal-400 font-bold" ${currentScore === "2.0" ? "selected" : ""}>2.0 (Mediocre)</option>
        <option value="1.5" class="text-slate-400 font-normal" ${currentScore === "1.5" ? "selected" : ""}>1.5 (Poor)</option>
        <option value="1.0" class="text-slate-400 font-normal" ${currentScore === "1.0" ? "selected" : ""}>1.0 (Skip)</option>
      </select>
    </td>
    <td class="py-2 px-2 text-center">
      <input type="checkbox" class="track-is-title h-4 w-4 rounded bg-slate-950 border-slate-700 text-indigo-600 focus:ring-0 cursor-pointer" ${isTitleTrack ? "checked" : ""}>
    </td>
    <td class="py-2 px-2">
      <select class="track-type w-full bg-slate-950 border border-slate-700 rounded px-1.5 py-1 text-xs text-slate-200">
        <option value="core" ${typeVal === 'core' ? 'selected' : ''}>Core</option>
        <option value="solo" ${typeVal === 'solo' ? 'selected' : ''}>Solo</option>
        <option value="unit" ${typeVal === 'unit' ? 'selected' : ''}>Unit</option>
        <option value="alt_lang" ${typeVal === 'alt_lang' ? 'selected' : ''}>Alt Lang</option>
        <option value="non_canon" ${typeVal === 'non_canon' ? 'selected' : ''}>Remix / Inst / Skit</option>
      </select>
    </td>
    <td class="py-2 px-3">
      <input type="text" placeholder="Notes" value="${escapeHtml(notes || '')}" class="track-notes w-full bg-slate-950 border border-slate-800 rounded px-2 py-1 text-xs text-slate-200">
    </td>
  `;
  return tr;
}

function addNewTrackRow(title = "", rating = null, isTitle = false, trackType = "core", notes = "") {
  const tbody = document.getElementById("track-rows");
  const nextNum = tbody.querySelectorAll("tr").length + 1;
  const tr = createTrackRowElement({
    trackNumber: nextNum,
    title,
    rating,
    isTitleTrack: isTitle,
    trackType,
    notes,
    placeholder: "Track title..."
  });

  tbody.appendChild(tr);
  renumberTrackRows();

  const titleInput = tr.querySelector(".track-title");
  if (titleInput) titleInput.focus();
}

function moveTrackRow(button, direction) {
  const row = button.closest("tr");
  if (!row) return;

  if (direction === -1) {
    const prevRow = row.previousElementSibling;
    if (prevRow) row.parentNode.insertBefore(row, prevRow);
  } else if (direction === 1) {
    const nextRow = row.nextElementSibling;
    if (nextRow) row.parentNode.insertBefore(nextRow, row);
  }

  renumberTrackRows();
}

function renumberTrackRows() {
  const rows = document.querySelectorAll("#track-rows tr");
  rows.forEach((row, idx) => {
    const numLabel = row.querySelector(".track-num-label");
    if (numLabel) numLabel.innerText = idx + 1;
  });
}

async function saveToDatabase() {
  const saveBtn = document.getElementById("save-btn");
  const status = document.getElementById("save-status");
  saveBtn.disabled = true;

  const albumIdVal = document.getElementById("editing-album-id").value;
  const rows = document.querySelectorAll("#track-rows tr");
  const finalTracks = [];
  let seq = 1;

  try {
    rows.forEach(row => {
      const includeCheckbox = row.querySelector(".track-include");
      if (includeCheckbox && !includeCheckbox.checked) return;

      const ratingEl = row.querySelector(".track-rating");
      const ratingVal = ratingEl ? ratingEl.value : "";
      const isTitleEl = row.querySelector(".track-is-title");
      const isTitleVal = isTitleEl ? isTitleEl.checked : false;
      const typeEl = row.querySelector(".track-type");
      const typeVal = typeEl ? typeEl.value : "core";
      const titleEl = row.querySelector(".track-title");
      const titleVal = titleEl ? titleEl.value.trim() : "";
      const notesEl = row.querySelector(".track-notes");
      const notesVal = notesEl ? notesEl.value.trim() : null;

      finalTracks.push({
        track_number: seq++,
        title: titleVal,
        rating: ratingVal ? parseFloat(ratingVal) : null,
        is_title_track: isTitleVal,
        track_type: typeVal || "core",
        notes: notesVal || null
      });
    });

    const payload = {
      album_id: albumIdVal ? parseInt(albumIdVal) : null,
      artist: document.getElementById("album-artist").value.trim(),
      title: document.getElementById("album-title").value.trim(),
      release_date: document.getElementById("album-date").value.trim() || null,
      album_type: document.getElementById("album-type").value,
      mb_release_group_id: document.getElementById("album-mbid").value.trim() || null,
      tracks: finalTracks
    };

    const res = await fetch("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}));
      throw new Error(errorData.detail || `Server returned error ${res.status}`);
    }

    loadLibrary();
    loadLibraryArtists();
    loadTracksData();
    initArtistSelector();
    loadWatchlist();

    if (bulkQueue.length > 0 && currentQueueIdx < bulkQueue.length) {
      currentQueueIdx++;
      loadNextInQueue();
    } else {
      cancelEdit(); // Auto-closes the album modal once saved
    }
  } catch (err) {
    status.className = "text-sm text-rose-400 mt-2";
    status.innerText = `Error: ${err.message}`;
    status.classList.remove("hidden");
  } finally {
    saveBtn.disabled = false;
  }
}

async function editAlbum(albumId) {
  try {
    const res = await fetch(`/api/albums/${albumId}`);
    if (!res.ok) throw new Error("Could not load album details");
    const data = await res.json();
    
    populateForm(data, albumId);
  } catch (err) {
    alert(err.message);
  }
}

async function deleteAlbum(albumId) {
  if (!confirm(`Are you sure you want to delete album #${albumId} and its ratings?`)) return;
  try {
    const res = await fetch(`/api/albums/${albumId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error("Delete failed");
    loadLibrary();
    loadLibraryArtists();
    loadTracksData();
    initArtistSelector();
    loadWatchlist();
    cancelEdit();
  } catch (err) {
    alert(err.message);
  }
}

function deleteCurrentStagedAlbum() {
  const albumId = document.getElementById("editing-album-id").value;
  if (albumId) {
    deleteAlbum(parseInt(albumId, 10));
  }
}

function cancelEdit() {
  document.getElementById("staging-section").classList.add("hidden");
  document.getElementById("editing-album-id").value = "";
  
  // If a bulk queue was active, clear and hide it
  if (bulkQueue.length > 0) {
    bulkQueue = [];
    currentQueueIdx = 0;
    document.getElementById("queue-banner")?.classList.add("hidden");
  }
}

async function loadLibrary(render = true) {
  const container = document.getElementById("library-albums-container");
  if (!container) return;

  try {
    const res = await fetch("/api/library");
    allAlbumsData = await res.json();
    if (render && currentLibraryView === 'albums') {
      renderFilteredAlbums();
    }
  } catch (e) {
    container.innerHTML = `<p class="text-rose-400">Failed to load library.</p>`;
  }
}

function getReleaseDateCutoffs() {
  const now = new Date();
  const currentYearStr = String(now.getFullYear());
  const prevYearStr = String(now.getFullYear() - 1);
  const d60 = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 60);
  const d60Str = `${d60.getFullYear()}-${String(d60.getMonth() + 1).padStart(2, '0')}-${String(d60.getDate()).padStart(2, '0')}`;
  const d6m = new Date(now.getFullYear(), now.getMonth() - 6, now.getDate());
  const d6mStr = `${d6m.getFullYear()}-${String(d6m.getMonth() + 1).padStart(2, '0')}-${String(d6m.getDate()).padStart(2, '0')}`;
  return { currentYearStr, prevYearStr, d60Str, d6mStr };
}

function matchesReleaseDateFilter(releaseDate, dateFilter, cutoffs) {
  if (!dateFilter || dateFilter === "all") return true;
  if (!releaseDate) return false;
  if (dateFilter === "60d") return releaseDate >= cutoffs.d60Str;
  if (dateFilter === "6m") return releaseDate >= cutoffs.d6mStr;
  if (dateFilter === "current_year") return releaseDate.startsWith(cutoffs.currentYearStr);
  if (dateFilter === "prev_year") return releaseDate.startsWith(cutoffs.prevYearStr);
  return true;
}

function renderFilteredAlbums() {
  const tbody = document.getElementById("library-albums-tbody");
  if (!tbody) return;

  const search = (document.getElementById("album-filter-search")?.value || "").toLowerCase().trim();
  const artist = document.getElementById("album-filter-artist")?.value || "";
  const rating = document.getElementById("album-filter-rating")?.value || "all";
  const albumType = document.getElementById("album-filter-type")?.value || "";
  const dateFilter = document.getElementById("album-filter-date")?.value || "all";
  const cutoffs = getReleaseDateCutoffs();

  const filtered = allAlbumsData.filter(item => {
    if (artist && item.artist !== artist) return false;
    if (albumType && item.album_type !== albumType) return false;
    if (!matchesReleaseDateFilter(item.release_date, dateFilter, cutoffs)) return false;

    if (search) {
      const matchTitle = (item.album_title || "").toLowerCase().includes(search);
      const matchArtist = (item.artist || "").toLowerCase().includes(search);
      if (!matchTitle && !matchArtist) return false;
    }

    const score = item.avg_rating !== null && item.avg_rating !== undefined ? parseFloat(item.avg_rating) : null;
    if (rating === "5.0" && score !== 5.0) {
      return false;
    } else if (rating === "4.0" && (score === null || score < 4.0)) {
      return false;
    } else if (rating === "3.0" && (score === null || score < 3.0)) {
      return false;
    } else if (rating === "unrated" && score !== null) {
      return false;
    }

    return true;
  });

  // Sort albums by active column
  sortRecords(filtered, albumSortColumn, albumSortAsc);

  // Update Pagination state
  updatePaginationControls(filtered.length);

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" class="py-6 text-center text-slate-500">No albums match your filter criteria.</td></tr>`;
    return;
  }

  // Slice to current page window
  const startIndex = (currentPage - 1) * pageSize;
  const paginatedItems = filtered.slice(startIndex, startIndex + pageSize);

  tbody.innerHTML = paginatedItems.map(item => {
    const scoreText = item.avg_rating !== null && item.avg_rating !== undefined ? parseFloat(item.avg_rating).toFixed(2) : 'Unrated';
    const typeBadge = `<span class="text-[10px] bg-slate-900 text-indigo-400 border border-slate-800 px-1.5 py-0.5 rounded font-mono uppercase">${escapeHtml(item.album_type || 'EP')}</span>`;

    return `
      <tr class="hover:bg-slate-800/40">
        <td class="py-2.5 px-3">
          <button type="button" onclick="editAlbum(${item.album_id})" 
                  class="font-semibold text-indigo-400 hover:text-indigo-300 hover:underline text-left transition">
            ${escapeHtml(item.album_title)}
          </button>
        </td>
        <td class="py-2.5 px-3">
          <button type="button" onclick="filterAlbumsByArtist('${escapeJsArg(item.artist)}')" 
                  class="text-left font-medium text-slate-200 hover:text-indigo-400 hover:underline transition">
            ${escapeHtml(item.artist)}
          </button>
        </td>
        <td class="py-2.5 px-2 text-center">${typeBadge}</td>
        <td class="py-2.5 px-3 font-mono text-slate-500">${item.release_date || 'No Date'}</td>
        <td class="py-2.5 px-2 text-center">
          <button type="button" onclick="filterTracksByAlbum('${escapeJsArg(item.album_title)}', '${escapeJsArg(item.artist)}')"
                  class="text-slate-400 hover:text-slate-200 bg-slate-900 px-2 py-0.5 rounded border border-slate-800 transition">
            ${item.track_count} tracks ➔
          </button>
        </td>
        <td class="py-2.5 px-3 text-right font-mono font-bold text-amber-400">${scoreText}</td>
      </tr>
    `;
  }).join("");
}

function filterAlbumsByArtist(artistName) {
  const albumSearch = document.getElementById("album-filter-search");
  if (albumSearch) albumSearch.value = ""; // Clear any leftover text search
  const artistSelect = document.getElementById("album-filter-artist");
  if (artistSelect) artistSelect.value = artistName;
  switchLibraryView('albums');
}

function filterTracksByAlbum(albumTitle, artistName) {
  const artistSelect = document.getElementById("track-filter-artist");
  if (artistSelect && artistName) artistSelect.value = artistName;
  const trackSearch = document.getElementById("track-filter-search");
  if (trackSearch) trackSearch.value = albumTitle;
  const ratingSelect = document.getElementById("track-filter-rating");
  if (ratingSelect) ratingSelect.value = "all";
  const typeSelect = document.getElementById("track-filter-type");
  if (typeSelect) typeSelect.value = "";
  const dateSelect = document.getElementById("track-filter-date");
  if (dateSelect) dateSelect.value = "all";
  switchLibraryView('tracks');
}

async function filterTracksByArtist(artistName, options = {}) {
  const { search = "", trackType = "" } = options;
  if (allTracksData.length === 0) {
    await loadTracksData();
  }
  const trackSearch = document.getElementById("track-filter-search");
  if (trackSearch) trackSearch.value = search;
  const artistSelect = document.getElementById("track-filter-artist");
  if (artistSelect && artistName) artistSelect.value = artistName;
  const ratingSelect = document.getElementById("track-filter-rating");
  if (ratingSelect) ratingSelect.value = "all";
  const typeSelect = document.getElementById("track-filter-type");
  if (typeSelect) typeSelect.value = trackType;
  const dateSelect = document.getElementById("track-filter-date");
  if (dateSelect) dateSelect.value = "all";

  switchMainView('management');
  switchLibrarySubView('explorer');
  switchLibraryView('tracks');
}

function jumpToLibraryAlbum(artistName, albumTitle) {
  return filterTracksByArtist(artistName, { search: albumTitle || "" });
}

let currentSingleArtist = "";
let analyticsArtistsCache = [];
let singleArtistHighlightedIndex = -1;

async function initArtistSelector() {
  try {
    const res = await fetch("/api/analytics/artists");
    analyticsArtistsCache = await res.json();
    
    const input = document.getElementById("analytics-artist-input");
    if (!input) return;

    if (analyticsArtistsCache.length === 0) {
      input.value = "";
      currentSingleArtist = "";
      return;
    }

    // Preserve current selection if still valid; otherwise default to first artist
    if (!currentSingleArtist || !analyticsArtistsCache.some(a => a.toLowerCase() === currentSingleArtist.toLowerCase())) {
      currentSingleArtist = analyticsArtistsCache[0];
    }
    
    input.value = currentSingleArtist;
    renderAnalytics();
  } catch (e) {
    console.error("Failed to load artists for analytics", e);
  }
}

function renderSingleArtistDropdown(filterText = "") {
  const dropdown = document.getElementById("single-artist-dropdown");
  if (!dropdown) return;

  const cleanFilter = filterText.trim().toLowerCase();
  const matched = cleanFilter
    ? analyticsArtistsCache.filter(a => a.toLowerCase().includes(cleanFilter))
    : analyticsArtistsCache;

  if (matched.length === 0) {
    dropdown.innerHTML = `<div class="px-3 py-2 text-xs text-slate-500 italic text-center">No matching artists</div>`;
    singleArtistHighlightedIndex = -1;
    return;
  }

  dropdown.innerHTML = matched.map((artist, idx) => {
    const isSelected = currentSingleArtist && artist.toLowerCase() === currentSingleArtist.toLowerCase();
    const isHighlighted = idx === singleArtistHighlightedIndex;
    return `
      <div class="combobox-item px-3 py-1.5 cursor-pointer text-sm transition flex items-center justify-between ${
        isSelected ? 'is-selected-artist bg-indigo-950/70 text-indigo-300 font-semibold' : 'text-slate-300 hover:bg-slate-800/80 hover:text-slate-100'
      } ${isHighlighted ? 'ring-1 ring-indigo-500 bg-slate-800/80' : ''}"
           data-artist="${escapeHtml(artist)}"
           data-index="${idx}"
           onmousedown="selectSingleArtist('${escapeHtml(artist).replace(/'/g, "\\'")}')">
        <span class="truncate">${escapeHtml(artist)}</span>
        ${isSelected ? '<span class="text-xs text-indigo-400 font-bold ml-2">✓</span>' : ''}
      </div>
    `;
  }).join("");

  // Auto-scroll to selected artist if opening without filter
  if (!cleanFilter && currentSingleArtist) {
    const selectedEl = dropdown.querySelector(".is-selected-artist");
    if (selectedEl) {
      selectedEl.scrollIntoView({ block: "nearest" });
    }
  }
}

function openSingleArtistDropdown(filterText = "") {
  const dropdown = document.getElementById("single-artist-dropdown");
  if (!dropdown) return;
  singleArtistHighlightedIndex = -1;
  renderSingleArtistDropdown(filterText);
  dropdown.classList.remove("hidden");
}

function closeSingleArtistDropdown() {
  const dropdown = document.getElementById("single-artist-dropdown");
  if (dropdown) {
    dropdown.classList.add("hidden");
    singleArtistHighlightedIndex = -1;
  }
}

function toggleSingleArtistDropdown(event) {
  if (event) event.stopPropagation();
  const dropdown = document.getElementById("single-artist-dropdown");
  if (!dropdown) return;
  if (dropdown.classList.contains("hidden")) {
    openSingleArtistDropdown("");
    const input = document.getElementById("analytics-artist-input");
    if (input) input.focus();
  } else {
    closeSingleArtistDropdown();
  }
}

function selectSingleArtist(artistName) {
  currentSingleArtist = artistName;
  const input = document.getElementById("analytics-artist-input");
  if (input) {
    input.value = artistName;
  }
  closeSingleArtistDropdown();
  renderAnalytics();
}

function onSingleArtistInputFocus(input) {
  if (input) {
    input.select();
  }
}

function onSingleArtistInput(val) {
  const cleanVal = val.trim();
  openSingleArtistDropdown(cleanVal);

  if (!cleanVal) {
    clearSingleArtistView();
    return;
  }

  // Case-insensitive match against library artists
  const matched = analyticsArtistsCache.find(a => a.toLowerCase() === cleanVal.toLowerCase());
  if (matched) {
    currentSingleArtist = matched;
    renderAnalytics();
  } else {
    clearSingleArtistView();
  }
}

function onSingleArtistKeyDown(event) {
  const dropdown = document.getElementById("single-artist-dropdown");
  const isDropdownOpen = dropdown && !dropdown.classList.contains("hidden");

  if (event.key === "Escape" || event.key === "Tab") {
    closeSingleArtistDropdown();
    return;
  }

  if (event.key === "ArrowDown") {
    event.preventDefault();
    if (!isDropdownOpen) {
      openSingleArtistDropdown("");
      return;
    }
    const items = dropdown.querySelectorAll(".combobox-item");
    if (items.length === 0) return;
    singleArtistHighlightedIndex = (singleArtistHighlightedIndex + 1) % items.length;
    updateHighlightedDropdownItem(items);
    return;
  }

  if (event.key === "ArrowUp") {
    event.preventDefault();
    if (!isDropdownOpen) {
      openSingleArtistDropdown("");
      return;
    }
    const items = dropdown.querySelectorAll(".combobox-item");
    if (items.length === 0) return;
    singleArtistHighlightedIndex = (singleArtistHighlightedIndex - 1 + items.length) % items.length;
    updateHighlightedDropdownItem(items);
    return;
  }

  if (event.key === "Enter") {
    if (isDropdownOpen) {
      event.preventDefault();
      const items = dropdown.querySelectorAll(".combobox-item");
      if (singleArtistHighlightedIndex >= 0 && singleArtistHighlightedIndex < items.length) {
        const artist = items[singleArtistHighlightedIndex].getAttribute("data-artist");
        if (artist) selectSingleArtist(artist);
      } else if (items.length > 0) {
        const artist = items[0].getAttribute("data-artist");
        if (artist) selectSingleArtist(artist);
      }
    }
  }
}

function updateHighlightedDropdownItem(items) {
  items.forEach((item, idx) => {
    if (idx === singleArtistHighlightedIndex) {
      item.classList.add("ring-1", "ring-indigo-500", "bg-slate-800/80");
      item.scrollIntoView({ block: "nearest" });
    } else {
      item.classList.remove("ring-1", "ring-indigo-500", "bg-slate-800/80");
    }
  });
}

function clearSingleArtistSearch() {
  const input = document.getElementById("analytics-artist-input");
  if (input) {
    input.value = "";
    input.focus();
  }
  closeSingleArtistDropdown();
  clearSingleArtistView();
}

function clearSingleArtistView() {
  currentSingleArtist = "";
  
  // Clear badges
  const badgeContainer = document.getElementById("analytics-artist-badges");
  if (badgeContainer) badgeContainer.innerHTML = "";

  // Reset Card 1: Q-WAR Sabermetrics
  const compactedQwarEl = document.getElementById("stat-compacted-qwar");
  if (compactedQwarEl) compactedQwarEl.innerText = "--";
  const qwarRateEl = document.getElementById("stat-qwar-rate");
  if (qwarRateEl) qwarRateEl.innerText = "--";
  updateQwarLinearBars(null, null);

  // Reset Card 2: Score & Hit Rate
  const coreAvgEl = document.getElementById("label-core-avg");
  if (coreAvgEl) coreAvgEl.textContent = "Artist Only:";
  document.getElementById("stat-group-avg").innerText = "--";
  document.getElementById("stat-group-hit-rate").innerText = "(--%)";
  document.getElementById("stat-overall-avg").innerText = "--";
  document.getElementById("stat-overall-hit-rate").innerText = "(--%)";

  // Reset Card 3: Perfect 5s
  const corePerfEl = document.getElementById("label-core-perfect");
  if (corePerfEl) corePerfEl.textContent = "Artist Only:";
  document.getElementById("stat-group-perfect").innerText = "--";
  document.getElementById("stat-group-perfect-pct").innerText = "(--%)";
  document.getElementById("stat-overall-perfect").innerText = "--";
  document.getElementById("stat-overall-perfect-pct").innerText = "(--%)";

  // Reset Card 4: Volume & Productivity
  const rawAlbumsEl = document.getElementById("stat-raw-albums");
  if (rawAlbumsEl) rawAlbumsEl.innerText = "--";
  const totalAlbumsEl = document.getElementById("stat-total-albums");
  if (totalAlbumsEl) totalAlbumsEl.innerText = "--";
  const rawTracksEl = document.getElementById("stat-raw-tracks");
  if (rawTracksEl) rawTracksEl.innerText = "--";
  const totalTracksEl = document.getElementById("stat-total-tracks");
  if (totalTracksEl) totalTracksEl.innerText = "--";
  const annualRateEl = document.getElementById("stat-annual-rate");
  if (annualRateEl) annualRateEl.innerText = "--";
  const activeYearsEl = document.getElementById("stat-active-years");
  if (activeYearsEl) activeYearsEl.innerText = "--";

  // Reset Full-Width Roster / Solo Card
  const rosterTitle = document.getElementById("roster-card-title");
  const rosterCount = document.getElementById("roster-card-count");
  const rosterContainer = document.getElementById("roster-card-content");
  if (rosterTitle) rosterTitle.innerText = "Group Members";
  if (rosterCount) rosterCount.innerText = "";
  if (rosterContainer) {
    rosterContainer.className = "flex items-center py-1";
    rosterContainer.innerHTML = `<span class="text-xs text-slate-600 italic">No artist selected</span>`;
  }

  // Destroy or Clear Chart
  if (scoreChartInstance) {
    scoreChartInstance.destroy();
    scoreChartInstance = null;
  }

  // Reset Title vs B-Side
  document.getElementById("title-bside-summary").innerText = "-- titles / -- b-sides";
  document.getElementById("stat-title-count").innerText = "0 tracks";
  document.getElementById("stat-title-avg").innerText = "--";
  document.getElementById("stat-title-hit").innerText = "--%";
  document.getElementById("stat-title-perfect").innerText = "0";

  document.getElementById("stat-bside-count").innerText = "0 tracks";
  document.getElementById("stat-bside-avg").innerText = "--";
  document.getElementById("stat-bside-hit").innerText = "--%";
  document.getElementById("stat-bside-perfect").innerText = "0";

  // Reset Histogram
  document.getElementById("histogram-total-tracks").innerText = "0 Rated Tracks";
  const histContainer = document.getElementById("histogram-bars-container");
  if (histContainer) histContainer.innerHTML = "";

  // Clear Top Albums
  const topAlbumsContainer = document.getElementById("top-albums-container");
  if (topAlbumsContainer) {
    topAlbumsContainer.innerHTML = `<p class="text-slate-600 text-xs py-2">Select an artist above to display top rated albums.</p>`;
  }
}

async function renderAnalytics() {
  const artist = currentSingleArtist;
  if (!artist) return;

  try {
    const res = await fetch(`/api/analytics/artist-summary?artist=${encodeURIComponent(artist)}`);
    const data = await res.json();

    // Box 1: Q-WAR Sabermetrics
    const compactedQwarEl = document.getElementById("stat-compacted-qwar");
    const compactedVal = (data.compacted_qwar !== null && data.compacted_qwar !== undefined)
      ? parseFloat(data.compacted_qwar)
      : null;
    if (compactedQwarEl) {
      compactedQwarEl.innerText = compactedVal !== null ? compactedVal.toFixed(2) : "--";
    }

    const qwarRateEl = document.getElementById("stat-qwar-rate");
    const rateVal = (data.qwar_per_track !== null && data.qwar_per_track !== undefined)
      ? parseFloat(data.qwar_per_track)
      : null;
    if (qwarRateEl) {
      qwarRateEl.innerText = rateVal !== null ? rateVal.toFixed(2) : "--";
    }

    updateQwarLinearBars(compactedVal, rateVal);

    // Box 2: Score & Hit Rate (Dynamic label: "Artist Only:" for Soloists, "Group Only:" for Groups)
    const isSolo = (data.artist_type === "solo");
    const coreLabel = isSolo ? "Artist Only:" : "Group Only:";
    const coreAvgEl = document.getElementById("label-core-avg");
    if (coreAvgEl) coreAvgEl.textContent = coreLabel;
    const corePerfEl = document.getElementById("label-core-perfect");
    if (corePerfEl) corePerfEl.textContent = coreLabel;

    document.getElementById("stat-group-avg").innerText = data.group_avg ? parseFloat(data.group_avg).toFixed(2) : "--";
    document.getElementById("stat-group-hit-rate").innerText = (data.group_hit_rate !== null && data.group_hit_rate !== undefined) ? `(${data.group_hit_rate}%)` : "(--%)";
    document.getElementById("stat-overall-avg").innerText = data.overall_avg ? parseFloat(data.overall_avg).toFixed(2) : "--";
    document.getElementById("stat-overall-hit-rate").innerText = (data.overall_hit_rate !== null && data.overall_hit_rate !== undefined) ? `(${data.overall_hit_rate}%)` : "(--%)";

    // Box 3: Perfect 5s
    document.getElementById("stat-group-perfect").innerText = data.group_perfect;
    document.getElementById("stat-group-perfect-pct").innerText = `(${data.group_perfect_pct}%)`;
    document.getElementById("stat-overall-perfect").innerText = data.overall_perfect;
    document.getElementById("stat-overall-perfect-pct").innerText = `(${data.overall_perfect_pct}%)`;

    // Box 4: Volume & Productivity
    const rawAlbumsEl = document.getElementById("stat-raw-albums");
    if (rawAlbumsEl) rawAlbumsEl.innerText = data.raw_total_albums ?? "--";
    const totalAlbumsEl = document.getElementById("stat-total-albums");
    if (totalAlbumsEl) totalAlbumsEl.innerText = data.plotted_albums_count ?? (data.albums ? data.albums.length : "--");

    const rawTracksEl = document.getElementById("stat-raw-tracks");
    if (rawTracksEl) rawTracksEl.innerText = data.raw_total_tracks ?? "--";
    const totalTracksEl = document.getElementById("stat-total-tracks");
    if (totalTracksEl) totalTracksEl.innerText = data.total_catalog_tracks ?? data.total_rated_tracks ?? "--";

    const annualRateEl = document.getElementById("stat-annual-rate");
    if (annualRateEl) annualRateEl.innerText = data.tracks_per_year ?? "--";
    const activeYearsEl = document.getElementById("stat-active-years");
    if (activeYearsEl) activeYearsEl.innerText = `${data.active_years ?? "--"}`;

    // Title Tracks vs. B-Sides Card
    const tb = data.title_bsides || {};
    document.getElementById("title-bside-summary").innerText = `${tb.title_count || 0} titles / ${tb.bside_count || 0} b-sides`;
    
    document.getElementById("stat-title-count").innerText = `${tb.title_count || 0} trk`;
    document.getElementById("stat-title-avg").innerText = tb.title_avg !== null && tb.title_avg !== undefined ? parseFloat(tb.title_avg).toFixed(2) : "--";
    document.getElementById("stat-title-hit").innerText = `${tb.title_hit_rate || 0}%`;
    document.getElementById("stat-title-perfect").innerText = tb.title_perfect || 0;

    document.getElementById("stat-bside-count").innerText = `${tb.bside_count || 0} trk`;
    document.getElementById("stat-bside-avg").innerText = tb.bside_avg !== null && tb.bside_avg !== undefined ? parseFloat(tb.bside_avg).toFixed(2) : "--";
    document.getElementById("stat-bside-hit").innerText = `${tb.bside_hit_rate || 0}%`;
    document.getElementById("stat-bside-perfect").innerText = tb.bside_perfect || 0;

    // Score Distribution Histogram
    const totalRated = data.total_rated_tracks || 0;
    document.getElementById("histogram-total-tracks").innerText = `${totalRated} Rated Tracks`;
    const histContainer = document.getElementById("histogram-bars-container");
    
    const scoreColors = {
      "5★": { bar: "bg-purple-500", text: "text-purple-400" },
      "4★": { bar: "bg-indigo-500", text: "text-indigo-400" },
      "3★": { bar: "bg-sky-500", text: "text-sky-400" },
      "2★": { bar: "bg-teal-500", text: "text-teal-400" },
      "1★": { bar: "bg-slate-600", text: "text-slate-400" }
    };

    if (histContainer && data.histogram) {
      histContainer.innerHTML = data.histogram.map(row => {
        const conf = scoreColors[row.rating] || { bar: "bg-slate-500", text: "text-slate-400" };
        return `
          <div class="flex items-center gap-2 text-xs">
            <span class="w-7 font-mono font-bold ${conf.text} text-right">${row.rating}</span>
            <div class="flex-1 bg-slate-900 rounded-full h-2 overflow-hidden border border-slate-800/80">
              <div class="${conf.bar} h-full rounded-full transition-all duration-300" style="width: ${row.pct}%"></div>
            </div>
            <div class="w-16 font-mono text-[11px] text-slate-400 text-right">
              <span class="font-semibold text-slate-200">${row.count}</span>
              <span class="text-slate-600">(${row.pct}%)</span>
            </div>
          </div>
        `;
      }).join("");
    }

    // Top Albums
    const topAlbumsContainer = document.getElementById("top-albums-container");
    if (!data.top_albums || data.top_albums.length === 0) {
      topAlbumsContainer.innerHTML = `<p class="text-slate-500">No rated EPs or LPs cataloged yet.</p>`;
    } else {
      topAlbumsContainer.innerHTML = data.top_albums.map(a => `
        <button type="button"
                onclick="jumpToLibraryAlbum('${escapeJsArg(currentSingleArtist)}', '${escapeJsArg(a.album_title)}')"
                title="View tracks for ${escapeHtml(a.album_title)} in Library Explorer"
                class="w-full text-left bg-slate-900 hover:bg-slate-800/80 p-2.5 rounded border border-slate-800 hover:border-indigo-500/60 flex justify-between items-center transition">
          <div>
            <span class="font-bold text-slate-200">${escapeHtml(a.album_title)}</span>
            <span class="ml-1 text-[10px] bg-slate-950 text-indigo-400 border border-slate-800 px-1.5 py-0.2 rounded">${escapeHtml(a.album_type)}</span>
            <div class="text-[11px] text-slate-500">${a.release_date || 'No Date'} • ${a.track_count} tracks</div>
          </div>
          <span class="font-mono font-bold text-amber-400 text-sm">${parseFloat(a.avg_score).toFixed(2)}</span>
        </button>
      `).join("");
    }

    // Badges next to Artist Pulldown
    const badgeContainer = document.getElementById("analytics-artist-badges");
    if (badgeContainer) {
      const statusBadge = getStatusBadge(data.status);
      const genBadge = data.generation ? getGenerationBadge(data.generation) : "";
      badgeContainer.innerHTML = `${genBadge} ${statusBadge}`;
    }

    // Populate Dynamic Full-Width Metric Card: Members or Parent Group
    const rosterTitle = document.getElementById("roster-card-title");
    const rosterCount = document.getElementById("roster-card-count");
    const rosterContainer = document.getElementById("roster-card-content");

    if (data.artist_type === "solo") {
      rosterTitle.innerText = "Group Affiliations";
      const parentGroups = data.parent_groups || [];
      rosterCount.innerText = parentGroups.length > 0 ? `${parentGroups.length} group${parentGroups.length === 1 ? '' : 's'}` : "Soloist";

      if (parentGroups.length === 0) {
        rosterContainer.className = "flex items-center py-1";
        rosterContainer.innerHTML = `<span class="text-xs text-slate-500 italic">Independent Solo Artist</span>`;
      } else {
        rosterContainer.className = "flex flex-wrap items-center gap-2 py-0.5";
        rosterContainer.innerHTML = parentGroups.map(pg => renderParentGroupBadge(pg, false)).join("");
      }
    } else {
      // Group Roster
      rosterTitle.innerText = "Group Members";
      const members = data.members || [];
      rosterCount.innerText = members.length > 0 ? `${members.length} members` : "";
      
      if (members.length === 0) {
        rosterContainer.className = "flex items-center py-1";
        rosterContainer.innerHTML = `<span class="text-xs text-slate-500 italic">No roster configured in Manage Artists</span>`;
      } else {
        rosterContainer.className = "flex flex-wrap items-center gap-1.5 py-0.5";
        rosterContainer.innerHTML = members.map(m => {
          const isFormer = Boolean(m.is_former);
          const hasSolo = Boolean(m.solo_artist_name);

          if (hasSolo) {
            // Linkable solo profile
            const colorClasses = isFormer
              ? "bg-rose-950/50 hover:bg-rose-900/60 border-rose-800/80 hover:border-rose-500 text-rose-300"
              : "bg-indigo-950/80 hover:bg-indigo-900 border-indigo-700/70 hover:border-indigo-400 text-indigo-200";

            return `
              <button type="button" onclick="jumpToSingleArtist('${escapeJsArg(m.solo_artist_name)}')"
                      class="px-2.5 py-1 rounded-lg border text-xs font-medium truncate flex items-center gap-1.5 transition ${colorClasses}"
                      title="${escapeHtml(m.member_name)} (Solo Discography)${isFormer ? ' - Former Member' : ''}">
                <span>${escapeHtml(m.member_name)}</span>
                <span class="text-[10px] opacity-70">↗</span>
              </button>
            `;
          } else if (isFormer) {
            // Muted/Dull Red for Former Member without Solo Profile
            return `
              <span class="px-2.5 py-1 rounded-lg bg-rose-950/30 border border-rose-900/40 text-rose-400/80 text-xs font-medium text-center truncate"
                    title="${escapeHtml(m.member_name)} (Former Member)">
                ${escapeHtml(m.member_name)}
              </span>
            `;
          } else {
            // Standard Neutral Gray for Active Member without Solo Profile
            return `
              <span class="px-2.5 py-1 rounded-lg bg-slate-900 border border-slate-800 text-slate-300 text-xs font-medium text-center truncate"
                    title="${escapeHtml(m.member_name)}">
                ${escapeHtml(m.member_name)}
              </span>
            `;
          }
        }).join("");
      }
    }

    // EMA & Chart Rendering
    currentArtistSummaryData = data;
    renderTrajectoryChart(data);

  } catch (err) {
    console.error("Failed to render analytics", err);
  }
}

// Unified Volume-Weighted Exponential Moving Average (EMA)
function computeVolumeWeightedEMA(albums, alphaBase = 0.45, refTracks = 6) {
  let runningEma = null;
  return (albums || []).map((album, idx) => {
    const trackCount = Math.max(1, album.track_count || 1);
    if (idx === 0) {
      runningEma = album.avg_score;
    } else {
      const alphaEffective = 1 - Math.pow(1 - alphaBase, trackCount / refTracks);
      runningEma = (album.avg_score * alphaEffective) + (runningEma * (1 - alphaEffective));
    }
    return parseFloat(runningEma.toFixed(2));
  });
}

function renderTrajectoryChart(data) {
  const alphaBase = 0.45;
  const refTracks = 6;
  const isQwar = (chartMetricMode === 'qwar');
  const isRate = (chartMetricMode === 'rate');
  const isSteppedMetric = isQwar || isRate;

  // 1. Clean release dates and sort chronologically
  const allAlbums = prepareSortedChartAlbums(data.albums);

  const ctx = document.getElementById('scoreChart')?.getContext('2d');
  if (!ctx) return;
  if (scoreChartInstance) {
    scoreChartInstance.destroy();
  }

  // Calculate dynamic max for Q-WAR and Rate y-axes
  const maxQwar = Math.max(...allAlbums.map(a => a.cumulative_qwar || 0), 0);
  const maxRate = Math.max(...allAlbums.map(a => a.cumulative_qwar_per_track || 0), 0);

  const qwarYAxis = {
    min: 0,
    suggestedMax: Math.max(120, Math.ceil(((maxQwar || 80) + 15) / 10) * 10),
    ticks: {
      color: '#94a3b8',
      callback: (val) => `${val} pts`
    },
    grid: { color: '#1e293b' }
  };
  const rateYAxis = {
    min: 0,
    suggestedMax: Math.max(2.5, Math.ceil(((maxRate || 2.0) + 0.3) * 10) / 10),
    ticks: {
      color: '#94a3b8',
      callback: (val) => `${val.toFixed(1)} pts/trk`
    },
    grid: { color: '#1e293b' }
  };
  const scoreYAxis = {
    min: 1.0,
    max: 5.0,
    ticks: { stepSize: 0.5, color: '#94a3b8' },
    grid: { color: '#1e293b' }
  };

  const activeYAxis = isRate ? rateYAxis : (isQwar ? qwarYAxis : scoreYAxis);

  const isReleaseMode = chartAlignmentMode !== 'calendar';
  const primaryAlbums = isReleaseMode
    ? allAlbums.filter(a => !a.is_advance_single && (a.album_type === 'EP' || a.album_type === 'LP'))
    : allAlbums.filter(a => !a.is_advance_single);
  const advanceAlbums = isReleaseMode ? [] : allAlbums.filter(a => a.is_advance_single);

  if (isReleaseMode && primaryAlbums.length === 0) {
    ctx.font = "12px sans-serif";
    ctx.fillStyle = "#64748b";
    ctx.textAlign = "center";
    ctx.fillText("No substantive EPs or LPs cataloged to sequence.", ctx.canvas.width / 2, ctx.canvas.height / 2);
    return;
  }

  const labels = isReleaseMode ? primaryAlbums.map((a, idx) => `#${idx + 1} (${a.album_type})`) : undefined;
  const xFor = (a, idx) => (isReleaseMode ? labels[idx] : a.release_date);
  let datasets = [];

  if (isSteppedMetric) {
    const metricPoints = primaryAlbums.map((a, idx) => {
      const prevVal = idx > 0 ? (isRate ? (primaryAlbums[idx - 1].cumulative_qwar_per_track || 0) : (primaryAlbums[idx - 1].cumulative_qwar || 0)) : 0;
      const currentVal = isRate ? (a.cumulative_qwar_per_track || 0) : (a.cumulative_qwar || 0);
      return {
        x: xFor(a, idx),
        y: currentVal,
        title: a.album_title,
        track_count: a.track_count,
        album_type: a.album_type,
        release_date: a.release_date,
        points_gained: parseFloat((currentVal - prevVal).toFixed(2)),
        cumulative_qwar: a.cumulative_qwar,
        cumulative_rate: a.cumulative_qwar_per_track,
        cumulative_tracks: a.cumulative_rated_tracks,
        running_avg: a.cumulative_avg_score
      };
    });

    datasets = [
      {
        type: 'line',
        label: isRate ? 'Quality Density (Q-WAR / Track)' : 'Cumulative Q-WAR',
        data: metricPoints.map(p => ({ x: p.x, y: p.y })),
        borderColor: isRate ? '#38bdf8' : '#818cf8',
        backgroundColor: isRate ? 'rgba(56, 189, 248, 0.10)' : 'rgba(99, 102, 241, 0.12)',
        borderWidth: 2.5,
        stepped: 'after',
        fill: true,
        pointRadius: 0,
        order: 3
      },
      {
        type: 'scatter',
        label: isReleaseMode ? 'Mini Album (EP)' : 'Included Release (EP / Single)',
        data: metricPoints.filter(p => p.album_type !== 'LP'),
        backgroundColor: '#fbbf24',
        borderColor: '#0f172a',
        borderWidth: 2,
        pointRadius: 6,
        pointHoverRadius: 9,
        order: 1
      },
      {
        type: 'scatter',
        label: 'Studio Album (LP Milestone)',
        data: metricPoints.filter(p => p.album_type === 'LP'),
        backgroundColor: '#ffffff',
        borderColor: '#0f172a',
        borderWidth: 2,
        pointStyle: 'rectRot',
        pointRadius: isReleaseMode ? 9 : 8,
        pointHoverRadius: isReleaseMode ? 12 : 11,
        order: 1
      }
    ];
  } else {
    const standardPoints = primaryAlbums.map((a, idx) => ({
      x: xFor(a, idx),
      y: a.avg_score,
      title: a.album_title,
      track_count: a.track_count,
      album_type: a.album_type,
      release_date: a.release_date,
      is_advance: false
    }));
    const emaSeries = computeVolumeWeightedEMA(primaryAlbums, alphaBase, refTracks);
    const trendData = isReleaseMode
      ? emaSeries
      : primaryAlbums.map((album, idx) => ({ x: album.release_date, y: emaSeries[idx] }));

    datasets = [
      {
        type: 'line',
        label: isReleaseMode ? 'Release-over-Release Trend' : 'Trendline',
        data: trendData,
        borderColor: '#818cf8',
        backgroundColor: 'rgba(99, 102, 241, 0.08)',
        borderWidth: 2.5,
        tension: 0.45,
        fill: true,
        pointRadius: 0,
        order: 3
      },
      {
        type: 'scatter',
        label: isReleaseMode ? 'Mini Album (EP)' : 'Included Release (EP / Single)',
        data: standardPoints.filter(p => p.album_type !== 'LP'),
        backgroundColor: '#fbbf24',
        borderColor: '#0f172a',
        borderWidth: 2,
        pointRadius: 6,
        pointHoverRadius: 9,
        order: 1
      },
      {
        type: 'scatter',
        label: isReleaseMode ? 'Studio Album (LP Anchor)' : 'Studio Album (LP)',
        data: standardPoints.filter(p => p.album_type === 'LP'),
        backgroundColor: '#ffffff',
        borderColor: '#0f172a',
        borderWidth: 2,
        pointStyle: 'rectRot',
        pointRadius: isReleaseMode ? 9 : 8,
        pointHoverRadius: isReleaseMode ? 12 : 11,
        order: 1
      }
    ];

    if (!isReleaseMode) {
      datasets.push({
        type: 'scatter',
        label: 'Duplicate / Offshoot Single (Excluded)',
        data: advanceAlbums.map(a => ({
          x: a.release_date,
          y: a.avg_score,
          title: a.album_title,
          track_count: a.track_count,
          album_type: a.album_type,
          is_advance: true
        })),
        backgroundColor: '#f43f5e',
        borderColor: '#0f172a',
        borderWidth: 2,
        pointRadius: 6,
        pointHoverRadius: 9,
        order: 2
      });
    }
  }

  const xScale = isReleaseMode
    ? { ticks: { color: '#94a3b8', font: { weight: 'bold' } }, grid: { color: '#1e293b' } }
    : {
        type: 'time',
        time: { unit: 'month', displayFormats: { month: 'MMM yyyy', year: 'yyyy' }, tooltipFormat: 'yyyy-MM-dd' },
        ticks: { color: '#94a3b8', maxRotation: 0, autoSkip: true, maxTicksLimit: 8 },
        grid: { color: '#1e293b' }
      };

  scoreChartInstance = new Chart(ctx, {
    plugins: [qwarTierBackgroundPlugin],
    data: isReleaseMode ? { labels, datasets } : { datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'nearest', intersect: true },
      plugins: {
        qwarTierBackground: { metricMode: chartMetricMode },
        legend: {
          display: true,
          position: 'top',
          align: 'end',
          labels: { color: '#94a3b8', font: { size: 10 }, usePointStyle: true, boxWidth: 6 }
        },
        tooltip: {
          backgroundColor: '#0f172a',
          titleColor: '#e2e8f0',
          bodyColor: isRate ? '#38bdf8' : (isQwar ? '#a5b4fc' : '#fbbf24'),
          borderColor: '#334155',
          borderWidth: 1,
          callbacks: {
            title: (items) => {
              const raw = items[0].raw;
              return raw && raw.title ? `${raw.title} (${raw.album_type || 'Release'})` : (isReleaseMode ? items[0].label : `Date: ${items[0].label}`);
            },
            label: (item) => {
              const raw = item.raw;
              if (isRate) {
                if (raw && raw.cumulative_rate !== undefined) {
                  const gainedStr = raw.points_gained !== undefined ? ` (${raw.points_gained >= 0 ? '+' : ''}${raw.points_gained} change)` : '';
                  const lines = [
                    `Density: ${raw.cumulative_rate ? raw.cumulative_rate.toFixed(2) : raw.y} pts/track${gainedStr}`,
                    `Total Q-WAR: ${raw.cumulative_qwar} pts (${raw.cumulative_tracks} rated tracks)`
                  ];
                  lines.push(isReleaseMode ? `Released: ${raw.release_date || 'No Date'}` : `Catalog Avg: ${raw.running_avg ? raw.running_avg.toFixed(2) : '--'} ★`);
                  return lines;
                }
                return `Density: ${raw && raw.y !== undefined ? raw.y : item.raw} pts/track`;
              }

              if (isQwar) {
                if (raw && raw.cumulative_qwar !== undefined) {
                  const gainedStr = raw.points_gained !== undefined ? ` (+${raw.points_gained >= 0 ? raw.points_gained : 0} earned)` : '';
                  return [
                    `Total Q-WAR: ${raw.cumulative_qwar} pts${gainedStr}`,
                    `Catalog Avg: ${raw.running_avg} ★ | Listening Density: ${raw.cumulative_rate} pts/trk`,
                    isReleaseMode ? `Released: ${raw.release_date || 'No Date'} (${raw.track_count} tracks)` : `Tracks: ${raw.track_count} (${raw.cumulative_tracks} total catalog tracks)`
                  ];
                }
                return `Q-WAR: ${raw && raw.y !== undefined ? raw.y : item.raw} pts`;
              }

              if (item.dataset.type === 'scatter') {
                if (raw.is_advance) {
                  return [
                    `Score: ${raw.y} / 5.0 (${raw.track_count} tracks)`,
                    `⚠️ Duplicate / Offshoot: Re-released on another project`
                  ];
                }
                const prefix = raw.album_type === 'LP' ? '💎 Studio LP' : 'EP/Release';
                return isReleaseMode
                  ? [`${prefix}: ${raw.y} / 5.0 (${raw.track_count} tracks)`, `Released: ${raw.release_date || 'No Date'}`]
                  : `${prefix}: ${raw.y} / 5.0 (${raw.track_count} tracks)`;
              }
              return `Trendline: ${raw && raw.y !== undefined ? raw.y : item.raw} / 5.0`;
            }
          }
        }
      },
      scales: {
        x: xScale,
        y: activeYAxis
      }
    }
  });
}

function toggleArtistTypeFields() {
  const type = document.getElementById("edit-artist-type").value;
  const groupField = document.getElementById("field-group-members");
  const soloField = document.getElementById("field-solo-parent");

  if (type === "solo") {
    groupField.classList.add("hidden");
    soloField.classList.remove("hidden");
  } else {
    groupField.classList.remove("hidden");
    soloField.classList.add("hidden");
  }
}

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// Safely escapes strings placed inside single-quoted onclick="func('...')" attributes
function escapeJsArg(str) {
  return escapeHtml(String(str ?? "").replace(/\\/g, "\\\\").replace(/'/g, "\\'"));
}

function closeArtistModal() {
  document.getElementById("artist-modal").classList.add("hidden");
}

function startEditArtist(id) {
  const artist = artistCache.find(a => a.id === id);
  if (!artist) {
    alert("Could not load artist details. Please refresh the page.");
    closeArtistModal();
    return;
  }

  document.getElementById("edit-artist-id").value = artist.id;
  document.getElementById("edit-artist-name").value = artist.name;
  document.getElementById("edit-artist-debut").value = artist.debut_year || "";
  document.getElementById("edit-artist-gen").value = artist.generation || "";
  document.getElementById("edit-artist-status").value = artist.status || "Active";
  document.getElementById("edit-artist-type").value = artist.artist_type || "group";

  // Populate Members or Parent Group
  if (artist.artist_type === "solo") {
    const groupPills = (artist.parent_groups || []).map(g => (g.is_former ? `*${g.group_name}` : g.group_name));
    document.getElementById("edit-artist-parent-groups").value = groupPills.join(", ");
    document.getElementById("edit-artist-members").value = "";
  } else {
    // Build comma-separated list, prefixing former members with '*'
    const memberList = (artist.member_objects || []).map(m => (m.is_former ? `*${m.member_name}` : m.member_name));
    document.getElementById("edit-artist-members").value = memberList.length > 0 
      ? memberList.join(", ") 
      : (artist.members || []).join(", ");
  }

  toggleArtistTypeFields();
}

function autoSelectGen() {
  const year = parseInt(document.getElementById("edit-artist-debut").value);
  const genSelect = document.getElementById("edit-artist-gen");
  if (!year || isNaN(year)) return;

  if (year < 2003) genSelect.value = "Gen 1";
  else if (year <= 2011) genSelect.value = "Gen 2";
  else if (year <= 2017) genSelect.value = "Gen 3";
  else if (year <= 2022) genSelect.value = "Gen 4";
  else genSelect.value = "Gen 5";
}

async function saveArtistDetails() {
  const id = document.getElementById("edit-artist-id").value;
  const name = document.getElementById("edit-artist-name").value.trim();
  const debut = document.getElementById("edit-artist-debut").value;
  const gen = document.getElementById("edit-artist-gen").value;
  const status = document.getElementById("edit-artist-status").value;
  const artistType = document.getElementById("edit-artist-type").value;
  const parentGroupsInput = document.getElementById("edit-artist-parent-groups").value;
  const membersRaw = document.getElementById("edit-artist-members").value;

  if (!name) {
    alert("Artist name cannot be empty");
    return;
  }

  const membersList = membersRaw.split(",").map(m => m.trim()).filter(Boolean);

  const payload = {
    name: name,
    debut_year: debut ? parseInt(debut) : null,
    generation: gen || null,
    status: status,
    artist_type: artistType,
    members: artistType === "group" ? membersList : null,
    parent_groups: parentGroupsInput
  };

  const url = id ? `/api/artists/${id}` : "/api/artists";
  const method = id ? "PUT" : "POST";

  try {
    const res = await fetch(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || "Failed to save artist details");
    }

    closeArtistModal();

    // If created from import context or if currently on Import/Export pane, pre-fill search fields
    if (!id && (artistModalContext === 'import' || currentLibrarySubView === 'io')) {
      const singleInput = document.getElementById("search-artist");
      const bulkInput = document.getElementById("bulk-artist");
      if (singleInput) singleInput.value = name;
      if (bulkInput) bulkInput.value = name;

      const status = document.getElementById("fetch-status");
      if (status) {
        status.className = "text-xs mt-3 p-2.5 rounded bg-emerald-950/80 border border-emerald-800 text-emerald-300 block";
        status.innerHTML = `✓ Artist <strong>${escapeHtml(name)}</strong> created and pre-filled. Ready to fetch releases.`;
      }
    }
    
    // Refresh whichever table is currently visible
    if (currentLibraryView === 'artists') {
      loadLibraryArtists();
    } else if (currentLibraryView === 'albums') {
      loadLibrary();
    } else {
      loadTracksData();
    }

    if (id && currentSingleArtist && (artistCache || []).some(a => a.id === parseInt(id, 10) && a.name.toLowerCase() === currentSingleArtist.toLowerCase())) {
      currentSingleArtist = name;
    }
    await initArtistSelector();
  } catch (err) {
    alert(err.message);
  }
}

async function deleteArtist(artistId, name, albumCount) {
  const warning = albumCount > 0 
    ? `Warning: "${name}" has ${albumCount} album(s). Deleting will remove all albums and tracks for this artist. Proceed?` 
    : `Delete artist "${name}"?`;

  if (!confirm(warning)) return;

  try {
    const res = await fetch(`/api/artists/${artistId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error("Delete failed");
    
    artistCache = []; // Clear cache so stale IDs are purged immediately
    closeArtistModal();
    if (currentLibraryView === 'artists') {
      loadLibraryArtists();
    } else if (currentLibraryView === 'albums') {
      loadLibrary();
    } else {
      loadTracksData();
    }
    initArtistSelector();
  } catch (err) {
    alert(err.message);
  }
}

async function deleteCurrentEditingArtist() {
  const idStr = document.getElementById("edit-artist-id").value;
  if (!idStr) return;
  const artistId = parseInt(idStr, 10);
  const name = document.getElementById("edit-artist-name").value.trim();

  // Look up current album count from cache if available
  const cached = (artistCache || []).find(a => a.id === artistId);
  const albumCount = cached ? cached.album_count : 0;

  await deleteArtist(artistId, name, albumCount);
}

async function loadTracksData() {
  try {
    const res = await fetch("/api/tracks");
    allTracksData = await res.json();
    populateArtistFilterOptions();
    applyFilters();
  } catch (err) {
    console.error("Failed to load tracks", err);
  }
}

function populateArtistFilterOptions() {
  const albumSelect = document.getElementById("album-filter-artist");
  const trackSelect = document.getElementById("track-filter-artist");
  
  // Case-insensitive alphabetical sort
  const artists = [...new Set(allTracksData.map(t => t.artist_name))]
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));

  const optionsHtml = `<option value="">All Artists</option>` + 
    artists.map(a => `<option value="${escapeHtml(a)}">${escapeHtml(a)}</option>`).join("");

  if (albumSelect) {
    const cur = albumSelect.value;
    albumSelect.innerHTML = optionsHtml;
    albumSelect.value = cur;
  }
  if (trackSelect) {
    const cur = trackSelect.value;
    trackSelect.innerHTML = optionsHtml;
    trackSelect.value = cur;
  }
}

function applyFilters() {
  if (currentLibraryView === 'albums') {
    renderFilteredAlbums();
    return;
  }

  const search = (document.getElementById("track-filter-search")?.value || "").toLowerCase().trim();
  const artist = document.getElementById("track-filter-artist")?.value || "";
  const rating = document.getElementById("track-filter-rating")?.value || "all";
  const trackTypeFilter = document.getElementById("track-filter-type")?.value || "";
  const dateFilter = document.getElementById("track-filter-date")?.value || "all";
  const cutoffs = getReleaseDateCutoffs();

  const filtered = allTracksData.filter(t => {
    if (artist && t.artist_name !== artist) return false;

    // Track Type & Title Track filter logic
    if (trackTypeFilter === "title_only") {
      if (!t.is_title_track) return false;
    } else if (trackTypeFilter === "bside_only") {
      const tType = t.track_type || "core";
      if (t.is_title_track || tType === "non_canon" || tType === "alt_lang") return false;
    } else if (trackTypeFilter) {
      const tType = t.track_type || "core";
      if (tType !== trackTypeFilter) return false;
    }

    // Release Date filter logic
    if (!matchesReleaseDateFilter(t.release_date, dateFilter, cutoffs)) return false;

    // Rating Filter Logic
    if (rating === "half_step") {
      if (t.rating === null || t.rating === undefined) return false;
      if (Math.abs((t.rating % 1) - 0.5) > 0.01) return false;
    } else if (rating === "5.0" && t.rating !== 5.0) {
      return false;
    } else if (rating === "4.0" && (t.rating === null || t.rating < 4.0)) {
      return false;
    } else if (rating === "unrated" && t.rating !== null) {
      return false;
    }

    if (search) {
      const matchTitle = (t.track_title || '').toLowerCase().includes(search);
      const matchAlbum = (t.album_title || '').toLowerCase().includes(search);
      const matchArtist = (t.artist_name || '').toLowerCase().includes(search);
      const matchNotes = (t.notes || '').toLowerCase().includes(search);
      if (!matchTitle && !matchAlbum && !matchArtist && !matchNotes) return false;
    }

    return true;
  });

  renderFilteredTracks(filtered);
}

function sortTable(column) {
  currentPage = 1;
  if (sortColumn === column) {
    sortAsc = !sortAsc;
  } else {
    sortColumn = column;
    sortAsc = (column === 'track_title' || column === 'album_title' || column === 'artist_name');
  }
  applyFilters();
}

function renderFilteredTracks(tracks) {
  sortRecords(tracks, sortColumn, sortAsc);

  // Update Pagination state
  updatePaginationControls(tracks.length);

  const tbody = document.getElementById("library-tracks-tbody");
  if (tracks.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" class="py-6 text-center text-slate-500">No tracks match your filter criteria.</td></tr>`;
    return;
  }

  // Slice to current page window
  const startIndex = (currentPage - 1) * pageSize;
  const paginatedTracks = tracks.slice(startIndex, startIndex + pageSize);

  tbody.innerHTML = paginatedTracks.map(t => {
    const currentScore = t.rating !== null && t.rating !== undefined ? parseFloat(t.rating).toFixed(1) : "";
    return `
      <tr class="hover:bg-slate-800/40">
        <td class="py-1.5 px-3">
          <select onchange="updateSingleTrackRating(${t.track_id}, this)"
                  class="bg-slate-950 border border-slate-700 rounded px-1.5 py-1 font-mono font-bold text-xs focus:outline-none focus:border-indigo-500 ${getScoreColor(t.rating)}">
            <option value="" class="text-slate-500 font-normal">--</option>
            <option value="5.0" class="text-purple-400 font-bold" ${currentScore === "5.0" ? "selected" : ""}>5.0</option>
            <option value="4.5" class="text-indigo-400 font-bold" ${currentScore === "4.5" ? "selected" : ""}>4.5</option>
            <option value="4.0" class="text-indigo-400 font-bold" ${currentScore === "4.0" ? "selected" : ""}>4.0</option>
            <option value="3.5" class="text-sky-400 font-bold" ${currentScore === "3.5" ? "selected" : ""}>3.5</option>
            <option value="3.0" class="text-sky-400 font-bold" ${currentScore === "3.0" ? "selected" : ""}>3.0</option>
            <option value="2.5" class="text-teal-400 font-bold" ${currentScore === "2.5" ? "selected" : ""}>2.5</option>
            <option value="2.0" class="text-teal-400 font-bold" ${currentScore === "2.0" ? "selected" : ""}>2.0</option>
            <option value="1.5" class="text-slate-400 font-normal" ${currentScore === "1.5" ? "selected" : ""}>1.5</option>
            <option value="1.0" class="text-slate-400 font-normal" ${currentScore === "1.0" ? "selected" : ""}>1.0</option>
          </select>
        </td>
        <td class="py-2.5 px-3">
          <span class="font-medium text-slate-200">${escapeHtml(t.track_title)}</span>
          ${t.is_title_track ? '<span class="ml-1 text-[10px] bg-indigo-950 text-indigo-400 border border-indigo-800 px-1 rounded">Title</span>' : ''}
          ${getTrackTypeBadge(t.track_type)}
        </td>
        <td class="py-2.5 px-3 text-slate-400">${escapeHtml(t.artist_name)}</td>
        <td class="py-2.5 px-3">
          <button type="button" onclick="editAlbum(${t.album_id})" 
                  class="text-left text-indigo-400 hover:text-indigo-300 hover:underline font-medium focus:outline-none transition">
            ${escapeHtml(t.album_title)}
          </button>
        </td>
        <td class="py-2.5 px-3 font-mono text-slate-500">${t.release_date || 'No Date'}</td>
        <td class="py-2.5 px-3 text-slate-400 italic">${escapeHtml(t.notes || '')}</td>
      </tr>
    `;
  }).join("");
}

function getTrackTypeBadge(type) {
  // Core catalog tracks don't need a badge cluttering the title
  if (!type || type === 'core') return '';

  const labels = {
    unit: { text: 'Unit', cls: 'bg-emerald-950 text-emerald-300 border-emerald-800' },
    solo: { text: 'Solo', cls: 'bg-purple-950 text-purple-300 border-purple-800' },
    alt_lang: { text: 'Alt Lang', cls: 'bg-sky-950 text-sky-300 border-sky-800' },
    non_canon: { text: 'Other', cls: 'bg-slate-900 text-slate-500 border-slate-800' },
  };

  const item = labels[type] || { text: type, cls: 'bg-slate-900 text-slate-400 border-slate-700' };
  return `<span class="ml-1 text-[10px] ${item.cls} border px-1 rounded uppercase tracking-wider">${item.text}</span>`;
}

async function updateSingleTrackRating(trackId, selectEl) {
  const newRating = selectEl.value ? parseFloat(selectEl.value) : null;
  
  // Update color immediately for snappy feedback
  selectEl.className = `bg-slate-950 border border-slate-700 rounded px-1.5 py-1 font-mono font-bold text-xs focus:outline-none focus:border-indigo-500 ${getScoreColor(newRating)}`;

  try {
    const res = await fetch(`/api/tracks/${trackId}/rating`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rating: newRating })
    });

    if (!res.ok) throw new Error("Failed to save track rating");

    // Keep local cache in sync so active filters don't desynchronize
    const track = allTracksData.find(t => t.track_id === trackId);
    if (track) {
      track.rating = newRating;
    }

    // Quietly refresh album summaries in the background so album avg scores stay accurate
    loadLibrary(false);
  } catch (err) {
    alert(err.message);
  }
}

// Distinct theme colors for up to 4 artists
const COMPARE_COLORS = [
  { border: '#818cf8', fill: 'rgba(129, 140, 248, 0.05)' }, // Indigo
  { border: '#34d399', fill: 'rgba(52, 211, 153, 0.05)' },  // Emerald
  { border: '#f59e0b', fill: 'rgba(245, 158, 11, 0.05)' },  // Amber
  { border: '#f43f5e', fill: 'rgba(244, 63, 94, 0.05)' }    // Rose
];

function switchAnalyticsSubView(subview) {
  currentAnalyticsSubView = subview;
  const singleBtn = document.getElementById("subnav-analytics-single");
  const compareBtn = document.getElementById("subnav-analytics-compare");
  const allBtn = document.getElementById("subnav-analytics-all");
  const singlePane = document.getElementById("analytics-pane-single");
  const comparePane = document.getElementById("analytics-pane-compare");
  const allPane = document.getElementById("analytics-pane-all");

  // Reset button styles
  [singleBtn, compareBtn, allBtn].forEach(btn => {
    if (btn) btn.className = "px-3.5 py-1.5 rounded-md text-slate-400 hover:text-white transition";
  });
  [singlePane, comparePane, allPane].forEach(pane => {
    if (pane) pane.classList.add("hidden");
  });
  closeSingleArtistDropdown();
  closeAllCompareDropdowns();

  if (subview === 'single') {
    singleBtn.className = "px-3.5 py-1.5 rounded-md bg-indigo-600 text-white font-semibold transition";
    singlePane.classList.remove("hidden");
    if (scoreChartInstance) scoreChartInstance.resize();
  } else if (subview === 'compare') {
    compareBtn.className = "px-3.5 py-1.5 rounded-md bg-indigo-600 text-white font-semibold transition";
    comparePane.classList.remove("hidden");
    initComparePills();
  } else if (subview === 'all') {
    allBtn.className = "px-3.5 py-1.5 rounded-md bg-indigo-600 text-white font-semibold transition";
    allPane.classList.remove("hidden");
    loadAllArtistsAnalytics();
  }
}

function switchArtistDashboardView(view) {
  currentDashboardView = view;
  const btnGroups = document.getElementById("dash-btn-groups");
  const btnSolos = document.getElementById("dash-btn-solos");
  const paneGroups = document.getElementById("view-dash-groups");
  const paneSolos = document.getElementById("view-dash-solos");

  if (view === 'groups') {
    btnGroups.className = "px-3.5 py-1.5 rounded-md bg-indigo-600 text-white font-semibold transition";
    btnSolos.className = "px-3.5 py-1.5 rounded-md text-slate-400 hover:text-white transition";
    paneGroups.classList.remove("hidden");
    paneSolos.classList.add("hidden");
  } else {
    btnSolos.className = "px-3.5 py-1.5 rounded-md bg-indigo-600 text-white font-semibold transition";
    btnGroups.className = "px-3.5 py-1.5 rounded-md text-slate-400 hover:text-white transition";
    paneSolos.classList.remove("hidden");
    paneGroups.classList.add("hidden");
  }
  renderArtistDashboardTable(view === 'groups' ? 'group' : 'solo');
}

async function loadAllArtistsAnalytics() {
  try {
    const res = await fetch("/api/analytics/all-artists");
    allArtistsData = await res.json();

    // Update badge counts
    const groupsCount = allArtistsData.filter(d => (d.artist_type || 'group') === 'group').length;
    const solosCount = allArtistsData.filter(d => d.artist_type === 'solo').length;
    
    const countElG = document.getElementById("dash-count-groups");
    const countElS = document.getElementById("dash-count-solos");
    if (countElG) countElG.innerText = `(${groupsCount})`;
    if (countElS) countElS.innerText = `(${solosCount})`;

    renderArtistDashboardTable(currentDashboardView === 'groups' ? 'group' : 'solo');
    loadWatchlist();
  } catch (err) {
    console.error("Failed to load all artists analytics", err);
  }
}

// Unified Artist Dashboard Table Sorting & Rendering
function sortArtistDashboardTable(type, col) {
  const isGroup = type === 'group';
  let sortCol = isGroup ? allArtistsSortColumn : soloArtistsSortColumn;
  let sortAsc = isGroup ? allArtistsSortAsc : soloArtistsSortAsc;

  if (sortCol === col) {
    sortAsc = !sortAsc;
  } else {
    sortCol = col;
    sortAsc = isGroup
      ? (col === 'artist' || col === 'generation' || col === 'status')
      : (col === 'artist' || col === 'parent_groups' || col === 'status');
  }

  if (isGroup) {
    allArtistsSortColumn = sortCol;
    allArtistsSortAsc = sortAsc;
    allArtistsCurrentPage = 1;
  } else {
    soloArtistsSortColumn = sortCol;
    soloArtistsSortAsc = sortAsc;
    soloArtistsCurrentPage = 1;
  }

  renderArtistDashboardTable(type);
}

/**
 * Computes the Geometric Mean Composite Score for tier ranking:
 * sqrt(Compacted Q-WAR * (Q-WAR Rate * 50)).
 */
function computeTierCompositeScore(artistObj) {
  const qwar = Math.max(0, Number(artistObj?.compacted_qwar) || 0);
  const rate = Math.max(0, Number(artistObj?.qwar_per_track) || 0);
  return Math.sqrt(qwar * (rate * 50));
}

function toggleTierListVisibility(type = 'group') {
  const prefix = type === 'solo' ? 'solo' : 'group';
  const content = document.getElementById(`${prefix}-tier-list-content`);
  const btn = document.getElementById(`${prefix}-tier-list-toggle`);
  if (!content || !btn) return;
  const isHidden = content.classList.toggle('hidden');
  btn.textContent = isHidden ? 'Show' : 'Hide';
}

/**
 * Renders the TierMaker-style S/A/B/C/D + Provisional tier list for Groups or Soloists.
 */
function formatTierThresholdRange(tierKey, thresholds) {
  if (tierKey === 'S') return `≥${thresholds.S}`;
  if (tierKey === 'A') return `${thresholds.A}–${thresholds.S}`;
  if (tierKey === 'B') return `${thresholds.B}–${thresholds.A}`;
  if (tierKey === 'C') return `${thresholds.C}–${thresholds.B}`;
  if (tierKey === 'D') return `<${thresholds.C}`;
  return '';
}

function renderCategoryLeaderCard({ title, gateLabel, accentClass, rowsHtml, emptyMsg }) {
  return `
    <div class="bg-slate-950 rounded-xl border border-slate-800 overflow-hidden flex flex-col">
      <div class="px-3 py-2 border-b border-slate-800/80 flex items-center justify-between gap-2 bg-slate-950">
        <span class="text-[11px] font-semibold uppercase tracking-wider ${accentClass} truncate">${escapeHtml(title)}</span>
        <span class="text-[10px] font-mono text-slate-500 shrink-0">${escapeHtml(gateLabel)}</span>
      </div>
      <div class="py-1.5 flex-1">
        ${rowsHtml || `<div class="px-3 py-3 text-center text-[11px] text-slate-500 italic">${escapeHtml(emptyMsg)}</div>`}
      </div>
    </div>
  `;
}

/**
 * Renders the 4 Category Leader cards (5* Rate, Title Track Avg, B-Side Avg, Best LP)
 * on both Group and Soloist dashboards, respecting active dashboard filters.
 */
function renderCategoryLeaders(type = 'group', artists = []) {
  const prefix = type === 'solo' ? 'solo' : 'group';
  const container = document.getElementById(`${prefix}-category-leaders`);
  if (!container) return;

  const cfg = CATEGORY_LEADERS_CONFIG[prefix] || CATEGORY_LEADERS_CONFIG.group;
  const topN = CATEGORY_LEADERS_CONFIG.topN || 5;
  const rankBadges = ['🏆', '🥈', '🥉', '④', '⑤'];

  const buildRows = (items, getMainLabel, getSubLabel, getValueStr, getSampleStr, getOnClick) => {
    if (!items.length) return '';
    return items.slice(0, topN).map((item, idx) => {
      const badge = rankBadges[idx] || `${idx + 1}.`;
      const badgeClass = idx < 3 ? 'text-xs leading-none' : 'text-[13px] text-slate-400 leading-none';
      const subLabel = getSubLabel ? getSubLabel(item) : '';
      return `
        <button type="button"
                onclick="${getOnClick(item)}"
                title="${escapeHtml(subLabel ? `${getMainLabel(item)} — ${subLabel}` : getMainLabel(item))}"
                class="w-full px-3 py-1 flex items-center justify-between gap-2 text-left hover:bg-slate-900/80 transition rounded">
          <div class="flex items-center gap-1.5 min-w-0">
            <span class="w-4 text-center shrink-0 ${badgeClass}">${badge}</span>
            <div class="flex items-baseline gap-1 min-w-0">
              <span class="text-xs font-medium text-slate-400 truncate">${escapeHtml(getMainLabel(item))}</span>
              ${subLabel ? `<span class="text-[10px] text-slate-500 shrink-0">· ${escapeHtml(subLabel)}</span>` : ''}
            </div>
          </div>
          <div class="shrink-0 text-right font-mono leading-none">
            <span class="text-xs font-semibold text-slate-300">${escapeHtml(getValueStr(item))}</span>
            <span class="text-[10px] text-slate-400 ml-0.5">${escapeHtml(getSampleStr(item))}</span>
          </div>
        </button>
      `;
    }).join('');
  };

  // 1. Perfect Rate (5* %)
  const perfectLeaders = artists
    .filter(a => (Number(a.total_rated_tracks) || 0) >= cfg.minPerfectTracks)
    .sort((a, b) => (b.overall_perfect_pct - a.overall_perfect_pct) || (b.overall_perfect - a.overall_perfect) || (b.total_rated_tracks - a.total_rated_tracks));
  const card1 = renderCategoryLeaderCard({
    title: 'Perfect (5★) Rate',
    gateLabel: `≥${cfg.minPerfectTracks} trk`,
    accentClass: 'text-amber-300',
    rowsHtml: buildRows(
      perfectLeaders,
      a => a.artist,
      null,
      a => `${(Number(a.overall_perfect_pct) || 0).toFixed(1)}%`,
      a => `(${a.overall_perfect || 0}/${a.total_rated_tracks || 0})`,
      a => `jumpToSingleArtist('${escapeJsArg(a.artist)}')`
    ),
    emptyMsg: `No ${prefix === 'solo' ? 'soloists' : 'groups'} with ≥${cfg.minPerfectTracks} rated tracks.`
  });

  // 2. Title Track Avg
  const titleLeaders = artists
    .filter(a => a.title_avg !== null && a.title_avg !== undefined && (Number(a.title_count) || 0) >= cfg.minTitleTracks)
    .sort((a, b) => (b.title_avg - a.title_avg) || (b.title_count - a.title_count));
  const card2 = renderCategoryLeaderCard({
    title: 'Title Track Avg',
    gateLabel: `≥${cfg.minTitleTracks} titles`,
    accentClass: 'text-indigo-300',
    rowsHtml: buildRows(
      titleLeaders,
      a => a.artist,
      null,
      a => Number(a.title_avg).toFixed(2),
      a => `(${a.title_count})`,
      a => `jumpToSingleArtist('${escapeJsArg(a.artist)}')`
    ),
    emptyMsg: `No ${prefix === 'solo' ? 'soloists' : 'groups'} with ≥${cfg.minTitleTracks} title tracks.`
  });

  // 3. B-Side Avg
  const bsideLeaders = artists
    .filter(a => a.bside_avg !== null && a.bside_avg !== undefined && (Number(a.bside_count) || 0) >= cfg.minBsideTracks)
    .sort((a, b) => (b.bside_avg - a.bside_avg) || (b.bside_count - a.bside_count));
  const card3 = renderCategoryLeaderCard({
    title: 'B-Side Track Avg',
    gateLabel: `≥${cfg.minBsideTracks} b-sides`,
    accentClass: 'text-emerald-300',
    rowsHtml: buildRows(
      bsideLeaders,
      a => a.artist,
      null,
      a => Number(a.bside_avg).toFixed(2),
      a => `(${a.bside_count})`,
      a => `jumpToSingleArtist('${escapeJsArg(a.artist)}')`
    ),
    emptyMsg: `No ${prefix === 'solo' ? 'soloists' : 'groups'} with ≥${cfg.minBsideTracks} B-sides.`
  });

  // 4. Best Album (any release type meeting minimum rated track gate)
  const albumLeaders = [];
  artists.forEach(a => {
    (a.albums || []).forEach(alb => {
      if ((Number(alb.rated_tracks) || 0) >= cfg.minAlbumTracks) {
        albumLeaders.push({
          artist: a.artist,
          album_title: alb.album_title,
          avg_score: alb.avg_score,
          rated_tracks: alb.rated_tracks
        });
      }
    });
  });
  albumLeaders.sort((a, b) => (b.avg_score - a.avg_score) || (b.rated_tracks - a.rated_tracks));
  const card4 = renderCategoryLeaderCard({
    title: 'Best Album',
    gateLabel: `≥${cfg.minAlbumTracks} trk`,
    accentClass: 'text-sky-300',
    rowsHtml: buildRows(
      albumLeaders,
      alb => alb.album_title,
      alb => alb.artist,
      alb => Number(alb.avg_score).toFixed(2),
      alb => `(${alb.rated_tracks})`,
      alb => `jumpToLibraryAlbum('${escapeJsArg(alb.artist)}', '${escapeJsArg(alb.album_title)}')`
    ),
    emptyMsg: `No albums with ≥${cfg.minAlbumTracks} rated tracks.`
  });

  container.innerHTML = card1 + card2 + card3 + card4;
}

function renderTierList(type = 'group', artists = []) {
  const prefix = type === 'solo' ? 'solo' : 'group';
  const body = document.getElementById(`${prefix}-tier-list-body`);
  const summaryEl = document.getElementById(`${prefix}-tier-list-summary`);
  if (!body) return;

  const thresholds = TIER_LIST_CONFIG[prefix] || TIER_LIST_CONFIG.group;
  const buckets = { S: [], A: [], B: [], C: [], D: [], P: [] };

  artists.forEach(item => {
    const score = computeTierCompositeScore(item);
    const ratedTracks = Number(item.total_rated_tracks) || 0;
    let tierKey = 'D';
    if (ratedTracks < TIER_LIST_CONFIG.minRatedTracks) {
      tierKey = 'P';
    } else if (score >= thresholds.S) {
      tierKey = 'S';
    } else if (score >= thresholds.A) {
      tierKey = 'A';
    } else if (score >= thresholds.B) {
      tierKey = 'B';
    } else if (score >= thresholds.C) {
      tierKey = 'C';
    }
    buckets[tierKey].push({ ...item, _tierComposite: score });
  });

  Object.keys(buckets).forEach(k => {
    buckets[k].sort((a, b) => {
      if (b._tierComposite !== a._tierComposite) return b._tierComposite - a._tierComposite;
      return (a.artist || '').localeCompare(b.artist || '');
    });
  });

  if (summaryEl) {
    const rankedCount = artists.length - buckets.P.length;
    summaryEl.textContent = buckets.P.length > 0
      ? `(${rankedCount} ranked · ${buckets.P.length} provisional)`
      : `(${rankedCount} ranked)`;
  }

  if (artists.length === 0) {
    const emptyMsg = prefix === 'group' ? 'No groups match your filter criteria.' : 'No soloists cataloged yet.';
    body.innerHTML = `<div class="py-5 text-center text-xs text-slate-500">${emptyMsg}</div>`;
    return;
  }

  const rowsHtml = TIER_ROW_PALETTE.map(tier => {
    const items = buckets[tier.key] || [];
    // Only show Provisional shelf when there are provisional artists
    if (tier.key === 'P' && items.length === 0) return '';

    const labelStyle = tier.bgHex ? `style="background-color: ${tier.bgHex};"` : '';
    const labelBgClass = tier.bgClass || '';
    const rangeText = formatTierThresholdRange(tier.key, thresholds);
    const rangeHtml = rangeText
      ? `<span class="text-[9px] font-bold opacity-80">(${rangeText})</span>`
      : '';
    const boxTitle = tier.key === 'P'
      ? `Provisional: fewer than ${TIER_LIST_CONFIG.minRatedTracks} rated tracks`
      : `${tier.label} Tier (${tier.subLabel}): Composite ${rangeText}`;
    const subHtml = tier.subLabel
      ? `<span class="text-[9px] font-bold opacity-80 leading-none mt-1">${tier.subLabel}</span>`
      : '';

    const pillsHtml = items.length > 0
      ? items.map(a => {
          const scoreStr = a._tierComposite.toFixed(1);
          const qwarStr = (Number(a.compacted_qwar) || 0).toFixed(1);
          const rateStr = (Number(a.qwar_per_track) || 0).toFixed(2);
          const title = `${a.artist} — Composite: ${scoreStr} (Q-WAR: ${qwarStr}, Rate: ${rateStr}, Rated: ${a.total_rated_tracks || 0} trk)`;
          return `
            <button type="button"
                    onclick="jumpToSingleArtist('${escapeJsArg(a.artist)}')"
                    title="${escapeHtml(title)}"
                    class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-slate-900 hover:bg-slate-800 border border-slate-700/80 hover:border-indigo-500/60 text-xs font-medium text-slate-300 transition shadow-sm">
              <span>${escapeHtml(a.artist)}</span>
              <span class="text-[10px] font-mono text-slate-400">${scoreStr}</span>
            </button>
          `;
        }).join('')
      : `<span class="text-[11px] text-slate-600 italic">—</span>`;

    return `
      <div class="flex items-stretch min-h-[48px]">
        <div ${labelStyle}
             title="${escapeHtml(boxTitle)}"
             class="w-20 sm:w-24 shrink-0 px-1 py-1.5 flex flex-col items-center justify-center font-extrabold text-sm tracking-wide select-none border-r border-slate-900/40 ${labelBgClass} ${tier.textClass}">
          <div class="flex items-baseline justify-center gap-0.5 leading-none">
            <span>${tier.label}</span>
            ${rangeHtml}
          </div>
          ${subHtml}
        </div>
        <div class="flex-1 flex flex-wrap items-center gap-1.5 px-3 py-2 bg-slate-950/60">
          ${pillsHtml}
        </div>
      </div>
    `;
  }).join('');

  body.innerHTML = rowsHtml;
}

function renderArtistDashboardTable(type = 'group') {
  const isGroup = type === 'group';
  const genFilter = isGroup ? (document.getElementById("group-dash-gen")?.value || "") : "";
  const statusFilter = isGroup
    ? (document.getElementById("group-dash-status")?.value || "")
    : (document.getElementById("solo-dash-status")?.value || "");

  const sortColumn = isGroup ? allArtistsSortColumn : soloArtistsSortColumn;
  const sortAsc = isGroup ? allArtistsSortAsc : soloArtistsSortAsc;
  let activePage = isGroup ? allArtistsCurrentPage : soloArtistsCurrentPage;

  const filtered = allArtistsData.filter(d => {
    if (isGroup) {
      if ((d.artist_type || 'group') !== 'group') return false;
      if (genFilter && d.generation !== genFilter) return false;
    } else {
      if (d.artist_type !== 'solo') return false;
    }
    if (statusFilter && (d.status || "Active") !== statusFilter) return false;
    return true;
  });

  renderCategoryLeaders(type, filtered);
  renderTierList(type, filtered);

  sortRecords(filtered, sortColumn, sortAsc, (item, col) => {
    if (col === 'parent_groups') {
      return (item.parent_groups && item.parent_groups.length > 0)
        ? item.parent_groups.map(g => g.group_name).join(', ')
        : '';
    }
    return item[col];
  });

  activePage = updateDashboardPaginationControls(filtered.length, activePage);
  if (isGroup) {
    allArtistsCurrentPage = activePage;
  } else {
    soloArtistsCurrentPage = activePage;
  }

  const startIdx = (activePage - 1) * allArtistsPageSize;
  const paginated = filtered.slice(startIdx, startIdx + allArtistsPageSize);

  const tbody = document.getElementById(isGroup ? "group-artists-tbody" : "solo-artists-tbody");
  if (!tbody) return;

  if (paginated.length === 0) {
    const emptyMsg = isGroup ? "No groups match your filter criteria." : "No soloists cataloged yet.";
    tbody.innerHTML = `<tr><td colspan="11" class="py-6 text-center text-slate-500">${emptyMsg}</td></tr>`;
    return;
  }

  tbody.innerHTML = paginated.map(d => renderArtistSummaryRow(d, { isGroup })).join("");
}

/**
 * Unified table row renderer for Artist Dashboards and Multi-Artist Compare table.
 * Guarantees visual, data, and metric consistency across all 11 summary columns.
 */
function renderArtistSummaryRow(d, options = {}) {
  const isGroup = options.isGroup ?? ((d.artist_type || 'group') === 'group');
  const color = options.color || null;
  const statusBadge = getStatusBadge(d.status);
  const qwarDisplay = (d.compacted_qwar !== null && d.compacted_qwar !== undefined)
    ? parseFloat(d.compacted_qwar).toFixed(1)
    : '--';
  const qwarRateDisplay = (d.qwar_per_track !== null && d.qwar_per_track !== undefined)
    ? parseFloat(d.qwar_per_track).toFixed(2)
    : '--';

  let col2Html = "";
  if (isGroup) {
    col2Html = `<td class="py-2.5 px-1.5">${getGenerationBadge(d.generation)}</td>`;
  } else {
    const parentPills = (d.parent_groups && d.parent_groups.length > 0)
      ? d.parent_groups.map(pg => renderParentGroupBadge(pg, true)).join(" ")
      : `<span class="text-slate-600 text-[11px] italic">Indie Soloist</span>`;
    col2Html = `<td class="py-2.5 px-1.5 text-left">${parentPills}</td>`;
  }

  const artistCell = color
    ? `
      <span class="w-2.5 h-2.5 rounded-full inline-block shrink-0" style="background-color: ${color}"></span>
      <button type="button" onclick="jumpToSingleArtist('${escapeJsArg(d.artist)}')" 
              class="hover:underline text-left font-bold truncate" style="color: ${color}">
        ${escapeHtml(d.artist)}
      </button>
    `
    : `
      <button type="button" onclick="jumpToSingleArtist('${escapeJsArg(d.artist)}')" 
              class="hover:text-indigo-400 transition hover:underline text-left font-bold text-slate-200">
        ${escapeHtml(d.artist)}
      </button>
    `;

  return `
    <tr class="hover:bg-slate-800/40 whitespace-nowrap text-xs">
      <td class="py-2.5 px-3 flex items-center gap-2">
        ${artistCell}
      </td>
      ${col2Html}
      <td class="py-2.5 px-1.5">${statusBadge}</td>
      <td class="py-2.5 px-2 text-center font-mono font-black text-indigo-400 text-sm">${qwarDisplay}</td>
      <td class="py-2.5 px-2 text-center font-mono font-bold text-indigo-300">${qwarRateDisplay}</td>
      <td class="py-2.5 px-2 text-center font-mono font-bold text-amber-400">${d.overall_avg ? parseFloat(d.overall_avg).toFixed(2) : '--'}</td>
      <td class="py-2.5 px-2 text-center text-emerald-400 font-semibold">${d.overall_hit_rate}%</td>
      <td class="py-2.5 px-2 text-center text-amber-300 font-bold">${d.overall_perfect}</td>
      <td class="py-2.5 px-2 text-center text-emerald-400 font-semibold">${d.overall_perfect_pct}%</td>
      <td class="py-2.5 px-2.5 text-right text-slate-300">
        ${d.total_catalog_tracks ?? d.total_rated_tracks} <span class="text-slate-500 text-[11px]">(${d.plotted_albums_count} rel)</span>
      </td>
      <td class="py-2.5 px-2.5 text-right text-sky-400 font-mono">${d.tracks_per_year} / yr</td>
    </tr>
  `;
}

// Unified Pagination Controller for Dashboards
function updateDashboardPaginationControls(totalCount, activePage) {
  return applyPaginationControls({
    prefix: "all-artists",
    totalItems: totalCount,
    currentPage: activePage,
    pageSize: allArtistsPageSize
  });
}

function goToActiveDashboardPage(delta) {
  const isGroup = currentDashboardView === 'groups';
  if (isGroup) {
    allArtistsCurrentPage += delta;
  } else {
    soloArtistsCurrentPage += delta;
  }
  renderArtistDashboardTable(isGroup ? 'group' : 'solo');
}

function changeAllArtistsPageSize(size) {
  allArtistsPageSize = parseInt(size, 10);
  allArtistsCurrentPage = 1;
  soloArtistsCurrentPage = 1;
  document.querySelectorAll(".all-artists-page-size, #all-artists-page-size").forEach(sel => {
    sel.value = size;
  });
  renderArtistDashboardTable(currentDashboardView === 'groups' ? 'group' : 'solo');
}

// Helper to click an artist's name and jump straight to their deep-dive pane
function jumpToSingleArtist(artistName) {
  currentSingleArtist = artistName;
  const input = document.getElementById("analytics-artist-input");
  if (input) {
    input.value = artistName;
  }
  closeSingleArtistDropdown();
  switchAnalyticsSubView('single');
  renderAnalytics();
}

// Tracks up to 4 selected artists [Slot 0, Slot 1, Slot 2, Slot 3]
let compareSlotArtists = [null, null, null, null];
let allAvailableArtists = [];
const COMPARE_SLOT_STYLES = [
  { text: "text-indigo-300", bgSelected: "bg-indigo-950/70", checkColor: "text-indigo-400", ring: "ring-indigo-500" },
  { text: "text-emerald-300", bgSelected: "bg-emerald-950/70", checkColor: "text-emerald-400", ring: "ring-emerald-500" },
  { text: "text-amber-300", bgSelected: "bg-amber-950/70", checkColor: "text-amber-400", ring: "ring-amber-500" },
  { text: "text-rose-300", bgSelected: "bg-rose-950/70", checkColor: "text-rose-400", ring: "ring-rose-500" }
];
let compareSlotHighlightedIndices = [-1, -1, -1, -1];

async function initComparePills() {
  cachedCompareDataList = null;
  try {
    const res = await fetch("/api/analytics/artists");
    allAvailableArtists = await res.json();
    syncCompareSlotInputs();
    renderComparisonAnalytics();
  } catch (err) {
    console.error("Failed to initialize compare search slots", err);
  }
}

function syncCompareSlotInputs() {
  for (let i = 0; i < 4; i++) {
    const input = document.getElementById(`compare-slot-${i}`);
    if (input) {
      input.value = compareSlotArtists[i] || "";
    }
  }
}

function renderCompareSlotDropdown(slotIndex, filterText = "") {
  const dropdown = document.getElementById(`compare-slot-dropdown-${slotIndex}`);
  if (!dropdown) return;

  const cleanFilter = filterText.trim().toLowerCase();
  const matched = cleanFilter
    ? allAvailableArtists.filter(a => a.toLowerCase().includes(cleanFilter))
    : allAvailableArtists;

  if (matched.length === 0) {
    dropdown.innerHTML = `<div class="px-3 py-2 text-xs text-slate-500 italic">No matching artists</div>`;
    compareSlotHighlightedIndices[slotIndex] = -1;
    return;
  }

  const currentArtist = compareSlotArtists[slotIndex];
  const style = COMPARE_SLOT_STYLES[slotIndex];

  dropdown.innerHTML = matched.map((artist, idx) => {
    const isSelected = currentArtist && artist.toLowerCase() === currentArtist.toLowerCase();
    const otherSlotIdx = compareSlotArtists.findIndex((a, sIdx) => a && a.toLowerCase() === artist.toLowerCase() && sIdx !== slotIndex);
    const isOccupiedByOther = otherSlotIdx !== -1;
    const isHighlighted = idx === compareSlotHighlightedIndices[slotIndex];

    let itemClasses = "";
    if (isSelected) {
      itemClasses = `is-selected-artist ${style.bgSelected} ${style.text} font-semibold`;
    } else if (isOccupiedByOther) {
      itemClasses = `opacity-40 text-slate-500 hover:bg-slate-900 cursor-not-allowed`;
    } else {
      itemClasses = `text-slate-300 hover:bg-slate-800/80 hover:text-slate-100`;
    }

    const highlightCls = isHighlighted ? `ring-1 ${style.ring} bg-slate-800/80` : '';

    return `
      <div class="compare-combobox-item px-2.5 py-1.5 cursor-pointer text-xs transition flex items-center justify-between ${itemClasses} ${highlightCls}"
           data-artist="${escapeHtml(artist)}"
           ${isOccupiedByOther ? `data-occupied="${otherSlotIdx + 1}"` : ''}
           onmousedown="selectCompareSlotArtist(${slotIndex}, '${escapeHtml(artist).replace(/'/g, "\\'")}')">
        <span class="truncate">${escapeHtml(artist)}</span>
        <div class="flex items-center gap-1 shrink-0 ml-1.5">
          ${isOccupiedByOther ? `<span class="text-[10px] text-slate-500">(Slot ${otherSlotIdx + 1})</span>` : ''}
          ${isSelected ? `<span class="text-xs ${style.checkColor} font-bold">✓</span>` : ''}
        </div>
      </div>
    `;
  }).join("");

  // Auto-scroll to selected artist if opened without filter
  if (!cleanFilter && currentArtist) {
    const selectedEl = dropdown.querySelector(".is-selected-artist");
    if (selectedEl) {
      selectedEl.scrollIntoView({ block: "nearest" });
    }
  }
}

function openCompareSlotDropdown(slotIndex, filterText = "") {
  closeAllCompareDropdowns(slotIndex);
  closeSingleArtistDropdown();

  const dropdown = document.getElementById(`compare-slot-dropdown-${slotIndex}`);
  if (!dropdown) return;
  compareSlotHighlightedIndices[slotIndex] = -1;
  renderCompareSlotDropdown(slotIndex, filterText);
  dropdown.classList.remove("hidden");
}

function closeCompareSlotDropdown(slotIndex) {
  const dropdown = document.getElementById(`compare-slot-dropdown-${slotIndex}`);
  if (dropdown) {
    dropdown.classList.add("hidden");
    compareSlotHighlightedIndices[slotIndex] = -1;
  }
  const input = document.getElementById(`compare-slot-${slotIndex}`);
  if (input) {
    input.value = compareSlotArtists[slotIndex] || "";
  }
}

function closeAllCompareDropdowns(exceptSlot = -1) {
  for (let i = 0; i < 4; i++) {
    if (i !== exceptSlot) {
      closeCompareSlotDropdown(i);
    }
  }
}

function toggleCompareSlotDropdown(slotIndex, event) {
  if (event) event.stopPropagation();
  const dropdown = document.getElementById(`compare-slot-dropdown-${slotIndex}`);
  if (!dropdown) return;
  if (dropdown.classList.contains("hidden")) {
    openCompareSlotDropdown(slotIndex, "");
    const input = document.getElementById(`compare-slot-${slotIndex}`);
    if (input) input.focus();
  } else {
    closeCompareSlotDropdown(slotIndex);
  }
}

function selectCompareSlotArtist(slotIndex, artistName) {
  const otherSlotIdx = compareSlotArtists.findIndex((a, idx) => a && a.toLowerCase() === artistName.toLowerCase() && idx !== slotIndex);
  if (otherSlotIdx !== -1) {
    alert(`${artistName} is already selected in Slot ${otherSlotIdx + 1}.`);
    return;
  }

  compareSlotArtists[slotIndex] = artistName;
  cachedCompareDataList = null;
  syncCompareSlotInputs();
  closeCompareSlotDropdown(slotIndex);
  renderComparisonAnalytics();
}

function onCompareSlotFocus(slotIndex, input) {
  if (input) {
    input.select();
  }
}

function onCompareSlotInput(slotIndex, val) {
  const cleanVal = val.trim();
  openCompareSlotDropdown(slotIndex, cleanVal);

  if (!cleanVal) {
    cachedCompareDataList = null;
    compareSlotArtists[slotIndex] = null;
    renderComparisonAnalytics();
    return;
  }

  // Exact / case-insensitive match against catalog
  const matched = allAvailableArtists.find(a => a.toLowerCase() === cleanVal.toLowerCase());
  if (matched) {
    const otherSlotIdx = compareSlotArtists.findIndex((a, idx) => a && a.toLowerCase() === matched.toLowerCase() && idx !== slotIndex);
    if (otherSlotIdx === -1) {
      compareSlotArtists[slotIndex] = matched;
      cachedCompareDataList = null;
      renderComparisonAnalytics();
    }
  }
}

function onCompareSlotKeyDown(slotIndex, event) {
  const dropdown = document.getElementById(`compare-slot-dropdown-${slotIndex}`);
  const isDropdownOpen = dropdown && !dropdown.classList.contains("hidden");

  if (event.key === "Escape" || event.key === "Tab") {
    closeCompareSlotDropdown(slotIndex);
    return;
  }

  if (event.key === "ArrowDown") {
    event.preventDefault();
    if (!isDropdownOpen) {
      openCompareSlotDropdown(slotIndex, "");
      return;
    }
    const items = dropdown.querySelectorAll(".compare-combobox-item");
    if (items.length === 0) return;
    compareSlotHighlightedIndices[slotIndex] = (compareSlotHighlightedIndices[slotIndex] + 1) % items.length;
    updateHighlightedCompareItem(slotIndex, items);
    return;
  }

  if (event.key === "ArrowUp") {
    event.preventDefault();
    if (!isDropdownOpen) {
      openCompareSlotDropdown(slotIndex, "");
      return;
    }
    const items = dropdown.querySelectorAll(".compare-combobox-item");
    if (items.length === 0) return;
    compareSlotHighlightedIndices[slotIndex] = (compareSlotHighlightedIndices[slotIndex] - 1 + items.length) % items.length;
    updateHighlightedCompareItem(slotIndex, items);
    return;
  }

  if (event.key === "Enter") {
    if (isDropdownOpen) {
      event.preventDefault();
      const items = dropdown.querySelectorAll(".compare-combobox-item");
      const currentHighlight = compareSlotHighlightedIndices[slotIndex];
      if (currentHighlight >= 0 && currentHighlight < items.length) {
        const item = items[currentHighlight];
        const artist = item.getAttribute("data-artist");
        if (artist) selectCompareSlotArtist(slotIndex, artist);
      } else if (items.length > 0) {
        const firstAvailable = Array.from(items).find(el => !el.hasAttribute("data-occupied")) || items[0];
        const artist = firstAvailable.getAttribute("data-artist");
        if (artist) selectCompareSlotArtist(slotIndex, artist);
      }
    }
  }
}

function updateHighlightedCompareItem(slotIndex, items) {
  const style = COMPARE_SLOT_STYLES[slotIndex];
  items.forEach((item, idx) => {
    if (idx === compareSlotHighlightedIndices[slotIndex]) {
      item.classList.add("ring-1", style.ring, "bg-slate-800/80");
      item.scrollIntoView({ block: "nearest" });
    } else {
      item.classList.remove("ring-1", style.ring, "bg-slate-800/80");
    }
  });
}

function onCompareSlotChange(slotIndex, val) {
  onCompareSlotInput(slotIndex, val);
}

let cachedCompareDataList = null;

function clearCompareSlot(slotIndex) {
  cachedCompareDataList = null;
  compareSlotArtists[slotIndex] = null;
  closeCompareSlotDropdown(slotIndex);
  syncCompareSlotInputs();
  const input = document.getElementById(`compare-slot-${slotIndex}`);
  if (input) input.focus();
  renderComparisonAnalytics();
}

async function renderComparisonAnalytics() {
  // Collect valid slots with their designated color index
  const activeSlots = compareSlotArtists
    .map((artist, slotIdx) => ({ artist, slotIdx }))
    .filter(item => item.artist);

  const tbody = document.getElementById("compare-metrics-tbody");

  if (activeSlots.length === 0) {
    cachedCompareDataList = null;
    if (compareChartInstance) compareChartInstance.destroy();
    tbody.innerHTML = `<tr><td colspan="11" class="py-6 text-center text-slate-500">Search and select at least one artist above to display comparisons.</td></tr>`;
    return;
  }

  try {
    let dataList = cachedCompareDataList;
    if (!dataList) {
      const query = activeSlots.map(s => encodeURIComponent(s.artist)).join(",");
      const res = await fetch(`/api/analytics/compare?artists=${query}`);
      dataList = await res.json();
      cachedCompareDataList = dataList;
    }

    // 1. Populate Metrics Table with slot-matched color accents using shared row renderer
    tbody.innerHTML = dataList.map(d => {
      const slotItem = activeSlots.find(s => s.artist.toLowerCase() === d.artist.toLowerCase());
      const colorIdx = slotItem ? slotItem.slotIdx : 0;
      const color = COMPARE_COLORS[colorIdx % COMPARE_COLORS.length].border;
      return renderArtistSummaryRow(d, { color, isGroup: (d.artist_type || 'group') === 'group' });
    }).join("");

    // 2. Build Datasets based on Alignment & Metric Modes
    const alphaBase = 0.45;
    const refTracks = 6;
    const isQwar = (compareMetricMode === 'qwar');
    const isRate = (compareMetricMode === 'rate');
    const isSteppedMetric = isQwar || isRate;
    let datasets = [];
    let maxReleases = 0;

    const maxCompareQwar = Math.max(
      ...dataList.flatMap(d => (d.albums || []).map(a => a.cumulative_qwar || 0)),
      0
    );
    const maxCompareRate = Math.max(
      ...dataList.flatMap(d => (d.albums || []).map(a => a.cumulative_qwar_per_track || 0)),
      0
    );

    const compareQwarYAxis = {
      min: 0,
      suggestedMax: Math.max(100, Math.ceil(((maxCompareQwar || 80) + 15) / 10) * 10),
      ticks: {
        color: '#94a3b8',
        callback: (val) => `${val} pts`
      },
      grid: { color: '#1e293b' }
    };

    const compareRateYAxis = {
      min: 0,
      suggestedMax: Math.max(2.5, Math.ceil(((maxCompareRate || 2.0) + 0.3) * 10) / 10),
      ticks: {
        color: '#94a3b8',
        callback: (val) => `${val.toFixed(1)} pts/trk`
      },
      grid: { color: '#1e293b' }
    };

    const compareScoreYAxis = {
      min: 1.0,
      max: 5.0,
      ticks: { stepSize: 0.5, color: '#94a3b8' },
      grid: { color: '#1e293b' }
    };

    const compareActiveYAxis = isRate ? compareRateYAxis : (isQwar ? compareQwarYAxis : compareScoreYAxis);

    if (compareAlignmentMode === 'calendar') {
      // MODE 1: CALENDAR TIMELINE
      datasets = dataList.map(d => {
        const slotItem = activeSlots.find(s => s.artist.toLowerCase() === d.artist.toLowerCase());
        const colorIdx = slotItem ? slotItem.slotIdx : 0;
        const color = COMPARE_COLORS[colorIdx % COMPARE_COLORS.length];

        const standardAlbums = prepareSortedChartAlbums(d.albums, true);

        if (isSteppedMetric) {
          const points = standardAlbums.map((album, aIdx) => {
            const prevVal = aIdx > 0 ? (isRate ? (standardAlbums[aIdx - 1].cumulative_qwar_per_track || 0) : (standardAlbums[aIdx - 1].cumulative_qwar || 0)) : 0;
            const currentVal = isRate ? (album.cumulative_qwar_per_track || 0) : (album.cumulative_qwar || 0);
            const gained = parseFloat((currentVal - prevVal).toFixed(2));
            return {
              x: album.release_date,
              y: currentVal,
              title: album.album_title,
              album_type: album.album_type,
              points_gained: gained,
              cumulative_qwar: album.cumulative_qwar,
              cumulative_rate: album.cumulative_qwar_per_track,
              cumulative_avg: album.cumulative_avg_score
            };
          });

          return {
            label: d.artist,
            data: points,
            borderColor: color.border,
            backgroundColor: color.fill,
            borderWidth: 2.5,
            stepped: 'after',
            fill: false,
            pointRadius: (ctx) => {
              const raw = ctx.raw;
              return (raw && raw.album_type === 'LP') ? 6 : 3;
            },
            pointStyle: (ctx) => {
              const raw = ctx.raw;
              return (raw && raw.album_type === 'LP') ? 'rectRot' : 'circle';
            },
            pointHoverRadius: 7
          };
        } else {
          const emaSeries = computeVolumeWeightedEMA(standardAlbums, alphaBase, refTracks);
          const points = standardAlbums.map((album, aIdx) => ({
            x: album.release_date,
            y: emaSeries[aIdx],
            title: album.album_title,
            album_type: album.album_type,
            raw_score: album.avg_score
          }));

          return {
            label: d.artist,
            data: points,
            borderColor: color.border,
            backgroundColor: color.fill,
            borderWidth: 2.5,
            tension: 0.45,
            fill: false,
            pointRadius: (ctx) => {
              const raw = ctx.raw;
              return (raw && raw.album_type === 'LP') ? 6 : 2;
            },
            pointStyle: (ctx) => {
              const raw = ctx.raw;
              return (raw && raw.album_type === 'LP') ? 'rectRot' : 'circle';
            },
            pointHoverRadius: 6
          };
        }
      });

    } else {
      // MODE 2: RELEASE SEQUENCE (ORDINAL)
      datasets = dataList.map(d => {
        const slotItem = activeSlots.find(s => s.artist.toLowerCase() === d.artist.toLowerCase());
        const colorIdx = slotItem ? slotItem.slotIdx : 0;
        const color = COMPARE_COLORS[colorIdx % COMPARE_COLORS.length];

        const substantiveAlbums = prepareSortedChartAlbums(d.albums || [])
          .filter(a => a.album_type === 'EP' || a.album_type === 'LP');

        if (substantiveAlbums.length > maxReleases) {
          maxReleases = substantiveAlbums.length;
        }

        if (isSteppedMetric) {
          const points = substantiveAlbums.map((album, aIdx) => {
            const prevVal = aIdx > 0 ? (isRate ? (substantiveAlbums[aIdx - 1].cumulative_qwar_per_track || 0) : (substantiveAlbums[aIdx - 1].cumulative_qwar || 0)) : 0;
            const currentVal = isRate ? (album.cumulative_qwar_per_track || 0) : (album.cumulative_qwar || 0);
            const gained = parseFloat((currentVal - prevVal).toFixed(2));
            return {
              x: aIdx + 1,
              y: currentVal,
              title: album.album_title,
              album_type: album.album_type,
              points_gained: gained,
              cumulative_qwar: album.cumulative_qwar,
              cumulative_rate: album.cumulative_qwar_per_track,
              cumulative_avg: album.cumulative_avg_score
            };
          });

          return {
            label: d.artist,
            data: points,
            borderColor: color.border,
            backgroundColor: color.fill,
            borderWidth: 2.5,
            stepped: 'after',
            fill: false,
            pointRadius: (ctx) => {
              const raw = ctx.raw;
              return (raw && raw.album_type === 'LP') ? 7 : 3;
            },
            pointStyle: (ctx) => {
              const raw = ctx.raw;
              return (raw && raw.album_type === 'LP') ? 'rectRot' : 'circle';
            },
            pointHoverRadius: 7
          };
        } else {
          const emaSeries = computeVolumeWeightedEMA(substantiveAlbums, alphaBase, refTracks);
          const points = substantiveAlbums.map((album, aIdx) => ({
            x: aIdx + 1, // Ordinal position 1, 2, 3...
            y: emaSeries[aIdx],
            title: album.album_title,
            album_type: album.album_type,
            raw_score: album.avg_score
          }));

          return {
            label: d.artist,
            data: points,
            borderColor: color.border,
            backgroundColor: color.fill,
            borderWidth: 2.5,
            tension: 0.45,
            fill: false,
            pointRadius: (ctx) => {
              const raw = ctx.raw;
              return (raw && raw.album_type === 'LP') ? 7 : 3;
            },
            pointStyle: (ctx) => {
              const raw = ctx.raw;
              return (raw && raw.album_type === 'LP') ? 'rectRot' : 'circle';
            },
            pointHoverRadius: 7
          };
        }
      });
    }

    // 3. Render Chart
    const ctx = document.getElementById('compareScoreChart')?.getContext('2d');
    if (!ctx) return;
    if (compareChartInstance) {
      compareChartInstance.destroy();
    }

    const chartConfig = {
      type: 'line',
      plugins: [qwarTierBackgroundPlugin],
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'nearest', intersect: false },
        plugins: {
          qwarTierBackground: { metricMode: compareMetricMode },
          legend: {
            display: true,
            position: 'top',
            align: 'end',
            labels: { color: '#94a3b8', font: { size: 11 }, usePointStyle: true, boxWidth: 8 }
          },
          tooltip: {
            backgroundColor: '#0f172a',
            titleColor: '#e2e8f0',
            borderColor: '#334155',
            borderWidth: 1,
            callbacks: {
              title: (items) => {
                const raw = items[0].raw;
                if (compareAlignmentMode === 'calendar') {
                  return raw && raw.title ? `${raw.title} (${raw.album_type})` : items[0].label;
                }
                return `Comeback #${items[0].label}${raw && raw.title ? ': ' + raw.title : ''}`;
              },
              label: (item) => {
                const raw = item.raw;
                const lpTag = (raw && raw.album_type === 'LP') ? ' 💎 [LP]' : '';
                if (isRate) {
                  const gainedStr = (raw && raw.points_gained !== undefined) ? ` (${raw.points_gained >= 0 ? '+' : ''}${raw.points_gained})` : '';
                  return `${item.dataset.label}${lpTag}: ${raw ? raw.cumulative_rate : item.raw} pts/track${gainedStr}`;
                }
                if (isQwar) {
                  const gainedStr = (raw && raw.points_gained !== undefined) ? ` (+${raw.points_gained >= 0 ? raw.points_gained : 0} earned)` : '';
                  return `${item.dataset.label}${lpTag}: ${raw ? raw.cumulative_qwar : item.raw} pts${gainedStr}`;
                }
                return `${item.dataset.label}${lpTag}: ${raw ? raw.y : item.raw} / 5.0`;
              }
            }
          }
        },
        scales: {
          x: compareAlignmentMode === 'calendar' ? {
            type: 'time',
            time: { unit: 'year', displayFormats: { year: 'yyyy' }, tooltipFormat: 'yyyy-MM-dd' },
            ticks: { color: '#94a3b8', maxRotation: 0 },
            grid: { color: '#1e293b' }
          } : {
            type: 'linear',
            ticks: {
              color: '#94a3b8',
              stepSize: 1,
              callback: (val) => Number.isInteger(val) && val > 0 ? `#${val}` : ''
            },
            min: 1,
            max: Math.max(maxReleases, 5),
            grid: { color: '#1e293b' }
          },
          y: compareActiveYAxis
        }
      }
    };

    compareChartInstance = new Chart(ctx, chartConfig);

  } catch (err) {
    console.error("Failed to render comparison analytics", err);
  }
}

function getScoreColor(rating) {
  if (rating === 5.0) return "text-purple-400";
  if (rating >= 4.0) return "text-indigo-400";
  if (rating >= 3.0) return "text-sky-400";
  if (rating >= 2.0) return "text-teal-400";
  if (rating !== null) return "text-slate-400";
  return "text-slate-600";
}

function initSearchKeyListeners() {
  // 1. Bulk Discography: Pressing Enter triggers discography search
  const bulkArtistInput = document.getElementById("bulk-artist");
  if (bulkArtistInput) {
    bulkArtistInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        fetchDiscography();
      }
    });
  }

  // 2. Single Album Fetch: Smart field flow & submission
  const singleArtistInput = document.getElementById("search-artist");
  const singleAlbumInput = document.getElementById("search-album");

  if (singleArtistInput && singleAlbumInput) {
    singleArtistInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        if (singleAlbumInput.value.trim()) {
          fetchAlbum();
        } else {
          // If album title is still empty, smoothly advance cursor to album field
          singleAlbumInput.focus();
        }
      }
    });

    singleAlbumInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        if (singleArtistInput.value.trim()) {
          fetchAlbum();
        } else {
          singleArtistInput.focus();
        }
      }
    });
  }
}

function initAlbumDateFilterLabels() {
  const now = new Date();
  const currentYear = now.getFullYear();
  const prevYear = currentYear - 1;

  ["album-filter-date", "track-filter-date"].forEach(id => {
    const dateSelect = document.getElementById(id);
    if (!dateSelect) return;
    const curOpt = dateSelect.querySelector('option[value="current_year"]');
    if (curOpt) curOpt.textContent = `This Calendar Year (${currentYear})`;
    const prevOpt = dateSelect.querySelector('option[value="prev_year"]');
    if (prevOpt) prevOpt.textContent = `Last Calendar Year (${prevYear})`;
  });
}

// ==========================================
// ARTIST WATCHLIST (PROSPECTS QUEUE)
// ==========================================

let watchlistData = [];

async function loadWatchlist() {
  try {
    const res = await fetch("/api/watchlist");
    if (!res.ok) return;
    watchlistData = await res.json();

    renderWatchlistTable(watchlistData);
  } catch (err) {
    console.error("Failed to load watchlist", err);
  }
}

let currentWatchlistItems = [];

function renderWatchlistTable(items) {
  currentWatchlistItems = Array.isArray(items) ? items : [];
  const tbody = document.getElementById("watchlist-tbody");
  const countEl = document.getElementById("watchlist-count");
  if (countEl) countEl.innerText = items.length;
  if (!tbody) return;

  if (items.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="6" class="py-6 text-center text-slate-500 italic">No prospective artists queued on the watchlist.</td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = items.map(item => {
    const isSolo = item.artist_type === "solo";
    const statusBadge = getStatusBadge(item.status || 'Active');

    let predictionCell = '';
    if (item.predicted_score != null) {
      const scoreVal = Number(item.predicted_score);
      const scoreClass = scoreVal >= 4.0
        ? "bg-emerald-950/70 text-emerald-300 border-emerald-800/80"
        : (scoreVal >= 3.5
          ? "bg-yellow-800/70 text-yellow-300 border-yellow-700/80"
          : "bg-slate-800/70 text-slate-300 border-slate-700/80");

      let tooltipText = "Predicted compatibility across top Deezer tracks";
      if (item.predicted_tracks_json) {
        try {
          const parsedTracks = JSON.parse(item.predicted_tracks_json);
          if (Array.isArray(parsedTracks) && parsedTracks.length > 0) {
            const lines = parsedTracks.slice(0, 5).map((t, idx) =>
              `${idx + 1}. ${t.title} — ${Number(t.blend).toFixed(2)}★ (${Math.round(Number(t.hit_prob || 0) * 100)}% Hit)`
            );
            tooltipText = `Top Predicted Tracks:\n${lines.join("\n")}`;
          }
        } catch (_) {}
      }

      if (item.predicted_at) {
        tooltipText += `\n\nLast refreshed: ${item.predicted_at}`;
      }
      const predictedAtHtml = item.predicted_at
        ? `<div class="text-[10px] text-slate-500 mt-0.5 font-mono">Updated: ${escapeHtml(item.predicted_at)}</div>`
        : "";

      predictionCell = `
        <div>
          <div class="inline-flex items-center gap-1.5">
            <span class="text-[10px] px-1 py-0.5 rounded border font-mono font-semibold cursor-help ${scoreClass}"
                  title="${escapeHtml(tooltipText)}">
              ${scoreVal.toFixed(2)}★ (${item.predicted_hits ?? 0}/${item.predicted_total ?? 0})
            </span>
            <button type="button" onclick="predictWatchlistArtist(${item.id}, this)"
                    class="text-slate-500 hover:text-indigo-400 text-xs px-1 transition"
                    title="Refresh taste prediction">
              ↻
            </button>
          </div>
          ${predictedAtHtml}
        </div>
      `;
    } else {
      predictionCell = `
        <button type="button" onclick="predictWatchlistArtist(${item.id}, this)"
                class="px-2.5 py-1 bg-indigo-950/80 text-indigo-300 hover:bg-indigo-900 border border-indigo-800/80 rounded text-[11px] font-medium transition"
                title="Predict taste compatibility using SonicTaste (STC) audio model">
          ✨ Predict
        </button>
      `;
    }

    // Member Subtitle Logic (Inline format: " • X members" next to artist name)
    let memberSubtext = '';
    if (!isSolo && item.members) {
      memberSubtext = formatGroupMemberCountBadge(item.members);
    }

    const genBadge = item.generation 
      ? getGenerationBadge(item.generation)
      : '<span class="text-slate-600">—</span>';

    const dateAddedHtml = item.date_added
      ? `<div class="text-[10px] text-slate-500 mt-0.5 font-mono">Added on: ${escapeHtml(item.date_added)}</div>`
      : '';
    const notesStr = item.notes ? escapeHtml(item.notes) : '<span class="text-slate-600">—</span>';

    return `
      <tr class="hover:bg-slate-800/40">
        <td class="py-2.5 px-3">
          <div class="flex items-baseline whitespace-nowrap">
            <button type="button" onclick="editWatchlistEntry(${item.id})"
                    class="text-left font-semibold text-slate-200 hover:text-indigo-400 hover:underline transition"
                    title="Click to edit ${escapeHtml(item.name)}">
              ${escapeHtml(item.name)}
            </button>
            ${memberSubtext}
          </div>
          ${dateAddedHtml}
        </td>
        <td class="py-2.5 px-2">${genBadge}</td>
        <td class="py-2.5 px-2">${statusBadge}</td>
        <td class="py-2.5 px-2 whitespace-nowrap">${predictionCell}</td>
        <td class="py-2.5 px-3 text-slate-400 max-w-md truncate" title="${escapeHtml(item.notes || '')}">
          ${notesStr}
        </td>
        <td class="py-2.5 px-3 text-right whitespace-nowrap space-x-1.5">
          <button type="button" onclick="promoteWatchlistArtist('${escapeJsArg(item.name)}')"
                  class="px-2 py-1 bg-emerald-950 text-emerald-400 hover:bg-emerald-900 border border-emerald-800 rounded text-[11px] font-medium transition"
                  title="Search discography on MusicBrainz to catalog">
            📥 Import
          </button>
          <button type="button" onclick="deleteWatchlistEntry(${item.id}, '${escapeJsArg(item.name)}')"
                  class="px-2 py-1 text-slate-500 hover:text-rose-400 rounded text-[11px] transition"
                  title="Remove from watchlist">
            ✕
          </button>
        </td>
      </tr>
    `;
  }).join("");
}

function promoteWatchlistArtist(artistName) {
  switchMainView("management");
  switchLibrarySubView("io");
  switchImportMode("bulk");
  const input = document.getElementById("bulk-artist");
  if (input) {
    input.value = artistName;
    input.scrollIntoView({ behavior: 'smooth', block: 'center' });
    fetchDiscography();
  }
}

function toggleWatchlistTypeFields() {
  const type = document.getElementById("watchlist-type")?.value;
  const parentField = document.getElementById("field-watchlist-parent");
  const memberField = document.getElementById("field-watchlist-members");
  if (type === "solo") {
    if (parentField) parentField.classList.remove("hidden");
    if (memberField) memberField.classList.add("hidden");
  } else {
    if (parentField) parentField.classList.add("hidden");
    if (memberField) memberField.classList.remove("hidden");
  }
}

function openWatchlistModal(defaultType = 'group') {
  document.getElementById("watchlist-id").value = "";
  document.getElementById("watchlist-name").value = "";
  document.getElementById("watchlist-type").value = defaultType;
  const genEl = document.getElementById("watchlist-gen");
  if (genEl) genEl.value = "";
  const statusEl = document.getElementById("watchlist-status");
  if (statusEl) statusEl.value = "Active";
  const memberEl = document.getElementById("watchlist-members");
  if (memberEl) memberEl.value = "";
  const parentEl = document.getElementById("watchlist-parent-groups");
  if (parentEl) parentEl.value = "";
  document.getElementById("watchlist-notes").value = "";
  document.getElementById("watchlist-modal-title").innerText = "Add Artist to Watchlist";
  toggleWatchlistTypeFields();
  document.getElementById("watchlist-modal").classList.remove("hidden");
  document.getElementById("watchlist-name").focus();
}

function editWatchlistEntry(id) {
  const item = watchlistData.find(w => w.id === id);
  if (!item) return;

  document.getElementById("watchlist-id").value = item.id;
  document.getElementById("watchlist-name").value = item.name || "";
  document.getElementById("watchlist-type").value = item.artist_type || "group";
  
  const genEl = document.getElementById("watchlist-gen");
  if (genEl) genEl.value = item.generation || "";
  
  const statusEl = document.getElementById("watchlist-status");
  if (statusEl) statusEl.value = item.status || "Active";
  
  const memberEl = document.getElementById("watchlist-members");
  if (memberEl) memberEl.value = item.members || "";

  const parentEl = document.getElementById("watchlist-parent-groups");
  if (parentEl) parentEl.value = item.parent_groups || "";
  
  document.getElementById("watchlist-notes").value = item.notes || "";
  
  document.getElementById("watchlist-modal-title").innerText = `Edit Watchlist Artist: ${item.name}`;
  toggleWatchlistTypeFields();
  document.getElementById("watchlist-modal").classList.remove("hidden");
  document.getElementById("watchlist-name").focus();
}

function closeWatchlistModal() {
  document.getElementById("watchlist-modal").classList.add("hidden");
  document.getElementById("watchlist-id").value = "";
}

async function submitWatchlistEntry() {
  const name = document.getElementById("watchlist-name").value.trim();
  if (!name) return alert("Please enter an artist name.");

  const editId = document.getElementById("watchlist-id").value;
  const artistType = document.getElementById("watchlist-type").value;

  const payload = {
    name: name,
    artist_type: artistType,
    generation: document.getElementById("watchlist-gen")?.value || null,
    status: document.getElementById("watchlist-status")?.value || "Active",
    members: artistType === "group" ? (document.getElementById("watchlist-members")?.value.trim() || null) : null,
    parent_groups: artistType === "solo" ? (document.getElementById("watchlist-parent-groups")?.value.trim() || null) : null,
    notes: document.getElementById("watchlist-notes")?.value.trim() || null
  };

  try {
    const url = editId ? `/api/watchlist/${editId}` : "/api/watchlist";
    const method = editId ? "PUT" : "POST";
    const res = await fetch(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || (editId ? "Failed to update artist" : "Failed to add artist"));
    }
    closeWatchlistModal();
    loadWatchlist();
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

async function predictWatchlistArtist(id, btnEl) {
  const originalText = btnEl ? btnEl.innerHTML : "";
  if (btnEl) {
    btnEl.disabled = true;
    btnEl.innerHTML = "⏳";
    btnEl.classList.add("opacity-60", "cursor-wait");
  }
  try {
    const res = await fetch(`/api/watchlist/${id}/predict`, { method: "POST" });
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || "Failed to run taste prediction");
    }
    await loadWatchlist();
  } catch (err) {
    alert(`Prediction error: ${err.message}`);
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.innerHTML = originalText;
      btnEl.classList.remove("opacity-60", "cursor-wait");
    }
  }
}

async function refreshAllWatchlistPredictions(btnEl) {
  const items = currentWatchlistItems || [];
  if (items.length === 0) return;

  const originalText = btnEl ? btnEl.innerHTML : "↻";
  if (btnEl) {
    btnEl.disabled = true;
    btnEl.classList.add("opacity-60", "cursor-wait");
  }

  const failures = [];
  try {
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (btnEl) btnEl.innerHTML = `⏳ ${i + 1}/${items.length}`;
      try {
        const res = await fetch(`/api/watchlist/${item.id}/predict`, { method: "POST" });
        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          failures.push(`${item.name}: ${errData.detail || "Failed"}`);
        }
      } catch (err) {
        failures.push(`${item.name}: ${err.message}`);
      }
    }
    await loadWatchlist();
    if (failures.length > 0) {
      alert(`Some predictions could not be refreshed:\n${failures.join("\n")}`);
    }
  } finally {
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.innerHTML = originalText;
      btnEl.classList.remove("opacity-60", "cursor-wait");
    }
  }
}

async function deleteWatchlistEntry(id, name) {
  if (!confirm(`Remove "${name}" from the watchlist?`)) return;
  try {
    const res = await fetch(`/api/watchlist/${id}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Failed to delete entry");
    loadWatchlist();
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

// Global click listener to close popups and comboboxes when clicking outside
document.addEventListener("click", (e) => {
  const container = document.getElementById("single-artist-combobox-container");
  if (container && !container.contains(e.target)) {
    closeSingleArtistDropdown();
  }
  for (let i = 0; i < 4; i++) {
    const compareContainer = document.getElementById(`compare-slot-combobox-container-${i}`);
    if (compareContainer && !compareContainer.contains(e.target)) {
      closeCompareSlotDropdown(i);
    }
  }
});

// Initial Startup
applyQwarBenchmarkLayout();
loadLibrary();
loadLibraryArtists();
initArtistSelector();
loadTracksData();
initSearchKeyListeners();
initAlbumDateFilterLabels();
loadWatchlist();
