"""
Telecaller (foundation document, section 4). The call log itself is the CRM Activity
of type "call", so every call already sits on the lead's timeline. This app adds the
daily targets and the call-desk workflow around it.
"""
from django.conf import settings
from django.db import models

from apps.core.models import TenantModel


class CallTarget(TenantModel):
    """Daily call and lead targets per telecaller, shown on their dashboard."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="call_targets")
    calls_per_day = models.PositiveSmallIntegerField(default=40)
    leads_per_day = models.PositiveSmallIntegerField(default=4)
    set_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        unique_together = [("tenant", "user")]

    def __str__(self):
        return f"{self.user}: {self.calls_per_day} calls / {self.leads_per_day} leads a day"
