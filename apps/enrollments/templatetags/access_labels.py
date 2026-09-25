from django import template

register = template.Library()

# Raw ClickFunnels/transport errors (EnrollmentAttempt.error_log) translated
# into one actionable sentence for non-technical staff. The raw text stays
# available in a collapsed "Détail technique" block. First match wins.
_RULES = [
    (("timeout", "timed out"), "ClickFunnels n'a pas répondu à temps. Réessayez dans quelques minutes."),
    (("429", "rate limit", "too many"), "ClickFunnels reçoit trop de demandes en ce moment. Réessayez dans quelques minutes."),
    (("401", "403", "unauthor", "forbidden", "token", "credential"),
     "La connexion à ClickFunnels n'est plus valide. Vérifiez la page « Connexion ClickFunnels »."),
    (("workspace",), "L'espace de travail ClickFunnels n'est pas configuré. Vérifiez la page « Connexion ClickFunnels »."),
    (("404", "not found", "introuvable"), "La formation ou le client est introuvable dans ClickFunnels."),
    (("422", "invalid", "email"), "ClickFunnels a refusé les informations du client (souvent une adresse e-mail invalide)."),
    (("connection", "network", "resolve", "unreachable"),
     "Impossible de joindre ClickFunnels. Réessayez dans quelques minutes."),
]
_DEFAULT = "ClickFunnels a refusé l'ouverture de l'accès. Réessayez ; si cela se reproduit, transmettez le détail technique ci-dessous."


@register.filter
def access_error_plain(error_log):
    if not error_log:
        return ""
    text = str(error_log).lower()
    for needles, sentence in _RULES:
        if any(needle in text for needle in needles):
            return sentence
    return _DEFAULT


@register.filter
def access_state(attempt):
    """(label, tone) of an EnrollmentAttempt in plain words — Actif / Suspendu / Échec."""
    if attempt.status == "SUCCESS":
        return ("Suspendu", "critical") if attempt.cf_suspended else ("Actif", "good")
    return ("Échec", "warn")
