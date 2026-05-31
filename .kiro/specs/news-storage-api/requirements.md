# Requirements Document

## Introduction

This feature extends the `moneycontrol-stocks-scraper` project (Phase 2) by adding two capabilities:

1. **Database storage**: The scraper's JSON output can be persisted into a pre-existing PostgreSQL table (`stoxscoop_dev.news_staging`) in addition to, or instead of, writing to a file. A configuration flag controls which output mode is active.

2. **REST API**: A FastAPI application running on port 8005 exposes HTTP endpoints to trigger scraping, store results, and query stored news records from the database.

The existing scraper behaviour (file output, CLI) is preserved unchanged when `output_mode: file` is set.

---

## Glossary

- **API**: The FastAPI HTTP application that exposes endpoints for triggering scraping and reading news records.
- **Config**: The `config.yaml` file that controls scraper and API behaviour, including output mode and database connection settings.
- **DB_Writer**: The component responsible for inserting `News_Record` objects into the PostgreSQL `news_staging` table.
- **File_Writer**: The existing component that writes JSON output to a timestamped file in `output_dir`.
- **News_Record**: A single row in the `news_staging` table, representing one scraped article mapped to the database schema.
- **Output_Mode**: The value of the `output_mode` key in `config.yaml`; one of `file`, `database`, or `both`.
- **Output_Record**: The structured JSON object produced by the existing scraper for a single article (date, url, sections).
- **Scraper**: The existing Python component that fetches and parses MoneyControl articles into `Output_Record` objects.
- **Stock_List**: The JSON array of stock name strings extracted from all sections of an `Output_Record`, stored in the `extracted_stocks` column.
- **Title**: The article title derived from page metadata (`<title>` tag or Open Graph `og:title`) or, as a fallback, the URL slug of the article.

---

## Requirements

### Requirement 1: Configuration — Output Mode and Database Settings

**User Story:** As a developer, I want to control where scraper output is sent via a single config flag, so that I can switch between file output, database storage, or both without changing code.

#### Acceptance Criteria

1. THE Config SHALL support an `output_mode` key with exactly three valid values: `file`, `database`, and `both`; comparison SHALL be case-insensitive.
2. WHEN `output_mode` is `file` (case-insensitive), THE Scraper SHALL write JSON output to the directory specified by the `output_dir` config key using the existing `File_Writer` behaviour and SHALL NOT attempt any database connection or insertion.
3. WHEN `output_mode` is `database` (case-insensitive), THE Scraper SHALL insert records into the PostgreSQL database using the `DB_Writer` and SHALL NOT write any JSON output file.
4. WHEN `output_mode` is `both` (case-insensitive), THE Scraper SHALL write JSON output to the file system AND insert records into the PostgreSQL database.
5. IF `output_mode` is absent from `config.yaml`, THEN THE Config SHALL default to `file` to preserve backward compatibility.
6. IF `output_mode` contains a value other than `file`, `database`, or `both` (case-insensitive), THEN THE Scraper SHALL log an error describing the invalid value and exit with a non-zero status code.
7. THE Config SHALL support a `database` section containing the following keys: `host` (string), `port` (integer between 1 and 65535), `dbname` (string), `user` (string), `password` (string), and `schema` (string).
8. WHEN `output_mode` is `database` or `both` and any required database key (`host`, `port`, `dbname`, `user`, `password`, `schema`) is absent from the `database` section of `config.yaml`, THEN THE Scraper SHALL log an error identifying the missing key and exit with a non-zero status code.
9. WHEN `output_mode` is `both` and the file write succeeds but the database insertion fails, THE Scraper SHALL log the database error and continue; WHEN the database insertion succeeds but the file write fails, THE Scraper SHALL log the file error and continue; in both cases THE Scraper SHALL exit with a non-zero status code after processing all URLs.

---

### Requirement 2: Map Output Records to Database Rows

**User Story:** As a developer, I want each scraped `Output_Record` to be mapped to a `News_Record` that matches the `news_staging` table schema, so that the data is stored in a consistent and queryable format.

#### Acceptance Criteria

