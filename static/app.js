// Mobile-First Cinema Companion Client (static/app.js)

const state = {
  activeTab: 'discover',
  recMediaType: 'movie',    // 'movie' or 'tv'
  recProfile: 'together',  // 'me' | 'partner' | 'together' — whose taste to optimise for
  recPage: 1,            // recommendation page for "load more"
  recHasMore: false,     // more results available in the pool
  dismissedIds: new Set(), // items tapped away ("not this one")
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

// Initialize app on DOM ready
document.addEventListener('DOMContentLoaded', () => {
  initPWA();
  checkStatus();
  loadMoods();
  checkActiveWatch();
  initStarSelector();
  initSpeechRecognition();
});

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
      text.innerText = 'TV Stream Ready';
    } else {
      dot.className = 'status-dot';
      text.innerText = 'TV Offline';
    }
  } catch (err) {
    console.error('Status check error:', err);
  }
}

// ---------------------------------------------------------------------------
// 2. Navigation Tabs
// ---------------------------------------------------------------------------
function switchTab(tab) {
  state.activeTab = tab;

  document.getElementById('tabDiscover').classList.toggle('active', tab === 'discover');
  document.getElementById('tabDirect').classList.toggle('active', tab === 'direct');
  document.getElementById('tabRate').classList.toggle('active', tab === 'rate');
  document.getElementById('tabHistory').classList.toggle('active', tab === 'history');

  document.getElementById('discoverView').style.display = tab === 'discover' ? 'block' : 'none';
  document.getElementById('directView').style.display = tab === 'direct' ? 'block' : 'none';
  document.getElementById('rateView').style.display = tab === 'rate' ? 'block' : 'none';
  document.getElementById('historyView').style.display = tab === 'history' ? 'block' : 'none';

  if (tab === 'history') {
    loadHistory();
  }
  if (tab === 'rate') {
    initRateTab();
  }
}

// ---------------------------------------------------------------------------
// 3. Recommender: Movie / TV Toggle & Mood Picker
// ---------------------------------------------------------------------------
function setRecProfile(profile) {
  if (state.recProfile === profile) return;
  state.recProfile = profile;
  document.querySelectorAll('[data-rec-profile]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.recProfile === profile);
  });
  fetchRecommendations();
}

function setRecMediaType(type) {
  state.recMediaType = type;
  document.getElementById('recTypeMovieBtn').classList.toggle('active', type === 'movie');
  document.getElementById('recTypeTvBtn').classList.toggle('active', type === 'tv');

  // Hide duration filter for TV Shows
  const durationGroup = document.getElementById('durationFilterGroup');
  if (durationGroup) {
    durationGroup.style.display = type === 'movie' ? 'block' : 'none';
  }

  const btn = document.getElementById('recommendBtn');
  if (btn) {
    btn.innerHTML = `<span>✨ Find ${type === 'tv' ? 'TV Show' : 'Movie'} for Tonight</span>`;
  }
}

async function loadMoods() {
  try {
    const res = await fetch('/api/moods');
    state.moodsData = await res.json();
    const grid = document.getElementById('moodGrid');
    grid.innerHTML = '';

    state.moodsData.forEach(mood => {
      const card = document.createElement('div');
      card.className = `mood-card ${mood.id === state.activeMoodId ? 'active' : ''}`;
      card.id = `mood-${mood.id}`;
      card.onclick = () => selectMood(mood.id);

      const emojiMatch = mood.name.match(/^(\p{Extended_Pictographic}|\p{Emoji_Presentation})/u);
      const emoji = emojiMatch ? emojiMatch[0] : '🎬';
      const cleanName = mood.name.replace(emoji, '').trim();

      card.innerHTML = `
        <div class="mood-icon">${emoji}</div>
        <div class="mood-name">${cleanName}</div>
      `;
      grid.appendChild(card);
    });

    renderSubMoods(state.activeMoodId);
  } catch (err) {
    console.error('Error loading moods:', err);
  }
}

