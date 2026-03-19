# UI/model_aigfs.py
# Contains all data access logic specific to NOAA AIGFS
from functools import lru_cache
import numpy as np
import xarray as xr
import pandas as pd
import fsspec
import os
import time

# Import model-specific paths from config
from .config import ZARR_ROOT_AIGFS, S3_ROOT_AIGFS, FILECACHE_OPTS, S3_OPTS_AIGFS

# --- AIGFS Level Filter ---
# Adjust this list based on what levels AIGFS actually provides
AIGFS_LEVEL_FILTER = [
    1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50
]

# ---- S3/Cache Resolver ---
def _resolve_local_grib(url: str) -> str:
    """Resolves an S3 URL to a local cached file path."""
    wrapped = f"filecache::{url}" if url.startswith("s3://") else url
    storage_options = {"filecache": FILECACHE_OPTS, "s3": S3_OPTS_AIGFS}
    local_path = fsspec.open_local(wrapped, **storage_options)
    try: 
        os.path.getsize(local_path)
    except Exception: 
        pass
    return local_path

# ---- AIGFS Path Generators ---
def get_zarr_path(model_date: str, model_time: str) -> str:
    """Returns the local Zarr path for AIGFS (if converted)."""
    return os.path.join(ZARR_ROOT_AIGFS, model_date, f"{model_time}.zarr")

def get_s3_grib_url(model_date: str, model_time: str, fhr: int, product: str = "pres") -> str:
    """
    Returns the S3 URL for AIGFS GRIB2 files.

    Path format:
      s3://noaa-nps-aigfs/aigfs/aigfs.YYYYMMDD/HH/model/atmos/grib2/aigfs.tHHz.{pres|sfc}.fHHH.grib2
    """
    fhr_str = str(int(fhr)).zfill(3)
    product = (product or "pres").lower()
    if product not in {"pres", "sfc"}:
        raise ValueError(f"Unknown AIGFS product '{product}'")

    return (
        f"{S3_ROOT_AIGFS}/aigfs/aigfs.{model_date}/{model_time}/"
        f"model/atmos/grib2/aigfs.t{model_time}z.{product}.f{fhr_str}.grib2"
    )


# ---- Helper Functions ---
def build_grib_filter(var: str, level: int | None):
    """
    Builds a filter dictionary for cfgrib to select specific variables.
    """
    v = var.lower()

    # Pressure-level fields
    if v in {"t", "u", "v", "z", "w", "gh"}:
        flt = {"shortName": v, "typeOfLevel": "isobaricInhPa"}
        if level is not None:
            flt["level"] = int(level)
        return flt

    # 10m winds
    if v in {"u10", "v10"}:
        return {"shortName": v, "typeOfLevel": "heightAboveGround", "level": 10}

    # 2m temperature (AIGFS sfc)
    if v in {"2t"}:
        return {"shortName": "2t", "typeOfLevel": "heightAboveGround", "level": 2}

    # Mean sea-level pressure (AIGFS sfc)
    if v in {"prmsl"}:
        return {"shortName": "prmsl", "typeOfLevel": "meanSea"}

    # Fallback (least safe)
    return {"shortName": v}


def _normalize_coords(ds):
    """
    Normalizes coordinate names and ensures proper longitude/latitude ordering.
    Converts 0-360 longitude to -180 to 180 if needed.
    """
    lon_name = "longitude" if "longitude" in ds.coords else "lon"
    lat_name = "latitude"  if "latitude"  in ds.coords else "lat"
    if lon_name not in ds.coords or lat_name not in ds.coords:
        return ds, "longitude", "latitude"  # Fallback
        
    lon = ds[lon_name]
    if lon.min() >= 0 and lon.max() <= 360:
        ds = ds.assign_coords({lon_name: xr.where(lon >= 180, lon - 360, lon)})
    ds = ds.sortby(lon_name)
    if (ds[lat_name].diff(lat_name) < 0).any():
        ds = ds.sortby(lat_name)
    return ds, lon_name, lat_name

# ---- Data Openers (Cached) ---
@lru_cache(maxsize=4)
def open_grib_dataset(resolved_grib_path: str, **flt):
    """Opens a GRIB file with cfgrib and applies coordinate normalization."""
    ds = xr.open_dataset(
        resolved_grib_path,
        engine="cfgrib",
        backend_kwargs={"filter_by_keys": flt or None, "indexpath": ""},
    )
    return _normalize_coords(ds)

