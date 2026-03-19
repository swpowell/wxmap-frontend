# UI/model_navgem_graphcast.py
# =============================================================================
# NavGem-GraphCast Model Handler (Zarr-only)
# =============================================================================
#
# This handler provides access to NavGem-GraphCast forecast data stored as
# a Zarr subset in S3. The Zarr store contains only the three variables needed
# for tile rendering: prmsl, t2m, and z500 (pre-converted to height).
#
# Data path:
#   s3://nrl-graphcast/navgem-graphcast/{YYYYMMDD}/{HH}/zarr/
#
# Ready marker:
#   s3://nrl-graphcast/navgem-graphcast/{YYYYMMDD}/{HH}/zarr/_READY
#
# Zarr store structure:
#   - Dimensions: time, lat, lon (standardized names)
#   - Variables: prmsl, t2m, z500 (float32, zstd compressed)
#   - Chunking: (1, 256, 256) for per-forecast-hour rendering
#
# Key differences from other handlers:
#   - Single Zarr store per forecast run (not per-fhr files)
#   - Time dimension in store (fhr maps to time index)
#   - Only supports: prmsl, t2m, gh (at 500 hPa only)
#   - z500 is pre-converted to geopotential height (m)
#
# If the Zarr store doesn't exist or _READY marker is missing, this handler
# fails loudly. No fallback to kerchunk or direct NetCDF access.
# =============================================================================

from __future__ import annotations

import os
from functools import lru_cache
from typing import Tuple, Optional

import s3fs
import xarray as xr

# EC2 context: import from package
from .config import S3_OPTS_AIGFS  # Private bucket credentials (reusing AIGFS creds)

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------

S3_BUCKET = "nrl-graphcast"
S3_PREFIX = "navgem-graphcast"

# Forecast time step (hours between each time index)
FORECAST_STEP_HOURS = 6

# Maximum forecast hour (0 to 240h = 41 timesteps)
MAX_FHR = 240

# Available pressure levels (only 500 hPa is in the Zarr subset)
AVAILABLE_LEVELS = [500]


# -----------------------------------------------------------------------------
# VARIABLE MAPPING
# -----------------------------------------------------------------------------

# Maps input aliases to (zarr_var_name, canonical_output_name)
# Input can be various aliases; output is always canonical
VARIABLE_MAP = {
    # Surface / single-level - PRMSL
    "prmsl": ("prmsl", "prmsl"),
    "msl": ("prmsl", "prmsl"),
    # Surface / single-level - T2M
    "t2m": ("t2m", "t2m"),
    "2t": ("t2m", "t2m"),
    # Geopotential height at 500 hPa
    "gh": ("z500", "gh"),
}


# -----------------------------------------------------------------------------
# PATH HELPERS
# -----------------------------------------------------------------------------

def get_zarr_s3_url(model_date: str, model_time: str) -> str:
    """Get the S3 URL for the Zarr store."""
    return f"s3://{S3_BUCKET}/{S3_PREFIX}/{model_date}/{model_time}/zarr"


def get_zarr_s3_key(model_date: str, model_time: str) -> str:
    """Get the S3 key (bucket/prefix format) for the Zarr store."""
    return f"{S3_BUCKET}/{S3_PREFIX}/{model_date}/{model_time}/zarr"


def get_ready_marker_key(model_date: str, model_time: str) -> str:
    """Get the S3 key for the ready marker."""
    return f"{S3_BUCKET}/{S3_PREFIX}/{model_date}/{model_time}/zarr/_READY"


