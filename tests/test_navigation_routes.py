from treepolo_mlb_data.webapp import STATIC_DIR


def test_navigation_items_are_upgraded_to_real_links_with_page_routes():
    routes = (STATIC_DIR / "navigation-routes.js").read_text(encoding="utf-8")
    loader = (STATIC_DIR / "fast-status.js").read_text(encoding="utf-8")

    assert 'document.createElement("a")' in routes
    assert 'url.searchParams.set("page", routeForPanel(panelId))' in routes
    assert '"sequence-panel": "sequence-pattern"' in routes
    assert '"follow-panel": "follow-up-event"' in routes
    assert '"workflow-panel": "research-workflow"' in routes
    assert '"cluster-compare-panel": "cluster-comparison"' in routes
    assert '"analysis-library-panel": "analysis-library"' in routes
    assert "/navigation-routes.js" in loader


def test_normal_left_click_stays_spa_but_modified_clicks_remain_native_link_actions():
    routes = (STATIC_DIR / "navigation-routes.js").read_text(encoding="utf-8")

    assert "event.ctrlKey || event.metaKey || event.shiftKey || event.altKey" in routes
    assert "event.preventDefault()" in routes
    assert "window.history.pushState" in routes
    assert "legacyButton.click()" in routes
    assert 'window.addEventListener("popstate"' in routes


def test_navigation_route_can_be_restored_on_direct_open():
    routes = (STATIC_DIR / "navigation-routes.js").read_text(encoding="utf-8")

    assert 'searchParams.get("page")' in routes
    assert "applyRouteFromLocation()" in routes
    assert "MutationObserver(scheduleRouteApply)" in routes
