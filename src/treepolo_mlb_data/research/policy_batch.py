"""The decision engine for many pitchers at once.

``policy_core.Engine`` solves one set of outcome probabilities at a time. The per-pitcher engines solve it once per pitcher, which repeats the same arrays and Bellman iterations hundreds of
times. ``BatchEngine`` does the same arithmetic for a batch of pitchers together (a leading batch axis on every array, torch on the graphics card when the resource guard allows it, else on the
CPU), with the same rules:

* every pitcher stops at his own convergence (same tolerance and the same test as ``Engine.solve``); a pitcher that has converged is no longer updated;
* the support rule, the states with nothing supported (they play the observed behaviour), the trust region and the tie-breaking are the ones of ``Engine``.

How many pitchers go into one batch is decided by the resource guard from the memory free at that moment (``Guard.batch_items``): the graphics memory above what the display needs when the card
is used, else the memory still free above the limit. Under pressure the batch is smaller; a batch that still does not fit (out of memory) is split and tried again; between iterations the guard
may pause the run (checkpoint) and, on the card, makes every call short and rests it (``gpu_pause``). Nothing here stops the run for lack of resources."""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

import numpy as np

from . import policy_core as pc
from .resource_guard import guard

C = pc.C


def _torch():
    import torch

    return torch


class BatchEngine:
    """The states, next states and terminal values are those of ``policy_core.Engine`` (they do not depend on the outcome probabilities); the arrays below are moved to a device once per use."""

    def __init__(self, shape: pc.Shape, env: pc.Environment, lam: float = 0.0, tol: float = 1e-13, max_iter: int = 3000):
        self.shape, self.env, self.tol, self.max_iter = shape, env, tol, max_iter
        nxt, term = pc.terminal_arrays(shape, env, lam)
        live = nxt >= 0
        self.outcomes = tuple(getattr(env, "outcomes", pc.OUTCOMES)); self.C = len(self.outcomes)
        self._np = {"live": live, "safe": np.where(live, nxt, 0), "term": term, "hr_term": pc.hr_terminal(shape, nxt, self.outcomes)}
        self._np["cont"] = self._one_step_continuation(live, term)
        self._start_idx = np.array([[shape.start_index(o, B) for B in range(8)] for o in range(3)]).reshape(-1)
        self._start_w = np.asarray(env.start, dtype=np.float64).reshape(-1)
        self.device = "cpu"
        self._t: dict[str, Any] = {}

    # ------------------------------------------------------------------------------------------------ setup
    def _one_step_continuation(self, live: np.ndarray, term: np.ndarray) -> np.ndarray:
        """The continuation of ``Engine.one_step`` (it does not depend on the probabilities)."""

        sh = self.shape; b, k, o, base, _, _ = sh.coords(); S = sh.size
        re_cnt = self.env.re_cnt
        cont = np.zeros((S, sh.n_actions, self.C))
        for c, name in enumerate(self.outcomes):
            if name == "ball":
                val = re_cnt[np.minimum(b + 1, sh.n_balls - 1), k, o, base]
            elif name in pc.STRIKE_LIKE:
                val = re_cnt[b, np.minimum(k + 1, sh.n_strikes - 1), o, base]
            elif name == "foul":
                val = re_cnt[b, np.where(k >= sh.n_strikes - 1, k, k + 1), o, base]
            else:
                val = np.zeros(S)
            cont[:, :, c] = val[:, None]
        return np.where(live, cont, term)

    def use_device(self, device: str) -> "BatchEngine":
        torch = _torch()
        self.device = device
        dev = torch.device("cuda" if device == "cuda" else "cpu")
        f64 = torch.float64
        self._t = {"live": torch.as_tensor(self._np["live"], device=dev), "safe": torch.as_tensor(self._np["safe"], device=dev, dtype=torch.long),
                   "term": torch.as_tensor(self._np["term"], device=dev, dtype=f64), "hr_term": torch.as_tensor(self._np["hr_term"], device=dev, dtype=f64),
                   "cont": torch.as_tensor(self._np["cont"], device=dev, dtype=f64), "start_idx": torch.as_tensor(self._start_idx, device=dev, dtype=torch.long),
                   "start_w": torch.as_tensor(self._start_w, device=dev, dtype=f64)}
        return self

    def bytes_per_item(self, copies: float = 6.0) -> float:
        """Memory one pitcher of a batch needs at its peak: a few arrays of states x actions x outcomes (the gathered values, the probabilities, their product)."""

        S, A = self.shape.size, self.shape.n_actions
        return copies * S * A * self.C * 8.0

    def tensor(self, array, dtype=None):
        torch = _torch()
        return torch.as_tensor(np.asarray(array), device=self._t["live"].device, dtype=dtype or torch.float64)

    # ------------------------------------------------------------------------------------------------ pieces
    def _q(self, V, P, term=None):
        """Q[b, s, a] for values ``V`` [B, S]; ``P`` is [B, S, A, C] or [S, A, C] (the same for every pitcher)."""

        torch = _torch()
        t = self._t["term"] if term is None else term
        G = torch.where(self._t["live"], V[:, self._t["safe"]], t)
        return (P * G).sum(dim=-1)

    def _effective(self, obs, support):
        """(support used, states with nothing supported): a state with no supported action keeps what was observed there (``Engine.__init__``)."""

        sup = support.clone()
        empty = ~sup.any(dim=-1)
        sup[empty] = obs[empty] > 0
        return sup, empty

    def _freeze(self, pi, obs, empty):
        if empty.any():
            pi = pi.clone(); pi[empty] = obs[empty]
        return pi

    def _argmin_policy(self, Q, sup, obs, empty):
        torch = _torch()
        masked = torch.where(sup, Q, torch.full_like(Q, float("inf")))
        pi = torch.zeros_like(Q)
        pi.scatter_(-1, masked.argmin(dim=-1, keepdim=True), 1.0)
        return self._freeze(pi, obs, empty)

    def _trust_policy(self, Q, sup, obs, empty, delta):
        """``policy_core.trust_shares`` for every state of every pitcher (rows are independent)."""

        torch = _torch()
        inf = float("inf")
        zero = torch.zeros_like(obs)
        low = torch.where(sup, torch.clamp(obs - delta, min=0.0), zero)
        up = torch.where(sup, torch.clamp(obs + delta, max=1.0), zero)
        room = up - low
        remaining = torch.clamp(1.0 - low.sum(dim=-1), min=0.0)
        order = torch.sort(torch.where(sup, Q, torch.full_like(Q, inf)), dim=-1, stable=True).indices
        sorted_room = torch.gather(room, -1, order)
        before = torch.cumsum(sorted_room, dim=-1) - sorted_room
        add_sorted = torch.minimum(torch.clamp(remaining[..., None] - before, min=0.0), sorted_room)
        add = torch.zeros_like(add_sorted)
        add.scatter_(-1, order, add_sorted)
        pi = low + add
        total = pi.sum(dim=-1, keepdim=True)
        pi = pi / torch.where(total > 0, total, torch.ones_like(total))
        return self._freeze(pi, obs, empty)

    def _step(self, label: str) -> None:
        """Between iterations: the guard may pause the run; on the card every call is made short and the card rests when it has been busy."""

        guard.checkpoint(label)
        if self.device == "cuda":
            guard.gpu_pause(_torch())

    # ------------------------------------------------------------------------------------------------ values
    def evaluate(self, pi, P, term=None):
        """Values [B, S] of the policies ``pi`` [B, S, A]; every pitcher iterates to his own convergence."""

        torch = _torch()
        B = pi.shape[0]
        V = torch.zeros(B, self.shape.size, dtype=torch.float64, device=pi.device)
        active = torch.ones(B, dtype=torch.bool, device=pi.device)
        for _ in range(self.max_iter):
            idx = active.nonzero(as_tuple=True)[0]
            Pa = P[idx] if P.dim() == 4 else P
            new = (pi[idx] * self._q(V[idx], Pa, term)).sum(dim=-1)
            diff = (new - V[idx]).abs().amax(dim=-1)
            V[idx] = new
            active[idx[diff < self.tol]] = False
            if not bool(active.any()):
                break
            self._step("batched evaluation")
        return V

    def solve_dp(self, kind: str, P, obs, sup, empty, trust: float):
        """(V [B, S], pi [B, S, A]) of ``dp_unrestricted`` or ``dp_trust``."""

        torch = _torch()
        B = obs.shape[0]
        V = torch.zeros(B, self.shape.size, dtype=torch.float64, device=obs.device)
        pi_out = torch.zeros_like(obs)
        active = torch.ones(B, dtype=torch.bool, device=obs.device)
        for _ in range(self.max_iter):
            idx = active.nonzero(as_tuple=True)[0]
            Pa = P[idx] if P.dim() == 4 else P
            Q = self._q(V[idx], Pa)
            if kind == "dp_unrestricted":
                pi = self._argmin_policy(Q, sup[idx], obs[idx], empty[idx])
            else:
                pi = self._trust_policy(Q, sup[idx], obs[idx], empty[idx], trust)
            new = (pi * Q).sum(dim=-1)
            diff = (new - V[idx]).abs().amax(dim=-1)
            V[idx] = new
            pi_out[idx] = pi
            active[idx[diff < self.tol]] = False
            if not bool(active.any()):
                break
            self._step("batched solve")
        return V, pi_out

    def start_value(self, V):
        """[B]: the value at the start of a plate appearance, weighted by how often each (outs, bases) starts it."""

        w = self._t["start_w"]
        return ((w[None, :] * V[:, self._t["start_idx"]]).sum(dim=-1) / w.sum())

    # ------------------------------------------------------------------------------------------------ everything one engine call gives
    def solve_kinds(self, P, obs, support, kinds: Sequence[str], trust: float, hr_kinds: Sequence[str] = ()) -> dict[str, dict[str, Any]]:
        """Per policy kind: ``start`` [B], ``pi`` [B, S, A] (numpy) and, for ``hr_kinds``, ``hr`` [B]: what ``Engine.solve`` / ``start_value`` / ``hr_probability`` give for each pitcher."""

        torch = _torch()
        sup, empty = self._effective(obs, support)
        out: dict[str, dict[str, Any]] = {}
        for kind in dict.fromkeys(("observed", *kinds)):
            if kind == "observed":
                pi = obs.clone(); V = self.evaluate(pi, P)
            elif kind == "const_FF":
                pi = obs.clone(); use = sup[:, :, 0] & ~empty
                pi[use] = 0.0; pi[use, 0] = 1.0
                V = self.evaluate(pi, P)
            elif kind == "greedy1":
                Q1 = (P * self._t["cont"]).sum(dim=-1)
                pi = self._argmin_policy(Q1, sup, obs, empty); V = self.evaluate(pi, P)
            elif kind in ("dp_unrestricted", "dp_trust"):
                V, pi = self.solve_dp(kind, P, obs, sup, empty, trust)
            else:
                raise ValueError(f"unknown policy {kind!r}")
            row: dict[str, Any] = {"start": self.start_value(V).cpu().numpy(), "pi": pi}
            if kind in hr_kinds:
                row["hr"] = self.start_value(self.evaluate(pi, P, self._t["hr_term"])).cpu().numpy()
            out[kind] = row
        return out


