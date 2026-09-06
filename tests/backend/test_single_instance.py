"""Phase 2 Step 2.3 — SingleInstanceGuard."""

import sys
import uuid

import pytest

from src.backend.single_instance import AlreadyRunningError, SingleInstanceGuard

pytestmark = pytest.mark.unit

win_only = pytest.mark.skipif(sys.platform != "win32", reason="Win32 named mutex")


@pytest.fixture
def mutex_name():
    # unique per test so a leaked handle from another run can't interfere
    return f"Local\\mm_test_{uuid.uuid4().hex}"


@win_only
def test_second_guard_with_the_same_name_does_not_acquire(mutex_name):
    g1 = SingleInstanceGuard(mutex_name)
    g2 = SingleInstanceGuard(mutex_name)
    try:
        assert g1.acquire() is True
        assert g2.acquire() is False
    finally:
        g1.release()
        g2.release()


@win_only
def test_release_frees_the_name_for_a_fresh_acquire(mutex_name):
    g1 = SingleInstanceGuard(mutex_name)
    assert g1.acquire() is True
    g1.release()

    g2 = SingleInstanceGuard(mutex_name)
    try:
        assert g2.acquire() is True
    finally:
        g2.release()


@win_only
def test_context_manager_raises_on_contention(mutex_name):
    held = SingleInstanceGuard(mutex_name)
    held.acquire()
    try:
        with pytest.raises(AlreadyRunningError):
            with SingleInstanceGuard(mutex_name):
                pass
    finally:
        held.release()


@win_only
def test_acquire_is_repeatable(mutex_name):
    g = SingleInstanceGuard(mutex_name)
    try:
        assert g.acquire() is True
        assert g.acquire() is True  # same answer, no double handle
    finally:
        g.release()


def test_release_is_idempotent(mutex_name):
    g = SingleInstanceGuard(mutex_name)
    g.acquire()
    g.release()
    g.release()  # must not raise


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows fallback path")
def test_non_windows_always_acquires(mutex_name):
    assert SingleInstanceGuard(mutex_name).acquire() is True
