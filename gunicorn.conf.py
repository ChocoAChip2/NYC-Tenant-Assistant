"""Gunicorn settings read automatically from the working directory.

Render starts the app with gunicorn from the repository root, and gunicorn
loads ./gunicorn.conf.py by default; flags in the start command still win.

WHY: the default access-log line records the full request line, the
Referer and the User-Agent. /building?address=... and /area?street=...
carry a tenant's address in the query string (and in the Referer of the
page after it), and Render keeps those lines. Found 2026-10-11 in the
Render log viewer. This format keeps the path without the query string,
the status, the size and the time taken, which is what diagnosing a slow
or failing page needs.
"""

accesslog = "-"
access_log_format = '%(h)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s %(M)sms'
