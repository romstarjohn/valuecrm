MAX_BULK_ENROLLMENT_SIZE = 50
DEFAULT_TIMEOUT = 30

# apps.provisioning: bounded in-process retry for transient ClickFunnels errors
# (429/5xx) during ProvisioningService.execute() — never used for 401/other 4xx.
PROVISIONING_MAX_INLINE_RETRIES = 2
PROVISIONING_INLINE_RETRY_BACKOFF_SECONDS = (1, 3)

# integrations.payments.tara: explicit (connect, read) timeout for
# POST /tara/paymentlinks and POST /tara/transactions/status — reuses the
# existing DEFAULT_TIMEOUT value for both, but as an explicit tuple rather
# than a bare scalar.
TARA_CONNECT_TIMEOUT_SECONDS = DEFAULT_TIMEOUT
TARA_READ_TIMEOUT_SECONDS = DEFAULT_TIMEOUT

# integrations.payments.tara: reject a provider response larger than this
# before attempting to parse it as JSON — a payment-link/status response is a
# few hundred bytes; this is a generous bound against a malformed/hostile
# oversized response, not a realistic expected size.
TARA_MAX_RESPONSE_BYTES = 1_048_576  # 1 MiB

# apps.payments: reject an inbound Tara webhook body larger than this before
# attempting to parse it as JSON — the documented webhook payloads
# (docs/Tara_API_Reference_Technique.docx §11) are a few hundred bytes; this
# is a generous bound against a malformed/hostile oversized delivery.
TARA_WEBHOOK_MAX_BODY_BYTES = 65_536  # 64 KiB

# apps.payments (Phase 7): bounded delivery attempts for a PaymentConfirmation
# before it's left in MANUAL_REVIEW rather than retried forever. Backoff is in
# minutes and indexed by (attempt_count - 1); once attempt_count reaches the
# max, the confirmation stops being picked up automatically.
PAYMENT_CONFIRMATION_MAX_ATTEMPTS = 5
PAYMENT_CONFIRMATION_RETRY_BACKOFF_MINUTES = (1, 5, 15, 60)

# apps.provisioning (Phase 7): bounded attempts for a ProvisioningRequest
# (either flow) before ProvisioningService stops retrying automatically and
# leaves it in MANUAL_REVIEW for an operator. Mirrors
# PAYMENT_CONFIRMATION_MAX_ATTEMPTS's role for the enrollment work record.
PROVISIONING_MAX_REQUEST_ATTEMPTS = 5

# integrations.payments.tara (Phase 9): hard cap on the page size a caller may
# request from POST /tara/paid/transactionlist, independent of whatever page
# size the reconciliation service itself chooses to use.
TARA_TRANSACTION_LIST_MAX_PAGE_SIZE = 100

# apps.payments (Phase 9): fixed Postgres advisory-lock key so two hourly
# reconciliation invocations can never process the same run concurrently. An
# arbitrary but stable integer — advisory locks are session-scoped, so a
# crashed/killed run releases it automatically when its DB connection closes.
RECONCILIATION_ADVISORY_LOCK_KEY = 782934651

# apps.payments (Phase 9): bounded stale-PENDING/UNKNOWN-PaymentAttempt
# selection for the hourly reconciliation run — never scans/re-checks every
# historical attempt every hour.
RECONCILIATION_STALE_ATTEMPT_BATCH_SIZE = 50
RECONCILIATION_STALE_ATTEMPT_MIN_AGE_MINUTES = 30
RECONCILIATION_STALE_ATTEMPT_MAX_CHECKS = 10
RECONCILIATION_STALE_ATTEMPT_BACKOFF_MINUTES = (15, 30, 60, 120, 240)

# apps.payments (Phase 9): bounded POST /tara/paid/transactionlist pagination
# for reconciliation reporting only — records pulled here are never used to
# grant payment credit (no productId in the documented response shape).
RECONCILIATION_TRANSACTION_LIST_PAGE_SIZE = 50
RECONCILIATION_TRANSACTION_LIST_MAX_PAGES = 20

# apps.payments (Phase 9): bounded batch sizes for the missing-follow-up
# repair pass and for driving the existing Phase 7 confirmation/provisioning
# workers from within the hourly run.
RECONCILIATION_REPAIR_BATCH_SIZE = 100
RECONCILIATION_CONFIRMATION_BATCH_SIZE = 100
RECONCILIATION_PROVISIONING_BATCH_SIZE = 100
