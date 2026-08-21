"""
Outbound email (FR-10, password reset).

One place, because there are exactly two things this system sends and both must
behave the same way when the mail server is unreachable: **never fail the caller's
request**.

A password reset that 500s because SMTP timed out tells an anonymous caller that
the address exists. An approved quote that fails to export because a mail server
blipped loses the export, when the PDF is already durable in S3 and can be sent
again. In both cases the delivery is best-effort and the failure is logged loudly
rather than raised.

That is a deliberate trade and not a shrug: the alternative is a queue with its own
retry and DLQ for two messages a day, which is machinery out of proportion to the
problem. If delivery ever needs a guarantee, `quote_export` already runs on the
worker behind SQS and is the natural place to put one.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMessage

log = logging.getLogger("cbc.mail")


def send(
    *,
    subject: str,
    body: str,
    to: str,
    attachment: tuple[str, bytes, str] | None = None,
) -> bool:
    """
    Send one message. Returns whether it left the process.

    Never raises. Callers decide what to do with False; none of them should turn
    it into a failed request.
    """
    if not to:
        log.warning("refusing to send %r with no recipient", subject)
        return False

    try:
        message = EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[to],
        )
        if attachment is not None:
            message.attach(*attachment)
        message.send(fail_silently=False)
    except Exception:  # noqa: BLE001 - transport failure must not reach the caller
        log.exception(
            "email delivery failed",
            extra={"subject": subject, "recipient": to, "backend": settings.EMAIL_BACKEND},
        )
        return False

    log.info("email sent", extra={"subject": subject, "recipient": to})
    return True
