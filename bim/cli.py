# -*- coding: utf-8 -*-
"""Command line:  python -m bim <command> --config config/gangneung.yaml
  setup    registry -> bridges, tiles, tracks covering the municipality (no processing)
  download acquisitions + weather only (all tracks), no processing
  update   one full cycle: new acquisitions -> processing -> judgement -> alerts -> dashboard (safe to run daily; idle if nothing new)
  report   rebuild alerts and dashboard from the existing results
  status   bridges, tracks, last acquisition per track, last cycle summary"""
import argparse, json, os, sys
from . import config as C


def main(argv=None):
    ap = argparse.ArgumentParser(prog='bim')
    ap.add_argument('command', choices=['setup', 'download', 'update', 'report', 'status'])
    ap.add_argument('--config', required=True)
    a = ap.parse_args(argv)
    c = C.load(a.config)
    from . import cycle, collect, alerts, report
    if a.command == 'setup':
        brs, tl, aoi, trs = cycle.prepare(c)
        print(json.dumps(dict(bridges=len(brs), tiles=len(tl), tracks=trs), ensure_ascii=False, indent=1))
    elif a.command == 'download':
        print(json.dumps(cycle.run(c, download_only=True), ensure_ascii=False, indent=1))
    elif a.command == 'update':
        print(json.dumps(cycle.run(c), ensure_ascii=False, indent=1))
    elif a.command == 'report':
        brs, tl, aoi, trs = cycle.prepare(c)
        res = collect.collect(c, brs, trs); al = alerts.make(c, res); print(report.build(c, res, al), len(al['alerts']), 'alerts')
    elif a.command == 'status':
        root = os.path.join(c['paths']['data'], c['name'])
        s = json.load(open(os.path.join(root, 'setup.json'), encoding='utf-8')) if os.path.exists(os.path.join(root, 'setup.json')) else {}
        al = json.load(open(os.path.join(root, 'alerts.json'), encoding='utf-8')) if os.path.exists(os.path.join(root, 'alerts.json')) else {}
        print(json.dumps(dict(bridges=s.get('bridges'), tiles=s.get('tiles'), tracks=s.get('tracks'), last_cycle=al.get('made'),
                              judged=al.get('n_judged'), alerts=len(al.get('alerts', []))), ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
