from treepolo_mlb_data.webapp import STATIC_DIR


def source(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_performance_diagnostics_is_opt_in_and_reports_callback_sources():
    diagnostics = source("performance-diagnostics.js")
    assert 'const STORAGE_KEY = "treepolo_perf_diagnostics"' in diagnostics
    assert 'window.localStorage.getItem(STORAGE_KEY) === "1"' in diagnostics
    assert "class InstrumentedMutationObserver" in diagnostics
    assert "EventTarget.prototype.addEventListener" in diagnostics
    assert "instrumentedSetTimeout" in diagnostics
    assert "instrumentedSetInterval" in diagnostics
    assert "instrumentedRequestAnimationFrame" in diagnostics
    assert 'supported.includes("longtask")' in diagnostics
    assert "eventLoopStalls" in diagnostics
    assert "performance.memory" in diagnostics
    assert "callback_aggregates" in diagnostics
    assert "slow_callbacks" in diagnostics
    assert "long_tasks" in diagnostics


def test_diagnostics_loads_before_observer_heavy_enhancements_but_after_foundational_legality():
    bootstrap = source("fast-status.js")
    legality_at = bootstrap.index('loadScriptOnce("/field-option-legality-v3.js"')
    diagnostics_at = bootstrap.index('loadScriptOnce("/performance-diagnostics.js"')
    acceptance_at = bootstrap.index('loadScriptOnce("/acceptance-fixes.js"')
    unified_at = bootstrap.index('loadScriptOnce("/field-controls-unified.js"')

    assert legality_at < diagnostics_at < acceptance_at
    assert diagnostics_at < unified_at
    assert '/analysis-save-ui.js' not in bootstrap
    assert '/analysis-load-metadata.js' not in bootstrap


def test_diagnostics_has_user_visible_copy_reset_and_stop_controls():
    diagnostics = source("performance-diagnostics.js")
    assert "效能診斷 Perf: ON" in diagnostics
    assert "複製診斷報告 Copy Report" in diagnostics
    assert "清除並重新記錄 Reset" in diagnostics
    assert "停止診斷 Stop + Reload" in diagnostics
    assert "navigator.clipboard.writeText" in diagnostics
