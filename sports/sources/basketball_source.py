"""
BasketballSource -- fetches structured basketball data from API-Basketball (api-sports.io).

Environment variables:
    BASKETBALL_API_KEY   -- api-sports.io key
    BASKETBALL_API_BASE  -- base URL (default: https://v2.nba.api-sports.io)
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from pathlib import Path

import aiohttp
from loguru import logger

from sports.base import SportSource

_BASE_URL   = os.getenv("BASKETBALL_API_BASE", "https://v2.nba.api-sports.io")
_CACHE_ROOT = Path("./_sports_cache/BASKETBALL")
_REQUEST_DELAY = 0.3


def _cache_path(endpoint: str, params: dict) -> Path:
    key = json.dumps({"ep": endpoint, **params}, sort_keys=True)
    h = hashlib.md5(key.encode()).hexdigest()[:12]
    return _CACHE_ROOT / date.today().isoformat() / f"{h}.json"


def _current_season(ref: date) -> str:
    if ref.month >= 10:
        return str(ref.year)
    elif ref.month <= 6:
        return str(ref.year - 1)
    else:
        return str(ref.year)


class BasketballSource(SportSource):

    def _headers(self) -> dict[str, str]:
        key = os.getenv("BASKETBALL_API_KEY", "")
        if not key:
            logger.warning("[BasketballSource] BASKETBALL_API_KEY manquante")
        return {"x-apisports-key": key}

    async def _get(self, session: aiohttp.ClientSession, endpoint: str, params: dict) -> dict:
        import asyncio
        cache_file = _cache_path(endpoint, params)
        if cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))
        url = f"{_BASE_URL}/{endpoint}"
        await asyncio.sleep(_REQUEST_DELAY)
        async with session.get(url, params=params, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            data = await resp.json()
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data), encoding="utf-8")
        return data

    async def list_events(self, target_date: date, leagues: list[str]) -> list[dict]:
        if not os.getenv("BASKETBALL_API_KEY"):
            return []
        season = _current_season(target_date)
        events: list[dict] = []
        async with aiohttp.ClientSession() as session:
            for league_id in leagues:
                try:
                    data = await self._get(session, "games", {"date": target_date.isoformat(), "league": str(league_id), "season": season})
                    for item in data.get("response", []):
                        league = item.get("league", {}); teams = item.get("teams", {})
                        home = teams.get("home", {}); away = teams.get("away", {})
                        raw_date = item.get("date", "")
                        kickoff = raw_date[11:16] if len(raw_date) >= 16 else "00:00"
                        events.append({"external_id": str(item.get("id", "")), "league": league.get("name", ""), "league_id": league.get("id", league_id), "home_team": home.get("name", ""), "home_team_id": home.get("id"), "away_team": away.get("name", ""), "away_team_id": away.get("id"), "kickoff": kickoff, "date": target_date, "status": item.get("status", {}).get("short", "NS")})
                except Exception as exc:
                    logger.error(f"[BasketballSource] list_events league={league_id}: {exc}")
        return events

    async def team_form(self, team_id: int | str) -> dict:
        if not os.getenv("BASKETBALL_API_KEY"):
            return _empty_form(team_id)
        season = _current_season(date.today())
        async with aiohttp.ClientSession() as session:
            try:
                data = await self._get(session, "games", {"team": str(team_id), "last": "10", "season": season})
                return _parse_basketball_form(int(team_id), data.get("response", []))
            except Exception as exc:
                logger.error(f"[BasketballSource] team_form team={team_id}: {exc}")
                return _empty_form(team_id)

    async def head_to_head(self, team_a_id: int | str, team_b_id: int | str) -> list[dict]:
        if not os.getenv("BASKETBALL_API_KEY"):
            return []
        async with aiohttp.ClientSession() as session:
            try:
                data = await self._get(session, "games/h2h", {"h2h": f"{team_a_id}-{team_b_id}", "last": "10"})
                return _parse_basketball_h2h(data.get("response", []))
            except Exception as exc:
                logger.error(f"[BasketballSource] head_to_head {team_a_id}-{team_b_id}: {exc}")
                return []

    async def standings(self, league_id: int | str) -> list[dict]:
        if not os.getenv("BASKETBALL_API_KEY"):
            return []
        season = _current_season(date.today())
        async with aiohttp.ClientSession() as session:
            try:
                data = await self._get(session, "standings", {"league": str(league_id), "season": season})
                return [_parse_basketball_standing(e) for e in data.get("response", [])]
            except Exception as exc:
                logger.error(f"[BasketballSource] standings league={league_id}: {exc}")
                return []

    async def event_odds(self, event_id: int | str) -> dict:
        if not os.getenv("BASKETBALL_API_KEY"):
            return {}
        async with aiohttp.ClientSession() as session:
            try:
                data = await self._get(session, "odds", {"game": str(event_id)})
                return _parse_basketball_odds(data.get("response", []))
            except Exception as exc:
                logger.error(f"[BasketballSource] event_odds game={event_id}: {exc}")
                return {}


def _parse_basketball_form(team_id: int, games: list[dict]) -> dict:
    last_matches = []; wins = losses = total_scored = total_conceded = 0; form_chars = []
    for g in games:
        teams = g.get("teams", {}); scores = g.get("scores", {})
        is_home = teams.get("home", {}).get("id") == team_id
        home_pts = _total_points(scores.get("home", {})); away_pts = _total_points(scores.get("away", {}))
        if home_pts is None or away_pts is None: continue
        pts_for = home_pts if is_home else away_pts; pts_against = away_pts if is_home else home_pts
        won = pts_for > pts_against
        if won: wins += 1; form_chars.append("W")
        else: losses += 1; form_chars.append("L")
        total_scored += pts_for; total_conceded += pts_against
        last_matches.append({"date": g.get("date", "")[:10], "result": "W" if won else "L", "points_for": pts_for, "points_against": pts_against, "is_home": is_home})
    n = len(last_matches) or 1
    return {"team_id": team_id, "last_matches": last_matches, "wins": wins, "draws": 0, "losses": losses, "goals_scored": total_scored, "goals_conceded": total_conceded, "points_scored_avg": round(total_scored / n, 2), "points_conceded_avg": round(total_conceded / n, 2), "form_string": "".join(form_chars[-5:])}


def _total_points(score_obj: dict) -> int | None:
    total = score_obj.get("total")
    if total is not None:
        try: return int(total)
        except (TypeError, ValueError): pass
    quarters = [score_obj.get(f"quarter_{i}") for i in range(1, 5)]
    if all(q is not None for q in quarters):
        try: return sum(int(q) for q in quarters)
        except (TypeError, ValueError): pass
    return None


def _parse_basketball_h2h(games: list[dict]) -> list[dict]:
    result = []
    for g in games:
        teams = g.get("teams", {}); scores = g.get("scores", {})
        result.append({"date": g.get("date", "")[:10], "home_team": teams.get("home", {}).get("name", ""), "home_score": _total_points(scores.get("home", {})), "away_score": _total_points(scores.get("away", {})), "away_team": teams.get("away", {}).get("name", "")})
    return result


def _parse_basketball_standing(entry: dict) -> dict:
    team = entry.get("team", {}); games = entry.get("games", {}); points = entry.get("points", {})
    return {"rank": entry.get("position"), "team": team.get("name", ""), "team_id": team.get("id"), "wins": games.get("win", {}).get("total"), "losses": games.get("lose", {}).get("total"), "draws": 0, "points": None, "played": (games.get("win", {}).get("total") or 0) + (games.get("lose", {}).get("total") or 0), "goals_for": points.get("for"), "goals_against": points.get("against"), "goal_diff": None}


def _parse_basketball_odds(response: list[dict]) -> dict:
    odds: dict = {}
    if not response: return odds
    bookmakers = response[0].get("bookmakers", []); bm = bookmakers[0] if bookmakers else {}
    for bet in bm.get("bets", []):
        name = bet.get("name", ""); values = bet.get("values", [])
        if name in ("Home/Away", "Money Line", "Moneyline"):
            for v in values:
                label = v.get("value", "")
                try: odd = float(v["odd"])
                except (KeyError, ValueError): continue
                if label == "Home": odds["home"] = odd
                elif label == "Away": odds["away"] = odd
        elif "Over/Under" in name or name == "Total":
            for v in values:
                label = v.get("value", "")
                try: odd = float(v["odd"])
                except (KeyError, ValueError): continue
                if label.startswith("Over"):
                    parts = label.split(); line = parts[1] if len(parts) > 1 else "0"
                    odds[f"over_{line}"] = odd
    return odds


def _empty_form(team_id) -> dict:
    return {"team_id": team_id, "last_matches": [], "wins": 0, "draws": 0, "losses": 0, "goals_scored": 0, "goals_conceded": 0, "points_scored_avg": 0.0, "points_conceded_avg": 0.0, "form_string": ""}
