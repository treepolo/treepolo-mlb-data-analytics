"""The mixed-effects model (no PyTorch needed) and the neural-network models (skipped when PyTorch is not installed)."""
from __future__ import annotations

import numpy as np
import pytest

from treepolo_mlb_data.research import outcome_models as om, policy_worlds as pw
from treepolo_mlb_data.research.config_schema import ConfigError
from treepolo_mlb_data.research.outcome_models import controls as ctl
from treepolo_mlb_data.research.policy_core import OUTCOMES

try:
    import torch
    HAS_TORCH = True
except ImportError:                                                  # pragma: no cover
    HAS_TORCH = False


def world(kind="W4", n_pa=8000, seed=3):
    w = pw.build_world(kind)
    cols = pw.simulate_pitches(w, n_pa, seed)
    pa = np.asarray(cols["pa_id"])
    cols = dict(cols, at_bat_number=pa, pitch_number=(np.arange(len(pa)) - np.searchsorted(pa, pa) + 1))          # rows are in plate-appearance order
    return w, cols


def fit(kind, cols, **settings):
    base = dict(max_iter=40, bagging_reps=1, seed=2, epochs=2, batch_size=2048, hidden_size=16, embedding_dim=4, device="auto", backend="hgb")
    return om.new_model(kind).fit(cols, None, {**base, **settings})


# ------------------------------------------------------------------------------------------------ mixed effects
def test_mixed_effects_estimate_a_variance_for_every_group_and_shrink_unseen_units_to_the_league():
    w, cols = world()
    model = fit("mixed", cols, control_pitcher="effect", control_batter="effect", em_rounds=3)
    assert model.name == "mixed" and "mixed" in om.model_kinds()
    est = model.info["penalties"]
    assert model.info["penalties_estimated"] and set(est) == {"pitcher", "batter"} and all(v > 0 for v in est.values())              # what the data chose
    assert est["pitcher"] != 20.0 and est["batter"] != 20.0 and model.info["effects"]["pitcher"] == len(np.unique(cols["pitcher"]))       # not the starting values
    rows = {k: v[:300] for k, v in cols.items() if isinstance(v, np.ndarray) and len(v) >= 300}
    new = dict(rows, pitcher=np.full(300, 987654), batter=np.full(300, 876543))
    assert np.allclose(model.predict_proba(new, effects="specific"), model.predict_proba(new, effects="population"), atol=1e-12)
    assert np.allclose(model.predict_proba(rows).sum(axis=1), 1) and model.classes == list(OUTCOMES)
    assert fit("mixed", cols).info["effects"] == {}                                                                              # without effects it is the logistic model


def test_mixed_effects_give_a_strongly_varying_group_a_smaller_penalty_than_a_group_that_does_not_vary():
    """In W4 the pitchers differ (a latent quality) and the batters do not: the batter penalty should come out larger (more shrinkage) than the pitcher penalty."""

    w, cols = world("W4", 30_000, 5)
    est = fit("mixed", cols, control_pitcher="effect", control_batter="effect", em_rounds=4).info["penalties"]
    assert est["batter"] > est["pitcher"]


# ------------------------------------------------------------------------------------------------ PyTorch models
@pytest.mark.skipif(not HAS_TORCH, reason="PyTorch is not installed")
def test_nn_embed_zero_vector_means_league_average_and_storage_round_trips():
    w, cols = world()
    model = fit("nn_embed", cols, control_pitcher="effect", epochs=3)
    assert "nn_embed" in om.model_kinds() and model.info["n_iter"] >= 1 and model.info["trace"] and not hasattr(model, "_x_dense")
    rows = {k: v[:200] for k, v in cols.items() if isinstance(v, np.ndarray) and len(v) >= 200}
    new = dict(rows, pitcher=np.full(200, 987654))
    assert np.allclose(model.predict_proba(new, effects="specific"), model.predict_proba(new, effects="population"), atol=1e-6)
    seen = model.predict_proba(rows, effects="specific"); pop = model.predict_proba(rows, effects="population")
    assert np.allclose(seen.sum(axis=1), 1, atol=1e-5) and np.abs(seen - pop).max() > 1e-4                                    # a known pitcher's embedding is not the zero vector
    again = om.OutcomeModel.from_bytes(model.to_bytes())
    assert np.allclose(again.predict_proba(rows, effects="specific"), seen, atol=1e-5)
    net_state = model.members[0]["state"]
    assert float(net_state["emb.pitcher.weight"][0].abs().sum()) == 0.0                                                          # row 0 is never trained


