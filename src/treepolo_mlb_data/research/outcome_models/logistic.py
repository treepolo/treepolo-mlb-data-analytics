"""Multinomial logistic regression with the situation interactions, optional player effects (each group with its own shrinkage), descriptors and pitch location."""
from __future__ import annotations

from typing import Any

import numpy as np
from scipy import sparse

from . import controls as ctl
from .base import OutcomeModel
from .shrinkage import estimated_penalties_for, fit_with_estimated_penalties
from .softmax import fit_softmax, softmax_rows


class LogisticModel(OutcomeModel):
    name = "logistic"
    label_zh = "邏輯迴歸"
    label_en = "Logistic regression"
    uses_design = True
    cost_rule = "linear"
    probe_settings = {"bagging_reps": 1}
    probe_unit = 0
    supports = {name: "效應直接放進多類別邏輯迴歸（稀疏欄位，各自的懲罰）" for name in ctl.CONTROL_NAMES}

    def _fit_member(self, sub, y, w, vsub, vy, s, seed, progress, design) -> tuple[dict[str, Any], dict[str, Any]]:
        D, E, spans = design
        x = sparse.hstack([D, E], format="csr") if E.shape[1] else D
        pen = self._penalties(D.shape[1], s)
        if self._use_auto(s, E):                          # the data choose how strongly each group of effects is shrunk
            res = fit_with_estimated_penalties(x, y, len(self.classes), pen, n_dense=D.shape[1], spans=spans, within=self.groups, col_counts=np.asarray(E.sum(axis=0)).ravel(),
                                               per_pitch=self.class_info, weights=w, max_iter=int(s["max_iter"]), em_rounds=int(s["em_rounds"]), progress=progress)
            fit = res["fit"]
            info = {"n_iter": res["n_iter"], "hit_cap": res["hit_cap"], "seconds": res["seconds"], "trace": [(i, loss, None) for i, loss in fit.trace],
                    "estimated_penalties": estimated_penalties_for(self.groups, res["penalties"])}
        else:
            fit = fit_softmax(x, y, len(self.classes), pen, weights=w, max_iter=int(s["max_iter"]))
            info = {"n_iter": fit.n_iter, "hit_cap": fit.hit_cap, "seconds": fit.seconds, "trace": [(i, loss, None) for i, loss in fit.trace]}
        member = {"coef": fit.coef.astype(np.float32), "intercept": fit.intercept.astype(np.float64), "n_dense": int(D.shape[1])}
        return member, info

    def _member_proba(self, member: dict[str, Any], pack: dict[str, Any], effects: str) -> np.ndarray:
        nd = member["n_dense"]
        z = pack["D"] @ member["coef"][:nd] + member["intercept"]
        e = pack.get("E")
        if effects == "specific" and e is not None and e.shape[1]:
            z = z + e @ member["coef"][nd:]
        return softmax_rows(np.asarray(z, dtype=np.float64))
