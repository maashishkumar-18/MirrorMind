"""The IPC method contract is internally consistent (Phase 3 Step 3.1a)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from src.backend.handlers import HANDLERS
from src.common.ipc.methods import METHOD_CONTRACTS


def test_every_contract_has_a_handler():
    assert set(METHOD_CONTRACTS) == set(HANDLERS)


@pytest.mark.parametrize("name", sorted(METHOD_CONTRACTS))
def test_contract_models_are_pydantic(name):
    c = METHOD_CONTRACTS[name]
    assert issubclass(c.params, BaseModel)
    assert issubclass(c.result, BaseModel)


@pytest.mark.parametrize("name", sorted(METHOD_CONTRACTS))
def test_params_models_reject_unknown_fields(name):
    params_model = METHOD_CONTRACTS[name].params
    with pytest.raises(ValidationError):
        params_model.model_validate({"definitely_not_a_field": 1})


def test_degraded_whitelist_is_the_expected_set():
    degraded_ok = {n for n, c in METHOD_CONTRACTS.items() if c.degraded_ok}
    assert degraded_ok == {"app.status", "backup.list", "backup.restore", "app.shutdown"}


def test_worker_methods_are_the_expected_set():
    worker = {n for n, c in METHOD_CONTRACTS.items() if c.worker}
    assert worker == {
        "health.check",
        "chat.send",
        "chat.confirm_action",
        "chat.new",
        "chat.history",
        "reminders.reconciliation",
    }


def test_worker_methods_are_never_degraded_ok():
    # in degraded mode there is no worker — a worker method must be blocked, not attempted
    for name, c in METHOD_CONTRACTS.items():
        if c.worker:
            assert not c.degraded_ok, name
