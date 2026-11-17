"""The grouped solver of the logistic model (``outcome_models.fastglm``) against the original L-BFGS solver on small data, and what the grouped fit guarantees.

Both solve the same penalised multinomial logistic regression, so on the same data they must give the same predictions (and, without player effects, the same coefficients). Tests marked
``report`` write the known-answer numbers of W4, W5 and W7 under both solvers to docs/research/known_answer_report.json."""
from __future__ import annotations

import numpy as np
import pytest

from report_helper import record
from treepolo_mlb_data.research import outcome_models as om, policy_worlds as pw

torch = pytest.importorskip("torch")

BASE = dict(max_iter=1000, bagging_reps=1, seed=3, device="cpu", control_count_pitch="none")  # the equivalence with the old solver is about the same model: the count x pitch-type block is shrunk only by the grouped solver


def _world(kind, n_pa, seed=2):
    return pw.simulate_pitches(pw.build_world(kind), n_pa, seed)


def _fit(cols, solver, **extra):
    return om.new_model("logistic").fit(cols, None, {**BASE, "solver": solver, **extra})


def _rows(cols, n=1500):
    return {k: v[:n] for k, v in cols.items() if isinstance(v, np.ndarray) and len(v) >= n}


@pytest.fixture(scope="module")
def w4():
    return _world("W4", 3000)


def _centred(x):
    """The softmax does not change when every class gets the same number added: compare coefficients after taking that out."""

    return x - x.mean(axis=-1, keepdims=True)


def test_without_player_effects_the_coefficients_are_the_same(w4):
    new, old = _fit(w4, "grouped"), _fit(w4, "lbfgs")
    a, b = new.members[0], old.members[0]
    assert np.abs(_centred(a["coef"][: a["n_dense"]]) - _centred(b["coef"][: b["n_dense"]])).max() < 1e-2
    assert np.abs(_centred(a["intercept"]) - _centred(b["intercept"])).max() < 2e-2
    rows = _rows(w4)
    for effects in ("population", "specific"):
        assert np.abs(new.predict_proba(rows, effects=effects) - old.predict_proba(rows, effects=effects)).max() < 5e-4
    assert new.info["n_iter"] < 30 and not new.info["hit_cap"]                      # merged situations: a handful of Newton steps


@pytest.mark.parametrize("extra", [
    dict(control_pitcher="effect", effect_penalty_mode="manual", effect_penalty_pitcher=7.0),
    dict(control_pitcher="effect", control_batter="effect", effect_penalty_mode="manual", effect_penalty_pitcher=7.0, effect_penalty_batter=9.0),
    dict(control_pitcher="within"),
    dict(control_pitcher_pitch="within"),
    dict(control_day="within"),
])
def test_with_player_effects_the_predictions_for_named_players_are_the_same(w4, extra):
    new, old = _fit(w4, "grouped", **extra), _fit(w4, "lbfgs", **extra)
    rows = _rows(w4)
    diff = np.abs(new.predict_proba(rows, effects="specific") - old.predict_proba(rows, effects="specific"))
    assert diff.max() < 6e-3 and diff.mean() < 5e-4                                    # (a nearly unpenalised effect of a unit that saw one outcome only keeps drifting)
    assert not new.info["hit_cap"]


def test_the_automatic_shrinkage_is_close_to_the_one_the_old_solver_chose(w4):
    new, old = _fit(w4, "grouped", control_pitcher="effect"), _fit(w4, "lbfgs", control_pitcher="effect")
    assert new.info["penalties_estimated"] and old.info["penalties_estimated"]
    a, b = np.asarray(new.info["penalties_by_class"]["pitcher"]), np.asarray(old.info["penalties_by_class"]["pitcher"])
    assert np.abs(np.log(a / b)).max() < 2.0                                               # both estimates are noisy (the old one is a few rounds of an approximation): the same order of magnitude
    rows = _rows(w4)
    assert np.abs(new.predict_proba(rows, effects="specific") - old.predict_proba(rows, effects="specific")).max() < 0.02


def test_the_league_average_player_is_the_average_one_when_effects_are_nearly_unpenalised(w4):
    """After the effects are centred the population prediction matches what happened (the pitch-type level used to stay inside the effects)."""

    cols = w4
    model = _fit(cols, "grouped", control_pitcher_pitch="within", control_pitcher="within")
    pop = model.predict_proba(cols, effects="population")
    for t in ("FF", "SL", "CH"):
        m = np.asarray(cols["pitch_type"]) == t
        if m.sum() > 500:
            ci = model.classes.index("whiff")
            observed = float((np.asarray(cols["outcome"])[m] == "whiff").mean())
            assert abs(float(pop[m][:, ci].mean()) - observed) < 0.03


