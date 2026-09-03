from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_export_fidelity_layer_is_bundled():
    patch = read("src/treepolo_mlb_data/stage4d_frontend_patch.py")
    assert '"stage4d-export-fidelity.js"' in patch


def test_standalone_svg_embeds_all_chart_presentation_styles():
    script = read("src/treepolo_mlb_data/web_static/stage4d-export-fidelity.js")
    assert "ensureEmbeddedStyles" in script
    assert 'document.createElementNS(SVG_NS, "style")' in script
    assert 'svg.insertBefore(style, svg.firstChild)' in script
    for css_class in (
        ".viz-label",
        ".viz-title",
        ".viz-subtitle",
        ".viz-axis",
        ".viz-gridline",
        ".viz-reference",
        ".viz-legend",
    ):
        assert css_class in script
    assert "Microsoft JhengHei" in script
    assert "stroke-dasharray: 5 4" in script
    assert "#viz-svg,#viz-png,#viz-copy,#viz-report" in script


def test_png_and_report_share_the_same_serialized_svg_source():
    primary = read("src/treepolo_mlb_data/web_static/stage4d-visualization.js")
    assert "function serializeSvg()" in primary
    assert "const svg=serializeSvg()" in primary
    assert "chart_svg:serializeSvg()" in primary
