# styles.py
# =============================================================================
# FIXED STYLE DEFINITIONS FOR WEATHER TILE RENDERING
# =============================================================================
#
# This module defines the SINGLE SOURCE OF TRUTH for color scales.
# It MUST be identical in both:
#   - Fargate pre-rendering stack
#   - EC2 dynamic rendering stack
#
# Key principles:
#   1. Use DISCRETE BINS (BoundaryNorm) not continuous normalization
#   2. Store data in NATIVE UNITS (K, m, Pa) - convert only for display
#   3. Same levels/cmap/norm everywhere = no color jumps when zooming
#
# To add a new product:
#   1. Add entry to STYLES dict below
#   2. Update frontend PRODUCT_CONFIG in map.html
#   3. Update parse_product() in app.py if needed
# =============================================================================

from typing import Dict, List, Any, Optional, Callable
import numpy as np

# -----------------------------------------------------------------------------
# STYLE DEFINITIONS
# -----------------------------------------------------------------------------
# Each product maps to a style spec with:
#   - native_units: what the GRIB data provides
#   - display_units: what users see in legends/tooltips
#   - levels: discrete bin boundaries (in NATIVE units)
#   - cmap: matplotlib colormap name
#   - extend: 'both', 'min', 'max', or 'neither' for out-of-range values
#   - label: human-readable name for legends
# -----------------------------------------------------------------------------

STYLES: Dict[str, Dict[str, Any]] = {
    # -------------------------------------------------------------------------
    # 2-METER TEMPERATURE
    # -------------------------------------------------------------------------
    # Native: Kelvin (K)
    # Display: Celsius (°C) and Fahrenheit (°F)
    # Range: -40°C to +50°C (covers most surface temps globally)
    # Step: 2°C bins
    "t2m": {
        "label": "2m Temperature",
        "native_units": "K",
        "display_units": "°C",
        "cmap": "RdYlBu_r",  # Red (hot) -> Yellow -> Blue (cold), reversed
        "extend": "both",
        # Levels in Kelvin: -40°C to +50°C by 2°C
        # -40°C = 233.15K, +50°C = 323.15K
        "levels": [round(273.15 + c, 2) for c in range(-40, 52, 2)],
        # Conversion functions for display
        "to_display": lambda k: k - 273.15,  # K -> °C
        "format_value": lambda k: f"{k - 273.15:.1f}°C",
        "format_value_dual": lambda k: f"{(k - 273.15) * 9/5 + 32:.1f}°F / {k - 273.15:.1f}°C",
    },

    # -------------------------------------------------------------------------
    # 500 hPa GEOPOTENTIAL HEIGHT
    # -------------------------------------------------------------------------
    # Native: meters (m)
    # Display: decameters (dam) - standard meteorological convention
    # Range: 480-600 dam (covers typical 500mb heights)
    # Step: 3 dam bins
    "gh500": {
        "label": "500 hPa Heights",
        "native_units": "m",
        "display_units": "dam",
        "cmap": "Spectral_r",  # Rainbow-ish, good for continuous height fields
        "extend": "both",
        # Levels in meters: 480-600 dam = 4800-6000 m, by 30m (3 dam)
        "levels": list(range(4800, 6030, 30)),
        "to_display": lambda m: m / 10.0,  # m -> dam
        "format_value": lambda m: f"{m / 10.0:.0f} dam",
    },

    # -------------------------------------------------------------------------
    # MEAN SEA LEVEL PRESSURE
    # -------------------------------------------------------------------------
    # Native: Pascals (Pa)
    # Display: hectopascals (hPa) = millibars (mb)
    # Range: 960-1050 hPa (covers most synoptic situations)
    # Step: 2 hPa bins
    "prmsl": {
        "label": "Sea Level Pressure",
        "native_units": "Pa",
        "display_units": "hPa",
        "cmap": "viridis",  # Perceptually uniform, good for pressure
        "extend": "both",
        # Levels in Pa: 960-1050 hPa = 96000-105000 Pa, by 200 Pa (2 hPa)
        "levels": list(range(96000, 105200, 200)),
        "to_display": lambda pa: pa / 100.0,  # Pa -> hPa
        "format_value": lambda pa: f"{pa / 100.0:.1f} hPa",
    },
}


# -----------------------------------------------------------------------------
# HELPER FUNCTIONS
# -----------------------------------------------------------------------------

def get_style(product: str) -> Optional[Dict[str, Any]]:
    """
    Get style definition for a product.
    
    Args:
        product: Product name (e.g., 't2m', 'gh500', 'prmsl')
    
    Returns:
        Style dict or None if product not found
    """
    return STYLES.get(product)


