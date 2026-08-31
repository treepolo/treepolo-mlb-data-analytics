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

# Pitch3D MLB and MiLB use one physical table; source-column names therefore share one registry.
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

# CAP-04 K-means needs a selector that admits K=1.  A full-covariance Gaussian-mixture
# BIC supplies that model-complexity comparison without forcing anisotropic clouds to be
# approximated by many spherical components.  The selected K is then fitted by K-means;
# manual K behavior remains untouched.
p = Path("src/treepolo_mlb_data/analysis/auto_cluster.py")
text = p.read_text(encoding="utf-8")
start = text.index("def _gap_candidates(")
end = text.index("\n\ndef _bic_candidates", start)
replacement = r'''def _kmeans_bic_candidates(
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
'''
text = text[:start] + textwrap.dedent(replacement) + text[end:]
old = '''    if method == "kmeans":
        candidates = _gap_candidates(matrix, max_k, minimum_size, seed)
        return _select_gap_k(candidates), candidates, minimum_size, max_k
'''
new = '''    if method == "kmeans":
        candidates = _kmeans_bic_candidates(matrix, max_k, minimum_size, seed)
        valid = [candidate for candidate in candidates if candidate.valid and candidate.score is not None]
        selected = min(valid, key=lambda item: (float(item.score), item.k)).k if valid else 1
        return selected, candidates, minimum_size, max_k
'''
if old not in text:
    raise SystemExit("K-means choose-k marker missing")
text = text.replace(old, new, 1)
text = text.replace(
    '        criterion = "BIC" if spec.method == "gmm" else "Gap statistic (1-SE)"',
    '        criterion = "BIC" if spec.method == "gmm" else "Full-covariance GMM BIC (K-means selector)"',
    1,
)
text = text.replace(
    '            "selection_criterion": "BIC" if spec.method == "gmm" else "Gap statistic (1-SE)",',
    '            "selection_criterion": "BIC" if spec.method == "gmm" else "Full-covariance GMM BIC (K-means selector)",',
    1,
)
p.write_text(text, encoding="utf-8")

replace(
    "tests/test_cap04_auto_cluster.py",
    'assert rows[0]["criterion"] == "K-means spherical BIC"',
    'assert rows[0]["criterion"] == "Full-covariance GMM BIC (K-means selector)"',
)
replace(
    "src/treepolo_mlb_data/web_static/cap04-auto-cluster.js",
    'const criterion = method?.value === "gmm" ? "BIC" : "K-means spherical BIC";',
    'const criterion = method?.value === "gmm" ? "BIC" : "Full-covariance GMM BIC（K-means 選模）";',
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
    assert "Full-covariance GMM BIC（K-means 選模）" in ui
''', encoding="utf-8")
