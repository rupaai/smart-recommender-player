// Mobile-First Cinema Companion Client (static/app.js)

const state = {
  activeTab: 'discover',
  recMediaType: 'movie',    // 'movie' or 'tv'
  recProfile: 'together',  // 'me' | 'partner' | 'together' — whose taste to optimise for
  recPage: 1,            // recommendation page for "load more"
  originsLoaded: false,  // "From" chips fetched from /api/origins
  recHasMore: false,     // more results available in the pool
  dismissedIds: new Set(), // items tapped away ("not this one")
  recSessionId: null,      // feedback log: the Discover session these results belong to
  directMediaType: 'movie', // 'movie' or 'tv'
  activeMoodId: 8,
  activeSubMood: null,
  selectedGenres: new Set(),
  era: '',
  duration: '',
  minRating: null,
  includeWatched: false,
  activeSession: null,
  selectedRating: 8,
  recognition: null,
  modalRecognition: null,
  jellyfinUrl: '',
  directSortBySize: true,
  lastDirectResults: [],
  activeDownloads: {},     // movieId -> status dict
  pollingIntervals: {},    // movieId -> intervalId
  moodsData: [],           // loaded from /api/moods
  // --- Rate Together (multi-profile co-view ratings) ---
  rateProfileId: 1,
  rateMediaType: 'movie',
  rateProfiles: [],
  rateQueue: [],
  rateIndex: 0,
  rateCursor: 0,           // how many items fetched so far (for "Load more")
  rateGenres: [],          // active genre chips ([] = anything)
  rateGenreList: [],       // catalogue-derived [{name, count}]
  rateSeed: null,          // null = 10-minute rotation bucket
  rateBalanced: true,      // walk the genre wheel (build a profile fast)
  rateSkipped: new Set()   // item ids skipped this session
};

const TABS = ['discover', 'direct', 'rate', 'history'];
const TAB_IDS = { discover: 'tabDiscover', direct: 'tabDirect', rate: 'tabRate', history: 'tabHistory' };
const VIEW_IDS = { discover: 'discoverView', direct: 'directView', rate: 'rateView', history: 'historyView' };

// Initialize app on DOM ready
document.addEventListener('DOMContentLoaded', () => {
  initPWA();
  checkStatus();
  loadMoods();
  checkActiveWatch();
  initStarSelector();
  initSpeechRecognition();
  loadPartnerName();
  updateFiltersSummary();

  // Open the tab named in the URL (#rate etc.) so a refresh or a home-screen
  // shortcut lands where you left off; otherwise Discover. Discover is
  // initialised here too — it used to load its "From" chips only after
  // switching away and back.
  const fromHash = location.hash.replace('#', '');
  switchTab(TABS.includes(fromHash) ? fromHash : 'discover', { initial: true });
});

window.addEventListener('hashchange', () => {
  const tab = location.hash.replace('#', '');
  if (TABS.includes(tab) && tab !== state.activeTab) switchTab(tab);
});

// Escape closes whichever sheet is open.
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (document.getElementById('feedbackModal').classList.contains('active')) closeFeedbackModal();
  if (document.getElementById('playerModal').classList.contains('active')) closePlayerModal();
});

// ---------------------------------------------------------------------------
// 0. Small UI helpers
// ---------------------------------------------------------------------------
// A value safe to drop into an inline onclick="fn(…)" attribute: JSON makes
// it a valid JS literal (quotes, apostrophes, unicode), escapeHtml makes the
// attribute survive. The old `.replace(/'/g, "\\'")` broke on titles with ".
function jsArg(value) {
  return escapeHtml(JSON.stringify(value == null ? '' : value));
}

function icon(name, cls = 'icon') {
  return `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
}

function setPressed(el, on) {
  if (!el) return;
  el.classList.toggle('active', !!on);
  el.setAttribute('aria-pressed', on ? 'true' : 'false');
}

// Poster with a typographic fallback. via.placeholder.com (used before) is
// gone, so missing posters rendered as broken-image alt text.
function posterHtml(url, title, cls) {
  if (!url) return posterFallbackHtml(title, cls);
  return `<img class="${cls}" src="${escapeHtml(url)}" alt="" loading="lazy"
    data-title="${escapeHtml(title || '')}" onerror="posterFailed(this)">`;
}

function posterFallbackHtml(title, cls) {
  return `<div class="${cls} poster-fallback" aria-hidden="true">${escapeHtml(title || '')}</div>`;
}

function posterFailed(img) {
  img.outerHTML = posterFallbackHtml(img.dataset.title, img.className);
}

let toastTimer = null;
function showToast(message) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2600);
}

// Partner's real name on the "His picks" control once profiles are renamed.
async function loadPartnerName() {
  try {
    const res = await fetch('/api/profiles');
    const profiles = await res.json();
    if (!Array.isArray(profiles) || profiles.length < 2) return;
    state.rateProfiles = state.rateProfiles.length ? state.rateProfiles : profiles;
    const partner = [...profiles].sort((a, b) => a.id - b.id)[1];
    const btn = document.querySelector('[data-rec-profile="partner"]');
    if (btn && partner && partner.name && partner.name !== 'Partner') {
      btn.textContent = `${partner.name}'s picks`;
    }
  } catch (_) { /* label stays "His picks" */ }
}

// PWA Service Worker Registration
function initPWA() {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js').catch(err => console.log('SW error:', err));
  }
}

// ---------------------------------------------------------------------------
// 1. Status & Jellyfin Connection
// ---------------------------------------------------------------------------
async function checkStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    const dot = document.getElementById('statusDot');
    const text = document.getElementById('statusText');
    state.jellyfinUrl = data.jellyfin_url || 'http://localhost:8096';

    if (data.jellyfin_online) {
      dot.className = 'status-dot online';
      text.innerText = 'TV ready';
    } else {
      dot.className = 'status-dot offline';
      text.innerText = 'TV offline';
    }
  } catch (err) {
    console.error('Status check error:', err);
    document.getElementById('statusText').innerText = 'TV status unknown';
  }
}

// ---------------------------------------------------------------------------
// 2. Navigation Tabs
// ---------------------------------------------------------------------------
function switchTab(tab, opts = {}) {
  const changed = state.activeTab !== tab;
  state.activeTab = tab;

  TABS.forEach(t => {
    const btn = document.getElementById(TAB_IDS[t]);
    btn.classList.toggle('active', t === tab);
    btn.setAttribute('aria-selected', t === tab ? 'true' : 'false');
    document.getElementById(VIEW_IDS[t]).style.display = t === tab ? 'block' : 'none';
  });

  if (!opts.initial) {
    if (location.hash !== `#${tab}`) history.replaceState(null, '', `#${tab}`);
    if (changed) window.scrollTo({ top: 0 });
  }

  if (tab === 'history') {
    loadHistory();
  }
  if (tab === 'rate') {
    initRateTab();
  }
  if (tab === 'discover' && !state.originsLoaded) {
    state.originsLoaded = true;
    initDiscoverTab();
  }
}

// ---------------------------------------------------------------------------
// 3. Recommender: Movie / TV Toggle & Mood Picker
// ---------------------------------------------------------------------------
// Origin chips. A region is a PLACE, not a genre, so it cannot be expressed
// with the genre chips — the recommender parses it out of the free-text query.
// Selecting one writes the phrase the parser recognises into the query box,
// so there is a single code path for "South Asian" whether typed or clicked.
// Group chips use the group's own name (lower-cased) as the phrase; country
// chips map to a phrase the recommender's parse_origin_intent() recognises.
// Clicking therefore writes exactly what typing the request would.
const ORIGIN_PHRASES = {
  'South Asian': 'south asian', 'East Asian': 'east asian',
  'Latin American': 'latin american', Korean: 'korean',
  IN: 'indian', JP: 'japanese', KR: 'korean', CN: 'chinese',
  HK: 'chinese', TW: 'chinese', ES: 'spanish language', FR: 'french',
  DE: 'german', IT: 'italian', BR: 'portuguese', MX: 'spanish language',
  NG: 'nigerian', IR: 'iranian', EG: 'egyptian', TR: 'turkish', RU: 'russian',
  BD: 'bangladeshi', PK: 'pakistani', LK: 'sri lankan', NP: 'nepali',
  TH: 'thai', ID: 'indonesian', PH: 'filipino', VN: 'vietnamese',
  GR: 'greek', SE: 'swedish', DK: 'danish', NO: 'norwegian', FI: 'finnish',
  NL: 'dutch', PL: 'polish', UA: 'ukrainian', AR: 'argentine',
  CO: 'colombian', CL: 'chilean', SA: 'arabic'
};

async function loadOrigins() {
  const container = document.getElementById('originChips');
  if (!container) return;
  try {
    const res = await fetch('/api/origins');
    const data = await res.json();
    const origins = data.origins || [];
    const group = document.getElementById('originGroup');
    if (!origins.length) { container.innerHTML = ''; if (group) group.hidden = true; return; }
    if (group) group.hidden = false;
    container.innerHTML = origins.map(o => `
      <button class="chip" data-origin-code="${escapeHtml(o.code)}" aria-pressed="false"
              onclick="setOrigin(${jsArg(String(o.code))})"
              title="${escapeHtml(o.count)} titles from here in your catalogue">
        ${escapeHtml(o.name)}<span class="chip-count">${escapeHtml(o.count)}</span>
      </button>`).join('');
    syncOriginChipFromQuery(document.getElementById('queryInput').value);
  } catch (err) {
    console.warn('Could not load origin chips:', err);
    container.innerHTML = '';
    const group = document.getElementById('originGroup');
    if (group) group.hidden = true;
  }
}

// Keeps the "From" chip highlighted when the origin phrase was typed by hand
// rather than clicked, so the UI never disagrees with the request it sent.
function syncOriginChipFromQuery(query) {
  const text = (query || '').toLowerCase();
  let matched = null;
  document.querySelectorAll('[data-origin-code]').forEach(btn => {
    const code = btn.dataset.originCode;
    const phrase = ORIGIN_PHRASES[code];
    const on = phrase && text.includes(phrase);
    setPressed(btn, on);
    if (on && !matched) matched = code;
  });
  updateFiltersSummary();
  return matched;
}

