from django_ratelimit.decorators import ratelimit
from ninja import Router
from ninja.responses import Response

from shared.logging_utils import get_logger, log_service_failure

from .services import WebhookProcessingService, WebhookRejectedError

logger = get_logger(__name__)

router = Router(tags=["Tara Payments"])

# Every response from this route is deliberately identical regardless of WHY
# — see tara_webhook()'s docstring. Do not add detail to these.
_RECEIVED_RESPONSE = {"status": "received"}
_REJECTED_RESPONSE = {"status": "rejected"}


@router.post("/webhook/", auth=None)
@ratelimit(key="ip", rate="60/m", block=True)
def tara_webhook(request):
    """
    POST /api/tara/webhook/ — the fixed, application-owned route Phase 5's
    checkout already constructs as webHookUrl. Do not create an alternate
    callback URL; mount nothing else for this purpose.

    CRITICAL TRUST CONSTRAINT (Phase 6, docs/TARA_INTEGRATION_PROJECT.md):
    Tara's documentation defines no signature header, algorithm, signed byte
    sequence, encoding, timestamp, or replay window — see
    integrations/payments/tara/signature.py's docstring. This endpoint
    therefore trusts NOTHING about the request's authenticity: auth=None
    (no session, no CSRF — Ninja only enforces CSRF for cookie/session-based
    auth, which this route deliberately doesn't use), and no field in the
    body is ever sufficient on its own to change payment state. See
    services.py::WebhookProcessingService for the server-to-server
    verification this triggers.

    Every response is deliberately generic (200 "received" or 400 "rejected")
    regardless of whether a businessId/productId/paymentId/order was
    recognized — revealing that would let an attacker enumerate valid
    identifiers. The only thing that varies is the HTTP status: 400 is
    reserved for requests too malformed/oversized to even durably record;
    everything else — including a businessId mismatch, an unknown productId,
    or a fully verified success — returns 200, because from Tara's delivery
    perspective the event WAS durably accepted.
    """
    if not (request.content_type or "").split(";")[0].strip() == "application/json":
        return Response(_REJECTED_RESPONSE, status=400)

    try:
        WebhookProcessingService().process_webhook(request.body)
    except WebhookRejectedError as e:
        log_service_failure(logger, "tara_webhook", "process_webhook", e)
        return Response(_REJECTED_RESPONSE, status=400)

    return Response(_RECEIVED_RESPONSE, status=200)
