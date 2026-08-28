from treepolo_mlb_data.analysis_state import AnalysisStateStore
from treepolo_mlb_data.result_projection import DEFAULT_CLIENT_RESULT_LIMIT, apply_result_limit


def result_with_rows(count: int) -> dict:
    return {
        "columns": ["n"],
        "rows": [{"n": index} for index in range(count)],
        "row_count": count,
        "backend": "sqlite",
    }


def test_shared_projection_preserves_fresh_no_limit_semantics_and_does_not_mutate_source():
    source = result_with_rows(10)
    assert apply_result_limit(source, {"mode": "basic"}) is source

    limited = apply_result_limit(source, {"mode": "basic", "result_limit": 3})
    assert len(source["rows"]) == 10
    assert len(limited["rows"]) == 3
    assert limited["row_count"] == 10
    assert limited["returned_row_count"] == 3
    assert limited["result_limit"] == 3


def test_history_detail_projects_legacy_cache_before_returning_to_client(tmp_path):
    payload = {"mode": "basic"}
    result = result_with_rows(1200)
    with AnalysisStateStore(tmp_path / "state.sqlite") as store:
        assert store.put_cached_result(
            "legacy-cache",
            data_revision="rev",
            backend="sqlite",
            payload=payload,
            result=result,
        )
        history_id = store.record_history(
            payload=payload,
            data_revision="rev",
            cache_key="legacy-cache",
            backend="sqlite",
            row_count=1200,
            status="success",
        )
        restored = store.get_history(history_id)

    assert restored is not None
    assert restored["result_available"] is True
    assert len(restored["result"]["rows"]) == DEFAULT_CLIENT_RESULT_LIMIT
    assert restored["result"]["row_count"] == 1200
    assert restored["result"]["returned_row_count"] == DEFAULT_CLIENT_RESULT_LIMIT
    assert restored["result"]["result_limit"] == DEFAULT_CLIENT_RESULT_LIMIT


def test_saved_detail_uses_original_payload_result_limit(tmp_path):
    payload = {"mode": "basic", "result_limit": 750}
    result = result_with_rows(1200)
    with AnalysisStateStore(tmp_path / "state.sqlite") as store:
        assert store.put_cached_result(
            "limited-cache",
            data_revision="rev",
            backend="sqlite",
            payload=payload,
            result=result,
        )
        saved = store.save_analysis(
            name="limited",
            payload=payload,
            cache_key="limited-cache",
            data_revision="rev",
        )

    assert saved["result_available"] is True
    assert len(saved["result"]["rows"]) == 750
    assert saved["result"]["row_count"] == 1200
    assert saved["result"]["returned_row_count"] == 750
    assert saved["result"]["result_limit"] == 750
