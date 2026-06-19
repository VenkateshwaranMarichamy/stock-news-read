# Requirements Document

## Introduction

This feature implements Phase 3 of the MoneyControl stocks scraper project: the event classification pipeline. After Phase 1 scrapes MoneyControl "Stocks to Watch" articles into structured JSON and Phase 2 stores them in the `news_staging` PostgreSQL table, Phase 3 classifies each scraped stock news entry by event type and subtype, then inserts structured records into the `stoxscoop_dev` event tables.

The pipeline consists of four components: a Stock_Name_Resolver that fuzzy-matches scraped stock names to `classification.ticker_symbol` records, an Event_Classifier that uses an LLM to extract event type, subtype, signal, sentiment, and detail fields from news text, an Event_DB_Writer that persists the classified events to the database, and a Pipeline_Orchestrator that coordinates these components and integrates with the existing scrape endpoint.

## Glossary

- **Pipeline_Orchestrator**: The top-level component that coordinates the stock name resolver, event classifier, and event DB writer for a single scrape run.
- **Stock_Name_Resolver**: The component that maps a scraped stock name string to a `classification.ticker_symbol.id` integer using normalised and fuzzy string matching.
- **Scraped_Stock_Name**: A stock name string extracted from a MoneyControl article section (e.g., "Escorts Kubota", "Pine Labs").
- **Ticker_Symbol**: A row in `classification.ticker_symbol` identified by its `id`, `trading_symbol`, `short_name`, and `name` columns.
- **Event_Classifier**: The component that sends a stock news text to an LLM and receives a structured classification result containing event type, subtype, signal, sentiment, priority, title, summary, and detail fields.
- **Classification_Result**: The structured output of the Event_Classifier for a single stock news entry, containing all fields required to populate the `events` table and the appropriate detail table.
- **Event_DB_Writer**: The component that persists a Classification_Result to the `stoxscoop_dev.event_batches`, `stoxscoop_dev.events`, and the appropriate detail table.
- **Event_Batch**: A row in `stoxscoop_dev.event_batches` representing one scrape run, grouping all events produced in that run.
- **Detail_Table**: One of the nine event-type-specific tables (`business_event_details`, `corporate_action_details`, `credit_rating_details`, `disclosure_details`, `financial_result_details`, `fundraising_details`, `governance_details`, `insider_details`, `legal_details`) that stores structured fields for a classified event.
- **Unresolved_Stock**: A Scraped_Stock_Name for which the Stock_Name_Resolver cannot find a matching Ticker_Symbol with sufficient confidence.
- **Confidence_Score**: A numeric value in the range [0.000, 1.000] representing the LLM's self-reported certainty in the Classification_Result.
- **Normalised_Name**: A stock name string converted to lowercase, with all punctuation removed, legal suffixes (e.g., "limited", "ltd", "industries", "inc", "corp") stripped, and consecutive whitespace collapsed to a single space with leading/trailing whitespace removed.

---

## Requirements

### Requirement 1: Stock Name Resolution

**User Story:** As a data engineer, I want scraped stock names to be reliably matched to ticker symbol records, so that classified events are linked to the correct stock.

#### Acceptance Criteria

1. WHEN a Scraped_Stock_Name is provided, THE Stock_Name_Resolver SHALL compute its Normalised_Name by converting to lowercase, removing all punctuation characters, stripping legal suffixes ("limited", "ltd", "industries", "inc", "corp"), collapsing consecutive whitespace to a single space, and trimming leading/trailing whitespace; THEN THE Stock_Name_Resolver SHALL attempt to match the Normalised_Name against the `short_name`, `trading_symbol`, and `name` columns of `classification.ticker_symbol` (each also normalised using the same transformation).
2. WHEN the Scraped_Stock_Name is null or its Normalised_Name is an empty string after transformation, THE Stock_Name_Resolver SHALL classify the stock as Unresolved and SHALL log a warning containing the original Scraped_Stock_Name and the reason "empty after normalisation".
3. WHEN an exact Normalised_Name match is found, THE Stock_Name_Resolver SHALL return the corresponding `ticker_symbol.id` without performing fuzzy matching, regardless of the record's `is_active` status; WHERE the same Normalised_Name matches multiple rows, THE Stock_Name_Resolver SHALL use column priority order `short_name` → `trading_symbol` → `name` and return the first match found.
4. WHEN no exact Normalised_Name match is found, THE Stock_Name_Resolver SHALL apply token-based fuzzy matching (minimum similarity threshold: 80%) against the `short_name` and `name` columns of active records only (`is_active = true`) and SHALL return the `ticker_symbol.id` of the highest-scoring candidate above the threshold; WHERE two candidates share the same highest score, THE Stock_Name_Resolver SHALL return the candidate with the lowest `id` value.
5. IF no active candidate exceeds the 80% similarity threshold, THEN THE Stock_Name_Resolver SHALL classify the stock as Unresolved and SHALL log a warning containing the Scraped_Stock_Name, the top candidate name, and its similarity score.
6. WHEN a pipeline run starts, THE Stock_Name_Resolver SHALL load all rows from `classification.ticker_symbol` into an in-memory cache and SHALL reuse that cache for all resolution calls within the same run without issuing additional database queries.
7. IF the database query to load the ticker symbol cache fails, THEN THE Stock_Name_Resolver SHALL raise a descriptive error containing the failure reason and SHALL halt the pipeline run before processing any stock entries.

