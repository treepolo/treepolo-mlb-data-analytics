"""Multinomial logistic regression with the situation interactions, optional player effects (each group with its own shrinkage), descriptors and pitch location.

Two ways to solve the same model:

* ``grouped`` (the default whenever it applies): ``fastglm.GroupedSoftmax``. Identical situations are merged, each effect group is solved unit by unit with exact Newton steps
  (the 'within' effects are fixed effects, handled the way ``fixest`` handles them: every unit is compared with itself), the shrinkage is estimated from the data while the sweeps run,
  and the work goes to the GPU when there is one (PyTorch). The bagged members that give the intervals are game-level bootstraps that start from the solution of the full fit and run until
  they have converged by the same criterion as the full fit (the sweeps of each member and whether it converged are in ``info["member_fits"]``); a fit that follows another (the unseen-player folds) starts from it and keeps its shrinkage. Needs PyTorch.
* ``lbfgs``: the original solver, everything in one L-BFGS on a sparse design matrix (``softmax.fit_softmax``). It is the only one that can do the descriptor controls
  (control_pitcher / control_batter = descriptors) and the pitch location, and it is what the grouped solver is checked against in the tests.
"""
from __future__ import annotations

import time
from typing import Any

import numpy as np
from scipy import sparse

from .. import gbm
from .. import league_core as lc
from . import controls as ctl
from . import design as dz
from .base import OutcomeModel
from .shrinkage import estimated_penalties_for, fit_with_estimated_penalties
from .softmax import fit_softmax, softmax_rows



