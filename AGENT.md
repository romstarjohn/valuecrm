# AGENT.md — Brand Identity Integration

This file is an execution contract for whichever agent (Claude Code or otherwise) works on brand
identity in this repository. Read it in full before touching any template, CSS file, or static
asset that affects how the app looks. It exists because the brand assets currently sit unused in
`static/`, and the app's visual language is currently inconsistent — see
"Current state (as found)" below for the specific gaps this file exists to close.

This file governs **look and feel only**: logo usage, typography, color tokens, and where they
apply. It does not change business logic, URLs, models, or the payments/Tara refactor already
planned in `docs/PAYMENTS_REFACTOR_PRD.md` — that work is separate and unaffected by this one.

---

## 1. Source of truth for brand assets

Do not invent brand colors, fonts, or a logo. Everything needed already exists in `static/`:

### 1.1 Logo mark — `static/images/watermark/`

Three finished, pre-rendered PNGs (@4x, transparent background) of the same mark: a circular
badge containing a large stylized "V" checkmark, with "Valued" arched across the top and
".Haircare." arched across the bottom.

| File | Mark color | Sampled hex | Use on |
|---|---|---|---|
| `watermark noir_1@4x.png` | Black | `#000000` | Light backgrounds (white/light cards, login page, print, favicon source) |
| `watermark white@4x.png` | Near-white | `#F6F6F6` | Dark backgrounds (sidebar `#1a1d21`, dark panels) |
| `watermark marron@4x.png` | Brown | `#683B14` | Warm/editorial backgrounds — the checkout page's ivory (`#f7f8f2`) surface is the obvious candidate; treat as the brand's secondary/accent variant |

These are finished art — **do not** try to recreate the wordmark or the "V" shape in HTML/CSS/SVG.
Reference the PNG directly (`<img>`, or `background-image` for decorative/watermark placement).
Pick the variant by contrast against whatever surface it sits on — if in doubt, render both
candidates and check contrast visually (see Section 4) rather than guessing.

The brand name in these assets is **"Valued Haircare."** The app previously called itself
"ValuedCRM" everywhere (page titles, sidebar heading, footer copy). **Resolved:** the tool's own
name and the tenant/storefront name are two different, independently configurable values — see
Section 1.4.

### 1.2 Typefaces — `static/fonts/`

Three families, none currently wired into any CSS (verified: no `@font-face` in the codebase, no
`static/fonts/` reference anywhere). Rendered samples (`/tmp/font_samples.png` from the session
that authored this file — regenerate with the snippet in Section 4.1 if you need to re-check):

- **Bahnschrift** (`Bahnschrift-Font-Family/`, 15 `.TTF` files) — clean geometric grotesque sans.
  This is a single variable-style family shipped as static width/weight instances (Microsoft's
  Bahnschrift ships as combinations of Condensed/SemiCondensed/Normal width × Light/SemiLight/
  Regular/Medium/SemiBold/Bold weight). The plain `BAHNSCHRIFT.TTF` is the normal-width regular
  cut; the numbered files (`BAHNSCHRIFT 1.TTF` … `14.TTF`) are the other width/weight instances.
  **Do not assume which number maps to which named weight** — inspect each file's internal name
  table before wiring multiple `@font-face` weights (e.g. `fonttools ttx -t name <file>` or open
  in Font Book) rather than guessing from the number. **Intended role:** primary UI typeface —
  the natural replacement for the currently-loaded Google-hosted Inter (`--font-sans` in
  `static/css/tokens.css`, loaded via CDN link in `templates/base.html` /
  `templates/public_base.html`).
- **Looper** (`Looper/Looper.ttf`, `Looper-Bold.ttf`) — a casual, rounded, monoline
  handwritten/marker script. **Intended role:** a decorative accent face, not body or UI text —
  e.g. the emphasized word in the checkout headline ("Your next chapter starts *here.*", currently
  styled with plain `<em>` in `static/css/checkout.css:28`), a signature-style flourish, or similar
  sparing warm-touch use. Do not set paragraph or UI copy in it — it's illegible at small sizes.
- **Kingfont Script** (`Kingfont Script/`) — a second, more traditional flowing script.
  Overlaps in purpose with Looper (both are accent scripts, not body faces). **Pick one as the
  primary accent script and treat the other as a documented alternative** — this is a design
  decision, not something to resolve by using both interchangeably; ask the user if it isn't
  obvious from context which one a given placement calls for.

