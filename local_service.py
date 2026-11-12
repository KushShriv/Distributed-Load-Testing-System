#!/usr/bin/env python3
"""Loopback-only local load tester. No third-party dependencies."""
import csv
import hmac
import io
import ipaddress
import json
import math
import secrets
import socket
import ssl
import threading
import time
from http.client import HTTPConnection, HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urljoin, urlsplit

VERSION = '0.1.0'
MAX_WORKERS, MAX_COUNT, MAX_DURATION = 100, 10000, 300
MAX_BODY, MAX_REDIRECTS = 1024 * 1024, 5
PAGE_ORIGIN = 'https://kushshriv.github.io'
ROOT = Path(__file__).resolve().parent
import distributed


def validate_url(url):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 32 for c in url):
        raise ValueError('Invalid URL')
    try:
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username is not None or parts.password is not None or parts.fragment:
            raise ValueError('Use an HTTP(S) URL without credentials or fragments')
        port = parts.port or (443 if parts.scheme == 'https' else 80)
    except ValueError as exc:
        raise ValueError('Invalid HTTP(S) URL: ' + str(exc)) from exc
    return parts, port


def validate_config(data):
    if not isinstance(data, dict) or data.get('authorized') is not True:
        raise ValueError('Confirm authorization to test the target')
    validate_url(data.get('url'))
    if data.get('method', 'GET') != 'GET': raise ValueError('Only GET is supported')
    def integer(name, default, maximum):
        n = data.get(name, default)
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= maximum:
            raise ValueError(f'{name} must be an integer from 1 to {maximum}')
        return n
    workers = integer('workers', 1, MAX_WORKERS)
    count = integer('count', 100, MAX_COUNT) if data.get('duration') is None else None
    duration = integer('duration', 0, MAX_DURATION) if data.get('duration') is not None else None
    delay = data.get('delay', 0)
    if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not math.isfinite(delay) or not 0 <= delay <= 60: raise ValueError('delay must be between 0 and 60 seconds')
    return {'url': data['url'], 'method':'GET', 'workers':workers, 'count':count, 'duration':duration, 'delay':delay, 'private_network':data.get('private_network') is True}


def fetch(url, allow_private):
    start = time.monotonic()
    for redirect in range(MAX_REDIRECTS + 1):
        parts, port = validate_url(url)
        addresses = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
        if not addresses: raise OSError('No address found')
        ip = addresses[0][4][0]
        addr = ipaddress.ip_address(ip.split('%')[0])
        if not addr.is_global and not allow_private: raise ValueError('Private or local destination requires explicit confirmation')
        # Connect to the checked IP, retaining the original hostname for TLS and Host.
        conn = (HTTPSConnection if parts.scheme == 'https' else HTTPConnection)(parts.hostname, port, timeout=5)
        sock = socket.create_connection((ip, port), timeout=5)
        try:
            conn.sock = sock
            if parts.scheme == 'https': conn.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parts.hostname)
            path = parts.path or '/'
            if parts.query: path += '?' + parts.query
            conn.request('GET', path, headers={'User-Agent':'LocalLoadTester/'+VERSION, 'Connection':'close'})
            response = conn.getresponse()
            status, location = response.status, response.getheader('Location')
            if status in (301, 302, 303, 307, 308) and location:
                if redirect == MAX_REDIRECTS: raise ValueError('Redirect limit exceeded')
                url = urljoin(url, location)
                validate_url(url)
                continue
            # Read a bounded body; close the connection if it exceeds the limit.
            response.read(MAX_BODY + 1)
            return status, time.monotonic() - start
        finally:
            conn.close()
    raise ValueError('Redirect limit exceeded')


