"""Design matrices for the outcome models: the situation columns with their interactions, the player-effect columns, the descriptors and the pitch location.

Everything is sparse: the situation block has about a dozen non-zero columns per row, an effect block exactly one (or none for a player the model has not seen), so the
pitcher-and-game effects (tens of thousands of columns) fit in memory.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
from scipy import sparse

from ..profiles import PITCH_TYPES

N_TYPES = len(PITCH_TYPES)                       # 9
TYPE_INDEX = {t: i for i, t in enumerate(PITCH_TYPES)}
KEY_BASE = 10_000_000                            # pitcher ids are below this


def type_index(values) -> np.ndarray:
    v = np.asarray(values).astype(str)
    out = np.full(len(v), -1, dtype=np.int64)
    for t, i in TYPE_INDEX.items():
        out[v == t] = i
    return out


def prev_index(values) -> np.ndarray:
    """Previous pitch type 0..8, or 9 for none (first pitch of the plate appearance)."""

    idx = type_index(values)
    return np.where(idx < 0, N_TYPES, idx)


def count_cell(cols: Mapping[str, np.ndarray]) -> np.ndarray:
    b = np.clip(np.nan_to_num(np.asarray(cols["balls"], dtype=float)), 0, 3).astype(np.int64)
    k = np.clip(np.nan_to_num(np.asarray(cols["strikes"], dtype=float)), 0, 2).astype(np.int64)
    return b * 3 + k


# ------------------------------------------------------------------------------------------------ situation block
def situation_design(cols: Mapping[str, np.ndarray]) -> tuple[sparse.csr_matrix, list[str]]:
    """One-hot blocks and their interactions: count, outs, runners, previous type, streak, pitch type, count x type, previous x type, repeat x type, streak x repeat, runners x outs."""

    n = len(cols["game_pk"])
    cell = count_cell(cols)
    outs = np.clip(np.nan_to_num(np.asarray(cols["outs_when_up"], dtype=float)), 0, 2).astype(np.int64)
    bases = np.asarray(cols["bases"]).astype(np.int64) & 7
    typ = np.maximum(type_index(cols["pitch_type"]), 0)
    prev = prev_index(cols["prev1_pitch_type"])
    streak = np.clip(np.nan_to_num(np.asarray(cols["streak_pos"], dtype=float), nan=1.0), 1, 3).astype(np.int64) - 1
    repeat = (prev == typ).astype(np.int64)
    blocks = [("count", cell, 12), ("outs", outs, 3), ("bases", bases, 8), ("prev", prev, N_TYPES + 1), ("streak", streak, 3), ("type", typ, N_TYPES), ("repeat", repeat, 2),
              ("count*type", cell * N_TYPES + typ, 12 * N_TYPES), ("prev*type", prev * N_TYPES + typ, (N_TYPES + 1) * N_TYPES), ("repeat*type", repeat * N_TYPES + typ, 2 * N_TYPES),
              ("streak*repeat", streak * 2 + repeat, 6), ("bases*outs", bases * 3 + outs, 24)]
    cols_idx, names, offset = [], [], 0
    for label, idx, size in blocks:
        cols_idx.append(idx + offset); names += [f"{label}={i}" for i in range(size)]; offset += size
    rows = np.tile(np.arange(n), len(blocks))
    mat = sparse.csr_matrix((np.ones(n * len(blocks), dtype=np.float32), (rows, np.concatenate(cols_idx))), shape=(n, offset))
    return mat, names


# ------------------------------------------------------------------------------------------------ player effects
def effect_keys(cols: Mapping[str, np.ndarray], group: str) -> np.ndarray:
    pitcher = np.asarray(cols["pitcher"]).astype(np.int64)
    batter = np.asarray(cols["batter"]).astype(np.int64)
    game = np.asarray(cols["game_pk"]).astype(np.int64)
    typ = np.maximum(type_index(cols["pitch_type"]), 0)
    if group == "pitcher":
        return pitcher
    if group == "batter":
        return batter
    if group == "pitcher_pitch":
        return pitcher * 16 + typ
    if group == "batter_pitch":
        return batter * 16 + typ
    if group == "pitcher_game":
        return game * KEY_BASE + pitcher
    if group == "pitcher_game_pitch":
        return (game * KEY_BASE + pitcher) * 16 + typ
    if group == "batter_count":
        return batter * 16 + count_cell(cols)
    raise ValueError(f"unknown effect group {group!r}")


class EffectIndex:
    """The units of one effect group seen in training; a unit the model has not seen has no column (its effect is 0)."""

    def __init__(self, keys: np.ndarray):
        self.keys = np.unique(np.asarray(keys, dtype=np.int64))

    def __len__(self) -> int:
        return int(len(self.keys))

    def lookup(self, keys: np.ndarray) -> np.ndarray:
        keys = np.asarray(keys, dtype=np.int64)
        if len(self.keys) == 0:
            return np.full(len(keys), -1, dtype=np.int64)
        pos = np.searchsorted(self.keys, keys)
        pos_c = np.clip(pos, 0, len(self.keys) - 1)
        return np.where(self.keys[pos_c] == keys, pos_c, -1)


def fit_effect_indexes(cols: Mapping[str, np.ndarray], groups: Sequence[str]) -> dict[str, EffectIndex]:
    return {g: EffectIndex(effect_keys(cols, g)) for g in groups}


def effect_matrix(cols: Mapping[str, np.ndarray], indexes: Mapping[str, EffectIndex], groups: Sequence[str]) -> tuple[sparse.csr_matrix, dict[str, tuple[int, int]]]:
    """Columns of every effect group side by side (in the order of ``groups``) and the (start, size) of each group."""

    n = len(cols["game_pk"])
    parts, spans, start = [], {}, 0
    for g in groups:
        idx = indexes[g].lookup(effect_keys(cols, g))
        ok = idx >= 0
        parts.append(sparse.csr_matrix((np.ones(int(ok.sum()), dtype=np.float32), (np.nonzero(ok)[0], idx[ok])), shape=(n, len(indexes[g]))))
        spans[g] = (start, len(indexes[g])); start += len(indexes[g])
    if not parts:
        return sparse.csr_matrix((n, 0), dtype=np.float32), spans
    return sparse.hstack(parts, format="csr"), spans


# ------------------------------------------------------------------------------------------------ continuous blocks (descriptors, location)
class Standardizer:
    def __init__(self, x: np.ndarray):
        x = np.asarray(x, dtype=np.float64)
        self.mean = np.nanmean(x, axis=0); self.std = np.nanstd(x, axis=0)
        self.mean = np.where(np.isfinite(self.mean), self.mean, 0.0); self.std = np.where((self.std > 1e-9) & np.isfinite(self.std), self.std, 1.0)

    def __call__(self, x: np.ndarray) -> np.ndarray:
        z = (np.asarray(x, dtype=np.float64) - self.mean) / self.std
        return np.where(np.isfinite(z), z, 0.0)


def location_terms(plate_x: np.ndarray, plate_z: np.ndarray, typ: np.ndarray, std: "Standardizer") -> sparse.csr_matrix:
    """Quadratic terms of the location (x, z, x^2, z^2, x*z), a separate set for every pitch type."""

    z = std(np.column_stack([plate_x, plate_z]))
    x_, z_ = z[:, 0], z[:, 1]
    terms = np.column_stack([x_, z_, x_ * x_, z_ * z_, x_ * z_]).astype(np.float32)
    n = len(typ)
    typ = np.maximum(np.asarray(typ), 0)
    rows = np.repeat(np.arange(n), 5)
    cols = (typ[:, None] * 5 + np.arange(5)[None, :]).ravel()
    return sparse.csr_matrix((terms.ravel(), (rows, cols)), shape=(n, N_TYPES * 5))


class LocationPool:
    """Where each pitch type was thrown in each count (and by each pitcher): the draws a prediction is averaged over when location is controlled."""

    def __init__(self, cols: Mapping[str, np.ndarray], *, per_key: int = 2000, per_pitcher: int = 300, min_pitcher: int = 30, seed: int = 0):
        rng = np.random.default_rng(seed)
        x = np.asarray(cols["plate_x"], dtype=float); z = np.asarray(cols["plate_z"], dtype=float)
        ok = np.isfinite(x) & np.isfinite(z)
        typ = type_index(cols["pitch_type"]); cell = count_cell(cols); pitcher = np.asarray(cols["pitcher"]).astype(np.int64)
        self.league: dict[tuple[int, int], np.ndarray] = {}
        self.league_type: dict[int, np.ndarray] = {}
        self.by_pitcher: dict[tuple[int, int], np.ndarray] = {}
        both = np.column_stack([x, z])

        def take(mask: np.ndarray, limit: int) -> np.ndarray:
            idx = np.nonzero(mask)[0]
            if len(idx) > limit:
                idx = rng.choice(idx, size=limit, replace=False)
            return both[idx]

        for t in range(N_TYPES):
            m_t = ok & (typ == t)
            if m_t.any():
                self.league_type[t] = take(m_t, per_key * 4)
            for c in range(12):
                m = m_t & (cell == c)
                if m.any():
                    self.league[(t, c)] = take(m, per_key)
        key = pitcher * 16 + typ
        for k in np.unique(key[ok & (typ >= 0)]):
            m = ok & (key == k)
            if m.sum() >= min_pitcher:
                self.by_pitcher[(int(k) // 16, int(k) % 16)] = take(m, per_pitcher)

    def sample(self, typ: np.ndarray, cell: np.ndarray, pitcher: np.ndarray | None, draws: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
        """[n, draws] x and z locations; a pitcher's own pool when he has one for the pitch type, otherwise the league's for the type and count."""

        n = len(typ)
        xs = np.zeros((n, draws)); zs = np.zeros((n, draws))
        pool_id = np.full(n, -1, dtype=np.int64)
        pools: list[np.ndarray] = []
        seen: dict[Any, int] = {}
        for i in range(n):
            t, c = int(typ[i]), int(cell[i])
            p = None if pitcher is None else int(pitcher[i])
            key: Any = ("p", p, t) if (p is not None and (p, t) in self.by_pitcher) else (("l", t, c) if (t, c) in self.league else ("t", t))
            if key not in seen:
                pool = self.by_pitcher.get((key[1], key[2])) if key[0] == "p" else (self.league.get((key[1], key[2])) if key[0] == "l" else self.league_type.get(key[1]))
                if pool is None or len(pool) == 0:
                    pool = np.zeros((1, 2))
                seen[key] = len(pools); pools.append(pool)
            pool_id[i] = seen[key]
        for j, pool in enumerate(pools):
            rows = np.nonzero(pool_id == j)[0]
            pick = rng.integers(0, len(pool), size=(len(rows), draws))
            xs[rows] = pool[pick, 0]; zs[rows] = pool[pick, 1]
        return xs, zs
