"""Grouped multinomial logistic regression, solved group by group (PyTorch, GPU when there is one, otherwise the CPU).

The model is the same as the one ``softmax.fit_softmax`` fits with L-BFGS: a softmax over the outcome classes whose logits are

    eta[i, k] = intercept[k] + dense[situation(i), k] + sum_g effect_g[unit_g(i), k]

with a ridge penalty ``l2`` on the dense columns (the situation block, 293 one-hot columns), and for every effect group g and class k a penalty ``lam[g, k]`` (the precision of a
normal prior, or the tiny 'within' value that leaves the effects practically unshrunk). What changes is how it is solved.

* Rows with the same situation are merged (``cell``): the dense part lives on the distinct situations, not on the pitches.
* The effect groups are not put into one big optimisation. Each group has one small problem per unit (a pitcher, a pitcher-game ...): a Newton step with the exact K x K
  Hessian of that unit, all units of a group at once, with a per-unit step-halving check. A sweep updates the dense block (one Newton step on the merged situations, the Hessian
  refreshed every few sweeps) and then every group in turn, coarse before fine (pitcher before pitcher-game); this is block-coordinate descent on the same convex objective.
  For the 'within' (fixed) effects this does what the weighted de-meaning of ``fixest::feglm`` does: each unit is compared with itself, solved exactly, instead of thousands of
  columns going through a general-purpose optimiser. The multi-class form is the baseline-free softmax with exact K x K blocks (not one binary model per class).
* The shrinkage of the effects that are not 'within' is estimated from the data while the sweeps run (empirical Bayes: the variance of the effects of a group and class is the mean of
  effect^2 + posterior variance, the posterior variance from the unit's own Hessian), then the sweeps go on with the penalties fixed until the logits stop moving.
* At the end every effect group is centred (information-weighted mean 0 inside its parent: the pitcher effects around the intercept, the pitcher-and-pitch effects around the pitcher
  and the pitch type, ...) and then calibrated: the means moved into the parents are chosen so that the league-average player (all effects 0) reproduces the observed share of every
  outcome overall, by pitch type and by count (``calibrate``). The logits of every pitch do not change, so predictions for named players are the same. (A group of nearly unpenalised
  effects otherwise keeps the level of a pitch type for itself, and the mean of its logits is far from the logit of the mean for the rare outcomes.)
"""
from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

import numpy as np

from ..resource_guard import guard

VAR_MIN, VAR_MAX = 1e-4, 100.0
START_PENALTY = 1.0


def dz_offsets() -> tuple[int, int]:
    """First column of the pitch-type block and of the count block of the situation design."""

    from . import design as dz

    return dz.SIT_OFFSETS["type"], dz.SIT_OFFSETS["count"]


def torch_cat(parts):
    import torch

    return torch.cat(parts, dim=0)


def torch_available() -> bool:
    try:
        import torch  # noqa: F401
    except Exception:
        return False
    return True


def resolve_device(requested: str = "auto") -> str:
    """The graphics card when it may be used right now (present, and free memory beyond what the display needs), else the CPU."""

    return guard.device(requested)


class Group:
    """One effect group: which unit every row belongs to, whether it is 'within' (not shrunk), and how to centre it."""

    def __init__(self, name: str, unit: np.ndarray, size: int, within: bool, parents: Sequence[tuple[str, np.ndarray]] = ()):
        self.name, self.unit, self.size, self.within = name, np.asarray(unit, dtype=np.int64), int(size), bool(within)
        self.parents = list(parents)         # (parent group name or "dense", for every unit of this group the unit / the dense column of the parent; -1 = none)


