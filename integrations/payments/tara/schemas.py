from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TaraPaymentDTO(BaseModel):
    """
    Raw Tara webhook/reconciliation payment shape.

    Field names below are provisional placeholders, not a verified contract —
    see docs/TARA_API_CONTRACT.md, which must be filled in with real captured
    samples before these are trusted. `extra='allow'` and the caller always
    preserving the full raw payload on Payment.raw_payload means this DTO can be
    corrected later without any data loss in the meantime.

    Untouched by Phase 4 — webhooks are explicitly out of scope
    (docs/TARA_INTEGRATION_PROJECT.md Phase 4).
    """
    model_config = ConfigDict(extra="allow")

    transaction_id: Optional[str] = None
    product_ref: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None


# --- Phase 4: typed request/response objects for POST /tara/paymentlinks and
# POST /tara/transactions/status. Field names/aliases match Tara's documented
# wire format exactly (docs/Tara_API_Reference_Technique.docx §3, §10). ---

_MAX_PRODUCT_NAME_LENGTH = 255
_MAX_PRODUCT_DESCRIPTION_LENGTH = 2000
_MAX_PRODUCT_ID_LENGTH = 255
_MAX_URL_LENGTH = 2048


def _require_url(value: str, field_name: str, *, schemes) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError(f"{field_name} must not be empty.")
    if len(value) > _MAX_URL_LENGTH:
        raise ValueError(f"{field_name} exceeds {_MAX_URL_LENGTH} characters.")
    parts = urlsplit(value)
    if parts.scheme not in schemes or not parts.netloc:
        allowed = "/".join(schemes)
        raise ValueError(f"{field_name} must be a valid {allowed} URL.")
    return value


class TaraPaymentLinkRequest(BaseModel):
    """
    Internal request for POST /tara/paymentlinks. apiKey/businessId are
    populated by TaraClient.create_payment_link() from the active TaraConfig —
    this DTO has no default for either, so it cannot be constructed into a
    valid request without the client supplying them; callers of the client
    method cannot pass apiKey/businessId themselves (see that method's
    signature — it doesn't accept them as parameters at all).
    """
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    api_key: str = Field(alias="apiKey", min_length=1)
    business_id: str = Field(alias="businessId", min_length=1)
    product_id: str = Field(alias="productId", min_length=1, max_length=_MAX_PRODUCT_ID_LENGTH)
    product_name: str = Field(alias="productName", min_length=1, max_length=_MAX_PRODUCT_NAME_LENGTH)
    product_price: int = Field(alias="productPrice", gt=0)
    product_description: str = Field(alias="productDescription", min_length=1, max_length=_MAX_PRODUCT_DESCRIPTION_LENGTH)
    product_picture_url: Optional[str] = Field(default=None, alias="productPictureUrl")
    return_url: Optional[str] = Field(default=None, alias="returnUrl")
    web_hook_url: str = Field(alias="webHookUrl")

    @field_validator("web_hook_url")
    @classmethod
    def _validate_webhook_url(cls, v):
        return _require_url(v, "webHookUrl", schemes=("https",))

    @field_validator("return_url")
    @classmethod
    def _validate_return_url(cls, v):
        if v is None:
            return v
        return _require_url(v, "returnUrl", schemes=("https",))

    @field_validator("product_picture_url")
    @classmethod
    def _validate_picture_url(cls, v):
        if v is None:
            return v
        return _require_url(v, "productPictureUrl", schemes=("http", "https"))


class TaraPaymentLinkResponse(BaseModel):
    """
    Response for POST /tara/paymentlinks. Only `status` is structurally
    required — Tara's documented example response can omit individual link
    channels, so none of the six link fields are required even on success
    ("valid partial optional-link response"). smsLink is deliberately NOT
    URL-validated: Tara documents it as an `sms:` URI, not an http(s) URL.
    """
    model_config = ConfigDict(populate_by_name=True)

    status: str
    message: str = ""
    whatsapp_link: Optional[str] = Field(default=None, alias="whatsappLink")
    telegram_link: Optional[str] = Field(default=None, alias="telegramLink")
    dikalo_link: Optional[str] = Field(default=None, alias="dikaloLink")
    general_link: Optional[str] = Field(default=None, alias="generalLink")
    card_link: Optional[str] = Field(default=None, alias="cardLink")
    sms_link: Optional[str] = Field(default=None, alias="smsLink")

    @field_validator("whatsapp_link", "telegram_link", "dikalo_link", "general_link", "card_link")
    @classmethod
    def _validate_http_link(cls, v, info):
        if not v:
            return v
        return _require_url(v, info.field_name, schemes=("http", "https"))

    @property
    def is_success(self) -> bool:
        """The only documented success value is the literal string "success" (case-insensitive) — anything else is a provider business failure."""
        return self.status.strip().lower() == "success"


class TaraTransactionStatusRequest(BaseModel):
    """Internal request for POST /tara/transactions/status. Same apiKey/businessId-injection contract as TaraPaymentLinkRequest."""
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    api_key: str = Field(alias="apiKey", min_length=1)
    business_id: str = Field(alias="businessId", min_length=1)
    product_id: str = Field(alias="productId", min_length=1, max_length=_MAX_PRODUCT_ID_LENGTH)


class TaraTransactionStatus(str, Enum):
    """
    Local, transport-layer status vocabulary for POST /tara/transactions/status.
    Deliberately distinct from apps.payments.models.PaymentAttempt.Status (an
    app-domain enum with different states/semantics) — this module must not
    import from apps/, and mapping between the two vocabularies is an
    orchestration concern for a later phase, not this client.
    """
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"