function selectMood(moodId) {
  state.activeMoodId = moodId;
  state.activeSubMood = null;
  document.querySelectorAll('.mood-card').forEach(c => c.classList.remove('active'));
  const activeCard = document.getElementById(`mood-${moodId}`);
  if (activeCard) activeCard.classList.add('active');

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

  // All / Default chip
  const allChip = document.createElement('button');
  allChip.className = `chip ${!state.activeSubMood ? 'active' : ''}`;
  allChip.innerText = 'All Vibes';
  allChip.onclick = () => {
    state.activeSubMood = null;
    chips.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
    allChip.classList.add('active');
  };
  chips.appendChild(allChip);

  moodInfo.submoods.forEach(sm => {
    const chip = document.createElement('button');
    chip.className = `chip ${state.activeSubMood === sm.name ? 'active' : ''}`;
    chip.innerText = sm.name;
    chip.onclick = () => {
      state.activeSubMood = sm.name;
      chips.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
    };
    chips.appendChild(chip);
  });
}

// ---------------------------------------------------------------------------
// 4. Recommender: Extra Options (Genres, Rating, Watched)
// ---------------------------------------------------------------------------
function toggleGenreFilter(btn) {
  const genre = btn.getAttribute('data-genre');
  if (state.selectedGenres.has(genre)) {
    state.selectedGenres.delete(genre);
    btn.classList.remove('active');
  } else {
    state.selectedGenres.add(genre);
    btn.classList.add('active');
  }
}

function clearSelectedGenres() {
  state.selectedGenres.clear();
  document.querySelectorAll('#genreChips .chip').forEach(c => c.classList.remove('active'));
}

function setFilter(btn) {
  const filterType = btn.getAttribute('data-filter');
  const val = btn.getAttribute('data-val');

  btn.parentElement.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
  btn.classList.add('active');

  if (filterType === 'era') state.era = val;
  if (filterType === 'duration') state.duration = val;
}

function setMinRatingFilter(btn) {
  const val = btn.getAttribute('data-val');
  btn.parentElement.querySelectorAll('.chip').forEach(c => {
    if (c.getAttribute('data-filter') === 'minRating') c.classList.remove('active');
  });
  btn.classList.add('active');
  state.minRating = val === 'default' ? null : parseFloat(val);
}

function toggleIncludeWatched() {
  state.includeWatched = !state.includeWatched;
  const btn = document.getElementById('includeWatchedBtn');
  if (btn) btn.classList.toggle('active', state.includeWatched);
}

