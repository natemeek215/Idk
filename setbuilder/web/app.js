/* Set Builder — UI.
 *
 * No build step, no framework. State lives in one object; screens re-render
 * from it. Keyboard first: this gets used quickly, in the dark.
 */

'use strict';

const state = {
  screen: 'import',
  status: null,
  tracks: [],
  arcs: [],
  set: [],              // array of track ids, in order
  undo: [],             // stack of previous set states
  suggestions: [],
  slot: null,
  playlistId: null,
  sort: { key: 'position', dir: 1 },
  filter: '',
  onlyAnalyzed: false,
  calib: null,
  customPoints: [0.3, 0.5, 0.7, 0.75, 0.9, 1.0],
  playingId: null,
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// ---------- helpers ----------

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (!response.ok) {
    let message = `${response.status}`;
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

let toastTimer = null;
function toast(message, isError = false) {
  const existing = $('.toast');
  if (existing) existing.remove();
  const element = document.createElement('div');
  element.className = 'toast' + (isError ? ' error' : '');
  element.textContent = message;
  document.body.appendChild(element);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.remove(), isError ? 6000 : 2800);
}

const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g,
  (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;',
                    '"': '&quot;', "'": '&#39;' }[character]));

const fmtBpm = (bpm) => (bpm ? bpm.toFixed(1).replace(/\.0$/, '') : '—');
const fmtEnergy = (energy) => (energy == null ? '—' : energy.toFixed(1));
const trackById = (id) => state.tracks.find((track) => track.id === id);

