from treepolo_mlb_data.webapp import STATIC_DIR


def source(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def test_cluster_comparison_loader_is_claimed_before_domcontentloaded_race():
    fast = source("fast-status.js")
    stage4 = source("stage4-controls.js")

    claim = 'const clusterComparisonReady = loadScriptOnce("/cluster-comparison-page.js", "clusterCompareLoader");'
    assert claim in fast
    assert 'script[data-cluster-compare-loader]' in stage4

    # The shared marker must be created before the sequential enhancement awaits,
    # so Stage 4's DOMContentLoaded fallback sees the in-flight script and does not
    # create a second loader for the same URL.
    assert fast.index(claim) < fast.index('await loadScriptOnce("/field-option-legality-v3.js"')

    # Unified single-field controls must wait for the same cluster-page promise,
    # never a second loadScriptOnce call that can attach to an already-fired load event.
    assert 'await clusterComparisonReady;' in fast
    assert fast.index('await clusterComparisonReady;') < fast.index('await loadScriptOnce("/field-controls-unified.js"')
    assert fast.count('loadScriptOnce("/cluster-comparison-page.js"') == 1
