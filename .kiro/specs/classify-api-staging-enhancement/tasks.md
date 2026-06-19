# Implementation Plan: classify-api-staging-enhancement

## Overview

Implement the `POST /classify` endpoint and `news_staging` classification tracking. The work spans a SQL migration, a new `Staging_Updater` DB helper, a new `classify` router, schema additions, orchestrator extension, and updates to the scrape router and DB writer. Tests live in `tests/classify/` using pytest and Hypothesis.

## Tasks

- [x] 1. Add SQL migration for classification-tracking columns
  - Create `migrations/001_add_classification_tracking.sql`
  - Add `event_batch_id integer` (nullable, FK → `event_batches(id)` ON DELETE SET NULL) using `ADD COLUMN IF NOT EXISTS`
  - Add `stocks_loaded integer NOT NULL DEFAULT 0` using `ADD COLUMN IF NOT EXISTS`
  - Add `stocks_failed integer NOT NULL DEFAULT 0` using `ADD COLUMN IF NOT EXISTS`
  - Add `classification_status varchar(20) NOT NULL DEFAULT 'pending'` using `ADD COLUMN IF NOT EXISTS`
  - Add backfill UPDATE for pre-existing rows where `classification_status IS NULL`
  - Add check constraint `chk_classification_status` (values: pending/partial/complete/failed) using `ADD CONSTRAINT IF NOT EXISTS`
  - Add check constraint `chk_stocks_non_negative` (`stocks_loaded >= 0 AND stocks_failed >= 0`) using `ADD CONSTRAINT IF NOT EXISTS`
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8_

- [x] 2. Add `ClassifyRequest` and `ClassifyResponse` schemas to `app/schemas.py`
  - [x] 2.1 Implement `ClassifyRequest` with `ids: list[int]` field and `validate_ids` validator
    - Validator must reject empty lists, lists > 50, and any non-positive integer value
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [ ]* 2.2 Write property test for `ClassifyRequest` validation
    - **Property 1: Valid `ids` lists are accepted; invalid ones are rejected**
    - **Validates: Requirements 1.1, 1.3, 1.4**
    - Create `tests/classify/test_schemas.py`
    - Use `@given(ids=st.lists(st.integers()))` with `@settings(max_examples=200)`
    - Assert validation succeeds iff `1 ≤ len(ids) ≤ 50` and all values `> 0`
    - Tag: `# Feature: classify-api-staging-enhancement, Property 1: Valid ids lists are accepted; invalid ones are rejected`

  - [x] 2.3 Implement `ClassifyResponse` with all seven response fields
    - Fields: `ids_processed`, `ids_missing`, `ids_skipped`, `total_stocks`, `events_inserted`, `events_failed`, `unresolved_stocks`
    - _Requirements: 6.1, 6.2_

- [x] 3. Implement `determine_status` helper and `Staging_Updater` class
  - [x] 3.1 Create `app/db/staging_updater.py` with module-level `determine_status(summary)` pure function
    - Returns `"complete"` if `db_inserted > 0 AND db_failures == 0 AND unresolved == 0`
    - Returns `"partial"` if `db_inserted > 0 AND (db_failures > 0 OR unresolved > 0)`
    - Returns `"failed"` if `db_inserted == 0` (any total) or `total == 0`
    - _Requirements: 5.2, 5.3, 5.4, 5.5_

  - [ ]* 3.2 Write property test for `determine_status`
    - **Property 4: Classification status is a pure function of `PipelineSummary`**
    - **Validates: Requirements 5.2, 5.3, 5.4, 5.5**
    - Create `tests/classify/test_determine_status.py`
    - Use `@given(st.builds(PipelineSummary, ...))` with `@settings(max_examples=500)` over non-negative integers
    - Assert returned status matches specification for all three cases; assert cases are mutually exclusive and exhaustive
    - Tag: `# Feature: classify-api-staging-enhancement, Property 4: Classification status is a pure function of PipelineSummary`

  - [x] 3.3 Implement `Staging_Updater.__init__`, `get_cumulative_loaded`, and `update_classification_status`
    - `get_cumulative_loaded(batch_id, source_url, schema)` — SELECT COUNT(*) from events matching batch_id + source_url
    - `update_classification_status(staging_id, batch_id, summary, url, schema, is_retry)` — compute status via `determine_status`, issue single UPDATE to `news_staging`; on retry (`is_retry=True`), call `get_cumulative_loaded` for `stocks_loaded`
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [ ]* 3.4 Write unit tests for `Staging_Updater`
    - Create `tests/classify/test_staging_updater.py`
    - Mock asyncpg pool/connection; verify correct SQL parameters for each status case (complete, partial, failed)
    - Verify `get_cumulative_loaded` issues the correct SELECT and returns the count
    - Verify retry path calls `get_cumulative_loaded` and uses its result for `stocks_loaded`
    - _Requirements: 5.1, 5.6_

