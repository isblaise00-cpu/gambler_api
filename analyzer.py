"""Extraction du programme du jour : l'IA ne produit ni selection ni confiance."""
import json
import os
import re

import pdfplumber
from loguru import logger

_PROMPT = """\
Extrais les donnees du programme PMUB/LONAB ci-dessous. Ne genere aucun pronostic.
Le document contient aussi des resultats d'anciennes courses : ignore ces encadres.
La course a extraire est celle annoncee dans le titre principal du programme.

PROGRAMME :
{text}

Retourne uniquement ce JSON, sans markdown :
{{
  "race": {{
    "type": "QUINTE",
    "race_name": "nom exact",
    "hippodrome": "hippodrome exact",
    "distance": 1600,
    "num_concurrents": 16,
    "date": "YYYY-MM-DD",
    "start_time": "HH:MM"
  }},
  "horses": [
    {{"num": 1, "nom": "NOM COMPLET DU CHEVAL", "non_partant": false}}
  ]
}}

Regles :
- type = TIERCE, QUARTE ou QUINTE. "4+1" signifie QUINTE (5 chevaux).
- Copie tous les numeros et noms COMPLETS du tableau CHEVAUX, non-partants compris.
- num_concurrents = nombre de chevaux inscrits au tableau, non-partants compris.
- non_partant est vrai uniquement si c'est explicitement indique pour CETTE course.
  Les mentions NP/NPO dans les resultats des journees precedentes ne s'appliquent pas.
- date = date du programme principal, jamais celle des resultats anciens.
- start_time = heure de "DEPART DE LA COURSE", pas la cloture des jeux.
- Une metadonnee absente reste null. N'invente aucune donnee ni aucun score.
"""


def _extract_text(pdf_path: str) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages).strip()


def extract_pdf_signals(text: str) -> tuple[dict, list]:
    header = re.search(r"(?im)^.*\bCHEVAUX\b[^\n]*(?:JOCKEYS|DRIVERS|CONDUCTEURS)[^\n]*$", text)
    if not header:
        logger.warning("En-tete du tableau des partants introuvable -- extraction regex ignoree.")
        return {}, []
    section = text[header.end():]
    token = r"(?:\d+(?:[.,]\d+)?\s*/\s*\d+(?:[.,]\d+)?|N\.?/?P\.?O?\.?|[-\u2013\u2014])"
    row = re.compile(rf"^\s*(\d{{1,2}})\s+.+?\s+({token})\s+({token})\s*$", re.I)
    # Partenaires sur la meme ligne : "NOM   15 - 2 - 10 ..."
    partner_row = re.compile(r"^\s*([^\d:\n]{2,70}?)\s+(\d{1,2}(?:\s*[-\u2013\u2014]\s*\d{1,2}){4,})\s*$")
    odds = {}
    partners = []
    seen_sources: set[str] = set()

    for line in section.splitlines():
        match = row.fullmatch(line)
        if match:
            number = int(match[1])
            values = [re.sub(r"\s", "", match[i]) for i in (2, 3)]
            record = {"cote_pt": values[0], "cote_tm": values[1]}
            if number in odds and odds[number] != record:
                raise ValueError(f"Cotes contradictoires pour le cheval {number}.")
            odds[number] = record
        match = partner_row.fullmatch(line)
        if match:
            name = match[1].strip()
            if re.search(r"ARRIV|RESULT|RÉSULT|FAVORI|FORME|CLASSE|PROGR|REGULAR|RÉGULAR|OUTSIDER", name, re.I):
                continue
            if name not in seen_sources:
                seen_sources.add(name)
                partners.append({"source": name, "nums": [int(n) for n in re.findall(r"\d+", match[2])]})

    # --- Extraction de la section APTITUDES (FORME, CLASSE, PROGRES, REGULARITE) ---
    # Format : "FORME : 5 – 10 – 2 – 15 – 8"  (tirets ordinaires ou en-dash)
    aptitude_re = re.compile(
        r"(?im)^(FORME|CLASSE|PROGRES|REGULARITE|FAVORIS)\s*:\s*(\d{1,2}(?:\s*[-\u2013\u2014]\s*\d{1,2}){3,})"
    )
    for m in aptitude_re.finditer(text):
        name = m.group(1).strip().upper()
        if name not in seen_sources:
            seen_sources.add(name)
            partners.append({"source": name, "nums": [int(n) for n in re.findall(r"\d+", m.group(2))]})

    # --- Extraction partenaires presse sur 2 lignes (nom seul puis numeros seuls) ---
    # Cas ou pdfplumber separe le nom et les numeros sur des lignes distinctes
    if len(partners) < 2:
        num_seq_re = re.compile(r"^\s*(\d{1,2}(?:\s*[-\u2013\u2014]\s*\d{1,2}){4,})\s*$")
        lines = text.splitlines()
        for i, line in enumerate(lines):
            m = num_seq_re.fullmatch(line)
            if m and i > 0:
                prev = lines[i - 1].strip()
                # La ligne precedente est un nom si : pas de chiffres, pas trop longue, pas vide
                if prev and not re.search(r"\d", prev) and 2 <= len(prev) <= 60:
                    if re.search(r"ARRIV|RESULT|RÉSULT|OUTSIDER", prev, re.I):
                        continue
                    if prev not in seen_sources:
                        seen_sources.add(prev)
                        partners.append({"source": prev, "nums": [int(n) for n in re.findall(r"\d+", m.group(1))]})

    if len(odds) < 7 or len(partners) < 2:
        logger.warning(
            f"Extraction regex incomplete (cotes={len(odds)}, partenaires={len(partners)}) "
            "-- le LLM et le mode degrade prendront le relai."
        )
    return odds, partners


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    fence = chr(96) * 3
    if fence in raw:
        raw = raw.split(fence)[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


async def _analyze_anthropic(text: str) -> dict:
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    response = await client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
        max_tokens=4096,
        messages=[{"role": "user", "content": _PROMPT.format(text=text)}],
    )
    return _parse_json(response.content[0].text)