1. THE DB_Writer SHALL map the `url` field of an `Output_Record` directly to the `url` column of `news_staging`.
2. THE DB_Writer SHALL set the `source` column to the hardcoded string `"moneycontrol"` for every inserted record.
3. THE DB_Writer SHALL derive the `title` column by first checking the `og:title` Open Graph meta tag, then the `<title>` HTML element; IF neither is found in page metadata, THEN THE DB_Writer SHALL derive the title from the last non-empty path segment of the URL by replacing hyphens with spaces and trimming leading and trailing whitespace, without any further transformation of special characters, numbers, or file extensions.
4. THE DB_Writer SHALL map the `date` field of an `Output_Record` to the `published_date` column as a timezone-aware `datetime` object with `tzinfo=UTC`; WHEN the `date` field is a `YYYY-MM-DD` string, THE DB_Writer SHALL interpret it as midnight UTC (`00:00:00+00:00`).
5. IF the `date` field of an `Output_Record` is `null` or contains a value that cannot be parsed as a valid date, THEN THE DB_Writer SHALL set `published_date` to `null` in the inserted row without rejecting the record.
6. THE DB_Writer SHALL serialize the entire `sections` object of an `Output_Record` to a JSON string and store it in the `content` column; WHEN `sections` is an empty dict `{}`, THE DB_Writer SHALL store the JSON representation of an empty object (`'{}'`).
7. THE DB_Writer SHALL extract all non-empty-string stock names from all sections of an `Output_Record` — the union of all non-empty keys across all section dictionaries — deduplicate them, and store the result as a JSONB array in the `extracted_stocks` column; WHEN sections is empty or yields no non-empty keys, THE DB_Writer SHALL store `'[]'`.
8. THE DB_Writer SHALL set `is_loaded` to `false` for every newly inserted record.
9. THE DB_Writer SHALL set `created_at` to the current timestamp by relying on the column's `DEFAULT now()` constraint and SHALL NOT supply an explicit value for `created_at` in the INSERT statement.

---

### Requirement 3: Handle Duplicate URLs on Insert

**User Story:** As a developer, I want the scraper to skip articles that are already in the database, so that re-running the scraper on the same URLs does not produce duplicate rows or errors.

#### Acceptance Criteria

1. WHEN the DB_Writer attempts to insert a `News_Record` whose `url` already exists in `news_staging`, THE DB_Writer SHALL skip the insertion without raising an error.
2. WHEN one or more URLs are skipped due to duplicate detection, THE DB_Writer SHALL log an informational message for each skipped URL that includes the URL value.
3. WHEN the DB_Writer finishes processing a batch of `Output_Record` objects, THE DB_Writer SHALL emit a log message reporting the count of successfully inserted rows and the count of skipped duplicate rows.

---

### Requirement 4: Database Connection Management

**User Story:** As a developer, I want the application to manage database connections reliably, so that connections are not leaked and the application recovers gracefully from transient failures.

#### Acceptance Criteria

1. THE DB_Writer SHALL use a connection pool to manage PostgreSQL connections, with a configurable minimum pool size (integer between 1 and 10, inclusive) and a configurable maximum pool size (integer between 1 and 100, inclusive, and greater than or equal to the minimum pool size).
2. WHEN a database connection cannot be established at startup, THEN THE DB_Writer SHALL raise a descriptive error that includes the parameter name (one of: `host`, `port`, `dbname`, `user`) and its configured value, and SHALL NOT proceed with any insert operations.
3. WHEN a database error occurs during an INSERT operation, THEN THE DB_Writer SHALL roll back the affected transaction, log the error with the URL of the affected record, and continue processing remaining records.
4. WHEN the application receives a shutdown signal, THE DB_Writer SHALL close all database connections within 30 seconds; IF connections cannot be closed within 30 seconds, THE DB_Writer SHALL forcibly terminate them.
5. WHEN a transient database error occurs during an INSERT operation (e.g., connection reset, temporary unavailability), THE DB_Writer SHALL retry the operation up to 3 times with a 1-second delay between attempts before logging the error and continuing to the next record.

---

### Requirement 5: FastAPI Application Startup

