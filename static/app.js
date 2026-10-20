// Mobile-First Cinema Companion Client (static/app.js)

const state = {
  activeTab: 'discover',
  recMediaType: 'movie',    // 'movie' or 'tv'
  recProfile: 'together',  // 'me' | 'partner' | 'together' — whose taste to optimise for
  recPage: 1,            // recommendation page for "load more"
  originsLoaded: false,  // "From" chips fetched from /api/origins
  recHasMore: false,     // more results available in the pool
  dismissedIds: new Set(), // items tapped away ("not this one")
  askQuestions: true,       // "guess the vibe" pop-up before results (filters switch)
  vibeQuestion: null,       // the question on screen, if any
  vibeBusy: false,
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
  ratingVocab: null,
  ratingVocabPromise: null,
  selectedVerdict: null,
  recognition: null,
  modalRecognition: null,
  jellyfinUrl: '',
  directSort: 'best',      // 'best' | 'smallest' | 'quality'
  directLinked: null,      // {movieId, title, mediaType, query} when opened from a Discover card
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
  rateSkipped: new Set(),  // item ids skipped this session
  rateVocab: null,         // verdicts + chips from /api/ratings/vocab
  rateDraft: null,         // what's been ticked on the current card
  rateWhyOpen: false,      // the folded "Why?" section
  rateWhyData: null,       // vibe-check questions + stored details for the current card
  rateMoreOpen: false,
  rateAwaitingWhy: false,  // a verdict is saved and the card stays open until Next
  rateQuick: false,        // quick mode: move on right after the verdict (saved in this browser)
  rateCardShownId: null,   // item id the card was last drawn for
  rateSavedVerdict: null,
  rateSeasons: null,       // per-season verdicts for the TV show on the card
  pairMode: false,         // "This or that" round (separate from the rating cards)
  pairs: [], pairIndex: 0, pairDone: 0, pairSkipped: new Set(), pairLoaded: false,
  rateSortMode: false,     // "Sort out my 7s": confirm verdicts for old ratings
  // --- Watch later ---
  activeProfileId: readActiveProfileId(),  // who is holding the phone (header switch)
  watchLater: new Map(),                   // media_item_id -> [profile_ids] on the list
  profiles: []                             // [{id, name}] from /api/profiles
};

const TABS = ['discover', 'direct', 'rate', 'later', 'history'];
const TAB_IDS = { discover: 'tabDiscover', direct: 'tabDirect', rate: 'tabRate', later: 'tabLater', history: 'tabHistory' };
const VIEW_IDS = { discover: 'discoverView', direct: 'directView', rate: 'rateView', later: 'laterView', history: 'historyView' };

// Initialize app on DOM ready
document.addEventListener('DOMContentLoaded', () => {
  initPWA();
  checkStatus();
  loadMoods();
  checkActiveWatch();
  initFeedbackSelector();
  initSpeechRecognition();
  loadPartnerName();
  refreshLaterCount();
  updateFiltersSummary();

  // Open the tab named in the URL (#rate etc.) so a refresh or a home-screen
  // shortcut lands where you left off; otherwise Discover. Discover is
  // initialised here too — it used to load its "From" chips only after
  // switching away and back.
  const fromHash = location.hash.replace('#', '');
  loadAskQuestions();
  switchTab(TABS.includes(fromHash) ? fromHash : 'discover', { initial: true });
});

window.addEventListener('hashchange', () => {
  const tab = location.hash.replace('#', '');
  if (TABS.includes(tab) && tab !== state.activeTab) switchTab(tab);
});

// Escape closes whichever sheet is open.
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (document.getElementById('vibeModal').classList.contains('active')) finishVibeQuestions();
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
// `action` (optional): { label, run } adds one button to the toast. It stays
// up longer so it can be tapped.
function showToast(message, action) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.replaceChildren(document.createTextNode(message));
  el.classList.toggle('has-action', !!action);
  if (action) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'toast-action';
    b.textContent = action.label;
    b.onclick = () => {
      el.classList.remove('show', 'has-action');
      clearTimeout(toastTimer);
      action.run();
    };
    el.appendChild(b);
  }
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show', 'has-action'), action ? 6000 : 2600);
}

// Real names on the "whose taste" controls once profiles are loaded. Old
// installs may still carry the former defaults ("Me" / "Partner") until the
// v11 migration renames them, so those are treated like "no name set".
const DEFAULT_PROFILE_NAMES = ['Me', 'Partner'];
function isDefaultProfileName(name) {
  return !name || DEFAULT_PROFILE_NAMES.includes(name);
}
async function loadPartnerName() {
  try {
    const res = await fetch('/api/profiles');
    const profiles = await res.json();
    if (!Array.isArray(profiles) || profiles.length < 2) return;
    state.rateProfiles = state.rateProfiles.length ? state.rateProfiles : profiles;
    state.profiles = [...profiles].sort((a, b) => a.id - b.id).slice(0, 2);
    renderWhoSwitch();
    const [first, second] = [...profiles].sort((a, b) => a.id - b.id);
    for (const [key, person] of [['me', first], ['partner', second]]) {
      const btn = document.querySelector(`[data-rec-profile="${key}"]`);
      if (btn && person && !isDefaultProfileName(person.name)) {
        btn.textContent = `${person.name}'s picks`;
      }
    }
  } catch (_) { /* labels stay "Just me" / "Their picks" */ }
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
  if (tab === 'later') {
    loadLater();
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

function toggleAskQuestions() {
  const box = document.getElementById('askQuestionsBtn');
  state.askQuestions = box ? box.checked : !state.askQuestions;
  try { localStorage.setItem('askQuestions', state.askQuestions ? 'on' : 'off'); } catch (e) { /* private mode */ }
}

function loadAskQuestions() {
  try { state.askQuestions = localStorage.getItem('askQuestions') !== 'off'; } catch (e) { state.askQuestions = true; }
  const box = document.getElementById('askQuestionsBtn');
  if (box) box.checked = state.askQuestions;
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
    state.vibeMoods = null;
    const data = state.askQuestions ? await startVibeSession() : await requestRecommendations(1);
    showRecommendationPage(data);
    if (data.question) openVibeQuestion(data.question);
    else noteSkippedQuestions(data.question_skipped);
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

function showRecommendationPage(data) {
  if (Array.isArray(data.moods)) state.vibeMoods = data.moods;
  state.recHasMore = !!data.has_more;
  renderRecommendations(data.items || [], data.engine, data.total_results);
  updateLoadMoreButton();
}

// ---------------------------------------------------------------------------
// "Guess the vibe" questions pop-up (design doc §3)
// ---------------------------------------------------------------------------
// The server decides whether to ask (short or ambiguous requests) and which
// question splits the current results best. Every answer re-ranks the same
// pool, and the results behind the sheet update straight away.
async function startVibeSession() {
  const res = await fetch('/api/discover/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...buildRecommendPayload(1), ask: true })
  });
  if (!res.ok) throw new Error('HTTP ' + res.status);
  const data = await res.json();
  state.recSessionId = data.session_id || null;
  return data;
}

// The switch is on but the server had nothing to ask. Only one reason is worth
// telling the person about: the server's catalogue has no mood data yet, so
// questions can never appear there (they would on a machine that has it).
function noteSkippedQuestions(reason) {
  if (reason !== 'no_mood_data' || state.moodDataNoted) return;
  state.moodDataNoted = true;
  showToast("No vibe questions yet: this server doesn't have the films' mood data. It arrives with the Mac's nightly build.");
}

function openVibeQuestion(q) {
  state.vibeQuestion = q;
  const modal = document.getElementById('vibeModal');
  const wasOpen = modal.classList.contains('active');
  document.getElementById('vibeStep').textContent = `Question ${q.step} of up to ${q.max_steps}`;
  document.getElementById('vibeQuestionText').textContent = q.text;
  document.getElementById('vibeOptions').innerHTML = q.options.map(o => `
    <button class="vibe-option" onclick="answerVibeQuestion(${jsArg(o.id)})">
      <span class="vibe-emoji" aria-hidden="true">${escapeHtml(o.emoji)}</span>
      <span class="vibe-label">${escapeHtml(o.label)}</span>
      ${o.examples && o.examples.length ? `
        <span class="vibe-examples">
          ${o.examples.map(e => `<span class="vibe-example">${posterHtml(e.poster_url, e.title, 'vibe-thumb')}<span class="vibe-example-title">${escapeHtml(e.title)}</span></span>`).join('')}
        </span>` : ''}
    </button>`).join('');
  if (!wasOpen) {
    state.vibeReturnFocus = document.activeElement;
    modal.classList.add('active');
  }
  setVibeBusy(false);
  const first = modal.querySelector('.vibe-option');
  if (first) first.focus();
}

function setVibeBusy(busy) {
  state.vibeBusy = busy;
  const sheet = document.querySelector('#vibeModal .vibe-sheet');
  sheet.setAttribute('aria-busy', busy ? 'true' : 'false');
  sheet.querySelectorAll('button').forEach(b => { b.disabled = busy; });
}

function closeVibeModal() {
  state.vibeQuestion = null;
  document.getElementById('vibeModal').classList.remove('active');
  const back = state.vibeReturnFocus;
  if (back && document.body.contains(back)) back.focus();
}

async function answerVibeQuestion(optionId) {
  const q = state.vibeQuestion;
  if (!q || state.vibeBusy) return;
  setVibeBusy(true);
  try {
    const res = await fetch('/api/discover/answer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: state.recSessionId, question_id: q.id, option_id: optionId })
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'HTTP ' + res.status);
    const data = await res.json();
    state.recPage = 1;
    showRecommendationPage(data);
    if (data.question) {
      openVibeQuestion(data.question);
    } else {
      closeVibeModal();
      showToast('Here are your picks.');
    }
  } catch (err) {
    console.error('Question answer failed:', err);
    closeVibeModal();
    showToast(err.message && !err.message.startsWith('HTTP') ? err.message : "Couldn't save that answer. Showing what we have.");
  }
}

// "Just show me": stop asking, keep the answers so far.
async function finishVibeQuestions() {
  if (!state.vibeQuestion) { closeVibeModal(); return; }
  closeVibeModal();
  try {
    const res = await fetch('/api/discover/finish', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: state.recSessionId })
    });
    if (res.ok) {
      state.recPage = 1;
      showRecommendationPage(await res.json());
    }
  } catch (err) {
    console.error('Finish failed:', err);
  }
}

