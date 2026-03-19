# app.py (EC2 Dynamic Rendering - Refactored for Style-Based Colorization)
# =============================================================================
# CloudFront Failover Origin for Dynamic Tile Rendering
# =============================================================================
#
# Key changes from previous version:
#   1. Uses styles.parse_product() for consistent var/level mapping
#   2. Uses visualization.colorize_styled() for fixed color scales
#   3. Adds /colorbar endpoint for frontend legend
#   4. Uses bbox cropping for memory efficiency (2025-02)
#
# =============================================================================

import os
import io
import numpy as np
from PIL import Image
from flask import Flask, render_template, send_file, request, abort, jsonify

# Import Modularized Components
from . import config
from . import mercator
from . import data_access
from . import visualization
from . import styles
import time
from collections import Counter

app = Flask(__name__)


# -----------------------------------------------------------------------------
# CLOUDWATCH LOGGING FOR FALLBACK MONITORING
# -----------------------------------------------------------------------------

_FALLBACK_COUNTS = Counter()
_FALLBACK_LAST_EMIT = 0
_FALLBACK_EMIT_INTERVAL = 60  # seconds


def record_fallback(model, product, z):
    """
    Aggregate fallback tile renders and emit a summary log once per interval.
    """
    global _FALLBACK_LAST_EMIT

    key = (model, product, z)
    _FALLBACK_COUNTS[key] += 1

    now = time.time()
    if now - _FALLBACK_LAST_EMIT >= _FALLBACK_EMIT_INTERVAL:
        for (m, p, zz), count in list(_FALLBACK_COUNTS.items())[:50]:
            app.logger.info(
                f"FALLBACK_SUMMARY model={m} product={p} z={zz} count={count}"
            )
        _FALLBACK_COUNTS.clear()
        _FALLBACK_LAST_EMIT = now


# -----------------------------------------------------------------------------
# ORIGIN VERIFICATION MIDDLEWARE
# -----------------------------------------------------------------------------

@app.before_request
def verify_cloudfront_origin():
    """Block direct access to tile/contour endpoints - must come through CloudFront."""
    path = request.path
    if path.startswith('/tiles/') or path.startswith('/contours/'):
        secret = request.headers.get('X-Origin-Verify')
        expected = os.environ.get('ORIGIN_VERIFY_SECRET')
        if expected and secret != expected:
            abort(403, description="Direct access not allowed")


# -----------------------------------------------------------------------------
# ROUTES
# -----------------------------------------------------------------------------

@app.route("/")
@app.route("/map")
def index():
    """Renders the main map page."""
    return render_template("map.html", mapbox_token=config.MAPBOX_TOKEN)


# -----------------------------------------------------------------------------
# TILE ROUTE (Path-Based for CloudFront Failover)
# -----------------------------------------------------------------------------

@app.route("/tiles/<model>/<model_date>/<model_time>/<product>/<fhr_str>/<int:z>/<int:x>/<int:y>.png")
def tiles(model, model_date, model_time, product, fhr_str, z, x, y):
    """
    CloudFront FAILOVER origin for dynamic raster tiles.
    
    Path structure matches S3 key:
        tiles/graphcast/20260118/12/gh500/132/2/1/1.png
    
    Uses FIXED color scales from styles.py for consistency with pre-rendered tiles.
    Uses bbox cropping for memory efficiency at higher zoom levels.
    """
    # Parse product using centralized mapping from styles.py
    var, level_int = styles.parse_product(product)
    fhr_int = int(fhr_str) if fhr_str.isdigit() else 0
    
    # Log fallback for monitoring
    record_fallback(model, product, z)
    
    # 1. Fetch Dataset
    try:
        ds, lon_name, lat_name = data_access.get_model_dataset(
            model, model_date, model_time, fhr_int, var, level_int
        )
    except Exception as e:
        app.logger.error(f"Dataset open failed ({model}, {var}, {level_int}): {e}")
        return (f"Dataset open failed: {e}", 500)

    # Get the data variable
    da = ds[var] if var in ds.data_vars else next(iter(ds.data_vars.values()))

    # 2. Crop to tile bbox for memory efficiency
    # This is critical for high-zoom tiles - reduces KDTree from ~1M to ~100s of points
    # Note: crop_da_to_tile() skips cropping for z<=3 to avoid seam issues
    da_cropped = mercator.crop_da_to_tile(da, lon_name, lat_name, z, x, y, pad_deg=1.5)

    # 3. Prepare Interpolation Grid (Web Mercator)
    mx, my = mercator.merc_grid_for_tile(z, x, y, config.TILE_SIZE)

    # 4. Interpolate Data (using cropped data)
    try:
        samp = mercator.fast_nearest_neighbor_interp_merc(
            da_cropped, lon_name, lat_name, mx, my
        )
    except Exception as e:
        app.logger.error(f"Interpolation failed: {e}")
        return (f"Interpolation failed: {e}", 500)

    # 5. Colorize using FIXED STYLE (key change!)
    try:
        img = visualization.colorize_styled(samp, product)
        bio = io.BytesIO()
        img.save(bio, "PNG", compress_level=1)
        bio.seek(0)
        
        response = send_file(bio, mimetype="image/png")
        response.headers["Cache-Control"] = "public, max-age=300"
        response.headers["X-Tile-Source"] = "dynamic"
        return response
    except Exception as e:
        app.logger.error(f"Colorize failed: {e}")
        return (f"Colorize failed: {e}", 500)


