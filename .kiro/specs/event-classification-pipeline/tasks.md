# Implementation Plan: Event Classification Pipeline

## Overview

Implement the four-module event classification pipeline under `app/pipeline/`, integrate it into the existing `POST /scrape` endpoint, and extend `ScrapeResponse` with the `events_queued` field. Tests live in `tests/pipeline/` using pytest and Hypothesis.

## Tasks

- [x] 1. Set up package structure and shared data models
  - Create `app/pipeline/__init__.py` (empty)
  - Create `tests/pipeline/__init__.py` (empty)
  - Define `ClassificationResult` dataclass in `app/pipeline/classifier.py` (stub file — class body only, no methods yet)
  - Define `PipelineSummary` dataclass in `app/pipeline/orchestrator.py` (stub file — class body only)
  - Add `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL` to `.env` (placeholder values) and document them in a comment block
  - _Requirements: 2.1, 2.5, 6.4_

- [x] 2. Implement `Stock_Name_Resolver` (`app/pipeline/resolver.py`)
  - [x] 2.1 Implement `Stock_Name_Resolver.normalise()`
    - Compile a single regex that strips legal suffixes (`limited`, `ltd`, `industries`, `inc`, `corp`) as standalone tokens
    - Lowercase → remove punctuation → strip legal suffixes → collapse whitespace → strip
    - Return empty string for null/empty input
    - _Requirements: 1.1_

  - [ ]* 2.2 Write property test for `normalise()` invariants
    - **Property 1: Name normalisation invariants**
    - **Validates: Requirements 1.1**
    - File: `tests/pipeline/test_resolver_properties.py`
    - Use `@given(st.text())` with `@settings(max_examples=500)`
    - Assert: result is lowercase, no punctuation, no legal suffix tokens, no leading/trailing whitespace, no consecutive spaces

  - [x] 2.3 Implement `Stock_Name_Resolver.load_cache()`
    - `SELECT id, trading_symbol, short_name, name, is_active FROM classification.ticker_symbol`
    - Store rows as list of dicts in `self._cache`
    - Raise `RuntimeError("Failed to load ticker cache: {reason}")` on DB failure
    - _Requirements: 1.6, 1.7_

  - [x] 2.4 Implement `Stock_Name_Resolver.resolve()`
    - Normalise input; return `None` + log warning if empty after normalisation
    - Exact match pass: iterate cache checking `short_name` → `trading_symbol` → `name` (all normalised); return first hit regardless of `is_active`
    - Fuzzy pass (only if no exact match): `rapidfuzz.fuzz.token_set_ratio` against `short_name` and `name` of active records only; threshold 80; tie-break by lowest `id`
    - Return `None` + log warning with top candidate and score if nothing exceeds threshold
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [ ]* 2.5 Write property tests for `resolve()` — fuzzy threshold and column priority
    - **Property 2: Fuzzy match threshold enforcement**
    - **Validates: Requirements 1.4**
    - **Property 3: Exact match column priority**
    - **Validates: Requirements 1.3**
    - File: `tests/pipeline/test_resolver_properties.py`
    - Use `@given(st.text(min_size=1), st.lists(ticker_strategy(), min_size=1))` for Property 2
    - Use `@given(normalised_name_strategy())` for Property 3

  - [x] 2.6 Write unit tests for `Stock_Name_Resolver`
    - File: `tests/pipeline/test_resolver.py`
    - Cover: `normalise()` with mixed case, punctuation, legal suffixes, extra whitespace, null, empty string, suffix-only strings
    - Cover: `resolve()` exact match priority order; fuzzy returns highest-scoring active candidate; inactive records excluded; tie-break by lowest `id`; below-threshold returns `None`
    - Cover: `load_cache()` DB failure raises `RuntimeError`
    - _Requirements: 1.1–1.7_

