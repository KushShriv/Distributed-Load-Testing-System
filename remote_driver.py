#!/usr/bin/env python3
"""Optional remote driver for the local load tester (Phase 3).

Serves a small control API over mutually authenticated TLS. The bind address is
mandatory; there is no default. Uses no third-party dependencies.
"""
import argparse
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import local_service as core

MAX_BODY = 8192
MAX_LATENCY_SAMPLES = core.MAX_COUNT


def server_context(cert, key, ca):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    ctx.load_verify_locations(ca)
    ctx.verify_mode = ssl.CERT_REQUIRED  # reject clients without a certificate signed by our CA
    return ctx


class DriverServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, context):
        super().__init__(address, DriverHandler)
        self.socket = context.wrap_socket(self.socket, server_side=True)
        self.run = None
        self.lock = threading.Lock()

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(10)
        return request, address


class DriverHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def send(self, status, body):
        data = json.dumps(body).encode()
        self.close_connection = True
        self.send_response(status)
        self.send_header('Connection', 'close')
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        run = self.server.run
        if self.path == '/v1/health':
            active = bool(run and run.state in ('pending', 'running'))
            self.send(200, {'version': core.VERSION, 'active': active}); return
        parts = self.path.split('/')
        if len(parts) == 4 and parts[:3] == ['', 'v1', 'runs'] and run and run.id == parts[3]:
            result = run.snapshot()
            with run.lock:
                result['latencies_ms'] = [round(v * 1000, 3) for v in run.latencies[:MAX_LATENCY_SAMPLES]]
            self.send(200, result); return
        self.send(404, {'error': 'Not found'})

    def do_POST(self):
        try:
            lengths = self.headers.get_all('Content-Length', [])
            if self.headers.get_all('Transfer-Encoding', []) or len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                self.send(400, {'error': 'A single Content-Length is required'}); return
            size = int(lengths[0])
            if size > MAX_BODY: self.send(413, {'error': 'Request too large'}); return
            body = self.rfile.read(size)
            if self.path == '/v1/runs':
                config = core.validate_config(json.loads(body))
                with self.server.lock:
                    run = self.server.run
                    if run and run.state in ('pending', 'running'): self.send(409, {'error': 'A slice is already running'}); return
                    run = core.Run(config); self.server.run = run; run.start()
                self.send(201, {'id': run.id}); return
            parts = self.path.split('/')
            run = self.server.run
            if len(parts) == 5 and parts[:3] == ['', 'v1', 'runs'] and parts[4] == 'cancel' and run and run.id == parts[3]:
                run.cancel.set(); self.send(200, {'ok': True}); return
            self.send(404, {'error': 'Not found'})
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.send(400, {'error': str(exc)[:200]})


def create_driver(bind, port, cert, key, ca):
    return DriverServer((bind, port), server_context(cert, key, ca))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bind', required=True, help='explicit address to listen on, e.g. 127.0.0.1 or a private interface')
    p.add_argument('--port', type=int, required=True)
    p.add_argument('--cert', required=True); p.add_argument('--key', required=True); p.add_argument('--ca', required=True)
    a = p.parse_args()
    server = create_driver(a.bind, a.port, a.cert, a.key, a.ca)
    print(f'Driver listening on https://{a.bind}:{server.server_port} (mutual TLS required). Press Ctrl-C to stop.', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: print('\nStopping.')
    finally:
        if server.run: server.run.cancel.set()
        server.server_close()


if __name__ == '__main__':
    main()