/** Poll a background job, calling onTick with its state, until it finishes. */
async function followJob(jobId, onTick) {
  for (;;) {
    const job = await api(`/api/jobs/${jobId}`);
    onTick(job);
    if (job.status === 'done') return job.result;
    if (job.status === 'error') throw new Error(job.error);
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
}

function setProgress(root, job) {
  const bar = $('.progress', root);
  const fill = $('.progress > div', root);
  const total = job.total || 0;
  bar.classList.toggle('indeterminate', !total);
  fill.style.width = total ? `${(100 * job.done) / total}%` : '35%';
  const now = $('.progress-line .now', root);
  const count = $('.progress-line .count', root);
  if (now) now.textContent = job.current || job.phase || '';
  if (count) count.textContent = total ? `${job.done} / ${total}` : '';
  const log = $('.log', root);
  if (log && job.messages) log.innerHTML = job.messages.map(
    (message) => escapeHtml(message)).join('<br>');
}

// ---------- navigation ----------

function show(screen) {
  state.screen = screen;
  $$('.screen').forEach((element) =>
    element.classList.toggle('active', element.id === `screen-${screen}`));
  $$('#tabs button').forEach((button) =>
    button.classList.toggle('active', button.dataset.screen === screen));
  if (screen === 'library') renderLibrary();
  if (screen === 'builder') refreshBuilder();
  if (screen === 'calibrate') loadCalibration();
  if (screen === 'export') renderExport();
  renderStatusbar();
}

$('#tabs').addEventListener('click', (event) => {
  const button = event.target.closest('button');
  if (button) show(button.dataset.screen);
});
$$('[data-goto]').forEach((button) =>
  button.addEventListener('click', () => show(button.dataset.goto)));

// ---------- status ----------

async function refreshStatus() {
  state.status = await api('/api/status');
  const { calibration, token, library, setup_complete: complete } = state.status;

  $('#scorer-badge').textContent = calibration.model_active
    ? `energy: your model (${calibration.labels} labels)`
    : `energy: heuristic${calibration.labels ? ` · ${calibration.labels}/${calibration.minimum} labels` : ''}`;

  $('#token-badge').textContent = state.status.has_credentials
    ? `tokens ${token.used}/${token.budget} · 12h`
    : 'no credentials';

  $$('#tabs button').forEach((button) => {
    if (button.dataset.screen !== 'setup' && button.dataset.screen !== 'import') {
      button.disabled = !state.status.has_credentials && state.tracks.length === 0;
    }
  });

  $('#pill-1').className = 'step-pill ' + (state.status.has_credentials ? 'done' : 'active');
  $('#pill-2').className = 'step-pill ' +
    (library.files > 0 ? 'done' : (state.status.has_credentials ? 'active' : ''));

  renderPlaylists();
  renderStatusbar();
  return complete;
}

function renderStatusbar() {
  const bits = [];
  if (state.screen === 'builder') {
    bits.push('<span class="k">1–5</span> add suggestion',
              '<span class="k">⌘Z</span> undo',
              '<span class="k">drag</span> reorder',
              '<span class="k">space</span> preview');
  } else if (state.screen === 'library') {
    bits.push('<span class="k">/</span> filter', '<span class="k">↑↓</span> move',
              '<span class="k">↵</span> play', '<span class="k">c</span> confirm match');
  } else if (state.screen === 'calibrate') {
    bits.push('<span class="k">1–0</span> rate', '<span class="k">↵</span> save',
              '<span class="k">p</span> play');
  }
  bits.push('<span class="k">1–5</span> screens (with ⌥)');
  if (state.set.length) bits.push(`${state.set.length} in set`);
  $('#statusbar').innerHTML = bits.join('&nbsp;&nbsp;·&nbsp;&nbsp;');
}

function renderPlaylists() {
  const playlists = state.status?.playlists || [];
  $('#playlist-history').hidden = playlists.length === 0;
  $('#playlist-list').innerHTML = playlists.map((playlist) => `
    <button class="folder" data-playlist="${playlist.id}">
      <span class="path">${escapeHtml(playlist.title)}</span>
      <span class="count">${playlist.tracks} tracks</span>
    </button>`).join('');

  const select = $('#library-playlist');
  select.innerHTML = '<option value="">All playlists</option>' +
    playlists.map((playlist) =>
      `<option value="${playlist.id}">${escapeHtml(playlist.title)}</option>`).join('');
  select.value = state.playlistId || '';
}

$('#playlist-list').addEventListener('click', (event) => {
  const button = event.target.closest('[data-playlist]');
  if (!button) return;
  state.playlistId = Number(button.dataset.playlist);
  loadTracks().then(() => show('library'));
});

// ---------- setup ----------

$('#save-credentials').addEventListener('click', async () => {
  const button = $('#save-credentials');
  const error = $('#credentials-error');
  error.hidden = true;
  button.disabled = true;
  button.textContent = 'Verifying…';
  try {
    await api('/api/credentials', {
      method: 'POST',
      body: { client_id: $('#client-id').value, client_secret: $('#client-secret').value },
    });
    toast('Credentials verified');
    $('#client-secret').value = '';
    await refreshStatus();
  } catch (exception) {
    error.textContent = exception.message;
    error.hidden = false;
  } finally {
    button.disabled = false;
    button.textContent = 'Save and verify';
  }
});

async function loadFolderSuggestions() {
  try {
    const { folders } = await api('/api/folders/suggest');
    const list = $('#folder-list');
    if (!folders.length) {
      list.innerHTML = `<p class="hint">
        No folders with more than 50 audio files found in ~/Music, ~/Documents
        or ~/Downloads. Type a path below instead.</p>`;
      return;
    }
    list.innerHTML = folders.map((folder) => `
      <button class="folder" data-path="${escapeHtml(folder.path)}">
        <span class="path">${escapeHtml(folder.name)}</span>
        <span class="count">${folder.count}${folder.approximate ? '+' : ''} files</span>
      </button>`).join('');
    list.querySelectorAll('.folder').forEach((element) =>
      element.addEventListener('click', () => element.classList.toggle('selected')));
    if (folders.length) list.firstElementChild.classList.add('selected');
  } catch (exception) {
    $('#folder-list').innerHTML =
      `<p class="err">Could not scan for folders: ${escapeHtml(exception.message)}</p>`;
  }
}

$('#save-folders').addEventListener('click', async () => {
  const folders = $$('#folder-list .folder.selected').map((element) => element.dataset.path);
  const manual = $('#folder-manual').value.trim();
  if (manual) folders.push(manual);
  const error = $('#folders-error');
  error.hidden = true;
  if (!folders.length) {
    error.textContent = 'Pick at least one folder, or skip.';
    error.hidden = false;
    return;
  }
  const progress = $('#scan-progress');
  progress.hidden = false;
  try {
    const { job_id: jobId } = await api('/api/folders', { method: 'POST', body: { folders } });
    const result = await followJob(jobId, (job) => setProgress(progress, job));
    toast(`${result.files} files indexed · ${result.with_tag_bpm} with a BPM tag`);
    await refreshStatus();
    show('import');
  } catch (exception) {
    error.textContent = exception.message;
    error.hidden = false;
  } finally {
    progress.hidden = true;
  }
});

$('#skip-folders').addEventListener('click', () => show('import'));

$('#rescan-library').addEventListener('click', async () => {
  try {
    const { job_id: jobId } = await api('/api/rescan', { method: 'POST' });
    toast('Rescanning…');
    const result = await followJob(jobId, () => {});
    toast(`${result.files} files indexed`);
    await refreshStatus();
  } catch (exception) { toast(exception.message, true); }
});

// ---------- import ----------

$('#do-import').addEventListener('click', doImport);
$('#playlist-url').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') doImport();
});

async function doImport() {
  const url = $('#playlist-url').value.trim();
  const error = $('#import-error');
  const progress = $('#import-progress');
  const summary = $('#import-summary');
  error.hidden = true;
  summary.hidden = true;
  if (!url) { error.textContent = 'Paste a playlist URL first.'; error.hidden = false; return; }

  $('#do-import').disabled = true;
  progress.hidden = false;
  try {
    const { job_id: jobId } = await api('/api/import', { method: 'POST', body: { url } });
    const result = await followJob(jobId, (job) => {
      $('#import-phase').textContent = job.phase || 'Working…';
      setProgress(progress, job);
    });
    progress.hidden = true;
    summary.hidden = false;
    $('#summary-text').textContent = result.headline;
    state.playlistId = result.playlist?.id ?? null;
    await loadTracks();
    await refreshStatus();

    const calibration = state.status.calibration;
    $('#offer-calibrate').hidden = calibration.model_active;
  } catch (exception) {
    progress.hidden = true;
    error.textContent = exception.message;
    error.hidden = false;
  } finally {
    $('#do-import').disabled = false;
  }
}

