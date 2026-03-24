/* ===== Overlay & modal helpers ===== */
const Overlay = {
  show() { document.getElementById('overlay').classList.remove('hidden') },
  hide() { document.getElementById('overlay').classList.add('hidden') }
};
function modal(html) { document.getElementById('modalHost').innerHTML = html; }
function closeModal() { modal(''); }

/* Small helper to read data-* either on the element or an ancestor */
function getDataAttr(el, attr) {
  return el?.dataset?.[attr] || el?.getAttribute?.(`data-${attr.replace(/[A-Z]/g, m => `-${m.toLowerCase()}`)}`) ||
    el?.closest?.(`[data-${attr.replace(/[A-Z]/g, m => `-${m.toLowerCase()}`)}]`)?.getAttribute(`data-${attr.replace(/[A-Z]/g, m => `-${m.toLowerCase()}`)}`);
}

/* === Home smart search === */
function routeSmartSearch(raw) {
  const q = (typeof raw === 'string' ? raw : (raw?.value || '')).trim();
  if (!q) { return; }

  // Exact ID patterns
  const chemRe = /^Chem_\d+$/i;
  const bottleRe = /^Chem_\d+_B\d+$/i;
  const batchRe = /^Chem_\d+_B\d+_[BSH]\d+$/i;

  if (batchRe.test(q)) { location.href = '/batches?q=' + encodeURIComponent(q); return; }
  if (bottleRe.test(q)) { location.href = '/bottles?q=' + encodeURIComponent(q); return; }
  if (chemRe.test(q)) { location.href = '/chemicals?q=' + encodeURIComponent(q); return; }

  // Fuzzy patterns → likely buckets
  if (/_B\d+_[BSH]\d+$/i.test(q)) { location.href = '/batches?q=' + encodeURIComponent(q); return; }
  if (/_B\d+$/i.test(q)) { location.href = '/bottles?q=' + encodeURIComponent(q); return; }

  // Otherwise search everywhere
  location.href = '/search?q=' + encodeURIComponent(q);
}

// Single, de-duplicated listeners for home search
document.addEventListener('click', (e) => {
  if (e.target && e.target.dataset && e.target.dataset.action === 'home-search') {
    e.preventDefault();
    const inp = document.getElementById('homeSearch');
    routeSmartSearch(inp);
  }
});
document.addEventListener('keydown', (e) => {
  if (e.target && e.target.id === 'homeSearch' && e.key === 'Enter') {
    routeSmartSearch(e.target);
  }
});
// Optional: navbar/global search
document.addEventListener('keydown', (e) => {
  if (e.target && e.target.id === 'navSearch' && e.key === 'Enter') {
    routeSmartSearch(e.target);
  }
});

