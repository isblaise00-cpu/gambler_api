"""
BasketballModel -- pace-adjusted efficiency model with normal-distribution win probability.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.stats import norm

from sports.base import SportModel

_LEAGUE_PARAMS: dict[str, dict] = {
    "nba": {"avg_pts": 225.0, "home_adv": 3.5, "margin_std": 11.0, "total_std": 14.0},
    "euroleague": {"avg_pts": 165.0, "home_adv": 3.0, "margin_std": 10.0, "total_std": 13.0},
    "default": {"avg_pts": 190.0, "home_adv": 3.0, "margin_std": 11.0, "total_std": 14.0},
}

_DECAY     = 0.12
_MIN_GAMES = 3


class BasketballModel(SportModel):

    def compute(self, event_data: dict) -> dict:
        event      = event_data.get("event", {})
        home_form  = event_data.get("home_form", {})
        away_form  = event_data.get("away_form", {})
        odds       = event_data.get("odds", {})
        league_key = event.get("league", "").lower()
        p          = _league_params(league_key)

        half_avg = p["avg_pts"] / 2.0
        home_off, home_def = _weighted_efficiency(home_form, half_avg)
        away_off, away_def = _weighted_efficiency(away_form, half_avg)

        xPts_home = (home_off + away_def) / 2.0 + p["home_adv"] / 2.0
        xPts_away = (away_off + home_def) / 2.0 - p["home_adv"] / 2.0

        expected_total  = xPts_home + xPts_away
        expected_margin = xPts_home - xPts_away

        prob_home = float(norm.cdf(expected_margin / p["margin_std"]))
        prob_away = 1.0 - prob_home

        ou_line = _extract_ou_line(odds)
        if ou_line is not None:
            prob_over = float(1.0 - norm.cdf((ou_line - expected_total) / p["total_std"]))
        else:
            prob_over = 0.5

        spread_line = _extract_spread_line(odds)
        if spread_line is not None:
            cover_prob = float(1.0 - norm.cdf((spread_line - expected_margin) / p["margin_std"]))
        else:
            cover_prob = prob_home

        return {
            "prob_home":         round(prob_home, 4),
            "prob_away":         round(prob_away, 4),
            "expected_total":    round(expected_total, 1),
            "expected_margin":   round(expected_margin, 1),
            "prob_over":         round(prob_over, 4),
            "cover_prob_spread": round(cover_prob, 4),
            "xPts_home":         round(xPts_home, 1),
            "xPts_away":         round(xPts_away, 1),
            "ou_line":           ou_line,
            "spread_line":       spread_line,
        }


def _league_params(league_name: str) -> dict:
    for key, params in _LEAGUE_PARAMS.items():
        if key != "default" and key in league_name:
            return params
    return _LEAGUE_PARAMS["default"]


def _weighted_efficiency(form: dict, fallback: float) -> tuple[float, float]:
    matches = sorted(form.get("last_matches", []), key=lambda m: m.get("date", ""), reverse=True)
    if len(matches) < _MIN_GAMES:
        return fallback, fallback
    weights = [math.exp(-_DECAY * i) for i in range(len(matches))]
    total_w = sum(weights)
    off_ = sum(w * m["points_for"]     for w, m in zip(weights, matches)) / total_w
    def_ = sum(w * m["points_against"] for w, m in zip(weights, matches)) / total_w
    return off_, def_


def _extract_ou_line(odds: dict) -> float | None:
    for key in odds:
        if key.startswith("over_"):
            try: return float(key[5:])
            except ValueError: pass
    return None


def _extract_spread_line(odds: dict) -> float | None:
    for key in odds:
        if key.startswith("spread_home_"):
            try: return float(key[12:])
            except ValueError: pass
    return None
