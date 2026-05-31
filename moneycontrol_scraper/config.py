"""Configuration loader for the MoneyControl scraper.

Reads ``config.yaml`` (or a custom path) and exposes a :class:`ScraperConfig`
dataclass with typed fields.  All fields have sensible defaults so the scraper
works even without a config file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Default config file location — project root / config.yaml
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


@dataclass
class ScraperConfig:
    """Typed configuration for the scraper.

    Attributes:
        urls:       List of article URLs to scrape (from config file).
        sections:   Whitelist of section title substrings to keep.
                    Empty list means keep all sections.
        output_dir: Directory where timestamped JSON files are saved.
        delay:      Seconds to wait between consecutive HTTP requests.
    """

    urls: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    output_dir: str = "output"
    delay: float = 1.0


def load_config(path: str | Path | None = None) -> ScraperConfig:
    """Load configuration from a YAML file.

    Falls back to :data:`DEFAULT_CONFIG_PATH` when *path* is ``None``.
    Returns a default :class:`ScraperConfig` if the file does not exist or
    cannot be parsed.

    Args:
        path: Path to the YAML config file, or ``None`` to use the default.

    Returns:
        A populated :class:`ScraperConfig` instance.
    """
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH

    if not config_path.exists():
        logger.warning("Config file not found at %s — using defaults.", config_path)
        return ScraperConfig()

    try:
        import yaml  # type: ignore[import]
    except ImportError:
        logger.warning(
            "PyYAML is not installed — cannot read config file. "
            "Install it with: pip install pyyaml"
        )
        return ScraperConfig()

    try:
        with open(config_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to parse config file %s: %s — using defaults.", config_path, exc)
        return ScraperConfig()

    return ScraperConfig(
        urls=_as_str_list(data.get("urls", [])),
        sections=_as_str_list(data.get("sections", [])),
        output_dir=str(data.get("output_dir", "output")),
        delay=float(data.get("delay", 1.0)),
    )


def filter_sections(
    sections: dict[str, dict[str, str]],
    whitelist: list[str],
) -> dict[str, dict[str, str]]:
    """Return only the sections whose title matches the whitelist.

    Matching is case-insensitive substring: a section is kept if any
    whitelist entry appears anywhere in the section title.

    If *whitelist* is empty, all sections are returned unchanged.

    Args:
        sections:  The full sections dict from an :class:`OutputRecord`.
        whitelist: List of section title substrings to keep.

    Returns:
        Filtered sections dict.
    """
    if not whitelist:
        return sections

    lower_whitelist = [w.lower() for w in whitelist]
    return {
        title: stocks
        for title, stocks in sections.items()
        if any(w in title.lower() for w in lower_whitelist)
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _as_str_list(value: object) -> list[str]:
    """Coerce *value* to a list of strings."""
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    if isinstance(value, str):
        return [value]
    return []
