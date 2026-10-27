"""
Extraction du programme du jour : extraction 100% regex + tables pdfplumber.
Plus aucun LLM, plus aucun mock.

Sortie garantie :
  {
    "race":               { type, race_name, hippodrome, distance, num_concurrents, date, start_time },
    "horses":             [ { num, nom, non_partant, perf, gains, driver, trainer, cote_pt, cote_tm } ],
    "partner_predictions":[ { source, nums } ],
    "entraineurs_en_forme": [...],
    "drivers_en_forme":   [...]
  }
"""
from __future__ import annotations

import json
import re

import pdfplumber
from loguru import logger

EN_DASH = '\u2013'

# ── Race meta (page 1 text) ───────────────────────────────────────────────────

_TYPE_RE = re.compile(
    r'"(TIERCE|QUARTE|QUINTE|4\+1|QUINT[EÉ])"\s+DU\s+\w+\s+(\d{1,2})\s+(\w+)\s+(\d{4})',
    re.IGNORECASE,
)
_CONCURRENTS_RE = re.compile(r'(\d+)\s+CONCURRENT', re.IGNORECASE)
_DISTANCE_RE    = re.compile(r'(\d[\s\d]{0,4})\s*M[EÈ]TRES', re.IGNORECASE)
_HIPPODROME_RE  = re.compile(
    r'\b([A-Z][A-Z\s\'\-]{1,30}[A-Z])\s*-\s*((?:QATAR\s+)?(?:PRIX|GRAND PRIX|GRAND NATIONAL|NATIONAL|CRITERIUM|COUPE|CHALLENGE|TROPHEE|COURSE)\b[^\n]{2,120})',
)
_DATE_MONTHS = {
    'JANVIER':1,'FEVRIER':2,'FÉVRIER':2,'MARS':3,'AVRIL':4,'MAI':5,'JUIN':6,
    'JUILLET':7,'AOUT':8,'AOÛT':8,'SEPTEMBRE':9,'OCTOBRE':10,'NOVEMBRE':11,'DECEMBRE':12,'DÉCEMBRE':12,
}
_START_RE = re.compile(r'D[EÉ]PART\s+DE\s+LA\s+COURSE\s*:\s*(\d+h\s*\d+)', re.IGNORECASE)
_NP_RE    = re.compile(r'^N\.?/?P\.?O?\.?$', re.IGNORECASE)


def _is_np(val) -> bool:
    return bool(_NP_RE.match(re.sub(r'\s', '', str(val or '')).upper()))


def _parse_gains(raw: str | None) -> int | None:
    if not raw:
        return None
    digits = re.sub(r'\D', '', str(raw))
    return int(digits) if digits else None


def _find_col(headers: list[str], *candidates: str) -> int | None:
    for c in candidates:
        for i, h in enumerate(headers):
            if h and c.lower() in h.lower():
                return i
    return None


# ── Race meta ─────────────────────────────────────────────────────────────────

def _extract_race_meta(text: str) -> dict:
    meta: dict = {}

    m = _TYPE_RE.search(text)
    if m:
        raw_type = m.group(1).upper()
        meta['type'] = 'QUINTE' if raw_type in ('QUINTE', 'QUINTÉ', '4+1') else raw_type
        day_num    = int(m.group(2))
        month_str  = m.group(3).upper()
        year       = int(m.group(4))
        month      = _DATE_MONTHS.get(month_str)
        if month:
            meta['date'] = f"{year:04d}-{month:02d}-{day_num:02d}"

    for line in text.splitlines():
        m2 = _HIPPODROME_RE.search(line)
        if m2:
            meta['hippodrome'] = m2.group(1).strip()
            meta['race_name']  = m2.group(2).strip().rstrip(' -')
            break

    m3 = _CONCURRENTS_RE.search(text)
    if m3:
        meta['num_concurrents'] = int(m3.group(1))

    for m4 in _DISTANCE_RE.finditer(text):
        val = int(re.sub(r'\s', '', m4.group(1)))
        if 500 <= val <= 10000:
            meta['distance'] = val
            break

    return meta


def _extract_start_time(text_p2: str) -> str | None:
    m = _START_RE.search(text_p2)
    return m.group(1).replace(' ', '') if m else None


# ── En forme ─────────────────────────────────────────────────────────────────

