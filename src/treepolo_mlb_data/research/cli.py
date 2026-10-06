from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="research_command", required=True)
    sub.add_parser("methods", help="List research methods and their settings")

    run = sub.add_parser("run", help="Run (or reuse) a research method")
    run.add_argument("--kind", required=True)
    run.add_argument("--config-file", type=Path, required=True, help="JSON file with the method settings")
    run.add_argument("--study", default=None, help="Study name (created if missing)")
    run.add_argument("--force", action="store_true", help="Run again even if an identical run exists")

    lst = sub.add_parser("list", help="List research runs")
    lst.add_argument("--study", default=None)
    lst.add_argument("--kind", default=None)
    lst.add_argument("--limit", type=int, default=50)

    show = sub.add_parser("show", help="Show a run and a page of its result")
    show.add_argument("run_id", type=int)
    show.add_argument("--section", type=int, default=0)
    show.add_argument("--offset", type=int, default=0)
    show.add_argument("--limit", type=int, default=20)

    rerun = sub.add_parser("rerun", help="Run the same settings again on local data")
    rerun.add_argument("run_id", type=int)

    study = sub.add_parser("study", help="Create or update a study's purpose and insights")
    study.add_argument("name")
    study.add_argument("--purpose-file", type=Path, default=None)
    study.add_argument("--insight-file", type=Path, default=None)

    export = sub.add_parser("export", help="Write a research bundle file")
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--study", action="append", default=[])
    export.add_argument("--run-id", action="append", type=int, default=[])
    export.add_argument("--no-artifacts", action="store_true")
    export.add_argument("--notes", default="")

    imp = sub.add_parser("import", help="Import a research bundle file")
    imp.add_argument("path", type=Path)


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _compact(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": run["id"], "kind": run["kind"], "status": run["status"], "origin": run["origin"],
        "created_at": run["created_at"], "duration_seconds": run["duration_seconds"],
        "total_rows": run["summary"].get("total_rows"), "studies": [s["name"] for s in run.get("studies", [])],
        "error": run["error"],
    }


def _study_by_name(service, name: str) -> dict[str, Any]:
    from .config_schema import ConfigError

    for study in service.list_studies():
        if study["name"] == name:
            return study
    raise ConfigError(f"Study not found: {name} / 找不到研究專案: {name}")


def run_command(args: argparse.Namespace, config) -> int:
    from ..web_analysis import AnalysisFacade
    from .bundle import export_bundle, import_bundle
    from .config_schema import ConfigError
    from .service import ResearchService

    facade = AnalysisFacade(config.database_path, config.analytics_database_path, backend=config.analysis_backend)
    service = ResearchService(config, facade)
    try:
        command = args.research_command
        if command == "methods":
            _print({"methods": service.methods()})
        elif command == "run":
            settings = json.loads(args.config_file.read_text(encoding="utf-8"))
            outcome = service.run(args.kind, settings, study_name=args.study, force=args.force)
            _print({"run": outcome["run"], "reused": outcome["reused"], "reproduced": outcome["reproduced"]})
        elif command == "list":
            study_id = _study_by_name(service, args.study)["id"] if args.study else None
            _print({"runs": [_compact(r) for r in service.list_runs(study_id=study_id, kind=args.kind, limit=args.limit)]})
        elif command == "show":
            detail = service.run_detail(args.run_id)
            if detail is None:
                raise ConfigError(f"Run not found: {args.run_id} / 找不到研究紀錄: {args.run_id}")
            page = service.result_page(args.run_id, args.section, args.offset, args.limit) if detail["status"] == "success" else None
            _print({"run": detail, "page": page})
        elif command == "rerun":
            outcome = service.rerun(args.run_id)
            _print({"run": outcome["run"], "reused": outcome["reused"], "reproduced": outcome["reproduced"]})
        elif command == "study":
            existing = next((s for s in service.list_studies() if s["name"] == args.name and s["origin"] == "local"), None)
            fields: dict[str, str] = {}
            if args.purpose_file:
                fields["purpose"] = args.purpose_file.read_text(encoding="utf-8")
            if args.insight_file:
                fields["insight_markdown"] = args.insight_file.read_text(encoding="utf-8")
            if existing is None:
                item = service.create_study(args.name, fields.get("purpose", ""), fields.get("insight_markdown", ""))
            else:
                item = service.update_study(existing["id"], **fields) if fields else existing
            _print({"study": item})
        elif command == "export":
            study_ids = [_study_by_name(service, name)["id"] for name in args.study]
            data = export_bundle(service.store, study_ids=study_ids, run_ids=args.run_id,
                                 include_artifacts=not args.no_artifacts, notes=args.notes)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_bytes(data)
            _print({"written": str(args.out), "bytes": len(data)})
        elif command == "import":
            _print(import_bundle(service.store, args.path.read_bytes()))
        else:  # pragma: no cover - argparse enforces the choices
            raise ConfigError(f"Unknown research command: {command}")
        return 0
    except (ConfigError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        service.close()
