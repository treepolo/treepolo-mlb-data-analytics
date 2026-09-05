from treepolo_mlb_data.webapp import STATIC_DIR


def source(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_navigation_items_are_real_links_but_route_generation_has_one_owner():
    routes = source("navigation-routes.js")
    panels = source("panel-activation.js")
    loader = source("fast-status.js")

    assert 'document.createElement("a")' in routes
    assert "panels()?.hrefForPanel?.(panelId)" in routes
    assert 'url.searchParams.set("page", routeForPanel(panelId))' in panels
    for mapping in (
        '"sequence-panel": "sequence-pattern"',
        '"follow-panel": "follow-up-event"',
        '"workflow-panel": "research-workflow"',
        '"cluster-compare-panel": "cluster-comparison"',
        '"analysis-library-panel": "analysis-library"',
    ):
        assert mapping in panels
    assert "/navigation-routes.js" in loader


def test_normal_left_click_delegates_spa_activation_and_modified_clicks_remain_native():
    routes = source("navigation-routes.js")
    panels = source("panel-activation.js")

    assert "event.ctrlKey || event.metaKey || event.shiftKey || event.altKey" in routes
    assert "event.preventDefault()" in routes
    assert 'panels()?.activate?.(panelId, { updateUrl:true, source:"route-link" })' in routes
    assert "window.history.pushState" in panels
    assert 'window.addEventListener("popstate"' in routes


def test_navigation_route_can_be_restored_on_direct_open():
    routes = source("navigation-routes.js")

    assert 'searchParams.get("page")' in routes
    assert 'applyRouteFromLocation("initial-route")' in routes
    assert "new MutationObserver(scheduleRouteApply)" in routes
    assert "panels()?.panelForRoute?.(route)" in routes
