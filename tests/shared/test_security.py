import pytest
from shared.security import encrypt_value, decrypt_value
from cryptography.fernet import InvalidToken

def test_encrypt_decrypt_cycle():
    original_value = "secret-api-key-123"
    encrypted = encrypt_value(original_value)
    assert encrypted != original_value
    
    decrypted = decrypt_value(encrypted)
    assert decrypted == original_value

def test_encrypt_none_returns_none():
    assert encrypt_value(None) is None
    assert encrypt_value("") == ""

def test_decrypt_none_returns_none():
    assert decrypt_value(None) is None
    assert decrypt_value("") == ""

def test_decrypt_invalid_token_raises_error():
    with pytest.raises(InvalidToken):
        decrypt_value("invalid-token-string")
