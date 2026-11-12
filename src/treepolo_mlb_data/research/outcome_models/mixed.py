"""Mixed-effects multinomial logistic regression: the pitcher, the batter (and the game, the pitch type ...) each get a vector of random effects with a normal prior, and the
variance of every prior is estimated from the data (empirical Bayes).

The fixed part is the same as in the logistic model. The prior variance of an effect group is 1 / penalty, so the estimation alternates: solve for the coefficients and the
effects given the penalties, then set each group's variance to the mean square of its effects, and repeat (``em_rounds`` times, each round warm-started from the last). A group
with little to say ends up with a large penalty (its effects are shrunk to 0, the league average); a group that matters keeps a small one. The estimated penalties are in the
fit summary. The problem is sparse and the solver is L-BFGS on the CPU, so this model needs no PyTorch.
"""
from __future__ import annotations

from typing import Any

import numpy as np
from scipy import sparse

from . import controls as ctl
from .base import OutcomeModel
from .logistic import LogisticModel
from .softmax import fit_softmax


class MixedModel(LogisticModel):
    name = "mixed"
    label_zh = "混合效應模型"
    label_en = "Mixed-effects model"
    cost_rule = "iterations"
    probe_settings = {"bagging_reps": 1, "em_rounds": 1}
    probe_unit = 1
    supports = {name: "隨機效應（常態先驗，變異數由資料估計）" for name in ctl.CONTROL_NAMES}

    def _fit_member(self, sub, y, w, vsub, vy, s, seed, progress, design) -> tuple[dict[str, Any], dict[str, Any]]:
        D, E, spans = design
        if not self.groups or E.shape[1] == 0:
            return super()._fit_member(sub, y, w, vsub, vy, s, seed, progress, design)
        x = sparse.hstack([D, E], format="csr")
        K = len(self.classes)
        pen = self._penalties(D.shape[1], s)
        fit, seconds, n_iter = None, 0.0, 0
        history = []
        for rnd in range(int(s["em_rounds"])):
            fit = fit_softmax(x, y, K, pen, weights=w, max_iter=int(s["max_iter"]), init=fit)
            seconds += fit.seconds; n_iter += fit.n_iter
            row = {}
            for g, within in self.groups.items():
                if within:
                    continue                                                             # "within" effects are not shrunk
                start, size = spans[g]
                block = fit.coef[D.shape[1] + start: D.shape[1] + start + size]
                var = float(np.clip(np.mean(block * block), 1e-4, 10.0)) if size else 1.0           # the prior variance of this group, from the effects just estimated
                pen[D.shape[1] + start: D.shape[1] + start + size] = 1.0 / var
                row[g] = 1.0 / var
            history.append(row)
            if progress:
                progress("outcome_model", (rnd + 1) / int(s["em_rounds"]), f"variance round {rnd + 1}")
        self._estimated_penalties = history[-1] if history else {}
        member = {"coef": fit.coef.astype(np.float32), "intercept": fit.intercept.astype(np.float64), "n_dense": int(D.shape[1])}
        return member, {"n_iter": n_iter, "hit_cap": fit.hit_cap, "seconds": seconds, "trace": [(i + 1, None, None) for i in range(len(history))], "estimated_penalties": self._estimated_penalties}

    def _summarize(self, infos: list[dict[str, Any]], s) -> dict[str, Any]:
        out = super()._summarize(infos, s)
        if infos and infos[0].get("estimated_penalties"):
            out["penalties"] = {**out["penalties"], **infos[0]["estimated_penalties"]}                  # what the data chose, not the starting values
            out["penalties_estimated"] = True
        return out
