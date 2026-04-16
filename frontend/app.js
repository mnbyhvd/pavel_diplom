'use strict';
/* ═══════════════════════════════════════════════════════════
   WAVEMAP — SPA + Spotify Web Playback SDK
═══════════════════════════════════════════════════════════ */

// ── State ─────────────────────────────────────────────────────
const S = {
  page:               'home',
  homeData:           null,
  spotifyApiBlocked:  false,
  allTracks:     [],
  moodChart:     null,
  featuresMeta:  null,   // from /api/features
  algoScatter:   null,   // Chart.js instance for algo scatter
  algoRadar:     null,   // Chart.js instance for algo radar
  algoBar:       null,   // Chart.js instance for algo bar
  discAxisX:     'valence',
  discAxisY:     'energy',
  algoAxisX:     'valence',
  algoAxisY:     'energy',
  algoData:      null,   // last explain response
  auth: {
    authenticated: false,
    accessToken:   null,
    product:       null,
    displayName:   null,
    avatar:        null,
  },
  sdk: {
    player:   null,
    deviceId: null,
    ready:    false,
  },
  searchTimer:  null,
  algoTimer:    null,
  genre: { name: null, page: 1, total: 0, loading: false },
};

const GENRE_EMOJIS = {
  pop:'🎤',rock:'🎸',jazz:'🎷',classical:'🎻','hip-hop':'🎤',
  electronic:'🎛',dance:'💃',metal:'🤘',blues:'🎺',country:'🤠',
  reggae:'🌿',folk:'🪕',soul:'❤️',funk:'🕺',punk:'⚡',
  ambient:'🌙',techno:'🤖',house:'🏠',indie:'🎵',alternative:'🔮',
  'r-n-b':'🎶',latin:'🌶️',edm:'🔊',acoustic:'🎸',piano:'🎹',
  trance:'🌀',grunge:'😤',emo:'🖤',gospel:'🙏',opera:'🎭',
};

// ── API ───────────────────────────────────────────────────────

async function api(url, opts = {}) {
  const r = await fetch(url, { credentials: 'include', ...opts });
  if (!r.ok) {
    const e = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(e.detail || `HTTP ${r.status}`);
  }
  return r.json();
}

// ── Router ────────────────────────────────────────────────────

function router() {
  const hash = location.hash.slice(1) || 'home';

  // Handle auth callbacks
  if (hash === 'auth_success') { history.replaceState(null,'','#home'); checkAuth(); router(); return; }
  if (hash === 'auth_error')   { history.replaceState(null,'','#home'); showToast('Ошибка авторизации Spotify'); router(); return; }

  document.querySelectorAll('.page').forEach(p => p.classList.add('hidden'));

  if (hash.startsWith('genre/')) {
    const g = decodeURIComponent(hash.slice(6));
    showPage('genre');
    loadGenrePage(g);
  } else {
    const valid = ['home','search','discover','algorithm'];
    const p = valid.includes(hash) ? hash : 'home';
    S.page = p;
    showPage(p);
    if (p === 'home')      loadHome();
    if (p === 'search')    initSearch();
    if (p === 'discover')  initDiscover();
    if (p === 'algorithm') initAlgorithm();
  }

  document.querySelectorAll('.nav-link').forEach(a => {
    a.classList.toggle('active', a.dataset.page === S.page);
  });
}

function showPage(name) {
  const el = document.getElementById(`page-${name}`);
  if (el) el.classList.remove('hidden');
}

// ── Auth ──────────────────────────────────────────────────────

async function checkAuth() {
  try {
    const data = await api('/auth/me');
    S.auth.authenticated = data.authenticated;
    if (data.authenticated) {
      S.auth.accessToken = data.access_token;

      // Server may be geo-blocked by Spotify — fetch user profile directly from browser
      if (!data.product || !data.display_name) {
        try {
          const resp = await fetch('https://api.spotify.com/v1/me', {
            headers: { Authorization: `Bearer ${data.access_token}` },
          });
          if (resp.ok) {
            const u = await resp.json();
            data.product      = u.product      || data.product;
            data.display_name = u.display_name || data.display_name;
            data.avatar       = u.images?.[0]?.url || data.avatar;
            data.country      = u.country      || data.country;
          }
        } catch (_) {}
      }

      S.auth.product     = data.product;
      S.auth.displayName = data.display_name;
      S.auth.avatar      = data.avatar;
      renderAuthUI(data);
      if (data.product === 'premium') initSpotifySDK();
      // Enrich any already-rendered tracks with album art
      setTimeout(() => {
        const tids = [...document.querySelectorAll('[data-tid]')].map(el => el.dataset.tid);
        if (tids.length) {
          fetchSpotifyTracks(tids).then(artMap => {
            // Patch S.homeData tracks if loaded
            if (S.homeData) {
              [...(S.homeData.featured||[]), ...(S.homeData.mood_picks||[])].forEach(t => {
                const info = artMap[t.track_id];
                if (info?.album_image) t.album_image = info.album_image;
              });
            }
            applyArtToDOM(artMap);
          });
        }
      }, 500);
    }
  } catch (_) {}
}

function renderAuthUI(data) {
  document.getElementById('spotifyLoginBtn').style.display = 'none';
  const profile = document.getElementById('userProfile');
  profile.classList.remove('hidden');

  const av = document.getElementById('userAvatar');
  if (data.avatar) { av.src = data.avatar; av.style.display = 'block'; }
  else { av.style.display = 'none'; }

  document.getElementById('userName').textContent = data.display_name || 'Пользователь';
  document.getElementById('userPlan').textContent = data.product === 'premium' ? '★ Premium' : 'Free';
}

document.getElementById('spotifyLoginBtn').addEventListener('click', () => {
  location.href = '/auth/login';
});
document.getElementById('logoutBtn').addEventListener('click', async () => {
  await api('/auth/logout', { method: 'POST' });
  location.reload();
});

// ── Spotify Web Playback SDK ──────────────────────────────────

window.onSpotifyWebPlaybackSDKReady = () => {
  if (!S.auth.authenticated || S.auth.product !== 'premium') return;
  initSpotifySDK();
};

function initSpotifySDK() {
  if (S.sdk.ready || !window.Spotify) return;

  const player = new Spotify.Player({
    name: 'WAVEMAP Player',
    getOAuthToken: async (cb) => {
      // Refresh token from backend if needed
      try {
        const d = await api('/auth/token');
        cb(d.access_token);
      } catch (_) {
        cb(S.auth.accessToken);
      }
    },
    volume: 0.7,
  });

  player.addListener('ready', async ({ device_id }) => {
    S.sdk.deviceId = device_id;
    S.sdk.ready    = true;
    S.sdk.player   = player;
    console.log('[SDK] Ready, device_id:', device_id);
    updatePlaybackBadge('Spotify Premium · полный трек');
    // Transfer playback to this device so Spotify recognises it as active
    try {
      const token = await getSpotifyToken();
      await fetch('https://api.spotify.com/v1/me/player', {
        method: 'PUT',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_ids: [device_id], play: false }),
      });
    } catch (_) {}
  });

  player.addListener('not_ready', () => { S.sdk.ready = false; });

  player.addListener('player_state_changed', (state) => {
    if (!state) return;
    const paused = state.paused;
    updatePlayBtn(!paused);
    setVisualizer(!paused);

    // Sync progress
    if (!paused) {
      startSDKProgressSync(player);
    } else {
      stopSDKProgressSync();
    }
  });

  player.addListener('initialization_error', ({ message }) => console.error('[SDK] init:', message));
  player.addListener('authentication_error', ({ message }) => {
    console.error('[SDK] auth:', message);
    S.spotifyApiBlocked = true;
    showSpotifyBlockedBanner();
  });
  player.addListener('account_error',        ({ message }) => {
    console.error('[SDK] account:', message);
    showToast('Требуется Spotify Premium для полного воспроизведения');
  });

  player.connect();
  S.sdk.player = player;
}

