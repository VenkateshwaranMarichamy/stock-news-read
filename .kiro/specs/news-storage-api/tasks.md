# Implementation Plan: news-storage-api

## Overview

Extend the existing `moneycontrol_scraper/` package with database storage and a FastAPI REST API. The implementation adds a new `app/` package alongside the existing scraper, extends `config.py` with output mode routing, and wires everything together through the CLI and API layer.

## Tasks

- [x] 1. Extend config and update dependencies
  - [x] 1.1 Add `DatabaseConfig` dataclass and extend `ScraperConfig` in `moneycontrol_scraper/config.py`
    - Add `DatabaseConfig` dataclass with fields: `host`, `port`, `dbname`, `user`, `password`, `schema`, `min_pool_size=2`, `max_pool_size=10`
    - Add `output_mode: str = "file"` and `database: DatabaseConfig | None = None` fields to `ScraperConfig`
    - _Requirements: 1.1, 1.5, 1.7_

  - [x] 1.2 Add validation logic to `load_config()` in `moneycontrol_scraper/config.py`
    - Normalise `output_mode` to lowercase after reading from YAML
    - If `output_mode` not in `{"file", "database", "both"}`, log error with invalid value and call `sys.exit(1)`
    - When `output_mode` is `"database"` or `"both"`, check all six required DB keys; if any missing, log error with key name and call `sys.exit(1)`
    - Construct `DatabaseConfig` from the `database` section when present
    - _Requirements: 1.1, 1.5, 1.6, 1.7, 1.8_

  - [x] 1.3 Update `pyproject.toml` to add Phase 2 dependencies
    - Add to `dependencies`: `fastapi>=0.111.0`, `uvicorn[standard]>=0.29.0`, `asyncpg>=0.29.0`, `pydantic>=2.0.0`
    - Add to `dev` optional-dependencies: `pytest-asyncio>=0.23.0`, `httpx>=0.27.0`
    - _Requirements: 5.1_

  - [ ]* 1.4 Write property test for `output_mode` routing (Property 1)
    - File: `tests/test_app_config.py`
    - Use `st.sampled_from(["file", "FILE", "File", "database", "DATABASE", "both", "BOTH"])` to generate valid modes
    - Assert that after `load_config()` normalisation, `output_mode` is always one of `{"file", "database", "both"}`
    - Use `st.text().filter(lambda s: s.lower() not in {"file","database","both"})` for invalid modes; assert `sys.exit(1)` is called
    - `# Feature: news-storage-api, Property 1: output_mode routing`
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [ ]* 1.5 Write unit tests for config validation edge cases
    - File: `tests/test_app_config.py`
    - Test: invalid `output_mode` value → `sys.exit(1)` (use `pytest.raises(SystemExit)`)
    - Test: `output_mode: database` with missing DB key → `sys.exit(1)` with key name in log
    - Test: absent `output_mode` key → defaults to `"file"`
    - _Requirements: 1.5, 1.6, 1.8_

- [x] 2. Create `app/` package structure and Pydantic schemas
  - [x] 2.1 Create `app/` package skeleton with `__init__.py` files
    - Create `app/__init__.py`, `app/db/__init__.py`, `app/routers/__init__.py`
    - _Requirements: 5.1_

  - [x] 2.2 Implement `app/schemas.py` with all Pydantic models
    - Implement `NewsRecordSchema` with all nine fields; use `model_config = ConfigDict(from_attributes=True)`
    - Implement `ScrapeRequest` with `urls: list[HttpUrl]` and `@field_validator` enforcing 1–50 items
    - Implement `ScrapeResponse` with `processed`, `inserted`, `skipped`, `failures` fields
    - Implement `NewsListResponse` with `items`, `total`, `page`, `page_size` fields
    - _Requirements: 6.1, 6.3, 9.1, 9.2, 9.3, 9.4_

  - [ ]* 2.3 Write property test for response schema completeness (Property 8)
    - File: `tests/test_app_config.py` (or a new `tests/test_schemas.py`)
    - Build `NewsRecordSchema` instances from composite Hypothesis strategies
    - Assert the serialised JSON contains exactly the nine required fields and no extras
    - Assert `published_date` and `created_at` are ISO 8601 strings with UTC offset when not null
    - Assert `extracted_stocks` is always a list, never null
    - `# Feature: news-storage-api, Property 8: response schema completeness`
    - _Requirements: 9.1, 9.2, 9.3, 9.4_

