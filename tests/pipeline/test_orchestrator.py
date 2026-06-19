"""Unit tests for Pipeline_Orchestrator.

Requirements: 6.1–6.4
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.pipeline.classifier import ClassificationResult
from app.pipeline.orchestrator import Pipeline_Orchestrator, PipelineSummary
from moneycontrol_scraper.models import OutputRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_record(sections: dict | None = None) -> OutputRecord:
    if sections is None:
        sections = {
            "Stocks to Watch": {
                "TCS": "TCS wins big contract",
                "Infosys": "Infosys reports results",
            }
        }
    return OutputRecord(
        date="2026-01-15",
        url="https://example.com/article",
        sections=sections,
    )


def make_ok_result(event_type: str = "business") -> ClassificationResult:
    return ClassificationResult(
        status="ok",
        event_type=event_type,
        event_subtype="contract",
        signal_type="bullish",
        sentiment="positive",
        priority="medium",
        title="Test",
        summary="Test summary",
        event_date="2026-01-15",
        confidence_score=Decimal("0.850"),
        confidence_model_version="model-v1",
        details={"contract_type": "new"},
        tags=["test"],
        signal_reason="positive",
    )


def make_failed_result() -> ClassificationResult:
    return ClassificationResult(
        status="failed",
        event_type=None, event_subtype=None, signal_type=None,
        sentiment=None, priority=None, title=None, summary=None,
        event_date=None, confidence_score=None, confidence_model_version=None,
    )


def make_orchestrator(
    resolver_resolve_side_effect=None,
    classifier_classify_side_effect=None,
    writer_write_event_side_effect=None,
    start_batch_return=1,
) -> Pipeline_Orchestrator:
    resolver = MagicMock()
    classifier = AsyncMock()
    writer = AsyncMock()

    writer.start_batch = AsyncMock(return_value=start_batch_return)
    writer.finish_batch = AsyncMock(return_value=None)

    if resolver_resolve_side_effect is not None:
        resolver.resolve = MagicMock(side_effect=resolver_resolve_side_effect)
    else:
        resolver.resolve = MagicMock(return_value=1)

    if classifier_classify_side_effect is not None:
        classifier.classify = AsyncMock(side_effect=classifier_classify_side_effect)
    else:
        classifier.classify = AsyncMock(return_value=make_ok_result())

    if writer_write_event_side_effect is not None:
        writer.write_event = AsyncMock(side_effect=writer_write_event_side_effect)
    else:
        writer.write_event = AsyncMock(return_value=True)

    return Pipeline_Orchestrator(resolver, classifier, writer)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPipelineOrchestratorRun:
    @pytest.mark.asyncio
    async def test_happy_path_all_succeed(self):
        """Full happy path: resolver → classifier → writer all succeed."""
        orch = make_orchestrator()
        record = make_record()

        summary, _batch_id = await orch.run(record, "test_batch")

        assert summary.total == 2
        assert summary.resolved == 2
        assert summary.unresolved == 0
        assert summary.classified == 2
        assert summary.classification_failures == 0
        assert summary.db_inserted == 2
        assert summary.db_failures == 0

    @pytest.mark.asyncio
    async def test_unresolved_stock_skips_classifier_and_writer(self):
        """Unresolved stock: classifier and writer not called for that entry."""
        resolve_results = [None, 1]  # first stock unresolved, second resolved
        orch = make_orchestrator(resolver_resolve_side_effect=resolve_results)
        record = make_record()

        summary, _batch_id = await orch.run(record, "test_batch")

        assert summary.total == 2
        assert summary.resolved == 1
        assert summary.unresolved == 1
        assert summary.classified == 1
        assert summary.db_inserted == 1

    @pytest.mark.asyncio
    async def test_classifier_failure_skips_writer(self):
        """Classifier failure: writer not called for that entry."""
        classify_results = [make_failed_result(), make_ok_result()]
        orch = make_orchestrator(classifier_classify_side_effect=classify_results)
        record = make_record()

        summary, _batch_id = await orch.run(record, "test_batch")

        assert summary.total == 2
        assert summary.resolved == 2
        assert summary.classified == 1
        assert summary.classification_failures == 1
        assert summary.db_inserted == 1

    @pytest.mark.asyncio
    async def test_writer_failure_continues_to_next_entry(self):
        """Writer failure: continues to next entry, counts as db_failure."""
        write_results = [False, True]
        orch = make_orchestrator(writer_write_event_side_effect=write_results)
        record = make_record()

        summary, _batch_id = await orch.run(record, "test_batch")

        assert summary.total == 2
        assert summary.db_inserted == 1
        assert summary.db_failures == 1

    @pytest.mark.asyncio
    async def test_summary_counts_correct_for_mixed_outcomes(self):
        """Mixed outcomes: all counts are correct."""
        # 3 stocks: 1 unresolved, 1 classified+inserted, 1 classified+failed
        record = make_record(sections={
            "Section A": {
                "Stock1": "news1",
                "Stock2": "news2",
                "Stock3": "news3",
            }
        })

        resolve_results = [None, 1, 2]
        classify_results = [make_ok_result(), make_ok_result()]
        write_results = [True, False]

        orch = make_orchestrator(
            resolver_resolve_side_effect=resolve_results,
            classifier_classify_side_effect=classify_results,
            writer_write_event_side_effect=write_results,
        )

        summary, _batch_id = await orch.run(record, "test_batch")

        assert summary.total == 3
        assert summary.resolved == 2
        assert summary.unresolved == 1
        assert summary.classified == 2
        assert summary.classification_failures == 0
        assert summary.db_inserted == 1
        assert summary.db_failures == 1

    @pytest.mark.asyncio
    async def test_finish_batch_called_in_finally(self):
        """finish_batch is always called, even if an exception occurs."""
        orch = make_orchestrator()
        record = make_record()

        # Make classifier raise an unexpected exception
        orch._classifier.classify = AsyncMock(side_effect=RuntimeError("unexpected"))

        with pytest.raises(RuntimeError):
            await orch.run(record, "test_batch")

        # finish_batch should still have been called
        orch._writer.finish_batch.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_sections_returns_zero_summary(self):
        """Empty sections → all counts are 0."""
        orch = make_orchestrator()
        record = make_record(sections={})

        summary, _batch_id = await orch.run(record, "test_batch")

        assert summary.total == 0
        assert summary.resolved == 0
        assert summary.unresolved == 0
        assert summary.classified == 0
        assert summary.db_inserted == 0

    @pytest.mark.asyncio
    async def test_summary_invariants(self):
        """resolved + unresolved == total; db_inserted + db_failures <= classified."""
        orch = make_orchestrator()
        record = make_record()

        summary, _batch_id = await orch.run(record, "test_batch")

        assert summary.resolved + summary.unresolved == summary.total
        assert summary.classified + summary.classification_failures + summary.unresolved == summary.total
        assert summary.db_inserted + summary.db_failures <= summary.classified
