"""Operational-logging tests for frontend.py (issue #91).

The service must leave a levelled, time-stamped operational trail:
  * the request handler emits one bounded line per request (method + path + status);
  * health/liveness probes are routed to DEBUG so a tight poller never floods the log;
  * the startup banner is emitted via the logger, not `print`.

No Ollama or network dependency: a real ThreadingHTTPServer is bound to an ephemeral
port for the per-request test, and the banner is exercised by running main() with the
server and config resolution mocked.
"""

import logging
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import frontend  # noqa: E402


class _Capture(logging.Handler):
    """A logging.Handler that records emitted records in memory."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


class TestRequestLogging(unittest.TestCase):
    """Handler.log_message must emit one bounded, levelled line per request."""

    def setUp(self):
        self.cap = _Capture()
        self.cap.setLevel(logging.DEBUG)
        frontend.log.addHandler(self.cap)
        self._old_level = frontend.log.level
        frontend.log.setLevel(logging.DEBUG)

    def tearDown(self):
        frontend.log.setLevel(self._old_level)
        frontend.log.removeHandler(self.cap)

    def _make_handler(self, path):
        """A Handler stand-in whose log_message can be driven without a socket."""
        handler = frontend.Handler.__new__(frontend.Handler)
        handler.client_address = ("127.0.0.1", 12345)
        handler.path = path
        handler._req_start = time.monotonic()
        return handler

    def test_request_emits_one_levelled_line_with_method_path_status(self):
        handler = self._make_handler("/api/overview?work=work1")
        # The stdlib calls: log_message('"%s" %s %s', requestline, str(code), str(size))
        handler.log_message('"%s" %s %s', "GET /api/overview?work=work1 HTTP/1.1", "200", "1234")
        self.assertEqual(len(self.cap.records), 1, "a single request yields one bounded line")
        record = self.cap.records[0]
        self.assertEqual(record.levelno, logging.INFO)
        message = record.getMessage()
        self.assertIn("GET", message)
        self.assertIn("/api/overview", message)
        self.assertIn("200", message)

    def test_health_path_routed_to_debug_not_info(self):
        handler = self._make_handler("/health")
        handler.log_message('"%s" %s %s', "GET /health HTTP/1.1", "200", "2")
        # Still one bounded line ...
        self.assertEqual(len(self.cap.records), 1)
        # ... but at DEBUG, so at the service's INFO level a poller sees nothing.
        self.assertEqual(self.cap.records[0].levelno, logging.DEBUG)
        info = [r for r in self.cap.records if r.levelno == logging.INFO]
        self.assertEqual(info, [], "health probe must not flood the INFO log")

    def test_one_request_emits_exactly_one_line(self):
        server = frontend.ThreadingHTTPServer(("127.0.0.1", 0), frontend.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
                resp.read()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        info = [r for r in self.cap.records if r.levelno == logging.INFO]
        self.assertEqual(len(info), 1, "a single request produces exactly one bounded line")
        self.assertIn("/", info[0].getMessage())


class TestStartupBanner(unittest.TestCase):
    """The startup banner must be emitted via the logger (levelled, time-stamped)."""

    def test_banner_emitted_via_logger(self):
        cap = _Capture()
        cap.setLevel(logging.DEBUG)
        frontend.log.addHandler(cap)
        old_level = frontend.log.level
        frontend.log.setLevel(logging.DEBUG)
        try:
            config = {
                "tracker_path": "tracker.json",
                "annotations_path": "annotations.jsonl",
                "exports_dir": Path("/tmp/visionkb-test-exports"),
                "thumbnails_dir": "thumbs",
            }
            resolve = mock.patch.object(
                frontend.config_loader, "resolve_environment", return_value=("testenv", config)
            )
            config_path = mock.patch.object(
                frontend.config_loader,
                "config_path_for",
                return_value="/tmp/visionkb-test/config.json",
            )
            server = mock.patch.object(frontend, "ThreadingHTTPServer", mock.MagicMock())
            argv = mock.patch("sys.argv", ["frontend.py"])
            with resolve, config_path, server, argv:
                frontend.main()
            info = [r for r in cap.records if r.levelno == logging.INFO]
            self.assertTrue(info, "the startup banner should be logged")
            joined = " ".join(r.getMessage() for r in info)
            self.assertIn("WebUI running at", joined)
            self.assertIn("tracker=", joined)
        finally:
            frontend.log.setLevel(old_level)
            frontend.log.removeHandler(cap)


if __name__ == "__main__":
    unittest.main()
