# mercator.py
import math
import numpy as np
import scipy.spatial
from functools import lru_cache

from .config import TILE_SIZE, EARTH_RADIUS, ORIGIN_SHIFT


# Web Mercator latitude limit (cannot represent beyond this)
MAX_LATITUDE = 85.05112878


# =============================================================================
# COORDINATE CONVERSION FUNCTIONS
# =============================================================================

def lonlat_to_merc(lon, lat):
    """Convert lon/lat (degrees) to Web Mercator x/y (meters)."""
    lon = np.asarray(lon, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    
    mx = lon * ORIGIN_SHIFT / 180.0
    
    # Clip latitude to valid Web Mercator range to avoid inf at poles
    lat_clipped = np.clip(lat, -MAX_LATITUDE, MAX_LATITUDE)
    my = EARTH_RADIUS * np.log(np.tan(np.pi / 4.0 + np.radians(lat_clipped) / 2.0))
    
    return mx, my


def merc_to_lonlat(mx, my):
    """Converts Web Mercator coordinates to longitude/latitude."""
    lon = (mx / ORIGIN_SHIFT) * 180.0
    # Inverse Mercator projection formula
    lat_rad = 2.0 * np.arctan(np.exp(my / EARTH_RADIUS)) - np.pi / 2.0
    lat = np.degrees(lat_rad)
    return lon, lat


# =============================================================================
# TILE COORDINATE FUNCTIONS
# =============================================================================

def tile_bounds_merc(z, x, y):
    """Calculates Web Mercator bounds for a tile."""
    res = 2 * ORIGIN_SHIFT / (TILE_SIZE * (2 ** z))
    minx = -ORIGIN_SHIFT + x * TILE_SIZE * res
    maxx = -ORIGIN_SHIFT + (x + 1) * TILE_SIZE * res
    maxy = ORIGIN_SHIFT - y * TILE_SIZE * res
    miny = ORIGIN_SHIFT - (y + 1) * TILE_SIZE * res
    return minx, miny, maxx, maxy


def tile_bounds_lonlat(z, x, y):
    """
    Calculate lon/lat bounds for a tile.
    
    Returns (west, south, east, north) in degrees.
    """
    minx, miny, maxx, maxy = tile_bounds_merc(z, x, y)
    west, south = merc_to_lonlat(minx, miny)
    east, north = merc_to_lonlat(maxx, maxy)
    return float(west), float(south), float(east), float(north)


def merc_grid_for_tile(z, x, y, n=TILE_SIZE):
    """
    Generate Web Mercator coordinate grid for tile pixel CENTERS.
    
    This is the key fix for the "skinny columns" artifact. By sampling
    pixel centers instead of edges, we eliminate phase alignment errors.
    
    Returns (mx_grid, my_grid) as 2D arrays of shape (n, n).
    """
    minx, miny, maxx, maxy = tile_bounds_merc(z, x, y)
    
    dx = (maxx - minx) / n
    dy = (maxy - miny) / n
    
    # Pixel centers: offset by 0.5 pixels from edges
    # Old (wrong): np.linspace(minx, maxx, n) - samples edges
    # New (correct): sample center of each pixel
    xs = minx + (np.arange(n) + 0.5) * dx
    ys = maxy - (np.arange(n) + 0.5) * dy  # y decreases downward in tiles
    
    return np.meshgrid(xs, ys)


def lonlat_grid_for_tile(z, x, y, n=TILE_SIZE):
    """
    Generates a grid of Lon/Lat points for tile pixel CENTERS.
    
    Updated to use pixel-center sampling via merc_grid_for_tile().
    """
    mx, my = merc_grid_for_tile(z, x, y, n)
    lon, lat = merc_to_lonlat(mx, my)
    return lon.astype(np.float32), lat.astype(np.float32)


# =============================================================================
# BBOX CROPPING HELPER
# =============================================================================

def crop_da_to_tile(da, lon_name: str, lat_name: str, z: int, x: int, y: int, 
                    pad_deg: float = 1.5):
    """
    Crop a DataArray to the bounding box of a tile plus padding.
    
    This dramatically reduces memory usage for interpolation by limiting
    the source data to just what's needed for this tile.
    
    Args:
        da: xarray DataArray with 2D spatial data
        lon_name: name of longitude coordinate
        lat_name: name of latitude coordinate
        z, x, y: tile coordinates
        pad_deg: padding in degrees around tile bounds (default 1.5)
    
    Returns:
        Cropped DataArray, or original if crop would be too small or risky
    """
    # Skip cropping for low zoom levels (z <= 3)
    # - Tiles are very large (45°+ wide), so cropping saves minimal memory
    # - Edge cases at 0° and ±180° longitude are common and tricky to handle
    # - These tiles are typically pre-rendered anyway
    if z <= 3:
        return da
    
    west, south, east, north = tile_bounds_lonlat(z, x, y)
    
    # Add padding
    west_p = west - pad_deg
    east_p = east + pad_deg
    south_p = south - pad_deg
    north_p = north + pad_deg
    
    lon_coords = da[lon_name].values
    lat_coords = da[lat_name].values
    
    # Handle 1D coordinates (regular grid)
    if lon_coords.ndim == 1 and lat_coords.ndim == 1:
        # Normalize longitude to -180 to 180 for comparison
        lon_normalized = ((lon_coords + 180) % 360) - 180
        
        # Check if tile is near problematic longitudes (0° or ±180°)
        # If so, skip cropping to avoid seam issues
        near_zero = west_p < 5 and east_p > -5  # Tile spans 0°
        near_antimeridian = west_p < -175 or east_p > 175  # Tile near ±180°
        
        if near_zero or near_antimeridian:
            # Skip cropping for tiles near problematic longitudes
            # This is conservative but safe
            return da
        
        # Simple case: no problematic longitude crossings
        lon_mask = (lon_normalized >= west_p) & (lon_normalized <= east_p)
        lat_mask = (lat_coords >= south_p) & (lat_coords <= north_p)
        
        if lon_mask.sum() < 2 or lat_mask.sum() < 2:
            return da  # Crop too small, return original
        
        # Find indices for slicing
        lon_indices = np.where(lon_mask)[0]
        lat_indices = np.where(lat_mask)[0]
        
        lon_slice = slice(lon_indices[0], lon_indices[-1] + 1)
        lat_slice = slice(lat_indices[0], lat_indices[-1] + 1)
        
        return da.isel({lon_name: lon_slice, lat_name: lat_slice})
    
    # For 2D coordinates (curvilinear grids), fall back to full data
    # This is rare and the performance hit is acceptable
    return da


# =============================================================================
# LEGACY LON/LAT KDTREE (kept for reference/fallback)
# =============================================================================

@lru_cache(maxsize=4)
def _get_cached_kdtree(grid_key: tuple) -> scipy.spatial.cKDTree:
    """
    Internal function to build the KDTree in lon/lat space.
    LEGACY - use _get_cached_kdtree_merc for new code.
    """
    lon_coords, lat_coords = _get_cached_kdtree.prime_cache.get(grid_key)
    if lon_coords is None:
        raise Exception(f"KDTree cache miss for key {grid_key}. This should not happen.")

    print(f"[DEBUG] Building new lon/lat KDTree for grid: {grid_key}")

    lon_2d, lat_2d = None, None
    
    if lon_coords.ndim == 1 and lat_coords.ndim == 1:
        lon_2d, lat_2d = np.meshgrid(lon_coords, lat_coords)
    elif lon_coords.ndim == 2 and lat_coords.ndim == 2:
        lon_2d, lat_2d = lon_coords, lat_coords
    else:
        raise ValueError(f"Coordinate arrays have unexpected dimensions: "
                         f"lon={lon_coords.shape}, lat={lat_coords.shape}")

    points = np.column_stack((lon_2d.ravel(), lat_2d.ravel()))
    return scipy.spatial.cKDTree(points)

_get_cached_kdtree.prime_cache = {}


def fast_nearest_neighbor_interp(da, lon_name: str, lat_name: str, 
                                  target_lon: np.ndarray, target_lat: np.ndarray) -> np.ndarray:
    """
    Performs fast nearest-neighbor interpolation using a cached cKDTree index.
    LEGACY - use fast_nearest_neighbor_interp_merc for new code.
    """
    lon_coords = da[lon_name].values
    lat_coords = da[lat_name].values

    grid_key = (
        'lonlat',  # Added prefix to distinguish from merc trees
        lon_coords.ndim, lon_coords.shape, 
        float(lon_coords.min()), float(lon_coords.max()),
        lat_coords.ndim, lat_coords.shape, 
        float(lat_coords.min()), float(lat_coords.max())
    )

    if grid_key not in _get_cached_kdtree.prime_cache:
        _get_cached_kdtree.prime_cache[grid_key] = (lon_coords, lat_coords)

    try:
        tree = _get_cached_kdtree(grid_key)
    except TypeError as e:
        print(f"CRITICAL: KDTree cache failed. This may be a concurrency issue or bad key. {e}")
        raise e

    target_points = np.column_stack((target_lon.ravel(), target_lat.ravel()))
    _, indices = tree.query(target_points, k=1)

    source_data_flat = da.values.ravel()
    interpolated_flat = source_data_flat[indices]

    return interpolated_flat.reshape(target_lon.shape)


# =============================================================================
# NEW MERCATOR-SPACE KDTREE (recommended)
# =============================================================================

@lru_cache(maxsize=64)
def _get_cached_kdtree_merc(grid_key: tuple) -> scipy.spatial.cKDTree:
    """
    Build KDTree in Web Mercator coordinate space.
    
    This eliminates the coordinate system mismatch between:
    - Mercator-uniform pixel grid (what tiles use)
    - Lon/lat degrees (what the source data uses)
    
    Doing nearest-neighbor in the same coordinate system as the
    target pixels eliminates aliasing artifacts at low zoom.
    
    Cache size increased to 64 to accommodate cropped subgrids.
    """
    lon_coords, lat_coords = _get_cached_kdtree_merc.prime_cache.get(grid_key)
    if lon_coords is None:
        raise Exception(f"KDTree cache miss for key {grid_key}")

    # Only log for larger grids (avoid spam for cropped tiles)
    n_points = lon_coords.size * lat_coords.size if lon_coords.ndim == 1 else lon_coords.size
    if n_points > 10000:
        print(f"[DEBUG] Building Mercator KDTree: {grid_key[:3]} ({n_points} points)")

    # Handle 1D vs 2D coordinate arrays
    if lon_coords.ndim == 1 and lat_coords.ndim == 1:
        lon_2d, lat_2d = np.meshgrid(lon_coords, lat_coords)
    elif lon_coords.ndim == 2 and lat_coords.ndim == 2:
        lon_2d, lat_2d = lon_coords, lat_coords
    else:
        raise ValueError(f"Unexpected coordinate dimensions: "
                         f"lon={lon_coords.shape}, lat={lat_coords.shape}")

    # CRITICAL: Normalize longitude to -180 to 180
    # GFS uses 0-360 convention; without this, points near 0° and 360° 
    # appear far apart in the KDTree when they're actually adjacent
    lon_2d = ((lon_2d + 180) % 360) - 180
    
    # Convert to Web Mercator meters
    mx_2d, my_2d = lonlat_to_merc(lon_2d, lat_2d)
    
    points = np.column_stack((mx_2d.ravel(), my_2d.ravel()))
    return scipy.spatial.cKDTree(points)

_get_cached_kdtree_merc.prime_cache = {}


def fast_nearest_neighbor_interp_merc(da, lon_name: str, lat_name: str, 
                                       target_mx: np.ndarray, target_my: np.ndarray) -> np.ndarray:
    """
    Nearest-neighbor interpolation using Mercator-space KDTree.
    
    This is the recommended interpolation function. It eliminates the
    coordinate system mismatch that causes striping artifacts at low zoom.
    
    Args:
        da: xarray DataArray with the source data
        lon_name: name of the longitude coordinate
        lat_name: name of the latitude coordinate  
        target_mx: 2D array of target Web Mercator x coordinates (meters)
        target_my: 2D array of target Web Mercator y coordinates (meters)
    
    Returns:
        2D array of interpolated values matching shape of target_mx
    """
    lon_coords = da[lon_name].values
    lat_coords = da[lat_name].values

    # Cache key includes 'merc' prefix to distinguish from lon/lat trees
    grid_key = (
        'merc',
        lon_coords.ndim, lon_coords.shape, 
        float(lon_coords.min()), float(lon_coords.max()),
        lat_coords.ndim, lat_coords.shape, 
        float(lat_coords.min()), float(lat_coords.max())
    )

    if grid_key not in _get_cached_kdtree_merc.prime_cache:
        _get_cached_kdtree_merc.prime_cache[grid_key] = (lon_coords, lat_coords)

    tree = _get_cached_kdtree_merc(grid_key)

    target_points = np.column_stack((target_mx.ravel(), target_my.ravel()))
    _, indices = tree.query(target_points, k=1)

    source_data_flat = da.values.ravel()
    interpolated_flat = source_data_flat[indices]

    return interpolated_flat.reshape(target_mx.shape)


def clear_kdtree_caches():
    """
    Clear all KDTree caches.
    
    Call this periodically to free memory, e.g., after processing
    a batch of requests or when memory pressure is detected.
    """
    _get_cached_kdtree.cache_clear()
    _get_cached_kdtree.prime_cache.clear()
    _get_cached_kdtree_merc.cache_clear()
    _get_cached_kdtree_merc.prime_cache.clear()