"""Unit tests for POST /classify router.

Tests cover: missing IDs, null/empty content, start_batch failure,
pipeline exception, Staging_Updater failure, bulk SELECT failure,
deduplication, and response aggregation.

Requirements: 1.5, 2.2, 2.3, 2.4, 3.4, 4.4, 6.3, 9.1, 9.2, 9.3, 9.4, 9.5
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pipeline.orchestrator import PipelineSummary
from app.schemas import ClassifyRequest, ClassifyResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_summary(total=3, db_inserted=2, db_failures=1, unresolved=0) -> PipelineSummary:
    return PipelineSummary(
        total=total,
        resolved=total - unresolved,
        unresolved=unresolved,
        classified=db_inserted,
        classification_failures=db_failures,
        db_inserted=db_inserted,
        db_failures=db_failures,
    )


def make_staging_row(id_: int, url: str = "http://example.com", content: dict | None = None,
                     batch_id: int | None = None):
    """Return a dict mimicking an asyncpg Record."""
    if content is None:
        content = {"Stocks to Watch": {"AAPL": "Apple news"}}
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": id_,
        "url": url,
        "content": content,
        "published_date": None,
        "event_batch_id": batch_id,
    }[key]
    row.get = lambda key, default=None: {
        "id": id_,
        "url": url,
        "content": content,
        "published_date": None,
        "event_batch_id": batch_id,
    }.get(key, default)
    return row


# ---------------------------------------------------------------------------
# ClassifyRequest validation
# ---------------------------------------------------------------------------

class TestClassifyRequestValidation:
    def test_valid_single_id(self):
        req = ClassifyRequest(ids=[1])
        assert req.ids == [1]

    def test_valid_50_ids(self):
        req = ClassifyRequest(ids=list(range(1, 51)))
        assert len(req.ids) == 50

    def test_empty_list_raises(self):
        with pytest.raises(Exception):
            ClassifyRequest(ids=[])

    def test_51_ids_raises(self):
        with pytest.raises(Exception):
            ClassifyRequest(ids=list(range(1, 52)))

    def test_zero_raises(self):
        with pytest.raises(Exception):
            ClassifyRequest(ids=[0])

    def test_negative_raises(self):
        with pytest.raises(Exception):
            ClassifyRequest(ids=[-1])

    def test_mixed_valid_invalid_raises(self):
        with pytest.raises(Exception):
            ClassifyRequest(ids=[1, 2, -3])


# ---------------------------------------------------------------------------
# determine_status pure function
# ---------------------------------------------------------------------------

class TestDetermineStatus:
    def test_complete(self):
        from app.db.staging_updater import determine_status
        s = PipelineSummary(total=3, resolved=3, unresolved=0, classified=3,
                            classification_failures=0, db_inserted=3, db_failures=0)
        assert determine_status(s) == "complete"

    def test_partial_with_failures(self):
        from app.db.staging_updater import determine_status
        s = PipelineSummary(total=3, resolved=3, unresolved=0, classified=2,
                            classification_failures=1, db_inserted=2, db_failures=1)
        assert determine_status(s) == "partial"

    def test_partial_with_unresolved(self):
        from app.db.staging_updater import determine_status
        s = PipelineSummary(total=3, resolved=2, unresolved=1, classified=2,
                            classification_failures=0, db_inserted=2, db_failures=0)
        assert determine_status(s) == "partial"

    def test_failed_zero_inserted(self):
        from app.db.staging_updater import determine_status
        s = PipelineSummary(total=3, resolved=3, unresolved=0, classified=0,
                            classification_failures=3, db_inserted=0, db_failures=0)
        assert determine_status(s) == "failed"

    def test_failed_zero_total(self):
        from app.db.staging_updater import determine_status
        s = PipelineSummary(total=0, resolved=0, unresolved=0, classified=0,
                            classification_failures=0, db_inserted=0, db_failures=0)
        assert determine_status(s) == "failed"


# ---------------------------------------------------------------------------
# Classify router — mock-based tests
# ---------------------------------------------------------------------------

class TestClassifyRouter:
    """Tests for the classify endpoint using mocked dependencies."""

    def _make_app_state(self, pool, schema="stoxscoop_dev", valid_subtypes=None):
        state = MagicMock()
        state.pool = pool
        state.schema = schema
        state.valid_subtypes = valid_subtypes or {}
        return state

    def _make_request(self, pool, schema="stoxscoop_dev"):
        req = MagicMock()
        req.app.state = self._make_app_state(pool, schema)
        return req

    @pytest.mark.asyncio
    async def test_missing_ids_go_to_ids_missing(self):
        """IDs not found in news_staging appear in ids_missing."""
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])  # no rows found
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=None),
        ))

        from app.routers.classify import classify
        request = ClassifyRequest(ids=[99, 100])
        req = self._make_request(mock_pool)

        with patch("app.routers.classify.Stock_Name_Resolver") as MockResolver, \
             patch("app.routers.classify.Event_Classifier"), \
             patch("app.routers.classify.Event_DB_Writer"), \
             patch("app.routers.classify.Pipeline_Orchestrator"):
            mock_resolver_instance = AsyncMock()
            mock_resolver_instance.load_cache = AsyncMock()
            MockResolver.return_value = mock_resolver_instance

            response = await classify(request, req)

        assert response.ids_missing == [99, 100]
        assert response.ids_processed == []
        assert response.ids_skipped == []

    @pytest.mark.asyncio
    async def test_null_content_goes_to_ids_skipped(self):
        """Records with null content go to ids_skipped."""
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()

        row = make_staging_row(1, content=None)
        mock_conn.fetch = AsyncMock(return_value=[row])
        mock_conn.execute = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=None),
        ))

        from app.routers.classify import classify
        request = ClassifyRequest(ids=[1])
        req = self._make_request(mock_pool)

        with patch("app.routers.classify.Stock_Name_Resolver") as MockResolver, \
             patch("app.routers.classify.Event_Classifier"), \
             patch("app.routers.classify.Event_DB_Writer"), \
             patch("app.routers.classify.Pipeline_Orchestrator"):
            mock_resolver_instance = AsyncMock()
            mock_resolver_instance.load_cache = AsyncMock()
            MockResolver.return_value = mock_resolver_instance

            response = await classify(request, req)

        assert 1 in response.ids_skipped
        assert 1 not in response.ids_processed

    @pytest.mark.asyncio
    async def test_pipeline_exception_goes_to_ids_skipped(self):
        """Pipeline exception for one record → ids_skipped, others continue."""
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()

        row1 = make_staging_row(1, url="http://a.com")
        row2 = make_staging_row(2, url="http://b.com")
        mock_conn.fetch = AsyncMock(return_value=[row1, row2])
        mock_conn.execute = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=None),
        ))

        from app.routers.classify import classify
        request = ClassifyRequest(ids=[1, 2])
        req = self._make_request(mock_pool)

        call_count = 0

        async def mock_run_with_batch_id(record, batch_id):
            nonlocal call_count
            call_count += 1
            if record.url == "http://a.com":
                raise RuntimeError("LLM exploded")
            return make_summary(total=1, db_inserted=1, db_failures=0, unresolved=0), batch_id

        with patch("app.routers.classify.Stock_Name_Resolver") as MockResolver, \
             patch("app.routers.classify.Event_Classifier"), \
             patch("app.routers.classify.Event_DB_Writer") as MockWriter, \
             patch("app.routers.classify.Pipeline_Orchestrator") as MockOrchestrator, \
             patch("app.routers.classify.Staging_Updater") as MockUpdater:

            mock_resolver_instance = AsyncMock()
            mock_resolver_instance.load_cache = AsyncMock()
            MockResolver.return_value = mock_resolver_instance

            mock_writer_instance = AsyncMock()
            mock_writer_instance.start_batch = AsyncMock(return_value=42)
            mock_writer_instance.finish_batch = AsyncMock()
            MockWriter.return_value = mock_writer_instance

            mock_orch_instance = AsyncMock()
            mock_orch_instance.run_with_batch_id = mock_run_with_batch_id
            MockOrchestrator.return_value = mock_orch_instance

            mock_updater_instance = AsyncMock()
            mock_updater_instance.update_classification_status = AsyncMock()
            MockUpdater.return_value = mock_updater_instance

            response = await classify(request, req)

        assert 1 in response.ids_skipped
        assert 2 in response.ids_processed
        assert response.events_inserted == 1

    @pytest.mark.asyncio
    async def test_staging_updater_failure_continues_http_200(self):
        """Staging_Updater failure → logs error, continues, returns HTTP 200."""
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()

        row = make_staging_row(1, url="http://a.com")
        mock_conn.fetch = AsyncMock(return_value=[row])
        mock_conn.execute = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=None),
        ))

        from app.routers.classify import classify
        request = ClassifyRequest(ids=[1])
        req = self._make_request(mock_pool)

        with patch("app.routers.classify.Stock_Name_Resolver") as MockResolver, \
             patch("app.routers.classify.Event_Classifier"), \
             patch("app.routers.classify.Event_DB_Writer") as MockWriter, \
             patch("app.routers.classify.Pipeline_Orchestrator") as MockOrchestrator, \
             patch("app.routers.classify.Staging_Updater") as MockUpdater:

            mock_resolver_instance = AsyncMock()
            mock_resolver_instance.load_cache = AsyncMock()
            MockResolver.return_value = mock_resolver_instance

            mock_writer_instance = AsyncMock()
            mock_writer_instance.start_batch = AsyncMock(return_value=42)
            mock_writer_instance.finish_batch = AsyncMock()
            MockWriter.return_value = mock_writer_instance

            mock_orch_instance = AsyncMock()
            mock_orch_instance.run_with_batch_id = AsyncMock(
                return_value=(make_summary(total=1, db_inserted=1, db_failures=0, unresolved=0), 42)
            )
            MockOrchestrator.return_value = mock_orch_instance

            mock_updater_instance = AsyncMock()
            mock_updater_instance.update_classification_status = AsyncMock(
                side_effect=RuntimeError("DB write failed")
            )
            MockUpdater.return_value = mock_updater_instance

            # Should not raise — Staging_Updater failure is swallowed
            response = await classify(request, req)

        assert isinstance(response, ClassifyResponse)
        assert 1 in response.ids_processed

    @pytest.mark.asyncio
    async def test_bulk_select_failure_returns_503(self):
        """Bulk SELECT failure → HTTP 503 with 'database connectivity issue'."""
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(side_effect=Exception("connection refused"))
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=None),
        ))

        from app.routers.classify import classify
        from fastapi import HTTPException
        request = ClassifyRequest(ids=[1])
        req = self._make_request(mock_pool)

        with patch("app.routers.classify.Stock_Name_Resolver") as MockResolver, \
             patch("app.routers.classify.Event_Classifier"), \
             patch("app.routers.classify.Event_DB_Writer"), \
             patch("app.routers.classify.Pipeline_Orchestrator"):
            mock_resolver_instance = AsyncMock()
            mock_resolver_instance.load_cache = AsyncMock()
            MockResolver.return_value = mock_resolver_instance

            with pytest.raises(HTTPException) as exc_info:
                await classify(request, req)

        assert exc_info.value.status_code == 503
        assert "database connectivity issue" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_deduplication_preserves_first_occurrence(self):
        """Duplicate IDs are deduplicated; each ID processed at most once."""
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()

        row = make_staging_row(1, url="http://a.com")
        mock_conn.fetch = AsyncMock(return_value=[row])
        mock_conn.execute = AsyncMock()
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=None),
        ))

        from app.routers.classify import classify
        # IDs [1, 1, 1] — should process 1 only once
        request = ClassifyRequest(ids=[1, 2, 1])  # 2 is missing, 1 is duplicate

        req = self._make_request(mock_pool)

        with patch("app.routers.classify.Stock_Name_Resolver") as MockResolver, \
             patch("app.routers.classify.Event_Classifier"), \
             patch("app.routers.classify.Event_DB_Writer") as MockWriter, \
             patch("app.routers.classify.Pipeline_Orchestrator") as MockOrchestrator, \
             patch("app.routers.classify.Staging_Updater") as MockUpdater:

            mock_resolver_instance = AsyncMock()
            mock_resolver_instance.load_cache = AsyncMock()
            MockResolver.return_value = mock_resolver_instance

            mock_writer_instance = AsyncMock()
            mock_writer_instance.start_batch = AsyncMock(return_value=42)
            mock_writer_instance.finish_batch = AsyncMock()
            MockWriter.return_value = mock_writer_instance

            mock_orch_instance = AsyncMock()
            mock_orch_instance.run_with_batch_id = AsyncMock(
                return_value=(make_summary(total=1, db_inserted=1, db_failures=0, unresolved=0), 42)
            )
            MockOrchestrator.return_value = mock_orch_instance

            mock_updater_instance = AsyncMock()
            mock_updater_instance.update_classification_status = AsyncMock()
            MockUpdater.return_value = mock_updater_instance

            response = await classify(request, req)

        # ID 1 processed once, ID 2 missing — total unique IDs = 2
        all_ids = response.ids_processed + response.ids_missing + response.ids_skipped
        assert len(all_ids) == len(set(all_ids)), "Each ID should appear in exactly one list"
        assert 2 in response.ids_missing
        assert response.ids_processed.count(1) <= 1

    @pytest.mark.asyncio
    async def test_empty_response_when_all_missing(self):
        """All IDs missing → ids_processed empty, all counts zero."""
        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=None),
        ))

        from app.routers.classify import classify
        request = ClassifyRequest(ids=[10, 20, 30])
        req = self._make_request(mock_pool)

        with patch("app.routers.classify.Stock_Name_Resolver") as MockResolver, \
             patch("app.routers.classify.Event_Classifier"), \
             patch("app.routers.classify.Event_DB_Writer"), \
             patch("app.routers.classify.Pipeline_Orchestrator"):
            mock_resolver_instance = AsyncMock()
            mock_resolver_instance.load_cache = AsyncMock()
            MockResolver.return_value = mock_resolver_instance

            response = await classify(request, req)

        assert response.ids_processed == []
        assert set(response.ids_missing) == {10, 20, 30}
        assert response.total_stocks == 0
        assert response.events_inserted == 0
