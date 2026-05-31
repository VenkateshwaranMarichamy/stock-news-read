"""Serialisation and UTF-8 sanitisation for the MoneyControl scraper."""

from __future__ import annotations

import logging
import re

from moneycontrol_scraper.exceptions import ScraperOutputError
from moneycontrol_scraper.models import OutputRecord

logger = logging.getLogger(__name__)

# Regex matching Unicode surrogate code points U+D800–U+DFFF.
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]", re.UNICODE)

# Replacement character used in place of surrogates and null bytes.
_REPLACEMENT_CHAR = "\ufffd"


def sanitise_string(value: str, field_name: str = "", url: str = "") -> str:
    """Replace Unicode surrogate code points (U+D800–U+DFFF) and null bytes
    (U+0000) with the Unicode replacement character (U+FFFD).

    If the result still cannot be encoded as valid UTF-8, returns an empty
    string and logs a warning identifying the affected field and article URL.

    Args:
        value:      The string to sanitise.
        field_name: Optional name of the field being sanitised (for logging).
        url:        Optional article URL associated with the value (for logging).

    Returns:
        A UTF-8-encodable string with surrogates and null bytes replaced, or
        ``""`` if the string remains unencodable after replacement.
    """
    # Replace surrogate code points with U+FFFD.
    sanitised = _SURROGATE_RE.sub(_REPLACEMENT_CHAR, value)

    # Replace null bytes (U+0000) with U+FFFD.
    sanitised = sanitised.replace("\x00", _REPLACEMENT_CHAR)

    # Verify the result is valid UTF-8.
    try:
        sanitised.encode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        logger.warning(
            "String value could not be encoded as UTF-8 after sanitisation; "
            "substituting empty string. field=%r url=%r",
            field_name,
            url,
        )
        return ""

    return sanitised


def serialise(records: list[OutputRecord]) -> str:
    """Serialise a list of OutputRecord objects to a JSON string.

    Sanitises all string fields before encoding. Output is UTF-8 with
    ``indent=2``.

    For each record, the following fields are sanitised via
    :func:`sanitise_string`:

    - ``record.url``
    - ``record.date`` (when not ``None``)
    - Each section title key in ``record.sections``
    - Each stock name key within each section
    - Each news text value within each section

    Args:
        records: List of :class:`~moneycontrol_scraper.models.OutputRecord`
            instances to serialise.

    Returns:
        A JSON string (UTF-8, ``indent=2``) representing the list of records.
    """
    import json

    output: list[dict] = []

    for record in records:
        url = sanitise_string(record.url, field_name="url", url=record.url)
        date = (
            sanitise_string(record.date, field_name="date", url=record.url)
            if record.date is not None
            else None
        )

        sanitised_sections: dict[str, dict[str, str]] = {}
        for section_title, stocks in record.sections.items():
            clean_title = sanitise_string(
                section_title, field_name="section_title", url=record.url
            )
            sanitised_stocks: dict[str, str] = {}
            for stock_name, news_text in stocks.items():
                clean_stock = sanitise_string(
                    stock_name, field_name="stock_name", url=record.url
                )
                clean_text = sanitise_string(
                    news_text, field_name="news_text", url=record.url
                )
                sanitised_stocks[clean_stock] = clean_text
            sanitised_sections[clean_title] = sanitised_stocks

        output.append(
            {
                "date": date,
                "url": url,
                "sections": sanitised_sections,
            }
        )

    return json.dumps(output, indent=2, ensure_ascii=False)


def write_output(json_str: str, output_path: str | None) -> None:
    """Write *json_str* to *output_path*, or print to stdout if path is None.

    When *output_path* is provided the JSON is written exclusively to that
    file (UTF-8 encoding) and nothing is printed to standard output.  When
    *output_path* is ``None`` the JSON is printed to standard output via
    :func:`print`.

    The writability of *output_path* is checked only at write time — no
    upfront validation is performed.  If the file cannot be opened or written
    (e.g. ``PermissionError``, ``IsADirectoryError``, or any other
    ``OSError``), the function raises :class:`ScraperOutputError` with a
    descriptive message that includes the path.  No partial output is written
    on error.

    Args:
        json_str:    The JSON string to write.
        output_path: Destination file path, or ``None`` to write to stdout.

    Raises:
        ScraperOutputError: if *output_path* is given but cannot be written.
    """
    if output_path is None:
        print(json_str)
        return

    try:
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(json_str)
    except (PermissionError, IsADirectoryError, OSError) as exc:
        raise ScraperOutputError(
            f"Cannot write output to {output_path!r}: {exc}"
        ) from exc