async function fetchRecommendations() {
  const container = document.getElementById('resultsContainer');
  const btn = document.getElementById('recommendBtn');
  const query = document.getElementById('queryInput').value.trim();

  btn.innerHTML = '<span>Analyzing Catalogue & Reviews... ⏳</span>';
  btn.disabled = true;

  try {
    state.recPage = 1;
    const data = await requestRecommendations(1);
    state.recHasMore = !!data.has_more;
    renderRecommendations(data.items || [], data.engine);
    updateLoadMoreButton();
  } catch (err) {
    console.error('Error fetching recommendations:', err);
    container.innerHTML = '<p style="text-align:center; color:var(--danger)">Failed to load recommendations.</p>';
    updateLoadMoreButton();
  } finally {
    btn.innerHTML = `<span>✨ Find ${state.recMediaType === 'tv' ? 'TV Show' : 'Movie'} for Tonight</span>`;
    btn.disabled = false;
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
    profile: state.recProfile
  };
  const res = await fetch('/api/recommend', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  if (!res.ok) throw new Error('HTTP ' + res.status);
  return await res.json();
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
  movies.forEach((m) => container.appendChild(buildMovieCard(m, engine)));
  updateLoadMoreButton();
}

function renderRecommendations(movies, engine) {
  const container = document.getElementById('resultsContainer');
  container.innerHTML = '';

  if (!movies || movies.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <p style="font-size: 28px; margin-bottom: 8px;">🎞️</p>
        <p>No matching ${state.recMediaType === 'tv' ? 'TV shows' : 'movies'} found with those criteria.</p>
        <p style="font-size: 13px; margin-top: 6px; color: var(--text-muted);">Try relaxing the era, quality threshold, or choosing "Surprise Me".</p>
      </div>
    `;
    return;
  }

  movies.forEach((m) => container.appendChild(buildMovieCard(m, engine)));
}

// Extracted so "Show more" and the initial render build identical cards.
function buildMovieCard(m, engine) {
    const card = document.createElement('div');
    card.className = 'movie-card';

    const posterSrc = m.poster_url || 'https://via.placeholder.com/150x225/161722/ffffff?text=No+Poster';
    const tasteBonus = m.taste_bonus > 0 ? `<span class="badge badge-bonus">+${m.taste_bonus} Taste Boost</span>` : '';
    // Co-view badge: why this film is ranked where it is.
    const tier = m.co_view_tier;
    const tierBadge = tier === 'A'
      ? `<span class="badge badge-coview-a" title="You both liked this">💞 You both liked it</span>`
      : tier === 'B'
        ? `<span class="badge badge-coview-b" title="${escapeHtml(m.tier_reason || 'One of you loved it; the other hasn\'t seen it')}">🧑 ${escapeHtml(m.tier_reason || "He'd recommend this")}</span>`
        : '';
    const combinedBadge = (m.combined_score && tier === 'A')
      ? `<span class="badge badge-coview-score" title="Your combined score for this film">Together ★ ${m.combined_score}</span>` : '';
    const queryBoost = m.query_boost > 0 ? `<span class="badge badge-query">+${m.query_boost} Match</span>` : '';
    const runtimeStr = m.runtime ? `<span class="badge badge-runtime">⏱ ${m.runtime}m</span>` : '';
    const engineBadge = engine === 'generative'
      ? '<span class="engine-badge badge-generative">🧠 Learning Your Taste</span>'
      : '<span class="engine-badge badge-legacy">🎲 Picks for You</span>';
    const reviewsBuzz = m.tier2_reviews_summary ? `<div class="movie-buzz">💬 ${m.tier2_reviews_summary}</div>` : '';

    const hasLocalFile = m.file_path && m.file_path.length > 0;
    const actionButtonHtml = hasLocalFile
      ? `<button class="btn-watch" style="width: 100%; background: #10b981;" onclick="playLocalItem(${m.id}, '${m.title.replace(/'/g, "\\'")}')">
           <span>▶️ Play Now</span>
         </button>`
      : `<button id="watch-btn-${m.id}" class="btn-watch" style="width: 100%;" onclick="startDownload(${m.id}, '${m.title.replace(/'/g, "\\'")}', '${m.media_type || state.recMediaType}')">
           <span>🍿 Watch Tonight</span>
         </button>`;

    card.innerHTML = `
      <div class="movie-card-main">
        <img class="movie-poster" src="${posterSrc}" alt="${m.title}" loading="lazy">
        <div class="movie-details">
          <div class="movie-title-row">
            <h3 class="movie-title">${m.title}</h3>
            <span class="movie-year">${m.release_year || ''}</span>
          </div>
          <div class="movie-badges">
            <span class="badge badge-tmdb">★ ${m.rating || 'N/A'}</span>
            ${engineBadge}
            ${tasteBonus}
            ${tierBadge}
            ${combinedBadge}
            ${queryBoost}
            ${runtimeStr}
          </div>
          <div class="movie-genres">${m.genres || (m.media_type === 'tv' ? 'TV Series' : 'Movie')}</div>
          ${m.tier2_keywords ? `<div class="movie-vibes">🏷 ${m.tier2_keywords}</div>` : ''}
          ${reviewsBuzz}
        </div>
      </div>
      <div class="movie-synopsis">${m.overview || 'No synopsis provided.'}</div>
      <div class="movie-footer">
        <div id="watch-container-${m.id}" style="flex: 1; margin-right: 8px;">
          ${actionButtonHtml}
        </div>
        <a class="btn-tv-link" href="${state.jellyfinUrl}" target="_blank">
          <span>📺 Jellyfin</span>
        </a>
      </div>
    `;
    return card;
}

// ---------------------------------------------------------------------------
// 5. Direct Download Subsystem (Movies & TV Shows)
// ---------------------------------------------------------------------------
function setDirectMediaType(type) {
  state.directMediaType = type;
  document.getElementById('directTypeMovieBtn').classList.toggle('active', type === 'movie');
  document.getElementById('directTypeTvBtn').classList.toggle('active', type === 'tv');

  const input = document.getElementById('directQueryInput');
  if (input) {
    input.placeholder = type === 'tv'
      ? 'Search TV series or episode (e.g. Stranger Things S01E01, Breaking Bad, The Bear)...'
      : 'Search any movie title (e.g. Inception, Dune 2024, Interstellar)...';
  }
}

function setDirectSort(sortBySize) {
  state.directSortBySize = sortBySize;
  document.getElementById('sortSmallestBtn').classList.toggle('active', sortBySize);
  document.getElementById('sortQualityBtn').classList.toggle('active', !sortBySize);

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

  btn.innerHTML = '<span>Searching... ⏳</span>';
  btn.disabled = true;
  container.innerHTML = `
    <div class="empty-state">
      <p style="font-size: 28px; margin-bottom: 8px;">🛰️</p>
      <p>Searching ${state.directMediaType === 'tv' ? 'TV torrent trackers (Apibay)' : 'YTS & Apibay'} for "${query}"...</p>
      <p style="font-size: 13px; color: var(--text-muted); margin-top: 4px;">Checking seeds and quality indicators...</p>
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
      <div class="empty-state" style="color: var(--danger)">
        <p style="font-size: 28px; margin-bottom: 8px;">❌</p>
        <p>Search failed: ${err.message}</p>
        <p style="font-size: 13px; color: var(--text-muted); margin-top: 4px;">Check backend network access and retry.</p>
      </div>
    `;
  } finally {
    btn.innerHTML = '<span>Search 🚀</span>';
    btn.disabled = false;
  }
}

function renderDirectResults(results, query) {
  const container = document.getElementById('directResultsContainer');
  container.innerHTML = '';

  if (!results || results.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <p style="font-size: 28px; margin-bottom: 8px;">📭</p>
        <p>No active torrents found for "${query}".</p>
        <p style="font-size: 13px; color: var(--text-muted); margin-top: 4px;">Torrents with 0 seeds are automatically filtered out.</p>
      </div>
    `;
    return;
  }

  const header = document.createElement('div');
  header.className = 'results-count-banner';
  header.innerHTML = `Found <strong>${results.length}</strong> verified torrents for "<em>${query}</em>"`;
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

    const safeTitle = (item.name || query).replace(/'/g, "\\'").replace(/"/g, '&quot;');
    const containerId = `direct-dl-slot-${idx}`;
    const mediaType = item.media_type || state.directMediaType;

    card.innerHTML = `
      <div class="torrent-card-main">
        <div class="torrent-header">
          <div class="torrent-name" title="${item.name}">${item.name}</div>
        </div>
        <div class="torrent-meta-row">
          <span class="badge ${badgeClass}">${item.quality || 'HD'}</span>
          <span class="badge badge-media-type">${mediaType === 'tv' ? '📺 TV' : '🎬 Movie'}</span>
          <span class="badge badge-size">💾 ${item.size_display || formatSize(item.size_bytes)}</span>
          <span class="badge badge-seeds">🟢 ${item.seeds} seeds</span>
          <span class="badge badge-peers">🔵 ${item.peers || 0} leech</span>
        </div>
        <div class="torrent-action-row" id="${containerId}">
          <button class="btn-download-direct" onclick="startDirectDownload('${encodeURIComponent(item.magnet)}', '${safeTitle}', '${containerId}', '${mediaType}')">
            <span>⬇️ Direct Download</span>
          </button>
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
    <button class="btn-download-direct" style="opacity: 0.7;" disabled>
      <span>⏳ Queuing Download...</span>
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
      <button class="btn-download-direct" style="background: var(--danger);">
        <span>❌ ${err.message}</span>
      </button>
    `;
    setTimeout(() => {
      container.innerHTML = `
        <button class="btn-download-direct" onclick="startDirectDownload('${encodedMagnet}', '${movieTitle.replace(/'/g, "\\'")}', '${containerId}', '${mediaType}')">
          <span>⬇️ Direct Download</span>
        </button>
      `;
    }, 3500);
  }
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
      container.innerHTML = `
        <button class="btn-download-direct" onclick="startDirectDownload('${downloadInfo.encodedMagnet || ''}', '${downloadInfo.title.replace(/'/g, "\\'")}', '${containerId}', '${downloadInfo.mediaType || 'movie'}')">
          <span>⬇️ Re-Download</span>
        </button>
      `;
    }
  } catch (err) {
    console.error('Cancel download error:', err);
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
          container.innerHTML = `
            <div class="progress-box ${data.status === 'cancelled' ? 'cancelled-box' : 'error-box'}">
              <span>${data.status === 'cancelled' ? '🛑 Download Cancelled' : `❌ Error: ${data.error}`}</span>
              <button class="btn-retry-mini" onclick="pollDownloadProgress(${movieId}, '${containerId}', '${movieTitle.replace(/'/g, "\\'")}', '${mediaType}')">Refresh</button>
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
            <button class="btn-ready-watch" onclick="playLocalItem(${movieId}, '${(data.name || movieTitle).replace(/'/g, "\\'")}')">
              <span>🎬 Ready to Watch</span>
            </button>
          `;
        }
        return;
      }

      // In-place Progress Bar with Cancel Action
      if (container) {
        let statusLabel = `⬇️ ${pct.toFixed(1)}% | ${speedStr}`;
        if (totalStr !== '0 B') {
          statusLabel += ` (${downloadedStr} / ${totalStr})`;
        }
        if (stateStr === 'organizing') {
          statusLabel = `📂 Organizing ${mediaType === 'tv' ? 'TV episode' : 'movie'} for Jellyfin...`;
        }

        container.innerHTML = `
          <div class="download-progress-bar-wrapper">
            <div class="download-progress-bar-fill" style="width: ${pct}%;"></div>
            <div class="download-progress-bar-text">
              ${statusLabel}
            </div>
            <button class="btn-cancel-mini-bar" onclick="cancelDownload(${movieId}, '${containerId}')" title="Cancel download">✕</button>
          </div>
        `;
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

    row.innerHTML = `
      <div style="flex: 1; min-width: 0;">
        <div class="active-download-title">${item.title}</div>
        <div class="active-download-sub">
          ${isReady ? '🎬 Ready to Watch' : `${item.progress.toFixed(1)}% · ${item.speedStr || '0 KB/s'} · ${item.status}`}
        </div>
      </div>
      <div style="display: flex; gap: 6px; align-items: center;">
        ${isReady
          ? `<button class="btn-ready-mini" onclick="playLocalItem(${item.movieId}, '${item.title.replace(/'/g, "\\'")}')">Play 🎬</button>`
          : `<button class="btn-cancel-mini" onclick="cancelDownload(${item.movieId}, '${item.containerId}')">Cancel ✕</button>`
        }
      </div>
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
    <button class="btn-watch" style="width: 100%; opacity: 0.8;" disabled>
      <span>🔍 Searching Torrents...</span>
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
    if (!results || results.length === 0) throw new Error('No torrents with seeds found');

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
    container.innerHTML = `
      <button class="btn-watch" style="width: 100%; background: var(--danger);">
        <span>❌ ${err.message}</span>
      </button>
    `;
    setTimeout(() => {
      container.innerHTML = `
        <button id="watch-btn-${movieId}" class="btn-watch" style="width: 100%;" onclick="startDownload(${movieId}, '${movieTitle.replace(/'/g, "\\'")}', '${mediaType}')">
          <span>🍿 Watch Tonight</span>
        </button>
      `;
    }, 3000);
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

  poster.src = item.poster_url || 'https://via.placeholder.com/55x80/161722/ffffff?text=Poster';
  title.innerText = `${item.title} (${item.release_year || 'Media'})`;
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
    star.innerHTML = '★';
    star.onclick = () => selectStarRating(i);
    container.appendChild(star);
  }
  updateStarLabel(state.selectedRating);
}

