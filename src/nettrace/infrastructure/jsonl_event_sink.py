"""Append-only structured event stream, safe for concurrent probe workers."""
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JsonlEventSink:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def emit(self, event_type: str, **fields: object) -> None:
        event: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "event_type": event_type,
            **fields,
        }
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as output:
                output.write(line + "\n")
