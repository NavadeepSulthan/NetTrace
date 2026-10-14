"""Monitoring orchestration and failure-state tracking."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit

from nettrace.domain.models import ProbeResult, TargetConfig
from nettrace.domain.policies import assess
from nettrace.domain.ports import EventSink, IncidentReporter, Prober


class ConsecutiveFailureTracker:
    """Fire once after N consecutive failures; reset on recovery to avoid alert floods."""

    def __init__(self, threshold: int) -> None:
        if threshold < 1:
            raise ValueError("failure threshold must be at least 1")
        self.threshold = threshold
        self._counts: dict[str, int] = {}
        self._alerted: set[str] = set()

    def observe(self, key: str, healthy: bool) -> tuple[int, bool, bool]:
        """Return (streak, alert_now, recovered_now)."""
        if healthy:
            recovered = self._counts.get(key, 0) > 0
            self._counts.pop(key, None)
            self._alerted.discard(key)
            return 0, False, recovered
        streak = self._counts.get(key, 0) + 1
        self._counts[key] = streak
        alert_now = streak >= self.threshold and key not in self._alerted
        if alert_now:
            self._alerted.add(key)
        return streak, alert_now, False


class MonitorService:
    def __init__(
        self,
        prober: Prober,
        events: EventSink,
        incidents: IncidentReporter,
        *,
        timeout_seconds: float = 4.0,
        workers: int = 5,
        failure_threshold: int = 2,
        logger: logging.Logger | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout must be greater than zero")
        if workers < 1:
            raise ValueError("workers must be at least 1")
        self._prober = prober
        self._events = events
        self._incidents = incidents
        self.timeout_seconds = timeout_seconds
        self.workers = workers
        self._failures = ConsecutiveFailureTracker(failure_threshold)
        self.log = logger or logging.getLogger("nettrace")

    def _emit(self, event_type: str, **fields: object) -> None:
        try:
            self._events.emit(event_type, **fields)
        except Exception:
            # Telemetry persistence must not take down the monitoring loop.
            self.log.exception("Could not persist event '%s'", event_type)

    @staticmethod
    def _internal_error(target: TargetConfig, exc: Exception) -> ProbeResult:
        parsed = urlsplit(target.url)
        return ProbeResult(
            target=target.url,
            host=parsed.hostname or "",
            port=parsed.port or (443 if parsed.scheme == "https" else 80),
            scheme=parsed.scheme,
            healthy=False,
            error_phase="internal",
            error=f"Unexpected probe failure: {type(exc).__name__}: {exc}",
        )

    def run_once(self, targets: list[TargetConfig]) -> list[ProbeResult]:
        if not targets:
            self.log.warning("No targets configured; nothing to probe")
            return []

        results_by_url: dict[str, ProbeResult] = {}
        pool_size = min(self.workers, len(targets))
        with ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix="nettrace-probe") as pool:
            futures = {pool.submit(self._prober.probe, target.url, self.timeout_seconds): target for target in targets}
            for future in as_completed(futures):
                target = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # Defensive boundary for injected or unforeseen failures.
                    self.log.exception("Probe worker crashed for %s", target.url)
                    result = self._internal_error(target, exc)
                results_by_url[target.url] = result

        ordered_results: list[ProbeResult] = []
        for target in targets:
            result = results_by_url[target.url]
            ordered_results.append(result)
            decision = assess(target, result)
            streak, alert_now, recovered_now = self._failures.observe(target.url, decision.is_healthy)
            self._emit(
                "probe_result",
                target=target.url,
                assessment="healthy" if decision.is_healthy else "degraded",
                reason=decision.reason,
                consecutive_failures=streak,
                result=result.to_dict(),
            )
            self.log.info(
                "Probe %s | status=%s dns=%.2fms tcp=%.2fms tls=%.2fms ttfb=%.2fms total=%.2fms | %s%s",
                target.url,
                result.status_code or "N/A",
                result.dns_ms,
                result.tcp_connect_ms,
                result.tls_handshake_ms,
                result.ttfb_ms,
                result.total_latency_ms,
                "HEALTHY" if decision.is_healthy else "DEGRADED",
                f" ({decision.reason})" if decision.reason else "",
            )
            if recovered_now:
                self.log.info("Recovery detected for %s", target.url)
                self._emit("target_recovered", target=target.url, result=result.to_dict())
            if alert_now:
                reason = decision.reason or "Consecutive health-check failures"
                self.log.warning("Incident threshold reached for %s: %s", target.url, reason)
                self._emit(
                    "incident_triggered",
                    target=target.url,
                    reason=reason,
                    consecutive_failures=streak,
                    result=result.to_dict(),
                )
                try:
                    report = self._incidents.report(target.url, reason)
                    if report:
                        self.log.warning("Triage report written to %s", report)
                        self._emit("triage_report_created", target=target.url, report_path=str(report))
                except Exception:
                    self.log.exception("Incident reporter failed for %s", target.url)
                    self._emit("triage_report_failed", target=target.url, reason=reason)
        return ordered_results

    def run_forever(self, targets: list[TargetConfig], interval_seconds: float, stop_event: threading.Event) -> None:
        if interval_seconds <= 0:
            raise ValueError("polling interval must be greater than zero")
        self.log.info(
            "Starting NetTrace: targets=%d interval=%.1fs workers=%d failure_threshold=%d",
            len(targets), interval_seconds, self.workers, self._failures.threshold,
        )
        while not stop_event.is_set():
            cycle_started = time.monotonic()
            self.run_once(targets)
            remaining = max(0.0, interval_seconds - (time.monotonic() - cycle_started))
            stop_event.wait(remaining)
        self.log.info("NetTrace monitor stopped")
