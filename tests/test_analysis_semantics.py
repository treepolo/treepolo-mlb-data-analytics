from treepolo_mlb_data.analysis import Binary, Column, default_registry


def test_default_semantics_are_thin_reusable_expressions():
    registry = default_registry()
    assert "whiff" in registry.names()
    assert "sweeper" in registry.names()
    sweeper = registry.resolve("sweeper")
    assert isinstance(sweeper, Binary)
    assert sweeper.left == Column("pitch_type")
