from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Any, Callable, Literal as TypingLiteral

from ._lazy_scientific import (
    GaussianMixture,
    KMeans,
    LogisticRegression,
    StandardScaler,
    accuracy_score,
    log_loss,
    np,
    stats,
)
from .model import Grain

ProgressCallback = Callable[[str, float | None, str | None], None]


@dataclass(frozen=True, slots=True)
class NumericalTable:
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    grain: Grain

    def __post_init__(self) -> None:
        if not self.columns or len(set(self.columns)) != len(self.columns):
            raise ValueError("numerical input requires unique columns")
        missing = [key for key in self.grain.keys if key not in self.columns]
        if missing:
            raise ValueError(f"numerical input must retain grain keys: {missing}")


@dataclass(frozen=True, slots=True)
class NumericalSection:
    title: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    grain: Grain
    backend: str = "numerical"

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "columns": list(self.columns),
            "rows": [dict(row) for row in self.rows],
            "grain": {"keys": list(self.grain.keys), "label": self.grain.label},
            "row_count": len(self.rows),
            "backend": self.backend,
        }


@dataclass(frozen=True, slots=True)
class ClusteringSpec:
    features: tuple[str, ...]
    method: TypingLiteral["kmeans", "gmm"] = "kmeans"
    clusters: int = 3
    standardize: bool = True
    seed: int = 42
    id_fields: tuple[str, ...] = ()
    partition_fields: tuple[str, ...] = ()
    assignment_limit: int = 5000


@dataclass(frozen=True, slots=True)
class ClusteringOutput:
    summary: NumericalSection
    assignments: NumericalTable
    probability_field: str | None


@dataclass(frozen=True, slots=True)
class RegressionSpec:
    dependent: str
    independent: tuple[str, ...]
    model: TypingLiteral["linear", "logistic"] = "linear"
    standardize_predictors: bool = False
    confidence: float = 0.95


@dataclass(frozen=True, slots=True)
class BootstrapSpec:
    value_field: str
    resample_unit_fields: tuple[str, ...]
    statistic: TypingLiteral["mean", "median", "proportion"] = "mean"
    group_field: str | None = None
    group_a: Any = None
    group_b: Any = None
    success_value: Any = 1
    iterations: int = 2000
    confidence: float = 0.95
    seed: int = 42


def _notify(callback: ProgressCallback | None, percentage: float | None, detail: str) -> None:
    if callback is not None:
        callback("numerical_compute", percentage, detail)


