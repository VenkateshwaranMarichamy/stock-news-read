# Implementation Plan: moneycontrol-stocks-scraper

## Overview

Implement a Python CLI package that fetches MoneyControl "Stocks to Watch" articles, parses them into structured sections and stock entries, and emits a JSON array of `OutputRecord` objects. The implementation follows the module breakdown in the design: `http_client.py`, `parser.py`, `serialiser.py`, `url_reader.py`, and `cli.py`, with a full pytest + Hypothesis test suite.

---

## Tasks

- [x] 1. Set up package structure, shared types, and custom exceptions
  - Create `moneycontrol_scraper/` directory with `__init__.py`
  - Define the `OutputRecord` dataclass (`date`, `url`, `sections` fields) in `moneycontrol_scraper/models.py`
  - Define the custom exception hierarchy (`ScraperError`, `ScraperFetchError`, `ScraperInputError`, `ScraperOutputError`) in `moneycontrol_scraper/exceptions.py`
  - Create `tests/` directory with an empty `__init__.py` (or `conftest.py`)
  - Add `pyproject.toml` (or `setup.cfg`) declaring `requests`, `beautifulsoup4` as runtime dependencies and `pytest`, `hypothesis` as dev dependencies
  - _Requirements: 1.2, 1.3, 5.2, 6.3, 7.4, 7.5_

- [x] 2. Implement `http_client.py` — HTTP fetching
  - [x] 2.1 Implement `HTTPClient.fetch` with User-Agent header, 30-second timeout, and `ScraperFetchError` on non-200 or network failure
    - Set `USER_AGENT` class constant and inject it as the `User-Agent` header on every request
    - Raise `ScraperFetchError` containing the URL and status code for non-200 responses
    - Catch `requests.ConnectionError` and `requests.Timeout`; raise `ScraperFetchError` with URL and reason
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x]* 2.2 Write property test for non-200 HTTP responses (Property 1)
    - **Property 1: Non-200 HTTP responses raise a descriptive error**
    - Use `st.integers(min_value=100, max_value=599).filter(lambda x: x != 200)` to generate status codes
    - Mock `requests.get` to return a response with the generated status code
    - Assert `ScraperFetchError` is raised and its message contains both the URL and the status code
    - **Validates: Requirements 1.2**

  - [x]* 2.3 Write unit tests for `HTTPClient` edge cases
    - Test network error (`requests.ConnectionError`) raises `ScraperFetchError`
    - Test timeout (`requests.Timeout`) raises `ScraperFetchError`
    - Test that the `User-Agent` header is present and contains "Mozilla" on every request
    - _Requirements: 1.3, 1.4_

