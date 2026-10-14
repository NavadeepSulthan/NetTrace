from pathlib import Path

from nettrace.application.monitor import ConsecutiveFailureTracker, MonitorService
from nettrace.domain.models import ProbeResult, TargetConfig


class FakeProber:
    def __init__(self, status=200, latency=10, error=None):
        self.status = status
        self.latency = latency
        self.error = error

    def probe(self, url, timeout=4.0):
        return ProbeResult(
            target=url, host="example.com", port=443, scheme="https",
            total_latency_ms=self.latency, status_code=self.status,
            healthy=self.error is None and 200 <= self.status < 400,
            error_phase="dns" if self.error else None, error=self.error,
        )


class MemoryEvents:
    def __init__(self):
        self.records = []

    def emit(self, event_type, **fields):
        self.records.append((event_type, fields))


class FakeReporter:
    def __init__(self):
        self.calls = []

    def report(self, target_url, reason):
        self.calls.append((target_url, reason))
        return Path("fake-report.log")


def test_failure_tracker_alerts_once_and_resets_after_recovery():
    tracker = ConsecutiveFailureTracker(2)
    assert tracker.observe("a", False) == (1, False, False)
    assert tracker.observe("a", False) == (2, True, False)
    assert tracker.observe("a", False) == (3, False, False)
    assert tracker.observe("a", True) == (0, False, True)
    assert tracker.observe("a", False) == (1, False, False)
    assert tracker.observe("a", False) == (2, True, False)


def test_monitor_triggers_triage_after_configured_failures():
    target = TargetConfig("https://example.com", latency_threshold_ms=100)
    events, reporter = MemoryEvents(), FakeReporter()
    service = MonitorService(FakeProber(status=503), events, reporter, failure_threshold=2, workers=1)
    service.run_once([target])
    assert reporter.calls == []
    service.run_once([target])
    assert len(reporter.calls) == 1
    assert any(event == "incident_triggered" for event, _ in events.records)
    # Still degraded: no repeated incident storm.
    service.run_once([target])
    assert len(reporter.calls) == 1


def test_monitor_emits_recovery_and_rearms_after_recovery():
    target = TargetConfig("https://example.com", latency_threshold_ms=100)
    events, reporter = MemoryEvents(), FakeReporter()
    prober = FakeProber(status=503)
    service = MonitorService(prober, events, reporter, failure_threshold=1, workers=1)
    service.run_once([target])
    prober.status = 200
    service.run_once([target])
    assert any(event == "target_recovered" for event, _ in events.records)
    prober.status = 503
    service.run_once([target])
    assert len(reporter.calls) == 2


def test_latency_threshold_is_part_of_health_policy():
    target = TargetConfig("https://example.com", latency_threshold_ms=5)
    events, reporter = MemoryEvents(), FakeReporter()
    service = MonitorService(FakeProber(status=200, latency=50), events, reporter, failure_threshold=1)
    result = service.run_once([target])[0]
    assert result.healthy
    assert len(reporter.calls) == 1
    record = next(fields for event, fields in events.records if event == "incident_triggered")
    assert "Latency" in record["reason"]
