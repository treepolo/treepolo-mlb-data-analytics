from treepolo_mlb_data.analysis import Grain
from treepolo_mlb_data.analysis.numerical import ClusteringSpec, NumericalExecutor, NumericalTable


def test_partitioned_clustering_fits_each_entity_separately_and_keeps_full_rows():
    rows = []
    for pitcher, shift in ((10, 0.0), (20, 100.0)):
        for index, x in enumerate((0.0, 0.1, 10.0, 10.1)):
            rows.append({
                "pitch_uid": f"{pitcher}-{index}",
                "pitcher": pitcher,
                "pitch_type": "CH",
                "x": x + shift,
                "y": x + shift,
                "outcome": index,
            })
    table = NumericalTable(
        ("pitch_uid", "pitcher", "pitch_type", "x", "y", "outcome"),
        tuple(rows),
        Grain(("pitch_uid",), "pitch"),
    )
    output = NumericalExecutor().cluster_table(
        table,
        ClusteringSpec(
            features=("x", "y"), clusters=2, method="kmeans", seed=4,
            partition_fields=("pitcher",), id_fields=("pitcher", "pitch_type"),
        ),
    )
    assert output.summary.grain.keys == ("pitcher", "cluster")
    assert len(output.summary.rows) == 4
    assert len(output.assignments.rows) == 8
    assert output.assignments.grain == table.grain
    assert "outcome" in output.assignments.columns
    assert all("cluster" in row and "outcome" in row for row in output.assignments.rows)
    by_pitcher = {10: set(), 20: set()}
    for row in output.assignments.rows:
        by_pitcher[row["pitcher"]].add(row["cluster"])
    assert by_pitcher == {10: {0, 1}, 20: {0, 1}}
