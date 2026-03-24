from typing import Dict, Optional, Tuple
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.colors import to_rgb

WELL_ROWS_96 = list("ABCDEFGH")
WELL_COLS_96 = list(range(1, 13))
WELL_ROWS_24 = list("ABCD")
WELL_COLS_24 = list(range(1, 7))
WELL_ROWS_48 = list("ABCDEF")
WELL_COLS_48 = list(range(1, 9))

def grid_shape(nwells: int):
    if nwells == 24:
        return (WELL_ROWS_24, WELL_COLS_24)
    if nwells == 48:
        return (WELL_ROWS_48, WELL_COLS_48)
    return (WELL_ROWS_96, WELL_COLS_96)

def well_to_idx(well: str, nwells: int):
    if not well: return None
    well = well.strip().upper()
    rows, cols = grid_shape(nwells)
    try:
        r = rows.index(well[0])
        c = int(well[1:]) - 1
        if 0 <= c < len(cols):
            return (r, c)
    except Exception:
        pass
    return None

# ----- Color maps -----
def _confidence_cmap():
    # 0 -> red, mid -> yellow, 100 -> green
    return LinearSegmentedColormap.from_list(
        "conf_cmap",
        ["#8b0000", "#ffea00", "#228b22"]
    )

def _make_cmap(kind: Optional[str]):
    if kind == "conversion":
        colors = ["#bde3ff", "#1482fa", "#0b41cd", "#022366"]
        return LinearSegmentedColormap.from_list("conv_cmap", colors)
    if kind == "yield":
        colors = ["#8b0000", "#ee553b", "#ffb600", "#b5bc17", "#228b22"]
        return LinearSegmentedColormap.from_list("yield_cmap", colors)
    if kind == "confidence":
        colors = ["#f0f0f0", "#bfe6bf", "#2f7d2f"]
        return LinearSegmentedColormap.from_list("confidence_cmap", colors)
    return None

