"""
Tara error taxonomy (docs/TARA_INTEGRATION_PROJECT.md Phase 4). Every exception
here must expose only safe, structured information — never an API key, webhook
secret, decrypted ciphertext, full request payload, customer phone/email, or a
sensitive payment URL. Callers are expected to log str(exception) directly;
that string must always be safe to log.
"""


class TaraError(Exception):
    """Base exception for Tara integration."""
    pass


class TaraConfigurationError(TaraError):
    """No active TaraConfig, or the active configuration is missing a required field (e.g. businessId)."""
    pass


class TaraCredentialError(TaraError):
    """The configured api_key/webhook_secret could not be decrypted (wrong/rotated FIELD_ENCRYPTION_KEY, corrupt ciphertext)."""
    pass


class TaraInvalidRequestError(TaraError):
    """
    A request was rejected by local validation before anything was sent to
    Tara. Carries only the field name and a safe reason — never the offending
    value, since callers may pass data that shouldn't be echoed back into logs.
    """
    def __init__(self, message: str, field: str = ""):
        super().__init__(message)
        self.field = field


class TaraTimeoutError(TaraError):
    """
    The request timed out. This is an INDETERMINATE outcome, not a definitive
    failure — Tara may have created the resource (e.g. a payment link) even
    though the response was lost. Callers must not treat this as FAILED, and
    must not blindly retry a non-idempotent call (e.g. payment-link creation)
    without first checking transaction status.
    """
    pass


class TaraConnectionError(TaraError):
    """DNS failure, connection refused, or other transport-level failure before any HTTP response was received. Also an indeterminate outcome."""
    pass


class TaraAPIError(TaraError):
    """
    Tara returned an HTTP error response. Carries only the HTTP status code
    and the operation name — NEVER any part of the response body.

    Phase 5 correction 1: this class previously carried a 200-character
    `response_excerpt` of the raw response text. Tara's error response shapes
    are entirely undocumented (docs/TARA_API_CONTRACT.md) — an error body
    could echo back request data (apiKey, businessId, customer phone/email,
    a payment URL) or arbitrary provider text, none of which is safe to assume
    is safe. That field has been removed outright rather than filtered,
    because no allowlist can safely parse a shape we don't understand. Do not
    re-add response body content to this class.
    """
    def __init__(self, message, status_code=None, operation: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.operation = operation


class TaraAuthError(TaraAPIError):
    """HTTP 401 — the API key is invalid/unauthorized."""
    pass


class TaraClientError(TaraAPIError):
    """HTTP 4xx other than 401 — a client-side request problem (not automatically retryable)."""
    pass


class TaraServerError(TaraAPIError):
    """HTTP 5xx — a Tara-side failure. Not automatically retried by this client."""
    pass


class TaraMalformedResponseError(TaraError):
    """
    Tara returned a response that doesn't match the documented contract: not
    valid JSON, not a JSON object, missing a required field, or (for
    transaction-status) a returned productId that doesn't match the one
    requested. Never trust or act on a response that raised this.
    """
    pass


class TaraProviderBusinessError(TaraError):
    """
    HTTP succeeded, but Tara's own response indicates business failure (e.g.
    payment-link status != "success"). Distinguish this from TaraAPIError,
    which means the HTTP call itself failed.

    Phase 5 correction 1: this class previously carried Tara's raw `status`
    string verbatim as `raw_status`. Even though "status" is documented as a
    short enum-like field, Pydantic only validates it as `str` — nothing
    prevents a buggy or malicious response from putting arbitrary content
    there (an echoed apiKey, phone number, or URL). This class now carries
    only the fixed, application-authored message string passed by the caller
    — never any text sourced from the provider response.
    """
    def __init__(self, message, operation: str = ""):
        super().__init__(message)
        self.operation = operation


class TaraUnsupportedOperationError(TaraError):
    """
    Raised by an operation that is deliberately disabled pending a real,
    documented implementation — e.g. list_paid_transactions() (Phase 5
    correction 2). Never makes a network request; fails closed immediately.
    """
    pass
