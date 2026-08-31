from __future__ import annotations

from pathlib import Path
import textwrap


def replace(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected snippet not found in {path}: {old[:160]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# Supplemental data routes are separate from the canonical Statcast sync lock.
replace(
    "src/treepolo_mlb_data/webapp.py",
    '    def data_action(self, action: str, payload: dict[str, Any]) -> Any:\n        if action == "status":\n            return self.status()\n        with self.sync_lock:\n',
    '    def data_action(self, action: str, payload: dict[str, Any]) -> Any:\n        if action == "status":\n            return self.status()\n        if action.startswith("supplemental-"):\n            from .supplemental_data import handle_supplemental_action\n            return handle_supplemental_action(self.config, action, payload)\n        with self.sync_lock:\n',
)

# Load the supplemental-data manager and CAP-04 controls through the canonical enhancement loader.
replace(
    "src/treepolo_mlb_data/web_static/fast-status.js",
    '    await loadScriptOnce("/navigation-routes.js", "navigationRoutes");\n\n    await waitForFieldCatalog();\n',
    '    await loadScriptOnce("/navigation-routes.js", "navigationRoutes");\n    await loadScriptOnce("/supplemental-data.js", "supplementalData");\n    await loadScriptOnce("/cap04-auto-cluster.js", "cap04AutoCluster");\n\n    await waitForFieldCatalog();\n',
)

# Pitch3D MLB and MiLB use one physical table; therefore source-column names must share one registry.
p = Path("src/treepolo_mlb_data/supplemental_data.py")
text = p.read_text(encoding="utf-8")
old = '''    def _column_map(self, source: str, dataset: str) -> dict[str, str]:
        return {
            str(row[0]): str(row[1])
            for row in self.conn.execute(
                "SELECT original_name,column_name FROM supplemental_schema WHERE source=? AND dataset=?",
                (source, dataset),
            )
        }
'''
new = '''    @staticmethod
    def _schema_dataset(source: str, dataset: str) -> str:
        return "__shared__" if source == "pitch3d" else dataset

    def _column_map(self, source: str, dataset: str) -> dict[str, str]:
        schema_dataset = self._schema_dataset(source, dataset)
        return {
            str(row[0]): str(row[1])
            for row in self.conn.execute(
                "SELECT original_name,column_name FROM supplemental_schema WHERE source=? AND dataset=?",
                (source, schema_dataset),
            )
        }
'''
if old not in text:
    raise SystemExit("supplemental _column_map marker missing")
text = text.replace(old, new, 1)
old = '        mapping = self._column_map(source, dataset)\n        table_columns = {str(row[1]) for row in self.conn.execute(f"PRAGMA table_info({_quote(table)})")}\n'
new = '        schema_dataset = self._schema_dataset(source, dataset)\n        mapping = self._column_map(source, dataset)\n        table_columns = {str(row[1]) for row in self.conn.execute(f"PRAGMA table_info({_quote(table)})")}\n'
if old not in text:
    raise SystemExit("supplemental dynamic-column marker missing")
text = text.replace(old, new, 1)
text = text.replace('(now, source, dataset, original),\n', '(now, source, schema_dataset, original),\n', 1)
text = text.replace('(source, dataset, original, candidate, sql_type, now, now),\n', '(source, schema_dataset, original, candidate, sql_type, now, now),\n', 1)
p.write_text(text, encoding="utf-8")

# CAP-04 K-means: PCA-aligned Gap reference distribution plus an explicit K=1
# clusterability gate.  If K=2 does not improve Gap beyond reference uncertainty,
# K=1 wins.  Once structure is established, apply the classical adjacent 1-SE rule.
p = Path("src/treepolo_mlb_data/analysis/auto_cluster.py")
text = p.read_text(encoding="utf-8")
start = text.index("def _gap_candidates(")
end = text.index("\n\ndef _bic_candidates", start)
replacement = r'''def _gap_candidates(
    matrix: Any,
    max_k: int,
    minimum_size: int,
    seed: int,
    *,
    references: int = 8,
) -> list[_Candidate]:
    """Compute Gap Statistic candidates including K=1.

    The null reference box is built in principal-component coordinates.  This
    avoids the axis-aligned bounding-box inflation that can make K=1 look
    artificially competitive for strongly elongated or rotated data.
    """
    if len(matrix) == 0:
        return []
    matrix = n.np.asarray(matrix, dtype=float)
    centered = matrix - n.np.mean(matrix, axis=0, keepdims=True)
    try:
        _, _, vt = n.np.linalg.svd(centered, full_matrices=False)
        rotated = centered @ vt.T
    except Exception:
        rotated = centered
    lower = n.np.min(rotated, axis=0)
    upper = n.np.max(rotated, axis=0)
    span = upper - lower
    rng = n.np.random.default_rng(seed)
    reference_matrices = [lower + rng.random(rotated.shape) * span for _ in range(max(1, references))]
    epsilon = 1e-12
    candidates: list[_Candidate] = []

    for k in range(1, max_k + 1):
        try:
            labels, _, inertia = _fit_kmeans(rotated, k, seed)
            sizes = _cluster_sizes(labels, k)
            valid, reason = _candidate_validity(k, sizes, minimum_size)
            reference_logs: list[float] = []
            for ref_index, reference in enumerate(reference_matrices):
                _, _, ref_inertia = _fit_kmeans(reference, k, seed + ref_index + 1)
                reference_logs.append(log(max(ref_inertia, epsilon)))
            observed_log = log(max(inertia, epsilon))
            gap = float(n.np.mean(reference_logs)) - observed_log
            if len(reference_logs) > 1:
                std = float(n.np.std(reference_logs, ddof=1))
                standard_error = std * sqrt(1.0 + 1.0 / len(reference_logs))
            else:
                standard_error = 0.0
            candidates.append(_Candidate(k, gap, standard_error, valid, reason, sizes))
        except Exception as exc:
            candidates.append(_Candidate(k, None, None, False, str(exc), []))
    return candidates


def _select_gap_k(candidates: list[_Candidate]) -> int:
    by_k = {candidate.k: candidate for candidate in candidates}
    valid = [candidate for candidate in candidates if candidate.valid and candidate.score is not None]
    if not valid:
        return 1
    one = by_k.get(1)
    two = by_k.get(2)
    if one is not None and one.valid and one.score is not None:
        if two is None or not two.valid or two.score is None:
            return 1
        improvement = float(two.score) - float(one.score)
        uncertainty = max(float(one.standard_error or 0.0), float(two.standard_error or 0.0))
        if improvement <= uncertainty:
            return 1

    max_k = max(candidate.k for candidate in candidates)
    for k in range(2, max_k):
        candidate = by_k.get(k)
        next_candidate = by_k.get(k + 1)
        if (
            candidate is None or next_candidate is None
            or not candidate.valid or not next_candidate.valid
            or candidate.score is None or next_candidate.score is None
        ):
            continue
        next_error = float(next_candidate.standard_error or 0.0)
        if float(candidate.score) >= float(next_candidate.score) - next_error:
            return candidate.k

    structured = [candidate for candidate in valid if candidate.k >= 2]
    if structured:
        return max(structured, key=lambda item: (float(item.score), -item.k)).k
    return valid[0].k
'''
text = text[:start] + textwrap.dedent(replacement) + text[end:]
p.write_text(text, encoding="utf-8")

replace(
    "tests/test_cap04_auto_cluster.py",
    'assert rows[0]["criterion"] == "K-means spherical BIC"',
    'assert rows[0]["criterion"] == "Gap statistic (1-SE)"',
)
replace(
    "src/treepolo_mlb_data/web_static/cap04-auto-cluster.js",
    'const criterion = method?.value === "gmm" ? "BIC" : "K-means spherical BIC";',
    'const criterion = method?.value === "gmm" ? "BIC" : "Gap Statistic + 1-SE 規則";',
)

# Strengthen the shared-schema regression and verify wiring remains intentional.
p = Path("tests/test_supplemental_data.py")
text = p.read_text(encoding="utf-8")
needle = '        assert store.conn.execute("SELECT COUNT(*) FROM pitch3d_pitches WHERE dataset=\'milb\'").fetchone()[0] == 1\n'
if needle not in text:
    raise SystemExit("supplemental namespace test marker missing")
addition = needle + '''        schema_datasets = {
            row[0] for row in store.conn.execute(
                "SELECT DISTINCT dataset FROM supplemental_schema WHERE source='pitch3d'"
            )
        }
        assert schema_datasets == {"__shared__"}
'''
text = text.replace(needle, addition, 1)
p.write_text(text, encoding="utf-8")

Path("tests/test_cap04_supplemental_wiring.py").write_text('''from pathlib import Path


def test_supplemental_api_and_frontend_loader_are_wired():
    webapp = Path("src/treepolo_mlb_data/webapp.py").read_text(encoding="utf-8")
    loader = Path("src/treepolo_mlb_data/web_static/fast-status.js").read_text(encoding="utf-8")
    assert 'action.startswith("supplemental-")' in webapp
    assert 'loadScriptOnce("/supplemental-data.js", "supplementalData")' in loader
    assert 'loadScriptOnce("/cap04-auto-cluster.js", "cap04AutoCluster")' in loader


def test_cap04_ui_describes_current_kmeans_selector():
    ui = Path("src/treepolo_mlb_data/web_static/cap04-auto-cluster.js").read_text(encoding="utf-8")
    assert "Auto K（允許 K=1）" in ui
    assert "Gap Statistic + 1-SE 規則" in ui
''', encoding="utf-8")
