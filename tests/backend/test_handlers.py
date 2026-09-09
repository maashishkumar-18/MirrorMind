"""IPC handlers against injected fakes (Phase 3 Step 3.1a)."""

from __future__ import annotations

import pytest

from src.backend import handlers
from src.backend.wire import MethodError
from src.common.ipc.methods import (
    AppStatusParams,
    BackupListParams,
    BackupRestoreParams,
    ChatHistoryParams,
    ChatNewParams,
    ChatSendParams,
    HealthCheckParams,
    ModelActivateParams,
    ModelCatalogParams,
    ModelDownloadParams,
    ModelStatusParams,
)
from src.models.app_config import AppConfig
from tests.backend.conftest import FakeModelManager, FakeOllama, FakeSessionWorker


def test_app_status_first_launch(ctx_factory, tmp_path):
    cfg_path = str(tmp_path / "app_config.json")
    ctx = ctx_factory(app_config_path=cfg_path)
    res = handlers.HANDLERS["app.status"](AppStatusParams(), ctx, "r")
    assert res.model_setup_required is True
    assert res.active_model is None
    assert res.ready is True and res.degraded is False


def test_app_status_after_activation(ctx_factory, tmp_path):
    cfg_path = str(tmp_path / "app_config.json")
    cfg = AppConfig.load(cfg_path)
    cfg.set_active_model("llama3.1:8b")
    cfg.set_last_exported_at("2026-09-01T00:00:00Z")

    ctx = ctx_factory(app_config_path=cfg_path)
    res = handlers.HANDLERS["app.status"](AppStatusParams(), ctx, "r")
    assert res.model_setup_required is False
    assert res.active_model == "llama3.1:8b"
    assert res.last_exported_at == "2026-09-01T00:00:00Z"


def test_health_check_goes_through_the_worker(ctx_factory):
    from tests.backend.conftest import FakeSessionWorker

    ctx = ctx_factory(worker=FakeSessionWorker(health_ok=True))
    res = handlers.HANDLERS["health.check"](HealthCheckParams(), ctx, "r")
    assert res.ok is True and res.details == ["ok"]


def test_health_check_unavailable_without_worker(ctx_factory):
    from src.backend.wire import MethodError

    with pytest.raises(MethodError) as ei:
        handlers.HANDLERS["health.check"](HealthCheckParams(), ctx_factory(), "r")
    assert ei.value.code == "unavailable"


def test_model_catalog(ctx_factory):
    ctx = ctx_factory(models=FakeModelManager())
    res = handlers.HANDLERS["model.catalog"](ModelCatalogParams(), ctx, "r")
    assert {m.name for m in res.models} == {"llama3.1:8b", "qwen2.5:7b"}
    assert next(m for m in res.models if m.name == "llama3.1:8b").recommended is True


def test_model_status_reports_installed_and_statuses(ctx_factory):
    ollama = FakeOllama(running=True, installed={"llama3.1:8b": 42})
    ctx = ctx_factory(ollama=ollama, models=FakeModelManager(installed={"llama3.1:8b"}))
    res = handlers.HANDLERS["model.status"](ModelStatusParams(), ctx, "r")
    assert res.ollama_running is True
    assert res.installed[0].name == "llama3.1:8b"
    assert res.statuses["llama3.1:8b"] == "available"
    assert res.statuses["qwen2.5:7b"] == "not_installed"


def test_model_download_streams_progress_then_result(ctx_factory):
    events: list[tuple[str, dict, str]] = []
    ctx = ctx_factory(models=FakeModelManager())
    ctx.emit_event = lambda m, p, r: events.append((m, p, r))

    res = handlers.HANDLERS["model.download"](ModelDownloadParams(name="qwen2.5:7b"), ctx, "rid9")
    assert res.model_name == "qwen2.5:7b"
    assert [e[0] for e in events] == ["model.download.progress"] * 3
    assert all(e[2] == "rid9" for e in events)
    assert events[-1][1]["phase"] == "complete"


def test_model_download_failure_raises_method_error(ctx_factory):
    ctx = ctx_factory(models=FakeModelManager(download_error="Not enough disk space"))
    ctx.emit_event = lambda *a: None
    with pytest.raises(MethodError) as ei:
        handlers.HANDLERS["model.download"](ModelDownloadParams(name="qwen2.5:7b"), ctx, "r")
    assert ei.value.code == "model_download_failed"
    assert "disk space" in ei.value.message


