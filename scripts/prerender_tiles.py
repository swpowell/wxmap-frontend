#!/usr/bin/env python3
"""
Pre-render weather tiles for GraphCast and GFS models.

This script generates pre-rendered tiles for temperature at 1000 hPa,
significantly improving load times for students viewing surface temperature forecasts.

Usage:
    python prerender_tiles.py --model graphcast --date 20260107 --init 00
    python prerender_tiles.py --model gfs --date 20260107 --init 12 --workers 2

Features:
    - Parallel rendering by forecast hour (default 2 workers)
    - Atomic publish with manifest-gated serving
    - Automatic cleanup of old runs
    - Fixed color scale for consistent visualization
    - Separate GRIB cache to avoid contention with live app
"""

import sys
import os

# CRITICAL: Set separate GRIB cache BEFORE importing UI modules
os.environ['GRIB_CACHE_DIR'] = '/mnt/grib_cache_prerender'

# Now safe to import
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import argparse
from pathlib import Path
from multiprocessing import Pool
from datetime import datetime
import shutil
import traceback

from UI import data_access, mercator, visualization, tile_store, config

def render_tile_to_file(model, date, init, fhr, var, level, z, x, y, 
                        ds, lon_name, lat_name, vmin, vmax, cmap, output_path):
    """
    Renders a single tile and saves it to disk.
    
    This function is called thousands of times per forecast hour,
    so it's optimized to reuse the dataset and KDTree cache.
    """
    try:
        # Get lon/lat grid for this tile
        lon, lat = mercator.lonlat_grid_for_tile(z, x, y, config.TILE_SIZE)
        
        # Interpolate data using cached KDTree
        da = ds[var] if var in ds.data_vars else next(iter(ds.data_vars.values()))
        samp = mercator.fast_nearest_neighbor_interp(da, lon_name, lat_name, lon, lat)
        
        # Colorize with fixed scale
        img = visualization.colorize(samp, vmin=vmin, vmax=vmax, cmap_name=cmap)
        
        # Save to disk
        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(output_path), "PNG", compress_level=1)
        
        return True
    except Exception as e:
        print(f"[ERROR] Failed to render tile {z}/{x}/{y}: {e}")
        return False


def render_forecast_hour(args):
    """
    Worker function: renders all tiles for one forecast hour.
    
    This is called by multiprocessing.Pool, so each worker gets its own
    Python process with its own KDTree cache.
    """
    model, date, init, fhr, temp_dir, zmax = args
    
    print(f"[{model.upper()}] Rendering fhr={fhr:03d}h...")
    start_time = datetime.now()
    
    try:
        # Open dataset once for this forecast hour
        ds, lon_name, lat_name = data_access.get_model_dataset(
            model, date, init, fhr, 
            config.PRERENDER_VAR, 
            config.PRERENDER_LEVEL
        )
        
        # Determine actual variable name (might not be exactly 't')
        actual_var = config.PRERENDER_VAR if config.PRERENDER_VAR in ds.data_vars else list(ds.data_vars.keys())[0]
        
        # Use fixed color scale from config
        vmin = config.PRERENDER_TEMP_VMIN
        vmax = config.PRERENDER_TEMP_VMAX
        cmap = config.PRERENDER_TEMP_CMAP
        
        tile_count = 0
        failed_tiles = 0
        
        # Generate all tiles z=0 through zmax
        for z in range(0, zmax + 1):
            for x in range(2**z):
                for y in range(2**z):
                    output_path = (temp_dir / config.PRERENDER_PRODUCT / 
                                  f"{fhr:03d}" / str(z) / str(x) / f"{y}.png")
                    
                    if render_tile_to_file(model, date, init, fhr, 
                                          actual_var, config.PRERENDER_LEVEL,
                                          z, x, y, ds, lon_name, lat_name, 
                                          vmin, vmax, cmap, output_path):
                        tile_count += 1
                    else:
                        failed_tiles += 1
        
        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"[{model.upper()}] Completed fhr={fhr:03d}h: "
              f"{tile_count} tiles in {elapsed:.1f}s "
              f"({tile_count/elapsed:.1f} tiles/s)")
        
        if failed_tiles > 0:
            print(f"[{model.upper()}] WARNING: {failed_tiles} tiles failed for fhr={fhr:03d}h")
        
        return fhr, tile_count, actual_var
        
    except Exception as e:
        print(f"[{model.upper()}] ERROR rendering fhr={fhr:03d}h: {e}")
        traceback.print_exc()
        return fhr, 0, None


