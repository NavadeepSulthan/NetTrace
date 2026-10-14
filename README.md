# NetTrace — Network Latency Diagnostics & Incident Triage

NetTrace is a small, dependency-free HTTP(S) diagnostics and monitoring daemon built to demonstrate practical networking, observability, testing, and operational engineering. It measures DNS resolution, TCP connection setup, TLS negotiation, time to first response byte (TTFB), and total elapsed time. A configurable monitor evaluates health and latency, records structured JSONL events, and runs bounded Linux triage after repeated failures.

> **Engineering note:** The values produced by NetTrace are measurements, not guarantees. Timer resolution is not measurement accuracy, and latency varies by network, resolver, remote server, machine load, and test setup. Use `scripts/benchmark.py` to collect evidence before putting performance numbers on a resume.

## Features

- **Single-connection phase timing:** DNS, TCP, TLS, HTTP first-byte timing, and total time are measured without silently opening a second HTTP connection for the TTFB phase.
- **Real HTTP status handling:** HTTP 4xx/5xx responses are recorded as responses, not confused with transport exceptions. Redirects are not followed, so the result refers to the configured URL.
- **Concurrent monitoring:** Bounded worker pool probes multiple endpoints without serially waiting for each target's network timeout.
- **Health policy:** Per-target latency threshold and configurable acceptable HTTP status ranges.
- **Failure hysteresis:** Triage starts only after N consecutive failed checks; one incident is emitted per failure episode, then the target must recover before another incident can trigger.
- **Structured event history:** Append-only JSON Lines events for probe results, incidents, recoveries, and triage reports.
- **Bounded logs and diagnostics:** Rotating application logs; the Linux triage script captures system load, memory, socket summary, DNS configuration, ping, and route trace with command timeouts where available.
- **Automated quality checks:** Tests run against a local HTTP server, so the test suite does not depend on public internet availability.
- **No runtime third-party packages:** Python standard library only. `pytest` and `ruff` are optional development dependencies.

## Clean architecture

```text
src/nettrace/
├── domain/
│   ├── models.py             # Target, probe result, assessment
│   ├── policies.py           # Pure health evaluation rules
│   └── ports.py              # Prober, event-sink, incident-reporter contracts
├── application/
│   ├── probe_endpoint.py     # Single-probe use case
│   └── monitor.py            # Monitoring orchestration and failure state
├── infrastructure/
│   ├── socket_prober.py      # DNS/TCP/TLS/HTTP adapter
│   ├── config_repository.py  # JSON config adapter and validation
│   ├── jsonl_event_sink.py   # Structured event persistence
│   └── incident_reporter.py # Bounded shell-script adapter
└── cli/
    └── main.py               # CLI and dependency wiring

scripts/
├── triage.sh                 # Linux system triage
└── benchmark.py              # Reproducible repeated-probe summary
tests/                         # Unit and local-server integration tests
.github/workflows/ci.yml       # Lint, tests, config and shell checks
```

The **domain** contains models and health policy without I/O. The **application** layer coordinates use cases and depends on contracts. The **infrastructure** layer implements those contracts using sockets, JSON files, and the triage script. The **CLI** is the composition root that wires concrete adapters together. Tests inject fakes at the port boundaries, and the network integration tests use a local ephemeral HTTP server.

## Requirements

- Python 3.10 or newer
- Linux is required for the automated system triage script. Single probes and tests also work on Windows/macOS.
- Optional for development: `pytest` and `ruff`

## Install and run

### 1. Create a virtual environment

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

The project has no runtime third-party dependencies. To install without development tools, use `python -m pip install -e .`.

### 2. Validate configuration

```bash
nettrace validate-config --config targets.json
```

### 3. Probe one endpoint

```bash
nettrace probe https://www.example.com --timeout 4
```

The command prints JSON and exits with status `0` for a 2xx/3xx response and `1` for a failed probe or a non-2xx/3xx status. It will not follow redirects.

### 4. Run one monitoring cycle

```bash
nettrace monitor --config targets.json --once --no-triage
```

This is useful for a local smoke test. It writes the application log and structured events to `nettrace.log` and `nettrace-events.jsonl`. `--no-triage` is useful off Linux or in development; the incident is still recorded.

### 5. Start continuous monitoring

```bash
nettrace monitor \
  --config targets.json \
  --interval 15 \
  --workers 5 \
  --timeout 4 \
  --failure-threshold 2 \
  --log-file nettrace.log \
  --events-file nettrace-events.jsonl \
  --triage-dir incidents
```

