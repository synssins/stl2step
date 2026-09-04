import { createViewer, parseSTL, parseEdges } from './viewer.js';

const $ = (id) => document.getElementById(id);
const el = {
  side: $('side'), tabFiles: $('tab-files'), tabHistory: $('tab-history'),
  paneFiles: $('pane-files'), paneHistory: $('pane-history'),
  drop: $('drop'), files: $('files'), picked: $('picked'), pickedEmpty: $('picked-empty'),
  hist: $('hist'), histEmpty: $('hist-empty'), histCount: $('hist-count'), maxMb: $('max-mb'), ver: $('ver'),
  gl: $('gl'), overlay: $('overlay'), overlayText: $('overlay-text'), status: $('status'), viewStep: $('view-step'),
  engineHint: $('engine-hint'), tolRow: $('tol-row'),
  btnConvert: $('btn-convert'), btnExport: $('btn-export'),
  result: $('result'), resultName: $('result-name'), stats: $('stats'), pills: $('pills'),
  warn: $('warn'), warnTitle: $('warn-title'), warnList: $('warn-list'), resultErr: $('result-err'),
};

const ENGINE_HINT = {
  verbatim: 'Byte-faithful. Flat regions merge into planar faces; curves stay faceted.',
  trueform: 'Recovers planes, cylinders and fillets as analytic faces. Cones, spheres and tori stay faceted.',
};
const ICON = {
  trash: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 4h10M6 4V2.5h4V4M4.5 4l.6 9h5.8l.6-9M6.5 7v4M9.5 7v4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
  redo: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M13 8a5 5 0 1 1-1.5-3.6M13 2v3h-3" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  cube: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1 15 5v6l-7 4-7-4V5z" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/><path d="M1 5l7 4 7-4M8 9v6" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>',
  check: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5 6.5 12 13 4.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  bang: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.5 14.5 13h-13z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/><path d="M8 6.5v3M8 11.5v.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
};

const state = {
  opts: {},
  picked: [],           // {key, file, geometry|null}
  jobs: [],
  sel: null,            // {type:'file', key} | {type:'job', id}
  view: 'import',
  shade: 'shaded',
  stepReady: false,
  pollTimer: null,
  loadSeq: 0,
};
const geomCache = new Map();   // job id -> {input, preview, edges}
const viewer = createViewer(el.gl);

// ---------- options ----------
function readStoredOpts() {
  try { return JSON.parse(localStorage.getItem('stl2step.opts') || '{}'); } catch { return {}; }
}
function storeOpts() {
  try { localStorage.setItem('stl2step.opts', JSON.stringify(state.opts)); } catch { /* private mode */ }
}
function fmtStep(key, v) {
  if (key === 'smooth_tol' && v === 0) return 'auto';
  if (key === 'weld' && v === 0) return 'exact';
  return String(v);
}
function syncOptionControls() {
  for (const seg of document.querySelectorAll('.seg[data-opt]')) {
    const key = seg.dataset.opt;
    for (const b of seg.querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.val) === String(state.opts[key]));
  }
  for (const st of document.querySelectorAll('.stepper[data-opt]')) {
    const key = st.dataset.opt, steps = st.dataset.steps.split(',').map(Number);
    const v = state.opts[key];
    const i = steps.indexOf(v);
    st.querySelector('output').textContent = fmtStep(key, v);
    st.querySelector('[data-dir="-1"]').disabled = i <= 0;
    st.querySelector('[data-dir="1"]').disabled = i >= steps.length - 1;
  }
  for (const t of document.querySelectorAll('.toggle[data-opt]')) t.setAttribute('aria-pressed', String(!!state.opts[t.dataset.opt]));
  const scale = document.querySelector('input[data-opt=scale]');
  if (document.activeElement !== scale) scale.value = state.opts.scale;
  const tf = state.opts.engine === 'trueform';
  el.engineHint.textContent = ENGINE_HINT[state.opts.engine];
  el.tolRow.style.opacity = tf ? '' : '.45';
  for (const b of el.tolRow.querySelectorAll('button')) if (!tf) b.disabled = true;
  document.querySelector('.toggle[data-opt=smooth_fillets]').disabled = !tf;
}
function setOpt(key, val) {
  state.opts[key] = val;
  storeOpts();
  syncOptionControls();
}
document.querySelectorAll('.seg[data-opt] button').forEach((b) => b.addEventListener('click', () => {
  const key = b.closest('.seg').dataset.opt;
  setOpt(key, key === 'threads' ? Number(b.dataset.val) : b.dataset.val);
}));
document.querySelectorAll('.stepper[data-opt] button').forEach((b) => b.addEventListener('click', () => {
  const st = b.closest('.stepper'), key = st.dataset.opt, steps = st.dataset.steps.split(',').map(Number);
  let i = steps.indexOf(state.opts[key]);
  if (i < 0) i = steps.findIndex((s) => s >= state.opts[key]);
  i = Math.min(steps.length - 1, Math.max(0, i + Number(b.dataset.dir)));
  setOpt(key, steps[i]);
}));
document.querySelectorAll('.toggle[data-opt]').forEach((t) => t.addEventListener('click', () => setOpt(t.dataset.opt, !state.opts[t.dataset.opt])));
document.querySelector('input[data-opt=scale]').addEventListener('change', (e) => {
  const v = Number(e.target.value);
  setOpt('scale', v > 0 && v <= 10000 ? v : 1);
});

