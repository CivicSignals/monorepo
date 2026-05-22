"""Celery task definitions for the foia module (M5).

Beat task
---------
:func:`send_foia_reminders` — scans for FOIA requests that are overdue for a
nudge email (status ``sent``, no acknowledgement/response, past the initial
silence threshold or the repeat interval) and sends a reminder via the
injectable notifications mailer. Registered as ``foia.send_foia_reminders``
in ``celery_app.py``'s beat schedule.

Routing
-------
The ``foia.*`` prefix is NOT in the default ``task_routes`` from M1 (which
covers ingestion / extraction / signals / notifications / searches /
integrations). FOIA reminders are notification-flavoured work that should run
on the ``notify`` worker, so we explicitly route ``foia.*`` → ``notify`` in
``celery_app.py``.

Idempotency
-----------
The task uses :func:`services.is_reminder_due` (pure predicate) to filter
candidates in Python after a broad DB scan, then :func:`services.mark_reminded`
which has its own same-calendar-day guard. If the scheduler fires twice on the
same day, the second run is a no-op.
"""

from __future__ import annotations

import asyncio

import structlog

from civicsignals_api.celery_app import celery_app
from civicsignals_api.db import SessionLocal
from civicsignals_api.modules.notifications.services import (
    EmailSender,
    OutboundEmail,
    default_email_sender,
)

logger = structlog.get_logger(__name__)


async def _run_send_foia_reminders(sender: EmailSender | None = None) -> int:
    """Async core of the beat task — returns the count of reminders sent.

    ``sender`` is the :class:`~notifications.services.EmailSender` to use.
    Defaults to the SMTP sender from settings (dev = Mailpit).  Pass a
    :class:`~notifications.services.RecordingEmailSender` in tests.
    """
    from civicsignals_api.modules.foia import services

    _sender = sender if sender is not None else default_email_sender()
    sent_count = 0

    async with SessionLocal() as session:
        async with session.begin():
            overdue = await services.list_overdue_reminders(session)

        for item in overdue:
            # Re-check idempotency + mark in a short transaction per reminder
            # so one failure doesn't roll back the whole batch.
            async with session.begin():
                updated = await services.mark_reminded(session, request_id=item.request_id)

            if not updated:
                logger.info(
                    "foia_reminder_skipped_already_sent_today",
                    request_id=str(item.request_id),
                )
                continue

            # Compose and send the nudge email.
            # item.reminder_count is the count *before* mark_reminded incremented it.
            reminder_num = item.reminder_count + 1
            message = OutboundEmail(
                to=item.requester_email,
                subject=f"Reminder: FOIA request still awaiting response — {item.subject}",
                text_body=(
                    f"Hello,\n\n"
                    f"Your FOIA request '{item.subject}' was submitted and has not yet "
                    f"received an acknowledgement or response from the agency.\n\n"
                    f"This is reminder #{reminder_num}. "
                    f"If you have not already done so, you may wish to follow up directly "
                    f"with the agency.\n\n"
                    f"You can manage reminder settings for this request in CivicSignals.\n\n"
                    f"— The CivicSignals Team"
                ),
            )
            try:
                _sender.send(message)
                sent_count += 1
                logger.info(
                    "foia_reminder_sent",
                    request_id=str(item.request_id),
                    to=item.requester_email,
                )
            except Exception:
                logger.warning(
                    "foia_reminder_send_failed",
                    request_id=str(item.request_id),
                    to=item.requester_email,
                    exc_info=True,
                )

    return sent_count


@celery_app.task(name="foia.send_foia_reminders")
def send_foia_reminders() -> int:
    """Celery beat task: scan and send overdue FOIA reminder emails (M5).

    Runs on the ``notify`` worker queue (see ``celery_app.py`` task_routes).
    Returns the number of reminder emails successfully sent in this run.
    Idempotent — safe to run multiple times per day; duplicate sends within the
    same calendar day are suppressed by :func:`services.mark_reminded`.
    """
    return asyncio.run(_run_send_foia_reminders())
