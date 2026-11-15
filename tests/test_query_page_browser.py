"""The situation-query page in a real browser (Chromium through Playwright): form, table, flags, chart and the not-covered list. Screenshots go to docs/research/figures."""
from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from test_pitch_query_method import setup  # noqa: F401  (fixture: services with a pitch_query run)
from treepolo_mlb_data import webapp

playwright = pytest.importorskip("playwright.sync_api")
import os

FIGURES = Path(__file__).resolve().parents[1] / "docs" / "research" / "figures"      # screenshots are written here only when TREEPOLO_SAVE_FIGURES=1


@pytest.fixture(scope="module")
def server(setup):  # noqa: F811
    service, league, policy, query, d = setup
    services = webapp.AppServices(service.config)
    server = ThreadingHTTPServer(("127.0.0.1", 0), webapp._Handler)
    server.services = services
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/", query, service
    server.shutdown(); server.server_close()


def launch(p):
    exe = Path("/opt/pw-browsers")
    candidates = sorted(exe.glob("chromium-*/chrome-linux*/chrome")) if exe.exists() else []
    return p.chromium.launch(executable_path=str(candidates[0]) if candidates else None, args=["--no-sandbox"])


def test_a_query_without_intervals_says_so_and_does_not_count(server):
    """One member and no resampling: the answer has no intervals. Every interval cell says so and the comparison-count line says nothing can be counted."""

    import json
    import re

    url, query, service = server

    def strip(route):
        response = route.fetch()
        body = response.json()
        for row in body["rows"]:
            for key in ("q_lo", "q_hi", "adv_lo", "adv_hi", "m_lo", "m_hi", "hr_lo", "hr_hi"):
                row[key] = None
        body["comparisons"] = {"n": 0, "confidence": 0.99, "expected_by_chance": 0.0, "found": 0, "what": "interval_excludes_zero"}
        route.fulfill(response=response, body=json.dumps(body))

    with playwright.sync_playwright() as p:
        try:
            browser = launch(p)
        except Exception as exc:  # pragma: no cover - no browser in this environment
            pytest.skip(f"no Chromium available: {exc}")
        page = browser.new_page(viewport={"width": 1300, "height": 1500})
        page.route(re.compile(r"/api/research/pitch-query\?"), strip)
        page.goto(url + "?page=research-query")
        page.wait_for_selector("#rq-go", timeout=15000)
        page.click("#rq-go")
        page.wait_for_selector("#rq-table tbody tr", timeout=30000)
        table = page.locator("#rq-table").inner_text()
        assert "—（沒有算區間）" in table and "[—" not in table and "[nan" not in table.lower()
        supported = page.locator("#rq-table tbody tr:not(.rq-row-unsupported)").first.inner_text()
        assert supported.count("—（沒有算區間）") == 4                                           # Q, versus the usual mix, marginal change and the home-run probability
        assert "沒有算區間，無法計數" in page.locator("#rq-chance").inner_text() and "純靠運氣" not in page.locator("#rq-chance").inner_text()
        browser.close()


def test_query_page_shows_the_table_flags_and_chart(server, tmp_path):
    url, query, service = server
    pid = service.pitch_query_players(query["id"], "RvR", "pitcher", "")["players"][0]
    with playwright.sync_playwright() as p:
        try:
            browser = launch(p)
        except Exception as exc:  # pragma: no cover - no browser in this environment
            pytest.skip(f"no Chromium available: {exc}")
        page = browser.new_page(viewport={"width": 1300, "height": 1500})
        page.goto(url + "?page=research-query")
        page.wait_for_selector("#research-query-panel", state="visible", timeout=15000)
        page.wait_for_selector("#rq-go", timeout=15000)
        page.select_option("#rq-balls", "1"); page.select_option("#rq-strikes", "2"); page.select_option("#rq-outs", "1"); page.check("#rq-b1")
        page.select_option("#rq-prev", "SL"); page.select_option("#rq-streak", "2")
        page.click("#rq-go")
        page.wait_for_selector("#rq-table tbody tr", timeout=30000)
        assert page.locator("#rq-table tbody tr").count() == 9
        banner = page.locator("#rq-banner").inner_text()
        assert "M0" in banner and "RvR" in banner
        assert page.locator("tr.rq-row-unsupported").count() >= 1                      # types nobody throws are marked, not hidden
        assert "不涵蓋" in page.locator("#rq-notcovered").inner_text()
        assert page.locator("#rq-chart svg").count() == 1 and page.locator("#rq-chart rect.rc-bar").count() >= 2
        out = FIGURES if os.environ.get("TREEPOLO_SAVE_FIGURES") == "1" else tmp_path
        out.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out / "p6_query_m0.png"), full_page=True)
        # a named pitcher (M1): the pitcher's box fills in and the banner shows the mode and his data volume
        page.fill("#rq-pitcher-text", ""); page.click("#rq-pitcher-text ~ button")
        page.wait_for_function("document.querySelectorAll('#rq-pitcher-list option').length > 0", timeout=15000)
        page.select_option("#rq-pitcher-list", pid["id"])
        page.click("#rq-go")
        page.wait_for_function("document.querySelector('#rq-banner') && !/^M0/.test(document.querySelector('#rq-banner').textContent) && document.querySelector('#rq-status').textContent === ''", timeout=90000)
        text = page.locator("#rq-result").inner_text()
        assert "M1" in page.locator("#rq-banner").inner_text() and "投手" in text and "個人資料權重" in text          # mode, named pitcher, his data volume and weight
        assert page.locator("#rq-table tbody tr").count() == 9
        page.screenshot(path=str(out / "p6_query_m1.png"), full_page=True)
        browser.close()
