# config.py
import os
import math
from functools import lru_cache

# --- Global Constants ---
TILE_SIZE = 512
PCTL_LOW  = 2.0   # Percentile for minimum color range
PCTL_HIGH = 98.0  # Percentile for maximum color range
EARTH_RADIUS = 6378137.0
ORIGIN_SHIFT = math.pi * EARTH_RADIUS

# --- Data Roots ---
# GraphCast
ZARR_ROOT_GRAPHCAST = "/mnt/data/zarr/graphcast"
S3_ROOT_GRAPHCAST = "s3://noaa-nws-graphcastgfs-pds"

# GFS
ZARR_ROOT_GFS = "/mnt/data/zarr/gfs"
S3_ROOT_GFS = "s3://noaa-gfs-bdp-pds"

# AIGFS (NEW)
ZARR_ROOT_AIGFS = "/mnt/data/zarr/aigfs"
S3_ROOT_AIGFS = "s3://noaa-nps-aigfs"


# --- File Cache Configuration ---
# MODIFIED: Read cache directory from environment variable
# This allows prerender scripts to use a separate cache directory
FILECACHE_OPTS = {
    "cache_storage": os.environ.get("GRIB_CACHE_DIR", "/mnt/grib_cache"),
    "same_names": False,
    "check_files": True,
    "expiry_time": 1 * 2400,    # 4 hours; tune as you like
    "cache_check": 25,          # prune about every 50 cache operations
}

# --- S3 (s3fs) Options ---
# For public NOAA buckets (anonymous access)
S3_OPTS = {
    "anon": True,
    "client_kwargs": {"region_name": "us-east-1"},
    "config_kwargs": {"max_pool_connections": 64},
}

# For AIGFS bucket (your private bucket - requires credentials)
# Set anon=False if you need AWS credentials for this bucket
# Credentials will be picked up from environment variables, 
# ~/.aws/credentials, or IAM role
S3_OPTS_AIGFS = {
    "anon": False,  # Your bucket requires authentication
    "client_kwargs": {"region_name": "us-east-1"},
    "config_kwargs": {"max_pool_connections": 64},
}

# --- Pre-rendering Configuration ---
# Root directory for pre-rendered tiles
PRERENDER_ROOT = "/mnt/data/tiles"

# Pre-rendering scope
PRERENDER_ZMAX = 5 # Maximum zoom level (0-6)
PRERENDER_FHRS = list(range(0, 13, 6))  # Forecast hours: [0, 6, 12, ..., 48]

# Product configuration
PRERENDER_PRODUCT = "temp_sfc"  # Product name (stable URL)
PRERENDER_VAR = "t"  # Variable name
PRERENDER_LEVEL = 1000  # Pressure level (hPa)

# Fixed color scale for temperature (Kelvin)
PRERENDER_TEMP_VMIN = 233.15  # -40°C
PRERENDER_TEMP_VMAX = 323.15  # +50°C
PRERENDER_TEMP_CMAP = "turbo"

# Retention policy
PRERENDER_KEEP_RUNS = {
    "graphcast": 1,  # Keep only latest run
    "gfs": 1,        # Keep only latest run
    "aigfs": 1,      # Keep only latest run (NEW)
}

# Separate GRIB cache for pre-rendering (to avoid contention with live app)
PRERENDER_GRIB_CACHE = "/mnt/grib_cache_prerender"


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
