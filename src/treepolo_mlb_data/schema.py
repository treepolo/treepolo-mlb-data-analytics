from __future__ import annotations

import re

SAFE_COLUMN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

INTEGER_COLUMNS = {
    "batter", "pitcher", "zone", "hit_location", "balls", "strikes",
    "game_year", "on_3b", "on_2b", "on_1b", "outs_when_up", "fielder_2",
    "fielder_3", "fielder_4", "fielder_5", "fielder_6", "fielder_7",
    "fielder_8", "fielder_9", "game_pk", "at_bat_number", "pitch_number",
    "home_score", "away_score", "bat_score", "fld_score", "post_away_score",
    "post_home_score", "post_bat_score", "post_fld_score", "age_pit_legacy",
    "age_bat_legacy", "n_thruorder_pitcher", "n_priorpa_thisgame_player_at_bat",
    "pitcher_days_since_prev_game", "batter_days_since_prev_game",
    "pitcher_days_until_next_game", "batter_days_until_next_game",
}

# These columns are identifiers even when SQLite stores them as INTEGER. Treating
# their numeric codes as continuous model features creates meaningless distances
# and coefficients (for example pitcher 800260 is not "larger" than pitcher
# 670912 in a statistical sense).
IDENTIFIER_COLUMNS = {
    "pitch_uid", "game_pk", "pitcher", "batter",
    "on_1b", "on_2b", "on_3b",
    "fielder_2", "fielder_3", "fielder_4", "fielder_5", "fielder_6",
    "fielder_7", "fielder_8", "fielder_9",
}

REAL_COLUMNS = {
    "release_speed", "release_pos_x", "release_pos_z", "pfx_x", "pfx_z",
    "plate_x", "plate_z", "sz_top", "sz_bot", "hc_x", "hc_y",
    "hit_distance_sc", "launch_speed", "launch_angle", "effective_speed",
    "release_spin_rate", "release_extension", "vx0", "vy0", "vz0", "ax",
    "ay", "az", "estimated_ba_using_speedangle", "estimated_woba_using_speedangle",
    "woba_value", "woba_denom", "babip_value", "iso_value", "launch_speed_angle",
    "estimated_slg_using_speedangle", "spin_axis", "delta_home_win_exp",
    "delta_run_exp", "delta_pitcher_run_exp", "api_break_z_with_gravity",
    "api_break_x_arm", "api_break_x_batter_in", "arm_angle", "release_pos_y",
    "hyper_speed", "home_score_diff", "bat_score_diff", "home_win_exp",
    "bat_win_exp", "age_pit", "age_bat", "bat_speed", "swing_length",
    "miss_distance", "attack_angle", "attack_direction", "swing_path_tilt",
    "intercept_ball_minus_batter_pos_x_inches", "intercept_ball_minus_batter_pos_y_inches",
    "spin_dir", "spin_rate_deprecated", "break_angle_deprecated", "break_length_deprecated",
}

CURRENT_DOCUMENTED_COLUMNS = {
    "pitch_type", "game_date", "release_speed", "release_pos_x", "release_pos_z",
    "player_name", "batter", "pitcher", "events", "description", "spin_dir",
    "spin_rate_deprecated", "break_angle_deprecated", "break_length_deprecated",
    "zone", "des", "game_type", "stand", "p_throws", "home_team", "away_team",
    "type", "hit_location", "bb_type", "balls", "strikes", "game_year", "pfx_x",
    "pfx_z", "plate_x", "plate_z", "on_3b", "on_2b", "on_1b", "outs_when_up",
    "inning", "inning_topbot", "hc_x", "hc_y", "tfs_deprecated", "tfs_zulu_deprecated",
    "fielder_2", "umpire", "sv_id", "vx0", "vy0", "vz0", "ax", "ay", "az",
    "sz_top", "sz_bot", "hit_distance_sc", "launch_speed", "launch_angle",
    "effective_speed", "release_spin_rate", "release_extension", "game_pk", "fielder_3",
    "fielder_4", "fielder_5", "fielder_6", "fielder_7", "fielder_8", "fielder_9",
    "release_pos_y", "estimated_ba_using_speedangle", "estimated_woba_using_speedangle",
    "woba_value", "woba_denom", "babip_value", "iso_value", "launch_speed_angle",
    "at_bat_number", "pitch_number", "pitch_name", "home_score", "away_score",
    "bat_score", "fld_score", "post_away_score", "post_home_score", "post_bat_score",
    "post_fld_score", "if_fielding_alignment", "of_fielding_alignment", "spin_axis",
    "delta_home_win_exp", "delta_run_exp", "delta_pitcher_run_exp",
    "estimated_slg_using_speedangle", "api_break_z_with_gravity", "api_break_x_arm",
    "api_break_x_batter_in", "arm_angle", "hyper_speed", "home_score_diff",
    "bat_score_diff", "home_win_exp", "bat_win_exp", "age_pit_legacy", "age_bat_legacy",
    "age_pit", "age_bat", "n_thruorder_pitcher", "n_priorpa_thisgame_player_at_bat",
    "pitcher_days_since_prev_game", "batter_days_since_prev_game",
    "pitcher_days_until_next_game", "batter_days_until_next_game", "bat_speed",
    "swing_length", "miss_distance", "attack_angle", "attack_direction", "swing_path_tilt",
    "intercept_ball_minus_batter_pos_x_inches", "intercept_ball_minus_batter_pos_y_inches",
}


def sqlite_type(column: str) -> str:
    if column in INTEGER_COLUMNS:
        return "INTEGER"
    if column in REAL_COLUMNS:
        return "REAL"
    return "TEXT"


def field_capabilities(column: str, sql_type: str | None = None) -> tuple[str, ...]:
    """Return semantic/type capabilities for a schema field.

    UI controls ask for capabilities instead of owning lists of allowed column
    names. New numeric/date-like fields therefore become eligible automatically.
    Baseball semantic roles are declared once at the schema boundary, which is
    also where the backend's canonical field contract lives.
    """
    resolved = (sql_type or sqlite_type(column) or "TEXT").upper()
    capabilities = {"reference", "filter", "group", "order", "id"}
    is_numeric = any(token in resolved for token in ("INT", "REAL", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC"))
    if column in IDENTIFIER_COLUMNS:
        capabilities.add("identifier")
    elif is_numeric:
        capabilities.update({"numeric", "trend_orderable"})
    if "DATE" in resolved or column.endswith("_date"):
        capabilities.update({"temporal", "trend_orderable"})

    # Pitch classifications are semantic schema roles, not UI-owned allowlists.
    # The pattern naturally covers the public Statcast pitch_type/pitch_name
    # classification columns while excluding identifiers such as pitch_uid.
    if resolved == "TEXT" and column.startswith("pitch_") and column.endswith(("_type", "_name")):
        capabilities.add("pitch_classification")
    if column == "pitch_type":
        capabilities.add("canonical_pitch_type")
    return tuple(sorted(capabilities))


def quote_ident(name: str) -> str:
    if not SAFE_COLUMN.fullmatch(name):
        raise ValueError(f"Unsafe column name from upstream CSV: {name!r}")
    return f'"{name}"'
