"""Derive the frozen final-test configs (F1 = 2025, F2 = 2026) from the modeling-period configs. Run from the repo root."""
import hashlib
import json
from pathlib import Path

C = Path("docs/research/configs")
OUT = Path("docs/research/configs/frozen")
RATES = ["whiff_per_swing", "swing_rate", "called_strike_per_take", "foul_per_swing", "in_play_per_swing", "hr_per_pitch", "mean_pitch_value"]
SOURCES = (["A1_run_expectancy", "A2_outcome_count_type"] + [f"A5_streak_curve_{r}_S1" for r in RATES]
           + [f"A6_streak_regression_{r}_{s}_2023-24" for r in RATES for s in ("S3", "S4")]
           + [f"A7_prev1_regression_{r}_S3" for r in ("whiff_per_swing", "swing_rate", "mean_pitch_value")] + ["A8_memory_ladder_logistic_entity"])
hashes = {}
for tag, year, study in (("F1_2025", 2025, "P5-6 test 2025"), ("F2_2026", 2026, "P5-6 test 2026")):
    folder = OUT / tag
    folder.mkdir(parents=True, exist_ok=True)
    for name in SOURCES:
        cfg = json.loads((C / f"{name}.json").read_text(encoding="utf-8"))
        cfg.setdefault("kind", {"A1_run_expectancy": "run_expectancy", "A2_outcome_count_type": "outcome_table"}.get(name))
        cfg["study"] = study
        cfg["purpose"] = "final_test"
        if cfg["kind"] == "outcome_model":
            cfg["scope"] = {"game_years": [2023, 2024, year], "game_types": ["R"]}
            cfg["train_years"] = [2023, 2024]
            cfg["test_years"] = [year]
        else:
            cfg["scope"] = {"game_years": [year], "game_types": ["R"]}
        text = json.dumps(cfg, ensure_ascii=False, sort_keys=True, indent=1) + "\n"
        out_name = name.replace("_2023-24", "") + f"_{tag[:2]}.json"
        (folder / out_name).write_text(text, encoding="utf-8")
        hashes[f"{tag}/{out_name}"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
(OUT / "SHA256SUMS.json").write_text(json.dumps(hashes, indent=1, sort_keys=True) + "\n", encoding="utf-8")
print(len(hashes), "frozen configs")
