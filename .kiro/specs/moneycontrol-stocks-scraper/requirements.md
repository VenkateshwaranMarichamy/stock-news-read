# Requirements Document

## Introduction

This feature implements a web scraper for MoneyControl "Stocks to Watch" morning news articles. The scraper fetches daily articles from MoneyControl's markets section, parses the structured content into sections (e.g., "Stocks to Watch", "Bulk Deals"), and extracts per-stock news text. The output is a structured JSON document keyed by section and stock name, suitable for downstream analysis or storage.

## Glossary

- **Scraper**: The Python program responsible for fetching and parsing MoneyControl article pages.
- **Article**: A MoneyControl "Stocks to Watch" news page published each morning, identified by a URL.
- **Section**: A named grouping within an article (e.g., "Stocks to Watch", "Bulk Deals", "Bulk and Block Deals").
- **Stock_Entry**: A named stock within a section, accompanied by one or more paragraphs of news text.
- **Output_Record**: The structured JSON object produced for a single article, containing the date, URL, and all parsed sections with their stock entries.
- **URL_List**: A collection of one or more article URLs provided as input to the Scraper.
- **HTML_Parser**: The component responsible for interpreting the raw HTML of an article page and extracting sections and stock entries.
- **HTTP_Client**: The component responsible for making HTTP requests to MoneyControl and returning the raw HTML response.

---

## Requirements

### Requirement 1: Fetch Article HTML

**User Story:** As a data analyst, I want the scraper to retrieve the full HTML content of a MoneyControl article page, so that I can parse its structured content.

#### Acceptance Criteria

1. WHEN a valid MoneyControl article URL (an HTTP or HTTPS URL whose host is moneycontrol.com) is provided, THE HTTP_Client SHALL fetch the page and return the complete response body string as the HTML content.
2. WHEN the HTTP response status code is not 200, THEN THE HTTP_Client SHALL raise a descriptive error indicating the URL and the status code received.
3. WHEN a network error or timeout occurs during fetching (with a maximum timeout of 30 seconds), THEN THE HTTP_Client SHALL raise a descriptive error indicating the URL and the nature of the failure, regardless of whether an HTTP status code was received.
4. THE HTTP_Client SHALL include a non-empty User-Agent header string identifying a known browser (e.g., containing "Mozilla") in every request.

---

### Requirement 2: Parse Article Sections

**User Story:** As a data analyst, I want the scraper to identify and extract all named sections from an article, so that stock entries can be grouped correctly.

#### Acceptance Criteria

1. WHEN the HTML content of an article is provided, THE HTML_Parser SHALL identify all heading-level elements (h1–h6 or equivalent bold/strong block elements) within the article's primary content container and treat each one whose extracted text is non-empty as a section heading.
2. THE HTML_Parser SHALL use the extracted heading text (trimmed of leading and trailing whitespace) as the section key in the Output_Record. IF the extracted heading text is empty or whitespace-only after trimming, THEN THE HTML_Parser SHALL omit that heading from the Output_Record entirely.
3. WHEN an article contains no heading-level elements within the primary content container, THEN THE HTML_Parser SHALL return an Output_Record with an empty sections object and SHALL log a warning message that includes the article URL.
4. THE HTML_Parser SHALL accept any non-empty heading-level element text as a valid section key without filtering by a predefined list of known section names.

---

### Requirement 3: Extract Stock Entries Within Sections

**User Story:** As a data analyst, I want the scraper to extract each stock name and its associated news text within a section, so that I can look up news by stock name.

#### Acceptance Criteria

1. WHEN a section is identified, THE HTML_Parser SHALL extract each Stock_Entry within that section; WHERE both a hyperlink and a sub-heading are present for the same entry, THE HTML_Parser SHALL use the hyperlink text as the stock name key.
2. THE HTML_Parser SHALL concatenate all paragraph elements appearing after a stock name heading and before the next stock name heading or section boundary into a single string value, separated by a single space.
3. WHEN a stock name appears more than once within the same section, THE HTML_Parser SHALL append subsequent paragraph text to the existing entry's value rather than overwriting it.
4. THE HTML_Parser SHALL strip leading and trailing whitespace from both stock name keys and news text values.
5. WHEN a section contains no Stock_Entry items (including sections whose content consists only of whitespace or HTML comments), THE HTML_Parser SHALL represent that section as an empty object `{}` in the Output_Record.
6. WHEN a stock name heading has no associated paragraph elements before the next heading or section boundary, THE HTML_Parser SHALL include that stock name in the Output_Record with an empty string as its value.

---

### Requirement 4: Extract Article Date

