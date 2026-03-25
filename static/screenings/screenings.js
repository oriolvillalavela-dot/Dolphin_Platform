(function () {
  const root = document.getElementById("screeningsRoot");
  if (!root) return;

  const page = root.dataset.page || "";

  function el(id) {
    return document.getElementById(id);
  }

  async function fetchJson(url, options) {
    const resp = await fetch(url, options);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.ok === false) {
      throw new Error(data.error || `Request failed (${resp.status})`);
    }
    return data;
  }

  function cleanText(value) {
    if (value === null || value === undefined) return "";
    const text = String(value).trim();
    return text.toLowerCase() === "nan" ? "" : text;
  }

  function safeNumber(value, fallbackValue) {
    const n = Number(value);
    if (Number.isFinite(n) && n > 0) return n;
    return fallbackValue;
  }

  function rowLabel(index) {
    return String.fromCharCode("A".charCodeAt(0) + index);
  }

  function colLabel(index) {
    return String(index + 1);
  }

  function dimensionsFromAny(dimensions) {
    return {
      rows: safeNumber(dimensions && dimensions.rows, 4),
      columns: safeNumber(dimensions && dimensions.columns, 6),
    };
  }

  function normalizeComponent(raw, roleHint) {
    if (!raw || typeof raw !== "object") return null;
    const role = cleanText(raw.role || roleHint) || "Reagent";
    const comp = {
      name: cleanText(raw.name || raw.common_name || raw.label) || "Unnamed",
      chem_id: cleanText(raw.chem_id) || null,
      role,
      smiles: cleanText(raw.smiles) || null,
    };
    let equivalents = raw.equivalents ?? raw.eq ?? "";
    let fraction = raw.fraction ?? "";
    const eqOrFraction = raw.equivalents_or_fraction ?? "";
    if (!equivalents && !fraction && eqOrFraction) {
      if (role.toLowerCase() === "solvent") fraction = eqOrFraction;
      else equivalents = eqOrFraction;
    }
    if (cleanText(equivalents)) comp.equivalents = cleanText(equivalents);
    if (cleanText(fraction)) comp.fraction = cleanText(fraction);
    return comp;
  }

  function componentKey(comp) {
    return [
      cleanText(comp.role).toLowerCase(),
      cleanText(comp.name).toLowerCase(),
      cleanText(comp.chem_id).toLowerCase(),
      cleanText(comp.smiles).toLowerCase(),
      cleanText(comp.equivalents),
      cleanText(comp.fraction),
    ].join("|");
  }

  function dedupeComponents(list) {
    const out = [];
    const seen = new Set();
    (list || []).forEach((raw) => {
      const comp = normalizeComponent(raw);
      if (!comp) return;
      const key = componentKey(comp);
      if (seen.has(key)) return;
      seen.add(key);
      out.push(comp);
    });
    return out;
  }

  function defaultAxes(dimensions) {
    const dims = dimensionsFromAny(dimensions);
    return {
      rows: Array.from({ length: dims.rows }, (_, i) => ({ label: rowLabel(i), variables: [] })),
      columns: Array.from({ length: dims.columns }, (_, i) => ({ label: colLabel(i), variables: [] })),
    };
  }

  function wellIdList(dimensions) {
    const dims = dimensionsFromAny(dimensions);
    const out = [];
    for (let r = 0; r < dims.rows; r += 1) {
      for (let c = 1; c <= dims.columns; c += 1) out.push(`${rowLabel(r)}${c}`);
    }
    return out;
  }

  function buildWellsFromAxes(design, incomingWells) {
    const dims = dimensionsFromAny(design.dimensions);
    const axes = design.axes || defaultAxes(dims);
    const wellsIn = incomingWells && typeof incomingWells === "object" ? incomingWells : {};
    const out = {};

    for (let r = 0; r < dims.rows; r += 1) {
      for (let c = 0; c < dims.columns; c += 1) {
        const wid = `${rowLabel(r)}${c + 1}`;
        const rowAxis = axes.rows[r] || { label: rowLabel(r), variables: [] };
        const colAxis = axes.columns[c] || { label: colLabel(c), variables: [] };
        const existing = wellsIn[wid] && typeof wellsIn[wid] === "object" ? wellsIn[wid] : {};
        let unique = [];
        if (Array.isArray(existing.unique_components)) unique = dedupeComponents(existing.unique_components);
        else if (Array.isArray(existing.components)) unique = dedupeComponents(existing.components);
        if (!unique.length) unique = dedupeComponents([...(rowAxis.variables || []), ...(colAxis.variables || [])]);

        out[wid] = {
          row_label: cleanText(existing.row_label) || cleanText(rowAxis.label) || rowLabel(r),
          column_label: cleanText(existing.column_label) || cleanText(colAxis.label) || colLabel(c),
          unique_components: unique,
        };
      }
    }
    return out;
  }

  function normalizeDesign(raw) {
    const payload = raw && typeof raw === "object" ? raw : {};
    const dimensions = dimensionsFromAny(payload.dimensions);
    const name = cleanText(payload.name || payload.plate_design_name || payload.plate_design) || "Untitled_Design";
    const globalComponents = dedupeComponents(payload.global_components || []);

    const axesRaw = payload.axes && typeof payload.axes === "object" ? payload.axes : {};
    const defaults = defaultAxes(dimensions);
    const axes = { rows: [], columns: [] };

    for (let i = 0; i < dimensions.rows; i += 1) {
      const item = Array.isArray(axesRaw.rows) ? axesRaw.rows[i] : null;
      const vars = item && Array.isArray(item.variables) ? dedupeComponents(item.variables) : [];
      axes.rows.push({
        label: cleanText(item && item.label) || defaults.rows[i].label,
        variables: vars,
      });
    }
    for (let i = 0; i < dimensions.columns; i += 1) {
      const item = Array.isArray(axesRaw.columns) ? axesRaw.columns[i] : null;
      const vars = item && Array.isArray(item.variables) ? dedupeComponents(item.variables) : [];
      axes.columns.push({
        label: cleanText(item && item.label) || defaults.columns[i].label,
        variables: vars,
      });
    }

    const design = {
      id: payload.id || null,
      name,
      plate_design_name: name,
      dimensions,
      global_components: globalComponents,
      axes,
      wells: {},
    };
    design.wells = buildWellsFromAxes(design, payload.wells);
    return design;
  }

  function formatComponent(comp) {
    const name = cleanText(comp.name) || cleanText(comp.chem_id) || "component";
    const role = cleanText(comp.role) || "component";
    const val = role.toLowerCase() === "solvent"
      ? cleanText(comp.fraction)
      : cleanText(comp.equivalents);
    const suffix = val ? ` (${val})` : "";
    return `${name}${suffix}`;
  }

  function componentListHtml(components, itemClass) {
    const list = Array.isArray(components) ? components : [];
    if (!list.length) return `<div class="text-slate-500 text-sm">No components.</div>`;
    return list.map((comp) => {
      const role = cleanText(comp.role) || "-";
      const line = formatComponent(comp);
      return `<div class="${itemClass}">
        <div class="item-role">${role}</div>
        <div>${line}</div>
      </div>`;
    }).join("");
  }

  function reactionLabel(item, fallback) {
    if (!item || typeof item !== "object") return fallback;
    return cleanText(item.name || item.reactant_name || item.product_name || item.chem_id) || fallback;
  }

  function reactionNodes(entries) {
    const raw = Array.isArray(entries) ? entries : [];
    const dedup = [];
    const seen = new Set();
    raw.forEach((item) => {
      const smiles = cleanText(item && item.smiles).toLowerCase();
      const name = reactionLabel(item, "").toLowerCase();
      const key = `${smiles}|${name}`;
      if (seen.has(key)) return;
      seen.add(key);
      dedup.push(item || {});
    });
    return dedup;
  }

  function shortReactionTag(item, prefix, index) {
    const candidates = [
      cleanText(item && item.short_label),
      cleanText(item && item.unique_id),
      cleanText(item && item.reactant_id),
      cleanText(item && item.product_id),
    ].filter(Boolean);
    const shortId = candidates.find((x) => x.length <= 14 && !x.includes(" "));
    return shortId || `${prefix} ${index + 1}`;
  }

  function reactionNodeHtml(item, tagLabel) {
    const svg = cleanText(item && item.structure_svg);
    const body = (svg && svg.includes("<svg"))
      ? `<div class="reaction-structure">${themeReactionSvg(svg)}</div>`
      : `<div class="reaction-placeholder">No 2D structure</div>`;
    return `<div class="reaction-node">
      ${body}
      <div class="reaction-node-label">${tagLabel}</div>
    </div>`;
  }

  function reactionGroupHtml(items, prefix) {
    const nodes = items.map((item, idx) => reactionNodeHtml(item, shortReactionTag(item, prefix, idx)));
    if (!nodes.length) {
      return `<div class="reaction-group">${reactionNodeHtml({}, `${prefix} 1`)}</div>`;
    }
    return `<div class="reaction-group">${nodes.join(`<div class="reaction-connector">+</div>`)}</div>`;
  }

  function reactionEquationHtml(stItems, pdItems) {
    const left = reactionGroupHtml(stItems, "SM");
    const right = pdItems.length ? reactionGroupHtml(pdItems, "P") : "";
    return `<div class="reaction-flow">
      ${left}
      <div class="reaction-connector reaction-arrow">\u2192</div>
      ${right}
    </div>`;
  }

  function themeReactionSvg(svgText) {
    try {
      const parser = new DOMParser();
      const doc = parser.parseFromString(svgText, "image/svg+xml");
      const svg = doc.querySelector("svg");
      if (!svg) return svgText;

      svg.setAttribute("width", "100%");
      svg.setAttribute("height", "100%");
      svg.style.background = "transparent";

      const isWhite = (val) => {
        const v = cleanText(val).toLowerCase();
        return v === "white" || v === "#fff" || v === "#ffffff" || v === "rgb(255,255,255)" || v === "rgba(255,255,255,1)";
      };
      const isBlack = (val) => {
        const v = cleanText(val).toLowerCase();
        return v === "black" || v === "#000" || v === "#000000" || v === "rgb(0,0,0)" || v === "rgba(0,0,0,1)";
      };

      svg.querySelectorAll("rect").forEach((rect) => {
        const fill = rect.getAttribute("fill");
        if (isWhite(fill)) {
          rect.setAttribute("fill", "none");
          rect.setAttribute("stroke", "none");
        }
      });

      const fgColor = "#dbeafe";
      svg.querySelectorAll("*").forEach((node) => {
        const stroke = node.getAttribute("stroke");
        const fill = node.getAttribute("fill");
        if (isBlack(stroke)) node.setAttribute("stroke", fgColor);
        if (isBlack(fill)) node.setAttribute("fill", fgColor);
      });

      return svg.outerHTML;
    } catch (_) {
      return svgText;
    }
  }

  function statusBadge(status) {
    const value = status || "Planning";
    let cls = "status-planning";
    if (value === "Awaiting Analysis") cls = "status-awaiting-analysis";
    if (value === "Awaiting Validation") cls = "status-awaiting-validation";
    if (value === "Completed") cls = "status-completed";
    return `<span class="status-badge ${cls}">${value}</span>`;
  }

  function formatDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toISOString().slice(0, 10);
  }

  function axisSummaryText(axisItem) {
    const vars = Array.isArray(axisItem.variables) ? axisItem.variables : [];
    if (!vars.length) return "No variable assigned";
    return vars.map((c) => formatComponent(c)).join(" + ");
  }

  function swapAxis(design, axisName, fromIndex, toIndex) {
    const axis = design.axes && design.axes[axisName];
    if (!axis || fromIndex === toIndex) return;
    if (fromIndex < 0 || toIndex < 0 || fromIndex >= axis.length || toIndex >= axis.length) return;
    const tmp = axis[fromIndex];
    axis[fromIndex] = axis[toIndex];
    axis[toIndex] = tmp;
    design.wells = buildWellsFromAxes(design, null);
  }

  function renderPlateMatrix(container, design, opts) {
    const options = opts || {};
    if (!container) return;
    if (!design) {
      container.innerHTML = `<div class="text-slate-500 text-sm">No design loaded.</div>`;
      return;
    }
    const dims = dimensionsFromAny(design.dimensions);
    const selectedWell = options.selectedWell || null;
    const editable = !!options.editable;
    const onSelectWell = options.onSelectWell || function () {};
    const onSwap = options.onSwap || function () {};

    const colSwapOptions = (idx) => {
      if (!editable) return "";
      let html = `<option value="">Swap with...</option>`;
      for (let i = 0; i < dims.columns; i += 1) {
        if (i === idx) continue;
        html += `<option value="${i}">Column ${i + 1}</option>`;
      }
      return html;
    };
    const rowSwapOptions = (idx) => {
      if (!editable) return "";
      let html = `<option value="">Swap with...</option>`;
      for (let i = 0; i < dims.rows; i += 1) {
        if (i === idx) continue;
        html += `<option value="${i}">Row ${rowLabel(i)}</option>`;
      }
      return html;
    };

    let html = `<table class="plate-matrix"><thead><tr><th class="axis-corner">Rows / Columns</th>`;
    for (let c = 0; c < dims.columns; c += 1) {
      const colAxis = design.axes.columns[c] || { label: colLabel(c), variables: [] };
      const dragAttr = editable ? `draggable="true" data-axis="columns" data-index="${c}"` : "";
      html += `<th class="axis-header" ${dragAttr}>
        <div class="axis-title">Column ${c + 1} · ${cleanText(colAxis.label) || colLabel(c)}</div>
        <div class="axis-components">${axisSummaryText(colAxis)}</div>
        ${editable ? `<select class="axis-swap-select" data-axis-select="columns" data-index="${c}">${colSwapOptions(c)}</select>` : ""}
      </th>`;
    }
    html += `</tr></thead><tbody>`;

    for (let r = 0; r < dims.rows; r += 1) {
      const rowAxis = design.axes.rows[r] || { label: rowLabel(r), variables: [] };
      const rowDragAttr = editable ? `draggable="true" data-axis="rows" data-index="${r}"` : "";
      html += `<tr>
        <th class="axis-header" ${rowDragAttr}>
          <div class="axis-title">Row ${rowLabel(r)} · ${cleanText(rowAxis.label) || rowLabel(r)}</div>
          <div class="axis-components">${axisSummaryText(rowAxis)}</div>
          ${editable ? `<select class="axis-swap-select" data-axis-select="rows" data-index="${r}">${rowSwapOptions(r)}</select>` : ""}
        </th>`;

      for (let c = 0; c < dims.columns; c += 1) {
        const wid = `${rowLabel(r)}${c + 1}`;
        const well = design.wells[wid] || { unique_components: [] };
        const comps = Array.isArray(well.unique_components) ? well.unique_components : [];
        const active = selectedWell === wid ? "active" : "";
        const summary = comps.slice(0, 2).map((x) => formatComponent(x)).join(", ");
        const extra = comps.length > 2 ? ` +${comps.length - 2}` : "";
        html += `<td class="matrix-well ${active}" data-well-id="${wid}">
          <div class="well-id">${wid}</div>
          <div class="well-components">${summary || "No variable components"}${extra}</div>
        </td>`;
      }
      html += `</tr>`;
    }
    html += `</tbody></table>`;
    container.innerHTML = html;

    container.querySelectorAll(".matrix-well").forEach((node) => {
      node.addEventListener("click", () => onSelectWell(node.dataset.wellId));
    });

    container.querySelectorAll("select[data-axis-select]").forEach((node) => {
      node.addEventListener("change", () => {
        const axis = node.getAttribute("data-axis-select");
        const from = Number(node.dataset.index || -1);
        const to = Number(node.value);
        if (!Number.isInteger(from) || !Number.isInteger(to)) return;
        onSwap(axis, from, to);
      });
    });

    if (!editable) return;

    let dragSource = null;
    container.querySelectorAll(".axis-header[draggable='true']").forEach((node) => {
      node.addEventListener("dragstart", (ev) => {
        dragSource = {
          axis: node.dataset.axis,
          index: Number(node.dataset.index || -1),
        };
        if (ev.dataTransfer) ev.dataTransfer.effectAllowed = "move";
      });
      node.addEventListener("dragover", (ev) => {
        if (!dragSource) return;
        const axis = node.dataset.axis;
        if (axis !== dragSource.axis) return;
        ev.preventDefault();
        node.classList.add("drag-over");
      });
      node.addEventListener("dragleave", () => {
        node.classList.remove("drag-over");
      });
      node.addEventListener("drop", (ev) => {
        ev.preventDefault();
        node.classList.remove("drag-over");
        if (!dragSource) return;
        const targetAxis = node.dataset.axis;
        const targetIndex = Number(node.dataset.index || -1);
        if (targetAxis !== dragSource.axis) return;
        if (!Number.isInteger(targetIndex)) return;
        onSwap(targetAxis, dragSource.index, targetIndex);
        dragSource = null;
      });
      node.addEventListener("dragend", () => {
        dragSource = null;
        container.querySelectorAll(".axis-header").forEach((h) => h.classList.remove("drag-over"));
      });
    });
  }

  function updateJsonPreview(target, design) {
    if (!target) return;
    if (!design) {
      target.value = "";
      return;
    }
    target.value = JSON.stringify({
      plate_design_name: design.name,
      dimensions: design.dimensions,
      global_components: design.global_components,
      axes: design.axes,
      wells: design.wells,
    }, null, 2);
  }

  // ---------------- Dashboard ----------------
  function initDashboard() {
    const body = el("screeningsTableBody");
    const queryInput = el("screeningsQuery");
    const statusSelect = el("screeningsStatus");
    const btnApply = el("screeningsFilterBtn");
    const btnPrev = el("screeningsPrev");
    const btnNext = el("screeningsNext");
    const pageInfo = el("screeningsPageInfo");

    const state = { page: 1, pageSize: 20, total: 0 };

    async function load() {
      body.innerHTML = `<tr><td colspan="8" class="text-center text-slate-400 py-6">Loading...</td></tr>`;
      try {
        const q = encodeURIComponent(cleanText(queryInput.value));
        const s = encodeURIComponent(cleanText(statusSelect.value));
        const data = await fetchJson(`/api/screenings?page=${state.page}&page_size=${state.pageSize}&query=${q}&status=${s}`);
        state.total = Number(data.total || 0);
        const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
        pageInfo.textContent = `Page ${state.page} / ${totalPages} · ${state.total} screenings`;

        if (!data.items || !data.items.length) {
          body.innerHTML = `<tr><td colspan="8" class="text-center text-slate-400 py-6">No screenings found.</td></tr>`;
          return;
        }

        body.innerHTML = data.items.map((item) => `
          <tr>
            <td><a href="/screenings/${encodeURIComponent(item.eln_id)}">${item.eln_id}</a></td>
            <td>${item.project_name || ""}</td>
            <td>${item.project_id || ""}</td>
            <td>${item.theme_number || ""}</td>
            <td>${formatDate(item.date)}</td>
            <td>${item.user || ""}</td>
            <td>${item.plate_design_name || ""}</td>
            <td>${statusBadge(item.status)}</td>
          </tr>
        `).join("");
      } catch (err) {
        body.innerHTML = `<tr><td colspan="8" class="text-center text-red-300 py-6">${err.message}</td></tr>`;
      }
    }

    btnApply.addEventListener("click", () => {
      state.page = 1;
      load();
    });
    queryInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        state.page = 1;
        load();
      }
    });
    btnPrev.addEventListener("click", () => {
      if (state.page > 1) {
        state.page -= 1;
        load();
      }
    });
    btnNext.addEventListener("click", () => {
      const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
      if (state.page < totalPages) {
        state.page += 1;
        load();
      }
    });

    load();
  }

  // ---------------- New screening wizard ----------------
  function initNewScreening() {
    const state = {
      parsed: null,
      designs: [],
      workingDesign: null,
      selectedWell: null,
      strategy: "existing",
    };

    const parseElnBtn = el("parseElnBtn");
    const elnFileInput = el("elnFileInput");
    const elnSummary = el("elnSummary");
    const manualPhotochem = el("manualPhotochem");
    const manualWavelengthWrap = el("manualWavelengthWrap");

    const existingDesignWrap = el("existingDesignWrap");
    const newDesignWrap = el("newDesignWrap");
    const existingDesignSelect = el("existingDesignSelect");
    const addSlotBtn = el("addSlotBtn");
    const slotContainer = el("slotContainer");
    const generateLayoutBtn = el("generateLayoutBtn");
    const plateSize = el("plateSize");
    const designNameInput = el("designNameInput");
    const finalizeBtn = el("finalizeBtn");
    const finalizeStatus = el("finalizeStatus");

    const designGrid = el("designGrid");
    const globalBox = el("globalComponentsBox");
    const selectedWellPanel = el("selectedWellPanel");
    const selectedWellLabel = el("selectedWellLabel");
    const designJsonPreview = el("designJsonPreview");

    function setStatus(msg, isError) {
      finalizeStatus.textContent = msg || "";
      finalizeStatus.className = isError ? "text-sm text-red-300 mt-3" : "text-sm text-slate-300 mt-3";
    }

    function currentManualMetadata() {
      const isPhotochemistry = !!manualPhotochem.checked;
      return {
        atmosphere: cleanText(el("manualAtmosphere").value),
        temperature: cleanText(el("manualTemperature").value),
        reaction_time: cleanText(el("manualReactionTime").value),
        agitation: cleanText(el("manualAgitation").value),
        photochemistry: isPhotochemistry,
        wavelength: isPhotochemistry ? cleanText(el("manualWavelength").value) : null,
      };
    }

    function renderSelectedWell() {
      if (!state.workingDesign || !state.selectedWell) {
        selectedWellLabel.textContent = "No well selected";
        selectedWellPanel.innerHTML = "Click a well to inspect.";
        return;
      }
      const well = state.workingDesign.wells[state.selectedWell] || { unique_components: [] };
      selectedWellLabel.textContent = `Selected: ${state.selectedWell}`;
      selectedWellPanel.innerHTML = componentListHtml(well.unique_components || [], "well-item");
    }

    function renderWorkingDesign() {
      if (!state.workingDesign) {
        renderPlateMatrix(designGrid, null, {});
        if (globalBox) globalBox.innerHTML = `<div class="text-slate-500 text-sm">No global components.</div>`;
        if (selectedWellPanel) selectedWellPanel.textContent = "Click a well to inspect.";
        updateJsonPreview(designJsonPreview, null);
        return;
      }
      const editable = state.strategy !== "existing";
      renderPlateMatrix(designGrid, state.workingDesign, {
        editable,
        selectedWell: state.selectedWell,
        onSelectWell: (wellId) => {
          state.selectedWell = wellId;
          renderWorkingDesign();
        },
        onSwap: (axis, from, to) => {
          swapAxis(state.workingDesign, axis, from, to);
          state.selectedWell = null;
          renderWorkingDesign();
        },
      });
      if (globalBox) {
        globalBox.innerHTML = componentListHtml(state.workingDesign.global_components || [], "global-item");
      }
      renderSelectedWell();
      updateJsonPreview(designJsonPreview, state.workingDesign);
    }

    function setWorkingDesign(rawDesign) {
      if (!rawDesign) {
        state.workingDesign = null;
        state.selectedWell = null;
        renderWorkingDesign();
        return;
      }
      state.workingDesign = normalizeDesign(rawDesign);
      state.selectedWell = null;
      if (!cleanText(designNameInput.value)) designNameInput.value = state.workingDesign.name;
      renderWorkingDesign();
    }

    function strategyChanged(value) {
      state.strategy = value;
      if (value === "new") {
        existingDesignWrap.classList.add("hidden");
        newDesignWrap.classList.remove("hidden");
      } else {
        existingDesignWrap.classList.remove("hidden");
        newDesignWrap.classList.add("hidden");
        loadSelectedDesign().catch((err) => setStatus(err.message, true));
      }
      renderWorkingDesign();
    }

    function slotCardTemplate(slotId) {
      return `
        <div class="slot-card" data-slot-id="${slotId}">
          <div class="slot-row-top">
            <input class="screenings-input slot-name" placeholder="Slot name (e.g. Ligand_1)">
            <select class="screenings-input slot-role">
              <option value="Reagent">Reagent</option>
              <option value="Catalyst">Catalyst</option>
              <option value="Ligand">Ligand</option>
              <option value="Additive">Additive</option>
              <option value="Solvent">Solvent</option>
              <option value="StMat">StMat</option>
            </select>
            <input class="screenings-input slot-value-label" placeholder="default eq/fraction">
            <button type="button" class="screenings-btn-secondary remove-slot-btn">Remove Slot</button>
          </div>
          <div class="slot-options"></div>
          <div class="flex gap-2">
            <button type="button" class="screenings-btn-secondary add-option-btn">Add Chemical Option</button>
          </div>
        </div>
      `;
    }

    function optionRowTemplate(optionId) {
      const listId = `chem_options_${optionId}`;
      return `
        <div class="slot-option-row" data-option-id="${optionId}">
          <input class="screenings-input opt-name" placeholder="Name">
          <input class="screenings-input opt-chem-id" placeholder="Chem_ID">
          <input class="screenings-input opt-smiles" placeholder="SMILES (optional)">
          <input class="screenings-input opt-value" placeholder="eq/fraction">
          <button type="button" class="screenings-btn-secondary remove-option-btn">X</button>
          <input class="screenings-input opt-lookup" list="${listId}" placeholder="Search in Chemicals DB (name, CAS, Chem_ID)">
          <datalist id="${listId}"></datalist>
        </div>
      `;
    }

    function addOptionRow(slotEl, initial) {
      const optionsHost = slotEl.querySelector(".slot-options");
      const optionId = `${Date.now()}_${Math.floor(Math.random() * 10000)}`;
      const wrap = document.createElement("div");
      wrap.innerHTML = optionRowTemplate(optionId);
      const row = wrap.firstElementChild;
      optionsHost.appendChild(row);

      const nameInput = row.querySelector(".opt-name");
      const chemIdInput = row.querySelector(".opt-chem-id");
      const smilesInput = row.querySelector(".opt-smiles");
      const valueInput = row.querySelector(".opt-value");
      const lookupInput = row.querySelector(".opt-lookup");
      const datalist = row.querySelector("datalist");

      if (initial) {
        nameInput.value = cleanText(initial.name);
        chemIdInput.value = cleanText(initial.chem_id);
        smilesInput.value = cleanText(initial.smiles);
        valueInput.value = cleanText(initial.equivalents || initial.fraction);
      }

      let timer = null;
      let latestResults = [];
      lookupInput.addEventListener("input", () => {
        if (timer) clearTimeout(timer);
        const q = cleanText(lookupInput.value);
        if (q.length < 2) return;
        timer = setTimeout(async () => {
          try {
            const data = await fetchJson(`/api/chemicals/search?query=${encodeURIComponent(q)}&limit=8`);
            latestResults = data.results || [];
            datalist.innerHTML = latestResults
              .map((r) => `<option value="${r.chem_id} | ${r.common_name || ""}"></option>`)
              .join("");
          } catch (_) {
            latestResults = [];
          }
        }, 250);
      });

      lookupInput.addEventListener("change", () => {
        const target = cleanText(lookupInput.value).split("|")[0].trim().toLowerCase();
        const selected = latestResults.find((r) => cleanText(r.chem_id).toLowerCase() === target) || latestResults[0];
        if (!selected) return;
        if (!cleanText(nameInput.value)) nameInput.value = cleanText(selected.common_name);
        chemIdInput.value = cleanText(selected.chem_id);
        if (!cleanText(smilesInput.value)) smilesInput.value = cleanText(selected.smiles);
      });

      row.querySelector(".remove-option-btn").addEventListener("click", () => row.remove());
    }

    function addSlot(initial) {
      const slotId = `${Date.now()}_${Math.floor(Math.random() * 10000)}`;
      const wrap = document.createElement("div");
      wrap.innerHTML = slotCardTemplate(slotId);
      const slotEl = wrap.firstElementChild;
      slotContainer.appendChild(slotEl);

      const slotName = slotEl.querySelector(".slot-name");
      const slotRole = slotEl.querySelector(".slot-role");
      const slotValueLabel = slotEl.querySelector(".slot-value-label");
      slotName.value = cleanText(initial && initial.slot);
      slotRole.value = cleanText(initial && initial.role) || "Reagent";
      slotValueLabel.value = cleanText(initial && initial.defaultValue);

      slotEl.querySelector(".remove-slot-btn").addEventListener("click", () => slotEl.remove());
      slotEl.querySelector(".add-option-btn").addEventListener("click", () => addOptionRow(slotEl));

      const options = initial && Array.isArray(initial.options) ? initial.options : [];
      if (options.length) options.forEach((opt) => addOptionRow(slotEl, opt));
      else addOptionRow(slotEl);
    }

    function collectComponentsByRole() {
      const out = {};
      slotContainer.querySelectorAll(".slot-card").forEach((slotEl, index) => {
        const slotNameRaw = cleanText(slotEl.querySelector(".slot-name").value);
        const slotRole = cleanText(slotEl.querySelector(".slot-role").value) || "Reagent";
        const valueLabel = cleanText(slotEl.querySelector(".slot-value-label").value);
        const slotName = slotNameRaw || `${slotRole}_${index + 1}`;
        const options = [];

        slotEl.querySelectorAll(".slot-option-row").forEach((optEl) => {
          const name = cleanText(optEl.querySelector(".opt-name").value);
          const chemId = cleanText(optEl.querySelector(".opt-chem-id").value);
          const smiles = cleanText(optEl.querySelector(".opt-smiles").value);
          const value = cleanText(optEl.querySelector(".opt-value").value) || valueLabel;
          if (!name && !chemId) return;
          const item = {
            name: name || chemId,
            chem_id: chemId || null,
            smiles: smiles || null,
            role: slotRole,
          };
          if (slotRole.toLowerCase() === "solvent") item.fraction = value || "";
          else item.equivalents = value || "";
          options.push(item);
        });
        if (!options.length) return;
        out[slotName] = { role: slotRole, options };
      });
      return out;
    }

    async function loadDesignList() {
      const data = await fetchJson("/api/plate-designs");
      state.designs = data.items || [];
      existingDesignSelect.innerHTML = state.designs.length
        ? state.designs.map((d) => `<option value="${d.id}">${d.name}</option>`).join("")
        : `<option value="">No saved designs</option>`;
      if (state.designs.length) await loadSelectedDesign();
      else setWorkingDesign(null);
    }

    async function loadSelectedDesign() {
      const designId = cleanText(existingDesignSelect.value);
      if (!designId) {
        setWorkingDesign(null);
        return;
      }
      const data = await fetchJson(`/api/plate-designs/${encodeURIComponent(designId)}`);
      setWorkingDesign(data.item);
      if (state.strategy === "existing") {
        designNameInput.value = cleanText(data.item.name);
      } else if (state.strategy === "edit") {
        designNameInput.value = `${cleanText(data.item.name)}_edited`;
      }
    }

    async function parseEln() {
      const file = elnFileInput.files && elnFileInput.files[0];
      if (!file) throw new Error("Select an ELN export file first.");
      const form = new FormData();
      form.append("file", file);
      const resp = await fetch("/api/screenings/parse-eln", { method: "POST", body: form });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || data.ok === false) throw new Error(data.error || "Failed to parse ELN file");
      state.parsed = data;

      const m = data.metadata || {};
      const st = data.eln_stmat_data || [];
      const pd = data.eln_product_data || [];
      elnSummary.innerHTML = `
        <div><b>ELN-ID:</b> ${m.eln_id || "-"}</div>
        <div><b>Date:</b> ${formatDate(m.date)}</div>
        <div><b>User:</b> ${m.user || "-"}</div>
        <div><b>Project:</b> ${m.project_name || "-"} (${m.project_id || "-"})</div>
        <div><b>Theme nº:</b> ${m.theme_number || "-"}</div>
        <div><b>Scale:</b> ${m.scale || "-"}</div>
        <div class="mt-2"><b>StMat entries:</b> ${st.length}</div>
        <div><b>Product entries:</b> ${pd.length}</div>
      `;
    }

    async function generateLayout() {
      const components = collectComponentsByRole();
      if (!Object.keys(components).length) throw new Error("Add at least one role slot with options.");
      const payload = {
        plate_size: Number(plateSize.value || 24),
        components_by_role: components,
        plate_design: cleanText(designNameInput.value) || undefined,
      };
      const data = await fetchJson("/api/screenings/generate-layout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setWorkingDesign(data.design);
      if (!cleanText(designNameInput.value)) designNameInput.value = cleanText(data.design.plate_design_name || data.design.name);
      if (data.source === "fallback") {
        const reason = cleanText(data.ai_error);
        if (reason) {
          setStatus(`AI response unavailable or invalid (${reason}). Deterministic fallback layout was generated.`, false);
        } else {
          setStatus("AI response unavailable or invalid. Deterministic fallback layout was generated.", false);
        }
      } else {
        setStatus("AI-optimized matrix generated.", false);
      }
    }

    async function finalize() {
      if (!state.parsed || !state.parsed.metadata || !cleanText(state.parsed.metadata.eln_id)) {
        throw new Error("Parse the ELN export first.");
      }
      if (!state.workingDesign && state.strategy !== "existing") {
        throw new Error("Generate or load a design first.");
      }

      let designId = null;
      if (state.strategy === "existing") {
        designId = cleanText(existingDesignSelect.value) || null;
        if (!designId) throw new Error("Select an existing design.");
      } else {
        const designName = cleanText(designNameInput.value);
        if (!designName) throw new Error("Provide a unique design name.");
        const saveResp = await fetchJson("/api/plate-designs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: designName,
            dimensions: state.workingDesign.dimensions,
            global_components: state.workingDesign.global_components || [],
            axes: state.workingDesign.axes || {},
            wells: state.workingDesign.wells || {},
          }),
        });
        designId = saveResp.item.id;
      }

      const metadata = state.parsed.metadata || {};
      const manualData = currentManualMetadata();
      await fetchJson("/api/screenings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          eln_id: metadata.eln_id,
          project_name: metadata.project_name || "",
          project_id: metadata.project_id || "",
          theme_number: metadata.theme_number || "",
          date: metadata.date || null,
          user: metadata.user || "",
          scale: metadata.scale || "",
          status: "Planning",
          is_photochemistry: !!manualData.photochemistry,
          wavelength_nm: manualData.photochemistry ? manualData.wavelength : null,
          plate_design_id: designId,
          manual_metadata: manualData,
          eln_stmat_data: state.parsed.eln_stmat_data || [],
          eln_product_data: state.parsed.eln_product_data || [],
        }),
      });

      const surfResp = await fetch(`/api/screenings/${encodeURIComponent(metadata.eln_id)}/generate-surf`, { method: "POST" });
      if (!surfResp.ok) {
        const err = await surfResp.json().catch(() => ({}));
        throw new Error(err.error || "Failed to generate SURF.");
      }
      const blob = await surfResp.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${metadata.eln_id}_provisional_surf.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      setStatus("SURF generated and downloaded. Redirecting to dashboard...", false);
      setTimeout(() => {
        window.location.href = "/screenings";
      }, 1100);
    }

    parseElnBtn.addEventListener("click", async () => {
      setStatus("", false);
      try {
        parseElnBtn.disabled = true;
        parseElnBtn.textContent = "Parsing...";
        await parseEln();
      } catch (err) {
        setStatus(err.message, true);
      } finally {
        parseElnBtn.disabled = false;
        parseElnBtn.textContent = "Parse ELN Export";
      }
    });

    manualPhotochem.addEventListener("change", () => {
      manualWavelengthWrap.classList.toggle("hidden", !manualPhotochem.checked);
    });
    manualWavelengthWrap.classList.toggle("hidden", !manualPhotochem.checked);

    document.querySelectorAll("input[name='designStrategy']").forEach((node) => {
      node.addEventListener("change", (ev) => strategyChanged(ev.target.value));
    });

    existingDesignSelect.addEventListener("change", () => {
      loadSelectedDesign().catch((err) => setStatus(err.message, true));
    });

    addSlotBtn.addEventListener("click", () => addSlot());
    generateLayoutBtn.addEventListener("click", async () => {
      setStatus("", false);
      try {
        generateLayoutBtn.disabled = true;
        generateLayoutBtn.textContent = "Generating...";
        await generateLayout();
      } catch (err) {
        setStatus(err.message, true);
      } finally {
        generateLayoutBtn.disabled = false;
        generateLayoutBtn.textContent = "Generate Layout";
      }
    });

    finalizeBtn.addEventListener("click", async () => {
      setStatus("", false);
      try {
        finalizeBtn.disabled = true;
        finalizeBtn.textContent = "Finalizing...";
        setStatus("Saving screening and generating SURF...", false);
        await finalize();
      } catch (err) {
        setStatus(err.message, true);
      } finally {
        finalizeBtn.disabled = false;
        finalizeBtn.textContent = "Save + Generate SURF";
      }
    });

    addSlot({
      slot: "Reagent_1",
      role: "Reagent",
      options: [{ name: "", chem_id: "", equivalents: "1.0" }],
    });
    strategyChanged("existing");
    loadDesignList().catch((err) => setStatus(err.message, true));
  }

  // ---------------- Detail page ----------------
  function initDetail() {
    const elnId = root.dataset.elnId;
    const meta = el("detailMeta");
    const grid = el("detailGrid");
    const reactionBox = el("detailReactionBox");
    const viewer = el("detailWellViewer");
    const label = el("detailWellLabel");
    const analyseBtn = el("analyseBtn");
    const validateBtn = el("validateBtn");
    const dlFinalBtn = el("downloadFinalSurfBtn");
    const dlImagesBtn = el("downloadImagesBtn");
    const msg = el("detailActionMsg");
    const analyseModal = el("analyseModal");
    const analyseModalClose = el("analyseModalClose");
    const analyseRptFile = el("analyseRptFile");
    const analyseType = el("analyseType");
    const analyseProductsBlock = el("analyseProductsBlock");
    const analyseProductsList = el("analyseProductsList");
    const analyseAddProductBtn = el("analyseAddProductBtn");
    const analyseCustomList = el("analyseCustomList");
    const analyseAddCustomBtn = el("analyseAddCustomBtn");
    const analyseYieldBlock = el("analyseYieldBlock");
    const analysePreviewTargetsBtn = el("analysePreviewTargetsBtn");
    const analyseTargetsTable = el("analyseTargetsTable");
    const analyseRunBtn = el("analyseRunBtn");
    const analyseModalMsg = el("analyseModalMsg");
    const analyseIsFormula = el("analyseIsFormula");
    const analyseRf = el("analyseRf");
    const analyseIsConc = el("analyseIsConc");
    const analyseVol = el("analyseVol");
    const analyseScale = el("analyseScale");
    const validateModal = el("validateModal");
    const validateModalClose = el("validateModalClose");
    const validateImages = el("validateImages");
    const validatePeaksWrap = el("validatePeaksWrap");
    const updateRolesBtn = el("updateRolesBtn");
    const validateTableWrap = el("validateTableWrap");
    const validateMsg = el("validateMsg");
    const finishValidationBtn = el("finishValidationBtn");

    const state = {
      item: null,
      selectedWell: null,
      pollTimer: null,
      analysisResults: null,
    };

    function renderMeta(item) {
      const stmat = item.eln_stmat_data || [];
      const products = item.eln_product_data || [];
      const lcmsStatus = item.lcms_status || "idle";
      meta.innerHTML = `
        <div><b>Status:</b> ${statusBadge(item.status)}</div>
        <div><b>LC/MS:</b> ${lcmsStatus}</div>
        <div><b>Project:</b> ${item.project_name || "-"} (${item.project_id || "-"})</div>
        <div><b>Theme nº:</b> ${item.theme_number || "-"}</div>
        <div><b>User:</b> ${item.user || "-"}</div>
        <div><b>Date:</b> ${formatDate(item.date)}</div>
        <div><b>Scale:</b> ${item.scale || "-"}</div>
        <div class="mt-2"><b>StMat entries:</b> ${stmat.length}</div>
        <div><b>Product entries:</b> ${products.length}</div>
      `;
    }

    function renderReactionBox(item) {
      const stList = reactionNodes(item.eln_stmat_data);
      const pdList = reactionNodes(item.eln_product_data);
      reactionBox.innerHTML = reactionEquationHtml(stList, pdList);
    }

    function renderActions(item) {
      const status = item.status;
      const lcmsStatus = item.lcms_status || "idle";
      const running = lcmsStatus === "running";
      analyseBtn.disabled = running || !(status === "Planning" || status === "Awaiting Analysis");
      validateBtn.disabled = !(status === "Awaiting Validation" || lcmsStatus === "done" || lcmsStatus === "validated");
      analyseBtn.classList.toggle("opacity-50", analyseBtn.disabled);
      validateBtn.classList.toggle("opacity-50", validateBtn.disabled);
      analyseBtn.textContent = running ? "Analysing..." : "Analyse";

      const finalReady = !!item.lcms_final_surf_ready;
      dlFinalBtn.classList.toggle("hidden", !finalReady);
      dlImagesBtn.classList.toggle("hidden", !finalReady);
      dlFinalBtn.href = `/api/screenings/${encodeURIComponent(elnId)}/analysis/final-surf`;
      dlImagesBtn.href = `/api/screenings/${encodeURIComponent(elnId)}/analysis/images`;
    }

    function renderWellPanel() {
      if (!state.item || !state.item.plate_design || !state.selectedWell) {
        label.textContent = "No well selected";
        viewer.innerHTML = "Click a well to inspect.";
        return;
      }
      const well = (((state.item.plate_design || {}).wells || {})[state.selectedWell]) || { unique_components: [] };
      label.textContent = `Selected: ${state.selectedWell}`;
      viewer.innerHTML = componentListHtml(well.unique_components || [], "well-item");
    }

    function renderDesign() {
      const design = state.item && state.item.plate_design ? normalizeDesign(state.item.plate_design) : null;
      if (!design) {
        renderPlateMatrix(grid, null, {});
        reactionBox.innerHTML = reactionEquationHtml([{}], []);
        renderWellPanel();
        return;
      }
      state.item.plate_design = design;
      renderPlateMatrix(grid, design, {
        editable: false,
        selectedWell: state.selectedWell,
        onSelectWell: (wellId) => {
          state.selectedWell = wellId;
          renderDesign();
          renderWellPanel();
        },
      });
      renderReactionBox(state.item);
      renderWellPanel();
    }

    function stopPolling() {
      if (state.pollTimer) {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
      }
    }

    function startPolling() {
      stopPolling();
      state.pollTimer = setInterval(async () => {
        try {
          const st = await fetchJson(`/api/screenings/${encodeURIComponent(elnId)}/analysis/status`);
          if (st.status === "error") {
            stopPolling();
            msg.textContent = `LC/MS analysis failed: ${st.error || "Unknown error"}`;
            msg.classList.add("text-red-300");
            await load();
          } else if (st.status !== "running") {
            stopPolling();
            await load();
          } else {
            msg.textContent = "LC/MS analysis is running...";
            msg.classList.remove("text-red-300");
          }
        } catch (_) {}
      }, 3500);
    }

    function showAnalyseModalMessage(text, isErr) {
      analyseModalMsg.textContent = text || "";
      analyseModalMsg.classList.toggle("text-red-300", !!isErr);
      analyseModalMsg.classList.toggle("text-slate-400", !isErr);
    }

    function showValidateMessage(text, isErr) {
      validateMsg.textContent = text || "";
      validateMsg.classList.toggle("text-red-300", !!isErr);
      validateMsg.classList.toggle("text-slate-400", !isErr);
    }

    function addCustomTargetRow(item) {
      const row = document.createElement("div");
      row.className = "analyse-list-row";
      row.innerHTML = `
        <input class="screenings-input custom-name" type="text" placeholder="Name" value="${cleanText(item && item.name)}">
        <input class="screenings-input custom-formula" type="text" placeholder="Formula or SMILES" value="${cleanText(item && (item.formula || item.smiles))}">
        <button class="screenings-btn-secondary remove-row-btn" type="button">Remove</button>
      `;
      row.querySelector(".remove-row-btn").addEventListener("click", () => row.remove());
      analyseCustomList.appendChild(row);
    }

    function addProductRow(item) {
      const row = document.createElement("div");
      row.className = "analyse-list-row";
      row.innerHTML = `
        <input class="screenings-input prod-name" type="text" placeholder="Product Name" value="${cleanText(item && item.name)}">
        <input class="screenings-input prod-smiles" type="text" placeholder="Product SMILES or Formula" value="${cleanText(item && (item.smiles || item.formula))}">
        <button class="screenings-btn-secondary remove-row-btn" type="button">Remove</button>
      `;
      row.querySelector(".remove-row-btn").addEventListener("click", () => row.remove());
      analyseProductsList.appendChild(row);
    }

    function collectCustomTargets() {
      const out = [];
      analyseCustomList.querySelectorAll(".analyse-list-row").forEach((row) => {
        const name = cleanText(row.querySelector(".custom-name") && row.querySelector(".custom-name").value);
        const formulaOrSmiles = cleanText(row.querySelector(".custom-formula") && row.querySelector(".custom-formula").value);
        if (!name && !formulaOrSmiles) return;
        out.push({ name: name || formulaOrSmiles, formula: formulaOrSmiles });
      });
      return out;
    }

    function collectProductOverrides() {
      const out = [];
      analyseProductsList.querySelectorAll(".analyse-list-row").forEach((row) => {
        const name = cleanText(row.querySelector(".prod-name") && row.querySelector(".prod-name").value);
        const smiles = cleanText(row.querySelector(".prod-smiles") && row.querySelector(".prod-smiles").value);
        if (!name && !smiles) return;
        out.push({ name: name || "Product", smiles, formula: smiles });
      });
      return out;
    }

    function collectYieldParams() {
      return {
        is_formula: cleanText(analyseIsFormula.value),
        response_factor: cleanText(analyseRf.value),
        conc_is_mM: cleanText(analyseIsConc.value),
        total_volume_mL: cleanText(analyseVol.value),
        reaction_scale_mmol: cleanText(analyseScale.value),
      };
    }

    function renderTargetsTable(targets) {
      const list = Array.isArray(targets) ? targets : [];
      if (!list.length) {
        analyseTargetsTable.innerHTML = `<div class="text-slate-400 text-sm">No targets available.</div>`;
        return;
      }
      const head = `
        <table class="screenings-table">
          <thead>
            <tr><th>Name</th><th>Role</th><th>MW</th><th>[M+H]+</th><th>[M+2H]2+</th><th>[M-H]-</th><th>[M-2H]2-</th></tr>
          </thead>
          <tbody>
      `;
      const rows = list.map((t) => {
        const ad = t.adducts || {};
        return `<tr>
          <td>${t.name || ""}</td>
          <td>${t.role || ""}</td>
          <td>${t.mw || ""}</td>
          <td>${ad["[M+H]+"] || ""}</td>
          <td>${ad["[M+2H]2+"] || ""}</td>
          <td>${ad["[M-H]-"] || ""}</td>
          <td>${ad["[M-2H]2-"] || ""}</td>
        </tr>`;
      }).join("");
      analyseTargetsTable.innerHTML = `${head}${rows}</tbody></table>`;
    }

    async function refreshTargets() {
      try {
        showAnalyseModalMessage("Building targets...", false);
        const payload = {
          products: collectProductOverrides(),
          custom_targets: collectCustomTargets(),
        };
        const data = await fetchJson(`/api/screenings/${encodeURIComponent(elnId)}/analysis/targets`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        analyseProductsBlock.classList.toggle("hidden", !data.requires_products);
        renderTargetsTable(data.targets || []);
        showAnalyseModalMessage("", false);
        return data.targets || [];
      } catch (err) {
        showAnalyseModalMessage(err.message, true);
        return [];
      }
    }

    async function runAnalysis() {
      const file = analyseRptFile.files && analyseRptFile.files[0];
      if (!file) throw new Error("Please upload a .rpt file.");
      // Block submission if the products block is visible but has no valid entries.
      if (!analyseProductsBlock.classList.contains("hidden")) {
        const products = collectProductOverrides();
        if (!products.length) throw new Error("At least one product target is required (add a name and SMILES/formula).");
      }
      const cfg = {
        analysis_type: cleanText(analyseType.value) || "product_formation",
        products: collectProductOverrides(),
        custom_targets: collectCustomTargets(),
        yield_params: collectYieldParams(),
      };
      const form = new FormData();
      form.append("rpt_file", file);
      form.append("config", JSON.stringify(cfg));
      try {
        const resp = await fetch(`/api/screenings/${encodeURIComponent(elnId)}/analysis/start`, {
          method: "POST",
          body: form,
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.ok === false) throw new Error(data.error || "Could not start analysis.");
        return data;
      } catch (err) {
        const msgText = cleanText(err && err.message).toLowerCase();
        if (!msgText.includes("failed to fetch")) throw err;
        // Fallback path: some deployments/proxies drop multipart uploads.
        const rptText = await file.text();
        const data = await fetchJson(`/api/screenings/${encodeURIComponent(elnId)}/analysis/start`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            filename: file.name || "upload.rpt",
            rpt_text: rptText,
            config: cfg,
          }),
        });
        return data;
      }
    }

    function openAnalyseModal() {
      showAnalyseModalMessage("", false);
      analyseModal.classList.remove("hidden");
      analyseProductsList.innerHTML = "";
      analyseCustomList.innerHTML = "";
      if (!(state.item.eln_product_data || []).length) {
        analyseProductsBlock.classList.remove("hidden");
        addProductRow();
      } else {
        analyseProductsBlock.classList.add("hidden");
      }
      refreshTargets().catch(() => {});
    }

    function closeAnalyseModal() {
      analyseModal.classList.add("hidden");
    }

    function wellSortKey(sampleId) {
      const m = /[_]([A-H])(\d{1,2})$/i.exec(String(sampleId || ""));
      if (!m) return [999, 999];
      return [m[1].toUpperCase().charCodeAt(0) - 65, parseInt(m[2], 10)];
    }

    function renderPeaksTable(peaks, targets) {
      if (!validatePeaksWrap) return;
      const raw = Array.isArray(peaks) ? peaks : [];
      if (!raw.length) {
        validatePeaksWrap.innerHTML = `<div class="text-slate-400 text-sm">No peak data available. Re-run the analysis to populate this table.</div>`;
        return;
      }

      // Sort by well (A1→D6) then peak_id ascending, keeping original index for editing
      const indexed = raw.map((p, i) => ({ p, i }));
      indexed.sort((a, b) => {
        const [ar, ac] = wellSortKey(a.p.sample_id);
        const [br, bc] = wellSortKey(b.p.sample_id);
        if (ar !== br) return ar - br;
        if (ac !== bc) return ac - bc;
        return Number(a.p.peak_id || 0) - Number(b.p.peak_id || 0);
      });

      // Collect available role options from targets + existing peak roles
      const roleSet = new Set([""]);
      (targets || []).forEach((t) => {
        const rl = cleanText(t.role_label);
        if (rl) roleSet.add(rl);
      });
      raw.forEach((p) => {
        const r = cleanText(p.role);
        if (r) roleSet.add(r);
      });
      const roleOptions = [...roleSet].sort((a, b) => {
        if (!a) return -1;
        if (!b) return 1;
        return a.localeCompare(b);
      });

      const headers = ["Well", "Sample", "Peak", "RT (min)", "Area", "Top m/z", "Top 5 m/z", "Adduct", "Role", "Source", "Conf."];
      const rows = indexed.map(({ p, i }) => {
        const wellMatch = /[_]([A-H]\d{1,2})$/i.exec(String(p.sample_id || ""));
        const well = wellMatch ? wellMatch[1].toUpperCase() : "";
        const curRole = cleanText(p.role);
        const selectOpts = roleOptions.map((r) =>
          `<option value="${r}" ${r === curRole ? "selected" : ""}>${r || "(none)"}</option>`
        ).join("");
        return `<tr data-peak-idx="${i}">
          <td style="font-weight:600;">${well}</td>
          <td class="text-xs">${cleanText(p.sample_id)}</td>
          <td>${cleanText(p.peak_id)}</td>
          <td>${cleanText(p.rt_min)}</td>
          <td>${cleanText(p.peak_area)}</td>
          <td>${cleanText(p.top_mz)}</td>
          <td class="text-xs">${cleanText(p.top5_mz)}</td>
          <td class="text-xs">${cleanText(p.found_adduct)}</td>
          <td><select class="validate-role-select" style="font-size:0.75rem;padding:2px 6px;background:#1e293b;border:1px solid #475569;color:#e2e8f0;border-radius:4px;">${selectOpts}</select></td>
          <td class="text-xs">${cleanText(p.role_source)}</td>
          <td>${cleanText(p.confidence_score)}</td>
        </tr>`;
      }).join("");

      validatePeaksWrap.innerHTML = `
        <table class="screenings-table" style="font-size:0.78rem;">
          <thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
          <tbody>${rows}</tbody>
        </table>`;
    }

    function collectUpdatedPeaks() {
      const peaks = (
        state.analysisResults &&
        state.analysisResults.results &&
        Array.isArray(state.analysisResults.results.peaks)
          ? state.analysisResults.results.peaks
          : []
      );
      const updated = peaks.map((p) => Object.assign({}, p));
      if (!validatePeaksWrap) return updated;
      validatePeaksWrap.querySelectorAll("tbody tr").forEach((row) => {
        const idx = Number(row.dataset.peakIdx);
        if (idx >= 0 && idx < updated.length) {
          const sel = row.querySelector(".validate-role-select");
          if (sel) updated[idx] = Object.assign({}, updated[idx], { role: cleanText(sel.value) });
        }
      });
      return updated;
    }

    async function updateRoles() {
      showValidateMessage("Recalculating results…", false);
      updateRolesBtn.disabled = true;
      updateRolesBtn.textContent = "Updating…";
      try {
        const updatedPeaks = collectUpdatedPeaks();
        const data = await fetchJson(`/api/screenings/${encodeURIComponent(elnId)}/analysis/update-roles`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ peaks: updatedPeaks }),
        });
        if (state.analysisResults && state.analysisResults.results) {
          state.analysisResults.results.summary_rows = data.summary_rows;
          state.analysisResults.results.peaks = data.peaks;
        }
        renderPeaksTable(data.peaks, state.analysisResults && state.analysisResults.targets);
        renderValidateTable(data.summary_rows || []);
        showValidateMessage("Results updated. Review the summary table, then click Validate.", false);
      } catch (err) {
        showValidateMessage(err.message, true);
      } finally {
        updateRolesBtn.disabled = false;
        updateRolesBtn.textContent = "Update Results";
      }
    }

    function renderValidateTable(rows) {
      const raw = Array.isArray(rows) ? rows : [];
      if (!raw.length) {
        validateTableWrap.innerHTML = `<div class="text-slate-400 text-sm">No rows to validate.</div>`;
        return;
      }
      // Sort by well (A1→D6): use sample_id or well field
      const indexed = raw.map((r, i) => ({ r, i }));
      indexed.sort((a, b) => {
        const [ar, ac] = wellSortKey(a.r.sample_id || a.r.well || "");
        const [br, bc] = wellSortKey(b.r.sample_id || b.r.well || "");
        if (ar !== br) return ar - br;
        return ac - bc;
      });
      const html = `
        <table class="screenings-table">
          <thead>
            <tr><th>Sample</th><th>Well</th><th>Result</th><th>Result Type</th><th>Conversion %</th><th>Yield %</th></tr>
          </thead>
          <tbody>
            ${indexed.map(({ r, i }) => `
              <tr data-idx="${i}">
                <td>${r.sample_id || ""}</td>
                <td>${r.well || ""}</td>
                <td><input class="validate-input val-result" type="text" value="${cleanText(r.result)}"></td>
                <td><input class="validate-input val-type" type="text" value="${cleanText(r.result_type)}"></td>
                <td><input class="validate-input val-conv" type="number" step="0.0001" value="${cleanText(r.conversion_pct)}"></td>
                <td><input class="validate-input val-yield" type="number" step="0.0001" value="${cleanText(r.yield_pct)}"></td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
      validateTableWrap.innerHTML = html;
    }

    function collectValidationOverrides() {
      const out = [];
      validateTableWrap.querySelectorAll("tbody tr").forEach((row) => {
        const idx = Number(row.dataset.idx || -1);
        const base = state.analysisResults && state.analysisResults.results && state.analysisResults.results.summary_rows
          ? state.analysisResults.results.summary_rows[idx] || {}
          : {};
        out.push({
          sample_id: base.sample_id || "",
          well: base.well || "",
          result: cleanText(row.querySelector(".val-result") && row.querySelector(".val-result").value),
          result_type: cleanText(row.querySelector(".val-type") && row.querySelector(".val-type").value),
          conversion_pct: cleanText(row.querySelector(".val-conv") && row.querySelector(".val-conv").value),
          yield_pct: cleanText(row.querySelector(".val-yield") && row.querySelector(".val-yield").value),
        });
      });
      return out;
    }

    async function openValidateModal() {
      showValidateMessage("", false);
      const data = await fetchJson(`/api/screenings/${encodeURIComponent(elnId)}/analysis/results`);
      state.analysisResults = data;
      const results = data.results || {};
      const rows = results.summary_rows || [];
      const peaks = results.peaks || [];
      const targets = data.targets || [];
      const imgs = data.images || [];
      validateImages.innerHTML = imgs.map((img) => `<div><img alt="${img.name || "analysis"}" src="${img.url}"><div class="text-xs text-slate-400 mt-1">${img.name || ""}</div></div>`).join("");
      renderPeaksTable(peaks, targets);
      renderValidateTable(rows);
      validateModal.classList.remove("hidden");
      const card = validateModal.querySelector(".screenings-modal-card");
      if (card) card.scrollTop = 0;
    }

    function closeValidateModal() {
      validateModal.classList.add("hidden");
    }

    async function load() {
      msg.textContent = "";
      const data = await fetchJson(`/api/screenings/${encodeURIComponent(elnId)}`);
      state.item = data.item;
      state.selectedWell = null;
      renderMeta(data.item);
      renderActions(data.item);
      renderDesign();
      if ((data.item.lcms_status || "") === "running") {
        startPolling();
      } else {
        stopPolling();
      }
    }

    analyseBtn.addEventListener("click", async () => {
      openAnalyseModal();
    });

    validateBtn.addEventListener("click", async () => {
      try {
        await openValidateModal();
      } catch (err) {
        msg.textContent = err.message;
      }
    });

    analyseModalClose.addEventListener("click", closeAnalyseModal);
    analyseAddCustomBtn.addEventListener("click", () => addCustomTargetRow());
    analyseAddProductBtn.addEventListener("click", () => addProductRow());
    analysePreviewTargetsBtn.addEventListener("click", () => {
      refreshTargets().catch(() => {});
    });
    analyseType.addEventListener("change", () => {
      analyseYieldBlock.classList.toggle("hidden", cleanText(analyseType.value) !== "yield_with_is");
    });
    analyseYieldBlock.classList.toggle("hidden", cleanText(analyseType.value) !== "yield_with_is");

    analyseRunBtn.addEventListener("click", async () => {
      try {
        analyseRunBtn.disabled = true;
        analyseRunBtn.textContent = "Running...";
        showAnalyseModalMessage("", false);
        await runAnalysis();
        closeAnalyseModal();
        msg.textContent = "LC/MS analysis started.";
        startPolling();
        await load();
      } catch (err) {
        showAnalyseModalMessage(err.message, true);
      } finally {
        analyseRunBtn.disabled = false;
        analyseRunBtn.textContent = "Run Analysis";
      }
    });

    validateModalClose.addEventListener("click", closeValidateModal);
    updateRolesBtn.addEventListener("click", () => {
      updateRoles().catch((err) => showValidateMessage(err.message, true));
    });
    finishValidationBtn.addEventListener("click", async () => {
      try {
        finishValidationBtn.disabled = true;
        finishValidationBtn.textContent = "Finishing...";
        showValidateMessage("", false);
        const overrides = collectValidationOverrides();
        await fetchJson(`/api/screenings/${encodeURIComponent(elnId)}/analysis/validate`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ overrides }),
        });
        closeValidateModal();
        msg.textContent = "Validation finished. Screening marked as Completed.";
        await load();
      } catch (err) {
        showValidateMessage(err.message, true);
      } finally {
        finishValidationBtn.disabled = false;
        finishValidationBtn.textContent = "Finish Validation";
      }
    });

    load().catch((err) => {
      meta.innerHTML = `<span class="text-red-300">${err.message}</span>`;
    });
  }

  if (page === "dashboard") initDashboard();
  if (page === "new") initNewScreening();
  if (page === "detail") initDetail();
})();
