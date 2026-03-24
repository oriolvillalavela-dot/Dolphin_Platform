
const Overlay = { show(){ document.getElementById('overlay').classList.remove('hidden') }, hide(){ document.getElementById('overlay').classList.add('hidden') } };
function modal(html){ document.getElementById('modalHost').innerHTML = html; }
function closeModal(){ modal(''); }

// --- Chemicals UI ---
const ChemUI = {
  openNewChemical(){
    modal(`
      <div class="modal" onclick="if(event.target===this)closeModal()">
        <div class="modal-card">
          <div class="modal-header"><h3>New Chemical</h3><button class="icon-btn" onclick="closeModal()">✕</button></div>
          <div class="modal-body">
            <div class="form slim" id="chemForm">
              ${[
                "common_name_abb","cas","ro_srn","chemform","mw","mim","density","stock_solution_c","purity","smiles","inchi","inchi_key"
              ].map(k=>`<label><span>${k}</span><input id="${k}" /></label>`).join("")}
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
  async save(){
    const payload = {};
    ["common_name_abb","cas","ro_srn","chemform","mw","mim","density","aggregate_state","stock_solution_c","purity","smiles","inchi","inchi_key"].forEach(k=>payload[k]=document.getElementById(k).value);
    Overlay.show();
    try{
      const r = await fetch('/chemicals/create', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      const txt = await r.text(); let data; try{ data = JSON.parse(txt) } catch(e){ throw new Error(txt) }
      if(!data.ok) throw new Error(data.error||'Failed');
      location.reload();
    }catch(e){ alert(e.message) } finally{ Overlay.hide() }
  },
  async openAvailability(chem_id){
    Overlay.show();
    try{
      const res = await fetch(`/chemicals/${chem_id}/availability`);
      const txt = await res.text(); let data; try{ data = JSON.parse(txt) } catch(e){ throw new Error(txt) }
      if(!data.ok) throw new Error(data.error||'Failed');
      const rows = data.items.map(x=>`<tr><td>${x.batch_id}</td><td>${x.location}</td><td>${x.sublocation||""}</td></tr>`).join('');
      modal(`
        <div class="modal" onclick="if(event.target===this)closeModal()">
          <div class="modal-card">
            <div class="modal-header"><h3>${chem_id} • Available / Stock Room</h3><button class="icon-btn" onclick="closeModal()">✕</button></div>
            <div class="modal-body">
              <div class="table-wrap"><table class="grid"><thead><tr><th>Batch_ID</th><th>Location</th><th>Sublocation</th></tr></thead><tbody>${rows||'<tr><td colspan=3>No batches</td></tr>'}</tbody></table></div>
            </div>
            <div class="modal-actions"><button class="btn" onclick="closeModal()">Close</button></div>
          </div>
        </div>
      `);
    }catch(e){ alert(e.message) } finally{ Overlay.hide() }
  }
};

// Supplier list helper
async function listSuppliers(q=""){
  const r = await fetch('/suppliers'+(q?`?q=${encodeURIComponent(q)}`:''));
  const txt = await r.text(); let d; try{ d = JSON.parse(txt) } catch(e){ throw new Error(txt) }
  return d.ok ? d.suppliers : [];
}

// --- Bottles UI ---
const BottlesUI = {
  async openGenerateBottle(chem_id){
    const suppliers = await listSuppliers();
    modal(`
      <div class="modal" onclick="if(event.target===this)closeModal()">
        <div class="modal-card">
          <div class="modal-header"><h3>Generate bottle for ${chem_id}</h3><button class="icon-btn" onclick="closeModal()">✕</button></div>
          <div class="modal-body">
            <div class="form slim">
              <label><span>supplier_id</span>
                <input id="supplier_input" placeholder="Start typing (or add new)">
                <div id="supplier_list" class="table-wrap" style="max-height:120px;overflow:auto;margin-top:6px;border:1px solid var(--border);border-radius:8px;padding:6px;background:var(--card)">
                  ${suppliers.map(n=>`<div class="option" data-action="pick-supplier" data-supplier="${n.replace(/"/g,'&quot;')}">${n}</div>`).join('')}
                </div>
              </label>
              <label><span>Lot_no</span><input id="Lot_no" /></label>
              <label><span>purity</span><input id="b_purity" type="number" step="0.01" /></label>
              <label><span>size/amount</span><input id="size_amount" /></label>
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
  async filterSuppliers(q){
    const names = await listSuppliers(q||"");
    const list = document.getElementById('supplier_list');
    if(q && names.length===0){
      list.style.display='block'; list.innerHTML = `<div class="option" data-action="pick-supplier" data-supplier="${q.replace(/"/g,'&quot;')}">Add "${q}"</div>`;
    }else{
      list.style.display='block'; list.innerHTML = names.map(n=>`<div class="option" data-action="pick-supplier" data-supplier="${n.replace(/"/g,'&quot;')}">${n}</div>`).join('');
    }
  },
  pickSupplier(name){
    const inp = document.getElementById('supplier_input');
    inp.value = name.startsWith('Add "') ? name.slice(5,-1) : name;
  },
  async saveBottle(chem_id){
    const supplier_id = document.getElementById('supplier_input').value.trim();
    const Lot_no = document.getElementById('Lot_no').value.trim();
    const purity = document.getElementById('b_purity').value.trim();
    const size_amount = document.getElementById('size_amount').value.trim();
    if(!supplier_id || !Lot_no || !purity || !size_amount){ alert('All fields are required'); return; }
    Overlay.show();
    try{
      const r = await fetch(`/bottles/create/${chem_id}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({supplier_id, Lot_no, purity, size_amount})});
      const txt = await r.text(); let data; try{ data = JSON.parse(txt) } catch(e){ throw new Error(txt) }
      if(!data.ok) throw new Error(data.error||'Failed');
      closeModal();
      BatchesUI.openInitialBatch(chem_id, data.bottle_id, data.bottle_no);
    }catch(e){ alert(e.message) } finally{ Overlay.hide() }
  }
};

// --- Batches UI ---
function formatDMY(iso){ try{ if(!iso) return ''; const d = new Date(iso); const dd = String(d.getUTCDate()).padStart(2,'0'); const mm = String(d.getUTCMonth()+1).padStart(2,'0'); const yy = d.getUTCFullYear(); return `${dd}/${mm}/${yy}`;}catch(e){ return '' } }

const BatchesUI = {
  openInitialBatch(chem_id, bottle_id, bottle_no){
    modal(this._batchModal({title:`Create initial batch for ${bottle_id}`, chem_id, bottle_no, typeLocked:true, Type:'Bottle'}));
  },
  openGenerateBatch(chem_id, bottle_id){
    const bottle_no = parseInt(bottle_id.split('_B').pop(),10);
    modal(this._batchModal({title:`Generate batch for ${bottle_id}`, chem_id, bottle_no, typeLocked:false}));
  },
  _batchModal({title, chem_id, bottle_no, typeLocked, Type}){
    return `
    <div class="modal" onclick="if(event.target===this)closeModal()">
      <div class="modal-card">
        <div class="modal-header"><h3>${title}</h3><button class="icon-btn" onclick="closeModal()">✕</button></div>
        <div class="modal-body">
          <div class="form slim">
            <label><span>Type</span>
              <select id="bt_Type" class="themed-select" ${typeLocked?'disabled':''}>
                <option ${Type==='Bottle'?'selected':''}>Bottle</option>
                <option>Stock solution</option>
                <option>Head</option>
              </select>
            </label>
            <label><span>concentration_moll</span><input id="bt_conc" type="number" step="0.000001" /></label>
            <label><span>Barcode</span><input id="bt_barcode" /></label>
            <label><span>location</span><input id="bt_location" /></label>
            <label><span>sublocation (optional)</span><input id="bt_sublocation" /></label>
            <label><span>amount</span><input id="bt_amount" /></label>
            <div id="stock_extra" style="display:none">
              <label><span>Expiring date</span><input id="bt_exp" type="date" /></label>
            </div>
          </div>
        </div>
        <div class="modal-actions">
          <button class="btn" data-action="save-batch" data-chem-id="${chem_id}" data-bottle-no="${bottle_no}" data-type-locked="${typeLocked?'1':'0'}">Create</button>
          <button class="btn small" onclick="closeModal()">Cancel</button>
        </div>
      </div>
    </div>`;
  },
  onTypeChange(){
    const t = document.getElementById('bt_Type').value;
    document.getElementById('stock_extra').style.display = (t==='Stock solution')?'block':'none';
  },
  async save(chem_id, bottle_no, typeLocked){
    const Type = typeLocked ? 'Bottle' : document.getElementById('bt_Type').value;
    const concentration_moll = document.getElementById('bt_conc').value;
    const Barcode = document.getElementById('bt_barcode').value;
    const location = document.getElementById('bt_location').value;
    const sublocation = document.getElementById('bt_sublocation').value;
    const amount = document.getElementById('bt_amount').value;
    const expiring_date = document.getElementById('bt_exp') ? document.getElementById('bt_exp').value : "";
    if(!Type || !concentration_moll || !Barcode || !location || !amount){ alert('Please fill all required fields'); return; }
    const url = typeLocked ? '/batches/create_for_new_bottle' : '/batches/create';
    const body = {chem_id, bottle_no, Type, concentration_moll, Barcode, location, sublocation, amount, expiring_date};
    Overlay.show();
    try{
      const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
      const txt = await r.text(); let data; try{ data = JSON.parse(txt) } catch(e){ throw new Error(txt) }
      if(!data.ok) throw new Error(data.error||'Failed');
      closeModal(); location.href = '/batches';
    }catch(e){ alert(e.message) } finally{ Overlay.hide() }
  },
  openManage(batch_id, location, sublocation, status, kind, exp){
    const showDate = (kind==='Stock solution' && status==='Expired');
    const dateRowHTML = `<div id="mg_exp_row" style="display:${showDate?'block':'none'}">
        <label><span>New expiring date (dd/mm/yyyy)</span><input id="mg_exp" placeholder="dd/mm/yyyy" value="${exp?formatDMY(exp):''}"></label>
      </div>`;
    modal(`
    <div class="modal" onclick="if(event.target===this)closeModal()">
      <div class="modal-card">
        <div class="modal-header"><h3>Manage ${batch_id}</h3><button class="icon-btn" onclick="closeModal()">✕</button></div>
        <div class="modal-body"><div class="form slim">
          <label><span>location</span><input id="mg_loc" value="${location}"></label>
          <label><span>sublocation</span><input id="mg_subloc" value="${sublocation||''}"></label>
          <label><span>Status</span>
            <select id="mg_status" class="themed-select">
              ${["Available","Empty","Stock Room","Expired"].map(s=>`<option ${s===status?'selected':''}>${s}</option>`).join('')}
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
  async saveManage(batch_id){
    const loc = document.getElementById('mg_loc').value;
    const sublocation = document.getElementById('mg_subloc').value;
    const status = document.getElementById('mg_status').value;
    const btn = document.querySelector('[data-action="save-manage-batch"][data-batch-id="'+batch_id+'"]');
    const kind = btn ? btn.dataset.kind : "";
    let expiring_date_ddmmyyyy = null;
    if(kind==='Stock solution' && status==='Available'){
      const expEl = document.getElementById('mg_exp');
      if(!expEl || !expEl.value.trim()){ alert('Please provide a new expiring date (dd/mm/yyyy)'); return; }
      expiring_date_ddmmyyyy = expEl.value.trim();
    }
    Overlay.show();
    try{
      const r = await fetch(`/batches/manage/${batch_id}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({location: loc, sublocation, status, expiring_date_ddmmyyyy})});
      const txt = await r.text(); let data; try{ data = JSON.parse(txt) } catch(e){ throw new Error(txt) }
      if(!data.ok) throw new Error(data.error||'Failed');
      closeModal(); window.location.reload();
    }catch(e){ alert(e.message) } finally{ Overlay.hide() }
  }
};

// --- Plate UI ---
const PlateUI = (()=>{
  const state = { type:"24", rows:[], cols:[], assignments:{}, currentKey:null };
  const CATS = [
    {v:"starting_material", label:"Starting material (eq)"},
    {v:"reagent", label:"Reagent (eq)"},
    {v:"solvent", label:"Solvent (fraction)"},
    {v:"catalyst", label:"Catalyst (eq)"},
    {v:"ligand", label:"Ligand (eq)"},
    {v:"additive", label:"Additive (eq)"},
  ];

  function init(type, prefill){
    state.type = type;
    state.rows = (type==="24")? ["A","B","C","D"] : ["A","B","C","D","E","F","G","H"];
    state.cols = (type==="24")? [1,2,3,4,5,6] : [1,2,3,4,5,6,7,8,9,10,11,12];
    if(prefill && typeof prefill==='object'){
      if(prefill.assignments && typeof prefill.assignments==='object'){ state.assignments = prefill.assignments; }
      if(prefill.meta){
        const m = prefill.meta;
        const set = (id,val)=>{ const el=document.getElementById(id); if(el && val!=null){ el.value = String(val); } };
        set('eln', m.eln||''); set('atmosphere', m.atmosphere||''); set('mix_mode', m.mix_mode||''); set('mix_rpm', m.mix_rpm||'');
        set('wavelength_nm', m.wavelength_nm); set('scale_mol', m.scale_mol); set('concentration_mol_l', m.concentration_mol_l);
      }
    }
    render();
  }

  function render(){
    const host = document.getElementById('plateHost'); host.innerHTML = "";
    const grid = document.createElement('div'); grid.className = 'plate';
    grid.style.gridTemplateColumns = `repeat(${state.cols.length+1}, 40px)`;
    grid.style.gridTemplateRows = `repeat(${state.rows.length+1}, 40px)`;

    const corner = hdr("•", "corner");
    corner.addEventListener('mouseenter', ()=> highlightAll(true));
    corner.addEventListener('mouseleave', ()=> highlightAll(false));
    corner.onclick = ()=> openModal("plate");
    grid.appendChild(corner);

    state.cols.forEach(col=>{
      const h = hdr(String(col), "col-h");
      h.addEventListener('mouseenter', ()=> highlightCol(col,true));
      h.addEventListener('mouseleave', ()=> highlightCol(col,false));
      h.onclick = ()=> openModal(`col:${col}`);
      grid.appendChild(h);
    });

    state.rows.forEach(r=>{
      const h = hdr(r, "row-h");
      h.addEventListener('mouseenter', ()=> highlightRow(r,true));
      h.addEventListener('mouseleave', ()=> highlightRow(r,false));
      h.onclick = ()=> openModal(`row:${r}`);
      grid.appendChild(h);

      state.cols.forEach(c=>{
        const cell = document.createElement('div'); cell.className='cell'; cell.dataset.row=r; cell.dataset.col=c; cell.title=`${r}${c}`;
        cell.addEventListener('mouseenter', ()=> cell.classList.add('highlight'));
        cell.addEventListener('mouseleave', ()=> cell.classList.remove('highlight'));
        cell.onclick = ()=> openModal(`well:${r}${c}`);
        grid.appendChild(cell);
      });
    });

    host.appendChild(grid);
  }

  function hdr(text,cls){ const d=document.createElement('div'); d.className='hdr '+cls; d.textContent=text; return d; }
  function highlightRow(r,on){
    document.querySelectorAll(`.hdr.row-h`).forEach(h=>{ if(h.textContent===r) h.classList.toggle('highlight',on); });
    document.querySelectorAll(`.cell[data-row="${r}"]`).forEach(c=> c.classList.toggle('highlight',on));
  }
  function highlightCol(c,on){
    document.querySelectorAll(`.hdr.col-h`).forEach(h=>{ if(h.textContent===String(c)) h.classList.toggle('highlight',on); });
    document.querySelectorAll(`.cell[data-col="${c}"]`).forEach(cell=> cell.classList.toggle('highlight',on));
  }
  function highlightAll(on){ document.querySelectorAll('.cell,.hdr').forEach(e=>e.classList.toggle('all',on)); }

  function mergeLists(base, add){
    const m = new Map((base||[]).map(x=>[`${x.category}|${(x.ref||'').trim().toLowerCase()}`, x]));
    (add||[]).forEach(x=> m.set(`${x.category}|${(x.ref||'').trim().toLowerCase()}`, x));
    return Array.from(m.values());
  }

  function effectiveListForKey(key){
    if(key.startsWith('well:')){
      const rc = key.slice(5);
      const r = rc.charAt(0), c = rc.slice(1);
      return mergeLists( mergeLists( mergeLists(state.assignments['plate']||[], state.assignments[`row:${r}`]||[]),
                                     state.assignments[`col:${c}`]||[] ),
                         state.assignments[key]||[] );
    }
    if(key.startsWith('row:')) return mergeLists(state.assignments['plate']||[], state.assignments[key]||[]);
    if(key.startsWith('col:')) return mergeLists(state.assignments['plate']||[], state.assignments[key]||[]);
    return state.assignments['plate']||[];
  }

  function openModal(key){
    state.currentKey = key;
    const list = effectiveListForKey(key);
    const rows = (list||[]).map(it=>rowHTML(it.ref, it.category, it.amount)).join('') || rowHTML();
    const html = `
    <div class="modal" onclick="if(event.target===this)PlateUI.closeModal()">
      <div class="modal-card">
        <div class="modal-header"><h3>Assign reagents • ${key}</h3><button class="icon-btn" onclick="PlateUI.closeModal()">✕</button></div>
        <div class="modal-body">
          <div id="rows">${rows}</div>
          <button class="btn small" data-action="plate-add-line">+ Add line</button>
          <div class="muted" style="margin-top:6px">Tip: type to search. Click a suggestion to use its Chem_ID.</div>
        </div>
        <div class="modal-actions">
          <button class="btn" data-action="plate-save-modal">Save</button>
          <button class="btn small" onclick="PlateUI.closeModal()">Cancel</button>
        </div>
      </div>
    </div>`;
    document.getElementById('modalHost').innerHTML = html;
  }
  function closeModal(){ document.getElementById('modalHost').innerHTML = ''; }
  function rowHTML(ref="", cat="reagent", amount=""){
    if(typeof amount==="object"&&amount!==null){ amount=""; }
    if(typeof ref==="object"&&ref!==null){ ref=""; }
    if(typeof cat==="object"&&cat!==null){ cat="reagent"; }
    const id = Math.random().toString(36).slice(2,9);
    return `<div class="row-inline typeahead" style="margin-bottom:8px">
      <input class="ref" data-rowid="${id}" placeholder="Chem_ID / name / CAS / SMILES" value="${ref||''}">
      <div class="typeahead-menu" id="menu-${id}" style="display:none"></div>
      <select class="cat themed-select">${CATS.map(c=>`<option value="${c.v}" ${c.v===cat?'selected':''}>${c.label}</option>`).join('')}</select>
      <input class="amt" placeholder="eq / fraction" value="${(amount??'')}">
      <button class="icon-btn" data-action="plate-remove-line">🗑</button>
    </div>`;
  }
  function addRow(){ document.getElementById('rows').insertAdjacentHTML('beforeend', rowHTML()) }
  function saveModal(){
    const list=[];
    document.querySelectorAll('#rows .row-inline').forEach(r=>{
      const ref = r.querySelector('.ref').value.trim();
      const cat = r.querySelector('.cat').value;
      const amt = r.querySelector('.amt').value.trim();
      if(ref && cat) list.push({ref, category:cat, amount:amt});
    });
    state.assignments[state.currentKey] = list;
    closeModal();
  }

  async function fetchLookup(q){
    const r = await fetch('/chemicals/lookup?q='+encodeURIComponent(q||''));
    const txt = await r.text(); let d; try{ d = JSON.parse(txt) } catch(e){ throw new Error(txt) }
    return d.ok ? d.items : [];
  }

  function reloadWithType(){
    const t = document.getElementById('plateType').value;
    const url = new URL(location.href); url.searchParams.set('type', t); location.href = url.toString();
  }
  async function save(){
    const meta = {
      eln: document.getElementById('eln').value.trim(),
      atmosphere: document.getElementById('atmosphere').value.trim(),
      mix_mode: document.getElementById('mix_mode').value,
      mix_rpm: document.getElementById('mix_rpm').value.trim(),
      wavelength_nm: document.getElementById('wavelength_nm').value,
      scale_mol: document.getElementById('scale_mol').value,
      concentration_mol_l: document.getElementById('concentration_mol_l').value,
    };
    Overlay.show();
    try{
      const res = await fetch('/plates/save', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({meta, assignments: state.assignments, plate_type: state.type})});
      const text = await res.text(); let data; try{ data = JSON.parse(text) } catch { throw new Error(text) }
      if(!data.ok) throw new Error(data.error||'Failed');
      alert(`Saved plate #${data.plate_no}`);
      location.href = '/surf';
    }catch(e){ alert(e.message) } finally{ Overlay.hide() }
  }

  // Event delegation for typeahead
  document.addEventListener('input', async (e)=>{
    if(e.target && e.target.classList.contains('ref')){
      const val = e.target.value.trim();
      const rid = e.target.dataset.rowid;
      const menu = document.getElementById('menu-'+rid);
      if(!menu) return;
      if(!val){ menu.style.display='none'; menu.innerHTML=''; return; }
      const items = await fetchLookup(val);
      if(items.length===0){ menu.style.display='none'; menu.innerHTML=''; return; }
      menu.innerHTML = items.map(it=>`<div class="typeahead-item" data-action="pick-chem" data-target="${rid}" data-chem="${it.chem_id}">${it.label}</div>`).join('');
      menu.style.display='block';
    }
  });
  document.addEventListener('click', (e)=>{
    const pick = e.target.closest('[data-action="pick-chem"]');
    if(pick){
      const target = pick.dataset.target;
      const chem = pick.dataset.chem;
      const input = document.querySelector(`.ref[data-rowid="${target}"]`);
      const menu  = document.getElementById('menu-'+target);
      if(input){ input.value = chem; }
      if(menu){ menu.style.display='none'; }
    }
  });

  return { init, reloadWithType, openModal, closeModal, addRow, save, saveModal };
})();

// ---- Global delegation ----
document.addEventListener('click', (e)=>{
  const a = e.target.closest('[data-action]');
  if(!a) return;
  const act = a.getAttribute('data-action');
  switch(act){
    case 'open-new-chemical': e.preventDefault(); ChemUI.openNewChemical(); break;
    case 'open-availability': e.preventDefault(); ChemUI.openAvailability(a.dataset.chemId); break;
    case 'open-generate-bottle': e.preventDefault(); BottlesUI.openGenerateBottle(a.dataset.chemId); break;
    case 'open-generate-batch': e.preventDefault(); BottlesUI.openGenerateBatch(a.dataset.chemId, a.dataset.bottleId); break;
    case 'save-chemical': e.preventDefault(); ChemUI.save(); break;
    case 'save-bottle': e.preventDefault(); BottlesUI.saveBottle(a.dataset.chemId); break;
    case 'save-batch': e.preventDefault(); {
      const locked = a.dataset.typeLocked === '1';
      BatchesUI.save(a.dataset.chemId, parseInt(a.dataset.bottleNo,10), locked);
      break;
    }
    case 'open-manage-batch': e.preventDefault(); BatchesUI.openManage(a.dataset.batchId, a.dataset.location, a.dataset.sublocation, a.dataset.status, a.dataset.kind, a.dataset.exp); break;
    case 'save-manage-batch': e.preventDefault(); BatchesUI.saveManage(a.dataset.batchId); break;
    case 'plate-add-line': e.preventDefault(); PlateUI.addRow(); break;
    case 'plate-remove-line': e.preventDefault(); a.closest('.row-inline').remove(); break;
    case 'plate-save-modal': e.preventDefault(); PlateUI.saveModal(); break;
    case 'save-plate': e.preventDefault(); PlateUI.save(); break;
    case 'pick-supplier': e.preventDefault(); BottlesUI.pickSupplier(a.dataset.supplier); break;
  }
});
document.addEventListener('input', (e)=>{
  if(e.target && e.target.id === 'supplier_input'){
    BottlesUI.filterSuppliers(e.target.value);
  }
});
document.addEventListener('change', (e)=>{
  if(e.target && e.target.id === 'bt_Type'){
    BatchesUI.onTypeChange();
  }
  if(e.target && e.target.id === 'mg_status'){
    const saveBtn = document.querySelector('[data-action="save-manage-batch"]');
    const kind = saveBtn ? saveBtn.dataset.kind : "";
    const row = document.getElementById('mg_exp_row');
    if(row){
      row.style.display = (kind==='Stock solution' && e.target.value==='Available') ? 'block' : 'none';
    }
  }
});

document.addEventListener('focusin', async (e)=>{
  if(e.target && e.target.id==='supplier_input'){
    const list = document.getElementById('supplier_list');
    if(list){
      const names = await listSuppliers("");
      list.innerHTML = names.map(n=>`<div class="option" data-action="pick-supplier" data-supplier="${n.replace(/"/g,'&quot;')}">${n}</div>`).join('');
      list.style.display='block';
    }
  }
});
// Startup splash: show once per browser session
(function(){
  const splash = document.getElementById('splash');
  if (!splash) return;

  // If we’ve shown it already in this tab/session, remove immediately
  if (sessionStorage.getItem('splashSeen')) {
    splash.remove();
    return;
  }

  // Fade out after ~1.7s (adjust if you prefer)
  window.addEventListener('load', () => {
    setTimeout(() => {
      splash.classList.add('fade-out');
      sessionStorage.setItem('splashSeen', '1');
    }, 1700);
  });
})();
