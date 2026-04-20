"""Send the digest via Resend."""

from __future__ import annotations

import logging
import os

import resend

log = logging.getLogger(__name__)

DEFAULT_FROM = "n-digest <noreply@noyolalearn.com>"
DEFAULT_TO = "mnoyola1@gmail.com"


def send_email(subject: str, html: str, to_email: str | None = None) -> str:
    """Send via Resend. Returns the Resend message id."""
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY not set")
    resend.api_key = api_key

    from_email = os.environ.get("RESEND_FROM_EMAIL")
    if from_email and "<" not in from_email:
        from_value = f"n-digest <{from_email}>"
    else:
        from_value = from_email or DEFAULT_FROM

    to_value = to_email or os.environ.get("DIGEST_TO_EMAIL") or DEFAULT_TO

    params: resend.Emails.SendParams = {
        "from": from_value,
        "to": [to_value],
        "subject": subject,
        "html": html,
        "headers": {
            "List-Unsubscribe": "<mailto:mnoyola1@gmail.com?subject=unsubscribe>",
            "X-Entity-Ref-ID": "n-digest",
        },
    }
    result = resend.Emails.send(params)
    message_id = result.get("id") if isinstance(result, dict) else getattr(result, "id", "")
    log.info("Resend send ok: id=%s to=%s", message_id, to_value)
    return message_id or ""
