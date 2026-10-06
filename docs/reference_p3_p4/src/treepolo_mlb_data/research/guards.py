from __future__ import annotations

from typing import Any, Mapping

from .config_schema import ConfigError

RESERVED_YEARS = (2025, 2026)  # test seasons (plan section 7): never used for tuning
PURPOSES = ("tuning", "final_test", "extra_study")


def scope_years(scope: Mapping[str, Any]) -> list[int]:
    """Seasons a normalized scope touches (for a date range: every year from date_from to date_to)."""

    if not scope:
        return []
    if "game_years" in scope:
        return sorted({int(y) for y in scope["game_years"]})
    return list(range(int(str(scope["date_from"])[:4]), int(str(scope["date_to"])[:4]) + 1))


def check_purpose(scope: Mapping[str, Any], purpose: str) -> None:
    """tuning: no reserved season in scope. final_test: ONLY reserved seasons. extra_study: anything (no clean test set)."""

    years = set(scope_years(scope))
    reserved = set(RESERVED_YEARS)
    if purpose == "tuning" and years & reserved:
        raise ConfigError(
            f"scope contains reserved test seasons {sorted(years & reserved)}; use purpose=final_test (only test seasons) "
            f"or extra_study / scope 含保留的檢驗球季 {sorted(years & reserved)}；請改用 purpose=final_test（只含檢驗球季）或 extra_study"
        )
    if purpose == "final_test" and (not years or not years <= reserved):
        raise ConfigError(
            f"final_test needs a scope made only of the reserved seasons {list(RESERVED_YEARS)} / "
            f"final_test 的範圍只能是保留的檢驗球季 {list(RESERVED_YEARS)}"
        )
    if purpose not in PURPOSES:
        raise ConfigError(f"purpose must be one of {list(PURPOSES)} / purpose 必須是 {list(PURPOSES)} 其中之一")


def check_modeling_scope(scope: Mapping[str, Any], purpose: str, label: str) -> None:
    """The scope that fixes a table learned from the modeling period (e.g. the run-expectancy table) must not contain test seasons
    unless the run is an extra study."""

    years = set(scope_years(scope))
    if purpose != "extra_study" and years & set(RESERVED_YEARS):
        raise ConfigError(
            f"{label} contains reserved test seasons {sorted(years & set(RESERVED_YEARS))}; only extra_study may use them / "
            f"{label} 含保留的檢驗球季 {sorted(years & set(RESERVED_YEARS))}，只有 extra_study 可以使用"
        )
