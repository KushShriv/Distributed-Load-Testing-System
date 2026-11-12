import json
import socket
import threading
import time
import unittest
from unittest.mock import patch
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import local_service as app


class Fixture(BaseHTTPRequestHandler):
    active = 0
    peak = 0
    guard = threading.Lock()
    def do_GET(self):
        if self.path == '/slow':
            with Fixture.guard:
                Fixture.active += 1
                Fixture.peak = max(Fixture.peak, Fixture.active)
            try: time.sleep(.15)
            finally:
                with Fixture.guard: Fixture.active -= 1
        if self.path == '/redirect-local':
            self.send_response(302); self.send_header('Location', 'http://127.0.0.1:%d/' % self.server.server_port); self.end_headers(); return
        if self.path == '/redirect':
            self.send_response(302); self.send_header('Location', 'file:///etc/passwd'); self.end_headers(); return
        self.send_response(503 if self.path == '/error' else 200); self.end_headers(); self.wfile.write(b'ok')
    def log_message(self, *args): pass


class LocalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
        threading.Thread(target=cls.fixture.serve_forever, daemon=True).start()
        cls.server = app.create_server(0, 'secret')
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.fixture.shutdown(); cls.fixture.server_close()
    def request(self, method, path, body=None, headers=None):
        conn = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        conn.request(method, path, body, headers or {})
        res = conn.getresponse(); data = res.read(); conn.close()
        return res.status, data
    def raw_request(self, request):
        with socket.create_connection(('127.0.0.1', self.server.server_port), timeout=3) as conn:
            conn.sendall(request)
            chunks = []
            while True:
                data = conn.recv(4096)
                if not data: break
                chunks.append(data)
            return b''.join(chunks)

    def test_rejects_ambiguous_request_framing_and_closes_connection(self):
        host = ('127.0.0.1:%d' % self.server.server_port).encode()
        for framing in [b'Transfer-Encoding: chunked\r\n', b'Content-Length: 1\r\nContent-Length: 1\r\n', b'']:
            request = (b'POST /api/v1/runs HTTP/1.1\r\nHost: ' + host + b'\r\nX-Pairing-Token: secret\r\n' + framing + b'Connection: close\r\n\r\n')
            response = self.raw_request(request)
            self.assertIn(b' 400 ', response)

    def test_oversized_body_and_bad_json_are_rejected(self):
        headers = {'X-Pairing-Token':'secret','Content-Type':'application/json'}
        self.assertEqual(self.request('POST','/api/v1/runs','x'*8193,headers)[0], 413)
        self.assertEqual(self.request('POST','/api/v1/runs','{',headers)[0], 400)

    def test_api_rate_limit_does_not_throttle_sample_target(self):
        with self.server.lock: self.server.requests = [time.monotonic()] * 600
        try:
            self.assertEqual(self.request('GET', '/sample')[0], 200)
            self.assertEqual(self.request('GET', '/api/v1/health', headers={'X-Pairing-Token':'secret'})[0], 429)
        finally:
            with self.server.lock: self.server.requests.clear()

    def test_workers_limit_concurrency_and_count(self):
        with Fixture.guard: Fixture.peak = 0
        run = app.Run(app.validate_config({'url':'http://127.0.0.1:%d/slow' % self.fixture.server_port, 'authorized':True, 'private_network':True, 'count':6, 'workers':2}))
        run.execute()
        result = run.snapshot()
        self.assertEqual(result['completed'], 6)
        self.assertEqual(result['successes'], 6)
        self.assertGreaterEqual(Fixture.peak, 2)
        self.assertLessEqual(Fixture.peak, 2)
        self.assertIsNotNone(result['latency_ms']['p99'])

    def test_final_request_does_not_wait_for_delay(self):
        run = app.Run(app.validate_config({'url':'http://127.0.0.1:%d/' % self.fixture.server_port, 'authorized':True, 'private_network':True, 'count':1, 'delay':2}))
        started = time.monotonic()
        run.execute()
        self.assertLess(time.monotonic() - started, 1)

    def test_redirect_destination_is_checked_before_connecting(self):
        real_getaddrinfo = socket.getaddrinfo
        real_connect = socket.create_connection
        def resolve(host, port, *args, **kwargs):
            if host == 'public.example': return real_getaddrinfo('8.8.8.8', port, *args, **kwargs)
            return real_getaddrinfo(host, port, *args, **kwargs)
        def connect(address, *args, **kwargs):
            host, port = address
            return real_connect(('127.0.0.1' if host == '8.8.8.8' else host, port), *args, **kwargs)
        with patch.object(app.socket, 'getaddrinfo', side_effect=resolve), patch.object(app.socket, 'create_connection', side_effect=connect):
            with self.assertRaisesRegex(ValueError, 'Private or local'):
                app.fetch('http://public.example:%d/redirect-local' % self.fixture.server_port, False)

    def test_latency_percentiles_and_error_exclusion(self):
        run = app.Run(app.validate_config({'url':'http://example.com', 'authorized':True}))
        run.started = time.monotonic() - 2
        run.ended = time.monotonic()
        run.completed = 6; run.transport_errors = 1
        run.latencies = [.001, .002, .003, .004, .100]
        result = run.snapshot()
        self.assertEqual(result['latency_ms'], {'p50':3.0, 'p95':100.0, 'p99':100.0})
        self.assertAlmostEqual(result['throughput_per_second'], 3, delta=.05)

    def test_unapproved_origin_cannot_start_run_even_with_token(self):
        headers = {'X-Pairing-Token':'secret','Content-Type':'application/json','Origin':'https://evil.example'}
        cfg = {'url':'http://127.0.0.1:%d/' % self.fixture.server_port, 'authorized':True, 'private_network':True}
        self.assertEqual(self.request('POST','/api/v1/runs',json.dumps(cfg),headers)[0], 403)

    def test_security_and_validation(self):
        self.assertEqual(self.request('GET', '/api/v1/health')[0], 401)
        self.assertEqual(self.request('GET', '/api/v1/health', headers={'X-Pairing-Token':'secret','Origin':'https://evil.example'})[0], 403)
        self.assertEqual(self.request('GET', '/api/v1/health', headers={'X-Pairing-Token':'secret','Host':'evil.example'})[0], 403)
        headers = {'X-Pairing-Token':'secret','Content-Type':'application/json'}
        for cfg in ({'url':'file:///etc/passwd','authorized':True}, {'url':'http://user:pass@localhost','authorized':True}, {'url':'http://localhost','authorized':True,'workers':101}, {'url':'http://localhost'}):
            self.assertEqual(self.request('POST','/api/v1/runs',json.dumps(cfg),headers)[0], 400)
    def test_duration_configuration(self):
        config = app.validate_config({'url':'http://example.com', 'authorized':True, 'duration':30})
        self.assertEqual(config['duration'], 30)
        self.assertIsNone(config['count'])
        with self.assertRaises(ValueError): app.validate_config({'url':'http://example.com', 'authorized':True, 'duration':301})

    def test_private_targets_require_confirmation(self):
        base = 'http://127.0.0.1:%d' % self.fixture.server_port
        with self.assertRaisesRegex(ValueError, 'Private or local'):
            app.fetch(base, False)

    def test_cancel_and_export(self):
        headers = {'X-Pairing-Token':'secret','Content-Type':'application/json'}
        cfg = {'url':'http://127.0.0.1:%d/slow' % self.fixture.server_port, 'authorized':True, 'private_network':True, 'count':100, 'workers':2}
        status, data = self.request('POST','/api/v1/runs',json.dumps(cfg),headers)
        self.assertEqual(status, 201, data)
        run_id = json.loads(data)['id']
        self.assertEqual(self.request('POST','/api/v1/runs/'+run_id+'/cancel',headers=headers)[0],200)
        for _ in range(100):
            _, data = self.request('GET','/api/v1/runs/'+run_id,headers=headers); run = json.loads(data)
            if run['state'] == 'cancelled': break
            time.sleep(.02)
        self.assertEqual(run['state'], 'cancelled')
        self.assertLess(run['completed'], 100)
        status, data = self.request('GET','/api/v1/runs/'+run_id+'/csv',headers=headers)
        self.assertEqual(status, 200); self.assertIn(b'cancelled', data)

    def test_run_counts_http_errors_and_rejects_bad_redirect(self):
        base = 'http://127.0.0.1:%d' % self.fixture.server_port
        headers = {'X-Pairing-Token':'secret','Content-Type':'application/json'}
        for path, expected in [('/error','503'),('/redirect',None)]:
            cfg = {'url':base+path,'authorized':True,'private_network':True,'count':4,'workers':2}
            status, data = self.request('POST','/api/v1/runs',json.dumps(cfg),headers)
            self.assertEqual(status, 201, data)
            run_id = json.loads(data)['id']
            for _ in range(100):
                _, data = self.request('GET','/api/v1/runs/'+run_id,headers=headers); run = json.loads(data)
                if run['state'] not in ('running','pending'): break
                time.sleep(.02)
            self.assertEqual(run['completed'],4)
            if expected: self.assertEqual(run['status_counts'][expected],4)
            else:
                self.assertEqual(run['transport_errors'],4)
                self.assertEqual(run['state'], 'failed')

if __name__ == '__main__': unittest.main()
