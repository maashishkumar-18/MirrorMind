"""Subprocess-kill fuzzing (Phase 4 Step 4.4b).

Roadmap Step 4.4 acceptance: "100/100 kill-and-recover cycles produce zero data
loss and zero silent hangs." ``RELIABILITY_ITERATIONS`` controls the count —
default 3 locally (one per kill window), the dedicated ``reliability`` CI job
sets 100.

Each iteration: spawn the backend, fire a burst of ``chat.send`` / feature
writes, ``SIGKILL`` it in one of three windows (IPC processing / DB write /
inference), relaunch against the same encrypted DB, and assert:

* the relaunch reaches ``app.ready`` (not ``app.integrity_failed``) within the
  timeout — the on-launch ``check_integrity`` passed, i.e. a WAL recovery of the
  interrupted write left a consistent DB, and there is no silent hang;
* every ``chat.send`` that returned a ``response`` before the kill still has both
  its user and assistant rows (zero acknowledged-write loss);
* the crashed session was finalized on relaunch;
* a direct ``PRAGMA integrity_check`` after a clean shutdown is ``ok``.
"""

from __future__ import annotations

import os
import time

import pytest

from db.connection import open_session_db
from db.health import check_integrity
from tests.reliability.fuzz_supervisor import DB_KEY, KILL_POINTS, Sidecar

# This module spawns real backend subprocesses — heavy and Windows-only in
# spirit. It runs only when RELIABILITY_ITERATIONS is set: the dedicated
# `reliability` CI job sets 100, a developer runs e.g.
# `RELIABILITY_ITERATIONS=3 pytest tests/reliability/`. The default `pytest`
# run skips it (the fast download-failure tests in this package still run).
_ITER_ENV = os.getenv("RELIABILITY_ITERATIONS")
pytestmark = [
    pytest.mark.reliability,
    pytest.mark.skipif(
        _ITER_ENV is None, reason="set RELIABILITY_ITERATIONS to run the kill-fuzz cycles"
    ),
]

ITERATIONS = int(_ITER_ENV or "3")


def _open_with_retry(db_path: str, *, attempts: int = 10):
    """Windows can hold the killed process's file handle for a beat; retry the
    keyed open past that."""
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return open_session_db(db_path, DB_KEY)
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.5)
    raise AssertionError(f"could not open {db_path} after {attempts} tries: {last}")


@pytest.mark.parametrize("iteration", range(ITERATIONS))
def test_kill_and_recover_cycle(iteration, tmp_path_factory):
    kill_point = KILL_POINTS[iteration % len(KILL_POINTS)]
    data_dir = tmp_path_factory.mktemp(f"fuzz-{iteration}")
    db_path = str(data_dir / "session.db")

    if kill_point == "db_write":
        os.environ["RAGPIPE_FUZZ_STALL_BEFORE_COMMIT_MS"] = "300"
    try:
        sc = Sidecar(data_dir)
        sc.start()

        acknowledged = 0
        for i in range(4):
            r = sc.send("chat.send", {"text": f"note {iteration}-{i}"}, timeout=8.0)
            if r is not None and r.get("message_type") == "response":
                acknowledged += 1
            sc.send(
                "schedule.create_item",
                {
                    "title": f"evt {i}",
                    "start_time": "2027-01-04T09:00:00+00:00",
                    "end_time": "2027-01-04T10:00:00+00:00",
                },
                timeout=8.0,
            )

        # kill inside the chosen window
        if kill_point == "db_write":
            sc.send("chat.send", {"text": "mid-write"}, timeout=0.05)
            time.sleep(0.12)  # inside the uncommitted-INSERT stall
        elif kill_point == "inference":
            sc.send("chat.send", {"text": "mid-inference"}, timeout=0.05)
        sc.kill()
    finally:
        os.environ.pop("RAGPIPE_FUZZ_STALL_BEFORE_COMMIT_MS", None)

    # ---- relaunch is the real recovery path (as the supervisor would) ----
    sc2 = Sidecar(data_dir)
    sc2.start(ready_timeout=40.0)  # start() raises if app.ready never arrives
    assert not sc2.saw_event(
        "app.integrity_failed"
    ), f"[{kill_point}] relaunched into degraded mode"
    assert sc2.saw_event("app.ready")

    # the relaunched backend answers chat.history — the worker warmed up cleanly
    assert _history_responds(sc2)

    # zero acknowledged-write loss + the crashed session was finalized
    conn = _open_with_retry(db_path)
    try:
        turns = conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE deleted_at IS NULL"
        ).fetchone()["n"]
        assert (
            turns >= acknowledged * 2
        ), f"[{kill_point}] {acknowledged} acknowledged sends, only {turns} message rows"
        open_sessions = conn.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE ended_at IS NULL"
        ).fetchone()["n"]
        assert open_sessions <= 1, f"[{kill_point}] {open_sessions} sessions open after relaunch"
    finally:
        conn.close()

    code = sc2.shutdown_clean()
    assert code == 0, f"[{kill_point}] clean shutdown exited {code}"

    # ---- a direct integrity_check after everything is closed ----
    conn = _open_with_retry(db_path)
    try:
        assert check_integrity(conn).ok, f"[{kill_point}] integrity_check failed post-recovery"
    finally:
        conn.close()


def _history_responds(sc: Sidecar) -> bool:
    r = sc.send("chat.history", {}, timeout=15.0)
    return r is not None and r.get("message_type") == "response"
