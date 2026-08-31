from __future__ import annotations

from treepolo_mlb_data.analysis.auto_cluster import install_auto_cluster
from treepolo_mlb_data.analysis.model import Grain
from treepolo_mlb_data.analysis.numerical import ClusteringSpec, NumericalExecutor, NumericalTable, np


install_auto_cluster()


def table_from_matrix(matrix, partition=None):
    rows = []
    for index, values in enumerate(matrix):
        row = {"pitch_uid": f"p{index}", "x": float(values[0]), "y": float(values[1])}
        if partition is not None:
            row["pitcher"] = partition[index]
        rows.append(row)
    columns = ("pitch_uid", "x", "y") + (("pitcher",) if partition is not None else ())
    return NumericalTable(columns, tuple(rows), Grain(("pitch_uid",), "pitch"))


def diagnostics(result):
    section = next(section for section in result["sections"] if section["title"].startswith("自動群數診斷"))
    return section["rows"]


def test_auto_kmeans_can_select_one_cluster():
    rng = np.random.default_rng(20260831)
    matrix = rng.normal(0.0, 1.0, size=(500, 2))
    result = NumericalExecutor().clustering(
        table_from_matrix(matrix),
        ClusteringSpec(features=("x", "y"), method="kmeans", clusters=0, seed=42),
    )
    assert result["numerical"]["auto_cluster_count"] is True
    assert result["numerical"]["selected_clusters"] == 1
    rows = diagnostics(result)
    assert rows[0]["candidate_k"] == 1
    assert rows[0]["valid"] is True
    assert rows[0]["selected"] is True
    assert rows[0]["criterion"] == "K-means spherical BIC"


def test_auto_kmeans_finds_two_clear_groups():
    rng = np.random.default_rng(7)
    left = rng.normal((-4.5, 0.0), (0.45, 0.45), size=(220, 2))
    right = rng.normal((4.5, 0.0), (0.45, 0.45), size=(220, 2))
    matrix = np.vstack([left, right])
    result = NumericalExecutor().clustering(
        table_from_matrix(matrix),
        ClusteringSpec(features=("x", "y"), method="kmeans", clusters=0, seed=42),
    )
    assert result["numerical"]["selected_clusters"] == 2
    summary = result["sections"][0]
    assert summary["row_count"] == 2
    assert sorted(row["sample_size"] for row in summary["rows"]) == [220, 220]


def test_auto_gmm_uses_bic_and_allows_k1():
    rng = np.random.default_rng(91)
    matrix = rng.normal(0.0, 1.0, size=(400, 2))
    result = NumericalExecutor().clustering(
        table_from_matrix(matrix),
        ClusteringSpec(features=("x", "y"), method="gmm", clusters=0, seed=42),
    )
    assert result["numerical"]["selection_criterion"] == "BIC"
    assert result["numerical"]["selected_clusters"] == 1
    assert diagnostics(result)[0]["candidate_k"] == 1
    assignments = result["sections"][-1]
    assert "cluster_probability" in assignments["columns"]


def test_auto_rejects_tiny_candidate_clusters():
    rng = np.random.default_rng(123)
    main = rng.normal((0.0, 0.0), (0.5, 0.5), size=(198, 2))
    outliers = np.asarray([[10.0, 10.0], [10.4, 10.2]])
    matrix = np.vstack([main, outliers])
    result = NumericalExecutor().clustering(
        table_from_matrix(matrix),
        ClusteringSpec(features=("x", "y"), method="kmeans", clusters=0, seed=42),
    )
    rejected = [row for row in diagnostics(result) if not row["valid"]]
    assert rejected
    assert any("minimum cluster size" in (row["rejection_reason"] or "") for row in rejected)
    selected = next(row for row in diagnostics(result) if row["selected"])
    assert selected["valid"] is True


def test_auto_can_select_different_k_by_partition():
    rng = np.random.default_rng(55)
    a = rng.normal(0.0, 1.0, size=(300, 2))
    b1 = rng.normal((-5.0, 0.0), (0.4, 0.4), size=(150, 2))
    b2 = rng.normal((5.0, 0.0), (0.4, 0.4), size=(150, 2))
    matrix = np.vstack([a, b1, b2])
    partitions = ["A"] * len(a) + ["B"] * (len(b1) + len(b2))
    result = NumericalExecutor().clustering(
        table_from_matrix(matrix, partitions),
        ClusteringSpec(
            features=("x", "y"), method="kmeans", clusters=0, seed=42,
            partition_fields=("pitcher",),
        ),
    )
    selected = {row["pitcher"]: row["selected_k"] for row in result["numerical"]["selection_by_partition"]}
    assert selected == {"A": 1, "B": 2}


def test_manual_cluster_count_behavior_is_unchanged():
    matrix = np.asarray([[-2.0, 0.0], [-1.9, 0.1], [2.0, 0.0], [1.9, -0.1]])
    result = NumericalExecutor().clustering(
        table_from_matrix(matrix),
        ClusteringSpec(features=("x", "y"), method="kmeans", clusters=2, seed=42),
    )
    assert result["sections"][0]["row_count"] == 2
    assert result["numerical"].get("auto_cluster_count") is None