def check_disk_space(required_gb=10):
    """Checks if there's enough disk space before starting."""
    try:
        import shutil
        stat = shutil.disk_usage(config.PRERENDER_ROOT)
        free_gb = stat.free / (1024**3)
        
        if free_gb < required_gb:
            print(f"WARNING: Low disk space! Free: {free_gb:.1f} GB, Required: {required_gb} GB")
            return False
        
        print(f"Disk space OK: {free_gb:.1f} GB available")
        return True
    except Exception as e:
        print(f"Warning: Could not check disk space: {e}")
        return True  # Proceed anyway


def main():
    parser = argparse.ArgumentParser(
        description="Pre-render weather tiles for faster student access",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Render latest GraphCast run
    python prerender_tiles.py --model graphcast --date 20260107 --init 00
    
    # Render GFS with custom worker count
    python prerender_tiles.py --model gfs --date 20260107 --init 12 --workers 2
    
    # Skip cleanup (for testing)
    python prerender_tiles.py --model graphcast --date 20260107 --init 00 --no-cleanup
        """
    )
    parser.add_argument("--model", required=True, choices=["graphcast", "gfs"],
                       help="Model to pre-render")
    parser.add_argument("--date", required=True, 
                       help="Model date (YYYYMMDD)")
    parser.add_argument("--init", required=True, 
                       help="Init time (00, 06, 12, 18)")
    parser.add_argument("--workers", type=int, default=2, 
                       help="Number of parallel workers (default: 2)")
    parser.add_argument("--zmax", type=int, default=None,
                       help="Maximum zoom level (overrides config, useful for testing)")
    parser.add_argument("--no-cleanup", action="store_true",
                       help="Skip cleanup of old runs (for testing)")
    args = parser.parse_args()
    
    # Use command-line zmax if provided, otherwise use config
    zmax = args.zmax if args.zmax is not None else config.PRERENDER_ZMAX
    
    print("="*70)
    print("WEATHER TILE PRE-RENDERING")
    print("="*70)
    print(f"Model:       {args.model.upper()}")
    print(f"Run:         {args.date} {args.init}Z")
    print(f"Product:     {config.PRERENDER_PRODUCT}")
    print(f"Variable:    {config.PRERENDER_VAR} @ {config.PRERENDER_LEVEL} hPa")
    print(f"Zoom:        0-{zmax}" + (" (override)" if args.zmax is not None else ""))
    print(f"Fhrs:        {config.PRERENDER_FHRS}")
    print(f"Workers:     {args.workers}")
    print(f"GRIB Cache:  {os.environ.get('GRIB_CACHE_DIR')}")
    print("="*70)
    
    # Check disk space
    if not check_disk_space(required_gb=10):
        print("\nERROR: Insufficient disk space. Aborting.")
        return 1
    
    # Create temp directory
    run_id = tile_store.get_run_id(args.date, args.init)
    temp_dir = Path(config.PRERENDER_ROOT) / f"_tmp_{args.model}_{run_id}"
    
    # Clean up any existing temp directory
    if temp_dir.exists():
        print(f"\nRemoving existing temp directory: {temp_dir}")
        shutil.rmtree(temp_dir)
    
    temp_dir.mkdir(parents=True, exist_ok=True)
    print(f"Temp directory: {temp_dir}")
    
    # Create in-progress manifest
    manifest = tile_store.create_manifest(args.model, args.date, args.init, 
                                         status="in_progress")
    manifest["zmax"] = zmax  # Update with actual zmax used
    tile_store.save_manifest(temp_dir / "manifest.json", manifest)
    
    # Render forecast hours in parallel
    fhr_args = [(args.model, args.date, args.init, fhr, temp_dir, zmax) 
                for fhr in config.PRERENDER_FHRS]
    
    print(f"\nStarting parallel rendering with {args.workers} workers...")
    overall_start = datetime.now()
    
    with Pool(processes=args.workers) as pool:
        results = pool.map(render_forecast_hour, fhr_args)
    
    overall_elapsed = (datetime.now() - overall_start).total_seconds()
    
    # Process results
    completed_fhrs = []
    total_tiles = 0
    resolved_var = None
    
    for fhr, count, var_used in results:
        if count > 0:
            completed_fhrs.append(fhr)
            total_tiles += count
            if var_used and not resolved_var:
                resolved_var = var_used
    
    # Update manifest to complete
    manifest["status"] = "complete"
    manifest["fhrs_rendered"] = sorted(completed_fhrs)
    manifest["tile_count"] = total_tiles
    manifest["var_resolved"] = resolved_var
    
    # Calculate total size
    try:
        total_size = sum(f.stat().st_size for f in temp_dir.rglob("*.png"))
        manifest["size_bytes"] = total_size
    except Exception as e:
        print(f"Warning: Could not calculate total size: {e}")
        total_size = 0
    
    tile_store.save_manifest(temp_dir / "manifest.json", manifest)
    
    # Atomic move to final location
    final_dir = tile_store.get_run_dir(args.model, args.date, args.init)
    
    # Safety: back up old run if it exists
    if final_dir.exists():
        backup_dir = Path(config.PRERENDER_ROOT) / args.model / f"_old_{run_id}_{int(datetime.now().timestamp())}"
        print(f"\nBacking up existing run to: {backup_dir}")
        shutil.move(str(final_dir), str(backup_dir))
    
    # Move temp to final
    print(f"Publishing to: {final_dir}")
    shutil.move(str(temp_dir), str(final_dir))
    
    # Clean up backup (we already have the new run in place)
    if 'backup_dir' in locals() and backup_dir.exists():
        shutil.rmtree(backup_dir)
    
    # Clean up old runs (unless disabled)
    if not args.no_cleanup:
        print(f"\nCleaning up old runs (keeping latest {config.PRERENDER_KEEP_RUNS.get(args.model, 1)})...")
        tile_store.cleanup_old_runs(args.model, 
                                    keep_n=config.PRERENDER_KEEP_RUNS.get(args.model, 1))
    
    # Print summary
    print("\n" + "="*70)
    print("PRE-RENDERING COMPLETE")
    print("="*70)
    print(f"Model:           {args.model.upper()}")
    print(f"Run:             {args.date} {args.init}Z")
    print(f"Tiles rendered:  {total_tiles:,}")
    print(f"Forecast hours:  {len(completed_fhrs)}/{len(config.PRERENDER_FHRS)}")
    print(f"Total size:      {total_size / (1024**2):.1f} MB")
    print(f"Total time:      {overall_elapsed / 60:.1f} minutes")
    print(f"Throughput:      {total_tiles / overall_elapsed:.1f} tiles/s")
    print(f"Location:        {final_dir}")
    print("="*70)
    
    if len(completed_fhrs) < len(config.PRERENDER_FHRS):
        print("\nWARNING: Some forecast hours failed to render!")
        missing = set(config.PRERENDER_FHRS) - set(completed_fhrs)
        print(f"Missing: {sorted(missing)}")
        return 1
    
    print("\n✓ Success! Tiles are now available for serving.")
    return 0


if __name__ == "__main__":
    sys.exit(main())