"""
Integration tests for LocalEmbeddingProvider (src/ingestion/local_embedder.py)
— Phase 1 Step 1.1.

Marked `integration`: these load the real vendored all-MiniLM-L6-v2 weights
from rag-pipeline/models/. The path-resolution tests are pure and don't load
the model.
"""

import numpy as np
import pytest

from src.ingestion.embedder import EmbeddingProvider, ProviderFactory
from src.ingestion.local_embedder import LocalEmbeddingProvider, resolve_model_path

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def provider():
    return LocalEmbeddingProvider()


def test_is_registered_in_provider_factory():
    p = ProviderFactory.create("local")
    assert isinstance(p, LocalEmbeddingProvider)
    assert isinstance(p, EmbeddingProvider)
    assert p.provider_name == "local"


def test_embed_batch_shape_and_dtype(provider):
    out = provider.embed_batch(["hello world", "another sentence", "third one"])
    assert isinstance(out, np.ndarray)
    assert out.shape == (3, 384)
    assert out.dtype == np.float32


def test_embed_query_shape(provider):
    v = provider.embed_query("what did we decide about lunch")
    assert isinstance(v, np.ndarray)
    assert v.shape == (384,)


def test_empty_batch_returns_empty_matrix(provider):
    out = provider.embed_batch([])
    assert out.shape[0] == 0


def _cos(a, b):
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def test_semantic_similarity(provider):
    a, b = provider.embed_batch(
        [
            "Remind me to call the dentist tomorrow morning",
            "I need to phone the dentist first thing tomorrow",
        ]
    )
    unrelated = provider.embed_query("The quarterly tax return is overdue")
    assert _cos(a, b) > 0.7
    assert _cos(a, b) > _cos(a, unrelated)


def test_count_tokens_uses_hf_tokenizer(provider):
    short = provider.count_tokens("hello")
    longer = provider.count_tokens("hello there this is a longer sentence with more words")
    assert 0 < short < longer


def test_usage_tracking(provider):
    provider.reset_usage()
    provider.embed_batch(["a", "b", "c"])
    usage = provider.get_usage()
    assert usage["provider"] == "local"
    assert usage["total_texts"] == 3
    assert usage["total_tokens"] > 0
    assert usage["dimensions"] == 384


# --- path resolution (no model load) --------------------------------------


def test_resolve_prefers_meipass(monkeypatch, tmp_path):
    fake = tmp_path / "models" / "all-MiniLM-L6-v2"
    fake.mkdir(parents=True)
    (fake / "config.json").write_text("{}")
    monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)
    assert resolve_model_path() == fake


def test_resolve_uses_env_override(monkeypatch, tmp_path):
    monkeypatch.delattr("sys._MEIPASS", raising=False)
    target = tmp_path / "custom-model"
    target.mkdir()
    (target / "config.json").write_text("{}")
    monkeypatch.setenv("RAGPIPE_EMBEDDING_MODEL_PATH", str(target))
    assert resolve_model_path() == target


def test_resolve_falls_back_to_bundled(monkeypatch):
    monkeypatch.delattr("sys._MEIPASS", raising=False)
    monkeypatch.delenv("RAGPIPE_EMBEDDING_MODEL_PATH", raising=False)
    path = resolve_model_path()
    assert path.name == "all-MiniLM-L6-v2"
    assert (path / "config.json").is_file()


def test_resolve_missing_path_raises(monkeypatch, tmp_path):
    monkeypatch.delattr("sys._MEIPASS", raising=False)
    monkeypatch.setenv("RAGPIPE_EMBEDDING_MODEL_PATH", str(tmp_path / "nope"))
    with pytest.raises(FileNotFoundError):
        resolve_model_path()
