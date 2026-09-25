import ipaddress

from django.conf import settings


def client_ip(request) -> str:
    """
    Real client IP for django_ratelimit (settings.RATELIMIT_IP_META_KEY).

    In production Apache proxies to Gunicorn, so REMOTE_ADDR is Apache's own
    address and every "per IP" limit would silently become one global bucket.
    With TRUSTED_PROXY_COUNT=N, the Nth entry from the right of
    X-Forwarded-For is used — the one appended by our own outermost proxy.
    Entries further left are client-supplied and never trusted. Falls back to
    REMOTE_ADDR when the header is missing, too short, or not a valid IP.
    """
    trusted = getattr(settings, "TRUSTED_PROXY_COUNT", 0)
    if trusted > 0:
        forwarded = [part.strip() for part in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if part.strip()]
        if len(forwarded) >= trusted:
            candidate = forwarded[-trusted]
            try:
                ipaddress.ip_address(candidate)
                return candidate
            except ValueError:
                pass
    return request.META.get("REMOTE_ADDR", "")
