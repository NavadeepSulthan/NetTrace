import json
import threading
from pathlib import Path

from nettrace.infrastructure.jsonl_event_sink import JsonlEventSink


def test_jsonl_sink_appends_parseable_events(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    sink = JsonlEventSink(path)
    sink.emit("probe_result", target="https://example.com", latency_ms=12.5)
    sink.emit("incident_triggered", target="https://example.com", reason="HTTP 503")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert rows[0]["event_type"] == "probe_result"
    assert rows[1]["reason"] == "HTTP 503"
    assert rows[0]["timestamp"].endswith("+00:00")


def test_jsonl_sink_keeps_concurrent_appends_as_complete_lines(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    sink = JsonlEventSink(path)

    def write_events(worker_id: int):
        for sequence in range(25):
            sink.emit("test", worker=worker_id, sequence=sequence)

    threads = [threading.Thread(target=write_events, args=(worker,)) for worker in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 100
    records = [json.loads(line) for line in lines]
    assert len({(record["worker"], record["sequence"]) for record in records}) == 100
