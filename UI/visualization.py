# visualization.py (EC2 Dynamic Rendering)
# =============================================================================
# TILE VISUALIZATION WITH FIXED STYLE-BASED COLORIZATION
# =============================================================================
#
# This module renders weather data tiles using FIXED discrete color bins.
# The key change from the old approach:
#   OLD: compute vmin/vmax per-field using percentiles (causes color jumps)
#   NEW: use fixed levels from styles.py (consistent across all tiles)
#
# =============================================================================

import numpy as np
import io
import xarray as xr
from PIL import Image
import matplotlib.pyplot as plt
from matplotlib import colors, cm
from matplotlib.colors import BoundaryNorm, ListedColormap
from functools import lru_cache

# Set up Matplotlib backend
plt.switch_backend("Agg")

from .config import TILE_SIZE, PCTL_LOW, PCTL_HIGH
from .data_access import get_model_dataset, get_model_levels
from . import styles


# -----------------------------------------------------------------------------
# STYLE-BASED COLORIZATION (NEW - PREFERRED)
# -----------------------------------------------------------------------------

def colorize_styled(data2d: np.ndarray, product: str) -> Image.Image:
    """
    Apply fixed style-based colorization using discrete bins.
    
    This is the NEW preferred method that ensures consistent colors
    across all tiles, models, and forecast hours.
    
    Args:
        data2d: 2D numpy array of values in NATIVE units
        product: Product name (e.g., 't2m', 'gh500', 'prmsl')
    
    Returns:
        RGBA PIL Image
    """
    style = styles.get_style(product)
    
    if not style:
        # Fallback to old percentile-based method for unknown products
        print(f"[WARN] No style defined for product '{product}', using fallback")
        return colorize(data2d)
    
    levels = style["levels"]
    cmap_name = style["cmap"]
    extend = style.get("extend", "both")
    
    # Get colormap
    cmap = cm.get_cmap(cmap_name)
    
    # Create BoundaryNorm for discrete bins
    # ncolors = len(levels) - 1 for the bins between levels
    norm = BoundaryNorm(levels, cmap.N, clip=False)
    
    # Handle out-of-range values based on 'extend' setting
    # BoundaryNorm with clip=False will use the first/last colors for out-of-range
    
    # Apply colormap
    # The norm maps values to [0, 1], cmap converts to RGBA
    rgba = cmap(norm(data2d), bytes=True)
    
    # Handle NaN/missing data - make transparent
    mask = ~np.isfinite(data2d)
    if mask.any():
        rgba[mask] = [0, 0, 0, 0]  # Transparent
    
    return Image.fromarray(rgba, mode="RGBA")


# -----------------------------------------------------------------------------
# LEGACY COLORIZATION (kept for fallback)
# -----------------------------------------------------------------------------

def get_global_range(
    model: str,
    model_date: str,
    model_time: str,
    fhr: int,
    var: str,
    level: int | None,
    da: xr.DataArray = None
):
    """
    LEGACY: Compute global vmin/vmax using percentiles.
    
    This causes color inconsistency between tiles/times. Use colorize_styled()
    instead for products with defined styles.
    
    Kept for:
      - Products without a defined style
      - Contour level computation (where we need actual data range)
    """
    if da is None:
        ds, _, _ = get_model_dataset(model, model_date, model_time, fhr, var, level)
        da = ds[var] if var in ds.data_vars else next(iter(ds.data_vars.values()))

    arr = np.asarray(da.values, dtype=np.float32)

    vmin = float(np.nanpercentile(arr, PCTL_LOW))
    vmax = float(np.nanpercentile(arr, PCTL_HIGH))

    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmin >= vmax:
        vmin = float(np.nanmin(arr))
        vmax = float(np.nanmax(arr))
    
    return vmin, vmax


def colorize(data2d, *, vmin=None, vmax=None, cmap_name="turbo"):
    """
    LEGACY: Apply continuous color map with normalization.
    
    This is the OLD method - use colorize_styled() for new code.
    Kept for backward compatibility and unknown products.
    """
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


# -----------------------------------------------------------------------------
# CONTOUR DRAWING
# -----------------------------------------------------------------------------

def draw_contours(
    gvmin_m: float,
    gvmax_m: float,
    vals_dam: np.ndarray,
    interval: float,
    label: int,
    lw: float
):
    """
    Generates a PNG image with contour lines for geopotential height (GH).
    
    Args:
        gvmin_m: Global minimum in meters (for computing contour levels)
        gvmax_m: Global maximum in meters
        vals_dam: Pre-sampled data in decameters
        interval: Contour interval in decameters
        label: Whether to add contour labels (0 or 1)
        lw: Line width
    
    Returns:
        BytesIO buffer containing PNG image
    """
    # Convert global range to decameters
    gvmin_dam = gvmin_m / 10.0
    gvmax_dam = gvmax_m / 10.0
    interval_dam = float(interval)
    
    # Compute contour levels centered on the data range
    mid = 0.5 * (gvmin_dam + gvmax_dam)
    center_dam = round(mid / interval_dam) * interval_dam
    
    k0 = int(np.floor((gvmin_dam - center_dam) / interval_dam))
    k1 = int(np.ceil((gvmax_dam - center_dam) / interval_dam))
    levels_global = center_dam + np.arange(k0, k1 + 1) * interval_dam

    # Draw contours
    N = TILE_SIZE
    X, Y = np.meshgrid(np.arange(N), np.arange(N))

    fig = plt.figure(figsize=(N/100, N/100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1], frame_on=False)
    ax.set_xlim(0, N)
    ax.set_ylim(N, 0)  # Y-down axis for tile coordinates
    ax.set_xticks([])
    ax.set_yticks([])

    CS = ax.contour(
        X, Y, vals_dam,
        levels=levels_global,
        colors="black",
        linewidths=lw,
        antialiased=True
    )

    if label:
        ax.clabel(CS, inline=True, fmt="%d", fontsize=8)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", transparent=True)
    plt.close(fig)
    buf.seek(0)
    return buf


# -----------------------------------------------------------------------------
# COLORBAR GENERATION (for API endpoint)
# -----------------------------------------------------------------------------

def generate_colorbar_image(product: str, width: int = 300, height: int = 20) -> bytes:
    """
    Generate a horizontal colorbar image for a product.
    
    This can be served as an API endpoint for the frontend to display
    a legend that matches the tile colors exactly.
    
    Args:
        product: Product name
        width: Image width in pixels
        height: Image height in pixels
    
    Returns:
        PNG image as bytes
    """
    style = styles.get_style(product)
    if not style:
        # Return empty/placeholder
        img = Image.new("RGBA", (width, height), (200, 200, 200, 255))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    
    levels = style["levels"]
    cmap = cm.get_cmap(style["cmap"])
    norm = BoundaryNorm(levels, cmap.N)
    
    # Create gradient
    gradient = np.linspace(levels[0], levels[-1], width)
    gradient = np.vstack([gradient] * height)
    
    # Apply colormap
    rgba = cmap(norm(gradient), bytes=True)
    img = Image.fromarray(rgba, mode="RGBA")
    
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()