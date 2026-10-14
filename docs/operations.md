# Operations runbook

## Before enabling continuous monitoring

1. Replace the sample targets with endpoints you own or are authorized to monitor.
2. Pick a healthy status range appropriate for each endpoint. For example, a service that intentionally returns 204 can keep the default 2xx/3xx range, while a custom status contract can use `acceptable_status_ranges`.
3. Choose the threshold based on observed baseline data rather than an arbitrary industry number. Start with `scripts/benchmark.py`, run it from the deployment network, and repeat at different times.
4. Test the configuration without network calls: `nettrace validate-config --config targets.json`.
5. Run one cycle with `--once --no-triage`; inspect both `nettrace.log` and `nettrace-events.jsonl`.

## Interpret a degraded target

- **`dns` error:** resolver failure, malformed/unresolvable name, or DNS infrastructure issue. Check resolver configuration and resolve the hostname independently.
- **`tcp` error:** no resolved address could be connected to within the socket timeout. Check routing, listener state, firewall rules, and port configuration.
- **`tls` error:** handshake/certificate validation failed. Check validity dates, hostname/SAN, certificate chain, supported TLS versions, and interception proxies.
- **HTTP status outside the accepted range:** transport succeeded far enough to receive an HTTP response, but application health policy rejected its status.
- **Latency threshold breach:** response was acceptable by status, but total observed probe time was above the configured SLA threshold. Compare DNS/TCP/TLS/TTFB phases before attributing the delay.

## Incident behavior

A target must fail `--failure-threshold` consecutive cycles before the automatic triage command runs. Further failures do not create another report while the incident remains open. A successful cycle emits a recovery event and rearms incident triggering. On Windows or when you do not want OS diagnostics, use `--no-triage`.

## Evidence for a performance claim

Record the environment, target, network path, date/time, sample count, warm-up count, timeout, and exact command. Repeat the run several times and compare phase distributions instead of reporting the best run. Avoid comparing results collected from different hosts or networks as if they were controlled experiments. Do not infer CPU, memory, or mean-time-to-repair improvements from the probe output; collect a separate before/after dataset for those claims.
