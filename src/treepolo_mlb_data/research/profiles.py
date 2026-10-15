"""Player descriptors for the league model (P6 plan 3.2, 3.3).

Everything here works on a *columnar* pitch table (``dict[str, numpy.ndarray]``), never on per-row dicts, so a league
(1.4M+ pitches) fits in a few hundred MB. A descriptor is a shrunken (empirical-Bayes) average of what a player did in
EARLIER games: a training row is described from the games that were played before its own game ("history" mode), so the
descriptor can never contain the pitch it is meant to predict. (Subtracting only the row's own game, "leave-game-out", was tried first:
a flexible model recovers a player's total from the other descriptor columns and decodes his current-game outcomes from the difference, which showed up
as a false gain in the pure-noise world P0.) Rows from a held-out period are described from the whole training period.

What is deliberately NOT used: bat tracking, batted-ball data, the current pitch's speed/location/movement, player ids and age.
Pitcher descriptors come from the pitcher's *typical* pitch of each type (his arsenal), not from the pitch being predicted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np

PITCH_TYPES = ("FF", "SI", "FC", "SL", "ST", "CH", "CU", "FS", "KC")
CATEGORY_OF = {"FF": 0, "SI": 0, "FC": 0, "SL": 1, "ST": 1, "CU": 1, "KC": 1, "CH": 2, "FS": 2}   # 0 fastball, 1 breaking, 2 offspeed
CATEGORY_NAMES = ("fb", "br", "off")
IN_PLAY = ("in_play_home_run", "in_play_triple", "in_play_double", "in_play_single", "in_play_out", "in_play_error_other")
SWING = ("whiff", "foul_tip", "foul") + IN_PLAY
TAKE = ("ball", "called_strike", "hit_by_pitch")
STRIKE_LIKE = ("called_strike", "whiff", "foul_tip", "foul")

# (name, numerator indicator, denominator indicator); the indicators are built in ``_indicators``.
PITCHER_RATES = (
    ("p_whiff_per_swing", "whiff", "swing"), ("p_zone_rate", "in_zone", "zoned"), ("p_cs_per_take", "called_strike", "take"),
    ("p_foul_per_swing", "foul", "swing"), ("p_first_strike", "first_strike", "first"), ("p_hr_per_pitch", "hr", "one"),
)
BATTER_RATES = (
    ("b_swing_rate", "swing", "one"), ("b_chase_rate", "swing_out", "out_zone"), ("b_zone_swing", "swing_in", "in_zone"),
    ("b_whiff_fb", "whiff_fb", "swing_fb"), ("b_whiff_br", "whiff_br", "swing_br"), ("b_whiff_off", "whiff_off", "swing_off"),
    ("b_foul_per_swing", "foul", "swing"), ("b_inplay_per_swing", "inplay", "swing"), ("b_first_swing", "first_swing", "first"),
    ("b_k_rate", "k_event", "pa_end"), ("b_bb_rate", "bb_event", "pa_end"), ("b_hr_per_pa", "hr", "pa_end"),
)
# continuous quantities averaged over a pitcher's pitches of one type
ARSENAL_MEANS = ("release_speed", "release_spin_rate", "axis_sin", "axis_cos", "pfx_x", "pfx_z", "release_pos_x", "release_pos_z",
                 "release_extension", "arm_angle", "plate_x", "plate_z")
RAW_COLUMNS = ("game_pk", "game_order", "pitcher", "batter", "group", "pitch_type", "outcome", "zone", "pitch_index_in_pa", "events", "release_speed",
               "release_spin_rate", "spin_axis", "pfx_x", "pfx_z", "release_pos_x", "release_pos_z", "release_extension", "arm_angle",
               "plate_x", "plate_z")


# ------------------------------------------------------------------------------------------------- keyed sums
class KeyedSums:
    """Sums of a value matrix over composite integer/string keys, with vectorised lookup for arbitrary query rows."""

    def fit(self, keys: Sequence[np.ndarray], values: np.ndarray) -> "KeyedSums":
        self.vocab = [np.unique(np.asarray(k)) for k in keys]
        self.stride = [1] * len(keys)
        for j in range(len(keys) - 2, -1, -1):
            self.stride[j] = self.stride[j + 1] * (len(self.vocab[j + 1]) + 1)
        composite, valid = self._encode(keys)
        composite = composite[valid]
        self.keys, inverse = np.unique(composite, return_inverse=True)
        values = np.asarray(values, dtype=np.float64)[valid]
        self.sums = np.zeros((len(self.keys), values.shape[1]))
        for c in range(values.shape[1]):
            self.sums[:, c] = np.bincount(inverse, weights=values[:, c], minlength=len(self.keys))
        return self

    def _encode(self, keys: Sequence[np.ndarray]):
        n = len(keys[0]); composite = np.zeros(n, dtype=np.int64); valid = np.ones(n, dtype=bool)
        for vocab, stride, k in zip(self.vocab, self.stride, keys):
            k = np.asarray(k)
            idx = np.searchsorted(vocab, k)
            idx_c = np.minimum(idx, len(vocab) - 1)
            ok = vocab[idx_c] == k
            valid &= ok
            composite += np.where(ok, idx_c + 1, 0).astype(np.int64) * stride
        return composite, valid

    def lookup(self, keys: Sequence[np.ndarray]) -> np.ndarray:
        composite, valid = self._encode(keys)
        pos = np.minimum(np.searchsorted(self.keys, composite), max(len(self.keys) - 1, 0))
        hit = valid & (len(self.keys) > 0) & (self.keys[pos] == composite if len(self.keys) else False)
        out = np.zeros((len(composite), self.sums.shape[1]))
        out[hit] = self.sums[pos[hit]]
        return out

    def group_sums(self) -> np.ndarray:
        return self.sums

    def decode(self) -> tuple[list[np.ndarray], np.ndarray]:
        """(key columns, sums) of every stored key, in the order of ``self.keys``."""

        rem = self.keys.copy(); columns = []
        for vocab, stride in zip(self.vocab, self.stride):
            idx = rem // stride; rem = rem % stride
            columns.append(vocab[np.maximum(idx - 1, 0)])
        return columns, self.sums


class SumTable:
    """Totals and 'strictly earlier games only' sums of a value matrix over composite keys.

    ``game_order`` is any increasing number per game (chronological rank); rows with the same key and an order below the query's are summed."""

    def fit(self, keys: Sequence[np.ndarray], order: np.ndarray, values: np.ndarray) -> "SumTable":
        self.total = KeyedSums().fit(keys, values)
        composite, valid = self.total._encode(keys)
        order = np.asarray(order, dtype=np.int64)
        self.span = int(order.max()) + 2 if len(order) else 2
        full = composite[valid] * self.span + (order[valid] + 1)
        values = np.asarray(values, dtype=np.float64)[valid]
        uniq, inv = np.unique(full, return_inverse=True)
        sums = np.zeros((len(uniq), values.shape[1]))
        for c in range(values.shape[1]):
            sums[:, c] = np.bincount(inv, weights=values[:, c], minlength=len(uniq))
        entity = uniq // self.span
        cum = np.cumsum(sums, axis=0)
        if len(uniq):
            block_start = np.searchsorted(entity, entity, side="left")
            before = np.zeros_like(cum); has = block_start > 0
            before[has] = cum[block_start[has] - 1]
            cum = cum - before
        self.full_keys, self.entity, self.cum = uniq, entity, cum
        return self

    def get(self, keys: Sequence[np.ndarray], order: np.ndarray | None, history: bool) -> np.ndarray:
        if not history:
            return self.total.lookup(keys)
        composite, valid = self.total._encode(keys)
        order = np.asarray(order, dtype=np.int64)
        query = composite * self.span + (order + 1)
        pos = np.searchsorted(self.full_keys, query, side="left") - 1
        ok = valid & (pos >= 0)
        pos_c = np.maximum(pos, 0)
        ok &= self.entity[pos_c] == composite
        out = np.zeros((len(composite), self.cum.shape[1]))
        out[ok] = self.cum[pos_c[ok]]
        return out