**User Story:** As a developer, I want the FastAPI application to start on a fixed port and be accessible over the network, so that other services and clients can call its endpoints.

#### Acceptance Criteria

1. THE API SHALL be startable with the command `uvicorn app.main:app --reload --host 0.0.0.0 --port 8005`.
2. THE API SHALL expose a health-check endpoint at `GET /health` that returns HTTP 200 with a JSON body `{"status": "ok"}` when the application is running.
3. WHEN the `GET /health` endpoint is called and the database is unreachable due to connection refused, DNS resolution failure, or authentication error, THE API SHALL return HTTP 503 with a JSON body `{"status": "degraded", "detail": "<reason>"}`. THE API SHALL only check database connectivity when the health endpoint is called and SHALL NOT proactively monitor connectivity between requests.
4. WHEN the `GET /health` endpoint is called and the database connectivity check does not complete within 5 seconds, THE API SHALL return HTTP 200 with a JSON body `{"status": "ok"}` rather than treating the incomplete check as a confirmed failure.
5. THE API SHALL read its database connection settings from the `database` section of `config.yaml`, specifically the keys: `host`, `port`, `dbname`, `user`, `password`, and `schema`.

---

### Requirement 6: Trigger Scraping via API

**User Story:** As a developer, I want to trigger scraping of one or more MoneyControl URLs through the API, so that I can initiate data collection programmatically without using the CLI.

#### Acceptance Criteria

1. THE API SHALL expose a `POST /scrape` endpoint that accepts a JSON request body containing a list of one or more and at most 50 MoneyControl article URLs.
2. WHEN a valid `POST /scrape` request is received, THE API SHALL invoke the Scraper for each provided URL, map each resulting `Output_Record` to a `News_Record`, and persist the records according to the output destination configured in `config.yaml`.
3. WHEN `POST /scrape` completes, THE API SHALL return HTTP 200 with a JSON response body containing: the count of URLs processed, the count of records inserted, the count of records skipped because a record with the same URL already exists in the output destination, and a list of failed URLs with their error reasons (empty list if none failed).
4. IF the request body contains no URLs or an empty list, THEN THE API SHALL return HTTP 422 with a JSON response body containing a validation error message indicating that at least one URL is required.
5. IF a URL in the request body is not a valid HTTP or HTTPS URL, THEN THE API SHALL return HTTP 422 with a JSON response body containing a validation error message identifying the invalid URL.
6. WHEN one or more URLs fail to scrape due to network error, timeout, or non-200 HTTP response, THE API SHALL include the failed URLs and their error reasons in the response body's failures list and SHALL still return HTTP 200 if at least one URL succeeded.
7. IF all URLs in the request fail to scrape, THEN THE API SHALL return HTTP 502 with a JSON body listing each failed URL and its error reason.

---

### Requirement 7: List News Records

**User Story:** As a developer, I want to retrieve a paginated list of news records from the database with optional filters, so that I can browse and search stored articles.

#### Acceptance Criteria

1. THE API SHALL expose a `GET /news` endpoint that returns a paginated list of `News_Record` objects from the `news_staging` table, ordered by `created_at` descending.
2. THE API SHALL support a `date` query parameter (format `YYYY-MM-DD`) that filters results to records whose `published_date` falls on that calendar date in UTC (i.e., `published_date >= date 00:00:00 UTC` AND `published_date < date+1 00:00:00 UTC`).
3. THE API SHALL support a `stock` query parameter (a stock name string) that filters results to records whose `extracted_stocks` JSONB array contains the provided stock name using a case-insensitive match.
4. THE API SHALL support a `source` query parameter that filters results to records whose `source` column matches the given value using a case-insensitive exact match.
5. THE API SHALL support `page` (default `1`, minimum `1`) and `page_size` (default `20`, minimum `1`, maximum `100`) query parameters for pagination.
6. WHEN a `GET /news` request is received, THE API SHALL return HTTP 200 with a JSON response body containing: `items` (list of matching `News_Record` objects), `total` (integer count of all matching records before pagination), `page` (current page number), and `page_size` (number of items per page).
7. WHEN no records match the applied filters, THE API SHALL return HTTP 200 with `items: []` and `total: 0`. WHEN records exist but the requested page number exceeds the available pages, THE API SHALL return HTTP 200 with `items: []` and `total` set to the actual count of matching records.
8. IF the `date` query parameter is provided but does not match the `YYYY-MM-DD` format, THEN THE API SHALL return HTTP 422 with a JSON response body containing a validation error message indicating the expected format.
9. IF `page` or `page_size` values are outside their allowed ranges, THEN THE API SHALL return HTTP 422 with a JSON response body containing a validation error message indicating the allowed range.