- [x] 3. Implement database connection pool
  - [x] 3.1 Implement `app/db/pool.py`
    - Implement `create_pool(db_config: DatabaseConfig) -> asyncpg.Pool` — creates pool with `min_size`/`max_size` from config; raises `RuntimeError` with param name + value on connection failure
    - Implement `close_pool(pool: asyncpg.Pool, timeout: float = 30.0) -> None` — closes pool gracefully; forcibly terminates connections if timeout exceeded
    - _Requirements: 4.1, 4.2, 4.4_

- [x] 4. Implement `DB_Writer` and insert queries
  - [x] 4.1 Implement `app/db/writer.py` — `DB_Writer` class
    - Implement `__init__(self, pool, schema)` storing pool and schema
    - Implement `_extract_title(record)`: priority order og:title → `<title>` → URL last path segment (hyphens→spaces, trimmed)
    - Implement `_extract_stocks(sections)`: union of all non-empty keys across all section dicts, deduplicated; return `[]` when empty
    - Implement `_parse_published_date(date_str)`: parse `YYYY-MM-DD` → midnight UTC `datetime`; return `None` for null or unparseable input
    - Implement `map_record(record)`: return dict with keys `url`, `source="moneycontrol"`, `title`, `published_date`, `content` (json.dumps), `extracted_stocks`, `is_loaded=False`; no `created_at` key
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9_

  - [x] 4.2 Implement `write_batch()` in `app/db/writer.py`
    - Use `INSERT INTO {schema}.news_staging (...) VALUES (...) ON CONFLICT (url) DO NOTHING`
    - Retry up to 3 times with `asyncio.sleep(1)` on `asyncpg.PostgresConnectionError` or `asyncpg.TooManyConnectionsError`
    - On permanent error: rollback transaction, log error with URL, continue to next record
    - On duplicate skip: log INFO with URL, increment skipped count
    - Return `(inserted_count, skipped_count)`
    - _Requirements: 2.9, 3.1, 3.2, 3.3, 4.3, 4.5_

  - [ ]* 4.3 Write property test for DB field mapping invariants (Property 2)
    - File: `tests/test_db_writer.py`
    - Generate `OutputRecord` instances with arbitrary `url`, `date` (valid/null/invalid), `sections`
    - Assert `map_record()` always produces `source == "moneycontrol"`, `is_loaded == False`, no `"created_at"` key
    - Assert `published_date` is midnight UTC when date is valid `YYYY-MM-DD`, else `None`
    - `# Feature: news-storage-api, Property 2: DB field mapping invariants`
    - _Requirements: 2.1, 2.2, 2.4, 2.5, 2.8, 2.9_

  - [ ]* 4.4 Write property test for `extracted_stocks` deduplication (Property 3)
    - File: `tests/test_db_writer.py`
    - Use `st.dictionaries(st.text(), st.dictionaries(st.text(), st.text()))` for sections
    - Assert result contains no duplicates, no empty strings, and only keys present in original sections
    - `# Feature: news-storage-api, Property 3: extracted_stocks deduplication`
    - _Requirements: 2.7_

  - [ ]* 4.5 Write property test for duplicate URL skip (Property 4)
    - File: `tests/test_db_writer.py`
    - Mock asyncpg pool; pre-populate a set of "existing" URLs
    - Assert `write_batch()` returns `(len(new_records), len(duplicate_records))` correctly
    - `# Feature: news-storage-api, Property 4: duplicate URL skip`
    - _Requirements: 3.1, 3.3_

  - [ ]* 4.6 Write unit tests for `DB_Writer` edge cases
    - File: `tests/test_db_writer.py`
    - Test title extraction priority: og:title wins over `<title>`, `<title>` wins over URL slug, URL slug fallback strips hyphens
    - Test transient retry: `PostgresConnectionError` triggers 3 retries then logs and continues
    - Test permanent error: rollback called, URL logged, next record processed
    - Test duplicate skip: INFO log emitted with URL
    - _Requirements: 2.3, 3.2, 4.3, 4.5_

