"""Event_Classifier: LLM-based event classification for stock news.

Requirements: 2.1–2.10, 3.1–3.9, 8.1–8.3
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re as _re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ClassificationResult:
    """Structured output of Event_Classifier for a single stock news entry."""

    status: Literal["ok", "unclassified", "failed"]
    event_type: str | None
    event_subtype: str | None
    signal_type: str | None
    sentiment: str | None
    priority: str | None
    title: str | None          # truncated to 500 chars
    summary: str | None
    event_date: str | None     # ISO 8601 YYYY-MM-DD or None
    confidence_score: Decimal | None   # numeric(4,3) or None
    confidence_model_version: str | None
    details: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    signal_reason: str | None = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIRED_JSON_FIELDS = [
    "event_type",
    "event_subtype",
    "signal_type",
    "sentiment",
    "priority",
    "title",
    "summary",
    "event_date",
    "confidence_score",
]

VALID_EVENT_TYPES = {
    "corporate_action",
    "disclosure",
    "insider",
    "business",
    "governance",
    "credit_rating",
    "financials",
    "fundraising",
    "legal",
}

VALID_SIGNAL_TYPES = {"bullish", "bearish", "neutral", "mixed"}
VALID_SENTIMENTS = {"positive", "negative", "neutral", "mixed"}
VALID_PRIORITIES = {"low", "medium", "high", "critical"}

# Detail fields per event_type (for prompt construction)
DETAIL_FIELDS: dict[str, list[str]] = {
    "business": [
        "contract_type", "client_name", "client_sector", "contract_value",
        "capex_amount", "currency", "duration_years", "geography", "project_name",
        "jv_partner", "ownership_pct", "is_repeat_order", "product_name",
        "target_geography", "target_segment", "campaign_name", "target_revenue",
        "target_timeline", "description",
    ],
    "disclosure": [
        "investor_category", "investor_name", "investor_country", "transaction_type",
        "transaction_mode", "shares_transacted", "price_per_share", "transaction_value",
        "currency", "stake_before", "stake_after", "transaction_date", "exchange",
        "description",
    ],
    "insider": [
        "person_name", "designation", "relationship", "transaction_type",
        "shares_transacted", "price_per_share", "transaction_value", "currency",
        "stake_before", "stake_after", "pledge_percentage", "sebi_disclosure_date",
        "transaction_date", "description",
    ],
    "corporate_action": [
        "record_date", "effective_date", "ratio", "amount_per_share", "total_size",
        "currency", "target_company", "swap_ratio", "offer_price",
        "stake_acquired_pct", "resulting_stake_pct", "shares_transacted", "description",
    ],
    "financials": [
        "period_quarter", "period_year", "revenue", "revenue_yoy_pct", "ebitda",
        "ebitda_margin", "pat", "pat_yoy_pct", "eps", "beat_miss",
        "guidance_revenue", "guidance_margin", "currency", "key_highlight", "description",
    ],
    "governance": [
        "person_name", "designation", "change_type", "effective_date", "reason",
        "regulator", "action_type", "penalty_amount", "currency", "meeting_date",
        "agenda_summary", "description",
    ],
    "legal": [
        "forum", "case_number", "counterparty", "demand_amount", "penalty_amount",
        "currency", "company_stance", "outcome", "order_date", "next_hearing_date",
        "contingent_liability", "description",
    ],
    "credit_rating": [
        "agency", "instrument_type", "instrument_name", "rating_before", "rating_after",
        "outlook_before", "outlook_after", "rated_amount", "currency", "rationale",
        "rating_date", "description",
    ],
    "fundraising": [
        "issue_size", "currency", "price_per_share", "number_of_shares",
        "allottee_name", "allottee_category", "coupon_rate", "maturity_date",
        "tenure_years", "purpose", "open_date", "close_date", "subscription_times",
        "description",
    ],
}


# Re-export shim: _RETRY_DELAY_RE and _parse_retry_delay have moved to provider_manager.py.
# These names are kept here so that any existing imports or tests that reference
# classifier._parse_retry_delay / classifier._RETRY_DELAY_RE continue to work unchanged.
from app.pipeline.provider_manager import (  # noqa: E402
    ConfigurationError,
    LLM_Provider_Manager,
    ProviderConfig,
    _RETRY_DELAY_RE,
    _parse_retry_delay as _parse_retry_delay_impl,
)


def _parse_retry_delay(error_text: str, default: float = 60.0) -> float:
    """Shim: delegates to provider_manager._parse_retry_delay.

    The canonical implementation lives in provider_manager.py.
    This wrapper preserves the original signature (with `default`) for
    backward compatibility with any existing callers or tests.
    """
    result = _parse_retry_delay_impl(error_text)
    return result if result is not None else default


def _make_default_provider_manager() -> LLM_Provider_Manager:
    """Build a single-provider LLM_Provider_Manager from environment variables.

    Reads LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL from the environment.
    Raises ConfigurationError if any variable is absent or empty.

    Requirements: 1.3, 5.2
    """
    llm_base_url = os.environ.get("LLM_BASE_URL", "")
    llm_api_key = os.environ.get("LLM_API_KEY", "")
    llm_model = os.environ.get("LLM_MODEL", "")

    if not llm_base_url or not llm_api_key or not llm_model:
        raise ConfigurationError(
            "LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL environment variables must be set"
        )

    return LLM_Provider_Manager(
        providers=[
            ProviderConfig(
                name="default",
                base_url=llm_base_url,
                api_key=llm_api_key,
                model=llm_model,
            )
        ],
        strategy="priority",
    )


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class Event_Classifier:
    """Classifies stock news text into structured event records using an LLM."""

    def __init__(self, valid_subtypes: dict[str, set[str]], provider_manager: LLM_Provider_Manager | None = None) -> None:
        """
        Args:
            valid_subtypes: {event_type: {subtype_code, ...}} loaded from event_subtypes table.
            provider_manager: Optional LLM provider manager. When None, builds a default
                              single-provider manager from LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
                              environment variables (backward-compatible behaviour).
        """
        self._valid_subtypes = valid_subtypes
        if provider_manager is None:
            provider_manager = _make_default_provider_manager()
        self._provider_manager = provider_manager

    # ------------------------------------------------------------------
    # Pure helpers (testable without LLM)
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        news_text: str,
        section_name: str,
        stock_name: str,
    ) -> str:
        """Build the full prompt string (system + user) for the LLM.

        Returns a single string containing both system and user parts,
        separated by a clear delimiter, so tests can inspect the full content.
        """
        # Build event_subtypes reference table
        subtypes_lines: list[str] = []
        for et, codes in sorted(self._valid_subtypes.items()):
            for code in sorted(codes):
                subtypes_lines.append(f"  {et} / {code}")
        subtypes_ref = "\n".join(subtypes_lines)

        # Build detail field schema section
        detail_schema_lines: list[str] = []
        for et, fields_list in sorted(DETAIL_FIELDS.items()):
            detail_schema_lines.append(f"\n{et}:")
            for f_name in fields_list:
                detail_schema_lines.append(f"  - {f_name}")
        detail_schema = "\n".join(detail_schema_lines)

        system_message = f"""You are a financial event classifier for Indian stock market news.
