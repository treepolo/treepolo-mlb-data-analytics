from __future__ import annotations

from typing import Any

# Trackman spellings vary slightly between feeds/versions. Normalize only the
# values we understand; unknown values are preserved verbatim so ingestion is
# lossless and future schema work can classify them later.
_PITCH_TYPES = {
    "fourseamfastball": "FF",
    "four-seamfastball": "FF",
    "four-seamfastball": "FF",
    "4-seamfastball": "FF",
    "fastball": "FF",
    "twoseamfastball": "SI",
    "two-seamfastball": "SI",
    "2-seamfastball": "SI",
    "sinker": "SI",
    "cutter": "FC",
    "cutfastball": "FC",
    "slider": "SL",
    "sweeper": "ST",
    "curveball": "CU",
    "curve": "CU",
    "knucklecurve": "KC",
    "changeup": "CH",
    "change-up": "CH",
    "splitter": "FS",
    "splitfinger": "FS",
    "split-finger": "FS",
    "forkball": "FO",
    "knuckleball": "KN",
    "eephus": "EP",
    "screwball": "SC",
}

_PITCH_CALLS = {
    "strikecalled": "called_strike",
    "calledstrike": "called_strike",
    "strikeswinging": "swinging_strike",
    "swingingstrike": "swinging_strike",
    "strikeswingingblocked": "swinging_strike_blocked",
    "foulball": "foul",
    "foul": "foul",
    "foultip": "foul_tip",
    "ballcalled": "ball",
    "calledball": "ball",
    "ballin dirt": "blocked_ball",
    "ballindirt": "blocked_ball",
    "hitbypitch": "hit_by_pitch",
    "inplay": "hit_into_play",
    "inplayout": "hit_into_play",
    "inplayhit": "hit_into_play",
}


def _key(value: Any) -> str:
    return "".join(str(value).strip().lower().split())


def canonical_pitch_type(auto_pitch_type: Any, tagged_pitch_type: Any = None) -> str | None:
    raw = auto_pitch_type if auto_pitch_type not in (None, "") else tagged_pitch_type
    if raw in (None, ""):
        return None
    text = str(raw).strip()
    return _PITCH_TYPES.get(_key(text), text)


def canonical_pitch_call(pitch_call: Any) -> str | None:
    if pitch_call in (None, ""):
        return None
    text = str(pitch_call).strip()
    return _PITCH_CALLS.get(_key(text), text)


def semantic_value_sets() -> dict[str, tuple[str, ...]]:
    """Finite CPBL semantic values useful to UI controls and tests."""
    return {
        "pitch_type": tuple(sorted(set(_PITCH_TYPES.values()))),
        "description": tuple(sorted(set(_PITCH_CALLS.values()))),
    }
