"""
Gambler API -- Service externe de generation de pronostics.

Endpoints :
  GET  /health                -- sante du service
  POST /api/generate          -- pronostic hippique LONAB
  POST /api/results/fetch     -- resultats hippiques LONAB
  POST /api/sports/generate   -- pronostics football / basketball

Securise par X-API-Key dans le header de chaque requete.
"""
from __future__ import annotations

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

from pipeline import generate
from results_engine import fetch_results
from sports_engine import generate_sport

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
    if request.url.path == "/health":
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
    "FOOTBALL":   ["39", "140", "135", "78", "61", "2"],
    "BASKETBALL": ["12", "120"],
}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "gambler-api"}


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
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception(f"[/api/results/fetch] Erreur inattendue : {e}")
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
        results = await generate_sport(sport, target, leagues)
        return {"sport": sport, "date": target.isoformat(), "total": len(results), "events": results}
    except Exception as e:
        logger.exception(f"[/api/sports/generate] Erreur inattendue : {e}")
        raise HTTPException(status_code=500, detail=f"[DEBUG] {type(e).__name__}: {e}")


# ── Demarrage direct ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
