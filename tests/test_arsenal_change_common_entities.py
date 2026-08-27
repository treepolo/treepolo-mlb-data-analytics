import sqlite3

from treepolo_mlb_data.web_analysis import AnalysisFacade


def make_db(path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE pitches (
        pitch_uid TEXT PRIMARY KEY,
        game_date TEXT,
        pitcher INTEGER,
        pitch_type TEXT
    )""")
    rows = []
    uid = 0

    def add(date, pitcher, pitch_type, count):
        nonlocal uid
        for _ in range(count):
            uid += 1
            rows.append((f"p{uid}", date, pitcher, pitch_type))

    # Pitcher 10 appears only in period A. None of these pitches should be
    # reported as removed just because the pitcher has no period-B sample.
    add("2026-04-01", 10, "FF", 8)
    add("2026-04-01", 10, "SL", 2)

    # Pitcher 20 appears in both periods and genuinely changes arsenal.
    add("2026-04-02", 20, "FF", 8)
    add("2026-04-02", 20, "CH", 2)
    add("2026-07-02", 20, "FF", 6)
    add("2026-07-02", 20, "SL", 4)

    # Pitcher 30 appears only in period B. None of these pitches should be
    # reported as added without a period-A comparison sample.
    add("2026-07-03", 30, "FF", 7)
    add("2026-07-03", 30, "CH", 3)

    conn.executemany("INSERT INTO pitches VALUES (?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_arsenal_change_compares_only_entities_present_in_both_periods(tmp_path):
    path = tmp_path / "arsenal_change.sqlite"
    make_db(path)

    result = AnalysisFacade(path, backend="sqlite").analyze({
        "mode": "arsenal_change",
        "entity_fields": ["pitcher"],
        "min_usage": 0.05,
        "period_a": {"start": "2026-04-01", "end": "2026-05-31"},
        "period_b": {"start": "2026-06-01", "end": "2026-08-31"},
    })

    added = result["sections"][0]["rows"]
    removed = result["sections"][1]["rows"]

    assert added == [{"pitcher": 20, "pitch_type": "SL"}]
    assert removed == [{"pitcher": 20, "pitch_type": "CH"}]
