# Claude Implementation Instructions — Tara Payments

You are the senior engineer responsible for implementing the Tara payment integration in this repository.

This file is an execution contract for you, not a user-facing project document. Follow it throughout the implementation. The existing repository is authoritative for architecture, coding style, naming, UI, CSS, persistence, validation, authorization, jobs, testing, and error handling.

Do not begin implementation by generating generic scaffolding. First inspect the repository and prove which existing patterns you will reuse.

---

## 1. Objective

Extend the existing application so an authorized administrator can:

1. configure Tara credentials for its organization/account;
2. create and manage sellable payment plans;
3. link each payment plan to an existing course already known by the application;
4. configure the plan’s price, currency, installment count, and installment schedule;
5. publish active plans to the web-app checkout;
6. receive and track customer payments;
7. automatically enroll eligible customers in the linked ClickFunnels course;
8. manually administer orders, installments, enrollment, and business dispositions without altering provider history;
9. reconcile missed or uncertain payment events automatically every hour.

The application database is authoritative for products/plans, orders, installments, customers, access policy, and enrollment. Tara is the payment provider. ClickFunnels is the external course-access system.

---

## 2. Non-negotiable repository rules

- Read all repository instruction files before changing code.
- Inspect existing implementations before proposing new files.
- Reuse the existing component library, CSS/layout system, table patterns, forms, modals, badges, notifications, page shells, services, repositories, validation, errors, authorization, logging, jobs, and tests.
- Do not introduce a new framework, UI library, CSS system, ORM, HTTP client, queue, scheduler, state manager, logger, encryption library, or test framework if an equivalent already exists.
- Do not create a parallel architecture for the Tara feature.
- Follow the dominant conventions in the closest comparable modules.
- Preserve unrelated code and user changes.
- Make the smallest coherent change for the active phase.
- Never invent a Tara endpoint, field, signature scheme, provider state, or idempotency guarantee.
- Never claim a check passed unless you ran it successfully.

When repository evidence is missing, use:

```text
Known from repository:
Known from Tara documentation:
Assumption:
Risk if assumption is wrong:
Decision required:
```

Stop for a decision when an unknown changes financial behavior, credential security, tenant isolation, payment confirmation, or course access.

---

## 3. Required repository discovery

Phase 0 is read-only. Do not change production code.

Locate and report:

1. repository instructions and contribution rules;
2. runtime, framework, database, ORM/data layer, routing, and module boundaries;
3. formatter, linter, type-checker, tests, build, migration, and CI commands;
4. tenant/organization ownership and authorization patterns;
5. at least two comparable admin configuration pages;
6. at least two comparable data tables and edit forms;
7. existing shared layout, card, form, input, select, table, badge, modal, toast, loading, empty-state, pagination, and responsive CSS patterns;
8. existing encrypted-setting or secret-storage implementation;
9. existing HTTP provider clients and their timeout/error/redaction conventions;
10. existing webhook endpoints, raw-body handling, validation, deduplication, and audit behavior;
11. existing background jobs, scheduler, queue, retry, locking, and monitoring patterns;
12. existing customer notification mechanisms;
13. existing course model/source and how available courses are queried;
14. the existing ClickFunnels course-assignment flow from entry point to external call;
15. tests for all relevant examples.

For every pattern you propose to reuse, cite its exact repository path and symbol.

Phase 0 output must include:

- repository pattern map;
- course-source and ClickFunnels flow map;
- proposed domain mapping;
- proposed file-level changes;
- migration approach;
- security and idempotency design;
- test strategy using existing infrastructure;
- phased implementation plan;
- blockers and questions;
- verification commands.

Stop after this report and wait for approval.

---

## 4. Administrator-configurable products and payment plans

Do not hard-code the example prices as application behavior.

Administrators must be able to create, edit, activate, deactivate, and order payment plans using the backend conventions already present in the repository.

Each payment plan must reference an existing course. Do not create a duplicate course catalog for Tara.

### Required plan fields

