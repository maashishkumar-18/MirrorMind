"""
Repo-root pytest fixtures, shared across every test module.
"""

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _no_langfuse_in_tests():
    """
    Guard against leaked Langfuse credentials in the test environment.

    observability.tracing.is_enabled() (and therefore traced_span()'s
    no-op guarantee) is gated on both LANGFUSE_PUBLIC_KEY and
    LANGFUSE_SECRET_KEY being unset. Characterization tests for
    GenerationOrchestrator rely on traced_span() being a genuine no-op
    so they never make real Langfuse network calls. This fixture fails
    fast with a clear message if a local .env leaks real credentials
    into the test run, rather than letting tests silently start making
    network calls (or, worse, pass locally due to network flakiness and
    then behave differently in CI).
    """
    leaked = [v for v in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY") if os.getenv(v)]
    if leaked:
        pytest.fail(
            f"{', '.join(leaked)} set in the test environment — unset before running "
            "tests to prevent accidental real tracing calls (traced_span only no-ops "
            "when both LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are absent)."
        )


@pytest.fixture
def tmp_sqlite_path(tmp_path: Path) -> Path:
    """A fresh, non-existent SQLite file path under pytest's tmp_path."""
    return tmp_path / "test.db"
