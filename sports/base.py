"""
Abstract contracts for the multi-sports pipeline.

SportSource  -- fetches raw structured data from an external API
               (events, team form, H2H, standings, market odds).
SportModel   -- pure statistical computation, no LLM.
               compute() returns probabilities; value_bets() is shared logic.

Adding a new sport = one SportSource subclass + one SportModel subclass
+ one entry in registry.py. Nothing else changes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date


class SportSource(ABC):
    """Data provider for a single sport. All methods are async."""

    @abstractmethod
    async def list_events(self, target_date: date, leagues: list[str]) -> list[dict]:
        """Return matches scheduled on target_date for the given league IDs.

        Each returned dict must contain at minimum:
            external_id   -- unique ID on the provider side (str)
            league        -- human-readable league name
            league_id     -- provider league ID
            home_team     -- home team name
            home_team_id  -- provider team ID
            away_team     -- away team name
            away_team_id  -- provider team ID
            kickoff       -- "HH:MM" (local kickoff time)
            date          -- date object
        """

    @abstractmethod
    async def team_form(self, team_id: int | str) -> dict:
        """Return recent form data for a team (last N matches)."""

    @abstractmethod
    async def head_to_head(self, team_a_id: int | str, team_b_id: int | str) -> list[dict]:
        """Return recent head-to-head match history between two teams."""

    @abstractmethod
    async def standings(self, league_id: int | str) -> list[dict]:
        """Return current league standings."""

    @abstractmethod
    async def event_odds(self, event_id: int | str) -> dict:
        """Return decimal market odds for an event. Return {} if unavailable."""


class SportModel(ABC):
    """Pure statistical model -- no LLM calls, deterministic given inputs."""

    @abstractmethod
    def compute(self, event_data: dict) -> dict:
        """Compute match probabilities from enriched event_data."""

    def value_bets(self, model_probs: dict, market_odds: dict) -> list[dict]:
        """Compare model probabilities against market implied probabilities."""
        if not market_odds:
            return []

        STATIC = {
            "1":        ["prob_1", "prob_home"],
            "X":        ["prob_x"],
            "2":        ["prob_2", "prob_away"],
            "home":     ["prob_home", "prob_1"],
            "away":     ["prob_away", "prob_2"],
            "over_2_5": ["prob_over_2_5", "prob_over"],
            "btts":     ["prob_btts"],
        }

        results: list[dict] = []
        for market, odd in market_odds.items():
            if odd is None or not isinstance(odd, (int, float)) or odd <= 1.0:
                continue

            model_prob: float | None = None
            candidates = STATIC.get(market)
            if candidates:
                for key in candidates:
                    if key in model_probs and model_probs[key] is not None:
                        model_prob = float(model_probs[key])
                        break
            elif market.startswith("over_"):
                for key in ("prob_over", "prob_over_2_5"):
                    if key in model_probs and model_probs[key] is not None:
                        model_prob = float(model_probs[key])
                        break

            if model_prob is None:
                continue

            implied_prob = 1.0 / odd
            edge = model_prob - implied_prob
            if edge > 0:
                results.append({
                    "market":       market,
                    "model_prob":   round(model_prob, 4),
                    "implied_prob": round(implied_prob, 4),
                    "decimal_odd":  odd,
                    "edge_pct":     round(edge * 100, 2),
                })

        return sorted(results, key=lambda x: x["edge_pct"], reverse=True)
