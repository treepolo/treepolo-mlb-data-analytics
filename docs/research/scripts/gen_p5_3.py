"""Generate the P5-3 (streak) config files into docs/research/configs/ (run from the repo root)."""
import json
from pathlib import Path

OUT = Path("docs/research/configs")
TYPES = ["FF", "SI", "FC", "SL", "ST", "CH", "CU", "FS", "KC"]
RATES = ["whiff_per_swing", "swing_rate", "called_strike_per_take", "foul_per_swing", "in_play_per_swing", "hr_per_pitch", "mean_pitch_value"]
SCOPES = {"2023-24": [2023, 2024], "2023": [2023], "2024": [2024]}
BASE_STRATA = ["pitch_index_in_pa", "balls", "strikes"]


def scope(years):
    return {"game_years": years, "game_types": ["R"]}


def write(name, cfg):
    (OUT / f"{name}.json").write_text(json.dumps(cfg, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")


for rate in RATES:
    for tag, strata in (("S1", BASE_STRATA), ("S1x", BASE_STRATA + ["prior_same_type_count"])):
        write(f"A5_streak_curve_{rate}_{tag}", {"kind": "streak_curve", "study": "P5-3 streak", "scope": scope([2023, 2024]), "purpose": "tuning", "rate": rate,
              "pitch_types": TYPES, "hand_view": "four_groups", "kmax": 4, "strata_fields": strata, "se_method": "cluster_bootstrap", "cluster_by": "pitcher",
              "bootstrap_reps": 200, "confidence": 0.99})
SPECS = {
    "S2": {"fixed_effects": [], "controls": ["count", "exposure"]},
    "S3": {"fixed_effects": ["pitcher", "batter"], "controls": ["count", "exposure"]},
    "S4": {"fixed_effects": ["pitcher_game", "batter"], "controls": ["count", "exposure"]},
    "S5": {"fixed_effects": ["pitcher", "batter"], "controls": ["count", "exposure", "prev1_description", "zone", "release_speed"]},
}
for rate in RATES:
    for spec, extra in SPECS.items():
        for tag, years in SCOPES.items():
            write(f"A6_streak_regression_{rate}_{spec}_{tag}", {"kind": "streak_regression", "study": "P5-3 streak", "scope": scope(years), "purpose": "tuning", "rate": rate,
                  "pitch_types": TYPES, "treatment": "streak_position", "kmax": 4, "hand_view": "four_groups", "cluster_by": "pitcher", "confidence": 0.99, **extra})
