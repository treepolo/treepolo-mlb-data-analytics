"""Run-value fixture: hand-computable half-innings (appendix B of docs/PITCH_SEQUENCING_DEV_P3_P4.md)."""
from __future__ import annotations

from pathlib import Path

from pitch_fixtures import make_pitches_db, make_row

# Game 1, Top 1 (away bats; a home run scores 2), then Bot 9 (home hits a walk-off home run); game 2 ends after a Top half
# (home wins without batting: NOT a walk-off); game 3 ends after a Bot half (home loses: NOT a walk-off).
RE_TABLE = {0: 0.5, 1000: 0.6, 1100: 0.55, 1: 0.9, 10: 0.3, 20: 0.1}


def _away(**kw):  # Top half: the batting team is the away team, so post_away_score == post_bat_score
    kw.setdefault("post_home_score", 0)
    kw["post_away_score"] = kw["post_bat_score"]
    return make_row(inning="1", inning_topbot="Top", **kw)


def run_rows() -> list[tuple]:
    rows = [
        _away(at_bat_number=1, pitch_number=1, description="ball", balls=0, strikes=0, outs_when_up=0, bat_score=0, post_bat_score=0),
        _away(at_bat_number=1, pitch_number=2, description="called_strike", balls=1, strikes=0, outs_when_up=0, bat_score=0, post_bat_score=0),
        _away(at_bat_number=1, pitch_number=3, description="hit_into_play", events="single", balls=1, strikes=1, outs_when_up=0,
              bat_score=0, post_bat_score=0),
        _away(at_bat_number=2, pitch_number=1, description="hit_into_play", events="home_run", balls=0, strikes=0, outs_when_up=0,
              on_1b=101, bat_score=0, post_bat_score=2, des="Doe homers (1) on a fly ball"),
        _away(at_bat_number=3, pitch_number=1, description="hit_into_play", events="field_out", balls=0, strikes=0, outs_when_up=0,
              bat_score=2, post_bat_score=2),
        _away(at_bat_number=4, pitch_number=1, description="hit_into_play", events="field_out", balls=0, strikes=0, outs_when_up=1,
              bat_score=2, post_bat_score=2),
        _away(at_bat_number=5, pitch_number=1, description="hit_into_play", events="field_out", balls=0, strikes=0, outs_when_up=2,
              bat_score=2, post_bat_score=2),
        # walk-off half-inning (Bot 9, last plate appearance of the game, home 3 - away 2)
        make_row(at_bat_number=60, pitch_number=1, inning="9", inning_topbot="Bot", description="ball", balls=0, strikes=0,
                 outs_when_up=0, on_1b=150, bat_score=1, post_bat_score=1, post_home_score=1, post_away_score=2),
        make_row(at_bat_number=60, pitch_number=2, inning="9", inning_topbot="Bot", description="hit_into_play", events="home_run",
                 balls=1, strikes=0, outs_when_up=0, on_1b=150, bat_score=1, post_bat_score=3, post_home_score=3, post_away_score=2),
        # game 2: last half-inning is a Top half (home team leads) -> not a walk-off
        make_row(game_pk=2, at_bat_number=1, pitch_number=1, inning="9", inning_topbot="Top", description="hit_into_play",
                 events="field_out", balls=0, strikes=0, outs_when_up=2, bat_score=0, post_bat_score=0, post_home_score=3, post_away_score=0),
        # game 3: last half-inning is a Bot half but the home team loses -> not a walk-off
        make_row(game_pk=3, at_bat_number=1, pitch_number=1, inning="9", inning_topbot="Bot", description="hit_into_play",
                 events="field_out", balls=0, strikes=0, outs_when_up=2, bat_score=0, post_bat_score=0, post_home_score=0, post_away_score=2),
    ]
    return rows


# pitch_uid -> (state_code, next_state_code, runs_to_end, runs_on_pitch, is_walkoff_half, pitch_value with RE_TABLE)
EXPECTED = {
    "1:1:1": (0, 1000, 2, 0, 0, 0.6 - 0.5),
    "1:1:2": (1000, 1100, 2, 0, 0, 0.55 - 0.6),
    "1:1:3": (1100, 1, 2, 0, 0, 0.9 - 0.55),
    "1:2:1": (1, 0, 2, 2, 0, 0.5 + 2 - 0.9),
    "1:3:1": (0, 10, 0, 0, 0, 0.3 - 0.5),
    "1:4:1": (10, 20, 0, 0, 0, 0.1 - 0.3),
    "1:5:1": (20, None, 0, 0, 0, 0.0 - 0.1),
    "1:60:1": (1, 1001, 2, 0, 1, None),
    "1:60:2": (1001, None, 2, 2, 1, None),
    "2:1:1": (20, None, 0, 0, 0, -0.1),
    "3:1:1": (20, None, 0, 0, 0, -0.1),
}
# state_code -> (n, re) from the non-walk-off rows
EXPECTED_RE = {0: (2, 1.0), 1000: (1, 2.0), 1100: (1, 2.0), 1: (1, 2.0), 10: (1, 0.0), 20: (3, 0.0)}


def make_run_db(path: Path) -> Path:
    return make_pitches_db(path, run_rows())
