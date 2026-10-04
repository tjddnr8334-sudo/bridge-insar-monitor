# -*- coding: utf-8 -*-
"""One processing cycle for a municipality (initial build or 12-day update), track by track:
download new acquisitions -> (re)build / update the coregistered stack per swath (only new dates are coregistered) ->
cut tile windows (streaming, coreg binaries freed per date) -> per tile: StaMPS PS -> QC filter -> thermal correction ->
allowable-displacement judgement -> second pass (Capon / APES / DS) for weak bridges -> collect, history, alerts, report."""
import glob, json, os, shutil, subprocess, time
from . import config as C, registry, tracks as TR, download, weather


def sh(cmd, env, log=None, bg=False):
    out = open(log, 'a') if log else subprocess.DEVNULL
    if bg: return subprocess.Popen(['bash', '-c', cmd], env=env, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    return subprocess.run(['bash', '-c', cmd], env=env, stdout=out, stderr=subprocess.STDOUT).returncode


def prepare(c):
    """Registry -> bridges / tiles of the municipality; tracks covering it (written to <data>/<name>/setup.json)."""
    root = os.path.join(c['paths']['data'], c['name']); os.makedirs(root, exist_ok=True)
    csv = os.path.join(c['paths']['data'], 'registry', 'bridge_std.csv')
    if not os.path.exists(csv) or c.get('refresh_registry'): registry.download(csv)
    brs = registry.bridges(csv, c['sido'], c.get('sigungu'), c.get('min_length_m', 0))
    tl = registry.tiles(brs)
    aoi = registry.aoi_wkt(tl)
    trs = c.get('tracks') or TR.find(aoi, start=c.get('start', '2018-06-01'))
    for tr in trs:   # per-period reference date (middle acquisition of that period) when not given
        if not tr.get('reference_date'):
            ds = TR.available_dates(aoi, tr['path'], tr.get('start', c.get('start', '2018-06-01')), tr.get('end'))
            tr['reference_date'] = TR.reference(ds) if ds else None; tr['n_dates'] = len(ds)
    trs = [tr for tr in trs if tr.get('reference_date')]
    json.dump(dict(bridges=len(brs), tiles=len(tl), aoi=aoi, tracks=trs), open(os.path.join(root, 'setup.json'), 'w'), ensure_ascii=False, indent=1)
    return brs, tl, aoi, trs


def run_track(c, tr, tl, aoi, log, download_only=False):
    key, data, work = C.track_dirs(c, tr); env = C.env(c, tr)
    json.dump(tl, open(os.path.join(data, 'tiles.json'), 'w'), ensure_ascii=False)
    slc = os.path.join(c['paths']['data'], 'slc', key); orb = c['paths']['orbits']
    from shapely import wkt as W
    b = W.loads(aoi).bounds
    # 1) acquisitions
    t0, t1 = tr.get('start', c.get('start', '2018-06-01')), tr.get('end')
    dates = TR.available_dates(aoi, tr['path'], t0, t1)
    gj = os.path.join(c['paths']['data'], c['name'], 'aoi.geojson')
    if not os.path.exists(gj):
        from shapely.geometry import mapping
        json.dump(dict(type='FeatureCollection', features=[dict(type='Feature', properties={}, geometry=mapping(W.loads(aoi)))]), open(gj, 'w'))
    new = download.update(env, gj, tr['path'], dates, slc, orb, os.path.join(data, 'download.log'))
    weather.fetch(tl, tr['utc'], os.path.join(data, 'temps'), start=t0, end=t1)
    if download_only: return new
    # |Bperp| > max dates are skipped before coregistration (they are removed by the QC filter anyway)
    # 2) stacks per swath (ISCE selects every burst of that swath overlapping the AOI box)
    for sw in sorted({b_['sw'] for b_ in TR.bursts(aoi, tr['path'], tr['reference_date'])}):
        name = '%s_iw%d' % (key, sw); D = os.path.join(work, 'stack_' + name)
        if os.path.exists(os.path.join(D, 'logs', 'STACK_DONE')):
            if not new: continue
            # update: keep reference / geometry / done markers of the reference steps; new dates only
            stamp = time.strftime('%Y%m%d%H%M')
            if os.path.isdir(os.path.join(D, 'stack', 'run_files')): os.rename(os.path.join(D, 'stack', 'run_files'), os.path.join(D, 'stack', 'run_files_' + stamp))
            for m in ('done_run_02_unpack_secondary_slc', 'done_run_03_average_baseline', 'done_coreg', 'STACK_DONE'):
                p = os.path.join(D, 'logs', m)
                if os.path.exists(p): os.remove(p)
            for p in (os.path.join(D, 'CUT_DONE'),):
                if os.path.exists(p): os.remove(p)
        S_, N_, W_, E_ = b[1], b[3], b[0], b[2]
        cut = sh('$BIM_CONDA/envs/isce2_mintpy/bin/python $BIM_HOME/stream_cut.py %s/stack %d' % (D, sw), env, os.path.join(D + '.cut.log'), bg=True)
        sh('bash $BIM_HOME/stack.sh %s %d %.4f %.4f %s %.4f %.4f %.4f %.4f' % (name, sw, (S_ + N_) / 2, (W_ + E_) / 2, slc, S_, N_, W_, E_), env, log)
        cut.wait()
        if new:   # tiles of this stack are recomputed with the extended time series (previous result kept in history)
            for f in glob.glob(os.path.join(data, 'win', '*', 'meta.json')):
                m = json.load(open(f))
                if m.get('stack', '').startswith(D):
                    t = m['tile']; os.makedirs(os.path.join(data, 'res_prev'), exist_ok=True)
                    r = os.path.join(data, 'res', t + '.json')
                    if os.path.exists(r): shutil.move(r, os.path.join(data, 'res_prev', t + '.json'))
                    shutil.rmtree(os.path.join(work, 'run', t), ignore_errors=True)
    # 3) tiles (first pass) and weak bridges (second pass)
    P = int(c.get('parallel_tiles', 4))
    sh('ls $BIM_DATA/win/*/CUT_DONE 2>/dev/null | xargs -r -n1 dirname | xargs -r -n1 basename | xargs -r -P %d -I T bash $BIM_HOME/tile.sh T' % P, env, log)
    sh('GW=$BIM_WORK bash $BIM_HOME/pool2_once.sh %d' % int(c.get('parallel_second', 2)), env, log)
    return new


def run(c, download_only=False):
    from . import collect, alerts, report
    root = os.path.join(c['paths']['data'], c['name']); log = os.path.join(root, 'cycle.log')
    brs, tl, aoi, trs = prepare(c); news = {}
    for tr in trs:
        news[C.track_dirs(c, tr)[0]] = run_track(c, tr, tl, aoi, log, download_only)
    if download_only: return dict(new_dates=news)
    res = collect.collect(c, brs, trs)
    al = alerts.make(c, res)
    report.build(c, res, al)
    return dict(new_dates=news, bridges=len(brs), alerts=len(al['alerts']))
