"""
Pipeline de generation standalone -- sans DB, sans SMS, sans scheduler.
Entree  : date cible
Sortie  : dict JSON complet (race + horses + proposals + commentary)
"""
from __future__ import annotations

from datetime import date

from loguru import logger

from lonab import fetch_today_pmub_pdf
from analyzer import analyze_pdf
from consensus_model import build_pronostic, build_pronostic_degraded
from race_type import race_type_from_date


async def generate(target: date) -> dict:
    """
    Genere un pronostic complet pour la date donnee.
    Leve une exception si le PDF est introuvable ou les donnees insuffisantes.
    """
    logger.info(f"[Gambler API] Debut generation pour {target}")

    # 1. Telechargement du PDF LONAB
    logger.info("Telechargement du programme LONAB...")
    pdf_info = await fetch_today_pmub_pdf(target=target)

    if pdf_info["date"] != target:
        raise ValueError(
            f"Le PDF disponible ({pdf_info['date']}) ne correspond pas "
            f"a la date demandee ({target}). Programme du jour non disponible."
        )

    # 2. Extraction (LLM + regex cotes + partenaires)
    logger.info("Extraction des donnees du PDF...")
    data = await analyze_pdf(str(pdf_info["path"]))

    # 3. Correction date et type de course (plus fiable que le LLM)
    if "race" in data:
        data["race"]["date"] = target.isoformat()
        data["race"]["type"] = race_type_from_date(target)

    # 4. Calcul du consensus (mode strict en priorite, degrade en fallback)
    logger.info("Calcul du consensus presse/cotes...")
    try:
        data = build_pronostic(data)
        logger.info("Pronostic genere en mode strict.")
    except ValueError as strict_err:
        logger.warning(f"Mode strict echoue ({strict_err}) -- tentative en mode degrade...")
        data = build_pronostic_degraded(data)
        logger.info("Pronostic genere en mode degrade.")

    # Ajouter les metadonnees PDF dans la reponse
    data["_meta"] = {
        "pdf_url":  pdf_info["url"],
        "pdf_date": pdf_info["date"].isoformat(),
        "generated_for": target.isoformat(),
    }

    logger.info(
        f"[Gambler API] Generation terminee -- "
        f"{len(data.get('proposals', []))} combinaisons, "
        f"mode: {data['proposals'][0].get('selection', {}).get('mode', '?') if data.get('proposals') else '?'}"
    )
    return data