$('#offer-calibrate').addEventListener('click', () => show('calibrate'));

// ---------- library ----------

async function loadTracks() {
  const query = state.playlistId ? `?playlist_id=${state.playlistId}` : '';
  const data = await api(`/api/tracks${query}`);
  state.tracks = data.tracks;
  state.counts = data.counts;
  if (state.screen === 'library') renderLibrary();
  return data;
}

function visibleTracks() {
  const needle = state.filter.toLowerCase();
  let rows = state.tracks.filter((track) => {
    if (state.onlyAnalyzed && !track.analyzed) return false;
    if (!needle) return true;
    return `${track.title} ${track.artist} ${track.camelot || ''}`
      .toLowerCase().includes(needle);
  });
  const { key, dir } = state.sort;
  rows = rows.slice().sort((left, right) => {
    const a = left[key], b = right[key];
    if (a == null && b == null) return 0;
    if (a == null) return 1;          // unknowns always sink
    if (b == null) return -1;
    if (typeof a === 'number' && typeof b === 'number') return (a - b) * dir;
    return String(a).localeCompare(String(b)) * dir;
  });
  return rows;
}

function renderLibrary() {
  const rows = visibleTracks();
  const counts = state.counts || {};
  $('#library-counts').textContent =
    `${rows.length} shown · ${counts.analyzed || 0} analysed · ` +
    `${counts.preview || 0} preview · ${counts.blocked || 0} not streamable` +
    (counts.possible ? ` · ${counts.possible} to confirm` : '');

  $$('#library-table th').forEach((th) => {
    const arrow = th.dataset.sort === state.sort.key
      ? `<span class="arrow">${state.sort.dir > 0 ? '↑' : '↓'}</span>` : '';
    th.innerHTML = th.textContent.replace(/[↑↓]/g, '').trim() + arrow;
  });

  $('#library-table tbody').innerHTML = rows.map((track, index) => {
    const blocked = track.access === 'blocked';
    const warn = track.warnings.length
      ? `<span class="warn-icon" title="${escapeHtml(track.warnings.join('\n'))}">⚠</span>`
      : '';
    const energyBar = track.energy != null
      ? `<span class="energy-bar"><i style="width:${(track.energy / 10) * 100}%"></i></span>`
      : '';
    return `<tr data-id="${track.id}" data-index="${index}"
                class="${blocked ? 'dim' : ''} ${track.id === state.playingId ? 'playing' : ''}">
      <td class="num">${(track.position ?? 0) + 1}</td>
      <td class="title">${escapeHtml(track.title)}${warn}</td>
      <td class="artist">${escapeHtml(track.artist || '')}</td>
      <td class="num">${fmtBpm(track.bpm)}</td>
      <td>${escapeHtml(track.camelot || '—')}</td>
      <td class="num">${fmtEnergy(track.energy)}${energyBar}</td>
      <td><span class="badge ${track.badge.replace(/ /g, '-')}">${escapeHtml(track.badge)}</span></td>
      <td>${track.match_state === 'possible'
            ? `<button class="btn small" data-confirm="${track.id}">confirm?</button>` : ''}</td>
    </tr>`;
  }).join('') || `<tr><td colspan="8" class="empty">
      Nothing here yet — import a playlist.</td></tr>`;
}

$('#library-table').addEventListener('click', (event) => {
  const confirmButton = event.target.closest('[data-confirm]');
  if (confirmButton) { openMatchDialog(Number(confirmButton.dataset.confirm)); return; }
  const row = event.target.closest('tr[data-id]');
  if (row) play(Number(row.dataset.id));
});

$('#library-table thead').addEventListener('click', (event) => {
  const th = event.target.closest('th[data-sort]');
  if (!th) return;
  const key = th.dataset.sort;
  state.sort = { key, dir: state.sort.key === key ? -state.sort.dir : 1 };
  renderLibrary();
});

$('#library-search').addEventListener('input', (event) => {
  state.filter = event.target.value;
  renderLibrary();
});
$('#only-analyzed').addEventListener('change', (event) => {
  state.onlyAnalyzed = event.target.checked;
  renderLibrary();
});
$('#library-playlist').addEventListener('change', (event) => {
  state.playlistId = event.target.value ? Number(event.target.value) : null;
  loadTracks();
});

// ---------- match confirmation ----------

let pendingMatchId = null;

