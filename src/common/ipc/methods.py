"""The IPC **method contract** — request ``params`` and response ``result``
models for every method the backend serves (Phase 3 Step 3.1a).

This is the Python half of project_logic.md §7's "zod (TypeScript) + Pydantic
(Python)" contract. The envelope (``envelope.py``) frames every message and
carries the ``version``; this module types what rides inside ``payload`` for a
given ``method``. A ``ipc/schema/methods.ts`` zod mirror + a cross-language
round-trip test land with the frontend sub-step.

Everything here is **additive under IPC version 1** — a new optional field or a
new method does not bump ``CURRENT_IPC_VERSION``; removing/retyping a field
does.

Payload shapes (all under the frozen ``IPCEnvelope.payload``):

    request   {"method": <name>, "params": {...}}
    response  {"method": <name>, "result": {...}}
    error     {"method": <name>?, "code": <slug>, "message": <text>}
    event     {"method": <name>, "params": {...}}
"""

from __future__ import annotations

from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, Field

# ``model_*`` field names (model.catalog / model.download results) collide with
# Pydantic v2's protected namespace; these are wire DTOs, not models with
# behaviour, so opting out is safe and silences the warning.
_WIRE = ConfigDict(extra="forbid", protected_namespaces=())


class _Params(BaseModel):
    """Base for every request-params model: reject unknown fields so a stale
    or malformed frontend call surfaces as ``validation_error``, not a
    silently-ignored argument."""

    model_config = _WIRE


class _Result(BaseModel):
    model_config = _WIRE


# --------------------------------------------------------------------------
# app.status
# --------------------------------------------------------------------------
class AppStatusParams(_Params):
    pass


class AppStatusResult(_Result):
    ipc_version: int
    model_setup_required: bool
    active_model: str | None
    last_exported_at: str | None
    degraded: bool
    ready: bool


# --------------------------------------------------------------------------
# health.check
# --------------------------------------------------------------------------
class HealthCheckParams(_Params):
    pass


class HealthCheckResult(_Result):
    ok: bool
    details: list[str]


# --------------------------------------------------------------------------
# model.catalog
# --------------------------------------------------------------------------
class CatalogEntry(_Result):
    name: str
    display_name: str
    size_bytes: int
    description: str
    min_ram_gb: float
    recommended: bool


class ModelCatalogParams(_Params):
    pass


class ModelCatalogResult(_Result):
    models: list[CatalogEntry]


# --------------------------------------------------------------------------
# model.status
# --------------------------------------------------------------------------
class ModelStatusParams(_Params):
    #: None → status for the catalog + everything installed.
    names: list[str] | None = None


class InstalledEntry(_Result):
    name: str
    size_bytes: int


class ModelStatusResult(_Result):
    ollama_running: bool
    installed: list[InstalledEntry]
    #: model name → "not_installed" | "downloading" | "available" | "active"
    statuses: dict[str, str]


# --------------------------------------------------------------------------
# model.download  (streaming — model.download.progress events, then a result)
# --------------------------------------------------------------------------
class ModelDownloadParams(_Params):
    name: str = Field(min_length=1)


class ModelDownloadProgressEvent(_Result):
    name: str
    phase: str
    percent: float
    speed_mbps: float
    eta_seconds: float | None
    message: str | None


class ModelDownloadResult(_Result):
    model_name: str
    status: str
    restarts: int
    resumes: int
    verified: bool


# --------------------------------------------------------------------------
# model.activate
# --------------------------------------------------------------------------
class ModelActivateParams(_Params):
    name: str = Field(min_length=1)


class ModelActivateResult(_Result):
    active_model: str
    verified: bool


# --------------------------------------------------------------------------
# backup.list
# --------------------------------------------------------------------------
class BackupListParams(_Params):
    pass


class BackupEntry(_Result):
    path: str
    created_at: str
    size_bytes: int


class BackupListResult(_Result):
    backups: list[BackupEntry]


# --------------------------------------------------------------------------
# backup.restore  (validate-only; the Rust supervisor performs the swap)
# --------------------------------------------------------------------------
class BackupRestoreParams(_Params):
    path: str = Field(min_length=1)


class BackupRestoreResult(_Result):
    ok: bool
    needs_restart: bool
    detail: str
    validated_snapshot_path: str | None


# --------------------------------------------------------------------------
# app.shutdown
# --------------------------------------------------------------------------
class AppShutdownParams(_Params):
    pass


class AppShutdownResult(_Result):
    stopping: bool


# --------------------------------------------------------------------------
# Lifecycle events (server-initiated; no request/params from the frontend)
# --------------------------------------------------------------------------
class AppReadyEvent(_Result):
    ipc_version: int
    model_setup_required: bool
    active_model: str | None


class AppIntegrityFailedEvent(_Result):
    details: list[str]


class AppPreviousDataUnrecoverableEvent(_Result):
    message: str


class AppRestoreStagedEvent(_Result):
    validated_snapshot_path: str


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------
class MethodContract(NamedTuple):
    params: type[BaseModel]
    result: type[BaseModel]
    #: served while the backend is in degraded (integrity-failed) mode
    degraded_ok: bool = False


METHOD_CONTRACTS: dict[str, MethodContract] = {
    "app.status": MethodContract(AppStatusParams, AppStatusResult, degraded_ok=True),
    "health.check": MethodContract(HealthCheckParams, HealthCheckResult),
    "model.catalog": MethodContract(ModelCatalogParams, ModelCatalogResult),
    "model.status": MethodContract(ModelStatusParams, ModelStatusResult),
    "model.download": MethodContract(ModelDownloadParams, ModelDownloadResult),
    "model.activate": MethodContract(ModelActivateParams, ModelActivateResult),
    "backup.list": MethodContract(BackupListParams, BackupListResult, degraded_ok=True),
    "backup.restore": MethodContract(BackupRestoreParams, BackupRestoreResult, degraded_ok=True),
    "app.shutdown": MethodContract(AppShutdownParams, AppShutdownResult, degraded_ok=True),
}