**User Story:** As a data analyst, I want the Output_Record to include the publication date of the article, so that I can identify which trading day the data belongs to.

#### Acceptance Criteria

1. WHEN an article page is parsed, THE HTML_Parser SHALL extract the publication date from page metadata (e.g., `<meta>` tags, JSON-LD, or a visible date element) and include it in the Output_Record as a string in ISO 8601 format (YYYY-MM-DD).
2. WHEN the publication date metadata field is absent or its value does not conform to a parseable date format, THE HTML_Parser SHALL attempt to extract the date from the article URL by matching patterns of the form `YYYY/MM/DD` or `YYYY-MM-DD` within the URL path.
3. IF neither the page metadata nor the URL contains a matching date pattern, THEN THE HTML_Parser SHALL set the date field to `null`, SHALL include the Output_Record in the output without rejection, and SHALL log a warning message that includes the article URL.

---

### Requirement 5: Produce Structured JSON Output

**User Story:** As a data analyst, I want each scraped article to produce a well-formed JSON record, so that I can consume the data programmatically.

#### Acceptance Criteria

1. THE Scraper SHALL produce one Output_Record per article URL, conforming to the following structure (where `date` is `null` when not found, and `sections` is `{}` when no sections are parsed):
   ```json
   {
     "date": "<YYYY-MM-DD or null>",
     "url": "<article URL>",
     "sections": {
       "<Section Title>": {
         "<Stock Name>": "<news text>"
       }
     }
   }
   ```
2. THE Scraper SHALL serialise each Output_Record as valid JSON with UTF-8 encoding.
3. WHEN multiple URLs are provided, THE Scraper SHALL produce one Output_Record per URL and SHALL collect all records into a JSON array.
4. WHEN an output file path is specified, THE Scraper SHALL write the JSON output to that file and SHALL NOT write JSON output to standard output.
5. IF no output file path is specified, THEN THE Scraper SHALL print the JSON output to standard output.
6. IF the specified output file path is not writable at the time of writing the final output, THEN THE Scraper SHALL log an error and exit with a non-zero status code without writing any partial output.

---

### Requirement 6: Process Multiple URLs

**User Story:** As a data analyst, I want to provide a list of article URLs in a single invocation, so that I can scrape multiple days' articles without running the tool repeatedly.

#### Acceptance Criteria

1. THE Scraper SHALL accept a URL_List as input, containing one or more MoneyControl article URLs.
2. WHEN processing a URL_List, THE Scraper SHALL process each URL sequentially and collect all Output_Records into a single result set.
3. WHEN one URL in the URL_List fails to fetch or parse, THE Scraper SHALL log the error for that URL and SHALL continue processing the remaining URLs without aborting.
4. THE Scraper SHALL introduce a configurable delay between consecutive HTTP requests, defaulting to 1 second, to avoid overloading the server.

---

### Requirement 7: Command-Line Interface

**User Story:** As a data analyst, I want to invoke the scraper from the command line with URLs or a file of URLs, so that I can integrate it into scripts and workflows.

#### Acceptance Criteria

1. THE Scraper SHALL provide a command-line interface that accepts one or more article URLs as positional arguments.
2. WHERE a `--file` option is provided, THE Scraper SHALL read URLs from the specified plain-text file, one URL per line, ignoring blank lines and lines beginning with `#`.
3. WHERE a `--output` option is provided with a valid writable file path, THE Scraper SHALL write the JSON output exclusively to that file and SHALL NOT write JSON output to standard output.
4. IF the command-line interface is invoked with no URLs and no `--file` option, THEN THE Scraper SHALL print a usage message describing available arguments and options, and exit with a non-zero status code after allowing any already-completed operations to finish.
5. IF the `--file` option is provided but the specified file cannot be read, THEN THE Scraper SHALL print an error message indicating the file path and exit with a non-zero status code.

---

### Requirement 8: Round-Trip Serialisation Integrity

**User Story:** As a data analyst, I want the JSON output to be losslessly re-parseable, so that downstream tools can reliably consume the data.

#### Acceptance Criteria

1. WHEN a valid Output_Record is serialised to a JSON string and that string is subsequently parsed and re-serialised, THE resulting JSON string SHALL contain the same keys, the same values, and the same value types as the original serialisation, regardless of key ordering or whitespace differences.
2. THE Scraper SHALL replace any Unicode surrogate code points (U+D800–U+DFFF) and null bytes (U+0000) in string values with the Unicode replacement character (U+FFFD) before serialisation.
3. IF a string value still cannot be encoded as valid UTF-8 after replacement, THEN THE Scraper SHALL substitute an empty string for that value and SHALL log a warning identifying the affected field and article URL.
