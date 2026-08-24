from treepolo_mlb_data.analysis import (
    Aggregate, Binary, Column, Filter, Grain, Literal, Metric, NamedExpr,
    PITCH_GRAIN, Source, node_from_dict, node_to_dict, validate,
)


def test_ast_round_trip_and_grain_validation():
    node = Aggregate(
        Filter(Source("pitches", PITCH_GRAIN), Binary(Column("pitch_type"), "=", Literal("FF"))),
        (NamedExpr("game_pk", Column("game_pk")),),
        (Metric("avg_velocity", "avg", Column("release_speed")), Metric("pitch_count", "count")),
        Grain(("game_pk",), "game"),
    )
    payload = node_to_dict(node)
    restored = node_from_dict(payload)
    assert restored == node
    assert validate(restored).keys == ("game_pk",)


def test_aggregate_rejects_mismatched_grain():
    node = Aggregate(
        Source("pitches", PITCH_GRAIN),
        (NamedExpr("game_pk", Column("game_pk")),),
        (Metric("pitch_count", "count"),),
        Grain(("pitcher",), "wrong"),
    )
    try:
        validate(node)
    except ValueError as exc:
        assert "grain keys" in str(exc)
    else:
        raise AssertionError("expected grain validation failure")
