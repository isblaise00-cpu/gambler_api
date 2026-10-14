"""
sports_lite.py -- Pipeline sports leger : matchs + prediction BSD uniquement.

Beaucoup plus rapide que sports_engine.py :
  - Pas de form, H2H, standings, cotes de marche
  - Pas d'appel LLM
  - 1 appel BSD pour lister les matchs par ligue
  - 1 appel BSD par match pour la prediction

Retourne pour chaque match :
  { league, home_team, away_team, kickoff, prediction }
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import date
from pathlib import Path

import aiohttp
from loguru import logger

_BASE_URL   = os.getenv("BSD_FOOTBALL_BASE", "https://sports.bzzoiro.com/api/v2")
_CACHE_ROOT = Path("./_sports_cache/LITE")
_MAX_EVENTS = int(os.getenv("MAX_SPORT_EVENTS", "20"))

# Cache season_id en memoire
_season_cache: dict[str, str] = {}


def _cache_path(endpoint: str, params: dict) -> Path:
    key = json.dumps({"ep": endpoint, **params}, sort_keys=True)
    h   = hashlib.md5(key.encode()).hexdigest()[:12]
    return _CACHE_ROOT / date.today().isoformat() / f"{h}.json"


def _headers() -> dict[str, str]:
    return {"Authorization": f"Token {os.getenv('BSD_FOOTBALL_TOKEN', '')}", "Accept": "application/json"}


async def _get(session: aiohttp.ClientSession, endpoint: str, params: dict) -> dict | list:
    cache_file = _cache_path(endpoint, params)
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    url = f"{_BASE_URL}/{endpoint.lstrip('/')}"
    async with session.get(url, params=params, headers=_headers(), timeout=aiohttp.ClientTimeout(total=15)) as resp:
        resp.raise_for_status()
        data = await resp.json()
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(data), encoding="utf-8")
    return data


async def _get_season_id(session: aiohttp.ClientSession, league_id: str) -> str | None:
    if league_id in _season_cache:
        return _season_cache[league_id]
    try:
        data = await _get(session, f"leagues/{league_id}/season/", {})
        sid = str(data.get("season", {}).get("id", ""))
        if sid:
            _season_cache[league_id] = sid
        return sid or None
    except Exception:
        return None


async def _fetch_prediction(session: aiohttp.ClientSession, event_id: str) -> dict:
    try:
        data = await _get(session, f"events/{event_id}/prediction/", {})
        markets = data.get("markets", {})
        reco    = data.get("recommendations", {})
        mr      = markets.get("match_result", {})
        return {
            "prob_home":    mr.get("prob_home"),
            "prob_draw":    mr.get("prob_draw"),
            "prob_away":    mr.get("prob_away"),
            "predicted":    mr.get("predicted"),       # "H", "D" ou "A"
            "xg_home":      markets.get("expected_goals", {}).get("home"),
            "xg_away":      markets.get("expected_goals", {}).get("away"),
            "bet_favorite": reco.get("bet_favorite", False),
            "rec_over_25":  reco.get("over_25", False),
            "rec_btts":     reco.get("btts", False),
        }
    except Exception:
        return {}


async def generate_sport_lite(sport: str, target: date, leagues: list[str]) -> list[dict]:
    token = os.getenv("BSD_FOOTBALL_TOKEN", "")
    if not token:
        logger.warning("[SportsLite] BSD_FOOTBALL_TOKEN manquant")
        return []

    events: list[dict] = []

    async with aiohttp.ClientSession() as session:
        # 1. Recuperer les matchs de chaque ligue en parallele
        async def fetch_league(league_id: str) -> list[dict]:
            try:
                season_id = await _get_season_id(session, league_id)
                params: dict = {
                    "date_from": target.isoformat(),
                    "date_to":   target.isoformat(),
                    "league_id": league_id,
                    "limit":     "50",
                }
                if season_id:
                    params["season_id"] = season_id
                data = await _get(session, "events/", params)
                result = []
                for item in data.get("results", []):
                    raw_date = item.get("event_date", "")
                    result.append({
                        "external_id": str(item["id"]),
                        "league":      item.get("league_name", f"League {league_id}"),
                        "home_team":   item.get("home_team", ""),
                        "away_team":   item.get("away_team", ""),
                        "kickoff":     raw_date[11:16] if len(raw_date) >= 16 else "00:00",
                    })
                return result
            except Exception as exc:
                logger.error(f"[SportsLite] fetch_league {league_id}: {exc}")
                return []

        league_results = await asyncio.gather(*[fetch_league(lid) for lid in leagues])
        for league_evs in league_results:
            events.extend(league_evs)

        if not events:
            return []

        # Limiter le total
        if len(events) > _MAX_EVENTS:
            logger.info(f"[SportsLite] {len(events)} matchs -> limite a {_MAX_EVENTS}")
            events = events[:_MAX_EVENTS]

        # 2. Recuperer les predictions en parallele
        predictions = await asyncio.gather(*[
            _fetch_prediction(session, ev["external_id"]) for ev in events
        ])

    # 3. Assembler la reponse legere
    results = []
    label = {"H": "Domicile", "D": "Nul", "A": "Exterieur"}
    for ev, pred in zip(events, predictions):
        predicted_key = pred.get("predicted")
        results.append({
            "league":     ev["league"],
            "home_team":  ev["home_team"],
            "away_team":  ev["away_team"],
            "kickoff":    ev["kickoff"],
            "prediction": {
                "winner":       label.get(predicted_key, "Inconnu"),
                "prob_home":    pred.get("prob_home"),
                "prob_draw":    pred.get("prob_draw"),
                "prob_away":    pred.get("prob_away"),
                "xg_home":      pred.get("xg_home"),
                "xg_away":      pred.get("xg_away"),
                "rec_over_25":  pred.get("rec_over_25", False),
                "rec_btts":     pred.get("rec_btts", False),
            },
        })

    logger.info(f"[SportsLite] {len(results)} matchs retournes.")
    return results
