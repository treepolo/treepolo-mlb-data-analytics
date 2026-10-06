from treepolo_mlb_data.webapp import STATIC_DIR


def test_research_page_is_wired_into_the_app():
    script = (STATIC_DIR / "research-runs-page.js").read_text(encoding="utf-8")
    index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    ci = (STATIC_DIR.parents[2] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert '<script src="/research-runs-page.js"></script>' in index
    assert index.index("/app.js") < index.index("/research-runs-page.js")
    assert 'const PANEL_ID = "research-runs-panel"' in script
    assert 'register?.(PANEL_ID, ROUTE)' in script and 'const ROUTE = "research-runs"' in script
    assert "node --check src/treepolo_mlb_data/web_static/research-runs-page.js" in ci
    for endpoint in ("/api/research/studies", "/api/research/runs", "/api/research/export", "/api/research/import", "/rerun"):
        assert endpoint in script
    assert "研究紀錄 Research Runs" in script and "匯出" in script and "匯入" in script
    assert "localStorage" not in script and "sessionStorage" not in script
    assert "innerHTML" not in script  # all user data is inserted with textContent


def test_chart_module_is_wired_and_its_node_test_passes():
    import shutil
    import subprocess

    index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    ci = (STATIC_DIR.parents[2] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert index.index("/research-charts.js") < index.index("/research-runs-page.js")
    assert "node --check src/treepolo_mlb_data/web_static/research-charts.js" in ci and "node tests/research-charts.test.cjs" in ci
    script = (STATIC_DIR / "research-runs-page.js").read_text(encoding="utf-8")
    assert "TreepoloResearchCharts" in script and "innerHTML" not in (STATIC_DIR / "research-charts.js").read_text(encoding="utf-8")
    node = shutil.which("node")
    if node:
        root = STATIC_DIR.parents[2]
        subprocess.run([node, str(root / "tests" / "research-charts.test.cjs")], check=True, cwd=root)
