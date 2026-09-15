from __future__ import annotations

from typing import Any

# Trackman spellings vary slightly between feeds/versions. Normalize only the
# values we understand; unknown values are preserved verbatim so ingestion is
# lossless and future schema work can classify them later.
#
# CPBL 2026 public Trackman has an important source quirk: AutoPitchType is
# almost always the low-information value ``breakingball`` while
# TaggedPitchType carries the actually useful public coarse class
# (``fastball`` / ``breakingball``). A detailed AutoPitchType still wins when
# one is present, but a generic/undefined AutoPitchType must not overwrite the
# more informative tagged class.
_DETAILED_PITCH_TYPES = {
    "fourseamfastball": "FF",
    "four-seamfastball": "FF",
    "4-seamfastball": "FF",
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

_COARSE_PITCH_TYPES = {
    "fastball": "fastball",
    "breakingball": "breakingball",
}

_LOW_INFORMATION_AUTO_TYPES = {
    "fastball",
    "breakingball",
    "undefined",
    "unknown",
    "other",
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


def _canonical_pitch_type_value(raw: Any) -> str | None:
    if raw in (None, ""):
        return None
    text = str(raw).strip()
    key = _key(text)
    return _DETAILED_PITCH_TYPES.get(key, _COARSE_PITCH_TYPES.get(key, text))


def canonical_pitch_type(auto_pitch_type: Any, tagged_pitch_type: Any = None) -> str | None:
    """Return the most informative source-supported pitch classification.

    Detailed automatic classifications (Slider, FourSeamFastBall, etc.) take
    precedence. CPBL's public 2026 feed, however, commonly emits a generic
    AutoPitchType=breakingball for both public TaggedPitchType classes; in that
    case TaggedPitchType is the meaningful source classification and is used.
    Generic ``fastball`` is intentionally *not* rewritten to ``FF`` because the
    public feed does not establish that it is specifically a four-seam fastball.
    """
    if auto_pitch_type not in (None, ""):
        auto_text = str(auto_pitch_type).strip()
        auto_key = _key(auto_text)
        if auto_key not in _LOW_INFORMATION_AUTO_TYPES:
            return _DETAILED_PITCH_TYPES.get(auto_key, auto_text)

    tagged = _canonical_pitch_type_value(tagged_pitch_type)
    if tagged is not None:
        return tagged

    return _canonical_pitch_type_value(auto_pitch_type)


def canonical_pitch_call(pitch_call: Any) -> str | None:
    if pitch_call in (None, ""):
        return None
    text = str(pitch_call).strip()
    return _PITCH_CALLS.get(_key(text), text)


def semantic_value_sets() -> dict[str, tuple[str, ...]]:
    """Finite CPBL semantic values useful to UI controls and tests."""
    return {
        "pitch_type": tuple(sorted(set(_DETAILED_PITCH_TYPES.values()) | set(_COARSE_PITCH_TYPES.values()))),
        "description": tuple(sorted(set(_PITCH_CALLS.values()))),
    }
