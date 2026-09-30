// Mobile-First Cinema Companion Client (static/app.js)

const state = {
  activeTab: 'discover',
  recMediaType: 'movie',    // 'movie' or 'tv'
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
  moodsData: []            // loaded from /api/moods
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
  document.getElementById('tabHistory').classList.toggle('active', tab === 'history');

  document.getElementById('discoverView').style.display = tab === 'discover' ? 'block' : 'none';
  document.getElementById('directView').style.display = tab === 'direct' ? 'block' : 'none';
  document.getElementById('historyView').style.display = tab === 'history' ? 'block' : 'none';

  if (tab === 'history') {
    loadHistory();
  }
}

// ---------------------------------------------------------------------------
// 3. Recommender: Movie / TV Toggle & Mood Picker
// ---------------------------------------------------------------------------
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
    const payload = {
      mood_id: state.activeMoodId,
      sub_mood: state.activeSubMood || null,
      media_type: state.recMediaType,
      genres: state.selectedGenres.size > 0 ? Array.from(state.selectedGenres) : null,
      era: state.era || null,
      duration: state.recMediaType === 'movie' ? (state.duration || null) : null,
      min_rating: state.minRating,
      top_n: 4,
      include_watched: state.includeWatched,
      query: query || null
    };

    const res = await fetch('/api/recommend', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    const movies = await res.json();
    renderRecommendations(movies);
  } catch (err) {
    console.error('Error fetching recommendations:', err);
    container.innerHTML = '<p style="text-align:center; color:var(--danger)">Failed to load recommendations.</p>';
  } finally {
    btn.innerHTML = `<span>✨ Find ${state.recMediaType === 'tv' ? 'TV Show' : 'Movie'} for Tonight</span>`;
    btn.disabled = false;
  }
}

function renderRecommendations(movies) {
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

  movies.forEach((m) => {
    const card = document.createElement('div');
    card.className = 'movie-card';

    const posterSrc = m.poster_url || 'https://via.placeholder.com/150x225/161722/ffffff?text=No+Poster';
    const tasteBonus = m.taste_bonus > 0 ? `<span class="badge badge-bonus">+${m.taste_bonus} Taste Boost</span>` : '';
    const queryBoost = m.query_boost > 0 ? `<span class="badge badge-query">+${m.query_boost} Match</span>` : '';
    const runtimeStr = m.runtime ? `<span class="badge badge-runtime">⏱ ${m.runtime}m</span>` : '';
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
            ${tasteBonus}
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
    container.appendChild(card);
  });
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