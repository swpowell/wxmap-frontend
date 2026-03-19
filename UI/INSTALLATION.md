# Pre-Rendered Tile System - Installation Guide

This guide walks you through installing and testing the pre-rendered tile system.

## Overview

The system pre-renders temperature tiles at 1000 hPa for both GraphCast and GFS models, storing them locally on EBS for instant access. This dramatically reduces load times from 5-10 seconds to <100ms for students viewing surface temperature forecasts.

## Files Generated

1. **config.py** (modified) - Added pre-rendering settings
2. **tile_store.py** (new) - Tile storage management module
3. **app.py** (modified) - Added pre-rendered tile serving
4. **prerender_tiles.py** (new) - Main pre-rendering script
5. **cleanup_old_runs.py** (new) - Cleanup utility

## Installation Steps

### Step 1: Back Up Your Current Files

```bash
cd /path/to/your/app
cp UI/config.py UI/config.py.backup
cp UI/app.py UI/app.py.backup
```

### Step 2: Install New Files

```bash
# Replace config.py
cp config.py UI/config.py

# Add new tile_store module
cp tile_store.py UI/tile_store.py

# Replace app.py
cp app.py UI/app.py

# Add scripts
mkdir -p scripts
cp prerender_tiles.py scripts/
cp cleanup_old_runs.py scripts/
chmod +x scripts/*.py
```

### Step 3: Create Required Directories

```bash
# Create tile storage directory
sudo mkdir -p /mnt/data/tiles
sudo chown $USER:$USER /mnt/data/tiles

# Create separate GRIB cache for pre-rendering
sudo mkdir -p /mnt/grib_cache_prerender
sudo chown $USER:$USER /mnt/grib_cache_prerender
```

### Step 4: Verify Installation

```bash
# Test imports
python3 -c "from UI import tile_store; print('✓ tile_store imported')"
python3 -c "from UI import config; print('✓ config imported with PRERENDER_ROOT:', config.PRERENDER_ROOT)"
```

### Step 5: Restart the app
sudo systemctl restart flaskapp

## Testing

### Phase A: Test Serving Layer (Low Risk)

This tests that the app can serve pre-rendered tiles without actually generating any.

#### 1. Create a test tile manually

```bash
# Create directory structure for one test tile
mkdir -p /mnt/data/tiles/graphcast/20260107_00/temp_sfc/024/0/0

# Create a simple test PNG (you can use any 256x256 PNG image)
# Or generate one with Python:
python3 << 'EOF'
from PIL import Image
img = Image.new('RGB', (256, 256), color='red')
img.save('/mnt/data/tiles/graphcast/20260107_00/temp_sfc/024/0/0/0.png')
print('✓ Test tile created')
EOF

# Create a manifest marking it complete
python3 << 'EOF'
import json
manifest = {
    "model": "graphcast",
    "date": "20260107",
    "init": "00",
    "status": "complete",
    "fhrs_rendered": [24],
}
with open('/mnt/data/tiles/graphcast/20260107_00/manifest.json', 'w') as f:
    json.dump(manifest, f)
print('✓ Test manifest created')
EOF
```

#### 2. Test serving the tile

Start your Flask app and visit:
```
http://your-server/tiles/0/0/0.png?model=graphcast&date=20260107&init=00&fhr=024&var=t&level=1000
```

Check the response headers - you should see:
```
X-Tile-Source: prerendered
```

If you add `&force=1` to the URL, you should see:
```
X-Tile-Source: dynamic
```

✓ If this works, Phase A is complete!

### Phase B: Test Pre-Rendering (One Model, One Run)

#### 1. Run pre-render for a single recent run

```bash
# Use a date you know exists (from your UI)
cd /path/to/your/app
python scripts/prerender_tiles.py --model graphcast --date 20260107 --init 00 --workers 2
```

This should take about 20-30 minutes and produce output like:
```
======================================================================
WEATHER TILE PRE-RENDERING
======================================================================
Model:       GRAPHCAST
Run:         20260107 00Z
...
[GRAPHCAST] Rendering fhr=000h...
[GRAPHCAST] Completed fhr=000h: 5461 tiles in 320.5s (17.0 tiles/s)
...
======================================================================
PRE-RENDERING COMPLETE
======================================================================
Model:           GRAPHCAST
Run:             20260107 00Z
Tiles rendered:  49,149
Forecast hours:  9/9
Total size:      2847.3 MB
Total time:      24.2 minutes
Throughput:      33.8 tiles/s
Location:        /mnt/data/tiles/graphcast/20260107_00
======================================================================

✓ Success! Tiles are now available for serving.
```