// ---------- tabs ----------
function showTab(which) {
  const files = which === 'files';
  el.tabFiles.setAttribute('aria-selected', String(files));
  el.tabHistory.setAttribute('aria-selected', String(!files));
  el.paneFiles.hidden = !files;
  el.paneHistory.hidden = files;
}
el.tabFiles.addEventListener('click', () => showTab('files'));
el.tabHistory.addEventListener('click', () => showTab('history'));

// ---------- viewport segments ----------
document.querySelectorAll('.seg[data-key] button').forEach((b) => b.addEventListener('click', () => {
  const key = b.closest('.seg').dataset.key;
  if (key === 'view') setView(b.dataset.val); else setShade(b.dataset.val);
}));
function pressSeg(key, val) {
  for (const b of document.querySelectorAll(`.seg[data-key=${key}] button`)) b.setAttribute('aria-pressed', String(b.dataset.val === val));
}
function setShade(v) {
  state.shade = v;
  pressSeg('shade', v);
  viewer.setWire(v === 'wire');
}
function setView(v) {
  if (v === 'step' && !state.stepReady) return;
  state.view = v;
  pressSeg('view', v);
  showCurrent();
}

// ---------- file picking ----------
el.files.addEventListener('change', () => { addFiles(el.files.files); el.files.value = ''; });
for (const ev of ['dragenter', 'dragover']) document.addEventListener(ev, (e) => { e.preventDefault(); el.drop.classList.add('over'); });
for (const ev of ['dragleave', 'drop']) document.addEventListener(ev, (e) => { e.preventDefault(); if (ev === 'drop' || e.target === document.documentElement) el.drop.classList.remove('over'); });
document.addEventListener('drop', (e) => addFiles(e.dataTransfer.files));

function addFiles(list) {
  let first = null;
  for (const f of list) {
    if (!/\.stl$/i.test(f.name)) continue;
    const key = `${f.name}:${f.size}:${f.lastModified}`;
    if (state.picked.some((p) => p.key === key)) continue;
    const p = { key, file: f, geometry: null };
    state.picked.push(p);
    first ??= p;
  }
  renderPicked();
  showTab('files');
  if (first) selectFile(first.key);
}
function removeFile(key) {
  state.picked = state.picked.filter((p) => p.key !== key);
  if (state.sel?.type === 'file' && state.sel.key === key) {
    state.sel = null;
    viewer.clear();
    setStatus('Pick an STL to begin.');
    hideResult();
  }
  renderPicked();
}
function renderPicked() {
  el.picked.replaceChildren(...state.picked.map((p) => {
    const li = document.createElement('li');
    li.classList.toggle('sel', state.sel?.type === 'file' && state.sel.key === p.key);
    li.innerHTML = `<div><button type="button" class="name"></button><div class="meta"></div></div>
      <button type="button" class="icon-btn" aria-label="Remove from list">${ICON.trash}</button>`;
    li.querySelector('.name').textContent = p.file.name;
    li.querySelector('.meta').textContent = fmtBytes(p.file.size);
    li.querySelector('.name').addEventListener('click', () => selectFile(p.key));
    li.querySelector('.icon-btn').addEventListener('click', () => removeFile(p.key));
    return li;
  }));
  el.pickedEmpty.hidden = state.picked.length > 0;
  el.btnConvert.disabled = state.picked.length === 0;
  el.btnConvert.textContent = state.picked.length > 1 ? `Convert ${state.picked.length} files to STEP` : 'Convert to STEP';
}
async function selectFile(key) {
  const p = state.picked.find((x) => x.key === key);
  if (!p) return;
  state.sel = { type: 'file', key };
  state.stepReady = false;
  el.viewStep.disabled = true;
  state.view = 'import';
  pressSeg('view', 'import');
  renderPicked();
  renderHistory();
  hideResult();
  el.btnExport.setAttribute('aria-disabled', 'true');
  el.btnExport.href = '#';
  const seq = ++state.loadSeq;
  if (!p.geometry) {
    overlay('Reading mesh…');
    try {
      p.geometry = parseSTL(await p.file.arrayBuffer());
    } catch (e) {
      overlay(null);
      setStatus(`${p.file.name} — could not parse STL`);
      return;
    }
    overlay(null);
  }
  if (seq !== state.loadSeq) return;
  viewer.setModel(p.geometry, null);
  setStatus(`${p.file.name} — ${fmtInt(p.geometry.attributes.position.count / 3)} triangles`);
}

