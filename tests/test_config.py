from pathlib import Path
from treepolo_mlb_data.config import AppConfig, load_config, save_config


def test_config_roundtrip(tmp_path: Path):
    path = tmp_path / "config.json"
    cfg = AppConfig(recent_refresh_days=10, auto_update_enabled=True)
    save_config(path, cfg)
    loaded = load_config(path)
    assert loaded.recent_refresh_days == 10
    assert loaded.auto_update_enabled is True