function buildRecommendPayload(page) {
  const query = document.getElementById('queryInput').value.trim();
  syncOriginChipFromQuery(query);
  return {
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
}

// Single place that builds and sends the recommend request, shared by the
// initial search and "Show more" so the two can't drift apart.
async function requestRecommendations(page) {
  const res = await fetch('/api/recommend', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(buildRecommendPayload(page))
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

// "Tonight: Cosy and comforting · A good cry": the two likeliest moods of the
// active questions session. Empty when there is no session or no moods.
function tonightLineHtml() {
  const moods = state.recSessionId && Array.isArray(state.vibeMoods) ? state.vibeMoods : [];
  if (!moods.length) return '';
  const names = moods.slice(0, 2).map(m => escapeHtml(m.label)).join(' · ');
  return `<p class="mood-tonight" id="moodTonight"><span class="mood-tonight-key">Tonight:</span> ${names}</p>`;
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
  header.innerHTML = `<h2>Tonight's picks</h2>${engineNote}${tonightLineHtml()}`;

  movies.forEach((m, i) => container.appendChild(buildMovieCard(m, engine, i, i + 1)));
}

function watchButtonHtml(m) {
  const type = m.media_type || state.recMediaType;
  return `<button id="watch-btn-${m.id}" class="btn-watch"
            onclick="startDownload(${m.id}, ${jsArg(m.title)}, ${jsArg(type)})">
            ${icon('download')}<span>Choose a copy</span>
          </button>`;
}

// ---------------------------------------------------------------------------
// Watch later: the "I'm …" switch and the bookmark button
// ---------------------------------------------------------------------------
// One shared list. state.watchLater maps media_item_id -> [profile_ids who put
// it on the list]; it is refilled from the `on_list` field of every item the
// app renders, so the buttons always reflect the server after a reload.
function readActiveProfileId() {
  try {
    const n = parseInt(localStorage.getItem('activeProfileId'), 10);
    if (n > 0) return n;
  } catch (_) { /* private mode: fall through */ }
  return 1;
}

function profileName(id) {
  const p = state.profiles.find(x => x.id === id);
  return p ? p.name : 'Someone';
}

function renderWhoSwitch() {
  const el = document.getElementById('whoSwitch');
  if (!el || state.profiles.length < 2) return;
  // A stored id that is not one of the two people falls back to the first.
  if (!state.profiles.some(p => p.id === state.activeProfileId)) state.activeProfileId = state.profiles[0].id;
  el.hidden = false;
  el.innerHTML = state.profiles.map(p => `
    <button type="button" class="segment${p.id === state.activeProfileId ? ' active' : ''}"
            aria-pressed="${p.id === state.activeProfileId}"
            onclick="setActiveProfile(${p.id})">
      <span class="who-lead">I'm </span>${escapeHtml(p.name)}
    </button>`).join('');
  refreshWatchLaterButtons();
  renderLaterBy();
}

function setActiveProfile(id) {
  state.activeProfileId = id;
  try { localStorage.setItem('activeProfileId', String(id)); } catch (_) { /* not remembered */ }
  renderWhoSwitch();
}

// Remember `on_list` from a batch of items (Discover, Rate, search).
function noteOnList(items) {
  for (const it of items || []) {
    if (it && it.id != null && Array.isArray(it.on_list)) state.watchLater.set(it.id, [...it.on_list]);
  }
}

// `opts.inRow`: the button sits inside a clickable row (Rate search), so its
// click must not also open the row.
function watchLaterButtonHtml(item, opts = {}) {
  noteOnList([item]);
  const stop = opts.inRow ? 'event.stopPropagation(); ' : '';
  return `<button type="button" class="btn-later${opts.inRow ? ' btn-later-row' : ''}" data-later-id="${item.id}"
            data-later-title="${escapeHtml(item.title)}"
            onclick="${stop}toggleWatchLater(${item.id}, ${jsArg(item.title)}, this)"></button>`;
}

// True when the active person has this title on the list.
function onMyWatchLater(id) {
  return (state.watchLater.get(id) || []).includes(state.activeProfileId);
}

// Paint one button from state.watchLater and the active person.
function paintWatchLaterButton(btn) {
  const id = Number(btn.dataset.laterId);
  const title = btn.dataset.laterTitle || '';
  const who = state.watchLater.get(id) || [];
  const mine = who.includes(state.activeProfileId);
  const other = who.find(pid => pid !== state.activeProfileId);
  const label = mine ? 'On your list' : 'Watch later';
  const otherNote = other != null ? `${profileName(other)} wants this` : '';
  btn.setAttribute('aria-pressed', mine ? 'true' : 'false');
  btn.classList.toggle('on', mine);
  btn.classList.toggle('other-wants', !mine && other != null);
  btn.setAttribute('aria-label', `${label}: ${title}${otherNote ? ` (${otherNote})` : ''}`);
  btn.title = mine
    ? (other != null ? 'You both want this. Tap to take it off your list' : 'On your list. Tap to remove')
    : (otherNote ? `${otherNote}. Tap to add it for you too` : 'Watch later');
  btn.innerHTML = `${icon('bookmark')}<span class="later-label">${mine && other != null ? 'Both' : 'Later'}</span>`
    + (otherNote ? `<span class="later-dot" aria-hidden="true"></span><span class="visually-hidden">${escapeHtml(otherNote)}</span>` : '');
}

function refreshWatchLaterButtons(id) {
  const sel = id == null ? '.btn-later' : `.btn-later[data-later-id="${id}"]`;
  document.querySelectorAll(sel).forEach(paintWatchLaterButton);
}

async function toggleWatchLater(id, title, btn) {
  const me = state.activeProfileId;
  const before = state.watchLater.get(id) || [];
  const adding = !before.includes(me);
  const after = adding ? [...before, me] : before.filter(p => p !== me);
  state.watchLater.set(id, after);          // optimistic
  refreshWatchLaterButtons(id);
  if (btn) btn.disabled = true;
  try {
    const res = adding
      ? await fetch('/api/watch-later', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ media_item_id: id, profile_id: me })
        })
      : await fetch(`/api/watch-later/${id}?profile_id=${me}`, { method: 'DELETE' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    showToast(adding ? 'Added to Watch later' : 'Removed from your list');
    refreshLaterCount();
    if (state.activeTab === 'later') loadLater({ quiet: true });
  } catch (err) {
    state.watchLater.set(id, before);       // roll back
    refreshWatchLaterButtons(id);
    showToast("Couldn't save. Try again.");
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Watch later: the Later tab
// ---------------------------------------------------------------------------
const later = { type: 'all', by: 'any', seq: 0 };   // by: 'any' | 'both' | profile id

function relativeAgo(ts) {
  // SQLite CURRENT_TIMESTAMP is UTC without a zone marker.
  const t = Date.parse(String(ts || '').replace(' ', 'T') + 'Z');
  if (!t) return '';
  const mins = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} day${days === 1 ? '' : 's'} ago`;
  const months = Math.round(days / 30);
  return months < 12 ? `${months} month${months === 1 ? '' : 's'} ago` : 'over a year ago';
}

// "Later 7" in the tab. Called on start and after anything that changes the list.
async function refreshLaterCount() {
  const el = document.getElementById('laterCount');
  if (!el) return;
  try {
    const res = await fetch('/api/watch-later');
    if (!res.ok) return;
    const n = (await res.json()).count || 0;
    el.textContent = n ? ` ${n}` : '';
    document.getElementById('tabLater').setAttribute('aria-label', n ? `Later, ${n} saved` : 'Later');
  } catch (_) { /* keep the old count */ }
}

function setLaterType(type) {
  later.type = type;
  document.querySelectorAll('[data-later-type]').forEach(b => setPressed(b, b.dataset.laterType === type));
  loadLater();
}

function setLaterBy(by) {
  later.by = by;
  renderLaterBy();
  loadLater();
}

function renderLaterBy() {
  const el = document.getElementById('laterByGroup');
  if (!el) return;
  const opts = [{ key: 'any', label: 'Anyone' }];
  state.profiles.forEach(p => opts.push({ key: String(p.id), label: p.name }));
  if (state.profiles.length >= 2) opts.push({ key: 'both', label: 'Both of you' });
  el.innerHTML = opts.map(o => {
    const on = String(later.by) === o.key;
    return `<button type="button" class="segment${on ? ' active' : ''}" aria-pressed="${on}"
              onclick="setLaterBy(${o.key === 'any' || o.key === 'both' ? jsArg(o.key) : o.key})">${escapeHtml(o.label)}</button>`;
  }).join('');
}

function laterAddedLine(e) {
  if (e.both) return `<span class="later-added together">${icon('heart', 'icon badge-icon')}You both want this</span>`;
  const a = (e.added_by || [])[0];
  if (!a) return '';
  const ago = relativeAgo(a.added_at);
  return `<span class="later-added">Added by ${escapeHtml(a.name || profileName(a.profile_id))}${ago ? ` · ${escapeHtml(ago)}` : ''}</span>`;
}

function buildLaterCard(e, rank, animate = true) {
  const card = document.createElement('article');
  card.className = 'movie-card later-card';
  if (animate) card.style.animationDelay = `${Math.min(rank - 1, 6) * 40}ms`;
  else card.style.animation = 'none';   // quiet refresh: don't replay the entrance
  const id = e.media_item_id;
  // Same record shape the other tabs give the shared helpers.
  const m = { id, title: e.title, media_type: e.media_type, on_list: (e.added_by || []).map(a => a.profile_id) };

  const meta = [];
  if (e.release_year) meta.push(escapeHtml(e.release_year));
  if (e.runtime) meta.push(formatRuntime(e.runtime));
  if (e.rating) meta.push(`<span class="star" title="TMDB rating">${icon('star', 'icon meta-star filled')}${escapeHtml(Number(e.rating).toFixed(1))}</span>`);

  const action = e.file_path
    ? `<button class="btn-watch play" onclick="playLocalItem(${id}, ${jsArg(e.title)})">${icon('play')}<span>Play now</span></button>`
    : watchButtonHtml(m);

  card.innerHTML = `
    <div class="movie-card-main">
      ${posterHtml(e.poster_url, e.title, 'movie-poster')}
      <div class="movie-details">
        <h3 class="movie-title">${escapeHtml(e.title)}</h3>
        <div class="movie-meta">${meta.join(' · ')}</div>
        <div class="movie-genres">${escapeHtml(e.genres || (e.media_type === 'tv' ? 'TV series' : 'Movie'))}</div>
        <div class="movie-badges">${laterAddedLine(e)}</div>
      </div>
    </div>
    <div class="movie-footer">
      <div class="ticket-no" aria-hidden="true"><small>No.</small><span class="foil">${String(rank).padStart(2, '0')}</span></div>
      <div class="watch-slot">${action}</div>
      ${watchLaterButtonHtml(m)}
      <button type="button" class="btn-tv-link btn-watched" aria-label="Watched it: ${escapeHtml(e.title)}"
              onclick="watchedFromLater(${id}, ${jsArg(e.title)})">Watched it</button>
    </div>`;
  card.querySelectorAll('.btn-later').forEach(paintWatchLaterButton);
  return card;
}

function laterQuery(extra) {
  const p = new URLSearchParams(extra || {});
  if (later.type !== 'all') p.set('media_type', later.type);
  if (later.by !== 'any' && later.by !== 'both') p.set('added_by', later.by);
  return p;
}

async function loadLater(opts = {}) {
  const list = document.getElementById('laterList');
  const seq = ++later.seq;
  if (!opts.quiet) {
    list.innerHTML = '<p class="rate-sub" style="text-align:center; padding: 20px;">Loading…</p>';
  }
  renderLaterBy();
  try {
    const [res, doneRes] = await Promise.all([
      fetch(`/api/watch-later?${laterQuery()}`),
      fetch('/api/watch-later?include_done=true')
    ]);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (seq !== later.seq) return;   // a newer request is on its way
    let items = data.items || [];
    if (later.by === 'both') items = items.filter(e => e.both);
    noteOnList(items.map(e => ({ id: e.media_item_id, on_list: (e.added_by || []).map(a => a.profile_id) })));
    const hadItems = items.length > 0;

    list.replaceChildren();
    if (!hadItems) {
      const filtered = later.type !== 'all' || later.by !== 'any';
      list.innerHTML = `
        <div class="empty-state">
          ${icon('bookmark', 'empty-icon')}
          <p class="empty-title">${filtered ? 'Nothing matches' : 'Nothing saved yet'}</p>
          <p class="empty-sub">${filtered ? 'Try a different filter.' : 'Tap the bookmark on any pick to save it for later.'}</p>
        </div>`;
    } else {
      items.forEach((e, i) => list.appendChild(buildLaterCard(e, i + 1, !opts.quiet)));
    }

    const doneData = doneRes.ok ? await doneRes.json() : { items: [] };
    renderLaterDone((doneData.items || []).filter(e => e.done_at)
      .sort((a, b) => String(b.done_at).localeCompare(String(a.done_at))).slice(0, 10));
    refreshLaterCount();
  } catch (err) {
    if (seq !== later.seq) return;
    console.error('Load Watch later error:', err);
    list.innerHTML = `
      <div class="empty-state error" role="alert">
        ${icon('x', 'empty-icon')}
        <p class="empty-title">Couldn't load your list</p>
        <button class="btn-secondary" onclick="loadLater()">${icon('refresh')}Try again</button>
      </div>`;
  }
}

function renderLaterDone(done) {
  const box = document.getElementById('laterDone');
  const el = document.getElementById('laterDoneList');
  box.hidden = done.length === 0;
  el.innerHTML = done.map(e => `
    <div class="later-done-row">
      <div class="later-done-info">
        <span class="later-done-title">${escapeHtml(e.title)}</span>
        <span class="later-done-meta">${escapeHtml(e.release_year || '')}${e.done_at ? ` · ${escapeHtml(e.done_reason === 'rated' ? 'rated' : 'watched')} ${escapeHtml(relativeAgo(e.done_at))}` : ''}</span>
      </div>
      <button type="button" class="btn-secondary" aria-label="Undo: put ${escapeHtml(e.title)} back on the list"
              onclick="undoLaterDone(${e.media_item_id})">Undo</button>
    </div>`).join('');
}

async function undoLaterDone(id) {
  try {
    const res = await fetch(`/api/watch-later/${id}/undo`, { method: 'POST' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    showToast('Back on your list');
    loadLater({ quiet: true });
  } catch (err) {
    showToast("Couldn't undo. Try again.");
  }
}

// "Watched it": mark it done, then open the Rate tab on that title.
async function watchedFromLater(id, title) {
  try {
    const res = await fetch(`/api/watch-later/${id}/done`, { method: 'POST' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (err) {
    showToast("Couldn't save. Try again.");
    return;
  }
  refreshLaterCount();
  // Rate under the person holding the phone ("I'm ..."), not whichever
  // profile the Rate tab last had selected.
  const want = state.activeProfileId;
  const queueLoaded = state.rateProfiles.length > 0 && state.rateCursor > 0;
  const switchProfile = want !== state.rateProfileId;
  if (switchProfile) state.rateProfileId = want;
  let item = null;
  try {
    const params = new URLSearchParams({ q: title, media_type: 'all', limit: 10, profile_id: state.rateProfileId });
    const res = await fetch(`/api/catalogue/search?${params}`);
    if (res.ok) item = ((await res.json()).items || []).find(it => it.id === id) || null;
  } catch (_) { /* fall back below */ }
  if (!item) item = { id, title, media_type: 'movie', on_list: [] };
  noteOnList([item]);
  switchTab('rate');
  // initRateTab loads its queue asynchronously the first time; wait for it so
  // the title is not wiped by that load.
  for (let i = 0; i < 40 && !(state.rateProfiles.length && state.rateCursor > 0); i++) {
    await new Promise(r => setTimeout(r, 100));
  }
  if (switchProfile && queueLoaded) {
    // The queue on screen belonged to the other profile; reload it for this one.
    state.rateQueue = [];
    state.rateIndex = 0;
    renderRateProfiles();
    await loadRatingQueue(true);
  }
  openSearchedTitle(item);
  showToast('Marked as watched. Rate it?', { label: 'Undo', run: () => undoLaterDone(id).then(refreshLaterCount) });
}

// Extracted so "Show more" and the initial render build identical cards.
function buildMovieCard(m, engine, index = 0, rank = 1) {
    const card = document.createElement('article');
    card.className = 'movie-card issued';
    card.style.setProperty('--issue-delay', `${Math.min(index, 6) * 40}ms`);

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
    // Learned taste: the real largest contributors to facet_fit / cluster_affinity.
    (m.taste_reasons || []).forEach((t) => reasons.push(`<span class="badge badge-taste" title="facet fit ${escapeHtml(m.facet_fit ?? 0)} · cluster ${escapeHtml(m.cluster_affinity ?? 0)}">${escapeHtml(t)}</span>`));
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
        <div class="ticket-no" aria-hidden="true"><small>No.</small><span class="foil">${String(rank).padStart(2, '0')}</span></div>
        <div id="watch-container-${m.id}" class="watch-slot">${actionButtonHtml}</div>
        ${watchLaterButtonHtml(m)}
        <a class="btn-tv-link" href="${escapeHtml(state.jellyfinUrl)}" target="_blank" rel="noopener" title="Open Jellyfin">
          <span class="label">Jellyfin</span> ${icon('external')}
        </a>
        <button class="btn-dismiss" onclick="dismissRecommendation(${m.id}, this)"
                title="Not this one" aria-label="Not this one: ${escapeHtml(m.title)}">${icon('x')}</button>
      </div>
    `;
    card.querySelectorAll('.btn-later').forEach(paintWatchLaterButton);
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

function sortDirectResults(results, mode) {
  const sorted = [...results];
  if (mode === 'smallest') {
    sorted.sort((a, b) => a.size_bytes - b.size_bytes);
  } else if (mode === 'quality') {
    const qualityTier = { '2160p': 0, '4K': 0, '1080p': 1, '720p': 2, 'HD': 3 };
    sorted.sort((a, b) => {
      const qA = qualityTier[a.quality] ?? 4;
      const qB = qualityTier[b.quality] ?? 4;
      if (qA !== qB) return qA - qB;
      return b.seeds - a.seeds;
    });
  } else {
    // Best match: the server's combined score (quality x seeders x size fit).
    sorted.sort((a, b) => (b.score ?? 0) - (a.score ?? 0) || b.seeds - a.seeds);
  }
  return sorted;
}

function setDirectSort(mode) {
  state.directSort = mode;
  setPressed(document.getElementById('sortBestBtn'), mode === 'best');
  setPressed(document.getElementById('sortSmallestBtn'), mode === 'smallest');
  setPressed(document.getElementById('sortQualityBtn'), mode === 'quality');

  if (state.lastDirectResults && state.lastDirectResults.length > 0) {
    renderDirectResults(
      sortDirectResults(state.lastDirectResults, mode),
      document.getElementById('directQueryInput').value.trim()
    );
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

  // The catalogue link only survives for the search it was opened with.
  const linked = state.directLinked;
  if (linked && (linked.query !== query || linked.mediaType !== state.directMediaType)) {
    state.directLinked = null;
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
        sort: state.directSort,
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

  const linked = state.directLinked;
  if (linked) {
    const note = document.createElement('p');
    note.className = 'direct-linked-note';
    note.innerHTML = `${icon('film')}<span>Pick a copy for <b>${escapeHtml(linked.title)}</b>. It will be filed under that title.</span>`;
    container.appendChild(note);
  }

  const header = document.createElement('p');
  header.className = 'results-count-banner';
  header.innerHTML = `${results.length} result${results.length === 1 ? '' : 's'} for “${escapeHtml(query)}”`;
  container.appendChild(header);

  // One card per file: indexers often list the same torrent more than once.
  const seenSlots = new Set();
  results.forEach((item, idx) => {
    const containerId = downloadSlotId(item.magnet);
    if (seenSlots.has(containerId)) return;
    seenSlots.add(containerId);

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

    const mediaType = item.media_type || state.directMediaType;
    const quality = item.quality || 'HD';
    const sizeText = item.size_display || formatSize(item.size_bytes);

    // Seeder health: how comfortably the swarm can finish a file this size.
    const health = item.seed_health ?? 1;
    const seedClass = health >= 0.9 ? 'ok' : health >= 0.6 ? 'mid' : 'low';
    const seedHint = seedClass === 'ok' ? 'Plenty sharing for a file this size'
      : seedClass === 'mid' ? 'Enough sharing, may be slower' : 'Few sharing for a file this size, may stall';

    const packNote = item.is_pack && item.per_episode_bytes
      ? `<span class="stat-sub">≈ ${escapeHtml(formatSize(item.per_episode_bytes))} per episode</span>` : '';
    const tags = [
      sortKeyIsBest() && idx === 0 ? '<span class="badge badge-best">Best match</span>' : '',
      item.is_pack ? '<span class="badge badge-pack">Season pack</span>' : '',
      mediaType === 'tv' && !item.is_pack ? '<span class="badge badge-media-type">Episode</span>' : '',
    ].join('');

    // Title shown/filed: the catalogue title when this search came from a Discover card.
    const fileTitle = linked ? linked.title : (item.name || query);
    const movieId = linked ? linked.movieId : 0;

    card.innerHTML = `
      <div class="torrent-card-main">
        <div class="torrent-header">
          <div class="torrent-name">${escapeHtml(item.name)}</div>
          ${tags ? `<div class="torrent-tags">${tags}</div>` : ''}
        </div>
        <dl class="torrent-stats">
          <div class="stat"><dt>Quality</dt><dd><span class="badge ${badgeClass}">${escapeHtml(quality)}</span></dd></div>
          <div class="stat"><dt>Size</dt><dd>${escapeHtml(sizeText)}</dd>${packNote}</div>
          <div class="stat seeds-${seedClass}" title="${seedHint}"><dt>Seeders</dt><dd>${escapeHtml(item.seeds)}</dd><span class="stat-sub">${seedClass === 'ok' ? 'healthy' : seedClass === 'mid' ? 'okay' : 'thin'}</span></div>
        </dl>
      </div>
      <div class="torrent-stub">
        <div class="torrent-action-row" id="${containerId}">
          ${directSlotHtml(encodeURIComponent(item.magnet), fileTitle, containerId, mediaType, movieId)}
        </div>
      </div>
    `;

    container.appendChild(card);
  });
}

// A result's download slot belongs to its file (the torrent's info hash), never
// to its place in the list: after a re-sort or a new search, a running
// download's progress, Cancel and Play must stay on the copy that was picked.
function downloadSlotId(magnet) {
  const text = String(magnet || '');
  const hash = /btih:([a-z0-9]+)/i.exec(text);
  if (hash) return `direct-dl-${hash[1].toLowerCase()}`;
  let h = 5381;
  for (let i = 0; i < text.length; i++) h = ((h * 33) ^ text.charCodeAt(i)) >>> 0;
  return `direct-dl-x${h.toString(36)}`;
}

// What a result's slot shows when the list is (re)drawn: the download already
// running or finished for that same file, otherwise a Download button.
function directSlotHtml(encodedMagnet, title, containerId, mediaType, linkedMovieId) {
  const dl = Object.values(state.activeDownloads).find(d => d.containerId === containerId);
  if (!dl) return directDownloadButton(encodedMagnet, title, containerId, mediaType, 'Download', linkedMovieId);
  if (dl.progress >= 100 || dl.status === 'completed') {
    return `<button class="btn-ready-watch" onclick="playLocalItem(${dl.movieId}, ${jsArg(dl.title)})">
              ${icon('play')}<span>Ready — play it</span>
            </button>`;
  }
  return `<button class="btn-download-direct" disabled>
            <span class="spinner" aria-hidden="true"></span><span>Downloading…</span>
          </button>`;
}

function sortKeyIsBest() {
  return state.directSort === 'best';
}

// ---------------------------------------------------------------------------
// 6. Non-blocking Download & Cancellation
// ---------------------------------------------------------------------------
async function startDirectDownload(encodedMagnet, movieTitle, containerId, mediaType = 'movie', linkedMovieId = 0) {
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
        movie_id: linkedMovieId || 0,
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
      encodedMagnet,
      linkedMovieId
    };

    renderActiveDownloadsMonitor();
    pollDownloadProgress(movieId, containerId, movieTitle, mediaType);

  } catch (err) {
    console.error('Direct download start error:', err);
    container.innerHTML = `
      <div class="progress-box error-box" role="alert">
        <span>Couldn't start: ${escapeHtml(err.message)}</span>
        <button class="btn-retry-mini" onclick="startDirectDownload(${jsArg(encodedMagnet)}, ${jsArg(movieTitle)}, ${jsArg(containerId)}, ${jsArg(mediaType)}, ${Number(linkedMovieId) || 0})">${icon('refresh')}Retry</button>
      </div>
    `;
  }
}

function directDownloadButton(encodedMagnet, title, containerId, mediaType, label = 'Download', linkedMovieId = 0) {
  return `<button class="btn-download-direct"
            onclick="startDirectDownload(${jsArg(encodedMagnet)}, ${jsArg(title)}, ${jsArg(containerId)}, ${jsArg(mediaType)}, ${Number(linkedMovieId) || 0})">
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
        ? directDownloadButton(downloadInfo.encodedMagnet, downloadInfo.title, containerId, downloadInfo.mediaType || 'movie', 'Download again', downloadInfo.linkedMovieId || 0)
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
        ...state.activeDownloads[movieId],
        movieId,
        title: data.name || movieTitle,
        mediaType,
        progress: pct,
        status: stateStr,
        downloadedStr,
        totalStr,
        speedStr,
        etaStr: (data.download_speed || 0) > 0 ? formatEta(data.eta_seconds) : '',
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
        // Percent, time left, then speed: the order they survive truncation
        // on a phone. Time left is hidden while speed is 0 (with 0 peers
        // libtorrent reports 0 B/s and any ETA would be fiction).
        const etaStr = formatEta(data.eta_seconds);
        const hasEta = etaStr && (data.download_speed || 0) > 0 && pct < 100;
        let statusLabel = stateStr === 'connecting'
          ? 'Connecting…'
          : [`${pct.toFixed(0)}%`, hasEta ? etaStr : '', speedStr].filter(Boolean).join(' · ');
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
          ${isReady ? 'Ready to watch' : [`${item.progress.toFixed(0)}%`, item.etaStr, item.speedStr || '0 B/s', statusWord].filter(Boolean).map(escapeHtml).join(' · ')}
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
    etaStr: (data.download_speed || 0) > 0 ? formatEta(data.eta_seconds) : '',
    downloadedStr: formatSize(data.downloaded || 0),
    totalStr: formatSize(data.total_size || 0)
  };
  renderActiveDownloadsMonitor();
}

// ---------------------------------------------------------------------------
// 8. Download for Recommender Movie Cards
// ---------------------------------------------------------------------------
// Downloads only start from the Download tab: this opens it with the title
// already searched, so the copy is picked there. The pick stays linked to the
// catalogue film (movie_id) so it is logged as "downloaded" and filed right.
function startDownload(movieId, movieTitle, mediaType = 'movie') {
  const type = mediaType === 'tv' ? 'tv' : 'movie';
  setDirectMediaType(type);
  state.directLinked = { movieId, title: movieTitle, mediaType: type, query: String(movieTitle).trim() };
  const input = document.getElementById('directQueryInput');
  if (input) input.value = movieTitle;
  switchTab('direct');
  executeDirectSearch();
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
async function fetchRatingVocab() {
  if (state.ratingVocab) return state.ratingVocab;
  if (!state.ratingVocabPromise) {
    state.ratingVocabPromise = (async () => {
      const res = await fetch('/api/ratings/vocab');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const vocab = await res.json();
      if (!Array.isArray(vocab.verdicts) || !vocab.verdicts.length) {
        throw new Error('Rating options were empty');
      }
      state.ratingVocab = vocab;
      return vocab;
    })().finally(() => { state.ratingVocabPromise = null; });
  }
  return state.ratingVocabPromise;
}

function initFeedbackSelector() {
  renderFeedbackVerdicts();
  return loadFeedbackVerdicts();
}

function loadFeedbackVerdicts() {
  return fetchRatingVocab().then(() => renderFeedbackVerdicts()).catch(err => {
    console.error('Could not load rating options:', err);
    renderFeedbackVerdicts('Could not load rating options. Close and reopen to try again.');
  });
}

function renderFeedbackVerdicts(message = '') {
  const container = document.getElementById('feedbackVerdictSelector');
  const label = document.getElementById('feedbackVerdictLabel');
  const submit = document.getElementById('feedbackSubmitButton');
  if (!container) return;
  const verdicts = state.ratingVocab?.verdicts || [];
  if (message || !verdicts.length) {
    container.innerHTML = escapeHtml(message || 'Loading rating options…');
    if (submit) submit.disabled = true;
    if (label) label.innerText = 'Choose one category';
    return;
  }
  container.innerHTML = verdicts.map((v, i) => `
    <button class="verdict-btn verdict-${escapeHtml(v.id)} ${state.selectedVerdict === v.id ? 'active' : ''}"
            onclick="selectFeedbackVerdict('${escapeHtml(v.id)}')" aria-pressed="${state.selectedVerdict === v.id}">
      <kbd>${i + 1}</kbd>${escapeHtml(v.label)}
    </button>`).join('');
  if (label) label.innerText = state.selectedVerdict
    ? verdicts.find(v => v.id === state.selectedVerdict)?.label || 'Choose one category'
    : 'Choose one category';
  if (submit) submit.disabled = !state.selectedVerdict;
}

function selectFeedbackVerdict(id) {
  if (!(state.ratingVocab?.verdicts || []).some(v => v.id === id)) return;
  state.selectedVerdict = id;
  document.querySelectorAll('#feedbackVerdictSelector .verdict-btn').forEach(button => {
    const selected = button.classList.contains(`verdict-${id}`);
    button.classList.toggle('active', selected);
    button.setAttribute('aria-pressed', selected ? 'true' : 'false');
  });
  const label = document.getElementById('feedbackVerdictLabel');
  if (label) label.innerText = state.ratingVocab.verdicts.find(v => v.id === id).label;
  const submit = document.getElementById('feedbackSubmitButton');
  if (submit) submit.disabled = false;
}

let lastFocusBeforeSheet = null;

function openFeedbackModal() {
  if (!state.activeSession) return;
  state.selectedVerdict = null;
  renderFeedbackVerdicts();
  if (!state.ratingVocab) loadFeedbackVerdicts();
  lastFocusBeforeSheet = document.activeElement;
  document.getElementById('modalMovieTitle').innerText = state.activeSession.title;
  document.getElementById('feedbackModal').classList.add('active');
  setTimeout(() => {
    const first = document.querySelector('#feedbackVerdictSelector .verdict-btn');
    if (first) first.focus();
  }, 50);
}

function closeFeedbackModal() {
  document.getElementById('feedbackModal').classList.remove('active');
  if (lastFocusBeforeSheet) lastFocusBeforeSheet.focus();
}

async function submitFeedback() {
  if (!state.activeSession) return;
  if (!state.selectedVerdict || !(state.ratingVocab?.verdicts || []).some(v => v.id === state.selectedVerdict)) {
    showToast('Choose a rating first');
    return;
  }
  const notes = document.getElementById('reviewNotesInput').value.trim();

  try {
    await fetch('/api/watch/finish', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: state.activeSession.session_id,
        // The legacy "I'm …" switch is this app's viewer selection. Send it
        // explicitly so watch completion does not use a stale NL cookie or
        // silently fall back to profile 1.
        profile_ids: [state.activeProfileId],
        verdict: state.selectedVerdict,
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
  try { state.rateQuick = localStorage.getItem('rateQuick') === 'on'; } catch (e) { /* private mode */ }
  initRateSearch();
  loadRateVocab();
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
  if (state.pairMode) { loadPairRound(); return; }
  if (reset) {
    state.rateQueue = [];
    state.rateIndex = 0;
    state.rateCursor = 0;
    state.rateSkipped = new Set();
  }
  const activeProfile = activeRateProfile();
  try {
    if (state.rateSortMode) {
      const sortParams = new URLSearchParams({
        profile_id: state.rateProfileId, media_type: state.rateMediaType, limit: 100
      });
      const sortRes = await fetch(`/api/ratings/inferred-queue?${sortParams}`);
      const sortData = await sortRes.json();
      noteOnList(sortData.items);
      state.rateQueue = (sortData.items || []).filter(it => !state.rateSkipped.has(it.id));
      state.rateCursor = (sortData.items || []).length;
      state.rateIndex = 0;
      renderRateCard();
      return;
    }
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
    noteOnList(data.items);

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
  if (state.pairMode) { renderPairs(); return; }
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
  } else if (state.rateSortMode) {
    setRateProgress(`Sorting your old ratings · ${done + 1} of ${total}`);
  } else {
    setRateProgress(`${done + 1} of ${total}` + (state.rateGenres.length ? ` · ${state.rateGenres.join(', ')}` : ''));
  }

  // Both people's verdicts, shown on the card's stub: yours is the one
  // being filled in now; theirs, if they've answered, sits beside it.
  const other = (item.co_view?.verdicts || []).find(v => v.profile_id !== state.rateProfileId);
  const otherProfile = state.rateProfiles.find(p => p.id !== state.rateProfileId);
  const otherName = other?.name || otherProfile?.name || 'Your partner';
  let otherVerdict = '<b>not yet</b>';
  let otherPending = true;
  if (other?.status === 'not_seen') { otherVerdict = "<b>hasn't seen it</b>"; otherPending = false; }
  else if (other?.user_rating) { otherVerdict = `<b>${escapeHtml(other.user_rating)}/10</b>`; otherPending = false; }

  // A title opened from search may already carry this person's verdict:
  // show it, and let them change it.
  const mine = (item.co_view?.verdicts || []).find(v => v.profile_id === state.rateProfileId);
  const verdictOf = (v) => {
    if (!v) return '';
    const label = rateVerdictLabel(v.verdict);
    const how = v.inferred ? (v.inferred_source === 'seasons' ? ' (from seasons)' : ' (guessed)') : '';
    return label ? `${label}${how}` : '';
  };
  const asked = me.name && !isDefaultProfileName(me.name) ? `Seen it, ${escapeHtml(me.name)}? What would you do now?` : 'Seen it? What would you do now?';
  let myVerdict = '<b>not yet</b>';
  let question = asked;
  if (mine?.status === 'not_seen') myVerdict = "<b>hasn't seen it</b>";
  else if (mine?.verdict) {
    myVerdict = `<b>${escapeHtml(verdictOf(mine))}</b>`;
    question = mine.inferred && mine.inferred_source === 'seasons'
      ? `Your seasons add up to ${escapeHtml(rateVerdictLabel(mine.verdict))}. Your verdict on the whole show?`
      : mine.inferred
      ? `You gave this ${escapeHtml(mine.user_rating)}/10 before. What's your verdict?`
      : `You said ${escapeHtml(rateVerdictLabel(mine.verdict))}. Change it?`;
  } else if (mine?.user_rating) {
    myVerdict = `<b>${escapeHtml(mine.user_rating)}/10</b>`;
    question = `You gave it ${escapeHtml(mine.user_rating)}/10. What's your verdict?`;
  }

  // The "other" person's verdict may now be a verdict word instead of a number.
  if (other?.verdict) otherVerdict = `<b>${escapeHtml(verdictOf(other))}</b>`;

  if (!state.rateDraft || state.rateDraft.itemId !== item.id) resetRateDraft(item, mine);

  const pills = [];
  if (item.in_library) pills.push('<span class="badge badge-coview-score">In your library</span>');
  if (item.co_view?.tier === 'A') pills.push(`<span class="badge badge-coview-a">${icon('heart', 'icon badge-icon')}You both liked it</span>`);

  const meta = [];
  if (item.release_year) meta.push(escapeHtml(item.release_year));
  if (item.runtime) meta.push(formatRuntime(item.runtime));
  if (item.rating) meta.push(`<span class="star" title="TMDB rating">${icon('star', 'icon meta-star filled')}${escapeHtml(Number(item.rating).toFixed(1))}</span>`);

  if (state.rateCardShownId !== item.id) {
    card.classList.remove('enter');
    void card.offsetWidth; // restart the entrance for each new title, not for in-place redraws
    card.classList.add('enter');
    state.rateCardShownId = item.id;
  }

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
      ${state.rateVocab ? `
      <div class="verdict-row" role="group" aria-labelledby="rateQuestion">
        ${state.rateVocab.verdicts.map((v, i) => `
          <button class="verdict-btn verdict-${escapeHtml(v.id)} ${state.rateSavedVerdict === v.id ? 'active' : ''}"
                  onclick="submitVerdict('${escapeHtml(v.id)}')" aria-pressed="${state.rateSavedVerdict === v.id}">
            <kbd>${i + 1}</kbd>${escapeHtml(v.label)}
          </button>`).join('')}
      </div>
      <label class="rate-dnf"><input type="checkbox" id="rateDnf" ${state.rateDraft.dnf ? 'checked' : ''}
             onchange="onRateDnfChange(this.checked)"> Didn't finish it</label>
      ${item.media_type === 'tv' ? '<div id="rateSeasonsSlot"></div>' : ''}

      <details class="rate-why" id="rateWhy" ${state.rateWhyOpen ? 'open' : ''} ontoggle="onRateWhyToggle(this)">
        <summary>Why? <span class="rate-why-sub">optional${state.rateAwaitingWhy ? ' · tap to add details' : ''}</span></summary>
        <div class="rate-why-body" id="rateWhyBody"></div>
      </details>

      ${state.rateAwaitingWhy ? `
        <p class="rate-saved" role="status">${state.rateSavedVerdict
          ? `✓ Saved as ${escapeHtml(rateVerdictLabel(state.rateSavedVerdict))}. Add details above if you like, or move on.`
          : '✓ Season verdicts saved. A verdict on the whole show is optional.'}</p>
        <button class="btn-done" onclick="finishRateWhy()">Next ${icon('skip')}</button>` : ''}
      ` : '<p class="rate-sub" style="text-align:center">Loading…</p>'}

      ${state.rateAwaitingWhy ? '' : `
      <div class="rate-actions">
        <button class="btn-not-seen" onclick="submitNotSeen()">${icon('eye-off')}Haven't seen it</button>
        ${watchLaterButtonHtml(item)}
        <button class="btn-skip" onclick="skipRateItem()">Skip ${icon('skip')}</button>
      </div>`}
      <label class="rate-quick"><input type="checkbox" ${state.rateQuick ? 'checked' : ''}
             onchange="setRateQuick(this.checked)"> Move on right after I pick a verdict</label>
      <p class="rate-hint"><kbd>1</kbd>–<kbd>5</kbd> verdict · <kbd>D</kbd> didn't finish · <kbd>W</kbd> why · <kbd>Enter</kbd> next · <kbd>N</kbd> haven't seen · <kbd>S</kbd> skip · <kbd>L</kbd> watch later</p>
    </div>

    <div class="rate-stub">
      <span class="stub-label">Verdicts</span>
      <span class="stub-verdict ${mine ? '' : 'pending'}">${escapeHtml(me.name)}: ${myVerdict}</span>
      <span class="stub-sep">/</span>
      <span class="stub-verdict ${otherPending ? 'pending' : ''}">${escapeHtml(otherName)}: ${otherVerdict}</span>
    </div>
  `;
  card.querySelectorAll('.btn-later').forEach(paintWatchLaterButton);
  if (state.rateVocab && state.rateWhyOpen) loadRateWhy(item);
  if (item.media_type === 'tv' && state.rateVocab) loadRateSeasons(item);
}

let ratingInFlight = false;

// --- Verdicts, the folded "Why?" section and vibe-check questions ----------

async function loadRateVocab() {
  if (state.rateVocab || state.rateVocabLoading || state.rateVocabFailed) return;
  state.rateVocabLoading = true;
  try {
    state.rateVocab = await fetchRatingVocab();
  } catch (err) {
    console.error('Could not load rating options:', err);
    state.rateVocabFailed = true;
    setRateProgress("Couldn't load the rating options. Reload the page to try again.", 'error');
  } finally {
    state.rateVocabLoading = false;
  }
  renderRateCard();
}

function rateVerdictLabel(id) {
  const v = (state.rateVocab?.verdicts || []).find(x => x.id === id);
  return v ? v.label : '';
}

function resetRateDraft(item, mine) {
  state.rateDraft = {
    itemId: item.id, worked: [], didnt: [], vibe: {},
    note: '', dnf: false, dirty: false, loaded: false
  };
  state.rateWhyData = null;
  state.rateWhyOpen = false;
  state.rateMoreOpen = false;
  state.rateAwaitingWhy = false;
  state.rateSavedVerdict = null;
  state.rateSeasons = null;
}

function onRateWhyToggle(el) {
  state.rateWhyOpen = el.open;
  if (el.open) {
    const item = currentRateItem();
    if (item) loadRateWhy(item);
  }
}

async function loadRateWhy(item) {
  const body = document.getElementById('rateWhyBody');
  if (!body) return;
  if (state.rateWhyData && state.rateWhyData.media_item_id === item.id) {
    renderRateWhy();
    return;
  }
  if (state.rateWhyLoadingId === item.id) return;
  state.rateWhyLoadingId = item.id;
  body.innerHTML = '<p class="rate-sub">Loading…</p>';
  try {
    const res = await fetch(`/api/ratings/details/${item.id}?profile_id=${state.rateProfileId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (currentRateItem()?.id !== item.id) return; // moved on while loading
    state.rateWhyData = data;
    const d = state.rateDraft;
    if (!d.dirty && data.details) {
      d.worked = data.details.worked || [];
      d.didnt = data.details.didnt || [];
      d.vibe = data.details.vibe_check || {};
      d.dnf = !!data.details.did_not_finish;
      d.loaded = true;
      const dnf = document.getElementById('rateDnf');
      if (dnf) dnf.checked = d.dnf;
    }
    renderRateWhy();
  } catch (err) {
    console.error('Could not load the vibe-check questions:', err);
    body.innerHTML = '<p class="rate-sub">Couldn\'t load the questions. The verdict still saves.</p>';
  } finally {
    state.rateWhyLoadingId = null;
  }
}

function chipRow(side) {
  const d = state.rateDraft;
  return state.rateVocab.chips.map(c => {
    const on = d[side].includes(c.id);
    return `<button class="chip ${on ? 'active' : ''}" aria-pressed="${on}"
              onclick="toggleRateChip('${side}', '${escapeHtml(c.id)}')">${escapeHtml(c.label)}</button>`;
  }).join('');
}

function vibeQuestionHtml(q) {
  const picked = state.rateDraft.vibe[q.id];
  const isOn = (id) => Array.isArray(picked) ? picked.includes(id) : picked === id;
  return `
    <div class="vibe-q" role="group" aria-label="${escapeHtml(q.text)}">
      <p class="vibe-q-text">${escapeHtml(q.text)}</p>
      <div class="vibe-q-options">
        ${q.options.map(o => `
          <button class="chip ${isOn(o.id) ? 'active' : ''}" aria-pressed="${isOn(o.id)}"
                  onclick="answerVibe('${escapeHtml(q.id)}', '${escapeHtml(o.id).replace(/'/g, "\\'")}', ${q.multi})">${escapeHtml(o.label)}</button>`).join('')}
      </div>
    </div>`;
}

function renderRateWhy() {
  const body = document.getElementById('rateWhyBody');
  if (!body || !state.rateVocab) return;
  const qs = state.rateWhyData?.questions || { shown: [], more: [] };
  body.innerHTML = `
    <div class="why-group">
      <p class="why-label">What worked</p>
      <div class="why-chips">${chipRow('worked')}</div>
    </div>
    <div class="why-group">
      <p class="why-label">What didn't</p>
      <div class="why-chips">${chipRow('didnt')}</div>
    </div>
    <div class="why-group">
      <p class="why-label">Vibe check</p>
      ${qs.shown.map(vibeQuestionHtml).join('')}
      ${qs.more.length ? `
      <details class="vibe-more" ${state.rateMoreOpen ? 'open' : ''}
               ontoggle="state.rateMoreOpen = this.open">
        <summary>More questions (${qs.more.length})</summary>
        ${qs.more.map(vibeQuestionHtml).join('')}
      </details>` : ''}
    </div>
    <label class="why-label" for="rateNote">Note</label>
    <input id="rateNote" class="rate-note" type="text" maxlength="200" placeholder="One line, optional"
           value="${escapeHtml(state.rateDraft.note)}" oninput="state.rateDraft.note = this.value; scheduleRateAutosave()">
  `;
}

function toggleRateChip(side, id) {
  const d = state.rateDraft;
  const other = side === 'worked' ? 'didnt' : 'worked';
  d[other] = d[other].filter(x => x !== id);
  d[side] = d[side].includes(id) ? d[side].filter(x => x !== id) : [...d[side], id];
  d.dirty = true;
  renderRateWhy();
  scheduleRateAutosave();
}

function vibeQuestionMeta(qid) {
  const qs = state.rateWhyData?.questions || {};
  return [...(qs.shown || []), ...(qs.more || [])].find(q => q.id === qid) || {};
}

function answerVibe(qid, optId, multi) {
  const d = state.rateDraft;
  if (multi) {
    const { max = 2, exclusive = null } = vibeQuestionMeta(qid);
    const cur = Array.isArray(d.vibe[qid]) ? d.vibe[qid] : [];
    let next;
    if (cur.includes(optId)) {
      next = cur.filter(x => x !== optId);
    } else if (exclusive && optId === exclusive) {
      next = [exclusive];                                   // "Nothing much" stands alone
    } else {
      next = [...cur.filter(x => x !== exclusive), optId];
    }
    if (next.length > max) next = next.slice(-max);
    if (next.length) d.vibe[qid] = next; else delete d.vibe[qid];
  } else if (d.vibe[qid] === optId) {
    delete d.vibe[qid];
  } else {
    d.vibe[qid] = optId;
  }
  d.dirty = true;
  renderRateWhy();
  scheduleRateAutosave();
}

// Once a verdict is saved the card stays open, and anything ticked afterwards
// is saved by itself a moment later, so "Next" never loses details.
let rateAutosaveTimer = null;

function scheduleRateAutosave() {
  if (!state.rateSavedVerdict) return;
  clearTimeout(rateAutosaveTimer);
  rateAutosaveTimer = setTimeout(flushRateAutosave, 700);
}

async function flushRateAutosave() {
  clearTimeout(rateAutosaveTimer);
  rateAutosaveTimer = null;
  const item = currentRateItem();
  const d = state.rateDraft;
  if (!item || !d || d.itemId !== item.id || !state.rateSavedVerdict) return true;
  if (ratingInFlight) { scheduleRateAutosave(); return true; }
  return postRating(item.id, rateVerdictPayload(state.rateSavedVerdict));
}

function setRateQuick(on) {
  state.rateQuick = !!on;
  try { localStorage.setItem('rateQuick', on ? 'on' : 'off'); } catch (e) { /* private mode */ }
}

function rateVerdictPayload(verdict) {
  const d = state.rateDraft;
  const payload = { verdict, status: 'seen', did_not_finish: !!d.dnf };
  // Only send the extras once they've been loaded or touched, so a quick tap
  // never wipes chips saved earlier.
  if (d.dirty || d.loaded) {
    payload.worked = d.worked;
    payload.didnt = d.didnt;
    payload.vibe_check = d.vibe;
  }
  if (d.note.trim()) payload.notes = d.note.trim();
  return payload;
}

async function submitVerdict(verdict) {
  const item = currentRateItem();
  if (!item || ratingInFlight || !state.rateVocab) return;
  if (!(await postRating(item.id, rateVerdictPayload(verdict)))) return;
  clearTimeout(rateAutosaveTimer);
  rateAutosaveTimer = null;
  state.rateSavedVerdict = verdict;

  // Quick mode: straight on to the next film, as before.
  if (state.rateQuick) {
    if (item.fromSearch) showToast(`Saved: ${item.title}, ${rateVerdictLabel(verdict)}`);
    advanceQueue();
    return;
  }

  // Otherwise the card stays open so the details can be added after the
  // verdict. "Why?" opens by itself for the two extremes; for the rest it
  // stays folded but one tap away. Tapping another verdict just changes it.
  state.rateAwaitingWhy = true;
  if (state.rateVocab.extreme.includes(verdict)) state.rateWhyOpen = true;
  // "Which season lost me" matters most after a Meh, a Not for me or a stop.
  if (state.rateSeasons && (SEASONS_OPEN_ON.includes(verdict) || state.rateDraft.dnf)) state.rateSeasons.open = true;
  renderRateCard();
  setRateProgress(`Saved: ${rateVerdictLabel(verdict)}. Add details or tap Next.`);
}

async function finishRateWhy() {
  const item = currentRateItem();
  if (!item) return;
  while (ratingInFlight) await new Promise(r => setTimeout(r, 50));
  const d = state.rateDraft;
  if (d && (rateAutosaveTimer || d.dirty)) {
    if (!(await flushRateAutosave())) return;
    while (ratingInFlight) await new Promise(r => setTimeout(r, 50));
  }
  if (item.fromSearch) {
    showToast(state.rateSavedVerdict
      ? `Saved: ${item.title}, ${rateVerdictLabel(state.rateSavedVerdict)}`
      : `Saved: ${item.title}, season verdicts`);
  }
  advanceQueue();
}

// --- Per-season verdicts on TV cards -----------------------------------------
// Optional and quick: one compact row per season with the five verdicts plus
// "Didn't finish". Each tap saves on its own. Folded by default; it opens by
// itself after "Didn't finish" or a show-level Meh / Not for me. Hidden for
// one-season shows. The show verdict stays the main signal; seasons only set
// the show's score when there isn't one.

const SEASONS_OPEN_ON = ['meh', 'not_for_me'];
const SEASON_SHORT = { rewatch: 'Again', loved: 'Loved', liked: 'Liked', meh: 'Meh', not_for_me: 'Nope', did_not_finish: 'Stop' };

function seasonOptions() {
  const extra = state.rateVocab.season_extra || { id: 'did_not_finish', label: "Didn't finish" };
  return [...state.rateVocab.verdicts, extra];
}

function seasonChoice(r) {
  if (!r) return null;
  return r.verdict || (r.did_not_finish ? 'did_not_finish' : null);
}

async function loadRateSeasons(item) {
  const cur = state.rateSeasons;
  if (cur && cur.itemId === item.id) { renderRateSeasons(); return; }
  state.rateSeasons = { itemId: item.id, loading: true, count: null, ratings: {}, open: false };
  try {
    const res = await fetch(`/api/ratings/details/${item.id}?profile_id=${state.rateProfileId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (currentRateItem()?.id !== item.id || state.rateSeasons?.itemId !== item.id) return;
    const s = state.rateSeasons;
    s.loading = false;
    s.count = data.seasons?.count || null;
    for (const r of data.seasons?.ratings || []) s.ratings[r.season_number] = seasonChoice(r);
    if (state.rateDraft?.dnf) s.open = true;
    renderRateSeasons();
  } catch (err) {
    console.error('Could not load season verdicts:', err);
    if (state.rateSeasons?.itemId === item.id) state.rateSeasons.loading = false;
  }
}

function seasonsSummaryText(s) {
  const rated = Object.entries(s.ratings).filter(([, v]) => v);
  if (!rated.length) return `optional · ${s.count} seasons`;
  const stopped = rated.filter(([, v]) => v === 'did_not_finish').map(([n]) => +n).sort((a, b) => a - b)[0];
  return `${rated.length} of ${s.count} rated${stopped ? ` · stopped in S${stopped}` : ''}`;
}

function renderRateSeasons() {
  const slot = document.getElementById('rateSeasonsSlot');
  const s = state.rateSeasons;
  if (!slot || !s || s.itemId !== currentRateItem()?.id) return;
  if (s.loading || !s.count || s.count < 2) { slot.innerHTML = ''; return; }
  const opts = seasonOptions();
  const rows = [];
  for (let n = 1; n <= s.count; n++) {
    const picked = s.ratings[n] || null;
    // Roving focus: one tab stop per season (the picked option, else the first).
    const focusId = picked || opts[0].id;
    rows.push(`
      <div class="season-row" role="radiogroup" aria-label="Season ${n}" data-season="${n}">
        <span class="season-label" aria-hidden="true">S${n}</span>
        <div class="season-opts">
          ${opts.map((o, i) => `
            <button type="button" role="radio" class="season-opt season-${escapeHtml(o.id)} ${picked === o.id ? 'active' : ''}"
                    id="season-${n}-${escapeHtml(o.id)}" aria-checked="${picked === o.id}"
                    aria-label="${escapeHtml(o.label)}" title="${escapeHtml(o.label)} (${i + 1})"
                    tabindex="${o.id === focusId ? 0 : -1}"
                    onclick="pickSeason(${n}, '${escapeHtml(o.id)}')">${escapeHtml(SEASON_SHORT[o.id] || o.label)}</button>`).join('')}
        </div>
      </div>`);
  }
  slot.innerHTML = `
    <details class="rate-seasons" id="rateSeasons" ${s.open ? 'open' : ''} ontoggle="state.rateSeasons && (state.rateSeasons.open = this.open)">
      <summary>Seasons <span class="rate-why-sub">${escapeHtml(seasonsSummaryText(s))}</span></summary>
      <div class="rate-seasons-body" onkeydown="onSeasonKey(event)">
        <p class="rate-seasons-hint">One tap per season, all optional. Tap again to clear.</p>
        ${rows.join('')}
      </div>
    </details>`;
}

async function pickSeason(n, choice, keepFocus = true) {
  const item = currentRateItem();
  const s = state.rateSeasons;
  if (!item || !s || s.itemId !== item.id) return;
  const before = s.ratings[n] || null;
  const next = before === choice ? null : choice; // tap again clears
  s.ratings[n] = next;
  renderRateSeasons();
  if (keepFocus) document.getElementById(`season-${n}-${choice}`)?.focus();
  const body = {
    media_item_id: item.id, profile_id: state.rateProfileId, season_number: n,
    verdict: next && next !== 'did_not_finish' ? next : null,
    did_not_finish: next === 'did_not_finish'
  };
  try {
    const res = await fetch('/api/ratings/season', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    if (currentRateItem()?.id !== item.id) return;
    // The stub shows the show verdict, which may now come from the seasons.
    if (data.summary) item.co_view = { ...(item.co_view || {}), verdicts: data.summary.verdicts, tier: data.summary.tier };
    if (!state.rateAwaitingWhy && !state.rateSavedVerdict) {
      // Seasons alone count as rating the show: offer Next instead of Skip.
      state.rateAwaitingWhy = true;
      renderRateCard();
      document.getElementById(`season-${n}-${choice}`)?.focus();
    } else {
      updateRateStubMine(item);
    }
    setRateProgress(next ? `Saved: season ${n}, ${seasonOptions().find(o => o.id === next)?.label}` : `Cleared season ${n}`);
  } catch (err) {
    console.error('Season verdict failed:', err);
    s.ratings[n] = before;
    renderRateSeasons();
    setRateProgress(`Season ${n} not saved (${err.message}). Try again.`, 'error');
  }
}

function updateRateStubMine(item) {
  const mine = (item.co_view?.verdicts || []).find(v => v.profile_id === state.rateProfileId);
  const el = document.querySelector('#rateCard .rate-stub .stub-verdict');
  if (!el || !mine?.verdict) return;
  const how = mine.inferred ? (mine.inferred_source === 'seasons' ? ' (from seasons)' : ' (guessed)') : '';
  el.classList.remove('pending');
  el.innerHTML = `${escapeHtml(activeRateProfile().name)}: <b>${escapeHtml(rateVerdictLabel(mine.verdict) + how)}</b>`;
}

// Inside the Seasons rows: ←/→ move along a season, ↑/↓ between seasons,
// 1-6 pick for the focused season (instead of the show-level 1-5).
function onSeasonKey(e) {
  const btn = e.target.closest?.('.season-opt');
  if (!btn) return;
  const row = btn.closest('.season-row');
  const n = +row.dataset.season;
  const buttons = [...row.querySelectorAll('.season-opt')];
  const i = buttons.indexOf(btn);
  const move = (target) => {
    if (!target) return;
    buttons.forEach(b => b.tabIndex = -1);
    target.tabIndex = 0;
    target.focus();
  };
  if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
    move(buttons[(i + (e.key === 'ArrowRight' ? 1 : buttons.length - 1)) % buttons.length]);
  } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    const other = document.querySelector(`.season-row[data-season="${n + (e.key === 'ArrowDown' ? 1 : -1)}"]`);
    if (other) move(other.querySelectorAll('.season-opt')[i]);
  } else if (/^[1-6]$/.test(e.key)) {
    const opt = seasonOptions()[+e.key - 1];
    if (opt) pickSeason(n, opt.id);
  } else {
    return;
  }
  e.preventDefault();
  e.stopPropagation();
}

function onRateDnfChange(checked) {
  if (!state.rateDraft) return;
  state.rateDraft.dnf = checked;
  scheduleRateAutosave();
  // Stopping partway is exactly when "which season lost me" is worth a tap.
  if (checked && state.rateSeasons && !state.rateSeasons.open) {
    state.rateSeasons.open = true;
    renderRateSeasons();
  }
}

function toggleRateSortMode() {
  if (state.pairMode) togglePairMode(false);
  state.rateSortMode = !state.rateSortMode;
  setPressed(document.getElementById('rateSortBtn'), state.rateSortMode);
  document.getElementById('rateGenreRow')?.classList.toggle('is-hidden', state.rateSortMode);
  loadRatingQueue(true);
}

// ---------------------------------------------------------------------------
// This or that: two films you've seen from the same genre, pick the one that's
// more your thing (services/pairs.py). Optional, about 10 pairs per round, and
// never mixed into the rating cards.
// ---------------------------------------------------------------------------
const PAIR_ROUND = 10;

function togglePairMode(on) {
  state.pairMode = typeof on === 'boolean' ? on : !state.pairMode;
  if (state.pairMode) {
    state.rateSortMode = false;
    setPressed(document.getElementById('rateSortBtn'), false);
  }
  setPressed(document.getElementById('pairBtn'), state.pairMode);
  document.getElementById('rateGenreRow')?.classList.toggle('is-hidden', state.pairMode || state.rateSortMode);
  document.getElementById('rateCard').style.display = state.pairMode ? 'none' : '';
  document.getElementById('rateEmpty').style.display = 'none';
  document.getElementById('pairView').classList.toggle('is-hidden', !state.pairMode);
  state.pairSkipped = new Set();
  state.pairDone = 0;
  if (state.pairMode) loadPairRound();
  else loadRatingQueue(true);
}

async function loadPairRound() {
  state.pairLoaded = false;
  setRateProgress('');
  document.getElementById('pairView').innerHTML = '<p class="rate-sub" style="text-align:center; padding: 20px;">Loading…</p>';
  try {
    const params = new URLSearchParams({
      profile_id: state.rateProfileId, media_type: state.rateMediaType,
      limit: Math.min(30, PAIR_ROUND + state.pairSkipped.size)
    });
    const res = await fetch(`/api/ratings/pairs?${params}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const key = p => `${Math.min(p.a.id, p.b.id)}-${Math.max(p.a.id, p.b.id)}`;
    state.pairs = (data.pairs || []).filter(p => !state.pairSkipped.has(key(p))).slice(0, PAIR_ROUND);
    state.pairIndex = 0;
    state.pairDone = 0;
    state.pairLoaded = true;
    renderPairs();
  } catch (err) {
    console.error('Failed to load pairs:', err);
    setRateProgress("Couldn't load pairs. Check the server and try again.", 'error');
    document.getElementById('pairView').innerHTML = '';
  }
}

function pairCardHtml(f, side, n) {
  const mine = f.verdict ? rateVerdictLabel(f.verdict) : '';
  return `
    <button class="pair-card" type="button" onclick="answerPair('${side}')" aria-label="${escapeHtml(f.title)}, option ${n}">
      ${posterHtml(f.poster_url, f.title, 'pair-poster')}
      <span class="pair-title">${escapeHtml(f.title)}</span>
      <span class="pair-meta">${[f.release_year, mine && `You: ${escapeHtml(mine)}`].filter(Boolean).join(' · ')}</span>
      <span class="pair-key"><kbd>${n}</kbd></span>
    </button>`;
}

function renderPairs() {
  const box = document.getElementById('pairView');
  if (!box || !state.pairMode) return;
  const pair = state.pairs[state.pairIndex];
  if (!state.pairLoaded) return;
  if (!pair) {
    const none = state.pairDone === 0;
    setRateProgress('');
    box.innerHTML = `
      <div class="rate-empty">
        <p class="empty-title">${none ? 'No good pairs left' : 'Round done'}</p>
        <p class="rate-sub">${none
          ? 'Nothing left to compare right now. Rate more films you have seen, and new pairs will appear.'
          : `You answered ${state.pairDone}. Each one sharpens which flavour you mean.`}</p>
        ${none ? '' : '<button class="btn-secondary" onclick="loadPairRound()">Another round</button>'}
      </div>`;
    return;
  }
  setRateProgress(`This or that · ${state.pairIndex + 1} of ${state.pairs.length}`);
  box.innerHTML = `
    <div class="pair-question">
      <p class="rate-question">Which is more your thing?</p>
      <p class="rate-sub pair-genre">${escapeHtml(pair.shared_genre)}</p>
    </div>
    <div class="pair-cards">
      ${pairCardHtml(pair.a, 'a', 1)}
      ${pairCardHtml(pair.b, 'b', 2)}
    </div>
    <div class="rate-actions pair-actions">
      <button class="btn-secondary" type="button" onclick="answerPair('same')">About the same <kbd>3</kbd></button>
      <button class="btn-skip" type="button" onclick="skipPair()">Skip <kbd>S</kbd></button>
    </div>
    <p class="rate-hint"><kbd>1</kbd> left · <kbd>2</kbd> right · <kbd>3</kbd> about the same · <kbd>S</kbd> skip</p>`;
}

let pairInFlight = false;
async function answerPair(choice) {
  const pair = state.pairs[state.pairIndex];
  if (!pair || pairInFlight) return;
  pairInFlight = true;
  try {
    const res = await fetch('/api/ratings/pairs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile_id: state.rateProfileId, a_id: pair.a.id, b_id: pair.b.id, choice })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    state.pairDone += 1;
    state.pairIndex += 1;
    renderPairs();
  } catch (err) {
    console.error('Pair answer failed:', err);
    setRateProgress(`Not saved (${err.message}). Try again.`, 'error');
  } finally {
    pairInFlight = false;
  }
}

function skipPair() {
  const pair = state.pairs[state.pairIndex];
  if (!pair) return;
  state.pairSkipped.add(`${Math.min(pair.a.id, pair.b.id)}-${Math.max(pair.a.id, pair.b.id)}`);
  state.pairIndex += 1;
  renderPairs();
}

async function submitNotSeen() {
  const item = currentRateItem();
  if (!item || ratingInFlight) return;
  // "Haven't seen it" is a real answer — stored with status='not_seen' and no
  // rating attached, never as a zero or a 1.
  if (!(await postRating(item.id, { status: 'not_seen' }))) return;
  // "Haven't seen it" is often the moment someone says "later": offer it once.
  if (!onMyWatchLater(item.id)) {
    showToast(`Saved as not seen. Add ${item.title} to Watch later?`, {
      label: 'Add to Watch later',
      run: () => { if (!onMyWatchLater(item.id)) toggleWatchLater(item.id, item.title, null); }
    });
  }
  advanceQueue();
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
    const shown = currentRateItem();
    if (shown && shown.id === itemId && summary?.verdicts) {
      shown.co_view = { ...(shown.co_view || {}), verdicts: summary.verdicts, tier: summary.tier };
      updateRateStubMine(shown);
    }
    flashRateFeedback(summary);
    refreshLaterCount();   // a seen verdict can finish a title on the list
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
  state.rateDraft = null;
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
  state.rateDraft = null;
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

// Keyboard shortcuts for the rating card (1-5 verdict, D = didn't finish,
// W = open/close Why, N = not seen, S = skip).
document.addEventListener('keydown', (e) => {
  if (state.activeTab !== 'rate') return;
  if (e.target && ['INPUT', 'TEXTAREA'].includes(e.target.tagName)) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (document.querySelector('.modal-overlay.active')) return;
  const k = e.key.toLowerCase();
  if ((k === 'enter' || k === 'arrowright') && state.rateAwaitingWhy && !state.pairMode) {
    if (k === 'enter' && e.target && ['BUTTON', 'SUMMARY', 'A'].includes(e.target.tagName)) return;
    finishRateWhy();
    e.preventDefault();
    return;
  }
  if (k.length !== 1) return;
  if (state.pairMode) {
    if (k === '1') answerPair('a'); else if (k === '2') answerPair('b');
    else if (k === '3') answerPair('same'); else if (k === 's') skipPair();
    else return;
    e.preventDefault();
    return;
  }
  if (state.rateAwaitingWhy && (k === 'n' || k === 's')) return; // saved; Next moves on
  if (k >= '1' && k <= '5' && state.rateVocab) {
    const v = state.rateVocab.verdicts[parseInt(k, 10) - 1];
    if (v) submitVerdict(v.id);
    e.preventDefault();
  } else if (k === 'd') {
    const box = document.getElementById('rateDnf');
    if (box && state.rateDraft) { box.checked = !box.checked; onRateDnfChange(box.checked); }
  } else if (k === 'w') {
    const why = document.getElementById('rateWhy');
    if (why) why.open = !why.open;
  } else if (k === 'n') {
    submitNotSeen();
  } else if (k === 's') {
    skipRateItem();
  } else if (k === 'l') {
    const later = document.querySelector('#rateCard .btn-later');
    if (later) { later.click(); e.preventDefault(); }
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
      noteOnList(data.items);
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
              ${row.kind === 'item' ? watchLaterButtonHtml(d, { inRow: true }) : ''}
            </li>`;
  }).join('');
  list.innerHTML = html;
  list.querySelectorAll('.btn-later').forEach(paintWatchLaterButton);
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
  if (rateAutosaveTimer) flushRateAutosave();
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

