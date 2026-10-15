"""Situation queries (P6 plan 3.9, A.6): what is each pitch type worth in this situation?

M0 reads the league-average situation table built by ``pitch_policy``. M1 (a named pitcher against the average batter), M2 (the average pitcher against a named batter)
and M3 (both named) ask the league model with the players' descriptors, solve the plate appearance for each sampled pair of players and average the pairs.
Nothing here looks at the current pitch's speed, location or movement, bat tracking or batted-ball data.
"""
from __future__ import annotations

import base64
import io
import json
import pickle
import zlib
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np

from . import gbm
from . import league_core as lc
from . import policy_build as pb
from . import policy_core as pc
from .config_schema import ConfigError
from .profiles import PITCH_TYPES

BASE_BITS = {"1": 1, "2": 2, "3": 4}
FLAGS = ("sufficient", "extrapolated", "unsupported")
NOT_COVERED = ("特定配對的過去對戰結果", "比分與局數", "守備、球場、天氣、捕手", "本球的球速與位置（位置只有「若落在這裡」的變體）", "球員的欺敵感等描述特徵沒有捕捉的個人特質")


class QueryError(ConfigError):
    """The question cannot be answered (unknown group, missing table, player without data ...)."""


def parse_bases(value: Any) -> int:
    """Bases as a number 0-7, or a three-character 1/0 string for first, second and third base ('100' = runner on first)."""

    if isinstance(value, (int, np.integer)):
        if not 0 <= int(value) <= 7:
            raise QueryError("bases must be 0-7 or a string such as '100' / bases 必須是 0-7 或像 '100' 的字串")
        return int(value)
    text = str(value).strip()
    if text.isdigit() and len(text) <= 1:
        return parse_bases(int(text))
    if len(text) != 3 or set(text) - {"0", "1"}:
        raise QueryError("bases must be 0-7 or a string such as '100' (first, second, third) / bases 必須是 0-7 或像 '100' 的字串（一壘、二壘、三壘）")
    return sum(BASE_BITS[str(i + 1)] for i, ch in enumerate(text) if ch == "1")


def unpack_arrays(rows: Sequence[Mapping[str, Any]]) -> dict[str, np.ndarray]:
    blob = "".join(r["payload"] for r in sorted(rows, key=lambda r: r["part"]))
    with np.load(io.BytesIO(zlib.decompress(base64.b64decode(blob)))) as z:
        return {k: z[k] for k in z.files}


