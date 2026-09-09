"""Log redaction for "Report a problem" (Phase 3 Step 3.4a, project_logic.md §12 /
roadmap Step 3.4 acceptance: "no raw message content in the log").

The backend's own log calls are already content-free (project_logic §6.1 — e.g.
``SessionWorker._record_metrics`` writes ``query=""``), so this is defence in
depth: a stray third-party log line or a traceback frame could still carry a
path, an address, or a token. ``redact`` runs over every log line the frontend
ever sees — both ``diagnostics.logs`` (the in-app viewer) and
``diagnostics.report`` (the file the user attaches to an e-mail).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from src.backend.paths import log_file

_SUBSTITUTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # e-mail addresses
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<email>"),
    # Windows user directory:  C:\Users\alice\...  ->  C:\Users\<user>\...
    (re.compile(r"([A-Za-z]:[\\/]Users[\\/])[^\\/\r\n]+", re.IGNORECASE), r"\1<user>"),
    # POSIX home directory:  /home/alice/...  /Users/alice/...
    (re.compile(r"(/(?:home|Users)/)[^/\s]+"), r"\1<user>"),
    # bearer tokens / api keys / passwords — redact the rest of the line after
    # the keyword (deliberately greedy: a diagnostic log leaking a credential is
    # far worse than losing the tail of one line).
    (
        re.compile(r"(?i)\b(authorization|bearer|api[_-]?key|secret|password)\b[\"'\s:=]+.+"),
        r"\1 <redacted>",
    ),
    # long hex runs — encryption keys, request ids, hashes
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "<hex>"),
    # IPv4 addresses
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
)


def redact(text: str) -> str:
    """Apply every scrubber in order. Idempotent — running it twice is a no-op."""
    for pattern, replacement in _SUBSTITUTIONS:
        text = pattern.sub(replacement, text)
    return text


def _log_files_oldest_first(active: Path) -> list[Path]:
    """``backend.log.3`` … ``backend.log.1`` then ``backend.log`` — chronological."""
    rotations = sorted(
        (p for p in active.parent.glob(f"{active.name}.*") if p.suffix.lstrip(".").isdigit()),
        key=lambda p: int(p.suffix.lstrip(".")),
        reverse=True,
    )
    return [*rotations, active] if active.exists() else rotations


def write_redacted_report(dest: str | Path, *, source: Path | None = None) -> int:
    """Concatenate the rotating log (oldest first), redact it, and write it to
    ``dest`` atomically (tmp + ``os.replace``). Returns the byte count written."""
    dest = Path(dest)
    active = source or log_file()
    parts = _log_files_oldest_first(active)

    chunks: list[str] = []
    for part in parts:
        try:
            chunks.append(part.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    body = redact("".join(chunks))

    tmp = dest.with_name(dest.name + ".tmp")
    try:
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return len(body.encode("utf-8"))
