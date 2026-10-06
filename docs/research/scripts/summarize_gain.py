"""Print the sequence-gain section of outcome_model runs: python summarize_gain.py RUN_ID [RUN_ID ...] (run from the repo root)."""
import sys
from treepolo_mlb_data.config import AppConfig
from treepolo_mlb_data.research.service import ResearchService

service = ResearchService(AppConfig(), None)
for rid in map(int, sys.argv[1:]):
    run = service.store.get_run(rid); c = run["config"]
    print(f"== run {rid}: models={c['models']} memory={c['memory']} ladder={c['sequence_ladder']} entity={c['entity_features']} seed={c['seed']} early_stopping={c['hgb_early_stopping']}")
    for r in service.store.load_result(rid)["sections"][1]["rows"]:
        print(f"   {r['group']} {r['model']:8s} {r['comparison']:9s} gain {r['delta_logloss']:+.5f} [{r['lo']:+.5f}, {r['hi']:+.5f}]  ev_mse_gain {r['delta_ev_mse']:+.2e} [{r['ev_lo']:+.1e}, {r['ev_hi']:+.1e}]")