def _population_gaps(model, cols):
    """(largest overall gap, largest gap by count) between the mean population prediction and what happened, in probability."""

    from treepolo_mlb_data.research.outcome_models import design as dz

    sub, y = model._encode(cols, model.settings, fixed_classes=True)
    pop = model.predict_proba(sub, effects="population")
    K = len(model.classes)
    share = np.bincount(y, minlength=K) / len(y)
    overall = float(np.abs(pop.mean(axis=0) - share).max())
    cnt = dz.count_cell(sub)
    by_count = max(float(np.abs(pop[cnt == c].mean(axis=0) - np.bincount(y[cnt == c], minlength=K) / (cnt == c).sum()).max()) for c in range(12) if (cnt == c).sum() > 300)
    return overall, by_count


@pytest.mark.parametrize("extra", [
    dict(),
    dict(control_pitcher="within", control_pitcher_pitch="within", control_day="within", control_batter="effect", control_survival="batter_and_count"),
    dict(control_pitcher_pitch="within", control_batter="effect", control_survival="batter_and_count"),
])
def test_the_league_average_player_reproduces_what_happened_overall_and_by_count(extra, monkeypatch):
    """With nearly unpenalised effects the units that never saw a rare outcome get effects far below 0 for it; the average of such logits is not the logit of the average probability.
    The calibration makes the population prediction match the observed share of every class, overall and by count (before it: up to 17 points off by count, 5 points overall), and
    it only moves means between the effects and their parents, so named players keep exactly the predictions they had."""

    from treepolo_mlb_data.research.outcome_models import fastglm

    cols = _world("W4", 6000, 3)
    model = _fit(cols, "grouped", **extra)
    overall, by_count = _population_gaps(model, cols)
    assert overall < 0.003 and by_count < 0.02
    monkeypatch.setattr(fastglm.GroupedSoftmax, "calibrate", lambda self, *a, **k: None)
    plain = _fit(cols, "grouped", **extra)
    rows = _rows(cols)
    assert np.abs(model.predict_proba(rows, effects="specific") - plain.predict_proba(rows, effects="specific")).max() < 1e-5
    if extra:
        assert _population_gaps(plain, cols)[0] > overall                                  # the information-weighted centring alone leaves the population off


def test_a_fit_that_follows_another_starts_from_it_and_keeps_its_shrinkage(w4):
    main = _fit(w4, "grouped", control_pitcher="effect", control_batter="effect")
    keep = np.nonzero(np.asarray(w4["pitcher"]) % 5 != 0)[0]
    sub = {k: v[keep] for k, v in w4.items() if isinstance(v, np.ndarray) and len(v) == len(w4["game_pk"])}
    cold = _fit(sub, "grouped", control_pitcher="effect", control_batter="effect")
    warm = om.new_model("logistic").fit(sub, None, {**BASE, "solver": "grouped", "control_pitcher": "effect", "control_batter": "effect"}, None, classes=main.classes, reference=main)
    rows = _rows(sub)
    assert warm.info["n_iter"] < cold.info["n_iter"]
    assert np.abs(warm.predict_proba(rows, effects="specific") - cold.predict_proba(rows, effects="specific")).max() < 0.03
    assert warm.info["penalties"] == main.info["penalties"] or all(abs(warm.info["penalties"][g] - main.info["penalties"][g]) < 1e-6 for g in main.info["penalties"])


def test_bootstrap_members_start_from_the_full_fit_and_differ_a_little(w4):
    model = _fit(w4, "grouped", control_pitcher="effect", bagging_reps=3)
    rows = _rows(w4, 800)
    members = model.predict_members(rows, effects="specific")
    assert members.shape == (3, 800, len(model.classes)) and np.allclose(members.sum(axis=2), 1)
    spread = float(members.std(axis=0).max())
    assert 1e-5 < spread < 0.3


def test_every_bootstrap_member_runs_until_it_has_converged_and_says_so(w4):
    """A member starts from the full fit and runs by the same criterion and the same cap as the full fit (no fixed 40 sweeps): the sweeps of the main fit and of every member, and whether each
    converged, are in the model's info."""

    model = _fit(w4, "grouped", control_pitcher="effect", bagging_reps=3)
    info = model.info
    assert info["main_fit"]["sweeps"] >= 1 and not info["main_fit"]["hit_cap"] and info["n_iter"] == info["main_fit"]["sweeps"]
    fits = info["member_fits"]
    assert [m["member"] for m in fits] == [1, 2, 3] and all(m["converged"] and m["sweeps"] >= 1 and m["seconds"] >= 0 for m in fits) and not info["hit_cap"]
    one = _fit(w4, "grouped", control_pitcher="effect")                                          # one member: the main fit is the member
    assert one.info["main_fit"]["sweeps"] == one.info["member_fits"][0]["sweeps"] and len(one.info["member_fits"]) == 1
    capped = _fit(w4, "grouped", control_pitcher="effect", bagging_reps=2, max_iter=2)           # a cap of two sweeps: nothing converges, and every fit says so
    assert capped.info["hit_cap"] and capped.info["main_fit"]["hit_cap"] and not any(m["converged"] for m in capped.info["member_fits"])
    assert max(m["sweeps"] for m in capped.info["member_fits"]) <= 2


