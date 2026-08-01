# Phase 2 Roadmap — Tara Payments / Provisioning

These are enhancements deliberately deferred out of the MVP implemented in
`apps/payments` and `apps/provisioning` (see `docs/ARCHITECTURE.md` and
`docs/OPERATIONS.md`). None of these block current usage — the MVP's
"Link to Contact" (editing `matched_contact` directly on the Payment Admin
change form), Admin-based catalog management, and the dashboard's operational
queues already cover the day-to-day workflow. This document exists so the
scope decisions are visible and don't get silently re-litigated or forgotten.

Treat each item below as a starting brief, not a finished spec — validate the
approach against the codebase at the time it's picked up, since the exact file
paths/models referenced here may have shifted.

---

## 1. Contact Creation/Merge Workflow for Unmatched Payments

**Problem:** When a Tara payment's phone/email doesn't match any existing
`Contact`, today's only resolution path is: (a) an operator manually creates
the `Contact` elsewhere (e.g. `apps/contacts` portal or Admin) first, then (b)
comes back to the `Payment` row and sets `matched_contact` to the newly
created record. There's no one-step "create a Contact from this payment's
phone/email and link it in the same action."

**Why it matters:** For genuinely new customers (as opposed to a data-entry
mismatch), this is the common case, not the exception — every first-time
buyer who paid before ever existing as a local `Contact` hits this path.

**Rough approach:** A custom Admin action or view on `PaymentAdmin` —
"Create Contact & Link" — that pre-fills a `ContactForm` (see
`apps/contacts/forms.py`) with `Payment.extracted_email`/`extracted_phone`,
creates the `Contact` on submit, and performs the same manual-link promotion
logic already in `PaymentAdmin.save_model` (promote to `MATCHED`/`HIGH` if a
product is already resolved, then call
`ProvisioningService.create_request_from_payment`). Also consider a "merge"
path for the case where a near-duplicate `Contact` already exists (e.g.
same person, slightly different phone formatting) — that's a bigger scope
(fuzzy matching, merge-record semantics) and should probably be split into
its own sub-item if picked up.

---

## 2. Rename `Product` → `Offer`

**Problem:** `apps/provisioning/models.py::Product` was named to match the
approved architecture plan's terminology at the time. If the business ends up
calling these "Offers" (a common e-commerce/info-product term, and arguably
clearer than "Product" once there's also a ClickFunnels "product"/course
concept in the same sentence), the model name should follow.

**Why it matters:** Naming drift between what operators call something in
conversation/docs and what the code calls it is a long-term maintainability
tax — every future onboarding conversation pays a small "oh, we call that a
Product internally" tax.

**Rough approach:** This is a mechanical rename, not a redesign — `Product` →
`Offer`, `ProductMapping` → `OfferMapping`, `matched_product`/`product_id`
field names → `matched_offer`/`offer_id` throughout `apps/payments`,
`apps/provisioning`, their Admin/services/tests, and the dashboard queue
labels. Use Django's `db_table` Meta option (or a data migration) to rename
the underlying tables without a destructive drop/recreate. **Only do this if
the business has actually confirmed the terminology change** — renaming
speculatively creates churn for no benefit.

---

## 3. Customer Timeline View (Aggregated Across Payments)

**Problem:** `PaymentTimelineEvent` today gives a full chronological history
for a *single* `Payment` (rendered inline on that Payment's Admin page — see
`apps/payments/admin.py::PaymentTimelineEventInline`). There's no view showing
one customer's history *across* all their payments and provisioning requests
— e.g. "show me everything that's ever happened for contact X."

**Why it matters:** Support/troubleshooting conversations are usually
customer-centric ("this person says they paid twice and got access once"),
not payment-centric. Today an operator has to find each `Payment` for that
contact separately via search and read each timeline individually.

**Rough approach:** A read-only view (Admin custom view, or a portal page
under `apps/contacts`) that queries
`Payment.objects.filter(matched_contact=contact)` and
`ProvisioningRequest.objects.filter(contact=contact)`, merges their
`PaymentTimelineEvent` sets (already payment-scoped, so this is a `payment__in`
filter) ordered by `created_at`, and renders one combined timeline. Consider
adding this as a tab/section on the existing `apps/contacts` detail page
(`contacts/detail.html` already shows `enrollment_attempts` — this would be a
natural sibling section) rather than a brand-new page.

---

## 4. Configuration Health Dashboard

**Problem:** The current dashboard's "Operational Queues" section (see
`apps/dashboard/views.py`) shows payment/provisioning *work item* counts
(Unmatched, Manual, Approval Required, Scheduled, Failed) plus a minimal
last-reconciliation-run indicator. It does not summarize *configuration*
health — e.g. "is there an active, valid `TaraConfig`?", "how many
`ProductMapping` rows exist vs. how many distinct `product_ref` values have
shown up in `NEEDS_REVIEW` payments (i.e. likely-missing mappings)?", "is the
active `ClickFunnelsConfig` workspace still valid?"

**Why it matters:** Most operational incidents in a system like this trace
back to a configuration problem (expired token, missing mapping, mis-set
policy), not a code bug. A dedicated health view turns "why did payments stop
being that flowing through today" from a multi-step investigation into a
single glance.

**Rough approach:** A new dashboard section or Admin view aggregating:
`TaraConfig`/`ClickFunnelsConfig` validation status, count of `is_active=True`
`Product`s with zero `ProductMapping`s (dead product), count of distinct
unresolved `product_ref` values seen in `NEEDS_REVIEW` payments in the last N
days (candidate missing mappings — this list is exactly what an operator needs
for Step 4 of the setup sequence in `docs/OPERATIONS.md`), and days since the
last successful `ReconciliationRun`. Mostly read-only aggregation queries
against existing models — no new persistence required.

---

## 5. Simulation Mode

**Problem:** Today, verifying that a `ProductMapping` + `provisioning_policy`
combination is configured correctly requires sending an actual Tara webhook
(or a manually crafted signed `curl` request per Step 7 of the setup
sequence) and watching what happens for real — including, for `AUTOMATIC`
products, an actual ClickFunnels enrollment call.

**Why it matters:** Testing against real external services is slower, riskier
(a mistake against an `AUTOMATIC` product really enrolls someone), and
depends on Tara actually having a safe sandbox/test mode — which
`docs/TARA_API_CONTRACT.md` flags as an open, unconfirmed question. A
simulation mode lets an operator answer "what would happen if a payment like
*this* arrived?" without touching Tara or ClickFunnels at all.

**Rough approach:** An Admin action or small form (inputs: a `product_ref`,
optional phone/email, optional amount) that runs the *read side* of
`PaymentMatchingService` (product/customer resolution, confidence scoring)
against real local data — without calling `record_payment` (so no `Payment`
row is created) and without calling `ProvisioningService.create_request_from_payment`
(so nothing is ever provisioned) — and simply reports back: which `Product`
would resolve (if any), which `Contact` would resolve (if any) and via which
signal, the resulting confidence level, and what `provisioning_policy` would
apply. This is a genuinely new read-only code path, not a reuse of the
existing services as-is, since those services assume a persisted `Payment` —
budget for a small refactor to extract the pure-matching logic
(`_match_product`/`_match_customer` in `PaymentMatchingService`) into a form
callable without a `Payment` instance.