# -----------------------------------------------------------------------------
# CONTOUR ROUTE
# -----------------------------------------------------------------------------

@app.route("/contours/<model>/<model_date>/<model_time>/<product>/<fhr_str>/<int:z>/<int:x>/<int:y>.png")
def contours(model, model_date, model_time, product, fhr_str, z, x, y):
    """
    Geopotential height contour tiles.
    """
    var, level_int = styles.parse_product(product)
    fhr_int = int(fhr_str) if fhr_str.isdigit() else 0
    
    # Style params
    interval = request.args.get("interval", default=6.0, type=float)
    label = request.args.get("label", default=1, type=int)
    lw = request.args.get("lw", default=1.0, type=float)

    # Contours need a pressure level
    if level_int is None:
        return _empty_tile()

    # Fetch GH data
    opened = None
    var_name = "gh"
    for v in ("gh", "z"):
        try:
            ds, lon_name, lat_name = data_access.get_model_dataset(
                model, model_date, model_time, fhr_int,
                var=v, level=level_int
            )
            opened = (ds, lon_name, lat_name)
            var_name = v
            break
        except Exception as e:
            app.logger.debug(f"Contour data access failed for {v}: {e}")

    if opened is None:
        return _empty_tile()

    ds, lon_name, lat_name = opened
    da = ds[var_name]

    # Get global range for contour levels
    # NOTE: We need full data range for contours, so don't crop here
    try:
        gvmin_m, gvmax_m = visualization.get_global_range(
            model, model_date, model_time, fhr_int, var_name, level_int, da=da
        )
    except Exception as e:
        app.logger.warning(f"Global range for contours failed ({e}). Using local.")
        gvmin_m, gvmax_m = np.nanmin(da.values), np.nanmax(da.values)

    # Crop for interpolation (but after getting global range)
    da_cropped = mercator.crop_da_to_tile(da, lon_name, lat_name, z, x, y, pad_deg=1.5)

    # Interpolate
    mx, my = mercator.merc_grid_for_tile(z, x, y, config.TILE_SIZE)

    try:
        vals = mercator.fast_nearest_neighbor_interp_merc(
            da_cropped, lon_name, lat_name, mx, my
        )
    except Exception as e:
        app.logger.error(f"Contour interpolation failed: {e}")
        return (f"Interpolation failed: {e}", 500)

    vals_dam = vals / 10.0  # Convert to decameters

    if not np.isfinite(vals_dam).any():
        return _empty_tile()

    # Draw contours
    try:
        buf = visualization.draw_contours(
            gvmin_m, gvmax_m,
            vals_dam, interval, label, lw
        )
        response = send_file(buf, mimetype="image/png")
        response.headers["Cache-Control"] = "public, max-age=300"
        response.headers["X-Tile-Source"] = "dynamic"
        return response
    except Exception as e:
        app.logger.error(f"Contour drawing failed: {e}")
        return (f"Contour drawing failed: {e}", 500)