// ---------- convert ----------
el.btnConvert.addEventListener('click', async () => {
  if (!state.picked.length) return;
  const fd = new FormData();
  for (const p of state.picked) fd.append('files', p.file, p.file.name);
  for (const [k, v] of Object.entries(state.opts)) fd.append(k, String(v));
  el.btnConvert.disabled = true;
  el.btnConvert.textContent = 'Uploading…';
  try {
    const r = await fetch('/api/jobs', { method: 'POST', body: fd });
    if (!r.ok) throw new Error(await errText(r));
    const { jobs } = await r.json();
    state.picked = [];
    renderPicked();
    await refreshJobs();
    showTab('history');
    if (jobs[0]) selectJob(jobs[0]);
  } catch (e) {
    setStatus(`Upload failed: ${e.message}`);
    renderPicked();
  }
});

// ---------- jobs ----------
async function refreshJobs() {
  try {
    const r = await fetch('/api/jobs?limit=200', { cache: 'no-store' });
    if (!r.ok) return;
    const prev = new Map(state.jobs.map((j) => [j.id, j.status]));
    state.jobs = (await r.json()).jobs;
    renderHistory();
    const sel = state.sel?.type === 'job' ? state.jobs.find((j) => j.id === state.sel.id) : null;
    if (sel && prev.get(sel.id) !== sel.status && !['queued', 'running'].includes(sel.status)) selectJob(sel.id);
  } finally {
    schedulePoll();
  }
}
function schedulePoll() {
  clearTimeout(state.pollTimer);
  if (state.jobs.some((j) => j.status === 'queued' || j.status === 'running')) state.pollTimer = setTimeout(refreshJobs, 1500);
}
function renderHistory() {
  el.histCount.textContent = state.jobs.length ? String(state.jobs.length) : '';
  el.histEmpty.hidden = state.jobs.length > 0;
  el.hist.replaceChildren(...state.jobs.map((j) => {
    const li = document.createElement('li');
    li.dataset.id = j.id;
    li.classList.toggle('sel', state.sel?.type === 'job' && state.sel.id === j.id);
    const faces = facesOf(j);
    li.innerHTML = `<button type="button" class="thumb" aria-label="Open job"></button>
      <div class="text"><button type="button" class="name"></button>
        <div class="meta"><span class="dot"></span><span class="m"></span></div></div>
      <div class="btns">
        <button type="button" class="icon-btn" aria-label="Convert again with current options" title="Convert again">${ICON.redo}</button>
        <button type="button" class="icon-btn danger" aria-label="Delete job" title="Delete">${ICON.trash}</button>
      </div>`;
    const thumb = li.querySelector('.thumb');
    if (j.thumb) { const img = new Image(); img.src = `/api/jobs/${j.id}/thumb`; img.alt = ''; thumb.append(img); }
    else thumb.innerHTML = ICON.cube;
    li.querySelector('.name').textContent = j.name;
    li.querySelector('.dot').classList.add(j.status);
    li.querySelector('.m').textContent = [statusWord(j), faces != null ? `${fmtInt(faces)} faces` : null, fmtAge(j.created), j.source === 'api' ? 'api' : null].filter(Boolean).join(' · ');
    thumb.addEventListener('click', () => selectJob(j.id));
    li.querySelector('.name').addEventListener('click', () => selectJob(j.id));
    li.querySelector('[title="Convert again"]').addEventListener('click', () => reconvert(j.id));
    const del = li.querySelector('.danger');
    del.addEventListener('click', () => {
      if (del.classList.contains('armed')) { deleteJob(j.id); return; }
      del.classList.add('armed');
      del.setAttribute('aria-label', 'Click again to confirm delete');
      setTimeout(() => { del.classList.remove('armed'); del.setAttribute('aria-label', 'Delete job'); }, 2500);
    });
    return li;
  }));
}
function facesOf(j) {
  const r = j.result;
  if (!r) return null;
  return r.facesAfterSmooth ?? r.facesAfterUnify ?? null;
}
function statusWord(j) {
  return { queued: 'queued', running: 'running', done: 'done', warn: 'warnings', failed: 'failed', error: 'error' }[j.status] || j.status;
}