function selectStarRating(val) {
  state.selectedRating = val;
  const stars = document.querySelectorAll('.star-btn');
  stars.forEach((s, idx) => {
    s.classList.toggle('active', idx < val);
  });
  updateStarLabel(val);
}

function updateStarLabel(val) {
  const label = document.getElementById('starLabel');
  if (label) label.innerText = `Rating: ${val}/10 ★`;
}

function openFeedbackModal() {
  if (!state.activeSession) return;
  document.getElementById('modalMovieTitle').innerText = state.activeSession.title;
  document.getElementById('feedbackModal').classList.add('active');
}

function closeFeedbackModal() {
  document.getElementById('feedbackModal').classList.remove('active');
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

    switchTab('history');
  } catch (err) {
    console.error('Submit feedback error:', err);
  }
}

// ---------------------------------------------------------------------------
// 11. History & Taste Profile View
// ---------------------------------------------------------------------------
async function loadHistory() {
  const list = document.getElementById('historyList');
  const genreContainer = document.getElementById('tasteGenresContainer');
  const kwContainer = document.getElementById('tasteKeywordsContainer');

  list.innerHTML = '<p style="text-align: center; color: var(--text-muted);">Loading watch history...</p>';

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
      genreContainer.innerHTML = '<span style="font-size: 13px; color: var(--text-muted);">Rate films or series (≥ 7.0) to build your personal taste profile!</span>';
    } else {
      topGenres.forEach(([genre, score]) => {
        const tag = document.createElement('span');
        tag.className = 'taste-tag';
        tag.innerText = `🎭 ${genre} (+${score.toFixed(1)})`;
        genreContainer.appendChild(tag);
      });

      topKws.forEach(([kw, score]) => {
        const tag = document.createElement('span');
        tag.className = 'taste-tag';
        tag.innerText = `🏷 ${kw}`;
        kwContainer.appendChild(tag);
      });
    }

    list.innerHTML = '';
    const items = data.history || [];
    if (items.length === 0) {
      list.innerHTML = '<p style="text-align: center; color: var(--text-muted); padding: 20px;">No movies logged yet. Watch and rate your first film!</p>';
      return;
    }

    items.forEach(h => {
      const itemEl = document.createElement('div');
      itemEl.className = 'history-item';
      const ratingStr = h.user_rating ? `★ ${h.user_rating}/10` : 'Unrated';
      const notesHtml = h.notes ? `<div style="font-size: 13px; color: #d1d5db; margin-top: 4px;">"${h.notes}"</div>` : '';
      const dateStr = (h.watched_at || '').substring(0, 10);

      itemEl.innerHTML = `
        <div class="history-info">
          <div class="history-title">${h.title} <span class="movie-year">(${h.release_year || ''})</span></div>
          <div class="history-meta">${dateStr} · ${h.genres || 'Film'}</div>
          ${notesHtml}
        </div>
        <div class="history-rating">${ratingStr}</div>
      `;
      list.appendChild(itemEl);
    });

  } catch (err) {
    console.error('Load history error:', err);
    list.innerHTML = '<p style="text-align: center; color: var(--danger);">Failed to load history.</p>';
  }
}

