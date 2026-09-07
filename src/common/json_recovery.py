"""Best-effort JSON recovery for small-local-model output (Phase 3 Step 3.1d).

Lifted verbatim from ``RetrievalAgent._parse_json`` — the same fence-strip /
``{...}`` regex / brace-balance-repair sequence was copied into
``MetadataExtractor`` and ``MeetingNoteHandler`` independently. One home now.

7B/8B models on CPU routinely wrap JSON in a markdown fence, prepend a
sentence of prose, or drop the closing brace after the final string value.
``recover_json`` handles those three; a genuinely unparseable payload raises
``ValueError`` (callers catch it and fall back to a safe default).
"""

from __future__ import annotations

import json
import re


def recover_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        # Small local models routinely drop the closing brace after the final
        # string value — balance braces and retry once.
        start = text.find("{")
        opened = text.count("{") - text.count("}")
        if start != -1 and opened > 0:
            repaired = text[start:].rstrip().rstrip(",") + "}" * opened
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass
        raise ValueError(f"could not parse JSON from model output: {raw[:200]}") from e
