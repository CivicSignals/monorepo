"""Saved-search digest email rendering (H4).

Pure, DB-free rendering of the :func:`notifications.services.build_digest_payload`
output into a branded, mobile-friendly :class:`~notifications.services.OutboundEmail`
(HTML + plaintext alternative). Kept separate from ``services.py`` so the templates
are unit-testable with a hand-built payload and no SMTP/DB — mirroring how the
B3/B6 transactional mails compose their bodies, just lifted into named functions
because a digest body is non-trivial.

Design notes
------------
* **Deep links.** Each new signal links to the web app signal detail route
  (``{web_base_url}/signals/{id}`` — the G2 detail page; the feed already links
  there). The saved-search header links to the feed filtered to that search. The
  base URL is the operator-configured ``web_base_url`` (dev = ``localhost:3000``).
* **Mobile-friendly + client-safe.** A single-column, max-width table layout with
  **inline** CSS (Gmail/Outlook strip ``<style>`` and external sheets), system
  font stack, and large tap targets. No external images.
* **Escaping.** Signal titles, entity names and the saved-search name are
  user/ingest-controlled, so every interpolated value is HTML-escaped
  (:func:`html.escape`) — the existing B3/B6 mails interpolate trusted server copy
  and don't, but a digest carries third-party content.
* **Empty digest.** When there are no new signals we still send a short "no new
  matches this period" note rather than skipping: a predictable cadence is the
  point of a digest, and silence is indistinguishable from a broken pipeline. The
  caller (``send_digest``) therefore always sends.
* **Unsubscribe (H5).** Every digest carries a signed one-click unsubscribe token
  (:mod:`notifications.unsubscribe`). The footer shows two links: a one-click
  *Unsubscribe* (the web confirm landing, ``unsubscribe_url``) and *Manage your
  digest preferences* (the authed ``/settings/notifications`` page). The same token
  also drives the RFC 8058 ``List-Unsubscribe`` / ``List-Unsubscribe-Post`` headers
  so a mail client renders a native unsubscribe button (``unsubscribe_post_url``,
  a POST endpoint on the API). The renderer is still pure — the caller
  (``send_digest``) mints the token and passes the URLs in.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

if TYPE_CHECKING:
    from .services import OutboundEmail

# Branding — kept module-local so the palette/product name live in one place.
_BRAND_NAME = "CivicSignals"
_BRAND_COLOR = "#4f46e5"  # indigo-600, matches the web feed accent
_TEXT_COLOR = "#111827"
_MUTED_COLOR = "#6b7280"
_BORDER_COLOR = "#e5e7eb"
_BG_COLOR = "#f3f4f6"
_FONT_STACK = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def signal_type_label(signal_type: str | None) -> str:
    """A human-readable label for a raw ``signal_type`` (``rfp_posted`` -> ``RFP Posted``).

    No server-side label map exists yet (the web app has its own); derive a
    title-cased label from the enum value, upper-casing the well-known initialisms
    so they don't render as ``Rfp``/``Rfi``/``Rfq``/``Icp``.
    """
    if not signal_type:
        return "Signal"
    words = [w for w in signal_type.replace("-", "_").split("_") if w]
    initialisms = {"rfp", "rfi", "rfq", "icp"}
    return " ".join(w.upper() if w in initialisms else w.capitalize() for w in words)


def _signal_url(web_base_url: str, signal_id: str | None) -> str:
    """Deep link to a single signal's detail page (G2 route)."""
    base = web_base_url.rstrip("/")
    if not signal_id:
        return f"{base}/feed"
    return f"{base}/signals/{quote(str(signal_id), safe='')}"


def _search_url(web_base_url: str, saved_search_id: str | None) -> str:
    """Deep link to the feed filtered to the saved search."""
    base = web_base_url.rstrip("/")
    if not saved_search_id:
        return f"{base}/feed"
    return f"{base}/feed?saved_search={quote(str(saved_search_id), safe='')}"


def _preferences_url(web_base_url: str) -> str:
    """Authed digest-preferences page link (the H4 footer target; H5 prefs UI)."""
    return f"{web_base_url.rstrip('/')}/settings/notifications"


def unsubscribe_landing_url(web_base_url: str, token: str) -> str:
    """The web one-click unsubscribe confirm page link (footer + List-Unsubscribe).

    Points at the web app's ``/settings/notifications/unsubscribe`` page, which
    shows a confirmation and POSTs the token to the API (so a link prefetch / scan
    cannot silently unsubscribe — the destructive action is a deliberate POST).
    """
    return f"{web_base_url.rstrip('/')}/settings/notifications/unsubscribe?token={quote(token, safe='')}"


