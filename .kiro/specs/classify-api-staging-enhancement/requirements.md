# Requirements Document

## Introduction

This feature enhances Phase 3 of the MoneyControl scraper pipeline with two capabilities:

1. **Manual Classify API** — a new `POST /classify` endpoint that allows callers to manually trigger event classification for one or more records already stored in `news_staging`, identified by their `news_staging.id` values.

2. **news_staging Classification Tracking** — new columns on the `news_staging` table that record the classification state of each row (`event_batch_id`, `stocks_loaded`, `stocks_failed`, `classification_status`), updated after every pipeline run whether triggered manually or via the existing `POST /scrape` background task.

Together these capabilities enable targeted retry of failed classifications and full observability of classification progress per staging record.

---

## Glossary

- **Classify_API**: The new `POST /classify` HTTP endpoint defined in this feature.
- **Pipeline_Orchestrator**: The existing `app/pipeline/orchestrator.py` component that coordinates stock resolution, event classification, and DB writing for a single article record.
- **Event_DB_Writer**: The existing `app/pipeline/event_writer.py` component that persists event batches and event rows to PostgreSQL.
- **Staging_Updater**: The new component responsible for reading and writing the classification-tracking columns on `news_staging` after a pipeline run.
- **news_staging**: The PostgreSQL table that stores scraped article records before and during classification.
- **event_batches**: The `stoxscoop_dev.event_batches` table that tracks pipeline batch runs.
- **ClassifyRequest**: The Pydantic request schema for `POST /classify`, containing a list of `news_staging.id` values.
- **ClassifyResponse**: The Pydantic response schema for `POST /classify`, containing per-record and aggregate classification results.
- **classification_status**: A varchar column on `news_staging` with four allowed values: `pending`, `partial`, `complete`, `failed`.
- **batch_id**: The integer primary key of a row in `event_batches`, used to group events from a single pipeline run.
- **stock entry**: A single (section_name, stock_name, news_text) triple extracted from an article's `content` JSONB field.
- **unresolved stock**: A stock entry whose `stock_name` could not be matched to a `classification.ticker_symbol` row.

---

## Requirements

### Requirement 1: ClassifyRequest Validation

**User Story:** As an API consumer, I want the `POST /classify` endpoint to validate my input, so that I receive clear errors for malformed requests before any database work is attempted.

#### Acceptance Criteria

1. THE Classify_API SHALL accept a JSON request body containing a field `ids` that is a non-empty list of between 1 and 50 positive integers (inclusive).
2. IF the request body is missing, is not valid JSON, or does not contain an `ids` field, THEN THE Classify_API SHALL return HTTP 422 with an error message identifying the `ids` field as required.
3. IF the `ids` list contains fewer than 1 or more than 50 values, THEN THE Classify_API SHALL return HTTP 422 with an error message identifying the `ids` field and stating that between 1 and 50 values are required.
4. IF any value in the `ids` list is not a positive integer (i.e., is zero, negative, a float, or a non-numeric type), THEN THE Classify_API SHALL return HTTP 422 with an error message identifying the `ids` field and stating that all values must be positive integers.
5. WHEN the `ids` list contains duplicate values, THE Classify_API SHALL deduplicate the list before processing, preserving the first occurrence of each value, and SHALL NOT return an error for duplicates.

---

### Requirement 2: Staging Record Lookup

**User Story:** As an API consumer, I want the endpoint to load article content from `news_staging` for the IDs I provide, so that classification runs against the correct stored data.

#### Acceptance Criteria

1. WHEN `POST /classify` is called with a valid `ids` list, THE Classify_API SHALL query `news_staging` for rows whose `id` is in the provided list, retrieving `id`, `url`, `content`, `published_date`, and `event_batch_id` for each row.
2. WHEN a provided `id` does not exist in `news_staging`, THE Classify_API SHALL skip that ID and include it in the response's `ids_missing` list.
3. WHEN a staging record's `content` field is NULL or contains no stock entries (all section dicts are empty), THE Classify_API SHALL skip that record entirely without invoking the classification pipeline, set its `classification_status` to `failed`, and include its `id` in the response's `ids_skipped` list.
4. WHEN all provided IDs are either missing or have no content, THE Classify_API SHALL return HTTP 200 with `ids_processed` as an empty list, all count fields set to 0, and populated `ids_missing` and `ids_skipped` lists.

---

### Requirement 3: Batch ID Reuse on Retry

**User Story:** As an operator, I want failed classifications to be retried under the same batch ID, so that partial results from a previous run are preserved and only the failed stocks are reprocessed.

#### Acceptance Criteria

