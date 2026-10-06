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
