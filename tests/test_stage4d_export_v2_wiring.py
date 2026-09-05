from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_cli_installs_parquet_export_v2_before_serving():
    cli = read("src/treepolo_mlb_data/cli.py")
    assert "from .stage4d_export_v2 import install as install_stage4d_export_v2" in cli
    assert "install_stage4d_export_v2()" in cli
    assert cli.index("install_stage4d_report_v2()") < cli.index("install_stage4d_export_v2()") < cli.index("install_stage4d_frontend_patch(webapp)")


def test_export_progress_is_bundled_and_visible_during_api_export():
    patch = read("src/treepolo_mlb_data/stage4d_frontend_patch.py")
    script = read("src/treepolo_mlb_data/web_static/stage4d-export-progress.js")
    assert "stage4d-export-progress.js" in patch
    assert "/api/export" in script or "api\\/export" in script
    assert "匯出中 Exporting…" in script
    assert "button.disabled = true" in script
    assert "button.disabled = false" in script
    assert 'button.setAttribute("aria-busy", "true")' in script
