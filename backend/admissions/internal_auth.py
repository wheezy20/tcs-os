"""Shared-secret auth for internal endpoints that Cloud Tasks calls back into
(the bulk-email batch sender and the transactional-email sender). Not a DRF
permission class — "the caller is Cloud Tasks" isn't a concept DRF's
permission model has. One secret (`BULK_EMAIL_INTERNAL_SECRET`) covers both:
same trust boundary (our own queue → our own endpoint), one fewer thing to
rotate.
"""

import hmac

from django.conf import settings


def internal_secret_ok(request):
    """True only if the request carries the correct X-Internal-Secret header.
    Compared with hmac.compare_digest, not `==`, so a wrong secret can't be
    distinguished from a correct one by response timing. Refuses everything
    when the secret isn't configured at all — an empty expected value must
    never make compare_digest("", "") a pass."""
    expected = settings.BULK_EMAIL_INTERNAL_SECRET
    provided = request.headers.get("X-Internal-Secret", "")
    return bool(expected) and hmac.compare_digest(provided, expected)
