# UI/model_atlas.py
from __future__ import annotations

import os
from functools import lru_cache
from typing import Tuple, Optional

import s3fs
import xarray as xr

from .config import S3_OPTS_AIGFS  # reuse creds pattern

S3_BUCKET = "nps-atlas"
S3_PREFIX = "atlas-gfs"

FORECAST_STEP_HOURS = int(os.getenv("ATLAS_STEP_HOURS", "6"))
MAX_FHR = int(os.getenv("ATLAS_MAX_FHR", "240"))

LEAD_DIM = "lead_time"
AVAILABLE_LEVELS = [500]

# Maps request aliases -> (store var, canonical output var)
VARIABLE_MAP = {
    "t2m": ("t2m", "t2m"),
    "2t": ("t2m", "t2m"),

    # Your pipeline asks for prmsl; Atlas stores msl
    "prmsl": ("msl", "prmsl"),
    "msl": ("msl", "prmsl"),

    # Your pipeline asks for gh (+ level=500); Atlas stores z500 already as height
    "gh": ("z500", "gh"),
    "z500": ("z500", "gh"),
}

def _get_s3_opts() -> dict:
    opts = dict(S3_OPTS_AIGFS)
    if os.getenv("S3FS_NO_CACHE", "0") == "1":
        opts["skip_instance_cache"] = True
    return opts

def get_zarr_s3_url(model_date: str, model_time: str) -> str:
    return f"s3://{S3_BUCKET}/{S3_PREFIX}/{model_date}/{model_time}/zarr"

def get_zarr_s3_key(model_date: str, model_time: str) -> str:
    return f"{S3_BUCKET}/{S3_PREFIX}/{model_date}/{model_time}/zarr"

def get_ready_marker_key(model_date: str, model_time: str) -> str:
    # Run-level marker next to zarr/
    return f"{S3_BUCKET}/{S3_PREFIX}/{model_date}/{model_time}/_READY"

def fhr_to_index(fhr: int) -> int:
    if fhr < 0 or fhr > MAX_FHR:
        raise ValueError(f"Forecast hour {fhr} out of range (0-{MAX_FHR})")
    if fhr % FORECAST_STEP_HOURS != 0:
        raise ValueError(f"Forecast hour {fhr} must be multiple of {FORECAST_STEP_HOURS}")
    return fhr // FORECAST_STEP_HOURS

def _check_ready(model_date: str, model_time: str) -> bool:
    fs = s3fs.S3FileSystem(**_get_s3_opts())
    return fs.exists(get_ready_marker_key(model_date, model_time))

def _normalize_coords(ds: xr.Dataset) -> xr.Dataset:
    # Rename coordinate variants if needed
    rename = {}
    if "longitude" in ds.coords and "lon" not in ds.coords:
        rename["longitude"] = "lon"
    if "latitude" in ds.coords and "lat" not in ds.coords:
        rename["latitude"] = "lat"
    if rename:
        ds = ds.rename(rename)

    # Lon to -180..180
    if "lon" in ds.coords:
        lon = ds["lon"]
        if float(lon.min()) >= 0 and float(lon.max()) > 180:
            ds = ds.assign_coords(lon=xr.where(lon > 180, lon - 360, lon)).sortby("lon")

    # Lat increasing
    if "lat" in ds.coords:
        lat = ds["lat"]
        if float(lat[0]) > float(lat[-1]):
            ds = ds.sortby("lat")

    return ds

@lru_cache(maxsize=2)
def _open_zarr(model_date: str, model_time: str) -> xr.Dataset:
    if not _check_ready(model_date, model_time):
        raise FileNotFoundError(
            f"Atlas _READY marker not found: s3://{get_ready_marker_key(model_date, model_time)}"
        )

    zarr_url = get_zarr_s3_url(model_date, model_time)
    print(f"[atlas] Opening Zarr: {zarr_url}")

    fs = s3fs.S3FileSystem(**_get_s3_opts())
    mapper = s3fs.S3Map(root=get_zarr_s3_key(model_date, model_time), s3=fs, check=False)

    consolidated = os.getenv("ATLAS_ZARR_CONSOLIDATED", "0") == "1"
    ds = xr.open_zarr(mapper, consolidated=consolidated, decode_timedelta=False)
    ds = _normalize_coords(ds)

    print(f"[atlas] Opened. Vars: {list(ds.data_vars)}, Dims: {dict(ds.dims)}")
    return ds

def _extract(ds: xr.Dataset, var: str, level: Optional[int], fhr: int) -> Tuple[xr.DataArray, str]:
    v = var.lower()
    if v not in VARIABLE_MAP:
        raise ValueError(f"Variable '{var}' not supported for Atlas. Supported: {list(VARIABLE_MAP.keys())}")

    store_var, canonical = VARIABLE_MAP[v]

    if canonical == "gh" and level not in (None, 500):
        raise ValueError("Atlas only supports gh at 500 hPa (level=500).")

    if store_var not in ds:
        raise KeyError(f"Atlas dataset missing '{store_var}'. Have: {list(ds.data_vars)}")

    da = ds[store_var]

    # Index by lead_time if present
    if LEAD_DIM in da.dims:
        da = da.isel({LEAD_DIM: fhr_to_index(fhr)})

    # Ensure truly 2D for rendering/interp
    da = da.squeeze(drop=True)
    return da, canonical

def get_dataset(model_date: str, model_time: str, fhr: int, var: str, level: Optional[int]):
    ds = _open_zarr(model_date, model_time)
    da2d, canonical = _extract(ds, var, level, fhr)
    out = xr.Dataset({canonical: da2d}, coords=ds.coords)
    return out, "lon", "lat"

def get_levels(model_date: str, model_time: str, var: str) -> dict:
    if var.lower() == "gh":
        return {"var": var, "level_dim": "level", "levels": AVAILABLE_LEVELS}
    return {"var": var, "level_dim": None, "levels": []}
