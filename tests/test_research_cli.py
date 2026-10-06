import json

import pytest

from research_fixtures import make_research_db
from treepolo_mlb_data.cli import main

PAYLOAD = {"mode": "basic", "group_by": ["pitch_type"], "metrics": [{"function": "count"}], "limit": 50}


def make_env(tmp_path, name="env"):
    root = tmp_path / name
    config = root / "config.json"
    root.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"data_dir": str(root / "data"), "analysis_backend": "sqlite"}), encoding="utf-8")
    make_research_db(root / "data" / "statcast.sqlite3")
    return config, root


def run_cli(config, *argv, capsys, expect=0):
    code = main(["--config", str(config), *argv])
    out = capsys.readouterr()
    assert code == expect, out.err
    return (json.loads(out.out) if out.out.strip() else None), out.err


def test_methods_run_reuse_list_show(tmp_path, capsys):
    config, root = make_env(tmp_path)
    cfg = root / "cfg.json"
    cfg.write_text(json.dumps({"payload": PAYLOAD}), encoding="utf-8")
    methods, _ = run_cli(config, "research", "methods", capsys=capsys)
    assert "analysis_payload" in [m["kind"] for m in methods["methods"]]
    first, _ = run_cli(config, "research", "run", "--kind", "analysis_payload", "--config-file", str(cfg), "--study", "demo", capsys=capsys)
    second, _ = run_cli(config, "research", "run", "--kind", "analysis_payload", "--config-file", str(cfg), "--study", "demo", capsys=capsys)
    assert first["reused"] is False and second["reused"] is True and first["run"]["id"] == second["run"]["id"]
    forced, _ = run_cli(config, "research", "run", "--kind", "analysis_payload", "--config-file", str(cfg), "--force", capsys=capsys)
    assert forced["reproduced"] is True

    listing, _ = run_cli(config, "research", "list", "--study", "demo", capsys=capsys)
    assert [r["id"] for r in listing["runs"]] == [first["run"]["id"]] and listing["runs"][0]["total_rows"] == 2
    shown, _ = run_cli(config, "research", "show", str(first["run"]["id"]), "--limit", "1", capsys=capsys)
    assert shown["page"]["total"] == 2 and len(shown["page"]["rows"]) == 1 and shown["run"]["status"] == "success"
    again, _ = run_cli(config, "research", "rerun", str(first["run"]["id"]), capsys=capsys)
    assert again["run"]["id"] == first["run"]["id"] and again["reproduced"] is True


def test_study_notes_export_import(tmp_path, capsys):
    config, root = make_env(tmp_path, "a")
    cfg = root / "cfg.json"
    cfg.write_text(json.dumps({"payload": PAYLOAD}), encoding="utf-8")
    run_cli(config, "research", "run", "--kind", "analysis_payload", "--config-file", str(cfg), "--study", "share", capsys=capsys)
    insight = root / "insight.md"
    insight.write_text("# my insight", encoding="utf-8")
    study, _ = run_cli(config, "research", "study", "share", "--insight-file", str(insight), capsys=capsys)
    assert study["study"]["insight_markdown"] == "# my insight"
    out = root / "out" / "share.zip"
    written, _ = run_cli(config, "research", "export", "--study", "share", "--out", str(out), "--notes", "hi", capsys=capsys)
    assert out.is_file() and written["bytes"] == out.stat().st_size

    other_config, _ = make_env(tmp_path, "b")
    report, _ = run_cli(other_config, "research", "import", str(out), capsys=capsys)
    assert report["runs_added"] == 1 and report["studies_added"] == 1
    listing, _ = run_cli(other_config, "research", "list", capsys=capsys)
    assert listing["runs"][0]["origin"] == "imported"


def test_errors_return_exit_code_2(tmp_path, capsys):
    config, root = make_env(tmp_path)
    bad = root / "bad.json"
    bad.write_text(json.dumps({"payload": {"mode": "basic", "result_limit": 5}}), encoding="utf-8")
    _, err = run_cli(config, "research", "run", "--kind", "analysis_payload", "--config-file", str(bad), capsys=capsys, expect=2)
    assert "result_limit" in err
    _, err = run_cli(config, "research", "run", "--kind", "nope", "--config-file", str(bad), capsys=capsys, expect=2)
    assert "Unknown research method" in err
    _, err = run_cli(config, "research", "list", "--study", "missing", capsys=capsys, expect=2)
    assert "Study not found" in err
    _, err = run_cli(config, "research", "show", "999", capsys=capsys, expect=2)
    assert "Run not found" in err
    _, err = run_cli(config, "research", "import", str(root / "missing.zip"), capsys=capsys, expect=2)
    assert "error:" in err
    with pytest.raises(SystemExit):
        main(["--config", str(config), "research"])
