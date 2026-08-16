import time
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import requests
from pydantic import ValidationError as PydanticValidationError

from shared.constants import (
    DEFAULT_TIMEOUT,
    TARA_CONNECT_TIMEOUT_SECONDS,
    TARA_MAX_RESPONSE_BYTES,
    TARA_READ_TIMEOUT_SECONDS,
    TARA_TRANSACTION_LIST_MAX_PAGE_SIZE,
)
from shared.logging_utils import get_logger, log_service_start, log_service_success, log_service_failure
from shared.security import decrypt_value
from integrations.payments.base import NormalizedPaymentDTO, PaymentProviderClient
from .exceptions import (
    TaraAPIError,
    TaraAuthError,
    TaraClientError,
    TaraConnectionError,
    TaraInvalidRequestError,
    TaraMalformedResponseError,
    TaraProviderBusinessError,
    TaraServerError,
    TaraTimeoutError,
)
from .schemas import (
    TaraPaymentDTO,
    TaraPaymentLinkRequest,
    TaraPaymentLinkResponse,
    TaraTransactionListItem,
    TaraTransactionListRequest,
    TaraTransactionStatusRequest,
    TaraTransactionStatusResponse,
)
from .signature import verify_hmac_signature

logger = get_logger(__name__)


def _safe_validation_summary(error: PydanticValidationError) -> str:
    """Field names only — never the offending values, which may come from caller-supplied data that shouldn't be echoed into logs/exceptions."""
    fields = sorted({".".join(str(part) for part in err["loc"]) for err in error.errors()})
    return f"invalid fields: {', '.join(fields)}" if fields else "validation failed"


