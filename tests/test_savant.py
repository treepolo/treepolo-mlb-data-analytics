from datetime import date
from treepolo_mlb_data.savant import SavantClient, FetchError


def test_client_constructs_with_slots_and_expected_params():
    client = SavantClient(pause_seconds=0)
    params = client.params(date(2024, 4, 1), date(2024, 4, 2))
    assert params["type"] == "details"
    assert params["game_date_gt"] == "2024-04-01"
    assert params["game_date_lt"] == "2024-04-02"
    assert params["hfGT"] == "R|PO|S|"


def test_response_validation_rejects_html():
    try:
        SavantClient._validate(b"<html>blocked</html>")
    except FetchError:
        pass
    else:
        raise AssertionError("HTML response must be rejected")
