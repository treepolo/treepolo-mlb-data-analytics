from pathlib import Path
from treepolo_mlb_data.config import AppConfig, load_config, save_config


def test_config_roundtrip(tmp_path: Path):
    path = tmp_path / "config.json"
    cfg = AppConfig(recent_refresh_days=10, auto_update_enabled=True)
    save_config(path, cfg)
    loaded = load_config(path)
    assert loaded.recent_refresh_days == 10
    assert loaded.auto_update_enabled is True


def test_research_config_defaults_and_paths():
    cfg = AppConfig(data_dir="somewhere")
    assert cfg.research_state_database_name == "research_runs.sqlite3"
    assert cfg.research_blob_dir_name == "research_runs"
    assert cfg.research_state_database_path == Path("somewhere") / "research_runs.sqlite3"
    assert cfg.research_blob_dir == Path("somewhere") / "research_runs"


def test_old_config_without_research_keys_still_loads(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text('{"data_dir": "d", "recent_refresh_days": 3}', encoding="utf-8")
    loaded = load_config(path)
    assert loaded.recent_refresh_days == 3
    assert loaded.research_state_database_name == "research_runs.sqlite3"


def test_unknown_config_key_is_still_rejected(tmp_path: Path):
    import pytest
    path = tmp_path / "config.json"
    path.write_text('{"not_a_real_key": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown configuration keys"):
        load_config(path)
