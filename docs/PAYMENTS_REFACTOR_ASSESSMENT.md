# Current-State Assessment — Payments / Tara Module

Companion to [`PAYMENTS_REFACTOR_PRD.md`](PAYMENTS_REFACTOR_PRD.md). This is a snapshot as of
2026-09-14 (commit `ddd8644` plus the uncommitted webhook-resend work on top of it) — validate
line numbers/counts against the codebase before acting on them, since they will drift.

## 1. File inventory

### `apps/payments/`

| File | Lines | Responsibility today |
|---|---:|---|
| `services.py` | 1,238 | 9 service classes: Tara config, order lifecycle, installment lifecycle, checkout, payment crediting, confirmation creation, confirmation delivery, webhook processing |
| `admin_services.py` | 812 | Admin-triggered action services: order/installment/enrollment admin actions, Tara status re-check, confirmation retry |
| `models.py` | 748 | 8 models: `TaraConfig`, `ReconciliationRun`, `PaymentPlan`, `Order`, `Installment`, `PaymentAttempt`, `TaraWebhookEvent`, `PaymentConfirmation`, `AdminAuditLog` |
| `admin.py` | 565 | Django Admin registrations/customizations for the above models |
| `reconciliation_services.py` | 350 | Single `ReconciliationService` class: locking, stale-attempt verification, transaction-list pulling, confirmation processing, provisioning processing, followup repair |
| `views.py` | 133 (was ~90) | Public checkout + checkout-status(+refresh) views |
| `forms.py` | 60 | `CheckoutContactForm` |
| `admin_actions.py` | 82 | Admin action glue (bulk actions registered on ModelAdmins) |
| `api.py` | 56 | Ninja API endpoints |
| `urls.py` | 11 | URL routing |

### `integrations/payments/tara/`

| File | Lines | Responsibility today |
|---|---:|---|
| `client.py` | 436 (was ~398) | `TaraClient`: HTTP calls to Tara, organized as one method per feature phase (payment link creation, transaction status, transaction list, **resend-webhook** just added, webhook signature verification) |
| `schemas.py` | 305 (was ~296) | Pydantic request/response DTOs for every `TaraClient` method |
| `exceptions.py` | 122 | Tara-specific exception hierarchy |
| `signature.py` | 28 | Webhook signature verification helper |

### Tests (payments + Tara only)

~8,000 lines across four directories, none of which map 1:1 to the source layout above:

- `tests/apps/payments/` — 18 files, mostly Django Admin behavior + a few view/model tests
  (`test_order_admin.py`, `test_checkout_status_page.py`, `test_checkout_views.py`,
  `test_payment_plan_model.py`, management-command tests, etc.)
- `tests/services/` — payments service tests interleaved with unrelated services
  (`test_checkout_service.py`, `test_order_service.py`, `test_reconciliation_service.py`,
  `test_webhook_processing_service.py`, `test_payment_credit_service.py`, etc. sit alongside
  `test_contact_service.py`, `test_course_service.py`, `test_enrollment_service.py` — non-payments
  domains in the same directory)
- `tests/integrations/` — `test_tara_client.py`, `test_tara_payment_link.py`,
  `test_tara_transaction_status.py`, `test_tara_resend_webhook.py` (currently untracked)
- `tests/api/` — `test_tara_webhook_api.py`

Observed overlap worth resolving, not just relocating:

- Checkout status has coverage in **three** places:
  `tests/apps/payments/test_checkout_status_page.py` (245 lines),
  `tests/apps/payments/test_checkout_views.py` (181 lines), and
  `tests/services/test_checkout_service.py` (573 lines). Some of this is legitimately
  layered (view vs. service vs. template), but it should be confirmed rather than assumed —
  see roadmap Phase 3.
- `tests/services/test_phase7_concurrency.py` is named after an implementation phase, not a
  behavior — a naming smell consistent with the "built in numbered phases" pattern noted in the
  PRD.

## 2. `services.py` — class-by-class breakdown

In file order (`apps/payments/services.py`), each with its approximate line range and a proposed
target module (see roadmap Phase 1 for the actual module-naming decision):

