"""
Tests for the EmbeddingGenerator default flip to the local provider
(src/ingestion/embedder.py) — Phase 1 Step 1.1.
"""

import numpy as np
import pytest

from src.ingestion.embedder import EmbeddingGenerator
from src.ingestion.local_embedder import LocalEmbeddingProvider

pytestmark = pytest.mark.integration


def test_default_generator_uses_local_provider():
    gen = EmbeddingGenerator(enable_logging=False)
    assert gen.model == "local"
    assert isinstance(gen.provider, LocalEmbeddingProvider)
    assert gen.dimensions == 384


def test_estimate_cost_is_gone():
    assert not hasattr(EmbeddingGenerator, "estimate_cost")


def test_embed_query_roundtrip_offline():
    gen = EmbeddingGenerator(enable_logging=False)
    vec = gen.embed_query("remind me to water the plants")
    assert vec is not None
    assert np.asarray(vec).shape == (384,)


def test_embed_queries_batch_offline():
    gen = EmbeddingGenerator(enable_logging=False)
    out = gen.embed_queries(["buy milk", "", "call mum"])
    assert len(out) == 3
    assert np.asarray(out[0]).shape == (384,)
    assert out[1] is None  # blank entry
    assert np.asarray(out[2]).shape == (384,)