1. WHEN a staging record's `event_batch_id` column is NULL, THE Classify_API SHALL call `Event_DB_Writer.start_batch` to create a new batch for that record and use the returned `batch_id` for that record's pipeline run; each NULL record in the same request receives its own independently created batch.
2. WHEN a staging record's `event_batch_id` column is NOT NULL, THE Classify_API SHALL pass that existing `batch_id` directly to the Pipeline_Orchestrator and SHALL NOT call `Event_DB_Writer.start_batch` for that record.
3. WHEN reusing an existing `batch_id`, stock entries that were already successfully classified in a previous run SHALL be skipped without re-classifying or re-inserting them; the observable outcome is that `write_event` returns `False` for those entries and they are not counted in `db_inserted` for the current run.
4. IF `Event_DB_Writer.start_batch` raises an exception for a NULL-batch record, THE Classify_API SHALL log the error, set that record's `classification_status` to `failed`, and continue processing the remaining records without aborting the request.

---

### Requirement 4: Pipeline Execution per Staging Record

**User Story:** As an operator, I want the classify endpoint to run the full classification pipeline for each staging record, so that events are extracted and persisted to the database.

#### Acceptance Criteria

1. WHEN a staging record is eligible for classification, THE Classify_API SHALL construct a `Pipeline_Orchestrator` instance with a `Stock_Name_Resolver`, `Event_Classifier`, and `Event_DB_Writer`, and SHALL call `Pipeline_Orchestrator.run(record, batch_name)` for that record synchronously (not as a background task), awaiting completion before processing the next record.
2. WHEN `Pipeline_Orchestrator.run` is called, THE Pipeline_Orchestrator SHALL load the `Stock_Name_Resolver` cache before processing any stock entries for that record.
3. WHEN `Pipeline_Orchestrator.run` completes, THE Classify_API SHALL capture the returned `PipelineSummary` containing `total`, `db_inserted`, `db_failures`, and `unresolved` counts.
4. WHEN `Pipeline_Orchestrator.run` raises an unhandled exception for a record, THE Classify_API SHALL log the error including the staging record `id` and the exception message, mark that record's `classification_status` as `failed`, include the record's `id` in the response's `ids_skipped` list, and continue processing remaining records.

---

### Requirement 5: Classification Status Update

**User Story:** As an operator, I want `news_staging` to reflect the outcome of each classification run, so that I can monitor progress and identify records that need attention.

#### Acceptance Criteria

1. WHEN `Pipeline_Orchestrator.run` completes for a staging record, THE Staging_Updater SHALL issue a single UPDATE to `news_staging` for that record's `id` setting `event_batch_id`, `stocks_loaded`, `stocks_failed`, and `classification_status` atomically.
2. WHEN `db_inserted > 0` AND `db_failures == 0` AND `unresolved == 0`, THE Staging_Updater SHALL set `classification_status` to `complete` and `stocks_loaded` to `db_inserted` and `stocks_failed` to 0.
3. WHEN `db_inserted > 0` AND (`db_failures > 0` OR `unresolved > 0`), THE Staging_Updater SHALL set `classification_status` to `partial`, `stocks_loaded` to `db_inserted`, and `stocks_failed` to `db_failures + unresolved`.
4. WHEN `db_inserted == 0` AND `total > 0`, THE Staging_Updater SHALL set `classification_status` to `failed`, `stocks_loaded` to 0, and `stocks_failed` to `total`.
5. WHEN a staging record has no stock entries in its content (total == 0), THE Staging_Updater SHALL set `classification_status` to `failed`, `stocks_loaded` to 0, and `stocks_failed` to 0 without calling the pipeline.
6. WHEN updating on a retry run (existing `event_batch_id` reused), THE Staging_Updater SHALL query `stoxscoop_dev.events` for the count of rows matching `batch_id = <existing_batch_id>` AND `source_url = <record_url>` to determine the cumulative `stocks_loaded`, and SHALL set `stocks_failed` to `total - stocks_loaded`.

---

### Requirement 6: ClassifyResponse

**User Story:** As an API consumer, I want a structured response from `POST /classify`, so that I can see exactly what was processed, inserted, and failed.

#### Acceptance Criteria

1. THE Classify_API SHALL return HTTP 200 with a `ClassifyResponse` body on successful completion, even if some or all records failed classification.
2. THE ClassifyResponse SHALL include the following fields: `ids_processed` (list of integer IDs that were found in `news_staging` and had at least one stock entry), `ids_missing` (list of integer IDs not found in `news_staging`), `ids_skipped` (list of integer IDs with NULL/empty content or that failed due to pipeline exception or `start_batch` failure), `total_stocks` (integer sum of `total` across all `PipelineSummary` results), `events_inserted` (integer sum of `db_inserted`), `events_failed` (integer sum of `db_failures`), `unresolved_stocks` (integer sum of `unresolved`).
3. WHEN no eligible records are found (all IDs are missing or skipped), THE Classify_API SHALL return HTTP 200 with `ids_processed` as an empty list and `total_stocks`, `events_inserted`, `events_failed`, and `unresolved_stocks` all set to 0.