- [x] 5. Implement read queries
  - [x] 5.1 Implement `app/db/queries.py`
    - Implement `fetch_news_list(pool, schema, *, date, stock, source, page, page_size)` → `(list[dict], int)`
      - Date filter: `published_date >= midnight UTC` AND `< midnight UTC + 1 day`
      - Stock filter: `extracted_stocks @> to_jsonb(lower($1))`
      - Source filter: `ILIKE`
      - Order: `created_at DESC`; pagination: `LIMIT page_size OFFSET (page-1)*page_size`
    - Implement `fetch_news_by_id(pool, schema, record_id)` → `dict | None`
    - Implement `fetch_news_by_url(pool, schema, url)` → `dict | None`
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 8.1, 8.4_

  - [ ]* 5.2 Write property test for pagination correctness (Property 6)
    - File: `tests/test_db_queries.py`
    - Mock pool returning N records; generate `page` in `[1,10]` and `page_size` in `[1,100]`
    - Assert `len(items) <= page_size`, `total == N`, items ordered by `created_at` DESC
    - `# Feature: news-storage-api, Property 6: pagination correctness`
    - _Requirements: 7.1, 7.5, 7.6_

  - [ ]* 5.3 Write property test for date filter UTC boundary (Property 7)
    - File: `tests/test_db_queries.py`
    - Generate arbitrary `st.dates()` as filter; generate records with `published_date` spanning the boundary
    - Assert only records within `[midnight UTC, midnight UTC + 1 day)` are returned
    - `# Feature: news-storage-api, Property 7: date filter UTC boundary`
    - _Requirements: 7.2_

- [x] 6. Checkpoint — core data layer complete
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Implement content round-trip serialisation test
  - [ ]* 7.1 Write property test for content round-trip (Property 5)
    - File: `tests/test_serialisation.py`
    - Use `st.dictionaries(st.text(), st.dictionaries(st.text(), st.text()))` for sections
    - Assert `json.loads(json.dumps(sections))` has identical keys, nested keys, and string values
    - `# Feature: news-storage-api, Property 5: content round-trip`
    - _Requirements: 2.6, 10.1_

