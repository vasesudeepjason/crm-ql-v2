from django.apps import AppConfig


class HrConfig(AppConfig):
    name = "apps.hr"
    label = "hr"

    def ready(self):
        from . import signals  # noqa: F401
