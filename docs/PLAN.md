# Plan: usable load testing from GitHub Pages

Status: local preview and experimental remote drivers (mutual TLS, tested on loopback only) implemented; browser matrix, fresh-machine acceptance, multi-machine driver tests and Pages publication remain open. Native macOS and Linux are the supported host targets.

## Outcome and boundaries

A visitor reads the instructions, downloads a versioned release, runs `./start.sh` on their own macOS or Linux computer, and runs an authorized HTTP load test. Only native macOS and Linux are supported; Windows and WSL are excluded. The command starts a local service and prints a local dashboard URL and a pairing code. It does not create cloud resources or ask for AWS keys. Document prerequisites, permissions, expected network traffic, costs for remote infrastructure, and how to stop and remove the tool. Mobile browsers are not an initial supported host.

Pages can offer a connection to this service after an explicit click, but success depends on browser local-network permissions and cross-origin rules. The primary fallback is the same UI served by the local service, which avoids a cross-origin connection. Test actual supported browser versions instead of promising universal support. HTTPS Pages can link to the downloadable release; it cannot itself execute the script on the visitor's machine.

## Approaches and choice

- **Local-first (recommended):** one machine runs the UI and load engine; optional remote drivers are added later. Low setup cost and no account or hosted backend. Limited by the machine and network.
- **User-owned EC2 first:** closer to a distributed setup, but needs cloud setup, networking, cost controls, and ongoing operations. Add only after local tests work.
- **Hosted multi-user service:** requires hosting, accounts, isolation and abuse management. Out of scope.

## Phase 1: reliable local engine

1. Define and document supported platforms and Python version; provide a pinned dependency file, a release archive, and `start.sh` that checks prerequisites, creates a virtual environment, installs dependencies, starts the service bound to `127.0.0.1`, and displays the local URL. Do not silently install system software or run privileged commands. Give clear errors and Ctrl-C shutdown. Provide checksums and an uninstall instruction. Do not recommend piping an unreviewed remote script directly into a shell.
2. Keep the existing code available as the original distributed prototype. Build a small local HTTP service and engine using existing Python libraries where reasonable. A single machine does not need Kafka, a broker, AWS, or multiple processes just to run a test. Be explicit that this is a new local execution path, not the existing distributed mode.
3. Define a versioned API: health/version; start test with URL, method initially GET, worker count, count or duration, and optional delay; status and results by random run ID; cancel. Mutations use POST, not GET. State is per run and bounded; keep results in memory for v1 and offer JSON/CSV export. Return clear validation errors and failed-run states.
4. Each worker sends sequential requests; workers run concurrently. Set connection/read timeouts, limit workers, duration, total requests, response body size, and redirect count. Avoid an unbounded queue. Record successes, HTTP status counts, transport errors, elapsed time, throughput and latency distribution (including p50/p95/p99). Define whether latency includes redirects, how requests in flight finish on cancellation, and how errors affect latency statistics. Initial conservative defaults and hard limits must be documented and tested; avoid claims about performance without measured evidence.
5. Require explicit confirmation that the user is authorized to test the target. Default to a local sample target; reject non-HTTP(S) URLs and embedded credentials. Set limits and make network impact visible before starting. Allow localhost because testing a local service is a key use case; make private-network targets explicit, and recheck destinations after redirects. A localhost-bound service can reach the user's network, so do not expose it to arbitrary websites.

## Phase 2: interface and local security

1. Add a local connection to the instruction page only when the real service is ready. Pair only after a user action; show connection and version status, configuration, running progress, cancel, results, errors, and export. Clearly report connection failures.
2. The service must bind only to loopback by default; validate the Host header against DNS rebinding, use a fresh high-entropy pairing token displayed by the launcher, require it for every API operation, and validate Origin with a narrow exact allowlist. Set restrictive CORS; never use wildcard origins with credentials. Treat token storage and browser compromise as design risks; do not put it in a URL or log it. Implement rate and size limits and do not rely on CORS as authentication. A local UI should use the same security model.
3. Test GitHub Pages-to-loopback connections on supported Chrome, Firefox and Safari releases, including permission denied, CORS errors and mixed-content behavior. Follow current browser documentation at implementation time. If unavailable or denied, explain how to open the same UI at localhost. Never request local-network access on page load.
4. Publish the instruction page via repository Pages using `docs/` or a Pages workflow, after review and explicit permission to publish. Verify repository subpath, HTTPS, accessibility and navigation on the actual published URL. Do not expose an API publicly by default.

## Phase 3: optional distributed drivers

Add only after Phase 1–2 work. Specify controller-to-driver protocol, registration, authentication, per-driver heartbeat, cancellation, result aggregation and clock-independent measurements. A user can run drivers on separate machines, including EC2, under their own control. Use mutually authenticated, encrypted connections for remote operation, explicit bind addresses and firewall instructions; never expose Kafka or an unauthenticated control API. Decide if Kafka is justified by a measured need before retaining it in the new path. Document deployment cost and clean-up. Distributed tests require aggregate rate and request limits, per-node errors, and partial-result handling.

## Verification and acceptance

- Fresh native macOS and Linux setup: start, pair, test a local fixture, cancel, export, stop, and restart without orphan processes. Verify missing prerequisites and offline failure messages.
- Unit checks: invalid configuration, worker limits, errors/timeouts, cancellation, result calculations and no false success. Integration test against a controlled local HTTP fixture; verify request count, concurrency limits and error reporting. Never benchmark an unrelated public service.
- Security checks: unauthenticated requests, unapproved origins, invalid Host, token guessing, disallowed redirects, malformed or oversized requests, and a website attempting to start a run without authorization.
- Browser checks: Pages over HTTPS to loopback, permission denied and localhost fallback in each supported browser. Verify the instruction page and real connection flow behave as documented.
- A release is ready only when these checks and setup instructions pass on documented versions. Benchmark numbers are separate, reproducible results with hardware and test conditions.

## Decisions to resolve during implementation

Supported browser versions; default limits; retention and export format; whether a user wants remote drivers; and whether a target-ownership verification mechanism is needed for any future shared deployment.

References: [GitHub Pages static hosting](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site), [Chrome local network access](https://developer.chrome.com/docs/modern-web-guidance/guides/security/local-network-access), [MDN CORS](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS).
