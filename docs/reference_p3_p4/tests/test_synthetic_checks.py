from __future__ import annotations

import pytest

from treepolo_mlb_data.research.synthetic import run_checks


def test_every_known_answer_check_passes(tmp_path):
    rows = run_checks(workdir=tmp_path)
    failed = [r for r in rows if not r["passed"]]
    assert not failed, failed
    assert {r["world"] for r in rows} >= {"T0", "T1", "T2", "T3", "impossible", "M0", "M1"}


def test_run_checks_rejects_small_worlds_and_unknown_groups(tmp_path):
    with pytest.raises(ValueError):
        run_checks(n_pa=1000, workdir=tmp_path)
    with pytest.raises(ValueError):
        run_checks(groups=("nope",), workdir=tmp_path)
