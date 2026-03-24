const AnalysisTool = {

    handleUpload(file) {
        if (!file) return;

        // Show Progress
        const progressArea = document.getElementById('progressArea');
        const progressBar = document.getElementById('progressBar');
        const uploadZone = document.getElementById('uploadZone');

        uploadZone.style.display = 'none';
        progressArea.classList.remove('hidden');

        // Simulate progress (fake for now, real is instant usually for small files)
        let p = 0;
        const intv = setInterval(() => {
            p += 10;
            if (p > 90) clearInterval(intv);
            progressBar.style.width = p + "%";
            document.getElementById('progressPercent').textContent = p + "%";
        }, 200);

        const fd = new FormData();
        fd.append('file', file);

        fetch('/api/analysis/process', {
            method: 'POST',
            body: fd
        })
            .then(r => r.json())
            .then(data => {
                clearInterval(intv);
                progressBar.style.width = "100%";
                document.getElementById('progressPercent').textContent = "100%";

                setTimeout(() => {
                    progressArea.classList.add('hidden');
                    document.getElementById('resultsSection').classList.remove('hidden');
                    this.renderResults(data);
                }, 500);
            })
            .catch(e => {
                clearInterval(intv);
                alert("Error: " + e);
                progressArea.classList.add('hidden');
                uploadZone.style.display = 'flex';
            });
    },

    renderResults(data) {
        if (!data.ok) { alert(data.error); return; }

        // Stats
        document.getElementById('stat_internal').textContent = data.stats.internal;
        document.getElementById('stat_external').textContent = data.stats.external;
        document.getElementById('stat_matches').textContent = data.stats.matches;
        document.getElementById('stat_orders').textContent = data.stats.orders;

        // Counts
        document.getElementById('countMosaic').textContent = data.internal_mosaic.length;
        document.getElementById('countRMM').textContent = data.internal_rmm.length;
        document.getElementById('countExternal').textContent = data.external.length;

        // Render Tables
        this.renderTable(data.internal_mosaic, 'tableMosaic');
        this.renderTable(data.internal_rmm, 'tableRMM');
        this.renderTable(data.external, 'tableExternal', true);
    },

    renderTable(items, tableId, isExternal = false) {
        const tbody = document.getElementById(tableId);
        tbody.innerHTML = "";

        items.forEach(item => {
            const tr = document.createElement('tr');
            tr.className = "hover:bg-white/5 transition-colors";

            // Status Badge
            let statusHtml = `<span class="text-amber-500 font-medium">Needs Ordering</span>`;
            if (item.matched) {
                statusHtml = `<span class="text-green-400 font-medium">Matched</span>`;
            }

            // Inventory Check
            let invHtml = `<span class="text-slate-500">-</span>`;
            if (item.matched) {
                if (item.batches > 0) {
                    invHtml = `
             <div class="text-green-400 font-bold">Yes (${item.batches})</div>
             <div class="text-xs text-slate-400">${item.location}</div>
           `;
                } else {
                    invHtml = `<span class="text-red-400 font-bold">No (0)</span>`;
                }
            }

            // Details / Chem ID
            let detailsHtml = "";
            if (item.matched) {
                detailsHtml = `<a href="/chemicals?id=${item.chem_id}" target="_blank" class="text-teal-400 hover:text-teal-300 underline">${item.chem_id}</a>`;
            } else {
                detailsHtml = `<span class="text-slate-600">-</span>`;
            }

            // For External, show extra info
            let extInfo = "";
            if (isExternal) {
                if (item.matched) {
                    detailsHtml = `
               <a href="/chemicals?id=${item.chem_id}" target="_blank" class="text-teal-400 hover:text-teal-300 underline block mb-1">${item.chem_id}</a>
               <div class="text-xs text-white/50">${item.iupac || item.smiles || "No Structure"}</div>
             `;
                } else {
                    detailsHtml = `<div class="text-xs text-white/50 break-all">${item.iupac || item.smiles || ""}</div>`;
                }
            }

            // Columns
            // Mosaic/RMM: ID, Status, Inv, Details
            // External: ID, Status, Inv, Info

            if (isExternal) {
                tr.innerHTML = `
            <td class="p-3 font-mono text-white">${item.supplier_id}</td>
            <td class="p-3">${statusHtml}</td>
            <td class="p-3">${invHtml}</td>
            <td class="p-3">${detailsHtml}</td>
          `;
            } else {
                tr.innerHTML = `
            <td class="p-3 font-mono text-white">${item.supplier_id}</td>
            <td class="p-3">${statusHtml}</td>
            <td class="p-3">${invHtml}</td>
            <td class="p-3">${detailsHtml}</td>
          `;
            }

            tbody.appendChild(tr);
        });
    },

    switchTab(tab, btn) {
        const tInt = document.getElementById('tabInternal');
        const tExt = document.getElementById('tabExternal');
        const vInt = document.getElementById('viewInternal');
        const vExt = document.getElementById('viewExternal');

        if (tab === 'internal') {
            vInt.classList.remove('hidden'); vInt.classList.add('grid');
            vExt.classList.add('hidden');

            tInt.className = "px-6 py-3 text-sm font-medium text-white border-b-2 border-amber-500 transition-colors";
            tExt.className = "px-6 py-3 text-sm font-medium text-slate-400 hover:text-white border-b-2 border-transparent transition-colors";
        } else {
            vInt.classList.add('hidden'); vInt.classList.remove('grid');
            vExt.classList.remove('hidden');

            tExt.className = "px-6 py-3 text-sm font-medium text-white border-b-2 border-amber-500 transition-colors";
            tInt.className = "px-6 py-3 text-sm font-medium text-slate-400 hover:text-white border-b-2 border-transparent transition-colors";
        }
    }

};

// Drag & Drop
const zone = document.getElementById('uploadZone');
if (zone) {
    zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('bg-slate-800'); });
    zone.addEventListener('dragleave', (e) => { e.preventDefault(); zone.classList.remove('bg-slate-800'); });
    zone.addEventListener('drop', (e) => {
        e.preventDefault();
        zone.classList.remove('bg-slate-800');
        if (e.dataTransfer.files.length) AnalysisTool.handleUpload(e.dataTransfer.files[0]);
    });
    zone.addEventListener('click', () => document.getElementById('sdfInput').click());
}
