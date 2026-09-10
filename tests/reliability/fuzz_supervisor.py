"""Subprocess-kill fuzz harness (Phase 4 Step 4.4b).

Spawns a real ``python -m src.backend.main`` sidecar (LLM + retrieval stubbed
for a sub-second warm-up), drives IPC traffic, ``SIGKILL``s it at a chosen
point, relaunches against the same data dir, and lets the caller assert the
invariants: the DB still passes a full ``PRAGMA integrity_check``, every
acknowledged ``chat.send`` write survived, the crashed session was finalized on
relaunch, and the relaunch reached ``app.ready`` (no silent hang).

Not a pytest module itself — ``test_kill_recover.py`` drives it.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable
DB_KEY = "0" * 64

# The three windows a kill can land in.
KILL_POINTS = ("ipc", "db_write", "inference")


@dataclass
class _Proc:
    popen: subprocess.Popen
    frames: queue.Queue[dict] = field(default_factory=queue.Queue)
    ready: threading.Event = field(default_factory=threading.Event)
    _events: list[dict] = field(default_factory=list)

    def _reader(self) -> None:
        assert self.popen.stdout is not None
        for line in self.popen.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue
            if frame.get("message_type") == "event":
                self._events.append(frame)
                if frame.get("payload", {}).get("method") == "app.ready":
                    self.ready.set()
            else:
                self.frames.put(frame)


class Sidecar:
    """One backend process lifecycle."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.proc: _Proc | None = None

    # -- lifecycle --------------------------------------------------------

    def start(self, *, ready_timeout: float = 30.0) -> None:
        env = {
            **os.environ,
            "RAGPIPE_DATA_DIR": str(self.data_dir),
            "RAGPIPE_DB_KEY": DB_KEY,
            "RAGPIPE_FAKE_LLM": str(REPO_ROOT / "tests" / "e2e" / "fixtures" / "reminder.json"),
            "RAGPIPE_FAKE_RETRIEVAL": "1",
            "RAGPIPE_FAKE_TOAST": str(self.data_dir / "toasts.jsonl"),
            "LANGFUSE_PUBLIC_KEY": "",
            "LANGFUSE_SECRET_KEY": "",
            "PYTHONUNBUFFERED": "1",
        }
        popen = subprocess.Popen(
            [PYTHON, "-m", "src.backend.main"],
            cwd=str(REPO_ROOT),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self.proc = _Proc(popen=popen)
        threading.Thread(target=self.proc._reader, daemon=True).start()
        if not self.proc.ready.wait(ready_timeout):
            self.kill()
            raise TimeoutError("backend did not reach app.ready")

    def kill(self) -> None:
        if self.proc and self.proc.popen.poll() is None:
            self.proc.popen.kill()
            try:
                self.proc.popen.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    def shutdown_clean(self) -> int:
        """Close stdin (EOF) and wait for a clean exit."""
        assert self.proc is not None
        try:
            self.proc.popen.stdin.close()  # type: ignore[union-attr]
        except OSError:
            pass
        try:
            return self.proc.popen.wait(timeout=25)
        except subprocess.TimeoutExpired:
            self.kill()
            return -1

    # -- IPC -------------------------------------------------------------

    def send(self, method: str, params: dict, *, timeout: float = 20.0) -> dict | None:
        """Fire a request; return the correlated response/error frame, or None
        if the process died first."""
        assert self.proc is not None
        rid = f"fz-{uuid.uuid4().hex[:8]}"
        env = {
            "version": 1,
            "message_type": "request",
            "request_id": rid,
            "timestamp": "2026-01-01T00:00:00Z",
            "payload": {"method": method, "params": params},
        }
        try:
            self.proc.popen.stdin.write(json.dumps(env) + "\n")  # type: ignore[union-attr]
            self.proc.popen.stdin.flush()  # type: ignore[union-attr]
        except (OSError, ValueError):
            return None
        deadline = time.time() + timeout
        pending: list[dict] = []
        while time.time() < deadline:
            if self.proc.popen.poll() is not None:
                return None
            try:
                frame = self.proc.frames.get(timeout=0.5)
            except queue.Empty:
                continue
            if frame.get("request_id") == rid:
                return frame
            pending.append(frame)
        return None

    def saw_event(self, method: str) -> bool:
        return any(
            e.get("payload", {}).get("method") == method
            for e in (self.proc._events if self.proc else [])
        )
