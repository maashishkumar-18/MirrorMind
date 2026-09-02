"""
Unit tests for the session MetadataExtractor
(src/ingestion/metadata_extractor.py) — Phase 1 Step 1.2. The single LLM
call (`simple_generate`) is mocked.
"""

import json
from unittest.mock import patch

import pytest

from src.common.types import SessionMessage
from src.ingestion.metadata_extractor import MetadataExtractor, SessionMetadata

pytestmark = pytest.mark.unit

MESSAGES = [
    SessionMessage("user", "Remind me to call the dentist tomorrow at 9am"),
    SessionMessage("assistant", "Got it — reminder set for 9am tomorrow."),
]


def _mock_llm(payload):
    return patch(
        "src.ingestion.metadata_extractor.simple_generate",
        return_value=json.dumps(payload) if not isinstance(payload, str) else payload,
    )


def test_extract_parses_all_fields():
    payload = {
        "topics": ["dentist appointment", "reminders"],
        "action_types": ["reminder"],
        "entities": ["dentist"],
        "sentiment": "neutral",
        "confidence": 0.82,
    }
    with _mock_llm(payload):
        md = MetadataExtractor().extract("s1", MESSAGES, "2026-09-02T10:00:00Z")
    assert isinstance(md, SessionMetadata)
    assert md.session_id == "s1"
    assert md.timestamp == "2026-09-02T10:00:00Z"
    assert md.topics == ["dentist appointment", "reminders"]
    assert md.action_types == ["reminder"]
    assert md.entities == ["dentist"]
    assert md.sentiment == "neutral"
    assert md.extraction_confidence == pytest.approx(0.82)


def test_unknown_action_types_and_sentiment_are_dropped_or_defaulted():
    with _mock_llm(
        {"topics": ["x"], "action_types": ["reminder", "buy_stuff"], "sentiment": "grumpy"}
    ):
        md = MetadataExtractor().extract("s1", MESSAGES, "t")
    assert md.action_types == ["reminder"]  # "buy_stuff" not an AgenticActionType
    assert md.sentiment == "neutral"  # "grumpy" not valid
    assert md.extraction_confidence == 0.0


def test_markdown_fenced_json_is_recovered():
    fenced = '```json\n{"topics": ["a"], "sentiment": "positive"}\n```'
    with _mock_llm(fenced):
        md = MetadataExtractor().extract("s1", MESSAGES, "t")
    assert md.topics == ["a"]
    assert md.sentiment == "positive"


def test_extract_with_retry_falls_back_to_empty_metadata_on_bad_json():
    with patch("src.ingestion.metadata_extractor.simple_generate", return_value="not json at all"):
        md = MetadataExtractor().extract_with_retry("s1", MESSAGES, "t", max_retries=1)
    assert md == SessionMetadata(session_id="s1", timestamp="t")
    assert md.topics == [] and md.action_types == [] and md.extraction_confidence == 0.0


def test_prepare_sample_uses_role_labels():
    sample = MetadataExtractor()._prepare_sample(MESSAGES)
    assert "[User]: Remind me to call the dentist" in sample
    assert "[Assistant]: Got it" in sample
