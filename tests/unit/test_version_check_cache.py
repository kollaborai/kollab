"""Cache-staleness rule for the update check (issue #169).

After an upgrade, ``cached_latest_version`` holds the pre-upgrade
release. The cache must count as stale whenever it does not show a
release strictly newer than the running version, so a refetch happens
within the short window instead of suppressing the notice for a full
interval.
"""

from __future__ import annotations

from typing import Any

import pytest

from kollabor.updates.version_check_service import (
    VersionCheckService,
    _cache_shows_current_or_older,
)


class StubConfig:
    """Dict-backed stand-in for ConfigService (get/save_key only)."""

    def __init__(self, values: dict[str, Any] | None = None):
        self.values = dict(values or {})
        self.saved: list[tuple[str, Any]] = []

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def save_key(self, key: str, value: Any) -> None:
        self.saved.append((key, value))
        self.values[key] = value


def make_service(
    cached: str, current: str = "0.15.3", **extra: Any
) -> VersionCheckService:
    config = StubConfig(
        {
            "kollabor.updates.cached_latest_version": cached,
            "kollabor.updates.check_interval_hours": 24,
            **extra,
        }
    )
    return VersionCheckService(config, current)


# --------------------------------------------------------------------- #
# the helper
# --------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("cached", "current", "expected_stale"),
    [
        ("0.15.2", "0.15.3", True),  # pre-upgrade cache: the bug in #169
        ("0.15.3", "0.15.3", True),  # equal: recheck on the short window
        ("0.15.4", "0.15.3", False),  # strictly newer: long TTL kept
        ("v0.15.2", "0.15.3", True),  # v-prefix tolerated
        ("0.15.3-rc1", "0.15.3", True),  # prerelease older than final
        ("", "0.15.3", True),  # empty cache: stale
        ("garbage", "0.15.3", True),  # unparseable: stale
        ("0.15.10", "0.15.9", False),  # numeric, not lexicographic
    ],
)
def test_helper_classifies_cache_staleness(cached, current, expected_stale):
    assert _cache_shows_current_or_older(cached, current) is expected_stale


# --------------------------------------------------------------------- #
# _is_cache_valid TTL selection
# --------------------------------------------------------------------- #


def _validity_with_cache(cached: str, current: str, hours_old: float) -> bool:
    service = make_service(cached, current)
    import time

    last_check = int(time.time() - hours_old * 3600)
    service.config.values["kollabor.updates.last_check_timestamp"] = last_check
    return service._is_cache_valid()


def test_cached_older_than_current_refetches_after_an_hour():
    # The #169 repro: cache predates an upgrade, check ran 2h ago.
    # Stale rule caps TTL at 1h -> invalid -> refetch -> notice appears.
    assert _validity_with_cache("0.15.2", "0.15.3", hours_old=2) is False


def test_cached_older_than_current_still_valid_within_an_hour():
    assert _validity_with_cache("0.15.2", "0.15.3", hours_old=0.5) is True


def test_cached_equal_to_current_keeps_short_window():
    # Current behavior preserved: 1h cap on the equal case.
    assert _validity_with_cache("0.15.3", "0.15.3", hours_old=2) is False
    assert _validity_with_cache("0.15.3", "0.15.3", hours_old=0.5) is True


def test_cached_newer_than_current_keeps_full_ttl():
    # A cached newer release is trusted for the configured 24h.
    assert _validity_with_cache("0.15.4", "0.15.3", hours_old=2) is True
    assert _validity_with_cache("0.15.4", "0.15.3", hours_old=23) is True


def test_empty_cache_is_stale_after_an_hour():
    assert _validity_with_cache("", "0.15.3", hours_old=2) is False


def test_unparseable_cache_is_stale_after_an_hour():
    assert _validity_with_cache("garbage", "0.15.3", hours_old=2) is False


def test_missing_timestamp_counts_as_stale():
    # age = huge -> invalid regardless of the TTL branch taken.
    service = make_service("0.15.4", "0.15.3")
    service.config.values["kollabor.updates.last_check_timestamp"] = 0
    assert service._is_cache_valid() is False


def test_default_interval_hours_is_24():
    service = VersionCheckService(StubConfig(), "0.15.3")
    assert service.check_interval_hours == 24