async function openMatchDialog(trackId) {
  pendingMatchId = trackId;
  const track = trackById(trackId);
  $('#match-title').textContent = track.title;
  $('#match-sub').textContent =
    `Fuzzy match scored ${track.match_score}% — between 70 and 87, so it needs your eye.`;
  $('#match-candidates').innerHTML = '<p class="hint">Looking…</p>';
  $('#match-dialog').showModal();

  const { candidates } = await api(`/api/tracks/${trackId}/candidates`);
  $('#match-candidates').innerHTML = candidates.length ? candidates.map((candidate) => `
    <button class="folder" data-path="${escapeHtml(candidate.path)}">
      <span>
        <div class="path">${escapeHtml(candidate.artist || '')} — ${escapeHtml(candidate.title || '')}</div>
        <div class="hint" style="margin:0">${escapeHtml(candidate.path)}</div>
      </span>
      <span class="count">${candidate.score}%</span>
    </button>`).join('') : '<p class="hint">No candidates in the scanned library.</p>';
}

$('#match-candidates').addEventListener('click', async (event) => {
  const button = event.target.closest('[data-path]');
  if (!button) return;
  await confirmMatch({ track_id: pendingMatchId, path: button.dataset.path });
});
$('#match-none').addEventListener('click', () =>
  confirmMatch({ track_id: pendingMatchId, reject: true }));
$('#match-cancel').addEventListener('click', () => $('#match-dialog').close());

async function confirmMatch(body) {
  $('#match-dialog').close();
  try {
    const { job_id: jobId } = await api('/api/tracks/confirm', { method: 'POST', body });
    toast('Re-analysing…');
    await followJob(jobId, () => {});
    await loadTracks();
    toast('Updated');
  } catch (exception) { toast(exception.message, true); }
}

// ---------- playback ----------

const player = $('#player');

function play(trackId) {
  const track = trackById(trackId);
  if (!track) return;
  if (!track.playable) { toast('No audio for this track — add the file to your library', true); return; }
  if (state.playingId === trackId && !player.paused) { player.pause(); state.playingId = null; }
  else {
    player.src = `/api/tracks/${trackId}/audio`;
    player.play().catch(() => toast('Could not play that file', true));
    state.playingId = trackId;
  }
  if (state.screen === 'library') renderLibrary();
}

// ---------- arcs ----------

async function loadArcs() {
  const data = await api('/api/arcs');
  state.arcs = data.arcs;
  const select = $('#arc-select');
  const current = select.value || state.status?.settings?.arc || data.default;
  select.innerHTML = state.arcs.map((arc) =>
    `<option value="${escapeHtml(arc.key)}">${escapeHtml(arc.name)}</option>`).join('');
  select.value = state.arcs.some((arc) => arc.key === current) ? current : data.default;
}

// ---------- set builder ----------

function builderParams() {
  return {
    set_track_ids: state.set,
    arc: $('#arc-select').value,
    set_length: Number($('#set-length').value) || 20,
    bpm_tolerance: Number($('#bpm-tolerance').value) / 100,
    harmonic_only: $('#harmonic-only').checked,
    playlist_id: state.playlistId,
    limit: 5,
  };
}

let refreshToken = 0;
async function refreshBuilder() {
  const token = ++refreshToken;
  const params = builderParams();
  try {
    const [suggest, plot] = await Promise.all([
      api('/api/suggest', { method: 'POST', body: params }),
      api('/api/set/plot', { method: 'POST', body: params }),
    ]);
    if (token !== refreshToken) return;   // a newer request already landed
    state.suggestions = suggest.suggestions;
    state.slot = suggest;
    renderSuggestions(suggest);
    renderSetlist(plot);
    drawChart(plot);
  } catch (exception) {
    if (token === refreshToken) toast(exception.message, true);
  }
}

function renderSuggestions(data) {
  $('#slot-number').textContent = `Slot ${data.slot}`;
  $('#slot-target').textContent =
    `arc wants energy ${data.target_energy.toFixed(1)}` +
    (data.beyond_set_length ? ' · past your target length' : '');
  $('#considered').textContent = `${data.considered} tracks considered`;

  if (!data.suggestions.length) {
    $('#suggestions').innerHTML = `<p class="empty">
      Nothing fits. Widen the BPM tolerance, or turn off "harmonic only".</p>`;
    return;
  }

  $('#suggestions').innerHTML = data.suggestions.map((track, index) => {
    const caution = /clash|stretch|hard/.test(track.verdict);
    return `<button class="sug" data-id="${track.id}" data-rank="${index + 1}">
      <div class="top">
        <span class="rank">${index + 1}</span>
        <span class="name">${escapeHtml(track.title)}</span>
        <span class="score">${(track.score * 100).toFixed(0)}</span>
      </div>
      <div class="who">${escapeHtml(track.artist || 'unknown artist')}</div>
      <div class="reason">${escapeHtml(track.reason)}</div>
      <div class="verdict ${caution ? 'caution' : ''}">— ${escapeHtml(track.verdict)}</div>
    </button>`;
  }).join('');
}

