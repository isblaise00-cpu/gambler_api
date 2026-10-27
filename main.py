"""
Gambler API -- Service externe de generation de pronostics.

Endpoints :
  GET  /health                    -- sante du service
  POST /api/generate              -- pronostic hippique LONAB
  POST /api/results/fetch         -- resultats hippiques LONAB
  GET  /api/football/leagues      -- championnats avec matchs du jour
  GET  /api/football/matches      -- matchs d'un championnat (leger)
  POST /api/sports/generate       -- pronostics football / basketball (complet)

Securise par X-API-Key dans le header de chaque requete.
"""
from __future__ import annotations

import asyncio
import os
from datetime import date as DateType
from typing import Optional, List

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from hippique.pipeline import generate
from hippique.results_engine import fetch_results
from sports_lite import generate_sport_lite
from sports.sources.football_source import FootballSource
from ui import UI_HTML

# ── Configuration ─────────────────────────────────────────────────────────────

API_KEY = os.getenv("API_KEY", "")
if not API_KEY:
    logger.warning("[Gambler API] API_KEY non definie -- toutes les requetes seront refusees !")

PORT = int(os.getenv("PORT", 9000))

# ── Application ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Gambler API",
    description="Service de generation de pronostics hippiques et sportifs",
    version="2.0.0",
    docs_url="/docs",
    redoc_url=None,
)


# ── Middleware : verification API key ─────────────────────────────────────────

@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    if request.url.path in ("/health", "/ui"):
        return await call_next(request)
    key = request.headers.get("x-api-key", "")
    if not API_KEY or key != API_KEY:
        logger.warning(f"[Gambler API] Acces refuse -- cle invalide depuis {request.client.host}")
        return JSONResponse(status_code=401, content={"detail": "API key invalide ou manquante."})
    return await call_next(request)


# ── Schemas ───────────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    date: Optional[str] = None


class ResultsFetchRequest(BaseModel):
    date: Optional[str] = None
    race_type: Optional[str] = None  # hint optionnel pour le choix du PDF


class SportGenerateRequest(BaseModel):
    sport: str                        # "FOOTBALL" | "BASKETBALL"
    date: Optional[str] = None
    leagues: Optional[List[str]] = None  # IDs de ligues; defaut selon le sport


# ── Defaults ligues ───────────────────────────────────────────────────────────

_DEFAULT_LEAGUES = {
    # IDs BSD API (sports.bzzoiro.com) : 1=PL, 3=LaLiga, 4=SerieA, 5=Bundesliga, 6=Ligue1
    "FOOTBALL":   ["1", "3", "4", "5", "6"],
    "BASKETBALL": ["12", "120"],
}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "gambler-api"}


@app.get("/ui", include_in_schema=False)
async def ui_page():
    from fastapi.responses import HTMLResponse
    html = UI_HTML.replace("%%API_KEY%%", API_KEY)
    return HTMLResponse(content=html)


@app.post("/api/generate")
async def generate_pronostic(body: GenerateRequest = GenerateRequest()):
    """Genere le pronostic hippique LONAB pour la date demandee."""
    if body.date:
        try:
            target = DateType.fromisoformat(body.date)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Format de date invalide : {body.date!r}. Attendu : YYYY-MM-DD")
    else:
        target = DateType.today()

    logger.info(f"[/api/generate] Requete pour {target}")
    try:
        result = await generate(target)
        return result
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception(f"[/api/generate] Erreur inattendue : {e}")
        raise HTTPException(status_code=500, detail=f"[DEBUG] {type(e).__name__}: {e}")


