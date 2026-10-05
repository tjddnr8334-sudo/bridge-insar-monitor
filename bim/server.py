# -*- coding: utf-8 -*-
"""Local web program:  python -m bim serve --config config/gangneung.yaml [--port 8080]
Endpoints (JSON): /api/summary  /api/bridges  /api/bridge?id=  /api/series?id=&track=&kind=  /api/alerts  /api/health
The page (web/app.html) shows alarms (bridge risk + processing errors), the map, the bridge list and per-bridge time series
(LOS / vertical / vertical after thermal correction, every track)."""
import json, os, sqlite3, urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from . import config as C


def make_handler(c):
    root = os.path.join(c['paths']['data'], c['name'])
    page = os.path.join(C.REPO, 'web', 'app.html')

    def jload(name, default):
        p = os.path.join(root, name)
        return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else default

    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype='application/json; charset=utf-8'):
            b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(b)))
            self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(b)

        def log_message(self, *a): pass

        def do_GET(self):
            u = urllib.parse.urlparse(self.path); q = dict(urllib.parse.parse_qsl(u.query))
            if u.path in ('/', '/index.html'):
                return self._send(200, open(page, 'rb').read(), 'text/html; charset=utf-8')
            st = jload('state.json', dict(bridges=[]))
            if u.path == '/api/summary':
                bs = st['bridges']; cnt = {}
                for b in bs:
                    r = b.get('r'); k = '미처리' if not r else ('판정 불가' if r['lv'] < 0 else ['정상', '관심', '주의', '경고'][r['lv']])
                    cnt[k] = cnt.get(k, 0) + 1
                h = jload('health.json', dict(events=[])); al = jload('alerts.json', dict(alerts=[]))
                setup = jload('setup.json', {})
                return self._send(200, dict(title=st.get('title', c['title']), made=st.get('made'), counts=cnt, n=len(bs), tracks=setup.get('tracks'),
                                            n_alerts=len(al['alerts']), n_errors=sum(1 for e in h['events'] if e['level'] in ('경고', '주의'))))
            if u.path == '/api/bridges':
                return self._send(200, [dict((k, b.get(k)) for k in ('id', 'n', 'c', 'lat', 'lon', 't', 'cls', 'len', 'yr', 'gr')) |
                                        dict(lv=(b['r'] or {}).get('lv') if b.get('r') else None, rn=(b['r'] or {}).get('rn') if b.get('r') else None,
                                             rv=(b['r'] or {}).get('rv') if b.get('r') else None) for b in st['bridges']])
            if u.path == '/api/bridge':
                b = next((x for x in st['bridges'] if x['id'] == q.get('id')), None)
                return self._send(200 if b else 404, b or dict(error='not found'))
            if u.path == '/api/series':
                db = os.path.join(root, 'history.sqlite')
                if not os.path.exists(db): return self._send(200, {})
                con = sqlite3.connect(db); out = {}
                for trk, kind, d, v in con.execute('select track, kind, date, disp_mm from series where bridge=? order by date', (q.get('id'),)):
                    out.setdefault(trk, {}).setdefault(kind, []).append([d, v])
                con.close(); return self._send(200, out)
            if u.path == '/api/alerts': return self._send(200, jload('alerts.json', dict(alerts=[])))
            if u.path == '/api/health': return self._send(200, jload('health.json', dict(events=[])))
            return self._send(404, dict(error='unknown path'))
    return H


def serve(c, port=8080, host='127.0.0.1'):
    srv = ThreadingHTTPServer((host, port), make_handler(c))
    print('bridge-insar-monitor: http://%s:%d  (%s)' % (host, port, c['title']), flush=True)
    srv.serve_forever()
