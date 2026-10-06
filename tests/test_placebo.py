from __future__ import annotations

import pytest

from treepolo_mlb_data.research.placebo import placebo_same_point


def _rows():
    rows = []
    for pa in range(1, 400):
        types = ["SL", "SL", "FF", "SL"] if pa % 2 else ["FF", "SL", "SL", "FF"]
        for j, t in enumerate(types, 1):
            rows.append({"game_pk": pa, "at_bat_number": 1, "pitch_type": t, "pitch_index_in_pa": j, "balls": 0, "e": 1, "x": float((pa + j) % 3 == 0)})
    return rows


def test_modes_are_validated_and_runs_are_deterministic():
    kwargs = dict(group_fields=(), strata_fields=("pitch_index_in_pa",), pitch_types=("SL",), kmax=3, shuffles=4, seed=3, min_n=5)
    with pytest.raises(ValueError, match="mode"):
        placebo_same_point(_rows(), mode="nope", **kwargs)
    a = placebo_same_point(_rows(), mode="pairs", **kwargs)
    assert a == placebo_same_point(_rows(), mode="pairs", **kwargs) and a
    assert a != placebo_same_point(_rows(), mode="labels", **kwargs)          # the two modes really differ when x depends on position only
    assert all(r["placebo_n"] == 4 for r in a)


def test_observed_adds_a_permutation_p_value():
    out = placebo_same_point(_rows(), group_fields=(), strata_fields=("pitch_index_in_pa",), pitch_types=("SL",), kmax=3, shuffles=4, seed=3, min_n=5,
                             observed={("SL", 2): 5.0})
    row = next(r for r in out if r["k"] == 2)
    assert row["observed"] == 5.0 and row["p_two_sided"] == pytest.approx(2 * (1 / 5))     # no shuffle reaches 5.0: (1+0)/(4+1), doubled
