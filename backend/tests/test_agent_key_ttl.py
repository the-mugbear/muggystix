"""Tests for the agent-key TTL helper (v2.58.0)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings as _settings
from app.services.agent_key_ttl import resolve_expires_at, resolve_ttl_hours


def test_resolve_ttl_hours_none_uses_default():
    assert resolve_ttl_hours(None) == _settings.AGENT_KEY_TTL_HOURS


def test_resolve_ttl_hours_passes_through_valid_value():
    assert resolve_ttl_hours(48) == 48


def test_resolve_ttl_hours_clamps_to_cap():
    over = _settings.AGENT_KEY_MAX_TTL_HOURS * 5
    assert resolve_ttl_hours(over) == _settings.AGENT_KEY_MAX_TTL_HOURS


def test_resolve_ttl_hours_zero_or_negative_floors_to_one():
    """Zero / negative is nonsensical (key would already be expired);
    floor to 1h so the caller's mistake doesn't produce an unusable
    artifact silently."""
    assert resolve_ttl_hours(0) == 1
    assert resolve_ttl_hours(-12) == 1


def test_resolve_expires_at_returns_aware_future_timestamp():
    before = datetime.now(timezone.utc)
    result = resolve_expires_at(2)
    after = datetime.now(timezone.utc)
    assert result.tzinfo is not None
    assert result >= before + timedelta(hours=2) - timedelta(seconds=2)
    assert result <= after + timedelta(hours=2) + timedelta(seconds=2)


def test_resolve_expires_at_uses_default_when_none():
    result = resolve_expires_at(None)
    expected = datetime.now(timezone.utc) + timedelta(
        hours=_settings.AGENT_KEY_TTL_HOURS
    )
    # Within 2 seconds — clock skew between the two now() calls
    assert abs((result - expected).total_seconds()) < 2
