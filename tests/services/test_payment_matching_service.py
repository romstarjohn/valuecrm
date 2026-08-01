from decimal import Decimal

import pytest

from apps.contacts.models import Contact
from apps.courses.models import Course
from apps.payments.models import Payment, PaymentTimelineEvent
from apps.payments.services import PaymentMatchingService, normalize_phone
from apps.provisioning.models import Product, ProductMapping
from integrations.payments.base import NormalizedPaymentDTO


@pytest.fixture
def course(db):
    return Course.objects.create(cf_course_id="crs_1", name="Course 1", workspace_id="ws_1")


@pytest.fixture
def product(db, course):
    product = Product.objects.create(name="Bootcamp", provisioning_policy=Product.ProvisioningPolicy.AUTOMATIC, expected_amount=Decimal("100.00"))
    product.courses.add(course)
    return product


@pytest.fixture
def mapping(db, product):
    return ProductMapping.objects.create(provider="tara", external_ref="tara_ref_1", product=product)


def make_dto(**overrides):
    defaults = dict(
        provider="tara",
        provider_transaction_id="txn_1",
        product_ref="tara_ref_1",
        amount=Decimal("100.00"),
        currency="USD",
        customer_phone="+1 555-000-1111",
        customer_email="student@example.com",
        raw_payload={"raw": "payload"},
    )
    defaults.update(overrides)
    return NormalizedPaymentDTO(**defaults)


@pytest.mark.django_db
def test_record_payment_is_idempotent():
    service = PaymentMatchingService()
    dto = make_dto()

    payment1, created1 = service.record_payment(dto)
    payment2, created2 = service.record_payment(dto)

    assert created1 is True
    assert created2 is False
    assert payment1.id == payment2.id
    assert Payment.objects.count() == 1


@pytest.mark.django_db
def test_full_match_phone_reaches_matched_high_confidence(mapping):
    Contact.objects.create(email="other@example.com", phone="+15550001111")
    service = PaymentMatchingService()
    payment, _ = service.record_payment(make_dto())

    result = service.match_and_process(payment)

    assert result.status == Payment.Status.MATCHED
    assert result.match_confidence == Payment.Confidence.HIGH
    assert result.matched_product_id == mapping.product_id
    assert result.matched_contact is not None


@pytest.mark.django_db
def test_email_match_used_when_no_phone_match(mapping):
    Contact.objects.create(email="student@example.com", phone="")
    service = PaymentMatchingService()
    payment, _ = service.record_payment(make_dto(customer_phone=""))

    result = service.match_and_process(payment)

    assert result.status == Payment.Status.MATCHED
    assert result.match_confidence == Payment.Confidence.HIGH


@pytest.mark.django_db
def test_no_product_mapping_always_needs_review_even_with_customer_match():
    Contact.objects.create(email="student@example.com", phone="+15550001111")
    service = PaymentMatchingService()
    # product_ref points at a mapping that doesn't exist
    payment, _ = service.record_payment(make_dto(product_ref="unknown_ref"))

    result = service.match_and_process(payment)

    assert result.status == Payment.Status.NEEDS_REVIEW
    assert result.matched_product is None


@pytest.mark.django_db
def test_amount_match_alone_never_reaches_matched_or_high_confidence(mapping):
    # No contact anywhere with this phone/email — only the amount lines up with the Product's expected_amount.
    service = PaymentMatchingService()
    payment, _ = service.record_payment(make_dto(customer_phone="", customer_email=""))

    result = service.match_and_process(payment)

    assert result.status == Payment.Status.NEEDS_REVIEW
    assert result.match_confidence != Payment.Confidence.HIGH
    assert result.match_confidence == Payment.Confidence.MEDIUM  # weak signal only, capped below HIGH


@pytest.mark.django_db
def test_mismatched_amount_yields_low_confidence_not_high(mapping):
    service = PaymentMatchingService()
    payment, _ = service.record_payment(
        make_dto(customer_phone="", customer_email="", amount=Decimal("9.99"))
    )

    result = service.match_and_process(payment)

    assert result.status == Payment.Status.NEEDS_REVIEW
    assert result.match_confidence == Payment.Confidence.LOW


@pytest.mark.django_db
def test_ambiguous_phone_match_falls_back_to_needs_review(mapping):
    Contact.objects.create(email="a@example.com", phone="+15550001111")
    Contact.objects.create(email="b@example.com", phone="+15550001111")
    service = PaymentMatchingService()
    payment, _ = service.record_payment(make_dto(customer_email=""))

    result = service.match_and_process(payment)

    assert result.status == Payment.Status.NEEDS_REVIEW
    assert result.matched_contact is None


@pytest.mark.django_db
def test_inactive_product_mapping_is_not_resolved(product, mapping):
    product.is_active = False
    product.save()
    Contact.objects.create(email="student@example.com", phone="+15550001111")

    service = PaymentMatchingService()
    payment, _ = service.record_payment(make_dto())
    result = service.match_and_process(payment)

    assert result.status == Payment.Status.NEEDS_REVIEW
    assert result.matched_product is None


@pytest.mark.django_db
def test_rematch_after_mapping_added_reaches_matched(product):
    Contact.objects.create(email="student@example.com", phone="+15550001111")
    service = PaymentMatchingService()

    payment, _ = service.record_payment(make_dto())
    first = service.match_and_process(payment)
    assert first.status == Payment.Status.NEEDS_REVIEW

    # Operator fixes the missing mapping ...
    ProductMapping.objects.create(provider="tara", external_ref="tara_ref_1", product=product)

    second = service.match_and_process(payment)
    assert second.status == Payment.Status.MATCHED
    assert second.match_confidence == Payment.Confidence.HIGH
    # Original NEEDS_REVIEW event preserved, REMATCHED event appended, not overwritten.
    event_types = list(payment.timeline_events.values_list("event_type", flat=True))
    assert PaymentTimelineEvent.EventType.NEEDS_REVIEW in event_types
    assert PaymentTimelineEvent.EventType.REMATCHED in event_types


@pytest.mark.django_db
def test_dismiss_marks_ignored_and_records_event(mapping):
    service = PaymentMatchingService()
    payment, _ = service.record_payment(make_dto(customer_phone="", customer_email=""))
    service.match_and_process(payment)
    assert payment.status == Payment.Status.NEEDS_REVIEW

    service.dismiss(payment, notes="refunded")

    payment.refresh_from_db()
    assert payment.status == Payment.Status.IGNORED
    assert "refunded" in payment.review_notes
    assert payment.timeline_events.filter(event_type=PaymentTimelineEvent.EventType.IGNORED).exists()


def test_normalize_phone_strips_formatting():
    assert normalize_phone("+1 (555) 000-1111") == "+15550001111"
    assert normalize_phone("") == ""
    assert normalize_phone(None) == ""


@pytest.mark.django_db
def test_no_secrets_or_raw_contact_payload_leaked_in_logs(mapping, caplog):
    import logging
    caplog.set_level(logging.INFO)

    Contact.objects.create(email="student@example.com", phone="+15550001111")
    service = PaymentMatchingService()
    payment, _ = service.record_payment(make_dto())
    service.match_and_process(payment)

    log_text = caplog.text
    assert "student@example.com" not in log_text
    assert "+1 555-000-1111" not in log_text
