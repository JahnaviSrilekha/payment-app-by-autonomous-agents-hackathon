"""HTTP error envelope and status/code mapping (spec section 5)."""


class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def body(self):
        return {"error": {"code": self.code, "message": self.message}}


def malformed_request(message="request body could not be parsed"):
    return ApiError(400, "malformed_request", message)


def missing_idempotency_key():
    return ApiError(400, "missing_idempotency_key", "Idempotency-Key header is absent or empty")


def unauthenticated(message="missing, malformed or unknown bearer token"):
    return ApiError(401, "unauthenticated", message)


def forbidden(message="not permitted to touch this resource"):
    return ApiError(403, "forbidden", message)


def not_found(message="no such resource"):
    return ApiError(404, "not_found", message)


def method_not_allowed():
    return ApiError(405, "method_not_allowed", "method not allowed for this resource")


def conflict(code, message):
    return ApiError(409, code, message)


def idempotency_key_reuse():
    return ApiError(409, "idempotency_key_reuse", "key already used with a different request body")


def email_taken():
    return ApiError(409, "email_taken", "email already registered")


def handle_taken():
    return ApiError(409, "handle_taken", "the handle derived from this email is already taken")


def request_not_pending():
    return ApiError(409, "request_not_pending", "request is not pending")


def insufficient_funds():
    return ApiError(409, "insufficient_funds", "insufficient funds")


def validation_failed(message="validation failed"):
    return ApiError(422, "validation_failed", message)


def self_payment():
    return ApiError(422, "self_payment", "cannot pay yourself")


def self_request():
    return ApiError(422, "self_request", "cannot request from yourself")