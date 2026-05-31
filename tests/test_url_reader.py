"""Tests for read_urls — Property 11; edge cases 7.1, 7.5."""

from __future__ import annotations

import os
import stat
import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from moneycontrol_scraper.exceptions import ScraperInputError
from moneycontrol_scraper.url_reader import read_urls


# ---------------------------------------------------------------------------
# Helpers / strategies
# ---------------------------------------------------------------------------

# A "valid URL line" for the purposes of file parsing: non-empty, not starting
# with '#', no leading/trailing whitespace (so it survives the strip).
_valid_url_line = st.from_regex(
    r"https://www\.moneycontrol\.com/[A-Za-z0-9/_-]+",
    fullmatch=True,
)

_blank_line = st.just("")

_comment_line = st.text(
    alphabet=st.characters(blacklist_categories=["Cc", "Cs"]),
    min_size=0,
).map(lambda s: "# " + s)


# ---------------------------------------------------------------------------
# Property 11: URL file parsing ignores blank lines and comments
# Feature: moneycontrol-stocks-scraper, Property 11: file parsing ignores blank lines and comments
# Validates: Requirements 7.2
# ---------------------------------------------------------------------------

@given(
    lines=st.lists(
        st.one_of(_valid_url_line, _blank_line, _comment_line),
        min_size=0,
        max_size=50,
    )
)
@settings(max_examples=100)
def test_file_parsing_ignores_blank_and_comment_lines(lines: list[str]) -> None:
    """Property 11: read_urls returns exactly the non-blank, non-comment lines in order."""
    expected = [ln for ln in lines if ln.strip() and not ln.strip().startswith("#")]

    file_content = "\n".join(lines)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(file_content)
        tmp_path = fh.name

    try:
        result = read_urls([], tmp_path)
    finally:
        os.unlink(tmp_path)

    assert result == expected


# ---------------------------------------------------------------------------
# Example: Requirement 7.1 — positional URL args are returned correctly
# ---------------------------------------------------------------------------

def test_positional_args_returned_as_is() -> None:
    """Positional args are included in the result unchanged."""
    urls = [
        "https://www.moneycontrol.com/news/article-1.html",
        "https://www.moneycontrol.com/news/article-2.html",
    ]
    result = read_urls(urls, None)
    assert result == urls


def test_positional_args_with_no_file() -> None:
    """When file_path is None, only positional args are returned."""
    result = read_urls(["https://example.com/a"], None)
    assert result == ["https://example.com/a"]


def test_empty_positional_args_and_no_file() -> None:
    """Empty positional args and no file returns an empty list."""
    assert read_urls([], None) == []


# ---------------------------------------------------------------------------
# Example: positional args come before file URLs
# ---------------------------------------------------------------------------

def test_positional_args_precede_file_urls() -> None:
    """Positional args appear before file-sourced URLs in the result."""
    positional = ["https://www.moneycontrol.com/pos1.html"]
    file_urls = [
        "https://www.moneycontrol.com/file1.html",
        "https://www.moneycontrol.com/file2.html",
    ]
    file_content = "\n".join(file_urls)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(file_content)
        tmp_path = fh.name

    try:
        result = read_urls(positional, tmp_path)
    finally:
        os.unlink(tmp_path)

    assert result == positional + file_urls


# ---------------------------------------------------------------------------
# Example: duplicates are preserved
# ---------------------------------------------------------------------------

def test_duplicates_are_preserved() -> None:
    """Duplicate URLs are not deduplicated."""
    url = "https://www.moneycontrol.com/news/article.html"
    result = read_urls([url, url], None)
    assert result == [url, url]


# ---------------------------------------------------------------------------
# Edge case: Requirement 7.5 — non-existent --file raises ScraperInputError
# ---------------------------------------------------------------------------

def test_nonexistent_file_raises_scraper_input_error() -> None:
    """A non-existent file path raises ScraperInputError with the path in the message."""
    bad_path = "/tmp/this_file_does_not_exist_xyz_12345.txt"
    with pytest.raises(ScraperInputError) as exc_info:
        read_urls([], bad_path)
    assert bad_path in str(exc_info.value)


def test_unreadable_file_raises_scraper_input_error(tmp_path: pytest.TempPathFactory) -> None:
    """A file with no read permissions raises ScraperInputError."""
    restricted = tmp_path / "restricted.txt"
    restricted.write_text("https://www.moneycontrol.com/news/article.html\n")
    restricted.chmod(0o000)

    try:
        with pytest.raises(ScraperInputError) as exc_info:
            read_urls([], str(restricted))
        assert str(restricted) in str(exc_info.value)
    finally:
        # Restore permissions so pytest can clean up the temp dir
        restricted.chmod(stat.S_IRUSR | stat.S_IWUSR)


# ---------------------------------------------------------------------------
# Edge case: file with only blank lines and comments returns empty list
# ---------------------------------------------------------------------------

def test_file_with_only_blanks_and_comments_returns_empty() -> None:
    """A file containing only blank lines and comments yields no URLs."""
    content = "# comment\n\n   \n# another comment\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(content)
        tmp_path = fh.name

    try:
        result = read_urls([], tmp_path)
    finally:
        os.unlink(tmp_path)

    assert result == []


# ---------------------------------------------------------------------------
# Edge case: inline whitespace on URL lines is stripped
# ---------------------------------------------------------------------------

def test_url_lines_are_stripped() -> None:
    """Leading/trailing whitespace on URL lines is stripped."""
    content = "  https://www.moneycontrol.com/news/article.html  \n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(content)
        tmp_path = fh.name

    try:
        result = read_urls([], tmp_path)
    finally:
        os.unlink(tmp_path)

    assert result == ["https://www.moneycontrol.com/news/article.html"]
