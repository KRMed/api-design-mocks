"""Error types and the JSON envelope every failure leaves through."""

from __future__ import annotations

from flask import jsonify
from werkzeug.exceptions import HTTPException


class APIError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message, *, code=None, status_code=None, errors=None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.errors = errors or []

    def to_dict(self):
        body = {"error": {"code": self.code, "message": self.message}}
        if self.errors:
            body["error"]["errors"] = self.errors
        return body


class UnauthorizedError(APIError):
    status_code = 401
    code = "unauthorized"


class ValidationError(APIError):
    status_code = 400
    code = "validation_error"


class NotFoundError(APIError):
    status_code = 404
    code = "not_found"


class ConflictError(APIError):
    status_code = 409
    code = "conflict"


def register_error_handlers(app):
    @app.errorhandler(APIError)
    def handle_api_error(exc):
        response = jsonify(exc.to_dict())
        response.status_code = exc.status_code
        return response

    @app.errorhandler(HTTPException)
    def handle_http_exception(exc):
        response = jsonify(
            {
                "error": {
                    "code": exc.name.lower().replace(" ", "_"),
                    "message": exc.description,
                }
            }
        )
        response.status_code = exc.code or 500
        return response

    @app.errorhandler(Exception)
    def handle_unexpected(exc):  # pragma: no cover
        app.logger.exception("unhandled exception")
        response = jsonify(
            {
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred.",
                }
            }
        )
        response.status_code = 500
        return response
