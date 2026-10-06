from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from ..analysis.model import Binary, Boolean, Column, Expr, InList, Literal
from ..analysis_state import canonical_json, read_data_revision
from .config_schema import ConfigError

_GAME_TYPE = re.compile(r"^[A-Z]{1,3}$")
_ALLOWED_KEYS = {"game_years", "date_from", "date_to", "game_types"}
_REQUIRED_COLUMNS = ("game_year", "game_type", "game_date", "_ingested_at")
_FINGERPRINT_CACHE: dict[tuple[str, str, str], dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()


def _iso_date(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"scope.{name} must be a YYYY-MM-DD string / scope.{name} 必須是 YYYY-MM-DD 文字")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ConfigError(f"scope.{name} is not a valid date / scope.{name} 不是有效日期: {value}") from exc


def normalize_scope(raw: Mapping[str, Any] | None, *, required: bool) -> dict[str, Any]:
    """Normalize a research data scope.

    Exactly one of ``game_years`` or (``date_from`` and ``date_to``) selects the
    seasons; ``game_types`` defaults to regular season only.
    """

    if raw is None or (not required and isinstance(raw, Mapping) and not raw):
        if required:
            raise ConfigError("scope is required / 必須指定研究範圍 scope")
        return {}
    if not isinstance(raw, Mapping):
        raise ConfigError("scope must be an object / scope 必須是物件")
    unknown = sorted(set(raw) - _ALLOWED_KEYS)
    if unknown:
        raise ConfigError(f"Unknown scope keys: {unknown} / scope 含未知的鍵: {unknown}")
    has_years = "game_years" in raw
    has_dates = "date_from" in raw or "date_to" in raw
    if has_years == has_dates:
        raise ConfigError(
            "scope needs either game_years or both date_from and date_to / scope 必須擇一指定 game_years，或同時指定 date_from 與 date_to"
        )
    scope: dict[str, Any] = {}
    if has_years:
        years = raw["game_years"]
        if not isinstance(years, (list, tuple)) or not years:
            raise ConfigError("scope.game_years must be a non-empty list / scope.game_years 必須是非空列表")
        cleaned: list[int] = []
        for year in years:
            if isinstance(year, bool) or not isinstance(year, int) or not 2015 <= year <= 2100:
                raise ConfigError(f"scope.game_years has an invalid year / scope.game_years 含無效年份: {year!r}")
            cleaned.append(year)
        scope["game_years"] = sorted(set(cleaned))
    else:
        if "date_from" not in raw or "date_to" not in raw:
            raise ConfigError("scope needs both date_from and date_to / scope 必須同時指定 date_from 與 date_to")
        date_from = _iso_date(raw["date_from"], "date_from")
        date_to = _iso_date(raw["date_to"], "date_to")
        if date_from > date_to:
            raise ConfigError("scope.date_from must not be after date_to / scope.date_from 不得晚於 date_to")
        scope["date_from"] = date_from
        scope["date_to"] = date_to
    types = raw.get("game_types", ["R"])
    if not isinstance(types, (list, tuple)) or not types:
        raise ConfigError("scope.game_types must be a non-empty list / scope.game_types 必須是非空列表")
    for game_type in types:
        if not isinstance(game_type, str) or not _GAME_TYPE.match(game_type):
            raise ConfigError(f"scope.game_types has an invalid code / scope.game_types 含無效代碼: {game_type!r}")
    scope["game_types"] = sorted(set(types))
    return scope


def scope_filter_expr(scope: Mapping[str, Any]) -> Expr | None:
    if not scope:
        return None
    terms: list[Expr] = []
    if "game_years" in scope:
        terms.append(InList(Column("game_year"), tuple(Literal(int(y)) for y in scope["game_years"])))
    else:
        terms.append(Binary(Column("game_date"), ">=", Literal(scope["date_from"])))
        terms.append(Binary(Column("game_date"), "<=", Literal(scope["date_to"])))
    terms.append(InList(Column("game_type"), tuple(Literal(str(t)) for t in scope["game_types"])))
    return Boolean("and", tuple(terms))


def _scope_where(scope: Mapping[str, Any]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if "game_years" in scope:
        marks = ",".join("?" for _ in scope["game_years"])
        clauses.append(f"game_year IN ({marks})")
        params.extend(int(y) for y in scope["game_years"])
    else:
        clauses.append("game_date >= ? AND game_date <= ?")
        params.extend([scope["date_from"], scope["date_to"]])
    marks = ",".join("?" for _ in scope["game_types"])
    clauses.append(f"game_type IN ({marks})")
    params.extend(str(t) for t in scope["game_types"])
    return " AND ".join(clauses), params


def compute_scope_fingerprint(database_path: Path, scope: Mapping[str, Any]) -> dict[str, Any]:
    """Describe the data inside a scope so a research run can tell when it changed.

    Uses row counts, date range and the newest ``_ingested_at`` per season/game
    type; ``_ingested_at`` moves whenever a row is inserted or corrected.
    """

    path = Path(database_path)
    revision = read_data_revision(path)
    if not scope:
        return {
            "data_revision": revision,
            "seasons": [],
            "total_rows": None,
            "scope_fingerprint": "revision:" + revision,
        }
    cache_key = (str(path.resolve()), revision, canonical_json(dict(scope)))
    with _CACHE_LOCK:
        cached = _FINGERPRINT_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)
    if not path.exists():
        raise ConfigError(f"Pitch database does not exist: {path} / 找不到逐球資料庫")
    conn = sqlite3.connect(path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(pitches)")}
        missing = [name for name in _REQUIRED_COLUMNS if name not in columns]
        if missing:
            raise ConfigError(f"pitches is missing columns {missing} / 資料表缺少欄位 {missing}")
        where, params = _scope_where(scope)
        rows = conn.execute(
            "SELECT game_year, game_type, COUNT(*), MIN(game_date), MAX(game_date), MAX(_ingested_at) "
            f"FROM pitches WHERE {where} GROUP BY game_year, game_type ORDER BY game_year, game_type",
            params,
        ).fetchall()
    finally:
        conn.close()
    seasons = [
        {
            "game_year": row[0], "game_type": row[1], "rows": int(row[2]),
            "min_date": row[3], "max_date": row[4], "max_ingested_at": row[5],
        }
        for row in rows
    ]
    result = {
        "data_revision": revision,
        "seasons": seasons,
        "total_rows": sum(item["rows"] for item in seasons),
        "scope_fingerprint": hashlib.sha256(canonical_json(seasons).encode("utf-8")).hexdigest(),
    }
    with _CACHE_LOCK:
        _FINGERPRINT_CACHE[cache_key] = result
    return dict(result)