@app.post("/api/results/fetch")
async def fetch_results_endpoint(body: ResultsFetchRequest = ResultsFetchRequest()):
    """
    Recupere les resultats hippiques LONAB pour la date demandee.

    Retourne :
      { arrival_order, race_type, date, source }
    """
    if body.date:
        try:
            target = DateType.fromisoformat(body.date)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Format de date invalide : {body.date!r}. Attendu : YYYY-MM-DD")
    else:
        target = DateType.today()

    logger.info(f"[/api/results/fetch] Requete pour {target}")
    try:
        result = await fetch_results(target=target, preferred_race_type=body.race_type)
        return result
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception(f"[/api/results/fetch] Erreur inattendue : {e}")
        raise HTTPException(status_code=500, detail=f"[DEBUG] {type(e).__name__}: {e}")


@app.get("/api/football/leagues")
async def football_leagues(date: Optional[str] = None):
    """
    Retourne la liste des championnats qui ont des matchs a la date donnee.

    Retourne :
      { date, leagues: [ { league_id, league_name, match_count } ] }
    """
    if date:
        try:
            target = DateType.fromisoformat(date)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Format de date invalide : {date!r}. Attendu : YYYY-MM-DD")
    else:
        target = DateType.today()

    logger.info(f"[/api/football/leagues] Scan de tous les championnats pour {target}")
    try:
        source = FootballSource()
        leagues = await asyncio.wait_for(
            source.list_all_leagues_with_events(target),
            timeout=30,
        )
        return {"date": target.isoformat(), "total": len(leagues), "leagues": leagues}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Timeout lors de la recuperation des championnats.")
    except Exception as e:
        logger.exception(f"[/api/football/leagues] Erreur : {e}")
        raise HTTPException(status_code=500, detail=f"[DEBUG] {type(e).__name__}: {e}")


@app.get("/api/football/matches")
async def football_matches(league_id: str, date: Optional[str] = None):
    """
    Retourne les matchs d'un championnat pour la date donnee (sans enrichissement ni LLM).

    Retourne :
      { date, league_id, matches: [ { home_team, away_team, kickoff, status, round } ] }
    """
    if date:
        try:
            target = DateType.fromisoformat(date)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Format de date invalide : {date!r}. Attendu : YYYY-MM-DD")
    else:
        target = DateType.today()

    logger.info(f"[/api/football/matches] Ligue {league_id} pour {target}")
    try:
        source = FootballSource()
        matches = await asyncio.wait_for(
            source.list_events_with_predictions(target, league_id),
            timeout=60,
        )
        return {"date": target.isoformat(), "league_id": league_id, "total": len(matches), "matches": matches}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Timeout lors de la recuperation des matchs.")
    except Exception as e:
        logger.exception(f"[/api/football/matches] Erreur : {e}")
        raise HTTPException(status_code=500, detail=f"[DEBUG] {type(e).__name__}: {e}")


@app.post("/api/sports/generate")
async def generate_sport_endpoint(body: SportGenerateRequest):
    """
    Genere les pronostics sportifs (football ou basketball) pour la date demandee.

    Retourne une liste de matchs enrichis :
      [ { event, home_form, away_form, h2h, standings, odds, model_probs, value_bets, analysis }, ... ]
    """
    sport = body.sport.upper()
    known = list(_DEFAULT_LEAGUES.keys())
    if sport not in known:
        raise HTTPException(status_code=400, detail=f"Sport inconnu : {sport!r}. Disponibles : {known}")

    if body.date:
        try:
            target = DateType.fromisoformat(body.date)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Format de date invalide : {body.date!r}. Attendu : YYYY-MM-DD")
    else:
        target = DateType.today()

    leagues = body.leagues or _DEFAULT_LEAGUES[sport]

    logger.info(f"[/api/sports/generate] {sport} pour {target}, ligues={leagues}")
    try:
        results = await asyncio.wait_for(
            generate_sport_lite(sport, target, leagues),
            timeout=60,
        )
        return {"sport": sport, "date": target.isoformat(), "total": len(results), "events": results}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Timeout : reessayez ou reduisez le nombre de ligues.")
    except Exception as e:
        logger.exception(f"[/api/sports/generate] Erreur inattendue : {e}")
        raise HTTPException(status_code=500, detail=f"[DEBUG] {type(e).__name__}: {e}")


# ── Demarrage direct ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