- [x] 3. Implement `parser.py` — HTML parsing
  - [x] 3.1 Implement `ArticleParser._find_content_container` and `_extract_sections`
    - Locate the primary article content `div` using BeautifulSoup
    - Walk children: treat `h1`–`h6` and bold/strong block elements as section headings; trim text; skip empty headings
    - Within a section, treat `a`-tagged sub-headings as stock name keys (prefer hyperlink text over surrounding heading text)
    - Concatenate paragraph elements between consecutive stock headings with a single space; strip leading/trailing whitespace from keys and values
    - Handle duplicate stock names by appending subsequent paragraph text to the existing entry
    - Return `{}` and emit `logging.warning` when no headings are found
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [x]* 3.2 Write property test for section heading extraction (Property 2)
    - **Property 2: Section headings are extracted and trimmed**
    - Generate lists of non-empty heading texts with `st.lists(st.text(min_size=1), min_size=1)`; build synthetic HTML around them
    - Assert every non-whitespace heading appears as a trimmed key in `sections`; assert no whitespace-only heading appears
    - **Validates: Requirements 2.1, 2.2, 2.4**

  - [x]* 3.3 Write property test for hyperlink key preference (Property 3)
    - **Property 3: Stock name keys prefer hyperlink text**
    - Generate link text and surrounding heading text with `st.text(min_size=1)`; build HTML where the stock sub-heading contains an `<a>` tag
    - Assert the returned stock name key equals the hyperlink text (stripped), not the outer heading text
    - **Validates: Requirements 3.1**

  - [x]* 3.4 Write property test for paragraph concatenation (Property 4)
    - **Property 4: Paragraph concatenation with single space**
    - Generate two or more paragraph texts with `st.lists(st.text(), min_size=2)`; build HTML with those paragraphs under a stock heading
    - Assert the resulting value equals the paragraphs joined by exactly one space, with leading/trailing whitespace stripped
    - **Validates: Requirements 3.2, 3.4**

  - [x]* 3.5 Write property test for duplicate stock name accumulation (Property 5)
    - **Property 5: Duplicate stock names accumulate text**
    - Generate a stock name and two or more paragraph lists with `st.text(min_size=1)` and `st.lists(st.text(), min_size=2)`; build HTML with the same stock heading appearing multiple times
    - Assert the output contains a single entry for that stock name whose value is the concatenation of all paragraph texts in order
    - **Validates: Requirements 3.3**

  - [x] 3.6 Implement `ArticleParser._extract_date`
    - Implement the five-priority date extraction chain: JSON-LD `datePublished` → `article:published_time` meta → `<time datetime>` → URL regex → `None` + warning
    - Normalise all extracted dates to `YYYY-MM-DD` (ISO 8601 date-only)
    - _Requirements: 4.1, 4.2, 4.3_

  - [x]* 3.7 Write property test for date extraction from metadata (Property 6)
    - **Property 6: Date extraction from metadata produces ISO 8601 format**
    - Generate dates with `st.dates()`; build HTML with JSON-LD, `article:published_time` meta, and `<time datetime>` variants
    - Assert the returned date string matches `YYYY-MM-DD` for each metadata format
    - **Validates: Requirements 4.1**

  - [x]* 3.8 Write property test for date fallback from URL (Property 7)
    - **Property 7: Date fallback extracts from URL path**
    - Generate dates with `st.dates()`; build URLs containing `YYYY/MM/DD` and `YYYY-MM-DD` path patterns; use HTML with no date metadata
    - Assert the returned date string matches `YYYY-MM-DD` extracted from the URL
    - **Validates: Requirements 4.2**

  - [x] 3.9 Implement `ArticleParser.parse` — wire `_find_content_container`, `_extract_sections`, and `_extract_date` into an `OutputRecord`
    - _Requirements: 2.1, 3.1, 4.1, 4.2, 4.3, 5.1_

  - [x]* 3.10 Write unit tests for `ArticleParser` edge cases
    - Test HTML with no headings → empty `sections` dict and warning logged
    - Test section with no stock entries → section key maps to `{}`
    - Test stock heading with no paragraphs → stock key maps to `""`
    - Test HTML with no date metadata and no date in URL → `date` is `None` and warning logged
    - _Requirements: 2.3, 3.5, 3.6, 4.3_

- [x] 4. Checkpoint — Ensure all parser and HTTP client tests pass
  - Run `pytest tests/test_http_client.py tests/test_parser.py -v` and confirm all tests pass; ask the user if questions arise.

- [x] 5. Implement `serialiser.py` — JSON serialisation and output
  - [x] 5.1 Implement `sanitise_string` — replace surrogates (U+D800–U+DFFF) and null bytes (U+0000) with U+FFFD; return `""` if still not UTF-8 encodable
    - _Requirements: 8.2, 8.3_

  - [x]* 5.2 Write property test for surrogate and null-byte sanitisation (Property 12)
    - **Property 12: Surrogate and null-byte sanitisation**
    - Generate strings containing surrogate characters with `st.text(alphabet=st.characters(categories=['Cs']))` and null bytes `'\x00'`
    - Assert every such character is replaced with U+FFFD and the result is encodable as valid UTF-8
    - **Validates: Requirements 8.2**

  - [x] 5.3 Implement `serialise` — sanitise all string fields in each `OutputRecord`, then encode to indented JSON (UTF-8, `indent=2`)
    - _Requirements: 5.1, 5.2_

  - [x]* 5.4 Write property test for serialisation round-trip (Property 8)
    - **Property 8: Serialisation round-trip preserves structure**
    - Build valid `OutputRecord` instances with arbitrary string values using a composite Hypothesis strategy
    - Serialise to JSON, parse back, and assert identical keys, values, and value types
    - **Validates: Requirements 5.2, 8.1**

  - [x] 5.5 Implement `write_output` — write JSON string to file path or stdout; raise `ScraperOutputError` on unwritable path
    - _Requirements: 5.4, 5.5, 5.6_

  - [x]* 5.6 Write unit tests for `serialiser` examples and edge cases
    - Test `write_output` writes correct JSON to a temp file
    - Test `write_output` prints to stdout when no path given (capture stdout)
    - Test unwritable output path raises `ScraperOutputError`
    - Test `sanitise_string` on a string that remains unencodable after replacement → returns `""`
    - _Requirements: 5.4, 5.5, 5.6, 8.3_

