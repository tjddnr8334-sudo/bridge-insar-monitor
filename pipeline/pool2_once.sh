#!/bin/bash
# One pass of the second-pass queue (Capon + APES refocus + DS phase linking): bridges in QC-valid tiles with few deck PS (n_ps < 5 or node cover < 0.25)
# or judged only through the abutment proxy. Order: facility class (1종 > 2종 > 3종 > 기타), then length. 2 bridges at a time, 6 cores each.
E=$BIM_DATA; P=${1:-2}

  python3 - > $BIM_WORK/pool2_todo.txt <<PY
import json, glob, os
tiles = {t['tile']: {b['id']: b for b in t['bridges']} for t in json.load(open('$E/tiles.json', encoding='utf-8'))}
rank = {'1종': 0, '2종': 1, '3종': 2, '기타': 3}; todo = []
for f in glob.glob('$E/res/*.json'):
    if f.endswith('_qc.json'): continue
    r = json.load(open(f, encoding='utf-8'))
    if r.get('qc', {}).get('status') != 'OK': continue
    for b in r['bridges']:
        if 'pass2' in b or os.path.exists('$BIM_WORK/run2/%s/merge2.json' % b['id']) or b['id'] not in tiles.get(r['tile'], {}): continue
        if b.get('n_ps', 0) < 5 or b.get('node_cover', 0) < 0.25 or b.get('proxy'):
            reg = tiles[r['tile']][b['id']].get('reg') or {}
            todo.append((rank.get(reg.get('cls'), 3), -b.get('len', 0), r['tile'], b['id']))
for k in sorted(todo): print(k[2], k[3])
PY
  if [ -s $BIM_WORK/pool2_todo.txt ]; then NPROC=6 xargs -P $P -L 1 bash $BIM_HOME/second.sh < $BIM_WORK/pool2_todo.txt; echo "$(date) pass2 batch done, queue $(wc -l < $BIM_WORK/pool2_todo.txt)" >> $BIM_WORK/pool2.log
  fi