def fhr_to_time_index(fhr: int) -> int:
    """
    Convert forecast hour to time index.
    
    The store contains forecasts from 0h to 240h at 6-hourly intervals,
    giving 41 timesteps (indices 0-40).
    
    Args:
        fhr: Forecast hour (0, 6, 12, ..., 240)
    
    Returns:
        Time index (0-40)
    
    Raises:
        ValueError: If fhr is out of range or not a multiple of 6
    """
    if fhr < 0 or fhr > MAX_FHR:
        raise ValueError(f"Forecast hour {fhr} out of range (0-{MAX_FHR})")
    if fhr % FORECAST_STEP_HOURS != 0:
        raise ValueError(f"Forecast hour {fhr} must be multiple of {FORECAST_STEP_HOURS}")
    return fhr // FORECAST_STEP_HOURS


# -----------------------------------------------------------------------------
# S3 FILESYSTEM
# -----------------------------------------------------------------------------

def _get_s3_opts() -> dict:
    """Get S3 options from config."""
    opts = dict(S3_OPTS_AIGFS)
    # Disable instance caching if debugging
    if os.getenv("S3FS_NO_CACHE", "0") == "1":
        opts["skip_instance_cache"] = True
    return opts


def _check_zarr_ready(model_date: str, model_time: str) -> bool:
    """Check if Zarr store exists with ready marker."""
    fs = s3fs.S3FileSystem(**_get_s3_opts())
    return fs.exists(get_ready_marker_key(model_date, model_time))


# -----------------------------------------------------------------------------
# DATASET OPENING
# -----------------------------------------------------------------------------

@lru_cache(maxsize=2)
def _open_zarr(model_date: str, model_time: str) -> xr.Dataset:
    """
    Open the Zarr store for a forecast run.
    
    The Zarr store has standardized dimensions (time, lat, lon) and
    variables (prmsl, t2m, z500).
    
    Args:
        model_date: YYYYMMDD format
        model_time: HH format (00, 06, 12, 18)
    
    Returns:
        xarray Dataset
    
    Raises:
        FileNotFoundError: If Zarr store or _READY marker doesn't exist
    """
    # Check ready marker exists (fail loudly if not)
    if not _check_zarr_ready(model_date, model_time):
        raise FileNotFoundError(
            f"Zarr _READY marker not found: s3://{get_ready_marker_key(model_date, model_time)}"
        )
    
    zarr_url = get_zarr_s3_url(model_date, model_time)
    print(f"[navgem_graphcast] Opening Zarr: {zarr_url}")
    
    # Use explicit S3Map for deterministic behavior
    fs = s3fs.S3FileSystem(**_get_s3_opts())
    mapper = s3fs.S3Map(root=get_zarr_s3_key(model_date, model_time), s3=fs, check=False)
    
    ds = xr.open_zarr(mapper, consolidated=True)
    
    print(f"[navgem_graphcast] Opened. Variables: {list(ds.data_vars)}, Dims: {dict(ds.dims)}")
    
    # Normalize coordinates (lon: -180 to 180, lat: increasing)
    ds = _normalize_coords(ds)
    
    return ds


def _normalize_coords(ds: xr.Dataset) -> xr.Dataset:
    """
    Normalize coordinates to match our standard conventions.
    
    The Zarr store has standardized dim names (time, lat, lon), so we just
    need to handle lon range and lat ordering.
    """
    # Convert 0-360 longitude to -180-180 if needed
    lon = ds["lon"]
    if float(lon.min()) >= 0 and float(lon.max()) > 180:
        ds = ds.assign_coords({"lon": xr.where(lon > 180, lon - 360, lon)})
        ds = ds.sortby("lon")
    
    # Ensure latitude is increasing (south to north)
    lat = ds["lat"]
    if float(lat[0]) > float(lat[-1]):
        ds = ds.sortby("lat")
    
    return ds


# -----------------------------------------------------------------------------
# VARIABLE EXTRACTION
# -----------------------------------------------------------------------------

