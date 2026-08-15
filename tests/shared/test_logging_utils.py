import logging
from shared.logging_utils import SecretScrubberFilter, log_service_start, _sanitize_data

class MockRecord:
    def __init__(self, msg, args=()):
        self.msg = msg
        self.args = args
        self.levelno = logging.INFO
    def getMessage(self):
        if self.args:
            return self.msg % self.args
        return self.msg

def test_secret_scrubber_filter():
    scrubber = SecretScrubberFilter()
    
    # Test Bearer token
    record = MockRecord("Sending request with Bearer abc-123-def")
    scrubber.filter(record)
    assert "[REDACTED]" in record.msg
    assert "abc-123" not in record.msg

    # Test API Key in query
    record = MockRecord("Url: /api/v2/courses?key=secret-key-456")
    scrubber.filter(record)
    assert "[REDACTED]" in record.msg
    assert "secret-key-456" not in record.msg

def test_sanitize_data_whitelist():
    data = {
        "workspace_id": "ws_123",
        "contact_email": "hack@me.com",
        "secret_token": "shhh",
        "course_id": "c_456",
        "duration_ms": 100,
        "cf_enrollment_id": "enr_789",
        "suspended": True,
    }
    sanitized = _sanitize_data(data)
    assert "workspace_id" in sanitized
    assert "course_id" in sanitized
    assert "duration_ms" in sanitized
    assert "cf_enrollment_id" in sanitized
    assert "suspended" in sanitized
    assert "contact_email" not in sanitized
    assert "secret_token" not in sanitized

def test_log_service_start_scrubbing(caplog):
    logger = logging.getLogger("test_scrubber_logger")
    logger.addFilter(SecretScrubberFilter())
    caplog.set_level(logging.INFO)
    
    # Manually log a message that contains a secret pattern
    # log_service_start would sanitize the dict, so we test the filter directly via logger.info
    logger.info("Manual secret: Bearer secret123")
    
    # Filter should REDACT
    for record in caplog.records:
        if "Manual secret" in record.message:
            assert "[REDACTED]" in record.message
            assert "secret123" not in record.message