# ------------------------------------------------------------------------------------------------ M0
@dataclass
class SituationTable:
    """The league-average situation table of one handedness group."""

    arrays: dict[str, np.ndarray]
    meta: dict[str, Any]
    shape: pc.Shape = field(default_factory=pc.Shape)

    @staticmethod
    def from_rows(rows: Sequence[Mapping[str, Any]]) -> "SituationTable":
        arrays = unpack_arrays(rows)
        meta = json.loads(str(arrays.pop("meta")))
        return SituationTable(arrays, meta)

    @property
    def group(self) -> str:
        return self.meta["group"]

    def state(self, balls: int, strikes: int, outs: int, bases: int, prev: str, streak: int) -> int:
        sh = self.shape
        if not (0 <= balls < sh.n_balls and 0 <= strikes < sh.n_strikes and 0 <= outs <= 2 and 0 <= bases <= 7):
            raise QueryError("count, outs or bases out of range / 球數、出局數或壘包超出範圍")
        if not 1 <= int(streak) <= 3:
            raise QueryError("streak must be 1, 2 or 3 (3 = three or more) / 連投顆數必須是 1、2、3（3＝三顆以上）")
        first = balls == 0 and strikes == 0
        if prev in ("", "none", None):
            if not first:
                raise QueryError("only the first pitch of a plate appearance has no previous pitch / 只有打席第一球沒有前一球")
            return int(sh.start_index(outs, bases))
        if prev not in PITCH_TYPES:
            raise QueryError(f"unknown previous pitch type {prev!r}; choose from {list(PITCH_TYPES)} or none / 未知的前一球球種")
        if first:
            raise QueryError("the first pitch of a plate appearance has no previous pitch / 打席第一球沒有前一球")
        return int(sh.index(balls, strikes, outs, bases, PITCH_TYPES.index(prev), int(streak) - 1))

    def rows(self, state: int, *, arrays: Mapping[str, np.ndarray] | None = None, evidence: str | None = None) -> list[dict[str, Any]]:
        """One dict per pitch type for ``state`` (from this table, or from ``arrays`` of the same layout for a player query)."""

        a = self.arrays if arrays is None else arrays
        tau = float(self.meta["tau"])
        sup = np.asarray(a["support"][state]).astype(bool)
        n = a["n"][state]
        out = []
        classes = self.meta["classes"]
        detected = pc.detect(a["advantage"][state], a["adv_lo"][state], a["adv_hi"][state], sup, tau)
        groups = indistinct_groups(a["q_lo"][state], a["q_hi"][state], sup)
        min_count = float(self.meta["min_count"])
        for ai, name in enumerate(PITCH_TYPES):
            flag = "unsupported" if not sup[ai] else ("sufficient" if n[ai] >= min_count else "extrapolated")
            row = {"action": name, "flag": flag, "supported": bool(sup[ai]), "q": _f(a["q"][state, ai]), "q_lo": _f(a["q_lo"][state, ai]), "q_hi": _f(a["q_hi"][state, ai]),
                   "advantage": _f(a["advantage"][state, ai]), "adv_lo": _f(a["adv_lo"][state, ai]), "adv_hi": _f(a["adv_hi"][state, ai]),
                   "marginal": _f(a["marginal"][state, ai]), "m_lo": _f(a["m_lo"][state, ai]), "m_hi": _f(a["m_hi"][state, ai]),
                   "hr_probability": _f(a["p_outcome"][state, ai, classes.index("in_play_home_run")]), "hr_lo": _f(a["hr_lo"][state, ai]), "hr_hi": _f(a["hr_hi"][state, ai]),
                   "n": int(n[ai]), "observed_share": _f(a["obs"][state, ai]), "trust_share": _f(a["dp_trust"][state, ai]), "detected": bool(detected[ai]),
                   "indistinct_group": groups[ai], "evidence": evidence or ("建模期偵測到（尚未經 2025 檢驗）" if detected[ai] else "無可偵測差異"),
                   "outcomes": {c: _f(a["p_outcome"][state, ai, ci]) for ci, c in enumerate(classes)}}
            out.append(row)
        return out

    def lookup(self, balls: int, strikes: int, outs: int, bases: Any, prev: str, streak: int) -> dict[str, Any]:
        state = self.state(balls, strikes, outs, parse_bases(bases), prev, streak)
        return {"mode": "M0", "group": self.group, "state": state, "rows": self.rows(state), "players": {}, "not_covered": list(NOT_COVERED)}


def _f(x: Any) -> float | None:
    x = float(x)
    return x if np.isfinite(x) else None


def indistinct_groups(lo: np.ndarray, hi: np.ndarray, supported: np.ndarray) -> list[str | None]:
    """Letters for the supported actions whose value intervals overlap (chained): actions with the same letter cannot be told apart. Unsupported actions get ``None``."""

    idx = [i for i in range(len(lo)) if supported[i] and np.isfinite(lo[i]) and np.isfinite(hi[i])]
    idx.sort(key=lambda i: (lo[i] + hi[i]))
    out: list[str | None] = [None] * len(lo)
    letter, reach = -1, -np.inf
    for i in idx:
        if lo[i] > reach:
            letter += 1; reach = hi[i]
        else:
            reach = max(reach, hi[i])
        out[i] = chr(ord("A") + letter)
    return out


# ------------------------------------------------------------------------------------------------ the model package
@dataclass
class PackageModel:
    members: dict[str, list[gbm.FittedGbm]]
    profile: Any
    meta: dict[str, Any]

    @staticmethod
    def from_rows(rows: Sequence[Mapping[str, Any]]) -> "PackageModel":
        parts: dict[tuple[str, int], list[tuple[int, str]]] = {}
        for r in rows:
            parts.setdefault((r["level"], int(r["member"])), []).append((int(r["part"]), r["payload"]))
        raw = {key: zlib.decompress(base64.b64decode("".join(p for _, p in sorted(v)))) for key, v in parts.items()}
        meta = json.loads(raw.pop(("__meta__", 0)).decode("utf-8"))
        profile = pickle.loads(raw.pop(("__profile__", 0))) if ("__profile__", 0) in raw else None
        members: dict[str, list[gbm.FittedGbm]] = {}
        for (level, i), blob in sorted(raw.items()):
            members.setdefault(level, []).append(gbm.FittedGbm.from_bytes(blob))
        return PackageModel(members, profile, meta)