async def _analyze_groq(text: str) -> dict:
    from groq import AsyncGroq
    client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
    response = await client.chat.completions.create(
        model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        messages=[{"role": "user", "content": _PROMPT.format(text=text)}],
        max_tokens=4096,
        response_format={"type": "json_object"},
    )
    return _parse_json(response.choices[0].message.content)


def _analyze_mock(text: str, odds: dict) -> dict:
    from datetime import date
    race_type = "QUARTE"
    for rt in ("QUINTE", "QUARTE", "TIERCE"):
        if rt in text.upper():
            race_type = rt
            break
    horses = [{"num": n, "nom": f"CHEVAL_{n}", "non_partant": False} for n in sorted(odds)]
    return {
        "race": {
            "type": race_type,
            "race_name": "MOCK",
            "hippodrome": "MOCK",
            "distance": None,
            "num_concurrents": len(odds),
            "date": date.today().isoformat(),
            "start_time": None,
        },
        "horses": horses,
    }


async def analyze_pdf(pdf_path: str) -> dict:
    text = _extract_text(pdf_path)
    odds, partners = extract_pdf_signals(text)
    provider = os.getenv("AI_PROVIDER", "anthropic").lower()

    data = None
    if provider != "mock":
        try:
            data = await (_analyze_groq(text) if provider == "groq" else _analyze_anthropic(text))
            if {h.get("num") for h in data.get("horses", [])} != set(odds):
                logger.warning("Mismatch LLM/cotes -- repli sur extraction par cotes.")
                data = None
        except Exception as exc:
            logger.warning(f"Extraction LLM echouee ({exc}) -- repli sur extraction par cotes.")
            data = None

    if data is None:
        data = _analyze_mock(text, odds)

    horses = data.get("horses", [])
    for horse in horses:
        if horse["num"] in odds:
            horse.update(odds[horse["num"]])
        if any(re.sub(r'[./]', '', str(horse.get(key, '')).upper()) in ("NP", "NPO")
               for key in ("cote_pt", "cote_tm")):
            horse["non_partant"] = True

    return {"race": data["race"], "horses": horses, "partner_predictions": partners}
