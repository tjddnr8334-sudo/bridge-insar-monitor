#!/usr/bin/env python3
"""
prep_data_isce.py — ISCE2 topsStack → StaMPS Format Converter

Reads coregistered SLCs (VRT format) from ISCE2 merged/SLC/ directory,
computes Amplitude Dispersion Index, and generates all binary/text files
that StaMPS ps_load_initial_isce.m expects.

Key handling:
  - SLC data via GDAL (VRT virtual rasters, 24734x2849 CFloat32)
  - Geometry at multilook resolution (2748x949 DOUBLE) → interpolated
  - Baselines parsed from swath-level text files
  - Running statistics for memory-efficient DA computation

Usage:
    python3 prep_data_isce.py [options]
"""

import os
import sys
import glob
import argparse
import numpy as np
import xml.etree.ElementTree as ET

try:
    from osgeo import gdal
    gdal.UseExceptions()
    HAS_GDAL = True
except ImportError:
    HAS_GDAL = False

# ============================================================
# Constants
# ============================================================
SENTINEL1_WAVELENGTH = 0.05546576  # C-band, meters
DEFAULT_HEADING = -13.0  # ascending, Korea


def parse_args():
    parser = argparse.ArgumentParser(description='ISCE2 → StaMPS data converter')
    parser.add_argument('--isce-dir', default='/home/insar/insar_processing',
                        help='ISCE2 processing directory')
    parser.add_argument('--output-dir', default=None,
                        help='StaMPS output directory (default: isce-dir/stamps)')
    parser.add_argument('--da-thresh', type=float, default=0.4,
                        help='Amplitude dispersion threshold')
    parser.add_argument('--master-date', default=None,
                        help='Master date YYYYMMDD (default: temporal midpoint)')
    parser.add_argument('--min-cand', type=int, default=1500)
    parser.add_argument('--pct', type=float, default=12.0)
    parser.add_argument('--da-max', type=float, default=0.70)
    parser.add_argument('--da-months', default='3,4,5,6,7,8,9,10,11')
    parser.add_argument('--max-ps', type=int, default=0,
                        help='Max PS candidates (0 = all below threshold)')
    return parser.parse_args()


