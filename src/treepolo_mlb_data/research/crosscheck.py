"""Cross-check of the repeat and previous-pitch effects with ``pyfixest`` (a report section; the decision engine never uses it).

For every outcome class one linear probability model (``feols``): the outcome (0/1) on the effect of being the second or third pitch of the same type in a row and on the type of the
previous pitch, with fixed effects for what the controls of the run ask for (pitcher x pitch type, pitcher x game, batter x count ...; a pitcher-overall or batter-overall effect that
another group already covers is left out) and for the count, outs, runners and pitch type. Standard errors are clustered by game. It is a different method (a linear probability model, no
shrinkage, the interactions of the previous pitch with the pitch type left out so that these effects are estimated as plain coefficients), so agreeing with the logistic model's
numbers is a check, not an identity.

With ``control_pitcher_pitch = within`` the fixed effect of every pitcher-and-pitch-type pair swallows what is the same for all pitches of that type: the overall level of each pitch type is
not controlled for (the logistic model calibrates it to the observed share), only comparisons inside the same pitcher and the same pitch type are. The repeat and previous-pitch effects
below are exactly such comparisons."""
from __future__ import annotations

from typing import Any, Mapping

import numpy as np

from .outcome_models import design as dz

SITUATION_FE = ("count", "outs", "bases", "type", "count*type", "bases*outs")      # the blocks that do not involve the previous pitch or the streak
COLUMNS = ("group", "class", "term", "effect", "se", "lo", "hi", "n")
NOTE_PITCHER_PITCH_ZH = ("開 control_pitcher_pitch=within 時，球種之間的整體水準是校準到實際比例（等於沒有控制）；"
                         "只有同一投手、同一球種內部的比較（連投、上一球）有控制。")
NOTE_PITCHER_PITCH_EN = ("With control_pitcher_pitch = within, the overall level of each pitch type is calibrated to the observed share (so it is NOT controlled for); "
                         "only comparisons inside the same pitcher and the same pitch type (repeat, previous pitch) are controlled.")


def identification_notes(controls: Mapping[str, Any]) -> list[dict[str, str]]:
    notes = []
    if controls.get("control_pitcher_pitch") == "within":
        notes.append({"control": "control_pitcher_pitch", "zh": NOTE_PITCHER_PITCH_ZH, "en": NOTE_PITCHER_PITCH_EN})
    if controls.get("control_day_pitch") == "within":
        notes.append({"control": "control_day_pitch", "zh": NOTE_PITCHER_PITCH_ZH.replace("control_pitcher_pitch", "control_day_pitch").replace("同一投手、同一球種", "同一投手、同一場比賽、同一球種"),
                      "en": NOTE_PITCHER_PITCH_EN.replace("control_pitcher_pitch", "control_day_pitch").replace("same pitcher and the same pitch type", "same pitcher, game and pitch type")})
    return notes


def available() -> bool:
    try:
        import pyfixest  # noqa: F401
        import pandas  # noqa: F401
    except Exception:
        return False
    return True


def fixed_effect_sets(groups: Mapping[str, bool]) -> list[str]:
    """The player groups to use as fixed effects: the ones the controls ask for, without a pitcher-overall or batter-overall effect that a finer group already covers."""

    names = list(groups)
    if "pitcher" in names and any(g in names for g in ("pitcher_pitch", "pitcher_game", "pitcher_game_pitch")):
        names.remove("pitcher")
    if "batter" in names and "batter_count" in names:
        names.remove("batter")
    return names


def run(group: str, sub: Mapping[str, np.ndarray], y: np.ndarray, classes: list[str], groups: Mapping[str, bool], *, confidence: float = 0.95, progress=None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(rows, info). ``sub``: the encoded training rows; ``y``: the class index of every row. ``info`` says whether it ran and why not."""

    if not available():
        return [], {"available": False, "reason": "pyfixest is not installed / 沒有安裝 pyfixest"}
    import pandas as pd
    import pyfixest as pf
    from scipy import stats

    n = len(y)
    off = np.array([dz.SIT_OFFSETS[b[0]] for b in dz.SIT_BLOCKS])
    bi = dz.situation_indices(sub) - off[None, :]
    names = [b[0] for b in dz.SIT_BLOCKS]
    data: dict[str, np.ndarray] = {}
    fes: list[str] = []
    for j, nm in enumerate(names):
        if nm in SITUATION_FE:
            data[f"b{j}"] = bi[:, j].astype(np.int32); fes.append(f"b{j}")
    del bi
    used = fixed_effect_sets(groups)
    for g in used:
        data["g_" + g] = pd.factorize(dz.effect_keys(sub, g))[0].astype(np.int32); fes.append("g_" + g)
    data["game"] = pd.factorize(np.asarray(sub["game_pk"]))[0].astype(np.int32)
    streak = np.clip(np.nan_to_num(np.asarray(sub["streak_pos"], dtype=float), nan=1.0), 1, 3).astype(int)
    covars = {"run_2": (streak == 2).astype(np.float32), "run_3plus": (streak == 3).astype(np.float32)}
    prev = dz.prev_index(sub["prev1_pitch_type"])
    shares = np.bincount(prev[prev < dz.N_TYPES], minlength=dz.N_TYPES)
    baseline = int(shares.argmax()) if shares.sum() else -1              # the previous type the others are compared with (the dummies of all types would add up to "not the first pitch", which the count already tells)
    for t, name in enumerate(dz.PITCH_TYPES):
        if t != baseline and (prev == t).any():
            covars[f"prev={name}"] = (prev == t).astype(np.float32)
    data.update({f"x{i}": v for i, v in enumerate(covars.values())})
    term_of = {f"x{i}": k for i, k in enumerate(covars)}
    present = [k for k, v in zip(covars, covars.values()) if v.any() and not v.all()]
    xs = [f"x{i}" for i, k in enumerate(covars) if k in present]
    if not xs:
        return [], {"available": False, "reason": "the data have no repeat or previous-pitch variation to estimate / 資料裡沒有連投或前一球的變化可以估"}
    rows: list[dict[str, Any]] = []
    crit = float(stats.norm.ppf(0.5 + confidence / 2.0))
    fe = " + ".join(fes) if fes else "1"
    for k, cname in enumerate(classes):
        if not (y == k).any():
            continue
        data["y"] = (y == k).astype(np.float32)
        df = pd.DataFrame(data)
        fit = pf.feols(f"y ~ {' + '.join(xs)} | {fe}", data=df, vcov={"CRV1": "game"}, copy_data=False, store_data=False, lean=False)
        tidy = fit.tidy()
        for coef_name, r in tidy.iterrows():
            est, se = float(r["Estimate"]), float(r["Std. Error"])
            rows.append({"group": group, "class": cname, "term": term_of.get(str(coef_name), str(coef_name)), "effect": est, "se": se, "lo": est - crit * se, "hi": est + crit * se, "n": int(getattr(fit, "_N", n))})
        del fit, df
        if progress:
            progress(k + 1, len(classes), cname)
    info = {"available": True, "previous_pitch_baseline": (dz.PITCH_TYPES[baseline] if baseline >= 0 else None), "fixed_effects": [n_ for n_ in (SITUATION_FE + tuple(used))], "covariates": present, "clustered_by": "game", "confidence": confidence}
    return rows, info