// ---------------------------------------------------------------------------
// 12. Voice Dictation
// ---------------------------------------------------------------------------
function initSpeechRecognition() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) return;

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
    alert('Voice input is not supported in this browser.');
    return;
  }
  const btn = document.getElementById('micBtn');
  btn.classList.add('listening');
  state.recognition.start();
}

function toggleModalVoice() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    alert('Voice input is not supported in this browser.');
    return;
  }
  const recog = new SpeechRecognition();
  const btn = document.getElementById('modalMicBtn');
  btn.innerText = '🔴';
  recog.onresult = (e) => {
    const text = e.results[0][0].transcript;
    const input = document.getElementById('reviewNotesInput');
    input.value = input.value ? `${input.value} ${text}` : text;
    btn.innerText = '🎙️';
  };
  recog.onerror = () => { btn.innerText = '🎙️'; };
  recog.onend = () => { btn.innerText = '🎙️'; };
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

function initRateTab() {
  renderRateGenres();
  if (!state.rateGenreList.length) loadRateGenres();
  if (state.rateProfiles.length === 0) {
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
    <button class="chip ${p.id === state.rateProfileId ? 'active' : ''}"
            onclick="setRateProfile(${p.id})"
            title="${p.rating_count} rated · ${p.not_seen_count || 0} unseen">
      ${escapeHtml(p.name)}
      <span class="rate-profile-count">${p.rating_count}</span>
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
  document.getElementById('rateTypeMovie').classList.toggle('active', type === 'movie');
  document.getElementById('rateTypeTv').classList.toggle('active', type === 'tv');
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
    document.getElementById('rateProgress').innerText = 'Could not load the queue.';
  }
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
    <button class="chip ${state.rateBalanced ? 'active' : ''}"
            onclick="setRateBalanced()"
            title="Rate one film per genre before repeating — builds your profile faster">
      🌈 Balanced
    </button>`;
  // Multiple chips are OR'd (any of the chosen genres), so a multi-select shows
  // a mix. The hint keeps that legible instead of looking like a broken filter.
  const hint = state.rateGenres.length > 1
    ? `<span class="rate-genre-hint">Showing ${state.rateGenres.length} genres — clear one to narrow it</span>`
    : '';
  container.innerHTML = balancedChip + hint + list.map(entry => {
    const name = typeof entry === 'string' ? entry : entry.name;
    const count = typeof entry === 'string' ? null : entry.count;
    return `<button class="chip ${state.rateGenres.includes(name) ? 'active' : ''}"
            onclick="setRateGenre('${name}')">${name}${
      count ? `<span class="chip-count">${count}</span>` : ''}</button>`;
  }).join('');
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
    const total = state.rateCursor;
    document.getElementById('rateProgress').innerText =
      total > 0 ? `Nothing left to rate here (${total} shown).` : 'Nothing left to rate here.';
    return;
  }
  card.style.display = 'block';
  empty.style.display = 'none';

  const done = state.rateIndex;
  const total = state.rateQueue.length;
  document.getElementById('rateProgress').innerText =
    `Rating as ${me.name} · ${done + 1} of ${total}` +
    (state.rateGenres.length ? ` · ${state.rateGenres.join(', ')}` : '');

  const poster = item.poster_url
    ? `<img src="${item.poster_url}" class="rate-poster" alt="${escapeHtml(item.title)} poster"
          onerror="this.style.display='none'">`
    : '<div class="rate-poster rate-poster-placeholder">🎬</div>';

  const other = (item.co_view?.verdicts || []).find(v => v.profile_id !== state.rateProfileId);
  let otherLine = '';
  if (other) {
    if (other.status === 'not_seen') {
      otherLine = `<span class="rate-other-pill">${escapeHtml(other.name)} hasn't seen this</span>`;
    } else if (other.user_rating) {
      otherLine = `<span class="rate-other-pill">${escapeHtml(other.name)} rated ${other.user_rating}/10</span>`;
    }
  }
  const inLibrary = item.in_library
    ? '<span class="badge badge-bonus">📀 In your library</span>' : '';
  const coview = item.co_view?.tier === 'A'
    ? '<span class="badge badge-tmdb">💞 You both liked it</span>' : '';

  const meta = [];
  if (item.release_year) meta.push(item.release_year);
  if (item.runtime) meta.push(`${item.runtime} min`);
  if (item.rating) meta.push(`★ ${item.rating.toFixed(1)} TMDB`);

  card.innerHTML = `
    <div class="rate-card-body">
      ${poster}
      <div class="rate-info">
        <h4 class="rate-title">${escapeHtml(item.title)}</h4>
        <p class="rate-meta">${meta.join(' · ')}</p>
        <p class="rate-genres">${escapeHtml(item.genres || '')}</p>
        <div class="rate-pills">${inLibrary}${coview}${otherLine}</div>
        ${item.overview ? `<p class="rate-overview">${escapeHtml(item.overview.slice(0, 260))}${item.overview.length > 260 ? '…' : ''}</p>` : ''}
      </div>
    </div>

    <div class="rate-stars" id="rateStars">
      ${Array.from({ length: 10 }, (_, i) => `
        <button class="star-btn" onclick="submitRating(${i + 1})" title="${i + 1}/10">★</button>
      `).join('')}
    </div>

    <div class="rate-actions">
      <button class="btn-not-seen" onclick="submitNotSeen()">
        👁️ I haven't seen it
      </button>
      <button class="chip" onclick="skipRateItem()">Skip</button>
      <button class="chip" onclick="shuffleRatingQueue()" title="Show different films">🎲 Shuffle</button>
    </div>
    <p class="rate-hint">Keys 1–0 rate · N = I haven't seen it · S = skip</p>
  `;
}