let sdkSyncInterval = null;
function startSDKProgressSync(player) {
  stopSDKProgressSync();
  sdkSyncInterval = setInterval(async () => {
    const state = await player.getCurrentState();
    if (!state) return;
    const { position, duration } = state;
    if (duration > 0) {
      const pct = (position / duration) * 100;
      document.getElementById('progressFill').style.width = pct + '%';
      document.getElementById('progressBar').value = pct;
      document.getElementById('timeCur').textContent = fmt(position / 1000);
      document.getElementById('timeTot').textContent = fmt(duration / 1000);
    }
  }, 500);
}
function stopSDKProgressSync() {
  if (sdkSyncInterval) clearInterval(sdkSyncInterval);
  sdkSyncInterval = null;
}

// ── Spotify API helpers (client-side, uses user token) ────────

async function getSpotifyToken() {
  try {
    const d = await api('/auth/token');
    S.auth.accessToken = d.access_token;
    return d.access_token;
  } catch (_) {
    return S.auth.accessToken;
  }
}

// Batch-fetch track info (album art, external_url, preview_url) from Spotify API.
// Returns map: { trackId: { album_image, external_url, preview_url } }
async function fetchSpotifyTracks(trackIds) {
  if (!S.auth.authenticated || !trackIds.length) return {};
  const result = {};
  try {
    for (let i = 0; i < trackIds.length; i += 50) {
      const batch = trackIds.slice(i, i + 50).join(',');
      // Use server-side proxy to avoid client-side geo-blocks on Spotify API
      const resp = await fetch(`/api/spotify/tracks?ids=${batch}`);
      if (!resp.ok) {
        console.warn('[Art] tracks proxy error', resp.status, await resp.text().catch(()=>''));
        break;
      }
      const data = await resp.json();
      (data.tracks || []).forEach(t => {
        if (!t?.id) return;
        result[t.id] = {
          album_image:  t.album?.images?.[1]?.url || t.album?.images?.[0]?.url || null,
          external_url: t.external_urls?.spotify || null,
          preview_url:  t.preview_url || null,
        };
      });
    }
  } catch (e) {
    console.warn('[Art] fetchSpotifyTracks error:', e);
  }
  return result;
}

function showSpotifyBlockedBanner() {
  if (document.getElementById('spotifyBlockedBanner')) return;
  const banner = document.createElement('div');
  banner.id = 'spotifyBlockedBanner';
  banner.style.cssText = `
    position:fixed; top:0; left:0; right:0; z-index:9999;
    background:#1a1a2e; border-bottom:1px solid #c8ff4740;
    padding:10px 20px; display:flex; align-items:center; gap:12px;
    font-size:13px; color:#aaa;
  `;
  banner.innerHTML = `
    <span style="color:#c8ff47; font-size:16px">⚠</span>
    <span>Spotify API заблокирован в вашем регионе. Обложки и воспроизведение недоступны без VPN.</span>
    <button onclick="this.parentNode.remove()" style="margin-left:auto;background:none;border:none;color:#666;cursor:pointer;font-size:16px">✕</button>
  `;
  document.body.prepend(banner);
}

// Update visible track cards and list items with fetched art.
function applyArtToDOM(artMap) {
  if (!Object.keys(artMap).length) return;
  // Scroll-row cards
  document.querySelectorAll('.track-card[data-tid]').forEach(card => {
    const info = artMap[card.dataset.tid];
    if (!info?.album_image) return;
    let img = card.querySelector('.card-art');
    if (!img) {
      img = document.createElement('img');
      img.className = 'card-art';
      card.querySelector('.card-art-ph')?.replaceWith(img);
      if (!img.parentNode) card.prepend(img);
    }
    if (!img.src) { img.src = info.album_image; img.loading = 'lazy'; }
  });
  // Track-list items
  document.querySelectorAll('.track-item[data-tid]').forEach(item => {
    const info = artMap[item.dataset.tid];
    if (!info?.album_image) return;
    const ph = item.querySelector('.ti-art-ph');
    if (ph) {
      const img = document.createElement('img');
      img.className = 'ti-art';
      img.src = info.album_image;
      img.loading = 'lazy';
      ph.replaceWith(img);
    }
  });
}

async function enrichWithArt(tracks) {
  if (!S.auth.authenticated || !tracks.length) return;
  const missing = tracks.filter(t => !t.album_image).map(t => t.track_id);
  if (!missing.length) return;
  const artMap = await fetchSpotifyTracks(missing);
  // Patch track objects so player also gets art
  tracks.forEach(t => {
    const info = artMap[t.track_id];
    if (info?.album_image) t.album_image = info.album_image;
    if (info?.external_url && !t.external_url) t.external_url = info.external_url;
    if (info?.preview_url  && !t.preview_url)  t.preview_url  = info.preview_url;
  });
  applyArtToDOM(artMap);
}

async function sdkPlay(trackId) {
  if (!S.sdk.ready || !S.sdk.deviceId) return false;
  try {
    const token = await getSpotifyToken();
    const resp = await fetch(`https://api.spotify.com/v1/me/player/play?device_id=${S.sdk.deviceId}`, {
      method: 'PUT',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ uris: [`spotify:track:${trackId}`] }),
    });
    // Spotify returns 204 No Content on success
    if (!resp.ok && resp.status !== 204) {
      const body = await resp.text().catch(() => '');
      console.error('[SDK] play failed:', resp.status, body);
      return false;
    }
    updatePlaybackBadge('Spotify Premium · полный трек');
    return true;
  } catch (e) {
    console.error('[SDK] play error:', e);
    return false;
  }
}

// ── Player Controller ─────────────────────────────────────────

