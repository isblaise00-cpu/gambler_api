"""
Gambler API -- Service externe de generation de pronostics hippiques LONAB.

Endpoint principal : POST /api/generate
Sante             : GET  /health

Securise par X-API-Key dans le header de chaque requete.
"""
from __future__ import annotations

import os
from datetime import date as DateType
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel

# Charge le .env si present (dev local)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from pipeline import generate

# ── Configuration ─────────────────────────────────────────────────────────────

API_KEY = os.getenv("API_KEY", "")
if not API_KEY:
    logger.warning("[Gambler API] API_KEY non definie -- toutes les requetes seront refusees !")

PORT = int(os.getenv("PORT", 9000))

# ── Application ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="Gambler API",
    description="Service de generation de pronostics hippiques LONAB/PMUB",
    version="1.0.0",
    docs_url="/docs",       # desactiver en prod si souhaite : docs_url=None
    redoc_url=None,
)


# ── Middleware : verification API key ─────────────────────────────────────────

@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    # L'endpoint /health est public
    if request.url.path == "/health":
        return await call_next(request)

    key = request.headers.get("x-api-key", "")
    if not API_KEY or key != API_KEY:
        logger.warning(f"[Gambler API] Acces refuse -- cle invalide depuis {request.client.host}")
        return JSONResponse(status_code=401, content={"detail": "API key invalide ou manquante."})

    return await call_next(request)


# ── Schemas ───────────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    date: Optional[str] = None  # format YYYY-MM-DD, defaut = aujourd'hui


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Verification que le service est actif."""
    return {"status": "ok", "service": "gambler-api"}


@app.post("/api/generate")
async def generate_pronostic(body: GenerateRequest = GenerateRequest()):
    """
    Genere le pronostic hippique LONAB pour la date demandee.

    - Telecharge le PDF du programme du jour depuis lonab.bf
    - Extrait les partants, cotes et pronostics partenaires
    - Calcule le classement consensus (presse 70% + cotes 30%)
    - Retourne 20 combinaisons + le classement des 7 meilleurs candidats

    Header requis : X-API-Key
    """
    # Parsing de la date
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
        logger.error(f"[/api/generate] Erreur metier : {e}")
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        logger.error(f"[/api/generate] PDF introuvable : {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception(f"[/api/generate] Erreur inattendue : {e}")
        raise HTTPException(status_code=500, detail="Erreur interne du service de generation.")


# ── Demarrage direct ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
