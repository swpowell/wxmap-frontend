# visualization.py
import numpy as np
import io
import xarray as xr # Import xarray
from PIL import Image
import matplotlib.pyplot as plt
from matplotlib import colors, cm
from functools import lru_cache

# Set up Matplotlib backend
plt.switch_backend("Agg")

from .config import TILE_SIZE, PCTL_LOW, PCTL_HIGH
from .data_access import get_dataset

@lru_cache(maxsize=512)
def get_global_range(
    model_date: str, 
    model_time: str, 
    fhr: int, 
    var: str, 
    level: int | None,
    da: xr.DataArray = None # <-- FIX: Accept an optional DataArray
):
    """Robust global vmin/vmax (2–98th pct) cached per request."""
    
    # If da is NOT provided, fall back to the old (slower) method of fetching it.
    if da is None:
        # print("[PERF] get_global_range: 'da' not provided, re-fetching data.")
        ds, _, _ = get_dataset(model_date, model_time, fhr, var, level)
        da = ds[var] if var in ds.data_vars else next(iter(ds.data_vars.values()))
    # else:
        # print("[PERF] get_global_range: 'da' was provided, skipping data fetch.")

    # Ensure it's 2D for percentile calculation (flatten if needed)
    arr = np.asarray(da.values, dtype=np.float32)

    vmin = float(np.nanpercentile(arr, PCTL_LOW))
    vmax = float(np.nanpercentile(arr, PCTL_HIGH))

    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmin >= vmax:
        vmin = float(np.nanmin(arr)); vmax = float(np.nanmax(arr))
    return vmin, vmax

def colorize(data2d, *, vmin=None, vmax=None, cmap_name="turbo"):
    """Applies color map and normalization to 2D data."""
    if vmin is None or vmax is None:
        finite = np.asarray(data2d, dtype=np.float32)
        vmin = np.nanmin(finite) if vmin is None else vmin
        vmax = np.nanmax(finite) if vmax is None else vmax
        if not np.isfinite(vmin) or not np.isfinite(vmax) or vmin >= vmax:
            vmin, vmax = 0.0, 1.0

    norm = colors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    cmap = cm.get_cmap(cmap_name)
    rgba = cmap(norm(data2d), bytes=True)
    return Image.fromarray(rgba, mode="RGBA")

def draw_contours(model_date: str, model_time: str, fhr_int: int, gh_level_int: int | None, vals_dam: np.ndarray, interval: float, label: int, lw: float):
    """
    Generates a PNG image with contour lines for geopotential height (GH).
    Requires pre-sampled data (vals_dam).
    """
    if gh_level_int is None:
        raise ValueError("Geopotential height level must be provided for contours.")

    # 1. Determine global contour levels
    var_name = "gh" # Assume gh is the requested variable name
    for v in ("gh", "z"): # Try 'gh' first, then 'z'
        try:
            # This call does NOT pass 'da', so it will use the fallback
            # data fetch, which is correct for contours.
            gvmin_m, gvmax_m = get_global_range(model_date, model_time, fhr_int, var=v, level=gh_level_int)
            var_name = v
            break # Success
        except Exception:
            pass # Try next var 

    gvmin_dam = gvmin_m / 10.0
    gvmax_dam = gvmax_m / 10.0

    interval_dam = float(interval)
    
    # Calculate center_dam (e.g., nearest multiple of interval)
    mid = 0.5 * (gvmin_dam + gvmax_dam)
    center_dam = round(mid / interval_dam) * interval_dam
    
    # Calculate levels that span the full global range
    k0 = int(np.floor((gvmin_dam - center_dam) / interval_dam))
    k1 = int(np.ceil ((gvmax_dam - center_dam) / interval_dam))
    levels_global = center_dam + np.arange(k0, k1 + 1) * interval_dam


    # 2. Draw contours on tile
    N = TILE_SIZE
    X, Y = np.meshgrid(np.arange(N), np.arange(N))

    fig = plt.figure(figsize=(N/100, N/100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1], frame_on=False)
    ax.set_xlim(0, N)
    ax.set_ylim(N, 0) # Y-down axis for tile coordinates
    ax.set_xticks([]); ax.set_yticks([])

    CS = ax.contour(X, Y, vals_dam, levels=levels_global, colors="black", linewidths=lw, antialiased=True)

    if label:
        ax.clabel(CS, inline=True, fmt="%d", fontsize=8)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", transparent=True, dpi=100) # dpi=100 is fine
    plt.close(fig)
    buf.seek(0)
    return buf