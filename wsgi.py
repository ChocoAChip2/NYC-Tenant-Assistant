"""WSGI entrypoint for production servers such as Gunicorn or Render."""

from app import app  # noqa: F401 -- gunicorn loads wsgi:app