def _empty_tile():
    """Return a transparent PNG tile."""
    empty = Image.new("RGBA", (config.TILE_SIZE, config.TILE_SIZE), (0, 0, 0, 0))
    bio = io.BytesIO()
    empty.save(bio, "PNG", compress_level=1)
    bio.seek(0)
    return send_file(bio, mimetype="image/png", max_age=300)


# -----------------------------------------------------------------------------
# COLORBAR API ENDPOINT (NEW)
# -----------------------------------------------------------------------------

@app.route("/colorbar/<product>")
def colorbar_config(product):
    """
    Returns colorbar configuration for frontend legend rendering.
    
    Example response:
    {
        "product": "t2m",
        "label": "2m Temperature",
        "units": "°C",
        "levels": [-40, -38, -36, ..., 48, 50],
        "vmin": -40,
        "vmax": 50,
        "cmap": "RdYlBu_r",
        "extend": "both",
        "nbins": 45
    }
    """
    config_data = styles.get_colorbar_config(product)
    if not config_data:
        return jsonify({"error": f"Unknown product: {product}"}), 404
    
    return jsonify(config_data)


@app.route("/colorbar/<product>/image.png")
def colorbar_image(product):
    """
    Returns a colorbar image that matches the tile colors exactly.
    
    Query params:
        width: image width (default 300)
        height: image height (default 20)
    """
    width = request.args.get("width", default=300, type=int)
    height = request.args.get("height", default=20, type=int)
    
    # Clamp to reasonable sizes
    width = max(50, min(1000, width))
    height = max(10, min(100, height))
    
    try:
        img_bytes = visualization.generate_colorbar_image(product, width, height)
        return send_file(
            io.BytesIO(img_bytes),
            mimetype="image/png",
            max_age=86400  # Cache for 1 day (colorbars are static)
        )
    except Exception as e:
        app.logger.error(f"Colorbar generation failed: {e}")
        return (f"Colorbar generation failed: {e}", 500)


# -----------------------------------------------------------------------------
# API ENDPOINTS
# -----------------------------------------------------------------------------

@app.route("/levels")
def levels_api():
    """API endpoint to get available pressure levels."""
    model = request.args.get("model", "graphcast")
    var = request.args.get("var", "t").lower()
    model_date = request.args.get("date", "20251109")
    model_time = request.args.get("init", "00")

    result = data_access.get_model_levels(model, model_date, model_time, var)
    if "error" in result:
        return (result, 500)
    return result


@app.route("/point_value")
def point_value():
    """
    API endpoint to get the data value at a specific lat/lon point.
    
    Returns value in NATIVE units. Frontend handles display conversion.
    """
    try:
        model = request.args.get("model", "graphcast")
        model_date = request.args.get("date", "20260115")
        model_time = request.args.get("init", "00")
        fhr_str = request.args.get("fhr", "000")
        var = request.args.get("var", "gh")
        level_str = request.args.get("level")
        
        lat = request.args.get("lat", type=float)
        lon = request.args.get("lon", type=float)
        
        if lat is None or lon is None:
            return ({"error": "Missing lat or lon parameter"}, 400)
        
        level_int = int(level_str) if level_str and level_str.isdigit() else None
        fhr_int = int(fhr_str) if fhr_str and fhr_str.isdigit() else 0
        
        ds, lon_name, lat_name = data_access.get_model_dataset(
            model, model_date, model_time, fhr_int, var, level_int
        )
        
        da = ds[var] if var in ds.data_vars else next(iter(ds.data_vars.values()))
        
        point_value_result = da.interp(
            {lon_name: lon, lat_name: lat},
            method="nearest"
        ).values
        
        point_value_result = float(point_value_result)
        
        return {
            "lat": lat,
            "lon": lon,
            "value": point_value_result,
            "var": var,
            "level": level_int,
            "units": "native"  # Frontend handles conversion
        }
        
    except Exception as e:
        app.logger.error(f"Error in point_value: {e}")
        import traceback
        traceback.print_exc()
        return ({"error": str(e)}, 500)