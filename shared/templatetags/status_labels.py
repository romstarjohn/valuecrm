from django import template

register = template.Library()

# French display text for every status/enum code shown via
# templates/components/status_badge.html, across every app's models
# (Order, Installment, PaymentAttempt, PaymentConfirmation, ReconciliationRun,
# AdminAuditLog outcome categories, TaraConfig, EnrollmentAttempt...).
# The badge's color (success/danger/warning/info) is decided separately, by
# status_badge.html's own if/elif chain on the raw (English) status value —
# this dict only changes what text is shown, never the color.
_STATUS_LABELS = {
    # Success
    "ACTIVE": "Actif",
    "PAID": "Payé",
    "SUCCEEDED": "Réussi",
    "SUCCESS": "Succès",
    "VALID": "Valide",
    "COMPLETED": "Terminé",
    "SENT": "Envoyé",
    "PROCESSED": "Traité",
    "APPROVED_FOR_RETRY": "Approuvé pour nouvel essai",
    # Danger
    "FAILED": "Échoué",
    "FAILURE": "Échec",
    "CANCELLED": "Annulé",
    "EXPIRED": "Expiré",
    "INVALID": "Invalide",
    "REJECTED": "Rejeté",
    "DENIED_BY_ADMIN": "Refusé par l'administrateur",
    "DISPUTED": "Contesté",
    "CUSTOMER_CANCELLED": "Annulé par le client",
    "REJECTED_INVALID_STATE": "Rejeté (état invalide)",
    "REJECTED_PERMISSION": "Rejeté (permission)",
    # Warning
    "PENDING": "En attente",
    "DUE": "Échéance",
    "PAST_DUE": "En retard",
    "SUSPENDED": "Suspendu",
    "WAIVED": "Exonéré",
    "MANUAL_REVIEW": "Vérification manuelle",
    "AWAITING_APPROVAL": "En attente d'approbation",
    "NEEDS_REVIEW": "À vérifier",
    "PARTIAL_FAILURE": "Échec partiel",
    "UNCORRELATED": "Non corrélé",
    "PROVIDER_NON_FINAL": "Non finalisé (fournisseur)",
    "UNKNOWN": "Inconnu",
    "SKIPPED": "Ignoré",
    "SKIPPED_OVERLAPPING": "Ignoré (chevauchement)",
    # Info / other
    "LINK_CREATED": "Lien créé",
    "CREATED": "Créé",
    "SCHEDULED": "Planifié",
    "RUNNING": "En cours",
    "OFFLINE": "Hors ligne",
}


@register.filter
def status_label(status):
    """French display text for a status/enum code. Falls back to the raw
    (uppercased) code for anything not yet catalogued, rather than failing."""
    if not status:
        return ""
    return _STATUS_LABELS.get(status, status.upper())