def unsubscribe_post_url(api_base_url: str, api_v1_prefix: str, token: str) -> str:
    """The API one-click unsubscribe POST endpoint (RFC 8058 List-Unsubscribe-Post).

    Mail clients POST here directly with no session; the endpoint flips the
    subscription identified by the signed token to ``off``.
    """
    base = api_base_url.rstrip("/")
    prefix = api_v1_prefix.rstrip("/")
    return f"{base}{prefix}/notifications/digests/unsubscribe?token={quote(token, safe='')}"


def _format_date(value: Any) -> str | None:
    """Best-effort ``YYYY-MM-DD`` from an ISO timestamp/date string, or ``None``."""
    if not value:
        return None
    text = value if isinstance(value, str) else str(value)
    # An ISO datetime/date both start ``YYYY-MM-DD``; take that prefix.
    return text[:10] if len(text) >= 10 else text


def _digest_subject(saved_search_name: str | None, count: int) -> str:
    name = saved_search_name or "your saved search"
    if count == 0:
        return f"{_BRAND_NAME}: no new signals for '{name}'"
    plural = "s" if count != 1 else ""
    return f"{_BRAND_NAME}: {count} new signal{plural} for '{name}'"


def render_digest_text(
    payload: dict[str, Any],
    *,
    web_base_url: str,
    unsubscribe_url: str | None = None,
) -> str:
    """Plaintext fallback body for a digest (deep links inline)."""
    name = payload.get("saved_search_name") or "your saved search"
    signals: list[dict[str, Any]] = list(payload.get("signals") or [])
    search_url = _search_url(web_base_url, payload.get("saved_search_id"))

    lines: list[str] = [f"{_BRAND_NAME} digest: {name}", ""]
    if not signals:
        lines += [
            "No new signals matched this saved search during this period.",
            "",
            f"View this search: {search_url}",
        ]
    else:
        plural = "s" if len(signals) != 1 else ""
        lines.append(f"{len(signals)} new signal{plural} matched:")
        lines.append("")
        for sig in signals:
            title = sig.get("title") or "(untitled signal)"
            type_label = signal_type_label(sig.get("signal_type"))
            url = _signal_url(web_base_url, sig.get("signal_id"))
            lines.append(f"- {title}")
            meta_bits = [type_label]
            entity = sig.get("entity")
            if entity:
                meta_bits.append(str(entity))
            score = sig.get("score")
            if score is not None:
                meta_bits.append(f"score {round(float(score))}")
            occurred = _format_date(sig.get("occurred_at"))
            if occurred:
                meta_bits.append(occurred)
            lines.append(f"  {' | '.join(meta_bits)}")
            lines.append(f"  {url}")
            lines.append("")
        lines.append(f"See all matches: {search_url}")

    lines += [
        "",
        "--",
        f"You're receiving this because you subscribed to digests for '{name}'.",
    ]
    if unsubscribe_url:
        lines.append(f"Unsubscribe from this digest: {unsubscribe_url}")
    lines.append(f"Manage your digest preferences: {_preferences_url(web_base_url)}")
    return "\n".join(lines)


def _render_signal_row(sig: dict[str, Any], *, web_base_url: str) -> str:
    title = html.escape(str(sig.get("title") or "(untitled signal)"))
    url = html.escape(_signal_url(web_base_url, sig.get("signal_id")), quote=True)
    type_label = html.escape(signal_type_label(sig.get("signal_type")))

    meta_bits: list[str] = [type_label]
    entity = sig.get("entity")
    if entity:
        meta_bits.append(html.escape(str(entity)))
    score = sig.get("score")
    if score is not None:
        meta_bits.append(f"score {round(float(score))}")
    occurred = _format_date(sig.get("occurred_at"))
    if occurred:
        meta_bits.append(html.escape(occurred))
    meta = " &middot; ".join(meta_bits)

    return (
        '<tr><td style="padding:0 0 16px 0;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border:1px solid {_BORDER_COLOR};border-radius:8px;">'
        '<tr><td style="padding:16px 18px;">'
        f'<a href="{url}" style="color:{_BRAND_COLOR};text-decoration:none;'
        f'font-size:16px;font-weight:600;line-height:1.35;">{title}</a>'
        f'<div style="color:{_MUTED_COLOR};font-size:13px;margin-top:6px;">{meta}</div>'
        "</td></tr></table>"
        "</td></tr>"
    )