/* ===== Chemicals UI ===== */
const ChemUI = {
  // Manual-only "New Chemical" modal
  openNewChemical() {
    modal(`
      <div class="modal">
        <div class="modal-card">
          <div class="modal-header">
            <h3>New Chemical</h3>
            <button class="icon-btn" onclick="closeModal()">✕</button>
          </div>
          <div class="modal-body">
            <div class="form slim" id="chemForm">
              <div class="row-inline" style="align-items:flex-end; gap:8px; margin-bottom:12px; border-bottom:1px solid var(--border); padding-bottom:12px;">
                  <label style="flex:1"><span>Auto-Fill (Name, CAS, SMILES)</span><input id="autofill_input" placeholder="Enter identifier..." onkeydown="if(event.key==='Enter')ChemUI.autoFill()"></label>
                  <button class="btn" onclick="ChemUI.autoFill()">Auto-Fill</button>
              </div>
              ${[
        "common_name_abb", "cas", "ro_srn", "chemform", "mw", "mim", "density",
        "stock_solution_c", "smiles", "inchi", "inchi_key"
      ].map(k => `<label><span>${k}</span><input id="${k}" /></label>`).join("")
      }
              <label><span>aggregate_state</span>
                <select id="aggregate_state" class="themed-select">
                  <option>solid</option>
                  <option>liquid</option>
                  <option>stock solution</option>
                  <option>n/a</option>
                </select>
              </label>
            </div>
          </div>
          <div class="modal-actions">
            <button class="btn" data-action="save-chemical">Save</button>
            <button class="btn small" onclick="closeModal()">Cancel</button>
          </div>
        </div>
      </div>
    `);
  },

  async save(force = false) {
    const payload = { force: force };
    [
      "common_name_abb", "cas", "ro_srn", "chemform", "mw", "mim", "density",
      "aggregate_state", "stock_solution_c", "smiles", "inchi", "inchi_key"
    ].forEach(k => payload[k] = document.getElementById(k).value);

    Overlay.show();
    try {
      const r = await fetch('/chemicals/create', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const txt = await r.text();
      let data;
      try { data = JSON.parse(txt) } catch (e) { throw new Error(txt) }

      if (r.status === 409 && data.duplicate) {
        // Duplicate found! Show modal
        Overlay.hide(); // Hide overlay to show new modal
        ChemUI.showDuplicateModal(data.existing);
        return;
      }

      if (!data.ok) throw new Error(data.error || 'Failed');
      location.reload();
    } catch (e) {
      Overlay.hide();
      alert(e.message);
    }
  },

  showDuplicateModal(existing) {
    modal(`
        <div class="modal">
            <div class="modal-card" style="border: 2px solid var(--warning);">
                <div class="modal-header" style="background: var(--warning-light);">
                    <h3 style="color:var(--warning-dark)">⚠️ Duplicate Found</h3>
                    <button class="icon-btn" onclick="closeModal()">✕</button>
                </div>
                <div class="modal-body">
                    <p>A chemical with matching identifiers (SMILES, InChI, or CAS) already exists:</p>
                    <div style="background:var(--bg-subtle); padding:12px; margin:12px 0; border-radius:8px;">
                        <strong>${existing.chem_id}</strong><br>
                        Name: ${existing.common_name_abb}<br>
                        CAS: ${existing.cas || '-'}<br>
                        <div style="font-family:monospace; font-size:0.85em; margin-top:4px; word-break:break-all;">${existing.smiles || ''}</div>
                    </div>
                    <p>Do you want to create this entry anyway?</p>
                </div>
                <div class="modal-actions">
                    <button class="btn danger" onclick="ChemUI.save(true)">Create Anyway</button>
                    <button class="btn secondary" onclick="closeModal()">Dismiss</button>
                </div>
            </div>
        </div>
      `);
  },

  async autoFill(prefix = '') {
    const val = document.getElementById('autofill_input').value.trim();
    if (!val) return;
    Overlay.show();
    try {
      const r = await fetch('/api/autofill', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: val })
      });
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'Failed to resolve');

      const item = data.item;
      const set = (id, v) => { if (document.getElementById(prefix + id)) document.getElementById(prefix + id).value = v || ''; };
      set('common_name_abb', item.common_name_abb);
      set('cas', item.cas);
      set('chemform', item.chemform);
      set('mw', item.mw);
      set('mim', item.mim);
      set('smiles', item.smiles);
      set('inchi', item.inchi);
      set('inchi_key', item.inchi_key);
    } catch (e) { alert(e.message); }
    finally { Overlay.hide(); }
  },

  openMultiEntry() {
    modal(`
      <div id="bulkModal" class="modal">
        <div class="modal-card" style="width: 800px; height: 80vh; max-height: 800px; display: flex; flex-direction: column; background: #1a2634; border: 1px solid #2f455c; color: #e7f1ff;">
          <div class="modal-header" style="border-bottom: 1px solid #2f455c; padding: 16px 24px;">
            <h3>Multi-Entry Import</h3>
            <button class="icon-btn" onclick="closeModal()">✕</button>
          </div>
          <div class="modal-body" style="flex:1; overflow:hidden; display:flex; flex-direction:column; padding: 0;">
            
            <!-- Step 1: Input -->
            <div id="bulkStep1" style="display:flex; flex-direction:column; flex:1; padding: 24px;">
                <div style="background: #243040; padding: 16px; border-radius: 8px; margin-bottom: 16px; border: 1px solid #2f455c;">
                  <h4 style="margin:0 0 8px; color:white;">Enter Identifiers</h4>
                  <p style="margin:0; font-size:0.9em; color:#9fb7d1;">
                    Paste one identifier per line (Name, CAS, or SMILES).
                  </p>
                </div>
                <textarea id="bulk_input" style="flex:1; width:100%; font-family:monospace; padding:16px; border:1px solid #2f455c; border-radius:8px; background:#0f1a26; color:#e7f1ff; resize:none; font-size:14px; line-height:1.5;" placeholder="Enter chemicals here..."></textarea>
            </div>

            <!-- Step 2: Loading -->
            <div id="bulkStepLoading" style="display:none; flex-direction:column; flex:1; align-items:center; justify-content:center; padding: 24px;">
                <div style="width: 60%; max-width: 400px;">
                    <div style="height: 6px; width: 100%; background: #2f455c; border-radius: 3px; overflow: hidden; position: relative;">
                        <div id="bulkProgressBar" style="position: absolute; left: 0; top: 0; height: 100%; width: 30%; background: #3aa6ff; border-radius: 3px; transition: width 0.3s; animation: indeterminate 1.5s infinite linear;"></div>
                    </div>
                    <p style="text-align: center; margin-top: 16px; color: #9fb7d1;">Checking for duplicates...</p>
                </div>
                <style>
                  @keyframes indeterminate {
                    0% { left: -30%; width: 30%; }
                    50% { left: 35%; width: 30%; }
                    100% { left: 100%; width: 30%; }
                  }
                </style>
            </div>

            <!-- Step 3: Resolution & Conflicts -->
            <div id="bulkStep2" style="display:none; flex-direction:column; flex:1; overflow:hidden;">
                <div style="padding: 16px 24px; background: #243040; border-bottom: 1px solid #2f455c;">
                   <h4 style="margin:0; color:white;">Review Conflicts</h4>
                   <p style="margin:4px 0 0; font-size:0.9em; color:#9fb7d1;">
                     Use the toggle to skip or force-create duplicates.
                   </p>
                </div>
                <div class="table-wrap" style="flex:1; overflow:auto; padding: 0;">
                    <table class="grid" id="bulkPreviewTable" style="margin:0;">
                        <thead style="position:sticky; top:0; background:#1a2634; box-shadow: 0 1px 0 #2f455c;">
                            <tr>
                                <th style="padding:12px 24px; color:#9fb7d1;">Input</th>
                                <th style="padding:12px 24px; color:#9fb7d1;">Status</th>
                                <th style="padding:12px 24px; color:#9fb7d1;">Details / Existing Match</th>
                                <th style="padding:12px 24px; text-align:center; color:#9fb7d1;">Action</th>
                            </tr>
                        </thead>
                        <tbody style="background:transparent;"></tbody>
                    </table>
                </div>
            </div>

            <!-- Step 4: Results -->
            <div id="bulkStep3" style="display:none; flex-direction:column; flex:1; overflow:hidden; text-align:center; justify-content:center;">
                <div style="font-size:64px; margin-bottom:24px;">✅</div>
                <h3 style="color:white; margin-bottom:8px;">Import Complete</h3>
                <p id="bulkSummaryText" style="color:#9fb7d1; margin-bottom:24px;"></p>
                <div style="max-height: 150px; overflow:auto; text-align:left; width:80%; margin:0 auto; font-family:monospace; font-size:12px; color:#ff6b6b;" id="bulkErrors"></div>
            </div>

          </div>
          <div class="modal-actions" style="border-top: 1px solid #2f455c; padding: 16px 24px; background: #1a2634;">
            <!-- Actions Step 1 -->
            <div id="bulkActions1" style="display:flex; gap:12px; margin-left: auto;">
                <button class="btn small ghost" onclick="closeModal()">Cancel</button>
                <button class="btn primary" onclick="ChemUI.bulkPreview()">Preview & Check</button>
            </div>
            <!-- Actions Step 2 -->
            <div id="bulkActions2" style="display:none; gap:12px; margin-left: auto;">
                <button class="btn small ghost" onclick="ChemUI.bulkBackTo1()">Back</button>
                <button class="btn primary" onclick="ChemUI.bulkConfirm()">Finalize Import</button>
            </div>
            <!-- Actions Step 3 -->
            <div id="bulkActions3" style="display:none; gap:12px; margin-left: auto;">
                <button class="btn primary" onclick="closeModal(); location.reload();">Close & Refresh</button>
            </div>
          </div>
        </div>
      </div>
    `);
  },

  stagedItems: [], // Stores resolved items from preview

  async bulkPreview() {
    const text = document.getElementById('bulk_input').value;
    const lines = text.split('\n').map(l => l.trim()).filter(l => l);
    if (!lines.length) return;

    // Show Loading
    document.getElementById('bulkStep1').style.display = 'none';
    document.getElementById('bulkActions1').style.display = 'none';
    document.getElementById('bulkStepLoading').style.display = 'flex';

    try {
      const r = await fetch('/chemicals/bulk_preview', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lines })
      });
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'Failed');

      ChemUI.stagedItems = data.results;
      ChemUI.renderBulkPreview();

      // Switch to Summary
      document.getElementById('bulkStepLoading').style.display = 'none';
      document.getElementById('bulkStep2').style.display = 'flex';
      document.getElementById('bulkActions2').style.display = 'flex';

    } catch (e) {
      alert(e.message);
      ChemUI.bulkBackTo1();
    }
  },

  renderBulkPreview() {
    const tbody = document.querySelector('#bulkPreviewTable tbody');
    tbody.innerHTML = ChemUI.stagedItems.map((r, i) => {
      let statusHtml = '';
      let detailsHtml = '';
      let actionHtml = '';

      if (r.status === 'valid') {
        statusHtml = '<span style="color:#4ade80; background:rgba(74, 222, 128, 0.1); padding:2px 8px; border-radius:99px; font-size:12px;">New</span>';
        detailsHtml = `<strong>${r.item.common_name_abb}</strong>`;
        // Checked by default, standard checkbox or text "Will Create"
        actionHtml = '<span style="color:#9fb7d1; font-size:13px;">Will Create</span><input type="checkbox" checked hidden data-idx="' + i + '" class="bulk-check">';
      } else if (r.status === 'conflict') {
        statusHtml = '<span style="color:#facc15; background:rgba(250, 204, 21, 0.1); padding:2px 8px; border-radius:99px; font-size:12px;">Duplicate</span>';
        detailsHtml = `
                <div style="margin-bottom:2px;">Matches: <a href="/chemicals?q=${r.existing.chem_id}" target="_blank" style="color:#3aa6ff;">${r.existing.chem_id}</a></div>
                <div style="font-size:0.9em; color:#9fb7d1;">${r.existing.common_name_abb}</div>
                <div style="font-size:0.85em; opacity:0.6; font-family:monospace;">CAS: ${r.existing.cas || '-'}</div>
              `;
        // Toggle Switch
        actionHtml = `
            <label class="toggle-switch">
                <input type="checkbox" class="bulk-check" data-idx="${i}">
                <span class="slider"></span>
            </label>
            <div style="font-size:10px; color:#9fb7d1; margin-top:4px;">Create Anyway</div>
        `;
      } else {
        statusHtml = '<span style="color:#ff6b6b; background:rgba(255, 107, 107, 0.1); padding:2px 8px; border-radius:99px; font-size:12px;">Error</span>';
        detailsHtml = `<span style="color:#ff6b6b;">${r.error}</span>`;
        actionHtml = '-';
      }

      return `<tr style="border-bottom: 1px solid #2f455c;">
            <td style="padding:14px 24px; max-width:200px; overflow:hidden; text-overflow:ellipsis; vertical-align:middle;">${r.input}</td>
            <td style="padding:14px 24px; vertical-align:middle;">${statusHtml}</td>
            <td style="padding:14px 24px; vertical-align:middle;">${detailsHtml}</td>
            <td style="padding:14px 24px; text-align:center; vertical-align:middle;">${actionHtml}</td>
          </tr>`;
    }).join('');
  },

  bulkBackTo1() {
    document.getElementById('bulkStep2').style.display = 'none';
    document.getElementById('bulkActions2').style.display = 'none';
    document.getElementById('bulkStepLoading').style.display = 'none';
    document.getElementById('bulkStep1').style.display = 'flex';
    document.getElementById('bulkActions1').style.display = 'flex';
  },

  async bulkConfirm() {
    const checks = document.querySelectorAll('.bulk-check:checked');
    if (!checks.length) { alert("No items selected for import."); return; }

    const itemsToImport = Array.from(checks).map(cb => {
      const idx = parseInt(cb.dataset.idx);
      return ChemUI.stagedItems[idx].item;
    });

    // Show Loading again? Or just Overlay
    Overlay.show();

    try {
      const r = await fetch('/chemicals/bulk_create_confirm', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: itemsToImport })
      });
      const data = await r.json();
      if (!data.ok) throw new Error(data.error);

      // Success Logic
      document.getElementById('bulkSummaryText').innerHTML =
        `Successfully imported <strong style="color:#4ade80">${data.count}</strong> chemicals.`;

      const errDiv = document.getElementById('bulkErrors');
      if (data.errors && data.errors.length > 0) {
        errDiv.innerHTML = "<strong>Errors:</strong><br>" + data.errors.join("<br>");
      } else {
        errDiv.innerHTML = "";
      }

      document.getElementById('bulkStep2').style.display = 'none';
      document.getElementById('bulkActions2').style.display = 'none';
      document.getElementById('bulkStep3').style.display = 'flex';
      document.getElementById('bulkActions3').style.display = 'flex';

    } catch (e) { alert(e.message); }
    finally { Overlay.hide(); }
  },

  async openAvailability(chem_id) {
    Overlay.show();
    try {
      const res = await fetch(`/chemicals/${chem_id}/availability`);
      const txt = await res.text(); let data; try { data = JSON.parse(txt) } catch (e) { throw new Error(txt) }
      if (!data.ok) throw new Error(data.error || 'Failed');
      const rows = data.items.map(x => `
        <tr>
            <td><a href="/batches?q=${encodeURIComponent(x.batch_id)}" target="_blank">${x.batch_id}</a></td>
            <td>${x.location}</td>
            <td>${x.sublocation || ""}</td>
            <td>${x.status || ""}</td>
        </tr>`).join('');
      modal(`
        <div class="modal">
          <div class="modal-card">
            <div class="modal-header">
              <h3>${chem_id} • Available / Stock Room</h3>
              <button class="icon-btn" onclick="closeModal()">✕</button>
            </div>
            <div class="modal-body">
              <div class="table-wrap">
                <table class="grid">
                  <thead><tr><th>Batch_ID</th><th>Location</th><th>Sublocation</th><th>Status</th></tr></thead>
                  <tbody>${rows || '<tr><td colspan=3>No batches</td></tr>'}</tbody>
                </table>
              </div>
            </div>
            <div class="modal-actions"><button class="btn" onclick="closeModal()">Close</button></div>
          </div>
        </div>
      `);
    } catch (e) { alert(e.message) } finally { Overlay.hide() }
  },

  async createFromSearch(query) {
    if (!query) return;
    Overlay.show();
    try {
      // 1. Autofill to resolve data
      const rAuto = await fetch('/api/autofill', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: query })
      });
      const dAuto = await rAuto.json();
      if (!dAuto.ok) throw new Error(dAuto.error || 'Could not resolve chemical details from search.');

      const item = dAuto.item;
      // 2. Create chemical
      const payload = {
        common_name_abb: item.common_name_abb,
        cas: item.cas,
        ro_srn: item.ro_srn,
        chemform: item.chemform,
        mw: item.mw,
        mim: item.mim,
        density: item.density,
        aggregate_state: item.aggregate_state,
        stock_solution_c: item.stock_solution_c,
        smiles: item.smiles,
        inchi: item.inchi,
        inchi_key: item.inchi_key
      };

      const rCreate = await fetch('/chemicals/create', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const dCreate = await rCreate.json();
      if (!dCreate.ok) throw new Error(dCreate.error || 'Failed to create chemical.');

      // 3. Success -> Redirect
      window.location.href = `/chemicals?q=${dCreate.chem.chem_id}`;

    } catch (e) {
      alert(e.message);
    } finally {
      Overlay.hide();
    }
  },

  /* Functional Group Filter Logic (Delegated to FGFilter) */
  applyFilters() {
    const q = document.getElementById('chemSearch').value;
    const params = FGFilter.getQueryParams();
    window.location = '?q=' + encodeURIComponent(q) + params;
  },

  clearFilters() {
    FGFilter.clear();
  },
};