- [x] 4. Checkpoint — Ensure all tests pass, ask the user if questions arise.

- [x] 5. Add `run_with_batch_id` to `Pipeline_Orchestrator`
  - [x] 5.1 Implement `run_with_batch_id(record, batch_id)` in `app/pipeline/orchestrator.py`
    - Identical flow to `run()` but skips `start_batch`; uses provided `batch_id` directly
    - Still calls `finish_batch` in `finally` block to keep `event_batches.total_events` and `completed_at` current
    - Returns `PipelineSummary`
    - _Requirements: 3.2, 4.1_

  - [ ]* 5.2 Write unit tests for `run_with_batch_id`
    - Create `tests/classify/test_orchestrator.py`
    - Mock resolver, classifier, writer; assert `start_batch` is NOT called
    - Assert `finish_batch` IS called in the finally block
    - Assert returned `PipelineSummary` counts are correct
    - _Requirements: 3.2, 4.1_

- [x] 6. Update `DB_Writer.write_batch` to return `url → staging_id` map
  - [x] 6.1 Modify `write_batch` in `app/db/writer.py` to return `tuple[int, int, dict[str, int]]`
    - Change INSERT to use `RETURNING id` and capture the returned `staging_id` for each inserted row
    - Third element of the return tuple maps `url → staging_id` for inserted rows only (skipped/duplicate rows are absent from the map)
    - _Requirements: 8.2_

  - [ ]* 6.2 Write unit tests for updated `write_batch`
    - Create `tests/classify/test_writer.py`
    - Mock asyncpg pool; verify the returned dict contains correct `url → staging_id` entries for inserted rows
    - Verify duplicate (skipped) rows are absent from the map
    - _Requirements: 8.2_

- [x] 7. Update `POST /scrape` to pass `staging_id` to background task and call `Staging_Updater`
  - [x] 7.1 Modify `app/routers/scrape.py` to unpack the new third return value from `write_batch`
    - Pass `staging_id` (looked up from the url→staging_id map) into `_run_pipeline` for each record
    - Inside `_run_pipeline`, after `orchestrator.run` completes, call `Staging_Updater.update_classification_status(staging_id, batch_id, summary, url, schema, is_retry=False)`
    - If `staging_id` is `None` (duplicate URL was skipped), log a WARNING with the article URL and skip the `Staging_Updater` call
    - _Requirements: 8.1, 8.2, 8.3_

  - [ ]* 7.2 Write unit tests for updated scrape background task
    - Create `tests/classify/test_scrape_integration.py`
    - Mock `DB_Writer` to return a known `staging_id`; verify `Staging_Updater.update_classification_status` is called with that ID
    - Verify that when `staging_id` is `None`, a WARNING is logged and `Staging_Updater` is not called
    - _Requirements: 8.1, 8.2, 8.3_

