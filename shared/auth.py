from django.contrib.auth.signals import user_login_failed
from django.contrib.auth.views import LoginView
from django.dispatch import receiver
from django_ratelimit.core import is_ratelimited

from shared.logging_utils import get_logger
from shared.ratelimit import client_ip

logger = get_logger("security.auth")

LOGIN_RATE_PER_IP = "10/m"
LOGIN_RATE_PER_USERNAME = "5/5m"


def _submitted_username(request) -> str:
    return (request.POST.get("username") or "").strip().lower()[:150]


def _username_key(group, request):
    return _submitted_username(request)


class RateLimitedLoginView(LoginView):
    """
    Staff login with brute-force protection: POSTs are limited per client IP
    and per submitted username. A limited request is answered with the same
    login page (HTTP 429, explicit message), never a raw 403 — and the
    credentials are not even checked, so a correct password guessed while
    limited reveals nothing.
    """

    def post(self, request, *args, **kwargs):
        ip_limited = is_ratelimited(request, group="login-ip", key="ip", rate=LOGIN_RATE_PER_IP, increment=True)
        user_limited = is_ratelimited(
            request, group="login-username", key=_username_key, rate=LOGIN_RATE_PER_USERNAME, increment=True,
        )
        if ip_limited or user_limited:
            logger.warning(
                "Login rate limit hit | ip=%s username=%s scope=%s",
                client_ip(request), _submitted_username(request), "ip" if ip_limited else "username",
            )
            form = self.get_form_class()(request)
            return self.render_to_response(self.get_context_data(form=form, ratelimited=True), status=429)
        return super().post(request, *args, **kwargs)


@receiver(user_login_failed)
def log_failed_login(sender, credentials, request=None, **kwargs):
    # Django already masks the password in `credentials`; only the username is logged anyway.
    username = str(credentials.get("username", ""))[:150]
    ip = client_ip(request) if request is not None else ""
    logger.warning("Failed login | ip=%s username=%s", ip, username)