- [x] 3. Implement `Event_Classifier` (`app/pipeline/classifier.py`)
  - [x] 3.1 Implement `Event_Classifier.__init__()` and `_build_prompt()`
    - Accept `valid_subtypes: dict[str, set[str]]` in constructor
    - `_build_prompt(news_text, section_name, stock_name)` builds system + user message pair
    - System message lists all nine required JSON field names and the full event_subtypes reference
    - User message includes `Section:`, `Stock:`, `News:` fields
    - Section hint logic: if `section_name.lower()` contains `"bulk deal"` → add `bulk_deal` hint; if contains `"block deal"` → add `block_deal` hint
    - Include per-event-type detail field schema in system message (all nine types, Requirements 3.1–3.9)
    - _Requirements: 2.1, 2.2, 2.3, 3.1–3.9, 8.1, 8.2, 8.3_

  - [ ]* 3.2 Write property tests for prompt completeness
    - **Property 4: LLM prompt completeness**
    - **Validates: Requirements 2.1, 8.3**
    - **Property 5: Detail field prompt completeness**
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9**
    - **Property 12: Bulk/block deal section hint inclusion**
    - **Validates: Requirements 8.1**
    - File: `tests/pipeline/test_classifier_properties.py`

  - [x] 3.3 Implement `_validate_confidence()` and `_process_title()`
    - `_validate_confidence(score, model_version)`: return `(None, None)` if score is None, NaN, infinite, or outside [0.0, 1.0]; otherwise return `(Decimal(score).quantize(Decimal("0.001")), model_version)`
    - `_process_title(title)`: truncate to 500 characters
    - _Requirements: 2.5, 2.8, 2.10_

  - [ ]* 3.4 Write property tests for confidence validation and title truncation
    - **Property 6: Confidence score range validation**
    - **Validates: Requirements 2.5, 2.8**
    - **Property 7: Title length invariant**
    - **Validates: Requirements 2.10**
    - File: `tests/pipeline/test_classifier_properties.py`

  - [x] 3.5 Implement `Event_Classifier.classify()`
    - Instantiate `openai.AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)`
    - Call with `model=LLM_MODEL`, `response_format={"type": "json_object"}`, timeout 30s
    - Retry up to 2 additional times (3 total) on network/timeout errors with 2s delay; invalid JSON → `status="failed"` immediately, no retry
    - Validate `(event_type, event_subtype)` pair against `valid_subtypes`; invalid → `status="unclassified"`, null out type/subtype/confidence
    - Apply `_validate_confidence()`, `_process_title()`, and details validation
    - Return fully populated `ClassificationResult`
    - _Requirements: 2.1–2.10_

  - [x] 3.6 Write unit tests for `Event_Classifier`
    - File: `tests/pipeline/test_classifier.py`
    - Cover: 3 LLM failures → `status="failed"`; 2 failures then success → `status="ok"`; invalid JSON → `status="failed"` no retry; invalid subtype pair → `status="unclassified"`; confidence out of range; title truncation at 500; absent/invalid details → `{}`; bulk deal hint present; non-bulk section → no hint
    - _Requirements: 2.1–2.10, 8.1–8.3_

- [x] 4. Checkpoint — resolver and classifier complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement `Event_DB_Writer` (`app/pipeline/event_writer.py`)
  - [x] 5.1 Implement `Event_DB_Writer.__init__()` and `_detail_table()`
    - Constructor accepts `pool: asyncpg.Pool` and `schema: str`
    - `_detail_table(event_type: str) -> str`: static/class method returning the exact detail table name per the mapping in Requirements 5.2; raise `ValueError` for unrecognised types
    - _Requirements: 5.2_

  - [ ]* 5.2 Write property test for detail table mapping
    - **Property 8: Event type → detail table mapping correctness**
    - **Validates: Requirements 5.2**
    - File: `tests/pipeline/test_writer_properties.py`
    - Use `@given(st.sampled_from(RECOGNISED_EVENT_TYPES))`

  - [x] 5.3 Implement `Event_DB_Writer.start_batch()` and `finish_batch()`
    - `start_batch(batch_name)`: INSERT into `{schema}.event_batches`; return `batch_id`; raise `RuntimeError("Failed to create event batch: {reason}")` on failure
    - `finish_batch(batch_id, total_events)`: UPDATE `completed_at` and `total_events`; log error on failure, do not re-raise
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [x] 5.4 Implement `Event_DB_Writer._build_event_params()` and `write_event()`
    - `_build_event_params(result, stock_id, batch_id, source_url)`: build the dict of column values for the `events` INSERT; always set `source_name='moneycontrol'` and `ingestion_source='moneycontrol'` and `is_verified=False`; apply confidence null-coercion (Requirement 5.7); use `published_date` fallback for `event_date` (Requirement 5.4)
    - `write_event(batch_id, stock_id, result, record, source_url)`: skip + log warning for unrecognised `event_type`; INSERT events row + detail row in single transaction; `ON CONFLICT DO NOTHING`; check affected row count (0 → log info, return `False`); rollback + log error on failure, return `False`; return `True` on success
    - _Requirements: 5.1–5.7, 9.1, 9.2, 9.3_

  - [ ]* 5.5 Write property tests for `write_event()` — source_name invariant and idempotency
    - **Property 9: source_name invariant**
    - **Validates: Requirements 5.5**
    - **Property 10: Idempotency — duplicate events are not inserted**
    - **Validates: Requirements 9.1, 9.2, 9.3**
    - File: `tests/pipeline/test_writer_properties.py`

  - [x] 5.6 Write unit tests for `Event_DB_Writer`
    - File: `tests/pipeline/test_writer.py`
    - Cover: `start_batch()` success and failure; `finish_batch()` failure logs but does not raise; `write_event()` success returns `True`; duplicate (0 rows) returns `False`; transaction failure rolls back and returns `False`; unrecognised `event_type` returns `False`; confidence_score not null + model_version null → both set to null
    - _Requirements: 4.1–4.6, 5.1–5.7, 9.1–9.3_

