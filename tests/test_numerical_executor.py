import math

import pytest

from treepolo_mlb_data.analysis import Grain
from treepolo_mlb_data.analysis.numerical import (
    BootstrapSpec,
    ClusteringSpec,
    NumericalExecutor,
    NumericalTable,
    RegressionSpec,
)


def table(rows, columns, grain=("id",)):
    return NumericalTable(tuple(columns), tuple(rows), Grain(tuple(grain), "synthetic"))


def section(result, title):
    return next(item for item in result["sections"] if item["title"] == title)


def test_linear_regression_recovers_known_coefficients():
    rows = tuple({"id": i, "x": float(i), "y": 2.0 + 3.0 * i} for i in range(1, 21))
    result = NumericalExecutor().regression(
        table(rows, ("id", "x", "y")),
        RegressionSpec(dependent="y", independent=("x",), model="linear"),
    )
    coefficients = {row["term"]: row for row in section(result, "迴歸係數 Coefficients")["rows"]}
    summary = section(result, "模型摘要 Model Summary")["rows"][0]
    assert coefficients["intercept"]["estimate"] == pytest.approx(2.0, abs=1e-10)
    assert coefficients["x"]["estimate"] == pytest.approx(3.0, abs=1e-10)
    assert summary["r_squared"] == pytest.approx(1.0, abs=1e-12)
    assert summary["sample_size"] == 20


def test_logistic_regression_finds_positive_relationship():
    rows = []
    for i, x in enumerate(range(-20, 21)):
        rows.append({"id": i, "x": float(x), "y": 1.0 if x >= 2 else 0.0})
    result = NumericalExecutor().regression(
        table(rows, ("id", "x", "y")),
        RegressionSpec(dependent="y", independent=("x",), model="logistic"),
    )
    coefficients = {row["term"]: row for row in section(result, "迴歸係數 Coefficients")["rows"]}
    summary = section(result, "模型摘要 Model Summary")["rows"][0]
    assert coefficients["x"]["estimate"] > 0
    assert summary["accuracy"] > 0.9
    assert math.isfinite(summary["log_loss"])


def test_kmeans_recovers_two_obvious_clusters_and_preserves_grain_key():
    rows = (
        {"id": "a", "x": 0.0, "y": 0.0},
        {"id": "b", "x": 0.2, "y": -0.1},
        {"id": "c", "x": 10.0, "y": 10.0},
        {"id": "d", "x": 10.2, "y": 9.9},
    )
    result = NumericalExecutor().clustering(
        table(rows, ("id", "x", "y")),
        ClusteringSpec(features=("x", "y"), clusters=2, method="kmeans", seed=7, id_fields=()),
    )
    summary = section(result, "分群摘要 Cluster Summary")
    centers = sorted((row["mean_x"], row["sample_size"]) for row in summary["rows"])
    assert centers[0][0] == pytest.approx(0.1, abs=0.2)
    assert centers[1][0] == pytest.approx(10.1, abs=0.2)
    assert sorted(size for _, size in centers) == [2, 2]
    assignments = section(result, "分群指派 Cluster Assignments")
    assert "id" in assignments["columns"]
    assert {row["id"] for row in assignments["rows"]} == {"a", "b", "c", "d"}


def test_gmm_returns_probability_and_is_deterministic():
    rows = tuple(
        {"id": i, "x": float(x), "y": float(y)}
        for i, (x, y) in enumerate(((0, 0), (.1, -.1), (5, 5), (5.2, 4.9), (10, 0), (10.1, .2)))
    )
    spec = ClusteringSpec(features=("x", "y"), clusters=3, method="gmm", seed=11, id_fields=("id",))
    first = NumericalExecutor().clustering(table(rows, ("id", "x", "y")), spec)
    second = NumericalExecutor().clustering(table(rows, ("id", "x", "y")), spec)
    first_assign = section(first, "分群指派 Cluster Assignments")["rows"]
    second_assign = section(second, "分群指派 Cluster Assignments")["rows"]
    assert first_assign == second_assign
    assert all(0.0 <= row["cluster_probability"] <= 1.0 for row in first_assign)


def test_bootstrap_mean_is_reproducible_and_uses_explicit_units():
    rows = (
        {"id": 1, "game": "g1", "value": 1.0},
        {"id": 2, "game": "g2", "value": 2.0},
        {"id": 3, "game": "g3", "value": 3.0},
        {"id": 4, "game": "g4", "value": 4.0},
    )
    spec = BootstrapSpec(
        value_field="value",
        resample_unit_fields=("game",),
        statistic="mean",
        iterations=1000,
        confidence=0.95,
        seed=123,
    )
    first = NumericalExecutor().bootstrap(table(rows, ("id", "game", "value")), spec)
    second = NumericalExecutor().bootstrap(table(rows, ("id", "game", "value")), spec)
    row = section(first, "Bootstrap 結果 Bootstrap Result")["rows"][0]
    row2 = section(second, "Bootstrap 結果 Bootstrap Result")["rows"][0]
    assert row == row2
    assert row["estimate"] == pytest.approx(2.5)
    assert row["resample_units"] == 4
    assert row["ci_lower"] < 2.5 < row["ci_upper"]


def test_bootstrap_group_difference_preserves_group_comparison():
    rows = (
        {"id": 1, "game": "a1", "group": "A", "value": 10.0},
        {"id": 2, "game": "a2", "group": "A", "value": 12.0},
        {"id": 3, "game": "b1", "group": "B", "value": 5.0},
        {"id": 4, "game": "b2", "group": "B", "value": 7.0},
    )
    result = NumericalExecutor().bootstrap(
        table(rows, ("id", "game", "group", "value")),
        BootstrapSpec(
            value_field="value", resample_unit_fields=("game",), statistic="mean",
            group_field="group", group_a="A", group_b="B", iterations=1000, seed=9,
        ),
    )
    row = section(result, "Bootstrap 結果 Bootstrap Result")["rows"][0]
    assert row["estimate"] == pytest.approx(5.0)
    assert row["group_a"] == "A" and row["group_b"] == "B"
    assert row["ci_lower"] <= 5.0 <= row["ci_upper"]


def test_bootstrap_rejects_missing_resampling_unit():
    rows = ({"id": 1, "value": 1.0}, {"id": 2, "value": 2.0})
    with pytest.raises(ValueError, match="explicit resample_unit_fields"):
        NumericalExecutor().bootstrap(
            table(rows, ("id", "value")),
            BootstrapSpec(value_field="value", resample_unit_fields=(), iterations=100),
        )
