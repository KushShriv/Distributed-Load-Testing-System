import json
import subprocess
import tempfile
import threading
import time
import unittest
from http.client import HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import distributed
import local_service as app
import remote_driver

ROOT = Path(__file__).resolve().parent.parent


class Fixture(BaseHTTPRequestHandler):
    hits = 0
    def do_GET(self):
        Fixture.hits += 1
        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
    def log_message(self, *a): pass


def start(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class DistributedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); d = Path(cls.tmp.name) / 'pki'
        subprocess.run([str(ROOT / 'make_certs.sh'), str(d), '127.0.0.1'], check=True, capture_output=True)
        cls.pki = d
        cls.fixture = start(ThreadingHTTPServer(('127.0.0.1', 0), Fixture))
        cls.drivers = [start(remote_driver.create_driver('127.0.0.1', 0, d / 'driver.crt', d / 'driver.key', d / 'ca.crt')) for _ in range(2)]
        cls.ctx = distributed.client_context(d / 'controller.crt', d / 'controller.key', d / 'ca.crt')
        cls.url = 'http://127.0.0.1:%d/' % cls.fixture.server_port
    @classmethod
    def tearDownClass(cls):
        for s in cls.drivers + [cls.fixture]: s.shutdown(); s.server_close()
        cls.tmp.cleanup()

    def driver_objs(self, ports=None):
        ports = ports or [s.server_port for s in self.drivers]
        return [distributed.Driver('https://127.0.0.1:%d' % p, self.ctx) for p in ports]

    def config(self, **kw):
        return app.validate_config(dict({'authorized': True, 'url': self.url, 'workers': 4, 'count': 40, 'private_network': True}, **kw))

    def wait(self, run, timeout=20):
        end = time.monotonic() + timeout
        while run.state in ('pending', 'running') and time.monotonic() < end: time.sleep(.1)

    def test_split_is_exact_and_gives_each_driver_a_worker(self):
        slices = distributed.plan_slices(self.config(workers=5, count=11), 3)
        self.assertEqual(sum(s['workers'] for s in slices), 5); self.assertEqual(sum(s['count'] for s in slices), 11)
        self.assertEqual(len(distributed.plan_slices(self.config(workers=1), 3)), 1)

    def test_aggregates_across_drivers(self):
        before = Fixture.hits
        run = distributed.DistributedRun(self.config(), self.driver_objs()); run.start(); self.wait(run)
        r = run.result()
        self.assertEqual(r['state'], 'completed'); self.assertEqual(r['completed'], 40); self.assertEqual(r['successes'], 40)
        self.assertEqual(Fixture.hits - before, 40); self.assertEqual(r['status_counts'], {'200': 40})
        self.assertTrue(all(d['completed'] == 20 for d in r['drivers'])); self.assertIsNotNone(r['latency_ms']['p99'])

    def test_lost_driver_gives_partial_result(self):
        dead = self.driver_objs([self.drivers[0].server_port, 1])  # port 1 refuses connections
        run = distributed.DistributedRun(self.config(), dead); run.start(); self.wait(run)
        r = run.result()
        self.assertEqual(r['state'], 'partial'); self.assertTrue(r['partial'])
        self.assertEqual(r['completed'], 20); self.assertEqual(r['drivers'][1]['state'], 'failed')

    def test_cancel_stops_drivers(self):
        run = distributed.DistributedRun(self.config(workers=2, count=None, duration=60, delay=.2), self.driver_objs())
        run.start(); time.sleep(1.5); run.cancel.set(); self.wait(run, 10)
        self.assertEqual(run.state, 'cancelled')
        time.sleep(.5)
        for s in self.drivers: self.assertEqual(s.run.state, 'cancelled')

    def test_rejects_client_without_certificate_and_wrong_ca(self):
        port = self.drivers[0].server_port
        import ssl
        no_cert = ssl.create_default_context(cafile=str(self.pki / 'ca.crt')); no_cert.check_hostname = False
        with self.assertRaises((ssl.SSLError, OSError)):
            c = HTTPSConnection('127.0.0.1', port, context=no_cert, timeout=5); c.request('GET', '/v1/health'); c.getresponse().read()
        with tempfile.TemporaryDirectory() as other:
            subprocess.run([str(ROOT / 'make_certs.sh'), other + '/p', '127.0.0.1'], check=True, capture_output=True)
            ctx = distributed.client_context(other + '/p/controller.crt', other + '/p/controller.key', self.pki / 'ca.crt')
            with self.assertRaises((ssl.SSLError, OSError)):
                c = HTTPSConnection('127.0.0.1', port, context=ctx, timeout=5); c.request('GET', '/v1/health'); c.getresponse().read()

    def test_driver_rejects_unauthorized_and_oversized_requests(self):
        d = self.driver_objs()[0]
        self.assertEqual(d.call('POST', '/v1/runs', {'url': self.url})[0], 400)
        self.assertEqual(d.call('POST', '/v1/runs', {'authorized': True, 'url': 'file:///etc/passwd'})[0], 400)
        self.assertEqual(d.call('POST', '/v1/runs', {'pad': 'x' * 9000})[0], 413)

    def test_controller_rejects_plain_http_driver_and_too_many_drivers(self):
        with self.assertRaises(ValueError): distributed.Driver('http://127.0.0.1:1', self.ctx)
        with self.assertRaises(ValueError): distributed.DistributedRun(self.config(), self.driver_objs() * 20)
        with self.assertRaises(ValueError): distributed.DistributedRun(self.config(), [])

    def test_control_api_starts_distributed_run(self):
        cfg = {'ca': str(self.pki / 'ca.crt'), 'cert': str(self.pki / 'controller.crt'), 'key': str(self.pki / 'controller.key'),
               'drivers': ['https://127.0.0.1:%d' % s.server_port for s in self.drivers]}
        path = Path(self.tmp.name) / 'drivers.json'; path.write_text(json.dumps(cfg))
        server = app.create_server(0, 'tok'); app.configure_drivers(server, path); start(server)
        try:
            from http.client import HTTPConnection
            def call(method, p, body=None):
                c = HTTPConnection('127.0.0.1', server.server_port); h = {'X-Pairing-Token': 'tok'}
                if body is not None: h['Content-Type'] = 'application/json'
                c.request(method, p, json.dumps(body) if body is not None else None, h); r = c.getresponse(); return r.status, json.loads(r.read())
            status, body = call('POST', '/api/v1/distributed-runs', {'authorized': True, 'url': self.url, 'workers': 2, 'count': 10, 'private_network': True})
            self.assertEqual(status, 201)
            end = time.monotonic() + 20
            while time.monotonic() < end:
                status, res = call('GET', '/api/v1/distributed-runs/' + body['id'])
                if res['state'] != 'running' and res['state'] != 'pending': break
                time.sleep(.2)
            self.assertEqual(res['state'], 'completed'); self.assertEqual(res['completed'], 10)
        finally: server.shutdown(); server.server_close()


if __name__ == '__main__':
    unittest.main()