function setOrigin(code) {
  const phrase = ORIGIN_PHRASES[code] || code.toLowerCase();
  const input = document.getElementById('queryInput');
  const current = (input.value || '').trim();
  const active = document.querySelector('.chip[data-origin-code].active');
  const already = active && active.dataset.originCode === code;

  // Tapping the active region clears it, so the chip is a toggle.
  if (already) {
    input.value = current.replace(new RegExp(`\\b${phrase}\\b`, 'i'), '').trim();
  } else {
    input.value = current ? `${phrase} ${current}` : `${phrase} movies`;
  }
  document.querySelectorAll('[data-origin-code]').forEach(b => setPressed(b, false));
  if (!already) {
    // Group names contain spaces; CSS.escape keeps the selector valid.
    const btn = document.querySelector(`[data-origin-code="${CSS.escape(code)}"]`);
    setPressed(btn, true);
  }
  updateFiltersSummary();
}

function hasResults() {
  return !!document.querySelector('#resultsContainer .movie-card');
}

function setRecProfile(profile) {
  if (state.recProfile === profile) return;
  state.recProfile = profile;
  document.querySelectorAll('[data-rec-profile]').forEach(btn => {
    setPressed(btn, btn.dataset.recProfile === profile);
  });
  // Re-rank what's already on screen; before the first search there is
  // nothing to refresh, so don't fire a request the user didn't ask for.
  if (hasResults()) fetchRecommendations();
}

function recommendLabel() {
  return `Find a ${state.recMediaType === 'tv' ? 'show' : 'movie'} for tonight`;
}

function setRecMediaType(type) {
  state.recMediaType = type;
  setPressed(document.getElementById('recTypeMovieBtn'), type === 'movie');
  setPressed(document.getElementById('recTypeTvBtn'), type === 'tv');

  // Hide duration filter for TV Shows
  const durationGroup = document.getElementById('durationFilterGroup');
  if (durationGroup) durationGroup.hidden = type !== 'movie';

  const label = document.getElementById('recommendLabel');
  if (label) label.textContent = recommendLabel();
  updateFiltersSummary();
}

async function loadMoods() {
  try {
    const res = await fetch('/api/moods');
    state.moodsData = await res.json();
    const grid = document.getElementById('moodGrid');
    grid.innerHTML = '';

    state.moodsData.forEach(mood => {
      // Real buttons, so moods are reachable by keyboard and announced.
      const card = document.createElement('button');
      card.type = 'button';
      card.className = 'mood-card';
      card.id = `mood-${mood.id}`;
      setPressed(card, mood.id === state.activeMoodId);
      card.onclick = () => selectMood(mood.id);
      if (mood.vibe) card.title = mood.vibe;

      // Mood names arrive as "😊 Happy / Feel-Good". The emoji is dropped:
      // the lettering and the mood's own vibe line carry the tile.
      const cleanName = mood.name.replace(/^[\p{Extended_Pictographic}\p{Emoji_Presentation}️‍\s]+/u, '').trim();
      // Non-breaking hyphens so "Feel-Good" never splits across lines, and a
      // no-break space before the slash so a line never starts with "/".
      const display = escapeHtml(cleanName).replace(/-/g, '‑').replace(/ \/ /g, ' / ');

      card.innerHTML = `
        <span class="mood-name">${display}</span>
        ${mood.vibe ? `<span class="mood-vibe">${escapeHtml(mood.vibe)}</span>` : ''}
      `;
      grid.appendChild(card);
    });

    renderSubMoods(state.activeMoodId);
  } catch (err) {
    console.error('Error loading moods:', err);
    document.getElementById('moodGrid').innerHTML =
      '<p class="rate-sub">Moods couldn\'t load — you can still describe what you want above.</p>';
  }
}

function selectMood(moodId) {
  state.activeMoodId = moodId;
  state.activeSubMood = null;
  document.querySelectorAll('.mood-card').forEach(c => setPressed(c, c.id === `mood-${moodId}`));
  renderSubMoods(moodId);
}

function renderSubMoods(moodId) {
  const section = document.getElementById('submoodSection');
  const chips = document.getElementById('submoodChips');
  if (!section || !chips) return;

  const moodInfo = state.moodsData.find(m => m.id === moodId);
  if (!moodInfo || !moodInfo.submoods || moodInfo.submoods.length === 0) {
    section.style.display = 'none';
    return;
  }

  section.style.display = 'block';
  chips.innerHTML = '';

  const pick = (name, chip) => {
    state.activeSubMood = name;
    chips.querySelectorAll('.chip').forEach(c => setPressed(c, c === chip));
  };

  const allChip = document.createElement('button');
  allChip.className = 'chip';
  allChip.innerText = 'Any';
  setPressed(allChip, !state.activeSubMood);
  allChip.onclick = () => pick(null, allChip);
  chips.appendChild(allChip);

  moodInfo.submoods.forEach(sm => {
    const chip = document.createElement('button');
    chip.className = 'chip';
    chip.innerText = sm.name;
    setPressed(chip, state.activeSubMood === sm.name);
    chip.onclick = () => pick(sm.name, chip);
    chips.appendChild(chip);
  });
}

// ---------------------------------------------------------------------------
// 4. Recommender: Extra Options (Genres, Rating, Watched)
// ---------------------------------------------------------------------------
function toggleGenreFilter(btn) {
  const genre = btn.getAttribute('data-genre');
  const on = !state.selectedGenres.has(genre);
  if (on) state.selectedGenres.add(genre); else state.selectedGenres.delete(genre);
  setPressed(btn, on);
  updateFiltersSummary();
}

function clearSelectedGenres() {
  state.selectedGenres.clear();
  document.querySelectorAll('#genreChips .chip').forEach(c => setPressed(c, false));
  updateFiltersSummary();
}

function setFilter(btn) {
  const filterType = btn.getAttribute('data-filter');
  const val = btn.getAttribute('data-val');

  btn.parentElement.querySelectorAll('.chip').forEach(c => setPressed(c, c === btn));

  if (filterType === 'era') state.era = val;
  if (filterType === 'duration') state.duration = val;
  updateFiltersSummary();
}

function setMinRatingFilter(btn) {
  const val = btn.getAttribute('data-val');
  btn.parentElement.querySelectorAll('[data-filter="minRating"]').forEach(c => setPressed(c, c === btn));
  state.minRating = val === 'default' ? null : parseFloat(val);
  updateFiltersSummary();
}

function toggleIncludeWatched() {
  const box = document.getElementById('includeWatchedBtn');
  state.includeWatched = box ? box.checked : !state.includeWatched;
  updateFiltersSummary();
}

// One line on the collapsed "More filters" row saying what is switched on,
// so a filter set last week can't silently shrink tonight's results.
function updateFiltersSummary() {
  const el = document.getElementById('filtersSummary');
  if (!el) return;
  const parts = [];
  const origin = document.querySelector('.chip[data-origin-code].active');
  if (origin) parts.push(origin.firstChild.textContent.trim());
  if (state.selectedGenres.size) parts.push(Array.from(state.selectedGenres).join(', '));
  const label = (sel) => { const b = document.querySelector(sel); return b ? b.textContent.trim() : ''; };
  if (state.era) parts.push(label(`[data-filter="era"][data-val="${state.era}"]`));
  if (state.duration && state.recMediaType === 'movie') parts.push(label(`[data-filter="duration"][data-val="${state.duration}"]`));
  if (state.minRating !== null) parts.push(state.minRating === 0 ? 'any rating' : `rated ${state.minRating}+`);
  if (state.includeWatched) parts.push('incl. seen');
  el.textContent = parts.join(' · ');
}

function resetAllFilters() {
  clearSelectedGenres();
  document.querySelectorAll('[data-filter="era"], [data-filter="duration"], [data-filter="minRating"]').forEach(c => {
    const isDefault = c.dataset.val === '' || c.dataset.val === 'default';
    setPressed(c, isDefault);
  });
  state.era = '';
  state.duration = '';
  state.minRating = null;
  const box = document.getElementById('includeWatchedBtn');
  if (box) box.checked = false;
  state.includeWatched = false;
  const active = document.querySelector('.chip[data-origin-code].active');
  if (active) setOrigin(active.dataset.originCode);
  updateFiltersSummary();
}

function renderSkeletons(n = 3) {
  return Array.from({ length: n }, () => `
    <div class="skeleton-card" aria-hidden="true">
      <div class="sk sk-poster"></div>
      <div class="sk-lines">
        <div class="sk sk-line" style="width: 70%; height: 16px;"></div>
        <div class="sk sk-line" style="width: 40%;"></div>
        <div class="sk sk-line" style="width: 90%; margin-top: 8px;"></div>
        <div class="sk sk-line" style="width: 80%;"></div>
      </div>
    </div>`).join('');
}

function scrollToResults() {
  const header = document.getElementById('resultsHeader');
  const target = header && !header.hidden && header.innerHTML ? header : document.getElementById('resultsContainer');
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  target.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
}

function setRecommendBusy(busy) {
  const btn = document.getElementById('recommendBtn');
  btn.disabled = busy;
  btn.setAttribute('aria-busy', busy ? 'true' : 'false');
  btn.innerHTML = busy
    ? `<span class="spinner" aria-hidden="true"></span><span id="recommendLabel">Finding picks…</span>`
    : `${icon('sparkle')}<span id="recommendLabel">${recommendLabel()}</span>`;
}

async function fetchRecommendations() {
  const container = document.getElementById('resultsContainer');
  const header = document.getElementById('resultsHeader');

  setRecommendBusy(true);
  header.innerHTML = '';
  container.innerHTML = renderSkeletons();
  document.getElementById('loadMoreBtn').style.display = 'none';
  scrollToResults();

  try {
    state.recPage = 1;
    state.recSessionId = null; // a new search is a new session
    const data = await requestRecommendations(1);
    state.recHasMore = !!data.has_more;
    renderRecommendations(data.items || [], data.engine, data.total_results);
    updateLoadMoreButton();
  } catch (err) {
    console.error('Error fetching recommendations:', err);
    header.innerHTML = '';
    container.innerHTML = `
      <div class="empty-state error" role="alert">
        ${icon('x', 'empty-icon')}
        <p class="empty-title">Couldn't get recommendations</p>
        <p class="empty-sub">The server didn't answer. Check that CouchCinema is still running, then try again.</p>
        <button class="btn-secondary" onclick="fetchRecommendations()">${icon('refresh')}Try again</button>
      </div>`;
    state.recHasMore = false;
    updateLoadMoreButton();
  } finally {
    setRecommendBusy(false);
  }
}