None of these three families should end up used for dense data tables, form labels, or anywhere
legibility at 12-14px matters — that's Bahnschrift's job (or Inter, until Bahnschrift is wired up).

### 1.3 Naming — configurable, not hardcoded (resolved, implemented)

Two distinct names, both driven by Django settings sourced from `.env` (never hardcoded in a
template) via `shared.context_processors.branding`, registered in `TEMPLATES.OPTIONS.
context_processors` in `core/settings.py`:

- **`BRAND_NAME`** (env `BRAND_NAME`, default `"Valued Haircare"`) — this tool's own identity.
  Used on every staff-facing surface: `templates/base.html`'s `<title>` default and footer,
  `templates/includes/sidebar.html`'s heading, and every staff page's `{% block title %}...{{
  BRAND_NAME }}{% endblock %}` (dashboard, contacts, courses, enrollments, operations,
  configuration).
- **`BUSINESS_NAME`** (env `BUSINESS_NAME`, default `"Monafrolibre"`) — the storefront/tenant a
  customer recognizes. Used on the public guest checkout only: `templates/public_base.html`'s
  `<title>`, brand-mark span, and footer, plus the three payment templates that extend it
  (`checkout_form.html`, `checkout_status.html`, `checkout_error.html`).

Available in any template as `{{ BRAND_NAME }}` / `{{ BUSINESS_NAME }}` — every template in the
app already uses these instead of a literal string (verified: `grep -rn "ValuedCRM"` across
`templates/` and `apps/**/templates/` returns nothing). If you add a new page, use these variables
from the start; don't reintroduce a hardcoded name.