def test_the_descriptor_controls_and_the_location_still_use_the_original_solver():
    from treepolo_mlb_data.research.synthetic import simulate_players

    d = simulate_players("P7", n_pitchers=20, n_batters=30, n_games=200, pitches_per_game=40, seed=7)
    d["prior_streak"] = np.zeros(len(d["game_pk"]))
    model = _fit(d, "grouped", control_pitcher="descriptors")
    assert not model._grouped() and model.members and np.isfinite(model.predict_proba(_rows(d, 200), effects="population")).all()


def test_the_gpu_gives_the_same_answer_as_the_cpu(w4):
    if not torch.cuda.is_available():
        pytest.skip("no GPU")
    cpu = _fit(w4, "grouped", control_pitcher="effect")
    gpu = om.new_model("logistic").fit(w4, None, {**BASE, "device": "cuda", "solver": "grouped", "control_pitcher": "effect"})
    rows = _rows(w4)
    assert np.abs(cpu.predict_proba(rows, effects="specific") - gpu.predict_proba(rows, effects="specific")).max() < 1e-4


# ------------------------------------------------------------------------------------------------ known answers under both solvers (numbers only)
def _repeat_effect(model, cols, action="SL"):
    n = min(len(cols["game_pk"]), 8000)
    base = {k: v[:n] for k, v in cols.items() if isinstance(v, np.ndarray) and len(v) >= n}
    first = dict(base, pitch_type=np.full(n, action), prev1_pitch_type=np.full(n, "FF" if action != "FF" else "SL"), streak_pos=np.ones(n))
    rep = dict(base, pitch_type=np.full(n, action), prev1_pitch_type=np.full(n, action), streak_pos=np.full(n, 2.0))
    i = model.classes.index("whiff")
    return float((model.predict_proba(rep, effects="population")[:, i] - model.predict_proba(first, effects="population")[:, i]).mean())


def _slider_minus_fastball(model, cols):
    n = min(8000, len(cols["game_pk"]))
    base = {k: v[:n] for k, v in cols.items() if isinstance(v, np.ndarray) and len(v) >= n}
    i = model.classes.index("whiff")
    sl = model.predict_proba(dict(base, pitch_type=np.full(n, "SL"), streak_pos=np.ones(n), prev1_pitch_type=np.full(n, "none")), effects="population")[:, i]
    ff = model.predict_proba(dict(base, pitch_type=np.full(n, "FF"), streak_pos=np.ones(n), prev1_pitch_type=np.full(n, "none")), effects="population")[:, i]
    return float((sl - ff).mean())


@pytest.mark.report
def test_report_known_answers_under_both_solvers():
    """W7 (the true repeat effect is 0) and W4 / W5 (the true slider effect is 0): the same controls, the new grouped solver and the original L-BFGS, 20,000 plate appearances each.
    A nearly unpenalised group needs thousands of L-BFGS iterations, so this takes a few minutes. Numbers only."""

    out = {}
    cases = (("W7", _repeat_effect, {"none": {}, "pitcher": dict(control_pitcher="effect"), "pitcher_pitch_effect": dict(control_pitcher_pitch="effect"),
                                      "pitcher_pitch_within": dict(control_pitcher_pitch="within")}),
             ("W4", _slider_minus_fastball, {"none": {}, "pitcher_effect": dict(control_pitcher="effect"), "pitcher_within": dict(control_pitcher="within")}),
             ("W5", _slider_minus_fastball, {"none": {}, "day_effect": dict(control_day="pitcher_game_effect"), "day_within": dict(control_day="within")}))
    for kind, measure, variants in cases:
        cols = _world(kind, 20_000, 7)
        for name, extra in variants.items():
            for solver in ("grouped", "lbfgs"):
                model = _fit(cols, solver, **extra)
                out[f"{kind}_{name}_{solver}"] = measure(model, cols)
                out[f"{kind}_{name}_{solver}_n_iter"] = float(model.info["n_iter"]); out[f"{kind}_{name}_{solver}_hit_cap"] = int(model.info["hit_cap"])
    record("grouped_solver_vs_lbfgs_known_answers", **out)
    assert all(np.isfinite(v) for v in out.values() if isinstance(v, float))
