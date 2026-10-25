from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.approvals.models import Approval

from . import services


@receiver(post_save, sender=Approval)
def _on_approval_created(sender, instance: Approval, created, **kwargs):
    if created and instance.status in (Approval.Status.PENDING,):
        services.approval_submitted(instance)