/* === Advanced Functional Group Filter Component === */
const FGFilter = {
  selected: new Set(), // Set of IDs
  mode: 'any',         // 'any' or 'all'
  groups: [],          // Array of {id, label, category}
  isOpen: false,

  async init() {
    const container = document.getElementById('fgFilterComponent');
    if (!container) return;

    // Load URL params
    const params = new URLSearchParams(location.search);
    params.getAll('fg').forEach(id => this.selected.add(id));
    this.mode = params.get('mode') || 'any';

    // Render skeleton
    this.renderContainer(container);

    // Fetch data
    try {
      const r = await fetch('/chemicals/functional_groups');
      const data = await r.json();
      if (data.ok) {
        this.groups = data.groups;
        this.render();
      }
    } catch (e) {
      console.error("Failed to load FG", e);
      container.innerHTML = '<div class="fgPlaceholder">Failed to load groups</div>';
    }

    // Global click to close
    document.addEventListener('click', (e) => {
      if (this.isOpen && !e.target.closest('.fgFilter')) {
        this.toggle(false);
      }
    });
  },

  renderContainer(container) {
    container.innerHTML = `
      <div class="fgControl" tabindex="0" onclick="FGFilter.toggle()" onkeydown="FGFilter.handleKey(event)">
        <div class="fgChips" id="fgChips"></div>
        <div class="fgIcons">
          <span id="fgClear" class="fgIconBtn" style="display:none" onclick="event.stopPropagation(); FGFilter.clear()">✕</span>
          <span style="font-size:10px; color:var(--fg-muted)">▼</span>
        </div>
      </div>
      <div class="fgDropdown" id="fgDropdown">
        <div class="fgDropdownHeader">
          <input type="text" class="fgDropdownSearch" placeholder="Search groups..." id="fgSearch" 
                 oninput="FGFilter.renderOptions()" onclick="event.stopPropagation()">
        </div>
        <div class="fgOptions" id="fgOptions"></div>
        <div class="fgFooter">
          <div class="fgToggle">
            <button class="${this.mode === 'any' ? 'active' : ''}" onclick="FGFilter.setMode('any')">Match Any</button>
            <button class="${this.mode === 'all' ? 'active' : ''}" onclick="FGFilter.setMode('all')">Match All</button>
          </div>
          <button class="fgButton fgButtonPrimary" onclick="ChemUI.applyFilters()">Apply</button>
        </div>
      </div>
    `;
    this.render();
  },

  render() {
    const chipsEl = document.getElementById('fgChips');
    const clearBtn = document.getElementById('fgClear');
    if (!chipsEl) return;

    // Render Chips
    if (this.selected.size === 0) {
      chipsEl.innerHTML = '<span class="fgPlaceholder">Functional groups...</span>';
      clearBtn.style.display = 'none';
    } else {
      clearBtn.style.display = 'flex';
      const arr = Array.from(this.selected);
      let html = '';
      // Show first 2
      arr.slice(0, 2).forEach(id => {
        const g = this.groups.find(x => x.id === id);
        const label = g ? g.label : id;
        html += `
          <div class="fgChip">
            <span>${label}</span>
            <button class="fgChipRemove" onclick="event.stopPropagation(); FGFilter.toggleId('${id}')">✕</button>
          </div>
        `;
      });
      // +N
      if (arr.length > 2) {
        html += `<div class="fgChip" style="padding:4px 8px"><span>+${arr.length - 2}</span></div>`;
      }
      chipsEl.innerHTML = html;
    }

    // Render Options if open
    if (this.isOpen) {
      this.renderOptions();
      document.getElementById('fgDropdown').classList.add('show');
      document.querySelector('.fgControl').classList.add('active');
    } else {
      document.getElementById('fgDropdown').classList.remove('show');
      document.querySelector('.fgControl').classList.remove('active');
    }

    // Update Mode Toggles
    document.querySelectorAll('.fgToggle button').forEach(b => {
      b.classList.toggle('active', (b.textContent.includes('Any') && this.mode === 'any') || (b.textContent.includes('All') && this.mode === 'all'));
    });
  },

  renderOptions() {
    const container = document.getElementById('fgOptions');
    const search = document.getElementById('fgSearch').value.toLowerCase();

    // Group by category
    const grouped = {};
    this.groups.forEach(g => {
      if (search && !g.label.toLowerCase().includes(search)) return;
      const cat = g.category || 'Other';
      if (!grouped[cat]) grouped[cat] = [];
      grouped[cat].push(g);
    });

    // Sort categories (Oxygen, Nitrogen, Sulfur, Halogen, Unsat, Phos, Other)
    const order = ["Oxygen-containing", "Nitrogen-containing", "Sulfur-containing", "Halogens", "Unsaturation / aromatic", "Phosphorus / boron", "Other"];

    let html = '';
    order.forEach(cat => {
      if (grouped[cat] && grouped[cat].length > 0) {
        html += `<div class="fgGroupTitle">${cat}</div>`;
        grouped[cat].forEach(g => {
          const isSel = this.selected.has(g.id);
          html += `
            <div class="fgOption" aria-selected="${isSel}" onclick="FGFilter.toggleId('${g.id}')">
              <input type="checkbox" ${isSel ? 'checked' : ''} readonly>
              <span>${g.label}</span>
            </div>
          `;
        });
      }
    });

    if (!html) html = '<div style="padding:10px; color:var(--fg-muted); text-align:center">No matches</div>';
    container.innerHTML = html;
  },

  toggle(force) {
    this.isOpen = force !== undefined ? force : !this.isOpen;
    this.render();
    if (this.isOpen) {
      setTimeout(() => document.getElementById('fgSearch').focus(), 50);
    }
  },

  toggleId(id) {
    if (this.selected.has(id)) this.selected.delete(id);
    else this.selected.add(id);
    this.render();
  },

  setMode(m) {
    this.mode = m;
    this.render();
  },

  clear() {
    this.selected.clear();
    this.render();
    ChemUI.applyFilters();
  },

  getQueryParams() {
    let s = '';
    this.selected.forEach(id => s += `&fg=${encodeURIComponent(id)}`);
    if (this.selected.size > 0 && this.mode !== 'any') s += `&mode=${this.mode}`;
    return s;
  },

  handleKey(e) {
    if (e.key === 'Enter' || e.key === ' ') {
      this.toggle();
      e.preventDefault();
    }
    if (e.key === 'Escape') {
      this.toggle(false);
    }
  }
};

/* Preview Logic */
ChemUI.openPreview = async function (chem_id) {
  Overlay.show();
  try {
    const r = await fetch(`/chemicals/${chem_id}/preview`);
    if (r.status === 404) throw new Error("No structure available");
    const svg = await r.text();

    modal(`
        <div class="modal">
          <div class="modal-card">
            <div class="modal-header">
              <h3>Preview: ${chem_id}</h3>
              <button class="icon-btn" onclick="closeModal()">✕</button>
            </div>
            <div class="modal-body" style="text-align:center; padding: 20px;">
              ${svg}
            </div>
            <div class="modal-actions">
              <button class="btn" onclick="closeModal()">Close</button>
            </div>
          </div>
        </div>
      `);
  } catch (e) {
    alert(e.message);
  } finally {
    Overlay.hide();
  }
};

// Init filters on load
document.addEventListener('DOMContentLoaded', () => {
  FGFilter.init();
});

