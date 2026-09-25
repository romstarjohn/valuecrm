from django.shortcuts import render
from django.views.defaults import permission_denied as default_permission_denied
from django_ratelimit.exceptions import Ratelimited


def permission_denied(request, exception=None):
    """handler403: django_ratelimit's block=True raises Ratelimited (a PermissionDenied) — answer it as 429, not "access denied"."""
    if isinstance(exception, Ratelimited):
        return render(request, "429.html", status=429)
    return default_permission_denied(request, exception)
