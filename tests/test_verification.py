import pytest

from batchmortal.verification import (
    ReviewRateLimitError,
    ReviewVerificationError,
    raise_for_review_access_error,
    turnstile_timeout_message,
    turnstile_token_ready,
)


def test_turnstile_token_ready_only_accepts_nonempty_token():
    assert turnstile_token_ready({"token_length": 120}) is True
    assert turnstile_token_ready({"token_length": 0}) is False
    assert turnstile_token_ready(None) is False


def test_rejected_turnstile_is_classified_separately():
    with pytest.raises(ReviewVerificationError):
        raise_for_review_access_error("Invalid CAPTCHA response", "[test]")


def test_rate_limit_is_classified_separately():
    with pytest.raises(ReviewRateLimitError):
        raise_for_review_access_error("Too Many Requests", "[test]")


def test_timeout_message_explains_manual_visible_browser_path():
    message = turnstile_timeout_message("[test]", interactive=True, timeout=180)
    assert "open Chrome window" in message
    assert "180s" in message


def test_timeout_message_explains_headless_limitation():
    message = turnstile_timeout_message("[test]", interactive=False, timeout=45)
    assert "Disable headless mode" in message
