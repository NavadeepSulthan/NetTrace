"""JSON configuration adapter with fail-fast validation and useful errors."""
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

from nettrace.domain.models import TargetConfig


class ConfigurationError(ValueError):
    pass


class JsonTargetRepository:
    def load(self, path: Path) -> list[TargetConfig]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigurationError(f"Configuration file not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"Invalid JSON in {path} at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
        except OSError as exc:
            raise ConfigurationError(f"Could not read configuration {path}: {exc}") from exc

        if not isinstance(raw, list):
            raise ConfigurationError("Configuration root must be a JSON array of targets")
        targets: list[TargetConfig] = []
        seen: set[str] = set()
        for index, item in enumerate(raw):
            label = f"targets[{index}]"
            if not isinstance(item, dict):
                raise ConfigurationError(f"{label} must be a JSON object")
            url = item.get("url")
            if not isinstance(url, str) or not url.strip():
                raise ConfigurationError(f"{label}.url must be a non-empty string")
            url = url.strip()
            try:
                parsed = urlsplit(url)
                if parsed.scheme not in ("http", "https") or not parsed.hostname:
                    raise ConfigurationError(f"{label}.url must be an absolute HTTP(S) URL: {url!r}")
                if parsed.username is not None or parsed.password is not None:
                    raise ConfigurationError(f"{label}.url must not contain user-info credentials")
                _ = parsed.port
            except ConfigurationError:
                raise
            except ValueError as exc:
                raise ConfigurationError(f"{label}.url is malformed: {exc}") from exc
            if url in seen:
                raise ConfigurationError(f"Duplicate target URL: {url}")
            seen.add(url)

            threshold = item.get("latency_threshold_ms", 400.0)
            if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
                raise ConfigurationError(f"{label}.latency_threshold_ms must be a number")
            if not math.isfinite(float(threshold)) or threshold <= 0:
                raise ConfigurationError(f"{label}.latency_threshold_ms must be a finite number > 0")

            raw_ranges = item.get("acceptable_status_ranges", [[200, 399]])
            if not isinstance(raw_ranges, list) or not raw_ranges:
                raise ConfigurationError(f"{label}.acceptable_status_ranges must be a non-empty array of [min, max] pairs")
            ranges: list[tuple[int, int]] = []
            for r_index, status_range in enumerate(raw_ranges):
                if (not isinstance(status_range, list) or len(status_range) != 2 or
                    any(isinstance(code, bool) or not isinstance(code, int) for code in status_range)):
                    raise ConfigurationError(f"{label}.acceptable_status_ranges[{r_index}] must be [integer_min, integer_max]")
                low, high = status_range
                if low < 100 or high > 599 or low > high:
                    raise ConfigurationError(f"{label}.acceptable_status_ranges[{r_index}] must satisfy 100 <= min <= max <= 599")
                ranges.append((low, high))
            targets.append(TargetConfig(url, float(threshold), tuple(ranges)))
        return targets