def _extract_variable(
    ds: xr.Dataset,
    var: str,
    level: Optional[int],
    time_idx: int,
) -> Tuple[xr.DataArray, str]:
    """
    Extract a variable slice from the Zarr dataset.
    
    Args:
        ds: Zarr dataset
        var: Variable name (t2m, prmsl, gh, msl, 2t, etc.)
        level: Pressure level in hPa (only 500 is supported for gh)
        time_idx: Time index (0-40)
    
    Returns:
        (2D DataArray, canonical_output_name) tuple
    
    Raises:
        ValueError: If variable not supported or level not available
    """
    var_lower = var.lower()
    
    # Check variable is supported
    if var_lower not in VARIABLE_MAP:
        raise ValueError(
            f"Variable '{var}' not supported. "
            f"Available: {list(VARIABLE_MAP.keys())}. "
            f"This handler only supports variables in the Zarr subset."
        )
    
    zarr_var, canonical_name = VARIABLE_MAP[var_lower]
    
    # Special handling for gh - only 500 hPa is available
    if var_lower == "gh":
        if level is not None and level != 500:
            raise ValueError(
                f"Only 500 hPa geopotential height is available. "
                f"Requested level: {level} hPa"
            )
    
    # Check variable exists in dataset
    if zarr_var not in ds.data_vars:
        raise ValueError(
            f"Variable '{zarr_var}' not found in Zarr store. "
            f"Available: {list(ds.data_vars)}"
        )
    
    da = ds[zarr_var]
    
    # Select time index (dims are standardized to time/lat/lon)
    da = da.isel(time=time_idx)
    
    return da, canonical_name


# -----------------------------------------------------------------------------
# PUBLIC API (matches other model handlers)
# -----------------------------------------------------------------------------

def get_dataset(
    model_date: str,
    model_time: str,
    fhr: int,
    var: str,
    level: Optional[int],
) -> Tuple[xr.Dataset, str, str]:
    """
    Get dataset with extracted variable for a specific forecast hour.
    
    This is the main entry point, matching the interface of other model handlers.
    
    Args:
        model_date: YYYYMMDD format
        model_time: HH format (00, 06, 12, 18)
        fhr: Forecast hour (0, 6, 12, ..., 240)
        var: Variable name (t2m, prmsl, gh, msl, 2t)
        level: Pressure level in hPa (only 500 supported for gh)
    
    Returns:
        (dataset, lon_name, lat_name) tuple where dataset contains
        the extracted 2D variable slice with canonical variable name
    
    Raises:
        FileNotFoundError: If Zarr store doesn't exist
        ValueError: If variable or level not supported
    """
    # Convert fhr to time index
    time_idx = fhr_to_time_index(fhr)
    
    # Open Zarr store
    ds = _open_zarr(model_date, model_time)
    
    # Extract variable (returns canonical name)
    da, canonical_name = _extract_variable(ds, var, level, time_idx)
    
    # Wrap in dataset with canonical variable name
    result_ds = xr.Dataset(
        {canonical_name: da},
        coords={"lon": ds["lon"], "lat": ds["lat"]}
    )
    
    # Return with standardized dim names
    return result_ds, "lon", "lat"


def get_levels(model_date: str, model_time: str, var: str) -> dict:
    """
    Get available pressure levels for a variable.
    
    Args:
        model_date: YYYYMMDD format
        model_time: HH format
        var: Variable name
    
    Returns:
        Dict with keys: var, level_dim, levels
    """
    var_lower = var.lower()
    
    # Only gh has levels, and only 500 is available
    if var_lower == "gh":
        return {
            "var": var,
            "level_dim": "level",
            "levels": AVAILABLE_LEVELS,
        }
    
    # Surface variables have no levels
    return {"var": var, "level_dim": None, "levels": []}


# -----------------------------------------------------------------------------
# CACHE MANAGEMENT
# -----------------------------------------------------------------------------

def clear_caches():
    """Clear all LRU caches (useful for testing or memory management)."""
    _open_zarr.cache_clear()


def get_cache_info():
    """Get cache statistics."""
    return {
        "zarr": _open_zarr.cache_info(),
    }