// === Edit modal helpers ===
ChemUI.openEdit = async function (chem_id) {
  try {
    Overlay.show();
    const r = await fetch(`/chemicals/${chem_id}/json`);
    const t = await r.text(); let d; try { d = JSON.parse(t) } catch (e) { throw new Error(t) }
    if (!d.ok) throw new Error(d.error || 'Load failed');
    const x = d.item || {};
    const field = (k, lab, v) => `<label><span>${lab}</span><input id="ed_${k}" value="${v ?? ''}"></label>`;
    modal(`
      <div class="modal">
        <div class="modal-card">
          <div class="modal-header"><h3>Edit ${chem_id}</h3><button class="icon-btn" onclick="closeModal()">✕</button></div>
          <div class="modal-body">
            <div class="form slim grid2">
              <div class="row-inline" style="align-items:flex-end; gap:8px; margin-bottom:12px; border-bottom:1px solid var(--border); padding-bottom:12px; grid-column: span 2;">
                  <label style="flex:1"><span>Auto-Fill (Name, CAS, SMILES)</span><input id="autofill_input" placeholder="Enter identifier..." onkeydown="if(event.key==='Enter')ChemUI.autoFill('ed_')"></label>
                  <button class="btn" onclick="ChemUI.autoFill('ed_')">Auto-Fill</button>
              </div>
              ${field('common_name_abb', 'common_name_abb', x.common_name_abb)}
              ${field('cas', 'cas', x.cas)}
              ${field('ro_srn', 'ro_srn', x.ro_srn)}
              ${field('chemform', 'chemform', x.chemform)}
              ${field('mw', 'mw', x.mw)}
              ${field('mim', 'mim', x.mim)}
              ${field('density', 'density', x.density)}
              ${field('aggregate_state', 'aggregate_state', x.aggregate_state)}
              ${field('stock_solution_c', 'stock_solution_c', x.stock_solution_c)}
              ${field('smiles', 'smiles', x.smiles)}
              ${field('inchi', 'inchi', x.inchi)}
              ${field('inchi_key', 'inchi_key', x.inchi_key)}
            </div>
          </div>
          <div class="modal-actions">
            <button class="btn" data-action="chem-edit-save" data-id="${chem_id}">Save</button>
            <button class="btn small" onclick="closeModal()">Cancel</button>
          </div>
        </div>
      </div>
    `);
  } catch (err) { alert(err.message || err); }
  finally { Overlay.hide(); }
};
ChemUI.saveEdit = async function (chem_id) {
  const pick = id => (document.getElementById('ed_' + id)?.value ?? '');
  const payload = {
    common_name_abb: pick('common_name_abb'),
    cas: pick('cas'),
    ro_srn: pick('ro_srn'),
    chemform: pick('chemform'),
    mw: pick('mw'),
    mim: pick('mim'),
    density: pick('density'),
    aggregate_state: pick('aggregate_state'),
    stock_solution_c: pick('stock_solution_c'),
    smiles: pick('smiles'),
    inchi: pick('inchi'),
    inchi_key: pick('inchi_key')
  };
  try {
    Overlay.show();
    const r = await fetch('/chemicals/' + chem_id + '/update', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const t = await r.text(); let d; try { d = JSON.parse(t) } catch (e) { throw new Error(t) }
    if (!d.ok) throw new Error(d.error || 'Save failed');
    closeModal(); location.reload();
  } catch (err) { alert(err.message || err); }
  finally { Overlay.hide(); }
};

/* ===== Exporter UI ===== */
const ExporterUI = {
  selected: new Set(),

  init() {
    // Load from storage
    try {
      const saved = sessionStorage.getItem('exporter_selection');
      if (saved) {
        JSON.parse(saved).forEach(id => this.selected.add(id));
      }
    } catch (e) { }

    this.render();
  },

  updateSelection(checkbox) {
    if (checkbox.checked) {
      this.selected.add(checkbox.value);
    } else {
      this.selected.delete(checkbox.value);
    }
    this.save();
    this.render();
  },

  toggleAll(master) {
    const checkboxes = document.querySelectorAll('.row-select');
    checkboxes.forEach(cb => {
      cb.checked = master.checked;
      if (master.checked) this.selected.add(cb.value);
      else this.selected.delete(cb.value);
    });
    this.save();
    this.render();
  },

  save() {
    sessionStorage.setItem('exporter_selection', JSON.stringify(Array.from(this.selected)));
  },

  render() {
    // Update checkboxes
    document.querySelectorAll('.row-select').forEach(cb => {
      cb.checked = this.selected.has(cb.value);
    });
    // Update count
    const el = document.getElementById('selCount');
    if (el) el.textContent = this.selected.size;
  },

  // --- Export Modal ---
  openExportModal() {
    if (this.selected.size === 0) {
      alert("No chemicals selected.");
      return;
    }
    const today = new Date().toISOString().slice(0, 10);
    modal(`
      <div class="modal">
        <div class="modal-card" style="width: 400px">
          <div class="modal-header">
            <h3>Export Chemicals</h3>
            <button class="icon-btn" onclick="closeModal()">✕</button>
          </div>
          <div class="modal-body">
            <div class="form slim">
              <label><span>Format</span>
                <select id="expFormat" class="themed-select" onchange="ExporterUI.onFormatChange()">
                  <option value="sdf">SDF (.sdf)</option>
                  <option value="csv">CSV (.csv)</option>
                  <option value="xlsx">Excel (.xlsx)</option>
                </select>
              </label>
              <label><span>File Name</span>
                <input id="expName" value="chem_export_${today}" placeholder="chem_export">
              </label>
              <div style="font-size:12px; color:var(--muted); margin-top:4px" id="expPreview">
                Will export as: chem_export_${today}.sdf
              </div>
            </div>
          </div>
          <div class="modal-actions">
            <button class="btn primary" onclick="ExporterUI.exportData()">Download</button>
            <button class="btn small" onclick="closeModal()">Cancel</button>
          </div>
        </div>
      </div>
    `);
  },

  onFormatChange() {
    const fmt = document.getElementById('expFormat').value;
    const nameInput = document.getElementById('expName');
    let val = nameInput.value;

    // Update preview
    const ext = fmt === 'xlsx' ? 'xlsx' : fmt;
    document.getElementById('expPreview').textContent = `Will export as: ${val}.${ext}`;
  },

  exportData() {
    const fmt = document.getElementById('expFormat').value;
    let name = document.getElementById('expName').value.trim();
    if (!name) { alert("File name is required"); return; }

    // Sanitize filename
    name = name.replace(/[^a-z0-9_\-]/gi, '_');

    // Build URL
    const params = new URLSearchParams();
    this.selected.forEach(id => params.append('ids', id));

    // Add FG filters if any (from URL or current state)
    // We should respect the current page filters too if the user wants "Export All" but here we export SELECTED.
    // Requirement: "Export should only include chemicals currently included by the filter"
    // Since we are exporting specific IDs (this.selected), the filter is implicit if the user selected them from a filtered list.
    // However, if the user selected items, then changed filter, the selection remains.
    // The requirement says "Export should only include chemicals currently included by the filter".
    // This implies we should intersect selection with filter? 
    // Or maybe "Export All" vs "Export Selected". 
    // The current UI is "Export Selected". So we just export what is selected.
    // But wait, "Multi-search results reflect filter inclusion/exclusion clearly."
    // Let's pass the FG params just in case backend wants to filter the ID list further?
    // Actually, if we pass IDs, we usually expect those exact IDs.
    // But if the requirement says "Export should only include chemicals currently included by the filter",
    // maybe we should pass the filter to the backend and let it filter the provided IDs?
    // Let's do that.

    const urlParams = new URLSearchParams(window.location.search);
    urlParams.getAll('fg').forEach(fg => params.append('fg', fg));
    if (urlParams.get('mode')) params.append('mode', urlParams.get('mode'));

    // Trigger download
    const url = `/export/${fmt}?${params.toString()}`;

    // We need to set the filename. The backend sets Content-Disposition, but we can't easily control it from here 
    // unless we use the download attribute on an anchor, but the backend stream name takes precedence usually.
    // But we can try.

    window.location.href = url;
    closeModal();

    // Clear selection as requested
    this.selected.clear();
    this.save();
    this.render();
  },

  // --- Multi-Search ---
  openMultiSearch() {
    modal(`
      <div class="modal">
        <div class="modal-card" style="width: 600px; max-height: 80vh; display:flex; flex-direction:column;">
          <div class="modal-header">
            <h3>Multi-Search Import</h3>
            <button class="icon-btn" onclick="closeModal()">✕</button>
          </div>
          <div class="modal-body" style="flex:1; overflow:hidden; display:flex; flex-direction:column;">
            <div id="msInputStep">
              <p style="margin-bottom:8px; font-size:0.9em; color:var(--text-muted)">
                Paste names, CAS, SMILES, or Chem_IDs (one per line). Max 2000 lines.
              </p>
              <textarea id="msInput" rows="10" style="width:100%; font-family:monospace; padding:8px; border:1px solid var(--border); border-radius:4px; background:var(--bg); color:var(--fg);"></textarea>
              <div style="font-size:0.8em; color:var(--muted); margin-top:4px">
                Each line will be searched in chemDB. Blank lines are ignored.
              </div>
            </div>
            
            <div id="msResultStep" style="display:none; flex:1; flex-direction:column; overflow:hidden;">
              <div class="toolbar" style="margin-bottom:8px; gap:10px;">
                <div id="msSummary" style="font-size:0.9em; font-weight:bold;"></div>
                <div style="flex:1"></div>
                <button class="btn small ghost" onclick="ExporterUI.msSelectAll()">Select All Found</button>
                <button class="btn small ghost" onclick="ExporterUI.msClearSel()">Clear</button>
              </div>
              <div class="table-wrap" style="flex:1; overflow:auto; border:1px solid var(--border); border-radius:8px;">
                <table class="grid" id="msTable">
                  <thead>
                    <tr>
                      <th style="width:30px"></th>
                      <th>Input</th>
                      <th>Type</th>
                      <th>Match</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody></tbody>
                </table>
              </div>
            </div>
          </div>
          <div class="modal-actions">
            <button id="msBtnSearch" class="btn primary" onclick="ExporterUI.runMultiSearch()">Search</button>
            <button id="msBtnAdd" class="btn primary" style="display:none" onclick="ExporterUI.msAddSelected()">Add to Export</button>
            <button class="btn small" onclick="closeModal()">Close</button>
          </div>
        </div>
      </div>
    `);
  },

  async runMultiSearch() {
    const text = document.getElementById('msInput').value;
    const lines = text.split('\n').map(l => l.trim()).filter(l => l);
    if (!lines.length) return;
    if (lines.length > 2000) {
      if (!confirm(`You pasted ${lines.length} lines. This might take a while. Continue?`)) return;
    }

    const btn = document.getElementById('msBtnSearch');
    btn.disabled = true;
    btn.textContent = "Searching...";

    try {
      const r = await fetch('/chemdb/multisearch', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lines })
      });
      const d = await r.json();
      if (!d.ok) throw new Error(d.error);

      // Render results
      document.getElementById('msInputStep').style.display = 'none';
      document.getElementById('msResultStep').style.display = 'flex';
      document.getElementById('msBtnSearch').style.display = 'none';
      document.getElementById('msBtnAdd').style.display = 'inline-flex';

      const tbody = document.querySelector('#msTable tbody');
      let found = 0, notFound = 0, invalid = 0;

      tbody.innerHTML = d.results.map((res, idx) => {
        if (res.status === 'found') found++;
        else if (res.status === 'invalid') invalid++;
        else notFound++;

        const match = res.matches && res.matches.length > 0 ? res.matches[0] : null;
        const isFound = res.status === 'found' && match;

        // Checkbox only if found
        const chk = isFound ? `<input type="checkbox" class="ms-chk" value="${match.chem_id}" checked>` : '';

        let matchHtml = '';
        if (isFound) {
          matchHtml = `<b>${match.chem_id}</b> <span style="color:var(--muted)">${match.common_name_abb}</span>`;
          if (res.matches.length > 1) matchHtml += ` <span class="tag warning">Ambiguous</span>`;
        } else {
          matchHtml = '<span style="color:var(--muted)">-</span>';
        }

        return `
          <tr>
            <td>${chk}</td>
            <td style="font-family:monospace; font-size:0.9em">${res.input}</td>
            <td><span class="tag">${res.type}</span></td>
            <td>${matchHtml}</td>
            <td>
              <span class="tag ${res.status === 'found' ? 'success' : 'danger'}">
                ${res.status}
              </span>
            </td>
          </tr>
        `;
      }).join('');

      document.getElementById('msSummary').textContent = `Input: ${lines.length} | Found: ${found} | Not Found: ${notFound}`;

    } catch (e) {
      alert(e.message);
      btn.disabled = false;
      btn.textContent = "Search";
    }
  },

  msSelectAll() {
    document.querySelectorAll('.ms-chk').forEach(c => c.checked = true);
  },
  msClearSel() {
    document.querySelectorAll('.ms-chk').forEach(c => c.checked = false);
  },

  msAddSelected() {
    let count = 0;
    document.querySelectorAll('.ms-chk:checked').forEach(c => {
      this.selected.add(c.value);
      count++;
    });
    this.save();
    this.render();
    closeModal();
    // Optional: notify user
    // alert(`Added ${count} chemicals to selection.`);
  },

  // Filters
  applyFilters() {
    const q = document.getElementById('chemSearch').value;
    const params = FGFilter.getQueryParams();
    window.location = '?q=' + encodeURIComponent(q) + params;
  },

  openPreview(chem_id) {
    ChemUI.openPreview(chem_id);
  }
};


/* ===== Suppliers helper ===== */
async function listSuppliers(q = "") {
  const r = await fetch('/suppliers' + (q ? `?q=${encodeURIComponent(q)}` : ''));
  const txt = await r.text(); let d; try { d = JSON.parse(txt) } catch (e) { throw new Error(txt) }
  return d.ok ? d.suppliers : [];
}

