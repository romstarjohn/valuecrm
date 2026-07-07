import logging
import re

# Sensitive patterns to scrub from logs
SENSITIVE_PATTERNS = [
    re.compile(r"Bearer\s+[a-zA-Z0-9\-\._~+/]+=*", re.IGNORECASE),
    re.compile(r"Token\s+[a-zA-Z0-9]+", re.IGNORECASE),
    re.compile(r"key=[a-zA-Z0-9\-\._~+/]+", re.IGNORECASE),
]

class SecretScrubberFilter(logging.Filter):
    """
    Logging filter that scrubs sensitive information from log messages.
    """
    def filter(self, record):
        message = record.getMessage()
        for pattern in SENSITIVE_PATTERNS:
            message = pattern.sub("[REDACTED]", message)
        record.msg = message
        record.args = ()  # Clear args since message is already formatted
        return True

def get_logger(name):
    return logging.getLogger(name)

def _sanitize_data(data):
    """
    Only allow safe IDs and operational metrics to be logged.
    Whitelisted: workspace_id, contact_id, course_id, enrollment_attempt_id, endpoint, status_code, duration_ms
    """
    safe_keys = {
        "workspace_id", "contact_id", "course_id", "enrollment_attempt_id",
        "endpoint", "status_code", "duration_ms", "method"
    }
    return {k: v for k, v in data.items() if k in safe_keys}

def log_service_start(logger, service_name, method_name, **data):
    sanitized = _sanitize_data(data)
    logger.info(f"START: {service_name}.{method_name} | Identifiers: {sanitized}")

def log_service_success(logger, service_name, method_name, **data):
    sanitized = _sanitize_data(data)
    logger.info(f"SUCCESS: {service_name}.{method_name} | Identifiers: {sanitized}")

def log_service_failure(logger, service_name, method_name, error, **data):
    sanitized = _sanitize_data(data)
    logger.exception(f"FAILURE: {service_name}.{method_name} | Error: {str(error)} | Identifiers: {sanitized}")
