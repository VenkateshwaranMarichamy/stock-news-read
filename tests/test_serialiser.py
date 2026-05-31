"""Tests for moneycontrol_scraper.serialiser — property-based and unit tests."""

from __future__ import annotations

import json
import os
import stat

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from moneycontrol_scraper.exceptions import ScraperOutputError
from moneycontrol_scraper.models import OutputRecord
from moneycontrol_scraper.serialiser import sanitise_string, serialise, write_output


# ---------------------------------------------------------------------------
# Task 5.2 — Property 12: Surrogate and null-byte sanitisation
# Feature: moneycontrol-stocks-scraper, Property 12: Surrogate and null-byte sanitisation
# Validates: Requirements 8.2
# ---------------------------------------------------------------------------


@given(st.text(alphabet=st.characters(categories=["Cs"]), min_size=1))
@settings(max_examples=200)
def test_property12_surrogate_sanitisation(surrogate_text):
    # Feature: moneycontrol-stocks-scraper, Property 12: Surrogate and null-byte sanitisation
    """For any string containing Unicode surrogate code points (U+D800–U+DFFF),
    sanitise_string SHALL replace each surrogate with U+FFFD and the result
    SHALL be encodable as valid UTF-8.

    Validates: Requirements 8.2
    """
    result = sanitise_string(surrogate_text)

    # No surrogates remain in the result
    for ch in result:
        assert "\ud800" <= ch <= "\udfff" or ch == "\ufffd" or ch not in (
            chr(c) for c in range(0xD800, 0xE000)
        ), f"Surrogate {ch!r} found in result"
    # Verify no surrogates remain
    assert not any("\ud800" <= ch <= "\udfff" for ch in result), (
        f"Surrogates still present in sanitised result: {result!r}"
    )

    # Result must be UTF-8 encodable
    encoded = result.encode("utf-8")
    assert isinstance(encoded, bytes)


@given(
    st.text(
        alphabet=st.characters(blacklist_categories=["Cs"]),
        max_size=20,
    ).map(lambda s: s + "\x00" + s)
)
@settings(max_examples=200)
def test_property12_null_byte_sanitisation(text_with_null):
    # Feature: moneycontrol-stocks-scraper, Property 12: Surrogate and null-byte sanitisation
    """For any string containing null bytes (U+0000), sanitise_string SHALL
    replace each null byte with U+FFFD and the result SHALL be encodable as
    valid UTF-8.

    Validates: Requirements 8.2
    """
    result = sanitise_string(text_with_null)

    # No null bytes remain
    assert "\x00" not in result, f"Null byte still present in result: {result!r}"

    # Result must be UTF-8 encodable
    encoded = result.encode("utf-8")
    assert isinstance(encoded, bytes)


# ---------------------------------------------------------------------------
# Task 5.4 — Property 8: Serialisation round-trip preserves structure
# Feature: moneycontrol-stocks-scraper, Property 8: Serialisation round-trip preserves structure
# Validates: Requirements 5.2, 8.1
# ---------------------------------------------------------------------------

# Strategy for safe text (no surrogates, no null bytes) used in OutputRecord fields.
# Null bytes are excluded because serialise() sanitises them (per Req 8.2), so
# a round-trip would not preserve them verbatim — which is correct behaviour.
_safe_text = st.text(
    alphabet=st.characters(blacklist_categories=["Cs"], blacklist_characters="\x00"),
    max_size=50,
)

# Strategy for building a sections dict: section title → {stock name → news text}
_sections_strategy = st.dictionaries(
    keys=_safe_text,
    values=st.dictionaries(keys=_safe_text, values=_safe_text, max_size=5),
    max_size=5,
)

# Composite strategy for OutputRecord
_output_record_strategy = st.builds(
    OutputRecord,
    date=st.one_of(st.none(), _safe_text),
    url=_safe_text,
    sections=_sections_strategy,
)


@given(_output_record_strategy)
@settings(max_examples=100)
def test_property8_serialisation_round_trip(record):
    # Feature: moneycontrol-stocks-scraper, Property 8: Serialisation round-trip preserves structure
    """For any valid OutputRecord with arbitrary safe string values, serialising
    to JSON and parsing back SHALL produce a Python object with identical keys,
    values, and value types as the original record.

    Validates: Requirements 5.2, 8.1
    """
    json_str = serialise([record])

    # Must be valid JSON
    parsed = json.loads(json_str)

    # Output is a list with exactly one element
    assert isinstance(parsed, list)
    assert len(parsed) == 1

    obj = parsed[0]

    # Keys present
    assert "date" in obj
    assert "url" in obj
    assert "sections" in obj

    # date: None maps to JSON null, string maps to string
    if record.date is None:
        assert obj["date"] is None
    else:
        assert isinstance(obj["date"], str)
        assert obj["date"] == record.date

    # url: string preserved
    assert isinstance(obj["url"], str)
    assert obj["url"] == record.url

    # sections: nested dict structure preserved
    assert isinstance(obj["sections"], dict)
    assert set(obj["sections"].keys()) == set(record.sections.keys())

    for section_title, stocks in record.sections.items():
        assert section_title in obj["sections"]
        assert isinstance(obj["sections"][section_title], dict)
        assert set(obj["sections"][section_title].keys()) == set(stocks.keys())
        for stock_name, news_text in stocks.items():
            assert obj["sections"][section_title][stock_name] == news_text
            assert isinstance(obj["sections"][section_title][stock_name], str)


