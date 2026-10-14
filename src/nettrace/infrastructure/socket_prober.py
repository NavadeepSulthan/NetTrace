"""Standard-library HTTP(S) prober with phase timings on a single connection.

TTFB is measured from request transmission to the first response byte. The
connection used for DNS/TCP/TLS is then used for the HTTP request, avoiding the
misleading double-connect behavior of measuring with a separate urllib request.
"""
from __future__ import annotations

import socket
import ssl
import time
import urllib.parse

from nettrace.domain.models import ProbeResult

_MAX_HEADER_BYTES = 64 * 1024
_USER_AGENT = "NetTrace/1.0"


def _request_target(parsed: urllib.parse.SplitResult) -> str:
    path = urllib.parse.quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~%")
    query = urllib.parse.quote(parsed.query, safe="=&?/:;+,%@!$'()*-._~%")
    return f"{path}?{query}" if query else path


def _read_status_line(reader) -> tuple[int, int]:
    """Read and validate one status line; return status code and byte count."""
    line = reader.readline(8192)
    if not line or not line.endswith(b"\n"):
        raise ConnectionError("connection closed while reading HTTP status line")
    pieces = line.split(None, 2)
    if len(pieces) < 2 or not pieces[0].startswith(b"HTTP/"):
        raise ValueError(f"invalid HTTP status line: {line[:200]!r}")
    status = int(pieces[1])
    if not 100 <= status <= 599:
        raise ValueError(f"invalid HTTP status code: {status}")
    return status, len(line)


def _read_headers(reader, current_bytes: int) -> int:
    total = current_bytes
    while True:
        line = reader.readline(8192)
        if not line:
            raise ConnectionError("connection closed before HTTP headers completed")
        total += len(line)
        if total > _MAX_HEADER_BYTES:
            raise ValueError(f"HTTP headers exceeded {_MAX_HEADER_BYTES} bytes")
        if line in (b"\r\n", b"\n"):
            return total


class SocketProber:
    """Probe an HTTP(S) target with no runtime dependencies outside Python."""

    def probe(self, url: str, timeout: float = 4.0) -> ProbeResult:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        try:
            parsed = urllib.parse.urlsplit(url)
            host = parsed.hostname
            if parsed.scheme not in ("http", "https") or not host:
                raise ValueError("URL must be an absolute http:// or https:// URL")
            if parsed.username is not None or parsed.password is not None:
                raise ValueError("URL user-info credentials are not supported")
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"Invalid target URL {url!r}: {exc}") from exc

        try:
            network_host = host if ":" in host else host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError(f"Invalid internationalized hostname: {host!r}") from exc

        started = time.perf_counter()
        ip: str | None = None
        dns_ms = tcp_ms = tls_ms = ttfb_ms = 0.0
        status_code = 0
        error_phase: str | None = None
        error: str | None = None
        sock: socket.socket | ssl.SSLSocket | None = None
        reader = None
        addresses = []
        try:
            phase_started = time.perf_counter()
            try:
                addresses = socket.getaddrinfo(network_host, port, type=socket.SOCK_STREAM)
                dns_ms = (time.perf_counter() - phase_started) * 1000
                if not addresses:
                    raise OSError("DNS returned no addresses")
            except Exception as exc:
                dns_ms = (time.perf_counter() - phase_started) * 1000
                error_phase, error = "dns", str(exc)

            if error is None:
                phase_started = time.perf_counter()
                last_connect_error: Exception | None = None
                for family, socktype, proto, _, sockaddr in addresses:
                    candidate = socket.socket(family, socktype, proto)
                    candidate.settimeout(timeout)
                    try:
                        candidate.connect(sockaddr)
                        sock = candidate
                        ip = sockaddr[0]
                        break
                    except OSError as exc:
                        last_connect_error = exc
                        candidate.close()
                tcp_ms = (time.perf_counter() - phase_started) * 1000
                if sock is None:
                    error_phase, error = "tcp", str(last_connect_error or "all connection attempts failed")

            if error is None and sock is not None and parsed.scheme == "https":
                phase_started = time.perf_counter()
                try:
                    context = ssl.create_default_context()
                    context.set_alpn_protocols(["http/1.1"])
                    sock = context.wrap_socket(sock, server_hostname=network_host)
                    sock.settimeout(timeout)
                    tls_ms = (time.perf_counter() - phase_started) * 1000
                    ip = sock.getpeername()[0]
                except Exception as exc:
                    tls_ms = (time.perf_counter() - phase_started) * 1000
                    error_phase, error = "tls", str(exc)

            if error is None and sock is not None:
                try:
                    host_header = f"[{network_host}]" if ":" in network_host else network_host
                    if port != (443 if parsed.scheme == "https" else 80):
                        host_header = f"{host_header}:{port}"
                    request = (
                        f"GET {_request_target(parsed)} HTTP/1.1\r\n"
                        f"Host: {host_header}\r\n"
                        f"User-Agent: {_USER_AGENT}\r\n"
                        "Accept: */*\r\n"
                        "Connection: close\r\n\r\n"
                    ).encode("ascii", errors="strict")
                    reader = sock.makefile("rb")
                    sock.sendall(request)
                    first_byte_started = time.perf_counter()
                    try:
                        first_byte = reader.read(1)
                    finally:
                        # Preserve the wait duration even when a timeout/error occurs.
                        ttfb_ms = (time.perf_counter() - first_byte_started) * 1000
                    if not first_byte:
                        raise ConnectionError("peer closed without an HTTP response")
                    status_line = first_byte + reader.readline(8192)
                    if not status_line.endswith(b"\n"):
                        raise ConnectionError("incomplete HTTP status line")
                    pieces = status_line.split(None, 2)
                    if len(pieces) < 2 or not pieces[0].startswith(b"HTTP/"):
                        raise ValueError(f"invalid HTTP status line: {status_line[:200]!r}")
                    status_code = int(pieces[1])
                    if not 100 <= status_code <= 599:
                        raise ValueError(f"invalid HTTP status code: {status_code}")
                    header_bytes = _read_headers(reader, len(status_line))
                    # Handle interim responses such as 100 Continue / 103 Early Hints.
                    while 100 <= status_code < 200 and status_code != 101:
                        status_code, line_bytes = _read_status_line(reader)
                        header_bytes = _read_headers(reader, line_bytes)
                    if header_bytes > _MAX_HEADER_BYTES:
                        raise ValueError(f"HTTP headers exceeded {_MAX_HEADER_BYTES} bytes")
                except Exception as exc:
                    error_phase, error = "http", str(exc)
        finally:
            if reader is not None:
                try:
                    reader.close()
                except OSError:
                    pass
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

        healthy = error is None and 200 <= status_code < 400
        return ProbeResult(
            target=url,
            host=host,
            port=port,
            scheme=parsed.scheme,
            ip=ip,
            dns_ms=round(dns_ms, 2),
            tcp_connect_ms=round(tcp_ms, 2),
            tls_handshake_ms=round(tls_ms, 2),
            ttfb_ms=round(ttfb_ms, 2),
            total_latency_ms=round((time.perf_counter() - started) * 1000, 2),
            status_code=status_code,
            healthy=healthy,
            error_phase=error_phase,
            error=error,
        )
