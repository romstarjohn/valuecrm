# UI vocabulary (staff interface)

The staff interface is written for non-technical staff. One concept = one word,
everywhere. Technical terms only appear under the **Technique** menu.

## Navigation

| Menu | What the user does there | Technical pages behind it |
|---|---|---|
| **À traiter** | The only to-do list: everything that needs a human, one button per item | dashboard |
| **Ventes** | Every sale, with a plain status and the next step | orders |
| **Clients** | Find a person: what they bought, paid, and whether they have access | contacts |
| **Offres** | What is sold: the course, its sales page, its prices, its payment link | courses + checkout offers + payment plans |
| **Accès aux formations** | Who has access to which course, grant/suspend access | enrollments |
| **Réglages** | ClickFunnels and Tara connections | configuration |
| **Technique** (admins) | Raw data: payment attempts, Tara notifications, e-mails, ClickFunnels jobs, reconciliation, audit log | operations lists, Django admin |

## Words

| Use | Never on non-technical pages |
|---|---|
| **Vente** | Commande (ok on Technique pages), Order |
| **Client** | Contact (except "fiche client"), Customer |
| **Offre** — a course as it is sold | Produit, Checkout offer |
| **Page de vente** — texts + image shown on the payment link | Produit, Offre commerciale |
| **Formule de prix** — "1 × 1 000 XAF", "3 × 20 000 XAF" | Plan de paiement, Plan |
| **Versement** — one payment due within a sale | Échéance, Installment |
| **Paiement** — money actually received | Tentative, Attempt |
| **Lien de paiement** — URL to share with customers | Checkout URL, slug |
| **Accès** — the customer can open the course in ClickFunnels | Provisionnement, Inscription (ok as "accès aux formations") |
| **Vérifier le paiement** — ask Tara to confirm | Vérifier le statut Tara |
| **Réf.** `#B40D3341` — first 8 characters | full UUID (detail page only) |

## Sale states (apps/operations/presentation.py::sale_state)

| Label | Tone | Meaning | Next step |
|---|---|---|---|
| Payée | good | Everything paid | — |
| Paiement en plusieurs fois | accent | Some installments paid, none late | — |
| Versement en retard | critical | An installment is overdue | contact the client |
| À vérifier | critical | Tara reported a payment we could not confirm | **Vérifier le paiement** |
| Confirmation en cours | warn | Tara's answer still pending | **Vérifier le paiement** |
| En attente de paiement | neutral | Link opened, nothing paid yet | — (expires automatically) |
| Paiement non démarré | neutral | Tara never produced a link | — (expires automatically) |
| Expirée | neutral | Not paid in time; reactivates if a payment arrives | — |
| Annulée | neutral | Cancelled | — |
| Suspendue | warn | Suspended manually | review |

## Writing rules

- Page subtitle = what the user can do here, in one sentence. Never how it works internally.
- Every empty state says what to do next.
- Every button says what happens ("Vérifier le paiement", not "Confirmer").
- Forms: ask for things the user has (a file, a price), never for things only a developer knows (URLs to static files, slugs, codes, sort numbers). Generate those automatically.