$('#suggestions').addEventListener('click', (event) => {
  const button = event.target.closest('.sug');
  if (button) addToSet(Number(button.dataset.id));
});

function addToSet(trackId) {
  state.undo.push(state.set.slice());
  state.set.push(trackId);
  refreshBuilder();
  renderStatusbar();
}

function undo() {
  if (!state.undo.length) { toast('Nothing to undo'); return; }
  state.set = state.undo.pop();
  refreshBuilder();
  renderStatusbar();
  toast('Undone');
}

function renderSetlist(plot) {
  const list = $('#setlist');
  const targets = plot.target;
  $('#setlist-empty').hidden = state.set.length > 0;

  list.innerHTML = state.set.map((trackId, index) => {
    const track = trackById(trackId) || {};
    const target = targets[index]?.energy;
    let drift = '', driftClass = 'on';
    if (track.energy != null && target != null) {
      const difference = track.energy - target;
      driftClass = difference > 0.7 ? 'over' : difference < -0.7 ? 'under' : 'on';
      drift = `${difference >= 0 ? '+' : ''}${difference.toFixed(1)}`;
    }
    return `<li draggable="true" data-index="${index}" data-id="${trackId}">
      <span class="slot">${index + 1}</span>
      <span class="name"><b>${escapeHtml(track.title || '?')}</b>
        <span>· ${escapeHtml(track.artist || '')}</span></span>
      <span class="meta">${fmtBpm(track.bpm)} · ${escapeHtml(track.camelot || '—')}
        · ${fmtEnergy(track.energy)}
        <span class="drift ${driftClass}">${drift}</span></span>
    </li>`;
  }).join('');

  const drifts = state.set.map((trackId, index) => {
    const track = trackById(trackId);
    const target = targets[index]?.energy;
    return (track?.energy != null && target != null) ? track.energy - target : null;
  }).filter((value) => value != null);

  $('#drift-summary').textContent = drifts.length
    ? `mean drift ${(drifts.reduce((a, b) => a + b, 0) / drifts.length).toFixed(2)}`
    : '';

  enableDragReorder(list);
}

function enableDragReorder(list) {
  let dragIndex = null;
  list.querySelectorAll('li').forEach((item) => {
    item.addEventListener('dragstart', () => {
      dragIndex = Number(item.dataset.index);
      item.classList.add('dragging');
    });
    item.addEventListener('dragend', () => item.classList.remove('dragging'));
    item.addEventListener('dragover', (event) => {
      event.preventDefault();
      item.classList.add('drag-over');
    });
    item.addEventListener('dragleave', () => item.classList.remove('drag-over'));
    item.addEventListener('drop', (event) => {
      event.preventDefault();
      item.classList.remove('drag-over');
      const dropIndex = Number(item.dataset.index);
      if (dragIndex == null || dragIndex === dropIndex) return;
      state.undo.push(state.set.slice());
      const [moved] = state.set.splice(dragIndex, 1);
      state.set.splice(dropIndex, 0, moved);
      refreshBuilder();
    });
    item.addEventListener('click', () => play(Number(item.dataset.id)));
  });
}

// ---------- chart ----------

function drawChart(plot) {
  const canvas = $('#chart');
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth, height = canvas.clientHeight || 210;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  const context = canvas.getContext('2d');
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);

  const padding = { left: 26, right: 10, top: 10, bottom: 18 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const slots = Math.max(plot.target.length, 2);
  const xAt = (slot) => padding.left + (plotWidth * (slot - 1)) / (slots - 1);
  const yAt = (energy) => padding.top + plotHeight * (1 - (energy - 1) / 9);

  const styles = getComputedStyle(document.documentElement);
  const colour = (name) => styles.getPropertyValue(name).trim();

  // grid
  context.strokeStyle = colour('--line-soft');
  context.fillStyle = colour('--text-faint');
  context.font = '10px ui-monospace, monospace';
  context.lineWidth = 1;
  for (let energy = 1; energy <= 10; energy += 3) {
    const y = yAt(energy);
    context.beginPath();
    context.moveTo(padding.left, y);
    context.lineTo(width - padding.right, y);
    context.stroke();
    context.fillText(String(energy), 6, y + 3);
  }

  // target arc
  context.strokeStyle = colour('--arc');
  context.lineWidth = 1.5;
  context.setLineDash([4, 3]);
  context.beginPath();
  plot.target.forEach((point, index) => {
    const x = xAt(point.slot), y = yAt(point.energy);
    index ? context.lineTo(x, y) : context.moveTo(x, y);
  });
  context.stroke();
  context.setLineDash([]);

  // actual set
  const actual = plot.actual.filter((point) => point.energy != null);
  if (actual.length) {
    context.strokeStyle = colour('--accent');
    context.lineWidth = 2;
    context.beginPath();
    actual.forEach((point, index) => {
      const x = xAt(point.slot), y = yAt(point.energy);
      index ? context.lineTo(x, y) : context.moveTo(x, y);
    });
    context.stroke();

    actual.forEach((point) => {
      context.beginPath();
      context.arc(xAt(point.slot), yAt(point.energy), 3, 0, Math.PI * 2);
      // A hollow ring means the energy came from a 30s preview: don't let a
      // low-confidence number look like a solid one.
      if (point.confidence === 'preview') {
        context.strokeStyle = colour('--warn');
        context.lineWidth = 1.5;
        context.stroke();
      } else {
        context.fillStyle = colour('--accent');
        context.fill();
      }
    });
  }

  // next slot marker
  const nextSlot = actual.length + 1;
  if (nextSlot <= slots) {
    context.strokeStyle = colour('--info');
    context.globalAlpha = 0.5;
    context.beginPath();
    context.moveTo(xAt(nextSlot), padding.top);
    context.lineTo(xAt(nextSlot), height - padding.bottom);
    context.stroke();
    context.globalAlpha = 1;
  }

  context.fillStyle = colour('--text-faint');
  context.fillText('1', padding.left - 2, height - 5);
  context.fillText(String(slots), width - padding.right - 10, height - 5);
}

