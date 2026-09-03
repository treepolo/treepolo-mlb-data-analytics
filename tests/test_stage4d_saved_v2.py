from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from treepolo_mlb_data.analysis_state import AnalysisStateStore
from treepolo_mlb_data.stage4d import Stage4DService
from treepolo_mlb_data.stage4d_saved_v2 import SNAPSHOT_VERSION, install


class _Analysis:
    def __init__(self, result: dict):
        self.result = result
        self.calls = 0

    def analyze(self, payload: dict) -> dict:
        self.calls += 1
        return self.result


def _result() -> dict:
    return {
        "backend": "numerical",
        "sections": [
            {
                "title": "Summary",
                "columns": ["cluster", "sample_size"],
                "rows": [{"cluster": 0, "sample_size": 3}],
                "row_count": 1,
                "grain": {"keys": ["cluster"]},
            },
            {
                "title": "Assignments",
                "columns": ["pitch_uid", "cluster", "pfx_x", "pfx_z"],
                "rows": [
                    {"pitch_uid": "a", "cluster": 0, "pfx_x": 0.1, "pfx_z": 0.2},
                    {"pitch_uid": "b", "cluster": 0, "pfx_x": 0.2, "pfx_z": 0.3},
                    {"pitch_uid": "c", "cluster": 0, "pfx_x": 0.3, "pfx_z": 0.4},
                ],
                "row_count": 3,
                "grain": {"keys": ["pitch_uid"]},
            },
        ],
    }


def _app(tmp_path: Path, result: dict):
    database = tmp_path / "statcast.sqlite3"
    state = AnalysisStateStore(tmp_path / "analysis_state.sqlite3")
    config = SimpleNamespace(root=tmp_path, database_path=database)
    return SimpleNamespace(analysis_state=state, config=config, analysis=_Analysis(result))


def _spec() -> dict:
    return {
        "version": "stage4d-v1",
        "type": "bar",
        "mapping": {"x": "cluster", "y": "sample_size"},
        "sampling": {"mode": "automatic", "method": "random", "size": 5000, "seed": 42},
        "display": {"bar_orientation": "vertical", "legend": True, "show_n": True},
    }


def test_frozen_v2_keeps_all_sections_and_deduplicates_snapshot(tmp_path: Path):
    install()
    result = _result()
    app = _app(tmp_path, result)
    try:
        service = Stage4DService(app)
        request = {
            "name": "Frozen A",
            "save_mode": "frozen",
            "source": {"kind": "analysis_payload", "payload": {"mode": "clustering"}},
            "section": 1,
            "spec": _spec(),
        }
        first = service.save_visualization(request)
        request["name"] = "Frozen B"
        second = service.save_visualization(request)

        assert first["snapshot_version"] == SNAPSHOT_VERSION
        assert first["snapshot_hash"] == second["snapshot_hash"]
        snapshot_hash = first["snapshot_hash"]
        snapshot_path = tmp_path / "data" / "visualization_snapshots" / f"snapshot-{snapshot_hash}.json.gz"
        assert snapshot_path.is_file()
        assert len(list((tmp_path / "data" / "visualization_snapshots").glob("snapshot-*.json.gz"))) == 1

        _, restored, provenance = service.resolve_source({"kind": "visualization", "id": first["id"]})
        assert restored is not None
        assert [section["title"] for section in restored["sections"]] == ["Summary", "Assignments"]
        assert provenance["legacy_frozen"] is False

        assert service.store.delete_visualization(first["id"]) is True
        assert snapshot_path.is_file(), "shared snapshot must remain while another visualization references it"
        assert service.store.delete_visualization(second["id"]) is True
        assert not snapshot_path.exists(), "snapshot should be removed after its final visualization reference is deleted"
    finally:
        app.analysis_state.close()


def test_updating_loaded_visualization_does_not_create_self_reference(tmp_path: Path):
    install()
    app = _app(tmp_path, _result())
    try:
        service = Stage4DService(app)
        source = {"kind": "analysis_payload", "payload": {"mode": "clustering"}}
        saved = service.store.save_visualization(
            name="Live",
            notes="",
            save_mode="live",
            source=source,
            section_index=0,
            spec=_spec(),
            provenance={},
        )
        updated = service.save_visualization(
            {
                "name": "Live edited",
                "save_mode": "live",
                "source": {"kind": "visualization", "id": saved["id"]},
                "section": 0,
                "spec": _spec(),
            },
            saved["id"],
        )
        assert updated["source"] == source
    finally:
        app.analysis_state.close()
