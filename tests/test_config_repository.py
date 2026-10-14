import json
from pathlib import Path

import pytest

from nettrace.infrastructure.config_repository import ConfigurationError, JsonTargetRepository


def write_config(tmp_path: Path, payload) -> Path:
    path = tmp_path / "targets.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_loads_defaults_and_status_ranges(tmp_path):
    path = write_config(tmp_path, [{"url": "https://example.com", "latency_threshold_ms": 250}])
    targets = JsonTargetRepository().load(path)
    assert len(targets) == 1
    assert targets[0].latency_threshold_ms == 250.0
    assert targets[0].accepts_status(200)
    assert targets[0].accepts_status(399)
    assert not targets[0].accepts_status(500)


def test_loads_multiple_ranges(tmp_path):
    path = write_config(tmp_path, [{"url": "http://example.com", "acceptable_status_ranges": [[200, 204], [301, 304]]}])
    target = JsonTargetRepository().load(path)[0]
    assert target.accepts_status(204)
    assert target.accepts_status(302)
    assert not target.accepts_status(299)


@pytest.mark.parametrize("payload", [
    {},
    [{"url": "file:///tmp/a"}],
    [{"url": "https://[broken-ipv6/"}],
    [{"url": "https://example.com", "latency_threshold_ms": 0}],
    [{"url": "https://example.com", "acceptable_status_ranges": [[400, 200]]}],
    [{"url": "https://example.com", "acceptable_status_ranges": []}],
    [{"url": "https://u:p@example.com"}],
])
def test_invalid_configs_fail_fast(tmp_path, payload):
    path = write_config(tmp_path, payload)
    with pytest.raises(ConfigurationError):
        JsonTargetRepository().load(path)


def test_duplicate_urls_are_rejected(tmp_path):
    path = write_config(tmp_path, [{"url": "https://example.com"}, {"url": "https://example.com"}])
    with pytest.raises(ConfigurationError, match="Duplicate"):
        JsonTargetRepository().load(path)


def test_bad_json_has_location_in_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("[ nope", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="line 1"):
        JsonTargetRepository().load(path)