const Player = (() => {
  const audio = new Audio();
  audio.volume = 0.7;

  let track    = null;
  let queue    = [];
  let queueIdx = 0;
  let useSDK   = false;
  let fadeTimer = null;

  audio.addEventListener('timeupdate', () => {
    if (useSDK) return;
    if (!audio.duration) return;
    const pct = (audio.currentTime / audio.duration) * 100;
    document.getElementById('progressFill').style.width = pct + '%';
    document.getElementById('progressBar').value = pct;
    document.getElementById('timeCur').textContent = fmt(audio.currentTime);
    document.getElementById('timeTot').textContent = fmt(audio.duration);
  });
  audio.addEventListener('loadedmetadata', () => {
    if (!useSDK) document.getElementById('timeTot').textContent = fmt(audio.duration);
  });
  audio.addEventListener('ended',  () => next());
  audio.addEventListener('play',   () => { updatePlayBtn(true);  setVisualizer(true);  document.getElementById('player-bar').classList.add('is-playing'); });
  audio.addEventListener('pause',  () => { updatePlayBtn(false); setVisualizer(false); document.getElementById('player-bar').classList.remove('is-playing'); });

  async function load(t) {
    track = t;
    updateUI(t);
    markPlaying(t.track_id);

    // Fetch album art + external_url from Spotify API if missing
    if (!t.album_image && S.auth.authenticated) {
      fetchSpotifyTracks([t.track_id]).then(artMap => {
        const info = artMap[t.track_id];
        if (!info) return;
        if (info.album_image) {
          track.album_image = info.album_image;
          const art = document.getElementById('plArt');
          const ph  = document.querySelector('.pl-art-ph');
          art.src = info.album_image;
          art.classList.add('show');
          ph?.classList.add('hidden');
          updateAmbient(info.album_image);
        }
        if (info.external_url && !track.external_url) {
          track.external_url = info.external_url;
          const ext = document.getElementById('plSpotify');
          ext.href = info.external_url;
          ext.classList.remove('hidden');
        }
        if (info.preview_url && !track.preview_url) {
          track.preview_url = info.preview_url;
        }
      }).catch(() => {});
    }

    // Disable play button while loading
    document.getElementById('btnPlay').disabled = true;
    document.getElementById('playbackBadge').classList.add('hidden');

    // Try SDK (Premium) first
    if (S.sdk.ready) {
      useSDK = true;
      audio.pause();
      const ok = await sdkPlay(t.track_id);
      if (ok) {
        document.getElementById('btnPlay').disabled = false;
        updatePlayBtn(true);
        setVisualizer(true);
        document.getElementById('player-bar').classList.add('is-playing');
        // fetch queue
        loadQueue(t.track_id);
        return;
      }
      useSDK = false;
    }

    // Fallback: 30s preview
    try {
      const d = await api(`/api/player/preview/${t.track_id}`);
      if (d.preview_url) {
        useSDK = false;
        loadAudio(d.preview_url);
        updatePlaybackBadge('preview · 30 сек');
        document.getElementById('btnPlay').disabled = false;
      } else {
        document.getElementById('btnPlay').disabled = true;
        showToast('Предпросмотр недоступен для этого трека');
      }
    } catch (_) {
      document.getElementById('btnPlay').disabled = true;
    }

    loadQueue(t.track_id);
  }

  async function loadQueue(trackId) {
    try {
      const d = await api(`/api/recommend/${trackId}?n=10`);
      queue    = d.recommendations || [];
      queueIdx = 0;
      renderQueue();
    } catch (_) {}
  }

  function loadAudio(url) {
    if (!audio.paused) {
      fadeOut(() => { audio.src = url; audio.load(); audio.play().then(fadeIn).catch(() => {}); });
    } else {
      audio.src = url;
      audio.load();
      audio.play().catch(() => {});
    }
  }

  async function toggle() {
    if (useSDK && S.sdk.player) {
      await S.sdk.player.togglePlay();
    } else {
      audio.paused ? audio.play().catch(() => {}) : audio.pause();
    }
  }

  async function next() {
    if (!queue.length) return;
    queueIdx = (queueIdx + 1) % queue.length;
    await load(queue[queueIdx]);
  }

  async function prev() {
    const pos = useSDK
      ? (await S.sdk.player?.getCurrentState())?.position / 1000
      : audio.currentTime;

    if ((pos || 0) > 3) {
      if (useSDK) S.sdk.player?.seek(0);
      else audio.currentTime = 0;
      return;
    }
    if (queueIdx > 0) { queueIdx--; await load(queue[queueIdx]); }
    else {
      if (useSDK) S.sdk.player?.seek(0);
      else audio.currentTime = 0;
    }
  }

  async function seek(pct) {
    if (useSDK && S.sdk.player) {
      const state = await S.sdk.player.getCurrentState();
      if (state) S.sdk.player.seek((pct / 100) * state.duration);
    } else if (audio.duration) {
      audio.currentTime = (pct / 100) * audio.duration;
    }
  }

  function setVolume(v) {
    audio.volume = v / 100;
    if (S.sdk.player) S.sdk.player.setVolume(v / 100);
    const hi   = document.getElementById('volIconHigh');
    const mute = document.getElementById('volIconMute');
    if (v == 0) { hi.classList.add('hidden'); mute.classList.remove('hidden'); }
    else        { mute.classList.add('hidden'); hi.classList.remove('hidden'); }
  }

  function fadeOut(cb) {
    clearInterval(fadeTimer);
    const step = Math.max(0.02, audio.volume / 12);
    fadeTimer = setInterval(() => {
      audio.volume = Math.max(0, audio.volume - step);
      if (audio.volume < 0.01) { clearInterval(fadeTimer); audio.volume = 0; cb(); }
    }, 25);
  }

  function fadeIn() {
    const target = parseInt(document.getElementById('volBar').value) / 100;
    audio.volume = 0;
    fadeTimer = setInterval(() => {
      audio.volume = Math.min(target, audio.volume + 0.04);
      if (audio.volume >= target) { clearInterval(fadeTimer); }
    }, 25);
  }

  function updateUI(t) {
    document.getElementById('plTitle').textContent  = t.track_name;
    document.getElementById('plArtist').textContent = t.artist_name;

    const art = document.getElementById('plArt');
    const ph  = document.querySelector('.pl-art-ph');
    if (t.album_image) {
      art.src = t.album_image;
      art.classList.add('show');
      ph.classList.add('hidden');
      updateAmbient(t.album_image);
    } else {
      art.classList.remove('show');
      ph.classList.remove('hidden');
    }

    const ext = document.getElementById('plSpotify');
    if (t.external_url) { ext.href = t.external_url; ext.classList.remove('hidden'); }
    else ext.classList.add('hidden');

    // Reset progress
    document.getElementById('progressFill').style.width = '0%';
    document.getElementById('progressBar').value = '0';
    document.getElementById('timeCur').textContent = '0:00';
    document.getElementById('timeTot').textContent = '—';
  }

  function renderQueue() {
    const list = document.getElementById('queueList');
    list.innerHTML = '';
    queue.forEach((t, i) => {
      const el = document.createElement('div');
      el.className = 'qi' + (i === queueIdx ? ' qi-active' : '');
      el.innerHTML = `
        <span class="qi-num">${i + 1}</span>
        <div class="qi-info">
          <div class="qi-name">${esc(t.track_name)}</div>
          <div class="qi-artist">${esc(t.artist_name)}</div>
        </div>`;
      el.addEventListener('click', () => { queueIdx = i; load(queue[i]); });
      list.appendChild(el);
    });
  }

  return { load, toggle, next, prev, seek, setVolume, get track() { return track; } };
})();

// ── Ambient color from album art ──────────────────────────────
function updateAmbient(imgUrl) {
  const orb1 = document.querySelector('.orb-1');
  const orb2 = document.querySelector('.orb-2');
  // Simple heuristic: just shift hue on existing orbs
  orb1.style.transition = 'all 2s ease';
  orb2.style.transition = 'all 2s ease';
}

