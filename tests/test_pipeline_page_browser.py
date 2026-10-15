"""The one-click page in Chromium: a finished pipeline (real server, real runs) and the running / failed states (mocked API)."""
from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from test_pipeline import finished, settings, world  # noqa: F401  (fixtures: a service with a finished pipeline)
from treepolo_mlb_data import webapp

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def url(world, finished):  # noqa: F811
    services = webapp.AppServices(world.config)
    services.research = world           # the page talks to the service that holds the finished pipeline
    server = ThreadingHTTPServer(("127.0.0.1", 0), webapp._Handler)
    server.services = services
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown(); server.server_close()


def launch(p):
    candidates = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux*/chrome")) if Path("/opt/pw-browsers").exists() else []
    try:
        return p.chromium.launch(executable_path=str(candidates[0]) if candidates else None, args=["--no-sandbox"])
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"no Chromium available: {exc}")


def test_finished_pipeline_shows_steps_summary_and_opens_the_query_page(url):
    with playwright.sync_playwright() as p:
        browser = launch(p)
        page = browser.new_page(viewport={"width": 1300, "height": 1700})
        page.goto(url + "?page=research-pipeline")
        page.wait_for_selector("#research-pipeline-panel", state="visible", timeout=15000)
        page.wait_for_selector(".rp-step", timeout=15000)
        assert page.locator(".rp-step").count() == 5
        assert page.locator(".rp-step.done").count() == 5
        page.wait_for_selector("#rp-open-query", timeout=15000)
        card = page.locator(".rp-card[data-group=RvR]")
        assert card.count() == 1 and "引擎檢查" in card.inner_text() and "每隊每季少失分" in card.inner_text()
        assert "選到的模型設定" in page.locator("#rp-best").inner_text()
        assert page.locator("#rp-start").is_enabled() and page.locator("#rp-cancel").is_disabled()
        assert "XGBoost" in page.locator("#rp-env").inner_text() or "scikit-learn" in page.locator("#rp-env").inner_text()
        assert page.locator("input[name=rp-group]").count() == 4 and page.locator("input[name=rp-tier]").count() == 3
        if __import__("os").environ.get("TREEPOLO_SAVE_FIGURES") == "1":
            page.screenshot(path=str(Path(__file__).resolve().parents[1] / "docs" / "research" / "figures" / "p6_pipeline_done.png"), full_page=True)
        page.click("#rp-open-query")
        page.wait_for_selector("#research-query-panel", state="visible", timeout=15000)
        page.wait_for_selector("#rq-go", timeout=15000)
        browser.close()


def test_running_and_failed_states_are_shown(url):
    steps = [{"id": i, "kind": k, "title": t, "status": "pending", "run_id": None, "seconds": None, "reused": None, "message": "", "fraction": None}
             for i, k, t in (("search", "param_search", "模型參數搜尋"), ("model", "league_model", "訓練全聯盟模型"), ("anchor", "action_effects", "固定效應對照答案"),
                             ("policy", "pitch_policy", "決策引擎與情境表"), ("query", "pitch_query", "查詢工具與驗收"))]
    job = {"state": "idle", "steps": [], "settings": None}
    info = {"tiers": [{"id": "fast", "label": "快速", "trials": 4, "folds": 3, "members": 5}, {"id": "standard", "label": "標準", "trials": 8, "folds": 5, "members": 30}],
            "environment": {"xgboost": True, "cuda": True, "default_backend": "xgboost", "default_device": "cuda"}, "groups": ["RvR", "RvL", "LvR", "LvL"], "steps": [s["title"] for s in steps]}
    seen = {}

    def handle(route):
        path = route.request.url.split("/api/research/pipeline/")[1].split("?")[0]
        if path == "info":
            body = info
        elif path == "start":
            seen["start"] = json.loads(route.request.post_data)
            job.update(state="running", steps=[dict(s) for s in steps])
            job["steps"][0].update(status="running", fraction=0.5, message="trial 2/4")
            body = job
        else:
            body = job
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    with playwright.sync_playwright() as p:
        browser = launch(p)
        page = browser.new_page(viewport={"width": 1300, "height": 1200})
        page.route("**/api/research/pipeline/**", handle)
        page.goto(url + "?page=research-pipeline")
        page.wait_for_selector("#rp-start", timeout=15000)
        assert "GPU" in page.locator("#rp-env").inner_text()
        page.check("input[name=rp-tier][value=standard]"); page.uncheck("input[name=rp-group][value=LvL]"); page.uncheck("input[name=rp-group][value=LvR]")
        page.click("#rp-start")
        page.wait_for_selector(".rp-step.running", timeout=15000)
        assert seen["start"] == {"tier": "standard", "groups": ["RvR", "RvL"]}
        assert page.locator("#rp-start").is_disabled() and page.locator("#rp-cancel").is_enabled() and "trial 2/4" in page.locator(".rp-step.running").inner_text()
        assert page.locator("input[name=rp-tier][value=standard]").is_checked() and not page.locator("input[name=rp-group][value=LvL]").is_checked()      # the choices stay while polling
        # the step fails: the message says which step and what to do
        job["steps"][0].update(status="done", seconds=61.0, fraction=1.0, message="")
        job["steps"][1].update(status="error", message="boom")
        job.update(state="error", error="步驟「訓練全聯盟模型」失敗：boom（已完成的步驟會保留，修正後再按開始會從這一步繼續）")
        page.wait_for_selector("#rp-error", timeout=15000)
        assert "訓練全聯盟模型" in page.locator("#rp-error").inner_text() and page.locator(".rp-step.error").count() == 1 and page.locator("#rp-start").is_enabled()
        browser.close()
