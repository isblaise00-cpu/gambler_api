"""
Sport registry -- maps sport codes to their (SportSource, SportModel) classes.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import SportSource, SportModel


def _build_registry() -> dict[str, tuple[type, type]]:
    from .sources.football_source import FootballSource
    from .sources.basketball_source import BasketballSource
    from .models.football_model import FootballModel
    from .models.basketball_model import BasketballModel

    return {
        "FOOTBALL":   (FootballSource, FootballModel),
        "BASKETBALL": (BasketballSource, BasketballModel),
    }


def known_sports() -> list[str]:
    return list(_build_registry().keys())


def get_source(sport: str) -> "SportSource":
    reg = _build_registry()
    if sport not in reg:
        raise ValueError(f"Sport inconnu : {sport!r}. Sports disponibles : {list(reg)}")
    source_cls, _ = reg[sport]
    return source_cls()


def get_model(sport: str) -> "SportModel":
    reg = _build_registry()
    if sport not in reg:
        raise ValueError(f"Sport inconnu : {sport!r}. Sports disponibles : {list(reg)}")
    _, model_cls = reg[sport]
    return model_cls()