// The recommendation pool holds 50 items and pagination serves from it, so
// "more" exists beyond the first page. Without this the UI silently capped the
// user at one page — the request used to send top_n: 4, overriding page_size
// and returning only 4 of 50 results.
async function loadMoreRecommendations() {
  if (!state.recHasMore) return;
  state.recPage += 1;
  const btn = document.getElementById('loadMoreBtn');
  if (btn) { btn.disabled = true; btn.textContent = 'Loading…'; }
  try {
    const data = await requestRecommendations(state.recPage);
    state.recHasMore = !!data.has_more;
    appendRecommendations(data.items || [], data.engine);
  } catch (err) {
    console.error('Error loading more:', err);
    state.recPage -= 1;
  }
  updateLoadMoreButton();
}

// Single place that builds and sends the recommend request, shared by the
// initial search and "Show more" so the two can't drift apart.
async function requestRecommendations(page) {
  const query = document.getElementById('queryInput').value.trim();
  syncOriginChipFromQuery(query);
  const payload = {
    mood_id: state.activeMoodId,
    sub_mood: state.activeSubMood || null,
    media_type: state.recMediaType,
    genres: state.selectedGenres.size > 0 ? Array.from(state.selectedGenres) : null,
    era: state.era || null,
    duration: state.recMediaType === 'movie' ? (state.duration || null) : null,
    min_rating: state.minRating,
    // `top_n` must stay null: it aliases page_size server-side, so a stale
    // value silently caps the page.
    top_n: null,
    page: page,
    page_size: 20,
    include_watched: state.includeWatched,
    query: query || null,
    profile: state.recProfile,
    // "Show more" pages belong to the same logged session as page 1.
    session_id: page > 1 ? state.recSessionId : null
  };
  const res = await fetch('/api/recommend', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  if (!res.ok) throw new Error('HTTP ' + res.status);
  const data = await res.json();
  if (data.session_id) state.recSessionId = data.session_id;
  return data;
}

// Feedback log: what happened to a recommended title. Fire-and-forget; a
// failed log must never get in the way of the person using the app.
function sendFeedback(mediaItemId, outcome) {
  fetch('/api/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ media_item_id: mediaItemId, outcome, session_id: state.recSessionId })
  }).catch(() => {});
}

function openSynopsis(el, mediaItemId) {
  el.classList.toggle('expanded');
  if (!el.dataset.logged) {
    el.dataset.logged = '1';
    sendFeedback(mediaItemId, 'opened');
  }
}

// "Not this one": the negative signal the recommender has been missing.
function dismissRecommendation(mediaItemId, btn) {
  state.dismissedIds.add(mediaItemId);
  sendFeedback(mediaItemId, 'dismissed');
  const card = btn.closest('.movie-card');
  if (card) {
    card.classList.add('dismissed');
    setTimeout(() => card.remove(), 220);
  }
  showToast('Noted. It will learn from that.');
}

function updateLoadMoreButton() {
  const btn = document.getElementById('loadMoreBtn');
  if (!btn) return;
  btn.style.display = state.recHasMore ? 'inline-flex' : 'none';
  btn.disabled = false;
  btn.textContent = 'Show more';
}

function appendRecommendations(movies, engine) {
  const container = document.getElementById('resultsContainer');
  if (!movies || movies.length === 0) { updateLoadMoreButton(); return; }
  const offset = container.querySelectorAll('.movie-card').length;
  movies.forEach((m, i) => container.appendChild(buildMovieCard(m, engine, i, offset + i + 1)));
  updateLoadMoreButton();
}

function renderRecommendations(movies, engine, total) {
  const container = document.getElementById('resultsContainer');
  const header = document.getElementById('resultsHeader');
  container.innerHTML = '';
  const noun = state.recMediaType === 'tv' ? 'shows' : 'movies';

  if (!movies || movies.length === 0) {
    header.innerHTML = '';
    container.innerHTML = `
      <div class="empty-state">
        ${icon('film', 'empty-icon')}
        <p class="empty-title">No ${noun} match all of that</p>
        <p class="empty-sub">Try loosening a filter under “More filters”, or pick “Surprise Me”.</p>
      </div>`;
    return;
  }

  // Which engine ranked the list is said once here, not on every card.
  const engineNote = engine === 'generative'
    ? '<span class="engine-note generative" title="Ranked by the model trained on your ratings">Learning your taste</span>'
    : '<span class="engine-note" title="Ranked by rating and your favourite genres — rate a few more to unlock the learning model">Picked by rating &amp; genre</span>';
  header.innerHTML = `<h2>Tonight's picks</h2>${engineNote}`;

  movies.forEach((m, i) => container.appendChild(buildMovieCard(m, engine, i, i + 1)));
}

function watchButtonHtml(m) {
  const type = m.media_type || state.recMediaType;
  return `<button id="watch-btn-${m.id}" class="btn-watch"
            onclick="startDownload(${m.id}, ${jsArg(m.title)}, ${jsArg(type)})">
            ${icon('download')}<span>Get it for tonight</span>
          </button>`;
}

// Extracted so "Show more" and the initial render build identical cards.
function buildMovieCard(m, engine, index = 0, rank = 1) {
    const card = document.createElement('article');
    card.className = 'movie-card';
    card.style.animationDelay = `${Math.min(index, 6) * 40}ms`;

    // Reasons this film is here, in order of how much they mean to you two.
    const reasons = [];
    const tier = m.co_view_tier;
    if (tier === 'A') reasons.push(`<span class="badge badge-coview-a" title="You both rated this highly">${icon('heart', 'icon badge-icon')}You both liked it</span>`);
    if (tier === 'B') reasons.push(`<span class="badge badge-coview-b" title="${escapeHtml(m.tier_reason || "One of you loved it; the other hasn't seen it")}">${escapeHtml(m.tier_reason || "He'd recommend this")}</span>`);
    if (m.combined_score && tier === 'A') reasons.push(`<span class="badge badge-coview-score" title="Your combined score">Together ${icon('star', 'icon badge-icon filled')}${escapeHtml(m.combined_score)}</span>`);
    // Series order: a sequel swapped for the film to watch first, or a later
    // entry whose earlier ones are already seen.
    if (m.series_note) reasons.push(`<span class="badge badge-series" title="${escapeHtml(m.series_note)}">${escapeHtml(m.replaced_sequel_title ? `Start of ${m.series_name || 'the series'}` : `#${m.series_position} of ${m.series_size}`)}</span>`);
    // Why this film matched an idea-led request — without it the concept
    // layer is invisible.
    if (m.matched_themes && m.matched_themes.length) reasons.push(`<span class="badge badge-theme" title="Themes matched from the premise">${escapeHtml(m.matched_themes.join(' · '))}</span>`);
    if (m.taste_bonus > 0) reasons.push(`<span class="badge badge-bonus" title="+${escapeHtml(m.taste_bonus)} taste boost">Fits your taste</span>`);
    if (m.query_boost > 0) reasons.push(`<span class="badge badge-query" title="+${escapeHtml(m.query_boost)} match boost">Matches your description</span>`);

    const meta = [];
    if (m.release_year) meta.push(escapeHtml(m.release_year));
    if (m.runtime) meta.push(formatRuntime(m.runtime));
    if (m.rating) meta.push(`<span class="star" title="TMDB rating">${icon('star', 'icon meta-star filled')}${escapeHtml(Number(m.rating).toFixed(1))}</span>`);

    const hasLocalFile = m.file_path && m.file_path.length > 0;
    const actionButtonHtml = hasLocalFile
      ? `<button class="btn-watch play" onclick="playLocalItem(${m.id}, ${jsArg(m.title)})">${icon('play')}<span>Play now</span></button>`
      : watchButtonHtml(m);

    const overview = m.overview
      ? `<p class="movie-synopsis" onclick="openSynopsis(this, ${m.id})" title="Tap to expand">${escapeHtml(m.overview)}</p>`
      : `<p class="movie-synopsis empty">No synopsis available.</p>`;

    card.innerHTML = `
      <div class="movie-card-main">
        ${posterHtml(m.poster_url, m.title, 'movie-poster')}
        <div class="movie-details">
          <h3 class="movie-title">${escapeHtml(m.title)}</h3>
          <div class="movie-meta">${meta.join(' · ')}</div>
          <div class="movie-genres">${escapeHtml(m.genres || (m.media_type === 'tv' ? 'TV series' : 'Movie'))}</div>
          <div class="movie-badges">${reasons.join('')}</div>
          ${m.tier2_keywords ? `<div class="movie-vibes">${escapeHtml(m.tier2_keywords)}</div>` : ''}
          ${m.tier2_reviews_summary ? `<div class="movie-buzz">${escapeHtml(m.tier2_reviews_summary)}</div>` : ''}
        </div>
      </div>
      ${overview}
      <div class="movie-footer">
        <div class="ticket-no" aria-hidden="true"><small>No.</small><span>${String(rank).padStart(2, '0')}</span></div>
        <div id="watch-container-${m.id}" class="watch-slot">${actionButtonHtml}</div>
        <a class="btn-tv-link" href="${escapeHtml(state.jellyfinUrl)}" target="_blank" rel="noopener" title="Open Jellyfin">
          <span class="label">Jellyfin</span> ${icon('external')}
        </a>
        <button class="btn-dismiss" onclick="dismissRecommendation(${m.id}, this)"
                title="Not this one" aria-label="Not this one: ${escapeHtml(m.title)}">${icon('x')}</button>
      </div>
    `;
    return card;
}

function formatRuntime(min) {
  const m = Number(min);
  if (!m) return '';
  const h = Math.floor(m / 60);
  return h ? `${h}h ${String(m % 60).padStart(2, '0')}m` : `${m}m`;
}

// ---------------------------------------------------------------------------
// 5. Direct Download Subsystem (Movies & TV Shows)
// ---------------------------------------------------------------------------
function setDirectMediaType(type) {
  state.directMediaType = type;
  setPressed(document.getElementById('directTypeMovieBtn'), type === 'movie');
  setPressed(document.getElementById('directTypeTvBtn'), type === 'tv');

  const input = document.getElementById('directQueryInput');
  if (input) {
    input.placeholder = type === 'tv'
      ? 'Show or episode, e.g. The Bear S02'
      : 'Movie title, e.g. Dune 2021';
  }
}

function setDirectSort(sortBySize) {
  state.directSortBySize = sortBySize;
  setPressed(document.getElementById('sortSmallestBtn'), sortBySize);
  setPressed(document.getElementById('sortQualityBtn'), !sortBySize);

  if (state.lastDirectResults && state.lastDirectResults.length > 0) {
    const sorted = [...state.lastDirectResults];
    if (sortBySize) {
      sorted.sort((a, b) => a.size_bytes - b.size_bytes);
    } else {
      const qualityTier = { '2160p': 0, '4K': 0, '1080p': 1, '720p': 2, 'HD': 3 };
      sorted.sort((a, b) => {
        const qA = qualityTier[a.quality] ?? 4;
        const qB = qualityTier[b.quality] ?? 4;
        if (qA !== qB) return qA - qB;
        return b.seeds - a.seeds;
      });
    }
    renderDirectResults(sorted, document.getElementById('directQueryInput').value.trim());
  }
}

async function executeDirectSearch() {
  const queryInput = document.getElementById('directQueryInput');
  const query = queryInput.value.trim();
  const container = document.getElementById('directResultsContainer');
  const btn = document.getElementById('directSearchBtn');

  if (!query) {
    queryInput.focus();
    return;
  }

  btn.innerHTML = '<span class="spinner" aria-hidden="true"></span>Searching';
  btn.disabled = true;
  container.innerHTML = `
    <div class="empty-state" aria-live="polite">
      <span class="spinner" aria-hidden="true" style="display:inline-block; margin-bottom: 10px;"></span>
      <p class="empty-title">Searching for “${escapeHtml(query)}”…</p>
      <p class="empty-sub">Checking who's sharing it and in what quality.</p>
    </div>
  `;

  try {
    const res = await fetch('/api/torrent/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query: query,
        sort_by_size: state.directSortBySize,
        media_type: state.directMediaType
      })
    });

    if (!res.ok) throw new Error('Torrent search request failed');
    const data = await res.json();
    state.lastDirectResults = data.results || [];
    renderDirectResults(state.lastDirectResults, query);
  } catch (err) {
    console.error('Direct search error:', err);
    container.innerHTML = `
      <div class="empty-state error" role="alert">
        ${icon('x', 'empty-icon')}
        <p class="empty-title">Search didn't go through</p>
        <p class="empty-sub">The server couldn't reach the search sites. Check the internet connection on the CouchCinema box, then try again.</p>
        <button class="btn-secondary" onclick="executeDirectSearch()">${icon('refresh')}Try again</button>
      </div>
    `;
  } finally {
    btn.textContent = 'Search';
    btn.disabled = false;
  }
}