- [x] 8. Implement FastAPI application and routers
  - [x] 8.1 Implement `app/main.py` — FastAPI app with lifespan
    - Create `lifespan` async context manager: startup calls `create_pool(config.database)` and stores on `app.state.pool`; shutdown calls `close_pool(app.state.pool)`
    - Instantiate `FastAPI(lifespan=lifespan)` and include `health_router`, `scrape_router`, `news_router`
    - Load config at module level using `load_config()`
    - _Requirements: 5.1, 5.5_

  - [x] 8.2 Implement `app/routers/health.py` — `GET /health`
    - On-demand DB ping using `asyncio.timeout(5.0)` and `pool.fetchval("SELECT 1")`
    - Return `{"status": "ok"}` (HTTP 200) on success or `asyncio.TimeoutError`
    - Raise `HTTPException(503)` with `{"status": "degraded", "detail": reason}` on connection failure
    - _Requirements: 5.2, 5.3, 5.4_

  - [x] 8.3 Implement `app/routers/scrape.py` — `POST /scrape`
    - Accept `ScrapeRequest`; run scraper synchronously in `loop.run_in_executor(None, _run_scraper, urls)`
    - Route results per `output_mode`: call `File_Writer` for `file`/`both`, call `DB_Writer` for `database`/`both`
    - Return `ScrapeResponse` (HTTP 200) on partial success; return HTTP 502 if all URLs fail
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7_

  - [x] 8.4 Implement `app/routers/news.py` — news endpoints
    - Register `GET /news/by-url` BEFORE `GET /news/{id}` to prevent path parameter collision
    - `GET /news`: call `fetch_news_list()` with all query params; return `NewsListResponse`
    - `GET /news/by-url`: call `fetch_news_by_url()`; return 404 if not found
    - `GET /news/{id}`: call `fetch_news_by_id()`; return 404 if not found
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8_

  - [ ]* 8.5 Write unit tests for health endpoint
    - File: `tests/test_health.py`
    - Test: DB reachable → HTTP 200 `{"status": "ok"}`
    - Test: DB unreachable (mock raises exception) → HTTP 503 `{"status": "degraded", ...}`
    - Test: DB check times out (mock `asyncio.TimeoutError`) → HTTP 200 `{"status": "ok"}`
    - _Requirements: 5.2, 5.3, 5.4_

  - [ ]* 8.6 Write integration tests for `POST /scrape`
    - File: `tests/test_scrape_endpoint.py`
    - Use `httpx.AsyncClient` with `ASGITransport`
    - Test: 0 URLs → HTTP 422
    - Test: 51 URLs → HTTP 422
    - Test: invalid URL (not HTTP/HTTPS) → HTTP 422
    - Test: partial failure (some URLs fail) → HTTP 200 with `failures` list populated
    - Test: all URLs fail → HTTP 502
    - _Requirements: 6.1, 6.4, 6.5, 6.6, 6.7_

  - [ ]* 8.7 Write integration tests for `GET /news` endpoints
    - File: `tests/test_news_endpoints.py`
    - Use `httpx.AsyncClient` with mocked DB pool
    - Test: pagination params respected (`page`, `page_size`)
    - Test: `date` filter returns only matching records; invalid date format → HTTP 422
    - Test: `stock` filter case-insensitive; `source` filter case-insensitive
    - Test: `GET /news/{id}` not found → HTTP 404; non-integer id → HTTP 422
    - Test: `GET /news/by-url` not found → HTTP 404; missing `url` param → HTTP 422
    - Test: no matches → HTTP 200 with `items: []` and `total: 0`
    - Test: `page`/`page_size` out of range → HTTP 422
    - _Requirements: 7.2, 7.3, 7.4, 7.7, 7.8, 7.9, 8.3, 8.6, 8.7, 8.8, 9.3, 9.4_

- [x] 9. Extend CLI to support database output
  - [x] 9.1 Extend `moneycontrol_scraper/cli.py` to read `output_mode` and call `DB_Writer`
    - After scraping, check `config.output_mode`
    - For `"database"` or `"both"`: instantiate `DB_Writer` with pool, call `write_batch(records)`
    - For `"file"` or `"both"`: call existing `File_Writer` logic
    - _Requirements: 1.2, 1.3, 1.4_

  - [x] 9.2 Handle partial failure in `both` mode in `moneycontrol_scraper/cli.py`
    - Wrap each destination write in a try/except; log errors independently per destination
    - Track whether either destination failed; exit with non-zero status code if either fails
    - _Requirements: 1.9_

- [x] 10. Final checkpoint — full test suite
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- The design uses Python throughout; all code examples should use Python 3.11+ syntax
- Property tests use Hypothesis with `@settings(max_examples=100)` minimum
- Tag format for property tests: `# Feature: news-storage-api, Property {N}: {property_text}`
- Route ordering in `news.py` is critical: `/news/by-url` must be registered before `/news/{id}`
- The `app/` package is new; `moneycontrol_scraper/` is extended minimally (config + cli only)
- Async tests use `pytest-asyncio` with `httpx.AsyncClient(app=app, base_url="http://test")`

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.3", "2.1"] },
    { "id": 1, "tasks": ["1.2", "2.2", "3.1"] },
    { "id": 2, "tasks": ["1.4", "1.5", "2.3", "4.1"] },
    { "id": 3, "tasks": ["4.2", "5.1"] },
    { "id": 4, "tasks": ["4.3", "4.4", "4.5", "4.6", "5.2", "5.3", "7.1"] },
    { "id": 5, "tasks": ["8.1"] },
    { "id": 6, "tasks": ["8.2", "8.3", "8.4"] },
    { "id": 7, "tasks": ["8.5", "8.6", "8.7", "9.1"] },
    { "id": 8, "tasks": ["9.2"] }
  ]
}
```
