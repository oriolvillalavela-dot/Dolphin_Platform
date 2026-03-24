
/* ===== Batch UI ===== */
const BatchUI = {
    openMultiManage() {
        modal(`
      <div class="modal">
        <div class="modal-card" style="width: 800px; max-height: 90vh; display: flex; flex-direction: column;">
          <div class="modal-header">
            <h3>Batch Multi-Manage</h3>
            <button class="icon-btn" onclick="closeModal()">✕</button>
          </div>
          <div class="modal-body" style="flex:1; overflow:hidden; display:flex; flex-direction:column; gap: 10px;">
            <!-- Step 1: Search -->
            <div style="display:flex; gap:10px; align-items:flex-start;">
                <textarea id="mmInput" class="simple-input" rows="3" placeholder="Paste Chem_IDs or Batch_IDs..." style="flex:1; resize:none;"></textarea>
                <div style="display:flex; flex-direction:column; gap:5px;">
                    <button class="btn primary" onclick="BatchUI.searchMultiple()">Search</button>
                    <button class="btn small" onclick="document.getElementById('mmInput').value=''">Clear</button>
                </div>
            </div>
            
            <!-- Step 2: Results & Actions -->
            <div id="mmResultsArea" style="flex:1; display:none; flex-direction:column; overflow:hidden; border-top:1px solid var(--border); padding-top:10px;">
                <div class="toolbar" style="margin-bottom:8px; gap:10px; align-items:center;">
                    <label><input type="checkbox" id="mmSelectAll" onchange="BatchUI.toggleAll(this)"> Select All</label>
                    <span id="mmCount" style="color:var(--muted); font-size:0.9em;">0 selected</span>
                    <div style="flex:1"></div>
                    
                    <!-- Bulk Actions -->
                    <select id="mmActionType" class="themed-select" onchange="BatchUI.updateActionInputs()" style="width:140px">
                        <option value="">-- Action --</option>
                        <option value="location">Update Location</option>
                        <option value="status">Update Status</option>
                        <option value="amount">Update Amount</option>
                    </select>
                    <div id="mmActionInputs" style="display:flex; gap:5px;"></div>
                    <button class="btn primary" onclick="BatchUI.applyBulk()">Apply</button>
                </div>

                <div class="table-wrap" style="flex:1; overflow:auto; border:1px solid var(--border); border-radius:8px;">
                    <table class="grid" id="mmTable">
                        <thead>
                            <tr>
                                <th style="width:30px"></th>
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
          <div class="modal-actions">
            <button class="btn small" onclick="closeModal()">Close</button>
          </div>
        </div>
      </div>
    `);
    },

    async searchMultiple() {
        const text = document.getElementById('mmInput').value;
        const ids = text.split(/[\n,]+/).map(s => s.trim()).filter(s => s);
        if (!ids.length) return;

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
            tbody.innerHTML = data.items.map(b => `
        <tr>
            <td><input type="checkbox" class="mm-check" value="${b.batch_id}" onchange="BatchUI.updateCount()"></td>
            <td>${b.batch_id}</td>
            <td>${b.chem_id}</td>
            <td>${b.location} / ${b.sublocation || '-'}</td>
            <td>${b.status}</td>
            <td>${b.amount}</td>
        </tr>
      `).join('');

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
        document.getElementById('mmCount').textContent = `${n} selected`;
    },

    updateActionInputs() {
        const type = document.getElementById('mmActionType').value;
        const container = document.getElementById('mmActionInputs');
        container.innerHTML = '';

        if (type === 'location') {
            container.innerHTML = `
            <input id="mmLoc" placeholder="Location" class="simple-input" style="width:100px">
            <input id="mmSub" placeholder="Sub" class="simple-input" style="width:80px">
          `;
        } else if (type === 'status') {
            container.innerHTML = `
            <select id="mmStatus" class="themed-select">
                <option>Available</option>
                <option>Empty</option>
                <option>Expired</option>
                <option>Reserved</option>
                <option>Sent</option>
            </select>
          `;
        } else if (type === 'amount') {
            container.innerHTML = `<input id="mmAmount" placeholder="Amount" class="simple-input" style="width:100px">`;
        }
    },

    async applyBulk() {
        const type = document.getElementById('mmActionType').value;
        if (!type) return;

        const batch_ids = Array.from(document.querySelectorAll('.mm-check:checked')).map(cb => cb.value);
        if (!batch_ids.length) { alert("No batches selected"); return; }

        const payload = { batch_ids, action: type };

        if (type === 'location') {
            payload.location = document.getElementById('mmLoc').value;
            payload.sublocation = document.getElementById('mmSub').value;
        } else if (type === 'status') {
            payload.status = document.getElementById('mmStatus').value;
        } else if (type === 'amount') {
            payload.amount = document.getElementById('mmAmount').value;
        }

        if (!confirm(`Update ${batch_ids.length} batches?`)) return;

        Overlay.show();
        try {
            const r = await fetch('/batches/bulk_update', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const d = await r.json();
            if (!d.ok) throw new Error(d.error);

            alert(`Updated ${d.updated} batches.`);
            BatchUI.searchMultiple(); // refresh

        } catch (e) { alert(e.message); }
        finally { Overlay.hide(); }
    }
};