- internal immutable identifier;
- organization/account ownership where applicable;
- stable unique code or slug;
- administrator-defined display name;
- optional public description;
- reference to an existing course;
- currency;
- installment count;
- amount per installment or an installment schedule;
- computed total;
- interval/due-date policy;
- access policy;
- active/published state;
- display order;
- creation/update metadata.

### Course linking

- The course field must use the repository’s existing course model, service, or API.
- The administrator must select from existing courses with the established select/autocomplete component.
- Persist the repository’s stable course identifier.
- Validate that the selected course exists and belongs to the correct organization/account.
- Display useful course metadata in the selector using existing UI patterns.
- Prevent deletion or unsafe mutation of a plan that has historical orders.
- If a course is archived or removed, preserve historical references and block new purchases according to existing application rules.
- The enrollment job must use the course reference stored on the order snapshot, not a browser-submitted course value.

### Pricing configuration

- Pricing is entered and validated in the admin backend.
- Amounts must use the repository’s established money representation. Prefer integer minor units when no existing money type dictates otherwise.
- Do not use floating-point arithmetic for money.
- The checkout browser may submit only a plan identifier; it must never be authoritative for amount, currency, installment count, course, or access policy.
- The backend loads the active plan and calculates the order from stored configuration.
- Existing orders retain a snapshot of the plan terms at purchase time.
- Editing a plan affects future orders only.
- The total must be derived deterministically and displayed before activation.
- Validate positive amounts, supported currency, installment count, schedule consistency, and uniqueness.

The initial examples—100,000 XAF once, 34,000 XAF three times, and 35,000 XAF five times—are seed/example values only. If the repository uses seeds or fixtures for default plans, implement them using its established mechanism. Do not make them permanent constants in payment logic.

### Installment options

First inspect whether the existing application supports structured schedules.

Choose the smallest model that satisfies confirmed requirements:

- **Uniform installments:** `installmentCount × installmentAmount`.
- **Custom schedule:** child rows containing sequence, amount, and due-offset/rule.

Do not implement both unless the requirements or repository justify both. Present the tradeoff during Phase 0.

### Access policy

The plan must explicitly configure when the linked course becomes eligible:

- after the first verified installment;
- after full payment;
- another supported policy only if approved.

Do not infer access eligibility from plan name or installment count.

---

## 5. Tara configuration

Authorized administrators must configure:

- Tara `businessId`;
- Tara API key;
- Tara webhook secret;
- environment if the provider supports distinct environments;
- enabled/disabled state.

Requirements:

- use the repository’s existing secret-management/encryption abstraction;
- if no safe abstraction exists, stop and propose one before implementation;
- API key and webhook secret are write-only;
- never return decrypted secrets to the frontend;
- display only masked metadata;
- never log credentials or include them in audit payloads;
- support safe replacement/rotation;
- scope credentials to the correct organization/account;
- authorize all configuration actions server-side;
- audit create, replace, enable, disable, and connection-test actions without recording secret values;
- generate the webhook URL in the application; do not accept an arbitrary per-order webhook URL from the browser.

The webhook secret is used locally to verify Tara notifications. Do not send it in payment-link requests unless official Tara documentation explicitly requires it.

---

## 6. Logical domain model

Map these concepts onto existing repository conventions. Do not force these exact class or table names if the repository has established alternatives.

### Payment plan

Administrator-configured commercial terms linked to an existing course.

### Order

A customer’s purchase of a snapshot of one payment plan.

Store a snapshot sufficient to preserve historical truth:

- plan identifier and display name;
- linked course identifier;
- currency;
- installment schedule;
- total;
- access policy.

### Installment

One expected portion of an order, with sequence, amount, due date/rule, and business state.

### Payment attempt

One provider interaction for an installment.

An installment may have multiple failed/expired attempts. Give each attempt a unique Tara `productId`.

### Webhook event

Immutable record of the provider notification, verification result, and processing result.

### Enrollment

Idempotent record of eligibility and ClickFunnels assignment for the customer/course.

### Notification

Idempotent delivery record for payment confirmations and reminders.

### Audit event

Immutable record of sensitive administrative actions.

---

## 7. State separation