// builder controls
['#arc-select', '#set-length', '#harmonic-only'].forEach((selector) =>
  $(selector).addEventListener('change', () => {
    saveSettings();
    refreshBuilder();
  }));

$('#bpm-tolerance').addEventListener('input', (event) => {
  $('#tol-val').textContent = `${event.target.value}%`;
  refreshBuilder();           // re-ranks live as you drag
});

$('#clear-set').addEventListener('click', () => {
  if (!state.set.length) return;
  state.undo.push(state.set.slice());
  state.set = [];
  refreshBuilder();
});

function saveSettings() {
  api('/api/settings', {
    method: 'POST',
    body: {
      arc: $('#arc-select').value,
      set_length: Number($('#set-length').value) || 20,
      bpm_tolerance: Number($('#bpm-tolerance').value) / 100,
      harmonic_only: $('#harmonic-only').checked,
    },
  }).catch(() => {});
}

// ---------- custom arc editor ----------

const arcDialog = $('#arc-dialog');
$('#edit-arc').addEventListener('click', () => { arcDialog.showModal(); drawArcEditor(); });
$('#arc-cancel').addEventListener('click', () => arcDialog.close());

function arcEditorGeometry() {
  const canvas = $('#arc-editor');
  return { canvas, width: canvas.clientWidth, height: canvas.clientHeight || 170,
           padding: 14 };
}

function drawArcEditor() {
  const { canvas, width, height, padding } = arcEditorGeometry();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = width * ratio;
  canvas.height = height * ratio;
  const context = canvas.getContext('2d');
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);

  const styles = getComputedStyle(document.documentElement);
  const colour = (name) => styles.getPropertyValue(name).trim();
  const xAt = (index) => padding + ((width - 2 * padding) * index) / (state.customPoints.length - 1);
  const yAt = (value) => padding + (height - 2 * padding) * (1 - value);

  context.strokeStyle = colour('--line');
  for (let index = 0; index <= 4; index++) {
    const y = padding + ((height - 2 * padding) * index) / 4;
    context.beginPath(); context.moveTo(padding, y);
    context.lineTo(width - padding, y); context.stroke();
  }

  // The curve, sampled through the same interpolation the backend uses.
  context.strokeStyle = colour('--arc');
  context.lineWidth = 2;
  context.beginPath();
  for (let step = 0; step <= 100; step++) {
    const position = step / 100;
    const value = hermiteEven(state.customPoints, position);
    const x = padding + (width - 2 * padding) * position;
    step ? context.lineTo(x, yAt(value)) : context.moveTo(x, yAt(value));
  }
  context.stroke();

  state.customPoints.forEach((value, index) => {
    context.beginPath();
    context.arc(xAt(index), yAt(value), 6, 0, Math.PI * 2);
    context.fillStyle = colour('--arc');
    context.fill();
    context.strokeStyle = colour('--bg');
    context.lineWidth = 2;
    context.stroke();
  });
}

/* Mirrors setbuilder/arcs.py: cubic Hermite with Catmull-Rom tangents. */
function hermiteEven(values, position) {
  const count = values.length;
  if (count < 2) return values[0] ?? 0;
  const span = Math.min(Math.max(position, 0), 1) * (count - 1);
  const index = Math.min(Math.floor(span), count - 2);
  const t = span - index;
  const y0 = values[index], y1 = values[index + 1];
  const tangent = (i) => {
    const previous = values[Math.max(i - 1, 0)];
    const following = values[Math.min(i + 1, count - 1)];
    const run = (Math.min(i + 1, count - 1) - Math.max(i - 1, 0));
    return run > 0 ? (following - previous) / run : 0;
  };
  const m0 = tangent(index), m1 = tangent(index + 1);
  const t2 = t * t, t3 = t2 * t;
  const value = (2 * t3 - 3 * t2 + 1) * y0 + (t3 - 2 * t2 + t) * m0
              + (-2 * t3 + 3 * t2) * y1 + (t3 - t2) * m1;
  return Math.min(1, Math.max(0, value));
}

