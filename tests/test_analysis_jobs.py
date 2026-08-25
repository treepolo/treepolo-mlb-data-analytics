from treepolo_mlb_data.analysis_jobs import (
    finish_analysis_job,
    get_analysis_job,
    start_analysis_job,
    update_analysis_job,
)


def test_analysis_job_progress_is_shared_and_monotonic():
    job_id = start_analysis_job("basic")
    first = get_analysis_job(job_id)
    assert first["status"] == "running"
    assert first["percentage"] == 0.0

    update_analysis_job(job_id, stage="planning", percentage=20, detail="planning")
    update_analysis_job(job_id, stage="second_query", percentage=5, detail="second")
    running = get_analysis_job(job_id)
    assert running["stage"] == "second_query"
    assert running["percentage"] == 20.0
    assert running["elapsed_seconds"] >= 0

    finish_analysis_job(job_id, backend="duckdb")
    done = get_analysis_job(job_id)
    assert done["status"] == "success"
    assert done["stage"] == "completed"
    assert done["percentage"] == 100.0
    assert done["backend"] == "duckdb"


def test_analysis_job_failure_is_visible():
    job_id = start_analysis_job("temporal")
    update_analysis_job(job_id, stage="duckdb_query", percentage=50)
    finish_analysis_job(job_id, error="boom")
    failed = get_analysis_job(job_id)
    assert failed["status"] == "failed"
    assert failed["error"] == "boom"
    assert failed["finished_at"] is not None