def _extract_en_forme(text: str) -> tuple[list, list]:
    lines       = text.splitlines()
    entraineurs: list[str] = []
    jockeys:     list[str] = []

    for i, line in enumerate(lines):
        # Entraineurs
        if re.search(r'ENTRAINEURS?\s+EN\s+FORME', line, re.IGNORECASE):
            for j in range(i + 1, min(i + 5, len(lines))):
                cand = lines[j].strip()
                if re.search(r'FORME|CLASSE|PROGRES|REGULARITE|FAVORIS', cand, re.IGNORECASE):
                    continue
                if EN_DASH in cand and re.match(r'[A-Z]', cand):
                    entraineurs = [n.strip() for n in cand.split(EN_DASH) if n.strip()]
                    break

        # Jockeys / Drivers
        if re.search(r'(?:JOCKEYS?|DRIVERS?|CONDUCTEURS?)\s+EN\s+FORME', line, re.IGNORECASE):
            search_lines = [line] + lines[i+1:i+4]
            for sl in search_lines:
                m = re.search(
                    r'PROG(?:RES|R[EÈ]S)\s*:\s*[\d\s\-\u2013]+\s+([A-Z].+)',
                    sl, re.IGNORECASE
                )
                if m:
                    jockeys = [n.strip() for n in m.group(1).split(EN_DASH) if n.strip()]
                    break
            if not jockeys:
                for j in range(i + 1, min(i + 5, len(lines))):
                    cand = lines[j].strip()
                    if cand and EN_DASH in cand and re.match(r'[A-Z]', cand):
                        jockeys = [n.strip() for n in cand.split(EN_DASH) if n.strip()]
                        break

    return entraineurs, jockeys


# ── Horse table ───────────────────────────────────────────────────────────────
#
# Format pdfplumber observe :
#   - 1 ligne d'en-tete (N°, CHEVAUX, JOCKEYS/DRIVERS, ENTRAINEURS, PERF., GAINS, ...)
#   - 1 ou 2 lignes de donnees ou chaque cellule contient TOUTES les valeurs separees par \n
#     (ex. galop=1 ligne, trot=2 lignes pour 10+5 chevaux)

def _extract_horses_from_table(all_tables: list) -> list:
    horses:    list[dict] = []
    seen_nums: set[int]   = set()

    for table in all_tables:
        if not table or len(table) < 2:
            continue

        # Chercher la ligne d'en-tete
        hdr_idx = None
        for idx, row in enumerate(table):
            cells = [str(c or '').upper() for c in row]
            if any('CHEVAUX' in c for c in cells):
                hdr_idx = idx
                break
        if hdr_idx is None:
            continue

        headers    = [str(c or '').upper().strip() for c in table[hdr_idx]]
        col_num    = _find_col(headers, 'n°', 'num', '#')
        col_horse  = _find_col(headers, 'chevaux')
        col_driver = _find_col(headers, 'drivers', 'jockeys', 'conducteurs', 'jockey')
        col_train  = _find_col(headers, 'entraineur')
        col_perf   = _find_col(headers, 'perf')
        col_gains  = _find_col(headers, 'gains')

        if col_horse is None:
            continue

        for row in table[hdr_idx + 1:]:
            if not row:
                continue

            def split_cell(idx) -> list[str]:
                if idx is None or idx >= len(row):
                    return []
                return [v.strip() for v in str(row[idx] or '').split('\n') if v.strip()]

            nums_vals   = split_cell(col_num)
            horse_vals  = split_cell(col_horse)
            driver_vals = split_cell(col_driver)
            train_vals  = split_cell(col_train)
            perf_vals   = split_cell(col_perf)
            gains_vals  = split_cell(col_gains)

            n = len(horse_vals)
            if n == 0:
                continue

            for k in range(n):
                def get(lst: list[str]) -> str | None:
                    return lst[k] if k < len(lst) else None

                num_raw = get(nums_vals)
                if num_raw is None:
                    # Numero parfois colle au nom
                    m = re.match(r'^(\d{1,2})\s*', get(horse_vals) or '')
                    num_raw = m.group(1) if m else None
                try:
                    num = int(re.sub(r'\D', '', str(num_raw or '')))
                except (ValueError, TypeError):
                    continue
                if num == 0 or num > 30 or num in seen_nums:
                    continue
                seen_nums.add(num)

                nom_raw  = get(horse_vals) or ''
                nom      = re.sub(r'^\d+\s*', '', nom_raw).strip()
                perf_raw = get(perf_vals)
                perf     = perf_raw if perf_raw and perf_raw not in ('', '-', EN_DASH) else None
                gains    = _parse_gains(get(gains_vals))
                driver   = (get(driver_vals) or '').strip() or None
                trainer  = (get(train_vals)  or '').strip() or None
                np_flag  = _is_np(perf_raw) or _is_np(driver) or _is_np(trainer)

                horses.append({
                    'num':         num,
                    'nom':         nom or f'CHEVAL_{num}',
                    'non_partant': np_flag,
                    'perf':        perf,
                    'gains':       gains,
                    'driver':      None if _is_np(driver)  else driver,
                    'trainer':     None if _is_np(trainer) else trainer,
                })

    horses.sort(key=lambda h: h['num'])
    return horses


# ── Cotes + partenaires presse ────────────────────────────────────────────────

