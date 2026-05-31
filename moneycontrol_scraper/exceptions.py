"""Custom exception hierarchy for the MoneyControl scraper."""


class ScraperError(RuntimeError):
    """Base exception for all scraper errors."""


class ScraperFetchError(ScraperError):
    """Raised when an HTTP request fails (non-200 status, network error, or timeout)."""


class ScraperInputError(ScraperError):
    """Raised when input is invalid (e.g., unreadable --file path, no URLs provided)."""


class ScraperOutputError(ScraperError):
    """Raised when the output destination is not writable."""
