"""
Fetch the daily PMUB programme PDF from lonab.bf.

The LONAB site lists available programmes at /fr/programme-pmub. Each entry
links to a PDF named like JH_PMUB_DU_DD-MM-YYYY.pdf (with naming variations).
We always pick by parsing the index -- never construct the URL -- because the
filename pattern is inconsistent across days.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup
from loguru import logger

INDEX_BASE = "https://lonab.bf/fr/programme-pmub?page="
BASE_URL = "https://lonab.bf"
MAX_PAGES = 10
PDF_HREF_RE = re.compile(r'\.pdf$', re.IGNORECASE)
DATE_IN_NAME_RE = re.compile(r'(\d{2})[-_](\d{2})[-_](\d{4})')
RACE_TYPE_IN_NAME_RE = re.compile(r'\b(TIERCE|QUARTE|QUINTE|QUINTÉ|4\+1|4_1)\b', re.IGNORECASE)

def _race_type_from_filename(name: str) -> str | None:
    """Extrait le type de course depuis le nom du fichier PDF, None si absent."""
    m = RACE_TYPE_IN_NAME_RE.search(name)
    if not m:
        return None
    raw = m.group(1).upper()
    if raw in ("QUINTE", "QUINTÉ", "4+1", "4_1"):
        return "QUINTE"
    return raw  # TIERCE ou QUARTE


def _parse_links(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    entries: list[dict] = []
    for a in soup.find_all("a", href=PDF_HREF_RE):
        href = a["href"]
        url = href if href.startswith("http") else BASE_URL + href
        name = href.rsplit("/", 1)[-1]
        m = DATE_IN_NAME_RE.search(name)
        d: Optional[date] = None
        if m:
            try:
                d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                d = None
        upper = name.upper()
        if "PMUB" in upper or "JH-PMU" in upper or "JH_PMU" in upper:
            kind = "PMUB"
        elif "ECD" in upper:
            kind = "ECD"
        else:
            kind = "OTHER"
        race_type = _race_type_from_filename(name)
        entries.append({"url": url, "date": d, "kind": kind, "filename": name, "race_type": race_type})
    return entries


async def list_programme_links(
    session: aiohttp.ClientSession,
    target: Optional[date] = None,
) -> list[dict]:
    """Parcourt les pages jusqu'a trouver `target` ou jusqu'a MAX_PAGES."""
    all_entries: list[dict] = []
    for page in range(MAX_PAGES):
        url = INDEX_BASE + str(page)
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            resp.raise_for_status()
            html = await resp.text()
        entries = _parse_links(html)
        if not entries:
            break
        all_entries.extend(entries)
        if target is not None:
            if any(e["date"] == target for e in entries):
                break
            # Si toutes les dates de cette page sont anterieures a target, inutile de continuer
            dated = [e["date"] for e in entries if e["date"] is not None]
            if dated and max(dated) < target and page > 0:
                break
    return all_entries


async def find_pmub_for(session: aiohttp.ClientSession, target: date) -> Optional[dict]:
    entries = await list_programme_links(session, target=target)
    pmub = [e for e in entries if e["kind"] == "PMUB" and e["date"] is not None]
    exact = [e for e in pmub if e["date"] == target]
    if exact:
        return exact[0]
    earlier = sorted([e for e in pmub if e["date"] <= target], key=lambda e: e["date"], reverse=True)
    return earlier[0] if earlier else None


async def download_pdf(session: aiohttp.ClientSession, url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "pdf" not in ctype.lower():
            logger.warning(f"Unexpected content-type for {url}: {ctype}")
        data = await resp.read()
    dest.write_bytes(data)
    logger.info(f"Downloaded {url} -> {dest} ({len(data):,} bytes)")
    return dest


async def fetch_today_pmub_pdf(target: Optional[date] = None, cache_dir: Optional[Path] = None) -> dict:
    target = target or datetime.utcnow().date()
    cache_dir = cache_dir or Path("./_lonab_cache")
    async with aiohttp.ClientSession() as session:
        entry = await find_pmub_for(session, target)
        if not entry:
            raise RuntimeError(f"No PMUB programme found on LONAB for {target} or earlier")
        dest = cache_dir / entry["filename"]
        if not dest.exists():
            await download_pdf(session, entry["url"], dest)
        else:
            logger.info(f"Using cached {dest}")
    return {"url": entry["url"], "date": entry["date"], "path": dest, "filename": entry["filename"], "race_type": entry.get("race_type")}