Do not combine provider status, payment-attempt state, installment state, order state, refund/dispute state, and enrollment state.

Suggested logical transitions:

```text
Payment attempt:
CREATED → LINK_CREATED → PENDING → SUCCEEDED | FAILED | EXPIRED | UNKNOWN

Installment:
SCHEDULED → DUE → PENDING → PAID | FAILED | WAIVED | CANCELLED

Order:
PENDING → ACTIVE | PAST_DUE | COMPLETED | CANCELLED | SUSPENDED

Enrollment:
NOT_ELIGIBLE → PENDING → ACTIVE | FAILED | SUSPENDED | REVOKED
```

Use repository naming conventions if they differ.

Rules:

- a Tara-confirmed successful payment remains successful;
- cancelling an order does not rewrite a successful provider payment;
- refunds/disputes must be separate facts;
- manual disposition is separate from provider status;
- final transitions must be protected by transactions/locking;
- duplicate and out-of-order events must be harmless;
- sensitive manual transitions require authorization, confirmation, reason, and audit.

---

## 8. Checkout workflow

1. Checkout displays active plans loaded from the backend.
2. Each plan displays its linked course and administrator-configured pricing.
3. Customer selects a plan and supplies required identity/contact information.
4. Backend authorizes/scopes the request where required.
5. Backend loads the authoritative plan and course.
6. Backend creates order snapshot, installments, and first payment attempt atomically.
7. Backend uses a request idempotency key to avoid duplicate orders.
8. Backend calls `POST /tara/paymentlinks` with the current attempt’s unique `productId`.
9. Backend stores the redacted provider request/response and returned links.
10. Backend returns only an allowlisted Tara checkout URL.
11. Browser redirects to Tara.
12. Return page displays backend status only; it never confirms payment from query parameters.

Use fixed application-owned `returnUrl` and `webHookUrl` values. Never trust callback URLs or price data from the browser.

Normally create a link only for the currently payable installment. Generate later links near their due date unless Tara confirms safe link lifetime and reuse behavior.

---

## 9. Tara APIs

Required initial scope:

- `POST /tara/paymentlinks`;
- webhook receiver;
- `POST /tara/transactions/status`;
- `POST /tara/transactionlist` or `/tara/paid/transactionlist`;
- optionally `POST /tara/resend-webhook` for an authorized recovery action.

Deferred:

- direct Mobile Money initiation;
- paid identity lookup;
- all payout APIs.

Do not implement Tara “Collecte” creation unless an official endpoint and contract are supplied. The available information describes collection webhooks but does not define a collection-creation API.

---

## 10. Webhook processing

The handler must:

1. enforce method, media type, and body-size limits;
2. retain raw bytes when required for signature verification;
3. verify the official Tara signature using constant-time comparison;
4. validate timestamp and replay window if supported;
5. strictly validate the JSON schema;
6. locate the candidate organization safely;
7. verify payload `businessId` against configuration;
8. locate the payment attempt using provider identifiers;
9. validate amount and currency when present;
10. deduplicate the event;
11. persist the event before side effects;
12. apply valid state transitions atomically;
13. return 2xx quickly after durable acceptance;
14. enqueue confirmation and enrollment work.

Tara’s signature header, algorithm, encoding, signed message, and replay specification are currently unknown. Do not invent them.

If cryptographic verification is unavailable, treat the webhook only as a trigger. Confirm payment server-to-server through Tara before marking it paid or granting access.

Never grant course access solely because:

- Tara returned a payment link;
- the browser reached the return URL;
- an initial Mobile Money request returned `SUCCESS`;
- an unverified webhook claimed `SUCCESS`.

---

## 11. ClickFunnels enrollment

Do not replace the existing working enrollment implementation.

During discovery:

- identify its public entry point;
- determine expected identifiers;
- understand whether it creates users, assigns courses, or both;
- inspect idempotency and failure behavior;
- locate its tests.

Wrap or orchestrate it using existing application patterns so that:

- enrollment is queued/asynchronous where supported;
- the idempotency identity is customer plus course;
- repeated successful payment events do not duplicate access;
- already-enrolled users resolve successfully;
- recoverable failures retry with bounded backoff;
- terminal failures enter an inspectable/manual-retry state;
- every attempt is recorded without leaking sensitive data.

