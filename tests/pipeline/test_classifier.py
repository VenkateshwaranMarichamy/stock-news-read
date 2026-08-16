"""Unit tests for Event_Classifier.

Requirements: 2.1–2.10, 8.1–8.3
"""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pipeline.classifier import (
    ClassificationResult,
    Event_Classifier,
    REQUIRED_JSON_FIELDS,
)
from app.pipeline.provider_manager import LLM_Provider_Manager, ProviderConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_SUBTYPES: dict[str, set[str]] = {
    "corporate_action": {"dividend", "bonus", "split", "merger", "acquisition"},
    "disclosure": {"bulk_deal", "block_deal", "shareholding_change"},
    "insider": {"insider_buy", "insider_sell", "pledge"},
    "business": {"contract", "order_win", "capex"},
    "governance": {"board_appointment", "board_resignation"},
    "credit_rating": {"upgrade", "downgrade"},
    "financials": {"quarterly_results", "annual_results"},
    "fundraising": {"qip", "ncd"},
    "legal": {"court_order", "tax_demand"},
}


def _make_test_provider_manager() -> LLM_Provider_Manager:
    """Build a single-provider manager with dummy credentials for unit tests."""
    return LLM_Provider_Manager(
        providers=[ProviderConfig(
            name="test-provider",
            base_url="http://localhost/v1",
            api_key="test-key",
            model="test-model",
        )],
        strategy="priority",
    )


def make_classifier() -> Event_Classifier:
    return Event_Classifier(VALID_SUBTYPES, provider_manager=_make_test_provider_manager())


def make_ok_response(
    event_type: str = "business",
    event_subtype: str = "contract",
    confidence: float = 0.85,
    title: str = "Test Title",
    details: dict | None = None,
) -> str:
    return json.dumps({
        "event_type": event_type,
        "event_subtype": event_subtype,
        "signal_type": "bullish",
        "sentiment": "positive",
        "priority": "medium",
        "title": title,
        "summary": "Test summary.",
        "event_date": "2026-01-15",
        "confidence_score": confidence,
        "tags": ["test"],
        "signal_reason": "positive contract",
        "details": details or {"contract_type": "new"},
    })


def make_mock_response(content: str):
    """Create a mock openai response object."""
    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


# ---------------------------------------------------------------------------
# _validate_confidence() tests
# ---------------------------------------------------------------------------

class TestValidateConfidence:
    def test_valid_score_returns_decimal(self):
        score, version = Event_Classifier._validate_confidence(0.85, "model-v1")
        assert score == Decimal("0.850")
        assert version == "model-v1"

    def test_zero_is_valid(self):
        score, version = Event_Classifier._validate_confidence(0.0, "model-v1")
        assert score == Decimal("0.000")

    def test_one_is_valid(self):
        score, version = Event_Classifier._validate_confidence(1.0, "model-v1")
        assert score == Decimal("1.000")

    def test_none_returns_none_none(self):
        score, version = Event_Classifier._validate_confidence(None, "model-v1")
        assert score is None
        assert version is None

    def test_above_one_returns_none(self):
        score, version = Event_Classifier._validate_confidence(1.1, "model-v1")
        assert score is None
        assert version is None

    def test_negative_returns_none(self):
        score, version = Event_Classifier._validate_confidence(-0.1, "model-v1")
        assert score is None
        assert version is None

    def test_nan_returns_none(self):
        import math
        score, version = Event_Classifier._validate_confidence(float("nan"), "model-v1")
        assert score is None
        assert version is None

    def test_infinity_returns_none(self):
        score, version = Event_Classifier._validate_confidence(float("inf"), "model-v1")
        assert score is None
        assert version is None


# ---------------------------------------------------------------------------
# _process_title() tests
# ---------------------------------------------------------------------------

class TestProcessTitle:
    def test_short_title_unchanged(self):
        result = Event_Classifier._process_title("Short title")
        assert result == "Short title"

    def test_exactly_500_chars_unchanged(self):
        title = "x" * 500
        result = Event_Classifier._process_title(title)
        assert len(result) == 500

    def test_501_chars_truncated_to_500(self):
        title = "x" * 501
        result = Event_Classifier._process_title(title)
        assert len(result) == 500

    def test_long_title_truncated(self):
        title = "a" * 1000
        result = Event_Classifier._process_title(title)
        assert len(result) == 500

    def test_none_returns_none(self):
        result = Event_Classifier._process_title(None)
        assert result is None


# ---------------------------------------------------------------------------
# _build_prompt() tests
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_prompt_contains_required_fields(self):
        c = make_classifier()
        prompt = c._build_prompt("Some news text", "Stocks to Watch", "Infosys")
        for field in REQUIRED_JSON_FIELDS:
            assert field in prompt, f"Missing field: {field}"

    def test_prompt_contains_section_name(self):
        c = make_classifier()
        prompt = c._build_prompt("news", "Bulk Deal Section", "TCS")
        assert "Bulk Deal Section" in prompt

    def test_prompt_contains_stock_name(self):
        c = make_classifier()
        prompt = c._build_prompt("news", "section", "Reliance Industries")
        assert "Reliance Industries" in prompt

    def test_bulk_deal_hint_present(self):
        c = make_classifier()
        prompt = c._build_prompt("news", "Bulk Deal Stocks", "TCS")
        assert "bulk_deal" in prompt

    def test_block_deal_hint_present(self):
        c = make_classifier()
        prompt = c._build_prompt("news", "Block Deal Stocks", "TCS")
        assert "block_deal" in prompt

    def test_non_bulk_section_no_hint(self):
        c = make_classifier()
        prompt = c._build_prompt("news", "Stocks to Watch", "TCS")
        # The hint text should not be present (the subtype code appears in the reference table,
        # but the explicit hint phrase should not be added for non-bulk sections)
        assert "Prefer event_type=disclosure, event_subtype=bulk_deal" not in prompt
        assert "Prefer event_type=disclosure, event_subtype=block_deal" not in prompt

    def test_both_bulk_and_block_hints(self):
        c = make_classifier()
        prompt = c._build_prompt("news", "Bulk and Block Deals", "TCS")
        assert "bulk_deal" in prompt
        assert "block_deal" in prompt

    def test_case_insensitive_section_hint(self):
        c = make_classifier()
        prompt = c._build_prompt("news", "bulk deal section", "TCS")
        assert "bulk_deal" in prompt