Two Django Admin templates (`templates/admin/payments/confirm_action.html` and
`confirm_disposition_action.html`) extend `admin/base_site.html`, not `base.html` — they inherit
whatever the default (or eventually overridden) Django admin chrome shows, same as `/admin/login/`
itself (still unbranded — see Section 2's login-page item, still open).

### 1.4 Existing design tokens — `static/css/tokens.css`

This file is already the intended single source of truth for spacing/color/radius/typography
variables (`:root` custom properties, "Variables only, no rules" per its own header comment).
**Every brand value this effort introduces — font stacks, the brown `#683B14` accent, logo asset
paths if you want them as CSS custom properties — belongs here, as a named token, not hardcoded
into individual template or CSS files.** This is the mechanism that keeps the two currently-
diverged visual languages (see Section 2) from re-diverging once merged.

---

## 2. Current state — what's resolved vs. still open

Originally written as a gap list before any of this landed; now kept as a status record instead
so the next agent doesn't have to re-derive what's already done from the diff.

**Resolved:**
- Real logo mark now used in `templates/public_base.html` (marron variant) and
  `templates/admin/login.html` (noir variant), replacing the old Font Awesome
  seedling/bridge-icon placeholders in both.
- `/admin/login/` is fully rebranded — see `templates/admin/login.html` +
  `static/css/login.css`, a standalone page (doesn't extend Django's `admin/base_site.html`)
  showing `{{ BRAND_NAME }}`, the noir logo, and portal-consistent styling instead of "Django
  administration." Covers the single largest inconsistency the original design review flagged.
- Fonts self-hosted via `static/css/fonts.css` (`@font-face` for Bahnschrift + Looper), loaded
  from both `base.html` and `public_base.html`/`admin/login.html`.
- Naming resolved and implemented — see Section 1.3.
- `ValuedCRM` no longer appears hardcoded anywhere (Section 1.3 already documents this).

**Decided/changed since the original draft — typography direction narrowed:**
- The checkout page's body copy explicitly uses `--font-sans` (Inter), not the Bahnschrift-based
  `--font-brand-sans`/`--font-display` — the earlier draft of this file suggested Bahnschrift as
  the primary UI face everywhere; in practice `.checkout-body` (`static/css/checkout.css`) now
  sets `--font-display` and `--font-accent` to alias `--font-sans` too, so the checkout page's
  headings and the "*here.*"-style accent word no longer render in Bahnschrift/Looper either —
  the whole page is Inter. `tokens.css` and `fonts.css` still define `--font-brand-sans`
  (Bahnschrift) and `--font-accent` (Looper) as available tokens, they're just not applied within
  `.checkout-body` right now. If a future pass wants the display/script distinction back for
  headings specifically (not body copy), that's a re-application of `--font-display`/
  `--font-accent` inside `.checkout-body`, not a fonts.css/tokens.css change.
- The staff portal (`base.css`'s `body` rule) was always Inter (`--font-sans`) — never changed.

**Still open:**
- No favicon set anywhere (Section 3, item 5 below).
- Looper vs. Kingfont Script primary-accent decision (Section 1.2) — moot for now given the
  typography direction above, but still unresolved if display/accent styling returns.
- The two admin-action-confirm templates (`templates/admin/payments/confirm_action.html`,
  `confirm_disposition_action.html`) still extend Django's default `admin/base_site.html` — not
  rebranded. Lower priority than the login page (seen far less often), but same gap in kind.

The staff portal and public checkout still legitimately keep different moods (dark
operational-dashboard vs. warm ivory/editorial) — that was never the inconsistency; both now draw
from the same sourced logo/font assets rather than one running on real brand assets and the other
on placeholders, which was the actual gap.

---

## 3. Integration mechanics — remaining work

1. ~~Self-host the fonts~~ — done, `static/css/fonts.css`.
2. ~~Extend `tokens.css`~~ — done (`--font-brand-sans`, `--font-display`, `--font-accent` all
   defined); note the current typography decision in Section 2 about where they're actually
   applied vs. just defined.
3. ~~Replace every placeholder brand-mark touchpoint~~ — done for `sidebar.html`,
   `public_base.html`, `admin/login.html`. Still open for the two admin-action-confirm templates
   (Section 2).
4. ~~Bring `/admin/login/` in scope~~ — done, `templates/admin/login.html` +
   `static/css/login.css`.
5. **Add a favicon** derived from the noir (solid) mark — still open.

## 4. Verification — don't call this done from reading CSS

Per this project's own standing rule for UI work: start the dev server and actually look at the
rendered pages before reporting brand integration complete. Type-checking/tests don't verify
visual correctness.

### 4.1 How this session did it (repeatable recipe)

Node 22+ and Chrome-remote-debugging-based screenshot tools were unavailable/unconfigured in this
environment; what worked was Playwright, installed into the project's own `.venv` (not added to
`requirements.txt` — it's a review/verification tool, not an app dependency):

```bash
source .venv/bin/activate
pip install playwright Pillow
python -m playwright install chromium
```

Log in with a real (or temporary, then-deleted) superuser and screenshot with a short Python
script using `playwright.sync_api` — navigate to each surface below, `page.screenshot(path=...,
full_page=True)`. To preview a font file directly without a browser, render sample text with
Pillow's `ImageFont.truetype(path, size)` onto a blank canvas — much faster than a full page
render when you just need to confirm what a `.ttf`/`.otf` actually looks like.

### 4.2 Surfaces to check after any brand change

At minimum, re-screenshot and eyeball:
- `/admin/login/` (or its replacement) — light background, logo contrast
- `/` dashboard — dark sidebar, logo contrast against `#1a1d21`
- `/checkout/` — ivory background, the display/script font in the headline
- Any page with a data table (e.g. `/operations/orders/`) — confirm Bahnschrift (if wired as
  `--font-sans`) stays legible at small table-text sizes; this is exactly the kind of regression a
  "swap the font" change can cause without a visual check

Do not consider brand integration finished until you've looked at both a dark-background and a
light-background surface with the logo on it, and at least one dense data table with the new body
font.

---

## 5. Non-goals / guardrails

- Don't touch the payments/Tara refactor scope (`docs/PAYMENTS_REFACTOR_PRD.md` and its companion
  docs) — unrelated effort, already scoped separately.
- Don't invent brand colors that aren't derived from the supplied assets (the three logo variants,
  the existing checkout green) or explicitly approved by the user.
- Don't change semantic/status colors (success/danger/warning green/red/amber in `tokens.css`) —
  those communicate state, not brand, and were not flagged as a brand inconsistency.
- Don't hardcode "Valued Haircare", "Monafrolibre", or any other literal brand/business name in a
  template or CSS file — always go through `{{ BRAND_NAME }}` / `{{ BUSINESS_NAME }}` (Section
  1.3), so a future deployment for a different tenant only needs new `.env` values, not template
  edits.
- Don't merge the checkout page's palette into the portal's (or vice versa) without being asked —
  giving both surfaces the same *sourced* assets is in scope; forcing one unified color palette
  across both is a bigger decision this file doesn't make.