def test_model_activate_not_installed(ctx_factory):
    ctx = ctx_factory(models=FakeModelManager(installed=set()))
    with pytest.raises(MethodError) as ei:
        handlers.HANDLERS["model.activate"](ModelActivateParams(name="qwen2.5:7b"), ctx, "r")
    assert ei.value.code == "model_not_installed"


def test_model_activate_success(ctx_factory, tmp_path):
    cfg_path = str(tmp_path / "app_config.json")
    mm = FakeModelManager(installed={"llama3.1:8b"})
    ctx = ctx_factory(models=mm, app_config_path=cfg_path)
    res = handlers.HANDLERS["model.activate"](ModelActivateParams(name="llama3.1:8b"), ctx, "r")
    assert res.active_model == "llama3.1:8b" and res.verified is True
    assert mm.activated == "llama3.1:8b"


def test_backup_list_and_stage_restore_roundtrip(ctx_factory):
    ctx = ctx_factory()
    snap = ctx.backups.create_backup()
    listed = handlers.HANDLERS["backup.list"](BackupListParams(), ctx, "r")
    assert [b.path for b in listed.backups] == [str(snap.path)]

    staged: list = []
    ctx.emit_event = lambda m, p, r: staged.append((m, p))
    ctx.request_restart = lambda path: staged.append(("restart", path))
    res = handlers.HANDLERS["backup.restore"](BackupRestoreParams(path=str(snap.path)), ctx, "r")
    assert res.ok and res.needs_restart
    assert res.validated_snapshot_path is not None
    assert staged[0][0] == "app.restore_staged"
    assert staged[1][0] == "restart"


def test_backup_restore_invalid_snapshot(ctx_factory, tmp_path):
    ctx = ctx_factory()
    bad = tmp_path / "nope.db"
    bad.write_bytes(b"not a database")
    with pytest.raises(MethodError) as ei:
        handlers.HANDLERS["backup.restore"](BackupRestoreParams(path=str(bad)), ctx, "r")
    assert ei.value.code == "restore_invalid"


# -- chat handlers ----------------------------------------------------------


def test_chat_send_maps_worker_result(ctx_factory):
    ctx = ctx_factory(worker=FakeSessionWorker())
    res = handlers.HANDLERS["chat.send"](ChatSendParams(text="hello"), ctx, "r")
    assert res.answer == "echo: hello" and res.tier == 1 and res.retrieve_needed is False


def test_chat_send_no_model_active_propagates(ctx_factory):
    ctx = ctx_factory(worker=FakeSessionWorker(no_model=True))
    with pytest.raises(MethodError) as ei:
        handlers.HANDLERS["chat.send"](ChatSendParams(text="hi"), ctx, "r")
    assert ei.value.code == "no_model_active"


def test_chat_new_and_history(ctx_factory):
    ctx = ctx_factory(worker=FakeSessionWorker())
    new = handlers.HANDLERS["chat.new"](ChatNewParams(), ctx, "r")
    assert new.session_id == "session_0000"
    hist = handlers.HANDLERS["chat.history"](ChatHistoryParams(), ctx, "r")
    assert hist.messages == []


def test_chat_handler_without_worker_is_unavailable(ctx_factory):
    with pytest.raises(MethodError) as ei:
        handlers.HANDLERS["chat.send"](ChatSendParams(text="x"), ctx_factory(), "r")
    assert ei.value.code == "unavailable"


def test_chat_confirm_action_maps_the_worker_result(ctx_factory):
    from src.common.ipc.methods import ChatConfirmActionParams

    ctx = ctx_factory(worker=FakeSessionWorker())
    res = handlers.HANDLERS["chat.confirm_action"](
        ChatConfirmActionParams(pending_action_id="pa-1", choice="reminder"), ctx, "r"
    )
    assert res.action_type == "reminder" and res.feature and res.feature.kind == "reminder"


# -- feature views (Step 3.3) ---------------------------------------------


def _fw(ctx_factory):
    return ctx_factory(worker=FakeSessionWorker())


def _h(name, params, ctx):
    return handlers.HANDLERS[name](params, ctx, "r")