---

### Requirement 2: Event Classification via LLM

**User Story:** As a data engineer, I want each stock news text to be classified into a structured event record by an LLM, so that downstream consumers can filter and act on events by type.

#### Acceptance Criteria

1. WHEN a resolved stock news text is provided, THE Event_Classifier SHALL send the text to the configured LLM with a structured prompt that instructs the model to return a JSON object containing: `event_type`, `event_subtype`, `signal_type`, `sentiment`, `priority`, `title`, `summary`, `confidence_score`, and a `details` object with fields appropriate to the event type.
2. THE Event_Classifier SHALL constrain the LLM response to valid `event_type` values (`corporate_action`, `disclosure`, `insider`, `business`, `governance`, `credit_rating`, `financials`, `fundraising`, `legal`) and valid `event_subtype` codes from the `event_subtypes` reference table.
3. THE Event_Classifier SHALL constrain the LLM response to valid `signal_type` values (`bullish`, `bearish`, `neutral`, `mixed`), valid `sentiment` values (`positive`, `negative`, `neutral`, `mixed`), and valid `priority` values (`low`, `medium`, `high`, `critical`).
4. WHEN the LLM returns a response, THE Event_Classifier SHALL validate that the returned `event_type` and `event_subtype` form a valid pair present in the `event_subtypes` reference table; IF the pair is invalid, THEN THE Event_Classifier SHALL log a warning including the invalid pair and set the Classification_Result status to `unclassified` with `event_type`, `event_subtype`, `confidence_score`, and `confidence_model_version` all set to null.
5. WHEN the LLM returns a `confidence_score` and a model identifier, THE Event_Classifier SHALL validate that `confidence_score` is a number in the range [0.000, 1.000]; IF `confidence_score` is outside this range, absent, or the model identifier is absent, THEN THE Event_Classifier SHALL set both `confidence_score` and `confidence_model_version` to null in the Classification_Result.
6. IF the LLM call fails due to a network error or a response timeout exceeding 30 seconds, THEN THE Event_Classifier SHALL retry the call up to 2 additional times with a 2-second delay between attempts; IF all 3 attempts fail, THEN THE Event_Classifier SHALL set the Classification_Result status to `failed` and log an error containing the Scraped_Stock_Name and the final failure reason.
7. IF the LLM returns a response that cannot be parsed as valid JSON, THEN THE Event_Classifier SHALL log the raw response text and set the Classification_Result status to `failed` immediately without retrying the LLM call.
8. THE Event_Classifier SHALL include the LLM model identifier as the `confidence_model_version` in every Classification_Result where `confidence_score` is not null.
9. WHEN the LLM returns a `details` object that is absent, not a JSON object, or contains no recognisable fields for the classified `event_type`, THE Event_Classifier SHALL set the `details` field to an empty object `{}` in the Classification_Result and SHALL log a warning containing the Scraped_Stock_Name and the event type.
10. WHEN the LLM returns a `title` value that exceeds 500 characters, THE Event_Classifier SHALL truncate the value to 500 characters before storing it in the Classification_Result.

---

### Requirement 3: Detail Field Extraction

**User Story:** As a data engineer, I want the LLM to extract structured detail fields specific to each event type, so that the appropriate detail table is populated with actionable data.

#### Acceptance Criteria

