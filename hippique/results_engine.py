"""
results_engine.py -- Recupere et extrait les resultats PMUB depuis lonab.bf.

Pas d'acces base de donnees -- retourne un dict pur.
Utilise par /api/results/fetch dans main.py.
Extraction 100% regex -- plus de LLM.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import aiohttp
import pdfplumber
from bs4 import BeautifulSoup
from loguru import logger

INDEX_BASE = "https://lonab.bf/resultats-gains-pmub?page="
BASE_URL   = "https://lonab.bf"
MAX_PAGES  = 10

PDF_HREF_RE  = re.compile(r'\.pdf$', re.IGNORECASE)
DATE_IN_NAME = re.compile(r'(\d{2})[-_](\d{2})[-_](\d{4})')

# Formats observes :
#   ARR : 13 - 12 - 10 - 4 - 2 NPO : 00 NP:00
#   ARRIVEE : 5 - 3 - 1 - 7
_ARRIVAL_RE  = re.compile(
    r'(?:ARR(?:IVEE?)?\s*:?)\s*((?:\d{1,2}\s*[-\u2013]\s*){2,}\d{1,2})',
    re.IGNORECASE,
)
_RACE_TYPE_RE = re.compile(
    r'\b(TIERCE|QUARTE|QUINTE|4\+1|QUINT[EÉ])\b',
    re.IGNORECASE,
)
_DATE_RE = re.compile(r'(\d{2})/(\d{2})/(\d{4})')


def _extract_text(pdf_path: str) -> str:
    pages: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return "\n".join(pages).strip()


def _regex_extract(text: str) -> dict:
    # Arrivee
    m = _ARRIVAL_RE.search(text)
    if not m:
        raise ValueError(
            "Ordre d'arrivee introuvable dans le PDF de resultats. "
            "Format attendu : 'ARR : X - Y - Z ...'"
        )
    arrival_order = [int(n) for n in re.findall(r'\d{1,2}', m.group(1))]

    # Type de course
    race_type = None
    mt = _RACE_TYPE_RE.search(text)
    if mt:
        raw = mt.group(1).upper()
        race_type = 'QUINTE' if raw in ('QUINTE', 'QUINTÉ', '4+1') else raw

    # Date
    extracted_date = None
    md = _DATE_RE.search(text)
    if md:
        try:
            extracted_date = f"{md.group(3)}-{md.group(2)}-{md.group(1)}"
        except Exception:
            pass

    return {
        "arrival_order": arrival_order,
        "race_type":     race_type,
        "date":          extracted_date,
    }


async def _list_result_links(
    session: aiohttp.ClientSession,
    target: Optional[date] = None,
) -> list[dict]:
    all_entries: list[dict] = []
    for page in range(MAX_PAGES):
        url = INDEX_BASE + str(page)
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            resp.raise_for_status()
            html = await resp.text()
        soup = BeautifulSoup(html, "html.parser")
        entries: list[dict] = []
        for a in soup.find_all("a", href=PDF_HREF_RE):
            href = a["href"]
            entry_url = href if href.startswith("http") else BASE_URL + href
            name = href.rsplit("/", 1)[-1]
            m    = DATE_IN_NAME.search(name)
            d: Optional[date] = None
            if m:
                try:
                    d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                except ValueError:
                    d = None
            entries.append({"url": entry_url, "date": d, "filename": name})
        if not entries:
            break
        all_entries.extend(entries)
        if target is not None:
            if any(e["date"] == target for e in entries):
                break
            dated = [e["date"] for e in entries if e["date"] is not None]
            if dated and max(dated) < target and page > 0:
                break
    return all_entries


async def _candidates_for(
    session: aiohttp.ClientSession,
    target: date,
    preferred_race_type: Optional[str] = None,
) -> list[dict]:
    """Retourne tous les PDFs de resultats pour `target`, preferred en premier."""
    entries    = await _list_result_links(session, target=target)
    candidates = [e for e in entries if e["date"] == target]
    if preferred_race_type:
        rtype_upper = preferred_race_type.upper()
        preferred = [c for c in candidates if rtype_upper in c["filename"].upper()]
        others    = [c for c in candidates if rtype_upper not in c["filename"].upper()]
        candidates = preferred + others
    return candidates


async def fetch_results(
    target: Optional[date] = None,
    cache_dir: Optional[Path] = None,
    preferred_race_type: Optional[str] = None,
) -> dict:
    """
    Telecharge le PDF resultats LONAB, extrait l'arrivee par regex.
    Essaie tous les PDFs du jour jusqu'a en trouver un avec une arrivee valide
    (certains jours LONAB publie un PDF de report sans arrivee en plus du vrai).
    Retourne un dict pur -- aucune ecriture en base.
    """
    target    = target    or datetime.utcnow().date()
    cache_dir = cache_dir or Path("./_lonab_cache")

    async with aiohttp.ClientSession() as session:
        candidates = await _candidates_for(session, target, preferred_race_type)
        if not candidates:
            raise FileNotFoundError(
                f"Resultats du {target} pas encore publies sur LONAB."
            )

        last_error: Exception = ValueError("Aucun PDF exploitable.")
        for entry in candidates:
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
                last_error = ValueError(f"{entry['filename']} : PDF image, texte non extractible.")
                logger.warning(str(last_error))
                continue

            try:
                data = _regex_extract(text)
            except ValueError as e:
                last_error = e
                logger.warning(f"{entry['filename']} : {e}")
                continue

            logger.info(f"Arrivee extraite depuis {entry['filename']} : {data['arrival_order']} ({data.get('race_type')}, {data.get('date')})")
            return {
                "arrival_order": data["arrival_order"],
                "race_type":     data.get("race_type"),
                "date":          data.get("date", target.isoformat()),
                "source":        entry["url"],
            }

        raise last_error