function renderDirectResults(results, query) {
  const container = document.getElementById('directResultsContainer');
  container.innerHTML = '';

  if (!results || results.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        ${icon('search', 'empty-icon')}
        <p class="empty-title">Nothing available for “${escapeHtml(query)}”</p>
        <p class="empty-sub">Only files someone is actively sharing are shown. Try a shorter title, or add the year.</p>
      </div>
    `;
    return;
  }

  const header = document.createElement('p');
  header.className = 'results-count-banner';
  header.innerHTML = `${results.length} result${results.length === 1 ? '' : 's'} for “${escapeHtml(query)}”`;
  container.appendChild(header);

  results.forEach((item, idx) => {
    const card = document.createElement('div');
    card.className = 'torrent-card';

    let badgeClass = 'badge-res-default';
    const qUpper = (item.quality || '').toUpperCase();
    if (qUpper.includes('2160') || qUpper.includes('4K')) {
      badgeClass = 'badge-res-4k';
    } else if (qUpper.includes('1080')) {
      badgeClass = 'badge-res-1080';
    } else if (qUpper.includes('720')) {
      badgeClass = 'badge-res-720';
    }

    const containerId = `direct-dl-slot-${idx}`;
    const mediaType = item.media_type || state.directMediaType;

    card.innerHTML = `
      <div class="torrent-card-main">
        <div class="torrent-header">
          <div class="torrent-name">${escapeHtml(item.name)}</div>
        </div>
        <div class="torrent-meta-row">
          <span class="badge ${badgeClass}">${escapeHtml(item.quality || 'HD')}</span>
          <span class="badge badge-size">${escapeHtml(item.size_display || formatSize(item.size_bytes))}</span>
          <span class="badge badge-seeds" title="People sharing the full file — more means faster">${escapeHtml(item.seeds)} sharing</span>
          ${item.peers ? `<span class="badge badge-peers" title="People still downloading it">${escapeHtml(item.peers)} downloading</span>` : ''}
          <span class="badge badge-media-type">${mediaType === 'tv' ? 'TV' : 'Movie'}</span>
        </div>
        <div class="torrent-action-row" id="${containerId}">
          ${directDownloadButton(encodeURIComponent(item.magnet), item.name || query, containerId, mediaType)}
        </div>
      </div>
    `;

    container.appendChild(card);
  });
}

// ---------------------------------------------------------------------------
// 6. Non-blocking Download & Cancellation
// ---------------------------------------------------------------------------
async function startDirectDownload(encodedMagnet, movieTitle, containerId, mediaType = 'movie') {
  const container = document.getElementById(containerId);
  if (!container) return;

  const magnet = decodeURIComponent(encodedMagnet);
  container.innerHTML = `
    <button class="btn-download-direct" disabled>
      <span class="spinner" aria-hidden="true"></span><span>Starting download…</span>
    </button>
  `;

  try {
    const res = await fetch('/api/torrent/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        magnet_link: magnet,
        movie_id: 0,
        movie_title: movieTitle,
        media_type: mediaType
      })
    });

    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || 'Download failed to start');

    const movieId = data.movie_id;

    state.activeDownloads[movieId] = {
      movieId,
      title: movieTitle,
      mediaType,
      progress: 0,
      status: 'connecting',
      containerId,
      encodedMagnet
    };

    renderActiveDownloadsMonitor();
    pollDownloadProgress(movieId, containerId, movieTitle, mediaType);

  } catch (err) {
    console.error('Direct download start error:', err);
    container.innerHTML = `
      <div class="progress-box error-box" role="alert">
        <span>Couldn't start: ${escapeHtml(err.message)}</span>
        <button class="btn-retry-mini" onclick="startDirectDownload(${jsArg(encodedMagnet)}, ${jsArg(movieTitle)}, ${jsArg(containerId)}, ${jsArg(mediaType)})">${icon('refresh')}Retry</button>
      </div>
    `;
  }
}

function directDownloadButton(encodedMagnet, title, containerId, mediaType, label = 'Download') {
  return `<button class="btn-download-direct"
            onclick="startDirectDownload(${jsArg(encodedMagnet)}, ${jsArg(title)}, ${jsArg(containerId)}, ${jsArg(mediaType)})">
            ${icon('download')}<span>${label}</span>
          </button>`;
}

// Cancel Download
async function cancelDownload(movieId, containerId) {
  try {
    if (state.pollingIntervals[movieId]) {
      clearInterval(state.pollingIntervals[movieId]);
      delete state.pollingIntervals[movieId];
    }

    await fetch('/api/torrent/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ movie_id: movieId })
    });

    const downloadInfo = state.activeDownloads[movieId];
    delete state.activeDownloads[movieId];
    renderActiveDownloadsMonitor();

    const container = document.getElementById(containerId);
    if (container && downloadInfo) {
      // Recommender cards re-run their own search; direct results reuse the magnet.
      container.innerHTML = downloadInfo.encodedMagnet
        ? directDownloadButton(downloadInfo.encodedMagnet, downloadInfo.title, containerId, downloadInfo.mediaType || 'movie', 'Download again')
        : watchButtonHtml({ id: movieId, title: downloadInfo.title, media_type: downloadInfo.mediaType });
    }
    showToast('Download cancelled');
  } catch (err) {
    console.error('Cancel download error:', err);
    showToast("Couldn't cancel — try again");
  }
}

// ---------------------------------------------------------------------------
// 7. In-Place Download Progress Polling (1-second HTTP Polling)
// ---------------------------------------------------------------------------
function pollDownloadProgress(movieId, containerId, movieTitle, mediaType = 'movie') {
  // Clear any existing polling interval for this movie ID
  if (state.pollingIntervals[movieId]) {
    clearInterval(state.pollingIntervals[movieId]);
  }

  const intervalId = setInterval(async () => {
    try {
      const res = await fetch(`/api/torrent/status/${movieId}`);
      if (!res.ok) return;

      const data = await res.json();
      const container = document.getElementById(containerId);

      // Handle cancel or failure
      if (data.status === 'cancelled' || data.error) {
        clearInterval(intervalId);
        delete state.pollingIntervals[movieId];
        delete state.activeDownloads[movieId];
        renderActiveDownloadsMonitor();

        if (container) {
          const cancelled = data.status === 'cancelled';
          container.innerHTML = `
            <div class="progress-box ${cancelled ? 'cancelled-box' : 'error-box'}" ${cancelled ? '' : 'role="alert"'}>
              <span>${cancelled ? 'Download cancelled' : `Download failed: ${escapeHtml(data.error)}`}</span>
              <button class="btn-retry-mini" onclick="pollDownloadProgress(${movieId}, ${jsArg(containerId)}, ${jsArg(movieTitle)}, ${jsArg(mediaType)})">${icon('refresh')}Check again</button>
            </div>
          `;
        }
        return;
      }

      const pct = Math.min(100, Math.max(0, data.progress || 0));
      const downloadedStr = formatSize(data.downloaded || 0);
      const totalStr = formatSize(data.total_size || 0);
      const speedStr = formatSpeed(data.download_speed || 0);
      const stateStr = data.status || 'downloading';

      // Update in-memory tracker
      state.activeDownloads[movieId] = {
        movieId,
        title: data.name || movieTitle,
        mediaType,
        progress: pct,
        status: stateStr,
        downloadedStr,
        totalStr,
        speedStr,
        containerId
      };
      renderActiveDownloadsMonitor();

      // Check for completion
      if (pct >= 100 || stateStr === 'completed' || stateStr === 'seeding' || stateStr === 'finished') {
        clearInterval(intervalId);
        delete state.pollingIntervals[movieId];

        if (container) {
          container.innerHTML = `
            <button class="btn-ready-watch" onclick="playLocalItem(${movieId}, ${jsArg(data.name || movieTitle)})">
              ${icon('play')}<span>Ready — play it</span>
            </button>
          `;
        }
        showToast(`${data.name || movieTitle} is ready to watch`);
        return;
      }

      // In-place progress bar with cancel
      if (container) {
        // Percent and time left come first so they survive truncation on a
        // phone; speed shows only when there's no honest ETA (with 0 peers
        // libtorrent reports 0 B/s and any ETA would be fiction).
        const etaStr = formatEta(data.eta_seconds);
        const hasEta = etaStr && (data.download_speed || 0) > 0 && pct < 100;
        let statusLabel = stateStr === 'connecting'
          ? 'Connecting…'
          : `${pct.toFixed(0)}% · ${hasEta ? etaStr : speedStr}`;
        if (data.resumed) statusLabel += ' · resumed';
        if (stateStr === 'organizing') {
          statusLabel = `Adding the ${mediaType === 'tv' ? 'episodes' : 'film'} to Jellyfin…`;
        }

        // Update the existing bar in place so the fill animates smoothly
        // instead of being rebuilt every second.
        const bar = container.querySelector('.download-progress-bar-wrapper');
        if (bar) {
          bar.querySelector('.download-progress-bar-fill').style.transform = `scaleX(${pct / 100})`;
          bar.querySelector('.download-progress-bar-text').textContent = statusLabel;
          bar.setAttribute('aria-valuenow', pct.toFixed(0));
        } else {
          container.innerHTML = `
            <div class="download-progress-bar-wrapper" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct.toFixed(0)}" aria-label="Download progress">
              <div class="download-progress-bar-fill" style="transform: scaleX(${pct / 100});"></div>
              <div class="download-progress-bar-text">${escapeHtml(statusLabel)}</div>
              <button class="btn-cancel-mini-bar" onclick="cancelDownload(${movieId}, ${jsArg(containerId)})" title="Cancel download" aria-label="Cancel download">${icon('x')}</button>
            </div>
          `;
        }
      }

    } catch (e) {
      console.warn('Polling check failed:', e);
    }
  }, 1000);

  state.pollingIntervals[movieId] = intervalId;
}

// Updates tracker and active monitor UI
function renderActiveDownloadsMonitor() {
  const card = document.getElementById('activeDownloadsContainer');
  const list = document.getElementById('activeDownloadsList');
  if (!card || !list) return;

  const entries = Object.values(state.activeDownloads);
  if (entries.length === 0) {
    card.style.display = 'none';
    return;
  }

  card.style.display = 'block';
  list.innerHTML = '';

  entries.forEach(item => {
    const row = document.createElement('div');
    row.className = 'active-download-row';
    const isReady = item.progress >= 100 || item.status === 'completed';

    const statusWord = { connecting: 'connecting', downloading: 'downloading', organizing: 'adding to Jellyfin' }[item.status] || item.status;
    row.innerHTML = `
      <div class="active-download-main">
        <div class="active-download-title">${escapeHtml(item.title)}</div>
        <div class="active-download-sub">
          ${isReady ? 'Ready to watch' : `${item.progress.toFixed(0)}% · ${escapeHtml(item.speedStr || '0 B/s')} · ${escapeHtml(statusWord)}`}
        </div>
        ${isReady ? '' : `<div class="mini-progress"><span style="transform: scaleX(${item.progress / 100})"></span></div>`}
      </div>
      ${isReady
        ? `<button class="btn-ready-mini" onclick="playLocalItem(${item.movieId}, ${jsArg(item.title)})">${icon('play')}Play</button>`
        : `<button class="btn-cancel-mini" onclick="cancelDownload(${item.movieId}, ${jsArg(item.containerId)})">Cancel</button>`
      }
    `;
    list.appendChild(row);
  });
}

function updateActiveDownloadTracker(data) {
  if (!data || !data.movie_id) return;
  const existing = state.activeDownloads[data.movie_id] || {};
  state.activeDownloads[data.movie_id] = {
    ...existing,
    movieId: data.movie_id,
    title: data.name || existing.title || 'Media',
    progress: Math.min(100, data.progress || 0),
    status: data.status || 'downloading',
    speedStr: formatSpeed(data.download_speed || 0),
    downloadedStr: formatSize(data.downloaded || 0),
    totalStr: formatSize(data.total_size || 0)
  };
  renderActiveDownloadsMonitor();
}

// ---------------------------------------------------------------------------
// 8. Download for Recommender Movie Cards
// ---------------------------------------------------------------------------
async function startDownload(movieId, movieTitle, mediaType = 'movie') {
  const container = document.getElementById(`watch-container-${movieId}`);
  if (!container) return;

  container.innerHTML = `
    <button class="btn-watch" disabled>
      <span class="spinner" aria-hidden="true"></span><span>Finding a copy…</span>
    </button>
  `;

  try {
    const searchRes = await fetch('/api/torrent/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: movieTitle, sort_by_size: true, media_type: mediaType })
    });

    if (!searchRes.ok) throw new Error('Search failed');
    const { results } = await searchRes.json();
    if (!results || results.length === 0) throw new Error('No copy available right now');

    const magnetLink = results[0].magnet;

    const downloadRes = await fetch('/api/torrent/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        magnet_link: magnetLink,
        movie_id: movieId,
        movie_title: movieTitle,
        media_type: mediaType
      })
    });

    const downloadData = await downloadRes.json();
    if (downloadData.error) throw new Error(downloadData.error);

    state.activeDownloads[movieId] = {
      movieId,
      title: movieTitle,
      mediaType,
      progress: 0,
      status: 'connecting',
      containerId: `watch-container-${movieId}`
    };
    renderActiveDownloadsMonitor();

    pollDownloadProgress(movieId, `watch-container-${movieId}`, movieTitle, mediaType);

  } catch (err) {
    console.error('Recommender download error:', err);
    // Stays put until the user acts — the old version vanished after 3s,
    // often before anyone had read it.
    container.innerHTML = `
      <div class="progress-box error-box" role="alert">
        <span>${escapeHtml(err.message)}</span>
        <button class="btn-retry-mini" onclick="startDownload(${movieId}, ${jsArg(movieTitle)}, ${jsArg(mediaType)})">${icon('refresh')}Retry</button>
      </div>
    `;
  }
}

// ---------------------------------------------------------------------------
// 9. Video Playback & Watch Session Lifecycle
// ---------------------------------------------------------------------------
async function playLocalItem(movieId, movieTitle) {
  try {
    const startRes = await fetch('/api/watch/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ media_item_id: movieId })
    });
    const sessionData = await startRes.json();
    if (sessionData.active) {
      showWatchingBanner(sessionData.active);
    }

    const streamRes = await fetch(`/api/stream/${movieId}`);
    if (streamRes.ok) {
      const data = await streamRes.json();
      if (data.jellyfin_url) {
        window.open(data.jellyfin_url, '_blank');
        return;
      }
    }

    window.open(state.jellyfinUrl, '_blank');
  } catch (err) {
    console.error('Play item error:', err);
    window.open(state.jellyfinUrl, '_blank');
  }
}

async function checkActiveWatch() {
  try {
    const res = await fetch('/api/watch/current');
    const data = await res.json();
    if (data.active) {
      showWatchingBanner(data.active);
    } else {
      document.getElementById('watchingBanner').style.display = 'none';
    }
  } catch (e) {
    console.warn('Check active watch error:', e);
  }
}

function showWatchingBanner(item) {
  state.activeSession = item;
  const banner = document.getElementById('watchingBanner');
  const poster = document.getElementById('watchingPoster');
  const title = document.getElementById('watchingTitle');

  poster.outerHTML = item.poster_url
    ? `<img id="watchingPoster" class="watching-poster" src="${escapeHtml(item.poster_url)}" alt="" data-title="" onerror="posterFailed(this)">`
    : `<div id="watchingPoster" class="watching-poster poster-fallback" aria-hidden="true"></div>`;
  title.innerText = item.release_year ? `${item.title} (${item.release_year})` : item.title;
  banner.style.display = 'flex';
}

async function cancelWatch() {
  if (!state.activeSession) return;
  try {
    await fetch('/api/watch/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: state.activeSession.session_id })
    });
    document.getElementById('watchingBanner').style.display = 'none';
    state.activeSession = null;
  } catch (e) {
    console.error('Cancel watch error:', e);
  }
}

// ---------------------------------------------------------------------------
// 10. Rating & Taste Profile Feedback Modal
// ---------------------------------------------------------------------------
function initStarSelector() {
  const container = document.getElementById('starSelector');
  container.innerHTML = '';
  for (let i = 1; i <= 10; i++) {
    const star = document.createElement('button');
    star.className = `star-btn ${i <= state.selectedRating ? 'active' : ''}`;
    star.innerHTML = icon('star', 'icon star-icon');
    star.setAttribute('role', 'radio');
    star.setAttribute('aria-label', `${i} out of 10`);
    star.setAttribute('aria-checked', i === state.selectedRating ? 'true' : 'false');
    star.onclick = () => selectStarRating(i);
    container.appendChild(star);
  }
  updateStarLabel(state.selectedRating);
}

function selectStarRating(val) {
  state.selectedRating = val;
  // Scoped to the sheet: the old selector also hit the Rate tab's stars.
  document.querySelectorAll('#starSelector .star-btn').forEach((s, idx) => {
    s.classList.toggle('active', idx < val);
    s.setAttribute('aria-checked', idx + 1 === val ? 'true' : 'false');
  });
  updateStarLabel(val);
}

const RATING_WORDS = ['', 'Awful', 'Bad', 'Poor', 'Weak', 'Okay', 'Decent', 'Good', 'Great', 'Brilliant', 'All-time favourite'];

function updateStarLabel(val) {
  const label = document.getElementById('starLabel');
  if (label) label.innerText = `${val}/10 · ${RATING_WORDS[val] || ''}`;
}

let lastFocusBeforeSheet = null;

function openFeedbackModal() {
  if (!state.activeSession) return;
  lastFocusBeforeSheet = document.activeElement;
  document.getElementById('modalMovieTitle').innerText = state.activeSession.title;
  document.getElementById('feedbackModal').classList.add('active');
  setTimeout(() => {
    const current = document.querySelector('#starSelector .star-btn[aria-checked="true"]');
    if (current) current.focus();
  }, 50);
}

function closeFeedbackModal() {
  document.getElementById('feedbackModal').classList.remove('active');
  if (lastFocusBeforeSheet) lastFocusBeforeSheet.focus();
}

async function submitFeedback() {
  if (!state.activeSession) return;
  const notes = document.getElementById('reviewNotesInput').value.trim();

  try {
    await fetch('/api/watch/finish', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: state.activeSession.session_id,
        user_rating: state.selectedRating,
        notes: notes || null
      })
    });

    closeFeedbackModal();
    document.getElementById('watchingBanner').style.display = 'none';
    state.activeSession = null;
    document.getElementById('reviewNotesInput').value = '';
    showToast('Saved — thanks, that sharpens your picks');

    switchTab('history');
  } catch (err) {
    console.error('Submit feedback error:', err);
    showToast("Couldn't save the rating — try again");
  }
}

// ---------------------------------------------------------------------------
// 11. History & Taste Profile View
// ---------------------------------------------------------------------------
async function loadHistory() {
  const list = document.getElementById('historyList');
  const genreContainer = document.getElementById('tasteGenresContainer');
  const kwContainer = document.getElementById('tasteKeywordsContainer');

  list.innerHTML = '<p class="rate-sub" style="text-align:center; padding: 20px;">Loading…</p>';

  try {
    const res = await fetch('/api/history');
    const data = await res.json();

    genreContainer.innerHTML = '';
    kwContainer.innerHTML = '';

    const prefGenres = data.taste_profile?.preferred_genres || {};
    const prefKws = data.taste_profile?.preferred_keywords || {};

    const topGenres = Object.entries(prefGenres).sort((a, b) => b[1] - a[1]).slice(0, 6);
    const topKws = Object.entries(prefKws).sort((a, b) => b[1] - a[1]).slice(0, 8);

    if (topGenres.length === 0 && topKws.length === 0) {
      genreContainer.innerHTML = `<span class="taste-empty">Nothing yet. Rate a few things 7 or higher on the
        <a href="#rate" onclick="switchTab('rate'); return false;">Rate</a> tab and your favourite genres show up here.</span>`;
    } else {
      genreContainer.innerHTML = topGenres.map(([genre, score]) =>
        `<span class="taste-tag">${escapeHtml(genre)}<span class="weight">+${score.toFixed(1)}</span></span>`).join('');
      kwContainer.innerHTML = topKws.map(([kw]) =>
        `<span class="taste-tag">${escapeHtml(kw)}</span>`).join('');
    }

    list.innerHTML = '';
    const items = data.history || [];
    if (items.length === 0) {
      list.innerHTML = `
        <div class="empty-state">
          ${icon('clock', 'empty-icon')}
          <p class="empty-title">Nothing watched yet</p>
          <p class="empty-sub">When you finish something, rate it from the “Now watching” bar and it lands here.</p>
        </div>`;
      return;
    }

    const dateFmt = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
    items.forEach(h => {
      const itemEl = document.createElement('div');
      itemEl.className = 'history-item';
      const ratingHtml = h.user_rating
        ? `<div class="history-rating">${escapeHtml(h.user_rating)}<small>/10</small></div>`
        : '<div class="history-rating unrated">Not rated</div>';
      const notesHtml = h.notes ? `<div class="history-notes">“${escapeHtml(h.notes)}”</div>` : '';
      const when = h.watched_at ? new Date(h.watched_at.replace(' ', 'T')) : null;
      const dateStr = when && !isNaN(when) ? dateFmt.format(when) : '';

      itemEl.innerHTML = `
        <div class="history-info">
          <div class="history-title">${escapeHtml(h.title)} ${h.release_year ? `<span class="movie-year">${escapeHtml(h.release_year)}</span>` : ''}</div>
          <div class="history-meta">${[dateStr, escapeHtml(h.genres || '')].filter(Boolean).join(' · ')}</div>
          ${notesHtml}
        </div>
        ${ratingHtml}
      `;
      list.appendChild(itemEl);
    });

  } catch (err) {
    console.error('Load history error:', err);
    list.innerHTML = `
      <div class="empty-state error" role="alert">
        ${icon('x', 'empty-icon')}
        <p class="empty-title">Couldn't load your history</p>
        <button class="btn-secondary" onclick="loadHistory()">${icon('refresh')}Try again</button>
      </div>`;
  }
}

// ---------------------------------------------------------------------------
// 12. Voice Dictation
// ---------------------------------------------------------------------------
function initSpeechRecognition() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    // No dictation here (e.g. Firefox): hide the mics rather than offer a
    // button that only says no.
    ['micBtn', 'modalMicBtn'].forEach(id => { const b = document.getElementById(id); if (b) b.hidden = true; });
    return;
  }

  state.recognition = new SpeechRecognition();
  state.recognition.continuous = false;
  state.recognition.interimResults = false;

  state.recognition.onresult = (e) => {
    const text = e.results[0][0].transcript;
    document.getElementById('queryInput').value = text;
    document.getElementById('micBtn').classList.remove('listening');
    fetchRecommendations();
  };

  state.recognition.onerror = () => {
    document.getElementById('micBtn').classList.remove('listening');
  };

  state.recognition.onend = () => {
    document.getElementById('micBtn').classList.remove('listening');
  };
}

function toggleVoiceSearch() {
  if (!state.recognition) {
    showToast("Voice input isn't available in this browser");
    return;
  }
  const btn = document.getElementById('micBtn');
  if (btn.classList.contains('listening')) { state.recognition.stop(); return; }
  btn.classList.add('listening');
  showToast('Listening…');
  state.recognition.start();
}

function toggleModalVoice() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    showToast("Voice input isn't available in this browser");
    return;
  }
  const recog = new SpeechRecognition();
  const btn = document.getElementById('modalMicBtn');
  btn.classList.add('listening');
  const done = () => btn.classList.remove('listening');
  recog.onresult = (e) => {
    const text = e.results[0][0].transcript;
    const input = document.getElementById('reviewNotesInput');
    input.value = input.value ? `${input.value} ${text}` : text;
  };
  recog.onerror = done;
  recog.onend = done;
  recog.start();
}

// ---------------------------------------------------------------------------
// 13. Helpers & Modal Close
// ---------------------------------------------------------------------------
function closePlayerModal() {
  const modal = document.getElementById('playerModal');
  const video = document.getElementById('videoPlayer');
  if (video) video.pause();
  modal.classList.remove('active');
}

function formatSpeed(bytes) {
  if (!bytes || bytes < 1024) return `${bytes || 0} B/s`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB/s`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB/s`;
}

function formatSize(bytes) {
  if (!bytes || bytes < 1024) return `${bytes || 0} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

// Human ETA from seconds remaining. Rounded so it doesn't churn every tick,
// and coarse beyond an hour ("3h left" is more honest than "2h 58m").
function formatEta(seconds) {
  if (seconds === null || seconds === undefined) return '';
  if (seconds <= 0) return 'finishing…';
  const s = Math.round(seconds);
  if (s < 60) return `${s}s left`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m left`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  if (h < 24) return rem ? `${h}h ${rem}m left` : `${h}h left`;
  const days = Math.round(h / 24);
  return `${days}d left`;
}

// ===========================================================================
// RATE TOGETHER — multi-profile co-view ratings
// ===========================================================================
// One card at a time. Each person rates it, or says "we haven't seen it", or
// skips. This is the only thing that gives the recommender real signal: taste
// profiles are only as good as the ratings behind them, and `not_seen` is what
// lets the recommender tell "he saw it, you haven't" apart from "neither of us
// has seen it".

// Catalogue titles and overviews are interpolated into innerHTML throughout
// this module, so anything user- or TMDB-supplied has to be escaped.
function escapeHtml(str) {
  return String(str == null ? '' : str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

const RATE_PAGE = 25;
// Used only if /api/ratings/genres is unreachable; the real list comes from
// the catalogue so chips never offer a genre with too few films to rate.
const DEFAULT_RATE_GENRES = ['Comedy', 'Drama', 'Action', 'Thriller', 'Adventure',
  'Crime', 'Family', 'Science Fiction', 'Fantasy', 'Horror', 'Romance',
  'Animation', 'Mystery', 'History', 'War', 'Music', 'Western'];

function initDiscoverTab() {
  loadOrigins();
}

function initRateTab() {
  initRateSearch();
  renderRateGenres();
  if (!state.rateGenreList.length) loadRateGenres();
  // Profiles may already be cached (loadPartnerName), but the queue still
  // needs its first fetch.
  if (state.rateProfiles.length === 0 || state.rateCursor === 0) {
    loadProfiles();
  } else {
    renderRateProfiles();
    renderRateCard();
  }
}

async function loadProfiles() {
  try {
    const res = await fetch('/api/profiles');
    state.rateProfiles = await res.json();
    renderRateProfiles();
    if (state.rateQueue.length === 0) {
      await loadRatingQueue(true);
    } else {
      renderRateCard();
    }
    loadCoViewSummary();
  } catch (err) {
    console.error('Failed to load profiles:', err);
    document.getElementById('rateProgress').innerText = 'Could not load profiles.';
  }
}

function renderRateProfiles() {
  const container = document.getElementById('rateProfileSwitch');
  if (!container) return;
  container.innerHTML = state.rateProfiles.map(p => `
    <button class="segment ${p.id === state.rateProfileId ? 'active' : ''}"
            aria-pressed="${p.id === state.rateProfileId}"
            onclick="setRateProfile(${p.id})"
            title="${escapeHtml(p.rating_count)} rated · ${escapeHtml(p.not_seen_count || 0)} not seen">
      ${escapeHtml(p.name)}
      <span class="segment-count">${escapeHtml(p.rating_count)}</span>
    </button>
  `).join('');
}

function setRateProfile(profileId) {
  state.rateProfileId = profileId;
  state.rateQueue = [];
  state.rateIndex = 0;
  renderRateProfiles();
  loadRatingQueue(true);
}

function setRateMediaType(type) {
  if (state.rateMediaType === type) return;
  state.rateMediaType = type;
  setPressed(document.getElementById('rateTypeMovie'), type === 'movie');
  setPressed(document.getElementById('rateTypeTv'), type === 'tv');
  state.rateQueue = [];
  state.rateIndex = 0;
  loadRatingQueue(true);
}

async function loadRatingQueue(reset) {
  if (reset) {
    state.rateQueue = [];
    state.rateIndex = 0;
    state.rateCursor = 0;
    state.rateSkipped = new Set();
  }
  const activeProfile = activeRateProfile();
  try {
    const limit = state.rateCursor === 0 ? RATE_PAGE : RATE_PAGE * 2;
    const params = new URLSearchParams({
      profile_id: state.rateProfileId,
      media_type: state.rateMediaType,
      limit: limit
    });
    if (state.rateGenres.length) params.set('genres', state.rateGenres.join(','));
    if (state.rateSeed != null) params.set('seed', state.rateSeed);
    if (state.rateBalanced) params.set('balanced', 'true');

    const res = await fetch(`/api/ratings/queue?${params}`);
    const data = await res.json();

    // Drop anything already rated or skipped this session so skipping really
    // advances rather than looping back to the same card.
    state.rateQueue = (data.items || []).filter(
      it => !state.rateSkipped.has(it.id) && !(it.co_view?.verdicts || [])
        .some(v => v.profile_id === state.rateProfileId)
    );
    state.rateCursor = (data.items || []).length;
    state.rateIndex = 0;
    renderRateCard();
    loadCoViewSummary();
  } catch (err) {
    console.error('Failed to load rating queue:', err);
    setRateProgress("Couldn't load titles to rate. Check the server and try Shuffle.", 'error');
  }
}

function setRateProgress(text, tone = '') {
  const el = document.getElementById('rateProgress');
  el.textContent = text;
  el.className = `rate-progress ${tone}`;
}

function activeRateProfile() {
  return state.rateProfiles.find(p => p.id === state.rateProfileId) || { name: 'you' };
}

function setRateGenre(genre) {
  if (state.rateGenres.includes(genre)) {
    state.rateGenres = state.rateGenres.filter(g => g !== genre);
  } else {
    state.rateGenres.push(genre);
  }
  // Re-render before reloading: without this the chip's `active` class was
  // never refreshed, so a genre stayed highlighted after being switched off
  // (and a newly picked one never lit up). The queue reload below is async,
  // so the stale highlight stayed on screen for the whole fetch.
  renderRateGenres();
  loadRatingQueue(true);
}

function shuffleRatingQueue() {
  // A fresh random seed draws a genuinely different set from the pool.
  state.rateSeed = Math.floor(Math.random() * 100000);
  loadRatingQueue(true);
}

function setRateBalanced() {
  state.rateBalanced = !state.rateBalanced;
  renderRateGenres();
  loadRatingQueue(true);
}

function renderRateGenres() {
  const container = document.getElementById('rateGenreChips');
  if (!container) return;
  const list = state.rateGenreList.length ? state.rateGenreList : DEFAULT_RATE_GENRES;
  const balancedChip = `
    <button class="chip ${state.rateBalanced ? 'active' : ''}" aria-pressed="${state.rateBalanced}"
            onclick="setRateBalanced()"
            title="One title per genre before repeating — builds your profile faster">
      Mix of genres
    </button>`;
  // Multiple chips are OR'd (any of the chosen genres), so a multi-select shows
  // a mix. The hint keeps that legible instead of looking like a broken filter.
  const hint = state.rateGenres.length > 1
    ? `<span class="rate-genre-hint">Showing any of ${state.rateGenres.length} genres</span>`
    : '';
  container.innerHTML = balancedChip + list.map(entry => {
    const name = typeof entry === 'string' ? entry : entry.name;
    const count = typeof entry === 'string' ? null : entry.count;
    const on = state.rateGenres.includes(name);
    return `<button class="chip ${on ? 'active' : ''}" aria-pressed="${on}"
            onclick="setRateGenre(${jsArg(name)})">${escapeHtml(name)}${
      count ? `<span class="chip-count">${escapeHtml(count)}</span>` : ''}</button>`;
  }).join('') + hint;
}

// Fetched from the catalogue so the chips reflect genres that actually have
// films to rate. Falls back to a sane static list offline.
async function loadRateGenres() {
  try {
    const res = await fetch('/api/ratings/genres');
    const data = await res.json();
    if (data && Array.isArray(data.genres) && data.genres.length) {
      state.rateGenreList = data.genres;
    }
  } catch (err) {
    console.warn('Falling back to static rating genres:', err);
  }
  renderRateGenres();
}

function resetRatingQueue() {
  state.rateSeed = Math.floor(Math.random() * 100000);
  loadRatingQueue(true);
}

function currentRateItem() {
  return state.rateQueue[state.rateIndex] || null;
}

function renderRateCard() {
  const card = document.getElementById('rateCard');
  const empty = document.getElementById('rateEmpty');
  const item = currentRateItem();
  const me = activeRateProfile();

  if (!item) {
    card.style.display = 'none';
    empty.style.display = 'block';
    setRateProgress('');
    return;
  }
  card.style.display = 'block';
  empty.style.display = 'none';

  const done = state.rateIndex;
  const total = state.rateQueue.length;
  if (item.fromSearch) {
    setRateProgress('From your search');
  } else {
    setRateProgress(`${done + 1} of ${total}` + (state.rateGenres.length ? ` · ${state.rateGenres.join(', ')}` : ''));
  }

  // Both people's verdicts, shown on the card's stub: yours is the one
  // being filled in now; theirs, if they've answered, sits beside it.
  const other = (item.co_view?.verdicts || []).find(v => v.profile_id !== state.rateProfileId);
  const otherProfile = state.rateProfiles.find(p => p.id !== state.rateProfileId);
  const otherName = other?.name || otherProfile?.name || 'Partner';
  let otherVerdict = '<b>not yet</b>';
  let otherPending = true;
  if (other?.status === 'not_seen') { otherVerdict = "<b>hasn't seen it</b>"; otherPending = false; }
  else if (other?.user_rating) { otherVerdict = `<b>${escapeHtml(other.user_rating)}/10</b>`; otherPending = false; }

  // A title opened from search may already carry this person's verdict:
  // show it, and let them change it.
  const mine = (item.co_view?.verdicts || []).find(v => v.profile_id === state.rateProfileId);
  let myVerdict = '<b>not yet</b>';
  if (mine?.status === 'not_seen') myVerdict = "<b>hasn't seen it</b>";
  else if (mine?.user_rating) myVerdict = `<b>${escapeHtml(mine.user_rating)}/10</b>`;
  const question = mine?.user_rating
    ? `You gave it ${escapeHtml(mine.user_rating)}/10. Change it?`
    : (me.name && me.name !== 'Me' ? `Seen it, ${escapeHtml(me.name)}? How was it?` : 'Seen it? How was it?');

  const pills = [];
  if (item.in_library) pills.push('<span class="badge badge-coview-score">In your library</span>');
  if (item.co_view?.tier === 'A') pills.push(`<span class="badge badge-coview-a">${icon('heart', 'icon badge-icon')}You both liked it</span>`);

  const meta = [];
  if (item.release_year) meta.push(escapeHtml(item.release_year));
  if (item.runtime) meta.push(formatRuntime(item.runtime));
  if (item.rating) meta.push(`<span class="star" title="TMDB rating">${icon('star', 'icon meta-star filled')}${escapeHtml(Number(item.rating).toFixed(1))}</span>`);

  card.classList.remove('enter');
  void card.offsetWidth; // restart the entrance for each new title
  card.classList.add('enter');

  card.innerHTML = `
    <div class="rate-card-body">
      ${posterHtml(item.poster_url, item.title, 'rate-poster')}
      <div class="rate-info">
        <h3 class="rate-title">${escapeHtml(item.title)}</h3>
        <p class="rate-meta">${meta.join(' · ')}</p>
        <p class="rate-genres">${escapeHtml(item.genres || '')}</p>
        <div class="rate-pills">${pills.join('')}</div>
        ${item.overview ? `<p class="rate-overview">${escapeHtml(item.overview.slice(0, 240))}${item.overview.length > 240 ? '…' : ''}</p>` : ''}
      </div>
    </div>

    <div class="rate-controls">
      <p class="rate-question" id="rateQuestion">${question}</p>
      <div class="rate-stars" role="group" aria-labelledby="rateQuestion">
        ${Array.from({ length: 10 }, (_, i) => `<button class="star-btn" onclick="submitRating(${i + 1})" aria-label="${i + 1} out of 10 — ${RATING_WORDS[i + 1]}" title="${i + 1} · ${RATING_WORDS[i + 1]}">${icon('star', 'icon star-icon')}</button>`).join('')}
      </div>
      <div class="rate-scale" aria-hidden="true"><span>1 · awful</span><span>10 · all-time favourite</span></div>

      <div class="rate-actions">
        <button class="btn-not-seen" onclick="submitNotSeen()">${icon('eye-off')}Haven't seen it</button>
        <button class="btn-skip" onclick="skipRateItem()">Skip ${icon('skip')}</button>
      </div>
      <p class="rate-hint"><kbd>1</kbd>–<kbd>0</kbd> rate · <kbd>N</kbd> haven't seen · <kbd>S</kbd> skip</p>
    </div>

    <div class="rate-stub">
      <span class="stub-label">Verdicts</span>
      <span class="stub-verdict ${mine ? '' : 'pending'}">${escapeHtml(me.name)}: ${myVerdict}</span>
      <span class="stub-sep">/</span>
      <span class="stub-verdict ${otherPending ? 'pending' : ''}">${escapeHtml(otherName)}: ${otherVerdict}</span>
    </div>
  `;
}

let ratingInFlight = false;

async function submitRating(value) {
  const item = currentRateItem();
  if (!item || ratingInFlight) return;
  if (await postRating(item.id, { user_rating: value, status: 'seen' })) {
    if (item.fromSearch) showToast(`Saved: ${item.title}, ${value}/10`);
    advanceQueue();
  }
}

async function submitNotSeen() {
  const item = currentRateItem();
  if (!item || ratingInFlight) return;
  // "Haven't seen it" is a real answer — stored with status='not_seen' and no
  // rating attached, never as a zero or a 1.
  if (await postRating(item.id, { status: 'not_seen' })) advanceQueue();
}

// Returns true only when the server stored it. The card used to advance
// even after a failed save, silently dropping the rating.
async function postRating(itemId, payload) {
  ratingInFlight = true;
  try {
    const res = await fetch('/api/ratings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ media_item_id: itemId, profile_id: state.rateProfileId, ...payload })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const summary = await res.json();
    flashRateFeedback(summary);
    return true;
  } catch (err) {
    console.error('Rating failed:', err);
    setRateProgress(`Not saved (${err.message}). Try again.`, 'error');
    return false;
  } finally {
    ratingInFlight = false;
  }
}

function flashRateFeedback(summary) {
  if (!summary || summary.tier === 'C') return;
  if (summary.tier === 'A') {
    showToast('Saved — you both like this one');
  } else if (summary.disagreement >= 3) {
    showToast(`Saved — you two differ by ${summary.disagreement} points`);
  }
}

function advanceQueue() {
  state.rateIndex += 1;
  // Refresh the profile counters (rating_count) without resetting the queue —
  // reloading the queue here used to snap the card back to the start.
  loadProfiles();
}

function skipRateItem() {
  const item = currentRateItem();
  if (item) {
    // Remember it so a reload doesn't hand back the same film.
    state.rateSkipped.add(item.id);
  }
  state.rateIndex += 1;
  // Near the end, pull a fresh batch so skipping always finds something new.
  if (state.rateIndex >= state.rateQueue.length - 1) {
    loadRatingQueue(false);
  } else {
    renderRateCard();
  }
}

async function loadCoViewSummary() {
  const container = document.getElementById('rateCoviewSummary');
  if (!container) return;
  try {
    const res = await fetch('/api/ratings/co-view');
    const data = await res.json();
    if (!data.count) {
      container.innerHTML = '';
      return;
    }
    container.innerHTML = `
      <h2 class="section-title">Both of you liked</h2>
      <p class="rate-sub">
        ${escapeHtml(data.count)} title${data.count === 1 ? '' : 's'} you both rated ${escapeHtml(data.min_liked)}+.
        “Us two” picks are built from these.
      </p>
      <div class="rate-coview-chips">
        ${data.items.slice(0, 12).map(i => `<span class="taste-tag">${escapeHtml(i.title)}</span>`).join('')}
      </div>
    `;
  } catch (err) {
    console.error('Co-view summary failed:', err);
  }
}

// Keyboard shortcuts for the rating card (1–0 rate, N = not seen, S = skip).
document.addEventListener('keydown', (e) => {
  if (state.activeTab !== 'rate') return;
  if (e.target && ['INPUT', 'TEXTAREA'].includes(e.target.tagName)) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (document.querySelector('.modal-overlay.active')) return;
  const k = e.key.toLowerCase();
  if (k.length !== 1) return;
  if (k >= '0' && k <= '9') {
    submitRating(k === '0' ? 10 : parseInt(k, 10));
    e.preventDefault();
  } else if (k === 'n') {
    submitNotSeen();
  } else if (k === 's') {
    skipRateItem();
  }
});

// ---------------------------------------------------------------------------
// Search to rate (design doc §1): find any title, rate it now.
// ---------------------------------------------------------------------------
// A combobox above the queue. Picking a result puts that title on the card
// in front of the queue, so the stars, "haven't seen it" and skip all work
// as usual. Titles missing from the catalogue can be found on TMDB and added.

const rateSearch = {
  bound: false,
  timer: null,
  seq: 0,           // ignores responses that arrive after a newer query
  query: '',
  mode: 'catalogue', // 'catalogue' | 'tmdb'
  rows: [],          // [{kind: 'item'|'tmdb'|'more', data}]
  active: -1,
};

function initRateSearch() {
  if (rateSearch.bound) return;
  const input = document.getElementById('rateSearchInput');
  if (!input) return;
  rateSearch.bound = true;

  input.addEventListener('input', () => {
    clearTimeout(rateSearch.timer);
    const q = input.value.trim();
    rateSearch.timer = setTimeout(() => runRateSearch(q), 250);
  });
  input.addEventListener('keydown', onRateSearchKey);
  input.addEventListener('focus', () => { if (rateSearch.rows.length) openRateSearchList(); });
  // Close when focus leaves the search box, but not before a click on a
  // result has landed (mousedown below keeps focus in place).
  input.addEventListener('blur', () => setTimeout(closeRateSearchList, 120));
  document.getElementById('rateSearchList').addEventListener('mousedown', e => e.preventDefault());
}

async function runRateSearch(q, mode = 'catalogue') {
  rateSearch.query = q;
  rateSearch.mode = mode;
  if (q.length < 2) {
    rateSearch.rows = [];
    closeRateSearchList();
    return;
  }
  const seq = ++rateSearch.seq;
  renderRateSearchMessage(mode === 'tmdb' ? 'Searching TMDB…' : 'Searching…');
  try {
    let rows;
    if (mode === 'tmdb') {
      const res = await fetch(`/api/catalogue/tmdb-search?${new URLSearchParams({ q })}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      rows = (data.items || []).map(d => ({ kind: 'tmdb', data: d }));
    } else {
      const params = new URLSearchParams({ q, media_type: 'all', limit: 8, profile_id: state.rateProfileId });
      const res = await fetch(`/api/catalogue/search?${params}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      rows = (data.items || []).map(d => ({ kind: 'item', data: d }));
      rows.push({ kind: 'more', data: null });
    }
    if (seq !== rateSearch.seq) return;
    rateSearch.rows = rows;
    rateSearch.active = rows.length && rows[0].kind !== 'more' ? 0 : -1;
    renderRateSearchList();
  } catch (err) {
    if (seq !== rateSearch.seq) return;
    console.error('Title search failed:', err);
    rateSearch.rows = [];
    renderRateSearchMessage(mode === 'tmdb'
      ? `Couldn't reach TMDB (${err.message}).`
      : "Search isn't working right now. Check the server.");
  }
}