def get_levels(product: str) -> List[float]:
    """Get the discrete bin levels for a product (in native units)."""
    style = STYLES.get(product)
    if style:
        return style["levels"]
    return []


def get_vmin_vmax(product: str) -> tuple:
    """
    Get (vmin, vmax) for a product based on its levels.
    
    Returns the first and last level values, which define the
    colormap extent.
    """
    levels = get_levels(product)
    if levels:
        return (levels[0], levels[-1])
    return (0.0, 1.0)


def get_cmap(product: str) -> str:
    """Get colormap name for a product."""
    style = STYLES.get(product)
    if style:
        return style["cmap"]
    return "turbo"  # fallback


def format_display_value(product: str, native_value: float) -> str:
    """
    Format a native-unit value for display.
    
    Args:
        product: Product name
        native_value: Value in native units (K, m, Pa)
    
    Returns:
        Formatted string with display units
    """
    style = STYLES.get(product)
    if style and "format_value" in style:
        return style["format_value"](native_value)
    return f"{native_value:.2f}"


def convert_to_display(product: str, native_value: float) -> float:
    """
    Convert native units to display units.
    
    Args:
        product: Product name
        native_value: Value in native units
    
    Returns:
        Value in display units
    """
    style = STYLES.get(product)
    if style and "to_display" in style:
        return style["to_display"](native_value)
    return native_value


def get_display_levels(product: str) -> List[float]:
    """
    Get levels converted to display units (for colorbar labels).
    """
    style = STYLES.get(product)
    if not style:
        return []
    
    levels = style["levels"]
    if "to_display" in style:
        return [style["to_display"](v) for v in levels]
    return levels


# -----------------------------------------------------------------------------
# COLORBAR METADATA FOR FRONTEND
# -----------------------------------------------------------------------------

def get_colorbar_config(product: str) -> Dict[str, Any]:
    """
    Get configuration needed to render a colorbar in the frontend.
    
    Returns a JSON-serializable dict with:
        - levels: array of display-unit values for tick marks
        - vmin/vmax: display-unit range
        - units: display unit string
        - label: product label
        - cmap: colormap name (frontend can use this to generate gradient)
        - colors: precomputed hex colors for each level (optional)
    """
    style = STYLES.get(product)
    if not style:
        return {}
    
    display_levels = get_display_levels(product)
    
    return {
        "product": product,
        "label": style["label"],
        "units": style["display_units"],
        "levels": display_levels,
        "vmin": display_levels[0] if display_levels else 0,
        "vmax": display_levels[-1] if display_levels else 1,
        "cmap": style["cmap"],
        "extend": style.get("extend", "both"),
        # Number of discrete bins
        "nbins": len(display_levels) - 1 if display_levels else 0,
    }


# -----------------------------------------------------------------------------
# VARIABLE NAME MAPPING
# -----------------------------------------------------------------------------
# Maps product names to the actual variable names used in GRIB files.
# This handles the mismatch between URL product codes and GRIB shortNames.

PRODUCT_TO_VAR = {
    "t2m": "2t",      # 2-meter temperature uses shortName "2t"
    "gh500": "gh",    # Geopotential height
    "prmsl": "prmsl", # Mean sea level pressure (sometimes "msl")
}

PRODUCT_TO_LEVEL = {
    "t2m": None,      # Surface field (level is height above ground, handled by filter)
    "gh500": 500,     # 500 hPa
    "prmsl": None,    # Surface/mean sea level
}


def parse_product(product: str) -> tuple:
    """
    Parse product string into (var, level).
    
    This is the AUTHORITATIVE mapping used by both tile rendering
    and the /point_value API endpoint.
    
    Args:
        product: Product code from URL (e.g., 't2m', 'gh500', 'prmsl')
    
    Returns:
        (var_name, level) tuple where:
            - var_name: GRIB shortName to query
            - level: pressure level in hPa, or None for surface fields
    
    Examples:
        't2m'   -> ('2t', None)
        'gh500' -> ('gh', 500)
        'prmsl' -> ('prmsl', None)
        'gh850' -> ('gh', 850)  # parsed dynamically
    """
    # Check explicit mappings first
    if product in PRODUCT_TO_VAR:
        return (PRODUCT_TO_VAR[product], PRODUCT_TO_LEVEL.get(product))
    
    # Try to parse pattern like "gh850", "t1000", "u500"
    import re
    match = re.match(r'^([a-z]+)(\d+)$', product)
    if match:
        var = match.group(1)
        level = int(match.group(2))
        return (var, level)
    
    # Unknown product - return as-is with no level
    return (product, None)
