"""Ports used by application services (dependency inversion boundary)."""
from pathlib import Path
from typing import Protocol
from nettrace.domain.models import ProbeResult, TargetConfig


class Prober(Protocol):
    def probe(self, url: str, timeout: float = 4.0) -> ProbeResult: ...


class TargetRepository(Protocol):
    def load(self, path: Path) -> list[TargetConfig]: ...


class EventSink(Protocol):
    def emit(self, event_type: str, **fields: object) -> None: ...


class IncidentReporter(Protocol):
    def report(self, target_url: str, reason: str) -> Path | None: ...
