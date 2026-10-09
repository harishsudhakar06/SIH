import json
import sys
from pathlib import Path

sys.path.insert(0, 'backend')
import app as m

a = m.create_app(auto_bootstrap=False)
with a.test_client() as c:
    r = c.post('/api/auth/login', json={'username': 'citizen', 'password': 'citizen123'})
    h = {'Authorization': 'Bearer ' + r.get_json()['token']}
    from seed import make_fixture_photo

    p = Path('backend/data/uploads/smoke1.jpg')
    make_fixture_photo(p, seed=3)
    with open(p, 'rb') as f:
        res = c.post('/api/reports',
                     data={'title': 'Smoke test crack', 'category': 'crack',
                           'lat': '25.58', 'lng': '91.88', 'gps_source': 'device',
                           'photo': (f, 'a.jpg', 'image/jpeg')}, headers=h)
    print('status', res.status_code)
    b = res.get_json()
    print('job', b['analysis']['status'], 'attempt', b['analysis']['attempt'])
    for s in b['analysis']['stages']:
        expl = (s['explanation'] or '')[:70]
        print('  %2d %-22s %-8s %s' % (s['seq'], s['stage'], s['status'], expl))
    print('assessment:', json.dumps(b['assessment'], indent=1))
    print('pipeline_error:', b['pipeline_error'])
    rid = b['report']['id']
    r2 = c.get(f'/api/reports/{rid}/analysis', headers=h)
    print('GET analysis', r2.status_code, r2.get_json()['job']['status'])
    # idempotency: second analyze while none active should create attempt 2
    r3 = c.post(f'/api/reports/{rid}/analyze', headers={**h,
                'Authorization': 'Bearer ' + c.post('/api/auth/login', json={'username': 'analyst', 'password': 'analyst123'}).get_json()['token']})
    print('POST analyze', r3.status_code, 'attempt', r3.get_json()['job']['attempt'])
