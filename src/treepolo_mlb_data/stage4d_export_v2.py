from __future__ import annotations

import csv
import tempfile
import uuid
from pathlib import Path
from typing import Any

from . import stage4d
from ._lazy_duckdb import duckdb
from .web_analysis import RequestError


def _sql_path(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def parquet_bytes_bulk(section: dict[str, Any]) -> bytes:
    """Serialize a formal result section to Parquet using DuckDB bulk CSV ingest.

    The original Stage 4D exporter inserted every row through DB-API executemany().
    That is functionally correct for tiny tests but becomes unnecessarily slow for
    real analysis results.  This path lets DuckDB parse a temporary CSV natively,
    preserving the explicit output schema before COPY ... FORMAT PARQUET.
    """

    columns = [str(value) for value in section.get("columns") or []]
    rows = [row for row in section.get("rows") or [] if isinstance(row, dict)]
    if not columns:
        raise RequestError("Cannot export a result with no columns to Parquet")

    types = {
        column: stage4d._parquet_type([row.get(column) for row in rows[:1000]])
        for column in columns
    }

    with tempfile.TemporaryDirectory(prefix="treepolo-stage4d-parquet-") as temp:
        temp_dir = Path(temp)
        input_csv = temp_dir / "input.csv"
        output = temp_dir / "result.parquet"
        null_token = f"__TREEPOLO_NULL_{uuid.uuid4().hex}__"

        if rows:
            with input_csv.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(columns)
                for row in rows:
                    values: list[Any] = []
                    for column in columns:
                        value = stage4d._scalar(row.get(column))
                        if value is None:
                            value = null_token
                        elif isinstance(value, bool):
                            value = "true" if value else "false"
                        values.append(value)
                    writer.writerow(values)

        conn = duckdb.connect()
        try:
            conn.execute(
                "CREATE TABLE export_data ("
                + ",".join(
                    f"{stage4d._quote_ident(column)} {types[column]}" for column in columns
                )
                + ")"
            )
            if rows:
                escaped_input = _sql_path(input_csv)
                escaped_null = null_token.replace("'", "''")
                conn.execute(
                    f"COPY export_data FROM '{escaped_input}' "
                    f"(FORMAT CSV, HEADER TRUE, NULL '{escaped_null}')"
                )
            escaped_output = _sql_path(output)
            conn.execute(f"COPY export_data TO '{escaped_output}' (FORMAT PARQUET)")
        finally:
            conn.close()
        return output.read_bytes()


def install() -> None:
    """Replace only the Parquet serializer while keeping Stage4DService/export API stable."""

    if getattr(stage4d, "_stage4d_export_v2_installed", False):
        return
    stage4d._stage4d_export_v2_installed = True
    stage4d._parquet_bytes = parquet_bytes_bulk