#### 2. Verify tiles are being served

Visit your app and select:
- Model: GraphCast
- Date: 20260107
- Init: 00Z
- Variable: t
- Level: 1000 hPa

The map should load instantly! Check browser dev tools network tab - tiles should have:
- `X-Tile-Source: prerendered`
- Cache time: 86400 seconds (24 hours)

### Phase C: Production Deployment

#### 1. Pre-render both models

```bash
# Get the latest run dates from your UI or S3
# For GraphCast:
python scripts/prerender_tiles.py --model graphcast --date YYYYMMDD --init HH --workers 2

# For GFS:
python scripts/prerender_tiles.py --model gfs --date YYYYMMDD --init HH --workers 2
```

#### 2. Set up automation (optional)

Create a cron job to automatically pre-render new runs:

```bash
# Edit crontab
crontab -e

# Add these lines (adjust times based on when new runs arrive):
# Run every 6 hours at 30 minutes past the hour
30 */6 * * * /path/to/your/app/scripts/auto_prerender.sh >> /var/log/prerender.log 2>&1
```

Create `scripts/auto_prerender.sh`:
```bash
#!/bin/bash
# Auto pre-render script

cd /path/to/your/app

# Activate virtual environment if needed
source venv/bin/activate

# Get latest dates (you'll need to implement this based on your S3 structure)
LATEST_GC_DATE=$(date -d "today" +%Y%m%d)
LATEST_GC_INIT="00"

LATEST_GFS_DATE=$(date -d "today" +%Y%m%d)
LATEST_GFS_INIT="00"

# Pre-render GraphCast
echo "Pre-rendering GraphCast ${LATEST_GC_DATE} ${LATEST_GC_INIT}Z..."
python scripts/prerender_tiles.py --model graphcast --date $LATEST_GC_DATE --init $LATEST_GC_INIT --workers 2

# Pre-render GFS
echo "Pre-rendering GFS ${LATEST_GFS_DATE} ${LATEST_GFS_INIT}Z..."
python scripts/prerender_tiles.py --model gfs --date $LATEST_GFS_DATE --init $LATEST_GFS_INIT --workers 2

echo "Pre-rendering complete!"
```

#### 3. Set up cleanup automation

```bash
# Run cleanup daily at 3 AM
0 3 * * * /path/to/your/venv/bin/python /path/to/your/app/scripts/cleanup_old_runs.py --all --keep 1 --clean-temp
```

## Monitoring

### Check storage usage

```bash
# See stats for all models
python scripts/cleanup_old_runs.py --all --keep 1 --dry-run
```

### Check what's being served

```bash
# Monitor your Flask app logs for:
# "X-Tile-Source: prerendered" vs "X-Tile-Source: dynamic"

# In production, you might want to add metrics:
tail -f /var/log/flask_app.log | grep "X-Tile-Source"
```

## Troubleshooting

### Problem: Tiles are still slow

**Check:**
1. Are tiles actually pre-rendered? `ls /mnt/data/tiles/graphcast/*/temp_sfc/`
2. Is the manifest status "complete"? `cat /mnt/data/tiles/graphcast/*/manifest.json | grep status`
3. Are you requesting the right var/level? (must be `var=t&level=1000`)
4. Check browser network tab for `X-Tile-Source` header

### Problem: Pre-rendering fails

**Check:**
1. Do you have enough disk space? `df -h /mnt/data`
2. Is the GRIB cache directory writable? `ls -ld /mnt/grib_cache_prerender`
3. Does the run exist in S3? Test with your UI first
4. Check the error message - often it's a missing forecast hour

### Problem: Out of disk space

**Solution:**
```bash
# Clean up immediately
python scripts/cleanup_old_runs.py --all --keep 1 --clean-temp

# Check what's using space
du -sh /mnt/data/tiles/*
du -sh /mnt/grib_cache*
```

## Performance Expectations

Based on smoke test results:

- **Per model run**: ~20-30 minutes (9 FHRs, 2 workers)
- **Both models**: ~40-60 minutes total
- **Disk space**: ~2.5-3 GB per model run (~5-6 GB total)
- **Student experience**: <100ms tile load (vs 5-10 seconds before)

## Next Steps

After successful deployment:

1. Monitor disk usage daily
2. Consider adding t2m (2-meter temperature) when variable names are confirmed
3. Consider extending to higher zoom levels if students need more detail
4. Consider adding other common variables (wind, etc.)

## Questions?

If you encounter issues not covered here, check:
1. Flask app logs
2. Prerender script output
3. Manifest files in `/mnt/data/tiles/*/*/manifest.json`