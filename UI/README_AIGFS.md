# AIGFS Integration Changes

This package adds NOAA AIGFS model support to your weather visualization application.

## Files Changed/Added

### New Files
- **`UI/model_aigfs.py`** - AIGFS-specific data access logic (new model handler)

### Modified Files
- **`UI/config.py`** - Added AIGFS paths and S3 configuration
- **`UI/data_access.py`** - Added AIGFS to the model dispatcher
- **`templates/map.html`** - Added AIGFS to model selector, extended FHR to 384h

### Unchanged Files (included for completeness)
- `UI/app.py`
- `UI/mercator.py`
- `UI/model_gfs.py`
- `UI/model_graphcast.py`
- `UI/tile_store.py`
- `UI/visualization.py`

## Installation

1. **Backup your current files:**
   ```bash
   cp -r UI UI.backup
   cp templates/map.html templates/map.html.backup
   ```

2. **Copy the new/updated files:**
   ```bash
   cp UI/model_aigfs.py /path/to/your/app/UI/
   cp UI/config.py /path/to/your/app/UI/
   cp UI/data_access.py /path/to/your/app/UI/
   cp templates/map.html /path/to/your/app/templates/
   ```

3. **Restart your Flask app:**
   ```bash
   sudo systemctl restart flaskapp
   ```

## Configuration Details

### S3 Bucket Access

The AIGFS data is in your private S3 bucket `s3://noaa-nps-aigfs`. The config uses `anon=False` for authentication:

```python
S3_OPTS_AIGFS = {
    "anon": False,  # Requires AWS credentials
    "client_kwargs": {"region_name": "us-east-1"},
    "config_kwargs": {"max_pool_connections": 64},
}
```

**Important:** Make sure your server has AWS credentials configured via:
- Environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`)
- `~/.aws/credentials` file
- IAM instance role (if running on EC2)

### GRIB Path Structure

The AIGFS GRIB files are accessed at:
```
s3://noaa-nps-aigfs/aigfs/aigfs.YYYYMMDD/HH/model/atmos/grib2/aigfs.tHHz.pres.fHHH.grib2
```

For example:
- `s3://noaa-nps-aigfs/aigfs/aigfs.20260113/00/model/atmos/grib2/aigfs.t00z.pres.f024.grib2`

### Forecast Hours

AIGFS supports forecast hours 0-384 in 6-hour increments (same as GFS).

## Key Differences from GraphCast/GFS

| Feature | GraphCast | GFS | AIGFS |
|---------|-----------|-----|-------|
| Max FHR | 240h | 384h | 384h |
| S3 Access | Public (anon) | Public (anon) | Private (auth) |
| Bucket | noaa-nws-graphcastgfs-pds | noaa-gfs-bdp-pds | noaa-nps-aigfs |

## Testing

1. **Verify the UI loads AIGFS option:**
   - Open your map page
   - Check the Model dropdown includes "AIGFS"

2. **Test data access:**
   ```bash
   # Quick test to see if GRIB files are accessible
   aws s3 ls s3://noaa-nps-aigfs/aigfs/ --recursive | head -20
   ```

3. **Test a tile request:**
   ```
   http://your-server/tiles/0/0/0.png?model=aigfs&date=YYYYMMDD&init=00&fhr=024&var=t&level=1000
   ```

## Variable Names

The current implementation assumes AIGFS uses the same GRIB variable names as GraphCast:
- `t` - Temperature
- `u` - U-component of wind
- `v` - V-component of wind
- `gh` - Geopotential height
- `z` - Geopotential (alternative name)

If AIGFS uses different variable names, update the `build_grib_filter()` function in `model_aigfs.py`.

## Troubleshooting

### "Access Denied" errors
- Verify AWS credentials are configured
- Check the bucket name is correct
- Ensure the IAM policy allows s3:GetObject on the bucket

### "Variable not found" errors
- The GRIB file structure may be different
- Check actual variable names with:
  ```python
  import xarray as xr
  ds = xr.open_dataset('path/to/file.grib2', engine='cfgrib')
  print(ds.data_vars)
  ```

### No levels showing
- The pressure level dimension name may differ
- Check the coordinate names in the GRIB files

## Pre-rendering Support

To enable pre-rendered tiles for AIGFS, the `PRERENDER_KEEP_RUNS` config already includes:
```python
PRERENDER_KEEP_RUNS = {
    "graphcast": 1,
    "gfs": 1,
    "aigfs": 1,  # Already added
}
```

Run the prerender script with:
```bash
python scripts/prerender_tiles.py --model aigfs --date YYYYMMDD --init HH --workers 2
```
