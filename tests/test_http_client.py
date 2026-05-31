"""Tests for HTTPClient — Properties 1; examples for 1.3, 1.4."""

from unittest.mock import MagicMock, patch

import pytest
import requests
from hypothesis import given, settings
from hypothesis import strategies as st

from moneycontrol_scraper.exceptions import ScraperFetchError
from moneycontrol_scraper.http_client import HTTPClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEST_URL = "https://www.moneycontrol.com/news/test-article.html"


def _make_response(status_code: int, body: str = "") -> MagicMock:
    """Build a minimal mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = body.encode("utf-8")
    return resp


# ---------------------------------------------------------------------------
# Property 1: Non-200 HTTP responses raise ScraperFetchError with URL + code
# Feature: moneycontrol-stocks-scraper, Property 1: non-200 raises descriptive error
# Validates: Requirements 1.2
# ---------------------------------------------------------------------------

@given(status_code=st.integers(min_value=100, max_value=599).filter(lambda x: x != 200))
@settings(max_examples=100)
def test_non_200_raises_scraper_fetch_error(status_code: int) -> None:
    """Property 1: any non-200 status raises ScraperFetchError containing URL and code."""
    client = HTTPClient()
    mock_resp = _make_response(status_code)

    with patch("requests.get", return_value=mock_resp):
        with pytest.raises(ScraperFetchError) as exc_info:
            client.fetch(TEST_URL)

    error_message = str(exc_info.value)
    assert TEST_URL in error_message, "Error message must contain the URL"
    assert str(status_code) in error_message, "Error message must contain the status code"


# ---------------------------------------------------------------------------
# Example: Requirement 1.1 — 200 response returns body as UTF-8 string
# ---------------------------------------------------------------------------

def test_200_returns_body_as_string() -> None:
    """A 200 response returns the complete body decoded as UTF-8."""
    client = HTTPClient()
    html_body = "<html><body>Hello, world!</body></html>"
    mock_resp = _make_response(200, html_body)

    with patch("requests.get", return_value=mock_resp):
        result = client.fetch(TEST_URL)

    assert result == html_body


# ---------------------------------------------------------------------------
# Example: Requirement 1.4 — User-Agent header is set on every request
# ---------------------------------------------------------------------------

def test_user_agent_header_is_sent() -> None:
    """The User-Agent header is included in every request and contains 'Mozilla'."""
    client = HTTPClient()
    mock_resp = _make_response(200, "<html/>")

    with patch("requests.get", return_value=mock_resp) as mock_get:
        client.fetch(TEST_URL)

    _, kwargs = mock_get.call_args
    headers = kwargs.get("headers", {})
    assert "User-Agent" in headers, "User-Agent header must be present"
    assert "Mozilla" in headers["User-Agent"], "User-Agent must identify a known browser"


def test_user_agent_constant_contains_mozilla() -> None:
    """The USER_AGENT class constant contains 'Mozilla'."""
    assert "Mozilla" in HTTPClient.USER_AGENT


# ---------------------------------------------------------------------------
# Example: Requirement 1.3 — network errors raise ScraperFetchError
# ---------------------------------------------------------------------------

def test_connection_error_raises_scraper_fetch_error() -> None:
    """requests.ConnectionError is caught and re-raised as ScraperFetchError."""
    client = HTTPClient()

    with patch("requests.get", side_effect=requests.ConnectionError("refused")):
        with pytest.raises(ScraperFetchError) as exc_info:
            client.fetch(TEST_URL)

    assert TEST_URL in str(exc_info.value)


def test_timeout_raises_scraper_fetch_error() -> None:
    """requests.Timeout is caught and re-raised as ScraperFetchError."""
    client = HTTPClient()

    with patch("requests.get", side_effect=requests.Timeout("timed out")):
        with pytest.raises(ScraperFetchError) as exc_info:
            client.fetch(TEST_URL)

    assert TEST_URL in str(exc_info.value)


# ---------------------------------------------------------------------------
# Example: Requirement 1.3 — timeout is set to DEFAULT_TIMEOUT
# ---------------------------------------------------------------------------

def test_default_timeout_is_30() -> None:
    """DEFAULT_TIMEOUT class constant is 30 seconds."""
    assert HTTPClient.DEFAULT_TIMEOUT == 30


def test_timeout_passed_to_requests() -> None:
    """The timeout parameter is forwarded to requests.get."""
    client = HTTPClient()
    mock_resp = _make_response(200, "<html/>")

    with patch("requests.get", return_value=mock_resp) as mock_get:
        client.fetch(TEST_URL)

    _, kwargs = mock_get.call_args
    assert kwargs.get("timeout") == HTTPClient.DEFAULT_TIMEOUT