def _unique(items: tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    for item in items:
        if item not in out:
            out.append(item)
    return tuple(out)


def _finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return float(value) if isinstance(value, bool) else None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _numeric_rows(table: NumericalTable, fields: tuple[str, ...]) -> tuple[list[dict[str, Any]], np.ndarray]:
    rows: list[dict[str, Any]] = []
    matrix: list[list[float]] = []
    for row in table.rows:
        values: list[float] = []
        valid = True
        for field in fields:
            number = _finite_number(row.get(field))
            if number is None:
                valid = False
                break
            values.append(number)
        if valid:
            rows.append(row)
            matrix.append(values)
    if not matrix:
        raise ValueError("no complete numeric rows remain after removing NULL/non-numeric values")
    return rows, np.asarray(matrix, dtype=float)


def _statistic(values: np.ndarray, kind: str, success_value: Any) -> float:
    if values.size == 0:
        return float("nan")
    if kind == "mean":
        return float(np.mean(values.astype(float)))
    if kind == "median":
        return float(np.median(values.astype(float)))
    if kind == "proportion":
        return float(np.mean(values == success_value))
    raise ValueError(f"unsupported bootstrap statistic: {kind}")


def _bootstrap_unit_parts(
    rows: list[dict[str, Any]], value_field: str, statistic: str, success_value: Any
) -> tuple[float, int]:
    if statistic == "proportion":
        return float(sum(row.get(value_field) == success_value for row in rows)), len(rows)
    values = [_finite_number(row.get(value_field)) for row in rows]
    numeric = [value for value in values if value is not None]
    return float(sum(numeric)), len(numeric)


class NumericalExecutor:
    """Strict numerical execution boundary consuming a typed relational result."""

    def cluster_table(
        self,
        table: NumericalTable,
        spec: ClusteringSpec,
        progress: ProgressCallback | None = None,
    ) -> ClusteringOutput:
        if not spec.features:
            raise ValueError("clustering requires at least one feature")
        if not 2 <= spec.clusters <= 50:
            raise ValueError("cluster count must be between 2 and 50")
        if "cluster" in table.columns or "cluster_probability" in table.columns:
            raise ValueError("clustering input cannot already contain cluster output fields")

        identity_fields = _unique(table.grain.keys + spec.id_fields)
        required = _unique(spec.features + identity_fields + spec.partition_fields)
        missing = [field for field in required if field not in table.columns]
        if missing:
            raise ValueError(f"clustering fields are missing from input: {missing}")
        rows, raw = _numeric_rows(table, spec.features)

        groups: dict[tuple[Any, ...], list[int]] = {}
        if spec.partition_fields:
            for index, row in enumerate(rows):
                key = tuple(row.get(field) for field in spec.partition_fields)
                if any(value is None for value in key):
                    continue
                groups.setdefault(key, []).append(index)
            if not groups:
                raise ValueError("no clustering partitions remain after removing NULL partition keys")
        else:
            groups[()] = list(range(len(rows)))

        summary_rows: list[dict[str, Any]] = []
        cluster_labels = np.full(len(rows), -1, dtype=int)
        cluster_probabilities = np.full(len(rows), np.nan, dtype=float)
        has_probability = spec.method == "gmm"
        group_count = len(groups)

        for group_index, (partition_key, indices) in enumerate(groups.items(), 1):
            if len(indices) < spec.clusters:
                label = ", ".join(
                    f"{field}={value!r}" for field, value in zip(spec.partition_fields, partition_key)
                ) or "all rows"
                raise ValueError(
                    f"cluster count {spec.clusters} exceeds complete rows {len(indices)} in partition {label}"
                )
            subset = raw[np.asarray(indices, dtype=int)]
            scaler = StandardScaler() if spec.standardize else None
            matrix = scaler.fit_transform(subset) if scaler is not None else subset
            _notify(
                progress,
                20.0 + 55.0 * (group_index - 1) / max(1, group_count),
                f"Clustering partition {group_index}/{group_count} with {len(indices)} complete rows",
            )
            if spec.method == "kmeans":
                model = KMeans(n_clusters=spec.clusters, random_state=spec.seed, n_init=10)
                local_labels = model.fit_predict(matrix)
                centers = model.cluster_centers_
                local_probabilities = None
            elif spec.method == "gmm":
                model = GaussianMixture(n_components=spec.clusters, random_state=spec.seed)
                local_labels = model.fit_predict(matrix)
                centers = model.means_
                local_probabilities = np.max(model.predict_proba(matrix), axis=1)
            else:
                raise ValueError(f"unsupported clustering method: {spec.method}")
            if scaler is not None:
                centers = scaler.inverse_transform(centers)

            for local_index, global_index in enumerate(indices):
                cluster_labels[global_index] = int(local_labels[local_index])
                if local_probabilities is not None:
                    cluster_probabilities[global_index] = float(local_probabilities[local_index])

            for cluster in range(spec.clusters):
                mask = local_labels == cluster
                cluster_subset = subset[mask]
                row: dict[str, Any] = {
                    field: value for field, value in zip(spec.partition_fields, partition_key)
                }
                row.update({"cluster": int(cluster), "sample_size": int(mask.sum())})
                for feature_index, field in enumerate(spec.features):
                    row[f"center_{field}"] = float(centers[cluster, feature_index])
                    row[f"mean_{field}"] = float(np.mean(cluster_subset[:, feature_index])) if cluster_subset.size else None
                    row[f"std_{field}"] = float(np.std(cluster_subset[:, feature_index], ddof=0)) if cluster_subset.size else None
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
        summary_grain = Grain(spec.partition_fields + ("cluster",), "cluster")
        assignment_columns = table.columns + ("cluster",) + (("cluster_probability",) if has_probability else ())
        assignment_table = NumericalTable(assignment_columns, tuple(complete_assignment_rows), table.grain)
        _notify(progress, 86.0, "Cluster assignments are ready for downstream analysis")
        return ClusteringOutput(
            NumericalSection("分群摘要 Cluster Summary", summary_columns, tuple(summary_rows), summary_grain),
            assignment_table,
            "cluster_probability" if has_probability else None,
        )

    def clustering(
        self,
        table: NumericalTable,
        spec: ClusteringSpec,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        output = self.cluster_table(table, spec, progress)
        identity_fields = _unique(table.grain.keys + spec.id_fields + spec.partition_fields)
        assignment_columns = identity_fields + tuple(
            field for field in spec.features if field not in identity_fields
        ) + ("cluster",) + ((output.probability_field,) if output.probability_field else ())
        limit = max(0, int(spec.assignment_limit))
        visible_rows = output.assignments.rows[:limit] if limit else ()
        visible = NumericalSection(
            "分群指派 Cluster Assignments",
            assignment_columns,
            tuple({field: row.get(field) for field in assignment_columns} for row in visible_rows),
            table.grain,
        )
        _notify(progress, 98.0, "Clustering complete")
        return {
            "sections": [output.summary.to_dict(), visible.to_dict()],
            "backend": "numerical",
            "numerical": {
                "method": spec.method,
                "features": list(spec.features),
                "partition_fields": list(spec.partition_fields),
                "standardized": spec.standardize,
                "seed": spec.seed,
                "input_rows": len(table.rows),
                "complete_rows": len(output.assignments.rows),
                "assignment_rows_returned": len(visible.rows),
                "identity_fields": list(identity_fields),
            },
        }

    def regression(
        self,
        table: NumericalTable,
        spec: RegressionSpec,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        if not spec.independent:
            raise ValueError("regression requires at least one independent variable")
        if not 0.5 < spec.confidence < 1:
            raise ValueError("regression confidence must be between 0.5 and 1")
        fields = spec.independent + (spec.dependent,)
        missing = [field for field in fields if field not in table.columns]
        if missing:
            raise ValueError(f"regression fields are missing from input: {missing}")
        rows, matrix = _numeric_rows(table, fields)
        x = matrix[:, : len(spec.independent)]
        y = matrix[:, -1]
        if len(rows) <= len(spec.independent) + 1:
            raise ValueError("regression requires more complete rows than fitted parameters")
        scaler = StandardScaler() if spec.standardize_predictors else None
        x_fit = scaler.fit_transform(x) if scaler is not None else x
        _notify(progress, 30.0, f"Fitting {spec.model} regression on {len(rows)} complete rows")

        coefficient_rows: list[dict[str, Any]] = []
        summary_rows: list[dict[str, Any]] = []
        if spec.model == "linear":
            design = np.column_stack([np.ones(len(x_fit)), x_fit])
            beta, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
            fitted = design @ beta
            residuals = y - fitted
            n = len(y)
            p = design.shape[1]
            df = n - p
            sse = float(np.sum(residuals ** 2))
            sst = float(np.sum((y - np.mean(y)) ** 2))
            r2 = 1.0 - sse / sst if sst > 0 else 0.0
            rmse = sqrt(sse / n)
            sigma2 = sse / df if df > 0 else float("nan")
            covariance = sigma2 * np.linalg.pinv(design.T @ design)
            standard_errors = np.sqrt(np.maximum(np.diag(covariance), 0.0))
            alpha = 1.0 - spec.confidence
            critical = float(stats.t.ppf(1 - alpha / 2, df)) if df > 0 else float("nan")
            names = ("intercept",) + spec.independent
            for index, name in enumerate(names):
                estimate = float(beta[index])
                se = float(standard_errors[index])
                t_stat = estimate / se if se > 0 else None
                p_value = float(2 * stats.t.sf(abs(t_stat), df)) if t_stat is not None and df > 0 else None
                coefficient_rows.append({
                    "term": name,
                    "estimate": estimate,
                    "std_error": se,
                    "statistic": t_stat,
                    "p_value": p_value,
                    "ci_lower": estimate - critical * se if np.isfinite(critical) else None,
                    "ci_upper": estimate + critical * se if np.isfinite(critical) else None,
                })
            summary_rows.append({
                "model": "linear",
                "sample_size": n,
                "predictors": len(spec.independent),
                "r_squared": r2,
                "rmse": rmse,
                "degrees_of_freedom": df,
                "standardized_predictors": int(spec.standardize_predictors),
            })
            summary_columns = (
                "model", "sample_size", "predictors", "r_squared", "rmse",
                "degrees_of_freedom", "standardized_predictors",
            )
        elif spec.model == "logistic":
            unique = np.unique(y)
            if unique.size != 2:
                raise ValueError("logistic regression requires a binary dependent variable")
            model = LogisticRegression(max_iter=2000, random_state=42)
            model.fit(x_fit, y)
            predicted = model.predict(x_fit)
            probabilities = model.predict_proba(x_fit)
            classes = model.classes_
            for name, estimate in zip(spec.independent, model.coef_[0]):
                coefficient_rows.append({
                    "term": name,
                    "estimate": float(estimate),
                    "std_error": None,
                    "statistic": None,
                    "p_value": None,
                    "ci_lower": None,
                    "ci_upper": None,
                })
            coefficient_rows.insert(0, {
                "term": "intercept",
                "estimate": float(model.intercept_[0]),
                "std_error": None,
                "statistic": None,
                "p_value": None,
                "ci_lower": None,
                "ci_upper": None,
            })
            summary_rows.append({
                "model": "logistic",
                "sample_size": len(y),
                "predictors": len(spec.independent),
                "accuracy": float(accuracy_score(y, predicted)),
                "log_loss": float(log_loss(y, probabilities, labels=classes)),
                "positive_class": float(classes[1]),
                "standardized_predictors": int(spec.standardize_predictors),
            })
            summary_columns = (
                "model", "sample_size", "predictors", "accuracy", "log_loss",
                "positive_class", "standardized_predictors",
            )
        else:
            raise ValueError(f"unsupported regression model: {spec.model}")

        _notify(progress, 96.0, "Regression complete")
        coefficient_columns = (
            "term", "estimate", "std_error", "statistic", "p_value", "ci_lower", "ci_upper",
        )
        sections = [
            NumericalSection("模型摘要 Model Summary", summary_columns, tuple(summary_rows), Grain((), "model")),
            NumericalSection("迴歸係數 Coefficients", coefficient_columns, tuple(coefficient_rows), Grain(("term",), "coefficient")),
        ]
        return {
            "sections": [section.to_dict() for section in sections],
            "backend": "numerical",
            "numerical": {
                "method": spec.model,
                "dependent": spec.dependent,
                "independent": list(spec.independent),
                "complete_rows": len(rows),
                "confidence": spec.confidence,
            },
        }

    def bootstrap(
        self,
        table: NumericalTable,
        spec: BootstrapSpec,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        if not spec.resample_unit_fields:
            raise ValueError("bootstrap requires explicit resample_unit_fields; do not assume pitch rows are independent")
        if not 100 <= spec.iterations <= 100_000:
            raise ValueError("bootstrap iterations must be between 100 and 100000")
        if not 0.5 < spec.confidence < 1:
            raise ValueError("bootstrap confidence must be between 0.5 and 1")
        required = (spec.value_field,) + spec.resample_unit_fields + ((spec.group_field,) if spec.group_field else ())
        missing = [field for field in required if field not in table.columns]
        if missing:
            raise ValueError(f"bootstrap fields are missing from input: {missing}")
        if spec.group_field is not None and spec.group_a == spec.group_b:
            raise ValueError("bootstrap group_a and group_b must be different")

        usable: list[dict[str, Any]] = []
        for row in table.rows:
            value = row.get(spec.value_field)
            if spec.statistic in {"mean", "median"} and _finite_number(value) is None:
                continue
            if any(row.get(field) is None for field in spec.resample_unit_fields):
                continue
            usable.append(row)
        if not usable:
            raise ValueError("bootstrap has no usable rows")

        units: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for row in usable:
            key = tuple(row.get(field) for field in spec.resample_unit_fields)
            units.setdefault(key, []).append(row)
        unit_values = list(units.values())
        if len(unit_values) < 2:
            raise ValueError("bootstrap requires at least two resampling units")

        def calculate(rows: list[dict[str, Any]]) -> float:
            if spec.group_field is None:
                values = np.asarray([row.get(spec.value_field) for row in rows], dtype=object)
                if spec.statistic in {"mean", "median"}:
                    values = values.astype(float)
                return _statistic(values, spec.statistic, spec.success_value)
            a = [row.get(spec.value_field) for row in rows if row.get(spec.group_field) == spec.group_a]
            b = [row.get(spec.value_field) for row in rows if row.get(spec.group_field) == spec.group_b]
            if not a or not b:
                return float("nan")
            av = np.asarray(a, dtype=object)
            bv = np.asarray(b, dtype=object)
            if spec.statistic in {"mean", "median"}:
                av = av.astype(float)
                bv = bv.astype(float)
            return _statistic(av, spec.statistic, spec.success_value) - _statistic(bv, spec.statistic, spec.success_value)

        estimate = calculate(usable)
        rng = np.random.default_rng(spec.seed)
        distribution = np.empty(spec.iterations, dtype=float)
        notify_every = max(1, spec.iterations // 20)

        exclusive_group_units = False
        group_a_units: list[list[dict[str, Any]]] = []
        group_b_units: list[list[dict[str, Any]]] = []
        if spec.group_field is not None:
            unit_group_sets = [
                {row.get(spec.group_field) for row in unit if row.get(spec.group_field) in {spec.group_a, spec.group_b}}
                for unit in unit_values
            ]
            exclusive_group_units = all(len(groups) <= 1 for groups in unit_group_sets)
            if exclusive_group_units:
                group_a_units = [unit for unit, groups in zip(unit_values, unit_group_sets) if groups == {spec.group_a}]
                group_b_units = [unit for unit, groups in zip(unit_values, unit_group_sets) if groups == {spec.group_b}]
                if not group_a_units or not group_b_units:
                    raise ValueError("bootstrap comparison requires resampling units in both groups")

        fast_scalar = spec.statistic in {"mean", "proportion"}
        if fast_scalar and spec.group_field is None:
            parts = np.asarray([
                _bootstrap_unit_parts(unit, spec.value_field, spec.statistic, spec.success_value)
                for unit in unit_values
            ], dtype=float)
            _notify(progress, 20.0, f"Resampling {len(unit_values)} explicit units")
            for iteration in range(spec.iterations):
                indices = rng.integers(0, len(parts), size=len(parts))
                picked = parts[indices]
                denominator = float(np.sum(picked[:, 1]))
                distribution[iteration] = float(np.sum(picked[:, 0]) / denominator) if denominator else float("nan")
                if iteration % notify_every == 0:
                    _notify(progress, 20.0 + 70.0 * iteration / spec.iterations, f"Bootstrap iteration {iteration + 1}/{spec.iterations}")
        elif fast_scalar and spec.group_field is not None and exclusive_group_units:
            a_parts = np.asarray([
                _bootstrap_unit_parts(unit, spec.value_field, spec.statistic, spec.success_value)
                for unit in group_a_units
            ], dtype=float)
            b_parts = np.asarray([
                _bootstrap_unit_parts(unit, spec.value_field, spec.statistic, spec.success_value)
                for unit in group_b_units
            ], dtype=float)
            _notify(progress, 20.0, f"Stratified resampling of {len(a_parts)} A units and {len(b_parts)} B units")
            for iteration in range(spec.iterations):
                a_pick = a_parts[rng.integers(0, len(a_parts), size=len(a_parts))]
                b_pick = b_parts[rng.integers(0, len(b_parts), size=len(b_parts))]
                a_den = float(np.sum(a_pick[:, 1]))
                b_den = float(np.sum(b_pick[:, 1]))
                a_stat = float(np.sum(a_pick[:, 0]) / a_den) if a_den else float("nan")
                b_stat = float(np.sum(b_pick[:, 0]) / b_den) if b_den else float("nan")
                distribution[iteration] = a_stat - b_stat
                if iteration % notify_every == 0:
                    _notify(progress, 20.0 + 70.0 * iteration / spec.iterations, f"Bootstrap iteration {iteration + 1}/{spec.iterations}")
        else:
            estimated_row_work = len(usable) * spec.iterations
            if estimated_row_work > 50_000_000:
                raise ValueError(
                    "Bootstrap median/mixed-group workload is too large for row-wise resampling. "
                    "Reduce input rows or iterations, or aggregate to an appropriate resampling unit first."
                )
            _notify(progress, 20.0, f"Resampling {len(unit_values)} explicit units")
            for iteration in range(spec.iterations):
                sampled: list[dict[str, Any]] = []
                if spec.group_field is not None and exclusive_group_units:
                    for index in rng.integers(0, len(group_a_units), size=len(group_a_units)):
                        sampled.extend(group_a_units[int(index)])
                    for index in rng.integers(0, len(group_b_units), size=len(group_b_units)):
                        sampled.extend(group_b_units[int(index)])
                else:
                    for index in rng.integers(0, len(unit_values), size=len(unit_values)):
                        sampled.extend(unit_values[int(index)])
                distribution[iteration] = calculate(sampled)
                if iteration % notify_every == 0:
                    _notify(progress, 20.0 + 70.0 * iteration / spec.iterations, f"Bootstrap iteration {iteration + 1}/{spec.iterations}")

        finite = distribution[np.isfinite(distribution)]
        if finite.size < max(20, int(spec.iterations * 0.5)):
            raise ValueError("too many bootstrap samples lacked valid observations or both required groups")
        alpha = 1.0 - spec.confidence
        lower, upper = np.quantile(finite, [alpha / 2, 1 - alpha / 2])
        q05, q25, q50, q75, q95 = np.quantile(finite, [.05, .25, .5, .75, .95])
        result_row = {
            "statistic": spec.statistic,
            "estimate": float(estimate),
            "ci_lower": float(lower),
            "ci_upper": float(upper),
            "confidence": float(spec.confidence),
            "iterations": int(spec.iterations),
            "resample_units": len(unit_values),
            "usable_rows": len(usable),
            "group_a": spec.group_a if spec.group_field else None,
            "group_b": spec.group_b if spec.group_field else None,
        }
        distribution_row = {
            "q05": float(q05),
            "q25": float(q25),
            "median": float(q50),
            "q75": float(q75),
            "q95": float(q95),
            "finite_iterations": int(finite.size),
        }
        _notify(progress, 98.0, "Bootstrap confidence interval complete")
        sections = [
            NumericalSection(
                "Bootstrap 結果 Bootstrap Result",
                tuple(result_row),
                (result_row,),
                Grain((), "bootstrap"),
            ),
            NumericalSection(
                "重抽樣分布摘要 Resampling Distribution Summary",
                tuple(distribution_row),
                (distribution_row,),
                Grain((), "bootstrap_distribution"),
            ),
        ]
        return {
            "sections": [section.to_dict() for section in sections],
            "backend": "numerical",
            "numerical": {
                "method": "bootstrap",
                "value_field": spec.value_field,
                "resample_unit_fields": list(spec.resample_unit_fields),
                "seed": spec.seed,
                "stratified_group_units": bool(spec.group_field is not None and exclusive_group_units),
            },
        }