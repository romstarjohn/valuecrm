import hashlib
import hmac


def verify_hmac_signature(payload_body: bytes, signature_header: str, secret: str) -> bool:
    """
    FABRICATED/UNSUPPORTED — DO NOT USE FOR AUTHORIZATION (Phase 6,
    docs/TARA_INTEGRATION_PROJECT.md). Tara's supplied documentation mentions
    a webhook secret but defines none of: the signature header name, the
    algorithm, the signed byte sequence, the encoding, a timestamp, a replay
    window, or secret-rotation behavior (docs/TARA_API_CONTRACT.md /
    docs/Tara_API_Reference_Technique.docx §11's own "Blocage de sécurité"
    note). The HMAC-SHA256-hex-digest scheme below was always a guess, never a
    verified contract.

    apps/payments/api.py::tara_webhook no longer calls this function (or
    TaraClient.verify_webhook_signature, which wraps it) for any trust
    decision — every webhook is treated as untrusted input and confirmed via
    TaraClient.check_transaction_status() instead (see
    apps/payments/services.py::WebhookProcessingService). This function is
    kept, not deleted, only so a future phase has a starting point once Tara
    provides the official contract — do not re-wire it into any auth/trust
    path before that happens.
    """
    if not signature_header or not secret:
        return False
    expected = hmac.new(secret.encode(), payload_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)
