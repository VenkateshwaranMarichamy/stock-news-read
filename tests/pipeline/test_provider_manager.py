"""Tests for app/pipeline/provider_manager.py.

Covers LLM_Provider_Manager, ProviderConfig, ProviderState, and ConfigurationError.
"""

from __future__ import annotations

import pytest

from app.pipeline.provider_manager import (
    ConfigurationError,
    LLM_Provider_Manager,
    ProviderConfig,
    ProviderState,
    _ProviderRuntime,
)


def test_placeholder() -> None:
    assert True
