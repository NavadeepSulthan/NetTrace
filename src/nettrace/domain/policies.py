"""Pure health policy. No I/O is performed here."""
from nettrace.domain.models import Assessment, ProbeResult, TargetConfig


def assess(target: TargetConfig, result: ProbeResult) -> Assessment:
    if result.error:
        phase = f"{result.error_phase}: " if result.error_phase else ""
        return Assessment(False, f"{phase}{result.error}")
    if not target.accepts_status(result.status_code):
        return Assessment(False, f"HTTP {result.status_code} is outside the configured healthy status ranges")
    if result.total_latency_ms > target.latency_threshold_ms:
        return Assessment(
            False,
            f"Latency {result.total_latency_ms:.2f} ms exceeded threshold "
            f"{target.latency_threshold_ms:.2f} ms",
        )
    return Assessment(True)
