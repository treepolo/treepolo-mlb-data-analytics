"""Run research config files (each has "kind" and "study" keys besides the method settings) and append timings to a log.

usage: PYTHONPATH=src python docs/research/scripts/run_configs.py LOGFILE CONFIG.json [CONFIG.json ...]
"""
import json
import sys
import time
from pathlib import Path

from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research.service import ResearchService

log, files = Path(sys.argv[1]), sys.argv[2:]
service = ResearchService(AppConfig(), None)
for name in files:
    cfg = json.loads(Path(name).read_text(encoding="utf-8"))
    kind, study = cfg.pop("kind"), cfg.pop("study")
    started = time.time()
    try:
        out = service.run(kind, cfg, study_name=study)
        line = {"config": Path(name).stem, "run_id": out["run"]["id"], "run_key": out["run"]["run_key"][:12], "reused": out["reused"], "seconds": round(time.time() - started, 1), "error": None}
    except Exception as exc:  # keep going; the log says what failed
        line = {"config": Path(name).stem, "run_id": None, "run_key": None, "reused": False, "seconds": round(time.time() - started, 1), "error": f"{type(exc).__name__}: {exc}"[:300]}
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")
