from pathlib import Path


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
