"""Tests for diarization/cluster.py — 3D-Speaker clustering algorithms."""

import numpy as np
import pytest

from audio.diarization.cluster import (
    ahc_cluster,
    auto_cluster,
    cosine_affinity,
    spectral_cluster,
    _pvalue_prune,
)


def _make_clusters(n_per_cluster: int = 20, n_clusters: int = 3, dim: int = 512, noise: float = 0.1):
    """Generate synthetic embeddings with known cluster assignments."""
    rng = np.random.RandomState(42)
    embeddings = []
    labels = []
    centers = rng.randn(n_clusters, dim).astype(np.float32)
    # L2-normalize centers
    centers /= np.linalg.norm(centers, axis=1, keepdims=True)

    for i, center in enumerate(centers):
        for _ in range(n_per_cluster):
            emb = center + rng.randn(dim).astype(np.float32) * noise
            emb /= np.linalg.norm(emb) + 1e-10
            embeddings.append(emb)
            labels.append(i)

    return np.stack(embeddings), np.array(labels)


class TestCosineAffinity:
    def test_shape(self):
        X = np.random.randn(10, 64).astype(np.float32)
        A = cosine_affinity(X)
        assert A.shape == (10, 10)

    def test_diagonal_zero(self):
        X = np.random.randn(5, 32).astype(np.float32)
        A = cosine_affinity(X)
        np.testing.assert_array_equal(np.diag(A), 0.0)

    def test_symmetric(self):
        X = np.random.randn(8, 64).astype(np.float32)
        A = cosine_affinity(X)
        np.testing.assert_allclose(A, A.T, atol=1e-6)


class TestPvaluePrune:
    def test_reduces_entries(self):
        X = np.random.randn(20, 64).astype(np.float32)
        A = cosine_affinity(X)
        np.maximum(A, 0.0, out=A)
        pruned = _pvalue_prune(A, beta=1.0)
        # Pruning should zero out some entries
        assert (pruned == 0).sum() >= (A == 0).sum()

    def test_stays_symmetric(self):
        X = np.random.randn(15, 64).astype(np.float32)
        A = cosine_affinity(X)
        np.maximum(A, 0.0, out=A)
        pruned = _pvalue_prune(A, beta=1.0)
        np.testing.assert_allclose(pruned, pruned.T, atol=1e-6)


class TestSpectralCluster:
    def test_finds_correct_clusters(self):
        X, true_labels = _make_clusters(n_per_cluster=20, n_clusters=3, noise=0.05)
        pred = spectral_cluster(X, max_speakers=8)

        # Check that predicted labels partition correctly
        # (labels may be permuted, so check by unique groupings)
        from collections import defaultdict
        groups = defaultdict(set)
        for i, lbl in enumerate(pred):
            groups[lbl].add(true_labels[i])

        # Each cluster should map to exactly one true label
        for lbl, true_set in groups.items():
            assert len(true_set) == 1, f"Cluster {lbl} maps to multiple true labels: {true_set}"

    def test_single_sample(self):
        X = np.random.randn(1, 64).astype(np.float32)
        labels = spectral_cluster(X)
        assert len(labels) == 1
        assert labels[0] == 0

    def test_two_speakers(self):
        X, _ = _make_clusters(n_per_cluster=15, n_clusters=2, noise=0.05)
        pred = spectral_cluster(X, max_speakers=4)
        assert len(set(pred)) == 2


class TestAhcCluster:
    def test_finds_clusters(self):
        X, true_labels = _make_clusters(n_per_cluster=10, n_clusters=3, noise=0.05)
        pred = ahc_cluster(X, threshold=0.5)
        n_pred_clusters = len(set(pred))
        # Should find at least 2 clusters (exact count depends on threshold)
        assert n_pred_clusters >= 2

    def test_single_sample(self):
        X = np.random.randn(1, 64).astype(np.float32)
        labels = ahc_cluster(X)
        assert len(labels) == 1


class TestAutoCluster:
    def test_small_uses_ahc(self):
        """For < 40 samples, auto_cluster should use AHC."""
        X, _ = _make_clusters(n_per_cluster=10, n_clusters=3, noise=0.05)
        assert len(X) == 30  # < 40
        labels = auto_cluster(X, max_speakers=8, threshold=0.5, ahc_max_samples=40)
        assert len(labels) == 30
        assert len(set(labels)) >= 2

    def test_large_uses_spectral(self):
        """For >= 40 samples, auto_cluster should use spectral."""
        X, _ = _make_clusters(n_per_cluster=20, n_clusters=3, noise=0.05)
        assert len(X) == 60  # >= 40
        labels = auto_cluster(X, max_speakers=8, threshold=0.5, ahc_max_samples=40)
        assert len(labels) == 60
        assert len(set(labels)) >= 2
