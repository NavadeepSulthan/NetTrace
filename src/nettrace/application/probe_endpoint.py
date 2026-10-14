"""Use case for probing a single endpoint through an injected prober."""
from nettrace.domain.models import ProbeResult
from nettrace.domain.ports import Prober


class ProbeEndpoint:
    def __init__(self, prober: Prober) -> None:
        self._prober = prober

    def execute(self, url: str, timeout: float = 4.0) -> ProbeResult:
        return self._prober.probe(url, timeout=timeout)
