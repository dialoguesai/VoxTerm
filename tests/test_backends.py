"""Tests for the diarization backend registry."""

import pytest


def test_list_backends_includes_all_known():
    from diarization.backends import list_backends
    names = list_backends()
    assert "campplus" in names
    assert "ecapa_tdnn" in names
    assert "titanet" in names
    assert "resemblyzer" in names
    assert "pyannote" in names


def test_get_backend_unknown_raises_value_error():
    from diarization.backends import get_backend
    with pytest.raises(ValueError, match="Unknown diarization backend"):
        get_backend("nonexistent_backend")


def test_get_backend_returns_correct_type():
    from diarization.backends import get_backend, EmbeddingBackend
    backend = get_backend("campplus")
    assert isinstance(backend, EmbeddingBackend)
    assert backend.name == "campplus"
    assert backend.embed_dim == 512


def test_backend_info_matches_instances():
    from diarization.backends import get_backend, BACKEND_INFO
    for name, info in BACKEND_INFO.items():
        backend = get_backend(name)
        assert backend.name == name
        assert backend.embed_dim == info["dim"]


def test_register_custom_backend():
    import numpy as np
    from diarization.backends import EmbeddingBackend, register_backend, get_backend

    class DummyBackend(EmbeddingBackend):
        @property
        def name(self):
            return "_test_dummy"

        @property
        def embed_dim(self):
            return 64

        def load(self):
            pass

        def extract(self, audio, sample_rate=16000):
            return np.zeros(64, dtype=np.float32)

    register_backend("_test_dummy", DummyBackend)
    backend = get_backend("_test_dummy")
    assert backend.embed_dim == 64
    assert backend.extract(np.zeros(16000)) is not None