(function bindArcEditor() {
  const canvas = $('#arc-editor');
  let dragging = null;
  const pointFromEvent = (event) => {
    const { width, height, padding } = arcEditorGeometry();
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left, y = event.clientY - rect.top;
    const index = Math.round(((x - padding) / (width - 2 * padding)) *
                             (state.customPoints.length - 1));
    const value = 1 - (y - padding) / (height - 2 * padding);
    return { index: Math.min(Math.max(index, 0), state.customPoints.length - 1),
             value: Math.min(Math.max(value, 0), 1) };
  };
  canvas.addEventListener('pointerdown', (event) => {
    dragging = pointFromEvent(event).index;
    canvas.setPointerCapture(event.pointerId);
    const { value } = pointFromEvent(event);
    state.customPoints[dragging] = value;
    drawArcEditor();
  });
  canvas.addEventListener('pointermove', (event) => {
    if (dragging == null) return;
    state.customPoints[dragging] = pointFromEvent(event).value;
    drawArcEditor();
  });
  canvas.addEventListener('pointerup', () => { dragging = null; });
})();

$('#arc-save').addEventListener('click', async () => {
  const name = $('#arc-name').value.trim();
  if (!name) { toast('Give the arc a name', true); return; }
  try {
    await api('/api/arcs/custom', { method: 'POST', body: { name, points: state.customPoints } });
    await loadArcs();
    $('#arc-select').value = `custom:${name}`;
    arcDialog.close();
    saveSettings();
    refreshBuilder();
    toast(`Saved "${name}"`);
  } catch (exception) { toast(exception.message, true); }
});

$('#arc-use').addEventListener('click', () => {
  const key = `points:${state.customPoints.map((value) => value.toFixed(3)).join(',')}`;
  const select = $('#arc-select');
  let option = select.querySelector('option[data-temp]');
  if (!option) {
    option = document.createElement('option');
    option.dataset.temp = '1';
    option.textContent = 'Custom (unsaved)';
    select.appendChild(option);
  }
  option.value = key;
  select.value = key;
  arcDialog.close();
  refreshBuilder();
});

// ---------- calibration ----------

async function loadCalibration() {
  const data = await api('/api/calibration/next?limit=1');
  state.calib = data;
  const track = data.tracks[0];

  const percent = Math.min(100, (data.labels / data.target) * 100);
  $('#calib-progress').textContent =
    `${data.labels} of ${data.target} rated · model takes over at ${data.minimum}`;
  $('#calib-card .progress > div').style.width = `${percent}%`;

  $('#model-status').innerHTML = data.model_active
    ? `Your model is scoring the library — fitted on ${data.trained_labels} labels` +
      (data.r2 != null ? `, cross-validated R² ${data.r2}.` : '.')
    : `Using the built-in heuristic. Rate ${Math.max(0, data.minimum - data.labels)} ` +
      `more track${data.minimum - data.labels === 1 ? '' : 's'} and your model takes over.`;

  if (!track) {
    $('#calib-title').textContent = 'Nothing left to rate';
    $('#calib-artist').textContent = 'Import more tracks to keep going.';
    $('#calib-current').textContent = '';
    return;
  }
  $('#calib-title').textContent = track.title;
  $('#calib-artist').textContent = track.artist || '';
  $('#calib-current').textContent =
    `current estimate ${fmtEnergy(track.energy)} (${track.energy_source})` +
    (track.confidence === 'preview' ? ' · from a 30s preview' : '');
  setRating(Math.round(track.energy ?? 5));
}

function setRating(value) {
  $('#rating').value = value;
  $('#rating-value').textContent = Number(value).toFixed(1).replace(/\.0$/, '');
}
$('#rating').addEventListener('input', (event) => setRating(event.target.value));

$('#calib-save').addEventListener('click', async () => {
  const track = state.calib?.tracks?.[0];
  if (!track) return;
  try {
    const result = await api('/api/calibration/label', {
      method: 'POST',
      body: { cache_key: track.cache_key, rating: Number($('#rating').value) },
    });
    if (result.refit?.fitted) {
      toast(`Model refitted on ${result.refit.n_labels} labels — it is scoring your library now`);
      await loadTracks();
    }
    await loadCalibration();
    await refreshStatus();
  } catch (exception) { toast(exception.message, true); }
});

$('#calib-skip').addEventListener('click', loadCalibration);
$('#calib-play').addEventListener('click', () => {
  const track = state.calib?.tracks?.[0];
  if (track) play(track.id);
});
$('#refit').addEventListener('click', async () => {
  const { result } = await api('/api/calibration/fit', { method: 'POST' });
  toast(result.fitted
    ? `Fitted on ${result.n_labels} labels (R² ${result.r2})`
    : result.reason, !result.fitted);
  await loadTracks();
  await loadCalibration();
  await refreshStatus();
});
$('#calib-reset').addEventListener('click', async () => {
  if (!confirm('Delete every label and the fitted model?')) return;
  await api('/api/calibration/labels', { method: 'DELETE' });
  await loadTracks();
  await loadCalibration();
  await refreshStatus();
  toast('Labels cleared — back to the heuristic');
});