# ------------------------------------------------------------------------------------------------ batches sized by the resource guard
def run_in_batches(engine: BatchEngine, n_items: int, work: Callable[[slice, int], Any], *, copies: float = 6.0, label: str = "batched engine") -> list[Any]:
    """Split ``n_items`` pitchers into batches whose size the guard picks from what is free now, call ``work(slice, batch_size)`` for each and return the results in order.

    The size is looked up again before every batch (it follows the memory that is free at that moment); a batch that runs out of memory is halved and tried again, and on the card the work goes to the
    CPU when even a single pitcher does not fit."""

    torch = _torch()
    results: list[Any] = []
    start = 0
    cap = None
    requested = "auto"
    while start < n_items:
        guard.checkpoint(label)
        device = guard.device(requested)
        engine.use_device(device)
        size = guard.batch_items(engine.bytes_per_item(copies), device)
        if cap is not None:
            size = min(size, cap)
        size = max(1, min(size, n_items - start))
        try:
            results.append(work(slice(start, start + size), size))
        except (torch.cuda.OutOfMemoryError, MemoryError):                                      # not enough memory for this many: half as many (down to one), then the CPU
            guard.shrink()
            if device == "cuda":
                torch.cuda.empty_cache()
            if size > 1:
                cap = max(1, size // 2)
            elif device == "cuda":
                requested = "cpu"
            else:
                guard.checkpoint(label)
            continue
        start += size
        requested = "auto"                                                                         # the cap that an out-of-memory set stays: the next batches are not tried at the size that failed
    return results