class GroupedSoftmax:
    def __init__(self, *, sit_idx: np.ndarray, n_dense: int, l2: float, cell: np.ndarray, counts: np.ndarray, groups: Sequence[Group], device: str = "cpu"):
        import torch

        self.torch = torch
        self.dev = torch.device(device)
        f64 = torch.float64
        self.D = int(n_dense)
        self.K = int(counts.shape[1])
        self.l2 = float(l2)
        S = sit_idx.shape[0]
        ext = np.concatenate([np.asarray(sit_idx, dtype=np.int64), np.full((S, 1), self.D, dtype=np.int64)], axis=1)       # the intercept is one more "block" with the single column D
        self.sit = torch.as_tensor(ext, device=self.dev)
        self.S, self.B = S, ext.shape[1]
        self.cell = torch.as_tensor(np.asarray(cell, dtype=np.int64), device=self.dev)
        self.Y0 = torch.as_tensor(np.asarray(counts, dtype=np.float64), device=self.dev, dtype=f64)         # [N, K] class counts of every row (the weight of the row is their sum)
        self.N = self.Y0.shape[0]
        self.groups = list(groups)
        self.unit = {g.name: torch.as_tensor(g.unit, device=self.dev) for g in self.groups}
        self.size = {g.name: g.size for g in self.groups}
        self.within = {g.name: g.within for g in self.groups}
        self.ia, self.ib = (t.to(self.dev) for t in torch.triu_indices(self.K, self.K))
        self.diag_pos = (self.ia == self.ib).nonzero().squeeze(1)
        self.l2vec = torch.full((self.D + 1, 1), self.l2, dtype=f64, device=self.dev); self.l2vec[self.D] = 0.0
        self._counts = {g.name: self.Y0.new_zeros((g.size,)).index_add_(0, self.unit[g.name], self.Y0.sum(dim=1)) for g in self.groups}
        self._parent_maps = {g.name: [(p, torch.as_tensor(m, device=self.dev)) for p, m in g.parents] for g in self.groups}
        self._plan_absorption()
        self.set_weights(None)
        self.reset()
        self._chol = None

    def _plan_absorption(self) -> None:
        """What a 'within' group (nearly unpenalised effects, one per unit) makes redundant.

        The units of such a group span everything that is constant inside a unit: the intercept, the pitch-type columns, the effects of the groups it is nested in. With a penalty of 1e-4
        they take the whole level, and what they span in the parents is pinned at 0 by the penalties of the parents (that is the optimum of the original problem). So a parent that is not
        'within' itself is frozen at 0 during the sweeps (the intercept and the dense columns included), exactly as ``fixest`` absorbs what a fixed effect spans; the means are given
        back to the parents when the fit is finished (``center``). A parent that IS 'within' would share the level with its child along a nearly flat direction; there the child is kept
        centred inside the parent during the sweeps (the logits do not change, the level just lives in the parent)."""

        by_name = {g.name: g for g in self.groups}
        self.frozen_groups: set[str] = set()
        frozen_cols: set[int] = set()
        self.relations: list[tuple[str, str, Any]] = []
        for g in self.groups:
            if not g.within:
                continue
            stack = [(g, np.arange(g.size))]
            seen = set()
            while stack:
                h, comp = stack.pop()
                for pname, pmap in h.parents:
                    pmap = np.asarray(pmap)
                    composed = np.where(comp >= 0, pmap[np.clip(comp, 0, max(len(pmap) - 1, 0))], -1) if len(pmap) else np.full(len(comp), -1)
                    if pname == "dense":
                        frozen_cols.update(int(c) for c in np.unique(composed[composed >= 0]))
                    elif pname in by_name:
                        parent = by_name[pname]
                        if parent.within:
                            self.relations.append((g.name, pname, self.torch.as_tensor(composed, device=self.dev)))
                        elif (g.name, pname) not in seen:
                            seen.add((g.name, pname))
                            self.frozen_groups.add(pname)
                            stack.append((parent, composed))
        for c in frozen_cols:
            self.l2vec[c] = 1e12
        self.frozen_cols = sorted(frozen_cols)

    def _recenter_within(self, rounds: int = 3) -> None:
        """Keep every 'within' group centred inside the 'within' group it is nested in (the logits do not change)."""

        torch = self.torch
        for child, parent, mapping in self.relations:
            th = self.th[child]; n = self._counts[child]
            ok = mapping >= 0
            if not bool(ok.any()):
                continue
            size = self.size[parent]
            num = th.new_zeros((size, self.K)).index_add_(0, mapping[ok], th[ok] * n[ok][:, None])
            den = th.new_zeros((size,)).index_add_(0, mapping[ok], n[ok])
            mean = num / den.clamp_min(1e-12)[:, None]
            th = th.clone(); th[ok] = th[ok] - mean[mapping[ok]]
            self.th[child] = th
            self.th[parent] = self.th[parent] + mean * (den > 0).to(mean.dtype)[:, None]

    # ------------------------------------------------------------------------------------------------ state
    def reset(self) -> None:
        torch = self.torch
        f64 = torch.float64
        self.Bt = torch.zeros((self.D + 1, self.K), dtype=f64, device=self.dev)
        share = (self.Y0.sum(dim=0) / self.Y0.sum()).clamp_min(1e-9)
        self.Bt[self.D] = 0.0 if self.D in self.frozen_cols else torch.log(share)
        self.th = {g.name: torch.zeros((g.size, self.K), dtype=f64, device=self.dev) for g in self.groups}
        self.lam = {g.name: torch.full((self.K,), 1e-4 if g.within else (1e12 if g.name in self.frozen_groups else START_PENALTY), dtype=f64, device=self.dev) for g in self.groups}
        self._chol = None

    def set_weights(self, row_weight: np.ndarray | None) -> None:
        """Weight every row (a bootstrap); None = the counts as they are."""

        torch = self.torch
        if row_weight is None:
            self.Y = self.Y0
        else:
            self.Y = self.Y0 * torch.as_tensor(np.asarray(row_weight, dtype=np.float64), device=self.dev)[:, None]
        self.W = self.Y.sum(dim=1)
        self._chol = None

    def get_state(self) -> dict[str, Any]:
        return {"Bt": self.Bt.clone(), "th": {k: v.clone() for k, v in self.th.items()}, "lam": {k: v.clone() for k, v in self.lam.items()}}

    def set_state(self, state: Mapping[str, Any]) -> None:
        self.Bt = state["Bt"].clone()
        self.th = {k: v.clone() for k, v in state["th"].items()}
        self.lam = {k: v.clone() for k, v in state["lam"].items()}
        self._chol = None

    def load_numpy(self, dense: np.ndarray, intercept: np.ndarray, effects: Mapping[str, np.ndarray]) -> None:
        """Start from coefficients (a warm start): the dense table, the intercept and the effect coefficients of the groups it has (a group or a unit it lacks starts at 0)."""

        torch = self.torch
        self.Bt[: self.D] = torch.as_tensor(np.asarray(dense, dtype=np.float64), device=self.dev)
        self.Bt[self.D] = torch.as_tensor(np.asarray(intercept, dtype=np.float64), device=self.dev)
        for g in self.groups:
            if g.name in effects:
                self.th[g.name] = torch.as_tensor(np.asarray(effects[g.name], dtype=np.float64), device=self.dev).clone()
        self._chol = None

    # ------------------------------------------------------------------------------------------------ pieces
    def _zc(self, Bt):
        return Bt[self.sit].sum(dim=1)                                    # [S, K] dense part of the logits of every situation

    def _eta(self):
        eta = self._zc(self.Bt)[self.cell]
        for g in self.groups:
            eta = eta + self.th[g.name][self.unit[g.name]]
        return eta

    def _nll(self, logp):
        return -(self.Y * logp).sum()

    def _penalty(self):
        pen = 0.5 * self.l2 * (self.Bt[: self.D] ** 2).sum()
        for g in self.groups:
            pen = pen + 0.5 * (self.lam[g.name][None, :] * self.th[g.name] ** 2).sum()
        return pen

    def objective(self) -> float:
        torch = self.torch
        logp = torch.log_softmax(self._eta(), dim=1)
        return float(self._nll(logp) + self._penalty())

    def _tm(self):
        """The torch module when the work runs on the card (then it is cut into short pieces with rests), else None."""

        return self.torch if self.dev.type == "cuda" else None

    def _scatter(self, index, src, size):
        """``zeros(size, cols).index_add_(0, index, src)`` in short pieces: the pieces are sized from the memory free now and adapted so that one call to the card is brief."""

        out = src.new_zeros((size, src.shape[1]))
        for sl in guard.gpu_chunks(len(index), bytes_per_row=src.shape[1] * 8 * 3, torch_module=self._tm()):
            out.index_add_(0, index[sl], src[sl])
        return out

    def _scatter_hess(self, index, P, W, size):
        """Sum over the rows of a unit of the K(K+1)/2 entries of the Hessian of every row, in short pieces (the entries are made piece by piece, so they never exist for all rows at once)."""

        out = P.new_zeros((size, self.K * (self.K + 1) // 2))
        for sl in guard.gpu_chunks(len(index), bytes_per_row=self.K * (self.K + 1) // 2 * 8 * 4, torch_module=self._tm()):
            out.index_add_(0, index[sl], self._hess_rows(P[sl], W[sl]))
        return out

    def _batched(self, fn, *mats):
        """``fn`` on the first dimension of ``mats``, in short pieces (batched solves and inverses of many small matrices)."""

        n = mats[0].shape[0]
        outs = []
        for sl in guard.gpu_chunks(n, bytes_per_row=self.K * self.K * 8 * 6, torch_module=self._tm()):
            outs.append(fn(*[m[sl] for m in mats]))
        return torch_cat(outs) if outs else fn(*mats)

    def _hess_rows(self, P, W=None):
        """[N, K(K+1)/2] upper triangle of W * (diag(P) - P P^T) for every row."""

        W = self.W if W is None else W
        H = -(P[:, self.ia] * P[:, self.ib]) * W[:, None]
        H[:, self.diag_pos] += P[:, self.ia[self.diag_pos]] * W[:, None]
        return H

    def _full_from_tri(self, H36):
        out = H36.new_zeros((H36.shape[0], self.K, self.K))
        out[:, self.ia, self.ib] = H36
        out[:, self.ib, self.ia] = H36
        return out

    # ------------------------------------------------------------------------------------------------ dense block
    def _dense_step(self, eta, logp, P, refresh: bool) -> float:
        torch = self.torch
        K, D1 = self.K, self.D + 1
        r = self.Y - self.W[:, None] * P
        Rc = self._scatter(self.cell, r, self.S)
        g = Rc.new_zeros((D1, K))
        for b in range(self.B):
            g += self._scatter(self.sit[:, b], Rc, D1)
        g = g - self.l2vec * self.Bt
        if refresh or self._chol is None:
            Hc = self._scatter_hess(self.cell, P, self.W, self.S)
            Hc = self._full_from_tri(Hc).reshape(self.S, K * K)
            acc = Hc.new_zeros((D1 * D1, K * K))
            for b1 in range(self.B):
                for b2 in range(self.B):
                    idx = self.sit[:, b1] * D1 + self.sit[:, b2]
                    for sl in guard.gpu_chunks(self.S, bytes_per_row=K * K * 8 * 2, torch_module=self._tm()):
                        acc.index_add_(0, idx[sl], Hc[sl])
                guard.checkpoint("dense Hessian")
            M = acc.reshape(D1, D1, K, K).permute(0, 2, 1, 3).reshape(D1 * K, D1 * K)
            ridge = (self.l2vec.expand(D1, K).reshape(-1)).clone()
            jitter = 1e-6 * float(M.diagonal().mean()) + 1e-9
            M = M + torch.diag(ridge + jitter)
            L, info = torch.linalg.cholesky_ex(M)
            while int(info) != 0:
                jitter *= 10
                M = M + torch.eye(D1 * K, dtype=M.dtype, device=M.device) * jitter
                L, info = torch.linalg.cholesky_ex(M)
            self._chol = L
        delta = torch.cholesky_solve(g.reshape(-1, 1), self._chol).reshape(D1, K)
        if self.frozen_cols:
            delta[self.frozen_cols] = 0.0                                          # absorbed by a within group: stays at 0 during the sweeps
        f0 = float(self._nll(logp) + 0.5 * self.l2 * (self.Bt[: self.D] ** 2).sum())
        zc0 = self._zc(self.Bt)
        step = 1.0
        for _ in range(6):
            Bn = self.Bt + step * delta
            eta_n = eta + (self._zc(Bn) - zc0)[self.cell]
            f1 = float(self._nll(torch.log_softmax(eta_n, dim=1)) + 0.5 * self.l2 * (Bn[: self.D] ** 2).sum())
            if f1 <= f0 + 1e-9 * abs(f0):
                self.Bt = Bn
                return step
            step *= 0.5
            self._chol = None                                              # the curvature was off: recompute it next sweep
        return 0.0

    # ------------------------------------------------------------------------------------------------ one effect group
    def _group_step(self, g: Group, eta, want_variance: bool, inner: int = 30, unit_tol: float = 1e-6):
        """Solve every unit of the group (given the logits of everything else) by Newton steps with the exact K x K Hessian of the unit and a per-unit step-halving check, repeated for the
        units that are still moving (a unit that has no pitch of an outcome class drifts a long way when it is nearly unpenalised; one Newton step does not get there). Returns the new
        logits, the posterior variances of the effects (from the first Hessian) when asked, and the largest step."""

        torch = self.torch
        K, U = self.K, g.size
        unit_all = self.unit[g.name]; lam = self.lam[g.name]
        th = self.th[g.name].clone()
        active = torch.ones((U,), dtype=torch.bool, device=th.device)
        post, biggest = None, 0.0
        eta = eta.clone()
        for it in range(inner):
            if it == 0:
                idx = None; u_r = unit_all; Yr = self.Y; Wr = self.W; er = eta
                act = torch.arange(U, device=th.device); Ua = U
            else:
                idx = torch.nonzero(active[unit_all]).squeeze(1)
                act = torch.nonzero(active).squeeze(1)
                Ua = int(act.numel())
                if Ua == 0:
                    break
                compact = torch.cumsum(active.to(torch.int64), 0) - 1
                u_r = compact[unit_all[idx]]; Yr = self.Y[idx]; Wr = self.W[idx]; er = eta[idx]
            logp = torch.log_softmax(er, dim=1); P = torch.exp(logp)
            r = Yr - Wr[:, None] * P
            R = self._scatter(u_r, r, Ua)
            H36 = self._scatter_hess(u_r, P, Wr, Ua)
            Hm = self._full_from_tri(H36) + torch.diag(lam)[None, :, :]
            tha = th[act]
            G = R - lam[None, :] * tha
            delta = self._batched(lambda a, b: torch.linalg.solve(a, b.unsqueeze(-1)).squeeze(-1), Hm, G)
            if it == 0 and want_variance:
                post = self._batched(lambda a: torch.diagonal(torch.linalg.inv(a), dim1=-2, dim2=-1), Hm)       # posterior variance of every effect (given the others)
            nll0 = -(Yr * logp).sum(dim=1)
            f0 = self._scatter(u_r, nll0[:, None], Ua)[:, 0] + 0.5 * (lam[None, :] * tha * tha).sum(dim=1)
            t = torch.ones((Ua,), dtype=th.dtype, device=th.device)
            todo = torch.ones((Ua,), dtype=torch.bool, device=th.device)
            final = torch.zeros((Ua,), dtype=th.dtype, device=th.device)
            for _ in range(8):
                tn = t[:, None] * delta
                logp_n = torch.log_softmax(er + tn[u_r], dim=1)
                f1 = self._scatter(u_r, (-(Yr * logp_n).sum(dim=1))[:, None], Ua)[:, 0] + 0.5 * (lam[None, :] * (tha + tn) ** 2).sum(dim=1)
                ok = todo & (f1 <= f0 + 1e-12 * (1.0 + f0.abs()))
                final = torch.where(ok, t, final)
                todo = todo & ~ok
                if not bool(todo.any()):
                    break
                t = torch.where(todo, t * 0.5, t)
            step = final[:, None] * delta
            th[act] = tha + step
            if idx is None:
                eta = eta + step[unit_all]
            else:
                eta[idx] = er + step[u_r]
            size = step.abs().max(dim=1).values
            biggest = max(biggest, float(size.max())) if Ua else biggest
            done = (size < unit_tol) | (final == 0)
            active[act[done]] = False
            if not bool(active.any()):
                break
        self.th[g.name] = th
        return eta, post, biggest

    # ------------------------------------------------------------------------------------------------ the sweeps
    def _sweep(self, number: int, hess_every: int, want_variance: bool):
        """One sweep (dense block, then every group). Returns the largest change of a logit and, when asked, the posterior variances of the groups that are not 'within'."""

        torch = self.torch
        eta = self._eta(); logp = torch.log_softmax(eta, dim=1); P = torch.exp(logp)
        eta_start = eta.clone()
        self._dense_step(eta, logp, P, (not self.groups) or number <= 3 or number % hess_every == 0)
        eta = self._eta()
        variances: dict[str, Any] = {}
        for g in self.groups:
            if g.name in self.frozen_groups:
                continue
            eta, post, _ = self._group_step(g, eta, want_variance=want_variance and not g.within)
            if post is not None:
                variances[g.name] = post
        if self.relations:
            self._recenter_within()
        # how far the predictions moved (probabilities, not logits: a unit that has only ever seen one outcome keeps drifting towards a probability of 1, which changes nothing)
        return float((torch.softmax(eta, dim=1) - torch.softmax(eta_start, dim=1)).abs().max()), variances

    def _flat(self):
        return self.torch.cat([self.Bt.reshape(-1)] + [self.th[g.name].reshape(-1) for g in self.groups])

    def _unflat(self, v) -> None:
        n = self.Bt.numel()
        self.Bt = v[:n].reshape(self.Bt.shape).clone()
        for g in self.groups:
            m = self.th[g.name].numel()
            self.th[g.name] = v[n:n + m].reshape(self.th[g.name].shape).clone(); n += m

    def fit(self, *, max_sweeps: int = 1000, tol: float = 1e-5, estimate: bool = True, tol_lam: float = 0.02, hess_every: int = 4, memory: int = 6, progress=None) -> dict[str, Any]:
        """Sweep until no predicted probability moves by more than ``tol`` in a sweep.

        With ``estimate`` the penalties of the groups that are not 'within' are re-estimated after every sweep (from the second on) until they stop changing (``tol_lam``, in log units);
        then the penalties are fixed. The sweeps with fixed penalties are accelerated by Anderson extrapolation of the whole parameter vector (block-coordinate descent alone creeps
        when the dense block and an effect group are correlated, as pitcher quality and pitch type are); an extrapolated point is used only when its objective is lower than the
        plain sweep's. Returns the number of sweeps, whether the cap was hit, the seconds, the objective trace and the change after every sweep."""

        torch = self.torch
        started = time.monotonic()
        trace: list[tuple[int, float, None]] = []
        changes: list[float] = []
        em = bool(estimate and any((not g.within) and g.name not in self.frozen_groups for g in self.groups))
        sweeps, converged = 0, False
        lam_stable = not em
        while sweeps < int(max_sweeps) and not lam_stable:                    # phase A: plain sweeps while the penalties are still moving
            sweeps += 1
            guard.checkpoint("sweep")
            change, variances = self._sweep(sweeps, hess_every, want_variance=sweeps >= 2)
            worst = 0.0
            for name, post in variances.items():
                var = ((self.th[name] ** 2 + post).mean(dim=0)).clamp(VAR_MIN, VAR_MAX)
                new = 1.0 / var
                worst = max(worst, float((new / self.lam[name]).log().abs().max()))
                self.lam[name] = new
            if variances and worst < tol_lam:
                lam_stable = True
            changes.append(change)
            if sweeps % 10 == 0 or sweeps == 1:
                trace.append((sweeps, self.objective() / max(float(self.W.sum()), 1.0), None))
            if progress:
                progress(sweeps, change)
        xs: list[Any] = []; rs: list[Any] = []
        x = self._flat()
        while sweeps < int(max_sweeps):                                       # phase B: penalties fixed, accelerated sweeps
            sweeps += 1
            guard.checkpoint("sweep")
            change, _ = self._sweep(sweeps, hess_every, want_variance=False)
            changes.append(change)
            if sweeps % 10 == 0 or sweeps == 1:
                trace.append((sweeps, self.objective() / max(float(self.W.sum()), 1.0), None))
            if progress:
                progress(sweeps, change)
            if change < tol and sweeps >= 2:
                converged = True
                break
            xg = self._flat()
            r = xg - x
            xs.append(x); rs.append(r)
            if len(rs) > memory + 1:
                xs.pop(0); rs.pop(0)
            nxt = xg
            if len(rs) >= 2 and self.groups:
                dX = torch.stack([xs[j + 1] - xs[j] for j in range(len(xs) - 1)], dim=1)
                dR = torch.stack([rs[j + 1] - rs[j] for j in range(len(rs) - 1)], dim=1)
                gram = dR.T @ dR
                gamma = torch.linalg.solve(gram + 1e-12 * float(gram.diagonal().mean()) * torch.eye(gram.shape[0], dtype=gram.dtype, device=gram.device), dR.T @ rs[-1])
                cand = xg - (dX + dR) @ gamma
                f_plain = self.objective()
                self._unflat(cand)
                if self.objective() <= f_plain:
                    nxt = cand
                else:
                    self._unflat(xg); xs.clear(); rs.clear()              # the extrapolation did not help: start the history again
            x = nxt
        return {"sweeps": sweeps, "hit_cap": not converged, "seconds": time.monotonic() - started, "trace": trace, "objective": self.objective(), "changes": changes}

    # ------------------------------------------------------------------------------------------------ centring and export
    def center(self, rounds: int = 30) -> None:
        """Move the mean of every effect group into its parent (the intercept, the dense columns, the group it is nested in); the logits of every row stay the same.

        The mean is weighted by the information a unit carries about each outcome class (its pitches times p(1 - p) of that class): a unit that has only ever seen one outcome tells
        nothing about the other classes and a nearly unpenalised effect of such a unit runs far away, so it must not decide the level. After this the league-average player (every
        effect 0) is the information-weighted average one."""

        torch = self.torch
        P = torch.softmax(self._eta(), dim=1)
        info_row = self.W[:, None] * P * (1.0 - P)
        weight = {g.name: info_row.new_zeros((g.size, self.K)).index_add_(0, self.unit[g.name], info_row) + 1e-9 for g in self.groups}
        for g in reversed(self.groups):                                        # fine groups first, so their means arrive in the coarse ones before those are centred
            th = self.th[g.name]; n = weight[g.name]
            for _ in range(rounds):
                moved = 0.0
                for pname, mapping in self._parent_maps[g.name]:
                    ok = mapping >= 0
                    if not bool(ok.any()):
                        continue
                    size = (self.D + 1 if pname == "dense" else self.size[pname])
                    num = th.new_zeros((size, self.K)).index_add_(0, mapping[ok], th[ok] * n[ok])
                    den = th.new_zeros((size, self.K)).index_add_(0, mapping[ok], n[ok])
                    mean = num / den.clamp_min(1e-12)
                    th = th.clone(); th[ok] = th[ok] - mean[mapping[ok]]
                    moved = max(moved, float(mean.abs().max()))
                    if pname == "dense":
                        self.Bt = self.Bt + mean
                    else:
                        self.th[pname] = self.th[pname] + mean
                if moved < 1e-12:
                    break
            self.th[g.name] = th
        self.calibrate()

    def calibrate(self, rounds: int = 12, eps: float = 0.5) -> None:
        """Make the league-average player (every effect 0) reproduce what happened, as probabilities.

        Centring the effects on the logit scale does not do it: when the effects are nearly unpenalised, a unit that never saw an outcome has an effect far below 0 for it (-9 to -17 logits
        for the rare outcomes), and the mean of such logits is not the logit of the mean probability. So the means that are moved from the effects into their parents are chosen so that the
        average predicted probability of every class equals the observed share overall, by pitch type (the parent of the pitcher-and-pitch effects) and by count (the parent of the
        batter-and-count effects): iterative proportional fitting on the logits, ``eps`` pitches of smoothing. A shift is added to the parent (the intercept, a pitch-type column, a count
        column) and taken off the units of the group nested in it, so the logits of every pitch, and with them every prediction for a named player, stay the same."""

        if not self.groups:
            return
        torch = self.torch
        K, S, D = self.K, self.S, self.D
        Wc = self._scatter(self.cell, self.W[:, None], S)[:, 0]
        Oc = self._scatter(self.cell, self.Y, S)
        type_off, count_off = dz_offsets()
        type_of_cell = self.sit[:, 5] - type_off                              # block 5 is the pitch type, block 0 the count (see design.SIT_BLOCKS)
        count_of_cell = self.sit[:, 0] - count_off
        families = []
        for name, lo, n, cell_stratum in (("intercept", D, 1, None), ("type", type_off, 9, type_of_cell), ("count", count_off, 12, count_of_cell)):
            for g in self.groups:
                found = None
                for pname, mapping in self._parent_maps[g.name]:
                    if pname != "dense" or not bool((mapping >= 0).all()):
                        continue
                    if (name == "intercept" and bool((mapping == D).all())) or (name != "intercept" and bool(((mapping >= lo) & (mapping < lo + n)).all())):
                        found = mapping - lo if name != "intercept" else torch.zeros_like(mapping)
                        break
                if found is not None:
                    cols = torch.tensor([D] if name == "intercept" else list(range(lo, lo + n)), device=self.dev)
                    families.append((cell_stratum if name != "intercept" else torch.zeros(S, dtype=torch.int64, device=self.dev), n, cols, g.name, found))
                    break
        for _ in range(rounds):
            biggest = 0.0
            for cell_stratum, n, cols, gname, unit_stratum in families:
                pc = torch.softmax(self._zc(self.Bt), dim=1)
                pred = pc.new_zeros((n, K)).index_add_(0, cell_stratum, Wc[:, None] * pc)
                obs = pc.new_zeros((n, K)).index_add_(0, cell_stratum, Oc)
                delta = torch.log(obs + eps) - torch.log(pred + eps)
                delta = delta - delta.mean(dim=1, keepdim=True)                # the same shift for every class changes nothing
                self.Bt[cols] = self.Bt[cols] + delta
                self.th[gname] = self.th[gname] - delta[unit_stratum]
                biggest = max(biggest, float(delta.abs().max()))
            guard.checkpoint("calibration")
            if biggest < 1e-4:
                break

    def export(self) -> dict[str, Any]:
        return {"dense": self.Bt[: self.D].cpu().numpy(), "intercept": self.Bt[self.D].cpu().numpy(), "effects": {k: v.cpu().numpy() for k, v in self.th.items()},
                "penalties": {k: v.cpu().numpy() for k, v in self.lam.items()}}