- [x] 8. Implement `POST /classify` router
  - [x] 8.1 Create `app/routers/classify.py` with the `classify` endpoint
    - Deduplicate `request.ids` preserving first occurrence before any DB work
    - Bulk SELECT `news_staging` WHERE `id = ANY($1)` retrieving `id`, `url`, `content`, `published_date`, `event_batch_id`; return HTTP 503 with `"database connectivity issue"` if SELECT raises
    - Populate `ids_missing` for IDs not returned by the SELECT
    - For each found row: if `content` is NULL or has no stock entries, add to `ids_skipped`, call `Staging_Updater` with a synthetic failed summary, continue
    - If `event_batch_id` is NULL: call `Event_DB_Writer.start_batch`; on failure log error, add to `ids_skipped`, continue
    - If `event_batch_id` is NOT NULL: call `orchestrator.run_with_batch_id` with the existing batch_id
    - If `event_batch_id` is NULL and `start_batch` succeeded: call `orchestrator.run`
    - On `orchestrator.run` / `run_with_batch_id` exception: log error with `staging_id`, add to `ids_skipped`, continue
    - After each successful run: call `Staging_Updater.update_classification_status`; on failure log error and continue (do not alter HTTP status)
    - Accumulate `ids_processed`, `total_stocks`, `events_inserted`, `events_failed`, `unresolved_stocks`
    - Return HTTP 200 `ClassifyResponse`
    - _Requirements: 1.5, 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4, 4.1, 4.2, 4.3, 4.4, 6.1, 6.2, 6.3, 9.1, 9.2, 9.3, 9.4, 9.5_

  - [ ]* 8.2 Write property test for deduplication
    - **Property 2: Deduplication preserves first occurrence**
    - **Validates: Requirements 1.5**
    - Create `tests/classify/test_classify_router.py`
    - Use `@given(ids=st.lists(st.integers(min_value=1, max_value=100), min_size=1, max_size=50))`
    - Assert the set of processed IDs equals the set of unique values from the original list
    - Tag: `# Feature: classify-api-staging-enhancement, Property 2: Deduplication preserves first occurrence`

  - [ ]* 8.3 Write property test for ID partition completeness
    - **Property 3: Every requested ID appears in exactly one response list**
    - **Validates: Requirements 2.1, 2.2, 2.3, 6.2**
    - In `tests/classify/test_classify_router.py`
    - Mock DB to return a known subset of IDs; assert every deduplicated input ID appears in exactly one of `ids_processed`, `ids_missing`, `ids_skipped`
    - Tag: `# Feature: classify-api-staging-enhancement, Property 3: Every requested ID appears in exactly one response list`

  - [ ]* 8.4 Write property test for response aggregation
    - **Property 5: Response aggregates correctly reflect pipeline summaries**
    - **Validates: Requirements 6.2**
    - In `tests/classify/test_classify_router.py`
    - Use `@given(summaries=st.lists(st.builds(PipelineSummary, ...), min_size=1, max_size=50))`
    - Assert `total_stocks`, `events_inserted`, `events_failed`, `unresolved_stocks` equal the corresponding sums
    - Tag: `# Feature: classify-api-staging-enhancement, Property 5: Response aggregates correctly reflect pipeline summaries`

  - [ ]* 8.5 Write property test for single-record failure resilience
    - **Property 6: Single-record failure does not prevent other records from being processed**
    - **Validates: Requirements 4.4, 9.2**
    - In `tests/classify/test_classify_router.py`
    - Use `@given(n=st.integers(min_value=2, max_value=10), fail_idx=st.integers(min_value=0))`
    - Mock orchestrator to raise for one record; assert N-1 records appear in `ids_processed` and the failing record appears in `ids_skipped`
    - Tag: `# Feature: classify-api-staging-enhancement, Property 6: Single-record failure does not prevent other records from being processed`

  - [x]* 8.6 Write unit tests for `POST /classify` router
    - In `tests/classify/test_classify_router.py`
    - Test: missing IDs → `ids_missing`; null content → `ids_skipped` with status=failed; `start_batch` failure → `ids_skipped`; pipeline exception → `ids_skipped`; `Staging_Updater` failure → continues, HTTP 200; bulk SELECT failure → HTTP 503 with "database connectivity issue"
    - _Requirements: 2.2, 2.3, 2.4, 3.4, 4.4, 6.3, 9.1, 9.2, 9.3, 9.4, 9.5_

- [ ] 9. Register `classify` router in `app/main.py`
  - Add `from app.routers import classify` import
  - Add `app.include_router(classify.router)` after existing router registrations
  - _Requirements: (wiring)_

- [x] 10. Final checkpoint — Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests use Hypothesis (already present in the project) with the tag format `# Feature: classify-api-staging-enhancement, Property {N}: {text}`
- Unit tests use pytest with mocked asyncpg pools/connections
- All tests go in `tests/classify/`
- `determine_status` is a pure module-level function in `staging_updater.py` — no I/O, fully unit-testable
- `run_with_batch_id` skips `start_batch` but still calls `finish_batch` in its `finally` block
- `write_batch` return type changes from `tuple[int, int]` to `tuple[int, int, dict[str, int]]` — update all call sites in `scrape.py`

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1", "2.1", "3.1"] },
    { "id": 1, "tasks": ["2.2", "2.3", "3.2", "3.3"] },
    { "id": 2, "tasks": ["3.4", "5.1"] },
    { "id": 3, "tasks": ["5.2", "6.1"] },
    { "id": 4, "tasks": ["6.2", "7.1"] },
    { "id": 5, "tasks": ["7.2", "8.1"] },
    { "id": 6, "tasks": ["8.2", "8.3", "8.4", "8.5", "8.6", "9"] }
  ]
}
```
