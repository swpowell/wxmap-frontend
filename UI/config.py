# config.py
import os
import math
from functools import lru_cache

# --- Global Constants ---
TILE_SIZE = 256
PCTL_LOW  = 2.0   # Percentile for minimum color range
PCTL_HIGH = 98.0  # Percentile for maximum color range
EARTH_RADIUS = 6378137.0
ORIGIN_SHIFT = math.pi * EARTH_RADIUS

# --- Data Roots (GraphCast Specific - can be expanded later) ---
ZARR_ROOT = "/mnt/data/zarr/graphcast"
S3_ROOT = "s3://noaa-nws-graphcastgfs-pds"

# --- File Cache Configuration ---
FILECACHE_OPTS = {
    "cache_storage": "/mnt/grib_cache",
    "same_names": True,
    "check_files": False,
}

# --- S3 (s3fs) Options ---
S3_OPTS = {
    "anon": True,
    "client_kwargs": {"region_name": "us-east-1"},
    "config_kwargs": {"max_pool_connections": 64},
}


@lru_cache(maxsize=1)
def read_mapbox_token():
    """Reads the MAPBOX_TOKEN from the expected file path."""
    try:
        # Construct the absolute path: current_dir/../mapbox_token
        token_path = os.path.join(os.path.dirname(__file__), '..', 'mapbox_token')
        with open(token_path, 'r') as f:
            token = f.read().strip()
        print(f"MAPBOX_TOKEN loaded successfully from {token_path}")
        return token
    except FileNotFoundError:
        print(f"Warning: Mapbox token file not found. Using placeholder 'XXX'.")
        return "XXX"
    except Exception as e:
        print(f"Error reading Mapbox token file: {e}. Using placeholder 'XXX'.")
        return "XXX"

# Expose the token reading function result directly
MAPBOX_TOKEN = read_mapbox_token()