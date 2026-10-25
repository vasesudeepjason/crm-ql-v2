"""
Background delivery. With RQ_ENABLED (Redis configured) jobs go to the "default"
queue and a worker (`manage.py rqworker default`) sends them; otherwise they run
inline so development and tests need nothing extra. Jobs carry the tenant id and
re-enter the tenant context themselves (section 7: reminders and emails must not
block web requests).
"""
from __future__ import annotations

import logging

from django.conf import settings

log = logging.getLogger(__name__)


def enqueue(func, *args, **kwargs):
    if getattr(settings, "RQ_ENABLED", False):
        try:
            import django_rq

            django_rq.get_queue("default").enqueue(func, *args, **kwargs)
            return
        except Exception:  # Redis down: don't lose the notification, send inline
            log.exception("RQ enqueue failed; running inline")
    func(*args, **kwargs)