def test_reminders_list_and_lifecycle_map_wire(ctx_factory):
    from src.common.ipc.methods import (
        FeatureIdParams,
        ReminderRescheduleParams,
        RemindersListParams,
    )

    ctx = _fw(ctx_factory)
    lst = _h("reminders.list", RemindersListParams(), ctx)
    assert [r.id for r in lst.reminders] == ["rem_1"]

    done = _h("reminders.complete", FeatureIdParams(id="rem_1"), ctx)
    assert done.reminder.completed_at is not None
    resc = _h(
        "reminders.reschedule",
        ReminderRescheduleParams(id="rem_1", scheduled_time="2026-10-01T09:00:00+00:00"),
        ctx,
    )
    assert resc.reminder.scheduled_time == "2026-10-01T09:00:00+00:00"
    assert _h("reminders.delete", FeatureIdParams(id="rem_1"), ctx).deleted is True


def test_todos_list_and_update_map_wire(ctx_factory):
    from src.common.ipc.methods import FeatureIdParams, TodosListParams, TodoUpdateParams

    ctx = _fw(ctx_factory)
    assert [t.id for t in _h("todos.list", TodosListParams(), ctx).todos] == ["todo_1"]
    upd = _h("todos.update", TodoUpdateParams(id="todo_1", priority="low"), ctx)
    assert upd.todo.priority == "low"
    assert _h("todos.complete", FeatureIdParams(id="todo_1"), ctx).todo.completed_at is not None


def test_meetings_capture_list_get_delete(ctx_factory):
    from src.common.ipc.methods import (
        FeatureIdParams,
        MeetingsCaptureParams,
        MeetingsListParams,
    )

    ctx = _fw(ctx_factory)
    cap = _h("meetings.capture", MeetingsCaptureParams(transcript="Alice: hi"), ctx)
    assert cap.meeting.needs_review is True and cap.meeting.raw_transcript == "Alice: hi"
    assert [m.id for m in _h("meetings.list", MeetingsListParams(), ctx).meetings] == ["mn_1"]
    assert _h("meetings.get", FeatureIdParams(id="mn_1"), ctx).meeting is not None
    assert _h("meetings.get", FeatureIdParams(id="missing"), ctx).meeting is None
    assert _h("meetings.delete", FeatureIdParams(id="mn_1"), ctx).deleted is True


def test_schedule_day_week_and_conflict_result(ctx_factory):
    from src.common.ipc.methods import (
        ScheduleCreateItemParams,
        ScheduleDayParams,
        ScheduleWeekParams,
    )

    ctx = _fw(ctx_factory)
    day = _h("schedule.day", ScheduleDayParams(date="2026-09-08"), ctx)
    assert [i.title for i in day.items] == ["Standup"]

    week = _h("schedule.week", ScheduleWeekParams(start_date="2026-09-07"), ctx)
    assert [d.date for d in week.days] == [f"2026-09-{n:02d}" for n in range(7, 14)]

    # no overwrite → conflict surfaced
    conflict = _h(
        "schedule.create_item",
        ScheduleCreateItemParams(
            title="Dentist",
            start_time="2026-09-08T09:00:00+00:00",
            end_time="2026-09-08T10:00:00+00:00",
        ),
        ctx,
    )
    assert conflict.item is None and conflict.conflict is not None
    assert conflict.conflict.conflicts_with[0].title == "Standup"

    # overwrite → created item
    created = _h(
        "schedule.create_item",
        ScheduleCreateItemParams(
            title="Dentist",
            start_time="2026-09-08T09:00:00+00:00",
            end_time="2026-09-08T10:00:00+00:00",
            overwrite_ids=["sci_1"],
        ),
        ctx,
    )
    assert created.conflict is None and created.item is not None and created.item.id == "sci_new"


def test_feature_method_without_worker_is_unavailable(ctx_factory):
    from src.common.ipc.methods import RemindersListParams

    with pytest.raises(MethodError) as ei:
        handlers.HANDLERS["reminders.list"](RemindersListParams(), ctx_factory(), "r")
    assert ei.value.code == "unavailable"


def test_reminders_reconciliation_maps_the_worker_result(ctx_factory):
    from src.common.ipc.methods import RemindersReconciliationParams
    from src.common.types import ReconciliationResult, Reminder

    recon = ReconciliationResult(
        overdue=[Reminder(id="r1", title="dentist", scheduled_time="2020-01-01T00:00:00+00:00")],
        pending_acknowledgment=[],
    )
    ctx = ctx_factory(worker=FakeSessionWorker(reconciliation=recon))
    res = handlers.HANDLERS["reminders.reconciliation"](RemindersReconciliationParams(), ctx, "r")
    assert [r.id for r in res.overdue] == ["r1"]
    assert res.overdue[0].title == "dentist"
    assert res.pending_acknowledgment == []
