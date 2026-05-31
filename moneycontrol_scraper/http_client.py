"""HTTP client for fetching MoneyControl article pages."""

import requests

from moneycontrol_scraper.exceptions import ScraperFetchError


class HTTPClient:
    """Fetches raw HTML from URLs with a browser-like User-Agent header."""

    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    DEFAULT_TIMEOUT = 30  # seconds

    def fetch(self, url: str) -> str:
        """Fetch the HTML content of the given URL.

        Args:
            url: The URL to fetch.

        Returns:
            The response body as a UTF-8 decoded string.

        Raises:
            ScraperFetchError: On non-200 HTTP status, network error, or timeout.
        """
        headers = {"User-Agent": self.USER_AGENT}
        try:
            response = requests.get(url, headers=headers, timeout=self.DEFAULT_TIMEOUT)
        except requests.Timeout as exc:
            raise ScraperFetchError(
                f"URL {url} failed: request timed out"
            ) from exc
        except requests.ConnectionError as exc:
            raise ScraperFetchError(
                f"URL {url} failed: connection error"
            ) from exc
        except requests.RequestException as exc:
            raise ScraperFetchError(
                f"URL {url} failed: {exc}"
            ) from exc

        if response.status_code != 200:
            raise ScraperFetchError(
                f"URL {url} returned status {response.status_code}"
            )

        return response.content.decode("utf-8")
