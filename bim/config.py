# -*- coding: utf-8 -*-
"""Municipality configuration (YAML) and the environment handed to the pipeline scripts."""
import os
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def load(path):
    with open(path, encoding='utf-8') as f:
        c = yaml.safe_load(f)
    c.setdefault('criteria', {})
    crit_file = os.path.join(REPO, 'config', 'criteria.yaml')
    with open(crit_file, encoding='utf-8') as f:
        base = yaml.safe_load(f)
    base.update(c['criteria'])
    c['criteria'] = base
    c['_path'] = os.path.abspath(path)
    return c


def track_dirs(c, track):
    """Data (large, slow disk) and work (fast disk) directories of one track."""
    key = '%s%d%s' % (track['dir'][0].lower(), track['path'], track.get('tag', ''))
    data = os.path.join(c['paths']['data'], c['name'], key)
    work = os.path.join(c['paths']['work'], c['name'], key)
    for d in (data, work):
        os.makedirs(d, exist_ok=True)
    return key, data, work


def env(c, track):
    """Environment variables read by pipeline/*.sh and pipeline/*.py."""
    key, data, work = track_dirs(c, track)
    e = dict(os.environ)
    e.update(BIM_HOME=os.path.join(REPO, 'pipeline'), BIM_DATA=data, BIM_WORK=work, BIM_TRACK=key,
             BIM_MASTER=track['reference_date'], BIM_CONDA=c['paths']['conda'], BIM_DEM=c['paths']['dem'],
             BIM_ORBITS=c['paths']['orbits'], BIM_AUX=c['paths']['aux'], STAMPS=c['paths']['stamps'],
             BIM_CRITERIA=os.path.join(REPO, 'config', 'criteria.yaml'), HDF5_USE_FILE_LOCKING='FALSE')
    return e