/* ===== Bottles UI ===== */
const BottlesUI = {
  async openGenerateBottle(chem_id) {
    modal(`
      <div class="modal">
        <div class="modal-card">
          <div class="modal-header">
            <h3>Generate bottle for ${chem_id}</h3>
            <button class="icon-btn" onclick="closeModal()">✕</button>
          </div>
          <div class="modal-body">
            <div class="form slim">
              <label><span>supplier_id</span>
                <input id="supplier_input" placeholder="Start typing (or add new)">
                <div id="supplier_list" class="table-wrap"
                     style="display:none;max-height:120px;overflow:auto;margin-top:6px;border:1px solid var(--border);border-radius:8px;padding:6px;background:var(--card)"></div>
              </label>
              <label><span>Lot_no</span><input id="Lot_no" /></label>
              <label><span>purity</span><input id="b_purity" type="number" step="0.01" /></label>
              <label><span>size/amount</span><input id="size_amount" /></label>
              <label><span>Barcode</span><input id="b_barcode" /></label>
            </div>
          </div>
          <div class="modal-actions">
            <button class="btn" data-action="save-bottle" data-chem-id="${chem_id}">Create bottle</button>
            <button class="btn small" onclick="closeModal()">Cancel</button>
          </div>
        </div>
      </div>
    `);
  },

  // Show "➕ Add…" when no matches; create via /suppliers/create
  async filterSuppliers(q) {
    const names = await listSuppliers(q || "");
    const list = document.getElementById('supplier_list');
    if (!list) return;
    if (q && names.length === 0) {
      list.style.display = 'block';
      const safe = q.replace(/"/g, '&quot;');
      list.innerHTML = `
        <div class="option" data-action="add-supplier" data-supplier="${safe}">
          ➕ Add "${safe}"
        </div>`;
    } else {
      list.style.display = names.length ? 'block' : 'none';
      list.innerHTML = names.map(n => `<div class="option" data-action="pick-supplier" data-supplier="${n.replace(/"/g, '&quot;')}">${n}</div>`).join('');
    }
  },

  async addSupplier(name) {
    const val = (name || '').trim();
    if (!val) return;
    try {
      Overlay.show();
      const r = await fetch('/suppliers/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: val })
      });
      const t = await r.text(); let d; try { d = JSON.parse(t) } catch (e) { throw new Error(t) }
      if (!d.ok) throw new Error(d.error || 'Failed to add supplier');
      this.pickSupplier(d.supplier || val);
    } catch (err) { alert(err.message || err); }
    finally { Overlay.hide(); }
  },

  pickSupplier(name) {
    const inp = document.getElementById('supplier_input');
    inp.value = name.startsWith('Add "') ? name.slice(5, -1) : name;
    const list = document.getElementById('supplier_list');
    if (list) list.style.display = 'none';
  },

  async saveBottle(chem_id) {
    const supplier_id = document.getElementById('supplier_input').value.trim();
    const Lot_no = document.getElementById('Lot_no').value.trim();
    const purity = document.getElementById('b_purity').value.trim();
    const size_amount = document.getElementById('size_amount').value.trim();
    const barcode = document.getElementById('b_barcode').value.trim();
    if (!supplier_id || !Lot_no || !purity || !size_amount) { alert('All fields are required'); return; }
    Overlay.show();
    try {
      const r = await fetch(`/bottles/create/${chem_id}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ supplier_id, Lot_no, purity, size_amount, barcode })
      });
      const txt = await r.text(); let data; try { data = JSON.parse(txt) } catch (e) { throw new Error(txt) }
      if (!data.ok) throw new Error(data.error || 'Failed');
      closeModal();
      // STEP 2: pass bottle's barcode & amount to prefill the auto-opened batch modal
      BatchesUI.openInitialBatch(chem_id, data.bottle_id, data.bottle_no, {
        barcode: barcode,
        amount: size_amount
      });
    } catch (e) { alert(e.message) } finally { Overlay.hide() }
  }
};

/* ===== Batches UI ===== */
function formatDMY(iso) {
  try {
    if (!iso) return '';
    const d = new Date(iso); const dd = String(d.getUTCDate()).padStart(2, '0');
    const mm = String(d.getUTCMonth() + 1).padStart(2, '0'); const yy = d.getUTCFullYear();
    return `${dd}/${mm}/${yy}`;
  } catch (e) { return '' }
}

const BatchesUI = {
  // Accept optional opts { barcode, amount } ONLY for the bottle->batch flow
  openInitialBatch(chem_id, bottle_id, bottle_no, opts = {}) {
    modal(this._batchModal({
      title: `Create initial batch for ${bottle_id}`,
      chem_id,
      bottle_no,
      typeLocked: true,
      Type: 'Bottle',
      defaults: { barcode: opts.barcode || '', amount: opts.amount || '' }
    }));
    this.onTypeChange();
  },
  openGenerateBatch(chem_id, bottle_id) {
    const bottle_no = parseInt(String(bottle_id).split('_B').pop(), 10);
    // No defaults for manual/other flows
    modal(this._batchModal({ title: `Generate batch for ${bottle_id}`, chem_id, bottle_no, typeLocked: false }));
    this.onTypeChange();
  },
  _batchModal({ title, chem_id, bottle_no, typeLocked, Type, defaults }) {
    const defBC = defaults?.barcode ?? '';
    const defAmt = defaults?.amount ?? '';
    return `
    <div class="modal">
      <div class="modal-card">
        <div class="modal-header"><h3>${title}</h3><button class="icon-btn" onclick="closeModal()">✕</button></div>
        <div class="modal-body">
          <div class="form slim">
            <label><span>Type</span>
              <select id="bt_Type" class="themed-select" ${typeLocked ? 'disabled' : ''}>
                <option ${Type === 'Bottle' ? 'selected' : ''}>Bottle</option>
                <option>Stock solution</option>
                <option>Head</option>
              </select>
            </label>
            <div id="conc_row">
              <label><span>concentration_moll</span><input id="bt_conc" type="number" step="0.000001" /></label>
            </div>
            <label><span>Barcode</span><input id="bt_barcode" value="${defBC}" /></label>
            <label><span>location</span><input id="bt_location" /></label>
            <label><span>sublocation (optional)</span><input id="bt_sublocation" /></label>
            <label><span>amount</span><input id="bt_amount" value="${defAmt}" /></label>
            <div id="stock_extra" style="display:none">
              <label><span>Expiring date</span><input id="bt_exp" type="date" /></label>
            </div>
          </div>
        </div>
        <div class="modal-actions">
          <button class="btn" data-action="save-batch" data-chem-id="${chem_id}" data-bottle-no="${bottle_no}" data-type-locked="${typeLocked ? '1' : '0'}">Create</button>
          <button class="btn small" onclick="closeModal()">Cancel</button>
        </div>
      </div>
    </div>`;
  },
  onTypeChange() {
    const t = document.getElementById('bt_Type')?.value || '';
    const conc = document.getElementById('conc_row');
    const stock = document.getElementById('stock_extra');
    if (conc) conc.style.display = (t === 'Stock solution') ? 'block' : 'none';
    if (stock) stock.style.display = (t === 'Stock solution') ? 'block' : 'none';
  },
  async save(chem_id, bottle_no, typeLocked) {
    const Type = typeLocked ? 'Bottle' : document.getElementById('bt_Type').value;
    const conc = document.getElementById('bt_conc')?.value;
    const Barcode = document.getElementById('bt_barcode').value;
    const location = document.getElementById('bt_location').value;
    const sublocation = document.getElementById('bt_sublocation').value;
    const amount = document.getElementById('bt_amount').value;
    const expiring_date = document.getElementById('bt_exp') ? document.getElementById('bt_exp').value : "";

    if (!Type || !Barcode || !location || !amount) { alert('Please fill all required fields'); return; }
    if (Type === 'Stock solution' && !conc) { alert('Please provide concentration for Stock solution'); return; }

    const url = typeLocked ? '/batches/create_for_new_bottle' : '/batches/create';
    const body = {
      chem_id, bottle_no, Type,
      concentration_moll: (Type === 'Stock solution') ? conc : "",
      Barcode, location, sublocation, amount, expiring_date
    };
    Overlay.show();
    try {
      const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const txt = await r.text(); let data; try { data = JSON.parse(txt) } catch (e) { throw new Error(txt) }
      if (!data.ok) throw new Error(data.error || 'Failed');
      closeModal(); location.href = '/batches';
    } catch (e) { alert(e.message) } finally { Overlay.hide() }
  },
  openManage(batch_id, location, sublocation, status, kind, exp) {
    const showDate = (kind === 'Stock solution' && status === 'Expired');
    const dateRowHTML = `<div id="mg_exp_row" style="display:${showDate ? 'block' : 'none'}">
        <label><span>New expiring date (dd/mm/yyyy)</span><input id="mg_exp" placeholder="dd/mm/yyyy" value="${exp ? formatDMY(exp) : ''}"></label>
      </div>`;
    modal(`
    <div class="modal">
      <div class="modal-card">
        <div class="modal-header"><h3>Manage ${batch_id}</h3><button class="icon-btn" onclick="closeModal()">✕</button></div>
        <div class="modal-body"><div class="form slim">
          <label><span>location</span><input id="mg_loc" value="${location}"></label>
          <label><span>sublocation</span><input id="mg_subloc" value="${sublocation || ''}"></label>
          <label><span>Status</span>
            <select id="mg_status" class="themed-select">
              ${["Available", "Empty", "Stock Room", "Expired", "Lent"].map(s => `<option ${s === status ? 'selected' : ''}>${s}</option>`).join('')}
            </select>
          </label>
          ${dateRowHTML}
        </div></div>
        <div class="modal-actions">
          <button class="btn" data-action="save-manage-batch" data-batch-id="${batch_id}" data-kind="${kind}">Save</button>
          <button class="btn small" onclick="closeModal()">Cancel</button>
        </div>
      </div>
    </div>`);
  },
  async saveManage(batch_id) {
    const loc = document.getElementById('mg_loc').value;
    const sublocation = document.getElementById('mg_subloc').value;
    const status = document.getElementById('mg_status').value;
    const btn = document.querySelector('[data-action="save-manage-batch"][data-batch-id="' + batch_id + '"]');
    const kind = btn ? btn.dataset.kind : "";
    let expiring_date_ddmmyyyy = null;
    if (kind === 'Stock solution' && status === 'Available') {
      const expEl = document.getElementById('mg_exp');
      if (!expEl || !expEl.value.trim()) { alert('Please provide a new expiring date (dd/mm/yyyy)'); return; }
      expiring_date_ddmmyyyy = expEl.value.trim();
    }
    Overlay.show();
    try {
      const r = await fetch(`/batches/manage/${batch_id}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ location: loc, sublocation, status, expiring_date_ddmmyyyy })
      });
      const txt = await r.text(); let data; try { data = JSON.parse(txt) } catch (e) { throw new Error(txt) }
      if (!data.ok) throw new Error(data.error || 'Failed');
      closeModal(); window.location.reload();
    } catch (e) { alert(e.message) } finally { Overlay.hide() }
  },

  openQCAnalysis(batch_id) {
    modal(`
      <div class="modal">
        <div class="modal-card">
          <div class="modal-header"><h3>QC Analysis for ${batch_id}</h3><button class="icon-btn" onclick="closeModal()">✕</button></div>
          <div class="modal-body">
            <div class="form slim">
              <p>Upload .rpt file to run QC analysis.</p>
              <div class="drop-zone" id="drop_zone" style="border: 2px dashed var(--border); padding: 20px; text-align: center; cursor: pointer; border-radius: 8px;">
                <p>Drag & Drop or Click to Upload</p>
                <input type="file" id="rpt_file" accept=".rpt" style="display: none;">
              </div>
              <div id="file_name" style="margin-top: 10px; font-style: italic; color: var(--muted);"></div>
            </div>
          </div>
          <div class="modal-actions">
            <button class="btn" onclick="BatchesUI.runQCAnalysis('${batch_id}')">Run Analysis</button>
            <button class="btn small" onclick="closeModal()">Cancel</button>
          </div>
        </div>
      </div>
    `);

    const dropZone = document.getElementById('drop_zone');
    const fileInput = document.getElementById('rpt_file');

    dropZone.onclick = () => fileInput.click();

    fileInput.onchange = () => {
      if (fileInput.files.length) {
        document.getElementById('file_name').textContent = fileInput.files[0].name;
      }
    };

    dropZone.ondragover = (e) => { e.preventDefault(); dropZone.style.borderColor = 'var(--accent)'; };
    dropZone.ondragleave = () => { dropZone.style.borderColor = 'var(--border)'; };
    dropZone.ondrop = (e) => {
      e.preventDefault();
      dropZone.style.borderColor = 'var(--border)';
      if (e.dataTransfer.files.length) {
        fileInput.files = e.dataTransfer.files;
        document.getElementById('file_name').textContent = e.dataTransfer.files[0].name;
      }
    };
  },

  async runQCAnalysis(batch_id) {
    const fileInput = document.getElementById('rpt_file');
    if (!fileInput.files.length) { alert("Please select a file"); return; }

    const formData = new FormData();
    formData.append('file', fileInput.files[0]);

    Overlay.show();
    try {
      const r = await fetch(`/batches/${batch_id}/qc_analysis`, {
        method: 'POST',
        body: formData
      });
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'Analysis failed');

      alert(`Analysis Complete!\nFound: ${data.result.chem_found}\nPurity: ${data.result.purity} (${data.result.purity_percent.toFixed(1)}%)`);
      closeModal();
      location.reload();
    } catch (e) { alert(e.message); }
    finally { Overlay.hide(); }
  },

  async openQCResults(batch_id) {
    Overlay.show();
    try {
      const r = await fetch(`/batches/${batch_id}/qc_results`);
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'Failed to fetch results');

      const item = data.item;
      modal(`
        <div class="modal">
          <div class="modal-card">
            <div class="modal-header"><h3>QC Results: ${batch_id}</h3><button class="icon-btn" onclick="closeModal()">✕</button></div>
            <div class="modal-body">
              <table class="grid">
                <tr><th>Chemical Found</th><td>${item.chem_found}</td></tr>
                <tr><th>Found Mass</th><td>${item.found_mass ? item.found_mass.toFixed(4) : '-'}</td></tr>
                <tr><th>Purity</th><td>${item.purity} (${item.purity_percent ? item.purity_percent.toFixed(1) : '0'}%)</td></tr>
                <tr><th>Retention Time</th><td>${item.retention_time ? item.retention_time.toFixed(2) : '-'} min</td></tr>
                <tr><th>Analyzed At</th><td>${item.created_at}</td></tr>
                <tr><th>File</th><td>${item.filename}</td></tr>
              </table>
            </div>
            <div class="modal-actions">
              <button class="btn" onclick="closeModal()">Close</button>
            </div>
          </div>
        </div>
      `);
    } catch (e) { alert(e.message); }
    finally { Overlay.hide(); }
  }
};