Classify the provided news text into a structured JSON event record.

Return ONLY a valid JSON object with these fields:
  event_type: one of [corporate_action, disclosure, insider, business, governance, credit_rating, financials, fundraising, legal]
  event_subtype: valid subtype_code for the event_type (see reference below)
  signal_type: one of [bullish, bearish, neutral, mixed]
  sentiment: one of [positive, negative, neutral, mixed]
  priority: one of [low, medium, high, critical]
  title: concise title (max 500 chars)
  summary: 1-2 sentence summary
  event_date: ISO 8601 date (YYYY-MM-DD) or null
  confidence_score: float in [0.0, 1.0]
  tags: list of relevant string tags
  signal_reason: brief rationale for signal_type
  details: object with fields specific to the event_type (see schema below)

IMPORTANT CONSTRAINTS FOR details object:
- For disclosure events, investor_category MUST be one of: superinvestor, fii, dii, hni, mutual_fund, insurance, corporate_body, other
  Use "other" when the investor does not clearly fit another category (e.g. public shareholders, venture funds, promoters selling via OFS).
  Never return null for investor_category.
- For governance and insider events, if multiple persons are involved, put ALL names in the single "person_name" field separated by commas.
  Do NOT use person_name_2, person_name_3 or any numbered variants — they do not exist in the schema.
- Only use field names that appear in the detail schema below. Do not invent new field names.
- For numeric fields (shares, amounts, percentages), return plain numbers only — no %, ₹, $, commas or units. Example: stake_before=9.5 not "9.50%".
- event_subtype MUST be one of the exact subtype_codes listed in the reference table below for the chosen event_type.
  Never use "other" or any value not in the reference. If no subtype fits perfectly, choose the closest available one.
  Example: if a government is selling shares via OFS, use shareholding_change (not "other").

