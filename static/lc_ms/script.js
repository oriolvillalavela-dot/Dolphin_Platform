/* ---------- IPC dynamic rows ---------- */
function addIpcRow() {
  const c = document.querySelector('#ipcRows');
  const row = document.createElement('div');
  row.className = 'row-inline';
  row.style.marginBottom = '10px';
  row.style.alignItems = 'flex-end';
  row.innerHTML = `
    <label style="flex: 2;"><span>ELN ID</span><input name="eln_id[]" placeholder="ELN…" required></label>
    <label style="flex: 1;"><span>IPC no.</span><input name="exp_no[]" placeholder="1" value="1"></label>
    <label style="flex: 1;"><span>Duration (h)</span><input name="duration_h[]" placeholder="1" value="1"></label>
    <button type="button" class="btn ghost" onclick="removeRow(this)" style="margin-bottom: 10px;">✕</button>
  `;
  c.appendChild(row);
  row.scrollIntoView({ behavior: 'smooth', block: 'center' });
}
function removeRow(btn) {
  const row = btn.closest('.row-inline');
  if (row) { row.remove(); }
}

/* ---------- Form validation ---------- */
function validateNewELN(form) {
  const elnEl = form.querySelector(`[name="eln_id"]`);
  const chemEl = form.querySelector(`[name="chemist"]`);
  if (!elnEl.value.trim()) { alert('Please fill mandatory fields.'); return false; }
  if (!chemEl.value.trim() || chemEl.value === '__manage__') {
    if (chemEl.value === '__manage__') openChemistsModal();
    alert('Please choose a chemist.'); return false;
  }
  const anyChem = ['stmat_1_chemform', 'stmat_2_chemform', 'product_1_chemform', 'product_2_chemform', 'product_3_chemform', 'product_4_chemform']
    .some(n => (form.querySelector(`[name="${n}"]`).value || '').trim().length > 0);
  if (!anyChem) { alert('Provide at least one molecular formula.'); return false; }
  return true;
}

