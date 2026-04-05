"""Speaker clustering algorithms ported from 3D-Speaker.

Provides spectral clustering with p-value pruning and agglomerative
hierarchical clustering (AHC) for periodic re-clustering of speaker
embeddings. Pure numpy/scipy — no PyTorch dependency.

Reference: github.com/modelscope/3D-Speaker (speakerlab/process/cluster.py)
"""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)


def cosine_affinity(embeddings: np.ndarray) -> np.ndarray:
    """Build cosine similarity affinity matrix from L2-normalized embeddings.

    Args:
        embeddings: (N, D) array of speaker embeddings.

    Returns:
        (N, N) symmetric affinity matrix with zeros on diagonal.
    """
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True) + 1e-10
    X = embeddings / norms
    A = X @ X.T
    np.fill_diagonal(A, 0.0)
    return A


def _pvalue_prune(affinity: np.ndarray, beta: float = 1.0) -> np.ndarray:
    """P-value based pruning of affinity matrix (3D-Speaker method).

    For each row, zero out entries below mean - beta * std. This removes
    spurious weak similarities and produces a cleaner Laplacian.

    Args:
        affinity: (N, N) symmetric affinity matrix (zeros on diagonal).
        beta: pruning aggressiveness (higher = more pruning). Default 1.0.

    Returns:
        Pruned affinity matrix (symmetric).
    """
    n = affinity.shape[0]
    pruned = affinity.copy()

    for i in range(n):
        row = affinity[i].copy()
        row[i] = 0.0  # exclude diagonal
        mask = row > 0
        if mask.sum() < 2:
            continue
        vals = row[mask]
        threshold = vals.mean() - beta * vals.std()
        pruned[i, row < threshold] = 0.0

    # Re-symmetrize: keep entry only if both directions survived pruning
    pruned = np.minimum(pruned, pruned.T)
    return pruned


def _eigengap_k(eigenvalues: np.ndarray, max_k: int, min_k: int = 2) -> int:
    """Estimate number of clusters via eigengap analysis.

    Finds k that maximizes the gap between eigenvalue[k-1] and eigenvalue[k],
    searching from min_k to max_k.

    Args:
        eigenvalues: sorted eigenvalues from Laplacian decomposition.
        max_k: maximum number of clusters to consider.
        min_k: minimum number of clusters (default 2).

    Returns:
        Estimated number of clusters.
    """
    if max_k < min_k:
        return min_k

    gaps = np.diff(eigenvalues[:max_k + 1])
    if len(gaps) < min_k:
        return min_k

    # Search from min_k onwards (skip gaps before min_k)
    search_gaps = gaps[min_k - 1:]
    if len(search_gaps) == 0:
        return min_k

    # Require gap to be significantly above noise
    median_gap = float(np.median(search_gaps)) if len(search_gaps) > 2 else 0.0
    best_idx = int(np.argmax(search_gaps))
    if search_gaps[best_idx] < max(median_gap * 3, 0.005):
        return min_k  # no clear structure

    return best_idx + min_k


def _kmeans(X: np.ndarray, k: int, max_iter: int = 30) -> np.ndarray:
    """K-means clustering with k-means++ initialization.

    Args:
        X: (N, D) feature matrix.
        k: number of clusters.
        max_iter: maximum iterations.

    Returns:
        (N,) array of cluster labels.
    """
    n = X.shape[0]
    if k >= n:
        return np.arange(n)

    rng = np.random.RandomState(42)

    # K-means++ seeding
    centroids = [X[rng.randint(n)]]
    for _ in range(1, k):
        dists = np.min(
            [np.sum((X - c) ** 2, axis=1) for c in centroids], axis=0
        )
        probs = dists / (dists.sum() + 1e-10)
        centroids.append(X[rng.choice(n, p=probs)])
    centroids_arr = np.stack(centroids)

    labels = np.zeros(n, dtype=int)
    for _ in range(max_iter):
        dists = np.stack(
            [np.sum((X - c) ** 2, axis=1) for c in centroids_arr]
        )
        new_labels = dists.argmin(axis=0)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for j in range(k):
            mask = labels == j
            if mask.any():
                centroids_arr[j] = X[mask].mean(axis=0)

    return labels


