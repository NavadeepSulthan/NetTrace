import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nettrace.infrastructure.socket_prober import SocketProber


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path.startswith("/slow"):
            time.sleep(0.12)
        if self.path.startswith("/503"):
            status = 503
        elif self.path.startswith("/redirect"):
            status = 302
        else:
            status = 200
        body = b"ok"
        self.send_response(status)
        if status == 302:
            self.send_header("Location", "/health")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@pytest.fixture()
def local_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_probe_measures_phases_on_local_server(local_server):
    result = SocketProber().probe(local_server + "/health", timeout=1.0)
    assert result.healthy is True
    assert result.status_code == 200
    assert result.host == "127.0.0.1"
    assert result.ip == "127.0.0.1"
    assert result.dns_ms >= 0
    assert result.tcp_connect_ms >= 0
    assert result.ttfb_ms >= 0
    assert result.total_latency_ms >= result.ttfb_ms
    assert result.error is None


def test_probe_preserves_server_error_status(local_server):
    result = SocketProber().probe(local_server + "/503", timeout=1.0)
    assert result.status_code == 503
    assert result.healthy is False
    assert result.error is None


def test_ttfb_reflects_delayed_response_headers(local_server):
    result = SocketProber().probe(local_server + "/slow", timeout=1.0)
    assert result.healthy is True
    assert result.ttfb_ms >= 80


def test_connection_failure_is_returned_as_result():
    result = SocketProber().probe("http://127.0.0.1:1/", timeout=0.2)
    assert result.healthy is False
    assert result.error_phase == "tcp"
    assert result.error


def test_invalid_url_is_rejected():
    with pytest.raises(ValueError, match="absolute http"):
        SocketProber().probe("file:///etc/passwd")


def test_url_with_user_info_is_rejected():
    with pytest.raises(ValueError, match="user-info"):
        SocketProber().probe("https://user:secret@example.com/")


def test_redirect_is_reported_without_following_it(local_server):
    result = SocketProber().probe(local_server + "/redirect", timeout=1.0)
    assert result.status_code == 302
    assert result.healthy is True
    assert result.target.endswith("/redirect")
