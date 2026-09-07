"""Newline-delimited JSON transport over stdio (Phase 3 Step 3.1a).

The Tauri shell (a later sub-step) spawns the PyInstaller-frozen backend as an
``externalBin`` sidecar and exchanges one ``IPCEnvelope`` JSON object per line
over the child's stdin/stdout. This module is *only* the framing — bytes ⇄
``dict`` — with no knowledge of methods, versions, or dispatch.

Framing rules:

- One envelope per line, UTF-8, ``\\n``-terminated. ``json.dumps`` escapes any
  newline inside a string value as ``\\n`` (two chars), so a multi-paragraph
  LLM answer never splits a frame.
- Blank lines are skipped.
- A line that is not valid JSON does not raise out of :meth:`read_messages`;
  the caller is handed ``None`` for that line (it emits a ``validation_error``
  envelope and keeps going).
- :meth:`send` is guarded by a lock — worker threads emit progress events
  while the main loop writes responses.
"""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Iterator
from typing import BinaryIO


class StdioTransport:
    def __init__(
        self,
        in_stream: BinaryIO | None = None,
        out_stream: BinaryIO | None = None,
    ) -> None:
        self._in = in_stream if in_stream is not None else sys.stdin.buffer
        self._out = out_stream if out_stream is not None else sys.stdout.buffer
        self._write_lock = threading.Lock()

    def read_messages(self) -> Iterator[dict | None]:
        """Yield one parsed envelope per non-blank input line until EOF.

        Yields ``None`` for a line that is present but not valid JSON — the
        dispatcher turns that into a ``validation_error`` and continues.
        """
        for raw_line in self._in:
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                yield None
                continue
            yield obj if isinstance(obj, dict) else None

    def send(self, envelope: dict) -> None:
        """Serialize one envelope and write it as a single line. Thread-safe."""
        line = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n"
        data = line.encode("utf-8")
        with self._write_lock:
            self._out.write(data)
            self._out.flush()