Stop with `Ctrl+C`. The interval is measured between cycle starts; if one cycle exceeds the interval, the next starts as soon as the cycle completes rather than introducing extra drift. Monitoring itself does not require elevated privileges. Some operating-system diagnostics may be limited by permissions or missing utilities.

### 6. Get a measured latency distribution

```bash
python scripts/benchmark.py https://www.example.com --count 50 --timeout 4
```

This reports sample count, success rate, min/mean/median/p95/p99/max total latency. It makes actual measurements; results are intentionally not hard-coded into the repository.

## Configuration

`targets.json` is a JSON array. `latency_threshold_ms` defaults to 400 ms. `acceptable_status_ranges` defaults to `[[200, 399]]`, with inclusive lower and upper bounds. More than one range is allowed.

```json
[
  {
    "url": "https://status.example.com/health",
    "latency_threshold_ms": 250,
    "acceptable_status_ranges": [[200, 204], [301, 304]]
  }
]
```

Configuration validation rejects malformed URLs, URL user-info credentials, duplicate target URLs, non-positive/non-finite latency thresholds, and invalid HTTP status ranges. The sample configuration retains the three endpoints from the initial project concept; the `httpstat.us/503` target is expected to trigger health failure when reachable and returning 503, so replace it with your own service's health endpoint for routine monitoring.

## Output and behavior

- `nettrace.log*`: human-readable rotating application logs.
- `nettrace-events.jsonl`: machine-readable event history, one JSON object per line.
- `incidents/triage_*.log`: best-effort host diagnostics after the configured failure streak.

Event types include `probe_result`, `incident_triggered`, `target_recovered`, `triage_report_created`, and `triage_report_failed`. JSONL is a lightweight event history, not a full metrics database; production deployment should define retention and disk-space limits.

## Measurement semantics and limitations

- `dns_ms` measures the system resolver call through `getaddrinfo`; operating-system name resolution may not obey the socket timeout exactly.
- `tcp_connect_ms` measures connection attempts until an address succeeds or all fail. On multi-address hosts, failed address attempts can add to this phase.
- `tls_handshake_ms` includes TLS negotiation and certificate validation, using the default trust store and hostname validation.
- `ttfb_ms` measures from request transmission to the first response byte on the same connection. The response status and headers are then parsed; the body is not downloaded.
- `total_latency_ms` includes the sequential probe phases and result processing. It is not a substitute for browser page-load timing or full response download time.
- The monitor opens direct sockets and deliberately does not transparently apply HTTP proxy environment variables. HTTP/1.1 is used. Targets requiring special authentication, client certificates, or custom headers need an explicit extension.
- A successful TCP/TLS session followed by a 500 response is an HTTP-level failure, not a DNS/TCP/TLS transport error. The health policy checks both status ranges and total latency.
- The triage script is designed for Linux. Ping/traceroute output can be blocked by firewalls; a failed diagnostic does not prove the monitored service is down.

## Test and quality checks

```bash
ruff check .
pytest -q
bash -n scripts/triage.sh
nettrace validate-config --config targets.json
```

The tests cover a local HTTP server (2xx, 5xx, delayed response), TCP failure handling, invalid URLs, configuration validation, health policy, incident thresholds, and recovery/re-arming. No external website is contacted during unit/integration tests.

## Resume use

Suggested wording after you have personally run the project and verified the behavior:

- **NetTrace — Network Latency Diagnostics & Incident Triage** | Python, TCP/IP, TLS, Linux, Bash, pytest, GitHub Actions
- Built a dependency-free HTTP(S) probe measuring DNS, TCP, TLS, first-response-byte, and end-to-end latency on a single connection; added configurable health and SLA checks.
- Implemented a concurrent monitoring service with consecutive-failure gating, recovery detection, structured JSONL events, and bounded Linux incident diagnostics.
- Added local-server integration tests, configuration validation, and CI checks; used a repeatable benchmark utility to report p95/p99 latency and success rates.

Do **not** claim a fixed memory footprint, CPU percentage, sub-0.1 ms measurement accuracy, or a specific MTTR reduction until you measure and can explain the exact setup. A strong interview story is to show the test design, explain why phase timing must reuse the same socket, discuss resolver timeout limitations, and present benchmark conditions and raw results honestly.

## Roadmap for a stronger portfolio version

1. Add a Prometheus exporter or OpenTelemetry metrics adapter behind a port.
2. Add configurable retry/backoff and alert integrations (webhook/email) with secrets supplied through environment variables.
3. Add a retention/rotation policy for JSONL events and incident reports.
4. Add optional TLS expiry checks and certificate metadata.
5. Run a controlled benchmark matrix (local network, remote endpoints, concurrency levels) and commit a methodology document—not fabricated headline numbers.

## License

MIT. See `LICENSE`.