def extract_pdf_signals(text: str) -> tuple[dict, list]:
    """Extrait les cotes et les selections des partenaires presse (texte brut)."""
    header = re.search(r"(?im)^.*\bCHEVAUX\b[^\n]*(?:JOCKEYS|DRIVERS|CONDUCTEURS)[^\n]*$", text)
    if not header:
        logger.warning("En-tete du tableau des partants introuvable -- extraction regex ignoree.")
        return {}, []
    section = text[header.end():]
    token = r"(?:\d+(?:[.,]\d+)?\s*/\s*\d+(?:[.,]\d+)?|N\.?/?P\.?O?\.?|[-\u2013\u2014])"
    row_re      = re.compile(rf"^\s*(\d{{1,2}})\s+.+?\s+({token})\s+({token})\s*$", re.I)
    partner_row = re.compile(r"^\s*([^\d:\n]{2,70}?)\s+(\d{1,2}(?:\s*[-\u2013\u2014]\s*\d{1,2}){4,})\s*$")
    odds:         dict      = {}
    partners:     list      = []
    seen_sources: set[str]  = set()

    for line in section.splitlines():
        match = row_re.fullmatch(line)
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

    aptitude_re = re.compile(
        r"(?im)^(FORME|CLASSE|PROGRES|REGULARITE|FAVORIS)\s*:\s*(\d{1,2}(?:\s*[-\u2013\u2014]\s*\d{1,2}){3,})"
    )
    for m in aptitude_re.finditer(text):
        name = m.group(1).strip().upper()
        if name not in seen_sources:
            seen_sources.add(name)
            partners.append({"source": name, "nums": [int(n) for n in re.findall(r"\d+", m.group(2))]})

    if len(partners) < 2:
        num_seq_re = re.compile(r"^\s*(\d{1,2}(?:\s*[-\u2013\u2014]\s*\d{1,2}){4,})\s*$")
        lines = text.splitlines()
        for i, line in enumerate(lines):
            m = num_seq_re.fullmatch(line)
            if m and i > 0:
                prev = lines[i - 1].strip()
                if prev and not re.search(r"\d", prev) and 2 <= len(prev) <= 60:
                    if re.search(r"ARRIV|RESULT|RÉSULT|OUTSIDER", prev, re.I):
                        continue
                    if prev not in seen_sources:
                        seen_sources.add(prev)
                        partners.append({"source": prev, "nums": [int(n) for n in re.findall(r"\d+", m.group(1))]})

    if len(odds) < 7 or len(partners) < 2:
        logger.warning(
            f"Extraction regex incomplete (cotes={len(odds)}, partenaires={len(partners)}) "
            "-- le mode degrade prendra le relai."
        )
    return odds, partners


# ── Point d'entree principal ──────────────────────────────────────────────────

def _extract_text(pdf_path: str) -> tuple[str, str, list]:
    """Retourne (texte_p1, texte_p2, tables_p2)."""
    with pdfplumber.open(pdf_path) as pdf:
        text_p1   = pdf.pages[0].extract_text() or ''
        text_p2   = pdf.pages[1].extract_text() or '' if len(pdf.pages) > 1 else ''
        tables_p2 = pdf.pages[1].extract_tables()  if len(pdf.pages) > 1 else []
    return text_p1, text_p2, tables_p2


async def analyze_pdf(pdf_path: str) -> dict:
    """
    Extrait toutes les donnees du PDF du programme hippique.
    Aucun LLM, aucun mock : extraction 100% locale (regex + tables pdfplumber).
    """
    text_p1, text_p2, tables_p2 = _extract_text(pdf_path)
    text_full = text_p1 + '\n' + text_p2

    race_meta           = _extract_race_meta(text_p1)
    start_time          = _extract_start_time(text_p2)
    entraineurs, jockeys = _extract_en_forme(text_p1)
    horses              = _extract_horses_from_table(tables_p2)
    odds, partners      = extract_pdf_signals(text_full)

    if not horses:
        raise RuntimeError(
            f"Extraction impossible : aucun cheval trouve dans {pdf_path}. "
            "Verifiez que le PDF contient bien un tableau de partants page 2."
        )

    if not race_meta.get('type') or not race_meta.get('hippodrome'):
        logger.warning(
            f"Metadonnees de course incompletes : type={race_meta.get('type')!r}, "
            f"hippodrome={race_meta.get('hippodrome')!r}"
        )

    # Enrichir les chevaux avec les cotes regex + detection NP via cotes
    horse_nums = {h['num'] for h in horses}
    if horse_nums != set(odds):
        logger.warning(
            f"Mismatch chevaux/cotes : chevaux={sorted(horse_nums)}, cotes={sorted(odds)}. "
            "Les cotes sont appliquees sur les chevaux connus."
        )

    for horse in horses:
        if horse['num'] in odds:
            horse.update(odds[horse['num']])
        for key in ('cote_pt', 'cote_tm'):
            if _is_np(str(horse.get(key, ''))):
                horse['non_partant'] = True

    return {
        'race': {
            'type':            race_meta.get('type'),
            'race_name':       race_meta.get('race_name'),
            'hippodrome':      race_meta.get('hippodrome'),
            'distance':        race_meta.get('distance'),
            'num_concurrents': race_meta.get('num_concurrents', len(horses)),
            'date':            race_meta.get('date'),
            'start_time':      start_time,
        },
        'horses':               horses,
        'partner_predictions':  partners,
        'entraineurs_en_forme': entraineurs,
        'drivers_en_forme':     jockeys,
    }