async function selectJob(id, keepView = false) {
  const j = state.jobs.find((x) => x.id === id);
  if (!j) return;
  state.sel = { type: 'job', id };
  renderPicked();
  renderHistory();
  renderResult(j);
  const hasStep = ['done', 'warn'].includes(j.status);
  state.stepReady = hasStep;
  el.viewStep.disabled = !hasStep;
  el.btnExport.setAttribute('aria-disabled', String(!hasStep));
  el.btnExport.href = hasStep ? `/api/jobs/${id}/step` : '#';
  if (!keepView || !hasStep) state.view = hasStep ? 'step' : 'import';
  pressSeg('view', state.view);
  const seq = ++state.loadSeq;
  const cache = geomCache.get(id) || {};
  geomCache.set(id, cache);
  if (geomCache.size > 12) geomCache.delete(geomCache.keys().next().value);
  try {
    if (state.view === 'step') {
      if (!cache.preview) {
        overlay(j.preview ? 'Loading solid…' : 'Tessellating STEP…');
        const [stl, edges] = await Promise.all([fetchBuf(`/api/jobs/${id}/preview.stl`), fetchBuf(`/api/jobs/${id}/preview.edges`)]);
        cache.preview = parseSTL(stl);
        cache.edges = parseEdges(edges);
        if (!j.preview) await refreshJobs();
      }
    } else if (!cache.input) {
      overlay('Loading mesh…');
      cache.input = parseSTL(await fetchBuf(`/api/jobs/${id}/input.stl`));
    }
  } catch (e) {
    overlay(null);
    if (seq === state.loadSeq) setStatus(`${j.name} — ${e.message}`);
    return;
  }
  overlay(null);
  if (seq !== state.loadSeq) return;
  showCurrent();
  if (state.view === 'step' && !j.thumb) sendThumb(j);
}
function showCurrent() {
  if (state.sel?.type !== 'job') return;
  const j = state.jobs.find((x) => x.id === state.sel.id);
  const cache = geomCache.get(state.sel.id);
  if (!j || !cache) return;
  if (state.view === 'step') {
    if (!cache.preview) { selectJob(j.id, true); return; }
    viewer.setModel(cache.preview, cache.edges);
    const faces = facesOf(j);
    setStatus(`${stem(j.name)}.step — ${faces != null ? fmtInt(faces) + ' faces' : '? faces'} — ${fmtSec(j.result?.seconds)} s`);
  } else {
    if (!cache.input) { selectJob(j.id, true); return; }
    viewer.setModel(cache.input, null);
    setStatus(`${j.name} — ${fmtInt(j.result?.triangles ?? cache.input.attributes.position.count / 3)} triangles`);
  }
}
async function sendThumb(j) {
  try {
    const blob = await viewer.snapshot();
    if (!blob) return;
    const r = await fetch(`/api/jobs/${j.id}/thumb`, { method: 'POST', body: blob, headers: { 'Content-Type': 'image/png' } });
    if (r.ok) {
      const cur = state.jobs.find((x) => x.id === j.id);
      if (cur) cur.thumb = true;
      renderHistory();
    }
  } catch { /* thumbnail is cosmetic */ }
}
async function reconvert(id) {
  const fd = new FormData();
  for (const [k, v] of Object.entries(state.opts)) fd.append(k, String(v));
  const r = await fetch(`/api/jobs/${id}/reconvert`, { method: 'POST', body: fd });
  if (!r.ok) { setStatus(`Reconvert failed: ${await errText(r)}`); return; }
  const { jobs } = await r.json();
  await refreshJobs();
  if (jobs[0]) selectJob(jobs[0]);
}
async function deleteJob(id) {
  const r = await fetch(`/api/jobs/${id}`, { method: 'DELETE' });
  if (!r.ok && r.status !== 404) { setStatus(`Delete failed: ${await errText(r)}`); return; }
  geomCache.delete(id);
  if (state.sel?.type === 'job' && state.sel.id === id) {
    state.sel = null;
    viewer.clear();
    hideResult();
    setStatus('Pick an STL to begin.');
  }
  await refreshJobs();
}

