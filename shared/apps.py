from django.apps import AppConfig


class SharedConfig(AppConfig):
    name = "shared"

    def ready(self):
        from . import auth  # noqa: F401 — registers the user_login_failed receiver
