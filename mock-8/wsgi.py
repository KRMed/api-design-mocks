"""Entry point: `python wsgi.py` for dev, or point gunicorn at `app`."""

from app import create_app
from seed import seed

app = create_app()
seed()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)
