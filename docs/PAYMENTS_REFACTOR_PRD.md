# PRD — Payments / Tara Module Refactor

Status: Draft, not yet approved for implementation.
Owner: TBD.
Scope: `apps/payments`, `integrations/payments/tara`, and their tests
(`tests/apps/payments`, `tests/services/test_*` for payments, `tests/integrations/test_tara_*`,
`tests/api/test_tara_webhook_api.py`).

Companion documents: [`PAYMENTS_REFACTOR_ASSESSMENT.md`](PAYMENTS_REFACTOR_ASSESSMENT.md)
(current-state inventory and pain points) and
[`PAYMENTS_REFACTOR_ROADMAP.md`](PAYMENTS_REFACTOR_ROADMAP.md) (phased plan).

---

## 1. Background

The Tara payment integration (`docs/TARA_INTEGRATION_PROJECT.md`) was built as a series of
numbered phases (the codebase still has comments like `# --- Phase 10: POST
/tara/resend-webhook ---` in `integrations/payments/tara/client.py`). Each phase shipped a working
feature, but nothing has gone back to consolidate the result. The module works — it has ~8,000
lines of tests across payments + Tara — but its internal organization now reflects the order
features were requested in, not the domain boundaries a new contributor would expect.

This refactor is about **internal structure**, not behavior. It should not change what an
administrator, customer, or Tara webhook can do — only how the code that implements those things
is organized, so the next feature phase (and the already-deferred items in
`docs/PHASE2_ROADMAP.md`, e.g. the `Product` → `Offer` rename) lands on a cleaner base.

## 2. Problem statement

- `apps/payments/services.py` is 1,238 lines and holds 9 largely-unrelated service classes
  (Tara credential config, order lifecycle, installment lifecycle, checkout, payment crediting,
  confirmation creation, confirmation delivery, webhook processing). Finding "the checkout logic"
  or "the webhook logic" means scrolling a single file, and an unrelated change (e.g. to
  `WebhookProcessingService`) risks a diff that touches the same file as `CheckoutService` work.
- `apps/payments/reconciliation_services.py`'s `ReconciliationService` is one class whose `run()`
  fans out into locking, stale-attempt verification, transaction-list pulling, confirmation
  processing, provisioning processing, and missing-followup repair — six responsibilities in one
  class with no seams for testing or reusing any one of them independently.
- `apps/payments/admin_services.py` (812 lines) and `apps/payments/admin.py` (565 lines) are
  large but more defensible — each class already maps to one admin action family. Worth
  confirming during the refactor rather than assuming they need to move.
- Tests are split across four directories that don't map cleanly to the source layout:
  `tests/apps/payments/`, `tests/services/` (payments and non-payments services mixed together),
  `tests/integrations/`, and `tests/api/`. Some features have coverage in more than one of these
  (e.g. checkout status has both `tests/apps/payments/test_checkout_status_page.py` and
  `tests/apps/payments/test_checkout_views.py`, plus `tests/services/test_checkout_service.py`
  and `tests/services/test_checkout_concurrency.py`), which makes it hard to tell where a new
  test for a checkout change belongs.
- `integrations/payments/tara/client.py` accretes one method per phase (`# --- Phase N: ... ---`
  section comments); there's no structural grouping by capability (payment links vs. status
  checks vs. webhook handling), so the file's shape is a timeline, not a map.

## 3. Goals

1. Split `apps/payments/services.py` into cohesive modules by domain responsibility (e.g. order
   lifecycle, checkout, crediting/webhook processing, confirmation delivery), each independently
   readable and testable, with the same public service classes/method signatures so calling code
   (views, admin, management commands, tests) needs only import-path updates, not behavioral
   changes.
2. Break `ReconciliationService.run()`'s six jobs into separately named, separately testable
   units, orchestrated by a thin coordinator — so e.g. "stale attempt verification" can be
   understood and tested without loading the whole reconciliation run in your head.
3. Establish (or confirm) one clear rule for where a payments/Tara test lives, and move existing
   tests to match it — no behavior change to the tests themselves, just location and, where
   duplication is found, consolidation.
4. Leave `integrations/payments/tara/client.py` organized by capability rather than by phase
   number, so the next Tara endpoint addition has an obvious place to go.
5. Zero regressions: every existing test passes, unmodified in assertions (moves/renames only),
   after each phase of the roadmap.

## 4. Non-goals

- No behavior changes to checkout, admin actions, webhook processing, or reconciliation logic.
- No changes to the `PaymentAttempt`/`Order`/`Installment` state machines or model schema.
- No database migrations.
- Not addressing the already-tracked `docs/PHASE2_ROADMAP.md` items (e.g. `Product` → `Offer`
  rename, contact merge workflow) — those are separate, deliberately deferred efforts and should
  stay that way unless the user says otherwise.
- No change to the Tara API contract (`docs/TARA_API_CONTRACT.md`) or webhook signature/retry
  behavior.

## 5. Success criteria

- `apps/payments/services.py` no longer exists as a single file, or is reduced to a thin
  re-export/compat surface if that's the chosen approach (see roadmap options).
- No single payments/Tara source file exceeds roughly 400–500 lines without a specific,
  documented reason.
- `ReconciliationService.run()` reads as an orchestration of named steps, each independently
  unit-testable without mocking the entire reconciliation run.
- A new contributor can answer "where do I add a test for X" from directory structure alone.
- Full test suite (`pytest`) passes at the end of every phase in the roadmap, and after the
  final phase, with no reduction in the number of test cases.

## 6. Risks / open questions

- **Import churn:** splitting `services.py` changes import paths everywhere it's used
  (`apps/payments/views.py`, `admin.py`, `admin_services.py`, `api.py`, management commands, and
  every test file that imports from it). This is mechanical but touches a lot of files — needs a
  decision on whether to keep `services.py` as a re-export shim during a transition period or do
  a single atomic cutover (see roadmap Phase 1).
- **Test relocation risk:** moving test files can silently lose fixtures/conftest scoping if not
  done carefully — the roadmap should treat each move as a "run full suite before and after"
  step, not a batch operation.
- **Where does the line sit between "refactor" and "the Phase 2 roadmap items"?** e.g. splitting
  `services.py` by domain may naturally raise the `Product`/`Offer` naming question again. Default
  answer: don't rename models/fields as part of this effort; flag it back to the Phase 2 roadmap
  instead of scope-creeping it in here.
- **Owner and timeline** are not yet assigned — this PRD assumes the work is picked up
  incrementally, phase by phase, rather than as one large PR (see roadmap for sequencing
  rationale).