def render_confidence_map(
    well_to_conf: Dict[str, float],
    nwells: int,
    title: str,
    outfile: str,
    axis_fontsize: int = 9,
    label_color: str = "white",
    show_labels: bool = True,
) -> str:
    rows, cols = grid_shape(nwells)
    nrows, ncols = len(rows), len(cols)

    # Fill missing wells with 0 (no blanks)
    grid = np.zeros((nrows, ncols), dtype=float)
    for r, rowlab in enumerate(rows):
        for c, colnum in enumerate(cols):
            w = f"{rowlab}{colnum}"
            v = well_to_conf.get(w, 0.0)
            try:
                v = float(v)
            except Exception:
                v = 0.0
            if np.isnan(v):
                v = 0.0
            grid[r, c] = v

    fig, ax = plt.subplots(figsize=(ncols*0.55, nrows*0.55))
    plt.subplots_adjust(left=0.06, right=0.98, top=0.90, bottom=0.08)

    cmap = _confidence_cmap()
    im = ax.imshow(grid, vmin=0.0, vmax=100.0, cmap=cmap, interpolation="nearest")

    ax.set_xticks(range(ncols)); ax.set_xticklabels([str(x) for x in cols], fontsize=axis_fontsize, color=label_color)
    ax.set_yticks(range(nrows)); ax.set_yticklabels(rows, fontsize=axis_fontsize, color=label_color)
    ax.tick_params(length=0)

    if show_labels:
        for r in range(nrows):
            for c in range(ncols):
                v = grid[r, c]
                ax.text(c, r, f"{v:.0f}", ha="center", va="center", fontsize=axis_fontsize, color=label_color)

    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.ax.tick_params(labelsize=max(8, axis_fontsize-1))
    cbar.set_label("Confidence (%)", fontsize=max(8, axis_fontsize-1))

    ax.set_title(title, pad=14)
    plt.savefig(outfile, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return outfile

def render_presence_map(
    well_to_bool: Dict[str, bool],
    nwells: int,
    title: str,
    outfile: str,
    *,
    color_present: str = "#96D294",
    color_absent: str  = "#CB4C4E",
    show_symbols: bool = True,
    symbol_color: str = "white",
    axis_fontsize: int = 9,
) -> str:
    rows, cols = grid_shape(nwells)
    nrows, ncols = len(rows), len(cols)

    img = np.ones((nrows, ncols, 3), dtype=float)
    g = np.array(to_rgb(color_present))
    r = np.array(to_rgb(color_absent))

    for well, ok in (well_to_bool or {}).items():
        idx = well_to_idx(well, nwells)
        if idx is None:
            continue
        rr, cc = idx
        img[rr, cc] = g if bool(ok) else r

    plt.figure(figsize=(ncols * 0.6, nrows * 0.6))
    plt.imshow(img, interpolation="nearest")
    plt.title(title, pad=10)
    plt.xticks(range(ncols), [str(c) for c in cols], fontsize=axis_fontsize)
    plt.yticks(range(nrows), rows, fontsize=axis_fontsize)

    if show_symbols:
        for rr in range(nrows):
            for cc in range(ncols):
                well = f"{rows[rr]}{cols[cc]}"
                ok = bool(well_to_bool.get(well, False))
                sym = "✓" if ok else "✗"
                plt.text(
                    cc, rr, sym,
                    ha="center", va="center",
                    fontsize=12, color=symbol_color, weight="bold"
                )

    plt.tight_layout()
    plt.savefig(outfile, dpi=150, bbox_inches="tight")
    plt.close()
    return outfile

def render_heatmap(
    well_to_val, nwells, title, vmin, vmax, cbarlabel, outfile,
    text_fmt: str | None = None,
    cmap: str | None = None,
    show_labels: bool = True,
    label_color: str = "white",
    axis_fontsize: int = 9,
    axis_color: str = "black",
):
    rows, cols = grid_shape(nwells)
    nrows, ncols = len(rows), len(cols)

    # Start at 0 everywhere, then overwrite with provided values
    grid = np.zeros((nrows, ncols), dtype=float)
    for r in range(nrows):
        for c in range(ncols):
            well = f"{rows[r]}{cols[c]}"
            v = (well_to_val or {}).get(well, 0.0)
            try:
                v = float(v)
            except Exception:
                v = 0.0
            if np.isnan(v):
                v = 0.0
            grid[r, c] = v

    cm = None
    if isinstance(cmap, str):
        cm = _make_cmap(cmap) or plt.get_cmap(cmap)

    plt.figure(figsize=(ncols * 0.6, nrows * 0.6))
    im = plt.imshow(grid, vmin=vmin, vmax=vmax, cmap=cm)

    plt.title(title)
    plt.xticks(range(ncols), [str(c) for c in cols], fontsize=axis_fontsize, color=axis_color)
    plt.yticks(range(nrows), rows, fontsize=axis_fontsize, color=axis_color)

    if show_labels and text_fmt:
        for r in range(nrows):
            for c in range(ncols):
                val = grid[r, c]
                plt.text(c, r, text_fmt.format(val), ha="center", va="center",
                         fontsize=max(axis_fontsize-1, 6), color=label_color, weight="bold")

    cbar = plt.colorbar(im)
    cbar.set_label(cbarlabel, fontsize=8)
    cbar.ax.tick_params(labelsize=8)

    plt.tight_layout()
    plt.savefig(outfile, dpi=150, bbox_inches="tight")
    plt.close()
    return outfile

def render_pies(
    well_to_fracs: Dict[str, Dict[str, float]],
    nwells: int,
    title: str,
    outfile: str,
    label_color: str = "black",
    axis_fontsize: int = 10,
    title_fontsize: int = 18,
    legend_fontsize: int = 12,
    title_y: float = 0.985,
    header_gap: float = 0.010,
) -> str:
    import math

    rows, cols = grid_shape(nwells)
    nrows, ncols = len(rows), len(cols)

    palette = {"SM": "#d3d9e5", "Prod": "#4f6d92", "SideProd": "#ffb74d", "NA": "#d8d8d8"}

    roles_present = set()
    for fr in (well_to_fracs or {}).values():
        for k in ("SM", "Prod", "SideProd"):
            v = fr.get(k, 0.0)
            try:
                v = float(v)
            except Exception:
                v = 0.0
            if v and not (isinstance(v, float) and math.isnan(v)) and v > 0:
                roles_present.add(k)
    if not roles_present:
        roles_present = {"NA"}

    fig, axes = plt.subplots(
        nrows, ncols, figsize=(ncols * 0.6, nrows * 0.6), constrained_layout=False
    )
    if nrows == 1 and ncols == 1:
        axes = np.array([[axes]])

    plt.subplots_adjust(left=0.08, right=0.98, top=0.78, bottom=0.24, wspace=0.02, hspace=0.02)
    fig.suptitle(title, y=title_y, fontsize=title_fontsize)

    for r in range(nrows):
        for c in range(ncols):
            ax = axes[r, c]
            ax.set_aspect("equal")
            well = f"{rows[r]}{cols[c]}"
            fr = well_to_fracs.get(well) or {}

            vals = {}
            for k in ("SM", "Prod", "SideProd"):
                v = fr.get(k, 0.0)
                try:
                    v = float(v)
                    if math.isnan(v):
                        v = 0.0
                except Exception:
                    v = 0.0
                vals[k] = v

            keys = [k for k in ("SM", "Prod", "SideProd") if vals.get(k, 0.0) > 0]
            if not keys:
                sizes = [1.0]
                colors = [palette["NA"]]
            else:
                sizes = [vals.get(k, 0.0) for k in keys]
                if sum(sizes) <= 0:
                    sizes = [1.0]
                    colors = [palette["NA"]]
                else:
                    colors = [palette[k] for k in keys]

            ax.pie(sizes, labels=None, normalize=True, colors=colors)
            ax.set_xticks([]); ax.set_yticks([])

    # left letters
    for r in range(nrows):
        pos = axes[r, 0].get_position()
        fig.text(
            x=pos.x0 - 0.02, y=pos.y0 + pos.height / 2,
            s=rows[r], ha="right", va="center",
            fontsize=axis_fontsize, color=label_color,
        )
    # top numbers
    for c in range(ncols):
        pos = axes[0, c].get_position()
        fig.text(
            x=pos.x0 + pos.width / 2, y=pos.y1 + header_gap,
            s=str(cols[c]), ha="center", va="bottom",
            fontsize=axis_fontsize, color=label_color,
        )

    used = [k for k in ("SM", "Prod", "SideProd") if k in roles_present] or ["NA"]
    handles = [mpatches.Patch(color=palette[k], label=k) for k in used]
    fig.legend(
        handles=handles, loc="lower center", ncol=len(used),
        bbox_to_anchor=(0.5, 0.08), frameon=False, fontsize=legend_fontsize,
    )

    plt.savefig(outfile, dpi=200)
    plt.close(fig)
    return outfile

