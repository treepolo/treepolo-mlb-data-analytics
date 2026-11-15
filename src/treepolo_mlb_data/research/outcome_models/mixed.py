"""Mixed-effects multinomial logistic regression: the pitcher, the batter (and the game, the pitch type ...) each get a vector of random effects with a normal prior whose variance is
estimated from the data (empirical Bayes, see ``shrinkage``).

It is the logistic model with the automatic shrinkage always on: whatever ``effect_penalty_mode`` says, the data choose the penalty of every effect group. The estimated penalties are
in the fit summary. It uses the grouped solver of the logistic model (``fastglm``, GPU when there is one); without PyTorch, or with the descriptor controls, the original L-BFGS solver.
"""
from __future__ import annotations

from . import controls as ctl
from .logistic import LogisticModel


class MixedModel(LogisticModel):
    name = "mixed"
    label_zh = "混合效應模型"
    label_en = "Mixed-effects model"
    force_auto = True
    cost_rule = "grouped"
    probe_settings = {"bagging_reps": 1}
    probe_unit = 0
    supports = {name: "隨機效應（常態先驗，變異數由資料估計）" for name in ctl.CONTROL_NAMES}
