# Tara API Contract (Provisional — Needs Verification)

This document mirrors [`CLICKFUNNELS_API_CONTRACT.md`](CLICKFUNNELS_API_CONTRACT.md)'s
structure but, unlike that one, **is not yet verified against real Tara
documentation or captured sample payloads.** Every field name below is a
placeholder inferred from the general shape of payment-webhook providers, not a
confirmed Tara contract.

**Do not treat any field name here as final.** Before relying on this in
production:
1. Obtain real Tara webhook documentation (or a sandbox account) and capture at
   least one real sample payload for each section below.
2. Replace the `<TBD>` markers with verified samples.
3. Update `integrations/payments/tara/schemas.py::TaraPaymentDTO` and
   `integrations/payments/tara/client.py` (base URL, endpoint paths, signature
   header/algorithm) to match.
4. Add a validation test against the real sample, per the project's own
   external-API-contract rule (see `GEMINI.md`).

Until that happens, the system is deliberately built to degrade safely:
`Payment.raw_payload` always preserves the full, unparsed webhook body
regardless of what `TaraPaymentDTO` successfully extracts, and `TaraPaymentDTO`
allows extra fields (`ConfigDict(extra="allow")`) so nothing is lost even if
these placeholders are wrong.

## Common Rules (Provisional)

- **Extra fields:** DTOs allow extra fields, same as the ClickFunnels contract.
- **Amounts:** assumed to be numeric (float/int) in the raw payload; converted
  to `Decimal` immediately in `TaraClient.parse_webhook_payload`.
- **IDs/references:** assumed to be strings; `product_ref` is compared as an
  exact string match against `ProductMapping.external_ref` — no normalization
  is applied.

---

## Webhook: Payment Notification

**POST** `<TBD — the URL Tara actually calls; Django's receiving endpoint is
POST /api/tara/webhook/, authenticated by signature, not session>`

**Signature header:** `X-Tara-Signature` `<TBD — placeholder name>`
**Signature algorithm:** HMAC-SHA256 hex digest of the raw request body, keyed
by `TaraConfig.webhook_secret` `<TBD — placeholder algorithm, see
integrations/payments/tara/signature.py>`

**Assumed request body shape (placeholder):**
```json
{
  "transaction_id": "<TBD>",
  "product_ref": "<TBD>",
  "amount": 0.00,
  "currency": "<TBD>",
  "phone": "<TBD — format unconfirmed, e.g. +15550001111>",
  "email": "<TBD>"
}
```

**Verified sample:** `<TBD — capture from Tara sandbox or documentation>`

---

## Reconciliation: List Paid Transactions

**GET** `<TBD — placeholder: https://api.tara.example/v1/transactions>`

**Auth:** `Authorization: Bearer <TaraConfig.api_key>` `<TBD — confirm auth scheme>`

**Query parameters:** `since=<ISO-8601 timestamp>` `<TBD — confirm parameter name and whether Tara supports incremental pulls at all>`

**Assumed response shape (placeholder):** a JSON array of the same object
shape as the webhook body above, or `{"transactions": [...]}` — `TaraClient`
already handles both shapes defensively.

**Verified sample:** `<TBD>`

---

## Credential Validation

**GET** `<TBD — placeholder: https://api.tara.example/v1/account>`

Used only by the "Verify Tara Credentials" Admin action to confirm the
configured `api_key` is valid. **Verified sample:** `<TBD>`

---

## Open Questions to Resolve During Capture

- Does Tara have a genuine sandbox/test mode, or does every webhook consume a
  real transaction ID? (Affects Step 7 — "Test the Configuration" — of the
  operator setup sequence in `docs/OPERATIONS.md`.)
- Exact phone number format (E.164? Local format? With/without country code?)
  — affects `apps/payments/services.py::normalize_phone`.
- Does Tara retry failed webhook deliveries, and if so, on what schedule/backoff?
  (Affects the "Webhook processing" row of the retry matrix in `docs/OPERATIONS.md`.)
- Is `product_ref` a stable identifier across a product's lifetime, or can it
  change (e.g. on a Tara-side product edit)?