function rateSearchMeta(d, kind) {
  const bits = [];
  if (d.release_year) bits.push(escapeHtml(d.release_year));
  bits.push(d.media_type === 'tv' ? 'TV' : 'Film');
  if (kind === 'tmdb' && d.catalogue_id) bits.push('already in your catalogue');
  return bits.join(' · ');
}

function rateSearchVerdict(d) {
  const v = d.my_verdict;
  if (!v) return '';
  if (v.status === 'not_seen') return '<span class="rate-search-verdict">Not seen</span>';
  if (v.user_rating) return `<span class="rate-search-verdict">You: ${escapeHtml(v.user_rating)}/10</span>`;
  return '';
}

function renderRateSearchList() {
  const list = document.getElementById('rateSearchList');
  const q = escapeHtml(rateSearch.query);
  const items = rateSearch.rows.filter(r => r.kind !== 'more');
  let html = '';
  if (!items.length) {
    html += `<li class="rate-search-empty" role="presentation">${rateSearch.mode === 'tmdb'
      ? `Nothing on TMDB matches “${q}”.` : `“${q}” isn't in your catalogue.`}</li>`;
  }
  html += rateSearch.rows.map((row, i) => {
    const activeCls = i === rateSearch.active ? ' active' : '';
    if (row.kind === 'more') {
      return `<li id="rateOpt-${i}" class="rate-search-option rate-search-more${activeCls}" role="option"
                  aria-selected="${i === rateSearch.active}" onclick="chooseRateSearchRow(${i})">
                ${icon('search')}<span>Not here? Search TMDB for “${q}”</span></li>`;
    }
    const d = row.data;
    return `<li id="rateOpt-${i}" class="rate-search-option${activeCls}" role="option"
                aria-selected="${i === rateSearch.active}" onclick="chooseRateSearchRow(${i})">
              ${posterHtml(d.poster_url, d.title, 'rate-search-poster')}
              <span class="rate-search-text">
                <span class="rate-search-title">${escapeHtml(d.title)}</span>
                <span class="rate-search-meta">${rateSearchMeta(d, row.kind)}</span>
              </span>
              ${row.kind === 'item' ? rateSearchVerdict(d) : `<span class="rate-search-verdict add">Add</span>`}
            </li>`;
  }).join('');
  list.innerHTML = html;
  openRateSearchList();
}

