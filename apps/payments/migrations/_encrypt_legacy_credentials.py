"""
Shared helper for apps/payments/migrations/0005_encrypt_legacy_tara_credentials.py.
Kept in its own module (not inline in the migration) so the same logic can be
unit-tested directly against the real TaraConfig model without a full migration
executor — see tests/services/test_tara_config_service.py.
"""

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _looks_encrypted(value: str) -> bool:
    """True if `value` decrypts under the current FIELD_ENCRYPTION_KEY — i.e. it's already a valid Fernet token, not legacy plaintext."""
    if not value:
        return True  # nothing to migrate
    key = settings.FIELD_ENCRYPTION_KEY
    if not key:
        return False
    try:
        Fernet(key.encode()).decrypt(value.encode())
        return True
    except (InvalidToken, ValueError):
        return False


def encrypt_legacy_plaintext_credentials(model) -> dict:
    """
    Idempotent safety net: any api_key/webhook_secret value on `model` that does
    NOT decrypt under the current key is treated as legacy plaintext and
    re-encrypted in place. Never logs the value itself, only a count. Safe to
    run repeatedly — already-encrypted values are left untouched, and nothing is
    destroyed (a value is only ever replaced by its own re-encrypted form).
    """
    from shared.security import encrypt_value

    migrated_count = 0
    for config in model.objects.all():
        changed = False
        if config.api_key and not _looks_encrypted(config.api_key):
            config.api_key = encrypt_value(config.api_key)
            changed = True
        if config.webhook_secret and not _looks_encrypted(config.webhook_secret):
            config.webhook_secret = encrypt_value(config.webhook_secret)
            changed = True
        if changed:
            config.save(update_fields=["api_key", "webhook_secret"])
            migrated_count += 1
    return {"migrated_count": migrated_count}
