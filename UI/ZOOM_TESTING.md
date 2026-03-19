# Zoom Level Testing Guide

This guide helps you determine the optimal zoom level for different variables.

## Quick Answer: How to Test Different Zoom Levels

```bash
# Test with zoom 0-4 (for quick testing)
python scripts/prerender_tiles.py --model graphcast --date 20260108 --init 00 --zmax 4

# Test with zoom 0-5
python scripts/prerender_tiles.py --model graphcast --date 20260108 --init 00 --zmax 5

# Use default zoom from config (currently 6)
python scripts/prerender_tiles.py --model graphcast --date 20260108 --init 00
```

## Zoom Level Comparison

### Storage & Performance

| Zoom | Tiles/FHR | Total Tiles (9 FHRs) | Storage/Run | Render Time | Coverage |
|------|-----------|---------------------|-------------|-------------|----------|
| 0-3  | 85        | 765                 | ~40 MB      | ~1 min      | Continental |
| 0-4  | 341       | 3,069               | ~150 MB     | ~3 min      | Regional |
| 0-5  | 1,365     | 12,285              | ~600 MB     | ~12 min     | State-level |
| 0-6  | 5,461     | 49,149              | ~2.5 GB     | ~24 min     | County-level |
| 0-7  | 21,845    | 196,605             | ~10 GB      | ~90 min     | City-level |
| 0-8  | 87,381    | 786,429             | ~40 GB      | ~6 hours    | Neighborhood |

*Times assume 2 workers, storage assumes ~50 KB/tile average*

### Visual Coverage at 0.25° Resolution

**Zoom 4**: Each tile ~320 km
- Good for: Continental-scale patterns
- Example: Viewing entire US at once

**Zoom 5**: Each tile ~160 km  
- Good for: Multi-state regions
- Example: Great Plains, Pacific Northwest

**Zoom 6**: Each tile ~80 km
- Good for: State/regional weather
- Example: California Central Valley
- **Recommended minimum for student forecasting**

**Zoom 7**: Each tile ~40 km
- Good for: Metropolitan areas
- Example: San Francisco Bay Area
- Useful if students focus on specific cities

**Zoom 8**: Each tile ~20 km
- Good for: City-level detail
- Probably overkill for 0.25° data

## Recommended Strategy for Multiple Variables

### Option 1: Tiered Approach
Pre-render different zoom levels for different variables:

```bash
# Critical variables (temperature, wind): zoom 0-6
python scripts/prerender_tiles.py --model graphcast --date 20260108 --init 00 --zmax 6
# Storage: ~2.5 GB per run

# Secondary variables (humidity, pressure): zoom 0-5
# (when you add more variables)
# Storage: ~600 MB per run

# Tertiary variables (rare views): zoom 0-4
# Storage: ~150 MB per run
```

**Total for 1 model, 3 variables**: ~3.3 GB per run

### Option 2: All Variables at Moderate Zoom
Pre-render all variables at zoom 0-5:

```bash
# Each variable at zoom 0-5
# Storage: ~600 MB per variable per run
# For 5 variables: ~3 GB per run
```

### Option 3: Mix of Models & Variables
Balance between models and variables:

```bash
# GraphCast: Temperature + Wind at zoom 0-6
# GFS: Temperature only at zoom 0-5
# Storage: GraphCast ~5 GB, GFS ~600 MB = ~5.6 GB total
```

## Testing Workflow

### Step 1: Test One Variable at Different Zooms

```bash
# Quick test at zoom 4
python scripts/prerender_tiles.py --model graphcast --date 20260108 --init 00 --zmax 4 --no-cleanup

# Check in your UI - can students see enough detail?
# If yes, use zoom 4 for this variable!
# If no, try zoom 5

# Test zoom 5
python scripts/prerender_tiles.py --model graphcast --date 20260108 --init 00 --zmax 5 --no-cleanup

# Compare in UI
```

### Step 2: Document Your Findings

Create a simple table:

| Variable | Use Case | Min Zoom | Storage | Notes |
|----------|----------|----------|---------|-------|
| temp_sfc | Daily forecasts | 5 | 600 MB | Students zoom to state-level |
| wind_500 | Upper-level | 4 | 150 MB | Continental patterns OK |
| precip | Fronts | 6 | 2.5 GB | Need county-level detail |

### Step 3: Update Config for Production

Once you know what works, update `config.py`:

```python
# Variable-specific zoom levels
PRERENDER_CONFIGS = {
    "temp_sfc": {"zmax": 5, "var": "t", "level": 1000},
    "wind_500": {"zmax": 4, "var": "u", "level": 500},
    "precip": {"zmax": 6, "var": "tp", "level": None},
}
```

*(You'd need to modify the prerender script to support multiple products)*

## Important Notes

1. **Mapbox overzoom still works**: Even if you pre-render to z=4, users can still zoom to z=10. It just scales up z=4 tiles.

2. **Test with actual student workflows**: Ask students to try the map and see if they have enough zoom.

3. **Storage adds up**: 
   - 2 models × 5 variables × zoom 0-6 = ~25 GB per run
   - 2 models × 5 variables × zoom 0-5 = ~6 GB per run
   - Big difference!

4. **Render time scales linearly**: 
   - z=0-4: ~3 min per run
   - z=0-5: ~12 min per run  
   - z=0-6: ~24 min per run
   - z=0-7: ~90 min per run

## My Recommendation for Your Use Case

Based on "students forecasting surface temperature":

**Start with zoom 0-5** for temperature:
```bash
python scripts/prerender_tiles.py --model graphcast --date 20260108 --init 00 --zmax 5
python scripts/prerender_tiles.py --model gfs --date 20260108 --init 00 --zmax 5
```

**Why?**
- ✅ Fast renders (~12 min each = 24 min total)
- ✅ Reasonable storage (~1.2 GB for both models)
- ✅ State-level detail (160 km per tile)
- ✅ Room to add 3-4 more variables within 10 GB budget
- ✅ Overzoom handles any closer views students need

**Test it and adjust**: If students complain they can't zoom in enough, bump to z=6. If they never zoom that far, you could even do z=4!

## Quick Commands for Testing

```bash
# Fast test (z=4): ~3 minutes, 150 MB
python scripts/prerender_tiles.py --model graphcast --date 20260108 --init 00 --zmax 4 --no-cleanup

# Medium test (z=5): ~12 minutes, 600 MB  
python scripts/prerender_tiles.py --model graphcast --date 20260108 --init 00 --zmax 5 --no-cleanup

# Full test (z=6): ~24 minutes, 2.5 GB
python scripts/prerender_tiles.py --model graphcast --date 20260108 --init 00 --zmax 6 --no-cleanup

# Don't forget to adjust the Mapbox source maxzoom in map.html to match!
```