"""
FootballSource (BSD) -- donnees football via BSD API (sports.bzzoiro.com).

Environment variables:
    BSD_FOOTBALL_TOKEN  -- token d'authentification (requis)
    BSD_FOOTBALL_BASE   -- base URL (defaut: https://sports.bzzoiro.com/api/v2)
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

from sports.base import SportSource

_BASE_URL   = os.getenv("BSD_FOOTBALL_BASE", "https://sports.bzzoiro.com/api/v2")
_CACHE_ROOT = Path("./_sports_cache/FOOTBALL")
_DELAY      = 0.2

# Cache en memoire des season_id par league_id (valide toute la journee)
_season_cache: dict[str, str] = {}


def _cache_path(endpoint: str, params: dict) -> Path:
    key = json.dumps({"ep": endpoint, **params}, sort_keys=True)
    h   = hashlib.md5(key.encode()).hexdigest()[:12]
    return _CACHE_ROOT / date.today().isoformat() / f"{h}.json"


class FootballSource(SportSource):

    def _headers(self) -> dict[str, str]:
        token = os.getenv("BSD_FOOTBALL_TOKEN", "")
        if not token:
            logger.warning("[FootballSource] BSD_FOOTBALL_TOKEN manquant")
        return {"Authorization": f"Token {token}", "Accept": "application/json"}

    async def _get(self, session: aiohttp.ClientSession, endpoint: str, params: dict, *, use_cache: bool = True) -> dict | list:
        cache_file = _cache_path(endpoint, params)
        if use_cache and cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))

        url = f"{_BASE_URL}/{endpoint.lstrip('/')}"
        await asyncio.sleep(_DELAY)
        async with session.get(url, params=params, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            data = await resp.json()

        if use_cache:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data), encoding="utf-8")
        return data

    async def _get_season_id(self, session: aiohttp.ClientSession, league_id: str) -> str | None:
        """Recupere le season_id de la saison en cours pour une ligue."""
        if league_id in _season_cache:
            return _season_cache[league_id]
        try:
            data = await self._get(session, f"leagues/{league_id}/season/", {})
            season = data.get("season", {})
            season_id = str(season.get("id", "")) if season else None
            if season_id:
                _season_cache[league_id] = season_id
                logger.info(f"[FootballSource] Saison ligue {league_id} : {season.get('name')} (id={season_id})")
            return season_id
        except Exception as exc:
            logger.warning(f"[FootballSource] Impossible de recuperer le season_id pour ligue {league_id}: {exc}")
            return None

    async def list_all_leagues_with_events(self, target_date: date) -> list[dict]:
        """
        Recupere tous les championnats qui ont au moins 1 match a la date donnee.
        1 requete /events/ pour grouper par league_id, puis N requetes /leagues/{id}/
        en parallele pour resoudre les noms.

        Retourne :
          [ { league_id, league_name, country, match_count } ] trie par league_name
        """
        if not os.getenv("BSD_FOOTBALL_TOKEN"):
            return []
        async with aiohttp.ClientSession() as session:
            try:
                params = {
                    "date_from": target_date.isoformat(),
                    "date_to":   target_date.isoformat(),
                    "limit":     "500",
                }
                data = await self._get(session, "events/", params)
                counts: dict[str, int] = {}
                for item in data.get("results", []):
                    lid = str(item.get("league_id", ""))
                    if lid:
                        counts[lid] = counts.get(lid, 0) + 1

                async def _fetch_league(lid: str) -> dict:
                    try:
                        info = await self._get(session, f"leagues/{lid}/", {})
                        return {
                            "league_id":   lid,
                            "league_name": info.get("name", f"League {lid}"),
                            "country":     info.get("country", ""),
                            "match_count": counts[lid],
                        }
                    except Exception:
                        return {
                            "league_id":   lid,
                            "league_name": f"League {lid}",
                            "country":     "",
                            "match_count": counts[lid],
                        }

                leagues = await asyncio.gather(*[_fetch_league(lid) for lid in counts])
                return sorted(leagues, key=lambda x: x["league_name"])
            except Exception as exc:
                logger.error(f"[FootballSource] list_all_leagues_with_events: {exc}")
                return []

    async def list_events(self, target_date: date, leagues: list[str]) -> list[dict]:
        if not os.getenv("BSD_FOOTBALL_TOKEN"):
            return []
        events: list[dict] = []
        async with aiohttp.ClientSession() as session:
            for league_id in leagues:
                try:
                    season_id = await self._get_season_id(session, league_id)
                    params = {
                        "date_from": target_date.isoformat(),
                        "date_to":   target_date.isoformat(),
                        "league_id": str(league_id),
                        "limit":     "200",
                    }
                    if season_id:
                        params["season_id"] = season_id
                    data = await self._get(session, "events/", params)
                    for item in data.get("results", []):
                        raw_date = item.get("event_date", "")
                        kickoff  = raw_date[11:16] if len(raw_date) >= 16 else "00:00"
                        events.append({
                            "external_id":   str(item["id"]),
                            "league":        item.get("league_name", f"League {league_id}"),
                            "league_id":     league_id,
                            "season_id":     season_id,
                            "home_team":     item.get("home_team", ""),
                            "home_team_id":  item.get("home_team_id"),
                            "away_team":     item.get("away_team", ""),
                            "away_team_id":  item.get("away_team_id"),
                            "kickoff":       kickoff,
                            "date":          target_date,
                            "status":        item.get("status", "notstarted"),
                            "round":         item.get("round_name", ""),
                            "is_neutral":    item.get("is_neutral_ground", False),
                        })
                except Exception as exc:
                    logger.error(f"[FootballSource] list_events league={league_id}: {exc}")
        return events

    async def list_events_with_predictions(self, target_date: date, league_id: str) -> list[dict]:
        """
        Recupere les matchs d'une ligue + la prediction BSD pour chaque match en parallele.

        Retourne :
          [ { external_id, home_team, away_team, kickoff, status, round, prediction } ]
        """
        if not os.getenv("BSD_FOOTBALL_TOKEN"):
            return []
        events = await self.list_events(target_date, [league_id])
        if not events:
            return []

        async with aiohttp.ClientSession() as session:
            async def _fetch_pred(ev: dict) -> dict:
                try:
                    pred_raw = await self._get(session, f"events/{ev['external_id']}/prediction/", {})
                    prediction = _parse_prediction(pred_raw) if isinstance(pred_raw, dict) else {}
                except Exception:
                    prediction = {}
                return {
                    "external_id": ev["external_id"],
                    "home_team":   ev["home_team"],
                    "away_team":   ev["away_team"],
                    "kickoff":     ev["kickoff"],
                    "status":      ev["status"],
                    "round":       ev.get("round", ""),
                    "prediction":  prediction,
                }

            return await asyncio.gather(*[_fetch_pred(ev) for ev in events])

    async def team_form(self, team_id: int | str, season_id: str | None = None) -> dict:
        if not os.getenv("BSD_FOOTBALL_TOKEN"):
            return _empty_form(team_id)
        async with aiohttp.ClientSession() as session:
            try:
                params: dict = {"status": "finished", "limit": "10"}
                if season_id:
                    params["season_id"] = season_id
                data = await self._get(session, f"teams/{team_id}/fixtures/", params)
                return _parse_form(int(team_id), data.get("results", []))
            except Exception as exc:
                logger.error(f"[FootballSource] team_form team={team_id}: {exc}")
                return _empty_form(team_id)

    async def head_to_head(self, team_a_id: int | str, team_b_id: int | str) -> list[dict]:
        if not os.getenv("BSD_FOOTBALL_TOKEN"):
            return []
        async with aiohttp.ClientSession() as session:
            try:
                data = await self._get(session, "events/", {"team_id": str(team_a_id), "status": "finished", "limit": "50"})
                tb = int(team_b_id)
                h2h_raw = [i for i in data.get("results", []) if i.get("home_team_id") == tb or i.get("away_team_id") == tb]
                return _parse_h2h(h2h_raw[:10])
            except Exception as exc:
                logger.error(f"[FootballSource] head_to_head {team_a_id}-{team_b_id}: {exc}")
                return []

    async def standings(self, league_id: int | str, season_id: str | None = None) -> list[dict]:
        if not os.getenv("BSD_FOOTBALL_TOKEN"):
            return []
        async with aiohttp.ClientSession() as session:
            try:
                params: dict = {}
                if season_id:
                    params["season_id"] = season_id
                data = await self._get(session, f"leagues/{league_id}/standings/", params)
                rows = data if isinstance(data, list) else data.get("standings", data.get("results", []))
                flat: list[dict] = []
                for row in rows:
                    if isinstance(row, list):
                        flat.extend(row)
                    elif isinstance(row, dict) and "standings" in row:
                        flat.extend(row["standings"])
                    else:
                        flat.append(row)
                return [_parse_standing(e) for e in flat]
            except Exception as exc:
                logger.error(f"[FootballSource] standings league={league_id}: {exc}")
                return []

    async def event_odds(self, event_id: int | str) -> dict:
        if not os.getenv("BSD_FOOTBALL_TOKEN"):
            return {}
        async with aiohttp.ClientSession() as session:
            try:
                odds_raw, pred_raw = await asyncio.gather(
                    self._get(session, f"events/{event_id}/odds/", {}),
                    self._get(session, f"events/{event_id}/prediction/", {}),
                    return_exceptions=True,
                )
                result: dict = {}
                if not isinstance(odds_raw, Exception):
                    items = odds_raw if isinstance(odds_raw, list) else odds_raw.get("results", [])
                    result.update(_parse_odds(items))
                if not isinstance(pred_raw, Exception) and isinstance(pred_raw, dict):
                    result["bsd"] = _parse_prediction(pred_raw)
                return result
            except Exception as exc:
                logger.error(f"[FootballSource] event_odds event={event_id}: {exc}")
                return {}


def _parse_form(team_id: int, matches: list[dict]) -> dict:
    last_matches: list[dict] = []
    wins = draws = losses = goals_for = goals_against = 0
    form_chars: list[str] = []
    for m in matches:
        is_home    = m.get("home_team_id") == team_id
        home_goals = m.get("home_score") or 0
        away_goals = m.get("away_score") or 0
        gf = home_goals if is_home else away_goals
        ga = away_goals if is_home else home_goals
        if gf > ga:
            result = "W"; wins += 1; form_chars.append("W")
        elif gf == ga:
            result = "D"; draws += 1; form_chars.append("D")
        else:
            result = "L"; losses += 1; form_chars.append("L")
        goals_for += gf; goals_against += ga
        last_matches.append({"date": (m.get("event_date") or "")[:10], "result": result, "goals_for": gf, "goals_against": ga, "is_home": is_home})
    return {"team_id": team_id, "last_matches": last_matches, "wins": wins, "draws": draws, "losses": losses, "goals_scored": goals_for, "goals_conceded": goals_against, "form_string": "".join(form_chars[-5:])}


def _parse_h2h(matches: list[dict]) -> list[dict]:
    return [{"date": (m.get("event_date") or "")[:10], "home_team": m.get("home_team", ""), "home_score": m.get("home_score"), "away_score": m.get("away_score"), "away_team": m.get("away_team", "")} for m in matches]


def _parse_standing(entry: dict) -> dict:
    return {"rank": entry.get("rank") or entry.get("position"), "team": entry.get("team") or entry.get("team_name", ""), "team_id": entry.get("team_id"), "points": entry.get("points"), "played": entry.get("played") or entry.get("games_played"), "wins": entry.get("won") or entry.get("wins"), "draws": entry.get("drawn") or entry.get("draws"), "losses": entry.get("lost") or entry.get("losses"), "goals_for": entry.get("goals_for") or entry.get("scored"), "goals_against": entry.get("goals_against") or entry.get("conceded"), "goal_diff": entry.get("goal_difference") or entry.get("goal_diff")}


def _parse_odds(items: list[dict]) -> dict:
    best: dict[tuple, float] = {}
    for item in items:
        market = item.get("market", ""); outcome = item.get("outcome", ""); odd = item.get("decimal_odds"); is_max = item.get("is_max_quote", False)
        if odd is None: continue
        key = (market, outcome)
        if key not in best or is_max or float(odd) > best[key]:
            best[key] = float(odd)
    odds: dict = {}
    if ("1x2", "HOME") in best: odds["1"] = best[("1x2", "HOME")]
    if ("1x2", "DRAW") in best: odds["X"] = best[("1x2", "DRAW")]
    if ("1x2", "AWAY") in best: odds["2"] = best[("1x2", "AWAY")]
    if ("over_under_25", "over") in best: odds["over_2_5"] = best[("over_under_25", "over")]
    if ("over_under_15", "over") in best: odds["over_1_5"] = best[("over_under_15", "over")]
    if ("over_under_35", "over") in best: odds["over_3_5"] = best[("over_under_35", "over")]
    if ("btts", "yes") in best: odds["btts"] = best[("btts", "yes")]
    if ("double_chance", "1X") in best: odds["double_chance_1X"] = best[("double_chance", "1X")]
    if ("double_chance", "12") in best: odds["double_chance_12"] = best[("double_chance", "12")]
    if ("double_chance", "X2") in best: odds["double_chance_X2"] = best[("double_chance", "X2")]
    return odds


def _parse_prediction(pred: dict) -> dict:
    out: dict = {}
    markets = pred.get("markets", {}); model = pred.get("model", {}); reco = pred.get("recommendations", {})
    mr = markets.get("match_result", {})
    if mr:
        out["bsd_prob_home"] = mr.get("prob_home"); out["bsd_prob_draw"] = mr.get("prob_draw"); out["bsd_prob_away"] = mr.get("prob_away"); out["bsd_predicted"] = mr.get("predicted")
    xg = markets.get("expected_goals", {})
    if xg:
        out["bsd_xg_home"] = xg.get("home"); out["bsd_xg_away"] = xg.get("away")
    ou = markets.get("over_under", {})
    if ou:
        out["bsd_prob_over_15"] = ou.get("prob_over_15"); out["bsd_prob_over_25"] = ou.get("prob_over_25"); out["bsd_prob_over_35"] = ou.get("prob_over_35")
    btts = markets.get("btts", {})
    if btts:
        out["bsd_prob_btts"] = btts.get("prob_yes")
    out["bsd_confidence"] = model.get("confidence"); out["bsd_model_version"] = model.get("version")
    if reco:
        out["bsd_favorite"] = reco.get("favorite"); out["bsd_favorite_prob"] = reco.get("favorite_prob"); out["bsd_bet_favorite"] = reco.get("bet_favorite"); out["bsd_rec_over_25"] = reco.get("over_25"); out["bsd_rec_btts"] = reco.get("btts")
    return out


def _empty_form(team_id) -> dict:
    return {"team_id": team_id, "last_matches": [], "wins": 0, "draws": 0, "losses": 0, "goals_scored": 0, "goals_conceded": 0, "form_string": ""}
