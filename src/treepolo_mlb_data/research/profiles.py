"""Player descriptors for the league model (P6 plan 3.2, 3.3).

Everything here works on a *columnar* pitch table (``dict[str, numpy.ndarray]``), never on per-row dicts, so a league
(1.4M+ pitches) fits in a few hundred MB. A descriptor is a shrunken (empirical-Bayes) average of what a player did in
OTHER games: for rows the model is trained on, the row's own game is subtracted ("leave-game-out"), so the descriptor
never contains the pitch it is meant to predict. Rows from a held-out period are described from the training period only.

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
RAW_COLUMNS = ("game_pk", "pitcher", "batter", "group", "pitch_type", "outcome", "zone", "pitch_index_in_pa", "events", "release_speed",
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
    """Fitted descriptor tables. ``transform`` returns a feature matrix for any columnar rows (see module docstring)."""

    pitch_types: tuple[str, ...] = PITCH_TYPES
    min_pitches_prior: int = 1
    fitted: dict[str, Any] = field(default_factory=dict)

    # ----------------------------------------------------------------------------------------------- fit
    def fit(self, cols: Mapping[str, np.ndarray]) -> "ProfileModel":
        ind, names = _indicators(cols)
        self.indicator_names = names
        ind_matrix = np.column_stack([ind[n] for n in names])
        col = {n: i for i, n in enumerate(names)}
        self.col = col
        game = np.asarray(cols["game_pk"]); pitcher = np.asarray(cols["pitcher"]); batter = np.asarray(cols["batter"])
        group = np.asarray(cols["group"]).astype(str); ptype = np.asarray(cols["pitch_type"]).astype(str)
        throws = np.array([g[0] for g in group])
        self.league = {name: float(ind_matrix[:, col[num]].sum() / max(ind_matrix[:, col[den]].sum(), 1.0))
                       for name, num, den in PITCHER_RATES + BATTER_RATES}
        self.league_group = {}
        for g in np.unique(group):
            m = group == g
            for name, num, den in PITCHER_RATES + BATTER_RATES:
                self.league_group[(name, str(g))] = float(ind_matrix[m, col[num]].sum() / max(ind_matrix[m, col[den]].sum(), 1.0))
        # keyed sums: entity / (entity, group) with and without the game
        self.t = {
            "p": KeyedSums().fit([pitcher], ind_matrix), "pg": KeyedSums().fit([pitcher, group], ind_matrix),
            "pG": KeyedSums().fit([pitcher, game], ind_matrix), "pgG": KeyedSums().fit([pitcher, group, game], ind_matrix),
            "b": KeyedSums().fit([batter], ind_matrix), "bg": KeyedSums().fit([batter, group], ind_matrix),
            "bG": KeyedSums().fit([batter, game], ind_matrix), "bgG": KeyedSums().fit([batter, group, game], ind_matrix),
        }
        # prior strengths (pseudo-trials) per rate metric and level
        self.k_all: dict[str, float] = {}; self.k_group: dict[str, float] = {}
        for name, num, den in PITCHER_RATES:
            self.k_all[name] = icc_strength_rate(self.t["p"].sums[:, col[num]], self.t["p"].sums[:, col[den]])
            self.k_group[name] = icc_strength_rate(self.t["pg"].sums[:, col[num]], self.t["pg"].sums[:, col[den]])
        for name, num, den in BATTER_RATES:
            self.k_all[name] = icc_strength_rate(self.t["b"].sums[:, col[num]], self.t["b"].sums[:, col[den]])
            self.k_group[name] = icc_strength_rate(self.t["bg"].sums[:, col[num]], self.t["bg"].sums[:, col[den]])
        # arsenal: restrict to the analysed pitch types
        in_types = np.isin(ptype, self.pitch_types)
        av = _arsenal_values(cols)
        category = np.array([CATEGORY_OF.get(t, -1) for t in ptype])
        counts = np.column_stack([np.ones(len(ptype))])
        sq = _squares(av)
        arsenal = np.column_stack([av, sq, counts])   # [value,present]*q, value^2 * q, one
        self.q = len(ARSENAL_MEANS)
        a_mask = in_types
        self.a = {
            "pt": KeyedSums().fit([pitcher[a_mask], ptype[a_mask]], arsenal[a_mask]),
            "ptG": KeyedSums().fit([pitcher[a_mask], ptype[a_mask], game[a_mask]], arsenal[a_mask]),
            "pc": KeyedSums().fit([pitcher[a_mask], category[a_mask]], arsenal[a_mask]),
            "pcG": KeyedSums().fit([pitcher[a_mask], category[a_mask], game[a_mask]], arsenal[a_mask]),
            "ptg": KeyedSums().fit([pitcher[a_mask], ptype[a_mask], group[a_mask]], arsenal[a_mask][:, -1:]),
            "ptgG": KeyedSums().fit([pitcher[a_mask], ptype[a_mask], group[a_mask], game[a_mask]], arsenal[a_mask][:, -1:]),
            "pgn": KeyedSums().fit([pitcher[a_mask], group[a_mask]], arsenal[a_mask][:, -1:]),
            "pgnG": KeyedSums().fit([pitcher[a_mask], group[a_mask], game[a_mask]], arsenal[a_mask][:, -1:]),
            "pn": KeyedSums().fit([pitcher[a_mask]], arsenal[a_mask][:, -1:]),
            "pnG": KeyedSums().fit([pitcher[a_mask], game[a_mask]], arsenal[a_mask][:, -1:]),
        }
        # league means of the arsenal quantities per (type, throwing hand) and per (category, hand); prior strengths
        self.league_arsenal: dict[tuple[str, str], np.ndarray] = {}; self.league_category: dict[tuple[int, str], np.ndarray] = {}
        for t in self.pitch_types:
            for hand in ("R", "L"):
                m = a_mask & (ptype == t) & (throws == hand)
                self.league_arsenal[(t, hand)] = self._mean_vector(av[m])
        for c in range(3):
            for hand in ("R", "L"):
                m = a_mask & (category == c) & (throws == hand)
                self.league_category[(c, hand)] = self._mean_vector(av[m])
        pt = self.a["pt"].sums
        self.k_arsenal = np.zeros(self.q)
        for j in range(self.q):
            self.k_arsenal[j] = icc_strength_mean(pt[:, 2 * j], pt[:, 2 * self.q + j], pt[:, 2 * j + 1])
        # usage share strength: a pitcher's share of one type is a rate over his pitches (fixed pseudo-count; shares are noisy for thin types)
        self.k_usage = 30.0
        self.n_rows = len(game)
        return self

    def _mean_vector(self, v: np.ndarray) -> np.ndarray:
        out = np.zeros(self.q)
        for j in range(self.q):
            present = v[:, 2 * j + 1].sum()
            out[j] = v[:, 2 * j].sum() / present if present > 0 else 0.0
        return out

    # ------------------------------------------------------------------------------------------- transform
    def feature_names(self, side: str) -> list[str]:
        if side == "pitcher":
            return ([n for n, _, _ in PITCHER_RATES] + [f"a_{q}" for q in ARSENAL_MEANS]
                    + ["a_speed_gap", "a_pfx_x_gap", "a_pfx_z_gap", "a_usage_share", "a_usage_share_group", "n_pitcher_pitches", "n_type_pitches"])
        if side == "batter":
            return [n for n, _, _ in BATTER_RATES] + ["n_batter_pitches"]
        if side == "repertoire":
            return ([n for n, _, _ in PITCHER_RATES] + [f"usage_{t}" for t in self.pitch_types] + ["fb_speed", "fb_pfx_x", "fb_pfx_z", "n_pitcher_pitches"])
        raise ValueError(side)

    def transform(self, cols: Mapping[str, np.ndarray], side: str, *, leave_game_out: bool) -> np.ndarray:
        game = np.asarray(cols["game_pk"]); group = np.asarray(cols["group"]).astype(str)
        if side in ("pitcher", "repertoire"):
            return self._pitcher_features(cols, game, group, leave_game_out, repertoire=(side == "repertoire"))
        if side == "batter":
            return self._batter_features(cols, game, group, leave_game_out)
        raise ValueError(side)

    def _rates(self, entity: np.ndarray, game: np.ndarray, group: np.ndarray, metrics, level_all: str, level_group: str, lgo: bool):
        all_sums = self.t[level_all].lookup([entity]); grp_sums = self.t[level_group].lookup([entity, group])
        if lgo:
            all_sums = all_sums - self.t[level_all + "G"].lookup([entity, game])
            grp_sums = grp_sums - self.t[level_group + "G"].lookup([entity, group, game])
        out = np.zeros((len(entity), len(metrics))); n_all = None
        for j, (name, num, den) in enumerate(metrics):
            mu = self.league[name]
            xa, na = all_sums[:, self.col[num]], all_sums[:, self.col[den]]
            r_all = (xa + self.k_all[name] * mu) / (na + self.k_all[name])
            xg, ng = grp_sums[:, self.col[num]], grp_sums[:, self.col[den]]
            out[:, j] = (xg + self.k_group[name] * r_all) / (ng + self.k_group[name])
        return out, all_sums[:, self.col["one"]]

    def _batter_features(self, cols, game, group, lgo):
        rates, n_pitches = self._rates(np.asarray(cols["batter"]), game, group, BATTER_RATES, "b", "bg", lgo)
        return np.column_stack([rates, np.log1p(np.maximum(n_pitches, 0))])

    def _arsenal_block(self, pitcher, ptype, category, game, lgo):
        a = self.a
        pt = a["pt"].lookup([pitcher, ptype]); pc = a["pc"].lookup([pitcher, category])
        if lgo:
            pt = pt - a["ptG"].lookup([pitcher, ptype, game]); pc = pc - a["pcG"].lookup([pitcher, category, game])
        return pt, pc

    def _shrunk_means(self, sums: np.ndarray, hand: np.ndarray, prior_lookup, key_of: np.ndarray) -> np.ndarray:
        q = self.q; out = np.zeros((len(sums), q))
        mu = np.zeros((len(sums), q))
        for hand_value in ("R", "L"):
            m = hand == hand_value
            if m.any():
                for key in np.unique(key_of[m]):
                    mm = m & (key_of == key)
                    mu[mm] = prior_lookup(key, hand_value)
        for j in range(q):
            n = sums[:, 2 * j + 1]
            out[:, j] = (sums[:, 2 * j] + self.k_arsenal[j] * mu[:, j]) / (n + self.k_arsenal[j])
        return out

    def _pitcher_features(self, cols, game, group, lgo, *, repertoire):
        pitcher = np.asarray(cols["pitcher"]); ptype = np.asarray(cols["pitch_type"]).astype(str)
        category = np.array([CATEGORY_OF.get(t, -1) for t in ptype]); hand = np.array([g[0] for g in group])
        rates, n_pitches = self._rates(pitcher, game, group, PITCHER_RATES, "p", "pg", lgo)
        a = self.a
        n_all_a = a["pn"].lookup([pitcher])[:, 0]; n_grp_a = a["pgn"].lookup([pitcher, group])[:, 0]
        if lgo:
            n_all_a = n_all_a - a["pnG"].lookup([pitcher, game])[:, 0]; n_grp_a = n_grp_a - a["pgnG"].lookup([pitcher, group, game])[:, 0]
        # fastball reference (category 0) of the same pitcher
        fb_key = np.zeros(len(pitcher), dtype=int)
        pc_fb = a["pc"].lookup([pitcher, fb_key])
        if lgo:
            pc_fb = pc_fb - a["pcG"].lookup([pitcher, fb_key, game])
        fb_mean = self._shrunk_means(pc_fb, hand, lambda k, h: self.league_category[(0, h)], fb_key)
        if repertoire:
            usage = np.zeros((len(pitcher), len(self.pitch_types)))
            for t_index, t in enumerate(self.pitch_types):
                tt = np.full(len(pitcher), t)
                share_all_num = a["pt"].lookup([pitcher, tt])[:, -1]; share_grp_num = a["ptg"].lookup([pitcher, tt, group])[:, 0]
                if lgo:
                    share_all_num = share_all_num - a["ptG"].lookup([pitcher, tt, game])[:, -1]
                    share_grp_num = share_grp_num - a["ptgG"].lookup([pitcher, tt, group, game])[:, 0]
                mu = np.array([self._league_share(t, h) for h in hand])
                r_all = (share_all_num + self.k_usage * mu) / (n_all_a + self.k_usage)
                usage[:, t_index] = (share_grp_num + self.k_usage * r_all) / (n_grp_a + self.k_usage)
            q = {n: i for i, n in enumerate(ARSENAL_MEANS)}
            return np.column_stack([rates, usage, fb_mean[:, q["release_speed"]], fb_mean[:, q["pfx_x"]], fb_mean[:, q["pfx_z"]], np.log1p(np.maximum(n_pitches, 0))])
        pt, _ = self._arsenal_block(pitcher, ptype, category, game, lgo)
        means = self._shrunk_means(pt, hand, lambda k, h: self.league_arsenal[(str(k), h)], ptype)
        q = {n: i for i, n in enumerate(ARSENAL_MEANS)}
        speed_gap = means[:, q["release_speed"]] - fb_mean[:, q["release_speed"]]
        x_gap = means[:, q["pfx_x"]] - fb_mean[:, q["pfx_x"]]; z_gap = means[:, q["pfx_z"]] - fb_mean[:, q["pfx_z"]]
        share_all_num = pt[:, -1]
        share_grp_num = a["ptg"].lookup([pitcher, ptype, group])[:, 0]
        if lgo:
            share_grp_num = share_grp_num - a["ptgG"].lookup([pitcher, ptype, group, game])[:, 0]
        mu = np.array([self._league_share(t, h) for t, h in zip(ptype, hand)])
        r_all = (share_all_num + self.k_usage * mu) / (n_all_a + self.k_usage)
        r_grp = (share_grp_num + self.k_usage * r_all) / (n_grp_a + self.k_usage)
        return np.column_stack([rates, means, speed_gap, x_gap, z_gap, r_all, r_grp, np.log1p(np.maximum(n_pitches, 0)), np.log1p(np.maximum(pt[:, -1], 0))])

    def _league_share(self, ptype: str, hand: str) -> float:
        cache = self.fitted.setdefault("league_share", {})
        if not cache:
            totals = {}
            for h in ("R", "L"):
                counts = {t: float(self._league_count(t, h)) for t in self.pitch_types}
                s = sum(counts.values()) or 1.0
                for t, c in counts.items():
                    cache[(t, h)] = c / s
        return cache.get((ptype, hand), 1.0 / len(self.pitch_types))

    def _league_count(self, ptype: str, hand: str) -> float:
        # sum the (pitcher, type, group) counts over pitchers whose group starts with the throwing hand
        t = self.a["ptg"]; types = t.vocab[1]; groups = t.vocab[2]
        if ptype not in types:
            return 0.0
        ti = int(np.searchsorted(types, ptype)) + 1
        total = 0.0
        stride = t.stride
        comp = t.keys
        # decode composite keys
        tcode = (comp // stride[1]) % (len(types) + 1)
        gcode = (comp // stride[2]) % (len(groups) + 1)
        gmask = np.array([groups[c - 1][0] == hand if c > 0 else False for c in gcode])
        return float(t.sums[(tcode == ti) & gmask, 0].sum())


def columns_from_rows(rows: Sequence[Mapping[str, Any]], *, group_field: str = "hand_group") -> dict[str, np.ndarray]:
    """Convert pitch-table row dicts to the columnar layout (``group`` is taken from ``hand_group`` by default)."""

    out: dict[str, np.ndarray] = {}
    for name in RAW_COLUMNS:
        source = group_field if name == "group" else name
        values = [r.get(source) for r in rows]
        if name in ("game_pk", "pitcher", "batter"):
            out[name] = np.array([int(v) for v in values], dtype=np.int64)
        elif name in ("group", "pitch_type", "outcome"):
            out[name] = np.array([str(v) for v in values])
        elif name == "events":
            out[name] = np.array(values, dtype=object)
        else:
            out[name] = np.array([np.nan if v is None else float(v) for v in values], dtype=np.float64)
    return out
