# app.py (Refactored)
from flask import Flask, render_template, send_file, request
import io
import numpy as np
from PIL import Image

# Import Modularized Components
from . import config
from . import mercator
from . import data_access
from . import visualization

app = Flask(__name__)

# --- Routes ---

@app.route("/")
@app.route("/map")
def index():
    """Renders the main map page."""
    # MAPBOX_TOKEN is read from file via config.py
    return render_template("map.html", mapbox_token=config.MAPBOX_TOKEN)

@app.route("/tiles/<int:z>/<int:x>/<int:y>.png")
def tiles(z, x, y):
    """Generates and returns the weather data tile (raster)."""
    # Parse request parameters
    model_date = request.args.get("date", "20251109")
    model_time = request.args.get("init", "00")
    fhr_str    = request.args.get("fhr", "024")

    var        = request.args.get("var", "t")
    level_str  = request.args.get("level")
    cmap       = request.args.get("cmap", "turbo")
    vmin_q     = request.args.get("vmin", type=float)
    vmax_q     = request.args.get("vmax", type=float)

    level_int = int(level_str) if level_str and level_str.isdigit() else None
    fhr_int   = int(fhr_str) if fhr_str and fhr_str.isdigit() else 24

    # 1. Fetch Dataset
    try:
        ds, lon_name, lat_name = data_access.get_dataset(model_date, model_time, fhr_int, var, level_int)
    except Exception as e:
        print(f"Error fetching dataset: {e}")
        return (f"Dataset open failed: {e}", 500)

    # Choose the data array
    da = ds[var] if var in ds.data_vars else next(iter(ds.data_vars.values()))

    # 2. Prepare Interpolation Grid (WebMercator)
    lon, lat = mercator.lonlat_grid_for_tile(z, x, y, config.TILE_SIZE)

    # 3. Interpolate Data
    try:
        samp = da.interp(
            {lon_name: (("y","x"), lon), lat_name: (("y","x"), lat)},
            method="nearest",
            kwargs={"fill_value": np.nan},
        ).values
    except Exception as e:
        print(f"Error interpolating data: {e}")
        return (f"Interpolation failed: {e}", 500)

    # 4. Get Color Range
    try:
        gvmin, gvmax = visualization.get_global_range(model_date, model_time, fhr_int, var, level_int)
    except Exception as e:
        # Fallback if global range calculation fails
        gvmin, gvmax = np.nanmin(samp), np.nanmax(samp)
        print(f"Warning: Global range calculation failed ({e}). Using local range.")


    vmin = gvmin if vmin_q is None else vmin_q
    vmax = gvmax if vmax_q is None else vmax_q

    # 5. Colorize and Serve
    try:
        img = visualization.colorize(samp, vmin=vmin, vmax=vmax, cmap_name=cmap)
        bio = io.BytesIO(); img.save(bio, "PNG"); bio.seek(0)
        return send_file(bio, mimetype="image/png", max_age=30)
    except Exception as e:
        print(f"Error colorizing image: {e}")
        return (f"Colorize failed: {e}", 500)


@app.route("/levels")
def levels_api():
    """API endpoint to get available pressure levels."""
    var = request.args.get("var", "t").lower()
    model_date = request.args.get("date", "20251109")
    model_time = request.args.get("init", "00")

    result = data_access.get_levels(model_date, model_time, var)
    if "error" in result:
        return (result, 500)
    return result

@app.route("/contours/<int:z>/<int:x>/<int:y>.png")
def contours(z, x, y):
    """Generates and returns the GH contour lines tile."""
    model_date = request.args.get("date", "20251109")
    model_time = request.args.get("init", "00")
    fhr_str    = request.args.get("fhr", "024")
    fhr_int    = int(fhr_str) if fhr_str and fhr_str.isdigit() else 24

    gh_level = request.args.get("gh_level")
    gh_level_int = int(gh_level) if gh_level and str(gh_level).isdigit() else None

    # Default to an empty transparent tile if no level is specified
    if gh_level_int is None:
        empty = Image.new("RGBA", (config.TILE_SIZE, config.TILE_SIZE), (0,0,0,0))
        bio = io.BytesIO(); empty.save(bio, "PNG"); bio.seek(0)
        return send_file(bio, mimetype="image/png", max_age=30)

    interval = request.args.get("interval", default=6.0, type=float)
    label = request.args.get("label", default=1, type=int)
    lw = request.args.get("lw", default=1.0, type=float)

    # 1. Fetch Geopotential Height (GH/Z) Data
    opened = None
    var_name = "gh"
    for v in ("gh", "z"):
        try:
            ds, lon_name, lat_name = data_access.get_dataset(
                model_date, model_time, fhr_int,
                var=v, level=gh_level_int
            )
            opened = (ds, lon_name, lat_name)
            var_name = v
            break
        except Exception as e:
            print(f"Error accessing contour data ({v}): {e}")
            pass

    if opened is None:
        empty = Image.new("RGBA", (config.TILE_SIZE, config.TILE_SIZE), (0,0,0,0))
        bio = io.BytesIO(); empty.save(bio, "PNG"); bio.seek(0)
        return send_file(bio, mimetype="image/png", max_age=30)

    ds, lon_name, lat_name = opened
    da = ds[var_name]

    # 2. Prepare Interpolation Grid
    lon, lat = mercator.lonlat_grid_for_tile(z, x, y, config.TILE_SIZE)

    # 3. Interpolate Data
    vals = da.interp(
        {lon_name: (("y","x"), lon), lat_name: (("y","x"), lat)},
        method="linear", kwargs={"fill_value": np.nan}
    ).values

    # Convert to Geopotential Height (m) if necessary, then to decameters (dam)
    # G0 = 9.80665 # Standard gravity, if 'z' is geopotential
    # if var_name == "z": vals = vals / G0
    vals_dam = vals / 10.0

    if not np.isfinite(vals_dam).any():
        empty = Image.new("RGBA", (config.TILE_SIZE, config.TILE_SIZE), (0,0,0,0))
        bio = io.BytesIO(); empty.save(bio, "PNG"); bio.seek(0)
        return send_file(bio, mimetype="image/png", max_age=30)

    # 4. Draw Contours and Serve
    try:
        buf = visualization.draw_contours(model_date, model_time, fhr_int, gh_level_int, vals_dam, interval, label, lw)
        return send_file(buf, mimetype="image/png", max_age=30)
    except Exception as e:
        print(f"Error drawing contours: {e}")
        return (f"Contour drawing failed: {e}", 500)