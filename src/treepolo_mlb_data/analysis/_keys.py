from __future__ import annotations

from .model import Column, OrderKey

# A plate appearance is (game_pk, at_bat_number); pitches inside it are ordered by pitch_number.
PA = (Column("game_pk"), Column("at_bat_number"))
ORDER = (OrderKey(Column("pitch_number")),)