# --------------------------------------------------------------------------------------------- shrinkage constants
def icc_strength_rate(x: np.ndarray, n: np.ndarray) -> float:
    """Beta-binomial prior strength k (pseudo-trials) by the ANOVA intraclass-correlation estimator; k = (1 - rho) / rho."""

    keep = n > 0
    x, n = x[keep], n[keep]; m = len(n)
    if m < 3 or n.sum() <= m:
        return 50.0
    total = n.sum(); p = x.sum() / total; r = x / n
    n_bar = (total - (n ** 2).sum() / total) / (m - 1)
    msb = (n * (r - p) ** 2).sum() / (m - 1)
    msw = (n * r * (1 - r)).sum() / max(total - m, 1)
    denom = msb + (n_bar - 1) * msw
    rho = (msb - msw) / denom if denom > 0 else 0.0
    rho = float(np.clip(rho, 1e-4, 0.999))
    return float(np.clip((1 - rho) / rho, 1.0, 1e5))


def icc_strength_mean(s1: np.ndarray, s2: np.ndarray, n: np.ndarray) -> float:
    """Prior strength k for averages: k = within variance / between variance (ANOVA), from per-entity sum, sum of squares, count."""

    keep = n > 1
    s1, s2, n = s1[keep], s2[keep], n[keep]; m = len(n)
    if m < 3:
        return 20.0
    total = n.sum(); mean_i = s1 / n; grand = s1.sum() / total
    msw = float(((s2 - n * mean_i ** 2).sum()) / max(total - m, 1))
    n_bar = (total - (n ** 2).sum() / total) / (m - 1)
    msb = float((n * (mean_i - grand) ** 2).sum() / (m - 1))
    tau2 = max((msb - msw) / n_bar, msw * 1e-4, 1e-12)
    return float(np.clip(msw / tau2, 1.0, 1e5))


