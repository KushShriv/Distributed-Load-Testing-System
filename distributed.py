"""Controller for optional remote drivers (Phase 3). Standard library only.

Splits one validated config across drivers, polls each driver's status (the
poll doubles as a heartbeat), aggregates results, and keeps partial results
when a driver is lost. Latencies are measured by each driver with its own
monotonic clock, so no clock synchronisation is needed.
"""
import json
import math
import secrets
import ssl
import threading
import time
from http.client import HTTPSConnection
from urllib.parse import urlsplit


MAX_WORKERS, MAX_COUNT = 100, 10000  # per-driver limits; keep in step with local_service
POLL_SECONDS = 1.0
MISSED_LIMIT = 3          # consecutive failed polls before a driver is marked lost
MAX_DRIVERS = 16
MAX_AGGREGATE_WORKERS = MAX_WORKERS * 4
MAX_AGGREGATE_COUNT = MAX_COUNT * 4


def client_context(cert, key, ca):
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=ca)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    return ctx


class Driver:
    def __init__(self, url, context):
        parts = urlsplit(url)
        if parts.scheme != 'https' or not parts.hostname or parts.username or parts.path not in ('', '/'):
            raise ValueError('Driver URL must be https://host:port')
        self.url, self.host, self.port, self.context = url, parts.hostname, parts.port or 443, context
        self.state, self.run_id, self.missed, self.snapshot, self.error = 'idle', None, 0, None, None

    def call(self, method, path, body=None):
        conn = HTTPSConnection(self.host, self.port, timeout=5, context=self.context)
        try:
            data = json.dumps(body).encode() if body is not None else None
            conn.request(method, path, body=data, headers={'Content-Type': 'application/json'} if data else {})
            resp = conn.getresponse(); raw = resp.read(8 * 1024 * 1024)
            return resp.status, json.loads(raw or b'{}')
        finally:
            conn.close()


def split(total, parts):
    base, extra = divmod(total, parts)
    return [base + (1 if i < extra else 0) for i in range(parts)]


def plan_slices(config, n):
    """Split workers and count across n drivers; every driver gets at least one worker."""
    n = min(n, config['workers'])
    workers = split(config['workers'], n)
    counts = split(config['count'], n) if config['count'] else [None] * n
    slices = []
    for w, c in zip(workers, counts):
        if config['count'] and c == 0: continue
        s = dict(config, workers=w, count=c, authorized=True)
        slices.append(s)
    return slices


class DistributedRun:
    def __init__(self, config, drivers):
        if not drivers: raise ValueError('No drivers configured')
        if len(drivers) > MAX_DRIVERS: raise ValueError('Too many drivers')
        if config['workers'] > MAX_AGGREGATE_WORKERS or (config['count'] or 0) > MAX_AGGREGATE_COUNT:
            raise ValueError('Aggregate limits exceeded')
        self.id = secrets.token_hex(16); self.config = config
        self.slices = plan_slices(config, len(drivers)); self.drivers = drivers[:len(self.slices)]
        self.cancel = threading.Event(); self.state = 'pending'; self.lock = threading.Lock()

    def start(self):
        threading.Thread(target=self.execute, daemon=True).start()

    def execute(self):
        self.state = 'running'
        for d, s in zip(self.drivers, self.slices):
            try:
                status, body = d.call('POST', '/v1/runs', s)
                if status != 201: raise OSError(body.get('error', f'HTTP {status}'))
                d.run_id, d.state = body['id'], 'running'
            except Exception as exc:
                d.state, d.error = 'failed', str(exc)[:200]
        while True:
            if self.cancel.is_set():
                for d in self.drivers:
                    if d.run_id and d.state == 'running':
                        try: d.call('POST', f'/v1/runs/{d.run_id}/cancel')
                        except Exception: pass
            alive = False
            for d in self.drivers:
                if d.state != 'running': continue
                try:
                    status, body = d.call('GET', f'/v1/runs/{d.run_id}')
                    if status != 200: raise OSError(f'HTTP {status}')
                    d.missed, d.snapshot = 0, body
                    if body['state'] in ('completed', 'failed', 'cancelled'): d.state = body['state']
                    else: alive = True
                except Exception as exc:
                    d.missed += 1; d.error = str(exc)[:200]
                    if d.missed >= MISSED_LIMIT: d.state = 'lost'
                    else: alive = True
            if not alive: break
            time.sleep(POLL_SECONDS if not self.cancel.is_set() else 0.2)
        states = [d.state for d in self.drivers]
        if self.cancel.is_set(): self.state = 'cancelled'
        elif all(s == 'completed' for s in states): self.state = 'completed'
        elif any(s == 'completed' for s in states): self.state = 'partial'
        else: self.state = 'failed'

    def result(self):
        completed = successes = errors = 0; statuses = {}; latencies = []; samples = []; elapsed = 0.0
        per_driver = []
        for d in self.drivers:
            s = d.snapshot or {}
            completed += s.get('completed', 0); successes += s.get('successes', 0); errors += s.get('transport_errors', 0)
            for k, v in s.get('status_counts', {}).items(): statuses[k] = statuses.get(k, 0) + v
            latencies += s.get('latencies_ms', []); samples += s.get('error_samples', [])[:2]
            elapsed = max(elapsed, s.get('elapsed_seconds', 0))
            per_driver.append({'url': d.url, 'state': d.state, 'completed': s.get('completed', 0),
                               'transport_errors': s.get('transport_errors', 0), 'error': d.error})
        values = sorted(latencies)
        def pct(p): return round(values[max(0, math.ceil(len(values) * p) - 1)], 2) if values else None
        return {'id': self.id, 'state': self.state, 'partial': self.state == 'partial', 'config': self.config,
                'completed': completed, 'successes': successes, 'transport_errors': errors, 'status_counts': statuses,
                'error_samples': samples[:5], 'elapsed_seconds': round(elapsed, 3),
                'throughput_per_second': round(completed / elapsed, 2) if elapsed else 0,
                'latency_ms': {'p50': pct(.5), 'p95': pct(.95), 'p99': pct(.99)}, 'drivers': per_driver}
