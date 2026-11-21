"""Static site + API on one origin (src/space_team_genesis/web.py)."""

import io
import tempfile
import unittest
from pathlib import Path

try:
    from space_team_genesis.web import SiteWithApi
except ImportError:  # src/ layout: run with PYTHONPATH=src
    SiteWithApi = None


def call(app, path, method="GET"):
    captured = {}

    def start_response(status, headers):
        captured["status"], captured["headers"] = status, dict(headers)

    environ = {"PATH_INFO": path, "REQUEST_METHOD": method, "wsgi.input": io.BytesIO(b"")}
    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body


@unittest.skipIf(SiteWithApi is None, "space_team_genesis not importable (run with PYTHONPATH=src)")
class TestSiteWithApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.site = root / "site"
        (self.site / "data").mkdir(parents=True)
        (self.site / "index.html").write_text("<h1>home</h1>", encoding="utf-8")
        (self.site / "app.js").write_text("console.log(1)", encoding="utf-8")
        (self.site / "data" / "x.js").write_text("window.X=1", encoding="utf-8")
        (root / "secret.txt").write_text("private", encoding="utf-8")
        self.api_calls = []

        def api(environ, start_response):
            self.api_calls.append(environ["PATH_INFO"])
            start_response("200 OK", [("Content-Type", "application/json")])
            return [b'{"api": true}']

        self.app = SiteWithApi(api, self.site)

    def tearDown(self):
        self.tmp.cleanup()

    def test_serves_index_and_files_with_types(self):
        status, headers, body = call(self.app, "/")
        self.assertEqual((status, body), ("200 OK", b"<h1>home</h1>"))
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        status, headers, _ = call(self.app, "/app.js")
        self.assertTrue(headers["Content-Type"].startswith("text/javascript"))
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(call(self.app, "/data/x.js")[2], b"window.X=1")

    def test_api_paths_are_delegated_unchanged(self):
        status, headers, body = call(self.app, "/api/v1/episodes", method="POST")
        self.assertEqual(body, b'{"api": true}')
        self.assertEqual(self.api_calls, ["/api/v1/episodes"])

    def test_no_path_escape(self):
        for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/data/../../secret.txt", "/..\\secret.txt",
                     "/data/%2e%2e/%2e%2e/secret.txt", "/\x00index.html"):
            status, _, body = call(self.app, path)
            self.assertEqual(status, "404 Not Found", path)
            self.assertNotIn(b"private", body)

    def test_missing_file_and_directories(self):
        self.assertEqual(call(self.app, "/nope.html")[0], "404 Not Found")
        self.assertEqual(call(self.app, "/data/")[0], "404 Not Found")  # no listing, no index there

    def test_only_get_and_head(self):
        self.assertEqual(call(self.app, "/index.html", method="POST")[0], "405 Method Not Allowed")
        status, headers, body = call(self.app, "/index.html", method="HEAD")
        self.assertEqual((status, body), ("200 OK", b""))
        self.assertEqual(headers["Content-Length"], str(len("<h1>home</h1>")))

    def test_requires_index(self):
        with self.assertRaises(ValueError):
            SiteWithApi(lambda e, s: [b""], Path(self.tmp.name) / "missing")


if __name__ == "__main__":
    unittest.main()
