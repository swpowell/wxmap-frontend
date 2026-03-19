# data_access.py
from functools import lru_cache
import numpy as np
import xarray as xr
import pandas as pd
import fsspec
import os
import time

from .config import ZARR_ROOT, S3_ROOT, FILECACHE_OPTS, S3_OPTS

# ---- S3/Cache Resolver ---
def _resolve_local_grib(url: str) -> str:
    """Returns a local filesystem path for the given S3 object using fsspec's filecache."""
    wrapped = f"filecache::{url}" if url.startswith("s3://") else url

    storage_options = {
        "filecache": FILECACHE_OPTS,
        "s3": S3_OPTS,
    }

    t0 = time.perf_counter()
    local_path = fsspec.open_local(wrapped, **storage_options)
    try:
        os.path.getsize(local_path)
    except Exception:
        pass
    return local_path

# ---- GraphCast Path Generators ---
def get_zarr_path(model_date: str, model_time: str) -> str:
    """Generates path to local Zarr store for a given init time."""
    return os.path.join(ZARR_ROOT, model_date, f"{model_time}.zarr")

def get_s3_grib_url(model_date: str, model_time: str, fhr: int) -> str:
    """Generates S3 URL for a specific GRIB file."""
    fhr_str = str(fhr).zfill(3)
    return f"{S3_ROOT}/graphcastgfs.{model_date}/{model_time}/forecasts_13_levels/graphcastgfs.t{model_time}z.pgrb2.0p25.f{fhr_str}"

# ---- Helper Functions ---
def build_grib_filter(var: str, level: int | None):
    """Creates a cfgrib filter dictionary."""
    var = var.lower()
    if var in {"t", "u", "v", "z", "w", "gh"}:
        flt = {"shortName": var, "typeOfLevel": "isobaricInhPa"}
        if level is not None:
            flt["level"] = int(level)
        return flt
    if var in {"u10", "v10"}:
        return {"shortName": var, "typeOfLevel": "heightAboveGround", "level": 10}
    return {"shortName": var}

def _normalize_coords(ds):
    """Helper to apply lon/lat sorting and wrapping in-place."""
    lon_name = "longitude" if "longitude" in ds.coords else "lon"
    lat_name = "latitude"  if "latitude"  in ds.coords else "lat"

    lon = ds[lon_name]
    if lon.min() >= 0 and lon.max() <= 360:
        ds = ds.assign_coords({lon_name: xr.where(lon >= 180, lon - 360, lon)})
    ds = ds.sortby(lon_name)

    if (ds[lat_name].diff(lat_name) < 0).any():
        ds = ds.sortby(lat_name)

    return ds, lon_name, lat_name

# ---- Data Openers (Cached) ---
@lru_cache(maxsize=16)
def open_grib_dataset(resolved_grib_path: str, **flt):
    """Opens a specific, resolved GRIB file path."""
    ds = xr.open_dataset(
        resolved_grib_path,
        engine="cfgrib",
        backend_kwargs={"filter_by_keys": flt or None, "indexpath": ""},
    )
    return _normalize_coords(ds)

