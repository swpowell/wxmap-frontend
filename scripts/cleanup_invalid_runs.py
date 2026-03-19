#!/usr/bin/env python3
"""
Clean up invalid or incomplete pre-rendered tile directories.

This removes:
1. Directories without manifests
2. Directories with failed/incomplete manifests
3. Empty directories

Usage:
    python cleanup_invalid_runs.py --model graphcast
    python cleanup_invalid_runs.py --all
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import argparse
from pathlib import Path
import shutil
from UI import tile_store, config

def find_invalid_runs(model: str):
    """Find all invalid run directories for a model."""
    model_root = Path(config.PRERENDER_ROOT) / model
    if not model_root.exists():
        return []
    
    invalid = []
    
    for d in model_root.iterdir():
        if not d.is_dir() or d.name.startswith('_'):
            continue
        
        manifest_path = d / "manifest.json"
        
        # No manifest
        if not manifest_path.exists():
            invalid.append((d, "no_manifest"))
            continue
        
        # Load manifest
        manifest = tile_store.load_manifest(manifest_path)
        
        # Failed to load
        if not manifest:
            invalid.append((d, "corrupt_manifest"))
            continue
        
        # Incomplete or failed
        status = manifest.get("status")
        if status != "complete":
            invalid.append((d, f"status_{status}"))
            continue
        
        # Empty (no tiles)
        tile_count = manifest.get("tile_count", 0)
        if tile_count == 0:
            invalid.append((d, "no_tiles"))
    
    return invalid

def main():
    parser = argparse.ArgumentParser(
        description="Clean up invalid pre-rendered tile directories"
    )
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--model", choices=["graphcast", "gfs"],
                      help="Model to clean")
    group.add_argument("--all", action="store_true",
                      help="Clean all models")
    
    parser.add_argument("--dry-run", action="store_true",
                       help="Show what would be deleted without deleting")
    
    args = parser.parse_args()
    
    print("="*60)
    print("INVALID RUN CLEANUP")
    print("="*60)
    
    if args.dry_run:
        print("DRY RUN MODE: No files will be deleted")
        print("="*60)
    
    # Determine which models to clean
    models = ["graphcast", "gfs"] if args.all else [args.model]
    
    total_deleted = 0
    total_size = 0
    
    for model in models:
        print(f"\n{model.upper()}:")
        
        invalid = find_invalid_runs(model)
        
        if not invalid:
            print("  ✓ No invalid runs found")
            continue
        
        print(f"  Found {len(invalid)} invalid run(s):")
        
        for run_dir, reason in invalid:
            # Calculate size
            try:
                size = sum(f.stat().st_size for f in run_dir.rglob('*') if f.is_file())
                size_mb = size / (1024**2)
            except Exception:
                size_mb = 0
            
            print(f"    - {run_dir.name} ({reason}, {size_mb:.1f} MB)")
            
            if not args.dry_run:
                try:
                    shutil.rmtree(run_dir)
                    print(f"      ✓ Deleted")
                    total_deleted += 1
                    total_size += size
                except Exception as e:
                    print(f"      ✗ Error: {e}")
    
    print("\n" + "="*60)
    if args.dry_run:
        print(f"Would delete {total_deleted} invalid run(s)")
    else:
        print(f"Deleted {total_deleted} invalid run(s)")
        print(f"Freed {total_size / (1024**2):.1f} MB")
    print("="*60)

if __name__ == "__main__":
    sys.exit(main())