/* ---------- Chemists modal + API ---------- */
let lastChemistSelection = null;
function onChemistSelectChange(sel) {
  if (sel.value === '__manage__') {
    if (lastChemistSelection) { sel.value = lastChemistSelection; }
    else if (sel.options.length > 0) { sel.selectedIndex = 0; }
    openChemistsModal();
  } else { lastChemistSelection = sel.value; }
}
function openChemistsModal() {
  const modal = document.getElementById('chemistsModal');
  modal.classList.remove('hidden');
  refreshChemistsUI();
}
function closeChemistsModal() {
  const modal = document.getElementById('chemistsModal');
  modal.classList.add('hidden');
}
async function refreshChemistsUI() {
  try {
    const res = await fetch('/chemists');
    const items = await res.json();
    const tbody = document.querySelector('#chemistsTable tbody');
    if (tbody) {
      tbody.innerHTML = '';
      for (const row of items) {
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td>${row.username}</td>
          <td>${row.user_id}</td>
          <td style="text-align:right"><button class="btn ghost" title="Delete" onclick="deleteChemist('${row.username}')">✕</button></td>`;
        tbody.appendChild(tr);
      }
    }
    const sel = document.getElementById('chemistSelect');
    if (sel) {
      const current = sel.value;
      sel.innerHTML = items.map(r => `<option value="${r.username}">${r.username} (${r.user_id})</option>`).join('') +
        `<option value="__manage__">➕ Manage chemists…</option>`;
      const toSelect = items.find(r => r.username === current) ? current : (items[0]?.username || '__manage__');
      sel.value = toSelect;
      lastChemistSelection = sel.value !== '__manage__' ? sel.value : null;
    }
  } catch (e) { console.error(e); }
}
async function addChemist() {
  const u = document.getElementById('newChemUsername');
  const id = document.getElementById('newChemUserID');
  const username = (u.value || '').trim().toLowerCase();
  const user_id = (id.value || '').trim().toUpperCase();
  if (!username || !user_id) { alert('Provide both username and user ID.'); return; }
  const res = await fetch('/chemists', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, user_id }) });
  if (res.ok) { u.value = ''; id.value = ''; refreshChemistsUI(); }
  else { const data = await res.json().catch(() => ({})); alert(data.error || 'Unable to add chemist.'); }
}
async function deleteChemist(username) {
  if (!confirm(`Delete chemist '${username}'?`)) return;
  const res = await fetch(`/chemists/${encodeURIComponent(username)}`, { method: 'DELETE' });
  if (res.ok) { refreshChemistsUI(); }
  else { const data = await res.json().catch(() => ({})); alert(data.error || 'Unable to delete chemist.'); }
}

/* ---------- Editable table row logic (ELN/IPCs/PURIF) ---------- */
function rowEdit(btn) {
  const tr = btn.closest('tr');
  tr.dataset.prev = JSON.stringify(getEditableFields(tr));
  toggleRowEditing(tr, true);
}
function rowCancel(btn) {
  const tr = btn.closest('tr');
  const prev = JSON.parse(tr.dataset.prev || '{}');
  for (const [k, v] of Object.entries(prev)) {
    const td = tr.querySelector(`td[data-field="${k}"]`);
    if (td) td.textContent = v || '';
  }
  toggleRowEditing(tr, false);
}
function rowSave(btn, kind) {
  const tr = btn.closest('tr');
  const id = tr.dataset.id;
  const payload = getEditableFields(tr);
  let url;
  if (kind === 'ipc') url = `/api/ipc-measurements/${id}`;
  else if (kind === 'purif') url = `/api/purif-measurements/${id}`;
  else url = `/api/elns/${encodeURIComponent(id)}`;
  fetch(url, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
    .then(async res => {
      if (res.ok) { toggleRowEditing(tr, false); }
      else { const data = await res.json().catch(() => ({})); alert(data.error || 'Failed to save changes.'); }
    })
    .catch(err => { console.error(err); alert('Network error. Changes not saved.'); });
}
function getEditableFields(tr) {
  const obj = {}; tr.querySelectorAll('td[data-field]').forEach(td => { obj[td.dataset.field] = td.textContent.trim(); });
  return obj;
}
function toggleRowEditing(tr, on) {
  tr.querySelectorAll('td[data-field]').forEach(td => { td.setAttribute('contenteditable', on ? 'true' : 'false'); });
  const [edit, save, cancel] = tr.querySelectorAll('.actions .btn');
  if (edit && save && cancel) { edit.classList.toggle('hidden', on); save.classList.toggle('hidden', !on); cancel.classList.toggle('hidden', !on); }
}

/* ---------- ELN Expand modal ---------- */
function openElnExpand() {
  document.getElementById('elnExpandModal').classList.remove('hidden');
}
function closeElnExpand() {
  document.getElementById('elnExpandModal').classList.add('hidden');
}

/* ---------- Boot ---------- */
document.addEventListener('DOMContentLoaded', () => {
  const sel = document.getElementById('chemistSelect');
  if (sel && sel.value !== '__manage__') { lastChemistSelection = sel.value; }
});


/* ---------- IPC/Purif Expand modals ---------- */
function openIpcExpand() { document.getElementById('ipcExpandModal').classList.remove('hidden'); }
function closeIpcExpand() { document.getElementById('ipcExpandModal').classList.add('hidden'); }
function openPurifExpand() { document.getElementById('purifExpandModal').classList.remove('hidden'); }
function closePurifExpand() { document.getElementById('purifExpandModal').classList.add('hidden'); }


/* ---------- Purification method dropdown (new method toggle) ---------- */
document.addEventListener('DOMContentLoaded', () => {
  const sel = document.getElementById('purifMethodSelect');
  const row = document.getElementById('newMethodRow');
  if (sel && row) {
    const update = () => { row.classList.toggle('hidden', sel.value !== '__new__'); };
    sel.addEventListener('change', update);
    update();
  }
});

/* ---------- Purification method: enable "new method" input only when selected ---------- */
document.addEventListener('DOMContentLoaded', () => {
  const sel = document.getElementById('purifMethodSelect');
  const row = document.getElementById('newMethodRow');
  const input = document.getElementById('newPurifMethodInput');
  if (sel && row && input) {
    const update = () => {
      const on = sel.value === '__new__';
      row.classList.toggle('hidden', !on);
      input.disabled = !on;
      if (!on) { input.value = input.value; } // keep text but block editing
    };
    sel.addEventListener('change', update);
    update();
  }
});

/* ---------- Modal table ELN-ID search & highlight ---------- */
function setupTableSearch(tableId, inputId) {
  const table = document.getElementById(tableId);
  const input = document.getElementById(inputId);
  if (!table || !input) return;
  const ths = Array.from(table.querySelectorAll('thead th'));
  const elnCol = ths.findIndex(th => /ELN\s*ID/i.test(th.textContent || ''));
  if (elnCol < 0) return;

  const apply = () => {
    const q = (input.value || '').trim().toLowerCase();
    let firstHit = null;
    table.querySelectorAll('tbody tr').forEach(tr => {
      tr.classList.remove('dim');
      tr.querySelectorAll('td').forEach(td => td.classList.remove('hit'));
      const tds = tr.querySelectorAll('td');
      const td = tds[elnCol];
      if (!td) return;
      const val = (td.textContent || '').trim().toLowerCase();
      if (q && val.includes(q)) {
        td.classList.add('hit');
        if (!firstHit) firstHit = tr;
      } else if (q) {
        tr.classList.add('dim');
      }
    });
    if (firstHit) { firstHit.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
  };

  input.addEventListener('input', apply);
}
document.addEventListener('DOMContentLoaded', () => {
  setupTableSearch('elnModalTable', 'elnSearch');
  setupTableSearch('ipcModalTable', 'ipcSearch');
  setupTableSearch('purifModalTable', 'purifSearch');
});
