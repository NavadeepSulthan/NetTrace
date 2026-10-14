# Architecture and engineering decisions

## Runtime flow

```text
CLI / composition root
        |
        v
MonitorService -----> Prober port -----> SocketProber
        |                                  DNS -> TCP -> TLS -> HTTP
        +-----> health policy (pure domain function)
        |
        +-----> EventSink port ----------> JSONL file
        |
        +-----> IncidentReporter port ---> bounded Linux triage script
```

The domain has no filesystem, process, or networking dependency. Application code coordinates policy and I/O through protocol contracts. The CLI constructs concrete adapters. This makes the important logic testable without a network, Linux diagnostics, or a JSON file on disk.

## Decisions and trade-offs

### Measure all network phases on one connection

A common diagnostic mistake is timing DNS/TCP/TLS with a raw socket, then using a separate HTTP client for TTFB. That second client can perform DNS and connection setup again, so the values do not describe a single request path and the phase costs cannot be interpreted together. NetTrace sends HTTP/1.1 through the socket used for TCP/TLS measurement. The trade-off is that this deliberately small implementation does not support HTTP/2, automatic proxy settings, cookies, authentication, or complete response-body timing.

### Keep HTTP status separate from transport errors

Receiving HTTP 503 proves that the connection reached an HTTP server, even though the service-level check should fail. NetTrace therefore preserves the HTTP status without converting it into a transport exception. `assess()` applies endpoint-specific accepted status ranges and latency thresholds after the probe completes.

### Use failure streaks and recovery transitions

One failed request can be transient. A configurable consecutive-failure threshold avoids triggering diagnostics on every brief blip. A failure episode emits at most one incident until a healthy result marks recovery. This controls alert storms, but tracker state is in memory and resets when the process restarts; persistent incident state would be a follow-up enhancement.

### Choose JSONL for initial event history

One JSON object per line is inspectable with standard command-line tools and simple to ingest in later systems. It is not a metrics database and the current event file does not rotate itself. Application logs do rotate; event retention should be configured operationally for long-running deployments.

### Keep diagnostics bounded and best-effort

The triage script uses fixed commands and treats command output as data. It does not attempt automatic remediation or modify host networking. Some tools may be unavailable or blocked; failure to ping a service does not prove that its HTTP endpoint is unhealthy. The monitor still records its own HTTP-level observations.

## State model

```text
HEALTHY -- failed check --> DEGRADED (failure streak increments)
DEGRADED -- streak reaches threshold --> INCIDENT_REPORTED
INCIDENT_REPORTED -- more failures --> INCIDENT_REPORTED (no duplicate incident)
DEGRADED / INCIDENT_REPORTED -- healthy check --> HEALTHY (recovery event)
```

An incident report is attempted at most once during an uninterrupted failure episode. The health state is keyed by target URL, so target identities should be unique in `targets.json`.

## Latency semantics

| Field | Measurement |
|---|---|
| `dns_ms` | Wall time spent inside the system `getaddrinfo` call |
| `tcp_connect_ms` | Time spent trying resolved addresses until a connection succeeds or all attempts fail |
| `tls_handshake_ms` | TLS handshake and default certificate/hostname validation |
| `ttfb_ms` | Time from completed request transmission to the first byte of the HTTP response |
| `total_latency_ms` | End-to-end probe elapsed time, including phase work and response-header parsing |

The phases are useful diagnostics, not perfectly additive laboratory measurements. The operating-system resolver does not offer a portable per-call timeout through `getaddrinfo`, and a multi-address TCP attempt can use more than one timeout interval. These details are explicit because credible operational tooling must communicate what its numbers do—and do not—mean.