- [x] 6. Implement `url_reader.py` — URL collection from CLI args and `--file`
  - [x] 6.1 Implement `read_urls` — merge positional args and `--file` lines; skip blank lines and `#` comments; raise `ScraperInputError` on unreadable file
    - _Requirements: 6.1, 7.1, 7.2, 7.5_

  - [x]* 6.2 Write property test for URL file parsing (Property 11)
    - **Property 11: URL file parsing ignores blank lines and comments**
    - Generate mixed lists of valid URL lines, blank lines, and `#`-prefixed comment lines using `st.lists(st.one_of(...))`; write to a temp file
    - Assert `read_urls` returns exactly the non-blank, non-comment lines in order
    - **Validates: Requirements 7.2**

  - [x]* 6.3 Write unit tests for `url_reader` edge cases
    - Test positional URL args are returned correctly
    - Test non-existent `--file` path raises `ScraperInputError`
    - _Requirements: 7.1, 7.5_

- [x] 7. Implement `cli.py` — argparse entry point and main scraper loop
  - [x] 7.1 Implement `build_parser` — define positional `urls`, `--file`, `--output`, and `--delay` arguments
    - `--delay` defaults to 1 second
    - _Requirements: 7.1, 7.2, 7.3, 6.4_

  - [x] 7.2 Implement `main` — collect URLs via `read_urls`, run the fetch-parse loop with per-URL error recovery, apply inter-request delay, serialise results, write output; exit with code 1 on fatal errors
    - Log `ScraperFetchError` / parse errors at `ERROR` level and continue to next URL
    - Exit with code 1 and print usage when no URLs are provided
    - Exit with code 1 on `ScraperInputError` or `ScraperOutputError`
    - _Requirements: 5.3, 5.4, 5.5, 5.6, 6.1, 6.2, 6.3, 6.4, 7.1, 7.2, 7.3, 7.4, 7.5_

  - [x]* 7.3 Write property test for multi-URL output array length (Property 9)
    - **Property 9: Multi-URL output array length equals input count**
    - Generate lists of N URLs with `st.lists(st.from_regex(r'https://www\\.moneycontrol\\.com/\\S+'), min_size=1)`; mock `HTTPClient.fetch` and `ArticleParser.parse` to succeed for all
    - Assert the serialised output is a JSON array of exactly N records, each with a matching `url` field
    - **Validates: Requirements 5.3, 6.2**

  - [x]* 7.4 Write property test for per-URL error recovery (Property 10)
    - **Property 10: Per-URL error recovery preserves successful records**
    - Generate mixed lists of URLs where a random subset raises `ScraperFetchError` (mocked); the rest succeed
    - Assert the output array contains exactly one record per successful URL and no record for failed URLs
    - **Validates: Requirements 6.3**

  - [x]* 7.5 Write unit tests for `cli` integration examples and edge cases
    - Test no URLs and no `--file` → `SystemExit(1)` and usage message printed
    - Test unreadable `--file` → `SystemExit(1)` and error message printed
    - Test unwritable `--output` → `SystemExit(1)`
    - Test `--delay` causes `time.sleep` to be called with the configured value between requests
    - _Requirements: 6.4, 7.1, 7.4, 7.5, 5.6_

- [x] 8. Final checkpoint — Ensure all tests pass
  - Run `pytest tests/ -v` and confirm the full suite passes; ask the user if questions arise.

---

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP
- Each task references specific requirements for traceability
- Checkpoints (tasks 4 and 8) ensure incremental validation before moving to the next phase
- Property tests use `@settings(max_examples=100)` minimum and are tagged with the comment format: `# Feature: moneycontrol-stocks-scraper, Property {N}: {property_text}`
- Unit tests and property tests are complementary — both are needed for full coverage
- The `OutputRecord` dataclass and exceptions live in `models.py` and `exceptions.py` respectively, imported by all other modules

---

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["2.1", "3.1"] },
    { "id": 1, "tasks": ["2.2", "2.3", "3.2", "3.3", "3.4", "3.5", "3.6"] },
    { "id": 2, "tasks": ["3.7", "3.8", "3.9", "5.1", "6.1"] },
    { "id": 3, "tasks": ["3.10", "5.2", "5.3", "6.2", "6.3"] },
    { "id": 4, "tasks": ["5.4", "5.5", "7.1"] },
    { "id": 5, "tasks": ["5.6", "7.2"] },
    { "id": 6, "tasks": ["7.3", "7.4", "7.5"] }
  ]
}
```