---

### Requirement 7: news_staging Schema Migration

**User Story:** As a database administrator, I want the `news_staging` table to have classification-tracking columns, so that the pipeline can record its state per record.

#### Acceptance Criteria

1. THE migration SQL SHALL add column `event_batch_id` of type `integer`, nullable, with a foreign key constraint referencing `stoxscoop_dev.event_batches(id)` with `ON DELETE SET NULL`, using `ADD COLUMN IF NOT EXISTS`.
2. THE migration SQL SHALL add column `stocks_loaded` of type `integer` with `DEFAULT 0` and `NOT NULL`, using `ADD COLUMN IF NOT EXISTS`.
3. THE migration SQL SHALL add column `stocks_failed` of type `integer` with `DEFAULT 0` and `NOT NULL`, using `ADD COLUMN IF NOT EXISTS`.
4. THE migration SQL SHALL add column `classification_status` of type `varchar(20)` with `DEFAULT 'pending'` and `NOT NULL`, using `ADD COLUMN IF NOT EXISTS`.
5. THE migration SQL SHALL include an UPDATE statement that sets `classification_status = 'pending'`, `stocks_loaded = 0`, and `stocks_failed = 0` for all pre-existing rows where `classification_status IS NULL`, ensuring backfill is part of the same migration script.
6. THE migration SQL SHALL add a check constraint (using `ADD CONSTRAINT IF NOT EXISTS`) ensuring `classification_status` is one of `'pending'`, `'partial'`, `'complete'`, `'failed'`.
7. THE migration SQL SHALL be idempotent — all `ADD COLUMN` and `ADD CONSTRAINT` statements SHALL use `IF NOT EXISTS` so that re-running the migration on an already-migrated table produces no errors and no changes.
8. THE migration SQL SHALL add a check constraint (using `ADD CONSTRAINT IF NOT EXISTS`) ensuring `stocks_loaded >= 0` AND `stocks_failed >= 0`.

---

### Requirement 8: POST /scrape Integration

**User Story:** As a developer, I want the existing `POST /scrape` background pipeline to also update the new `news_staging` classification columns, so that both manual and automatic classification paths produce consistent tracking data.

#### Acceptance Criteria

1. WHEN the `POST /scrape` background pipeline completes classification for a staging record, THE Staging_Updater SHALL update `event_batch_id`, `stocks_loaded`, `stocks_failed`, and `classification_status` on the corresponding `news_staging` row using the same status-determination logic as `POST /classify` (Requirements 5.2–5.5).
2. WHEN the `POST /scrape` pipeline inserts a new staging record and then immediately classifies it in the background, THE Staging_Updater SHALL update the classification columns on that same row after the pipeline run completes, using the `news_staging.id` returned by the staging INSERT.
3. IF the `POST /scrape` pipeline cannot identify the `news_staging.id` for a processed record (e.g., the record was skipped due to a duplicate URL), THEN THE Staging_Updater SHALL log a warning at WARNING level containing the article URL and skip the status update for that record, continuing processing of remaining records without raising an exception.

---

### Requirement 9: Error Handling and Resilience

**User Story:** As an operator, I want the classify endpoint to handle individual record failures gracefully, so that a single bad record does not prevent other records from being processed.

#### Acceptance Criteria

1. WHEN a database error occurs while loading staging records (the initial bulk SELECT), THE Classify_API SHALL return HTTP 503 with an error message containing the text "database connectivity issue" and SHALL NOT begin loading or processing any staging records.
2. WHEN `Pipeline_Orchestrator.run` raises an exception for one record, THE Classify_API SHALL log the error, include that record's `id` in `ids_skipped` (and NOT in `ids_processed`), and continue processing the remaining records.
3. WHEN the `Staging_Updater` fails to write classification status for a record (the UPDATE raises an exception), THE Classify_API SHALL log the error including the staging record `id` and the exception message, and SHALL continue processing remaining records without altering the HTTP response status.
4. IF logging the `Staging_Updater` failure itself raises an exception, THE Classify_API SHALL skip the status update for that record and continue processing remaining records without raising an exception.
5. IF the `event_batches` table is unreachable when `start_batch` is called, THE Classify_API SHALL log the error, set that record's `classification_status` to `failed`, and continue processing the remaining records without aborting the request or returning HTTP 503.