---

### Requirement 8: Get a Single News Record

**User Story:** As a developer, I want to retrieve a single news record by its database ID or by its URL, so that I can look up a specific article's stored data.

#### Acceptance Criteria

1. THE API SHALL expose a `GET /news/{id}` endpoint that accepts a positive integer `id` path parameter and returns the `News_Record` with that `id`.
2. WHEN a `GET /news/{id}` request is received and a record with that `id` exists, THE API SHALL return HTTP 200 with all fields of the `News_Record` as a JSON object.
3. WHEN a `GET /news/{id}` request is received and no record with that `id` exists, THE API SHALL return HTTP 404 with a JSON body indicating the record was not found.
4. THE API SHALL expose a `GET /news/by-url` endpoint that accepts a `url` query parameter and returns the `News_Record` whose `url` column matches the provided value exactly.
5. WHEN a `GET /news/by-url` request is received and a matching record exists, THE API SHALL return HTTP 200 with all fields of the `News_Record` as a JSON object.
6. WHEN a `GET /news/by-url` request is received and no record with that URL exists, THE API SHALL return HTTP 404 with a JSON body indicating the record was not found.
7. IF the `id` path parameter in `GET /news/{id}` is not a valid positive integer, THEN THE API SHALL return HTTP 422 with a JSON response body containing a validation error message indicating the expected format, without performing a database lookup.
8. IF the `url` query parameter is absent from a `GET /news/by-url` request, THEN THE API SHALL return HTTP 422 with a JSON response body containing a validation error message indicating that the `url` parameter is required.

---

### Requirement 9: API Response Schema for News Records

**User Story:** As a developer, I want the API to return news records in a consistent, well-typed JSON schema, so that clients can reliably deserialise and use the data.

#### Acceptance Criteria

1. THE API SHALL return each `News_Record` as a JSON object with exactly the following fields and no additional top-level fields: `id` (integer), `url` (string), `source` (string), `title` (string), `published_date` (ISO 8601 datetime string with UTC timezone offset, or `null`), `content` (JSON object, or `null` when unavailable), `extracted_stocks` (JSON array of strings, never `null`; use `[]` when empty), `created_at` (ISO 8601 datetime string with UTC timezone offset), and `is_loaded` (boolean).
2. THE API SHALL return `published_date` and `created_at` as ISO 8601 strings that include a UTC timezone offset (e.g., `"2026-05-08T00:00:00+00:00"`); naive datetime strings without a timezone offset SHALL NOT be returned.
3. THE API SHALL return `content` as a parsed JSON object in all API responses; WHEN `content` is unavailable, THE API SHALL return `null` rather than an empty object or a raw string.
4. THE API SHALL return `extracted_stocks` as a JSON array of strings in all API responses; WHEN no stocks are present, THE API SHALL return `[]` rather than `null`.

---

### Requirement 10: Round-Trip Integrity of Stored Content

**User Story:** As a developer, I want the `sections` data stored in the database to be losslessly retrievable, so that the API returns exactly the same structure that the scraper produced.

#### Acceptance Criteria

1. WHEN an `Output_Record`'s `sections` object is serialised to a JSON string and stored in the `content` column as a TEXT value, and subsequently retrieved and deserialised by the API, THE resulting object SHALL have the same keys, the same nested keys, and the same string values as the original `sections` object, regardless of key ordering differences introduced by JSON parsing.
2. WHEN the `extracted_stocks` array is stored as JSONB and retrieved by the API, THE resulting array SHALL contain the same stock name strings as were inserted, with no additions, removals, or modifications; the order of elements in the retrieved array MAY differ from the insertion order.
