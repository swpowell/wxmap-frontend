# tile_store.py
"""
Tile storage management for pre-rendered weather tiles.

This module handles:
- Path generation for pre-rendered tiles
- Manifest creation and validation
- Run directory management
- Cleanup of old runs
"""

import os
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List

from .config import (
    PRERENDER_ROOT,
    PRERENDER_PRODUCT,
    PRERENDER_VAR,
    PRERENDER_LEVEL,
    PRERENDER_ZMAX,
    PRERENDER_FHRS,
    PRERENDER_TEMP_VMIN,
    PRERENDER_TEMP_VMAX,
    PRERENDER_TEMP_CMAP,
    TILE_SIZE,
)


def get_run_id(date: str, init: str) -> str:
    """Creates a run identifier from date and init time."""
    return f"{date}_{init}"


def get_tile_path(model: str, date: str, init: str, fhr: int, z: int, x: int, y: int) -> Path:
    """Returns the path where a pre-rendered tile should be stored."""
    run_id = get_run_id(date, init)
    return (Path(PRERENDER_ROOT) / model / run_id / PRERENDER_PRODUCT / 
            f"{fhr:03d}" / str(z) / str(x) / f"{y}.png")


def get_manifest_path(model: str, date: str, init: str) -> Path:
    """Returns the path to the manifest file for a model run."""
    run_id = get_run_id(date, init)
    return Path(PRERENDER_ROOT) / model / run_id / "manifest.json"


def get_run_dir(model: str, date: str, init: str) -> Path:
    """Returns the directory path for a model run."""
    run_id = get_run_id(date, init)
    return Path(PRERENDER_ROOT) / model / run_id


def check_prerendered_tile(model: str, date: str, init: str, fhr: int, 
                           z: int, x: int, y: int) -> Optional[Path]:
    """
    Checks if a pre-rendered tile exists AND the run is complete.
    Returns the path if valid, None otherwise.
    
    This function enforces manifest-gated serving: tiles are only served
    if the manifest exists and has status='complete'.
    """
    # First check manifest
    manifest_path = get_manifest_path(model, date, init)
    if not manifest_path.exists():
        return None
    
    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
        
        # Only serve tiles from completed runs
        if manifest.get("status") != "complete":
            return None
    except Exception as e:
        print(f"Warning: Could not read manifest at {manifest_path}: {e}")
        return None
    
    # Check tile exists
    tile_path = get_tile_path(model, date, init, fhr, z, x, y)
    if tile_path.exists():
        return tile_path
    
    return None


def create_manifest(model: str, date: str, init: str, 
                   status: str = "in_progress") -> Dict:
    """Creates a manifest dictionary for a model run."""
    return {
        # Basic identification
        "model": model,
        "date": date,
        "init": init,
        "run_id": get_run_id(date, init),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        
        # Product configuration
        "product": PRERENDER_PRODUCT,
        "var_requested": PRERENDER_VAR,
        "var_resolved": None,  # Will be filled in by prerender script
        "level_requested": PRERENDER_LEVEL,
        
        # Rendering parameters
        "fhrs_rendered": [],
        "zmax": PRERENDER_ZMAX,
        "tile_size": TILE_SIZE,
        "projection": "webmercator",
        "renderer": "kdtree_nearest",
        
        # Color scale
        "colormap": PRERENDER_TEMP_CMAP,
        "scale_type": "fixed",
        "vmin": PRERENDER_TEMP_VMIN,
        "vmax": PRERENDER_TEMP_VMAX,
        
        # Status and metrics
        "status": status,  # "in_progress", "complete", "failed"
        "tile_count": 0,
        "size_bytes": 0,
        
        # Schema version for future compatibility
        "tile_schema_version": "1.0",
    }


