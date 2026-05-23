"""Legacy local entrypoint that simply reuses the main Flask app object."""

import os

from app import app


if __name__ == "__main__":
    # Keep this file compatible with environments that still start the app from
    # test.py instead of app.py or wsgi.py.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
