"""Phase 2 Step 2.3 — ShutdownCoordinator."""

import pytest

from src.backend.lifecycle import ShutdownCoordinator

pytestmark = pytest.mark.unit


def test_teardown_runs_in_reverse_registration_order():
    order: list[str] = []
    coord = ShutdownCoordinator()
    coord.register("a", lambda: order.append("a"))
    coord.register("b", lambda: order.append("b"))
    coord.register("c", lambda: order.append("c"))

    results = coord.shutdown()

    assert order == ["c", "b", "a"]
    assert [r.name for r in results] == ["c", "b", "a"]
    assert all(r.ok for r in results)
    assert all(r.error is None for r in results)


def test_a_raising_step_is_isolated_and_the_rest_still_run():
    ran: list[str] = []
    coord = ShutdownCoordinator()
    coord.register("ok1", lambda: ran.append("ok1"))
    coord.register("boom", lambda: (_ for _ in ()).throw(RuntimeError("nope")))
    coord.register("ok2", lambda: ran.append("ok2"))

    results = coord.shutdown()

    assert ran == ["ok2", "ok1"]  # boom skipped, others ran
    by_name = {r.name: r for r in results}
    assert by_name["boom"].ok is False
    assert "RuntimeError: nope" in by_name["boom"].error
    assert by_name["ok1"].ok and by_name["ok2"].ok


def test_shutdown_is_idempotent():
    calls: list[int] = []
    coord = ShutdownCoordinator()
    coord.register("once", lambda: calls.append(1))

    first = coord.shutdown()
    second = coord.shutdown()

    assert len(first) == 1
    assert second == []
    assert calls == [1]


def test_register_after_shutdown_raises():
    coord = ShutdownCoordinator()
    coord.shutdown()
    with pytest.raises(RuntimeError):
        coord.register("late", lambda: None)


def test_step_result_carries_timing():
    coord = ShutdownCoordinator()
    coord.register("x", lambda: None)
    (result,) = coord.shutdown()
    assert result.elapsed_ms >= 0.0


def test_concurrent_register_and_shutdown_do_not_corrupt_the_step_list():
    # Phase 2 audit: register()/shutdown() are called from different threads
    # (a stop signal vs. main() still wiring) — a lock guards _steps/_done.
    import threading

    coord = ShutdownCoordinator()
    ran: list[str] = []
    errors: list[Exception] = []

    def registrar(i: int):
        try:
            coord.register(f"r{i}", lambda i=i: ran.append(f"r{i}"))
        except RuntimeError:
            pass  # lost the race with shutdown() — acceptable
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=registrar, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    results = coord.shutdown()
    for t in threads:
        t.join()

    assert not errors
    # every step that made it in ran exactly once; no partial/duplicate entries
    assert sorted(ran) == sorted(r.name for r in results if r.ok)
    assert len(ran) == len(set(ran))


def test_a_real_scheduler_thread_is_joined(migrated_db_path):
    from src.features.scheduler import SchedulerConfig, SchedulerThread
    from src.features.toast_bridge import NoOpToastBridge

    sched = SchedulerThread(
        migrated_db_path, NoOpToastBridge(), config=SchedulerConfig(poll_seconds=0.05)
    )
    sched.start()

    coord = ShutdownCoordinator()
    coord.register("scheduler", lambda: sched.stop(timeout=2.0))
    (result,) = coord.shutdown()

    assert result.ok
    assert not sched.is_alive()
    assert sched.stop() is True  # Phase 2 audit: stop() now returns a bool