def save_manifest(manifest_path: Path, manifest: Dict):
    """Saves a manifest to disk."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)
    print(f"Manifest saved: {manifest_path}")


def load_manifest(manifest_path: Path) -> Optional[Dict]:
    """Loads a manifest from disk. Returns None if not found or invalid."""
    if not manifest_path.exists():
        return None
    
    try:
        with open(manifest_path) as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading manifest from {manifest_path}: {e}")
        return None


def get_run_dirs(model: str) -> List[Path]:
    """Returns all VALID run directories for a model, sorted newest first.
    
    Only includes runs with complete manifests to avoid cleanup issues
    with empty or failed runs.
    """
    model_root = Path(PRERENDER_ROOT) / model
    if not model_root.exists():
        return []
    
    # Get all directories that look like YYYYMMDD_HH (not temp dirs)
    run_dirs = []
    for d in model_root.iterdir():
        if d.is_dir() and not d.name.startswith('_'):
            # Only include runs with complete manifests
            manifest_path = d / "manifest.json"
            if manifest_path.exists():
                manifest = load_manifest(manifest_path)
                if manifest and manifest.get("status") == "complete":
                    run_dirs.append(d)
            # Optionally warn about incomplete runs
            elif not manifest_path.exists():
                print(f"Warning: Run directory exists without manifest: {d}")
    
    # Sort by directory name (which is date_init), newest first
    return sorted(run_dirs, key=lambda p: p.name, reverse=True)


def cleanup_old_runs(model: str, keep_n: int = 1):
    """
    Deletes old pre-rendered runs, keeping only the N most recent.
    
    This should be called AFTER a new run is successfully published,
    never before (to maintain availability).
    """
    run_dirs = get_run_dirs(model)
    
    if len(run_dirs) <= keep_n:
        print(f"No cleanup needed for {model} (have {len(run_dirs)}, keeping {keep_n})")
        return
    
    import shutil
    
    for old_dir in run_dirs[keep_n:]:
        try:
            print(f"Deleting old run: {old_dir}")
            shutil.rmtree(old_dir)
        except Exception as e:
            print(f"Error deleting {old_dir}: {e}")


def cleanup_stale_temp_dirs(max_age_hours: int = 24):
    """
    Deletes temporary directories older than max_age_hours.
    
    This is a safety mechanism to clean up failed renders.
    """
    import time
    import shutil
    
    root = Path(PRERENDER_ROOT)
    if not root.exists():
        return
    
    now = time.time()
    cutoff = now - (max_age_hours * 3600)
    
    # Look for _tmp_* and _old_* directories
    for model_dir in root.iterdir():
        if not model_dir.is_dir():
            continue
        
        for temp_dir in model_dir.iterdir():
            if not temp_dir.is_dir():
                continue
            
            if temp_dir.name.startswith(('_tmp_', '_old_')):
                try:
                    mtime = temp_dir.stat().st_mtime
                    if mtime < cutoff:
                        print(f"Deleting stale temp dir: {temp_dir}")
                        shutil.rmtree(temp_dir)
                except Exception as e:
                    print(f"Error deleting stale temp dir {temp_dir}: {e}")


def get_stats(model: str) -> Dict:
    """Returns statistics about pre-rendered tiles for a model."""
    run_dirs = get_run_dirs(model)
    
    stats = {
        "model": model,
        "run_count": len(run_dirs),
        "runs": [],
        "total_size_bytes": 0,
        "total_tiles": 0,
    }
    
    for run_dir in run_dirs:
        manifest_path = run_dir / "manifest.json"
        manifest = load_manifest(manifest_path)
        
        if manifest:
            stats["runs"].append({
                "run_id": manifest.get("run_id"),
                "status": manifest.get("status"),
                "tile_count": manifest.get("tile_count", 0),
                "size_bytes": manifest.get("size_bytes", 0),
                "generated_at": manifest.get("generated_at"),
            })
            stats["total_tiles"] += manifest.get("tile_count", 0)
            stats["total_size_bytes"] += manifest.get("size_bytes", 0)
    
    return stats