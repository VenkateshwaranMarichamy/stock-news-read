"""Pipeline_Orchestrator: coordinates resolver, classifier, and writer.

Requirements: 4.1–4.4, 6.1–6.4
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from moneycontrol_scraper.models import OutputRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PipelineSummary:
    """Summary counts for a single pipeline run."""

    total: int
    resolved: int
    unresolved: int
    classified: int
    classification_failures: int
    db_inserted: int
    db_failures: int


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Pipeline_Orchestrator:
    """Coordinates Stock_Name_Resolver, Event_Classifier, and Event_DB_Writer."""

    def __init__(self, resolver, classifier, writer) -> None:
        self._resolver = resolver
        self._classifier = classifier
        self._writer = writer

    async def run(self, record: OutputRecord, batch_name: str) -> tuple[PipelineSummary, int]:
        """Run the full classification pipeline for a single OutputRecord.

        Returns (PipelineSummary, batch_id) so callers can use the batch_id
        to update news_staging classification columns.
        """
        batch_id = await self._writer.start_batch(batch_name)

        total = 0
        resolved = 0
        unresolved = 0
        classified = 0
        classification_failures = 0
        db_inserted = 0
        db_failures = 0

        try:
            for section_name, stocks in record.sections.items():
                for stock_name, news_text in stocks.items():
                    total += 1

                    # Step 3: resolve
                    stock_id = self._resolver.resolve(stock_name)
                    if stock_id is None:
                        unresolved += 1
                        logger.warning(
                            "Unresolved stock '%s' in article %s — skipping classification",
                            stock_name, record.url,
                        )
                        continue

                    resolved += 1

                    # Step 4: classify
                    result = await self._classifier.classify(
                        news_text, section_name, stock_name
                    )
                    if result.status != "ok":
                        classification_failures += 1
                        if result.status == "failed":
                            logger.error(
                                "Classification failed for '%s' in article %s",
                                stock_name, record.url,
                            )
                        continue

                    classified += 1

                    # Step 5: write
                    success = await self._writer.write_event(
                        batch_id, stock_id, result, record, record.url
                    )
                    if success:
                        db_inserted += 1
                    else:
                        db_failures += 1

        finally:
            await self._writer.finish_batch(batch_id, db_inserted)

        summary = PipelineSummary(
            total=total,
            resolved=resolved,
            unresolved=unresolved,
            classified=classified,
            classification_failures=classification_failures,
            db_inserted=db_inserted,
            db_failures=db_failures,
        )
        logger.info(
            "Pipeline summary for %s: total=%d resolved=%d unresolved=%d "
            "classified=%d classification_failures=%d db_inserted=%d db_failures=%d",
            record.url,
            summary.total, summary.resolved, summary.unresolved,
            summary.classified, summary.classification_failures,
            summary.db_inserted, summary.db_failures,
        )
        return summary, batch_id

    async def run_with_batch_id(
        self, record: OutputRecord, batch_id: int
    ) -> tuple[PipelineSummary, int]:
        """Run the classification pipeline using an existing batch_id (retry path).

        Identical to run() but skips start_batch — uses the provided batch_id
        directly. Still calls finish_batch in the finally block to keep
        event_batches.total_events and completed_at current.

        Requirements: 3.2, 4.1
        """
        total = 0
        resolved = 0
        unresolved = 0
        classified = 0
        classification_failures = 0
        db_inserted = 0
        db_failures = 0

        try:
            for section_name, stocks in record.sections.items():
                for stock_name, news_text in stocks.items():
                    total += 1

                    stock_id = self._resolver.resolve(stock_name)
                    if stock_id is None:
                        unresolved += 1
                        logger.warning(
                            "Unresolved stock '%s' in article %s — skipping classification",
                            stock_name, record.url,
                        )
                        continue

                    resolved += 1

                    result = await self._classifier.classify(
                        news_text, section_name, stock_name
                    )
                    if result.status != "ok":
                        classification_failures += 1
                        if result.status == "failed":
                            logger.error(
                                "Classification failed for '%s' in article %s",
                                stock_name, record.url,
                            )
                        continue

                    classified += 1

                    success = await self._writer.write_event(
                        batch_id, stock_id, result, record, record.url
                    )
                    if success:
                        db_inserted += 1
                    else:
                        db_failures += 1

        finally:
            await self._writer.finish_batch(batch_id, db_inserted)

        summary = PipelineSummary(
            total=total,
            resolved=resolved,
            unresolved=unresolved,
            classified=classified,
            classification_failures=classification_failures,
            db_inserted=db_inserted,
            db_failures=db_failures,
        )
        logger.info(
            "Pipeline summary (retry) for %s: total=%d resolved=%d unresolved=%d "
            "classified=%d classification_failures=%d db_inserted=%d db_failures=%d",
            record.url,
            summary.total, summary.resolved, summary.unresolved,
            summary.classified, summary.classification_failures,
            summary.db_inserted, summary.db_failures,
        )
        return summary, batch_id
