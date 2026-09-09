"""Log redaction for Report-a-problem (Phase 3 Step 3.4a)."""

from __future__ import annotations

from src.backend.log_redaction import redact, write_redacted_report


def test_redacts_emails():
    assert redact("contact alice@example.com now") == "contact <email> now"


def test_redacts_windows_user_directory():
    line = r"traceback at C:\Users\Maash Kumar\AppData\Local\thing.py line 5"
    out = redact(line)
    assert "Maash Kumar" not in out
    assert r"C:\Users\<user>\AppData" in out


def test_redacts_posix_home():
    assert "/home/<user>/notes" in redact("opened /home/alice/notes")


def test_redacts_long_hex_runs():
    key = "a" * 64
    assert key not in redact(f"key={key} loaded")
    assert "<hex>" in redact(f"key={key} loaded")


def test_redacts_ipv4():
    assert redact("connect to 192.168.1.42:11434") == "connect to <ip>:11434"


def test_redacts_token_assignments():
    assert redact("Authorization: Bearer abc.def.ghi") == "Authorization <redacted>"
    assert redact("password = hunter2") == "password <redacted>"


def test_redact_is_idempotent():
    line = r"alice@example.com from C:\Users\bob\x at 10.0.0.1 with api_key=deadbeefdeadbeef"
    once = redact(line)
    assert redact(once) == once


def test_write_redacted_report_concatenates_oldest_first(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "backend.log.2").write_text("OLDEST alice@example.com\n", encoding="utf-8")
    (log_dir / "backend.log.1").write_text("MIDDLE\n", encoding="utf-8")
    (log_dir / "backend.log").write_text("NEWEST\n", encoding="utf-8")

    dest = tmp_path / "report.txt"
    n = write_redacted_report(dest, source=log_dir / "backend.log")

    body = dest.read_text(encoding="utf-8")
    assert body == "OLDEST <email>\nMIDDLE\nNEWEST\n"
    assert n == len(body.encode("utf-8"))
    assert not (tmp_path / "report.txt.tmp").exists()


def test_write_redacted_report_handles_a_missing_active_file(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "backend.log.1").write_text("only a rotation\n", encoding="utf-8")

    dest = tmp_path / "report.txt"
    write_redacted_report(dest, source=log_dir / "backend.log")
    assert dest.read_text(encoding="utf-8") == "only a rotation\n"
