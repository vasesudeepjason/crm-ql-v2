"""Field sessions post attendance automatically (foundation document, HRMS)."""
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.field.models import FieldSession

from .models import Attendance, Employee


@receiver(post_save, sender=FieldSession)
def _session_attendance(sender, instance: FieldSession, created, **kwargs):
    from . import services

    emp = Employee.objects.filter(user_id=instance.executive_id).first()
    if emp is None:
        return
    if created:
        services.check_in(emp, instance.started_at, source=Attendance.Source.FIELD)
    elif instance.ended_at:
        services.check_out(emp, instance.ended_at)
