"""Unit tests for Event_DB_Writer.

Requirements: 4.1–4.6, 5.1–5.7, 9.1–9.3
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, call
import pytest

from app.pipeline.classifier import ClassificationResult
from app.pipeline.event_writer import Event_DB_Writer, RECOGNISED_EVENT_TYPES
from moneycontrol_scraper.models import OutputRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_writer() -> tuple[Event_DB_Writer, MagicMock]:
    pool = MagicMock()
    writer = Event_DB_Writer(pool, "stoxscoop_dev")
    return writer, pool


def make_result(
    event_type: str = "business",
    event_subtype: str = "contract",
    confidence_score: Decimal | None = Decimal("0.850"),
    confidence_model_version: str | None = "gemini-2.5-flash-lite",
    title: str = "Test Event",
    details: dict | None = None,
) -> ClassificationResult:
    return ClassificationResult(
        status="ok",
        event_type=event_type,
        event_subtype=event_subtype,
        signal_type="bullish",
        sentiment="positive",
        priority="medium",
        title=title,
        summary="Test summary",
        event_date="2026-01-15",
        confidence_score=confidence_score,
        confidence_model_version=confidence_model_version,
        details=details or {"contract_type": "new"},
        tags=["test"],
        signal_reason="positive contract",
    )


def make_record(date: str | None = "2026-01-15") -> OutputRecord:
    return OutputRecord(
        date=date,
        url="https://example.com/article",
        sections={"Stocks to Watch": {"TestCo": "Some news text"}},
    )


def make_mock_conn(fetchrow_return=None, execute_return="UPDATE 1"):
    """Create a mock asyncpg connection."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    conn.execute = AsyncMock(return_value=execute_return)

    # Transaction context manager
    txn = AsyncMock()
    txn.__aenter__ = AsyncMock(return_value=txn)
    txn.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=txn)

    return conn


def make_mock_pool(conn):
    """Create a mock pool that yields the given connection."""
    pool = MagicMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=ctx)
    return pool


# ---------------------------------------------------------------------------
# _detail_table() tests
# ---------------------------------------------------------------------------

class TestDetailTable:
    def test_all_recognised_types_return_correct_table(self):
        expected = {
            "business": "business_event_details",
            "corporate_action": "corporate_action_details",
            "credit_rating": "credit_rating_details",
            "disclosure": "disclosure_details",
            "financials": "financial_result_details",
            "fundraising": "fundraising_details",
            "governance": "governance_details",
            "insider": "insider_details",
            "legal": "legal_details",
        }
        for event_type, table in expected.items():
            assert Event_DB_Writer._detail_table(event_type) == table

    def test_unrecognised_type_raises_value_error(self):
        with pytest.raises(ValueError, match="Unrecognised event_type"):
            Event_DB_Writer._detail_table("unknown_type")


# ---------------------------------------------------------------------------
# start_batch() tests
# ---------------------------------------------------------------------------

class TestStartBatch:
    @pytest.mark.asyncio
    async def test_start_batch_success_returns_id(self):
        """start_batch returns the batch_id from the DB."""
        conn = make_mock_conn(fetchrow_return={"id": 42})
        pool = make_mock_pool(conn)
        writer = Event_DB_Writer(pool, "stoxscoop_dev")

        batch_id = await writer.start_batch("moneycontrol_2026-01-15_10-00-00")
        assert batch_id == 42

    @pytest.mark.asyncio
    async def test_start_batch_failure_raises_runtime_error(self):
        """start_batch raises RuntimeError on DB failure."""
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(side_effect=Exception("DB error"))
        pool = make_mock_pool(conn)
        writer = Event_DB_Writer(pool, "stoxscoop_dev")

        with pytest.raises(RuntimeError, match="Failed to create event batch"):
            await writer.start_batch("test_batch")


# ---------------------------------------------------------------------------
# finish_batch() tests
# ---------------------------------------------------------------------------