# --------------------------------------------------------------------------------------------- indicator matrix
def _indicators(cols: Mapping[str, np.ndarray]) -> tuple[dict[str, np.ndarray], list[str]]:
    outcome = np.asarray(cols["outcome"]).astype(str)
    ptype = np.asarray(cols["pitch_type"]).astype(str)
    zone = np.asarray(cols["zone"], dtype=np.float64)
    first = np.asarray(cols["pitch_index_in_pa"], dtype=np.float64) == 1
    events = np.asarray(cols["events"], dtype=object)
    has_event = np.array([e is not None and e == e and str(e) != "" for e in events])
    ev = np.array([str(e) if h else "" for e, h in zip(events, has_event)])
    category = np.array([CATEGORY_OF.get(t, -1) for t in ptype])
    swing = np.isin(outcome, SWING); take = np.isin(outcome, TAKE)
    zoned = ~np.isnan(zone); in_zone = zoned & (zone >= 1) & (zone <= 9); out_zone = zoned & (zone >= 11)
    ind = {
        "one": np.ones(len(outcome)), "swing": swing, "take": take, "whiff": outcome == "whiff", "foul": outcome == "foul",
        "called_strike": outcome == "called_strike", "inplay": np.isin(outcome, IN_PLAY), "hr": outcome == "in_play_home_run",
        "zoned": zoned, "in_zone": in_zone, "out_zone": out_zone, "swing_out": swing & out_zone, "swing_in": swing & in_zone,
        "first": first, "first_strike": first & np.isin(outcome, STRIKE_LIKE), "first_swing": first & swing,
        "pa_end": has_event, "k_event": np.isin(ev, ("strikeout", "strikeout_double_play")), "bb_event": np.isin(ev, ("walk", "intent_walk")),
    }
    for c, name in enumerate(CATEGORY_NAMES):
        ind[f"swing_{name}"] = swing & (category == c); ind[f"whiff_{name}"] = (outcome == "whiff") & (category == c)
    names = list(ind)
    return {k: np.asarray(v, dtype=np.float64) for k, v in ind.items()}, names


def _arsenal_values(cols: Mapping[str, np.ndarray]) -> np.ndarray:
    """(n, 2 * len(ARSENAL_MEANS)): per quantity a [value-or-0, present] pair; spin axis becomes sin/cos of the angle."""

    n = len(cols["outcome"]); out = np.zeros((n, 2 * len(ARSENAL_MEANS)))
    axis = np.radians(np.asarray(cols["spin_axis"], dtype=np.float64))
    derived = {"axis_sin": np.sin(axis), "axis_cos": np.cos(axis)}
    for j, name in enumerate(ARSENAL_MEANS):
        v = derived[name] if name in derived else np.asarray(cols[name], dtype=np.float64)
        ok = ~np.isnan(v)
        out[:, 2 * j] = np.where(ok, v, 0.0); out[:, 2 * j + 1] = ok
    return out


def _squares(values: np.ndarray) -> np.ndarray:
    """Per quantity [value^2] aligned with the value columns of ``_arsenal_values`` (for the variance of a player's typical pitch)."""

    return values[:, 0::2] ** 2


