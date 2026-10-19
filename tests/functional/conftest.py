# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""Pytest hooks for the functional tests."""

from typing import Optional, Set

import pytest

_config: Optional[pytest.Config] = None
# The releases that already have a failed test.
_failed_series: Set[str] = set()


def pytest_configure(config: pytest.Config):
    """Keep the config, for pytest_runtest_logreport to find the terminal reporter."""
    global _config
    _config = config


def _series(item: pytest.Item) -> Optional[str]:
    """Return the release ``item`` runs for, or None if it isn't parametrized by release."""
    callspec = getattr(item, "callspec", None)
    return callspec.params.get("series") if callspec else None


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    """Record the release of every failed test."""
    report = yield
    series = _series(item)
    if report.failed and series is not None:
        _failed_series.add(series)
    return report


def pytest_runtest_setup(item: pytest.Item):
    """Skip the rest of a release's tests once one of them has failed.

    Each test builds on the ones before it, so after a failure the rest would only fail too,
    each after waiting out its timeouts. Together those waits outlast the CI job, which is then
    cancelled before pytest prints its summary or the models' debug-logs.
    """
    series = _series(item)
    if series in _failed_series:
        pytest.skip(f"an earlier test for {series} failed")


def pytest_runtest_logreport(report: pytest.TestReport):
    """Print a failure's traceback as soon as it happens.

    pytest only prints tracebacks in its summary at the end of the session. When CI cancels a
    job that runs out of time, the session never ends, and the tracebacks are lost.
    """
    if not report.failed or _config is None:
        return
    reporter = _config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        return
    reporter.write_line("")
    reporter.write_sep("!", f"{report.nodeid} ({report.when}) failed")
    report.toterminal(reporter._tw)  # pyright: ignore[reportPrivateUsage]
    reporter.write_line("")
