"""Failures from external services are normalised to UpstreamError so the route layer can map them
to a clear HTTP status and message without knowing which service failed."""


class UpstreamError(Exception):
    """kind: rate_limited | timeout | unavailable | bad_response"""

    def __init__(self, service, kind, status=None, retry_after_s=None):
        suffix = f" (HTTP {status})" if status else ""
        super().__init__(f"{service}: {kind}{suffix}")
        self.service = service
        self.kind = kind
        self.status = status
        self.retry_after_s = retry_after_s

    @property
    def user_message(self):
        return _USER_MESSAGES[self.kind].format(service=_DISPLAY_NAMES.get(self.service, self.service))

    def as_warning(self):
        """The entry shown to the user when this failure is survivable (the response still succeeds)."""
        return {"service": self.service, "kind": self.kind, "message": self.user_message}


class HttpError(Exception):
    """Bad user input or an unknown city: maps to a 4xx."""

    def __init__(self, status, message, code):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


# Users don't know what an "LLM" is; the `service` field in responses stays "LLM" for the UI and logs.
_DISPLAY_NAMES = {"LLM": "The AI writer"}

_USER_MESSAGES = {
    "rate_limited": "{service} is rate-limiting us right now. Please try again shortly.",
    "timeout": "{service} took too long to respond.",
    "unavailable": "{service} is currently unavailable.",
    "bad_response": "{service} returned an unexpected response.",
}


def error_response(err):
    """Map an exception to (status, JSON body, headers). Never includes upstream bodies or secrets."""
    if isinstance(err, HttpError):
        return err.status, {"error": err.message, "code": err.code}, {}
    if isinstance(err, UpstreamError):
        headers = {}
        if err.kind == "rate_limited" and err.retry_after_s:
            headers["Retry-After"] = str(int(err.retry_after_s + 0.999))
        status = 429 if err.kind == "rate_limited" else 504 if err.kind == "timeout" else 502
        return status, {"error": err.user_message, "code": err.kind, "service": err.service}, headers
    return 500, {"error": "Internal server error", "code": "internal"}, {}