@dataclass
class ProfileModel:
    """Fitted descriptor tables. ``transform(cols, side, history=...)`` returns a feature matrix for columnar rows.

    ``history=True``: each row is described from games played strictly before its own game (use for the rows the model is trained on);
    ``history=False``: from the whole fitted period (use for rows of a later, held-out period)."""

    pitch_types: tuple[str, ...] = PITCH_TYPES
    fitted: dict[str, Any] = field(default_factory=dict)

    # ----------------------------------------------------------------------------------------------- fit
    def fit(self, cols: Mapping[str, np.ndarray]) -> "ProfileModel":
        ind, names = _indicators(cols)
        self.indicator_names = names
        ind_matrix = np.column_stack([ind[n] for n in names])
        col = {n: i for i, n in enumerate(names)}
        self.col = col
        order = _order(cols)
        pitcher = np.asarray(cols["pitcher"]); batter = np.asarray(cols["batter"])
        group = np.asarray(cols["group"]).astype(str); ptype = np.asarray(cols["pitch_type"]).astype(str)
        throws = np.array([g[0] for g in group])
        rates = PITCHER_RATES + BATTER_RATES
        self.league = {name: float(ind_matrix[:, col[num]].sum() / max(ind_matrix[:, col[den]].sum(), 1.0)) for name, num, den in rates}
        self.t = {
            "p": SumTable().fit([pitcher], order, ind_matrix), "pg": SumTable().fit([pitcher, group], order, ind_matrix),
            "b": SumTable().fit([batter], order, ind_matrix), "bg": SumTable().fit([batter, group], order, ind_matrix),
        }
        self.k_all: dict[str, float] = {}; self.k_group: dict[str, float] = {}
        for name, num, den in PITCHER_RATES:
            self.k_all[name] = icc_strength_rate(self.t["p"].total.sums[:, col[num]], self.t["p"].total.sums[:, col[den]])
            self.k_group[name] = icc_strength_rate(self.t["pg"].total.sums[:, col[num]], self.t["pg"].total.sums[:, col[den]])
        for name, num, den in BATTER_RATES:
            self.k_all[name] = icc_strength_rate(self.t["b"].total.sums[:, col[num]], self.t["b"].total.sums[:, col[den]])
            self.k_group[name] = icc_strength_rate(self.t["bg"].total.sums[:, col[num]], self.t["bg"].total.sums[:, col[den]])
        # arsenal: restricted to the analysed pitch types
        a_mask = np.isin(ptype, self.pitch_types)
        av = _arsenal_values(cols)
        category = np.array([CATEGORY_OF.get(t, -1) for t in ptype])
        arsenal = np.column_stack([av, _squares(av), np.ones(len(ptype))])   # [value, present] * q, value^2 * q, one
        one = arsenal[:, -1:]
        self.q = len(ARSENAL_MEANS)
        sel = lambda *arrays: [x[a_mask] for x in arrays]
        self.a = {
            "pt": SumTable().fit(sel(pitcher, ptype), order[a_mask], arsenal[a_mask]),
            "pc": SumTable().fit(sel(pitcher, category), order[a_mask], arsenal[a_mask]),
            "ptg": SumTable().fit(sel(pitcher, ptype, group), order[a_mask], one[a_mask]),
            "pgn": SumTable().fit(sel(pitcher, group), order[a_mask], one[a_mask]),
            "pn": SumTable().fit(sel(pitcher), order[a_mask], one[a_mask]),
        }
        self.league_arsenal: dict[tuple[str, str], np.ndarray] = {}; self.league_category: dict[tuple[int, str], np.ndarray] = {}
        for t in self.pitch_types:
            for hand in ("R", "L"):
                self.league_arsenal[(t, hand)] = self._mean_vector(av[a_mask & (ptype == t) & (throws == hand)])
        self.league_any = {hand: self._mean_vector(av[a_mask & (throws == hand)]) for hand in ("R", "L")}   # pitch types outside the frozen list ('', EP, ...)
        for c in range(3):
            for hand in ("R", "L"):
                self.league_category[(c, hand)] = self._mean_vector(av[a_mask & (category == c) & (throws == hand)])
        pt = self.a["pt"].total.sums
        self.k_arsenal = np.array([icc_strength_mean(pt[:, 2 * j], pt[:, 2 * self.q + j], pt[:, 2 * j + 1]) for j in range(self.q)])
        self.k_usage = 30.0   # a pitcher's share of one type is a rate over his pitches; fixed pseudo-count (shares of thin types are noisy)
        counts = {}
        for hand in ("R", "L"):
            m = a_mask & (throws == hand)
            c = {t: float((ptype[m] == t).sum()) for t in self.pitch_types}; tot = sum(c.values()) or 1.0
            counts.update({(t, hand): v / tot for t, v in c.items()})
        self.league_share = counts
        self.n_rows = len(pitcher)
        return self

    def _mean_vector(self, v: np.ndarray) -> np.ndarray:
        out = np.zeros(self.q)
        for j in range(self.q):
            present = v[:, 2 * j + 1].sum()
            out[j] = v[:, 2 * j].sum() / present if present > 0 else 0.0
        return out

    # ------------------------------------------------------------------------------------------- who is in the tables
    def players(self, side: str, group: str) -> tuple[np.ndarray, np.ndarray]:
        """(ids, pitches) of the pitchers or batters seen in ``group`` during the fitted period."""

        if side == "pitcher":
            (ids, groups), sums = self.a["pgn"].total.decode(); n = sums[:, 0]
        else:
            (ids, groups), sums = self.t["bg"].total.decode(); n = sums[:, self.col["one"]]
        keep = np.asarray(groups).astype(str) == group
        return np.asarray(ids)[keep], n[keep]

    def pitcher_arsenal(self, pitcher, group: str) -> dict[str, int]:
        """Pitches of each analysed type that ``pitcher`` threw in ``group``."""

        (ids, types, groups), sums = self.a["ptg"].total.decode()
        keep = (np.asarray(ids) == pitcher) & (np.asarray(groups).astype(str) == group)
        return {str(t): int(n) for t, n in zip(np.asarray(types)[keep], sums[keep, 0])}

    # ------------------------------------------------------------------------------------------- transform
    def feature_names(self, side: str) -> list[str]:
        if side == "pitcher":
            return ([n for n, _, _ in PITCHER_RATES] + [f"a_{q}" for q in ARSENAL_MEANS]
                    + ["a_speed_gap", "a_pfx_x_gap", "a_pfx_z_gap", "a_usage_share", "a_usage_share_group", "n_pitcher_pitches", "n_type_pitches"])
        if side == "batter":
            return [n for n, _, _ in BATTER_RATES] + ["n_batter_pitches"]
        if side == "repertoire":
            return [n for n, _, _ in PITCHER_RATES] + [f"usage_{t}" for t in self.pitch_types] + ["fb_speed", "fb_pfx_x", "fb_pfx_z", "n_pitcher_pitches"]
        raise ValueError(side)

    def transform(self, cols: Mapping[str, np.ndarray], side: str, *, history: bool) -> np.ndarray:
        order = _order(cols) if history else None
        group = np.asarray(cols["group"]).astype(str)
        if side in ("pitcher", "repertoire"):
            return self._pitcher_features(cols, order, group, history, repertoire=(side == "repertoire"))
        if side == "batter":
            return self._batter_features(cols, order, group, history)
        raise ValueError(side)

    def _rates(self, entity, order, group, metrics, level_all: str, level_group: str, history: bool):
        all_sums = self.t[level_all].get([entity], order, history); grp_sums = self.t[level_group].get([entity, group], order, history)
        out = np.zeros((len(entity), len(metrics)))
        for j, (name, num, den) in enumerate(metrics):
            mu = self.league[name]
            r_all = (all_sums[:, self.col[num]] + self.k_all[name] * mu) / (all_sums[:, self.col[den]] + self.k_all[name])
            out[:, j] = (grp_sums[:, self.col[num]] + self.k_group[name] * r_all) / (grp_sums[:, self.col[den]] + self.k_group[name])
        return out, all_sums[:, self.col["one"]]

    def _batter_features(self, cols, order, group, history):
        rates, n_pitches = self._rates(np.asarray(cols["batter"]), order, group, BATTER_RATES, "b", "bg", history)
        return np.column_stack([rates, np.log1p(np.maximum(n_pitches, 0))])

    def _shrunk_means(self, sums: np.ndarray, hand: np.ndarray, prior_of, key_of: np.ndarray) -> np.ndarray:
        mu = np.zeros((len(sums), self.q))
        for hand_value in ("R", "L"):
            m = hand == hand_value
            for key in np.unique(key_of[m]) if m.any() else ():
                mu[m & (key_of == key)] = prior_of(key, hand_value)
        out = np.zeros((len(sums), self.q))
        for j in range(self.q):
            out[:, j] = (sums[:, 2 * j] + self.k_arsenal[j] * mu[:, j]) / (sums[:, 2 * j + 1] + self.k_arsenal[j])
        return out

    def _pitcher_features(self, cols, order, group, history, *, repertoire):
        pitcher = np.asarray(cols["pitcher"]); ptype = np.asarray(cols["pitch_type"]).astype(str)
        category = np.array([CATEGORY_OF.get(t, -1) for t in ptype]); hand = np.array([g[0] for g in group])
        rates, n_pitches = self._rates(pitcher, order, group, PITCHER_RATES, "p", "pg", history)
        a = self.a
        n_all = a["pn"].get([pitcher], order, history)[:, 0]; n_grp = a["pgn"].get([pitcher, group], order, history)[:, 0]
        fb_key = np.zeros(len(pitcher), dtype=int)
        fb = self._shrunk_means(a["pc"].get([pitcher, fb_key], order, history), hand, lambda k, h: self.league_category[(0, h)], fb_key)
        q = {n: i for i, n in enumerate(ARSENAL_MEANS)}
        if repertoire:
            usage = np.zeros((len(pitcher), len(self.pitch_types)))
            for ti, t in enumerate(self.pitch_types):
                tt = np.full(len(pitcher), t)
                num_all = a["pt"].get([pitcher, tt], order, history)[:, -1]; num_grp = a["ptg"].get([pitcher, tt, group], order, history)[:, 0]
                mu = np.array([self.league_share.get((t, h), 1.0 / len(self.pitch_types)) for h in hand])
                r_all = (num_all + self.k_usage * mu) / (n_all + self.k_usage)
                usage[:, ti] = (num_grp + self.k_usage * r_all) / (n_grp + self.k_usage)
            return np.column_stack([rates, usage, fb[:, q["release_speed"]], fb[:, q["pfx_x"]], fb[:, q["pfx_z"]], np.log1p(np.maximum(n_pitches, 0))])
        pt = a["pt"].get([pitcher, ptype], order, history)
        means = self._shrunk_means(pt, hand, lambda k, h: self.league_arsenal.get((str(k), h), self.league_any[h]), ptype)
        gaps = [means[:, q["release_speed"]] - fb[:, q["release_speed"]], means[:, q["pfx_x"]] - fb[:, q["pfx_x"]], means[:, q["pfx_z"]] - fb[:, q["pfx_z"]]]
        num_grp = a["ptg"].get([pitcher, ptype, group], order, history)[:, 0]
        mu = np.array([self.league_share.get((t, h), 1.0 / len(self.pitch_types)) for t, h in zip(ptype, hand)])
        r_all = (pt[:, -1] + self.k_usage * mu) / (n_all + self.k_usage)
        r_grp = (num_grp + self.k_usage * r_all) / (n_grp + self.k_usage)
        return np.column_stack([rates, means, *gaps, r_all, r_grp, np.log1p(np.maximum(n_pitches, 0)), np.log1p(np.maximum(pt[:, -1], 0))])


def _order(cols: Mapping[str, np.ndarray]) -> np.ndarray:
    """Chronological rank of each row's game (``game_order`` when given, else the game id, which grows with time)."""

    key = np.asarray(cols["game_order"] if "game_order" in cols else cols["game_pk"], dtype=np.float64)
    uniq = np.unique(key)
    return np.searchsorted(uniq, key).astype(np.int64)


def columns_from_rows(rows: Sequence[Mapping[str, Any]], *, group_field: str = "hand_group") -> dict[str, np.ndarray]:
    """Convert pitch-table row dicts to the columnar layout (``group`` is taken from ``hand_group`` by default)."""

    out: dict[str, np.ndarray] = {}
    for name in RAW_COLUMNS:
        source = group_field if name == "group" else name
        values = [r.get(source) for r in rows]
        if name in ("game_pk", "game_order", "pitcher", "batter"):
            out[name] = np.array([0 if v is None else int(v) for v in values], dtype=np.int64)
        elif name in ("group", "pitch_type", "outcome"):
            out[name] = np.array([str(v) for v in values])
        elif name == "events":
            out[name] = np.array(values, dtype=object)
        else:
            out[name] = np.array([np.nan if v is None else float(v) for v in values], dtype=np.float64)
    return out
