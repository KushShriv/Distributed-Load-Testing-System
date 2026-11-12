# Preview verification

Status: incomplete; this is not a release acceptance record. Checks performed on 8 November 2026.

- Native macOS 27.2, Apple silicon: in a fresh unmodified source copy on the default port 8765, the launcher created a virtual environment; the service paired, completed five requests to its local sample, exported CSV, cancelled a run, stopped with Ctrl-C, restarted, and stopped cleanly.
- Automated checks: `python3 -m unittest discover -s tests` covers launcher failures and platform selection, archive reproducibility, configuration, controlled loopback fixture runs, request counts and concurrency, cancellation, results, redirects, and API security. These checks do not prove native Linux or browser support.
- Native Linux: not tested. No native Linux host or available Linux VM/container runtime was found in this workspace. Simulated platform-name tests are not native acceptance.
- Browser acceptance: an in-app browser paired and completed a three-request local sample run. Native Chrome testing stalled; Firefox was unavailable; native Safari was not tested. Published HTTPS Pages-to-loopback behavior, denied permissions, CORS and mixed content must be checked in documented Chrome, Firefox and Safari versions. A live published page and explicit permission to publish are required for the published URL check.
- Request timeouts: the five-second socket timeout limits inactivity; DNS resolution and a continuously active response do not have a total wall-clock deadline. Cancellation and duration can therefore wait longer than the nominal timeout for in-flight requests. Do not claim a hard end-to-end request deadline.
- Distributed drivers are not implemented. The draft design is in [DISTRIBUTED-DESIGN.md](DISTRIBUTED-DESIGN.md); the plan gates implementation on local and browser acceptance.
