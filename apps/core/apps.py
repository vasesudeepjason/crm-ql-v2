from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "apps.core"
    label = "core"
    verbose_name = "Core"

    def ready(self):
        from . import checks  # noqa: F401  (registers the RLS-role and secrets system checks)
