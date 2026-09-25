# Roadmap — Payments / Tara Module Refactor

Companion to [`PAYMENTS_REFACTOR_PRD.md`](PAYMENTS_REFACTOR_PRD.md) (goals/non-goals) and
[`PAYMENTS_REFACTOR_ASSESSMENT.md`](PAYMENTS_REFACTOR_ASSESSMENT.md) (current-state detail this
plan is based on).

Treat each phase below as a starting brief, not a finished spec — validate file names, line
numbers, and class boundaries against the codebase at the time it's picked up, since the current
uncommitted webhook-resend work (and whatever lands after it) will shift them. Each phase should
land as its own PR/commit, in order, with the full test suite green before moving to the next —
this is a mechanical, low-risk-per-step refactor, not a big-bang rewrite.

---

## Sequencing rationale

Order matters here: split the *service* layer before touching *tests*, because test relocation is
much easier to get right once you know the final import paths. Do the smallest, most isolated
piece (`ReconciliationService`) as a warm-up before the largest, most load-bearing one
(`services.py`), so the team validates the approach (and the "keep the shim or cut over
atomically" decision) on lower-stakes code first.

## Phase 0 — Decide the transition strategy (no code changes)

Before writing any code, get explicit sign-off on:

1. **Shim vs. atomic cutover** for `services.py`: either (a) split into new modules and leave
   `services.py` as a file that re-imports and re-exports everything from its new home (so no
   caller needs to change in the same PR), or (b) split and update every import site in one PR.
   (a) is safer to review incrementally but leaves a temporary indirection layer to clean up
   later; (b) is a bigger single diff but leaves no residue. Recommendation: (a) for `services.py`
   given how many call sites it has (views, admin, admin_services, api, management commands, ~20+
   test files), with an explicit follow-up phase to remove the shim once all imports are updated.
2. **Target module names** for the `services.py` split (see Phase 1) — the groupings in the
   assessment doc are a proposal, not a decision.
3. **Test directory convention** going forward (see Phase 3) — e.g. "one test dir per source
   package, mirroring `apps/payments/`'s internal module layout once Phase 1 lands."

## Phase 1 — Split `apps/payments/services.py`

Break the single file into cohesion-based modules. Based on the assessment's grouping, a
starting proposal (confirm in Phase 0):

- `order_lifecycle_service.py` — `OrderService`, `InstallmentService`, `PaymentAttemptService`
- `checkout_service.py` — `CheckoutResult`, `CheckoutService`
- `tara_config_service.py` — `TaraConfigService`
- `payment_processing_service.py` — `PaymentCreditService`, `WebhookProcessingService`
- `payment_confirmation_service.py` — `PaymentConfirmationService`,
  `PaymentConfirmationDeliveryService`

Steps:

1. Create the new files, move each class verbatim (no logic changes), fix internal imports
   between them.
2. Apply the Phase 0 shim decision in `services.py`.
3. Run the full test suite — every test should pass unmodified, since nothing about behavior or
   public import paths (if using the shim) has changed.
4. If using the shim: open follow-up tickets/tasks to migrate each call site
   (`apps/payments/views.py`, `admin.py`, `admin_services.py`, `api.py`, management commands, and
   test files) off `services.py` and onto the new modules directly, then delete the shim. This
   can be done incrementally, file by file, each as its own small PR.

## Phase 2 — Decompose `ReconciliationService`

Extract each of the six responsibilities identified in the assessment
(`_verify_stale_attempts`, `_pull_transaction_list_for_reporting`, `_process_confirmations`,
`_process_provisioning`, `_repair_missing_followups`, plus the locking wrapper) into its own
class or function with a name that describes the job, not the fact that it's step N of a run.
`ReconciliationService.run()` becomes a thin coordinator that calls each step and aggregates the
resulting counters — same external behavior (still one `ReconciliationRun` record, same counters,
same return shape), same call sites (the `run_hourly_reconciliation` management command and its
test), just each step independently importable and unit-testable without exercising the whole
run. Verify against `tests/services/test_reconciliation_service.py` and
`tests/services/test_reconciliation_lock.py` — these should still pass essentially as-is if the
externally observable behavior hasn't changed; if either test file needs invasive changes to keep
passing, that's a signal the split changed behavior, not just structure.

## Phase 3 — Consolidate and relocate tests

1. Apply the Phase 0 test-directory convention.
2. For each area with overlapping coverage flagged in the assessment (checkout status across
   three files; anything else discovered while moving things), read the overlapping tests first
   and confirm whether they're legitimately layered (e.g. view-level vs. service-level) or
   duplicating the same assertion at two levels — only consolidate the latter.
3. Move files into their new homes as pure moves (`git mv`) where content doesn't change, so
   history is preserved; only touch imports.
4. Rename phase-numbered test files to behavior-based names (e.g.
   `tests/services/test_phase7_concurrency.py` → whatever concurrency behavior it actually
   covers — read it first to find out).
5. Run the full suite after every batch of moves, not just at the end — moved fixtures/conftest
   scoping is the most likely thing to silently break.

## Phase 4 — Reorganize `integrations/payments/tara/client.py`

Regroup `TaraClient`'s methods by capability (payment link creation, transaction status/list,
webhook resend, webhook signature verification) instead of by the `# --- Phase N: ... ---`
section comments currently marking them. Pure reordering/re-commenting within the same file and
class — no split into multiple files unless the file's grown large enough by this point to
warrant it (re-check against the assessment's size guidance). Update/remove the phase-number
comments in favor of capability-based section headers.

## Phase 5 — Retrospective

Once Phases 1–4 have landed: confirm every success criterion in the PRD, remove any remaining
shims from Phase 1, and fold any newly-discovered deferred items (e.g. if the `services.py` split
surfaces a real case for the `Product`→`Offer` rename) into `docs/PHASE2_ROADMAP.md` rather than
pulling them into this effort retroactively.

---

## What to do if something doesn't fit this plan

If, while executing a phase, the actual code doesn't match what the assessment doc describes
(classes moved, line counts way off, a "Phase 10" feature merged that changes the picture), stop
and reconcile the assessment doc against reality before continuing — don't silently improvise a
different split than what got signed off in Phase 0.
