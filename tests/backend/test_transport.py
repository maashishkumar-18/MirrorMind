"""StdioTransport framing (Phase 3 Step 3.1a)."""

from __future__ import annotations

import io
import json
import threading

from src.backend.transport import StdioTransport


def _transport(data: bytes) -> tuple[StdioTransport, io.BytesIO]:
    out = io.BytesIO()
    return StdioTransport(io.BytesIO(data), out), out


def test_reads_one_envelope_per_line():
    t, _ = _transport(b'{"a":1}\n{"b":2}\n')
    assert list(t.read_messages()) == [{"a": 1}, {"b": 2}]


def test_skips_blank_lines():
    t, _ = _transport(b'\n\n{"a":1}\n\n')
    assert list(t.read_messages()) == [{"a": 1}]


def test_non_json_line_yields_none_and_stream_continues():
    t, _ = _transport(b'not json\n{"a":1}\n')
    assert list(t.read_messages()) == [None, {"a": 1}]


def test_json_that_is_not_an_object_yields_none():
    t, _ = _transport(b'[1,2,3]\n"str"\n42\n')
    assert list(t.read_messages()) == [None, None, None]


def test_unicode_round_trips():
    t, out = _transport(b"")
    t.send({"msg": "café — \U0001f600"})
    assert json.loads(out.getvalue().decode("utf-8"))["msg"] == "café — \U0001f600"


def test_multiline_string_value_is_one_frame():
    """A multi-paragraph LLM answer has literal newlines in a string value;
    json.dumps escapes them to \\n so the frame never splits."""
    t, out = _transport(b"")
    answer = "para one\n\npara two\nwith a third line"
    t.send({"payload": {"result": {"text": answer}}})
    written = out.getvalue()
    assert written.count(b"\n") == 1  # exactly the frame terminator
    back = list(StdioTransport(io.BytesIO(written), io.BytesIO()).read_messages())
    assert back == [{"payload": {"result": {"text": answer}}}]


def test_send_is_thread_safe():
    t, out = _transport(b"")
    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            for i in range(50):
                t.send({"w": n, "i": i})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert not errors
    lines = out.getvalue().decode().strip().split("\n")
    assert len(lines) == 8 * 50
    for line in lines:  # every line is a complete, parseable object
        json.loads(line)