@lru_cache(maxsize=4)
def open_zarr_dataset(zarr_path: str, fhr: int, var: str, level: int | None, 
                      *, init_date: str | None = None, init_hour: str | None = None):
    """Opens Zarr store and selects data slice using fast sel()"""
    ds = xr.open_zarr(zarr_path, consolidated=True)
    if var not in ds: 
        raise KeyError(f"variable '{var}' not in Zarr")
    da = ds[var]
    if 'time' not in da.coords: 
        raise KeyError("Zarr has no 'time' coordinate")

    if not init_date or not init_hour:
        t0 = da['time'].values[0]
        target_time = np.datetime64(t0) + np.timedelta64(int(fhr), 'h')
    else:
        init_iso = f"{init_date[:4]}-{init_date[4:6]}-{init_date[6:8]}T{init_hour}:00:00"
        init_ts = np.datetime64(init_iso)
        target_time = init_ts + np.timedelta64(int(fhr), 'h')

    try:
        da = da.sel(time=target_time, method="nearest")
    except Exception as e:
        print(f"[AIGFS] Warning: Zarr time sel() failed ({e}). Fallback to slow argmin.")
        times = da['time'].values
        idx = int(np.argmin(np.abs(times - target_time)))
        da = da.isel(time=idx)

    for levdim in ("level", "isobaricInhPa", "lev"):
        if level is not None and levdim in da.dims:
            try:
                da = da.sel({levdim: int(level)}, method="nearest")
            except Exception as e:
                print(f"[AIGFS] Warning: Zarr level sel() failed ({e}). Fallback to slow argmin.")
                levs = da[levdim].values
                idxl = int(np.argmin(np.abs(levs.astype(np.int64) - int(level))))
                da = da.isel({levdim: idxl})
            break
    
    ds_out = da.to_dataset(name=var)
    return _normalize_coords(ds_out)

# ---- Main Access Point ---
def get_dataset(model_date: str, model_time: str, fhr: int, var: str, level: int | None):
    """
    Main entry point for fetching AIGFS data.
    Tries local Zarr first, then falls back to S3 GRIB.

    IMPORTANT: AIGFS 2t and prmsl come from *sfc* files, not *pres* files.
    """
    # Try Zarr first
    zarr_path = get_zarr_path(model_date, model_time)
    print(f"[DATA_ACCESS-AIGFS] Checking for Zarr at path: {zarr_path}")
    try:
        if os.path.isdir(zarr_path):
            print(f"[DATA_ACCESS-AIGFS] Found Zarr. Attempting to open.")
            return open_zarr_dataset(
                zarr_path, fhr, var, level,
                init_date=model_date, init_hour=model_time
            )
        else:
            print(f"[DATA_ACCESS-AIGFS] Zarr path not found.")
    except Exception as e:
        print(f"[DATA_ACCESS-AIGFS] Zarr open FAILED ({zarr_path}): {e}")

    # Fall back to S3 GRIB
    v = (var or "").lower()
    product = "sfc" if v in {"2t", "prmsl", "u10", "v10"} else "pres"

    print(f"[DATA_ACCESS-AIGFS] Falling back to S3 GRIB ({product}).")
    s3_url = get_s3_grib_url(model_date, model_time, fhr, product=product)
    print(f"[DATA_ACCESS-AIGFS] S3 URL: {s3_url}")

    try:
        local_grib_path_resolved = _resolve_local_grib(f"filecache::{s3_url}")
        flt = build_grib_filter(var, level)
        return open_grib_dataset(local_grib_path_resolved, **flt)
    except Exception as e:
        print(f"[DATA_ACCESS-AIGFS] GRIB fetch failed: {e}")
        raise


def get_levels(model_date: str, model_time: str, var: str):
    """
    Returns available pressure levels for AIGFS.
    Tries Zarr first, then falls back to S3 GRIB.
    """
    ds = None
    zarr_path = get_zarr_path(model_date, model_time)
    if os.path.isdir(zarr_path):
        try:
            ds = xr.open_zarr(zarr_path, consolidated=True)
        except Exception as e:
            print(f"[AIGFS] Zarr open failed for levels check: {e}")

    if ds is None:
        try:
            s3_url = get_s3_grib_url(model_date, model_time, fhr=0)
            local_grib_path_resolved = _resolve_local_grib(f"filecache::{s3_url}")
            flt = {"typeOfLevel": "isobaricInhPa"}
            if var in {"t", "u", "v", "z", "w", "gh"}: 
                flt["shortName"] = var
            ds = xr.open_dataset(
                local_grib_path_resolved, engine="cfgrib",
                backend_kwargs={"filter_by_keys": flt, "indexpath": ""},
            )
        except Exception as e:
            print(f"[AIGFS] GRIB open failed for levels check: {e}")
            return {"var": var, "level_dim": None, "levels": []}

    for levdim in ("isobaricInhPa", "level", "lev"):
        if levdim in ds.coords:
            levs = ds[levdim].values.tolist()
            try:
                # Apply level filter
                levs_filtered = [l for l in levs if l in AIGFS_LEVEL_FILTER]
                levs_sorted = sorted(list(set(levs_filtered)), reverse=True)
            except Exception:
                levs_sorted = []
            
            return {"var": var, "level_dim": levdim, "levels": levs_sorted}

    return {"var": var, "level_dim": None, "levels": []}
