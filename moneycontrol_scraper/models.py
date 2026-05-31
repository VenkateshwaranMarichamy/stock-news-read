"""Data models for the MoneyControl scraper."""

from dataclasses import dataclass, field


@dataclass
class OutputRecord:
    """Structured record produced for a single scraped article.

    Attributes:
        date: Publication date in ISO 8601 format (YYYY-MM-DD), or None if not found.
        url: Source article URL.
        sections: Nested mapping of section title → stock name → news text.
    """

    date: str | None
    url: str
    sections: dict[str, dict[str, str]] = field(default_factory=dict)