@dataclass
class PlayerInfo:
    id: Any
    side: str
    pitches: int
    shrinkage: float | None
    arsenal: dict[str, int] = field(default_factory=dict)


class PlayerQuery:
    """M1-M3 for one handedness group."""

    def __init__(self, table: SituationTable, package: PackageModel, env: pc.Environment | None = None):
        if package.profile is None:
            raise QueryError("the model package has no player descriptors / 模型包沒有球員描述")
        if package.meta["group"] != table.group:
            raise QueryError("the table and the model package belong to different groups / 情境表與模型包的分組不同")
        self.table, self.package, self.shape = table, package, table.shape
        a = table.arrays
        self.env = env or pc.Environment(re_pa=a["re_pa"], re_cnt=a["re_cnt"], jbip=a["jbip"], start=a["start"])
        dyn = pc.Dynamics(P=a["p_outcome"].copy(), obs=a["obs"].copy(), support=a["support"].astype(bool), n=a["n"])
        self._base_obs, self._base_support, self._base_P = a["obs"], a["support"].astype(bool), a["p_outcome"]
        self.engine = pc.Engine(self.shape, dyn, self.env)
        self.classes = package.meta["classes"]

    # --- who exists
    def player_info(self, side: str, ident: Any) -> PlayerInfo:
        prof = self.package.profile; group = self.table.group
        ids, n = prof.players(side, group)
        pos = np.nonzero(ids == ident)[0]
        if not len(pos):
            raise QueryError(f"{side} {ident} has no pitches in group {group} / {side} {ident} 在分組 {group} 沒有資料")
        pitches = int(n[pos[0]])
        name = "p_whiff_per_swing" if side == "pitcher" else "b_swing_rate"
        k = float(prof.k_group.get(name, 50.0))
        arsenal = prof.pitcher_arsenal(ident, group) if side == "pitcher" else {}
        return PlayerInfo(ident, side, pitches, pitches / (pitches + k) if pitches + k > 0 else None, arsenal)

    def _draw(self, side: str, n: int, rng: np.random.Generator, min_pitches: int) -> np.ndarray:
        ids, cnt = self.package.profile.players(side, self.table.group)
        keep = cnt >= min_pitches
        if keep.sum() == 0:
            keep = cnt > 0
        ids, cnt = ids[keep], cnt[keep].astype(float)
        return rng.choice(ids, size=n, p=cnt / cnt.sum())

    def _arsenal_mask(self, pitcher: Any, min_share: float, min_count: int) -> np.ndarray:
        arsenal = self.package.profile.pitcher_arsenal(pitcher, self.table.group)
        total = sum(arsenal.values())
        return np.array([arsenal.get(t, 0) >= min_count and arsenal.get(t, 0) >= min_share * max(total, 1) for t in PITCH_TYPES])

    # --- the query
    def query(self, balls: int, strikes: int, outs: int, bases: Any, prev: str, streak: int, *, pitcher: Any = None, batter: Any = None, level: str = "L3",
              samples: int = 100, members: int = 10, intervals: int = 5, seed: int = 20240601, min_pitches: int = 300, max_rows: int = 600_000) -> dict[str, Any]:
        """M0 (nobody named) is the table lookup. Otherwise: sample pairs of players (the named player fixed, the other drawn by how many pitches he saw), predict the
        outcome probabilities of the plate appearance's states with the group's league model, solve each pair, average. The interval is the spread of ``intervals``
        independent groups of pairs, each predicted by its own subset of the bagged members (M3: the spread of the members)."""

        if pitcher is None and batter is None:
            return self.table.lookup(balls, strikes, outs, bases, prev, streak)
        if level not in self.package.members:
            raise QueryError(f"the package has no level {level}; available {sorted(self.package.members)} / 模型包沒有階 {level}")
        base = parse_bases(bases)
        state = self.table.state(balls, strikes, outs, base, prev, streak)
        mode = "M3" if pitcher is not None and batter is not None else ("M1" if pitcher is not None else "M2")
        infos = {}
        if pitcher is not None:
            infos["pitcher"] = self.player_info("pitcher", pitcher)
        if batter is not None:
            infos["batter"] = self.player_info("batter", batter)
        rng = np.random.default_rng(seed)
        n_pairs = 1 if mode == "M3" else int(samples)
        pit = np.full(n_pairs, pitcher, dtype=object) if pitcher is not None else self._draw("pitcher", n_pairs, rng, min_pitches).astype(object)
        bat = np.full(n_pairs, batter, dtype=object) if batter is not None else self._draw("batter", n_pairs, rng, min_pitches).astype(object)
        sh = self.shape
        b, k, o, bs, p, r = sh.coords()
        # the states of this plate appearance: outs and bases do not change inside it
        active = np.nonzero((o == outs) & (bs == base) & ((p == sh.none) == ((b == 0) & (k == 0))) & ((p != sh.none) | (r == 0)))[0]
        n_act = len(active)
        avail = len(self.package.members[level])
        use_members = max(1, min(int(members), avail))
        results: list[tuple[int, dict[str, np.ndarray]]] = []
        if mode == "M3":
            cols = self._cols(active, pit, bat)
            x, _ = lc.level_matrix(self.package.profile, cols, level, "outcome", history=False)
            for g in range(use_members):
                proba = self.package.members[level][g].predict_proba(x).reshape(n_act, sh.n_actions, -1)
                results.append((g, self._solve_pair(proba, active, state, pit[0])))
            K = use_members
        else:
            K = max(1, min(int(intervals), n_pairs, use_members))
            per_group = max(1, use_members // K)
            batch = max(1, max_rows // (n_act * sh.n_actions))
            for j0 in range(0, n_pairs, batch):
                idx = np.arange(j0, min(n_pairs, j0 + batch))
                cols = self._cols(active, pit[idx], bat[idx])
                x, _ = lc.level_matrix(self.package.profile, cols, level, "outcome", history=False)
                group_of = idx % K
                for g in np.unique(group_of):
                    chosen = [(g * per_group + t) % avail for t in range(per_group)]
                    mine = np.repeat(group_of == g, n_act * sh.n_actions)
                    proba = np.mean([self.package.members[level][m].predict_proba(x[mine]) for m in chosen], axis=0)
                    proba = proba.reshape(-1, n_act, sh.n_actions, proba.shape[-1])
                    for jj, pair in enumerate(idx[group_of == g]):
                        results.append((int(g), self._solve_pair(proba[jj], active, state, pit[pair])))
        return self._assemble(mode, state, results, K, infos, level, n_pairs, use_members)

    def _cols(self, active: np.ndarray, pitchers: np.ndarray, batters: np.ndarray) -> dict[str, np.ndarray]:
        """Feature columns of every (active state, pitch type) row, repeated for each sampled pair of players."""

        sh = self.shape
        b, k, o, bs, p, r = (c[active] for c in sh.coords())
        A = sh.n_actions
        rows = np.repeat(np.arange(len(active)), A); act = np.tile(np.arange(A), len(active))
        same = (p[rows] == act) & (p[rows] < A)
        one = dict(game_pk=np.zeros(len(rows), dtype=np.int64), balls=b[rows].astype(float), strikes=k[rows].astype(float), outs_when_up=o[rows].astype(float), bases=bs[rows],
                   streak_pos=np.where(same, np.minimum(r[rows] + 2, 3), 1).astype(float), prev1_pitch_type=np.array(list(PITCH_TYPES) + ["none"])[np.minimum(p[rows], A)],
                   pitch_type=np.array(PITCH_TYPES)[act], group=np.full(len(rows), self.table.group))
        cols = {key: np.tile(v, len(pitchers)) for key, v in one.items()}
        cols["pitcher"] = np.repeat(np.asarray(pitchers, dtype=object), len(rows)); cols["batter"] = np.repeat(np.asarray(batters, dtype=object), len(rows))
        return cols

    def _solve_pair(self, proba: np.ndarray, active: np.ndarray, state: int, pitcher: Any) -> dict[str, np.ndarray]:
        """Outcome probabilities [n_act, A, classes of the package] -> the numbers at the queried state for one pair of players."""

        sh, meta = self.shape, self.table.meta
        P = np.zeros((len(active), sh.n_actions, pc.C))
        for ci, name in enumerate(self.classes):
            P[:, :, pc.OC[name]] = proba[:, :, ci]
        P /= P.sum(axis=2, keepdims=True)
        sup = self._base_support[active].copy()
        mask = self._arsenal_mask(pitcher, meta["min_share"], int(meta["min_count"]))     # the pitcher's own arsenal limits what he can throw
        if mask.any():
            sup &= mask[None, :]
        dyn = self.engine.dyn
        dyn.P[active] = P
        dyn.obs[active] = pc.masked_shares(self._base_obs[active], sup); dyn.support[active] = sup
        eng = self.engine.rebind(dyn)
        V = eng.evaluate(eng.obs_eff, active=active, tol=1e-10)
        Q = eng.q(V, rows=np.array([state]))[0]
        o_s, s_s = eng.obs_eff[state], eng.sup_eff[state]
        out = {"q": Q, "adv": pc.advantage(Q[None], o_s[None])[0], "marg": pc.marginal_values(Q[None], o_s[None], float(meta["delta"]))[0],
               "trust": pc.trust_shares(Q[None], o_s[None], s_s[None], float(meta["trust"]))[0], "obs": o_s.copy(), "sup": s_s.copy(), "p": P[list(active).index(state)]}
        for key in ("q", "adv", "marg", "trust"):
            out[key] = np.where(s_s, out[key], np.nan)           # an action this pitcher cannot throw is not averaged in
        return out

    def _assemble(self, mode, state, results, K, infos, level, n_pairs, n_members):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)           # an action no sampled pair can throw is an empty mean: it stays NaN and is reported as unsupported
            return self._assemble_rows(mode, state, results, K, infos, level, n_pairs, n_members)

    def _assemble_rows(self, mode, state, results, K, infos, level, n_pairs, n_members):
        from scipy import stats

        sh = self.shape; A = sh.n_actions; classes = self.table.meta["classes"]

        def by_group(key, shape):
            out = np.full((K,) + shape, np.nan)
            for g in range(K):
                vals = [r[key] for gg, r in results if gg == g]
                if vals:
                    with np.errstate(all="ignore"):
                        out[g] = np.nanmean(np.stack(vals), axis=0)
            return out

        stack = {k_: by_group(k_, (A,)) for k_ in ("q", "adv", "marg", "obs", "trust")}
        stack["p"] = by_group("p", (A, pc.C))
        conf = float(self.table.meta["confidence"])
        t = float(stats.t.ppf(0.5 + conf / 2, max(K - 1, 1))) if K > 1 else 0.0

        def interval(arr):
            with np.errstate(all="ignore"):
                m = np.nanmean(arr, axis=0)
                sd = np.nanstd(arr, axis=0, ddof=1) if K > 1 else np.zeros_like(m)
            return m, m - t * sd, m + t * sd

        q, qlo, qhi = interval(stack["q"]); adv, alo, ahi = interval(stack["adv"]); mg, mlo, mhi = interval(stack["marg"])
        hr_m, hr_lo, hr_hi = interval(stack["p"][:, :, pc.OC["in_play_home_run"]])
        sup_share = np.mean([r["sup"] for _, r in results], axis=0)
        with np.errstate(all="ignore"):
            p_mean = np.nanmean(stack["p"], axis=0)
            obs_mean = np.nanmean(stack["obs"], axis=0); trust_mean = np.nanmean(stack["trust"], axis=0)
        arrays = {"q": q[None], "q_lo": qlo[None], "q_hi": qhi[None], "advantage": adv[None], "adv_lo": alo[None], "adv_hi": ahi[None], "marginal": mg[None], "m_lo": mlo[None], "m_hi": mhi[None],
                  "hr_lo": hr_lo[None], "hr_hi": hr_hi[None], "p_outcome": p_mean[None], "support": (sup_share > 0)[None], "n": self.table.arrays["n"][state][None],
                  "obs": obs_mean[None], "dp_trust": trust_mean[None]}
        rows = self.table.rows(0, arrays=arrays, evidence="個別化估計（建模期，尚未經 2025 檢驗）")
        for row, share in zip(rows, sup_share):
            row["pair_support_share"] = float(share)
        return {"mode": mode, "group": self.table.group, "state": state, "level": level, "rows": rows,
                "players": {s: {"id": str(i.id), "pitches": i.pitches, "shrinkage": i.shrinkage, "arsenal": i.arsenal} for s, i in infos.items()},
                "sampling": {"pairs": n_pairs, "members": n_members, "intervals": K,
                             "note": "interval = spread of the members (one pair)" if mode == "M3" else "interval = spread of independent groups of sampled pairs, each with its own members"},
                "not_covered": list(NOT_COVERED)}


# ------------------------------------------------------------------------------------------------ player names (plan 3.9)
import re  # noqa: E402

_VERBS = (r"(?:singles|doubles|triples|homers|grounds|flies|lines|pops|walks|strikes out|called out|hit by pitch|reaches|safe|bunts|sacrifice|intentionally|fouls out|"
          r"out on|double plays|triple plays|grounded|flied|lined|popped|struck out|walked|singled|doubled|tripled|homered|hits a|is hit|catcher interference|fielder's choice)")
_DES = re.compile(rf"^\s*(.+?)\s+{_VERBS}\b", re.IGNORECASE)


def batter_name_from_des(des: Any) -> str | None:
    """The batter's name from the description of the last pitch of a plate appearance ('Aaron Judge homers (1) on a fly ball ...'); None when it cannot be read."""

    if not des:
        return None
    m = _DES.match(str(des))
    if not m:
        return None
    name = m.group(1).strip()
    return name if 2 <= len(name) <= 40 and not name.lower().startswith(("with ", "the ")) else None


def pitcher_name(player_name: Any) -> str | None:
    """'Skenes, Paul' -> 'Paul Skenes'."""

    if not player_name:
        return None
    text = str(player_name).strip()
    if "," in text:
        last, first = [t.strip() for t in text.split(",", 1)]
        return f"{first} {last}".strip()
    return text


def build_names(db_path: Any, scope: Mapping[str, Any]) -> dict[str, Any]:
    """Names of the pitchers (``player_name``) and batters (read from ``des``) of the pitches in ``scope``, with how many players each side could be named for."""

    import sqlite3
    from collections import Counter, defaultdict
    from .scope import scope_where_sql

    where, params = scope_where_sql(scope)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        pitchers: dict[Any, Counter] = defaultdict(Counter); batters: dict[Any, Counter] = defaultdict(Counter)
        pitcher_ids, batter_ids = set(), set()
        have = {r[1] for r in conn.execute("PRAGMA table_info(pitches)")}
        if "player_name" in have:
            for pid, name in conn.execute(f"SELECT pitcher, player_name FROM pitches WHERE {where} GROUP BY pitcher, player_name", params):
                pitcher_ids.add(pid)
                nm = pitcher_name(name)
                if nm:
                    pitchers[pid][nm] += 1
        else:
            pitcher_ids = {r[0] for r in conn.execute(f"SELECT DISTINCT pitcher FROM pitches WHERE {where}", params)}
        for bid in conn.execute(f"SELECT DISTINCT batter FROM pitches WHERE {where}", params):
            batter_ids.add(bid[0])
        if "des" in have:
            for bid, des in conn.execute(f"SELECT batter, des FROM pitches WHERE {where} AND events IS NOT NULL AND des IS NOT NULL", params):
                nm = batter_name_from_des(des)
                if nm:
                    batters[bid][nm] += 1
    finally:
        conn.close()
    rows = [{"side": "pitcher", "id": str(i), "name": c.most_common(1)[0][0]} for i, c in pitchers.items()]
    rows += [{"side": "batter", "id": str(i), "name": c.most_common(1)[0][0]} for i, c in batters.items()]
    coverage = {"pitcher": len(pitchers) / len(pitcher_ids) if pitcher_ids else float("nan"), "batter": len(batters) / len(batter_ids) if batter_ids else float("nan"),
                "pitchers": len(pitcher_ids), "batters": len(batter_ids)}
    return {"rows": rows, "coverage": coverage}


def format_table(result: Mapping[str, Any]) -> str:
    """The nine rows of a query as a plain-text table (command line)."""

    def num(v, digits=4):
        return "   -  " if v is None else f"{v:.{digits}f}"

    lines = [f"mode {result['mode']}  group {result['group']}" + (f"  level {result['level']}" if result.get("level") else "")]
    for side, info in (result.get("players") or {}).items():
        lines.append(f"{side}: {info.get('name') or info['id']}  pitches {info['pitches']}  shrinkage {num(info['shrinkage'], 2)}")
    for note in result.get("notice") or []:
        lines.append(f"! {note}")
    lines.append("action flag         Q [99% interval]            advantage   marginal(+10pp)  HR/pitch  n      group  detected")
    for r in result["rows"]:
        lines.append(f"{r['action']:<6} {r['flag']:<12} {num(r['q'])} [{num(r['q_lo'])}, {num(r['q_hi'])}]  {num(r['advantage'])}   {num(r['marginal'])}        {num(r['hr_probability'])}   "
                     f"{r['n']:<6} {r['indistinct_group'] or '-':<5}  {'yes' if r['detected'] else 'no'}")
    lines.append("not covered: " + "; ".join(result.get("not_covered") or []))
    return "\n".join(lines)
