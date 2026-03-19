# mercator.py
import math
import numpy as np
from .config import TILE_SIZE, EARTH_RADIUS, ORIGIN_SHIFT

def tile_bounds_merc(z, x, y):
    """Calculates Web Mercator bounds for a tile."""
    res = 2 * ORIGIN_SHIFT / (TILE_SIZE * (2 ** z))
    minx = -ORIGIN_SHIFT + x * TILE_SIZE * res
    maxx = -ORIGIN_SHIFT + (x + 1) * TILE_SIZE * res
    maxy = ORIGIN_SHIFT - y * TILE_SIZE * res
    miny = ORIGIN_SHIFT - (y + 1) * TILE_SIZE * res
    return minx, miny, maxx, maxy

def merc_to_lonlat(mx, my):
    """Converts Web Mercator coordinates to longitude/latitude."""
    lon = (mx / ORIGIN_SHIFT) * 180.0
    # Inverse Mercator projection formula
    lat_rad = 2.0 * np.arctan(np.exp(my / EARTH_RADIUS)) - np.pi / 2.0
    lat = np.degrees(lat_rad)
    return lon, lat

def lonlat_grid_for_tile(z, x, y, n=TILE_SIZE):
    """Generates a grid of Lon/Lat points for every pixel in a tile."""
    minx, miny, maxx, maxy = tile_bounds_merc(z, x, y)
    xs = np.linspace(minx, maxx, n)
    # y-axis is reversed in tile coordinates
    ys = np.linspace(miny, maxy, n)[::-1]
    mx, my = np.meshgrid(xs, ys)
    lon, lat = merc_to_lonlat(mx, my)
    return lon.astype(np.float32), lat.astype(np.float32)