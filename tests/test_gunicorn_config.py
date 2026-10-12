"""gunicorn.conf.py: access-log lines must not carry addresses.

/building?address=... and /area?street=... put a tenant's address in the
query string, and Render keeps the access log. Render's start command runs
gunicorn from the repository root, which loads ./gunicorn.conf.py.
"""

import os
import runpy
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AccessLogFormatTests(unittest.TestCase):
    def setUp(self):
        self.config = runpy.run_path(os.path.join(_ROOT, "gunicorn.conf.py"))

    def test_path_only_no_query_referer_or_user_agent(self):
        fmt = self.config["access_log_format"]
        self.assertIn("%(U)s", fmt)
        for leaky in ("%(r)s", "%(q)s", "%(f)s", "%(a)s", "%({"):
            self.assertNotIn(leaky, fmt)

    def test_the_access_log_still_goes_to_stdout(self):
        self.assertEqual(self.config["accesslog"], "-")

    def test_a_rendered_line_drops_the_address(self):
        try:
            from gunicorn.glogging import Logger
            from gunicorn.config import Config
        except ImportError:  # gunicorn is only needed on the server
            self.skipTest("gunicorn not installed")
        import datetime
        from types import SimpleNamespace
        cfg = Config()
        cfg.set("access_log_format", self.config["access_log_format"])
        atoms = Logger(cfg).atoms(
            SimpleNamespace(status="200 OK", headers=[], sent=123, response_length=123),
            SimpleNamespace(method="GET", path="/building", query="address=231+Echo+Place%2C+Bronx",
                            uri="/building?address=231+Echo+Place%2C+Bronx", version=(1, 1), headers=[]),
            {"REMOTE_ADDR": "10.0.0.1", "REQUEST_METHOD": "GET", "RAW_URI": "/building?address=231+Echo+Place%2C+Bronx",
             "PATH_INFO": "/building", "QUERY_STRING": "address=231+Echo+Place%2C+Bronx", "SERVER_PROTOCOL": "HTTP/1.1",
             "HTTP_REFERER": "https://x/area?street=Echo", "HTTP_USER_AGENT": "UA"},
            datetime.timedelta(seconds=1),
        )
        line = self.config["access_log_format"] % atoms
        self.assertIn("GET /building HTTP/1.1", line)
        self.assertNotIn("Echo", line)
        self.assertNotIn("UA", line)


if __name__ == "__main__":
    unittest.main()
