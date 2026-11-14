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

    def rows(self, state: int, *, arrays: Mapping[str, np.ndarray] | None = None) -> list[dict[str, Any]]:
        """One dict per pitch type for ``state`` (from this table, or from ``arrays`` of the same layout for a player query)."""

        a = self.arrays if arrays is None else arrays
        sup = np.asarray(a["support"][state]).astype(bool)
        n = a["n"][state]
        out = []
        classes = self.meta["classes"]
        groups = overlap_groups(a["q_lo"][state], a["q_hi"][state], sup)
        min_count = float(self.meta["min_count"])
        for ai, name in enumerate(PITCH_TYPES):
            flag = "unsupported" if not sup[ai] else ("sufficient" if n[ai] >= min_count else "extrapolated")
            row = {"action": name, "flag": flag, "supported": bool(sup[ai]), "q": _f(a["q"][state, ai]), "q_lo": _f(a["q_lo"][state, ai]), "q_hi": _f(a["q_hi"][state, ai]),
                   "advantage": _f(a["advantage"][state, ai]), "adv_lo": _f(a["adv_lo"][state, ai]), "adv_hi": _f(a["adv_hi"][state, ai]),
                   "marginal": _f(a["marginal"][state, ai]), "m_lo": _f(a["m_lo"][state, ai]), "m_hi": _f(a["m_hi"][state, ai]),
                   "hr_probability": _f(a["p_outcome"][state, ai, classes.index("in_play_home_run")]), "hr_lo": _f(a["hr_lo"][state, ai]), "hr_hi": _f(a["hr_hi"][state, ai]),
                   "n": int(n[ai]), "observed_share": _f(a["obs"][state, ai]), "trust_share": _f(a["dp_trust"][state, ai]), "overlap_group": groups[ai],
                   "outcomes": {c: _f(a["p_outcome"][state, ai, ci]) for ci, c in enumerate(classes)}}
            out.append(row)
        return out

    def lookup(self, balls: int, strikes: int, outs: int, bases: Any, prev: str, streak: int) -> dict[str, Any]:
        state = self.state(balls, strikes, outs, parse_bases(bases), prev, streak)
        rows = self.rows(state)
        return {"mode": "M0", "group": self.group, "state": state, "rows": rows, "players": {}, "comparisons": self.comparisons(rows), "not_covered": list(NOT_COVERED)}

    def comparisons(self, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        """How many pitch types were compared in this answer, how many have an advantage interval that excludes 0, and how many would by luck alone."""

        sup = [r for r in rows if r["supported"]]
        lo = [np.nan if r["adv_lo"] is None else r["adv_lo"] for r in sup]; hi = [np.nan if r["adv_hi"] is None else r["adv_hi"] for r in sup]
        return pc.chance_summary(lo, hi, float(self.meta["confidence"]))


def _f(x: Any) -> float | None:
    x = float(x)
    return x if np.isfinite(x) else None


def overlap_groups(lo: np.ndarray, hi: np.ndarray, supported: np.ndarray) -> list[str | None]:
    """Letters for the supported actions whose value intervals overlap (chained). Same letter = the intervals overlap, so the data cannot tell the pitch types
    in the group apart in value: a description, not a verdict. Unsupported actions get ``None``."""

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


# ------------------------------------------------------------------------------------------------ the old league_model package (reading only)
@dataclass
class PackageModel:
    """The bagged members and the player descriptors that the old ``league_model`` method stores with ``save_package``. Situation queries no longer use it (they ask the model
    of ``outcome_model_train``); it stays so that the stored runs of the old method can still be read."""

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


# ------------------------------------------------------------------------------------------------ player queries (M1-M3)
@dataclass
class PlayerInfo:
    id: Any
    side: str
    pitches: int
    shrinkage: float | None
    arsenal: dict[str, int] = field(default_factory=dict)
    known: bool = True


class PlayerQuery:
    """M1-M3 for one handedness group: the trained outcome model is asked about the named players with ``effects="specific"`` (a player it has not seen, or one who is not
    named, counts as the league average), and the plate appearance is solved once for every bagged member. The interval is the spread of the members."""

    def __init__(self, table: SituationTable, model: Any, env: pc.Environment | None = None):
        if model.group is not None and model.group != table.group:
            raise QueryError("the table and the model belong to different groups / 情境表與模型的分組不同")
        if not set(model.classes) <= set(pc.OUTCOMES):
            raise QueryError("the model has outcome classes the decision engine does not know / 模型的結果類別決策引擎不認得")
        self.table, self.model, self.shape = table, model, table.shape
        a = table.arrays
        self.env = env or pc.Environment(re_pa=a["re_pa"], re_cnt=a["re_cnt"], jbip=a["jbip"], start=a["start"])
        dyn = pc.Dynamics(P=a["p_outcome"].copy(), obs=a["obs"].copy(), support=a["support"].astype(bool), n=a["n"])
        self._base_obs, self._base_support, self._base_P = a["obs"], a["support"].astype(bool), a["p_outcome"]
        self.engine = pc.Engine(self.shape, dyn, self.env)
        self.classes = list(model.classes)

    # --- who exists
    def player_info(self, side: str, ident: Any) -> PlayerInfo:
        i = self.model.player_info(side, ident)
        return PlayerInfo(i["id"], side, i["pitches"], i["shrinkage"], i["arsenal"], i["known"])

    def _arsenal_mask(self, arsenal: Mapping[str, int], min_share: float, min_count: int) -> np.ndarray:
        total = sum(arsenal.values())
        return np.array([arsenal.get(t, 0) >= min_count and arsenal.get(t, 0) >= min_share * max(total, 1) for t in PITCH_TYPES])

    # --- the query
    def query(self, balls: int, strikes: int, outs: int, bases: Any, prev: str, streak: int, *, pitcher: Any = None, batter: Any = None, members: int | None = None,
              seed: int = 20240601, **_unused: Any) -> dict[str, Any]:
        """M0 (nobody named) is the table lookup. Otherwise the model predicts the outcome probabilities of every state of the plate appearance for the named players, and the
        plate appearance is solved for each bagged member (``members`` limits how many are used)."""

        if pitcher is None and batter is None:
            return self.table.lookup(balls, strikes, outs, bases, prev, streak)
        base = parse_bases(bases)
        state = self.table.state(balls, strikes, outs, base, prev, streak)
        mode = "M3" if pitcher is not None and batter is not None else ("M1" if pitcher is not None else "M2")
        infos = {}
        if pitcher is not None:
            infos["pitcher"] = self.player_info("pitcher", pitcher)
        if batter is not None:
            infos["batter"] = self.player_info("batter", batter)
        sh = self.shape
        b, k, o, bs, p, r = sh.coords()
        # the states of this plate appearance: outs and bases do not change inside it
        active = np.nonzero((o == outs) & (bs == base) & ((p == sh.none) == ((b == 0) & (k == 0))) & ((p != sh.none) | (r == 0)))[0]
        n_act = len(active)
        pid = infos["pitcher"].id if "pitcher" in infos and infos["pitcher"].known else -1          # not named or not seen: the league-average player
        bid = infos["batter"].id if "batter" in infos and infos["batter"].known else -1
        proba = self.model.predict_members(self._cols(active, int(pid), int(bid)), effects="specific", seed=seed)          # [members, n_act * A, classes]
        use = proba.shape[0] if members is None else max(1, min(int(members), proba.shape[0]))
        arsenal = infos["pitcher"].arsenal if "pitcher" in infos else {}
        results = [(g, self._solve_pair(proba[g].reshape(n_act, sh.n_actions, -1), active, state, arsenal)) for g in range(use)]
        out = self._assemble(mode, state, results, use, infos, None, 1, use)
        out["notice"] = [f"{'投手' if s == 'pitcher' else '打者'} {i.id}：模型沒有這位球員的資料，答案等於聯盟平均 / no data for this {s}: the answer is the league average"
                         for s, i in infos.items() if not i.known]
        return out

    def _cols(self, active: np.ndarray, pitcher: int, batter: int) -> dict[str, np.ndarray]:
        """Feature columns of every (active state, pitch type) row, with the pitcher and the batter filled in (-1 = nobody: the league average)."""

        sh = self.shape
        b, k, o, bs, p, r = (c[active] for c in sh.coords())
        A = sh.n_actions
        rows = np.repeat(np.arange(len(active)), A); act = np.tile(np.arange(A), len(active))
        same = (p[rows] == act) & (p[rows] < A)
        return dict(game_pk=np.zeros(len(rows), dtype=np.int64), balls=b[rows].astype(float), strikes=k[rows].astype(float), outs_when_up=o[rows].astype(float), bases=bs[rows],
                    streak_pos=np.where(same, np.minimum(r[rows] + 2, 3), 1).astype(float), prev1_pitch_type=np.array(list(PITCH_TYPES) + ["none"])[np.minimum(p[rows], A)],
                    pitch_type=np.array(PITCH_TYPES)[act], group=np.full(len(rows), self.table.group), pitcher=np.full(len(rows), pitcher, dtype=np.int64),
                    batter=np.full(len(rows), batter, dtype=np.int64))

    def _solve_pair(self, proba: np.ndarray, active: np.ndarray, state: int, arsenal: Mapping[str, int]) -> dict[str, np.ndarray]:
        """Outcome probabilities [n_act, A, classes of the model] -> the numbers at the queried state for one member."""

        sh, meta = self.shape, self.table.meta
        P = np.zeros((len(active), sh.n_actions, pc.C))
        for ci, name in enumerate(self.classes):
            P[:, :, pc.OC[name]] = proba[:, :, ci]
        P /= P.sum(axis=2, keepdims=True)
        sup = self._base_support[active].copy()
        mask = self._arsenal_mask(arsenal, meta["min_share"], int(meta["min_count"])) if arsenal else np.zeros(len(PITCH_TYPES), dtype=bool)      # the pitcher's own arsenal limits what he can throw
        if mask.any():
            sup &= mask[None, :]
        dyn = self.engine.dyn
        dyn.P[active] = P
        dyn.obs[active] = np.where(sup.any(axis=1, keepdims=True), pc.masked_shares(self._base_obs[active], sup), self._base_obs[active]); dyn.support[active] = sup       # no supported action: keep what was seen
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
        rows = self.table.rows(0, arrays=arrays)
        for row, share in zip(rows, sup_share):
            row["pair_support_share"] = float(share)
        return {"mode": mode, "group": self.table.group, "state": state, "level": level, "rows": rows, "comparisons": self.table.comparisons(rows),
                "players": {s: {"id": str(i.id), "pitches": i.pitches, "shrinkage": i.shrinkage, "arsenal": i.arsenal, "known": i.known} for s, i in infos.items()},
                "sampling": {"pairs": n_pairs, "members": n_members, "intervals": K, "note": "interval = spread of the bagged members of the trained model"},
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
    lines.append("action flag         Q [99% interval]            advantage [interval]               marginal(+10pp)  HR/pitch  n      overlap")
    for r in result["rows"]:
        lines.append(f"{r['action']:<6} {r['flag']:<12} {num(r['q'])} [{num(r['q_lo'])}, {num(r['q_hi'])}]  {num(r['advantage'])} [{num(r['adv_lo'])}, {num(r['adv_hi'])}]   "
                     f"{num(r['marginal'])}        {num(r['hr_probability'])}   {r['n']:<6} {r['overlap_group'] or '-'}")
    cmp_ = result.get("comparisons")
    if cmp_:
        lines.append(f"compared {cmp_['n']} pitch types; at {cmp_['confidence']:.0%} about {cmp_['expected_by_chance']:.2f} intervals would exclude 0 by luck alone; found {cmp_['found']}")
    lines.append("not covered: " + "; ".join(result.get("not_covered") or []))
    return "\n".join(lines)
