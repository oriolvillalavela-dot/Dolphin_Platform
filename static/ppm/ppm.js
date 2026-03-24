/**
 * PPM — Project Molecule Tracker
 * ppm.js v5: Upload, polling, project dashboard, Canvas Gantt,
 *             inline SMILES editor, RDKit-backed 2D structure lightbox.
 */
'use strict';

const ppm = (() => {

  // ── State ────────────────────────────────────────────────────────────────
  let _pollTimer      = null;
  let _currentProject = null;

  // Map: molecule_id → base64 structure_img (populated when project loads)
  const _structureCache = {};

  // ── Status colour palette ────────────────────────────────────────────────
  const STATUS_COLORS = {
    'In plan':           { fill: 'rgba(6,182,212,0.85)',  border: '#06b6d4', text: '#e0f2fe' },
    'In progress':       { fill: 'rgba(234,179,8,0.85)',  border: '#eab308', text: '#fef9c3' },
    'Obtained':          { fill: 'rgba(34,197,94,0.85)',  border: '#22c55e', text: '#dcfce7' },
    'Delivered':         { fill: 'rgba(30,64,175,0.9)',   border: '#1d4ed8', text: '#dbeafe' },
    'On hold':           { fill: 'rgba(168,85,247,0.85)', border: '#a855f7', text: '#f3e8ff' },
    'Cancelled/Stopped': { fill: 'rgba(239,68,68,0.85)',  border: '#ef4444', text: '#fee2e2' },
  };

  // Card accent colours (cycling palette for projects)
  const CARD_ACCENTS = ['#06b6d4','#22c55e','#f59e0b','#a855f7','#ef4444','#6366f1','#0ea5e9','#14b8a6'];


  // ═══════════════════════════════════════════════════════════════════════
  //  PROJECT CARDS
  // ═══════════════════════════════════════════════════════════════════════

  async function loadProjectSelector() {
    const sel = document.getElementById('projectSelector');
    try {
      const res  = await fetch('/api/ppm/projects');
      const data = await res.json();
      const items = data.items || [];

      // Populate hidden <select>
      if (sel) {
        const prev = sel.value;
        sel.innerHTML = '<option value="">—</option>';
        items.forEach(p => {
          const o = document.createElement('option');
          o.value = p.project_id;
          o.textContent = p.project_id;
          sel.appendChild(o);
        });
        if (prev && [...sel.options].some(o => o.value === prev)) sel.value = prev;
      }

      _buildProjectCards(items);

    } catch (_) {}
  }

  function _buildProjectCards(items) {
    const grid = document.getElementById('projectCardsGrid');
    if (!grid) return;

    if (!items || items.length === 0) {
      grid.innerHTML = `<div class="col-span-full text-slate-600 text-xs text-center py-6">
        No projects yet — upload a PDF report to get started.</div>`;
      return;
    }

    grid.innerHTML = '';

    // Show first 4 in the card strip; more are accessible via the hidden select
    items.slice(0, 4).forEach((proj, idx) => {
      const cc        = CARD_ACCENTS[idx % CARD_ACCENTS.length];
      const pid       = proj.project_id;
      const molCount  = proj.molecule_count || 0;
      const latestWk  = proj.latest_week   || '—';

      const card = document.createElement('div');
      card.className = 'prj-card';
      card.dataset.pid = pid;
      card.style.setProperty('--cc', cc);
      card.style.animationDelay = `${idx * 80}ms`;

      card.innerHTML = `
        <div class="prj-card-body">
          <div class="prj-card-header">
            <span class="prj-pid">${pid}</span>
            <span class="prj-badge">${proj.theme_id ? `T${proj.theme_id}` : 'Active'}</span>
          </div>
          <p class="prj-mol-count">${molCount} molecule${molCount !== 1 ? 's' : ''}</p>
          <p class="prj-meta">Latest: ${latestWk}</p>
        </div>
        <div class="prj-card-bar"></div>`;

      card.addEventListener('click', () => _selectCard(card, pid));
      grid.appendChild(card);
    });

    // Auto-select first if nothing selected
    if (!_currentProject && items.length > 0) {
      setTimeout(() => {
        const first = grid.querySelector('.prj-card');
        if (first) _selectCard(first, items[0].project_id);
      }, 60);
    }
  }

  function _selectCard(cardEl, pid) {
    document.querySelectorAll('.prj-card').forEach(c => c.classList.remove('active'));
    cardEl.classList.add('active');
    const sel = document.getElementById('projectSelector');
    if (sel) sel.value = pid;
    loadProject(pid);
  }


  // ═══════════════════════════════════════════════════════════════════════
  //  DROP-ZONE
  // ═══════════════════════════════════════════════════════════════════════

  function _showOverlay(id) {
    ['dzProcessing', 'dzSuccess', 'dzError'].forEach(o => {
      document.getElementById(o)?.classList.toggle('hidden', o !== id);
    });
  }

  function resetDropZone() {
    ['dzProcessing', 'dzSuccess', 'dzError'].forEach(o => document.getElementById(o)?.classList.add('hidden'));
    const inp = document.getElementById('pdfFileInput');
    if (inp) inp.value = '';
    _stopPoll();
  }

  function handleDragOver(e)     { e.preventDefault(); document.getElementById('dropZone').classList.add('dz-over'); }
  function handleDragLeave()     { document.getElementById('dropZone').classList.remove('dz-over'); }
  function handleDrop(e)         { e.preventDefault(); document.getElementById('dropZone').classList.remove('dz-over'); const f = e.dataTransfer?.files?.[0]; if (f) _uploadFile(f); }
  function handleFileSelect(inp) { const f = inp.files?.[0]; if (f) _uploadFile(f); }

  async function _uploadFile(file) {
    if (!file.name.toLowerCase().endsWith('.pdf')) { alert('Only PDF files are accepted.'); return; }
    _showOverlay('dzProcessing');
    document.getElementById('dzStatus').textContent    = 'Uploading PDF…';
    document.getElementById('dzSubStatus').textContent = file.name;
    _stopPoll();
    const fd = new FormData();
    fd.append('file', file);
    try {
      const res  = await fetch('/api/ppm/upload', { method: 'POST', body: fd });
      const data = await res.json();
      if (!res.ok || data.error) {
        _showOverlay('dzError');
        document.getElementById('dzErrorText').textContent = data.error || `HTTP ${res.status}`;
        return;
      }
      document.getElementById('dzStatus').textContent = 'Extracting molecules + structures…';
      _startPoll(data.job_id);
    } catch (err) {
      _showOverlay('dzError');
      document.getElementById('dzErrorText').textContent = `Network error: ${err.message}`;
    }
  }


  // ═══════════════════════════════════════════════════════════════════════
  //  JOB POLLING
  // ═══════════════════════════════════════════════════════════════════════

  function _startPoll(jobId) { _stopPoll(); _pollTimer = setInterval(() => _pollJob(jobId), 2000); _pollJob(jobId); }
  function _stopPoll()       { if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; } }

  async function _pollJob(jobId) {
    try {
      const res  = await fetch(`/api/ppm/jobs/${jobId}`);
      if (!res.ok) return;
      const data = await res.json();
      if (data.status === 'pending' || data.status === 'processing') return;
      _stopPoll();
      if (data.status === 'error') {
        _showOverlay('dzError');
        document.getElementById('dzErrorText').textContent = data.error_msg || 'Unknown error';
        return;
      }
      _showOverlay('dzSuccess');
      document.getElementById('dzSuccessText').textContent   = data.status === 'review' ? 'Extracted with warnings' : 'Extraction complete';
      document.getElementById('dzSuccessDetail').textContent = `${data.record_count} records · ${data.week_date || 'N/A'}${data.flagged_for_review ? ' ⚠' : ''}`;
      loadJobs();
      loadProjectSelector();
    } catch (_) {}
  }


  // ═══════════════════════════════════════════════════════════════════════
  //  JOBS LIST
  // ═══════════════════════════════════════════════════════════════════════

  async function loadJobs() {
    const c = document.getElementById('jobsListContainer');
    if (!c) return;
    try {
      const res  = await fetch('/api/ppm/jobs');
      const data = await res.json();
      if (!data.items?.length) { c.innerHTML = '<div class="text-slate-600 text-xs text-center py-4">No uploads yet.</div>'; return; }
      c.innerHTML = data.items.map(job => {
        const date = job.upload_ts ? new Date(job.upload_ts).toLocaleDateString() : '—';
        return `<div class="flex items-start justify-between py-2.5 border-b border-white/5 last:border-0">
          <div class="flex-1 min-w-0">
            <p class="text-slate-300 text-xs font-medium truncate">${job.filename}${job.flagged_for_review ? ' ⚠' : ''}</p>
            <p class="text-slate-600 text-xs">${date} · ${job.record_count} records · ${job.week_date || '—'}</p>
          </div>
          <span class="job-${job.status} text-xs font-semibold ml-2 shrink-0 capitalize">${job.status}</span>
        </div>`;
      }).join('');
    } catch (_) { c.innerHTML = '<div class="text-red-400 text-xs text-center py-4">Failed to load.</div>'; }
  }


  // ═══════════════════════════════════════════════════════════════════════
  //  PROJECT DASHBOARD
  // ═══════════════════════════════════════════════════════════════════════

  async function loadProject(projectId) {
    _currentProject = projectId || null;

    const emptyState  = document.getElementById('rightEmptyState');
    const statsDiv    = document.getElementById('projectStats');
    const molSection  = document.getElementById('moleculesSection');
    const ganttCard   = document.getElementById('ganttCard');

    if (!projectId) {
      emptyState?.classList.remove('hidden');
      [statsDiv, molSection, ganttCard].forEach(el => el?.classList.add('hidden'));
      return;
    }

    try {
      const res  = await fetch(`/api/ppm/project/${encodeURIComponent(projectId)}`);
      if (!res.ok) return;
      const data = await res.json();

      emptyState?.classList.add('hidden');

      // Stats
      document.getElementById('statProject').textContent   = data.project_id;
      document.getElementById('statTheme').textContent     = (data.theme_ids || []).join(', ') || '—';
      document.getElementById('statMolecules').textContent = (data.molecules || []).length;
      document.getElementById('statWeeks').textContent     = (data.week_dates || []).length;
      statsDiv?.classList.remove('hidden');

      // Cache structure images by molecule ID (take latest per mol)
      const structs = {};
      (data.records || []).forEach(r => {
        if (r.structure_img && !structs[r.molecule_id]) {
          structs[r.molecule_id] = r.structure_img;
        }
      });
      Object.assign(_structureCache, structs);

      // Molecule table
      const hint = document.getElementById('molStructureHint');
      const hasAnyStructure = Object.keys(structs).length > 0;
      if (hint) hint.classList.toggle('hidden', !hasAnyStructure);
      if (document.getElementById('molTableProjectId'))
        document.getElementById('molTableProjectId').textContent = projectId;
      _renderMoleculesTable(data.records || [], data.molecules || []);
      molSection?.classList.remove('hidden');

      // Gantt
      if ((data.gantt_series || []).length > 0) {
        drawGantt(data.gantt_series, data.week_dates || []);
        ganttCard?.classList.remove('hidden');
      } else {
        ganttCard?.classList.add('hidden');
      }
    } catch (err) { console.error('loadProject:', err); }
  }

  function _statusBadge(status) {
    const c = STATUS_COLORS[status] || { fill: 'rgba(100,116,139,0.4)', border: '#64748b', text: '#cbd5e1' };
    return `<span style="background:${c.fill};color:${c.text};border:1px solid ${c.border};padding:2px 8px;border-radius:9999px;font-size:0.65rem;font-weight:600;">${status}</span>`;
  }

  function _escAttr(s) {
    return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  function _renderMoleculesTable(records, molecules) {
    const tbody = document.getElementById('moleculesTableBody');
    if (!tbody) return;

    const latestMap = {};
    records.forEach(r => {
      if (!latestMap[r.molecule_id] || (r.week_date > (latestMap[r.molecule_id].week_date || '')))
        latestMap[r.molecule_id] = r;
    });

    tbody.innerHTML = molecules.map(mol => {
      const rec        = latestMap[mol] || {};
      const imgB64     = _structureCache[mol] || null;
      const hasImg     = !!imgB64;
      const curSmiles  = rec.smiles || '';
      const safeId     = mol.replace(/[^a-z0-9]/gi, '_');

      const structCell = hasImg
        ? `<button class="struct-btn" onclick="ppm.openLightbox('${mol}')">&#9883; View</button>`
        : `<span class="struct-btn no-img" id="view-btn-${safeId}">&mdash;</span>`;

      const clearBtn = curSmiles
        ? `<button class="smiles-clear-btn" title="Clear" onclick="ppm.clearSMILES('${mol}','${safeId}')">&times;</button>`
        : '';

      return `
        <tr class="hover:bg-white/3 transition-colors">
          <td class="px-3 py-2 text-slate-300 font-mono text-xs">${mol}</td>
          <td class="px-3 py-2">${rec.status ? _statusBadge(rec.status) : '&mdash;'}</td>
          <td class="px-3 py-2 text-slate-500 text-xs">${rec.week_date || '&mdash;'}</td>
          <td class="px-3 py-2">
            <div class="smiles-cell">
              <input type="text" id="smiles-${safeId}"
                class="smiles-input${curSmiles ? ' has-val' : ''}"
                value="${_escAttr(curSmiles)}"
                placeholder="e.g. CCO"
                title="${curSmiles ? 'SMILES saved' : 'Enter SMILES, press Enter or Save'}"
                onkeydown="if(event.key==='Enter') ppm.saveSMILES('${mol}',this);"
              >
              <button class="smiles-save-btn" onclick="ppm.saveSMILES('${mol}',document.getElementById('smiles-${safeId}'))">Save</button>
              ${clearBtn}
            </div>
          </td>
          <td class="px-3 py-2 text-right" id="struct-cell-${safeId}">${structCell}</td>
        </tr>`;
    }).join('');
  }


  // ═══════════════════════════════════════════════════════════════════════
  //  SMILES SAVE / CLEAR
  // ═══════════════════════════════════════════════════════════════════════

  async function saveSMILES(molId, inputEl) {
    const smiles = (inputEl?.value || '').trim();
    if (!smiles) { inputEl?.focus(); return; }
    if (!_currentProject) return;
    inputEl.disabled = true;
    inputEl.classList.remove('err');
    try {
      const res  = await fetch(`/api/ppm/project/${encodeURIComponent(_currentProject)}/structure`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ molecule_id: molId, smiles }),
      });
      const data = await res.json();
      if (!res.ok) {
        inputEl.classList.add('err');
        inputEl.title = data.error || 'Invalid SMILES';
        alert('SMILES error: ' + (data.error || 'Could not parse.'));
        return;
      }
      // Update cache and View cell without reload
      _structureCache[molId] = data.structure_img;
      inputEl.classList.add('has-val');
      inputEl.classList.remove('err');
      inputEl.title = 'SMILES saved';
      const safeId = molId.replace(/[^a-z0-9]/gi, '_');
      const cellEl = document.getElementById('struct-cell-' + safeId);
      if (cellEl) {
        cellEl.innerHTML = '<button class="struct-btn" onclick="ppm.openLightbox(\'' + molId + '\')">&amp;#x26DB; View</button>';
      }
      // Add clear button if not already present
      const wrap = inputEl.closest('.smiles-cell');
      if (wrap && !wrap.querySelector('.smiles-clear-btn')) {
        const clr = document.createElement('button');
        clr.className = 'smiles-clear-btn';
        clr.title = 'Clear SMILES';
        clr.textContent = '\u00d7';
        clr.onclick = () => ppm.clearSMILES(molId, safeId);
        wrap.appendChild(clr);
      }
    } catch (err) {
      inputEl.classList.add('err');
      alert('Network error: ' + err.message);
    } finally {
      inputEl.disabled = false;
    }
  }

  async function clearSMILES(molId, safeId) {
    if (!_currentProject) return;
    if (!confirm('Clear SMILES and 2D structure for ' + molId + '?')) return;
    try {
      await fetch(
        '/api/ppm/project/' + encodeURIComponent(_currentProject) + '/structure/' + encodeURIComponent(molId),
        { method: 'DELETE' }
      );
      delete _structureCache[molId];
      const inp = document.getElementById('smiles-' + safeId);
      if (inp) { inp.value = ''; inp.classList.remove('has-val', 'err'); inp.title = ''; }
      const cellEl = document.getElementById('struct-cell-' + safeId);
      if (cellEl) cellEl.innerHTML = '<span class="struct-btn no-img">&#8212;</span>';
      const wrap = inp ? inp.closest('.smiles-cell') : null;
      if (wrap) { const clr = wrap.querySelector('.smiles-clear-btn'); if (clr) clr.remove(); }
    } catch (err) { alert('Error: ' + err.message); }
  }


  // ═══════════════════════════════════════════════════════════════════════
  //  2D STRUCTURE LIGHTBOX
  // ═══════════════════════════════════════════════════════════════════════

  function openLightbox(molId) {
    const imgB64 = _structureCache[molId];
    if (!imgB64) return;

    const lb    = document.getElementById('structureLightbox');
    const img   = document.getElementById('lightboxImg');
    const title = document.getElementById('lightboxTitle');
    const st    = document.getElementById('lightboxStatus');

    if (!lb || !img) return;

    img.src       = `data:image/png;base64,${imgB64}`;
    title.textContent = molId;

    // Show the status of this molecule too
    const rec = Object.values(_structureCache).find ? null : null;
    st.textContent = '';

    lb.classList.remove('hidden');
    // Keyboard close
    document.addEventListener('keydown', _lbKeyHandler);
  }

  function closeLightbox(e) {
    if (e && e.target !== document.getElementById('structureLightbox')) return;
    _doCloseLightbox();
  }

  function _doCloseLightbox() {
    document.getElementById('structureLightbox')?.classList.add('hidden');
    document.removeEventListener('keydown', _lbKeyHandler);
  }

  function _lbKeyHandler(e) { if (e.key === 'Escape') _doCloseLightbox(); }


  // ═══════════════════════════════════════════════════════════════════════
  //  GANTT CHART (vanilla Canvas)
  // ═══════════════════════════════════════════════════════════════════════

  function drawGantt(series, weekDates) {
    const canvas = document.getElementById('ganttCanvas');
    if (!canvas) return;
    const LABEL_W = 180, ROW_H = 38, HEADER_H = 52, PAD = 14, MIN_BAR_W = 30;

    function parseIsoDate(value) {
      const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ''));
      if (!match) return null;
      return Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    }

    function buildSegmentsFromWeeks(weeks, orderedDates) {
      const snapshots = orderedDates
        .filter(date => weeks && weeks[date])
        .map(date => ({ date, status: weeks[date] }));
      if (!snapshots.length) return [];

      const segments = [];
      let runStart = 0;
      for (let idx = 1; idx < snapshots.length; idx += 1) {
        if (snapshots[idx].status !== snapshots[idx - 1].status) {
          segments.push({
            status: snapshots[runStart].status,
            start_date: snapshots[runStart].date,
            end_date: snapshots[idx].date,
          });
          runStart = idx;
        }
      }
      segments.push({
        status: snapshots[runStart].status,
        start_date: snapshots[runStart].date,
        end_date: snapshots[snapshots.length - 1].date,
      });
      return segments;
    }

    const datedTicks = (weekDates || [])
      .map(date => ({ label: date, ts: parseIsoDate(date) }))
      .filter(item => item.ts !== null);

    if (!datedTicks.length) return;

    const minTs = datedTicks[0].ts;
    const maxTs = datedTicks[datedTicks.length - 1].ts;
    const plotW = Math.max(Math.max(datedTicks.length - 1, 1) * 170, 960);

    canvas.width  = Math.max(PAD * 2 + LABEL_W + plotW + 20, 520);
    canvas.height = Math.max(HEADER_H + series.length * ROW_H + PAD + 24, 560);
    canvas.style.width  = canvas.width  + 'px';
    canvas.style.height = canvas.height + 'px';

    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#040D1C';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    const plotX0 = PAD + LABEL_W;
    const plotX1 = canvas.width - PAD;
    const plotSpan = Math.max(plotX1 - plotX0, 1);
    const tsSpan = Math.max(maxTs - minTs, 1);
    const tickStep = Math.max(1, Math.ceil(datedTicks.length / 10));

    function xForTs(ts) {
      if (maxTs === minTs) return plotX0 + plotSpan / 2;
      return plotX0 + ((ts - minTs) / tsSpan) * plotSpan;
    }

    // Header
    ctx.fillStyle = 'rgba(255,255,255,0.04)';
    ctx.fillRect(0, 0, canvas.width, HEADER_H);
    ctx.font = 'bold 11px Inter,system-ui';
    ctx.fillStyle = '#64748b';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'center';

    datedTicks.forEach((tick, idx) => {
      const x = xForTs(tick.ts);
      if (idx % tickStep === 0 || idx === datedTicks.length - 1) {
        ctx.fillText(tick.label.slice(0, 10), x, HEADER_H / 2);
      }
      ctx.strokeStyle = 'rgba(255,255,255,0.04)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, canvas.height);
      ctx.stroke();
    });

    const ABBR = { 'In plan':'Plan','In progress':'WIP','Obtained':'Done','Delivered':'Dlvd','On hold':'Hold','Cancelled/Stopped':'Cxl' };

    series.forEach((row, ri) => {
      const yTop = HEADER_H + ri * ROW_H;
      if (ri % 2 === 0) {
        ctx.fillStyle = 'rgba(255,255,255,0.02)';
        ctx.fillRect(0, yTop, canvas.width, ROW_H);
      }
      ctx.strokeStyle = 'rgba(255,255,255,0.04)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, yTop + ROW_H);
      ctx.lineTo(canvas.width, yTop + ROW_H);
      ctx.stroke();

      ctx.font = 'bold 11px "Roboto Mono",monospace';
      ctx.fillStyle = '#94a3b8';
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      ctx.fillText(row.molecule_id, plotX0 - PAD, yTop + ROW_H / 2);

      const segments = Array.isArray(row.segments) && row.segments.length
        ? row.segments
        : buildSegmentsFromWeeks(row.weeks || {}, datedTicks.map(tick => tick.label));

      segments.forEach(segment => {
        const startTs = parseIsoDate(segment.start_date);
        const endTs = parseIsoDate(segment.end_date);
        if (startTs === null) return;
        const colors = STATUS_COLORS[segment.status] || { fill:'rgba(100,116,139,0.5)', border:'#64748b', text:'#cbd5e1' };
        const x = xForTs(startTs);
        const rawEnd = endTs === null ? x : xForTs(endTs);
        const w = Math.max(rawEnd - x, MIN_BAR_W);
        const y = yTop + 6;
        const h = ROW_H - 12;
        const r = 6;
        ctx.beginPath();
        ctx.moveTo(x + r, y);
        ctx.lineTo(x + w - r, y);
        ctx.arcTo(x + w, y, x + w, y + r, r);
        ctx.lineTo(x + w, y + h - r);
        ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
        ctx.lineTo(x + r, y + h);
        ctx.arcTo(x, y + h, x, y + h - r, r);
        ctx.lineTo(x, y + r);
        ctx.arcTo(x, y, x + r, y, r);
        ctx.closePath();
        ctx.fillStyle = colors.fill;
        ctx.fill();
        ctx.strokeStyle = colors.border;
        ctx.lineWidth = 1;
        ctx.stroke();
        ctx.font = 'bold 10px Inter,system-ui';
        ctx.fillStyle = colors.text;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(ABBR[segment.status] || segment.status.slice(0, 4), x + w / 2, y + h / 2);
      });
    });

    // Legend
    const legend = document.getElementById('ganttLegend');
    if (legend) {
      legend.innerHTML = Object.entries(STATUS_COLORS).map(([s, c]) =>
        `<span style="display:inline-flex;align-items:center;gap:4px;font-size:0.6rem;color:${c.text}">
          <span style="display:inline-block;width:10px;height:10px;border-radius:3px;background:${c.fill};border:1px solid ${c.border}"></span>${s}
        </span>`).join('');
    }
  }


  // ═══════════════════════════════════════════════════════════════════════
  //  INIT
  // ═══════════════════════════════════════════════════════════════════════

  function _init() {
    loadJobs();
    loadProjectSelector();
  }

  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', _init); }
  else { _init(); }

  // ── Public API ───────────────────────────────────────────────────────────
  return {
    handleDragOver, handleDragLeave, handleDrop, handleFileSelect,
    resetDropZone, loadJobs, loadProject, loadProjectSelector,
    drawGantt, openLightbox, closeLightbox,
    saveSMILES, clearSMILES,
  };
})();