class Run:
    def __init__(self, config):
        self.id = secrets.token_hex(16); self.config = config; self.lock = threading.Lock(); self.cancel = threading.Event()
        self.state = 'pending'; self.claimed = 0; self.completed = 0; self.successes = 0; self.status_counts = {}; self.transport_errors = 0; self.error_samples = []; self.latencies = []; self.started = 0; self.ended = 0
    def start(self):
        threading.Thread(target=self.execute, daemon=True).start()
    def execute(self):
        self.started = time.monotonic(); self.state = 'running'
        threads = [threading.Thread(target=self.worker, daemon=True) for _ in range(self.config['workers'])]
        started_threads = []
        try:
            for t in threads:
                t.start(); started_threads.append(t)
            for t in started_threads: t.join()
            self.state = 'cancelled' if self.cancel.is_set() else ('failed' if self.completed and self.transport_errors == self.completed else 'completed')
        except Exception as exc:
            self.cancel.set()
            for t in started_threads: t.join()
            self.state = 'failed'; self.error_samples.append(str(exc)[:200])
        finally: self.ended = time.monotonic()
    def worker(self):
        while not self.cancel.is_set():
            with self.lock:
                if self.claimed >= (self.config['count'] or MAX_COUNT): return
                if self.config['duration'] and time.monotonic() - self.started >= self.config['duration']: return
                self.claimed += 1
            try:
                status, latency = fetch(self.config['url'], self.config['private_network'])
                with self.lock:
                    key = str(status); self.status_counts[key] = self.status_counts.get(key, 0) + 1
                    self.successes += int(200 <= status < 400); self.latencies.append(latency)
            except Exception as exc:
                with self.lock:
                    self.transport_errors += 1
                    if len(self.error_samples) < 5: self.error_samples.append(str(exc)[:200])
            finally:
                with self.lock: self.completed += 1
            with self.lock:
                if self.claimed >= (self.config['count'] or MAX_COUNT): return
            delay = self.config['delay']
            if self.config['duration']:
                delay = min(delay, max(0, self.started + self.config['duration'] - time.monotonic()))
            if self.cancel.wait(delay): return
    def snapshot(self):
        with self.lock:
            elapsed = max(0, (self.ended or time.monotonic()) - self.started) if self.started else 0
            values = sorted(self.latencies)
            def percentile(p): return round(values[max(0, math.ceil(len(values)*p)-1)]*1000, 2) if values else None
            return {'id':self.id, 'state':self.state, 'config':self.config, 'completed':self.completed, 'successes':self.successes, 'status_counts':dict(self.status_counts), 'transport_errors':self.transport_errors, 'error_samples':list(self.error_samples), 'elapsed_seconds':round(elapsed, 3), 'throughput_per_second':round(self.completed/elapsed, 2) if elapsed else 0, 'latency_ms':{'p50':percentile(.5), 'p95':percentile(.95), 'p99':percentile(.99)}}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, token):
        super().__init__(address, Handler); self.token = token; self.runs = {}; self.distributed = {}; self.drivers = None; self.lock = threading.Lock(); self.slots = threading.BoundedSemaphore(32); self.requests = []
    def get_request(self):
        request, address = super().get_request()
        request.settimeout(10)
        return request, address
    def process_request(self, request, client_address):
        if not self.slots.acquire(False): self.shutdown_request(request); return
        try: super().process_request(request, client_address)
        except Exception: self.slots.release(); raise
    def process_request_thread(self, request, client_address):
        try: super().process_request_thread(request, client_address)
        finally: self.slots.release()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *args): pass
    def send(self, status, body, content_type='application/json'):
        if not isinstance(body, bytes): body = json.dumps(body).encode()
        self.close_connection = True
        self.send_response(status); self.send_header('Connection', 'close'); self.send_header('Content-Type', content_type); self.send_header('Content-Length', str(len(body))); self.send_header('Cache-Control', 'no-store'); self.send_header('X-Content-Type-Options', 'nosniff')
        origin = self.headers.get('Origin')
        if origin in self.allowed_origins(): self.send_header('Access-Control-Allow-Origin', origin); self.send_header('Vary', 'Origin')
        self.end_headers(); self.wfile.write(body)
    def allowed_origins(self):
        port = self.server.server_port
        return {f'http://127.0.0.1:{port}', f'http://localhost:{port}', PAGE_ORIGIN}
    def guard(self, token=True, rate_limit=True):
        if len(self.headers.get_all('Host', [])) != 1 or self.headers.get('Host') not in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}: self.send(403, {'error':'Invalid Host'}); return False
        origin = self.headers.get('Origin')
        if len(self.headers.get_all('Origin', [])) > 1 or (origin and origin not in self.allowed_origins()): self.send(403, {'error':'Origin not allowed'}); return False
        if rate_limit:
            now = time.monotonic()
            with self.server.lock:
                self.server.requests = [t for t in self.server.requests if now-t < 60]
                if len(self.server.requests) >= 600: self.send(429, {'error':'Rate limit exceeded'}); return False
                self.server.requests.append(now)
        if token and len(self.headers.get_all('X-Pairing-Token', [])) != 1: self.send(401, {'error':'Invalid pairing token'}); return False
        provided = self.headers.get('X-Pairing-Token','')
        if token and not hmac.compare_digest(provided.encode('utf-8'), self.server.token.encode('utf-8')): self.send(401, {'error':'Invalid pairing token'}); return False
        return True
    def do_OPTIONS(self):
        if not self.guard(False): return
        self.send_response(204); origin = self.headers.get('Origin')
        if origin in self.allowed_origins(): self.send_header('Access-Control-Allow-Origin', origin); self.send_header('Vary','Origin')
        self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS'); self.send_header('Access-Control-Allow-Headers','Content-Type, X-Pairing-Token'); self.send_header('Content-Length','0'); self.end_headers()
    def do_GET(self):
        if self.path == '/sample':
            if self.guard(False, rate_limit=False): self.send(200, {'message':'Local sample target'})
            return
        if self.path == '/':
            if self.guard(False): self.send(200, (ROOT/'docs'/'index.html').read_bytes(), 'text/html; charset=utf-8')
            return
        if not self.guard(): return
        if self.path == '/api/v1/health': self.send(200, {'version':VERSION}); return
        parts = self.path.split('/')
        if len(parts) == 5 and parts[:4] == ['', 'api', 'v1', 'distributed-runs']:
            drun = self.server.distributed.get(parts[4])
            if drun: self.send(200, drun.result()); return
        if len(parts) in (5,6) and parts[:4] == ['', 'api', 'v1', 'runs']:
            run = self.server.runs.get(parts[4])
            if run:
                result = run.snapshot()
                if len(parts) == 6 and parts[5] == 'csv':
                    stream = io.StringIO(); writer = csv.writer(stream); writer.writerow(['field','value'])
                    for k,v in result.items(): writer.writerow([k, json.dumps(v) if isinstance(v,(dict,list)) else v])
                    self.send(200, stream.getvalue().encode(), 'text/csv; charset=utf-8'); return
                if len(parts) == 5: self.send(200, result); return
        self.send(404, {'error':'Not found'})
    def do_POST(self):
        if not self.guard(): return
        try:
            lengths = self.headers.get_all('Content-Length', [])
            if self.headers.get_all('Transfer-Encoding', []) or len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                self.send(400, {'error':'A single Content-Length is required; transfer encoding is not supported'}); return
            size = int(lengths[0])
            if size > 8192: self.send(413, {'error':'Request too large'}); return
            if self.path == '/api/v1/runs':
                data = json.loads(self.rfile.read(size)); config = validate_config(data)
                with self.server.lock:
                    if any(r.state in ('pending','running') for r in self.server.runs.values()): self.send(409, {'error':'A test is already running'}); return
                    if len(self.server.runs) >= 20: self.server.runs.pop(next(iter(self.server.runs)))
                    run = Run(config); self.server.runs[run.id] = run; run.start()
                self.send(201, {'id':run.id}); return
            if self.path == '/api/v1/distributed-runs':
                if not self.server.drivers: self.send(409, {'error':'No remote drivers configured'}); return
                config = validate_config(json.loads(self.rfile.read(size)))
                with self.server.lock:
                    if any(r.state in ('pending','running') for r in list(self.server.runs.values()) + list(self.server.distributed.values())): self.send(409, {'error':'A test is already running'}); return
                    if len(self.server.distributed) >= 20: self.server.distributed.pop(next(iter(self.server.distributed)))
                    drun = distributed.DistributedRun(config, self.server.drivers()); self.server.distributed[drun.id] = drun; drun.start()
                self.send(201, {'id':drun.id}); return
            parts = self.path.split('/')
            if len(parts) == 6 and parts[:4] == ['', 'api', 'v1', 'distributed-runs'] and parts[5] == 'cancel' and parts[4] in self.server.distributed:
                self.server.distributed[parts[4]].cancel.set(); self.send(200, {'ok':True}); return
            if len(parts) == 6 and parts[:4] == ['', 'api', 'v1', 'runs'] and parts[5] == 'cancel' and parts[4] in self.server.runs:
                self.server.runs[parts[4]].cancel.set(); self.send(200, {'ok':True}); return
            self.send(404, {'error':'Not found'})
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc: self.send(400, {'error':str(exc)[:200]})


def create_server(port=8765, token=None): return Server(('127.0.0.1', port), token or secrets.token_urlsafe(32))


def configure_drivers(server, config_path):
    """Load drivers.json: {"ca":..., "cert":..., "key":..., "drivers":["https://host:port", ...]}."""
    cfg = json.loads(Path(config_path).read_text())
    ctx = distributed.client_context(cfg['cert'], cfg['key'], cfg['ca'])
    urls = list(cfg['drivers'])
    for u in urls: distributed.Driver(u, ctx)  # validate early
    server.drivers = lambda: [distributed.Driver(u, ctx) for u in urls]

if __name__ == '__main__':
    server = create_server()
    import os
    if os.environ.get('DLT_DRIVERS'): configure_drivers(server, os.environ['DLT_DRIVERS'])
    print(f'Local dashboard: http://127.0.0.1:{server.server_port}/\nPairing code: {server.token}\nKeep this code private. Press Ctrl-C to stop.', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: print('\nStopping.')
    finally:
        for run in list(server.runs.values()) + list(server.distributed.values()): run.cancel.set()
        server.server_close()