function renderRateSearchMessage(text) {
  const list = document.getElementById('rateSearchList');
  list.innerHTML = `<li class="rate-search-empty" role="presentation">${escapeHtml(text)}</li>`;
  openRateSearchList();
}

function openRateSearchList() {
  const list = document.getElementById('rateSearchList');
  const input = document.getElementById('rateSearchInput');
  list.hidden = false;
  input.setAttribute('aria-expanded', 'true');
  if (rateSearch.active >= 0) input.setAttribute('aria-activedescendant', `rateOpt-${rateSearch.active}`);
  else input.removeAttribute('aria-activedescendant');
}

function closeRateSearchList() {
  const list = document.getElementById('rateSearchList');
  const input = document.getElementById('rateSearchInput');
  if (!list || !input) return;
  list.hidden = true;
  input.setAttribute('aria-expanded', 'false');
  input.removeAttribute('aria-activedescendant');
}

function onRateSearchKey(e) {
  const n = rateSearch.rows.length;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    if (!n) return;
    e.preventDefault();
    const step = e.key === 'ArrowDown' ? 1 : -1;
    rateSearch.active = (rateSearch.active + step + n) % n;
    renderRateSearchList();
    document.getElementById(`rateOpt-${rateSearch.active}`)?.scrollIntoView({ block: 'nearest' });
  } else if (e.key === 'Enter') {
    e.preventDefault();
    clearTimeout(rateSearch.timer);
    const q = e.target.value.trim();
    // Enter before the debounce fired: search now rather than pick a stale row.
    if (q !== rateSearch.query) { runRateSearch(q); return; }
    if (rateSearch.active >= 0) chooseRateSearchRow(rateSearch.active);
  } else if (e.key === 'Escape') {
    if (!document.getElementById('rateSearchList').hidden) {
      e.preventDefault();
      e.stopPropagation();
      closeRateSearchList();
    }
  }
}