class TaraClient(PaymentProviderClient):
    """
    Sole HTTP boundary to Tara — mirrors integrations/clickfunnels/client.py's
    conventions (constructor auth, _request() wrapper, structured start/
    success/failure logging). BASE_URL is the real, documented, fixed base URL
    (docs/Tara_API_Reference_Technique.docx) — not administrator-configurable,
    never accepted from checkout input, product configuration, or the browser.

    Compatibility decision (Phase 4, docs/TARA_INTEGRATION_PROJECT.md):
    validate_credentials() was REMOVED. It called a fabricated
    `GET {BASE_URL}/account` with no basis in Tara's documented API surface
    (Phase 0 discovery) — and after its only caller
    (apps/payments/admin.py::TaraConfigAdmin.verify_credentials_action) was
    already removed in Phase 2, it had zero remaining callers anywhere in the
    codebase (verified by repo-wide grep before removal). Do not re-add it
    without a real, documented, safe endpoint to call.

    list_paid_transactions() implements the real documented POST
    /tara/paid/transactionlist contract as of Phase 9 (was disabled/fabricated
    in Phases 4-8 — see git history). This is the PAID-status filter variant —
    distinct from /tara/transactionlist (all transactions, unpaginated-caller's
    responsibility, not currently implemented by any method here). Its response
    has no productId, so its only live caller
    (apps/payments/reconciliation_services.py) uses it for reporting only,
    never to grant payment credit; authoritative attempt verification still
    goes through check_transaction_status().

    BASE_URL correction: was "https://www.dklo.co/api" (missing the /tara
    segment) through at least 2026-08-16, which every endpoint below silently
    inherited — Tara's API rejected every call built from it with HTTP 405.
    Confirmed correct value verified live against Tara for paymentlinks and
    both transactionlist variants; check_transaction_status was not
    independently reconfirmed but shares the same base per this class's own
    preexisting docstrings (which already documented /tara/paymentlinks and
    /tara/transactions/status — this fix makes the code match what these
    docstrings always claimed).
    """

    BASE_URL = "https://www.dklo.co/api/tara"
    WEBHOOK_SIGNATURE_HEADER = "X-Tara-Signature"  # TBD — placeholder header name, never confirmed by Tara; unused for trust decisions as of Phase 6 (see verify_webhook_signature's docstring)

    def __init__(self, api_key: str, webhook_secret: str, business_id: str):
        self.session = requests.Session()
        self.api_key = api_key
        self.webhook_secret = webhook_secret
        self.business_id = business_id
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    @classmethod
    def from_configuration(cls, config) -> "TaraClient":
        return cls(
            api_key=decrypt_value(config.api_key),
            webhook_secret=decrypt_value(config.webhook_secret),
            business_id=config.business_id,
        )

    def _request(
        self, method: str, url: str,
        timeout=DEFAULT_TIMEOUT, allow_redirects: bool = True, operation: str = "", **kwargs,
    ) -> Any:
        """
        Low-level transport wrapper — a single network attempt, never
        automatically retried (payment-link creation is not idempotent from
        Tara's side). Distinguishes transport-level failures (timeout,
        connection loss — both INDETERMINATE outcomes, see TaraTimeoutError/
        TaraConnectionError) from HTTP-level failures (4xx/5xx) from
        malformed/oversized responses.

        `operation` (the calling method's name) is attached to raised
        exceptions as safe diagnostic context — see TaraAPIError's docstring
        for why the response body itself is never attached instead.
        """
        method = method.upper()
        log_service_start(logger, "TaraClient", "_request", endpoint=url, method=method)
        start_time = time.time()

        try:
            response = self.session.request(
                method=method, url=url, timeout=timeout, allow_redirects=allow_redirects, **kwargs,
            )
        except requests.exceptions.Timeout as e:
            duration_ms = int((time.time() - start_time) * 1000)
            log_service_failure(logger, "TaraClient", "_request", e, endpoint=url, duration_ms=duration_ms, method=method)
            raise TaraTimeoutError("Tara request timed out — outcome is indeterminate, not a definitive failure.") from e
        except requests.exceptions.ConnectionError as e:
            duration_ms = int((time.time() - start_time) * 1000)
            log_service_failure(logger, "TaraClient", "_request", e, endpoint=url, duration_ms=duration_ms, method=method)
            raise TaraConnectionError("Could not connect to Tara — outcome is indeterminate, not a definitive failure.") from e
        except requests.exceptions.RequestException as e:
            duration_ms = int((time.time() - start_time) * 1000)
            log_service_failure(logger, "TaraClient", "_request", e, endpoint=url, duration_ms=duration_ms, method=method)
            raise TaraAPIError(str(e))

        duration_ms = int((time.time() - start_time) * 1000)
        log_context = {"endpoint": url, "status_code": response.status_code, "duration_ms": duration_ms, "method": method}

        if len(response.content) > TARA_MAX_RESPONSE_BYTES:
            raise TaraMalformedResponseError("Tara response exceeded the maximum allowed size.")

        if response.status_code == 401:
            raise TaraAuthError("The Tara API key is invalid or unauthorized.", status_code=401, operation=operation)
        if 400 <= response.status_code < 500:
            raise TaraClientError(
                f"Tara rejected the request (HTTP {response.status_code}).",
                status_code=response.status_code, operation=operation,
            )
        if 500 <= response.status_code < 600:
            raise TaraServerError(
                f"Tara returned a server error (HTTP {response.status_code}).",
                status_code=response.status_code, operation=operation,
            )

        try:
            data = response.json()
        except ValueError as e:
            log_service_failure(logger, "TaraClient", "_request", e, **log_context)
            raise TaraMalformedResponseError("Tara response was not valid JSON.") from e

        log_service_success(logger, "TaraClient", "_request", **log_context)
        return data

    def _parse_response(self, data: Any, model_cls, operation: str):
        if not isinstance(data, dict):
            raise TaraMalformedResponseError(f"Tara {operation} response was not a JSON object.")
        try:
            return model_cls.model_validate(data)
        except PydanticValidationError as e:
            raise TaraMalformedResponseError(
                f"Tara {operation} response did not match the documented contract ({_safe_validation_summary(e)})."
            ) from e

    # --- Phase 4: POST /tara/paymentlinks ---

    def create_payment_link(
        self, *,
        product_id: str, product_name: str, product_price: Decimal, product_description: str,
        web_hook_url: str, product_picture_url: Optional[str] = None, return_url: Optional[str] = None,
    ) -> TaraPaymentLinkResponse:
        """
        POST /tara/paymentlinks. apiKey/businessId come from this client's own
        configuration (self.api_key/self.business_id) — this method does not
        accept either as a parameter, so a caller cannot override them or
        supply a browser-submitted value in their place.

        product_price must be a Decimal with no fractional component — XAF/XOF
        are zero-decimal currencies per Tara's own documentation
        (docs/Tara_API_Reference_Technique.docx §1) — and is never converted
        via float. Phase 5 must construct this call from authoritative
        Order/Installment/PaymentAttempt data, never from browser input.

        Never automatically retried. A TaraTimeoutError/TaraConnectionError
        here is an INDETERMINATE outcome — Tara may have created the link
        anyway even though the response was lost. Callers must check
        transaction status before assuming failure or creating a second
        attempt/link, and must never write PaymentAttempt state from inside
        this method — it returns a value; it does not persist anything.
        """
        log_service_start(logger, "TaraClient", "create_payment_link", product_id=product_id)

        if product_price != product_price.to_integral_value():
            raise TaraInvalidRequestError(
                "product_price must be a whole number — XAF/XOF are zero-decimal currencies.",
                field="product_price",
            )

        try:
            request_dto = TaraPaymentLinkRequest(
                api_key=self.api_key,
                business_id=self.business_id,
                product_id=product_id,
                product_name=product_name,
                product_price=int(product_price),
                product_description=product_description,
                product_picture_url=product_picture_url,
                return_url=return_url,
                web_hook_url=web_hook_url,
            )
        except PydanticValidationError as e:
            raise TaraInvalidRequestError(f"Invalid payment link request ({_safe_validation_summary(e)}).") from e

        url = f"{self.BASE_URL}/paymentlinks"
        data = self._request(
            "POST", url,
            json=request_dto.model_dump(by_alias=True, exclude_none=True),
            timeout=(TARA_CONNECT_TIMEOUT_SECONDS, TARA_READ_TIMEOUT_SECONDS),
            allow_redirects=False,
            operation="create_payment_link",
        )

        response_dto = self._parse_response(data, TaraPaymentLinkResponse, "create_payment_link")
        if not response_dto.is_success:
            # Never include response_dto.status/message here — Phase 5 correction 1:
            # "status" is only str-typed by Pydantic, not a verified closed enum, so
            # nothing guarantees Tara couldn't put arbitrary (e.g. echoed) text there.
            raise TaraProviderBusinessError(
                "Tara reported payment-link creation as unsuccessful.",
                operation="create_payment_link",
            )

        log_service_success(logger, "TaraClient", "create_payment_link", product_id=product_id)
        return response_dto

    # --- Phase 4: POST /tara/transactions/status ---

    def check_transaction_status(self, product_id: str) -> TaraTransactionStatusResponse:
        """
        POST /tara/transactions/status. apiKey/businessId come from this
        client's own configuration, same non-overridable contract as
        create_payment_link(). Verifies the returned productId matches the one
        requested — a mismatch raises TaraMalformedResponseError and must
        never be used to update a payment (caller obligation for Phase 5+;
        this method itself never writes any state).

        Not automatically retried — this client layer has no bounded-retry
        precedent (apps/provisioning/services.py::ProvisioningService's
        bounded retry is a SERVICE-layer concern for ClickFunnels enrollment,
        not a client-layer one), so none was added here either.
        """
        log_service_start(logger, "TaraClient", "check_transaction_status", product_id=product_id)

        try:
            request_dto = TaraTransactionStatusRequest(
                api_key=self.api_key, business_id=self.business_id, product_id=product_id,
            )
        except PydanticValidationError as e:
            raise TaraInvalidRequestError(f"Invalid transaction status request ({_safe_validation_summary(e)}).") from e

        url = f"{self.BASE_URL}/transactions/status"
        data = self._request(
            "POST", url,
            json=request_dto.model_dump(by_alias=True, exclude_none=True),
            timeout=(TARA_CONNECT_TIMEOUT_SECONDS, TARA_READ_TIMEOUT_SECONDS),
            allow_redirects=False,
            operation="check_transaction_status",
        )

        response_dto = self._parse_response(data, TaraTransactionStatusResponse, "check_transaction_status")
        if response_dto.product_id != product_id:
            raise TaraMalformedResponseError(
                "Tara's transaction status response productId did not match the requested productId."
            )

        log_service_success(
            logger, "TaraClient", "check_transaction_status",
            product_id=product_id, normalized_result=response_dto.normalized_status.value,
        )
        return response_dto

    # --- Webhook handling ---

    def verify_webhook_signature(self, request) -> bool:
        """
        FABRICATED/UNSUPPORTED — not called by apps/payments/api.py::tara_webhook
        for any trust decision as of Phase 6. See signature.py's
        verify_hmac_signature() docstring for the full rationale. Kept
        callable (not removed) only for a future phase once Tara's official
        signature contract is confirmed.
        """
        signature_header = request.headers.get(self.WEBHOOK_SIGNATURE_HEADER, "")
        return verify_hmac_signature(request.body, signature_header, self.webhook_secret)

    def parse_webhook_payload(self, raw_payload: Dict[str, Any]) -> NormalizedPaymentDTO:
        dto = TaraPaymentDTO.model_validate(raw_payload)
        return NormalizedPaymentDTO(
            provider="tara",
            provider_transaction_id=dto.transaction_id or "",
            product_ref=dto.product_ref or "",
            amount=self._to_decimal(dto.amount),
            currency=dto.currency or "",
            customer_phone=dto.phone or "",
            customer_email=dto.email or "",
            raw_payload=raw_payload,
        )

    # --- Phase 9: POST /tara/paid/transactionlist ---

    def list_paid_transactions(self, start: int, size: int) -> List[TaraTransactionListItem]:
        """
        POST /tara/paid/transactionlist (docs/Tara_API_Reference_Technique.docx
        §7). Replaces the Phase 5 fabricated/disabled GET implementation —
        see git history for that version's rationale, no longer relevant now
        that the real documented contract is implemented.

        Single page only — `start`/`size` are caller-supplied (0-based offset,
        page size); bounded pagination/looping across pages is the caller's
        responsibility (see apps/payments/reconciliation_services.py), not
        this client's. `size` is capped at TARA_TRANSACTION_LIST_MAX_PAGE_SIZE
        regardless of what the caller requests.

        Not automatically retried, same as every other method on this client.
        The documented response is a bare JSON array with no total/cursor/
        hasMore indicator — this method itself makes no assumption about
        whether more pages exist; the caller decides that by comparing the
        returned length to `size` (see TaraTransactionListItem's docstring for
        why these items can never be used to grant payment credit).
        """
        log_service_start(logger, "TaraClient", "list_paid_transactions", start=start, size=size)

        if size <= 0 or size > TARA_TRANSACTION_LIST_MAX_PAGE_SIZE:
            raise TaraInvalidRequestError(
                f"size must be between 1 and {TARA_TRANSACTION_LIST_MAX_PAGE_SIZE}.", field="size",
            )
        if start < 0:
            raise TaraInvalidRequestError("start must not be negative.", field="start")

        try:
            request_dto = TaraTransactionListRequest(
                api_key=self.api_key, business_id=self.business_id, start=start, size=size,
            )
        except PydanticValidationError as e:
            raise TaraInvalidRequestError(f"Invalid transaction list request ({_safe_validation_summary(e)}).") from e

        url = f"{self.BASE_URL}/paid/transactionlist"
        data = self._request(
            "POST", url,
            json=request_dto.model_dump(by_alias=True, exclude_none=True),
            timeout=(TARA_CONNECT_TIMEOUT_SECONDS, TARA_READ_TIMEOUT_SECONDS),
            allow_redirects=False,
            operation="list_paid_transactions",
        )

        if not isinstance(data, list):
            raise TaraMalformedResponseError("Tara paid transaction list response was not a JSON array.")

        items = []
        for raw_item in data:
            if not isinstance(raw_item, dict):
                raise TaraMalformedResponseError("Tara paid transaction list contained a non-object item.")
            try:
                items.append(TaraTransactionListItem.model_validate(raw_item))
            except PydanticValidationError as e:
                raise TaraMalformedResponseError(
                    f"Tara paid transaction list item did not match the documented contract ({_safe_validation_summary(e)})."
                ) from e

        log_service_success(logger, "TaraClient", "list_paid_transactions", start=start, size=size)
        return items

    @staticmethod
    def _to_decimal(value: Optional[float]) -> Optional[Decimal]:
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
