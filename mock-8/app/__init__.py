"""App factory and authentication.

`authenticate` is the only request hook in the service: it resolves the API key
into the account every handler scopes to. There is deliberately no rate
limiter, no request-ID propagation and no access log.
"""

from __future__ import annotations

from flask import Flask, g, request

from app.errors import UnauthorizedError, register_error_handlers

API_KEYS = {
    "key_northwind": {"id": "acct_northwind", "name": "Northwind Traders"},
    "key_initech": {"id": "acct_initech", "name": "Initech"},
}

PUBLIC_PATHS = {"/healthz"}


def create_app():
    app = Flask(__name__)

    @app.before_request
    def authenticate():
        """Registered on the app, not per route, so a new route is covered."""
        g.account = None
        if request.path in PUBLIC_PATHS:
            return None
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise UnauthorizedError(
                "Missing or malformed Authorization header. "
                "Expected `Authorization: Bearer <api-key>`."
            )
        account = API_KEYS.get(header[len("Bearer ") :].strip())
        if account is None:
            raise UnauthorizedError("Unknown API key.")
        g.account = account
        return None

    from app.api import bp

    app.register_blueprint(bp)
    register_error_handlers(app)
    return app
