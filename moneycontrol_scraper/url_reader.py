"""URL collection from CLI positional arguments and/or a --file path."""

from __future__ import annotations

from moneycontrol_scraper.exceptions import ScraperInputError


def read_urls(positional_args: list[str], file_path: str | None) -> list[str]:
    """
    Collect URLs from positional CLI arguments and/or a --file path.

    Args:
        positional_args: URLs supplied directly on the command line.
        file_path: Optional path to a plain-text file containing one URL per
                   line.  Blank lines and lines beginning with ``#`` are
                   ignored.

    Returns:
        Combined list of URLs — positional args first, then file URLs — in
        the order they were provided.  Duplicates are preserved.

    Raises:
        ScraperInputError: If ``file_path`` is given but the file cannot be
                           read (e.g. does not exist or permission denied).
    """
    urls: list[str] = list(positional_args)

    if file_path is not None:
        try:
            with open(file_path, encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    # Skip blank lines and comment lines
                    if not stripped or stripped.startswith("#"):
                        continue
                    urls.append(stripped)
        except OSError as exc:
            raise ScraperInputError(
                f"Cannot read URL file '{file_path}': {exc.strerror}"
            ) from exc

    return urls
