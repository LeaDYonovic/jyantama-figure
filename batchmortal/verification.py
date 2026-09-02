"""Turnstile state handling for the Mortal review site.

This module deliberately does not solve, bypass, or forge verification.  It only
observes the token produced by the site's own widget, classifies errors, and
provides clear guidance when a person must complete the challenge in Chrome.
"""

from __future__ import annotations


TURNSTILE_REJECTION_MARKERS = (
    "invalid captcha response",
    "timeout-or-duplicate",
)
RATE_LIMIT_MARKERS = (
    "too many requests",
    "rate limit",
)


class ReviewVerificationError(RuntimeError):
    """The review site's verification was rejected or could not be completed."""


class ReviewRateLimitError(RuntimeError):
    """The review site asked the client to slow down."""


def turnstile_token_ready(state: dict | None) -> bool:
    state = state if isinstance(state, dict) else {}
    try:
        return int(state.get("token_length") or 0) > 0
    except (TypeError, ValueError):
        return False


def raise_for_review_access_error(page_text: str, log_prefix: str) -> None:
    normalized = str(page_text or "").lower()
    if any(marker in normalized for marker in TURNSTILE_REJECTION_MARKERS):
        raise ReviewVerificationError(
            f"{log_prefix} Turnstile verification was rejected or expired"
        )
    if any(marker in normalized for marker in RATE_LIMIT_MARKERS):
        raise ReviewRateLimitError(
            f"{log_prefix} Review site rate limited this request"
        )


def turnstile_timeout_message(
    log_prefix: str,
    *,
    interactive: bool,
    timeout: float,
) -> str:
    waited = max(0, round(float(timeout)))
    if interactive:
        guidance = "Complete the verification in the open Chrome window, then retry."
    else:
        guidance = "Disable headless mode so the verification can be completed in Chrome."
    return f"{log_prefix} Timed out after {waited}s waiting for Turnstile. {guidance}"