# ---------------------------------------------------------------------------
# Task 5.6 — Unit tests for serialiser examples and edge cases
# ---------------------------------------------------------------------------


class TestWriteOutputToFile:
    """write_output(json_str, path) writes correct content to file."""

    def test_writes_json_to_file(self, tmp_path):
        json_str = '["hello", "world"]'
        output_file = tmp_path / "output.json"
        write_output(json_str, str(output_file))

        assert output_file.exists()
        content = output_file.read_text(encoding="utf-8")
        assert content == json_str

    def test_written_file_is_valid_json(self, tmp_path):
        record = OutputRecord(date="2024-05-13", url="https://example.com", sections={})
        json_str = serialise([record])
        output_file = tmp_path / "result.json"
        write_output(json_str, str(output_file))

        content = output_file.read_text(encoding="utf-8")
        parsed = json.loads(content)
        assert isinstance(parsed, list)
        assert parsed[0]["date"] == "2024-05-13"
        assert parsed[0]["url"] == "https://example.com"


class TestWriteOutputToStdout:
    """write_output(json_str, None) prints to stdout."""

    def test_prints_to_stdout(self, capsys):
        json_str = '{"key": "value"}'
        write_output(json_str, None)

        captured = capsys.readouterr()
        assert json_str in captured.out

    def test_nothing_written_to_stderr(self, capsys):
        write_output("[]", None)
        captured = capsys.readouterr()
        assert captured.err == ""


class TestWriteOutputUnwritablePath:
    """Unwritable output path raises ScraperOutputError."""

    def test_unwritable_path_raises_scraper_output_error(self, tmp_path):
        # Create a read-only directory; writing a file inside it should fail
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)  # r-x, no write

        target = readonly_dir / "output.json"
        with pytest.raises(ScraperOutputError):
            write_output('["data"]', str(target))

        # Restore permissions so tmp_path cleanup works
        readonly_dir.chmod(stat.S_IRWXU)

    def test_is_directory_raises_scraper_output_error(self, tmp_path):
        # Passing a directory path (not a file) should raise ScraperOutputError
        with pytest.raises(ScraperOutputError):
            write_output('["data"]', str(tmp_path))


class TestSanitiseStringUnit:
    """Unit tests for sanitise_string."""

    def test_surrogate_replaced_with_replacement_char(self):
        # U+D800 is a high surrogate
        value = "hello\ud800world"
        result = sanitise_string(value)
        assert "\ud800" not in result
        assert "\ufffd" in result
        result.encode("utf-8")  # must not raise

    def test_null_byte_replaced_with_replacement_char(self):
        value = "hello\x00world"
        result = sanitise_string(value)
        assert "\x00" not in result
        assert "\ufffd" in result
        result.encode("utf-8")  # must not raise

    def test_clean_string_unchanged(self):
        value = "Reliance Industries"
        assert sanitise_string(value) == value

    def test_multiple_surrogates_all_replaced(self):
        value = "\ud800\udc00\udfff"
        result = sanitise_string(value)
        assert not any("\ud800" <= ch <= "\udfff" for ch in result)
        result.encode("utf-8")  # must not raise

    def test_empty_string_returns_empty(self):
        assert sanitise_string("") == ""

    def test_mixed_surrogates_and_normal_text(self):
        value = "abc\ud900def\x00ghi"
        result = sanitise_string(value)
        assert "abc" in result
        assert "def" in result
        assert "ghi" in result
        assert "\ud900" not in result
        assert "\x00" not in result
        result.encode("utf-8")  # must not raise


class TestSerialiseEdgeCases:
    """Edge cases for the serialise function."""

    def test_serialise_empty_list_returns_json_array(self):
        result = serialise([])
        assert result == "[]"

    def test_serialise_empty_list_is_valid_json(self):
        result = serialise([])
        parsed = json.loads(result)
        assert parsed == []

    def test_serialise_record_with_date_none_produces_null(self):
        record = OutputRecord(date=None, url="https://example.com", sections={})
        result = serialise([record])
        parsed = json.loads(result)
        assert parsed[0]["date"] is None

    def test_serialise_record_with_date_string(self):
        record = OutputRecord(date="2024-05-13", url="https://example.com", sections={})
        result = serialise([record])
        parsed = json.loads(result)
        assert parsed[0]["date"] == "2024-05-13"

    def test_serialise_result_is_indented_json(self):
        record = OutputRecord(date="2024-01-01", url="https://example.com", sections={})
        result = serialise([record])
        # indent=2 means the JSON should contain newlines and spaces
        assert "\n" in result

    def test_serialise_sanitises_surrogates_in_url(self):
        record = OutputRecord(
            date=None,
            url="https://example.com/\ud800path",
            sections={},
        )
        result = serialise([record])
        parsed = json.loads(result)
        assert "\ud800" not in parsed[0]["url"]

    def test_serialise_sanitises_null_bytes_in_section_title(self):
        record = OutputRecord(
            date=None,
            url="https://example.com",
            sections={"Section\x00Title": {"Stock": "text"}},
        )
        result = serialise([record])
        parsed = json.loads(result)
        sections = parsed[0]["sections"]
        for key in sections:
            assert "\x00" not in key