async function submitRating(value) {
  const item = currentRateItem();
  if (!item) return;
  await postRating(item.id, { user_rating: value, status: 'seen' });
  advanceQueue();
}

async function submitNotSeen() {
  const item = currentRateItem();
  if (!item) return;
  // "Haven't seen it" is a real answer — stored with status='not_seen' and no
  // rating attached, never as a zero or a 1.
  await postRating(item.id, { status: 'not_seen' });
  advanceQueue();
}

async function postRating(itemId, payload) {
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
  } catch (err) {
    console.error('Rating failed:', err);
    document.getElementById('rateProgress').innerText = `Could not save: ${err.message}`;
  }
}

function flashRateFeedback(summary) {
  const msg = document.getElementById('rateProgress');
  if (!summary || summary.tier === 'C') return;
  if (summary.tier === 'A') {
    msg.innerText = '💞 Added — you both like this one.';
  } else if (summary.disagreement >= 3) {
    msg.innerText = `Noted — you two differ by ${summary.disagreement} points.`;
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
      <div class="section-title"><span>Both of you liked</span></div>
      <p class="rate-sub" style="margin-bottom: 8px;">
        ${data.count} film${data.count === 1 ? '' : 's'} rated ${data.min_liked}+ by both —
        this is what "watch together" recommendations are built from.
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
  const k = e.key.toLowerCase();
  if (k >= '0' && k <= '9') {
    submitRating(k === '0' ? 10 : parseInt(k, 10));
    e.preventDefault();
  } else if (k === 'n') {
    submitNotSeen();
  } else if (k === 's') {
    skipRateItem();
  }
});
