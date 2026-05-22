"""Tests for the transactional email seam (B1 mailer)."""

from __future__ import annotations

from civicsignals_api.modules.notifications import services as notifications_services


def test_recording_sender_captures_messages() -> None:
    rec = notifications_services.RecordingEmailSender()
    msg = notifications_services.OutboundEmail(to="a@example.com", subject="Hi", text_body="hello")
    notifications_services.send_email(msg, sender=rec)
    assert rec.sent == [msg]


def test_send_email_swallows_transport_errors() -> None:
    class _Boom:
        def send(self, message: notifications_services.OutboundEmail) -> None:
            raise RuntimeError("smtp down")

    # Best-effort: a failing transport must not raise (signup must not break).
    notifications_services.send_email(
        notifications_services.OutboundEmail(to="x@example.com", subject="s", text_body="b"),
        sender=_Boom(),
    )
