"""
Repo-root pytest fixtures, shared across every test module.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from db.connection import open_session_db
from db.migration_runner import MigrationRunner


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

    If this fires locally: `unset LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY`
    is not enough. Several modules under test call `load_dotenv()` at
    import time (e.g. src/common/llm_client.py, src/retrieval/hybrid_search.py),
    which re-populates os.environ from a local .env file during test
    collection — python-dotenv's default `override=False` only skips a
    key that is already *set* (even to ""), not one that is merely
    absent. Set both to an explicit empty string instead:
    `export LANGFUSE_PUBLIC_KEY="" LANGFUSE_SECRET_KEY=""`. CI never hits
    this — .env is gitignored and never present there, so load_dotenv()
    is a no-op and both vars are genuinely absent.
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


@pytest.fixture
def migrated_db_path(tmp_path: Path) -> str:
    """Path to a fresh session DB with the 0001 schema applied. The
    migration-runner snapshot dir is redirected under tmp_path so no .bak
    files land in the repo. (Several older DB test modules still inline this
    setup — new ones should use this fixture.)"""
    path = str(tmp_path / "session.db")
    MigrationRunner(db_path=path, snapshot_dir=tmp_path / "snap").run()
    return path


@pytest.fixture
def session_conn(migrated_db_path: str) -> Iterator[object]:
    """An open connection to a migrated session DB, pre-seeded with one
    ``sessions`` row (id ``s1``) so handlers that take a nullable
    ``session_id`` can be exercised with a real FK target."""
    conn = open_session_db(migrated_db_path)
    now = "2026-02-01T00:00:00Z"
    conn.execute(
        "INSERT INTO sessions (id, started_at, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("s1", now, now, now),
    )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()
