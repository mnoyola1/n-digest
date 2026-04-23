"""Send the digest via Resend."""

from __future__ import annotations

import logging
import os

import resend

log = logging.getLogger(__name__)

DEFAULT_FROM = "n-digest <noreply@noyolalearn.com>"
DEFAULT_TO = "mnoyola1@gmail.com"


def _split_addresses(value: str | None) -> list[str]:
    """Parse a comma- or semicolon-separated list of email addresses."""
    if not value:
        return []
    out: list[str] = []
    for chunk in value.replace(";", ",").split(","):
        addr = chunk.strip()
        if addr:
            out.append(addr)
    return out


def send_email(
    subject: str,
    html: str,
    to_email: str | None = None,
    cc_emails: list[str] | None = None,
) -> str:
    """Send via Resend. Returns the Resend message id.

    Recipients can be passed explicitly, or read from env:
    - DIGEST_TO_EMAIL: primary (string). Falls back to DEFAULT_TO.
    - DIGEST_CC_EMAIL: comma- or semicolon-separated CC list (optional).
    """
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

    cc_list = cc_emails if cc_emails is not None else _split_addresses(os.environ.get("DIGEST_CC_EMAIL"))

    params: dict = {
        "from": from_value,
        "to": [to_value],
        "subject": subject,
        "html": html,
        "headers": {
            "List-Unsubscribe": "<mailto:mnoyola1@gmail.com?subject=unsubscribe>",
            "X-Entity-Ref-ID": "n-digest",
        },
    }
    if cc_list:
        params["cc"] = cc_list

    result = resend.Emails.send(params)
    message_id = result.get("id") if isinstance(result, dict) else getattr(result, "id", "")
    cc_log = f" cc={','.join(cc_list)}" if cc_list else ""
    log.info("Resend send ok: id=%s to=%s%s", message_id, to_value, cc_log)
    return message_id or ""
