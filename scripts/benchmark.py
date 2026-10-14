#!/usr/bin/env python3
"""Repeat one probe and report phase-by-phase latency distributions as JSON."""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from nettrace.infrastructure.socket_prober import SocketProber  # noqa: E402


PHASES = ("dns_ms", "tcp_connect_ms", "tls_handshake_ms", "ttfb_ms", "total_latency_ms")


def percentile(values: list[float], percentile_value: float) -> float:
    """Nearest-rank percentile; deterministic even for small sample counts."""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = max(1, math.ceil(percentile_value * len(ordered)))
    return ordered[rank - 1]


def distribution(values: list[float]) -> dict[str, float]:
    return {
        "min": round(min(values), 2),
        "mean": round(statistics.fmean(values), 2),
        "p50": round(percentile(values, 0.50), 2),
        "p95": round(percentile(values, 0.95), 2),
        "p99": round(percentile(values, 0.99), 2),
        "max": round(max(values), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="HTTP(S) target to probe repeatedly")
    parser.add_argument("--count", type=int, default=30, help="number of measured samples (default: 30)")
    parser.add_argument("--warmup", type=int, default=3, help="warm-up samples discarded before measurement (default: 3)")
    parser.add_argument("--timeout", type=float, default=4.0, help="socket operation timeout (default: 4)")
    args = parser.parse_args()
    if args.count < 1 or args.warmup < 0 or args.timeout <= 0:
        parser.error("count and timeout must be greater than zero; warmup must be >= 0")

    prober = SocketProber()
    for _ in range(args.warmup):
        prober.probe(args.url, timeout=args.timeout)
    results = [prober.probe(args.url, timeout=args.timeout) for _ in range(args.count)]
    successful = [result for result in results if result.healthy]
    phase_stats = {
        phase.removesuffix("_ms"): distribution([getattr(result, phase) for result in results])
        for phase in PHASES
    }
    summary = {
        "target": args.url,
        "sample_count": len(results),
        "warmup_samples_discarded": args.warmup,
        "successful_samples": len(successful),
        "success_rate_percent": round(100 * len(successful) / len(results), 2),
        "status_codes": {str(code): count for code, count in sorted(Counter(r.status_code for r in results).items())},
        "failures_by_phase": dict(sorted(Counter(r.error_phase for r in results if r.error_phase).items())),
        "phase_latency_ms": phase_stats,
        "warning": "Observed values depend on target and environment; this script does not establish universal performance claims.",
    }
    print(json.dumps(summary, indent=2))
    return 0 if len(successful) == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
