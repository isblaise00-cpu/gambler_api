"""
sports_lite.py -- Pipeline sports leger : matchs + prediction BSD uniquement.

Beaucoup plus rapide que sports_engine.py :
  - Pas de form, H2H, standings, cotes de marche
  - Pas d'appel LLM
  - 1 appel BSD pour lister les matchs par ligue
  - 1 appel BSD par match pour la prediction

Retourne pour chaque match :
  { league, home_team, away_team, kickoff, prediction }

Sports supportes : FOOTBALL, BASKETBALL (via BSD API)
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

_FOOTBALL_BASE_URL   = os.getenv("BSD_FOOTBALL_BASE",    "https://sports.bzzoiro.com/api/v2")
_BASKETBALL_BASE_URL = os.getenv("BSD_BASKETBALL_BASE",  "https://sports.bzzoiro.com/basketball/api/v2")
_CACHE_ROOT = Path("./_sports_cache/LITE")
_MAX_EVENTS = int(os.getenv("MAX_SPORT_EVENTS", "20"))

_FOOTBALL_LEAGUE_NAMES: dict[str, str] = {
    "1": "Premier League",
    "3": "La Liga",
    "4": "Serie A",
    "5": "Bundesliga",
    "6": "Ligue 1",
}

_BASKETBALL_LEAGUE_NAMES: dict[str, str] = {
    "1": "NBA",
    "2": "Euroleague",
    "3": "Liga ACB",
    "4": "Lega A Basket",
    "5": "Germany BBL",
    "6": "Eurocup",
}

# Cache season_id en memoire (football uniquement)
_season_cache: dict[str, str] = {}


def _cache_path(sport: str, endpoint: str, params: dict) -> Path:
    key = json.dumps({"sport": sport, "ep": endpoint, **params}, sort_keys=True)
    h   = hashlib.md5(key.encode()).hexdigest()[:12]
    return _CACHE_ROOT / date.today().isoformat() / f"{h}.json"


def _headers() -> dict[str, str]:
    return {"Authorization": f"Token {os.getenv('BSD_FOOTBALL_TOKEN', '')}", "Accept": "application/json"}


async def _get(session: aiohttp.ClientSession, base_url: str, sport: str, endpoint: str, params: dict) -> dict | list:
    cache_file = _cache_path(sport, endpoint, params)
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    url = f"{base_url}/{endpoint.lstrip('/')}"
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
        data = await _get(session, _FOOTBALL_BASE_URL, "FOOTBALL", f"leagues/{league_id}/season/", {})
        sid = str(data.get("season", {}).get("id", ""))
        if sid:
            _season_cache[league_id] = sid
        return sid or None
    except Exception:
        return None


# ── Football ──────────────────────────────────────────────────────────────────

async def _fetch_football_prediction(session: aiohttp.ClientSession, event_id: str) -> dict:
    try:
        data    = await _get(session, _FOOTBALL_BASE_URL, "FOOTBALL", f"events/{event_id}/prediction/", {})
        markets = data.get("markets", {})
        reco    = data.get("recommendations", {})
        mr      = markets.get("match_result", {})
        return {
            "prob_home":    mr.get("prob_home"),
            "prob_draw":    mr.get("prob_draw"),
            "prob_away":    mr.get("prob_away"),
            "predicted":    mr.get("predicted"),
            "xg_home":      markets.get("expected_goals", {}).get("home"),
            "xg_away":      markets.get("expected_goals", {}).get("away"),
            "bet_favorite": reco.get("bet_favorite", False),
            "rec_over_25":  reco.get("over_25", False),
            "rec_btts":     reco.get("btts", False),
        }
    except Exception:
        return {}


async def _generate_football(session: aiohttp.ClientSession, target: date, leagues: list[str]) -> list[dict]:
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
            data = await _get(session, _FOOTBALL_BASE_URL, "FOOTBALL", "events/", params)
            result = []
            for item in data.get("results", []):
                raw_date = item.get("event_date", "")
                result.append({
                    "external_id": str(item["id"]),
                    "league":      _FOOTBALL_LEAGUE_NAMES.get(league_id, item.get("league_name", f"League {league_id}")),
                    "home_team":   item.get("home_team", ""),
                    "away_team":   item.get("away_team", ""),
                    "kickoff":     raw_date[11:16] if len(raw_date) >= 16 else "00:00",
                })
            return result
        except Exception as exc:
            logger.error(f"[SportsLite/Football] fetch_league {league_id}: {exc}")
            return []

    league_results = await asyncio.gather(*[fetch_league(lid) for lid in leagues])
    events: list[dict] = []
    for evs in league_results:
        events.extend(evs)

    if not events:
        return []
    if len(events) > _MAX_EVENTS:
        events = events[:_MAX_EVENTS]

    predictions = await asyncio.gather(*[
        _fetch_football_prediction(session, ev["external_id"]) for ev in events
    ])

    label = {"H": "Domicile", "D": "Nul", "A": "Exterieur"}
    results = []
    for ev, pred in zip(events, predictions):
        results.append({
            "league":    ev["league"],
            "home_team": ev["home_team"],
            "away_team": ev["away_team"],
            "kickoff":   ev["kickoff"],
            "prediction": {
                "winner":      label.get(pred.get("predicted"), "Inconnu"),
                "prob_home":   pred.get("prob_home"),
                "prob_draw":   pred.get("prob_draw"),
                "prob_away":   pred.get("prob_away"),
                "xg_home":     pred.get("xg_home"),
                "xg_away":     pred.get("xg_away"),
                "rec_over_25": pred.get("rec_over_25", False),
                "rec_btts":    pred.get("rec_btts", False),
            },
        })
    return results


# ── Basketball ────────────────────────────────────────────────────────────────

async def _generate_basketball(session: aiohttp.ClientSession, target: date, leagues: list[str]) -> list[dict]:
    async def fetch_league(league_id: str) -> list[dict]:
        try:
            params: dict = {
                "date_from": target.isoformat(),
                "date_to":   target.isoformat(),
                "league":    league_id,
                "limit":     "50",
            }
            data = await _get(session, _BASKETBALL_BASE_URL, "BASKETBALL", "events/", params)
            result = []
            for item in data.get("results", []):
                raw_date  = item.get("event_date", "")
                league_obj = item.get("league", {})
                home_obj   = item.get("home_team", {})
                away_obj   = item.get("away_team", {})
                result.append({
                    "external_id": str(item["id"]),
                    "league":      _BASKETBALL_LEAGUE_NAMES.get(
                        league_id,
                        league_obj.get("name", f"League {league_id}") if isinstance(league_obj, dict) else f"League {league_id}",
                    ),
                    "home_team":   item.get("home_team_name") or (home_obj.get("name", "") if isinstance(home_obj, dict) else str(home_obj)),
                    "away_team":   item.get("away_team_name") or (away_obj.get("name", "") if isinstance(away_obj, dict) else str(away_obj)),
                    "home_score":  item.get("home_score"),
                    "away_score":  item.get("away_score"),
                    "status":      item.get("status", ""),
                    "kickoff":     raw_date[11:16] if len(raw_date) >= 16 else "00:00",
                })
            return result
        except Exception as exc:
            logger.error(f"[SportsLite/Basketball] fetch_league {league_id}: {exc}")
            return []

    league_results = await asyncio.gather(*[fetch_league(lid) for lid in leagues])
    events: list[dict] = []
    for evs in league_results:
        events.extend(evs)

    if not events:
        return []
    if len(events) > _MAX_EVENTS:
        events = events[:_MAX_EVENTS]

    results = []
    for ev in events:
        results.append({
            "league":     ev["league"],
            "home_team":  ev["home_team"],
            "away_team":  ev["away_team"],
            "kickoff":    ev["kickoff"],
            "home_score": ev.get("home_score"),
            "away_score": ev.get("away_score"),
            "status":     ev.get("status"),
            "prediction": {},
        })
    return results


# ── Basketball : discovery ────────────────────────────────────────────────────

async def list_basketball_leagues(target: date) -> list[dict]:
    """Retourne tous les championnats basketball avec au moins 1 match a la date."""
    token = os.getenv("BSD_FOOTBALL_TOKEN", "")
    if not token:
        return []
    async with aiohttp.ClientSession() as session:
        try:
            data = await _get(session, _BASKETBALL_BASE_URL, "BASKETBALL", "events/", {
                "date_from": target.isoformat(),
                "date_to":   target.isoformat(),
                "limit":     "500",
            })
            counts: dict[str, dict] = {}
            for item in data.get("results", []):
                league_obj = item.get("league", {})
                lid = str(league_obj.get("id", "")) if isinstance(league_obj, dict) else ""
                if not lid:
                    continue
                if lid not in counts:
                    counts[lid] = {
                        "league_id":   lid,
                        "league_name": league_obj.get("name", f"League {lid}"),
                        "country":     league_obj.get("country", ""),
                        "match_count": 0,
                    }
                counts[lid]["match_count"] += 1
            return sorted(counts.values(), key=lambda x: x["league_name"])
        except Exception as exc:
            logger.error(f"[SportsLite/Basketball] list_leagues: {exc}")
            return []


async def list_basketball_matches(target: date, league_id: str) -> list[dict]:
    """Retourne les matchs basketball d'une ligue pour la date donnee."""
    token = os.getenv("BSD_FOOTBALL_TOKEN", "")
    if not token:
        return []
    async with aiohttp.ClientSession() as session:
        try:
            data = await _get(session, _BASKETBALL_BASE_URL, "BASKETBALL", "events/", {
                "date_from": target.isoformat(),
                "date_to":   target.isoformat(),
                "league":    league_id,
                "limit":     "200",
            })
            results = []
            for item in data.get("results", []):
                raw_date  = item.get("event_date", "")
                home_obj  = item.get("home_team", {})
                away_obj  = item.get("away_team", {})
                results.append({
                    "id":         str(item["id"]),
                    "home_team":  item.get("home_team_name") or (home_obj.get("name", "") if isinstance(home_obj, dict) else ""),
                    "away_team":  item.get("away_team_name") or (away_obj.get("name", "") if isinstance(away_obj, dict) else ""),
                    "kickoff":    raw_date[11:16] if len(raw_date) >= 16 else "00:00",
                    "status":     item.get("status", ""),
                    "home_score": item.get("home_score"),
                    "away_score": item.get("away_score"),
                })
            return results
        except Exception as exc:
            logger.error(f"[SportsLite/Basketball] list_matches league={league_id}: {exc}")
            return []


# ── Entrypoint ────────────────────────────────────────────────────────────────

async def generate_sport_lite(sport: str, target: date, leagues: list[str]) -> list[dict]:
    token = os.getenv("BSD_FOOTBALL_TOKEN", "")
    if not token:
        logger.warning("[SportsLite] BSD_FOOTBALL_TOKEN manquant")
        return []

    async with aiohttp.ClientSession() as session:
        if sport == "BASKETBALL":
            results = await _generate_basketball(session, target, leagues)
        else:
            results = await _generate_football(session, target, leagues)

    logger.info(f"[SportsLite/{sport}] {len(results)} matchs retournes.")
    return results