def spectral_cluster(
    embeddings: np.ndarray,
    max_speakers: int = 8,
    p_value_beta: float = 1.0,
    min_speakers: int = 2,
) -> np.ndarray:
    """Spectral clustering with p-value pruning (3D-Speaker algorithm).

    Args:
        embeddings: (N, D) array of speaker embeddings.
        max_speakers: maximum number of clusters.
        p_value_beta: pruning aggressiveness for affinity matrix.
        min_speakers: minimum number of clusters.

    Returns:
        (N,) array of cluster labels (0-indexed).
    """
    n = embeddings.shape[0]
    if n < 2:
        return np.zeros(n, dtype=int)

    # Build and prune affinity matrix
    A = cosine_affinity(embeddings)
    A = _pvalue_prune(A, beta=p_value_beta)
    np.maximum(A, 0.0, out=A)

    # Symmetric normalized Laplacian: L_sym = I - D^{-1/2} A D^{-1/2}
    degrees = A.sum(axis=1)
    d_inv_sqrt = np.where(degrees > 1e-10, 1.0 / np.sqrt(degrees), 0.0)
    L_sym = np.eye(n) - (d_inv_sqrt[:, None] * A * d_inv_sqrt[None, :])

    # Eigendecomposition (smallest eigenvalues)
    eigenvalues, eigenvectors = np.linalg.eigh(L_sym)

    # Estimate k via eigengap
    max_k = min(max_speakers, n // 2)
    k = _eigengap_k(eigenvalues, max_k, min_k=min_speakers)

    # K-means on row-normalized first k eigenvectors
    features = eigenvectors[:, :k].copy()
    row_norms = np.linalg.norm(features, axis=1, keepdims=True) + 1e-10
    features /= row_norms

    return _kmeans(features, k)


def ahc_cluster(
    embeddings: np.ndarray,
    threshold: float = 0.5,
) -> np.ndarray:
    """Agglomerative hierarchical clustering with cosine distance.

    Uses scipy if available, otherwise falls back to a simple numpy
    implementation. Better than spectral for small sample counts (< 40).

    Args:
        embeddings: (N, D) array of speaker embeddings.
        threshold: cosine distance threshold for merging (0-2 range).

    Returns:
        (N,) array of cluster labels (0-indexed).
    """
    n = embeddings.shape[0]
    if n < 2:
        return np.zeros(n, dtype=int)

    try:
        from scipy.cluster.hierarchy import fcluster, linkage
        from scipy.spatial.distance import squareform

        # Compute cosine distance matrix
        A = cosine_affinity(embeddings)
        np.fill_diagonal(A, 1.0)
        dist_matrix = 1.0 - A
        np.clip(dist_matrix, 0.0, 2.0, out=dist_matrix)
        np.fill_diagonal(dist_matrix, 0.0)

        # Convert to condensed form for linkage
        condensed = squareform(dist_matrix)
        Z = linkage(condensed, method="average")
        labels = fcluster(Z, t=threshold, criterion="distance") - 1  # 0-indexed

        return labels.astype(int)
    except ImportError:
        log.warning("scipy not available, falling back to spectral clustering")
        return spectral_cluster(embeddings)


def auto_cluster(
    embeddings: np.ndarray,
    max_speakers: int = 8,
    threshold: float = 0.5,
    p_value_beta: float = 1.0,
    ahc_max_samples: int = 40,
) -> np.ndarray:
    """Auto-select clustering algorithm based on sample count.

    Uses AHC for small datasets (< ahc_max_samples) and spectral clustering
    for larger ones. Mirrors 3D-Speaker's CommonClustering logic.

    Args:
        embeddings: (N, D) array of speaker embeddings.
        max_speakers: maximum number of clusters.
        threshold: AHC cosine distance threshold.
        p_value_beta: spectral clustering pruning parameter.
        ahc_max_samples: sample count threshold for algorithm selection.

    Returns:
        (N,) array of cluster labels (0-indexed).
    """
    n = embeddings.shape[0]
    if n < 2:
        return np.zeros(n, dtype=int)

    if n < ahc_max_samples:
        return ahc_cluster(embeddings, threshold=threshold)
    else:
        return spectral_cluster(
            embeddings,
            max_speakers=max_speakers,
            p_value_beta=p_value_beta,
        )
