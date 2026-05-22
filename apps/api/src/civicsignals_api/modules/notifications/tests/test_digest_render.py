"""Unit tests for the H4 digest email renderer (pure — no DB, no SMTP).

These build a hand-shaped ``build_digest_payload`` dict and assert the rendered
HTML + plaintext bodies contain the saved-search name, each signal's title/type/
entity/score/date, the per-signal deep links, the empty-digest note, the
unsubscribe footer (H5 seam), and that user-controlled values are HTML-escaped.
"""

from __future__ import annotations

from typing import Any

from civicsignals_api.modules.notifications import templates

_WEB = "http://localhost:3000"


def _payload(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "saved_search_id": "11111111-1111-1111-1111-111111111111",
        "saved_search_name": "Hot RFPs",
        "workspace_id": "22222222-2222-2222-2222-222222222222",
        "user_id": "33333333-3333-3333-3333-333333333333",
        "recipient_email": "buyer@example.com",
        "signals": [
            {
                "signal_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "title": "City of Springfield RFP for road resurfacing",
                "signal_type": "rfp_posted",
                "entity": "City of Springfield",
                "occurred_at": "2026-05-20T14:00:00+00:00",
                "score": 87.4,
                "status": "new",
            }
        ],
    }
    base.update(over)
    return base


def test_render_email_subject_and_recipient() -> None:
    msg = templates.render_digest_email(_payload(), web_base_url=_WEB, to="buyer@example.com")
    assert msg.to == "buyer@example.com"
    assert "Hot RFPs" in msg.subject
    assert "1 new signal" in msg.subject
    assert msg.html_body is not None


def test_html_and_text_contain_signal_fields_and_deep_link() -> None:
    msg = templates.render_digest_email(_payload(), web_base_url=_WEB, to="buyer@example.com")
    assert msg.html_body is not None
    deep_link = "http://localhost:3000/signals/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

    for body in (msg.html_body, msg.text_body):
        assert "Hot RFPs" in body
        assert "City of Springfield RFP for road resurfacing" in body
        assert "RFP Posted" in body  # humanized signal type, initialism upper-cased
        assert "City of Springfield" in body  # entity
        assert "score 87" in body  # rounded score
        assert "2026-05-20" in body  # date only
        assert deep_link in body


def test_search_deep_link_present() -> None:
    msg = templates.render_digest_email(_payload(), web_base_url=_WEB, to="buyer@example.com")
    assert msg.html_body is not None
    search_link = "http://localhost:3000/feed?saved_search=11111111-1111-1111-1111-111111111111"
    assert search_link in msg.html_body
    assert search_link in msg.text_body


def test_unsubscribe_footer_present_h5_seam() -> None:
    msg = templates.render_digest_email(_payload(), web_base_url=_WEB, to="buyer@example.com")
    assert msg.html_body is not None
    prefs = "http://localhost:3000/settings/notifications"
    assert prefs in msg.html_body
    assert prefs in msg.text_body
    assert "preferences" in msg.html_body.lower()


def test_empty_digest_sends_no_new_signals_note() -> None:
    msg = templates.render_digest_email(
        _payload(signals=[]), web_base_url=_WEB, to="buyer@example.com"
    )
    assert msg.html_body is not None
    assert "no new signals" in msg.subject.lower()
    assert "No new signals" in msg.html_body
    assert "No new signals" in msg.text_body
    # Still links back to the search so the recipient can check it themselves.
    assert "/feed?saved_search=" in msg.html_body


def test_missing_optional_fields_do_not_crash() -> None:
    sparse = _payload(
        saved_search_name=None,
        signals=[
            {
                "signal_id": None,
                "title": None,
                "signal_type": None,
                "entity": None,
                "occurred_at": None,
                "score": None,
                "status": None,
            }
        ],
    )
    msg = templates.render_digest_email(sparse, web_base_url=_WEB, to="buyer@example.com")
    assert msg.html_body is not None
    # A title-less signal renders a placeholder rather than blowing up.
    assert "untitled signal" in msg.html_body
    assert "untitled signal" in msg.text_body
    # No signal_id -> the deep link degrades to the feed.
    assert "/feed" in msg.html_body


def test_user_controlled_values_are_html_escaped() -> None:
    evil = _payload(
        saved_search_name="<script>alert(1)</script>",
        signals=[
            {
                "signal_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "title": "Bad <img src=x onerror=alert(1)> title",
                "signal_type": "rfp_posted",
                "entity": "Town & <b>Country</b>",
                "occurred_at": "2026-05-20",
                "score": 50,
                "status": "new",
            }
        ],
    )
    msg = templates.render_digest_email(evil, web_base_url=_WEB, to="buyer@example.com")
    assert msg.html_body is not None
    # Raw markup must not survive into the HTML body.
    assert "<script>" not in msg.html_body
    assert "<img src=x" not in msg.html_body
    assert "&lt;script&gt;" in msg.html_body
    assert "&amp;" in msg.html_body  # the ampersand in the entity name is escaped


def test_signal_type_label_humanizes_initialisms() -> None:
    assert templates.signal_type_label("rfp_posted") == "RFP Posted"
    assert templates.signal_type_label("rfi_rfq") == "RFI RFQ"
    assert templates.signal_type_label("contract_awarded") == "Contract Awarded"
    assert templates.signal_type_label(None) == "Signal"
