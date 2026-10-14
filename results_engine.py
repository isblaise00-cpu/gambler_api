"""
results_engine.py -- Recupere et extrait les resultats PMUB depuis lonab.bf.

Pas d'acces base de donnees -- retourne un dict pur.
Utilise par /api/results/fetch dans main.py.
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import aiohttp
import pdfplumber
from bs4 import BeautifulSoup
from loguru import logger

INDEX_URL = "https://lonab.bf/resultats-gains-pmub"
BASE_URL  = "https://lonab.bf"

PDF_HREF_RE  = re.compile(r'\.pdf$', re.IGNORECASE)
DATE_IN_NAME = re.compile(r'(\d{2})[-_](\d{2})[-_](\d{4})')

_PROMPT = """\
Tu es un expert en hippisme. Analyse ce document officiel de resultats PMUB/LONAB.

DOCUMENT :
{text}

Reponds UNIQUEMENT avec un JSON valide (sans markdown, sans commentaire) :

{{
  "date": "2026-06-03",
  "race_type": "QUARTE",
  "hippodrome": "COTONOU",
  "arrival_order": [5, 9, 4, 1]
}}

REGLES :
1. JSON valide uniquement -- sans markdown, sans backticks
2. arrival_order = numeros des chevaux dans l'ordre exact d'arrivee (du 1er au dernier place)
3. race_type = TIERCE, QUARTE ou QUINTE selon le document. "4+1" = QUINTE : extraire les CINQ chevaux.
4. date au format YYYY-MM-DD
5. Inclure uniquement les chevaux classes (3 pour TIERCE, 4 pour QUARTE, 5 pour QUINTE)
"""


def _extract_text(pdf_path: str) -> str:
    pages: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return "\n".join(pages).strip()


def _parse_json(raw: str) -> dict:
    import json
    raw = raw.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    return json.loads(raw)


async def _llm_extract(text: str) -> dict:
    provider = os.getenv("AI_PROVIDER", "anthropic").lower()
    if provider == "groq":
        from groq import AsyncGroq
        client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
        response = await client.chat.completions.create(
            model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            messages=[{"role": "user", "content": _PROMPT.format(text=text)}],
            max_tokens=512,
            response_format={"type": "json_object"},
        )
        return _parse_json(response.choices[0].message.content)
    else:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        response = await client.messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            max_tokens=512,
            messages=[{"role": "user", "content": _PROMPT.format(text=text)}],
        )
        return _parse_json(response.content[0].text)


async def _list_result_links(session: aiohttp.ClientSession) -> list[dict]:
    async with session.get(INDEX_URL, timeout=aiohttp.ClientTimeout(total=20)) as resp:
        resp.raise_for_status()
        html = await resp.text()
    soup = BeautifulSoup(html, "html.parser")
    entries: list[dict] = []
    for a in soup.find_all("a", href=PDF_HREF_RE):
        href = a["href"]
        url  = href if href.startswith("http") else BASE_URL + href
        name = href.rsplit("/", 1)[-1]
        m    = DATE_IN_NAME.search(name)
        d: Optional[date] = None
        if m:
            try:
                d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                d = None
        entries.append({"url": url, "date": d, "filename": name})
    return entries


async def _find_result_pdf(
    session: aiohttp.ClientSession,
    target: date,
    preferred_race_type: Optional[str] = None,
) -> Optional[dict]:
    entries    = await _list_result_links(session)
    candidates = [e for e in entries if e["date"] == target]
    if not candidates:
        return None
    if preferred_race_type:
        rtype_upper = preferred_race_type.upper()
        typed = [c for c in candidates if rtype_upper in c["filename"].upper()]
        if typed:
            return typed[0]
    return candidates[0]


async def fetch_results(
    target: Optional[date] = None,
    cache_dir: Optional[Path] = None,
    preferred_race_type: Optional[str] = None,
) -> dict:
    """
    Telecharge le PDF resultats LONAB, extrait l'arrivee via LLM.
    Retourne un dict pur -- aucune ecriture en base.

    Retour :
      {
        "arrival_order": [int, ...],
        "race_type":     str,
        "date":          str (YYYY-MM-DD),
        "source":        str (URL du PDF)
      }
    """
    target    = target    or datetime.utcnow().date()
    cache_dir = cache_dir or Path("./_lonab_cache")

    async with aiohttp.ClientSession() as session:
        entry = await _find_result_pdf(session, target, preferred_race_type)
        if not entry:
            raise RuntimeError(
                f"Aucun resultat PMUB trouve sur LONAB pour le {target}. "
                "Verifiez que les resultats sont publies sur lonab.bf."
            )

        dest = cache_dir / f"RES_{entry['filename']}"
        if not dest.exists():
            async with session.get(entry["url"], timeout=aiohttp.ClientTimeout(total=60)) as resp:
                resp.raise_for_status()
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(await resp.read())
            logger.info(f"Resultats telecharges : {dest}")
        else:
            logger.info(f"Resultats en cache : {dest}")

    text = _extract_text(str(dest))
    if not text.strip():
        raise ValueError(
            "Impossible d'extraire le texte du PDF de resultats. "
            "Le fichier est probablement base sur des images."
        )

    data = await _llm_extract(text)

    arrival_raw = data.get("arrival_order")
    if not isinstance(arrival_raw, list) or len(arrival_raw) == 0:
        raise ValueError("Le LLM n'a pas retourne d'arrivee (arrival_order vide ou absent).")

    data["arrival_order"] = [int(n) for n in arrival_raw]

    logger.info(f"Arrivee extraite : {data['arrival_order']} ({data.get('race_type')}, {data.get('date')})")
    return {
        "arrival_order": data["arrival_order"],
        "race_type":     data.get("race_type"),
        "date":          data.get("date", target.isoformat()),
        "source":        entry["url"],
    }
