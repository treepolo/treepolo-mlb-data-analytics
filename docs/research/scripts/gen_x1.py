"""Derive the extra-study X1 configs (2023-2026, purpose extra_study) from the modeling-period configs. Run from the repo root."""
import json
from pathlib import Path

C = Path("docs/research/configs"); OUT = C / "x1"; OUT.mkdir(exist_ok=True)
RATES = ["whiff_per_swing", "swing_rate", "called_strike_per_take", "foul_per_swing", "in_play_per_swing", "hr_per_pitch", "mean_pitch_value"]
sources = ["A1_run_expectancy", "A2_outcome_count_type"] + [f"A6_streak_regression_{r}_S3_2023-24" for r in RATES] + ["A8_memory_ladder_logistic_entity"]
for name in sources:
    cfg = json.loads((C / f"{name}.json").read_text(encoding="utf-8"))
    cfg.setdefault("kind", {"A1_run_expectancy": "run_expectancy", "A2_outcome_count_type": "outcome_table"}.get(name))
    cfg["study"] = "P5-7 extra study"; cfg["purpose"] = "extra_study"
    cfg["scope"] = {"game_years": [2023, 2024, 2025, 2026], "game_types": ["R"]}
    if cfg["kind"] == "outcome_model":
        cfg["split"] = "grouped_kfold"; cfg.pop("train_years", None); cfg.pop("test_years", None)
    (OUT / (name.replace("_2023-24", "") + "_X1.json")).write_text(json.dumps(cfg, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")
