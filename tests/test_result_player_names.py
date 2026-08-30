from treepolo_mlb_data.webapp import STATIC_DIR


def source(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_player_name_enhancer_covers_statcast_player_id_columns():
    js = source("result-player-names.js")
    for field in (
        "pitcher", "batter", "on_1b", "on_2b", "on_3b",
        "fielder_2", "fielder_3", "fielder_4", "fielder_5",
        "fielder_6", "fielder_7", "fielder_8", "fielder_9",
    ):
        assert f"{field}:" in js
    assert 'const PEOPLE_API = "https://statsapi.mlb.com/api/v1/people";' in js
    assert "personIds" in js
    assert "person?.fullName" in js
    assert "data.playerNameFor" not in js  # DOM ownership uses dataset, not a second result model.


def test_player_names_are_display_only_and_inserted_before_the_id_column():
    js = source("result-player-names.js")
    assert 'th.dataset.playerNameFor = match.field;' in js
    assert 'th.dataset.resultKey = match.config.key;' in js
    assert 'match.header.insertAdjacentElement("beforebegin", th);' in js
    assert 'idCell.insertAdjacentElement("beforebegin", nameCell);' in js
    assert "api/analyze" not in js
    assert "group_by" not in js
    assert "payload" not in js


def test_player_name_enhancer_uses_machine_key_for_localized_result_headers():
    names = source("result-player-names.js")
    app = source("app.js")
    assert "function headerKey(header)" in names
    assert "header?.dataset?.resultKey" in names
    assert 'header?.getAttribute?.("title")' in names
    assert "const rawKeys = new Set(headers.map(headerKey));" in names
    assert "const field = headerKey(header);" in names
    assert "th.title = column;" in app


def test_result_paging_reapplies_names_after_each_page_render():
    paging = source("result-paging.js")
    hook = "window.treepoloPlayerNames?.enhanceTable?.(table);"
    assert paging.count(hook) >= 2
    assert paging.index(hook) < paging.index("function installPagerFor")


def test_player_name_enhancer_loads_before_result_paging():
    fast = source("fast-status.js")
    names = 'await loadScriptOnce("/result-player-names.js", "resultPlayerNames");'
    paging = 'await loadScriptOnce("/result-paging.js", "resultPaging");'
    assert names in fast
    assert paging in fast
    assert fast.index(names) < fast.index(paging)
