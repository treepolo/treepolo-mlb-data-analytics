from __future__ import annotations

from datetime import date

import pytest

from treepolo_mlb_data.cpbl_client import CPBLClient


pytestmark = pytest.mark.integration


def test_live_cpbl_schedule_and_game_contract():
    client = CPBLClient(timeout_seconds=30, retries=2, backoff_seconds=0.5, pause_seconds=0.0)
    games = client.schedule(date(2026, 5, 10))
    assert games, "CPBL schedule endpoint returned no games for a known played date"

    ids = {str(game.get("GameId") or game.get("gameId") or "") for game in games}
    assert "2026-A-91" in ids

    game = client.game("2026-A-91")
    assert str(game.get("GameId") or game.get("gameId") or "") == "2026-A-91"
    assert isinstance(game.get("LiveLog"), list)