@lru_cache(maxsize=16)
def open_zarr_dataset(zarr_path: str, fhr: int, var: str, level: int | None, *, init_date: str | None = None, init_hour: str | None = None):
    """Opens Zarr store and selects data slice by valid time."""
    ds = xr.open_zarr(zarr_path, consolidated=True)

    if var not in ds:
        raise KeyError(f"variable '{var}' not in Zarr store: {list(ds.data_vars)}")

    da = ds[var]

    if 'time' not in da.coords or da['time'].size == 0:
        raise KeyError("Zarr dataset has no non-empty 'time' coordinate")

    if not init_date or not init_hour:
        t0 = da['time'].values[0]
        target = np.datetime64(t0) + np.timedelta64(int(fhr), 'h')
    else:
        init_iso = f"{init_date[:4]}-{init_date[4:6]}-{init_date[6:8]}T{init_hour}:00:00"
        init_ts = np.datetime64(init_iso)
        target = init_ts + np.timedelta64(int(fhr), 'h')

    times = da['time'].values
    try:
        idx = int(np.argmin(np.abs(times - target)))
    except Exception:
        target_ts = pd.to_datetime(str(target))
        idx = int(np.argmin(np.abs(pd.to_datetime(times) - target_ts)))

    da = da.isel(time=idx)

    for levdim in ("level", "isobaricInhPa", "lev"):
        if level is not None and levdim in da.dims:
            try:
                da = da.sel({levdim: int(level)})
            except Exception:
                levs = da[levdim].values
                idxl = int(np.argmin(np.abs(levs.astype(np.int64) - int(level))))
                da = da.isel({levdim: idxl})
            break

    ds_out = da.to_dataset(name=var)
    return _normalize_coords(ds_out)

# ---- Main Access Point ---
def get_dataset(model_date: str, model_time: str, fhr: int, var: str, level: int | None):
    """
    Main data-opening function. Tries Zarr first, falls back to S3 GRIB.
    Returns (ds, lon_name, lat_name).
    """
    zarr_path = get_zarr_path(model_date, model_time)

    try:
        if os.path.isdir(zarr_path):
            # --- ADDED PRINT STATEMENT ---
            print(f"[DATA_ACCESS] Found Zarr. Attempting to open: {zarr_path}")
            return open_zarr_dataset(zarr_path, fhr, var, level, init_date=model_date, init_hour=model_time)
    except Exception as e:
        print(f"[DATA_ACCESS] Zarr open FAILED ({zarr_path}): {e}")

    # --- ADDED PRINT STATEMENT ---
    print(f"[DATA_ACCESS] Zarr not found or failed. Falling back to S3 GRIB.")
    
    # Fallback to S3 GRIB
    s3_url = get_s3_grib_url(model_date, model_time, fhr)
    local_grib_path_cached = f"filecache::{s3_url}"
    local_grib_path_resolved = _resolve_local_grib(local_grib_path_cached)

    flt = build_grib_filter(var, level)

    return open_grib_dataset(local_grib_path_resolved, **flt)

def get_levels(model_date: str, model_time: str, var: str):
    """Fetches available pressure levels for a given model run and variable."""
    ds = None
    # Try Zarr
    zarr_path = get_zarr_path(model_date, model_time)
    if os.path.isdir(zarr_path):
        try:
            # --- FIX: Use xr.open_zarr with consolidated=True ---
            # This is MUCH faster than open_dataset(engine="zarr")
            # as it reads the .zmetadata file directly.
            ds = xr.open_zarr(zarr_path, consolidated=True)
        except Exception as e:
            print(f"Zarr open failed for levels check: {e}")

    # Fallback to GRIB (f000)
    if ds is None:
        try:
            s3_url = get_s3_grib_url(model_date, model_time, fhr=0)
            local_grib_path_resolved = _resolve_local_grib(f"filecache::{s3_url}")

            flt = {"typeOfLevel": "isobaricInhPa"}
            if var in {"t", "u", "v", "z", "w", "gh"}:
                flt["shortName"] = var

            ds_grib = xr.open_dataset(
                local_grib_path_resolved,
                engine="cfgrib",
                backend_kwargs={"filter_by_keys": flt, "indexpath": ""},
            )
            ds = ds_grib
        except Exception as e:
            print(f"GRIB open failed for levels check: {e}")

    if ds is None:
         return {"var": var, "level_dim": None, "levels": []}

    for levdim in ("isobaricInhPa", "level", "lev"):
        if levdim in ds.coords:
            levs = ds[levdim].values.tolist()
            try:
                # Ensure levels are unique and sorted
                levs = sorted(list(set(levs)), reverse=True)
            except Exception:
                pass
            return {"var": var, "level_dim": levdim, "levels": levs}

    return {"var": var, "level_dim": None, "levels": []}