// ── HOME ──────────────────────────────────────────────────────
async function loadHome() {
  if (S.homeData) return;
  document.getElementById('featured-row').innerHTML = '<div class="spinner"></div>';
  try {
    const d = await api('/api/home');
    S.homeData = d;
    renderSidebarGenres(d.genres || []);
    renderScrollRow(document.getElementById('featured-row'), d.featured || []);
    renderGenreGrid(document.getElementById('genre-grid'), d.genres || []);
    if (d.mood_point) {
      const { valence: v, energy: e } = d.mood_point;
      document.getElementById('mood-label').textContent = `V=${v} · E=${e}`;
    }
    renderTrackList(document.getElementById('mood-picks'), d.mood_picks || []);
    // Enrich album art from Spotify API (client-side, non-blocking)
    const allHomeTracks = [...(d.featured || []), ...(d.mood_picks || [])];
    enrichWithArt(allHomeTracks);
  } catch (err) {
    document.getElementById('featured-row').innerHTML = `<p class="empty-hint">${esc(err.message)}</p>`;
  }
}

function renderSidebarGenres(genres) {
  const c = document.getElementById('sidebar-genres');
  c.innerHTML = '';
  genres.slice(0, 28).forEach(g => {
    const a = document.createElement('a');
    a.className = 'sg-link';
    a.href = `#genre/${encodeURIComponent(g.name)}`;
    a.innerHTML = `<span class="sg-dot" style="background:${g.color}"></span>${esc(g.name)}`;
    c.appendChild(a);
  });
}

function renderScrollRow(container, tracks) {
  container.innerHTML = '';
  tracks.forEach(t => {
    const card = document.createElement('div');
    card.className = 'track-card';
    card.dataset.tid = t.track_id;
    const artHtml = t.album_image
      ? `<img class="card-art" src="${esc(t.album_image)}" loading="lazy" alt="" />`
      : `<div class="card-art-ph">♪</div>`;
    card.innerHTML = `
      ${artHtml}
      <div class="card-name">${esc(t.track_name)}</div>
      <div class="card-artist">${esc(t.artist_name)}</div>
      <button class="card-play">▶</button>`;
    card.addEventListener('click', () => Player.load(t));
    container.appendChild(card);
  });
}

function renderGenreGrid(container, genres) {
  container.innerHTML = '';
  genres.slice(0, 24).forEach(g => {
    const emoji = GENRE_EMOJIS[g.name] || '🎵';
    const a = document.createElement('a');
    a.className = 'genre-card';
    a.href = `#genre/${encodeURIComponent(g.name)}`;
    a.style.background = `linear-gradient(135deg, ${g.color}dd 0%, ${g.color}66 100%)`;
    a.innerHTML = `
      <div class="genre-card-name">${esc(g.name)}</div>
      ${g.count ? `<div class="genre-card-count">${g.count.toLocaleString()} треков</div>` : ''}
      <span class="genre-card-emoji">${emoji}</span>`;
    container.appendChild(a);
  });
}

// ── TRACK LIST ────────────────────────────────────────────────
function renderTrackList(container, tracks, opts = {}) {
  if (!opts.append) container.innerHTML = '';
  if (!tracks.length && !opts.append) {
    container.innerHTML = '<p class="empty-hint">Ничего не найдено</p>';
    return;
  }
  const start = opts.startIndex || 0;
  tracks.forEach((t, i) => {
    container.appendChild(makeTrackItem(t, i + start + 1, opts));
  });
}

function makeTrackItem(t, num, opts = {}) {
  const el = document.createElement('div');
  el.className = 'track-item';
  el.dataset.tid = t.track_id;
  if (Player.track?.track_id === t.track_id) el.classList.add('is-playing');

  const artHtml = t.album_image
    ? `<img class="ti-art" src="${esc(t.album_image)}" loading="lazy" alt="" />`
    : `<div class="ti-art-ph">♪</div>`;

  let badges = '';
  if (t.valence != null) badges += `<span class="ti-badge badge-v">V ${t.valence}</span>`;
  if (t.energy  != null) badges += `<span class="ti-badge badge-e">E ${t.energy}</span>`;
  if (opts.showDist && t.distance != null)
    badges += `<span class="ti-badge badge-dist">d=${t.distance}</span>`;

  el.innerHTML = `
    <span class="ti-num">${num}</span>
    <span class="ti-play-ico"><svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg></span>
    ${artHtml}
    <div class="ti-meta">
      <div class="ti-name">${esc(t.track_name)}</div>
      <div class="ti-artist">${esc(t.artist_name)}</div>
      ${t.track_genre ? `<span class="ti-genre">${esc(t.track_genre)}</span>` : ''}
    </div>
    <div class="ti-right">${badges}</div>`;

  el.addEventListener('click', () => Player.load(t));
  return el;
}

function markPlaying(trackId) {
  document.querySelectorAll('.track-item.is-playing').forEach(el => el.classList.remove('is-playing'));
  document.querySelectorAll(`.track-item[data-tid="${CSS.escape(trackId)}"]`)
    .forEach(el => el.classList.add('is-playing'));
}

// ── SEARCH ────────────────────────────────────────────────────
function initSearch() {
  const inp = document.getElementById('search-input');
  if (inp.dataset.bound) { inp.focus(); return; }
  inp.dataset.bound = '1';
  inp.addEventListener('input', e => {
    const q = e.target.value.trim();
    clearTimeout(S.searchTimer);
    if (q.length < 2) {
      document.getElementById('search-results').innerHTML = '<p class="empty-hint">Начните вводить — поиск в реальном времени</p>';
      return;
    }
    S.searchTimer = setTimeout(() => doSearch(q), 280);
  });
  inp.focus();
  // ⌘K shortcut
  document.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
      e.preventDefault();
      if (S.page !== 'search') { location.hash = '#search'; } else inp.focus();
    }
  });
}

async function doSearch(q) {
  const c = document.getElementById('search-results');
  c.innerHTML = '<div class="spinner"></div>';
  try {
    const d = await api(`/api/search/local?q=${encodeURIComponent(q)}&limit=40`);
    renderTrackList(c, d.results || []);
    enrichWithArt(d.results || []);
  } catch (err) {
    c.innerHTML = `<p class="empty-hint">${esc(err.message)}</p>`;
  }
}

// ── DISCOVER ──────────────────────────────────────────────────
async function initDiscover() {
  if (S.moodChart) return;
  document.getElementById('discList').innerHTML = '<div class="spinner"></div>';

  // Ensure features meta loaded
  if (!S.featuresMeta) await loadFeaturesMeta();

  if (!S.allTracks.length) {
    try {
      const d = await api('/api/tracks?limit=5000');
      S.allTracks = d.tracks || [];
    } catch (err) {
      document.getElementById('discList').innerHTML = `<p class="empty-hint">${esc(err.message)}</p>`;
      return;
    }
  }

  // Populate axis selectors
  populateAxisSelects('discAxisX', 'discAxisY', S.discAxisX, S.discAxisY);

  buildMoodChart(S.discAxisX, S.discAxisY);

  // Axis change handlers
  document.getElementById('discAxisX').addEventListener('change', e => {
    S.discAxisX = e.target.value;
    rebuildMoodChart();
  });
  document.getElementById('discAxisY').addEventListener('change', e => {
    S.discAxisY = e.target.value;
    rebuildMoodChart();
  });

  const vs = document.getElementById('valSlider');
  const es = document.getElementById('engSlider');
  vs.addEventListener('input', () => { document.getElementById('valVal').textContent = (+vs.value).toFixed(2); });
  es.addEventListener('input', () => { document.getElementById('engVal').textContent = (+es.value).toFixed(2); });
  document.getElementById('moodBtn').addEventListener('click', () => {
    triggerMood(+vs.value, +es.value);
  });
  document.getElementById('discList').innerHTML = '<p class="empty-hint">Выберите точку на карте</p>';
}