Enrollment uses the order’s course snapshot and verified access policy.

---

## 12. Customer confirmations

Use the existing notification infrastructure.

Send a payment confirmation only after definitive payment verification.

Include only appropriate information:

- customer name;
- amount and currency;
- payment/order reference;
- plan/course name;
- installment number;
- remaining balance;
- next due date when applicable;
- access/enrollment status.

Use a deterministic notification idempotency key so duplicate webhooks cannot send duplicate confirmations.

---

## 13. Administrator payment operations

Build pages with existing admin layout and components.

Required views:

- Tara configuration;
- configurable payment-plan list and edit form;
- plan-to-existing-course selection;
- orders list;
- order detail;
- installments and immutable provider attempts;
- enrollment status and attempts;
- audit history where existing conventions support it.

Required or candidate actions:

- activate/deactivate a plan;
- check Tara status;
- retry enrollment;
- resend confirmation;
- cancel an order;
- waive or cancel a future installment where allowed;
- apply a manual business disposition;
- resend a webhook only if the Tara API behavior is confirmed.

Do not add an unrestricted “mark paid” button. If an approved manual verification path is required, store it as a distinct, highly privileged and audited disposition without changing Tara’s recorded status.

---

## 14. Hourly reconciliation

Implement using the repository’s existing scheduler/job mechanism.

The job must:

- find stale `PENDING` or `UNKNOWN` payment attempts;
- query Tara status safely;
- reconcile paid transaction lists where supported;
- detect provider payments without matching local attempts;
- detect amount, currency, business, or identifier mismatches;
- recover verified paid orders missing confirmation or enrollment;
- mark due/overdue installments according to configured rules;
- create the next payable attempt/link according to policy;
- emit an inspectable run summary and alerts;
- use locking or uniqueness controls so concurrent workers cannot duplicate transitions, messages, links, or enrollment.

The webhook is the fast path. The hourly job is the recovery path. Do not intentionally delay every successful enrollment by one hour.

---

## 15. Security requirements

- rotate previously exposed Tara credentials before production;
- encrypt recoverable provider secrets at rest;
- keep encryption keys outside the application database;
- never expose decrypted credentials to frontend APIs;
- mask settings;
- enforce tenant isolation on every query and mutation;
- use unpredictable identifiers;
- validate and normalize inputs;
- use HTTPS and repository-standard session/cookie protections;
- apply CSRF protection when relevant;
- rate-limit checkout, credential, status, and webhook endpoints;
- use fixed callback URLs and a redirect allowlist;
- redact secrets and unnecessary PII from logs;
- use strict provider schemas and bounded response sizes;
- treat provider timeouts as unknown outcomes;
- do not automatically retry non-idempotent calls without verification;
- use database uniqueness constraints in addition to application checks;
- require strong authorization and audit for manual financial actions;
- never call payout APIs as part of this feature.

---

## 16. Iterative implementation phases

Implement one approved phase at a time.

### Phase 0 — Discovery

Read-only repository analysis and file-level plan. Stop for approval.

### Phase 1 — Configurable plans and course linkage

- persistence/migrations;
- plan CRUD/configuration following existing admin patterns;
- existing-course selector;
- price/installment/access-policy validation;
- plan snapshots for historical orders;
- authorization and tests.

### Phase 2 — Tara credentials

- encrypted per-organization configuration;
- masked read model;
- save/replace/disable/test behavior;
- admin UI;
- authorization, audit, and tests.

### Phase 3 — Payment domain

- orders;
- installments;
- payment attempts;
- provider and business states;
- constraints, indexes, transactions, and tests.

### Phase 4 — Tara client

- typed provider boundary;
- payment-link and status operations;
- timeout, redaction, and normalized errors;
- fixtures/fakes and tests;
- no real payments in automated tests.

### Phase 5 — Checkout

- active plan display;
- server-authoritative pricing;
- idempotent order creation;
- payment-link creation;
- safe redirect and return/status page;
- tests for browser tampering and duplicate submission.