def render_digest_html(
    payload: dict[str, Any],
    *,
    web_base_url: str,
    unsubscribe_url: str | None = None,
) -> str:
    """Branded, mobile-friendly HTML body for a digest (inline CSS, table layout)."""
    name = html.escape(str(payload.get("saved_search_name") or "your saved search"))
    signals: list[dict[str, Any]] = list(payload.get("signals") or [])
    search_url = html.escape(_search_url(web_base_url, payload.get("saved_search_id")), quote=True)
    prefs_url = html.escape(_preferences_url(web_base_url), quote=True)
    unsub_url = html.escape(unsubscribe_url, quote=True) if unsubscribe_url else None

    if signals:
        plural = "s" if len(signals) != 1 else ""
        intro = (
            f'<p style="color:{_TEXT_COLOR};font-size:15px;margin:0 0 18px 0;">'
            f"<strong>{len(signals)}</strong> new signal{plural} matched "
            f"<strong>{name}</strong>:</p>"
        )
        rows = "".join(_render_signal_row(sig, web_base_url=web_base_url) for sig in signals)
        body = (
            f"{intro}"
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>'
            f'<p style="margin:8px 0 0 0;"><a href="{search_url}" '
            f'style="color:{_BRAND_COLOR};font-size:14px;text-decoration:none;">'
            "See all matches &rarr;</a></p>"
        )
    else:
        body = (
            f'<p style="color:{_TEXT_COLOR};font-size:15px;margin:0 0 18px 0;">'
            f"No new signals matched <strong>{name}</strong> during this period.</p>"
            f'<p style="margin:0;"><a href="{search_url}" '
            f'style="color:{_BRAND_COLOR};font-size:14px;text-decoration:none;">'
            "View this search &rarr;</a></p>"
        )

    return (
        "<!DOCTYPE html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_BRAND_NAME} digest</title></head>"
        f'<body style="margin:0;padding:0;background:{_BG_COLOR};">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{_BG_COLOR};padding:24px 0;"><tr><td align="center">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="max-width:600px;width:100%;background:#ffffff;border-radius:12px;'
        f'overflow:hidden;font-family:{_FONT_STACK};">'
        # Header / brand bar
        f'<tr><td style="background:{_BRAND_COLOR};padding:20px 24px;">'
        f'<span style="color:#ffffff;font-size:18px;font-weight:700;">{_BRAND_NAME}</span>'
        '<span style="color:rgba(255,255,255,0.8);font-size:14px;"> &middot; saved-search digest</span>'
        "</td></tr>"
        # Body
        f'<tr><td style="padding:24px;">{body}</td></tr>'
        # Footer / unsubscribe (H5)
        f'<tr><td style="padding:18px 24px;border-top:1px solid {_BORDER_COLOR};'
        f'color:{_MUTED_COLOR};font-size:12px;line-height:1.5;">'
        f"You're receiving this because you subscribed to digests for "
        f"<strong>{name}</strong>.<br>"
        + (
            f'<a href="{unsub_url}" style="color:{_MUTED_COLOR};text-decoration:underline;">'
            "Unsubscribe</a> &middot; "
            if unsub_url
            else ""
        )
        + f'<a href="{prefs_url}" style="color:{_MUTED_COLOR};text-decoration:underline;">'
        "Manage your digest preferences</a>"
        "</td></tr>"
        "</table></td></tr></table></body></html>"
    )


def render_digest_email(
    payload: dict[str, Any],
    *,
    web_base_url: str,
    to: str,
    unsubscribe_url: str | None = None,
    unsubscribe_post_url: str | None = None,
) -> OutboundEmail:
    """Render a digest payload into a ready-to-send :class:`OutboundEmail` (H4/H5).

    Pure: takes the ``build_digest_payload`` dict + the recipient address + the web
    base URL, returns subject/text/html. Always returns a sendable email (the empty
    case is a "no new signals" note, not a skip — see module docstring).

    H5: when ``unsubscribe_url`` is given it is rendered in the footer; when
    ``unsubscribe_post_url`` is given the email also gets the RFC 8058
    ``List-Unsubscribe`` + ``List-Unsubscribe-Post`` headers so mail clients show a
    native one-click unsubscribe button. The ``List-Unsubscribe`` value lists the
    one-click POST URL (and the human ``unsubscribe_url`` as a mailto-less https
    fallback) per RFC 2369 angle-bracket syntax.
    """
    # Local import keeps this rendering module free of any service/DB import cycle.
    from .services import OutboundEmail

    signals = list(payload.get("signals") or [])

    headers: dict[str, str] = {}
    if unsubscribe_post_url:
        # RFC 8058 one-click: List-Unsubscribe lists the POST URL; the human-facing
        # confirm URL (if any) is included as an additional <https://…> entry so a
        # client that does not support one-click still has a link to open.
        targets = [f"<{unsubscribe_post_url}>"]
        if unsubscribe_url:
            targets.append(f"<{unsubscribe_url}>")
        headers["List-Unsubscribe"] = ", ".join(targets)
        headers["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    return OutboundEmail(
        to=to,
        subject=_digest_subject(payload.get("saved_search_name"), len(signals)),
        text_body=render_digest_text(
            payload, web_base_url=web_base_url, unsubscribe_url=unsubscribe_url
        ),
        html_body=render_digest_html(
            payload, web_base_url=web_base_url, unsubscribe_url=unsubscribe_url
        ),
        headers=headers,
    )