function populateAxisSelects(xId, yId, defaultX, defaultY) {
  if (!S.featuresMeta) return;
  const features = S.featuresMeta;
  [xId, yId].forEach((id, idx) => {
    const sel = document.getElementById(id);
    if (!sel || sel.dataset.populated) return;
    sel.innerHTML = features.map(f =>
      `<option value="${f.key}" ${f.key === (idx===0?defaultX:defaultY) ? 'selected' : ''}>${f.label}</option>`
    ).join('');
    sel.dataset.populated = '1';
  });
}

function rebuildMoodChart() {
  if (S.moodChart) { S.moodChart.destroy(); S.moodChart = null; }
  buildMoodChart(S.discAxisX, S.discAxisY);
  // Update axis labels
  const fm = S.featuresMeta || [];
  const xf = fm.find(f => f.key === S.discAxisX);
  const yf = fm.find(f => f.key === S.discAxisY);
  document.getElementById('discAxisXLabel').textContent = (xf?.label || S.discAxisX) + ' →';
  document.getElementById('discAxisYLabel').textContent = '↑ ' + (yf?.label || S.discAxisY);
}

function buildMoodChart(xAxis = 'valence', yAxis = 'energy') {
  const ctx = document.getElementById('moodChart').getContext('2d');
  const fm = S.featuresMeta || [];
  const xf = fm.find(f => f.key === xAxis);
  const yf = fm.find(f => f.key === yAxis);

  S.moodChart = new Chart(ctx, {
    type: 'scatter',
    data: {
      datasets: [
        {
          label: 'Все треки',
          data: S.allTracks.map(t => ({ x: t[xAxis] ?? 0, y: t[yAxis] ?? 0, track: t })),
          backgroundColor: 'rgba(136,136,170,0.25)',
          pointRadius: 2.5, pointHoverRadius: 6, order: 4,
        },
        {
          label: 'Рекомендации',
          data: [],
          backgroundColor: 'rgba(71,212,255,0.9)',
          pointRadius: 7, pointHoverRadius: 10, order: 2,
        },
        {
          label: 'Выбранный',
          data: [],
          backgroundColor: '#c8ff47',
          pointRadius: 10, pointHoverRadius: 13, order: 1,
        },
        {
          label: 'Настроение',
          data: [],
          backgroundColor: '#ffb830',
          pointStyle: 'crossRot', pointRadius: 13, order: 0,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: {
        x: {
          title: { display: true, text: xf?.label || xAxis, color: '#44445a', font: { size: 11, family: 'DM Sans' } },
          grid: { color: 'rgba(255,255,255,0.04)' },
          ticks: { color: '#44445a', maxTicksLimit: 6 },
        },
        y: {
          title: { display: true, text: yf?.label || yAxis, color: '#44445a', font: { size: 11, family: 'DM Sans' } },
          grid: { color: 'rgba(255,255,255,0.04)' },
          ticks: { color: '#44445a', maxTicksLimit: 6 },
        },
      },
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      onClick(event, elements) {
        if (elements.length) {
          const pt = S.moodChart.data.datasets[elements[0].datasetIndex].data[elements[0].index];
          if (pt?.track) { selectChartTrack(pt.track); return; }
        }
        // Only trigger mood if valence/energy axes are active
        if (xAxis === 'valence' && yAxis === 'energy') {
          const wrap = document.getElementById('chartWrap');
          const rect = wrap.getBoundingClientRect();
          const xPx = event.native.clientX - rect.left;
          const yPx = event.native.clientY - rect.top;
          const v = Math.max(0, Math.min(1, S.moodChart.scales.x.getValueForPixel(xPx)));
          const e = Math.max(0, Math.min(1, S.moodChart.scales.y.getValueForPixel(yPx)));
          triggerMood(v, e);
        }
      },
      onHover(event, elements) {
        const tip = document.getElementById('chartTooltip');
        if (!elements.length) { tip.classList.add('hidden'); return; }
        const pt = S.moodChart.data.datasets[elements[0].datasetIndex].data[elements[0].index];
        if (!pt?.track) { tip.classList.add('hidden'); return; }
        const wrap = document.getElementById('chartWrap');
        const rect = wrap.getBoundingClientRect();
        tip.style.left = (event.native.clientX - rect.left + 12) + 'px';
        tip.style.top  = (event.native.clientY - rect.top  - 10) + 'px';
        const t = pt.track;
        const xVal = (t[xAxis] ?? 0).toFixed(3);
        const yVal = (t[yAxis] ?? 0).toFixed(3);
        const xColor = xf?.color || '#a78bfa';
        const yColor = yf?.color || '#f87171';
        tip.innerHTML = `
          <div class="ctip-name">${esc(t.track_name)}</div>
          <div class="ctip-sub">${esc(t.artist_name)}</div>
          <div class="ctip-sub"><span style="color:${xColor}">${xf?.label||xAxis}: ${xVal}</span> · <span style="color:${yColor}">${yf?.label||yAxis}: ${yVal}</span></div>`;
        tip.classList.remove('hidden');
      },
    },
  });
}

async function selectChartTrack(t) {
  const xA = S.discAxisX, yA = S.discAxisY;
  setChartHighlight([], [{ x: t[xA] ?? 0, y: t[yA] ?? 0, track: t }], null);
  const d = await api(`/api/recommend/${t.track_id}?n=8`);
  const recs = d.recommendations || [];
  setChartHighlight(
    recs.map(r => ({ x: r[xA] ?? 0, y: r[yA] ?? 0, track: r })),
    [{ x: t[xA] ?? 0, y: t[yA] ?? 0, track: t }],
    null
  );
  document.getElementById('discTitle').textContent = `Похожие на «${t.track_name}»`;
  renderTrackList(document.getElementById('discList'), recs, { showDist: true });
  Player.load(t);
}

async function triggerMood(v, e) {
  document.getElementById('valSlider').value = v;
  document.getElementById('engSlider').value = e;
  document.getElementById('valVal').textContent = v.toFixed(2);
  document.getElementById('engVal').textContent = e.toFixed(2);

  const xA = S.discAxisX, yA = S.discAxisY;
  const moodPt = xA === 'valence' && yA === 'energy' ? { x: v, y: e } : null;
  setChartHighlight([], [], moodPt);

  const d = await api(`/api/recommend/mood?valence=${v}&energy=${e}&n=8`);
  const recs = d.recommendations || [];
  setChartHighlight(recs.map(r => ({ x: r[xA] ?? 0, y: r[yA] ?? 0, track: r })), [], moodPt);
  document.getElementById('discTitle').textContent = `V=${v.toFixed(2)} · E=${e.toFixed(2)}`;
  renderTrackList(document.getElementById('discList'), recs, { showDist: true });
}

function setChartHighlight(recs, selected, mood) {
  S.moodChart.data.datasets[1].data = recs;
  S.moodChart.data.datasets[2].data = selected;
  S.moodChart.data.datasets[3].data = mood ? [mood] : [];
  S.moodChart.update('none');
}

// ── FEATURES META ─────────────────────────────────────────────
async function loadFeaturesMeta() {
  if (S.featuresMeta) return S.featuresMeta;
  try {
    const d = await api('/api/features');
    S.featuresMeta = d.features || [];
  } catch (_) {
    S.featuresMeta = [];
  }
  return S.featuresMeta;
}

// ── ALGORITHM ─────────────────────────────────────────────────
async function initAlgorithm() {
  // Load features meta and render cards
  if (!S.featuresMeta) await loadFeaturesMeta();
  renderFeatureCards();

  const inp = document.getElementById('algoInput');
  if (inp.dataset.bound) return;
  inp.dataset.bound = '1';

  inp.addEventListener('input', e => {
    const q = e.target.value.trim();
    clearTimeout(S.algoTimer);
    const dd = document.getElementById('algoDropdown');
    if (q.length < 2) { dd.classList.add('hidden'); return; }
    S.algoTimer = setTimeout(async () => {
      const d = await api(`/api/search/local?q=${encodeURIComponent(q)}&limit=8`);
      renderAlgoDropdown(d.results || []);
    }, 280);
  });

  document.addEventListener('click', e => {
    if (!e.target.closest('.algo-search-section'))
      document.getElementById('algoDropdown').classList.add('hidden');
  });
}

function renderFeatureCards() {
  const grid = document.getElementById('featureCardsGrid');
  if (!grid || grid.dataset.rendered) return;
  grid.dataset.rendered = '1';
  const features = S.featuresMeta || [];
  grid.innerHTML = features.map(f => {
    const pct = Math.round(f.mean * 100);
    const weightPct = Math.min(100, Math.round((f.weight / 1.5) * 100));
    return `
    <div class="feat-card glass-panel" style="border-top-color:${f.color}40;--feature-color:${f.color}40">
      <div class="feat-card-top">
        <span class="feat-card-icon">${f.icon}</span>
        <span class="feat-card-label" style="color:${f.color}">${f.label}</span>
        <span class="feat-card-weight" title="Вес в алгоритме">w=${f.weight}</span>
      </div>
      <p class="feat-card-desc">${esc(f.description)}</p>
      <div class="feat-card-stats">
        <div class="feat-stat-row">
          <span class="feat-stat-lbl">Среднее по датасету</span>
          <div class="feat-stat-bar">
            <div class="feat-stat-fill" style="width:${pct}%;background:${f.color}"></div>
          </div>
          <span class="feat-stat-val" style="color:${f.color}">${f.mean.toFixed(3)}</span>
        </div>
        <div class="feat-stat-row">
          <span class="feat-stat-lbl">Вес алгоритма</span>
          <div class="feat-stat-bar">
            <div class="feat-stat-fill" style="width:${weightPct}%;background:rgba(200,255,71,0.7)"></div>
          </div>
          <span class="feat-stat-val" style="color:#c8ff47">${f.weight.toFixed(1)}</span>
        </div>
      </div>
      <div class="feat-card-hint">${esc(f.range_hint)}</div>
    </div>`;
  }).join('');
}

function renderAlgoDropdown(tracks) {
  const dd = document.getElementById('algoDropdown');
  dd.innerHTML = '';
  if (!tracks.length) { dd.classList.add('hidden'); return; }
  tracks.forEach(t => {
    const d = document.createElement('div');
    d.className = 'ad-item';
    d.innerHTML = `<div class="ad-name">${esc(t.track_name)}</div><div class="ad-artist">${esc(t.artist_name)}</div>`;
    d.addEventListener('click', () => {
      document.getElementById('algoInput').value = `${t.track_name} — ${t.artist_name}`;
      document.getElementById('algoDropdown').classList.add('hidden');
      loadAlgoExplain(t.track_id);
    });
    dd.appendChild(d);
  });
  dd.classList.remove('hidden');
}

async function loadAlgoExplain(id) {
  const content = document.getElementById('algoContent');
  content.classList.add('hidden');
  try {
    // Preload tracks for scatter if not already loaded
    await ensureAllTracks();
    const d = await api(`/api/recommend/explain/${id}?n=6`);
    S.algoData = d;
    renderAlgoSteps(d);
    content.classList.remove('hidden');
  } catch (err) {
    showToast('Ошибка: ' + err.message);
  }
}

function renderAlgoSteps(data) {
  const { query, algorithm, feature_stats, query_features, recommendations } = data;
  const fm = S.featuresMeta || [];

  // ── Step 1: Query card + feature bars + radar ──────────────
  const artHtml = query.album_image
    ? `<img class="qc-art" src="${esc(query.album_image)}" alt="" />`
    : `<div class="qc-art-ph">♪</div>`;
  document.getElementById('algoQueryCard').innerHTML = `
    ${artHtml}
    <div>
      <div class="qc-name">${esc(query.track_name)}</div>
      <div class="qc-artist">${esc(query.artist_name)}</div>
      ${query.track_genre ? `<span class="ti-genre">${esc(query.track_genre)}</span>` : ''}
    </div>
    <button class="btn-play-sm" onclick="Player.load(${JSON.stringify(query).replace(/"/g,'&quot;')})">▶</button>`;

  const features = algorithm.features || [];
  document.getElementById('algoFeatureBars').innerHTML = features.map(key => {
    const meta = fm.find(f => f.key === key) || { label: key, color: '#888', unit: '0–1' };
    const raw = query_features[key]?.raw ?? (query[key] ?? 0);
    // Normalize display: tempo uses 50-200 BPM range, others 0-1
    const displayPct = key === 'tempo'
      ? Math.max(0, Math.min(100, ((raw - 50) / 150) * 100))
      : Math.min(100, Math.max(0, raw * 100));
    const displayVal = key === 'tempo' ? `${Math.round(raw)} BPM` : raw.toFixed(3);
    const w = (algorithm.weights[key] || 1).toFixed(1);
    return `
    <div class="fb-row">
      <span class="fb-label">${esc(meta.label)}</span>
      <div class="fb-track">
        <div class="fb-fill" style="width:${displayPct}%;background:${meta.color}"></div>
      </div>
      <span class="fb-val" style="color:${meta.color}">${displayVal}</span>
      <span class="fb-weight">×${w}</span>
    </div>`;
  }).join('');

  // Radar chart
  buildAlgoRadar(query, features, fm, query_features);

  // ── Step 2: Normalization table ────────────────────────────
  document.getElementById('algoNormTable').innerHTML = `
    <table class="norm-table">
      <thead>
        <tr>
          <th>Признак</th><th>x (raw)</th><th>μ (mean)</th>
          <th>σ (std)</th><th>z-score</th><th>Вес w</th><th>z·√w</th>
        </tr>
      </thead>
      <tbody>
        ${features.map(key => {
          const meta = fm.find(f => f.key === key) || {};
          const qf   = query_features[key] || {};
          const fs   = feature_stats[key] || {};
          const z    = qf.z?.toFixed(3) ?? '—';
          const zw   = qf.z_weighted?.toFixed(3) ?? '—';
          const w    = (algorithm.weights[key] || 1).toFixed(2);
          return `<tr>
            <td><span class="norm-feat-dot" style="background:${meta.color||'#888'}"></span>${esc(meta.label||key)}</td>
            <td class="mono">${(qf.raw ?? 0).toFixed(4)}</td>
            <td class="mono">${(fs.scaler_mean ?? fs.mean ?? 0).toFixed(4)}</td>
            <td class="mono">${(fs.scaler_scale ?? fs.std ?? 1).toFixed(4)}</td>
            <td class="highlight">${z}</td>
            <td class="mono" style="color:#c8ff47">${w}</td>
            <td class="highlight">${zw}</td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;

  // ── Step 3: Scatter projection ─────────────────────────────
  buildAlgoScatter(query, recommendations, fm);
  populateAlgoAxisSelects(features, fm);

  // ── Step 4: Per-feature distance breakdown ─────────────────
  buildAlgoDistanceChart(recommendations, features, fm);

  // ── Step 5: KNN results ────────────────────────────────────
  const recTracks = recommendations.map(r => r.track);
  renderTrackList(document.getElementById('algoRecsList'), recTracks, { showDist: true });
}

function buildAlgoRadar(query, features, fm, query_features) {
  const canvas = document.getElementById('algoRadarChart');
  if (S.algoRadar) { S.algoRadar.destroy(); S.algoRadar = null; }

  const labels = features.map(k => {
    const meta = fm.find(f => f.key === k);
    return meta ? meta.label : k;
  });
  // Normalize all to 0-1 for radar display
  const vals = features.map(k => {
    const raw = query_features[k]?.raw ?? (query[k] ?? 0);
    if (k === 'tempo') return Math.max(0, Math.min(1, (raw - 50) / 150));
    return Math.max(0, Math.min(1, raw));
  });
  const colors = features.map(k => (fm.find(f => f.key === k)?.color || '#888'));

  S.algoRadar = new Chart(canvas.getContext('2d'), {
    type: 'radar',
    data: {
      labels,
      datasets: [{
        data: vals,
        backgroundColor: 'rgba(200,255,71,0.12)',
        borderColor: '#c8ff47',
        borderWidth: 2,
        pointBackgroundColor: colors,
        pointRadius: 5,
        pointHoverRadius: 7,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        r: {
          min: 0, max: 1,
          ticks: { display: false, stepSize: 0.25 },
          grid: { color: 'rgba(255,255,255,0.06)' },
          angleLines: { color: 'rgba(255,255,255,0.06)' },
          pointLabels: { color: '#8888aa', font: { size: 11, family: 'DM Sans' } },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: item => ` ${item.raw.toFixed(3)}`,
          },
        },
      },
    },
  });
}

function populateAlgoAxisSelects(features, fm) {
  ['algoAxisX', 'algoAxisY'].forEach((id, idx) => {
    const sel = document.getElementById(id);
    if (!sel) return;
    const cur = idx === 0 ? S.algoAxisX : S.algoAxisY;
    sel.innerHTML = features.map(k => {
      const meta = fm.find(f => f.key === k) || {};
      return `<option value="${k}" ${k === cur ? 'selected' : ''}>${meta.label || k}</option>`;
    }).join('');
    sel.dataset.populated = '1';
    sel.onchange = () => {
      if (idx === 0) S.algoAxisX = sel.value;
      else S.algoAxisY = sel.value;
      if (S.algoData) {
        buildAlgoScatter(
          S.algoData.query,
          S.algoData.recommendations,
          fm
        );
      }
    };
  });
}

async function ensureAllTracks() {
  if (S.allTracks.length) return;
  try {
    const d = await api('/api/tracks?limit=5000');
    S.allTracks = d.tracks || [];
  } catch (_) {}
}

function buildAlgoScatter(query, recs, fm) {
  if (!S.allTracks.length) return; // needs track data — caller should call ensureAllTracks first
  const canvas = document.getElementById('algoScatterChart');
  if (!canvas) return;
  if (S.algoScatter) { S.algoScatter.destroy(); S.algoScatter = null; }

  const xA = S.algoAxisX, yA = S.algoAxisY;
  const xf = fm.find(f => f.key === xA);
  const yf = fm.find(f => f.key === yA);

  const recIds = new Set(recs.map(r => r.track.track_id));

  S.algoScatter = new Chart(canvas.getContext('2d'), {
    type: 'scatter',
    data: {
      datasets: [
        {
          label: 'Все треки',
          data: S.allTracks.map(t => ({ x: t[xA] ?? 0, y: t[yA] ?? 0, track: t })),
          backgroundColor: 'rgba(100,100,140,0.2)',
          pointRadius: 2, pointHoverRadius: 5, order: 4,
        },
        {
          label: 'Рекомендации',
          data: recs.map(r => ({ x: r.track[xA] ?? 0, y: r.track[yA] ?? 0, track: r.track })),
          backgroundColor: 'rgba(71,212,255,0.9)',
          pointRadius: 8, pointHoverRadius: 11, order: 2,
        },
        {
          label: 'Запрос',
          data: [{ x: query[xA] ?? 0, y: query[yA] ?? 0, track: query }],
          backgroundColor: '#c8ff47',
          pointRadius: 11, pointHoverRadius: 14, order: 1,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: {
        x: {
          title: { display: true, text: xf?.label || xA, color: '#44445a', font: { size: 11 } },
          grid: { color: 'rgba(255,255,255,0.04)' },
          ticks: { color: '#44445a', maxTicksLimit: 6 },
        },
        y: {
          title: { display: true, text: yf?.label || yA, color: '#44445a', font: { size: 11 } },
          grid: { color: 'rgba(255,255,255,0.04)' },
          ticks: { color: '#44445a', maxTicksLimit: 6 },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: { enabled: false },
      },
      onHover(event, elements) {
        const tip = document.getElementById('algoScatterTip');
        if (!elements.length) { tip.classList.add('hidden'); return; }
        const pt = S.algoScatter.data.datasets[elements[0].datasetIndex].data[elements[0].index];
        if (!pt?.track) { tip.classList.add('hidden'); return; }
        const wrap = document.getElementById('algoScatterWrap');
        if (!wrap) return;
        const rect = wrap.getBoundingClientRect();
        tip.style.left = (event.native.clientX - rect.left + 12) + 'px';
        tip.style.top  = (event.native.clientY - rect.top  - 10) + 'px';
        const t = pt.track;
        tip.innerHTML = `
          <div class="ctip-name">${esc(t.track_name)}</div>
          <div class="ctip-sub">${esc(t.artist_name)}</div>
          <div class="ctip-sub">
            <span style="color:${xf?.color||'#888'}">${xf?.label||xA}: ${(t[xA]??0).toFixed(3)}</span> ·
            <span style="color:${yf?.color||'#888'}">${yf?.label||yA}: ${(t[yA]??0).toFixed(3)}</span>
          </div>`;
        tip.classList.remove('hidden');
      },
    },
  });
}

function buildAlgoDistanceChart(recs, features, fm) {
  const canvas = document.getElementById('algoBarChart');
  if (!canvas) return;
  if (S.algoBar) { S.algoBar.destroy(); S.algoBar = null; }

  // Stacked horizontal bar: each rec = 1 bar, stacked by feature contribution %
  const top = recs.slice(0, 6);
  const labels = top.map(r => {
    const name = r.track.track_name;
    return name.length > 20 ? name.slice(0, 19) + '…' : name;
  });

  const datasets = features.map(key => {
    const meta = fm.find(f => f.key === key) || { label: key, color: '#888' };
    return {
      label: meta.label,
      data: top.map(r => r.feature_contributions?.[key]?.pct ?? 0),
      backgroundColor: meta.color + 'cc',
      borderColor: meta.color,
      borderWidth: 0,
      borderRadius: 2,
    };
  });

  S.algoBar = new Chart(canvas.getContext('2d'), {
    type: 'bar',
    data: { labels, datasets },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: {
          stacked: true,
          max: 100,
          ticks: { color: '#44445a', font: { size: 10 }, callback: v => v + '%' },
          grid: { color: 'rgba(255,255,255,0.03)' },
        },
        y: {
          stacked: true,
          ticks: { color: '#8888aa', font: { size: 11, family: 'DM Sans' } },
          grid: { display: false },
        },
      },
      plugins: {
        legend: {
          position: 'bottom',
          labels: {
            color: '#8888aa',
            font: { size: 10, family: 'DM Sans' },
            boxWidth: 10, boxHeight: 10,
            padding: 8,
          },
        },
        tooltip: {
          callbacks: {
            label: item => ` ${item.dataset.label}: ${item.raw.toFixed(1)}%`,
          },
        },
      },
    },
  });
}

// ── GENRE PAGE ────────────────────────────────────────────────
async function loadGenrePage(genre) {
  S.page = 'genre';
  document.querySelectorAll('.nav-link').forEach(a => a.classList.remove('active'));

  const g = S.genre;
  g.name = genre; g.page = 1; g.total = 0;

  const title = document.getElementById('genreTitle');
  title.textContent = genre;
  document.getElementById('genreCount').textContent = '';
  document.getElementById('genreTrackList').innerHTML = '<div class="spinner"></div>';
  document.getElementById('genreLoadMore').classList.add('hidden');

  // Hero background
  const genreData = S.homeData?.genres?.find(x => x.name === genre);
  const color = genreData?.color || '#1db954';
  document.getElementById('genreHeroBg').style.background =
    `linear-gradient(135deg, ${color} 0%, transparent 70%)`;

  await fetchGenreTracksPage(genre, 1);
}

async function fetchGenreTracksPage(genre, page) {
  const g = S.genre;
  if (g.loading) return;
  g.loading = true;
  try {
    const d = await api(`/api/genre/${encodeURIComponent(genre)}?page=${page}&per_page=50`);
    g.total = d.total; g.page = page;
    const c = document.getElementById('genreTrackList');
    if (page === 1) {
      c.innerHTML = '';
      document.getElementById('genreCount').textContent = `${d.total.toLocaleString()} треков`;
    }
    renderTrackList(c, d.tracks, { append: true, startIndex: (page-1)*50 });
    const more = document.getElementById('genreLoadMore');
    ((page-1)*50 + d.tracks.length < g.total) ? more.classList.remove('hidden') : more.classList.add('hidden');
    enrichWithArt(d.tracks);
  } catch (err) {
    document.getElementById('genreTrackList').innerHTML = `<p class="empty-hint">${esc(err.message)}</p>`;
  } finally { g.loading = false; }
}

// ── PLAYER BINDINGS ───────────────────────────────────────────
function bindPlayer() {
  document.getElementById('btnPlay').addEventListener('click',  () => Player.toggle());
  document.getElementById('btnPrev').addEventListener('click',  () => Player.prev());
  document.getElementById('btnNext').addEventListener('click',  () => Player.next());

  const pb = document.getElementById('progressBar');
  pb.addEventListener('input', e => Player.seek(+e.target.value));

  const vb = document.getElementById('volBar');
  vb.addEventListener('input', e => Player.setVolume(+e.target.value));

  document.getElementById('volBtn').addEventListener('click', () => {
    if (+vb.value > 0) { vb.dataset.prev = vb.value; vb.value = 0; }
    else vb.value = vb.dataset.prev || 70;
    Player.setVolume(+vb.value);
  });

  document.getElementById('queueBtn').addEventListener('click', () => {
    document.getElementById('queuePanel').classList.toggle('hidden');
  });
  document.getElementById('queueClose').addEventListener('click', () => {
    document.getElementById('queuePanel').classList.add('hidden');
  });

  document.getElementById('genreLoadBtn').addEventListener('click', () => {
    const g = S.genre;
    if (g.name) fetchGenreTracksPage(g.name, g.page + 1);
  });

  // Space bar = play/pause
  document.addEventListener('keydown', e => {
    if (e.code === 'Space' && e.target === document.body) {
      e.preventDefault();
      Player.toggle();
    }
  });
}

// ── UI helpers ────────────────────────────────────────────────
function updatePlayBtn(playing) {
  document.getElementById('iconPlay').classList.toggle('hidden', playing);
  document.getElementById('iconPause').classList.toggle('hidden', !playing);
  document.getElementById('player-bar').classList.toggle('is-playing', playing);
}
function setVisualizer(active) {
  document.getElementById('visualizer').classList.toggle('active', active);
}
function updatePlaybackBadge(text) {
  const b = document.getElementById('playbackBadge');
  document.getElementById('badgeText').textContent = text;
  b.classList.remove('hidden');
}

let toastTimer = null;
function showToast(msg) {
  const t = document.getElementById('noPreviewToast');
  t.querySelector('svg + *')?.remove();
  t.appendChild(Object.assign(document.createElement('span'), { textContent: msg }));
  t.classList.remove('hidden', 'fade-out');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    t.classList.add('fade-out');
    setTimeout(() => t.classList.add('hidden'), 320);
  }, 3000);
}

function fmt(s) {
  if (!s || isNaN(s)) return '0:00';
  const m = Math.floor(s / 60), sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2,'0')}`;
}
function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Horizontal scroll rows ────────────────────────────────────
function initScrollRows() {
  // Wheel → horizontal scroll
  document.addEventListener('wheel', e => {
    const row = e.target.closest('.scroll-row');
    if (!row) return;
    e.preventDefault();
    row.scrollLeft += e.deltaY || e.deltaX;
  }, { passive: false });

  // Mouse drag to scroll
  let drag = { active: false, row: null, startX: 0, scrollX: 0 };

  document.addEventListener('mousedown', e => {
    const row = e.target.closest('.scroll-row');
    if (!row) return;
    drag = { active: true, row, startX: e.clientX, scrollX: row.scrollLeft };
    row.style.cursor = 'grabbing';
    row.style.userSelect = 'none';
  });

  document.addEventListener('mousemove', e => {
    if (!drag.active) return;
    drag.row.scrollLeft = drag.scrollX - (e.clientX - drag.startX);
  });

  document.addEventListener('mouseup', () => {
    if (!drag.active) return;
    drag.row.style.cursor = '';
    drag.row.style.userSelect = '';
    drag.active = false;
  });
}

// ── BOOT ──────────────────────────────────────────────────────
window.addEventListener('hashchange', router);
document.addEventListener('DOMContentLoaded', () => {
  bindPlayer();
  checkAuth();
  initScrollRows();
  router();
});
