from __future__ import annotations


class BridgeError(Exception):
    """Public, redacted error. Never expose upstream exceptions to the HTTP client."""

    def __init__(self, status: int, code: str, message: str, *, job_id: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.job_id = job_id

    def payload(self) -> dict:
        error = {
            "message": self.message,
            "type": "invalid_request_error" if self.status < 500 else "server_error",
            "param": None,
            "code": self.code,
        }
        if self.job_id:
            error["job_id"] = self.job_id
        return {"error": error}