async function chooseRateSearchRow(i) {
  const row = rateSearch.rows[i];
  if (!row) return;
  if (row.kind === 'more') {
    runRateSearch(rateSearch.query, 'tmdb');
    return;
  }
  if (row.kind === 'item') {
    openSearchedTitle(row.data);
    return;
  }
  // TMDB result: add it to the catalogue (a no-op if it's already there).
  renderRateSearchMessage(`Adding ${row.data.title}…`);
  try {
    const res = await fetch(`/api/catalogue/tmdb-import?profile_id=${state.rateProfileId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ media_type: row.data.media_type, tmdb_id: row.data.tmdb_id })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    openSearchedTitle(data);
  } catch (err) {
    console.error('TMDB import failed:', err);
    renderRateSearchMessage(`Couldn't add it (${err.message}).`);
  }
}

// Puts the chosen title on the card, in front of the queue.
function openSearchedTitle(item) {
  item.fromSearch = true;
  state.rateQueue = state.rateQueue.filter((it, idx) => idx < state.rateIndex || it.id !== item.id);
  state.rateQueue.splice(state.rateIndex, 0, item);
  state.rateSkipped.delete(item.id);

  const input = document.getElementById('rateSearchInput');
  input.value = '';
  rateSearch.rows = [];
  rateSearch.query = '';
  closeRateSearchList();
  input.blur(); // so 1–0 / N / S go to the card straight away

  renderRateCard();
  document.getElementById('rateCard').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