# ---------------------------------------------------------------------------
# classify() tests
# ---------------------------------------------------------------------------

class TestClassify:
    @pytest.mark.asyncio
    async def test_successful_classification(self):
        """Valid LLM response → status='ok'."""
        c = make_classifier()
        mock_resp = make_mock_response(make_ok_response())

        with patch("openai.AsyncOpenAI") as mock_openai_cls:
            mock_client = AsyncMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

            result = await c.classify("Some news text", "Stocks to Watch", "TestCo")

        assert result.status == "ok"
        assert result.event_type == "business"
        assert result.event_subtype == "contract"

    @pytest.mark.asyncio
    async def test_three_failures_returns_failed(self):
        """3 consecutive network errors → status='failed'."""
        import openai as openai_module
        c = make_classifier()

        with patch("openai.AsyncOpenAI") as mock_openai_cls:
            mock_client = AsyncMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create = AsyncMock(
                side_effect=openai_module.APIConnectionError(request=MagicMock())
            )

            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await c.classify("news", "section", "TestCo")

        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_two_failures_then_success(self):
        """2 failures then success → status='ok'."""
        import openai as openai_module
        c = make_classifier()
        mock_resp = make_mock_response(make_ok_response())

        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise openai_module.APIConnectionError(request=MagicMock())
            return mock_resp

        with patch("openai.AsyncOpenAI") as mock_openai_cls:
            mock_client = AsyncMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create = side_effect

            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await c.classify("news", "section", "TestCo")

        assert result.status == "ok"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_invalid_json_returns_failed_no_retry(self):
        """Invalid JSON → status='failed' immediately, no retry."""
        c = make_classifier()
        mock_resp = make_mock_response("not valid json {{{")

        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return mock_resp

        with patch("openai.AsyncOpenAI") as mock_openai_cls:
            mock_client = AsyncMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create = side_effect

            result = await c.classify("news", "section", "TestCo")

        assert result.status == "failed"
        assert call_count == 1  # no retry

    @pytest.mark.asyncio
    async def test_invalid_subtype_pair_returns_unclassified(self):
        """Invalid (event_type, event_subtype) pair → status='unclassified'."""
        c = make_classifier()
        bad_response = json.dumps({
            "event_type": "business",
            "event_subtype": "nonexistent_subtype",
            "signal_type": "neutral",
            "sentiment": "neutral",
            "priority": "low",
            "title": "Test",
            "summary": "Test",
            "event_date": None,
            "confidence_score": 0.5,
            "tags": [],
            "signal_reason": None,
            "details": {},
        })
        mock_resp = make_mock_response(bad_response)

        with patch("openai.AsyncOpenAI") as mock_openai_cls:
            mock_client = AsyncMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

            result = await c.classify("news", "section", "TestCo")

        assert result.status == "unclassified"
        assert result.event_type is None
        assert result.event_subtype is None
        assert result.confidence_score is None
        assert result.confidence_model_version is None

    @pytest.mark.asyncio
    async def test_confidence_out_of_range_nulled(self):
        """confidence_score outside [0,1] → both confidence fields null."""
        c = make_classifier()
        mock_resp = make_mock_response(make_ok_response(confidence=1.5))

        with patch("openai.AsyncOpenAI") as mock_openai_cls:
            mock_client = AsyncMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

            result = await c.classify("news", "section", "TestCo")

        assert result.confidence_score is None
        assert result.confidence_model_version is None

    @pytest.mark.asyncio
    async def test_title_truncated_at_500(self):
        """Title > 500 chars is truncated to 500."""
        c = make_classifier()
        long_title = "T" * 600
        mock_resp = make_mock_response(make_ok_response(title=long_title))

        with patch("openai.AsyncOpenAI") as mock_openai_cls:
            mock_client = AsyncMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

            result = await c.classify("news", "section", "TestCo")

        assert result.title is not None
        assert len(result.title) == 500

    @pytest.mark.asyncio
    async def test_absent_details_returns_empty_dict(self):
        """Absent details → details={}."""
        c = make_classifier()
        response_data = json.loads(make_ok_response())
        del response_data["details"]
        mock_resp = make_mock_response(json.dumps(response_data))

        with patch("openai.AsyncOpenAI") as mock_openai_cls:
            mock_client = AsyncMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

            result = await c.classify("news", "section", "TestCo")

        assert result.details == {}

    @pytest.mark.asyncio
    async def test_invalid_details_not_dict_returns_empty(self):
        """details that is not a dict → details={}."""
        c = make_classifier()
        response_data = json.loads(make_ok_response())
        response_data["details"] = "not a dict"
        mock_resp = make_mock_response(json.dumps(response_data))

        with patch("openai.AsyncOpenAI") as mock_openai_cls:
            mock_client = AsyncMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

            result = await c.classify("news", "section", "TestCo")

        assert result.details == {}
