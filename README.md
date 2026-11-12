# Distributed Load Testing System

This repository contains a new local load tester and the original distributed university prototype. The local path does not use Kafka or AWS. Remote drivers are optional. It is an early implementation, not a performance benchmark or a published release.

## Run locally

You need Python 3.10 or later with `venv` on native macOS or Linux. Windows and WSL are not supported. These are target platforms; the full fresh-install platform matrix has not yet been verified. No third-party Python packages are required. Download the source or clone the repository, review it, and run:

```sh
./start.sh
```

The launcher creates `.venv`, binds the service to `127.0.0.1:8765`, and prints a dashboard URL and a fresh pairing code. Open the URL on the same computer and enter the code. The dashboard starts in Demo mode; choose Local mode and connect. To test the included sample, enable local/private destinations, confirm authorization, and start the run. Press Ctrl-C in the terminal to stop the service; closing the browser does not stop it. Delete the project directory, including `.venv`, to uninstall. The launcher does not install system software or require administrator privileges or internet access.

Only test targets and redirects you are authorized to test. Requests originate from your machine and use its network. A remote target or its infrastructure can incur costs. Check the target, worker count, request count, and network impact before starting. Private and loopback destinations require a separate checkbox. Redirects are rechecked on every hop. Treat the pairing code like a password; do not share it or enter it on an untrusted page. The service keeps it in memory, requires it for API calls, checks the Host and Origin, and only allows the local dashboard and `https://kushshriv.github.io` origins. Browser extensions and scripts running at an allowed origin can still compromise a session.

The static page can connect after an explicit click, but local-network permissions, mixed-content rules, or browser configuration can prevent it. Use the dashboard URL printed by the launcher when that happens. Chrome, Firefox and Safari have not yet been tested as a complete support matrix. See [Chrome local network access](https://developer.chrome.com/docs/modern-web-guidance/guides/security/local-network-access) and [MDN CORS](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS).

## Limits and results

The API is `/api/v1`: `GET /health`, `POST /runs`, `GET /runs/{id}`, `POST /runs/{id}/cancel`, and `GET /runs/{id}/csv`. Every API request needs `X-Pairing-Token`. Start accepts JSON with `url`, `authorized: true`, optional `private_network: true`, `workers`, `count` or `duration` (seconds), and `delay` (seconds). Only GET targets are supported. The dashboard supports both request count and duration.

Defaults are one worker, 100 requests and no delay. Hard limits are 100 workers, 10,000 requests per run, 300 seconds for duration, 60 seconds delay, five redirects, 1 MiB read per response plus one byte, and a five-second socket timeout. Duration runs also stop at 10,000 requests. The limit is on bytes read; the target may transmit more before the connection closes. A duration ends new requests, not in-flight requests. Cancellation stops new requests; in-flight requests may finish or time out. The socket timeout is an inactivity timeout: DNS resolution or a continuously active response has no total wall-clock deadline, so an in-flight request can take longer. Latency covers the full redirect chain and bounded response read. Percentiles use the nearest-rank method and include completed HTTP responses, including HTTP errors; transport errors have no latency sample. Success means a final HTTP status from 200 through 399. Throughput counts completed attempts, including errors. The service holds at most 20 runs in memory and loses them on restart; export JSON or CSV before stopping. One run can execute at a time.

Verify locally with `python3 -m unittest discover -s tests`. Build a local, reproducible preview archive and checksum with `python3 build_release.py`; both are written to `dist/`. From that directory, check the archive with `shasum -a 256 -c SHA256SUMS` on macOS or `sha256sum -c SHA256SUMS` on Linux. The preview is not a published release. Fresh-platform checks and the browser matrix are still required before release; see [verification status](docs/VERIFICATION.md). The plan and static page have not been published by this change.

## Original distributed prototype

The original Python, Kafka, Flask and Streamlit files are preserved. They are a separate, unverified Linux prototype and are not used by the local service. Its historical setup requires Kafka and ZooKeeper, `kafka-client.py`, `orchestrator.py`, `target.py`, one or more `driver.py` processes, and `streamlit run frontend.py`. Its checked-in `driver_nodes.json` is empty, so setup and troubleshooting are required. The original performance claims had no reproducible test conditions and should not be used as benchmark results. 

## Optional remote drivers (experimental)

Run drivers on machines you control. Each driver and the controller authenticate each other with mutual TLS, so a driver rejects any client without a certificate from your CA. Tested only on loopback; a multi-machine or EC2 run has not been tested.

1. `./make_certs.sh certs DRIVER_HOST_OR_IP` creates a private CA plus controller and driver certificates (90 days). Keep `ca.key` private. Copy `ca.crt` and the matching cert and key to each machine. One driver certificate covers the one host name or IP you gave; run the script again per host.
2. On each driver machine: `python3 remote_driver.py --bind ADDRESS --port 9443 --cert driver.crt --key driver.key --ca ca.crt`. `--bind` is mandatory. Use a private interface and open the port only to the controller in your firewall. Never expose it to the internet.
3. On the controller write `drivers.json`: `{"ca":"ca.crt","cert":"controller.crt","key":"controller.key","drivers":["https://HOST:9443"]}`. Start with `DLT_DRIVERS=drivers.json ./start.sh`.
4. `POST /api/v1/distributed-runs` (same body and pairing token as `/api/v1/runs`) splits workers and count across drivers. `GET /api/v1/distributed-runs/<id>` returns aggregate and per-driver results; `POST .../cancel` cancels. A driver that misses 3 polls (about 3 seconds) is marked `lost`; the run ends `partial` with the results received so far. Latencies come from each driver's own monotonic clock, so clocks need no synchronisation.
5. Limits: at most 16 drivers; per-driver worker and request limits plus aggregate caps of 400 workers and 40000 requests. Kafka is not used; a single HTTPS control channel is enough at these limits, and no measured need for Kafka exists. Cost: you pay for any machines you run, so stop and delete them when done. Clean up with Ctrl-C on each driver and delete the certificate files.

The dashboard UI does not drive distributed runs yet; use the API.
