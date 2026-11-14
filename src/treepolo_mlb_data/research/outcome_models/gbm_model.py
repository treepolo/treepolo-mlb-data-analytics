"""Boosted trees (XGBoost, or scikit-learn without player effects) with the player effects handed to the trees as an offset (base margin).

Stage 1 fits a penalised multinomial logistic regression of the situation, the pitch type and the player effects; the part of its prediction that comes from the effects
(``effect columns @ effect coefficients``, in logits) is the offset. Stage 2 trains the trees on top of that offset, so what the trees learn is what is left after the players
have been accounted for. With ``backfit_rounds`` > 1 the effects are re-estimated given the trees and the trees are trained again. A prediction for the league-average
player uses offset 0; for named players the offset of their effects (a player the model has not seen has offset 0).
"""
from __future__ import annotations

from typing import Any

import numpy as np
from scipy import sparse

from .. import gbm
from .. import league_core as lc
from . import controls as ctl
from . import design as dz
from .base import OutcomeModel
from .shrinkage import estimated_penalties_for, fit_with_estimated_penalties
from .softmax import fit_softmax


class GbmModel(OutcomeModel):
    name = "gbm"
    label_zh = "梯度提升樹"
    label_en = "Gradient-boosted trees"
    uses_design = False
    cost_rule = "rounds"
    probe_settings = {"max_iter": 20, "bagging_reps": 1, "backfit_rounds": 1}
    probe_unit = 20
    supports = {name: "樹用偏移量（base_margin）吸收效應，樹學剩下的部分（需要 xgboost）" for name in ctl.CONTROL_NAMES if name not in ("control_location",)}
    supports["control_location"] = "訓練時放 plate_x、plate_z；預測時對歷史位置抽樣平均"

    # ------------------------------------------------------------------------------------------------ features
    def _tree_features(self, rows, *, training: bool, effects: str = "specific") -> np.ndarray:
        x, _ = lc.situation_features(rows, with_action=True)
        blocks = [x]
        if self.profile is not None:
            if training:
                d = self._descriptors(rows, history=True); self.desc_std = dz.Standardizer(d)
                blocks.append(d.astype(np.float32))
            elif effects == "population":
                blocks.append(np.tile(self.desc_std.mean.astype(np.float32), (len(rows["game_pk"]), 1)))          # the league-average player
            else:
                blocks.append(self._descriptors(rows, history=False).astype(np.float32))
        if self.pool is not None:
            blocks.append(np.column_stack([np.asarray(rows["plate_x"], dtype=np.float32), np.asarray(rows["plate_z"], dtype=np.float32)]))
        return np.hstack(blocks) if len(blocks) > 1 else x

    def _training_design(self, sub, s):
        _, E, spans = super()._training_design(sub, s)
        self._x_tree = self._tree_features(sub, training=True)
        return None, E, spans

    # ------------------------------------------------------------------------------------------------ one member
    def _stage1(self, sub, y, w, E, s, tree_margin=None, init=None):
        sit, _ = dz.situation_design(sub)
        x = sparse.hstack([sit, E], format="csr")
        pen = self._penalties(sit.shape[1], s)
        self._last_estimated = None
        if self._use_auto(s, E):
            _, spans = dz.effect_matrix(sub, self.indexes, list(self.groups))
            res = fit_with_estimated_penalties(x, y, len(self.classes), pen, n_dense=sit.shape[1], spans=spans, within=self.groups, col_counts=np.asarray(E.sum(axis=0)).ravel(),
                                               per_pitch=self.class_info, weights=w, offset=tree_margin, max_iter=int(s["max_iter"]), em_rounds=int(s["em_rounds"]), init=init)
            self._last_estimated = estimated_penalties_for(self.groups, res["penalties"])
            return res["fit"], sit.shape[1]
        fit = fit_softmax(x, y, len(self.classes), pen, weights=w, offset=tree_margin, max_iter=int(s["max_iter"]), init=init)
        return fit, sit.shape[1]

    def _fit_member(self, sub, y, w, vsub, vy, s, seed, progress, design) -> tuple[dict[str, Any], dict[str, Any]]:
        _, E, spans = design
        backend = s["backend"]; device = gbm.resolve_device(backend, s["device"])
        params = {k: s[k] for k in ("max_depth", "min_leaf", "learning_rate", "l2", "max_iter", "early_stopping_rounds")}
        K = len(self.classes)
        x_tree = self._x_tree
        has_effects = bool(self.groups) and E.shape[1] > 0
        eff_coef = None; offset = None; fit1 = None; n_dense1 = 0
        info: dict[str, Any] = {"seconds": 0.0}
        if has_effects:
            fit1, n_dense1 = self._stage1(sub, y, w, E, s)
            eff_coef = fit1.coef[n_dense1:]
            offset = np.asarray(E @ eff_coef, dtype=np.float64)
            info["seconds"] += fit1.seconds
        games = np.asarray(sub["game_pk"])
        if vsub is not None:
            fit_idx = np.arange(len(y)); xv = self._tree_features(vsub, training=False, effects="specific"); yv = vy
            ev, _ = dz.effect_matrix(vsub, self.indexes, list(self.groups)) if has_effects else (None, None)
            valid_offset = None if ev is None else np.asarray(ev @ eff_coef, dtype=np.float64)
        elif backend == "xgboost":
            fit_idx, val_idx = lc.game_split(games, 0.1, seed)
            xv = x_tree[val_idx] if len(val_idx) else None; yv = y[val_idx] if len(val_idx) else None
            valid_offset = None if (offset is None or not len(val_idx)) else offset[val_idx]
        else:
            fit_idx = np.arange(len(y)); xv = yv = valid_offset = None
        rounds = int(s["backfit_rounds"]) if has_effects else 1
        tree = None
        for rd in range(rounds):
            classes_present = np.unique(y[fit_idx])
            bm = None if offset is None else offset[fit_idx][:, classes_present]
            vm = None if (valid_offset is None) else valid_offset[:, classes_present]
            tree = gbm.fit_gbm(backend, device, params, x_tree[fit_idx], y[fit_idx], K, sample_weight=None if w is None else w[fit_idx], x_valid=xv, y_valid=yv, seed=seed,
                               base_margin=bm, valid_margin=vm)
            info["seconds"] += tree.seconds
            if rd + 1 < rounds:                       # re-estimate the effects given the trees, then train the trees again
                margin = self._tree_margin(tree, x_tree, K)
                fit1, _ = self._stage1(sub, y, w, E, s, tree_margin=margin, init=fit1)
                eff_coef = fit1.coef[n_dense1:]
                offset = np.asarray(E @ eff_coef, dtype=np.float64)
                if valid_offset is not None and vsub is not None:
                    valid_offset = np.asarray(ev @ eff_coef, dtype=np.float64)
                elif valid_offset is not None:
                    valid_offset = offset[val_idx]
        if has_effects and getattr(self, "_last_estimated", None):
            info["estimated_penalties"] = self._last_estimated
        info["n_iter"] = float(tree.n_iter)
        info["hit_cap"] = bool(backend == "xgboost" and tree.n_iter >= int(s["max_iter"]))
        hist = tree.history
        train_h, valid_h = hist.get("train", []), hist.get("valid", [])
        info["trace"] = [(i + 1, train_h[i] if i < len(train_h) else None, valid_h[i] if i < len(valid_h) else None) for i in range(9, max(len(train_h), len(valid_h)), 10)]
        return {"tree": tree, "eff_coef": None if eff_coef is None else eff_coef.astype(np.float32), "classes_present": [int(c) for c in tree.classes]}, info

    @staticmethod
    def _tree_margin(tree, x, n_classes: int) -> np.ndarray:
        """Logits the trees alone produce (the offset they were trained on is not included), one column per class of the model."""

        import xgboost as xgb

        raw = tree.model.predict(xgb.DMatrix(np.asarray(x, dtype=np.float32)), output_margin=True, iteration_range=(0, tree.n_iter)).reshape(len(x), -1)
        out = np.zeros((len(x), n_classes))
        out[:, list(tree.classes)] = raw
        return out

    # ------------------------------------------------------------------------------------------------ prediction
    def _pack(self, rows, effects):
        pack = super()._pack(rows, effects)
        pack["x"] = self._tree_features(rows, training=False, effects=effects)
        return pack

    def _member_proba(self, member: dict[str, Any], pack: dict[str, Any], effects: str) -> np.ndarray:
        tree = member["tree"]
        e = pack.get("E")
        margin = None
        if effects == "specific" and member["eff_coef"] is not None and e is not None and e.shape[1]:
            margin = np.asarray(e @ member["eff_coef"], dtype=np.float32)[:, member["classes_present"]]
        elif member["eff_coef"] is not None:
            margin = np.zeros((pack["n"], len(member["classes_present"])), dtype=np.float32)           # the league-average player
        return tree.predict_proba(pack["x"], base_margin=margin)