/* ===== Plate UI + Preview ===== */
/* (keep your PlateUI and PlatePreview code as-is) */

/* ===== Global action delegation ===== */
document.addEventListener('click', async (e) => {
  const a = e.target.closest('[data-action]');
  if (!a) return;
  const act = a.getAttribute('data-action');

  switch (act) {
    case 'open-new-chemical': e.preventDefault(); ChemUI.openNewChemical(); break;
    case 'open-multi-entry': e.preventDefault(); ChemUI.openMultiEntry(); break;
    case 'open-availability': e.preventDefault(); ChemUI.openAvailability(a.dataset.chemId); break;

    // Edit
    case 'chem-edit': {
      e.preventDefault();
      const chemId = getDataAttr(a, 'chemId') || a.dataset.chemId;
      if (!chemId) { alert('Missing chem_id'); break; }
      ChemUI.openEdit(chemId);
      break;
    }
    case 'chem-edit-save': {
      e.preventDefault();
      const id = a.dataset.id;
      ChemUI.saveEdit(id);
      break;
    }

    // Bottles & Batches
    case 'open-generate-batch': {
      e.preventDefault();
      const chemId = getDataAttr(a, 'chemId');
      const bottleId = getDataAttr(a, 'bottleId');
      if (!chemId || !bottleId) { alert('Could not determine chem_id or bottle_id for this row.'); return; }
      BatchesUI.openGenerateBatch(chemId, bottleId);
      break;
    }
    case 'open-generate-bottle': e.preventDefault(); BottlesUI.openGenerateBottle(a.dataset.chemId); break;
    case 'save-chemical': e.preventDefault(); ChemUI.save(); break;
    case 'save-bottle': e.preventDefault(); BottlesUI.saveBottle(a.dataset.chemId); break;

    case 'save-batch': {
      e.preventDefault();
      const locked = a.dataset.typeLocked === '1';
      BatchesUI.save(a.dataset.chemId, parseInt(a.dataset.bottleNo, 10), locked);
      break;
    }

    case 'open-manage-batch': e.preventDefault();
      BatchesUI.openManage(a.dataset.batchId, a.dataset.location, a.dataset.sublocation, a.dataset.status, a.dataset.kind, a.dataset.exp); break;

    case 'save-manage-batch': e.preventDefault(); BatchesUI.saveManage(a.dataset.batchId); break;

    case 'open-qc-analysis': e.preventDefault(); BatchesUI.openQCAnalysis(a.dataset.batchId); break;
    case 'open-qc-results': e.preventDefault(); BatchesUI.openQCResults(a.dataset.batchId); break;

    case 'plate-save-modal': e.preventDefault(); PlateUI?.closeModal?.(); break; /* assignments already saved live */

    case 'pick-supplier': e.preventDefault(); BottlesUI.pickSupplier(a.dataset.supplier); break;
    case 'add-supplier': e.preventDefault(); BottlesUI.addSupplier(a.dataset.supplier); break;

    case 'open-preview': e.preventDefault(); ChemUI.openPreview(a.dataset.chemId); break;
  }
});

/* Supplier autocomplete wiring */
document.addEventListener('input', (e) => {
  if (e.target && e.target.id === 'supplier_input') { BottlesUI.filterSuppliers(e.target.value); }
});

/* Batch type & status dependent UI */
document.addEventListener('change', (e) => {
  if (e.target && e.target.id === 'bt_Type') { BatchesUI.onTypeChange(); }
  if (e.target && e.target.id === 'mg_status') {
    const saveBtn = document.querySelector('[data-action="save-manage-batch"]');
    const kind = saveBtn ? saveBtn.dataset.kind : "";
    const row = document.getElementById('mg_exp_row');
    if (row) {
      row.style.display = (kind === 'Stock solution' && e.target.value === 'Available') ? 'block' : 'none';
    }
  }
});

/* Supplier list on focus */
document.addEventListener('focusin', async (e) => {
  if (e.target && e.target.id === 'supplier_input') {
    const list = document.getElementById('supplier_list');
    if (list) {
      const names = await listSuppliers("");
      list.innerHTML = names.map(n => `<div class="option" data-action="pick-supplier" data-supplier="${n.replace(/"/g, '&quot;')}">${n}</div>`).join('');
      list.style.display = 'block';
    }
  }
});


/* =========================================
   Plate Designer Logic
   ========================================= */
/* =========================================
   Plate Designer Logic
   ========================================= */
/* =========================================
   Plate Designer Logic
   ========================================= */
/* =========================================
   Plate Designer Logic (Refactored)
   ========================================= */
