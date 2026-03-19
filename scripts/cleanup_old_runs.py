#!/usr/bin/env python3
"""
Cleanup utility for pre-rendered weather tiles.

This script:
1. Deletes old model runs (keeping N most recent)
2. Removes stale temporary directories from failed renders

Usage:
    # Clean up old runs for a specific model
    python cleanup_old_runs.py --model graphcast --keep 1
    
    # Clean up all models
    python cleanup_old_runs.py --all --keep 1
    
    # Also clean stale temp directories
    python cleanup_old_runs.py --all --keep 1 --clean-temp
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import argparse
from UI import tile_store

def main():
    parser = argparse.ArgumentParser(
        description="Clean up old pre-rendered tile runs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Keep only latest run for GraphCast
    python cleanup_old_runs.py --model graphcast --keep 1
    
    # Keep latest 2 runs for GFS
    python cleanup_old_runs.py --model gfs --keep 2
    
    # Clean all models and remove stale temp dirs
    python cleanup_old_runs.py --all --keep 1 --clean-temp
        """
    )
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--model", choices=["graphcast", "gfs"],
                      help="Model to clean up")
    group.add_argument("--all", action="store_true",
                      help="Clean up all models")
    
    parser.add_argument("--keep", type=int, default=1,
                       help="Number of runs to keep (default: 1)")
    parser.add_argument("--clean-temp", action="store_true",
                       help="Also clean stale temporary directories (>24h old)")
    parser.add_argument("--temp-age-hours", type=int, default=24,
                       help="Max age for temp directories in hours (default: 24)")
    parser.add_argument("--dry-run", action="store_true",
                       help="Show what would be deleted without actually deleting")
    
    args = parser.parse_args()
    
    print("="*60)
    print("PRE-RENDERED TILE CLEANUP")
    print("="*60)
    
    if args.dry_run:
        print("DRY RUN MODE: No files will be deleted")
        print("="*60)
    
    # Determine which models to clean
    models = ["graphcast", "gfs"] if args.all else [args.model]
    
    # Clean up old runs
    for model in models:
        print(f"\nCleaning {model.upper()} runs (keeping {args.keep} latest)...")
        
        if args.dry_run:
            # Show what would be deleted
            run_dirs = tile_store.get_run_dirs(model)
            if len(run_dirs) <= args.keep:
                print(f"  No cleanup needed (have {len(run_dirs)}, keeping {args.keep})")
            else:
                print(f"  Would delete {len(run_dirs) - args.keep} old runs:")
                for old_dir in run_dirs[args.keep:]:
                    manifest = tile_store.load_manifest(old_dir / "manifest.json")
                    size_mb = manifest.get("size_bytes", 0) / (1024**2) if manifest else 0
                    print(f"    - {old_dir.name} ({size_mb:.1f} MB)")
        else:
            # Actually delete
            tile_store.cleanup_old_runs(model, keep_n=args.keep)
    
    # Clean up stale temp directories
    if args.clean_temp:
        print(f"\nCleaning stale temp directories (>{args.temp_age_hours}h old)...")
        
        if args.dry_run:
            # Would need to implement dry-run logic in tile_store.cleanup_stale_temp_dirs
            print("  (Dry-run for temp cleanup not yet implemented)")
        else:
            tile_store.cleanup_stale_temp_dirs(max_age_hours=args.temp_age_hours)
    
    # Show current state
    print("\n" + "="*60)
    print("CURRENT STATE")
    print("="*60)
    
    for model in models:
        stats = tile_store.get_stats(model)
        print(f"\n{model.upper()}:")
        print(f"  Runs:        {stats['run_count']}")
        print(f"  Total tiles: {stats['total_tiles']:,}")
        print(f"  Total size:  {stats['total_size_bytes'] / (1024**2):.1f} MB")
        
        if stats['runs']:
            print(f"  Latest run:  {stats['runs'][0]['run_id']} "
                  f"({stats['runs'][0]['status']})")
    
    print("\n✓ Cleanup complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())