// ---------- result card ----------
function renderResult(j) {
  el.result.hidden = false;
  el.resultName.textContent = j.name;
  const r = j.result, o = j.options || {};
  const rows = [];
  if (r) {
    if (o.engine === 'trueform') {
      rows.push(['Planes', fmtInt(r.smoothPlanes ?? 0)], ['Cylinders', fmtInt(r.smoothCylinders ?? 0)], ['Fillets', fmtInt(r.smoothFillets ?? 0)]);
      if (r.smoothRejected) rows.push(['Rejected', fmtInt(r.smoothRejected), 'dim']);
      rows.push(['Faces', fmtInt(r.facesAfterSmooth ?? r.facesAfterUnify)]);
    } else {
      rows.push(['Triangles', fmtInt(r.triangles)], ['Faces', `${fmtInt(r.facesBeforeUnify)} → ${fmtInt(r.facesAfterUnify)}`]);
    }
    if (r.solids > 1 || r.components > 1) rows.push(['Solids', fmtInt(r.solids)]);
    rows.push(['Volume delta', r.volumeDeltaPct >= 0 ? `${r.volumeDeltaPct.toFixed(3)}%` : 'not measured', r.volumeDeltaPct >= 0 ? '' : 'dim']);
    rows.push(['Time', `${fmtSec(r.seconds)} s`, 'dim']);
  } else {
    rows.push(['Status', statusWord(j)]);
    if (j.status === 'running' && j.started) rows.push(['Started', fmtAge(j.started) + ' ago', 'dim']);
  }
  el.stats.replaceChildren(...rows.flatMap(([k, v, cls]) => {
    const dt = document.createElement('dt'); dt.textContent = k;
    const dd = document.createElement('dd'); dd.textContent = v; if (cls) dd.className = cls;
    return [dt, dd];
  }));
  el.pills.replaceChildren();
  if (r) {
    const ok = r.watertight && !r.openShells;
    const pill = document.createElement('span');
    pill.className = `pill ${ok ? 'ok' : 'bad'}`;
    pill.innerHTML = `${ok ? ICON.check : ICON.bang}<span></span>`;
    pill.querySelector('span').textContent = ok ? 'Watertight' : (r.openShells ? `${r.openShells} open shell${r.openShells > 1 ? 's' : ''}` : 'Not watertight');
    el.pills.append(pill);
  }
  const warns = r?.warnings || [];
  el.warn.hidden = warns.length === 0;
  el.warn.open = false;
  el.warnTitle.textContent = `${warns.length} warning${warns.length === 1 ? '' : 's'} — conversion completed with notes`;
  el.warnList.replaceChildren(...warns.map((w) => { const li = document.createElement('li'); li.textContent = w; return li; }));
  const err = j.error || (r && r.ok === false ? r.error : null);
  el.resultErr.hidden = !err;
  el.resultErr.textContent = err || '';
}
function hideResult() { el.result.hidden = true; }

// ---------- helpers ----------
function overlay(text) { el.overlay.hidden = !text; el.overlayText.textContent = text || ''; }
function setStatus(t) { el.status.textContent = t; }
async function fetchBuf(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error(await errText(r));
  return r.arrayBuffer();
}
async function errText(r) {
  try { const j = await r.json(); return j.detail || j.error || r.statusText; } catch { return r.statusText || `HTTP ${r.status}`; }
}
function stem(name) { return name.replace(/\.stl$/i, ''); }
function fmtInt(n) { return n == null ? '–' : Number(n).toLocaleString(); }
function fmtSec(s) { return s == null ? '–' : (s < 10 ? s.toFixed(2) : s.toFixed(1)); }
function fmtBytes(b) { return b < 1048576 ? `${(b / 1024).toFixed(0)} KB` : `${(b / 1048576).toFixed(1)} MB`; }
function fmtAge(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h`;
  return `${Math.floor(s / 86400)} d`;
}

// ---------- init ----------
(async () => {
  try {
    const d = await (await fetch('/api/defaults')).json();
    state.opts = { ...d.options, ...readStoredOpts() };
    el.maxMb.textContent = d.max_upload_mb;
  } catch {
    state.opts = { engine: 'verbatim', units: 'mm', schema: 'AP214', scale: 1, weld: 0, verify: true, unify: true, smooth_fillets: true, smooth_tol: 0, smooth_angle: 2, threads: 0 };
  }
  syncOptionControls();
  try {
    const h = await (await fetch('/api/health')).json();
    if (!h.converter) { el.ver.textContent = 'converter missing'; el.ver.style.color = 'var(--red)'; }
  } catch { /* offline */ }
  el.viewStep.disabled = true;
  await refreshJobs();
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshJobs(); });
})();