const PlateDesigner = {
  // State
  designId: null,
  designName: "",
  plateSize: 96,
  assignments: {}, // Key: "A1", Value: [{role, chemId, name, eq, fraction...}]
  selection: new Set(),
  metadata: {
    eln_id: "", rxn_date: "", atmosphere: "", mixing: "no", reaction_tech: "standard",
    wavelength: "", scale: "", concentration: ""
  },

  // Roles config (Neon Colors)
  // These should match CSS vars but we need hex for some JS manipulation if needed, or just class names.
  // We'll use CSS vars directly in styles.
  roles: {
    solvent: { label: "Solvent", color: "var(--role-solvent)", css: "solvent" },
    ligand: { label: "Ligand", color: "var(--role-ligand)", css: "ligand" },
    catalyst_ni: { label: "Cat. Ni", color: "var(--role-catalyst)", css: "catalyst" },
    catalyst_ir: { label: "Cat. Ir", color: "var(--role-catalyst)", css: "catalyst" },
    reagent: { label: "Reagent", color: "var(--role-reagent)", css: "reagent" },
    oxidant: { label: "Oxidant", color: "var(--role-oxidant)", css: "reagent" },
    additive: { label: "Additive", color: "var(--role-additive)", css: "additive" },
    acid: { label: "Acid", color: "var(--role-acid)", css: "reagent" },
    base: { label: "Base", color: "var(--role-base)", css: "reagent" },
    startingmat: { label: "Start. Mat.", color: "var(--role-starting_material)", css: "starting_material" }
  },

  // Init
  init() {
    if (document.getElementById('pdApp')) {
      console.log("Initializing PlateDesigner...");
      this.bindEvents();
      // Default Date
      document.getElementById('meta_date').valueAsDate = new Date();
      this.updateMeta('rxn_date', document.getElementById('meta_date').value);
      this.setSize(96);
    }
  },

  bindEvents() {
    // Close search results on click outside
    document.addEventListener('click', (e) => {
      if (!e.target.closest('#asChemSearch') && !e.target.closest('#asSearchResults')) {
        const sr = document.getElementById('asSearchResults');
        if (sr) sr.style.display = 'none';
      }
    });

    // Debounced Search
    const searchInp = document.getElementById('asChemSearch');
    if (searchInp) {
      searchInp.addEventListener('input', (e) => this.searchChem(e.target.value));
      searchInp.addEventListener('focus', (e) => { if (e.target.value) this.searchChem(e.target.value); });
    }
  },

  setSize(size) {
    const newSize = parseInt(size);
    if (this.plateSize !== newSize && Object.keys(this.assignments).length > 0) {
      if (!confirm(`Changing size to ${newSize} will clear current assignments. Continue?`)) {
        document.getElementById('pdSize').value = this.plateSize;
        return;
      }
      this.assignments = {};
      this.selection.clear();
    }
    this.plateSize = newSize;
    this.renderGrid();
  },

  // --- Rendering ---

  renderGrid() {
    const grid = document.getElementById('pdGrid');
    if (!grid) return;

    // Config
    let rows = 8, cols = 12;
    let rowLabels = "ABCDEFGH".split("");
    if (this.plateSize === 48) { rows = 6; cols = 8; rowLabels = "ABCDEF".split(""); }
    if (this.plateSize === 24) { rows = 4; cols = 6; rowLabels = "ABCD".split(""); }

    // Grid Template: 
    // Cols: Header Col + N Data Cols
    // Rows: 3 Header Rows (Group, Badge, Num) + N Data Rows
    grid.style.gridTemplateColumns = `40px repeat(${cols}, 48px)`;
    grid.innerHTML = "";

    // --- Header Row 1: Groups (Simplified: Just Spacers for now, or unified label) ---
    // Top-Left corner for 3 rows
    const tl = document.createElement('div');
    tl.style.gridRow = "span 3";
    tl.style.gridColumn = "1";
    tl.className = "pd-hdr-num";
    tl.innerHTML = `<span style="font-size:10px; opacity:0.5;">${this.plateSize}W</span>`;
    tl.onclick = () => this.selectAll();
    grid.appendChild(tl);

    // For simplicity in this version, we will perform a scan to see if columns share common chems
    // And render badges in Row 2. Row 1 can be generic role label if needed.

    // Row 1: "Top Labels" (Placeholder or Functional Grouping)
    for (let c = 1; c <= cols; c++) {
      const el = document.createElement('div');
      el.className = "pd-hdr-group";
      el.textContent = ""; // Could put 'Solvent' if col is pure solvent
      grid.appendChild(el);
    }

    // Row 2: Badges (Dynamic Chem ID)
    for (let c = 1; c <= cols; c++) {
      const badge = document.createElement('div');
      badge.className = "pd-hdr-badge";
      // Check for common chem in this col
      const common = this.findCommonChem('col', c, rows, rowLabels);
      if (common) {
        badge.textContent = common.name || common.id;
        badge.title = common.name;
        badge.style.borderColor = "var(--accent)";
      } else {
        badge.innerHTML = "&darr;"; // Indicator
        badge.style.opacity = "0.2";
      }
      badge.onclick = () => this.selectCol(c, rows);
      grid.appendChild(badge);
    }

    // Row 3: Numbers
    for (let c = 1; c <= cols; c++) {
      const el = document.createElement('div');
      el.className = "pd-hdr-num";
      el.textContent = c;
      el.onclick = () => this.selectCol(c, rows);
      grid.appendChild(el);
    }

    // --- Data Rows ---
    for (let r = 0; r < rows; r++) {
      const rChar = rowLabels[r];

      // Row Header (Char + Badge)
      const rh = document.createElement('div');
      rh.className = "pd-hdr-row";

      // Check common chem
      const common = this.findCommonChem('row', rChar, cols);
      let badgeHtml = "";
      if (common) {
        badgeHtml = `<div class="pd-hdr-badge" style="display:inline-flex; width:auto; padding:0 4px; height:18px; font-size:9px; border-color:var(--accent);">${common.id}</div>`;
      }

      rh.innerHTML = `<span>${rChar}</span> ${badgeHtml}`;
      rh.onclick = () => this.selectRow(rChar, cols);
      grid.appendChild(rh);

      // Wells
      for (let c = 1; c <= cols; c++) {
        const wid = `${rChar}${c}`;
        const well = document.createElement('div');
        well.className = "pd-well";
        well.dataset.wid = wid;

        // Content
        const inner = document.createElement('div');
        inner.className = "pd-well-inner";
        this.renderWellContent(inner, wid);
        well.appendChild(inner);

        // Events
        well.onclick = (e) => this.toggleWell(wid, e.ctrlKey || e.metaKey); // Ctrl logic
        well.onmouseenter = () => this.inspectWell(wid);
        well.onmouseleave = () => this.inspectWell(null);

        // Selection
        if (this.selection.has(wid)) well.classList.add('selected');

        grid.appendChild(well);
      }
    }

    this.updateLegend();
  },

  renderWellContent(el, wid) {
    const data = this.assignments[wid];
    if (!data || data.length === 0) {
      el.style.background = "";
      return;
    }

    // Visualization Priority
    // 1. If single component, full color.
    // 2. If multiple, maybe gradient? Or just "Main" role color.
    // Req: "Fill with role-corresponding neon colors"

    // Let's find the "Highest Priority" role present
    const priority = ['catalyst_ni', 'catalyst_ir', 'oxidant', 'ligand', 'reagent', 'startingmat', 'base', 'acid', 'additive', 'solvent'];

    // Sort assignments by priority
    const sorted = [...data].sort((a, b) => {
      return priority.indexOf(a.role) - priority.indexOf(b.role);
    });

    const main = sorted[0];
    const roleConf = this.roles[main.role];
    const color = roleConf ? roleConf.color : "#fff";

    el.style.backgroundColor = color;

    // If multiple components, maybe add a small indicator or border?
    // Current CSS opacity is 0.8.
    if (data.length > 1) {
      el.style.boxShadow = "inset 0 0 0 3px rgba(0,0,0,0.3)";
    }
  },

  findCommonChem(type, index, max, rowLabels) {
    // Check if all wells in this col/row share a specific component (e.g. same Solvent or same Catalyst)
    // We prioritize "Variable" components usually.
    // Normalized check: Do ALL wells have chem X?
    // Simplification: Check the FIRST role assignment of the FIRST well, see if others match.

    let wells = [];
    if (type === 'col') {
      for (let r = 0; r < 8; r++) { // Assume max 8 for 'H' check or use passed max
        if (r >= "ABCDEFGH".split("").length) break; // safety
        // Wait, headers are A..
        let rLabels = rowLabels || "ABCDEFGH".split("");
        if (r < rLabels.length) wells.push(`${rLabels[r]}${index}`);
      }
    } else {
      // Row
      for (let c = 1; c <= max; c++) wells.push(`${index}${c}`);
    }

    // Get assignments for first well
    const firstW = wells[0];
    const firstAss = this.assignments[firstW];
    if (!firstAss || firstAss.length === 0) return null;

    // Try to find a chem present in ALL wells
    // Candidates are chems in first well
    for (let cand of firstAss) {
      let allHave = true;
      for (let w of wells) {
        const wAss = this.assignments[w];
        if (!wAss || !wAss.find(x => x.chemId === cand.chemId)) {
          allHave = false; break;
        }
      }
      if (allHave) return { id: cand.chemId, name: cand.name };
    }
    return null;
  },

  // --- Interaction ---
  toggleWell(wid, multi) {
    if (multi) {
      if (this.selection.has(wid)) this.selection.delete(wid);
      else this.selection.add(wid);
    } else {
      this.selection.clear();
      this.selection.add(wid);
    }
    this.renderGrid();
    this.updateScopeLabel();
  },

  selectRow(rChar, cols) {
    // Add to selection
    for (let c = 1; c <= cols; c++) this.selection.add(`${rChar}${c}`);
    this.renderGrid();
    this.updateScopeLabel();
  },

  selectCol(cNum, rows) {
    const chars = "ABCDEFGH".substring(0, rows);
    for (let char of chars) this.selection.add(`${char}${cNum}`);
    this.renderGrid();
    this.updateScopeLabel();
  },

  selectAll() {
    document.querySelectorAll('.pd-well').forEach(el => this.selection.add(el.dataset.wid));
    this.renderGrid();
    this.updateScopeLabel();
  },

  clearAll() {
    if (confirm("Clear entire design?")) {
      this.assignments = {};
      this.selection.clear();
      this.designName = "";
      this.updateName("");
      this.renderGrid();
    }
  },

  updateScopeLabel() {
    const lbl = document.getElementById('asScopeLabel');
    lbl.textContent = `SELECTION (${this.selection.size})`;
  },

  updateName(val) {
    this.designName = val;
    document.getElementById('pdName').value = val;
  },

  setSize(val) {
    this.plateSize = parseInt(val);
    // assignments ok?
    this.renderGrid();
  },

  updateMeta(key, val) {
    this.metadata[key] = val;
    if (key === 'reaction_tech') {
      const grp = document.getElementById('meta_wave_group');
      grp.style.display = (val === 'photochemistry') ? 'block' : 'none';
    }
  },

  // --- Assignments ---

  searchChem(q) {
    const resDiv = document.getElementById('asSearchResults');
    if (!q || q.length < 2) { resDiv.style.display = 'none'; return; }

    if (this._tm) clearTimeout(this._tm);
    this._tm = setTimeout(async () => {
      try {
        const r = await fetch(`/api/chemicals/search?q=${encodeURIComponent(q)}`);
        const d = await r.json();
        if (d.ok && d.results.length > 0) {
          resDiv.innerHTML = d.results.map(item => `
               <div class="pd-search-row" onclick="PlateDesigner.selectChem('${item.chem_id}', '${item.common_name || item.chem_id}')">
                 <div>
                   <div style="font-weight:700; color:white;">${item.common_name || item.chem_id}</div>
                   <div style="font-size:10px; color:#aaa;">${item.chem_id} • ${item.cas || ''}</div>
                 </div>
                 <div style="font-size:10px; opacity:0.5;">Select</div>
               </div>
            `).join('');
          resDiv.style.display = 'block';
        } else {
          resDiv.style.display = 'none';
        }
      } catch (e) { console.error(e); }
    }, 300);
  },

  selectChem(id, name) {
    const inp = document.getElementById('asChemSearch');
    inp.value = name;
    inp.dataset.id = id;
    inp.dataset.name = name;
    document.getElementById('asSearchResults').style.display = 'none';
  },

  onRoleChange(val) {
    const frac = document.getElementById('asFractionGroup');
    frac.style.display = (val === 'solvent') ? 'block' : 'none';
  },

  applyAssignment() {
    const wells = Array.from(this.selection);
    if (!wells.length) { alert("Select wells first."); return; }

    const role = document.getElementById('asRole').value;
    const inp = document.getElementById('asChemSearch');
    const chemId = inp.dataset.id || inp.value; // Fallback
    const name = inp.dataset.name || chemId;

    if (!chemId) { alert("Enter a chemical."); return; }

    const eq = parseFloat(document.getElementById('asEq').value) || 0;

    const entry = { role, chemId, name, eq };
    if (role === 'solvent') {
      entry.fraction = parseFloat(document.getElementById('asFrac').value) || 0;
    }

    wells.forEach(w => {
      if (!this.assignments[w]) this.assignments[w] = [];
      // Check duplicate role?
      // Rem current if exists? No, additive. user can remove via inspector.
      this.assignments[w].push(entry);
    });

    this.renderGrid();
  },

  // --- Inspector ---

  inspectWell(wid) {
    const title = document.getElementById('inspWell');
    const div = document.getElementById('inspContent');

    if (!wid) {
      // If selection exists, maybe show summary? For now clear.
      // title.textContent = ""; 
      return;
    }
    title.textContent = "Well " + wid;
    div.innerHTML = "";

    const data = this.assignments[wid];
    if (!data || !data.length) {
      div.innerHTML = "<div style='opacity:0.5; text-align:center;'>Empty</div>";
      return;
    }

    data.forEach((item, idx) => {
      const rc = this.roles[item.role] || {};
      div.innerHTML += `
         <div class="pd-panel" style="background:#222; margin-bottom:8px; border:1px solid #444;">
            <div style="padding:8px; display:flex; justify-content:space-between; align-items:center;">
               <div style="display:flex; gap:8px; align-items:center;">
                  <div style="width:10px; height:10px; border-radius:50%; background:${rc.color}"></div>
                  <div>
                     <div style="font-weight:700; color:white;">${item.name}</div>
                     <div style="font-size:10px; color:#aaa;">${rc.label} • ${item.eq} eq</div>
                  </div>
               </div>
               <button class="icon-btn tiny" style="color:#f55;" onclick="PlateDesigner.removeAs('${wid}', ${idx})">✕</button>
            </div>
         </div>
       `;
    });
  },

  removeAs(wid, idx) {
    if (this.assignments[wid]) {
      this.assignments[wid].splice(idx, 1);
      if (this.assignments[wid].length === 0) delete this.assignments[wid];
    }
    this.renderGrid();
    this.inspectWell(wid);
  },

  updateLegend() {
    const leg = document.getElementById('pdLegend');
    leg.innerHTML = Object.values(this.roles).map(r => `
       <div style="display:flex; align-items:center; gap:4px;">
          <div style="width:8px; height:8px; background:${r.color}; border-radius:50%;"></div>
          ${r.label}
       </div>
     `).join('');
  },

  // --- IO ---

  async saveDesign() {
    const name = document.getElementById('pdName').value;
    if (!name) { alert("Enter Experiment Name"); return; }

    // Backend Format
    const payload = {
      name: name,
      plate_type: this.plateSize,
      plate_metadata: this.metadata,
      assignments: {} // convert list to map if needed, typically JSON is fine
    };
    // Our assignments structure matches what we want to save roughly, 
    // but let's conform to what `plate_save` likely expects if it parses specifically.
    // `plate_save` loop iterates `assignments`. 
    payload.assignments = this.assignments;

    try {
      const r = await fetch('/api/plates', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const d = await r.json();
      if (d.ok) alert("Saved! ID: " + d.id);
      else alert("Error: " + d.error);
    } catch (e) { alert(e); }
  },

  exportSURF() {
    const name = document.getElementById('pdName').value || "Design";
    const payload = {
      name: name,
      plate_type: this.plateSize,
      assignments: this.assignments,
      meta: this.metadata
    };

    fetch('/plates/export_surf', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
    })
      .then(r => r.blob())
      .then(blob => {
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `SURF_${name}.xlsx`;
        document.body.appendChild(a);
        a.click();
        a.remove();
      })
      .catch(e => alert(e));
  },

  loadModal() {
    // TODO: Load Logic
    alert("Load feature coming soon.");
  }
};

// Initialize
document.addEventListener('DOMContentLoaded', () => {
  PlateDesigner.init();
});