1. WHEN the classified `event_type` is `business`, THE Event_Classifier SHALL instruct the LLM to extract the following fields where present in the news text and return them as a structured object: `contract_type`, `client_name`, `client_sector`, `contract_value`, `capex_amount`, `currency` (ISO 4217 3-letter code), `duration_years`, `geography`, `project_name`, `jv_partner`, `ownership_pct` (0–100 numeric scale), `is_repeat_order`, `product_name`, `target_geography`, `target_segment`, `campaign_name`, `target_revenue`, `target_timeline`, and `description`.
2. WHEN the classified `event_type` is `disclosure`, THE Event_Classifier SHALL instruct the LLM to extract the following fields where present: `investor_category`, `investor_name`, `investor_country`, `transaction_type`, `transaction_mode`, `shares_transacted`, `price_per_share`, `transaction_value`, `currency` (ISO 4217 3-letter code), `stake_before` (0–100 numeric scale), `stake_after` (0–100 numeric scale), `transaction_date` (ISO 8601 YYYY-MM-DD), `exchange`, and `description`.
3. WHEN the classified `event_type` is `insider`, THE Event_Classifier SHALL instruct the LLM to extract the following fields where present: `person_name`, `designation`, `relationship`, `transaction_type`, `shares_transacted`, `price_per_share`, `transaction_value`, `currency` (ISO 4217 3-letter code), `stake_before` (0–100 numeric scale), `stake_after` (0–100 numeric scale), `pledge_percentage` (0–100 numeric scale), `sebi_disclosure_date` (ISO 8601 YYYY-MM-DD), `transaction_date` (ISO 8601 YYYY-MM-DD), and `description`.
4. WHEN the classified `event_type` is `corporate_action`, THE Event_Classifier SHALL instruct the LLM to extract the following fields where present: `record_date` (ISO 8601 YYYY-MM-DD), `effective_date` (ISO 8601 YYYY-MM-DD), `ratio`, `amount_per_share`, `total_size`, `currency` (ISO 4217 3-letter code), `target_company`, `swap_ratio`, `offer_price`, `stake_acquired_pct` (0–100 numeric scale), `resulting_stake_pct` (0–100 numeric scale), `shares_transacted`, and `description`.
5. WHEN the classified `event_type` is `financials`, THE Event_Classifier SHALL instruct the LLM to extract the following fields where present: `period_quarter` (one of Q1/Q2/Q3/Q4), `period_year` (4-digit integer), `revenue`, `revenue_yoy_pct`, `ebitda`, `ebitda_margin`, `pat`, `pat_yoy_pct`, `eps`, `beat_miss`, `guidance_revenue`, `guidance_margin`, `currency` (ISO 4217 3-letter code), `key_highlight`, and `description`.
6. WHEN the classified `event_type` is `governance`, THE Event_Classifier SHALL instruct the LLM to extract the following fields where present: `person_name`, `designation`, `change_type`, `effective_date` (ISO 8601 YYYY-MM-DD), `reason`, `regulator`, `action_type`, `penalty_amount`, `currency` (ISO 4217 3-letter code), `meeting_date` (ISO 8601 YYYY-MM-DD), `agenda_summary`, and `description`.
7. WHEN the classified `event_type` is `legal`, THE Event_Classifier SHALL instruct the LLM to extract the following fields where present: `forum`, `case_number`, `counterparty`, `demand_amount`, `penalty_amount`, `currency` (ISO 4217 3-letter code), `company_stance`, `outcome`, `order_date` (ISO 8601 YYYY-MM-DD), `next_hearing_date` (ISO 8601 YYYY-MM-DD), `contingent_liability`, and `description`.
8. WHEN the classified `event_type` is `credit_rating`, THE Event_Classifier SHALL instruct the LLM to extract the following fields where present: `agency`, `instrument_type`, `instrument_name`, `rating_before`, `rating_after`, `outlook_before`, `outlook_after`, `rated_amount`, `currency` (ISO 4217 3-letter code), `rationale`, `rating_date` (ISO 8601 YYYY-MM-DD), and `description`.
9. WHEN the classified `event_type` is `fundraising`, THE Event_Classifier SHALL instruct the LLM to extract the following fields where present: `issue_size`, `currency` (ISO 4217 3-letter code), `price_per_share`, `number_of_shares`, `allottee_name`, `allottee_category`, `coupon_rate`, `maturity_date` (ISO 8601 YYYY-MM-DD), `tenure_years`, `purpose`, `open_date` (ISO 8601 YYYY-MM-DD), `close_date` (ISO 8601 YYYY-MM-DD), `subscription_times`, and `description`.
10. THE Event_Classifier SHALL set any detail field to null when the corresponding information is not explicitly stated in the news text and cannot be directly derived from information that is explicitly stated; inferred or assumed values SHALL NOT be substituted for null.
11. WHEN the classified `event_type` does not match any of the nine recognised types, THE Event_Classifier SHALL set the `details` field to an empty object `{}` and SHALL log a warning containing the unrecognised event type value.

