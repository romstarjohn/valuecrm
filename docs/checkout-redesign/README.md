# Checkout redesign

The current version adds per-course product mockups and benefits with a shared Mon Afro Libre default. See [Course offers](COURSE_OFFERS.md) for inheritance, overrides, content sources, current validation, and the new artwork prompt.

The public enrollment flow now uses a warm ivory and forest-green palette, editorial headings, tropical African photography, selectable payment plans, and a separate order summary. The purchase form comes first: plans, customer details, summary, and payment action. The companion offer column stacks the mockup above the benefits; on mobile it follows the purchase flow. The public checkout is in French, including validation and number formatting.

Prices distinguish the first installment due today from the full commitment, including the installment interval. The initial summary is rendered on the server. Validation preserves the selected plan, entered details, and idempotency key. The payment button resets when the browser returns from Tara. Existing provider redirects, verified status mapping, automatic refresh, and manual status checks remain in use.

## Review

- `checkout-1440.png`: desktop.
- `checkout-390.png`: mobile.
- Additional screenshots cover 320, 768, and 1024 px plus empty, confirmed, pending, failed, awaiting-payment, and error states.
- `browser-checks.json`: Chrome checks for overflow, image loading, JavaScript errors, plan changes, native validation, submitted plan, browser-back recovery, and initial rendering without JavaScript.

Screenshots render the actual Django templates with illustrative, unsaved course data. Browser payment submission uses a local mock handoff; it does not contact Tara. Course names, descriptions, and amounts on the real checkout come from active payment plans.

Regression command (local PostgreSQL test database):

```sh
pytest tests/apps/payments/test_checkout_plan_list.py tests/apps/payments/test_checkout_views.py tests/apps/payments/test_checkout_status_page.py tests/apps/payments/test_checkout_rate_limiting.py --nomigrations -q --tb=line
```

## Original lifestyle artwork

Production asset: `static/images/checkout/tropical-learning.jpg` (1536 × 1024, approximately 356 KB). Generated with the built-in imagegen tool, then encoded as JPEG for delivery. The photograph is decorative editorial artwork, not a claim about a particular course, student, or location.

Generation prompt:

> Use case: photorealistic-natural. Asset type: editorial photograph for an online course checkout website, landscape 1536x1024. Primary request: a beautiful tropical African context for learning online. Scene: contemporary open-air coastal West African workspace with lush palm fronds, warm terracotta plaster, a timber table, and a soft glimpse of tropical coastline. Subject: one young adult Black African woman in a simple cream linen shirt, natural hair, absorbed in learning on an open laptop, relaxed confident expression, photographed candidly in profile. Composition: medium-wide editorial photograph, person and laptop in the center-right, abundant real foliage and architecture, space at bottom for a web text overlay that will be added separately. Lighting: soft late-afternoon sunlight, rich deep forest greens, warm sandy neutrals, subtle film grain, premium natural magazine photography. Constraints: no lettering, no logos, no watermark, no UI, no collage, no stereotypical costume or safari imagery. Anatomically realistic.