// ---------- export ----------

function renderExport() {
  $('#export-count').textContent =
    `${state.set.length} track${state.set.length === 1 ? '' : 's'}`;
  const lines = state.set.map((trackId, index) => {
    const track = trackById(trackId) || {};
    return `${String(index + 1).padStart(2)}. ${track.artist || ''} - ${track.title || ''}` +
           `  —  ${fmtBpm(track.bpm)} BPM · ${track.camelot || '--'} · ` +
           `energy ${fmtEnergy(track.energy)}` +
           (track.confidence === 'preview' ? '   (energy estimated from 30s preview)' : '');
  });
  $('#export-preview').textContent = lines.join('\n') || 'Nothing in the set yet.';
}

async function download(format) {
  if (!state.set.length) { toast('The set is empty', true); return; }
  const response = await fetch('/api/export', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      track_ids: state.set, name: $('#export-name').value || 'Set',
      arc: $('#arc-select').value, format,
    }),
  });
  if (!response.ok) { toast('Export failed', true); return; }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${($('#export-name').value || 'Set').replace(/\s+/g, '-')}` +
                  `.${format === 'm3u' ? 'm3u8' : 'txt'}`;
  link.click();
  URL.revokeObjectURL(url);
}

$('#export-m3u').addEventListener('click', () => download('m3u'));
$('#export-txt').addEventListener('click', () => download('txt'));
$('#copy-txt').addEventListener('click', async () => {
  await navigator.clipboard.writeText($('#export-preview').textContent);
  toast('Copied');
});

// ---------- keyboard ----------

document.addEventListener('keydown', (event) => {
  const inField = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName);

  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'z') {
    event.preventDefault(); undo(); return;
  }
  if (event.altKey && /^[1-5]$/.test(event.key)) {
    event.preventDefault();
    show(['import', 'library', 'builder', 'calibrate', 'export'][Number(event.key) - 1]);
    return;
  }
  if (inField) return;

  if (state.screen === 'builder') {
    if (/^[1-5]$/.test(event.key)) {
      const suggestion = state.suggestions[Number(event.key) - 1];
      if (suggestion) { event.preventDefault(); addToSet(suggestion.id); }
    }
    if (event.key === ' ') {
      const suggestion = state.suggestions[0];
      if (suggestion) { event.preventDefault(); play(suggestion.id); }
    }
  }

  if (state.screen === 'library') {
    if (event.key === '/') { event.preventDefault(); $('#library-search').focus(); return; }
    const rows = $$('#library-table tbody tr[data-id]');
    const current = rows.findIndex((row) => row.classList.contains('cursor'));
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const next = Math.min(Math.max(current + (event.key === 'ArrowDown' ? 1 : -1), 0),
                            rows.length - 1);
      rows.forEach((row) => row.classList.remove('cursor'));
      if (rows[next]) {
        rows[next].classList.add('cursor');
        rows[next].scrollIntoView({ block: 'nearest' });
      }
    }
    if (event.key === 'Enter' && rows[current]) play(Number(rows[current].dataset.id));
    if (event.key === 'c' && rows[current]) openMatchDialog(Number(rows[current].dataset.id));
  }

  if (state.screen === 'calibrate') {
    if (/^[0-9]$/.test(event.key)) {
      event.preventDefault();
      setRating(event.key === '0' ? 10 : Number(event.key));
    }
    if (event.key === 'Enter') { event.preventDefault(); $('#calib-save').click(); }
    if (event.key === 'p') { event.preventDefault(); $('#calib-play').click(); }
  }
});

window.addEventListener('resize', () => {
  if (state.screen === 'builder' && state.slot) refreshBuilder();
});

// ---------- boot ----------

(async function boot() {
  try {
    const complete = await refreshStatus();
    await Promise.all([loadArcs(), loadTracks()]);

    const settings = state.status.settings || {};
    if (settings.arc) $('#arc-select').value = settings.arc;
    if (settings.set_length) $('#set-length').value = settings.set_length;
    if (settings.bpm_tolerance) {
      $('#bpm-tolerance').value = Math.round(settings.bpm_tolerance * 100);
      $('#tol-val').textContent = `${Math.round(settings.bpm_tolerance * 100)}%`;
    }
    $('#harmonic-only').checked = !!settings.harmonic_only;

    if (!complete) { loadFolderSuggestions(); show('setup'); }
    else if (state.tracks.length) show('builder');
    else show('import');
  } catch (exception) {
    toast(`Could not reach the backend: ${exception.message}`, true);
    show('setup');
  }
})();