---

### Requirement 4: Event Batch Management

**User Story:** As a data engineer, I want each scrape run to be tracked as a named batch, so that I can audit which events were ingested together and when.

#### Acceptance Criteria

1. WHEN the Pipeline_Orchestrator begins processing a scrape run, THE Event_DB_Writer SHALL insert one row into `stoxscoop_dev.event_batches` with `batch_name` set to a string of the form `moneycontrol_<YYYY-MM-DD_HH-MM-SS>` using the UTC timestamp of the run start, `ingestion_source` set to `moneycontrol`, `started_at` set to the run start timestamp, and `total_events` set to 0.
2. WHEN the Pipeline_Orchestrator completes processing all stock entries in a scrape run, THE Event_DB_Writer SHALL update the batch row with `completed_at` set to the UTC completion timestamp and `total_events` set to the count of successfully inserted event rows.
3. IF the pipeline terminates before all stock entries are processed (due to an unhandled exception or explicit abort), THE Event_DB_Writer SHALL update the batch row with `completed_at` set to the UTC termination timestamp and `total_events` set to the count of event rows successfully inserted before termination.
4. THE Event_DB_Writer SHALL return the `event_batches.id` of the created batch row to the Pipeline_Orchestrator for use as `batch_id` in all event rows for that run.
5. IF the INSERT into `stoxscoop_dev.event_batches` fails, THE Event_DB_Writer SHALL raise a descriptive error containing the failure reason and SHALL halt the pipeline run before processing any stock entries.
6. IF the UPDATE to `stoxscoop_dev.event_batches` at completion or termination fails, THE Event_DB_Writer SHALL log an error containing the batch_id and failure reason and SHALL NOT re-raise the error or abort any already-completed processing.

---

### Requirement 5: Event and Detail Table Persistence

**User Story:** As a data engineer, I want classified events and their detail fields to be written atomically to the database, so that the events table and detail tables remain consistent.

#### Acceptance Criteria

1. WHEN a Classification_Result is available for a resolved stock, THE Event_DB_Writer SHALL insert one row into `stoxscoop_dev.events` with `stock_id`, `batch_id`, `event_type`, `event_subtype`, `signal_type`, `signal_reason`, `sentiment`, `priority`, `confidence_score` (numeric(4,3) or null), `confidence_model_version` (varchar(30) or null), `tags`, `title` (varchar(500)), `summary`, `event_date`, `source_url`, `source_name` (varchar(100)), `ingestion_source` set to `moneycontrol`, and `is_verified` set to false.
2. WHEN an event row is inserted, THE Event_DB_Writer SHALL insert one row into the Detail_Table corresponding to the `event_type` using the `event_id` of the newly inserted event row and the detail fields from the Classification_Result, according to the following mapping: `business` → `business_event_details`, `corporate_action` → `corporate_action_details`, `credit_rating` → `credit_rating_details`, `disclosure` → `disclosure_details`, `financials` → `financial_result_details`, `fundraising` → `fundraising_details`, `governance` → `governance_details`, `insider` → `insider_details`, `legal` → `legal_details`.
3. THE Event_DB_Writer SHALL execute the `events` insert and the Detail_Table insert within a single database transaction; IF either insert fails, THEN THE Event_DB_Writer SHALL roll back the transaction, log the error with the Scraped_Stock_Name, batch_id, event type, and failure description, and continue processing remaining entries.
4. WHEN the `event_date` cannot be extracted from the news text (i.e., the Classification_Result returns null, an empty string, or a non-parseable date for `event_date`), THE Event_DB_Writer SHALL use the `published_date` of the source article from `news_staging` as the `event_date`.
5. THE Event_DB_Writer SHALL set `source_url` to the article URL and `source_name` to `moneycontrol` for every inserted event row.
6. WHEN the `event_type` in the Classification_Result does not match any of the nine recognised types, THE Event_DB_Writer SHALL skip insertion for that entry, log a warning containing the Scraped_Stock_Name and the unrecognised event type, and continue processing remaining entries.
7. WHEN `confidence_score` is not null in the Classification_Result, THE Event_DB_Writer SHALL ensure `confidence_model_version` is also not null before inserting; IF `confidence_model_version` is null while `confidence_score` is not null, THEN THE Event_DB_Writer SHALL set both fields to null to satisfy the database CHECK constraint.

---

### Requirement 6: Unresolved and Failed Entry Handling

**User Story:** As a data engineer, I want unresolved stock names and classification failures to be logged and skipped gracefully, so that the pipeline does not abort on partial failures.

#### Acceptance Criteria