| Class | Lines (approx.) | Notes |
|---|---|---|
| `TaraConfigService` | 96–205 | Credential storage/rotation + `get_client()` factory. Used by `CheckoutService`, `ReconciliationService`, admin views. Natural home: its own `tara_config_service.py` or alongside `integrations/payments/tara`. |
| `OrderService` | 207–337 | Order creation, installment scheduling, order-level transitions, cancellation. |
| `InstallmentService` | 339–359 | Installment status transitions. Small; could merge into `OrderService`'s module or stay separate — worth a decision, not an assumption. |
| `PaymentAttemptService` | 360–433 | Payment attempt creation + transitions. |
| `CheckoutResult` / `CheckoutService` | 434–734 | The largest single class (~300 lines): guest contact resolution, signed-reference tokens, `start_checkout`, the new `request_status_refresh` (added on top, uncommitted), and several `_private` helpers for locking/link creation. |
| `PaymentCreditService` | 735–893 | Applies verified success/failure from Tara to `PaymentAttempt`/`Installment`/`Order`. Called from both `WebhookProcessingService` and admin's `check_tara_status`. |
| `PaymentConfirmationService` | 894–929 | Creates a `PaymentConfirmation` record. |
| `PaymentConfirmationDeliveryService` | 930–1067 | Sends confirmations (email presumably), with claim/retry/failure-category logic. |
| `WebhookProcessingService` | 1068–1238 | Parses, dedups, verifies, and applies incoming Tara webhooks — calls into `PaymentCreditService`. |

Grouping observation: `PaymentCreditService`, `WebhookProcessingService`, and
`PaymentConfirmationService`/`PaymentConfirmationDeliveryService` form one natural cluster
("what happens when Tara tells us a payment succeeded/failed"); `OrderService`,
`InstallmentService`, `PaymentAttemptService` form another ("order/installment/attempt lifecycle
state machines"); `CheckoutService` and `TaraConfigService` are each closer to standalone.

## 3. `reconciliation_services.py` — method-by-method breakdown

`ReconciliationService` (one class, `apps/payments/reconciliation_services.py`):

- `run()` / `_run_locked()` — top-level orchestration + advisory locking (`_try_acquire_lock`,
  `_release_lock`)
- `_verify_stale_attempts()` / `_verify_one_attempt()` / `_bump_check_count()` — re-checks
  PENDING/UNKNOWN attempts against Tara
- `_pull_transaction_list_for_reporting()` — pulls Tara's transaction list for reconciliation
  reporting
- `_process_confirmations()` — drives pending `PaymentConfirmation` delivery
- `_process_provisioning()` — drives pending provisioning/enrollment follow-through
- `_repair_missing_followups()` — repairs orders stuck without a follow-up action

Each of these is a distinct job glued together only by being called in sequence from
`_run_locked()`. This is the clearest single candidate in the module for "extract into named,
independently-testable steps behind a thin coordinator" (roadmap Phase 2).

## 4. What's *not* a problem (don't fix what isn't broken)

- `admin_services.py` and `admin.py`, despite their size, already have one class per admin
  action family (`OrderAdministrationService`, `PaymentAttemptAdministrationService`,
  `PaymentConfirmationAdministrationService`, `EnrollmentAdministrationService`). Splitting these
  further is optional — confirm during Phase 1/2 whether it's warranted, don't assume it is.
- `models.py` at 748 lines for 8 models with real business logic (`clean()` validation,
  computed properties) is within the normal range for a Django app this size; not a priority.
- Test *count* and *coverage* are strong (~8,000 lines). The problem is organization, not
  thoroughness — nothing here should read as "there isn't enough testing."
- `integrations/payments/tara/exceptions.py` and `signature.py` are small and single-purpose;
  no action needed.

## 5. Signal that this will keep happening

The webhook-resend feature currently uncommitted on this branch (`request_status_refresh` in
`CheckoutService`, `resend_webhook` in `TaraClient`, new `checkout_status_refresh` view) was
built the same way as everything before it: bolted onto the existing large files under a
`# --- Phase 10 ---` comment. That's not a criticism of the feature — it's evidence that without
this refactor, `services.py` and `client.py` will keep growing linearly with every future phase.
