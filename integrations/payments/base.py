from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional


@dataclass
class NormalizedPaymentDTO:
    """
    Provider-agnostic payment shape. apps/payments/services.py and everything
    downstream (matching, provisioning) depends only on this shape — never on a
    specific provider's raw schema. Adding a second payment provider means writing
    a new integrations/payments/<provider>/ module that produces this DTO, not
    touching matching/provisioning code.
    """
    provider: str
    provider_transaction_id: str
    product_ref: str = ""
    amount: Optional[Decimal] = None
    currency: str = ""
    customer_phone: str = ""
    customer_email: str = ""
    raw_payload: Dict[str, Any] = field(default_factory=dict)


class PaymentProviderClient(ABC):
    """
    Abstract boundary for any payment provider integration. Tara is the first
    (and currently only) implementation — see integrations/payments/tara/.
    Mirrors the "one integration boundary per external service" rule already
    applied to integrations/clickfunnels/.
    """

    @abstractmethod
    def verify_webhook_signature(self, request) -> bool:
        ...

    @abstractmethod
    def parse_webhook_payload(self, raw_payload: Dict[str, Any]) -> NormalizedPaymentDTO:
        ...

    @abstractmethod
    def list_paid_transactions(self, since: Optional[datetime] = None) -> List[NormalizedPaymentDTO]:
        ...