1. WHEN a Scraped_Stock_Name is classified as Unresolved, THE Pipeline_Orchestrator SHALL skip classification and DB insertion for that entry and SHALL log a warning containing the Scraped_Stock_Name, the article URL, and the reason for non-resolution.
2. WHEN an Event_Classifier failure occurs for a stock entry, THE Pipeline_Orchestrator SHALL skip DB insertion for that entry and SHALL log an error containing the Scraped_Stock_Name, the article URL, and the failure reason.
3. WHEN an Event_DB_Writer transaction fails for a stock entry, THE Pipeline_Orchestrator SHALL log the error, ensure the transaction has been rolled back (no partial data persists), and continue processing the remaining entries without retrying the failed entry.
4. THE Pipeline_Orchestrator SHALL produce a summary log at INFO level to the same log output as other pipeline messages at the end of each run containing: total stock entries processed, count resolved, count unresolved, count classified, count classification failures, count DB inserted, and count DB failures.

---

### Requirement 7: Pipeline Integration with Scrape Endpoint

**User Story:** As a data engineer, I want the event classification pipeline to run automatically after news_staging insertion, so that events are classified without requiring a separate manual trigger.

#### Acceptance Criteria

1. WHEN the `POST /scrape` endpoint completes `news_staging` insertion and `output_mode` is `database` or `both`, THE Pipeline_Orchestrator SHALL be invoked automatically once per successfully inserted `news_staging` record, passing the OutputRecord and its article URL.
2. WHEN the Pipeline_Orchestrator is invoked, it SHALL run as an asyncio background task so that the `POST /scrape` HTTP response is returned to the client before classification processing begins.
3. THE ScrapeResponse SHALL always include an `events_queued` integer field set to the total count of Stock_Entry items across all successfully inserted OutputRecords submitted to the pipeline; WHEN `output_mode` is `file` or no records were inserted, `events_queued` SHALL be 0.
4. IF the Pipeline_Orchestrator raises an unhandled exception, THE scrape endpoint SHALL log the error at ERROR level, completely suppress exception propagation to the HTTP client, and return a successful ScrapeResponse with `events_queued` set to 0 for any records not successfully submitted.
5. WHEN `output_mode` is `file`, THE Pipeline_Orchestrator SHALL NOT be invoked and `events_queued` SHALL be 0.

---

### Requirement 8: Section-to-Event-Type Hinting

**User Story:** As a data engineer, I want the article section name to be passed to the classifier as a hint, so that bulk deal and block deal sections are classified correctly without relying solely on news text.

#### Acceptance Criteria

1. WHEN a stock entry originates from a section whose name contains "Bulk Deal" or "Block Deal" (case-insensitive), THE Event_Classifier SHALL include the section name as a classification hint in the LLM prompt; WHERE the section name matches both patterns (e.g., "Bulk and Block Deals"), THE Event_Classifier SHALL include both `bulk_deal` and `block_deal` as candidate hint subtypes in the prompt.
2. WHEN a section hint is provided, THE Event_Classifier SHALL explicitly instruct the LLM in the prompt to prefer the hinted `event_type` and `event_subtype`; the LLM output SHALL use the hinted subtype unless the news text explicitly names a different event type (e.g., the text explicitly describes a board resignation or a court order), in which case the LLM MAY override the hint.
3. THE Event_Classifier SHALL pass the originating section name to the LLM prompt for all stock entries, not only bulk/block deal sections, to provide additional context for classification.

---

### Requirement 9: Idempotency and Duplicate Prevention

**User Story:** As a data engineer, I want re-running the pipeline on the same article to not create duplicate event records, so that the event tables remain clean after retries or reruns.

#### Acceptance Criteria

1. WHEN the Pipeline_Orchestrator processes a stock entry, THE Event_DB_Writer SHALL check whether an event row already exists with the same `stock_id`, `source_url`, `event_type`, `event_subtype`, and `event_date`; IF such a row exists, THEN THE Event_DB_Writer SHALL skip insertion and log an informational message containing the `stock_id`, `source_url`, `event_type`, `event_subtype`, and `event_date` of the duplicate.
2. WHEN `source_url` is null, THE Event_DB_Writer SHALL treat two rows as duplicates only when both `source_url` values are null AND `stock_id`, `event_type`, `event_subtype`, and `event_date` all match, using explicit NULL-equality semantics rather than SQL NULL comparison.
3. WHEN two concurrent pipeline runs attempt to insert the same event simultaneously, exactly one insert SHALL succeed and the other SHALL be treated as a duplicate and skipped without raising an unhandled error.