### Phase 6 — Verified webhooks

- raw-body support;
- official signature verification;
- strict validation;
- tenant/business verification;
- deduplication and replay protection;
- atomic state transitions;
- duplicate, out-of-order, mismatch, and invalid-signature tests.

### Phase 7 — Confirmation and enrollment

- existing ClickFunnels code reuse;
- idempotent enrollment jobs;
- customer confirmation;
- retry/manual recovery;
- external-failure tests.

### Phase 8 — Admin operations

- order/payment/enrollment views;
- safe manual actions;
- audit history;
- existing table/form/modal/status styling;
- authorization tests.

### Phase 9 — Reconciliation and hardening

- hourly job;
- concurrency protection;
- monitoring and alerts;
- end-to-end tests;
- security review;
- rollout, rollback, and operational runbook.

---

## 17. Required iteration loop

For every phase:

1. re-read the closest existing examples;
2. cite exact files and symbols being followed;
3. propose the smallest coherent change;
4. identify assumptions and blockers;
5. implement only the approved scope;
6. add/update tests using existing conventions;
7. run formatting and linting;
8. run type checks where applicable;
9. run focused tests;
10. run broader tests/build where practical;
11. inspect the complete diff;
12. review tenant isolation, authorization, secret handling, idempotency, transactions, concurrency, logging, and error handling;
13. inspect affected UI using the repository’s normal preview/browser/screenshot workflow;
14. fix findings and repeat checks;
15. report evidence and stop for the next approval when requested.

Do not say “done” when checks are failing, skipped without disclosure, or blocked.

---

## 18. Required phase report

```markdown
## Phase result

### Implemented
- ...

### Repository patterns reused
- `path/to/file`: exact symbol/pattern

### Files changed
- `path/to/file`: reason

### Verification
- `command` — PASS/FAIL/NOT RUN

### UI review
- Existing example compared:
- Result:

### Self-review
- Finding:
- Resolution:

### Security
- Tenant isolation:
- Authorization:
- Secret handling:
- Idempotency/concurrency:
- Webhook trust:

### Assumptions and blockers
- ...

### Remaining work
- ...
```

---

## 19. Definition of done

Do not declare the integration complete until:

- administrators can securely configure Tara;
- administrators can create and edit pricing/installment plans;
- every plan is linked to an existing valid course;
- existing orders preserve purchased pricing/course terms after plan edits;
- checkout loads server-authoritative plan data;
- unique orders and payment attempts are idempotent;
- payment links are generated server-side;
- only verified final payments update installments;
- duplicate and out-of-order events are harmless;
- confirmation and ClickFunnels enrollment occur exactly once logically;
- failed enrollment and notifications can recover;
- hourly reconciliation is concurrency-safe;
- manual actions are authorized and audited;
- tenant-isolation tests pass;
- security tests cover signature failure, replay, browser tampering, unknown identifiers, amount mismatch, and unauthorized administration;
- format, lint, type, unit, integration, build, and migration checks pass as applicable;
- affected UI matches existing layout and CSS patterns;
- monitoring, alerts, rollout, rollback, and runbook are ready;
- no unresolved critical or high-severity issue remains.

---

## 20. Initial prompt

Begin with this instruction:

```text
Read TARA_INTEGRATION_PROJECT.md and all repository instruction files.

Perform Phase 0 only. Do not modify production code.

Inspect the existing repository and produce the required discovery report. Your
recommendations must cite exact repository files and symbols. Pay particular
attention to:

- existing admin styling and reusable table/form/layout components;
- the source of existing courses and how a configuration form should select one;
- the existing ClickFunnels enrollment implementation;
- tenant scoping and authorization;
- encrypted settings/secrets;
- provider clients, webhooks, jobs, notifications, audit logs, and tests.

Propose how configurable payment plans should reference existing courses and
preserve a snapshot of course/pricing terms on each order.

Do not invent missing repository or Tara behavior. List blockers using the
Known/Assumption/Risk/Decision format. Stop after the Phase 0 report and wait
for approval.
```

