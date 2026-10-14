"""CLI composition root: wires concrete adapters to application services."""
from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import threading
from pathlib import Path

from nettrace import __version__
from nettrace.application.monitor import MonitorService
from nettrace.application.probe_endpoint import ProbeEndpoint
from nettrace.infrastructure.config_repository import ConfigurationError, JsonTargetRepository
from nettrace.infrastructure.incident_reporter import ShellIncidentReporter
from nettrace.infrastructure.jsonl_event_sink import JsonlEventSink
from nettrace.infrastructure.socket_prober import SocketProber


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _configure_logging(log_file: Path | None, level: str) -> logging.Logger:
    logger = logging.getLogger("nettrace")
    logger.handlers.clear()
    logger.setLevel(getattr(logging, level.upper()))
    logger.propagate = False
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    return logger


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nettrace",
        description="Measure HTTP(S) latency phases and monitor endpoint health.",
    )
    parser.add_argument("--version", action="version", version=f"NetTrace {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    probe = sub.add_parser("probe", help="probe a single HTTP(S) URL and print JSON")
    probe.add_argument("url")
    probe.add_argument("--timeout", type=float, default=4.0, help="socket operation timeout in seconds (default: 4)")

    validate = sub.add_parser("validate-config", help="validate targets.json without making network requests")
    validate.add_argument("--config", type=Path, default=Path("targets.json"))

    monitor = sub.add_parser("monitor", help="monitor configured targets")
    monitor.add_argument("--config", type=Path, default=Path("targets.json"))
    monitor.add_argument("--interval", type=float, default=15.0, help="seconds between cycle starts (default: 15)")
    monitor.add_argument("--workers", type=int, default=5, help="maximum concurrent probes (default: 5)")
    monitor.add_argument("--timeout", type=float, default=4.0, help="socket operation timeout (default: 4)")
    monitor.add_argument("--failure-threshold", type=int, default=2, help="consecutive failures before triage (default: 2)")
    monitor.add_argument("--log-file", type=Path, default=Path("nettrace.log"))
    monitor.add_argument("--events-file", type=Path, default=Path("nettrace-events.jsonl"))
    monitor.add_argument("--triage-dir", type=Path, default=Path("incidents"))
    monitor.add_argument("--no-triage", action="store_true", help="disable shell-based system triage")
    monitor.add_argument("--once", action="store_true", help="run one monitoring cycle, then exit")
    monitor.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    prober = SocketProber()

    if args.command == "probe":
        try:
            result = ProbeEndpoint(prober).execute(args.url, args.timeout)
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps(result.to_dict(), indent=2))
        return 0 if result.healthy else 1

    repository = JsonTargetRepository()
    try:
        targets = repository.load(args.config)
    except ConfigurationError as exc:
        parser.error(str(exc))

    if args.command == "validate-config":
        print(f"Configuration valid: {len(targets)} target(s) loaded from {args.config}")
        for target in targets:
            print(f"  - {target.url} (threshold={target.latency_threshold_ms:g} ms, ranges={target.acceptable_status_ranges})")
        return 0

    if args.interval <= 0 or args.timeout <= 0 or args.workers < 1 or args.failure_threshold < 1:
        parser.error("interval and timeout must be > 0; workers and failure-threshold must be >= 1")
    logger = _configure_logging(args.log_file, args.log_level)
    event_sink = JsonlEventSink(args.events_file)
    if args.no_triage:
        class DisabledReporter:
            def report(self, target_url: str, reason: str):
                logger.warning("Triage disabled; incident recorded for %s: %s", target_url, reason)
                return None
        reporter = DisabledReporter()
    else:
        reporter = ShellIncidentReporter(_project_root() / "scripts" / "triage.sh", args.triage_dir)
    service = MonitorService(
        prober,
        event_sink,
        reporter,
        timeout_seconds=args.timeout,
        workers=args.workers,
        failure_threshold=args.failure_threshold,
        logger=logger,
    )
    if args.once:
        service.run_once(targets)
        return 0

    stop_event = threading.Event()
    try:
        service.run_forever(targets, args.interval, stop_event)
    except KeyboardInterrupt:
        stop_event.set()
        logger.info("Shutdown requested")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