- [x] 6. Implement `Pipeline_Orchestrator` (`app/pipeline/orchestrator.py`)
  - [x] 6.1 Implement `Pipeline_Orchestrator.__init__()` and `run()`
    - Constructor accepts `resolver`, `classifier`, `writer`
    - `run(record, batch_name)`:
      1. Call `writer.start_batch(batch_name)` → `batch_id`
      2. Iterate `record.sections` → `(section_name, stock_name, news_text)` triples
      3. Call `resolver.resolve(stock_name)` → skip + log warning if `None`
      4. Call `classifier.classify(news_text, section_name, stock_name)` → skip DB write if `status != "ok"`
      5. Call `writer.write_event(batch_id, stock_id, result, record, record.url)`
      6. Accumulate counts into `PipelineSummary`
      7. Call `writer.finish_batch(batch_id, db_inserted)` in `finally` block
      8. Log summary at INFO level; return `PipelineSummary`
    - _Requirements: 4.1–4.4, 6.1–6.4_

  - [ ]* 6.2 Write property test for pipeline summary completeness
    - **Property 11: Pipeline summary completeness**
    - **Validates: Requirements 6.4**
    - File: `tests/pipeline/test_orchestrator_properties.py`
    - Use `@given(pipeline_run_strategy())` with mocked resolver/classifier/writer
    - Assert: `resolved + unresolved == total`, `classified + classification_failures + unresolved == total`, `db_inserted + db_failures <= classified`

  - [x] 6.3 Write unit tests for `Pipeline_Orchestrator`
    - File: `tests/pipeline/test_orchestrator.py`
    - Cover: full happy path (resolver → classifier → writer all succeed); unresolved stock skips classifier and writer; classifier failure skips writer; writer failure continues to next entry; summary counts correct for mixed outcomes
    - _Requirements: 6.1–6.4_

- [x] 7. Checkpoint — pipeline modules complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Integrate pipeline into `app/routers/scrape.py` and `app/schemas.py`
  - [x] 8.1 Add `events_queued: int` field to `ScrapeResponse` in `app/schemas.py`
    - Add field with default `0` so existing callers are not broken
    - _Requirements: 7.3_

  - [ ]* 8.2 Write property test for `events_queued` count correctness
    - **Property 13: events_queued count correctness**
    - **Validates: Requirements 7.3**
    - File: `tests/pipeline/test_scrape_integration_properties.py`
    - Use `@given(st.lists(output_record_strategy(), min_size=0, max_size=10))`
    - Assert `response.events_queued == sum(len(stocks) for record in records for stocks in record.sections.values())`

  - [x] 8.3 Wire pipeline into `scrape.py`
    - Import `Pipeline_Orchestrator`, `Stock_Name_Resolver`, `Event_Classifier`, `Event_DB_Writer` from `app.pipeline`
    - After `writer.write_batch()` in the `database`/`both` branch, for each successfully inserted record:
      - Compute `stock_entry_count = sum(len(stocks) for stocks in record.sections.values())`
      - Construct orchestrator with resolver, classifier, writer instances (load `valid_subtypes` from `event_subtypes` table or a static mapping)
      - Fire `asyncio.create_task(orchestrator.run(record, batch_name))` with an exception handler that logs at ERROR and suppresses
    - Accumulate `events_queued` across all inserted records
    - Set `events_queued = 0` when `output_mode == "file"` or no records inserted
    - Return `ScrapeResponse(..., events_queued=events_queued)`
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [ ]* 8.4 Write unit tests for scrape endpoint integration
    - File: `tests/pipeline/test_scrape_integration.py`
    - Cover: `output_mode="database"` → pipeline task fired per inserted record, `events_queued` equals total stock entries; `output_mode="file"` → pipeline not invoked, `events_queued=0`; pipeline background task raises → error logged, `events_queued=0`, HTTP 200 returned
    - _Requirements: 7.1–7.5_

- [x] 9. Final checkpoint — full pipeline integrated
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties (Properties 1–13 from design)
- Unit tests validate specific examples and edge cases
- `rapidfuzz` and `openai` must be added to project dependencies before implementing Tasks 2 and 3
- `valid_subtypes` dict for `Event_Classifier` can be loaded once at app startup from the `event_subtypes` table and stored on `app.state`

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1"] },
    { "id": 1, "tasks": ["2.1", "3.1", "5.1", "8.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "3.2", "3.3", "5.2"] },
    { "id": 3, "tasks": ["2.4", "3.4", "3.5", "5.3"] },
    { "id": 4, "tasks": ["2.5", "2.6", "3.6", "5.4"] },
    { "id": 5, "tasks": ["5.5", "5.6", "6.1", "8.2"] },
    { "id": 6, "tasks": ["6.2", "6.3", "8.3"] },
    { "id": 7, "tasks": ["8.4"] }
  ]
}
```