class LogisticModel(OutcomeModel):
    name = "logistic"
    label_zh = "邏輯迴歸"
    label_en = "Logistic regression"
    uses_design = True
    cost_rule = "grouped"
    probe_settings = {"bagging_reps": 1}
    probe_unit = 0
    supports = {name: "效應直接放進多類別邏輯迴歸（逐組、逐單位解；固定效應用同組內比較）" for name in ctl.CONTROL_NAMES}

    # ------------------------------------------------------------------------------------------------ which solver
    def _grouped(self) -> bool:
        from . import fastglm

        return self.settings.get("solver", "auto") != "lbfgs" and self.profile is None and self.pool is None and fastglm.torch_available()

    def _training_design(self, sub, s):
        if self._grouped():
            return None, None, None                                  # the grouped solver builds what it needs from the rows
        return super()._training_design(sub, s)

    # ------------------------------------------------------------------------------------------------ legacy (L-BFGS) member
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

    # ------------------------------------------------------------------------------------------------ grouped solver
    def _fit_members(self, sub, y, vsub, vy, s, progress, design, reference=None):
        if not self._grouped():
            return super()._fit_members(sub, y, vsub, vy, s, progress, design, reference)
        from . import fastglm

        device = fastglm.resolve_device(s["device"])
        try:
            return self._fit_members_grouped(sub, y, s, progress, reference, device)
        except RuntimeError as exc:
            if device == "cuda" and "out of memory" in str(exc).lower():
                import torch

                torch.cuda.empty_cache()
                return self._fit_members_grouped(sub, y, s, progress, reference, "cpu")           # not enough GPU memory: the same work on the CPU
            raise

    def _group_specs(self, sub, within_flags):
        from . import fastglm

        D_int = self._dense_width()
        specs = []
        for g, within in self.groups.items():
            index = self.indexes[g]
            unit = index.lookup(dz.effect_keys(sub, g))
            keys = index.keys
            parents: list[tuple[str, np.ndarray]] = []

            def parent_group(name, mapped):
                if name in self.indexes:
                    parents.append((name, self.indexes[name].lookup(mapped)))

            typ_col = dz.SIT_OFFSETS["type"]
            if g in ("pitcher", "batter"):
                parents.append(("dense", np.full(len(keys), D_int, dtype=np.int64)))                          # the mean goes to the intercept
            elif g in ("pitcher_pitch", "batter_pitch"):
                parent_group("pitcher" if g == "pitcher_pitch" else "batter", keys // 16)
                parents.append(("dense", typ_col + keys % 16))
            elif g == "pitcher_game":
                parent_group("pitcher", keys % dz.KEY_BASE)
            elif g == "pitcher_game_pitch":
                parent_group("pitcher_game", keys // 16)
                parents.append(("dense", typ_col + keys % 16))
            elif g == "batter_count":
                parent_group("batter", keys // 16)
                parents.append(("dense", dz.SIT_OFFSETS["count"] + keys % 16))
            specs.append(fastglm.Group(g, unit, len(keys), within, parents))
        return specs

    @staticmethod
    def _dense_width() -> int:
        return dz.SIT_WIDTH

    def _reference_start(self, reference):
        """Dense table, intercept, effect coefficients and shrinkage of ``reference`` (a model fitted before on more data), laid over the units of this fit; None if it does not fit."""

        if reference is None or not getattr(reference, "members", None) or reference.name not in ("logistic", "mixed") or reference.classes != self.classes:
            return None
        member = reference.members[0]
        if "n_dense" not in member or int(member["n_dense"]) != dz.SIT_WIDTH:
            return None
        coef = np.asarray(member["coef"], dtype=np.float64)
        nd = int(member["n_dense"])
        effects, start = {}, nd
        for g in reference.groups:
            size = len(reference.indexes[g])
            if g in self.groups:
                pos = reference.indexes[g].lookup(self.indexes[g].keys)
                block = np.zeros((len(self.indexes[g]), coef.shape[1]))
                ok = pos >= 0
                block[ok] = coef[start + pos[ok]]
                effects[g] = block
            start += size
        penalties = (reference.info.get("penalties_by_class") or {})
        return {"dense": coef[:nd], "intercept": np.asarray(member["intercept"], dtype=np.float64), "effects": effects,
                "penalties": {g: np.asarray(v, dtype=float) for g, v in penalties.items() if g in self.groups},
                "eb": (np.asarray(penalties["count_pitch"], dtype=float) if "count_pitch" in penalties else None)}

    def _fit_members_grouped(self, sub, y, s, progress, reference, device):
        from . import fastglm

        K = len(self.classes)
        started = time.monotonic()
        key = dz.situation_key(sub)
        _, first, cell = np.unique(key, return_index=True, return_inverse=True)
        sit_idx = dz.situation_indices(lc.take(sub, first))
        S = len(first)
        games = np.asarray(sub["game_pk"])
        n = len(y)
        has_groups = bool(self.groups)
        if has_groups:
            counts = np.zeros((n, K)); counts[np.arange(n), y] = 1.0
            solver_cell = cell
            specs = self._group_specs(sub, self.groups)
        else:
            counts = np.bincount(cell * K + y, minlength=S * K).reshape(S, K).astype(np.float64)
            solver_cell = np.arange(S)
            specs = []
        eb_cols = (dz.SIT_OFFSETS["count*type"] + np.arange(12 * dz.N_TYPES)) if s.get("control_count_pitch", "effect") == "effect" else None        # the count x pitch-type interactions: shrunk by the data
        seen = np.bincount(dz.count_cell(sub) * dz.N_TYPES + np.maximum(dz.type_index(sub["pitch_type"]), 0), minlength=12 * dz.N_TYPES) > 0                 # the count x pitch-type cells that have pitches
        make = lambda cnt: fastglm.GroupedSoftmax(sit_idx=sit_idx, n_dense=dz.SIT_WIDTH, l2=float(s["l2"]), cell=solver_cell, counts=cnt, groups=specs, device=device, shrink_cols=eb_cols,
                                                  shrink_grid=(12, dz.N_TYPES), shrink_present=seen)
        solver = make(counts)
        start = self._reference_start(reference) if has_groups or reference is not None else None
        estimate = bool(((has_groups and any(not w for w in self.groups.values())) or eb_cols is not None) and (self.force_auto or s.get("effect_penalty_mode", "auto") == "auto"))
        if start is not None:
            solver.load_numpy(start["dense"], start["intercept"], start["effects"])
            if estimate and all(g in start["penalties"] for g, w in self.groups.items() if not w) and (eb_cols is None or start["eb"] is not None):
                for g, lam in start["penalties"].items():                                  # a fit that follows another keeps its shrinkage
                    if not self.groups.get(g, True):
                        solver.lam[g] = solver.torch.as_tensor(lam, dtype=solver.torch.float64, device=solver.dev)
                if eb_cols is not None:
                    solver.lam_eb = solver.torch.as_tensor(start["eb"], dtype=solver.torch.float64, device=solver.dev)
                estimate = False
        if not estimate:
            for g, within in self.groups.items():
                if not within and not (start is not None and g in start["penalties"]):
                    solver.lam[g] = solver.torch.full((K,), float(s[f"effect_penalty_{g}"]), dtype=solver.torch.float64, device=solver.dev)
            if eb_cols is not None and not (start is not None and start["eb"] is not None):
                solver.lam_eb = solver.torch.full((K,), float(s.get("penalty_count_pitch", 50.0)), dtype=solver.torch.float64, device=solver.dev)
        cap = int(s["max_iter"])
        follow = start is not None
        tol = 1e-5 if follow else 1e-6                      # the convergence criterion of the main fit, which every bootstrap member uses as well
        main = solver.fit(max_sweeps=cap, tol=tol, estimate=estimate, progress=(lambda i, c: progress("outcome_model", None, f"sweep {i}") if progress else None))
        solver.center()
        members, infos = [], []
        reps = int(s["bagging_reps"])
        base_state = solver.get_state()
        exp = solver.export()
        chosen = {g: exp["penalties"][g] for g in self.groups}
        est = estimated_penalties_for(self.groups, chosen) if (estimate or (start is not None and has_groups and any(g in start["penalties"] for g in self.groups))) else None
        if est is not None and eb_cols is not None:
            est["count_pitch"] = np.asarray(exp["penalties"]["count_pitch"], dtype=float)                 # what the data chose for the count x pitch-type interactions, per outcome class

        def member_of(e):
            parts = [e["dense"]] + [e["effects"][g] for g in self.groups]
            return {"coef": np.concatenate(parts, axis=0).astype(np.float32), "intercept": e["intercept"].astype(np.float64), "n_dense": int(dz.SIT_WIDTH)}

        main_fit = {"sweeps": int(main["sweeps"]), "hit_cap": bool(main["hit_cap"]), "seconds": float(main["seconds"])}
        if reps <= 1:
            members.append(member_of(exp))
            infos.append({"n_iter": main["sweeps"], "hit_cap": main["hit_cap"], "seconds": time.monotonic() - started, "own_seconds": float(main["seconds"]), "trace": main["trace"],
                          "estimated_penalties": est, "main": main_fit})
        else:
            rng = np.random.default_rng(int(s["seed"]))
            for r in range(reps):
                w = gbm.poisson_game_weights(games, rng)
                if has_groups:
                    solver.set_weights(w)
                else:
                    cnt = np.bincount(cell * K + y, weights=w, minlength=S * K).reshape(S, K)
                    solver = make(cnt)
                solver.set_state(base_state)
                if not has_groups:
                    solver.load_numpy(exp["dense"], exp["intercept"], {})
                run = solver.fit(max_sweeps=cap, tol=tol, estimate=False)          # a bootstrap member starts from the full fit and runs until it has converged by the same criterion as the full fit
                solver.center()
                members.append(member_of(solver.export()))
                infos.append({"n_iter": run["sweeps"], "hit_cap": bool(run["hit_cap"]), "seconds": run["seconds"] + (main["seconds"] if r == 0 else 0.0), "own_seconds": float(run["seconds"]),
                              "trace": main["trace"] if r == 0 else [], "estimated_penalties": est if r == 0 else None, "main": main_fit if r == 0 else None})
                if progress:
                    progress("outcome_model", (r + 1) / reps, f"member {r + 1}/{reps}")
        return members, infos

    # ------------------------------------------------------------------------------------------------ prediction
    def _member_proba(self, member: dict[str, Any], pack: dict[str, Any], effects: str) -> np.ndarray:
        nd = member["n_dense"]
        z = pack["D"] @ member["coef"][:nd] + member["intercept"]
        e = pack.get("E")
        if effects == "specific" and e is not None and e.shape[1]:
            z = z + e @ member["coef"][nd:]
        return softmax_rows(np.asarray(z, dtype=np.float64))
