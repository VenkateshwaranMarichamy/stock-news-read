"""CLI integration tests — Properties 9, 10; unit tests for CLI examples and edge cases.

Tests:
  - Property 9: Multi-URL output array length equals input count
  - Property 10: Per-URL error recovery preserves successful records
  - Unit tests for CLI integration examples and edge cases (Task 7.5)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from moneycontrol_scraper.exceptions import ScraperFetchError
from moneycontrol_scraper.models import OutputRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_HTML = "<html><body><div class='content_wrapper'></div></body></html>"

_VALID_RECORD = OutputRecord(
    date="2024-05-13",
    url="https://www.moneycontrol.com/news/article.html",
    sections={"Stocks to Watch": {"Reliance": "Strong Q4 results."}},
)


def _make_output_record(url: str) -> OutputRecord:
    """Return a minimal valid OutputRecord for the given URL."""
    return OutputRecord(date="2024-05-13", url=url, sections={})


# ---------------------------------------------------------------------------
# Property 9: Multi-URL output array length equals input count
# Feature: moneycontrol-stocks-scraper, Property 9: Multi-URL output array length equals input count
# Validates: Requirements 5.3, 6.2
# ---------------------------------------------------------------------------

@given(
    urls=st.lists(
        st.just("https://www.moneycontrol.com/news/article.html"),
        min_size=1,
        max_size=10,
    )
)
@settings(max_examples=50)
def test_property_9_multi_url_output_length_equals_input_count(
    urls: list[str],
) -> None:
    """Property 9: For N URLs where all fetches succeed, the output JSON array
    has exactly N records, each with a 'url' field.

    **Validates: Requirements 5.3, 6.2**
    """
    n = len(urls)

    def mock_parse(html: str, url: str) -> OutputRecord:
        return _make_output_record(url)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as tmp:
        tmp_path = tmp.name

    try:
        with (
            patch(
                "moneycontrol_scraper.http_client.HTTPClient.fetch",
                return_value=_MINIMAL_HTML,
            ),
            patch(
                "moneycontrol_scraper.parser.ArticleParser.parse",
                side_effect=mock_parse,
            ),
            patch("sys.argv", ["moneycontrol-scraper"] + urls + ["--output", tmp_path]),
            patch("time.sleep"),  # skip delays
        ):
            from moneycontrol_scraper.cli import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        with open(tmp_path, encoding="utf-8") as fh:
            records = json.load(fh)

        assert isinstance(records, list), "Output must be a JSON array"
        assert len(records) == n, (
            f"Expected {n} records for {n} URLs, got {len(records)}"
        )
        for record in records:
            assert "url" in record, "Each record must have a 'url' field"
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Property 10: Per-URL error recovery preserves successful records
# Feature: moneycontrol-stocks-scraper, Property 10: Per-URL error recovery preserves successful records
# Validates: Requirement 6.3
# ---------------------------------------------------------------------------

@given(
    url_pairs=st.lists(
        st.tuples(
            st.just("https://www.moneycontrol.com/news/article.html"),
            st.booleans(),  # True = should_fail
        ),
        min_size=1,
        max_size=10,
    )
)
@settings(max_examples=50)
def test_property_10_error_recovery_preserves_successful_records(
    url_pairs: list[tuple[str, bool]],
) -> None:
    """Property 10: When a subset of URLs fail to fetch, the output contains
    exactly one record per successful URL and none for failed URLs.

    **Validates: Requirement 6.3**
    """
    urls = [url for url, _ in url_pairs]
    failing = {i for i, (_, should_fail) in enumerate(url_pairs) if should_fail}
    expected_success_count = len(url_pairs) - len(failing)

    call_count = [0]

    def mock_fetch(url: str) -> str:
        idx = call_count[0]
        call_count[0] += 1
        if idx in failing:
            raise ScraperFetchError(f"URL {url} returned status 500")
        return _MINIMAL_HTML

    def mock_parse(html: str, url: str) -> OutputRecord:
        return _make_output_record(url)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as tmp:
        tmp_path = tmp.name

    try:
        with (
            patch(
                "moneycontrol_scraper.http_client.HTTPClient.fetch",
                side_effect=mock_fetch,
            ),
            patch(
                "moneycontrol_scraper.parser.ArticleParser.parse",
                side_effect=mock_parse,
            ),
            patch("sys.argv", ["moneycontrol-scraper"] + urls + ["--output", tmp_path]),
            patch("time.sleep"),
        ):
            from moneycontrol_scraper.cli import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        with open(tmp_path, encoding="utf-8") as fh:
            records = json.load(fh)

        assert isinstance(records, list), "Output must be a JSON array"
        assert len(records) == expected_success_count, (
            f"Expected {expected_success_count} successful records, got {len(records)}"
        )
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Unit tests: CLI integration examples and edge cases (Task 7.5)
# ---------------------------------------------------------------------------


class TestCLINoInput:
    """No URLs, no --file, and no config URLs → SystemExit(1) and usage printed to stderr."""

    def test_no_urls_no_file_exits_with_code_1(self, capsys: pytest.CaptureFixture) -> None:
        from moneycontrol_scraper.config import ScraperConfig
        with (
            patch("sys.argv", ["moneycontrol-scraper"]),
            # Return a config with no URLs so the no-input path is exercised
            patch("moneycontrol_scraper.cli.load_config", return_value=ScraperConfig(urls=[])),
        ):
            from moneycontrol_scraper.cli import main

            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 1

    def test_no_urls_no_file_prints_usage_to_stderr(self, capsys: pytest.CaptureFixture) -> None:
        from moneycontrol_scraper.config import ScraperConfig
        with (
            patch("sys.argv", ["moneycontrol-scraper"]),
            patch("moneycontrol_scraper.cli.load_config", return_value=ScraperConfig(urls=[])),
        ):
            from moneycontrol_scraper.cli import main

            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert captured.err, "Usage message should be printed to stderr"
        assert "usage" in captured.err.lower()


class TestCLIUnreadableFile:
    """Unreadable --file path → SystemExit(1) and error message printed."""

    def test_unreadable_file_exits_with_code_1(self, capsys: pytest.CaptureFixture) -> None:
        with patch(
            "sys.argv",
            ["moneycontrol-scraper", "--file", "/nonexistent/path/urls.txt"],
        ):
            from moneycontrol_scraper.cli import main

            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 1

    def test_unreadable_file_prints_error_to_stderr(self, capsys: pytest.CaptureFixture) -> None:
        with patch(
            "sys.argv",
            ["moneycontrol-scraper", "--file", "/nonexistent/path/urls.txt"],
        ):
            from moneycontrol_scraper.cli import main

            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert captured.err, "Error message should be printed to stderr"


class TestCLIUnwritableOutput:
    """Unwritable --output path → SystemExit(1)."""

    def test_unwritable_output_exits_with_code_1(self) -> None:
        url = "https://www.moneycontrol.com/news/article.html"
        unwritable_path = "/nonexistent_dir/output.json"

        with (
            patch(
                "moneycontrol_scraper.http_client.HTTPClient.fetch",
                return_value=_MINIMAL_HTML,
            ),
            patch(
                "moneycontrol_scraper.parser.ArticleParser.parse",
                return_value=_make_output_record(url),
            ),
            patch(
                "sys.argv",
                ["moneycontrol-scraper", url, "--output", unwritable_path],
            ),
            patch("time.sleep"),
        ):
            from moneycontrol_scraper.cli import main

            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 1


class TestCLIDelay:
    """--delay causes time.sleep to be called with the configured value between requests."""

    def test_delay_calls_time_sleep_with_configured_value(self) -> None:
        urls = [
            "https://www.moneycontrol.com/news/article1.html",
            "https://www.moneycontrol.com/news/article2.html",
            "https://www.moneycontrol.com/news/article3.html",
        ]
        delay = 2.5

        def mock_parse(html: str, url: str) -> OutputRecord:
            return _make_output_record(url)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            tmp_path = tmp.name

        try:
            with (
                patch(
                    "moneycontrol_scraper.http_client.HTTPClient.fetch",
                    return_value=_MINIMAL_HTML,
                ),
                patch(
                    "moneycontrol_scraper.parser.ArticleParser.parse",
                    side_effect=mock_parse,
                ),
                patch("sys.argv", ["moneycontrol-scraper"] + urls + ["--output", tmp_path, "--delay", str(delay)]),
                patch("time.sleep") as mock_sleep,
            ):
                from moneycontrol_scraper.cli import main

                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 0

            # sleep is called between requests: N-1 times for N URLs
            assert mock_sleep.call_count == len(urls) - 1
            for call in mock_sleep.call_args_list:
                assert call.args[0] == delay, (
                    f"time.sleep should be called with {delay}, got {call.args[0]}"
                )
        finally:
            os.unlink(tmp_path)

    def test_no_sleep_after_last_url(self) -> None:
        """time.sleep is NOT called after the last URL."""
        url = "https://www.moneycontrol.com/news/article.html"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            tmp_path = tmp.name

        try:
            with (
                patch(
                    "moneycontrol_scraper.http_client.HTTPClient.fetch",
                    return_value=_MINIMAL_HTML,
                ),
                patch(
                    "moneycontrol_scraper.parser.ArticleParser.parse",
                    return_value=_make_output_record(url),
                ),
                patch("sys.argv", ["moneycontrol-scraper", url, "--output", tmp_path]),
                patch("time.sleep") as mock_sleep,
            ):
                from moneycontrol_scraper.cli import main

                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 0

            mock_sleep.assert_not_called()
        finally:
            os.unlink(tmp_path)


class TestCLISingleURLToStdout:
    """Single URL with mocked fetch+parse → valid JSON array with 1 record written to stdout.
    Uses --output - convention (stdout) by patching _resolve_output_path to return None."""

    def test_single_url_outputs_json_array_to_stdout(self, capsys: pytest.CaptureFixture) -> None:
        url = "https://www.moneycontrol.com/news/article.html"
        expected_record = _make_output_record(url)

        with (
            patch(
                "moneycontrol_scraper.http_client.HTTPClient.fetch",
                return_value=_MINIMAL_HTML,
            ),
            patch(
                "moneycontrol_scraper.parser.ArticleParser.parse",
                return_value=expected_record,
            ),
            # Force output to stdout by making _resolve_output_path return None
            patch("moneycontrol_scraper.cli._resolve_output_path", return_value=None),
            patch("sys.argv", ["moneycontrol-scraper", url]),
            patch("time.sleep"),
        ):
            from moneycontrol_scraper.cli import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        captured = capsys.readouterr()
        records = json.loads(captured.out)

        assert isinstance(records, list), "Output must be a JSON array"
        assert len(records) == 1, "Expected exactly 1 record"
        assert records[0]["url"] == url
        assert records[0]["date"] == "2024-05-13"
        assert records[0]["sections"] == {}