[Event subtypes reference — all valid (event_type, subtype_code) pairs]
{subtypes_ref}

[Detail field schema for each event_type]
{detail_schema}"""

        # Section hint logic
        hint_lines: list[str] = []
        lower_section = section_name.lower()
        if "bulk deal" in lower_section:
            hint_lines.append(
                "Hint: This entry is from a bulk deal section. Prefer event_type=disclosure, event_subtype=bulk_deal."
            )
        if "block deal" in lower_section:
            hint_lines.append(
                "Hint: This entry is from a block deal section. Prefer event_type=disclosure, event_subtype=block_deal."
            )

        hint_text = "\n".join(hint_lines)
        if hint_text:
            hint_text = "\n" + hint_text

        user_message = f"""Section: {section_name}{hint_text}
Stock: {stock_name}
News: {news_text}"""

        return f"SYSTEM:\n{system_message}\n\nUSER:\n{user_message}"

    @staticmethod
    def _validate_confidence(
        score: float | None,
        model_version: str | None,
    ) -> tuple[Decimal | None, str | None]:
        """Validate confidence score and return (score, model_version) or (None, None).

        Returns (None, None) if score is None, NaN, infinite, or outside [0.0, 1.0].
        Otherwise returns (Decimal(score).quantize(Decimal("0.001")), model_version).
        """
        if score is None:
            return (None, None)
        try:
            f = float(score)
        except (TypeError, ValueError):
            return (None, None)
        if math.isnan(f) or math.isinf(f):
            return (None, None)
        if f < 0.0 or f > 1.0:
            return (None, None)
        return (Decimal(str(f)).quantize(Decimal("0.001")), model_version)

    @staticmethod
    def _process_title(title: str | None) -> str | None:
        """Truncate title to 500 characters."""
        if title is None:
            return None
        return title[:500]

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    async def classify(
        self,
        news_text: str,
        section_name: str,
        stock_name: str,
    ) -> ClassificationResult:
        """Classify a stock news entry using the LLM.

        Uses the provider fallback loop: tries each available provider in turn.
        Within each provider, retries up to 2 additional times (3 total) on network/timeout errors.
        On RateLimitError: records the error with the manager and falls back to the next provider.
        Invalid JSON → status="failed" immediately, no retry.
        Invalid (event_type, event_subtype) pair → status="unclassified".
        """
        import openai  # imported here to avoid hard dependency at module load

        prompt = self._build_prompt(news_text, section_name, stock_name)

        # Split prompt into system and user parts
        parts = prompt.split("\n\nUSER:\n", 1)
        system_content = parts[0].replace("SYSTEM:\n", "", 1)
        user_content = parts[1] if len(parts) > 1 else prompt

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

        num_providers = len(self._provider_manager.get_provider_configs())

        for _fallback in range(num_providers + 1):
            provider = self._provider_manager.get_available_provider()
            if provider is None:
                return ClassificationResult(
                    status="failed",
                    event_type=None, event_subtype=None, signal_type=None,
                    sentiment=None, priority=None, title=None, summary=None,
                    event_date=None, confidence_score=None,
                    confidence_model_version=None,
                    details={"reason": "all_providers_exhausted"},
                    tags=[],
                    signal_reason=None,
                )

            client = openai.AsyncOpenAI(base_url=provider.base_url, api_key=provider.api_key)

            last_error: Exception | None = None
            rate_limited_this_provider = False
            raw_text: str | None = None

            for attempt in range(3):
                try:
                    response = await asyncio.wait_for(
                        client.chat.completions.create(
                            model=provider.model,
                            messages=messages,
                            response_format={"type": "json_object"},
                        ),
                        timeout=30.0,
                    )
                    raw_text = response.choices[0].message.content or ""
                    break  # success — exit inner retry loop
                except (openai.APIConnectionError, openai.APITimeoutError, asyncio.TimeoutError) as exc:
                    last_error = exc
                    if attempt < 2:
                        await asyncio.sleep(2)
                    continue
                except openai.RateLimitError as exc:
                    last_error = exc
                    # Record the error and break to try the next provider
                    self._provider_manager.record_rate_limit_error(provider.name, str(exc))
                    rate_limited_this_provider = True
                    break  # break inner loop → continue outer fallback loop
                except Exception as exc:
                    # Non-retryable error
                    logger.error("LLM failed for '%s': %s", stock_name, exc)
                    return ClassificationResult(
                        status="failed",
                        event_type=None, event_subtype=None, signal_type=None,
                        sentiment=None, priority=None, title=None, summary=None,
                        event_date=None, confidence_score=None,
                        confidence_model_version=None,
                    )
            else:
                # All 3 attempts failed (network/timeout errors only)
                if isinstance(last_error, openai.RateLimitError):
                    # Shouldn't reach here (RateLimitError breaks before else), but guard anyway
                    self._provider_manager.record_rate_limit_error(provider.name, str(last_error))
                    rate_limited_this_provider = True
                else:
                    logger.error("LLM failed for '%s': %s", stock_name, last_error)
                    return ClassificationResult(
                        status="failed",
                        event_type=None, event_subtype=None, signal_type=None,
                        sentiment=None, priority=None, title=None, summary=None,
                        event_date=None, confidence_score=None,
                        confidence_model_version=None,
                    )

            # If this provider was rate-limited, continue to next fallback iteration
            if rate_limited_this_provider:
                continue

            # raw_text must be set at this point (successful response)
            if raw_text is None:
                # Defensive: should not happen if loop logic is correct
                continue

            # Parse JSON
            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError:
                logger.error("LLM returned invalid JSON for '%s': %s", stock_name, raw_text)
                return ClassificationResult(
                    status="failed",
                    event_type=None, event_subtype=None, signal_type=None,
                    sentiment=None, priority=None, title=None, summary=None,
                    event_date=None, confidence_score=None,
                    confidence_model_version=None,
                )

            result = self._process_response(data, stock_name, provider.model)
            logger.info(
                "CLASSIFY_OK provider=%s model=%s stock=%r status=%s",
                provider.name, provider.model, stock_name, result.status,
            )
            return result

        # Fallback loop exhausted without a result — all providers unavailable
        return ClassificationResult(
            status="failed",
            event_type=None, event_subtype=None, signal_type=None,
            sentiment=None, priority=None, title=None, summary=None,
            event_date=None, confidence_score=None,
            confidence_model_version=None,
            details={"reason": "all_providers_exhausted"},
            tags=[],
            signal_reason=None,
        )

    def _process_response(
        self,
        data: dict,
        stock_name: str,
        llm_model: str,
    ) -> ClassificationResult:
        """Process parsed LLM JSON response into a ClassificationResult."""
        event_type = data.get("event_type")
        event_subtype = data.get("event_subtype")

        # Validate (event_type, event_subtype) pair
        valid_pair = (
            event_type in self._valid_subtypes
            and event_subtype in self._valid_subtypes.get(event_type, set())
        )
        if not valid_pair:
            logger.warning(
                "Invalid (event_type, event_subtype) pair for '%s': (%s, %s)",
                stock_name, event_type, event_subtype,
            )
            return ClassificationResult(
                status="unclassified",
                event_type=None, event_subtype=None,
                signal_type=data.get("signal_type"),
                sentiment=data.get("sentiment"),
                priority=data.get("priority"),
                title=self._process_title(data.get("title")),
                summary=data.get("summary"),
                event_date=data.get("event_date") or None,
                confidence_score=None,
                confidence_model_version=None,
                details={},
                tags=data.get("tags") or [],
                signal_reason=data.get("signal_reason"),
            )

        # Validate confidence
        raw_confidence = data.get("confidence_score")
        confidence_score, confidence_model_version = self._validate_confidence(
            raw_confidence, llm_model
        )

        # Process details
        raw_details = data.get("details")
        if not isinstance(raw_details, dict) or not raw_details:
            if event_type in VALID_EVENT_TYPES:
                logger.warning(
                    "Missing or invalid details for '%s' (event_type=%s)",
                    stock_name, event_type,
                )
            details: dict = {}
        else:
            details = raw_details

        return ClassificationResult(
            status="ok",
            event_type=event_type,
            event_subtype=event_subtype,
            signal_type=data.get("signal_type"),
            sentiment=data.get("sentiment"),
            priority=data.get("priority"),
            title=self._process_title(data.get("title")),
            summary=data.get("summary"),
            event_date=data.get("event_date") or None,
            confidence_score=confidence_score,
            confidence_model_version=confidence_model_version,
            details=details,
            tags=data.get("tags") or [],
            signal_reason=data.get("signal_reason"),
        )