class TaraTransactionStatusResponse(BaseModel):
    """
    Response for POST /tara/transactions/status. `status` preserves Tara's raw
    string exactly as returned, for diagnostics; `normalized_status` maps it to
    the documented SUCCESS/FAILURE/PENDING vocabulary, or UNKNOWN for anything
    else — an unrecognized status is never silently treated as success.
    """
    model_config = ConfigDict(populate_by_name=True)

    product_id: str = Field(alias="productId", min_length=1)
    status: str
    message: str = ""

    @property
    def normalized_status(self) -> TaraTransactionStatus:
        try:
            return TaraTransactionStatus(self.status.strip().upper())
        except ValueError:
            return TaraTransactionStatus.UNKNOWN


# --- Phase 9: POST /tara/paid/transactionlist (docs/Tara_API_Reference_Technique.docx
# §7 — "Lister les transactions payées"). Documented request body: apiKey,
# businessId, start, size. Documented response is a bare JSON array (no
# wrapper object, no total/cursor/hasMore) of items shaped transactionId/
# amount/currency/status/createdAt/paidAt. The supplied examples never
# include productId — see TaraTransactionListItem's docstring for why this
# means these records are reporting-only, never a credit source. ---

class TaraTransactionListRequest(BaseModel):
    """Internal request for POST /tara/paid/transactionlist. Same apiKey/businessId-injection contract as the other Tara request DTOs."""
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    api_key: str = Field(alias="apiKey", min_length=1)
    business_id: str = Field(alias="businessId", min_length=1)
    start: int = Field(ge=0)
    size: int = Field(gt=0)


class TaraTransactionListItem(BaseModel):
    """
    One row of the documented POST /tara/paid/transactionlist response.
    Deliberately has NO productId/paymentId field — Tara's supplied examples
    for this endpoint never include one (docs/Tara_API_Reference_Technique.docx
    §7), unlike /transactions/status and the webhook payloads. Without an
    exact trusted correlation identifier, a row here can never be matched to
    a specific PaymentAttempt.tara_product_id — callers (see
    apps/payments/reconciliation_services.py) must use these only for
    reconciliation reporting/manual review, never to grant payment credit.
    """
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    transaction_id: Optional[str] = Field(default=None, alias="transactionId")
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    status: str = ""
    created_at: Optional[datetime] = Field(default=None, alias="createdAt")
    paid_at: Optional[datetime] = Field(default=None, alias="paidAt")

    @field_validator("amount", mode="before")
    @classmethod
    def _parse_amount_safely(cls, v):
        """Documented as a bare `number` — parsed via Decimal(str(...)) only, never float(), matching the rest of this module's amount handling."""
        if v is None or v == "":
            return None
        try:
            return Decimal(str(v))
        except InvalidOperation:
            return None

    @field_validator("created_at", "paid_at", mode="before")
    @classmethod
    def _parse_timestamp_safely(cls, v):
        if not v:
            return None
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None


_MAX_WEBHOOK_FIELD_LENGTH = 255
_MAX_WEBHOOK_STATUS_LENGTH = 50


class TaraWebhookPayload(BaseModel):
    """
    Inbound Tara webhook body (Phase 6, docs/TARA_INTEGRATION_PROJECT.md).
    Covers the three documented payload shapes (Mobile Money, collecte, card
    payment — docs/Tara_API_Reference_Technique.docx §11), which share
    businessId/paymentId/status/creationDate/changeDate but differ on which of
    productId/amount/collectionId/phoneNumber are present. All optional except
    business_id and status — the Mobile Money example genuinely omits
    productId, so its absence alone must not reject the payload.

    UNTRUSTED INPUT: this schema only bounds/type-checks the shape. It proves
    nothing about authenticity — see TaraWebhookEvent's docstring. phone_number
    is parsed but is NEVER used to correlate a payment (repository rule,
    apps/payments/services.py::WebhookProcessingService) and is deliberately
    not persisted anywhere.
    """
    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    business_id: str = Field(alias="businessId", min_length=1, max_length=_MAX_WEBHOOK_FIELD_LENGTH)
    payment_id: Optional[str] = Field(default=None, alias="paymentId", max_length=_MAX_WEBHOOK_FIELD_LENGTH)
    product_id: Optional[str] = Field(default=None, alias="productId", max_length=_MAX_WEBHOOK_FIELD_LENGTH)
    collection_id: Optional[str] = Field(default=None, alias="collectionId", max_length=_MAX_WEBHOOK_FIELD_LENGTH)
    phone_number: Optional[str] = Field(default=None, alias="phoneNumber", max_length=50)
    amount: Optional[Decimal] = None
    creation_date: Optional[datetime] = Field(default=None, alias="creationDate")
    change_date: Optional[datetime] = Field(default=None, alias="changeDate")
    status: str = Field(min_length=1, max_length=_MAX_WEBHOOK_STATUS_LENGTH)

    @field_validator("payment_id", "product_id", "collection_id", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        return v if v else None

    @field_validator("amount", mode="before")
    @classmethod
    def _parse_amount_safely(cls, v):
        """Webhook amounts are documented as strings; parsed via Decimal(str(...)) only — never float()."""
        if v is None or v == "":
            return None
        try:
            return Decimal(str(v))
        except InvalidOperation:
            return None  # unparseable — treated as absent, never guessed

    @field_validator("creation_date", "change_date", mode="before")
    @classmethod
    def _parse_timestamp_safely(cls, v):
        """A timestamp that fails to parse must not reject the whole payload — it's diagnostic metadata, not a trust/correlation field."""
        if not v:
            return None
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    @model_validator(mode="after")
    def _require_some_correlation_id(self):
        if not self.payment_id and not self.product_id:
            raise ValueError("Payload has neither paymentId nor productId — nothing to correlate or reject safely on.")
        return self