@pytest.mark.skipif(not HAS_TORCH, reason="PyTorch is not installed")
def test_nn_embed_uses_the_gpu_when_there_is_one_and_a_stronger_penalty_shrinks_the_embeddings():
    w, cols = world()
    loose = fit("nn_embed", cols, control_pitcher="effect", effect_penalty_pitcher=0.01, epochs=3)
    tight = fit("nn_embed", cols, control_pitcher="effect", effect_penalty_pitcher=1e6, epochs=3)
    size = lambda m: float(m.members[0]["state"]["emb.pitcher.weight"].abs().mean())
    assert size(tight) < size(loose)
    assert loose.info["device"].startswith("cuda" if torch.cuda.is_available() else "cpu")                                      # device auto: the GPU when PyTorch can use one
    if torch.cuda.is_available():
        assert fit("nn_embed", cols, device="cuda", epochs=1).info["device"].startswith("cuda")
    else:
        with pytest.raises(ConfigError, match="cuda"):
            fit("nn_embed", cols, device="cuda", epochs=1)


@pytest.mark.skipif(not HAS_TORCH, reason="PyTorch is not installed")
@pytest.mark.parametrize("cell", ["gru", "transformer"])
def test_sequence_model_reads_the_earlier_pitches_and_refuses_situations_without_them(cell):
    w, cols = world()
    model = fit("sequence", cols, sequence_cell=cell, sequence_length=4, epochs=2)
    rows = {k: v[:500] for k, v in cols.items() if isinstance(v, np.ndarray) and len(v) >= 500}
    p = model.predict_proba(rows)
    assert p.shape == (500, len(OUTCOMES)) and np.allclose(p.sum(axis=1), 1, atol=1e-5) and np.isfinite(p).all()
    first = rows["pitch_number"] == 1
    assert first.any()                                                                      # a first pitch has no history and still gets an answer
    grid = {k: v for k, v in rows.items() if k not in ("at_bat_number", "pitch_number")}
    with pytest.raises(ConfigError, match="sequence|序列"):
        model.predict_proba(grid)                                                           # the decision engine's grid has no earlier pitches


@pytest.mark.skipif(not HAS_TORCH, reason="PyTorch is not installed")
def test_the_sequence_history_is_the_earlier_pitches_of_the_same_plate_appearance():
    w, cols = world(n_pa=300)
    model = fit("sequence", cols, sequence_length=3, epochs=1)
    prev = model._history({k: v for k, v in cols.items() if isinstance(v, np.ndarray)})
    pa = np.asarray(cols["pa_id"])
    for i in (0, 5, 40, 200):
        got = [j for j in prev[i] if j >= 0]
        assert all(pa[j] == pa[i] and j < i for j in got) and got == sorted(got)           # earlier, same plate appearance, oldest first
        expected = [j for j in range(max(0, i - 3), i) if pa[j] == pa[i]]
        assert got == expected[-3:]


def test_the_models_that_need_pytorch_say_so_when_it_is_missing(monkeypatch):
    from treepolo_mlb_data.research import outcome_models as pkg

    monkeypatch.setattr(pkg, "_MODELS", {k: v for k, v in pkg._MODELS.items() if k not in ("nn_embed", "sequence")})
    monkeypatch.setattr(pkg, "_load", lambda: None)
    with pytest.raises(ConfigError, match="PyTorch"):
        pkg.get_model_class("nn_embed")
    assert {"logistic", "gbm", "mixed"} <= set(pkg.model_kinds())


def test_the_decision_engine_refuses_the_sequence_model():
    from treepolo_mlb_data.research.policy_methods import PitchPolicyMethod

    class Store:
        def get_run(self, run_id):
            return {"kind": "outcome_model_train", "status": "success", "run_key": "k"}

        def load_result(self, run_id):
            return {"extras": {"model": "sequence"}}

        def load_artifact(self, run_id, name):
            return {"rows": []}

    class Ctx:
        store = Store()

    with pytest.raises(ConfigError, match="sequence"):
        PitchPolicyMethod._model_run(Ctx(), {"model_run_id": 3})
