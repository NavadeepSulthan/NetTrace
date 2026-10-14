"""Domain models: deliberately independent of sockets, files, and CLI details."""
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ProbeResult:
    target: str
    host: str
    port: int
    scheme: str
    ip: str | None = None
    dns_ms: float = 0.0
    tcp_connect_ms: float = 0.0
    tls_handshake_ms: float = 0.0
    ttfb_ms: float = 0.0
    total_latency_ms: float = 0.0
    status_code: int = 0
    healthy: bool = False
    error_phase: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TargetConfig:
    url: str
    latency_threshold_ms: float = 400.0
    acceptable_status_ranges: tuple[tuple[int, int], ...] = ((200, 399),)

    def accepts_status(self, status_code: int) -> bool:
        return any(low <= status_code <= high for low, high in self.acceptable_status_ranges)


@dataclass(frozen=True, slots=True)
class Assessment:
    is_healthy: bool
    reason: str | None = None