# ============================================================
# ISCE2 XML Parser
# ============================================================
def read_isce_xml(xml_path):
    """Read width, length, data_type, bands from ISCE2 XML."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    meta = {}
    for prop in root.iter('property'):
        name = prop.get('name')
        val_elem = prop.find('value')
        if name and val_elem is not None and val_elem.text:
            meta[name] = val_elem.text.strip()
    width = int(meta.get('width', 0))
    length = int(meta.get('length', 0))
    data_type = meta.get('data_type', 'FLOAT')
    bands = int(meta.get('number_bands', 1))
    return width, length, data_type, bands


def read_isce_binary(filepath, width, length, dtype, bands=1):
    """Read ISCE2 raw binary file."""
    data = np.fromfile(filepath, dtype=dtype)
    if bands == 1:
        return data.reshape(length, width)
    else:
        # BIP interleaved
        return data.reshape(length, width * bands)


# ============================================================
# GDAL SLC Reader
# ============================================================
def read_slc_gdal(vrt_path):
    """Read SLC via GDAL (handles VRT chain)."""
    ds = gdal.Open(vrt_path, gdal.GA_ReadOnly)
    if ds is None:
        raise RuntimeError(f"GDAL cannot open: {vrt_path}")
    band = ds.GetRasterBand(1)
    data = band.ReadAsArray()  # returns complex64
    width = ds.RasterXSize
    height = ds.RasterYSize
    ds = None
    return data, width, height


# ============================================================
# Geometry Interpolation
# ============================================================
def interpolate_geom_to_slc(geom_data, geom_w, geom_l, slc_w, slc_l):
    """
    Interpolate multilooked geometry to full SLC resolution.
    Uses simple bilinear zoom.
    """
    from scipy.ndimage import zoom
    zoom_y = slc_l / geom_l
    zoom_x = slc_w / geom_w
    return zoom(geom_data, (zoom_y, zoom_x), order=1)


# ============================================================
# Baseline Parser
# ============================================================
def _read_bperp_from_file(txt_file):
    """Read average Bperp from a single ISCE2 baseline text file."""
    bperp_sum = 0.0
    count = 0
    with open(txt_file, 'r') as f:
        for line in f:
            if 'Bperp' in line and 'average' in line:
                try:
                    val = float(line.split(':')[-1].strip())
                    bperp_sum += val
                    count += 1
                except ValueError:
                    pass
    return bperp_sum / count if count > 0 else None


def _find_bperp_for_date(baseline_dir, ref_date, target_date):
    """
    Find Bperp(target_date, ref=ref_date) from ISCE2 baseline files.
    Returns Bperp value or None if not found.
    Sign convention: file REF_TARGET stores Bperp(TARGET) - Bperp(REF).
    """
    # Direct match: ref_target
    for first, second, sign in [(ref_date, target_date, +1),
                                (target_date, ref_date, -1)]:
        pattern = f'{first}_{second}'
        txt_file = os.path.join(baseline_dir, pattern, f'{pattern}.txt')
        if os.path.exists(txt_file):
            val = _read_bperp_from_file(txt_file)
            if val is not None:
                return val * sign
    return None


def parse_baselines(baseline_dir, slave_dates, master_date):
    """
    Parse ISCE2 baseline text files and compute Bperp relative to StaMPS master.

    ISCE2 topsStack stores baselines as REF_SECONDARY/ where REF is the
    geometric reference date (often the first SLC). For StaMPS Star Graph,
    we need Bperp relative to the StaMPS master, which may differ from REF.

    Correct formula:
        Bperp(slave, master) = Bperp(slave, ref) - Bperp(master, ref)
    """
    # Step 1: Detect ISCE2 geometric reference date from baseline directory names
    bl_dirs = glob.glob(os.path.join(baseline_dir, '*_*'))
    ref_date = None
    if bl_dirs:
        # ISCE2 convention: all dirs are REFDATE_SECONDARY
        first_dates = [os.path.basename(d).split('_')[0] for d in bl_dirs]
        from collections import Counter
        date_counts = Counter(first_dates)
        ref_date = date_counts.most_common(1)[0][0]

    print(f"  ISCE2 geometric reference: {ref_date}")
    print(f"  StaMPS master:             {master_date}")

    if ref_date == master_date:
        # Simple case: ISCE2 ref == StaMPS master, read directly
        print(f"  → Reference matches master, reading baselines directly")
        bperps = {}
        for sdate in slave_dates:
            val = _find_bperp_for_date(baseline_dir, master_date, sdate)
            bperps[sdate] = val if val is not None else 0.0
        return bperps

    # Step 2: Collect Bperp(date, ref=ISCE2_REF) for all dates
    print(f"  → Reference ≠ master, re-referencing baselines...")
    bperp_vs_ref = {}  # date -> Bperp(date, ref=isce2_ref)

    all_needed = set(slave_dates) | {master_date}
    for date_str in all_needed:
        if date_str == ref_date:
            bperp_vs_ref[date_str] = 0.0
            continue
        val = _find_bperp_for_date(baseline_dir, ref_date, date_str)
        if val is not None:
            bperp_vs_ref[date_str] = val
        else:
            print(f"    WARNING: No baseline found for {date_str}, defaulting to 0")
            bperp_vs_ref[date_str] = 0.0

    # Step 3: Re-reference to StaMPS master
    # Bperp(slave, master) = Bperp(slave, ref) - Bperp(master, ref)
    master_bperp = bperp_vs_ref.get(master_date, 0.0)
    print(f"  Bperp(master, ref) = {master_bperp:.2f} m")

    bperps = {}
    for sdate in slave_dates:
        bperps[sdate] = bperp_vs_ref.get(sdate, 0.0) - master_bperp

    bperp_vals = list(bperps.values())
    print(f"  Re-referenced Bperp range: {min(bperp_vals):.1f} ~ {max(bperp_vals):.1f} m")

    return bperps


# ============================================================
# Heading from ISCE2
# ============================================================
def get_heading(isce_dir):
    """Get satellite heading from ISCE2 reference metadata."""
    ref_dir = os.path.join(isce_dir, 'reference')
    if not os.path.isdir(ref_dir):
        return DEFAULT_HEADING

    for xml_file in glob.glob(os.path.join(ref_dir, 'IW*.xml')):
        try:
            tree = ET.parse(xml_file)
            for elem in tree.iter():
                if 'heading' in (elem.tag or '').lower():
                    try:
                        return float(elem.text)
                    except (ValueError, TypeError):
                        pass
        except ET.ParseError:
            pass

    return DEFAULT_HEADING


# ============================================================
# Main
# ============================================================
def main():
    args = parse_args()

    if not HAS_GDAL:
        print("ERROR: GDAL (osgeo) required for reading VRT SLC files")
        print("Install: conda install -c conda-forge gdal")
        sys.exit(1)

    isce_dir = args.isce_dir
    global DA_MONTHS; DA_MONTHS = set(int(m) for m in args.da_months.split(','))
    output_dir = args.output_dir or os.path.join(isce_dir, 'stamps')
    da_thresh = args.da_thresh

    merged_dir = os.path.join(isce_dir, 'merged')
    slc_dir = os.path.join(merged_dir, 'SLC')
    geom_dir = os.path.join(merged_dir, 'geom_reference')
    baseline_dir = os.path.join(isce_dir, 'baselines')

    print("=" * 60)
    print("  ISCE2 → StaMPS Data Converter (GDAL)")
    print("=" * 60)
    print(f"  ISCE dir:    {isce_dir}")
    print(f"  Output dir:  {output_dir}")
    print(f"  DA thresh:   {da_thresh}")
    print()

    # ── 1. Discover SLC dates ──
    print("[1/8] Discovering SLC dates...")
    slc_date_dirs = sorted(glob.glob(os.path.join(slc_dir, '2*')))
    all_dates = []
    slc_files = {}  # date -> vrt path
    for d in slc_date_dirs:
        date_str = os.path.basename(d)
        vrt_file = os.path.join(d, date_str + '.slc.full.vrt')
        if os.path.exists(vrt_file):
            all_dates.append(date_str)
            slc_files[date_str] = vrt_file

    n_slc = len(all_dates)
    print(f"  Found {n_slc} SLC dates: {all_dates[0]} ~ {all_dates[-1]}")

    if n_slc < 5:
        print("ERROR: Need at least 5 SLC dates")
        sys.exit(1)

    # ── 2. Get SLC dimensions from first VRT ──
    print("\n[2/8] Reading SLC dimensions...")
    test_ds = gdal.Open(slc_files[all_dates[0]], gdal.GA_ReadOnly)
    slc_width = test_ds.RasterXSize
    slc_length = test_ds.RasterYSize
    test_ds = None
    print(f"  SLC full resolution: {slc_width} x {slc_length}")

    # ── 3. Select master date ──
    print("\n[3/8] Selecting master date...")
    if args.master_date and args.master_date in all_dates:
        master_date = args.master_date
    else:
        mid_idx = n_slc // 2
        master_date = all_dates[mid_idx]
    print(f"  Master date: {master_date} (index {all_dates.index(master_date)}/{n_slc})")

    slave_dates = [d for d in all_dates if d != master_date]
    n_slave = len(slave_dates)

    # ── 4. Read geometry (multilooked) ──
    print("\n[4/8] Reading geometry files...")

    # hgt.rdr
    hgt_xml = os.path.join(geom_dir, 'hgt.rdr.xml')
    geom_w, geom_l, hgt_dtype, _ = read_isce_xml(hgt_xml)
    np_dtype = np.float64 if hgt_dtype == 'DOUBLE' else np.float32
    print(f"  Geometry resolution: {geom_w} x {geom_l} ({hgt_dtype})")

    hgt_ml = read_isce_binary(os.path.join(geom_dir, 'hgt.rdr'), geom_w, geom_l, np_dtype)
    lat_ml = read_isce_binary(os.path.join(geom_dir, 'lat.rdr'), geom_w, geom_l, np_dtype)
    lon_ml = read_isce_binary(os.path.join(geom_dir, 'lon.rdr'), geom_w, geom_l, np_dtype)

    print(f"  Height: {np.nanmin(hgt_ml):.1f} ~ {np.nanmax(hgt_ml):.1f} m")
    print(f"  Lat: {np.nanmin(lat_ml):.4f} ~ {np.nanmax(lat_ml):.4f}")
    print(f"  Lon: {np.nanmin(lon_ml):.4f} ~ {np.nanmax(lon_ml):.4f}")

    # los.rdr (2 bands BIP: inc_angle, az_angle as float32)
    los_file = os.path.join(geom_dir, 'los.rdr')
    los_xml = os.path.join(geom_dir, 'los.rdr.xml')
    los_w, los_l, los_dtype, los_bands = read_isce_xml(los_xml)
    los_np_dtype = np.float64 if los_dtype == 'DOUBLE' else np.float32

    los_size = os.path.getsize(los_file)
    expected_2band_f32 = los_w * los_l * 4 * 2
    expected_1band_f64 = los_w * los_l * 8

    if los_size == expected_2band_f32:
        # 2 bands float32. ISCE topsStack writes los.rdr as BIP (inc, az per pixel),
        # not BSQ. Reading it as BSQ interleaves the azimuth band into the incidence
        # angle (half the values come out negative), which drives n_trial_wraps
        # negative and makes StaMPS step 2 fail. Pick whichever layout yields
        # physically valid incidence angles.
        los_raw = np.fromfile(los_file, dtype=np.float32)
        inc_bip = los_raw.reshape(los_l, los_w, 2)[:, :, 0]
        inc_bsq = los_raw[:los_w * los_l].reshape(los_l, los_w)

        def _valid_frac(a):
            return float(np.mean((a > 10.0) & (a < 70.0)))

        f_bip, f_bsq = _valid_frac(inc_bip), _valid_frac(inc_bsq)
        if f_bip >= f_bsq:
            inc_angle_ml = inc_bip
            print(f"  los.rdr: 2 bands float32 BIP (inc {f_bip*100:.1f}% valid)")
        else:
            inc_angle_ml = inc_bsq
            print(f"  los.rdr: 2 bands float32 BSQ (inc {f_bsq*100:.1f}% valid)")
    elif los_size == expected_1band_f64:
        # 1 band float64
        inc_angle_ml = np.fromfile(los_file, dtype=np.float64).reshape(los_l, los_w)
        print(f"  los.rdr: 1 band float64")
    else:
        inc_angle_ml = np.ones((los_l, los_w), dtype=np.float32) * 39.0
        print(f"  WARNING: los.rdr size mismatch, using default inc_angle=39")

    print(f"  Inc angle: {np.nanmin(inc_angle_ml):.1f} ~ {np.nanmax(inc_angle_ml):.1f} deg")

    # Interpolate geometry to SLC resolution
    print("\n  Interpolating geometry to SLC resolution...")
    try:
        from scipy.ndimage import zoom
        zoom_y = slc_length / geom_l
        zoom_x = slc_width / geom_w
        print(f"  Zoom factors: {zoom_x:.2f}x (range), {zoom_y:.2f}x (azimuth)")

        lat_full = zoom(lat_ml, (zoom_y, zoom_x), order=1)
        lon_full = zoom(lon_ml, (zoom_y, zoom_x), order=1)
        hgt_full = zoom(hgt_ml, (zoom_y, zoom_x), order=1)
        inc_full = zoom(inc_angle_ml.astype(np.float64), (zoom_y, zoom_x), order=1)

        # Trim to exact SLC dimensions
        lat_full = lat_full[:slc_length, :slc_width]
        lon_full = lon_full[:slc_length, :slc_width]
        hgt_full = hgt_full[:slc_length, :slc_width]
        inc_full = inc_full[:slc_length, :slc_width]

        print(f"  Interpolated to: {lat_full.shape[1]} x {lat_full.shape[0]}")
    except ImportError:
        print("  ERROR: scipy required for geometry interpolation")
        sys.exit(1)

    # ── 5. Compute Amplitude Dispersion ──
    print(f"\n[5/8] Computing Amplitude Dispersion Index...")
    print(f"  Loading {n_slc} SLCs via GDAL ({slc_width}x{slc_length} each)...")

    # Running statistics: mean and variance via Welford's algorithm
    amp_count = np.zeros((slc_length, slc_width), dtype=np.int32)
    amp_mean = np.zeros((slc_length, slc_width), dtype=np.float64)
    amp_m2 = np.zeros((slc_length, slc_width), dtype=np.float64)

    for idx, date_str in enumerate(all_dates):
        vrt_path = slc_files[date_str]
        ds = gdal.Open(vrt_path, gdal.GA_ReadOnly)
        if ds is None:
            print(f"    WARNING: Cannot open {vrt_path}, skipping")
            continue

        band = ds.GetRasterBand(1)
        slc_data = band.ReadAsArray()  # complex64
        ds = None

        if slc_data is None:
            print(f"    WARNING: Empty data for {date_str}, skipping")
            continue

        amp = np.abs(slc_data).astype(np.float64)
        valid = amp > 0
        if valid.any(): amp = amp / np.median(amp[valid])          # per-date radiometric calibration
        if int(date_str[4:6]) not in DA_MONTHS: del slc_data, amp; continue   # snow season excluded from D_A

        # Welford's online algorithm
        amp_count += valid.astype(np.int32)
        delta = np.where(valid, amp - amp_mean, 0)
        amp_mean += np.where(amp_count > 0, delta / np.maximum(amp_count, 1), 0)
        delta2 = np.where(valid, amp - amp_mean, 0)
        amp_m2 += delta * delta2

        del slc_data, amp
        if (idx + 1) % 10 == 0 or idx == n_slc - 1:
            print(f"    Loaded {idx+1}/{n_slc} SLCs")

    # DA = std / mean
    variance = np.where(amp_count > 1, amp_m2 / (amp_count - 1), 0)
    std_amp = np.sqrt(np.maximum(variance, 0))
    da = np.where(amp_mean > 0, std_amp / amp_mean, 999.0).astype(np.float32)

    # PS candidate selection
    min_valid = int(amp_count.max() * 0.8)
    geo_ok = (hgt_full > -100) & (hgt_full < 9000) & (np.abs(lat_full) > 0.001) & (np.abs(lon_full) > 0.001) & (amp_count >= min_valid) & (da > 0)
    n_std = int(np.sum(geo_ok & (da < da_thresh)))
    if n_std < args.min_cand:
        da_thresh = float(min(args.da_max, np.percentile(da[geo_ok], args.pct)))
        print(f"  adaptive D_A: {n_std} candidates at the standard threshold -> best {args.pct}% -> D_A < {da_thresh:.3f}")
    ps_mask = (da < da_thresh) & (da > 0) & (amp_count >= min_valid)

    # Exclude invalid geometry
    ps_mask &= (hgt_full > -100) & (hgt_full < 9000)
    ps_mask &= (np.abs(lat_full) > 0.001) & (np.abs(lon_full) > 0.001)

    n_ps = int(np.sum(ps_mask))
    total_px = slc_width * slc_length
    print(f"  DA threshold: {da_thresh}")
    print(f"  PS candidates: {n_ps:,} / {total_px:,} ({100.0*n_ps/total_px:.2f}%)")

    if n_ps == 0:
        print("ERROR: No PS candidates found. Try increasing --da-thresh")
        sys.exit(1)

    if args.max_ps > 0 and n_ps > args.max_ps:
        da_flat = da[ps_mask]
        sort_idx = np.argsort(da_flat)
        threshold_new = da_flat[sort_idx[args.max_ps - 1]]
        ps_mask = ps_mask & (da <= threshold_new)
        n_ps = int(np.sum(ps_mask))
        print(f"  Trimmed to {n_ps:,} (max-ps={args.max_ps})")

    ps_az, ps_rg = np.where(ps_mask)  # row=azimuth, col=range

    # Free memory
    del amp_count, amp_mean, amp_m2, variance, std_amp

    # ── 6. Extract PS candidate geometry ──
    print(f"\n[6/8] Extracting PS candidate geometry...")
    ps_lon = lon_full[ps_az, ps_rg].astype(np.float32)
    ps_lat = lat_full[ps_az, ps_rg].astype(np.float32)
    ps_hgt = hgt_full[ps_az, ps_rg].astype(np.float32)
    ps_da = da[ps_az, ps_rg]
    ps_inc = inc_full[ps_az, ps_rg].astype(np.float32)

    print(f"  PS lon: {ps_lon.min():.4f} ~ {ps_lon.max():.4f}")
    print(f"  PS lat: {ps_lat.min():.4f} ~ {ps_lat.max():.4f}")
    print(f"  PS hgt: {ps_hgt.min():.1f} ~ {ps_hgt.max():.1f} m")
    print(f"  PS DA:  {ps_da.min():.3f} ~ {ps_da.max():.3f}")

    # Free interpolated geometry
    del lat_full, lon_full, hgt_full, inc_full, da

    # ── 7. Extract complex phase at PS locations ──
    print(f"\n[7/8] Extracting phase at PS locations...")

    # Load master SLC
    master_ds = gdal.Open(slc_files[master_date], gdal.GA_ReadOnly)
    master_slc = master_ds.GetRasterBand(1).ReadAsArray()
    master_ds = None
    master_at_ps = master_slc[ps_az, ps_rg].copy()
    master_amp_vals = np.abs(master_at_ps)
    del master_slc

    # Phase + calibration for each slave
    calamp_data = []
    ph_array = np.zeros((n_ps, n_slave), dtype=np.complex64)

    for idx, date_str in enumerate(slave_dates):
        ds = gdal.Open(slc_files[date_str], gdal.GA_ReadOnly)
        if ds is None:
            print(f"    WARNING: Cannot open {date_str}")
            continue
        slave_slc = ds.GetRasterBand(1).ReadAsArray()
        ds = None

        slave_at_ps = slave_slc[ps_az, ps_rg]
        del slave_slc

        # Interferometric phase: slave * conj(master)
        ph = slave_at_ps * np.conj(master_at_ps)

        # Keep amplitude info (StaMPS uses it)
        ph_array[:, idx] = ph.astype(np.complex64)

        # Calibration: mean amplitude of slave
        slave_amp = np.abs(slave_at_ps)
        mean_amp = float(np.mean(slave_amp[slave_amp > 0])) if np.any(slave_amp > 0) else 1.0
        calamp_data.append((date_str, mean_amp))

        del slave_at_ps
        if (idx + 1) % 10 == 0 or idx == n_slave - 1:
            print(f"    Processed {idx+1}/{n_slave} slaves")

    # ── 8. Baselines + Heading ──
    print(f"\n[8/8] Reading baselines & writing output...")
    bperps = parse_baselines(baseline_dir, slave_dates, master_date)
    n_with_bperp = sum(1 for v in bperps.values() if v != 0)
    print(f"  Baselines: {n_with_bperp}/{n_slave} non-zero")

    heading = get_heading(isce_dir)
    print(f"  Heading: {heading:.2f} deg")

    # ── Write StaMPS output ──
    os.makedirs(output_dir, exist_ok=True)

    # Single patch
    patch_name = 'PATCH_1'
    patch_dir = os.path.join(output_dir, patch_name)
    os.makedirs(patch_dir, exist_ok=True)

    # patch.list
    with open(os.path.join(output_dir, 'patch.list'), 'w') as f:
        f.write(patch_name + '\n')

    # patch.in
    with open(os.path.join(patch_dir, 'patch.in'), 'w') as f:
        f.write(f"0\n{slc_length}\n0\n{slc_width}\n")

    # pscands.1.ij (text: ID azimuth range)
    print(f"  Writing pscands.1.ij ({n_ps:,} candidates)...")
    with open(os.path.join(patch_dir, 'pscands.1.ij'), 'w') as f:
        for k in range(n_ps):
            f.write(f"{k+1} {ps_az[k]} {ps_rg[k]}\n")

    # pscands.1.ll (binary: lon lat pairs, float32)
    ll_data = np.column_stack([ps_lon, ps_lat]).astype(np.float32)
    ll_data.tofile(os.path.join(patch_dir, 'pscands.1.ll'))

    # pscands.1.ph (binary: complex phase per slave)
    # Format: for each slave, n_ps * (real, imag) as float32
    print(f"  Writing pscands.1.ph...")
    with open(os.path.join(patch_dir, 'pscands.1.ph'), 'wb') as f:
        for s in range(n_slave):
            interleaved = np.zeros(n_ps * 2, dtype=np.float32)
            interleaved[0::2] = ph_array[:, s].real
            interleaved[1::2] = ph_array[:, s].imag
            f.write(interleaved.tobytes())

    # pscands.1.da (text)
    np.savetxt(os.path.join(patch_dir, 'pscands.1.da'), ps_da, fmt='%.6f')

    # pscands.1.hgt (binary float32)
    ps_hgt.astype(np.float32).tofile(os.path.join(patch_dir, 'pscands.1.hgt'))

    # day.1.in
    with open(os.path.join(patch_dir, 'day.1.in'), 'w') as f:
        for d in slave_dates:
            f.write(d + '\n')

    # master_day.1.in
    with open(os.path.join(patch_dir, 'master_day.1.in'), 'w') as f:
        f.write(master_date + '\n')

    # bperp.1.in
    with open(os.path.join(patch_dir, 'bperp.1.in'), 'w') as f:
        for d in slave_dates:
            f.write(f"{bperps.get(d, 0.0):.4f}\n")

    # heading.1.in
    with open(os.path.join(patch_dir, 'heading.1.in'), 'w') as f:
        f.write(f"{heading:.6f}\n")

    # lambda.1.in
    with open(os.path.join(patch_dir, 'lambda.1.in'), 'w') as f:
        f.write(f"{SENTINEL1_WAVELENGTH:.8f}\n")

    # calamp.out
    master_mean_amp = float(np.mean(master_amp_vals[master_amp_vals > 0]))
    with open(os.path.join(patch_dir, 'calamp.out'), 'w') as f:
        for date_str, amp_val in calamp_data:
            cal_val = amp_val / master_mean_amp if master_mean_amp > 0 else 1.0
            f.write(f"{date_str}/{date_str}.slc {cal_val:.6f}\n")

    # width.txt, len.txt
    with open(os.path.join(patch_dir, 'width.txt'), 'w') as f:
        f.write(f"{slc_width}\n")
    with open(os.path.join(patch_dir, 'len.txt'), 'w') as f:
        f.write(f"{slc_length}\n")

    # processor.txt
    with open(os.path.join(patch_dir, 'processor.txt'), 'w') as f:
        f.write("isce\n")

    # inc_angle.raw (binary float32, full SLC resolution from multilooked)
    # StaMPS load_isce reads this as (width, length) then indexes with (range+1, az+1)
    print(f"  Writing inc_angle.raw...")
    # Re-interpolate inc angle since we freed it
    inc_full_write = zoom(inc_angle_ml.astype(np.float64),
                          (slc_length / geom_l, slc_width / geom_w), order=1)
    inc_full_write = inc_full_write[:slc_length, :slc_width]
    inc_full_write.astype(np.float32).tofile(os.path.join(patch_dir, 'inc_angle.raw'))
    del inc_full_write

    # inc_angle.raw.xml (required by StaMPS load_isce.m)
    inc_xml_content = (
        '<image_name>\n'
        f'    <property name="width">\n        <value>{slc_width}</value>\n    </property>\n'
        f'    <property name="length">\n        <value>{slc_length}</value>\n    </property>\n'
        '    <property name="number_bands">\n        <value>1</value>\n    </property>\n'
        '    <property name="data_type">\n        <value>FLOAT</value>\n    </property>\n'
        '    <property name="scheme">\n        <value>BIP</value>\n    </property>\n'
        '</image_name>\n'
    )
    with open(os.path.join(patch_dir, 'inc_angle.raw.xml'), 'w') as f:
        f.write(inc_xml_content)

    # Also write top-level copies for StaMPS
    for fname in ['width.txt', 'len.txt', 'processor.txt', 'master_day.1.in',
                   'heading.1.in', 'lambda.1.in', 'day.1.in', 'bperp.1.in', 'calamp.out']:
        src = os.path.join(patch_dir, fname)
        dst = os.path.join(output_dir, fname)
        if os.path.exists(src) and not os.path.exists(dst):
            import shutil
            shutil.copy2(src, dst)

    # ── Summary ──
    print()
    print("=" * 60)
    print("  Conversion Complete!")
    print("=" * 60)
    print(f"  Master date:    {master_date}")
    print(f"  SLC dates:      {n_slc} ({all_dates[0]} ~ {all_dates[-1]})")
    print(f"  Slave dates:    {n_slave}")
    print(f"  PS candidates:  {n_ps:,}")
    print(f"  SLC size:       {slc_width} x {slc_length}")
    print(f"  Geom size:      {geom_w} x {geom_l}")
    print(f"  DA threshold:   {da_thresh}")
    print(f"  Output:         {output_dir}/{patch_name}")
    print()
    print("  Output files:")
    for f in sorted(os.listdir(patch_dir)):
        fpath = os.path.join(patch_dir, f)
        fsize = os.path.getsize(fpath)
        if fsize > 1024*1024:
            print(f"    {f:<25s} {fsize/1024/1024:.1f} MB")
        elif fsize > 1024:
            print(f"    {f:<25s} {fsize/1024:.1f} KB")
        else:
            print(f"    {f:<25s} {fsize} B")
    print()
    print("Next steps:")
    print(f"  cd {output_dir}/{patch_name}")
    print(f"  octave --no-gui --eval \"addpath(genpath('$STAMPS/matlab')); stamps(1,1)\"")
    print()


if __name__ == '__main__':
    main()