class TestFinishBatch:
    @pytest.mark.asyncio
    async def test_finish_batch_success(self):
        """finish_batch executes UPDATE without raising."""
        conn = make_mock_conn()
        pool = make_mock_pool(conn)
        writer = Event_DB_Writer(pool, "stoxscoop_dev")

        # Should not raise
        await writer.finish_batch(42, 10)
        conn.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_finish_batch_failure_logs_but_does_not_raise(self, caplog):
        """finish_batch logs error on failure but does not re-raise."""
        conn = AsyncMock()
        conn.execute = AsyncMock(side_effect=Exception("DB error"))
        pool = make_mock_pool(conn)
        writer = Event_DB_Writer(pool, "stoxscoop_dev")

        import logging
        with caplog.at_level(logging.ERROR):
            await writer.finish_batch(42, 10)  # Should not raise

        assert any("Failed to update batch" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# write_event() tests
# ---------------------------------------------------------------------------

class TestWriteEvent:
    @pytest.mark.asyncio
    async def test_write_event_success_returns_true(self):
        """Successful insert returns True."""
        conn = make_mock_conn(fetchrow_return={"id": 100})
        pool = make_mock_pool(conn)
        writer = Event_DB_Writer(pool, "stoxscoop_dev")

        result = make_result()
        record = make_record()

        success = await writer.write_event(1, 10, result, record, record.url)
        assert success is True

    @pytest.mark.asyncio
    async def test_write_event_duplicate_returns_false(self, caplog):
        """ON CONFLICT DO NOTHING (fetchrow returns None) → returns False, logs info."""
        conn = make_mock_conn(fetchrow_return=None)
        pool = make_mock_pool(conn)
        writer = Event_DB_Writer(pool, "stoxscoop_dev")

        result = make_result()
        record = make_record()

        import logging
        with caplog.at_level(logging.INFO):
            success = await writer.write_event(1, 10, result, record, record.url)

        assert success is False
        assert any("Duplicate skipped" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_write_event_transaction_failure_returns_false(self, caplog):
        """Transaction failure → returns False, logs error."""
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(side_effect=Exception("transaction error"))

        txn = AsyncMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=None)
        conn.transaction = MagicMock(return_value=txn)

        pool = make_mock_pool(conn)
        writer = Event_DB_Writer(pool, "stoxscoop_dev")

        result = make_result()
        record = make_record()

        import logging
        with caplog.at_level(logging.ERROR):
            success = await writer.write_event(1, 10, result, record, record.url)

        assert success is False
        assert any("write_event failed" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_write_event_unrecognised_type_returns_false(self, caplog):
        """Unrecognised event_type → returns False, logs warning."""
        writer, _ = make_writer()
        result = make_result(event_type="unknown_type", event_subtype="something")
        record = make_record()

        import logging
        with caplog.at_level(logging.WARNING):
            success = await writer.write_event(1, 10, result, record, record.url)

        assert success is False
        assert any("Unrecognised event_type" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_confidence_score_not_null_model_version_null_both_nulled(self):
        """confidence_score not null + confidence_model_version null → both set to null."""
        conn = make_mock_conn(fetchrow_return={"id": 100})
        pool = make_mock_pool(conn)
        writer = Event_DB_Writer(pool, "stoxscoop_dev")

        result = make_result(
            confidence_score=Decimal("0.850"),
            confidence_model_version=None,  # null version with non-null score
        )
        record = make_record()

        success = await writer.write_event(1, 10, result, record, record.url)
        assert success is True

        # Verify the INSERT was called with None for both confidence fields
        call_args = conn.fetchrow.call_args
        args = call_args[0]
        # Find confidence_score and confidence_model_version in the positional args
        # The SQL has $9=confidence_score, $10=confidence_model_version
        # args[0] is the SQL, args[1..] are the values
        # confidence_score is at index 9 (0-based: args[9])
        assert args[9] is None   # confidence_score
        assert args[10] is None  # confidence_model_version

    def test_build_event_params_source_name_always_moneycontrol(self):
        """source_name is always 'moneycontrol'."""
        writer, _ = make_writer()
        result = make_result()
        record = make_record()

        params = writer._build_event_params(result, 1, 1, record.url, record.date)
        assert params["source_name"] == "moneycontrol"
        assert params["ingestion_source"] == "scraper"
        assert params["is_verified"] is False
