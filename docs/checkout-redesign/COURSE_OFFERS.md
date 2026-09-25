# Course mockups and offer content

The French checkout starts with the payment plan, contact information with field explanations, then the order summary and payment button. On desktop, the companion column places the product mockup above the full benefits list. On mobile, the purchase flow appears before this offer content. The selected course determines the offer and available plans. All course descriptions and plans remain available without JavaScript; JavaScript adds course filtering and live summary updates.

## Default and overrides

`CheckoutOffer` stores the presentation: title, subtitle, language, product image, image alternative text, highlights, and closing note. Ordered `CheckoutBenefit` records store the full inclusion list, including highlighted bonuses.

One offer may be the shared default. The migration installs **Cheveux crépus longs et libres — La méthode ultime en milieu tropical** as that default, as requested. Every existing or newly created course inherits this content until an explicit offer is attached to that course. The relationship is one-to-one, so each override belongs to one course. ClickFunnels course synchronization does not overwrite these locally managed presentations.

Manage profiles at `/admin/courses/checkoutoffer/`:

- Edit the default to change the shared base presentation.
- Create an offer and select a course to override only that course.
- Add or reorder included benefits, flag a bonus, and supply that course’s mockup.
- Use an HTTPS image URL or a bundled static path beginning with `images/`.

Prices, currencies, installment schedules, and access policies still come from active `PaymentPlan` records. The template does not create a new purchasable course or change existing prices. Local `Bootcamp` remains a test course and inherits the default presentation. Future production courses inherit the same base automatically.

## Content sources

- [Production course](https://monafrolibre.com/cheveux-crepus-longs-et-libres-la-methode-ultime-en-milieu-tropical/): current program title and course offer.
- [Reference checkout](https://formation.monafrolibre.com/-rejoindre-4-semaines-pour-avoir-les-locs-libres): product-mockup and inclusion-list structure.
- The user’s supplied copy: more than ten hours of training, transformation techniques, private resources, VIP replies within 24 hours seven days a week, lifetime access and updates, and the included natural-care training.

The production page also advertises progressive module release for installment plans. This is a separate access-control requirement: the current payment model supports first-payment or full-payment eligibility, not per-module release. This change does not claim to implement progressive access. Access timing on checkout continues to follow the selected payment plan.

## Review

70 tests passed, including new-course inheritance, course-specific overrides, content escaping, safe image paths, inactive-course visibility, single-default enforcement, synchronization preservation, required-phone enforcement before order creation, Tara callback/return URLs, French validation and locale isolation, and checkout regressions. Tests ran against PostgreSQL with actual migrations on a rebuilt test database.

```sh
pytest tests/apps/payments/test_checkout_offers.py tests/apps/payments/test_checkout_plan_list.py tests/apps/payments/test_checkout_views.py tests/apps/payments/test_checkout_status_page.py tests/apps/payments/test_checkout_rate_limiting.py tests/services/test_course_service.py tests/services/test_checkout_service.py::test_tara_called_with_authoritative_data_not_browser_input tests/services/test_checkout_service.py::test_return_url_contains_only_opaque_signed_reference --migrations -q --tb=line
```

Chrome checks passed at 320, 390, 768, 1024, and 1440 px, including course switching, offer visibility, filtering of plans, summary values, purchase-step ordering at every width, mockup placement above benefits, French browser and server validation, mock payment submission, browser-back recovery, and the no-JavaScript fallback. Screenshots in this directory use unsaved illustrative course data; no live payment was made.

## Artwork

The default product mockup is `static/images/checkout/monafrolibre-tropical-course.jpg` (1536 × 1024, approximately 440 KB), generated using the built-in imagegen tool and encoded as JPEG. The page identifies it as a digital-program presentation, with no physical products shipped.

Generation prompt:

> Use case: product-mockup. Asset type: landscape 1536x1024 product bundle image for Mon Afro Libre online hair-care training checkout. Create a premium photorealistic studio arrangement of a large desktop monitor, a small tablet in front, and an upright slim course workbook, on warm ivory limestone with soft shadows, against a pale warm cream background. On the coordinated screens and workbook, show a tasteful editorial portrait of an adult Black African woman with beautiful long natural tightly coiled hair, with dark forest green design, subtle palm leaf details and muted gold typography. Main title clearly and exactly 'CHEVEUX CRÉPUS' and below 'LONGS & LIBRES'. Small brand text 'MON AFRO LIBRE'. Style: sophisticated botanical beauty education brand, realistic devices, balanced front three-quarter view, lush tropical African visual context, minimal uncluttered premium presentation, no other readable labels. All objects fully inside the image with generous margins, centered arrangement. This is a digital course presentation, no skincare bottles, no payment UI, no pricing, no invented testimonials, no watermark.

## French checkout and digital delivery

The public pages, helper copy, validation, payment status, error messages, and number formatting are French. The locale is scoped to the public checkout views and does not change staff-interface language. Editable course and plan names remain the configured catalog content. The email field identifies where the order confirmation and enrollment information are sent. The email and phone are required; optional name fields explain their purpose; no shipping address is requested for a digital course.

The introductory instruction sentence and the progress indicator have been removed. After successful form validation, the browser is redirected to Tara. Tara’s return URL brings it back to the signed order-status page; the webhook independently confirms the payment, and the pending page refreshes until confirmation arrives. A webhook does not itself redirect the browser.

## Checkout brand typography and name

The public header, footer, and page titles combine the configured `BRAND_NAME` and `BUSINESS_NAME`: Valued Haircare & Monafrolibre. The brown supplied logo replaces the placeholder seedling. Public checkout text uses locally hosted Bahnschrift; the headline's accent uses Looper. Kingfont Script remains an unused alternative. Shared brand tokens live in tokens.css; the checkout opts into them without changing staff typography in this checkout-focused update. The font's internal tables confirm Bahnschrift is variable (weight 300–700, width 75–100), so one file supplies all required weights at normal width.

## Readability adjustment

Checkout body text, field labels, inputs, benefit copy, and the payment button use 18 px at the default browser size; helper copy uses 16 px and small badges/captions use at least 14 px. Sizes are in rem to respect text enlargement. Bahnschrift uses its normal-width axis. Muted text is darker (#4e5b52), field boundaries and placeholders have stronger contrast, and the layout switches to one column at 1000 px.

Chrome verified 320, 390, 768, 1024, and 1440 px: the local font loads, text sizes meet the above values, and checkout interactions continue to work. Measured secondary text contrast against the page background is 6.68:1; the payment button text contrast is 9.58:1. Text enlarged to 200% at 390 px produced no horizontal page overflow. These are targeted readability checks, not a full accessibility audit.