/* ===== Batch UI ===== */
const BatchUI = window.BatchUI = {
  openMultiManage() {
    modal(`
      <div class="modal">
        <div class="modal-card" style="width: 900px; height: 80vh; max-height: 800px; display: flex; flex-direction: column;">
          <div class="modal-header">
            <h3>Batch Multi-Manage</h3>
            <button class="icon-btn" onclick="closeModal()">✕</button>
          </div>
          <div class="modal-body" style="flex:1; overflow:hidden; display:flex; flex-direction:column; gap: 15px; padding: 20px;">
            <!-- Step 1: Search -->
            <div style="display:flex; gap:15px; align-items:flex-start;">
                <div style="flex:1;">
                    <label style="display:block; margin-bottom:5px; font-weight:600; color:var(--text-main);">Search Targets</label>
                    <!-- Requirement 1: Styled Input matching global theme -->
                    <textarea id="mmInput" class="simple-input" rows="3" placeholder="Paste list of Chem_IDs (e.g. 11, chem_12) or Batch_IDs..." style="width:100%; resize:vertical; font-family:var(--font-main); background:var(--bg-input); color:var(--text-main); border:1px solid var(--border);"></textarea>
                </div>
                <div style="display:flex; flex-direction:column; gap:8px; margin-top:24px;">
                    <button class="btn primary" onclick="BatchUI.searchMultiple()">Search Batches</button>
                    <button class="btn secondary" onclick="document.getElementById('mmInput').value=''">Clear Input</button>
                </div>
            </div>
            
            <!-- Step 2: Results & Actions -->
            <div id="mmResultsArea" style="flex:1; display:none; flex-direction:column; overflow:hidden; border-top:1px solid var(--border); padding-top:15px;">
                
                <!-- Action Bar -->
                <div class="card" style="margin-bottom:15px; padding:10px; background:var(--bg-subtle); display:flex; flex-wrap:wrap; gap:15px; align-items:center;">
                    <div style="display:flex; gap:8px; align-items:center; border-right:1px solid var(--border); padding-right:15px;">
                        <input type="checkbox" id="mmSelectAll" onchange="BatchUI.toggleAll(this)">
                        <label for="mmSelectAll" style="font-weight:600; cursor:pointer;">Select All</label>
                        <span id="mmCount" style="color:var(--text-muted); font-size:0.9em; margin-left:5px;">(0 selected)</span>
                    </div>

                    <div style="flex:1; display:flex; gap:10px; align-items:center;">
                        <span style="font-weight:600; color:var(--text-main);">Bulk Action:</span>
                        <select id="mmActionType" class="themed-select" onchange="BatchUI.updateActionInputs()" style="width:160px;">
                            <option value="">-- Choose Action --</option>
                            <option value="location">Update Location</option>
                            <option value="status">Update Status</option>
                            <option value="amount">Update Amount</option>
                        </select>
                        
                        <!-- Dynamic Inputs Container -->
                        <div id="mmActionInputs" style="display:flex; gap:10px; align-items:center;"></div>
                        
                        <div style="flex:1"></div>
                        <button class="btn primary" onclick="BatchUI.applyBulk()">Apply Changes</button>
                    </div>
                </div>

                <!-- Results Table -->
                <div class="table-wrap" style="flex:1; overflow:auto; border:1px solid var(--border); border-radius:8px; background:var(--bg-surface);">
                    <table class="grid" id="mmTable">
                        <thead style="position:sticky; top:0; z-index:1;">
                            <tr>
                                <th style="width:40px; text-align:center;">✓</th>
                                <th>Batch ID</th>
                                <th>Chem ID</th>
                                <th>Location</th>
                                <th>Status</th>
                                <th>Amount</th>
                            </tr>
                        </thead>
                        <tbody></tbody>
                    </table>
                </div>
            </div>
          </div>
          <div class="modal-actions" style="border-top:1px solid var(--border); padding:15px; background:var(--bg-subtle);">
            <button class="btn secondary" onclick="closeModal()">Close</button>
          </div>
        </div>
      </div>
    `);
  },

  async searchMultiple() {
    const text = document.getElementById('mmInput').value;
    // Requirement 2: Smart Parsing
    // 1. Split by newlines or commas
    // 2. Trim whitespace
    // 3. Lowercase everything (target is chem_id or batch_id)
    // 4. If it's just numbers, prepend 'chem_'

    let ids = text.split(/[\n,]+/).map(s => s.trim()).filter(s => s);

    ids = ids.map(id => {
      // Check if pure number
      if (/^\d+$/.test(id)) {
        return `chem_${id}`;
      }
      return id.toLowerCase();
    });

    if (!ids.length) { alert("Please enter at least one ID."); return; }

    Overlay.show();
    try {
      const r = await fetch('/batches/search_multiple', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids })
      });
      const data = await r.json();
      if (!data.ok) throw new Error(data.error);

      // Render
      const tbody = document.querySelector('#mmTable tbody');
      if (!data.items.length) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:20px; color:var(--text-muted);">No matching batches found.</td></tr>';
      } else {
        tbody.innerHTML = data.items.map(b => `
            <tr>
                <td style="text-align:center;"><input type="checkbox" class="mm-check" value="${b.batch_id}" onchange="BatchUI.updateCount()"></td>
                <td style="font-family:monospace; font-weight:600;">${b.batch_id}</td>
                <td>${b.chem_id}</td>
                <td>${b.location} ${b.sublocation ? '/ ' + b.sublocation : ''}</td>
                <td><span class="badge ${b.status === 'Available' ? 'success' : ''}">${b.status}</span></td>
                <td>${b.amount || '-'}</td>
            </tr>
          `).join('');
      }

      document.getElementById('mmResultsArea').style.display = 'flex';
      BatchUI.updateCount();

    } catch (e) { alert(e.message); }
    finally { Overlay.hide(); }
  },

  toggleAll(master) {
    document.querySelectorAll('.mm-check').forEach(cb => cb.checked = master.checked);
    BatchUI.updateCount();
  },

  updateCount() {
    const n = document.querySelectorAll('.mm-check:checked').length;
    document.getElementById('mmCount').textContent = `(${n} selected)`;
  },

  updateActionInputs() {
    const type = document.getElementById('mmActionType').value;
    const container = document.getElementById('mmActionInputs');
    container.innerHTML = '';

    if (type === 'location') {
      container.innerHTML = `
            <input id="mmLoc" placeholder="Location" class="simple-input" style="width:140px">
            <input id="mmSub" placeholder="Sublocation" class="simple-input" style="width:100px">
          `;
    } else if (type === 'status') {
      container.innerHTML = `
            <select id="mmStatus" class="themed-select" onchange="BatchUI.handleStatusChange(this)" style="min-width:140px;">
                <option value="Available">Available</option>
                <option value="Expired">Expired</option>
                <option value="Stock Room">Stock Room</option>
                <option value="Empty">Empty</option>
                <option value="Lent">Lent</option>
            </select>
            
            <!-- Dynamic conditional inputs -->
            <!-- Requirement 4: Hidden if Stock Room, Required if Available -->
            <div id="mmStatusLocationGroup" style="display:none; align-items:center; gap:5px; margin-left:10px;">
                <input id="mmStatusLoc" placeholder="Location *" class="simple-input" style="width:120px; border-color:var(--primary);">
                <input id="mmStatusSub" placeholder="Sub" class="simple-input" style="width:80px">
            </div>

            <!-- Auto-set logic feedback -->
            <div id="mmStatusExtra" style="display:none; align-items:center; gap:5px; font-size:0.9em; color:var(--text-muted); padding-left:10px; border-left:1px solid var(--border);">
                <span>⮕ Auto-set Location:</span>
                <input id="mmExtraLoc" class="simple-input" style="width:100px; background:var(--bg-subtle);" readonly>
                <input id="mmExtraSub" class="simple-input" style="width:80px; background:var(--bg-subtle);" readonly placeholder="(cleared)">
            </div>
          `;
      // Init check
      BatchUI.handleStatusChange(document.getElementById('mmStatus'));
    } else if (type === 'amount') {
      container.innerHTML = `<input id="mmAmount" placeholder="New Amount" class="simple-input" style="width:120px">`;
    }
  },

  handleStatusChange(selectInfo) {
    const val = selectInfo.value;
    const locGroup = document.getElementById('mmStatusLocationGroup'); // available logic
    const extraDiv = document.getElementById('mmStatusExtra'); // stock room logic
    const extraLoc = document.getElementById('mmExtraLoc');
    const extraSub = document.getElementById('mmExtraSub');

    // Reset
    locGroup.style.display = 'none';
    extraDiv.style.display = 'none';

    if (val === 'Stock Room') {
      // Requirement 4: Hide input, show auto-set visual
      extraDiv.style.display = 'flex';
      extraLoc.value = 'Stock Room';
      extraSub.value = '';
    }
    else if (val === 'Available') {
      // Requirement 3: Show Location Input (Marked as required conceptually)
      locGroup.style.display = 'flex';
    }
  },

  async applyBulk() {
    const type = document.getElementById('mmActionType').value;
    if (!type) { alert("Please select an action type."); return; }

    const batch_ids = Array.from(document.querySelectorAll('.mm-check:checked')).map(cb => cb.value);
    if (!batch_ids.length) { alert("No batches selected to update."); return; }

    const updates = {};
    let confirmMsg = "";

    if (type === 'location') {
      const loc = document.getElementById('mmLoc').value.trim();
      const sub = document.getElementById('mmSub').value.trim();
      updates.location = loc;
      updates.sublocation = sub || null;
      confirmMsg = `Update Location to "${loc}"` + (sub ? ` / "${sub}"` : "") + ` for ${batch_ids.length} batches?`;

    } else if (type === 'status') {
      const st = document.getElementById('mmStatus').value;
      updates.status = st;

      if (st === 'Stock Room') {
        updates.location = 'Stock Room';
        updates.sublocation = null;
        confirmMsg = `Set Status AND Location to "Stock Room" (clearing sublocation) for ${batch_ids.length} batches?`;
      }
      else if (st === 'Available') {
        // Requirement 3: Validation
        const loc = document.getElementById('mmStatusLoc').value.trim();
        const sub = document.getElementById('mmStatusSub').value.trim();

        if (!loc) {
          alert("Location is MANDATORY when Status is 'Available'.");
          return;
        }

        updates.location = loc;
        updates.sublocation = sub || null; // Optional
        confirmMsg = `Set Status to "Available" and Location to "${loc}" for ${batch_ids.length} batches?`;
      }
      else {
        // For others (Expired, Empty, Lent), we typically don't force location change, but strictly speaking requirement only mentioned Available/Stock Room.
        // We'll leave location untouched for others unless spec proceeds.
        confirmMsg = `Update Status to "${st}" for ${batch_ids.length} batches?`;
      }

    } else if (type === 'amount') {
      const amt = document.getElementById('mmAmount').value.trim();
      updates.amount = amt;
      confirmMsg = `Update Amount to "${amt}" for ${batch_ids.length} batches?`;
    }

    if (!confirm(confirmMsg)) return;

    Overlay.show();
    try {
      const r = await fetch('/batches/bulk_update', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ batch_ids, updates })
      });
      const d = await r.json();
      if (!d.ok) throw new Error(d.error);

      alert(`Successfully updated ${d.updated} batches.`);
      BatchUI.searchMultiple(); // Refresh list to show changes

    } catch (e) { alert(e.message); }
    finally { Overlay.hide(); }
  }
};

// Init PlateDesigner if on page
document.addEventListener('DOMContentLoaded', () => {
  PlateDesigner.init();
});

