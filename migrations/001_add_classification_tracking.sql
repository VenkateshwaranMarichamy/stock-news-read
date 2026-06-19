-- Idempotent migration: add classification-tracking columns to news_staging
-- Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8

ALTER TABLE stoxscoop_dev.news_staging
    ADD COLUMN IF NOT EXISTS event_batch_id integer
        REFERENCES stoxscoop_dev.event_batches(id) ON DELETE SET NULL;

ALTER TABLE stoxscoop_dev.news_staging
    ADD COLUMN IF NOT EXISTS stocks_loaded integer NOT NULL DEFAULT 0;

ALTER TABLE stoxscoop_dev.news_staging
    ADD COLUMN IF NOT EXISTS stocks_failed integer NOT NULL DEFAULT 0;

ALTER TABLE stoxscoop_dev.news_staging
    ADD COLUMN IF NOT EXISTS classification_status varchar(20) NOT NULL DEFAULT 'pending';

-- Backfill pre-existing rows where classification_status was added as NOT NULL DEFAULT 'pending'
-- but any rows that somehow have NULL (e.g. from a partial prior migration) are set to defaults
UPDATE stoxscoop_dev.news_staging
SET classification_status = 'pending',
    stocks_loaded = 0,
    stocks_failed = 0
WHERE classification_status IS NULL;

-- Check constraints (idempotent via IF NOT EXISTS)
ALTER TABLE stoxscoop_dev.news_staging
    ADD CONSTRAINT IF NOT EXISTS chk_classification_status
        CHECK (classification_status IN ('pending', 'partial', 'complete', 'failed'));

ALTER TABLE stoxscoop_dev.news_staging
    ADD CONSTRAINT IF NOT EXISTS chk_stocks_non_negative
        CHECK (stocks_loaded >= 0 AND stocks_failed >= 0);
