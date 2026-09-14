from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from treepolo_mlb_data.config import AppConfig, save_config


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def config_for(root: Path) -> AppConfig:
    return AppConfig(data_dir=str(root / "data"), analysis_backend="duckdb")


def wait_http(url: str, timeout: float = 45.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(.15)
    raise RuntimeError(f"server not ready: {url}")


async def response_for_click(page, selector: str) -> dict[str, Any]:
    async with page.expect_response(lambda r: r.url.endswith("/api/analyze") and r.request.method == "POST", timeout=120_000) as pending:
        await page.locator(selector).click()
    response = await pending.value
    body = await response.json()
    if not response.ok:
        raise AssertionError(body.get("error") or f"HTTP {response.status}")
    return body


async def activate(page, panel: str) -> None:
    nav = page.locator(f'button.nav-item[data-panel="{panel}"]')
    if await nav.count():
        await nav.first.click()
    else:
        await page.evaluate("panel => window.treepoloPanels.activate(panel,{updateUrl:false,source:'stage7'})", panel)
    await page.wait_for_function("panel => document.getElementById(panel)?.classList.contains('active-panel')", panel)


async def set_condition(page, host: str, field: str, op: str, value: str) -> None:
    row = page.locator(f"{host} .condition-row").first
    await row.locator(".condition-field").select_option(field)
    await row.locator(".condition-op").select_option(op)
    await row.locator(".condition-value").fill(str(value))


async def add_classic_filter(page, box_name: str, field: str, op: str, value: str) -> None:
    host = page.locator(f'[data-filter-box="{box_name}"]')
    await host.locator(".add-filter").click()
    row = host.locator(".condition-row").last
    await row.locator(".condition-field").select_option(field)
    await row.locator(".condition-op").select_option(op)
    await row.locator(".condition-value").fill(str(value))


async def apply_workflow(page, payload: dict[str, Any]) -> None:
    await page.wait_for_function("() => !!window.treepoloStage4Pages?.applyPayload")
    ok = await page.evaluate("payload => window.treepoloStage4Pages.applyPayload(payload)", payload)
    if ok is False:
        raise AssertionError("workflow applyPayload refused Stage 7 payload")
    await activate(page, "workflow-panel")


async def screenshot(page, output: Path, number: int, suffix: str = "") -> str:
    path = output / f"q{number:02d}{suffix}.png"
    await page.screenshot(path=str(path), full_page=True)
    return str(path)


async def run_browser(root: Path, base_url: str) -> dict[str, Any]:
    backend = load_json(root / "reports" / "7g_ten_questions_backend.json")
    details = {int(item["number"]): item.get("detail") or {} for item in backend["tests"]}
    target = details[1]["target_pitch_type"]
    output = root / "reports" / "e2e"
    output.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1600, "height": 1000})
        await page.goto(base_url, wait_until="networkidle", timeout=60_000)
        await page.wait_for_function("() => window.treepoloDatasetReady && !!window.treepoloFieldCatalog?.fields?.().length", timeout=60_000)
        meta = await page.evaluate("() => fetch('/api/meta').then(r=>r.json())")
        if meta.get("dataset",{}).get("id") != "cpbl":
            raise AssertionError("browser E2E is not bound to CPBL dataset")

        async def record(number: int, runner):
            started=time.perf_counter()
            try:
                detail=await runner()
                shot=await screenshot(page,output,number)
                results.append({"number":number,"status":"PASS","seconds":time.perf_counter()-started,"detail":detail,"screenshot":shot})
            except Exception as exc:
                shot=await screenshot(page,output,number,"-FAIL")
                results.append({"number":number,"status":"FAIL","seconds":time.perf_counter()-started,"error":f"{type(exc).__name__}: {exc}","screenshot":shot})

        async def q1():
            await activate(page,"sequence-panel")
            await set_condition(page,"#sequence-event","pitch_type","eq",target)
            await page.locator("#sequence-occurrence").fill("3")
            await page.locator("#sequence-exact").fill("3")
            await page.locator("#sequence-last").check()
            counts={}
            for arrangement in ("consecutive","none_adjacent"):
                await page.locator("#sequence-arrangement").select_option(arrangement)
                body=await response_for_click(page,'[data-run="sequence_pattern"]')
                count=int(body.get("row_count",len(body.get("rows",[]))))
                if count <= 0: raise AssertionError(f"#1 UI {arrangement} returned no rows")
                counts[arrangement]=count
            payload={"mode":"workflow","stages":[{"kind":"event_pattern_cohorts","event":{"field":"pitch_type","op":"eq","value":target},"occurrence":3,"exact_count":3,"require_last_event":True,"arrangements":["consecutive","none_adjacent"],"cohort_alias":"pattern_cohort"}],"limit":5000}
            await apply_workflow(page,payload)
            body=await response_for_click(page,"#workflow-panel button.primary")
            cohorts={r.get("pattern_cohort") for r in body.get("rows",[])}
            if not {"consecutive","none_adjacent"}.issubset(cohorts): raise AssertionError("#1 UI workflow lacks both cohorts")
            return {"target":target,"classic_counts":counts,"workflow_rows":body.get("row_count")}

        async def q2():
            await activate(page,"arsenal-panel")
            await page.locator("#arsenal-entities").fill("pitcher")
            await page.locator("#arsenal-min-usage").fill("0.05")
            arsenal=await response_for_click(page,'[data-run="arsenal"]')
            if not arsenal.get("rows"): raise AssertionError("#2 Arsenal UI empty")
            await activate(page,"role-panel")
            await page.locator("#role-entities").fill("pitcher")
            await page.locator("#role-metric-kind").select_option("usage_rate")
            await page.locator("#role-rank").fill("1")
            role=await response_for_click(page,'[data-run="pitch_role"]')
            if not role.get("rows"): raise AssertionError("#2 Pitch Role UI empty")
            payload={"mode":"workflow","stages":[{"kind":"arsenal_signature","entity_fields":["pitcher"],"pitch_field":"pitch_type","min_usage":.05,"alias":"arsenal"},{"kind":"pitch_role_select","entity_fields":["arsenal"],"pitch_field":"pitch_type","metric_kind":"usage_rate","rank":1,"tie_method":"dense_rank","alias":"arsenal_role_rank"},{"kind":"aggregate","group_by":["arsenal","pitch_type"],"metrics":[{"function":"count","alias":"pitch_rows"}]}],"limit":5000}
            await apply_workflow(page,payload); wf=await response_for_click(page,"#workflow-panel button.primary")
            if not wf.get("rows"): raise AssertionError("#2 same-arsenal workflow UI empty")
            return {"arsenal_rows":arsenal.get("row_count"),"role_rows":role.get("row_count"),"workflow_rows":wf.get("row_count")}

        async def q3():
            d=details[3]; pitcher=d["pitcher"]; ptype=d["pitch_type"]
            payload={"mode":"workflow","filters":[{"field":"pitcher","op":"eq","value":pitcher}],"stages":[{"kind":"aggregate","group_by":["pitcher","game_date","game_pk"],"metrics":[{"function":"count","alias":"total_count"},{"function":"count","alias":"target_count","condition":{"field":"pitch_type","op":"eq","value":ptype}},{"function":"avg","field":"release_speed","alias":"target_avg_speed","condition":{"field":"pitch_type","op":"eq","value":ptype}}]},{"kind":"derive","alias":"usage_rate","left":"target_count","operator":"/","right_field":"total_count"},{"kind":"trend","alias":"rising_3","field":"usage_rate","direction":"up","periods":3,"partition_by":["pitcher"],"order_by":[{"field":"game_date"},{"field":"game_pk"}],"strict":True},{"kind":"offset","alias":"game4_speed","field":"target_avg_speed","direction":"lead","offset":1,"partition_by":["pitcher"],"order_by":[{"field":"game_date"},{"field":"game_pk"}]},{"kind":"filter","field":"rising_3","op":"eq","value":True}],"limit":5000}
            await apply_workflow(page,payload); body=await response_for_click(page,"#workflow-panel button.primary")
            if not body.get("rows"): raise AssertionError("#3 UI workflow empty")
            return {"pitcher":pitcher,"pitch_type":ptype,"rows":body.get("row_count")}

        async def q4():
            payload={"mode":"workflow","stages":[{"kind":"pitch_role_annotate","entity_fields":["pitcher"],"metric_kind":"usage_rate","exclude_pitch_types":["FF"],"rank":1,"tie_method":"row_number","alias":"selected_pitch_type"},{"kind":"aggregate","group_by":["pitcher","selected_pitch_type"],"metrics":[{"function":"count","alias":"candidate_count","condition":{"field":"pitch_type","op":"eq","value_field":"selected_pitch_type"}},{"function":"count","alias":"ff_count","condition":{"field":"pitch_type","op":"eq","value":"FF"}},{"function":"avg","field":"release_speed","alias":"candidate_speed","condition":{"field":"pitch_type","op":"eq","value_field":"selected_pitch_type"}},{"function":"avg","field":"release_speed","alias":"ff_speed","condition":{"field":"pitch_type","op":"eq","value":"FF"}}]}],"limit":5000}
            await apply_workflow(page,payload); body=await response_for_click(page,"#workflow-panel button.primary")
            if not body.get("rows"): raise AssertionError("#4 UI workflow empty")
            return {"rows":body.get("row_count")}

        async def q5():
            payload={"mode":"workflow","stages":[{"kind":"arsenal_signature","entity_fields":["pitcher"],"pitch_field":"pitch_type","min_usage":.05,"alias":"arsenal"},{"kind":"aggregate","group_by":["arsenal","pitcher"],"metrics":[{"function":"count","alias":"total_count"},{"function":"count","alias":"ff_count","condition":{"field":"pitch_type","op":"eq","value":"FF"}},{"function":"avg","field":"release_speed","alias":"avg_speed"}]},{"kind":"derive","alias":"ff_usage_rate","left":"ff_count","operator":"/","right_field":"total_count"},{"kind":"empirical_percentile","field":"ff_usage_rate","partition_by":["arsenal"],"alias":"ff_usage_pct"},{"kind":"filter","field":"ff_usage_pct","op":"ge","value":.5},{"kind":"aggregate","group_by":["arsenal"],"metrics":[{"function":"count","alias":"pitchers_high_half"},{"function":"avg","field":"avg_speed","alias":"cohort_avg_speed"}]}],"limit":5000}
            await apply_workflow(page,payload); body=await response_for_click(page,"#workflow-panel button.primary")
            if not body.get("rows"): raise AssertionError("#5 UI workflow empty")
            return {"rows":body.get("row_count")}

        async def q6():
            await activate(page,"follow-panel")
            await set_condition(page,"#follow-anchor","pitch_type","eq",target)
            await set_condition(page,"#follow-target","pitch_type","eq",target)
            await set_condition(page,"#follow-between","pitch_type","eq","FF")
            await page.locator("#follow-gap").fill("3")
            body=await response_for_click(page,'[data-run="follow_event"]')
            if not body.get("rows"): raise AssertionError("#6 UI follow-event empty")
            return {"target":target,"rows":body.get("row_count")}

        async def q7():
            await activate(page,"cross-panel")
            await add_classic_filter(page,"cross","pitch_type","eq","FF")
            await page.locator("#cross-unit").fill("pitcher,game_pk")
            await page.locator("#cross-baseline").fill("pitcher")
            await page.locator("#cross-value").select_option("release_speed")
            body=await response_for_click(page,'[data-run="cross_level"]')
            if not body.get("rows"): raise AssertionError("#7 UI cross-level empty")
            return {"rows":body.get("row_count")}

        async def q8():
            d=details[8]
            await activate(page,"arsenal-change-panel")
            await page.locator("#change-entities").fill("pitcher")
            await page.locator("#change-min-usage").fill("0.05")
            await page.locator("#change-a-start").fill(str(d["period_a"]["start"]))
            await page.locator("#change-a-end").fill(str(d["period_a"]["end"]))
            await page.locator("#change-b-start").fill(str(d["period_b"]["start"]))
            await page.locator("#change-b-end").fill(str(d["period_b"]["end"]))
            body=await response_for_click(page,'[data-run="arsenal_change"]')
            count=sum(int(s.get("row_count",0)) for s in body.get("sections",[]))
            if count<=0: raise AssertionError("#8 UI arsenal-change empty")
            return {"rows":count}

        async def q9():
            await activate(page,"percentile-panel")
            await page.locator("#percentile-entities").fill("pitcher")
            await page.locator("#percentile-value").select_option("release_speed")
            await page.locator("#percentile-threshold").fill("80")
            await page.locator("#percentile-side").select_option("high")
            body=await response_for_click(page,'[data-run="percentile"]')
            if not body.get("rows"): raise AssertionError("#9 UI percentile empty")
            return {"rows":body.get("row_count")}

        async def q10():
            d=details[10]
            payload={"mode":"cluster_compare","entity_fields":["pitcher"],"min_usage":.05,"reference_pitch_type":"FF","selection_value_field":"release_speed","selection_function":"avg","selection_direction":"desc","tie_method":"row_number","features":d["features"],"method":"kmeans","clusters":2,"standardize":True,"seed":42,"evaluation_field":"release_speed","evaluation_direction":"desc","max_input_rows":500000}
            await page.wait_for_function("() => !!window.treepoloClusterComparePage?.applyPayload")
            ok=await page.evaluate("p=>window.treepoloClusterComparePage.applyPayload(p)",payload)
            if not ok: raise AssertionError("#10 cluster UI refused payload")
            await activate(page,"cluster-compare-panel")
            body=await response_for_click(page,"#cc-run")
            comparison=(body.get("sections") or [{}])[0]
            if not comparison.get("rows"): raise AssertionError("#10 UI cluster comparison empty")
            return {"rows":comparison.get("row_count"),"features":d["features"]}

        for n,runner in enumerate((q1,q2,q3,q4,q5,q6,q7,q8,q9,q10),1):
            await record(n,runner)
        await browser.close()

    failed=[r["number"] for r in results if r["status"]!="PASS"]
    return {"stage":"7G-browser-e2e","dataset":"cpbl","tests":results,"passed":10-len(failed),"failed":failed}


def main() -> None:
    parser=argparse.ArgumentParser();parser.add_argument("--root",type=Path,default=Path("stage7_work"));parser.add_argument("--port",type=int,default=8899);args=parser.parse_args()
    root=args.root.resolve();config=config_for(root);config_path=root/"stage7_e2e_config.json";save_config(config_path,config)
    base_url=f"http://127.0.0.1:{args.port}/"
    proc=subprocess.Popen([sys.executable,"-m","treepolo_mlb_data.cli","--config",str(config_path),"--dataset","cpbl","ui","--host","127.0.0.1","--port",str(args.port),"--no-browser"],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env={**os.environ,"PYTHONUNBUFFERED":"1"})
    try:
        wait_http(base_url+"api/meta")
        report=asyncio.run(run_browser(root,base_url))
        dump_json(root/"reports"/"7g_ten_questions_e2e.json",report)
        if report["failed"]: raise SystemExit("Stage 7G browser E2E failed: "+",".join(map(str,report["failed"])))
    finally:
        proc.terminate()
        try: proc.wait(timeout=10)
        except subprocess.TimeoutExpired: proc.kill()


if __name__=="__main__": main()
