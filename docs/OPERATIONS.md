# Operational Guide

This guide provides instructions for common administrative tasks in ValuedCRM.

## 1. Setting Up ClickFunnels v2 Integration

### Generating an API Access Token
1. Log in to your ClickFunnels 2.0 account.
2. Navigate to **Team Settings** > **Developer Portal**.
3. Click **Add new platform application**.
4. Fill in the name (e.g., "ValuedCRM Integration") and click **Create platform application**.
5. Copy the **API Access Token** (Bearer Token) displayed.

### Configuring ValuedCRM
1. Go to the **ClickFunnels Configurations** section in the Django Admin.
2. Click "Add ClickFunnels Configuration".
3. Enter a name (e.g., "Main Team").
4. Paste the **API Access Token** into the field.
5. (Optional) Update the **API User Agent** (Default: `ValuedCRM/1.0`).
6. Check **Is Active**.
7. Click **Save**.

### Verifying Connection
1. In the list view, select the configuration.
2. Choose the action **Verify ClickFunnels Credentials** and click **Go**.
3. Upon success, the `Validation Status` will become `Valid`, and your first Team and Workspace will be automatically selected.

## 2. Syncing Courses
Before enrolling students, you must pull your products from ClickFunnels:
1. Navigate to the **Courses** section in the Django Admin.
2. Select any record (or all).
3. Choose the action **Sync Courses From ClickFunnels** and click **Go**.
4. This will populate local records with all courses from your active workspace.

## 3. Managing Enrollments

### Single Enrollment
Use the API `POST /api/enrollments/` or add a manual entry in the Admin if needed (though API/Bulk is preferred).

### Bulk Enrollment
1. Use the API `POST /api/enrollments/bulk`.
2. Provide a list of up to 50 emails and a `course_id`.
3. The system will synchronously attempt each enrollment and return a result for every email.

## 4. Reviewing Audit Logs
Navigate to **Enrollment Attempts** in the Admin.
- **Integrity:** Attempts are read-only.
- **Failures:** If an attempt status is `FAILURE`, open the record and inspect the `Error Log` and `Response Payload`.

## 5. Troubleshooting

### "401 Unauthorized" (API)
- Ensure you are logged into the Django Admin in the same browser.

### "403 Forbidden" (CF API)
- Ensure the `API User Agent` is set correctly and not blocked by CF security.

### "Invalid Token" (Encryption)
- If you lose your `FIELD_ENCRYPTION_KEY`, you must re-save your configurations in the Admin to re-encrypt the tokens.

### Logs
Search the `stdout` logs for `FAILURE` tags to see stack traces for unexpected errors.

## 6. Tara Payments — Production Setup (Phase 9)

### Required environment variables
- `DJANGO_SECRET_KEY`, `FIELD_ENCRYPTION_KEY` (Fernet key for encrypted credentials).
- `PUBLIC_BASE_URL` — canonical HTTPS base URL (no trailing slash). Required for checkout: used to build Tara's `webHookUrl`/`returnUrl`. Checkout fails closed if unset.
- `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`.
- `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL` — payment confirmations. Defaults to the console backend (no-op) if unset.
- `ALLOWED_HOSTS`, `DEBUG=False` in production.
- `TRUSTED_PROXY_COUNT` — number of reverse proxies in front of Gunicorn that append to `X-Forwarded-For` (Apache → `1`). Default `0` uses `REMOTE_ADDR`, which behind Apache is the proxy's own IP: every per-IP rate limit (login, checkout, Tara webhook) would then be one global bucket. Only set it if the outermost proxy really appends the client IP.

### Tara credential setup
1. Django Admin → **Tara Configurations** → add one, mark **Is Active**.
2. Enter `businessId` (plain) and API key/webhook secret (write-only, encrypted at rest — see `TaraConfig`). Only one config may be active.
3. No safe credential-verification endpoint is documented — status stays `PENDING` until a real payment/status check succeeds in practice.

### Webhook URL
Tara delivers to `{PUBLIC_BASE_URL}/api/tara/webhook/`. Every delivery is treated as **untrusted** (no confirmed Tara signature contract exists) and is only a trigger to call `check_transaction_status()` server-to-server — see `WebhookProcessingService`.

### Scheduled commands (cron/systemd timer/platform scheduler — none is built into this app)
- **Hourly**: `python manage.py run_hourly_reconciliation` — verifies stale PaymentAttempts, pulls the transaction list for reporting, drives confirmation/provisioning workers, repairs missing follow-up work. Concurrency-safe (Postgres advisory lock); exits non-zero only on a total run failure.
- **Frequent (e.g. every 5 min), optional if not relying solely on the hourly run**: `python manage.py process_payment_confirmations` and `python manage.py process_pending_provisioning`.

Example crontab:
```
0 * * * * cd /path/to/app && /path/to/venv/bin/python manage.py run_hourly_reconciliation >> /var/log/valuedcrm/reconcile.log 2>&1
```

### Rollout order
1. Apply migrations (`python manage.py migrate`) — additive only, no destructive schema changes across Phases 1-9.
2. Set/rotate environment variables above.
3. Configure `TaraConfig` and `ClickFunnelsConfig` in Admin.
4. Deploy application code.
5. Schedule `run_hourly_reconciliation` (and the confirmation/provisioning commands if not folding them into the hourly run alone).

### Rollback limitations
Migrations are additive (new tables/columns/permissions) — safe to roll forward repeatedly. Rolling the **application code** back after new columns/permissions exist is safe (unused columns are simply ignored); rolling the **database** back after real payments/confirmations/provisioning requests were recorded is **not** safe — no destructive rollback path is provided or recommended.

### Monitoring
Watch `ReconciliationRun.run_status` (Admin, read-only) — `FAILED` needs immediate attention; `PARTIAL_FAILURE`/`safe_error_count > 0` should be reviewed. Watch `PaymentConfirmation`/`ProvisioningRequest` rows stuck in `MANUAL_REVIEW`.

### Incident response
A failed hourly run never blocks the next one (advisory lock releases automatically on connection loss). If payments appear stuck: use the Admin **Check Tara Status** action (Phase 8) on the specific `PaymentAttempt`, never re-run reconciliation as a blind fix.

### Manual-review workflow
`PaymentAttempt` stuck at `UNKNOWN`, `PaymentConfirmation`/`ProvisioningRequest` at `MANUAL_REVIEW` all require an explicit, reasoned Admin action (Phase 8: Check Tara Status / Retry Confirmation / Retry) — never automatic.

### Unresolved limitations
- **Tara webhook signature**: no confirmed header/algorithm/replay-window contract exists; webhooks are never trusted alone (see `integrations/payments/tara/signature.py`).
- **ClickFunnels lost-response idempotency**: no "list enrollments" endpoint exists to verify a lost-response enrollment actually succeeded; only local `ProvisioningAttempt` records prevent a *repeat* call from re-enrolling (see `ProvisioningService._enroll_with_retry`).
- **Transaction-list correlation**: `POST /tara/paid/transactionlist` never returns a `productId`, so pulled records are reporting-only and can never grant credit.
