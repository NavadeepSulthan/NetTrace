"""Bounded, shell-based Linux triage adapter; the use case depends only on a port."""
import logging
import subprocess
from pathlib import Path
from urllib.parse import urlsplit


class ShellIncidentReporter:
    def __init__(self, script_path: Path, output_dir: Path, timeout_seconds: float = 25.0) -> None:
        self.script_path = script_path
        self.output_dir = output_dir
        self.timeout_seconds = timeout_seconds
        self.log = logging.getLogger("nettrace")

    def report(self, target_url: str, reason: str) -> Path | None:
        host = urlsplit(target_url).hostname or target_url
        if not self.script_path.is_file():
            self.log.error("Triage script not found: %s", self.script_path)
            return None
        try:
            result = subprocess.run(
                ["bash", str(self.script_path), host, reason, str(self.output_dir)],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.log.error("Could not complete triage for %s: %s", target_url, exc)
            return None
        if result.returncode != 0:
            self.log.error("Triage script exited %s: %s", result.returncode, result.stderr.strip())
            return None
        for line in reversed(result.stdout.splitlines()):
            prefix = "Report generated at: "
            if line.startswith(prefix):
                candidate = Path(line[len(prefix):].strip())
                return candidate if candidate.is_file() else None
        self.log.warning("Triage finished without returning a report path: %s", result.stdout.strip())
        return None
