"""Server-side verification for Cloudflare Turnstile — gates the three
public action endpoints that create a real record (Inquiry, Application,
Offer response) against bot submissions. Not applied to the draft
save/autosave endpoints: those don't create anything a bot benefits from,
and requiring a fresh solve on every autosave would be a real UX cost for a
real parent filling in the multi-step form over several minutes.

See docs/admissions/02-stack-and-schema.md for the widget-placement/token-
freshness notes on the frontend side.
"""

import json
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


class TurnstileVerificationError(Exception):
    """Raised with a message safe to show directly to the parent."""


def verify_turnstile_token(token, remote_ip=None):
    if not token:
        raise TurnstileVerificationError("Please complete the verification challenge and try again.")

    payload = {"secret": settings.TURNSTILE_SECRET_KEY, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    req = Request(VERIFY_URL, data=urlencode(payload).encode(), method="POST")
    try:
        with urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
    except URLError:
        raise TurnstileVerificationError("Could not verify the challenge right now. Please try again.")

    if not result.get("success"):
        raise TurnstileVerificationError("Verification failed. Please reload the page and try again.")
