from __future__ import annotations

from dataclasses import dataclass
from math import ceil, log, sqrt
from typing import Any

from . import numerical as n
from .model import Grain


@dataclass(slots=True)
class _Candidate:
    k: int
    score: float | None
    standard_error: float | None
    valid: bool
    reason: str | None
    sizes: list[int]


def _minimum_cluster_size(row_count: int) -> int:
    """Protect Auto K against nuisance clusters that are too small to interpret."""
    return max(5, min(30, int(ceil(row_count * 0.03))))


def _adaptive_max_k(row_count: int, minimum_size: int) -> int:
    if row_count <= 0:
        return 1
    return max(1, min(8, row_count // max(1, minimum_size)))


def _fit_kmeans(matrix: Any, k: int, seed: int) -> tuple[Any, Any, float]:
    if k == 1:
        labels = n.np.zeros(len(matrix), dtype=int)
        centers = n.np.mean(matrix, axis=0, keepdims=True)
        inertia = float(n.np.sum((matrix - centers[0]) ** 2))
        return labels, centers, inertia
    model = n.KMeans(n_clusters=k, random_state=seed, n_init=10)
    labels = model.fit_predict(matrix)
    return labels, model.cluster_centers_, float(model.inertia_)


def _fit_gmm(matrix: Any, k: int, seed: int) -> tuple[Any, Any, Any, float]:
    if len(matrix) == 1 and k == 1:
        labels = n.np.zeros(1, dtype=int)
        centers = n.np.asarray(matrix, dtype=float).reshape(1, -1)
        return labels, centers, n.np.ones(1, dtype=float), 0.0
    model = n.GaussianMixture(n_components=k, random_state=seed, n_init=3)
    labels = model.fit_predict(matrix)
    probabilities = n.np.max(model.predict_proba(matrix), axis=1)
    return labels, model.means_, probabilities, float(model.bic(matrix))


def _cluster_sizes(labels: Any, k: int) -> list[int]:
    return [int(n.np.sum(labels == cluster)) for cluster in range(k)]


def _candidate_validity(k: int, sizes: list[int], minimum_size: int) -> tuple[bool, str | None]:
    if k == 1:
        return True, None
    if not sizes:
        return False, "clustering returned no cluster sizes"
    smallest = min(sizes)
    if smallest < minimum_size:
        return False, f"minimum cluster size {smallest} < required {minimum_size}"
    return True, None


def _kmeans_bic_candidates(
    matrix: Any,
    max_k: int,
    minimum_size: int,
    seed: int,
) -> list[_Candidate]:
    """Score candidate K with full-covariance Gaussian-mixture BIC, including K=1.

    Candidate validity is still evaluated on the actual K-means partition, so
    minimum-cluster-size protection describes the model that will be returned.
    Full covariance is important here: a spherical Gaussian proxy can reduce BIC
    by fragmenting one elongated cluster into many nuisance components.
    """
    candidates: list[_Candidate] = []
    for k in range(1, max_k + 1):
        try:
            labels, _, _ = _fit_kmeans(matrix, k, seed)
            sizes = _cluster_sizes(labels, k)
            valid, reason = _candidate_validity(k, sizes, minimum_size)
            _, _, _, bic = _fit_gmm(matrix, k, seed)
            candidates.append(_Candidate(k, bic, None, valid, reason, sizes))
        except Exception as exc:
            candidates.append(_Candidate(k, None, None, False, str(exc), []))
    return candidates


def _bic_candidates(matrix: Any, max_k: int, minimum_size: int, seed: int) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for k in range(1, max_k + 1):
        try:
            labels, _, _, bic = _fit_gmm(matrix, k, seed)
            sizes = _cluster_sizes(labels, k)
            valid, reason = _candidate_validity(k, sizes, minimum_size)
            candidates.append(_Candidate(k, bic, None, valid, reason, sizes))
        except Exception as exc:
            candidates.append(_Candidate(k, None, None, False, str(exc), []))
    return candidates


def _choose_k(matrix: Any, method: str, seed: int) -> tuple[int, list[_Candidate], int, int]:
    row_count = len(matrix)
    if row_count <= 0:
        raise ValueError("no complete rows remain for automatic clustering")
    minimum_size = min(row_count, _minimum_cluster_size(row_count))
    max_k = min(row_count, _adaptive_max_k(row_count, minimum_size))
    if method == "kmeans":
        candidates = _kmeans_bic_candidates(matrix, max_k, minimum_size, seed)
        valid = [candidate for candidate in candidates if candidate.valid and candidate.score is not None]
        selected = min(valid, key=lambda item: (float(item.score), item.k)).k if valid else 1
        return selected, candidates, minimum_size, max_k
    if method == "gmm":
        candidates = _bic_candidates(matrix, max_k, minimum_size, seed)
        valid = [candidate for candidate in candidates if candidate.valid and candidate.score is not None]
        selected = min(valid, key=lambda item: (float(item.score), item.k)).k if valid else 1
        return selected, candidates, minimum_size, max_k
    raise ValueError(f"unsupported clustering method: {method}")


def _fit_selected(matrix: Any, method: str, k: int, seed: int) -> tuple[Any, Any, Any | None]:
    if method == "kmeans":
        labels, centers, _ = _fit_kmeans(matrix, k, seed)
        return labels, centers, None
    labels, centers, probabilities, _ = _fit_gmm(matrix, k, seed)
    return labels, centers, probabilities


def _auto_clustering(
    table: n.NumericalTable,
    spec: n.ClusteringSpec,
    progress: n.ProgressCallback | None,
) -> dict[str, Any]:
    if not spec.features:
        raise ValueError("clustering requires at least one feature")
    if "cluster" in table.columns or "cluster_probability" in table.columns:
        raise ValueError("clustering input cannot already contain cluster output fields")

    identity_fields = n._unique(table.grain.keys + spec.id_fields)
    required = n._unique(spec.features + identity_fields + spec.partition_fields)
    missing = [field for field in required if field not in table.columns]
    if missing:
        raise ValueError(f"clustering fields are missing from input: {missing}")
    rows, raw = n._numeric_rows(table, spec.features)
    if not rows:
        raise ValueError("no complete numeric rows remain for clustering")

    groups: dict[tuple[Any, ...], list[int]] = {}
    excluded_partition_rows = 0
    if spec.partition_fields:
        for index, row in enumerate(rows):
            key = tuple(row.get(field) for field in spec.partition_fields)
            if any(value is None for value in key):
                excluded_partition_rows += 1
                continue
            groups.setdefault(key, []).append(index)
        if not groups:
            raise ValueError("no clustering partitions remain after removing NULL partition keys")
    else:
        groups[()] = list(range(len(rows)))

    summary_rows: list[dict[str, Any]] = []
    diagnostic_rows: list[dict[str, Any]] = []
    cluster_labels = n.np.full(len(rows), -1, dtype=int)
    cluster_probabilities = n.np.full(len(rows), n.np.nan, dtype=float)
    has_probability = spec.method == "gmm"
    selected_by_partition: list[dict[str, Any]] = []
    group_count = len(groups)

    for group_index, (partition_key, indices) in enumerate(groups.items(), 1):
        subset = raw[n.np.asarray(indices, dtype=int)]
        if spec.standardize:
            matrix, offsets, scales = n._standardize_feature_matrix(subset, spec.features)
        else:
            matrix = subset
            offsets = n.np.zeros(subset.shape[1], dtype=float)
            scales = n.np.ones(subset.shape[1], dtype=float)

        n._notify(
            progress,
            18.0 + 50.0 * (group_index - 1) / max(1, group_count),
            f"Auto-selecting cluster count for partition {group_index}/{group_count} ({len(indices)} complete rows)",
        )
        selected_k, candidates, minimum_size, max_k = _choose_k(matrix, spec.method, spec.seed)
        labels, centers, probabilities = _fit_selected(matrix, spec.method, selected_k, spec.seed)
        if spec.standardize:
            centers = centers * scales + offsets

        partition_info = {field: value for field, value in zip(spec.partition_fields, partition_key)}
        selected_by_partition.append(
            {
                **partition_info,
                "selected_k": selected_k,
                "complete_rows": len(indices),
                "minimum_cluster_size": minimum_size,
                "adaptive_max_k": max_k,
            }
        )
        criterion = "BIC" if spec.method == "gmm" else "Full-covariance GMM BIC (K-means selector)"
        for candidate in candidates:
            diagnostic_rows.append(
                {
                    **partition_info,
                    "candidate_k": candidate.k,
                    "criterion": criterion,
                    "score": candidate.score,
                    "selection_standard_error": candidate.standard_error,
                    "valid": candidate.valid,
                    "selected": candidate.k == selected_k,
                    "cluster_sizes": ",".join(str(size) for size in candidate.sizes),
                    "minimum_cluster_size": minimum_size,
                    "adaptive_max_k": max_k,
                    "rejection_reason": candidate.reason,
                }
            )

        for local_index, global_index in enumerate(indices):
            cluster_labels[global_index] = int(labels[local_index])
            if probabilities is not None:
                cluster_probabilities[global_index] = float(probabilities[local_index])

        for cluster in range(selected_k):
            mask = labels == cluster
            cluster_subset = subset[mask]
            row: dict[str, Any] = dict(partition_info)
            row.update({"cluster": int(cluster), "sample_size": int(mask.sum())})
            for feature_index, field in enumerate(spec.features):
                row[f"center_{field}"] = float(centers[cluster, feature_index])
                row[f"mean_{field}"] = (
                    float(n.np.mean(cluster_subset[:, feature_index])) if cluster_subset.size else None
                )
                row[f"std_{field}"] = (
                    float(n.np.std(cluster_subset[:, feature_index], ddof=0)) if cluster_subset.size else None
                )
            summary_rows.append(row)

    complete_assignment_rows: list[dict[str, Any]] = []
    for index, source in enumerate(rows):
        if cluster_labels[index] < 0:
            continue
        row = dict(source)
        row["cluster"] = int(cluster_labels[index])
        if has_probability:
            row["cluster_probability"] = float(cluster_probabilities[index])
        complete_assignment_rows.append(row)

    summary_columns = spec.partition_fields + ("cluster", "sample_size") + tuple(
        name for field in spec.features for name in (f"center_{field}", f"mean_{field}", f"std_{field}")
    )
    summary = n.NumericalSection(
        "分群摘要 Cluster Summary",
        summary_columns,
        tuple(summary_rows),
        Grain(spec.partition_fields + ("cluster",), "cluster"),
    )
    diagnostic_columns = spec.partition_fields + (
        "candidate_k",
        "criterion",
        "score",
        "selection_standard_error",
        "valid",
        "selected",
        "cluster_sizes",
        "minimum_cluster_size",
        "adaptive_max_k",
        "rejection_reason",
    )
    diagnostics = n.NumericalSection(
        "自動群數診斷 Auto Cluster Diagnostics",
        diagnostic_columns,
        tuple(diagnostic_rows),
        Grain(spec.partition_fields + ("candidate_k",), "auto cluster candidate"),
    )

    identity_fields = n._unique(table.grain.keys + spec.id_fields + spec.partition_fields)
    assignment_columns = identity_fields + tuple(field for field in spec.features if field not in identity_fields) + (
        "cluster",
    ) + (("cluster_probability",) if has_probability else ())
    limit = max(0, int(spec.assignment_limit))
    visible_rows = complete_assignment_rows[:limit] if limit else []
    visible = n.NumericalSection(
        "分群指派 Cluster Assignments",
        assignment_columns,
        tuple({field: row.get(field) for field in assignment_columns} for row in visible_rows),
        table.grain,
    )
    n._notify(progress, 98.0, "Automatic cluster-count selection and clustering complete")
    return {
        "sections": [summary.to_dict(), diagnostics.to_dict(), visible.to_dict()],
        "backend": "numerical",
        "numerical": {
            "method": spec.method,
            "features": list(spec.features),
            "partition_fields": list(spec.partition_fields),
            "standardized": spec.standardize,
            "isotropic_feature_groups": [
                [spec.features[left], spec.features[right]]
                for left, right in n._circular_feature_groups(spec.features)
            ],
            "seed": spec.seed,
            "input_rows": len(table.rows),
            "complete_rows": len(complete_assignment_rows),
            "excluded_rows": max(0, len(table.rows) - len(complete_assignment_rows)),
            "excluded_partition_rows": excluded_partition_rows,
            "assignment_rows_returned": len(visible_rows),
            "identity_fields": list(identity_fields),
            "auto_cluster_count": True,
            "selection_criterion": "BIC" if spec.method == "gmm" else "Full-covariance GMM BIC (K-means selector)",
            "selected_clusters": (
                selected_by_partition[0]["selected_k"] if len(selected_by_partition) == 1 else selected_by_partition
            ),
            "selection_by_partition": selected_by_partition,
        },
    }


def install_auto_cluster() -> None:
    if getattr(n.NumericalExecutor, "_cap04_auto_installed", False):
        return
    original = n.NumericalExecutor.clustering

    def clustering(
        self: n.NumericalExecutor,
        table: n.NumericalTable,
        spec: n.ClusteringSpec,
        progress=None,
    ):
        if int(spec.clusters) != 0:
            return original(self, table, spec, progress)
        return _auto_clustering(table, spec, progress)

    n.NumericalExecutor.clustering = clustering  # type: ignore[method-assign]
    n.NumericalExecutor._cap04_auto_installed = True  # type: ignore[attr-defined]
