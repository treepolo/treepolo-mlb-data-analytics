"""Synthetic pitch worlds with known answers, the full-width pitches fixture, and the self-check behind `synthetic_check`."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

INGESTED = "2026-01-01T00:00:00+00:00"
DEFAULTS: dict[str, Any] = dict(
    pitch_uid=None, game_pk=1, at_bat_number=1, pitch_number=1, game_year=2024, game_type="R", game_date="2024-05-01",
    inning="1", inning_topbot="Top", pitcher=10, batter=100, pitch_type="FF", description="ball", events=None, des=None,
    release_speed=93.0, plate_x=0.0, plate_z=2.5, zone=5, spin_axis=None, pfx_x=0.0, pfx_z=0.0, release_spin_rate=2200.0,
    release_extension=6.5, launch_speed=None, launch_angle=None, bat_speed=None, swing_length=None, attack_angle=None,
    attack_direction=None, swing_path_tilt=None, intercept_ball_minus_batter_pos_x_inches=None,
    intercept_ball_minus_batter_pos_y_inches=None, miss_distance=None, p_throws="R", stand="R", balls=0, strikes=0,
    outs_when_up=0, on_1b=None, on_2b=None, on_3b=None, bat_score=0, post_bat_score=0, post_home_score=0, post_away_score=0,
    delta_run_exp=None, _ingested_at=INGESTED,
)
COLUMNS = tuple(DEFAULTS)
_TEXT = {"pitch_uid", "game_type", "game_date", "inning", "inning_topbot", "pitch_type", "description", "events", "des",
         "p_throws", "stand", "_ingested_at"}
_REAL = {"release_speed", "plate_x", "plate_z", "spin_axis", "pfx_x", "pfx_z", "release_spin_rate", "release_extension", "delta_run_exp", "launch_speed", "launch_angle", "bat_speed", "swing_length",
         "attack_angle", "attack_direction", "swing_path_tilt", "intercept_ball_minus_batter_pos_x_inches",
         "intercept_ball_minus_batter_pos_y_inches", "miss_distance"}


def _ddl() -> str:
    parts = []
    for name in COLUMNS:
        kind = "TEXT" if name in _TEXT else "REAL" if name in _REAL else "INTEGER"
        parts.append(f"{name} {kind}" + (" PRIMARY KEY" if name == "pitch_uid" else ""))
    return ", ".join(parts)


PITCHES_DDL = _ddl()


def make_row(**values: Any) -> tuple:
    unknown = set(values) - set(DEFAULTS)
    if unknown:
        raise KeyError(f"unknown pitches columns: {sorted(unknown)}")
    row = dict(DEFAULTS); row.update(values)
    if row["pitch_uid"] is None:
        row["pitch_uid"] = f'{row["game_pk"]}:{row["at_bat_number"]}:{row["pitch_number"]}'
    return tuple(row[c] for c in COLUMNS)


def create_pitches_db(path: Path, *, append: bool = False) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not append:
        path.unlink(missing_ok=True)
        for suffix in (".duckdb", ".duckdb.wal"):  # the DuckDB mirror is refreshed only when data_revision changes
            path.with_suffix(suffix).unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    if not append:
        conn.executescript(
            "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);"
            "INSERT INTO settings VALUES ('data_revision','rev-1','2026-01-01T00:00:00+00:00');"
            f"CREATE TABLE pitches ({PITCHES_DDL});"
        )
    return conn


def insert_rows(conn: sqlite3.Connection, rows: Iterable[tuple]) -> None:
    conn.executemany(f"INSERT INTO pitches ({','.join(COLUMNS)}) VALUES ({','.join('?' for _ in COLUMNS)})", rows)


def make_pitches_db(path: Path, rows: Iterable[tuple]) -> Path:
    conn = create_pitches_db(path)
    insert_rows(conn, rows)
    conn.commit(); conn.close()
    return Path(path)


OUT_DESCRIPTION = ("ball", "called_strike", "swinging_strike", "foul", "hit_into_play")

# name -> simulate() settings.  decay is the true drop in whiff-per-swing for every extra pitch of a same-type streak.
WORLDS: dict[str, dict] = {
    "T0": dict(decay=0.0, start_strikes=2, w_sl=(0.70, 0.05), w_ff=(0.70, 0.05), swing=0.8),    # zero decay, same hazards
    "T1": dict(decay=0.03, start_strikes=2, w_sl=(0.60, 0.30), w_ff=(0.60, 0.30), swing=0.8),   # true decay, same hazards
    "T2": dict(decay=0.0, start_strikes=2, w_sl=(0.75, 0.10), w_ff=(0.50, 0.02), swing=0.8),    # zero decay, FF/SL hazards differ
    "T3": dict(decay=0.03, start_strikes=2, w_sl=(0.65, 0.35), w_ff=(0.45, 0.20), swing=0.8),   # true decay, hazards differ
}


def simulate(n_pa=300_000, decay=0.0, start_strikes=0, w_sl=(0.45, 0.15), w_ff=(0.45, 0.15), p_sl=0.5, swing=0.5, seed=1, max_steps=25):
    """Per-pitch arrays for ``n_pa`` plate appearances against two latent batter types (A whiffy, B not).

    The whiff probability per swing for pitch type T and batter type b at streak position k is
    ``w_T[b] - decay*(k-1)`` (clipped to [0.01, 0.99]); a swing that is not a whiff is a foul (60%) or in play (40%);
    a take is a called strike (40%) or a ball. A PA ends on 3 strikes, 4 balls or a ball in play. Keys: pa, j (pitch
    index), balls, strikes (before the pitch), t (1 = SL, 0 = FF), k (true streak position), out (0 ball, 1 called
    strike, 2 whiff, 3 foul, 4 in play), btype, nsame (earlier same-type pitches in the PA), last, s_after, b_after.
    """

    rng = np.random.default_rng(seed)
    btype = (rng.random(n_pa) < 0.5).astype(int)
    balls = np.zeros(n_pa, int); strikes = np.full(n_pa, start_strikes, dtype=int)
    alive = np.ones(n_pa, bool); prev = np.full(n_pa, -1); streak = np.zeros(n_pa, int); cnt = np.zeros((n_pa, 2), int)
    rows = []
    for step in range(1, max_steps + 1):
        idx = np.nonzero(alive)[0]
        if idx.size == 0:
            break
        t = (rng.random(idx.size) < p_sl).astype(int)
        k = np.where(t == prev[idx], streak[idx] + 1, 1)
        base = np.where(t == 1, np.where(btype[idx] == 0, w_sl[0], w_sl[1]), np.where(btype[idx] == 0, w_ff[0], w_ff[1]))
        w = np.clip(base - decay * (k - 1), 0.01, 0.99)
        sw = rng.random(idx.size) < swing
        u = rng.random(idx.size)
        whiff = sw & (u < w)
        rest = sw & ~whiff
        inplay = rest & (rng.random(idx.size) < 0.4)
        foul = rest & ~inplay
        take = ~sw
        cs = take & (rng.random(idx.size) < 0.4)
        ball = take & ~cs
        out = np.select([ball, cs, whiff, foul, inplay], [0, 1, 2, 3, 4])
        s_new = strikes[idx] + ((out == 1) | (out == 2) | ((out == 3) & (strikes[idx] < 2)))
        b_new = balls[idx] + (out == 0)
        done = (s_new >= 3) | (b_new >= 4) | (out == 4)
        rows.append((idx.copy(), np.full(idx.size, step), balls[idx].copy(), strikes[idx].copy(), t, k, out, btype[idx].copy(),
                     cnt[idx, t].copy(), done.copy(), s_new.copy(), b_new.copy()))
        cnt[idx, t] += 1
        strikes[idx] = s_new; balls[idx] = b_new
        prev[idx] = t; streak[idx] = k
        alive[idx[done]] = False
    names = ("pa", "j", "balls", "strikes", "t", "k", "out", "btype", "nsame", "last", "s_after", "b_after")
    return {name: np.concatenate([r[i] for r in rows]) for i, name in enumerate(names)}


def world_rows(d, *, year=2024, pa_offset=0):
    """pitches rows for a simulated world. Every PA is its own game and its own half-inning (no run context needed)."""

    order = np.lexsort((d["j"], d["pa"]))
    for i in order:
        pa = int(d["pa"][i]) + pa_offset; j = int(d["j"][i]); out = int(d["out"][i])
        events = None
        if d["last"][i]:
            events = "field_out" if out == 4 else ("strikeout" if d["s_after"][i] >= 3 else "walk")
        yield make_row(
            pitch_uid=f"{pa}:{j}", game_pk=pa + 1, at_bat_number=1, pitch_number=j, game_year=year, game_date=f"{year}-05-01",
            pitch_type="SL" if d["t"][i] == 1 else "FF", description=OUT_DESCRIPTION[out], events=events,
            release_speed=85.0 if d["t"][i] == 1 else 94.0, batter=100 + int(d["btype"][i]),
            balls=int(d["balls"][i]), strikes=int(d["strikes"][i]),
        )


def write_world_db(path: Path, d, *, year=2024, append=False, pa_offset=0) -> int:
    conn = create_pitches_db(path, append=append)
    rows = list(world_rows(d, year=year, pa_offset=pa_offset))
    insert_rows(conn, rows)
    conn.commit(); conn.close()
    return len(rows)


def oracle_cells(d, *, pitch_type=1, kmax=5, exposure=False):
    """Numpy oracle for the whiff-per-swing cells of one pitch type: {(j, balls, strikes[, nsame], k): (swings, whiffs)}."""

    m = (d["t"] == pitch_type) & np.isin(d["out"], [2, 3, 4])      # swings of this pitch type
    columns = [d["j"][m], d["balls"][m], d["strikes"][m]] + ([d["nsame"][m]] if exposure else []) + [np.minimum(d["k"][m], kmax)]
    whiff = (d["out"][m] == 2).astype(int)
    keys, inverse = np.unique(np.column_stack(columns), axis=0, return_inverse=True)
    inverse = inverse.reshape(-1)
    swings = np.bincount(inverse, minlength=len(keys)); whiffs = np.bincount(inverse, weights=whiff, minlength=len(keys))
    return {tuple(int(x) for x in key): (int(swings[i]), int(whiffs[i])) for i, key in enumerate(keys)}


# ---------------------------------------------------------------------------------------------- self-check
MODEL_WORLDS: dict[str, dict] = {   # for the sequence-gain check: no decay vs a true decay of 0.04 per streak step
    "M0": dict(decay=0.0, start_strikes=0, w_sl=(0.60, 0.30), w_ff=(0.60, 0.30), swing=0.6),
    "M1": dict(decay=0.04, start_strikes=0, w_sl=(0.60, 0.30), w_ff=(0.60, 0.30), swing=0.6),
}
CHECK_GROUPS = ("oracle", "T0", "T1", "T2", "T3", "impossible", "placebo", "sequence_gain")
MIN_CHECK_PA = 300_000     # the tolerances below were set for this size


def _check(world: str, name: str, expected: str, observed: Any, passed: bool) -> dict[str, Any]:
    return {"world": world, "check": name, "expected": expected,
            "observed": None if observed is None else round(float(observed), 6), "passed": int(bool(passed))}


def _curve(db: Path, *, exposure: bool = False, pitch_types: Sequence[str] = ("SL",), cells_only: bool = False):
    from ..analysis import AnalysisEngine, Filter, PITCH_GRAIN, Source
    from ..analysis.pitch_table import build_pitch_table, streak_cells_node
    from .scope import scope_filter_expr
    from .streak import same_point_curve

    strata = ("pitch_index_in_pa", "balls", "strikes") + (("prior_same_type_count",) if exposure else ())
    table = build_pitch_table(Filter(Source("pitches", PITCH_GRAIN), scope_filter_expr({"game_years": [2024], "game_types": ["R"]})),
                              memory=1, prior_same_type_count=exposure)
    node = streak_cells_node(table, group_fields=(), strata_fields=strata, rate="whiff_per_swing", kmax=5, pitch_types=pitch_types)
    result = AnalysisEngine(db, analytics_database_path=db.with_suffix(".duckdb"), backend="duckdb").execute(node)
    if result.backend != "duckdb":
        raise RuntimeError("synthetic_check needs the DuckDB backend (the engine fell back to SQLite)")
    naive, same = same_point_curve(result.rows, group_fields=(), strata_fields=strata, kmax=5)
    return result, naive, {r["k"]: r for r in same}, strata


def run_checks(*, n_pa: int = MIN_CHECK_PA, seed: int = 1, workdir: Path | None = None,
               groups: Sequence[str] = CHECK_GROUPS) -> list[dict[str, Any]]:
    """Run the known-answer checks of the pipeline on synthetic worlds; every row says whether it passed."""

    if n_pa < MIN_CHECK_PA:
        raise ValueError(f"n_pa must be at least {MIN_CHECK_PA} (the tolerances were set for that size)")
    unknown = sorted(set(groups) - set(CHECK_GROUPS))
    if unknown:
        raise ValueError(f"unknown check groups {unknown}")
    own = tempfile.TemporaryDirectory() if workdir is None else None
    root = Path(own.name if own else workdir); root.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, Any]] = []
    try:
        worlds: dict[str, tuple] = {}

        def world(name):
            if name not in worlds:
                d = simulate(n_pa=n_pa, seed=seed, **WORLDS[name]); db = root / f"{name}.sqlite3"
                write_world_db(db, d); worlds[name] = (d, db)
            return worlds[name]

        if "oracle" in groups:
            d, db = world("T1")
            result, _, _, strata = _curve(db)
            ast = {tuple(r[f] for f in strata) + (int(r["k"]),): (int(r["n"]), int(r["sx"])) for r in result.rows if r["n"]}
            out.append(_check("T1", "AST cells equal the numpy oracle", "identical cell counts", len(ast), ast == oracle_cells(d, kmax=5)))
        if "T0" in groups:
            _, naive, same, _ = _curve(world("T0")[1]); means = {r["k"]: r["mean"] for r in naive}
            out.append(_check("T0", "naive curve fakes a decay (k=1 minus k=4)", "> 0.15", means[1] - means[4], means[1] - means[4] > 0.15))
            for k in (2, 3):
                out.append(_check("T0", f"same-point estimate k={k} is about zero", "|x| < 0.02", same[k]["estimate"], abs(same[k]["estimate"]) < 0.02))
        if "T1" in groups:
            _, _, same, _ = _curve(world("T1")[1])
            out.append(_check("T1", "same-point k=2 recovers -0.03", "-0.045 .. -0.020", same[2]["estimate"], -0.045 < same[2]["estimate"] < -0.020))
            out.append(_check("T1", "same-point k=3 recovers -0.06", "-0.080 .. -0.040", same[3]["estimate"], -0.080 < same[3]["estimate"] < -0.040))
            out.append(_check("T1", "interval at k=2 excludes zero", "hi < 0", same[2]["hi"], same[2]["hi"] < 0))
        if "T2" in groups:
            _, _, base, _ = _curve(world("T2")[1]); _, _, expo, _ = _curve(world("T2")[1], exposure=True)
            out.append(_check("T2", "differing hazards leave a bias (documented limitation) k=2", "< -0.03", base[2]["estimate"], base[2]["estimate"] < -0.03))
            out.append(_check("T2", "exposure stratum removes it, k=2", "|x| < 0.02", expo[2]["estimate"], abs(expo[2]["estimate"]) < 0.02))
            out.append(_check("T2", "exposure stratum removes it, k=3", "|x| < 0.03", expo[3]["estimate"], abs(expo[3]["estimate"]) < 0.03))
            out.append(_check("T2", "the exposure stratum costs coverage", "coverage drops", expo[2]["coverage"], expo[2]["coverage"] < base[2]["coverage"]))
        if "T3" in groups:
            _, _, expo, _ = _curve(world("T3")[1], exposure=True)
            out.append(_check("T3", "exposure same-point k=2 near the truth -0.03", "-0.06 .. -0.015", expo[2]["estimate"], -0.06 < expo[2]["estimate"] < -0.015))
        if "impossible" in groups:
            d = simulate(n_pa=20_000, seed=3, p_sl=1.0, **WORLDS["T1"]); db = root / "only_sl.sqlite3"; write_world_db(db, d)
            _, _, same, _ = _curve(db)
            out.append(_check("impossible", "an only-slider pitcher gives no comparable stratum", "all estimates None",
                              sum(r["estimate"] is None for r in same.values()), all(r["estimate"] is None and r["coverage"] == 0.0 for r in same.values())))
        if "placebo" in groups:
            out.extend(_placebo_checks(world, root))
        if "sequence_gain" in groups:
            out.extend(_gain_checks(root, seed))
        return out
    finally:
        if own:
            own.cleanup()


def _placebo_checks(world, root: Path) -> list[dict[str, Any]]:
    from ..analysis import AnalysisEngine, Binary, Case, Column, Filter, IsNull, Literal, NamedExpr, OrderKey, PITCH_GRAIN, Project, Sort, Source
    from ..analysis.pitch_table import build_pitch_table, rate_terms
    from .placebo import placebo_same_point
    from .scope import scope_filter_expr

    out = []
    for name in ("T0", "T1"):
        _, db = world(name)
        _, _, same, strata = _curve(db)
        table = build_pitch_table(Filter(Source("pitches", PITCH_GRAIN), scope_filter_expr({"game_years": [2024], "game_types": ["R"]})), memory=1)
        e, x = rate_terms("whiff_per_swing")
        keys = ("game_pk", "at_bat_number", "pitch_index_in_pa", "pitch_type") + strata[1:]
        node = Sort(Project(table, (NamedExpr("pitch_uid", Column("pitch_uid")),) + tuple(NamedExpr(f, Column(f)) for f in dict.fromkeys(keys))
                            + (NamedExpr("e", e), NamedExpr("x", Case(((Binary(e, "=", Literal(1)), x),), Literal(0)))), PITCH_GRAIN),
                    (OrderKey(Column("game_pk")), OrderKey(Column("at_bat_number")), OrderKey(Column("pitch_index_in_pa"))))
        result = AnalysisEngine(db, analytics_database_path=db.with_suffix(".duckdb"), backend="duckdb").execute(node)
        rows = placebo_same_point(list(result.rows), group_fields=(), strata_fields=strata, pitch_types=("SL",), kmax=5, shuffles=20, seed=1,
                                  observed={("SL", k): same[k]["estimate"] for k in same if same[k]["estimate"] is not None})
        p2 = next(r for r in rows if r["k"] == 2)
        if name == "T0":
            out.append(_check("T0", "placebo is centred near zero", "|mean| < 0.01", p2["placebo_mean"], abs(p2["placebo_mean"]) < 0.01))
            out.append(_check("T0", "observed k=2 lies inside the placebo band", "lo-0.01 .. hi+0.01", p2["observed"],
                              p2["placebo_lo"] - 0.01 < p2["observed"] < p2["placebo_hi"] + 0.01))
        else:
            out.append(_check("T1", "observed k=2 lies below the placebo band", "< placebo_lo", p2["observed"], p2["observed"] < p2["placebo_lo"]))
    return out


def _gain_checks(root: Path, seed: int) -> list[dict[str, Any]]:
    from ..analysis import AnalysisEngine, Column, Filter, NamedExpr, PITCH_GRAIN, Project, Source
    from ..analysis.pitch_table import build_pitch_table
    from .modeling import Design, add_derived, fit_predict, paired_game_bootstrap, row_logloss, DEFAULT_CLASS_MERGE
    from .scope import scope_filter_expr

    out = []
    for name, settings in MODEL_WORLDS.items():
        db = root / f"{name}.sqlite3"
        write_world_db(db, simulate(n_pa=100_000, seed=11, **settings), year=2023)
        write_world_db(db, simulate(n_pa=100_000, seed=12, **settings), year=2024, append=True, pa_offset=10_000_000)
        table = build_pitch_table(Filter(Source("pitches", PITCH_GRAIN), scope_filter_expr({"game_years": [2023, 2024], "game_types": ["R"]})), memory=2)
        fields = ("pitch_uid", "game_pk", "game_year", "outcome", "pitch_type", "balls", "strikes", "prev1_pitch_type", "streak_pos")
        result = AnalysisEngine(db, analytics_database_path=db.with_suffix(".duckdb"), backend="duckdb").execute(
            Project(table, tuple(NamedExpr(f, Column(f)) for f in fields), PITCH_GRAIN))
        if result.backend != "duckdb":
            raise RuntimeError("synthetic_check needs the DuckDB backend")
        rows = [dict(r) for r in result.rows]; add_derived(rows, 4)
        classes = sorted({DEFAULT_CLASS_MERGE[r["outcome"]] for r in rows}); y = np.array([classes.index(DEFAULT_CLASS_MERGE[r["outcome"]]) for r in rows])
        year = np.array([r["game_year"] for r in rows]); games = np.array([r["game_pk"] for r in rows])
        train, test = year == 2023, year == 2024
        losses = {}
        for variant, cats in (("base", ["pitch_type", "count_state"]), ("full", ["pitch_type", "count_state", "prev1_pitch_type", "streak_cap"])):
            tr = [r for r, f in zip(rows, train) if f]; te = [r for r, f in zip(rows, test) if f]
            design = Design([], cats).fit(tr)
            proba, _, _ = fit_predict("logistic", {"C": 1.0, "max_iter": 300}, design.transform(tr), y[train], design.transform(te), len(classes))
            losses[variant] = row_logloss(y[test], proba)
        mean, lo, hi, _ = paired_game_bootstrap(losses["base"] - losses["full"], games[test], reps=200, seed=seed)
        if name == "M0":
            out.append(_check("M0", "no decay: sequence features add (almost) nothing", "mean < 0.0002 and hi < 0.0003", mean, mean < 0.0002 and hi < 0.0003))
        else:
            out.append(_check("M1", "true decay: sequence features help (lower interval bound)", "lo > 0.0005", lo, lo > 0.0005))
    return out
