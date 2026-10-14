"""
sports_engine.py -- Pipeline sports complet (fetch + enrich + model + LLM).

Pas d'acces base de donnees -- retourne une liste de dicts enrichis.
Utilise par /api/sports/generate dans main.py.
"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Optional

from loguru import logger

from sports.registry import get_source, get_model
from sports.analyzer import analyze_event

# Nombre max de matchs traites par requete (pour eviter les timeouts)
_MAX_EVENTS = int(__import__("os").getenv("MAX_SPORT_EVENTS", "10"))
# Nombre max de LLM en parallele
_LLM_CONCURRENCY = int(__import__("os").getenv("LLM_CONCURRENCY", "4"))


async def generate_sport(
    sport: str,
    target: date,
    leagues: list[str],
) -> list[dict]:
    """
    Genere les pronostics sportifs pour un sport/date/ligues donnes.
    Retourne une liste de dicts enrichis, sans persistance DB.

    Chaque dict contient :
      event        -- infos du match (home_team, away_team, kickoff, ...)
      home_form    -- forme equipe domicile
      away_form    -- forme equipe exterieur
      h2h          -- historique confrontations directes
      standings    -- classement de la ligue
      odds         -- cotes du marche
      model_probs  -- probabilites statistiques (Dixon-Coles / pace-efficiency)
      value_bets   -- paris a valeur positive detectes
      analysis     -- analyse qualitative LLM (predictions, commentary, confidence)
    """
    sport = sport.upper()
    source = get_source(sport)
    model  = get_model(sport)

    logger.info(f"[SportsEngine] {sport} -- recuperation des matchs du {target}")
    events = await source.list_events(target, leagues)

    if not events:
        logger.info(f"[SportsEngine] {sport} -- aucun match programme.")
        return []

    # Limiter le nombre de matchs pour eviter les timeouts
    if len(events) > _MAX_EVENTS:
        logger.info(f"[SportsEngine] {len(events)} matchs trouves -- limite a {_MAX_EVENTS}.")
        events = events[:_MAX_EVENTS]

    logger.info(f"[SportsEngine] {sport} -- {len(events)} match(s) a enrichir (parallele).")

    # Enrichissement en parallele
    enrich_tasks = [_enrich(source, model, ev) for ev in events]
    enrich_results = await asyncio.gather(*enrich_tasks, return_exceptions=True)

    enriched: list[dict] = []
    for ev, result in zip(events, enrich_results):
        if isinstance(result, Exception):
            logger.error(f"[SportsEngine] Enrich echoue pour {ev.get('external_id')}: {result}")
        else:
            enriched.append(result)
            logger.info(f"[SportsEngine] Enrichi : {ev['home_team']} - {ev['away_team']}")

    logger.info(f"[SportsEngine] {sport} -- analyse LLM de {len(enriched)} matchs (semaphore={_LLM_CONCURRENCY})...")

    # Analyse LLM en parallele avec semaphore pour eviter de surcharger l'API
    semaphore = asyncio.Semaphore(_LLM_CONCURRENCY)

    async def analyze_with_limit(ed: dict) -> dict:
        async with semaphore:
            try:
                ed["analysis"] = await analyze_event(
                    sport, ed, ed["model_probs"], ed["value_bets"],
                )
            except Exception as exc:
                logger.error(f"[SportsEngine] LLM echoue : {exc}")
                ed["analysis"] = None
            return ed

    analyzed = await asyncio.gather(*[analyze_with_limit(ed) for ed in enriched])

    result = [ed for ed in analyzed if ed.get("analysis") is not None]
    logger.info(f"[SportsEngine] {sport} -- {len(result)} pronostic(s) genere(s).")
    return result


async def _enrich(source, model, ev: dict) -> dict:
    """Enrichit un evenement avec forme, H2H, classement, cotes et probabilites."""
    home_id   = ev["home_team_id"]
    away_id   = ev["away_team_id"]
    league_id = ev["league_id"]
    event_id  = ev["external_id"]

    season_id = ev.get("season_id")
    home_form, away_form, h2h, standings, odds = await asyncio.gather(
        source.team_form(home_id, season_id),
        source.team_form(away_id, season_id),
        source.head_to_head(home_id, away_id),
        source.standings(league_id, season_id),
        source.event_odds(event_id),
        return_exceptions=True,
    )

    if isinstance(home_form, Exception): home_form = {}
    if isinstance(away_form, Exception): away_form = {}
    if isinstance(h2h,       Exception): h2h       = []
    if isinstance(standings, Exception): standings = []
    if isinstance(odds,      Exception): odds      = {}

    event_data = {
        "event":     ev,
        "home_form": home_form,
        "away_form": away_form,
        "h2h":       h2h,
        "standings": standings,
        "odds":      odds,
    }

    model_probs = model.compute(event_data)
    value_bets  = model.value_bets(model_probs, odds)

    event_data["model_probs"] = model_probs
    event_data["value_bets"]  = value_bets
    return event_data
