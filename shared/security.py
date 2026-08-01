"""
Fernet symmetric encryption (cryptography.fernet) — AES-128-CBC + HMAC-SHA256
in an encrypt-then-MAC construction, i.e. authenticated encryption: tampered or
corrupted ciphertext raises cryptography.fernet.InvalidToken rather than
silently decrypting to the wrong plaintext. Key comes from FIELD_ENCRYPTION_KEY
(env var via core/settings.py, never hard-coded).
"""

from cryptography.fernet import Fernet
from django.conf import settings

def encrypt_value(value: str) -> str:
    if not value:
        return value
    key = settings.FIELD_ENCRYPTION_KEY
    if not key:
        raise ValueError("FIELD_ENCRYPTION_KEY not found in settings")
    f = Fernet(key.encode())
    return f.encrypt(value.encode()).decode()

def decrypt_value(token: str) -> str:
    if not token:
        return token
    key = settings.FIELD_ENCRYPTION_KEY
    if not key:
        raise ValueError("FIELD_ENCRYPTION_KEY not found in settings")
    f = Fernet(key.encode())
    return f.decrypt(token.encode()).decode()
